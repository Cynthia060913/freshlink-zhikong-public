"""PR #10 boundary regressions (four defects A reproduced on the
migrated ``58546e9`` snapshot, recorded in the new repo #8 body).

Each class pins one defect to a failing-then-green behaviour:

1. **ISO-8601 JSON time rejected** — the ``mode="before"`` validator
   only accepted ``datetime`` objects, so HTTP JSON string timestamps
   (``"...Z"`` / ``"+08:00"``) were rejected with 422 before pydantic
   could parse them. F03-RC2 requires BOTH ISO strings and internal
   datetimes to work; naive values are never guessed.
2. **F03 traceability fields silently dropped, digest unchanged** —
   ``CandidateCreate`` ignored ``observation_ids`` / ``rule_*`` /
   ``evidence`` inputs, so replaying the same ``source_event_id``
   with different traceability answered ``accepted_duplicate``
   instead of ``conflict``. F03-RC2: the canonical digest must cover
   the traceability fields and the stored body must return them.
3. **Read-phase OperationalError escapes** — only the INSERT phase
   mapped storage failures to ``retryable_failure``; the batch /
   warehouse pre-read, the idempotency lookup and the post-integrity
   re-read all leaked ``OperationalError`` as raw 500s. F03-RC2:
   前置读取、幂等查找、写提交、并发冲突后读取和提交后读取均要
   考虑数据库故障。
4. **S1→S2→S3 third open version misclassified as overlap** — the
   write-side overlap check used stored intervals, so a superseded
   open-ended S1 (whose read-side window already closed at S2's
   start) blocked the S3 replacement. F02: the write check must use
   the same successor-derived half-open windows the reader uses.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

from fastapi import HTTPException, Response
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.modules.alerts.models import (
    BatchCreate,
    BatchRuleSnapshotCreate,
    CandidateCreate,
    CandidateKind,
    DeviceEventCreate,
    DeviceEventKind,
    RuleDict,
    RuleSeverity,
    WarehouseCreate,
    compute_content_digest,
)
from app.modules.alerts.routes.events import read_candidate_by_source
from app.modules.alerts.services.batches import create_batch
from app.modules.alerts.services.events import (
    get_event_by_source,
    receive_candidate,
    receive_device_event,
)
from app.modules.alerts.services.rules import (
    SnapshotOverlapConflict,
    create_snapshot,
    select_rules_for_observation,
)
from app.modules.alerts.services.warehouses import create_warehouse
from tests.modules.alerts.test_alerts_routes import _client, _IsolatedCase

T0 = datetime(2026, 10, 13, 8, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=1)
T2 = T0 + timedelta(days=2)

_OBS_A = "00000000-0000-4000-8000-000000000004"
_OBS_B = "00000000-0000-4000-8000-000000000005"


def _rc2(**over: Any) -> CandidateCreate:
    """The F03-RC2 example shape (contracts/examples/candidate-f03-rc2.json),
    built directly from literals so these tests do not depend on the
    shared fixtures evolving."""

    base: dict[str, Any] = {
        "schema_version": "1.0",
        "source_event_id": "77777777-7777-4777-8777-777777777777",
        "warehouse_id": uuid.UUID("22222222-2222-4222-8222-222222222222"),
        "batch_id": uuid.UUID("55555555-5555-4555-8555-555555555555"),
        "observation_ids": [_OBS_A],
        "metric_code": "temperature",
        "sampled_at": "2026-10-14T00:01:00Z",
        "value": 11.0,
        "unit": "°C",
        "kind": CandidateKind.TEMPERATURE_HIGH,
        "severity": "warning",
        "rule_snapshot_id": uuid.UUID("88888888-8888-4888-8888-888888888888"),
        "rule_version": "v1",
        "algorithm_version": "threshold-rule/0.2.0-rc2",
        "evidence": {"comparison": ">", "threshold": 8.0, "quality": ["ok"]},
    }
    base.update(over)
    return CandidateCreate(**base)


def _engine() -> Any:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


class _World(unittest.TestCase):
    """One warehouse + one batch on a fresh engine."""

    wid: uuid.UUID
    bid: uuid.UUID

    def setUp(self) -> None:
        self.eng = _engine()
        with Session(self.eng) as session:
            self.wid = create_warehouse(
                session, WarehouseCreate(code="WH", name="W")
            ).id
            self.bid = create_batch(
                session, self.wid, BatchCreate(code="B1", product="x")
            ).id

    def _world_candidate(self, **over: Any) -> CandidateCreate:
        over.setdefault("warehouse_id", self.wid)
        over.setdefault("batch_id", self.bid)
        return _rc2(**over)


# ---------------------------------------------------------------------------
# Defect 1 — ISO-8601 JSON timestamps
# ---------------------------------------------------------------------------


class IsoJsonTimestampCase(unittest.TestCase):
    def test_z_suffix_string_parsed_to_utc(self) -> None:
        payload = _rc2()
        self.assertEqual(payload.sampled_at, datetime(2026, 10, 14, 0, 1, tzinfo=UTC))

    def test_offset_string_normalized_to_utc(self) -> None:
        payload = _rc2(sampled_at="2026-10-14T08:01:00+08:00")
        self.assertEqual(payload.sampled_at, datetime(2026, 10, 14, 0, 1, tzinfo=UTC))

    def test_naive_string_rejected_not_guessed(self) -> None:
        with self.assertRaises(ValueError):
            _rc2(sampled_at="2026-10-14T00:01:00")

    def test_garbage_string_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _rc2(sampled_at="not-a-timestamp")

    def test_internal_datetime_still_accepted(self) -> None:
        payload = _rc2(sampled_at=datetime(2026, 10, 14, 0, 1, tzinfo=UTC))
        self.assertEqual(payload.sampled_at, datetime(2026, 10, 14, 0, 1, tzinfo=UTC))

    def test_device_event_iso_string_accepted(self) -> None:
        payload = DeviceEventCreate(
            source_event_id="dev-evt-1",
            kind=DeviceEventKind.OFFLINE,
            warehouse_id=uuid.uuid4(),
            device_id="device-01",
            sampled_at="2026-10-13T08:00:00Z",
        )
        self.assertEqual(payload.sampled_at, T0)

    def test_device_event_naive_string_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DeviceEventCreate(
                source_event_id="dev-evt-2",
                kind=DeviceEventKind.OFFLINE,
                warehouse_id=uuid.uuid4(),
                device_id="device-01",
                sampled_at="2026-10-13T08:00:00",
            )


class HttpIsoJsonCandidateCase(_IsolatedCase):
    """POST /candidates with the RC2 JSON body — ISO strings must reach
    the service through the real FastAPI parsing path."""

    def _ids(self, client: Any) -> dict[str, str]:
        wid = client.post("/warehouses/", json={"code": "WH", "name": "W"}).json()["id"]
        bid = client.post(
            f"/warehouses/{wid}/batches", json={"code": "B-001", "product": "x"}
        ).json()["id"]
        return {"wid": wid, "bid": bid}

    def _body(self, ids: dict[str, str], **over: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "schema_version": "1.0",
            "source_event_id": "77777777-7777-4777-8777-777777777777",
            "warehouse_id": ids["wid"],
            "batch_id": ids["bid"],
            "observation_ids": [_OBS_A],
            "metric_code": "temperature",
            "sampled_at": "2026-10-14T00:01:00Z",
            "value": 11.0,
            "unit": "°C",
            "kind": "temperature_high",
            "severity": "warning",
            "rule_snapshot_id": "88888888-8888-4888-8888-888888888888",
            "rule_version": "v1",
            "algorithm_version": "threshold-rule/0.2.0-rc2",
            "evidence": {"comparison": ">", "threshold": 8.0, "quality": ["ok"]},
        }
        base.update(over)
        return base

    def test_iso_json_candidate_accepted(self) -> None:
        client = _client()
        ids = self._ids(client)
        r = client.post("/candidates", json=self._body(ids))
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["status"], "accepted_new")

    def test_naive_iso_json_rejected_422(self) -> None:
        client = _client()
        ids = self._ids(client)
        r = client.post(
            "/candidates", json=self._body(ids, sampled_at="2026-10-14T00:01:00")
        )
        self.assertEqual(r.status_code, 422)

    def test_conflicting_traceability_replay_is_409(self) -> None:
        client = _client()
        ids = self._ids(client)
        first = client.post("/candidates", json=self._body(ids))
        self.assertEqual(first.status_code, 201, first.text)
        replay = client.post(
            "/candidates", json=self._body(ids, observation_ids=[_OBS_B])
        )
        self.assertEqual(replay.status_code, 409, replay.text)
        self.assertEqual(replay.json()["status"], "conflict")

    def test_same_content_replay_still_duplicate(self) -> None:
        client = _client()
        ids = self._ids(client)
        first = client.post("/candidates", json=self._body(ids))
        replay = client.post("/candidates", json=self._body(ids))
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "accepted_duplicate")
        self.assertEqual(replay.json()["event_id"], first.json()["event_id"])

    def test_by_source_returns_full_traceability(self) -> None:
        client = _client()
        ids = self._ids(client)
        client.post("/candidates", json=self._body(ids))
        r = client.get("/candidates/by-source/77777777-7777-4777-8777-777777777777")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["observation_ids"], [_OBS_A])
        self.assertEqual(body["severity"], "warning")
        self.assertEqual(
            body["rule_snapshot_id"], "88888888-8888-4888-8888-888888888888"
        )
        self.assertEqual(body["rule_version"], "v1")
        self.assertEqual(body["algorithm_version"], "threshold-rule/0.2.0-rc2")
        self.assertEqual(body["evidence"]["threshold"], 8.0)

    def test_batch_from_other_warehouse_is_invalid(self) -> None:
        client = _client()
        ids = self._ids(client)
        other_wid = uuid.uuid4()
        r = client.post(
            "/candidates", json=self._body(ids, warehouse_id=str(other_wid))
        )
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["status"], "invalid")
        self.assertEqual(r.json()["reason_code"], "batch_warehouse_mismatch")


# ---------------------------------------------------------------------------
# Defect 2 — traceability fields in the digest and the stored body
# ---------------------------------------------------------------------------


class TraceabilityDigestCase(unittest.TestCase):
    def test_rc2_example_constructs(self) -> None:
        payload = _rc2()
        self.assertEqual(payload.severity, "warning")
        self.assertEqual(payload.observation_ids, [_OBS_A])
        self.assertEqual(payload.evidence, {"comparison": ">", "threshold": 8.0, "quality": ["ok"]})

    def test_digest_covers_observation_ids(self) -> None:
        self.assertNotEqual(
            compute_content_digest(_rc2()),
            compute_content_digest(_rc2(observation_ids=[_OBS_B])),
        )

    def test_digest_covers_evidence(self) -> None:
        changed = _rc2(evidence={"comparison": ">", "threshold": 9.5, "quality": ["ok"]})
        self.assertNotEqual(compute_content_digest(_rc2()), compute_content_digest(changed))

    def test_digest_covers_rule_snapshot_id(self) -> None:
        self.assertNotEqual(
            compute_content_digest(_rc2()),
            compute_content_digest(
                _rc2(rule_snapshot_id=uuid.UUID("99999999-9999-4999-8999-999999999999"))
            ),
        )

    def test_observation_ids_upper_bound_enforced(self) -> None:
        with self.assertRaises(ValueError):
            _rc2(observation_ids=[f"obs-{i:03d}" for i in range(65)])

    def test_observation_ids_at_bound_accepted(self) -> None:
        payload = _rc2(observation_ids=[f"obs-{i:03d}" for i in range(64)])
        self.assertEqual(len(payload.observation_ids), 64)

    def test_unknown_schema_version_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _rc2(schema_version="2.0")

    def test_oversized_payload_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _rc2(evidence={"blob": "x" * 40_000})


class TraceabilityPersistenceCase(_World):
    def test_replay_with_new_evidence_conflicts(self) -> None:
        with Session(self.eng) as session:
            first = receive_candidate(session, self._world_candidate())
            replay = receive_candidate(
                session, self._world_candidate(observation_ids=[_OBS_B])
            )
        self.assertEqual(first.status, "accepted_new")
        self.assertEqual(replay.status, "conflict")
        self.assertEqual(replay.reason_code, "content_digest_mismatch")

    def test_by_source_exposes_full_body(self) -> None:
        with Session(self.eng) as session:
            receive_candidate(session, self._world_candidate())
            stored = get_event_by_source(session, "77777777-7777-4777-8777-777777777777")
        self.assertEqual(stored.severity, "warning")
        self.assertEqual(stored.warehouse_id, self.wid)
        self.assertEqual(stored.rule_version, "v1")
        self.assertEqual(stored.algorithm_version, "threshold-rule/0.2.0-rc2")
        self.assertIn(_OBS_A, stored.observation_ids_json)
        self.assertIn("threshold", stored.evidence_json or "")


# ---------------------------------------------------------------------------
# Defect 3 — read-phase storage failures stay in the receipt envelope
# ---------------------------------------------------------------------------


def _broken() -> OperationalError:
    return OperationalError("SELECT", {}, RuntimeError("db busy"))


class ReadPhaseFailureCase(_World):
    def test_batch_lookup_failure_is_retryable(self) -> None:
        with Session(self.eng) as session:
            with (
                mock.patch.object(session, "get", side_effect=_broken()),
                mock.patch.object(session, "rollback"),
            ):
                receipt = receive_candidate(session, self._world_candidate())
        self.assertEqual(receipt.status, "retryable_failure")
        self.assertEqual(receipt.reason_code, "storage_unavailable")
        self.assertTrue(receipt.retryable)
        self.assertEqual(receipt.retry_after_s, 30.0)

    def test_idempotency_lookup_failure_is_retryable(self) -> None:
        with Session(self.eng) as session:
            with (
                mock.patch.object(session, "exec", side_effect=_broken()),
                mock.patch.object(session, "rollback"),
            ):
                receipt = receive_candidate(session, self._world_candidate())
        self.assertEqual(receipt.status, "retryable_failure")
        self.assertEqual(receipt.reason_code, "storage_unavailable")

    def test_post_integrity_fallback_read_failure_is_retryable(self) -> None:
        with Session(self.eng) as session:
            with (
                mock.patch.object(
                    session,
                    "commit",
                    side_effect=IntegrityError("INSERT", {}, RuntimeError("dup")),
                ),
                mock.patch.object(session, "rollback"),
                mock.patch.object(session, "exec", side_effect=_broken()),
            ):
                receipt = receive_candidate(session, self._world_candidate())
        self.assertEqual(receipt.status, "retryable_failure")
        self.assertEqual(receipt.reason_code, "storage_unavailable")

    def test_device_event_warehouse_lookup_failure_is_retryable(self) -> None:
        payload = DeviceEventCreate(
            source_event_id="dev-evt-1",
            kind=DeviceEventKind.OFFLINE,
            warehouse_id=self.wid,
            device_id="device-01",
            sampled_at=T0,
        )
        with Session(self.eng) as session:
            with (
                mock.patch.object(session, "get", side_effect=_broken()),
                mock.patch.object(session, "rollback"),
            ):
                receipt = receive_device_event(session, payload)
        self.assertEqual(receipt.status, "retryable_failure")
        self.assertEqual(receipt.reason_code, "storage_unavailable")

    def test_non_key_integrity_violation_is_retryable(self) -> None:
        """A unique violation on a non-key constraint stored nothing
        and fixed no caller input — classified retriable, not
        duplicate/conflict."""
        with Session(self.eng) as session:
            with (
                mock.patch.object(
                    session,
                    "commit",
                    side_effect=IntegrityError("INSERT", {}, RuntimeError("dup")),
                ),
                mock.patch.object(session, "rollback"),
                mock.patch(
                    "app.modules.alerts.services.events._get_by_source",
                    return_value=None,
                ),
            ):
                receipt = receive_candidate(session, self._world_candidate())
        self.assertEqual(receipt.status, "retryable_failure")
        self.assertEqual(receipt.reason_code, "storage_unavailable")

    def test_device_event_idempotency_lookup_failure_is_retryable(self) -> None:
        payload = DeviceEventCreate(
            source_event_id="dev-evt-2",
            kind=DeviceEventKind.OFFLINE,
            warehouse_id=self.wid,
            device_id="device-01",
            sampled_at=T0,
        )
        with Session(self.eng) as session:
            with (
                mock.patch.object(session, "exec", side_effect=_broken()),
                mock.patch.object(session, "rollback"),
            ):
                receipt = receive_device_event(session, payload)
        self.assertEqual(receipt.status, "retryable_failure")
        self.assertEqual(receipt.reason_code, "storage_unavailable")

    def test_device_event_post_integrity_fallback_read_failure_is_retryable(
        self,
    ) -> None:
        payload = DeviceEventCreate(
            source_event_id="dev-evt-3",
            kind=DeviceEventKind.OFFLINE,
            warehouse_id=self.wid,
            device_id="device-01",
            sampled_at=T0,
        )
        with Session(self.eng) as session:
            with (
                mock.patch.object(
                    session,
                    "commit",
                    side_effect=IntegrityError("INSERT", {}, RuntimeError("dup")),
                ),
                mock.patch.object(session, "rollback"),
                mock.patch.object(session, "exec", side_effect=_broken()),
            ):
                receipt = receive_device_event(session, payload)
        self.assertEqual(receipt.status, "retryable_failure")
        self.assertEqual(receipt.reason_code, "storage_unavailable")

    def test_by_source_route_maps_storage_failure_to_503(self) -> None:
        class _BrokenSession:
            def exec(self, *_args: Any, **_kw: Any) -> None:
                raise _broken()

        response = Response()
        with self.assertRaises(HTTPException) as ctx:
            read_candidate_by_source(_BrokenSession(), object(), "evt-x", response)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(response.headers.get("retry-after"), "30")


# ---------------------------------------------------------------------------
# Defect 4 — S1 → S2 → S3 open-ended chain replacement
# ---------------------------------------------------------------------------


def _snapshot(
    start: datetime, supersedes: tuple[uuid.UUID, ...] = ()
) -> BatchRuleSnapshotCreate:
    return BatchRuleSnapshotCreate(
        version_id=uuid.uuid4(),
        rules=[
            RuleDict(
                metric_code="temperature_high",
                candidate_kind="threshold_high",
                severity=RuleSeverity.WARNING,
                threshold_low=None,
                threshold_high=8.0,
            )
        ],
        effective_start_at=start,
        effective_end_at=None,
        supersedes_snapshot_ids=list(supersedes),
    )


class ChainReplacementCase(_World):
    def test_third_open_version_replacement_accepted(self) -> None:
        with Session(self.eng) as session:
            s1 = create_snapshot(session, self.bid, _snapshot(T0))
            s2 = create_snapshot(session, self.bid, _snapshot(T1, (s1.id,)))
            s3 = create_snapshot(session, self.bid, _snapshot(T2, (s2.id,)))
            ids = [s1.id, s2.id, s3.id]
            selection = select_rules_for_observation(
                session,
                warehouse_id=self.wid,
                batch_ids=[self.bid],
                metric_code="temperature_high",
                sampled_at=T2 + timedelta(hours=1),
            )
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(selection.status, "configured")
        self.assertEqual([e.snapshot_id for e in selection.rules], [ids[2]])

    def test_chain_selection_returns_each_version_in_its_window(self) -> None:
        with Session(self.eng) as session:
            s1 = create_snapshot(session, self.bid, _snapshot(T0))
            s2 = create_snapshot(session, self.bid, _snapshot(T1, (s1.id,)))
            s3 = create_snapshot(session, self.bid, _snapshot(T2, (s2.id,)))
            picks = []
            for probe in (T0 + timedelta(hours=1), T1 + timedelta(hours=1), T2 + timedelta(hours=1)):
                selection = select_rules_for_observation(
                    session,
                    warehouse_id=self.wid,
                    batch_ids=[self.bid],
                    metric_code="temperature_high",
                    sampled_at=probe,
                )
                picks.append([e.snapshot_id for e in selection.rules])
        self.assertEqual(picks[0], [s1.id])
        self.assertEqual(picks[1], [s2.id])
        self.assertEqual(picks[2], [s3.id])

    def test_out_of_order_replacement_still_conflicts(self) -> None:
        """An earlier-starting successor leaves S1's derived window
        still covering the new start — a genuine overlap that must
        stay rejected (guards against over-exempting)."""
        with Session(self.eng) as session:
            s1 = create_snapshot(session, self.bid, _snapshot(T0))
            s2 = create_snapshot(session, self.bid, _snapshot(T2, (s1.id,)))
            with self.assertRaises(SnapshotOverlapConflict):
                create_snapshot(
                    session, self.bid, _snapshot(T1, (s2.id,))
                )

    def test_unsuperseded_open_overlap_still_rejected(self) -> None:
        with Session(self.eng) as session:
            create_snapshot(session, self.bid, _snapshot(T0))
            with self.assertRaises(SnapshotOverlapConflict):
                create_snapshot(session, self.bid, _snapshot(T1))


if __name__ == "__main__":
    unittest.main()

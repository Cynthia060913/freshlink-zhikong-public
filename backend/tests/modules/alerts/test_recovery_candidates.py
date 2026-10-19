"""Recovery candidate regressions (S0 f98 §3 decision 3, PR #10).

C posts a ``temperature_recovered`` candidate as a brand-new
``source_event_id`` whose ``related_source_event_id`` points at the
original anomaly. B must:

- reject the recovery explicitly while the original is unknown
  (``invalid``/``unknown_related_event`` — never a dangling stored
  link, never a manufactured "resolved");
- reject warehouse/batch/metric mismatches and recoveries of
  recoveries (``related_event_mismatch``);
- commit the recovery row AND the original alert's
  ``open/acknowledged -> resolved`` transition in ONE transaction;
- treat same-ID-same-body replay as ``accepted_duplicate`` without a
  second close, and same-ID-different-body as ``conflict``;
- include ``related_source_event_id`` in the canonical digest;
- keep a second, distinct recovery of an already-resolved original a
  plain ``accepted_new`` without a redundant transition.
"""

from __future__ import annotations

import hashlib
import json
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, func, select

from app.modules.alerts.models import (
    AlertEvent,
    AlertStatus,
    AlertStatusTransition,
    CandidateCreate,
    CandidateKind,
)
from app.modules.alerts.models.event import compute_content_digest
from app.modules.alerts.services.events import (
    get_alert_status,
    get_event_by_source,
    list_status_transitions,
    receive_candidate,
)
from tests.modules.alerts.test_alerts_routes import _client, _IsolatedCase
from tests.modules.alerts.test_pr10_boundary_regressions import _World


def _broken() -> OperationalError:
    return OperationalError("SELECT", {}, RuntimeError("db busy"))


def _recovery(
    world: _World, source_event_id: str, related: str, **over: object
) -> CandidateCreate:
    return world._world_candidate(  # noqa: SLF001 - test helper reuse
        source_event_id=source_event_id,
        kind=CandidateKind.TEMPERATURE_RECOVERED,
        related_source_event_id=related,
        **over,
    )


class RecoveryValidationCase(_World):
    def test_recovery_requires_related_source_event_id(self) -> None:
        with self.assertRaises(ValueError):
            self._world_candidate(kind=CandidateKind.TEMPERATURE_RECOVERED)

    def test_related_only_valid_on_recovery_kind(self) -> None:
        with self.assertRaises(ValueError):
            self._world_candidate(related_source_event_id="anom-1")

    def test_unknown_related_event_is_explicit_invalid(self) -> None:
        with Session(self.eng) as session:
            receipt = receive_candidate(
                session, _recovery(self, "rec-unknown", "missing-1")
            )
            events = session.exec(select(func.count()).select_from(AlertEvent)).one()
            transitions = session.exec(
                select(func.count()).select_from(AlertStatusTransition)
            ).one()
        self.assertEqual(receipt.status, "invalid")
        self.assertEqual(receipt.reason_code, "unknown_related_event")
        self.assertEqual(events, 0)
        self.assertEqual(transitions, 0)

    def test_unknown_related_then_real_original_is_accepted(self) -> None:
        """invalid is not a permanent poison: once C's original lands,
        re-posting the SAME recovery body classifies accepted_new."""
        with Session(self.eng) as session:
            first = receive_candidate(session, _recovery(self, "rec-late", "anom-1"))
            self.assertEqual(first.status, "invalid")
            anomaly = self._world_candidate(source_event_id="anom-1")
            self.assertEqual(receive_candidate(session, anomaly).status, "accepted_new")
            retry = receive_candidate(session, _recovery(self, "rec-late", "anom-1"))
        self.assertEqual(retry.status, "accepted_new")

    def test_related_batch_mismatch_rejected(self) -> None:
        with Session(self.eng) as session:
            anomaly = self._world_candidate(source_event_id="anom-1", batch_id=self.bid)
            self.assertEqual(receive_candidate(session, anomaly).status, "accepted_new")
            other_bid = self._other_batch(session)
            receipt = receive_candidate(
                session,
                _recovery(self, "rec-1", "anom-1", batch_id=other_bid),
            )
        self.assertEqual(receipt.status, "invalid")
        self.assertEqual(receipt.reason_code, "related_event_mismatch")

    def test_related_metric_mismatch_rejected(self) -> None:
        with Session(self.eng) as session:
            anomaly = self._world_candidate(source_event_id="anom-1")
            self.assertEqual(receive_candidate(session, anomaly).status, "accepted_new")
            receipt = receive_candidate(
                session,
                _recovery(self, "rec-1", "anom-1", metric_code="humidity"),
            )
        self.assertEqual(receipt.status, "invalid")
        self.assertEqual(receipt.reason_code, "related_event_mismatch")

    def test_recovery_of_a_recovery_rejected(self) -> None:
        with Session(self.eng) as session:
            anomaly = self._world_candidate(source_event_id="anom-1")
            receive_candidate(session, anomaly)
            first = receive_candidate(session, _recovery(self, "rec-1", "anom-1"))
            self.assertEqual(first.status, "accepted_new")
            second = receive_candidate(session, _recovery(self, "rec-2", "rec-1"))
        self.assertEqual(second.status, "invalid")
        self.assertEqual(second.reason_code, "related_event_mismatch")

    def _other_batch(self, session: Session) -> uuid.UUID:
        from app.modules.alerts.models import BatchCreate
        from app.modules.alerts.services.batches import create_batch

        return create_batch(session, self.wid, BatchCreate(code="B2", product="x")).id


class RecoveryLifecycleCase(_World):
    anomaly_id: uuid.UUID

    def setUp(self) -> None:
        super().setUp()
        with Session(self.eng) as session:
            receipt = receive_candidate(
                session, self._world_candidate(source_event_id="anom-1")
            )
            assert receipt.status == "accepted_new"
            assert receipt.event_id is not None
            self.anomaly_id = receipt.event_id

    def test_recovery_resolves_original_atomically(self) -> None:
        with Session(self.eng) as session:
            receipt = receive_candidate(session, _recovery(self, "rec-1", "anom-1"))
            self.assertEqual(receipt.status, "accepted_new")
            row = get_event_by_source(session, "rec-1")
            self.assertEqual(row.kind, "temperature_recovered")
            self.assertEqual(row.related_source_event_id, "anom-1")
            self.assertEqual(get_alert_status(session, self.anomaly_id), "resolved")
            transitions = list_status_transitions(session, self.anomaly_id)
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].from_status, "open")
        self.assertEqual(transitions[0].to_status, "resolved")
        self.assertEqual(transitions[0].trigger_source_event_id, "rec-1")
        self.assertEqual(transitions[0].reason, "temperature_recovered")

    def test_anomaly_without_recovery_stays_open(self) -> None:
        with Session(self.eng) as session:
            self.assertEqual(get_alert_status(session, self.anomaly_id), "open")

    def test_replay_recovery_is_duplicate_without_second_close(self) -> None:
        with Session(self.eng) as session:
            first = receive_candidate(session, _recovery(self, "rec-1", "anom-1"))
            replay = receive_candidate(session, _recovery(self, "rec-1", "anom-1"))
            transitions = list_status_transitions(session, self.anomaly_id)
        self.assertEqual(first.status, "accepted_new")
        self.assertEqual(replay.status, "accepted_duplicate")
        self.assertEqual(replay.event_id, first.event_id)
        self.assertEqual(len(transitions), 1)

    def test_conflicting_recovery_replay_is_conflict(self) -> None:
        with Session(self.eng) as session:
            receive_candidate(session, _recovery(self, "rec-1", "anom-1"))
            changed = _recovery(self, "rec-1", "anom-1", value=12.0)
            receipt = receive_candidate(session, changed)
            transitions = list_status_transitions(session, self.anomaly_id)
        self.assertEqual(receipt.status, "conflict")
        self.assertEqual(receipt.reason_code, "content_digest_mismatch")
        self.assertEqual(len(transitions), 1)

    def test_related_id_is_part_of_the_digest(self) -> None:
        with Session(self.eng) as session:
            other = self._world_candidate(source_event_id="anom-2")
            receive_candidate(session, other)
            receive_candidate(session, _recovery(self, "rec-1", "anom-1"))
            swapped = _recovery(self, "rec-1", "anom-2")
            receipt = receive_candidate(session, swapped)
        self.assertEqual(receipt.status, "conflict")

    def test_second_distinct_recovery_no_redundant_transition(self) -> None:
        with Session(self.eng) as session:
            first = receive_candidate(session, _recovery(self, "rec-1", "anom-1"))
            second = receive_candidate(session, _recovery(self, "rec-2", "anom-1"))
            transitions = list_status_transitions(session, self.anomaly_id)
        self.assertEqual(first.status, "accepted_new")
        self.assertEqual(second.status, "accepted_new")
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].trigger_source_event_id, "rec-1")

    def test_acknowledged_original_resolves_from_acknowledged(self) -> None:
        with Session(self.eng) as session:
            session.add(
                AlertStatusTransition(
                    alert_event_id=self.anomaly_id,
                    from_status=AlertStatus.OPEN,
                    to_status=AlertStatus.ACKNOWLEDGED,
                    trigger_source_event_id=None,
                    reason="manual-ack-test",
                )
            )
            session.commit()
            receive_candidate(session, _recovery(self, "rec-1", "anom-1"))
            transitions = list_status_transitions(session, self.anomaly_id)
            final_status = get_alert_status(session, self.anomaly_id)
        self.assertEqual(len(transitions), 2)
        self.assertEqual(transitions[0].to_status, "acknowledged")
        self.assertEqual(transitions[1].from_status, "acknowledged")
        self.assertEqual(transitions[1].to_status, "resolved")
        self.assertEqual(final_status, "resolved")

    def test_commit_failure_persists_neither_row_nor_transition(self) -> None:
        payload = _recovery(self, "rec-1", "anom-1")
        with Session(self.eng) as session:
            with (
                mock.patch.object(session, "commit", side_effect=_broken()),
                mock.patch.object(session, "rollback"),
            ):
                receipt = receive_candidate(session, payload)
        self.assertEqual(receipt.status, "retryable_failure")
        self.assertEqual(receipt.reason_code, "storage_unavailable")
        with Session(self.eng) as session:
            events = session.exec(select(func.count()).select_from(AlertEvent)).one()
            transitions = session.exec(
                select(func.count()).select_from(AlertStatusTransition)
            ).one()
            self.assertEqual(events, 1)  # only the setUp anomaly
            self.assertEqual(transitions, 0)
        with Session(self.eng) as session:
            retry = receive_candidate(session, payload)
            self.assertEqual(retry.status, "accepted_new")
            self.assertEqual(get_alert_status(session, self.anomaly_id), "resolved")


class RecoveryHttpCase(_IsolatedCase):
    def _ids(self, client: TestClient) -> dict[str, str]:
        wid = client.post("/warehouses/", json={"code": "WH", "name": "W"}).json()["id"]
        bid = client.post(
            f"/warehouses/{wid}/batches", json={"code": "B-001", "product": "x"}
        ).json()["id"]
        return {"wid": wid, "bid": bid}

    def _anomaly_body(self, ids: dict[str, str]) -> dict[str, object]:
        return {
            "source_event_id": "anom-http-1",
            "warehouse_id": ids["wid"],
            "batch_id": ids["bid"],
            "observation_ids": ["00000000-0000-4000-8000-000000000004"],
            "metric_code": "temperature",
            "sampled_at": "2026-10-14T00:01:00Z",
            "value": 11.0,
            "unit": "°C",
            "kind": "temperature_high",
            "severity": "warning",
            "rule_version": "v1",
        }

    def _recovery_body(self, ids: dict[str, str]) -> dict[str, object]:
        # severity/rule_version PRESERVE the anomaly's values (f98 §3
        # decision 1) — the HTTP fixtures must model a legal pair.
        return {
            "source_event_id": "rec-http-1",
            "warehouse_id": ids["wid"],
            "batch_id": ids["bid"],
            "observation_ids": ["00000000-0000-4000-8000-000000000006"],
            "metric_code": "temperature",
            "sampled_at": "2026-10-14T00:05:00Z",
            "value": 7.0,
            "unit": "°C",
            "kind": "temperature_recovered",
            "severity": "warning",
            "rule_version": "v1",
            "related_source_event_id": "anom-http-1",
        }

    def test_http_recovery_flow_resolves_original(self) -> None:
        client = _client()
        ids = self._ids(client)
        anomaly = client.post("/candidates", json=self._anomaly_body(ids))
        self.assertEqual(anomaly.status_code, 201, anomaly.text)

        before = client.get("/candidates/by-source/anom-http-1")
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual(before.json()["status"], "open")

        recovery = client.post("/candidates", json=self._recovery_body(ids))
        self.assertEqual(recovery.status_code, 201, recovery.text)
        self.assertEqual(recovery.json()["status"], "accepted_new")

        after = client.get("/candidates/by-source/anom-http-1")
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json()["status"], "resolved")

        rec_read = client.get("/candidates/by-source/rec-http-1")
        self.assertEqual(rec_read.status_code, 200, rec_read.text)
        self.assertEqual(rec_read.json()["kind"], "temperature_recovered")
        self.assertEqual(rec_read.json()["related_source_event_id"], "anom-http-1")

    def test_http_unknown_related_is_422(self) -> None:
        client = _client()
        ids = self._ids(client)
        body = self._recovery_body(ids)
        body["related_source_event_id"] = "missing-original"
        r = client.post("/candidates", json=body)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["status"], "invalid")
        self.assertEqual(r.json()["reason_code"], "unknown_related_event")

    def test_http_recovery_without_related_is_422(self) -> None:
        client = _client()
        ids = self._ids(client)
        body = self._recovery_body(ids)
        del body["related_source_event_id"]
        r = client.post("/candidates", json=body)
        self.assertEqual(r.status_code, 422)

    def test_http_recovery_replay_is_duplicate(self) -> None:
        client = _client()
        ids = self._ids(client)
        client.post("/candidates", json=self._anomaly_body(ids))
        first = client.post("/candidates", json=self._recovery_body(ids))
        replay = client.post("/candidates", json=self._recovery_body(ids))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "accepted_duplicate")


# ---------------------------------------------------------------------------
# Round-4 defect 1 — digest compatibility for empty related_source_event_id
# (PR #10 reviews 5563581508 / 5563695226): a NULL related id on an
# ordinary candidate must stay OUT of the canonical payload, or old
# persisted rows replay as conflict instead of accepted_duplicate.
# ---------------------------------------------------------------------------

# The fixed RC2 example digest shared with #5 / C's PR #11 vectors,
# computed over the PRE-recovery field set (no related key).
_PLAIN_RC2_DIGEST = (
    "sha256:20789a39ec33eba8c25651c3f41c5c077c466c852ff58a11532cb211ca5aa234"
)


class OldDigestCompatCase(_World):
    def test_plain_candidate_digest_matches_pre_recovery_vector(self) -> None:
        from tests.modules.alerts.test_pr10_boundary_regressions import _rc2

        self.assertEqual(compute_content_digest(_rc2()), _PLAIN_RC2_DIGEST)

    def test_stored_payload_json_has_no_related_key(self) -> None:
        with Session(self.eng) as session:
            receive_candidate(session, self._world_candidate(source_event_id="plain-1"))
            row = get_event_by_source(session, "plain-1")
        self.assertNotIn("related_source_event_id", row.payload_json)

    def test_old_persisted_row_same_body_replay_is_duplicate(self) -> None:
        """A's cross-version probe: a row persisted by the pre-recovery
        code (digest over the old field set) must still answer
        accepted_duplicate when the exact old body is replayed."""
        payload = self._world_candidate(source_event_id="old-1")
        # Recompute the digest the way the OLD code did — from the
        # payload dict WITHOUT the related key — independently of the
        # app's canonical function, so a regression that re-adds the
        # key turns this into a conflict and fails the test.
        data = payload.model_dump(mode="json")
        data.pop("related_source_event_id", None)
        old_canonical = json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        old_digest = (
            f"sha256:{hashlib.sha256(old_canonical.encode('utf-8')).hexdigest()}"
        )
        with Session(self.eng) as session:
            session.add(
                AlertEvent(
                    source_event_id="old-1",
                    kind="temperature_high",
                    warehouse_id=self.wid,
                    batch_id=self.bid,
                    metric_code="temperature",
                    sampled_at=payload.sampled_at,
                    value=11.0,
                    unit="°C",
                    severity="warning",
                    observation_ids_json=json.dumps(
                        payload.observation_ids, ensure_ascii=False
                    ),
                    rule_version="v1",
                    algorithm_version="threshold-rule/0.2.0-rc2",
                    content_digest=old_digest,
                    payload_json=old_canonical,
                )
            )
            session.commit()
            receipt = receive_candidate(session, payload)
            row = get_event_by_source(session, "old-1")
        self.assertEqual(receipt.status, "accepted_duplicate")
        self.assertEqual(receipt.event_id, row.id)

    def test_recovery_digest_still_includes_related_id(self) -> None:
        # A NON-EMPTY related id is part of the digest: replaying the
        # same recovery key with a DIFFERENT (even valid) related id is
        # a conflict, never a duplicate.
        with Session(self.eng) as session:
            receive_candidate(session, self._world_candidate(source_event_id="anom-1"))
            receive_candidate(session, _recovery(self, "rec-1", "anom-1"))
            receipt = receive_candidate(session, _recovery(self, "rec-1", "anom-2"))
        self.assertEqual(receipt.status, "conflict")
        self.assertEqual(receipt.reason_code, "content_digest_mismatch")


# ---------------------------------------------------------------------------
# Round-4 defect 2 — the original must be a temperature anomaly and the
# recovery must PRESERVE its rule identity + severity (PR #10 reviews
# 5563581508 / 5563695226). Wrong input adds no row and no transition.
# ---------------------------------------------------------------------------


class RecoveryKindAndRuleCase(_World):
    _SNAP_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    _SNAP_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

    def _original(self, source_event_id: str, **over: object) -> None:
        with Session(self.eng) as session:
            receipt = receive_candidate(
                session, self._world_candidate(source_event_id=source_event_id, **over)
            )
            assert receipt.status == "accepted_new"

    def _assert_no_side_effects(self, source: str) -> None:
        with Session(self.eng) as session:
            events = session.exec(select(func.count()).select_from(AlertEvent)).one()
            transitions = session.exec(
                select(func.count()).select_from(AlertStatusTransition)
            ).one()
            row = get_event_by_source(session, source)
            status = get_alert_status(session, row.id)
        self.assertEqual(events, 1)  # only the original anomaly
        self.assertEqual(transitions, 0)
        self.assertEqual(status, AlertStatus.OPEN)

    def test_data_quality_original_cannot_be_resolved(self) -> None:
        self._original("dq-1", kind=CandidateKind.DATA_QUALITY)
        with Session(self.eng) as session:
            receipt = receive_candidate(session, _recovery(self, "rec-1", "dq-1"))
        self.assertEqual(receipt.status, "invalid")
        self.assertEqual(receipt.reason_code, "related_event_mismatch")
        self._assert_no_side_effects("dq-1")

    def test_humidity_original_cannot_be_resolved(self) -> None:
        self._original(
            "hyg-1",
            kind=CandidateKind.HUMIDITY_HIGH,
            metric_code="humidity",
        )
        with Session(self.eng) as session:
            receipt = receive_candidate(
                session,
                _recovery(self, "rec-1", "hyg-1", metric_code="humidity"),
            )
        self.assertEqual(receipt.status, "invalid")
        self.assertEqual(receipt.reason_code, "related_event_mismatch")
        self._assert_no_side_effects("hyg-1")

    def test_temperature_low_original_is_recoverable(self) -> None:
        self._original("low-1", kind=CandidateKind.TEMPERATURE_LOW)
        with Session(self.eng) as session:
            receipt = receive_candidate(session, _recovery(self, "rec-1", "low-1"))
            row = get_event_by_source(session, "low-1")
            status = get_alert_status(session, row.id)
        self.assertEqual(receipt.status, "accepted_new")
        self.assertEqual(status, "resolved")

    def test_rule_identity_and_severity_must_preserve_original(self) -> None:
        """A's probe: original critical + original-v1 + snapshotA; recovery
        info + other-v2 + snapshotB — must be rejected before any write."""
        self._original(
            "anom-1",
            severity="critical",
            rule_version="original-v1",
            rule_snapshot_id=self._SNAP_A,
        )
        with Session(self.eng) as session:
            receipt = receive_candidate(
                session,
                _recovery(
                    self,
                    "rec-1",
                    "anom-1",
                    severity="info",
                    rule_version="other-v2",
                    rule_snapshot_id=self._SNAP_B,
                ),
            )
        self.assertEqual(receipt.status, "invalid")
        self.assertEqual(receipt.reason_code, "related_rule_mismatch")
        self._assert_no_side_effects("anom-1")

    def test_matching_rule_identity_recovery_accepted(self) -> None:
        self._original(
            "anom-1",
            severity="critical",
            rule_version="original-v1",
            rule_snapshot_id=self._SNAP_A,
        )
        with Session(self.eng) as session:
            receipt = receive_candidate(
                session,
                _recovery(
                    self,
                    "rec-1",
                    "anom-1",
                    severity="critical",
                    rule_version="original-v1",
                    rule_snapshot_id=self._SNAP_A,
                ),
            )
            row = get_event_by_source(session, "anom-1")
            status = get_alert_status(session, row.id)
        self.assertEqual(receipt.status, "accepted_new")
        self.assertEqual(status, "resolved")

    def test_severity_alone_mismatch_rejected(self) -> None:
        self._original("anom-1", severity="critical")
        with Session(self.eng) as session:
            receipt = receive_candidate(
                session, _recovery(self, "rec-1", "anom-1", severity="warning")
            )
        self.assertEqual(receipt.status, "invalid")
        self.assertEqual(receipt.reason_code, "related_rule_mismatch")
        self._assert_no_side_effects("anom-1")


if __name__ == "__main__":
    unittest.main()

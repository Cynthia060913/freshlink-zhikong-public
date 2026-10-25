"""Fixed C internal-v0 to proposed B F03 correction projection vectors."""

import copy
import hashlib
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, select, update

from app.modules.telemetry.correction_wire_candidate import (
    prepare_held_correction_wire_on_connection_candidate,
    project_held_correction_wire_candidate,
)
from app.modules.telemetry.data_quality_correction_candidate import (
    _correction_source_id,
)
from app.modules.telemetry.f03_body_candidate import (
    f03_digest_candidate,
    normalize_f03_body_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.quality import process_batch
from app.modules.telemetry.storage_candidate import metadata, observations

ORIGINAL_ID = "11111111-1111-4111-8111-111111111111"
WAREHOUSE_ID = "33333333-3333-4333-8333-333333333333"
BATCH_ID = "44444444-4444-4444-8444-444444444444"
OBSERVATION_ID = "55555555-5555-4555-8555-555555555555"
EVIDENCE_ID = "66666666-6666-4666-8666-666666666666"
SNAPSHOT_ID = "77777777-7777-4777-8777-777777777777"
CORRECTION_ID = str(
    _correction_source_id(UUID(ORIGINAL_ID), f"reception:{EVIDENCE_ID}:{'a' * 64}")
)
DEVICE_ID = "88888888-8888-4888-8888-888888888888"
MESSAGE_ID = "99999999-9999-4999-8999-999999999999"


def _digest(body: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _original() -> dict:
    return normalize_f03_body_candidate(
        {
            "schema_version": "1.0",
            "source_event_id": ORIGINAL_ID,
            "kind": "temperature_high",
            "warehouse_id": WAREHOUSE_ID,
            "batch_id": BATCH_ID,
            "observation_ids": [OBSERVATION_ID],
            "metric_code": "temperature",
            "sampled_at": "2026-10-25T08:00:00Z",
            "value": 11.0,
            "unit": "°C",
            "severity": "warning",
            "rule_snapshot_id": SNAPSHOT_ID,
            "rule_version": "rule-1",
            "algorithm_version": "threshold/v1",
            "evidence": {"rule": "high"},
            "related_source_event_id": None,
        }
    )


def _internal() -> dict:
    return {
        "schema_version": "c-data-quality-correction/v0",
        "source_event_id": CORRECTION_ID,
        "kind": "data_quality",
        "related_source_event_id": ORIGINAL_ID,
        "warehouse_id": WAREHOUSE_ID,
        "observation_id": OBSERVATION_ID,
        "evidence": {
            "evidence_kind": "reception",
            "evidence_id": EVIDENCE_ID,
            "reason_code": "invalid_temperature_unit",
            "outcome": "invalid",
            "received_at_utc": "2026-10-25T08:03:00Z",
            "raw_sha256": "a" * 64,
            "send_state": "held_until_f03_contract_acceptance",
        },
    }


def _raw_observation() -> dict:
    return {
        "schema_version": "1.0",
        "device_id": DEVICE_ID,
        "warehouse_id": WAREHOUSE_ID,
        "message_id": MESSAGE_ID,
        "sampled_at": "2026-10-25T08:00:00Z",
        "metrics": [
            {"key": "temperature", "value": 11.0, "unit": "°C"},
            {"key": "humidity", "value": 70.0, "unit": "%RH"},
        ],
    }


class CorrectionWireCandidateTests(unittest.TestCase):
    def _project(self, internal: dict, original: dict):
        return project_held_correction_wire_candidate(
            internal_body=internal,
            internal_body_sha256=_digest(internal),
            original_body=original,
            original_body_sha256=f03_digest_candidate(original).removeprefix("sha256:"),
        )

    def test_stable_wire_body_digest_and_no_internal_mutation(self) -> None:
        internal, original = _internal(), _original()
        saved_internal, saved_original = (
            copy.deepcopy(internal),
            copy.deepcopy(original),
        )
        first = self._project(internal, original)
        second = self._project(internal, original)
        self.assertEqual(first, second)
        self.assertEqual(first.internal_body_sha256, _digest(internal))
        self.assertEqual(first.content_digest, "sha256:" + _digest(first.body))
        self.assertEqual(first.body["source_event_id"], CORRECTION_ID)
        self.assertEqual(first.body["related_source_event_id"], ORIGINAL_ID)
        self.assertEqual(first.body["batch_id"], BATCH_ID)
        self.assertEqual(first.body["observation_ids"], [OBSERVATION_ID])
        self.assertEqual(first.body["sampled_at"], "2026-10-25T08:03:00Z")
        self.assertIsNone(first.body["value"])
        self.assertEqual(first.body["severity"], "info")
        self.assertNotIn("send_state", first.body["evidence"]["correction"])
        self.assertEqual(internal, saved_internal)
        self.assertEqual(original, saved_original)

    def test_published_json_matches_exact_wire_vector(self) -> None:
        path = (
            Path(__file__).resolve().parents[3]
            / "docs/modules/C/examples/data-quality-correction-f03-wire-candidate.json"
        )
        published = json.loads(path.read_text(encoding="utf-8"))
        projection = self._project(_internal(), _original())
        self.assertEqual(published, projection.body)
        self.assertEqual(
            projection.content_digest,
            "sha256:8c04fb17927df069a2a4bf02213f221a76af0fa62110313db07f34e566290dbf",
        )

    def test_changed_frozen_digest_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "internal correction body digest"):
            project_held_correction_wire_candidate(
                internal_body=_internal(),
                internal_body_sha256="0" * 64,
                original_body=_original(),
                original_body_sha256=f03_digest_candidate(_original()).removeprefix(
                    "sha256:"
                ),
            )

    def test_wrong_link_or_warehouse_cannot_project(self) -> None:
        for field, value in (
            ("related_source_event_id", CORRECTION_ID),
            ("warehouse_id", BATCH_ID),
            ("observation_id", EVIDENCE_ID),
        ):
            with self.subTest(field=field):
                internal = _internal()
                internal[field] = value
                with self.assertRaisesRegex(ValueError, "does not match"):
                    self._project(internal, _original())

    def test_invalid_evidence_and_original_are_rejected(self) -> None:
        internal = _internal()
        internal["evidence"]["raw_sha256"] = "not-a-digest"
        with self.assertRaisesRegex(ValueError, "raw evidence digest"):
            self._project(internal, _original())
        original = _original()
        original["kind"] = "temperature_recovered"
        with self.assertRaises(ValueError):
            self._project(_internal(), original)

    def test_changed_source_identity_is_not_projected(self) -> None:
        internal = _internal()
        internal["source_event_id"] = EVIDENCE_ID
        with self.assertRaisesRegex(ValueError, "stable evidence key"):
            self._project(internal, _original())

    def test_connection_port_reads_held_row_without_releasing_it(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        metadata.create_all(engine)
        now = datetime(2026, 10, 25, 8, 3, tzinfo=UTC)
        internal, original = _internal(), _original()
        raw = _raw_observation()
        quality_view = process_batch([{**raw, "received_at": "2026-10-25T08:03:00Z"}])[
            0
        ]
        try:
            with engine.begin() as connection:
                connection.execute(
                    observations.insert().values(
                        id=UUID(OBSERVATION_ID),
                        device_id=UUID(DEVICE_ID),
                        message_id=UUID(MESSAGE_ID),
                        warehouse_id=UUID(WAREHOUSE_ID),
                        content_sha256="b" * 64,
                        raw_text=json.dumps(raw),
                        raw_payload=raw,
                        sampled_at_utc=datetime(2026, 10, 25, 8, tzinfo=UTC),
                        first_received_at_utc=now,
                        normalized=quality_view["normalized"],
                        quality=quality_view["quality"],
                        risk=quality_view["risk"],
                    )
                )
                connection.execute(
                    outbox.insert().values(
                        source_event_id=UUID(ORIGINAL_ID),
                        related_source_event_id=None,
                        observation_id=UUID(OBSERVATION_ID),
                        body_sha256=f03_digest_candidate(original).removeprefix(
                            "sha256:"
                        ),
                        body=original,
                        state="delivered",
                        attempt_count=1,
                        next_attempt_at_utc=now,
                    )
                )
                connection.execute(
                    outbox.insert().values(
                        source_event_id=UUID(CORRECTION_ID),
                        related_source_event_id=UUID(ORIGINAL_ID),
                        observation_id=UUID(OBSERVATION_ID),
                        body_sha256=_digest(internal),
                        body=internal,
                        state="held_contract",
                        attempt_count=0,
                        next_attempt_at_utc=now,
                    )
                )
            with engine.begin() as connection:
                projected = prepare_held_correction_wire_on_connection_candidate(
                    connection, source_event_id=UUID(CORRECTION_ID)
                )
                self.assertIsNotNone(projected)
                self.assertEqual(projected, self._project(internal, original))
            with engine.connect() as connection:
                saved = (
                    connection.execute(
                        select(outbox).where(
                            outbox.c.source_event_id == UUID(CORRECTION_ID)
                        )
                    )
                    .mappings()
                    .one()
                )
                self.assertEqual(saved["state"], "held_contract")
                self.assertEqual(saved["attempt_count"], 0)
                self.assertEqual(saved["body"], internal)
                self.assertEqual(saved["body_sha256"], _digest(internal))
            changed_raw = copy.deepcopy(raw)
            changed_raw["metrics"][0]["value"] = 5.0
            with engine.begin() as connection:
                connection.execute(
                    update(observations)
                    .where(observations.c.id == UUID(OBSERVATION_ID))
                    .values(raw_payload=changed_raw)
                )
                with self.assertRaisesRegex(ValueError, "raw_text and raw_payload"):
                    prepare_held_correction_wire_on_connection_candidate(
                        connection, source_event_id=UUID(CORRECTION_ID)
                    )
            with engine.begin() as connection:
                connection.execute(
                    update(observations)
                    .where(observations.c.id == UUID(OBSERVATION_ID))
                    .values(raw_payload=raw, quality=["missing_metric:humidity"])
                )
                with self.assertRaisesRegex(ValueError, "stored quality differs"):
                    prepare_held_correction_wire_on_connection_candidate(
                        connection, source_event_id=UUID(CORRECTION_ID)
                    )
            with engine.begin() as connection:
                connection.execute(
                    update(observations)
                    .where(observations.c.id == UUID(OBSERVATION_ID))
                    .values(quality=["ok"], raw_text=json.dumps(changed_raw))
                )
                with self.assertRaisesRegex(ValueError, "raw_text and raw_payload"):
                    prepare_held_correction_wire_on_connection_candidate(
                        connection, source_event_id=UUID(CORRECTION_ID)
                    )
            with engine.connect() as connection:
                saved = connection.execute(
                    select(outbox.c.state, outbox.c.attempt_count).where(
                        outbox.c.source_event_id == UUID(CORRECTION_ID)
                    )
                ).one()
                self.assertEqual(saved, ("held_contract", 0))
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

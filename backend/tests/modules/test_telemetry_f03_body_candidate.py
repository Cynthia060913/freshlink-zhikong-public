"""C-only F03 body compatibility checks against B PR #10 at 9cf9a5d.

The digest vectors are cross-team examples, not proof of a live B delivery.
"""

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select

from app.modules.telemetry.f03_body_candidate import (
    f03_digest_candidate,
    normalize_f03_body_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox, stage_f03_candidate
from app.modules.telemetry.storage_candidate import ingest_observation, metadata

SOURCE = "77777777-7777-4777-8777-777777777777"
OBSERVATION = "00000000-0000-4000-8000-000000000004"
WAREHOUSE = "22222222-2222-4222-8222-222222222222"
BATCH = "55555555-5555-4555-8555-555555555555"
RULE = "88888888-8888-4888-8888-888888888888"


def _anomaly() -> dict:
    return {
        "schema_version": "1.0",
        "source_event_id": SOURCE,
        "kind": "temperature_high",
        "warehouse_id": WAREHOUSE,
        "batch_id": BATCH,
        "observation_ids": [OBSERVATION],
        "metric_code": "temperature",
        "sampled_at": "2026-10-14T00:01:00Z",
        "value": 11.0,
        "unit": "°C",
        "severity": "warning",
        "rule_snapshot_id": RULE,
        "rule_version": "v1",
        "algorithm_version": "threshold-rule/0.2.0-rc2",
        "evidence": {"comparison": ">", "threshold": 8.0, "quality": ["ok"]},
    }


class Reception:
    def __init__(self, raw_text: str):
        self.raw_text = raw_text
        self.reception_id = uuid4()
        self.received_at = datetime(2026, 10, 14, 0, 1, 1, tzinfo=UTC)
        self.authenticated_device_id = UUID("11111111-1111-4111-8111-111111111111")
        self.warehouse_id = UUID(WAREHOUSE)

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


class F03BodyCandidateTests(unittest.TestCase):
    def test_anomaly_includes_new_nullable_link_in_b_digest(self) -> None:
        body = normalize_f03_body_candidate(_anomaly())
        self.assertIsNone(body["related_source_event_id"])
        self.assertEqual(
            set(body),
            {
                "schema_version",
                "source_event_id",
                "kind",
                "warehouse_id",
                "batch_id",
                "observation_ids",
                "metric_code",
                "sampled_at",
                "value",
                "unit",
                "severity",
                "rule_snapshot_id",
                "rule_version",
                "algorithm_version",
                "evidence",
                "related_source_event_id",
            },
        )
        # B's new optional DTO field changes the old 7b digest even for
        # ordinary anomalies. This vector was computed from 9cf's model_dump
        # field set and B's documented canonical UTF-8 JSON rule.
        self.assertEqual(
            f03_digest_candidate(body),
            "sha256:f862dc326de9146c30723cb636cdd8e7fbb99cea3eb464ee09fd2d40cdc9f36c",
        )
        with self.assertRaisesRegex(ValueError, "complete normalized"):
            f03_digest_candidate(_anomaly())

    def test_recovery_matches_b_new_fixed_digest(self) -> None:
        body = {
            **_anomaly(),
            "source_event_id": "99999999-9999-4999-8999-999999999999",
            "related_source_event_id": SOURCE,
            "kind": "temperature_recovered",
            "observation_ids": ["00000000-0000-4000-8000-000000000006"],
            "sampled_at": "2026-10-14T00:05:00Z",
            "value": 7.0,
            "severity": "info",
            "evidence": {"comparison": "<", "threshold": 8.0, "consecutive_ok": 3},
        }
        normalized = normalize_f03_body_candidate(body)
        self.assertEqual(
            f03_digest_candidate(normalized),
            "sha256:d6d1ef23731e60bdcdee94f36c7b271952d071ce7d4d53841cb4dd7cee301c0c",
        )
        self.assertEqual(normalized["related_source_event_id"], SOURCE)

    def test_time_and_numeric_normalization_is_stable(self) -> None:
        body = _anomaly()
        body.update(
            sampled_at="2026-10-14T08:01:00+08:00", value=11, severity=" WARNING "
        )
        self.assertEqual(
            normalize_f03_body_candidate(body), normalize_f03_body_candidate(_anomaly())
        )

    def test_invalid_or_missing_evidence_cannot_be_staged(self) -> None:
        cases = [
            {"sampled_at": "2026-10-14T00:01:00"},
            {"sampled_at": "nonsense"},
            {"unit": "°F"},
            {"value": None},
            {"value": float("nan")},
            {"severity": "safe"},
            {"rule_snapshot_id": None},
            {"observation_ids": []},
            {"evidence": {}},
            {"evidence": {"bad": float("inf")}},
            {"related_source_event_id": SOURCE},
            {"unexpected_public_field": "ignored by B"},
        ]
        for update in cases:
            with self.subTest(update=update), self.assertRaises(ValueError):
                normalize_f03_body_candidate({**_anomaly(), **update})

    def test_recovery_needs_distinct_confirmed_original_id_shape(self) -> None:
        body = {**_anomaly(), "kind": "temperature_recovered"}
        for related in (None, "not-a-uuid", SOURCE):
            with self.subTest(related=related), self.assertRaises(ValueError):
                normalize_f03_body_candidate(
                    {**body, "related_source_event_id": related}
                )

    def test_oversized_body_and_non_json_evidence_rejected(self) -> None:
        for evidence in ({"text": "x" * 32769}, {"set": {1, 2}}):
            with self.subTest(type=type(next(iter(evidence.values())))):
                with self.assertRaises(ValueError):
                    normalize_f03_body_candidate({**_anomaly(), "evidence": evidence})

    def test_normalized_body_survives_outbox_reopen_without_digest_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "c-f03.db"
            engine = create_engine(f"sqlite:///{path}")
            metadata.create_all(engine)
            raw = {
                "schema_version": "1.0",
                "device_id": "11111111-1111-4111-8111-111111111111",
                "message_id": str(uuid4()),
                "sampled_at": "2026-10-14T00:01:00Z",
                "metrics": [{"key": "temperature", "value": 11, "unit": "°C"}],
            }
            result = ingest_observation(
                engine, Reception(json.dumps(raw, ensure_ascii=False))
            )
            assert result.observation_id is not None
            raw_body = {**_anomaly(), "observation_ids": [str(result.observation_id)]}
            body = normalize_f03_body_candidate(raw_body)
            self.assertEqual(
                stage_f03_candidate(
                    engine,
                    source_event_id=UUID(SOURCE),
                    observation_id=result.observation_id,
                    body=raw_body,
                    now=datetime(2026, 10, 14, 0, 1, 2, tzinfo=UTC),
                ),
                "staged",
            )
            engine.dispose()
            reopened = create_engine(f"sqlite:///{path}")
            with reopened.connect() as connection:
                row = connection.execute(select(outbox)).mappings().one()
            self.assertEqual(row["body"], body)
            self.assertEqual("sha256:" + row["body_sha256"], f03_digest_candidate(body))
            reopened.dispose()


if __name__ == "__main__":
    unittest.main()

"""Actual B historical F02 selection feeding C's atomic multi-batch port."""

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select

from app.modules.telemetry.multi_batch_candidate import (
    select_and_stage_gated_multi_batch_candidate,
)
from app.modules.telemetry.outbox_candidate import outbox
from app.modules.telemetry.storage_candidate import (
    ingest_observation,
    metadata,
    observations,
)

SAMPLED = datetime(2026, 10, 19, tzinfo=UTC)


@dataclass(frozen=True)
class Reception:
    raw_text: str
    reception_id: UUID
    authenticated_device_id: UUID
    warehouse_id: UUID
    received_at: datetime = SAMPLED

    @property
    def raw_payload(self) -> dict:
        return json.loads(self.raw_text)


class ActualBMultiBatchTests(unittest.TestCase):
    def test_two_historical_rules_one_high_one_normal(self):
        if sys.version_info < (3, 14):  # noqa: UP036 - B service uses 3.14 syntax
            self.skipTest("B F02 service needs project Python 3.14")

        from sqlmodel import Session

        from app.modules.alerts.models import (
            Batch,
            BatchRuleSnapshot,
            BatchRuleSnapshotCreate,
            Warehouse,
        )
        from app.modules.alerts.services.rules import create_snapshot
        from app.modules.iot.models import Device

        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'b-c-multi.db'}")
            try:
                metadata.create_all(engine)
                for table in (
                    Warehouse.__table__,
                    Batch.__table__,
                    BatchRuleSnapshot.__table__,
                    Device.__table__,
                ):
                    table.create(engine, checkfirst=True)
                device, warehouse, first, second, epoch = [uuid4() for _ in range(5)]
                with Session(engine) as session:
                    session.add(Warehouse(id=warehouse, code="c-multi", name="Multi"))
                    session.flush()
                    for batch, suffix in ((first, "first"), (second, "second")):
                        session.add(
                            Batch(
                                id=batch,
                                warehouse_id=warehouse,
                                code=f"apple-{suffix}",
                                product="apple",
                                started_at=SAMPLED - timedelta(days=1),
                            )
                        )
                    session.add(
                        Device(
                            id=device,
                            code="c-multi-sensor",
                            name="Multi sensor",
                            kind="temperature_sensor",
                            warehouse_id=warehouse,
                        )
                    )
                    session.commit()
                    for batch, threshold in ((first, 8), (second, 20)):
                        create_snapshot(
                            session,
                            batch,
                            BatchRuleSnapshotCreate(
                                version_id=uuid4(),
                                rule_version=f"batch-{threshold}-v1",
                                effective_start_at=SAMPLED - timedelta(hours=1),
                                rules=[
                                    {
                                        "metric_code": "temperature",
                                        "candidate_kind": "threshold_high",
                                        "severity": "warning",
                                        "threshold_high": threshold,
                                        "unit": "°C",
                                    }
                                ],
                            ),
                        )
                raw = {
                    "schema_version": "1.0",
                    "device_id": str(device),
                    "message_id": str(uuid4()),
                    "sampled_at": "2026-10-19T00:00:00Z",
                    "metrics": [
                        {"key": "temperature", "value": 13, "unit": "°C"},
                        {"key": "humidity", "value": 75, "unit": "%RH"},
                    ],
                }
                saved = ingest_observation(
                    engine,
                    Reception(
                        json.dumps(raw, ensure_ascii=False), uuid4(), device, warehouse
                    ),
                )
                self.assertEqual(saved.status, "accepted_new")

                def gate(connection, **_kwargs):
                    connection.exec_driver_sql(
                        "UPDATE telemetry_evaluation_candidate SET state=state WHERE 0"
                    )
                    return 1

                with patch(
                    "app.modules.telemetry.multi_batch_candidate.begin_evidence_write",
                    side_effect=gate,
                ):
                    result = select_and_stage_gated_multi_batch_candidate(
                        engine,
                        device_id=device,
                        warehouse_id=warehouse,
                        stream_epoch=epoch,
                        observation_id=saved.observation_id,
                        now=SAMPLED,
                    )
                    self.assertEqual(result.status, "candidate_staged")
                    self.assertEqual(
                        select_and_stage_gated_multi_batch_candidate(
                            engine,
                            device_id=device,
                            warehouse_id=warehouse,
                            stream_epoch=epoch,
                            observation_id=saved.observation_id,
                            now=SAMPLED,
                        ).status,
                        "not_due",
                    )
                with engine.connect() as connection:
                    bodies = list(connection.execute(select(outbox.c.body)).scalars())
                    risk = connection.execute(
                        select(observations.c.risk).where(
                            observations.c.id == saved.observation_id
                        )
                    ).scalar_one()
                self.assertEqual(len(bodies), 1)
                self.assertEqual(bodies[0]["batch_id"], str(first))
                self.assertEqual(
                    {item["batch_id"]: item["state"] for item in risk["batches"]},
                    {str(first): "candidate", str(second): "normal"},
                )
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()

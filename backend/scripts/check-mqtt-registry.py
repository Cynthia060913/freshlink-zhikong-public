"""Consume a real broker receipt in the DISPOSABLE A test database only.

The exported message is a fixed smoke fixture. Warehouse existence is an
explicit substitute; neither C observations nor production state are written.
"""

import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.engine import make_url
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import User
from app.modules.iot.models import Device
from app.modules.iot.registry import (
    bind_registered_device,
    receive_registered_telemetry,
)

DEVICE_ID = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE_ID = UUID("22222222-2222-4222-8222-222222222222")


class MQTTRegistryCheck(unittest.TestCase):
    def test_actual_receipt_and_registration(self) -> None:
        database = make_url(str(settings.DATABASE_URL))
        self.assertEqual(database.host, "test-db", "Use the disposable test database")
        self.assertEqual(database.database, "freshlink_test")
        self.assertEqual(settings.PROJECT_NAME, "FreshLink-test")
        received = Path("/results/mqtt-receipt.json").read_bytes()
        created = False
        try:
            with Session(engine) as session:
                self.assertIsNone(session.get(Device, DEVICE_ID))
                actor = session.exec(
                    select(User).where(User.email == settings.FIRST_SUPERUSER)
                ).one()
                device = Device(
                    id=DEVICE_ID,
                    code="mqtt-registry-fixture",
                    name="isolated MQTT registry fixture",
                    kind="temperature_sensor",
                )
                session.add(device)
                session.commit()
                created = True
                bind_registered_device(
                    session,
                    actor=actor,
                    device_id=DEVICE_ID,
                    warehouse_id=WAREHOUSE_ID,
                    warehouse_exists=lambda warehouse_id: warehouse_id == WAREHOUSE_ID,
                )
            # A new Session verifies that registration/binding really persisted.
            with Session(engine) as session:
                now = datetime.now(UTC)
                envelope = receive_registered_telemetry(
                    session,
                    authenticated_device_id=DEVICE_ID,
                    credential_enabled=True,
                    warehouse_exists=lambda warehouse_id: warehouse_id == WAREHOUSE_ID,
                    topic=f"freshlink/v1/devices/{DEVICE_ID}/telemetry",
                    payload=received,
                    received_at=now,
                )
                self.assertEqual(envelope.raw_text.encode("utf-8"), received)
                self.assertEqual(envelope.authenticated_device_id, DEVICE_ID)
                self.assertEqual(envelope.warehouse_id, WAREHOUSE_ID)
                self.assertEqual(envelope.received_at, now)
                self.assertEqual(envelope.raw_payload["metrics"][0]["value"], 5)
                self.assertEqual(envelope.raw_payload["metrics"][0]["unit"], "°C")
                self.assertIsNone(session.get(Device, DEVICE_ID).last_seen_at)
        finally:
            if created:
                with Session(engine) as session:
                    record = session.get(Device, DEVICE_ID)
                    if record:
                        session.delete(record)
                        session.commit()


if __name__ == "__main__":
    unittest.main()

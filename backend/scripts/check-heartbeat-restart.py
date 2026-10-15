"""Seed/verify heartbeat checkpoints across a real disposable DB restart.

Refuses the normal application DB. Run only through the isolated test Compose
project; this does not prove MQTT authentication or B notification delivery.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.modules.iot.heartbeat_storage import (
    persist_registered_heartbeat,
    scan_registered_device,
)
from app.modules.iot.models import Device, DeviceConnectivityEvent, HeartbeatMessage


def run() -> None:
    url = urlparse(str(settings.DATABASE_URL))
    if (
        url.hostname != "test-db"
        or url.path != "/freshlink_test"
        or url.username != "freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
    ):
        raise RuntimeError("Refusing non-disposable database")
    target = Path("/results/heartbeat-restart.json")
    moment = datetime(2026, 10, 14, tzinfo=UTC)
    mode = sys.argv[1]

    legacy_target = Path("/results/legacy-device.json")
    if mode == "seed-a01":
        if legacy_target.exists():
            raise RuntimeError("Legacy fixture already exists; do not overwrite")
        identity = uuid4()
        code = "legacy-" + identity.hex
        # Raw SQL intentionally uses only A01 columns, before A02 exists.
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "a01_device_registry"
            )
            connection.execute(
                text(
                    "INSERT INTO iot_device (id, code, name, kind, warehouse_id, is_simulated, created_at, last_seen_at) VALUES (:id, :code, :name, :kind, NULL, true, :at, :at)"
                ),
                {
                    "id": identity,
                    "code": code,
                    "name": "Legacy synthetic device",
                    "kind": "temperature_sensor",
                    "at": moment,
                },
            )
        legacy_target.write_text(
            json.dumps(
                {
                    "device_id": str(identity),
                    "code": code,
                    "timestamp": moment.isoformat(),
                }
            ),
            encoding="utf-8",
        )
        return

    legacy = json.loads(legacy_target.read_text(encoding="utf-8"))
    with Session(engine) as session:
        prior = session.get(Device, UUID(legacy["device_id"]))
        assert prior is not None
        assert prior.code == legacy["code"] and prior.name == "Legacy synthetic device"
        assert prior.kind == "temperature_sensor" and prior.is_simulated is True
        assert prior.created_at == moment and prior.last_seen_at == moment
        assert prior.warehouse_id is None and prior.connectivity == "unknown"
        assert prior.last_sent_at is None and prior.last_heartbeat_message_id is None
        assert prior.offline_source_event_id is None

    def send(identity: UUID, message: UUID, sent: datetime, received: datetime):
        body = json.dumps(
            {
                "schema_version": "1.0",
                "device_id": str(identity),
                "message_id": str(message),
                "sent_at": sent.isoformat(),
            }
        ).encode()
        with Session(engine) as session:
            return persist_registered_heartbeat(
                session,
                authenticated_device_id=identity,
                credential_enabled=True,
                topic=f"freshlink/v1/devices/{identity}/heartbeat",
                payload=body,
                received_at=received,
                retained=False,
            )

    if mode == "seed":
        if target.exists():
            raise RuntimeError("Checkpoint already exists; do not overwrite")
        with Session(engine) as session:
            device = Device(
                code="restart-" + uuid4().hex,
                name="Disposable restart fixture",
                kind="temperature_sensor",
            )
            session.add(device)
            session.commit()
            session.refresh(device)
            identity = device.id
        send(identity, uuid4(), moment, moment)
        with Session(engine) as session:
            scan_registered_device(
                session, device_id=identity, now=moment + timedelta(seconds=90)
            )
        message = uuid4()
        fresh = moment + timedelta(seconds=95)
        send(identity, message, fresh, fresh)
        with Session(engine) as session:
            rows = session.exec(
                select(DeviceConnectivityEvent)
                .where(DeviceConnectivityEvent.device_id == identity)
                .order_by(DeviceConnectivityEvent.occurred_at)
            ).all()
            assert (
                len(rows) == 2
                and rows[1].related_source_event_id == rows[0].source_event_id
            )
        result = {
            "device_id": str(identity),
            "message_id": str(message),
            "last_seen_at": fresh.isoformat(),
            "event_ids": [str(r.source_event_id) for r in rows],
            "phase": "seeded_before_restart",
            "a01_existing_record_preserved_by_a02": True,
            "not_tested": ["MQTT authentication", "B deliveries", "browser", "E1"],
        }
        target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    elif mode == "verify":
        result = json.loads(target.read_text(encoding="utf-8"))
        identity = UUID(result["device_id"])
        message = UUID(result["message_id"])
        fresh = datetime.fromisoformat(result["last_seen_at"])
        with Session(engine) as session:
            device = session.get(Device, identity)
            assert (
                device is not None
                and device.connectivity == "online"
                and device.last_seen_at == fresh
            )
            assert session.get(HeartbeatMessage, (identity, message)) is not None
            rows = session.exec(
                select(DeviceConnectivityEvent)
                .where(DeviceConnectivityEvent.device_id == identity)
                .order_by(DeviceConnectivityEvent.occurred_at)
            ).all()
            assert [str(r.source_event_id) for r in rows] == result["event_ids"]
            assert rows[1].related_source_event_id == rows[0].source_event_id
            assert all(r.delivery_state == "held_unbound" for r in rows)
        assert (
            send(identity, message, fresh, fresh + timedelta(seconds=1)).outcome
            == "duplicate"
        )
        with Session(engine) as session:
            assert session.get(Device, identity).last_seen_at == fresh
            assert (
                len(
                    session.exec(
                        select(DeviceConnectivityEvent).where(
                            DeviceConnectivityEvent.device_id == identity
                        )
                    ).all()
                )
                == 2
            )
        result["phase"] = "verified_after_postgresql_container_and_runner_restart"
        target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    else:
        raise ValueError("Expected seed-a01, seed or verify")


if __name__ == "__main__":
    run()

"""A03 crash/restart proof in owned test-db only, not production MQTT/B."""

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import User
from app.modules.iot.control_models import CommandAttempt, CommandCreate, DeviceCommand
from app.modules.iot.control_storage import (
    claim_dispatch,
    create_command,
    expire_command,
    get_command,
    receive_command_ack,
)
from app.modules.iot.heartbeat_storage import persist_registered_heartbeat
from app.modules.iot.models import Device
from app.modules.iot.simulator import SoftwarePlant


def run() -> None:
    url = urlparse(str(settings.DATABASE_URL))
    if (
        url.hostname != "test-db"
        or url.path != "/freshlink_test"
        or url.username != "freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
    ):
        raise RuntimeError("Refusing non-disposable database")
    directory = Path("/results")
    target = directory / "control-restart.json"
    t0 = datetime(2026, 10, 14, tzinfo=UTC)
    mode = sys.argv[1]
    if mode == "seed":
        if target.exists() or (directory / "control-device.db").exists():
            raise RuntimeError("Refusing to overwrite prior evidence")
        actor = User(
            email=f"restart-{uuid4().hex}@example.com",
            hashed_password="fixture-unused",
            is_superuser=True,
        )
        warehouse, sensor = uuid4(), uuid4()
        with Session(engine) as s:
            device = Device(
                code="restart-control-" + uuid4().hex,
                name="Synthetic control restart",
                kind="cooling_actuator",
                warehouse_id=warehouse,
            )
            s.add(actor)
            s.add(device)
            s.commit()
            s.refresh(actor)
            s.refresh(device)
            actor_id, identity = actor.id, device.id
            persist_registered_heartbeat(
                s,
                authenticated_device_id=identity,
                credential_enabled=True,
                topic=f"freshlink/v1/devices/{identity}/heartbeat",
                payload=json.dumps(
                    {
                        "schema_version": "1.0",
                        "device_id": str(identity),
                        "message_id": str(uuid4()),
                        "sent_at": t0.isoformat(),
                    }
                ).encode(),
                received_at=t0,
                retained=False,
            )
            actor = s.get(User, actor_id)
            command = create_command(
                s,
                actor=actor,
                device_id=identity,
                request=CommandCreate(command_id=uuid4(), enabled=True),
                now=t0,
                warehouse_exists=lambda checked: checked == warehouse,
            )
            command_id = command.command_id
            dispatch = claim_dispatch(
                s,
                command_id=command_id,
                now=t0,
                warehouse_exists=lambda checked: checked == warehouse,
            )
        assert dispatch is not None
        plant = SoftwarePlant(
            directory / "control-device.db",
            actuator_id=identity,
            sensor_id=sensor,
            warehouse_id=warehouse,
            started_at=t0,
        )
        device_ack = plant.execute(
            dispatch.payload, topic=dispatch.topic, now=t0 + timedelta(seconds=1)
        )
        result = {
            "phase": "seeded_in_flight_before_restart",
            "command_id": str(command_id),
            "actor_id": str(actor_id),
            "device_id": str(identity),
            "warehouse_id": str(warehouse),
            "sensor_id": str(sensor),
            "payload_sha256": hashlib.sha256(dispatch.payload).hexdigest(),
            "device_ack": device_ack,
            "first_attempt_id": str(dispatch.attempt_id),
            "warehouse_lookup": "explicit synthetic fixture, not B",
        }
        target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    elif mode == "verify":
        result = json.loads(target.read_text(encoding="utf-8"))
        command_id, identity, warehouse, sensor = (
            UUID(result[key])
            for key in ("command_id", "device_id", "warehouse_id", "sensor_id")
        )
        with Session(engine) as s:
            assert s.get(DeviceCommand, command_id).status == "queued"
            assert (
                s.get(CommandAttempt, UUID(result["first_attempt_id"])).outcome
                == "pending"
            )
            assert (
                claim_dispatch(
                    s,
                    command_id=command_id,
                    now=t0 + timedelta(seconds=29),
                    warehouse_exists=lambda checked: checked == warehouse,
                )
                is None
            )
            dispatch = claim_dispatch(
                s,
                command_id=command_id,
                now=t0 + timedelta(seconds=30),
                warehouse_exists=lambda checked: checked == warehouse,
            )
        assert (
            dispatch is not None
            and hashlib.sha256(dispatch.payload).hexdigest() == result["payload_sha256"]
        )
        plant = SoftwarePlant(
            directory / "control-device.db",
            actuator_id=identity,
            sensor_id=sensor,
            warehouse_id=warehouse,
            started_at=t0,
        )
        original_ack = plant.execute(
            dispatch.payload, topic=dispatch.topic, now=t0 + timedelta(seconds=31)
        )
        assert original_ack == result["device_ack"]
        with plant._connection() as connection:
            assert (
                connection.execute(
                    "SELECT executions FROM plant WHERE id=1"
                ).fetchone()[0]
                == 1
            )
        with Session(engine) as s:
            arguments = {
                "authenticated_device_id": identity,
                "credential_enabled": True,
                "topic": f"freshlink/v1/devices/{identity}/acks",
                "payload": json.dumps(original_ack).encode(),
                "received_at": t0 + timedelta(seconds=31),
                "retained": False,
            }
            assert receive_command_ack(s, **arguments) == "accepted"
            assert receive_command_ack(s, **arguments) == "duplicate"
            assert get_command(s, command_id).status == "acknowledged"
            actor = s.get(User, UUID(result["actor_id"]))
            late_command = create_command(
                s,
                actor=actor,
                device_id=identity,
                request=CommandCreate(
                    command_id=uuid4(), enabled=False, lifetime_seconds=5
                ),
                now=t0 + timedelta(seconds=32),
                warehouse_exists=lambda checked: checked == warehouse,
            )
            late_id = late_command.command_id
            late_dispatch = claim_dispatch(
                s,
                command_id=late_id,
                now=t0 + timedelta(seconds=32),
                warehouse_exists=lambda checked: checked == warehouse,
            )
        late_ack = plant.execute(
            late_dispatch.payload,
            topic=late_dispatch.topic,
            now=t0 + timedelta(seconds=33),
        )
        with Session(engine) as s:
            assert (
                expire_command(
                    s, command_id=late_id, now=t0 + timedelta(seconds=37)
                ).status
                == "timeout"
            )
            assert (
                receive_command_ack(
                    s,
                    authenticated_device_id=identity,
                    credential_enabled=True,
                    topic=f"freshlink/v1/devices/{identity}/acks",
                    payload=json.dumps(late_ack).encode(),
                    received_at=t0 + timedelta(seconds=38),
                    retained=False,
                )
                == "late"
            )
            detail = get_command(s, late_id)
            assert detail.status == "timeout" and detail.receipts[0].outcome == "late"
            assert (
                len(
                    s.exec(
                        select(CommandAttempt).where(
                            CommandAttempt.command_id == command_id
                        )
                    ).all()
                )
                == 2
            )
        result["phase"] = "verified_after_postgresql_and_runner_restart"
        result["same_payload_after_lease_recovery"] = True
        result["software_device_executions_after_replay"] = 1
        result["late_ack_preserved_timeout"] = True
        result["not_tested"] = [
            "B warehouse integration",
            "MQTT server command worker",
            "browser",
            "production database",
            "E1",
        ]
        target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    else:
        raise ValueError("Expected seed or verify")


if __name__ == "__main__":
    run()

"""Explicit control worker CLI; never starts from the HTTP app or migrates data."""

import argparse
import math
import os
import signal
from pathlib import Path
from uuid import UUID

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from app.modules.iot.control_worker import ControlWorker, ControlWorkerSettings
from app.modules.iot.models import Device
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session


def read_control_settings(path: Path) -> ControlWorkerSettings:
    """Bounded read, fixed errors: never echo an accidentally embedded secret."""
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("invalid config file")
        with path.open("rb") as stream:
            payload = stream.read(65537)
        if len(payload) > 65536:
            raise ValueError("config too large")
        return ControlWorkerSettings.model_validate_json(payload)
    except Exception:
        raise ValueError("control_config_invalid") from None


def verify_control_scope(engine: Engine, device_ids: list[UUID]) -> None:
    """Read-only startup check; no schema creation, binding or online claims.

    This is not a whole-release migration certificate: it checks the actual
    minimum control tables and B warehouses. Runtime services still revalidate
    individual commands in their normal transaction after this preflight.
    """
    if not 1 <= len(device_ids) <= 14 or len(set(device_ids)) != len(device_ids):
        raise ValueError("control_scope_invalid")
    if not all(isinstance(identity, UUID) for identity in device_ids):
        raise ValueError("control_scope_invalid")
    if engine.dialect.name != "postgresql":
        raise ValueError("control_postgresql_required")
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        required = {
            "iot_device",
            "iot_command",
            "iot_command_attempt",
            "iot_command_receipt",
            "alerts_warehouse",
        }
        if not required <= set(inspect(connection).get_table_names()):
            raise ValueError("control_schema_missing")
        with Session(bind=connection) as session:
            devices = session.exec(
                select(Device).where(col(Device.id).in_(device_ids))
            ).all()
            if len(devices) != len(device_ids):
                raise ValueError("control_device_unknown")
            exists = warehouse_lookup_for_session(session)
            for device in devices:
                if device.kind != "cooling_actuator" or device.warehouse_id is None:
                    raise ValueError("control_actuator_binding_required")
                if exists(device.warehouse_id) is not True:
                    raise ValueError("control_warehouse_unknown")


def build_control_worker(
    settings: ControlWorkerSettings, password: str
) -> ControlWorker:
    if not password:
        raise ValueError("control_mqtt_credential_required")
    worker = ControlWorker(
        settings,
        password=password,
        warehouse_lookup_factory=warehouse_lookup_for_session,
    )
    try:
        verify_control_scope(worker.engine, settings.device_ids)
    except Exception:
        worker.engine.dispose()
        raise RuntimeError("control_startup_preflight_failed") from None
    return worker


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Explicit registered-actuator worker")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float)
    args = parser.parse_args(argv)
    if args.max_seconds is not None and (
        not math.isfinite(args.max_seconds) or args.max_seconds <= 0
    ):
        raise SystemExit("control_duration_invalid")
    worker = None
    try:
        settings = read_control_settings(args.config)
        worker = build_control_worker(
            settings, os.environ.get("FRESHLINK_CONTROL_MQTT_PASSWORD", "")
        )
        signal.signal(signal.SIGINT, lambda _s, _f: worker.stop_requested.set())
        signal.signal(signal.SIGTERM, lambda _s, _f: worker.stop_requested.set())
        worker.run(max_seconds=args.max_seconds)
    except Exception:
        raise SystemExit("control_runtime_failed") from None
    finally:
        if worker is not None:
            worker.engine.dispose()


if __name__ == "__main__":
    main()

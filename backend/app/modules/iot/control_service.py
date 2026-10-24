"""Explicit process entry for the existing control worker; no DDL or startup."""

import argparse
import math
import os
import signal
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from app.modules.iot.control_worker import ControlWorker, ControlWorkerSettings
from app.modules.iot.models import Device
from app.modules.iot.warehouse_bridge import warehouse_lookup_for_session


def verify_control_scope(engine: Engine, config: ControlWorkerSettings) -> None:
    """Read only; reject unknown/unbound sensors and phantom warehouses."""
    with Session(engine) as session:
        devices = session.exec(
            select(Device).where(col(Device.id).in_(config.device_ids))
        ).all()
        exists = warehouse_lookup_for_session(session)
        if len(devices) != len(config.device_ids) or any(
            device.kind != "cooling_actuator"
            or device.warehouse_id is None
            or not exists(device.warehouse_id)
            for device in devices
        ):
            raise ValueError("explicit registered actuator/warehouse scope required")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float)
    args = parser.parse_args()
    if args.max_seconds is not None and (
        not math.isfinite(args.max_seconds) or args.max_seconds <= 0
    ):
        parser.error("max-seconds must be finite and positive")
    worker = None
    previous = {}
    try:
        if args.config.stat().st_size > 65536:
            raise ValueError("config too large")
        config = ControlWorkerSettings.model_validate_json(args.config.read_bytes())
        password = os.environ.get("FRESHLINK_CONTROL_MQTT_PASSWORD", "")
        if not password:
            raise ValueError("controller account password required")
        worker = ControlWorker(
            config,
            password=password,
            warehouse_lookup_factory=warehouse_lookup_for_session,
        )
        verify_control_scope(worker.engine, config)

        def stop(_signum: int, _frame: object) -> None:
            assert worker is not None
            worker.stop_requested.set()

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, stop)
        worker.run(max_seconds=args.max_seconds)
    except Exception:
        # Pydantic and broker exceptions can embed config/URLs: never echo.
        raise SystemExit(
            "control service failed; check private configuration and scope"
        ) from None
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if worker is not None:
            worker.engine.dispose()


if __name__ == "__main__":
    main()

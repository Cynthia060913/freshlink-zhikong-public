"""Bounded supervisor for 1-7 independent software-plant zones (2-14 devices).

Reuses the actual durable device runtime; does not register devices, migrate
application tables, start server workers, or send external notifications.
"""

import argparse
import math
import os
import signal
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.iot.simulator_runtime import DeviceRuntime, RuntimeSettings


class FleetSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zones: list[RuntimeSettings] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def disjoint_scopes(self) -> FleetSettings:
        identities = [
            value for zone in self.zones for value in (zone.sensor_id, zone.actuator_id)
        ]
        usernames = [
            value
            for zone in self.zones
            for value in (zone.sensor_username, zone.actuator_username)
        ]
        paths = [str(zone.state_file.resolve()).casefold() for zone in self.zones]
        parents = {zone.state_file.resolve().parent for zone in self.zones}
        if len(set(identities)) != len(identities) or len(set(usernames)) != len(
            usernames
        ):
            raise ValueError(
                "fleet device IDs and account names must be globally distinct"
            )
        if len(set(paths)) != len(paths) or len(parents) != 1:
            raise ValueError(
                "fleet state files need distinct names in one dedicated directory"
            )
        parent = next(iter(parents))
        if parent in {Path(parent.anchor), Path.home(), Path.cwd()}:
            raise ValueError("refusing a broad fleet state directory")
        if any(
            zone.state_file.suffix.lower() != ".db"
            or zone.state_file.name.startswith(".")
            for zone in self.zones
        ):
            raise ValueError("fleet states must be named .db files")
        if len({zone.warehouse_id for zone in self.zones}) > 2:
            raise ValueError("v4 fleet supports at most two warehouses")
        if (
            len({(zone.host, zone.port, zone.tls, zone.ca_file) for zone in self.zones})
            != 1
        ):
            raise ValueError(
                "fleet zones must share the selected broker and TLS policy"
            )
        return self


class FleetRuntimeError(RuntimeError):
    """Fixed, credential-free diagnostics."""


class ManagedRuntime(Protocol):
    stop_requested: threading.Event
    fatal_reason: str | None

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def step(self, *, now: datetime, monotonic: float) -> None: ...


def password_environment_name(device_id: UUID) -> str:
    return "FRESHLINK_SIM_" + device_id.hex.upper() + "_PASSWORD"


def _verify_existing_state(zone: RuntimeSettings) -> None:
    path = zone.state_file
    if path.is_symlink():
        raise FleetRuntimeError("fleet_state_not_regular")
    if not path.exists():
        return
    if not path.is_file():
        raise FleetRuntimeError("fleet_state_not_regular")
    try:
        # The read-only URI prevents even accidental creation/DDL in an unrelated
        # existing SQLite file before SoftwarePlant's normal additive upgrade.
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            row = db.execute(
                "SELECT actuator,sensor,warehouse FROM plant WHERE id=1"
            ).fetchone()
        if tables != {"plant", "commands", "observations", "runtime_audit"} or row != (
            str(zone.actuator_id),
            str(zone.sensor_id),
            str(zone.warehouse_id),
        ):
            raise FleetRuntimeError("fleet_state_identity_mismatch")
    except sqlite3.Error:
        raise FleetRuntimeError("fleet_state_not_owned") from None


class SimulatorFleet:
    def __init__(
        self,
        settings: FleetSettings,
        *,
        password_for: Callable[[UUID], str],
        factory: Callable[..., ManagedRuntime] = DeviceRuntime,
    ) -> None:
        selected = FleetSettings.model_validate(settings.model_dump()).zones
        # Check ALL credentials and existing files before creating any plant or
        # opening a network connection. Never print the supplied secret value.
        secrets = {}
        for zone in selected:
            for identity in (zone.sensor_id, zone.actuator_id):
                try:
                    value = password_for(identity)
                except Exception:
                    raise FleetRuntimeError(
                        "fleet_credential_provider_failed"
                    ) from None
                if not isinstance(value, str) or not value or len(value) > 4096:
                    raise FleetRuntimeError("fleet_credentials_missing")
                secrets[identity] = value
            _verify_existing_state(zone)
        for zone in selected:
            zone.state_file.parent.mkdir(parents=True, exist_ok=True)
        created: list[ManagedRuntime] = []
        try:
            for zone in selected:
                created.append(
                    factory(
                        zone,
                        sensor_password=secrets[zone.sensor_id],
                        actuator_password=secrets[zone.actuator_id],
                    )
                )
        except Exception:
            cleanup_failed = False
            for runtime in reversed(created):
                try:
                    runtime.stop()
                except Exception:
                    cleanup_failed = True
            raise FleetRuntimeError(
                "fleet_initialization_cleanup_failed"
                if cleanup_failed
                else "fleet_initialization_failed"
            ) from None
        self.runtimes = tuple(created)
        self.stop_requested = threading.Event()

    def run(self, *, max_seconds: float | None = None) -> None:
        if max_seconds is not None and (
            not math.isfinite(max_seconds) or not 0 < max_seconds <= 3600
        ):
            raise ValueError("fleet max_seconds must be positive and <=3600")
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        failed = False
        cleanup_failed = False
        try:
            for runtime in self.runtimes:
                if self.stop_requested.is_set():
                    break
                runtime.start()
            while not self.stop_requested.is_set() and (
                deadline is None or time.monotonic() < deadline
            ):
                for runtime in self.runtimes:
                    if runtime.fatal_reason or runtime.stop_requested.is_set():
                        raise FleetRuntimeError("fleet_device_failed")
                    runtime.step(now=datetime.now(UTC), monotonic=time.monotonic())
                self.stop_requested.wait(0.05)
        except Exception:
            failed = True
        finally:
            self.stop_requested.set()
            # One owner runs/steps/stops all zones; callbacks only queue work.
            # Partial startup or one failed stop must not leave siblings running.
            for runtime in reversed(self.runtimes):
                try:
                    runtime.stop()
                except Exception:
                    cleanup_failed = True
        if failed or cleanup_failed:
            raise FleetRuntimeError(
                "fleet_shutdown_failed" if cleanup_failed else "fleet_device_failed"
            ) from None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a v4 software-device fleet; no application startup"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float)
    arguments = parser.parse_args()
    if arguments.max_seconds is not None and (
        not math.isfinite(arguments.max_seconds)
        or not 0 < arguments.max_seconds <= 3600
    ):
        parser.error("max-seconds must be positive and <=3600")
    try:
        settings = FleetSettings.model_validate_json(arguments.config.read_bytes())
    except ValueError, OSError:
        parser.error("invalid or unreadable fleet configuration; values are not logged")
    fleet = SimulatorFleet(
        settings,
        password_for=lambda identity: os.environ.get(
            password_environment_name(identity), ""
        ),
    )
    signal.signal(signal.SIGINT, lambda *_: fleet.stop_requested.set())
    signal.signal(signal.SIGTERM, lambda *_: fleet.stop_requested.set())
    fleet.run(max_seconds=arguments.max_seconds)


if __name__ == "__main__":
    main()

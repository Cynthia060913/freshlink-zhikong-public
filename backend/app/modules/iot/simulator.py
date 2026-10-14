"""A-owned software plant, not a production listener or shared database model.

SQLite belongs only to the simulator. Execution, plant state and original ACK
commit atomically; broker receipt never clears the observation outbox. Thermal
parameters are engineering fixtures, not measured apple quality/energy claims.
"""

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, StrictBool, field_validator


class SimulatorRejected(ValueError):
    """Stable diagnostics; no credential or untrusted payload in the message."""


class CoolingCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    command_id: UUID
    device_id: UUID
    warehouse_id: UUID
    action: Literal["set_cooling"]
    parameters: dict[Literal["enabled"], StrictBool]
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @field_validator("parameters")
    @classmethod
    def exact_parameters(
        cls, value: dict[Literal["enabled"], bool]
    ) -> dict[Literal["enabled"], bool]:
        if set(value) != {"enabled"}:
            raise ValueError("enabled is required")
        return value

    @field_validator("issued_at", "expires_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


@dataclass(frozen=True)
class PlantSample:
    payload_text: str
    power_w: float
    energy_kwh: float

    @property
    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.payload_text)
        return result


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SimulatorRejected("clock_requires_timezone")
    return value.astimezone(UTC)


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SimulatorRejected("duplicate_json_member")
        result[key] = value
    return result


class SoftwarePlant:
    """One serialized local simulator; identity is immutable across restarts.

    A small cooling actuator changes the paired sensor's future temperature.
    The active interval uses 200 W baseline plus 800 W while cooling. The simple
    thermal equation is dT/dt = .002*(22-T)-.04*cooling; it is NOT calibration.
    Limit each integration interval to 60 seconds: do not silently fast-forward
    a stopped simulator. Command records are not evicted (replay safety); capacity
    exhaustion requires explicit operator rotation, not lost deduplication.
    """

    def __init__(
        self,
        database: Path,
        *,
        actuator_id: UUID,
        sensor_id: UUID,
        warehouse_id: UUID,
        started_at: datetime,
        initial_temperature_c: float = 10.0,
        max_pending: int = 1000,
        max_commands: int = 10000,
    ) -> None:
        if (
            not math.isfinite(initial_temperature_c)
            or not -50 <= initial_temperature_c <= 80
        ):
            raise SimulatorRejected("invalid_initial_temperature")
        if not 1 <= max_pending <= 1000 or not 1 <= max_commands <= 10000:
            raise SimulatorRejected("invalid_capacity")
        if actuator_id == sensor_id:
            raise SimulatorRejected("sensor_and_actuator_must_differ")
        self.database = database
        self.actuator_id = actuator_id
        self.sensor_id = sensor_id
        self.warehouse_id = warehouse_id
        self.max_pending = max_pending
        self.max_commands = max_commands
        start = _utc(started_at).isoformat()
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS plant (
                    id INTEGER PRIMARY KEY CHECK (id=1), actuator TEXT NOT NULL,
                    sensor TEXT NOT NULL, warehouse TEXT NOT NULL,
                    temperature REAL NOT NULL, energy REAL NOT NULL,
                    cooling INTEGER NOT NULL, executions INTEGER NOT NULL,
                    stepped_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, ack TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, power REAL NOT NULL,
                    energy REAL NOT NULL, stored INTEGER NOT NULL DEFAULT 0,
                    sequence INTEGER NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS runtime_audit (
                    id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL,
                    kind TEXT NOT NULL, detail TEXT NOT NULL
                );
            """)
            # executescript commits its DDL; reacquire the write lock before the
            # additive device-state upgrade. Never migrate shared application DB.
            connection.execute("BEGIN IMMEDIATE")
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(commands)")
            }
            if "ack_transported" not in columns:
                connection.execute(
                    "ALTER TABLE commands ADD COLUMN ack_transported INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                "INSERT OR IGNORE INTO plant VALUES (1,?,?,?,?,0,0,0,?)",
                (
                    str(actuator_id),
                    str(sensor_id),
                    str(warehouse_id),
                    initial_temperature_c,
                    start,
                ),
            )
            row = connection.execute(
                "SELECT actuator,sensor,warehouse FROM plant WHERE id=1"
            ).fetchone()
            if row != (str(actuator_id), str(sensor_id), str(warehouse_id)):
                raise SimulatorRejected("state_identity_mismatch")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _advance(
        connection: sqlite3.Connection, now: datetime
    ) -> tuple[float, float, float]:
        temperature, energy, cooling, prior = connection.execute(
            "SELECT temperature,energy,cooling,stepped_at FROM plant WHERE id=1"
        ).fetchone()
        seconds = (now - datetime.fromisoformat(prior)).total_seconds()
        if not 0 <= seconds <= 60:
            raise SimulatorRejected("invalid_step_interval")
        # Exact solution of the linear fixture model, independent of step size.
        equilibrium = 22.0 - (20.0 if cooling else 0.0)
        temperature = equilibrium + (temperature - equilibrium) * math.exp(
            -0.002 * seconds
        )
        power = 1000.0 if cooling else 200.0
        energy += power * seconds / 3_600_000
        connection.execute(
            "UPDATE plant SET temperature=?,energy=?,stepped_at=? WHERE id=1",
            (temperature, energy, now.isoformat()),
        )
        return float(temperature), float(power), float(energy)

    def execute(
        self,
        payload: bytes,
        *,
        topic: str,
        now: datetime,
        retained: bool = False,
        fail_switch: bool = False,
    ) -> dict[str, Any]:
        """Consume only a trusted transport's own unretained command topic.

        This is not user/broker authentication. A future authenticated publisher
        and broker ACL must guarantee the caller's authority. A duplicate returns
        its original persisted ACK even after expiry; it does not re-execute.
        """
        current = _utc(now)
        if retained or topic != f"freshlink/v1/devices/{self.actuator_id}/commands":
            raise SimulatorRejected("invalid_command_transport")
        if len(payload) > 4096:
            raise SimulatorRejected("command_too_large")
        try:
            raw = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_members)
            command = CoolingCommand.model_validate(raw)
        except ValueError, RecursionError:
            raise SimulatorRejected("invalid_command_payload") from None
        if (
            command.device_id != self.actuator_id
            or command.warehouse_id != self.warehouse_id
        ):
            raise SimulatorRejected("command_identity_mismatch")
        if not 0 < (command.expires_at - command.issued_at).total_seconds() <= 300:
            raise SimulatorRejected("invalid_command_lifetime")
        canonical = json.dumps(
            command.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        with self._connection() as connection:
            prior = connection.execute(
                "SELECT fingerprint,ack FROM commands WHERE id=?",
                (str(command.command_id),),
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise SimulatorRejected("command_content_conflict")
                result: dict[str, Any] = json.loads(prior[1])
                connection.execute(
                    "UPDATE commands SET ack_transported=0 WHERE id=?",
                    (str(command.command_id),),
                )
                return result
            count = connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
            if count >= self.max_commands:
                raise SimulatorRejected("command_capacity_reached")
            if current < command.issued_at:
                raise SimulatorRejected("command_not_yet_valid")
            reason = (
                "expired"
                if current >= command.expires_at
                else ("simulated_switch_failure" if fail_switch else None)
            )
            if reason is None:
                self._advance(connection, current)
                connection.execute(
                    "UPDATE plant SET cooling=?,executions=executions+1 WHERE id=1",
                    (int(command.parameters["enabled"]),),
                )
            cooling = connection.execute(
                "SELECT cooling FROM plant WHERE id=1"
            ).fetchone()[0]
            ack = {
                "schema_version": "1.0",
                "command_id": str(command.command_id),
                "device_id": str(self.actuator_id),
                "executed_at": current.isoformat() if reason is None else None,
                "result": "executed" if reason is None else "failed",
                "state": {"cooling_enabled": bool(cooling)},
                "reason": reason,
            }
            connection.execute(
                "INSERT INTO commands (id,fingerprint,ack) VALUES (?,?,?)",
                (str(command.command_id), fingerprint, json.dumps(ack)),
            )
            return ack

    def pending_acknowledgements(self) -> list[tuple[UUID, str]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id,ack FROM commands WHERE ack_transported=0 ORDER BY rowid LIMIT 32"
            ).fetchall()
            return [(UUID(identifier), text) for identifier, text in rows]

    def confirm_ack_transport(self, command_id: UUID) -> None:
        """Broker transport receipt ONLY; does not assert server ACK persistence."""
        with self._connection() as connection:
            changed = connection.execute(
                "UPDATE commands SET ack_transported=1 WHERE id=?", (str(command_id),)
            ).rowcount
            if changed == 0:
                raise SimulatorRejected("command_not_found")

    @staticmethod
    def _audit(
        connection: sqlite3.Connection, kind: str, now: datetime, detail: dict[str, Any]
    ) -> None:
        connection.execute(
            "INSERT INTO runtime_audit VALUES (?,?,?,?)",
            (str(uuid4()), now.isoformat(), kind, json.dumps(detail)),
        )
        connection.execute(
            "DELETE FROM runtime_audit WHERE rowid NOT IN (SELECT rowid FROM runtime_audit ORDER BY rowid DESC LIMIT 1000)"
        )

    def audit_rejection(
        self, payload: bytes, *, now: datetime, reason_code: str
    ) -> None:
        if (
            not reason_code
            or len(reason_code) > 64
            or any(c not in "abcdefghijklmnopqrstuvwxyz_" for c in reason_code)
        ):
            raise SimulatorRejected("invalid_audit_reason")
        with self._connection() as connection:
            self._audit(
                connection,
                "command_rejected",
                _utc(now),
                {
                    "reason_code": reason_code,
                    "payload_bytes": len(payload),
                    "payload_sha256": hashlib.sha256(payload).hexdigest(),
                },
            )

    def resume_after_gap(self, now: datetime) -> bool:
        """Explicit startup recovery: record missing interval, no invented samples.

        For a >60s gap, freeze simulated temperature/energy and switch cooling
        off, requiring a new command. Keep old ACKs and all pending observations.
        """
        current = _utc(now)
        with self._connection() as connection:
            prior = connection.execute(
                "SELECT stepped_at FROM plant WHERE id=1"
            ).fetchone()[0]
            seconds = (current - datetime.fromisoformat(prior)).total_seconds()
            if seconds < 0:
                raise SimulatorRejected("clock_moved_backwards")
            if seconds <= 60:
                return False
            self._audit(
                connection,
                "restart_gap",
                current,
                {
                    "from": prior,
                    "to": current.isoformat(),
                    "missing_seconds": seconds,
                    "cooling_disabled": True,
                },
            )
            connection.execute(
                "UPDATE plant SET cooling=0,stepped_at=? WHERE id=1",
                (current.isoformat(),),
            )
            return True

    def audit_records(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT occurred_at,kind,detail FROM runtime_audit ORDER BY rowid"
            ).fetchall()
            return [
                {"occurred_at": occurred, "kind": kind, "detail": json.loads(detail)}
                for occurred, kind, detail in rows
            ]

    def sample(self, now: datetime) -> PlantSample:
        """Commit sample and outbox bytes together; full queue changes no state."""
        current = _utc(now)
        with self._connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM observations WHERE stored=0"
            ).fetchone()[0]
            if count >= self.max_pending:
                raise SimulatorRejected("observation_queue_full")
            prior = connection.execute(
                "SELECT stepped_at FROM plant WHERE id=1"
            ).fetchone()[0]
            if current <= datetime.fromisoformat(prior):
                raise SimulatorRejected("sample_time_must_advance")
            temperature, power, energy = self._advance(connection, current)
            identifier = str(uuid4())
            payload = {
                "schema_version": "1.0",
                "device_id": str(self.sensor_id),
                "warehouse_id": str(self.warehouse_id),
                "message_id": identifier,
                "sampled_at": current.isoformat(),
                "metrics": [
                    {
                        "key": "temperature",
                        "value": round(temperature, 6),
                        "unit": "°C",
                    },
                    {"key": "humidity", "value": 65.0, "unit": "%RH"},
                ],
            }
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM observations"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO observations VALUES (?,?,?,?,0,?)",
                (identifier, text, power, energy, sequence),
            )
            return PlantSample(text, power, energy)

    def pending(self) -> list[PlantSample]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload,power,energy FROM observations WHERE stored=0 ORDER BY sequence"
            ).fetchall()
            return [PlantSample(*row) for row in rows]

    def confirm_storage(self, message_id: UUID, *, outcome: str) -> None:
        """Call ONLY after trusted C durable acceptance, never from PUBACK."""
        if outcome not in {"accepted_new", "accepted_duplicate"}:
            raise SimulatorRejected("storage_not_confirmed")
        with self._connection() as connection:
            changed = connection.execute(
                "UPDATE observations SET stored=1 WHERE id=?", (str(message_id),)
            ).rowcount
            if changed == 0:
                raise SimulatorRejected("observation_not_found")
            # Retain bounded local accepted history; never remove pending bytes.
            connection.execute(
                "DELETE FROM observations WHERE stored=1 AND sequence NOT IN (SELECT sequence FROM observations WHERE stored=1 ORDER BY sequence DESC LIMIT 1000)"
            )

    def state(self) -> dict[str, Any]:
        with self._connection() as connection:
            temperature, energy, cooling, executions, stepped = connection.execute(
                "SELECT temperature,energy,cooling,executions,stepped_at FROM plant WHERE id=1"
            ).fetchone()
            return {
                "temperature_c": temperature,
                "energy_kwh": energy,
                "cooling_enabled": bool(cooling),
                "execution_count": executions,
                "stepped_at": stepped,
            }

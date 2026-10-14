"""Local transport test helper; no production account, MQTT client or app DB."""

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from app.modules.iot.simulator import SoftwarePlant

ACTUATOR = UUID("66666666-6666-4666-8666-666666666666")
SENSOR = UUID("11111111-1111-4111-8111-111111111111")
WAREHOUSE = UUID("22222222-2222-4222-8222-222222222222")


def run(mode: str, directory: Path) -> None:
    database = directory / "simulator.db"
    now = datetime.now(UTC)
    if mode == "prepare" and database.exists():
        raise RuntimeError("Test directory must be new; refusing to overwrite state")
    if mode != "prepare" and not database.exists():
        raise RuntimeError("Prepared state is required")
    plant = SoftwarePlant(
        database,
        actuator_id=ACTUATOR,
        sensor_id=SENSOR,
        warehouse_id=WAREHOUSE,
        started_at=now - timedelta(seconds=1),
    )
    if mode == "prepare":
        sample = plant.sample(now)
        (directory / "before.json").write_text(sample.payload_text, encoding="utf-8")
        (directory / "before-power.json").write_text(
            json.dumps({"power_w": sample.power_w, "energy_kwh": sample.energy_kwh}),
            encoding="utf-8",
        )
        command = {
            "schema_version": "1.0",
            "command_id": str(uuid4()),
            "device_id": str(ACTUATOR),
            "warehouse_id": str(WAREHOUSE),
            "action": "set_cooling",
            "parameters": {"enabled": True},
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=300)).isoformat(),
        }
        (directory / "command.json").write_text(json.dumps(command), encoding="utf-8")
    elif mode in {"execute", "duplicate"}:
        if mode == "execute":
            before_execution = plant.sample(now)
            (directory / "control-before.json").write_text(
                before_execution.payload_text, encoding="utf-8"
            )
        ack = plant.execute(
            (directory / "command-receipt.json").read_bytes(),
            topic=f"freshlink/v1/devices/{ACTUATOR}/commands",
            now=now,
        )
        filename = "ack.json" if mode == "execute" else "duplicate-ack.json"
        (directory / filename).write_text(
            json.dumps(ack, sort_keys=True), encoding="utf-8"
        )
        if mode == "execute":
            time.sleep(1)
            sample = plant.sample(datetime.now(UTC))
            (directory / "after.json").write_text(sample.payload_text, encoding="utf-8")
            (directory / "after-power.json").write_text(
                json.dumps(
                    {"power_w": sample.power_w, "energy_kwh": sample.energy_kwh}
                ),
                encoding="utf-8",
            )
    elif mode == "verify":
        before = json.loads(
            (directory / "control-before.json").read_text(encoding="utf-8")
        )
        after = json.loads(
            (directory / "telemetry-receipt.json").read_text(encoding="utf-8")
        )
        before_power = json.loads(
            (directory / "before-power.json").read_text(encoding="utf-8")
        )
        after_power = json.loads(
            (directory / "after-power.json").read_text(encoding="utf-8")
        )
        assert (directory / "command.json").read_bytes() == (
            directory / "command-receipt.json"
        ).read_bytes()
        assert (directory / "ack.json").read_bytes() == (
            directory / "ack-receipt.json"
        ).read_bytes()
        assert (directory / "duplicate-ack.json").read_bytes() == (
            directory / "ack.json"
        ).read_bytes()
        assert (directory / "after.json").read_bytes() == (
            directory / "telemetry-receipt.json"
        ).read_bytes()
        assert (
            json.loads((directory / "ack-receipt.json").read_text())["result"]
            == "executed"
        )
        assert plant.state()["execution_count"] == 1
        assert len(plant.pending()) == 3  # PUBACK did NOT confirm C persistence.
        assert after["metrics"][0]["value"] < before["metrics"][0]["value"]
        assert before_power["power_w"] == 200 and after_power["power_w"] == 1000
        assert after_power["energy_kwh"] > before_power["energy_kwh"]
        result = {
            "status": "passed",
            "evidence_type": "isolated_mqtt_file_bridge_real_execution",
            "command_id": json.loads((directory / "command.json").read_text())[
                "command_id"
            ],
            "before_temperature_c": before["metrics"][0]["value"],
            "after_temperature_c": after["metrics"][0]["value"],
            "before_power_w": before_power["power_w"],
            "after_power_w": after_power["power_w"],
            "execution_count": 1,
            "pending_observations": 3,
            "process_restart_deduplication": True,
            "not_checked": [
                "continuous_listener",
                "production_authentication",
                "public_control_api",
                "C_storage",
                "B_alerts",
                "E1",
            ],
        }
        (directory / "result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "execute", "duplicate", "verify"])
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    run(arguments.mode, arguments.directory)

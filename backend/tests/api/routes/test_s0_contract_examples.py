"""Executable A->C proposal vectors; no B/C service acceptance is asserted."""

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.modules.iot.ingress import DeviceContext, IngressRejected, receive_telemetry

PACKET = json.loads(
    (Path(__file__).parent / "fixtures" / "s0-telemetry-rc1.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", PACKET["cases"], ids=lambda case: case["id"])
def test_s0_telemetry_vectors(case: dict) -> None:
    trusted = PACKET["trusted_context"]
    context = DeviceContext(UUID(trusted["device_id"]), UUID(trusted["warehouse_id"]))
    received_at = datetime.fromisoformat(trusted["received_at"])
    payload = case["raw_text"].encode("utf-8")
    args = {
        "topic": trusted["topic"],
        "payload": payload,
        "context": context,
        "received_at": received_at,
    }
    if case["a_outcome"] == "reject":
        with pytest.raises(IngressRejected) as error:
            receive_telemetry(**args)
        assert error.value.reason_code == case["reason_code"]
    else:
        envelope = receive_telemetry(**args)
        assert envelope.raw_text.encode("utf-8") == payload
        assert envelope.raw_payload == json.loads(case["raw_text"])
        assert envelope.authenticated_device_id == context.device_id
        assert envelope.warehouse_id == context.warehouse_id
        assert envelope.received_at == received_at


def test_s0_vectors_remain_an_unsigned_proposal() -> None:
    assert PACKET["contract_version"] == "0.2.0-rc1"
    assert PACKET["status"] == "proposal_pending_owner_confirmation"
    case_ids = [case["id"] for case in PACKET["cases"]]
    assert len(case_ids) == len(set(case_ids))
    assert {case["a_outcome"] for case in PACKET["cases"]} == {"forward", "reject"}

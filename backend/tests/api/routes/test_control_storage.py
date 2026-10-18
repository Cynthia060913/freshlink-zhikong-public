"""Actual disposable PG command tests; warehouse lookup is an explicit fake.

No actual B service, broker send, production migration or browser check.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, delete, select

from app.core.db import engine
from app.models import User
from app.modules.iot.control_models import (
    CommandCreate,
    CommandReceipt,
    DeviceCommand,
)
from app.modules.iot.control_routes import control_warehouse_lookup
from app.modules.iot.control_storage import (
    ControlError,
    claim_dispatch,
    create_command,
    expire_command,
    finish_dispatch,
    get_command,
    receive_command_ack,
)
from app.modules.iot.heartbeat_storage import persist_registered_heartbeat
from app.modules.iot.models import Device
from app.modules.iot.simulator import SoftwarePlant

T0 = datetime(2026, 10, 14, tzinfo=UTC)


@pytest.fixture
def setup_control(db):
    _ = db
    actor = User(
        email=f"control-{uuid4().hex}@example.com",
        hashed_password="test-only",
        is_superuser=True,
    )
    warehouse = uuid4()
    device = Device(
        code="control-" + uuid4().hex,
        name="Control storage fixture",
        kind="cooling_actuator",
        warehouse_id=warehouse,
    )
    with Session(engine) as s:
        s.add(actor)
        s.add(device)
        s.commit()
        s.refresh(actor)
        s.refresh(device)
        identity = device.id
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
                    "sent_at": T0.isoformat(),
                }
            ).encode(),
            received_at=T0,
            retained=False,
        )
        s.refresh(actor)
    yield actor, identity, warehouse
    with Session(engine) as s:
        s.exec(delete(DeviceCommand).where(DeviceCommand.device_id == identity))
        s.exec(delete(Device).where(Device.id == identity))
        s.exec(delete(User).where(User.id == actor.id))
        s.commit()


def create(fixture, request=None, now=T0):
    actor, identity, warehouse = fixture
    with Session(engine) as s:
        return create_command(
            s,
            actor=actor,
            device_id=identity,
            request=request or CommandCreate(command_id=uuid4(), enabled=True),
            now=now,
            warehouse_exists=lambda identity: identity == warehouse,
        )


def claim(command, fixture, now=T0):
    with Session(engine) as s:
        return claim_dispatch(
            s,
            command_id=command.command_id,
            now=now,
            warehouse_exists=lambda identity: identity == fixture[2],
        )


def stored(command):
    with Session(engine) as s:
        return s.get(DeviceCommand, command.command_id)


def ack(
    command,
    *,
    executed_at=T0 + timedelta(seconds=1),
    result="executed",
    enabled=True,
    reason=None,
):
    return {
        "schema_version": "1.0",
        "command_id": str(command.command_id),
        "device_id": str(command.device_id),
        "executed_at": executed_at.isoformat() if executed_at else None,
        "result": result,
        "state": {"cooling_enabled": enabled},
        "reason": reason,
    }


def receive(command, data=None, now=T0 + timedelta(seconds=2), **changes):
    arguments = {
        "authenticated_device_id": command.device_id,
        "credential_enabled": True,
        "topic": f"freshlink/v1/devices/{command.device_id}/acks",
        "payload": json.dumps(data or ack(command)).encode(),
        "received_at": now,
        "retained": False,
        **changes,
    }
    with Session(engine) as s:
        return receive_command_ack(s, **arguments)


def finish(dispatch, outcome="accepted", now=T0):
    with Session(engine) as s:
        finish_dispatch(s, dispatch=dispatch, outcome=outcome, now=now)


def test_creation_is_durable_and_retry_is_same_command(setup_control):
    request = CommandCreate(command_id=uuid4(), enabled=True)
    first = create(setup_control, request)
    again = create(setup_control, request, T0 + timedelta(seconds=1))
    assert (
        again.command_id == first.command_id
        and again.payload_text == first.payload_text
    )
    assert first.status == "queued" and first.attempt_count == 0
    assert json.loads(first.payload_text)["parameters"] == {"enabled": True}
    request.enabled = False
    with pytest.raises(ControlError, match="command_id_conflict"):
        create(setup_control, request)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("kind", "device_not_actuator"),
        ("unbound", "device_unbound"),
        ("offline", "device_not_online"),
        ("stale", "device_not_online"),
        ("ordinary", "control_forbidden"),
        ("disabled", "control_forbidden"),
    ],
)
def test_creation_authority_and_device_conditions(setup_control, change, reason):
    actor, identity, _ = setup_control
    with Session(engine) as s:
        device = s.get(Device, identity)
        user = s.get(User, actor.id)
        if change == "kind":
            device.kind = "temperature_sensor"
        if change == "unbound":
            device.warehouse_id = None
        if change == "offline":
            device.connectivity, device.offline_source_event_id = "offline", uuid4()
        if change == "stale":
            device.last_seen_at = T0 - timedelta(seconds=90)
        if change == "ordinary":
            user.is_superuser = False
        if change == "disabled":
            user.is_active = False
        s.commit()
    with pytest.raises(ControlError, match=reason):
        create(setup_control)


def test_unknown_device_warehouse_and_bad_clock(setup_control):
    actor, identity, _ = setup_control
    with Session(engine) as s:
        for target, lookup, reason in [
            (uuid4(), lambda _x: True, "device_unknown"),
            (identity, lambda _x: False, "device_unbound"),
        ]:
            with pytest.raises(ControlError, match=reason):
                create_command(
                    s,
                    actor=actor,
                    device_id=target,
                    request=CommandCreate(command_id=uuid4(), enabled=True),
                    now=T0,
                    warehouse_exists=lookup,
                )
        with pytest.raises(ControlError, match="timezone"):
            create_command(
                s,
                actor=actor,
                device_id=identity,
                request=CommandCreate(command_id=uuid4(), enabled=True),
                now=T0.replace(tzinfo=None),
                warehouse_exists=lambda _x: True,
            )


def test_one_active_command_and_expiry_releases_slot(setup_control):
    first = create(
        setup_control,
        CommandCreate(command_id=uuid4(), enabled=True, lifetime_seconds=5),
    )
    with pytest.raises(ControlError, match="in_flight"):
        create(setup_control)
    second = create(setup_control, now=T0 + timedelta(seconds=5))
    assert stored(first).status == "timeout" and stored(second).status == "queued"


def test_concurrent_same_command_and_concurrent_dispatch(setup_control):
    request = CommandCreate(command_id=uuid4(), enabled=True)
    barrier = Barrier(2)

    def issue(_):
        barrier.wait(timeout=5)
        return create(setup_control, request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(issue, range(2)))
    assert rows[0].command_id == rows[1].command_id
    barrier = Barrier(2)

    def send(_):
        barrier.wait(timeout=5)
        return claim(rows[0], setup_control)

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(send, range(2)))
    assert sum(item is not None for item in attempts) == 1


def test_broker_acceptance_is_not_execution(setup_control):
    command = create(setup_control)
    dispatch = claim(command, setup_control)
    finish(dispatch)
    assert stored(command).status == "sent" and stored(command).ack_received_at is None
    assert receive(command) == "accepted"
    assert stored(command).status == "acknowledged"
    assert receive(command, now=T0 + timedelta(seconds=100)) == "duplicate"
    assert stored(command).status == "acknowledged"


def test_execution_before_puback_does_not_get_downgraded(setup_control):
    command = create(setup_control)
    dispatch = claim(command, setup_control)
    assert receive(command) == "accepted"
    finish(dispatch, now=T0 + timedelta(seconds=3))
    finish(dispatch, now=T0 + timedelta(seconds=4))
    assert stored(command).status == "acknowledged"
    with pytest.raises(ControlError, match="outcome_conflict"):
        finish(dispatch, "permanent")


def test_late_execution_ack_preserves_timeout_and_evidence(setup_control):
    command = create(
        setup_control,
        CommandCreate(command_id=uuid4(), enabled=True, lifetime_seconds=5),
    )
    claim(command, setup_control)
    with Session(engine) as s:
        result = expire_command(
            s, command_id=command.command_id, now=T0 + timedelta(seconds=5)
        )
        assert result.status == "timeout"
    assert receive(command, now=T0 + timedelta(seconds=6)) == "late"
    assert stored(command).status == "timeout"
    assert receive(command, now=T0 + timedelta(seconds=7)) == "duplicate"
    with Session(engine) as s:
        row = s.exec(
            select(CommandReceipt).where(
                CommandReceipt.command_id == command.command_id
            )
        ).one()
        assert row.outcome == "late" and row.result == "executed"


def test_failed_execution_and_changed_receipt_are_retained(setup_control):
    command = create(setup_control)
    claim(command, setup_control)
    failed = ack(
        command,
        executed_at=None,
        result="failed",
        enabled=False,
        reason="simulated_switch_failure",
    )
    assert receive(command, failed) == "accepted"
    assert stored(command).status == "failed"
    assert receive(command) == "conflict"
    assert stored(command).failure_reason == "simulated_switch_failure"


@pytest.mark.parametrize(
    "invalid",
    [
        "retained",
        "disabled",
        "topic",
        "oversize",
        "identity",
        "unknown",
        "no_dispatch",
        "state",
        "expired_execution",
        "future_execution",
        "bad_json",
        "duplicate_json",
        "failed_shape",
        "unknown_version",
    ],
)
def test_invalid_ack_cannot_create_success(setup_control, invalid):
    command = create(setup_control)
    if invalid != "no_dispatch":
        claim(command, setup_control)
    changes = {}
    data = ack(command)
    if invalid == "retained":
        changes["retained"] = True
    if invalid == "disabled":
        changes["credential_enabled"] = False
    if invalid == "topic":
        changes["topic"] = "wrong/acks"
    if invalid == "oversize":
        changes["payload"] = b"x" * 4097
    if invalid == "identity":
        data["device_id"] = str(uuid4())
    if invalid == "unknown":
        data["command_id"] = str(uuid4())
    if invalid == "state":
        data["state"]["cooling_enabled"] = False
    if invalid == "expired_execution":
        data["executed_at"] = (T0 + timedelta(seconds=60)).isoformat()
    if invalid == "future_execution":
        data["executed_at"] = (T0 + timedelta(seconds=59)).isoformat()
    if invalid == "bad_json":
        changes["payload"] = b"not-json"
    if invalid == "duplicate_json":
        changes["payload"] = b'{"result":"executed","result":"failed"}'
    if invalid == "failed_shape":
        data["result"] = "failed"
    if invalid == "unknown_version":
        data["schema_version"] = "9"
    with pytest.raises(ControlError):
        receive(command, data, **changes)
    assert stored(command).status == "queued"


def test_retry_lease_crash_and_expiry(setup_control):
    command = create(setup_control)
    first = claim(command, setup_control)
    assert claim(command, setup_control, T0 + timedelta(seconds=29)) is None
    second = claim(command, setup_control, T0 + timedelta(seconds=30))
    assert second.payload == first.payload and second.attempt_id != first.attempt_id
    finish(first, "transient", T0 + timedelta(seconds=31))
    assert stored(command).lease_id == second.attempt_id
    finish(second, "transient", T0 + timedelta(seconds=31))
    assert claim(command, setup_control, T0 + timedelta(seconds=31)) is None
    assert claim(command, setup_control, T0 + timedelta(seconds=32)) is not None
    assert claim(command, setup_control, T0 + timedelta(seconds=60)) is None
    assert stored(command).status == "timeout"


def test_dispatch_revalidates_actor_and_binding(setup_control):
    command = create(setup_control)
    with Session(engine) as s:
        actor = s.get(User, setup_control[0].id)
        actor.is_superuser = False
        s.commit()
    assert claim(command, setup_control) is None
    assert stored(command).status == "failed"


def test_permanent_transport_failure_is_traced(setup_control):
    command = create(setup_control)
    attempt = claim(command, setup_control)
    finish(attempt, "permanent")
    assert stored(command).failure_reason == "broker_publish_rejected"
    assert claim(command, setup_control) is None


def test_uncertain_failed_delivery_blocks_contradictory_command(setup_control):
    command = create(setup_control)
    attempt = claim(command, setup_control)
    finish(attempt, "permanent")
    with pytest.raises(ControlError, match="prior_delivery_uncertain"):
        create(
            setup_control,
            CommandCreate(command_id=uuid4(), enabled=False),
            now=T0 + timedelta(seconds=1),
        )
    replacement = create(
        setup_control,
        CommandCreate(command_id=uuid4(), enabled=False),
        now=T0 + timedelta(seconds=60),
    )
    assert replacement.status == "queued"


def test_write_failure_and_unrelated_changes_do_not_commit(setup_control):
    actor, identity, _ = setup_control
    request = CommandCreate(command_id=uuid4(), enabled=True)
    with Session(engine) as s:
        with patch.object(
            s,
            "commit",
            side_effect=OperationalError("commit", {}, RuntimeError("outage")),
        ):
            with pytest.raises(OperationalError):
                create_command(
                    s,
                    actor=actor,
                    device_id=identity,
                    request=request,
                    now=T0,
                    warehouse_exists=lambda _x: True,
                )
    with Session(engine) as s:
        assert s.get(DeviceCommand, request.command_id) is None
        device = s.get(Device, identity)
        device.name = "unrelated"
        with pytest.raises(ValueError, match="pending_changes"):
            create_command(
                s,
                actor=actor,
                device_id=identity,
                request=request,
                now=T0,
                warehouse_exists=lambda _x: True,
            )
    with Session(engine) as s:
        assert s.get(Device, identity).name == "Control storage fixture"


def test_database_cannot_forge_success(setup_control):
    command = create(setup_control)
    with Session(engine) as s:
        row = s.get(DeviceCommand, command.command_id)
        row.status = "acknowledged"
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def test_dispatch_clock_stale_device_and_exhausted_budget(setup_control):
    command = create(
        setup_control,
        CommandCreate(command_id=uuid4(), enabled=True, lifetime_seconds=300),
    )
    with pytest.raises(ControlError, match="before_issue"):
        claim(command, setup_control, T0 - timedelta(seconds=1))
    assert claim(command, setup_control, T0 + timedelta(seconds=90)) is None
    with Session(engine) as s:
        row = s.get(DeviceCommand, command.command_id)
        row.attempt_count = 10
        s.commit()
    assert claim(command, setup_control) is None
    assert stored(command).failure_reason == "dispatch_retry_budget_exhausted"


def test_conflict_evidence_capacity_preserves_original(setup_control):
    command = create(setup_control)
    claim(command, setup_control)
    assert receive(command) == "accepted"
    for index in range(1, 32):
        data = ack(command, executed_at=T0 + timedelta(seconds=1, milliseconds=index))
        assert receive(command, data) == "conflict"
    with pytest.raises(ControlError, match="capacity"):
        receive(
            command,
            ack(command, executed_at=T0 + timedelta(seconds=1, milliseconds=33)),
        )
    assert receive(command) == "duplicate"
    assert stored(command).status == "acknowledged"


def test_dispatch_and_ack_commit_failure_roll_back(setup_control):
    command = create(setup_control)
    with (
        Session(engine) as s,
        patch.object(
            s,
            "commit",
            side_effect=OperationalError("commit", {}, RuntimeError("outage")),
        ),
    ):
        with pytest.raises(OperationalError):
            claim_dispatch(
                s,
                command_id=command.command_id,
                now=T0,
                warehouse_exists=lambda _x: True,
            )
    assert stored(command).attempt_count == 0
    dispatch = claim(command, setup_control)
    with (
        Session(engine) as s,
        patch.object(
            s,
            "commit",
            side_effect=OperationalError("commit", {}, RuntimeError("outage")),
        ),
    ):
        with pytest.raises(OperationalError):
            finish_dispatch(s, dispatch=dispatch, outcome="accepted", now=T0)
    assert stored(command).status == "queued"
    with (
        Session(engine) as s,
        patch.object(
            s,
            "commit",
            side_effect=OperationalError("commit", {}, RuntimeError("outage")),
        ),
    ):
        with pytest.raises(OperationalError):
            receive_command_ack(
                s,
                authenticated_device_id=command.device_id,
                credential_enabled=True,
                topic=f"freshlink/v1/devices/{command.device_id}/acks",
                payload=json.dumps(ack(command)).encode(),
                received_at=T0 + timedelta(seconds=2),
                retained=False,
            )
    assert stored(command).ack_fingerprint is None


def test_readonly_trace_unknown_and_command_mismatch(setup_control):
    command = create(setup_control)
    claim(command, setup_control)
    foreign = uuid4()
    with pytest.raises(ControlError, match="command_mismatch"):
        receive(
            command,
            {**ack(command), "device_id": str(foreign)},
            authenticated_device_id=foreign,
            topic=f"freshlink/v1/devices/{foreign}/acks",
        )
    with Session(engine) as s:
        assert get_command(s, uuid4()) is None
        trace = get_command(s, command.command_id)
        assert trace.parameters == {"enabled": True} and trace.status == "queued"


def test_invalid_dispatch_ids_outcomes_and_clock(setup_control):
    from app.modules.iot.control_storage import Dispatch

    command = create(setup_control)
    dispatch = claim(command, setup_control)
    with pytest.raises(ControlError, match="invalid_dispatch_outcome"):
        finish(dispatch, "unknown")
    with pytest.raises(ControlError, match="clock_reversed"):
        finish(dispatch, now=T0 - timedelta(seconds=1))
    with pytest.raises(ControlError, match="dispatch_unknown"):
        finish(Dispatch(command.command_id, uuid4(), dispatch.topic, dispatch.payload))


def test_actual_software_execution_changes_later_data(setup_control, tmp_path):
    command = create(setup_control)
    plant = SoftwarePlant(
        tmp_path / "owned-device.db",
        actuator_id=command.device_id,
        sensor_id=uuid4(),
        warehouse_id=command.warehouse_id,
        started_at=T0,
    )
    before = plant.sample(T0 + timedelta(seconds=0.5))
    dispatch = claim(command, setup_control)
    device_ack = plant.execute(
        dispatch.payload, topic=dispatch.topic, now=T0 + timedelta(seconds=1)
    )
    assert receive(command, device_ack) == "accepted"
    after = plant.sample(T0 + timedelta(seconds=10))
    assert after.power_w > before.power_w
    assert after.payload["metrics"][0]["value"] < before.payload["metrics"][0]["value"]
    assert (
        plant.execute(
            dispatch.payload, topic=dispatch.topic, now=T0 + timedelta(seconds=11)
        )
        == device_ack
    )
    assert receive(command, device_ack, now=T0 + timedelta(seconds=12)) == "duplicate"


def test_api_permissions_missing_integration_and_history(
    setup_control, client, superuser_token_headers, normal_user_token_headers
):
    from app.main import app

    _, identity, _ = setup_control
    base = f"/api/v1/devices/{identity}/commands"
    body = {"command_id": str(uuid4()), "enabled": True}
    assert client.post(base, json=body).status_code == 401
    assert (
        client.post(base, json=body, headers=normal_user_token_headers).status_code
        == 403
    )
    assert (
        client.post(base, json=body, headers=superuser_token_headers).status_code == 503
    )
    with Session(engine) as s:
        device = s.get(Device, identity)
        device.last_seen_at = datetime.now(UTC)
        s.commit()
    app.dependency_overrides[control_warehouse_lookup] = lambda: lambda _identity: True
    try:
        result = client.post(base, json=body, headers=superuser_token_headers)
        assert result.status_code == 201 and result.json()["status"] == "queued"
        assert (
            client.post(
                base, json={**body, "enabled": False}, headers=superuser_token_headers
            ).status_code
            == 409
        )
        assert (
            client.post(
                base,
                json={"command_id": str(uuid4()), "enabled": 1},
                headers=superuser_token_headers,
            ).status_code
            == 422
        )
        assert (
            client.post(
                base,
                json={
                    "command_id": str(uuid4()),
                    "enabled": True,
                    "event_id": str(uuid4()),
                },
                headers=superuser_token_headers,
            ).status_code
            == 422
        )
        page = client.get(base, headers=normal_user_token_headers)
        assert page.status_code == 200 and page.json()["count"] == 1
        detail = client.get(
            base + "/" + body["command_id"], headers=normal_user_token_headers
        )
        assert detail.status_code == 200 and detail.json()["receipts"] == []
        assert (
            client.get(
                base + "/" + str(uuid4()), headers=normal_user_token_headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                base + "?limit=101", headers=normal_user_token_headers
            ).status_code
            == 422
        )
    finally:
        app.dependency_overrides.pop(control_warehouse_lookup, None)

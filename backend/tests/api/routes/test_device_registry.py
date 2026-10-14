"""Real test-PostgreSQL registry checks; warehouse service is an explicit fake.

No actual B warehouse table/service, MQTT authentication or C ingestion is used.
"""

import json
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.core.db import engine
from app.models import User
from app.modules.iot.ingress import MAX_PAYLOAD_BYTES, IngressRejected
from app.modules.iot.models import Device
from app.modules.iot.registry import (
    DeviceBindingError,
    bind_registered_device,
    receive_registered_telemetry,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)
ADMIN = User(
    email="registry-admin@example.com", hashed_password="unused-test", is_superuser=True
)


@pytest.fixture
def registered_device(db: Session) -> Generator[UUID]:
    _ = db  # Ensure the disposable database fixture is initialized first.
    device = Device(
        code=f"adapter-{uuid4().hex}",
        name="registry fixture",
        kind="temperature_sensor",
    )
    with Session(engine) as session:
        session.add(device)
        session.commit()
        session.refresh(device)
        device_id = device.id
    yield device_id
    with Session(engine) as session:
        record = session.get(Device, device_id)
        if record:
            session.delete(record)
            session.commit()


def payload_for(device_id: UUID, **claims) -> bytes:
    return json.dumps(
        {
            "schema_version": "1.0",
            "device_id": str(device_id),
            "message_id": str(uuid4()),
            "metrics": [],
            **claims,
        }
    ).encode()


def receive(session: Session, device_id: UUID, warehouse_exists, **overrides):
    args = {
        "authenticated_device_id": device_id,
        "credential_enabled": True,
        "warehouse_exists": warehouse_exists,
        "topic": f"freshlink/v1/devices/{device_id}/telemetry",
        "payload": payload_for(device_id),
        "received_at": NOW,
    }
    args.update(overrides)
    return receive_registered_telemetry(session, **args)


def bind(session: Session, device_id: UUID, warehouse_id: UUID, **overrides):
    args = {
        "actor": ADMIN,
        "device_id": device_id,
        "warehouse_id": warehouse_id,
        "warehouse_exists": lambda _: True,
    }
    args.update(overrides)
    return bind_registered_device(session, **args)


def test_binding_persists_and_envelope_uses_database_binding(registered_device: UUID):
    warehouse_id = uuid4()
    with Session(engine) as session:
        record = bind(session, registered_device, warehouse_id)
        assert record.warehouse_id == warehouse_id
        assert record.last_seen_at is None
    with Session(engine) as session:
        validator = Mock(return_value=True)
        envelope = receive(session, registered_device, validator)
        validator.assert_called_once_with(warehouse_id)
        assert envelope.warehouse_id == warehouse_id
        assert envelope.authenticated_device_id == registered_device
        assert "warehouse_id" not in envelope.raw_payload
        assert session.get(Device, registered_device).last_seen_at is None


@pytest.mark.parametrize(
    "active,superuser", [(True, False), (False, True), (False, False)]
)
def test_binding_requires_active_authenticated_admin(
    registered_device: UUID, active: bool, superuser: bool
):
    actor = User(
        email="registry-user@example.com",
        hashed_password="unused-test",
        is_active=active,
        is_superuser=superuser,
    )
    validator = Mock(return_value=True)
    with Session(engine) as session:
        with pytest.raises(DeviceBindingError, match="binding_forbidden"):
            bind(
                session,
                registered_device,
                uuid4(),
                actor=actor,
                warehouse_exists=validator,
            )
        validator.assert_not_called()
        assert session.get(Device, registered_device).warehouse_id is None


def test_missing_device_and_warehouse_rejected(registered_device: UUID):
    with Session(engine) as session:
        with pytest.raises(DeviceBindingError, match="device_unknown"):
            bind(session, uuid4(), uuid4())
    with Session(engine) as session:
        with pytest.raises(DeviceBindingError, match="warehouse_unknown"):
            bind(session, registered_device, uuid4(), warehouse_exists=lambda _: False)
        assert session.get(Device, registered_device).warehouse_id is None


def test_repeat_binding_is_idempotent_and_rebinding_is_rejected(
    registered_device: UUID,
):
    first, second = uuid4(), uuid4()
    with Session(engine) as session:
        bind(session, registered_device, first)
        assert bind(session, registered_device, first).warehouse_id == first
        with pytest.raises(DeviceBindingError, match="binding_conflict"):
            bind(session, registered_device, second)
        session.rollback()
        assert session.get(Device, registered_device).warehouse_id == first


def test_concurrent_first_binding_cannot_silently_overwrite(registered_device: UUID):
    barrier = Barrier(2)
    warehouses = [uuid4(), uuid4()]

    def attempt(warehouse_id: UUID) -> tuple[str, UUID]:
        with Session(engine) as session:
            barrier.wait(timeout=5)
            try:
                bind(session, registered_device, warehouse_id)
                return "bound", warehouse_id
            except DeviceBindingError as error:
                session.rollback()
                return str(error), warehouse_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, item) for item in warehouses]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(item[0] for item in results) == ["binding_conflict", "bound"]
    winner = next(item[1] for item in results if item[0] == "bound")
    with Session(engine) as session:
        assert session.get(Device, registered_device).warehouse_id == winner


def test_commit_failure_rolls_back_binding(registered_device: UUID):
    failure = OperationalError("test", {}, RuntimeError("injected failure"))
    with Session(engine) as session:
        with patch.object(session, "commit", side_effect=failure):
            with pytest.raises(OperationalError):
                bind(session, registered_device, uuid4())
        assert session.get(Device, registered_device).warehouse_id is None


def test_warehouse_outage_does_not_bind_or_become_permanent_rejection(
    registered_device: UUID,
):
    warehouse_id = uuid4()
    unavailable = Mock(side_effect=RuntimeError("warehouse service unavailable"))
    with Session(engine) as session:
        with pytest.raises(RuntimeError, match="unavailable"):
            bind(session, registered_device, warehouse_id, warehouse_exists=unavailable)
        session.rollback()
        assert session.get(Device, registered_device).warehouse_id is None
        bind(session, registered_device, warehouse_id)
        with pytest.raises(RuntimeError, match="unavailable"):
            receive(session, registered_device, unavailable)


@pytest.mark.parametrize(
    "which,reason",
    [
        ("unknown", "device_unknown"),
        ("unbound", "device_unbound"),
        ("disabled", "device_disabled"),
        ("oversized", "payload_too_large"),
    ],
)
def test_registry_rejections(registered_device: UUID, which: str, reason: str):
    validator = Mock(return_value=True)
    device_id = uuid4() if which == "unknown" else registered_device
    overrides = {}
    if which == "disabled":
        overrides["credential_enabled"] = False
    if which == "oversized":
        overrides["payload"] = b" " * (MAX_PAYLOAD_BYTES + 1)
    with Session(engine) as session:
        with pytest.raises(IngressRejected) as caught:
            receive(session, device_id, validator, **overrides)
        assert caught.value.reason_code == reason
        validator.assert_not_called()


def test_missing_warehouse_blocks_previously_bound_device(registered_device: UUID):
    with Session(engine) as session:
        bind(session, registered_device, uuid4())
        with pytest.raises(IngressRejected, match="device_unbound"):
            receive(session, registered_device, lambda _: False)


def test_other_request_binding_is_reloaded_from_database(registered_device: UUID):
    warehouse_id = uuid4()
    with Session(engine) as stale_session:
        cached = stale_session.get(Device, registered_device)
        assert cached.warehouse_id is None
        with Session(engine) as writer:
            bind(writer, registered_device, warehouse_id)
        envelope = receive(stale_session, registered_device, lambda _: True)
        assert envelope.warehouse_id == warehouse_id
        assert cached.warehouse_id == warehouse_id


def test_payload_cannot_select_another_registered_device(registered_device: UUID):
    with Session(engine) as session:
        bind(session, registered_device, uuid4())
        with pytest.raises(IngressRejected, match="identity_mismatch"):
            receive(
                session, registered_device, lambda _: True, payload=payload_for(uuid4())
            )


def test_payload_cannot_override_binding_or_topic(registered_device: UUID):
    with Session(engine) as session:
        bind(session, registered_device, uuid4())
        with pytest.raises(IngressRejected, match="identity_mismatch"):
            receive(
                session,
                registered_device,
                lambda _: True,
                payload=payload_for(registered_device, warehouse_id=str(uuid4())),
            )
        with pytest.raises(IngressRejected, match="topic_mismatch"):
            receive(
                session,
                registered_device,
                lambda _: True,
                topic=f"freshlink/v1/devices/{uuid4()}/telemetry",
            )

"""Actual disposable PostgreSQL state/replay/outbox regression checks."""

import json
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, delete, select

from app.core.config import settings
from app.core.db import engine
from app.modules.iot.heartbeat_storage import (
    persist_registered_heartbeat,
    scan_registered_device,
)
from app.modules.iot.ingress import MAX_PAYLOAD_BYTES, IngressRejected
from app.modules.iot.models import (
    Device,
    DeviceConnectivityEvent,
    HeartbeatConflict,
    HeartbeatMessage,
)

T0 = datetime(2026, 10, 14, tzinfo=UTC)


@pytest.fixture
def device_id(db: Session) -> Generator[UUID]:
    _ = db
    with Session(engine) as s:
        device = Device(
            code=f"hb-store-{uuid4().hex}",
            name="Heartbeat storage fixture",
            kind="temperature_sensor",
        )
        s.add(device)
        s.commit()
        s.refresh(device)
        identity = device.id
    yield identity
    with Session(engine) as s:
        for model in (HeartbeatConflict, HeartbeatMessage, DeviceConnectivityEvent):
            s.exec(delete(model).where(model.device_id == identity))
        s.exec(delete(Device).where(Device.id == identity))
        s.commit()


def payload(identity: UUID, message_id: UUID, sent_at: datetime = T0, **extra) -> bytes:
    return json.dumps(
        {
            "schema_version": "1.0",
            "device_id": str(identity),
            "message_id": str(message_id),
            "sent_at": sent_at.isoformat(),
            **extra,
        }
    ).encode()


def receive(
    s: Session,
    identity: UUID,
    message_id: UUID,
    *,
    sent_at: datetime = T0,
    at: datetime = T0,
    **extra,
):
    return persist_registered_heartbeat(
        s,
        authenticated_device_id=identity,
        credential_enabled=True,
        topic=f"freshlink/v1/devices/{identity}/heartbeat",
        payload=payload(identity, message_id, sent_at, **extra),
        received_at=at,
        retained=False,
    )


def events(identity: UUID) -> list[DeviceConnectivityEvent]:
    with Session(engine) as s:
        return list(
            s.exec(
                select(DeviceConnectivityEvent)
                .where(DeviceConnectivityEvent.device_id == identity)
                .order_by(DeviceConnectivityEvent.occurred_at)
            ).all()
        )


def test_first_heartbeat_commits_state_and_replay_record(device_id: UUID):
    message = uuid4()
    with Session(engine) as s:
        assert receive(s, device_id, message).outcome == "accepted"
    with Session(engine) as independent:
        device = independent.get(Device, device_id)
        assert device.connectivity == "online"
        assert device.last_seen_at == T0
        assert device.last_sent_at == T0
        assert device.last_heartbeat_message_id == message
        assert (
            independent.get(HeartbeatMessage, (device_id, message)).outcome
            == "accepted"
        )
    assert events(device_id) == []


@pytest.mark.parametrize(
    "status,history,reference",
    [
        ("online", False, False),
        ("offline", True, False),
        ("online", True, True),
        ("unknown", False, True),
    ],
)
def test_database_rejects_false_connectivity_history(
    device_id: UUID, status: str, history: bool, reference: bool
):
    with Session(engine) as s:
        device = s.get(Device, device_id)
        device.connectivity = status
        if history:
            device.last_seen_at = T0
            device.last_sent_at = T0
            device.last_heartbeat_message_id = uuid4()
        if reference:
            device.offline_source_event_id = uuid4()
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()
    with Session(engine) as s:
        assert s.get(Device, device_id).connectivity == "unknown"


def test_duplicate_on_new_connection_does_not_renew(device_id: UUID):
    message = uuid4()
    with Session(engine) as s:
        receive(s, device_id, message)
    with Session(engine) as restarted:
        assert (
            receive(
                restarted, device_id, message, at=T0 + timedelta(seconds=10)
            ).outcome
            == "duplicate"
        )
    with Session(engine) as s:
        assert s.get(Device, device_id).last_seen_at == T0
        assert (
            len(
                s.exec(
                    select(HeartbeatMessage).where(
                        HeartbeatMessage.device_id == device_id
                    )
                ).all()
            )
            == 1
        )


def test_conflict_preserves_first_fingerprint_and_records_one_evidence(device_id: UUID):
    message = uuid4()
    with Session(engine) as s:
        receive(s, device_id, message)
        original = s.get(HeartbeatMessage, (device_id, message)).fingerprint
    for seconds in (10, 11):
        with Session(engine) as s:
            assert (
                receive(
                    s, device_id, message, at=T0 + timedelta(seconds=seconds), counter=1
                ).outcome
                == "conflict"
            )
    with Session(engine) as s:
        assert s.get(Device, device_id).last_seen_at == T0
        assert s.get(HeartbeatMessage, (device_id, message)).fingerprint == original
        assert (
            len(
                s.exec(
                    select(HeartbeatConflict).where(
                        HeartbeatConflict.device_id == device_id
                    )
                ).all()
            )
            == 1
        )


def test_out_of_order_is_durable_but_cannot_postpone_offline(device_id: UUID):
    first, late = uuid4(), uuid4()
    with Session(engine) as s:
        receive(s, device_id, first)
    with Session(engine) as s:
        result = receive(
            s,
            device_id,
            late,
            sent_at=T0 - timedelta(seconds=1),
            at=T0 + timedelta(seconds=80),
        )
        assert result.outcome == "out_of_order"
    with Session(engine) as s:
        assert s.get(HeartbeatMessage, (device_id, late)).outcome == "out_of_order"
        assert (
            scan_registered_device(
                s, device_id=device_id, now=T0 + timedelta(seconds=90)
            ).state.status
            == "offline"
        )
    assert events(device_id)[0].occurred_at == T0 + timedelta(seconds=90)


def test_never_seen_stays_unknown(device_id: UUID):
    with Session(engine) as s:
        assert (
            scan_registered_device(
                s, device_id=device_id, now=T0 + timedelta(days=1)
            ).outcome
            == "unchanged"
        )
        assert s.get(Device, device_id).connectivity == "unknown"
    assert events(device_id) == []


def test_exact_deadline_and_repeated_scanner_are_idempotent(device_id: UUID):
    with Session(engine) as s:
        receive(s, device_id, uuid4())
    for seconds, expected in ((89, "online"), (90, "offline"), (91, "offline")):
        with Session(engine) as s:
            result = scan_registered_device(
                s, device_id=device_id, now=T0 + timedelta(seconds=seconds)
            )
            assert result.state.status == expected
    rows = events(device_id)
    assert len(rows) == 1 and rows[0].kind == "offline"
    assert rows[0].delivery_state == "held_unbound"


def test_recovery_new_id_links_original_event(device_id: UUID):
    with Session(engine) as s:
        receive(s, device_id, uuid4())
    with Session(engine) as s:
        scan_registered_device(s, device_id=device_id, now=T0 + timedelta(seconds=90))
    fresh_id = uuid4()
    with Session(engine) as s:
        result = receive(
            s,
            device_id,
            fresh_id,
            sent_at=T0 + timedelta(seconds=95),
            at=T0 + timedelta(seconds=95),
        )
        assert result.state.status == "online"
    rows = events(device_id)
    assert [r.kind for r in rows] == ["offline", "recovered"]
    assert rows[0].source_event_id != rows[1].source_event_id
    assert rows[1].related_source_event_id == rows[0].source_event_id
    with Session(engine) as s:
        assert (
            receive(
                s,
                device_id,
                fresh_id,
                sent_at=T0 + timedelta(seconds=95),
                at=T0 + timedelta(seconds=96),
            ).outcome
            == "duplicate"
        )
    assert len(events(device_id)) == 2


def test_delayed_scanner_cannot_hide_an_outage(device_id: UUID):
    with Session(engine) as s:
        receive(s, device_id, uuid4())
    with Session(engine) as s:
        result = receive(
            s,
            device_id,
            uuid4(),
            sent_at=T0 + timedelta(seconds=95),
            at=T0 + timedelta(seconds=95),
        )
        assert len(result.events) == 2
    assert [r.occurred_at for r in events(device_id)] == [
        T0 + timedelta(seconds=90),
        T0 + timedelta(seconds=95),
    ]


def test_scan_started_before_new_heartbeat_commit_is_harmless(device_id: UUID):
    with Session(engine) as s:
        receive(
            s,
            device_id,
            uuid4(),
            sent_at=T0 + timedelta(seconds=10),
            at=T0 + timedelta(seconds=10),
        )
    with Session(engine) as s:
        assert (
            scan_registered_device(s, device_id=device_id, now=T0).outcome
            == "unchanged"
        )
    assert events(device_id) == []


def test_concurrent_replays_commit_only_once(device_id: UUID):
    barrier = Barrier(2)
    message = uuid4()

    def worker():
        with Session(engine) as s:
            barrier.wait(timeout=10)
            return receive(s, device_id, message).outcome

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert sorted(results) == ["accepted", "duplicate"]


def test_concurrent_scanners_emit_one_event(device_id: UUID):
    with Session(engine) as s:
        receive(s, device_id, uuid4())
    barrier = Barrier(2)

    def worker():
        with Session(engine) as s:
            barrier.wait(timeout=10)
            return scan_registered_device(
                s, device_id=device_id, now=T0 + timedelta(seconds=90)
            ).outcome

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert sorted(results) == ["accepted", "unchanged"]
    assert len(events(device_id)) == 1


def test_real_constraint_failure_rolls_back_state_fingerprint_and_events(
    device_id: UUID,
):
    message = uuid4()
    with Session(engine) as s:
        receive(s, device_id, message)
    fresh = uuid4()
    with Session(engine) as s:
        add = s.add

        def invalid_message(row):
            if isinstance(row, HeartbeatMessage):
                row.outcome = "invalid-for-check-constraint"
            add(row)

        with (
            patch.object(s, "add", side_effect=invalid_message),
            pytest.raises(IntegrityError),
        ):
            receive(
                s,
                device_id,
                fresh,
                sent_at=T0 + timedelta(seconds=95),
                at=T0 + timedelta(seconds=95),
            )
    with Session(engine) as s:
        assert s.get(Device, device_id).last_seen_at == T0
        assert s.get(HeartbeatMessage, (device_id, fresh)) is None
    assert events(device_id) == []
    with Session(engine) as s:
        assert (
            receive(
                s,
                device_id,
                fresh,
                sent_at=T0 + timedelta(seconds=95),
                at=T0 + timedelta(seconds=95),
            ).outcome
            == "accepted"
        )
    assert len(events(device_id)) == 2


def test_storage_outage_propagates(device_id: UUID):
    with (
        Session(engine) as s,
        patch.object(
            s,
            "commit",
            side_effect=OperationalError("commit", {}, RuntimeError("test outage")),
        ),
    ):
        with pytest.raises(OperationalError):
            receive(s, device_id, uuid4())
    with Session(engine) as s:
        assert s.get(Device, device_id).connectivity == "unknown"


def test_unrelated_pending_changes_are_not_committed(device_id: UUID):
    with Session(engine) as s:
        device = s.get(Device, device_id)
        device.name = "unrelated edit"
        with pytest.raises(ValueError, match="pending_changes"):
            receive(s, device_id, uuid4())
    with Session(engine) as s:
        assert s.get(Device, device_id).name == "Heartbeat storage fixture"


@pytest.mark.parametrize(
    "disabled,retained,oversized",
    [(True, False, False), (False, True, False), (False, False, True)],
)
def test_pre_database_rejection(
    device_id: UUID, disabled: bool, retained: bool, oversized: bool
):
    with Session(engine) as s, patch.object(s, "exec") as lookup:
        with pytest.raises(IngressRejected):
            persist_registered_heartbeat(
                s,
                authenticated_device_id=device_id,
                credential_enabled=not disabled,
                topic=f"freshlink/v1/devices/{device_id}/heartbeat",
                payload=b" " * (MAX_PAYLOAD_BYTES + 1)
                if oversized
                else payload(device_id, uuid4()),
                received_at=T0,
                retained=retained,
            )
        lookup.assert_not_called()


def test_invalid_time_does_not_write_state(device_id: UUID):
    with Session(engine) as s:
        with pytest.raises(ValueError):
            scan_registered_device(s, device_id=device_id, now=T0.replace(tzinfo=None))
        with pytest.raises(IngressRejected):
            receive(s, device_id, uuid4(), sent_at=T0.replace(tzinfo=None))
    with Session(engine) as s:
        assert s.get(Device, device_id).connectivity == "unknown"


def test_api_exposes_committed_status_and_event_history(
    device_id: UUID, client: TestClient, normal_user_token_headers: dict[str, str]
):
    with Session(engine) as s:
        receive(s, device_id, uuid4())
    base = f"{settings.API_V1_STR}/devices/{device_id}"
    assert (
        client.get(base, headers=normal_user_token_headers).json()["connectivity"]
        == "online"
    )
    with Session(engine) as s:
        scan_registered_device(s, device_id=device_id, now=T0 + timedelta(seconds=90))
    assert (
        client.get(base, headers=normal_user_token_headers).json()["connectivity"]
        == "offline"
    )
    history = client.get(
        base + "/connectivity-events", headers=normal_user_token_headers
    )
    assert history.status_code == 200 and history.json()["count"] == 1
    assert history.json()["data"][0]["delivery_state"] == "held_unbound"
    assert client.get(base + "/connectivity-events").status_code == 401
    assert (
        client.get(
            base + "/connectivity-events",
            headers=normal_user_token_headers,
            params={"limit": 0},
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"{settings.API_V1_STR}/devices/{uuid4()}/connectivity-events",
            headers=normal_user_token_headers,
        ).status_code
        == 404
    )

"""Actual migrated PG reads; synthetic fixtures, no MQTT/model/send claim."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, col

from app.core.db import engine
from app.modules.iot.forecast_observations import (
    ForecastObservationUnavailable,
    read_forecast_observations,
)
from app.modules.iot.forecast_window import ForecastWindowPolicy
from app.modules.iot.models import Device
from app.modules.telemetry.storage_candidate import observations

pytestmark = pytest.mark.skipif(
    os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") != "1",
    reason="explicit disposable PostgreSQL opt-in required",
)
NOW = datetime(2026, 10, 31, 6, 0, tzinfo=UTC)


def owned_test_target(url: URL) -> bool:
    if url.host in {"db", "test-db"}:
        return True
    # Hosted CI runs pytest outside Compose and reaches its disposable DB
    # through a published loopback port. Ordinary local runs do not qualify.
    return (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("GITHUB_REPOSITORY") == "Cynthia060913/freshlink-zhikong-public"
        and url.host in {"localhost", "127.0.0.1"}
        and url.database == "app"
        and url.username == "postgres"
        and url.port == 5432
    )


@pytest.mark.parametrize(
    "actions,url,allowed",
    [
        ("", "postgresql://postgres@localhost:5432/app", False),
        ("false", "postgresql://postgres@localhost:5432/app", False),
        ("true", "postgresql://postgres@localhost:5432/app", True),
        ("true", "postgresql://postgres@127.0.0.1:5432/app", True),
        ("true", "postgresql://postgres@production.invalid:5432/app", False),
        ("true", "postgresql://postgres@localhost:5432/original", False),
        ("true", "postgresql://owner@localhost:5432/app", False),
        ("true", "postgresql://postgres@localhost:5433/app", False),
    ],
)
def test_ci_loopback_exception_keeps_other_targets_blocked(
    monkeypatch, actions, url, allowed
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", actions)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Cynthia060913/freshlink-zhikong-public")
    assert owned_test_target(make_url(url)) is allowed


def test_other_repository_ci_cannot_enable_loopback_test_db(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/other")
    assert not owned_test_target(make_url("postgresql://postgres@localhost:5432/app"))


@pytest.fixture
def pg_scope() -> Iterator[tuple[UUID, UUID, list[UUID]]]:
    assert engine.dialect.name == "postgresql"
    assert owned_test_target(engine.url), "owned Docker/Actions test DB required"
    room, device = uuid4(), uuid4()
    ids = [device]
    with Session(engine) as session:
        session.add(
            Device(
                id=device,
                code=f"forecast-{device.hex}",
                name="Fixture",
                kind="temperature_sensor",
                warehouse_id=room,
            )
        )
        session.commit()
    try:
        yield device, room, ids
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(observations).where(observations.c.device_id.in_(ids))
            )
            connection.execute(delete(Device).where(col(Device.id).in_(ids)))


def add_rows(device: UUID, room: UUID, *, count: int = 8) -> list[dict[str, object]]:
    return [
        {
            "id": uuid4(),
            "device_id": device,
            "message_id": uuid4(),
            "warehouse_id": room,
            "content_sha256": "0" * 64,
            "raw_text": "synthetic forecast read fixture",
            "raw_payload": {},
            "sampled_at_utc": NOW - timedelta(seconds=16 - index * 2),
            "first_received_at_utc": NOW - timedelta(seconds=16 - index * 2),
            "normalized": {"metrics": {"temperature": 7.0 + index / 10}},
            "quality": ["ok"],
            "risk": {"state": "unknown"},
        }
        for index in range(count)
    ]


def store(rows: list[dict[str, object]]) -> None:
    with engine.begin() as connection:
        connection.execute(observations.insert(), rows)


def read(device: UUID, room: UUID, **kwargs):
    return read_forecast_observations(
        engine, device_id=device, warehouse_id=room, as_of=NOW, **kwargs
    )


def test_actual_normalized_pg_window_is_ordered_and_read_only(pg_scope) -> None:
    device, room, _ = pg_scope
    rows = add_rows(device, room)
    store(list(reversed(rows)))
    transaction_flags = []

    def inspect_flags(connection, _cursor, statement, _parameters, _context, _many):
        if (
            statement.startswith("SELECT")
            and "telemetry_observation_candidate" in statement
        ):
            transaction_flags.append(
                (
                    connection.exec_driver_sql(
                        "SHOW transaction_read_only"
                    ).scalar_one(),
                    connection.exec_driver_sql(
                        "SHOW transaction_isolation"
                    ).scalar_one(),
                )
            )

    event.listen(engine, "after_cursor_execute", inspect_flags)
    try:
        result = read(device, room)
    finally:
        event.remove(engine, "after_cursor_execute", inspect_flags)
    assert result.status == "ready" and result.rows_read == 8
    assert result.device_connectivity == "unknown"  # No online claim from temperature.
    assert result.window is not None
    assert [p.observation_id for p in result.window.points] == [r["id"] for r in rows]
    assert result.window.target_at == NOW + timedelta(seconds=60)
    assert transaction_flags == [("on", "repeatable read")]
    with engine.connect() as connection:
        saved = (
            connection.execute(
                select(observations.c.id, observations.c.normalized).where(
                    observations.c.device_id == device
                )
            )
            .mappings()
            .all()
        )
    assert {r["id"]: r["normalized"] for r in saved} == {
        r["id"]: r["normalized"] for r in rows
    }


def test_future_receipts_and_other_scopes_cannot_enter_window_or_capacity(
    pg_scope,
) -> None:
    device, room, _ = pg_scope
    good = add_rows(device, room)
    excluded = add_rows(device, room)
    for row in excluded:
        row.update(
            first_received_at_utc=NOW + timedelta(seconds=1),
            sampled_at_utc=None,
            quality=["invalid"],
        )
    wrong_room = add_rows(device, uuid4())
    other_device = add_rows(uuid4(), room)
    # The second device is a synthetic C row only; no server registry FK exists.
    pg_scope[2].append(other_device[0]["device_id"])
    store(good + excluded + wrong_room + other_device)
    result = read(device, room, policy=ForecastWindowPolicy(max_points=8))
    assert result.status == "ready" and result.window is not None
    assert {p.observation_id for p in result.window.points} == {r["id"] for r in good}


def test_recent_missing_sampling_time_is_not_silently_dropped_by_sql(pg_scope) -> None:
    device, room, _ = pg_scope
    rows = add_rows(device, room)
    bad = dict(
        rows[0],
        id=uuid4(),
        message_id=uuid4(),
        sampled_at_utc=None,
        first_received_at_utc=NOW,
    )
    store(rows + [bad])
    result = read(device, room)
    assert result.status == "invalid_input" and result.rows_read == 9
    assert result.window is not None and result.window.points == ()


def test_latest_bad_quality_is_not_filtered_out_to_make_window_ready(pg_scope) -> None:
    device, room, _ = pg_scope
    rows = add_rows(device, room)
    rows[-1]["quality"] = ["missing_temperature"]
    store(rows)
    result = read(device, room)
    assert result.status == "quality_blocked"
    assert result.window is not None and result.window.points == ()


def test_capacity_overflow_refuses_instead_of_truncating_bad_evidence(pg_scope) -> None:
    device, room, _ = pg_scope
    rows = add_rows(device, room)
    extra = dict(rows[0], id=uuid4(), message_id=uuid4(), quality=["invalid"])
    store(rows + [extra])
    result = read(device, room, policy=ForecastWindowPolicy(max_points=8))
    assert result.status == "input_limit_exceeded" and result.rows_read == 9
    assert result.window is None


def test_missing_device_binding_and_kind_return_no_model_window(pg_scope) -> None:
    device, room, ids = pg_scope
    assert read(uuid4(), room).status == "unknown_device"
    assert read(device, uuid4()).status == "warehouse_mismatch"
    for kind, binding, expected in [
        ("temperature_sensor", None, "unbound_device"),
        ("cooling_actuator", room, "unsupported_device"),
    ]:
        key = uuid4()
        ids.append(key)
        with Session(engine) as session:
            session.add(
                Device(
                    id=key,
                    code=f"forecast-{key.hex}",
                    name="Fixture",
                    kind=kind,
                    warehouse_id=binding,
                )
            )
            session.commit()
        result = read(key, room)
        assert result.status == expected and result.window is None


def test_exact_window_start_and_receipt_decision_are_inclusive(pg_scope) -> None:
    device, room, _ = pg_scope
    rows = add_rows(device, room)
    rows[0].update(
        sampled_at_utc=NOW - timedelta(seconds=120), first_received_at_utc=NOW
    )
    store(rows)
    result = read(device, room, policy=ForecastWindowPolicy(max_gap_seconds=120))
    assert result.status == "ready" and result.window is not None
    assert result.window.points[0].observation_id == rows[0]["id"]


def test_late_arrival_does_not_make_old_sampling_fresh(pg_scope) -> None:
    device, room, _ = pg_scope
    rows = add_rows(device, room)
    for row in rows:
        row["sampled_at_utc"] -= timedelta(seconds=30)
        row["first_received_at_utc"] = NOW
    store(rows)
    result = read(device, room)
    assert result.status == "stale_data"
    assert result.window is not None and not result.window.points


def test_storage_failure_is_a_fixed_error_not_an_empty_success(pg_scope) -> None:
    device, room, _ = pg_scope
    with patch.object(
        engine,
        "connect",
        side_effect=OperationalError(
            "synthetic-sensitive-detail", {}, Exception("do not expose")
        ),
    ):
        with pytest.raises(ForecastObservationUnavailable) as caught:
            read(device, room)
    assert str(caught.value) == "forecast_observation_read_failed"


def test_invalid_scope_and_naive_decision_refused_before_read(pg_scope) -> None:
    device, room, _ = pg_scope
    with pytest.raises(ValueError, match="UUID_scope_required"):
        read_forecast_observations(
            engine, device_id=str(device), warehouse_id=room, as_of=NOW
        )
    with pytest.raises(ValueError, match="aware_decision_time_required"):
        read_forecast_observations(
            engine, device_id=device, warehouse_id=room, as_of=NOW.replace(tzinfo=None)
        )
    with pytest.raises(ValueError, match="forecast_policy_required"):
        read(device, room, policy=None)

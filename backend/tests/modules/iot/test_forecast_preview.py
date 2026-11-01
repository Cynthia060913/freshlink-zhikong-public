"""Actual migrated PG -> guarded C model -> authenticated read-only HTTP."""

import json
import os
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, event
from sqlmodel import Session, col

from app.core.db import engine
from app.core.security import create_access_token
from app.models import User
from app.modules.iot import forecast_preview as preview
from app.modules.iot.forecast_routes import forecast_clock, forecast_enabled, router
from app.modules.iot.models import Device
from tests.modules.iot.test_forecast_observations import NOW, add_rows, store
from tests.modules.iot.test_forecast_observations import pg_scope as pg_scope

pytestmark = pytest.mark.skipif(
    os.getenv("FRESHLINK_INBOX_TEST_POSTGRES") != "1",
    reason="explicit disposable PostgreSQL opt-in required",
)


def fixture_device(device_id) -> Device:
    with Session(engine) as session:
        device = session.get(Device, device_id)
        assert device is not None
        return device


def run_preview(pg_scope, **kwargs) -> preview.ForecastPreview:
    return preview.read_temperature_forecast_preview(
        engine,
        device=fixture_device(pg_scope[0]),
        decision_at=NOW,
        enabled=True,
        **kwargs,
    )


def test_default_disabled_never_reads_model_or_observations(pg_scope) -> None:
    with (
        patch.object(preview, "load_forecast_parameters") as model,
        patch.object(preview, "read_forecast_observations") as data,
    ):
        result = preview.read_temperature_forecast_preview(
            engine, device=fixture_device(pg_scope[0]), decision_at=NOW
        )
    assert result.status == "unavailable" and result.reason == "preview_disabled"
    assert result.predicted_temperature_c is None and not result.input_points
    model.assert_not_called()
    data.assert_not_called()


def test_pinned_parameters_are_the_actual_c_artifact() -> None:
    parameters = preview.load_forecast_parameters()
    original = Path("experiments/c_forecast/artifacts/v1/parameters.json")
    assert preview.PARAMETER_FILE.read_bytes() == original.read_bytes()
    assert parameters.sha256() == preview.PARAMETER_SHA256
    assert (parameters.history_seconds, parameters.horizon_seconds) == (120, 60)


@pytest.mark.parametrize(
    "kind", ["missing", "array", "invalid", "schema", "changed", "large"]
)
def test_missing_or_unreviewed_parameters_fail_closed(pg_scope, tmp_path, kind) -> None:
    path = tmp_path / "synthetic-parameters.json"
    document = json.loads(preview.PARAMETER_FILE.read_bytes())
    if kind == "array":
        path.write_text("[]")
    elif kind == "invalid":
        path.write_text("SYNTHETIC_BAD_JSON_ONLY")
    elif kind == "large":
        path.write_text(" " * (preview.MAX_PARAMETER_BYTES + 1))
    elif kind != "missing":
        document["schema_version" if kind == "schema" else "model_version"] = (
            "unreviewed"
        )
        path.write_text(json.dumps(document))
    with patch.object(preview, "read_forecast_observations") as data:
        result = run_preview(pg_scope, parameters_path=path)
    data.assert_not_called()
    assert (
        result.reason == "model_unavailable" and result.predicted_temperature_c is None
    )
    assert str(path) not in result.model_dump_json()
    assert "SYNTHETIC_BAD_JSON_ONLY" not in result.model_dump_json()


def test_actual_pg_model_snapshot_is_traceable_and_emits_no_writes(pg_scope) -> None:
    device, room, _ = pg_scope
    rows = add_rows(device, room)
    store(list(reversed(rows)))
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(engine, "after_cursor_execute", capture)
    try:
        with patch.object(
            preview, "predict_temperature", wraps=preview.predict_temperature
        ) as model:
            result = run_preview(pg_scope)
    finally:
        event.remove(engine, "after_cursor_execute", capture)
    model.assert_called_once()
    assert result.status == "predicted" and result.reason is None
    assert result.model_source == preview.MODEL_SOURCE
    assert result.parameter_sha256 == preview.PARAMETER_SHA256
    assert result.target_at == NOW + timedelta(seconds=60)
    assert result.refresh_before == NOW + timedelta(seconds=13)
    assert result.last_value_temperature_c == 7.7
    assert result.linear_trend_temperature_c == pytest.approx(10.8)
    assert result.predicted_temperature_c is not None
    assert [point.observation_id for point in result.input_points] == [
        row["id"] for row in rows
    ]
    assert result.business_warning_accepted is False and result.experimental is True
    assert result.device_connectivity == "unknown"
    assert not {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}.intersection(statements)


@pytest.mark.parametrize("bad", ["quality", "gap", "stale", "insufficient"])
def test_bad_windows_do_not_call_the_model_or_publish_a_value(pg_scope, bad) -> None:
    device, room, _ = pg_scope
    rows = add_rows(device, room)
    expected = {
        "quality": "quality_blocked",
        "gap": "gapped_data",
        "stale": "stale_data",
        "insufficient": "insufficient_data",
    }[bad]
    if bad == "quality":
        rows[-1]["quality"] = ["invalid"]
    elif bad == "gap":
        rows[0]["sampled_at_utc"] = NOW - timedelta(seconds=80)
        rows[0]["first_received_at_utc"] = NOW - timedelta(seconds=80)
    elif bad == "stale":
        for row in rows:
            row["sampled_at_utc"] -= timedelta(seconds=30)
            row["first_received_at_utc"] -= timedelta(seconds=30)
    else:
        rows = rows[:3]
    store(rows)
    with patch.object(preview, "predict_temperature") as model:
        result = run_preview(pg_scope)
    model.assert_not_called()
    assert result.status == "unavailable" and result.reason == expected
    assert result.predicted_temperature_c is None and result.input_points == []


def test_not_yet_received_bad_data_cannot_change_prediction(pg_scope) -> None:
    device, room, _ = pg_scope
    store(add_rows(device, room))
    before = run_preview(pg_scope)
    bad = dict(
        add_rows(device, room)[-1],
        quality=["invalid"],
        first_received_at_utc=NOW + timedelta(seconds=1),
    )
    store([bad])
    assert run_preview(pg_scope) == before


def test_missing_model_storage_or_prediction_errors_never_leak_details(
    pg_scope,
) -> None:
    device, room, _ = pg_scope
    store(add_rows(device, room))
    with patch.object(
        preview,
        "read_forecast_observations",
        side_effect=preview.ForecastObservationUnavailable("SYNTHETIC_PRIVATE_DETAIL"),
    ):
        result = run_preview(pg_scope)
    assert result.reason == "storage_unavailable"
    assert "SYNTHETIC_PRIVATE_DETAIL" not in result.model_dump_json()
    with patch.object(
        preview,
        "predict_temperature",
        side_effect=ValueError("SYNTHETIC_PRIVATE_DETAIL"),
    ):
        result = run_preview(pg_scope)
    assert result.reason == "prediction_unavailable"
    assert "SYNTHETIC_PRIVATE_DETAIL" not in result.model_dump_json()


@pytest.fixture
def http_scope(pg_scope) -> Iterator[tuple[TestClient, dict[str, str], User]]:
    _ = pg_scope  # Require the owned test-target check before creating a user.
    user = User(
        email=f"forecast-{uuid4().hex}@example.com",
        hashed_password="unused-fixture-only",
        is_superuser=False,
    )
    with Session(engine) as session:
        session.add(user)
        session.commit()
        session.refresh(user)
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[forecast_clock] = lambda: NOW
    application.dependency_overrides[forecast_enabled] = lambda: True
    token = create_access_token(user.id, timedelta(minutes=1))
    try:
        with TestClient(application) as client:
            yield client, {"Authorization": f"Bearer {token}"}, user
    finally:
        with engine.begin() as connection:
            connection.execute(delete(User).where(col(User.id) == user.id))


def test_real_jwt_http_uses_server_decision_and_registered_room(
    pg_scope, http_scope
) -> None:
    device, room, _ = pg_scope
    store(add_rows(device, room))
    client, headers, _ = http_scope
    response = client.get(
        f"/api/v1/devices/{device}/temperature-forecast?as_of=2099-01-01&warehouse_id={uuid4()}",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    result = response.json()
    assert result["status"] == "predicted" and result["warehouse_id"] == str(room)
    assert result["decision_at"] == "2026-10-31T06:00:00Z"
    assert len(result["input_points"]) == 8
    assert result["business_warning_accepted"] is False


def test_unauthenticated_and_invalid_tokens_never_call_model(
    pg_scope, http_scope
) -> None:
    client, _, _ = http_scope
    url = f"/api/v1/devices/{pg_scope[0]}/temperature-forecast"
    with patch.object(preview, "predict_temperature") as model:
        assert client.get(url).status_code == 401
        assert (
            client.get(
                url, headers={"Authorization": "Bearer invalid-fixture"}
            ).status_code
            == 403
        )
    model.assert_not_called()


def test_http_unknown_device_and_default_capability(http_scope) -> None:
    client, headers, _ = http_scope
    assert (
        client.get(
            f"/api/v1/devices/{uuid4()}/temperature-forecast", headers=headers
        ).status_code
        == 404
    )
    client.app.dependency_overrides.pop(forecast_enabled)
    response = client.get("/api/v1/devices/forecast-preview/status", headers=headers)
    assert response.status_code == 200 and response.json() == {"enabled": False}

"""A01 regression tests; use a disposable test PostgreSQL database only."""

import uuid
from collections.abc import Generator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.core.config import settings
from app.modules.iot.models import Device

URL = f"{settings.API_V1_STR}/devices/"


@pytest.fixture
def device_code(db: Session) -> Generator[str]:
    code = f"pytest-{uuid.uuid4().hex}"
    yield code
    db.rollback()
    db.exec(delete(Device).where(Device.code == code))
    db.commit()


@pytest.mark.parametrize("kind", ["temperature_sensor", "cooling_actuator"])
def test_device_round_trip(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
    device_code: str,
    kind: str,
) -> None:
    payload = {
        "code": f" {device_code.upper()} ",
        "name": " 测试设备 ",
        "kind": kind,
        "warehouse_id": None,
    }
    response = client.post(URL, headers=superuser_token_headers, json=payload)
    assert response.status_code == 201
    device = response.json()
    assert device["code"] == device_code
    assert device["name"] == "测试设备"
    assert device["kind"] == kind
    assert device["is_simulated"] is True
    assert device["warehouse_id"] is None
    assert device["last_seen_at"] is None
    assert device["connectivity"] == "unknown"
    assert datetime.fromisoformat(device["created_at"]).utcoffset() is not None
    uuid.UUID(device["id"])
    detail = client.get(URL + device["id"], headers=normal_user_token_headers)
    assert detail.status_code == 200
    assert detail.json() == device
    before = client.get(URL, headers=normal_user_token_headers)
    assert before.status_code == 200
    duplicate = client.post(URL, headers=superuser_token_headers, json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Device code already exists"
    after = client.get(URL, headers=normal_user_token_headers)
    assert after.json()["count"] == before.json()["count"]
    count = after.json()["count"]
    ids = []
    for skip in range(count):
        page = client.get(
            URL, headers=normal_user_token_headers, params={"skip": skip, "limit": 1}
        )
        assert page.status_code == 200
        assert page.json()["count"] == count
        assert len(page.json()["data"]) == 1
        ids.append(page.json()["data"][0]["id"])
    assert len(set(ids)) == count
    assert device["id"] in ids
    empty = client.get(URL, headers=normal_user_token_headers, params={"skip": count})
    assert empty.json()["data"] == []


def test_device_permissions(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    device_code: str,
) -> None:
    payload = {"code": device_code, "name": "权限测试", "kind": "temperature_sensor"}
    before = client.get(URL, headers=normal_user_token_headers).json()["count"]
    assert (
        client.post(URL, headers=normal_user_token_headers, json=payload).status_code
        == 403
    )
    assert client.post(URL, json=payload).status_code == 401
    assert client.get(URL).status_code == 401
    assert client.get(URL + str(uuid.uuid4())).status_code == 401
    assert client.get(URL, headers=normal_user_token_headers).json()["count"] == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("code", ""),
        ("code", "has space"),
        ("code", "中文"),
        ("code", "-bad"),
        ("code", "a" * 65),
        ("code", 123),
        ("name", "  "),
        ("name", 123),
        ("name", "a" * 121),
        ("kind", "unsupported"),
        ("warehouse_id", str(uuid.uuid4())),
    ],
)
def test_device_invalid_registration(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    device_code: str,
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "code": device_code,
        "name": "测试",
        "kind": "temperature_sensor",
    }
    payload[field] = value
    assert (
        client.post(URL, headers=superuser_token_headers, json=payload).status_code
        == 422
    )


@pytest.mark.parametrize(
    "suffix,status",
    [
        ("?skip=-1", 422),
        ("?limit=0", 422),
        ("?limit=101", 422),
        ("not-a-uuid", 422),
        (str(uuid.uuid4()), 404),
    ],
)
def test_device_invalid_query(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    suffix: str,
    status: int,
) -> None:
    assert (
        client.get(URL + suffix, headers=superuser_token_headers).status_code == status
    )

"""A-owned shared-root checks; not B concurrency or notification acceptance."""

import uuid

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def test_alerts_paths_have_one_global_prefix() -> None:
    paths = app.openapi()["paths"]
    prefix = settings.API_V1_STR
    assert f"{prefix}/alerts/" in paths
    assert f"{prefix}/alerts/{{alert_event_id}}/status" in paths
    assert f"{prefix}/alerts/{{alert_event_id}}/acknowledge" in paths
    assert not any(path.startswith(f"{prefix}{prefix}/") for path in paths)


def test_alerts_list_requires_login(client: TestClient) -> None:
    response = client.get(f"{settings.API_V1_STR}/alerts/")
    assert response.status_code == 401


def test_alerts_reader_uses_real_root_and_cannot_acknowledge(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    path = f"{settings.API_V1_STR}/alerts/"
    response = client.get(path, headers=normal_user_token_headers)
    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)
    assert isinstance(response.json()["count"], int)
    missing = str(uuid.uuid4())
    assert (
        client.get(
            f"{path}{missing}/status", headers=normal_user_token_headers
        ).status_code
        == 404
    )
    # The shared ingestion guard must not accidentally grant ordinary readers
    # lifecycle write access, or demand an ingestion credential for GETs.
    assert (
        client.post(
            f"{path}{missing}/acknowledge", headers=normal_user_token_headers
        ).status_code
        == 403
    )


def test_alerts_admin_missing_event_and_query_validation(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    path = f"{settings.API_V1_STR}/alerts/"
    assert (
        client.post(
            f"{path}{uuid.uuid4()}/acknowledge", headers=superuser_token_headers
        ).status_code
        == 404
    )
    for query in ({"state": "normal"}, {"limit": 0}, {"skip": -1}):
        assert (
            client.get(path, params=query, headers=superuser_token_headers).status_code
            == 422
        )

"""HTTP smoke of an archive-built image's default CMD, not full E1 acceptance."""

import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def require(value: bool, reason: str) -> None:
    if not value:
        raise RuntimeError("Application image proof: " + reason)


def preflight() -> str:
    require(Path("/.dockerenv").is_file(), "disposable container required")
    source = os.environ.get("IMAGE_PROOF_SOURCE", "")
    url = urlsplit(os.environ.get("DATABASE_URL", ""))
    require(bool(re.fullmatch("[0-9a-f]{40}", source)), "exact source required")
    require(os.environ.get("IMAGE_CANDIDATE_PROOF") == "1", "explicit opt-in required")
    require(
        url.hostname == "test-db"
        and url.path == "/freshlink_test"
        and url.username == "freshlink_test",
        "disposable database required",
    )
    require(
        os.environ.get("PROJECT_NAME") == "FreshLink-test"
        and os.environ.get("FASTAPI_ENV") == "development",
        "test settings required",
    )
    require(
        not any(
            os.environ.get(key)
            for key in (
                "SMTP_HOST",
                "SMTP_USER",
                "SMTP_PASSWORD",
                "EMAILS_FROM_EMAIL",
                "SENTRY_DSN",
            )
        ),
        "outbound settings forbidden",
    )
    return source


def prove(client: httpx.Client, source: str) -> dict:
    """The caller must use the internal application service, never a public URL."""
    origin = client.base_url
    require(
        origin.scheme == "http"
        and origin.host == "application"
        and origin.port == 8000
        and origin.path in ("", "/")
        and not origin.username
        and not origin.password,
        "unexpected application origin",
    )
    root = client.get("/")
    require(
        root.status_code == 200 and "text/html" in root.headers.get("content-type", ""),
        "compiled frontend HTML",
    )
    assets = re.findall(r"/assets/[A-Za-z0-9._-]+\.js", root.text)
    require(bool(assets), "compiled frontend script reference")
    asset = client.get(assets[0])
    require(
        asset.status_code == 200
        and len(asset.content) > 20
        and "javascript" in asset.headers.get("content-type", ""),
        "compiled JavaScript response",
    )
    health = client.get("/api/v1/utils/health-check/")
    require(health.status_code == 200 and health.json() is True, "server health")
    require(
        client.get("/api/v1/devices/").status_code == 401, "anonymous device refusal"
    )
    login = client.post(
        "/api/v1/login/access-token",
        data={
            "username": "image-proof@example.com",
            "password": "image-proof-disposable-admin-only",
        },
    )
    require(
        login.status_code == 200 and isinstance(login.json().get("access_token"), str),
        "real login",
    )
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    user = client.get("/api/v1/users/me", headers=headers)
    require(
        user.status_code == 200
        and user.json().get("is_active") is True
        and user.json().get("is_superuser") is True,
        "admin readback",
    )
    empty = client.get("/api/v1/devices/", headers=headers)
    require(
        empty.status_code == 200 and empty.json().get("count") == 0,
        "owned empty fixture DB",
    )
    fixture = {
        "code": "image-proof-device",
        "name": "Disposable image HTTP fixture",
        "kind": "temperature_sensor",
    }
    created = client.post("/api/v1/devices/", json=fixture, headers=headers)
    require(
        created.status_code == 201
        and created.json().get("connectivity") == "unknown"
        and created.json().get("is_simulated") is True,
        "register simulated unknown device",
    )
    device_id = created.json().get("id")
    require(isinstance(device_id, str), "device identity")
    require(
        client.post("/api/v1/devices/", json=fixture, headers=headers).status_code
        == 409,
        "duplicate refusal",
    )
    page = client.get("/api/v1/devices/", headers=headers)
    require(
        page.status_code == 200 and page.json().get("count") == 1,
        "duplicate did not increase total",
    )
    detail = client.get("/api/v1/devices/" + device_id, headers=headers)
    require(
        detail.status_code == 200 and detail.json().get("id") == device_id,
        "saved device detail",
    )
    return {
        "status": "archive_image_default_cmd_http_smoke_passed_not_E1",
        "source": source,
        "checks": [
            "compiled_html_and_javascript",
            "default_cmd_http_health",
            "anonymous_401",
            "real_login_admin",
            "register_201_unknown",
            "duplicate_409_total_one",
            "saved_detail",
        ],
        "not_verified": [
            "browser interaction",
            "formal B/C/A04 chain",
            "MQTT continuous workers",
            "E1",
            "second computer",
            "real notifications",
            "final submission",
        ],
    }


def main(only_preflight: bool = False) -> None:
    source = preflight()
    if only_preflight:
        return
    with httpx.Client(
        base_url="http://application:8000",
        timeout=5,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                response = client.get("/api/v1/utils/health-check/")
                if response.status_code == 200 and response.json() is True:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("Application image default CMD did not become healthy")
        report = prove(client, source)
    with Path("/results/application-http.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report))  # noqa: T201 -- bounded synthetic evidence, never token


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    main(parser.parse_args().preflight)

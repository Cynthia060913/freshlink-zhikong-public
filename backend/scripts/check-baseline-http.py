"""Real process/HTTP baseline proof, Docker-only and disposable database only."""

import argparse
import json
import logging
import os
import platform
import re
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4


def require(condition: bool, reason: str) -> None:
    """A PASS report must never depend on removable assert statements."""
    if not condition:
        raise RuntimeError("Baseline HTTP verification failed: " + reason)


def request(base, path, *, data=None, token=None, form=False):
    headers = {
        "Content-Type": (
            "application/x-www-form-urlencoded" if form else "application/json"
        )
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None
    if data is not None:
        body = (urlencode(data) if form else json.dumps(data)).encode()
    query = Request(base + path, data=body, headers=headers)
    try:
        with urlopen(query, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def main(*, preflight=False):
    if not Path("/.dockerenv").is_file():
        raise RuntimeError("Docker-only proof; do not run in host Python")
    source = os.environ.get("BASELINE_SOURCE_COMMIT", "")
    scope = os.environ.get("BASELINE_PROOF_SCOPE", "")
    dirty = os.environ.get("BASELINE_SOURCE_DIRTY", "")
    if not re.fullmatch("[0-9a-f]{40}", source):
        raise RuntimeError("Exact source commit is required")
    if scope not in {"candidate", "published-main"} or dirty not in {"0", "1"}:
        raise RuntimeError("Invalid source evidence")
    if scope == "published-main" and dirty != "0":
        raise RuntimeError("A dirty worktree cannot prove published main")

    from app.core.config import settings

    url = urlparse(str(settings.DATABASE_URL))
    if (
        url.hostname != "test-db"
        or url.path != "/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
    ):
        raise RuntimeError("Refusing non-disposable database")
    if preflight:
        logging.info("PASS disposable-only preflight before any migration")
        return

    from app.core.db import engine

    with engine.connect() as connection:
        heads = (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            .scalars()
            .all()
        )
        if len(heads) != 1:
            raise RuntimeError("Exactly one migration head is required")
        pg = connection.exec_driver_sql("SHOW server_version").scalar_one()

    base = "http://127.0.0.1:18080" + settings.API_V1_STR
    target = Path("/results/baseline-http.json")
    log_path = Path("/results/baseline-uvicorn.log")
    with log_path.open("x", encoding="utf-8") as log:
        server = subprocess.Popen(
            [
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "18080",
                "--no-access-log",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("Disposable server stopped before health")
                try:
                    status, value = request(base, "/utils/health-check/")
                    if status == 200 and value is True:
                        break
                except URLError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Disposable server health timed out")

            require(request(base, "/devices/")[0] == 401, "anonymous device refusal")
            status, login = request(
                base,
                "/login/access-token",
                form=True,
                data={
                    "username": settings.FIRST_SUPERUSER,
                    "password": settings.FIRST_SUPERUSER_PASSWORD,
                },
            )
            require(
                status == 200 and isinstance(login.get("access_token"), str),
                "password login",
            )
            token = login["access_token"]
            status, actor = request(base, "/users/me", token=token)
            require(
                status == 200 and actor["is_active"] and actor["is_superuser"],
                "active admin readback",
            )
            status, empty = request(base, "/devices/", token=token)
            require(status == 200 and empty["count"] == 0, "empty device list")
            fixture = {
                "code": "baseline-" + uuid4().hex[:12],
                "name": "Disposable baseline HTTP fixture",
                "kind": "temperature_sensor",
            }
            status, saved = request(base, "/devices/", data=fixture, token=token)
            require(
                status == 201 and saved["connectivity"] == "unknown",
                "register unknown device",
            )
            require(
                request(base, "/devices/", data=fixture, token=token)[0] == 409,
                "duplicate device refusal",
            )
            status, page = request(base, "/devices/", token=token)
            require(
                status == 200 and page["count"] == 1, "device count after duplicate"
            )
            require(page["data"][0]["id"] == saved["id"], "device list identity")
            status, detail = request(base, "/devices/" + saved["id"], token=token)
            require(
                status == 200 and detail["connectivity"] == "unknown",
                "device detail readback",
            )
            report = {
                "phase": "isolated_baseline_real_http_passed",
                "source_commit": source,
                "source_worktree_dirty": dirty == "1",
                "scope": scope,
                "requested_by": os.environ.get("BASELINE_PROOF_ACTOR", "local"),
                "python_version": platform.python_version(),
                "postgresql_version": pg,
                "migration_head": heads[0],
                "checks": [
                    "real_uvicorn_process_health",
                    "anonymous_401",
                    "password_login",
                    "active_admin_readback",
                    "register_201_unknown",
                    "duplicate_409_count_still_one",
                    "authenticated_list_detail",
                ],
                "not_tested": [
                    "browser_interaction",
                    "personal_local_machine",
                    "B_C_formal_integration",
                    "real_notifications",
                    "E1",
                ],
            }
            with target.open("x", encoding="utf-8") as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
            logging.info(
                "PASS real process, health, login, device API; browser/E1 untested"
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    main(preflight=parser.parse_args().preflight)

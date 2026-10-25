"""Owned MQTT/C/B to persistent notification runner proof with synthetic sinks.

No actual SMTP/Feishu request, terminal receipt, recovery or full E1 claim.
The existing four-process/TCP proof creates the committed business event;
fresh child processes then exercise B's actual worker and PG attempt store.
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.modules.alerts.models import AlertEvent, NotificationAttempt
from app.modules.alerts.notifications.runner import run_dispatch_worker
from app.modules.alerts.notifications.transport import (
    DeliveryOutcome,
    NotificationKind,
    NotificationResult,
    NotificationTransport,
)


def require_owned_environment():
    database = urlparse(str(settings.DATABASE_URL))
    if (
        database.hostname != "test-db"
        or database.path != "/freshlink_test"
        or settings.PROJECT_NAME != "FreshLink-test"
        or settings.FASTAPI_ENV != "development"
        or os.environ.get("FRESHLINK_GATED_PROOF") != "1"
        or settings.SMTP_HOST
        or settings.EMAILS_FROM_EMAIL
        or os.environ.get("FEISHU_WEBHOOK_URL")
        or os.environ.get("FEISHU_WEBHOOK_SECRET")
    ):
        raise RuntimeError(
            "Owned disposable environment without real channels required"
        )


def attempts():
    with engine.connect() as connection:
        return list(
            connection.execute(select(NotificationAttempt.__table__)).mappings()
        )


def child(mode):
    require_owned_environment()
    calls = []

    class SyntheticSink(NotificationTransport):
        def __init__(self, kind):
            self.kind = kind

        def send(self, envelope):
            # A pure in-process sink: no SMTP, HTTP or other network library.
            calls.append(
                {"channel": self.kind.value, "event_id": str(envelope.source_event_id)}
            )
            accepted = not (
                mode == "partial" and self.kind is NotificationKind.APP_PUSH
            )
            return NotificationResult(
                attempt_id=uuid4(),
                transport=self.kind,
                outcome=(
                    DeliveryOutcome.ACCEPTED_BY_SERVICE
                    if accepted
                    else DeliveryOutcome.TRANSIENT_FAILURE
                ),
                delivered=accepted,
                detail="SYNTHETIC_SINK_ONLY_NOT_TERMINAL_RECEIPT",
            )

    @contextmanager
    def factory():
        with Session(engine) as session:
            yield session

    reports = run_dispatch_worker(
        factory,
        transports=[SyntheticSink(kind) for kind in NotificationKind],
        max_attempts=1,
        min_attempt_age_s=0,
        poll_interval_s=0,
        max_cycles=1,
        dry_run=mode == "dry",
    )
    assert len(reports) == 1 and reports[0].interrupted is None
    result = {
        "mode": mode,
        "pid": os.getpid(),
        "pending": [str(value) for value in reports[0].pending],
        "calls": calls,
        "synthetic_sinks_only": True,
        "real_notifications_sent": 0,
    }
    with Path(f"/results/notification-child-{mode}.json").open(
        "x", encoding="utf-8"
    ) as output:
        json.dump(result, output, indent=2)
    engine.dispose()


def run():
    require_owned_environment()
    report_file = Path("/results/notification-composition.json")
    if report_file.exists() or attempts():
        raise RuntimeError("Refusing old proof or notification attempts")
    # Reuse, do not emulate, A's actual four CLI processes, MQTT and TCP API.
    spec = importlib.util.spec_from_file_location(
        "owned_runtime_composition",
        Path(__file__).with_name("check-runtime-composition.py"),
    )
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    runtime.run()
    with engine.connect() as connection:
        events = list(connection.execute(select(AlertEvent.__table__)).mappings())
    assert len(events) == 1 and not attempts()
    event_id = events[0]["id"]
    # Actual A startup module, default dry-run, no configured real channel.
    profile = Path("/results/notification-empty-profile.json")
    with profile.open("x", encoding="utf-8") as output:
        json.dump({"recipients": []}, output)
    preview = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "app.modules.iot.notification_service",
            "--config",
            str(profile),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert preview.returncode == 0
    startup = json.loads(preview.stdout)
    assert startup["dry_run"] is True and startup["configured_channels"] == []
    assert startup["pending_count"] == 0 and not attempts()
    children = []
    for mode in ("dry", "partial", "resume", "repeat"):
        with Path(f"/results/notification-{mode}.log").open(
            "x", encoding="utf-8"
        ) as log:
            completed = subprocess.run(
                [sys.executable, "-B", str(Path(__file__).resolve()), "--child", mode],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
            )
        assert completed.returncode == 0, (
            "Owned notification child failed; inspect private log"
        )
        detail = json.loads(
            Path(f"/results/notification-child-{mode}.json").read_text(encoding="utf-8")
        )
        children.append(detail)
        rows = attempts()
        assert all(row["alert_event_id"] == event_id for row in rows)
        assert all(
            row["detail"] == "SYNTHETIC_SINK_ONLY_NOT_TERMINAL_RECEIPT" for row in rows
        )
        assert len(rows) == {"dry": 0, "partial": 2, "resume": 3, "repeat": 3}[mode]
        expected_calls = {
            "dry": [],
            "partial": ["smtp_email", "app_push"],
            "resume": ["app_push"],
            "repeat": [],
        }[mode]
        assert [call["channel"] for call in detail["calls"]] == expected_calls
        if mode == "repeat":
            assert detail["pending"] == []
    rows = attempts()
    assert sum(row["delivered"] for row in rows) == 2
    assert len({child["pid"] for child in children}) == 4
    result = {
        "phase": "actual_runtime_to_persistent_synthetic_notifications_passed",
        "source_revision": os.environ["GATED_SOURCE_REVISION"],
        "source_dirty": os.environ["GATED_SOURCE_DIRTY"] == "1",
        "checked_at": datetime.now(UTC).isoformat(),
        "actual_runtime_processes": ["receive", "store", "evaluate", "deliver-high"],
        "actual_tcp_http_api": True,
        "actual_candidate_c01_migration": True,
        "b_event_id": str(event_id),
        "source_event_id": events[0]["source_event_id"],
        "notification_child_pids": [child["pid"] for child in children],
        "actual_pg_dispatch_lease": True,
        "actual_a_entry_default_dry_run": True,
        "dry_run_zero_attempts": True,
        "restart_sends_only_missing_channel": True,
        "third_delivery_process_no_resend": True,
        "persistent_attempt_rows": len(rows),
        "synthetic_sinks_only": True,
        "notifications_sent": 0,
        "complete_e1": False,
        "not_tested": [
            "real SMTP/Feishu service",
            "terminal receipts",
            "recovery",
            "browser",
            "second machine",
            "release approval",
        ],
    }
    engine.dispose()
    with report_file.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
    sys.stdout.write(
        "PASS actual MQTT/C/B to persistent synthetic notifications; NOT FULL E1\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", choices=("dry", "partial", "resume", "repeat"))
    arguments = parser.parse_args()
    if arguments.child:
        child(arguments.child)
    else:
        run()

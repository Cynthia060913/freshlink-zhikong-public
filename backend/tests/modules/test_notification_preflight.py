"""Read-only PG inventory and redaction checks; synthetic channels only."""

import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from app.core.db import engine
from app.modules.alerts.models.event import AlertEvent, NotificationAttempt
from app.modules.iot import notification_preflight as preflight

CHANNELS = ("smtp_email", "app_push")


@pytest.fixture
def private_pg():
    if os.environ.get("B01_LIVE_PG_TESTS") != "1":
        pytest.skip("Requires explicitly enabled disposable PostgreSQL")
    assert engine.dialect.name == "postgresql"
    schema = "notification_preflight_" + uuid4().hex
    assert schema.startswith("notification_preflight_") and schema.isidentifier()
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        SQLModel.metadata.create_all(scoped)
        yield scoped
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def seed(database, source="synthetic-expected"):
    event = AlertEvent(
        source_event_id=source,
        kind="temperature_high",
        severity="critical",
        sampled_at=datetime.now(UTC),
        content_digest="0" * 64,
        payload_json="{}",
    )
    with Session(database) as session:
        session.add(event)
        session.commit()
        session.refresh(event)
        return event.id


def test_exact_single_event_is_readiness_only_without_writes(private_pg):
    seed(private_pg)
    report = preflight.inspect_inventory(
        private_pg, source_event_id="synthetic-expected", channels=CHANNELS
    )
    assert report["status"] == "readiness_only"
    assert report["attempt_count"] == 0
    assert report["read_only_transaction"] is True
    assert report["send_authorized"] is False
    assert report["terminal_receipt_confirmed"] is False
    with private_pg.connect() as connection:
        assert connection.scalar(text("SHOW transaction_read_only")) == "off"


def test_unexpected_older_event_is_blocked_not_hidden_by_limit_one(private_pg):
    seed(private_pg, "synthetic-older-unapproved")
    seed(private_pg)
    report = preflight.inspect_inventory(
        private_pg, source_event_id="synthetic-expected", channels=CHANNELS
    )
    assert report["status"] == "blocked"
    assert report["event_count"] == report["pending_count_capped_at_two"] == 2
    assert "requires_fresh_single_event_database" in report["reasons"]


def test_missing_source_and_prior_attempt_are_blocked(private_pg):
    event_id = seed(private_pg)
    with Session(private_pg) as session:
        session.add(
            NotificationAttempt(
                alert_event_id=event_id,
                channel="smtp_email",
                outcome="timeout",
                delivered=False,
            )
        )
        session.commit()
    report = preflight.inspect_inventory(
        private_pg, source_event_id="synthetic-wrong", channels=CHANNELS
    )
    assert report["status"] == "blocked"
    assert "expected_source_not_found" in report["reasons"]
    assert "existing_attempts_require_review" in report["reasons"]


def test_b_precheck_original_status_sql_is_invalid_but_readback_is_safe(private_pg):
    from sqlalchemy.exc import ProgrammingError

    seed(private_pg)
    with private_pg.connect() as connection, pytest.raises(ProgrammingError):
        connection.execute(text("SELECT status FROM alerts_event"))
    assert (
        preflight.inspect_inventory(
            private_pg, source_event_id="synthetic-expected", channels=CHANNELS
        )["status"]
        == "readiness_only"
    )


@pytest.mark.parametrize("channels", [(), ("smtp_email",), ("smtp_email",) * 2])
def test_missing_or_duplicate_channel_refused_before_connection(channels):
    database = Mock()
    database.dialect.name = "postgresql"
    with pytest.raises(ValueError):
        preflight.inspect_inventory(
            database, source_event_id="synthetic-expected", channels=channels
        )
    database.execution_options.assert_not_called()


def test_private_config_error_does_not_leak_value(tmp_path, capsys):
    profile = tmp_path / "private.json"
    profile.write_text('{"smtp_password":"SYNTHETIC_SECRET_MUST_NOT_LEAK"}')
    assert preflight.main(["--config", str(profile), "--source-event-id", "x"]) == 2
    output = capsys.readouterr()
    assert "SYNTHETIC_SECRET_MUST_NOT_LEAK" not in output.out + output.err


def test_main_never_calls_transport_send(tmp_path, monkeypatch, capsys):
    profile = tmp_path / "private.json"
    profile.write_text(json.dumps({"recipients": ["operator@example.com"]}))
    transports = [
        SimpleNamespace(kind=SimpleNamespace(value=kind), send=Mock())
        for kind in CHANNELS
    ]
    monkeypatch.setattr(preflight, "build_transports", lambda: transports)
    monkeypatch.setattr(
        preflight, "inspect_inventory", lambda *_args, **_kwargs: {"status": "blocked"}
    )
    assert preflight.main(["--config", str(profile), "--source-event-id", "x"]) == 1
    for transport in transports:
        transport.send.assert_not_called()
    assert "operator@example.com" not in capsys.readouterr().out


def test_full_cli_with_real_channel_objects_reads_pg_without_sending(
    private_pg, tmp_path, monkeypatch, capsys
):
    from app.modules.alerts.notifications.feishu import (
        FeishuWebhookConfig,
        FeishuWebhookTransport,
    )
    from app.modules.alerts.notifications.smtp import SMTPConfig, SMTPTransport

    seed(private_pg)
    transports = [
        SMTPTransport(
            SMTPConfig(
                host="smtp.example.invalid",
                port=465,
                timeout_seconds=2,
                use_tls=False,
                use_ssl=True,
                from_email="sender@example.com",
            )
        ),
        FeishuWebhookTransport(
            FeishuWebhookConfig(
                webhook_url="https://open.feishu.cn"
                + "/open-apis/bot/v2/hook/"
                + "SYNTHETIC_NOT_A_VALID_TOKEN"
            )
        ),
    ]
    calls = Mock(side_effect=AssertionError("Preflight must not send"))
    monkeypatch.setattr(SMTPTransport, "send", calls)
    monkeypatch.setattr(FeishuWebhookTransport, "send", calls)
    monkeypatch.setattr(preflight, "build_transports", lambda: transports)
    monkeypatch.setattr(
        preflight, "create_engine", lambda *_args, **_kwargs: private_pg
    )
    profile = tmp_path / "private.json"
    profile.write_text(json.dumps({"recipients": ["operator@example.com"]}))
    assert (
        preflight.main(
            ["--config", str(profile), "--source-event-id", "synthetic-expected"]
        )
        == 0
    )
    calls.assert_not_called()
    report = json.loads(capsys.readouterr().out)
    assert report["configured_channels"] == sorted(CHANNELS)
    assert report["attempt_count"] == 0 and report["send_authorized"] is False
    with Session(private_pg) as session:
        assert not session.exec(select(NotificationAttempt)).all()

"""CLI negative cases; actual PG paths live in test_gated_recovery_observation."""

from unittest.mock import MagicMock

import pytest

from app.modules.iot import recovery_observation_service as service


def test_private_configuration_failure_is_redacted(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text('{"password":"SYNTHETIC_PRIVATE_VALUE"}', encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["prepare", "--config", str(config), "--original-source-event-id", "bad"],
    )
    factory = MagicMock()
    monkeypatch.setattr(service, "create_engine", factory)
    with pytest.raises(SystemExit) as caught:
        service.main()
    assert "SYNTHETIC" not in str(caught.value)
    assert str(config) not in str(caught.value)
    factory.assert_not_called()


def test_held_result_is_not_reported_as_complete(tmp_path, monkeypatch, capsys):
    from uuid import uuid4

    from tests.modules.test_telemetry_service import settings_for

    config = tmp_path / "scope.json"
    config.write_text(
        settings_for(uuid4(), uuid4()).model_dump_json(), encoding="utf-8"
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare",
            "--config",
            str(config),
            "--original-source-event-id",
            str(uuid4()),
        ],
    )
    engine = MagicMock()
    monkeypatch.setattr(service, "create_engine", lambda *_a, **_k: engine)
    monkeypatch.setattr(service, "prepare_once", lambda *_a, **_k: {"held": 1})
    with pytest.raises(SystemExit) as caught:
        service.main()
    assert caught.value.code == 2
    assert '"held": 1' in capsys.readouterr().out
    engine.dispose.assert_called_once()

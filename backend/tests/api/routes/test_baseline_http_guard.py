"""Negative guards must fail before connecting to a database or starting a server."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

_PATH = Path(__file__).resolve().parents[3] / "scripts/check-baseline-http.py"
_SPEC = importlib.util.spec_from_file_location("baseline_http_proof", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
proof = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(proof)


def _source(monkeypatch):
    monkeypatch.setenv("BASELINE_SOURCE_COMMIT", "a" * 40)
    monkeypatch.setenv("BASELINE_PROOF_SCOPE", "candidate")
    monkeypatch.setenv("BASELINE_SOURCE_DIRTY", "0")


def test_host_execution_refused_before_any_app_import():
    with mock.patch.object(proof.Path, "is_file", return_value=False):
        with pytest.raises(RuntimeError, match="Docker-only"):
            proof.main()


@pytest.mark.parametrize("source", ["", "a03", "x" * 40, "a" * 39, "a" * 41])
def test_exact_source_required_before_database(monkeypatch, source):
    _source(monkeypatch)
    monkeypatch.setenv("BASELINE_SOURCE_COMMIT", source)
    with mock.patch.object(proof.Path, "is_file", return_value=True):
        with pytest.raises(RuntimeError, match="Exact source"):
            proof.main()


def test_dirty_candidate_cannot_be_reported_as_published_main(monkeypatch):
    _source(monkeypatch)
    monkeypatch.setenv("BASELINE_PROOF_SCOPE", "published-main")
    monkeypatch.setenv("BASELINE_SOURCE_DIRTY", "1")
    with mock.patch.object(proof.Path, "is_file", return_value=True):
        with pytest.raises(RuntimeError, match="dirty worktree"):
            proof.main()


def test_non_disposable_database_refused_without_connecting(monkeypatch):
    _source(monkeypatch)
    engine = mock.MagicMock()
    settings = SimpleNamespace(
        DATABASE_URL="postgresql://example:unused@outside/app",
        PROJECT_NAME="FreshLink-test",
    )
    with (
        mock.patch.object(proof.Path, "is_file", return_value=True),
        mock.patch("app.core.config.settings", settings),
        mock.patch("app.core.db.engine", engine),
    ):
        with pytest.raises(RuntimeError, match="Refusing non-disposable"):
            proof.main()
    engine.connect.assert_not_called()


def test_preflight_validates_but_never_connects_or_starts_process(monkeypatch):
    _source(monkeypatch)
    settings = SimpleNamespace(
        DATABASE_URL="postgresql://example:unused@test-db/freshlink_test",
        PROJECT_NAME="FreshLink-test",
    )
    with (
        mock.patch.object(proof.Path, "is_file", return_value=True),
        mock.patch("app.core.config.settings", settings),
        mock.patch("app.core.db.engine") as engine,
        mock.patch.object(proof.subprocess, "Popen") as process,
    ):
        proof.main(preflight=True)
    engine.connect.assert_not_called()
    process.assert_not_called()

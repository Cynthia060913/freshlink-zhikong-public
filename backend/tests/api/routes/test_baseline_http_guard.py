"""Negative guards must fail before connecting to a database or starting a server."""

import importlib.util
import subprocess
import sys
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


@pytest.mark.parametrize("bad_index", range(1, 9))
def test_optimized_bad_http_never_writes_pass_report(monkeypatch, tmp_path, bad_index):
    """Execute main under -O, including login then bad device response paths."""
    _source(monkeypatch)
    code = f"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
spec = importlib.util.spec_from_file_location('optimized_proof', {str(_PATH)!r})
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
output = Path({str(tmp_path)!r})
def mapped_path(value):
    if value == '/.dockerenv':
        return SimpleNamespace(is_file=lambda: True)
    return output / Path(value).name if str(value).startswith('/results/') else Path(value)
settings = SimpleNamespace(
    DATABASE_URL='postgresql://example:unused@test-db/freshlink_test',
    PROJECT_NAME='FreshLink-test', API_V1_STR='/api/v1',
    FIRST_SUPERUSER='admin@example.com', FIRST_SUPERUSER_PASSWORD='test-only',
)
connection = MagicMock()
connection.exec_driver_sql.side_effect = [
    SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ['a03_control_storage'])),
    SimpleNamespace(scalar_one=lambda: '18.6'),
]
engine = MagicMock()
engine.connect.return_value.__enter__.return_value = connection
process = MagicMock()
process.poll.return_value = None
process.terminate.side_effect = lambda: (output / 'terminated').write_text('yes')
responses = [
    (200, True),
    (401, {{}}),
    (200, {{'access_token': 'synthetic-token'}}),
    (200, {{'is_active': True, 'is_superuser': True}}),
    (200, {{'count': 0, 'data': []}}),
    (201, {{'id': 'fixture-device', 'connectivity': 'unknown'}}),
    (409, {{}}),
    (200, {{'count': 1, 'data': [{{'id': 'fixture-device'}}]}}),
    (200, {{'id': 'fixture-device', 'connectivity': 'unknown'}}),
]
responses[{bad_index}] = (503, {{}})
with patch.object(proof, 'Path', mapped_path), patch('app.core.db.engine', engine), \\
     patch('app.core.config.settings', settings), \\
     patch.object(proof.subprocess, 'Popen', return_value=process), \\
     patch.object(proof, 'request', side_effect=responses):
    proof.main()
"""
    result = subprocess.run(
        [sys.executable, "-O", "-c", code],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0
    assert "Baseline HTTP verification failed" in result.stderr
    assert not (tmp_path / "baseline-http.json").exists()
    assert (tmp_path / "terminated").read_text() == "yes"

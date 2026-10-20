"""Read-only final-image source audit, not application or privacy acceptance."""

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath


def verify(manifest: dict, revision: str, backend: Path) -> dict:
    if (
        not re.fullmatch(r"[0-9a-f]{40}", revision)
        or manifest.get("revision") != revision
    ):
        raise RuntimeError("Image source identity is missing or inconsistent")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise RuntimeError("Source manifest exceeds bounded audit scope")
    expected = {}
    for row in rows:
        path = row.get("path", "")
        parts = PurePosixPath(path).parts
        if not parts or ".." in parts or path.startswith("/") or "\\" in path:
            raise RuntimeError("Unsafe manifest path")
        if path.startswith(("backend/app/", "backend/scripts/")) or path in {
            "backend/pyproject.toml",
            "backend/alembic.ini",
        }:
            relative = path.removeprefix("backend/")
            if relative in expected or not re.fullmatch(
                r"[0-9a-f]{64}", row.get("sha256", "")
            ):
                raise RuntimeError("Duplicate path or invalid source hash")
            expected[relative] = row["sha256"]
    if not {"app/main.py", "pyproject.toml", "alembic.ini"}.issubset(expected):
        raise RuntimeError("Incomplete backend source manifest")
    actual = set()
    for prefix in ("app", "scripts"):
        directory = backend / prefix
        if not directory.is_dir() or directory.is_symlink():
            raise RuntimeError("Missing or symlinked runtime source directory")
        for path in directory.rglob("*"):
            relative = path.relative_to(backend).as_posix()
            if relative.startswith("app/frontend/") or "__pycache__" in path.parts:
                continue
            if path.is_symlink():
                raise RuntimeError("Unexpected runtime source symlink")
            if path.is_file() and not path.name.endswith(".pyc"):
                actual.add(relative)
                if len(actual) > 10000:
                    raise RuntimeError("Runtime source exceeds bounded audit scope")
    actual.update(
        name for name in ("pyproject.toml", "alembic.ini") if (backend / name).is_file()
    )
    if actual != set(expected):
        raise RuntimeError("Runtime source file set differs from the pinned Git tree")
    for relative, digest in expected.items():
        path = backend / relative
        if path.is_symlink() or path.stat().st_size > 20 * 1024 * 1024:
            raise RuntimeError("Unsafe source file or size")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Runtime source byte mismatch")
    frontend = backend / "app/frontend"
    if not (frontend / "index.html").is_file():
        raise RuntimeError("Built frontend index is missing")
    bundle = []
    for path in frontend.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("Unexpected frontend symlink")
        if path.is_file():
            if len(bundle) >= 10000 or path.stat().st_size > 20 * 1024 * 1024:
                raise RuntimeError("Frontend exceeds bounded audit scope")
            bundle.append(path)
    if not any(path.suffix == ".js" for path in bundle):
        raise RuntimeError("Built frontend JavaScript is missing")
    return {
        "status": "verified_image_source_and_frontend_presence_not_E1",
        "source": revision,
        "backend_files_verified": len(expected),
        "frontend_files_present": len(bundle),
        "not_verified": [
            "application startup",
            "formal migrations",
            "E1",
            "second computer",
            "notifications",
            "full image security audit",
        ],
    }


def main() -> None:
    if not Path("/.dockerenv").is_file():
        raise RuntimeError("This entrypoint is for a disposable container only")
    source = Path("/proof/source-manifest.json")
    if source.stat().st_size > 2 * 1024 * 1024:
        raise RuntimeError("Oversized manifest")
    manifest = json.loads(source.read_text(encoding="utf-8-sig"))
    result = verify(
        manifest, os.environ.get("FRESHLINK_EXPECTED_SOURCE", ""), Path("/app/backend")
    )
    print(json.dumps(result))  # noqa: T201 -- bounded evidence, never secrets


if __name__ == "__main__":
    main()

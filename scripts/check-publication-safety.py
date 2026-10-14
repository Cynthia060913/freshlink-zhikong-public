"""Read-only text guard for a Git snapshot; never print matched secret values."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "github_credential": re.compile(
        rb"\b(?:github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,})\b"
    ),
    "private_key": re.compile(
        rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    ),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "google_api_key": re.compile(rb"\bAIza[A-Za-z0-9_-]{35}\b"),
    "slack_credential": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
}


def private_path(name: str) -> bool:
    """The inherited root .env is a demo; real values must not be put there."""
    parts = name.replace("\\", "/").lower().split("/")
    leaf = parts[-1]
    examples = leaf.endswith((".example", ".sample", ".template"))
    return (
        parts[0] == "work"
        or "__pycache__" in parts
        or leaf in {"id_rsa", "id_ed25519"}
        or leaf.endswith((".pyc", ".sqlite", ".sqlite3", ".db"))
        or (leaf.startswith(".env.") and not examples)
    )


def findings(name: str, data: bytes, secrets: dict[str, bytes]) -> list[dict]:
    """Return only filenames, rules and line numbers, not matching content."""
    result = (
        [{"path": name, "rule": "private_path", "line": 0}]
        if private_path(name)
        else []
    )
    if b"\0" in data:
        return result
    for rule, pattern in PATTERNS.items():
        for match in pattern.finditer(data):
            result.append(
                {
                    "path": name,
                    "rule": rule,
                    "line": data.count(b"\n", 0, match.start()) + 1,
                }
            )
    for key, value in secrets.items():
        position = data.find(value)
        if position >= 0:
            result.append(
                {
                    "path": name,
                    "rule": "local_secret_match",
                    "key": key,
                    "line": data.count(b"\n", 0, position) + 1,
                }
            )
    return result


def git(root: Path, *args: str, input_data: bytes | None = None) -> bytes:
    process = subprocess.run(
        ["git", *args], cwd=root, input=input_data, capture_output=True, check=False
    )
    if process.returncode:
        # Git diagnostics can contain sensitive filenames; omit raw stderr.
        raise RuntimeError("Git read failed; check the repository and snapshot.")
    return process.stdout


def local_secrets(root: Path) -> dict[str, bytes]:
    config = root / ".env.freshlink"
    result = {}
    if config.is_file():
        for line in config.read_bytes().splitlines():
            if line.startswith(b"#") or b"=" not in line:
                continue
            key, value = line.split(b"=", 1)
            key = key.strip().decode("utf-8", errors="replace")
            value = value.strip().strip(b"\"'")
            if re.search(r"PASSWORD|SECRET|TOKEN|API_KEY", key) and len(value) >= 12:
                result[key] = value
    return result


def snapshot(root: Path, staged: bool, revision: str) -> list[tuple[str, str]]:
    if staged:
        entries = git(root, "ls-files", "--stage", "-z").split(b"\0")
    else:
        commit = git(
            root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}"
        )
        entries = git(root, "ls-tree", "-r", "-z", commit.decode().strip()).split(b"\0")
    result = []
    for entry in entries:
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        fields = metadata.split()
        if staged and fields[2] != b"0":
            raise RuntimeError("Resolve index conflicts before checking.")
        if fields[0] == b"160000":
            raise RuntimeError("Submodule content requires a separate audit.")
        object_id = fields[1] if staged else fields[2]
        result.append(
            (object_id.decode("ascii"), name.decode("utf-8", errors="replace"))
        )
    return result


def check(root: Path, staged: bool, revision: str) -> dict:
    entries = snapshot(root, staged, revision)
    secrets = local_secrets(root)
    result = {
        "snapshot": "index" if staged else revision,
        "files": len(entries),
        "binary_not_text_scanned": 0,
        "findings": [],
    }
    for start in range(0, len(entries), 100):
        group = entries[start : start + 100]
        batch = git(
            root,
            "cat-file",
            "--batch",
            input_data=("\n".join(item[0] for item in group) + "\n").encode(),
        )
        offset = 0
        for object_id, name in group:
            newline = batch.index(b"\n", offset)
            actual_id, kind, length = batch[offset:newline].split()
            if actual_id.decode() != object_id or kind != b"blob":
                raise RuntimeError("Unexpected Git object; content audit incomplete.")
            size = int(length)
            data = batch[newline + 1 : newline + 1 + size]
            offset = newline + size + 2
            if b"\0" in data:
                result["binary_not_text_scanned"] += 1
            result["findings"].extend(findings(name, data, secrets))
    result["passed"] = not result["findings"]
    result["limits"] = [
        "Text patterns only; not a complete security audit.",
        "Does not scan history, comments, Actions archives or images.",
        "Does not establish credential revocation or consent to publish personal data.",
    ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--staged",
        action="store_true",
        help="Scan the full index, including unchanged tracked files.",
    )
    selection.add_argument(
        "--revision", default="HEAD", help="Commit snapshot to check; default HEAD."
    )
    args = parser.parse_args()
    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        report = check(root, args.staged, args.revision)
    except (OSError, RuntimeError, ValueError):
        sys.stdout.write(
            json.dumps(
                {
                    "passed": False,
                    "error": "Read-only check incomplete; verify Git and input.",
                }
            )
            + "\n"
        )
        return 2
    sys.stdout.write(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

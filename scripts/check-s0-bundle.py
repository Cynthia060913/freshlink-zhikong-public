"""Validate the pinned S0 proposal; this never signs for another role."""

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "contracts/s0-signoff-manifest.json"
EXPECTED_IDS = {"F01", "F02", "F03", "F04", "F05", "F06", "F03-recovery"}


def validate_manifest(document: dict) -> None:
    if document.get("contract_version") != "0.2.0-rc3":
        raise ValueError("unexpected contract version")
    if document.get("status") != "proposed_not_three_party_signed":
        raise ValueError("this checker does not authorize a signed status")
    if document.get("repository") != "Cynthia060913/freshlink-zhikong-public":
        raise ValueError("wrong repository")
    if not re.fullmatch(r"[0-9a-f]{40}", document.get("common_baseline", "")):
        raise ValueError("baseline must be a complete SHA")
    if document.get("normative_document") != "contracts/S0-SIGNOFF.md":
        raise ValueError("wrong normative document")
    sources = document.get("sources")
    if not isinstance(sources, list) or len(sources) != len(EXPECTED_IDS):
        raise ValueError("missing or duplicate contract source")
    if {item.get("id") for item in sources} != EXPECTED_IDS:
        raise ValueError("missing or duplicate contract source")
    for source in sources:
        if not re.fullmatch(r"[0-9a-f]{40}", source.get("revision", "")):
            raise ValueError("source must pin a complete SHA, not a branch")
        paths = source.get("paths")
        if not isinstance(paths, list) or not paths or len(paths) != len(set(paths)):
            raise ValueError("source paths must be nonempty and unique")
        for path in paths:
            if (
                not isinstance(path, str)
                or "\\" in path
                or ":" in path
                or ".." in PurePosixPath(path).parts
                or PurePosixPath(path).is_absolute()
                or not path.startswith(("contracts/", "docs/", "backend/"))
                or PurePosixPath(path).suffix not in {".md", ".json", ".py"}
            ):
                raise ValueError("unsafe or unexpected source path")
    excluded = document.get("not_proof_of", [])
    if not {"three_party_acceptance", "real_notification_receipts"}.issubset(excluded):
        raise ValueError("acceptance and notification limits must remain explicit")


def resolve_sources(document: dict) -> int:
    """Read local Git objects only; no network, writes, or source execution."""
    count = 0
    for source in document["sources"]:
        for path in source["paths"]:
            result = subprocess.run(
                ["git", "show", f"{source['revision']}:{path}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                timeout=30,
            )
            if not result.stdout or len(result.stdout) > 524288:
                raise ValueError("source missing or exceeds verification bound")
            if path.endswith(".json"):
                json.loads(result.stdout)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolve-sources", action="store_true")
    args = parser.parse_args()
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    validate_manifest(document)
    if not (ROOT / document["normative_document"]).is_file():
        raise ValueError("normative document is missing")
    if args.resolve_sources:
        count = resolve_sources(document)
        print(f"PASS pinned S0 manifest and {count} local source blobs/JSON files")
    else:
        print("PASS S0 manifest structure; source Git objects not resolved")
    print("NOT PROOF: three-party signatures, implementation, notifications or E1")


if __name__ == "__main__":
    main()

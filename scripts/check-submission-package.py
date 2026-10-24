"""Read-only package consistency gate; never certifies runtime/receipt or sends."""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

ROLES = {
    "source_archive",
    "application_archive",
    "runbook",
    "design_document",
    "presentation",
    "video",
}
GATES = {
    "S0",
    "E1",
    "FLEET",
    "SECOND_MACHINE",
    "SMTP_RECEIPT",
    "FEISHU_RECEIPT",
    "M01",
    "M02",
    "M03",
    "M04",
    "M05",
    "PRIVACY",
    "SOURCE_LICENSES",
}
SUFFIXES = {
    "source_archive": {".zip"},
    "application_archive": {".tar", ".zip", ".gz"},
    "runbook": {".md", ".pdf", ".txt"},
    "design_document": {".docx", ".pdf", ".md"},
    "presentation": {".pptx"},
    "video": {".mp4", ".webm"},
}


class PackageError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise PackageError(code)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def relative_file(root, name):
    require(isinstance(name, str) and name, "invalid_relative_path")
    parts = PurePosixPath(name).parts
    require(
        not PurePosixPath(name).is_absolute()
        and "\\" not in name
        and ":" not in name
        and not re.search(r"[\x00-\x1f]", name)
        and not re.search(r'[<>"|?*]', name)
        and all(part not in {".", ".."} for part in parts)
        and all(not part.endswith((".", " ")) for part in parts)
        and "/".join(parts) == name,
        "unsafe_relative_path",
    )
    path = root.joinpath(*parts)
    for parent in (path, *path.parents):
        if parent == root:
            break
        require(not is_link(parent), "linked_package_entry")
    require(path.resolve().is_relative_to(root), "escaped_package_root")
    require(path.is_file(), "missing_package_file")
    return path


def is_link(path):
    # Reject Windows junctions/reparse points as well as POSIX symlinks.
    return path.is_symlink() or bool(
        getattr(path.lstat(), "st_file_attributes", 0) & 0x400
    )


def read_json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "duplicate_json_key")
            value[key] = item
        return value

    require(path.stat().st_size <= 2 * 1024 * 1024, "manifest_too_large")
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def verify(directory, manifest_name="submission-manifest.json"):
    supplied = Path(directory)
    require(supplied.is_dir() and not is_link(supplied), "invalid_package_root")
    root = supplied.resolve()
    manifest = read_json(relative_file(root, manifest_name))
    require(isinstance(manifest, dict), "invalid_manifest")
    require(
        set(manifest)
        == {"format", "source", "state", "algorithm_enabled", "files", "acceptance"},
        "unknown_manifest_fields",
    )
    require(manifest.get("format") == "freshlink-submission-v1", "wrong_format")
    source = manifest.get("source", "")
    require(
        isinstance(source, str) and re.fullmatch(r"[0-9a-f]{40}", source),
        "invalid_source",
    )
    require(
        manifest.get("state") == "ready_for_human_final_review", "candidate_not_ready"
    )
    require(
        manifest.get("algorithm_enabled") is False, "algorithm_needs_separate_review"
    )
    files = manifest.get("files")
    gates = manifest.get("acceptance")
    require(isinstance(files, list) and len(files) <= 500, "invalid_file_inventory")
    require(isinstance(gates, dict) and set(gates) == GATES, "missing_acceptance_gate")
    inventory = {}
    roles = set()
    for item in files:
        require(isinstance(item, dict), "invalid_inventory_entry")
        require(
            set(item) == {"role", "path", "source", "bytes", "sha256"},
            "unknown_inventory_fields",
        )
        role = item.get("role")
        require(role in ROLES | {"evidence", "supporting"}, "unknown_file_role")
        require(item.get("source") == source, "mixed_artifact_source")
        name = item.get("path")
        require(
            isinstance(name, str) and name not in inventory, "duplicate_inventory_path"
        )
        path = relative_file(root, name)
        require(
            type(item.get("bytes")) is int and item["bytes"] > 0, "invalid_file_size"
        )
        require(path.stat().st_size == item["bytes"], "file_size_mismatch")
        require(
            isinstance(item.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]),
            "invalid_file_hash",
        )
        require(digest(path) == item["sha256"], "file_hash_mismatch")
        if role in ROLES:
            require(path.suffix.lower() in SUFFIXES[role], "wrong_delivery_file_type")
            roles.add(role)
        inventory[name] = item
    require(roles == ROLES, "missing_required_material")
    for gate, value in gates.items():
        require(isinstance(value, dict), "invalid_acceptance_record")
        require(value.get("state") == "passed", "acceptance_not_passed")
        require(value.get("source") == source, "mixed_acceptance_source")
        require(value.get("reviewer") in {"A", "B", "C", "captain"}, "missing_reviewer")
        evidence = value.get("evidence")
        require(isinstance(evidence, list) and evidence, "missing_acceptance_evidence")
        require(
            all(isinstance(name, str) and name in inventory for name in evidence),
            "unlisted_acceptance_evidence",
        )
        if gate in {"SMTP_RECEIPT", "FEISHU_RECEIPT"}:
            require(
                value.get("consent_confirmed") is True, "notification_consent_missing"
            )
            require(
                value.get("terminal_received_confirmed") is True,
                "terminal_receipt_missing",
            )
        if gate == "SECOND_MACHINE":
            require(
                value.get("independent_computer_confirmed") is True,
                "second_machine_unconfirmed",
            )
    # No unrelated config, user DB, caches, symlinks or omitted files may slip in.
    walked = 0
    for folder, directories, names in os.walk(root, followlinks=False):
        for entry in directories + names:
            path = Path(folder) / entry
            walked += 1
            require(walked <= 10000, "oversized_package_tree")
            require(not is_link(path), "linked_package_entry")
            name = path.relative_to(root).as_posix()
            require(
                entry not in {".git", "__pycache__", "node_modules"},
                "private_or_cache_path",
            )
            require(
                not entry.startswith(".env")
                and path.suffix.lower()
                not in {".db", ".sqlite", ".sqlite3", ".key", ".pyc"},
                "private_or_database_path",
            )
            if path.is_file():
                require(
                    name == manifest_name or name in inventory, "unlisted_package_file"
                )
    return {
        "status": "inventory_and_record_consistency_passed_not_final_acceptance",
        "source": source,
        "files": len(inventory),
        "acceptance_records": len(gates),
        "limits": [
            "Human attestations are checked for completeness, not proven true.",
            "No document rendering, video playback, image/tar internals or full privacy audit.",
            "No runtime, real receipt, second-machine or final submission is certified.",
            "Algorithm enabled requires a separate expanded material gate.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--manifest", default="submission-manifest.json")
    arguments = parser.parse_args()
    try:
        result = verify(arguments.directory, arguments.manifest)
    except PackageError as error:
        sys.stdout.write(
            json.dumps(
                {"status": "blocked_not_ready", "passed": False, "reason": str(error)}
            )
            + "\n"
        )
        return 1
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        # Never echo arbitrary file content or private paths on failed validation.
        sys.stdout.write(
            json.dumps({"status": "blocked_not_ready", "passed": False}) + "\n"
        )
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

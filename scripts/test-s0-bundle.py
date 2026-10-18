"""Offline regression checks for S0 bundle version and source boundaries."""

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "s0_bundle_check", ROOT / "scripts/check-s0-bundle.py"
)
assert SPEC is not None and SPEC.loader is not None
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class BundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(CHECK.MANIFEST.read_text(encoding="utf-8"))

    def test_current_manifest_is_proposal(self) -> None:
        CHECK.validate_manifest(self.document)

    def test_missing_or_repeated_interface_rejected(self) -> None:
        for sources in (
            self.document["sources"][:-1],
            self.document["sources"][:-1] + [self.document["sources"][0]],
        ):
            with self.subTest(sources=len(sources)):
                changed = copy.deepcopy(self.document)
                changed["sources"] = sources
                with self.assertRaises(ValueError):
                    CHECK.validate_manifest(changed)

    def test_mutable_or_short_revision_rejected(self) -> None:
        for revision in ("main", "63b1f35", "A" * 40):
            with self.subTest(revision=revision):
                self.document["sources"][0]["revision"] = revision
                with self.assertRaises(ValueError):
                    CHECK.validate_manifest(self.document)

    def test_source_path_boundary(self) -> None:
        for path in ("../secret.json", "/tmp/x.json", "D:\\x.json", "work/x.json"):
            with self.subTest(path=path):
                self.document["sources"][0]["paths"] = [path]
                with self.assertRaises(ValueError):
                    CHECK.validate_manifest(self.document)

    def test_cannot_self_declare_acceptance(self) -> None:
        self.document["status"] = "signed"
        with self.assertRaises(ValueError):
            CHECK.validate_manifest(self.document)

    def test_cannot_remove_receipt_limits(self) -> None:
        self.document["not_proof_of"] = []
        with self.assertRaises(ValueError):
            CHECK.validate_manifest(self.document)


if __name__ == "__main__":
    unittest.main()

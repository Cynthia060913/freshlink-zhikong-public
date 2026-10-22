"""Standard-library regressions: no database, package install or host mutation."""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "image_source_guard",
    Path(__file__).resolve().parents[3] / "scripts/check-image-source.py",
)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ImageSourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "backend"
        (self.root / "app/frontend/assets").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        self.source = "a" * 40
        files = {
            "app/main.py": "# Synthetic app source\n",
            "scripts/proof.py": "# Synthetic proof source\n",
            "pyproject.toml": "# Synthetic metadata\n",
            "alembic.ini": "# Synthetic migration metadata\n",
        }
        rows = []
        for name, text in files.items():
            value = text.encode()
            (self.root / name).write_bytes(value)
            rows.append(
                {"path": "backend/" + name, "sha256": hashlib.sha256(value).hexdigest()}
            )
        (self.root / "app/frontend/index.html").write_text("<html></html>")
        (self.root / "app/frontend/assets/main.js").write_text("// synthetic bundle")
        self.manifest = {"revision": self.source, "files": rows}

    def verify(self):
        return guard.verify(self.manifest, self.source, self.root)

    def test_exact_sources_and_bundle_presence(self):
        report = self.verify()
        self.assertEqual(report["backend_files_verified"], 4)
        self.assertEqual(report["frontend_files_present"], 2)
        self.assertIn("application startup", report["not_verified"])

    def test_wrong_identity_refused(self):
        self.manifest["revision"] = "b" * 40
        with self.assertRaisesRegex(RuntimeError, "identity"):
            self.verify()

    def test_tampered_source_refused(self):
        (self.root / "app/main.py").write_text("# changed")
        with self.assertRaisesRegex(RuntimeError, "byte mismatch"):
            self.verify()

    def test_missing_source_refused(self):
        (self.root / "scripts/proof.py").unlink()
        with self.assertRaisesRegex(RuntimeError, "file set"):
            self.verify()

    def test_unexpected_private_file_refused(self):
        (self.root / "app/.env.freshlink").write_text("SYNTHETIC_ONLY")
        with self.assertRaisesRegex(RuntimeError, "file set"):
            self.verify()

    def test_source_symlink_refused(self):
        target = self.root.parent / "outside.py"
        target.write_text("# synthetic outside source")
        (self.root / "app/main.py").unlink()
        (self.root / "app/main.py").symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            self.verify()

    def test_path_traversal_refused(self):
        self.manifest["files"].append(
            {"path": "backend/app/../../elsewhere", "sha256": "a" * 64}
        )
        with self.assertRaisesRegex(RuntimeError, "Unsafe manifest path"):
            self.verify()

    def test_missing_bundle_refused(self):
        (self.root / "app/frontend/assets/main.js").unlink()
        with self.assertRaisesRegex(RuntimeError, "JavaScript"):
            self.verify()

    def test_host_entrypoint_refused_before_manifest_read(self):
        with patch.object(Path, "is_file", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "container only"):
                guard.main()


if __name__ == "__main__":
    unittest.main()

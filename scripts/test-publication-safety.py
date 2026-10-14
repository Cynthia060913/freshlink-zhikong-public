"""Offline unit tests; examples are constructed strings, not real credentials."""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "publication_guard", Path(__file__).with_name("check-publication-safety.py")
)
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class SafetyTests(unittest.TestCase):
    def test_constructed_github_credential(self):
        data = b"prefix\n" + b"ghp_" + b"a" * 36
        result = GUARD.findings("example.py", data, {})
        self.assertEqual(
            result, [{"path": "example.py", "rule": "github_credential", "line": 2}]
        )
        self.assertNotIn(data.decode(), str(result))

    def test_constructed_fine_grained_credential(self):
        self.assertEqual(
            len(GUARD.findings("example.py", b"github_pat_" + b"b" * 40, {})), 1
        )

    def test_local_value_never_printed(self):
        value = b"fictional-only-for-unit-test"
        result = GUARD.findings("demo.txt", value, {"SECRET_KEY": value})
        self.assertEqual(result[0]["key"], "SECRET_KEY")
        self.assertNotIn(value.decode(), str(result))

    def test_private_paths(self):
        for name in [
            ".env.freshlink",
            "backend/.env.local",
            "work/results.json",
            "scripts/__pycache__/x.pyc",
            "id_ed25519",
            "data.sqlite3",
        ]:
            with self.subTest(name=name):
                self.assertTrue(GUARD.private_path(name))

    def test_example_paths_and_upstream_demo(self):
        for name in [
            ".env",
            ".env.freshlink.example",
            "docs/sample.md",
            "simulator/state.json.example",
        ]:
            with self.subTest(name=name):
                self.assertFalse(GUARD.private_path(name))

    def test_binary_is_not_claimed_as_text_scanned(self):
        self.assertEqual(
            GUARD.findings("photo.png", b"\0" + b"ghp_" + b"a" * 36, {}), []
        )

    def test_private_binary_still_rejected(self):
        self.assertEqual(
            GUARD.findings("work/photo.png", b"\0", {})[0]["rule"], "private_path"
        )

    def test_private_key_header(self):
        data = b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----"
        self.assertEqual(GUARD.findings("key.txt", data, {})[0]["rule"], "private_key")

    def test_safe_environment_reference(self):
        self.assertEqual(
            GUARD.findings("script.py", b'os.environ["GITHUB_TOKEN"]', {}), []
        )

    def test_safe_api_sample(self):
        self.assertEqual(
            GUARD.findings("sample.json", b'{"message_id":"demo"}', {}), []
        )


if __name__ == "__main__":
    unittest.main()

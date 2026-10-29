"""No application, database, Docker or provider imports: exact source refusal tests."""

import runpy
import unittest
from pathlib import Path

VERIFY = runpy.run_path(str(Path(__file__).with_name("offline-demo-runtime.py")))[
    "verify_source"
]


class SourceGuardTests(unittest.TestCase):
    def test_reviewed_old_and_new_revisions(self):
        for revision in (
            "e7fffd61c199234918dcf1713cc1f3dd6ce78c51",
            "f8c0b00ce138c319c331cd2be6d3be03b61b5e1b",
        ):
            with self.subTest(revision=revision):
                VERIFY(revision, revision)

    def test_missing_malformed_and_mismatched_sources_are_refused(self):
        exact = "f8c0b00ce138c319c331cd2be6d3be03b61b5e1b"
        for source, expected in (
            (None, exact),
            (exact, None),
            (exact, "0" * 40),
            ("../" + exact, "../" + exact),
            (exact.upper(), exact.upper()),
            (exact + "\n", exact + "\n"),
            ("", ""),
            ("short", "short"),
        ):
            with (
                self.subTest(source=source, expected=expected),
                self.assertRaises(RuntimeError),
            ):
                VERIFY(source, expected)


if __name__ == "__main__":
    unittest.main()

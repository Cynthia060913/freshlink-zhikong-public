"""Synthetic package consistency tests; never real competition evidence."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

script = Path(__file__).with_name("check-submission-package.py")
spec = importlib.util.spec_from_file_location("package_gate", script)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class SubmissionTests(unittest.TestCase):
    def fixture(self, root):
        source = "a" * 40
        names = dict(
            zip(
                sorted(gate.ROLES),
                (
                    "02-application/app.tar",
                    "03-design/design.md",
                    "04-presentation/deck.pptx",
                    "02-application/run.md",
                    "01-source/source.zip",
                    "05-video/movie.webm",
                ),
                strict=True,
            )
        )
        files = []
        for role, name in names.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"SYNTHETIC ONLY - NOT A REAL ARTIFACT")
            files.append(
                {
                    "role": role,
                    "path": name,
                    "source": source,
                    "bytes": path.stat().st_size,
                    "sha256": gate.digest(path),
                }
            )
        acceptance = {
            name: {
                "state": "passed",
                "source": source,
                "reviewer": "A",
                "evidence": [files[0]["path"]],
            }
            for name in gate.GATES
        }
        for name in ("SMTP_RECEIPT", "FEISHU_RECEIPT"):
            acceptance[name].update(
                consent_confirmed=True, terminal_received_confirmed=True
            )
        acceptance["SECOND_MACHINE"]["independent_computer_confirmed"] = True
        return {
            "format": "freshlink-submission-v1",
            "source": source,
            "state": "ready_for_human_final_review",
            "algorithm_enabled": False,
            "files": files,
            "acceptance": acceptance,
        }

    def test_consistent_synthetic_inventory_is_not_final_acceptance(self):
        with self.folder() as name:
            root = Path(name)
            data = self.fixture(root)
            self.save(root, data)
            result = gate.verify(root)
            self.assertEqual(
                result["status"],
                "inventory_and_record_consistency_passed_not_final_acceptance",
            )
            self.assertTrue(result["limits"])

    def folder(self):
        temp = script.parents[1] / "work/submission-gate-tests"
        temp.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=temp)

    def save(self, root, data):
        (root / "submission-manifest.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def test_real_missing_or_mixed_records_are_rejected(self):
        cases = (
            "candidate",
            "missing_video",
            "mixed_artifact",
            "mixed_evidence",
            "tampered",
            "pending_e1",
            "no_terminal",
            "no_consent",
            "not_second_machine",
            "missing_ppt",
            "extra_secret",
            "path_escape",
            "missing_reviewer",
            "algorithm",
            "no_evidence",
            "duplicate_path",
            "extra_manifest_secret",
            "missing_gate",
            "unlisted_evidence",
            "windows_unsafe_name",
        )
        for case in cases:
            with self.subTest(case=case), self.folder() as name:
                root = Path(name)
                data = self.fixture(root)
                if case == "candidate":
                    data["state"] = "candidate"
                elif case == "missing_video":
                    data["files"] = [f for f in data["files"] if f["role"] != "video"]
                elif case == "mixed_artifact":
                    data["files"][0]["source"] = "b" * 40
                elif case == "mixed_evidence":
                    data["acceptance"]["E1"]["source"] = "b" * 40
                elif case == "tampered":
                    (root / data["files"][0]["path"]).write_bytes(b"modified")
                elif case == "pending_e1":
                    data["acceptance"]["E1"]["state"] = "pending"
                elif case == "no_terminal":
                    data["acceptance"]["SMTP_RECEIPT"][
                        "terminal_received_confirmed"
                    ] = False
                elif case == "no_consent":
                    data["acceptance"]["FEISHU_RECEIPT"]["consent_confirmed"] = False
                elif case == "not_second_machine":
                    data["acceptance"]["SECOND_MACHINE"][
                        "independent_computer_confirmed"
                    ] = False
                elif case == "missing_ppt":
                    data["files"] = [
                        f for f in data["files"] if f["role"] != "presentation"
                    ]
                elif case == "extra_secret":
                    private = root / ".ENV.local"
                    private.write_text("synthetic only", encoding="utf-8")
                    data["files"].append(
                        {
                            "role": "supporting",
                            "path": private.name,
                            "source": data["source"],
                            "bytes": private.stat().st_size,
                            "sha256": gate.digest(private),
                        }
                    )
                elif case == "path_escape":
                    data["files"][0]["path"] = "../outside.tar"
                elif case == "missing_reviewer":
                    del data["acceptance"]["M03"]["reviewer"]
                elif case == "algorithm":
                    data["algorithm_enabled"] = True
                elif case == "no_evidence":
                    data["acceptance"]["E1"]["evidence"] = []
                elif case == "duplicate_path":
                    data["files"].append(data["files"][0].copy())
                elif case == "extra_manifest_secret":
                    data["smtp_password"] = "synthetic-not-a-real-secret"
                elif case == "missing_gate":
                    del data["acceptance"]["E1"]
                elif case == "unlisted_evidence":
                    data["acceptance"]["M03"]["evidence"] = ["not-listed.md"]
                elif case == "windows_unsafe_name":
                    data["files"][0]["path"] = "02-application/bad?.tar"
                self.save(root, data)
                with self.assertRaises(gate.PackageError):
                    gate.verify(root)


if __name__ == "__main__":
    unittest.main()

"""No DB/network: material corruption must fail, never silently be accepted."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

script = Path(__file__).with_name("check-design-evidence.py")
spec = importlib.util.spec_from_file_location("design_check", script)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
original = script.parents[1] / "docs/submission/design"


class DesignEvidenceTests(unittest.TestCase):
    def test_actual_materials(self):
        self.assertEqual(checker.verify(original)["tables"], 33)

    def test_invalid_materials_are_rejected(self):
        temp_root = script.parents[1] / "work/design-evidence-tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cases = (
            "schema",
            "source",
            "invented_fk",
            "duplicate",
            "dictionary",
            "pdf",
            "png",
            "editable",
            "drawn_edge",
        )
        for case in cases:
            with (
                self.subTest(case=case),
                tempfile.TemporaryDirectory(dir=temp_root) as folder,
            ):
                target = Path(folder) / "design"
                shutil.copytree(original, target)
                edges_path = target / "er-edges.json"
                edges = json.loads(edges_path.read_text(encoding="utf-8"))
                if case == "schema":
                    (target / "schema-inventory.json").write_text(
                        "{}", encoding="utf-8"
                    )
                elif case == "source":
                    edges["source"] = "0" * 40
                elif case == "invented_fk":
                    edges["edges"][0]["parent"] = "alerts_warehouse"
                elif case == "duplicate":
                    edges["edges"][-1] = edges["edges"][0]
                elif case == "dictionary":
                    (target / "DATA-DICTIONARY.md").write_text(
                        "# empty", encoding="utf-8"
                    )
                elif case in ("pdf", "png"):
                    (target / ("tikz_er_a." + case)).write_bytes(b"invalid")
                elif case == "editable":
                    (target / "tikz_er_a.tex").write_text("invalid", encoding="utf-8")
                elif case == "drawn_edge":
                    path = target / "tikz_er_a.tex"
                    path.write_text(
                        path.read_text(encoding="utf-8").replace(
                            "(heartbeat);", "(receipt);"
                        ),
                        encoding="utf-8",
                    )
                edges_path.write_text(json.dumps(edges), encoding="utf-8")
                with self.assertRaises(ValueError):
                    checker.verify(target)


if __name__ == "__main__":
    unittest.main(verbosity=2)

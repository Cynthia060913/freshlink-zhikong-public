"""Read-only M03 figure/source evidence consistency; never a DB connection."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SOURCE = "71e5a8820ee966db1b250afaf0a72143a4bdc62b"
SCHEMA_HASH = "d43cc6519a7ca324e2a3dcff30999e7d258222e5de6cec72ca7a6d8f7c432bd5"
FIGURES = ("tikz_architecture", "tikz_er_a", "tikz_er_b", "tikz_er_c")


def verify(root):
    schema_path = root / "schema-inventory.json"
    # The captured schema had no terminal LF; repository formatting adds only LF.
    if (
        hashlib.sha256(schema_path.read_bytes().rstrip(b"\n")).hexdigest()
        != SCHEMA_HASH
    ):
        raise ValueError("Schema evidence changed; regenerate and review, not relabel")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    edges = json.loads((root / "er-edges.json").read_text(encoding="utf-8"))
    if (
        schema["source"] != SOURCE
        or edges["source"] != SOURCE
        or schema["migration_head"] != "c01_telemetry_storage"
        or schema["business_rows_read"] is not False
        or schema["method"]
        != "actual_empty_postgresql_alembic_then_read_only_schema_reflection"
    ):
        raise ValueError("Wrong evidence source, migration or read scope")
    tables = {table["name"]: table for table in schema["tables"]}
    if len(tables) != 33 or len(edges["edges"]) != 17:
        raise ValueError("Incomplete table/ER relationship evidence")
    seen = set()
    for edge in edges["edges"]:
        key = (
            edge["child"],
            edge["parent"],
            tuple(edge["columns"]),
            tuple(edge["target"]),
        )
        if key in seen:
            raise ValueError("Repeated ER edge")
        seen.add(key)
        if edge["figure"] not in FIGURES[1:]:
            raise ValueError("Unknown ER figure")
        real = tables[edge["child"]]["foreign_keys"]
        if not any(
            fk["referred_table"] == edge["parent"]
            and fk["constrained_columns"] == edge["columns"]
            and fk["referred_columns"] == edge["target"]
            for fk in real
        ):
            raise ValueError("ER invents a database foreign key")
    dictionary = (root / "DATA-DICTIONARY.md").read_text(encoding="utf-8")
    if any("## " + name + "\n" not in dictionary for name in tables):
        raise ValueError("Data dictionary omits a table")
    for name in FIGURES:
        tex = (root / (name + ".tex")).read_text(encoding="utf-8")
        if "\\begin{tikzpicture}" not in tex or "\\end{document}" not in tex:
            raise ValueError("Missing editable source")
        if not (root / (name + ".pdf")).read_bytes().startswith(b"%PDF-"):
            raise ValueError("Invalid PDF output")
        if not (root / (name + ".png")).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Invalid PNG preview")
        if name in FIGURES[1:]:
            actual = set()
            for line in tex.splitlines():
                if "\\draw[arr]" in line:
                    nodes = re.findall(
                        r"\(([A-Za-z][A-Za-z0-9_]*)(?:\.[a-z]+)?\)", line
                    )
                    if len(nodes) < 2:
                        raise ValueError("Unrecognized ER edge drawing")
                    actual.add((nodes[0], nodes[-1]))
            expected = {
                (edge["parent_node"], edge["child_node"])
                for edge in edges["edges"]
                if edge["figure"] == name
            }
            if actual != expected:
                raise ValueError("Rendered ER source does not match actual FK edges")
    return {
        "source": SOURCE,
        "tables": 33,
        "actual_fk_edges_in_core_figures": 17,
        "figures": 4,
        "status": "material_consistency_not_business_acceptance",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "docs/submission/design",
    )
    args = parser.parse_args()
    sys.stdout.write(json.dumps(verify(args.directory), ensure_ascii=False) + "\n")

"""Build the editable A appendix from its Markdown, preserving soft wraps."""

import re
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

FOLDER = Path(__file__).resolve().parent


def plain(text):
    return text.replace("`", "")


def font(run, size=11, bold=False):
    run.font.name = "Microsoft YaHei"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "微软雅黑")
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor(31, 41, 55)


def joined(lines):
    result = ""
    for line in lines:
        line = plain(line)
        if (
            result
            and re.search(r"[A-Za-z0-9]$", result)
            and re.match(r"^[A-Za-z0-9]", line)
        ):
            result += " "
        result += line
    return result


def paragraph(doc, lines, kind="normal"):
    p = doc.add_paragraph(style="List Bullet" if kind == "bullet" else None)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.2
    p.paragraph_format.widow_control = True
    font(p.add_run(joined(lines)), 10 if kind == "note" else 11)


def table(doc, rows):
    output = doc.add_table(rows=0, cols=3)
    output.style = "Table Grid"
    output.autofit = False
    for column, width in zip(output.columns, [3.0, 5.6, 7.4], strict=True):
        column.width = Cm(width)
    for index, cells in enumerate(rows):
        row = output.add_row()
        cant_split = OxmlElement("w:cantSplit")
        row._tr.get_or_add_trPr().append(cant_split)
        if index == 0:
            row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        for cell, value, width in zip(row.cells, cells, [3.0, 5.6, 7.4], strict=True):
            cell.width = Cm(width)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(3)
            p.paragraph_format.line_spacing = 1.1
            font(p.add_run(plain(value)), 9.5, bold=index == 0)


def main():
    source = (FOLDER / "A-TECHNICAL-APPENDIX.md").read_text(encoding="utf-8")
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.2)
    section.left_margin = section.right_margin = Cm(2.4)
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(11)
    style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "微软雅黑")
    pending = []
    rows = []
    kind = "normal"

    def flush():
        if pending:
            paragraph(doc, pending, kind)
            pending.clear()
        if rows:
            table(doc, rows)
            rows.clear()

    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            flush()
            kind = "normal"
        elif line.startswith(("# ", "## ")):
            flush()
            title = line.startswith("# ")
            p = doc.add_paragraph() if title else doc.add_heading(level=2)
            p.paragraph_format.keep_with_next = True
            font(p.add_run(line[2 if title else 3 :]), 20 if title else 15, True)
        elif line.startswith("|") and line.endswith("|"):
            if pending:
                paragraph(doc, pending, kind)
                pending.clear()
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
                rows.append(cells)
        elif line.startswith("- "):
            flush()
            kind = "bullet"
            pending.append(line[2:])
        elif line.startswith("> "):
            if kind != "note":
                flush()
            kind = "note"
            pending.append(line[2:])
        else:
            pending.append(line)
    flush()
    target = FOLDER / "A-TECHNICAL-APPENDIX.docx"
    doc.save(target)
    print("DOCX generated from adjacent Markdown; render before acceptance")


if __name__ == "__main__":
    main()

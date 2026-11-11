#!/usr/bin/env python3
"""Render the software design specification as an A4 DOCX.

The operation manual keeps its own header, table of contents and cover.
This builder follows the filing sample for the design specification only:
SimHei cover lines, SimSun body, a next-page section break after the cover,
and a dashed footer page number.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor, Twips

from build_manual import add_field, set_east_asia, set_style_fonts, set_update_fields
from common import load_json, sha256_file


BODY_FONT = "SimSun"
COVER_FONT = "SimHei"
HEADING_SIZES = {1: 16, 2: 14, 3: 12}
COVER_SIZES = {"title": 22, "document": 22, "version": 16, "date": 15}
TEXT_WIDTH_MM = 146.5
PAGE_WIDTH = Twips(11906)
PAGE_HEIGHT = Twips(16838)


def set_run(run, font_name: str, size_pt: float, bold: bool = False) -> None:
    set_east_asia(run, font_name)
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.font.color.rgb = RGBColor(0, 0, 0)


def prepare_paragraph(paragraph, align, line_spacing: float = 1.0) -> None:
    fmt = paragraph.paragraph_format
    fmt.alignment = align
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing = line_spacing
    fmt.first_line_indent = Pt(0)
    fmt.left_indent = Pt(0)


def set_mark_font(paragraph, font_name: str, size_pt: float) -> None:
    """Set the paragraph mark font so an empty line uses the intended size."""
    p_pr = paragraph._p.get_or_add_pPr()
    r_pr = p_pr.find(qn("w:rPr"))
    if r_pr is None:
        r_pr = OxmlElement("w:rPr")
        p_pr.append(r_pr)
    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, fonts)
    for key in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(qn(f"w:{key}"), font_name)
    half_points = str(int(round(size_pt * 2)))
    for tag in ("w:sz", "w:szCs"):
        node = r_pr.find(qn(tag))
        if node is None:
            node = OxmlElement(tag)
            r_pr.append(node)
        node.set(qn("w:val"), half_points)


def set_first_line_indent(paragraph) -> None:
    paragraph.paragraph_format.first_line_indent = Twips(420)
    ind = paragraph._p.get_or_add_pPr().get_or_add_ind()
    ind.set(qn("w:firstLineChars"), "200")


def set_outline_level(paragraph, level: int) -> None:
    paragraph._p.get_or_add_pPr().get_or_add_outlineLvl().val = max(level - 1, 0)


def ensure_child(parent, tag: str):
    node = parent.find(qn(tag))
    if node is None:
        node = OxmlElement(tag)
        parent.append(node)
    return node


def new_paragraph(doc: Document):
    existing = doc.paragraphs
    if len(existing) == 1 and not existing[0].text and not existing[0].runs:
        return existing[0]
    return doc.add_paragraph()


def configure_normal(doc: Document) -> None:
    normal = doc.styles["Normal"]
    set_style_fonts(normal, BODY_FONT)
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    fmt = normal.paragraph_format
    fmt.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing = 1.0


def set_character_spacing(doc: Document) -> None:
    settings = doc.settings.element
    node = ensure_child(settings, "w:characterSpacingControl")
    node.set(qn("w:val"), "compressPunctuation")


def apply_section(section) -> None:
    section.page_width = PAGE_WIDTH
    section.page_height = PAGE_HEIGHT
    section.top_margin = Twips(1440)
    section.bottom_margin = Twips(1440)
    section.left_margin = Twips(1800)
    section.right_margin = Twips(1800)
    section.header_distance = Twips(851)
    section.footer_distance = Twips(992)
    section.different_first_page_header_footer = False
    sect_pr = section._sectPr
    grid = ensure_child(sect_pr, "w:docGrid")
    grid.set(qn("w:type"), "lines")
    grid.set(qn("w:linePitch"), "312")
    grid.set(qn("w:charSpace"), "0")
    numbering = ensure_child(sect_pr, "w:pgNumType")
    numbering.set(qn("w:fmt"), "numberInDash")
    numbering.set(qn("w:start"), "1")


def clear_header(section) -> None:
    header = section.header
    header.is_linked_to_previous = False
    paragraph = header.paragraphs[0]
    paragraph.text = ""
    prepare_paragraph(paragraph, WD_ALIGN_PARAGRAPH.LEFT)


def write_footer(section) -> None:
    footer = section.footer
    footer.is_linked_to_previous = False
    paragraph = footer.paragraphs[0]
    paragraph.text = ""
    prepare_paragraph(paragraph, WD_ALIGN_PARAGRAPH.CENTER)
    add_field(paragraph, " PAGE  \\* MERGEFORMAT ", "- 1 -")
    set_run(paragraph.runs[-1], BODY_FONT, 9)


def add_blank(doc: Document) -> None:
    paragraph = new_paragraph(doc)
    prepare_paragraph(paragraph, WD_ALIGN_PARAGRAPH.JUSTIFY)


def add_cover_spacer(doc: Document) -> None:
    paragraph = new_paragraph(doc)
    prepare_paragraph(paragraph, WD_ALIGN_PARAGRAPH.CENTER, 2.4)
    paragraph.paragraph_format.space_before = Pt(17)
    paragraph.paragraph_format.space_after = Pt(16.5)
    set_mark_font(paragraph, COVER_FONT, 22)


def add_cover_line(doc: Document, text: str, size_pt: float) -> None:
    paragraph = new_paragraph(doc)
    prepare_paragraph(paragraph, WD_ALIGN_PARAGRAPH.CENTER)
    run = paragraph.add_run(text)
    set_run(run, COVER_FONT, size_pt, bold=True)


def start_body_section(doc: Document) -> None:
    section = doc.add_section(WD_SECTION.NEW_PAGE)
    apply_section(section)
    write_footer(section)


def add_cover(doc: Document, page: dict[str, Any]) -> None:
    add_cover_spacer(doc)
    for _ in range(6):
        add_blank(doc)
    insert_lower_gap = True
    for block in page.get("blocks") or []:
        role = str(block.get("role") or "title")
        if insert_lower_gap and role in {"version", "date"}:
            for _ in range(17):
                add_blank(doc)
            insert_lower_gap = False
        add_cover_line(doc, str(block.get("text", "")), COVER_SIZES.get(role, 16))
    start_body_section(doc)


def add_heading(doc: Document, title: str, level: int) -> None:
    paragraph = new_paragraph(doc)
    align = WD_ALIGN_PARAGRAPH.LEFT if level >= 3 else WD_ALIGN_PARAGRAPH.JUSTIFY
    line_spacing = 1.5 if level >= 3 else 1.0
    prepare_paragraph(paragraph, align, line_spacing)
    set_outline_level(paragraph, level)
    run = paragraph.add_run(title)
    set_run(run, BODY_FONT, HEADING_SIZES.get(level, 12), bold=True)


def add_body(doc: Document, text: str) -> None:
    paragraph = new_paragraph(doc)
    prepare_paragraph(paragraph, WD_ALIGN_PARAGRAPH.JUSTIFY)
    set_first_line_indent(paragraph)
    run = paragraph.add_run(text)
    set_run(run, BODY_FONT, 10.5)


def add_caption(doc: Document, caption: str) -> None:
    paragraph = new_paragraph(doc)
    prepare_paragraph(paragraph, WD_ALIGN_PARAGRAPH.CENTER)
    run = paragraph.add_run(caption)
    set_run(run, BODY_FONT, 7.5)


def shade_cell(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def fitted_width(block: dict[str, Any]):
    raw = block.get("width_mm", TEXT_WIDTH_MM)
    try:
        width = float(raw)
    except (TypeError, ValueError):
        width = TEXT_WIDTH_MM
    if width <= 0 or width > TEXT_WIDTH_MM:
        width = TEXT_WIDTH_MM
    return Mm(width)


def add_placeholder(doc: Document, caption: str) -> None:
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    cell = table.cell(0, 0)
    cell.width = Mm(TEXT_WIDTH_MM)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    shade_cell(cell, "F2F2F2")
    cell.text = ""
    paragraph = cell.paragraphs[0]
    prepare_paragraph(paragraph, WD_ALIGN_PARAGRAPH.CENTER)
    run = paragraph.add_run("截图预留")
    set_run(run, BODY_FONT, 10.5)
    table.rows[0].height = Mm(48)
    if caption:
        add_caption(doc, caption)


def add_image(doc: Document, block: dict[str, Any], base: Path) -> None:
    image = Path(str(block.get("path", "")))
    if not image.is_absolute():
        image = (base / image).resolve()
    if not image.is_file():
        raise FileNotFoundError(f"Image not found: {image}")
    paragraph = new_paragraph(doc)
    prepare_paragraph(paragraph, WD_ALIGN_PARAGRAPH.CENTER)
    run = paragraph.add_run()
    run.add_picture(str(image), width=fitted_width(block))
    if block.get("caption"):
        add_caption(doc, str(block["caption"]))


def add_blocks(doc: Document, blocks: list[dict[str, Any]], base: Path) -> None:
    for block in blocks:
        kind = block.get("type")
        if kind == "paragraph":
            add_body(doc, str(block.get("text", "")))
        elif kind == "image":
            add_image(doc, block, base)
        elif kind == "placeholder_image":
            add_placeholder(doc, str(block.get("caption", "")))
        elif kind == "cover_line":
            continue
        else:
            raise ValueError(f"Unsupported design-spec block: {kind}")


def build(input_path: Path, output_path: Path) -> None:
    payload = load_json(input_path)
    pages = payload.get("pages") or []
    if not pages:
        raise ValueError("design specification input contains no pages")
    doc = Document()
    configure_normal(doc)
    set_character_spacing(doc)
    apply_section(doc.sections[0])
    clear_header(doc.sections[0])
    write_footer(doc.sections[0])
    set_update_fields(doc)
    facts = payload.get("facts", {})
    document = payload.get("document", {})
    doc.core_properties.title = f"{facts.get('software_full_name', '')}{document.get('title', '软件设计说明书')}"
    doc.core_properties.subject = document.get("type", "软件著作权鉴别材料")
    doc.core_properties.author = str(facts.get("rightsholder", ""))
    for page in pages:
        if page.get("kind") == "cover":
            add_cover(doc, page)
            continue
        add_heading(doc, str(page.get("title", "")), int(page.get("level", 1)))
        add_blocks(doc, page.get("blocks") or [], input_path.parent)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    print(f"DOCX={output_path.resolve()}")
    print(f"INPUT_PAGES={len(pages)} SHA256={sha256_file(output_path)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.input.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

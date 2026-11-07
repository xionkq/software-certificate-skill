#!/usr/bin/env python3
"""Compose provenance-traceable source identification materials.

Filing volumes follow the accepted sample: A4, Calibri 10.5 pt with SimSun
for Han characters, no page header, and a footer page number. The two
volumes contain whole source files only, are not a continuous slice, and
each lands in 55–65 pages with a combined 120–125 pages.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import unicodedata
from pathlib import Path

from common import load_json, now_iso, relative_posix, safe_text, save_json, sha256_file

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, Twips
except ImportError as exc:
    raise SystemExit("python-docx is required; use the bundled document runtime.") from exc


# Sample geometry: A4, top/bottom 1440 twips (25.4 mm), left/right 1800 twips (31.75 mm).
PAGE_WIDTH_MM = 210.0
PAGE_HEIGHT_MM = 297.0
MARGIN_TOP_MM = 25.4
MARGIN_BOTTOM_MM = 25.4
MARGIN_LEFT_MM = 31.75
MARGIN_RIGHT_MM = 31.75
MM_TO_PT = 72.0 / 25.4
CONTENT_WIDTH_PT = (PAGE_WIDTH_MM - MARGIN_LEFT_MM - MARGIN_RIGHT_MM) * MM_TO_PT
CONTENT_HEIGHT_PT = (PAGE_HEIGHT_MM - MARGIN_TOP_MM - MARGIN_BOTTOM_MM) * MM_TO_PT
ASCII_FONT = "Calibri"
EAST_ASIA_FONT = "SimSun"
FONT_SIZE_PT = 10.5  # 五号
LINE_SPACING_TWIPS = 285  # sample w:spacing line="285" (14.25 pt)
LINE_SPACING_PT = LINE_SPACING_TWIPS / 20.0
# Leave 2 pt of slack so the last row cannot spill onto the next page.
ROWS_PER_PAGE = max(1, (int(CONTENT_HEIGHT_PT * 20) - 40) // LINE_SPACING_TWIPS)
VOLUME_PAGE_MIN = 55
VOLUME_PAGE_MAX = 65
TOTAL_PAGE_MIN = 120
TOTAL_PAGE_MAX = 125
TARGET_FRONT_PAGES = 60
TARGET_BACK_PAGES = 62
MAX_VOLUME_ROWS = VOLUME_PAGE_MAX * ROWS_PER_PAGE
# Used only when Calibri/SimSun cannot be measured. Real advances come from the
# installed faces so a normal source line is not broken early.
FALLBACK_ADVANCE_PT = 11.0
LATIN_ADVANCE_PT = FALLBACK_ADVANCE_PT
CJK_ADVANCE_PT = FALLBACK_ADVANCE_PT
WRAP_WIDTH_PT = CONTENT_WIDTH_PT - 2.0

WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
# Same faces as render_code_pdf.py. CFF collections such as Ubuntu NotoSansCJK
# are omitted because reportlab's TTFont cannot parse them.
CJK_CANDIDATES = [
    (WINDOWS_FONTS / "simsun.ttc", 0),
    (WINDOWS_FONTS / "simhei.ttf", None),
    (WINDOWS_FONTS / "simfang.ttf", None),
    (Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"), 0),
    (Path("/usr/share/fonts/truetype/arphic/uming.ttc"), 0),
    (Path("/System/Library/Fonts/STHeiti Light.ttc"), 0),
]
ASCII_CANDIDATES = [
    (WINDOWS_FONTS / "calibri.ttf", None),
    (WINDOWS_FONTS / "calibri.ttc", 0),
    (Path("/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf"), None),
    (Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"), None),
    (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), None),
    (Path("/System/Library/Fonts/Supplemental/Calibri.ttf"), None),
]
_MEASURE_ASCII = "SoftCertMeasureCalibri"
_MEASURE_CJK = "SoftCertMeasureCJK"
_ADVANCE_CACHE: dict[str, float] = {}
_METRICS_READY = False
_ASCII_FACE: str | None = None
_CJK_FACE: str | None = None

DEFAULT_EXTENSIONS = {
    ".py", ".cs", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".vue",
    ".go", ".rs", ".c", ".h", ".cpp", ".cc", ".php", ".rb", ".swift", ".sql",
    ".cshtml", ".razor", ".html"
}
SENSITIVE_TOKENS = ("privatekey", "private_key", "id_rsa", "id_dsa", "secret.key", ".env")


def matches_any(relative: str, patterns: list[str]) -> bool:
    normalized = relative.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) or Path(normalized).match(pattern) for pattern in patterns)


def select_files(root: Path, manifest: dict) -> list[Path]:
    ordered = manifest.get("ordered_files", [])
    exclude = list(manifest.get("exclude", []))
    if ordered:
        candidates = [(root / item).resolve() for item in ordered]
    else:
        candidates_set: set[Path] = set()
        for pattern in manifest.get("include", ["**/*"]):
            candidates_set.update(path.resolve() for path in root.glob(pattern) if path.is_file())
        candidates = sorted(candidates_set, key=lambda p: relative_posix(p, root).lower())
    result: list[Path] = []
    for path in candidates:
        try:
            rel = relative_posix(path, root)
        except ValueError as exc:
            raise ValueError(f"Manifest path escapes project root: {path}") from exc
        if not path.is_file() or path.suffix.lower() not in DEFAULT_EXTENSIONS:
            continue
        if matches_any(rel, exclude):
            continue
        if any(token in rel.lower() for token in SENSITIVE_TOKENS):
            continue
        result.append(path)
    return result


def char_columns(character: str) -> int:
    """East-Asian width class, used to choose the CJK face for a character."""
    return 2 if unicodedata.east_asian_width(character) in ("W", "F") else 1


def _register_measure_font(logical_name: str, candidates: list[tuple[Path, int | None]],
                           override_env: str) -> str | None:
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return None
    override = os.environ.get(override_env)
    ordered = list(candidates)
    if override:
        path = Path(override)
        ordered = [(path, 0 if path.suffix.lower() == ".ttc" else None)] + ordered
    for path, subfont in ordered:
        if not path.is_file():
            continue
        try:
            if subfont is None:
                pdfmetrics.registerFont(TTFont(logical_name, str(path)))
            else:
                pdfmetrics.registerFont(TTFont(logical_name, str(path), subfontIndex=subfont))
            return logical_name
        except Exception:
            continue
    return None


def _ensure_measure_fonts() -> None:
    global _METRICS_READY, _ASCII_FACE, _CJK_FACE
    if _METRICS_READY:
        return
    _METRICS_READY = True
    _ASCII_FACE = _register_measure_font(_MEASURE_ASCII, ASCII_CANDIDATES, "SOFTCERT_CODE_ASCII_FONT")
    _CJK_FACE = _register_measure_font(_MEASURE_CJK, CJK_CANDIDATES, "SOFTCERT_CODE_CJK_FONT")


def ascii_metrics_loaded() -> bool:
    """True when wrap width comes from an installed Latin face, not the 11 pt fallback."""
    _ensure_measure_fonts()
    return _ASCII_FACE is not None


def char_advance_pt(character: str) -> float:
    cached = _ADVANCE_CACHE.get(character)
    if cached is not None:
        return cached
    _ensure_measure_fonts()
    face = _CJK_FACE if char_columns(character) == 2 else _ASCII_FACE
    width = FALLBACK_ADVANCE_PT
    if face is not None:
        try:
            from reportlab.pdfbase import pdfmetrics
            measured = pdfmetrics.stringWidth(character, face, FONT_SIZE_PT)
            if measured > 0:
                width = measured
        except Exception:
            width = FALLBACK_ADVANCE_PT
    _ADVANCE_CACHE[character] = width
    return width


def wrap_source_line(line: str) -> list[str]:
    """Wrap one source line inside the content width without dropping characters."""
    expanded = line.expandtabs(4)
    if expanded == "":
        return [""]
    segments: list[str] = []
    current: list[str] = []
    width = 0.0
    for character in expanded:
        advance = char_advance_pt(character)
        if current and width + advance > WRAP_WIDTH_PT:
            segments.append("".join(current))
            current, width = [], 0.0
        current.append(character)
        width += advance
    if current:
        segments.append("".join(current))
    return segments or [""]


def page_count_for_rows(rows: int) -> int:
    if rows <= 0:
        return 0
    return (rows + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE


def choose_filing_volumes(files: list[dict]) -> tuple[int, int] | None:
    """Pick a prefix and a suffix of whole files.

    Returns ``(front_end, back_start)`` where the front volume is
    ``files[:front_end]``, the back volume is ``files[back_start:]``, and at
    least one file sits between them. Page counts must be 55–65 each and
    120–125 together. Prefer a pair that keeps backend files in both volumes
    when the corpus has any, then the pair closest to 60 and 62 pages.
    """
    count = len(files)
    if count < 3:
        return None
    prefix = [0]
    for item in files:
        prefix.append(prefix[-1] + int(item["visual_rows"]))
    has_backend = any(item.get("side") == "backend" for item in files)

    def pages(start: int, end: int) -> int:
        return page_count_for_rows(prefix[end] - prefix[start])

    def covers_backend(start: int, end: int) -> bool:
        return any(item.get("side") == "backend" for item in files[start:end])

    ranked: list[tuple[tuple, int, int]] = []
    for front_end in range(1, count):
        front_pages = pages(0, front_end)
        if front_pages > VOLUME_PAGE_MAX:
            break
        if front_pages < VOLUME_PAGE_MIN:
            continue
        for back_start in range(front_end + 1, count):
            back_pages = pages(back_start, count)
            if back_pages > VOLUME_PAGE_MAX:
                continue
            if back_pages < VOLUME_PAGE_MIN:
                break
            total = front_pages + back_pages
            if not (TOTAL_PAGE_MIN <= total <= TOTAL_PAGE_MAX):
                continue
            backend_miss = 0
            if has_backend:
                if not covers_backend(0, front_end):
                    backend_miss += 1
                if not covers_backend(back_start, count):
                    backend_miss += 1
            ranked.append((
                (backend_miss, abs(front_pages - TARGET_FRONT_PAGES) + abs(back_pages - TARGET_BACK_PAGES),
                 abs(total - (TARGET_FRONT_PAGES + TARGET_BACK_PAGES)), back_start - front_end),
                front_end, back_start,
            ))
    if not ranked:
        return None
    covering = [item for item in ranked if item[0][0] == 0] if has_backend else ranked
    pool = covering or ranked
    pool.sort(key=lambda item: item[0])
    return pool[0][1], pool[0][2]


def paginate_rows(rows: list[str]) -> list[list[str]]:
    if not rows:
        return []
    return [rows[index:index + ROWS_PER_PAGE] for index in range(0, len(rows), ROWS_PER_PAGE)]


def write_txt(path: Path, pages: list[list[str]]) -> None:
    path.write_text("\n\f\n".join("\n".join(page) for page in pages) + "\n", encoding="utf-8")


def add_page_field(paragraph) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, instr, separate, text, end):
        run._r.append(node)


def set_page_number_start(section, start: int) -> None:
    sect_pr = section._sectPr
    pg_num_type = sect_pr.find(qn("w:pgNumType"))
    if pg_num_type is None:
        pg_num_type = OxmlElement("w:pgNumType")
        cols = sect_pr.find(qn("w:cols"))
        if cols is not None:
            cols.addprevious(pg_num_type)
        else:
            sect_pr.append(pg_num_type)
    pg_num_type.set(qn("w:start"), str(start))


def set_run_font(run) -> None:
    run.font.name = ASCII_FONT
    run.font.size = Pt(FONT_SIZE_PT)
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    fonts.set(qn("w:ascii"), ASCII_FONT)
    fonts.set(qn("w:hAnsi"), ASCII_FONT)
    fonts.set(qn("w:cs"), ASCII_FONT)
    fonts.set(qn("w:eastAsia"), EAST_ASIA_FONT)


def write_docx(path: Path, pages: list[list[str]], page_number_start: int = 1) -> None:
    """Write code pages with an empty header and a centered footer page number."""
    doc = Document()
    section = doc.sections[0]
    section.page_width = Mm(PAGE_WIDTH_MM)
    section.page_height = Mm(PAGE_HEIGHT_MM)
    section.top_margin = Mm(MARGIN_TOP_MM)
    section.bottom_margin = Mm(MARGIN_BOTTOM_MM)
    section.left_margin = Mm(MARGIN_LEFT_MM)
    section.right_margin = Mm(MARGIN_RIGHT_MM)
    section.header_distance = Mm(15)
    section.footer_distance = Mm(17.5)
    section.different_first_page_header_footer = False
    set_page_number_start(section, page_number_start)
    normal = doc.styles["Normal"]
    normal.font.name = ASCII_FONT
    normal.font.size = Pt(FONT_SIZE_PT)
    normal_fonts = normal.element.get_or_add_rPr().get_or_add_rFonts()
    normal_fonts.set(qn("w:ascii"), ASCII_FONT)
    normal_fonts.set(qn("w:hAnsi"), ASCII_FONT)
    normal_fonts.set(qn("w:cs"), ASCII_FONT)
    normal_fonts.set(qn("w:eastAsia"), EAST_ASIA_FONT)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    normal.paragraph_format.line_spacing = Twips(LINE_SPACING_TWIPS)
    normal.paragraph_format.widow_control = False

    header = section.header.paragraphs[0]
    header.text = ""
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.paragraph_format.space_before = Pt(0)
    footer.paragraph_format.space_after = Pt(0)
    dash_before = footer.add_run("- ")
    set_run_font(dash_before)
    add_page_field(footer)
    for run in footer.runs:
        set_run_font(run)
    dash_after = footer.add_run(" -")
    set_run_font(dash_after)

    for page_index, lines in enumerate(pages):
        for line_index, line in enumerate(lines):
            paragraph = doc.add_paragraph()
            if page_index and not line_index:
                paragraph.paragraph_format.page_break_before = True
            if line:
                set_run_font(paragraph.add_run(line))
    doc.save(path)


def load_source_files(root: Path, files: list[Path], sides: dict[str, str]) -> tuple[list[dict], list[dict], int]:
    """Return placeable files, oversized files, and retained blank-line count."""
    placeable: list[dict] = []
    oversized: list[dict] = []
    blank_lines = 0
    for path in files:
        text = safe_text(path, max_bytes=20_000_000)
        if text is None:
            continue
        rel = relative_posix(path, root)
        original_lines = text.splitlines()
        visual: list[str] = []
        mapping: list[dict] = []
        digest = sha256_file(path)
        for original_no, original in enumerate(original_lines, 1):
            if not original.strip():
                blank_lines += 1
            segments = wrap_source_line(original)
            for segment_no, segment in enumerate(segments, 1):
                visual.append(segment)
                mapping.append({
                    "file": rel,
                    "file_sha256": digest,
                    "source_line": original_no,
                    "segment": segment_no,
                    "source_text": original.expandtabs(4),
                })
        if not visual:
            continue
        record = {
            "path": rel,
            "sha256": digest,
            "side": sides.get(rel, ""),
            "original_lines": len(original_lines),
            "visual_rows": len(visual),
            "visual": visual,
            "mapping": mapping,
        }
        if len(visual) > MAX_VOLUME_ROWS:
            oversized.append({
                "path": rel,
                "sha256": digest,
                "original_lines": len(original_lines),
                "visual_rows": len(visual),
                "page_count": page_count_for_rows(len(visual)),
            })
            continue
        placeable.append(record)
    return placeable, oversized, blank_lines


def assign_output_lines(files: list[dict]) -> tuple[list[str], list[dict]]:
    lines: list[str] = []
    mapping: list[dict] = []
    for record in files:
        record["output_start_line"] = len(lines) + 1
        for segment, meta in zip(record["visual"], record["mapping"]):
            lines.append(segment)
            published = dict(meta)
            published["output_line"] = len(lines)
            mapping.append(published)
        record["output_end_line"] = len(lines)
    return lines, mapping


def volume_payload(files: list[dict], page_number_start: int) -> dict:
    rows: list[str] = []
    for record in files:
        rows.extend(record["visual"])
    pages = paginate_rows(rows)
    return {
        "files": [record["path"] for record in files],
        "pages": pages,
        "page_count": len(pages),
        "page_number_start": page_number_start,
        "visual_rows": len(rows),
        "first_output_line": files[0]["output_start_line"] if files else None,
        "last_output_line": files[-1]["output_end_line"] if files else None,
        "logical_source_pages": list(range(1, len(pages) + 1)),
    }


def block_reason(placeable: list[dict], oversized: list[dict]) -> str:
    full_pages = page_count_for_rows(sum(int(item["visual_rows"]) for item in placeable))
    reason = (
        f"无法在不拆开源文件的前提下形成前后两卷：可排版文件 {len(placeable)} 个，"
        f"连续铺排约 {full_pages} 页。需要前后卷各 {VOLUME_PAGE_MIN}–{VOLUME_PAGE_MAX} 页、"
        f"合计 {TOTAL_PAGE_MIN}–{TOTAL_PAGE_MAX} 页，且中间至少隔开一个源文件。"
    )
    if oversized:
        listed = "、".join(item["path"] for item in oversized[:8])
        reason += f" 另有 {len(oversized)} 个文件超过 {VOLUME_PAGE_MAX} 页，已整文件跳过：{listed}。"
    return reason


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--facts", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    root = args.project.resolve()
    manifest = load_json(args.manifest)
    sides = {str(item.get("path")): str(item.get("side") or "") for item in manifest.get("file_decisions", [])}
    files = select_files(root, manifest)
    if not files:
        parser.error("No eligible source files selected by the manifest")
    placeable, oversized, blank_lines = load_source_files(root, files, sides)
    lines, mapping = assign_output_lines(placeable)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    full_pages = paginate_rows(lines)
    full_txt = output / "source-full.txt"
    write_txt(full_txt, full_pages)
    choice = choose_filing_volumes(placeable)
    file_records = [{
        "path": item["path"], "sha256": item["sha256"], "side": item.get("side", ""),
        "original_lines": item["original_lines"], "visual_rows": item["visual_rows"],
        "output_start_line": item["output_start_line"], "output_end_line": item["output_end_line"],
    } for item in placeable]
    layout = {
        "font_ascii": ASCII_FONT, "font_east_asia": EAST_ASIA_FONT,
        "font_size_pt": FONT_SIZE_PT, "line_spacing_pt": LINE_SPACING_PT,
        "rows_per_page": ROWS_PER_PAGE,
        "margins_mm": {"top": MARGIN_TOP_MM, "bottom": MARGIN_BOTTOM_MM,
                       "left": MARGIN_LEFT_MM, "right": MARGIN_RIGHT_MM},
        "pagination": "page_break_before",
        "line_policy": "original lines retained, including blanks; long lines wrap on measured Calibri/SimSun advances without dropping characters",
        "advance": "measured" if ascii_metrics_loaded() else "fallback_11pt",
        "header": "none",
        "footer": "centered page number, - n -",
        "page_numbering": "front starts at 1, back continues",
    }
    base = {
        "schema_version": "1.1",
        "generated_at": now_iso(),
        "project_root": str(root),
        "manifest": str(args.manifest.resolve()),
        "manifest_review": manifest.get("review", {}),
        "ordered_files_confirmed": bool(manifest.get("ordered_files")),
        "selection_policy": manifest.get("selection_policy", {}),
        "file_decisions": manifest.get("file_decisions", []),
        "lines_per_page": ROWS_PER_PAGE,
        "rows_per_page": ROWS_PER_PAGE,
        "blank_lines_excluded": 0,
        "blank_lines_retained": blank_lines,
        "original_line_count": sum(item["original_lines"] for item in file_records)
        + sum(item["original_lines"] for item in oversized),
        "full_line_count": len(lines),
        "full_page_count": len(full_pages),
        "files": file_records,
        "line_mapping": [{key: value for key, value in item.items() if key != "source_text"} for item in mapping],
        "skipped_oversized_files": oversized,
        "layout": layout,
        "artifacts": {"full_txt": {"path": str(full_txt), "sha256": sha256_file(full_txt)}},
    }
    provenance_path = output / "source-provenance.json"
    if choice is None:
        reason = block_reason(placeable, oversized)
        base.update({
            "selection": "blocked",
            "block_reason": reason,
            "filing_page_count": 0,
            "filing_groups": {},
            "gap_files": [],
            "pages": [],
        })
        save_json(provenance_path, base)
        print(f"SOURCE_OUTPUT={output}")
        print(f"BLOCK_REASON={reason}")
        print(f"PROVENANCE={provenance_path}")
        return 3

    front_end, back_start = choice
    front_files = placeable[:front_end]
    back_files = placeable[back_start:]
    gap_files = [item["path"] for item in placeable[front_end:back_start]]
    front = volume_payload(front_files, 1)
    back = volume_payload(back_files, front["page_count"] + 1)
    artifacts = dict(base["artifacts"])
    filing_groups = {}
    page_meta: list[dict] = []
    for key, payload in (("front", front), ("back", back)):
        txt_path = output / f"source-{key}.txt"
        docx_path = output / f"source-{key}.docx"
        write_txt(txt_path, payload["pages"])
        write_docx(docx_path, payload["pages"], payload["page_number_start"])
        artifacts[f"{key}_txt"] = {"path": str(txt_path), "sha256": sha256_file(txt_path)}
        artifacts[f"{key}_docx"] = {"path": str(docx_path), "sha256": sha256_file(docx_path)}
        filing_groups[key] = {
            "files": payload["files"],
            "page_count": payload["page_count"],
            "page_number_start": payload["page_number_start"],
            "visual_rows": payload["visual_rows"],
            "logical_source_pages": payload["logical_source_pages"],
            "first_output_line": payload["first_output_line"],
            "last_output_line": payload["last_output_line"],
        }
        for page_no, page_lines in enumerate(payload["pages"], 1):
            page_meta.append({
                "volume": key,
                "page": page_no,
                "line_count": len(page_lines),
                "effective_lines": sum(1 for line in page_lines if line.strip()),
            })
    base.update({
        "selection": "file_aligned_front_back",
        "block_reason": "",
        "filing_page_count": front["page_count"] + back["page_count"],
        "filing_groups": filing_groups,
        "gap_files": gap_files,
        "pages": page_meta,
        "artifacts": artifacts,
    })
    save_json(provenance_path, base)
    print(f"SOURCE_OUTPUT={output}")
    print(
        f"FILES={len(file_records)} LINES={len(lines)} FULL_PAGES={len(full_pages)} "
        f"FRONT_PAGES={front['page_count']} BACK_PAGES={back['page_count']} "
        f"FILING_PAGES={front['page_count'] + back['page_count']} SELECTION=file_aligned_front_back"
    )
    print(f"PROVENANCE={provenance_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

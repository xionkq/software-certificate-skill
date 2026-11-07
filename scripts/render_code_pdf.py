#!/usr/bin/env python3
"""Draw code filing volumes as PDF directly on a deterministic line grid.

Unlike the office-suite conversion path, pagination here is correct by
construction: every page draws exactly the lines composed by compose_code.py
at fixed positions, with fonts resolved from the local system and embedded as
subsets. No layout engine re-flows the text, so the page count can never
drift. Exit code 4 signals an unusable environment (missing reportlab or no
TrueType CJK font) so the workflow can fall back to office conversion.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from common import load_json, now_iso, save_json, sha256_file
from compose_code import (ASCII_CANDIDATES, ASCII_FONT, CJK_CANDIDATES, FONT_SIZE_PT,
                          LINE_SPACING_PT, MARGIN_BOTTOM_MM, MARGIN_LEFT_MM, MARGIN_RIGHT_MM,
                          MARGIN_TOP_MM, MM_TO_PT, PAGE_HEIGHT_MM, PAGE_WIDTH_MM, char_columns)

try:
    from reportlab.lib.colors import black
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover - environment gate
    print("RENDER_UNAVAILABLE=reportlab is not installed", file=sys.stderr)
    raise SystemExit(4)


PAGE_WIDTH_PT = PAGE_WIDTH_MM * MM_TO_PT
PAGE_HEIGHT_PT = PAGE_HEIGHT_MM * MM_TO_PT
MARGIN_TOP_PT = MARGIN_TOP_MM * MM_TO_PT
MARGIN_LEFT_PT = MARGIN_LEFT_MM * MM_TO_PT
MARGIN_RIGHT_PT = MARGIN_RIGHT_MM * MM_TO_PT
MARGIN_BOTTOM_PT = MARGIN_BOTTOM_MM * MM_TO_PT
FOOTER_BASELINE_PT = MARGIN_BOTTOM_PT * 0.45


def register_first(logical_name: str, candidates: list[tuple[Path, int | None]],
                   override_env: str) -> str | None:
    override = os.environ.get(override_env)
    if override:
        candidates = [(Path(override), 0 if override.lower().endswith(".ttc") else None)] + candidates
    for path, subfont in candidates:
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


def line_runs(line: str, ascii_font: str, cjk_font: str) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    current: list[str] = []
    current_font = ""
    for character in line:
        font = cjk_font if char_columns(character) == 2 else ascii_font
        if font != current_font and current:
            runs.append((current_font, "".join(current)))
            current = []
        current_font = font
        current.append(character)
    if current:
        runs.append((current_font, "".join(current)))
    return runs


def draw_runs(page, runs: list[tuple[str, str]], x: float, baseline: float, size: float) -> None:
    for font, text in runs:
        page.setFont(font, size)
        page.drawString(x, baseline, text)
        x += pdfmetrics.stringWidth(text, font, size)


def parse_pages(txt_path: Path) -> list[list[str]]:
    text = txt_path.read_text(encoding="utf-8")
    if text.endswith("\n"):
        text = text[:-1]
    return [chunk.split("\n") for chunk in text.split("\n\f\n")]


def smoke_render(pdf_path: Path, render_dir: Path, page_numbers: list[int]) -> tuple[list[dict], list[str]]:
    try:
        import pymupdf
    except ImportError:
        return [], []
    rendered, issues = [], []
    render_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf_path) as document:
        for number in page_numbers:
            pixmap = document[number - 1].get_pixmap(dpi=120)
            target = render_dir / f"page-{number:02d}.png"
            pixmap.save(target)
            samples = pixmap.samples
            nonwhite = sum(1 for value in samples if value < 248)
            ratio = nonwhite / max(1, len(samples))
            if ratio < 0.0005:
                issues.append(f"疑似空白页：{number}")
            rendered.append({"page": number, "path": str(target), "sha256": sha256_file(target),
                             "nonwhite_ratio": round(ratio, 6)})
    return rendered, issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--volume", required=True, choices=["front", "back"])
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--render-dir", type=Path)
    parser.add_argument("--expected-pages", required=True, type=int)
    args = parser.parse_args()
    started = time.perf_counter()

    cjk = register_first("SoftCertCJK", CJK_CANDIDATES, "SOFTCERT_CODE_CJK_FONT")
    ascii_font = register_first("SoftCertCalibri", ASCII_CANDIDATES, "SOFTCERT_CODE_ASCII_FONT")
    if not cjk or not ascii_font:
        print("RENDER_UNAVAILABLE=no usable TrueType fonts (Calibri and CJK)", file=sys.stderr)
        return 4

    provenance = load_json(args.provenance)
    facts = load_json(args.facts)
    groups = provenance.get("filing_groups", {})
    if args.volume not in groups:
        print(f"RENDER_ERROR=volume {args.volume} not in provenance filing groups", file=sys.stderr)
        return 2
    artifact = provenance.get("artifacts", {}).get(f"{args.volume}_txt", {})
    txt_path = Path(artifact.get("path", ""))
    if not txt_path.is_file():
        print("RENDER_ERROR=page source txt missing", file=sys.stderr)
        return 2
    if sha256_file(txt_path) != artifact.get("sha256"):
        print("RENDER_ERROR=page source txt does not match provenance hash", file=sys.stderr)
        return 2

    pages = parse_pages(txt_path)
    expected = int(args.expected_pages)
    if len(pages) != expected or groups[args.volume].get("page_count") != expected:
        print(f"RENDER_ERROR=expected {expected} pages, composed {len(pages)}", file=sys.stderr)
        return 2

    logical_pages = list(groups[args.volume].get("logical_source_pages", []))
    page_meta = [item for item in provenance.get("pages", []) if item.get("volume") == args.volume]
    effective_lines = [int(item.get("effective_lines", item.get("line_count", 0))) for item in page_meta]

    layout = provenance.get("layout", {})
    font_size = float(layout.get("font_size_pt", FONT_SIZE_PT))
    line_height = float(layout.get("line_spacing_pt", LINE_SPACING_PT))
    page_start = int(groups[args.volume].get("page_number_start", 1))

    descent = max(abs(pdfmetrics.getAscentDescent(font, font_size)[1])
                  for font in (ascii_font, cjk))
    args.pdf.parent.mkdir(parents=True, exist_ok=True)
    page = canvas.Canvas(str(args.pdf), pagesize=(PAGE_WIDTH_PT, PAGE_HEIGHT_PT),
                         pageCompression=1, invariant=1)
    page.setTitle("源代码")
    page.setCreator("software-certificate-skill direct renderer")
    page.setFillColor(black)
    for index, lines in enumerate(pages):
        footer = f"- {page_start + index} -"
        footer_width = pdfmetrics.stringWidth(footer, ascii_font, font_size)
        page.setFont(ascii_font, font_size)
        page.drawString((PAGE_WIDTH_PT - footer_width) / 2.0, FOOTER_BASELINE_PT, footer)
        for line_no, line in enumerate(lines):
            if not line:
                continue
            baseline = PAGE_HEIGHT_PT - MARGIN_TOP_PT - (line_no + 1) * line_height + descent
            draw_runs(page, line_runs(line, ascii_font, cjk), MARGIN_LEFT_PT, baseline, font_size)
        page.showPage()
    page.save()

    from pypdf import PdfReader
    actual_pages = len(PdfReader(str(args.pdf)).pages)
    issues: list[str] = []
    if actual_pages != expected:
        issues.append(f"期望{expected}页，实际PDF为{actual_pages}页")
    rendered, render_issues = ([], [])
    if args.render_dir and not issues:
        rendered, render_issues = smoke_render(args.pdf, args.render_dir.resolve(),
                                               sorted({1, len(pages)}))
        issues.extend(render_issues)
    elapsed = round(time.perf_counter() - started, 3)

    report = {
        "schema_version": "1.1",
        "generated_at": now_iso(),
        "input": str(txt_path),
        "input_sha256": artifact.get("sha256"),
        "pdf": str(args.pdf.resolve()),
        "pdf_sha256": sha256_file(args.pdf),
        "pdf_pages": actual_pages,
        "expected_pages": expected,
        "software_full_name": facts.get("software_full_name", ""),
        "logical_source_pages": logical_pages,
        "effective_lines": effective_lines,
        "rendered_pages": len(rendered),
        "conversion": {"engine": "Direct PDF (ReportLab)", "label": "direct-pdf",
                       "status": "pass" if not issues else "fail",
                       "elapsed_seconds": elapsed,
                       "fonts": {"ascii": ascii_font, "cjk": cjk, "requested_ascii": ASCII_FONT},
                       "pagination": "fixed line grid, footer page number, no header"},
        "conversion_attempts": [{"engine": "Direct PDF (ReportLab)",
                                 "status": "pass" if not issues else "fail", "diagnostic": None}],
        "render": {"status": "pass" if rendered else "skipped", "mode": "spot_check", "diagnostic": None},
        "rendered": rendered,
        "issues": issues,
        "status": "pass" if not issues else "fail",
    }
    save_json(args.report.resolve(), report)
    print(f"PDF={args.pdf.resolve()}")
    print(f"PAGES={actual_pages} RENDERED={len(rendered)} STATUS={report['status']} ELAPSED={elapsed}s")
    print(f"REPORT={args.report.resolve()}")
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())

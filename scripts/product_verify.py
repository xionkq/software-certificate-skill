#!/usr/bin/env python3
"""Run consolidated release checks for the beginner-facing product workflow."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from common import load_json, now_iso, save_json, sha256_file
from product_model import find_slots, safe_filename


def check(name: str, passed: bool, detail: str, severity: str = "error") -> dict[str, Any]:
    return {"name": name, "status": "pass" if passed else "fail", "severity": severity, "detail": detail}


def docx_text(path: Path) -> str:
    if not path.is_file():
        return ""
    with zipfile.ZipFile(path) as archive:
        return "\n".join(re.sub(r"<[^>]+>", "", archive.read(name).decode("utf-8", "ignore"))
                         for name in archive.namelist() if name.startswith("word/") and name.endswith(".xml"))


def backend_side_coverage(provenance: dict[str, Any]) -> tuple[bool, str]:
    """Every filing volume of a both-sided corpus must show backend source.

    Only the first/last 30 pages are filed for large corpora, so backend
    files can silently drop out of the delivered pages even when selected.
    Applies to analyzer-confirmed fullstack projects AND corpora the
    selector balanced because both sides were detected in the repository.
    """
    sides = {item.get("path"): item.get("side") for item in provenance.get("file_decisions", [])}
    file_records = provenance.get("files", [])
    composed_backend = [item.get("path") for item in file_records if sides.get(item.get("path")) == "backend"]
    if not composed_backend:
        return False, "项目含后端实现，但代码材料未纳入任何后端源文件；须重新选择源码"
    missing: list[str] = []
    for name, group in provenance.get("filing_groups", {}).items():
        listed = group.get("files") or []
        if not any(sides.get(path) == "backend" for path in listed):
            missing.append(name)
    if missing:
        return False, ("以下交存卷不含后端源码：" + "、".join(sorted(missing))
                       + "；须调整源码混排后重新生成")
    return True, f"{composed_backend}个后端源文件已纳入，前后交存卷均含后端实现"


def screenshot_release_check(index_path: Path, data: dict[str, Any]) -> tuple[bool, str]:
    mode, state = data.get("mode", "unknown"), data.get("state", "unknown")
    captures = data.get("captures", [])
    if mode == "skip" and state == "skipped_by_user":
        return False, "用户明确跳过截图；只允许生成带占位符的草稿"
    if state != "captured":
        return False, f"截图状态{state}；模式{mode}"
    if not captures:
        return False, f"截图列表为空；模式{mode}"
    issues: list[str] = []
    for item in captures:
        shot_value = item.get("path")
        shot = Path(shot_value) if shot_value else None
        if shot and not shot.is_absolute():
            shot = (index_path.parent / shot).resolve()
        if item.get("status") != "pass":
            issues.append(f"{item.get('id')}:status")
        if not shot or not shot.is_file():
            issues.append(f"{item.get('id')}:file")
            continue
        if sha256_file(shot) != item.get("sha256"):
            issues.append(f"{item.get('id')}:hash")
        metrics = item.get("metrics", {})
        if int(metrics.get("width", 0)) < 900 or int(metrics.get("height", 0)) < 500:
            issues.append(f"{item.get('id')}:dimensions")
        if item.get("quality_findings"):
            issues.append(f"{item.get('id')}:quality")
        if not item.get("evidence_ids"):
            issues.append(f"{item.get('id')}:mapping")
        if not item.get("role") or not item.get("url"):
            issues.append(f"{item.get('id')}:context")
    summary = data.get("summary", {})
    if summary.get("errors") or summary.get("quality_warnings") or summary.get("missing_planned"):
        issues.append("summary")
    return not issues, (f"截图{len(captures)}张，文件、哈希、清晰度和章节映射通过" if not issues
                        else "问题：" + "、".join(issues[:12]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal", required=True, type=Path)
    parser.add_argument("--quality", required=True, type=Path)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--business", required=True, type=Path)
    parser.add_argument("--design-spec-content", required=True, type=Path)
    parser.add_argument("--application-model", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--screenshot-index", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    formal, quality = args.formal.resolve(), args.quality.resolve()
    facts, business = load_json(args.facts), load_json(args.business)
    design_spec = load_json(args.design_spec_content)
    application, provenance = load_json(args.application_model), load_json(args.provenance)
    screenshots = load_json(args.screenshot_index) if args.screenshot_index.exists() else {"captures": []}
    software = facts.get("software_full_name", "")
    software_filename = safe_filename(software)
    version = facts.get("version", "")
    spec_filename = f"{software_filename}软件设计说明书"
    expected = ["申请表信息.txt", f"{spec_filename}.docx"]
    groups = provenance.get("filing_groups", {})
    expected += ["源代码（前30页）.docx", "源代码（后30页）.docx"]
    checks: list[dict[str, Any]] = []
    missing = [name for name in expected if not (formal / name).is_file()]
    actual = {path.name for path in formal.iterdir() if path.is_file()}
    unexpected = sorted(actual - set(expected))
    package_ok = not missing and not unexpected
    package_detail = []
    if missing:
        package_detail.append("缺少：" + "、".join(missing))
    if unexpected:
        package_detail.append("非交付文件：" + "、".join(unexpected))
    checks.append(check("正式文件完整性与最小交付面", package_ok,
                        "；".join(package_detail) if package_detail else f"{len(expected)}项交付文件齐全且无内部文件"))
    application_text = (formal / "申请表信息.txt").read_text(encoding="utf-8") if (formal / "申请表信息.txt").is_file() else ""
    checks.append(check("申请表必填字段与长度", bool(application.get("release_ready")), f"问题数：{len(application.get('issues', []))}"))
    checks.append(check("单一事实源一致性", bool(software and version and software in application_text and version in application_text),
                        "软件名称与版本已贯穿申请表" if software in application_text and version in application_text else "申请表名称或版本不一致"))
    docx_files = list(formal.glob("*.docx"))
    valid_docx, docx_errors = True, []
    for path in docx_files:
        try:
            if not docx_text(path):
                valid_docx = False
                docx_errors.append(path.name)
        except Exception:
            valid_docx = False
            docx_errors.append(path.name)
    checks.append(check("DOCX结构", valid_docx and bool(docx_files),
                        "异常：" + "、".join(docx_errors) if docx_errors or not valid_docx or not docx_files
                        else f"{len(docx_files)}个DOCX结构可读"))
    mapping = provenance.get("line_mapping", [])
    sample_ok = bool(mapping) and all(item.get("file") and item.get("source_line") for item in mapping[::max(1, len(mapping)//20)])
    checks.append(check("代码真实性抽样追溯", sample_ok, f"映射记录{len(mapping)}条"))
    front, back = groups.get("front") or {}, groups.get("back") or {}
    front_pages, back_pages = int(front.get("page_count", 0)), int(back.get("page_count", 0))
    front_files, back_files = set(front.get("files") or []), set(back.get("files") or [])
    group_ok = (
        55 <= front_pages <= 65 and 55 <= back_pages <= 65
        and 120 <= front_pages + back_pages <= 125
        and bool(front_files) and bool(back_files)
        and front_files.isdisjoint(back_files)
        and bool(provenance.get("gap_files"))
    )
    checks.append(check(
        "代码页数与文件边界", group_ok,
        f"前{front_pages}页、后{back_pages}页、合计{front_pages + back_pages}页；"
        f"间隔文件{len(provenance.get('gap_files') or [])}个"
        if group_ok else f"分卷{list(groups)}未满足各55–65页、合计120–125页且文件不连续"))
    lines_intact = (
        provenance.get("selection") == "file_aligned_front_back"
        and provenance.get("blank_lines_excluded", 1) == 0
        and bool(mapping)
    )
    checks.append(check(
        "代码行完整且未截断", lines_intact,
        f"映射记录{len(mapping)}条；空行保留{provenance.get('blank_lines_retained', 0)}行；"
        f"超长单文件跳过{len(provenance.get('skipped_oversized_files') or [])}个"))
    # A frontend-only repository must not present itself as a full system:
    # the form has to acknowledge the external backend it depends on.
    architecture = str(provenance.get("selection_policy", {}).get("architecture_scope", ""))
    side_balance = provenance.get("selection_policy", {}).get("side_balance")
    if architecture == "frontend_only":
        described = " ".join(str(business.get(key, "")) for key in
                             ("runtime_support", "runtime_platform", "main_functions",
                              "technical_features", "software_purpose"))
        acknowledged = bool(re.search(r"后端|服务端|服务器|接口|API", described))
        checks.append(check("申请范围与源码架构一致", acknowledged,
                            "纯前端仓库：材料仅含前端源码，申请表已注明需配合后端/接口服务" if acknowledged
                            else "纯前端仓库，但申请表未注明依赖后端/接口服务，申请范围存在误导风险"))
    elif architecture == "fullstack" or side_balance:
        # Covers analyzer-confirmed fullstack AND the selector's fail-safe
        # balancing for unclear scopes, so a misread scope can never ship
        # frontend-only code materials for a repository holding a backend.
        covered, coverage_detail = backend_side_coverage(provenance)
        checks.append(check("申请范围与源码架构一致", covered, coverage_detail))
    elif architecture and architecture != "unclassified":
        checks.append(check("申请范围与源码架构一致", True, f"架构范围：{architecture}"))
    evidence_ok = bool(business.get("capabilities")) and all(item.get("evidence_ids") for item in business.get("capabilities", []))
    checks.append(check("业务功能证据", evidence_ok, f"已确认功能{len(business.get('capabilities', []))}项"))
    spec_generation = design_spec.get("generation", {})
    spec_docx_path = formal / f"{spec_filename}.docx"
    spec_text = docx_text(spec_docx_path)
    section_titles = spec_generation.get("section_titles") or ["1.软件概述", "2.运行环境", "3.主要功能", "4.使用流程"]
    sections_present = all(title in spec_text for title in section_titles)
    identity_present = bool(spec_text) and software in spec_text and version in spec_text and "软件设计说明书" in spec_text
    checks.append(check("设计说明书四节结构", sections_present and identity_present,
                        "四节、软件名称、版本和文档名均在正文中" if sections_present and identity_present
                        else "设计说明书缺少固定四节或未写入软件名称与版本"))
    module_names = [str(item) for item in spec_generation.get("module_names") or []]
    function_paragraph = str(spec_generation.get("function_paragraph") or "")
    flow_headings = [str(item) for item in spec_generation.get("flow_headings") or []]
    modules_aligned = bool(module_names) and all(name in function_paragraph for name in module_names) and all(
        f"4.{index}{name}" in flow_headings for index, name in enumerate(module_names, 1)
    )
    headings_in_docx = all(heading in spec_text for heading in flow_headings)
    checks.append(check("设计说明书模块一致", modules_aligned and headings_in_docx,
                        f"模块{len(module_names)}个，使用流程标题{len(flow_headings)}个"
                        if modules_aligned and headings_in_docx else "第3节模块与第4节标题不一致"))
    captions = [str(item) for item in spec_generation.get("figure_captions") or []]
    expected_captions = [f"图{index}" for index in range(1, len(captions) + 1)]
    captions_in_docx = all(re.search(rf"(?<!\d){re.escape(caption)}(?!\d)", spec_text) for caption in captions)
    spec_document_xml = ""
    if spec_docx_path.is_file():
        with zipfile.ZipFile(spec_docx_path) as archive:
            spec_document_xml = archive.read("word/document.xml").decode("utf-8", "replace")
            footer_xml = "\n".join(archive.read(name).decode("utf-8", "replace")
                                   for name in archive.namelist() if name.startswith("word/footer"))
    else:
        footer_xml = ""
    figure_ok = captions == expected_captions and captions_in_docx and "TOC" not in spec_document_xml and " PAGE " in footer_xml
    checks.append(check("设计说明书图号与版式", figure_ok,
                        f"图号{len(captions)}个，页脚页码已设置，无目录域" if figure_ok
                        else "图号不连续，或设计说明书误含目录、缺少页脚页码"))
    captures_ok, capture_detail = screenshot_release_check(args.screenshot_index.resolve(), screenshots)
    checks.append(check("截图清晰度、来源与章节映射", captures_ok, capture_detail))
    slots = find_slots({"facts": facts, "business": business, "application": application})
    checks.append(check("待确认占位符清零", not slots, "无占位符" if not slots else "、".join(slots)))
    sensitive = provenance.get("manifest_review", {}).get("secret_findings", [])
    checks.append(check("敏感信息与第三方风险", not sensitive, f"未进入材料的命中记录{len(sensitive)}项" if sensitive else "未发现进入材料的敏感项"))
    errors = [item for item in checks if item["status"] == "fail" and item["severity"] == "error"]
    report = {
        "schema_version": "1.0", "generated_at": now_iso(), "software_full_name": software,
        "version": version, "checks": checks, "summary": {"pass": sum(item["status"] == "pass" for item in checks),
        "fail": sum(item["status"] == "fail" for item in checks), "blocking": len(errors)},
        "release_ready": not errors, "formal_files": [{"name": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size}
                                                       for path in sorted(formal.iterdir()) if path.is_file()],
    }
    save_json(args.output.resolve(), report)
    print(f"PRODUCT_VERIFICATION={args.output.resolve()}")
    print(f"RELEASE_READY={str(report['release_ready']).lower()} PASS={report['summary']['pass']} FAIL={report['summary']['fail']}")
    return 0 if report["release_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

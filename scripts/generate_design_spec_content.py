#!/usr/bin/env python3
"""Build a four-section software design specification from confirmed facts."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from common import load_json, now_iso, save_json
from product_model import find_slots


SECTION_TITLES = ("1.软件概述", "2.运行环境", "3.主要功能", "4.使用流程")
HARDWARE_HINT = re.compile(r"CPU|处理器|内存|硬盘|磁盘|显卡", re.IGNORECASE)
GENERIC_HARDWARE = "满足上述系统需求的通用计算机"
FIGURE_TOKEN = re.compile(r"【图\d+】")


def text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value or "").strip() else []


def clean(value: Any) -> str:
    return str(value or "").strip().rstrip("。；; ")


def unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def clauses(value: Any) -> list[str]:
    return [part.strip() for part in re.split(r"[；;]", str(value or "")) if part.strip()]


def screenshot_map(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item.get("id"): item for item in index.get("captures", []) if item.get("id")}


def environment_lines(business: dict[str, Any]) -> tuple[str, str, bool]:
    """Split confirmed environment text into system and hardware lines.

    Hardware numbers are copied only from an explicit field or from clauses
    that already name CPU, memory or disk. A missing minimum never becomes a
    fabricated i5 / 16G / 512G line.
    """
    chunks = []
    for key in ("runtime_platform", "runtime_support", "development_environment"):
        chunks.extend(clauses(business.get(key, "")))
    hardware_found = [item for item in chunks if HARDWARE_HINT.search(item)]
    system_found = [item for item in clauses(business.get("runtime_platform", ""))
                    + clauses(business.get("runtime_support", ""))
                    if not HARDWARE_HINT.search(item)]
    system = "；".join(unique(system_found)) or "以实际部署环境为准"
    explicit = clean(business.get("hardware_requirements"))
    if explicit:
        return system, explicit, False
    if hardware_found:
        return system, "；".join(unique(hardware_found)), False
    return system, GENERIC_HARDWARE, True


def overview_text(facts: dict[str, Any], business: dict[str, Any]) -> str:
    name = clean(facts.get("software_full_name"))
    users = clean(business.get("target_users")) or "实际使用者"
    purpose = clean(business.get("software_purpose")) or "完成软件提供的业务处理"
    goal = clean(business.get("development_purpose")) or purpose
    tech = clean(business.get("technical_features")) or "项目中已经实现的处理方式"
    domain = clean(business.get("industry_domain")) or "相应业务领域"
    if tech.startswith("系统采用") or tech.startswith("采用"):
        tech_sentence = tech
    else:
        tech_sentence = f"系统采用{tech}"
    return (f"本系统是面向{users}的{name}，主要用于{purpose}，旨在{goal}。"
            f"{tech_sentence}。本系统的定位为{domain}。")


def bind_figure_refs(text: str, numbers: list[int]) -> str:
    """Keep author figure marks when they already match, otherwise add them."""
    body = clean(text)
    if not numbers:
        return f"{body}。" if body else ""
    expected = [f"【图{number}】" for number in numbers]
    found = FIGURE_TOKEN.findall(body)
    local = [f"【图{index}】" for index in range(1, len(numbers) + 1)]
    if found == local and [f"【图{number}】" for number in numbers] != local:
        pieces = FIGURE_TOKEN.split(body)
        rewritten = []
        for index, piece in enumerate(pieces[:-1]):
            rewritten.append(piece)
            rewritten.append(expected[index])
        rewritten.append(pieces[-1])
        body = "".join(rewritten)
    missing = [token for token in expected if token not in body]
    if missing:
        refs = "、".join(missing)
        body = f"{body}如{refs}" if body else f"界面如{refs}"
    return body if body.endswith("。") else f"{body}。"


def default_narrative(module: dict[str, Any]) -> str:
    name = module["name"]
    purpose = clean(module.get("purpose") or module.get("summary")) or "完成该项业务处理"
    entry = clean(module.get("entry"))
    visible = clean(module.get("visible_elements"))
    sentences = [f"{name}用于{purpose}"]
    if entry and visible:
        sentences.append(f"从“{entry}”进入后可见{visible}")
    elif entry:
        sentences.append(f"从“{entry}”进入")
    elif visible:
        sentences.append(f"界面可见{visible}")
    return "。".join(sentences) + "。"


def linked_capabilities(raw: dict[str, Any], cap_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [cap_by_id[item] for item in text_list(raw.get("capability_ids")) if item in cap_by_id]


def node_from_raw(raw: dict[str, Any], cap_by_id: dict[str, dict[str, Any]], fallback_id: str) -> dict[str, Any]:
    linked = linked_capabilities(raw, cap_by_id)
    first = linked[0] if linked else {}
    shots = text_list(raw.get("screenshot_ids"))
    if "screenshot_ids" not in raw:
        for capability in linked:
            shots.extend(text_list(capability.get("screenshot_ids")))
    evidence = text_list(raw.get("evidence_ids"))
    if not evidence:
        for capability in linked:
            evidence.extend(text_list(capability.get("evidence_ids")))
    return {
        "id": str(raw.get("id") or first.get("id") or fallback_id),
        "name": clean(raw.get("name") or first.get("name")),
        "summary": clean(raw.get("summary") or first.get("purpose") or raw.get("name")),
        "flow_narrative": str(raw.get("flow_narrative") or "").strip(),
        "screenshot_ids": unique(shots),
        "evidence_ids": unique(evidence),
        "entry": clean(raw.get("entry") or first.get("entry")),
        "visible_elements": clean(raw.get("visible_elements") or first.get("visible_elements")),
        "purpose": clean(raw.get("purpose") or raw.get("summary") or first.get("purpose")),
        "success_feedback": clean(raw.get("success_feedback") or first.get("success_feedback")),
    }


def module_from_capability(capability: dict[str, Any], index: int) -> dict[str, Any]:
    shots = text_list(capability.get("screenshot_ids")) or [f"capability-{index:03d}"]
    return {
        "id": str(capability.get("id") or f"MOD-{index:03d}"),
        "name": clean(capability.get("name")) or f"功能{index}",
        "summary": clean(capability.get("purpose") or capability.get("name")),
        "flow_narrative": "",
        "screenshot_ids": shots,
        "evidence_ids": text_list(capability.get("evidence_ids")),
        "entry": clean(capability.get("entry")),
        "visible_elements": clean(capability.get("visible_elements")),
        "purpose": clean(capability.get("purpose")),
        "success_feedback": clean(capability.get("success_feedback")),
        "children": [],
    }


def merge_single_child(module: dict[str, Any]) -> dict[str, Any]:
    children = module.get("children") or []
    if len(children) != 1:
        return module
    child = children[0]
    merged = dict(module)
    merged["children"] = []
    if not clean(module.get("flow_narrative")):
        merged["flow_narrative"] = child.get("flow_narrative", "")
    shots = text_list(module.get("screenshot_ids"))
    for shot in text_list(child.get("screenshot_ids")):
        if shot not in shots:
            shots.append(shot)
    merged["screenshot_ids"] = shots
    if not clean(module.get("summary")):
        merged["summary"] = child.get("summary", "")
    for key in ("entry", "visible_elements", "purpose", "success_feedback", "evidence_ids"):
        if not module.get(key):
            merged[key] = child.get(key, [] if key == "evidence_ids" else "")
    return merged


def resolve_modules(business: dict[str, Any]) -> list[dict[str, Any]]:
    capabilities = [item for item in business.get("capabilities") or [] if isinstance(item, dict)]
    cap_by_id = {str(item.get("id")): item for item in capabilities if item.get("id")}
    raw_modules = [item for item in business.get("modules") or [] if isinstance(item, dict)]
    if not raw_modules:
        return [module_from_capability(item, index) for index, item in enumerate(capabilities, 1)]
    modules: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_modules, 1):
        module = node_from_raw(raw, cap_by_id, f"MOD-{index:03d}")
        children = []
        for child_index, child in enumerate(raw.get("children") or [], 1):
            if isinstance(child, dict):
                children.append(node_from_raw(child, cap_by_id, f"{module['id']}-{child_index:02d}"))
        module["children"] = children
        modules.append(merge_single_child(module))
    return [item for item in modules if item.get("name")]


def image_blocks(node: dict[str, Any], shots: dict[str, dict[str, Any]], figure: int,
                 allow_placeholders: bool) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[int], int]:
    blocks: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    numbers: list[int] = []
    for shot_id in text_list(node.get("screenshot_ids")):
        shot = shots.get(shot_id)
        caption = f"图{figure}"
        ready = bool(shot and shot.get("status") == "pass" and shot.get("path") and Path(str(shot["path"])).is_file())
        if ready:
            blocks.append({"type": "image", "path": shot["path"], "width_mm": 146.5, "caption": caption})
            numbers.append(figure)
            figure += 1
        else:
            missing.append({"name": node.get("name", ""), "screenshot_id": shot_id})
            if allow_placeholders:
                blocks.append({"type": "placeholder_image", "caption": caption})
                numbers.append(figure)
                figure += 1
    return blocks, missing, numbers, figure


def narrative_blocks(node: dict[str, Any], shots: dict[str, dict[str, Any]], figure: int,
                     allow_placeholders: bool) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    images, missing, numbers, figure = image_blocks(node, shots, figure, allow_placeholders)
    source = node.get("flow_narrative") or default_narrative(node)
    paragraph = bind_figure_refs(source, numbers)
    blocks = [{"type": "paragraph", "text": paragraph}] if paragraph else []
    blocks.extend(images)
    return blocks, missing, figure


def function_paragraph(modules: list[dict[str, Any]]) -> str:
    names = "、".join(item["name"] for item in modules)
    clauses_text = []
    for item in modules:
        summary = clean(item.get("summary")) or "完成该项业务处理"
        clauses_text.append(summary if summary.startswith(item["name"]) else f"{item['name']}模块{summary}")
    return f"该系统涵盖{names}共{len(modules)}个模块。{'；'.join(clauses_text)}。"


def flow_sections(modules: list[dict[str, Any]], shots: dict[str, dict[str, Any]],
                  allow_placeholders: bool) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    sections: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    captions: list[str] = []
    figure = 1
    for index, module in enumerate(modules, 1):
        children = module.get("children") or []
        if len(children) >= 2:
            parent_blocks, parent_missing, figure = narrative_blocks(module, shots, figure, allow_placeholders) if (
                module.get("flow_narrative") or text_list(module.get("screenshot_ids"))
            ) else ([], [], figure)
            missing.extend(parent_missing)
            sections.append({"kind": "section", "level": 2, "title": f"4.{index}{module['name']}",
                             "evidence_ids": module.get("evidence_ids", []), "blocks": parent_blocks})
            for child_index, child in enumerate(children, 1):
                blocks, child_missing, figure = narrative_blocks(child, shots, figure, allow_placeholders)
                missing.extend(child_missing)
                sections.append({"kind": "section", "level": 3, "title": f"4.{index}.{child_index}{child['name']}",
                                 "evidence_ids": child.get("evidence_ids", []), "blocks": blocks})
        else:
            blocks, module_missing, figure = narrative_blocks(module, shots, figure, allow_placeholders)
            missing.extend(module_missing)
            sections.append({"kind": "section", "level": 2, "title": f"4.{index}{module['name']}",
                             "evidence_ids": module.get("evidence_ids", []), "blocks": blocks})
    for section in sections:
        captions.extend(block.get("caption", "") for block in section.get("blocks", []) if block.get("caption"))
    return sections, missing, captions


def build_payload(facts: dict[str, Any], business: dict[str, Any], index: dict[str, Any],
                  allow_placeholders: bool) -> tuple[dict[str, Any], list[dict[str, str]]]:
    modules = resolve_modules(business)
    system, hardware, hardware_unspecified = environment_lines(business)
    written = now_iso()[:7].replace("-", ".")
    manual_facts = dict(facts)
    if isinstance(manual_facts.get("rightsholder"), dict):
        manual_facts["rightsholder"] = manual_facts["rightsholder"].get("name", "")
    version = clean(facts.get("version"))
    functions = function_paragraph(modules)
    shots = screenshot_map(index)
    flows, missing, captions = flow_sections(modules, shots, allow_placeholders)
    pages: list[dict[str, Any]] = [
        {"kind": "cover", "title": facts.get("software_full_name", ""), "blocks": [
            {"type": "cover_line", "role": "title", "text": facts.get("software_full_name", "")},
            {"type": "cover_line", "role": "document", "text": "软件设计说明书"},
            {"type": "cover_line", "role": "version", "text": f"版本号：{version}"},
            {"type": "cover_line", "role": "date", "text": f"撰写日期：{written}"},
        ]},
        {"kind": "section", "level": 1, "title": "1.软件概述",
         "blocks": [{"type": "paragraph", "text": overview_text(facts, business)}]},
        {"kind": "section", "level": 1, "title": "2.运行环境", "blocks": [
            {"type": "paragraph", "text": f"系统需求：{system}"},
            {"type": "paragraph", "text": f"硬件需求：{hardware}"},
        ]},
        {"kind": "section", "level": 1, "title": "3.主要功能",
         "blocks": [{"type": "paragraph", "text": functions}]},
        {"kind": "section", "level": 1, "title": "4.使用流程", "blocks": []},
        *flows,
    ]
    module_names = [item["name"] for item in modules]
    payload = {
        "facts": manual_facts,
        "document": {"title": "软件设计说明书", "type": "软件著作权鉴别材料", "edition": "正式版",
                     "status": "待渲染复核", "date": written, "version_line": f"版本号：{version}"},
        "pages": pages,
        "generation": {
            "document_kind": "design_specification",
            "section_titles": list(SECTION_TITLES),
            "module_names": module_names,
            "flow_headings": [page["title"] for page in flows],
            "function_paragraph": functions,
            "figure_captions": captions,
            "hardware_text": hardware,
            "hardware_unspecified": hardware_unspecified,
            "screenshot_mode": index.get("mode"),
            "screenshot_state": index.get("state"),
            "missing_screenshots": missing,
        },
    }
    return payload, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--business", required=True, type=Path)
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-placeholders", action="store_true")
    args = parser.parse_args()
    facts, business = load_json(args.facts), load_json(args.business)
    index = load_json(args.screenshots) if args.screenshots and args.screenshots.exists() else {"captures": []}
    slots = find_slots({"facts": facts, "business": business})
    if slots:
        print(f"PENDING_SLOTS={len(slots)}")
        return 2
    if not resolve_modules(business):
        print("PENDING_MODULES=1")
        return 2
    payload, missing = build_payload(facts, business, index, args.allow_placeholders)
    if missing and not args.allow_placeholders:
        print(f"MISSING_SCREENSHOTS={len(missing)}")
        return 3
    save_json(args.output.resolve(), payload)
    generation = payload["generation"]
    print(f"DESIGN_SPEC_CONTENT={args.output.resolve()}")
    print(f"MODULES={len(generation['module_names'])} FIGURES={len(generation['figure_captions'])} "
          f"MISSING={len(missing)} HARDWARE_UNSPECIFIED={str(generation['hardware_unspecified']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_design_spec_content import (GENERIC_HARDWARE, build_payload, environment_lines,
                                          resolve_modules)


def facts() -> dict:
    return {"software_full_name": "文本摘要命令行工具", "version": "V1.0", "rightsholder": "开源测试组织"}


def business(**overrides) -> dict:
    payload = {
        "software_purpose": "读取文本并生成规范化摘要",
        "target_users": "内部工作人员",
        "industry_domain": "通用软件工具",
        "development_purpose": "提高文本摘要处理效率",
        "runtime_platform": "Windows、macOS、Linux",
        "runtime_support": "具备文件读写权限的计算机",
        "technical_features": "确定性处理与输入校验",
        "capabilities": [
            {"id": "CAP-core", "name": "摘要处理", "purpose": "生成文本摘要", "entry": "命令行入口",
             "visible_elements": "输入参数和结果输出", "evidence_ids": ["FILE-core"],
             "screenshot_ids": ["shot-core"]},
            {"id": "CAP-log", "name": "结果核对", "purpose": "核对摘要是否对应原文", "entry": "输出文件",
             "visible_elements": "摘要正文", "evidence_ids": ["FILE-log"], "screenshot_ids": ["shot-log"]},
        ],
    }
    payload.update(overrides)
    return payload


def shot(path: Path, second: Path | None = None) -> dict:
    captures = [{"id": "shot-core", "status": "pass", "path": str(path)}]
    if second is not None:
        captures.append({"id": "shot-log", "status": "pass", "path": str(second)})
    return {"captures": captures}


class DesignSpecContentTests(unittest.TestCase):
    def test_capabilities_become_modules_with_continuous_figures(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "core.png"
            second = Path(temp) / "log.png"
            Image.new("RGB", (1200, 700), "white").save(image)
            Image.new("RGB", (1200, 700), "white").save(second)
            payload, missing = build_payload(facts(), business(), shot(image, second), False)
        titles = [page["title"] for page in payload["pages"]]
        self.assertEqual(titles[:5], ["文本摘要命令行工具", "1.软件概述", "2.运行环境", "3.主要功能", "4.使用流程"])
        self.assertIn("4.1摘要处理", titles)
        self.assertIn("4.2结果核对", titles)
        self.assertNotIn("4.1.1", "".join(titles))
        generation = payload["generation"]
        self.assertEqual(generation["module_names"], ["摘要处理", "结果核对"])
        self.assertEqual(generation["figure_captions"], ["图1", "图2"])
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertIn("【图1】", rendered)
        self.assertIn("【图2】", rendered)
        self.assertEqual(missing, [])
        overview = payload["pages"][1]["blocks"][0]["text"]
        for fragment in ("内部工作人员", "读取文本并生成规范化摘要", "提高文本摘要处理效率",
                         "确定性处理与输入校验", "通用软件工具"):
            self.assertIn(fragment, overview)
        self.assertNotIn("i5", generation["hardware_text"])
        self.assertNotIn("16G", generation["hardware_text"])
        self.assertEqual(generation["hardware_text"], GENERIC_HARDWARE)
        self.assertTrue(generation["hardware_unspecified"])

    def test_children_collapse_when_only_one_and_expand_when_two(self):
        single = business(modules=[{
            "name": "专注训练", "summary": "开展计时专注任务",
            "children": [{"name": "迷你窗口", "flow_narrative": "训练开始后可以唤起迷你窗口。",
                          "screenshot_ids": ["shot-core"]}],
        }], capabilities=[])
        modules = resolve_modules(single)
        self.assertEqual(modules[0]["name"], "专注训练")
        self.assertEqual(modules[0]["children"], [])
        self.assertIn("迷你窗口", modules[0]["flow_narrative"])
        multiple = business(modules=[{
            "name": "专注训练", "summary": "开展计时专注任务", "screenshot_ids": [],
            "children": [
                {"name": "专项训练", "flow_narrative": "打开舒尔特方格如【图1】。", "screenshot_ids": ["shot-core"]},
                {"name": "训练历史", "flow_narrative": "查看本周记录。", "screenshot_ids": []},
            ],
        }], capabilities=[])
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "core.png"
            Image.new("RGB", (1200, 700), "white").save(image)
            payload, _missing = build_payload(facts(), multiple, shot(image), False)
        headings = payload["generation"]["flow_headings"]
        self.assertEqual(headings, ["4.1专注训练", "4.1.1专项训练", "4.1.2训练历史"])
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertIn("【图1】", rendered)
        self.assertNotIn("【图2】", rendered)
        self.assertIn("专注训练", payload["generation"]["function_paragraph"])
        self.assertNotIn("专项训练模块", payload["generation"]["function_paragraph"])

    def test_hardware_comes_only_from_stated_environment(self):
        system, hardware, unspecified = environment_lines({
            "runtime_platform": "Windows10；i5及以上CPU",
            "runtime_support": "内存16G及以上；硬盘512G及以上",
        })
        self.assertEqual(system, "Windows10")
        self.assertIn("i5及以上CPU", hardware)
        self.assertIn("内存16G及以上", hardware)
        self.assertFalse(unspecified)
        system, hardware, unspecified = environment_lines({
            "runtime_platform": "Windows10", "hardware_requirements": "内存8G及以上",
        })
        self.assertEqual(hardware, "内存8G及以上")
        self.assertFalse(unspecified)

    def test_missing_screenshot_blocks_without_placeholder_flag(self):
        payload, missing = build_payload(facts(), business(), {"captures": []}, False)
        self.assertEqual(len(missing), 2)
        self.assertEqual(payload["generation"]["figure_captions"], [])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "facts.json").write_text(json.dumps(facts(), ensure_ascii=False), encoding="utf-8")
            (root / "business.json").write_text(json.dumps(business(), ensure_ascii=False), encoding="utf-8")
            (root / "shots.json").write_text("{}", encoding="utf-8")
            blocked = subprocess.run([
                sys.executable, str(SCRIPTS / "generate_design_spec_content.py"),
                "--facts", str(root / "facts.json"), "--business", str(root / "business.json"),
                "--screenshots", str(root / "shots.json"), "--output", str(root / "spec.json"),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(blocked.returncode, 3, blocked.stdout + blocked.stderr)
            self.assertFalse((root / "spec.json").exists())
            allowed = subprocess.run([
                sys.executable, str(SCRIPTS / "generate_design_spec_content.py"),
                "--facts", str(root / "facts.json"), "--business", str(root / "business.json"),
                "--screenshots", str(root / "shots.json"), "--output", str(root / "spec.json"),
                "--allow-placeholders",
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
            saved = json.loads((root / "spec.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["generation"]["figure_captions"], ["图1", "图2"])
            self.assertIn("placeholder_image", json.dumps(saved, ensure_ascii=False))


class DesignSpecDocumentTests(unittest.TestCase):
    def test_docx_cover_footer_figures_and_no_toc(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "core.png"
            Image.new("RGB", (1280, 800), "white").save(image)
            payload, _missing = build_payload(facts(), business(), shot(image), False)
            source = root / "spec.json"
            source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            output = root / "spec.docx"
            completed = subprocess.run([
                sys.executable, str(SCRIPTS / "build_design_spec.py"),
                "--input", str(source), "--output", str(output),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            with zipfile.ZipFile(output) as package:
                document_xml = package.read("word/document.xml").decode("utf-8")
                footer_xml = "\n".join(package.read(name).decode("utf-8")
                                       for name in package.namelist() if name.startswith("word/footer"))
            text = "".join(part.split("<")[0] for part in document_xml.split(">"))
            self.assertIn("文本摘要命令行工具", text)
            self.assertIn("软件设计说明书", text)
            self.assertIn("版本号：V1.0", text)
            self.assertIn("撰写日期：", text)
            self.assertIn("1.软件概述", text)
            self.assertIn("2.运行环境", text)
            self.assertIn("3.主要功能", text)
            self.assertIn("4.使用流程", text)
            self.assertIn("图1", text)
            self.assertIn("w:drawing", document_xml)
            self.assertNotIn("TOC", document_xml)
            self.assertIn("PAGE", footer_xml)
            self.assertIn("-", footer_xml)
            images = [
                block for page in payload["pages"] for block in page.get("blocks") or []
                if block.get("type") == "image"
            ]
            self.assertTrue(images)
            self.assertTrue(all(block["width_mm"] == 146.5 for block in images))

    def test_docx_layout_matches_filing_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "screen.png"
            Image.new("RGB", (1600, 1000), "white").save(image)
            payload = {
                "facts": {"software_full_name": "版式样例软件", "version": "V1.0", "rightsholder": "测试"},
                "document": {"title": "软件设计说明书", "type": "软件著作权鉴别材料"},
                "pages": [
                    {"kind": "cover", "blocks": [
                        {"type": "cover_line", "role": "title", "text": "版式样例软件"},
                        {"type": "cover_line", "role": "document", "text": "软件设计说明书"},
                        {"type": "cover_line", "role": "version", "text": "版本号：V1.0"},
                        {"type": "cover_line", "role": "date", "text": "撰写日期：2026.10"},
                    ]},
                    {"kind": "section", "level": 1, "title": "1.软件概述",
                     "blocks": [{"type": "paragraph", "text": "这是概述正文。"}]},
                    {"kind": "section", "level": 2, "title": "4.1用户登录", "blocks": []},
                    {"kind": "section", "level": 3, "title": "4.1.1专注训练", "blocks": [
                        {"type": "paragraph", "text": "这是流程正文。"},
                        {"type": "image", "path": str(image), "width_mm": 200, "caption": "图1"},
                    ]},
                ],
            }
            source = root / "spec.json"
            source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            output = root / "spec.docx"
            completed = subprocess.run([
                sys.executable, str(SCRIPTS / "build_design_spec.py"),
                "--input", str(source), "--output", str(output),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            with zipfile.ZipFile(output) as package:
                document_xml = package.read("word/document.xml")
                footer_parts = [
                    package.read(name)
                    for name in package.namelist() if name.startswith("word/footer") and name.endswith(".xml")
                ]
                settings_xml = package.read("word/settings.xml").decode("utf-8")
            root_xml = ElementTree.fromstring(document_xml)
            sections = root_xml.findall(".//w:sectPr", NS)
            self.assertGreaterEqual(len(sections), 2)
            for section in sections:
                margins = section.find("w:pgMar", NS)
                self.assertEqual(attr(margins, "top"), "1440")
                self.assertEqual(attr(margins, "bottom"), "1440")
                self.assertEqual(attr(margins, "left"), "1800")
                self.assertEqual(attr(margins, "right"), "1800")
                self.assertEqual(attr(margins, "header"), "851")
                self.assertEqual(attr(margins, "footer"), "992")
                numbering = section.find("w:pgNumType", NS)
                self.assertEqual(attr(numbering, "fmt"), "numberInDash")
                self.assertEqual(attr(numbering, "start"), "1")
                grid = section.find("w:docGrid", NS)
                self.assertEqual(attr(grid, "linePitch"), "312")
            self.assertIn("compressPunctuation", settings_xml)
            self.assertNotIn("TOC", document_xml.decode("utf-8"))
            self.assertGreaterEqual(len(footer_parts), 2)
            for footer_xml in footer_parts:
                footer_root = ElementTree.fromstring(footer_xml)
                footer_text = "".join(node.text or "" for node in footer_root.findall(".//w:t", NS))
                self.assertEqual(footer_text, "- 1 -")
                self.assertIn(" PAGE ", footer_xml.decode("utf-8"))
                self.assertEqual(size_of(footer_root), "18")
                self.assertEqual(font_of(footer_root), "SimSun")
            cover = paragraph_with(root_xml, "版式样例软件")
            self.assertEqual(font_of(cover), "SimHei")
            self.assertEqual(size_of(cover), "44")
            self.assertEqual(size_of(paragraph_with(root_xml, "1.软件概述")), "32")
            self.assertEqual(outline_of(paragraph_with(root_xml, "1.软件概述")), "0")
            self.assertEqual(font_of(paragraph_with(root_xml, "1.软件概述")), "SimSun")
            self.assertEqual(size_of(paragraph_with(root_xml, "4.1用户登录")), "28")
            self.assertEqual(outline_of(paragraph_with(root_xml, "4.1用户登录")), "1")
            third = paragraph_with(root_xml, "4.1.1专注训练")
            self.assertEqual(size_of(third), "24")
            self.assertEqual(outline_of(third), "2")
            self.assertEqual(spacing_line(third), "360")
            self.assertEqual(attr(third.find("./w:pPr/w:jc", NS), "val"), "left")
            body = paragraph_with(root_xml, "这是概述正文。")
            self.assertEqual(font_of(body), "SimSun")
            self.assertEqual(size_of(body), "21")
            self.assertEqual(indent_attr(body, "firstLine"), "420")
            self.assertEqual(indent_attr(body, "firstLineChars"), "200")
            self.assertEqual(size_of(paragraph_with(root_xml, "图1")), "15")
            self.assertEqual(font_of(paragraph_with(root_xml, "图1")), "SimSun")
            self.assertIn('cx="5274000"', document_xml.decode("utf-8"))


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def paragraph_with(root, text: str):
    for paragraph in root.findall(".//w:p", {"w": W[1:-1]}):
        joined = "".join(node.text or "" for node in paragraph.findall(".//w:t", {"w": W[1:-1]}))
        if joined == text:
            return paragraph
    raise AssertionError(f"missing paragraph: {text}")


def size_of(paragraph) -> str:
    sizes = [node.get(f"{W}val") for node in paragraph.findall(".//w:sz", {"w": W[1:-1]})]
    sizes = [item for item in sizes if item]
    if not sizes:
        raise AssertionError("paragraph has no font size")
    return sizes[0]


def outline_of(paragraph) -> str:
    node = paragraph.find("./w:pPr/w:outlineLvl", {"w": W[1:-1]})
    if node is None:
        raise AssertionError("paragraph has no outline level")
    return node.get(f"{W}val")


def font_of(paragraph) -> str:
    node = paragraph.find(".//w:rFonts", NS)
    if node is None:
        raise AssertionError("paragraph has no font")
    return node.get(f"{W}eastAsia")


def spacing_line(paragraph) -> str:
    node = paragraph.find("./w:pPr/w:spacing", NS)
    if node is None:
        raise AssertionError("paragraph has no spacing")
    return node.get(f"{W}line")


def indent_attr(paragraph, name: str) -> str:
    node = paragraph.find("./w:pPr/w:ind", NS)
    if node is None:
        raise AssertionError("paragraph has no indent")
    return node.get(f"{W}{name}")


def attr(node, name: str) -> str:
    if node is None:
        raise AssertionError(f"missing element for {name}")
    return node.get(f"{W}{name}")


NS = {"w": W[1:-1]}


if __name__ == "__main__":
    unittest.main()

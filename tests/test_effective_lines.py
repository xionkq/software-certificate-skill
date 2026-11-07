from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def write_manifest(project: Path, ordered: list[str]) -> Path:
    manifest = project / "manifest.json"
    manifest.write_text(json.dumps({
        "include": ["**/*.py"], "exclude": [], "ordered_files": ordered,
        "review": {"confirmed_by": "unit", "confirmed_at": "2026-08-12T00:00:00+08:00",
                   "open_source_boundary_checked": True, "generated_code_boundary_checked": True,
                   "secret_scan_checked": True}}, ensure_ascii=False), encoding="utf-8")
    return manifest


def compose(project: Path, manifest: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([
        sys.executable, str(SCRIPTS / "compose_code.py"), "--project", str(project),
        "--manifest", str(manifest), "--output-dir", str(output),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)


class SourceLineTests(unittest.TestCase):
    def test_short_corpus_blocks_instead_of_padding(self):
        with tempfile.TemporaryDirectory(prefix="softcert-effective-") as temp:
            root = Path(temp)
            project, output = root / "project", root / "output"
            project.mkdir()
            blocks = []
            for index in range(1, 131):
                blocks.append(f"def handler_{index}(payload):")
                blocks.append(f"    return normalize(payload, key={index})")
                blocks.append("")
                blocks.append("   ")
                blocks.append("")
            (project / "engine.py").write_text("\n".join(blocks) + "\n", encoding="utf-8")
            compose_result = compose(project, write_manifest(project, ["engine.py"]), output)
            self.assertEqual(compose_result.returncode, 3, compose_result.stdout + compose_result.stderr)
            self.assertFalse((output / "source-front.docx").exists())
            provenance = json.loads((output / "source-provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(provenance["selection"], "blocked")
            self.assertEqual(provenance["blank_lines_excluded"], 0)
            self.assertGreater(provenance["blank_lines_retained"], 0)
            raw = (output / "source-full.txt").read_text(encoding="utf-8")
            self.assertIn("\n\n", raw.replace("\n\f\n", "\n"))

    def test_wrapped_lines_keep_every_character_and_volumes_do_not_share_files(self):
        with tempfile.TemporaryDirectory(prefix="softcert-wrap-") as temp:
            root = Path(temp)
            project, output = root / "project", root / "output"
            project.mkdir()
            ordered = []
            marker = "TOKEN_" + ("A" * 180) + "_END"
            for index in range(80):
                relative = f"src/service_{index:03d}.py"
                path = project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                lines = [f"value_{index}_{line} = process_item({line})" for line in range(80)]
                if index == 0:
                    lines[0] = marker
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                ordered.append(relative)
            compose_result = compose(project, write_manifest(project, ordered), output)
            self.assertEqual(compose_result.returncode, 0, compose_result.stdout + compose_result.stderr)
            provenance = json.loads((output / "source-provenance.json").read_text(encoding="utf-8"))
            front = provenance["filing_groups"]["front"]
            back = provenance["filing_groups"]["back"]
            self.assertTrue(55 <= front["page_count"] <= 65)
            self.assertTrue(55 <= back["page_count"] <= 65)
            self.assertTrue(120 <= front["page_count"] + back["page_count"] <= 125)
            self.assertTrue(set(front["files"]).isdisjoint(set(back["files"])))
            self.assertNotIn(front["files"][-1], back["files"])
            self.assertTrue(provenance["gap_files"])
            self.assertEqual(back["page_number_start"], front["page_count"] + 1)
            flat = (output / "source-full.txt").read_text(encoding="utf-8").replace("\n", "").replace("\f", "")
            self.assertIn(marker, flat)
            with zipfile.ZipFile(output / "source-front.docx") as archive:
                document = archive.read("word/document.xml").decode("utf-8")
                header = archive.read("word/header1.xml").decode("utf-8")
                footer = archive.read("word/footer1.xml").decode("utf-8")
            self.assertNotIn("PAGE", header)
            self.assertNotIn("<w:t", header.replace("<w:tabs", ""))
            self.assertIn("PAGE", footer)
            self.assertIn("Calibri", document)
            self.assertIn('w:val="21"', document)
            self.assertIn("pageBreakBefore", document)

    def test_installed_calibri_keeps_a_normal_use_line_together(self):
        sys.path.insert(0, str(SCRIPTS))
        import compose_code
        if not compose_code.ascii_metrics_loaded():
            self.skipTest("Calibri metrics unavailable")
        line = "use crate::error::{AppError, AppResult};"
        self.assertEqual(compose_code.wrap_source_line(line), [line])
        wide = "@" * 80
        parts = compose_code.wrap_source_line(wide)
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), wide)


if __name__ == "__main__":
    unittest.main()

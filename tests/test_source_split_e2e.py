from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def explicit_docx_pages(path: Path) -> int:
    """Each page after the first starts with a w:pageBreakBefore paragraph."""
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", "replace")
    return xml.count("w:pageBreakBefore") + 1


@unittest.skipUnless(os.environ.get("RUN_DOCUMENT_E2E") == "1", "set RUN_DOCUMENT_E2E=1 with LibreOffice available")
class SourceSplitEndToEndTests(unittest.TestCase):
    def test_front_and_back_volumes_are_separate_files(self):
        with tempfile.TemporaryDirectory(prefix="softcert-split-") as temp:
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
            manifest = project / "manifest.json"
            manifest.write_text(json.dumps({
                "include": ["**/*.py"], "exclude": [], "ordered_files": ordered,
                "review": {"confirmed_by": "e2e", "confirmed_at": "2026-08-11T00:00:00+08:00",
                           "open_source_boundary_checked": True, "generated_code_boundary_checked": True,
                           "secret_scan_checked": True}}, ensure_ascii=False), encoding="utf-8")
            compose = subprocess.run([
                sys.executable, str(SCRIPTS / "compose_code.py"), "--project", str(project),
                "--manifest", str(manifest), "--output-dir", str(output),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            self.assertEqual(compose.returncode, 0, compose.stdout + compose.stderr)
            provenance = json.loads((output / "source-provenance.json").read_text(encoding="utf-8"))
            front = provenance["filing_groups"]["front"]
            back = provenance["filing_groups"]["back"]
            self.assertTrue(set(front["files"]).isdisjoint(set(back["files"])))
            self.assertNotIn(front["files"][-1], back["files"])
            self.assertEqual(back["last_output_line"], provenance["full_line_count"])
            flat = (output / "source-full.txt").read_text(encoding="utf-8").replace("\n", "").replace("\f", "")
            self.assertIn(marker, flat)
            for key in ("front", "back"):
                expected = provenance["filing_groups"][key]["page_count"]
                docx, pdf = output / f"source-{key}.docx", output / f"source-{key}.pdf"
                report = output / f"{key}.render.json"
                rendered = output / f"{key}-pages"
                conversion = subprocess.run([
                    sys.executable, str(SCRIPTS / "convert_document.py"), "--input", str(docx),
                    "--pdf", str(pdf), "--report", str(report), "--render-dir", str(rendered),
                    "--expected-pages", str(expected), "--timeout-seconds", "240",
                ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=420)
                self.assertEqual(conversion.returncode, 0, conversion.stdout + conversion.stderr)
                self.assertEqual(explicit_docx_pages(docx), expected)
                self.assertEqual(len(PdfReader(str(pdf)).pages), expected)
                model = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual(model["pdf_pages"], expected)
                self.assertEqual(model["rendered_pages"], expected)
                self.assertTrue(model["conversion"]["engine"].startswith("LibreOffice"))
                self.assertEqual(model["conversion_attempts"][0]["engine"], "LibreOffice")
                self.assertEqual(model["conversion_attempts"][0]["status"], "pass")
                self.assertTrue(Path(model["conversion_attempts"][0]["diagnostic"]).is_file())


if __name__ == "__main__":
    unittest.main()

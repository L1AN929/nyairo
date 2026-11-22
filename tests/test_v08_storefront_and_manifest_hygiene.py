"""Storefront (README / INSTALL) and release-manifest hygiene for v0.8.0.

Pins three things that could silently regress:

* the desktop installer is advertised first, and the CLI path second;
* historical audit evidence lives under ``docs/audits/`` and every relative
  Markdown link in the shipped storefront docs still resolves;
* ``MANIFEST.json`` describes the current ``0.8.0`` source tree (moved audit
  files included, streamed state-tx modules included, README hash exact).
"""

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ARCHIVED_AUDITS = (
    "HEALTHCHECK.md",
    "HERMES_FIXES.md",
    "HERMES_UPDATE_REVIEW.json",
    "PRIVACY_REVIEW.md",
    "PRIVACY_TEST_EVIDENCE.json",
    "REVIEW_REPORT.md",
    "TEST_EVIDENCE.json",
    "UPDATE_TEST_EVIDENCE.json",
)

ROOT_TEMP_FILES = (
    ".b-final.log",
    ".journey-old.log",
    ".tmp-stage8-predemo.log",
    ".tmp_probe_e.py",
    "20261106-combined-a-prepare.sh",
    "20261106-combined-a-run.sh",
    "life_request_budget.lock",
)

STOREFRONT_DOCS = ("README.md", "INSTALL.md", "UPDATE.md", "TESTING.md", "KNOWN_ISSUES.md")

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


class ArchivedEvidence(unittest.TestCase):
    def test_audit_files_live_under_docs_audits(self):
        for name in ARCHIVED_AUDITS:
            self.assertFalse((ROOT / name).exists(), f"{name} still at repo root")
            self.assertTrue((ROOT / "docs" / "audits" / name).is_file(), name)
        self.assertTrue((ROOT / "docs" / "audits" / "README.md").is_file())

    def test_root_temp_files_are_gone(self):
        for name in ROOT_TEMP_FILES:
            self.assertFalse((ROOT / name).exists(), f"{name} still at repo root")


class Storefront(unittest.TestCase):
    def test_readme_puts_windows_desktop_before_wsl_cli(self):
        text = read("README.md")
        quickstart = text.index("## 快速上手")
        desktop = text.index("nyairo-0.8.0-setup.exe", quickstart)
        cli = text.index("https://nyairo.com/install.sh", quickstart)
        self.assertLess(desktop, cli, "desktop installer must precede the WSL CLI path")
        self.assertIn("方式一", text[quickstart:])

    def test_readme_highlights_byok_deterministic_continuity(self):
        text = read("README.md")
        self.assertIn("BYOK", text)
        self.assertIn("0.8.0", text)
        self.assertIn("确定性", text)
        self.assertIn("Token", text)
        for model in ("DeepSeek", "Ollama"):
            self.assertIn(model, text)

    def test_no_stale_v07_download_links_in_storefront(self):
        for name in ("README.md", "INSTALL.md"):
            text = read(name)
            self.assertNotIn("nyairo-0.7.0-setup.exe", text, name)
            self.assertNotIn("nyairo-v0.7.0.zip", text, name)

    def test_install_advertises_v080(self):
        text = read("INSTALL.md")
        self.assertIn("v0.8.0", text)
        self.assertIn("nyairo-v0.8.0.zip", text)
        self.assertIn("nyairo-0.8.0-setup.exe", text)
        self.assertNotIn("nyairo-0.7.0-setup.exe", text)

    def test_audit_links_point_into_docs_audits(self):
        self.assertIn("docs/audits/PRIVACY_REVIEW.md", read("INSTALL.md"))
        self.assertIn("docs/audits/PRIVACY_REVIEW.md", read("UPDATE.md"))
        self.assertIn("docs/audits/HEALTHCHECK.md", read("TESTING.md"))
        self.assertIn("docs/audits/PRIVACY_REVIEW.md", read("KNOWN_ISSUES.md"))

    def test_relative_markdown_links_resolve(self):
        problems = []
        for name in STOREFRONT_DOCS:
            base = ROOT / name
            for match in LINK.finditer(base.read_text(encoding="utf-8")):
                target = match.group(1).strip()
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                target = target.split("#")[0]
                if not target:
                    continue
                if not (base.parent / target).resolve().exists():
                    problems.append(f"{name}: {target}")
        self.assertEqual(problems, [])


class ManifestHygiene(unittest.TestCase):
    def test_manifest_describes_080_tree(self):
        manifest = json.loads(read("MANIFEST.json"))
        files = manifest["files"]
        self.assertEqual(manifest["version"], "0.8.0")
        self.assertEqual(manifest["file_count"], len(files))

        for name in ARCHIVED_AUDITS:
            self.assertNotIn(name, files)
            self.assertIn(f"docs/audits/{name}", files)

        for required in (
            "docs/audits/PRIVACY_REVIEW.md",
            "components/life/scripts/state_tx_guard.py",
            "components/life/scripts/scoped_content_record.py",
            "README.md",
        ):
            self.assertIn(required, files)

        readme_digest = hashlib.sha256((ROOT / "README.md").read_bytes()).hexdigest()
        self.assertEqual(files["README.md"], readme_digest)

    def test_release_registration_matches_manifest_version(self):
        manifest = json.loads(read("MANIFEST.json"))
        release = json.loads(read("scripts/release.json"))
        self.assertEqual(release["version"], "v" + manifest["version"].removeprefix("v"))


if __name__ == "__main__":
    unittest.main()

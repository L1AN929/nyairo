#!/usr/bin/env python3
"""v0.9 Stage 1 regressions: hard defects 1A (daily token pre-flight ceiling) & 1B (manual ZIP install).

Covers (llm_calls == 0):
1. life_request_budget daily token ceiling pre-flight enforcement:
   - 49,000 + 2,000 -> refused pre-flight (REASON_DAILY_TOKEN_BUDGET), persisted utc_day_tokens stays 49,000;
   - 48,000 + 2,000 -> accepted at exact cap (utc_day_tokens == 50,000), subsequent reserve(est_tokens=1) and
     reserve(est_tokens=0) refused;
   - check(est_tokens=2000) is 100% read-only / zero-write on both absent and existing state roots, and
     validates est_tokens strictly.
2. INSTALL.md manual ZIP unpack vs scripts/bootstrap.sh:118-122 guard:
   - scripts/bootstrap.sh == website/install.sh byte-for-byte identical;
   - INSTALL.md `## 方式三` commands write lowercase verified archive hash to
     `$HOME/.local/share/nyairo/v0.8.0.source-sha256` before invoking `bash scripts/bootstrap.sh`;
   - Executing the extracted `## 方式三` commands satisfies `bootstrap.sh:118-122` and skips re-downloading
     the release ZIP (while omitting `.source-sha256` triggers the expected guard failure).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import life_request_budget as lrb  # noqa: E402


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.stat()
        digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "DIR"
        snap[rel] = (st.st_size, int(st.st_mtime_ns), digest)
    return snap


def _extract_way3_bash_block(install_md_text: str) -> str:
    start = install_md_text.index("## 方式三")
    tail = install_md_text[start:]
    next_sep = tail.find("\n---")
    section = tail[:next_sep] if next_sep != -1 else tail
    match = re.search(r"```bash\n(.*?)\n```", section, flags=re.DOTALL)
    if not match:
        raise AssertionError("Could not find fenced bash block under `## 方式三` in INSTALL.md")
    return match.group(1)


class TestV09DailyTokenBudgetPreFlight(unittest.TestCase):
    """Defect 1A: daily_token_budget pre-reservation ceiling + zero-write check(est_tokens=...)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v09-lrb-preflight-"))
        self.root = self.tmp / "state"
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _budget(self, state_root: Path | None = None, **kwargs: object) -> lrb.LifeRequestBudget:
        kwargs.setdefault("utc_day_max", 24)
        kwargs.setdefault("daily_token_budget", lrb.DEFAULT_DAILY_TOKEN_BUDGET)
        return lrb.LifeRequestBudget(state_root or self.root, **kwargs)

    def test_49000_plus_2000_refused_preflight_leaving_49000_persisted(self) -> None:
        budget = self._budget()
        first = budget.reserve(lrb.REQUEST_KIND_CONTENT, est_tokens=49_000)
        self.assertEqual(first["utc_day_tokens"], 49_000)
        self.assertEqual(first["utc_day_calls"], 1)

        pre_over = budget.check(est_tokens=2_000)
        self.assertFalse(pre_over["allowed"])
        self.assertEqual(pre_over["reason_code"], lrb.REASON_DAILY_TOKEN_BUDGET)
        self.assertIn("reserved 49000+2000 of 50000 est tokens", pre_over["detail"])

        pre_fit = budget.check(est_tokens=1_000)
        self.assertTrue(pre_fit["allowed"])
        self.assertEqual(pre_fit["reason_code"], "")

        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.reserve(lrb.REQUEST_KIND_CONTENT, est_tokens=2_000)
        self.assertEqual(caught.exception.reason_code, lrb.REASON_DAILY_TOKEN_BUDGET)
        self.assertIn("reserved 49000+2000 of 50000 est tokens", caught.exception.detail)

        # Conservative default (2,000) must also be refused pre-flight at 49,000.
        with self.assertRaises(lrb.RequestBudgetError) as caught_default:
            budget.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(caught_default.exception.reason_code, lrb.REASON_DAILY_TOKEN_BUDGET)

        reopened = self._budget()
        view = reopened.view()
        self.assertEqual(view["utc_day_tokens"], 49_000)
        self.assertEqual(view["utc_day_calls"], 1)

    def test_48000_plus_2000_accepted_exact_cap_then_subsequent_refused(self) -> None:
        budget = self._budget()
        budget.reserve(lrb.REQUEST_KIND_CONTENT, est_tokens=48_000)

        verdict = budget.check(est_tokens=2_000)
        self.assertTrue(verdict["allowed"])
        self.assertEqual(verdict["reason_code"], "")

        second = budget.reserve(lrb.REQUEST_KIND_CONTENT, est_tokens=2_000)
        self.assertEqual(second["utc_day_tokens"], 50_000)
        self.assertEqual(second["utc_day_calls"], 2)

        for est in (1, 0):
            with self.assertRaises(lrb.RequestBudgetError) as caught:
                budget.reserve(lrb.REQUEST_KIND_CONTENT, est_tokens=est)
            self.assertEqual(caught.exception.reason_code, lrb.REASON_DAILY_TOKEN_BUDGET)
            self.assertIn("reserved 50000 of 50000 est tokens", caught.exception.detail)

        after_check = budget.check()
        self.assertFalse(after_check["allowed"])
        self.assertEqual(after_check["reason_code"], lrb.REASON_DAILY_TOKEN_BUDGET)
        self.assertIn("reserved 50000 of 50000 est tokens", after_check["detail"])

        view = budget.view()
        self.assertEqual(view["utc_day_tokens"], 50_000)
        self.assertEqual(view["utc_day_calls"], 2)

    def test_check_with_est_tokens_is_zero_write_on_absent_and_existing_roots(self) -> None:
        absent_root = self.tmp / "never-created" / "life-state"
        absent_budget = self._budget(state_root=absent_root)

        ok = absent_budget.check(est_tokens=2_000)
        self.assertTrue(ok["allowed"])
        self.assertFalse(absent_root.exists(), "check(est_tokens=2000) must not create state_root")
        self.assertFalse(absent_root.parent.exists(), "check(est_tokens=2000) must not create parent dirs")

        over = absent_budget.check(est_tokens=50_001)
        self.assertFalse(over["allowed"])
        self.assertEqual(over["reason_code"], lrb.REASON_DAILY_TOKEN_BUDGET)
        self.assertIn("reserved 0+50001 of 50000 est tokens", over["detail"])
        self.assertFalse(absent_root.exists())

        budget = self._budget()
        budget.reserve(lrb.REQUEST_KIND_CONTENT, est_tokens=49_000)
        before = _snapshot_tree(self.root)

        self.assertFalse(budget.check(est_tokens=2_000)["allowed"])
        self.assertTrue(budget.check(est_tokens=1_000)["allowed"])
        self.assertTrue(budget.check()["allowed"])

        after = _snapshot_tree(self.root)
        self.assertEqual(before, after, "check(est_tokens=...) must produce zero disk mutations")

    def test_check_rejects_invalid_est_tokens_without_writes(self) -> None:
        absent_root = self.tmp / "invalid-est-check"
        budget = self._budget(state_root=absent_root)
        for bad in (-1, True, False, "2000", 1.5, None):
            with self.assertRaises(ValueError):
                budget.check(est_tokens=bad)  # type: ignore[arg-type]
        self.assertFalse(absent_root.exists())


class TestV09InstallManualZipAndBootstrapParity(unittest.TestCase):
    """Defect 1B: INSTALL.md manual ZIP steps satisfy scripts/bootstrap.sh:118-122 + website parity."""

    def test_bootstrap_sh_and_website_install_sh_are_byte_identical(self) -> None:
        boot = (ROOT / "scripts" / "bootstrap.sh").read_bytes()
        web = (ROOT / "website" / "install.sh").read_bytes()
        self.assertEqual(boot, web, "scripts/bootstrap.sh must remain byte-identical to website/install.sh")

    def test_install_md_way3_and_user_guide_record_source_sha256_before_bootstrap(self) -> None:
        install_md = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
        block = _extract_way3_bash_block(install_md)

        expected_sha_cmd = (
            'awk \'NR==1 {print tolower($1)}\' nyairo-v0.8.0.zip.sha256 '
            '> "$HOME/.local/share/nyairo/v0.8.0.source-sha256"'
        )
        expected_boot_cmd = 'cd "$HOME/.local/share/nyairo/releases/v0.8.0" && bash scripts/bootstrap.sh'

        self.assertIn("sha256sum -c nyairo-v0.8.0.zip.sha256", block)
        self.assertIn('python3 -m zipfile -e nyairo-v0.8.0.zip "$HOME/.local/share/nyairo/releases/v0.8.0"', block)
        self.assertIn('python3 "$HOME/.local/share/nyairo/releases/v0.8.0/scripts/verify_manifest.py"', block)
        self.assertIn(expected_sha_cmd, block)
        self.assertIn(expected_boot_cmd, block)
        self.assertLess(
            block.index(expected_sha_cmd),
            block.index(expected_boot_cmd),
            ".source-sha256 hash marker must be written BEFORE invoking scripts/bootstrap.sh",
        )

        guide = (ROOT / "docs" / "USER_GUIDE.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn(expected_sha_cmd, guide)

    @unittest.skipUnless(os.name == "posix", "scripts/bootstrap.sh runs in Linux / WSL")
    def test_install_md_way3_commands_satisfy_bootstrap_guard_and_skip_redownload(self) -> None:
        install_md = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
        block = _extract_way3_bash_block(install_md)

        # Extract the exact local verification + unpack + .source-sha256 commands from INSTALL.md
        # (everything except the network curl downloads, comments, and final bootstrap invocation).
        local_cmds: list[str] = []
        for raw in block.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("curl "):
                continue
            if "bash scripts/bootstrap.sh" in line:
                continue
            local_cmds.append(line)
        self.assertGreaterEqual(len(local_cmds), 5, local_cmds)

        with tempfile.TemporaryDirectory(prefix="v09-install-way3-") as td:
            base = Path(td)
            home = base / "home"
            home.mkdir()
            workdir = base / "downloads"
            workdir.mkdir()

            # Build a realistic local nyairo-v0.8.0.zip with real verify_manifest.py & valid MANIFEST.json
            staging = base / "pkg"
            (staging / "scripts").mkdir(parents=True)
            rel_json = staging / "scripts" / "release.json"
            rel_json.write_text('{"version": "v0.8.0"}\n', encoding="utf-8")
            verify_src = ROOT / "scripts" / "verify_manifest.py"
            verify_dst = staging / "scripts" / "verify_manifest.py"
            verify_dst.write_bytes(verify_src.read_bytes())

            files_map = {
                "scripts/release.json": hashlib.sha256(rel_json.read_bytes()).hexdigest(),
                "scripts/verify_manifest.py": hashlib.sha256(verify_dst.read_bytes()).hexdigest(),
            }
            tree_lines = "".join(f"{digest}  {name}\n" for name, digest in sorted(files_map.items()))
            manifest = {
                "version": "0.8.0",
                "status": "SOURCE_REVIEWED_TARGETED_TESTS_PASSED",
                "files": files_map,
                "file_count": len(files_map),
                "tree_sha256": hashlib.sha256(tree_lines.encode("utf-8")).hexdigest(),
            }
            (staging / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            zip_path = workdir / "nyairo-v0.8.0.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for file_path in sorted(staging.rglob("*")):
                    if file_path.is_file():
                        zf.write(file_path, arcname=file_path.relative_to(staging).as_posix())

            archive_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
            # Use UPPERCASE hex in .sha256 to prove awk tolower($1) normalizes cleanly.
            (workdir / "nyairo-v0.8.0.zip.sha256").write_text(
                f"{archive_sha.upper()}  nyairo-v0.8.0.zip\n", encoding="utf-8"
            )

            # Execute INSTALL.md `## 方式三` steps 2 & 3 verbatim.
            env = dict(os.environ, HOME=str(home), PATH="/usr/bin:/bin")
            step_res = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", "\n".join(local_cmds)],
                cwd=str(workdir),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(step_res.returncode, 0, step_res.stdout + step_res.stderr)
            self.assertIn('"valid": true', step_res.stdout)

            release_dir = home / ".local" / "share" / "nyairo" / "releases" / "v0.8.0"
            state_file = home / ".local" / "share" / "nyairo" / "v0.8.0.source-sha256"
            self.assertTrue(release_dir.is_dir())
            self.assertTrue(state_file.is_file())
            self.assertEqual(state_file.read_text(encoding="utf-8").strip(), archive_sha)

            # Stub `curl` (serves .sha256, fails hard with exit 99 if .zip is ever fetched!) and stub `uv`.
            stub_bin = base / "stub_bin"
            stub_bin.mkdir()
            curl_log = base / "curl.log"
            sync_marker = base / "sync_reached.txt"

            curl_stub = stub_bin / "curl"
            curl_stub.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'out=""\n'
                'url=""\n'
                'while (($#)); do\n'
                '  case "$1" in\n'
                '    -o) out="$2"; shift 2;;\n'
                '    --retry|--connect-timeout) shift 2;;\n'
                '    -*) shift;;\n'
                '    *) url="$1"; shift;;\n'
                '  esac\n'
                'done\n'
                f'printf "%s\\n" "$url" >> "{curl_log}"\n'
                'if [[ "$url" == *.zip.sha256 ]]; then\n'
                f'  printf "{archive_sha}  nyairo-v0.8.0.zip\\n" > "$out"\n'
                '  exit 0\n'
                'fi\n'
                'printf "UNEXPECTED_ZIP_REDOWNLOAD: %s\\n" "$url" >&2\n'
                'exit 99\n',
                encoding="utf-8",
            )
            curl_stub.chmod(0o700)

            uv_stub = stub_bin / "uv"
            uv_stub.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'if [[ "${1:-}" == "python" && "${2:-}" == "install" ]]; then\n'
                "  exit 0\n"
                "fi\n"
                'if [[ "${1:-}" == "python" && "${2:-}" == "find" ]]; then\n'
                f'  printf "%s\\n" "{sys.executable}"\n'
                "  exit 0\n"
                "fi\n"
                'if [[ "${1:-}" == "sync" ]]; then\n'
                f'  printf "SYNC_REACHED\\n" > "{sync_marker}"\n'
                "  exit 42\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            uv_stub.chmod(0o700)

            boot_env = dict(os.environ, HOME=str(home), PATH=f"{stub_bin}:/usr/bin:/bin")
            boot_res = subprocess.run(
                ["bash", str(ROOT / "scripts" / "bootstrap.sh"), "--no-setup", "--no-launch"],
                cwd=str(release_dir),
                env=boot_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(boot_res.returncode, 42, boot_res.stdout + boot_res.stderr)
            self.assertTrue(sync_marker.is_file(), "bootstrap.sh did not pass L118-133 into [3/5] uv sync")
            self.assertIn('"valid": true', boot_res.stdout)
            self.assertIn("[3/5]", boot_res.stdout)

            urls = [u for u in curl_log.read_text(encoding="utf-8").splitlines() if u.strip()]
            self.assertEqual(len(urls), 1, f"expected only .sha256 check, got {urls}")
            self.assertTrue(urls[0].endswith("/nyairo-v0.8.0.zip.sha256"), urls)
            self.assertFalse((home / ".local" / "share" / "nyairo" / ".install-lock").exists())

            # Negative control: removing .source-sha256 MUST trigger the bootstrap.sh:118-122 refusal.
            state_file.unlink()
            sync_marker.unlink()
            neg_res = subprocess.run(
                ["bash", str(ROOT / "scripts" / "bootstrap.sh"), "--no-setup", "--no-launch"],
                cwd=str(release_dir),
                env=boot_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(neg_res.returncode, 0)
            self.assertIn("已存在但没有匹配的安装记录", neg_res.stderr)
            self.assertFalse(sync_marker.exists())


if __name__ == "__main__":
    unittest.main()

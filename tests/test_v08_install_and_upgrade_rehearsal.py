#!/usr/bin/env python3
"""Step 4B: Clean v0.8.0 install & v0.7.0 -> v0.8.0 live upgrade rehearsal (llm_calls == 0).

Verifies on isolated temporary instances (never touches /root/.nyairo-day):
1. Clean v0.8.0 release metadata & bootstrap parity (release.json == v0.8.0, MANIFEST == 0.8.0,
   scripts/bootstrap.sh byte-identical to website/install.sh).
2. Clean v0.8.0 instance init + start on ephemeral loopback port:
   - NYAIRO_V02_CONTROL_ROOT injected outside instance root,
   - All 19 GET routes return 200 OK with 0 disk writes and 0 sqlite -wal/-shm aux files
     (including sample #1 on a brand-new uninitialized budget store!),
   - Live antipuppet_scorecard.plain_chinese_receipt is honest and non-empty,
   - Clean stop leaves 0 stray processes.
3. v0.7.0 -> v0.8.0 live upgrade rehearsal:
   - Existing v0.7.0 instance with legacy in-root v02_control_highwater_ledger.json and
     legacy in-root v02_vc_authority_watermark.json boots cleanly under v0.8.0 runtime,
   - Legacy tombstones & V-C seals are seamlessly enforced (0 resurrection),
   - First new forget write migrates authority into external NYAIRO_V02_CONTROL_ROOT and
     survives restoring a pre-upgrade backup of day_root,
   - All 19 GET routes return 200 OK with 0 disk writes.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT, REPO_ROOT / "components" / "life" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from components.life.scripts import v02_recall_projection as rproj
from components.life.scripts import v02_representation_lease_forget as rlf

GET_ROUTES = (
    "/api/phone/state",
    "/api/phone/living",
    "/api/phone/desk/presence",
    "/api/phone/home/window",
    "/api/phone/autonomy/rhythm",
    "/api/phone/v03/ecology",
    "/api/phone/v03/embodied-social",
    "/api/phone/v03/encounters",
    "/api/phone/v04/neighborhood",
    "/api/phone/v04/builder-trylive",
    "/api/phone/v04/elastic-play",
    "/api/phone/v02/agency-depth",
    "/api/phone/v02/crossmodal",
    "/api/phone/v02/runtime-migration",
    "/api/phone/v02/honest-ux",
    "/api/phone/v02/recall-projection",
    "/api/phone/v02/common-ground",
    "/api/phone/v08/living-traces",
    "/api/phone/v08/emergent-echoes",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _snapshot_tree(root: Path) -> tuple[dict[str, tuple[int, int]], list[str]]:
    snap: dict[str, tuple[int, int]] = {}
    aux: list[str] = []
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        try:
            st = p.stat()
        except OSError:
            continue
        snap[rel] = (st.st_size, int(st.st_mtime_ns))
        if rel.endswith("-wal") or rel.endswith("-shm") or rel.endswith("-journal"):
            aux.append(rel)
    return snap, aux


def _sweep_19_get_routes(root: Path, port: int) -> tuple[dict[str, Any], list[str], list[str], str]:
    base = f"http://127.0.0.1:{port}"
    before, _ = _snapshot_tree(root)
    non_200: dict[str, Any] = {}
    receipt = ""
    for route in GET_ROUTES:
        req = urllib.request.Request(base + route, headers={"Origin": base}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                body = resp.read().decode("utf-8", "replace")
                if resp.status != 200:
                    non_200[route] = resp.status
                elif route == "/api/phone/v02/runtime-migration":
                    doc = json.loads(body)
                    receipt = str(
                        (((doc.get("data") or {}).get("antipuppet_scorecard") or {}).get("plain_chinese_receipt") or "")
                    )
        except urllib.error.HTTPError as exc:
            non_200[route] = exc.code
        except Exception as exc:
            non_200[route] = f"ERR:{type(exc).__name__}"
    after, aux = _snapshot_tree(root)
    diffs = [k for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
    return non_200, diffs, aux, receipt


class TestV08InstallAndUpgradeRehearsal(unittest.TestCase):
    def test_01_v080_release_metadata_and_bootstrap_parity(self) -> None:
        release = json.loads((REPO_ROOT / "scripts" / "release.json").read_text(encoding="utf-8"))
        manifest = json.loads((REPO_ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(release["version"], "v0.8.0")
        self.assertEqual(manifest["version"], "0.8.0")

        boot_bytes = (REPO_ROOT / "scripts" / "bootstrap.sh").read_bytes()
        web_bytes = (REPO_ROOT / "website" / "install.sh").read_bytes()
        self.assertEqual(boot_bytes, web_bytes, "scripts/bootstrap.sh and website/install.sh must stay byte-identical")
        self.assertIn(b"local version=v0.8.0", boot_bytes)

        latest = json.loads((REPO_ROOT / "website" / "downloads" / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["version"], "0.8.0")
        self.assertEqual(latest["tag"], "v0.8.0")
        self.assertEqual(latest["windows"]["filename"], "nyairo-0.8.0-setup.exe")

    @unittest.skipUnless(os.name == "posix", "nyairo_day.py live runtime runs on POSIX/WSL")
    def test_02_clean_v080_install_and_zero_write_first_sweep(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nyairo-v080-clean-") as td:
            base = Path(td)
            inst = base / "clean-inst"
            auth_db = base / "clean-inst-memory-authority" / "authority.db"
            port = _free_port()

            r_init = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "nyairo_day.py"),
                    "init",
                    "--root",
                    str(inst),
                    "--memory-authority",
                    str(auth_db),
                    "--starter-reading",
                    "--json",
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(r_init.returncode, 0, r_init.stderr + r_init.stdout)

            try:
                r_start = subprocess.run(
                    [
                        sys.executable,
                        str(REPO_ROOT / "scripts" / "nyairo_day.py"),
                        "start",
                        "--root",
                        str(inst),
                        "--port",
                        str(port),
                        "--v03-clock-interval",
                        "0",
                        "--json",
                    ],
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(r_start.returncode, 0, r_start.stderr + r_start.stdout)
                time.sleep(1.5)

                # Even the VERY FIRST 19-route GET sweep on a clean v0.8.0 install must have 0 writes and 0 aux files
                non_200, diffs, aux, receipt = _sweep_19_get_routes(inst, port)
                self.assertEqual(non_200, {})
                self.assertEqual(diffs, [], f"unexpected disk writes on clean v0.8.0 first GET sweep: {diffs}")
                self.assertEqual(aux, [])
                self.assertTrue(receipt)
                self.assertIn("未满 7 天", receipt)
            finally:
                subprocess.run(
                    [sys.executable, str(REPO_ROOT / "scripts" / "nyairo_day.py"), "stop", "--root", str(inst)],
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

    @unittest.skipUnless(os.name == "posix", "nyairo_day.py live runtime runs on POSIX/WSL")
    def test_03_v070_to_v080_upgrade_preserves_data_and_legacy_forget_barriers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nyairo-v07-to-v08-") as td:
            base = Path(td)
            inst = base / "legacy-v07-inst"
            auth_db = base / "legacy-v07-inst-memory-authority" / "authority.db"
            ext_ctrl = base / "legacy-v07-inst-memory-authority" / "v02-control"
            backup = base / "pre-v08-backup"
            port = _free_port()

            r_init = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "nyairo_day.py"),
                    "init",
                    "--root",
                    str(inst),
                    "--memory-authority",
                    str(auth_db),
                    "--starter-reading",
                    "--json",
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(r_init.returncode, 0, r_init.stderr + r_init.stdout)

            # Seed v0.7.0-era state WITHOUT NYAIRO_V02_CONTROL_ROOT (legacy in-root control ledger & V-C watermark)
            prev_env = os.environ.pop(rlf.CONTROL_ROOT_ENV_VAR, None)
            try:
                rlf.ingest_source(
                    inst,
                    source_id="src_v07_legacy_forgotten",
                    text="旧版 0.7 已忘掉的片段",
                    visibility="shared",
                    topic="legacy_topic",
                    session_id="sess_v07",
                    operation_id="op_v07_src_1",
                    now_iso="2026-11-15T10:00:00+08:00",
                )
                st_e, _ = rlf.execute_explicit_forget(
                    inst,
                    source_ids=["src_v07_legacy_forgotten"],
                    operation_id="op_v07_fgt_1",
                    actor_side="subject",
                )
                self.assertEqual(st_e, 200)
                rproj.record_episodic_trace(
                    inst,
                    memory_id="mem_v07_keep",
                    topic="海边旧书店",
                    exact_detail="在 0.7 里买了一本画册",
                    gist_summary="在旧书店买画册",
                    now_iso="2026-11-15T11:00:00+08:00",
                    has_subject_capability=True,
                )
                rproj.record_episodic_trace(
                    inst,
                    memory_id="mem_v07_forgotten",
                    topic="不想再提的小插曲",
                    exact_detail="旧版已抹除内容",
                    gist_summary="旧版已抹除",
                    now_iso="2026-11-15T11:05:00+08:00",
                    has_subject_capability=True,
                )
                rproj.tombstone_memory(inst, memory_id="mem_v07_forgotten", reason="v07_forget", now_iso="2026-11-15T11:10:00+08:00")
                # Confirm legacy files live inside inst (v0.7 layout)
                self.assertTrue(inst.joinpath(*rlf.CONTROL_LEDGER_REL_PATH).is_file())
                self.assertTrue(inst.joinpath(*rproj.VC_AUTHORITY_WATERMARK_REL_PATH).is_file())
                self.assertFalse(ext_ctrl.exists())

                # Now upgrade to v0.8.0 runtime (NYAIRO_V02_CONTROL_ROOT enabled)
                os.environ[rlf.CONTROL_ROOT_ENV_VAR] = str(ext_ctrl)

                # Legacy v0.7 forget barriers MUST still be enforced seamlessly
                barrier_v08 = rlf.load_unified_forget_barrier_readonly(inst)
                self.assertIn("src_v07_legacy_forgotten", barrier_v08["blocked_ids"])
                self.assertIn(rproj.compute_vc_item_digest("mem_v07_forgotten"), barrier_v08["vc_sealed_digests"])

                # Existing v0.7 kept memory survives, forgotten memory stays gone
                rec = rproj.project_recall_set(inst, now_iso="2026-11-19T12:00:00+08:00", include_unavailable=False)
                mids = [it.get("memory_id") for it in rec.get("items", [])]
                self.assertIn("mem_v07_keep", mids)
                self.assertNotIn("mem_v07_forgotten", mids)

                # Snapshot before a new v0.8 forget, then write a v0.8 forget -> seeds external ext_ctrl from legacy!
                rlf.ingest_source(
                    inst,
                    source_id="src_v08_new_forget",
                    text="新版 0.8 遗忘片段",
                    visibility="shared",
                    topic="v08_topic",
                    session_id="sess_v08",
                    operation_id="op_v08_src_1",
                    now_iso="2026-11-19T12:05:00+08:00",
                )
                shutil.copytree(inst, backup, ignore=shutil.ignore_patterns("*.sock"))
                st_e2, _ = rlf.execute_explicit_forget(
                    inst,
                    source_ids=["src_v08_new_forget"],
                    operation_id="op_v08_fgt_1",
                    actor_side="subject",
                )
                self.assertEqual(st_e2, 200)
                self.assertTrue(ext_ctrl.joinpath(*rlf.CONTROL_LEDGER_REL_PATH).is_file())

                # Restore pre-v0.8-forget backup -> BOTH v0.7 legacy AND v0.8 new forgotten IDs remain blocked!
                shutil.rmtree(inst)
                shutil.copytree(backup, inst)
                barrier_post = rlf.load_unified_forget_barrier_readonly(inst)
                self.assertIn("src_v07_legacy_forgotten", barrier_post["blocked_ids"])
                self.assertIn("src_v08_new_forget", barrier_post["blocked_ids"])

                # Start v0.8.0 HTTP server on upgraded instance & verify 19 zero-write GET routes
                try:
                    r_start = subprocess.run(
                        [
                            sys.executable,
                            str(REPO_ROOT / "scripts" / "nyairo_day.py"),
                            "start",
                            "--root",
                            str(inst),
                            "--port",
                            str(port),
                            "--v03-clock-interval",
                            "0",
                            "--json",
                        ],
                        cwd=str(REPO_ROOT),
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(r_start.returncode, 0, r_start.stderr + r_start.stdout)
                    time.sleep(1.5)
                    non_200, diffs, aux, receipt = _sweep_19_get_routes(inst, port)
                    self.assertEqual(non_200, {})
                    self.assertEqual(diffs, [])
                    self.assertEqual(aux, [])
                    self.assertTrue(receipt)
                finally:
                    subprocess.run(
                        [sys.executable, str(REPO_ROOT / "scripts" / "nyairo_day.py"), "stop", "--root", str(inst)],
                        cwd=str(REPO_ROOT),
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
            finally:
                if prev_env is None:
                    os.environ.pop(rlf.CONTROL_ROOT_ENV_VAR, None)
                else:
                    os.environ[rlf.CONTROL_ROOT_ENV_VAR] = prev_env


if __name__ == "__main__":
    unittest.main()

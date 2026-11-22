#!/usr/bin/env python3
"""Regression tests for 0.2 Step 1 & Step 2 live wiring closure.

Covers:
1. EventFeedPoller mode sync with phone/v02_runtime_migration.json and wake_router counter persistence.
2. External NYAIRO_V02_CONTROL_ROOT rollback-domain isolation across day_root backup restore.
3. Zero-write real antipuppet_scorecard.plain_chinese_receipt & sixth_dim projection.
4. phone_tool.py invariants (37 / 1199 / 1800) + newly unlocked 0.2 subject closure actions over PhoneHTTPServer.
5. Completed chat turns auto-ingested into RLF so partner forget-hold succeeds without polluting held_threads.
6. Daily loop settlement auto-feeding episodic recall & autobiography episodes with private-slice protection.
7. Workbench project auto-binding to workspace/works/<id>.md without overwriting externally DIVERGED files.
8. analyze_image_palette preserving optional work_id -> linked_work_id.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _p in (str(REPO_ROOT), str(LIFE_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from chiyo_bundle import lived_context_bridge, phone_tool
from components.life.scripts import autonomous_life_events as ale
from components.life.scripts import v02_autobiography_skill_provenance as asp
from components.life.scripts import v02_recall_projection as rproj
from components.life.scripts import v02_representation_lease_forget as rlf
from components.life.scripts import v02_runtime_governance_migration as rtmig
from components.life.scripts import v02_v03_daily_loop_bridge as bridge
from components.phone import capability as phone_cap
from components.phone import server as phone_server
from components.phone import service as phone_service
from components.phone import v02_runtime_migration_view as rtmig_view


def _load_chiyo_guidance_len() -> int:
    plugin_init = REPO_ROOT / "plugins" / "chiyo" / "__init__.py"
    spec = importlib.util.spec_from_file_location("_nyairo_chiyo_plugin_check", str(plugin_init))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return len(getattr(mod, "_PHONE_AND_WORLD_GUIDANCE"))


def _make_tiny_png_b64(rgb: tuple[int, int, int] = (72, 128, 176)) -> str:
    """Create a valid 4x4 RGB PNG purely with stdlib zlib (zero external deps)."""
    w, h = 4, 4
    raw = bytearray()
    r, g, b = rgb
    for _ in range(h):
        raw.append(0)
        for _ in range(w):
            raw.extend((r, g, b))

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw))) + _chunk(b"IEND", b"")
    return base64.b64encode(png).decode("ascii")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        st = p.stat()
        out[rel] = (st.st_size, int(st.st_mtime_ns))
    return out


class _EmptySharing:
    def listing(self, **_kw: Any) -> dict[str, Any]:
        return {"ok": True, "status": phone_service.STATUS_OK, "data": {"shares": [], "truncated": False}}


class _LivePhoneHarness:
    def __init__(self, day_root: Path) -> None:
        self.day_root = day_root
        self.csrf_token = "step12-test-csrf"
        self.cap_token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        inst = {
            "root": str(day_root),
            "subject": "subject_1",
            "world_home": str(day_root / "world"),
            "supply_socket": str(day_root / "run" / "supply.sock"),
            "private_items_artifact_id": "art:priv",
            "artifact_grant_id": "grant:step12",
            "workspace_id": "ws:step12",
        }
        self.service = phone_service.PhoneService(inst, environment={}, sharing=_EmptySharing())
        self.cap = phone_cap.Capability(
            token=self.cap_token,
            subject="subject_1",
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-19T10:00:00+08:00",
        )
        self.server = phone_server.create_server(
            self.service,
            csrf_token=self.csrf_token,
            port=0,
            capability=self.cap,
        )
        self.port = self.server.server_port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def get(self, path: str, *, as_subject: bool = False) -> tuple[int, dict[str, Any]]:
        headers = {"Origin": self.base_url}
        if as_subject:
            headers[phone_cap.HEADER_NAME] = self.cap_token
        req = urllib.request.Request(self.base_url + path, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def post(self, path: str, body: dict[str, Any], *, as_subject: bool = False) -> tuple[int, dict[str, Any]]:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
            "Origin": self.base_url,
            "X-CSRF-Token": self.csrf_token,
        }
        if as_subject:
            headers[phone_cap.HEADER_NAME] = self.cap_token
        req = urllib.request.Request(self.base_url + path, data=raw, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


class TestV02Step12LiveWiring(unittest.TestCase):
    def test_01_event_poller_reads_phone_budget_mode_and_persists_wake_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Without an existing v02_runtime_migration.json, poller must not fabricate one
            poller_empty = ale.EventFeedPoller([], day_root=root)
            poller_empty.coalesce_events(
                [{"source": "clock", "kind": "tick", "summary": "morning"}],
                now_iso="2026-11-19T09:00:00+08:00",
            )
            self.assertFalse((root / "phone" / "v02_runtime_migration.json").exists())

            # Create real runtime migration state in LOW_BUDGET_CONSERVATION
            doc_eco, cmd_eco = rtmig.apply_runtime_migration_mutation(
                root,
                {
                    "action": "set_budget_mode",
                    "mode": "LOW_BUDGET_CONSERVATION",
                    "operation_key": "op_step12_eco",
                },
                actor_side="partner",
                now_iso="2026-11-19T09:05:00+08:00",
            )
            self.assertEqual(cmd_eco, "APPLIED", doc_eco)

            poller = ale.EventFeedPoller([], day_root=root)
            out = poller.coalesce_events(
                [
                    {"source": "world", "kind": "weather_shift", "summary": "海风微凉"},
                    {"source": "world", "kind": "weather_shift", "summary": "海风微凉（重复播报）"},
                    {"source": "world", "kind": "weather_shift", "summary": "海风微凉（第三次）"},
                ],
                now_iso="2026-11-19T09:06:00+08:00",
            )
            self.assertGreaterEqual(len(out), 1)
            self.assertEqual(poller.coalescer.state["mode_config"]["mode"], "LOW_BUDGET")

            state_doc = json.loads((root / "phone" / "v02_runtime_migration.json").read_text(encoding="utf-8"))
            wr = state_doc.get("wake_router") or {}
            self.assertGreaterEqual(int(wr.get("effective_wakes_total", 0)), 1)
            self.assertGreaterEqual(int(wr.get("coalesced_saved_wakes", 0)), 1)

            # Switching to DORMANT_NO_COGNITION must sync poller mode to DORMANT
            doc_slp, cmd_slp = rtmig.apply_runtime_migration_mutation(
                root,
                {
                    "action": "set_budget_mode",
                    "mode": "DORMANT_NO_COGNITION",
                    "operation_key": "op_step12_sleep",
                },
                actor_side="partner",
                now_iso="2026-11-19T09:10:00+08:00",
            )
            self.assertEqual(cmd_slp, "APPLIED", doc_slp)
            poller.coalesce_events(
                [{"source": "world", "kind": "ambient", "summary": "quiet"}],
                now_iso="2026-11-19T09:11:00+08:00",
            )
            self.assertEqual(poller.coalescer.state["mode_config"]["mode"], "DORMANT")

    def test_02_external_v02_control_root_survives_day_root_restore(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            day_root = base / "day-instance"
            backup_root = base / "day-backup"
            ext_control = base / "external-authority" / "v02-control"
            day_root.mkdir(parents=True)

            prev_env = os.environ.get(rlf.CONTROL_ROOT_ENV_VAR)
            os.environ[rlf.CONTROL_ROOT_ENV_VAR] = str(ext_control)
            try:
                rlf.ingest_source(
                    day_root,
                    source_id="src_keep_vs_forget",
                    text="保密片段内容",
                    visibility="shared",
                    topic="topic_ext",
                    session_id="sess_ext",
                    operation_id="op_src_ext_1",
                    now_iso="2026-11-19T08:00:00+08:00",
                )
                rproj.record_episodic_trace(
                    day_root,
                    memory_id="mem_ext_1",
                    topic="海边散步",
                    exact_detail="在旧灯塔下捡到一块海玻璃",
                    gist_summary="在旧灯塔下散步",
                    now_iso="2026-11-19T08:05:00+08:00",
                    has_subject_capability=True,
                )

                # Take a pre-forget backup snapshot of day_root
                shutil.copytree(day_root, backup_root)

                # Execute forget tombstone + V-C revocation seal under external control root
                st_h, hold_doc = rlf.submit_forget_command(
                    day_root,
                    command_text="忘掉这段对话",
                    operation_id="op_fgt_ext_1",
                    recent_source_ids=["src_keep_vs_forget"],
                    actor_side="partner",
                    shared_with_partner=True,
                )
                self.assertEqual(st_h, 200, hold_doc)
                st_c, fin_doc = rlf.execute_explicit_forget(
                    day_root,
                    source_ids=["src_keep_vs_forget"],
                    operation_id="op_fgt_ext_exec",
                    actor_side="subject",
                )
                self.assertEqual(st_c, 200, fin_doc)
                rproj.tombstone_memory(day_root, memory_id="mem_ext_1", reason="user_forget", now_iso="2026-11-19T08:20:00+08:00")

                # Control ledger & V-C watermark must exist in ext_control, NOT in day_root
                self.assertTrue(ext_control.joinpath(*rlf.CONTROL_LEDGER_REL_PATH).exists())
                self.assertTrue(ext_control.joinpath(*rproj.VC_AUTHORITY_WATERMARK_REL_PATH).exists())
                self.assertFalse(day_root.joinpath(*rlf.CONTROL_LEDGER_REL_PATH).exists())
                self.assertFalse(day_root.joinpath(*rproj.VC_AUTHORITY_WATERMARK_REL_PATH).exists())

                # Roll back day_root completely to the pre-forget backup!
                shutil.rmtree(day_root)
                shutil.copytree(backup_root, day_root)

                # Unified barrier & recall projection MUST still block both restored items
                barrier = rlf.load_unified_forget_barrier_readonly(day_root)
                self.assertIn("src_keep_vs_forget", barrier["blocked_ids"])
                self.assertIn(rproj.compute_vc_item_digest("mem_ext_1"), barrier["vc_sealed_digests"])

                recall_set = rproj.project_recall_set(day_root, now_iso="2026-11-19T08:30:00+08:00", include_unavailable=False)
                visible_mids = [item.get("memory_id") for item in recall_set.get("items", [])]
                self.assertNotIn("mem_ext_1", visible_mids)

                # Runtime migration view must see watermark_exists=True from external control root
                st_v, view_doc = rtmig_view.read_v02_runtime_migration_view(day_root, has_subject_capability=False)
                self.assertEqual(st_v, 200)
                vc_auth = (view_doc.get("data") or {}).get("vc_authority_status") or {}
                self.assertTrue(vc_auth.get("watermark_exists"), vc_auth)
            finally:
                if prev_env is None:
                    os.environ.pop(rlf.CONTROL_ROOT_ENV_VAR, None)
                else:
                    os.environ[rlf.CONTROL_ROOT_ENV_VAR] = prev_env

    def test_03_runtime_migration_scorecard_real_plain_chinese_receipt_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            before = _snapshot_tree(root)
            st, doc = rtmig_view.read_v02_runtime_migration_view(root, has_subject_capability=False)
            after = _snapshot_tree(root)
            self.assertEqual(st, 200)
            self.assertEqual(before, after, "GET runtime-migration must remain 100% zero-write")

            sc = (doc.get("data") or {}).get("antipuppet_scorecard") or {}
            self.assertIn("sixth_dim", sc)
            receipt = str(sc.get("plain_chinese_receipt") or "")
            self.assertTrue(receipt)
            self.assertIn("未满 7 天", receipt, f"day-1 root should honestly say < 7 days: {receipt}")

            # Trigger a real violation (7 days observed with 0 quiet days -> S05-3 non-LOW risk)
            (root / "phone").mkdir(parents=True, exist_ok=True)
            (root / "phone" / "v03_clock_state.json").write_text(
                json.dumps({"days_observed": 7, "rolling_7d_quiet_days": 0}, ensure_ascii=False),
                encoding="utf-8",
            )
            st2, doc2 = rtmig_view.read_v02_runtime_migration_view(root, has_subject_capability=False)
            self.assertEqual(st2, 200)
            sc2 = (doc2.get("data") or {}).get("antipuppet_scorecard") or {}
            self.assertNotEqual(sc2["sixth_dim"].get("risk"), "LOW", sc2["sixth_dim"])
            receipt2 = str(sc2.get("plain_chinese_receipt") or "")
            self.assertTrue(receipt2)
            self.assertNotIn("未满 7 天", receipt2)
            self.assertNotEqual(receipt, receipt2)

    def test_04_phone_tool_invariants_and_newly_unlocked_v02_subject_actions(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(_load_chiyo_guidance_len(), 1199)
        self.assertEqual(lived_context_bridge.MAX_LIVED_CONTEXT_CHARS, 1800)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            harness = _LivePhoneHarness(root)
            try:
                cap_dir = root / "cap_priv"
                cap_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.chmod(cap_dir, 0o700)
                cap_path = cap_dir / "phone-capability.json"
                cap_path.write_text(
                    json.dumps(
                        {
                            "schema_version": phone_cap.SCHEMA_CAPABILITY,
                            "kind": phone_cap.KIND_CAPABILITY,
                            "token": harness.cap_token,
                            "subject": "subject_1",
                            "endpoint": harness.base_url,
                            "issued_at": "2026-11-19T10:00:00+08:00",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                os.chmod(cap_path, 0o600)
                profile_dir = root / "hermes_profile"
                (profile_dir / "chiyo").mkdir(parents=True, exist_ok=True)
                (profile_dir / "chiyo" / "config.json").write_text(
                    json.dumps(
                        {
                            "cli_owner": True,
                            "local_owner_uid": os.getuid(),
                            "phone": {
                                "enabled": True,
                                "write_enabled": True,
                                "private_artifacts": True,
                                "base_url": harness.base_url,
                                "profile_home": str(root),
                                "capability_file": str(cap_path),
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                calls = [
                    (
                        "v02_agency_depth",
                        {
                            "action": "open_bounded_inquiry",
                            "question": "海风潮湿时松木纸为什么容易微卷？",
                            "topic_tag": "paper_craft",
                            "candidate_explanations": [
                                {"summary_cn": "空气湿度高", "stance": "plausible"},
                                {"summary_cn": "纸张纤维方向", "stance": "plausible"},
                            ],
                            "next_evidence_needed": "明天晴天对比同一批纸",
                            "step_budget": 3,
                            "shared": True,
                            "operation_key": "op_pt_inq_1",
                        },
                    ),
                    (
                        "v02_crossmodal",
                        {
                            "action": "curate_unshown_box_item",
                            "box_item_id": "box_sketch_01",
                            "artifact_id": "art_box_01",
                            "version_id": "v1",
                            "title_cn": "还没想好要不要摆出来的雨巷草图",
                            "curation_state": "kept",
                            "note_cn": "先收在盒子里慢慢看",
                            "operation_key": "op_pt_box_1",
                        },
                    ),
                    (
                        "v02_common_ground",
                        {
                            "action": "record_dual_rationale_decision",
                            "decision_id": "dec_lamp_01",
                            "topic_cn": "书桌台灯换暖黄灯泡",
                            "scope_cn": "room_lighting",
                            "adopted_choice_cn": "换 2700K 暖光灯泡",
                            "subject_rationale_cn": "晚上画草图眼睛更舒服",
                            "partner_rationale_cn": "推门进来感觉更柔和",
                            "operation_key": "op_pt_dec_1",
                        },
                    ),
                    (
                        "v02_common_ground",
                        {
                            "action": "record_missed_moment",
                            "moment_id": "mm_rain_01",
                            "summary_cn": "午后下了一阵急雨，错过了窗边那道短虹",
                            "operation_key": "op_pt_mm_1",
                        },
                    ),
                    (
                        "v02_runtime_migration",
                        {
                            "action": "claim_device_presence_lease",
                            "device_id": "desk-main-01",
                            "continuation_ref": "cont_morning_01",
                            "lease_seconds": 180,
                            "operation_key": "op_pt_lease_1",
                        },
                    ),
                ]
                for surface, payload in calls:
                    raw_out = phone_tool.phone_handler(
                        {
                            "action": "phone_life_update",
                            "target_surface": surface,
                            "payload": payload,
                        },
                        hermes_home=str(profile_dir),
                    )
                    parsed = json.loads(raw_out) if isinstance(raw_out, str) else raw_out
                    self.assertTrue(
                        parsed.get("ok") is True,
                        f"phone_life_update({surface}, {payload['action']}) failed: {parsed}",
                    )
            finally:
                harness.close()

    def test_05_completed_turn_auto_ingests_rlf_and_unblocks_partner_forget_hold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            harness = _LivePhoneHarness(root)
            try:
                # 1. Fresh root: partner forget-hold returns 409 FORGET_NO_ELIGIBLE_CANDIDATES
                c0, d0 = harness.post(
                    "/api/phone/v02/honest-ux",
                    {
                        "action": "request_forget_scope_hold",
                        "scope_hint": "recent_turn",
                        "target_domain": "conversation",
                        "actor_side": "partner",
                        "operation_key": "op_fgt_empty_01",
                    },
                    as_subject=False,
                )
                self.assertEqual(c0, 409, d0)
                self.assertEqual(d0.get("reason_code"), "FORGET_NO_ELIGIBLE_CANDIDATES")

                # 2. Desktop records a normal completed turn (hold_thread=False, partner CSRF only)
                c1, d1 = harness.post(
                    "/api/phone/autonomy/rhythm",
                    {
                        "action": "record_turn_context",
                        "topic_label": "傍晚沿着旧海堤散步吹风",
                        "reason": "COMPLETED_TURN",
                        "owner": "subject",
                        "hold_thread": False,
                        "session_id": "sess_desktop_main",
                        "turn_id": "turn_step12_01",
                    },
                    as_subject=False,
                )
                self.assertEqual(c1, 200, d1)
                self.assertTrue(d1.get("ok"), d1)

                # Verify held_threads was NOT polluted by normal completed turns
                cg, dg = harness.get("/api/phone/autonomy/rhythm", as_subject=False)
                self.assertEqual(cg, 200, dg)
                threads = ((dg.get("data") or {}).get("held_threads") or [])
                self.assertEqual(len(threads), 0, f"COMPLETED_TURN must not pollute held_threads: {threads}")

                # 3. Now partner forget-hold MUST succeed with 200 OK
                c2, d2 = harness.post(
                    "/api/phone/v02/honest-ux",
                    {
                        "action": "request_forget_scope_hold",
                        "scope_hint": "recent_turn",
                        "target_domain": "conversation",
                        "actor_side": "partner",
                        "operation_key": "op_fgt_after_turn_01",
                    },
                    as_subject=False,
                )
                self.assertEqual(c2, 200, d2)
                self.assertTrue(d2.get("ok"), d2)
                hdata = d2.get("data") or {}
                self.assertTrue(hdata.get("case_id") or hdata.get("hold_id"), d2)
            finally:
                harness.close()

    def test_06_daily_loop_settlement_feeds_recall_and_autobiography_safely(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # 1. Shared round (MEANINGLESS_IDLE)
            settled_pub = bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "actor_side": "subject",
                    "effect_state": "APPLIED",
                    "action_kind": "MEANINGLESS_IDLE",
                    "idle_kind": "daydream_window",
                    "quiet_note": "看云影慢慢移过窗台",
                    "receipt_ref": "rcpt:step12_idle_1",
                    "operation_key": "op_step12_idle_1",
                },
                now_iso="2026-11-19T14:20:00+08:00",
            )
            self.assertTrue(settled_pub.get("ok"), settled_pub)
            self.assertTrue(settled_pub.get("settled"), settled_pub)
            self.assertNotIn("recall_projection", settled_pub.get("slices_updated") or [])
            self.assertNotIn("autobiography", settled_pub.get("slices_updated") or [])

            rec_pub = rproj.project_recall_set(
                root,
                now_iso="2026-11-19T14:25:00+08:00",
                has_subject_capability=False,
                include_unavailable=False,
            )
            self.assertTrue(
                any(it.get("memory_id") == "ep_settled_op_step12_idle_1" for it in rec_pub.get("items", [])),
                rec_pub,
            )

            # 2. Private round (visibility='private') must NOT leak into partner recall or partner autobiography
            settled_priv = bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "actor_side": "subject",
                    "effect_state": "APPLIED",
                    "action_kind": "MEANINGLESS_IDLE",
                    "idle_kind": "daydream_window",
                    "quiet_note": "LEAK_PRIVATE_ROUND_SECRET",
                    "visibility": "private",
                    "receipt_ref": "rcpt:step12_idle_priv",
                    "operation_key": "op_step12_idle_priv",
                },
                now_iso="2026-11-19T14:30:00+08:00",
            )
            self.assertTrue(settled_priv.get("ok"), settled_priv)
            self.assertTrue(settled_priv.get("settled"), settled_priv)

            rec_partner = rproj.project_recall_set(
                root,
                now_iso="2026-11-19T14:35:00+08:00",
                has_subject_capability=False,
                include_unavailable=False,
            )
            self.assertFalse(
                any(it.get("memory_id") == "ep_settled_op_step12_idle_priv" for it in rec_partner.get("items", [])),
                rec_partner,
            )
            rec_subject = rproj.project_recall_set(
                root,
                now_iso="2026-11-19T14:35:00+08:00",
                has_subject_capability=True,
                include_unavailable=False,
            )
            self.assertTrue(
                any(it.get("memory_id") == "ep_settled_op_step12_idle_priv" for it in rec_subject.get("items", [])),
                rec_subject,
            )

            p12_partner = asp.project_p12_autobiographical_periods(
                root,
                has_subject_capability=False,
                now_iso="2026-11-19T14:35:00+08:00",
            )
            self.assertNotIn("LEAK_PRIVATE_ROUND_SECRET", json.dumps(p12_partner, ensure_ascii=False))

    def test_07_workbench_md_representation_binding_and_diverged_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inst = {
                "root": str(root),
                "subject": "subject_1",
                "world_home": str(root / "world"),
                "supply_socket": str(root / "run" / "supply.sock"),
                "private_items_artifact_id": "art:priv",
                "artifact_grant_id": "grant:step12",
                "workspace_id": "ws:step12",
            }
            svc = phone_service.PhoneService(inst, environment={}, sharing=_EmptySharing())
            svc._sync_project_to_rlf_representation(
                artifact_id="work:proj_coastal_sketch",
                title="海堤风物草稿",
                content="# 海堤风物草稿\n第一版笔记",
                commit=False,
            )
            md_path = root / "workspace" / "works" / "work_proj_coastal_sketch.md"
            self.assertTrue(md_path.exists())
            self.assertIn("第一版笔记", md_path.read_text(encoding="utf-8"))

            st_cur, rep_cur = rlf.inspect_representation(
                root,
                artifact_id="work:proj_coastal_sketch",
                has_subject_capability=True,
            )
            self.assertEqual(st_cur, 200, rep_cur)
            self.assertEqual(rep_cur.get("representation_health"), rlf.REP_CURRENT)

            # External editor modifies the .md file directly
            md_path.write_text("# 外部手动改写\n不要覆盖我的草稿", encoding="utf-8")

            # Subsequent internal project commit MUST NOT overwrite the externally diverged .md file
            svc._sync_project_to_rlf_representation(
                artifact_id="work:proj_coastal_sketch",
                title="海堤风物草稿",
                content="# 海堤风物草稿\n第二版自动提交",
                commit=True,
            )
            self.assertIn("不要覆盖我的草稿", md_path.read_text(encoding="utf-8"))
            st_div, rep_div = rlf.inspect_representation(
                root,
                artifact_id="work:proj_coastal_sketch",
                has_subject_capability=True,
            )
            self.assertEqual(st_div, 200, rep_div)
            self.assertEqual(rep_div.get("representation_health"), rlf.REP_DIVERGED)

    def test_08_analyze_image_palette_preserves_linked_work_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            harness = _LivePhoneHarness(root)
            try:
                c, d = harness.post(
                    "/api/phone/v02/crossmodal",
                    {
                        "action": "analyze_image_palette",
                        "title": "港湾薄雾试色",
                        "image_base64": _make_tiny_png_b64((64, 112, 152)),
                        "share_with_room": True,
                        "work_id": "work_coastal_01",
                        "operation_key": "op_pal_work_01",
                    },
                    as_subject=False,
                )
                self.assertEqual(c, 200, d)
                self.assertTrue(d.get("ok"), d)

                cg, dg = harness.get("/api/phone/v02/crossmodal", as_subject=False)
                self.assertEqual(cg, 200, dg)
                studies = (dg.get("data") or {}).get("palette_studies") or []
                self.assertGreaterEqual(len(studies), 1)
                self.assertEqual(studies[0].get("linked_work_id"), "work_coastal_01")
            finally:
                harness.close()


if __name__ == "__main__":
    unittest.main()

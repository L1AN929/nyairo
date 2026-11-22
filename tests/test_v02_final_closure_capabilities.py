#!/usr/bin/env python3
"""Closure tests for v0.2 Bucket-3 capabilities:
F-025 Audio Notes, F-148 Two-Step Dignified Retirement, F-113 Opt-In Proactive Outbound Gate,
F-076 Self-Claim, F-011/012 External Edit / Canonical Rebuild, F-168/171/173 Zero-Write Telemetry,
and F-042 S03 perception_ref + life_situation_view live S03 owner check.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT, REPO_ROOT / "components" / "life" / "scripts", REPO_ROOT / "components" / "phone"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from components.life.scripts import life_request_budget as lrb
from components.life.scripts import life_situation_view as lsv
from components.life.scripts import s03_claims as s03
from components.life.scripts import v02_autobiography_skill_provenance as asp_engine
from components.life.scripts import v02_representation_lease_forget as rep_lease
from components.life.scripts import v02_runtime_governance_migration as rm_engine
from components.phone import v02_crossmodal_view as cm_view
from components.phone import v02_runtime_migration_view as rm_view


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _aux_sqlite_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return [
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.name.endswith(("-wal", "-shm", "-journal"))
    ]


class V02FinalClosureCapabilitiesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v02-final-closure-"))
        self.day_root = self.tmp / "day"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. F-025 Audio / Voice-Note Capture
    # ------------------------------------------------------------------

    def test_01_f025_audio_notes_privacy_honest_asr_validation_and_cap(self) -> None:
        t0 = "2026-11-19T10:00:00+08:00"
        before = _snapshot_tree(self.tmp)
        c0, v0 = cm_view.read_v02_crossmodal_view(
            self.day_root, has_subject_capability=False, now_iso=t0
        )
        after = _snapshot_tree(self.tmp)
        self.assertEqual(c0, 200)
        self.assertEqual(before, after)
        self.assertEqual(v0["audio_notes"], [])
        self.assertEqual(v0["audio_note_count"], 0)
        self.assertEqual(v0["data"]["audio_notes"], [])
        self.assertEqual(v0["data"]["audio_note_count"], 0)
        self.assertEqual(v0["llm_calls"], 0)

        # Forged actor_side in payload MUST be rejected (400 INVALID_FIELDS)
        c_forge, e_forge = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "ingest_audio_note",
                "title_cn": "伪造测试",
                "duration_seconds": 12.5,
                "audio_format": "wav",
                "source_kind": "mic_capture",
                "actor_side": "subject",
            },
            actor_side="partner",
            now_iso=t0,
        )
        self.assertEqual(c_forge, 400)
        self.assertEqual(e_forge["reason_code"], "INVALID_FIELDS")

        # Partner audio note without transcript -> UNAVAILABLE_LOCAL_ASR (never fabricated!)
        c_p, r_p = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "ingest_audio_note",
                "title_cn": "窗外雨声便笺",
                "duration_seconds": 18.5,
                "audio_format": "wav",
                "source_kind": "ambient_memo",
                "ambient_tag_cn": "细雨",
                "operation_key": "op_aud_p1",
            },
            actor_side="partner",
            now_iso=t0,
        )
        self.assertEqual(c_p, 200)
        rcpt_p = r_p["action_receipt"]
        self.assertTrue(rcpt_p["audio_note_id"].startswith("aud_"))
        self.assertEqual(rcpt_p["title_cn"], "窗外雨声便笺")
        self.assertEqual(rcpt_p["duration_seconds"], 18.5)
        self.assertEqual(rcpt_p["audio_format"], "wav")
        self.assertEqual(rcpt_p["transcript_status"], "UNAVAILABLE_LOCAL_ASR")
        self.assertEqual(len(rcpt_p["sha256_hex"]), 64)
        self.assertEqual(rcpt_p["author_side"], "partner")
        self.assertTrue(rcpt_p["shared"])

        # Idempotent replay returns DUPLICATE with identical receipt
        c_dup, r_dup = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "ingest_audio_note",
                "title_cn": "窗外雨声便笺",
                "duration_seconds": 18.5,
                "audio_format": "wav",
                "source_kind": "ambient_memo",
                "ambient_tag_cn": "细雨",
                "operation_key": "op_aud_p1",
            },
            actor_side="partner",
            now_iso=t0,
        )
        self.assertEqual(c_dup, 200)
        self.assertEqual(r_dup["command_status"], "DUPLICATE")
        self.assertEqual(r_dup["action_receipt"]["audio_note_id"], rcpt_p["audio_note_id"])

        # Subject private audio note WITH user-supplied transcript -> USER_SUPPLIED, hidden from partner!
        sha_explicit = "a" * 64
        c_s, r_s = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "ingest_audio_note",
                "title_cn": "私下哼的一小段旋律",
                "duration_seconds": 9.0,
                "audio_format": "voice_memo",
                "source_kind": "mic_capture",
                "sha256_hex": sha_explicit,
                "transcript_cn": "记录一下刚想到的副歌节奏",
                "shared": False,
                "operation_key": "op_aud_s1",
            },
            actor_side="subject",
            now_iso="2026-11-19T10:05:00+08:00",
        )
        self.assertEqual(c_s, 200)
        self.assertEqual(r_s["action_receipt"]["author_side"], "subject")
        self.assertEqual(r_s["action_receipt"]["transcript_status"], "USER_SUPPLIED")
        self.assertEqual(r_s["action_receipt"]["sha256_hex"], sha_explicit)
        self.assertFalse(r_s["action_receipt"]["shared"])

        # Partner GET MUST filter out shared=False note completely
        snap_before_get = _snapshot_tree(self.day_root)
        c_gp, v_gp = cm_view.read_v02_crossmodal_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-19T10:06:00+08:00"
        )
        c_gs, v_gs = cm_view.read_v02_crossmodal_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-19T10:06:00+08:00"
        )
        snap_after_get = _snapshot_tree(self.day_root)
        self.assertEqual(c_gp, 200)
        self.assertEqual(c_gs, 200)
        self.assertEqual(snap_before_get, snap_after_get)
        self.assertEqual(v_gp["audio_note_count"], 1)
        self.assertEqual([n["title_cn"] for n in v_gp["audio_notes"]], ["窗外雨声便笺"])
        self.assertEqual(v_gs["audio_note_count"], 2)

        # Validation failures (duration out of range, bad format, bad sha256)
        for bad_pl in (
            {"action": "ingest_audio_note", "title_cn": "短", "duration_seconds": 0.2, "audio_format": "wav"},
            {"action": "ingest_audio_note", "title_cn": "长", "duration_seconds": 601, "audio_format": "wav"},
            {"action": "ingest_audio_note", "title_cn": "错格式", "duration_seconds": 5, "audio_format": "flac"},
            {"action": "ingest_audio_note", "title_cn": "错哈希", "duration_seconds": 5, "audio_format": "mp3", "sha256_hex": "nothex"},
        ):
            code_bad, _ = cm_view.mutate_v02_crossmodal_view(self.day_root, bad_pl, actor_side="partner")
            self.assertEqual(code_bad, 400, f"Expected 400 for {bad_pl}")

        # Cap at max 32 entries
        for idx in range(34):
            c_i, _ = cm_view.mutate_v02_crossmodal_view(
                self.day_root,
                {
                    "action": "ingest_audio_note",
                    "title_cn": f"便笺_{idx:02d}",
                    "duration_seconds": 2.0,
                    "audio_format": "ogg",
                    "source_kind": "local_file",
                    "shared": True,
                },
                actor_side="partner",
                now_iso=f"2026-11-19T11:{idx:02d}:00+08:00",
            )
            self.assertEqual(c_i, 200)
        _, v_cap = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=True)
        self.assertEqual(v_cap["audio_note_count"], 32)
        self.assertEqual(len(v_cap["audio_notes"]), 32)

    # ------------------------------------------------------------------
    # 2. F-148 Two-Step Dignified Permanent Retirement
    # ------------------------------------------------------------------

    def test_02_f148_two_step_dignified_retirement_and_zero_destructive_wipe(self) -> None:
        # Seed keepsakes, memories, audio notes to prove zero destructive delete
        (self.day_root / "phone").mkdir(parents=True, exist_ok=True)
        keepsake_path = self.day_root / "phone" / "window_keepsakes.json"
        keepsake_doc = {"pins": [{"share_id": "sh_1"}], "keepsakes": [{"id": "ks_1"}]}
        keepsake_path.write_text(json.dumps(keepsake_doc, ensure_ascii=False), encoding="utf-8")
        recall_path = self.day_root / "phone" / "v02_recall_projection.json"
        recall_doc = {"schema": "nyairo.v02.recall_projection.v1", "memories": {"m1": {"text": "一起看海"}}}
        recall_path.write_text(json.dumps(recall_doc, ensure_ascii=False), encoding="utf-8")
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "ingest_audio_note", "title_cn": "留念语音", "duration_seconds": 10, "audio_format": "mp3"},
            actor_side="partner",
            now_iso="2026-11-19T09:00:00+08:00",
        )

        # Default zero-write GET status is ACTIVE
        before_get = _snapshot_tree(self.day_root)
        c_g0, v_g0 = rm_view.read_v02_runtime_migration_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-19T09:05:00+08:00"
        )
        self.assertEqual(c_g0, 200)
        self.assertEqual(before_get, _snapshot_tree(self.day_root))
        self.assertEqual(v_g0["retirement_status"]["status"], "ACTIVE")
        self.assertIsNone(v_g0["retirement_status"]["prepared_at"])
        self.assertIsNone(v_g0["retirement_status"]["sealed_at"])
        self.assertIsNone(v_g0["retirement_status"]["confirmation_phrase"])

        # Confirming before prepare -> 409 RETIREMENT_NOT_PREPARED (zero writes!)
        c_noprep, e_noprep = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "confirm_dignified_retirement", "confirmation_phrase": "确认温和封存归档"},
            actor_side="partner",
            now_iso="2026-11-19T09:06:00+08:00",
        )
        self.assertEqual(c_noprep, 409)
        self.assertEqual(e_noprep["reason_code"], "RETIREMENT_NOT_PREPARED")
        self.assertEqual(before_get, _snapshot_tree(self.day_root))

        # Step 1: Prepare
        c_prep, v_prep = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "prepare_dignified_retirement",
                "reason_cn": "准备长期休整归档",
                "farewell_note_cn": "谢谢这段日子的陪伴",
                "operation_key": "op_ret_prep_1",
            },
            actor_side="partner",
            now_iso="2026-11-19T09:10:00+08:00",
        )
        self.assertEqual(c_prep, 200)
        rs = v_prep["retirement_status"]
        self.assertEqual(rs["status"], "PENDING_CONFIRMATION")
        self.assertEqual(rs["confirmation_phrase"], rm_engine.RETIREMENT_CONFIRMATION_PHRASE_CN)
        self.assertEqual(rs["archive_manifest"]["memories_count"], 1)
        self.assertEqual(rs["archive_manifest"]["keepsakes_count"], 2)
        self.assertGreaterEqual(rs["archive_manifest"]["skills_count"], 5)
        self.assertEqual(rs["archive_manifest"]["audio_notes_count"], 1)
        self.assertFalse(rs["archive_manifest"]["destructive_wipe_performed"])

        # Cancel reverts PENDING_CONFIRMATION -> ACTIVE cleanly
        c_can, v_can = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "cancel_dignified_retirement", "operation_key": "op_ret_can_1"},
            actor_side="partner",
            now_iso="2026-11-19T09:15:00+08:00",
        )
        self.assertEqual(c_can, 200)
        self.assertEqual(v_can["retirement_status"]["status"], "ACTIVE")

        # Prepare again, then test wrong phrase -> 400 CONFIRMATION_PHRASE_MISMATCH
        c_prep2, _ = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "prepare_dignified_retirement",
                "reason_cn": "再次确认归档",
                "farewell_note_cn": "后会有期",
                "operation_key": "op_ret_prep_2",
            },
            actor_side="partner",
            now_iso="2026-11-19T09:20:00+08:00",
        )
        self.assertEqual(c_prep2, 200)

        c_bad, e_bad = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "confirm_dignified_retirement", "confirmation_phrase": "随便写一句"},
            actor_side="partner",
            now_iso="2026-11-19T09:21:00+08:00",
        )
        self.assertEqual(c_bad, 400)
        self.assertEqual(e_bad["reason_code"], "CONFIRMATION_PHRASE_MISMATCH")

        # Step 2: Confirm with exact phrase -> RETIRED_ARCHIVE_SEALED + DORMANT_NO_COGNITION
        c_ok, v_ok = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "confirm_dignified_retirement",
                "confirmation_phrase": rm_engine.RETIREMENT_CONFIRMATION_PHRASE_CN,
                "farewell_note_cn": "所有纪念都安静留着",
                "operation_key": "op_ret_confirm_1",
            },
            actor_side="partner",
            now_iso="2026-11-19T09:25:00+08:00",
        )
        self.assertEqual(c_ok, 200)
        self.assertEqual(v_ok["retirement_status"]["status"], "RETIRED_ARCHIVE_SEALED")
        self.assertEqual(v_ok["retirement_status"]["sealed_at"], "2026-11-19T09:25:00+08:00")
        self.assertEqual(v_ok["retirement_status"]["farewell_note_cn"], "所有纪念都安静留着")
        self.assertEqual(v_ok["runtime_mode"]["health_state"], "DORMANT_NO_COGNITION")
        self.assertEqual(v_ok["runtime_mode"]["mode"], "DORMANT")

        # Keepsakes & memories remain 100% intact on disk (zero destructive wipe!)
        self.assertEqual(json.loads(keepsake_path.read_text(encoding="utf-8")), keepsake_doc)
        self.assertEqual(json.loads(recall_path.read_text(encoding="utf-8")), recall_doc)

        # Post-seal cancel & re-prepare MUST fail with 409 ALREADY_SEALED
        c_post_can, e_post_can = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "cancel_dignified_retirement"},
            actor_side="partner",
            now_iso="2026-11-19T09:30:00+08:00",
        )
        self.assertEqual(c_post_can, 409)
        self.assertEqual(e_post_can["reason_code"], "ALREADY_SEALED")

        c_post_prep, e_post_prep = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "prepare_dignified_retirement", "reason_cn": "re-prepare"},
            actor_side="partner",
            now_iso="2026-11-19T09:31:00+08:00",
        )
        self.assertEqual(c_post_prep, 409)
        self.assertEqual(e_post_prep["reason_code"], "ALREADY_SEALED")

        # Post-seal set_budget_mode(NORMAL) MUST fail with 409 ALREADY_SEALED and keep disk DORMANT
        c_post_mode, e_post_mode = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "set_budget_mode", "mode": "NORMAL", "reason_cn": "attempt wake"},
            actor_side="partner",
            now_iso="2026-11-19T09:32:00+08:00",
        )
        self.assertEqual(c_post_mode, 409)
        self.assertEqual(e_post_mode["reason_code"], "ALREADY_SEALED")
        persisted_rm = json.loads(
            (self.day_root / "phone" / "v02_runtime_migration.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted_rm["mode_config"]["mode"], "DORMANT")

    # ------------------------------------------------------------------
    # 3. F-113 Opt-In Proactive Outbound Queue & Anti-Harassment Gate
    # ------------------------------------------------------------------

    def test_03_f113_proactive_outbound_rbac_and_strict_gate_order(self) -> None:
        # Zero-write default state
        c0, v0 = rm_view.read_v02_runtime_migration_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-19T10:00:00+08:00"
        )
        self.assertEqual(c0, 200)
        po0 = v0["proactive_outbound"]
        self.assertFalse(po0["enabled"])
        self.assertEqual(po0["quiet_start_hm"], "22:30")
        self.assertEqual(po0["quiet_end_hm"], "07:30")
        self.assertEqual(po0["daily_cap"], 2)
        self.assertEqual(po0["min_cooldown_minutes"], 180)
        self.assertEqual(po0["sent_today_count"], 0)
        self.assertEqual(po0["pending_queue"], [])

        # RBAC: subject cannot configure or acknowledge/cancel; partner cannot enqueue
        c_sub_cfg, e_sub_cfg = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "configure_proactive_outbound", "enabled": True},
            actor_side="subject",
        )
        self.assertEqual(c_sub_cfg, 403)
        self.assertEqual(e_sub_cfg["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        c_sub_ack, e_sub_ack = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "acknowledge_or_cancel_proactive_message", "message_id": "pmsg_0001", "decision": "acknowledge"},
            actor_side="subject",
        )
        self.assertEqual(c_sub_ack, 403)
        self.assertEqual(e_sub_ack["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        c_par_enq, e_par_enq = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "enqueue_proactive_message", "message_cn": "测试"},
            actor_side="partner",
        )
        self.assertEqual(c_par_enq, 403)
        self.assertEqual(e_par_enq["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # (b) Disabled -> 403 PROACTIVE_DISABLED (zero write)
        snap_before = _snapshot_tree(self.tmp)
        c_dis, e_dis = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "enqueue_proactive_message", "message_cn": "窗台薄荷发新芽了"},
            actor_side="subject",
            now_iso="2026-11-19T10:00:00+08:00",
        )
        self.assertEqual(c_dis, 403)
        self.assertEqual(e_dis["reason_code"], "PROACTIVE_DISABLED")
        self.assertEqual(snap_before, _snapshot_tree(self.tmp))

        # Partner configures enabled=True, quiet 22:30-07:30, cap=2, cooldown=120m
        c_cfg, v_cfg = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "configure_proactive_outbound",
                "enabled": True,
                "quiet_start_hm": "22:30",
                "quiet_end_hm": "07:30",
                "daily_cap": 2,
                "min_cooldown_minutes": 120,
                "operation_key": "op_po_cfg_1",
            },
            actor_side="partner",
            now_iso="2026-11-19T09:00:00+08:00",
        )
        self.assertEqual(c_cfg, 200)
        self.assertTrue(v_cfg["proactive_outbound"]["enabled"])
        self.assertEqual(v_cfg["proactive_outbound"]["min_cooldown_minutes"], 120)

        # (c) DORMANT_NO_COGNITION -> 409 DORMANT_BLOCKED
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "set_budget_mode", "mode": "DORMANT_NO_COGNITION"},
            actor_side="partner",
            now_iso="2026-11-19T09:30:00+08:00",
        )
        c_dorm, e_dorm = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "enqueue_proactive_message", "message_cn": "休眠拦截测试"},
            actor_side="subject",
            now_iso="2026-11-19T10:00:00+08:00",
        )
        self.assertEqual(c_dorm, 409)
        self.assertEqual(e_dorm["reason_code"], "DORMANT_BLOCKED")

        # Restore NORMAL mode
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "set_budget_mode", "mode": "NORMAL"},
            actor_side="partner",
            now_iso="2026-11-19T09:45:00+08:00",
        )

        # (d) Quiet hours -> 409 QUIET_HOURS_ACTIVE (both late night & early morning East-8)
        for q_iso in ("2026-11-19T23:10:00+08:00", "2026-11-19T06:45:00+08:00"):
            c_q, e_q = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {"action": "enqueue_proactive_message", "message_cn": "夜里不该打扰"},
                actor_side="subject",
                now_iso=q_iso,
            )
            self.assertEqual(c_q, 409)
            self.assertEqual(e_q["reason_code"], "QUIET_HOURS_ACTIVE")

        # Allowed daytime enqueue #1 + exact-once operation_key replay
        c_e1, v_e1 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "enqueue_proactive_message",
                "message_cn": "窗台的小盆栽冒新芽了",
                "reason_cn": "日常温柔分享",
                "channel_hint": "pocket_phone",
                "operation_key": "op_po_msg_1",
            },
            actor_side="subject",
            now_iso="2026-11-19T10:00:00+08:00",
        )
        self.assertEqual(c_e1, 200)
        msg1_id = v_e1["action_receipt"]["message_id"]
        self.assertEqual(v_e1["proactive_outbound"]["sent_today_count"], 1)
        self.assertEqual(len(v_e1["proactive_outbound"]["pending_queue"]), 1)

        # Exact-once dedup replay even inside cooldown window returns DUPLICATE
        c_e1_dup, v_e1_dup = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "enqueue_proactive_message",
                "message_cn": "窗台的小盆栽冒新芽了",
                "reason_cn": "日常温柔分享",
                "channel_hint": "pocket_phone",
                "operation_key": "op_po_msg_1",
            },
            actor_side="subject",
            now_iso="2026-11-19T10:05:00+08:00",
        )
        self.assertEqual(c_e1_dup, 200)
        self.assertEqual(v_e1_dup["command_status"], "DUPLICATE")
        self.assertEqual(v_e1_dup["proactive_outbound"]["sent_today_count"], 1)

        # (f) Cooldown active -> 429 COOLDOWN_ACTIVE
        c_cd, e_cd = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "enqueue_proactive_message", "message_cn": "太快再发一条"},
            actor_side="subject",
            now_iso="2026-11-19T11:00:00+08:00",
        )
        self.assertEqual(c_cd, 429)
        self.assertEqual(e_cd["reason_code"], "COOLDOWN_ACTIVE")

        # After cooldown (12:05 >= 120m) -> enqueue #2 succeeds
        c_e2, v_e2 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "enqueue_proactive_message",
                "message_cn": "傍晚降温记得添件外套",
                "reason_cn": "天气提醒",
                "operation_key": "op_po_msg_2",
            },
            actor_side="subject",
            now_iso="2026-11-19T12:05:00+08:00",
        )
        self.assertEqual(c_e2, 200)
        msg2_id = v_e2["action_receipt"]["message_id"]
        self.assertEqual(v_e2["proactive_outbound"]["sent_today_count"], 2)

        # (e) Daily cap reached -> 429 DAILY_CAP_REACHED
        c_cap, e_cap = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "enqueue_proactive_message", "message_cn": "第三条超额"},
            actor_side="subject",
            now_iso="2026-11-19T15:30:00+08:00",
        )
        self.assertEqual(c_cap, 429)
        self.assertEqual(e_cap["reason_code"], "DAILY_CAP_REACHED")

        # Partner acknowledges msg1 & cancels msg2
        c_ack, v_ack = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "acknowledge_or_cancel_proactive_message",
                "message_id": msg1_id,
                "decision": "acknowledge",
                "operation_key": "op_po_ack_1",
            },
            actor_side="partner",
            now_iso="2026-11-19T15:40:00+08:00",
        )
        self.assertEqual(c_ack, 200)
        self.assertEqual(v_ack["action_receipt"]["status"], "ACKNOWLEDGED")

        c_cxl, v_cxl = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "acknowledge_or_cancel_proactive_message",
                "message_id": msg2_id,
                "decision": "cancel",
                "operation_key": "op_po_cxl_2",
            },
            actor_side="partner",
            now_iso="2026-11-19T15:45:00+08:00",
        )
        self.assertEqual(c_cxl, 200)
        self.assertEqual(v_cxl["action_receipt"]["status"], "CANCELLED")

        # (a) After dignified retirement seal -> 409 INSTANCE_RETIRED takes top priority!
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "prepare_dignified_retirement", "reason_cn": "收官"},
            actor_side="partner",
            now_iso="2026-11-20T09:00:00+08:00",
        )
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "confirm_dignified_retirement",
                "confirmation_phrase": rm_engine.RETIREMENT_CONFIRMATION_PHRASE_CN,
            },
            actor_side="partner",
            now_iso="2026-11-20T09:01:00+08:00",
        )
        c_ret, e_ret = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "enqueue_proactive_message", "message_cn": "已封存不可发送"},
            actor_side="subject",
            now_iso="2026-11-20T10:00:00+08:00",
        )
        self.assertEqual(c_ret, 409)
        self.assertEqual(e_ret["reason_code"], "INSTANCE_RETIRED")

    # ------------------------------------------------------------------
    # 4. F-076 Self-Claim & F-011/012 External Edit / Canonical Rebuild
    # ------------------------------------------------------------------

    def test_04_f076_self_claim_and_f011_f012_external_edit_rebuild(self) -> None:
        # Partner blocked on all 3 subject-only actions
        for act, pl in (
            ("record_self_claim", {"action": "record_self_claim", "domain": "rhythm", "statement": "喜欢清晨散步"}),
            ("observe_external_edit", {"action": "observe_external_edit", "artifact_id": "art_1"}),
            ("rebuild_projection_from_canonical", {"action": "rebuild_projection_from_canonical", "artifact_id": "art_1"}),
        ):
            c_blk, e_blk = rm_view.mutate_v02_runtime_migration_view(
                self.day_root, pl, actor_side="partner"
            )
            self.assertEqual(c_blk, 403, f"Partner must get 403 for {act}")
            self.assertEqual(e_blk["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # Subject records two period self-claims across months with contradiction detection
        c_sc1, v_sc1 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_self_claim",
                "period_id": "2026-10",
                "claim_key": "evening_walk",
                "domain": "rhythm",
                "polarity": "affirm",
                "statement": "TA 更喜欢傍晚独自散步看云",
                "visibility": "shared",
                "operation_key": "op_sc_1",
            },
            actor_side="subject",
            now_iso="2026-10-15T18:00:00+08:00",
        )
        self.assertEqual(c_sc1, 200)
        self.assertEqual(v_sc1["action_receipt"]["claim"]["period_id"], "2026-10")

        c_sc2, v_sc2 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_self_claim",
                "period_id": "2026-11",
                "claim_key": "evening_walk",
                "domain": "rhythm",
                "polarity": "negate",
                "statement": "天气转冷后，TA 晚上不太想出门散步了",
                "visibility": "shared",
                "operation_key": "op_sc_2",
            },
            actor_side="subject",
            now_iso="2026-11-19T18:00:00+08:00",
        )
        self.assertEqual(c_sc2, 200)
        self.assertEqual(len(v_sc2["action_receipt"]["cross_month_contradictions"]), 1)

        # F-011/012: create ManagedArtifact + bind representation, edit externally, observe, rebuild
        ws_root = self.day_root / "workspace"
        ws_root.mkdir(parents=True, exist_ok=True)
        st_cr, res_cr = rep_lease.create_managed_artifact(
            self.day_root,
            artifact_id="art_notes_1",
            controller_principal_ref="subject:ta",
            subject_id="ta",
            initial_body="# 笔记\n\n第一段原始内容。\n",
            operation_id="op_cr_art1",
            title="日常手记",
            visibility="shared",
            now_iso="2026-11-19T10:00:00+08:00",
        )
        self.assertEqual(st_cr, 200, res_cr)
        st_bd, res_bd = rep_lease.bind_representation(
            self.day_root,
            artifact_id="art_notes_1",
            workspace_root=ws_root,
            workspace_rel_path="notes/daily.md",
            operation_id="op_bd_art1",
            write_projection=True,
            now_iso="2026-11-19T10:01:00+08:00",
        )
        self.assertEqual(st_bd, 200, res_bd)

        # Reject path traversal '..' and symlink paths strictly with 403!
        c_trav, e_trav = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "observe_external_edit",
                "artifact_id": "art_notes_1",
                "workspace_root": str(ws_root),
                "workspace_rel_path": "../escape.md",
            },
            actor_side="subject",
            now_iso="2026-11-19T10:02:00+08:00",
        )
        self.assertEqual(c_trav, 403)
        self.assertIn("PATH_", e_trav["reason_code"])

        symlink_ws = self.tmp / "sym_ws"
        try:
            symlink_ws.symlink_to(ws_root, target_is_directory=True)
            c_sym, e_sym = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "rebuild_projection_from_canonical",
                    "artifact_id": "art_notes_1",
                    "workspace_root": str(symlink_ws),
                },
                actor_side="subject",
                now_iso="2026-11-19T10:02:30+08:00",
            )
            self.assertEqual(c_sym, 403)
            self.assertIn("SYMLINK", e_sym["reason_code"])
        except OSError:
            pass

        # Externally modify bound .md file -> observe_external_edit records DIVERGED observation
        md_file = ws_root / "notes" / "daily.md"
        md_file.write_text("# 笔记\n\n外部编辑器改过的一行。\n", encoding="utf-8")
        c_obs, v_obs = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "observe_external_edit",
                "artifact_id": "art_notes_1",
                "workspace_root": str(ws_root),
                "operation_key": "op_obs_1",
            },
            actor_side="subject",
            now_iso="2026-11-19T10:05:00+08:00",
        )
        self.assertEqual(c_obs, 200)
        self.assertEqual(v_obs["action_receipt"]["code"], "EXTERNAL_EDIT_OBSERVED")
        self.assertEqual(v_obs["action_receipt"]["representation_health"], "DIVERGED")
        self.assertFalse(v_obs["action_receipt"]["observation"]["attributed_as_ta_action_commit"])

        # Rebuild refuses to clobber diverged external edit (409 BLOCKED_CONFLICT)
        c_clob, e_clob = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "rebuild_projection_from_canonical",
                "artifact_id": "art_notes_1",
                "workspace_root": str(ws_root),
                "operation_key": "op_reb_clob",
            },
            actor_side="subject",
            now_iso="2026-11-19T10:06:00+08:00",
        )
        self.assertEqual(c_clob, 409)
        self.assertEqual(e_clob["reason_code"], "BLOCKED_CONFLICT")

        # When projection file is removed (MISSING), rebuild restores canonical head cleanly
        md_file.unlink()
        c_reb, v_reb = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "rebuild_projection_from_canonical",
                "artifact_id": "art_notes_1",
                "workspace_root": str(ws_root),
                "operation_key": "op_reb_ok",
            },
            actor_side="subject",
            now_iso="2026-11-19T10:07:00+08:00",
        )
        self.assertEqual(c_reb, 200)
        self.assertEqual(v_reb["action_receipt"]["code"], "PROJECTION_REBUILT")
        self.assertEqual(md_file.read_text(encoding="utf-8"), "# 笔记\n\n第一段原始内容。\n")

    # ------------------------------------------------------------------
    # 5. F-168 / F-171 / F-173 Live Zero-Write Telemetry
    # ------------------------------------------------------------------

    def test_05_f168_f171_f173_zero_write_telemetry_and_stalled_guard(self) -> None:
        # Empty root GET -> 100% zero write with honest telemetry
        before = _snapshot_tree(self.tmp)
        c0, v0 = rm_view.read_v02_runtime_migration_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-19T09:30:00+08:00"
        )
        self.assertEqual(c0, 200)
        self.assertEqual(before, _snapshot_tree(self.tmp))

        obs = v0["observability_status"]
        self.assertEqual(obs["observability_class"], "QUIET_EVENTLESS_DAY")
        self.assertFalse(obs["stalled_flag"])
        self.assertTrue(obs["is_quiet_unblocked"])

        bqs = v0["budget_quota_status"]
        self.assertFalse(bqs["present"])
        self.assertEqual(bqs["slot_mode"], "four_slot_v03")
        self.assertEqual(bqs["active_slot"], "morning")
        self.assertEqual(bqs["slot_cap"], lrb.V03_FOUR_SLOT_QUOTAS["morning"]["calls"])
        self.assertEqual(bqs["carry_ratio"], 0.5)

        bss = v0["budget_survival_status"]
        self.assertFalse(bss["present"])
        self.assertEqual(bss["tier"], "NORMAL")
        self.assertEqual(bss["llm_calls"], 0)

        # Trigger 90% budget drop in autobiography_skill_provenance -> budget_survival_status reflects it zero-write
        asp_engine.apply_budget_drop(
            self.day_root,
            {"drop_pct": 90, "reason": "PROVIDER_QUOTA_DROP"},
            now_iso="2026-11-19T14:00:00+08:00",
        )
        # Trigger degraded/stalled runtime health via corrupted runtime_migration file -> stalled NEVER quiet!
        (self.day_root / "phone").mkdir(parents=True, exist_ok=True)
        (self.day_root / "phone" / "v02_runtime_migration.json").write_text("{corrupt", encoding="utf-8")

        before2 = _snapshot_tree(self.day_root)
        c1, v1 = rm_view.read_v02_runtime_migration_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-19T14:15:00+08:00"
        )
        self.assertEqual(c1, 200)
        self.assertEqual(before2, _snapshot_tree(self.day_root))
        self.assertTrue(v1["observability_status"]["stalled_flag"])
        self.assertFalse(v1["observability_status"]["is_quiet_unblocked"])
        self.assertEqual(v1["budget_quota_status"]["active_slot"], "afternoon")
        self.assertTrue(v1["budget_survival_status"]["present"])
        self.assertEqual(v1["budget_survival_status"]["tier"], "LOW_BUDGET_CONSERVATION")
        self.assertFalse(v1["budget_survival_status"]["excuse_fabricated"])

    # ------------------------------------------------------------------
    # 6. F-042 S03 perception_ref & life_situation_view Live S03 Owner Check
    # ------------------------------------------------------------------

    def test_06_f042_s03_perception_ref_and_life_situation_view_zero_wal(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)
        # Absent S03 DB -> s03_owner_connected=False, s03_claim_count=0, zero writes
        before0 = _snapshot_tree(self.day_root)
        sit0 = lsv.read_life_situation(
            home=self.day_root,
            subject_id="chiyo.global",
            now="2026-11-19T10:00:00+08:00",
        )
        self.assertEqual(before0, _snapshot_tree(self.day_root))
        self.assertFalse(sit0["s03_owner_connected"])
        self.assertEqual(sit0["s03_claim_count"], 0)
        self.assertFalse(sit0["knowledge"]["s03_owner_connected"])
        self.assertEqual(sit0["knowledge"]["s03_claim_count"], 0)

        # Initialize live S03 DB at <day_root>/s03/claims.sqlite3 and assert claim with perception_ref
        db_path = self.day_root / "s03" / "claims.sqlite3"
        svc = s03.S03ClaimService.initialize(db_path, subject="chiyo.global")
        res = svc.assert_claim(
            predicate_id="inquiry_conclusion",
            scope_id="audio_window_rain",
            value="窗外开始下细雨",
            source_kind=s03.SOURCE_OBSERVED,
            source_ref="crossmodal:audio",
            evidence_ref="aud:aud_0001",
            perception_ref="crossmodal:aud_0001",
            now="2026-11-19T10:05:00+00:00",
            operation_key="op_s03_pref_1",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["perception_ref"], "crossmodal:aud_0001")
        self.assertEqual(_aux_sqlite_files(self.day_root), [])

        cur = svc.current_view(now="2026-11-19T10:06:00+00:00")
        self.assertEqual(cur["claims"][0]["perception_ref"], "crossmodal:aud_0001")
        self.assertTrue(svc.rebuild()["matches"])

        # life_situation_view reads live S03 owner strictly zero-write (mode=ro&immutable=1, zero -wal/-shm!)
        before1 = _snapshot_tree(self.day_root)
        sit1 = lsv.read_life_situation(
            home=self.day_root,
            subject_id="chiyo.global",
            now="2026-11-19T10:10:00+08:00",
        )
        after1 = _snapshot_tree(self.day_root)
        self.assertEqual(before1, after1)
        self.assertEqual(_aux_sqlite_files(self.day_root), [])
        self.assertTrue(sit1["s03_owner_connected"])
        self.assertEqual(sit1["s03_claim_count"], 1)
        self.assertTrue(sit1["knowledge"]["s03_owner_connected"])
        self.assertEqual(sit1["knowledge"]["s03_claim_count"], 1)

        # Backward compatibility: legacy SQLite without perception_ref column still reads & upgrades cleanly
        legacy_db = self.tmp / "legacy_claims.sqlite3"
        conn = sqlite3.connect(str(legacy_db))
        try:
            legacy_sql = s03.SCHEMA_SQL.replace("    perception_ref    TEXT,\n", "")
            conn.executescript(legacy_sql)
            conn.commit()
        finally:
            conn.close()
        leg_svc = s03.S03ClaimService(legacy_db, subject="chiyo.global", create=False)
        leg_res = leg_svc.assert_claim(
            predicate_id="inquiry_conclusion",
            scope_id="legacy_test",
            value="兼容旧库升级测试",
            source_kind=s03.SOURCE_OBSERVED,
            source_ref="crossmodal:legacy",
            evidence_ref="ev:1",
            perception_ref="percept:legacy_1",
            now="2026-11-19T10:20:00+00:00",
            operation_key="op_leg_1",
        )
        self.assertTrue(leg_res["ok"])
        self.assertEqual(leg_res["perception_ref"], "percept:legacy_1")
        self.assertTrue(leg_svc.rebuild()["matches"])

    # ------------------------------------------------------------------
    # 7. Frozen Invariants & File Hygiene (LF, UTF-8 no BOM, 0 Emojis, Neutral TA)
    # ------------------------------------------------------------------

    def test_07_frozen_invariants_and_file_hygiene(self) -> None:
        self.assertEqual(
            rm_view.PARTNER_ALLOWED_ACTIONS,
            frozenset({
                "set_budget_mode",
                "record_carrier_check",
                "pack_selective_suitcase",
                "coalesce_probe",
            }),
        )
        self.assertEqual(
            rm_view.SUBJECT_ALLOWED_ACTIONS,
            frozenset({
                "set_budget_mode",
                "record_carrier_check",
                "pack_selective_suitcase",
                "coalesce_probe",
                "reverify_migrated_skill",
                "record_workspace_audit",
            }),
        )

        owned_files = (
            REPO_ROOT / "components" / "life" / "scripts" / "v02_crossmodal_perception.py",
            REPO_ROOT / "components" / "phone" / "v02_crossmodal_view.py",
            REPO_ROOT / "components" / "life" / "scripts" / "v02_runtime_governance_migration.py",
            REPO_ROOT / "components" / "phone" / "v02_runtime_migration_view.py",
            REPO_ROOT / "components" / "life" / "scripts" / "s03_claims.py",
            REPO_ROOT / "components" / "life" / "scripts" / "life_situation_view.py",
            REPO_ROOT / "tests" / "test_v02_final_closure_capabilities.py",
        )
        for fpath in owned_files:
            raw = fpath.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM forbidden: {fpath}")
            self.assertNotIn(b"\r\n", raw, f"CRLF forbidden: {fpath}")
            text = raw.decode("utf-8")
            emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"unicode.So symbols/emojis forbidden in {fpath}: {emojis[:5]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

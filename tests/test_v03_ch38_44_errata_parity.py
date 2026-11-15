#!/usr/bin/env python3
"""0.3 第 38–44 章及缺口勘误（角色包闭集/导出签名、课表生病与家务只读联动、吵架 QR06/09/10/14、
话题挂起 TH10/12、想念 MH09/12-13、低落期不迁怒与 LEAVE_BE 降打扰、求助小忙 AH02/11/§3）专项单测。
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(Path(__file__).resolve().parent),
    str(ROOT / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import autonomy_rhythm_view as arv  # noqa: E402
from components.phone import emotional_tension_view as et  # noqa: E402
from components.phone import sensory_character_view as scv  # noqa: E402

BJT = timezone(timedelta(hours=8))


class V03Ch38To44ErrataParityTests(unittest.TestCase):
    """Full errata parity verification for v0.3 Chapters 38–44."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.t0 = datetime(2026, 11, 9, 10, 0, 0, tzinfo=BJT)  # Monday 10:00 BJT

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # =========================================================================
    # Chapter 38: sensory_character_view.py
    # =========================================================================

    def test_01_ch38_family_list_dict_none_and_persona_core_2000(self) -> None:
        base_pack = copy.deepcopy(scv.BUILT_IN_PRESETS["chiyo"])

        # 1) family as list (e.g. mother weekly warm_reserved)
        pack_list = copy.deepcopy(base_pack)
        pack_list["family"] = [
            {"id": "mother", "relation": "母亲", "cadence": "weekly", "tone": "warm_reserved"}
        ]
        norm_list = scv.validate_and_normalize_pack(pack_list)
        self.assertIsInstance(norm_list["family"], list)
        self.assertEqual(norm_list["family"][0]["id"], "mother")

        # 2) family as dict
        pack_dict = copy.deepcopy(base_pack)
        pack_dict["family"] = {"id": "sister", "relation": "姐姐", "cadence": "monthly", "tone": "warm"}
        norm_dict = scv.validate_and_normalize_pack(pack_dict)
        self.assertIsInstance(norm_dict["family"], dict)
        self.assertEqual(norm_dict["family"]["id"], "sister")

        # 3) family as None
        pack_none = copy.deepcopy(base_pack)
        pack_none["family"] = None
        norm_none = scv.validate_and_normalize_pack(pack_none)
        self.assertIsNone(norm_none["family"])

        # 4) persona.core up to 2000 chars succeeds; 2001 chars rejected
        pack_2000 = copy.deepcopy(base_pack)
        pack_2000["persona"] = {"core": "静" * 2000}
        norm_2000 = scv.validate_and_normalize_pack(pack_2000)
        self.assertEqual(len(norm_2000["persona"]["core"]), 2000)

        pack_2001 = copy.deepcopy(base_pack)
        pack_2001["persona"] = {"core": "静" * 2001}
        with self.assertRaises(scv.CharacterPackValidationError) as ctx:
            scv.validate_and_normalize_pack(pack_2001)
        self.assertEqual(ctx.exception.reason_code, "PACK_FIELD_INVALID:persona.core")

    def test_02_ch38_closed_set_sections_chiyo_sample_and_layer_fence(self) -> None:
        # chiyo_sample alias & CHIYO_SAMPLE_PACK align with Chapter 38 spec (keeping 4 built-in presets)
        self.assertEqual(len(scv.BUILT_IN_PRESETS), 4)
        self.assertEqual(scv.PRESET_ALIASES.get("chiyo_sample"), "chiyo")
        chiyo_norm = scv.validate_and_normalize_pack(scv.chiyo_sample_pack())
        self.assertIsNone(chiyo_norm["family"])
        self.assertEqual(len(chiyo_norm["obligations"]), 3)
        self.assertEqual(chiyo_norm["sexual_seed"]["seed_status"], "DRAFT")
        self.assertEqual(chiyo_norm["temper"]["friction_fade_h"], 72)

        # Valid closed-set sections all normalize cleanly
        rich_pack = copy.deepcopy(scv.BUILT_IN_PRESETS["chiyo"])
        rich_pack["conversation"] = {"thread_hold_min": 45, "when_jumped": ["FINISHES_FIRST"]}
        rich_pack["busy_periods"] = [
            {"id": "finals", "kind": "EXAM", "from": "06-15", "to": "06-30"}
        ]
        rich_norm = scv.validate_and_normalize_pack(rich_pack)
        self.assertEqual(rich_norm["conversation"]["thread_hold_min"], 45)
        self.assertEqual(rich_norm["busy_periods"][0]["kind"], "EXAM")

        # Unknown subkeys inside closed-set sections are rejected
        for section, bad_payload in (
            ("temper", {"unknown_temper_knob": 1}),
            ("conversation", {"unknown_conv_knob": True}),
            (
                "busy_periods",
                [{"id": "bp1", "kind": "EXAM", "from": "06-01", "to": "06-10", "unknown_busy_knob": "x"}],
            ),
            ("sexual_seed", {"unknown_seed_knob": True}),
            ("obligations", [{"id": "ob1", "unknown_ob_field": 123}]),
        ):
            bad_pack = copy.deepcopy(scv.BUILT_IN_PRESETS["neutral_resident"])
            bad_pack[section] = bad_payload
            with self.assertRaises(scv.CharacterPackValidationError, msg=f"section={section}") as unk_ctx:
                scv.validate_and_normalize_pack(bad_pack)
            self.assertTrue(unk_ctx.exception.reason_code.startswith("PACK_FIELD_UNKNOWN:"))

        # SC17 / STAGE_UNEXPLAINED errata guard in evaluate_adult_gate_preview:
        # STUDY + attends=True + working_adult -> STAGE_UNEXPLAINED; WORK + attends=True -> NOT unexplained
        study_pack = copy.deepcopy(scv.BUILT_IN_PRESETS["neutral_resident"])
        study_pack["obligations"] = [
            {"id": "uni_course", "kind": "STUDY", "institution_kind": "university", "attends": True}
        ]
        gate_study = scv.evaluate_adult_gate_preview(scv.validate_and_normalize_pack(study_pack))
        self.assertEqual(gate_study["verdict"], "FAIL")
        self.assertIn("STAGE_UNEXPLAINED", gate_study["reasons"])

        work_pack = copy.deepcopy(scv.BUILT_IN_PRESETS["neutral_resident"])
        work_pack["obligations"] = [
            {"id": "office_job", "kind": "WORK", "institution_kind": "studio", "attends": True}
        ]
        gate_work = scv.evaluate_adult_gate_preview(scv.validate_and_normalize_pack(work_pack))
        self.assertEqual(gate_work["verdict"], "REQUIRES_DEPLOY_ATTESTATION")
        self.assertNotIn("STAGE_UNEXPLAINED", gate_work["reasons"])

        # Deploy-layer / user-layer keys remain strictly forbidden (DEPLOY_ONLY | USER_ONLY)
        for foreign_key in ("deploy_enabled", "adult_confirmed", "user_genitals", "allow_subject_initiation"):
            bad_top = copy.deepcopy(scv.BUILT_IN_PRESETS["neutral_resident"])
            bad_top[foreign_key] = "forbidden"
            with self.assertRaises(scv.CharacterPackValidationError) as fk_ctx:
                scv.validate_and_normalize_pack(bad_top)
            self.assertTrue(fk_ctx.exception.reason_code.startswith("PACK_FIELD_NOT_ALLOWED:"))

    def test_03_ch38_export_pack_and_attest_character_pack(self) -> None:
        now_iso = self.t0.isoformat(timespec="seconds")
        sel = scv.apply_character_pack_action(
            self.root,
            {"action": "select_preset", "preset_id": "chiyo_sample"},
            subject_view=True,
            now_iso=now_iso,
        )
        self.assertTrue(sel["ok"])
        self.assertEqual(sel["data"]["exportable_pack"]["kind"], "proposed.character_pack.v1")
        self.assertNotIn("attestation", sel["data"]["exportable_pack"])

        # GET projection also includes exportable_pack
        stored_state, state_reason = scv.read_character_pack_state(self.root)
        proj_get = scv.project_character_pack(stored_state, state_reason=state_reason, subject_view=True)
        self.assertEqual(proj_get["exportable_pack"]["kind"], "proposed.character_pack.v1")

        exp = scv.apply_character_pack_action(
            self.root,
            {"action": "export_pack"},
            subject_view=False,
            now_iso=now_iso,
        )
        self.assertTrue(exp["ok"])
        exp_data = exp["data"]
        parsed_export = json.loads(exp_data["pack_json"])
        self.assertEqual(parsed_export["kind"], "proposed.character_pack.v1")
        self.assertEqual(exp_data["filename"], "chiyo.pack.json")
        self.assertEqual(len(exp_data["pack_sha256"]), 64)

        att = scv.apply_character_pack_action(
            self.root,
            {"action": "attest_character_pack"},
            subject_view=False,
            now_iso=now_iso,
        )
        self.assertTrue(att["ok"])
        att_info = att["data"]["attestation"]
        self.assertEqual(att_info["attested_by"], "deployer")
        self.assertEqual(att_info["attested_at"], now_iso)
        self.assertEqual(att_info["pack_sha256"], exp_data["pack_sha256"])
        self.assertTrue(att_info["matches_active_pack"])

    # =========================================================================
    # Chapter 39: Schedule, FamilyChannel, Sick Leave & Intimacy Chores (Read-Only)
    # =========================================================================

    def test_04_ch39_readonly_character_pack_obligations_and_family_channel(self) -> None:
        pack_file = self.root / "phone" / "character_pack.json"
        pack_file.parent.mkdir(parents=True, exist_ok=True)
        custom_pack = copy.deepcopy(scv.BUILT_IN_PRESETS["chiyo"])
        custom_pack["family"] = [
            {"id": "mother", "relation": "母亲", "cadence": "weekly", "tone": "warm_reserved"}
        ]
        custom_pack["obligations"] = [
            {
                "id": "class_tue_am",
                "kind": "class",
                "mode": "FIXED",
                "weekdays": ["TUE"],
                "start_hhmm": "09:00",
                "end_hhmm": "11:30",
                "travel_minutes": 20,
            },
            {
                "id": "edit_offer",
                "kind": "commission",
                "mode": "OFFERED",
                "per_week_range": [1, 2],
                "minutes_range": [60, 120],
                "lead_days_range": [2, 4],
                "pay_range": [200, 400],
            },
        ]
        pack_file.write_text(json.dumps(custom_pack, ensure_ascii=False), encoding="utf-8")
        before_bytes = pack_file.read_bytes()

        # Tue 10:00 -> inside FIXED class_tue_am -> partner sees OUT + family_channel
        t_tue = datetime(2026, 11, 10, 10, 0, 0, tzinfo=BJT)
        proj_partner = et.read_emotional_tension(self.root, now=t_tue, subject_view=False)
        self.assertEqual(proj_partner["schedule"]["partner_status"], "OUT")
        fc = proj_partner["schedule"]["family_channel"]
        self.assertTrue(fc["configured"])
        self.assertEqual(fc["member_count"], 1)
        self.assertGreaterEqual(len(fc["due_contacts"]), 1)
        self.assertFalse(fc["due_contacts"][0]["enters_private_scene"])
        self.assertEqual(pack_file.read_bytes(), before_bytes)
        self.assertFalse(et.emotional_tension_state_path(self.root).exists())

        # Subject can decide_obligation on character_pack obligation directly
        proj_her = et.read_emotional_tension(self.root, now=t_tue, subject_view=True)
        occ_id = next(
            o["occurrence_id"]
            for o in proj_her["schedule"]["weekly_occurrences"]
            if o["obligation_id"] == "class_tue_am"
        )
        skipped = et.apply_emotional_tension_action(
            self.root,
            {"action": "decide_obligation", "occurrence_id": occ_id, "decision": "SKIP"},
            now=t_tue,
            subject_view=True,
        )
        self.assertEqual(skipped["schedule"]["partner_status"], "FREE")

    def test_05_ch39_ecology_sick_leave_exemption_and_adult_intimacy_chores_readonly(self) -> None:
        et.apply_emotional_tension_action(
            self.root,
            {
                "action": "upsert_schedule_obligation",
                "obligation_id": "wed-shift",
                "mode": "FIXED",
                "kind": "part_time_shift",
                "weekdays": ["WED"],
                "start_hhmm": "13:00",
                "end_hhmm": "16:00",
            },
            now=self.t0,
            subject_view=True,
        )
        eco_path = self.root / "world" / "v03_ecology_state.json"
        eco_path.parent.mkdir(parents=True, exist_ok=True)
        eco_path.write_text(
            json.dumps({"illness": {"phase": "acute_illness"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        intim_path = self.root / "phone" / "adult_intimacy.json"
        intim_path.write_text(
            json.dumps(
                {
                    "space": {
                        "bed_linen_state": "RUMPLED",
                        "stock": {"tissue": 0, "water": 2},
                        "chores": [{"kind": "AIR_ROOM", "target": "window"}],
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        eco_bytes = eco_path.read_bytes()
        intim_bytes = intim_path.read_bytes()

        t_wed = datetime(2026, 11, 11, 14, 0, 0, tzinfo=BJT)
        proj = et.read_emotional_tension(self.root, now=t_wed, subject_view=True)
        self.assertTrue(proj["schedule"]["sick_leave"])
        self.assertEqual(proj["schedule"]["exemption_status"], "SICK_LEAVE")
        # Exempted during illness -> FREE instead of OUT, occurrence marked SICK_LEAVE
        self.assertEqual(proj["schedule"]["partner_status"], "FREE")
        wed_occ = next(
            o for o in proj["schedule"]["weekly_occurrences"] if o["obligation_id"] == "wed-shift"
        )
        self.assertEqual(wed_occ["decision"], "SICK_LEAVE")
        self.assertEqual(wed_occ["exemption"], "SICK_LEAVE")

        chore_kinds = {c["kind"] for c in proj["schedule"]["chores"]}
        self.assertIn("CHANGE_SHEETS", chore_kinds)
        self.assertIn("RESTOCK", chore_kinds)
        self.assertIn("AIR_ROOM", chore_kinds)
        # Strictly zero-write on foreign state files
        self.assertEqual(eco_path.read_bytes(), eco_bytes)
        self.assertEqual(intim_path.read_bytes(), intim_bytes)

    # =========================================================================
    # Chapter 40: Quarrel QR06 / QR09 / QR10 / QR14
    # =========================================================================

    def test_06_ch40_qr06_dissolve_friction_rejects_apology_and_gift_409(self) -> None:
        noted = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "note_friction",
                "scope": "HOUSEHOLD",
                "incident_ref": "inc-f-qr06",
                "incident_kind": "OFFHAND_REMARK",
            },
            now=self.t0,
            subject_view=True,
        )
        fid = noted["friction_id"]

        for bad_by in ("APOLOGY", "GIFT"):
            with self.assertRaises(et.EmotionalTensionConflictError) as ctx:
                et.apply_emotional_tension_action(
                    self.root,
                    {"action": "dissolve_friction", "friction_id": fid, "by": bad_by},
                    now=self.t0 + timedelta(minutes=5),
                    subject_view=True,
                )
            self.assertEqual(ctx.exception.reason_code, "VERBAL_APOLOGY_OR_GIFT_INSUFFICIENT")

        ok_dis = et.apply_emotional_tension_action(
            self.root,
            {"action": "dissolve_friction", "friction_id": fid, "by": "LET_PASS"},
            now=self.t0 + timedelta(minutes=10),
            subject_view=True,
        )
        self.assertEqual(len(ok_dis["quarrel"]["frictions"]), 0)

    def test_07_ch40_qr09_upgrade_friction_to_rupture_and_open_rupture_from_friction(self) -> None:
        f1 = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "note_friction",
                "scope": "US",
                "incident_ref": "inc-upg-1",
                "incident_kind": "SMALL_CLASH",
            },
            now=self.t0,
            subject_view=True,
        )
        upg1 = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "upgrade_friction_to_rupture",
                "from_friction_id": f1["friction_id"],
                "rupture_id": "rup-from-fric-1",
            },
            now=self.t0 + timedelta(minutes=15),
            subject_view=True,
        )
        self.assertEqual(upg1["rupture_status"], "OPEN")
        self.assertEqual(upg1["upgraded_from_friction_id"], f1["friction_id"])
        self.assertEqual(len(upg1["quarrel"]["frictions"]), 0)

        f2 = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "note_friction",
                "scope": "PLANS",
                "incident_ref": "inc-upg-2",
                "incident_kind": "TEASE_LANDED_WRONG",
            },
            now=self.t0 + timedelta(minutes=20),
            subject_view=True,
        )
        upg2 = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "open_rupture",
                "from_friction_id": f2["friction_id"],
                "rupture_id": "rup-from-fric-2",
                "description": "玩笑越界升级成正式破裂",
                "repair_conditions": [{"kind": "CHANGE_SEEN", "min_count": 1, "distinct_days": 1}],
            },
            now=self.t0 + timedelta(minutes=25),
            subject_view=True,
        )
        self.assertEqual(upg2["rupture_status"], "OPEN")
        self.assertEqual(upg2["upgraded_from_friction_id"], f2["friction_id"])

    def test_08_ch40_qr10_change_seen_rejects_recognition_alone_requires_real_action(self) -> None:
        et.apply_emotional_tension_action(
            self.root,
            {
                "action": "open_rupture",
                "rupture_id": "rup-qr10",
                "scope": "US",
                "description": "说到没做到",
                "incident_ref": "inc-qr10",
                "incident_kind": "COMMITMENT_BROKEN",
                "repair_conditions": [{"kind": "CHANGE_SEEN", "min_count": 1, "distinct_days": 1}],
            },
            now=self.t0,
            subject_view=True,
        )
        rec_only = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "record_behavior_change",
                "actor": "subject",
                "rupture_id": "rup-qr10",
                "evidence_ref": "ev-rec-only",
                "behavior_kind": "RECOGNITION",
            },
            now=self.t0 + timedelta(hours=2),
            subject_view=True,
        )
        self.assertFalse(rec_only["conditions_met"])
        self.assertFalse(rec_only["resolved_rupture"])
        self.assertEqual(rec_only["rupture_status"], "OPEN")

        real_act = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "record_behavior_change",
                "actor": "subject",
                "rupture_id": "rup-qr10",
                "evidence_ref": "ev-real-action",
                "behavior_kind": "COMMITMENT_KEPT",
            },
            now=self.t0 + timedelta(hours=4),
            subject_view=True,
        )
        self.assertTrue(real_act["conditions_met"])
        self.assertTrue(real_act["resolved_rupture"])
        self.assertEqual(real_act["rupture_status"], "RESOLVED")

    def test_09_ch40_qr14_let_go_rupture_drop_requested_at_and_sleep_or_cooldown_gate(self) -> None:
        et.apply_emotional_tension_action(
            self.root,
            {
                "action": "open_rupture",
                "rupture_id": "rup-qr14",
                "scope": "CRAFT",
                "description": "随口贬低手稿",
                "incident_ref": "inc-qr14",
                "incident_kind": "WORDS_HURT",
                "repair_conditions": [{"kind": "CHANGE_SEEN", "min_count": 1, "distinct_days": 1}],
            },
            now=self.t0,
            subject_view=True,
        )

        # Unslept and within 12h cooldown -> 409 TOO_SOON
        with self.assertRaises(et.EmotionalTensionConflictError) as early_ctx:
            et.apply_emotional_tension_action(
                self.root,
                {"action": "let_go_rupture", "rupture_id": "rup-qr14"},
                now=self.t0 + timedelta(hours=2),
                subject_view=True,
            )
        self.assertEqual(early_ctx.exception.reason_code, "TOO_SOON")

        # Partner requests drop right at 13h -> immediate let_go 5m later still blocked (<30m quiet gate)
        t_drop = self.t0 + timedelta(hours=13)
        et.apply_emotional_tension_action(
            self.root,
            {"action": "request_drop", "rupture_id": "rup-qr14", "note": "别气了翻篇吧"},
            now=t_drop,
            subject_view=False,
        )
        with self.assertRaises(et.EmotionalTensionConflictError) as drop_ctx:
            et.apply_emotional_tension_action(
                self.root,
                {"action": "let_go_rupture", "rupture_id": "rup-qr14"},
                now=t_drop + timedelta(minutes=5),
                subject_view=True,
            )
        self.assertEqual(drop_ctx.exception.reason_code, "TOO_SOON")

        # After quiet window + cooldown/sleep -> LET_GO succeeds
        let_go = et.apply_emotional_tension_action(
            self.root,
            {"action": "let_go_rupture", "rupture_id": "rup-qr14"},
            now=t_drop + timedelta(minutes=45),
            subject_view=True,
        )
        self.assertEqual(let_go["rupture_status"], "LET_GO")

    # =========================================================================
    # Chapter 41: autonomy_rhythm_view.py TH10 & TH12
    # =========================================================================

    def test_10_ch41_th10_clear_session_threads_and_session_switch(self) -> None:
        now_iso = "2026-11-11T15:00:00+08:00"
        arv.apply_autonomy_rhythm_action(
            self.root,
            {
                "action": "hold_subject_topic",
                "topic_label": "胶片冲洗显影时间",
                "reason": "MID_TELLING",
            },
            subject_view=True,
            now_iso=now_iso,
        )
        arv.apply_autonomy_rhythm_action(
            self.root,
            {"action": "evaluate_turn", "user_text": "继续聊胶片冲洗显影时间", "session_id": "sess-a"},
            subject_view=False,
            now_iso="2026-11-11T15:01:00+08:00",
        )

        cleared = arv.clear_session_threads(
            self.root, session_id="sess-b", now_iso="2026-11-11T15:02:00+08:00"
        )
        self.assertEqual(cleared["cleared_count"], 1)
        self.assertEqual(cleared["topic_threads"]["held_count"], 0)
        self.assertIsNone(cleared["topic_threads"]["last_session_topic"])

        # Session switch in evaluate_turn (sess-b -> sess-c) also clears old session topic inertia
        arv.apply_autonomy_rhythm_action(
            self.root,
            {"action": "evaluate_turn", "user_text": "今天在读一本植物学旧书", "session_id": "sess-b"},
            subject_view=False,
            now_iso="2026-11-11T15:05:00+08:00",
        )
        sw = arv.apply_autonomy_rhythm_action(
            self.root,
            {"action": "evaluate_turn", "user_text": "晚饭吃点什么好呢", "session_id": "sess-c"},
            subject_view=False,
            now_iso="2026-11-11T15:06:00+08:00",
        )
        self.assertFalse(sw["turn_decision"]["topic_shift_detected"])
        self.assertEqual(sw["turn_decision"]["selected_thread_response"], "FOLLOW")

    def test_11_ch41_th12_adult_intimacy_active_and_aftercare_suppress_thread_pullback(self) -> None:
        held = arv.apply_autonomy_rhythm_action(
            self.root,
            {
                "action": "hold_subject_topic",
                "topic_label": "二手书店淘到的画册",
                "reason": "MID_TELLING",
            },
            subject_view=True,
            now_iso="2026-11-11T21:00:00+08:00",
        )
        tid = held["thread"]["thread_id"]

        intim_path = self.root / "phone" / "adult_intimacy.json"
        intim_path.parent.mkdir(parents=True, exist_ok=True)
        for permit_state in ("ACTIVE", "AFTERCARE"):
            intim_path.write_text(
                json.dumps(
                    {
                        "permits": {
                            "active_permit_id": "perm-01",
                            "records": {"perm-01": {"state": permit_state, "phase": permit_state}},
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            saved_bytes = intim_path.read_bytes()

            proj = arv.read_autonomy_rhythm(
                self.root, subject_view=True, now_iso="2026-11-11T21:05:00+08:00"
            )
            self.assertTrue(proj["topic_threads"]["suppressed_by_intimate_scene"])
            self.assertEqual(proj["topic_threads"]["can_return_ids"], [])

            with self.assertRaises(arv.AutonomyRhythmConflictError) as ret_err:
                arv.apply_autonomy_rhythm_action(
                    self.root,
                    {"action": "return_to_thread", "thread_id": tid},
                    subject_view=True,
                    now_iso="2026-11-11T21:06:00+08:00",
                )
            self.assertEqual(ret_err.exception.reason_code, "PRIVATE_SCENE_ACTIVE")

            ev = arv.apply_autonomy_rhythm_action(
                self.root,
                {"action": "evaluate_turn", "user_text": "抱紧一点休息会儿"},
                subject_view=False,
                now_iso="2026-11-11T21:07:00+08:00",
            )
            self.assertFalse(ev["turn_decision"]["topic_shift_detected"])
            self.assertEqual(ev["turn_decision"]["selected_thread_response"], "FOLLOW")
            self.assertEqual(ev["turn_decision"]["offered_thread_options"], ["FOLLOW"])
            self.assertEqual(intim_path.read_bytes(), saved_bytes)

        # Once intimate scene & aftercare close -> return_to_thread works normally
        intim_path.write_text(
            json.dumps(
                {
                    "permits": {
                        "active_permit_id": None,
                        "records": {"perm-01": {"state": "CLOSED", "phase": "CLOSED"}},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ret_ok = arv.apply_autonomy_rhythm_action(
            self.root,
            {"action": "return_to_thread", "thread_id": tid},
            subject_view=True,
            now_iso="2026-11-11T21:15:00+08:00",
        )
        self.assertTrue(ret_ok["returned_thread"]["returned_once"])

    # =========================================================================
    # Chapter 42: Missing Partner MH09 & MH12–13
    # =========================================================================

    def test_12_ch42_mh12_13_bidirectional_thought_of_him_and_mh09_suppression(self) -> None:
        t_wed = datetime(2026, 11, 11, 15, 0, 0, tzinfo=BJT)

        # Bidirectional thought_of_him / thought_of_you
        h1 = et.apply_emotional_tension_action(
            self.root,
            {"action": "thought_of_him", "by": "her", "cue": "路过面包店闻到烤栗子味"},
            now=t_wed,
            subject_view=True,
        )
        self.assertEqual(h1["by"], "subject")
        self.assertTrue(h1["surfaced"])
        self.assertFalse(h1["tracks_absence_duration"])

        p1 = et.apply_emotional_tension_action(
            self.root,
            {"action": "thought_of_you", "by": "him", "cue": "看到一只很像你画过的小猫"},
            now=t_wed + timedelta(minutes=2),
            subject_view=False,
        )
        self.assertEqual(p1["by"], "partner")
        self.assertTrue(p1["warm_acknowledgment"])

        # Same cue by subject twice on same day -> 409 THOUGHT_CUE_ALREADY_USED_TODAY
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "thought_of_him", "by": "her", "cue": "路过面包店闻到烤栗子味"},
                now=t_wed + timedelta(minutes=5),
                subject_view=True,
            )

        # MH09: Suppress proactive missing_note in in_space, partner_urgent, or active adult_intimacy
        et.apply_emotional_tension_action(
            self.root,
            {
                "action": "add_missing_note",
                "note_id": "mn-mh09",
                "source_ref": "leaf-01",
                "kind": "FOUND",
                "summary": "捡到一片银杏叶",
            },
            now=t_wed + timedelta(minutes=10),
            subject_view=True,
        )
        et.apply_emotional_tension_action(
            self.root,
            {"action": "declare_need_space", "incident_ref": "inc-mh09-space", "space_style": "SHORT"},
            now=t_wed + timedelta(minutes=12),
            subject_view=True,
        )
        arr_space = et.apply_emotional_tension_action(
            self.root,
            {"action": "partner_arrive", "partner_urgent": False},
            now=t_wed + timedelta(minutes=15),
            subject_view=False,
        )
        self.assertEqual(arr_space["surfaced_count"], 0)
        self.assertTrue(arr_space["proactive_missing_suppressed"])

        sup_thought = et.apply_emotional_tension_action(
            self.root,
            {"action": "thought_of_him", "by": "her", "cue": "窗台下起小雨"},
            now=t_wed + timedelta(minutes=16),
            subject_view=True,
        )
        self.assertTrue(sup_thought["suppressed"])

        # Clear space via sleep, then test active adult_intimacy suppression
        et.apply_emotional_tension_action(
            self.root,
            {"action": "complete_sleep"},
            now=t_wed + timedelta(hours=3),
            subject_view=True,
        )
        intim_path = self.root / "phone" / "adult_intimacy.json"
        intim_path.parent.mkdir(parents=True, exist_ok=True)
        intim_path.write_text(
            json.dumps({"permits": {"active_permit_id": "p-mh09"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        arr_intim = et.apply_emotional_tension_action(
            self.root,
            {"action": "partner_arrive", "partner_urgent": False},
            now=t_wed + timedelta(hours=3, minutes=5),
            subject_view=False,
        )
        self.assertEqual(arr_intim["surfaced_count"], 0)
        self.assertEqual(arr_intim["missing_partner"]["surfaced_notes"], [])

    # =========================================================================
    # Chapter 43: Low Mood (blame_partner=False & LEAVE_BE reduces burden)
    # =========================================================================

    def test_13_ch43_low_mood_blame_partner_always_false_and_leave_be_reduces_burden(self) -> None:
        chosen_seed = None
        win = None
        for idx in range(100):
            cand = f"seed-lm43-{idx}"
            w = et.derive_weekly_low_mood_window(cand, self.t0)
            if w is not None:
                chosen_seed = cand
                win = w
                break
        assert chosen_seed is not None and win is not None
        et.apply_emotional_tension_action(
            self.root,
            {"action": "configure_instance_seed", "instance_seed": chosen_seed},
            now=self.t0,
            subject_view=True,
        )
        in_dt = win["_start_dt"] + timedelta(minutes=20)

        why = et.apply_emotional_tension_action(
            self.root,
            {"action": "why_low_mood"},
            now=in_dt,
            subject_view=False,
        )
        self.assertFalse(why["blame_partner"])
        self.assertFalse(why["low_mood"]["blame_partner"])

        c1 = et.apply_emotional_tension_action(
            self.root,
            {"action": "comfort_low_mood", "attempt_kind": "ASK_WHY"},
            now=in_dt + timedelta(minutes=5),
            subject_view=False,
        )
        c2 = et.apply_emotional_tension_action(
            self.root,
            {"action": "comfort_low_mood", "attempt_kind": "CHEER_UP"},
            now=in_dt + timedelta(minutes=10),
            subject_view=False,
        )
        self.assertGreater(c2["intrusion_burden"], c1["intrusion_burden"])
        self.assertFalse(c2["blame_partner"])

        leave_be = et.apply_emotional_tension_action(
            self.root,
            {"action": "comfort_low_mood", "attempt_kind": "LEAVE_BE"},
            now=in_dt + timedelta(minutes=15),
            subject_view=False,
        )
        self.assertTrue(leave_be["reduced_intrusion_burden"])
        self.assertLess(leave_be["intrusion_burden"], c2["intrusion_burden"])
        self.assertFalse(leave_be["blame_partner"])

    # =========================================================================
    # Chapter 44: Asking Partner a Small Favor (AH02, AH11, 44 §3)
    # =========================================================================

    def test_14_ch44_ah02_sit_awhile_optional_target_object_ref(self) -> None:
        t_free = datetime(2026, 11, 11, 19, 0, 0, tzinfo=BJT)

        # sit_awhile without target_object_ref succeeds (AH02)
        fav = et.apply_emotional_tension_action(
            self.root,
            {"action": "ask_partner_favor", "favor_kind": "sit_awhile", "summary": "在沙发边陪坐一会儿"},
            now=t_free,
            subject_view=True,
        )
        self.assertIsNone(fav["target_object_ref"])
        self.assertEqual(fav["asking_partner"]["open_favor"]["favor_id"], fav["favor_id"])

        et.apply_emotional_tension_action(
            self.root,
            {"action": "withdraw_favor", "favor_id": fav["favor_id"]},
            now=t_free + timedelta(minutes=1),
            subject_view=True,
        )

        # Object-bound favor without target_object_ref still rejected (400)
        with self.assertRaises(et.EmotionalTensionValidationError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "ask_partner_favor", "favor_kind": "look_once", "summary": "帮我看一眼"},
                now=t_free + timedelta(minutes=2),
                subject_view=True,
            )

    def test_15_ch44_ah11_respond_later_sets_deferred_later_without_locking_quota(self) -> None:
        t_night = datetime(2026, 11, 11, 20, 0, 0, tzinfo=BJT)
        fav1 = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "ask_partner_favor",
                "favor_kind": "look_once",
                "target_object_ref": "draft-page-01",
                "summary": "有空帮我看一眼这页排版",
            },
            now=t_night,
            subject_view=True,
        )
        fid1 = fav1["favor_id"]

        later = et.apply_emotional_tension_action(
            self.root,
            {"action": "respond_favor", "favor_id": fid1, "decision": "LATER"},
            now=t_night + timedelta(minutes=5),
            subject_view=False,
        )
        self.assertEqual(later["favor_status"], "DEFERRED_LATER")
        self.assertFalse(later["entered_cooldown"])
        self.assertTrue(later["retains_right_to_respond"])
        self.assertEqual(len(later["asking_partner"]["deferred_favors"]), 1)
        her_after_later = et.read_emotional_tension(
            self.root, now=t_night + timedelta(minutes=5), subject_view=True
        )
        self.assertEqual(her_after_later["asking_partner"]["cooldown_favor_kinds_until_sleep"], [])

        # Quota is NOT locked for the rest of the night -> another favor (even same kind) allowed!
        fav2 = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "ask_partner_favor",
                "favor_kind": "look_once",
                "target_object_ref": "draft-page-02",
                "summary": "顺便瞄一眼封面字号",
            },
            now=t_night + timedelta(minutes=20),
            subject_view=True,
        )
        self.assertIsNotNone(fav2["favor_id"])

        # Partner retains right to respond to the earlier DEFERRED_LATER favor!
        acc_deferred = et.apply_emotional_tension_action(
            self.root,
            {"action": "respond_favor", "favor_id": fid1, "decision": "ACCEPT"},
            now=t_night + timedelta(minutes=45),
            subject_view=False,
        )
        self.assertEqual(acc_deferred["favor_status"], "ACCEPTED")
        self.assertEqual(len(acc_deferred["asking_partner"]["deferred_favors"]), 0)

    def test_16_ch44_sec3_non_in_space_disagreement_and_tension_allow_pass_water_or_tissue(self) -> None:
        t_eve = datetime(2026, 11, 11, 21, 0, 0, tzinfo=BJT)

        # Open disagreement AND live relational rupture (NOT in_space)
        et.apply_emotional_tension_action(
            self.root,
            {"action": "open_disagreement", "disagreement_id": "dis-44", "scope": "PLANS", "hold": "HELD_FIRMLY"},
            now=t_eve,
            subject_view=True,
        )
        et.apply_emotional_tension_action(
            self.root,
            {
                "action": "open_rupture",
                "rupture_id": "rup-44",
                "scope": "US",
                "description": "因为计划分歧吵了一架",
                "incident_ref": "inc-44",
                "incident_kind": "SMALL_CLASH",
                "repair_conditions": [{"kind": "CHANGE_SEEN", "min_count": 1, "distinct_days": 1}],
            },
            now=t_eve + timedelta(minutes=1),
            subject_view=True,
        )

        # Everyday pass-water / pass-tissue favor succeeds while NOT in_space
        water_fav = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "ask_partner_favor",
                "favor_kind": "help_briefly",
                "target_object_ref": "cup-water-01",
                "summary": "顺手递杯水给我",
            },
            now=t_eve + timedelta(minutes=5),
            subject_view=True,
        )
        self.assertFalse(water_fav["resolves_rupture"])
        self.assertFalse(water_fav["resolves_disagreement"])
        et.apply_emotional_tension_action(
            self.root,
            {"action": "respond_favor", "favor_id": water_fav["favor_id"], "decision": "ACCEPT"},
            now=t_eve + timedelta(minutes=6),
            subject_view=False,
        )

        # Once in_space is declared, even pass-tissue is blocked
        et.apply_emotional_tension_action(
            self.root,
            {"action": "declare_need_space", "incident_ref": "inc-44", "space_style": "SHORT"},
            now=t_eve + timedelta(minutes=10),
            subject_view=True,
        )
        with self.assertRaises(et.EmotionalTensionConflictError) as blk_ctx:
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "ask_partner_favor",
                    "favor_kind": "help_briefly",
                    "target_object_ref": "box-tissue-01",
                    "summary": "递张纸巾",
                },
                now=t_eve + timedelta(minutes=12),
                subject_view=True,
            )
        self.assertEqual(blk_ctx.exception.reason_code, "TENSION_BLOCKS_FAVOR")


if __name__ == "__main__":
    unittest.main()

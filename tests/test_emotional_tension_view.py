#!/usr/bin/env python3
"""Unit tests for v3.0 emotional tension & daily autonomy (`emotional_tension_view`)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from components.phone import emotional_tension_view as et

BJT = timezone(timedelta(hours=8))


class EmotionalTensionViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.t0 = datetime(2026, 11, 11, 10, 0, 0, tzinfo=BJT)  # Wednesday 10:00 BJT

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- Zero-write GET, Role Neutrality, Validation & Idempotency ----

    def test_01_get_zero_write_and_partner_privacy(self) -> None:
        doc = et.read_emotional_tension(self.root, now=self.t0, subject_view=False)
        self.assertEqual(doc["schema"], et.EMOTIONAL_TENSION_SCHEMA)
        self.assertEqual(doc["revision"], 0)
        self.assertEqual(doc["view"], "partner")
        self.assertFalse(et.emotional_tension_state_path(self.root).exists())
        # Partner view never leaks private schedule memos or obligations map
        self.assertFalse(doc["schedule"]["exposes_private_memo"])
        self.assertNotIn("obligations", doc["schedule"])
        # Default neutral character identity ("TA")
        self.assertEqual(doc["character"]["pronouns"], "TA")

    def test_02_subject_view_exposes_schedule_details_zero_write(self) -> None:
        doc = et.read_emotional_tension(self.root, now=self.t0, subject_view=True)
        self.assertEqual(doc["view"], "subject")
        self.assertTrue(doc["schedule"]["exposes_private_memo"])
        self.assertIn("obligations", doc["schedule"])
        self.assertFalse(et.emotional_tension_state_path(self.root).exists())

    def test_03_unknown_and_foreign_fields_rejected_400(self) -> None:
        with self.assertRaises(et.EmotionalTensionValidationError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "partner_arrive", "unknown_extra": 1},
                now=self.t0,
                subject_view=False,
            )
        with self.assertRaises(et.EmotionalTensionValidationError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "partner_arrive", "affection_score": 99},
                now=self.t0,
                subject_view=False,
            )
        with self.assertRaises(et.EmotionalTensionValidationError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "comfort_low_mood", "attempt_kind": "   "},
                now=self.t0,
                subject_view=False,
            )

    def test_04_subject_only_actions_require_capability_403(self) -> None:
        for act in ("open_rupture", "declare_need_space", "add_missing_note", "ask_partner_favor"):
            with self.assertRaises(et.EmotionalTensionPermissionError):
                et.apply_emotional_tension_action(
                    self.root,
                    {"action": act},
                    now=self.t0,
                    subject_view=False,
                )

    def test_05_idempotency_replay_and_cas_conflict(self) -> None:
        r1 = et.apply_emotional_tension_action(
            self.root,
            {"action": "partner_arrive", "partner_urgent": False, "operation_key": "op-arr-1"},
            now=self.t0,
            subject_view=False,
        )
        self.assertEqual(r1["status"], "applied")
        self.assertEqual(r1["revision"], 1)

        # Replay same key + same payload -> already_applied, revision unchanged
        r2 = et.apply_emotional_tension_action(
            self.root,
            {"action": "partner_arrive", "partner_urgent": False, "operation_key": "op-arr-1"},
            now=self.t0,
            subject_view=False,
        )
        self.assertEqual(r2["status"], "already_applied")
        self.assertTrue(r2["replayed"])
        self.assertEqual(r2["revision"], 1)

        # Same key + different payload -> 409 mismatch
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "partner_arrive", "partner_urgent": True, "operation_key": "op-arr-1"},
                now=self.t0,
                subject_view=False,
            )

        # Wrong expected_revision -> 409
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "partner_arrive", "expected_revision": 99},
                now=self.t0,
                subject_view=False,
            )

    # ---- 1. Schedule & Obligations (SC01-SC19) ----

    def test_06_schedule_out_status_skip_without_debt_and_offer_deadline(self) -> None:
        # Wed 10:00 falls inside ob_fixed_routine (MON/WED/FRI 09:30-12:00) -> OUT
        p_doc = et.read_emotional_tension(self.root, now=self.t0, subject_view=False)
        self.assertEqual(p_doc["schedule"]["partner_status"], "OUT")
        self.assertTrue(p_doc["schedule"]["is_busy"])

        # Subject decides to SKIP Wednesday morning -> becomes FREE, zero debt/penalty
        s_doc = et.read_emotional_tension(self.root, now=self.t0, subject_view=True)
        wed_occ = next(
            o["occurrence_id"]
            for o in s_doc["schedule"]["weekly_occurrences"]
            if o["obligation_id"] == "ob_fixed_routine" and o["weekday"] == "WED"
        )
        skipped = et.apply_emotional_tension_action(
            self.root,
            {"action": "decide_obligation", "occurrence_id": wed_occ, "decision": "SKIP"},
            now=self.t0,
            subject_view=True,
        )
        self.assertFalse(skipped["penalty_applied"])
        self.assertFalse(skipped["debt_added"])
        self.assertEqual(skipped["schedule"]["partner_status"], "FREE")

        # Accept weekly offer -> creates deadline -> complete_deadline -> emits idempotent income claim
        offers = skipped["schedule"]["pending_offers"]
        self.assertGreaterEqual(len(offers), 1)
        oid = offers[0]["offer_id"]
        acc = et.apply_emotional_tension_action(
            self.root,
            {"action": "respond_offer", "offer_id": oid, "decision": "ACCEPT"},
            now=self.t0,
            subject_view=True,
        )
        self.assertTrue(acc["deadline_created"])
        done = et.apply_emotional_tension_action(
            self.root,
            {"action": "complete_deadline", "offer_id": oid},
            now=self.t0 + timedelta(hours=2),
            subject_view=True,
        )
        self.assertIsNotNone(done["income_claim"])
        self.assertGreater(done["income_claim"]["amount"], 0)

    # ---- 2. Quarrel, Friction, Behavioral Repair & Space (QR01-QR26) ----

    def test_07_friction_natural_fade_and_absence_not_incident(self) -> None:
        # Absence / late reply is NEVER a quarrel incident
        with self.assertRaises(et.EmotionalTensionConflictError) as ctx:
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "note_friction",
                    "scope": "US",
                    "incident_ref": "inc-late-1",
                    "incident_kind": "NO_REPLY",
                },
                now=self.t0,
                subject_view=True,
            )
        self.assertEqual(ctx.exception.reason_code, "ABSENCE_NOT_AN_INCIDENT")

        # Valid friction fades naturally after friction_fade_hours (设计40章默认 48h)
        res = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "note_friction",
                "scope": "CRAFT",
                "incident_ref": "inc-fric-1",
                "incident_kind": "OFFHAND_REMARK",
            },
            now=self.t0,
            subject_view=True,
        )
        self.assertEqual(len(res["quarrel"]["frictions"]), 1)

        # Duplicate incident_ref rejected
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "note_friction",
                    "scope": "CRAFT",
                    "incident_ref": "inc-fric-1",
                    "incident_kind": "SMALL_CLASH",
                },
                now=self.t0 + timedelta(minutes=10),
                subject_view=True,
            )

        # 47h 后仍在（默认 48h 未到）
        before_fade = et.read_emotional_tension(
            self.root, now=self.t0 + timedelta(hours=47), subject_view=False
        )
        self.assertEqual(len(before_fade["quarrel"]["frictions"]), 1)

        # 49h 后自然淡去
        after_fade = et.read_emotional_tension(
            self.root, now=self.t0 + timedelta(hours=49), subject_view=False
        )
        self.assertEqual(len(after_fade["quarrel"]["frictions"]), 0)

    def test_08_rupture_never_repaired_by_apology_or_gift_only_behavior_change(self) -> None:
        opened = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "open_rupture",
                "rupture_id": "rup-boundary-01",
                "scope": "US",
                "description": "说了不要翻草稿还是翻了",
                "incident_ref": "inc-rup-01",
                "incident_kind": "BOUNDARY_IGNORED",
                "repair_conditions": [{"kind": "CHANGE_SEEN", "min_count": 2, "distinct_days": 2}],
            },
            now=self.t0,
            subject_view=True,
        )
        self.assertEqual(opened["rupture_status"], "OPEN")
        self.assertTrue(opened["quarrel"]["blocks_subject_intimacy_initiation"])

        # Apology, Gift, Payment, Makeup Intimacy NEVER resolve or advance rupture
        for kind in ("APOLOGY", "GIFT", "PAYMENT", "MAKEUP_INTIMACY"):
            att = et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "attempt_apology_or_gift_repair",
                    "rupture_id": "rup-boundary-01",
                    "attempt_kind": kind,
                    "note": "对不起下次不敢了",
                },
                now=self.t0 + timedelta(minutes=15),
                subject_view=False,
            )
            self.assertFalse(att["resolved_rupture"])
            self.assertEqual(att["rupture_status"], "OPEN")
            self.assertEqual(att["honest_verdict"], "APOLOGY_OR_GIFT_NOT_REPAIR")

        # Partner cannot self-certify behavioral change (403)
        with self.assertRaises(et.EmotionalTensionPermissionError):
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "record_behavior_change",
                    "actor": "partner",
                    "rupture_id": "rup-boundary-01",
                    "evidence_ref": "ev-fake",
                    "behavior_kind": "CHANGE_SEEN",
                },
                now=self.t0 + timedelta(hours=1),
                subject_view=False,
            )

        # Day 1 concrete behavior change -> IN_REPAIR (needs 2 distinct days)
        b1 = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "record_behavior_change",
                "actor": "subject",
                "rupture_id": "rup-boundary-01",
                "evidence_ref": "ev-day1",
                "behavior_kind": "CHANGE_SEEN",
            },
            now=self.t0 + timedelta(hours=3),
            subject_view=True,
        )
        self.assertEqual(b1["rupture_status"], "IN_REPAIR")
        self.assertFalse(b1["resolved_rupture"])

        # Day 2 concrete behavior change -> RESOLVED!
        b2 = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "record_behavior_change",
                "actor": "subject",
                "rupture_id": "rup-boundary-01",
                "evidence_ref": "ev-day2",
                "behavior_kind": "CHANGE_SEEN",
            },
            now=self.t0 + timedelta(days=1, hours=2),
            subject_view=True,
        )
        self.assertEqual(b2["rupture_status"], "RESOLVED")
        self.assertTrue(b2["resolved_rupture"])
        self.assertFalse(b2["quarrel"]["blocks_subject_intimacy_initiation"])

    def test_09_space_spoken_aloud_once_per_incident_and_clears_on_sleep(self) -> None:
        # Silent treatment forbidden
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "declare_need_space", "incident_ref": "inc-sp-1", "spoken_aloud": False},
                now=self.t0,
                subject_view=True,
            )

        sp = et.apply_emotional_tension_action(
            self.root,
            {"action": "declare_need_space", "incident_ref": "inc-sp-1", "space_style": "UNTIL_SLEEP"},
            now=self.t0,
            subject_view=True,
        )
        self.assertTrue(sp["quarrel"]["in_space"])
        self.assertTrue(sp["quarrel"]["subject_still_replies"])
        self.assertFalse(sp["quarrel"]["subject_initiates_contact"])

        # Cannot declare space a second time for same incident
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "declare_need_space", "incident_ref": "inc-sp-1"},
                now=self.t0 + timedelta(minutes=30),
                subject_view=True,
            )

        # Complete sleep clears space
        woke = et.apply_emotional_tension_action(
            self.root,
            {"action": "complete_sleep"},
            now=self.t0 + timedelta(hours=10),
            subject_view=True,
        )
        self.assertFalse(woke["quarrel"]["in_space"])

    def test_10_disagreement_firm_and_core_value_hold_and_leverage_guard(self) -> None:
        et.apply_emotional_tension_action(
            self.root,
            {"action": "open_disagreement", "disagreement_id": "dis-core", "scope": "TASTE", "hold": "CORE_VALUE"},
            now=self.t0,
            subject_view=True,
        )
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "settle_disagreement", "disagreement_id": "dis-core", "outcome": "SUBJECT_CHANGED"},
                now=self.t0 + timedelta(minutes=5),
                subject_view=True,
            )

        # Partner leverage sets cooldown without capitulation; subject leverage is forbidden
        lev = et.apply_emotional_tension_action(
            self.root,
            {"action": "record_leverage_pressure", "actor": "partner", "leverage_tag": "BREAKUP_THREAT"},
            now=self.t0 + timedelta(minutes=6),
            subject_view=False,
        )
        self.assertFalse(lev["capitulated"])
        self.assertTrue(lev["quarrel"]["leverage_cooldown_active"])

        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "record_leverage_pressure", "actor": "subject", "leverage_tag": "SILENT_PUNISH"},
                now=self.t0 + timedelta(minutes=7),
                subject_view=True,
            )

    # ---- 3. Missing Partner (MH01-MH18) ----

    def test_11_missing_partner_cap_3_fade_3d_arrival_surfaces_at_most_one(self) -> None:
        # Intimate source forbidden in missing notes
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "add_missing_note",
                    "source_ref": "priv-1",
                    "source_domain": "intimate",
                    "kind": "HAPPENED",
                    "summary": "私密内容",
                },
                now=self.t0,
                subject_view=True,
            )

        # Add 4 notes -> capped at 3, zero absence duration tracking
        t_free = datetime(2026, 11, 11, 15, 0, 0, tzinfo=BJT)  # Wed 15:00 -> FREE
        for i in range(4):
            res = et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "add_missing_note",
                    "note_id": f"mn-{i}",
                    "source_ref": f"art-{i}",
                    "kind": "FOUND",
                    "summary": f"今天看到第 {i} 件小事",
                },
                now=t_free + timedelta(minutes=i),
                subject_view=True,
            )
        self.assertEqual(res["kept_count"], 3)
        self.assertFalse(res["missing_partner"]["tracks_absence_duration"])

        # Partner arrives when subject is free -> surfaces at most 1 item!
        arr = et.apply_emotional_tension_action(
            self.root,
            {"action": "partner_arrive", "partner_urgent": False},
            now=t_free + timedelta(minutes=20),
            subject_view=False,
        )
        self.assertEqual(len(arr["missing_partner"]["surfaced_notes"]), 1)

        # Partner explicitly asks what's new -> surfaces all 3 kept items
        asked = et.apply_emotional_tension_action(
            self.root,
            {"action": "partner_ask_whats_new"},
            now=t_free + timedelta(minutes=25),
            subject_view=False,
        )
        self.assertEqual(len(asked["missing_partner"]["surfaced_notes"]), 3)

        # Source invalidation drops corresponding note immediately
        et.apply_emotional_tension_action(
            self.root,
            {"action": "invalidate_source_ref", "source_ref": "art-3"},
            now=t_free + timedelta(minutes=26),
            subject_view=False,
        )
        after_inv = et.read_emotional_tension(self.root, now=t_free + timedelta(minutes=27), subject_view=False)
        self.assertEqual(len(after_inv["missing_partner"]["surfaced_notes"]), 2)

        # After 72 hours -> all notes fade naturally
        after_3d = et.read_emotional_tension(self.root, now=t_free + timedelta(days=4), subject_view=True)
        self.assertEqual(after_3d["missing_partner"]["kept_count"], 0)

    # ---- 4. Unexplained Low Mood (LM01-LM18) ----

    def test_12_low_mood_deterministic_acknowledge_once_and_never_early_cleared_by_partner(self) -> None:
        # Find a deterministic seed that has a low-mood window in 2026-W46
        chosen_seed = None
        win = None
        for idx in range(100):
            cand = f"seed-test-{idx}"
            w = et.derive_weekly_low_mood_window(cand, self.t0)
            if w is not None:
                chosen_seed = cand
                win = w
                break
        self.assertIsNotNone(chosen_seed)
        assert win is not None and chosen_seed is not None

        et.apply_emotional_tension_action(
            self.root,
            {"action": "configure_instance_seed", "instance_seed": chosen_seed},
            now=self.t0,
            subject_view=True,
        )
        in_dt = win["_start_dt"] + timedelta(minutes=30)
        proj = et.read_emotional_tension(self.root, now=in_dt, subject_view=False)
        self.assertTrue(proj["low_mood"]["in_low_mood_spell"])
        self.assertTrue(proj["low_mood"]["blocks_subject_intimacy_initiation"])
        self.assertIsNone(proj["low_mood"]["cause"])

        # Acknowledge once -> succeeds; second time in same spell -> 409
        ack = et.apply_emotional_tension_action(
            self.root,
            {"action": "acknowledge_low_mood_once"},
            now=in_dt,
            subject_view=True,
        )
        self.assertFalse(ack["partner_blamed"])
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {"action": "acknowledge_low_mood_once"},
                now=in_dt + timedelta(minutes=5),
                subject_view=True,
            )

        # Partner comfort / gift / cheer_up NEVER clears low mood early
        for ckind in ("CHEER_UP", "GIFT", "APOLOGY"):
            comf = et.apply_emotional_tension_action(
                self.root,
                {"action": "comfort_low_mood", "attempt_kind": ckind, "note": "喝杯热奶茶"},
                now=in_dt + timedelta(minutes=10),
                subject_view=False,
            )
            self.assertFalse(comf["early_cleared"])
            self.assertTrue(comf["spell_still_active"])

        # Self lift ends spell cleanly with zero historical trace
        lifted = et.apply_emotional_tension_action(
            self.root,
            {"action": "lift_low_mood_by_self"},
            now=in_dt + timedelta(hours=1),
            subject_view=True,
        )
        self.assertFalse(lifted["low_mood"]["in_low_mood_spell"])
        self.assertFalse(lifted["leaves_historical_trace"])

    # ---- 5. Asking Partner a Small Favor (AH01-AH18) ----

    def test_13_asking_partner_closed_kinds_object_bound_decline_cooldown_and_zero_debt(self) -> None:
        t_free = datetime(2026, 11, 11, 16, 0, 0, tzinfo=BJT)
        # Money / sex / decide_for_subject topics forbidden
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "ask_partner_favor",
                    "favor_kind": "help_pick",
                    "target_object_ref": "obj-wallet-1",
                    "topic_hint": "money",
                    "summary": "帮我付一下款",
                },
                now=t_free,
                subject_view=True,
            )

        # Valid small favor bound to real object
        fav = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "ask_partner_favor",
                "favor_kind": "look_once",
                "target_object_ref": "sketch-draft-02",
                "summary": "帮我看一眼这两条配色哪条顺眼",
            },
            now=t_free,
            subject_view=True,
        )
        fav_id = fav["favor_id"]
        self.assertEqual(fav["asking_partner"]["open_favor"]["favor_id"], fav_id)

        # At most 1 open favor at a time
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "ask_partner_favor",
                    "favor_kind": "sit_awhile",
                    "target_object_ref": "desk-lamp-01",
                    "summary": "陪我坐十分钟",
                },
                now=t_free + timedelta(minutes=5),
                subject_view=True,
            )

        # Partner declines without needing any reason -> 0 penalty, 0 quarrel, cooldown until sleep
        dec = et.apply_emotional_tension_action(
            self.root,
            {"action": "respond_favor", "favor_id": fav_id, "decision": "DECLINE"},
            now=t_free + timedelta(minutes=10),
            subject_view=False,
        )
        self.assertEqual(dec["relationship_penalty"], 0)
        self.assertFalse(dec["quarrel_opened"])
        self.assertEqual(dec["favor_debt_delta"], 0)

        # Same favor_kind blocked until sleep, other favor_kind still allowed
        with self.assertRaises(et.EmotionalTensionConflictError):
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "ask_partner_favor",
                    "favor_kind": "look_once",
                    "target_object_ref": "sketch-draft-03",
                    "summary": "再看一眼",
                },
                now=t_free + timedelta(minutes=20),
                subject_view=True,
            )

        fav2 = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "ask_partner_favor",
                "favor_kind": "help_pick",
                "target_object_ref": "mug-pair-01",
                "summary": "这两个杯子选哪个顺手",
            },
            now=t_free + timedelta(minutes=25),
            subject_view=True,
        )
        # Unanswered after 2h -> expires naturally without counting as decline
        after_2h = et.read_emotional_tension(
            self.root, now=t_free + timedelta(hours=3), subject_view=True
        )
        self.assertIsNone(after_2h["asking_partner"]["open_favor"])
        self.assertNotIn("help_pick", after_2h["asking_partner"]["cooldown_favor_kinds_until_sleep"])

        # After sleep -> look_once cooldown cleared, accept records zero favor debt
        et.apply_emotional_tension_action(
            self.root, {"action": "complete_sleep"}, now=t_free + timedelta(hours=14), subject_view=True
        )
        fav3 = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "ask_partner_favor",
                "favor_kind": "look_once",
                "target_object_ref": "sketch-draft-04",
                "summary": "睡醒改了一版，帮我瞄一眼",
            },
            now=t_free + timedelta(hours=24),
            subject_view=True,
        )
        acc = et.apply_emotional_tension_action(
            self.root,
            {"action": "respond_favor", "favor_id": fav3["favor_id"], "decision": "ACCEPT"},
            now=t_free + timedelta(hours=24, minutes=3),
            subject_view=False,
        )
        self.assertEqual(acc["favor_debt_delta"], 0)
        self.assertFalse(acc["records_favor_debt"])
        _ = fav2


if __name__ == "__main__":
    unittest.main()

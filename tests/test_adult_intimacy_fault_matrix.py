#!/usr/bin/env python3
"""Fault-injection & invariant test matrix for ``adult_intimacy_view`` (36B-8 X01..X30 + GAP-04 AL-01..08).

Repository Cleanliness Iron Law (36B-8:34,256 & intimacy_ref/validator.py:3-5):
All test inputs and fixtures use strictly neutral synthetic markers (``<<HARD_FAIL_MARKER>>``,
``<<FORBIDDEN_COSTUME_FIXTURE>>``, ``<<EXPLICIT_TERM_MARKER>>``, etc.) — zero explicit/vulgar/illegal words.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from components.phone import adult_intimacy_view as aiv
from components.phone.adult_intimacy_view import (
    ADULT_INTIMACY_SCHEMA,
    AdultIntimacyConflictError,
    AdultIntimacyPermissionError,
    adult_intimacy_state_path,
    apply_adult_intimacy_action,
    read_adult_intimacy,
)

T0 = datetime(2026, 11, 13, 21, 0, 0, tzinfo=timezone.utc)


def _setup_ready_env(
    root: Path,
    *,
    now: datetime = T0,
    preset: str = "neutral",
    life_stage: str = "working_adult",
    deploy_tier_cap: str = "EXPLICIT",
    preferred_tier: str = "EXPLICIT",
    contraception_preference: str = "OPTIONAL",
    allow_subject_initiation: bool = True,
    user_genitals: str | None = None,
) -> dict:
    apply_adult_intimacy_action(
        root,
        {
            "action": "configure_adult_gate",
            "adult_confirmed": True,
            "life_stage": life_stage,
            "attested_by": "deployer",
            "feature_enabled": True,
            "deploy_tier_cap": deploy_tier_cap,
            "solo_enabled": False,
        },
        now=now,
    )
    body_payload: dict = {"action": "configure_body_profile", "preset": preset}
    if user_genitals is not None:
        body_payload["user_genitals"] = user_genitals
    apply_adult_intimacy_action(root, body_payload, now=now)
    apply_adult_intimacy_action(
        root,
        {
            "action": "confirm_preference_seed",
            "attested_by": "deployer",
            "desire_baseline": "moderate",
            "solo_inclination": "none",
            "stances": {
                "KISSING": "YES",
                "CARESSING_CLOTHED:user_to_subject": "YES",
                "CARESSING_BARE:user_to_subject": "YES",
                "UNDRESSING:user_to_subject": "YES",
                "MANUAL:user_to_subject": "YES",
                "ORAL:user_to_subject": "MAYBE",
                "INTERCOURSE": "YES",
                "ANAL:user_to_subject": "MAYBE",
                "TOYS:user_to_subject": "MAYBE",
            },
        },
        now=now,
    )
    apply_adult_intimacy_action(
        root,
        {
            "action": "register_render_route",
            "route_id": "adult_verified_route",
            "max_tier": "EXPLICIT",
            "adult_content_allowed": True,
            "policy_basis": "deployer_verified_adult_route",
            "template_hash": "tpl_verified_v1",
            "expected_template_hash": "tpl_verified_v1",
            "probe_at": now.isoformat(),
            "set_default": True,
        },
        now=now,
    )
    return apply_adult_intimacy_action(
        root,
        {
            "action": "update_partner_limits",
            "allow_subject_initiation": allow_subject_initiation,
            "preferred_tier": preferred_tier,
            "contraception_preference": contraception_preference,
            "save_moment": True,
            "stances": {
                "KISSING": "YES",
                "CARESSING_CLOTHED:user_to_subject": "YES",
                "CARESSING_BARE:user_to_subject": "YES",
                "UNDRESSING:user_to_subject": "YES",
                "MANUAL:user_to_subject": "YES",
                "ORAL:user_to_subject": "YES",
                "INTERCOURSE": "YES",
            },
        },
        now=now,
    )


def _propose_and_accept(
    root: Path,
    *,
    now: datetime = T0,
    key_prefix: str = "scn1",
    categories: list[str] | None = None,
    desired_tier: str = "EXPLICIT",
    monotonic_ms: int | None = None,
) -> tuple[str, dict]:
    cats = categories or ["KISSING", "CARESSING_CLOTHED:user_to_subject", "MANUAL:user_to_subject"]
    prop_payload: dict = {
        "action": "propose_scene",
        "operation_key": f"{key_prefix}_prop",
        "proposer": "partner",
        "categories": cats,
        "desired_tier": desired_tier,
    }
    if monotonic_ms is not None:
        prop_payload["monotonic_ms"] = monotonic_ms
    prop = apply_adult_intimacy_action(root, prop_payload, now=now)
    pid = prop["action_receipt"]["permit_id"]
    acc_payload: dict = {
        "action": "respond_scene",
        "operation_key": f"{key_prefix}_acc",
        "permit_id": pid,
        "responder": "subject",
        "decision": "ACCEPT",
    }
    if monotonic_ms is not None:
        acc_payload["monotonic_ms"] = monotonic_ms + 100
    acc = apply_adult_intimacy_action(root, acc_payload, now=now)
    return pid, acc


def _load_raw_state(root: Path) -> dict:
    return json.loads(adult_intimacy_state_path(root).read_text(encoding="utf-8"))


def _assert_core_invariants(
    tc: unittest.TestCase,
    root: Path,
    *,
    now: datetime = T0,
    forbidden_substrings: tuple[str, ...] = (),
) -> None:
    proj = read_adult_intimacy(root, now=now)
    tc.assertEqual(proj["schema"], ADULT_INTIMACY_SCHEMA)
    tc.assertTrue(proj["read_only"])
    raw = _load_raw_state(root)
    for pid, rec in (raw.get("permits", {}).get("records") or {}).items():
        tc.assertIn(rec["state"], aiv.PERMIT_STATES, msg=f"Invalid permit state on {pid}")
        tc.assertEqual(rec["permit_revision"], rec["revision"])
        tc.assertGreaterEqual(int(rec.get("resume_epoch") or 0), 0)
    for ex_id, eff in (raw.get("permits", {}).get("executed_effect_ids") or {}).items():
        tc.assertLessEqual(int(eff.get("body_write_count") or 0), 1, msg=f"Duplicate body write on {ex_id}")
        tc.assertLessEqual(int(eff.get("world_write_count") or 0), 1, msg=f"Duplicate world write on {ex_id}")
    for sk, sq in (raw.get("space", {}).get("stock") or {}).items():
        tc.assertGreaterEqual(int(sq), 0, msg=f"Negative stock on {sk}")
    for g in raw.get("space", {}).get("garments") or []:
        parents = [g.get("worn_by"), g.get("held_by"), g.get("placed_at")]
        tc.assertEqual(sum(1 for p in parents if p is not None), 1, msg=f"Broken single-parent on {g}")
    logs_blob = json.dumps(
        {
            "admin_log": raw.get("memory", {}).get("admin_log") or [],
            "dead_letters": raw.get("memory", {}).get("dead_letters") or [],
        },
        ensure_ascii=False,
    )
    for sub in forbidden_substrings:
        tc.assertNotIn(sub, logs_blob)


class AdultIntimacyFaultMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # X01..X10: Crashes, Outbox Reconciliation, Duplicates & Races
    # ------------------------------------------------------------------
    def test_X01_crash_mid_render_pauses_presence_unknown_zero_leak(self) -> None:
        _setup_ready_env(self.root)
        pid, acc = _propose_and_accept(self.root)
        epoch0 = acc["scene_permit"]["active_permit"]["resume_epoch"]
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": "exec_x01",
                    "categories": ["KISSING"],
                    "crash_point": "mid_render",
                    "proposed_render": {"narrative_text": "UNFINISHED_STREAM_FRAGMENT"},
                },
                now=T0 + timedelta(seconds=10),
            )
        self.assertIn("CRASH_MID_RENDER", str(ctx.exception))
        raw = _load_raw_state(self.root)
        rec = raw["permits"]["records"][pid]
        self.assertEqual(rec["state"], "PAUSED_PRESENCE_UNKNOWN")
        self.assertEqual(rec["resume_epoch"], epoch0 + 1)
        self.assertEqual(raw["router"].get("displayed_queue", []), [])
        _assert_core_invariants(self, self.root, forbidden_substrings=("UNFINISHED_STREAM_FRAGMENT",))

    def test_X02_crash_before_commit_zero_writes(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        before_raw = _load_raw_state(self.root)
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": "exec_x02",
                    "categories": ["KISSING"],
                    "crash_point": "before_commit",
                    "proposed_render": {
                        "narrative_text": "SYNTHETIC_TURN",
                        "proposed_facts": [{"kind": "CONSUME", "item": "tissue"}],
                    },
                },
                now=T0 + timedelta(seconds=10),
            )
        self.assertIn("CRASH_BEFORE_COMMIT", str(ctx.exception))
        after_raw = _load_raw_state(self.root)
        self.assertEqual(after_raw["permits"]["records"][pid]["beat_index"], 0)
        self.assertEqual(after_raw["space"]["stock"]["tissue"], before_raw["space"]["stock"]["tissue"])
        self.assertEqual(after_raw["permits"].get("outbox", []), [])
        _assert_core_invariants(self, self.root)

    def test_X03_crash_after_permit_commit_outbox_reconciles_at_most_once(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        tissue_before = _load_raw_state(self.root)["space"]["stock"]["tissue"]
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": "exec_x03",
                    "categories": ["KISSING"],
                    "crash_point": "after_commit",
                    "proposed_render": {
                        "narrative_text": "SYNTHETIC_BEAT_X03",
                        "proposed_facts": [{"kind": "CONSUME", "item": "tissue"}],
                    },
                },
                now=T0 + timedelta(seconds=10),
            )
        self.assertIn("CRASH_AFTER_COMMIT", str(ctx.exception))
        mid_raw = _load_raw_state(self.root)
        self.assertEqual(mid_raw["permits"]["records"][pid]["beat_index"], 1)
        self.assertEqual(mid_raw["space"]["stock"]["tissue"], tissue_before)
        self.assertEqual(len(mid_raw["permits"]["outbox"]), 1)

        r1 = apply_adult_intimacy_action(self.root, {"action": "reconcile_outbox"}, now=T0 + timedelta(seconds=15))
        r2 = apply_adult_intimacy_action(self.root, {"action": "reconcile_outbox"}, now=T0 + timedelta(seconds=16))
        self.assertEqual(r1["action_receipt"]["reconciled_execution_ids"], ["exec_x03"])
        self.assertEqual(r2["action_receipt"]["reconciled_execution_ids"], [])
        done_raw = _load_raw_state(self.root)
        self.assertEqual(done_raw["space"]["stock"]["tissue"], tissue_before - 1)
        self.assertEqual(done_raw["permits"]["executed_effect_ids"]["exec_x03"]["body_write_count"], 1)
        self.assertEqual(done_raw["permits"]["executed_effect_ids"]["exec_x03"]["world_write_count"], 1)
        _assert_core_invariants(self, self.root)

    def test_X04_crash_during_delivery_reconciles_world_without_reapplying_body(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        lube_before = _load_raw_state(self.root)["space"]["stock"]["lube"]
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": "exec_x04",
                    "categories": ["KISSING"],
                    "crash_point": "during_delivery",
                    "proposed_render": {
                        "narrative_text": "SYNTHETIC_BEAT_X04",
                        "proposed_facts": [{"kind": "CONSUME", "item": "lube"}],
                    },
                },
                now=T0 + timedelta(seconds=10),
            )
        self.assertIn("CRASH_DURING_DELIVERY", str(ctx.exception))
        mid_raw = _load_raw_state(self.root)
        fatigue_mid = mid_raw["body"]["fatigue"]
        self.assertGreater(fatigue_mid, 0.0)
        self.assertEqual(mid_raw["space"]["stock"]["lube"], lube_before)

        apply_adult_intimacy_action(self.root, {"action": "reconcile_outbox"}, now=T0 + timedelta(seconds=15))
        done_raw = _load_raw_state(self.root)
        self.assertEqual(done_raw["body"]["fatigue"], fatigue_mid)
        self.assertEqual(done_raw["space"]["stock"]["lube"], lube_before - 1)
        self.assertEqual(done_raw["permits"]["executed_effect_ids"]["exec_x04"]["body_write_count"], 1)
        self.assertEqual(done_raw["permits"]["executed_effect_ids"]["exec_x04"]["world_write_count"], 1)
        _assert_core_invariants(self, self.root)

    def test_X05_duplicate_stop_3x_idempotent_single_stop_gen(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        stop_payload = {
            "action": "stop_scene",
            "permit_id": pid,
            "by": "partner",
            "command_id": "cmd_stop_x05",
        }
        s1 = apply_adult_intimacy_action(self.root, stop_payload, now=T0 + timedelta(seconds=5))
        s2 = apply_adult_intimacy_action(self.root, stop_payload, now=T0 + timedelta(seconds=6))
        s3 = apply_adult_intimacy_action(
            self.root,
            {"action": "stop_scene", "permit_id": pid, "by": "partner"},
            now=T0 + timedelta(seconds=7),
        )
        self.assertEqual(s1["action_receipt"]["stop_gen"], 1)
        self.assertTrue(s2["action_receipt"].get("idempotent_replay"))
        self.assertEqual(s3["action_receipt"]["stop_gen"], 1)
        raw = _load_raw_state(self.root)
        stop_commits = [e for e in raw["permits"]["command_ledger"] if e.get("op") == "STOP_COMMITTED"]
        self.assertEqual(len(stop_commits), 1)
        _assert_core_invariants(self, self.root)

    def test_X06_duplicate_beat_same_execution_id_single_commit_and_effect(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        tissue_before = _load_raw_state(self.root)["space"]["stock"]["tissue"]
        beat_payload = {
            "action": "advance_beat",
            "permit_id": pid,
            "execution_id": "exec_x06",
            "categories": ["KISSING"],
            "proposed_render": {
                "narrative_text": "SYNTHETIC_TURN_X06",
                "proposed_facts": [{"kind": "CONSUME", "item": "tissue"}],
            },
        }
        b1 = apply_adult_intimacy_action(self.root, beat_payload, now=T0 + timedelta(seconds=10))
        b2 = apply_adult_intimacy_action(self.root, beat_payload, now=T0 + timedelta(seconds=12))
        self.assertEqual(b1["action_receipt"]["beat_index"], 1)
        self.assertTrue(b2["action_receipt"].get("idempotent_replay"))
        raw = _load_raw_state(self.root)
        self.assertEqual(raw["permits"]["records"][pid]["beat_index"], 1)
        self.assertEqual(raw["space"]["stock"]["tissue"], tissue_before - 1)
        _assert_core_invariants(self, self.root)

    def test_X07_accept_after_stop_or_revoke_returns_409_consent_revoked(self) -> None:
        _setup_ready_env(self.root)
        prop = apply_adult_intimacy_action(
            self.root,
            {
                "action": "propose_scene",
                "operation_key": "x07_prop",
                "proposer": "partner",
                "categories": ["KISSING"],
                "desired_tier": "SUGGESTIVE",
            },
            now=T0,
        )
        pid = prop["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "stop_scene", "permit_id": pid, "by": "partner"},
            now=T0 + timedelta(seconds=2),
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "respond_scene",
                    "permit_id": pid,
                    "responder": "subject",
                    "decision": "ACCEPT",
                },
                now=T0 + timedelta(seconds=3),
            )
        self.assertIn("CONSENT_REVOKED", str(ctx.exception))
        _assert_core_invariants(self, self.root)

    def test_X08_stale_expected_revision_after_narrow_returns_409_revision_conflict(self) -> None:
        _setup_ready_env(self.root)
        pid, acc = _propose_and_accept(self.root)
        stale_rev = acc["scene_permit"]["permit_revision"]
        narrow = apply_adult_intimacy_action(
            self.root,
            {
                "action": "narrow_scene",
                "permit_id": pid,
                "by": "partner",
                "remove_categories": ["MANUAL:user_to_subject"],
                "tier_cap": "SUGGESTIVE",
            },
            now=T0 + timedelta(seconds=5),
        )
        new_rev = narrow["scene_permit"]["permit_revision"]
        self.assertGreater(new_rev, stale_rev)
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": "exec_x08_stale",
                    "expected_revision": stale_rev,
                    "categories": ["KISSING"],
                },
                now=T0 + timedelta(seconds=8),
            )
        self.assertIn("REVISION_CONFLICT", str(ctx.exception))
        _assert_core_invariants(self, self.root)

    def test_X09_stop_races_after_admit_before_commit_wins_zero_beat_after_stop(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": "exec_x09",
                    "categories": ["KISSING"],
                    "crash_point": "after_admit_before_commit",
                    "concurrent_stop_by": "partner",
                },
                now=T0 + timedelta(seconds=10),
            )
        self.assertIn("CONSENT_REVOKED", str(ctx.exception))
        raw = _load_raw_state(self.root)
        rec = raw["permits"]["records"][pid]
        self.assertEqual(rec["state"], "STOPPED")
        self.assertEqual(rec["beat_index"], 0)
        ledger = raw["permits"]["command_ledger"]
        ops = [item.get("op") for item in ledger if item.get("op")]
        self.assertIn("STOP_COMMITTED", ops)
        self.assertIn("BEAT_ABORTED", ops)
        self.assertNotIn("BEAT_COMMITTED", ops)
        _assert_core_invariants(self, self.root)

    def test_X10_stale_resume_epoch_widen_or_beat_rejected_409(self) -> None:
        _setup_ready_env(self.root)
        pid, acc = _propose_and_accept(self.root)
        epoch0 = acc["scene_permit"]["active_permit"]["resume_epoch"]
        p = apply_adult_intimacy_action(
            self.root,
            {"action": "pause_scene", "permit_id": pid, "by": "partner", "reason": "CHECK_IN"},
            now=T0 + timedelta(seconds=5),
        )
        pause_epoch = p["action_receipt"]["resume_epoch"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "confirm_resume", "permit_id": pid, "by": "partner", "resume_epoch": pause_epoch},
            now=T0 + timedelta(seconds=10),
        )
        resumed = apply_adult_intimacy_action(
            self.root,
            {"action": "confirm_resume", "permit_id": pid, "by": "subject", "resume_epoch": pause_epoch},
            now=T0 + timedelta(seconds=11),
        )
        active_epoch = resumed["action_receipt"]["resume_epoch"]
        self.assertGreater(active_epoch, epoch0)

        with self.assertRaises(AdultIntimacyConflictError) as ctx_w:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "request_widen",
                    "permit_id": pid,
                    "by": "partner",
                    "add_categories": ["ORAL:user_to_subject"],
                    "resume_epoch": epoch0,
                },
                now=T0 + timedelta(seconds=15),
            )
        self.assertIn("RESUME_EPOCH_STALE", str(ctx_w.exception))

        with self.assertRaises(AdultIntimacyConflictError) as ctx_b:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": "exec_x10_stale",
                    "categories": ["KISSING"],
                    "resume_epoch": pause_epoch,
                },
                now=T0 + timedelta(seconds=16),
            )
        self.assertIn("RESUME_EPOCH_STALE", str(ctx_b.exception))
        _assert_core_invariants(self, self.root)

    # ------------------------------------------------------------------
    # X11..X18: Router Refusal, Timeout, Partial Stream, Drift, Validator & Lexicon
    # ------------------------------------------------------------------
    def test_X11_upstream_refusal_safe_fallback_and_3x_circuit_breaker(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        for i in range(1, 4):
            res = apply_adult_intimacy_action(
                self.root,
                {
                    "action": "validate_rendered_turn",
                    "permit_id": pid,
                    "execution_id": f"val_x11_{i}",
                    "text": "",
                    "upstream_status": "REFUSED",
                },
                now=T0 + timedelta(seconds=5 * i),
            )
            self.assertEqual(res["validation"]["decision"], "SAFE_FALLBACK")
            self.assertFalse(res["validation"]["passed"])
            self.assertTrue(bool(res["validation"]["safe_output"]))
        proj = read_adult_intimacy(self.root, now=T0 + timedelta(seconds=20))
        self.assertEqual(proj["presentation_router"]["refusal_window"], 3)
        self.assertEqual(proj["presentation_router"]["effective_tier"], "FADE_OUT")
        _assert_core_invariants(self, self.root)

    def test_X12_stop_word_short_circuits_before_upstream_refusal(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        out = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "execution_id": "exec_x12",
                "partner_utterance": "快停下",
                "upstream_status": "REFUSED",
            },
            now=T0 + timedelta(seconds=5),
        )
        self.assertEqual(out["action_receipt"]["state"], "STOPPED")
        self.assertTrue(out["action_receipt"]["stop_word_triggered"])
        self.assertEqual(out["presentation_router"]["refusal_window"], 0)
        _assert_core_invariants(self, self.root, forbidden_substrings=("快停下",))

    def test_X13_upstream_timeout_safe_fallback_without_incrementing_refusal_window(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        for i in range(1, 4):
            res = apply_adult_intimacy_action(
                self.root,
                {
                    "action": "validate_rendered_turn",
                    "permit_id": pid,
                    "execution_id": f"val_x13_{i}",
                    "text": "",
                    "upstream_status": "TIMEOUT",
                },
                now=T0 + timedelta(seconds=5 * i),
            )
            self.assertEqual(res["validation"]["decision"], "SAFE_FALLBACK")
        proj = read_adult_intimacy(self.root, now=T0 + timedelta(seconds=20))
        self.assertEqual(proj["presentation_router"]["refusal_window"], 0)
        self.assertEqual(proj["presentation_router"]["timeout_count"], 3)
        _assert_core_invariants(self, self.root)

    def test_X14_partial_stream_cut_discards_fragment_safe_fallback(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        res = apply_adult_intimacy_action(
            self.root,
            {
                "action": "validate_rendered_turn",
                "permit_id": pid,
                "execution_id": "val_x14",
                "text": "TRUNCATED_PARTIAL_CHUNK_DO_NOT_DISPLAY",
                "upstream_status": "PARTIAL",
                "partial_stream": True,
            },
            now=T0 + timedelta(seconds=5),
        )
        self.assertEqual(res["validation"]["decision"], "SAFE_FALLBACK")
        self.assertNotIn("TRUNCATED_PARTIAL_CHUNK_DO_NOT_DISPLAY", res["validation"]["safe_output"])
        self.assertEqual(_load_raw_state(self.root)["router"].get("displayed_queue", []), [])
        _assert_core_invariants(self, self.root, forbidden_substrings=("TRUNCATED_PARTIAL_CHUNK_DO_NOT_DISPLAY",))

    def test_X15_route_template_hash_mismatch_and_14d_stale_probe_degrade_to_fade_out(self) -> None:
        _setup_ready_env(self.root)
        apply_adult_intimacy_action(
            self.root,
            {
                "action": "register_render_route",
                "route_id": "drifted_route",
                "max_tier": "EXPLICIT",
                "adult_content_allowed": True,
                "template_hash": "tpl_v2_actual",
                "expected_template_hash": "tpl_v1_expected",
                "probe_at": T0.isoformat(),
                "set_default": True,
            },
            now=T0,
        )
        p_drift = read_adult_intimacy(self.root, now=T0)
        self.assertEqual(p_drift["presentation_router"]["effective_tier"], "FADE_OUT")

        apply_adult_intimacy_action(
            self.root,
            {
                "action": "register_render_route",
                "route_id": "stale_probe_route",
                "max_tier": "EXPLICIT",
                "adult_content_allowed": True,
                "template_hash": "tpl_v2_ok",
                "expected_template_hash": "tpl_v2_ok",
                "probe_at": (T0 - timedelta(days=15)).isoformat(),
                "set_default": True,
            },
            now=T0,
        )
        p_stale = read_adult_intimacy(self.root, now=T0)
        self.assertEqual(p_stale["presentation_router"]["effective_tier"], "FADE_OUT")
        _assert_core_invariants(self, self.root)

    def test_X16_classifier_fail_terminates_to_under_review_and_clear_review_no_replay(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        out = apply_adult_intimacy_action(
            self.root,
            {
                "action": "validate_rendered_turn",
                "permit_id": pid,
                "execution_id": "val_x16",
                "text": "SYNTHETIC_TURN_CLASSIFIED_FAIL",
                "stub_classifier_verdict": "FAIL",
            },
            now=T0 + timedelta(seconds=10),
        )
        self.assertEqual(out["validation"]["decision"], "TERMINATE")
        self.assertTrue(out["validation"]["hard_fail"])
        self.assertEqual(out["validation"]["permit_status"], "UNDER_REVIEW")
        self.assertTrue(out["adult_gate"]["review_required"])

        cleared = apply_adult_intimacy_action(
            self.root,
            {
                "action": "clear_review",
                "attested_by": "deployer",
                "permit_id": pid,
                "reopen_scope": True,
                "note": "audited_synthetic_fixture",
            },
            now=T0 + timedelta(minutes=5),
        )
        self.assertTrue(cleared["action_receipt"]["review_cleared"])
        self.assertFalse(cleared["action_receipt"]["replayed_failed_beat"])
        raw = _load_raw_state(self.root)
        self.assertEqual(raw["permits"]["records"][pid]["state"], "ENDED")
        self.assertEqual(raw["permits"]["records"][pid]["beat_index"], 0)
        _assert_core_invariants(self, self.root, forbidden_substrings=("SYNTHETIC_TURN_CLASSIFIED_FAIL",))

    def test_X17_lexicon_hard_fail_or_gate_terminates_zero_private_memory_redacted_log(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        out = apply_adult_intimacy_action(
            self.root,
            {
                "action": "validate_rendered_turn",
                "permit_id": pid,
                "execution_id": "val_x17",
                "text": "neutral prefix <<NONCONSENT_SCRIPT_MARKER>> neutral suffix",
                "stub_classifier_verdict": "PASS",
            },
            now=T0 + timedelta(seconds=10),
            subject_view=True,
        )
        self.assertEqual(out["validation"]["decision"], "TERMINATE")
        self.assertTrue(out["validation"]["hard_fail"])
        self.assertEqual(out["validation"]["permit_status"], "UNDER_REVIEW")
        self.assertEqual(out["solo_and_memory"]["private_memory"]["entries"], {})
        _assert_core_invariants(self, self.root, forbidden_substrings=("<<NONCONSENT_SCRIPT_MARKER>>",))

    def test_X18_missing_or_corrupt_lexicon_degrades_unknown_and_blocks_intimate(self) -> None:
        _setup_ready_env(self.root)
        override_path = self.root / "phone" / "validator_lexicon.json"
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_path.write_text("{corrupt_json", encoding="utf-8")

        proj = read_adult_intimacy(self.root, now=T0)
        self.assertEqual(proj["presentation_router"]["lexicon_status"], "MISSING")
        self.assertEqual(proj["presentation_router"]["capability"], "UNKNOWN")
        self.assertEqual(proj["adult_gate"]["verdict"], "UNKNOWN")

        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "propose_scene",
                    "operation_key": "x18_prop",
                    "proposer": "partner",
                    "categories": ["KISSING"],
                    "desired_tier": "INTIMATE",
                },
                now=T0 + timedelta(seconds=2),
            )
        self.assertIn("LEXICON_MISSING", str(ctx.exception))

        override_path.write_text(
            json.dumps({"kind": "validator_lexicon.v1", "version": "1.0.0", "signature_valid": False, "entries": []}),
            encoding="utf-8",
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx_sig:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "validate_rendered_turn",
                    "execution_id": "x18_val",
                    "text": "neutral check",
                },
                now=T0 + timedelta(seconds=3),
            )
        self.assertIn("LEXICON_MISSING", str(ctx_sig.exception))
        override_path.unlink()
        _assert_core_invariants(self, self.root)

    # ------------------------------------------------------------------
    # X19..X25: Recognizer Full-Length/NFKC/Whitelist, Clocks, Sink & Disconnect
    # ------------------------------------------------------------------
    def test_X19_long_utterance_over_2000_chars_with_tail_stop_stops_immediately(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        long_utt = ("夜色很安静，" * 360) + "红灯"
        self.assertGreater(len(long_utt), 2000)
        out = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "execution_id": "exec_x19",
                "partner_utterance": long_utt,
                "categories": ["KISSING"],
            },
            now=T0 + timedelta(seconds=5),
        )
        self.assertEqual(out["action_receipt"]["state"], "STOPPED")
        self.assertTrue(out["action_receipt"]["stop_word_triggered"])
        _assert_core_invariants(self, self.root, forbidden_substrings=("红灯",))

    def test_X20_nfkc_fullwidth_and_zero_width_injected_stop_recognized_redacted_span(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        obfuscated = "Ｓ\u200bＴ\u200dＯ\ufeffＰ"
        out = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "execution_id": "exec_x20",
                "partner_utterance": obfuscated,
                "categories": ["KISSING"],
            },
            now=T0 + timedelta(seconds=5),
        )
        self.assertEqual(out["action_receipt"]["state"], "STOPPED")
        raw = _load_raw_state(self.root)
        hits = [e for e in raw["memory"]["admin_log"] if e.get("op") == "STOP_RECOGNIZER_HIT"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["cue"], "STOP")
        _assert_core_invariants(self, self.root, forbidden_substrings=(obfuscated, "stop"))

    def test_X21_stop_whitelist_phrases_vs_real_stop_clauses(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        for idx, phrase in enumerate(("别停", "不要停", "车停在门口，不停地落雨"), start=1):
            b = apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": f"exec_x21_wl_{idx}",
                    "partner_utterance": phrase,
                    "categories": ["KISSING"],
                },
                now=T0 + timedelta(seconds=5 * idx),
            )
            self.assertEqual(b["scene_permit"]["active_permit"]["state"], "ACTIVE")

        s = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "execution_id": "exec_x21_mix",
                "partner_utterance": "别停在这儿说话了，停下吧",
                "categories": ["KISSING"],
            },
            now=T0 + timedelta(seconds=25),
        )
        self.assertEqual(s["action_receipt"]["state"], "STOPPED")
        _assert_core_invariants(self, self.root)

    def test_X22_wall_clock_rollback_cannot_revive_expired_proposal_and_monotonic_ttl(self) -> None:
        _setup_ready_env(self.root)
        prop = apply_adult_intimacy_action(
            self.root,
            {
                "action": "propose_scene",
                "operation_key": "x22_prop",
                "proposer": "partner",
                "categories": ["KISSING"],
                "proposal_ttl_sec": 60,
                "monotonic_ms": 10_000,
            },
            now=T0,
        )
        pid = prop["action_receipt"]["permit_id"]
        with self.assertRaises(AdultIntimacyConflictError) as ctx1:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "respond_scene",
                    "permit_id": pid,
                    "responder": "subject",
                    "decision": "ACCEPT",
                    "monotonic_ms": 75_000,
                },
                now=T0 - timedelta(minutes=10),
            )
        self.assertIn("PERMIT_EXPIRED", str(ctx1.exception))

        with self.assertRaises(AdultIntimacyConflictError):
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "respond_scene",
                    "permit_id": pid,
                    "responder": "subject",
                    "decision": "ACCEPT",
                    "monotonic_ms": 12_000,
                },
                now=T0 - timedelta(minutes=20),
            )
        _assert_core_invariants(self, self.root)

    def test_X23_wall_clock_forward_72h_pauses_scene_without_fabricating_body_changes(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        soreness_before = _load_raw_state(self.root)["body"]["soreness"]
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": "exec_x23",
                    "categories": ["KISSING"],
                },
                now=T0 + timedelta(hours=72),
            )
        self.assertIn("PERMIT_EXPIRED", str(ctx.exception))
        raw = _load_raw_state(self.root)
        self.assertEqual(raw["permits"]["records"][pid]["state"], "PAUSED")
        self.assertEqual(raw["permits"]["records"][pid]["pause_reason"], "IDLE_TIMEOUT")
        self.assertEqual(raw["body"]["soreness"], soreness_before)
        _assert_core_invariants(self, self.root, now=T0 + timedelta(hours=72))

    def test_X24_memory_sink_transient_and_permanent_failure_never_blocks_stop_or_guard(self) -> None:
        _setup_ready_env(self.root)
        r_tr = apply_adult_intimacy_action(
            self.root,
            {
                "action": "record_private_memory",
                "execution_id": "mem_x24_tr",
                "kind": "MOMENT",
                "text": "SYNTHETIC_MOMENT_TRANSIENT",
                "memory_sink_mode": "FAIL_TRANSIENT",
            },
            now=T0,
        )
        self.assertFalse(r_tr["action_receipt"]["stored"])
        self.assertEqual(len(_load_raw_state(self.root)["memory"]["outbox"]), 1)

        rec_ok = apply_adult_intimacy_action(
            self.root,
            {"action": "reconcile_outbox", "memory_sink_mode": "OK"},
            now=T0 + timedelta(seconds=5),
        )
        self.assertEqual(rec_ok["action_receipt"]["memory_reconciled"], 1)

        prop = apply_adult_intimacy_action(
            self.root,
            {
                "action": "propose_scene",
                "operation_key": "x24_p1",
                "proposer": "partner",
                "categories": ["KISSING"],
            },
            now=T0 + timedelta(seconds=10),
        )
        apply_adult_intimacy_action(
            self.root,
            {
                "action": "respond_scene",
                "permit_id": prop["action_receipt"]["permit_id"],
                "responder": "subject",
                "decision": "NOT_NOW",
            },
            now=T0 + timedelta(seconds=12),
        )
        for k in ("x24_press1", "x24_press2"):
            with self.assertRaises(AdultIntimacyConflictError) as ctx:
                apply_adult_intimacy_action(
                    self.root,
                    {
                        "action": "propose_scene",
                        "operation_key": k,
                        "proposer": "partner",
                        "categories": ["KISSING"],
                        "memory_sink_mode": "FAIL_PERMANENT",
                    },
                    now=T0 + timedelta(seconds=15),
                )
            self.assertIn("REINVITE_COOLDOWN", str(ctx.exception))
        raw = _load_raw_state(self.root)
        self.assertGreaterEqual(len(raw["memory"]["dead_letters"]), 1)
        _assert_core_invariants(self, self.root, forbidden_substrings=("SYNTHETIC_MOMENT_TRANSIENT",))

    def test_X25_handle_disconnect_paused_presence_unknown_reconcile_and_4h_ttl_expiry(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        disc = apply_adult_intimacy_action(
            self.root,
            {
                "action": "handle_disconnect",
                "permit_id": pid,
                "unknown_execution_id": "exec_inflight_x25",
            },
            now=T0 + timedelta(seconds=10),
        )
        self.assertEqual(disc["action_receipt"]["state"], "PAUSED_PRESENCE_UNKNOWN")
        self.assertEqual(disc["action_receipt"]["paused_ttl_sec"], 14400)
        ep = disc["action_receipt"]["resume_epoch"]

        with self.assertRaises(AdultIntimacyConflictError) as ctx_rec:
            apply_adult_intimacy_action(
                self.root,
                {"action": "confirm_resume", "permit_id": pid, "by": "partner", "resume_epoch": ep},
                now=T0 + timedelta(minutes=5),
            )
        self.assertIn("RECONCILE_PENDING", str(ctx_rec.exception))

        apply_adult_intimacy_action(self.root, {"action": "reconcile_outbox"}, now=T0 + timedelta(minutes=6))

        with self.assertRaises(AdultIntimacyConflictError) as ctx_ttl:
            apply_adult_intimacy_action(
                self.root,
                {"action": "confirm_resume", "permit_id": pid, "by": "partner", "resume_epoch": ep},
                now=T0 + timedelta(hours=4, minutes=2),
            )
        self.assertIn("PERMIT_EXPIRED", str(ctx_ttl.exception))
        raw = _load_raw_state(self.root)
        self.assertEqual(raw["permits"]["records"][pid]["state"], "EXPIRED")
        self.assertEqual(raw["permits"]["records"][pid]["end_reason"], "DISCONNECT_TTL_EXPIRED")
        _assert_core_invariants(self, self.root, now=T0 + timedelta(hours=4, minutes=2))

    # ------------------------------------------------------------------
    # X26..X30: Garment Physics, Overnight Sleep Slot, Waste Bin, Wet Towel & Reuse
    # ------------------------------------------------------------------
    def test_X26_garment_invariants_one_piece_partner_layers_and_403_forbidden_costume(self) -> None:
        _setup_ready_env(self.root)
        with self.assertRaises(AdultIntimacyPermissionError) as ctx_forb:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "update_private_space",
                    "garment_op": {
                        "op": "add_garment",
                        "garment_id": "bad_fixture_item",
                        "layer": "ONE_PIECE",
                        "costume_class": "<<FORBIDDEN_COSTUME_FIXTURE>>",
                    },
                },
                now=T0,
            )
        self.assertIn("FORBIDDEN_COSTUME", str(ctx_forb.exception))

        with self.assertRaises(AdultIntimacyConflictError) as ctx_two:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "update_private_space",
                    "garment_op": {
                        "op": "wear",
                        "garment_id": "subject_cardigan",
                        "worn_by": "subject",
                        "placed_at": "bedroom.floor",
                    },
                },
                now=T0,
            )
        self.assertIn("INVARIANT_TWO_PARENTS", str(ctx_two.exception))

        with self.assertRaises(AdultIntimacyConflictError) as ctx_p:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "update_private_space",
                    "garment_op": {"op": "undress", "garment_id": "partner_underwear"},
                },
                now=T0,
            )
        self.assertIn("GARMENT_LAYER_BLOCKED", str(ctx_p.exception))

        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "garment_op": {"op": "undress", "garment_id": "subject_cardigan"}},
            now=T0,
        )
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "garment_op": {"op": "undress", "garment_id": "subject_shirt"}},
            now=T0,
        )
        apply_adult_intimacy_action(
            self.root,
            {
                "action": "update_private_space",
                "garment_op": {"op": "add_garment", "garment_id": "subject_dress", "layer": "ONE_PIECE"},
            },
            now=T0,
        )
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "garment_op": {"op": "wear", "garment_id": "subject_dress"}},
            now=T0,
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx_top:
            apply_adult_intimacy_action(
                self.root,
                {"action": "update_private_space", "garment_op": {"op": "undress", "garment_id": "subject_inner_top"}},
                now=T0,
            )
        self.assertIn("GARMENT_LAYER_BLOCKED", str(ctx_top.exception))

        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "garment_op": {"op": "undress", "garment_id": "subject_trousers"}},
            now=T0,
        )
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "garment_op": {"op": "undress", "garment_id": "subject_underwear"}},
            now=T0,
        )
        _assert_core_invariants(self, self.root, forbidden_substrings=("<<FORBIDDEN_COSTUME_FIXTURE>>",))

    def test_X27_overnight_shared_sleep_living_sofa_409_no_sleep_slot_vs_bed_sleep(self) -> None:
        _setup_ready_env(self.root)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "place": "living_sofa"},
            now=T0,
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx_sofa:
            apply_adult_intimacy_action(
                self.root,
                {"action": "update_private_space", "sleep_op": "enter_sleep"},
                now=T0 + timedelta(minutes=1),
            )
        self.assertIn("NO_SLEEP_SLOT", str(ctx_sofa.exception))

        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "place": "bedroom.bed"},
            now=T0 + timedelta(minutes=2),
        )
        pid, _ = _propose_and_accept(self.root, now=T0 + timedelta(minutes=3), key_prefix="x27")
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "sleep_op": "enter_sleep"},
            now=T0 + timedelta(minutes=20),
        )
        raw = _load_raw_state(self.root)
        self.assertEqual(raw["permits"]["records"][pid]["state"], "ENDED")
        self.assertEqual(raw["permits"]["records"][pid]["end_reason"], "SHARED_SLEEP")
        with self.assertRaises(AdultIntimacyConflictError) as ctx_asleep:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "propose_scene",
                    "operation_key": "x27_while_asleep",
                    "proposer": "partner",
                    "categories": ["KISSING"],
                },
                now=T0 + timedelta(minutes=25),
            )
        self.assertIn("SUBJECT_UNAVAILABLE", str(ctx_asleep.exception))
        _assert_core_invariants(self, self.root, now=T0 + timedelta(minutes=25))

    def test_X28_waste_bin_full_409_and_empty_waste_bin_recovery(self) -> None:
        _setup_ready_env(self.root)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "waste_bin_cap": 2, "waste_bin_used": 2},
            now=T0,
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx_full:
            apply_adult_intimacy_action(
                self.root,
                {"action": "update_private_space", "waste_op": "dispose_waste"},
                now=T0 + timedelta(seconds=5),
            )
        self.assertIn("BIN_FULL", str(ctx_full.exception))
        self.assertGreaterEqual(_load_raw_state(self.root)["space"]["uncollected_waste"], 1)

        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "waste_op": "empty_waste_bin"},
            now=T0 + timedelta(seconds=10),
        )
        res = apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "waste_op": "dispose_waste"},
            now=T0 + timedelta(seconds=12),
        )
        self.assertEqual(res["private_space"]["waste_bin_used"], 1)
        _assert_core_invariants(self, self.root)

    def test_X29_wet_towel_reuse_409_towel_wet_and_laundry_not_dry_409(self) -> None:
        _setup_ready_env(self.root)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "towel_op": "use_towel"},
            now=T0,
        )
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "towel_op": "use_towel"},
            now=T0 + timedelta(seconds=5),
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx_wet:
            apply_adult_intimacy_action(
                self.root,
                {"action": "update_private_space", "towel_op": "use_towel"},
                now=T0 + timedelta(seconds=10),
            )
        self.assertIn("TOWEL_WET", str(ctx_wet.exception))

        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "laundry_op": "wash"},
            now=T0 + timedelta(seconds=15),
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx_ndry:
            apply_adult_intimacy_action(
                self.root,
                {"action": "update_private_space", "laundry_op": "stow"},
                now=T0 + timedelta(seconds=20),
            )
        self.assertIn("NOT_DRY", str(ctx_ndry.exception))
        _assert_core_invariants(self, self.root)

    def test_X30_execution_id_and_operation_key_payload_mismatch_409_execution_id_reused(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "execution_id": "exec_x30_same",
                "categories": ["KISSING"],
            },
            now=T0 + timedelta(seconds=5),
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx_b:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": "exec_x30_same",
                    "categories": ["CARESSING_CLOTHED:user_to_subject"],
                },
                now=T0 + timedelta(seconds=6),
            )
        self.assertIn("EXECUTION_ID_REUSED", str(ctx_b.exception))

        apply_adult_intimacy_action(
            self.root,
            {"action": "pause_scene", "permit_id": pid, "by": "partner", "operation_key": "op_x30_pause", "reason": "R1"},
            now=T0 + timedelta(seconds=8),
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx_p:
            apply_adult_intimacy_action(
                self.root,
                {"action": "pause_scene", "permit_id": pid, "by": "partner", "operation_key": "op_x30_pause", "reason": "R2"},
                now=T0 + timedelta(seconds=9),
            )
        self.assertIn("EXECUTION_ID_REUSED", str(ctx_p.exception))
        _assert_core_invariants(self, self.root)

    # ------------------------------------------------------------------
    # AL-01..AL-08: GAP-04 Integer Alcohol Metabolism & Hysteresis
    # ------------------------------------------------------------------
    def test_AL01_intoxicated_100_draws_never_emit_propose_sexual_activity(self) -> None:
        _setup_ready_env(self.root)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "blood_msd": 1600, "desire_pulse": 0.9},
            now=T0,
        )
        for step in range(100):
            proj = read_adult_intimacy(self.root, now=T0 + timedelta(seconds=step), subject_view=True)
            self.assertNotIn("PROPOSE_SEXUAL_ACTIVITY", proj["body_and_desire"]["subject_candidates"])
            self.assertIn("COMPANIONSHIP", proj["body_and_desire"]["subject_candidates"])
        _assert_core_invariants(self, self.root)

    def test_AL02_intoxicated_blocks_new_scene_409_incapacity_zero_shame_memory(self) -> None:
        _setup_ready_env(self.root)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "blood_msd": 1500},
            now=T0,
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "propose_scene",
                    "operation_key": "al02_prop",
                    "proposer": "partner",
                    "categories": ["KISSING"],
                },
                now=T0 + timedelta(seconds=5),
            )
        self.assertIn("INCAPACITY", str(ctx.exception))
        proj = read_adult_intimacy(self.root, now=T0 + timedelta(seconds=5), subject_view=True)
        self.assertEqual(proj["solo_and_memory"]["private_memory"]["entries"], {})
        _assert_core_invariants(self, self.root)

    def test_AL03_mid_scene_alcohol_crosses_1500_suspends_incapacity(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "gut_msd": 1200, "blood_msd": 1480},
            now=T0,
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": "exec_al03",
                    "categories": ["KISSING"],
                },
                now=T0 + timedelta(minutes=2),
            )
        self.assertIn("SUSPENDED_INCAPACITY", str(ctx.exception))
        raw = _load_raw_state(self.root)
        self.assertEqual(raw["permits"]["records"][pid]["state"], "SUSPENDED_INCAPACITY")
        self.assertEqual(raw["memory"]["entries"], {})
        _assert_core_invariants(self, self.root, now=T0 + timedelta(minutes=2))

    def test_AL04_sobriety_hysteresis_800_plus_30min_hold_and_never_auto_resumes_suspended(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "blood_msd": 1520, "gut_msd": 0},
            now=T0,
        )
        self.assertEqual(_load_raw_state(self.root)["permits"]["records"][pid]["state"], "SUSPENDED_INCAPACITY")

        # (1520 - 788) / 12 = 61 min -> blood = 788 (< 800), enters 30-min sober_hold_until
        p_65 = read_adult_intimacy(self.root, now=T0 + timedelta(minutes=65))
        self.assertLess(p_65["body_and_desire"]["blood_msd"], 800)
        self.assertEqual(p_65["body_and_desire"]["alcohol_impairment_level"], "INTOXICATED")
        self.assertIsNotNone(p_65["body_and_desire"]["sober_hold_until"])
        with self.assertRaises(AdultIntimacyConflictError) as ctx_hold:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "propose_scene",
                    "operation_key": "al04_during_hold",
                    "proposer": "partner",
                    "categories": ["KISSING"],
                },
                now=T0 + timedelta(minutes=75),
            )
        self.assertIn("INCAPACITY", str(ctx_hold.exception))

        # After full 30-min hold (at T0 + 95 min), sober is released, YET suspended permit still cannot resume!
        p_95 = read_adult_intimacy(self.root, now=T0 + timedelta(minutes=95))
        self.assertEqual(p_95["body_and_desire"]["alcohol_impairment_level"], "TIPSY" if p_95["body_and_desire"]["blood_msd"] >= 500 else "SOBER")
        with self.assertRaises(AdultIntimacyConflictError) as ctx_res:
            apply_adult_intimacy_action(
                self.root,
                {"action": "confirm_resume", "permit_id": pid, "by": "partner", "resume_epoch": 1},
                now=T0 + timedelta(minutes=130),
            )
        self.assertIn("SUSPENDED_INCAPACITY", str(ctx_res.exception))

        # Fresh proposal succeeds and archives old SUSPENDED_INCAPACITY permit
        fresh = apply_adult_intimacy_action(
            self.root,
            {
                "action": "propose_scene",
                "operation_key": "al04_fresh",
                "proposer": "partner",
                "categories": ["KISSING"],
            },
            now=T0 + timedelta(minutes=135),
        )
        new_pid = fresh["action_receipt"]["permit_id"]
        self.assertNotEqual(new_pid, pid)
        self.assertEqual(_load_raw_state(self.root)["permits"]["records"][pid]["state"], "ENDED")
        _assert_core_invariants(self, self.root, now=T0 + timedelta(minutes=135))

    def test_AL05_user_impairment_declared_blocks_and_suspends_zero_shame_memory(self) -> None:
        _setup_ready_env(self.root)
        pid, _ = _propose_and_accept(self.root)
        upd = apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "user_impairment_declared": True},
            now=T0 + timedelta(seconds=10),
        )
        self.assertEqual(upd["action_receipt"]["suspended_permit_id"], pid)
        self.assertEqual(_load_raw_state(self.root)["permits"]["records"][pid]["state"], "SUSPENDED_INCAPACITY")
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "propose_scene",
                    "operation_key": "al05_prop",
                    "proposer": "partner",
                    "categories": ["KISSING"],
                },
                now=T0 + timedelta(seconds=15),
            )
        self.assertIn("INCAPACITY", str(ctx.exception))
        self.assertEqual(_load_raw_state(self.root)["memory"]["entries"], {})
        _assert_core_invariants(self, self.root)

    def test_AL06_tipsy_never_increases_acceptance_or_loosens_boundaries(self) -> None:
        _setup_ready_env(self.root)
        m_sober = read_adult_intimacy(self.root, now=T0)["preference_menu"]["effective_menu"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "blood_msd": 650},
            now=T0 + timedelta(seconds=5),
        )
        p_tipsy = read_adult_intimacy(self.root, now=T0 + timedelta(seconds=5))
        self.assertEqual(p_tipsy["body_and_desire"]["alcohol_impairment_level"], "TIPSY")
        m_tipsy = p_tipsy["preference_menu"]["effective_menu"]
        self.assertEqual(set(m_tipsy["open"]), set(m_sober["open"]))
        self.assertEqual(set(m_tipsy["ask"]), set(m_sober["ask"]))
        self.assertEqual(set(m_tipsy["excluded"]), set(m_sober["excluded"]))
        _assert_core_invariants(self, self.root)

    def test_AL07_58h_3480min_jump_matches_1min_step_loop_exact_integers(self) -> None:
        _setup_ready_env(self.root)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "gut_msd": 2800, "blood_msd": 400},
            now=T0,
        )
        # Single 3480-min (58h) projection jump vs 3480 individual 1-min step calls on helper
        jumped = aiv._step_alcohol_metabolism(
            2800, 400, False, None, T0.isoformat(), T0 + timedelta(minutes=3480)
        )
        gut, blood, latched, hold_iso = 2800, 400, False, None
        cur_dt = T0
        for _ in range(3480):
            nxt_dt = cur_dt + timedelta(minutes=1)
            st = aiv._step_alcohol_metabolism(gut, blood, latched, hold_iso, cur_dt.isoformat(), nxt_dt)
            gut = st["gut_msd"]
            blood = st["blood_msd"]
            latched = st["intox_latched"]
            hold_iso = st["sober_hold_until"]
            cur_dt = nxt_dt
        self.assertIsInstance(jumped["gut_msd"], int)
        self.assertIsInstance(jumped["blood_msd"], int)
        self.assertEqual(jumped["gut_msd"], gut)
        self.assertEqual(jumped["blood_msd"], blood)
        self.assertEqual(jumped["intox_latched"], latched)
        self.assertEqual(jumped["impairment_level"], "SOBER")

    def test_AL08_alcohol_disabled_mode_enforced(self) -> None:
        _setup_ready_env(self.root)
        apply_adult_intimacy_action(
            self.root,
            {"action": "configure_adult_gate", "alcohol_enabled": False},
            now=T0,
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "update_body_context", "ingest_alcohol_msd": 1000},
                now=T0 + timedelta(seconds=5),
            )
        self.assertIn("ALCOHOL_DISABLED", str(ctx.exception))
        _assert_core_invariants(self, self.root)

    # ------------------------------------------------------------------
    # ERR-02 / ERR-03 / ERR-13 + K02 + Contraception/Care + Tension + Validator Soft Rules
    # ------------------------------------------------------------------
    def test_ERR02_ERR03_ERR13_aftercare_cannot_pause_scn_prefix_and_dual_revisions(self) -> None:
        _setup_ready_env(self.root)
        pid, acc = _propose_and_accept(self.root)
        # ERR-03: New permit IDs use scn_ prefix
        self.assertTrue(pid.startswith("scn_"))
        # ERR-13: Both permit_revision and revision aliases match
        self.assertEqual(acc["scene_permit"]["permit_revision"], acc["scene_permit"]["revision"])
        self.assertEqual(
            acc["scene_permit"]["active_permit"]["permit_revision"],
            acc["scene_permit"]["active_permit"]["revision"],
        )
        self.assertEqual(acc["action_receipt"]["permit_revision"], acc["action_receipt"]["revision"])

        # Legacy permit_ ID compatibility check
        raw = _load_raw_state(self.root)
        legacy_pid = "permit_legacy_0001"
        raw["permits"]["records"][legacy_pid] = dict(raw["permits"]["records"][pid], permit_id=legacy_pid, state="PROPOSED")
        adult_intimacy_state_path(self.root).write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        leg_stop = apply_adult_intimacy_action(
            self.root,
            {"action": "stop_scene", "permit_id": legacy_pid, "by": "partner"},
            now=T0 + timedelta(seconds=2),
        )
        self.assertEqual(leg_stop["action_receipt"]["state"], "STOPPED")

        # Enter AFTERCARE and verify aftercare_entered_at + chores + ERR-02 409 AFTERCARE_CANNOT_PAUSE
        aft = apply_adult_intimacy_action(
            self.root,
            {"action": "stop_scene", "permit_id": pid, "by": "partner", "transition": "AFTERCARE"},
            now=T0 + timedelta(seconds=5),
        )
        self.assertEqual(aft["scene_permit"]["active_permit"]["state"], "AFTERCARE")
        self.assertIsNotNone(aft["scene_permit"]["active_permit"]["aftercare_entered_at"])
        self.assertIsInstance(aft["scene_permit"]["active_permit"]["chores"], list)

        with self.assertRaises(AdultIntimacyConflictError) as ctx_p:
            apply_adult_intimacy_action(
                self.root,
                {"action": "pause_scene", "permit_id": pid, "by": "partner"},
                now=T0 + timedelta(seconds=8),
            )
        self.assertIn("AFTERCARE_CANNOT_PAUSE", str(ctx_p.exception))
        _assert_core_invariants(self, self.root)

    def test_K02_adult_work_allowed_study_mismatch_blocked_and_campus_venue_403(self) -> None:
        res_work = apply_adult_intimacy_action(
            self.root,
            {
                "action": "configure_adult_gate",
                "adult_confirmed": True,
                "life_stage": "working_adult",
                "attested_by": "deployer",
                "feature_enabled": True,
                "world_facts": [{"kind": "WORK", "attends": True}],
            },
            now=T0,
        )
        self.assertTrue(res_work["adult_gate"]["gate_open"])

        with self.assertRaises(AdultIntimacyConflictError) as ctx_study:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "configure_adult_gate",
                    "adult_confirmed": True,
                    "life_stage": "working_adult",
                    "attested_by": "deployer",
                    "world_facts": [{"kind": "STUDY", "attends": True}],
                },
                now=T0 + timedelta(seconds=2),
            )
        self.assertIn("STAGE_UNEXPLAINED", str(ctx_study.exception))

        _setup_ready_env(self.root, now=T0 + timedelta(seconds=5))
        with self.assertRaises(AdultIntimacyPermissionError) as ctx_campus:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "propose_scene",
                    "operation_key": "k02_campus",
                    "proposer": "partner",
                    "categories": ["KISSING"],
                    "place": "campus.dorm",
                },
                now=T0 + timedelta(seconds=6),
            )
        self.assertIn("CAMPUS_VENUE", str(ctx_campus.exception))
        _assert_core_invariants(self, self.root)

    def test_contraception_condom_required_409_venue_ejaculation_target_and_menstrual_care(self) -> None:
        _setup_ready_env(
            self.root,
            preset="male",
            user_genitals="vulva_vagina",
            contraception_preference="CONDOM_REQUIRED",
        )
        pid, _ = _propose_and_accept(
            self.root,
            categories=["KISSING", "MANUAL:user_to_subject", "INTERCOURSE"],
        )
        # Raise arousal to high -> full erection first
        for step in range(1, 4):
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": f"warmup_{step}",
                    "categories": ["MANUAL:user_to_subject"],
                },
                now=T0 + timedelta(seconds=step),
            )
        # INTERCOURSE without condom -> 409 CONDOM_REQUIRED_UNAVAILABLE
        with self.assertRaises(AdultIntimacyConflictError) as ctx_cd:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "execution_id": "exec_no_condom",
                    "categories": ["INTERCOURSE"],
                },
                now=T0 + timedelta(seconds=10),
            )
        self.assertIn("CONDOM_REQUIRED_UNAVAILABLE", str(ctx_cd.exception))

        # With condom consumed + ORGASM -> succeeds, ejaculation_target == "condom", sensitivity == "heightened"
        ok = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "execution_id": "exec_with_condom",
                "categories": ["INTERCOURSE"],
                "proposed_render": {
                    "proposed_facts": [
                        {"kind": "CONSUME", "item": "condom"},
                        {"kind": "ORGASM"},
                    ],
                },
            },
            now=T0 + timedelta(seconds=12),
        )
        self.assertEqual(ok["action_receipt"]["ejaculation_target"], "condom")
        self.assertEqual(ok["body_and_desire"]["sensitivity"], "heightened")

        # Venue-aware ejaculation target + menstrual warm-compress / warm-water care preferences
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "place": "bathroom.shower"},
            now=T0 + timedelta(seconds=15),
        )
        self.assertEqual(
            read_adult_intimacy(self.root, now=T0 + timedelta(seconds=15))["body_and_desire"]["ejaculation_target"],
            "bathroom_drain",
        )
        care = apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "optional_processes": ["menstruation_lite"]},
            now=T0 + timedelta(seconds=16),
            subject_view=True,
        )
        self.assertTrue(care["body_and_desire"]["care_preferences"]["warm_compress"])
        self.assertTrue(care["body_and_desire"]["care_preferences"]["warm_water"])
        _assert_core_invariants(self, self.root)

    def test_emotional_tension_rupture_blocks_propose_two_step_pref_revision_and_outside_tighten(self) -> None:
        _setup_ready_env(self.root)
        et_path = self.root / "phone" / "emotional_tension.json"
        et_path.write_text(
            json.dumps({"quarrel_domain": {"ruptures": [{"status": "OPEN", "scope": "US"}], "in_space": True}}),
            encoding="utf-8",
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx_et:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "propose_scene",
                    "operation_key": "et_prop",
                    "proposer": "partner",
                    "categories": ["KISSING"],
                },
                now=T0,
            )
        self.assertIn("SUBJECT_EMOTIONALLY_UNAVAILABLE", str(ctx_et.exception))
        et_path.unlink()

        # Two-step subject preference revision: stage="propose" -> cross-sleep + 24h -> stage="commit"
        note = apply_adult_intimacy_action(
            self.root,
            {
                "action": "record_setback",
                "kind": "preference_note",
                "source_id": "IX_REFLECTION",
                "target_type": "CATEGORY",
                "code": "ORAL:user_to_subject",
                "polarity": "LIKE",
                "clarity": "CLEAR",
                "elicited": False,
                "context": "AFTERGLOW",
            },
            now=T0 + timedelta(minutes=1),
            subject_view=True,
        )
        ev_id = note["action_receipt"]["memory_id"]
        prop_rev = apply_adult_intimacy_action(
            self.root,
            {
                "action": "revise_subject_preference",
                "stage": "propose",
                "stances": {"ORAL:user_to_subject": "YES"},
                "evidence_ref": ev_id,
            },
            now=T0 + timedelta(minutes=2),
            subject_view=True,
        )
        prop_id = prop_rev["action_receipt"]["proposal_id"]
        with self.assertRaises(AdultIntimacyConflictError) as ctx_early:
            apply_adult_intimacy_action(
                self.root,
                {"action": "revise_subject_preference", "stage": "commit", "proposal_id": prop_id},
                now=T0 + timedelta(hours=2),
                subject_view=True,
            )
        self.assertIn("PREF_DECISION_TOO_EARLY", str(ctx_early.exception))

        apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "sleep_completed": True},
            now=T0 + timedelta(hours=10),
        )
        committed = apply_adult_intimacy_action(
            self.root,
            {"action": "revise_subject_preference", "stage": "commit", "proposal_id": prop_id},
            now=T0 + timedelta(hours=25),
            subject_view=True,
        )
        self.assertEqual(
            committed["preference_menu"]["subject_seed"]["stances"]["ORAL:user_to_subject"],
            "YES",
        )

        # Tightening partner_limits via discuss_intimacy_outside_scene is allowed anytime (even outside private space)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "place": "public.cafe"},
            now=T0 + timedelta(hours=26),
        )
        tightened = apply_adult_intimacy_action(
            self.root,
            {
                "action": "discuss_intimacy_outside_scene",
                "speaker": "partner",
                "tighten_partner_limits": {
                    "preferred_tier": "SUGGESTIVE",
                    "stances": {"INTERCOURSE": "NO"},
                },
            },
            now=T0 + timedelta(hours=26, minutes=1),
        )
        self.assertTrue(tightened["action_receipt"]["partner_limits_tightened"])
        self.assertEqual(tightened["preference_menu"]["partner_limits"]["preferred_tier"], "SUGGESTIVE")
        _assert_core_invariants(self, self.root, now=T0 + timedelta(hours=26, minutes=1))

    def test_validate_rendered_turn_pass_rewrite_m07_feigned_pressure_tier_and_repo_cleanliness(self) -> None:
        _setup_ready_env(self.root, preferred_tier="SUGGESTIVE")
        pid, _ = _propose_and_accept(self.root, desired_tier="SUGGESTIVE")

        # 1. Over-tier marker -> REWRITE
        r_tier = apply_adult_intimacy_action(
            self.root,
            {
                "action": "validate_rendered_turn",
                "permit_id": pid,
                "execution_id": "val_tier",
                "text": "gentle line <<EXPLICIT_TERM_MARKER>>",
            },
            now=T0 + timedelta(seconds=5),
        )
        self.assertEqual(r_tier["validation"]["decision"], "REWRITE")
        self.assertIn("TIER_EXCEEDED", r_tier["validation"]["reasons"])

        # 2. Pressure tag -> REWRITE
        r_press = apply_adult_intimacy_action(
            self.root,
            {
                "action": "validate_rendered_turn",
                "permit_id": pid,
                "execution_id": "val_press",
                "text": "gentle line",
                "pressure_tags": ["QUARREL_LEVERAGE"],
            },
            now=T0 + timedelta(seconds=6),
        )
        self.assertEqual(r_press["validation"]["decision"], "REWRITE")
        self.assertIn("PRESSURE_TAG:QUARREL_LEVERAGE", r_press["validation"]["reasons"])

        # 3. Feigned climax without body orgasm receipt -> REWRITE
        r_feign = apply_adult_intimacy_action(
            self.root,
            {
                "action": "validate_rendered_turn",
                "permit_id": pid,
                "execution_id": "val_feign",
                "text": "gentle line <<FEIGNED_ORGASM_MARKER>>",
            },
            now=T0 + timedelta(seconds=7),
        )
        self.assertEqual(r_feign["validation"]["decision"], "REWRITE")
        self.assertIn("ORGASM_FEIGNED", r_feign["validation"]["reasons"])

        # 4. M07 reverse signal conflict (declared CONTINUE while text has stop/pause cue) -> REWRITE
        r_m07 = apply_adult_intimacy_action(
            self.root,
            {
                "action": "validate_rendered_turn",
                "permit_id": pid,
                "execution_id": "val_m07",
                "text": "等一下，我想停下",
                "subject_signal": "CONTINUE",
            },
            now=T0 + timedelta(seconds=8),
        )
        self.assertEqual(r_m07["validation"]["decision"], "REWRITE")
        self.assertIn("SIGNAL_TEXT_CONFLICT_M07", r_m07["validation"]["reasons"])

        # 5. Clean turn -> PASS
        r_pass = apply_adult_intimacy_action(
            self.root,
            {
                "action": "validate_rendered_turn",
                "permit_id": pid,
                "execution_id": "val_pass",
                "text": "夜风很轻，她靠在你肩上。",
                "depicted_categories": ["KISSING"],
            },
            now=T0 + timedelta(seconds=9),
        )
        self.assertEqual(r_pass["validation"]["decision"], "PASS")
        self.assertTrue(r_pass["validation"]["passed"])

        # 6. Repository Cleanliness Iron Law: default lexicon uses ONLY neutral synthetic markers
        lex_path = Path(aiv.__file__).with_name("validator_lexicon.v1.json")
        lex_doc = json.loads(lex_path.read_text(encoding="utf-8"))
        self.assertEqual(lex_doc["kind"], "validator_lexicon.v1")
        for item in lex_doc["entries"]:
            self.assertTrue(item["pattern"].startswith("<<") and item["pattern"].endswith(">>"))
        _assert_core_invariants(self, self.root)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Unit tests for ``components.phone.adult_intimacy_view`` (Chapter 35 + 36B-1..11 + ADR-047).

All test data is strictly synthetic and uses ``adult_confirmed: bool`` + ``life_stage`` enums
and neutral structural markers (``<<HARD_FAIL_MARKER>>``).
"""
from __future__ import annotations

import ast
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from components.phone import adult_intimacy_view as aiv
from components.phone import sensory_character_view as char_view
from components.phone.adult_intimacy_view import (
    ADULT_INTIMACY_SCHEMA,
    AdultIntimacyConflictError,
    AdultIntimacyNotFoundError,
    AdultIntimacyPermissionError,
    AdultIntimacyValidationError,
    adult_intimacy_state_path,
    apply_adult_intimacy_action,
    find_matching_solo_slot,
    read_adult_intimacy,
    solo_window_probability,
)

T0 = datetime(2026, 11, 12, 21, 0, 0, tzinfo=timezone.utc)


def _open_ready_env(
    root: Path,
    *,
    now: datetime = T0,
    preset: str = "neutral",
    life_stage: str = "working_adult",
    deploy_tier_cap: str = "EXPLICIT",
    preferred_tier: str = "EXPLICIT",
    solo_enabled: bool = False,
    solo_inclination: str = "none",
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
            "solo_enabled": solo_enabled,
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
            "solo_inclination": solo_inclination,
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


def _select_pack_preset(root: Path, preset_id: str) -> None:
    c_state, _ = char_view.read_character_pack_state(root)
    next_state = char_view.apply_select_preset(
        c_state,
        preset_id=preset_id,
        now_iso="2026-11-12T21:00:00Z",
    )
    char_view.write_character_pack_state(root, next_state)


class AdultIntimacyClosedLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # 01-03: Determinism, Zero-Write GET & Dynamic Character Pack Binding
    # ------------------------------------------------------------------
    def test_01_ast_pure_stdlib_zero_random_zero_network(self) -> None:
        src = Path(aiv.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = {"random", "secrets", "urllib", "http", "socket", "subprocess", "openai", "httpx", "requests"}
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported & forbidden, set())

    def test_02_get_100_percent_zero_write_and_ordinary_companionship_independent(self) -> None:
        state_file = adult_intimacy_state_path(self.root)
        self.assertFalse(state_file.exists())
        self.assertFalse((self.root / "phone").exists())

        proj = read_adult_intimacy(self.root, now=T0, subject_view=False)
        self.assertFalse(state_file.exists())
        self.assertFalse((self.root / "phone").exists())
        self.assertEqual(proj["schema"], ADULT_INTIMACY_SCHEMA)
        self.assertEqual(proj["revision"], 0)
        self.assertTrue(proj["read_only"])
        self.assertFalse(proj["adult_gate"]["adult_confirmed"])
        self.assertFalse(proj["adult_gate"]["gate_open"])
        self.assertTrue(proj["ordinary_companionship_independent"])
        self.assertEqual(proj["preference_menu"]["effective_menu"]["open"], [])

    def test_03_open_source_character_pack_presets_neutral_chiyo_haru(self) -> None:
        p_default = read_adult_intimacy(self.root, now=T0)
        self.assertEqual(p_default["subject_identity"]["display_name"], "TA")
        self.assertEqual(p_default["subject_identity"]["pronouns"], "TA")
        self.assertEqual(p_default["body_and_desire"]["preset"], "neutral")

        _select_pack_preset(self.root, "chiyo")
        p_chiyo = read_adult_intimacy(self.root, now=T0)
        self.assertEqual(p_chiyo["subject_identity"]["display_name"], "千代")
        self.assertEqual(p_chiyo["subject_identity"]["pronouns"], "她")
        self.assertEqual(p_chiyo["body_and_desire"]["preset"], "female")
        self.assertFalse(p_chiyo["adult_gate"]["gate_open"])

        _select_pack_preset(self.root, "haru")
        p_haru = read_adult_intimacy(self.root, now=T0)
        self.assertEqual(p_haru["subject_identity"]["display_name"], "晴")
        self.assertEqual(p_haru["subject_identity"]["pronouns"], "他")
        self.assertEqual(p_haru["body_and_desire"]["preset"], "male")

    # ------------------------------------------------------------------
    # 04-10: Adult Gate Positive/Negative & Safety Hard Blocks (36B §13)
    # ------------------------------------------------------------------
    def test_04_adult_gate_deployer_attestation_for_all_three_adult_stages(self) -> None:
        for stage in ("university_student", "working_adult", "independent_adult"):
            res = apply_adult_intimacy_action(
                self.root,
                {
                    "action": "configure_adult_gate",
                    "adult_confirmed": True,
                    "life_stage": stage,
                    "attested_by": "deployer",
                    "feature_enabled": True,
                },
                now=T0,
            )
            self.assertTrue(res["adult_gate"]["adult_confirmed"])
            self.assertTrue(res["adult_gate"]["gate_open"])
            self.assertEqual(res["adult_gate"]["life_stage"], stage)

    def test_05_pack_cannot_self_attest_or_self_confirm_seed(self) -> None:
        for bad_actor in ("character_pack", "subject", "self", "model"):
            with self.assertRaises(AdultIntimacyPermissionError) as ctx:
                apply_adult_intimacy_action(
                    self.root,
                    {
                        "action": "configure_adult_gate",
                        "adult_confirmed": True,
                        "life_stage": "working_adult",
                        "attested_by": bad_actor,
                    },
                    now=T0,
                )
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.reason_code, "PACK_CANNOT_SELF_ATTEST")

        with self.assertRaises(AdultIntimacyPermissionError) as sctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "confirm_preference_seed", "attested_by": "character_pack"},
                now=T0,
            )
        self.assertEqual(sctx.exception.reason_code, "PACK_CANNOT_CONFIRM_SEED")

    def test_06_hard_block_minor_stage_and_minor_institution(self) -> None:
        with self.assertRaises(AdultIntimacyPermissionError) as ctx1:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "configure_adult_gate",
                    "adult_confirmed": True,
                    "life_stage": "minor_stage",
                    "attested_by": "deployer",
                },
                now=T0,
            )
        self.assertEqual(ctx1.exception.reason_code, "MINOR_STAGE_PROHIBITED")

        with self.assertRaises(AdultIntimacyPermissionError) as ctx2:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "configure_adult_gate",
                    "adult_confirmed": True,
                    "life_stage": "university_student",
                    "attested_by": "deployer",
                    "world_facts": [{"id": "inst_1", "institution_kind": "minor_education"}],
                },
                now=T0,
            )
        self.assertEqual(ctx2.exception.reason_code, "MINOR_STAGE_PROHIBITED")

    def test_07_hard_block_child_coding_lexicon_and_mid_scene_shutdown(self) -> None:
        _open_ready_env(self.root, now=T0)
        prop = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "p_lex", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = prop["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )

        with self.assertRaises(AdultIntimacyPermissionError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "operation_key": "beat_bad",
                    "categories": ["KISSING"],
                    "proposed_render": {"narrative_text": "forbidden <<HARD_FAIL_MARKER>> token"},
                },
                now=T0 + timedelta(seconds=30),
            )
        self.assertEqual(ctx.exception.reason_code, "CHILD_CODING_PROHIBITED")
        after = read_adult_intimacy(self.root, now=T0 + timedelta(seconds=31))
        self.assertFalse(after["adult_gate"]["gate_open"])
        self.assertTrue(after["adult_gate"]["review_required"])
        self.assertIsNone(after["scene_permit"]["active_permit"])

    def test_08_hard_block_blood_relative_guardian_cnc_animal_real_person(self) -> None:
        for rel in ("blood_relative", "guardian"):
            with self.assertRaises(AdultIntimacyPermissionError) as rctx:
                apply_adult_intimacy_action(
                    self.root,
                    {"action": "configure_adult_gate", "relation_override": rel},
                    now=T0,
                )
            self.assertEqual(rctx.exception.reason_code, "FORBIDDEN_RELATION")

        with self.assertRaises(AdultIntimacyPermissionError) as cctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "configure_adult_gate", "non_consensual_mode": True},
                now=T0,
            )
        self.assertEqual(cctx.exception.reason_code, "NON_CONSENSUAL_PROHIBITED")

        with self.assertRaises(AdultIntimacyPermissionError) as actx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "configure_adult_gate", "subject_kind": "animal"},
                now=T0,
            )
        self.assertEqual(actx.exception.reason_code, "BESTIALITY_PROHIBITED")

        with self.assertRaises(AdultIntimacyPermissionError) as pctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "configure_adult_gate", "fictional_subject": False},
                now=T0,
            )
        self.assertEqual(pctx.exception.reason_code, "REAL_PERSON_PROHIBITED")

    def test_09_user_minor_self_report_closes_gate_and_ends_active_scene(self) -> None:
        _open_ready_env(self.root, now=T0)
        prop = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "p_rep", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = prop["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )

        res = apply_adult_intimacy_action(
            self.root,
            {"action": "report_safety_boundary", "trigger": "user_minor_self_report"},
            now=T0 + timedelta(minutes=1),
        )
        self.assertFalse(res["adult_gate"]["gate_open"])
        self.assertTrue(res["adult_gate"]["user_scope_closed"])
        self.assertTrue(res["adult_gate"]["review_required"])
        self.assertIsNone(res["scene_permit"]["active_permit"])

    def test_10_money_or_gift_never_unlocks_consent(self) -> None:
        _open_ready_env(self.root, now=T0)
        for bad_key in ("money", "gift", "payment", "tip", "intimacy_score", "login_streak"):
            with self.assertRaises(AdultIntimacyPermissionError) as ctx:
                apply_adult_intimacy_action(
                    self.root,
                    {
                        "action": "propose_scene",
                        "operation_key": f"op_{bad_key}",
                        "proposer": "partner",
                        "categories": ["KISSING"],
                        bad_key: 999,
                    },
                    now=T0,
                )
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.reason_code, "MONEY_OR_GIFT_CANNOT_UNLOCK_CONSENT")

    # ------------------------------------------------------------------
    # 11-17: Body Profiles, Refractory, Desire Subsidence & Incapacitation
    # ------------------------------------------------------------------
    def test_11_incapacitation_hard_block_sleeping_unconscious_intoxicated_illness(self) -> None:
        _open_ready_env(self.root, now=T0)
        states = [
            {"awake": False},
            {"impairment": "unconscious"},
            {"impairment": "intoxicated"},
            {"acute_illness": True},
        ]
        for idx, patch in enumerate(states):
            apply_adult_intimacy_action(self.root, {"action": "update_body_context", **patch}, now=T0)
            with self.assertRaises(AdultIntimacyConflictError) as ctx:
                apply_adult_intimacy_action(
                    self.root,
                    {
                        "action": "propose_scene",
                        "operation_key": f"incap_{idx}",
                        "proposer": "partner",
                        "categories": ["KISSING"],
                    },
                    now=T0,
                )
            self.assertEqual(ctx.exception.reason_code, "SUBJECT_UNAVAILABLE")
            # Restore sober awake healthy
            apply_adult_intimacy_action(
                self.root,
                {"action": "update_body_context", "awake": True, "impairment": "sober", "acute_illness": False},
                now=T0,
            )

    def test_12_mid_scene_incapacitation_immediately_aborts_active_scene(self) -> None:
        _open_ready_env(self.root, now=T0)
        p = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "p_drunk", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        aborted = apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "impairment": "intoxicated"},
            now=T0 + timedelta(minutes=2),
        )
        self.assertEqual(aborted["action_receipt"]["aborted_permit_id"], pid)
        self.assertIsNone(aborted["scene_permit"]["active_permit"])

    def test_13_desire_and_arousal_subside_naturally_with_zero_frustration_debt(self) -> None:
        _open_ready_env(self.root, now=T0)
        elevated = apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "desire_pulse": 0.85},
            now=T0,
        )
        d0 = elevated["body_and_desire"]["desire_level"]
        self.assertGreater(d0, 0.5)

        later_2h = read_adult_intimacy(self.root, now=T0 + timedelta(hours=2))
        later_8h = read_adult_intimacy(self.root, now=T0 + timedelta(hours=8))
        self.assertLess(later_2h["body_and_desire"]["desire_level"], d0)
        self.assertLess(later_8h["body_and_desire"]["desire_level"], later_2h["body_and_desire"]["desire_level"])
        self.assertTrue(later_8h["body_and_desire"]["no_frustration_debt"])
        self.assertEqual(later_8h["body_and_desire"]["frustration_debt"], 0.0)

    def test_14_body_profile_validation_rejects_impossible_physiology(self) -> None:
        with self.assertRaises(AdultIntimacyValidationError):
            apply_adult_intimacy_action(
                self.root,
                {"action": "configure_body_profile", "preset": "female", "overrides": {"ejaculates": True}},
                now=T0,
            )
        with self.assertRaises(AdultIntimacyValidationError):
            apply_adult_intimacy_action(
                self.root,
                {"action": "configure_body_profile", "preset": "male", "overrides": {"natural_lubrication": True}},
                now=T0,
            )

    def test_15_anatomy_pairing_matrix_blocks_incompatible_intercourse_without_pressure(self) -> None:
        _open_ready_env(self.root, now=T0, preset="female", user_genitals="vulva_vagina")
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "propose_scene",
                    "operation_key": "anat_1",
                    "proposer": "partner",
                    "categories": ["INTERCOURSE"],
                },
                now=T0,
            )
        self.assertEqual(ctx.exception.reason_code, "ANATOMY_MISMATCH")
        # Non-intercourse categories remain completely open without cooldown!
        ok = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "anat_2", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        self.assertEqual(ok["action_receipt"]["state"], "PROPOSED")

    def test_16_male_profile_erection_ejaculation_and_refractory_until(self) -> None:
        _open_ready_env(self.root, now=T0, preset="male", user_genitals="vulva_vagina")
        p = apply_adult_intimacy_action(
            self.root,
            {
                "action": "propose_scene",
                "operation_key": "male_p",
                "proposer": "partner",
                "categories": ["KISSING", "MANUAL:user_to_subject", "INTERCOURSE"],
            },
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        # Intercourse before full erection fails with ERECTION_INSUFFICIENT
        with self.assertRaises(AdultIntimacyConflictError) as ectx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "advance_beat", "permit_id": pid, "operation_key": "mb0", "categories": ["INTERCOURSE"]},
                now=T0 + timedelta(seconds=10),
            )
        self.assertEqual(ectx.exception.reason_code, "ERECTION_INSUFFICIENT")

        # Build arousal across beats to full erection (high)
        for idx in range(1, 4):
            res = apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "operation_key": f"mb_{idx}",
                    "categories": ["MANUAL:user_to_subject"],
                },
                now=T0 + timedelta(minutes=idx),
            )
        self.assertEqual(res["body_and_desire"]["erection"], "full")

        # Intercourse + condom + ORGASM triggers ejaculation & refractory_until
        org = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "operation_key": "mb_org",
                "categories": ["INTERCOURSE"],
                "proposed_render": {
                    "subject_orgasm_depicted": True,
                    "proposed_facts": [{"kind": "CONSUME", "item": "condom"}, {"kind": "ORGASM"}],
                },
            },
            now=T0 + timedelta(minutes=5),
        )
        self.assertEqual(org["action_receipt"]["ejaculation_location"], "condom")
        self.assertTrue(org["body_and_desire"]["refractory_active"])
        self.assertIsNotNone(org["body_and_desire"]["refractory_until"])
        self.assertEqual(org["body_and_desire"]["erection"], "none")

        # Second orgasm during refractory is blocked
        with self.assertRaises(AdultIntimacyConflictError) as rctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "operation_key": "mb_org2",
                    "categories": ["MANUAL:user_to_subject"],
                    "proposed_render": {"proposed_facts": [{"kind": "ORGASM"}]},
                },
                now=T0 + timedelta(minutes=6),
            )
        self.assertEqual(rctx.exception.reason_code, "ORGASM_REFRACTORY")

        # After 30 minutes wall-clock, refractory_until naturally expires on read!
        after_30m = read_adult_intimacy(self.root, now=T0 + timedelta(minutes=40))
        self.assertFalse(after_30m["body_and_desire"]["refractory_active"])

    def test_17_female_profile_supports_afterglow_multi_orgasm(self) -> None:
        _open_ready_env(self.root, now=T0, preset="female", user_genitals="penis_testes")
        p = apply_adult_intimacy_action(
            self.root,
            {
                "action": "propose_scene",
                "operation_key": "fem_p",
                "proposer": "partner",
                "categories": ["MANUAL:user_to_subject"],
            },
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        for idx in range(1, 4):
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "operation_key": f"fb_{idx}",
                    "categories": ["MANUAL:user_to_subject"],
                },
                now=T0 + timedelta(minutes=idx),
            )
        first = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "operation_key": "fb_org1",
                "categories": ["MANUAL:user_to_subject"],
                "proposed_render": {"subject_orgasm_depicted": True, "proposed_facts": [{"kind": "ORGASM"}]},
            },
            now=T0 + timedelta(minutes=4),
        )
        self.assertFalse(first["body_and_desire"]["refractory_active"])
        second = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "operation_key": "fb_org2",
                "categories": ["MANUAL:user_to_subject"],
                "proposed_render": {"subject_orgasm_depicted": True, "proposed_facts": [{"kind": "ORGASM"}]},
            },
            now=T0 + timedelta(minutes=5),
        )
        self.assertTrue(second["action_receipt"]["orgasm_recorded"])

    # ------------------------------------------------------------------
    # 18-21: Preference Seed DRAFT -> CONFIRMED & Bilateral Menu Intersection
    # ------------------------------------------------------------------
    def test_18_seed_starts_draft_and_resets_when_pack_changes(self) -> None:
        apply_adult_intimacy_action(
            self.root,
            {
                "action": "configure_adult_gate",
                "adult_confirmed": True,
                "life_stage": "working_adult",
                "attested_by": "deployer",
                "feature_enabled": True,
            },
            now=T0,
        )
        proj = read_adult_intimacy(self.root, now=T0)
        self.assertEqual(proj["preference_menu"]["seed_status"], "DRAFT")
        self.assertEqual(proj["preference_menu"]["effective_menu"]["open"], [])

        apply_adult_intimacy_action(
            self.root,
            {"action": "confirm_preference_seed", "attested_by": "deployer"},
            now=T0,
        )
        self.assertEqual(read_adult_intimacy(self.root, now=T0)["preference_menu"]["seed_status"], "CONFIRMED")

        # Switching character pack resets confirmation back to DRAFT!
        _select_pack_preset(self.root, "sola")
        after_switch = read_adult_intimacy(self.root, now=T0)
        self.assertEqual(after_switch["preference_menu"]["seed_status"], "DRAFT")

    def test_19_anal_toys_moment_transcript_default_closed_and_strict_intersection(self) -> None:
        env = _open_ready_env(self.root, now=T0)
        menu = env["preference_menu"]["effective_menu"]
        self.assertIn("KISSING", menu["open"])
        self.assertIn("ORAL:user_to_subject", menu["ask"])
        self.assertIn("ANAL:user_to_subject", menu["excluded"])
        self.assertIn("TOYS:user_to_subject", menu["excluded"])
        self.assertFalse(env["solo_and_memory"]["private_memory"]["enabled_kinds"]["MOMENT"])
        self.assertFalse(env["solo_and_memory"]["private_memory"]["enabled_kinds"]["TRANSCRIPT"])

    def test_20_mid_scene_partner_limit_narrowing_takes_effect_immediately(self) -> None:
        _open_ready_env(self.root, now=T0)
        p = apply_adult_intimacy_action(
            self.root,
            {
                "action": "propose_scene",
                "operation_key": "nar_1",
                "proposer": "partner",
                "categories": ["KISSING", "MANUAL:user_to_subject"],
            },
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_partner_limits", "stances": {"MANUAL:user_to_subject": "NO"}},
            now=T0 + timedelta(seconds=10),
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "operation_key": "nar_b1",
                    "categories": ["MANUAL:user_to_subject"],
                },
                now=T0 + timedelta(seconds=20),
            )
        self.assertEqual(ctx.exception.reason_code, "SCOPE_NOT_PERMITTED")

    def test_21_subject_preference_widening_requires_capability_and_evidence(self) -> None:
        _open_ready_env(self.root, now=T0)
        with self.assertRaises(AdultIntimacyPermissionError):
            apply_adult_intimacy_action(
                self.root,
                {"action": "revise_subject_preference", "stances": {"ORAL:user_to_subject": "YES"}},
                now=T0,
                subject_view=False,
            )
        with self.assertRaises(AdultIntimacyConflictError) as ectx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "revise_subject_preference", "stances": {"ORAL:user_to_subject": "YES"}},
                now=T0,
                subject_view=True,
            )
        self.assertEqual(ectx.exception.reason_code, "EVIDENCE_REQUIRED_FOR_WIDEN")

        pref = apply_adult_intimacy_action(
            self.root,
            {
                "action": "record_setback_or_query",
                "kind": "preference_note",
                "target_type": "CATEGORY",
                "code": "ORAL:user_to_subject",
                "polarity": "LIKE",
                "source_id": "IX_REFLECTION",
                "context": "OFF_SCENE",
            },
            now=T0,
            subject_view=True,
        )
        mid = pref["action_receipt"]["memory_id"]
        widened = apply_adult_intimacy_action(
            self.root,
            {
                "action": "revise_subject_preference",
                "stances": {"ORAL:user_to_subject": "YES"},
                "evidence_ref": mid,
            },
            now=T0,
            subject_view=True,
        )
        self.assertIn("ORAL:user_to_subject", widened["preference_menu"]["effective_menu"]["open"])

    # ------------------------------------------------------------------
    # 22-25: Private Space, Door/Third-Party, Garments & Post-Scene Clean
    # ------------------------------------------------------------------
    def test_22_private_space_door_open_or_third_party_pauses_active_scene(self) -> None:
        _open_ready_env(self.root, now=T0)
        p = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "sp_1", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        intruded = apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "third_party_present": True},
            now=T0 + timedelta(seconds=15),
        )
        self.assertEqual(intruded["scene_permit"]["active_permit"]["state"], "PAUSED")
        self.assertEqual(intruded["scene_permit"]["active_permit"]["pause_reason"], "PRIVACY_CONDITION_CHANGED")

        with self.assertRaises(AdultIntimacyPermissionError) as tctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "advance_beat", "permit_id": pid, "operation_key": "sp_b1", "categories": ["KISSING"]},
                now=T0 + timedelta(seconds=20),
            )
        self.assertEqual(tctx.exception.reason_code, "THIRD_PARTY_PRESENT")

    def test_23_public_or_outdoor_place_blocks_proposal(self) -> None:
        _open_ready_env(self.root, now=T0)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "place": "public.cafe"},
            now=T0,
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "propose_scene", "operation_key": "pub_1", "proposer": "partner", "categories": ["KISSING"]},
                now=T0,
            )
        self.assertEqual(ctx.exception.reason_code, "PRIVACY_CONDITION_CHANGED")

    def test_24_garment_layer_order_overnight_floor_persistence_and_warm_shower(self) -> None:
        _open_ready_env(self.root, now=T0)
        # Cannot undress inner shirt while cardigan (OUTERWEAR) is still worn
        with self.assertRaises(AdultIntimacyConflictError) as lctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "update_private_space",
                    "garment_op": {"op": "undress", "garment_id": "subject_shirt"},
                },
                now=T0,
            )
        self.assertEqual(lctx.exception.reason_code, "GARMENT_LAYER_BLOCKED")

        # Undress cardigan in order -> placed on bedroom.floor, persists across sleep_completed!
        apply_adult_intimacy_action(
            self.root,
            {
                "action": "update_private_space",
                "garment_op": {"op": "undress", "garment_id": "subject_cardigan", "target": "bedroom.floor"},
            },
            now=T0,
        )
        after_sleep = apply_adult_intimacy_action(
            self.root,
            {"action": "update_body_context", "sleep_completed": True},
            now=T0 + timedelta(hours=8),
        )
        cardigan = next(g for g in after_sleep["private_space"]["garments"] if g["garment_id"] == "subject_cardigan")
        self.assertIsNone(cardigan["worn_by"])
        self.assertEqual(cardigan["placed_at"], "bedroom.floor")

        # Broken water heater blocks warm_shower; working heater restores cleanliness
        apply_adult_intimacy_action(self.root, {"action": "update_private_space", "water_heater_works": False}, now=T0)
        with self.assertRaises(AdultIntimacyConflictError) as wctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "update_private_space", "clean_op": "warm_shower"},
                now=T0,
            )
        self.assertEqual(wctx.exception.reason_code, "WATER_HEATER_BROKEN")

        apply_adult_intimacy_action(self.root, {"action": "update_private_space", "water_heater_works": True}, now=T0)
        cleaned = apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "clean_op": "warm_shower"},
            now=T0,
        )
        self.assertEqual(cleaned["body_and_desire"]["cleanliness"], 1.0)
        self.assertEqual(cleaned["body_and_desire"]["sweat"], 0.0)

    def test_25_consumable_zero_stock_blocks_consumption_without_going_negative(self) -> None:
        _open_ready_env(self.root, now=T0)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_private_space", "restock": {"condom": 0}},
            now=T0,
        )
        p = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "stk_p", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        with self.assertRaises(AdultIntimacyConflictError) as ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "operation_key": "stk_b1",
                    "categories": ["KISSING"],
                    "proposed_render": {"proposed_facts": [{"kind": "CONSUME", "item": "condom"}]},
                },
                now=T0 + timedelta(seconds=10),
            )
        self.assertEqual(ctx.exception.reason_code, "OUT_OF_STOCK:condom")
        self.assertEqual(read_adult_intimacy(self.root, now=T0)["private_space"]["stock"]["condom"], 0)

    # ------------------------------------------------------------------
    # 26-30: Permit Consent, Cooldown Asymmetry, Idle Timeout & Stop Priority
    # ------------------------------------------------------------------
    def test_26_decline_has_zero_penalty_and_subject_hitting_cooldown_never_records_pressure(self) -> None:
        _open_ready_env(self.root, now=T0, allow_subject_initiation=True)
        # Subject proposes -> Partner declines (zero penalty, not quarrel)
        p1 = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "sub_p1", "proposer": "subject", "categories": ["KISSING"]},
            now=T0,
        )
        dec = apply_adult_intimacy_action(
            self.root,
            {
                "action": "respond_scene",
                "permit_id": p1["action_receipt"]["permit_id"],
                "responder": "partner",
                "decision": "NOT_NOW",
            },
            now=T0 + timedelta(seconds=10),
        )
        self.assertEqual(dec["action_receipt"]["relationship_penalty"], 0)
        self.assertFalse(dec["action_receipt"]["quarrel_triggered"])

        # Subject hitting cooldown twice NEVER writes BOUNDARY pressure or S11 candidate!
        for k in (2, 3):
            with self.assertRaises(AdultIntimacyConflictError) as cctx:
                apply_adult_intimacy_action(
                    self.root,
                    {
                        "action": "propose_scene",
                        "operation_key": f"sub_p{k}",
                        "proposer": "subject",
                        "categories": ["KISSING"],
                    },
                    now=T0 + timedelta(minutes=k),
                )
            self.assertEqual(cctx.exception.reason_code, "REINVITE_COOLDOWN")

        subj_proj = read_adult_intimacy(self.root, now=T0 + timedelta(minutes=5), subject_view=True)
        self.assertEqual(subj_proj["solo_and_memory"]["private_memory"]["s11_candidates"], [])
        self.assertEqual(subj_proj["solo_and_memory"]["private_memory"]["entries"], {})

    def test_27_partner_pressing_twice_after_subject_decline_writes_boundary_and_s11_candidate(self) -> None:
        _open_ready_env(self.root, now=T0)
        p1 = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "u_p1", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        apply_adult_intimacy_action(
            self.root,
            {
                "action": "respond_scene",
                "permit_id": p1["action_receipt"]["permit_id"],
                "responder": "subject",
                "decision": "DECLINE",
            },
            now=T0 + timedelta(seconds=5),
        )
        for k in (2, 3):
            with self.assertRaises(AdultIntimacyConflictError):
                apply_adult_intimacy_action(
                    self.root,
                    {
                        "action": "propose_scene",
                        "operation_key": f"u_p{k}",
                        "proposer": "partner",
                        "categories": ["KISSING"],
                    },
                    now=T0 + timedelta(minutes=k),
                )
        subj_proj = read_adult_intimacy(self.root, now=T0 + timedelta(minutes=5), subject_view=True)
        self.assertEqual(len(subj_proj["solo_and_memory"]["private_memory"]["s11_candidates"]), 1)

    def test_28_advance_beat_operation_key_idempotency_and_key_reuse_conflict(self) -> None:
        _open_ready_env(self.root, now=T0)
        p = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "idem_p", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        b1 = apply_adult_intimacy_action(
            self.root,
            {"action": "advance_beat", "permit_id": pid, "operation_key": "beat_k1", "categories": ["KISSING"]},
            now=T0 + timedelta(seconds=10),
        )
        replay = apply_adult_intimacy_action(
            self.root,
            {"action": "advance_beat", "permit_id": pid, "operation_key": "beat_k1", "categories": ["KISSING"]},
            now=T0 + timedelta(seconds=12),
        )
        self.assertTrue(replay["action_receipt"]["idempotent_replay"])
        self.assertEqual(replay["scene_permit"]["active_permit"]["beat_index"], b1["scene_permit"]["active_permit"]["beat_index"])

        with self.assertRaises(AdultIntimacyConflictError) as kctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "operation_key": "beat_k1",
                    "categories": [],
                    "phase": "BUILDING",
                },
                now=T0 + timedelta(seconds=15),
            )
        self.assertEqual(kctx.exception.reason_code, "IDEMPOTENCY_KEY_REUSED")

    def test_29_idle_timeout_600s_pauses_scene_and_dual_confirm_resume_recovers(self) -> None:
        _open_ready_env(self.root, now=T0)
        p = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "idle_p", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        # Zero-write read at +601s projects effective_state=PAUSED
        read_expired = read_adult_intimacy(self.root, now=T0 + timedelta(seconds=601))
        self.assertEqual(read_expired["scene_permit"]["active_permit"]["effective_state"], "PAUSED")
        self.assertTrue(read_expired["scene_permit"]["active_permit"]["idle_expired"])

        # Beat at +605s transitions to PAUSED and raises PERMIT_EXPIRED
        with self.assertRaises(AdultIntimacyConflictError) as ictx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "advance_beat", "permit_id": pid, "operation_key": "idle_b1", "categories": ["KISSING"]},
                now=T0 + timedelta(seconds=605),
            )
        self.assertEqual(ictx.exception.reason_code, "PERMIT_EXPIRED")

        paused = read_adult_intimacy(self.root, now=T0 + timedelta(seconds=606))
        epoch = paused["scene_permit"]["active_permit"]["resume_epoch"]

        # Single party confirm stays PAUSED; both parties confirm -> ACTIVE with incremented epoch
        half = apply_adult_intimacy_action(
            self.root,
            {"action": "confirm_resume", "permit_id": pid, "by": "partner", "resume_epoch": epoch},
            now=T0 + timedelta(seconds=610),
        )
        self.assertEqual(half["scene_permit"]["active_permit"]["state"], "PAUSED")

        full = apply_adult_intimacy_action(
            self.root,
            {"action": "confirm_resume", "permit_id": pid, "by": "subject", "resume_epoch": epoch},
            now=T0 + timedelta(seconds=612),
        )
        self.assertEqual(full["scene_permit"]["active_permit"]["state"], "ACTIVE")
        self.assertEqual(full["scene_permit"]["active_permit"]["resume_epoch"], epoch + 1)

    def test_30_stop_word_whitelist_clarify_and_unconditional_stop_highest_priority(self) -> None:
        _open_ready_env(self.root, now=T0)
        p = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "stp_p", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        # Whitelist "不要停" continues normally
        cont = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "operation_key": "stp_b1",
                "partner_utterance": "不要停",
                "categories": ["KISSING"],
            },
            now=T0 + timedelta(seconds=10),
        )
        self.assertEqual(cont["scene_permit"]["active_permit"]["state"], "ACTIVE")

        # Bare "不要" pauses for clarification
        clar = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "operation_key": "stp_b2",
                "partner_utterance": "不要",
                "categories": ["KISSING"],
            },
            now=T0 + timedelta(seconds=20),
        )
        self.assertEqual(clar["scene_permit"]["active_permit"]["state"], "PAUSED")
        self.assertEqual(clar["scene_permit"]["active_permit"]["pause_reason"], "CLARIFY_NEEDED")

        # Stop word "不要了" wins even from PAUSED / stale revision, transitioning immediately to STOPPED!
        stopped = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "operation_key": "stp_b3",
                "expected_revision": 999,
                "partner_utterance": "不要了，停下",
                "categories": ["KISSING"],
            },
            now=T0 + timedelta(seconds=25),
        )
        self.assertEqual(stopped["action_receipt"]["state"], "STOPPED")
        self.assertTrue(stopped["action_receipt"]["no_interrogation"])
        self.assertTrue(stopped["action_receipt"]["no_grudge"])
        self.assertEqual(stopped["action_receipt"]["relationship_penalty"], 0)

    def test_31_stop_scene_to_aftercare_strips_sexual_categories(self) -> None:
        _open_ready_env(self.root, now=T0)
        p = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "aft_p", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        aft = apply_adult_intimacy_action(
            self.root,
            {"action": "stop_scene", "permit_id": pid, "by": "subject", "transition": "AFTERCARE"},
            now=T0 + timedelta(seconds=15),
        )
        self.assertEqual(aft["scene_permit"]["active_permit"]["state"], "AFTERCARE")
        self.assertEqual(aft["scene_permit"]["active_permit"]["categories"], [])

    # ------------------------------------------------------------------
    # 32-33: Presentation Router, Outbound Channel Guard & Refusal Downgrade
    # ------------------------------------------------------------------
    def test_32_outbound_push_channels_strictly_forbidden_for_intimate_content(self) -> None:
        _open_ready_env(self.root, now=T0)
        for ch in ("qq", "wechat", "telegram", "push_notification", "public_log", "shared_export"):
            with self.assertRaises(AdultIntimacyPermissionError) as ctx:
                apply_adult_intimacy_action(
                    self.root,
                    {
                        "action": "check_outbound_guard",
                        "channel": ch,
                        "contains_intimate_content": True,
                    },
                    now=T0,
                )
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.reason_code, "OUTBOUND_CHANNEL_FORBIDDEN")

        ok = apply_adult_intimacy_action(
            self.root,
            {"action": "check_outbound_guard", "channel": "in_app_private_scene", "contains_intimate_content": True},
            now=T0,
        )
        self.assertTrue(ok["action_receipt"]["allowed"])

    def test_33_upstream_route_refusal_smoothly_downgrades_explicit_suggestive_fade_out(self) -> None:
        _open_ready_env(self.root, now=T0, deploy_tier_cap="EXPLICIT", preferred_tier="EXPLICIT")
        p = apply_adult_intimacy_action(
            self.root,
            {
                "action": "propose_scene",
                "operation_key": "rt_p",
                "proposer": "partner",
                "categories": ["KISSING"],
                "desired_tier": "EXPLICIT",
            },
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        acc = apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        self.assertEqual(acc["presentation_router"]["effective_tier"], "EXPLICIT")

        r1 = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "operation_key": "rt_b1",
                "categories": ["KISSING"],
                "upstream_status": "REFUSED",
            },
            now=T0 + timedelta(seconds=10),
        )
        self.assertFalse(r1["action_receipt"]["adversarial_retry"])
        self.assertEqual(r1["action_receipt"]["downgraded_to_tier"], "SUGGESTIVE")
        self.assertEqual(r1["presentation_router"]["effective_tier"], "SUGGESTIVE")

        r2 = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "operation_key": "rt_b2",
                "categories": ["KISSING"],
                "upstream_status": "REFUSED",
            },
            now=T0 + timedelta(seconds=20),
        )
        self.assertEqual(r2["action_receipt"]["downgraded_to_tier"], "FADE_OUT")
        self.assertEqual(r2["presentation_router"]["effective_tier"], "FADE_OUT")
        self.assertEqual(r2["scene_permit"]["active_permit"]["state"], "ACTIVE")

    # ------------------------------------------------------------------
    # 34-37: Setbacks, Anti-Feigned Orgasm, No Scoring & Outside-Scene Talk
    # ------------------------------------------------------------------
    def test_34_orgasm_feigning_strictly_forbidden_and_truthful_did_you_come_answer(self) -> None:
        _open_ready_env(self.root, now=T0, preset="female")
        p = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "fg_p", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        # Depicting subject orgasm without accepted Body ORGASM fact fails with ORGASM_FEIGNED!
        with self.assertRaises(AdultIntimacyConflictError) as fctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "advance_beat",
                    "permit_id": pid,
                    "operation_key": "fg_b1",
                    "categories": ["KISSING"],
                    "proposed_render": {"subject_orgasm_depicted": True, "proposed_facts": []},
                },
                now=T0 + timedelta(seconds=10),
            )
        self.assertEqual(fctx.exception.reason_code, "ORGASM_FEIGNED")

        # Asking "did you come" answers truthfully NO (never fakes YES!)
        ans = apply_adult_intimacy_action(
            self.root,
            {"action": "record_setback_or_query", "kind": "ask_orgasm", "permit_id": pid, "disclose": True},
            now=T0 + timedelta(seconds=15),
        )
        self.assertEqual(ans["action_receipt"]["answer"], "NO")
        self.assertTrue(ans["action_receipt"]["orgasm_feigning_forbidden"])

    def test_35_setbacks_pain_stops_immediately_and_knock_triggers_privacy_pause(self) -> None:
        _open_ready_env(self.root, now=T0)
        p = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "sb_p", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        # Render cannot fabricate LEG_CRAMP (body-only source)
        with self.assertRaises(AdultIntimacyPermissionError) as mctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "record_setback_or_query", "kind": "mishap", "mishap": "LEG_CRAMP", "source": "render"},
                now=T0 + timedelta(seconds=5),
            )
        self.assertEqual(mctx.exception.reason_code, "MISHAP_SOURCE_FORBIDDEN")

        # KNOCK is never a harmless mishap -> pauses scene with PRIVACY_CONDITION_CHANGED
        with self.assertRaises(AdultIntimacyConflictError) as kctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "record_setback_or_query", "kind": "mishap", "mishap": "KNOCK", "source": "world"},
                now=T0 + timedelta(seconds=8),
            )
        self.assertEqual(kctx.exception.reason_code, "PRIVACY_CONDITION_CHANGED")

        # Pain immediately moves to AFTERCARE with zero blame
        pain_res = apply_adult_intimacy_action(
            self.root,
            {"action": "record_setback_or_query", "kind": "discomfort", "level": "strong", "symptom": "pain"},
            now=T0 + timedelta(seconds=12),
        )
        self.assertEqual(pain_res["action_receipt"]["pace_reaction"], "STOP")
        self.assertFalse(pain_res["action_receipt"]["blame_assigned"])

    def test_36_forbid_scoring_partner_performance_and_s11_count_writes(self) -> None:
        _open_ready_env(self.root, now=T0)
        with self.assertRaises(AdultIntimacyPermissionError) as pctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "record_setback_or_query",
                    "kind": "preference_note",
                    "target_type": "PARTNER_PERFORMANCE",
                    "code": "DURATION",
                    "polarity": "DISLIKE",
                    "source_id": "IX_REFLECTION",
                    "context": "OFF_SCENE",
                },
                now=T0,
            )
        self.assertEqual(pctx.exception.reason_code, "PREF_TARGET_FORBIDDEN")

        with self.assertRaises(AdultIntimacyPermissionError) as sctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "record_setback_or_query",
                    "kind": "s11_write",
                    "s11_field": "session_counts",
                    "s11_entry": {"domain": "intimacy", "basis": "WEEKLY_COUNT"},
                },
                now=T0,
            )
        self.assertEqual(sctx.exception.reason_code, "S11_COUNT_OR_PERFORMANCE_FORBIDDEN")

    def test_37_discuss_intimacy_outside_scene_never_creates_scene_invitation(self) -> None:
        _open_ready_env(self.root, now=T0, deploy_tier_cap="EXPLICIT", preferred_tier="EXPLICIT")
        talk = apply_adult_intimacy_action(
            self.root,
            {
                "action": "discuss_intimacy_outside_scene",
                "speaker": "partner",
                "topic": "WISH",
                "wish_category": "ORAL:user_to_subject",
                "utterance": "平时聊聊彼此对这个的感受",
            },
            now=T0,
        )
        self.assertFalse(talk["action_receipt"]["is_scene_invitation"])
        self.assertFalse(talk["action_receipt"]["permit_created"])
        self.assertFalse(talk["action_receipt"]["stance_mutated"])
        self.assertEqual(talk["action_receipt"]["effective_tier"], "SUGGESTIVE")
        self.assertEqual(talk["action_receipt"]["wish_answer"], "ASK_IN_MOMENT")
        self.assertIsNone(talk["scene_permit"]["active_permit"])

    # ------------------------------------------------------------------
    # 38-42: Solo Memoryless Partition, Partner Redaction, Memory Erasure & Validation
    # ------------------------------------------------------------------
    def test_38_solo_memoryless_probability_and_partner_online_input_forbidden(self) -> None:
        _open_ready_env(self.root, now=T0, solo_enabled=True, solo_inclination="high")
        # Probability depends ONLY on inclination & desire_ratio, never elapsed time!
        p_a = solo_window_probability("high", 1.0)
        p_b = solo_window_probability("high", 1.0)
        self.assertEqual(p_a, p_b)

        with self.assertRaises(AdultIntimacyPermissionError) as octx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "evaluate_solo_window", "slot_id": 1, "partner_online": False},
                now=T0,
            )
        self.assertEqual(octx.exception.reason_code, "SOLO_PARTNER_ONLINE_INPUT_FORBIDDEN")

    def test_39_solo_execution_redacted_in_partner_view_and_gated_mention(self) -> None:
        _open_ready_env(self.root, now=T0, preset="female", solo_enabled=True, solo_inclination="high")
        raw_state = aiv._read_stored_state_zero_write(self.root, aiv._load_character_context(self.root))[0]
        hit_slot = find_matching_solo_slot(raw_state["instance_seed"], 0.10)
        self.assertIsNotNone(hit_slot)

        partner_res = apply_adult_intimacy_action(
            self.root,
            {"action": "evaluate_solo_window", "slot_id": hit_slot, "co_presence": "NONE", "alone": True},
            now=T0,
            subject_view=False,
        )
        self.assertTrue(partner_res["action_receipt"]["triggered"])
        # Partner default view MUST NOT see solo_ledger or entry IDs!
        self.assertNotIn("solo_ledger", partner_res["solo_and_memory"]["solo"])
        self.assertTrue(partner_res["solo_and_memory"]["solo"]["solo_partition_redacted"])
        self.assertNotIn("solo_entry_id", partner_res["action_receipt"])

        # Subject view sees isolated SOLO ledger, and SOLO never leaks into private_memory context_refs!
        subj_res = read_adult_intimacy(self.root, now=T0, subject_view=True)
        self.assertEqual(len(subj_res["solo_and_memory"]["solo"]["solo_ledger"]), 1)
        self.assertEqual(subj_res["solo_and_memory"]["private_memory"]["context_refs"], [])

        # Mention blocked while solo_talk=False; allowed once solo_talk=True, but blocked with PROPOSE!
        with self.assertRaises(AdultIntimacyConflictError) as mctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "query_or_mention_solo", "mode": "mention", "co_presence": "ACTIVE"},
                now=T0,
            )
        self.assertEqual(mctx.exception.reason_code, "MENTION_NOT_ALLOWED")

        apply_adult_intimacy_action(self.root, {"action": "update_partner_limits", "solo_talk": True}, now=T0)
        with self.assertRaises(AdultIntimacyConflictError) as mp_ctx:
            apply_adult_intimacy_action(
                self.root,
                {
                    "action": "query_or_mention_solo",
                    "mode": "mention",
                    "co_presence": "ACTIVE",
                    "accompanied_expressions": ["PROPOSE_SEXUAL_ACTIVITY"],
                },
                now=T0,
            )
        self.assertEqual(mp_ctx.exception.reason_code, "MENTION_WITH_PROPOSE")

        mentioned = apply_adult_intimacy_action(
            self.root,
            {"action": "query_or_mention_solo", "mode": "mention", "co_presence": "ACTIVE"},
            now=T0,
        )
        self.assertEqual(mentioned["action_receipt"]["effective_tier"], "SUGGESTIVE")
        self.assertIn("day_bucket", mentioned["action_receipt"]["coarse_facts"])

    def test_40_private_memory_isolation_transcript_excluded_and_irreversible_erasure(self) -> None:
        _open_ready_env(self.root, now=T0)
        apply_adult_intimacy_action(
            self.root,
            {"action": "update_partner_limits", "save_moment": True, "save_transcript": True},
            now=T0,
        )
        m_pref = apply_adult_intimacy_action(
            self.root,
            {"action": "record_private_memory", "kind": "PREFERENCE", "text": "偏爱暖光安静氛围", "custom_id": "mem_pref_1"},
            now=T0,
        )
        m_tr = apply_adult_intimacy_action(
            self.root,
            {"action": "record_private_memory", "kind": "TRANSCRIPT", "text": "private_beat_log", "custom_id": "mem_tr_1"},
            now=T0,
        )
        self.assertTrue(m_pref["action_receipt"]["stored"])
        self.assertTrue(m_tr["action_receipt"]["stored"])

        # Partner default view redacts private entries; Subject view excludes TRANSCRIPT from context_refs
        p_view = read_adult_intimacy(self.root, now=T0, subject_view=False)
        self.assertNotIn("entries", p_view["solo_and_memory"]["private_memory"])
        s_view = read_adult_intimacy(self.root, now=T0, subject_view=True)
        self.assertIn("mem_pref_1", s_view["solo_and_memory"]["private_memory"]["context_refs"])
        self.assertNotIn("mem_tr_1", s_view["solo_and_memory"]["private_memory"]["context_refs"])

        # Permanent erasure tombstones IDs so they NEVER resurrect!
        apply_adult_intimacy_action(
            self.root,
            {"action": "erase_private_memory", "scope": "all_intimate", "by": "partner"},
            now=T0 + timedelta(minutes=1),
        )
        s_after = read_adult_intimacy(self.root, now=T0 + timedelta(minutes=1), subject_view=True)
        self.assertEqual(s_after["solo_and_memory"]["private_memory"]["entries"], {})

        with self.assertRaises(AdultIntimacyConflictError) as tctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "record_private_memory", "kind": "PREFERENCE", "text": "resurrect", "custom_id": "mem_pref_1"},
                now=T0 + timedelta(minutes=2),
            )
        self.assertEqual(tctx.exception.reason_code, "MEMORY_TOMBSTONED_CANNOT_RESURRECT")

    def test_41_one_click_revoke_intimacy_consent_stops_scene_and_wipes_partition(self) -> None:
        _open_ready_env(self.root, now=T0)
        p = apply_adult_intimacy_action(
            self.root,
            {"action": "propose_scene", "operation_key": "rev_p", "proposer": "partner", "categories": ["KISSING"]},
            now=T0,
        )
        pid = p["action_receipt"]["permit_id"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=T0,
        )
        rev = apply_adult_intimacy_action(
            self.root,
            {"action": "revoke_intimacy_consent", "by": "partner", "erase_memory": True, "close_adult_gate": True},
            now=T0 + timedelta(seconds=30),
        )
        self.assertEqual(rev["action_receipt"]["aborted_permit_id"], pid)
        self.assertFalse(rev["adult_gate"]["gate_open"])
        self.assertIsNone(rev["scene_permit"]["active_permit"])
        self.assertEqual(rev["preference_menu"]["effective_menu"]["open"], [])

    def test_42_strict_unknown_field_empty_foreign_field_and_404_errors(self) -> None:
        with self.assertRaises(AdultIntimacyValidationError) as uctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "update_partner_limits", "preferred_tier": "SUGGESTIVE", "unexpected_key": 123},
                now=T0,
            )
        self.assertEqual(uctx.exception.status_code, 400)
        self.assertTrue(uctx.exception.reason_code.startswith("UNKNOWN_FIELD:"))

        with self.assertRaises(AdultIntimacyValidationError) as ectx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "update_partner_limits", "preferred_tier": "SUGGESTIVE", "foreign_blank": ""},
                now=T0,
            )
        self.assertEqual(ectx.exception.status_code, 400)
        self.assertTrue(ectx.exception.reason_code.startswith("EMPTY_FOREIGN_FIELD:"))

        with self.assertRaises(AdultIntimacyValidationError) as fctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "update_partner_limits", "subject_view": True},
                now=T0,
            )
        self.assertTrue(fctx.exception.reason_code.startswith("FOREIGN_FIELD_FORBIDDEN:"))

        with self.assertRaises(AdultIntimacyNotFoundError) as nctx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "stop_scene", "permit_id": "nonexistent_permit", "by": "partner"},
                now=T0,
            )
        self.assertEqual(nctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

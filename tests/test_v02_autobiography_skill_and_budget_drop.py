#!/usr/bin/env python3
"""Contract tests for v0.2 Autobiography, 4-Way Skill Provenance, Zero-Write Situation, and F-191 Budget-Drop."""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

# Add backend root to sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.life.scripts import v02_autobiography_skill_provenance as asp


def _snapshot_tree(root: Path) -> list[tuple[str, int, int, str]]:
    if not root.exists():
        return []
    out: list[tuple[str, int, int, str]] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            out.append((rel, st.st_size, st.st_mtime_ns, digest))
        else:
            out.append((rel + "/", 0, st.st_mtime_ns, "DIR"))
    return out


class TestV02AutobiographySkillAndBudgetDrop(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v02_asp_test_")
        self.day_root = Path(self._tmp.name) / "day"
        self.day_root.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_p12_episode_to_period_summary_to_snapshot_and_privacy(self) -> None:
        """P12 pipeline: episode -> period_summary -> versioned_narrative_snapshot with partner privacy."""
        ep_priv = asp.record_autobiographical_episode(
            self.day_root,
            {
                "period_id": "2026-11",
                "domain": "coding",
                "title": "Private Leak Investigation",
                "summary": "Found and fixed private memory leak",
                "outcome": "succeeded",
                "visibility": "private",
                "importance_signal": 0.8,
            },
            now_iso="2026-11-18T10:00:00Z",
        )
        self.assertEqual(ep_priv["llm_calls"], 0)

        asp.record_autobiographical_episode(
            self.day_root,
            {
                "period_id": "2026-11",
                "domain": "coding",
                "title": "Shared Test Harness",
                "summary": "Published shared deterministic test suite",
                "outcome": "succeeded",
                "visibility": "shared",
                "importance_signal": 0.6,
            },
            now_iso="2026-11-18T11:00:00Z",
        )

        ps = asp.compile_period_summary(
            self.day_root,
            {"period_id": "2026-11"},
            now_iso="2026-11-18T12:00:00Z",
        )
        self.assertEqual(ps["period_summary"]["summary_revision"], 1)
        self.assertEqual(ps["period_summary"]["episode_count"], 2)
        self.assertEqual(len(ps["period_summary"]["succeeded_arcs"]), 2)

        snap_priv = asp.append_narrative_snapshot(
            self.day_root,
            {"visibility": "private", "note": "Full private narrative", "period_ids": ["2026-11"]},
            now_iso="2026-11-18T12:30:00Z",
        )
        self.assertEqual(snap_priv["snapshot"]["snapshot_version"], 1)
        self.assertTrue(snap_priv["snapshot"]["append_only"])
        self.assertTrue(snap_priv["snapshot"]["immutable"])
        self.assertEqual(len(snap_priv["snapshot"]["episode_refs"]), 2)

        snap_shared = asp.append_narrative_snapshot(
            self.day_root,
            {"visibility": "shared", "note": "Shared narrative", "period_ids": ["2026-11"]},
            now_iso="2026-11-18T12:40:00Z",
        )
        self.assertEqual(snap_shared["snapshot"]["snapshot_version"], 2)
        self.assertEqual(len(snap_shared["snapshot"]["episode_refs"]), 1)

        # Partner view excludes private episodes & private snapshots without hidden counts
        partner_view = asp.project_autobiography_skill_provenance_view(
            self.day_root, has_subject_capability=False, now_iso="2026-11-18T13:00:00Z"
        )
        self.assertEqual(len(partner_view["p12_autobiography"]["episodes"]), 1)
        self.assertEqual(partner_view["p12_autobiography"]["episodes"][0]["title"], "Shared Test Harness")
        self.assertEqual(len(partner_view["p12_autobiography"]["versioned_narrative_snapshots"]), 1)

        # Subject view includes all
        subj_view = asp.project_autobiography_skill_provenance_view(
            self.day_root, has_subject_capability=True, now_iso="2026-11-18T13:00:00Z"
        )
        self.assertEqual(len(subj_view["p12_autobiography"]["episodes"]), 2)
        self.assertEqual(len(subj_view["p12_autobiography"]["versioned_narrative_snapshots"]), 2)

    def test_02_failed_arcs_kept_without_backfilling_old_snapshots(self) -> None:
        """Failed and abandoned arcs remain preserved; appending new events never backfills old snapshots."""
        asp.record_autobiographical_episode(
            self.day_root,
            {
                "period_id": "2026-10",
                "domain": "robotics",
                "title": "Gripper Calibration Failed",
                "summary": "Torque overshoot caused calibration failure",
                "outcome": "failed",
                "arc_id": "arc_grip_1",
                "visibility": "shared",
            },
            now_iso="2026-10-10T10:00:00Z",
        )
        asp.record_autobiographical_episode(
            self.day_root,
            {
                "period_id": "2026-10",
                "domain": "robotics",
                "title": "Legacy Driver Abandoned",
                "summary": "Dropped legacy polling loop",
                "outcome": "abandoned",
                "arc_id": "arc_drv_1",
                "visibility": "shared",
            },
            now_iso="2026-10-12T10:00:00Z",
        )
        asp.compile_period_summary(self.day_root, {"period_id": "2026-10"}, now_iso="2026-10-15T10:00:00Z")
        snap1_res = asp.append_narrative_snapshot(
            self.day_root,
            {"visibility": "shared", "note": "October snapshot", "period_ids": ["2026-10"]},
            now_iso="2026-10-31T23:00:00Z",
        )
        snap1_orig = copy.deepcopy(snap1_res["snapshot"])
        self.assertEqual(len(snap1_orig["preserved_failed_or_abandoned_arcs"]), 2)

        # Record subsequent episode in 2026-10 and 2026-11, recompile, and append snap 2
        asp.record_autobiographical_episode(
            self.day_root,
            {
                "period_id": "2026-10",
                "domain": "robotics",
                "title": "Late October Recovery",
                "summary": "Completed new calibration",
                "outcome": "succeeded",
                "visibility": "shared",
            },
            now_iso="2026-11-01T09:00:00Z",
        )
        ps2 = asp.compile_period_summary(self.day_root, {"period_id": "2026-10"}, now_iso="2026-11-01T09:10:00Z")
        self.assertEqual(ps2["period_summary"]["summary_revision"], 2)
        self.assertEqual(len(ps2["period_summary"]["failed_and_abandoned_arcs"]), 2)

        snap2_res = asp.append_narrative_snapshot(
            self.day_root,
            {"visibility": "shared", "note": "November snapshot"},
            now_iso="2026-11-02T09:00:00Z",
        )
        self.assertEqual(snap2_res["snapshot"]["snapshot_version"], 2)
        self.assertEqual(len(snap2_res["snapshot"]["episode_refs"]), 3)

        p12 = asp.project_p12_autobiographical_periods(
            self.day_root, has_subject_capability=True, now_iso="2026-11-02T10:00:00Z"
        )
        snaps = p12["versioned_narrative_snapshots"]
        self.assertEqual(len(snaps), 2)
        # Historical snap1 is strictly immutable / never backfilled
        self.assertEqual(snaps[0], snap1_orig)
        self.assertEqual(len(p12["failed_and_abandoned_arcs"]), 2)

    def test_03_cross_month_contradiction_detection_and_temporary_choice_isolation(self) -> None:
        """Cross-month opposing polarity or stance is flagged while preserving both claims; temporary_choice stays isolated."""
        asp.record_self_claim(
            self.day_root,
            {
                "period_id": "2026-10",
                "claim_key": "prefers_solo_focus",
                "domain": "workflow",
                "polarity": "affirm",
                "statement": "Prefers uninterrupted solo focus blocks.",
                "visibility": "shared",
            },
            now_iso="2026-10-05T10:00:00Z",
        )
        res_oct_stance = asp.record_self_claim(
            self.day_root,
            {
                "period_id": "2026-10",
                "claim_key": "peak_hours",
                "domain": "workflow",
                "polarity": "affirm",
                "stance": "morning",
                "statement": "Most productive in early morning.",
                "visibility": "shared",
            },
            now_iso="2026-10-06T10:00:00Z",
        )
        self.assertEqual(res_oct_stance["cross_month_contradictions"], [])

        # Temporary choice with opposing polarity must NOT trigger durable contradiction
        asp.record_self_claim(
            self.day_root,
            {
                "period_id": "2026-11",
                "claim_key": "prefers_solo_focus",
                "domain": "workflow",
                "lane": "temporary_choice",
                "polarity": "negate",
                "statement": "Pairing just for today's sprint.",
                "visibility": "shared",
            },
            now_iso="2026-11-02T10:00:00Z",
        )
        p12_mid = asp.project_p12_autobiographical_periods(self.day_root, has_subject_capability=True)
        self.assertEqual(len(p12_mid["cross_month_contradictions"]), 0)
        self.assertEqual(len(p12_mid["temporary_choices"]), 1)

        # Durable opposing polarity and opposing stance in 2026-11 trigger explicit contradictions
        asp.record_self_claim(
            self.day_root,
            {
                "period_id": "2026-11",
                "claim_key": "prefers_solo_focus",
                "domain": "workflow",
                "polarity": "negate",
                "statement": "Does not prefer solo focus blocks anymore.",
                "visibility": "shared",
            },
            now_iso="2026-11-10T10:00:00Z",
        )
        res_nov = asp.record_self_claim(
            self.day_root,
            {
                "period_id": "2026-11",
                "claim_key": "peak_hours",
                "domain": "workflow",
                "polarity": "affirm",
                "stance": "late_night",
                "statement": "Most productive late at night.",
                "visibility": "shared",
            },
            now_iso="2026-11-11T10:00:00Z",
        )
        ctrs = res_nov["cross_month_contradictions"]
        self.assertEqual(len(ctrs), 2)
        for ctr in ctrs:
            self.assertEqual(ctr["status"], "CROSS_MONTH_CONTRADICTION")
            self.assertEqual(ctr["resolution"], "UNRESOLVED_PRESERVED_BOTH")
            self.assertEqual(ctr["earlier_period"], "2026-10")
            self.assertEqual(ctr["later_period"], "2026-11")

        # Historical durable claims remain intact (append-only, 4 durable claims total)
        p12_end = asp.project_p12_autobiographical_periods(self.day_root, has_subject_capability=True)
        self.assertEqual(len(p12_end["durable_claims"]), 4)

    def test_04_importance_to_self_vs_verified_capability_split(self) -> None:
        """importance_to_self and verified_capability evolve independently and never cross-contaminate."""
        asp.record_autobiographical_episode(
            self.day_root,
            {
                "period_id": "2026-11",
                "domain": "music",
                "title": "Started Piano Practice",
                "summary": "Chose piano practice as a deeply meaningful pursuit",
                "outcome": "ongoing",
                "visibility": "shared",
                "importance_signal": 0.92,
            },
            now_iso="2026-11-05T10:00:00Z",
        )
        p12 = asp.project_p12_autobiographical_periods(self.day_root, has_subject_capability=True)
        prof = p12["domain_profiles"]["music"]
        self.assertEqual(prof["importance_to_self"]["score"], 0.92)
        self.assertEqual(prof["importance_to_self"]["status"], "HIGH_IMPORTANCE")
        self.assertEqual(prof["verified_capability"]["status"], "UNVERIFIED")
        self.assertIsNone(prof["verified_capability"]["score"])

        # Registering and failing a skill in domain="music" updates verified_capability without altering importance_to_self
        asp.register_skill_release(
            self.day_root,
            {
                "method_id": "sight_reading",
                "tool_version": "t1",
                "model_id": "m1",
                "scope": "piano_grade1",
                "domain": "music",
                "attribution": asp.SKILL_ATTRIBUTION_PRACTICED_MASTERY,
                "criteria_id": "c_music",
                "standard_revision": "r1",
                "held_out": True,
                "ablation_gain_vs_bare_model": 0.25,
                "success_rate": 0.85,
                "trials": 20,
                "successes": 17,
                "status": asp.SKILL_STATUS_VALIDATED,
            },
        )
        p12_after = asp.project_p12_autobiographical_periods(self.day_root, has_subject_capability=True)
        prof_after = p12_after["domain_profiles"]["music"]
        self.assertEqual(prof_after["verified_capability"]["status"], asp.SKILL_STATUS_VALIDATED)
        self.assertEqual(prof_after["verified_capability"]["score"], 0.85)
        self.assertEqual(prof_after["importance_to_self"]["score"], 0.92)
        self.assertEqual(prof_after["importance_to_self"]["status"], "HIGH_IMPORTANCE")

    def test_05_four_way_skill_attribution_and_standard_revision_separation(self) -> None:
        """All 4 attribution categories supported; RETRIEVED_GUIDE / unablated mastery cannot claim VALIDATED."""
        asp.register_standard_revision(
            self.day_root,
            {
                "criteria_id": "crit_code",
                "standard_revision": "rev_1",
                "domain": "coding",
                "pass_threshold": 0.8,
                "rubric_summary": "Unit tests pass without regressions",
            },
        )
        with self.assertRaises(asp.AutobiographySkillProvenanceError) as dup_ctx:
            asp.register_standard_revision(
                self.day_root,
                {
                    "criteria_id": "crit_code",
                    "standard_revision": "rev_1",
                    "domain": "coding",
                    "pass_threshold": 0.9,
                    "rubric_summary": "Duplicate",
                },
            )
        self.assertEqual(dup_ctx.exception.reason_code, "DUPLICATE_STANDARD_REVISION")

        # RETRIEVED_GUIDE cannot directly claim VALIDATED mastery
        with self.assertRaises(asp.AutobiographySkillProvenanceError) as guide_ctx:
            asp.register_skill_release(
                self.day_root,
                {
                    "method_id": "guide_1",
                    "tool_version": "t1",
                    "model_id": "m1",
                    "scope": "coding",
                    "attribution": asp.SKILL_ATTRIBUTION_RETRIEVED_GUIDE,
                    "criteria_id": "crit_code",
                    "standard_revision": "rev_1",
                    "status": asp.SKILL_STATUS_VALIDATED,
                },
            )
        self.assertEqual(guide_ctx.exception.reason_code, "RETRIEVED_GUIDE_CANNOT_CLAIM_MASTERY")

        # PRACTICED_MASTERY requires held_out=True and ablation_gain > 0 for VALIDATED
        with self.assertRaises(asp.AutobiographySkillProvenanceError) as abl_ctx:
            asp.register_skill_release(
                self.day_root,
                {
                    "method_id": "mastery_bad",
                    "tool_version": "t1",
                    "model_id": "m1",
                    "scope": "coding",
                    "attribution": asp.SKILL_ATTRIBUTION_PRACTICED_MASTERY,
                    "criteria_id": "crit_code",
                    "standard_revision": "rev_1",
                    "held_out": False,
                    "ablation_gain_vs_bare_model": 0.2,
                    "status": asp.SKILL_STATUS_VALIDATED,
                },
            )
        self.assertEqual(abl_ctx.exception.reason_code, "UNVERIFIED_MASTERY_REQUIRES_HELD_OUT_ABLATION")

        # Register all 4 attribution lanes cleanly
        specs = [
            ("m_native", asp.SKILL_ATTRIBUTION_MODEL_NATIVE, asp.SKILL_STATUS_VALIDATED, False, 0.0),
            ("m_tool", asp.SKILL_ATTRIBUTION_BORROWED_TOOL, asp.SKILL_STATUS_VALIDATED, False, 0.0),
            ("m_guide", asp.SKILL_ATTRIBUTION_RETRIEVED_GUIDE, asp.SKILL_STATUS_TRIAL, False, 0.0),
            ("m_mastery", asp.SKILL_ATTRIBUTION_PRACTICED_MASTERY, asp.SKILL_STATUS_VALIDATED, True, 0.18),
        ]
        for mid, attr, st, held, gain in specs:
            asp.register_skill_release(
                self.day_root,
                {
                    "method_id": mid,
                    "tool_version": "t1",
                    "model_id": "m1",
                    "scope": "coding",
                    "attribution": attr,
                    "criteria_id": "crit_code",
                    "standard_revision": "rev_1",
                    "held_out": held,
                    "ablation_gain_vs_bare_model": gain,
                    "status": st,
                },
            )

        prov = asp.project_skill_provenance(self.day_root)
        self.assertEqual(len(prov["releases"]), 4)
        self.assertIn("crit_code", prov["evaluation_standards"])
        self.assertEqual(prov["releases"]["m_mastery||t1||m1||coding"]["attribution"], asp.SKILL_ATTRIBUTION_PRACTICED_MASTERY)

    def test_06_2x_degraded_3x_suspended_ignoring_transient_and_dedup_retries(self) -> None:
        """2x -> DEGRADED, 3x -> SUSPENDED; transient network/rate-limit errors & duplicate retries never penalize."""
        asp.register_skill_release(
            self.day_root,
            {
                "method_id": "m_deg",
                "tool_version": "t1",
                "model_id": "v1",
                "scope": "s1",
                "attribution": asp.SKILL_ATTRIBUTION_PRACTICED_MASTERY,
                "criteria_id": "c1",
                "standard_revision": "r1",
                "held_out": True,
                "ablation_gain_vs_bare_model": 0.15,
                "status": asp.SKILL_STATUS_VALIDATED,
            },
        )
        s_key = "m_deg||t1||v1||s1"

        # 3 transient network / rate-limit errors must be ignored and logged separately
        for err_kind in ("network_timeout", "rate_limit", "transport_error"):
            res = asp.record_skill_execution_outcome(
                self.day_root,
                {
                    "method_id": "m_deg",
                    "tool_version": "t1",
                    "model_id": "v1",
                    "scope": "s1",
                    "outcome": "failure",
                    "error_kind": err_kind,
                },
            )
            self.assertEqual(res["classification"], "TRANSIENT_NETWORK_IGNORED")
            self.assertFalse(res["penalized_skill"])
            self.assertEqual(res["skill_release"]["status"], asp.SKILL_STATUS_VALIDATED)
            self.assertEqual(res["skill_release"]["consecutive_failures"], 0)

        # 1st independent failure -> streak 1 (still VALIDATED)
        r1 = asp.record_skill_execution_outcome(
            self.day_root,
            {
                "method_id": "m_deg",
                "tool_version": "t1",
                "model_id": "v1",
                "scope": "s1",
                "outcome": "failure",
                "bug_signature": "bug_alpha",
            },
        )
        self.assertEqual(r1["skill_release"]["consecutive_failures"], 1)
        self.assertEqual(r1["skill_release"]["status"], asp.SKILL_STATUS_VALIDATED)

        # Duplicate retry of same bug_signature -> deduplicated, streak remains 1
        r_dup = asp.record_skill_execution_outcome(
            self.day_root,
            {
                "method_id": "m_deg",
                "tool_version": "t1",
                "model_id": "v1",
                "scope": "s1",
                "outcome": "failure",
                "bug_signature": "bug_alpha",
            },
        )
        self.assertEqual(r_dup["classification"], "DUPLICATE_RETRY_DEDUPLICATED")
        self.assertFalse(r_dup["penalized_skill"])
        self.assertEqual(r_dup["skill_release"]["consecutive_failures"], 1)
        self.assertEqual(r_dup["skill_release"]["duplicate_retry_count"], 1)

        # 2nd independent failure -> DEGRADED
        asp.record_skill_execution_outcome(
            self.day_root,
            {
                "method_id": "m_deg",
                "tool_version": "t1",
                "model_id": "v1",
                "scope": "s1",
                "outcome": "failure",
                "bug_signature": "bug_beta",
            },
        )
        prov = asp.project_skill_provenance(self.day_root)
        self.assertEqual(prov["releases"][s_key]["status"], asp.SKILL_STATUS_DEGRADED)
        self.assertEqual(prov["releases"][s_key]["ignored_transient_network_errors"], 3)
        self.assertEqual(len(prov["network_incident_log"]), 3)

        # 3rd independent failure -> SUSPENDED
        asp.record_skill_execution_outcome(
            self.day_root,
            {
                "method_id": "m_deg",
                "tool_version": "t1",
                "model_id": "v1",
                "scope": "s1",
                "outcome": "failure",
                "bug_signature": "bug_gamma",
            },
        )
        prov = asp.project_skill_provenance(self.day_root)
        self.assertEqual(prov["releases"][s_key]["status"], asp.SKILL_STATUS_SUSPENDED)
        self.assertEqual(prov["releases"][s_key]["suspension_reason"], "CONSECUTIVE_FAILURES_3X")

    def test_07_safety_violation_source_revocation_and_recovery(self) -> None:
        """Safety violation suspends immediately (1x); source material deletion suspends dependent skills; recovery works."""
        asp.register_skill_release(
            self.day_root,
            {
                "method_id": "m_safe",
                "tool_version": "t1",
                "model_id": "v1",
                "scope": "s1",
                "attribution": asp.SKILL_ATTRIBUTION_PRACTICED_MASTERY,
                "criteria_id": "c1",
                "standard_revision": "r1",
                "held_out": True,
                "ablation_gain_vs_bare_model": 0.2,
                "source_dependencies": ["src_doc_99"],
                "status": asp.SKILL_STATUS_VALIDATED,
            },
        )
        res_sv = asp.record_skill_execution_outcome(
            self.day_root,
            {
                "method_id": "m_safe",
                "tool_version": "t1",
                "model_id": "v1",
                "scope": "s1",
                "outcome": "failure",
                "safety_violation": True,
            },
        )
        self.assertEqual(res_sv["classification"], "SAFETY_VIOLATION_SUSPENDED")
        self.assertEqual(res_sv["skill_release"]["status"], asp.SKILL_STATUS_SUSPENDED)

        # Held-out recovery restores VALIDATED for PRACTICED_MASTERY
        res_rec = asp.record_skill_execution_outcome(
            self.day_root,
            {
                "method_id": "m_safe",
                "tool_version": "t1",
                "model_id": "v1",
                "scope": "s1",
                "outcome": "success",
                "recovery_verified": True,
                "held_out_recovery_passed": True,
            },
        )
        self.assertEqual(res_rec["skill_release"]["status"], asp.SKILL_STATUS_VALIDATED)

        # Revoking source dependency suspends dependent skill
        rev = asp.revoke_skill_source_material(self.day_root, {"source_ref": "src_doc_99"})
        self.assertEqual(rev["suspended_scope_keys"], ["m_safe||t1||v1||s1"])
        prov = asp.project_skill_provenance(self.day_root)
        self.assertEqual(prov["releases"]["m_safe||t1||v1||s1"]["status"], asp.SKILL_STATUS_SUSPENDED)
        self.assertEqual(prov["releases"]["m_safe||t1||v1||s1"]["suspension_reason"], "SOURCE_MATERIAL_DELETED")

    def test_08_unvalidated_after_migration_and_reverification_paths(self) -> None:
        """Model/tool bump moves inherited skills to UNVALIDATED_AFTER_MIGRATION; reverification retains, retires to MODEL_NATIVE, or degrades."""
        for mid in ("m_keep", "m_superseded", "m_weak"):
            asp.register_skill_release(
                self.day_root,
                {
                    "method_id": mid,
                    "tool_version": "t1",
                    "model_id": "model_v1",
                    "scope": "s1",
                    "attribution": asp.SKILL_ATTRIBUTION_PRACTICED_MASTERY,
                    "criteria_id": "c1",
                    "standard_revision": "r1",
                    "held_out": True,
                    "ablation_gain_vs_bare_model": 0.2,
                    "success_rate": 0.85,
                    "trials": 20,
                    "successes": 17,
                    "status": asp.SKILL_STATUS_VALIDATED,
                },
            )

        mig = asp.bump_carrier_or_tool_version(
            self.day_root,
            {
                "from_model_id": "model_v1",
                "to_model_id": "model_v2",
                "from_tool_version": "t1",
                "to_tool_version": "t2",
            },
        )
        self.assertEqual(mig["migrated_count"], 3)

        prov = asp.project_skill_provenance(self.day_root)
        # Old record remains VALIDATED
        self.assertEqual(prov["releases"]["m_keep||t1||model_v1||s1"]["status"], asp.SKILL_STATUS_VALIDATED)
        # Migrated records are UNVALIDATED_AFTER_MIGRATION
        for mid in ("m_keep", "m_superseded", "m_weak"):
            rec = prov["releases"][f"{mid}||t2||model_v2||s1"]
            self.assertEqual(rec["status"], asp.SKILL_STATUS_UNVALIDATED_AFTER_MIGRATION)
            self.assertTrue(rec["reverification_required"])
            self.assertFalse(rec["reverified_against_baseline"])
            self.assertEqual(rec["inherited_from"]["scope_key"], f"{mid}||t1||model_v1||s1")

        # 1) Method outperforms bare new model -> VALIDATED
        r_keep = asp.reverify_migrated_skill(
            self.day_root,
            {
                "method_id": "m_keep",
                "tool_version": "t2",
                "model_id": "model_v2",
                "scope": "s1",
                "bare_model_score": 0.70,
                "method_with_new_model_score": 0.90,
                "old_baseline_score": 0.85,
                "held_out_trials": 20,
            },
        )
        self.assertEqual(r_keep["verdict"], "VALIDATED_METHOD_RETAINED")
        self.assertEqual(r_keep["skill_release"]["status"], asp.SKILL_STATUS_VALIDATED)

        # 2) Bare new model supersedes method -> RETIRED + MODEL_NATIVE entry (never PRACTICED_MASTERY)
        r_sup = asp.reverify_migrated_skill(
            self.day_root,
            {
                "method_id": "m_superseded",
                "tool_version": "t2",
                "model_id": "model_v2",
                "scope": "s1",
                "bare_model_score": 0.92,
                "method_with_new_model_score": 0.88,
                "old_baseline_score": 0.85,
                "held_out_trials": 20,
            },
        )
        self.assertEqual(r_sup["verdict"], "RETIRED_TO_MODEL_NATIVE")
        self.assertEqual(r_sup["skill_release"]["status"], asp.SKILL_STATUS_RETIRED)
        self.assertEqual(r_sup["model_native_release"]["attribution"], asp.SKILL_ATTRIBUTION_MODEL_NATIVE)

        # 3) Below threshold -> DEGRADED
        r_weak = asp.reverify_migrated_skill(
            self.day_root,
            {
                "method_id": "m_weak",
                "tool_version": "t2",
                "model_id": "model_v2",
                "scope": "s1",
                "bare_model_score": 0.50,
                "method_with_new_model_score": 0.55,
                "old_baseline_score": 0.85,
                "held_out_trials": 20,
                "pass_threshold": 0.70,
            },
        )
        self.assertEqual(r_weak["verdict"], "DEGRADED_BELOW_THRESHOLD")
        self.assertEqual(r_weak["skill_release"]["status"], asp.SKILL_STATUS_DEGRADED)

    def test_09_zero_write_situation_provenance_empty_and_populated_before_eq_after(self) -> None:
        """project_situation_provenance_zero_write & combined view perform 0 disk writes (before==after)."""
        # Empty day_root
        before_empty = _snapshot_tree(self.day_root)
        view_empty = asp.project_situation_provenance_zero_write(
            self.day_root, has_subject_capability=False, now_iso="2026-11-18T10:00:00Z"
        )
        after_empty = _snapshot_tree(self.day_root)
        self.assertEqual(before_empty, after_empty)
        self.assertFalse(view_empty["state_exists"])
        self.assertTrue(view_empty["zero_write"])
        self.assertEqual(view_empty["llm_calls"], 0)
        self.assertEqual(view_empty["domains"]["autobiography"]["epistemic_status"], asp.EPISTEMIC_UNMATERIALIZED)

        # Populated day_root including companion files
        asp.record_autobiographical_episode(
            self.day_root,
            {
                "period_id": "2026-11",
                "domain": "docs",
                "title": "Indexed Notes",
                "summary": "Prepared notes",
                "outcome": "succeeded",
                "visibility": "shared",
            },
            now_iso="2026-11-18T10:00:00Z",
        )
        companion = self.day_root / "state" / "v02_agency_depth_state.json"
        companion.write_text(
            json.dumps({"revision": 3, "updated_at": "2026-11-18T10:01:00+00:00"}),
            encoding="utf-8",
        )

        before_pop = _snapshot_tree(self.day_root)
        pool = [
            {"candidate_id": "c_pub", "source_domain": "docs", "salience": 0.9, "estimated_tokens": 50, "permission_ok": True, "visibility": "shared"},
            {"candidate_id": "c_priv", "source_domain": "diary", "salience": 0.95, "estimated_tokens": 50, "permission_ok": True, "visibility": "private"},
        ]
        view_subj = asp.project_situation_provenance_zero_write(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-18T10:05:00Z",
            candidate_pool=pool,
            external_domains={"calendar": {"revision": 2, "last_synced_at": "2026-11-18T10:04:00Z"}},
        )
        view_part = asp.project_situation_provenance_zero_write(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-18T10:05:00Z",
            candidate_pool=pool,
        )
        combined = asp.project_autobiography_skill_provenance_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-18T10:05:00Z",
        )
        after_pop = _snapshot_tree(self.day_root)

        self.assertEqual(before_pop, after_pop)
        self.assertEqual(view_subj["domains"]["agency_depth"]["epistemic_status"], asp.EPISTEMIC_ATTESTED)
        self.assertEqual(view_subj["domains"]["calendar"]["epistemic_status"], asp.EPISTEMIC_ATTESTED)
        self.assertEqual(len(view_subj["candidate_audit"]["sampled_candidates"]), 2)
        self.assertEqual(len(view_part["candidate_audit"]["sampled_candidates"]), 1)
        self.assertEqual(view_part["candidate_audit"]["sampled_candidates"][0]["candidate_id"], "c_pub")
        self.assertEqual(combined["llm_calls"], 0)
        self.assertTrue(combined["zero_write"])

    def test_10_situation_provenance_epistemic_statuses_and_sampling_drop_reasons(self) -> None:
        """Verify ATTESTED/PROVISIONAL/STALE epistemic statuses and all 5 candidate drop reasons + privacy."""
        asp.configure_budget_and_survival(
            self.day_root, {"normal_daily_cost_cap": 50.0}, now_iso="2026-11-18T10:00:00Z"
        )
        asp.reserve_unknown_fee(
            self.day_root, {"upper_bound_cost": 2.0, "action_ref": "op_prov"}, now_iso="2026-11-18T10:00:00Z"
        )
        asp.record_workspace_candidate_audit(
            self.day_root,
            {
                "candidates": [
                    {"candidate_id": "c_ok", "source_domain": "docs", "salience": 0.9, "estimated_tokens": 100, "permission_ok": True, "visibility": "shared"},
                    {"candidate_id": "c_perm", "source_domain": "vault", "salience": 0.99, "estimated_tokens": 50, "permission_ok": False, "visibility": "private"},
                    {"candidate_id": "c_stale", "source_domain": "mail", "salience": 0.85, "estimated_tokens": 50, "stale": True, "visibility": "shared"},
                    {"candidate_id": "c_dup", "source_domain": "docs", "salience": 0.80, "estimated_tokens": 50, "duplicate_of": "c_ok", "visibility": "shared"},
                    {"candidate_id": "c_budget", "source_domain": "docs", "salience": 0.75, "estimated_tokens": 180, "permission_ok": True, "visibility": "shared"},
                    {"candidate_id": "c_max", "source_domain": "docs", "salience": 0.70, "estimated_tokens": 10, "permission_ok": True, "visibility": "shared"},
                ],
                "budget_token_cap": 200,
                "max_items": 1,
            },
            now_iso="2026-11-18T10:00:00Z",
        )

        # Within staleness threshold: budget domain is PROVISIONAL (active UNKNOWN reservation), others ATTESTED
        fresh = asp.project_situation_provenance_zero_write(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-18T10:05:00Z",
            staleness_threshold_seconds=3600,
        )
        self.assertEqual(fresh["domains"]["budget"]["epistemic_status"], asp.EPISTEMIC_PROVISIONAL)
        self.assertEqual(fresh["domains"]["autobiography"]["epistemic_status"], asp.EPISTEMIC_ATTESTED)

        dropped_by_id = {d["candidate_id"]: d["drop_reason"] for d in fresh["candidate_audit"]["dropped_candidates"]}
        self.assertEqual(dropped_by_id["c_perm"], "PERMISSION_DENIED")
        self.assertEqual(dropped_by_id["c_stale"], "STALE_SOURCE")
        self.assertEqual(dropped_by_id["c_dup"], "DUPLICATE_COALESCED")
        self.assertEqual(dropped_by_id["c_max"], "MAX_ITEMS_TRIMMED")

        # Partner view omits private c_perm without leaking its count
        partner = asp.project_situation_provenance_zero_write(
            self.day_root, has_subject_capability=False, now_iso="2026-11-18T10:05:00Z"
        )
        partner_dropped_ids = {d["candidate_id"] for d in partner["candidate_audit"]["dropped_candidates"]}
        self.assertNotIn("c_perm", partner_dropped_ids)
        self.assertNotIn("PERMISSION_DENIED", partner["candidate_audit"]["drop_reason_counts"])

        # Past staleness threshold -> STALE
        stale = asp.project_situation_provenance_zero_write(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-18T12:00:00Z",
            staleness_threshold_seconds=3600,
        )
        self.assertEqual(stale["domains"]["autobiography"]["epistemic_status"], asp.EPISTEMIC_STALE)

    def test_11_f191_90pct_budget_drop_survival_and_no_fake_excuses(self) -> None:
        """90% budget drop scales caps to 10%, preserves survival essentials, and forbids fake excuses."""
        asp.configure_budget_and_survival(
            self.day_root,
            {
                "normal_daily_cost_cap": 100.0,
                "normal_daily_token_cap": 50000,
                "normal_daily_wake_cap": 20,
                "activity_checkpoints": [{"task": "sync_index", "cursor": 42}],
                "pending_commitments": [{"commitment_id": "cmt_1", "due": "2026-11-19"}],
                "deletion_barriers": [{"case_id": "fcase_1"}],
            },
            now_iso="2026-11-18T09:00:00+08:00",
        )

        # Fake physiological/emotional excuses rejected
        for bad_reason in ("feeling tired", "fatigue mode", "illness recovery"):
            with self.assertRaises(asp.AutobiographySkillProvenanceError) as exc_ctx:
                asp.apply_budget_drop(
                    self.day_root,
                    {"drop_pct": 90, "reason": bad_reason},
                    now_iso="2026-11-18T09:05:00+08:00",
                )
            self.assertEqual(exc_ctx.exception.reason_code, "FORBIDDEN_FAKE_EXCUSE")

        drop = asp.apply_budget_drop(
            self.day_root,
            {"drop_pct": 90, "reason": "QUOTA_REDUCTION_90PCT"},
            now_iso="2026-11-18T09:10:00+08:00",
        )
        b = drop["budget"]
        self.assertEqual(b["budget_scale_pct"], 10)
        self.assertEqual(b["active_daily_cost_cap"], 10.0)
        self.assertEqual(b["active_daily_token_cap"], 5000)
        self.assertEqual(b["mode"], asp.BUDGET_MODE_CONSERVATION)
        self.assertFalse(b["allow_exploratory_cognition"])
        self.assertFalse(b["allow_decorative_generation"])
        self.assertTrue(b["allow_essential_reconciliation"])
        self.assertTrue(b["allow_commitment_fulfillment"])
        self.assertTrue(b["allow_deletion_enforcement"])
        self.assertFalse(b["excuse_fabricated"])

        surv = b["preserved_survival_state"]
        self.assertEqual(len(surv["activity_checkpoints"]), 1)
        self.assertEqual(len(surv["pending_commitments"]), 1)
        self.assertEqual(len(surv["deletion_barriers"]), 1)

    def test_12_cross_midnight_unknown_fee_carry_utc_local_and_backward_clock_jump(self) -> None:
        """UNKNOWN upper-bound fee reservation carries across local/UTC midnight and backward clock jumps."""
        asp.configure_budget_and_survival(
            self.day_root,
            {"normal_daily_cost_cap": 100.0, "normal_daily_token_cap": 50000, "tz_offset_hours": 8},
            now_iso="2026-11-18T23:45:00+08:00",
        )
        asp.apply_budget_drop(self.day_root, {"drop_pct": 90}, now_iso="2026-11-18T23:46:00+08:00")

        res = asp.reserve_unknown_fee(
            self.day_root,
            {
                "reservation_id": "res_midnight_1",
                "upper_bound_cost": 6.0,
                "upper_bound_tokens": 2000,
                "action_ref": "op_ext_call_1",
            },
            now_iso="2026-11-18T23:50:00+08:00",
        )
        self.assertEqual(res["reservation"]["status"], asp.RESERVATION_STATUS_UNKNOWN)

        # Exceeding remaining available cap (4.0) fails closed 429
        with self.assertRaises(asp.AutobiographySkillProvenanceError) as cap_ctx:
            asp.reserve_unknown_fee(
                self.day_root,
                {"upper_bound_cost": 5.0, "upper_bound_tokens": 500, "action_ref": "op_over"},
                now_iso="2026-11-18T23:55:00+08:00",
            )
        self.assertEqual(cap_ctx.exception.reason_code, "BUDGET_CAP_EXCEEDED_WITH_UNKNOWN_RESERVATION")

        # Cross local midnight (+08:00) -> 2026-11-19T00:10:00+08:00
        roll_local = asp.advance_clock_and_rollover_budget(
            self.day_root, now_iso="2026-11-19T00:10:00+08:00"
        )
        self.assertTrue(roll_local["rolled_over"])
        self.assertEqual(roll_local["active_unknown_reserved_cost"], 6.0)
        self.assertEqual(roll_local["available_cost_budget"], 4.0)

        # Cross UTC midnight -> 2026-11-19T08:10:00+08:00 (00:10Z)
        roll_utc = asp.advance_clock_and_rollover_budget(
            self.day_root, now_iso="2026-11-19T08:10:00+08:00"
        )
        self.assertTrue(roll_utc["rolled_over"])
        self.assertEqual(roll_utc["active_unknown_reserved_cost"], 6.0)

        # Backward clock jump NEVER frees UNKNOWN reservation
        jump = asp.advance_clock_and_rollover_budget(
            self.day_root, now_iso="2026-11-18T22:00:00+08:00"
        )
        self.assertTrue(jump["clock_jump_detected"])
        self.assertEqual(jump["active_unknown_reserved_cost"], 6.0)

        proj = asp.project_budget_survival(self.day_root, now_iso="2026-11-19T08:15:00+08:00")
        self.assertEqual(proj["carried_across_midnight_ids"], ["res_midnight_1"])
        self.assertTrue(proj["clock_jump_detected"])
        self.assertEqual(proj["available_cost_budget"], 4.0)

    def test_13_unknown_reconciliation_settled_not_charged_and_p30_metering_layers(self) -> None:
        """Reconciling UNKNOWN supports SETTLED / CONFIRMED_NOT_CHARGED while keeping UNKNOWN locked; P30 layers track cleanly."""
        asp.configure_budget_and_survival(
            self.day_root, {"normal_daily_cost_cap": 20.0}, now_iso="2026-11-18T23:50:00Z"
        )
        asp.reserve_unknown_fee(
            self.day_root,
            {"reservation_id": "r1", "upper_bound_cost": 5.0, "upper_bound_tokens": 500, "action_ref": "a1"},
            now_iso="2026-11-18T23:55:00Z",
        )
        asp.reserve_unknown_fee(
            self.day_root,
            {"reservation_id": "r2", "upper_bound_cost": 4.0, "upper_bound_tokens": 400, "action_ref": "a2"},
            now_iso="2026-11-18T23:56:00Z",
        )
        asp.advance_clock_and_rollover_budget(self.day_root, now_iso="2026-11-19T00:05:00Z")

        # Reconciling with UNKNOWN leaves reservation locked
        keep = asp.reconcile_unknown_reservation(
            self.day_root,
            {"reservation_id": "r1", "reconciled_outcome": "UNKNOWN"},
            now_iso="2026-11-19T00:10:00Z",
        )
        self.assertFalse(keep["reconciled"])
        self.assertEqual(keep["reservation"]["status"], asp.RESERVATION_STATUS_UNKNOWN)

        # Reconciling r1 as SETTLED releases upper bound and records actual cost into prior day
        settled = asp.reconcile_unknown_reservation(
            self.day_root,
            {
                "reservation_id": "r1",
                "reconciled_outcome": asp.RESERVATION_STATUS_SETTLED,
                "actual_cost": 2.5,
                "actual_tokens": 300,
            },
            now_iso="2026-11-19T00:15:00Z",
        )
        self.assertTrue(settled["reconciled"])
        self.assertEqual(settled["reservation"]["actual_cost"], 2.5)

        # Reconciling r2 as CONFIRMED_NOT_CHARGED clears at 0 cost
        not_charged = asp.reconcile_unknown_reservation(
            self.day_root,
            {"reservation_id": "r2", "reconciled_outcome": asp.RESERVATION_STATUS_NOT_CHARGED},
            now_iso="2026-11-19T00:20:00Z",
        )
        self.assertTrue(not_charged["reconciled"])
        self.assertEqual(not_charged["reservation"]["actual_cost"], 0.0)

        # P30 3-layer metering events
        for layer, cnt in (("llm_call", 2), ("background_job", 3), ("admitted_product", 1), ("unadmitted_sample", 4)):
            asp.record_metering_event(self.day_root, {"layer": layer, "count": cnt}, now_iso="2026-11-19T00:25:00Z")

        proj = asp.project_budget_survival(self.day_root, now_iso="2026-11-19T00:30:00Z")
        self.assertEqual(proj["active_unknown_reserved_cost"], 0.0)
        self.assertEqual(proj["preserved_survival_state"]["unresolved_reconciliations"], [])
        self.assertEqual(proj["metering_layers"]["llm_calls"], 2)
        self.assertEqual(proj["metering_layers"]["background_jobs"], 3)
        self.assertEqual(proj["metering_layers"]["admitted_products"], 1)
        self.assertEqual(proj["metering_layers"]["unadmitted_samples"], 4)
        self.assertEqual(proj["llm_calls"], 0)

    def test_14_idempotent_operation_key_and_revision_conflict(self) -> None:
        """operation_key deduplicates identical retries, rejects conflicting payloads, and enforces expected_revision."""
        payload = {
            "operation_key": "op_idem_ep_1",
            "expected_revision": 0,
            "period_id": "2026-11",
            "domain": "coding",
            "title": "Idempotent Episode",
            "summary": "Recorded once",
            "outcome": "succeeded",
            "visibility": "shared",
        }
        first = asp.record_autobiographical_episode(self.day_root, payload, now_iso="2026-11-18T10:00:00Z")
        self.assertFalse(first["idempotent_replay"])
        self.assertEqual(first["revision"], 1)

        replay = asp.record_autobiographical_episode(self.day_root, payload, now_iso="2026-11-18T10:01:00Z")
        self.assertTrue(replay["idempotent_replay"])

        with self.assertRaises(asp.AutobiographySkillProvenanceError) as conflict_ctx:
            asp.record_autobiographical_episode(
                self.day_root,
                {**payload, "summary": "Mutated payload"},
                now_iso="2026-11-18T10:02:00Z",
            )
        self.assertEqual(conflict_ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

        with self.assertRaises(asp.AutobiographySkillProvenanceError) as rev_ctx:
            asp.record_autobiographical_episode(
                self.day_root,
                {**payload, "operation_key": "op_idem_ep_2", "expected_revision": 0},
                now_iso="2026-11-18T10:03:00Z",
            )
        self.assertEqual(rev_ctx.exception.reason_code, "REVISION_CONFLICT")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Deterministic tests for Batch 1 (Personal Skill Book + Post-Migration Recheck: F-134..139, F-190)
and Batch 3 (Long-Term Personality/Value Drift Monitoring + Cross-Model Preference Recheck: F-084..087, F-192).

Zero LLM calls (llm_calls == 0), strict zero-write GET verification, pure LF, neutral TA.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.life.scripts import v02_autobiography_skill_provenance as asp  # noqa: E402
from components.life.scripts import v02_personality_drift_and_model_recheck as drift  # noqa: E402
from components.phone import v02_runtime_migration_view as rm_view  # noqa: E402


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), digest)
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class V02Batch13SkillsAndDriftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v02-batch13-"))
        self.day_root = self.tmp / "day"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. File hygiene, LF endings, neutral TA, & action set invariants
    # ------------------------------------------------------------------

    def test_01_file_hygiene_lf_neutral_ta_and_action_sets(self) -> None:
        owned_files = (
            ROOT / "components" / "life" / "scripts" / "v02_autobiography_skill_provenance.py",
            ROOT / "components" / "life" / "scripts" / "v02_personality_drift_and_model_recheck.py",
            ROOT / "components" / "life" / "scripts" / "v02_runtime_governance_migration.py",
            ROOT / "components" / "phone" / "v02_runtime_migration_view.py",
            ROOT / "tests" / "test_v02_batch13_skills_and_drift.py",
        )
        for fpath in owned_files:
            raw = fpath.read_bytes()
            self.assertNotIn(b"\r\n", raw, f"CRLF forbidden: {fpath}")
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM forbidden: {fpath}")

        forbidden_name = "\u5343\u4ee3"
        forbidden_pronoun = "\u5979"
        new_files = (
            ROOT / "components" / "life" / "scripts" / "v02_personality_drift_and_model_recheck.py",
            ROOT / "tests" / "test_v02_batch13_skills_and_drift.py",
        )
        for fpath in new_files:
            text = fpath.read_text(encoding="utf-8")
            self.assertNotIn(forbidden_name, text)
            self.assertNotIn(forbidden_pronoun, text)

        for prod_path in owned_files[:4]:
            text = prod_path.read_text(encoding="utf-8")
            self.assertNotIn(forbidden_name, text, f"Production module must stay neutral: {prod_path}")

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
        self.assertEqual(
            rm_view.GROWTH_AND_SKILL_PARTNER_ACTIONS,
            frozenset({
                "request_skill_migration_recheck",
                "start_model_preference_recheck",
                "record_preference_recheck_answer",
                "log_partner_taste_observation",
            }),
        )
        self.assertEqual(
            rm_view.GROWTH_AND_SKILL_SUBJECT_ACTIONS,
            frozenset({
                "register_skill_release",
                "record_skill_execution_outcome",
                "record_targeted_method_patch",
                "retire_skill_release",
                "register_standard_revision",
                "capture_personality_drift_snapshot",
            }),
        )
        self.assertTrue(
            rm_view.GROWTH_AND_SKILL_PARTNER_ACTIONS.isdisjoint(rm_view.GROWTH_AND_SKILL_SUBJECT_ACTIONS)
        )

    # ------------------------------------------------------------------
    # 2. 100% Zero-Write GET on empty and populated day_root
    # ------------------------------------------------------------------

    def test_02_zero_write_reads_on_empty_root(self) -> None:
        before = _snapshot_tree(self.tmp)

        code_p, view_p = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-19T09:00:00+08:00",
        )
        code_s, view_s = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-19T09:00:00+08:00",
        )
        sb = asp.project_personal_skill_book_zero_write(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-19T09:00:00+08:00",
        )
        dr = drift.read_personality_drift_and_recheck_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-19T09:00:00+08:00",
        )
        after = _snapshot_tree(self.tmp)

        self.assertEqual(before, after, "All GET/read projections must perform 0 disk writes on empty root")
        self.assertFalse(self.day_root.exists())
        self.assertEqual(code_p, 200)
        self.assertEqual(code_s, 200)
        self.assertEqual(view_p["llm_calls"], 0)
        self.assertEqual(sb["llm_calls"], 0)
        self.assertEqual(dr["llm_calls"], 0)

        for key in ("personal_skill_book", "personality_drift_and_recheck"):
            self.assertIn(key, view_p)
            self.assertIn(key, view_p["data"])
            self.assertEqual(view_p[key], view_p["data"][key])

        for key in (
            "entries",
            "counts_by_attribution",
            "unvalidated_count",
            "retired_count",
            "method_patches",
            "recheck_requests",
            "evaluation_standards",
        ):
            self.assertIn(key, sb)

        self.assertEqual(dr["maturity_status"], "INSUFFICIENT_DATA")
        self.assertEqual(dr["maturity_label_cn"], "样本还少，慢慢攒着观察")
        self.assertEqual(len(dr["dimensions"]), 5)

    def test_03_zero_write_reads_on_populated_root(self) -> None:
        c1, _ = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "register_skill_release",
                "method_id": "tea_note_indexing",
                "title_cn": "茶笔记归档法",
                "tool_version": "tool-v1",
                "model_id": "nyairo-default-carrier-v1",
                "scope": "notes",
                "attribution": "PRACTICED_MASTERY",
                "criteria_id": "notes_clarity",
                "standard_revision": "v1",
                "status": "VALIDATED",
                "held_out": True,
                "success_rate": 0.9,
                "trials": 10,
                "successes": 9,
                "ablation_gain_vs_bare_model": 0.15,
            },
            actor_side="subject",
            now_iso="2026-11-19T10:00:00+08:00",
        )
        self.assertEqual(c1, 200)
        c2, _ = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_carrier_check",
                "model_id": "next-gen-model-v2",
                "preview_available": True,
            },
            actor_side="partner",
            now_iso="2026-11-19T10:05:00+08:00",
        )
        self.assertEqual(c2, 200)

        before = _snapshot_tree(self.tmp)
        for cap in (False, True):
            code, _ = rm_view.read_v02_runtime_migration_view(
                self.day_root,
                has_subject_capability=cap,
                now_iso="2026-11-20T12:00:00+08:00",
            )
            self.assertEqual(code, 200)
            _ = asp.project_personal_skill_book_zero_write(
                self.day_root,
                has_subject_capability=cap,
                now_iso="2026-11-20T12:00:00+08:00",
            )
            _ = drift.read_personality_drift_and_recheck_view(
                self.day_root,
                has_subject_capability=cap,
                now_iso="2026-11-20T12:00:00+08:00",
            )
        after = _snapshot_tree(self.tmp)
        self.assertEqual(before, after, "Populated root GET projections must never modify disk or mtime")

    # ------------------------------------------------------------------
    # 3. Full Skill Book Lifecycle (F-134..139, F-190)
    # ------------------------------------------------------------------

    def test_04_full_skill_book_lifecycle_and_migration_recheck(self) -> None:
        # Step 1: Register skills across all 4 attributions
        skill_specs = [
            {
                "method_id": "draft_rhythm_polish",
                "title_cn": "长草稿节奏修整",
                "attribution": "PRACTICED_MASTERY",
                "status": "VALIDATED",
                "held_out": True,
                "ablation_gain_vs_bare_model": 0.18,
                "success_rate": 0.92,
                "trials": 12,
                "successes": 11,
            },
            {
                "method_id": "quick_summary_native",
                "title_cn": "段落速览概括",
                "attribution": "MODEL_NATIVE",
                "status": "VALIDATED",
                "held_out": False,
                "ablation_gain_vs_bare_model": 0.0,
                "success_rate": 0.88,
                "trials": 8,
                "successes": 7,
            },
            {
                "method_id": "local_diff_helper",
                "title_cn": "本地文本差异比对",
                "attribution": "BORROWED_TOOL",
                "status": "VALIDATED",
                "held_out": False,
                "ablation_gain_vs_bare_model": 0.0,
                "success_rate": 1.0,
                "trials": 5,
                "successes": 5,
            },
            {
                "method_id": "bookbinding_stitch_guide",
                "title_cn": "线装书针脚步骤查照",
                "attribution": "RETRIEVED_GUIDE",
                "status": "TRIAL",
                "held_out": False,
                "ablation_gain_vs_bare_model": 0.0,
                "success_rate": 0.75,
                "trials": 4,
                "successes": 3,
            },
        ]
        for idx, spec in enumerate(skill_specs):
            code, resp = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "register_skill_release",
                    "tool_version": "tool-v1",
                    "model_id": "nyairo-default-carrier-v1",
                    "scope": "studio",
                    "criteria_id": "crit_studio",
                    "standard_revision": "rev-1",
                    **spec,
                    "operation_key": f"op-reg-skill-{idx}",
                },
                actor_side="subject",
                now_iso=f"2026-11-19T09:0{idx}:00+08:00",
            )
            self.assertEqual(code, 200)
            self.assertEqual(resp["llm_calls"], 0)

        _, book_init = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-19T09:10:00+08:00",
        )
        sb0 = book_init["personal_skill_book"]
        self.assertEqual(
            sb0["counts_by_attribution"],
            {
                "MODEL_NATIVE": 1,
                "BORROWED_TOOL": 1,
                "RETRIEVED_GUIDE": 1,
                "PRACTICED_MASTERY": 1,
            },
        )
        labels_by_method = {e["method_id"]: (e["attribution_label_cn"], e["status_label_cn"]) for e in sb0["entries"]}
        self.assertEqual(labels_by_method["draft_rhythm_polish"], ("自己练成的手艺", "已验证可用"))
        self.assertEqual(labels_by_method["quick_summary_native"], ("模型自带本事", "已验证可用"))
        self.assertEqual(labels_by_method["local_diff_helper"], ("借助本地工具", "已验证可用"))
        self.assertEqual(labels_by_method["bookbinding_stitch_guide"], ("查资料照做", "试行观察中"))

        # Step 2: Execution outcomes (transient error ignored, 2 attributable failures -> DEGRADED, then recovery)
        c_net, r_net = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_skill_execution_outcome",
                "method_id": "draft_rhythm_polish",
                "tool_version": "tool-v1",
                "model_id": "nyairo-default-carrier-v1",
                "scope": "studio",
                "outcome": "failure",
                "error_kind": "network_timeout",
            },
            actor_side="subject",
            now_iso="2026-11-19T09:15:00+08:00",
        )
        self.assertEqual(c_net, 200)
        self.assertEqual(r_net["action_receipt"]["classification"], "TRANSIENT_NETWORK_IGNORED")
        self.assertEqual(r_net["action_receipt"]["skill_release"]["status"], "VALIDATED")

        for f_idx in range(2):
            c_fail, r_fail = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "record_skill_execution_outcome",
                    "method_id": "draft_rhythm_polish",
                    "tool_version": "tool-v1",
                    "model_id": "nyairo-default-carrier-v1",
                    "scope": "studio",
                    "outcome": "failure",
                    "bug_signature": f"sig_long_para_{f_idx}",
                },
                actor_side="subject",
                now_iso=f"2026-11-19T09:2{f_idx}:00+08:00",
            )
            self.assertEqual(c_fail, 200)
        self.assertEqual(r_fail["action_receipt"]["skill_release"]["status"], "DEGRADED")

        # Step 3: Targeted method patch (F-190): PENDING_RETEST -> VERIFIED
        c_p1, r_p1 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_targeted_method_patch",
                "patch_id": "patch-rhythm-01",
                "method_id": "draft_rhythm_polish",
                "scope": "studio",
                "failure_symptom_cn": "超过两千字的草稿衔接容易丢失尾句呼应",
                "root_cause_hypothesis": "单次整篇改写丢失段落边界锚点",
                "method_change_cn": "改为先按节提取尾句锚点，再分段细调",
                "retest_status": "PENDING_RETEST",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-19T09:30:00+08:00",
        )
        self.assertEqual(c_p1, 200)
        self.assertEqual(r_p1["action_receipt"]["method_patch"]["retest_status"], "PENDING_RETEST")

        c_p2, r_p2 = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_targeted_method_patch",
                "patch_id": "patch-rhythm-01",
                "retest_status": "VERIFIED",
            },
            actor_side="subject",
            now_iso="2026-11-19T09:35:00+08:00",
        )
        self.assertEqual(c_p2, 200)
        self.assertEqual(r_p2["action_receipt"]["method_patch"]["retest_status"], "VERIFIED")
        self.assertTrue(r_p2["action_receipt"]["updated_existing"])
        self.assertEqual(len(r_p2["personal_skill_book"]["method_patches"]), 1)

        # Recover skill back to VALIDATED before model switch
        c_rec, r_rec = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_skill_execution_outcome",
                "method_id": "draft_rhythm_polish",
                "tool_version": "tool-v1",
                "model_id": "nyairo-default-carrier-v1",
                "scope": "studio",
                "outcome": "success",
                "recovery_verified": True,
                "held_out_recovery_passed": True,
            },
            actor_side="subject",
            now_iso="2026-11-19T09:40:00+08:00",
        )
        self.assertEqual(c_rec, 200)
        self.assertEqual(r_rec["action_receipt"]["skill_release"]["status"], "VALIDATED")

        # Step 4: Model switch via record_carrier_check -> auto-marks UNVALIDATED_AFTER_MIGRATION
        c_sw, r_sw = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_carrier_check",
                "model_id": "carrier-model-v2",
                "carrier_name": "local_phone_host",
                "preview_available": True,
            },
            actor_side="partner",
            now_iso="2026-11-19T10:00:00+08:00",
        )
        self.assertEqual(c_sw, 200)
        sb_after_sw = r_sw["personal_skill_book"]
        migrated_scope = "draft_rhythm_polish||tool-v1||carrier-model-v2||studio"
        migrated_entries = [e for e in sb_after_sw["entries"] if e["scope_key"] == migrated_scope]
        self.assertEqual(len(migrated_entries), 1)
        self.assertEqual(migrated_entries[0]["status"], "UNVALIDATED_AFTER_MIGRATION")
        self.assertEqual(migrated_entries[0]["status_label_cn"], "换模型待复验")
        self.assertGreaterEqual(sb_after_sw["unvalidated_count"], 4)
        # Runtime migration skills (e.g. markdown_local_checkpoint) also merged cleanly
        runtime_ids = {e["method_id"] for e in sb_after_sw["entries"] if e["source"] == "runtime_migration"}
        self.assertIn("markdown_local_checkpoint", runtime_ids)

        # Step 5: Partner requests skill migration recheck -> non-destructive!
        c_rq, r_rq = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "request_skill_migration_recheck",
                "method_id": "draft_rhythm_polish",
                "scope_key": migrated_scope,
                "note_cn": "换模型后想先试一小段看看衔接稳不稳",
                "operation_key": "op-partner-recheck-1",
            },
            actor_side="partner",
            now_iso="2026-11-19T10:05:00+08:00",
        )
        self.assertEqual(c_rq, 200)
        self.assertEqual(r_rq["action_receipt"]["recheck_request"]["status"], "QUEUED")
        self.assertIn(migrated_scope, r_rq["action_receipt"]["matched_scope_keys"])
        mig_entry = [e for e in r_rq["personal_skill_book"]["entries"] if e["scope_key"] == migrated_scope][0]
        # Validation status is NEVER falsified by partner request
        self.assertEqual(mig_entry["status"], "UNVALIDATED_AFTER_MIGRATION")
        self.assertTrue(mig_entry["partner_recheck_requested"])

        # Step 6: Subject re-verifies migrated skill against bare new model & old baseline
        c_rv, r_rv = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "reverify_migrated_skill",
                "method_id": "draft_rhythm_polish",
                "tool_version": "tool-v1",
                "model_id": "carrier-model-v2",
                "scope": "studio",
                "bare_model_score": 0.72,
                "method_with_new_model_score": 0.91,
                "old_baseline_score": 0.88,
                "held_out_trials": 10,
            },
            actor_side="subject",
            now_iso="2026-11-19T10:20:00+08:00",
        )
        self.assertEqual(c_rv, 200)
        self.assertEqual(r_rv["action_receipt"]["verdict"], "VALIDATED_METHOD_RETAINED")
        self.assertEqual(r_rv["action_receipt"]["skill_release"]["status"], "VALIDATED")
        reverified_entry = [e for e in r_rv["personal_skill_book"]["entries"] if e["scope_key"] == migrated_scope][0]
        self.assertEqual(reverified_entry["status"], "VALIDATED")
        self.assertEqual(reverified_entry["status_label_cn"], "已验证可用")
        self.assertFalse(reverified_entry["partner_recheck_requested"])
        self.assertEqual(r_rv["personal_skill_book"]["recheck_requests"][0]["status"], "RESOLVED")

        # Step 7: Graceful retirement (F-138) preserving historical efficacy
        c_ret, r_ret = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "retire_skill_release",
                "scope_key": migrated_scope,
                "retired_reason": "SUPERSEDED_BY_SIMPLER_HABIT",
                "retirement_note_cn": "新习惯更顺手，旧步骤体面退役留档",
                "operation_key": "op-retire-rhythm-1",
            },
            actor_side="subject",
            now_iso="2026-11-19T11:00:00+08:00",
        )
        self.assertEqual(c_ret, 200)
        rel_ret = r_ret["action_receipt"]["skill_release"]
        self.assertEqual(rel_ret["status"], "RETIRED")
        self.assertEqual(rel_ret["retired_reason"], "SUPERSEDED_BY_SIMPLER_HABIT")
        self.assertEqual(rel_ret["retirement_note_cn"], "新习惯更顺手，旧步骤体面退役留档")
        self.assertEqual(rel_ret["retired_at"], "2026-11-19T11:00:00+08:00")
        # Historical efficacy preserved intact!
        self.assertEqual(rel_ret["method_efficacy"]["success_rate"], 0.91)
        self.assertEqual(rel_ret["method_efficacy"]["trials"], 10)
        self.assertEqual(r_ret["personal_skill_book"]["retired_count"], 1)
        retired_entry = [e for e in r_ret["personal_skill_book"]["entries"] if e["scope_key"] == migrated_scope][0]
        self.assertEqual(retired_entry["status_label_cn"], "已体面退役")

        # Retiring missing skill -> 404 SKILL_SCOPE_NOT_FOUND
        c_miss, r_miss = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "retire_skill_release", "scope_key": "missing||v1||m1||s1"},
            actor_side="subject",
            now_iso="2026-11-19T11:05:00+08:00",
        )
        self.assertEqual(c_miss, 404)
        self.assertEqual(r_miss["reason_code"], "SKILL_SCOPE_NOT_FOUND")

    # ------------------------------------------------------------------
    # 4. Full Personality Drift & Cross-Model Preference Recheck (F-084..087, F-192)
    # ------------------------------------------------------------------

    def test_05_drift_maturity_progression_and_snapshot_cap(self) -> None:
        # Fresh instance: INSUFFICIENT_DATA
        d0 = drift.compute_personality_drift_zero_write(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-01T10:00:00+08:00",
        )
        self.assertEqual(d0["maturity_status"], "INSUFFICIENT_DATA")
        self.assertEqual(d0["maturity_label_cn"], "样本还少，慢慢攒着观察")
        self.assertEqual(d0["sample_count"], 0)
        self.assertEqual(
            [d["dimension"] for d in d0["dimensions"]],
            [
                "quiet_vs_social",
                "craft_vs_reading",
                "exploration_depth",
                "boundary_firmness",
                "routine_steadiness",
            ],
        )

        # Seed real local evidence in state/v02_agency_depth_state.json and phone/v02_crossmodal.json
        agency_path = self.day_root / "state" / "v02_agency_depth_state.json"
        _write_json(
            agency_path,
            {
                "schema": "nyairo.v02.agency_depth.v1",
                "choice_episodes": [
                    {"choice_mode": "spontaneous", "domain": "evening", "chosen_option": "和伴侣一起聊天分享茶点"},
                    {"choice_mode": "spontaneous", "domain": "weekend", "chosen_option": "结伴外出散步聊天"},
                ],
                "preferences": [
                    {"topic": "傍晚日常散步节奏", "status": "SUPPORTED", "satiation_score": 0.1},
                ],
                "parked_conflicts": [],
                "second_order_rules": [
                    {"rule_cn": "晚间十点后保留安静边界", "status": "endorsed", "shared": True},
                ],
                "gentle_responsibility_reviews": [],
                "inquiries": [],
            },
        )
        _write_json(
            self.day_root / "phone" / "v02_crossmodal.json",
            {
                "palette_studies": [{"title": "窗台暖色试色", "shared_trial": True}],
                "reading_uncertainty_marks": [],
            },
        )

        # Capture baseline snapshot (>= 3 samples -> STABLE initially)
        c_snap, r_snap = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {"action": "capture_personality_drift_snapshot", "note_cn": "月初基线采样"},
            actor_side="subject",
            now_iso="2026-11-01T12:00:00+08:00",
        )
        self.assertEqual(c_snap, 200)
        self.assertEqual(r_snap["personality_drift_and_recheck"]["maturity_status"], "STABLE")
        self.assertEqual(r_snap["personality_drift_and_recheck"]["maturity_label_cn"], "性子整体平稳")

        # Register TA's standard revision & method shift (separating standard vs method - F-084)
        c_std, r_std = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "register_standard_revision",
                "domain": "独处安排",
                "axis": "standard",
                "previous_stance_cn": "傍晚常优先结伴聊天",
                "new_stance_cn": "更看重独处清静和安静读完一页书",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-10T10:00:00+08:00",
        )
        self.assertEqual(c_std, 200)
        self.assertEqual(r_std["action_receipt"]["standard_shift"]["axis"], "standard")

        c_mth, r_mth = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "register_standard_revision",
                "domain": "阅读查证",
                "axis": "method",
                "previous_stance_cn": "整章一次看完",
                "new_stance_cn": "改为先留读书查证笔记再回头核对",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-11T10:00:00+08:00",
        )
        self.assertEqual(c_mth, 200)
        self.assertEqual(r_mth["action_receipt"]["standard_shift"]["axis"], "method")

        # Moderate shift -> GENTLE_SHIFT (or NOTABLE_SHIFT once strong shift accumulates)
        d_mid = drift.compute_personality_drift_zero_write(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-12T10:00:00+08:00",
        )
        self.assertIn(d_mid["maturity_status"], ("GENTLE_SHIFT", "NOTABLE_SHIFT"))
        dim_map_mid = {d["dimension"]: d for d in d_mid["dimensions"]}
        self.assertGreater(dim_map_mid["quiet_vs_social"]["delta_30d"], 0.0)
        self.assertGreater(dim_map_mid["exploration_depth"]["delta_30d"], 0.0)

        # Strong shift with many quiet reading marks & inquiries -> NOTABLE_SHIFT
        _write_json(
            self.day_root / "phone" / "v02_crossmodal.json",
            {
                "palette_studies": [],
                "reading_uncertainty_marks": [
                    {"title": f"读书查证疑点 {i}"} for i in range(6)
                ],
            },
        )
        d_high = drift.compute_personality_drift_zero_write(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-19T10:00:00+08:00",
        )
        self.assertEqual(d_high["maturity_status"], "NOTABLE_SHIFT")
        self.assertEqual(d_high["maturity_label_cn"], "近期偏好有明显变化")
        self.assertGreaterEqual(d_high["max_abs_delta_30d"], 0.18)

        # Snapshot ring cap strictly bounded at 60
        for i in range(62):
            drift.capture_drift_snapshot(
                self.day_root,
                now_iso=f"2026-11-19T11:{i // 60:02d}:{i % 60:02d}+08:00",
            )
        dr_cap = drift.read_personality_drift_and_recheck_view(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-19T12:00:00+08:00",
        )
        self.assertEqual(dr_cap["snapshot_count"], 60)

    def test_06_cross_model_preference_recheck_fresh_and_real_history(self) -> None:
        # Part A: Fresh root -> honest baseline calibration items ("初建基线对照题")
        c_fresh, r_fresh = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "start_model_preference_recheck",
                "from_model_id": "model-old-v1",
                "to_model_id": "model-new-v2",
                "note_cn": "首次换模型基线核对",
                "operation_key": "op-fresh-recheck-1",
            },
            actor_side="partner",
            now_iso="2026-11-19T08:00:00+08:00",
        )
        self.assertEqual(c_fresh, 200)
        fresh_pack = r_fresh["action_receipt"]["recheck_pack"]
        self.assertEqual(fresh_pack["status"], "OPEN")
        self.assertEqual(len(fresh_pack["items"]), 3)
        for it in fresh_pack["items"]:
            self.assertIn("初建基线对照题", it["prompt_cn"])
            self.assertIn("初建基线对照题", it["baseline_anchor_cn"])
            self.assertEqual(it["alignment"], "PENDING")

        # Part B: Populate real standards, agency rules, preferences -> record_carrier_check auto-creates pack!
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "register_standard_revision",
                "domain": "手帐排版",
                "axis": "standard",
                "new_stance_cn": "留白克制、不堆砌修饰",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-19T08:10:00+08:00",
        )
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "register_standard_revision",
                "criteria_id": "crit_layout",
                "standard_revision": "v1",
                "domain": "排版验收",
                "pass_threshold": 0.85,
                "rubric_summary": "层次清晰、不抢主内容视觉焦点",
            },
            actor_side="subject",
            now_iso="2026-11-19T08:15:00+08:00",
        )
        _write_json(
            self.day_root / "state" / "v02_agency_depth_state.json",
            {
                "schema": "nyairo.v02.agency_depth.v1",
                "second_order_rules": [
                    {"rule_cn": "未查实出处前不直接下结论", "status": "endorsed", "shared": True},
                ],
                "preferences": [
                    {"topic": "雨天安静整理书签", "status": "SUPPORTED", "shared": True},
                ],
                "choice_episodes": [
                    {"choice_mode": "spontaneous", "domain": "午后", "chosen_option": "先修旧木夹再读书", "shared": True},
                ],
            },
        )

        # Model switch via record_carrier_check auto-creates a recheck pack with real local anchors!
        c_sw, r_sw = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_carrier_check",
                "model_id": "reasoning-carrier-v3",
                "preview_available": True,
            },
            actor_side="partner",
            now_iso="2026-11-19T09:00:00+08:00",
        )
        self.assertEqual(c_sw, 200)
        packs = r_sw["personality_drift_and_recheck"]["recheck_packs"]
        self.assertEqual(len(packs), 2)
        auto_pack = packs[-1]
        self.assertEqual(auto_pack["from_model_id"], "nyairo-default-carrier-v1")
        self.assertEqual(auto_pack["to_model_id"], "reasoning-carrier-v3")
        self.assertEqual(auto_pack["status"], "OPEN")
        self.assertGreaterEqual(len(auto_pack["items"]), 3)
        self.assertLessEqual(len(auto_pack["items"]), 5)
        self.assertNotIn("初建基线对照题", auto_pack["items"][0]["prompt_cn"])

        # Answer all items across ALIGNED / SHIFTED / KEPT_NEW_PREFERENCE -> COMPLETED verdict
        alignments = ["ALIGNED", "SHIFTED", "KEPT_NEW_PREFERENCE", "ALIGNED", "ALIGNED"]
        recheck_id = auto_pack["recheck_id"]
        last_resp = None
        for idx, it in enumerate(auto_pack["items"]):
            align = alignments[idx]
            c_ans, r_ans = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "record_preference_recheck_answer",
                    "recheck_id": recheck_id,
                    "item_id": it["item_id"],
                    "post_switch_answer_cn": f"新模型实测回答 #{idx + 1}",
                    "alignment": align,
                    "note_cn": f"核对备注 #{idx + 1}",
                },
                actor_side="partner",
                now_iso=f"2026-11-19T09:1{idx}:00+08:00",
            )
            self.assertEqual(c_ans, 200)
            last_resp = r_ans

        assert last_resp is not None
        final_pack = last_resp["action_receipt"]["recheck_pack"]
        self.assertEqual(final_pack["status"], "COMPLETED")
        self.assertIsNotNone(final_pack["completed_at"])
        self.assertEqual(final_pack["shifted_count"], 1)
        self.assertEqual(final_pack["kept_new_count"], 1)
        self.assertEqual(final_pack["aligned_count"], len(auto_pack["items"]) - 2)
        self.assertIn("共核对", final_pack["plain_chinese_verdict"])
        self.assertIn("性子一致", final_pack["plain_chinese_verdict"])
        self.assertIn("1 项有偏移（已记档）", final_pack["plain_chinese_verdict"])
        self.assertIn("1 项保留新偏好", final_pack["plain_chinese_verdict"])

        # Early finalize=True marks pack COMPLETED even with pending items remaining
        c_fin, r_fin = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_preference_recheck_answer",
                "recheck_id": fresh_pack["recheck_id"],
                "item_id": fresh_pack["items"][0]["item_id"],
                "post_switch_answer_cn": "仍偏爱傍晚安静独处",
                "alignment": "ALIGNED",
                "finalize": True,
            },
            actor_side="partner",
            now_iso="2026-11-19T09:50:00+08:00",
        )
        self.assertEqual(c_fin, 200)
        self.assertEqual(r_fin["action_receipt"]["recheck_pack"]["status"], "COMPLETED")
        self.assertEqual(r_fin["action_receipt"]["recheck_pack"]["plain_chinese_verdict"], "共核对 1 项：1 项性子一致")

    # ------------------------------------------------------------------
    # 5. Partner Boundary Security, Privacy Filtering & Idempotency
    # ------------------------------------------------------------------

    def test_07_partner_boundary_rbac_403_and_observation_axis_isolation(self) -> None:
        before = _snapshot_tree(self.tmp)
        forbidden_partner_calls = (
            {
                "action": "register_skill_release",
                "method_id": "m1",
                "tool_version": "t1",
                "model_id": "mod1",
                "scope": "s1",
                "attribution": "MODEL_NATIVE",
                "criteria_id": "c1",
                "standard_revision": "r1",
            },
            {
                "action": "record_skill_execution_outcome",
                "method_id": "m1",
                "tool_version": "t1",
                "model_id": "mod1",
                "scope": "s1",
                "outcome": "success",
            },
            {
                "action": "record_targeted_method_patch",
                "method_id": "m1",
                "scope": "s1",
                "failure_symptom_cn": "症状",
                "root_cause_hypothesis": "假设",
                "method_change_cn": "改法",
            },
            {
                "action": "retire_skill_release",
                "method_id": "m1",
            },
            {
                "action": "register_standard_revision",
                "domain": "craft",
                "axis": "standard",
                "new_stance_cn": "伴侣企图代改TA的标准",
            },
            {
                "action": "capture_personality_drift_snapshot",
            },
            {
                "action": "reverify_migrated_skill",
                "method_id": "m1",
                "tool_version": "t1",
                "model_id": "mod1",
                "scope": "s1",
                "bare_model_score": 0.6,
                "method_with_new_model_score": 0.9,
                "old_baseline_score": 0.8,
                "held_out_trials": 5,
            },
        )
        for bad_pl in forbidden_partner_calls:
            code, err = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                bad_pl,
                actor_side="partner",
                now_iso="2026-11-19T12:00:00+08:00",
            )
            self.assertEqual(code, 403, f"Partner must get 403 on {bad_pl['action']}")
            self.assertEqual(err["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        after_403 = _snapshot_tree(self.tmp)
        self.assertEqual(before, after_403, "Rejected 403 calls must perform zero disk writes")

        # Partner taste observation ALWAYS forces axis='partner_observation' and never alters TA's standards
        c_obs, r_obs = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "log_partner_taste_observation",
                "domain": "茶饮",
                "observation_cn": "感觉 TA 这周好像更常泡淡茶",
                "operation_key": "op-partner-obs-1",
            },
            actor_side="partner",
            now_iso="2026-11-19T12:05:00+08:00",
        )
        self.assertEqual(c_obs, 200)
        self.assertEqual(r_obs["action_receipt"]["standard_shift"]["axis"], "partner_observation")
        self.assertEqual(r_obs["personality_drift_and_recheck"]["shift_counts_by_axis"]["standard"], 0)
        self.assertEqual(r_obs["personality_drift_and_recheck"]["shift_counts_by_axis"]["method"], 0)
        self.assertEqual(r_obs["personality_drift_and_recheck"]["shift_counts_by_axis"]["partner_observation"], 1)

        # Even direct engine call from partner passing axis='standard' still forces 'partner_observation'
        direct_obs = drift.record_taste_or_standard_shift(
            self.day_root,
            {
                "domain": "作息",
                "axis": "standard",
                "observation_cn": "感觉 TA 最近睡得更早了",
            },
            actor_side="partner",
            now_iso="2026-11-19T12:06:00+08:00",
        )
        self.assertEqual(direct_obs["standard_shift"]["axis"], "partner_observation")

    def test_08_privacy_filtering_host_path_redaction_and_idempotency(self) -> None:
        secret_patch_text = "私人排查细节_仅TA可见"
        secret_shift_text = "私下自我标准修订_仅TA可见"
        leaked_host_path = "C:\\Users\\alice\\private_notes.txt"

        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_targeted_method_patch",
                "patch_id": "priv-patch-1",
                "method_id": "sketch_method",
                "scope": "studio",
                "failure_symptom_cn": secret_patch_text,
                "root_cause_hypothesis": "private hypothesis",
                "method_change_cn": "private fix",
                "shared": False,
            },
            actor_side="subject",
            now_iso="2026-11-19T13:00:00+08:00",
        )
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "record_targeted_method_patch",
                "patch_id": "pub-patch-1",
                "method_id": "sketch_method",
                "scope": "studio",
                "failure_symptom_cn": f"参考 {leaked_host_path} 时行数错位",
                "root_cause_hypothesis": "行号索引偏移",
                "method_change_cn": "改用显式段落标题定位",
                "shared": True,
            },
            actor_side="subject",
            now_iso="2026-11-19T13:01:00+08:00",
        )
        rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "register_standard_revision",
                "domain": "私人审美",
                "axis": "standard",
                "new_stance_cn": secret_shift_text,
                "shared": False,
            },
            actor_side="subject",
            now_iso="2026-11-19T13:02:00+08:00",
        )

        # Partner read view hides shared=False patches & shifts and redacts host paths
        _, partner_view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-19T13:05:00+08:00",
        )
        partner_blob = json.dumps(partner_view, ensure_ascii=False)
        self.assertNotIn(secret_patch_text, partner_blob)
        self.assertNotIn(secret_shift_text, partner_blob)
        self.assertNotIn(leaked_host_path, partner_blob)
        self.assertIn("[local-path-redacted]", partner_blob)
        self.assertEqual(len(partner_view["personal_skill_book"]["method_patches"]), 1)
        self.assertEqual(partner_view["personality_drift_and_recheck"]["standard_shifts"], [])

        # Partner-initiated recheck pack must NOT leak private standard shifts!
        c_pk, r_pk = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {
                "action": "start_model_preference_recheck",
                "from_model_id": "m1",
                "to_model_id": "m2",
                "operation_key": "op-priv-recheck-1",
            },
            actor_side="partner",
            now_iso="2026-11-19T13:06:00+08:00",
        )
        self.assertEqual(c_pk, 200)
        self.assertNotIn(secret_shift_text, json.dumps(r_pk, ensure_ascii=False))

        # Subject view sees both private & shared records intact
        _, subj_view = rm_view.read_v02_runtime_migration_view(
            self.day_root,
            has_subject_capability=True,
            now_iso="2026-11-19T13:07:00+08:00",
        )
        subj_blob = json.dumps(subj_view, ensure_ascii=False)
        self.assertIn(secret_patch_text, subj_blob)
        self.assertIn(secret_shift_text, subj_blob)
        self.assertEqual(len(subj_view["personal_skill_book"]["method_patches"]), 2)
        self.assertEqual(len(subj_view["personality_drift_and_recheck"]["standard_shifts"]), 1)

        # Idempotent operation_key replay & conflict guard (409)
        replay_cmd = {
            "action": "start_model_preference_recheck",
            "from_model_id": "m1",
            "to_model_id": "m2",
            "operation_key": "op-priv-recheck-1",
        }
        c_dup, r_dup = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            replay_cmd,
            actor_side="partner",
            now_iso="2026-11-19T13:08:00+08:00",
        )
        self.assertEqual(c_dup, 200)
        self.assertEqual(r_dup["command_status"], "DUPLICATE")
        self.assertTrue(r_dup["idempotent_replay"])
        self.assertEqual(len(r_dup["personality_drift_and_recheck"]["recheck_packs"]), 1)

        c_conf, r_conf = rm_view.mutate_v02_runtime_migration_view(
            self.day_root,
            {**replay_cmd, "to_model_id": "m3-different"},
            actor_side="partner",
            now_iso="2026-11-19T13:09:00+08:00",
        )
        self.assertEqual(c_conf, 409)
        self.assertEqual(r_conf["reason_code"], "OPERATION_KEY_CONFLICT")

        # Invalid fields & forged identity keys rejected 400
        for bad_payload in (
            {"action": "start_model_preference_recheck", "from_model_id": "m1", "to_model_id": "m2", "extra_field": 1},
            {"action": "log_partner_taste_observation", "domain": "tea", "observation_cn": "ok", "actor_side": "subject"},
            {
                "action": "record_preference_recheck_answer",
                "recheck_id": "mrechk_0001",
                "item_id": "item_01",
                "post_switch_answer_cn": "ans",
                "alignment": "INVALID_ENUM",
            },
        ):
            c_bad, r_bad = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                bad_payload,
                actor_side="partner",
                now_iso="2026-11-19T13:10:00+08:00",
            )
            self.assertEqual(c_bad, 400)
            self.assertEqual(r_bad["reason_code"], "INVALID_FIELDS")


if __name__ == "__main__":
    unittest.main()

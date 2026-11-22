#!/usr/bin/env python3
"""Batch 2 (F-044..046, F-101, F-102) & Batch 4 Backend (Co-Practice Proposal + Shared Collection) tests.

Verifies:
1. 100% zero-write GETs on empty & populated roots for agency_depth, honest_ux,
   crossmodal, evaluate_second_order_constraints, and collect_v02_v03_loop_candidates.
2. Bounded Inquiry -> conclude_or_stop_inquiry -> real S03 s03_claims.sqlite3 claim
   + belief_revisions projection (with strict partner privacy filtering & daily-loop settlement).
3. Active second-order rules (start_trial / endorse) genuinely filtering matching
   candidates in collect_v02_v03_loop_candidates while proposed / released rules do not,
   plus record_second_order_enforcement receipts (cap=40, newest last, privacy-filtered).
4. Old commitment vs new value tradeoff (resolve_commitment_value_conflict enforcing
   409 EXTERNAL_COMMITMENT_REQUIRES_HANDOFF, succeeding when agreed; partner
   propose_commitment_tradeoff proposing gently without forcing TA).
5. Partner propose_rescaled_copractice on v02_honest_ux_view immediately visible in
   rescaled_copractices with status="PROPOSED_BY_PARTNER" while preserving all 5
   SUBJECT_ONLY_ACTIONS returning 403 FORBIDDEN_PARTNER_ACTION.
6. Partner curate_shared_collection on v02_crossmodal_view creating/updating shared
   items, returning 403 on private items, and preserving 403 on check_collection_staleness.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import (  # noqa: E402
    s03_claims as s03,
    v02_agency_depth_engine as ad_engine,
    v02_lived_slices_and_vd_protocols as lived_slices,
    v02_v03_daily_loop_bridge as loop_bridge,
)
from components.phone import (  # noqa: E402
    long_companionship_view,
    outing_places_view,
    v02_agency_depth_view as ad_view,
    v02_crossmodal_view as cm_view,
    v02_honest_ux_view as hux_view,
    workbench_curio_view,
)


def _snapshot_tree(root: Path) -> list[tuple[str, str, int, int]]:
    if not root.exists():
        return []
    snap: list[tuple[str, str, int, int]] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        kind = "dir" if p.is_dir() else "file"
        size = 0 if p.is_dir() else int(st.st_size)
        snap.append((rel, kind, size, int(st.st_mtime_ns)))
    return snap


class Batch24InquiryRulesCopracticeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # 1. 100% Zero-Write GETs & Candidate Collection (Empty & Populated)
    # ------------------------------------------------------------------
    def test_01_zero_write_gets_on_empty_and_populated_roots(self) -> None:
        empty_root = self.root / "empty_instance"
        empty_root.mkdir(parents=True, exist_ok=True)
        before_empty = _snapshot_tree(empty_root)
        now_iso = "2026-11-19T10:30:00+08:00"

        for has_cap in (False, True):
            c1, d1 = ad_view.read_v02_agency_depth_view(
                empty_root, has_subject_capability=has_cap, now_iso=now_iso
            )
            self.assertEqual(c1, 200)
            self.assertEqual(d1["llm_calls"], 0)
            self.assertEqual(d1["data"]["belief_revisions"], [])
            self.assertEqual(d1["data"]["rule_enforcements"], [])
            self.assertEqual(d1["data"]["commitment_tradeoffs"], [])

            c2, d2 = hux_view.read_v02_honest_ux_view(
                empty_root, has_subject_capability=has_cap, now_iso=now_iso
            )
            self.assertEqual(c2, 200)
            self.assertEqual(d2["llm_calls"], 0)

            c3, d3 = cm_view.read_v02_crossmodal_view(
                empty_root, has_subject_capability=has_cap, now_iso=now_iso
            )
            self.assertEqual(c3, 200)
            self.assertEqual(d3["llm_calls"], 0)

        eval_empty = ad_engine.evaluate_second_order_constraints(
            empty_root,
            candidate_kind="advance_workbench_draft",
            candidate_title="深夜赶稿冲刺",
            tags=("creative_intensive",),
            now_iso=now_iso,
        )
        self.assertFalse(eval_empty["constrained"])
        self.assertEqual(eval_empty["blocking_rules"], [])

        cand_empty = loop_bridge.collect_v02_v03_loop_candidates(empty_root, now_iso=now_iso)
        self.assertEqual(cand_empty["llm_calls"], 0)
        self.assertEqual(cand_empty["second_order_constrained_candidates"], [])

        after_empty = _snapshot_tree(empty_root)
        self.assertEqual(before_empty, after_empty)
        self.assertFalse((empty_root / "life" / "state" / "s03_claims.sqlite3").exists())

        # Populated root must also remain 100% zero-write on every GET & candidate collect
        pop_root = self.root / "populated_instance"
        pop_root.mkdir(parents=True, exist_ok=True)
        ad_view.mutate_v02_agency_depth_view(
            pop_root,
            {
                "action": "open_bounded_inquiry",
                "question": "茶壶是不是因为壶盖松了凉得快？",
                "topic_tag": "teapot_heat",
                "shared": True,
            },
            actor_side="subject",
            now_iso=now_iso,
        )
        hux_view.mutate_v02_honest_ux_view(
            pop_root,
            {
                "action": "propose_rescaled_copractice",
                "title": "晚饭后各自安静看十分钟书",
                "scale_mode": "quiet_checkin",
                "operation_key": "op_pop_cop",
            },
            actor_side="partner",
            now_iso=now_iso,
        )
        cm_view.mutate_v02_crossmodal_view(
            pop_root,
            {
                "action": "curate_shared_collection",
                "title": "旧书店木版画展折页",
                "source_kind": "article",
                "status": "fresh",
                "operation_key": "op_pop_col",
            },
            actor_side="partner",
            now_iso=now_iso,
        )

        before_pop = _snapshot_tree(pop_root)
        for has_cap in (False, True):
            ad_view.read_v02_agency_depth_view(pop_root, has_subject_capability=has_cap, now_iso=now_iso)
            hux_view.read_v02_honest_ux_view(pop_root, has_subject_capability=has_cap, now_iso=now_iso)
            cm_view.read_v02_crossmodal_view(pop_root, has_subject_capability=has_cap, now_iso=now_iso)
        ad_engine.evaluate_second_order_constraints(
            pop_root, candidate_kind="advance_workbench_draft", candidate_title="草稿", now_iso=now_iso
        )
        loop_bridge.collect_v02_v03_loop_candidates(pop_root, now_iso=now_iso)
        after_pop = _snapshot_tree(pop_root)
        self.assertEqual(before_pop, after_pop)
        self.assertFalse((pop_root / "life" / "state" / "s03_claims.sqlite3").exists())

    # ------------------------------------------------------------------
    # 2. F-044..046 Live Belief Revision -> Real S03 SQLite Claim
    # ------------------------------------------------------------------
    def test_02_inquiry_conclude_writes_real_s03_claim_and_hides_private(self) -> None:
        day_root = self.root / "inquiry_s03"
        t0 = "2026-11-19T09:00:00+08:00"
        t1 = "2026-11-19T09:20:00+08:00"
        t2 = "2026-11-19T09:40:00+08:00"

        # Shared inquiry with candidate_explanations so previous_belief_cn can be inferred
        c_open, d_open = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "open_bounded_inquiry",
                "question": "窗边总有穿堂风是不是因为窗缝老化？",
                "topic_tag": "window_draft",
                "shared": True,
                "candidate_explanations": [
                    {"summary_cn": "以为是旧窗框整条密封胶坏了", "stance": "ruled_out"},
                    {"summary_cn": "其实是上沿扣锁没压紧", "stance": "plausible"},
                ],
                "operation_key": "op_inq_shared_open",
            },
            actor_side="subject",
            now_iso=t0,
        )
        self.assertEqual(c_open, 200)
        shared_inq_id = d_open["data"]["inquiries"][0]["inquiry_id"]

        # Record real evidence note with source_ref
        c_ev, _ = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "record_inquiry_evidence",
                "inquiry_id": shared_inq_id,
                "note": "用手贴着窗框摸了一圈，只有右上角锁扣处漏风",
                "source_ref": "obs:window_check_01",
                "operation_key": "op_inq_shared_ev1",
            },
            actor_side="subject",
            now_iso=t1,
        )
        self.assertEqual(c_ev, 200)

        # Conclude shared inquiry -> writes S03 claim + belief_revisions
        c_con, d_con = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "conclude_or_stop_inquiry",
                "inquiry_id": shared_inq_id,
                "outcome": "CONCLUDED",
                "conclusion": "穿堂风来自右上角扣锁未压紧，扣实后已经不漏风",
                "operation_key": "op_inq_shared_conclude",
            },
            actor_side="subject",
            now_iso=t2,
        )
        self.assertEqual(c_con, 200)
        revs = d_con["data"]["belief_revisions"]
        self.assertEqual(len(revs), 1)
        rev = revs[0]
        self.assertEqual(rev["inquiry_id"], shared_inq_id)
        self.assertEqual(rev["topic_tag"], "window_draft")
        self.assertEqual(rev["previous_belief_cn"], "以为是旧窗框整条密封胶坏了")
        self.assertIn("右上角扣锁未压紧", rev["revised_belief_cn"])
        self.assertEqual(rev["evidence_refs"], ["obs:window_check_01"])
        self.assertEqual(rev["status"], "REVISED")
        self.assertTrue(rev["shared"])
        self.assertTrue(str(rev["s03_claim_id"]).startswith("claim:"))
        self.assertTrue(str(rev["s03_slot_id"]).startswith("slot:"))

        # Verify real SQLite persistence & deterministic rebuild
        s03_db = day_root / "life" / "state" / "s03_claims.sqlite3"
        self.assertTrue(s03_db.is_file())
        svc = s03.S03ClaimService(s03_db, subject="inquiry:window_draft", create=False)
        cur_view = svc.current_view(now=t2)
        self.assertEqual(cur_view["counts"]["claims"], 1)
        claim = cur_view["claims"][0]
        self.assertEqual(claim["claim_id"], rev["s03_claim_id"])
        self.assertEqual(claim["predicate"], "inquiry_conclusion")
        self.assertEqual(claim["status"], s03.STATUS_SUPPORTED)
        self.assertEqual(claim["source_kind"], s03.SOURCE_OBSERVED)
        self.assertEqual(claim["object"], {"text": rev["revised_belief_cn"]})
        self.assertTrue(svc.rebuild()["matches"])

        # Private inquiry (shared=False) with explicit previous_belief_cn must stay hidden from partner
        _, d_priv_open = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "open_bounded_inquiry",
                "question": "最近午后容易走神是不是午饭碳水太重？",
                "topic_tag": "private_focus_check",
                "shared": False,
                "operation_key": "op_inq_priv_open",
            },
            actor_side="subject",
            now_iso=t0,
        )
        priv_inq_id = next(
            i["inquiry_id"] for i in d_priv_open["data"]["inquiries"] if i["topic_tag"] == "private_focus_check"
        )
        c_priv_con, _ = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "conclude_or_stop_inquiry",
                "inquiry_id": priv_inq_id,
                "outcome": "CONCLUDED",
                "previous_belief_cn": "原以为只是单纯没睡够",
                "conclusion": "午后走神和连续久坐缺水关系更大，起身走五分钟就清明了",
                "operation_key": "op_inq_priv_conclude",
            },
            actor_side="subject",
            now_iso=t2,
        )
        self.assertEqual(c_priv_con, 200)

        # Subject sees both revisions; partner sees ONLY shared revision
        _, subj_get = ad_view.read_v02_agency_depth_view(day_root, has_subject_capability=True, now_iso=t2)
        _, part_get = ad_view.read_v02_agency_depth_view(day_root, has_subject_capability=False, now_iso=t2)
        self.assertEqual(len(subj_get["data"]["belief_revisions"]), 2)
        self.assertEqual(len(part_get["data"]["belief_revisions"]), 1)
        self.assertEqual(part_get["data"]["belief_revisions"][0]["inquiry_id"], shared_inq_id)
        self.assertNotIn("private_focus_check", str(part_get))

    def test_03_s03_env_override_safe_failure_and_daily_loop_conclude(self) -> None:
        day_root = self.root / "inquiry_loop_settle"
        custom_db = self.root / "custom_s03" / "override.sqlite3"
        t0 = "2026-11-19T11:00:00+08:00"
        t1 = "2026-11-19T11:30:00+08:00"

        _, d_open = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "open_bounded_inquiry",
                "question": "水壶保温变短是因为底座松了吗？",
                "topic_tag": "kettle_base",
                "shared": True,
                "operation_key": "op_loop_inq_open",
            },
            actor_side="subject",
            now_iso=t0,
        )
        inq_id = d_open["data"]["inquiries"][0]["inquiry_id"]

        with mock.patch.dict(os.environ, {"CHIYO_S03_CLAIMS_DB": str(custom_db)}):
            settled = loop_bridge.settle_v02_v03_round_consequences(
                day_root,
                {
                    "actor_side": "subject",
                    "action_kind": "ADVANCE_BOUNDED_INQUIRY",
                    "effect_state": "APPLIED",
                    "receipt_ref": "rcpt:inq_conclude_1",
                    "operation_key": "op_settle_inq_conclude_1",
                    "inquiry_id": inq_id,
                    "note": "试了两次沸水保温，换回原装垫圈后明显变久",
                    "conclude": True,
                    "previous_belief_cn": "原以为加热底座接触不良",
                    "conclusion": "只是壶盖硅胶垫圈偏移，复位后保温恢复正常",
                },
                now_iso=t1,
            )
        self.assertTrue(settled["ok"])
        self.assertTrue(settled["settled"])
        self.assertIn("v02_agency_depth.belief_revisions", settled["slices_updated"])
        bi = settled["details"]["bounded_inquiry"]
        self.assertEqual(bi["status"], "CONCLUDED")
        self.assertIsNotNone(bi["belief_revision"])
        self.assertTrue(custom_db.is_file())
        self.assertTrue(str(bi["belief_revision"]["s03_claim_id"]).startswith("claim:"))

        # S03 write failure must fail safely without losing the concluded inquiry
        _, d_open2 = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "open_bounded_inquiry",
                "question": "书架最上层会不会受潮？",
                "topic_tag": "shelf_damp",
                "shared": True,
                "operation_key": "op_inq_s03_err_open",
            },
            actor_side="subject",
            now_iso=t0,
        )
        inq2 = next(i["inquiry_id"] for i in d_open2["data"]["inquiries"] if i["topic_tag"] == "shelf_damp")
        with mock.patch.object(s03.S03ClaimService, "assert_claim", side_effect=OSError("disk full")):
            c_safe, d_safe = ad_view.mutate_v02_agency_depth_view(
                day_root,
                {
                    "action": "conclude_or_stop_inquiry",
                    "inquiry_id": inq2,
                    "outcome": "CONCLUDED",
                    "conclusion": "书架最上层纸页干燥，不需要额外防潮盒",
                    "operation_key": "op_inq_s03_err_conclude",
                },
                actor_side="subject",
                now_iso=t1,
            )
        self.assertEqual(c_safe, 200)
        rev2 = next(r for r in d_safe["data"]["belief_revisions"] if r["inquiry_id"] == inq2)
        self.assertEqual(rev2["status"], "REVISED")
        self.assertIsNone(rev2["s03_claim_id"])

    # ------------------------------------------------------------------
    # 3. F-101 Live Second-Order Volition Enforcement & Receipts
    # ------------------------------------------------------------------
    def test_04_second_order_rule_filters_candidates_by_status_window_and_scope(self) -> None:
        day_root = self.root / "second_order_live"
        late_iso = "2026-11-19T22:30:00+08:00"
        afternoon_iso = "2026-11-19T14:30:00+08:00"
        day_root.mkdir(parents=True, exist_ok=True)

        # Seed heavy workbench draft + gentle plant observation
        t_state = workbench_curio_view.empty_tray_state()
        t_state, _ = workbench_curio_view.set_tray(
            t_state,
            {
                "images": [],
                "materials": [],
                "work": {
                    "artifact_id": "artifact:sprint_chapter",
                    "baseline_version_id": "artifact-version:v3",
                },
                "note": "高强度长篇重活冲刺",
            },
            "2026-11-19T18:00:00+08:00",
        )
        workbench_curio_view.write_tray_state(day_root, t_state)

        rh_state = long_companionship_view.empty_room_habits_state()
        rh_state, _, _ = long_companionship_view.apply_link_plant_sketch(
            rh_state,
            plant_id="fern_01",
            plant_name="铁线蕨小盆栽",
            target_kind="project",
            target_artifact_id="artifact:fern_note",
            observation_note="睡前轻松看看新芽",
            shared=True,
            now_iso="2026-11-19T18:05:00+08:00",
        )
        long_companionship_view.write_room_habits_state(day_root, rh_state)

        # 1) Proposed rule MUST NOT filter
        _, d_prop = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "manage_second_order_rule",
                "rule_op": "propose",
                "title": "晚间不安排高强度重活冲刺",
                "trigger_window": "22:00-07:00",
                "reason_cn": "留一点缓冲给睡眠",
                "shared": True,
                "operation_key": "op_rule_prop",
            },
            actor_side="subject",
            now_iso="2026-11-19T19:00:00+08:00",
        )
        rule_id = d_prop["data"]["second_order_rules"][0]["rule_id"]
        c_prop = loop_bridge.collect_v02_v03_loop_candidates(day_root, now_iso=late_iso)
        self.assertEqual(c_prop["second_order_constrained_candidates"], [])
        fams_prop = {c_prop["descriptors"][c["candidate_id"]]["action_family"] for c in c_prop["candidates"]}
        self.assertIn("advance_workbench_draft", fams_prop)

        # 2) start_trial -> genuinely filters heavy workbench draft at 22:30, keeps plant observation
        ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "manage_second_order_rule",
                "rule_op": "start_trial",
                "rule_id": rule_id,
                "operation_key": "op_rule_trial",
            },
            actor_side="subject",
            now_iso="2026-11-19T19:10:00+08:00",
        )
        c_trial = loop_bridge.collect_v02_v03_loop_candidates(day_root, now_iso=late_iso)
        self.assertEqual(len(c_trial["second_order_constrained_candidates"]), 1)
        self.assertEqual(
            c_trial["second_order_constrained_candidates"][0]["action_family"],
            "advance_workbench_draft",
        )
        fams_trial = {c_trial["descriptors"][c["candidate_id"]]["action_family"] for c in c_trial["candidates"]}
        self.assertNotIn("advance_workbench_draft", fams_trial)
        self.assertIn("observe_plant_journal", fams_trial)

        # 3) Outside wrap-around window (14:30) -> zero candidates filtered
        c_aft = loop_bridge.collect_v02_v03_loop_candidates(day_root, now_iso=afternoon_iso)
        self.assertEqual(c_aft["second_order_constrained_candidates"], [])
        fams_aft = {c_aft["descriptors"][c["candidate_id"]]["action_family"] for c in c_aft["candidates"]}
        self.assertIn("advance_workbench_draft", fams_aft)

        # 4) endorse -> still filters inside window (including after midnight wrap-around 01:30)
        ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "manage_second_order_rule",
                "rule_op": "endorse",
                "rule_id": rule_id,
                "operation_key": "op_rule_endorse",
            },
            actor_side="subject",
            now_iso="2026-11-19T19:20:00+08:00",
        )
        c_wrap = loop_bridge.collect_v02_v03_loop_candidates(
            day_root, now_iso="2026-11-20T01:30:00+08:00"
        )
        self.assertEqual(len(c_wrap["second_order_constrained_candidates"]), 1)

        # 5) release -> stops filtering immediately
        ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "manage_second_order_rule",
                "rule_op": "release",
                "rule_id": rule_id,
                "operation_key": "op_rule_release",
            },
            actor_side="subject",
            now_iso="2026-11-19T19:30:00+08:00",
        )
        c_rel = loop_bridge.collect_v02_v03_loop_candidates(day_root, now_iso=late_iso)
        self.assertEqual(c_rel["second_order_constrained_candidates"], [])
        fams_rel = {c_rel["descriptors"][c["candidate_id"]]["action_family"] for c in c_rel["candidates"]}
        self.assertIn("advance_workbench_draft", fams_rel)

    def test_05_second_order_enforcement_receipts_cap_privacy_and_bridge_settlement(self) -> None:
        day_root = self.root / "rule_enforcements"
        t0 = "2026-11-19T22:10:00+08:00"

        _, d_pub = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "manage_second_order_rule",
                "rule_op": "endorse",
                "title": "晚间不远门外出",
                "trigger_window": "晚间",
                "shared": True,
                "operation_key": "op_pub_rule",
            },
            actor_side="subject",
            now_iso=t0,
        )
        pub_rule_id = d_pub["data"]["second_order_rules"][0]["rule_id"]

        _, d_priv = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "manage_second_order_rule",
                "rule_op": "endorse",
                "title": "午休不冲动花钱购物",
                "trigger_window": "午休",
                "shared": False,
                "operation_key": "op_priv_rule",
            },
            actor_side="subject",
            now_iso=t0,
        )
        priv_rule_id = next(
            r["rule_id"] for r in d_priv["data"]["second_order_rules"] if not r["shared"]
        )

        # Direct evaluation checks for 午休 and 晚间
        noon_res = ad_engine.evaluate_second_order_constraints(
            day_root,
            candidate_kind="non_essential_purchase",
            candidate_title="顺手网购一件小摆件",
            now_iso="2026-11-19T12:45:00+08:00",
        )
        self.assertTrue(noon_res["constrained"])
        self.assertEqual(noon_res["blocking_rules"][0]["rule_id"], priv_rule_id)

        # Settle round with second_order_deferred_rule_id -> records real enforcement receipt
        settled = loop_bridge.settle_v02_v03_round_consequences(
            day_root,
            {
                "actor_side": "subject",
                "action_kind": "MEANINGLESS_IDLE",
                "effect_state": "APPLIED",
                "receipt_ref": "rcpt:defer_1",
                "operation_key": "op_defer_receipt_1",
                "second_order_deferred_rule_id": pub_rule_id,
                "second_order_deferred_rule_title": "晚间不远门外出",
                "second_order_deferred_candidate_ref": "outing:market_night",
                "second_order_deferred_candidate_title": "去远街夜市逛一圈",
            },
            now_iso=t0,
        )
        self.assertTrue(settled["ok"])
        self.assertIn("v02_agency_depth.rule_enforcements", settled["slices_updated"])

        # Record private enforcement too -> must be hidden from partner view
        ad_engine.record_second_order_enforcement(
            day_root,
            rule_id=priv_rule_id,
            rule_title="午休不冲动花钱购物",
            candidate_ref="purchase:secret_item",
            candidate_title="私密购物候选",
            effect="FILTERED",
            reason_cn="午休不花钱",
            now_iso=t0,
        )

        _, subj_view = ad_view.read_v02_agency_depth_view(day_root, has_subject_capability=True, now_iso=t0)
        _, part_view = ad_view.read_v02_agency_depth_view(day_root, has_subject_capability=False, now_iso=t0)
        self.assertEqual(len(subj_view["data"]["rule_enforcements"]), 2)
        self.assertEqual(len(part_view["data"]["rule_enforcements"]), 1)
        self.assertEqual(part_view["data"]["rule_enforcements"][0]["rule_id"], pub_rule_id)
        self.assertNotIn("私密购物候选", str(part_view))

        # Verify cap at 40 entries, newest last
        for idx in range(45):
            ad_engine.record_second_order_enforcement(
                day_root,
                rule_id=pub_rule_id,
                rule_title="晚间不远门外出",
                candidate_ref=f"outing:seq_{idx:03d}",
                candidate_title=f"候选外出_{idx:03d}",
                effect="DEFERRED",
                now_iso=t0,
            )
        _, cap_view = ad_view.read_v02_agency_depth_view(day_root, has_subject_capability=True, now_iso=t0)
        enfs = cap_view["data"]["rule_enforcements"]
        self.assertEqual(len(enfs), 40)
        self.assertEqual(enfs[-1]["candidate_ref"], "outing:seq_044")

    # ------------------------------------------------------------------
    # 4. F-102 Old Commitment vs New Value Tradeoff
    # ------------------------------------------------------------------
    def test_06_commitment_value_conflict_handoff_guard_and_partner_proposal(self) -> None:
        day_root = self.root / "commitment_tradeoff"
        t0 = "2026-11-19T10:00:00+08:00"
        t1 = "2026-11-19T10:15:00+08:00"
        t2 = "2026-11-19T10:30:00+08:00"

        # Register shared partner commitment + private concern
        c_reg1, _ = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "register_gentle_responsibility",
                "resp_id": "resp_shared_bookclub",
                "title_cn": "每周五一起整理共读书单",
                "target_kind": "shared_partner_commitment",
                "last_touched_at": "2026-11-01T10:00:00+08:00",
                "shared": True,
                "operation_key": "op_reg_shared_bc",
            },
            actor_side="subject",
            now_iso=t0,
        )
        self.assertEqual(c_reg1, 200)

        c_reg2, _ = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "register_gentle_responsibility",
                "resp_id": "resp_priv_sketch",
                "title_cn": "每周画一张私密速写",
                "target_kind": "private_concern",
                "last_touched_at": "2026-11-01T10:00:00+08:00",
                "shared": False,
                "operation_key": "op_reg_priv_sk",
            },
            actor_side="subject",
            now_iso=t0,
        )
        self.assertEqual(c_reg2, 200)

        # Partner cannot call subject-only resolve_commitment_value_conflict -> 403
        c_forb, d_forb = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "resolve_commitment_value_conflict",
                "resp_id": "resp_shared_bookclub",
                "new_value_cn": "想把周五晚留给安静散步",
                "conflict_note_cn": "整理书单太赶了",
                "tradeoff_decision": "keep_and_adapt",
            },
            actor_side="partner",
            now_iso=t1,
        )
        self.assertEqual(c_forb, 403)
        self.assertEqual(d_forb["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # Unagreed hand_off_and_retire on shared_partner_commitment -> 409 EXTERNAL_COMMITMENT_REQUIRES_HANDOFF
        c_409, d_409 = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "resolve_commitment_value_conflict",
                "resp_id": "resp_shared_bookclub",
                "new_value_cn": "现在更想把周五晚上留白发呆",
                "conflict_note_cn": "旧约定节奏太密",
                "tradeoff_decision": "hand_off_and_retire",
                "partner_agreed": False,
                "operation_key": "op_retire_blocked",
            },
            actor_side="subject",
            now_iso=t1,
        )
        self.assertEqual(c_409, 409)
        self.assertEqual(d_409["reason_code"], "EXTERNAL_COMMITMENT_REQUIRES_HANDOFF")

        # Partner proposes a gentle commitment tradeoff without forcing TA's responsibility
        c_pprop, d_pprop = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "propose_commitment_tradeoff",
                "resp_id": "resp_shared_bookclub",
                "new_value_cn": "周五晚上可以改成两周聊一次，或者随时停掉都没关系",
                "conflict_note_cn": "不用勉强按周交作业",
                "suggested_decision": "rescale_with_partner",
                "operation_key": "op_partner_tradeoff_prop",
            },
            actor_side="partner",
            now_iso=t1,
        )
        self.assertEqual(c_pprop, 200)
        p_tr = d_pprop["data"]["commitment_tradeoffs"][0]
        self.assertEqual(p_tr["status"], "PROPOSED_BY_PARTNER")
        self.assertEqual(p_tr["proposed_by"], "partner")
        self.assertTrue(p_tr["partner_agreed"])
        self.assertTrue(p_tr["shared"])
        # TA's responsibility status must remain untouched (ACTIVE_GENTLE) by partner proposal
        bc_after_prop = next(
            r for r in d_pprop["data"]["gentle_responsibility_reviews"] if r["resp_id"] == "resp_shared_bookclub"
        )
        self.assertEqual(bc_after_prop["status"], "ACTIVE_GENTLE")

        # Partner targeting a private responsibility gets 404 (zero existence leak)
        c_priv_404, d_priv_404 = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "propose_commitment_tradeoff",
                "resp_id": "resp_priv_sketch",
                "new_value_cn": "试探私密项",
                "suggested_decision": "keep_and_adapt",
            },
            actor_side="partner",
            now_iso=t1,
        )
        self.assertEqual(c_priv_404, 404)
        self.assertEqual(d_priv_404["reason_code"], "RESPONSIBILITY_NOT_FOUND")

        # Subject agrees and retires shared commitment with partner_agreed=True -> 200
        c_ok, d_ok = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "resolve_commitment_value_conflict",
                "resp_id": "resp_shared_bookclub",
                "new_value_cn": "和伴侣商量好以后随缘分享书摘，不再固定周五整理",
                "conflict_note_cn": "双方达成温和退场",
                "tradeoff_decision": "hand_off_and_retire",
                "partner_agreed": True,
                "shared": True,
                "operation_key": "op_retire_agreed",
            },
            actor_side="subject",
            now_iso=t2,
        )
        self.assertEqual(c_ok, 200)
        bc_after_ret = next(
            r for r in d_ok["data"]["gentle_responsibility_reviews"] if r["resp_id"] == "resp_shared_bookclub"
        )
        self.assertEqual(bc_after_ret["status"], "retired_keep_history")
        resolved_tr = d_ok["data"]["commitment_tradeoffs"][0]
        self.assertEqual(resolved_tr["status"], "RESOLVED")
        self.assertEqual(resolved_tr["tradeoff_decision"], "hand_off_and_retire")
        self.assertTrue(resolved_tr["partner_agreed"])

        # Private tradeoff stays hidden from partner GET
        c_priv_res, _ = ad_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "resolve_commitment_value_conflict",
                "resp_id": "resp_priv_sketch",
                "new_value_cn": "改为顺手画小草图不限时",
                "conflict_note_cn": "私密自我调适",
                "tradeoff_decision": "keep_and_adapt",
                "shared": False,
                "operation_key": "op_priv_adapt",
            },
            actor_side="subject",
            now_iso=t2,
        )
        self.assertEqual(c_priv_res, 200)
        _, subj_get = ad_view.read_v02_agency_depth_view(day_root, has_subject_capability=True, now_iso=t2)
        _, part_get = ad_view.read_v02_agency_depth_view(day_root, has_subject_capability=False, now_iso=t2)
        self.assertEqual(len(subj_get["data"]["commitment_tradeoffs"]), 3)
        self.assertEqual(len(part_get["data"]["commitment_tradeoffs"]), 2)
        self.assertNotIn("私密自我调适", str(part_get))

    # ------------------------------------------------------------------
    # 5. Batch 4.1 Partner-Initiated Rescaled Co-Practice Proposal
    # ------------------------------------------------------------------
    def test_07_honest_ux_partner_propose_rescaled_copractice_and_subject_only_guard(self) -> None:
        day_root = self.root / "honest_ux_copractice"
        t0 = "2026-11-19T12:00:00+08:00"

        # Partner proposes a new gentle rescaled co-practice
        code, body = hux_view.mutate_v02_honest_ux_view(
            day_root,
            {
                "action": "propose_rescaled_copractice",
                "title": "晚间轻接力共享歌单",
                "scale_mode": "light_relay",
                "note_cn": "最近忙的话改成各自顺手留一首歌就好，不记缺勤",
                "operation_key": "op_partner_cop_1",
            },
            actor_side="partner",
            now_iso=t0,
        )
        self.assertEqual(code, 200)
        self.assertEqual(body["command_status"], "APPLIED")
        pid = body["data"]["practice_id"]
        self.assertEqual(body["data"]["status"], "PROPOSED_BY_PARTNER")
        self.assertEqual(body["data"]["proposed_by"], "partner")
        self.assertTrue(body["data"]["partner_confirmed"])
        self.assertFalse(body["data"]["subject_confirmed"])
        self.assertTrue(body["data"]["shared"])
        self.assertTrue(body["data"]["no_debt"])

        # Idempotent replay
        c_dup, d_dup = hux_view.mutate_v02_honest_ux_view(
            day_root,
            {
                "action": "propose_rescaled_copractice",
                "title": "晚间轻接力共享歌单",
                "scale_mode": "light_relay",
                "note_cn": "最近忙的话改成各自顺手留一首歌就好，不记缺勤",
                "operation_key": "op_partner_cop_1",
            },
            actor_side="partner",
            now_iso=t0,
        )
        self.assertEqual(c_dup, 200)
        self.assertEqual(d_dup["command_status"], "DUPLICATE")

        # Immediately visible in GET /api/phone/v02/honest-ux for both partner and subject
        for has_cap in (False, True):
            gc, gd = hux_view.read_v02_honest_ux_view(day_root, has_subject_capability=has_cap, now_iso=t0)
            self.assertEqual(gc, 200)
            matched = [r for r in gd["data"]["rescaled_copractices"] if r["practice_id"] == pid]
            self.assertEqual(len(matched), 1)
            row = matched[0]
            self.assertEqual(row["status"], "PROPOSED_BY_PARTNER")
            self.assertEqual(row["proposed_by"], "partner")
            self.assertEqual(row["scale_mode"], "light_relay")
            self.assertTrue(row["partner_confirmed"])
            self.assertFalse(row["subject_confirmed"])
            self.assertTrue(row["shared"])
            self.assertTrue(row["no_debt"])

        # Partner proposing rescale of an existing shared lived_slices routine merges cleanly
        lived_slices.mutate_v02_lived_slices(
            day_root,
            {
                "action": "register_copractice_routine",
                "routine_id": "cop_shared_walk",
                "title": "周末去街角书店散步",
                "cadence_mode": "heavy_joint",
                "shared": True,
                "operation_key": "op_ls_reg_1",
            },
            actor_side="subject",
            now_iso=t0,
        )
        lived_slices.mutate_v02_lived_slices(
            day_root,
            {
                "action": "register_copractice_routine",
                "routine_id": "cop_priv_only",
                "title": "私密个人习惯",
                "cadence_mode": "heavy_joint",
                "shared": False,
                "operation_key": "op_ls_reg_priv",
            },
            actor_side="subject",
            now_iso=t0,
        )
        c_priv_cop, d_priv_cop = hux_view.mutate_v02_honest_ux_view(
            day_root,
            {
                "action": "propose_rescaled_copractice",
                "practice_id": "cop_priv_only",
                "scale_mode": "light_relay",
            },
            actor_side="partner",
            now_iso=t0,
        )
        self.assertEqual(c_priv_cop, 403)
        self.assertEqual(d_priv_cop["reason_code"], "FORBIDDEN_COPRACTICE_SCOPE")

        c_up, _ = hux_view.mutate_v02_honest_ux_view(
            day_root,
            {
                "action": "propose_rescaled_copractice",
                "practice_id": "cop_shared_walk",
                "scale_mode": "pause_without_debt",
                "note_cn": "这周先歇着，不留任何负担",
                "operation_key": "op_partner_cop_merge",
            },
            actor_side="partner",
            now_iso=t0,
        )
        self.assertEqual(c_up, 200)
        _, gd2 = hux_view.read_v02_honest_ux_view(day_root, has_subject_capability=False, now_iso=t0)
        merged_walk = [r for r in gd2["data"]["rescaled_copractices"] if r["practice_id"] == "cop_shared_walk"]
        self.assertEqual(len(merged_walk), 1)
        self.assertEqual(merged_walk[0]["scale_mode"], "pause_without_debt")
        self.assertEqual(merged_walk[0]["status"], "PROPOSED_BY_PARTNER")

        # All 5 SUBJECT_ONLY_ACTIONS MUST still return 403 FORBIDDEN_PARTNER_ACTION for partner
        self.assertEqual(len(hux_view.SUBJECT_ONLY_ACTIONS), 5)
        for subj_act in sorted(hux_view.SUBJECT_ONLY_ACTIONS):
            fc, fb = hux_view.mutate_v02_honest_ux_view(
                day_root, {"action": subj_act}, actor_side="partner", now_iso=t0
            )
            self.assertEqual(fc, 403, f"Expected 403 for partner calling {subj_act}")
            self.assertEqual(fb["reason_code"], "FORBIDDEN_PARTNER_ACTION")

    # ------------------------------------------------------------------
    # 6. Batch 4.3 Partner-Safe Shared Collection Curation
    # ------------------------------------------------------------------
    def test_08_crossmodal_partner_curate_shared_collection_and_private_403(self) -> None:
        day_root = self.root / "crossmodal_shared_col"
        t0 = "2026-11-19T14:00:00+08:00"
        t1 = "2026-11-19T14:20:00+08:00"

        # 1) Partner calling check_collection_staleness MUST still return 403 FORBIDDEN_ACTOR_SIDE
        c_old, d_old = cm_view.mutate_v02_crossmodal_view(
            day_root,
            {"action": "check_collection_staleness", "title": "禁止伴侣直调"},
            actor_side="partner",
            now_iso=t0,
        )
        self.assertEqual(c_old, 403)
        self.assertEqual(d_old["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # 2) Partner creates shared collection item (even if share_with_room=False passed -> forced shared=True)
        c_add, d_add = cm_view.mutate_v02_crossmodal_view(
            day_root,
            {
                "action": "curate_shared_collection",
                "title": "旧书店水彩纸推荐清单",
                "source_kind": "article",
                "source_ref": "https://example.invalid/paper-guide",
                "status": "fresh",
                "note_cn": "有空一起翻翻看",
                "share_with_room": False,
                "operation_key": "op_col_partner_add",
            },
            actor_side="partner",
            now_iso=t0,
        )
        self.assertEqual(c_add, 200)
        items_p = d_add["data"]["collection_health"]["items"]
        self.assertEqual(len(items_p), 1)
        shared_item_id = items_p[0]["item_id"]
        self.assertTrue(items_p[0]["shared"])
        self.assertEqual(items_p[0]["status"], "fresh")
        self.assertEqual(items_p[0]["curated_by"], "partner")

        # 3) Partner updates shared item status to revisited, then archived
        c_upd1, d_upd1 = cm_view.mutate_v02_crossmodal_view(
            day_root,
            {
                "action": "curate_shared_collection",
                "item_id": shared_item_id,
                "status": "revisited",
                "note_cn": "刚重看了一遍纸张克重说明",
                "operation_key": "op_col_partner_revisit",
            },
            actor_side="partner",
            now_iso=t1,
        )
        self.assertEqual(c_upd1, 200)
        self.assertEqual(d_upd1["data"]["collection_health"]["items"][0]["status"], "revisited")
        self.assertEqual(d_upd1["data"]["collection_health"]["counts_by_status"]["revisited"], 1)

        c_upd2, d_upd2 = cm_view.mutate_v02_crossmodal_view(
            day_root,
            {
                "action": "curate_shared_collection",
                "item_id": shared_item_id,
                "status": "archived",
                "operation_key": "op_col_partner_archive",
            },
            actor_side="partner",
            now_iso=t1,
        )
        self.assertEqual(c_upd2, 200)
        self.assertEqual(d_upd2["data"]["collection_health"]["items"][0]["status"], "archived")
        self.assertEqual(d_upd2["data"]["collection_health"]["counts_by_status"]["archived"], 1)

        # 4) Subject creates a private collection item; partner attempting to mutate it MUST get 403
        c_priv, d_priv = cm_view.mutate_v02_crossmodal_view(
            day_root,
            {
                "action": "check_collection_staleness",
                "title": "私密灵感剪报",
                "source_kind": "note",
                "status": "fresh",
                "share_with_room": False,
                "operation_key": "op_col_subj_priv",
            },
            actor_side="subject",
            now_iso=t0,
        )
        self.assertEqual(c_priv, 200)
        priv_item_id = next(
            it["item_id"] for it in d_priv["data"]["collection_health"]["items"] if not it["shared"]
        )

        c_403, d_403 = cm_view.mutate_v02_crossmodal_view(
            day_root,
            {
                "action": "curate_shared_collection",
                "item_id": priv_item_id,
                "status": "revisited",
            },
            actor_side="partner",
            now_iso=t1,
        )
        self.assertEqual(c_403, 403)
        self.assertEqual(d_403["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # Partner GET never exposes the private item
        _, p_get = cm_view.read_v02_crossmodal_view(day_root, has_subject_capability=False, now_iso=t1)
        self.assertNotIn(priv_item_id, str(p_get))
        self.assertEqual(p_get["data"]["collection_health"]["private_items_withheld_count"], 1)


if __name__ == "__main__":
    unittest.main()

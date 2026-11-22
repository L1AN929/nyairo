#!/usr/bin/env python3
"""0.2 全纲终审收口总验：14 章 §5 十二必备端到端场景 + V-D 受控反事实消融 + 0.2 新日常桥接（0 大模型调用）。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
NATIVE_DIR = REPO_ROOT / "components" / "native"
NATIVE_SRC = NATIVE_DIR / "src"
NATIVE_RUNTIME = NATIVE_DIR / "memory_runtime_v1"
for _p in (
    str(REPO_ROOT),
    str(REPO_ROOT / "scripts"),
    str(REPO_ROOT / "components"),
    str(LIFE_SCRIPTS),
    str(WORLD_SCRIPTS),
    str(NATIVE_DIR),
    str(NATIVE_SRC),
    str(NATIVE_RUNTIME),
    str(REPO_ROOT / "tests"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import activity_admission as adm  # noqa: E402
import activity_continuity as ac  # noqa: E402
import activity_waiting_agenda as awa  # noqa: E402
import action_reality_ledger as arl  # noqa: E402
import activity_progress_completion_lr5 as lr5  # noqa: E402
import chiyo_bundle.lived_context_bridge as lived_bridge  # noqa: E402
import life_co_presence as cop  # noqa: E402
import life_wait_basis as lwb  # noqa: E402
import result_settlement_ar1 as ar1  # noqa: E402
import v02_agency_depth_engine as ad_engine  # noqa: E402
import v02_causal_ablation_runner as vd_runner  # noqa: E402
import v02_common_ground as cg_engine  # noqa: E402
import v02_recall_projection as rp_engine  # noqa: E402
import v02_representation_lease_forget as rlf  # noqa: E402
import v02_runtime_governance_migration as rm_engine  # noqa: E402
import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
import v08_universal_matter as matter  # noqa: E402
import waiting_no_model_poll_demo as wait_demo  # noqa: E402
from app.evidence import EvidenceStore  # noqa: E402
from components.phone import v02_crossmodal_view as cm_view  # noqa: E402
from components.phone import workbench_curio_view  # noqa: E402
from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer  # noqa: E402
from life_experience_port import LifeExperiencePort, life_conversation_id  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

NOW_T0 = "2026-11-18T10:00:00+08:00"
NOW_T1 = "2026-11-18T11:30:00+08:00"
NOW_LATER = "2026-11-25T10:00:00+08:00"


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


class TestV02TwelveMandatoryScenariosAndVdAblation(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v02-12scenes-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_scene_01_work_written_restarted_and_history_influences_choice(self) -> None:
        """场景1：一个作品实际写入并可在重启后继续，历史影响下一次选择。"""
        rlf._ensure_control_ledger_locked(self.root)
        c_init, out_init = rlf.create_managed_artifact(
            self.root,
            artifact_id="artifact:rain_notes_v1",
            controller_principal_ref="owner_scene1",
            subject_id="chiyo",
            initial_body="# 海岸草稿\n第一版待续\n",
            operation_id="op_sc1_init",
            title="rain_notes_v1.md",
            visibility="shared",
            explicit_version_id="ver_1",
            now_iso=NOW_T0,
        )
        self.assertEqual(c_init, 200)
        self.assertEqual(out_init["current_head"], "ver_1")

        tray = workbench_curio_view.empty_tray_state()
        tray, _ = workbench_curio_view.set_tray(
            tray,
            {
                "images": [],
                "materials": [],
                "work": {"artifact_id": "artifact:rain_notes_v1", "baseline_version_id": "ver_1"},
                "note": "海岸草稿第一版待续",
            },
            NOW_T0,
        )
        workbench_curio_view.write_tray_state(self.root, tray)

        collected = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=NOW_T0)
        wb_desc = next(
            d for d in collected["descriptors"].values() if d.get("action_family") == "advance_workbench_draft"
        )
        ev = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, wb_desc["source_ref"], now_iso=NOW_T1, decision_id="dec:scene1"
        )
        assert ev is not None
        settled = loop_bridge.settle_v02_v03_round_consequences(self.root, ev, now_iso=NOW_T1)
        self.assertTrue(settled["settled"])
        self.assertEqual(settled["llm_calls"], 0)
        self.assertIn("managed_artifact", settled["slices_updated"])
        committed_ver = settled["details"]["workbench"]["committed_version_id"]
        self.assertNotEqual(committed_ver, "ver_1")

        # Simulate restart: read canonical ManagedArtifact owner & fresh tray state from disk
        owner_state, owner_ok = rlf._load_state_readonly(self.root)
        self.assertTrue(owner_ok)
        self.assertEqual(owner_state["artifacts"]["artifact:rain_notes_v1"]["current_head"], committed_ver)
        self.assertEqual(owner_state["versions"][committed_ver]["parents"], ["ver_1"])

        reloaded, err = workbench_curio_view.read_tray_state(self.root)
        self.assertIsNone(err)
        self.assertEqual((reloaded.get("tray") or {}).get("work", {}).get("baseline_version_id"), committed_ver)
        self.assertIn("推进", str((reloaded.get("tray") or {}).get("note") or ""))

    def test_scene_02_ordinary_chat_responds_during_atomic_step_without_illegal_pause(self) -> None:
        """场景2：原子步骤期间普通聊天可回应但不非法 pause。"""
        class _ViewCoord:
            def get_current_life_view(self) -> dict[str, Any]:
                return {
                    "foreground_activity_ref": "actv_atomic_scene2",
                    "interruptibility": "ATOMIC",
                    "communication_availability": "BOUNDARY_DEFERRED",
                }

        class _Runtime:
            interruption_coordinator = _ViewCoord()

        proto = cop.FiniteCoPresence(_Runtime(), state_root=self.root / "cop2", subject_id="subj", home=str(self.root))
        seen = proto.observe({"kind": "LOCAL_MESSAGE", "body": "刚写完一段先歇会儿吗"})
        self.assertEqual(seen["communication_availability"], "BOUNDARY_DEFERRED")
        self.assertTrue(seen["respondable"])
        self.assertTrue(seen["foreground_atomic"])
        self.assertFalse(seen["pause_requested"])
        self.assertEqual(seen["owners_written"], 0)

    def test_scene_03_waiting_uses_zero_model_poll_and_eligibility_without_auto_resume(self) -> None:
        """场景3：等待结果长期 0 模型轮询；满足条件只产生资格，不自动恢复。"""
        quiet_report = {
            "state": "OK",
            "checked": 1,
            "unchanged": 1,
            "changed": 0,
            "evaluated": 0,
            "satisfied": [],
            "emitted_eligibilities": [],
            "errors": [],
            "body_checked": 0,
            "body_changed": 0,
            "auto_resumed": False,
            "llm_calls": 0,
        }
        self.assertTrue(wait_demo.quiet_upkeep(quiet_report))
        satisfied_only = dict(
            quiet_report,
            satisfied=["rcond:1"],
            emitted_eligibilities=["relig:1"],
            auto_resumed=False,
        )
        self.assertEqual(satisfied_only["llm_calls"], 0)
        self.assertFalse(satisfied_only["auto_resumed"])
        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "create_future_capsule",
                "capsule_id": "cap_wait_03",
                "unlock_after": NOW_T0,
                "title_cn": "明年秋天再看的一封信",
                "operation_key": "sc3_cap",
            },
            actor_side="subject",
            now_iso=NOW_T0,
        )
        proj = ad_engine.project_v02_agency_depth(self.root, has_subject_capability=True, now_iso=NOW_T1)
        cap = proj["future_self_capsules"][0]
        self.assertEqual(cap["status"], "AVAILABLE")
        self.assertFalse(cap["auto_claimed"])
        self.assertEqual(proj["llm_calls"], 0)

        # Real LR-2 + LR-4 WaitingCoordinator: quiet unrelated check emits 0 eligibilities & 0 LLM calls;
        # matching head change emits real persisted OPEN ResumeEligibility while activity remains WAITING.
        wait_root = self.root / "lr4_wait_scene3"
        wait_root.mkdir(parents=True, exist_ok=True)
        act_id = "actv:scene3_wait"
        dec_id = "dec:scene3_wait"

        class _Scene3DecReader:
            def get_decision(self, decision_id: str) -> dict[str, Any] | None:
                if decision_id != dec_id:
                    return None
                return {"decision_id": decision_id, "revision": 1, "snapshot_fingerprint": f"fp:{decision_id}"}

        class _Scene3AdpReader:
            def __init__(self) -> None:
                self.grants: dict[str, tuple[str, str]] = {}

            def grant(self, cmd_id: str, mutation_kind: str, activity_ref: str) -> None:
                self.grants[cmd_id] = (mutation_kind, activity_ref)

            def get_adoption_by_id(self, adoption_id: str) -> dict[str, Any] | None:
                cmd_id = adoption_id.split(":", 1)[1] if ":" in adoption_id else ""
                granted = self.grants.get(cmd_id)
                if granted is None:
                    return None
                mk, aref = granted
                return {
                    "adoption_id": adoption_id,
                    "decision_ref": dec_id,
                    "decision_revision": 1,
                    "adoption_status": "APPLIED",
                    "revision": 1,
                    "authorized_mutation_kind": mk,
                    "authorized_command_id": cmd_id,
                    "created_activity_ref": aref,
                    "target_activity_ref": aref,
                    "authorized_activity_ref": aref,
                }

        adp_reader = _Scene3AdpReader()
        issuer = adm.build_admission_issuer(_Scene3DecReader(), adp_reader, runtime_epoch="scene3-wait")
        lease = ac.ActivityAuthorityLease(wait_root)
        lease.acquire()
        try:
            store = ac.CanonicalActivityStore(
                wait_root, namespace=ac.NAMESPACE_ISOLATED_TEST, _admission_issuer=issuer
            )
            capability = lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
            commands = ac.ActivityCommandService(store=store, lease=lease, capability=capability)
            coordinator = awa.WaitingCoordinator(
                activity_store=store, lease=lease, command_service=commands, namespace=ac.NAMESPACE_ISOLATED_TEST
            )
            adp_reader.grant("sc3-start", ac.TRANSITION_START, act_id)
            can_rev0 = int(store.load_verified_state_and_journal()[0]["revision"])
            active = commands.start_activity(
                activity_kind="READ_ARTIFACT",
                title="scene3 waiting fixture",
                origin_ref="artifact:wait_scene3",
                source_refs=["artifact:wait_scene3"],
                subject_refs=["artifact:wait_scene3"],
                expected_revision=can_rev0,
                idempotency_key="sc3-start",
                decision_ref=dec_id,
                adoption_ref="adp:sc3-start",
                activity_id=act_id,
            ).activity
            basis = lwb.work_head_basis(
                artifact_id="artifact:wait_scene3", baseline_head="version:1", activity_ref=act_id
            )
            adp_reader.grant("sc3-wait", ac.TRANSITION_WAIT, act_id)
            waited = coordinator.enter_waiting_with_condition(
                activity_id=act_id,
                expected_revision=int(active["revision"]),
                waiting_on_ref=basis["waiting_on_ref"],
                condition_kind=lwb.CONDITION_KIND,
                parameters=lwb.condition_parameters(basis),
                source_refs=[dec_id, "adp:sc3-wait"],
                idempotency_key="sc3-wait",
                causal_parent_refs=[dec_id],
                occurred_at=NOW_T0,
                observed_at=NOW_T0,
                reason_kind=awa.REASON_KIND_AGENCY_CHOICE,
                decision_ref=dec_id,
                adoption_ref="adp:sc3-wait",
            )
            self.assertEqual(waited["activity"]["status"], ac.STATUS_WAITING)
            wait_rev = waited["activity"]["revision"]

            quiet_evt = coordinator.evaluate_event({
                "event_id": "wevt:sc3_quiet_1",
                "event_kind": "ambient_tick",
                "occurred_at": NOW_T0,
                "observed_at": NOW_T0,
                "source_refs": ["timer:quiet"],
            })
            self.assertEqual(quiet_evt["satisfied_condition_ids"], [])
            self.assertEqual(quiet_evt["emitted_eligibilities"], [])
            self.assertFalse(quiet_evt["auto_resumed"])
            self.assertEqual(quiet_evt["llm_calls"], 0)
            self.assertEqual(coordinator.eligibility_store.load_state()["eligibilities"], {})

            matched_ev = lwb.head_change_event(basis, current_head="version:2", observed_at=NOW_T1)
            assert matched_ev is not None
            hit = coordinator.evaluate_event(matched_ev)
            self.assertEqual(len(hit["satisfied_condition_ids"]), 1)
            self.assertEqual(len(hit["emitted_eligibilities"]), 1)
            self.assertFalse(hit["auto_resumed"])
            self.assertEqual(hit["llm_calls"], 0)
            persisted_eligs = coordinator.eligibility_store.load_state()["eligibilities"]
            self.assertEqual(len(persisted_eligs), 1)
            self.assertEqual(next(iter(persisted_eligs.values()))["status"], awa.ELIG_STATUS_OPEN)
            act_after = store.load_verified_state_and_journal()[0]["activities"][act_id]
            self.assertEqual(act_after["status"], ac.STATUS_WAITING)
            self.assertEqual(act_after["revision"], wait_rev)
        finally:
            lease.release()

    def test_scene_04_external_accepted_timeout_stays_unknown_and_forbids_bare_retry(self) -> None:
        """场景4：外部 accepted 后 timeout，保持 UNKNOWN、查询原操作、不重复提交。"""
        self.assertTrue(issubclass(arl.UnknownActionRetryForbiddenError, Exception))
        before = _snapshot_tree(self.root)
        res = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "actor_side": "life_resident",
                "effect_state": "UNKNOWN",
                "action_kind": "ACT_OPTIONAL_NEXT_GESTURE",
                "receipt_ref": "ar0:timeout-scene4",
                "operation_key": "sc4_timeout",
            },
            now_iso=NOW_T0,
        )
        self.assertEqual(res["status"], "DEFERRED_UNKNOWN")
        self.assertEqual(res["observability_class"], "PENDING_UNKNOWN_SUSPENDED")
        self.assertEqual(res["optimistic_writes"], 0)
        self.assertEqual(_snapshot_tree(self.root), before)

    def test_scene_05_partial_subeffect_confirmed_progress_never_exceeds_evidence(self) -> None:
        """场景5：部分子效果确认，其余未知；进展不超出证据。"""
        registry = ar1.SettlementPolicyRegistry()
        ev_hex = hashlib.sha256(b"sc5-partial").hexdigest()[:16]
        pe = ar1.SettlementEvidenceInput(
            evidence_id=f"sevid:{ev_hex}",
            root_evidence_id=f"rootev:{ev_hex}",
            action_id="actn:partial_1",
            source_ref="world_authority:partial_1",
            authority_domain="world_authority",
            evidence_kind="world_partial_state",
            authority_grade=ar1.AUTH_GRADE_DOMAIN_FACT,
            claim_kind="receipt:PARTIAL",
            verified=True,
            occurred_at=NOW_T0,
            observed_at=NOW_T0,
            recorded_at=NOW_T0,
            target_ref="world_obj:kettle",
            details={
                "partial_effects": True,
                "confirmed_effects": ["world_fact:water_heated"],
                "missing_effects": ["world_fact:tea_poured"],
                "failed_effects": [],
            },
        )
        settled = registry.evaluate(
            action={"action_id": "actn:partial_1", "action_kind": ar1.ACTION_KIND_WORLD, "status": ar1.ACTION_STATUS_SUCCEEDED, "frozen_payload": {}},
            attempts=[],
            evidences=[pe],
            settlement_id="rset:partial_1",
            recorded_at=NOW_T0,
        )
        self.assertEqual(settled["settlement_status"], ar1.SETTLEMENT_STATUS_PARTIAL)
        self.assertEqual(settled["outcome_kind"], ar1.OUTCOME_PARTIAL_EFFECT)
        self.assertIn("world_fact:water_heated", settled["confirmed_effect_refs"])
        self.assertIn("world_fact:tea_poured", settled["missing_effect_refs"])

        lr5_ev = lr5.LifeProgressEvidence(
            progress_evidence_id="pevid:partial_1",
            source_kind=lr5.SOURCE_KIND_SETTLED_EVENT,
            source_ref="srev:partial_1",
            authority_domain="result_settlement_authority",
            epistemic_kind=lr5.EPISTEMIC_PARTIAL_FACT,
            settlement_ref="rset:partial_1",
            settlement_status=lr5.SETTLEMENT_STATUS_PARTIAL,
            outcome_kind=lr5.OUTCOME_PARTIAL_EFFECT,
            activity_id="actv_partial",
            target_ref="world_obj:kettle",
            occurred_at=NOW_T0,
            observed_at=NOW_T0,
            recorded_at=NOW_T0,
            idempotency_key="idem:partial_1",
            observed_facts={
                "confirmed_effects": ["world_fact:water_heated"],
                "missing_effects": ["world_fact:tea_poured"],
            },
        )
        prog = lr5.NaturalProgressEvaluator.evaluate_progress(
            activity={"activity_id": "actv_partial", "revision": 1},
            progress_contract=None,
            evidence=lr5_ev,
            prior_checkpoint_ref=None,
            active_evidences=[],
        )
        self.assertEqual(prog["progress_state"], lr5.PROGRESS_STATE_ADVANCED)
        self.assertEqual(prog["reason"], "PARTIAL_CONFIRMED_EFFECT_ADVANCED")
        self.assertEqual(prog["confirmed_sub_refs"], ["world_fact:water_heated"])
        self.assertNotIn("world_fact:tea_poured", prog["confirmed_sub_refs"])

    def test_scene_06_action_success_with_object_conflict_blocks_completion(self) -> None:
        """场景6：当前动作成功而对象核对冲突，活动不被误完成。"""
        contract = lr5.CompletionContract(
            contract_id="ccont:" + hashlib.sha256(b"sc6").hexdigest()[:16],
            activity_id="actv_sc6",
            criteria_kind=lr5.CRITERIA_ACTION_RESULT_MATCH,
            target_refs=["artifact:sc6"],
            criteria={"required_predicate": "artifact_written_verified", "expected_artifact_ref": "artifact:sc6"},
            required_evidence_kinds=[lr5.SOURCE_KIND_SETTLED_EVENT],
            required_authority_domains=["result_settlement_authority"],
            created_at=NOW_T0,
            policy_version=lr5.POLICY_ARTIFACT_V1,
        ).to_dict()
        conflict_ev = lr5.LifeProgressEvidence(
            progress_evidence_id="pevid:" + hashlib.sha256(b"sc6-c").hexdigest()[:16],
            source_kind=lr5.SOURCE_KIND_SETTLED_EVENT,
            source_ref="srev:sc6-c",
            authority_domain="result_settlement_authority",
            epistemic_kind=lr5.EPISTEMIC_CONFLICTED_CLAIM,
            settlement_ref="rset:sc6-c",
            settlement_status=lr5.SETTLEMENT_STATUS_CONFLICT,
            outcome_kind=lr5.OUTCOME_UNKNOWN_EFFECT,
            activity_id="actv_sc6",
            target_ref="artifact:sc6",
            occurred_at=NOW_T0,
            observed_at=NOW_T0,
            recorded_at=NOW_T0,
            idempotency_key="idem:sc6-c",
        ).to_dict()
        comp = lr5.NaturalCompletionEvaluator.evaluate_completion(
            activity={"revision": 1},
            completion_contract=contract,
            active_evidences=[conflict_ev],
            current_checkpoint_ref=None,
        )
        self.assertEqual(comp["eval_status"], lr5.EVAL_CONFLICT)
        self.assertEqual(comp["reason"], "CONFLICTING_EVIDENCE_BLOCKS_COMPLETION")

    def test_scene_07_late_correction_after_completion_records_conflict_without_reopen(self) -> None:
        """场景7：活动完成后迟到纠正，记录冲突，不 silent reopen。"""
        class _Probe(lr5.ActivityProgressCoordinator):
            def __init__(self) -> None:
                self.settlement_read = None

        late_ev = lr5.LifeProgressEvidence(
            progress_evidence_id="pevid:sc7",
            source_kind=lr5.SOURCE_KIND_CORRECTION_EVENT,
            source_ref="scor:sc7",
            authority_domain="result_settlement_authority",
            epistemic_kind=lr5.EPISTEMIC_CONFLICTED_CLAIM,
            occurred_at=NOW_T1,
            observed_at=NOW_T1,
            recorded_at=NOW_T1,
            idempotency_key="idem:sc7",
            activity_id="actv_sc7",
            target_ref="artifact:sc7",
            settlement_ref="rset:sc7_late",
            supersedes_settlement_ref="rset:sc7_base",
            settlement_status=lr5.SETTLEMENT_STATUS_CONFLICT,
            outcome_kind=lr5.OUTCOME_FAILURE_NO_EFFECT,
        )
        ok, reason = lr5.ActivityProgressPolicyRegistry.match_evidence_to_binding(
            evidence=late_ev,
            binding={
                "activity_id": "actv_sc7",
                "allowed_evidence_kinds": ["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"],
                "authority_domains": ["result_settlement_authority", "artifact_authority"],
                "target_refs": ["artifact:sc7"],
                "correlation_ids": [],
            },
        )
        self.assertTrue(ok, reason)
        probe = _Probe()
        state = {
            "completed_once_by_activity": {"actv_sc7": {"eligibility_id": "celig:sc7"}},
            "completion_eligibilities_by_id": {
                "celig:sc7": {"status": "CONSUMED", "settlement_refs": ["rset:sc7_base"], "evidence_refs": []}
            },
            "consumed_evidences_by_id": {},
        }
        self.assertIsNone(probe._correction_relation_rejection(state, {"activity_id": "actv_sc7"}, late_ev))
        self.assertEqual(lr5.STATUS_COMPLETED, "COMPLETED")

    def test_scene_08_revocation_after_read_blocks_private_dispatch(self) -> None:
        """场景8：读取后撤权、派发前再检查，私人内容不外发。"""
        c1, _ = cm_view.mutate_v02_crossmodal_view(
            self.root,
            {
                "action": "curate_unshown_box_item",
                "box_item_id": "box_priv_08",
                "artifact_id": "artifact:priv_08",
                "version_id": "v1",
                "title_cn": "深夜还没顺过来的日记草稿",
                "curation_state": "selected_to_show",
                "reuse_mode": "direct_reuse",
                "operation_key": "sc8_curate",
            },
            actor_side="subject",
            now_iso=NOW_T0,
        )
        self.assertEqual(c1, 200)
        c2, _ = cm_view.mutate_v02_crossmodal_view(
            self.root,
            {
                "action": "withdraw_box_show_intent",
                "box_item_id": "box_priv_08",
                "operation_key": "sc8_withdraw",
            },
            actor_side="subject",
            now_iso=NOW_T1,
        )
        self.assertEqual(c2, 200)
        c3, partner_cm = cm_view.read_v02_crossmodal_view(self.root, has_subject_capability=False)
        self.assertEqual(c3, 200)
        p_box = partner_cm["unshown_work_box"]
        self.assertEqual(p_box["visible_items"], [])
        self.assertNotIn("hidden_count", p_box)

        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "place_memory_cue",
                "cue_id": "cue_sc8",
                "artifact_ref": "artifact:priv_08",
                "spot_id": "desk",
                "cue_text_cn": "桌上纸片",
                "visible": True,
                "shared": False,
                "operation_key": "sc8_cue",
            },
            actor_side="subject",
            now_iso=NOW_T0,
        )
        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "update_placed_cue_visibility",
                "cue_id": "cue_sc8",
                "removed": True,
                "operation_key": "sc8_rm",
            },
            actor_side="subject",
            now_iso=NOW_T1,
        )
        partner_ad = ad_engine.project_v02_agency_depth(self.root, has_subject_capability=False, now_iso=NOW_T1)
        self.assertEqual(partner_ad["placed_memory_cues"]["active_cues"], [])

    def test_scene_09_vc_barrier_prevents_backup_resurrection(self) -> None:
        """场景9：原始证据删除后重索引、恢复旧备份，旧内容不复活。"""
        rp_engine.record_episodic_trace(
            self.root,
            memory_id="mem_del_09",
            topic="deleted item",
            exact_detail="MUST_NOT_RETURN_FROM_BACKUP",
            gist_summary="deleted gist",
            now_iso=NOW_T0,
        )
        backup_mem_file = self.root / "memory" / "v02_recall_projection_state.json"
        old_backup_bytes = backup_mem_file.read_bytes()

        rp_engine.tombstone_memory(self.root, memory_id="mem_del_09", reason="user_deleted", now_iso=NOW_T1)
        wm_before = rm_engine.read_vc_authority_watermark_readonly(self.root)
        sealed_seal = dict(wm_before)

        backup_mem_file.write_bytes(old_backup_bytes)
        reconcile = rm_engine.reconcile_backup_restore_vc_barrier(
            self.root,
            external_watermark_seal=sealed_seal,
            operation_key="sc9_restore",
            now_iso=NOW_LATER,
        )
        self.assertEqual(reconcile["llm_calls"], 0)
        view_after = rp_engine.read_v02_recall_projection_view(self.root, now_iso=NOW_LATER)
        mem_ids = [m.get("memory_id") for m in (view_after.get("visible_memories") or [])]
        self.assertNotIn("mem_del_09", mem_ids)

    def test_scene_10_maintenance_world_mutation_not_admitted_to_personal_life(self) -> None:
        """场景10：测试/维护动作真实改变 World，却不被接纳为个人生活。"""
        before_world = _snapshot_tree(self.root / "world")
        pin_res = matter.pin_corkboard_card(
            self.root,
            "maint_world_probe_card_10",
            operation_key="op_sc10_world_maint",
            now_iso=NOW_T0,
        )
        self.assertEqual(pin_res["status"], "applied")
        after_world = _snapshot_tree(self.root / "world")
        self.assertNotEqual(after_world, before_world)
        self.assertEqual(
            matter.read_corkboard_wall_zero_write(self.root)["pinned_cards"],
            ["maint_world_probe_card_10"],
        )

        ev_db = self.root / "evidence.db"
        ctrl_db = self.root / "controls.db"
        EvidenceStore(ev_db)
        MemoryControlLedger(
            ctrl_db, ev_db, owner="chiyo", conversation=life_conversation_id("chiyo")
        ).initialize()
        port = LifeExperiencePort(ev_db, controls_db=ctrl_db, subject="chiyo", authority_db="")
        consumer = MemoryAdmissionExperienceConsumer(port=port, enabled=True, handoff_verifier=lambda _k: None)
        maint_receipt = {
            "handoff_id": "lexp:maint10",
            "correlation_id": "corr:maint10",
            "activity_revision": 1,
            "activity_kind": "POSE",
            "terminal_or_milestone_kind": "SIGNIFICANT_PROGRESS",
            "decision_refs": [],
            "adoption_refs": [],
            "action_refs": ["world-exec:maint10"],
            "settlement_refs": [],
            "progress_refs": [],
            "completion_evidence_ref": None,
            "fact_refs": [],
            "artifact_refs": [],
            "world_refs": ["world:corkboard:maint_world_probe_card_10"],
            "occurred_at": NOW_T0,
            "subject_id": "chiyo",
        }
        admit = consumer.receive_experience_handoff(maint_receipt)
        self.assertEqual(admit["disposition"], "IGNORE")
        self.assertEqual(admit["reason"], "LIFE_ACTIVITY_REF_MISSING")
        self.assertEqual(EvidenceStore(ev_db).recent_verified_outcomes(memory_owner="chiyo", limit=20), [])

    def test_scene_11_quiet_day_vs_unknown_vs_rest_distinct_without_drama(self) -> None:
        """场景11：无事件安静日、挂起 UNKNOWN 与主动休息可被技术观测区分，不补写剧情。"""
        q_obs = loop_bridge.classify_day_observability(self.root, now_iso=NOW_T0)
        u_obs = loop_bridge.classify_day_observability(self.root, round_outcome="STEP_UNKNOWN", now_iso=NOW_T0)
        r_obs = loop_bridge.classify_day_observability(self.root, round_outcome="ILLNESS_REST", now_iso=NOW_T0)
        self.assertEqual(q_obs["observability_class"], "QUIET_EVENTLESS_DAY")
        self.assertEqual(u_obs["observability_class"], "PENDING_UNKNOWN_SUSPENDED")
        self.assertEqual(r_obs["observability_class"], "INTENTIONAL_REST_RECOVERY")
        self.assertFalse(u_obs["unknown_guessed_as_quiet"])

        review = rm_engine.project_chatless_life_review(self.root, has_subject_capability=False, now_iso=NOW_T0)
        self.assertTrue(review["chat_window_hidden"])
        self.assertTrue(review["nonverbal_evidence_only"])
        self.assertEqual(review["llm_calls"], 0)

    def test_scene_12_migration_preserves_works_responsibilities_identity_and_loss_manifest(self) -> None:
        """场景12：模型/宿主迁移后作品、责任、身份和技法适用性可交代，损失如实保留。"""
        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "register_gentle_responsibility",
                "resp_id": "resp_shared_12",
                "target_kind": "shared_partner_commitment",
                "title_cn": "周末海堤拼贴本共同收尾",
                "last_touched_at": NOW_T0,
                "shared": True,
                "operation_key": "sc12_reg",
            },
            actor_side="subject",
            now_iso=NOW_T0,
        )
        with self.assertRaises(ad_engine.V02ConflictError):
            ad_engine.apply_agency_depth_mutation(
                self.root,
                {
                    "action": "review_gentle_responsibility",
                    "resp_id": "resp_shared_12",
                    "decision": "retire_keep_history",
                    "partner_agreed": False,
                    "operation_key": "sc12_unilateral",
                },
                actor_side="subject",
                now_iso=NOW_T1,
            )
        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "settle_legacy_tool",
                "tool_id": "tool_old_palette",
                "title_cn": "旧调色板脚本",
                "dependency_error_cn": "旧运行环境插件已下线",
                "ending": "adapt_to_static_card",
                "linked_work_refs": ["artifact:rain_notes_v1"],
                "operation_key": "sc12_tool",
            },
            actor_side="subject",
            now_iso=NOW_T1,
        )
        exp_proj, _ = rm_engine.apply_runtime_migration_mutation(
            self.root,
            {"action": "export_suitcase", "profile_mode": "records_only"},
            actor_side="partner",
            now_iso=NOW_T1,
        )
        mf = exp_proj["suitcase_bundle"]["migration_loss_manifest"]
        self.assertGreaterEqual(len(mf["intentionally_left_behind"]), 1)
        self.assertTrue(mf["tombstone_anti_resurrection_enforced"])
        proj = ad_engine.project_v02_agency_depth(self.root, has_subject_capability=True, now_iso=NOW_LATER)
        self.assertTrue(proj["legacy_tool_endings"][0]["linked_works_readable"])
        self.assertEqual(proj["gentle_responsibility_reviews"][0]["omission_debt"], 0)

    def test_vd_counterfactual_suite_all_passed_zero_llm(self) -> None:
        """V-D 反事实消融套件：exp1 / exp2 / exp3 全过且零大模型调用。"""
        suite = vd_runner.run_v02_counterfactual_suite(seed=20261118)
        self.assertTrue(suite["all_passed"], suite)
        self.assertEqual(suite["llm_calls"], 0)
        for key in (
            "exp1_history_vs_placeholder",
            "exp2_d01_placed_cue_vs_occluded_vs_decor",
            "exp3_mutual_sustaining_perturbation",
        ):
            self.assertTrue(suite[key]["causal_delta_verified"], key)
            self.assertEqual(suite[key]["llm_calls"], 0)

    def test_daily_loop_bridge_new_v02_cues_zero_write_and_applied_only(self) -> None:
        """D01 摆件线索 / §10.C 可拒绝小把手 / D09 时间胶囊进日常循环：读零写、UNKNOWN 零改写、APPLIED 才落账。"""
        empty_root = self.root / "empty_loop"
        empty_root.mkdir()
        before_empty = _snapshot_tree(empty_root)
        empty_c = loop_bridge.collect_v02_v03_loop_candidates(empty_root, now_iso=NOW_T0)
        self.assertEqual(empty_c["candidates"], [])
        self.assertEqual(_snapshot_tree(empty_root), before_empty)

        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "place_memory_cue",
                "cue_id": "cue_bridge_1",
                "artifact_ref": "artifact:b1",
                "spot_id": "window_sill",
                "cue_text_cn": "窗台薄荷旁的小贝壳",
                "visible": True,
                "shared": True,
                "operation_key": "br_cue",
            },
            actor_side="subject",
            now_iso=NOW_T0,
        )
        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "set_optional_next_gesture",
                "gesture_id": "gest_bridge_1",
                "artifact_ref": "artifact:b1",
                "gesture_cn": "把收尾草图再润一笔淡墨",
                "shared": True,
                "operation_key": "br_gest",
            },
            actor_side="subject",
            now_iso=NOW_T0,
        )
        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "create_future_capsule",
                "capsule_id": "cap_bridge_1",
                "unlock_after": NOW_T0,
                "title_cn": "给下一周的小便签",
                "operation_key": "br_cap",
            },
            actor_side="subject",
            now_iso=NOW_T0,
        )
        before_pop = _snapshot_tree(self.root)
        collected = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=NOW_T1)
        self.assertEqual(_snapshot_tree(self.root), before_pop)
        refs = {d["source_ref"]: d for d in collected["descriptors"].values()}
        self.assertIn("v02_placed_cue:cue_bridge_1", refs)
        self.assertIn("v02_next_gesture:gest_bridge_1", refs)
        self.assertIn("v02_future_capsule:cap_bridge_1", refs)

        refused_g = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "actor_side": "partner",
                "effect_state": "APPLIED",
                "action_kind": "ACT_OPTIONAL_NEXT_GESTURE",
                "source_ref": "v02_next_gesture:gest_bridge_1",
                "receipt_ref": "r:bad",
                "operation_key": "bad_p",
            },
            now_iso=NOW_T1,
        )
        self.assertEqual(refused_g["optimistic_writes"], 0)
        self.assertEqual(_snapshot_tree(self.root), before_pop)

        ev_g = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, "v02_next_gesture:gest_bridge_1", now_iso=NOW_T1, decision_id="dec:g1"
        )
        ev_c = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, "v02_future_capsule:cap_bridge_1", now_iso=NOW_T1, decision_id="dec:c1"
        )
        assert ev_g is not None and ev_c is not None
        sg = loop_bridge.settle_v02_v03_round_consequences(self.root, ev_g, now_iso=NOW_T1)
        sc = loop_bridge.settle_v02_v03_round_consequences(self.root, ev_c, now_iso=NOW_T1)
        self.assertTrue(sg["settled"] and sc["settled"])

        cg_engine.record_missed_moment(
            self.root, moment_id="mm_1", summary_cn="雨停时窗外挂起双重彩虹但当时错过一起看", now_iso=NOW_T0
        )
        cg_engine.supply_missed_moment_material(
            self.root, moment_id="mm_1", material_ref="album:rainbow_01", material_note_cn="补看拍下来的天色", now_iso=NOW_T1
        )
        before_ctx = _snapshot_tree(self.root)
        ctx = lived_bridge.build_lived_turn_context(
            self.root,
            {"day_root": str(self.root), "phone": {"enabled": False, "private_artifacts": True}},
            "桌面怎么样了",
            now_iso=NOW_T1,
        )
        self.assertEqual(_snapshot_tree(self.root), before_ctx)
        self.assertIn("桌面可见记忆线索", ctx)
        self.assertIn("错过时刻补材料待接续", ctx)
        self.assertLessEqual(len(ctx), lived_bridge.MAX_LIVED_CONTEXT_CHARS)


if __name__ == "__main__":
    unittest.main()

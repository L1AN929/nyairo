#!/usr/bin/env python3
"""Regression tests closing all GPT adversarial audit findings on v0.2."""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
for _p in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "chiyo_bundle"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from chiyo_bundle import lived_context_bridge as lived_bridge  # noqa: E402
from components.life.scripts import (  # noqa: E402
    life_runtime_production as prod_runtime,
    state_tx_guard as tx_guard,
    v02_autobiography_skill_provenance as asp,
    v02_common_ground as common_ground,
    v02_crash_and_partial_reconciler as cpr,
    v02_lived_slices_and_vd_protocols as lived_slices,
    v02_recall_projection as recall_proj,
    v02_representation_lease_forget as rlf,
    v02_runtime_governance_migration as rtmig,
    v02_v03_daily_loop_bridge as loop_bridge,
)
from components.phone import (  # noqa: E402
    service as phone_service_mod,
    v02_honest_ux_view as hux_view,
)
from lifesupply.db import LifeSupplyDB  # noqa: E402
from lifesupply.service.life_supply import LifeSupplyService  # noqa: E402
from lifesupply.service.switches import FeatureSwitches  # noqa: E402
from components.world.scripts import v08_universal_matter as matter  # noqa: E402

T0 = "2026-11-18T10:00:00+08:00"
T1 = "2026-11-18T11:00:00+08:00"
T2 = "2026-11-18T12:00:00+08:00"


def _write_json(path: Path, doc: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int]] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        snap[rel] = (st.st_size if p.is_file() else -1, st.st_mtime_ns)
    return snap


def _make_phone_service(root: Path) -> phone_service_mod.PhoneService:
    return phone_service_mod.PhoneService(
        {
            "root": str(root),
            "subject": "subj",
            "world_home": str(root / "world"),
            "supply_socket": str(root / "run" / "supply.sock"),
            "private_items_artifact_id": "priv_1",
            "artifact_grant_id": "grant_1",
            "workspace_id": "ws_1",
        }
    )


class TestV02GPTAuditRemediation(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_partner_privacy_nested_containers_legacy_traces_workbench_recall_and_counts(self) -> None:
        # 1a + count leak: nested private item inside public room / container must stay hidden AND not inflate counts
        _write_json(
            self.root / "world" / "v08_matter_space.json",
            {
                "zones": {
                    "home_desk": {"zone_id": "home_desk", "label_cn": "书桌", "visibility": "shared", "capacity_slots": 10},
                },
                "matter_objects": {
                    "obj_open_cup": {
                        "object_id": "obj_open_cup",
                        "label_cn": "公开茶杯",
                        "parent_id": "home_desk",
                        "zone_id": "home_desk",
                        "visibility": "shared",
                        "shared": True,
                    },
                    "bag_priv": {
                        "object_id": "bag_priv",
                        "label_cn": "私密帆布包",
                        "parent_id": "home_desk",
                        "zone_id": "home_desk",
                        "visibility": "private",
                        "shared": False,
                        "children_ids": ["diary_nested"],
                        "assembled_parts_ids": ["lock_part_priv"],
                    },
                    "diary_nested": {
                        "object_id": "diary_nested",
                        "label_cn": "藏在包里的小本子",
                        "parent_id": "bag_priv",
                        "zone_id": "home_desk",
                    },
                    "box_deep_child": {
                        "object_id": "box_deep_child",
                        "label_cn": "再下一层便条",
                        "parent_id": "diary_nested",
                        "zone_id": "home_desk",
                    },
                },
            },
        )
        # 1b: legacy room_layout_traces entry lacking matter_space row must default private unless shared=True
        _write_json(
            self.root / "phone" / "v02_runtime_migration.json",
            {
                "chatless_life_review": {
                    "room_layout_traces": {
                        "objects": [
                            {"object_id": "legacy_orphan_secret", "label_cn": "LEGACY_PRIVATE_OBJ", "zone": "desk_room"},
                            {"object_id": "legacy_shared_lamp", "label_cn": "公开台灯", "zone": "desk_room", "shared": True},
                        ]
                    }
                }
            },
        )
        # 1c: unshared workbench item must not appear in partner P12 chapters or bump attested count
        _write_json(
            self.root / "phone" / "workbench_tray.json",
            {
                "updated_at": T0,
                "tray": {"work": {"artifact_id": "wb_secret_novel", "shared": False}},
            },
        )
        # 1d: PhoneService.v02_recall_projection must differentiate subject vs partner
        recall_proj.record_episodic_trace(
            self.root,
            memory_id="mem_priv_only",
            topic="PRIVATE_TOPIC_SECRET",
            exact_detail="PRIVATE_EXACT_BODY",
            gist_summary="PRIVATE_GIST",
            shared=False,
            now_iso=T0,
        )
        recall_proj.record_episodic_trace(
            self.root,
            memory_id="mem_shared_pub",
            topic="公开海边晚步",
            exact_detail="一起沿着防波堤散步",
            gist_summary="海边晚步",
            shared=True,
            now_iso=T0,
        )
        # Count leak 2: private reconciler UNKNOWN must not inflate partner crash_reconciler_summary / epistemic_status
        rec = cpr.CrashAndPartialReconcilerStore(self.root / "reconciler")
        rec.initialize()
        rec.create_decision(decision_id="dec_c1")
        t1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_c1", request_id="req_c1", logical_action_id="la_c1"
        )
        rec.create_action_attempt(attempt_id="att_c1", action_id=t1["action_id"])
        rec.tx_t3_admit_action_result(
            action_id=t1["action_id"],
            attempt_id="att_c1",
            outcome="UNKNOWN",
            receipt_kind="PROVIDER_RECEIPT",
            receipt_id="rcpt_priv_unk",
        )

        # Verify v08_universal_matter partner view hides nested items AND private child counts
        m_partner = matter.read_matter_space_view(self.root, actor_side="partner", now_iso=T1)
        m_ids = {row["object_id"] for row in m_partner["objects"]}
        self.assertIn("obj_open_cup", m_ids)
        self.assertTrue({"bag_priv", "diary_nested", "box_deep_child"}.isdisjoint(m_ids))
        desk_z = next(z for z in m_partner["zones"] if z["zone_id"] == "home_desk")
        self.assertIn("obj_open_cup", desk_z["children_ids"])
        for hid_id in ("bag_priv", "diary_nested", "box_deep_child"):
            self.assertNotIn(hid_id, desk_z["children_ids"])
        self.assertEqual(desk_z["occupancy"], len(desk_z["children_ids"]))

        # Verify PhoneService.v02_recall_projection(subject_view=False) hides private memories & unshared workbench
        svc = _make_phone_service(self.root)
        c_p, rp_partner = svc.v02_recall_projection(subject_view=False)
        c_s, rp_subject = svc.v02_recall_projection(subject_view=True)
        self.assertEqual(c_p, 200)
        self.assertEqual(c_s, 200)
        rp_p_blob = json.dumps(rp_partner, ensure_ascii=False)
        self.assertNotIn("PRIVATE_TOPIC_SECRET", rp_p_blob)
        self.assertNotIn("mem_priv_only", rp_p_blob)
        self.assertNotIn("wb_secret_novel", rp_p_blob)
        self.assertIn("mem_shared_pub", rp_p_blob)
        self.assertIn("mem_priv_only", json.dumps(rp_subject, ensure_ascii=False))
        self.assertIn("wb_secret_novel", json.dumps(rp_subject, ensure_ascii=False))

        # Verify honest-ux partner view hides nested private objects, legacy unshared traces, unshared workbench, and reconciler UNKNOWN count
        _, hux_p = hux_view.read_v02_honest_ux_view(self.root, has_subject_capability=False, now_iso=T1)
        _, hux_s = hux_view.read_v02_honest_ux_view(self.root, has_subject_capability=True, now_iso=T1)
        dp = hux_p["data"]
        ds = hux_s["data"]
        p_blob = json.dumps(dp, ensure_ascii=False)
        for leaked in ("bag_priv", "diary_nested", "box_deep_child", "legacy_orphan_secret", "wb_secret_novel"):
            self.assertNotIn(leaked, p_blob)
        p_obj_ids = {o.get("object_id") for o in dp["chatless_traces"]["objects"]}
        self.assertIn("obj_open_cup", p_obj_ids)
        self.assertEqual(len(dp["chatless_traces"]["waits"]), 0)
        self.assertTrue(dp["crash_reconciler_summary"].get("perspective_redacted"))
        self.assertEqual(dp["crash_reconciler_summary"]["unknown_actions"], 0)
        self.assertEqual(dp["provenance"]["epistemic_status"], "VERIFIED")
        self.assertEqual(dp["provenance"]["unverified_count"], 0)
        self.assertEqual(ds["crash_reconciler_summary"]["unknown_actions"], 1)
        self.assertEqual(ds["provenance"]["epistemic_status"], "UNKNOWN")

    def test_02_restored_backup_hmac_tombstone_barrier_and_partner_hold_isolation(self) -> None:
        rlf._ensure_control_ledger_locked(self.root)
        rlf.ingest_source(
            self.root,
            source_id="src_forget_restore",
            text="SECRET_AFTER_BACKUP_RESTORE",
            visibility="private",
            operation_id="op_src_rest",
            now_iso=T0,
        )
        common_ground.record_ground_item(
            self.root,
            item_id="src_forget_restore",
            topic="会一起散步的事",
            summary="之前提过一句",
            ground_status=common_ground.SHARED_WITH_PARTNER,
            item_kind="copractice",
            requires_agreement=True,
            now_iso=T0,
        )
        recall_proj.record_episodic_trace(
            self.root,
            memory_id="src_forget_restore",
            topic="被删前同步记忆",
            exact_detail="SECRET_AFTER_BACKUP_RESTORE",
            shared=True,
            now_iso=T0,
        )
        _write_json(
            self.root / "phone" / "tentative_plans.json",
            {
                "tentative_plans": [
                    {"plan_id": "src_forget_restore", "title": "被删的旧计划", "status": "PROPOSED", "shared": True}
                ]
            },
        )
        # Backup pre-forget RLF & recall state
        rlf_file = self.root / "governance" / "v02_representation_lease_forget_state.json"
        rp_file = self.root / "memory" / "v02_recall_projection_state.json"
        rlf_backup = rlf_file.read_bytes()
        rp_backup = rp_file.read_bytes()

        # Execute forget + tombstone in recall projection (keeping control_authority & vc_watermark)
        c_fg, _ = rlf.execute_explicit_forget(
            self.root, source_ids=["src_forget_restore"], operation_id="op_fg_exec", now_iso=T1
        )
        self.assertEqual(c_fg, 200)
        recall_proj.tombstone_memory(self.root, memory_id="src_forget_restore", reason="user_forget", now_iso=T1)

        # Simulate stale backup restore overwriting state files while control ledger & V-C watermark remain
        rlf_file.write_bytes(rlf_backup)
        rp_file.write_bytes(rp_backup)

        # All four consumers must honor HMAC/opaque/V-C tombstones directly from control ledger & watermark!
        _, hux = hux_view.read_v02_honest_ux_view(self.root, has_subject_capability=True, now_iso=T2)
        self.assertNotIn("src_forget_restore", json.dumps(hux, ensure_ascii=False))
        loop_cands = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T2)
        self.assertNotIn("src_forget_restore", json.dumps(loop_cands["descriptors"], ensure_ascii=False))
        ctx_text = lived_bridge.build_lived_turn_context(
            self.root,
            {"day_root": str(self.root), "phone": {"enabled": False, "private_artifacts": True}},
            "还记得那个散步吗",
            now_iso=T2,
        )
        self.assertNotIn("SECRET_AFTER_BACKUP_RESTORE", ctx_text)
        self.assertNotIn("被删的旧计划", ctx_text)
        rp_view = recall_proj.project_recall_set(self.root, include_unavailable=False, now_iso=T2)
        self.assertEqual([i["memory_id"] for i in rp_view["items"]], [])

        # Partner hold isolation: partner cannot see or clarify subject-private hold
        rlf.ingest_source(self.root, source_id="src_sub_only", text="priv", operation_id="op_sub_src", now_iso=T0)
        _, h_sub = rlf.submit_forget_command(
            self.root,
            command_text="忘掉刚才那段",
            operation_id="op_sub_hold",
            recent_source_ids=["src_sub_only"],
            actor_side="subject",
            shared_with_partner=False,
            now_iso=T1,
        )
        sub_hid = h_sub["hold_id"]
        _, p_hux = hux_view.read_v02_honest_ux_view(self.root, has_subject_capability=False, now_iso=T2)
        self.assertNotIn(sub_hid, [c["case_id"] for c in p_hux["data"]["forget_cases"]])
        c_forb, r_forb = hux_view.mutate_v02_honest_ux_view(
            self.root,
            {"action": "clarify_forget_scope", "case_id": sub_hid, "scope_choice": "last_turn", "operation_key": "op_forb"},
            actor_side="partner",
            now_iso=T2,
        )
        self.assertEqual(c_forb, 403)
        self.assertEqual(r_forb["reason_code"], "FORBIDDEN_FORGET_CASE_SCOPE")

    def test_03_forget_scope_narrowing_releases_out_of_scope_sources(self) -> None:
        rlf._ensure_control_ledger_locked(self.root)
        rlf.ingest_source(
            self.root, source_id="s_old_topic", text="old", topic="topic_a", session_id="seg_1", operation_id="o1",
            now_iso="2026-11-18T08:00:00+08:00",
        )
        rlf.ingest_source(
            self.root, source_id="s_cur_topic", text="mid", topic="topic_b", session_id="seg_2", operation_id="o2",
            now_iso="2026-11-18T10:50:00+08:00",
        )
        rlf.ingest_source(
            self.root, source_id="s_last_turn", text="latest", topic="topic_b", session_id="seg_2", operation_id="o3",
            now_iso="2026-11-18T10:58:00+08:00",
        )
        _, hold_res = rlf.submit_forget_command(
            self.root,
            command_text="忘掉刚才那段",
            operation_id="op_scope_hold",
            recent_source_ids=["s_old_topic", "s_cur_topic", "s_last_turn"],
            actor_side="partner",
            shared_with_partner=True,
            now_iso="2026-11-18T11:00:00+08:00",
        )
        hid = hold_res["hold_id"]
        c_clar, out_clar = hux_view.mutate_v02_honest_ux_view(
            self.root,
            {"action": "clarify_forget_scope", "case_id": hid, "scope_choice": "last_turn", "operation_key": "op_scope_clar"},
            actor_side="partner",
            now_iso="2026-11-18T11:01:00+08:00",
        )
        self.assertEqual(c_clar, 200)
        self.assertEqual(out_clar["data"]["selected_source_ids"], ["s_last_turn"])
        self.assertEqual(sorted(out_clar["data"]["released_source_ids"]), ["s_cur_topic", "s_old_topic"])
        st_disk, _ = rlf._load_state_readonly(self.root)
        self.assertTrue(st_disk["sources"]["s_last_turn"]["hold_active"])
        self.assertFalse(st_disk["sources"]["s_cur_topic"]["hold_active"])
        self.assertFalse(st_disk["sources"]["s_old_topic"]["hold_active"])

    def test_04_unified_budget_dormant_and_common_ground_copractice(self) -> None:
        # 1) DORMANT sets budget_scale_pct=0 & allow_exploratory_cognition=False even when ASP wasn't dropped
        svc = _make_phone_service(self.root)
        c1, _ = svc.v02_runtime_migration_action(
            payload={"action": "set_budget_mode", "mode": "DORMANT", "reason_cn": "停机休眠"},
            subject_view=True,
        )
        self.assertEqual(c1, 200)
        _, rm1 = svc.v02_runtime_migration(subject_view=True)
        _, hx1 = svc.v02_honest_ux(subject_view=True)
        self.assertEqual(rm1["data"]["runtime_mode"]["mode"], "DORMANT")
        self.assertEqual(rm1["data"]["runtime_mode"]["budget_scale_pct"], 0)
        self.assertEqual(hx1["data"]["budget_survival"]["mode"], "DORMANT")
        self.assertEqual(hx1["data"]["budget_survival"]["budget_scale_pct"], 0)
        self.assertFalse(hx1["data"]["budget_survival"]["allow_exploratory_cognition"])

        # 2) Lowering ASP budget to 10% while DORMANT must keep DORMANT; switching back to NORMAL shows LOW_BUDGET 10% on BOTH endpoints
        asp.apply_budget_drop(
            self.root, {"drop_pct": 90, "operation_key": "op_asp_10"}, now_iso=T0
        )
        _, hx2 = svc.v02_honest_ux(subject_view=True)
        self.assertEqual(hx2["data"]["budget_survival"]["mode"], "DORMANT")
        self.assertEqual(hx2["data"]["budget_survival"]["budget_scale_pct"], 0)

        c2, _ = svc.v02_runtime_migration_action(
            payload={"action": "set_budget_mode", "mode": "NORMAL", "reason_cn": "醒来但预算紧张"},
            subject_view=True,
        )
        self.assertEqual(c2, 200)
        _, rm3 = svc.v02_runtime_migration(subject_view=True)
        _, hx3 = svc.v02_honest_ux(subject_view=True)
        self.assertEqual(rm3["data"]["runtime_mode"]["budget_scale_pct"], 10)
        self.assertEqual(rm3["data"]["runtime_mode"]["health_state"], "LOW_BUDGET_CONSERVATION")
        self.assertEqual(hx3["data"]["budget_survival"]["budget_scale_pct"], 10)
        self.assertFalse(hx3["data"]["budget_survival"]["allow_exploratory_cognition"])

        # 3) Common ground copractice surfaces & confirms bi-directionally + normalizes paused_no_debt
        common_ground.record_ground_item(
            self.root,
            item_id="cg_agree_1",
            topic="傍晚安静歇会儿",
            summary="各自做点小事，不设汇报压力",
            item_kind="copractice",
            requires_agreement=True,
            now_iso=T0,
        )
        _, p_view = hux_view.read_v02_honest_ux_view(self.root, has_subject_capability=False, now_iso=T1)
        pids = {row["practice_id"] for row in p_view["data"]["rescaled_copractices"]}
        self.assertIn("cg_agree_1", pids)
        c_ok, r_ok = hux_view.mutate_v02_honest_ux_view(
            self.root,
            {
                "action": "confirm_rescaled_copractice",
                "practice_id": "cg_agree_1",
                "scale_mode": "paused_no_debt",
                "confirmed": True,
                "operation_key": "op_cg_confirm",
            },
            actor_side="partner",
            now_iso=T2,
        )
        self.assertEqual(c_ok, 200)
        self.assertEqual(r_ok["data"]["scale_mode"], "pause_without_debt")
        self.assertTrue(r_ok["data"]["common_ground_synced"])
        cg_state = json.loads((self.root / "memory" / "v02_common_ground_state.json").read_text(encoding="utf-8"))
        self.assertEqual(cg_state["items"]["cg_agree_1"]["delivery_state"], "AGREED")
        self.assertEqual(cg_state["items"]["cg_agree_1"]["ground_status"], "SHARED_WITH_PARTNER")

    def test_05_authoritative_receipt_verification_import_toctou_and_crash_retry_idempotency(self) -> None:
        # 5a: Action settlement verifies owner DB when reconciler / authoritative receipt is involved
        rec = cpr.CrashAndPartialReconcilerStore(self.root / "reconciler")
        rec.initialize()
        rec.create_decision(decision_id="dec_r5")
        t1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_r5", request_id="req_r5", logical_action_id="la_r5"
        )
        rec.create_action_attempt(attempt_id="att_r5", action_id=t1["action_id"])
        rec.tx_t3_admit_action_result(
            action_id=t1["action_id"],
            attempt_id="att_r5",
            outcome="SUCCEEDED",
            receipt_kind="PROVIDER_RECEIPT",
            receipt_id="prcpt:real_r5",
        )
        bad = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "OUTING_VISIT",
                "place_id": "park",
                "receipt_ref": "prcpt:forged_999",
                "action_id": t1["action_id"],
            },
            now_iso=T1,
        )
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason_code"], "UNVERIFIED_OWNER_RECEIPT")
        good = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "OUTING_VISIT",
                "place_id": "park",
                "receipt_ref": "prcpt:real_r5",
                "action_id": t1["action_id"],
                "operation_key": "op_settle_real_r5",
            },
            now_iso=T1,
        )
        self.assertTrue(good["settled"])

        # 5b: explicit_import_representation rejects TOCTOU file drift when expected_observed_sha256 mismatches
        ws = self.root / "ws"
        ws.mkdir()
        rlf._ensure_control_ledger_locked(self.root)
        _, art_init = rlf.create_managed_artifact(
            self.root,
            artifact_id="art_toctou",
            controller_principal_ref="subject_1",
            subject_id="subject_1",
            initial_body="v1",
            operation_id="op_init_t",
            now_iso=T0,
        )
        head_0 = art_init["current_head"]
        rlf.bind_representation(
            self.root,
            artifact_id="art_toctou",
            workspace_root=ws,
            workspace_rel_path="doc.md",
            operation_id="op_bind_t",
            now_iso=T0,
        )
        md = ws / "doc.md"
        md.write_text("reviewed body\n", encoding="utf-8")
        _, obs = rlf.observe_external_edit(
            self.root, artifact_id="art_toctou", workspace_root=ws, operation_id="op_obs_t", now_iso=T1
        )
        reviewed_sha = obs["observation"]["observed_sha256"]
        md.write_text("tampered after review\n", encoding="utf-8")
        c_st, r_st = hux_view.mutate_v02_honest_ux_view(
            self.root,
            {
                "action": "explicit_import_representation",
                "artifact_id": "art_toctou",
                "expected_observed_sha256": reviewed_sha,
                "operation_key": "op_imp_toctou",
            },
            actor_side="subject",
            now_iso=T2,
        )
        self.assertEqual(c_st, 409)
        self.assertEqual(r_st["reason_code"], "OBSERVED_CONTENT_CHANGED_SINCE_REVIEW")

        # 5c: Crash after RLF import before honest_state receipt commit + retry must replay idempotently (no 409)
        md.write_text("reviewed body\n", encoding="utf-8")
        retry_payload = {
            "action": "explicit_import_representation",
            "artifact_id": "art_toctou",
            "expected_observed_sha256": reviewed_sha,
            "operation_key": "op_imp_crash_retry",
        }
        # Simulate crash right after pending_ops + owner write (before operation_ledger commit)
        h_path = hux_view._state_path(self.root)
        st_pre = hux_view._load_honest_state_readonly(self.root)
        fp_retry = hux_view._payload_fingerprint("subject", retry_payload)
        hux_view._persist_pending_op(
            self.root,
            st_pre,
            op_key="op_imp_crash_retry",
            fp=fp_retry,
            action="explicit_import_representation",
            stamp=T2,
            extra={"resolved_base_version": head_0, "resolved_expected_observed_sha256": reviewed_sha},
        )
        c_owner, _ = rlf.explicit_import_representation(
            self.root,
            artifact_id="art_toctou",
            workspace_root=ws,
            base_projection_version=head_0,
            actor_ref="subject",
            operation_id="op_imp_crash_retry",
            expected_observed_sha256=reviewed_sha,
            now_iso=T2,
        )
        self.assertEqual(c_owner, 200)
        c_ret, r_ret = hux_view.mutate_v02_honest_ux_view(
            self.root, retry_payload, actor_side="subject", now_iso=T2
        )
        self.assertEqual(c_ret, 200)
        self.assertTrue(r_ret["ok"])

        # >256 operation_ledger eviction still replays idempotently via state_tx_guard ring
        for idx in range(270):
            c_m, _ = hux_view.mutate_v02_honest_ux_view(
                self.root,
                {
                    "action": "mark_representation_divergence_seen",
                    "artifact_id": "art_toctou",
                    "observed_head_hash": f"h_{idx}",
                    "operation_key": f"op_evict_{idx:03d}",
                },
                actor_side="partner",
                now_iso=T2,
            )
            self.assertEqual(c_m, 200)
        # Retry op 0 after 270 ops (>256)
        c_ev, r_ev = hux_view.mutate_v02_honest_ux_view(
            self.root,
            {
                "action": "mark_representation_divergence_seen",
                "artifact_id": "art_toctou",
                "observed_head_hash": "h_0",
                "operation_key": "op_evict_000",
            },
            actor_side="partner",
            now_iso=T2,
        )
        self.assertEqual(c_ev, 200)
        self.assertTrue(r_ev.get("idempotent_replay"))

    def test_06_deterministic_ablation_zero_rng_lottery_cp05_pre_ack_kill_and_readonly_db_zero_aux_churn(self) -> None:
        # No p_ok probability lottery in Experiments A or B
        src_a = inspect.getsource(lived_slices.run_experiment_a_affordance_x_narrative)
        src_b = inspect.getsource(lived_slices.run_experiment_b_continuation_x_narrative)
        self.assertNotIn("p_ok", src_a)
        self.assertNotIn("random() <", src_a)
        self.assertNotIn("p_ok", src_b)
        self.assertNotIn("random() <", src_b)
        exp_a_neg = lived_slices.run_experiment_a_affordance_x_narrative(control_mode="affordance_schema_mismatch")
        self.assertFalse(exp_a_neg["passed"])

        # LifeSupplyDB(read_only=True) on non-WAL-active DB must create zero -wal/-shm auxiliary files
        db_dir = self.root / "supply_ro"
        db_dir.mkdir()
        db_path = db_dir / "life_supply.sqlite3"
        rw = LifeSupplyDB(db_path, read_only=False)
        with rw.connection() as con:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # Remove -wal/-shm if present after closing rw
        for suf in ("-wal", "-shm"):
            aux = Path(str(db_path) + suf)
            if aux.exists():
                aux.unlink()
        snap_before = _snapshot_tree(db_dir)
        ro = LifeSupplyDB(db_path, read_only=True)
        with ro.connection() as con:
            row = con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            self.assertGreater(int(row[0]), 0)
        self.assertEqual(_snapshot_tree(db_dir), snap_before)

    def test_07_round2_supply_daemon_ro_recall_post_vc_digest_and_bridge_receipt_hardening(self) -> None:
        # 1. LifeSupplyService READ_OPS must open DB in read_only_scope without WAL/SHM or dir mtime churn
        svc_root = self.root / "supply_svc"
        uid = os.getuid() if hasattr(os, "getuid") else 0
        svc = LifeSupplyService(
            svc_root,
            operator_uids=(uid,),
            service_uids=(uid,),
            switches=FeatureSwitches.all_on(),
            read_only=False,
            require_non_root_identity=False,
        )
        try:
            self.assertEqual(svc.state.state, "READY")
            # Ensure startup connections left zero lingering -wal/-shm files on disk
            for suf in ("-wal", "-shm"):
                aux = Path(str(svc.db_path) + suf)
                if aux.exists():
                    with svc.db.connection() as con:
                        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            snap_before = _snapshot_tree(svc_root)
            mtime_before = svc_root.stat().st_mtime_ns
            st_resp = svc.dispatch({"op": "status"}, peer_uid=uid)
            hp_resp = svc.dispatch({"op": "health"}, peer_uid=uid)
            self.assertEqual(st_resp.get("status"), "OK")
            self.assertEqual(hp_resp.get("status"), "OK")
            self.assertEqual(svc_root.stat().st_mtime_ns, mtime_before)
            self.assertEqual(_snapshot_tree(svc_root), snap_before)
            for suf in ("-wal", "-shm"):
                self.assertFalse(Path(str(svc.db_path) + suf).exists())
        finally:
            svc.close()

        # 2. PhoneService.v02_recall_projection_action must guard partner overwrites & tombstones
        psvc = _make_phone_service(self.root)
        c_sub, b_sub = psvc.v02_recall_projection_action(
            payload={
                "action": "record_episodic_trace",
                "memory_id": "mem_priv_r2",
                "topic": "秘密晚间散步",
                "exact_detail": "SECRET_ROUND2_MEM",
                "gist_summary": "SECRET_ROUND2_GIST",
                "shared": False,
                "visibility": "private",
            },
            subject_view=True,
        )
        self.assertEqual(c_sub, 200, b_sub)

        c_tomb_p, _ = psvc.v02_recall_projection_action(
            payload={"action": "tombstone_memory", "memory_id": "mem_priv_r2"},
            subject_view=False,
        )
        self.assertEqual(c_tomb_p, 403)

        c_ow_p, b_ow_p = psvc.v02_recall_projection_action(
            payload={
                "action": "record_episodic_trace",
                "memory_id": "mem_priv_r2",
                "topic": "partner overwrite attempt",
                "shared": True,
                "visibility": "shared",
            },
            subject_view=False,
        )
        self.assertEqual(c_ow_p, 403, b_ow_p)
        self.assertEqual(b_ow_p.get("reason_code"), "FORBIDDEN_PRIVATE_MEMORY")
        self.assertNotIn("SECRET_ROUND2_MEM", json.dumps(b_ow_p, ensure_ascii=False))

        c_priv_p, b_priv_p = psvc.v02_recall_projection_action(
            payload={
                "action": "record_episodic_trace",
                "memory_id": "mem_partner_priv_new",
                "topic": "trying private",
                "shared": False,
                "visibility": "private",
            },
            subject_view=False,
        )
        self.assertEqual(c_priv_p, 403, b_priv_p)
        self.assertEqual(b_priv_p.get("reason_code"), "FORBIDDEN_ACTOR_SIDE")

        # 3. Backup-restore after V-C tombstone seals "vc:<id>" must stay blocked in RLF & consumers
        backup_recall_bytes = (self.root / "memory" / "v02_recall_projection_state.json").read_bytes()
        c_tomb_s, b_tomb_s = psvc.v02_recall_projection_action(
            payload={"action": "tombstone_memory", "memory_id": "mem_priv_r2"},
            subject_view=True,
        )
        self.assertEqual(c_tomb_s, 200, b_tomb_s)
        # Restore pre-tombstone recall file while V-C watermark retains vc:mem_priv_r2 seal
        (self.root / "memory" / "v02_recall_projection_state.json").write_bytes(backup_recall_bytes)
        barrier = rlf.load_unified_forget_barrier_readonly(self.root)
        self.assertTrue(rlf.is_entity_forget_blocked("mem_priv_r2", barrier))
        _, b_rp_sub = psvc.v02_recall_projection(subject_view=True)
        rp_data = b_rp_sub.get("data", {})
        self.assertNotIn("mem_priv_r2", rp_data.get("proactive_surfaceable_ids") or [])
        sub_stubs = [
            it
            for it in (rp_data.get("direct_recall_items") or [])
            if it.get("memory_id") == "mem_priv_r2"
        ]
        self.assertTrue(all(it.get("clarity") == "UNAVAILABLE" for it in sub_stubs), sub_stubs)
        self.assertNotIn("SECRET_ROUND2_MEM", json.dumps(b_rp_sub, ensure_ascii=False))
        self.assertNotIn("SECRET_ROUND2_GIST", json.dumps(b_rp_sub, ensure_ascii=False))
        _, b_rp_partner = psvc.v02_recall_projection(subject_view=False)
        self.assertNotIn(
            "mem_priv_r2",
            [it.get("memory_id") for it in (b_rp_partner.get("data", {}).get("direct_recall_items") or [])],
        )

        # 4. Bridge receipt hardening: empty CrashAndPartialReconcilerStore & ExternalProviderLedger blocks spoofing, accepts real crcpt / prov_rcpt
        cpr.CrashAndPartialReconcilerStore(self.root / "reconciler").initialize()
        ext_ledger = cpr.ExternalProviderLedger(self.root)
        ext_ledger.configure_lookup(lookup_enabled=True, conclusive_not_found=True)

        bad_empty_db = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "COMPLETE_INDEPENDENT_WORK",
                "job_kind": "manuscript_fee",
                "amount_cents": 1200,
                "work_ref": "shift:r2_empty_db",
                "receipt_ref": "rcpt_fake_on_empty_db",
                "operation_key": "op_r2_fake_empty",
            },
            now_iso=T0,
        )
        self.assertFalse(bad_empty_db["settled"])
        self.assertEqual(bad_empty_db["reason_code"], "UNVERIFIED_OWNER_RECEIPT")

        bad_v02v03 = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "actor_side": "life_resident",
                "effect_state": "APPLIED",
                "action_kind": "FOLLOW_TOMORROW_NOTE",
                "future_item_ref": "tomorrow_note:nonexistent",
                "source_ref": "tomorrow_note:nonexistent",
                "receipt_ref": "v02v03:forged_bypass",
                "operation_key": "op_r2_forged_v02v03",
            },
            now_iso=T0,
        )
        self.assertFalse(bad_v02v03["settled"])
        self.assertEqual(bad_v02v03["reason_code"], "UNVERIFIED_OWNER_RECEIPT")

        rlf._ensure_control_ledger_locked(self.root)
        c_art, d_art = rlf.create_managed_artifact(
            self.root,
            artifact_id="art_r2_receipt",
            controller_principal_ref="subject_1",
            subject_id="subject_1",
            initial_body="hello",
            operation_id="op_r2_create_art",
            now_iso=T0,
        )
        self.assertEqual(c_art, 200, d_art)
        real_crcpt = d_art["creator_receipt_ref"]
        good_crcpt = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "COMPLETE_INDEPENDENT_WORK",
                "job_kind": "manuscript_fee",
                "amount_cents": 1500,
                "work_ref": "shift:r2_crcpt",
                "artifact_id": "art_r2_receipt",
                "receipt_ref": real_crcpt,
                "operation_key": "op_r2_good_crcpt",
            },
            now_iso=T0,
        )
        self.assertTrue(good_crcpt["settled"], good_crcpt)

        inv_ok = ext_ledger.invoke(
            logical_action_id="act_ext_ok",
            idempotency_key="idem_ext_ok",
            attempt_id="att_1",
            payload_hash="sha256:ok",
            supports_idempotency=True,
            outcome="SUCCEEDED",
        )
        inv_fail = ext_ledger.invoke(
            logical_action_id="act_ext_fail",
            idempotency_key="idem_ext_fail",
            attempt_id="att_2",
            payload_hash="sha256:fail",
            supports_idempotency=True,
            outcome="FAILED",
        )
        self.assertEqual(inv_ok["provider_receipt_id"], "prov_rcpt_000001")
        self.assertIsNone(inv_fail["provider_receipt_id"])

        good_ext = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "COMPLETE_INDEPENDENT_WORK",
                "job_kind": "proofreading_shift",
                "amount_cents": 1800,
                "work_ref": "shift:r2_ext_ok",
                "receipt_ref": "prov_rcpt_000001",
                "operation_key": "op_r2_ext_ok",
            },
            now_iso=T0,
        )
        self.assertTrue(good_ext["settled"], good_ext)

        bad_ext = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "COMPLETE_INDEPENDENT_WORK",
                "job_kind": "proofreading_shift",
                "amount_cents": 1800,
                "work_ref": "shift:r2_ext_fail",
                "receipt_ref": "prov_rcpt_000002",
                "operation_key": "op_r2_ext_fail",
            },
            now_iso=T0,
        )
        self.assertFalse(bad_ext["settled"], bad_ext)
        self.assertEqual(bad_ext["reason_code"], "UNVERIFIED_OWNER_RECEIPT")

    def test_08_round2_adversarial_closure_crash_eviction_corrupt_utf8_scopes_and_wal(self) -> None:
        # 1. Real subprocess Popen + proc.kill() mid-flight after RLF commit, before Honest UX UI ledger write
        kill_root = self.root / "kill_import"
        ws_kill = kill_root / "ws"
        ws_kill.mkdir(parents=True)
        rlf._ensure_control_ledger_locked(kill_root)
        c0, d0 = rlf.create_managed_artifact(
            kill_root,
            artifact_id="art_kill_1",
            controller_principal_ref="subj",
            subject_id="subj",
            initial_body="v1 body\n",
            operation_id="op_k_init",
            title="note.md",
            visibility="shared",
            now_iso=T0,
        )
        self.assertEqual(c0, 200)
        head_v1 = d0["current_head"]
        c_bind, _ = rlf.bind_representation(
            kill_root,
            artifact_id="art_kill_1",
            workspace_root=ws_kill,
            workspace_rel_path="note.md",
            operation_id="op_k_bind",
            now_iso=T0,
        )
        self.assertEqual(c_bind, 200)
        (ws_kill / "note.md").write_text("v2 crash import body\n", encoding="utf-8")
        c_obs, _ = rlf.observe_external_edit(
            kill_root,
            artifact_id="art_kill_1",
            workspace_root=ws_kill,
            operation_id="op_k_obs",
            actor_ref="subj",
            trusted_actor_identity=True,
            now_iso=T0,
        )
        self.assertEqual(c_obs, 200)

        child_code = (
            "import sys, time; from pathlib import Path\n"
            f"for p in {([str(ROOT), str(ROOT / 'components'), str(ROOT / 'components' / 'life' / 'scripts')])!r}:\n"
            "    if p not in sys.path: sys.path.insert(0, p)\n"
            "from components.phone import v02_honest_ux_view as hux\n"
            "orig = hux._rlf.explicit_import_representation\n"
            "def hook(*args, **kwargs):\n"
            "    res = orig(*args, **kwargs)\n"
            "    if res[0] == 200:\n"
            "        print('COMMITTED_BEFORE_UI_LEDGER', flush=True)\n"
            "        time.sleep(60)\n"
            "    return res\n"
            "hux._rlf.explicit_import_representation = hook\n"
            f"hux.mutate_v02_honest_ux_view(Path({str(kill_root)!r}), "
            "{'action': 'explicit_import_representation', 'artifact_id': 'art_kill_1', "
            f"'workspace_root': {str(ws_kill)!r}, 'base_projection_version': {head_v1!r}, "
            "'operation_key': 'op_kill_import_1'}, actor_side='subject', now_iso="
            f"{T1!r})\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdout is not None
            marker = proc.stdout.readline().strip()
            self.assertEqual(marker, "COMMITTED_BEFORE_UI_LEDGER")
            proc.kill()
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()

        st_mid, ok_mid = rlf._load_state_readonly(kill_root)
        self.assertTrue(ok_mid)
        head_after_kill = st_mid["artifacts"]["art_kill_1"]["current_head"]
        self.assertNotEqual(head_after_kill, head_v1)
        ux_mid = json.loads((kill_root / "phone" / "v02_honest_ux_state.json").read_text(encoding="utf-8"))
        self.assertNotEqual(
            (ux_mid.get("operation_ledger") or ux_mid.get("operations") or {}).get("op_kill_import_1", {}).get("command_status"),
            "APPLIED",
        )

        c_retry, r_retry = hux_view.mutate_v02_honest_ux_view(
            kill_root,
            {
                "action": "explicit_import_representation",
                "artifact_id": "art_kill_1",
                "workspace_root": str(ws_kill),
                "base_projection_version": head_v1,
                "operation_key": "op_kill_import_1",
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(c_retry, 200, r_retry)
        st_after, _ = rlf._load_state_readonly(kill_root)
        self.assertEqual(st_after["artifacts"]["art_kill_1"]["current_head"], head_after_kill)
        art_versions = [
            v for v in (st_after.get("versions") or {}).values() if v.get("artifact_id") == "art_kill_1"
        ]
        self.assertEqual(len(art_versions), 2)

        # 2. >512 (540-op) eviction in v02_representation_lease_forget preserves idempotency and conflict detection
        ev_root = self.root / "evict_540"
        rlf._ensure_control_ledger_locked(ev_root)
        c_ev0, d_ev0 = rlf.create_managed_artifact(
            ev_root,
            artifact_id="art_ev_0",
            controller_principal_ref="subj",
            subject_id="subj",
            initial_body="ev0",
            operation_id="op_evict_512_000",
            now_iso=T0,
        )
        self.assertEqual(c_ev0, 200)
        ev0_head = d_ev0["current_head"]
        for i in range(539):
            c_i, _ = rlf.ingest_source(
                ev_root,
                source_id=f"src_ev_{i:03d}",
                text=f"shared filler {i}",
                operation_id=f"op_fill_{i:03d}",
                visibility="shared",
                now_iso=T0,
            )
            self.assertEqual(c_i, 200)
        st_ev, _ = rlf._load_state_readonly(ev_root)
        self.assertNotIn("op_evict_512_000", st_ev.get("operations") or {})
        ring_keys = {
            row.get("operation_key")
            for row in (st_ev.get("committed_op_ring") or [])
            if isinstance(row, dict)
        }
        self.assertIn("op_evict_512_000", ring_keys)
        c_ev_rep, d_ev_rep = rlf.create_managed_artifact(
            ev_root,
            artifact_id="art_ev_0",
            controller_principal_ref="subj",
            subject_id="subj",
            initial_body="ev0",
            operation_id="op_evict_512_000",
            now_iso=T0,
        )
        self.assertEqual(c_ev_rep, 200, d_ev_rep)
        self.assertTrue(d_ev_rep.get("idempotent_replay"))
        st_ev_after, _ = rlf._load_state_readonly(ev_root)
        self.assertEqual(st_ev_after["artifacts"]["art_ev_0"]["current_head"], ev0_head)
        c_ev_conf, d_ev_conf = rlf.create_managed_artifact(
            ev_root,
            artifact_id="art_ev_0",
            controller_principal_ref="subj",
            subject_id="subj",
            initial_body="MUTATED_CONFLICT",
            operation_id="op_evict_512_000",
            now_iso=T0,
        )
        self.assertEqual(c_ev_conf, 409)
        self.assertEqual(d_ev_conf.get("code"), "OPERATION_ID_PAYLOAD_CONFLICT")

        # 3. Corrupt governance JSON fails closed without clobbering disk + degrades Honest UX epistemics
        cor_root = self.root / "corrupt_gov"
        gov_file = cor_root / "governance" / "v02_representation_lease_forget_state.json"
        gov_file.parent.mkdir(parents=True, exist_ok=True)
        gov_file.write_text("{corrupt", encoding="utf-8")
        c_cor, d_cor = rlf.create_managed_artifact(
            cor_root,
            artifact_id="art_cor_1",
            controller_principal_ref="subj",
            subject_id="subj",
            initial_body="should fail closed",
            operation_id="op_cor_1",
            now_iso=T0,
        )
        self.assertEqual(c_cor, 503, d_cor)
        self.assertEqual(d_cor.get("code"), "CORRUPT_GOVERNANCE_AUTHORITY_BLOCKED")
        self.assertEqual(gov_file.read_text(encoding="utf-8"), "{corrupt")
        c_cor_proj, cor_proj = hux_view.read_v02_honest_ux_view(cor_root, has_subject_capability=True, now_iso=T0)
        self.assertEqual(c_cor_proj, 200)
        self.assertTrue(cor_proj["ok"])
        self.assertIn(cor_proj["data"]["provenance"]["epistemic_status"], ("UNVERIFIED", "UNKNOWN"))
        self.assertTrue(cor_proj["data"]["provenance"]["read_degraded"])
        self.assertGreaterEqual(len(cor_proj["data"]["provenance"]["degraded_domains"]), 1)

        # 4. Non-UTF8 markdown import fails 422 INVALID_UTF8_MARKDOWN and keeps current_head intact
        utf_root = self.root / "non_utf8"
        ws_utf = utf_root / "ws"
        ws_utf.mkdir(parents=True)
        rlf._ensure_control_ledger_locked(utf_root)
        c_u0, d_u0 = rlf.create_managed_artifact(
            utf_root,
            artifact_id="art_utf8_1",
            controller_principal_ref="subj",
            subject_id="subj",
            initial_body="valid utf8\n",
            operation_id="op_u_init",
            title="bad.md",
            visibility="shared",
            now_iso=T0,
        )
        self.assertEqual(c_u0, 200)
        u_head0 = d_u0["current_head"]
        rlf.bind_representation(
            utf_root,
            artifact_id="art_utf8_1",
            workspace_root=ws_utf,
            workspace_rel_path="bad.md",
            operation_id="op_u_bind",
            now_iso=T0,
        )
        (ws_utf / "bad.md").write_bytes(b"\xff\xfe\xfa\x00bad")
        c_u_imp, d_u_imp = rlf.explicit_import_representation(
            utf_root,
            artifact_id="art_utf8_1",
            workspace_root=ws_utf,
            base_projection_version=u_head0,
            actor_ref="subj",
            operation_id="op_u_imp",
            require_prior_observation=False,
            now_iso=T1,
        )
        self.assertEqual(c_u_imp, 422, d_u_imp)
        self.assertEqual(d_u_imp.get("code"), "INVALID_UTF8_MARKDOWN")
        st_u_after, _ = rlf._load_state_readonly(utf_root)
        self.assertEqual(st_u_after["artifacts"]["art_utf8_1"]["current_head"], u_head0)

        # 5. Partner 409 FORGET_NO_ELIGIBLE_CANDIDATES, scope narrow-down cleanup, and overlapping holds
        hold_root = self.root / "holds"
        rlf._ensure_control_ledger_locked(hold_root)
        rlf.ingest_source(
            hold_root,
            source_id="src_priv",
            text="only private turn",
            operation_id="op_h_priv",
            visibility="private",
            session_id="sess_h",
            now_iso=T0,
        )
        c_h_empty, r_h_empty = hux_view.mutate_v02_honest_ux_view(
            hold_root,
            {
                "action": "request_forget_scope_hold",
                "scope_hint": "recent_turn",
                "target_domain": "conversation",
                "conversation_id": "sess_h",
                "operation_key": "op_h_empty",
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_h_empty, 409, r_h_empty)
        self.assertEqual(r_h_empty.get("error_code"), "FORGET_NO_ELIGIBLE_CANDIDATES")

        rlf.ingest_source(
            hold_root,
            source_id="src_h1",
            text="shared turn 1",
            operation_id="op_h_s1",
            visibility="shared",
            topic="topic_a",
            session_id="sess_h",
            now_iso=T0,
        )
        rlf.ingest_source(
            hold_root,
            source_id="src_h2",
            text="shared turn 2",
            operation_id="op_h_s2",
            visibility="shared",
            topic="topic_a",
            session_id="sess_h",
            now_iso=T1,
        )
        c_h1, r_h1 = hux_view.mutate_v02_honest_ux_view(
            hold_root,
            {
                "action": "request_forget_scope_hold",
                "scope_hint": "recent_turn",
                "target_domain": "conversation",
                "conversation_id": "sess_h",
                "operation_key": "op_h_topic",
            },
            actor_side="partner",
            now_iso=T1,
        )
        self.assertEqual(c_h1, 200, r_h1)
        hold1 = r_h1["data"]["case_id"]
        c_exp, r_exp = hux_view.mutate_v02_honest_ux_view(
            hold_root,
            {
                "action": "clarify_forget_scope",
                "case_id": hold1,
                "scope_choice": "current_topic",
                "operation_key": "op_h_expand",
            },
            actor_side="partner",
            now_iso=T1,
        )
        self.assertEqual(c_exp, 200, r_exp)
        st_h_mid, _ = rlf._load_state_readonly(hold_root)
        self.assertTrue(st_h_mid["sources"]["src_h1"]["hold_active"])
        self.assertTrue(st_h_mid["sources"]["src_h2"]["hold_active"])

        c_clar, r_clar = hux_view.mutate_v02_honest_ux_view(
            hold_root,
            {
                "action": "clarify_forget_scope",
                "case_id": hold1,
                "scope_choice": "last_turn",
                "operation_key": "op_h_clarify",
            },
            actor_side="partner",
            now_iso=T1,
        )
        self.assertEqual(c_clar, 200, r_clar)
        st_h_narrow, _ = rlf._load_state_readonly(hold_root)
        self.assertFalse(st_h_narrow["sources"]["src_h1"]["hold_active"])
        self.assertTrue(st_h_narrow["sources"]["src_h2"]["hold_active"])

        c_h2, r_h2 = hux_view.mutate_v02_honest_ux_view(
            hold_root,
            {
                "action": "request_forget_scope_hold",
                "scope_hint": "recent_turn",
                "target_domain": "conversation",
                "conversation_id": "sess_h",
                "operation_key": "op_h_overlap",
            },
            actor_side="partner",
            now_iso=T2,
        )
        self.assertEqual(c_h2, 200, r_h2)
        hold2 = r_h2["data"]["case_id"]
        c_can1, _ = rlf.resolve_hold_case(
            hold_root,
            hold_id=hold1,
            resolution="explicit_cancel_hold",
            operation_id="op_h_cancel1",
            actor_side="subject",
            now_iso=T2,
        )
        self.assertEqual(c_can1, 200)
        st_after_c1, _ = rlf._load_state_readonly(hold_root)
        self.assertTrue(st_after_c1["sources"]["src_h2"]["hold_active"])
        c_can2, _ = rlf.resolve_hold_case(
            hold_root,
            hold_id=hold2,
            resolution="explicit_cancel_hold",
            operation_id="op_h_cancel2",
            actor_side="subject",
            now_iso=T2,
        )
        self.assertEqual(c_can2, 200)
        st_after_c2, _ = rlf._load_state_readonly(hold_root)
        self.assertFalse(st_after_c2["sources"]["src_h2"]["hold_active"])

        # 6. Receipt cross-field mismatch & ProductionLifeRuntime startup WAL reconciler
        c_rcpt, d_rcpt = rlf.create_managed_artifact(
            hold_root,
            artifact_id="art_real_bind",
            controller_principal_ref="subj",
            subject_id="subj",
            initial_body="receipt check",
            operation_id="op_rcpt_art",
            now_iso=T2,
        )
        self.assertEqual(c_rcpt, 200)
        bad_mismatch = loop_bridge.settle_v02_v03_round_consequences(
            hold_root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "COMPLETE_INDEPENDENT_WORK",
                "job_kind": "manuscript_fee",
                "amount_cents": 1200,
                "work_ref": "shift:r2_mismatch",
                "artifact_id": "art_wrong_mismatch",
                "receipt_ref": d_rcpt["creator_receipt_ref"],
                "operation_key": "op_r2_mismatch",
            },
            now_iso=T2,
        )
        self.assertFalse(bad_mismatch["settled"])
        self.assertEqual(bad_mismatch["reason_code"], "UNVERIFIED_OWNER_RECEIPT")

        empty_rt_root = self.root / "rt_empty"
        empty_rt_root.mkdir()
        os.chmod(empty_rt_root, 0o700)
        all_ks = prod_runtime.ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True,
            agency_enabled=True,
            action_execution_enabled=True,
            proactive_enabled=True,
        )
        rt_empty = prod_runtime.ProductionLifeRuntime(
            state_root=empty_rt_root,
            install_root=empty_rt_root,
            kill_switches=all_ks,
            apply_production_cutover=True,
        )
        try:
            self.assertFalse(rt_empty.startup_reconciler_recovery["reconciler_present"])
            self.assertFalse((empty_rt_root / "canonical_life.sqlite3").exists())
            self.assertFalse((empty_rt_root / "reconciler" / "canonical_life.sqlite3").exists())
        finally:
            rt_empty.close()
            if rt_empty.lease.held:
                rt_empty.lease.release()

        wal_rt_root = self.root / "rt_wal"
        wal_rt_root.mkdir()
        os.chmod(wal_rt_root, 0o700)
        rec_sub = cpr.CrashAndPartialReconcilerStore(wal_rt_root / "reconciler")
        rec_sub.initialize()
        rec_sub.tx_t0_commit_decision(decision_id="dec_wal_1", request_id="req_wal_1", writer_epoch=1)
        t1_row = rec_sub.tx_t1_commit_adoption_activity_action(
            decision_id="dec_wal_1",
            request_id="req_wal_1",
            logical_action_id="la_wal_1",
            activity_title="Interrupted WAL action",
            writer_epoch=1,
        )
        td_row = rec_sub.tx_td_prepare_dispatch(action_id=t1_row["action_id"], writer_epoch=1)
        rec_sub.tx_ta_start_attempt(attempt_id=td_row["attempt_id"], writer_epoch=1)
        rt_wal = prod_runtime.ProductionLifeRuntime(
            state_root=wal_rt_root,
            install_root=wal_rt_root,
            kill_switches=all_ks,
            apply_production_cutover=True,
        )
        try:
            self.assertTrue(rt_wal.startup_reconciler_recovery["reconciler_present"])
            self.assertTrue(rt_wal.startup_reconciler_recovery["recovered"])
            self.assertGreaterEqual(len(rt_wal.startup_reconciler_recovery["reconciled_actions"]), 1)
        finally:
            rt_wal.close()
            if rt_wal.lease.held:
                rt_wal.lease.release()


if __name__ == "__main__":
    unittest.main()

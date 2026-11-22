#!/usr/bin/env python3
"""v0.8 Wave 7: 0.2 / 0.3 设计债四项最小实装验收（0 product-model calls）。

覆盖 ``docs/engineering/20261114-design-debt-registration.md`` 中登记的四项：

* 4.A P12 自传体叙事只读投影（``project_p12_autobiographical_narrative``）：
  真实实例数据 -> 章节/转折/母题/溯源 ref；空目录零捏造；伴侣视角严格脱敏；
  零写盘；``llm_calls == 0``；挂到 recall 与 agency 两个读面。
* 4.B 有界 Inquiry 生命周期（``v02_agency_depth_engine`` + daily-loop bridge）：
  ``OPEN -> INVESTIGATING -> CONCLUDED / STOPPED``；``MAX_OPEN_INQUIRIES=5`` 与
  ``MAX_EVIDENCE_NOTES_PER_INQUIRY=8`` 上限拒止；同 ``operation_key`` 幂等；
  经 ``collect_*`` + ``*_round_event_for_selected_candidate`` + ``settle_*`` 与
  ``run_*_tick`` 推进真实落盘。
* 4.C Ch.16 22 类年度衰减监测矩阵：22 类不重不漏、四域归类、衰减/重锚定字段合法、
  零写盘、视图与 HTTP 映射兼容。
* 4.D Ch.07 §4 ``SlotQuota`` & ``DailyEnvelope``：时段配额、日分类包络、日硬上限、
  跨天隔离、``enforce_slot_envelope=False`` 完全向后兼容。

Run:  python3 -m unittest tests.test_v08_v02v03_design_debt_closure
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
import v02_common_ground as common_ground  # noqa: E402
from components.life.scripts import life_request_budget as lrb  # noqa: E402
from components.life.scripts import v02_agency_depth_engine as ad_engine  # noqa: E402
from components.life.scripts import v02_recall_projection as recall  # noqa: E402
from components.life.scripts import v02_runtime_governance_migration as rm_engine  # noqa: E402
from components.phone import v02_agency_depth_view as ad_view  # noqa: E402
from components.phone import v02_runtime_migration_view as rm_view  # noqa: E402

T0 = "2026-11-15T09:00:00+08:00"
T1 = "2026-11-15T18:00:00+08:00"


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        st = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-design-debt-")
        self.root = Path(self._tmp.name) / "instance"
        (self.root / "phone").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()


# ===========================================================================
# 4.A P12 autobiographical narrative
# ===========================================================================


class P12NarrativeTest(_Base):
    def _seed_recall_state(self, memories: dict) -> None:
        path = self.root / "memory" / "v02_recall_projection_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": recall.SCHEMA_VERSION,
                    "memories": memories,
                    "tombstones": {},
                    "updated_at": T0,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _seed_rich_instance(self) -> None:
        self._seed_recall_state({
            "mem_1": {
                "memory_id": "mem_1",
                "topic": "窗边薄荷",
                "gist_summary": "秋天给窗边薄荷换了盆。",
                "occurred_at": "2026-09-12T10:00:00+08:00",
                "is_relationship_milestone": True,
                "shared": True,
            },
            "mem_2": {
                "memory_id": "mem_2",
                "topic": "窗边薄荷",
                "gist_summary": "入冬前把薄荷挪回屋里。",
                "occurred_at": "2026-11-02T10:00:00+08:00",
                "is_relationship_milestone": False,
                "shared": True,
            },
            "mem_private": {
                "memory_id": "mem_private",
                "topic": "PRIVATE_MEMORY_SECRET",
                "gist_summary": "PRIVATE_MEMORY_DETAIL",
                "occurred_at": "2026-10-01T10:00:00+08:00",
                "shared": False,
            },
        })
        common_ground.record_ground_item(
            self.root,
            item_id="cg_shared",
            topic="共同的雨天读书记忆",
            summary="一起在窗边听雨翻旧书。",
            ground_status=common_ground.SHARED_WITH_PARTNER,
            delivery_state=common_ground.DELIVERY_AGREED,
            now_iso=T0,
        )
        common_ground.record_ground_item(
            self.root,
            item_id="cg_private",
            topic="PRIVATE_GROUND_SECRET",
            summary="PRIVATE_GROUND_DETAIL",
            ground_status=common_ground.PRIVATE_UNSHARED,
            now_iso=T0,
        )
        ad_view.mutate_v02_agency_depth_view(
            self.root,
            {
                "action": "open_bounded_inquiry",
                "question": "窗边薄荷越冬要不要剪枝？",
                "topic_tag": "窗边薄荷",
                "shared": True,
                "operation_key": "p12-inq-open",
            },
            actor_side="subject",
            now_iso=T0,
        )
        _proj, _ = ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "conclude_or_stop_inquiry",
                "inquiry_id": "inq_0001",
                "outcome": "CONCLUDED",
                "conclusion": "留老茎剪徒长枝更稳。",
                "operation_key": "p12-inq-conclude",
            },
            actor_side="subject",
            now_iso=T1,
        )

    def test_01_empty_instance_is_honest_and_zero_write(self) -> None:
        empty_root = Path(self._tmp.name) / "empty_p12"
        before = _snapshot(empty_root)
        narrative = recall.project_p12_autobiographical_narrative(
            empty_root, has_subject_capability=True, now_iso=T0
        )
        self.assertFalse(narrative["state_exists"])
        self.assertEqual(narrative["state_status"], recall.P12_STATUS_NO_STATE)
        self.assertEqual(narrative["chapters"], [])
        self.assertEqual(narrative["turning_points"], [])
        self.assertEqual(narrative["recurring_motifs"], [])
        self.assertEqual(narrative["attested_source_refs"], [])
        self.assertEqual(narrative["llm_calls"], 0)
        self.assertEqual(_snapshot(empty_root), before)

    def test_02_real_instance_chapters_turning_points_motifs_and_attested_refs(self) -> None:
        self._seed_rich_instance()
        narrative = recall.project_p12_autobiographical_narrative(
            self.root, has_subject_capability=True, now_iso=T1
        )
        self.assertTrue(narrative["state_exists"])
        self.assertEqual(narrative["state_status"], recall.P12_STATUS_ATTESTED)
        self.assertEqual(narrative["perspective"], "subject")
        self.assertGreaterEqual(len(narrative["chapters"]), 2)
        refs = set(narrative["attested_source_refs"])
        self.assertIn("recall:mem_1", refs)
        self.assertIn("common_ground:cg_shared", refs)
        self.assertIn("agency_depth:inq_0001", refs)
        self.assertTrue(narrative["turning_points"])
        motifs = {row["motif"]: row["occurrences"] for row in narrative["recurring_motifs"]}
        self.assertGreaterEqual(motifs.get("窗边薄荷", 0), 2)
        self.assertEqual(narrative["llm_calls"], 0)
        self.assertTrue(narrative["zero_write"])

    def test_03_partner_view_strictly_hides_private_material(self) -> None:
        self._seed_rich_instance()
        partner = recall.project_p12_autobiographical_narrative(
            self.root, has_subject_capability=False, now_iso=T1
        )
        subject = recall.project_p12_autobiographical_narrative(
            self.root, has_subject_capability=True, now_iso=T1
        )
        partner_blob = json.dumps(partner, ensure_ascii=False)
        for secret in (
            "PRIVATE_MEMORY_SECRET",
            "PRIVATE_MEMORY_DETAIL",
            "PRIVATE_GROUND_SECRET",
            "PRIVATE_GROUND_DETAIL",
        ):
            self.assertNotIn(secret, partner_blob)
        self.assertNotIn("recall:mem_private", partner["attested_source_refs"])
        self.assertIn("recall:mem_private", subject["attested_source_refs"])
        self.assertEqual(partner["perspective"], "partner")

    def test_04_recall_and_agency_read_surfaces_mount_the_narrative(self) -> None:
        self._seed_rich_instance()
        recall_view = recall.read_v02_recall_projection_view(
            self.root, now_iso=T1, has_subject_capability=True
        )
        self.assertIn("p12_autobiographical_narrative", recall_view)

        code, doc = ad_view.read_v02_agency_depth_view(
            self.root, has_subject_capability=False, now_iso=T1
        )
        self.assertEqual(code, 200)
        self.assertIn("p12_autobiographical_narrative", doc)
        self.assertIn("p12_autobiographical_narrative", doc["data"])
        self.assertEqual(doc["p12_autobiographical_narrative"], doc["data"]["p12_autobiographical_narrative"])
        self.assertEqual(doc["llm_calls"], 0)

    def test_05_narrative_is_zero_write_on_a_materialized_instance(self) -> None:
        self._seed_rich_instance()
        before = _snapshot(self.root)
        recall.project_p12_autobiographical_narrative(self.root, now_iso=T1)
        recall.read_v02_recall_projection_view(self.root, now_iso=T1)
        ad_view.read_v02_agency_depth_view(self.root, now_iso=T1)
        self.assertEqual(_snapshot(self.root), before)


# ===========================================================================
# 4.B Bounded inquiry lifecycle
# ===========================================================================


class BoundedInquiryStateMachineTest(_Base):
    def _open(self, *, question: str, shared: bool = True, key: str | None = None) -> tuple[int, dict]:
        payload = {"action": "open_bounded_inquiry", "question": question, "shared": shared}
        if key:
            payload["operation_key"] = key
        return ad_view.mutate_v02_agency_depth_view(
            self.root, payload, actor_side="subject", now_iso=T0
        )

    def test_01_open_evidence_conclude_happy_path(self) -> None:
        code, doc = self._open(question="旧木窗隔音怎么做？", key="inq-open-1")
        self.assertEqual(code, 200)
        inq = doc["inquiries"][0]
        self.assertEqual(inq["status"], "OPEN")
        self.assertEqual(inq["evidence_notes"], [])
        iid = inq["inquiry_id"]

        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {
                "action": "record_inquiry_evidence",
                "inquiry_id": iid,
                "note": "在书店翻到硅胶密封条的做法",
                "source_ref": "book:window_seal",
                "operation_key": "inq-ev-1",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200)
        self.assertEqual(doc["inquiries"][0]["status"], "INVESTIGATING")
        self.assertEqual(len(doc["inquiries"][0]["evidence_notes"]), 1)

        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {
                "action": "conclude_or_stop_inquiry",
                "inquiry_id": iid,
                "outcome": "CONCLUDED",
                "conclusion": "先贴门缝密封条，别换整扇窗。",
                "operation_key": "inq-done-1",
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(code, 200)
        done = doc["inquiries"][0]
        self.assertEqual(done["status"], "CONCLUDED")
        self.assertEqual(done["conclusion"], "先贴门缝密封条，别换整扇窗。")
        self.assertIsNotNone(done["settled_at"])

    def test_02_open_operation_key_replay_is_idempotent(self) -> None:
        self._open(question="一次幂等探究", key="inq-idem")
        code, doc = self._open(question="一次幂等探究", key="inq-idem")
        self.assertEqual(code, 200)
        self.assertEqual(doc["command_status"], "DUPLICATE")
        self.assertEqual(len(doc["inquiries"]), 1)

    def test_03_stop_path_and_terminal_rejects_more_evidence(self) -> None:
        _, doc = self._open(question="要不要养第二盆植物？")
        iid = doc["inquiries"][0]["inquiry_id"]
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {
                "action": "conclude_or_stop_inquiry",
                "inquiry_id": iid,
                "outcome": "STOPPED",
                "stop_reason": "空间不够，先不养。",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200)
        self.assertEqual(doc["inquiries"][0]["status"], "STOPPED")
        self.assertEqual(doc["inquiries"][0]["stop_reason"], "空间不够，先不养。")

        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {"action": "record_inquiry_evidence", "inquiry_id": iid, "note": "还想补一条"},
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(code, 409)
        self.assertEqual(doc["reason_code"], "INQUIRY_ALREADY_SETTLED")

    def test_04_open_capacity_is_five_active(self) -> None:
        for idx in range(ad_engine.MAX_OPEN_INQUIRIES):
            code, _ = self._open(question=f"问题 {idx}")
            self.assertEqual(code, 200)
        code, doc = self._open(question="第六个探究")
        self.assertEqual(code, 409)
        self.assertEqual(doc["reason_code"], "INQUIRY_CAPACITY_EXCEEDED")

    def test_05_evidence_notes_cap_is_eight(self) -> None:
        _, doc = self._open(question="证据上限探究")
        iid = doc["inquiries"][0]["inquiry_id"]
        for idx in range(ad_engine.MAX_EVIDENCE_NOTES_PER_INQUIRY):
            code, _ = ad_view.mutate_v02_agency_depth_view(
                self.root,
                {
                    "action": "record_inquiry_evidence",
                    "inquiry_id": iid,
                    "note": f"第 {idx} 条真实观察",
                    "operation_key": f"ev-{idx}",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(code, 200)
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {"action": "record_inquiry_evidence", "inquiry_id": iid, "note": "第九条"},
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(code, 409)
        self.assertEqual(doc["reason_code"], "INQUIRY_EVIDENCE_CAPACITY_EXCEEDED")

    def test_06_partner_cannot_mutate_and_private_inquiry_is_hidden(self) -> None:
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {"action": "open_bounded_inquiry", "question": "越权打开"},
            actor_side="partner",
        )
        self.assertEqual(code, 403)
        self.assertEqual(doc["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        self._open(question="SHARED_INQUIRY_TOPIC", shared=True)
        self._open(question="PRIVATE_INQUIRY_TOPIC", shared=False)
        p_code, p_doc = ad_view.read_v02_agency_depth_view(
            self.root, has_subject_capability=False, now_iso=T1
        )
        self.assertEqual(p_code, 200)
        blob = json.dumps(p_doc, ensure_ascii=False)
        self.assertIn("SHARED_INQUIRY_TOPIC", blob)
        self.assertNotIn("PRIVATE_INQUIRY_TOPIC", blob)
        self.assertEqual(len(p_doc["inquiries"]), 1)

    def test_07_bounded_inquiry_flows_through_the_daily_loop_bridge(self) -> None:
        _proj, status = ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "open_bounded_inquiry",
                "question": "窗台那盆薄荷要不要换更大的盆？",
                "topic_tag": "窗边薄荷",
                "shared": True,
                "operation_key": "bridge-open",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(status, "APPLIED")

        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        inq_ids = [
            desc.get("inquiry_id")
            for desc in collected["descriptors"].values()
            if desc.get("action_family") == "advance_bounded_inquiry"
        ]
        self.assertEqual(inq_ids, ["inq_0001"])

        source_ref = next(
            desc["source_ref"]
            for desc in collected["descriptors"].values()
            if desc.get("action_family") == "advance_bounded_inquiry"
        )
        event = bridge.v02_v03_round_event_for_selected_candidate(
            collected, source_ref, decision_id="inq-decision-1"
        )
        self.assertIsInstance(event, dict)
        self.assertEqual(event["action_kind"], "ADVANCE_BOUNDED_INQUIRY")
        self.assertEqual(event["inquiry_id"], "inq_0001")

        settled = bridge.settle_v02_v03_round_consequences(
            self.root, event, now_iso=T0
        )
        self.assertEqual(settled["status"], "APPLIED", settled)
        self.assertIn("v02_agency_depth.inquiries", settled["slices_updated"])
        self.assertEqual(settled["details"]["bounded_inquiry"]["evidence_count"], 1)

        replay = bridge.settle_v02_v03_round_consequences(
            self.root, event, now_iso=T1
        )
        self.assertTrue(replay.get("already_applied"))

        ledger_path = self.root / "phone" / bridge.CONSEQUENCE_LEDGER_FILENAME
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        self.assertIn(event["operation_key"], ledger["receipts"])

        projected = ad_engine.project_v02_agency_depth(
            self.root, has_subject_capability=True, now_iso=T1
        )
        inq = projected["inquiries"][0]
        self.assertEqual(inq["status"], "INVESTIGATING")
        self.assertEqual(len(inq["evidence_notes"]), 1)

    def test_08_daily_loop_selection_and_settlement_advance_the_inquiry(self) -> None:
        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "open_bounded_inquiry",
                "question": "雨水多的季节晾布巾要挂哪里？",
                "shared": True,
                "operation_key": "tick-open",
            },
            actor_side="subject",
            now_iso=T0,
        )
        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        # 只保留 inquiry 候选，验证确定性选择器会真正选中它（Cycle 复用同一选择结果）。
        inq_cids = [
            cid
            for cid, desc in collected["descriptors"].items()
            if desc.get("action_family") == "advance_bounded_inquiry"
        ]
        self.assertEqual(len(inq_cids), 1)
        selected = bridge.select_deterministic_loop_action(collected)
        self.assertEqual(selected.get("action_family"), "advance_bounded_inquiry")
        self.assertEqual(selected.get("selected_candidate_id"), inq_cids[0])

        source_ref = collected["descriptors"][inq_cids[0]]["source_ref"]
        round_event = bridge.v02_v03_round_event_for_selected_candidate(
            collected, source_ref, decision_id="tick-decision"
        )
        self.assertEqual(round_event["action_kind"], "ADVANCE_BOUNDED_INQUIRY")
        settled = bridge.settle_v02_v03_round_consequences(
            self.root, round_event, now_iso=T0
        )
        self.assertEqual(settled["status"], "APPLIED", settled)
        projected = ad_engine.project_v02_agency_depth(
            self.root, has_subject_capability=True, now_iso=T1
        )
        self.assertEqual(len(projected["inquiries"][0]["evidence_notes"]), 1)

    def test_09_bridge_refuses_when_evidence_capacity_is_full(self) -> None:
        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "open_bounded_inquiry",
                "question": "证据塞满后仍被选中会怎样？",
                "operation_key": "cap-open",
            },
            actor_side="subject",
            now_iso=T0,
        )
        for idx in range(ad_engine.MAX_EVIDENCE_NOTES_PER_INQUIRY):
            ad_engine.apply_agency_depth_mutation(
                self.root,
                {
                    "action": "record_inquiry_evidence",
                    "inquiry_id": "inq_0001",
                    "note": f"预置证据 {idx}",
                    "operation_key": f"cap-ev-{idx}",
                },
                actor_side="subject",
                now_iso=T0,
            )
        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        source_ref = next(
            desc["source_ref"]
            for desc in collected["descriptors"].values()
            if desc.get("action_family") == "advance_bounded_inquiry"
        )
        event = bridge.v02_v03_round_event_for_selected_candidate(
            collected, source_ref, decision_id="cap-decision"
        )
        refused = bridge.settle_v02_v03_round_consequences(
            self.root, event, now_iso=T1
        )
        self.assertEqual(refused["status"], "REFUSED")
        self.assertEqual(refused["reason_code"], "INQUIRY_EVIDENCE_CAPACITY_EXCEEDED")
        self.assertEqual(refused["optimistic_writes"], 0)


# ===========================================================================
# 4.C Ch.16 22-category annual decay matrix
# ===========================================================================


class AnnualDecayMatrixTest(_Base):
    def test_01_registry_is_exactly_22_unique_across_four_groups(self) -> None:
        rows = rm_engine.DECAY_MONITOR_CATEGORIES_22
        self.assertEqual(len(rows), 22)
        names = [row["category"] for row in rows]
        self.assertEqual(len(set(names)), 22)
        counts = {group: 0 for group in rm_engine.DECAY_MONITOR_GROUPS}
        for row in rows:
            self.assertIn(row["group"], counts)
            counts[row["group"]] += 1
        self.assertEqual(counts, {
            "memory_recall": 6,
            "relationship_common_ground": 5,
            "agency_habits_projects": 6,
            "world_rhythm_somatic": 5,
        })
        self.assertEqual(sum(counts.values()), 22)

    def test_02_empty_instance_is_absent_baseline_and_zero_write(self) -> None:
        empty_root = Path(self._tmp.name) / "empty_decay"
        before = _snapshot(empty_root)
        matrix = rm_engine.project_annual_decay_matrix_22(
            empty_root, horizon_days=365, now_iso=T0
        )
        self.assertEqual(matrix["total_categories"], 22)
        self.assertEqual(matrix["horizon_days"], 365)
        self.assertEqual(matrix["llm_calls"], 0)
        self.assertTrue(matrix["zero_write"])
        self.assertEqual(matrix["status_counts"]["ABSENT_BASELINE"], 22)
        self.assertEqual(_snapshot(empty_root), before)

    def test_03_present_sources_are_active_with_legal_decay_fields(self) -> None:
        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "open_bounded_inquiry",
                "question": "衰减矩阵探测问题",
                "operation_key": "decay-open",
            },
            actor_side="subject",
            now_iso=T0,
        )
        matrix = rm_engine.project_annual_decay_matrix_22(
            self.root, horizon_days=365, now_iso=T1
        )
        by_name = {row["category"]: row for row in matrix["categories"]}
        self.assertTrue(by_name["open_inquiries"]["present"])
        self.assertEqual(by_name["open_inquiries"]["entry_count"], 1)
        self.assertEqual(by_name["open_inquiries"]["status"], "ACTIVE")
        for row in matrix["categories"]:
            self.assertIn(row["status"], rm_engine.DECAY_MONITOR_STATUSES)
            self.assertGreater(row["half_life_days"], 0)
            self.assertGreaterEqual(row["floor_weight"], 0.0)
            self.assertLessEqual(row["floor_weight"], 1.0)
            lo, hi = row["drift_band"]
            self.assertLessEqual(lo, hi)
            self.assertTrue(row["re_anchor_policy"])

    def test_04_stale_baseline_needs_reanchor(self) -> None:
        ad_engine.apply_agency_depth_mutation(
            self.root,
            {
                "action": "open_bounded_inquiry",
                "question": "旧基线重锚探究",
                "operation_key": "reanchor-open",
            },
            actor_side="subject",
            now_iso=T0,
        )
        state_file = self.root / "state" / "v02_agency_depth_state.json"
        old = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()
        import os
        os.utime(state_file, (old, old))
        matrix = rm_engine.project_annual_decay_matrix_22(
            self.root, horizon_days=365, now_iso="2026-11-15T10:00:00+08:00"
        )
        by_name = {row["category"]: row for row in matrix["categories"]}
        self.assertEqual(by_name["open_inquiries"]["status"], "NEEDS_REANCHOR")
        self.assertGreater(by_name["open_inquiries"]["age_days"], 365)

    def test_05_runtime_migration_view_mounts_the_matrix(self) -> None:
        code, env = rm_view.read_v02_runtime_migration_view(
            self.root, has_subject_capability=False, now_iso=T0
        )
        self.assertEqual(code, 200)
        self.assertIn("annual_decay_matrix_22", env)
        self.assertIn("annual_decay_matrix_22", env["data"])
        self.assertEqual(env["annual_decay_matrix_22"]["total_categories"], 22)
        self.assertEqual(env["llm_calls"], 0)


# ===========================================================================
# 4.D SlotQuota & DailyEnvelope
# ===========================================================================


class SlotQuotaAndDailyEnvelopeTest(_Base):
    def _budget(self) -> lrb.LifeRequestBudget:
        return lrb.LifeRequestBudget(self.root, utc_day_max=24, run_window_max=1000)

    @staticmethod
    def _dt(day: int, hour: int = 10) -> datetime:
        return datetime(2026, 11, day, hour, 0, tzinfo=timezone.utc)

    def test_01_constants_match_ch07_section4(self) -> None:
        self.assertEqual(lrb.SLOT_QUOTAS, {
            "DAYTIME_ACTIVE": 2,
            "EVENING_WIND_DOWN": 1,
            "NIGHT_REST": 0,
        })
        self.assertEqual(lrb.DAILY_ENVELOPE_CAPS, {
            "daily_hard_cap": 8,
            "per_category_cap": {
                "INITIATION": 3,
                "CONTINUATION": 5,
                "REFLECTION": 2,
            },
        })

    def test_02_slot_quotas_refuse_independently(self) -> None:
        budget = self._budget()
        now = self._dt(15)
        for _ in range(2):
            budget.record_slot_and_daily_consumption(
                slot_kind="DAYTIME_ACTIVE", category="INITIATION", now_dt=now
            )
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.record_slot_and_daily_consumption(
                slot_kind="DAYTIME_ACTIVE", category="INITIATION", now_dt=now
            )
        self.assertEqual(caught.exception.reason_code, lrb.REASON_SLOT_QUOTA_EXCEEDED)

        budget.record_slot_and_daily_consumption(
            slot_kind="EVENING_WIND_DOWN", category="REFLECTION", now_dt=now
        )
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.record_slot_and_daily_consumption(
                slot_kind="EVENING_WIND_DOWN", category="REFLECTION", now_dt=now
            )
        self.assertEqual(caught.exception.reason_code, lrb.REASON_SLOT_QUOTA_EXCEEDED)

        night = budget.check_slot_and_daily_envelope(
            slot_kind="NIGHT_REST", category="REFLECTION", now_dt=now
        )
        self.assertFalse(night["allowed"])
        self.assertEqual(night["reason_code"], lrb.REASON_SLOT_QUOTA_EXCEEDED)

    def test_03_category_and_daily_caps_refuse_independently(self) -> None:
        budget = self._budget()
        now = self._dt(15)
        day = budget._resolve_day(now)
        ledger = {
            "schema_version": lrb.SCHEMA_VERSION,
            "kind": lrb.KIND,
            "active_day": day,
            "days": {day: 0},
            "windows": {},
            "run_id": None,
            "tokens": [],
            "day_tokens": {},
            "opened_at": "2026-11-15T00:00:00+00:00",
            "updated_at": "2026-11-15T00:00:00+00:00",
            "slot_envelope_days": {
                day: {
                    "slots": {"DAYTIME_ACTIVE": 0},
                    "categories": {"CONTINUATION": 5},
                    "total": 5,
                    "ops": {},
                }
            },
        }
        budget.path.parent.mkdir(parents=True, exist_ok=True)
        budget.path.write_text(json.dumps(ledger), encoding="utf-8")
        cat_verdict = budget.check_slot_and_daily_envelope(
            slot_kind="DAYTIME_ACTIVE", category="CONTINUATION", now_dt=now
        )
        self.assertFalse(cat_verdict["allowed"])
        self.assertEqual(cat_verdict["reason_code"], lrb.REASON_DAILY_ENVELOPE_EXCEEDED)

        ledger["slot_envelope_days"][day] = {
            "slots": {"DAYTIME_ACTIVE": 0},
            "categories": {"CONTINUATION": 0},
            "total": 8,
            "ops": {},
        }
        budget.path.write_text(json.dumps(ledger), encoding="utf-8")
        day_verdict = budget.check_slot_and_daily_envelope(
            slot_kind="DAYTIME_ACTIVE", category="CONTINUATION", now_dt=now
        )
        self.assertFalse(day_verdict["allowed"])
        self.assertEqual(day_verdict["reason_code"], lrb.REASON_DAILY_ENVELOPE_EXCEEDED)
        self.assertGreaterEqual(day_verdict["day_total"], 8)

    def test_04_operation_key_replay_is_idempotent(self) -> None:
        budget = self._budget()
        now = self._dt(15)
        first = budget.record_slot_and_daily_consumption(
            slot_kind="DAYTIME_ACTIVE",
            category="INITIATION",
            now_dt=now,
            operation_key="slot-op-1",
        )
        self.assertFalse(first["already_applied"])
        replay = budget.record_slot_and_daily_consumption(
            slot_kind="DAYTIME_ACTIVE",
            category="INITIATION",
            now_dt=now,
            operation_key="slot-op-1",
        )
        self.assertTrue(replay["already_applied"])
        snapshot = budget.slot_and_daily_snapshot(now_dt=now)
        self.assertEqual(snapshot["slots"]["DAYTIME_ACTIVE"]["used"], 1)
        self.assertEqual(snapshot["day_total"], 1)

    def test_05_cross_day_isolation(self) -> None:
        budget = self._budget()
        day1 = self._dt(15)
        day2 = self._dt(16)
        for _ in range(2):
            budget.record_slot_and_daily_consumption(
                slot_kind="DAYTIME_ACTIVE", category="INITIATION", now_dt=day1
            )
        snap_day1 = budget.slot_and_daily_snapshot(now_dt=day1)
        self.assertEqual(snap_day1["slots"]["DAYTIME_ACTIVE"]["used"], 2)
        with self.assertRaises(lrb.RequestBudgetError):
            budget.record_slot_and_daily_consumption(
                slot_kind="DAYTIME_ACTIVE", category="INITIATION", now_dt=day1
            )
        second_day = budget.record_slot_and_daily_consumption(
            slot_kind="DAYTIME_ACTIVE", category="INITIATION", now_dt=day2
        )
        self.assertEqual(second_day["slot_used"], 1)
        snap_day2 = budget.slot_and_daily_snapshot(now_dt=day2)
        self.assertEqual(snap_day2["slots"]["DAYTIME_ACTIVE"]["used"], 1)

    def test_06_disabled_envelope_is_byte_for_byte_backward_compatible(self) -> None:
        budget = self._budget()
        now = self._dt(15)
        self.assertEqual(budget.can_spend(now=now), budget.check(now=now))
        # Exhaust the slot, then a non-enforcing caller is still allowed by the old rules.
        for _ in range(2):
            budget.record_slot_and_daily_consumption(
                slot_kind="DAYTIME_ACTIVE", category="INITIATION", now_dt=now
            )
        legacy = budget.can_spend(now=now)
        self.assertTrue(legacy["allowed"])
        self.assertNotIn("slot_envelope_checked", legacy)
        self.assertTrue(budget.reserve(lrb.REQUEST_KIND_DECISION, now=now)["reservation"])

        enforcing = budget.can_spend(
            now=now,
            slot_kind="DAYTIME_ACTIVE",
            category="INITIATION",
            enforce_slot_envelope=True,
        )
        self.assertFalse(enforcing["allowed"])
        self.assertEqual(enforcing["reason_code"], lrb.REASON_SLOT_QUOTA_EXCEEDED)
        self.assertTrue(enforcing["slot_envelope_checked"])

    def test_07_old_ledger_without_slot_field_still_reads(self) -> None:
        budget = self._budget()
        now = self._dt(15)
        budget.reserve(lrb.REQUEST_KIND_DECISION, now=now)
        raw = json.loads(budget.path.read_text(encoding="utf-8"))
        self.assertNotIn("slot_envelope_days", raw, "reserve must not add the optional field")
        snapshot = budget.slot_and_daily_snapshot(now_dt=now)
        self.assertEqual(snapshot["state"], lrb.STATE_ABSENT)
        appended = budget.record_slot_and_daily_consumption(
            slot_kind="DAYTIME_ACTIVE", category="INITIATION", now_dt=now
        )
        self.assertEqual(appended["slot_used"], 1)
        self.assertEqual(budget.slot_and_daily_snapshot(now_dt=now)["state"], lrb.STATE_OK)

    def test_08_unknown_slot_or_category_has_an_explicit_reason_code(self) -> None:
        budget = self._budget()
        now = self._dt(15)
        verdict = budget.check_slot_and_daily_envelope(
            slot_kind="MIDNIGHT_SNACK", category="INITIATION", now_dt=now
        )
        self.assertFalse(verdict["allowed"])
        self.assertEqual(verdict["reason_code"], lrb.REASON_UNKNOWN_SLOT_OR_CATEGORY)
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.record_slot_and_daily_consumption(
                slot_kind="DAYTIME_ACTIVE", category="UNKNOWN", now_dt=now
            )
        self.assertEqual(caught.exception.reason_code, lrb.REASON_UNKNOWN_SLOT_OR_CATEGORY)


if __name__ == "__main__":
    unittest.main()

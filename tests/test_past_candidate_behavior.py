#!/usr/bin/env python3
"""过去到候选 → 下一步准备：候选自带的 owner 目标必须被执行消费。

选定的接缝（本文件只守这一处）
------------------------------
LR-2 canonical Activity 与 AG-0 的 ``CONTINUE_CURRENT`` 候选都带着 **owner 自己写下的**
``subject_refs`` / ``target_ref`` / ``checkpoint_ref``。正式 work 步骤此前只读候选的
``source_ref``（activity id），于是退回本轮绑定/历史选中的 resume point；当前台 Activity
指向另一件作品时，CONTINUE 会写到另一件作品。修复把这三个 owner refs 做成一个只读投影
（``past_candidate_bridge``），让正式 step / adoption / round report 都以候选自带的 owner
目标为准；没有作品 ref 时保持原 fallback，owner 仍决定它是否可续，读不到/已归档一律具名拒绝。

替身范围
--------
本文件复用既有 Stage A 的 ``_FakePort`` / ``_FakeRuntime`` / ``_Decision`` 替身（不启动
真实 Supply/Activity socket）；真实 owner 的完整链路由 ``scripts/past_candidate_behavior_smoke.py``
覆盖。这里没有真实模型、没有网络、没有产品调用。

Run (POSIX host)::

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_past_candidate_behavior
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

import autonomous_life_cycle as alc  # noqa: E402
import past_candidate_bridge as pcb  # noqa: E402
import scripts.past_candidate_behavior_smoke as smoke  # noqa: E402
from tests.test_v04_autonomous_day_stage import (  # noqa: E402
    _Decision, _FakePort, _FakeRuntime)

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

TS = "2026-11-02T09:40:00+00:00"
A1 = "artifact:" + "1" * 32
A2 = "artifact:" + "2" * 32
A3 = "artifact:" + "3" * 32
H1 = "artifact-version:" + "a" * 8
H2 = "artifact-version:" + "b" * 8
H3 = "artifact-version:" + "c" * 8
ACT = "actv:past-candidate:1"


def _cycle(*, artifacts=(A1, A2), reading_artifact_id=None,
           private_items_artifact_id=None):
    root = tempfile.mkdtemp(prefix="nyairo-past-candidate-")
    port = _FakePort([{"artifact_id": artifact, "title": f"作品{artifact[-2:]}",
                       "head_version_id": head, "version_number": 1,
                       "lifecycle": "ACTIVE", "revision": 1, "content": "一段"}
                      for artifact, head in zip(artifacts, (H1, H2, H3))])
    runtime = _FakeRuntime(root)
    cycle = alc.AutonomousLifeCycle(
        runtime, artifact_port=port, subject_id="subject:test",
        workspace_id="workspace:test", artifact_grant_id="grant:past-candidate",
        reading_artifact_id=reading_artifact_id,
        private_items_artifact_id=private_items_artifact_id,
        enabled=True, env={}, pause_state_root=root, continuity_state_root=root)
    return cycle, runtime, port


def _current_candidate(runtime, *, target_refs):
    candidate_id = "cand:past:current"
    runtime.candidate_read.rows[candidate_id] = {
        "candidate_id": candidate_id,
        "candidate_kind": "CONTINUE_CURRENT",
        "source_kind": "CURRENT_ACTIVITY",
        "source_ref": ACT,
        "activity_ref": ACT,
        "target_refs": list(target_refs),
    }
    return candidate_id


class PastCandidateBridgeTest(unittest.TestCase):
    """纯投影：只认 ``artifact:`` 作品 ref，版本/其它命名空间/排除项一律不认。"""

    def test_the_activity_projection_prefers_its_own_subject_refs(self):
        record = {"subject_refs": [A2, H2], "target_ref": A3, "checkpoint_ref": H1}
        self.assertEqual(pcb.work_ref_from_activity(record), A2)
        self.assertEqual(pcb.work_ref_from_candidate({"target_refs": [H2, A3]}), A3)

    def test_a_version_or_foreign_namespace_is_not_a_work_target(self):
        for ref in (H2, "closeout:artifact:" + "4" * 32, "place:park",
                    "world_fact:smoke:1", "artifactversion:" + "d" * 8):
            self.assertIsNone(pcb.work_ref_from_refs([ref]), ref)

    def test_an_excluded_artifact_is_never_projected(self):
        self.assertIsNone(pcb.work_ref_from_candidate(
            {"target_refs": [A2]}, exclusions={A2[len("artifact:"):]}))
        self.assertEqual(pcb.work_ref_from_candidate(
            {"target_refs": [A2, A3]}, exclusions={A2[len("artifact:"):]}), A3)

    def test_an_empty_record_answers_none(self):
        self.assertIsNone(pcb.work_ref_from_activity(None))
        self.assertIsNone(pcb.work_ref_from_activity({}))
        self.assertIsNone(pcb.work_ref_from_candidate({}))
        self.assertIsNone(pcb.work_ref_from_candidate({}, exclusions={A1}))


class SmokeStubSelectionTest(unittest.TestCase):
    """脚本替身的选择器：按整个 target ref 与命名空间选，不靠子串或列表顺序。

    真实一轮里同一件作品会同时出现「继续」和「收尾」两种候选；收尾候选带的
    ``closeout:artifact:artifact:<id>`` 含有作品 id 的子串。选择器若用字符串包含，
    在收尾候选排前面时会选中它（真实冒烟首轮因此报 ALREADY_DONE/START）。这里用
    确定性夹具覆盖：反转顺序、不同 id、只有收尾、仅版本/外来命名空间、合法性缺失。
    """

    # The owner's own artifact_id already starts with ``artifact:``; the personal-opportunity
    # source then builds ``f"artifact:{artifact_id}"``, so the offered work ref really reads
    # ``artifact:artifact:<uuid>``.  These fixtures keep that production spelling.
    SEED = "artifact:" + "a" * 32
    SEED_WORK_REF = "artifact:" + SEED
    OTHER = "artifact:" + "b" * 32
    OTHER_WORK_REF = "artifact:" + OTHER
    HEAD = "artifactversion:" + "1" * 8

    @staticmethod
    def _row(candidate_id, *, refs, kind="CONSIDER_PERSONAL_OPPORTUNITY",
             legal=("START",)):
        return {"candidate_id": candidate_id, "candidate_kind": kind,
                "source_kind": "PERSONAL_OPPORTUNITY", "target_refs": list(refs),
                "legal_decisions": list(legal)}

    def _work_row(self, candidate_id="cand:work:1", refs=None, legal=("START",)):
        return self._row(candidate_id, refs=refs or [self.SEED_WORK_REF, self.HEAD],
                         legal=legal)

    def _closeout_row(self, candidate_id="cand:closeout:1"):
        return self._row(candidate_id, refs=[
            "closeout:artifact:" + self.SEED, self.SEED_WORK_REF])

    def test_the_owner_prefixed_id_maps_to_the_offered_work_ref(self):
        self.assertEqual(smoke._work_ref_for_seed(self.SEED), self.SEED_WORK_REF)
        bare = self.SEED[len("artifact:"):]
        self.assertEqual(smoke._work_ref_for_seed(bare), self.SEED)
        self.assertEqual(smoke._work_ref_for_seed(self.SEED_WORK_REF),
                         self.SEED_WORK_REF)

    def test_the_exact_work_offer_wins_whatever_the_order(self):
        work = self._work_row()
        closeout = self._closeout_row()
        for offered in ([closeout, work], [work, closeout]):
            matches = smoke._work_start_matches(offered, self.SEED)
            self.assertEqual([row["candidate_id"] for row in matches],
                             ["cand:work:1"], offered)

    def test_a_closeout_only_offer_is_never_a_start_target(self):
        self.assertEqual(smoke._work_start_matches([self._closeout_row()], self.SEED), [])
        # Even if the plain ref is absent, the closeout spelling still contains the seed id:
        # a substring match would accept it, a whole-ref match must not.
        seed_only_closeout = self._row(
            "cand:closeout:2", refs=["closeout:artifact:" + self.SEED])
        self.assertEqual(
            smoke._work_start_matches([seed_only_closeout], self.SEED), [])

    def test_a_different_id_does_not_match(self):
        other = self._work_row("cand:work:other",
                               refs=[self.OTHER_WORK_REF, self.HEAD])
        self.assertEqual(smoke._work_start_matches([other], self.SEED), [])
        self.assertEqual(
            [row["candidate_id"] for row in
             smoke._work_start_matches([other], self.OTHER)],
            ["cand:work:other"])

    def test_start_must_be_legal_for_that_candidate(self):
        not_startable = self._work_row(legal=("CONTINUE",))
        self.assertEqual(smoke._work_start_matches([not_startable], self.SEED), [])

    def test_a_bare_seed_id_is_normalized_to_the_artifact_ref(self):
        bare = self.SEED[len("artifact:"):]
        single_prefix = self._row("cand:work:bare",
                                  refs=[self.SEED, self.HEAD])
        matches = smoke._work_start_matches([single_prefix], bare)
        self.assertEqual([row["candidate_id"] for row in matches], ["cand:work:bare"])

    def test_continue_matching_excludes_closeout_and_non_current_rows(self):
        current = self._row("cand:continue:1", kind="CONTINUE_CURRENT",
                            refs=[self.SEED_WORK_REF, self.HEAD], legal=("CONTINUE",))
        wrapped = self._row("cand:continue:closeout", kind="CONTINUE_CURRENT",
                            refs=["closeout:artifact:" + self.SEED, self.SEED_WORK_REF],
                            legal=("CONTINUE",))
        not_current = self._row("cand:start:1", kind="CONSIDER_PERSONAL_OPPORTUNITY",
                                refs=[self.SEED_WORK_REF], legal=("START",))
        not_legal = self._row("cand:continue:2", kind="CONTINUE_CURRENT",
                              refs=[self.SEED_WORK_REF], legal=("START",))
        for offered in ([wrapped, not_current, not_legal, current],
                        [current, not_legal, not_current, wrapped]):
            matches = smoke._work_continue_matches(offered)
            self.assertEqual([row["candidate_id"] for row in matches],
                             ["cand:continue:1"], offered)


@POSIX_ONLY
class CurrentActivityTargetTest(unittest.TestCase):
    """正式 step / adoption / round report 都以候选自带的 owner 目标为准。"""

    def test_the_candidate_target_wins_over_an_unrelated_bound_work(self):
        cycle, runtime, _port = _cycle()
        cycle.work_artifact_id = A1
        chosen = _current_candidate(runtime, target_refs=[A2, H2])
        decision = _Decision("CONTINUE", selected=chosen, target_activity_ref=ACT)

        point, selection = cycle._read_resume_point()
        self.assertEqual(point.artifact_id, A1)
        self.assertEqual(selection["selected_by"], "BOUND_WORK")

        step = cycle._work_step_spec_for_decision(decision, fallback=point, observed_at=TS)
        self.assertEqual(step["payload_spec"]["artifact_id"], A2, step)
        self.assertEqual(step["payload_spec"]["expected_head"], H2, step)
        # The AR-0 target keeps the same spelling every work step uses (the owner id is already
        # prefixed in this fixture, so the step adds its own namespace once).
        self.assertEqual(step["target_ref"], "artifact:" + A2, step)

        spec = cycle._work_adoption_spec_for_decision(decision, fallback=point)
        self.assertIn(A2, spec["target_refs"])
        self.assertNotIn(A1, spec["target_refs"])

        routed = cycle.round_point_for(decision)
        self.assertIsNotNone(routed)
        self.assertEqual(routed.artifact_id, A2)

    def test_without_any_work_target_the_round_fallback_is_kept(self):
        cycle, runtime, _port = _cycle()
        cycle.work_artifact_id = A1
        chosen = _current_candidate(runtime, target_refs=[ACT])
        decision = _Decision("CONTINUE", selected=chosen, target_activity_ref=ACT)
        point, _selection = cycle._read_resume_point()
        step = cycle._work_step_spec_for_decision(decision, fallback=point, observed_at=TS)
        self.assertEqual(step["payload_spec"]["artifact_id"], A1, step)
        self.assertIsNone(cycle.round_point_for(decision))

    def test_the_reading_material_is_never_turned_into_a_work(self):
        cycle, runtime, _port = _cycle(artifacts=(A1, A2, A3), reading_artifact_id=A3,
                                       private_items_artifact_id=A2)
        cycle.work_artifact_id = A1
        chosen = _current_candidate(runtime, target_refs=[A3])
        decision = _Decision("CONTINUE", selected=chosen, target_activity_ref=ACT)
        point, _selection = cycle._read_resume_point()
        # A2 is the private-items document and A3 the reading material: both are excluded, so the
        # helper names no work and the existing fallback stands.
        self.assertIsNone(cycle._named_work_ref(decision))
        step = cycle._work_step_spec_for_decision(decision, fallback=point, observed_at=TS)
        self.assertEqual(step["payload_spec"]["artifact_id"], A1, step)

    def test_an_archived_target_is_refused_by_name_not_retargeted(self):
        cycle, runtime, port = _cycle()
        cycle.work_artifact_id = A1
        port.artifacts[A2]["lifecycle"] = "ARCHIVED"
        chosen = _current_candidate(runtime, target_refs=[A2, H2])
        decision = _Decision("CONTINUE", selected=chosen, target_activity_ref=ACT)
        point, _selection = cycle._read_resume_point()
        with self.assertRaises(alc.AutonomousLifeError) as ctx:
            cycle._work_step_spec_for_decision(decision, fallback=point, observed_at=TS)
        self.assertIn("WORK_NOT_CONTINUABLE", str(ctx.exception))
        with self.assertRaises(alc.AutonomousLifeError) as ctx:
            cycle._work_adoption_spec_for_decision(decision, fallback=point)
        self.assertIn("WORK_NOT_CONTINUABLE", str(ctx.exception))

    def test_an_unreadable_target_is_refused_by_name(self):
        cycle, runtime, port = _cycle()
        cycle.work_artifact_id = A1
        chosen = _current_candidate(runtime, target_refs=[A2, H2])
        decision = _Decision("CONTINUE", selected=chosen, target_activity_ref=ACT)
        # The owner no longer answers for that object: the ref is still the candidate's own target,
        # so the refusal is named instead of silently falling back to the bound work.
        port.artifacts.pop(A2)
        point, _selection = cycle._read_resume_point()
        with self.assertRaises(alc.AutonomousLifeError) as ctx:
            cycle._work_step_spec_for_decision(decision, fallback=point, observed_at=TS)
        self.assertIn("WORK_NOT_CONTINUABLE", str(ctx.exception))

    def test_a_version_only_target_keeps_the_fallback(self):
        cycle, runtime, _port = _cycle()
        cycle.work_artifact_id = A1
        chosen = _current_candidate(runtime, target_refs=[H2])
        decision = _Decision("CONTINUE", selected=chosen, target_activity_ref=ACT)
        point, _selection = cycle._read_resume_point()
        self.assertIsNone(cycle._named_work_ref(decision))
        step = cycle._work_step_spec_for_decision(decision, fallback=point, observed_at=TS)
        self.assertEqual(step["payload_spec"]["artifact_id"], A1, step)


if __name__ == "__main__":
    unittest.main()

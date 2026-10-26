#!/usr/bin/env python3
"""v0.5 第二块：正式评价承接到同一作品续作，含退休损失（接缝层正负控）。

本文件只守本单新增的正式阶段与正负控，不重跑上一块的 43/37 验收：

1. AG-1 自己拥有 ``method_trial`` 的形状契约：未知键、ref 不匹配、非法 outcome 都在
   Decision 落盘前被拒；合法形状才交给 cycle。
2. 一条经 ``_bind_method_usage`` → ``_settle_method_binding`` 的正式 Decision 评价，
   由 B 的真实 ``PersonalPracticeService`` 保存；被评价版本是**本轮 offered 的那一版**，
   后果 refs 只来自 owner 的 step receipt（作品 + 版本），做法文档不会是自己的结果。
3. 无 A 可读证据时 seam 在写之前拒绝；owner CAS 已把 head 移走时评价被 owner 拒，
   旧绑定不静默改指。
4. 反例（NOT_HELPED）同样经正式阶段保存为 CONTESTED，并在下一轮 context 与
   v0.5 有界评价投影里可读。
5. 退休后的手柄不在本轮 offered 行里；cycle 对旧手柄不记录 usage/trial，AG-1 的拒绝
   由冒烟里的正式 ``evaluate_once`` 覆盖。

真实 socket 全链（真实 Supply owner + 真实 M0 + 正式 AG-0/AG-1/AG-2/AR-0）见
``scripts/v05_practice_continuation_smoke.py``；Decision/内容都是脚本替身。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                 "life cycle imports fcntl; run on POSIX/WSL")

if os.name == "posix":  # pragma: no branch - Windows skips every class below
    import agency_decision_ag1 as ag1  # noqa: E402
    import autonomous_life_cycle as alc  # noqa: E402
    import life_practice_context as lpc  # noqa: E402
    import personal_practice_service as pps  # noqa: E402
    from test_v04_personal_practice_stage import FakeMemory, seeded_port, service_for  # noqa: E402
else:  # pragma: no cover - the native Windows module cannot even import AG-1
    ag1 = alc = lpc = pps = None
    FakeMemory = seeded_port = service_for = None

INSTRUCTION = "先写一句结论，再补三段理由"
SCOPE = "只在这个长文项目里"
UNCERTAINTY = "不知道短文本是否也适用"
EVIDENCE_ID = "exp:continuation"
EVIDENCE_TEXT = "那一版先写结论，确实一次写完了。"
WORK_REF = "artifact:work-abcd1234"
WORK_VERSION = "ver-work-7"
OTHER_WORK_REF = "artifact:work-other999"


def _record(**overrides):
    base = {"decision_id": "dec:v05-continuation", "method_ref": None, "method_trial": None}
    base.update(overrides)
    return SimpleNamespace(**base)


def _trial(ref, *, outcome="HELPED", evidence=(EVIDENCE_ID,), detail="这次先写结论顺一些"):
    return {"method_ref": ref, "outcome": outcome, "detail": detail,
            "experience_ids": list(evidence)}


def _cycle(service, memory, round_rows):
    """A real cycle object with only the settlement attributes the seam needs."""

    cycle = alc.AutonomousLifeCycle.__new__(alc.AutonomousLifeCycle)
    cycle.practice = lpc.PracticeSeam(service, memory=memory)
    cycle._round_methods = [dict(row) for row in round_rows]
    cycle._pending_method_usage = None
    cycle._pending_method_trial = None
    cycle.last_method_usage = None
    cycle.last_method_trial = None
    cycle.last_practice_error = None
    return cycle


def _receipt(artifact=WORK_REF, version=WORK_VERSION, effect="APPLIED"):
    return {"step_receipt": {"artifact_id": artifact, "version_id": version,
                             "effect_state": effect}}


class _Base(unittest.TestCase):
    def setUp(self):
        self.port = seeded_port()
        self.memory = FakeMemory()
        self.experience_id = self.memory.add(EVIDENCE_ID, EVIDENCE_TEXT)
        self.service = service_for(self.port, self.memory)
        created = self.service.create_method(
            title="先写结论", instruction=INSTRUCTION, scope=SCOPE, uncertainty=UNCERTAINTY,
            experience_ids=[self.experience_id],
            operation_key="practice:v05-continuation:method")
        self.assertTrue(created["ok"], created)
        self.method_id = str(created["data"]["artifact_id"])
        self.seam = lpc.PracticeSeam(self.service, memory=self.memory)
        self.offered = [dict(row) for row in self.seam.methods(query="")]
        self.assertEqual(len(self.offered), 1, self.offered)
        self.method_ref = str(self.offered[0]["method_ref"])
        self.offered_head = str(self.offered[0]["head_version_id"])

    def stored(self):
        return self.service.read_method(self.method_id)["data"]


@POSIX_ONLY
class FormalTrialContractTest(_Base):
    """AG-1 拥有形状；正式 Decision 的评价经真实 owner 保存。"""

    def test_ag1_owns_the_trial_shape_before_any_decision_is_committed(self):
        clean = ag1.validate_method_trial(_trial(self.method_ref), method_ref=self.method_ref)
        self.assertEqual(clean["outcome"], "HELPED")
        self.assertEqual(clean["experience_ids"], [EVIDENCE_ID])

        cases = [
            (dict(_trial(self.method_ref), unexpected="x"),
             ag1.FAILURE_CLASS_INVALID_METHOD_TRIAL),
            (dict(_trial(self.method_ref), outcome="MAYBE"),
             ag1.FAILURE_CLASS_INVALID_METHOD_TRIAL),
            (_trial(lpc.method_ref_for("artifact:ffffffffffffffff")),
             ag1.FAILURE_CLASS_INVALID_METHOD_TRIAL),
            (dict(_trial(self.method_ref), method_ref="not-a-handle"),
             ag1.FAILURE_CLASS_INVENTED_METHOD_REF),
        ]
        for raw, failure_class in cases:
            with self.assertRaises(ag1.DecisionValidationError) as caught:
                ag1.validate_method_trial(raw, method_ref=self.method_ref)
            self.assertEqual(caught.exception.failure_class, failure_class, raw)

    def test_the_formal_decision_trial_is_saved_against_the_offered_head(self):
        cycle = _cycle(self.service, self.memory, self.offered)
        cycle._bind_method_usage(
            _record(method_ref=self.method_ref, method_trial=_trial(self.method_ref)))
        base = _receipt()
        cycle._settle_method_binding(base)

        usage = base["method_usage"]
        trial = base["method_trial"]
        self.assertEqual(usage["artifact_id"], self.method_id)
        self.assertEqual(usage["version_ref"], self.offered_head,
                         "the usage bound a version other than the offered head")
        self.assertEqual(usage["consequence_refs"], [WORK_REF, "artifactversion:%s" % WORK_VERSION])
        self.assertNotIn(self.method_id, usage["consequence_refs"])
        self.assertEqual(trial["outcome"], "HELPED")
        self.assertTrue(trial["evidence_usable"])
        self.assertEqual(trial["method_state"], pps.METHOD_STATE_TRIAL)

        stored = self.stored()
        self.assertEqual(stored["method_state"], pps.METHOD_STATE_TRIAL)
        self.assertEqual(len(stored["trials"]), 1)
        self.assertEqual(stored["trials"][0]["version_id"], self.offered_head,
                         "the verdict was filed against a version the round never offered")
        self.assertEqual(stored["trials"][0]["outcome"], "HELPED")

    def test_without_readable_evidence_the_seam_refuses_before_writing(self):
        cycle = _cycle(self.service, self.memory, self.offered)
        cycle._bind_method_usage(_record(
            method_ref=self.method_ref,
            method_trial=_trial(self.method_ref, evidence=())))
        base = _receipt()
        cycle._settle_method_binding(base)

        self.assertEqual(base["method_trial"]["status"], "REFUSED")
        self.assertEqual(base["method_trial"]["reason_code"], lpc.REASON_NO_EVIDENCE)
        self.assertEqual(self.stored()["trials"], [],
                         "a verdict without evidence still reached the owner")
        self.assertEqual(self.stored()["method_state"], pps.METHOD_STATE_TENTATIVE)
        # The usage still reports the owner's real result: it is not a verdict.
        self.assertEqual(base["method_usage"]["consequence_refs"],
                         [WORK_REF, "artifactversion:%s" % WORK_VERSION])

    def test_an_owner_cas_moved_head_refuses_the_verdict_and_keeps_the_binding(self):
        revised = self.service.revise_method(
            self.method_id, instruction="并发改过的做法", expected_head=self.offered_head,
            operation_key="practice:v05-continuation:revise")
        self.assertTrue(revised["ok"], revised)

        cycle = _cycle(self.service, self.memory, self.offered)
        cycle._bind_method_usage(
            _record(method_ref=self.method_ref, method_trial=_trial(self.method_ref)))
        base = _receipt()
        cycle._settle_method_binding(base)

        self.assertEqual(base["method_trial"]["status"], "REFUSED", base["method_trial"])
        self.assertEqual(self.stored()["trials"], [])
        self.assertEqual(self.stored()["method_state"], pps.METHOD_STATE_TENTATIVE)
        self.assertEqual(base["method_usage"]["version_ref"], self.offered_head)

    def test_a_retired_handle_is_not_in_this_rounds_offer(self):
        retired = self.service.retire_method(
            self.method_id, expected_head=self.offered_head,
            operation_key="practice:v05-continuation:retire")
        self.assertTrue(retired["ok"], retired)
        context = self.seam.decision_context(query="")
        self.assertEqual(context["methods"], [])
        self.assertEqual(context["method_refs"], [])

        cycle = _cycle(self.service, self.memory, [])
        cycle._bind_method_usage(_record(method_ref=self.method_ref))
        self.assertIsNone(cycle._pending_method_usage)
        self.assertIn(lpc.REASON_METHOD_REF_UNKNOWN, str(cycle.last_practice_error))
        base = {}
        cycle._settle_method_binding(base)
        self.assertEqual(base, {}, "a retired handle produced a usage or a trial")

        # The document is still hers: retirement is a state, not a delete.
        stored = self.stored()
        self.assertEqual(stored["method_state"], pps.METHOD_STATE_RETIRED)
        self.assertTrue(str(stored["body"]).strip())
        self.assertEqual(stored["instruction"], INSTRUCTION)


@POSIX_ONLY
class CounterexampleVisibilityTest(_Base):
    """NOT_HELPED 同样经正式阶段保存，并在下一轮真的可读。"""

    def test_a_formal_counterexample_moves_the_method_to_contested_and_is_readable(self):
        cycle = _cycle(self.service, self.memory, self.offered)
        cycle._bind_method_usage(_record(
            method_ref=self.method_ref,
            method_trial=_trial(self.method_ref, outcome="NOT_HELPED",
                                detail="这次先写结论反而卡住")))
        base = _receipt()
        cycle._settle_method_binding(base)

        self.assertEqual(base["method_trial"]["outcome"], "NOT_HELPED")
        self.assertEqual(base["method_trial"]["method_state"], pps.METHOD_STATE_CONTESTED)
        stored = self.stored()
        self.assertEqual(stored["method_state"], pps.METHOD_STATE_CONTESTED)
        self.assertEqual(len(stored["counterexamples"]), 1)

        # Next decision round: the changed state, the count and the bounded explicit verdict.
        context = self.seam.decision_context(query="")
        row = [item for item in context["methods"] if item["method_ref"] == self.method_ref][0]
        self.assertEqual(row["method_state"], pps.METHOD_STATE_CONTESTED)
        self.assertEqual(int(row["counterexample_count"]), 1)
        evaluation = self.seam.evaluations_for(self.seam.last_methods)[self.method_id]
        self.assertTrue(evaluation["available"], evaluation)
        self.assertEqual(evaluation["outcome"], "NOT_HELPED")
        self.assertEqual(evaluation["version_id"], self.offered_head)
        self.assertEqual(evaluation["evidence_count"], 1)
        self.assertNotIn("detail", evaluation)

        # CONTESTED is a state, not a loss: the handle stays offered next round.
        self.assertIn(self.method_ref, context["method_refs"])
        self.assertNotIn("withheld_methods", context)

    def test_another_work_never_becomes_the_target_of_this_round(self):
        """A's owner target wins over an unrelated bound work (existing rule, re-asserted)."""

        import past_candidate_bridge as pcb
        from test_past_candidate_behavior import _cycle as past_cycle
        from test_past_candidate_behavior import _current_candidate
        from test_v04_autonomous_day_stage import _Decision

        a1 = "artifact:" + "1" * 32
        a2 = "artifact:" + "2" * 32
        h2 = "artifactversion:" + "b" * 8
        activity = "actv:v05-continuation:1"
        cycle, runtime, _port = past_cycle()
        cycle.work_artifact_id = a2  # the bound work is the *other* object
        chosen = _current_candidate(runtime, target_refs=[a1, h2])
        decision = _Decision("CONTINUE", selected=chosen, target_activity_ref=activity)

        point, selection = cycle._read_resume_point()
        self.assertEqual(point.artifact_id, a2)
        self.assertEqual(selection["selected_by"], "BOUND_WORK")
        self.assertEqual(cycle.round_point_for(decision).artifact_id, a1)
        step = cycle._work_step_spec_for_decision(decision, fallback=point,
                                                  observed_at="2026-11-02T09:40:00+00:00")
        self.assertEqual(step["payload_spec"]["artifact_id"], a1, step)
        self.assertEqual(pcb.work_ref_from_candidate(
            {"target_refs": [a1, h2]}), a1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

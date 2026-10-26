#!/usr/bin/env python3
"""v0.4 第三段接缝：结果 → 本轮真实使用的做法 → 有证据评价 → 下一轮可见状态。

这个文件只测本轮修的两处归属事实（都是既有规则的实现，不是新评价类型）：

1. 做法绑定必须落在**本轮真的提供给 Decision 的那次读取**上。Decision 提交后，同轮后续
   读取（写作提示自己的方法检索）会覆盖 ``PracticeSeam.last_methods``；若在这之后按手柄
   重新解析，就会把模型从未见过的**新 head** 当成它评价的版本。绑定应使用 offered 行的
   head；方法在提交后被改过时，评价按 CAS 被 owner 拒绝，而不是静默改绑。
2. 结果 refs 只能来自 owner 真实的 step receipt。没有 step 的回合（NO_ACTION / WAIT /
   PAUSE）没有结果，因此 ``consequence_refs`` 为空，不能把**做法文档自身**当成结果。

真实实现：``PersonalPracticeService`` + 内存 owner 替身端口（复用阶段 B 的 ``FakePort``），
A 侧 Memory 用形状替身。Decision/内容模型不在本文件内；真实 socket 全链见
``scripts/result_method_behavior_smoke.py``。反例、无证据 UNKNOWN、证据扣留、未提供/未使用
方法不冒归因、重复结算只写一次、成功写版本不等于 HELPED，都在这里做正负控。
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
    import autonomous_life_cycle as alc  # noqa: E402
    import life_practice_context as lpc  # noqa: E402
    import personal_practice_service as pps  # noqa: E402
    from test_v04_personal_practice_stage import FakeMemory, seeded_port, service_for  # noqa: E402
else:  # pragma: no cover - the native Windows module cannot even import AG-1
    alc = lpc = pps = None
    FakeMemory = seeded_port = service_for = None

METHOD_INSTRUCTION = "先写一句结论，再补三段理由"
METHOD_SCOPE = "只在这个长文项目里"
METHOD_UNCERTAINTY = "不知道短文本是否也适用"
EVIDENCE_ID = "exp:evidence"
EVIDENCE_TEXT = "先写结论那一页就写完了。"
WORK_REF = "artifact:work1234"
WORK_VERSION = "ver-work-2"


def _record(**overrides):
    base = {"decision_id": "dec:result-method", "method_ref": None, "method_trial": None}
    base.update(overrides)
    return SimpleNamespace(**base)


def _trial(ref, *, outcome="HELPED", evidence=(EVIDENCE_ID,), detail="这次先写结论顺一些"):
    return {"method_ref": ref, "outcome": outcome, "detail": detail,
            "experience_ids": list(evidence)}


def _cycle(service, memory, round_rows):
    """A real cycle object with only the two settlement attributes it needs.

    The methods under test are ``AutonomousLifeCycle._bind_method_usage`` and
    ``_settle_method_binding``; everything else on the cycle is irrelevant to them.
    """

    cycle = alc.AutonomousLifeCycle.__new__(alc.AutonomousLifeCycle)
    cycle.practice = lpc.PracticeSeam(service, memory=memory)
    cycle._round_methods = [dict(row) for row in round_rows]
    cycle._pending_method_usage = None
    cycle._pending_method_trial = None
    cycle.last_method_usage = None
    cycle.last_method_trial = None
    cycle.last_practice_error = None
    return cycle


class _CountingService:
    """Delegate to the real practice service, counting ``record_trial`` calls."""

    def __init__(self, service):
        self._service = service
        self.trial_calls = []
        self._name = type(service).__name__

    def __getattr__(self, name):
        attribute = getattr(self._service, name)
        if name != "record_trial":
            return attribute

        def counted(*args, **kwargs):
            self.trial_calls.append({"args": args, "kwargs": dict(kwargs)})
            return attribute(*args, **kwargs)

        return counted


@POSIX_ONLY
class BindingAndSettlementTest(unittest.TestCase):
    def setUp(self):
        self.port = seeded_port()
        self.memory = FakeMemory()
        self.experience_id = self.memory.add(EVIDENCE_ID, EVIDENCE_TEXT)
        self.service = service_for(self.port, self.memory)
        created = self.service.create_method(
            title="先写结论", instruction=METHOD_INSTRUCTION, scope=METHOD_SCOPE,
            uncertainty=METHOD_UNCERTAINTY, experience_ids=[self.experience_id],
            operation_key="practice:method:result-1")
        self.assertTrue(created["ok"], created)
        self.artifact_id = str(created["data"]["artifact_id"])
        # The exact read the Decision was offered (and AG-1 validated the handle against).
        self.seam = lpc.PracticeSeam(self.service, memory=self.memory)
        self.offered = [dict(row) for row in self.seam.methods(query="")]
        self.assertEqual(len(self.offered), 1, self.offered)
        self.method_ref = self.offered[0]["method_ref"]
        self.offered_head = self.offered[0]["head_version_id"]

    # -- 1. the version this round offered, not a later head --------------------------

    def test_a_method_revised_after_the_decision_is_not_silently_rebound(self):
        revised = self.service.revise_method(
            self.artifact_id, instruction="改过的做法", expected_head=self.offered_head,
            operation_key="practice:revise:after-decision")
        self.assertTrue(revised["ok"], revised)
        later = self.seam.methods(query="")
        self.assertEqual(len(later), 1)
        self.assertNotEqual(later[0]["head_version_id"], self.offered_head)

        cycle = _cycle(self.service, self.memory, self.offered)
        cycle._bind_method_usage(
            _record(method_ref=self.method_ref, method_trial=_trial(self.method_ref)))

        usage = cycle._pending_method_usage
        self.assertIsNotNone(usage, cycle.last_practice_error)
        self.assertEqual(usage["artifact_id"], self.artifact_id)
        self.assertEqual(usage["version_ref"], self.offered_head,
                         "binding followed the later read instead of the offered head")
        self.assertEqual(cycle._pending_method_trial["expected_head"], self.offered_head)

        base = {"step_receipt": {"artifact_id": WORK_REF, "version_id": WORK_VERSION,
                                 "effect_state": "APPLIED"}}
        cycle._settle_method_binding(base)

        self.assertEqual(base["method_trial"]["status"], "REFUSED", base["method_trial"])
        self.assertEqual(base["method_usage"]["consequence_refs"],
                         [WORK_REF, "artifactversion:%s" % WORK_VERSION])
        stored = self.service.read_method(self.artifact_id)["data"]
        self.assertEqual(stored["trials"], [], "a verdict about an unseen head was recorded")
        self.assertEqual(stored["method_state"], pps.METHOD_STATE_TENTATIVE)

    def test_the_offered_head_is_kept_even_when_no_later_read_happens(self):
        cycle = _cycle(self.service, self.memory, self.offered)
        cycle._bind_method_usage(_record(method_ref=self.method_ref))
        self.assertEqual(cycle._pending_method_usage["version_ref"], self.offered_head)

    # -- 2. consequence refs only from the owner's own receipt ------------------------

    def test_no_step_receipt_names_no_result_and_never_the_method_itself(self):
        cycle = _cycle(self.service, self.memory, self.offered)
        cycle._bind_method_usage(_record(method_ref=self.method_ref))
        base = {}
        cycle._settle_method_binding(base)

        usage = base["method_usage"]
        self.assertEqual(usage["consequence_refs"], [],
                         "a round with no step invented a consequence")
        self.assertNotIn(self.artifact_id, usage["consequence_refs"],
                         "the method document was named as its own consequence")
        self.assertIsNone(usage["step_effect"])
        self.assertIsNone(usage["version_written"])
        self.assertNotIn("method_trial", base)

    def test_an_applied_receipt_carries_the_real_work_and_version_only(self):
        cycle = _cycle(self.service, self.memory, self.offered)
        cycle._bind_method_usage(_record(method_ref=self.method_ref))
        base = {"step_receipt": {"artifact_id": WORK_REF, "version_id": WORK_VERSION,
                                 "effect_state": "APPLIED"}}
        cycle._settle_method_binding(base)

        usage = base["method_usage"]
        self.assertEqual(usage["consequence_refs"],
                         [WORK_REF, "artifactversion:%s" % WORK_VERSION])
        self.assertNotIn(self.artifact_id, usage["consequence_refs"])
        self.assertEqual(usage["step_effect"], "APPLIED")
        self.assertEqual(usage["version_written"], WORK_VERSION)

    def test_a_successful_write_never_becomes_a_helped_trial(self):
        cycle = _cycle(self.service, self.memory, self.offered)
        cycle._bind_method_usage(_record(method_ref=self.method_ref))
        base = {"step_receipt": {"artifact_id": WORK_REF, "version_id": WORK_VERSION,
                                 "effect_state": "APPLIED"}}
        cycle._settle_method_binding(base)

        self.assertIn("method_usage", base)
        self.assertNotIn("method_trial", base, "a written version was treated as a verdict")
        stored = self.service.read_method(self.artifact_id)["data"]
        self.assertEqual(stored["trials"], [])
        self.assertEqual(stored["method_state"], pps.METHOD_STATE_TENTATIVE)

    # -- 3. no attribution for an unoffered or unused method --------------------------

    def test_a_handle_outside_this_rounds_offer_is_never_attributed(self):
        cycle = _cycle(self.service, self.memory, self.offered)
        invented = lpc.method_ref_for("artifact:ffffffffffffffff")
        self.assertNotEqual(invented, self.method_ref)
        cycle._bind_method_usage(_record(method_ref=invented))

        self.assertIsNone(cycle._pending_method_usage)
        self.assertIn(lpc.REASON_METHOD_REF_UNKNOWN, str(cycle.last_practice_error))
        base = {}
        cycle._settle_method_binding(base)
        self.assertEqual(base, {})

    def test_a_record_that_names_no_method_attributes_nothing(self):
        counting = _CountingService(self.service)
        cycle = _cycle(counting, self.memory, self.offered)
        cycle._bind_method_usage(_record(method_trial=_trial(self.method_ref)))
        self.assertIsNone(cycle._pending_method_usage)
        base = {}
        cycle._settle_method_binding(base)
        self.assertEqual(base, {})
        self.assertEqual(counting.trial_calls, [])

    # -- 4. at most one trial per round ------------------------------------------------

    def test_repeated_settlement_records_the_same_trial_once(self):
        counting = _CountingService(self.service)
        cycle = _cycle(counting, self.memory, self.offered)
        cycle._bind_method_usage(
            _record(method_ref=self.method_ref, method_trial=_trial(self.method_ref)))
        base = {"step_receipt": {"artifact_id": WORK_REF, "version_id": WORK_VERSION,
                                 "effect_state": "APPLIED"}}
        cycle._settle_method_binding(base)
        first = dict(base["method_trial"])
        self.assertEqual(first.get("outcome"), "HELPED", first)
        self.assertEqual(first.get("method_state"), pps.METHOD_STATE_TRIAL)

        second = {}
        cycle._settle_method_binding(second)
        self.assertEqual(second, {}, "settlement ran twice and rewrote the report")
        self.assertEqual(len(counting.trial_calls), 1)
        stored = self.service.read_method(self.artifact_id)["data"]
        self.assertEqual(len(stored["trials"]), 1)

    def test_no_action_stays_legal_and_binds_no_result(self):
        cycle = _cycle(self.service, self.memory, self.offered)
        cycle._bind_method_usage(_record(method_ref=self.method_ref))
        base = {}
        cycle._settle_method_binding(base)
        self.assertEqual(set(base), {"method_usage"})
        self.assertEqual(base["method_usage"]["consequence_refs"], [])
        self.assertNotIn("outcome", base)
        self.assertNotIn("step_receipt", base)


@POSIX_ONLY
class EvaluationAndNextRoundVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.memory = FakeMemory()
        self.experience_id = self.memory.add(EVIDENCE_ID, EVIDENCE_TEXT)
        self.service = service_for(seeded_port(), self.memory)
        created = self.service.create_method(
            title="先写结论", instruction=METHOD_INSTRUCTION, scope=METHOD_SCOPE,
            uncertainty=METHOD_UNCERTAINTY, experience_ids=[self.experience_id],
            operation_key="practice:method:evaluation-1")
        self.artifact_id = str(created["data"]["artifact_id"])
        self.seam = lpc.PracticeSeam(self.service, memory=self.memory)
        self.rows = self.seam.methods(query="")
        self.method_ref = self.rows[0]["method_ref"]

    def _head(self):
        return self.service.read_method(self.artifact_id)["data"]["head_version_id"]

    def test_no_evidence_refuses_helped_but_unknown_stays_recordable(self):
        with self.assertRaises(lpc.PracticeSeamError) as caught:
            self.seam.record_trial(artifact_id=self.artifact_id, outcome="HELPED",
                                   detail="没有证据", experience_ids=[],
                                   expected_head=self._head())
        self.assertEqual(caught.exception.reason_code, lpc.REASON_NO_EVIDENCE)

        unknown = self.seam.record_trial(artifact_id=self.artifact_id, outcome="UNKNOWN",
                                         detail="说不好", experience_ids=[],
                                         expected_head=self._head())
        self.assertEqual(unknown["outcome"], "UNKNOWN")
        self.assertFalse(unknown["evidence_usable"])
        self.assertEqual(unknown["method_state"], pps.METHOD_STATE_TENTATIVE)

    def test_a_counterexample_is_kept_and_visible_in_the_next_round(self):
        counter = self.seam.record_trial(
            artifact_id=self.artifact_id, outcome="NOT_HELPED",
            detail="这次先写结论反而卡住", experience_ids=[self.experience_id],
            expected_head=self._head())
        self.assertTrue(counter["evidence_usable"], counter)
        self.assertEqual(counter["method_state"], pps.METHOD_STATE_CONTESTED)

        # What the *next* decision round may see: the changed state and the counterexample
        # count, through the same owner read -- never the old TENTATIVE row.
        context = self.seam.decision_context(query="")
        listed = [row for row in context["methods"] if row["method_ref"] == self.method_ref]
        self.assertEqual(len(listed), 1, context)
        self.assertEqual(listed[0]["method_state"], pps.METHOD_STATE_CONTESTED)
        self.assertEqual(listed[0]["counterexample_count"], 1)
        self.assertEqual(listed[0]["trial_count"], 1)
        # v0.5: the *explicit* verdict itself is readable next round, bounded to its own facts.
        evaluation = self.seam.evaluations_for(self.seam.last_methods)[self.artifact_id]
        self.assertTrue(evaluation["available"], evaluation)
        self.assertEqual(evaluation["outcome"], "NOT_HELPED")
        self.assertEqual(evaluation["evidence_count"], 1)
        self.assertNotIn("detail", evaluation)
        self.assertIn("不是权限", context["note"])

    def test_withdrawn_evidence_is_withheld_and_cannot_support_a_verdict(self):
        head = self._head()
        self.memory.withdrawn.add(self.experience_id)

        self.assertEqual(self.seam.experiences(), [])
        self.assertIsNone(self.seam.read_experience(self.experience_id))
        withheld = self.seam.read_method(self.artifact_id)
        self.assertTrue(withheld["governance_blocked"], withheld)
        self.assertFalse(withheld["content_available"], withheld)
        self.assertEqual(withheld["instruction"], "")
        context = self.seam.decision_context(query="")
        row = [item for item in context["methods"] if item["method_ref"] == self.method_ref][0]
        self.assertEqual(row["instruction"], "",
                         "withdrawn evidence still leaked the method's prose")

        with self.assertRaises(lpc.PracticeSeamError) as caught:
            self.seam.record_trial(artifact_id=self.artifact_id, outcome="HELPED",
                                   detail="证据已经撤回", experience_ids=[self.experience_id],
                                   expected_head=head)
        self.assertEqual(caught.exception.reason_code, lpc.REASON_NO_EVIDENCE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

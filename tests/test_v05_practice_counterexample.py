#!/usr/bin/env python3
"""v0.5 第五条：多情境/反例/修订/退休的接缝级不变量与最小负控。

本文件只守本单要证明的既有规则，不重跑上一块的 44/36：

1. 没有**经 A 治理仍可读**的经历，``HELPED``/``NOT_HELPED`` 都不能写：owner 拒绝，
   seam 在 owner 写入之前就拒绝。
2. ``UNKNOWN`` 不改变状态，不产生反例；一次 ``HELPED`` 不会把已有反例计数抹掉，
   也不存在任何“稳定支持/偏好”状态。
3. 修订是 CAS：旧 head 被拒；用当前 head 修订回 ``TENTATIVE``，旧指令留在
   ``revised_from``，反例一条不少。
4. 退休是逻辑状态：文档、正文、历史仍可读；之后试用与修订都拒；退役手柄不进入
   本轮 offer，AG-1 对 offer 之外的 ref 仍按形状拒绝。
5. 同一个评价操作键重复提交不会多写一条；有界评价投影只给 outcome / 被评价版本 /
   证据条数，不带 detail；head 变了不混读。

真实 socket 全链（真实 Supply / M0 / 正式 AG-0/AG-1/AG-2/AR-0，两个不同作品）见
``scripts/v05_practice_counterexample_smoke.py``。Decision 与内容都是脚本替身。
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
EVIDENCE_ID = "exp:counterexample"
EVIDENCE_TEXT = "那一版先写结论，确实一次写完了。"
SECOND_EVIDENCE_ID = "exp:counterexample:second"
WORK_REF = "artifact:work-abcd1234"
WORK_VERSION = "ver-work-7"


def _record(**overrides):
    base = {"decision_id": "dec:v05-counterexample", "method_ref": None, "method_trial": None}
    base.update(overrides)
    return SimpleNamespace(**base)


def _trial(ref, *, outcome="HELPED", evidence=(EVIDENCE_ID,), detail="这次先写结论顺一些"):
    return {"method_ref": ref, "outcome": outcome, "detail": detail,
            "experience_ids": list(evidence)}


def _cycle(service, memory, round_rows):
    """一个真实 cycle 对象，只装配结算接缝需要的属性。"""

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
        self.second_experience_id = self.memory.add(SECOND_EVIDENCE_ID, "另一件作品里也试过。")
        self.service = service_for(self.port, self.memory)
        created = self.service.create_method(
            title="先写结论", instruction=INSTRUCTION, scope=SCOPE, uncertainty=UNCERTAINTY,
            experience_ids=[self.experience_id],
            operation_key="practice:v05-counterexample:method")
        self.assertTrue(created["ok"], created)
        self.method_id = str(created["data"]["artifact_id"])
        self.seam = lpc.PracticeSeam(self.service, memory=self.memory)
        self.offered = [dict(row) for row in self.seam.methods(query="")]
        self.assertEqual(len(self.offered), 1, self.offered)
        self.method_ref = str(self.offered[0]["method_ref"])
        self.offered_head = str(self.offered[0]["head_version_id"])

    def stored(self):
        return self.service.read_method(self.method_id)["data"]

    def head(self):
        return str(self.stored()["head_version_id"])

    def record(self, outcome, *, evidence=None, key=None, head=None, detail="评价"):
        return self.service.record_trial(
            self.method_id, outcome=outcome, detail=detail,
            experience_ids=[self.experience_id] if evidence is None else list(evidence),
            expected_head=self.head() if head is None else head,
            operation_key=key or ("practice:v05-counterexample:trial:%s" % outcome.lower()))


@POSIX_ONLY
class NoVerdictWithoutEvidenceTest(_Base):
    """没有可读证据，`HELPED`/`NOT_HELPED` 都不能写。"""

    def test_the_owner_refuses_a_verdict_without_evidence(self):
        for outcome in ("HELPED", "NOT_HELPED"):
            answer = self.service.record_trial(
                self.method_id, outcome=outcome, detail="没有证据",
                experience_ids=[], expected_head=self.head(),
                operation_key="practice:v05-counterexample:no-evidence:%s" % outcome)
            self.assertFalse(answer["ok"], answer)
            self.assertEqual(answer.get("reason_code"), "PRACTICE_EVIDENCE_REQUIRED", answer)
        stored = self.stored()
        self.assertEqual(stored["trials"], [])
        self.assertEqual(stored["method_state"], pps.METHOD_STATE_TENTATIVE)

    def test_the_seam_refuses_before_the_owner_is_written(self):
        cycle = _cycle(self.service, self.memory, self.offered)
        cycle._bind_method_usage(_record(
            method_ref=self.method_ref,
            method_trial=_trial(self.method_ref, evidence=())))
        base = _receipt()
        cycle._settle_method_binding(base)

        self.assertEqual(base["method_trial"]["status"], "REFUSED")
        self.assertEqual(base["method_trial"]["reason_code"], lpc.REASON_NO_EVIDENCE)
        self.assertEqual(self.stored()["trials"], [])
        self.assertEqual(self.stored()["method_state"], pps.METHOD_STATE_TENTATIVE)


@POSIX_ONLY
class StateRulesTest(_Base):
    """UNKNOWN 不升级；反例不会被后来的正评价抹掉。"""

    def test_unknown_never_upgrades(self):
        answer = self.record("UNKNOWN", evidence=[], key="practice:v05-counterexample:unknown")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["data"]["method_state"], pps.METHOD_STATE_TENTATIVE)
        stored = self.stored()
        self.assertEqual(stored["method_state"], pps.METHOD_STATE_TENTATIVE)
        self.assertEqual(len(stored["trials"]), 1)
        self.assertEqual(stored["counterexamples"], [])

    def test_a_counterexample_survives_a_later_helped(self):
        first = self.record("NOT_HELPED", key="practice:v05-counterexample:ce")
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["data"]["method_state"], pps.METHOD_STATE_CONTESTED)
        self.assertEqual(len(self.stored()["counterexamples"]), 1)

        second = self.record("HELPED", evidence=[self.second_experience_id],
                             key="practice:v05-counterexample:helped")
        self.assertTrue(second["ok"], second)
        # 既有规则：HELPED 让状态回到 TRIAL；但反例计数与那条评价都不被抹掉，
        # 也没有任何“稳定支持/偏好”状态被写出来。
        stored = self.stored()
        self.assertEqual(stored["method_state"], pps.METHOD_STATE_TRIAL)
        self.assertEqual(len(stored["counterexamples"]), 1)
        self.assertEqual(len(stored["trials"]), 2)
        self.assertNotIn("SUPPORTED", pps.METHOD_STATES)


@POSIX_ONLY
class RevisionCasTest(_Base):
    """修订是 CAS；旧版本与反例留在 owner 历史里。"""

    def setUp(self):
        super().setUp()
        self.trial_head = self.head()
        self.assertTrue(self.record("NOT_HELPED", key="practice:v05-counterexample:pre-revise")["ok"])
        self.pre_head = self.head()
        self.assertTrue(self.stored()["counterexamples"])

    def test_a_stale_head_cannot_revise(self):
        stale = self.service.revise_method(
            self.method_id, instruction="并发改过的做法", expected_head="version-stale",
            operation_key="practice:v05-counterexample:revise-stale")
        self.assertFalse(stale["ok"], stale)
        self.assertEqual(stale.get("status"), pps.STATUS_CONFLICT, stale)

        stored = self.stored()
        self.assertEqual(stored["head_version_id"], self.pre_head)
        self.assertEqual(stored["instruction"], INSTRUCTION)
        self.assertEqual(len(stored["counterexamples"]), 1)

    def test_a_revision_returns_tentative_and_keeps_history(self):
        revised = self.service.revise_method(
            self.method_id, instruction="先写两句结论，再补两段理由",
            scope="长文与回顾", uncertainty="不知道是否适用短文本",
            expected_head=self.pre_head,
            operation_key="practice:v05-counterexample:revise")
        self.assertTrue(revised["ok"], revised)
        data = revised["data"]
        self.assertEqual(data["method_state"], pps.METHOD_STATE_TENTATIVE)
        self.assertEqual(int(data["revisions"]), 1)
        self.assertEqual(int(data["trials_preserved"]), 1)
        self.assertEqual(data["previous"]["instruction"], INSTRUCTION)

        stored = self.stored()
        self.assertEqual(stored["method_state"], pps.METHOD_STATE_TENTATIVE)
        self.assertNotEqual(stored["head_version_id"], self.pre_head)
        self.assertEqual(stored["instruction"], "先写两句结论，再补两段理由")
        self.assertEqual(len(stored["trials"]), 1)
        self.assertEqual(len(stored["counterexamples"]), 1)
        self.assertEqual(stored["trials"][0]["version_id"], self.trial_head,
                         "旧评价被改指到了新版本")


@POSIX_ONLY
class RetirementBoundaryTest(_Base):
    """退休保留文档，拒绝之后的评价与修订，并退出本轮 offer。"""

    def setUp(self):
        super().setUp()
        self.assertTrue(self.record("NOT_HELPED", key="practice:v05-counterexample:pre-retire")["ok"])
        self.pre_head = self.head()
        retired = self.service.retire_method(
            self.method_id, expected_head=self.pre_head,
            operation_key="practice:v05-counterexample:retire")
        self.assertTrue(retired["ok"], retired)
        self.retired = retired["data"]

    def test_retirement_keeps_the_document_and_refuses_later_writes(self):
        self.assertEqual(self.retired["method_state"], pps.METHOD_STATE_RETIRED)
        self.assertTrue(self.retired["content_kept"])
        self.assertEqual(int(self.retired["trials_preserved"]), 1)

        trial = self.record("HELPED", key="practice:v05-counterexample:after-retire")
        self.assertFalse(trial["ok"], trial)
        self.assertEqual(trial.get("reason_code"), "PRACTICE_METHOD_RETIRED", trial)

        revised = self.service.revise_method(
            self.method_id, instruction="退休后再改", expected_head=self.pre_head,
            operation_key="practice:v05-counterexample:revise-after-retire")
        self.assertFalse(revised["ok"], revised)
        self.assertEqual(revised.get("reason_code"), "PRACTICE_METHOD_RETIRED", revised)

        stored = self.stored()
        self.assertEqual(stored["method_state"], pps.METHOD_STATE_RETIRED)
        self.assertEqual(stored["instruction"], INSTRUCTION)
        self.assertTrue(str(stored.get("body") or "").strip())
        self.assertEqual(len(stored["counterexamples"]), 1)

    def test_a_retired_method_is_withheld_from_the_round_offer(self):
        context = self.seam.decision_context(query="")
        self.assertEqual(context["methods"], [])
        self.assertEqual(context["method_refs"], [])
        withheld = context.get("withheld_methods") or {}
        self.assertGreaterEqual(int(withheld.get("count") or 0), 1)
        self.assertIn("PRACTICE_METHOD_RETIRED", withheld.get("reason_codes") or [])

    def test_ag1_still_rejects_a_ref_outside_the_offer(self):
        # 形状不合法的 ref 在 AG-1 自己的校验里就被拒，退役与否都不会被它放行。
        with self.assertRaises(ag1.DecisionValidationError) as caught:
            ag1.validate_method_trial(
                dict(_trial(self.method_ref), method_ref="not-a-handle"),
                method_ref=self.method_ref)
        self.assertEqual(caught.exception.failure_class, ag1.FAILURE_CLASS_INVENTED_METHOD_REF)


@POSIX_ONLY
class IdempotenceAndBoundedViewTest(_Base):
    """重复操作键不多写；投影有界，head 变了不混读。"""

    def test_the_same_trial_operation_is_not_written_twice(self):
        key = "practice:v05-counterexample:idempotent"
        first = self.record("HELPED", key=key)
        self.assertTrue(first["ok"], first)
        second = self.record("HELPED", key=key, head=self.head())
        self.assertTrue(second["ok"], second)
        stored = self.stored()
        self.assertEqual(len(stored["trials"]), 1,
                         "同一个操作键把同一条评价写了两遍")

    def test_the_round_view_is_bounded_and_head_safe(self):
        self.assertTrue(self.record("HELPED", key="practice:v05-counterexample:view")["ok"])
        rows = self.seam.methods(query="")
        self.assertEqual(len(rows), 1, rows)
        evaluation = self.seam.evaluations_for(rows)[self.method_id]
        self.assertTrue(evaluation["available"], evaluation)
        self.assertEqual(evaluation["outcome"], "HELPED")
        # 评价是对记录时读到的版本：记录前 head 就是本轮 offer 的 head。
        self.assertEqual(evaluation["version_id"], self.offered_head)
        self.assertEqual(int(evaluation["evidence_count"]), 1)
        self.assertNotIn("detail", evaluation)
        self.assertNotIn("这次先写结论顺一些", str(evaluation))

        # 另一个写者把 head 移走后，旧行不能再拼出一份“当前”评价。
        current = self.stored()
        revised = self.service.revise_method(
            self.method_id, instruction="另一版做法", expected_head=current["head_version_id"],
            operation_key="practice:v05-counterexample:view-revise")
        self.assertTrue(revised["ok"], revised)
        stale = self.seam.evaluations_for(rows)[self.method_id]
        self.assertFalse(stale["available"], stale)
        self.assertEqual(stale["reason_code"], lpc.REASON_DETAIL_HEAD_CHANGED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

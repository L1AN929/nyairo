#!/usr/bin/env python3
"""v0.5 第一块：有界的 owner 评价投影与同轮表达来源。

只测这一块新增的两条读视图，**不**新增持久使用记录、不改 owner 判据、不改 AG-1 规则：

1. 方法列表已有的 ``trial_count`` / ``counterexample_count`` 进入最终 practice 白名单；
   真实整数照传，缺值保持未知（``None``），绝不补 0 冒充"没有评价"。
2. 对本轮真正提供给 Decision 的最多 3 条方法，经既有治理读口 ``read_method`` 取最近一条
   显式 trial —— 只留 outcome / 受评版本 / 证据条数，不带 detail 正文；列表 head 与详情
   head 不一致不拼接；读失败保留列表并具名；治理扣留时不从任何旁路恢复。
3. 分享来源的 id/title 只复用本轮已实际读取且确认同一 artifact 的数据；缺标题/身份不符
   就省略，且不新增任何 owner 读取。``truncated`` 真值经 adapter 白名单透传，但整块仍按
   既有预算裁剪。
4. 分享列表的两层截断各自如实上报：owner 的 ``list_projects.truncated`` 不被分享读层丢掉
   （101 条真实草稿 → 列表返回 100 行且 ``truncated=True``，``counts.total`` 仍只认当前
   返回行）；prompt 自己按 ``MAX_PROMPT_SHARES`` 截掉的 OPEN 草稿（6 条 → 展示 5 条）使
   ``prompt_block.truncated=True``，而不是把 5 条冒充成全部。

真实实现：``PersonalPracticeService`` + 内存 owner 替身端口（复用阶段 B 的
``FakePort`` / ``FakeMemory``）。全链（正式 assemble → evaluate_once → 原始 JSON → 真实
Supply/M0 owner）在 ``scripts/v05_results_expression_smoke.py``；本文件不宣称真实模型采用。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                 "life cycle imports fcntl; run on POSIX/WSL")

_DONOR_ERROR: str | None = "POSIX only"
donor = None

if os.name == "posix":  # pragma: no branch - Windows skips every class below
    import agency_cognition_adapter as aa  # noqa: E402
    import autonomous_life_cycle as alc  # noqa: E402
    import life_practice_context as lpc  # noqa: E402
    import life_sharing_context as lsc  # noqa: E402
    import life_sharing_service as lss  # noqa: E402
    import one_day_life as odl  # noqa: E402
    import personal_practice_service as pps  # noqa: E402
    from test_v04_personal_practice_stage import FakeMemory, seeded_port, service_for  # noqa: E402

    try:
        import test_life_v1_integration as donor  # noqa: E402
        _DONOR_ERROR = None
    except Exception as error:  # noqa: BLE001 - a missing fixture skips only the real-owner class
        _DONOR_ERROR = "%s: %s" % (type(error).__name__, error)
else:  # pragma: no cover
    aa = alc = lpc = lsc = lss = odl = pps = None
    FakeMemory = seeded_port = service_for = None

EVIDENCE_ID = "exp:evidence"
EVIDENCE_TEXT = "先写结论那一页就写完了。"
METHOD_INSTRUCTION = "先写一句结论，再补三段理由"
METHOD_SCOPE = "只在这个长文项目里"
METHOD_UNCERTAINTY = "不知道短文本是否也适用"


class _CountingService:
    """Delegate to the real service; count detail reads and every write."""

    WRITES = ("create_review", "create_method", "record_trial", "revise_method", "retire_method")

    def __init__(self, service: Any) -> None:
        self._service = service
        self.read_method_calls: list[str] = []
        self.write_calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._service, name)
        if name == "read_method":
            def counted(artifact_id: str) -> Any:
                self.read_method_calls.append(str(artifact_id))
                return attribute(artifact_id)
            return counted
        if name in self.WRITES:
            def counted(*args: Any, **kwargs: Any) -> Any:
                self.write_calls.append(name)
                return attribute(*args, **kwargs)
            return counted
        return attribute


class _DetailFailingService(_CountingService):
    """The real service, except one method's detail read raises (a failing owner answer)."""

    def __init__(self, service: Any, *, fail_id: str) -> None:
        super().__init__(service)
        self._fail_id = str(fail_id)

    def __getattr__(self, name: str) -> Any:
        if name == "read_method":
            def maybe_fail(artifact_id: str) -> Any:
                self.read_method_calls.append(str(artifact_id))
                if str(artifact_id) == self._fail_id:
                    raise RuntimeError("owner detail read failed")
                return self._service.read_method(artifact_id)
            return maybe_fail
        return super().__getattr__(name)


class _ExplodingPort:
    """Any owner read through the artifact port is a test failure."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError("shareable_sources must not read the owner (%s)" % name)


def _make_method(service: Any, memory: Any, *, key: str = "one") -> str:
    created = service.create_method(
        title="先写结论", instruction=METHOD_INSTRUCTION, scope=METHOD_SCOPE,
        uncertainty=METHOD_UNCERTAINTY, experience_ids=[EVIDENCE_ID],
        operation_key="practice:method:v05-%s" % key)
    assert created["ok"], created
    return str(created["data"]["artifact_id"])


def _new_cycle(seam: Any) -> Any:
    cycle = alc.AutonomousLifeCycle.__new__(alc.AutonomousLifeCycle)
    cycle.practice = seam
    cycle._round_methods = []
    cycle._round_practice_context = None
    cycle._round_practice_evaluations = {}
    cycle._round_practice_evaluated = False
    cycle._round_work_titles = {}
    cycle._round_work_title = ""
    cycle._round_project_options = []
    cycle.last_practice_error = None
    return cycle


@POSIX_ONLY
class BoundedEvaluationProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.memory = FakeMemory()
        self.memory.add(EVIDENCE_ID, EVIDENCE_TEXT)
        self.service = service_for(seeded_port(), self.memory)
        self.artifact_id = _make_method(self.service, self.memory)
        self.seam = lpc.PracticeSeam(self.service, memory=self.memory)

    def _head(self) -> str:
        return str(self.service.read_method(self.artifact_id)["data"]["head_version_id"])

    def _record(self, outcome: str, *, detail: str) -> dict:
        answer = self.service.record_trial(
            self.artifact_id, outcome=outcome, detail=detail,
            experience_ids=[EVIDENCE_ID], expected_head=self._head(),
            operation_key="practice:trial:v05-%s" % outcome.lower())
        self.assertTrue(answer["ok"], answer)
        return answer

    def test_counts_and_the_most_recent_explicit_trial_reach_the_decision_view(self):
        self._record("HELPED", detail="这次先写结论顺一些")
        # The version a trial was *about* is the head it was recorded against, i.e. the head
        # this round offered -- not the new head its own write produced.
        counter_head = self._head()
        self._record("NOT_HELPED", detail="这次反而卡在结论上")

        rows = self.seam.methods(query="")
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["trial_count"], 2)
        self.assertEqual(rows[0]["counterexample_count"], 1)

        found = self.seam.evaluations_for(rows)
        self.assertEqual(self.seam.last_detail_reads, 1)
        view = found[self.artifact_id]
        self.assertTrue(view["available"], view)
        self.assertEqual(view["outcome"], "NOT_HELPED")
        self.assertEqual(view["version_id"], counter_head)
        self.assertEqual(view["evidence_count"], 1)
        self.assertNotIn("detail", view, "the trial's own free text must not travel")

        # The list projection carries both real counts; the enrichment is attached to the same row.
        context = self.seam.decision_context(query="")
        row = [item for item in context["methods"]
               if item["artifact_id"] == self.artifact_id][0]
        self.assertEqual(row["trial_count"], 2)
        self.assertEqual(row["counterexample_count"], 1)

    def test_a_method_without_a_trial_says_so_instead_of_inventing_one(self):
        rows = self.seam.methods(query="")
        found = self.seam.evaluations_for(rows)
        view = found[self.artifact_id]
        self.assertFalse(view["available"])
        self.assertEqual(view["reason_code"], lpc.REASON_NO_EXPLICIT_TRIAL)
        self.assertNotIn("outcome", view)
        self.assertNotIn("version_id", view)

    def test_a_head_that_moved_between_list_and_detail_is_never_stitched_together(self):
        self._record("NOT_HELPED", detail="反例一")
        rows = self.seam.methods(query="")
        offered_head = rows[0]["head_version_id"]
        revised = self.service.revise_method(
            self.artifact_id, instruction="改过的做法", expected_head=offered_head,
            operation_key="practice:revise:v05-head")
        self.assertTrue(revised["ok"], revised)

        found = self.seam.evaluations_for(rows)
        view = found[self.artifact_id]
        self.assertFalse(view["available"])
        self.assertEqual(view["reason_code"], lpc.REASON_DETAIL_HEAD_CHANGED)
        self.assertNotIn("outcome", view, "two heads were mixed into one evaluation snapshot")
        # Exactly one read: it did not keep re-reading until the versions agreed.
        self.assertEqual(self.seam.last_detail_reads, 1)
        fresh = self.seam.methods(query="")
        current_head = self.service.read_method(self.artifact_id)["data"]["head_version_id"]
        self.assertEqual(fresh[0]["head_version_id"], current_head)

    def test_a_failed_detail_read_keeps_the_readable_list_and_names_the_gap(self):
        counting = _DetailFailingService(self.service, fail_id=self.artifact_id)
        seam = lpc.PracticeSeam(counting, memory=self.memory)
        rows = seam.methods(query="")
        self.assertEqual(len(rows), 1, rows)

        found = seam.evaluations_for(rows)
        view = found[self.artifact_id]
        self.assertFalse(view["available"])
        self.assertEqual(view["reason_code"], lpc.REASON_DETAIL_UNAVAILABLE)
        self.assertEqual(len(rows), 1, "the readable list row must survive a detail failure")
        self.assertTrue(rows[0]["available"])

    def test_withheld_evidence_withholds_the_verdict_and_keeps_the_offer_contract(self):
        self._record("NOT_HELPED", detail="反例二")
        self.memory.withdrawn.add(EVIDENCE_ID)

        rows = self.seam.methods(query="", available_only=True)
        self.assertEqual(len(rows), 1, rows)
        self.assertTrue(rows[0]["available"], "withdrawn evidence is not a retirement")
        found = self.seam.evaluations_for(rows)
        view = found[self.artifact_id]
        self.assertFalse(view["available"])
        self.assertEqual(view["reason_code"], lpc.REASON_EVIDENCE_WITHHELD)
        self.assertNotIn("outcome", view)
        self.assertNotIn("version_id", view)
        self.assertNotIn("evidence_count", view)
        self.assertEqual(rows[0]["instruction"], "",
                         "the owner's withholding still means no prose in the offer")

    def test_retirement_removes_the_offer_and_names_no_verdict(self):
        head = self._head()
        retired = self.service.retire_method(self.artifact_id, expected_head=head,
                                             operation_key="practice:retire:v05")
        self.assertTrue(retired["ok"], retired)
        rows = self.seam.methods(query="", available_only=True)
        self.assertEqual(rows, [])
        self.assertEqual(len(self.seam.last_withheld_methods), 1)
        withheld = self.seam.last_withheld_methods[0]
        self.assertEqual(withheld["loss_reason"], lpc.LOSS_METHOD_RETIRED)
        self.assertEqual(withheld["method_state"], pps.METHOD_STATE_RETIRED)
        self.assertFalse(withheld["available"])
        self.assertEqual(self.seam.decision_context(query="")["method_refs"], [])

    def test_the_round_reads_at_most_three_details_and_never_the_whole_workspace(self):
        for index in range(4):
            _make_method(self.service, self.memory, key="extra-%d" % index)
        seam = lpc.PracticeSeam(self.service, memory=self.memory, max_methods=5)
        rows = seam.methods(query="", limit=5)
        self.assertEqual(len(rows), 5, rows)

        found = seam.evaluations_for(rows)
        self.assertEqual(seam.last_detail_reads, lpc.MAX_DETAIL_READS)
        self.assertEqual(len(found), lpc.MAX_DETAIL_READS)
        self.assertEqual(len({row["artifact_id"] for row in rows[:3]}), 3)

    def test_the_rounds_repeated_context_calls_do_not_repeat_detail_reads(self):
        counting = _CountingService(self.service)
        cycle = _new_cycle(lpc.PracticeSeam(counting, memory=self.memory))

        first = cycle.practice_decision_context()
        second = cycle.practice_decision_context()
        self.assertEqual(len(counting.read_method_calls), 1, counting.read_method_calls)
        rows = [row for row in first["methods"] if row["artifact_id"] == self.artifact_id]
        self.assertTrue(rows, first)
        self.assertEqual(rows[0]["evaluation"]["reason_code"], lpc.REASON_NO_EXPLICIT_TRIAL)
        self.assertEqual([row["artifact_id"] for row in second["methods"]],
                         [row["artifact_id"] for row in first["methods"]])

    def test_reassembly_reads_the_same_owner_facts_and_writes_nothing(self):
        self._record("HELPED", detail="这次顺一些")
        self._record("NOT_HELPED", detail="这次卡住")
        counting = _CountingService(self.service)
        cycle = _new_cycle(lpc.PracticeSeam(counting, memory=self.memory))
        view = cycle.practice_decision_context()
        row = [item for item in view["methods"] if item["artifact_id"] == self.artifact_id][0]
        self.assertEqual(row["evaluation"]["outcome"], "NOT_HELPED")
        self.assertEqual(counting.write_calls, [], "the read view wrote to the owner")

        # A brand-new seam over the same owner (a re-assembly, not a cached round) answers the
        # same facts -- without ``last_method_usage`` or any in-process history.
        rebuilt = lpc.PracticeSeam(self.service, memory=self.memory)
        rows = rebuilt.methods(query="")
        found = rebuilt.evaluations_for(rows)[self.artifact_id]
        self.assertEqual(found["outcome"], row["evaluation"]["outcome"])
        self.assertEqual(found["version_id"], row["evaluation"]["version_id"])
        self.assertEqual(found["evidence_count"], row["evaluation"]["evidence_count"])
        self.assertEqual(counting.write_calls, [], "the rebuilt view wrote to the owner")


@POSIX_ONLY
class DecisionPayloadWhitelistTest(unittest.TestCase):
    def _practice_block(self, *rows: Mapping[str, Any]) -> dict:
        bounded = aa.ProductionAgencyCognitionAdapter._bounded_situation(
            {"practice": {"methods": list(rows), "experiences": []}})
        return bounded.get("practice") or {}

    def test_real_counts_and_the_bounded_verdict_reach_the_final_practice_whitelist(self):
        block = self._practice_block({
            "method_ref": "method:0123456789abcdef",
            "title": "先写结论", "method_state": "CONTESTED",
            "head_version_id": "artifact-version:abc", "trial_count": 2,
            "counterexample_count": 1,
            "evaluation": {"available": True, "outcome": "NOT_HELPED",
                           "version_id": "artifact-version:abc", "evidence_count": 1,
                           "detail": "这次卡住的原因"},
        })
        method = block["methods"][0]
        self.assertEqual(method["trial_count"], 2)
        self.assertEqual(method["counterexample_count"], 1)
        self.assertEqual(set(method["evaluation"]),
                         {"available", "outcome", "version_id", "evidence_count"})
        self.assertNotIn("detail", method["evaluation"])

    def test_a_missing_count_stays_unknown_and_is_never_zero(self):
        block = self._practice_block({"method_ref": "method:0123456789abcdef",
                                      "head_version_id": "artifact-version:abc"})
        method = block["methods"][0]
        self.assertIn("trial_count", method)
        self.assertIsNone(method["trial_count"])
        self.assertIsNone(method["counterexample_count"])
        self.assertNotEqual(method["trial_count"], 0)

    def test_an_unavailable_verdict_keeps_only_its_named_reason(self):
        block = self._practice_block({
            "method_ref": "method:0123456789abcdef",
            "evaluation": {"available": False,
                           "reason_code": "PRACTICE_METHOD_HEAD_CHANGED",
                           "outcome": "NOT_HELPED", "version_id": "artifact-version:old"},
        })
        evaluation = block["methods"][0]["evaluation"]
        self.assertFalse(evaluation["available"])
        self.assertEqual(evaluation["reason_code"], "PRACTICE_METHOD_HEAD_CHANGED")
        self.assertNotIn("outcome", evaluation)
        self.assertNotIn("version_id", evaluation)


@POSIX_ONLY
class SharingSourceAndTruncationTest(unittest.TestCase):
    def test_a_verified_title_travels_with_its_source_and_an_unmatched_one_does_not(self):
        bounded = aa._bounded_sharing({
            "status": "observed",
            "sources": [
                {"artifact_id": "artifact:work", "kind": "current_work", "title": "第一版正文"},
                {"artifact_id": "artifact:read", "kind": "reading_material"},
            ],
            "truncated": True,
        })
        self.assertEqual(bounded["sources"][0]["title"], "第一版正文")
        self.assertNotIn("title", bounded["sources"][1])
        self.assertIs(bounded["truncated"], True)

    def test_an_owner_list_that_was_not_cut_claims_nothing(self):
        bounded = aa._bounded_sharing({"status": "observed", "sources": [],
                                       "truncated": False})
        self.assertIs(bounded["truncated"], False)
        unknown = aa._bounded_sharing({"status": "observed", "sources": []})
        self.assertNotIn("truncated", unknown)

    def test_the_budget_still_drops_the_whole_sharing_block(self):
        adapter = aa.ProductionAgencyCognitionAdapter(client=object(), max_prompt_chars=400)
        text, working = adapter._fit_prompt({
            "situation_context": {
                "sharing": {"shares": [{"title": "x" * 4000}], "truncated": True},
                "world": {"place": "书房"},
                "body": {"capacity": "UNKNOWN"},
            },
            "candidates": [{"candidate_id": "c1", "source_kind": "PERSONAL_OPPORTUNITY",
                            "source_ref": "artifact:one", "legal_decisions": ["START"],
                            "target_refs": ["artifact:one"]}],
        })
        situation = working["situation_context"]
        self.assertNotIn("sharing", situation)
        self.assertEqual(situation["world"], {"place": "书房"})
        self.assertTrue(text)


@POSIX_ONLY
class ShareableSourceIdentityTest(unittest.TestCase):
    def _cycle(self) -> Any:
        cycle = alc.AutonomousLifeCycle.__new__(alc.AutonomousLifeCycle)
        cycle.work_artifact_id = "work1234"
        cycle.reading_artifact_id = "read5678"
        cycle._round_work_titles = {"artifact:work1234": "第一版正文"}
        cycle._reading_situation_cache = {
            "reading_artifact_id": "read5678",
            "material": {"title": "田野笔记"},
        }
        cycle.artifact_port = _ExplodingPort()
        return cycle

    def test_titles_are_paired_only_with_the_artifact_that_was_read(self):
        sources = self._cycle().shareable_sources()
        by_kind = {row["kind"]: row for row in sources}
        self.assertEqual(by_kind["current_work"]["artifact_id"], "artifact:work1234")
        self.assertEqual(by_kind["current_work"]["title"], "第一版正文")
        self.assertEqual(by_kind["reading_material"]["artifact_id"], "artifact:read5678")
        self.assertEqual(by_kind["reading_material"]["title"], "田野笔记")
        self.assertEqual(len(sources), 2)

    def test_a_mismatched_identity_or_a_missing_read_omits_the_title(self):
        cycle = self._cycle()
        cycle._round_work_titles = {"artifact:someone-else": "别人的标题"}
        cycle._reading_situation_cache = {"reading_artifact_id": "read9999",
                                          "material": {"title": "另一份材料"}}
        sources = cycle.shareable_sources()
        self.assertTrue(all("title" not in row for row in sources), sources)

        cycle._reading_situation_cache = None
        sources = cycle.shareable_sources()
        reading = [row for row in sources if row["kind"] == "reading_material"][0]
        self.assertNotIn("title", reading)


class _NoFlagShareOwner:
    """One envelope-shaped share row; its list answer has no ``truncated`` key at all.

    That is the old owner shape.  The sharing read layer must keep saying ``False`` (nothing was
    reported as cut) instead of inventing a truncation flag, and must not write while reading.
    """

    subject_id = "test:owner"
    workspace_id = "ws:test"

    def __init__(self) -> None:
        share = {
            "state": lss.STATE_DRAFT,
            "source_artifact_id": "artifact:source",
            "source_version_id": "artifact-version:v1",
            "source_revision": 1,
            "source_content_chars": 10,
            "source_title_chars": 4,
            "audience": lss.AUDIENCE_LOCAL_OWNER,
            "channel": lss.CHANNEL_LOCAL_INBOX,
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "share_revision": 1,
            "created_at": "2026-10-25T00:00:00+00:00",
            "updated_at": "2026-10-25T00:00:00+00:00",
            "excerpt_kind": "EXPLICIT_TEXT",
        }
        self.content = lss.encode_share_content(share, "草稿正文")
        self.writes = 0

    def list_projects(self) -> dict:
        return {"ok": True, "status": "ok", "projects": [
            {"artifact_id": "artifact:testshare", "title": "[share] 测试",
             "lifecycle": "ACTIVE"}]}

    def read_project(self, artifact_id: str, **kwargs) -> dict:
        if str(artifact_id) != "artifact:testshare":
            return {"ok": False, "status": "not_found", "reason_code": "NOT_FOUND"}
        return {"ok": True, "status": "ok", "project": {
            "artifact_id": "artifact:testshare", "title": "[share] 测试",
            "content": self.content, "head_version_id": "artifact-version:h1",
            "revision": 1, "version_number": 1, "lifecycle": "ACTIVE"}}

    def create_project(self, **kwargs) -> dict:
        self.writes += 1
        raise AssertionError("the read path must not create anything")

    def commit_project(self, **kwargs) -> dict:
        self.writes += 1
        raise AssertionError("the read path must not commit anything")


@POSIX_ONLY
class ShareListTruncationProjectionTest(unittest.TestCase):
    def test_an_owner_answer_without_the_flag_invents_no_truncation(self):
        owner = _NoFlagShareOwner()
        service = lss.LifeSharingService(owner)
        listing = service.list_shares(include_text=False)
        self.assertTrue(listing["ok"], listing)
        self.assertEqual(len(listing["data"]["shares"]), 1)
        self.assertIs(listing["data"]["truncated"], False)
        self.assertEqual(listing["data"]["counts"]["total"], 1)
        self.assertEqual(owner.writes, 0)


@POSIX_ONLY
@unittest.skipIf(donor is None, "donor fixture unavailable: %s" % (_DONOR_ERROR or ""))
class RealOwnerShareTruncationTest(unittest.TestCase):
    """真实 Supply owner：列表自己截断与 prompt bound 截断是两件不同的事实。"""

    def setUp(self) -> None:
        self.base = donor.DScenarioBase("runTest")
        self.base.setUp()
        self.addCleanup(self._close)
        self.prepared = self.base.prepare_scenario(keep_fixture_world=True)
        self.data = self.prepared.data
        env = odl.instance_environment(self.base.instance)
        env.update({
            "NYAIRO_LIFE_SUPPLY_SOCKET": str(self.base.services.supply_path),
            "NYAIRO_LIFE_SUBJECT": str(self.data["subject_id"]),
            "NYAIRO_LIFE_WORKSPACE_ID": str(self.data["workspace_id"]),
            "NYAIRO_ARTIFACT_GRANT": str(self.data["artifact_grant_id"]),
        })
        self.service = lss.sharing_from_env(env)
        self.source_id = str(self.data.get("reading_artifact_id")
                             or self.data.get("work_artifact_id") or "")
        self.assertNotEqual(self.source_id, "", "the instance must have a real source artifact")
        self.share_ids: list[str] = []

    def _close(self) -> None:
        try:
            self.base.doCleanups()
        except Exception:  # noqa: BLE001 - cleanup of a fixture is not a test result
            pass

    def _draft(self, index: int) -> str:
        answer = self.service.prepare_share(
            source_artifact_id=self.source_id,
            share_text="第%d条草稿正文，还留在这里。" % index,
            title="截断观察%d" % index,
            operation_key="share:op:v05-truncation:%d" % index)
        self.assertTrue(answer.get("ok"), answer)
        self.assertEqual(answer.get("state"), lss.STATE_DRAFT, answer)
        share_id = str(answer["share_id"])
        self.share_ids.append(share_id)
        return share_id

    def _heads(self) -> dict:
        return {share_id: lsc.read_draft(self.service, share_id)["head_version_id"]
                for share_id in self.share_ids}

    def test_six_open_drafts_make_the_prompt_bound_truncated_not_the_owner_list(self):
        for index in range(lsc.MAX_PROMPT_SHARES + 1):
            self._draft(index)
        before = self._heads()

        owner_list = self.service.list_shares(include_text=False)
        self.assertTrue(owner_list["ok"], owner_list)
        self.assertIs(owner_list["data"]["truncated"], False,
                      "six rows are not the owner's own cut")
        self.assertEqual(owner_list["data"]["counts"]["total"], 6)

        block = lsc.prompt_block(self.service)
        self.assertEqual(len(block["shares"]), lsc.MAX_PROMPT_SHARES)
        self.assertIs(block["truncated"], True,
                      "the prompt bound hid one OPEN draft; five rows are not the whole list")
        self.assertEqual(block["counts"]["total"], 6)
        shown = [row["share_id"] for row in block["shares"]]
        self.assertEqual(len(shown), lsc.MAX_PROMPT_SHARES)
        self.assertTrue(set(shown) <= set(self.share_ids), shown)

        # The read was read-only: every draft is where it was, and nothing was dropped or sent.
        self.assertEqual(self._heads(), before)
        after = self.service.list_shares(include_text=False)
        self.assertEqual(after["data"]["counts"]["total"], 6)
        self.assertTrue(all(row["state"] == lss.STATE_DRAFT for row in after["data"]["shares"]))
        self.assertEqual(after["data"]["inbox"]["delivered_count"], 0)

    def test_the_owner_list_cut_reaches_the_sharing_list_and_the_hidden_draft_survives(self):
        for index in range(lss.MAX_LIST + 1):
            self._draft(index)
        listing = self.service.list_shares(include_text=False)
        self.assertTrue(listing["ok"], listing)
        self.assertEqual(len(listing["data"]["shares"]), lss.MAX_LIST)
        self.assertIs(listing["data"]["truncated"], True,
                      "the owner's own truncated answer must not be dropped by this layer")
        # The count is still "the rows this answer returned", not a made-up exact total.
        self.assertEqual(listing["data"]["counts"]["total"], lss.MAX_LIST)
        visible = {row["share_id"] for row in listing["data"]["shares"]}
        hidden = [share_id for share_id in self.share_ids if share_id not in visible]
        self.assertEqual(len(hidden), 1, {"visible": len(visible), "drafts": len(self.share_ids)})
        hidden_view = lsc.read_draft(self.service, hidden[0])
        self.assertEqual(hidden_view["state"], lss.STATE_DRAFT)
        self.assertIsNone(hidden_view["receipt"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

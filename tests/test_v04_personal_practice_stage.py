#!/usr/bin/env python3
"""v0.4 阶段 B：个人实践工作台（回顾 / 做法）的 owner 服务。

这个文件测什么
--------------
``components/life/scripts/personal_practice_service.py``：一件实践文档就是既有的
``ManagedArtifact``（经 ``PrivateProjectService``），第一行是 B 唯一的公开标记，经历只通过
A 的 Memory 接口读。覆盖：

* 标记：只认第一行、正文原样保留、元数据挤不掉正文、非法标记照实报错；
* 回顾：引用真实经历才建得起来；读回来带每条经历**现在**的可读性；
* 做法：新建即 TENTATIVE；HELPED → TRIAL、NOT_HELPED → CONTESTED、UNKNOWN 不变；
  没有可读证据只能 UNKNOWN；修订回 TENTATIVE 且反例不抹；退休是逻辑状态不删文档；
* 治理传播：经历一旦读不回来，回顾正文与依赖它的做法正文都不再透出（递归）；
* 工厂：``practice_from_env`` 缺一件 env 就返回 ``None``。

证据分级（照实写）
------------------
* ``FakePort``（复用 ``tests/test_v04_private_projects_stage.py`` 的内存 owner 替身）与
  ``FakeMemory``（按 A 的 ``LifeMemoryService`` **答复形状**冻结的替身）测的是本模块自己的契约。
* ``RealMemoryContractTests`` 用的是 **A 真实的 ``LifeMemoryService``**（未绑定状态），证明真的
  接缝不是只对着替身绿的。
* ``RealOwnerAndMemoryIntegrationTests`` 用真实 Life Supply owner（AF_UNIX）+ 真实 M0 EvidenceStore：
  **只在 POSIX 跑**，原生 Windows 会显式 skip；跑过的证据写在 note 与结果文档里。
* 夹具根唯一创建并保留（不删除、不清理、不用 TemporaryDirectory）。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import personal_practice_service as pps  # noqa: E402
from test_v04_private_projects_stage import (  # noqa: E402
    ITEMS,
    READING,
    WORKSPACE,
    GRANT,
    FakePort,
)

SUBJECT = "chiyo"
#: Supply 的 subject 与 Memory 的 persona 不是一回事：集成测试里故意用两个不同的名字。
SUPPLY_SUBJECT = "practice:integration-subject"
POSIX_ONLY = unittest.skipIf(os.name == "nt",
                             "真实 Supply socket 与真实 M0 EvidenceStore 只在 POSIX 可用")


def fixture_base() -> Path:
    override = os.environ.get("PI_SCRATCH_DIR") or os.environ.get("TEMP")
    base = Path(override or "/tmp") / "nyairo-practice-b"
    base.mkdir(parents=True, exist_ok=True)
    return base


def unique_root(prefix: str) -> Path:
    root = fixture_base() / ("%s%d-%d" % (prefix, os.getpid(), time.time_ns()))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


class FakeMemory:
    """One A-shaped Memory answer set (``ok/status/reason/detail/data``), nothing more."""

    def __init__(self, owner: str = SUBJECT) -> None:
        self.owner = owner
        self.rows: dict[str, dict[str, Any]] = {}
        self.withdrawn: set[str] = set()
        self.calls: list[tuple[str, Any]] = []

    def add(self, experience_id: str, text: str, *, occurred_at: str = "2026-11-01T08:00:00+00:00",
            outcome_kind: str = "VERIFIED_RESULT", owner: Optional[str] = None) -> str:
        self.rows[experience_id] = {
            "experience_id": experience_id,
            "event_id": "event-%s" % experience_id.split(":")[-1],
            "occurred_at": occurred_at,
            "text": text,
            "source_ref": "life:test",
            "activity_ref": "activity:test",
            "artifact_ref": "artifact:test",
            "version_ref": "version:test",
            "owner_result": "ok",
            "outcome_kind": outcome_kind,
            "owner": owner or self.owner,
        }
        return experience_id

    # -- A's seam ----------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        self.calls.append(("status", None))
        return {"ok": True, "status": "READY", "reason": None, "detail": None,
                "data": {"binding_status": "READY", "recall_ready": True,
                         "owner": self.owner, "participation": "BOUND_READY_READONLY_VERIFIED"}}

    def list_experiences(self, *, query: str = "", limit: int = 20, since=None) -> dict[str, Any]:
        self.calls.append(("list_experiences", query))
        rows = [dict(row) for row in self.rows.values()
                if row["experience_id"] not in self.withdrawn]
        return {"ok": True, "status": "READY", "reason": None, "detail": None,
                "data": {"experiences": rows[:limit], "count": len(rows[:limit]),
                         "binding_status": "READY", "owner": self.owner}}

    def read_experience(self, experience_id: str) -> dict[str, Any]:
        self.calls.append(("read_experience", experience_id))
        if experience_id in self.withdrawn:
            return {"ok": False, "status": "NOT_FOUND",
                    "reason": "MEMORY_EXPERIENCE_NOT_RECALLABLE",
                    "detail": "corrected away or deleted",
                    "data": {"binding_status": "READY", "experience": None,
                             "experience_id": experience_id}}
        row = self.rows.get(experience_id)
        if row is None:
            return {"ok": False, "status": "NOT_FOUND",
                    "reason": "MEMORY_EXPERIENCE_NOT_RECALLABLE", "detail": "",
                    "data": {"binding_status": "READY", "experience": None,
                             "experience_id": experience_id}}
        return {"ok": True, "status": "READY", "reason": None, "detail": None,
                "data": {"binding_status": "READY", "owner": self.owner,
                         "experience": dict(row), "experience_id": experience_id}}

    def context(self, *, query: str = "", limit: int = 5) -> dict[str, Any]:
        answer = self.list_experiences(query=query, limit=limit)
        return answer


def service_for(port: Any, memory: Any = None) -> pps.PersonalPracticeService:
    projects = pps.PrivateProjectService(port, WORKSPACE, GRANT,
                                         excluded_artifact_ids=(READING, ITEMS))
    return pps.PersonalPracticeService(projects, memory_service=memory)


def seeded_port() -> FakePort:
    port = FakePort()
    port.seed(artifact_id=READING, title="她的阅读材料", content="第一页\n%%PAGE%%\n第二页")
    port.seed(artifact_id=ITEMS, title="她的私人日常事项", content="{\"items\":{}}")
    return port


class MarkerTests(unittest.TestCase):
    """第一行标记：全树唯一，正文永远原样。"""

    def test_marker_is_the_first_line_and_the_body_survives_verbatim(self) -> None:
        body = "## 我当时看到的\n第一行正文\n\n<!-- 不像标记的一行 -->\n最后一行"
        content = pps.build_practice_content(
            {"kind": pps.KIND_REVIEW, "title": "回顾一", "question": "为什么？",
             "experience_ids": ["exp:1"], "experience_basis": pps.BASIS_EXPERIENCE}, body)
        first_line = content.split("\n", 1)[0]
        self.assertTrue(first_line.startswith("<!-- nyairo-practice:v1 "))
        parsed = pps.parse_practice_content(content)
        self.assertEqual(parsed["kind"], pps.KIND_REVIEW)
        self.assertTrue(parsed["marker_present"])
        self.assertEqual(parsed["parse_error"], "")
        self.assertEqual(parsed["question"], "为什么？")
        self.assertEqual(parsed["experience_ids"], ["exp:1"])
        # 元数据没有挤掉正文：标记行之后的正文逐字相同。
        self.assertEqual(parsed["body"], body)
        self.assertTrue(pps.is_practice_content(content))

    def test_a_plain_document_is_not_a_practice_document(self) -> None:
        parsed = pps.parse_practice_content("普通的一页，第一行不是标记。")
        self.assertEqual(parsed["kind"], "")
        self.assertFalse(parsed["marker_present"])
        self.assertFalse(pps.is_practice_content("普通的一页"))
        self.assertEqual(pps.practice_kind("普通的一页"), "")

    def test_a_later_marker_line_is_just_text(self) -> None:
        content = "正文第一行\n<!-- nyairo-practice:v1 {\"kind\":\"review\"} -->"
        self.assertFalse(pps.is_practice_content(content))

    def test_broken_marker_is_named_not_guessed(self) -> None:
        broken = "<!-- nyairo-practice:v1 {not json} -->\n\n正文"
        parsed = pps.parse_practice_content(broken)
        self.assertEqual(parsed["parse_error"], "MARKER_JSON_INVALID")
        self.assertFalse(pps.is_practice_content(broken))
        unknown = pps.build_practice_content({"kind": "review"}, "")
        other = unknown.replace("\"kind\":\"review\"", "\"kind\":\"opinion\"")
        self.assertTrue(pps.parse_practice_content(other)["parse_error"]
                        .startswith("MARKER_KIND_UNKNOWN"))


class ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.port = seeded_port()
        self.memory = FakeMemory()
        self.exp_one = self.memory.add("exp:1", "我用先写三行再展开的办法写完了那一页。")
        self.service = service_for(self.port, self.memory)

    def test_create_review_cites_a_real_experience_and_reads_it_back(self) -> None:
        created = self.service.create_review(question="怎样把一页写完？", title="写完一页的回顾",
                                             experience_ids=[self.exp_one],
                                             operation_key="practice:review:1")
        self.assertTrue(created["ok"], created)
        self.assertEqual(created["status"], "applied")
        artifact_id = created["data"]["artifact_id"]
        self.assertTrue(artifact_id)
        self.assertEqual(created["data"]["experience_basis"], pps.BASIS_EXPERIENCE)
        detail = self.service.read_review(artifact_id)
        self.assertTrue(detail["ok"], detail)
        data = detail["data"]
        self.assertEqual(data["question"], "怎样把一页写完？")
        self.assertTrue(data["body"].strip())
        self.assertTrue(data["content_available"])
        self.assertEqual(len(data["experience_checks"]), 1)
        check = data["experience_checks"][0]
        self.assertEqual(check["status"], pps.CHECK_CURRENT)
        self.assertTrue(check["usable"])
        self.assertEqual(check["experience_id"], self.exp_one)
        self.assertIn("先写三行", check["excerpt"])
        self.assertEqual(data["governance"]["blocked"], False)
        # 真的走 A 的接口读过那条经历。
        self.assertIn(("read_experience", self.exp_one), self.memory.calls)

    def test_an_unreadable_experience_is_refused_before_the_owner_sees_it(self) -> None:
        before = len(self.port.calls)
        refused = self.service.create_review(question="q", experience_ids=["exp:missing"],
                                             operation_key="practice:review:missing")
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "rejected")
        self.assertEqual(refused["reason_code"], "PRACTICE_EVIDENCE_UNREADABLE")
        self.assertEqual(refused["data"]["experience_checks"][0]["status"], pps.CHECK_UNAVAILABLE)
        writes = [name for name, _payload in self.port.calls[before:]
                  if name in ("create_artifact", "commit_version")]
        self.assertEqual(writes, [])

    def test_a_review_without_experiences_is_an_honest_plan(self) -> None:
        created = self.service.create_review(question="还没证据的一个问题",
                                             operation_key="practice:review:plan")
        self.assertTrue(created["ok"], created)
        self.assertEqual(created["data"]["experience_basis"], pps.BASIS_NONE)
        data = self.service.read_review(created["data"]["artifact_id"])["data"]
        self.assertEqual(data["experience_basis"], pps.BASIS_NONE)
        self.assertEqual(data["experience_checks"], [])
        self.assertIn("没有引用任何经历", data["note"])

    def test_no_memory_means_no_claimed_experience(self) -> None:
        service = service_for(self.port, None)
        refused = service.create_review(question="q", experience_ids=[self.exp_one],
                                        operation_key="practice:review:unbound")
        self.assertEqual(refused["reason_code"], "PRACTICE_EVIDENCE_UNREADABLE")
        self.assertEqual(refused["data"]["experience_checks"][0]["reason_code"],
                         "PRACTICE_MEMORY_UNBOUND")
        allowed = service.create_review(question="没有 Memory 时的一份计划",
                                        operation_key="practice:review:unbound-plan")
        self.assertTrue(allowed["ok"], allowed)
        self.assertEqual(allowed["data"]["experience_basis"], pps.BASIS_NONE)

    def test_list_reviews_skips_other_artifacts_and_shows_archived(self) -> None:
        created = self.service.create_review(question="q1", experience_ids=[self.exp_one],
                                             operation_key="practice:review:list")
        artifact_id = created["data"]["artifact_id"]
        listed = self.service.list_reviews()
        self.assertTrue(listed["ok"], listed)
        self.assertEqual([row["artifact_id"] for row in listed["data"]["projects"]], [artifact_id])
        self.assertEqual(listed["data"]["projects"][0]["experience_count"], 1)
        self.assertNotIn("content", listed["data"]["projects"][0])
        self.assertNotIn("_haystack", listed["data"]["projects"][0])
        closed = self.service.projects.close_project(artifact_id=artifact_id,
                                                     expected_revision=1,
                                                     operation_key="practice:review:close")
        self.assertTrue(closed["ok"], closed)
        listed = self.service.list_reviews()
        self.assertEqual(listed["data"]["projects"][0]["status"], "archived")
        self.assertEqual(listed["data"]["skipped"]["other"], 2)

    def test_read_is_refused_for_another_kind(self) -> None:
        method = self.service.create_method(title="一条做法", instruction="先写三行",
                                            operation_key="practice:method:kind")
        wrong = self.service.read_review(method["data"]["artifact_id"])
        self.assertFalse(wrong["ok"])
        self.assertEqual(wrong["reason_code"], "PRACTICE_KIND_MISMATCH")
        plain = self.service.read_review(READING)
        self.assertFalse(plain["ok"])
        self.assertEqual(plain["reason_code"], "PRACTICE_NOT_A_DOCUMENT")


class MethodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.port = seeded_port()
        self.memory = FakeMemory()
        self.helped = self.memory.add("exp:helped", "先写三行再展开，那一页就写完了。")
        self.counter = self.memory.add("exp:counter", "换成先列要点，反而没写完。")
        self.service = service_for(self.port, self.memory)
        created = self.service.create_method(
            title="先写三行再展开", instruction="先写三行，再展开成段落。",
            scope="一页以内的短文", uncertainty="长文里是否成立未知",
            experience_ids=[self.helped], operation_key="practice:method:1")
        self.assertTrue(created["ok"], created)
        self.artifact_id = created["data"]["artifact_id"]
        self.created = created

    def head(self) -> str:
        return self.service.read_method(self.artifact_id)["data"]["head_version_id"]

    def test_a_new_method_is_tentative_with_scope_and_unknown(self) -> None:
        self.assertEqual(self.created["data"]["method_state"], "TENTATIVE")
        data = self.service.read_method(self.artifact_id)["data"]
        self.assertEqual(data["method_state"], "TENTATIVE")
        self.assertEqual(data["scope"], "一页以内的短文")
        self.assertEqual(data["uncertainty"], "长文里是否成立未知")
        self.assertFalse(data["verified_skill"])
        self.assertEqual(data["trials"], [])
        self.assertIn("做法草案不是已验证 Skill", data["note"])

    def test_helped_only_reaches_trial_and_never_supported(self) -> None:
        head = self.head()
        for index in range(3):
            recorded = self.service.record_trial(self.artifact_id, outcome="HELPED",
                                                 detail="第 %d 次有用" % (index + 1),
                                                 experience_ids=[self.helped],
                                                 expected_head=head, operation_key="trial:%d" % index)
            self.assertTrue(recorded["ok"], recorded)
            self.assertEqual(recorded["data"]["method_state"], "TRIAL")
            head = self.service.read_method(self.artifact_id)["data"]["head_version_id"]
        data = self.service.read_method(self.artifact_id)["data"]
        self.assertEqual(data["method_state"], "TRIAL")
        self.assertEqual(len(data["trials"]), 3)
        self.assertNotIn("SUPPORTED", json.dumps(data, ensure_ascii=False))

    def test_a_counterexample_contests_the_method_and_is_kept(self) -> None:
        recorded = self.service.record_trial(self.artifact_id, outcome="NOT_HELPED",
                                             detail="长文里没用", experience_ids=[self.counter],
                                             expected_head=self.head(),
                                             operation_key="practice:trial:counter")
        self.assertTrue(recorded["ok"], recorded)
        data = self.service.read_method(self.artifact_id)["data"]
        self.assertEqual(data["method_state"], "CONTESTED")
        self.assertEqual(len(data["counterexamples"]), 1)
        self.assertEqual(data["counterexamples"][0]["outcome"], "NOT_HELPED")
        self.assertEqual(data["counterexamples"][0]["detail"], "长文里没用")

    def test_helped_without_readable_evidence_is_refused(self) -> None:
        refused = self.service.record_trial(self.artifact_id, outcome="HELPED",
                                            detail="感觉不错", experience_ids=[],
                                            expected_head=self.head(),
                                            operation_key="practice:trial:noevidence")
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], "PRACTICE_EVIDENCE_REQUIRED")
        self.assertEqual(self.service.read_method(self.artifact_id)["data"]["trials"], [])
        # 读不回来的经历同样不算证据。
        refused = self.service.record_trial(self.artifact_id, outcome="NOT_HELPED",
                                            detail="x", experience_ids=["exp:withdrawn"],
                                            expected_head=self.head(),
                                            operation_key="practice:trial:unreadable")
        self.assertEqual(refused["reason_code"], "PRACTICE_EVIDENCE_REQUIRED")

    def test_unknown_keeps_the_state_and_is_recorded(self) -> None:
        unknown = self.service.record_trial(self.artifact_id, outcome="UNKNOWN",
                                            detail="说不好", experience_ids=[],
                                            expected_head=self.head(),
                                            operation_key="practice:trial:unknown")
        self.assertTrue(unknown["ok"], unknown)
        self.assertEqual(unknown["data"]["method_state"], "TENTATIVE")
        self.assertFalse(unknown["data"]["evidence_usable"])
        data = self.service.read_method(self.artifact_id)["data"]
        self.assertEqual([row["outcome"] for row in data["trials"]], ["UNKNOWN"])
        self.assertEqual(data["method_state"], "TENTATIVE")

    def test_revise_returns_to_tentative_and_keeps_counterexamples(self) -> None:
        self.service.record_trial(self.artifact_id, outcome="NOT_HELPED", detail="长文没用",
                                  experience_ids=[self.counter],
                                  expected_head=self.head(),
                                  operation_key="practice:trial:revise-counter")
        head = self.head()
        revised = self.service.revise_method(self.artifact_id,
                                             instruction="先写五行，再展开。",
                                             scope="一页以内", uncertainty="长文未知",
                                             expected_head=head,
                                             operation_key="practice:revise:1")
        self.assertTrue(revised["ok"], revised)
        self.assertEqual(revised["data"]["method_state"], "TENTATIVE")
        self.assertEqual(revised["data"]["revisions"], 1)
        self.assertEqual(revised["data"]["trials_preserved"], 1)
        data = self.service.read_method(self.artifact_id)["data"]
        self.assertEqual(data["instruction"], "先写五行，再展开。")
        self.assertEqual(data["method_state"], "TENTATIVE")
        self.assertEqual(len(data["counterexamples"]), 1)
        self.assertEqual(data["revisions"], 1)

    def test_retire_is_logical_and_keeps_the_document(self) -> None:
        head = self.head()
        retired = self.service.retire_method(self.artifact_id, expected_head=head,
                                             operation_key="practice:retire:1")
        self.assertTrue(retired["ok"], retired)
        self.assertEqual(retired["data"]["method_state"], "RETIRED")
        self.assertTrue(retired["data"]["content_kept"])
        data = self.service.read_method(self.artifact_id)["data"]
        self.assertEqual(data["method_state"], "RETIRED")
        self.assertTrue(data["body"].strip())
        again = self.service.retire_method(self.artifact_id,
                                           expected_head=data["head_version_id"],
                                           operation_key="practice:retire:2")
        self.assertEqual(again["status"], "already_applied")
        trial = self.service.record_trial(self.artifact_id, outcome="UNKNOWN", detail="",
                                          experience_ids=[], expected_head=data["head_version_id"],
                                          operation_key="practice:trial:retired")
        self.assertEqual(trial["reason_code"], "PRACTICE_METHOD_RETIRED")
        revision = self.service.revise_method(self.artifact_id, instruction="改写",
                                              expected_head=data["head_version_id"],
                                              operation_key="practice:revise:retired")
        self.assertEqual(revision["reason_code"], "PRACTICE_METHOD_RETIRED")

    def test_trial_needs_a_current_head(self) -> None:
        with self.assertRaises(pps.PracticeContractRefused):
            self.service.record_trial(self.artifact_id, outcome="UNKNOWN", detail="",
                                      experience_ids=[], expected_head="",
                                      operation_key="practice:trial:nohead")
        stale = self.service.record_trial(self.artifact_id, outcome="UNKNOWN", detail="",
                                          experience_ids=[], expected_head="version-moved-on",
                                          operation_key="practice:trial:stale")
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["status"], "conflict")

    def test_list_methods_searches_by_text_and_never_returns_bodies(self) -> None:
        listed = self.service.list_methods(query="三行")
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(len(listed["data"]["projects"]), 1)
        self.assertNotIn("body", listed["data"]["projects"][0])
        self.assertEqual(listed["data"]["projects"][0]["method_state"], "TENTATIVE")
        self.assertEqual(self.service.list_methods(query="完全不相关")["data"]["projects"], [])


class GovernanceTests(unittest.TestCase):
    """证据读不回来 → 依赖它的正文不再透出（且递归传播）。"""

    def setUp(self) -> None:
        self.port = seeded_port()
        self.memory = FakeMemory()
        self.exp = self.memory.add("exp:1", "那次是这样做的，结果写完了。")
        self.service = service_for(self.port, self.memory)
        review = self.service.create_review(question="怎样写完一页？",
                                            experience_ids=[self.exp],
                                            operation_key="practice:review:gov")
        self.review_id = review["data"]["artifact_id"]
        method = self.service.create_method(title="先写三行", instruction="先写三行再展开",
                                            scope="短文", uncertainty="长文未知",
                                            experience_ids=[self.exp],
                                            review_refs=[self.review_id],
                                            operation_key="practice:method:gov")
        self.method_id = method["data"]["artifact_id"]

    def test_a_withdrawn_experience_blocks_both_bodies(self) -> None:
        before = self.service.read_method(self.method_id)["data"]
        self.assertTrue(before["content_available"], before["governance"])
        self.assertEqual(len(before["evidence_checks"]), 2)

        self.memory.withdrawn.add(self.exp)

        review = self.service.read_review(self.review_id)["data"]
        self.assertTrue(review["governance"]["blocked"])
        self.assertFalse(review["content_available"])
        self.assertEqual(review["body"], "")
        self.assertEqual(review["content"], "")
        self.assertEqual(review["governance"]["reason_code"], "PRACTICE_EVIDENCE_UNAVAILABLE")
        self.assertEqual(review["experience_checks"][0]["status"], pps.CHECK_UNAVAILABLE)

        method = self.service.read_method(self.method_id)["data"]
        self.assertTrue(method["governance"]["blocked"])
        self.assertEqual(method["body"], "")
        self.assertEqual(method["content"], "")
        checks = {row["kind"]: row for row in method["evidence_checks"]}
        self.assertFalse(checks[pps.EVIDENCE_EXPERIENCE]["usable"])
        self.assertFalse(checks[pps.EVIDENCE_REVIEW]["usable"])
        self.assertIn("PRACTICE_EVIDENCE_UNAVAILABLE", checks[pps.EVIDENCE_REVIEW]["reason_code"])

    def test_a_blocked_document_refuses_new_trials_and_writes(self) -> None:
        self.memory.withdrawn.add(self.exp)
        head = self.service.read_method(self.method_id)["data"]["head_version_id"]
        trial = self.service.record_trial(self.method_id, outcome="UNKNOWN", detail="",
                                          experience_ids=[], expected_head=head,
                                          operation_key="practice:trial:blocked")
        self.assertEqual(trial["reason_code"], "PRACTICE_EVIDENCE_UNAVAILABLE")
        created = self.service.create_method(title="依赖读不到的证据",
                                             instruction="x",
                                             experience_ids=[self.exp],
                                             operation_key="practice:method:blocked")
        self.assertEqual(created["reason_code"], "PRACTICE_EVIDENCE_UNREADABLE")


class DocumentTests(unittest.TestCase):
    """AR0 续写正文：元数据由服务重建，正文原样保留。"""

    def setUp(self) -> None:
        self.port = seeded_port()
        self.memory = FakeMemory()
        self.exp = self.memory.add("exp:1", "那次写完了。")
        self.service = service_for(self.port, self.memory)
        created = self.service.create_review(question="问题一", title="回顾一",
                                             experience_ids=[self.exp],
                                             operation_key="practice:review:doc")
        self.review_id = created["data"]["artifact_id"]
        self.head = created["data"]["head_version_id"] or \
            self.service.read_review(self.review_id)["data"]["head_version_id"]

    def test_body_can_be_continued_and_the_metadata_survives(self) -> None:
        new_body = "## 续写的一节\n\n新的观察：写到第三行就顺了。"
        updated = self.service.update_document(self.review_id, content=new_body,
                                               expected_head=self.head,
                                               operation_key="practice:doc:1")
        self.assertTrue(updated["ok"], updated)
        self.assertTrue(updated["data"]["marker_rebuilt"])
        self.assertFalse(updated["data"]["marker_stripped"])
        data = self.service.read_review(self.review_id)["data"]
        self.assertEqual(data["body"], new_body)
        self.assertEqual(data["question"], "问题一")
        self.assertEqual(data["experience_ids"], [self.exp])
        self.assertEqual(len(data["experience_checks"]), 1)

    def test_a_writer_cannot_overwrite_the_metadata_by_pasting_the_marker(self) -> None:
        data = self.service.read_review(self.review_id)["data"]
        forged = pps.build_practice_content(
            {"kind": pps.KIND_REVIEW, "title": "被改写", "question": "被改写",
             "experience_ids": ["exp:forged"], "experience_basis": pps.BASIS_EXPERIENCE},
            "正文被整段交回来。")
        updated = self.service.update_document(self.review_id, content=forged,
                                               expected_head=data["head_version_id"],
                                               operation_key="practice:doc:forged")
        self.assertTrue(updated["ok"], updated)
        self.assertTrue(updated["data"]["marker_stripped"])
        after = self.service.read_review(self.review_id)["data"]
        self.assertEqual(after["question"], "问题一")
        self.assertEqual(after["experience_ids"], [self.exp])
        self.assertEqual(after["body"], "正文被整段交回来。")
        # 交回来的标记没有被写第二遍。
        self.assertEqual(after["content"].count("<!-- nyairo-practice:v1"), 1)

    def test_a_blank_body_or_a_stale_head_is_refused(self) -> None:
        blank = self.service.update_document(self.review_id, content="   ",
                                            expected_head=self.head,
                                            operation_key="practice:doc:blank")
        self.assertEqual(blank["reason_code"], "PRACTICE_BODY_REQUIRED")
        stale = self.service.update_document(self.review_id, content="正文",
                                             expected_head="version-moved-on",
                                             operation_key="practice:doc:stale")
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["status"], "conflict")
        with self.assertRaises(pps.PracticeContractRefused):
            self.service.update_document(self.review_id, content="正文", expected_head="",
                                         operation_key="practice:doc:nohead")
        plain = self.port.seed(artifact_id="artifact:" + "c" * 32, title="普通项目",
                               content="普通正文")
        not_practice = self.service.update_document("artifact:" + "c" * 32, content="x",
                                                    expected_head=plain,
                                                    operation_key="practice:doc:plain")
        self.assertEqual(not_practice["reason_code"], "PRACTICE_NOT_A_DOCUMENT")


class FactoryTests(unittest.TestCase):
    def test_a_half_wired_environment_answers_none(self) -> None:
        self.assertIsNone(pps.practice_from_env({}, {}))
        self.assertIsNone(pps.practice_from_env({}, {"NYAIRO_LIFE_WORKSPACE_ID": "ws-1"}))

    def test_a_ready_environment_builds_the_service_over_an_existing_port(self) -> None:
        port = seeded_port()
        env = {"NYAIRO_LIFE_WORKSPACE_ID": WORKSPACE, "NYAIRO_ARTIFACT_GRANT": GRANT,
               "NYAIRO_LIFE_SUBJECT": SUBJECT}
        service = pps.practice_from_env(port, env)
        self.assertIsNotNone(service)
        self.assertEqual(service.workspace_id, WORKSPACE)
        self.assertEqual(service.subject_id, SUBJECT)
        listed = service.list_reviews()
        self.assertTrue(listed["ok"], listed)
        # A 的工厂总会回一个服务（可能是未绑定），所以"接上了"不等于"能读经历"。
        self.assertTrue(listed["data"]["memory_bound"])
        refused = service.create_review(question="q", experience_ids=["exp:any"],
                                        operation_key="practice:review:factory")
        self.assertEqual(refused["reason_code"], "PRACTICE_EVIDENCE_UNREADABLE")


class RealMemoryContractTests(unittest.TestCase):
    """A 的真实 ``LifeMemoryService``（未绑定）接进来：拒绝路径不是只对着替身绿。"""

    def test_the_real_unbound_memory_refuses_evidence_but_allows_a_plan(self) -> None:
        from life_memory_service import memory_service_from_env

        memory = memory_service_from_env({})
        status = memory.status()
        self.assertNotEqual(status.get("status"), "READY")
        port = seeded_port()
        service = service_for(port, memory)
        refused = service.create_review(question="q", experience_ids=["exp:any"],
                                        operation_key="practice:review:realunbound")
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason_code"], "PRACTICE_EVIDENCE_UNREADABLE")
        check = refused["data"]["experience_checks"][0]
        self.assertFalse(check["usable"])
        # A 自己的原因码（不是 P 编的）出现在核对结果里。
        self.assertTrue(check["reason_code"])
        plan = service.create_review(question="没有 Memory 的一份计划",
                                     operation_key="practice:review:realplan")
        self.assertTrue(plan["ok"], plan)
        method = service.create_method(title="草案", instruction="先写三行",
                                       operation_key="practice:method:realunbound")
        self.assertTrue(method["ok"], method)
        head = service.read_method(method["data"]["artifact_id"])["data"]["head_version_id"]
        unknown = service.record_trial(method["data"]["artifact_id"], outcome="UNKNOWN",
                                       detail="说不好", experience_ids=[],
                                       expected_head=head,
                                       operation_key="practice:trial:realunbound")
        self.assertTrue(unknown["ok"], unknown)
        self.assertEqual(unknown["data"]["method_state"], "TENTATIVE")


@POSIX_ONLY
class RealOwnerAndMemoryIntegrationTests(unittest.TestCase):
    """真实 Life Supply owner（AF_UNIX）+ 真实 M0 EvidenceStore 的一条完整链路。

    只在这里做一次集成：真实工件创建 / CAS / 真实经历读取 / 治理撤回后正文不再透出。
    """

    def setUp(self) -> None:
        from test_v04_memory_product import build_memory, memory_env

        self.root = unique_root("practice-integration-")
        # 真实 Supply owner
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        uid = os.geteuid()
        data = self.root / "supply"
        self.socket_path = self.root / "life-supply.sock"
        self.supply = LifeSupplyService(data, operator_uids={uid}, service_uids={uid},
                                        switches=FeatureSwitches.all_on(),
                                        writer_instance="practice-integration")
        # Python 3.12 的 ``TCPServer.server_bind`` 会在允许时给 socket 设 SO_REUSEADDR（AF_UNIX 上
        # 是 EOPNOTSUPP）；夹具不需要 reuse，就在构造前关掉——只动这个夹具类，不改产品代码。
        LifeSupplySocketServer.allow_reuse_address = False
        LifeSupplySocketServer.allow_reuse_port = False
        # 参数顺序是 (path, service)：写成 (service, path) 会 bind 到服务对象的 repr 上。
        self.server = LifeSupplySocketServer(str(self.socket_path), self.supply)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # 真实 M0 记忆库
        self.binding = build_memory(self.root / "phone", rows=[{
            "source_ref": "life:integration",
            "text": "先写三行再展开，那一页真的写完了。",
            "occurred_at": "2026-11-02T09:00:00+00:00",
        }])
        from life_memory_service import memory_service_from_env

        self.memory = memory_service_from_env(memory_env(self.binding))
        self.assertEqual(self.memory.status().get("status"), "READY",
                         self.memory.status())
        rows = self.memory.list_experiences(query="写", limit=5)["data"]["experiences"]
        self.assertTrue(rows, "the real Memory must expose the seeded experience")
        self.experience_id = rows[0]["experience_id"]

    def tearDown(self) -> None:
        try:
            self.server.shutdown()
        except Exception:
            pass
        try:
            self.server.server_close()
        except Exception:
            pass
        try:
            self.supply.close()
        except Exception:
            pass

    def test_review_and_method_over_the_real_owner_and_real_memory(self) -> None:
        from life_artifact_port import LifeArtifactPort

        workspace = self._provision_workspace()
        grant = self._issue_artifact_grant(workspace)
        port = LifeArtifactPort(str(self.socket_path), SUPPLY_SUBJECT)
        # Supply 的 subject 与 Memory 的 persona 不是一回事：这里故意用两个名字。
        self.assertNotEqual(SUPPLY_SUBJECT, self.binding["owner"])
        projects = pps.PrivateProjectService(port, workspace, grant, subject_id=SUPPLY_SUBJECT)
        service = pps.PersonalPracticeService(projects, memory_service=self.memory)

        review = service.create_review(question="怎样把一页写完？", title="一次真实的回顾",
                                       experience_ids=[self.experience_id],
                                       operation_key="integration:review:1")
        self.assertTrue(review["ok"], review)
        review_id = review["data"]["artifact_id"]
        detail = service.read_review(review_id)
        self.assertTrue(detail["ok"], detail)
        self.assertTrue(detail["data"]["content_available"])
        self.assertEqual(detail["data"]["experience_checks"][0]["status"], pps.CHECK_CURRENT)

        method = service.create_method(title="先写三行", instruction="先写三行，再展开。",
                                       scope="一页以内", uncertainty="长文未知",
                                       experience_ids=[self.experience_id],
                                       review_refs=[review_id],
                                       operation_key="integration:method:1")
        self.assertTrue(method["ok"], method)
        method_id = method["data"]["artifact_id"]
        head = service.read_method(method_id)["data"]["head_version_id"]
        trial = service.record_trial(method_id, outcome="HELPED", detail="这一次有用",
                                     experience_ids=[self.experience_id],
                                     expected_head=head,
                                     operation_key="integration:trial:1")
        self.assertTrue(trial["ok"], trial)
        self.assertEqual(trial["data"]["method_state"], "TRIAL")
        # 真实 owner 里确实多了一件工件，而且它在列表里就是这一份回顾。
        listed = service.list_reviews()
        self.assertIn(review_id, [row["artifact_id"] for row in listed["data"]["projects"]])
        # 交付面仍不认识"已验证"：trial 之后也没有 SUPPORTED。
        after = service.read_method(method_id)["data"]
        self.assertEqual(after["method_state"], "TRIAL")
        self.assertNotIn("SUPPORTED", json.dumps(after, ensure_ascii=False))

    # -- operator-side plumbing (real owner grants; the subject never sees this) ---------

    def _operator(self, request: dict) -> dict:
        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(20.0)
            client.connect(str(self.socket_path))
            client.sendall(body + b"\n")
            line = client.makefile("rb").readline(1 << 20)
        self.assertTrue(line, "the owner closed the connection without an answer")
        answer = json.loads(line.decode("utf-8"))
        self.assertIsInstance(answer, dict)
        return answer

    def _issue(self, capability: str, scope: str, target: str, authority_scope: str,
               use_mode: str, operation_id: str) -> str:
        answer = self._operator({"op": "issue_grant", "grant": {
            "subject": SUPPLY_SUBJECT, "capability": capability, "scope": scope,
            "target": target, "authority_scope": authority_scope, "use_mode": use_mode,
            "operation_id": operation_id}})
        self.assertEqual(answer.get("status"), "ALLOW", answer)
        return str(answer["grant_id"])

    def _provision_workspace(self) -> str:
        grant = self._issue("PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
                            "workspace:%s" % SUPPLY_SUBJECT, "life-supply.workspace.v1",
                            "ONE_SHOT", "issue:practice-workspace")
        answer = self._operator({"op": "provision_workspace", "grant_id": grant,
                                 "operation_key": "practice:workspace",
                                 "workspace": {"subject_id": SUPPLY_SUBJECT}})
        self.assertEqual(answer.get("status"), "OK", answer)
        return str(answer["workspace"]["workspace_id"])

    def _issue_artifact_grant(self, workspace_id: str) -> str:
        return self._issue("COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                           "workspace:%s" % workspace_id, "life-supply.artifact.v1",
                           "REUSABLE", "issue:practice-artifact")

    # -- helpers -----------------------------------------------------------------------

    @staticmethod
    def _workspace(port: Any) -> str:
        rows = port.list_artifacts()
        for row in rows:
            workspace = str(row.get("workspace_id") or "")
            if workspace:
                return workspace
        raise AssertionError("the real owner listed no workspace")

    @staticmethod
    def _grant() -> str:
        from lifesupply.service.switches import FeatureSwitches  # noqa: F401

        return os.environ.get("NYAIRO_PRACTICE_TEST_GRANT") or "grant-practice-integration"


class GovernanceLeakTests(unittest.TestCase):
    """治理挡住之后，**任意嵌套字段**都不许漏出受治理的正文 / 指令 / 试用说明。

    这条是父级只读核对发现的真实漏洞的回归：``data.project`` 曾经带着原始 ``content``，
    ``instruction`` / ``question`` / ``trials[].detail`` / 旧指令也照旧发给调用方 —— 只清顶层
    ``body`` 不够。
    """

    QUESTION = "北极星问句-8f2c1d"
    BODY = "北极星正文-4b9e77"
    INSTRUCTION = "北极星做法-6a1f30"
    SCOPE = "北极星范围-2c7d55"
    UNCERTAINTY = "北极星未知-93ab41"
    TRIAL = "北极星试用说明-77de02"
    OLD = "北极星旧指令-1e5c88"
    QUESTION2 = "北极星第二问句-b3d901"
    INSTRUCTION2 = "北极星第二做法-c40f11"
    TRIAL2 = "北极星第二试用-f2a7b3"

    def setUp(self) -> None:
        self.port = seeded_port()
        self.memory = FakeMemory()
        self.exp = self.memory.add("exp:gov", "那次是这样做的，结果写完了。")
        self.service = service_for(self.port, self.memory)

        review = self.service.create_review(question=self.QUESTION, title="回顾-治理",
                                            experience_ids=[self.exp],
                                            operation_key="gov:review:1")
        self.assertTrue(review["ok"], review)
        self.review_id = review["data"]["artifact_id"]
        continued = self.service.update_document(self.review_id, content=self.BODY,
                                                 expected_head=review["data"]["head_version_id"],
                                                 operation_key="gov:review:doc")
        self.assertTrue(continued["ok"], continued)

        method = self.service.create_method(title="做法-治理", instruction=self.INSTRUCTION,
                                            scope=self.SCOPE, uncertainty=self.UNCERTAINTY,
                                            experience_ids=[self.exp],
                                            review_refs=[self.review_id],
                                            operation_key="gov:method:1")
        self.assertTrue(method["ok"], method)
        self.method_id = method["data"]["artifact_id"]
        head = self.service.read_method(self.method_id)["data"]["head_version_id"]
        trial = self.service.record_trial(self.method_id, outcome="HELPED", detail=self.TRIAL,
                                          experience_ids=[self.exp], expected_head=head,
                                          operation_key="gov:trial:1")
        self.assertTrue(trial["ok"], trial)
        head = self.service.read_method(self.method_id)["data"]["head_version_id"]
        revised = self.service.revise_method(self.method_id, instruction=self.OLD,
                                             scope=self.SCOPE, uncertainty=self.UNCERTAINTY,
                                             expected_head=head, operation_key="gov:revise:1")
        self.assertTrue(revised["ok"], revised)

        # 第二条做法：自己的经历仍然可读，只靠"被引用的回顾"变成 blocked。
        readable = self.memory.add("exp:ok", "这一次也读完了。")
        self.exp_ok = readable
        second = self.service.create_method(title="做法-引用", instruction=self.INSTRUCTION2,
                                            scope="一页以内", uncertainty="未知",
                                            experience_ids=[self.exp_ok],
                                            review_refs=[self.review_id],
                                            operation_key="gov:method:2")
        self.assertTrue(second["ok"], second)
        self.second_id = second["data"]["artifact_id"]
        head = self.service.read_method(self.second_id)["data"]["head_version_id"]
        trial = self.service.record_trial(self.second_id, outcome="NOT_HELPED", detail=self.TRIAL2,
                                          experience_ids=[self.exp_ok], expected_head=head,
                                          operation_key="gov:trial:2")
        self.assertTrue(trial["ok"], trial)

        # 治理：撤回那条被引用的经历。
        self.memory.withdrawn.add(self.exp)

    def leaked(self, payload: Any, *sentinels: str) -> list[str]:
        blob = json.dumps(payload, ensure_ascii=False)
        return [sentinel for sentinel in sentinels if sentinel in blob]

    def test_a_blocked_review_leaks_nothing_nested(self) -> None:
        answer = self.service.read_review(self.review_id)
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(answer["data"]["governance"]["blocked"])
        leaked = self.leaked(answer, self.QUESTION, self.BODY)
        self.assertEqual(leaked, [], "受治理的回顾正文/问句仍然出现在信封里：%s" % leaked)
        # 身份、状态与核对结果照旧可读（不是"整份消失"）。
        self.assertEqual(answer["data"]["kind"], pps.KIND_REVIEW)
        self.assertEqual(answer["data"]["project"]["title"], "回顾-治理")
        self.assertTrue(answer["data"]["project"]["head_version_id"])
        self.assertEqual(len(answer["data"]["experience_checks"]), 1)
        self.assertFalse(answer["data"]["experience_checks"][0]["usable"])

    def test_a_blocked_method_leaks_no_instruction_or_trial_detail(self) -> None:
        answer = self.service.read_method(self.method_id)
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(answer["data"]["governance"]["blocked"])
        leaked = self.leaked(answer, self.INSTRUCTION, self.SCOPE, self.UNCERTAINTY,
                             self.TRIAL, self.OLD, self.BODY, self.QUESTION)
        self.assertEqual(leaked, [], "受治理的指令/试用说明仍然出现在信封里：%s" % leaked)
        # 状态与身份还在：RETIRED / CONTESTED 之外的枚举、head、版本号都能读。
        self.assertEqual(answer["data"]["method_state"], "TENTATIVE")
        self.assertTrue(answer["data"]["project"]["head_version_id"])
        self.assertEqual(answer["data"]["project"]["title"], "做法-治理")
        self.assertEqual(len(answer["data"]["evidence_checks"]), 2)

    def test_a_method_blocked_only_by_a_cited_review_also_leaks_nothing(self) -> None:
        answer = self.service.read_method(self.second_id)
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(answer["data"]["governance"]["blocked"])
        leaked = self.leaked(answer, self.INSTRUCTION2, self.TRIAL2, self.BODY, self.QUESTION)
        self.assertEqual(leaked, [], "引用传播后仍然漏出正文：%s" % leaked)
        checks = {row["kind"]: row for row in answer["data"]["evidence_checks"]}
        self.assertTrue(checks[pps.EVIDENCE_EXPERIENCE]["usable"])
        self.assertFalse(checks[pps.EVIDENCE_REVIEW]["usable"])

    def test_list_rows_do_not_carry_blocked_content(self) -> None:
        reviews = self.service.list_reviews()
        self.assertTrue(reviews["ok"], reviews)
        leaked = self.leaked(reviews, self.QUESTION, self.BODY)
        self.assertEqual(leaked, [], "回顾列表仍然漏出正文/问句：%s" % leaked)
        row = reviews["data"]["projects"][0]
        self.assertTrue(row.get("governance_blocked"))
        self.assertEqual(row["title"], "回顾-治理")

        methods = self.service.list_methods()
        self.assertTrue(methods["ok"], methods)
        leaked = self.leaked(methods, self.INSTRUCTION, self.INSTRUCTION2, self.SCOPE,
                             self.UNCERTAINTY, self.TRIAL, self.TRIAL2, self.OLD)
        self.assertEqual(leaked, [], "做法列表仍然漏出指令/试用说明：%s" % leaked)
        for row in methods["data"]["projects"]:
            self.assertTrue(row.get("governance_blocked"), row)
            self.assertEqual(row["instruction"], "")
            self.assertEqual(row["scope"], "")
            self.assertEqual(row["uncertainty"], "")


if __name__ == "__main__":
    unittest.main()

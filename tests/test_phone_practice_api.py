#!/usr/bin/env python3
"""v0.4 阶段 B：手机上的个人实践工作台 + Memory（HTTP 面，只测本轮新增的那几条路）。

这个文件测什么
--------------
``GET/POST /api/phone/memory``、``GET /api/phone/memory/experiences[/<id>]``、
``POST /api/phone/memory/enable``，以及 ``/api/phone/practice`` 的回顾与做法全流程
（列表 / 详情 / 创建 / 续写 / 试用 / 修订 / 退休）。

* 服务是真的：真实 ``PhoneService`` + 真实 loopback ``create_server``。owner 用
  ``test_v04_private_projects_stage.FakePort``（内存替身）顶替 Supply socket；Memory 用
  ``test_v04_personal_practice_stage.FakeMemory``（按 A 的答复形状冻结的替身）注入
  ``PhoneService._memory_service``。P 的职责只是转发、拒绝与记录来源。
* GET 是纯读：一次列表/详情之后，owner 侧不应出现任何写操作。
* 写真要 CSRF；请求体只收自己声明的那几个字段；``expected_head`` 缺失即拒；客户端不能声明
  owner / workspace / artifact_id / 数据库路径。
* 夹具根建在会话 scratch 下并**保留**（不删除、不清理），名字唯一。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import server as phone_server, service as phone_service  # noqa: E402
from test_v04_personal_practice_stage import FakeMemory  # noqa: E402
from test_v04_private_projects_stage import (  # noqa: E402
    FakePort,
    GRANT,
    ITEMS,
    READING,
    WORKSPACE,
)

CSRF = "practice-csrf-token"


def fixture_base() -> Path:
    override = os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-practice-phone") if override
            else (Path(os.environ.get("TEMP") or "/tmp") / "nyairo-practice-phone"))
    base.mkdir(parents=True, exist_ok=True)
    return base


_ROUND = 0


def unique_root(prefix: str) -> Path:
    global _ROUND
    base = fixture_base()
    _ROUND += 1
    root = base / ("%s%d-%d-%d" % (prefix, os.getpid(), time.time_ns(), _ROUND))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def instance_for(root: Path) -> dict:
    world_home = root / "world"
    world_home.mkdir(parents=True, exist_ok=True)
    return {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "practice-fixture",
        "subject": "chiyo",
        "local_owner_uid": 0,
        "profile_home": str(root),
        "root": str(root),
        "world_home": str(world_home),
        "world_id": "w-practice",
        "supply_socket": str(root / "run" / "supply-fixture.sock"),
        "private_items_artifact_id": ITEMS,
        "reading_artifact_id": READING,
        "artifact_grant_id": GRANT,
        "workspace_id": WORKSPACE,
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }


class PracticeAPIBase(unittest.TestCase):
    """One real loopback server per test, over one in-memory owner and one fake Memory."""

    def setUp(self) -> None:
        self.root = unique_root("v04-practice-api-")
        self.port = FakePort()
        self.port.seed(artifact_id=READING, title="她的阅读材料", content="第一页")
        self.port.seed(artifact_id=ITEMS, title="她的私人日常事项", content="{}")
        self.memory = FakeMemory()
        self.exp_one = self.memory.add("exp:1", "我用先写三行再展开的办法写完了那一页。")
        self.exp_two = self.memory.add("exp:2", "换成先列要点就没写完。")
        self.instance = instance_for(self.root)
        self.service = phone_service.PhoneService(self.instance,
                                                  environment={"NYA02_SUBJECT": "chiyo"})
        # 只有这两处注入：夹具没有 Supply socket，Memory 也没有真的 M0 库。
        self.service._port = lambda: self.port
        self.service._memory_service = lambda: self.memory
        self.httpd = phone_server.create_server(self.service, csrf_token=CSRF,
                                                port=free_port())
        self.base_url = "http://127.0.0.1:%d" % int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        httpd = getattr(self, "httpd", None)
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        thread = getattr(self, "thread", None)
        if thread is not None:
            thread.join(timeout=10)

    # -- transport ---------------------------------------------------------------------

    def call(self, method: str, path: str, body=None, *, headers=None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return int(error.code), json.loads(error.read().decode("utf-8"))

    def post(self, path: str, body, *, csrf: bool = True):
        extra = {"X-CSRF-Token": CSRF} if csrf else {}
        return self.call("POST", path, body, headers=extra)

    def journal(self) -> list[dict]:
        path = self.root / "phone" / "operations.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def writes(self) -> list[str]:
        return [name for name, _payload in self.port.calls
                if name in ("create_artifact", "commit_version", "archive_artifact")]

    # -- fixtures ----------------------------------------------------------------------

    def create_review(self, *, key: str = "practice:review:1",
                      experience_ids=None) -> tuple[int, dict]:
        body = {"question": "怎样把一页写完？", "title": "写完一页的回顾",
                "experience_ids": ([self.exp_one] if experience_ids is None else experience_ids),
                "operation_key": key}
        return self.post("/api/phone/practice/reviews", body)

    def review_id(self) -> str:
        code, doc = self.create_review()
        self.assertEqual(code, 200, doc)
        return str(doc["data"]["artifact_id"])


class MemoryReadTest(PracticeAPIBase):
    def test_memory_status_is_a_pure_read(self) -> None:
        code, doc = self.call("GET", "/api/phone/memory")
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        self.assertEqual(doc["status"], "ok")
        data = doc["data"]
        self.assertEqual(data["binding_status"], "READY")
        self.assertTrue(data["recall_ready"])
        self.assertEqual(data["owner"], "chiyo")
        self.assertTrue(data["installed"])
        self.assertTrue(data["enable_available"])
        self.assertEqual(self.writes(), [])

    def test_experiences_are_searchable_and_bounded(self) -> None:
        code, doc = self.call("GET", "/api/phone/memory/experiences?query=%E5%86%99&limit=1")
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        self.assertEqual(doc["data"]["count"], 1)
        self.assertEqual(doc["data"]["limit"], 1)
        self.assertEqual(doc["data"]["owner"], "chiyo")
        self.assertIn(("list_experiences", "写"), self.memory.calls)
        self.assertEqual(self.writes(), [])

    def test_an_unknown_query_parameter_is_refused(self) -> None:
        code, doc = self.call("GET", "/api/phone/memory/experiences?since=2026-01-01")
        self.assertEqual(code, 400, doc)
        self.assertTrue(doc["reason_code"].startswith("UNKNOWN_QUERY"))

    def test_experience_detail_is_a_pure_read(self) -> None:
        code, doc = self.call("GET", "/api/phone/memory/experiences/%s" % self.exp_one)
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        self.assertEqual(doc["data"]["experience"]["experience_id"], self.exp_one)
        self.assertIn("先写三行", doc["data"]["experience"]["text"])

    def test_a_withdrawn_experience_is_not_served(self) -> None:
        self.memory.withdrawn.add(self.exp_one)
        code, doc = self.call("GET", "/api/phone/memory/experiences/%s" % self.exp_one)
        self.assertEqual(code, 503, doc)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["reason_code"], "MEMORY_EXPERIENCE_NOT_RECALLABLE")
        self.assertNotIn("text", doc["data"])

    def test_memory_enable_refuses_a_client_owner_and_needs_csrf(self) -> None:
        code, doc = self.post("/api/phone/memory/enable", {"operation_key": "memory:1"},
                              csrf=False)
        self.assertEqual(code, 403, doc)
        self.assertEqual(doc["reason_code"], "CSRF_TOKEN_MISSING")
        code, doc = self.post("/api/phone/memory/enable",
                              {"operation_key": "memory:1", "owner": "someone-else"})
        self.assertEqual(code, 409, doc)
        self.assertIn("CLIENT_IDENTITY_REFUSED", doc["reason_code"])
        code, doc = self.post("/api/phone/memory/enable",
                              {"operation_key": "memory:1", "database": "/tmp/x.db"})
        self.assertEqual(code, 400, doc)
        self.assertTrue(doc["reason_code"].startswith("UNKNOWN_FIELD"))
        self.assertEqual(self.writes(), [])


class MemoryEnableTest(PracticeAPIBase):
    """让 A 的真实 ``enable_instance_memory`` 拒绝一个没有 Life 实例的夹具根。"""

    def test_enable_is_explicit_and_never_restarts_anything(self) -> None:
        code, doc = self.post("/api/phone/memory/enable", {"operation_key": "memory:enable:1"})
        self.assertIn(code, (409, 503), doc)
        self.assertFalse(doc["ok"])
        data = doc.get("data") or {}
        self.assertFalse(data.get("service_started", False))
        self.assertEqual(data.get("processes_started", 0), 0)
        self.assertEqual(data.get("owner"), "chiyo")
        rows = self.journal()
        self.assertEqual(rows[-1]["action"], "memory_enable")
        self.assertEqual(rows[-1]["operation_key"], "memory:enable:1")
        # 一个 GET 绝不会走这条路。
        self.call("GET", "/api/phone/memory")
        self.assertEqual(len([row for row in self.journal() if row["action"] == "memory_enable"]), 1)


class PracticeReadTest(PracticeAPIBase):
    def test_workbench_lists_carry_no_bodies(self) -> None:
        self.review_id()
        code, doc = self.call("GET", "/api/phone/practice")
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        data = doc["data"]
        self.assertEqual(len(data["reviews"]), 1)
        self.assertEqual(data["counts"], {"reviews": 1, "methods": 0})
        self.assertEqual(data["memory"]["binding_status"], "READY")
        self.assertTrue(data["not_verified_skill"])
        self.assertNotIn("content", data["reviews"][0])
        self.assertNotIn("body", data["reviews"][0])
        self.assertEqual(self.writes(), ["create_artifact"])

    def test_review_detail_carries_body_and_evidence_checks(self) -> None:
        artifact_id = self.review_id()
        code, doc = self.call("GET", "/api/phone/practice/reviews/%s" % artifact_id)
        self.assertEqual(code, 200, doc)
        data = doc["data"]
        self.assertEqual(data["kind"], "review")
        self.assertTrue(data["content_available"])
        self.assertTrue(data["body"].strip())
        self.assertEqual(data["experience_checks"][0]["status"], "CURRENT")
        self.assertTrue(data["experience_checks"][0]["usable"])
        self.assertEqual(self.writes(), ["create_artifact"])

    def test_a_withdrawn_experience_stops_the_body_from_leaking(self) -> None:
        artifact_id = self.review_id()
        self.memory.withdrawn.add(self.exp_one)
        code, doc = self.call("GET", "/api/phone/practice/reviews/%s" % artifact_id)
        self.assertEqual(code, 200, doc)
        data = doc["data"]
        self.assertTrue(data["governance"]["blocked"])
        self.assertFalse(data["content_available"])
        self.assertEqual(data["body"], "")
        self.assertEqual(data["content"], "")
        self.assertEqual(data["governance"]["reason_code"], "PRACTICE_EVIDENCE_UNAVAILABLE")

    def test_methods_list_and_search(self) -> None:
        self.post("/api/phone/practice/methods",
                  {"title": "先写三行", "instruction": "先写三行，再展开。",
                   "scope": "一页以内", "uncertainty": "长文未知",
                   "operation_key": "practice:method:1"})
        code, doc = self.call("GET", "/api/phone/practice/methods?query=%E4%B8%89%E8%A1%8C")
        self.assertEqual(code, 200, doc)
        self.assertEqual(len(doc["data"]["projects"]), 1)
        self.assertEqual(doc["data"]["projects"][0]["method_state"], "TENTATIVE")
        self.assertNotIn("body", doc["data"]["projects"][0])
        code, doc = self.call("GET", "/api/phone/practice/methods?query=nothing-matches")
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["data"]["projects"], [])

    def test_a_review_id_is_never_served_as_a_method(self) -> None:
        artifact_id = self.review_id()
        code, doc = self.call("GET", "/api/phone/practice/methods/%s" % artifact_id)
        self.assertEqual(code, 409, doc)
        self.assertEqual(doc["reason_code"], "PRACTICE_KIND_MISMATCH")


class PracticeWriteTest(PracticeAPIBase):
    def test_create_review_needs_csrf_its_own_fields_and_real_evidence(self) -> None:
        body = {"question": "怎样把一页写完？", "experience_ids": [self.exp_one],
                "operation_key": "practice:review:1"}
        code, doc = self.post("/api/phone/practice/reviews", body, csrf=False)
        self.assertEqual(code, 403, doc)
        self.assertEqual(doc["reason_code"], "CSRF_TOKEN_MISSING")

        code, doc = self.post("/api/phone/practice/reviews", dict(body, extra=1))
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "UNKNOWN_FIELD:extra")

        code, doc = self.post("/api/phone/practice/reviews", dict(body, artifact_id="artifact:x"))
        self.assertEqual(code, 409, doc)
        self.assertIn("CLIENT_IDENTITY_REFUSED", doc["reason_code"])

        code, doc = self.post("/api/phone/practice/reviews",
                              dict(body, experience_ids="exp:1"))
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "EXPERIENCE_IDS_INVALID")

        code, doc = self.post("/api/phone/practice/reviews",
                              dict(body, experience_ids=["exp:missing"],
                                   operation_key="practice:review:missing"))
        self.assertEqual(code, 409, doc)
        self.assertEqual(doc["reason_code"], "PRACTICE_EVIDENCE_UNREADABLE")
        self.assertEqual(self.writes(), [])

        code, doc = self.post("/api/phone/practice/reviews", body)
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["status"], "applied")
        self.assertTrue(doc["data"]["artifact_id"])
        rows = self.journal()
        self.assertEqual(rows[-1]["action"], "practice_review_create")
        self.assertEqual(rows[-1]["operation_key"], "practice:review:1")
        self.assertIn("create_artifact", self.writes())

    def test_document_continuation_needs_a_head_and_keeps_the_metadata(self) -> None:
        artifact_id = self.review_id()
        path = "/api/phone/practice/reviews/%s/documents" % artifact_id
        code, doc = self.post(path, {"content": "续写的一段。", "operation_key": "practice:doc:1"})
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "EXPECTED_HEAD_INVALID")

        detail_code, detail = self.call("GET", "/api/phone/practice/reviews/%s" % artifact_id)
        self.assertEqual(detail_code, 200, detail)
        head = detail["data"]["head_version_id"]

        code, doc = self.post(path, {"content": "续写的一段。", "expected_head": "version-moved-on",
                                     "operation_key": "practice:doc:stale"})
        self.assertEqual(code, 409, doc)

        code, doc = self.post(path, {"content": "续写的一段。", "expected_head": head,
                                     "operation_key": "practice:doc:1"})
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["data"]["metadata_preserved"])
        code, detail = self.call("GET", "/api/phone/practice/reviews/%s" % artifact_id)
        self.assertEqual(detail["data"]["body"], "续写的一段。")
        self.assertEqual(detail["data"]["question"], "怎样把一页写完？")

    def test_full_method_flow_helped_counterexample_revision_retire(self) -> None:
        code, doc = self.post("/api/phone/practice/methods",
                              {"title": "先写三行", "instruction": "先写三行，再展开。",
                               "scope": "一页以内", "uncertainty": "长文未知",
                               "experience_ids": [self.exp_one],
                               "review_refs": [self.review_id()],
                               "operation_key": "practice:method:1"})
        self.assertEqual(code, 200, doc)
        method_id = str(doc["data"]["artifact_id"])
        self.assertEqual(doc["data"]["method_state"], "TENTATIVE")

        def head() -> str:
            _code, view = self.call("GET", "/api/phone/practice/methods/%s" % method_id)
            return str(view["data"]["head_version_id"])

        trials = "/api/phone/practice/methods/%s/trials" % method_id
        code, doc = self.post(trials, {"outcome": "HELPED", "detail": "有用",
                                       "experience_ids": [], "expected_head": head(),
                                       "operation_key": "practice:trial:noevidence"})
        self.assertEqual(code, 409, doc)
        self.assertEqual(doc["reason_code"], "PRACTICE_EVIDENCE_REQUIRED")

        code, doc = self.post(trials, {"outcome": "HELPED", "detail": "这一次有用",
                                       "experience_ids": [self.exp_one],
                                       "expected_head": head(),
                                       "operation_key": "practice:trial:helped"})
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["data"]["method_state"], "TRIAL")

        code, doc = self.post(trials, {"outcome": "NOT_HELPED", "detail": "长文没用",
                                       "experience_ids": [self.exp_two],
                                       "expected_head": head(),
                                       "operation_key": "practice:trial:counter"})
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["data"]["method_state"], "CONTESTED")

        code, doc = self.post(trials, {"outcome": "UNKNOWN", "detail": "说不好",
                                       "experience_ids": [], "expected_head": head(),
                                       "operation_key": "practice:trial:unknown"})
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["data"]["method_state"], "CONTESTED")

        code, doc = self.post("/api/phone/practice/methods/%s/revisions" % method_id,
                              {"instruction": "先写五行，再展开。", "scope": "一页以内",
                               "uncertainty": "长文未知", "expected_head": head(),
                               "operation_key": "practice:revise:1"})
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["data"]["method_state"], "TENTATIVE")
        self.assertEqual(doc["data"]["revisions"], 1)
        self.assertEqual(doc["data"]["trials_preserved"], 3)

        code, view = self.call("GET", "/api/phone/practice/methods/%s" % method_id)
        self.assertEqual(len(view["data"]["counterexamples"]), 1)
        self.assertEqual(view["data"]["instruction"], "先写五行，再展开。")
        self.assertFalse(view["data"]["verified_skill"])

        code, doc = self.post("/api/phone/practice/methods/%s/retire" % method_id,
                              {"expected_head": head(), "operation_key": "practice:retire:1"})
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["data"]["method_state"], "RETIRED")
        code, view = self.call("GET", "/api/phone/practice/methods/%s" % method_id)
        self.assertEqual(view["data"]["method_state"], "RETIRED")
        self.assertTrue(view["data"]["body"].strip())
        rows = [row["action"] for row in self.journal()]
        for action in ("practice_method_create", "practice_trial", "practice_revise",
                       "practice_retire"):
            self.assertIn(action, rows)

    def test_trials_reject_an_unknown_outcome_and_a_client_state(self) -> None:
        code, doc = self.post("/api/phone/practice/methods",
                              {"title": "草案", "instruction": "先写三行",
                               "operation_key": "practice:method:2"})
        self.assertEqual(code, 200, doc)
        method_id = str(doc["data"]["artifact_id"])
        _code, view = self.call("GET", "/api/phone/practice/methods/%s" % method_id)
        head = view["data"]["head_version_id"]
        path = "/api/phone/practice/methods/%s/trials" % method_id
        code, doc = self.post(path, {"outcome": "SUPPORTED", "expected_head": head,
                                     "operation_key": "practice:trial:bad"})
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "PRACTICE_OUTCOME_INVALID")
        code, doc = self.post(path, {"outcome": "HELPED", "method_state": "SUPPORTED",
                                     "expected_head": head,
                                     "operation_key": "practice:trial:state"})
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "UNKNOWN_FIELD:method_state")

    def test_a_method_cannot_be_created_with_unreadable_evidence(self) -> None:
        code, doc = self.post("/api/phone/practice/methods",
                              {"title": "建不成", "instruction": "x",
                               "experience_ids": ["exp:missing"],
                               "operation_key": "practice:method:missing"})
        self.assertEqual(code, 409, doc)
        self.assertEqual(doc["reason_code"], "PRACTICE_EVIDENCE_UNREADABLE")
        self.assertEqual(self.writes(), [])

    def test_project_screen_cannot_rewrite_a_practice_document(self) -> None:
        artifact_id = self.review_id()
        code, doc = self.call("GET", "/api/phone/projects")
        self.assertEqual(code, 200, doc)
        row = [item for item in doc["projects"] if item["artifact_id"] == artifact_id]
        self.assertEqual(len(row), 1)
        # 普通项目屏看得见它，但明确标成回顾、不可续写。
        self.assertEqual(row[0]["artifact_type"], "review")
        self.assertFalse(row[0]["continuable"])
        code, doc = self.call("GET", "/api/phone/projects/%s" % artifact_id)
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["project"]["artifact_type"], "review")
        code, doc = self.post("/api/phone/projects/%s/commits" % artifact_id,
                              {"content": "普通项目屏写的一句话", "expected_head": "x",
                               "operation_key": "project:commit:practice"})
        self.assertEqual(code, 409, doc)
        self.assertEqual(doc["reason_code"], "PROJECT_IS_A_PRACTICE_DOCUMENT")

    def test_get_never_writes_on_the_owner(self) -> None:
        artifact_id = self.review_id()
        before = len(self.writes())
        for path in ("/api/phone/practice", "/api/phone/practice/reviews/%s" % artifact_id,
                     "/api/phone/memory", "/api/phone/memory/experiences"):
            code, _doc = self.call("GET", path)
            self.assertEqual(code, 200, path)
        self.assertEqual(len(self.writes()), before)


class PracticeGovernanceLeakApiTest(PracticeAPIBase):
    """治理挡下之后，HTTP 信封（任意嵌套字段）都不许带受治理的正文 / 指令 / 试用说明。"""

    QUESTION = "北极星问句-8f2c1d"
    BODY = "北极星正文-4b9e77"
    INSTRUCTION = "北极星做法-6a1f30"
    SCOPE = "北极星范围-2c7d55"
    UNCERTAINTY = "北极星未知-93ab41"
    TRIAL = "北极星试用说明-77de02"
    OLD = "北极星旧指令-1e5c88"

    def setUp(self) -> None:
        super().setUp()
        code, review = self.post("/api/phone/practice/reviews", {
            "question": self.QUESTION, "title": "回顾-治理", "experience_ids": [self.exp_one],
            "operation_key": "leak:review:1"})
        self.assertEqual(code, 200, review)
        self.review_id = str(review["data"]["artifact_id"])
        code, detail = self.call("GET", "/api/phone/practice/reviews/%s" % self.review_id)
        self.assertEqual(code, 200, detail)
        code, doc = self.post("/api/phone/practice/reviews/%s/documents" % self.review_id,
                              {"content": self.BODY,
                               "expected_head": detail["data"]["head_version_id"],
                               "operation_key": "leak:review:doc"})
        self.assertEqual(code, 200, doc)

        code, method = self.post("/api/phone/practice/methods", {
            "title": "做法-治理", "instruction": self.INSTRUCTION, "scope": self.SCOPE,
            "uncertainty": self.UNCERTAINTY, "experience_ids": [self.exp_one],
            "review_refs": [self.review_id], "operation_key": "leak:method:1"})
        self.assertEqual(code, 200, method)
        self.method_id = str(method["data"]["artifact_id"])

        code, view = self.call("GET", "/api/phone/practice/methods/%s" % self.method_id)
        self.assertEqual(code, 200, view)
        code, doc = self.post("/api/phone/practice/methods/%s/trials" % self.method_id,
                              {"outcome": "HELPED", "detail": self.TRIAL,
                               "experience_ids": [self.exp_one],
                               "expected_head": view["data"]["head_version_id"],
                               "operation_key": "leak:trial:1"})
        self.assertEqual(code, 200, doc)

        code, view = self.call("GET", "/api/phone/practice/methods/%s" % self.method_id)
        self.assertEqual(code, 200, view)
        code, doc = self.post("/api/phone/practice/methods/%s/revisions" % self.method_id,
                              {"instruction": self.OLD, "scope": self.SCOPE,
                               "uncertainty": self.UNCERTAINTY,
                               "expected_head": view["data"]["head_version_id"],
                               "operation_key": "leak:revise:1"})
        self.assertEqual(code, 200, doc)

        # 治理：撤回那条被引用的经历。
        self.memory.withdrawn.add(self.exp_one)

    def leaked(self, payload, *sentinels: str) -> list[str]:
        blob = json.dumps(payload, ensure_ascii=False)
        return [sentinel for sentinel in sentinels if sentinel in blob]

    def all_sentinels(self) -> tuple[str, ...]:
        return (self.QUESTION, self.BODY, self.INSTRUCTION, self.SCOPE, self.UNCERTAINTY,
                self.TRIAL, self.OLD)

    def test_the_review_detail_envelope_carries_no_governed_text(self) -> None:
        code, doc = self.call("GET", "/api/phone/practice/reviews/%s" % self.review_id)
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["data"]["governance"]["blocked"])
        self.assertFalse(doc["data"]["content_available"])
        leaked = self.leaked(doc, self.QUESTION, self.BODY)
        self.assertEqual(leaked, [], "回顾详情仍然漏出受治理的正文/问句：%s" % leaked)
        self.assertEqual(doc["data"]["project"]["title"], "回顾-治理")
        self.assertTrue(doc["data"]["project"]["head_version_id"])

    def test_the_method_detail_envelope_carries_no_governed_text(self) -> None:
        code, doc = self.call("GET", "/api/phone/practice/methods/%s" % self.method_id)
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["data"]["governance"]["blocked"])
        leaked = self.leaked(doc, *self.all_sentinels())
        self.assertEqual(leaked, [], "做法详情仍然漏出受治理的指令/试用说明：%s" % leaked)
        self.assertEqual(doc["data"]["project"]["title"], "做法-治理")
        self.assertEqual(doc["data"]["method_state"], "TENTATIVE")

    def test_the_project_surface_does_not_serve_a_blocked_practice_body(self) -> None:
        """普通项目面看得见这一件，但不替实践面把受治理的正文送出来。"""
        code, doc = self.call("GET", "/api/phone/projects/%s" % self.review_id)
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["project"]["artifact_type"], "review")
        self.assertFalse(doc["project"]["continuable"])
        leaked = self.leaked(doc, self.QUESTION, self.BODY, self.INSTRUCTION, self.OLD)
        self.assertEqual(leaked, [], "项目面仍然送出受治理的实践正文：%s" % leaked)
        self.assertFalse(doc["project"]["content_available"])

    def test_the_workbench_and_list_envelopes_carry_no_governed_text(self) -> None:
        for path in ("/api/phone/practice", "/api/phone/practice/methods"):
            code, doc = self.call("GET", path)
            self.assertEqual(code, 200, (path, doc))
            leaked = self.leaked(doc, *self.all_sentinels())
            self.assertEqual(leaked, [], "%s 仍然漏出受治理内容：%s" % (path, leaked))
        code, doc = self.call("GET", "/api/phone/practice")
        self.assertEqual(code, 200, doc)
        row = doc["data"]["reviews"][0]
        self.assertTrue(row["governance_blocked"])
        self.assertEqual(row["question"], "")
        method_row = doc["data"]["methods"][0]
        self.assertEqual(method_row["instruction"], "")
        self.assertEqual(method_row["uncertainty"], "")
        self.assertTrue(method_row["governance_blocked"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""v0.4 阶段 B：手机上的资料库与研究（HTTP 面，只测本轮新增的那几条路）。

这个文件测什么
--------------
``GET /api/phone/materials``（列表 + 检索）、``GET /api/phone/materials/<id>``、
``POST /api/phone/materials``（导入文本）、``GET /api/phone/research``、
``GET /api/phone/research/<id>``、``POST /api/phone/research``（从问题 + 具体来源创建）、
``POST .../research/<id>/updates``（CAS 续写）、``POST .../research/<id>/close``（归档），
以及普通 ``/projects`` 列表对"资料/研究不是可续写作品"的类型提示。

* 服务是真的：真实 ``PhoneService`` + 真实 loopback ``create_server``。owner 用
  ``test_v04_private_projects_stage.FakePort``（内存替身）顶替 Supply socket —— P 的职责只是
  转发、拒绝与记录来源，它不重算任何 owner 事实，所以这里测的是 P 自己那层。
* 断言只读 P 的行为与落盘的 ``phone/operations.jsonl``；Supply 的真实语义（Governance 预留、
  不可变版本）由 Supply 自己的测试负责。
* GET 是纯读：一次列表/详情之后，owner 侧不应出现任何写操作。
* 写真要 CSRF；请求体只收自己声明的那几个字段；嵌套的 ``source_refs`` 只收两个字段。
* 夹具根建在会话 scratch 下并**保留**（不删除、不清理），名字唯一。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
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
from test_v04_private_projects_stage import (  # noqa: E402
    FakePort,
    GRANT,
    ITEMS,
    READING,
    WORKSPACE,
)

CSRF = "research-csrf-token"
WRITE_CALLS = ("create_artifact", "commit_version", "archive_artifact")


def fixture_base() -> Path:
    override = os.environ.get("NYAIRO_RESEARCH_TEST_ROOT") or os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-research-pi") if override
            else (Path(tempfile.gettempdir()) / "nyairo-research-pi"))
    base.mkdir(parents=True, exist_ok=True)
    return base


_ROOT_SERIAL = 0


def unique_root(prefix: str) -> Path:
    global _ROOT_SERIAL
    base = fixture_base()
    _ROOT_SERIAL += 1
    root = base / ("%s%d-%d-%d" % (prefix, os.getpid(), time.time_ns(), _ROOT_SERIAL))
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
        "name": "research-fixture",
        "subject": "chiyo",
        "local_owner_uid": 0,
        "profile_home": str(root),
        "root": str(root),
        "world_home": str(world_home),
        "world_id": "w-research",
        "supply_socket": str(root / "run" / "supply-fixture.sock"),
        "private_items_artifact_id": ITEMS,
        "reading_artifact_id": READING,
        "artifact_grant_id": GRANT,
        "workspace_id": WORKSPACE,
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }


class ResearchAPIBase(unittest.TestCase):
    """One real loopback server per test, over one in-memory owner."""

    def setUp(self) -> None:
        self.root = unique_root("v04-research-api-")
        self.port = FakePort()
        self.port.seed(artifact_id=READING, title="她的阅读材料", content="第一页\n%%PAGE%%\n第二页")
        self.port.seed(artifact_id=ITEMS, title="她的私人日常事项", content='{"items":{}}')
        self.port.seed(artifact_id="artifact:" + "a" * 32, title="她自己的日常记录",
                       content="旧的一页。")
        self.instance = instance_for(self.root)
        self.service = phone_service.PhoneService(self.instance, environment={})
        # 只有这一处注入：这一份夹具没有 Supply socket，所以把 port 换成内存 owner。
        self.service._port = lambda: self.port
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
        return [name for name, _payload in self.port.calls if name in WRITE_CALLS]

    # -- fixtures ----------------------------------------------------------------------
    def import_material(self, key: str = "material:1") -> dict:
        code, doc = self.post("/api/phone/materials", {
            "title": "潮汐表（他抄的）", "text": "潮汐每天两次。\n第二行。",
            "source_kind": "user_supplied", "origin": "他手抄给我的一张纸",
            "operation_key": key})
        self.assertEqual(code, 200, doc)
        return doc["data"]["material"]

    def create_research(self, material: dict, key: str = "research:1") -> dict:
        code, doc = self.post("/api/phone/research", {
            "question": "潮汐为什么每天两次？",
            "source_refs": [{"artifact_id": material["artifact_id"],
                             "source_revision": material["source_revision"]}],
            "scope": "只讨论这张手抄纸上的内容", "operation_key": key})
        self.assertEqual(code, 200, doc)
        return doc["data"]["project"]


class MaterialAPITest(ResearchAPIBase):
    def test_list_and_search_are_pure_reads(self) -> None:
        code, doc = self.call("GET", "/api/phone/materials")
        self.assertEqual(code, 200)
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["status"], "ok")
        rows = doc["data"]["materials"]
        self.assertEqual([row["artifact_id"] for row in rows], [READING])
        self.assertTrue(rows[0]["read_only"])
        self.assertEqual(rows[0]["source_kind"], "reading_material")
        self.assertEqual(self.writes(), [])

        material = self.import_material()
        code, doc = self.call("GET", "/api/phone/materials?query=%E6%BD%AE%E6%B1%90")
        self.assertEqual(code, 200)
        ids = [row["artifact_id"] for row in doc["data"]["materials"]]
        self.assertEqual(ids, [material["artifact_id"]])
        code, doc = self.call("GET", "/api/phone/materials?query=zzz-nothing")
        self.assertEqual(doc["data"]["materials"], [])
        self.assertEqual(doc["data"]["count"], 0)
        self.assertEqual(self.writes(), ["create_artifact"])  # 只有导入那一次

    def test_query_is_fenced(self) -> None:
        code, doc = self.call("GET", "/api/phone/materials?bogus=1")
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "UNKNOWN_QUERY:bogus")
        code, doc = self.call("GET", "/api/phone/materials?query=a&query=b")
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "QUERY_REPEATED:query")
        code, doc = self.call("GET", "/api/phone/materials?limit=0")
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "LIMIT_INVALID")
        code, doc = self.call("GET", "/api/phone/materials?limit=999")
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "LIMIT_OUT_OF_RANGE")
        code, doc = self.call("GET", "/api/phone/research?query=x")
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "UNKNOWN_QUERY:query")

    def test_material_detail_returns_text_and_reading_is_not_a_research(self) -> None:
        material = self.import_material()
        code, doc = self.call("GET", "/api/phone/materials/%s" % material["artifact_id"])
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["data"]["material"]["text"], "潮汐每天两次。\n第二行。")
        self.assertEqual(doc["data"]["material"]["origin"], "他手抄给我的一张纸")
        self.assertEqual(doc["data"]["material"]["source_kind"], "user_supplied")
        self.assertEqual(self.writes(), ["create_artifact"])

        code, doc = self.call("GET", "/api/phone/research/%s" % material["artifact_id"])
        self.assertEqual(code, 404)
        self.assertEqual(doc["reason_code"], "ARTIFACT_IS_A_MATERIAL")
        code, doc = self.call("GET", "/api/phone/materials/%s" % READING)
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["data"]["material"]["text"], "第一页\n%%PAGE%%\n第二页")

    def test_import_needs_csrf_and_only_its_own_fields(self) -> None:
        body = {"title": "一张纸", "text": "正文", "source_kind": "fictional",
                "origin": "他明确说是虚构的", "operation_key": "material:csrf"}
        code, doc = self.post("/api/phone/materials", body, csrf=False)
        self.assertEqual(code, 403)
        self.assertEqual(doc["reason_code"], "CSRF_TOKEN_MISSING")

        code, doc = self.post("/api/phone/materials", dict(body, extra=1))
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "UNKNOWN_FIELD:extra")

        code, doc = self.post("/api/phone/materials",
                              dict(body, artifact_id="artifact:" + "b" * 32))
        self.assertEqual(code, 409)
        self.assertIn("CLIENT_IDENTITY_REFUSED", doc["reason_code"])

        code, doc = self.post("/api/phone/materials", dict(body, source_kind="web_scrape"))
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "SOURCE_KIND_INVALID")

        code, doc = self.post("/api/phone/materials", dict(body, text=""))
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "MATERIAL_TEXT_INVALID")

        code, doc = self.post("/api/phone/materials", dict(body, text="正文\x00尾巴"))
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "MATERIAL_TEXT_INVALID")

        code, doc = self.post("/api/phone/materials", body)
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["status"], "applied")
        self.assertEqual(doc["data"]["material"]["source_kind"], "fictional")
        rows = self.journal()
        self.assertEqual(rows[-1]["action"], "material_import")
        self.assertEqual(rows[-1]["operation_key"], "material:csrf")
        self.assertIn("create_artifact", self.writes())


class ResearchAPITest(ResearchAPIBase):
    def test_create_needs_sources_and_checks_them(self) -> None:
        material = self.import_material()
        code, doc = self.post("/api/phone/research", {
            "question": "没有来源", "operation_key": "research:none"})
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "SOURCE_REFS_INVALID")

        code, doc = self.post("/api/phone/research", {
            "question": "空来源", "source_refs": [], "operation_key": "research:empty"})
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "SOURCE_REFS_REQUIRED")

        code, doc = self.post("/api/phone/research", {
            "question": "多字段", "operation_key": "research:extra",
            "source_refs": [{"artifact_id": material["artifact_id"],
                             "source_revision": material["source_revision"], "price": 1}]})
        self.assertEqual(code, 400)
        self.assertIn("UNKNOWN_FIELD:source_refs", doc["reason_code"])

        code, doc = self.post("/api/phone/research", {
            "question": "读不到", "operation_key": "research:missing",
            "source_refs": [{"artifact_id": "artifact:not-here", "source_revision": "v1"}]})
        self.assertEqual(code, 409)
        self.assertEqual(doc["status"], "rejected")
        self.assertEqual(doc["reason_code"], "RESEARCH_SOURCE_UNAVAILABLE")
        self.assertEqual(doc["data"]["source_checks"][0]["status"], "UNAVAILABLE")

    def test_create_detail_update_close_round_trip(self) -> None:
        material = self.import_material()
        project = self.create_research(material)
        self.assertTrue(project["artifact_id"])
        self.assertTrue(project["content"].startswith("<!-- nyairo-research:v1 "))
        self.assertEqual(project["source_checks"][0]["status"], "CURRENT")
        self.assertTrue(project["not_memory"])

        code, doc = self.call("GET", "/api/phone/research")
        self.assertEqual(code, 200)
        ids = [row["artifact_id"] for row in doc["data"]["projects"]]
        self.assertEqual(ids, [project["artifact_id"]])
        self.assertNotIn("content", doc["data"]["projects"][0])

        code, doc = self.call("GET", "/api/phone/research/%s" % project["artifact_id"])
        self.assertEqual(code, 200, doc)
        detail = doc["data"]["project"]
        self.assertEqual(detail["question"], "潮汐为什么每天两次？")
        self.assertEqual(detail["source_checks"][0]["status"], "CURRENT")
        self.assertTrue(detail["sources_current"])
        self.assertTrue(detail["not_memory"])
        self.assertFalse(detail["stable_belief"])
        self.assertIn("## 暂定发现", detail["body"])

        # 缺 head 不发；缺字段被 P 挡住。
        path = "/api/phone/research/%s/updates" % project["artifact_id"]
        code, doc = self.post(path, {"content": "新的一版", "operation_key": "research:update:nohead"})
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "EXPECTED_HEAD_INVALID")
        stale = {"content": "新的一版", "expected_head": "version-moved-on",
                 "operation_key": "research:update:stale"}
        code, doc = self.post(path, stale)
        self.assertEqual(code, 409)
        self.assertEqual(doc["status"], "conflict")

        fresh = {"content": "## 暂定发现\n两次潮差大概一样。",
                 "expected_head": project["head_version_id"],
                 "operation_key": "research:update:1"}
        code, doc = self.post(path, fresh)
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["status"], "applied")
        self.assertEqual(doc["data"]["version_number"], 2)
        self.assertTrue(doc["data"]["marker_preserved"])
        self.assertIn("两次潮差大概一样。", doc["data"]["project"]["content"])
        self.assertTrue(doc["data"]["project"]["content"].startswith("<!-- nyairo-research:v1 "))

        head = doc["data"]["project"]["head_version_id"]
        close_path = "/api/phone/research/%s/close" % project["artifact_id"]
        code, doc = self.post(close_path, {"expected_revision": 2,
                                           "operation_key": "research:close:1"})
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["data"]["lifecycle"], "ARCHIVED")
        self.assertTrue(doc["data"]["content_kept"])
        code, doc = self.call("GET", "/api/phone/research/%s" % project["artifact_id"])
        self.assertEqual(doc["data"]["project"]["status"], "archived")
        self.assertIn("两次潮差大概一样。", doc["data"]["project"]["content"])

        code, doc = self.post(path, {"content": "关闭后再写", "expected_head": head,
                                     "operation_key": "research:update:2"})
        self.assertEqual(code, 409)
        self.assertEqual(doc["reason_code"], "RESEARCH_ARCHIVED")

        actions = [row["action"] for row in self.journal()]
        for expected in ("material_import", "research_create", "research_update", "research_close"):
            self.assertIn(expected, actions)

    def test_close_refuses_a_bad_revision_before_the_owner(self) -> None:
        material = self.import_material()
        project = self.create_research(material)
        writes_before = len(self.writes())
        path = "/api/phone/research/%s/close" % project["artifact_id"]
        for value in (0, -1, True, "2", 2.5):
            code, doc = self.post(path, {"expected_revision": value,
                                         "operation_key": "research:close:bad"})
            self.assertEqual(code, 400, value)
            self.assertEqual(doc["reason_code"], "EXPECTED_REVISION_INVALID")
        self.assertEqual(len(self.writes()), writes_before)

    def test_project_list_types_research_and_material_and_refuses_project_commits(self) -> None:
        material = self.import_material()
        project = self.create_research(material)
        code, doc = self.call("GET", "/api/phone/projects")
        self.assertEqual(code, 200, doc)
        rows = {row["artifact_id"]: row for row in doc["projects"]}
        self.assertIn(project["artifact_id"], rows)
        self.assertEqual(rows[project["artifact_id"]]["artifact_type"], "research")
        self.assertFalse(rows[project["artifact_id"]]["continuable"])
        self.assertIn(material["artifact_id"], rows)
        self.assertEqual(rows[material["artifact_id"]]["artifact_type"], "material")
        self.assertFalse(rows[material["artifact_id"]]["continuable"])
        self.assertNotIn(READING, rows)

        path = "/api/phone/projects/%s/commits" % project["artifact_id"]
        code, doc = self.post(path, {"content": "从这里硬改", "expected_head": project["head_version_id"],
                                     "operation_key": "project:commit:research"})
        self.assertEqual(code, 409)
        self.assertEqual(doc["reason_code"], "PROJECT_IS_A_RESEARCH")
        path = "/api/phone/projects/%s/commits" % material["artifact_id"]
        code, doc = self.post(path, {"content": "把资料改成作品", "expected_head": "whatever",
                                     "operation_key": "project:commit:material"})
        self.assertEqual(code, 409)
        self.assertEqual(doc["reason_code"], "PROJECT_IS_A_MATERIAL")

    def test_get_is_pure_read_even_with_a_research(self) -> None:
        material = self.import_material()
        project = self.create_research(material)
        writes = list(self.writes())
        self.call("GET", "/api/phone/materials")
        self.call("GET", "/api/phone/materials/%s" % material["artifact_id"])
        self.call("GET", "/api/phone/research")
        self.call("GET", "/api/phone/research/%s" % project["artifact_id"])
        self.call("GET", "/api/phone/projects")
        self.assertEqual(self.writes(), writes)


if __name__ == "__main__":
    unittest.main()

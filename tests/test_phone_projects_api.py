#!/usr/bin/env python3
"""v0.4 阶段 B：手机上的私人项目工作台（HTTP 面，只测本轮新增的那几条路）。

这个文件测什么
--------------
P 的项目面：``GET /api/phone/projects``、``GET /api/phone/projects/<id>``、
``POST .../projects``、``POST .../projects/<id>/commits``、``POST .../projects/<id>/close``。

* 服务是真的：真实 ``PhoneService`` + 真实 loopback ``create_server``。owner 用
  ``test_v04_private_projects_stage.FakePort``（内存替身）顶替 Supply socket —— P 的职责只是
  转发、拒绝与记录来源，它不重算任何 owner 事实，所以这里测的是 P 自己那层。
* 断言只读 P 的行为与落盘的 ``phone/operations.jsonl``；Supply 的真实语义（Governance 预留、
  不可变版本）由 Supply 自己的测试负责。
* GET 是纯读：一次列表/详情之后，owner 侧不应出现任何写操作。
* 写真要 CSRF；请求体只收自己声明的那几个字段；``expected_head`` / ``expected_revision`` 缺失即拒。
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

CSRF = "projects-csrf-token"


def fixture_base() -> Path:
    override = os.environ.get("NYAIRO_PROJECTS_TEST_ROOT") or os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-projects-pi") if override
            else (Path(tempfile.gettempdir()) / "nyairo-projects-pi"))
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
        "name": "projects-fixture",
        "subject": "chiyo",
        "local_owner_uid": 0,
        "profile_home": str(root),
        "root": str(root),
        "world_home": str(world_home),
        "world_id": "w-projects",
        "supply_socket": str(root / "run" / "supply-fixture.sock"),
        "private_items_artifact_id": ITEMS,
        "reading_artifact_id": READING,
        "artifact_grant_id": GRANT,
        "workspace_id": WORKSPACE,
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }


class ProjectsAPIBase(unittest.TestCase):
    """One real loopback server per test, over one in-memory owner."""

    def setUp(self) -> None:
        self.root = unique_root("v04-projects-api-")
        self.port = FakePort()
        self.port.seed(artifact_id=READING, title="她的阅读材料", content="第一页")
        self.port.seed(artifact_id=ITEMS, title="她的私人日常事项", content="{}")
        self.work_id = "artifact:" + "a" * 32
        self.work_head = self.port.seed(artifact_id=self.work_id, title="她自己的日常记录",
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
        return [name for name, _payload in self.port.calls
                if name in ("create_artifact", "commit_version", "archive_artifact")]


class ProjectsReadTest(ProjectsAPIBase):
    def test_list_is_a_pure_read_without_private_text(self) -> None:
        code, doc = self.call("GET", "/api/phone/projects")
        self.assertEqual(code, 200)
        self.assertTrue(doc["ok"])
        titles = [row["title"] for row in doc["projects"]]
        self.assertEqual(titles, ["她自己的日常记录"])
        self.assertNotIn("content", doc["projects"][0])
        self.assertFalse(doc["private_text_included"])
        # 材料与私人事项不是项目。
        ids = [row["artifact_id"] for row in doc["projects"]]
        self.assertNotIn(READING, ids)
        self.assertNotIn(ITEMS, ids)
        self.assertEqual(doc["counts"]["excluded"], 2)
        self.assertEqual(doc["excluded_artifact_ids"], sorted([ITEMS, READING]))
        # GET 零写：owner 只被问过列表。
        self.assertEqual(self.writes(), [])

    def test_detail_carries_text_head_and_honest_history(self) -> None:
        code, doc = self.call("GET", "/api/phone/projects/%s" % self.work_id)
        self.assertEqual(code, 200)
        project = doc["project"]
        self.assertEqual(project["title"], "她自己的日常记录")
        self.assertEqual(project["content"], "旧的一页。")
        self.assertEqual(project["head_version_id"], self.work_head)
        self.assertTrue(doc["private_text_included"])
        history = doc["history"]
        self.assertFalse(history["complete"])
        self.assertEqual(history["reason_code"], "PORT_LACKS_LIST_VERSIONS")
        self.assertEqual([row["version_id"] for row in history["entries"]], [self.work_head])
        self.assertEqual(self.writes(), [])

    def test_reading_material_is_not_served_as_a_project(self) -> None:
        code, doc = self.call("GET", "/api/phone/projects/%s" % READING)
        self.assertEqual(code, 404)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["reason_code"], "PROJECT_ARTIFACT_EXCLUDED")


class ProjectsWriteTest(ProjectsAPIBase):
    def test_create_needs_csrf_and_only_its_own_fields(self) -> None:
        body = {"title": "夜航手记", "content": "第一段。", "operation_key": "project:create:1"}
        code, doc = self.post("/api/phone/projects", body, csrf=False)
        self.assertEqual(code, 403)
        self.assertEqual(doc["reason_code"], "CSRF_TOKEN_MISSING")

        code, doc = self.post("/api/phone/projects", dict(body, extra=1))
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "UNKNOWN_FIELD:extra")

        code, doc = self.post("/api/phone/projects", dict(body, artifact_id=self.work_id))
        self.assertEqual(code, 409)
        self.assertIn("CLIENT_IDENTITY_REFUSED", doc["reason_code"])

        code, doc = self.post("/api/phone/projects", body)
        self.assertEqual(code, 200)
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["status"], "applied")
        artifact_id = doc["data"]["artifact_id"]
        self.assertTrue(artifact_id)
        self.assertEqual(doc["data"]["version_number"], 1)
        rows = self.journal()
        self.assertEqual(rows[-1]["action"], "project_create")
        self.assertEqual(rows[-1]["operation_key"], "project:create:1")
        # 真的落了 owner 的创建。
        self.assertIn("create_artifact", self.writes())

    def test_commit_requires_a_head_and_reports_conflict(self) -> None:
        path = "/api/phone/projects/%s/commits" % self.work_id
        code, doc = self.post(path, {"content": "新的一版", "operation_key": "project:commit:1"})
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "EXPECTED_HEAD_INVALID")

        stale = {"content": "新的一版", "expected_head": "version-moved-on",
                 "operation_key": "project:commit:stale"}
        code, doc = self.post(path, stale)
        self.assertEqual(code, 409)
        self.assertEqual(doc["status"], "rejected")

        fresh = {"content": "新的一版", "expected_head": self.work_head,
                 "operation_key": "project:commit:2"}
        code, doc = self.post(path, fresh)
        self.assertEqual(code, 200)
        self.assertEqual(doc["status"], "applied")
        self.assertEqual(doc["data"]["version_number"], 2)
        code, detail = self.call("GET", "/api/phone/projects/%s" % self.work_id)
        self.assertEqual(code, 200)
        self.assertEqual(detail["project"]["content"], "新的一版")
        self.assertEqual(detail["project"]["revision"], 2)

    def test_close_archives_and_keeps_the_text(self) -> None:
        path = "/api/phone/projects/%s/close" % self.work_id
        code, doc = self.post(path, {"expected_revision": 1, "operation_key": "project:close:1"})
        self.assertEqual(code, 200)
        self.assertEqual(doc["status"], "applied")
        self.assertEqual(doc["data"]["lifecycle"], "ARCHIVED")

        code, again = self.post(path, {"expected_revision": 2,
                                       "operation_key": "project:close:2"})
        self.assertEqual(code, 200)
        self.assertEqual(again["status"], "already_applied")

        code, detail = self.call("GET", "/api/phone/projects/%s" % self.work_id)
        self.assertEqual(code, 200)
        self.assertEqual(detail["project"]["status"], "archived")
        self.assertEqual(detail["project"]["content"], "旧的一页。")
        # 归档不是删除：owner 只收到 archive，没有 tombstone 之类的动作。
        self.assertIn("archive_artifact", self.writes())

    def test_bad_revision_is_refused_before_the_owner(self) -> None:
        path = "/api/phone/projects/%s/close" % self.work_id
        for value in (0, -1, True, "1", 1.5):
            code, doc = self.post(path, {"expected_revision": value,
                                         "operation_key": "project:close:bad"})
            self.assertEqual(code, 400, value)
            self.assertEqual(doc["reason_code"], "EXPECTED_REVISION_INVALID")
        self.assertEqual(self.writes(), [])


if __name__ == "__main__":
    unittest.main()

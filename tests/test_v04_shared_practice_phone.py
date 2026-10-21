#!/usr/bin/env python3
"""v0.4 阶段 C：手机共同实践（门面读半边 + HTTP 守卫）。

写语义（用户输入 / 结束怎么走）在 ``tests/test_v04_shared_practice_phone_transport.py`` 里：
用户那一半进本实例自己的 ``LocalContactInbox``，A 的工件只被手机读（pause 是 A 发布的 user 侧
状态动作）。这个文件只测两件事：

* **门面读半边**：``summary()`` / ``work()`` 把 A 的真实投影搬成界面要的形状（材料与版本、
  双方贡献、作品正文、版本历史、权限边界），坏数据具名，不编；
* **HTTP 守卫**：Host / Origin / Sec-Fetch / CSRF、身份字段按名拒绝、未知字段、resume 400、
  非 pause 不带正文、坏 id 不猜。

夹具：``project_port`` 是**同一位 owner 的端口对象**（A 自己的 ``FakePort``），A 的服务由 A 的
factory 真建（``actor="user"``）；主体回应在种子数据里由 Life 角色 + 经核对来源写入，
**手机没有写过她那一半**。夹具根建在会话 scratch 并保留。
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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import shared_practice_service as sps  # noqa: E402
import test_v04_shared_practice_service as fx  # noqa: E402
from components.phone import practice_shared as phone_practice  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

API = "/api/phone"
CSRF = "shared-practice-csrf"
MATERIAL = "artifact:material-bound"
SUBJECT = "chiyo"
TITLE = "一起把第三页排一遍"
OPENING = "先把第二段拆开，我这一段是这样写的。"
SECOND = "第二段我改了一遍，你看看还挤不挤。"


# ---------------------------------------------------------------------------------------------
# fixtures（保留）
# ---------------------------------------------------------------------------------------------


def fixture_base() -> Path:
    override = (os.environ.get("NYAIRO_PHONE_SHARED_PRACTICE_ROOT")
                or os.environ.get("PI_SCRATCH_DIR"))
    base = ((Path(override) / "nyairo-phone-shared-practice") if override
            else (Path(os.environ.get("TEMP") or "/tmp") / "nyairo-phone-shared-practice"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def unique_root(prefix: str) -> Path:
    root = fixture_base() / ("%s%d-%d" % (prefix, os.getpid(), time.time_ns()))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def directory_snapshot(root: Path) -> dict:
    out = {}
    for path in sorted(Path(root).rglob("*")):
        try:
            info = path.stat()
        except OSError:
            out[str(path.relative_to(root))] = ("MISSING", 0)
            continue
        out[str(path.relative_to(root))] = (info.st_size, info.st_mtime_ns)
    return out


def instance_for(root: Path) -> dict:
    world_home = root / "one-day" / "world-home"
    world_home.mkdir(parents=True, exist_ok=True)
    (root / "one-day" / "life" / "state").mkdir(parents=True, exist_ok=True)
    (root / "run").mkdir(parents=True, exist_ok=True)
    socket_path = root / "run" / "supply.sock"
    socket_path.write_text("", encoding="utf-8")
    return {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "shared-practice-read",
        "subject": SUBJECT,
        "root": str(root),
        "profile_home": str(root),
        "world_home": str(world_home),
        "world_id": "w-practice",
        "supply_socket": str(socket_path),
        "private_items_artifact_id": "artifact:" + "1" * 32,
        "artifact_grant_id": "grant:" + "2" * 32,
        "workspace_id": "workspace:" + "3" * 32,
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }


def service_for(root: Path, port: Any) -> Any:
    instance = instance_for(root)
    return phone_service.PhoneService(
        instance, environment={"CHIYO_WORLD_HOME": str(instance["world_home"])},
        project_port=port)


def seeded_practice(port: Any) -> dict:
    """A real practice opened by the user role, with one real reply from the Life role."""

    user = fx.build(port, actor=sps.CALLER_USER)
    opened = user.open_practice(title=TITLE, opening_text=OPENING, material_refs=[MATERIAL],
                                operation_key="open:seed")
    if not opened.get("ok"):
        raise AssertionError("fixture could not open: %r" % (opened,))
    practice_id = opened["data"]["session_id"]
    head = user.read_practice(practice_id)["data"]["project"]["head_version_id"]
    user.submit_user_contribution(practice_id, text=SECOND, expected_head=head,
                                  operation_key="user:seed:1")
    head = user.read_practice(practice_id)["data"]["project"]["head_version_id"]
    verifier = fx.FakeSourceVerifier()
    verifier.add_decision()
    life = fx.build(port, actor=sps.CALLER_LIFE, verifier=verifier)
    her = life.submit_subject_response(practice_id, text="我看了，第二段可以再拆细一点。",
                                       basis={"decision_ref": fx.DECISION_REF},
                                       expected_head=head, operation_key="life:seed:1")
    if not her.get("ok"):
        raise AssertionError("fixture could not write her reply: %r" % (her,))
    return {"practice_id": practice_id, "user": user, "life": life}


# ---------------------------------------------------------------------------------------------
# 1. 门面：读半边
# ---------------------------------------------------------------------------------------------


class FacadeReadTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = unique_root("v04-practice-read-")
        self.port = fx.seeded_port()
        self.service = service_for(self.root, self.port)
        self.facade = self.service._shared_practice_service()
        self.seed = seeded_practice(self.port)

    def test_describe_names_the_published_interface_and_the_transport(self) -> None:
        described = self.facade.describe()
        self.assertTrue(described["bound"], described)
        self.assertEqual(described["interface_gaps"], [])
        methods = [row["method"] for row in described["required_interface"]]
        self.assertEqual(methods, ["open_practice", "list_practices", "read_practice",
                                   "submit_user_contribution", "pause_practice", "end_practice",
                                   "context_view"])
        self.assertEqual(described["actions"], ["submit", "modify", "pause", "end"])
        self.assertNotIn("resume", described["actions"])
        self.assertEqual(described["transport"]["kind"], "local_contact_inbox")
        self.assertEqual(described["acceptance_owner"], "life")
        self.assertFalse(described["phone_writes_user_contribution"])
        self.assertFalse(described["phone_ends_practice"])
        self.assertFalse(described["writes_subject_contribution"])
        self.assertFalse(described["accepts_for_subject"])
        self.assertFalse(described["starts_life_daemon"])

    def test_the_facade_has_no_path_that_writes_her_half(self) -> None:
        for name in ("submit_subject_response", "submit_subject_contribution",
                     "commit_work_text", "resume_practice", "reopen_practice",
                     "reconfirm_materials"):
            self.assertFalse(hasattr(self.facade, name), "手机不该有 %s" % name)
        for name in phone_practice.LIFE_ONLY_OPERATIONS:
            self.assertFalse(hasattr(self.facade, name), "手机不该有 %s" % name)

    def test_summary_projects_identity_state_and_counts_without_any_body(self) -> None:
        answer = self.facade.summary()
        self.assertTrue(answer["ok"], answer)
        data = answer["data"]
        self.assertFalse(data["private_text_included"])
        self.assertTrue(data["transport"]["bound"])
        rows = data["practices"]
        self.assertEqual([row["practice_id"] for row in rows], [self.seed["practice_id"]])
        row = rows[0]
        self.assertEqual(row["title"], TITLE)
        self.assertEqual(row["state"], "OPEN")
        self.assertTrue(row["open"])
        self.assertTrue(row["source_readable"])
        # 列表只给身份、状态与计数；材料标题是详情才有的投影（A 的 list 摘要不带标题）。
        self.assertEqual(row["source_artifact_id"], MATERIAL)
        self.assertEqual(row["subject_rounds"], 1)
        self.assertNotIn(SECOND, json.dumps(answer, ensure_ascii=False), "列表不投影正文")

    def test_work_separates_both_sides_and_shows_the_real_versions(self) -> None:
        answer = self.facade.work(self.seed["practice_id"])
        self.assertTrue(answer["ok"], answer)
        data = answer["data"]
        self.assertEqual(data["practice"]["title"], TITLE)
        self.assertEqual(data["practice"]["joint_state"], "JOINT")
        self.assertEqual(data["material"]["artifact_id"], MATERIAL)
        self.assertTrue(data["material"]["readable"])
        self.assertEqual(data["work"]["text"], OPENING, "第一轮就是共同作品的正文")
        rows = data["contributions"]
        self.assertEqual([row["by"] for row in rows], ["user", "subject"])
        self.assertEqual(rows[0]["text"], SECOND)
        self.assertEqual(rows[1]["by_text"], "她")
        self.assertTrue(rows[1]["basis_verified"], "她的回应带着经核对的来源")
        self.assertTrue(rows[1]["decision_ref"].startswith("dec:"))
        self.assertEqual(data["user_contributions"], 1)
        self.assertEqual(data["subject_contributions"], 1)
        self.assertTrue(data["permissions"]["subject_contribution_visible"])
        self.assertFalse(data["permissions"]["may_write_subject_contribution"])
        self.assertFalse(data["permissions"]["may_accept_for_subject"])
        self.assertEqual(data["permissions"]["actions"],
                         ["submit", "modify", "pause", "end"])
        self.assertGreaterEqual(len(data["versions"]), 3, "每次写入都是 owner 的一版")
        for row in data["versions"]:
            self.assertEqual(row["text"], "", "版本历史不投影正文")
        self.assertTrue([row for row in data["versions"] if row["is_head"]])

    def test_reading_a_plain_artifact_is_not_a_practice(self) -> None:
        answer = self.facade.work(MATERIAL)
        self.assertFalse(answer["ok"], answer)
        self.assertEqual(answer["reason_code"], sps.REASON_NOT_A_SESSION)

    def test_a_broken_marker_is_refused_instead_of_guessed(self) -> None:
        practice_id = self.seed["practice_id"]
        projects = self.service._shared_practice_service()._service.projects
        current = projects.read_project(practice_id)
        self.assertTrue(current.get("ok"), current)
        broken = "<!-- nyairo-shared-practice:v1 {not json} -->\n" + OPENING
        projects.commit_project(artifact_id=practice_id, content=broken,
                                expected_head=current["project"]["head_version_id"],
                                operation_key="fixture:break-marker:1")
        answer = self.facade.work(practice_id)
        self.assertFalse(answer["ok"], answer)
        self.assertIn(answer["reason_code"],
                      (sps.REASON_MARKER_INVALID, sps.REASON_NOT_A_SESSION))
        listing = self.facade.summary()
        self.assertTrue(listing["ok"], listing)
        self.assertEqual(listing["data"]["practices"], [], "坏数据不进列表")

    def test_resume_is_refused_by_name_in_both_layers(self) -> None:
        answer = self.facade.act(practice_id=self.seed["practice_id"], action="resume",
                                 expected_head="x", operation_key="practice_op:resume")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "SHARED_PRACTICE_ACTION_REFUSED")
        self.assertIn("resume", answer["data"]["subject_only_actions"])
        also = self.facade.resume(practice_id=self.seed["practice_id"], expected_head="x",
                                  operation_key="practice_op:resume2")
        self.assertEqual(also["reason_code"], "SHARED_PRACTICE_ACTION_UNSUPPORTED")

    def test_an_unbound_owner_is_a_named_refusal(self) -> None:
        facade = phone_practice.PhoneSharedPractice(subject=SUBJECT)
        self.assertFalse(facade.describe()["bound"])
        answer = facade.summary()
        self.assertEqual(answer["reason_code"], "SHARED_PRACTICE_UNBOUND")
        self.assertIn("read_practice", json.dumps(answer["data"]["interface"]))
        self.assertFalse(answer["data"]["accepted"])

    def test_a_wrongly_named_owner_is_refused_by_name_and_never_called(self) -> None:
        calls: list[str] = []

        class WrongNames:
            def list(self) -> dict:
                calls.append("list")
                return {"ok": True, "status": "ok", "data": {"sessions": []}}

        facade = phone_practice.PhoneSharedPractice(service=WrongNames())
        self.assertFalse(facade.describe()["bound"])
        gaps = {row["op"]: row["missing"] for row in facade.describe()["interface_gaps"]}
        self.assertEqual(gaps.get("list"), "method")
        self.assertEqual(facade.summary()["reason_code"],
                         "SHARED_PRACTICE_INTERFACE_MISMATCH")
        self.assertEqual(calls, [], "核不过就不许调用 owner")


# ---------------------------------------------------------------------------------------------
# 2. HTTP 守卫（真实 loopback / 真实装配）
# ---------------------------------------------------------------------------------------------


class HTTPGuardTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = unique_root("v04-practice-http-guard-")
        self.port = fx.seeded_port()
        self.service = service_for(self.root, self.port)
        self.httpd = phone_server.create_server(self.service, csrf_token=CSRF,
                                                port=free_port())
        self.base_url = "http://127.0.0.1:%d" % int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        if getattr(self, "httpd", None) is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if getattr(self, "thread", None) is not None:
            self.thread.join(timeout=10)

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

    def post(self, path: str, body, *, csrf: bool = True, headers=None) -> tuple[int, dict]:
        extra = {"X-CSRF-Token": CSRF} if csrf else {}
        extra.update(headers or {})
        return self.call("POST", path, body, headers=extra)

    def open_body(self, **extra) -> dict:
        body = {"source_artifact_id": MATERIAL, "source_version_id": "v1", "title": TITLE,
                "text": OPENING, "operation_key": "practice_op:guard"}
        body.update(extra)
        return body

    def test_guards_refuse_before_the_owner(self) -> None:
        before = directory_snapshot(self.root)
        code, doc = self.post(API + "/shared-practice", self.open_body(), csrf=False)
        self.assertEqual(code, 403, doc)
        self.assertEqual(doc["reason_code"], "CSRF_TOKEN_MISSING")
        code, doc = self.post(API + "/shared-practice", self.open_body(),
                              headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403, doc)
        self.assertEqual(doc["reason_code"], "ORIGIN_NOT_ALLOWED")
        code, doc = self.call("GET", API + "/shared-practice",
                              headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(code, 403, doc)
        self.assertEqual(doc["reason_code"], "CROSS_SITE_REFUSED")
        self.assertEqual(directory_snapshot(self.root), before)

    def test_identity_and_her_side_fields_are_refused_by_name(self) -> None:
        for field, value in (("subject", "someone"), ("contributor", "subject"),
                             ("accepted", True), ("state", "ENDED"), ("home", "/tmp/x"),
                             ("path", "/etc/passwd"), ("url", "https://example.com"),
                             ("channel", "telegram")):
            code, doc = self.post(API + "/shared-practice", self.open_body(**{field: value}))
            self.assertEqual(code, 409, (field, doc))
            self.assertIn(field, json.dumps(doc, ensure_ascii=False))

    def test_unknown_and_missing_fields_are_not_silently_dropped(self) -> None:
        code, doc = self.post(API + "/shared-practice", self.open_body(whatever=1))
        self.assertEqual(code, 400, doc)
        self.assertIn("whatever", json.dumps(doc, ensure_ascii=False))
        for missing in ("title", "text"):
            body = self.open_body()
            body.pop(missing)
            code, doc = self.post(API + "/shared-practice", body)
            self.assertEqual(code, 400, (missing, doc))

    def test_resume_and_bad_field_combinations_are_refused(self) -> None:
        practice_id = "artifact:" + "a" * 32
        path = API + "/shared-practice/works/" + practice_id + "/actions"
        for action in ("resume", "reopen"):
            code, doc = self.post(path, {"action": action, "expected_head": "h",
                                         "operation_key": "practice_op:" + action})
            self.assertEqual(code, 400, doc)
            self.assertEqual(doc["reason_code"], "SHARED_PRACTICE_ACTION_UNSUPPORTED")
        code, doc = self.post(path, {"action": "pause", "expected_head": "h",
                                     "text": "偷偷写她的话",
                                     "operation_key": "practice_op:pause-text"})
        self.assertEqual(code, 400, doc)
        self.assertIn("text", json.dumps(doc, ensure_ascii=False))
        code, doc = self.post(path, {"action": "submit", "text": SECOND,
                                     "reason": "夹带理由",
                                     "operation_key": "practice_op:submit-reason"})
        self.assertEqual(code, 400, doc)
        self.assertIn("reason", json.dumps(doc, ensure_ascii=False))

    def test_pause_needs_the_head_it_read(self) -> None:
        practice_id = "artifact:" + "a" * 32
        path = API + "/shared-practice/works/" + practice_id + "/actions"
        code, doc = self.post(path, {"action": "pause", "operation_key": "practice_op:no-head"})
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "SHARED_PRACTICE_HEAD_REQUIRED")

    def test_bad_ids_and_plain_artifacts_are_named(self) -> None:
        code, doc = self.call("GET", API + "/shared-practice/works/" + MATERIAL)
        self.assertEqual(code, 409, doc)
        self.assertEqual(doc["reason_code"], sps.REASON_NOT_A_SESSION)
        code, doc = self.call("GET", API + "/shared-practice/works/" + "a" * 240)
        self.assertEqual(code, 404, doc)

    def test_get_list_and_work_are_pure_reads(self) -> None:
        seed = seeded_practice(self.port)
        before = directory_snapshot(self.root)
        code, doc = self.call("GET", API + "/shared-practice")
        self.assertEqual(code, 200, doc)
        self.assertEqual([row["practice_id"] for row in doc["data"]["practices"]],
                         [seed["practice_id"]])
        code, doc = self.call("GET", API + "/shared-practice/works/" + seed["practice_id"])
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["data"]["work"]["text"], OPENING)
        self.assertEqual([row["by"] for row in doc["data"]["contributions"]],
                         ["user", "subject"])
        self.assertEqual(directory_snapshot(self.root), before, "GET 必须零写")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""v0.4 阶段 C（续）：**正式装配** + 用户输入走传输（不再由手机直接写 A）。

这个文件测什么
--------------
1. **装配**：``PhoneService(instance, project_port=…)`` -- 不注入任何 ``shared_practice=`` 替身 --
   由 A 自己的 factory（``shared_practice_service_from_env``）以 ``actor="user"`` 建出 owner；
   绑不上就具名（``SHARED_PRACTICE_UNBOUND`` / ``SHARED_PRACTICE_BINDINGS_MISSING``）。
2. **传输**：用户开一次 = 一条 ``CO_PRESENCE_INVITATION``（带 ``material_refs``，**不建 A 工件**）；
   用户说话 = ``LOCAL_MESSAGE``；结束 = ``CO_PRESENCE_END``。A 里不因为手机而多出用户贡献
   （那是正常 Life 处理事件时写的），手机也不自己把作品标成 ENDED。
3. **原键**：同一个 ``operation_key`` 重试只有一条事件；``GET .../operations/<key>`` 是 B 的只读对账。
4. **pause** 仍然是 A 发布的 user 侧动作（只停这件作品的追加）。

证据分级（照实写）
------------------
* ``project_port`` 是**同一位 owner 的端口对象**（``FakePort``，A 自己契约用例同一份），
  不是「假 A 服务」：``SharedPracticeService`` 由 A 的 factory 真建、角色是真 ``user``。
* 传输是**真的** ``LocalContactInbox``（真 JSON 账本、真事件、真 operation 读），落在本实例自己的
  state root 下；夹具根建在会话 scratch 并保留。
* **没有**跑正常 Life runner、没有跑模型：所以这里能证明的是「事件真的进了她的来信箱、A 没被手机写」，
  不能宣称「体验整链已完成」（那是父级真装配 + 真 runner 的验收）。
* 不联网、不连原实例、不删任何夹具。

运行：``py -3.13 -X utf8 -m unittest tests.test_v04_shared_practice_phone_transport``
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
# fixtures（保留：一个测试一个唯一根）
# ---------------------------------------------------------------------------------------------


def fixture_base() -> Path:
    override = (os.environ.get("NYAIRO_PHONE_SHARED_PRACTICE_ROOT")
                or os.environ.get("PI_SCRATCH_DIR"))
    base = ((Path(override) / "nyairo-phone-practice-transport") if override
            else (Path(os.environ.get("TEMP") or "/tmp") / "nyairo-phone-practice-transport"))
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


def instance_for(root: Path, *, grant: bool = True) -> dict:
    world_home = root / "one-day" / "world-home"
    world_home.mkdir(parents=True, exist_ok=True)
    (root / "one-day" / "life" / "state").mkdir(parents=True, exist_ok=True)
    (root / "run").mkdir(parents=True, exist_ok=True)
    socket_path = root / "run" / "supply.sock"
    socket_path.write_text("", encoding="utf-8")
    payload = {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "shared-practice-transport",
        "subject": SUBJECT,
        "root": str(root),
        "profile_home": str(root),
        "world_home": str(world_home),
        "world_id": "w-practice",
        "supply_socket": str(socket_path),
        "private_items_artifact_id": "artifact:" + "1" * 32,
        "artifact_grant_id": "grant:" + "2" * 32 if grant else "",
        "workspace_id": "workspace:" + "3" * 32,
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }
    return payload


def service_for(root: Path, port: Any, *, grant: bool = True) -> Any:
    """The production wiring: instance bindings + a real port.  No injected A owner."""

    instance = instance_for(root, grant=grant)
    return phone_service.PhoneService(
        instance, environment={"CHIYO_WORLD_HOME": str(instance["world_home"])},
        project_port=port)


def user_side(port: Any) -> Any:
    """A's own user-role service, used only to *seed* an artifact in the pause test."""

    return fx.build(port, actor=sps.CALLER_USER)


# ---------------------------------------------------------------------------------------------
# 1. 正式装配
# ---------------------------------------------------------------------------------------------


class CompositionTests(unittest.TestCase):

    def test_a_is_built_from_the_instance_with_actor_user(self) -> None:
        root = unique_root("v04-practice-compose-")
        port = fx.seeded_port()
        service = service_for(root, port)
        answer = service.shared_practices()
        self.assertTrue(answer["ok"], answer)
        data = answer["data"]
        self.assertTrue(data["service"]["bound"], data["service"])
        self.assertEqual(data["service"]["interface_gaps"], [])
        self.assertTrue(data["service"]["transport"]["bound"], data["service"])
        self.assertEqual(data["service"]["transport"]["owner"], "life")
        self.assertEqual(data["practices"], [])
        # 真的是 A 的 user 角色在跑（不是手机自己写的一套）。
        owner = service._shared_practice_service()._service
        self.assertEqual(owner.actor, sps.CALLER_USER)
        self.assertEqual(owner.subject_id, SUBJECT)

    def test_explicit_reading_source_is_readable_but_private_ledger_stays_excluded(self):
        from test_v04_shared_practice_phone import seeded_practice
        root = unique_root("v04-practice-reading-")
        port = fx.seeded_port()
        service = service_for(root, port)
        service.reading_artifact_id = MATERIAL
        seed = seeded_practice(port)
        got = service.shared_practice(seed["practice_id"])
        self.assertTrue(got["ok"], got)
        self.assertFalse(got["data"]["governance"]["blocked"])
        self.assertTrue(any(row["is_subject"] and row["text"]
                            for row in got["data"]["contributions"]))
        owner = service._shared_practice_service()._service
        self.assertIn(service.private_items_artifact_id, owner.projects.excluded_artifact_ids)
        self.assertNotIn(MATERIAL, owner.projects.excluded_artifact_ids)

    def test_a_half_wired_instance_is_named_not_faked(self) -> None:
        root = unique_root("v04-practice-unbound-")
        service = service_for(root, fx.seeded_port(), grant=False)
        answer = service.shared_practices()
        self.assertFalse(answer["ok"], answer)
        self.assertEqual(answer["reason_code"], "SHARED_PRACTICE_UNBOUND")
        described = answer["data"]["service"]
        self.assertFalse(described["bound"])
        self.assertIn("SHARED_PRACTICE_BINDINGS_MISSING",
                      json.dumps(described, ensure_ascii=False))


# ---------------------------------------------------------------------------------------------
# 2. 传输：用户那一半进她的来信箱，A 不被手机写
# ---------------------------------------------------------------------------------------------


class TransportTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = unique_root("v04-practice-transport-")
        self.port = fx.seeded_port()
        self.service = service_for(self.root, self.port)
        self.facade = self.service._shared_practice_service()

    def events(self) -> list[dict]:
        answer = self.service.contact_events(limit=20)
        self.assertTrue(answer["ok"], answer)
        return answer["data"]["events"]

    def test_open_is_an_invitation_with_materials_and_creates_no_artifact(self) -> None:
        answer = self.facade.open_practice(source_artifact_id=MATERIAL,
                                           source_version_id="version-material-bound-1",
                                           title=TITLE, text=OPENING,
                                           operation_key="practice_op:open")
        self.assertTrue(answer["ok"], answer)
        data = answer["data"]
        self.assertEqual(data["action"], "open")
        self.assertEqual(data["event_kind"], "CO_PRESENCE_INVITATION")
        self.assertTrue(data["queued"], data)
        self.assertTrue(data["submitted"])
        self.assertFalse(data["accepted"], "邀请不是接纳")
        self.assertFalse(data["phone_wrote_practice"])
        self.assertEqual(data["material_refs"], [MATERIAL])
        self.assertEqual(data["practice_id"], "", "作品要等她那边处理完才存在")

        events = self.events()
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0]["event_kind"], "CO_PRESENCE_INVITATION")
        self.assertEqual(events[0]["processing_state"], "PENDING")
        # A 里没有多出任何作品：手机没有 parallel artifact。
        self.assertEqual(self.service.shared_practices()["data"]["practices"], [])

    def test_a_user_turn_is_a_message_and_writes_no_contribution(self) -> None:
        self.facade.open_practice(source_artifact_id=MATERIAL,
                                  source_version_id="v", title=TITLE, text=OPENING,
                                  operation_key="practice_op:open")
        answer = self.facade.submit_user(practice_id="artifact:" + "a" * 32, text=SECOND,
                                         operation_key="practice_op:msg1")
        self.assertTrue(answer["ok"], answer)
        data = answer["data"]
        self.assertEqual(data["action"], "submit")
        self.assertEqual(data["event_kind"], "LOCAL_MESSAGE")
        self.assertTrue(data["queued"])
        self.assertFalse(data["user_contribution_written"],
                         "手机不把用户贡献写进 A（那是正常 Life 处理事件时做的）")
        self.assertFalse(data["subject_responded"])
        self.assertFalse(data["accepted"])
        kinds = [row["event_kind"] for row in self.events()]
        self.assertEqual(sorted(kinds), ["CO_PRESENCE_INVITATION", "LOCAL_MESSAGE"])

    def test_one_operation_key_is_one_event_across_retry_and_query(self) -> None:
        body = dict(practice_id="artifact:" + "a" * 32, text=SECOND,
                    operation_key="practice_op:same")
        first = self.facade.submit_user(**body)
        again = self.facade.submit_user(**body)
        self.assertTrue(first["ok"], first)
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["data"]["event_ref"], first["data"]["event_ref"],
                         "同一个键只落一条事件")
        messages = [row for row in self.events() if row["event_kind"] == "LOCAL_MESSAGE"]
        self.assertEqual(len(messages), 1, messages)

        read = self.service.shared_practice_operation("practice_op:same")
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["status"], "applied")
        self.assertEqual(read["data"]["event_ref"], first["data"]["event_ref"])
        self.assertTrue(read["data"]["recorded"])
        self.assertEqual(read["data"]["event_kind"], "LOCAL_MESSAGE")

        absent = self.service.shared_practice_operation("practice_op:never")
        self.assertTrue(absent["ok"], absent)
        self.assertEqual(absent["status"], "absent")
        self.assertFalse(absent["data"]["recorded"])
        self.assertTrue(absent["data"]["retryable"], "没落地的键可以继续用")

    def test_end_without_a_known_invitation_is_a_named_refusal(self) -> None:
        before = self.events()
        answer = self.facade.end(practice_id="artifact:" + "a" * 32,
                                 operation_key="practice_op:end", reason="先到这里")
        self.assertFalse(answer["ok"], answer)
        self.assertEqual(answer["reason_code"], "SHARED_PRACTICE_INVITATION_UNKNOWN")
        self.assertEqual(self.events(), before, "找不到邀请就不写 END 事件，也不改作品")

    def test_end_with_a_binding_sends_an_explicit_end_event(self) -> None:
        opened = self.facade.open_practice(source_artifact_id=MATERIAL,
            source_version_id="version-material-bound-1", title=TITLE, text=OPENING,
            operation_key="practice_op:bound-invite")
        ref = opened["data"]["event_ref"]
        contact = self.service._contact_service()
        inbox = contact._inbox_or_raise()
        claim = inbox.claim(ref, consumer="test:normal-life")
        self.assertTrue(claim["ok"], claim)
        accepted = inbox.record_result(ref, outcome="ACCEPTED",
            claim_token=claim["claim_token"], reason_code="CO_PRESENCE_ACCEPTED")
        self.assertTrue(accepted["ok"], accepted)
        event = contact.event(ref)["data"]["event"]
        pid = "artifact:" + "a" * 32
        contact._session_path.write_text(json.dumps({"state": "OPEN",
            "invitation_id": event["invitation_id"], "practice_session_ref": pid,
            "practice_invitation_event_ref": ref}), encoding="utf-8")
        before = directory_snapshot(self.root)
        self.assertEqual(self.facade._invitation_for(pid)["event_ref"], ref)
        self.assertEqual(directory_snapshot(self.root), before, "binding lookup is pure")
        self.assertEqual(self.facade._invitation_for("artifact:other"), {})
        answer = self.facade.end(practice_id=pid,
            operation_key="practice_op:end2", reason="先到这里")
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(answer["data"]["queued"])
        ends = [row for row in self.events() if row["event_kind"] == "CO_PRESENCE_END"]
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0]["invitation_id"], event["invitation_id"])

    def test_pause_still_only_touches_the_artifact(self) -> None:
        seeded = user_side(self.port)
        opened = seeded.open_practice(title=TITLE, opening_text=OPENING,
                                      material_refs=[MATERIAL], operation_key="open:seed")
        self.assertTrue(opened["ok"], opened)
        practice_id = opened["data"]["session_id"]
        self.assertEqual([row["practice_id"] for row in
                          self.service.shared_practices()["data"]["practices"]], [practice_id])

        head = self.service.shared_practice(practice_id)["data"]["practice"]["head_version_id"]
        before = self.events()
        answer = self.service.shared_practice_act(practice_id=practice_id, action="pause",
                                                  expected_head=head,
                                                  operation_key="practice_op:pause")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["data"]["state"], "PAUSED")
        self.assertEqual(self.events(), before, "暂停不进她的来信箱")


# ---------------------------------------------------------------------------------------------
# 3. 真实 HTTP 面：同一条装配 + 同一条传输
# ---------------------------------------------------------------------------------------------


class TransportHTTPTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = unique_root("v04-practice-http-")
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

    def test_open_over_http_is_a_queued_invitation_and_get_is_pure(self) -> None:
        code, doc = self.post(API + "/shared-practice",
                              {"source_artifact_id": MATERIAL, "source_version_id": "v1",
                               "title": TITLE, "text": OPENING,
                               "operation_key": "practice_op:http-open"})
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        data = doc["data"]
        self.assertTrue(data["queued"], data)
        self.assertEqual(data["event_kind"], "CO_PRESENCE_INVITATION")
        self.assertFalse(data["accepted"])
        self.assertEqual(data["practice_id"], "")

        before = directory_snapshot(self.root)
        code, doc = self.call("GET", API + "/shared-practice")
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        self.assertEqual(doc["data"]["practices"], [])
        self.assertTrue(doc["data"]["transport"]["bound"])
        self.assertEqual(directory_snapshot(self.root), before, "GET 必须零写")

    def test_submit_over_http_keeps_the_key_and_operation_reads_applied(self) -> None:
        practice_id = "artifact:" + "a" * 32
        path = API + "/shared-practice/works/" + practice_id + "/actions"
        body = {"action": "submit", "text": SECOND, "expected_head": "version-head-2",
                "operation_key": "practice_op:http-msg"}
        code, doc = self.post(path, body)
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["data"]["queued"], doc)
        self.assertFalse(doc["data"]["accepted"])
        code, again = self.post(path, body)
        self.assertEqual(code, 200, again)
        self.assertEqual(again["data"]["event_ref"], doc["data"]["event_ref"])

        code, read = self.call("GET", API + "/shared-practice/operations/practice_op:http-msg")
        self.assertEqual(code, 200, read)
        self.assertEqual(read["status"], "applied", read)
        self.assertEqual(read["data"]["event_ref"], doc["data"]["event_ref"])
        code, absent = self.call("GET", API + "/shared-practice/operations/practice_op:none")
        self.assertEqual(code, 200, absent)
        self.assertEqual(absent["status"], "absent")

    def test_guards_still_hold(self) -> None:
        code, doc = self.post(API + "/shared-practice",
                              {"source_artifact_id": MATERIAL, "source_version_id": "v",
                               "title": TITLE, "text": OPENING,
                               "operation_key": "practice_op:no-csrf"}, csrf=False)
        self.assertEqual(code, 403, doc)
        self.assertEqual(doc["reason_code"], "CSRF_TOKEN_MISSING")
        code, doc = self.post(API + "/shared-practice",
                              {"source_artifact_id": MATERIAL, "source_version_id": "v",
                               "title": TITLE, "text": OPENING, "subject": "someone",
                               "operation_key": "practice_op:id"})
        self.assertEqual(code, 409, doc)
        self.assertIn("subject", json.dumps(doc, ensure_ascii=False))
        code, doc = self.post(API + "/shared-practice",
                              {"source_artifact_id": MATERIAL, "source_version_id": "v",
                               "title": TITLE, "text": OPENING,
                               "operation_key": "practice_op:origin"},
                              headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403, doc)

class FixtureTests(unittest.TestCase):
    """父级用的启动/关闭入口：真 HTTP 面、真装配；关掉之后端口不再接受连接。"""

    def test_start_use_and_close(self) -> None:
        from components.phone import fixture  # noqa: E402 - the runnable entry the parent uses

        handle = fixture.start_fixture(project_port=fx.seeded_port())
        try:
            base = handle["url"].rstrip("/")
            request = urllib.request.Request(base + "/api/phone/shared-practice")
            request.add_header("Accept", "application/json")
            with urllib.request.urlopen(request, timeout=20) as response:
                doc = json.loads(response.read().decode("utf-8"))
            self.assertEqual(int(response.status), 200)
            self.assertTrue(doc["ok"], doc)
            self.assertTrue(doc["data"]["service"]["bound"])
            self.assertTrue(doc["data"]["transport"]["bound"])
            self.assertEqual(doc["data"]["practices"], [])
            self.assertTrue(Path(handle["root"]).exists(), "夹具根保留在磁盘上")
            port = int(handle["port"])
        finally:
            closed = handle["close"]()
        self.assertTrue(closed["ok"], closed)
        with self.assertRaises(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=5):
                pass
        again = handle["close"]()
        self.assertTrue(again["already_closed"], again)

if __name__ == "__main__":
    unittest.main(verbosity=2)

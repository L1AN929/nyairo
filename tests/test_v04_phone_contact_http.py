#!/usr/bin/env python3
"""v0.4 阶段 C：手机来访 HTTP 面（真实 loopback 服务 + 真实 ``PhoneService``）。

这个文件测什么
--------------
``GET /api/phone/contact``（计数 + 有界行 + B 的只读会话）、
``GET .../contact/events``（列表，永不带正文）、``GET .../contact/events/<ref>``（正文只在这里）、
``GET .../contact/operations/<key>``（原 key 只读对账）、
``POST .../contact/events``（投递一条普通来话 / 明确邀请 / 显式结束）。

* 服务是真的：真实 ``PhoneService``（自己的 Life state root）+ 真实 ``create_server``
  （loopback、Host / Origin / Sec-Fetch-Site / CSRF 四道闸门）。
* 断言包含：GET 零写（state root 前后一字不差）、字段白名单、
  任意 ``uid`` / ``home`` / ``path`` / ``url`` / ``channel`` 按名拒绝（409）、
  ``queued`` 与 ``accepted=false`` 同时出现、原 key 在 UNKNOWN 之后仍可查且不变、
  坏账本给具名 reason（不是 200 + 空列表）。
* 夹具根建在会话 scratch（``$PI_SCRATCH_DIR``）下并**保留**，名字唯一；不删除任何文件。

不做：不联网、不连原实例、不起 Life 运行时、不发真实模型请求。
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
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_local_contact as lc  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

CSRF = "contact-csrf-token"
SENTINEL = "PRIVATE_CONTACT_SENTINEL_DO_NOT_LEAK"
API = "/api/phone"


def fixture_base() -> Path:
    override = os.environ.get("NYAIRO_PHONE_CONTACT_ROOT") or os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-phone-contact") if override
            else (Path(os.environ.get("TEMP") or "/tmp") / "nyairo-phone-contact"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def unique_root(prefix: str) -> Path:
    base = fixture_base()
    root = base / ("%s%d-%d" % (prefix, os.getpid(), time.time_ns()))
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
    return {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "contact-fixture",
        "subject": "chiyo",
        "root": str(root),
        "profile_home": str(root),
        "world_home": str(world_home),
        "world_id": "w-contact",
        "supply_socket": str(root / "run" / "supply-fixture.sock"),
        "private_items_artifact_id": "artifact:" + "1" * 32,
        "artifact_grant_id": "grant:" + "2" * 32,
        "workspace_id": "workspace:" + "3" * 32,
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }


class ContactAPIBase(unittest.TestCase):
    """One real loopback server per test, over one real state root."""

    def setUp(self) -> None:
        self.root = unique_root("v04-contact-api-")
        self.state_root = self.root / "one-day" / "life" / "state"
        self.state_root.mkdir(parents=True)
        self.instance = instance_for(self.root)
        Path(self.instance["supply_socket"]).parent.mkdir(parents=True, exist_ok=True)
        Path(self.instance["supply_socket"]).write_text("", encoding="utf-8")
        self.service = phone_service.PhoneService(
            self.instance, environment={"CHIYO_WORLD_HOME": str(self.instance["world_home"])})
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

    def post(self, path: str, body, *, csrf: bool = True, headers=None) -> tuple[int, dict]:
        extra = {"X-CSRF-Token": CSRF} if csrf else {}
        extra.update(headers or {})
        return self.call("POST", path, body, headers=extra)

    # -- flows -------------------------------------------------------------------------

    def deliver(self, **kwargs) -> dict:
        body = {"event_kind": "LOCAL_MESSAGE", "text": "我来啦",
                "operation_key": "contact:send:1"}
        body.update(kwargs)
        code, doc = self.post(API + "/contact/events", body)
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        return doc["data"]

    def invite(self, **kwargs) -> dict:
        body = {"event_kind": "CO_PRESENCE_INVITATION", "scope": "一起坐十分钟",
                "text": "坐坐？", "operation_key": "contact:send:invite"}
        body.update(kwargs)
        code, doc = self.post(API + "/contact/events", body)
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        return doc["data"]


class ContactReadTest(ContactAPIBase):
    def test_state_reports_contact_without_any_text(self) -> None:
        self.deliver(text="有正文的来话" + SENTINEL)
        code, doc = self.call("GET", API + "/state")
        self.assertEqual(code, 200, doc)
        self.assertIn("contact", doc["apps"])
        entry = doc["apps"]["contact"]
        self.assertEqual(entry["status"], "available")
        self.assertIn("session", entry["data"])
        self.assertNotIn(SENTINEL, json.dumps(doc, ensure_ascii=False),
                         "/state 不许带来话正文")

    def test_overview_is_a_pure_read(self) -> None:
        self.invite()
        before = directory_snapshot(self.root)
        code, doc = self.call("GET", API + "/contact")
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        data = doc["data"]
        self.assertEqual(data["subject"], "chiyo")
        self.assertTrue(data["home_bound"])
        self.assertFalse(data["private_text_included"])
        self.assertFalse(data["external_send"])
        self.assertFalse(data["delivered_to_user"])
        self.assertIn("session", data)
        self.assertIn("queued", data["queued_note"].lower() + data["queued_note"])
        self.assertNotIn(SENTINEL, json.dumps(doc, ensure_ascii=False))
        self.assertEqual(directory_snapshot(self.root), before, "GET /contact 必须零写")

    def test_list_has_no_body_and_carries_state(self) -> None:
        self.deliver(text="列表里不该出现" + SENTINEL)
        before = directory_snapshot(self.root)
        code, doc = self.call("GET", API + "/contact/events?limit=5")
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        self.assertFalse(doc["data"]["private_text_included"])
        self.assertEqual(len(doc["data"]["events"]), 1)
        row = doc["data"]["events"][0]
        self.assertFalse(row["body_included"])
        self.assertEqual(row["processing_state"], "PENDING")
        self.assertEqual(row["effect"], "OBSERVATION_ONLY")
        self.assertFalse(row["activity_started"])
        self.assertFalse(row["pause_requested"])
        self.assertNotIn(SENTINEL, json.dumps(doc, ensure_ascii=False))
        self.assertEqual(directory_snapshot(self.root), before, "GET 列表必须零写")

    def test_detail_is_the_only_projection_with_the_text(self) -> None:
        delivered = self.deliver(text="详情里才有" + SENTINEL)
        ref = delivered["event_ref"]
        code, doc = self.call("GET", API + "/contact/events/" + ref)
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        self.assertTrue(doc["data"]["body_included"])
        self.assertTrue(doc["data"]["private_text_included"])
        self.assertIn(SENTINEL, doc["data"]["body"])
        self.assertFalse(doc["data"]["event"]["activity_started"])

    def test_unknown_event_is_404_not_empty(self) -> None:
        code, doc = self.call("GET", API + "/contact/events/contact_evt:" + "0" * 16)
        self.assertEqual(code, 404, doc)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["reason_code"], "CONTACT_EVENT_NOT_FOUND")

    def test_list_refuses_unknown_query_and_bad_limit(self) -> None:
        code, doc = self.call("GET", API + "/contact/events?foo=1")
        self.assertEqual(code, 400, doc)
        self.assertIn("UNKNOWN_QUERY", doc["reason_code"])
        code, doc = self.call("GET", API + "/contact/events?limit=0")
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "LIMIT_INVALID")
        code, doc = self.call("GET", API + "/contact/events?limit=999")
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "LIMIT_OUT_OF_RANGE")

    def test_overview_refuses_any_query(self) -> None:
        code, doc = self.call("GET", API + "/contact?limit=5")
        self.assertEqual(code, 400, doc)
        self.assertIn("UNKNOWN_QUERY", doc["reason_code"])


class ContactDeliverTest(ContactAPIBase):
    def test_message_is_queued_and_never_accepted(self) -> None:
        data = self.deliver(text="我来啦" + SENTINEL)
        self.assertEqual(data["event_kind"], "LOCAL_MESSAGE")
        self.assertEqual(data["effect"], "OBSERVATION_ONLY")
        self.assertEqual(data["processing_state"], "PENDING")
        self.assertTrue(data["queued"], "投完就是 queued")
        self.assertFalse(data["accepted"], "P/HTTP 层永远不替她接纳")
        self.assertFalse(data["pause_requested"])
        self.assertFalse(data["activity_started"])
        self.assertEqual(data["owners_written"], 0)
        self.assertTrue(lc.state_path(self.state_root).exists())

    def test_invitation_needs_a_scope(self) -> None:
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "CO_PRESENCE_INVITATION", "text": "坐坐？",
                               "operation_key": "contact:send:noscope"})
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "CONTACT_SCOPE_REQUIRED")

    def test_plain_text_is_never_upgraded_to_an_invitation(self) -> None:
        """正文写着「一起坐坐」也只是 OBSERVATION_ONLY：升级必须显式 kind + scope。"""

        data = self.deliver(text="一起坐坐好不好")
        self.assertEqual(data["effect"], "OBSERVATION_ONLY")
        self.assertEqual(data["event_kind"], "LOCAL_MESSAGE")
        code, doc = self.call("GET", API + "/contact/events/" + data["event_ref"])
        self.assertIsNone(doc["data"]["invitation"], "普通来话不许凭空长出邀请块")

    def test_message_may_not_carry_invitation_fields(self) -> None:
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "LOCAL_MESSAGE", "text": "你好",
                               "scope": "一起坐坐", "operation_key": "contact:send:scope"})
        self.assertEqual(code, 400, doc)
        self.assertIn("CONTACT_FIELD_NOT_ALLOWED_FOR_KIND", doc["reason_code"])

    def test_end_requires_an_invitation_id(self) -> None:
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "CO_PRESENCE_END", "explicit_end": True,
                               "operation_key": "contact:send:end0"})
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "CONTACT_INVITATION_ID_REQUIRED")

    def test_end_requires_explicit_end(self) -> None:
        invitation = self.invite()
        code, doc = self.call("GET", API + "/contact/events/" + invitation["event_ref"])
        invitation_id = doc["data"]["event"]["invitation_id"]
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "CO_PRESENCE_END",
                               "invitation_id": invitation_id,
                               "operation_key": "contact:send:end1"})
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "CONTACT_END_NEEDS_EXPLICIT_END")

    def test_end_delivers_and_is_queued(self) -> None:
        invitation = self.invite()
        code, doc = self.call("GET", API + "/contact/events/" + invitation["event_ref"])
        invitation_id = doc["data"]["event"]["invitation_id"]
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "CO_PRESENCE_END",
                               "invitation_id": invitation_id, "explicit_end": True,
                               "reason": "EXPLICIT_END",
                               "operation_key": "contact:send:end2"})
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        self.assertEqual(doc["data"]["effect"], "END")
        self.assertEqual(doc["data"]["processing_state"], "PENDING")
        self.assertTrue(doc["data"]["queued"])
        self.assertFalse(doc["data"]["accepted"])

    def test_same_key_is_idempotent(self) -> None:
        first = self.deliver(text="同一句话", operation_key="contact:send:same")
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "LOCAL_MESSAGE", "text": "同一句话",
                               "operation_key": "contact:send:same"})
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["status"], "already_applied")
        self.assertEqual(doc["data"]["event_ref"], first["event_ref"])
        self.assertFalse(doc["data"]["created"])
        self.assertTrue(doc["data"]["replayed"])

    def test_operation_journal_row_is_written(self) -> None:
        """每次投递都在 phone/operations.jsonl 留一行归属（P 自己的窄账本）。"""

        self.deliver(operation_key="contact:send:journal")
        path = self.root / "phone" / "operations.jsonl"
        self.assertTrue(path.exists(), "投递必须留一行归属")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        last = rows[-1]
        self.assertEqual(last["action"], "contact:LOCAL_MESSAGE")
        self.assertEqual(last["operation_key"], "contact:send:journal")
        self.assertNotIn(SENTINEL, json.dumps(rows, ensure_ascii=False))


class ContactSecurityTest(ContactAPIBase):
    """Host / Origin / Sec-Fetch-Site / CSRF 四道闸门，与身份字段的按名拒绝。"""

    def test_missing_csrf_is_refused(self) -> None:
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "LOCAL_MESSAGE", "text": "你好",
                               "operation_key": "contact:send:nochain"}, csrf=False)
        self.assertEqual(code, 403, doc)
        self.assertEqual(doc["reason_code"], "CSRF_TOKEN_MISSING")
        self.assertFalse(lc.state_path(self.state_root).exists(), "被拒绝时零写")

    def test_wrong_csrf_is_refused(self) -> None:
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "LOCAL_MESSAGE", "text": "你好",
                               "operation_key": "contact:send:badcsrf"},
                              headers={"X-CSRF-Token": "not-the-token"})
        # post() 里的 X-CSRF-Token 先加，headers 后更新，所以这里是错令牌。
        self.assertEqual(code, 403, doc)
        self.assertEqual(doc["reason_code"], "CSRF_TOKEN_INVALID")
        self.assertFalse(lc.state_path(self.state_root).exists())

    def test_cross_site_is_refused(self) -> None:
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "LOCAL_MESSAGE", "text": "你好",
                               "operation_key": "contact:send:cross"},
                              headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(code, 403, doc)
        self.assertEqual(doc["reason_code"], "CROSS_SITE_REFUSED")

    def test_foreign_origin_is_refused(self) -> None:
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "LOCAL_MESSAGE", "text": "你好",
                               "operation_key": "contact:send:origin"},
                              headers={"Origin": "http://evil.example"})
        self.assertEqual(code, 403, doc)
        self.assertEqual(doc["reason_code"], "ORIGIN_NOT_ALLOWED")

    def test_foreign_host_is_refused(self) -> None:
        code, doc = self.call("GET", API + "/contact",
                              headers={"Host": "evil.example"})
        self.assertEqual(code, 403, doc)
        self.assertEqual(doc["reason_code"], "HOST_NOT_ALLOWED")

    def test_identity_and_addressing_fields_are_refused_by_name(self) -> None:
        """任意 uid / home / path / url / channel 一律 409 按名拒绝，绝不静默丢掉。"""

        for field, value in (
            ("subject", "someone-else"),
            ("subject_id", "someone-else"),
            ("uid", 1000),
            ("home", "/tmp/other-home"),
            ("home_ref", "home:other"),
            ("state_root", "C:/somewhere/else"),
            ("life_state_root", "/var/lib/other"),
            ("path", "/etc/passwd"),
            ("file", "x.json"),
            ("url", "http://evil.example/hook"),
            ("uri", "file:///etc/passwd"),
            ("channel", "telegram"),
            ("audience", "remote:someone"),
            ("recipient", "someone@example.com"),
            ("transport", "smtp"),
            ("provider", "slack"),
            ("owner", "someone"),
        ):
            body = {"event_kind": "LOCAL_MESSAGE", "text": "你好",
                    "operation_key": "contact:send:refused", field: value}
            code, doc = self.post(API + "/contact/events", body)
            self.assertEqual(code, 409, (field, doc))
            self.assertIn("REFUSED", doc["reason_code"], (field, doc))
            self.assertIn(field, doc["reason_code"], (field, doc))
        self.assertFalse(lc.state_path(self.state_root).exists(), "被拒绝时零写")

    def test_unknown_field_is_refused(self) -> None:
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "LOCAL_MESSAGE", "text": "你好",
                               "operation_key": "contact:send:unknown", "accepted": True})
        self.assertEqual(code, 400, doc)
        self.assertIn("UNKNOWN_FIELD", doc["reason_code"])

    def test_client_cannot_choose_the_outcome(self) -> None:
        """``outcome`` / ``processing_state`` 不是可投字段：界面不能写她的答案。"""

        for field in ("outcome", "processing_state", "accepted", "claimed_by"):
            code, doc = self.post(API + "/contact/events",
                                  {"event_kind": "LOCAL_MESSAGE", "text": "你好",
                                   "operation_key": "contact:send:answer", field: "ACCEPTED"})
            self.assertEqual(code, 400, (field, doc))
            self.assertTrue(doc["reason_code"].startswith("UNKNOWN_FIELD"), (field, doc))

    def test_client_cannot_name_an_invitation_for_a_new_invitation(self) -> None:
        code, doc = self.post(API + "/contact/events",
                              {"event_kind": "CO_PRESENCE_INVITATION", "scope": "一起坐坐",
                               "invitation_id": "localinvite:" + "f" * 16,
                               "operation_key": "contact:send:chosenid"})
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "CONTACT_INVITATION_ID_NOT_CLIENT_CHOSEN")

    def test_non_json_content_type_is_refused(self) -> None:
        request = urllib.request.Request(self.base_url + API + "/contact/events",
                                         data=b"event_kind=LOCAL_MESSAGE", method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("X-CSRF-Token", CSRF)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                code, doc = int(response.status), json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            code, doc = int(error.code), json.loads(error.read().decode())
        self.assertEqual(code, 400, doc)
        self.assertEqual(doc["reason_code"], "CONTENT_TYPE_REQUIRED")


class ContactOperationTest(ContactAPIBase):
    def test_original_key_reads_back(self) -> None:
        data = self.deliver(operation_key="contact:send:reconcile")
        before = directory_snapshot(self.root)
        code, doc = self.call("GET", API + "/contact/operations/" + data["operation_key"])
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        self.assertEqual(doc["status"], "applied")
        self.assertEqual(doc["data"]["event_ref"], data["event_ref"])
        self.assertEqual(doc["data"]["event_state"], "PENDING")
        self.assertEqual(directory_snapshot(self.root), before, "对账必须零写")

    def test_absent_key_says_absent(self) -> None:
        code, doc = self.call("GET", API + "/contact/operations/contact_op:" + "0" * 24)
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["status"], "absent")
        self.assertTrue(doc["data"]["retryable"])

    def test_unknown_outcome_keeps_the_same_key(self) -> None:
        invitation = self.invite(operation_key="contact:send:unknown")
        inbox = lc.LocalContactInbox(self.state_root, subject_id="chiyo",
                                     home_ref=str(self.instance["world_home"]))
        token = inbox.claim(invitation["event_ref"], consumer="life_cycle")["claim_token"]
        inbox.record_result(invitation["event_ref"], outcome="UNKNOWN", claim_token=token,
                            reason_code="CONTACT_MODEL_UNAVAILABLE")
        code, doc = self.call("GET",
                              API + "/contact/operations/" + invitation["operation_key"])
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["status"], "unknown")
        self.assertTrue(doc["data"]["retryable"])
        self.assertEqual(doc["data"]["outcome"], "UNKNOWN")
        # 同一个键还是那条事件：没有被改写、也没有换键。
        self.assertEqual(doc["data"]["event_ref"], invitation["event_ref"])

    def test_bad_operation_key_in_path_is_refused(self) -> None:
        code, doc = self.call("GET", API + "/contact/operations/bad%20key")
        self.assertEqual(code, 404, doc)


class ContactUnboundTest(unittest.TestCase):
    """没有 Life state root / home 的实例：照实说不可用，不假装一个空收件箱。"""

    def setUp(self) -> None:
        self.root = unique_root("v04-contact-unbound-")
        self.instance = instance_for(self.root)
        # 故意不给 NYA02_RESIDENT_STATE，也不给 world_home 之外的任何 home 来源。
        self.instance["world_home"] = ""
        Path(self.instance["supply_socket"]).parent.mkdir(parents=True, exist_ok=True)
        Path(self.instance["supply_socket"]).write_text("", encoding="utf-8")
        self.service = phone_service.PhoneService(self.instance, environment={})

    def test_missing_home_is_named(self) -> None:
        answer = self.service.contact()
        self.assertFalse(answer["ok"], answer)
        self.assertEqual(answer["reason_code"], "CONTACT_HOME_UNBOUND")


class ContactCorruptLedgerTest(ContactAPIBase):
    def test_corrupt_ledger_is_a_named_failure_not_an_empty_list(self) -> None:
        lc.state_path(self.state_root).write_text("{not json", encoding="utf-8")
        code, doc = self.call("GET", API + "/contact/events")
        self.assertNotEqual(code, 200, doc)
        self.assertFalse(doc["ok"], doc)
        self.assertIn(doc["reason_code"],
                      {"CONTACT_STATE_CORRUPT", "CONTACT_STATE_UNREADABLE",
                       "CONTACT_STATE_EMPTY"}, doc)

    def test_corrupt_ledger_on_the_overview(self) -> None:
        lc.state_path(self.state_root).write_text("", encoding="utf-8")
        code, doc = self.call("GET", API + "/contact")
        self.assertFalse(doc["ok"], doc)
        self.assertNotEqual(code, 200, doc)


if __name__ == "__main__":
    unittest.main()

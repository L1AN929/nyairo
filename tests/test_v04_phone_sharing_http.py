#!/usr/bin/env python3
"""v0.4 阶段 B：手机分享 HTTP 面（真实 loopback 服务 + 真实 ``PhoneService``）的定向用例。

这个文件测什么
--------------
``GET /api/phone/shares``（列表，永不带正文）、``GET .../shares/<id>``（正文只在来源还可读时投影）、
``GET .../shares/sources``（只能选这台实例真的读得到的作品/阅读绑定）、
``POST .../shares``（显式短文 prepare）、``POST .../shares/<id>/actions``（send_local / defer / drop）、
``GET .../shares/operations/<key>``（原 key 只读对账）。

* 服务是真的：真实 ``PhoneService`` + 真实 ``create_server``（loopback、Host/Origin/Sec-Fetch/CSRF 闸门）；
  owner 用 ``test_v04_private_projects_stage.FakePort``（内存替身：CAS / operation_key 幂等 / UNKNOWN）。
* 断言包含：GET 零写、字段白名单、身份与「发到哪儿」字段按名拒绝、路径/URL 不可能是来源、
  来源换版 → 409 stale_source、陈旧 head → 409 conflict、已投递/终态不可重发、
  来源读被拒绝时**整份 JSON** 不含那段正文、原 key 在 UNKNOWN 之后仍然可查且不变。
* 夹具根建在会话 scratch（``$PI_SCRATCH_DIR``）下并**保留**，名字唯一；不删除任何文件。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import tempfile
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

import life_artifact_port as lap  # noqa: E402
from components.phone import server as phone_server, service as phone_service  # noqa: E402
from test_v04_private_projects_stage import (  # noqa: E402
    FakePort,
    GRANT,
    ITEMS,
    READING,
    WORKSPACE,
)

CSRF = "shares-csrf-token"
SENTINEL = "PRIVATE_SHARE_SENTINEL_DO_NOT_RETURN"
SOURCE_TEXT = "第一段是留白。第二段还是太挤。" + SENTINEL
SHARE_TEXT = "留白重排完了，第二段还是挤。"


def fixture_base() -> Path:
    override = os.environ.get("NYAIRO_PHONE_SHARING_ROOT") or os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-phone-shares") if override
            else (Path(tempfile.gettempdir()) / "nyairo-phone-shares"))
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


def instance_for(root: Path) -> dict:
    world_home = root / "world"
    world_home.mkdir(parents=True, exist_ok=True)
    return {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "shares-fixture",
        "subject": "chiyo",
        "local_owner_uid": 0,
        "profile_home": str(root),
        "root": str(root),
        "world_home": str(world_home),
        "world_id": "w-shares",
        "supply_socket": str(root / "run" / "supply-fixture.sock"),
        "private_items_artifact_id": ITEMS,
        "reading_artifact_id": READING,
        "artifact_grant_id": GRANT,
        "workspace_id": WORKSPACE,
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }


class SharesAPIBase(unittest.TestCase):
    """One real loopback server per test, over one in-memory owner."""

    def setUp(self) -> None:
        self.root = unique_root("v04-shares-api-")
        self.port = FakePort()
        self.port.seed(artifact_id=READING, title="她的阅读材料", content="第一页")
        self.port.seed(artifact_id=ITEMS, title="她的私人日常事项", content="{}")
        self.source = "artifact:" + "a" * 32
        self.port.seed(artifact_id=self.source, title="她的留白练习", content=SOURCE_TEXT)
        self.instance = instance_for(self.root)
        # 这一份夹具没有真的 Supply socket：写一个空的占位文件，让「socket 在不在」这一层通过，
        # 所有 owner 调用都走下面的内存 port（与 test_phone_projects_api 同一手法）。
        Path(self.instance["supply_socket"]).parent.mkdir(parents=True, exist_ok=True)
        Path(self.instance["supply_socket"]).write_text("", encoding="utf-8")
        self.service = phone_service.PhoneService(self.instance, environment={})
        # 这一份夹具没有 Supply socket：把 port 换成内存 owner（与 test_phone_projects_api 同一手法）。
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

    def post(self, path: str, body, *, csrf: bool = True, headers=None):
        extra = {"X-CSRF-Token": CSRF} if csrf else {}
        extra.update(headers or {})
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

    # -- flows -------------------------------------------------------------------------

    def prepare(self, **kwargs) -> dict:
        body = {"source_artifact_id": self.source, "share_text": SHARE_TEXT,
                "title": "留白重排", "operation_key": "share:prepare:1"}
        body.update(kwargs)
        code, doc = self.post("/api/phone/shares", body)
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"], doc)
        return doc["data"]

    def act(self, share_id: str, action: str, expected_head: str, **kwargs):
        body = {"action": action, "expected_head": expected_head,
                "operation_key": "share:%s:%d" % (action, len(self.port.calls))}
        body.update(kwargs)
        return self.post("/api/phone/shares/%s/actions" % share_id, body)


class SharesReadTest(SharesAPIBase):
    def test_state_reports_the_shares_app_without_any_text(self) -> None:
        self.prepare()
        code, doc = self.call("GET", "/api/phone/state")
        self.assertEqual(code, 200, doc)
        self.assertEqual(set(doc["apps"]), set(phone_service.APPS))
        self.assertIn("shares", doc["apps"])
        entry = doc["apps"]["shares"]
        self.assertEqual(entry["status"], "available")
        self.assertEqual(entry["data"]["counts"]["draft"], 1)
        self.assertTrue(entry["data"]["inbox"]["local_only"])
        self.assertNotIn("shares", json.dumps(entry["data"].get("shares"), ensure_ascii=False))
        self.assertNotIn(SHARE_TEXT, json.dumps(doc, ensure_ascii=False))

    def test_list_sources_and_detail_are_pure_reads(self) -> None:
        draft = self.prepare()
        before = len(self.writes())
        code, listing = self.call("GET", "/api/phone/shares")
        self.assertTrue(listing["ok"])
        self.assertEqual(listing["data"]["counts"]["draft"], 1)
        self.assertFalse(listing["data"]["private_text_included"])
        self.assertIn("本地存档", listing["data"]["note"])
        self.assertNotIn(SHARE_TEXT, json.dumps(listing, ensure_ascii=False))

        code, sources = self.call("GET", "/api/phone/shares/sources")
        self.assertEqual(code, 200, sources)
        kinds = {row["artifact_id"]: row for row in sources["data"]["sources"]}
        self.assertTrue(kinds[self.source]["readable"])
        self.assertEqual(kinds[READING]["kind"], "reading")
        self.assertNotIn(ITEMS, kinds)
        self.assertNotIn(draft["share_id"], kinds)  # 分享件自己不是来源

        code, detail = self.call("GET", "/api/phone/shares/%s" % draft["share_id"])
        self.assertEqual(code, 200, detail)
        share = detail["data"]["share"]
        self.assertEqual(share["state"], "DRAFT")
        self.assertEqual(share["text"], SHARE_TEXT)
        self.assertEqual(share["head_version_id"], draft["head_version_id"])
        self.assertEqual(share["audience"], "local:owner")
        self.assertFalse(detail["data"]["delivered"])
        self.assertTrue(detail["data"]["local_only"])
        self.assertEqual(self.writes()[before:], [], "GET 必须零写")

    def test_an_unknown_share_id_is_404(self) -> None:
        code, doc = self.call("GET", "/api/phone/shares/artifact:%s" % ("e" * 32))
        self.assertEqual(code, 404)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["status"], "not_found")

    def test_the_share_query_is_whitelisted(self) -> None:
        code, doc = self.call("GET", "/api/phone/shares?foo=1")
        self.assertEqual(code, 400)
        self.assertIn("UNKNOWN_QUERY", doc["reason_code"])

        code, doc = self.call("GET", "/api/phone/shares?states=BOGUS")
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "SHARE_STATE_INVALID")

        code, doc = self.call("GET", "/api/phone/shares?limit=0")
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "LIMIT_INVALID")

        code, doc = self.call("GET", "/api/phone/shares?states=DRAFT&limit=5")
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc["ok"])

        code, doc = self.call("GET", "/api/phone/shares/sources?x=1")
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "UNKNOWN_QUERY")


class SharesWriteTest(SharesAPIBase):
    def test_prepare_needs_csrf_and_an_explicit_short_text(self) -> None:
        body = {"source_artifact_id": self.source, "share_text": SHARE_TEXT,
                "operation_key": "share:prepare:csrf"}
        code, doc = self.post("/api/phone/shares", body, csrf=False)
        self.assertEqual(code, 403)
        self.assertEqual(doc["reason_code"], "CSRF_TOKEN_MISSING")
        self.assertEqual(self.writes(), [])

        code, doc = self.post("/api/phone/shares", {"source_artifact_id": self.source,
                                                    "operation_key": "share:prepare:none"})
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "SHARE_TEXT_REQUIRED")
        self.assertEqual(self.writes(), [])

        code, doc = self.post("/api/phone/shares", {"source_artifact_id": self.source,
                                                    "share_text": SHARE_TEXT, "excerpt_chars": 5,
                                                    "operation_key": "share:prepare:both"})
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "SHARE_TEXT_EXCLUSIVE")

    def test_a_body_cannot_address_the_share_anywhere(self) -> None:
        base = {"source_artifact_id": self.source, "share_text": SHARE_TEXT,
                "operation_key": "share:prepare:addr"}
        for field, value in (("audience", "mailto:someone@example.com"),
                             ("channel", "telegram"),
                             ("recipient", "local:someone-else"),
                             ("url", "https://example.com/hook"),
                             ("telegram", "@someone")):
            code, doc = self.post("/api/phone/shares", dict(base, **{field: value}))
            self.assertEqual(code, 409, (field, doc))
            self.assertIn("SHARE_ADDRESSING_REFUSED", doc["reason_code"])
        self.assertEqual(self.writes(), [], "被拒绝的请求不能写 owner")

    def test_identity_fields_in_the_body_are_refused(self) -> None:
        body = {"source_artifact_id": self.source, "share_text": SHARE_TEXT,
                "operation_key": "share:prepare:identity", "artifact_id": self.source}
        code, doc = self.post("/api/phone/shares", body)
        self.assertEqual(code, 409)
        self.assertIn("CLIENT_IDENTITY_REFUSED", doc["reason_code"])

    def test_a_path_or_a_url_is_never_a_source(self) -> None:
        for value in ("/etc/passwd", "https://example.com/x", "sqlite:/tmp/x.db",
                      "file:///tmp/x", "chiyo@example.com"):
            code, doc = self.post("/api/phone/shares",
                                  {"source_artifact_id": value, "share_text": SHARE_TEXT,
                                   "operation_key": "share:prepare:bad"})
            self.assertEqual(code, 400, (value, doc))
            self.assertEqual(doc["reason_code"], "SOURCE_ID_INVALID")
        self.assertEqual(self.writes(), [])

    def test_a_foreign_artifact_id_is_refused_before_anything_is_written(self) -> None:
        code, doc = self.post("/api/phone/shares",
                              {"source_artifact_id": "artifact:" + "f" * 32,
                               "share_text": SHARE_TEXT, "operation_key": "share:prepare:foreign"})
        self.assertEqual(code, 409)
        self.assertEqual(doc["reason_code"], "SHARE_SOURCE_NOT_OFFERED")
        self.assertEqual(self.writes(), [])

    def test_prepare_journals_its_actor_and_key(self) -> None:
        self.prepare()
        rows = self.journal()
        self.assertEqual(rows[-1]["action"], "share:prepare")
        self.assertEqual(rows[-1]["operation_key"], "share:prepare:1")
        self.assertEqual(rows[-1]["actor"], "user")
        self.assertTrue(rows[-1]["ok"])


class SharesLifecycleTest(SharesAPIBase):
    def test_defer_then_send_local_and_never_a_second_delivery(self) -> None:
        draft = self.prepare()
        share_id = draft["share_id"]
        code, doc = self.act(share_id, "defer", draft["head_version_id"])
        self.assertEqual(code, 400)
        self.assertEqual(doc["reason_code"], "DEFER_CONDITION_REQUIRED")

        code, deferred = self.act(share_id, "defer", draft["head_version_id"],
                                  opportunity_ref="夜里安静下来的时候")
        self.assertEqual(code, 200, deferred)
        self.assertEqual(deferred["data"]["state"], "DEFERRED")
        self.assertEqual(deferred["data"]["deferred"]["opportunity_ref"], "夜里安静下来的时候")

        code, sent = self.act(share_id, "send_local", deferred["data"]["head_version_id"])
        self.assertEqual(code, 200, sent)
        data = sent["data"]
        self.assertEqual(data["state"], "LOCAL_DELIVERED")
        self.assertTrue(data["delivered"])
        self.assertEqual(data["delivery_count"], 1)
        receipt = data["receipt"]
        self.assertTrue(receipt["local_only"])
        self.assertFalse(receipt["external_channel_sent"])
        self.assertFalse(receipt["read_by_audience"])
        self.assertIn("本地存档", data["note"])

        code, again = self.act(share_id, "send_local", data["head_version_id"])
        self.assertEqual(code, 409, again)
        self.assertEqual(again["reason_code"], "SHARE_ALREADY_DELIVERED")
        self.assertFalse(again["data"]["delivered"])

        code, detail = self.call("GET", "/api/phone/shares/%s" % share_id)
        self.assertEqual(detail["data"]["share"]["state"], "LOCAL_DELIVERED")
        self.assertEqual(detail["data"]["receipt"]["delivery_id"], receipt["delivery_id"])
        self.assertEqual(detail["data"]["share"]["actions"], [], "已投递没有下一步动作")

    def test_a_stale_head_is_409_and_keeps_the_draft(self) -> None:
        draft = self.prepare()
        versions_before = len(self.port.versions.get(draft["share_id"], []))
        code, doc = self.act(draft["share_id"], "defer", "version-old", defer_condition="以后再说")
        self.assertEqual(code, 409, doc)
        self.assertEqual(doc["status"], "conflict")
        self.assertEqual(doc["data"]["current_head_version_id"], draft["head_version_id"])
        self.assertEqual(len(self.port.versions.get(draft["share_id"], [])), versions_before,
                         "CAS 失败不能落一个新版本")
        code, detail = self.call("GET", "/api/phone/shares/%s" % draft["share_id"])
        self.assertEqual(detail["data"]["share"]["state"], "DRAFT")

    def test_a_moved_source_is_stale_source_409(self) -> None:
        draft = self.prepare()
        self.port.versions[self.source].append({
            "version_id": "version-moved", "version_number": 2,
            "parent_version_id": self.port.artifacts[self.source]["head_version_id"],
            "content": "改过的一页。", "created_at": 1760000300.0,
            "operation_key": "seed-moved"})
        self.port.artifacts[self.source]["head_version_id"] = "version-moved"
        before = len(self.writes())
        code, doc = self.act(draft["share_id"], "send_local", draft["head_version_id"])
        self.assertEqual(code, 409, doc)
        self.assertEqual(doc["status"], "stale_source")
        self.assertEqual(doc["reason_code"], "SOURCE_REVISION_CHANGED")
        self.assertEqual(len(self.writes()), before, "来源换版时不能写任何版本")
        code, listing = self.call("GET", "/api/phone/shares")
        row = listing["data"]["shares"][0]
        self.assertEqual(row["state"], "DRAFT")
        self.assertEqual(row["source_state"], "REVISION_CHANGED")

    def test_drop_is_terminal_and_never_re_sends(self) -> None:
        draft = self.prepare()
        code, dropped = self.act(draft["share_id"], "drop", draft["head_version_id"],
                                 reason="这段先不给她看")
        self.assertEqual(code, 200, dropped)
        self.assertEqual(dropped["data"]["state"], "DROPPED")
        self.assertEqual(dropped["data"]["dropped"]["reason"], "这段先不给她看")
        for action in ("send_local", "defer"):
            body = {"action": action, "expected_head": dropped["data"]["head_version_id"],
                    "operation_key": "share:terminal:%s" % action}
            if action == "defer":
                body["defer_condition"] = "随便"
            code, doc = self.post("/api/phone/shares/%s/actions" % draft["share_id"], body)
            self.assertEqual(code, 409, (action, doc))
            self.assertEqual(doc["reason_code"], "SHARE_TERMINAL_STATE")

    def test_unknown_keeps_the_original_key_and_is_reconciled_by_a_pure_read(self) -> None:
        draft = self.prepare(operation_key="share:prepare:2")
        self.port.unknown_next = True
        self.port.resolve_to = lap.EffectState.NO_CHANGE
        code, unsure = self.post("/api/phone/shares/%s/actions" % draft["share_id"],
                                 {"action": "send_local",
                                  "expected_head": draft["head_version_id"],
                                  "operation_key": "share:send:unknown"})
        self.assertEqual(code, 202, unsure)
        self.assertEqual(unsure["status"], "unknown")
        self.assertTrue(unsure["data"]["retryable"])
        self.assertEqual(unsure["data"]["operation_key"], "share:send:unknown")

        writes_before = len(self.writes())
        code, reconcile = self.call("GET", "/api/phone/shares/operations/share:send:unknown")
        self.assertEqual(code, 200, reconcile)
        self.assertTrue(reconcile["ok"])
        data = reconcile["data"]
        self.assertEqual(data["operation_key"], "share:send:unknown", "对账不许换键")
        self.assertTrue(data["recorded"])
        self.assertEqual(data["records"][-1]["action"], "share:send_local")
        self.assertEqual(data["records"][-1]["status"], "unknown")
        self.assertEqual(len(self.writes()), writes_before, "对账是只读")

    def test_the_share_surface_keeps_every_http_guard(self) -> None:
        code, doc = self.call("GET", "/api/phone/shares", headers={"Host": "evil.example.com"})
        self.assertEqual(code, 403)
        self.assertEqual(doc["reason_code"], "HOST_NOT_ALLOWED")

        code, doc = self.call("GET", "/api/phone/shares",
                              headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(code, 403)
        self.assertEqual(doc["reason_code"], "CROSS_SITE_REFUSED")

        code, doc = self.call("POST", "/api/phone/shares",
                              {"source_artifact_id": self.source, "share_text": SHARE_TEXT,
                               "operation_key": "share:prepare:origin"},
                              headers={"X-CSRF-Token": CSRF,
                                       "Origin": "http://evil.example.com"})
        self.assertEqual(code, 403)
        self.assertEqual(doc["reason_code"], "ORIGIN_NOT_ALLOWED")
        self.assertEqual(self.writes(), [])


class SharesWithheldTest(SharesAPIBase):
    """来源读被拒绝之后，列表与详情这两份 JSON 都不含那段正文。"""

    def refuse_source(self) -> None:
        original = self.service._sharing_service().projects.read_project

        def refuse(artifact_id, **kwargs):
            if artifact_id == self.source:
                return {"ok": False, "status": "rejected",
                        "reason_code": "SOURCE_GOVERNED", "detail": "governance refused"}
            return original(artifact_id, **kwargs)

        self.service._sharing_service().projects.read_project = refuse

    def test_list_and_detail_do_not_leak_a_refused_source(self) -> None:
        draft = self.prepare(share_text="留白重排完了。" + SENTINEL)
        self.refuse_source()
        code, listing = self.call("GET", "/api/phone/shares")
        self.assertEqual(code, 200, listing)
        self.assertEqual(listing["data"]["withheld_count"], 1)
        self.assertFalse(listing["data"]["private_text_included"])
        self.assertNotIn(SENTINEL, json.dumps(listing, ensure_ascii=False))

        code, detail = self.call("GET", "/api/phone/shares/%s" % draft["share_id"])
        self.assertEqual(code, 200, detail)
        self.assertEqual(detail["data"]["share"]["text"], "")
        self.assertTrue(detail["data"]["share"]["text_withheld"])
        self.assertFalse(detail["data"]["private_text_included"])
        self.assertEqual(detail["data"]["withheld_reason_code"], "SOURCE_GOVERNED")
        self.assertNotIn(SENTINEL, json.dumps(detail, ensure_ascii=False))

    def test_sending_from_a_refused_source_is_stale_source(self) -> None:
        draft = self.prepare(share_text="留白重排完了。" + SENTINEL)
        self.refuse_source()
        before = len(self.writes())
        code, doc = self.act(draft["share_id"], "send_local", draft["head_version_id"])
        self.assertEqual(code, 409, doc)
        self.assertEqual(doc["status"], "stale_source")
        self.assertEqual(doc["reason_code"], "SOURCE_GOVERNED")
        self.assertEqual(len(self.writes()), before)
        self.assertNotIn(SENTINEL, json.dumps(doc, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()

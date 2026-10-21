#!/usr/bin/env python3
"""P 的阅读工作台 HTTP 面：只测本轮新增接线与直接受影响的精确边界。

本文件不跑旧手机 45 项、不跑真实世界实例，也不产生任何跨设备依赖：

* 夹具是一份真实的 ``PhoneService`` + 真实 loopback HTTP 服务器（``create_server``），
  C 的 ``PhoneReading`` 用**桩**替换：P 的职责只是转发、拒绝与记录来源，不重算任何阅读事实，
  所以这里测的是 P 自己那层（路由、Query/请求体白名单、身份拒绝、信封映射、编号越界、零写）。
* 断言只读 P 自己的行为与落盘的 ``phone/operations.jsonl``；C 的真实语义（Supply CAS、
  取得证据、版本提示重看）由 C 的测试负责。桩的调用记录就是"P 到底发了什么"。
* 夹具目录建在会话 scratch（或系统临时目录）下且**保留**（不删除、不清理），名字唯一。

覆盖：``GET /api/phone/reading/material?page=N``（默认交给 owner 的最近停点、纯读零写、
Query 只有 page、严格页码、未绑定材料诚实 unavailable、facade 缺方法 fail closed）、
``POST .../reading/material``（只收 content/expected_revision/operation_key、UTF-8 字节界限、
身份与未知字段拒绝、CSRF、状态码映射）、``POST .../reading/positions``（严格整数页、required
字段、原样转发）、``POST .../reading/annotations``（kind 白名单与逐 kind 字段、note≠unclear）、
``GET .../reading/operations/<key>``（原 key 查询、404/202 映射）、旧 ``GET /api/phone/reading``
仍不含正文。
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
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import server as phone_server, service as phone_service  # noqa: E402

CSRF = "reading-csrf-token"
READING_ARTIFACT = "artifact-read-1"
PAGE_MARKER = "%%PAGE%%"
#: The real-C narrow test needs a real ``LifeArtifactPort``, whose socket path must be POSIX
#: absolute (``life_artifact_port``); on Windows the port refuses to be built at all.
posix_only = unittest.skipIf(os.name == "nt", "需要 POSIX AF_UNIX 的 LifeArtifactPort")


def fixture_base() -> Path:
    """One retained fixture root per process; never deleted, never reused across classes."""

    override = os.environ.get("NYAIRO_READING_TEST_ROOT") or os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-reading-pi") if override
            else (Path(tempfile.gettempdir()) / "nyairo-reading-pi"))
    base.mkdir(parents=True, exist_ok=True)
    return base


#: How many fixture roots this process has minted.  The pid is shared by every root here and the
#: clock can repeat inside one millisecond (the parent's POSIX run hit exactly that: two roots with
#: the same prefix in the same millisecond), so this serial is what actually makes the name unique.
_ROOT_SERIAL = 0


def unique_root(prefix: str) -> Path:
    """One retained, never-reused fixture root; the same prefix twice cannot collide."""

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


# ---------------------------------------------------------------------------------------------
# stub owner facade (C's real PhoneReading lives in components/life/scripts; P never re-implements)
# ---------------------------------------------------------------------------------------------


def material_payload(**overrides):
    data = {
        "available": True,
        "artifact_id": READING_ARTIFACT,
        "source_id": "artifact:" + READING_ARTIFACT,
        "source_revision": "artifact:" + READING_ARTIFACT + ":v3",
        "version_number": 3,
        "title": "夜航手记",
        "page": 2,
        "pages": 3,
        "text": "第二页的真实正文。",
        "content_available": True,
        "private_head": "pdi-head-9",
        "bookmark": {"page": 2, "source_revision": "artifact:" + READING_ARTIFACT + ":v3",
                     "needs_reread": False},
        "understanding": "UNKNOWN",
        "annotations": [{"id": "memo:1", "kind": "note", "title": "页边", "body": "想到别的事",
                         "source_id": "artifact:" + READING_ARTIFACT,
                         "source_revision": "artifact:" + READING_ARTIFACT + ":v3", "page": 2,
                         "created_at": "2026-10-22T00:00:00Z"}],
    }
    data.update(overrides)
    return {"ok": True, "status": phone_service.STATUS_OK, "data": data}


class StubReading:
    """Records exactly what P forwarded; answers with whatever the test declared."""

    def __init__(self, *, material=None, replace=None, position=None, annotate=None,
                 operation=None):
        self.calls: list[tuple] = []
        self._material = material_payload() if material is None else material
        self._replace = replace or {"ok": True, "status": phone_service.STATUS_OK,
                                    "data": {"artifact_id": READING_ARTIFACT,
                                             "source_revision": "artifact:%s:v4" % READING_ARTIFACT,
                                             "version_number": 4, "page": 1, "pages": 2,
                                             "operation_key": "import-1",
                                             "needs_reread": True}}
        self._position = position or {"ok": True, "status": phone_service.STATUS_OK,
                                      "data": {"page": 2,
                                               "source_revision": "artifact:%s:v3" % READING_ARTIFACT,
                                               "private_head": "pdi-head-10",
                                               "understanding": "UNKNOWN",
                                               "operation_key": "pos-1",
                                               "acquired_chars": 12}}
        self._annotate = annotate or {"ok": True, "status": phone_service.STATUS_OK,
                                      "data": {"id": "memo:2", "kind": "note", "page": 2,
                                               "operation_key": "ann-1",
                                               "understanding": "UNKNOWN"}}
        self._operation = operation or {"ok": True, "status": phone_service.STATUS_OK,
                                        "data": {"operation_key": "import-1",
                                                 "action": "reading_import",
                                                 "status": "applied"}}

    def material(self, page=None):
        self.calls.append(("material", page))
        return dict(self._material)

    def replace_material(self, content, expected_revision, operation_key):
        self.calls.append(("replace_material", content, expected_revision, operation_key))
        return dict(self._replace)

    def save_position(self, page, source_revision, expected_head, operation_key):
        self.calls.append(("save_position", page, source_revision, expected_head, operation_key))
        return dict(self._position)

    def annotate(self, kind, page, source_revision, expected_head, operation_key,
                 title=None, body=None, note=None):
        self.calls.append(("annotate", kind, page, source_revision, expected_head,
                           operation_key, title, body, note))
        return dict(self._annotate)

    def operation(self, operation_key):
        self.calls.append(("operation", operation_key))
        return dict(self._operation)

    def answer(self, name: str, payload: dict) -> None:
        """Swap one canned answer without restarting the fixture server."""

        if name not in ("material", "replace", "position", "annotate", "operation"):
            raise AssertionError("unknown stub answer: %s" % name)
        setattr(self, "_" + name, payload)


class NoReadingFacade:
    """An older facade with no reading methods at all: P must say "not implemented"."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []


class StubOwnerPort:
    """Stands in for ``LifeArtifactPort``'s read side: one head, no socket."""

    def __init__(self, head: str = "pdi-head-9", number: int = 3) -> None:
        self.head = head
        self.number = number
        self.reads: list[str] = []

    def read_artifact(self, artifact_id: str) -> dict:
        self.reads.append(artifact_id)
        return {"artifact": {"title": "夜航手记"},
                "head": {"version_id": self.head, "version_number": self.number},
                "content": "第一页" + PAGE_MARKER + "第二页"}


class StubOwnerItems:
    """Stands in for C's ``PrivateDailyItems`` read side: rows by item type + memo bodies."""

    def __init__(self, rows, bodies=None) -> None:
        self.rows = list(rows)
        self.bodies = dict(bodies or {})
        self.text_calls: list[str] = []

    def items(self, item_type: str) -> list[dict]:
        return [dict(row) for row in self.rows
                if str(row.get("item_type")) == str(item_type)]

    def full_text(self, item_id: str) -> str:
        self.text_calls.append(item_id)
        if item_id not in self.bodies:
            raise KeyError(item_id)
        return self.bodies[item_id]


class StubStateShopping:
    """The four economy reads ``/state`` summarizes (no prices, no writes)."""

    def wallet(self):
        return {"ok": True, "status": "applied", "data": {"currency": "JPY", "balance": 9760}}

    def shop(self):
        return {"ok": True, "status": "applied", "data": {"currency": "JPY", "items": []}}

    def store(self):
        return {"ok": True, "status": "applied", "data": {
            "store_id": "home_goods_store", "currency": "JPY", "catalog": [],
            "availability": {"status": "OPEN"}}}

    def inventory(self):
        return {"ok": True, "status": "applied", "data": {"items": []}}


class StubStateWorld:
    """The nine snapshot blocks ``/state`` reads (no body, no writes)."""

    def snapshot(self):
        return {"ok": True, "status": "applied", "data": {
            "world": {"status": "available", "world_id": "w-reading", "revision": 3},
            "location": {"status": "available", "node": "home/bedroom", "place_id": "home",
                         "area_id": "bedroom", "name": "卧室"},
            "local_environment": {"status": "available", "furniture": []},
            "destinations": {"status": "available", "nodes": []},
            "travel": {"status": "none"},
            "objects": {"status": "available", "here": [], "held": []},
            "body": {"status": "available", "pose": "standing"},
            "actions": {"status": "available", "verbs": []},
            "recent_executions": {"status": "available", "items": []},
        }}


def instance_for(root: Path, *, reading_artifact_id: str = READING_ARTIFACT) -> dict:
    world_home = root / "world"
    world_home.mkdir(parents=True, exist_ok=True)
    instance = {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "reading-fixture",
        "subject": "chiyo",
        "local_owner_uid": 0,
        "profile_home": str(root),
        "root": str(root),
        "world_home": str(world_home),
        "world_id": "w-reading",
        "supply_socket": str(root / "run" / "supply-fixture.sock"),
        "private_items_artifact_id": "artifact-items",
        "artifact_grant_id": "grant-reading",
        "workspace_id": "ws-reading",
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }
    if reading_artifact_id is not None:
        instance["reading_artifact_id"] = reading_artifact_id
    return instance


class ReadingAPIBase(unittest.TestCase):
    """One real loopback server per test; the reading facade is the stub above."""

    reading: StubReading

    def build(self, *, reading=None, root=None, reading_artifact_id=READING_ARTIFACT,
              shopping=None, world=None, items=None, port=None) -> None:
        self._stop_server()
        self.root = root or unique_root("rc1-reading-api-")
        self.reading = reading if reading is not None else StubReading()
        self.instance = instance_for(self.root, reading_artifact_id=reading_artifact_id)
        self.service = phone_service.PhoneService(self.instance, environment={},
                                                  shopping=shopping, world=world,
                                                  reading=self.reading)
        if items is not None:
            # Test-only injection: this fixture has no Supply socket, so the notes read side is
            # replaced by a stub whose rows are C's own item shape.  P's own code still runs.
            self.service._items = lambda: items
        if port is not None:
            self.service._port = lambda: port
        self.httpd = phone_server.create_server(self.service, csrf_token=CSRF,
                                                port=free_port())
        self.base_url = "http://127.0.0.1:%d" % int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def _stop_server(self) -> None:
        httpd = getattr(self, "httpd", None)
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        thread = getattr(self, "thread", None)
        if thread is not None:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive(), "the fixture server did not stop")
        self.httpd = None
        self.thread = None

    def tearDown(self) -> None:
        self._stop_server()

    # -- transport -------------------------------------------------------------------------

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
        extra = dict(headers or {})
        if csrf:
            extra["X-CSRF-Token"] = CSRF
        return self.call("POST", path, body, headers=extra)

    def journal(self) -> list[dict]:
        path = self.root / "phone" / "operations.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def files_under_root(self) -> list[str]:
        return sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))

    def names(self) -> list[str]:
        return [call[0] for call in self.reading.calls]


# ---------------------------------------------------------------------------------------------
# GET /api/phone/reading/material
# ---------------------------------------------------------------------------------------------


class ReadingMaterialReadTest(ReadingAPIBase):
    def test_material_without_page_asks_the_owner_for_the_saved_stop(self) -> None:
        self.build()
        before = self.files_under_root()
        code, doc = self.call("GET", "/api/phone/reading/material")
        self.assertEqual(code, 200, doc)
        self.assertIs(doc["ok"], True)
        self.assertEqual(doc["status"], "applied")
        # The page is the owner's business: P must not pre-fill 1 and pretend it is a stop.
        self.assertEqual(self.reading.calls, [("material", None)])
        data = doc["data"]
        for key in ("available", "artifact_id", "source_id", "source_revision", "version_number",
                    "title", "page", "pages", "text", "content_available", "private_head",
                    "bookmark", "understanding", "annotations"):
            self.assertIn(key, data, key)
        self.assertEqual(data["text"], "第二页的真实正文。")
        self.assertEqual(data["understanding"], "UNKNOWN")
        self.assertEqual(data["annotations"][0]["kind"], "note")
        # A GET never records, never writes a file and never moves the bookmark.
        self.assertEqual(self.journal(), [])
        self.assertEqual(self.files_under_root(), before, "GET material wrote something")

    def test_material_forwards_an_explicit_page_as_an_integer(self) -> None:
        self.build()
        code, doc = self.call("GET", "/api/phone/reading/material?page=3")
        self.assertEqual(code, 200, doc)
        self.assertEqual(self.reading.calls, [("material", 3)])

    def test_material_on_a_root_without_a_bound_artifact_is_honestly_unavailable(self) -> None:
        self.build(reading_artifact_id=None)
        code, doc = self.call("GET", "/api/phone/reading/material")
        self.assertEqual(code, 503, doc)
        self.assertIs(doc["ok"], False)
        self.assertEqual(doc["status"], "unavailable")
        # C's own vocabulary for "this root has no material" -- and its honest empty shape.
        self.assertEqual(doc["reason_code"], "READING_MATERIAL_UNAVAILABLE")
        self.assertIs(doc["data"]["available"], False)
        self.assertIs(doc["data"]["content_available"], False)
        self.assertIsNone(doc["data"]["bookmark"])
        self.assertEqual(doc["data"]["annotations"], [])
        self.assertNotIn("text", doc["data"])
        self.assertEqual(self.reading.calls, [], "an unbound root must not reach the facade")

    def test_material_query_allowlist_is_exactly_page(self) -> None:
        self.build()
        for query in ("?page=0", "?page=-1", "?page=abc", "?page=1.5", "?page=true", "?page=01",
                      "?page=", "?page", "?page=%2B2", "?page=1&page=2"):
            with self.subTest(query=query):
                code, doc = self.call("GET", "/api/phone/reading/material" + query)
                self.assertEqual(code, 400, doc)
                self.assertEqual(doc["ok"], False)
                self.assertTrue(str(doc["reason_code"]).startswith(("PAGE_INVALID",
                                                                    "QUERY_REPEATED")),
                                doc["reason_code"])
        code, doc = self.call("GET", "/api/phone/reading/material?page=65")
        self.assertEqual((code, doc["reason_code"]), (400, "PAGE_OUT_OF_RANGE"))
        for query in ("?offset=1", "?page=1&artifact_id=artifact-other", "?limit=3"):
            with self.subTest(query=query):
                code, doc = self.call("GET", "/api/phone/reading/material" + query)
                self.assertEqual((code, doc["ok"]), (400, False))
                self.assertTrue(str(doc["reason_code"]).startswith("UNKNOWN_QUERY"), doc)
        self.assertEqual(self.reading.calls, [], "a refused query must not reach the owner")

    def test_material_read_keeps_the_host_origin_and_fetch_site_gates(self) -> None:
        self.build()
        code, doc = self.call("GET", "/api/phone/reading/material",
                              headers={"Host": "evil.example"})
        self.assertEqual((code, doc["reason_code"]), (403, "HOST_NOT_ALLOWED"))
        code, doc = self.call("GET", "/api/phone/reading/material",
                              headers={"Origin": "http://evil.example"})
        self.assertEqual((code, doc["reason_code"]), (403, "ORIGIN_NOT_ALLOWED"))
        code, doc = self.call("GET", "/api/phone/reading/material",
                              headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual((code, doc["reason_code"]), (403, "CROSS_SITE_REFUSED"))
        self.assertEqual(self.reading.calls, [])

    def test_material_read_has_no_post_side_route_and_the_old_path_is_get_only(self) -> None:
        self.build()
        # No body: an unknown API route is refused before anything is read, and this test is about
        # the route table, not about draining a payload nobody asked for.
        code, doc = self.call("POST", "/api/phone/reading")
        self.assertEqual((code, doc["reason_code"]), (404, "NOT_FOUND"))
        code, doc = self.call("GET", "/api/phone/reading/positions")
        self.assertEqual((code, doc["reason_code"]), (404, "NOT_FOUND"))
        self.assertEqual(self.reading.calls, [])

    def test_a_facade_without_the_method_is_not_implemented_not_an_empty_page(self) -> None:
        self.build(reading=NoReadingFacade())
        code, doc = self.call("GET", "/api/phone/reading/material")
        self.assertEqual(code, 503, doc)
        self.assertEqual(doc["status"], "unavailable")
        self.assertEqual(doc["reason_code"], "PHONE_FACADE_UNIMPLEMENTED")
        self.assertEqual(doc["data"], {"method": "material"})

    def test_a_non_envelope_answer_is_an_adapter_error_never_a_page(self) -> None:
        self.build(reading=StubReading(material=["not", "an", "envelope"]))
        code, doc = self.call("GET", "/api/phone/reading/material")
        self.assertEqual((code, doc["reason_code"]), (503, "PHONE_READING_ADAPTER_ERROR"))

    def test_source_id_follows_the_existing_artifact_rule(self) -> None:
        self.build()
        self.assertEqual(self.service.reading_source_id(), "artifact:" + READING_ARTIFACT)
        self.build(reading_artifact_id=None)
        self.assertIsNone(self.service.reading_source_id())


# ---------------------------------------------------------------------------------------------
# POST /api/phone/reading/material
# ---------------------------------------------------------------------------------------------


class ReadingImportTest(ReadingAPIBase):
    def test_import_forwards_only_the_text_the_revision_and_the_key(self) -> None:
        self.build()
        content = "第一页正文" + PAGE_MARKER + "第二页正文"
        code, doc = self.post("/api/phone/reading/material",
                              {"content": content, "expected_revision":
                               "artifact:%s:v3" % READING_ARTIFACT, "operation_key": "import-1"})
        self.assertEqual(code, 200, doc)
        self.assertIs(doc["ok"], True)
        self.assertEqual(doc["status"], "applied")
        # Verbatim: no re-splitting, no title, no artifact id, no receipt.
        self.assertEqual(self.reading.calls,
                         [("replace_material", content,
                           "artifact:%s:v3" % READING_ARTIFACT, "import-1")])
        rows = self.journal()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["action"], rows[0]["operation_key"], rows[0]["ok"]),
                         ("reading_import", "import-1", True))
        self.assertEqual(rows[0]["actor"], "user")

    def test_import_requires_csrf_and_the_json_content_type(self) -> None:
        self.build()
        # No body: the CSRF guard runs before anything is read, so this proves the gate without
        # also depending on how Windows closes a socket that still holds an unread body.
        code, doc = self.post("/api/phone/reading/material", None, csrf=False)
        self.assertEqual((code, doc["reason_code"]), (403, "CSRF_TOKEN_MISSING"))
        code, doc = self.post("/api/phone/reading/material", None, csrf=False,
                              headers={"X-CSRF-Token": "wrong"})
        self.assertEqual((code, doc["reason_code"]), (403, "CSRF_TOKEN_INVALID"))
        self.assertEqual(self.reading.calls, [])

    def test_import_refuses_client_identity_and_owner_facts(self) -> None:
        self.build()
        for field, value in (("artifact_id", "artifact-other"), ("uid", 0), ("owner", "chiyo"),
                             ("source", "url"), ("workspace_id", "ws-other")):
            with self.subTest(field=field):
                code, doc = self.post("/api/phone/reading/material",
                                      {"content": "正文", "expected_revision": "rev-1",
                                       "operation_key": "import-x", field: value})
                self.assertEqual(code, 409, doc)
                self.assertTrue(str(doc["reason_code"]).startswith("CLIENT_IDENTITY_REFUSED"),
                                doc["reason_code"])
        self.assertEqual(self.reading.calls, [])

    def test_import_refuses_unknown_fields_including_title_and_understanding(self) -> None:
        self.build()
        for field in ("title", "understanding", "url", "page"):
            with self.subTest(field=field):
                code, doc = self.post("/api/phone/reading/material",
                                      {"content": "正文", "expected_revision": "rev-1",
                                       "operation_key": "import-x", field: "x"})
                self.assertEqual((code, doc["reason_code"]),
                                 (400, "UNKNOWN_FIELD:%s" % field))
        self.assertEqual(self.reading.calls, [])

    def test_import_enforces_the_utf8_byte_bounds_and_page_marker_is_untouched(self) -> None:
        self.build()
        # 8000 CJK characters == 24000 UTF-8 bytes: the largest accepted whole text.
        exact = "字" * 8000
        code, doc = self.post("/api/phone/reading/material",
                              {"content": exact, "expected_revision": "rev-1",
                               "operation_key": "import-big"})
        self.assertEqual(code, 200, doc)
        self.assertEqual(self.reading.calls[0][1], exact)
        self.assertEqual(len(self.reading.calls[0][1].encode("utf-8")), 24000)
        for bad in ("", "   ", "\n\n", "a" * 24001, "字" * 8001, "正文\x00", 5, None, True):
            with self.subTest(value=repr(bad)[:20]):
                code, doc = self.post("/api/phone/reading/material",
                                      {"content": bad, "expected_revision": "rev-1",
                                       "operation_key": "import-bad"})
                self.assertEqual((code, doc["reason_code"]),
                                 (400, "READING_CONTENT_INVALID"))
        self.assertEqual(len(self.reading.calls), 1, "a refused text must not reach the owner")

    def test_import_requires_an_operation_key_and_a_revision(self) -> None:
        self.build()
        code, doc = self.post("/api/phone/reading/material",
                              {"content": "正文", "expected_revision": "rev-1"})
        self.assertEqual((code, doc["reason_code"]), (400, "OPERATION_KEY_REQUIRED"))
        code, doc = self.post("/api/phone/reading/material",
                              {"content": "正文", "operation_key": "import-1"})
        self.assertEqual((code, doc["reason_code"]), (400, "EXPECTED_REVISION_INVALID"))
        self.assertEqual(self.reading.calls, [])

    def test_owner_status_is_never_upgraded(self) -> None:
        for status, expected in (("applied", 200), ("already_applied", 200), ("rejected", 409),
                                 ("unavailable", 503), ("unknown", 202), ("disabled", 409)):
            with self.subTest(status=status):
                self.build(reading=StubReading(replace={
                    "ok": status in ("applied", "already_applied"), "status": status,
                    "reason_code": "READING_REVISION_CONFLICT",
                    "data": {"reason_code": "READING_REVISION_CONFLICT"}}))
                code, doc = self.post("/api/phone/reading/material",
                                      {"content": "正文", "expected_revision": "rev-old",
                                       "operation_key": "k-" + status})
                self.assertEqual(code, expected, doc)
                self.assertEqual(doc["status"], status)
                self.assertEqual(doc["reason_code"], "READING_REVISION_CONFLICT")
                self.assertEqual(len(self.journal()), 1)


# ---------------------------------------------------------------------------------------------
# POST /api/phone/reading/positions
# ---------------------------------------------------------------------------------------------


class ReadingPositionTest(ReadingAPIBase):
    def test_position_forwards_the_page_revision_and_head_verbatim(self) -> None:
        self.build()
        code, doc = self.post("/api/phone/reading/positions",
                              {"page": 2, "source_revision":
                               "artifact:%s:v3" % READING_ARTIFACT,
                               "expected_head": "pdi-head-9", "operation_key": "pos-1"})
        self.assertEqual(code, 200, doc)
        self.assertIs(doc["ok"], True)
        self.assertEqual(self.reading.calls,
                         [("save_position", 2, "artifact:%s:v3" % READING_ARTIFACT,
                           "pdi-head-9", "pos-1")])
        self.assertEqual(doc["data"]["understanding"], "UNKNOWN")
        rows = self.journal()
        self.assertEqual((rows[0]["action"], rows[0]["operation_key"]),
                         ("reading_position", "pos-1"))

    def test_page_is_a_strict_integer_never_a_bool_float_or_string(self) -> None:
        self.build()
        base = {"source_revision": "artifact:%s:v3" % READING_ARTIFACT,
                "expected_head": "pdi-head-9", "operation_key": "pos-x"}
        for page, reason in ((True, "PAGE_INVALID"), (False, "PAGE_INVALID"),
                             (2.0, "PAGE_INVALID"), ("2", "PAGE_INVALID"), (0, "PAGE_INVALID"),
                             (None, "PAGE_INVALID"), (65, "PAGE_OUT_OF_RANGE")):
            with self.subTest(page=repr(page)):
                code, doc = self.post("/api/phone/reading/positions", dict(base, page=page))
                self.assertEqual((code, doc["reason_code"]), (400, reason))
        self.assertEqual(self.reading.calls, [], "a refused page must not reach the owner")

    def test_position_requires_the_head_and_the_revision(self) -> None:
        self.build()
        page = {"page": 1, "source_revision": "rev-1", "expected_head": "head-1",
                "operation_key": "pos-1"}
        for missing, reason in (("page", "PAGE_INVALID"),
                                ("source_revision", "SOURCE_REVISION_INVALID"),
                                ("expected_head", "EXPECTED_HEAD_INVALID"),
                                ("operation_key", "OPERATION_KEY_REQUIRED")):
            with self.subTest(missing=missing):
                body = dict(page)
                body.pop(missing)
                code, doc = self.post("/api/phone/reading/positions", body)
                self.assertEqual((code, doc["reason_code"]), (400, reason))
        self.assertEqual(self.reading.calls, [])

    def test_position_refuses_artifact_identity_and_unknown_fields(self) -> None:
        self.build()
        base = {"page": 1, "source_revision": "rev-1", "expected_head": "head-1",
                "operation_key": "pos-1"}
        code, doc = self.post("/api/phone/reading/positions",
                              dict(base, artifact_id="artifact-other"))
        self.assertEqual(code, 409, doc)
        self.assertTrue(str(doc["reason_code"]).startswith("CLIENT_IDENTITY_REFUSED"))
        code, doc = self.post("/api/phone/reading/positions", dict(base, understanding="KNOWN"))
        self.assertEqual((code, doc["reason_code"]), (400, "UNKNOWN_FIELD:understanding"))
        self.assertEqual(self.reading.calls, [])

    def test_position_owner_refusal_keeps_its_own_reason_code(self) -> None:
        self.build(reading=StubReading(position={
            "ok": False, "status": "rejected", "reason_code": "PAGE_NOT_PRESENT",
            "data": {"reason_code": "PAGE_NOT_PRESENT", "pages": 3}}))
        code, doc = self.post("/api/phone/reading/positions",
                              {"page": 3, "source_revision": "rev-1", "expected_head": "head-1",
                               "operation_key": "pos-3"})
        self.assertEqual((code, doc["status"]), (409, "rejected"))
        self.assertEqual(doc["reason_code"], "PAGE_NOT_PRESENT")
        self.assertEqual(doc["data"]["pages"], 3)


# ---------------------------------------------------------------------------------------------
# POST /api/phone/reading/annotations
# ---------------------------------------------------------------------------------------------


class ReadingAnnotationTest(ReadingAPIBase):
    def base(self, **overrides) -> dict:
        body = {"page": 2, "source_revision": "artifact:%s:v3" % READING_ARTIFACT,
                "expected_head": "pdi-head-9", "operation_key": "ann-1"}
        body.update(overrides)
        return body

    def test_note_carries_title_and_body_and_never_the_note_field(self) -> None:
        self.build()
        code, doc = self.post("/api/phone/reading/annotations",
                              self.base(kind="note", title="页边", body="这一页让我想到别的事"))
        self.assertEqual(code, 200, doc)
        self.assertEqual(self.reading.calls,
                         [("annotate", "note", 2, "artifact:%s:v3" % READING_ARTIFACT,
                           "pdi-head-9", "ann-1", "页边", "这一页让我想到别的事", None)])
        rows = self.journal()
        self.assertEqual((rows[0]["action"], rows[0]["operation_key"]),
                         ("reading_annotate", "ann-1"))

    def test_note_with_the_unclear_field_is_refused_not_silently_dropped(self) -> None:
        self.build()
        code, doc = self.post("/api/phone/reading/annotations",
                              self.base(kind="note", title="页边", body="正文", note="疑问"))
        self.assertEqual((code, doc["reason_code"]), (400, "UNKNOWN_FIELD:note"))
        code, doc = self.post("/api/phone/reading/annotations",
                              self.base(kind="note", body="只有正文"))
        self.assertEqual((code, doc["reason_code"]), (400, "ANNOTATION_TITLE_REQUIRED"))
        code, doc = self.post("/api/phone/reading/annotations",
                              self.base(kind="note", title="只有标题"))
        self.assertEqual((code, doc["reason_code"]), (400, "ANNOTATION_BODY_REQUIRED"))
        self.assertEqual(self.reading.calls, [])

    def test_unclear_carries_only_a_note_and_stays_a_separate_kind(self) -> None:
        self.build(reading=StubReading(annotate={
            "ok": True, "status": "applied",
            "data": {"id": "unclear:1", "kind": "unclear", "page": 2,
                     "operation_key": "ann-1", "understanding": "UNKNOWN"}}))
        code, doc = self.post("/api/phone/reading/annotations",
                              self.base(kind="unclear", note="这一段没读懂"))
        self.assertEqual(code, 200, doc)
        self.assertEqual(self.reading.calls,
                         [("annotate", "unclear", 2, "artifact:%s:v3" % READING_ARTIFACT,
                           "pdi-head-9", "ann-1", None, None, "这一段没读懂")])
        self.assertEqual(doc["data"]["kind"], "unclear")
        code, doc = self.post("/api/phone/reading/annotations",
                              self.base(kind="unclear", title="x", note="y"))
        self.assertEqual((code, doc["reason_code"]), (400, "UNKNOWN_FIELD:title"))
        code, doc = self.post("/api/phone/reading/annotations", self.base(kind="unclear"))
        self.assertEqual((code, doc["reason_code"]), (400, "ANNOTATION_NOTE_REQUIRED"))

    def test_kind_whitelist_is_exact(self) -> None:
        self.build()
        for kind in ("memo", "NOTE", "", None, 1, True, "unclear_note"):
            with self.subTest(kind=repr(kind)):
                code, doc = self.post("/api/phone/reading/annotations",
                                      self.base(kind=kind, note="疑问"))
                self.assertEqual((code, doc["reason_code"]),
                                 (400, "ANNOTATION_KIND_INVALID"))
        self.assertEqual(self.reading.calls, [])

    def test_page_not_acquired_keeps_the_owner_reason_and_is_a_rejection(self) -> None:
        self.build(reading=StubReading(annotate={
            "ok": False, "status": "rejected", "reason_code": "PAGE_NOT_ACQUIRED",
            "data": {"reason_code": "PAGE_NOT_ACQUIRED"}}))
        code, doc = self.post("/api/phone/reading/annotations",
                              self.base(kind="note", title="页边", body="正文"))
        self.assertEqual((code, doc["status"]), (409, "rejected"))
        self.assertEqual(doc["reason_code"], "PAGE_NOT_ACQUIRED")
        self.assertEqual(doc["data"]["reason_code"], "PAGE_NOT_ACQUIRED")

    def test_annotation_bounds_follow_the_owners_utf8_limits(self) -> None:
        self.build()
        # C's bound is 8000 UTF-8 bytes: a 4001-byte body is legitimate, not an error.
        code, doc = self.post("/api/phone/reading/annotations",
                              self.base(kind="note", title="页边", body="a" * 4001))
        self.assertEqual(code, 200, doc)
        self.assertEqual(self.reading.calls[0][7], "a" * 4001)
        self.reading.calls.clear()
        code, doc = self.post("/api/phone/reading/annotations",
                              self.base(kind="note", title="页边", body="a" * 8000))
        self.assertEqual(code, 200, doc)
        code, doc = self.post("/api/phone/reading/annotations",
                              self.base(kind="note", title="a" * 200, body="正文"))
        self.assertEqual(code, 200, doc)
        self.reading.calls.clear()
        for overrides, reason in (
            ({"kind": "note", "title": "页边", "body": "a" * 8001},
             "ANNOTATION_BODY_INVALID"),
            ({"kind": "note", "title": "a" * 201, "body": "正文"},
             "ANNOTATION_TITLE_INVALID"),
            ({"kind": "note", "title": "页边", "body": "   "},
             "ANNOTATION_BODY_INVALID"),
            ({"kind": "unclear", "note": "a" * 8001}, "ANNOTATION_NOTE_INVALID"),
        ):
            with self.subTest(reason=reason):
                code, doc = self.post("/api/phone/reading/annotations",
                                      self.base(**overrides))
                self.assertEqual((code, doc["reason_code"]), (400, reason))
        self.assertEqual(self.reading.calls, [], "a refused annotation must not reach the owner")


# ---------------------------------------------------------------------------------------------
# GET /api/phone/reading/operations/<key>
# ---------------------------------------------------------------------------------------------


class ReadingOperationTest(ReadingAPIBase):
    def test_operation_looks_up_the_original_key_and_passes_the_answer_through(self) -> None:
        self.build()
        code, doc = self.call("GET", "/api/phone/reading/operations/import-1")
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["status"], "applied")
        self.assertEqual(doc["data"]["action"], "reading_import")
        self.assertEqual(self.reading.calls, [("operation", "import-1")])

    def test_unknown_results_keep_their_own_status_and_key(self) -> None:
        self.build(reading=StubReading(operation={"ok": False, "status": "unknown",
                                                  "reason_code": "SUPPLY_RESULT_UNKNOWN",
                                                  "data": {"operation_key": "import-1"}}))
        code, doc = self.call("GET", "/api/phone/reading/operations/import-1")
        self.assertEqual((code, doc["status"]), (202, "unknown"))
        self.assertEqual(self.reading.calls, [("operation", "import-1")])

    def test_a_never_landed_key_is_not_found_and_is_not_retried_with_a_new_key(self) -> None:
        self.build(reading=StubReading(operation={"ok": False, "status": "not_found",
                                                  "reason_code": "OPERATION_NOT_FOUND",
                                                  "data": {"operation_key": "ghost"}}))
        code, doc = self.call("GET", "/api/phone/reading/operations/ghost")
        self.assertEqual((code, doc["status"]), (404, "not_found"))
        self.assertEqual(self.reading.calls, [("operation", "ghost")])

    def test_a_key_that_is_not_a_legal_operation_key_never_reaches_the_owner(self) -> None:
        self.build()
        for suffix in ("has%20space", "../material", "a" * 200, "a%2Fb"):
            with self.subTest(suffix=suffix):
                code, doc = self.call("GET", "/api/phone/reading/operations/" + suffix)
                self.assertEqual((code, doc["reason_code"]), (404, "NOT_FOUND"))
        self.assertEqual(self.reading.calls, [])

    def test_the_reading_operation_route_is_get_only(self) -> None:
        self.build()
        code, doc = self.call("POST", "/api/phone/reading/operations/import-1")
        self.assertEqual((code, doc["reason_code"]), (404, "NOT_FOUND"))
        self.assertEqual(self.reading.calls, [])


# ---------------------------------------------------------------------------------------------
# the legacy reading list
# ---------------------------------------------------------------------------------------------


class LegacyReadingTest(ReadingAPIBase):
    def test_legacy_reading_never_carries_a_body_or_touches_the_supply_root(self) -> None:
        self.build()
        before = self.files_under_root()
        code, doc = self.call("GET", "/api/phone/reading")
        self.assertEqual(code, 200, doc)
        for key in ("text", "content", "annotations", "material"):
            self.assertNotIn(key, doc)
        # No socket in the fixture, so the owner honestly says unavailable instead of faking it.
        self.assertEqual(doc["status"], "unavailable")
        self.assertEqual(self.reading.calls, [], "the legacy list must not read the facade")
        self.assertEqual(self.files_under_root(), before)


# ---------------------------------------------------------------------------------------------
# C's own http_status hint and the real facade (narrow integration, not a second owner)
# ---------------------------------------------------------------------------------------------

try:  # C's facade is another writer's file; the narrow test skips only if the tree lacks it
    import phone_reading as real_phone_reading  # noqa: E402
except Exception:  # pragma: no cover - only outside the integrated tree
    real_phone_reading = None

try:  # the memo item type is C's constant; a half-written owner file must skip, not fail, here
    from private_daily_items import ITEM_MEMO as MEMO_ITEM_TYPE  # noqa: E402
except Exception:  # pragma: no cover - C is editing its own file
    MEMO_ITEM_TYPE = None


class ReadingOwnerHintTest(ReadingAPIBase):
    def test_a_400_hint_is_forwarded_as_an_input_error(self) -> None:
        self.build()
        self.reading.answer("replace", {
            "ok": False, "status": "rejected", "reason_code": "CONTENT_EMPTY",
            "http_status": 400, "data": {"reason_code": "CONTENT_EMPTY"}})
        code, doc = self.post("/api/phone/reading/material",
                              {"content": "正文", "expected_revision": "rev-1",
                               "operation_key": "import-1"})
        self.assertEqual((code, doc["status"]), (400, "rejected"))
        self.assertEqual(doc["reason_code"], "CONTENT_EMPTY")

    def test_a_hint_can_never_flip_the_meaning_of_the_answer(self) -> None:
        self.build()
        for hint, expected in ((200, 409), (202, 409), (500, 409), ("400", 409), (True, 409)):
            with self.subTest(hint=repr(hint)):
                self.reading.answer("replace", {
                    "ok": False, "status": "rejected",
                    "reason_code": "READING_REVISION_CONFLICT",
                    "http_status": hint, "data": {}})
                code, _doc = self.post("/api/phone/reading/material",
                                       {"content": "正文", "expected_revision": "rev-1",
                                        "operation_key": "import-1"})
                self.assertEqual(code, expected)
        self.reading.answer("replace", {
            "ok": True, "status": "applied", "http_status": 409, "data": {}})
        code, _doc = self.post("/api/phone/reading/material",
                               {"content": "正文", "expected_revision": "rev-1",
                                "operation_key": "import-1"})
        self.assertEqual(code, 200)


@unittest.skipIf(real_phone_reading is None, "C 的 PhoneReading 尚未落地")
class RealFacadeConformanceTest(ReadingAPIBase):
    """真实 C facade 的窄测：P 造的就是 C 要的那个对象，且它的话原样回来。"""

    def real_service(self) -> tuple[phone_service.PhoneService, dict]:
        root = unique_root("rc1-reading-real-")
        instance = instance_for(root)
        socket_path = Path(instance["supply_socket"])
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        # A real path that is not a live owner: the port builds, the call fails honestly.
        socket_path.write_bytes(b"not a socket")
        return phone_service.PhoneService(instance, environment={}), instance

    @posix_only
    def test_p_constructs_the_exact_facade_c_documents(self) -> None:
        service, _instance = self.real_service()
        facade = service._reading_instance()
        self.assertIsInstance(facade, real_phone_reading.PhoneReading)
        self.assertEqual(facade.reading_artifact_id, READING_ARTIFACT)
        self.assertEqual(facade.private_items_artifact_id, "artifact-items")
        self.assertEqual(facade.workspace_id, "ws-reading")
        self.assertEqual(facade.grant_id, "grant-reading")
        self.assertEqual(facade.source_id, service.reading_source_id())
        self.assertEqual(facade.source_id, real_phone_reading.reading_source_id(READING_ARTIFACT))

    def test_p_forwards_exactly_cs_parameter_names(self) -> None:
        import inspect

        # Two roots with the same prefix, minted back to back in this process, must never share a
        # name: the parent's POSIX run failed exactly here (same pid, same millisecond).
        first = unique_root("rc1-reading-real-")
        second = unique_root("rc1-reading-real-")
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir() and second.is_dir(), "both retained fixture roots exist")

        init = inspect.signature(real_phone_reading.PhoneReading.__init__)
        self.assertEqual(list(init.parameters),
                         ["self", "port", "reading_artifact_id", "private_items_artifact_id",
                          "workspace_id", "grant_id", "clock"])
        self.assertEqual(str(init.parameters["reading_artifact_id"].kind), "KEYWORD_ONLY")
        material = inspect.signature(real_phone_reading.PhoneReading.material)
        self.assertEqual(list(material.parameters), ["self", "page"])
        self.assertIsNone(material.parameters["page"].default)
        for name, expected in (
            ("replace_material", ["self", "content", "expected_revision", "operation_key"]),
            ("save_position", ["self", "page", "source_revision", "expected_head",
                               "operation_key"]),
            ("annotate", ["self", "kind", "page", "source_revision", "expected_head",
                          "operation_key", "title", "body", "note"]),
            ("operation", ["self", "operation_key"]),
        ):
            signature = inspect.signature(getattr(real_phone_reading.PhoneReading, name))
            self.assertEqual(list(signature.parameters), expected, name)

    @posix_only
    def test_the_real_facade_answers_through_p_in_its_own_words(self) -> None:
        service, _instance = self.real_service()
        result = service.reading_material(None)
        self.assertEqual((result["ok"], result["status"]), (False, "unavailable"))
        self.assertEqual(result["reason_code"], "SUPPLY_UNAVAILABLE")
        # The detail is C's own: P's pre-check would never have produced it.
        self.assertIn("LIFE_SUPPLY_UNREACHABLE", str(result["data"].get("detail")))
        self.assertNotIn("text", result["data"])


@unittest.skipIf(MEMO_ITEM_TYPE is None, "C 的 private_daily_items 当前不可导入")
class NotesReadingAnchorTest(ReadingAPIBase):
    """The reading anchor on a memo: passed through verbatim, optional, and never body text."""

    ANCHOR = {"source_id": "artifact:" + READING_ARTIFACT,
              "source_revision": "artifact:%s:v3" % READING_ARTIFACT,
              "page": 2}

    def fixtures(self):
        rows = [
            {"item_id": "memo:with-anchor", "item_type": MEMO_ITEM_TYPE, "title": "页边",
             "revisions": 2, "created_at": "2026-10-22T00:00:00Z",
             "updated_at": "2026-10-22T00:00:00Z", "reading_anchor": dict(self.ANCHOR)},
            {"item_id": "memo:old", "item_type": MEMO_ITEM_TYPE, "title": "旧的",
             "revisions": 1, "created_at": "2026-10-20T00:00:00Z",
             "updated_at": "2026-10-20T00:00:00Z"},
            {"item_id": "memo:odd", "item_type": MEMO_ITEM_TYPE, "title": "残缺",
             "revisions": 1, "reading_anchor": {"source_id": "artifact:x", "page": "2"}},
            {"item_id": "pos:1", "item_type": "reading_position", "source_id": "artifact:x",
             "source_revision": "artifact:x:v1", "position": 2},
        ]
        bodies = {"memo:with-anchor": "这一页想到别的事", "memo:old": "旧笔记"}
        return rows, bodies

    def test_notes_passes_the_anchor_through_and_keeps_old_memos_unchanged(self) -> None:
        rows, bodies = self.fixtures()
        self.build(items=StubOwnerItems(rows, bodies), port=StubOwnerPort())
        before = self.files_under_root()
        code, doc = self.call("GET", "/api/phone/notes")
        self.assertEqual(code, 200, doc)
        self.assertIs(doc["ok"], True)
        by_id = {row["id"]: row for row in doc["items"]}
        # 1) the anchor of an annotated memo comes back exactly, protocol ids untouched.
        self.assertEqual(by_id["memo:with-anchor"]["reading_anchor"], self.ANCHOR)
        self.assertEqual(by_id["memo:with-anchor"]["body"], "这一页想到别的事")
        # 2) a memo without an anchor keeps the old shape: no null, no invented ids.
        self.assertNotIn("reading_anchor", by_id["memo:old"])
        self.assertEqual(by_id["memo:old"]["body"], "旧笔记")
        # 3) a malformed anchor is omitted, never repaired, and does not break the list.
        self.assertNotIn("reading_anchor", by_id["memo:odd"])
        # 4) only memos are notes: a reading position is not listed as one.
        self.assertNotIn("pos:1", by_id)
        # A notes GET is still a pure read.
        self.assertEqual(self.journal(), [])
        self.assertEqual(self.files_under_root(), before)

    def test_the_anchor_helper_keeps_protocol_ids_exact(self) -> None:
        anchor = {"source_id": "artifact:a/b:c", "source_revision": "artifact:a/b:c:v7", "page": 7}
        self.assertEqual(phone_service.memo_reading_anchor({"reading_anchor": anchor}), anchor)
        # Extra keys are dropped; the three protocol fields are kept as given.
        self.assertEqual(phone_service.memo_reading_anchor(
            {"reading_anchor": dict(anchor, stale=True)}), anchor)
        self.assertIsNone(phone_service.memo_reading_anchor({}))
        for broken in (None, "artifact:a:v1:2", {}, {"source_id": "s", "source_revision": "r"},
                       {"source_id": "s", "source_revision": "r", "page": True},
                       {"source_id": "s", "source_revision": "r", "page": 0},
                       {"source_id": "s", "source_revision": "r", "page": 1.5},
                       {"source_id": "  ", "source_revision": "r", "page": 1},
                       {"source_id": 1, "source_revision": "r", "page": 1},
                       {"source_id": "s", "source_revision": "", "page": 1}):
            with self.subTest(broken=repr(broken)):
                self.assertIsNone(phone_service.memo_reading_anchor({"reading_anchor": broken}))

    def test_metadata_and_state_never_carry_note_bodies_or_anchors(self) -> None:
        rows, bodies = self.fixtures()
        self.build(items=StubOwnerItems(rows, bodies), port=StubOwnerPort(),
                   shopping=StubStateShopping(), world=StubStateWorld())
        # ``/state`` only asks the private owner when the supply socket path exists; the read side
        # is the stub injected above, so a plain file is enough to reach P's summarizer.
        socket_path = Path(self.instance["supply_socket"])
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.write_bytes(b"stub-port")
        code, notes = self.call("GET", "/api/phone/notes")
        self.assertEqual(code, 200, notes)
        blob = json.dumps(notes, ensure_ascii=False)
        self.assertIn("这一页想到别的事", blob, "the private notes read carries the body")
        code, state = self.call("GET", "/api/phone/state")
        self.assertEqual(code, 200, state)
        blob = json.dumps(state, ensure_ascii=False)
        for private in ("这一页想到别的事", "旧笔记", "memo:with-anchor",
                        "artifact:%s:v3" % READING_ARTIFACT):
            self.assertNotIn(private, blob)
        self.assertNotIn("reading_anchor", blob)
        notes_app = state["apps"]["notes"]["data"]
        self.assertEqual(notes_app["item_count"], 3)
        self.assertNotIn("body", notes_app)
        self.assertNotIn("items", notes_app)


if __name__ == "__main__":
    unittest.main(verbosity=2)

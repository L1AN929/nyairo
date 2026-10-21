#!/usr/bin/env python3
"""P 的 ``chiyo_phone`` 工具：阅读工作台五个动作只走同一个 owner，入口与两把钥匙不变。

本文件只跑本轮新增的动作与直接受影响的边界，不重刷旧手机工具用例：

* 夹具是一个真实的 loopback 手机服务（真实 ``create_server`` + 真实 ``PhoneService``），C 的
  ``PhoneReading`` 与两个既有 facade 用桩替换；工具进程真的通过 HTTP 提交，断言看的是**工具发
  出去的东西**和 owner 桩收到的调用，不是"函数返回了字符串"。
* profile 是临时目录里的一份真实 ``chiyo/config.json``：``phone.enabled`` / ``write_enabled`` /
  ``private_artifacts`` 与 ``gateway_bindings`` 都按原规则生效，工具不新增入口。
* 阅读是私人面：受限记忆下没有 ``phone.private_artifacts`` 时五个动作一起拒，钥匙与旧笔记一致。
* 夹具目录保留（不删除、不清理），名字唯一。
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
for entry in (str(ROOT), str(ROOT / "components")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import server as phone_server, service as phone_service  # noqa: E402

CSRF = "reading-tool-csrf"
BOUND_DM = "telegram:bot:dm:42"
READING_ARTIFACT = "artifact-read-1"


def fixture_base() -> Path:
    override = os.environ.get("NYAIRO_READING_TEST_ROOT") or os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-reading-pi") if override
            else (Path(tempfile.gettempdir()) / "nyairo-reading-pi"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def unique_root(prefix: str) -> Path:
    root = fixture_base() / (prefix + "%d-%d" % (os.getpid(), int(time.time() * 1000) % 1000000))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class StubReading:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def material(self, page=None):
        self.calls.append(("material", page))
        wanted = 2 if page is None else int(page)
        return {"ok": True, "status": "applied", "data": {
            "available": True, "artifact_id": READING_ARTIFACT,
            "source_id": "artifact:" + READING_ARTIFACT,
            "source_revision": "artifact:%s:v3" % READING_ARTIFACT, "version_number": 3,
            "title": "夜航手记", "page": wanted, "pages": 3,
            "text": "第%d页的真实正文。" % wanted, "content_available": True,
            "private_head": "pdi-head-9",
            "bookmark": {"page": wanted,
                         "source_revision": "artifact:%s:v3" % READING_ARTIFACT,
                         "needs_reread": False},
            "understanding": "UNKNOWN", "annotations": []}}

    def replace_material(self, content, expected_revision, operation_key):
        self.calls.append(("replace_material", content, expected_revision, operation_key))
        return {"ok": True, "status": "applied", "data": {
            "artifact_id": READING_ARTIFACT,
            "source_revision": "artifact:%s:v4" % READING_ARTIFACT, "version_number": 4,
            "page": 1, "pages": 2, "operation_key": operation_key, "needs_reread": True}}

    def save_position(self, page, source_revision, expected_head, operation_key):
        self.calls.append(("save_position", page, source_revision, expected_head, operation_key))
        return {"ok": True, "status": "applied", "data": {
            "page": page, "source_revision": source_revision, "private_head": "pdi-head-10",
            "understanding": "UNKNOWN", "operation_key": operation_key}}

    def annotate(self, kind, page, source_revision, expected_head, operation_key,
                 title=None, body=None, note=None):
        self.calls.append(("annotate", kind, page, source_revision, expected_head,
                           operation_key, title, body, note))
        return {"ok": True, "status": "applied", "data": {
            "id": "memo:2", "kind": kind, "page": page, "operation_key": operation_key,
            "understanding": "UNKNOWN"}}

    def operation(self, operation_key):
        self.calls.append(("operation", operation_key))
        if operation_key == "ghost":
            return {"ok": False, "status": "not_found", "reason_code": "OPERATION_NOT_FOUND",
                    "data": {"operation_key": operation_key}}
        if operation_key == "pending":
            return {"ok": False, "status": "unknown", "reason_code": "SUPPLY_RESULT_UNKNOWN",
                    "data": {"operation_key": operation_key}}
        return {"ok": True, "status": "applied", "data": {
            "operation_key": operation_key, "action": "reading_import", "status": "applied"}}


class StubShopping:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def wallet(self):
        self.calls.append(("wallet",))
        return {"ok": True, "status": "applied", "data": {"currency": "JPY", "balance": 9760}}

    def shop(self):
        self.calls.append(("shop",))
        return {"ok": True, "status": "applied", "data": {"currency": "JPY", "items": []}}

    def store(self):
        self.calls.append(("store",))
        return {"ok": True, "status": "applied", "data": {
            "store_id": "home_goods_store", "currency": "JPY", "catalog": [],
            "availability": {"status": "OPEN"}}}

    def inventory(self):
        self.calls.append(("inventory",))
        return {"ok": True, "status": "applied", "data": {"items": []}}


class StubWorld:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def snapshot(self):
        self.calls.append(("snapshot",))
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


class ReadingToolBase(unittest.TestCase):
    """One profile + one loopback phone service per test."""

    def setUp(self) -> None:
        self.root = unique_root("rc1-reading-tool-")
        self.profile = self.root / "profile"
        self.profile.mkdir()
        self.instance_root = self.root / "instance"
        world_home = self.instance_root / "world"
        world_home.mkdir(parents=True)
        self.reading = StubReading()
        self.shopping = StubShopping()
        self.world = StubWorld()
        self.instance = {
            "schema_version": phone_service.SCHEMA_INSTANCE,
            "kind": phone_service.KIND_INSTANCE,
            "name": "reading-tool-fixture",
            "subject": "chiyo",
            "local_owner_uid": 0,
            "profile_home": str(self.instance_root),
            "root": str(self.instance_root),
            "world_home": str(world_home),
            "world_id": "w-reading",
            "supply_socket": str(self.instance_root / "run" / "supply-none.sock"),
            "private_items_artifact_id": "artifact-items",
            "artifact_grant_id": "grant-reading",
            "workspace_id": "ws-reading",
            "reading_artifact_id": READING_ARTIFACT,
            "world_body_socket": str(world_home / "run" / "gateway.sock"),
            "features": {},
        }
        self.service = phone_service.PhoneService(self.instance, environment={},
                                                  shopping=self.shopping, world=self.world,
                                                  reading=self.reading)
        self.httpd = phone_server.create_server(self.service, csrf_token=CSRF,
                                                port=free_port())
        self.base_url = "http://127.0.0.1:%d" % int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.write_config()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)
        self.assertFalse(self.thread.is_alive(), "the fixture server did not stop")

    def write_config(self, *, write_enabled: bool = True, private_artifacts: bool = True,
                     memory=False, memory_tool_policy=None, bindings=None) -> dict:
        config = {
            "gateway_bindings": {"telegram": list(bindings or [BOUND_DM])},
            "memory": memory,
            "phone": {
                "enabled": True,
                "base_url": self.base_url,
                "write_enabled": bool(write_enabled),
                "private_artifacts": bool(private_artifacts),
                "profile_home": str(self.instance_root),
            },
        }
        if memory_tool_policy is not None:
            config["memory_tool_policy"] = memory_tool_policy
        (self.profile / "chiyo").mkdir(exist_ok=True)
        (self.profile / "chiyo" / "config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8")
        return config

    def call(self, args, **kwargs) -> dict:
        options = {"hermes_home": str(self.profile), "platform": "telegram",
                   "conversation_key": BOUND_DM}
        options.update(kwargs)
        return json.loads(phone_tool.phone_handler(args, **options))

    def reading_calls(self, name: str) -> list[tuple]:
        return [call for call in self.reading.calls if call[0] == name]

    def journal(self) -> list[dict]:
        path = self.instance_root / "phone" / "operations.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------------------------
# reading_material
# ---------------------------------------------------------------------------------------------


class ReadingMaterialToolTest(ReadingToolBase):
    def test_material_is_a_read_that_returns_the_page_text(self) -> None:
        result = self.call({"action": "reading_material"})
        self.assertIs(result["ok"], True, result)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.reading_calls("material"), [("material", None)])
        self.assertEqual(result["result"]["data"]["text"], "第2页的真实正文。")
        self.assertEqual(result["result"]["data"]["understanding"], "UNKNOWN")

    def test_material_page_is_forwarded_as_a_query_and_validated_locally(self) -> None:
        result = self.call({"action": "reading_material", "page": 3})
        self.assertIs(result["ok"], True, result)
        self.assertEqual(self.reading_calls("material"), [("material", 3)])
        self.assertEqual(result["result"]["data"]["page"], 3)
        self.reading.calls.clear()
        for page in (0, -1, "3", 3.0, True, 65):
            with self.subTest(page=repr(page)):
                refused = self.call({"action": "reading_material", "page": page})
                self.assertIs(refused["ok"], False)
                self.assertEqual(refused["reason_code"], "INVALID_ARGUMENTS")
        self.assertEqual(self.reading.calls, [], "a refused page must not reach the owner")

    def test_material_read_needs_the_private_artifact_permit_not_write(self) -> None:
        self.write_config(write_enabled=False, private_artifacts=True)
        result = self.call({"action": "reading_material"})
        self.assertIs(result["ok"], True, result)
        self.write_config(write_enabled=True, private_artifacts=False, memory=True,
                          memory_tool_policy="disclosure_limited")
        refused = self.call({"action": "reading_material"})
        self.assertIs(refused["ok"], False, refused)
        self.assertEqual(refused["reason_code"], "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")
        self.assertEqual(len(self.reading_calls("material")), 1)


# ---------------------------------------------------------------------------------------------
# reading_import
# ---------------------------------------------------------------------------------------------


class ReadingImportToolTest(ReadingToolBase):
    def test_import_posts_exactly_the_text_the_revision_and_the_key(self) -> None:
        content = "第一页%%PAGE%%第二页"
        result = self.call({"action": "reading_import", "content": content,
                            "expected_revision": "artifact:%s:v3" % READING_ARTIFACT,
                            "operation_key": "import-1"})
        self.assertIs(result["ok"], True, result)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.reading_calls("replace_material"),
                         [("replace_material", content,
                           "artifact:%s:v3" % READING_ARTIFACT, "import-1")])
        rows = self.journal()
        self.assertEqual((rows[0]["action"], rows[0]["operation_key"]),
                         ("reading_import", "import-1"))
        self.assertEqual(rows[0]["actor"], "model")
        self.assertIn("旧版本保留", result["message"])

    def test_import_needs_write_enabled_and_the_private_artifact_permit(self) -> None:
        self.write_config(write_enabled=False)
        refused = self.call({"action": "reading_import", "content": "正文",
                             "expected_revision": "rev-1", "operation_key": "import-1"})
        self.assertEqual(refused["reason_code"], "PHONE_WRITE_DISABLED")
        self.write_config(private_artifacts=False, memory=True,
                          memory_tool_policy="disclosure_limited")
        refused = self.call({"action": "reading_import", "content": "正文",
                             "expected_revision": "rev-1", "operation_key": "import-1"})
        self.assertEqual(refused["reason_code"], "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")
        self.assertEqual(self.reading.calls, [])

    def test_import_text_bounds_are_checked_before_any_request(self) -> None:
        for bad in ("", "   ", "a" * 24001, "字" * 8001, "正文\x00", 5, None):
            with self.subTest(value=repr(bad)[:16]):
                refused = self.call({"action": "reading_import", "content": bad,
                                     "expected_revision": "rev-1", "operation_key": "import-1"})
                self.assertIs(refused["ok"], False)
                self.assertEqual(refused["reason_code"], "INVALID_ARGUMENTS")
        self.assertEqual(self.reading.calls, [])
        result = self.call({"action": "reading_import", "content": "字" * 8000,
                            "expected_revision": "rev-1", "operation_key": "import-big"})
        self.assertIs(result["ok"], True, result)

    def test_import_refuses_an_artifact_or_title_field_before_sending(self) -> None:
        for field, value in (("artifact_id", "artifact-other"), ("title", "标题"),
                             ("path", "/tmp/x"), ("understanding", "KNOWN")):
            with self.subTest(field=field):
                refused = self.call({"action": "reading_import", "content": "正文",
                                     "expected_revision": "rev-1", "operation_key": "import-1",
                                     field: value})
                self.assertEqual(refused["reason_code"], "UNKNOWN_FIELD")
                self.assertEqual(refused["fields"], [field])
        self.assertEqual(self.reading.calls, [])

    def test_a_rejected_import_keeps_the_owner_reason(self) -> None:
        self.reading.replace_material = lambda content, expected_revision, operation_key: {
            "ok": False, "status": "rejected", "reason_code": "READING_REVISION_CONFLICT",
            "data": {"reason_code": "READING_REVISION_CONFLICT"}}
        result = self.call({"action": "reading_import", "content": "正文",
                            "expected_revision": "rev-old", "operation_key": "import-old"})
        self.assertIs(result["ok"], False)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["result"]["reason_code"], "READING_REVISION_CONFLICT")
        self.assertIn("版本/head 已变", result["message"])


# ---------------------------------------------------------------------------------------------
# reading_position
# ---------------------------------------------------------------------------------------------


class ReadingPositionToolTest(ReadingToolBase):
    def test_position_forwards_a_strict_page(self) -> None:
        result = self.call({"action": "reading_position", "page": 1,
                            "source_revision": "artifact:%s:v3" % READING_ARTIFACT,
                            "expected_head": "pdi-head-9", "operation_key": "pos-1"})
        self.assertIs(result["ok"], True, result)
        self.assertEqual(self.reading_calls("save_position"),
                         [("save_position", 1, "artifact:%s:v3" % READING_ARTIFACT,
                           "pdi-head-9", "pos-1")])
        self.assertEqual(result["result"]["data"]["understanding"], "UNKNOWN")
        self.assertIn("不是理解证明", result["message"])

    def test_position_refuses_bool_float_and_string_pages(self) -> None:
        for page in (True, False, 2.0, "2", 0, 65, None):
            with self.subTest(page=repr(page)):
                refused = self.call({"action": "reading_position", "page": page,
                                     "source_revision": "rev-1", "expected_head": "head-1",
                                     "operation_key": "pos-x"})
                self.assertEqual(refused["reason_code"], "INVALID_ARGUMENTS")
        self.assertEqual(self.reading.calls, [])


# ---------------------------------------------------------------------------------------------
# reading_annotate
# ---------------------------------------------------------------------------------------------


class ReadingAnnotateToolTest(ReadingToolBase):
    def test_note_posts_title_and_body_and_never_a_note_field(self) -> None:
        result = self.call({"action": "reading_annotate", "kind": "note", "page": 2,
                            "source_revision": "artifact:%s:v3" % READING_ARTIFACT,
                            "expected_head": "pdi-head-9", "operation_key": "ann-1",
                            "title": "页边", "body": "想到别的事"})
        self.assertIs(result["ok"], True, result)
        self.assertEqual(self.reading_calls("annotate"),
                         [("annotate", "note", 2, "artifact:%s:v3" % READING_ARTIFACT,
                           "pdi-head-9", "ann-1", "页边", "想到别的事", None)])
        rows = self.journal()
        self.assertEqual((rows[0]["action"], rows[0]["operation_key"]),
                         ("reading_annotate", "ann-1"))

    def test_unclear_posts_only_a_note_and_stays_its_own_kind(self) -> None:
        result = self.call({"action": "reading_annotate", "kind": "unclear", "page": 2,
                            "source_revision": "artifact:%s:v3" % READING_ARTIFACT,
                            "expected_head": "pdi-head-9", "operation_key": "ann-1",
                            "note": "这一段没读懂"})
        self.assertIs(result["ok"], True, result)
        self.assertEqual(self.reading_calls("annotate"),
                         [("annotate", "unclear", 2, "artifact:%s:v3" % READING_ARTIFACT,
                           "pdi-head-9", "ann-1", None, None, "这一段没读懂")])

    def test_kind_and_per_kind_fields_are_pinned_before_sending(self) -> None:
        base = {"action": "reading_annotate", "page": 2, "source_revision": "rev-1",
                "expected_head": "head-1", "operation_key": "ann-1"}
        for kind in ("memo", "NOTE", 1, True, None):
            with self.subTest(kind=repr(kind)):
                refused = self.call(dict(base, kind=kind, note="疑问"))
                self.assertEqual(refused["reason_code"], "INVALID_ARGUMENTS")
        refused = self.call(dict(base, kind="note", body="只有正文"))
        self.assertEqual(refused["reason_code"], "INVALID_ARGUMENTS")
        self.assertIn("title", refused["message"])
        refused = self.call(dict(base, kind="note", title="标题", body="正文", note="疑问"))
        self.assertEqual(refused["reason_code"], "UNKNOWN_FIELD")
        refused = self.call(dict(base, kind="unclear", title="标题", note="疑问"))
        self.assertEqual(refused["reason_code"], "UNKNOWN_FIELD")
        refused = self.call(dict(base, kind="unclear"))
        self.assertEqual(refused["reason_code"], "INVALID_ARGUMENTS")
        self.assertEqual(self.reading.calls, [])

    def test_page_not_acquired_message_points_at_the_stop(self) -> None:
        self.reading.annotate = lambda *args, **kwargs: {
            "ok": False, "status": "rejected", "reason_code": "PAGE_NOT_ACQUIRED",
            "data": {"reason_code": "PAGE_NOT_ACQUIRED"}}
        result = self.call({"action": "reading_annotate", "kind": "note", "page": 2,
                            "source_revision": "rev-1", "expected_head": "head-1",
                            "operation_key": "ann-2", "title": "页边", "body": "正文"})
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["result"]["reason_code"], "PAGE_NOT_ACQUIRED")
        self.assertIn("先 reading_position 保存停点", result["message"])


# ---------------------------------------------------------------------------------------------
# reading_operation + the gates
# ---------------------------------------------------------------------------------------------


class ReadingOperationToolTest(ReadingToolBase):
    def test_operation_queries_the_original_key_and_never_retries_with_a_new_one(self) -> None:
        result = self.call({"action": "reading_operation", "operation_key": "import-1"})
        self.assertIs(result["ok"], True, result)
        self.assertEqual(self.reading_calls("operation"), [("operation", "import-1")])
        pending = self.call({"action": "reading_operation", "operation_key": "pending"})
        self.assertEqual(pending["status"], "unknown")
        self.assertIn("不要换 key 重发", pending["message"])
        ghost = self.call({"action": "reading_operation", "operation_key": "ghost"})
        self.assertEqual(ghost["status"], "not_found")
        self.assertEqual(self.reading_calls("operation"),
                         [("operation", "import-1"), ("operation", "pending"),
                          ("operation", "ghost")])

    def test_operation_requires_the_key_and_is_private(self) -> None:
        refused = self.call({"action": "reading_operation"})
        self.assertEqual(refused["reason_code"], "OPERATION_KEY_REQUIRED")
        self.write_config(private_artifacts=False, memory=True,
                          memory_tool_policy="disclosure_limited")
        refused = self.call({"action": "reading_operation", "operation_key": "import-1"})
        self.assertEqual(refused["reason_code"], "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")
        self.assertEqual(self.reading.calls, [])

    def test_every_reading_action_needs_the_private_artifact_permit_under_restricted_memory(
            self) -> None:
        self.write_config(private_artifacts=False, memory=True, memory_tool_policy="disabled")
        bodies = {
            "reading_material": {},
            "reading_import": {"content": "正文", "expected_revision": "rev-1",
                               "operation_key": "import-1"},
            "reading_position": {"page": 1, "source_revision": "rev-1", "expected_head": "head-1",
                                 "operation_key": "pos-1"},
            "reading_annotate": {"kind": "note", "page": 1, "source_revision": "rev-1",
                                 "expected_head": "head-1", "operation_key": "ann-1",
                                 "title": "页边", "body": "正文"},
            "reading_operation": {"operation_key": "import-1"},
        }
        for action, extra in bodies.items():
            with self.subTest(action=action):
                refused = self.call(dict({"action": action}, **extra))
                self.assertIs(refused["ok"], False, refused)
                self.assertEqual(refused["reason_code"],
                                 "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")
        self.assertEqual(self.reading.calls, [])

    def test_reading_actions_stay_inside_the_original_entry_rules(self) -> None:
        refused = self.call({"action": "reading_material"}, platform="local")
        self.assertEqual(refused["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")
        refused = self.call({"action": "reading_material"},
                            conversation_key="telegram:bot:dm:999")
        self.assertEqual(refused["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")
        refused = self.call({"action": "reading_material"}, parent_session_id="delegated-1")
        self.assertEqual(refused["reason_code"], "PHONE_DELEGATED_SOURCE_REFUSED")
        self.assertEqual(self.reading.calls, [])

    def test_schema_advertises_the_five_actions_and_their_shapes(self) -> None:
        schema = phone_tool.PHONE_TOOL_SCHEMA
        actions = schema["parameters"]["properties"]["action"]["enum"]
        for action in ("reading_material", "reading_import", "reading_position",
                       "reading_annotate", "reading_operation"):
            self.assertIn(action, actions)
        props = schema["parameters"]["properties"]
        self.assertEqual(props["page"]["type"], "integer")
        self.assertEqual((props["page"]["minimum"], props["page"]["maximum"]), (1, 64))
        self.assertEqual(props["kind"]["enum"], ["note", "unclear"])
        self.assertEqual(props["content"]["maxLength"], 24000)
        # The reading face is private: the schema still offers no artifact/path/authority input.
        for forbidden in ("artifact_id", "path", "url", "authority", "understanding"):
            self.assertNotIn(forbidden, props)
        for action in ("reading_material", "reading_import", "reading_position",
                       "reading_annotate", "reading_operation"):
            self.assertIn(action, phone_tool.PRIVATE_ARTIFACT_ACTIONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)

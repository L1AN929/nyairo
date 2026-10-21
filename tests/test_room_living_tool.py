#!/usr/bin/env python3
"""P 的 ``chiyo_phone`` 工具：实体店/柜台购买/房间动作只走同一个 owner，入口与开关不变。

本文件只跑本轮新增的动作与直接受影响的边界，不重刷旧手机工具用例：

* 夹具是一个真实的 loopback 手机服务（真实 ``create_server`` + 真实 ``PhoneService``），两个
  owner facade 用桩替换；工具进程真的通过 HTTP 提交，断言看的是**工具发出去的东西**和
  owner 桩收到的调用，不是"函数返回了字符串"。
* profile 是临时目录里的一份真实 ``chiyo/config.json``：``phone.enabled`` / ``write_enabled`` /
  ``private_artifacts`` 与 ``gateway_bindings`` 都按原规则生效，工具不新增入口。
* 夹具目录保留（不删除），名字唯一。
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

CSRF = "room-tool-csrf"
BOUND_DM = "telegram:bot:dm:42"


# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------


def fixture_base() -> Path:
    base = Path(os.environ.get("NYAIRO_ROOM_TEST_ROOT")
                or (Path(tempfile.gettempdir()) / "nyairo-room-pi"))
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


class StubShopping:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def store(self) -> dict:
        self.calls.append(("store",))
        return {"ok": True, "status": phone_service.STATUS_OK, "data": {
            "store_id": "home_goods_store",
            "availability": {"status": "OPEN", "scheme": "daily_rhythm"},
            "currency": "JPY",
            "catalog": [{"catalog_item_id": "simple_desk", "name": "书桌", "price": 4200}],
            "purchase_available": True,
        }}

    def store_purchase(self, catalog_item_id, operation_key) -> dict:
        self.calls.append(("store_purchase", catalog_item_id, operation_key))
        return {"ok": True, "status": phone_service.STATUS_OK, "data": {
            "catalog_item_id": catalog_item_id, "operation_key": operation_key,
            "delivery_status": "PENDING"}}

    def wallet(self) -> dict:
        self.calls.append(("wallet",))
        return {"ok": True, "status": phone_service.STATUS_OK, "data": {
            "currency": "JPY", "balance": 9760}}


class StubWorld:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def snapshot(self) -> dict:
        self.calls.append(("snapshot",))
        return {"ok": True, "status": phone_service.STATUS_OK, "data": {
            "world": {"status": "available", "world_id": "w-room", "revision": 3},
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

    def act(self, action, params, operation_key) -> dict:
        self.calls.append(("act", action, dict(params), operation_key))
        return {"ok": True, "status": phone_service.STATUS_OK,
                "data": {"reason_code": "room_action_committed"}}

    def operation(self, operation_key) -> dict:  # pragma: no cover - not used here
        self.calls.append(("operation", operation_key))
        return {"ok": True, "status": phone_service.STATUS_OK, "data": {}}


class RoomToolBase(unittest.TestCase):
    """One profile + one loopback phone service per test."""

    def setUp(self) -> None:
        self.root = unique_root("rc1-room-tool-")
        self.profile = self.root / "profile"
        self.profile.mkdir()
        self.instance_root = self.root / "instance"
        world_home = self.instance_root / "world"
        world_home.mkdir(parents=True)
        self.shopping = StubShopping()
        self.world = StubWorld()
        self.instance = {
            "schema_version": phone_service.SCHEMA_INSTANCE,
            "kind": phone_service.KIND_INSTANCE,
            "name": "room-tool-fixture",
            "subject": "chiyo",
            "local_owner_uid": 0,
            "profile_home": str(self.instance_root),
            "root": str(self.instance_root),
            "world_home": str(world_home),
            "world_id": "w-room",
            "supply_socket": str(self.instance_root / "run" / "supply-none.sock"),
            "private_items_artifact_id": "artifact-room",
            "artifact_grant_id": "grant-room",
            "workspace_id": "ws-room",
            "world_body_socket": str(world_home / "run" / "gateway.sock"),
            "features": {},
        }
        self.service = phone_service.PhoneService(self.instance, environment={},
                                                  shopping=self.shopping, world=self.world)
        self.httpd = phone_server.create_server(self.service, csrf_token=CSRF,
                                                port=free_port())
        self.base = "http://127.0.0.1:%d" % int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.write_config()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)
        self.assertFalse(self.thread.is_alive(), "the fixture server did not stop")

    def shop_calls(self, name: str) -> list[tuple]:
        """Only the calls for one facade method: ``/state`` also reads before each command."""

        return [call for call in self.shopping.calls if call[0] == name]

    def world_calls(self, name: str) -> list[tuple]:
        return [call for call in self.world.calls if call[0] == name]

    def write_config(self, *, write_enabled: bool = True, private_artifacts: bool = True,
                     memory=False, memory_tool_policy=None, bindings=None) -> dict:
        config = {
            "gateway_bindings": {"telegram": list(bindings or [BOUND_DM])},
            "memory": memory,
            "phone": {
                "enabled": True,
                "base_url": self.base,
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


# ---------------------------------------------------------------------------------------------
# reads and counter purchases
# ---------------------------------------------------------------------------------------------


class ToolStoreTest(RoomToolBase):
    def test_store_read_goes_through_the_same_phone_service(self) -> None:
        result = self.call({"action": "store"})
        self.assertIs(result["ok"], True, result)
        self.assertEqual(result["result"]["data"]["store_id"], "home_goods_store")
        self.assertEqual(len(self.shop_calls("store")), 2,
                         "one read for the /state tile and one for the explicit GET")
        self.assertEqual(self.shop_calls("store_purchase"), [])
        self.assertNotIn("csrf_token", result["result"])

    def test_store_is_a_read_action_and_takes_no_extra_field(self) -> None:
        result = self.call({"action": "store", "operation_key": "nope"})
        self.assertEqual(result["reason_code"], "UNKNOWN_FIELD")
        self.assertEqual(self.shopping.calls, [])

    def test_counter_purchase_reuses_one_key_for_one_intent(self) -> None:
        first = self.call({"action": "store_purchase", "catalog_item_id": "simple_desk"})
        second = self.call({"action": "store_purchase", "catalog_item_id": "simple_desk"})
        self.assertIs(first["ok"], True, first)
        self.assertEqual(first["operation_key"], second["operation_key"],
                         "the same intent must keep the same idempotency key")
        self.assertEqual([call[2] for call in self.shop_calls("store_purchase")],
                         [first["operation_key"], first["operation_key"]])

    def test_counter_purchase_honours_an_explicit_key(self) -> None:
        result = self.call({"action": "store_purchase", "catalog_item_id": "simple_desk",
                            "operation_key": "counter-001"})
        self.assertEqual(result["operation_key"], "counter-001")
        self.assertEqual(self.shop_calls("store_purchase"),
                         [("store_purchase", "simple_desk", "counter-001")])

    def test_counter_purchase_is_not_a_price_or_identity_surface(self) -> None:
        result = self.call({"action": "store_purchase", "catalog_item_id": "simple_desk",
                            "price": 1})
        self.assertEqual(result["reason_code"], "UNKNOWN_FIELD")
        self.assertEqual(self.shopping.calls, [])


# ---------------------------------------------------------------------------------------------
# the four existing gates still hold for the new actions
# ---------------------------------------------------------------------------------------------


class ToolGateTest(RoomToolBase):
    def test_write_switch_still_guards_the_counter(self) -> None:
        self.write_config(write_enabled=False)
        result = self.call({"action": "store_purchase", "catalog_item_id": "simple_desk"})
        self.assertEqual(result["reason_code"], "PHONE_WRITE_DISABLED")
        self.assertEqual(self.shopping.calls, [])

    def test_delegated_turn_has_no_entry(self) -> None:
        result = self.call({"action": "store_purchase", "catalog_item_id": "simple_desk"},
                           parent_session_id="delegate-1")
        self.assertEqual(result["reason_code"], "PHONE_DELEGATED_SOURCE_REFUSED")
        self.assertEqual(self.shopping.calls, [])

    def test_unbound_chat_entry_is_refused(self) -> None:
        result = self.call({"action": "store"},
                           conversation_key="telegram:bot:dm:99")
        self.assertEqual(result["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")
        result = self.call({"action": "store"}, platform="local", conversation_key=None)
        self.assertEqual(result["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")
        self.assertEqual(self.shopping.calls, [])

    def test_restricted_memory_keeps_the_same_private_artifact_permit(self) -> None:
        self.write_config(memory=True, memory_tool_policy="restricted",
                          private_artifacts=False)
        purchase = self.call({"action": "purchase", "catalog_item_id": "virtual_snack_wafer"})
        counter = self.call({"action": "store_purchase", "catalog_item_id": "simple_desk"})
        self.assertEqual(purchase["reason_code"], "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")
        self.assertEqual(counter["reason_code"], "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED",
                         "the physical counter is the same private-artifact surface as the shop")
        # A world command is not a private artifact; it stays available under the same profile.
        world = self.call({"action": "world_action", "world_action": "TOGGLE_FIXTURE",
                           "params": {"object_id": "lamp-1", "state": "on"}})
        self.assertIs(world["ok"], True, world)
        self.assertEqual(self.world.calls[-1][1], "TOGGLE_FIXTURE")


# ---------------------------------------------------------------------------------------------
# room verbs through the tool
# ---------------------------------------------------------------------------------------------


class ToolRoomVerbTest(RoomToolBase):
    def test_arrange_forwards_the_owner_shape(self) -> None:
        result = self.call({"action": "world_action", "world_action": "ARRANGE",
                            "params": {"object_id": "item-desk-1",
                                       "target": {"kind": "room", "location_id": "home",
                                                  "area_id": "bedroom"}}})
        self.assertIs(result["ok"], True, result)
        verb, params = self.world.calls[-1][1], self.world.calls[-1][2]
        self.assertEqual(verb, "ARRANGE")
        self.assertEqual(params["object_id"], "item-desk-1")
        self.assertEqual(params["target"], {"kind": "room", "location_id": "home",
                                            "area_id": "bedroom"})

    def test_p_does_not_invent_a_second_name_for_arrange(self) -> None:
        # C froze the verb as ARRANGE: the tool must not advertise or forward RELOCATE.
        result = self.call({"action": "world_action", "world_action": "RELOCATE",
                            "params": {"object_id": "item-desk-1",
                                       "target": {"kind": "room", "location_id": "home",
                                                  "area_id": "bedroom"}}})
        self.assertEqual(result["reason_code"], "UNKNOWN_WORLD_ACTION")
        self.assertEqual(self.world_calls("act"), [])

    def test_arrange_refuses_a_container_and_a_missing_object(self) -> None:
        result = self.call({"action": "world_action", "world_action": "ARRANGE",
                            "params": {"object_id": "item-desk-1",
                                       "target": {"kind": "container", "location_id": "home",
                                                  "container_id": "drawer"}}})
        self.assertEqual(result["reason_code"], "INVALID_PARAMS")
        result = self.call({"action": "world_action", "world_action": "ARRANGE",
                            "params": {"target": {"kind": "room", "location_id": "home",
                                                  "area_id": "bedroom"}}})
        self.assertEqual(result["reason_code"], "PARAM_REQUIRED")
        self.assertEqual(self.world_calls("act"), [])

    def test_toggle_fixture_is_absolute(self) -> None:
        result = self.call({"action": "world_action", "world_action": "TOGGLE_FIXTURE",
                            "params": {"object_id": "lamp-1", "state": "off"}})
        self.assertIs(result["ok"], True, result)
        self.assertEqual(self.world.calls[-1][1], "TOGGLE_FIXTURE")
        self.assertEqual(self.world.calls[-1][2], {"state": "off", "object_id": "lamp-1"})
        relaxed = self.call({"action": "world_action", "world_action": "TOGGLE_FIXTURE",
                             "params": {"object_id": "lamp-1", "state": "toggle"}})
        self.assertEqual(relaxed["reason_code"], "INVALID_PARAMS")
        self.assertEqual(len([c for c in self.world.calls if c[0] == "act"]), 1)

    def test_room_verbs_refuse_client_identity_price_and_elapsed(self) -> None:
        for field, value in (("uid", 0), ("price", 900), ("elapsed_seconds", 7200)):
            with self.subTest(field=field):
                result = self.call({"action": "world_action", "world_action": "TOGGLE_FIXTURE",
                                    "params": {"object_id": "lamp-1", "state": "on",
                                               field: value}})
                self.assertEqual(result["reason_code"], "CLIENT_IDENTITY_REFUSED")
        self.assertEqual(self.world_calls("act"), [])

    def test_unknown_room_verb_is_refused_before_any_request(self) -> None:
        result = self.call({"action": "world_action", "world_action": "INSTALL",
                            "params": {"object_id": "lamp-1"}})
        self.assertEqual(result["reason_code"], "UNKNOWN_WORLD_ACTION")
        self.assertEqual(self.world_calls("act"), [])


# ---------------------------------------------------------------------------------------------
# the tool schema still describes exactly what the tool accepts
# ---------------------------------------------------------------------------------------------


class ToolSchemaTest(unittest.TestCase):
    def test_schema_advertises_the_new_actions_and_their_fields(self) -> None:
        schema = phone_tool.PHONE_TOOL_SCHEMA
        actions = schema["parameters"]["properties"]["action"]["enum"]
        for name in ("store", "store_purchase"):
            self.assertIn(name, actions)
        self.assertIn("store", phone_tool.READ_ACTIONS)
        self.assertIn("store_purchase", phone_tool.WRITE_ACTIONS)
        verbs = schema["parameters"]["properties"]["world_action"]["enum"]
        for verb in ("ARRANGE", "TOGGLE_FIXTURE"):
            self.assertIn(verb, verbs)
        # P advertises only the verb names C actually exposes.
        self.assertNotIn("RELOCATE", verbs)
        self.assertEqual(list(phone_tool.WORLD_ACTIONS),
                         ["MOVE", "PICK_UP", "PLACE", "POSE", "REST",
                          "ARRANGE", "TOGGLE_FIXTURE"])
        params = schema["parameters"]["properties"]["params"]["properties"]
        self.assertIn("object_id", params)
        self.assertNotIn("fixture_id", params)
        self.assertEqual(params["state"]["enum"], ["start", "stop", "on", "off"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

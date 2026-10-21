#!/usr/bin/env python3
"""P 的房间/实体店 HTTP 面：只测本轮新增接线与直接受影响的精确边界。

本文件不跑旧手机 45 项、不跑真实世界实例，也不产生任何跨设备依赖：

* 夹具是一份真实的 ``PhoneService`` + 真实 loopback HTTP 服务器（``create_server``），
  两个 owner facade 用**桩**替换：P 的职责只是转发与拒绝，不重算任何 owner 事实，
  所以这里测的是 P 自己那层（路由、白名单、身份拒绝、信封映射、``/state`` 摘要、日志）。
* 断言只读 P 自己的行为与落盘的 ``phone/operations.jsonl``；C 的真实语义由 C 的测试负责。
* 夹具目录建在系统临时目录下且**保留**（不删除），名字唯一。

覆盖：``GET /api/phone/store``（纯读 + Host/Origin 闸门 + facade 未实现时 fail closed）、
``POST /api/phone/store/purchases``（CSRF、身份/价格字段拒绝、未知字段、缺 key、applied/
already_applied/rejected/unavailable 的 HTTP 映射与 reason_code 原样、操作日志）、
``/state`` 的新实体店摘要与房间摘要（缺块 = NOT_REPORTED，灯原样复制）、
``ARRANGE``/``RELOCATE``/``TOGGLE_FIXTURE`` 的形状与黑名单、以及 GET 不写盘。
"""
from __future__ import annotations

import json
import inspect
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

from components.phone import server as phone_server, service as phone_service  # noqa: E402

CSRF = "room-csrf-token"
SNAPSHOT_BLOCKS = ("world", "location", "local_environment", "destinations", "travel",
                   "objects", "body", "actions", "recent_executions")


def fixture_base() -> Path:
    """One retained fixture root per process; never deleted, never reused across classes."""

    base = Path(os.environ.get("NYAIRO_ROOM_TEST_ROOT")
                or (Path(tempfile.gettempdir()) / "nyairo-room-pi"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def unique_root(prefix: str) -> Path:
    base = fixture_base()
    root = base / (prefix + "%d-%d" % (os.getpid(), int(time.time() * 1000) % 1000000))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# ---------------------------------------------------------------------------------------------
# stub owner facades (C's real ones live in components/world/scripts; P never re-implements them)
# ---------------------------------------------------------------------------------------------


def store_payload(**overrides):
    data = {
        "store_id": "home_goods_store",
        "channel": "home_goods_store",
        "availability": {"status": "OPEN", "scheme": "daily_rhythm", "timezone": "Asia/Tokyo"},
        "currency": "JPY",
        "catalog": [
            {"catalog_item_id": "simple_desk", "name": "书桌", "price": 4200},
            {"catalog_item_id": "simple_chair", "name": "椅子", "price": 1800},
            {"catalog_item_id": "bedside_lamp", "name": "床头灯", "price": 900},
        ],
        "purchase_available": True,
    }
    data.update(overrides)
    return {"ok": True, "status": phone_service.STATUS_OK, "data": data}


def world_payload(*, light="present", deliveries=None, owned=None, surfaces=None):
    environment = {"status": "available", "furniture": []}
    if light == "present":
        environment["light"] = {"status": "available", "band": "dim", "power": "on",
                                "fixture_ids": ["shelf_lamp01"], "on_seconds": 120,
                                "declared_source": "catalog:bedside_lamp"}
    # C's real snapshot puts the three room blocks *inside* ``objects`` and the light inside
    # ``local_environment`` (contract §3.1–§3.4).  A top-level copy is a shape no owner sends,
    # so the stub never carries one: P has to be exercised against the real nesting.
    objects = {"status": "available", "here": [], "held": []}
    if deliveries is not None:
        objects["deliveries"] = deliveries
    if owned is not None:
        objects["owned_furniture"] = owned
    if surfaces is not None:
        objects["surfaces"] = surfaces
    data = {
        "world": {"status": "available", "world_id": "w-room", "revision": 7},
        "location": {"status": "available", "node": "home/bedroom", "place_id": "home",
                     "area_id": "bedroom", "name": "卧室"},
        "local_environment": environment,
        "destinations": {"status": "available", "nodes": []},
        "travel": {"status": "none"},
        "objects": objects,
        "body": {"status": "available", "pose": "standing"},
        "actions": {"status": "available",
                    "verbs": [{"verb": "ARRANGE", "available": True, "blocked_reason": None},
                              {"verb": "TOGGLE_FIXTURE", "available": True,
                               "blocked_reason": None}]},
        "recent_executions": {"status": "available", "items": []},
    }
    return {"ok": True, "status": phone_service.STATUS_OK, "data": data}


class StubShopping:
    """Records what P actually forwarded; answers with whatever the test declared."""

    def __init__(self, *, store=None, purchase=None):
        self.calls: list[tuple] = []
        self._store = store_payload() if store is None else store
        self._purchase = ({"ok": True, "status": phone_service.STATUS_OK,
                           "data": {"catalog_item_id": "simple_desk",
                                    "operation_key": "k-1",
                                    "delivery_status": "PENDING"}}
                          if purchase is None else purchase)

    def store(self) -> dict:
        self.calls.append(("store",))
        return dict(self._store)

    def store_purchase(self, catalog_item_id, operation_key):
        self.calls.append(("store_purchase", catalog_item_id, operation_key))
        return dict(self._purchase)


class NoStoreFacade:
    """An older facade with no ``store`` method at all: P must say "not implemented"."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def store_purchase(self, catalog_item_id, operation_key):  # pragma: no cover - unused
        self.calls.append(("store_purchase", catalog_item_id, operation_key))
        return {"ok": True, "status": phone_service.STATUS_OK, "data": {}}


class StubWorld:
    def __init__(self, *, snapshot=None, act=None):
        self.calls: list[tuple] = []
        self._snapshot = world_payload() if snapshot is None else snapshot
        self._act = act or {"ok": True, "status": phone_service.STATUS_OK,
                            "data": {"reason_code": "arrange_committed"}}

    def snapshot(self):
        self.calls.append(("snapshot",))
        return dict(self._snapshot)

    def act(self, action, params, operation_key):
        self.calls.append(("act", action, dict(params), operation_key))
        return dict(self._act)

    def operation(self, operation_key):
        self.calls.append(("operation", operation_key))
        return {"ok": True, "status": phone_service.STATUS_OK, "data": {"operation_key": operation_key}}



def instance_for(root: Path) -> dict:
    world_home = root / "world"
    world_home.mkdir(parents=True, exist_ok=True)
    return {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "room-fixture",
        "subject": "chiyo",
        "local_owner_uid": 0,
        "profile_home": str(root),
        "root": str(root),
        "world_home": str(world_home),
        "world_id": "w-room",
        "supply_socket": str(root / "run" / "supply-fixture.sock"),
        "private_items_artifact_id": "artifact-room",
        "artifact_grant_id": "grant-room",
        "workspace_id": "ws-room",
        "world_body_socket": str(world_home / "run" / "gateway.sock"),
        "features": {},
    }


class RoomAPIBase(unittest.TestCase):
    """One real loopback server per test; facades are the stubs above."""

    shopping: StubShopping
    world: StubWorld

    def build(self, *, shopping=None, world=None, root=None) -> None:
        self._stop_server()
        self.root = root or unique_root("rc1-room-api-")
        self.shopping = shopping if shopping is not None else StubShopping()
        self.world = world if world is not None else StubWorld()
        self.instance = instance_for(self.root)
        self.service = phone_service.PhoneService(self.instance, environment={},
                                                  shopping=self.shopping, world=self.world)
        self.httpd = phone_server.create_server(self.service, csrf_token=CSRF,
                                                port=free_port())
        self.base = "http://127.0.0.1:%d" % int(self.httpd.server_address[1])
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
        request = urllib.request.Request(self.base + path, data=data, method=method)
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

    def purchase(self, body, *, csrf: bool = True, headers=None):
        extra = dict(headers or {})
        if csrf:
            extra["X-CSRF-Token"] = CSRF
        return self.call("POST", "/api/phone/store/purchases", body, headers=extra)

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


# ---------------------------------------------------------------------------------------------
# GET /api/phone/store
# ---------------------------------------------------------------------------------------------


class StoreReadTest(RoomAPIBase):
    def test_store_read_passes_the_owner_projection_through_and_writes_nothing(self) -> None:
        self.build()
        before = self.files_under_root()
        code, doc = self.call("GET", "/api/phone/store")
        self.assertEqual(code, 200)
        self.assertIs(doc["ok"], True)
        data = doc["data"]
        self.assertEqual(data["store_id"], "home_goods_store")
        self.assertEqual(data["availability"]["status"], "OPEN")
        self.assertEqual([row["catalog_item_id"] for row in data["catalog"]],
                         ["simple_desk", "simple_chair", "bedside_lamp"])
        self.assertEqual([call[0] for call in self.shopping.calls], ["store"])
        self.assertEqual(self.files_under_root(), before, "GET /store wrote something")

    def test_store_read_is_guarded_like_the_world_snapshot(self) -> None:
        self.build()
        code, doc = self.call("GET", "/api/phone/store", headers={"Host": "evil.example"})
        self.assertEqual((code, doc["reason_code"]), (403, "HOST_NOT_ALLOWED"))
        code, doc = self.call("GET", "/api/phone/store", headers={"Origin": "http://evil.example"})
        self.assertEqual((code, doc["reason_code"]), (403, "ORIGIN_NOT_ALLOWED"))
        code, doc = self.call("GET", "/api/phone/store", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual((code, doc["reason_code"]), (403, "CROSS_SITE_REFUSED"))
        self.assertEqual(self.shopping.calls, [], "a refused read must not reach the owner")

    def test_store_read_on_a_facade_without_store_is_not_implemented_not_empty(self) -> None:
        self.build(shopping=NoStoreFacade())
        code, doc = self.call("GET", "/api/phone/store")
        self.assertEqual(code, 503)
        self.assertIs(doc["ok"], False)
        self.assertEqual(doc["status"], "unavailable")
        self.assertEqual(doc["reason_code"], "PHONE_FACADE_UNIMPLEMENTED")
        self.assertEqual(doc["data"], {"method": "store"})

    def test_store_read_is_not_reachable_by_post(self) -> None:
        self.build()
        code, doc = self.call("POST", "/api/phone/store", {"catalog_item_id": "simple_desk"},
                              headers={"X-CSRF-Token": CSRF})
        self.assertEqual((code, doc["reason_code"]), (404, "NOT_FOUND"))


# ---------------------------------------------------------------------------------------------
# POST /api/phone/store/purchases
# ---------------------------------------------------------------------------------------------


class StorePurchaseTest(RoomAPIBase):
    def test_counter_purchase_forwards_exactly_the_catalogue_row_and_the_key(self) -> None:
        self.build()
        code, doc = self.purchase({"catalog_item_id": "simple_desk", "operation_key": "buy-1"},
                                  headers={"X-Phone-Actor": "model"})
        self.assertEqual(code, 200)
        self.assertIs(doc["ok"], True)
        self.assertEqual(doc["status"], "applied")
        self.assertEqual(self.shopping.calls, [("store_purchase", "simple_desk", "buy-1")])
        rows = self.journal()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "store_purchase")
        self.assertEqual(rows[0]["operation_key"], "buy-1")
        self.assertEqual(rows[0]["actor"], "model")
        self.assertIs(rows[0]["ok"], True)

    def test_owner_word_is_never_upgraded(self) -> None:
        for status, code_expected in (("already_applied", 200), ("rejected", 409),
                                      ("unavailable", 503), ("disabled", 409)):
            with self.subTest(status=status):
                self.build(shopping=StubShopping(purchase={
                    "ok": status in ("applied", "already_applied"),
                    "status": status,
                    "reason_code": "STORE_CLOSED",
                    "data": {"reason_code": "STORE_CLOSED"}}))
                code, doc = self.purchase({"catalog_item_id": "bedside_lamp",
                                           "operation_key": "k-" + status})
                self.assertEqual(code, code_expected)
                self.assertEqual(doc["status"], status)
                if status != "already_applied":
                    self.assertEqual(doc["reason_code"], "STORE_CLOSED")

    def test_csrf_is_required_and_checked(self) -> None:
        self.build()
        code, doc = self.purchase({"catalog_item_id": "simple_desk", "operation_key": "k-1"},
                                  csrf=False)
        self.assertEqual((code, doc["reason_code"]), (403, "CSRF_TOKEN_MISSING"))
        code, doc = self.call("POST", "/api/phone/store/purchases",
                              {"catalog_item_id": "simple_desk", "operation_key": "k-1"},
                              headers={"X-CSRF-Token": "not-the-token"})
        self.assertEqual((code, doc["reason_code"]), (403, "CSRF_TOKEN_INVALID"))
        self.assertEqual(self.shopping.calls, [])

    def test_client_identity_price_and_balance_are_refused(self) -> None:
        self.build()
        for field, value in (("uid", 0), ("owner", "chiyo"), ("home", "/tmp/x"),
                             ("price", 1), ("balance", 9760), ("main_self_verified", True)):
            with self.subTest(field=field):
                body = {"catalog_item_id": "simple_desk", "operation_key": "k-" + field}
                body[field] = value
                code, doc = self.purchase(body)
                self.assertEqual(code, 409)
                self.assertIn(doc["reason_code"], {"CLIENT_IDENTITY_REFUSED",
                                                   "CLIENT_IDENTITY_REFUSED:%s" % field})
        self.assertEqual(self.shopping.calls, [])

    def test_unknown_field_and_bad_catalogue_or_key_are_refused(self) -> None:
        self.build()
        code, doc = self.purchase({"catalog_item_id": "simple_desk", "operation_key": "k-1",
                                   "quantity": 2})
        self.assertEqual((code, doc["reason_code"]), (400, "UNKNOWN_FIELD:quantity"))
        code, doc = self.purchase({"operation_key": "k-1"})
        self.assertEqual((code, doc["reason_code"]), (400, "CATALOG_ITEM_INVALID"))
        code, doc = self.purchase({"catalog_item_id": 42, "operation_key": "k-1"})
        self.assertEqual((code, doc["reason_code"]), (400, "CATALOG_ITEM_INVALID"))
        code, doc = self.purchase({"catalog_item_id": "simple_desk"})
        self.assertEqual((code, doc["reason_code"]), (400, "OPERATION_KEY_REQUIRED"))
        code, doc = self.purchase({"catalog_item_id": "simple_desk", "operation_key": "bad key!"})
        self.assertEqual((code, doc["reason_code"]), (400, "OPERATION_KEY_INVALID"))
        self.assertEqual(self.shopping.calls, [])

    def test_body_shape_gates(self) -> None:
        self.build()
        data = json.dumps({"catalog_item_id": "simple_desk", "operation_key": "k-1"}).encode()
        request = urllib.request.Request(self.base + "/api/phone/store/purchases", data=data,
                                         method="POST")
        request.add_header("Content-Type", "text/plain")
        request.add_header("X-CSRF-Token", CSRF)
        try:
            urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as error:
            doc = json.loads(error.read().decode("utf-8"))
            self.assertEqual((error.code, doc["reason_code"]), (400, "CONTENT_TYPE_REQUIRED"))
        else:  # pragma: no cover - the guard must refuse
            self.fail("a non-JSON content type was accepted")
        self.assertEqual(self.shopping.calls, [])


# ---------------------------------------------------------------------------------------------
# /state summaries
# ---------------------------------------------------------------------------------------------


class StateRoomSummaryTest(RoomAPIBase):
    def test_state_carries_a_store_app_and_an_honest_room_block(self) -> None:
        self.build(world=StubWorld(snapshot=world_payload(
            deliveries={"status": "available", "items": [{"item": "desk"}, {"item": "chair"}]},
            owned={"status": "available", "items": [{"item": "desk"}]},
            surfaces={"status": "unavailable", "reason_code": "NO_SURFACE_DECLARED"})))
        code, doc = self.call("GET", "/api/phone/state")
        self.assertEqual(code, 200)
        self.assertEqual(set(doc["apps"]), set(phone_service.APPS))
        store = doc["apps"]["store"]
        self.assertEqual(store["status"], "available", store)
        self.assertEqual(store["data"]["catalog_count"], 3)
        self.assertEqual(store["data"]["store_id"], "home_goods_store")
        self.assertEqual(store["data"]["availability"]["status"], "OPEN")
        # The phone's own virtual shop channel is untouched by the physical store.
        self.assertEqual(doc["apps"]["shop"]["status"], "unavailable")

        world_app = doc["apps"]["world"]
        self.assertEqual(set(world_app["data"]["blocks"]), set(SNAPSHOT_BLOCKS))
        room = world_app["data"]["room"]
        self.assertEqual(room["deliveries"], {"status": "available", "count": 2})
        self.assertEqual(room["owned_furniture"], {"status": "available", "count": 1})
        self.assertEqual(room["surfaces"], {"status": "unavailable",
                                            "reason_code": "NO_SURFACE_DECLARED"})
        self.assertEqual(room["light"]["band"], "dim")
        self.assertEqual(room["light"]["power"], "on")
        self.assertEqual(room["source"], "world_phone_world.snapshot")

    def test_missing_room_blocks_are_not_reported_and_an_unknown_light_is_not_off(self) -> None:
        self.build(world=StubWorld(snapshot=world_payload(light="absent")))
        code, doc = self.call("GET", "/api/phone/state")
        self.assertEqual(code, 200)
        room = doc["apps"]["world"]["data"]["room"]
        for name in ("deliveries", "owned_furniture", "surfaces", "light"):
            self.assertEqual(room[name], {"status": None, "reason_code": "NOT_REPORTED"}, name)
        self.assertNotIn("count", room["deliveries"])
    def test_real_nested_owner_blocks_are_read_where_the_owner_publishes_them(self) -> None:
        """C's three room blocks live under ``objects``; a top-level lookup would drop all three."""
        snapshot = world_payload(
            deliveries={"status": "available", "mode": "temporal_home_delivery",
                        "duration_seconds": 7200, "destination_location": "home/bedroom",
                        "pending": [{"delivery_id": "d-1", "status": "PENDING"}],
                        "delivered": [{"delivery_id": "d-0", "status": "DELIVERED"}],
                        "pending_count": 1, "delivered_count": 1,
                        "realized_by": "world_body_service.tick",
                        "read_delivers_items": False,
                        "items": [{"delivery_id": "d-1", "status": "PENDING"},
                                  {"delivery_id": "d-0", "status": "DELIVERED"}]},
            owned={"status": "available", "source": "world_property_economy",
                   "items": [{"item_instance_id": "item-desk-1",
                              "catalog_item_id": "simple_desk", "delivered": True,
                              "placement": {"kind": "room", "location_id": "home",
                                            "area_id": "bedroom"}}],
                   "count": 1, "arrange_available": True, "unplaced_delivered": [],
                   "read_is_pure": True},
            surfaces={"status": "available", "source": "world_body_substrate",
                      "here": [{"surface_id": "desk_surface:item-desk-1",
                                "surface_kind": "desk_surface"}],
                      "count": 1, "rest_here": [],
                      "declared_kinds": ["bed_surface", "chair_surface", "desk_surface"],
                      "instance_scoped": True, "read_is_pure": True})
        data = snapshot["data"]
        # The fixture itself is guarded: these keys really are only inside ``objects``.
        for name in ("deliveries", "owned_furniture", "surfaces"):
            self.assertNotIn(name, data, name)
            self.assertIn(name, data["objects"], name)

        self.build(world=StubWorld(snapshot=snapshot))
        code, doc = self.call("GET", "/api/phone/state")
        self.assertEqual(code, 200)
        room = doc["apps"]["world"]["data"]["room"]
        for name in ("deliveries", "owned_furniture", "surfaces"):
            self.assertNotEqual(room[name].get("reason_code"), "NOT_REPORTED", name)
        self.assertEqual(room["deliveries"]["status"], "available")
        self.assertEqual(room["deliveries"]["count"], 2)
        self.assertEqual(room["owned_furniture"], {"status": "available", "count": 1})
        self.assertEqual(room["surfaces"]["status"], "available")
        light = room["light"]
        self.assertEqual((light["status"], light["power"], light["band"]),
                         ("available", "on", "dim"))
        self.assertEqual(light["fixture_ids"], ["shelf_lamp01"])
        self.assertEqual(light["on_seconds"], 120)
        self.assertEqual(light["declared_source"], "catalog:bedside_lamp")

    def test_a_nested_owner_block_that_is_unavailable_keeps_its_own_reason(self) -> None:
        self.build(world=StubWorld(snapshot=world_payload(
            deliveries={"status": "unavailable", "reason_code": "ECONOMY_STATE_ERROR",
                        "items": []})))
        code, doc = self.call("GET", "/api/phone/state")
        self.assertEqual(code, 200)
        room = doc["apps"]["world"]["data"]["room"]
        self.assertEqual(room["deliveries"], {"status": "unavailable",
                                              "reason_code": "ECONOMY_STATE_ERROR",
                                              "count": 0})


# ---------------------------------------------------------------------------------------------
# room verbs on the existing world action route
# ---------------------------------------------------------------------------------------------


class RoomVerbTest(RoomAPIBase):
    def world_action(self, action, params, key="room-key-1"):
        body = {"action": action, "params": params, "operation_key": key}
        return self.call("POST", "/api/phone/world/actions", body,
                         headers={"X-CSRF-Token": CSRF})

    def test_arrange_forwards_the_owner_shape(self) -> None:
        self.build()
        code, doc = self.world_action("ARRANGE", {
            "object_id": "item-desk-1",
            "target": {"kind": "room", "location_id": "home", "area_id": "bedroom"}},
            key="arrange-desk")
        self.assertEqual(code, 200, doc)
        self.assertEqual(self.world.calls[-1][0], "act")
        self.assertEqual(self.world.calls[-1][1], "ARRANGE")
        self.assertEqual(self.world.calls[-1][2], {
            "object_id": "item-desk-1",
            "target": {"kind": "room", "location_id": "home", "area_id": "bedroom"}})

    def test_relocate_spelling_is_not_invented_by_p(self) -> None:
        # C froze the verb name as ``ARRANGE``; P must not advertise a second name for it.
        self.build()
        code, doc = self.world_action("RELOCATE", {
            "object_id": "item-desk-1",
            "target": {"kind": "room", "location_id": "home", "area_id": "bedroom"}})
        self.assertEqual((code, doc["reason_code"]), (400, "UNKNOWN_WORLD_ACTION"))
        self.assertEqual(self.world.calls, [])

    def test_arrange_accepts_a_surface_target_and_refuses_other_kinds(self) -> None:
        self.build()
        code, doc = self.world_action("ARRANGE", {
            "object_id": "item-desk-1",
            "target": {"kind": "surface", "location_id": "home", "surface_id": "desk_surface"}})
        self.assertEqual(code, 200, doc)
        self.assertEqual(self.world.calls[-1][2]["target"]["surface_id"], "desk_surface")
        code, doc = self.world_action("ARRANGE", {
            "object_id": "item-desk-1",
            "target": {"kind": "container", "location_id": "home", "container_id": "drawer"}})
        self.assertEqual((code, doc["reason_code"]), (400, "TARGET_KIND_NOT_ALLOWED:container"))
        self.assertEqual(len([c for c in self.world.calls if c[0] == "act"]), 1)

    def test_arrange_needs_an_object_and_a_target(self) -> None:
        self.build()
        code, doc = self.world_action("ARRANGE", {"target": {"kind": "room",
                                                            "location_id": "home",
                                                            "area_id": "bedroom"}})
        self.assertEqual((code, doc["reason_code"]), (400, "PARAM_REQUIRED:object_id"))
        code, doc = self.world_action("ARRANGE", {"object_id": "item-desk-1"})
        self.assertEqual((code, doc["reason_code"]), (400, "PARAM_REQUIRED:target"))
        code, doc = self.world_action("ARRANGE", {"item_instance_id": "item-desk-1",
                                                  "target": {"kind": "room",
                                                             "location_id": "home",
                                                             "area_id": "bedroom"}})
        self.assertEqual((code, doc["reason_code"]), (400, "UNKNOWN_FIELD:item_instance_id"))
        self.assertEqual(self.world.calls, [])

    def test_toggle_fixture_is_absolute_and_never_a_blind_toggle(self) -> None:
        self.build()
        code, doc = self.world_action("TOGGLE_FIXTURE", {"object_id": "lamp-1", "state": "on"},
                                      key="light-on")
        self.assertEqual(code, 200, doc)
        self.assertEqual(self.world.calls[-1][1], "TOGGLE_FIXTURE")
        self.assertEqual(self.world.calls[-1][2], {"state": "on", "object_id": "lamp-1"})
        for bad in ("toggle", "start", "ON", True):
            with self.subTest(state=bad):
                code, doc = self.world_action("TOGGLE_FIXTURE", {"object_id": "lamp-1",
                                                                "state": bad})
                self.assertEqual((code, doc["reason_code"]), (400, "FIXTURE_STATE_INVALID"))
        code, doc = self.world_action("TOGGLE_FIXTURE", {"state": "on"})
        self.assertEqual((code, doc["reason_code"]), (400, "PARAM_REQUIRED:object_id"))
        self.assertEqual(len([c for c in self.world.calls if c[0] == "act"]), 1)

    def test_room_verbs_refuse_client_identity_price_and_elapsed(self) -> None:
        self.build()
        for field, value in (("uid", 0), ("price", 4200), ("balance", 9760),
                             ("elapsed_seconds", 7200), ("fatigue", 0.1)):
            with self.subTest(field=field):
                code, doc = self.world_action("TOGGLE_FIXTURE", {"object_id": "lamp-1",
                                                                 "state": "on", field: value})
                self.assertEqual(code, 409)
                self.assertIn("CLIENT_IDENTITY_REFUSED", doc["reason_code"])
        self.assertEqual(self.world.calls, [])

    def test_unknown_verb_inside_the_room_family_is_still_refused(self) -> None:
        self.build()
        code, doc = self.world_action("INSTALL", {"object_id": "lamp-1"})
        self.assertEqual((code, doc["reason_code"]), (400, "UNKNOWN_WORLD_ACTION"))
        self.assertEqual(self.world.calls, [])


# ---------------------------------------------------------------------------------------------
# conformance with C's real facade (import only: no world instance, no socket, no write)
# ---------------------------------------------------------------------------------------------


class FacadeConformanceTest(unittest.TestCase):
    """P's forwarding convention must match the owner's real signatures and verb table."""

    def _world_scripts(self) -> None:
        path = str(ROOT / "components" / "world" / "scripts")
        if path not in sys.path:
            sys.path.insert(0, path)

    def test_counter_purchase_call_matches_the_shopping_facade(self) -> None:
        self._world_scripts()
        try:
            import world_phone_shopping as wps
        except Exception as exc:  # pragma: no cover - only when C's tree is absent
            self.skipTest("world_phone_shopping not importable here: %s" % type(exc).__name__)
        self.assertEqual(list(inspect.signature(wps.PhoneShopping.store).parameters), ["self"])
        names = list(inspect.signature(wps.PhoneShopping.store_purchase).parameters)
        self.assertEqual(names[1:], ["catalog_item_id", "operation_key"])

    def test_room_verbs_and_param_tables_match_the_world_facade(self) -> None:
        self._world_scripts()
        try:
            import world_phone_world as wpw
        except Exception as exc:  # pragma: no cover - only when C's tree is absent
            self.skipTest("world_phone_world not importable here: %s" % type(exc).__name__)
        self.assertEqual(tuple(wpw.VERBS), phone_service.WORLD_ACTIONS)
        for verb in ("ARRANGE", "TOGGLE_FIXTURE"):
            required, optional = wpw.PARAM_SPEC[verb]
            allowed, must = phone_server.WORLD_ACTION_FIELDS[verb]
            self.assertEqual(set(required) | set(optional), set(allowed), verb)
            self.assertEqual(set(required), set(must), verb)

if __name__ == "__main__":
    unittest.main(verbosity=2)

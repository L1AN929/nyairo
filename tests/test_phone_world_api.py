#!/usr/bin/env python3
"""P 的世界 HTTP 面：真实室内世界 + 真实 World/Body owner + 真实 HTTP 的精确正负。

本文件只跑本轮新增接线与直接受影响的边界，不跑旧全库：

* 夹具是一个全新的 ``prepare-demo --with-world`` 根：worldgen 生成的真实室内场景、
  真实能力声明、真实 substrate 摆放（床是休息面，杯子是可拿物件），真实 10000 JPY genesis；
* 每个用例用 ``serve_instance`` 起真实的 Supply + WorldBodyService 子进程并绑 loopback；
* 断言只读 owner 自己的落盘事实（``world_state.json`` / ``body_substrate.json`` /
  ``runtime.json`` / 账本 / 经济档）以及 C 自己返回的字段，不把"HTTP 200"当证据。

覆盖：``GET /api/phone/world`` 九块与零写入、``/state`` 的 world/body app、CSRF/Host/Origin、
请求体白名单与身份拒绝、五个动词的精确参数、POSE 的 applied/already_applied/目标冲突、
MOVE 的真实 30 秒室内路程（出发不落地、owner tick 到达）、REST 不假装恢复、拿放的诚实拒绝、
``GET /api/phone/world/operations/<key>`` 的对账与 404。

夹具根在 ``/tmp/nyaw05r.jdK3q/rc1-world-*`` 保留，不删除、不清理。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import demo, server as phone_server, service as phone_service  # noqa: E402

BASE = Path(os.environ.get("NYAIRO_V04_PHONE_ROOT") or "/tmp/nyaw05r.jdK3q")
posix_only = unittest.skipIf(os.name == "nt", "需要 POSIX AF_UNIX/fcntl（在 WSL/Linux 上运行）")

#: C's contract §2: the snapshot's fixed blocks.
SNAPSHOT_BLOCKS = ("world", "location", "local_environment", "destinations", "travel",
                   "objects", "body", "actions", "recent_executions")
#: The verbs that were already in ``local_owner_verbs`` before RC1's room work: C may add to
#: this table, but a table that loses one of these is still a regression.
LEGACY_LOCAL_OWNER_VERBS = ("MOVE", "PICK_UP", "PLACE", "POSE", "REST")
#: Never in P's hand: a raw somatic value (contract §2.7).
FORBIDDEN_BODY_KEYS = ("fatigue", "activation", "comfort", "sensory_load", "tension", "warmth",
                       "raw_values", "somatic")


def unique_root(prefix: str) -> Path:
    BASE.mkdir(parents=True, exist_ok=True)
    root = BASE / (prefix + "%d-%d" % (os.getpid(), int(time.time() * 1000) % 1000000))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    return root


def walk_floats(value, path="") -> list[str]:
    found: list[str] = []
    if isinstance(value, float):
        found.append(path or "<root>")
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(walk_floats(item, "%s.%s" % (path, key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(walk_floats(item, "%s[%d]" % (path, index)))
    return found


class PhoneWorldAPIBase(unittest.TestCase):
    """One real ``--with-world`` instance; one real serve session per test."""

    root: Path
    prepared: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = unique_root("rc1-world-api-")
        cls.prepared = demo.prepare_demo(cls.root, with_world=True)

    def setUp(self) -> None:
        self.stop = threading.Event()
        self.ready: dict = {}
        self.error: list = []

        def run() -> None:
            try:
                demo.serve_instance(self.root, port=0, stop_event=self.stop,
                                    on_ready=lambda info: self.ready.update(info),
                                    announce=False)
            except Exception as exc:  # recorded, never hidden
                self.error.append("%s: %s" % (type(exc).__name__, exc))

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.ready.get("port") or self.error:
                break
            time.sleep(0.05)
        if self.error:
            self.fail("serve failed: %s" % self.error[0])
        self.assertTrue(self.ready.get("port"), "serve never became ready")
        self.base = "http://127.0.0.1:%d" % int(self.ready["port"])
        self.instance = phone_service.load_instance(self.root)
        self.world_home = Path(str(self.instance["world_home"]))
        self.csrf = self._csrf()

    def tearDown(self) -> None:
        self.stop.set()
        self.thread.join(timeout=120)
        self.assertFalse(self.thread.is_alive(), "serve did not stop after stop_event")

    # -- transport ------------------------------------------------------------------

    def call(self, method: str, path: str, body=None, *, headers=None,
             raw: bool = False) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
                return int(response.status), (payload if raw else json.loads(payload))
        except urllib.error.HTTPError as error:
            payload = error.read()
            if raw:
                return int(error.code), payload
            try:
                return int(error.code), json.loads(payload)
            except ValueError:
                return int(error.code), {"raw": payload[:200].decode("utf-8", "replace")}

    def _csrf(self) -> str:
        code, doc = self.call("GET", "/api/phone/state")
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc.get("ok"), doc)
        token = doc.get("csrf_token")
        self.assertIsInstance(token, str)
        self.assertEqual(len(token), 64, token)
        return token

    def act(self, action: str, params: dict, key: str) -> tuple[int, dict]:
        return self.call("POST", "/api/phone/world/actions",
                         {"action": action, "params": params, "operation_key": key},
                         headers={"X-CSRF-Token": self.csrf})

    def world(self) -> dict:
        code, doc = self.call("GET", "/api/phone/world")
        self.assertEqual(code, 200, doc)
        self.assertTrue(doc.get("ok"), doc)
        return doc

    # -- owner truths ----------------------------------------------------------------

    def world_files(self) -> dict[str, tuple[int, int]]:
        """Size + mtime of every world/body artifact a GET must never touch."""

        relative = (
            ("data", "world", "world_state.json"),
            ("data", "world", "body_substrate.json"),
            ("data", "body", "runtime.json"),
            ("data", "body", "transitions.jsonl"),
            ("data", "body", "consequences.jsonl"),
            ("state", "world_property_economy.json"),
            ("state", "world_travel_delivery.json"),
        )
        found: dict[str, tuple[int, int]] = {}
        for parts in relative:
            path = self.world_home.joinpath(*parts)
            if path.exists():
                stat = path.stat()
                found[str(path)] = (stat.st_size, stat.st_mtime_ns)
        ledger = self.world_home / "state" / "world_execution_ledger.jsonl"
        if ledger.exists():
            stat = ledger.stat()
            found[str(ledger)] = (stat.st_size, stat.st_mtime_ns)
        return found

    def snapshot_body(self) -> dict:
        return self.world()["data"]["body"]


@posix_only
class PhoneWorldStateTest(PhoneWorldAPIBase):
    def test_state_reports_world_and_an_upgraded_body_app(self) -> None:
        code, doc = self.call("GET", "/api/phone/state")
        self.assertEqual(code, 200)
        self.assertEqual(set(doc["apps"]), set(phone_service.APPS))
        self.assertIn("world", doc["apps"])
        for name, entry in doc["apps"].items():
            self.assertIn(entry["status"], {"available", "unavailable", "disabled"}, name)
        world_app = doc["apps"]["world"]
        self.assertEqual(world_app["status"], "available", world_app)
        self.assertEqual(world_app["data"]["world_id"], self.instance["world_id"])
        self.assertEqual(world_app["data"]["location"]["place_id"], "home")
        self.assertEqual(world_app["data"]["location"]["area_id"], "bedroom")
        self.assertEqual(set(world_app["data"]["blocks"]), set(SNAPSHOT_BLOCKS))
        body_app = doc["apps"]["body"]
        self.assertEqual(body_app["status"], "available", body_app)
        data = body_app["data"]
        self.assertEqual(data["body_status"], "available")
        self.assertIn(data["pose"], {"standing", "seated", "lying", "transitioning"})
        self.assertIsInstance(data["signals"], list)
        self.assertIs(data["bands_only"], True)
        # The body app carries C's projection, never a raw somatic value.
        for key in FORBIDDEN_BODY_KEYS:
            self.assertNotIn(key, data)
        self.assertEqual(walk_floats(data), [])

    def test_world_snapshot_is_complete_and_writes_nothing(self) -> None:
        before = self.world_files()
        self.assertTrue(before, "fixture has no world/body files to compare")
        first = self.world()
        second = self.world()
        after = self.world_files()
        self.assertEqual(before, after, "GET /api/phone/world wrote something")

        data = first["data"]
        self.assertEqual(set(data), set(SNAPSHOT_BLOCKS))
        for name in SNAPSHOT_BLOCKS:
            self.assertIn("status", data[name], name)
        self.assertEqual(data["location"]["node"], "home/bedroom")
        self.assertEqual(data["local_environment"]["status"], "available")
        self.assertTrue(data["local_environment"]["furniture"],
                        "the --with-world fixture must declare real furniture")
        self.assertEqual(data["objects"]["status"], "available")
        here = {row["object_id"]: row for row in data["objects"]["here"]}
        bed = here.get("home_bed01")
        self.assertIsNotNone(bed, here)
        self.assertEqual(bed["placement"]["kind"], "surface")
        self.assertEqual(bed["placement"]["surface_id"], "bed_surface")
        self.assertIs(bed["portable"], False)
        self.assertEqual(data["travel"]["status"], "none")
        # ``local_owner_verbs`` is C's own table (rc1-room-living-contract.md §2).  A frozen
        # five-item literal is what went stale when ARRANGE/TOGGLE_FIXTURE landed, so the
        # expectation is read from C's real ``VERBS`` — exact, plus the five legacy verbs must
        # still be present (a shortened table must not pass by comparing to itself).
        from world_phone_world import VERBS as OWNER_VERBS  # the module the API itself uses
        verbs = data["actions"]["local_owner_verbs"]
        self.assertEqual(verbs, list(OWNER_VERBS))
        for legacy in LEGACY_LOCAL_OWNER_VERBS:
            self.assertIn(legacy, verbs)
        nodes = {row["node"]: row for row in data["destinations"]["nodes"]}
        self.assertIn("home", nodes)
        self.assertEqual(nodes["home"]["walk_seconds"], 30)
        self.assertIs(nodes["home"]["direct_move_available"], True)
        self.assertEqual(walk_floats(data["body"]), [])
        self.assertEqual(walk_floats(data), [])
        self.assertEqual(first, second, "two reads of the same state disagreed")


@posix_only
class PhoneWorldGuardTest(PhoneWorldAPIBase):
    def test_host_origin_and_csrf_are_enforced_on_world_routes(self) -> None:
        code, doc = self.call("GET", "/api/phone/world", headers={"Host": "evil.example"})
        self.assertEqual((code, doc["reason_code"]), (403, "HOST_NOT_ALLOWED"))
        code, doc = self.call("GET", "/api/phone/world",
                              headers={"Origin": "http://evil.example"})
        self.assertEqual((code, doc["reason_code"]), (403, "ORIGIN_NOT_ALLOWED"))
        code, doc = self.call("GET", "/api/phone/world",
                              headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual((code, doc["reason_code"]), (403, "CROSS_SITE_REFUSED"))
        body = {"action": "POSE", "params": {"pose": "seated"}, "operation_key": "guard-no-csrf"}
        code, doc = self.call("POST", "/api/phone/world/actions", body)
        self.assertEqual((code, doc["reason_code"]), (403, "CSRF_TOKEN_MISSING"))
        code, doc = self.call("POST", "/api/phone/world/actions", body,
                              headers={"X-CSRF-Token": "0" * 64})
        self.assertEqual((code, doc["reason_code"]), (403, "CSRF_TOKEN_INVALID"))
        self.assertNotEqual(self.snapshot_body()["pose"], "lying",
                            "a refused POST changed the body")

    def test_params_are_an_exact_whitelist_and_client_identity_is_refused(self) -> None:
        stamp = int(time.time() * 1000)
        for field, value in (("uid", os.geteuid()), ("authority", "verified_main_self_tool"),
                             ("home", "/tmp/elsewhere"), ("elapsed_seconds", 0),
                             ("fatigue", 0.1), ("signals", [])):
            code, doc = self.act("POSE", {"pose": "seated", field: value}, "guard-id-%d" % stamp)
            self.assertEqual(code, 409, (field, doc))
            self.assertTrue(doc["reason_code"].startswith("CLIENT_IDENTITY_REFUSED"), doc)
        code, doc = self.act("POSE", {"pose": "seated", "orientation": "sideways"},
                             "guard-pose-%d" % stamp)
        self.assertEqual((code, doc["reason_code"]), (400, "ORIENTATION_INVALID"))
        code, doc = self.act("POSE", {"pose": "seated", "total": 1}, "guard-unknown-%d" % stamp)
        self.assertEqual(code, 400, doc)
        self.assertTrue(doc["reason_code"].startswith("UNKNOWN_FIELD"), doc)
        code, doc = self.act("POSE", {}, "guard-missing-%d" % stamp)
        self.assertEqual((code, doc["reason_code"]), (400, "PARAM_REQUIRED:pose"))
        code, doc = self.act("FLY", {}, "guard-action-%d" % stamp)
        self.assertEqual((code, doc["reason_code"]), (400, "UNKNOWN_WORLD_ACTION"))
        code, doc = self.act("REST", {"state": "stop", "mode": "rest"}, "guard-mode-%d" % stamp)
        self.assertEqual((code, doc["reason_code"]), (400, "MODE_REQUIRES_START"))
        code, doc = self.act("MOVE", {"destination_location_id": "x" * 97},
                             "guard-long-%d" % stamp)
        self.assertEqual((code, doc["reason_code"]), (400, "DESTINATION_INVALID"))
        code, doc = self.act("PLACE", {"object_id": "home_bed01",
                                       "target": {"kind": "room", "location_id": "home"}},
                             "guard-target-%d" % stamp)
        self.assertEqual((code, doc["reason_code"]), (400, "TARGET_INVALID"))
        code, doc = self.act("PICK_UP", {"object_id": "home_bed01", "slot_id": "third_hand"},
                             "guard-slot-%d" % stamp)
        self.assertEqual((code, doc["reason_code"]), (400, "SLOT_INVALID"))
        # A top-level field outside {action, params, operation_key} is refused too.
        code, doc = self.call("POST", "/api/phone/world/actions",
                              {"action": "POSE", "params": {"pose": "seated"},
                               "operation_key": "guard-top-%d" % stamp, "uid": os.geteuid()},
                              headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(code, 409, doc)
        self.assertTrue(doc["reason_code"].startswith("CLIENT_IDENTITY_REFUSED"), doc)


@posix_only
class PhoneWorldActTest(PhoneWorldAPIBase):
    def test_pose_applies_once_and_a_reused_key_cannot_change_its_target(self) -> None:
        key = "api-world-pose-%d" % int(time.time() * 1000)
        code, first = self.act("POSE", {"pose": "seated", "orientation": "south"}, key)
        self.assertEqual(code, 200, first)
        self.assertEqual(first["status"], "applied", first)
        self.assertEqual(first["data"]["action"], "POSE")
        self.assertEqual(first["data"]["operation_key"], key)
        self.assertEqual(first["data"]["source"], "resolver")
        body = self.snapshot_body()
        self.assertEqual(body["pose"], "seated")

        code, again = self.act("POSE", {"pose": "seated", "orientation": "south"}, key)
        self.assertEqual((code, again["status"]), (200, "already_applied"), again)
        self.assertEqual(again["data"]["replay_evidence"], "matched")

        # One key is one operation: a different target with the same key is a refusal, not a write.
        code, conflict = self.act("POSE", {"pose": "lying"}, key)
        self.assertEqual((code, conflict["status"]), (409, "rejected"), conflict)
        self.assertEqual(conflict["reason_code"], "OPERATION_TARGET_CONFLICT")
        self.assertEqual(self.snapshot_body()["pose"], "seated")

        # A key spent on another verb is a conflict too (one phone-wide namespace).
        code, crossed = self.act("REST", {"state": "stop"}, key)
        self.assertEqual((code, crossed["status"]), (409, "rejected"), crossed)
        self.assertEqual(crossed["reason_code"], "OPERATION_KEY_CONFLICT")
        self.assertEqual(crossed["data"]["operation"], "world")

        code, operation = self.call("GET", "/api/phone/world/operations/" + key)
        self.assertEqual((code, operation["status"]), (200, "applied"), operation)
        self.assertEqual(operation["data"]["execution_id"], first["data"]["execution_id"])
        self.assertEqual(operation["data"]["action_type"], "POSE")
        self.assertIs(operation["data"]["reconciliation_required"], False)

    def test_move_declares_a_real_thirty_second_walk_and_only_a_tick_arrives(self) -> None:
        key = "api-world-move-%d" % int(time.time() * 1000)
        code, doc = self.act("MOVE", {"destination_location_id": "home"}, key)
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["status"], "applied", doc)
        travel = doc["data"]["travel"]
        self.assertEqual(travel["status"], "in_progress", travel)
        self.assertEqual(int(travel["route_duration_seconds"]), 30)
        self.assertIs(travel["location_unchanged"], True)
        self.assertIsNotNone(travel["expected_arrival_at"])
        self.assertLessEqual(int(travel["remaining_seconds"]), 30)
        self.assertGreaterEqual(int(travel["remaining_seconds"]), 1)

        # The departure is not the arrival: two reads inside the walk keep the origin.
        for _ in range(2):
            data = self.world()["data"]
            self.assertEqual(data["location"]["node"], "home/bedroom")
            self.assertEqual(data["travel"]["status"], "in_progress")
            self.assertIs(data["travel"]["location_unchanged"], True)

        deadline = time.monotonic() + 90
        node = "home/bedroom"
        while time.monotonic() < deadline:
            node = self.world()["data"]["location"]["node"]
            if node != "home/bedroom":
                break
            time.sleep(2)
        self.assertEqual(node, "home", "the owner tick never realized the arrival")
        data = self.world()["data"]
        self.assertEqual(data["travel"]["status"], "none")
        self.assertEqual(data["location"]["place_id"], "home")

    def test_rest_is_a_command_and_a_low_fatigue_body_is_never_faked(self) -> None:
        code, doc = self.act("REST", {"state": "start"},
                             "api-world-rest-%d" % int(time.time() * 1000))
        if doc["status"] == "applied":
            # Accepted: the *next* snapshot is the fact, and it must really be active.
            recovery = self.snapshot_body()["recovery"]
            self.assertEqual(recovery["state"], "active", recovery)
        else:
            self.assertEqual(code, 409, doc)
            self.assertIn(doc["reason_code"],
                          {"RECOVERY_NOT_NEEDED", "REST_REQUIRES_RESTING_POSE", "TRAVELLING"},
                          doc)
            self.assertEqual(self.snapshot_body()["recovery"]["state"], "inactive")
        # Stopping when nothing is open is refused by the owner, not invented here.
        code, stop = self.act("REST", {"state": "stop"},
                              "api-world-rest-stop-%d" % int(time.time() * 1000))
        if self.snapshot_body()["recovery"]["state"] != "active":
            self.assertEqual((code, stop["status"]), (409, "rejected"), stop)
            self.assertIn(stop["reason_code"], {"NO_OPEN_RECOVERY", "RECOVERY_NOT_NEEDED"}, stop)

    def test_objects_are_refused_honestly_and_capabilities_come_from_declarations(self) -> None:
        code, doc = self.act("PICK_UP", {"object_id": "home_bed01", "slot_id": "left_hand"},
                             "api-world-pick-%d" % int(time.time() * 1000))
        self.assertEqual((code, doc["status"]), (409, "rejected"), doc)
        self.assertIn(doc["reason_code"], {"NOT_REACHABLE", "NOT_TAKEABLE"}, doc)
        self.assertEqual(self.snapshot_body()["pose"] != "lying", True)
        code, doc = self.act("PLACE", {"object_id": "home_bed01",
                                       "target": {"kind": "room", "location_id": "home",
                                                  "area_id": "living"}},
                             "api-world-place-%d" % int(time.time() * 1000))
        self.assertEqual((code, doc["status"]), (409, "rejected"), doc)
        self.assertIn(doc["reason_code"], {"nothing_held", "NOT_TAKEABLE", "NOT_ALLOWED"}, doc)
        # The takeable object is declared by the package, not by a name: the cup is portable.
        capabilities = json.loads(Path(str(self.instance["capabilities"])).read_text(
            encoding="utf-8"))
        cup = capabilities["objects"]["cafe_cup04"]
        self.assertIs(cup["takeable"], True)
        self.assertIs(cup["portable"], True)
        bed = capabilities["objects"]["home_bed01"]
        self.assertIs(bed["takeable"], False)
        self.assertIs(bed["fixed"], True)

    def test_unknown_operation_is_a_404_and_the_shopping_route_still_works(self) -> None:
        code, doc = self.call("GET", "/api/phone/world/operations/never-used-%d"
                              % int(time.time() * 1000))
        self.assertEqual(code, 404, doc)
        self.assertEqual(doc["status"], "not_found")
        self.assertEqual(doc["reason_code"], "OPERATION_NOT_FOUND")
        code, doc = self.call("GET", "/api/phone/operations/never-used-shopping")
        self.assertEqual(code, 404, doc)
        self.assertEqual(doc["reason_code"], "OPERATION_NOT_FOUND")
        code, _payload = self.call("GET", "/api/phone/world/unknown", raw=True)
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""``chiyo_phone`` 的世界动作：同一实例、同一入口授权、同一 HTTP 面。

不起真实模型、不联网：handler 是确定性函数，只按 profile 显式 ``phone`` 段访问本机手机服务。
证据一律取 owner 自己的事实（世界快照、``phone/operations.jsonl``），不把工具的回执当结论。

覆盖：
1. ``action=world`` 只读（不需要 ``write_enabled``），九块齐全、不带 csrf；
2. ``write_enabled`` 关闭时 ``world_action`` 拒绝（``PHONE_WRITE_DISABLED``），一个请求不发；
3. 真实 ``world_action``：POSE 经 C 的门面落到 body，日志 ``actor=model`` / ``world:pose``；
4. 参数白名单与身份拒绝（uid/home/authority/elapsed/fatigue 一律拒）；
5. ``world_operation`` 对账：已提交 key 有结果，未知 key ``not_found``；
6. 受限记忆 profile：世界快照仍可读，私人笔记仍被拒；
7. 被委派会话/未绑定入口一律拒绝（同 ``hermes_plugin.allowed``）。

夹具根在 ``/tmp/nyaw05r.jdK3q/rc1-world-*`` 保留，不删除、不清理。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import demo, service as phone_service  # noqa: E402

BASE = Path(os.environ.get("NYAIRO_V04_PHONE_ROOT") or "/tmp/nyaw05r.jdK3q")
posix_only = unittest.skipIf(os.name == "nt", "需要 POSIX AF_UNIX/fcntl（在 WSL/Linux 上运行）")

SNAPSHOT_BLOCKS = ("world", "location", "local_environment", "destinations", "travel",
                   "objects", "body", "actions", "recent_executions")


def unique_root(prefix: str) -> Path:
    BASE.mkdir(parents=True, exist_ok=True)
    root = BASE / (prefix + "%d-%d" % (os.getpid(), int(time.time() * 1000) % 1000000))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    return root


@posix_only
class PhoneWorldToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = unique_root("rc1-world-tool-")
        cls.prepared = demo.prepare_demo(cls.root, with_world=True)
        cls.profile = cls.root / "profile"
        (cls.profile / "chiyo").mkdir(parents=True, exist_ok=True)
        cls.stop = threading.Event()
        cls.ready: dict = {}
        cls.error: list = []

        def run() -> None:
            try:
                demo.serve_instance(cls.root, port=0, stop_event=cls.stop,
                                    on_ready=lambda info: cls.ready.update(info),
                                    announce=False)
            except Exception as exc:  # recorded, never hidden
                cls.error.append("%s: %s" % (type(exc).__name__, exc))

        cls.thread = threading.Thread(target=run, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if cls.ready.get("port") or cls.error:
                break
            time.sleep(0.05)
        if cls.error:
            raise AssertionError("serve failed: %s" % cls.error[0])
        if not cls.ready.get("port"):
            raise AssertionError("serve never became ready")
        cls.instance = phone_service.load_instance(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stop.set()
        cls.thread.join(timeout=120)

    # -- profile plumbing ------------------------------------------------------------

    def write_config(self, section=None, **profile) -> Path:
        payload = {"cli_owner": True, "local_owner_uid": os.getuid()}
        payload.update(profile)
        if section is not None:
            payload["phone"] = section
        path = self.profile / "chiyo" / "config.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def phone_section(self, *, enabled: bool = True, write: bool = True) -> dict:
        section = {
            "enabled": enabled,
            "base_url": "http://127.0.0.1:%d" % int(self.ready["port"]),
            "profile_home": str(self.root),
        }
        if write:
            section["write_enabled"] = True
        return section

    def call(self, action: str, **fields) -> dict:
        args = {"action": action}
        args.update(fields)
        return json.loads(phone_tool.phone_handler(args, hermes_home=str(self.profile)))

    def journal(self) -> list[dict]:
        path = self.root / "phone" / "operations.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    # -- reads -----------------------------------------------------------------------

    def test_world_read_needs_no_write_permit(self) -> None:
        self.write_config(self.phone_section(write=False))
        answer = self.call("world")
        self.assertTrue(answer["ok"], answer)
        result = answer["result"]
        self.assertEqual(result["kind"], "world_phone_world")
        self.assertEqual(set(result["data"]), set(SNAPSHOT_BLOCKS))
        self.assertEqual(result["data"]["location"]["node"], "home/bedroom")
        self.assertEqual(result["data"]["local_environment"]["status"], "available")
        self.assertNotIn("csrf_token", result)
        # The tool copies C's own body projection; it invents no somatic value.
        body = result["data"]["body"]
        self.assertEqual(body["status"], "available")
        self.assertIs(body["bands_only"], True)
        for key in ("fatigue", "activation", "comfort", "sensory_load", "tension", "warmth"):
            self.assertNotIn(key, body)

    def test_world_action_needs_the_write_permit_and_sends_nothing_when_disabled(self) -> None:
        self.write_config(self.phone_section(write=False))
        before = len(self.journal())
        answer = self.call("world_action", world_action="POSE", params={"pose": "lying"},
                           operation_key="tool-noperm-%d" % int(time.time() * 1000))
        self.assertFalse(answer["ok"], answer)
        self.assertEqual(answer["reason_code"], "PHONE_WRITE_DISABLED")
        self.assertEqual(len(self.journal()), before)

    def test_world_action_pose_reaches_the_owner_and_is_attributed_to_the_model(self) -> None:
        self.write_config(self.phone_section())
        key = "tool-pose-%d" % int(time.time() * 1000)
        answer = self.call("world_action", world_action="POSE",
                           params={"pose": "seated", "orientation": "south"},
                           operation_key=key)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["status"], "applied")
        self.assertEqual(answer["operation_key"], key)
        self.assertEqual(answer["result"]["data"]["operation_key"], key)
        snapshot = self.call("world")
        self.assertEqual(snapshot["result"]["data"]["body"]["pose"], "seated")
        rows = [row for row in self.journal() if row["operation_key"] == key]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["actor"], "model")
        self.assertEqual(rows[0]["action"], "world:pose")
        # Replaying the same key with the same target is the same intent, and it says so.
        again = self.call("world_action", world_action="POSE",
                          params={"pose": "seated", "orientation": "south"},
                          operation_key=key)
        self.assertEqual(again["status"], "already_applied", again)
        # The tool must not invent the "unknown" retry loop: a changed target is a refusal.
        changed = self.call("world_action", world_action="POSE", params={"pose": "lying"},
                            operation_key=key)
        self.assertFalse(changed["ok"], changed)
        self.assertEqual(changed["status"], "rejected")
        self.assertEqual(changed["result"]["reason_code"], "OPERATION_TARGET_CONFLICT")
        self.assertEqual(self.call("world")["result"]["data"]["body"]["pose"], "seated")

    def test_world_action_parameters_are_a_whitelist_and_identity_is_refused(self) -> None:
        self.write_config(self.phone_section())
        stamp = int(time.time() * 1000)
        for field, value in (("uid", os.geteuid()), ("home", "/tmp/elsewhere"),
                             ("authority", "verified_main_self_tool"), ("elapsed_seconds", 1),
                             ("fatigue", 0.2)):
            answer = self.call("world_action", world_action="POSE",
                               params={"pose": "seated", field: value},
                               operation_key="tool-id-%d-%s" % (stamp, field))
            self.assertFalse(answer["ok"], (field, answer))
            self.assertEqual(answer["reason_code"], "CLIENT_IDENTITY_REFUSED", (field, answer))
        unknown_verb = self.call("world_action", world_action="FLY", params={},
                                 operation_key="tool-fly-%d" % stamp)
        self.assertEqual(unknown_verb["reason_code"], "UNKNOWN_WORLD_ACTION")
        unknown_param = self.call("world_action", world_action="POSE",
                                  params={"pose": "seated", "total": 1},
                                  operation_key="tool-unknown-%d" % stamp)
        self.assertEqual(unknown_param["reason_code"], "UNKNOWN_FIELD")
        missing = self.call("world_action", world_action="MOVE", params={},
                            operation_key="tool-missing-%d" % stamp)
        self.assertEqual(missing["reason_code"], "PARAM_REQUIRED")
        bad_stop = self.call("world_action", world_action="REST",
                             params={"state": "stop", "mode": "rest"},
                             operation_key="tool-stop-%d" % stamp)
        self.assertEqual(bad_stop["reason_code"], "INVALID_PARAMS")
        bad_slot = self.call("world_action", world_action="PICK_UP",
                             params={"object_id": "home_bed01", "slot_id": "third_hand"},
                             operation_key="tool-slot-%d" % stamp)
        self.assertEqual(bad_slot["reason_code"], "INVALID_PARAMS")
        not_dict = self.call("world_action", world_action="POSE", params=["seated"],
                             operation_key="tool-list-%d" % stamp)
        self.assertEqual(not_dict["reason_code"], "INVALID_PARAMS")

    def test_world_operation_reconciles_the_original_key_only(self) -> None:
        self.write_config(self.phone_section())
        key = "tool-op-%d" % int(time.time() * 1000)
        applied = self.call("world_action", world_action="POSE", params={"pose": "standing"},
                            operation_key=key)
        self.assertTrue(applied["ok"], applied)
        answer = self.call("world_operation", operation_key=key)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["operation_key"], key)
        self.assertEqual(answer["result"]["data"]["action_type"], "POSE")
        self.assertEqual(answer["result"]["data"]["execution_id"],
                         applied["result"]["data"]["execution_id"])
        unknown = self.call("world_operation", operation_key="tool-never-%d"
                            % int(time.time() * 1000))
        self.assertFalse(unknown["ok"], unknown)
        self.assertEqual(unknown["status"], "not_found")
        self.assertEqual(unknown["http_status"], 404)
        no_key = self.call("world_operation")
        self.assertEqual(no_key["reason_code"], "OPERATION_KEY_REQUIRED")

    def test_restricted_memory_keeps_private_notes_shut_but_the_world_readable(self) -> None:
        self.write_config(self.phone_section(write=False), memory=True,
                          memory_tool_policy="restricted")
        world = self.call("world")
        self.assertTrue(world["ok"], world)
        self.assertEqual(world["result"]["data"]["location"]["place_id"], "home")
        notes = self.call("notes")
        self.assertFalse(notes["ok"], notes)
        self.assertEqual(notes["reason_code"], "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")
        # The world snapshot is not a private artifact read; a world *command* is still a write,
        # so the same explicit permit is required and refused here.
        blocked = self.call("world_action", world_action="POSE", params={"pose": "standing"},
                            operation_key="tool-mem-%d" % int(time.time() * 1000))
        self.assertFalse(blocked["ok"], blocked)
        self.assertEqual(blocked["reason_code"], "PHONE_WRITE_DISABLED")


    def test_delegated_and_unbound_entries_are_refused(self) -> None:
        self.write_config(self.phone_section())
        delegated = json.loads(phone_tool.phone_handler(
            {"action": "world_action", "world_action": "POSE", "params": {"pose": "seated"},
             "operation_key": "tool-delegated-%d" % int(time.time() * 1000)},
            hermes_home=str(self.profile), parent_session_id="session:parent"))
        self.assertEqual(delegated["reason_code"], "PHONE_DELEGATED_SOURCE_REFUSED")
        self.write_config(self.phone_section(), cli_owner=False, local_owner_uid=os.getuid())
        unbound = self.call("world")
        self.assertEqual(unbound["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")
        self.write_config(self.phone_section())
        other = json.loads(phone_tool.phone_handler(
            {"action": "world"}, hermes_home=str(self.profile), platform="local"))
        self.assertEqual(other["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""``chiyo_phone`` 工具的确定性正负：真落 Supply / 真经济 / 真 Resolver。

不起真实模型、不联网：handler 是确定性函数，只按 profile 的显式 ``phone`` 段访问本机手机
服务；断言直接读 owner 的落盘真相（经济文档、substrate consumption、手机操作日志），所以
"工具说成功"从来不是证据。

覆盖：
1. 默认关闭（无配置 / 无 ``enabled``）→ ``PHONE_DISABLED``，一个请求都不发；
2. 写默认关闭 → ``PHONE_WRITE_DISABLED``；
3. ``base_url`` 不是 loopback → ``PHONE_BASE_URL_NOT_LOOPBACK``；
4. ``profile_home`` 不是这部手机 → ``PHONE_PROFILE_MISMATCH``；
5. 真实读：钱包余额 = owner 文档余额、笔记、收藏；
6. 真实写：购买扣款一次并物化、使用经 Resolver 落一条 consumption 回执、日志里
   ``actor=model``；
7. 幂等：同 key 重放 ``already_applied`` 不二次扣款；``operation`` 对账；未知 key
   ``not_found``。

夹具根 ``/tmp/nyaw05r.jdK3q/v04-phone-p-tool-*`` 保留，不删除、不清理。
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
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import demo, service as phone_service  # noqa: E402

BASE = Path(os.environ.get("NYAIRO_V04_PHONE_ROOT") or "/tmp/nyaw05r.jdK3q")
posix_only = unittest.skipIf(os.name == "nt", "需要 POSIX AF_UNIX/fcntl（在 WSL/Linux 上运行）")


def unique_root(prefix: str) -> Path:
    BASE.mkdir(parents=True, exist_ok=True)
    root = BASE / (prefix + "%d-%d" % (os.getpid(), int(time.time() * 1000) % 1000000))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    return root


@posix_only
class PhoneToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = unique_root("v04-phone-p-tool-")
        cls.prepared = demo.prepare_demo(cls.root)
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
            except Exception as exc:
                cls.error.append("%s: %s" % (type(exc).__name__, exc))

        cls.thread = threading.Thread(target=run, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 90
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
        cls.thread.join(timeout=90)

    # -- profile plumbing ------------------------------------------------------------

    def write_config(self, section=None, **profile) -> Path:
        # A real CLI profile binds its entry explicitly; the tool must check the same rule as
        # hermes_plugin.allowed, so this fixture carries cli_owner/local_owner_uid by default.
        payload = {"cli_owner": True, "local_owner_uid": os.getuid()}
        payload.update(profile)
        if section is not None:
            payload["phone"] = section
        path = self.profile / "chiyo" / "config.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def phone_section(self, *, enabled: bool = True, write: bool = True,
                      profile_home: str = None, base_url: str = None,
                      private_artifacts: bool = False) -> dict:
        section = {
            "enabled": enabled,
            "base_url": base_url or ("http://127.0.0.1:%d" % int(self.ready["port"])),
            "profile_home": profile_home or str(self.root),
        }
        if write:
            section["write_enabled"] = True
        if private_artifacts:
            section["private_artifacts"] = True
        return section

    def call(self, action: str, **fields) -> dict:
        args = {"action": action}
        args.update(fields)
        return json.loads(phone_tool.phone_handler(args, hermes_home=str(self.profile)))

    # -- owner truths ----------------------------------------------------------------

    def economy(self) -> dict:
        return json.loads(
            Path(str(self.instance["economy_state"])).read_text(encoding="utf-8"))

    def balance(self) -> int:
        """The owner's own arithmetic: the genesis grant plus every real transaction."""

        document = self.economy()
        grant = document.get("starting_grant")
        total = int(grant.get("amount") if isinstance(grant, dict) else (grant or 0))
        for entry in document.get("transactions") or []:
            amount = int(entry.get("amount") or 0)
            direction = str(entry.get("direction") or "").upper()
            if direction == "DEBIT":
                total -= amount
            elif direction == "CREDIT":
                total += amount
        return total

    def substrate(self) -> dict:
        path = Path(str(self.instance["world_home"])) / "data" / "world" / "body_substrate.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def journal(self) -> list[dict]:
        path = self.root / "phone" / "operations.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    # -- negatives -------------------------------------------------------------------

    def test_feature_is_off_by_default(self) -> None:
        self.write_config(None)
        result = self.call("wallet")
        self.assertIs(result["ok"], False)
        self.assertEqual(result["reason_code"], "PHONE_DISABLED")
        self.write_config({"enabled": False, "base_url": "http://127.0.0.1:1"})
        self.assertEqual(self.call("wallet")["reason_code"], "PHONE_DISABLED")

    def test_writes_are_off_until_the_profile_says_otherwise(self) -> None:
        self.write_config(self.phone_section(write=False))
        result = self.call("purchase", catalog_item_id="virtual_snack_wafer",
                           operation_key="tool-write-off")
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["reason_code"], "PHONE_WRITE_DISABLED")
        read = self.call("wallet")
        self.assertIs(read["ok"], True, read)
        self.assertEqual(int(read["result"]["data"]["balance"]), self.balance())

    def test_base_url_must_be_loopback(self) -> None:
        self.write_config(self.phone_section(base_url="http://10.0.0.5:8080"))
        result = self.call("wallet")
        self.assertEqual(result["reason_code"], "PHONE_BASE_URL_NOT_LOOPBACK")

    def test_another_profile_phone_is_refused(self) -> None:
        self.write_config(self.phone_section(profile_home=str(self.root / "someone-else")))
        result = self.call("wallet")
        self.assertEqual(result["reason_code"], "PHONE_PROFILE_MISMATCH")
        self.assertEqual(result["ok"], False)

    def test_unknown_action_and_fields_are_refused(self) -> None:
        self.write_config(self.phone_section())
        self.assertEqual(self.call("teleport")["reason_code"], "UNKNOWN_ACTION")
        result = self.call("purchase", catalog_item_id="virtual_snack_wafer",
                           operation_key="tool-extra", price=1)
        self.assertEqual(result["reason_code"], "UNKNOWN_FIELD")

    # -- real reads ------------------------------------------------------------------

    def test_reads_are_the_owners_own_numbers(self) -> None:
        self.write_config(self.phone_section())
        wallet = self.call("wallet")
        self.assertIs(wallet["ok"], True, wallet)
        self.assertEqual(wallet["result"]["data"]["currency"], "JPY")
        self.assertEqual(int(wallet["result"]["data"]["balance"]), self.balance())
        shop = self.call("shop")
        catalog = shop["result"]["data"]["catalog"]
        self.assertEqual([row["catalog_item_id"] for row in catalog], ["virtual_snack_wafer"])
        notes = self.call("notes")
        self.assertIs(notes["ok"], True, notes)
        self.assertIn("items", notes["result"])
        references = self.call("references")
        self.assertIn("items", references["result"])

    # -- real writes -----------------------------------------------------------------

    def test_purchase_and_use_really_land_in_the_economy_and_the_substrate(self) -> None:
        self.write_config(self.phone_section())
        before = self.balance()
        key = "tool-buy-%d" % int(time.time() * 1000)
        bought = self.call("purchase", catalog_item_id="virtual_snack_wafer",
                           operation_key=key)
        self.assertIs(bought["ok"], True, bought)
        self.assertEqual(bought["status"], "applied")
        data = bought["result"]["data"]
        self.assertEqual(int(data["price"]), 240)
        self.assertEqual(self.balance(), before - 240)
        item_id = data["item_instance_id"]
        self.assertTrue([row for row in self.economy()["items"]
                         if row["item_instance_id"] == item_id
                         and row["disposition"] == "OWNED"])

        replay = self.call("purchase", catalog_item_id="virtual_snack_wafer",
                           operation_key=key)
        self.assertEqual(replay["status"], "already_applied", replay)
        self.assertEqual(self.balance(), before - 240)

        inventory = self.call("inventory")
        row = next(item for item in inventory["result"]["data"]["items"]
                   if item["item_instance_id"] == item_id)
        self.assertIs(row["usable"], True)

        use_key = "tool-use-%d" % int(time.time() * 1000)
        used = self.call("use", item_instance_id=item_id, operation_key=use_key)
        self.assertIs(used["ok"], True, used)
        self.assertEqual(used["status"], "applied")
        payload = used["result"]["data"]
        self.assertEqual(payload["local_owner"]["source"], "local_owner_phone")
        execution_id = payload["execution_id"]
        self.assertEqual(payload["receipt"]["execution_id"], execution_id)
        self.assertIn(execution_id, self.substrate().get("consumptions") or {})

        again = self.call("use", item_instance_id=item_id, operation_key=use_key)
        self.assertEqual(again["status"], "already_applied", again)
        other = self.call("use", item_instance_id=item_id, operation_key=use_key + "-2")
        self.assertEqual(other["status"], "rejected")
        self.assertEqual(other["result"]["reason_code"], "ALREADY_CONSUMED")

        reconciled = self.call("operation", operation_key=use_key)
        self.assertIs(reconciled["ok"], True, reconciled)
        self.assertEqual(reconciled["result"]["data"]["execution_id"], execution_id)
        missing = self.call("operation", operation_key="never-anywhere")
        self.assertEqual(missing["result"]["status"], "not_found")

        rows = [row for row in self.journal() if row["operation_key"] in (key, use_key)]
        self.assertTrue(rows, "the phone must record who asked for this operation")
        self.assertTrue(all(row["actor"] == "model" for row in rows), rows)

    def test_note_save_writes_the_real_private_items_artifact(self) -> None:
        self.write_config(self.phone_section())
        key = "tool-note-%d" % int(time.time() * 1000)
        saved = self.call("note_save", title="手机上记的一笔", body="由工具写入", operation_key=key)
        self.assertIs(saved["ok"], True, saved)
        self.assertEqual(saved["status"], "applied")
        listed = self.call("notes")
        row = next(item for item in listed["result"]["items"]
                   if item["id"] == saved["result"]["data"]["id"])
        self.assertEqual(row["title"], "手机上记的一笔")
        self.assertEqual(row["body"], "由工具写入")


    def test_entry_authorization_follows_the_original_allowed_rule(self) -> None:
        self.write_config(self.phone_section(), cli_owner=False)
        self.assertEqual(self.call("wallet")["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")
        self.write_config(self.phone_section(), local_owner_uid=os.getuid() + 1)
        self.assertEqual(self.call("wallet")["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")

        bindings = {"telegram": ["telegram:bot:dm:42"]}
        self.write_config(self.phone_section(), gateway_bindings=bindings)
        args = {"action": "wallet"}

        def call_as(**kwargs) -> dict:
            return json.loads(phone_tool.phone_handler(args, hermes_home=str(self.profile),
                                                       **kwargs))

        bound_dm = call_as(platform="telegram", conversation_key="telegram:bot:dm:42")
        self.assertIs(bound_dm["ok"], True, bound_dm)
        unbound_dm = call_as(platform="telegram", conversation_key="telegram:bot:dm:99")
        self.assertEqual(unbound_dm["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")
        group = call_as(platform="telegram", conversation_key="telegram:bot:group:42")
        self.assertEqual(group["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")
        local = call_as(platform="local", conversation_key=None)
        self.assertEqual(local["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")
        delegated = call_as(parent_session_id="parent-session-1")
        self.assertEqual(delegated["reason_code"], "PHONE_DELEGATED_SOURCE_REFUSED")

    def test_the_entry_gate_is_the_plugins_own_rule(self) -> None:
        import ast

        source = (ROOT / "chiyo_bundle" / "hermes_plugin.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(item for item in tree.body
                    if isinstance(item, ast.FunctionDef) and item.name == "allowed")
        namespace = {"os": os}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "allowed", "exec"), namespace)
        shipped = namespace["allowed"]
        configs = [
            {"cli_owner": True, "local_owner_uid": os.getuid(),
             "gateway_bindings": {"telegram": ["telegram:bot:dm:42"]}},
            {"cli_owner": False, "local_owner_uid": os.getuid()},
            {"cli_owner": True, "local_owner_uid": os.getuid() + 1},
        ]
        cases = [("cli", None), ("local", None), ("telegram", "telegram:bot:dm:42"),
                 ("telegram", "telegram:bot:dm:99"), ("telegram", "telegram:bot:group:42"),
                 ("telegram", None), ("discord", "discord:bot:dm:1")]
        for config in configs:
            for platform, key in cases:
                self.assertEqual(phone_tool.entry_allowed(config, platform, key),
                                 bool(shipped(config, platform, key)),
                                 (config, platform, key))

    def test_restricted_memory_keeps_private_artifacts_behind_a_second_permit(self) -> None:
        self.write_config(self.phone_section(), memory=True)
        for action in ("notes", "references", "reading"):
            result = self.call(action)
            self.assertEqual(result["reason_code"], "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED", action)
        save = self.call("note_save", title="记忆受限", body="不该落地",
                         operation_key="mem-restricted-1")
        self.assertEqual(save["reason_code"], "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")
        # The economy views stay open: the phone is not disabled, only its private surface.
        wallet = self.call("wallet")
        self.assertIs(wallet["ok"], True, wallet)
        shop = self.call("shop")
        self.assertIs(shop["ok"], True, shop)
        # An explicit permit opens exactly that surface; nothing else changes.
        self.write_config(self.phone_section(private_artifacts=True), memory=True,
                          memory_tool_policy="restricted")
        notes = self.call("notes")
        self.assertIs(notes["ok"], True, notes)
        for field in ("记忆受限",):
            self.assertFalse([row for row in notes["result"]["items"] if row["title"] == field])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""v0.4 手机 HTTP API 的精确正负（真实 Supply + 真实 World + 真实经济）。

本文件只跑新 API 的安全闸门与真实接线，不做整仓回归：

* 一次性真实实例（``components.phone.demo.prepare_demo``：真 workspace/artifact/私人事项文档、
  真 worldgen/world home、真 10000 JPY genesis、真 ``consumable_catalog``）；
* 每个用例用 ``serve_instance`` 起**真实的两个 owner 子进程**（Supply 与 WorldBodyService）并
  绑 loopback，全部经由 HTTP 面验证；
* 负控覆盖：无 CSRF、坏 Host、静态白名单/穿越、客户端身份字段、CAS 冲突、未知目录、
  重复 operation_key 不二次扣款/不二次消耗、未知操作对账、Supply 缺席时的 unavailable。

夹具根在 ``/tmp/nyaw05r.jdK3q/v04-phone-p-*`` 保留，不删除、不清理；临时 job 目录用
``tempfile.mkdtemp``（同样保留）。
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
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import demo, server as phone_server, service as phone_service  # noqa: E402

BASE = Path(os.environ.get("NYAIRO_V04_PHONE_ROOT") or "/tmp/nyaw05r.jdK3q")
posix_only = unittest.skipIf(os.name == "nt", "需要 POSIX AF_UNIX/fcntl（在 WSL/Linux 上运行）")


def unique_root(prefix: str) -> Path:
    BASE.mkdir(parents=True, exist_ok=True)
    root = BASE / (prefix + "%d-%d" % (os.getpid(), int(time.time() * 1000) % 1000000))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    return root


class PhoneAPIBase(unittest.TestCase):
    """One real instance per class; one real serve session per test."""

    prepared: dict = {}
    root: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = unique_root("v04-phone-p-api-")
        cls.prepared = demo.prepare_demo(cls.root)

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
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.ready.get("port") or self.error:
                break
            time.sleep(0.05)
        if self.error:
            self.fail("serve failed: %s" % self.error[0])
        self.assertTrue(self.ready.get("port"), "serve never became ready")
        self.base = "http://127.0.0.1:%d" % int(self.ready["port"])
        self.instance = phone_service.load_instance(self.root)
        self.csrf = self._csrf()

    def tearDown(self) -> None:
        self.stop.set()
        self.thread.join(timeout=90)
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
            with urllib.request.urlopen(request, timeout=30) as response:
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

    def mutate(self, path: str, body: dict, *, key: str = None) -> tuple[int, dict]:
        headers = {"X-CSRF-Token": self.csrf}
        if key:
            headers["X-Operation-Key"] = key
        return self.call("POST", path, body, headers=headers)

    # -- owner-side reads ------------------------------------------------------------

    def economy(self) -> dict:
        path = Path(str(self.instance["economy_state"]))
        return json.loads(path.read_text(encoding="utf-8"))

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


@posix_only
class PhoneAPIStateTest(PhoneAPIBase):
    def test_state_reports_every_app_with_explicit_status(self) -> None:
        code, doc = self.call("GET", "/api/phone/state")
        self.assertEqual(code, 200)
        self.assertTrue(doc["ok"])
        self.assertIs(doc["read_only"], False)
        self.assertEqual(set(doc["apps"]), set(phone_service.APPS))
        for name, entry in doc["apps"].items():
            self.assertIn(entry["status"], {"available", "unavailable", "disabled"}, name)
        self.assertEqual(doc["apps"]["wallet"]["status"], "available")
        self.assertEqual(doc["apps"]["wallet"]["data"]["currency"], "JPY")
        self.assertEqual(doc["apps"]["shop"]["data"]["catalog_count"], 1)
        self.assertEqual(doc["instance"]["subject"], "chiyo")
        self.assertEqual(doc["instance"]["mode"], "local_owner")
        self.assertEqual(doc["instance"]["fingerprint"],
                         phone_service.profile_fingerprint(str(self.root)))
        # The state view is a summary: it never carries the whole catalog or a note body.
        self.assertNotIn("catalog", doc["apps"]["shop"]["data"])
        self.assertNotIn("transactions", doc["apps"]["wallet"]["data"])

    def test_wallet_shop_and_inventory_come_from_the_real_owners(self) -> None:
        code, wallet = self.call("GET", "/api/phone/wallet")
        self.assertEqual(code, 200, wallet)
        self.assertEqual(wallet["data"]["currency"], "JPY")
        self.assertEqual(int(wallet["data"]["balance"]), self.balance())
        self.assertEqual(wallet["data"]["starting_grant"], 10000)
        code, shop = self.call("GET", "/api/phone/shop")
        self.assertEqual(code, 200, shop)
        self.assertEqual(shop["data"]["store_id"], "phone_virtual_store")
        self.assertTrue(shop["data"]["purchase_available"])
        catalog = shop["data"]["catalog"]
        self.assertEqual([row["catalog_item_id"] for row in catalog],
                         ["virtual_snack_wafer"])
        self.assertEqual(int(catalog[0]["price"]), 240)
        self.assertEqual(catalog[0]["usable_kind"], "CONSUME")
        code, inventory = self.call("GET", "/api/phone/inventory")
        self.assertEqual(code, 200, inventory)
        self.assertIn("items", inventory["data"])


@posix_only
class PhoneAPIGuardTest(PhoneAPIBase):
    def test_static_surface_is_an_exact_whitelist(self) -> None:
        for path, needle in (("/", b"<"), ("/index.html", b"<"), ("/app.js", b"function"),
                             ("/style.css", b"{")):
            code, payload = self.call("GET", path, raw=True)
            self.assertEqual(code, 200, path)
            self.assertIn(needle, payload, path)
        for path in ("/AGENTS.md", "/instance.json", "/../AGENTS.md",
                     "/%2e%2e/instance.json", "/one-day/instance.json",
                     "/api/phone/../state", "/api/phone/unknown"):
            code, _payload = self.call("GET", path, raw=True)
            self.assertEqual(code, 404, path)

    def test_host_origin_and_csrf_are_enforced(self) -> None:
        request = urllib.request.Request(self.base + "/api/phone/state")
        request.add_header("Host", "evil.example")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=15)
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(json.loads(caught.exception.read())["reason_code"],
                         "HOST_NOT_ALLOWED")

        code, doc = self.call("GET", "/api/phone/state", headers={"Origin": "http://evil.example"})
        self.assertEqual((code, doc["reason_code"]), (403, "ORIGIN_NOT_ALLOWED"))
        code, doc = self.call("GET", "/api/phone/state",
                              headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual((code, doc["reason_code"]), (403, "CROSS_SITE_REFUSED"))

        body = {"title": "无令牌", "body": "不该落地", "operation_key": "guard-no-csrf"}
        code, doc = self.call("POST", "/api/phone/notes", body)
        self.assertEqual((code, doc["reason_code"]), (403, "CSRF_TOKEN_MISSING"))
        code, doc = self.call("POST", "/api/phone/notes", body,
                              headers={"X-CSRF-Token": "0" * 64})
        self.assertEqual((code, doc["reason_code"]), (403, "CSRF_TOKEN_INVALID"))
        # Nothing landed: the token is the only way in, and the note is nowhere.
        code, notes = self.call("GET", "/api/phone/notes")
        self.assertFalse([row for row in notes["items"] if row["title"] == "无令牌"])

    def test_client_identity_fields_are_refused_not_trusted(self) -> None:
        for field, value in (("uid", os.geteuid()), ("main_self_verified", True),
                             ("price", 0), ("balance", 999999), ("authority", "verified_main_self_tool"),
                             ("home", "/tmp/elsewhere")):
            body = {"title": "身份注入", "body": "不该落地", "operation_key": "guard-identity",
                    field: value}
            code, doc = self.mutate("/api/phone/notes", body)
            self.assertEqual(code, 409, (field, doc))
            self.assertTrue(doc["reason_code"].startswith("CLIENT_IDENTITY_REFUSED"), doc)
        code, doc = self.mutate("/api/phone/notes",
                                {"title": "未知字段", "body": "x", "operation_key": "guard-unknown",
                                 "total": 1})
        self.assertEqual(code, 400, doc)
        self.assertTrue(doc["reason_code"].startswith("UNKNOWN_FIELD"), doc)


@posix_only
class PhoneAPINotesTest(PhoneAPIBase):
    def test_note_is_written_to_the_real_artifact_and_replays_without_a_second_version(self):
        key = "api-note-%d" % int(time.time() * 1000)
        code, first = self.mutate("/api/phone/notes",
                                  {"title": "手机上的备忘", "body": "第一行\n第二行",
                                   "operation_key": key})
        self.assertEqual(code, 200, first)
        self.assertEqual(first["status"], "applied")
        note_id = first["data"]["id"]
        head = first["data"]["head"]

        code, listed = self.call("GET", "/api/phone/notes")
        self.assertEqual(code, 200)
        self.assertEqual(listed["head"], head)
        row = next(item for item in listed["items"] if item["id"] == note_id)
        self.assertEqual(row["title"], "手机上的备忘")
        self.assertEqual(row["body"], "第一行\n第二行")
        self.assertIs(listed["private_text_included"], True)

        code, again = self.mutate("/api/phone/notes",
                                  {"title": "手机上的备忘", "body": "第一行\n第二行",
                                   "operation_key": key})
        self.assertEqual((code, again["status"]), (200, "already_applied"), again)
        code, unchanged = self.call("GET", "/api/phone/notes")
        self.assertEqual(unchanged["head"], head)

        # Same title, new body, new key -> a real revision of the same memo.
        code, revised = self.mutate("/api/phone/notes",
                                    {"title": "手机上的备忘", "body": "改过的正文",
                                     "operation_key": key + "-rev", "note_id": note_id})
        self.assertEqual((code, revised["status"]), (200, "applied"), revised)
        code, listed = self.call("GET", "/api/phone/notes")
        row = next(item for item in listed["items"] if item["id"] == note_id)
        self.assertEqual(row["body"], "改过的正文")
        self.assertGreaterEqual(int(row["revision"]), 2)

        # A stale expected_head is a definite refusal and writes nothing.
        code, doc = self.mutate("/api/phone/notes",
                                {"title": "手机上的备忘", "body": "不该落地",
                                 "operation_key": key + "-stale",
                                 "expected_head": "artifact-version:stale"})
        self.assertEqual(code, 409, doc)
        self.assertEqual(doc["reason_code"], "NOTE_HEAD_CONFLICT")

    def test_reference_is_a_pointer_and_reading_never_claims_understanding(self) -> None:
        code, saved = self.mutate("/api/phone/references",
                                  {"source_id": "supply:artifact/reading",
                                   "source_revision": "1", "label": "那本簿子",
                                   "operation_key": "api-ref-%d" % int(time.time() * 1000)})
        self.assertEqual(code, 200, saved)
        code, listed = self.call("GET", "/api/phone/references")
        self.assertEqual(code, 200)
        row = next(item for item in listed["items"] if item["id"] == saved["data"]["id"])
        self.assertEqual(row["label"], "那本簿子")
        self.assertIs(row["read"], False)
        self.assertIs(row["content_fetched"], False)
        code, reading = self.call("GET", "/api/phone/reading")
        self.assertEqual(code, 200)
        self.assertEqual(reading["understanding"], "UNKNOWN")
        for item in reading["items"]:
            self.assertEqual(item["understanding"], "UNKNOWN")
            self.assertNotIn("body", item)

    def test_supply_absence_is_unavailable_never_a_fake_note_list(self) -> None:
        instance = dict(self.instance)
        instance["supply_socket"] = str(self.root / "run" / "not-there.sock")
        detached = phone_service.PhoneService(instance)
        notes = detached.notes()
        self.assertIs(notes["ok"], False)
        self.assertEqual(notes["status"], "unavailable")
        self.assertEqual(notes["reason_code"], "SUPPLY_UNAVAILABLE")
        self.assertNotIn("items", notes)
        self.assertEqual(phone_server.http_status_for("unavailable"), 503)
        self.assertEqual(phone_server.http_status_for("rejected"), 409)
        self.assertEqual(phone_server.http_status_for("unknown"), 202)
        self.assertEqual(phone_server.http_status_for("not_found"), 404)


    def test_two_same_title_creates_are_two_independent_notes(self) -> None:
        stamp = int(time.time() * 1000)
        title = "同名备忘-%d" % stamp
        first = self.mutate("/api/phone/notes",
                            {"title": title, "body": "第一份",
                             "operation_key": "api-dup-a-%d" % stamp})
        second = self.mutate("/api/phone/notes",
                             {"title": title, "body": "第二份",
                              "operation_key": "api-dup-b-%d" % stamp})
        self.assertEqual((first[0], first[1]["status"]), (200, "applied"), first)
        self.assertEqual((second[0], second[1]["status"]), (200, "applied"), second)
        self.assertNotEqual(first[1]["data"]["id"], second[1]["data"]["id"])
        code, listed = self.call("GET", "/api/phone/notes")
        rows = {row["id"]: row for row in listed["items"]}
        self.assertEqual(rows[first[1]["data"]["id"]]["body"], "第一份")
        self.assertEqual(rows[second[1]["data"]["id"]]["body"], "第二份")

    def test_revision_needs_an_id_never_renames_and_touches_only_that_note(self) -> None:
        stamp = int(time.time() * 1000)
        title_a = "甲-%d" % stamp
        first = self.mutate("/api/phone/notes",
                            {"title": title_a, "body": "甲正文",
                             "operation_key": "api-one-a-%d" % stamp})
        second = self.mutate("/api/phone/notes",
                             {"title": "乙-%d" % stamp, "body": "乙正文",
                              "operation_key": "api-one-b-%d" % stamp})
        note_a, note_b = first[1]["data"]["id"], second[1]["data"]["id"]

        # A create without a note_id never revises by matching title (the fallback is gone).
        again = self.mutate("/api/phone/notes",
                            {"title": title_a, "body": "第三份",
                             "operation_key": "api-one-c-%d" % stamp})
        self.assertEqual(again[0], 200, again)
        self.assertNotIn(again[1]["data"]["id"], (note_a, note_b))

        # The owner's revise contract only rewrites the body: refuse a rename instead of lying.
        rename = self.mutate("/api/phone/notes",
                             {"title": "改名了", "body": "甲正文", "note_id": note_a,
                              "operation_key": "api-rename-%d" % stamp})
        self.assertEqual((rename[0], rename[1]["reason_code"]),
                         (409, "NOTE_TITLE_CHANGE_NOT_SUPPORTED"), rename)

        # An explicit id revises exactly one note and leaves its sibling alone.
        revised = self.mutate("/api/phone/notes",
                              {"title": title_a, "body": "甲改过", "note_id": note_a,
                               "operation_key": "api-revise-a-%d" % stamp})
        self.assertEqual((revised[0], revised[1]["status"]), (200, "applied"), revised)
        code, listed = self.call("GET", "/api/phone/notes")
        rows = {row["id"]: row for row in listed["items"]}
        self.assertEqual(rows[note_a]["body"], "甲改过")
        self.assertEqual(rows[note_b]["body"], "乙正文")

        missing = self.mutate("/api/phone/notes",
                              {"title": "x", "body": "y",
                               "note_id": "memo:missing-%d" % stamp,
                               "operation_key": "api-missing-%d" % stamp})
        self.assertEqual((missing[0], missing[1]["reason_code"]),
                         (409, "NOTE_NOT_FOUND"), missing)

        # The original CAS precondition still refuses a stale head, and nothing is written.
        stale = self.mutate("/api/phone/notes",
                            {"title": title_a, "body": "不该落地",
                             "expected_head": "artifact-version:stale",
                             "operation_key": "api-stale-%d" % stamp})
        self.assertEqual((stale[0], stale[1]["reason_code"]),
                         (409, "NOTE_HEAD_CONFLICT"), stale)
        code, listed = self.call("GET", "/api/phone/notes")
        rows = {row["id"]: row for row in listed["items"]}
        self.assertEqual(rows[note_a]["body"], "甲改过")


@posix_only
class PhoneAPIPurchaseTest(PhoneAPIBase):
    def _buy(self, key: str) -> dict:
        code, doc = self.mutate("/api/phone/purchases",
                                {"catalog_item_id": "virtual_snack_wafer", "operation_key": key})
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["status"], "applied", doc)
        return doc

    def test_purchase_charges_once_materializes_and_replays_without_a_second_charge(self):
        before = self.balance()
        key = "api-buy-%d" % int(time.time() * 1000)
        bought = self._buy(key)
        data = bought["data"]
        self.assertEqual(int(data["price"]), 240)
        self.assertEqual(int(data["current_balance"]), before - 240)
        self.assertEqual(data["ownership"], "OWNED")
        self.assertEqual(data["delivery_location"], "home/bedroom")
        self.assertIn(data["materialization"], {"materialized", "already_materialized"})
        item_id = data["item_instance_id"]
        self.assertEqual(self.balance(), before - 240)

        code, replay = self.mutate("/api/phone/purchases",
                                   {"catalog_item_id": "virtual_snack_wafer",
                                    "operation_key": key})
        self.assertEqual((code, replay["status"]), (200, "already_applied"), replay)
        self.assertEqual(replay["data"]["item_instance_id"], item_id)
        self.assertEqual(self.balance(), before - 240)

        code, inventory = self.call("GET", "/api/phone/inventory")
        self.assertEqual(code, 200)
        row = next(item for item in inventory["data"]["items"]
                   if item["item_instance_id"] == item_id)
        self.assertEqual(row["ownership"], "OWNED")
        self.assertIs(row["usable"], True)
        self.assertEqual(row["location"], "room:home/bedroom")
        self.assertEqual(row["materialization"], "materialized")

        code, operation = self.call("GET", "/api/phone/operations/" + key)
        self.assertEqual((code, operation["status"]), (200, "applied"), operation)
        self.assertEqual(operation["data"]["transaction_id"], data["transaction_id"])

    def test_unknown_item_and_unknown_operation_are_typed(self) -> None:
        code, doc = self.mutate("/api/phone/purchases",
                                {"catalog_item_id": "basic_bed",
                                 "operation_key": "api-bad-%d" % int(time.time() * 1000)})
        self.assertEqual(code, 409, doc)
        self.assertIn(doc["reason_code"], {"UNKNOWN_CATALOG_ITEM",
                                           "ITEM_NOT_SOLD_IN_PHONE_STORE"})
        code, doc = self.call("GET", "/api/phone/operations/never-used-key")
        self.assertEqual(code, 404, doc)
        self.assertEqual(doc["status"], "not_found")
        self.assertEqual(doc["reason_code"], "OPERATION_NOT_FOUND")

    def test_use_goes_through_the_resolver_and_consumes_exactly_once(self):
        bought = self._buy("api-use-buy-%d" % int(time.time() * 1000))
        item_id = bought["data"]["item_instance_id"]
        use_key = "api-use-%d" % int(time.time() * 1000)
        code, used = self.mutate("/api/phone/items/use",
                                 {"item_instance_id": item_id, "operation_key": use_key})
        self.assertEqual(code, 200, used)
        self.assertEqual(used["status"], "applied", used)
        data = used["data"]
        self.assertEqual(data["object_id"], item_id)
        self.assertIs(data["applied_to_body"], True)
        self.assertEqual(data["local_owner"]["source"], "local_owner_phone")
        self.assertEqual(data["local_owner"]["binding"]["subject"], "chiyo")
        self.assertTrue(str(data["receipt"]["receipt_id"]).startswith("world-consumption:"))

        code, replay = self.mutate("/api/phone/items/use",
                                   {"item_instance_id": item_id, "operation_key": use_key})
        self.assertEqual((code, replay["status"]), (200, "already_applied"), replay)
        self.assertEqual(replay["data"]["receipt"]["receipt_id"],
                         data["receipt"]["receipt_id"])

        code, second = self.mutate("/api/phone/items/use",
                                   {"item_instance_id": item_id,
                                    "operation_key": use_key + "-again"})
        self.assertEqual(code, 409, second)
        self.assertEqual(second["reason_code"], "ALREADY_CONSUMED")

        code, inventory = self.call("GET", "/api/phone/inventory")
        row = next(item for item in inventory["data"]["items"]
                   if item["item_instance_id"] == item_id)
        self.assertIs(row["consumed"], True)
        self.assertIs(row["usable"], False)
        self.assertEqual(row["blocked_reason"], "ALREADY_CONSUMED")
        # The operator/model origin of every mutation is recorded, never inferred.
        journal = (self.root / "phone" / "operations.jsonl").read_text(encoding="utf-8")
        rows = [json.loads(line) for line in journal.splitlines() if line.strip()]
        self.assertTrue([row for row in rows if row["action"] == "use"
                         and row["operation_key"] == use_key])
        self.assertTrue(all(row["actor"] in ("user", "model") for row in rows))


if __name__ == "__main__":
    unittest.main()

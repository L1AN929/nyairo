#!/usr/bin/env python3
"""``GET /api/phone/today/timeline``：她今天有记录的事，合成一条只读时间线。

这里不起 Supply/World、不调用模型：手机根、嵌套 Life、业务时钟、Activity 权威、日计划、
World execution ledger 与四个 facade 的假回复都由本用例自己造，然后用真实的
``PhoneHTTPServer`` 走一遍 HTTP 面。覆盖：

* 活动、日计划（只表示排定）、World 动作（同一 execution 的分阶段记录不双计）、日记、
  相册、钱包交易都按各自的记录时间进入同一天；
* 业务 clock 的记录与真实 clock 的记录不硬排成全序，``ordering.comparable`` 为 false，
  域内仍按时间排；
* operator 默认只有骨架：没有日记正文、没有相册 caption、没有图片字节；
  主体声明（``X-Phone-Actor: model``）拿到 ``detail_ref``，但聚合仍不复制正文；
* body 只作为读取时的当前快照，不插进过去；
* 未知 query 被拒绝；调用前后所有被读文件逐字节不变（零写）。
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "chiyo_bundle")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

DAY = "2026-10-20"


class _FakeService(phone_service.PhoneService):
    """Overrides only the four facades the timeline merges; keeps the real day/state logic."""

    def diary_list(self):
        return {
            "ok": True, "status": "applied", "count": 2, "private_text_included": False,
            "entries": [
                {"id": "diary:today", "created_at": "2026-10-20T03:00:00+00:00",
                 "chars": 42, "revisions": 1, "has_text": True},
                {"id": "diary:yesterday", "created_at": "2026-10-19T03:00:00+00:00",
                 "chars": 9, "revisions": 1, "has_text": True},
            ],
        }

    def album_list(self):
        return {
            "ok": True, "status": "applied", "count": 2, "private_media_included": False,
            "images": [
                {"artifact_id": "artifact:pic1", "caption": "SECRET-CAPTION",
                 "content_type": "image/png", "lifecycle": "ACTIVE",
                 "created_at_iso": "2026-10-20T04:00:00+00:00",
                 "source_label": "user_import", "generated_locally": False},
                {"artifact_id": "artifact:pic-old", "caption": "OLD-CAPTION",
                 "content_type": "image/jpeg", "lifecycle": "ACTIVE",
                 "created_at_iso": "2026-10-19T04:00:00+00:00",
                 "source_label": "user_import", "generated_locally": False},
            ],
        }

    def wallet(self):
        return {
            "ok": True, "status": "applied",
            "data": {
                "currency": "JPY", "balance": 880,
                "transactions": [
                    {"transaction_id": "tx:today", "kind": "purchase", "direction": "out",
                     "amount": 120, "occurred_at": "2026-10-20T05:00:00+00:00",
                     "catalog_item_id": "virtual_snack_wafer"},
                    {"transaction_id": "tx:old", "kind": "purchase", "direction": "out",
                     "amount": 50, "occurred_at": "2026-10-19T05:00:00+00:00",
                     "catalog_item_id": "virtual_snack_wafer"},
                ],
            },
        }

    def world(self):
        return {
            "ok": True, "status": "applied",
            "data": {
                "location": {"place_id": "home", "name": "家", "status": "available"},
                "destinations": {"status": "available", "places": [
                    {"location_id": "reading_point", "name": "阅读角"},
                    {"location_id": "home", "name": "家"},
                ]},
                "travel": {"status": "in_progress", "execution_id": "exec:today",
                           "origin_location_id": "home",
                           "destination_location_id": "reading_point",
                           "started_at": "2026-10-20T02:00:00+00:00",
                           "expected_arrival_at": "2026-10-20T02:05:00+00:00"},
                "body": {"status": "available", "pose": "seated", "orientation": "north",
                         "recovery": "resting", "bands_only": True,
                         "raw_values_exposed": False,
                         "signals": [{"signal_type": "warmth",
                                      "qualitative_band": "mild"}]},
            },
        }


class DayTimelineAPITest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="phone-timeline-"))
        self.addCleanup(self._cleanup)
        nested = self.root / "one-day"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "instance.json").write_text(
            json.dumps({"schema_version": "life.one_day.v1"}), encoding="utf-8")
        self.state_root = nested / "life" / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.world_home = self.root / "world"
        self.server = None

    def _cleanup(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    # -- fixtures -------------------------------------------------------------------------
    def _write_clock(self) -> Path:
        path = self.state_root / "fastday" / "fastday_clock.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "kind": "explicit_compressed_demo_clock", "schema_version": 1,
            "mode": "COMPRESSED_DEMO", "rate": 12.0, "state": "STOPPED",
            "domain_id": "fastday:run:phone-timeline", "run_id": "run:phone-timeline",
            "logical_anchor": "2026-10-20T00:00:00+08:00",
            "logical_checkpoint": "2026-10-20T12:00:00+08:00",
            "horizon_logical": "2026-10-21T00:00:00+08:00",
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def _write_activity(self) -> Path:
        path = self.state_root / "canonical_life_state.json"
        path.write_text(json.dumps({
            "schema_version": "life.activity.canonical.v1", "subject_id": "chiyo",
            "revision": 2, "foreground_activity_ref": "actv_today",
            "activities": {
                "actv_today": {
                    "activity_id": "actv_today", "activity_kind": "READING",
                    "title": "读《桃花源记》第一节", "status": "PAUSED",
                    "started_at": "2026-10-20T09:00:00+08:00", "ended_at": None,
                    "terminal_reason_code": None},
                "actv_old": {
                    "activity_id": "actv_old", "activity_kind": "READING",
                    "title": "昨夜翻了两页", "status": "ABANDONED",
                    "started_at": "2026-10-19T23:30:00+08:00",
                    "ended_at": "2026-10-19T23:55:00+08:00"},
            },
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def _write_plan(self) -> Path:
        path = self.state_root / "life_day_plan.json"
        path.write_text(json.dumps({
            "schema_version": "chiyo.life.day_plan.v1", "subject_id": "chiyo",
            "revision": 1,
            "entries": {
                "plan:p1": {
                    "plan_ref": "plan:p1", "date": DAY, "class": "SELF_PLANNED",
                    "item_ref": "fitem:abc", "status": "PLANNED",
                    "source": "SUBJECT_DECISION", "decision_ref": "dec:1",
                    "revision": 1, "created_at": "2026-10-20T08:00:00+08:00",
                    "updated_at": "2026-10-20T08:00:00+08:00",
                    "idempotency_key": "plan-intent:1"},
            },
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def _write_future_item(self) -> Path:
        path = self.state_root / "life_future_items.json"
        path.write_text(json.dumps({
            "schema_version": "chiyo.life.future_items.v1", "subject_id": "chiyo",
            "revision": 1,
            "items": {
                "fitem:abc": {
                    "item_ref": "fitem:abc", "title": "读一章", "due_at": "2026-10-20T09:00:00+08:00",
                    "status": "OPEN", "origin": "MODEL_PROPOSAL",
                    "created_at": "2026-10-19T21:00:00+08:00",
                    "updated_at": "2026-10-19T21:00:00+08:00"},
            },
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def _write_ledger(self) -> Path:
        path = self.world_home / "data" / "life_execution.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [
            {"kind": "world_execution_ledger_entry",
             "schema_version": "world.execution.ledger.v2",
             "execution_id": "exec:today", "action_type": "POSE", "status": "prepared",
             "timestamp": "2026-10-20T02:00:00+00:00"},
            {"kind": "world_execution_ledger_entry",
             "schema_version": "world.execution.ledger.v2",
             "execution_id": "exec:today", "action_type": "POSE", "status": "committed",
             "timestamp": "2026-10-20T02:01:00+00:00"},
            {"kind": "world_execution_ledger_entry",
             "schema_version": "world.execution.ledger.v2",
             "execution_id": "exec:old", "action_type": "MOVE", "status": "committed",
             "timestamp": "2026-10-19T02:00:00+00:00"},
        ]
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n",
                        encoding="utf-8")
        return path

    def _everything(self) -> list[Path]:
        return [self._write_clock(), self._write_activity(), self._write_plan(),
                self._write_future_item(), self._write_ledger()]

    def _service(self) -> _FakeService:
        return _FakeService({
            "root": str(self.root), "subject": "chiyo",
            "world_home": str(self.world_home),
            "supply_socket": str(self.root / "run" / "supply.sock"),
            "private_items_artifact_id": "artifact:private-items",
            "artifact_grant_id": "grant:1", "workspace_id": "workspace:1",
        })

    def _get(self, *, query: str = "", actor: str = "") -> tuple[int, dict]:
        self.server = phone_server.PhoneHTTPServer(
            ("127.0.0.1", 0), service=self._service(), csrf_token="test-token")
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        port = self.server.server_address[1]
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/phone/today/timeline%s" % (port, query))
        if actor:
            request.add_header("X-Phone-Actor", actor)
        try:
            try:
                with urllib.request.urlopen(request, timeout=10) as answer:
                    return answer.status, json.loads(answer.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    # -- tests ----------------------------------------------------------------------------
    def test_operator_timeline_merges_sources_without_private_text(self):
        self._everything()
        status, payload = self._get()

        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"], payload)
        data = payload["data"]
        self.assertEqual(data["day"], DAY)
        self.assertEqual(data["view"], "operator")
        self.assertTrue(data["skeleton_only"])

        by_kind = {}
        for row in data["entries"]:
            by_kind.setdefault(row["kind"], []).append(row)
        for kind in ("activity", "plan", "world_action", "diary_write", "album_saved",
                     "wallet_transaction"):
            self.assertIn(kind, by_kind, data["entries"])
        self.assertEqual(len(by_kind["world_action"]), 1, by_kind["world_action"])
        world = by_kind["world_action"][0]
        self.assertEqual(world["source_ref"], "exec:today")
        self.assertEqual(world["status"], "committed")
        self.assertEqual((world["place"] or {}).get("name"), "阅读角")
        self.assertEqual(by_kind["activity"][0]["title"], "读《桃花源记》第一节")
        self.assertEqual(by_kind["plan"][0]["title"], "读一章")
        self.assertEqual(by_kind["plan"][0]["action"], "PLAN_ESTABLISHED")
        self.assertEqual(by_kind["diary_write"][0]["chars"], 42)
        self.assertTrue(by_kind["diary_write"][0]["private_detail_withheld"])
        self.assertEqual(by_kind["wallet_transaction"][0]["amount"], 120)
        self.assertEqual(by_kind["album_saved"][0]["source_label"], "user_import")
        self.assertNotIn("caption", by_kind["album_saved"][0])

        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("SECRET-CAPTION", rendered)
        self.assertNotIn("OLD-CAPTION", rendered)
        # 昨天的记录一条都不在。
        self.assertNotIn("昨夜翻了两页", rendered)
        self.assertEqual(by_kind["diary_write"][0]["source_ref"], "diary:today")

        body = data["body_snapshot"]
        self.assertEqual(body["history"], "NOT_RECORDED")
        self.assertEqual(body["time_basis"], "current_state")
        self.assertEqual(body["pose"], "seated")

        ordering = data["ordering"]
        self.assertFalse(ordering["comparable"])
        self.assertIn("business_clock", ordering["domains"])
        self.assertIn("system_clock", ordering["domains"])
        self.assertEqual(data["sources"]["activities"]["state"], "ok")
        self.assertEqual(data["sources"]["world"]["state"], "ok")

    def test_subject_view_keeps_the_same_skeleton_and_adds_detail_refs(self):
        self._everything()
        status, payload = self._get(actor="model")

        self.assertEqual(status, 200, payload)
        data = payload["data"]
        self.assertEqual(data["view"], "subject")
        self.assertFalse(data["skeleton_only"])
        rows = {row["kind"]: row for row in data["entries"]}
        self.assertFalse(rows["diary_write"]["private_detail_withheld"])
        self.assertTrue(rows["diary_write"]["detail_ref"].startswith("/api/phone/diary/"))
        self.assertFalse(rows["album_saved"]["private_detail_withheld"])
        self.assertTrue(rows["album_saved"]["detail_ref"].startswith("/api/phone/album/"))
        # 即使是她自己的工具视图，聚合也不复制正文/说明。
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("SECRET-CAPTION", rendered)
        self.assertNotIn('"caption":', rendered)

    def test_unknown_query_is_refused_before_reading(self):
        self._everything()
        status, payload = self._get(query="?day=2026-10-19")

        self.assertEqual(status, 400, payload)
        self.assertFalse(payload["ok"])
        self.assertIn("UNKNOWN_QUERY", str(payload.get("reason_code")))

    def test_reading_the_timeline_writes_nothing(self):
        paths = self._everything()
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        listing_before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))

        _status, payload = self._get()

        self.assertTrue(payload["ok"], payload)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.assertEqual(before, after)
        self.assertEqual(
            listing_before,
            sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")))


class PhoneToolRegistrationTest(unittest.TestCase):
    def test_day_timeline_is_a_read_action_and_a_private_artifact_surface(self):
        from chiyo_bundle import phone_tool

        self.assertEqual(phone_tool.READ_ACTIONS["day_timeline"],
                         "/api/phone/today/timeline")
        self.assertIn("day_timeline", phone_tool.ACTIONS)
        self.assertIn("day_timeline", phone_tool.PRIVATE_ARTIFACT_ACTIONS)
        self.assertNotIn("day_timeline", phone_tool.WRITE_ACTIONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)

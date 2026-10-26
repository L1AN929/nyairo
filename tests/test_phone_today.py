#!/usr/bin/env python3
"""``GET /api/phone/today``：今天做过的活动与作品版本，只读。

这里不起 Supply、不调用模型、不碰真实实例：只造一个手机根 + 嵌套 Life 实例、一份
canonical Activity 权威、一份业务时钟文件和一份内存里的作品 port，然后用真实的
``PhoneHTTPServer`` 走一遍 HTTP 面。覆盖：

* 今天（业务时钟当天）的两条活动都在，昨天的活动不在——日界按 +08:00，与 Activity
  自己的写入时区一致；UTC 前一天晚上的时间戳也必须是「今天」；
* 今天产生的作品版本带作品名和版本号，昨天的版本不出现；
* 一天什么都没做就是两个空列表，不编内容；
* 活动权威读不到时是具名的 unavailable，而不是看起来「她今天很安静」的成功空答案；
* 调用前后状态根与时钟文件逐字节不变（只读，零写）。
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
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

DAY = "2026-10-20"
DAY_STARTED_EARLY = "2026-10-20T01:10:00+08:00"   # UTC 是 10-19T17:10：按 UTC 分日会掉
DAY_STARTED_LATE = "2026-10-20T14:30:00+08:00"
YESTERDAY_STARTED = "2026-10-19T23:30:00+08:00"


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


class _FakeProjectPort:
    """只读的作品 port：与真 owner 同形状的列表/正文/版本，不做任何写。"""

    subject_id = "chiyo"

    def __init__(self) -> None:
        self.artifact = {
            "artifact_id": "artifact:work1",
            "title": "窗边手记",
            "lifecycle": "ACTIVE",
            "revision": 2,
            "head_version_id": "av:2",
            "created_at": _epoch("2026-10-18T09:00:00+08:00"),
            "updated_at": _epoch("2026-10-20T10:00:00+08:00"),
            "workspace_id": "workspace:1",
        }
        self.versions = [
            {"version_id": "av:2", "version_number": 2, "parent_version_id": "av:1",
             "created_at": _epoch("2026-10-20T10:00:00+08:00"), "operation_key": "op:2",
             "content_sha256": "b" * 64},
            {"version_id": "av:1", "version_number": 1, "parent_version_id": None,
             "created_at": _epoch("2026-10-19T10:00:00+08:00"), "operation_key": "op:1",
             "content_sha256": "a" * 64},
        ]

    # -- reads ---------------------------------------------------------------------------
    def list_artifacts(self):
        return [dict(self.artifact)]

    def read_artifact(self, artifact_id: str):
        return {"artifact_id": artifact_id, "content": "窗边手记\n\n今天窗边很亮。\n",
                "head": {"version_id": "av:2", "version_number": 2,
                         "created_at": _epoch("2026-10-20T10:00:00+08:00")}}

    def list_versions(self, artifact_id: str, *, limit=None, before_version_number=None):
        return {"entries": [dict(row) for row in self.versions], "complete": True,
                "reason_code": "", "total_versions": len(self.versions),
                "source": "owner"}

    # -- writes (never called by a read; present because the port shape requires them) ----
    def create_artifact(self, **kwargs):  # pragma: no cover - 只读测试不写
        raise AssertionError("today must never write")

    def commit_version(self, **kwargs):  # pragma: no cover
        raise AssertionError("today must never write")

    def archive_artifact(self, **kwargs):  # pragma: no cover
        raise AssertionError("today must never write")


class TodayAPITest(unittest.TestCase):
    """一条真实 HTTP 读取路径；根、状态与时钟全部由本用例自己造。"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="phone-today-"))
        self.addCleanup(self._cleanup)
        # 嵌套 Life 实例：手机根下的 one-day/instance.json（只写 schema，读侧只校验它）。
        nested = self.root / "one-day"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "instance.json").write_text(
            json.dumps({"schema_version": "life.one_day.v1"}), encoding="utf-8")
        self.state_root = nested / "life" / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._write_clock("2026-10-20T12:00:00+08:00")
        self.port = _FakeProjectPort()
        self.server = None

    def _cleanup(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    # -- fixtures -------------------------------------------------------------------------
    def _write_clock(self, logical_checkpoint: str) -> Path:
        path = self.state_root / "fastday" / "fastday_clock.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "kind": "explicit_compressed_demo_clock", "schema_version": 1,
            "mode": "COMPRESSED_DEMO", "rate": 12.0, "state": "STOPPED",
            "domain_id": "fastday:run:phone-today", "run_id": "run:phone-today",
            "logical_anchor": "2026-10-20T00:00:00+08:00",
            "logical_checkpoint": logical_checkpoint,
            "horizon_logical": "2026-10-21T00:00:00+08:00",
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def _write_activities(self, activities: dict) -> Path:
        path = self.state_root / "canonical_life_state.json"
        path.write_text(json.dumps({
            "schema_version": "life.activity.canonical.v1", "subject_id": "chiyo",
            "revision": 4, "foreground_activity_ref": "actv_today_two",
            "activities": activities,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def _two_today_and_one_yesterday(self) -> Path:
        return self._write_activities({
            "actv_today_one": {
                "activity_id": "actv_today_one", "activity_kind": "READING",
                "title": "读完《桃花源记》第一节", "status": "COMPLETED",
                "started_at": DAY_STARTED_EARLY, "ended_at": "2026-10-20T01:40:00+08:00",
                "terminal_reason_code": "COMPLETED_WITH_EVIDENCE"},
            "actv_today_two": {
                "activity_id": "actv_today_two", "activity_kind": "BODY_ACTION",
                "title": "在窗边坐下", "status": "ACTIVE",
                "started_at": DAY_STARTED_LATE, "ended_at": None,
                "terminal_reason_code": None},
            "actv_yesterday": {
                "activity_id": "actv_yesterday", "activity_kind": "READING",
                "title": "昨夜翻了两页", "status": "ABANDONED",
                "started_at": YESTERDAY_STARTED, "ended_at": "2026-10-19T23:55:00+08:00",
                "terminal_reason_code": "ABANDONED_BY_DECISION"},
        })

    # -- HTTP -----------------------------------------------------------------------------
    def _service(self) -> phone_service.PhoneService:
        return phone_service.PhoneService({
            "root": str(self.root), "subject": "chiyo",
            "world_home": str(self.root / "world"),
            "supply_socket": str(self.root / "run" / "supply.sock"),
            "private_items_artifact_id": "artifact:private-items",
            "artifact_grant_id": "grant:1", "workspace_id": "workspace:1",
        }, project_port=self.port)

    def _get_today(self) -> tuple[int, dict]:
        self.server = phone_server.PhoneHTTPServer(
            ("127.0.0.1", 0), service=self._service(), csrf_token="test-token")
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        port = self.server.server_address[1]
        try:
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/api/phone/today" % port, timeout=10) as answer:
                    return answer.status, json.loads(answer.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:  # 4xx/5xx 的正文仍是本接口的 envelope
                return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    # -- tests ----------------------------------------------------------------------------
    def test_today_lists_the_two_today_activities_and_not_yesterday(self):
        self._two_today_and_one_yesterday()
        status, payload = self._get_today()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertEqual(data["day"], DAY)
        self.assertEqual(data["clock"]["source"], "business_clock")
        titles = [row["title"] for row in data["activities"]]
        self.assertEqual(len(titles), 2, data["activities"])
        self.assertIn("读完《桃花源记》第一节", titles)
        self.assertIn("在窗边坐下", titles)
        self.assertNotIn("昨夜翻了两页", titles)
        # 结果与开始时间都是 owner 自己的记录，不是这里推的。
        first = data["activities"][0]
        self.assertEqual(first["started_at"], DAY_STARTED_EARLY)
        self.assertEqual(first["status"], "COMPLETED")
        self.assertEqual(data["sources"]["activities"]["state"], "ok")

    def test_today_lists_todays_work_version_with_title_and_number(self):
        self._two_today_and_one_yesterday()
        _status, payload = self._get_today()

        works = payload["data"]["works"]
        self.assertEqual(len(works), 1, works)
        self.assertEqual(works[0]["title"], "窗边手记")
        self.assertEqual(works[0]["version_number"], 2)
        self.assertEqual(works[0]["artifact_id"], "artifact:work1")
        self.assertEqual(payload["data"]["sources"]["works"]["state"], "ok")

    def test_a_day_with_nothing_returns_empty_lists(self):
        # 作品版本也留在昨天：今天确实没有任何已发生的事。
        self.port.versions[0]["created_at"] = _epoch("2026-10-19T10:00:00+08:00")
        self._write_activities({
            "actv_yesterday": {
                "activity_id": "actv_yesterday", "activity_kind": "READING",
                "title": "昨夜翻了两页", "status": "ABANDONED",
                "started_at": YESTERDAY_STARTED, "ended_at": "2026-10-19T23:55:00+08:00"},
        })
        _status, payload = self._get_today()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["activities"], [])
        self.assertEqual(payload["data"]["activity_count"], 0)
        self.assertEqual(payload["data"]["works"], [])

    def test_missing_authority_is_unavailable_not_an_empty_day(self):
        _status, payload = self._get_today()

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["reason_code"], "CANONICAL_STATE_ABSENT")

    def test_reading_today_writes_nothing(self):
        state = self._two_today_and_one_yesterday()
        clock = self.state_root / "fastday" / "fastday_clock.json"
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (state, clock)}
        listing_before = sorted(p.name for p in self.state_root.rglob("*"))

        _status, payload = self._get_today()

        self.assertTrue(payload["ok"])
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (state, clock)}
        self.assertEqual(before, after)
        self.assertEqual(listing_before, sorted(p.name for p in self.state_root.rglob("*")))


if __name__ == "__main__":
    unittest.main(verbosity=2)

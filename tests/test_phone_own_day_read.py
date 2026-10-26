#!/usr/bin/env python3
"""``GET /api/phone/today/arrangements``：今天安排与可证执行状态，只读。

不碰真实实例：造一个手机根 + 嵌套 Life 状态根、真实 day_plan/obligations 文档（经各自
领域写口）、一份 canonical Activity 权威，再用假 Action/Decision 读口驱动执行证据。
覆盖：

* 自选计划与外部义务分列表；``PLANNED`` 只表示排进今天，不等于未开始；
* 义务 ``DONE`` 必须同时满足同一目标、``START``、精确候选 id 和带版本的 owner 回执；
* 同一作品的新版本 / 另一个 Decision 的动作不能冒认计划完成；
* 没有决定链的 Activity 标 ``UNKNOWN``，不硬塞进「未排」；
* 空日诚实为空，坏来源具名不可用，昨天的计划不在今天；
* ``/today`` 原有形状不变；两次读取前后所有文件逐字节不变（零写）。
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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import own_day_read  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

DAY = "2026-10-26"
NOW = "2026-10-26T12:00:00+08:00"
TARGET = "artifact:work1"


class _FakeActionRead:
    """The AR-0 read port shape the projection actually uses; no write methods at all."""

    def __init__(self) -> None:
        self.actions: list[dict] = []
        self.receipts: dict[str, list[dict]] = {}

    def list_actions(self, *, status=None):
        rows = [dict(row) for row in self.actions]
        if status is not None:
            rows = [row for row in rows if row.get("status") == status]
        return rows

    def list_receipts(self, action_id: str):
        return [dict(row) for row in self.receipts.get(str(action_id), [])]


class _FakeDecisionRead:
    def __init__(self) -> None:
        self.decisions: dict[str, dict] = {}

    def get_decision(self, decision_id: str):
        row = self.decisions.get(str(decision_id))
        return dict(row) if isinstance(row, dict) else None


class _EmptyProjectPort:
    def list_artifacts(self):
        return []


class ArrangementsReadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="phone-own-day-"))
        self.addCleanup(self._cleanup)
        nested = self.root / "one-day"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "instance.json").write_text(
            json.dumps({"schema_version": "life.one_day.v1"}), encoding="utf-8")
        self.state_root = nested / "life" / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._write_clock(NOW)
        self._write_activities({})
        self.action_read = _FakeActionRead()
        self.decision_read = _FakeDecisionRead()
        patches = [
            mock.patch.object(own_day_read, "build_action_reader",
                              lambda _root: self.action_read),
            mock.patch.object(own_day_read, "build_decision_reader",
                              lambda _root: self.decision_read),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = None

    def _cleanup(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    # -- fixtures -------------------------------------------------------------------------
    def _write_clock(self, logical_checkpoint: str) -> Path:
        path = self.state_root / "fastday" / "fastday_clock.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "kind": "explicit_compressed_demo_clock", "schema_version": 1,
            "mode": "COMPRESSED_DEMO", "rate": 12.0, "state": "STOPPED",
            "domain_id": "fastday:run:phone-own-day", "run_id": "run:phone-own-day",
            "logical_anchor": "2026-10-26T00:00:00+08:00",
            "logical_checkpoint": logical_checkpoint,
            "horizon_logical": "2026-10-27T00:00:00+08:00",
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def _write_activities(self, activities: dict) -> Path:
        path = self.state_root / "canonical_life_state.json"
        path.write_text(json.dumps({
            "schema_version": "life.activity.canonical.v1", "subject_id": "chiyo",
            "revision": 1, "foreground_activity_ref": None, "activities": activities,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def _activity(self, ref: str, *, decision_ref=None, status="ACTIVE") -> dict:
        return {
            "activity_id": ref, "activity_kind": "CONTINUE_PERSONAL_ARTIFACT",
            "title": "继续《窗边手记》", "status": status,
            "started_at": "2026-10-26T11:00:00+08:00",
            "ended_at": "2026-10-26T11:20:00+08:00" if status != "ACTIVE" else None,
            "terminal_reason_code": None,
            "decision_ref": decision_ref,
            "source_refs": [TARGET],
        }

    def _plan_item(self, title: str = "把水井那一段补记一笔",
                   source_refs=None) -> dict:
        import life_future_items as lfi

        store = lfi.FutureItems(self.state_root, subject_id="chiyo")
        answer = store.propose(title=title, due_at=NOW,
                               source_refs=list(source_refs or []), now=NOW)
        return dict(answer["item"])

    def _plan(self, item: dict, *, decision_ref: str = "dec:plan:1") -> dict:
        import life_day_plan as ldp

        store = ldp.DayPlan(self.state_root, subject_id="chiyo")
        return dict(store.plan(item, date=DAY, decision_ref=decision_ref, now=NOW)["entry"])

    def _obligation(self, *, title: str = "上午先把导言读完并写一句",
                    target: str = TARGET, due_at: str = "2026-10-26T13:00:00+08:00",
                    key: str = "oblig:test:1") -> dict:
        import life_day_plan as ldp

        answer = ldp.import_obligation(
            self.state_root, date=DAY, title=title, target_ref=target,
            due_at=due_at, source_ref="operator:test", operation_key=key,
            target_verifier=lambda _ref: {"resumable": True},
            subject_id="chiyo", now=NOW)
        return dict(answer["entry"])

    def _obligation_candidate(self, obligation_ref: str) -> str:
        import life_day_plan as ldp
        from artifact_continuation_source import ArtifactContinuationSource

        option = ldp.obligation_option_ref(obligation_ref, TARGET, DAY)
        return ArtifactContinuationSource.extra_candidate_id(option, 1)

    def _succeeded(self, *, action_id: str, decision_ref: str, activity_ref: str,
                   version_id: str = "av:2") -> None:
        self.action_read.actions.append({
            "action_id": action_id, "target_ref": TARGET, "status": "SUCCEEDED",
            "activity_ref": activity_ref, "decision_ref": decision_ref,
        })
        self.action_read.receipts[action_id] = [{
            "receipt_id": "rcpt:%s" % action_id, "action_id": action_id,
            "status_claim": "SUCCEEDED", "observed_at": "2026-10-26T11:05:00+08:00",
            "details": {"version_id": version_id, "version_number": 2},
        }]

    # -- HTTP -----------------------------------------------------------------------------
    def _service(self) -> phone_service.PhoneService:
        return phone_service.PhoneService({
            "root": str(self.root), "subject": "chiyo",
            "world_home": str(self.root / "world"),
            "supply_socket": str(self.root / "run" / "supply.sock"),
            "private_items_artifact_id": "artifact:private-items",
            "artifact_grant_id": "grant:1", "workspace_id": "workspace:1",
        }, project_port=_EmptyProjectPort())

    def _start_server(self) -> None:
        self.server = phone_server.PhoneHTTPServer(
            ("127.0.0.1", 0), service=self._service(), csrf_token="test-token")
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

    def _get(self, path: str) -> tuple[int, dict]:
        if self.server is None:
            self._start_server()
        port = self.server.server_address[1]
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d%s" % (port, path), timeout=10) as answer:
                return answer.status, json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _arrangements(self) -> tuple[int, dict]:
        return self._get("/api/phone/today/arrangements")

    # -- tests ----------------------------------------------------------------------------
    def test_plan_and_obligation_are_separate_and_planned_is_not_not_started(self):
        item = self._plan_item(source_refs=[TARGET])
        plan = self._plan(item)
        obligation = self._obligation()

        status, payload = self._arrangements()

        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertEqual(data["day"], DAY)
        self.assertEqual(data["clock"]["source"], "business_clock")
        self.assertTrue(data["read_only"])
        self.assertEqual(data["plan"]["count"], 1)
        self.assertEqual(data["obligations"]["count"], 1)
        row = data["plan"]["entries"][0]
        self.assertEqual(row["plan_ref"], plan["plan_ref"])
        self.assertEqual(row["item_ref"], item["item_ref"])
        self.assertEqual(row["definition_status"], "PLANNED")
        # PLANNED 是「排进今天」，不是「还没开始」；没有执行证据时是 UNKNOWN。
        self.assertEqual(row["execution"]["state"], "UNKNOWN")
        self.assertEqual(row["execution"]["reason_code"], "NO_EXECUTION_EVIDENCE")
        obligation_row = data["obligations"]["entries"][0]
        self.assertEqual(obligation_row["obligation_ref"], obligation["obligation_ref"])
        self.assertEqual(obligation_row["result"], "PENDING")
        self.assertEqual(obligation_row["execution"]["state"], "PENDING")
        self.assertEqual(data["unplanned_count"], 0)
        self.assertEqual(data["sources"]["plan"]["state"], "ok")
        self.assertEqual(data["sources"]["obligations"]["state"], "ok")

    def test_obligation_done_requires_exact_decision_and_owner_receipt(self):
        obligation = self._obligation()
        candidate = self._obligation_candidate(obligation["obligation_ref"])
        self._succeeded(action_id="act:1", decision_ref="dec:1", activity_ref="actv_1")
        self.decision_read.decisions["dec:1"] = {
            "decision_id": "dec:1", "decision": "START",
            "selected_candidate_ref": candidate,
        }
        self._write_activities({"actv_1": self._activity("actv_1", decision_ref="dec:1")})

        status, payload = self._arrangements()

        self.assertEqual(status, 200, payload)
        data = payload["data"]
        row = data["obligations"]["entries"][0]
        self.assertEqual(row["result"], "DONE")
        self.assertEqual(row["evidence"]["decision_id"], "dec:1")
        self.assertEqual(row["evidence"]["activity_ref"], "actv_1")
        self.assertEqual(row["evidence"]["version_id"], "av:2")
        # 已关联的活动不进「未排」，也不进「关联不足」。
        self.assertEqual(data["unplanned_count"], 0)
        self.assertEqual(data["unclassified_count"], 0)
        self.assertEqual(data["activity_count"], 1)

    def test_known_but_different_decision_leaves_obligation_pending(self):
        obligation = self._obligation()
        self._obligation_candidate(obligation["obligation_ref"])
        self._succeeded(action_id="act:2", decision_ref="dec:2", activity_ref="actv_2")
        self.decision_read.decisions["dec:2"] = {
            "decision_id": "dec:2", "decision": "START",
            "selected_candidate_ref": "cand:some-other-choice",
        }
        self._write_activities({"actv_2": self._activity("actv_2", decision_ref="dec:2")})

        _status, payload = self._arrangements()

        data = payload["data"]
        self.assertEqual(data["obligations"]["entries"][0]["result"], "PENDING")
        # 同一个目标上、另一个已知 Decision 的动作不是这条义务的完成，但活动确实未排。
        self.assertEqual(data["unplanned_count"], 1)
        self.assertEqual(data["unplanned_activities"][0]["ref"], "actv_2")

    def test_plan_done_uses_the_real_reserved_candidate_identity(self):
        import life_day_plan as ldp
        import life_future_items as lfi
        from artifact_continuation_source import ArtifactContinuationSource

        item = self._plan_item(source_refs=[TARGET])
        entry = self._plan(item)
        futures = lfi.FutureItems(self.state_root, subject_id="chiyo")

        def lookup(ref):
            found = futures.get(ref)
            return found.to_dict() if found is not None else None

        options, withheld = ldp.reserved_options(
            plan_entries=[entry], obligations=[], now=NOW,
            futures_lookup=lookup, target_reader=lambda _ref: {"resumable": True}, limit=1)
        self.assertEqual(len(options), 1, (options, withheld))
        candidate = ArtifactContinuationSource.extra_candidate_id(
            options[0]["option_ref"], 1)
        self._succeeded(action_id="act:plan", decision_ref="dec:plan:exec",
                        activity_ref="actv_plan", version_id="av:9")
        self.decision_read.decisions["dec:plan:exec"] = {
            "decision_id": "dec:plan:exec", "decision": "START",
            "selected_candidate_ref": candidate,
        }
        self._write_activities({"actv_plan": self._activity("actv_plan")})

        _status, payload = self._arrangements()

        data = payload["data"]
        row = data["plan"]["entries"][0]
        self.assertEqual(row["execution"]["state"], "DONE", row["execution"])
        self.assertEqual(row["execution"]["evidence"]["decision_id"], "dec:plan:exec")
        self.assertEqual(row["execution"]["evidence"]["activity_ref"], "actv_plan")
        self.assertEqual(row["execution"]["evidence"]["version_id"], "av:9")
        self.assertEqual(data["unplanned_count"], 0)

    def test_newer_version_of_same_work_does_not_complete_the_plan(self):
        item = self._plan_item(source_refs=[TARGET])
        self._plan(item)
        self._succeeded(action_id="act:3", decision_ref="dec:3",
                        activity_ref="actv_3", version_id="av:5")
        self.decision_read.decisions["dec:3"] = {
            "decision_id": "dec:3", "decision": "START",
            "selected_candidate_ref": "cand:normal-continuation",
        }
        self._write_activities({"actv_3": self._activity("actv_3", decision_ref="dec:3")})

        _status, payload = self._arrangements()

        data = payload["data"]
        row = data["plan"]["entries"][0]
        self.assertEqual(row["execution"]["state"], "UNKNOWN")
        self.assertEqual(row["execution"]["reason_code"], "NO_EXECUTION_EVIDENCE")
        self.assertEqual(data["unplanned_count"], 1)

    def test_activity_without_decision_link_is_unknown_not_unplanned(self):
        item = self._plan_item()
        self._plan(item)
        self._write_activities({"actv_x": {
            "activity_id": "actv_x", "activity_kind": "READING", "title": "翻了两页",
            "status": "COMPLETED", "started_at": "2026-10-26T10:00:00+08:00",
            "ended_at": "2026-10-26T10:20:00+08:00", "decision_ref": None,
            "source_refs": [TARGET],
        }})

        _status, payload = self._arrangements()

        data = payload["data"]
        self.assertEqual(data["unplanned_count"], 0)
        self.assertEqual(data["unclassified_count"], 1)
        row = data["unclassified_activities"][0]
        self.assertEqual(row["association"], "UNKNOWN")
        self.assertEqual(row["reason_code"], "DECISION_REF_MISSING")

    def test_empty_day_is_honest_and_empty(self):
        status, payload = self._arrangements()

        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertEqual(data["plan"]["entries"], [])
        self.assertEqual(data["obligations"]["entries"], [])
        self.assertEqual(data["unplanned_activities"], [])
        self.assertEqual(data["unclassified_activities"], [])
        self.assertEqual(data["activity_count"], 0)

    def test_corrupt_plan_is_named_not_an_empty_day(self):
        (self.state_root / "life_day_plan.json").write_text("not json", encoding="utf-8")

        status, payload = self._arrangements()

        self.assertEqual(status, 503, payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason_code"], "DAY_PLAN_STATE_CORRUPT")
        self.assertEqual(payload["data"]["plan"]["status"], "unavailable")
        self.assertEqual(payload["data"]["plan"]["entries"], [])

    def test_yesterday_plan_is_not_today(self):
        (self.state_root / "life_day_plan.json").write_text(json.dumps({
            "schema_version": "chiyo.life.day_plan.v1", "subject_id": "chiyo",
            "revision": 1,
            "entries": {"plan:old": {
                "plan_ref": "plan:old", "date": "2026-10-25", "item_ref": "fitem:old",
                "class": "SELF_PLANNED", "status": "PLANNED",
                "source": "SUBJECT_DECISION", "decision_ref": "dec:old", "revision": 1,
            }},
        }, ensure_ascii=False), encoding="utf-8")

        _status, payload = self._arrangements()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["plan"]["entries"], [])

    def test_unknown_query_is_refused(self):
        status, payload = self._get("/api/phone/today/arrangements?day=2026-10-25")

        self.assertEqual(status, 400, payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason_code"], "UNKNOWN_QUERY")

    def test_today_shape_is_unchanged_and_both_reads_write_nothing(self):
        self._write_activities({
            "actv_today": {
                "activity_id": "actv_today", "activity_kind": "READING",
                "title": "读完第一节", "status": "COMPLETED",
                "started_at": "2026-10-26T09:00:00+08:00",
                "ended_at": "2026-10-26T09:30:00+08:00",
                "terminal_reason_code": "COMPLETED_WITH_EVIDENCE",
            },
        })
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(self.state_root.rglob("*")) if path.is_file()}
        listing = sorted(str(path.relative_to(self.state_root))
                         for path in self.state_root.rglob("*"))

        today_status, today_payload = self._get("/api/phone/today")
        self.assertEqual(today_status, 200, today_payload)
        self.assertTrue(today_payload["ok"])
        self.assertEqual(set(today_payload["data"]), {
            "schema", "day", "day_basis", "timezone", "clock", "activities",
            "activity_count", "works", "work_count", "sources", "read_only"})
        self.assertEqual(today_payload["data"]["schema"], "phone.today.v1")
        self.assertEqual(today_payload["data"]["day_basis"], "activity_started_at")
        self.assertEqual(len(today_payload["data"]["activities"]), 1)

        arr_status, arr_payload = self._arrangements()
        self.assertEqual(arr_status, 200, arr_payload)
        self.assertTrue(arr_payload["ok"])

        after = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(self.state_root.rglob("*")) if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(listing, sorted(str(path.relative_to(self.state_root))
                                         for path in self.state_root.rglob("*")))


if __name__ == "__main__":
    unittest.main(verbosity=2)

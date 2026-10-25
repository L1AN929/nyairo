#!/usr/bin/env python3
"""手机「此刻」与受控窗口的离线正负控。

这里不启服务、不碰真实实例、不发模型请求：

* ``window_status`` / ``run_bounded_window`` 的共享上限从持久请求账本推回，监督进程重启、
  状态文件丢失、窗口崩溃都不会刷新它；
* 账本读不到、run 窗口改变、参数越界、并发窗口都在**启动之前**具名拒绝；
* ``Supervisor.stop_window`` 只停自己启动的子进程；
* ``living_view`` 只读：历史 ACTIVE wake 不会因为页面打开就变成「正在做」。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(ROOT / "components"), str(LIFE_SCRIPTS)]

from components.phone import living  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life budget/lock modules use fcntl")

RUN_ID = "run:one-day"


def _ledger(state_root: Path, calls: int, *, run_id: str = RUN_ID) -> Path:
    from life_request_budget import SCHEMA_VERSION, KIND, budget_path

    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "active_day": "2026-10-23",
        "days": {"2026-10-23": calls},
        "windows": {run_id: {"calls": calls, "opened_at": "2026-10-23T00:00:00+00:00"}},
        "run_id": run_id,
        "tokens": [],
        "opened_at": "2026-10-23T00:00:00+00:00",
        "updated_at": "2026-10-23T00:00:00+00:00",
    }
    path = budget_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _calls(state_root: Path) -> int:
    from life_request_budget import budget_path

    return int(json.loads(budget_path(state_root).read_text(encoding="utf-8"))
               ["windows"][RUN_ID]["calls"])


@POSIX_ONLY
class BoundedWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="usable-window-"))
        self.root = self.tmp / "phone"
        self.state_root = self.tmp / "life-state"
        self.root.mkdir(parents=True)
        self.state_root.mkdir(parents=True)
        self.env = {
            "CHIYO_AUTONOMOUS_RUN_ID": RUN_ID,
            "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY": "96",
            "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW": "96",
        }
        _ledger(self.state_root, 13)
        self._original = living._bound_environment
        living._bound_environment = lambda root, environment=None: (dict(self.env),
                                                                   self.state_root)
        self.addCleanup(setattr, living, "_bound_environment", self._original)
        self.calls = []

    def _spend(self, amount: int) -> dict:
        _ledger(self.state_root, _calls(self.state_root) + int(amount))
        return {"ok": True, "status": "READY", "exit_code": 0, "model_called": True,
                "status_word": "ACTED"}

    def test_first_window_records_the_ledger_baseline_and_spends_only_what_was_asked(self):
        result = living.run_bounded_window(self.root, duration_seconds=30, max_new_requests=1,
                                           run_fn=lambda root, **kw: self._spend(kw["max_new_requests"]))
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["window"]["allowed_requests"], 1)
        self.assertEqual(result["window"]["spent_requests"], 1)
        budget = living.window_status(self.root)
        self.assertEqual(budget["used"], 1)
        self.assertEqual(budget["remaining"], 3)
        doc = json.loads((self.root / "run" / "usable-window.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["ledger_baseline_run_window_calls"], 13)

    def test_cap_is_shared_across_supervisor_restarts(self):
        for expected_used, expected_remaining in ((1, 3), (2, 2), (3, 1)):
            result = living.run_bounded_window(
                self.root, duration_seconds=30, max_new_requests=1,
                run_fn=lambda root, **kw: self._spend(kw["max_new_requests"]))
            self.assertTrue(result.get("ok"), result)
            budget = living.window_status(self.root)
            self.assertEqual(budget["used"], expected_used)
            self.assertEqual(budget["remaining"], expected_remaining)

        exhausted = living.run_bounded_window(
            self.root, duration_seconds=30, max_new_requests=1,
            run_fn=lambda root, **kw: self._spend(1))
        self.assertTrue(exhausted.get("ok"))
        refused = living.run_bounded_window(
            self.root, duration_seconds=30, max_new_requests=1,
            run_fn=lambda root, **kw: self._spend(1))
        self.assertFalse(refused.get("ok"))
        self.assertEqual(refused["reason"], "FUNCTION_WINDOW_CAP_EXHAUSTED")
        self.assertFalse(refused["model_called"])
        self.assertEqual(_calls(self.state_root), 17)

    def test_losing_one_copy_does_not_refresh_the_cap(self):
        living.run_bounded_window(self.root, duration_seconds=30, max_new_requests=2,
                                  run_fn=lambda root, **kw: self._spend(kw["max_new_requests"]))
        self.assertEqual(living.window_status(self.root)["used"], 2)
        (self.root / "run" / "usable-window.json").unlink()
        budget = living.window_status(self.root)
        # The mirror next to the Life state still carries the baseline.
        self.assertEqual(budget["baseline"], 13)
        self.assertEqual(budget["used"], 2)
        self.assertEqual(budget["remaining"], 2)

    def test_losing_every_copy_refuses_instead_of_rebasing(self):
        living.run_bounded_window(self.root, duration_seconds=30, max_new_requests=2,
                                  run_fn=lambda root, **kw: self._spend(kw["max_new_requests"]))
        (self.root / "run" / "usable-window.json").unlink()
        (self.root / "one-day" / "run" / "usable-window.json").unlink()
        budget = living.window_status(self.root)
        self.assertTrue(budget["baseline_lost"])
        self.assertEqual(budget["reason"], "WINDOW_BASELINE_LOST")
        self.assertEqual(budget["remaining"], 0)
        called = []
        result = living.run_bounded_window(
            self.root, duration_seconds=30, max_new_requests=1,
            run_fn=lambda root, **kw: called.append(kw) or {"ok": True})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result["reason"], "WINDOW_BASELINE_LOST")
        self.assertEqual(called, [])
        self.assertEqual(_calls(self.state_root), 15)

    def test_ledger_rollback_never_lowers_the_used_high_water(self):
        living.run_bounded_window(self.root, duration_seconds=30, max_new_requests=2,
                                  run_fn=lambda root, **kw: self._spend(kw["max_new_requests"]))
        self.assertEqual(living.window_status(self.root)["used"], 2)
        _ledger(self.state_root, 13)  # a restored/older ledger
        budget = living.window_status(self.root)
        self.assertEqual(budget["used"], 2)
        self.assertEqual(budget["remaining"], 2)

    def test_unreadable_ledger_refuses_before_anything_starts(self):
        from life_request_budget import budget_path

        budget_path(self.state_root).write_text("{not json", encoding="utf-8")
        called = []
        result = living.run_bounded_window(
            self.root, duration_seconds=30, max_new_requests=1,
            run_fn=lambda root, **kw: called.append(kw) or {"ok": True})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result["reason"], "LEDGER_UNREADABLE")
        self.assertEqual(called, [])
        self.assertFalse(result["model_called"])

    def test_a_changed_run_window_refuses(self):
        living.run_bounded_window(self.root, duration_seconds=30, max_new_requests=1,
                                  run_fn=lambda root, **kw: self._spend(kw["max_new_requests"]))
        _ledger(self.state_root, 14, run_id="run:another")
        result = living.run_bounded_window(self.root, duration_seconds=30, max_new_requests=1,
                                           run_fn=lambda root, **kw: self._spend(1))
        self.assertFalse(result.get("ok"))
        self.assertEqual(result["reason"], "RUN_WINDOW_CHANGED")

    def test_bounds_are_refused_before_the_ledger_is_touched(self):
        for kwargs, reason in (
            ({"duration_seconds": 601, "max_new_requests": 1}, "DURATION_OUT_OF_RANGE"),
            ({"duration_seconds": 30, "max_new_requests": 5}, "REQUEST_CAP_OUT_OF_RANGE"),
            ({"duration_seconds": 0, "max_new_requests": 1}, "DURATION_OUT_OF_RANGE"),
        ):
            called = []
            result = living.run_bounded_window(
                self.root, run_fn=lambda root, **kw: called.append(kw) or {"ok": True}, **kwargs)
            self.assertFalse(result.get("ok"))
            self.assertEqual(result["reason"], reason)
            self.assertEqual(called, [])

    def test_a_held_writer_lease_is_reported_not_stolen(self):
        import fcntl

        import activity_continuity as ac

        run = self.state_root / "run"
        run.mkdir(parents=True, exist_ok=True)
        lock_path = run / ac.LOCK_FILENAME
        handle = lock_path.open("a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        called = []
        try:
            result = living.run_bounded_window(
                self.root, duration_seconds=30, max_new_requests=1,
                run_fn=lambda root, **kw: called.append(kw) or {"ok": True})
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        self.assertFalse(result.get("ok"))
        self.assertEqual(result["reason"], "LEASE_NOT_FREE")
        self.assertEqual(called, [])
        self.assertFalse(result["model_called"])

    def test_a_second_concurrent_window_is_refused(self):
        entered = threading.Event()

        def slow(root, **kw):
            entered.set()
            time.sleep(0.6)
            return self._spend(1)

        box = {}
        thread = threading.Thread(
            target=lambda: box.update(living.run_bounded_window(
                self.root, duration_seconds=30, max_new_requests=1, run_fn=slow)))
        thread.start()
        entered.wait(2.0)
        second = living.run_bounded_window(
            self.root, duration_seconds=30, max_new_requests=1, run_fn=slow)
        thread.join(5.0)
        self.assertTrue(box.get("ok"), box)
        self.assertFalse(second.get("ok"))
        self.assertEqual(second["reason"], "WINDOW_ALREADY_RUNNING")
        self.assertEqual(_calls(self.state_root), 14)


@POSIX_ONLY
class SupervisorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="usable-supervisor-"))
        self.root = self.tmp / "phone"
        self.root.mkdir(parents=True)
        self.state_root = self.tmp / "life-state"
        self.state_root.mkdir(parents=True)
        _ledger(self.state_root, 13)
        self._original = living._bound_environment
        living._bound_environment = lambda root, environment=None: (
            {"CHIYO_AUTONOMOUS_RUN_ID": RUN_ID}, self.state_root)
        self.addCleanup(setattr, living, "_bound_environment", self._original)

    def test_snapshot_reports_capacity_and_does_not_start_a_window(self):
        supervisor = living.Supervisor(self.root, port=3831)
        snapshot = supervisor.snapshot()
        self.assertTrue(snapshot["running"])
        self.assertEqual(snapshot["windows"]["remaining"], 4)
        self.assertIsNone(snapshot["child"])
        self.assertFalse((self.root / "run" / "usable-window.json").exists())

    def test_stop_refuses_when_it_started_nothing(self):
        supervisor = living.Supervisor(self.root, port=3831)
        result = supervisor.stop_window()
        self.assertFalse(result.get("ok"))
        self.assertEqual(result["reason"], "NO_SUPERVISED_WINDOW")

    def test_stop_only_signals_its_own_child(self):
        supervisor = living.Supervisor(self.root, port=3831)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        supervisor._child = child
        try:
            result = supervisor.stop_window()
            self.assertTrue(result.get("ok"))
            self.assertIsNotNone(child.poll())
        finally:
            if child.poll() is None:
                child.kill()

    def test_pause_and_resume_only_toggle_the_operator_bit(self):
        import life_pause

        supervisor = living.Supervisor(self.root, port=3831)
        paused = supervisor.set_paused(True)
        self.assertTrue(paused.get("ok"))
        self.assertTrue(paused.get("paused"))
        self.assertTrue(life_pause.read(self.state_root)["paused"])
        resumed = supervisor.set_paused(False)
        self.assertTrue(resumed.get("ok"))
        self.assertFalse(resumed.get("paused"))
        self.assertFalse(life_pause.read(self.state_root)["paused"])
        # Resuming never opens a window or starts a model.
        self.assertIsNone(supervisor._child)
        self.assertFalse((self.root / "run" / "usable-window.json").exists())

    def test_start_window_refuses_out_of_range_before_spawning(self):
        supervisor = living.Supervisor(self.root, port=3831)
        for kwargs, reason in (
            ({"duration_seconds": 601, "max_new_requests": 1}, "DURATION_OUT_OF_RANGE"),
            ({"duration_seconds": 30, "max_new_requests": 5}, "REQUEST_CAP_OUT_OF_RANGE"),
        ):
            result = supervisor.start_window(**kwargs)
            self.assertFalse(result.get("ok"))
            self.assertEqual(result["reason"], reason)
            self.assertIsNone(supervisor._child)


class LivingProjectionTest(unittest.TestCase):
    """``living_view`` must never upgrade recorded history into "doing this now"."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="usable-living-"))
        self._original = living.live_day.status
        self.report = {
            "ok": True, "status": "READY", "exit_code": 0,
            "running_recorded": True, "pid": 4321, "pid_alive": True,
            "lease": {"status": "HELD"}, "reason": None,
            "summary": {
                "segment": {"ended_at": "2026-10-23T10:00:00+00:00"},
                "where": {"status": "READY", "place_name": "书房",
                          "place_id": "place:study", "unknowns": []},
                "doing": {"status_word": "ACTED", "activity_ref": "actv:one",
                          "decision": "START", "outcome": "ACTED",
                          "pause": {"paused": False}},
                "stop_point": {"status": "READY", "title": "一篇旧作",
                               "artifact_id": "artifact:old",
                               "head_version_id": "av:1", "resumable": True},
                "next": {"check_window_at": "2026-10-23T11:00:00+00:00"},
                "changed": {"actions": [{"action_id": "action:1",
                                         "status": "SUCCEEDED"}],
                            "last_wake": {"status": "ACTED", "at": "2026-10-23T09:58:00+00:00"},
                            "budget": {"run_window_calls": 13}},
                "categories": {},
            },
        }
        living.live_day.status = lambda root, **kwargs: dict(self.report)
        self.addCleanup(setattr, living.live_day, "status", self._original)

    def test_running_resident_with_an_acted_wake_is_active(self):
        view = living.living_view(self.root)
        self.assertEqual(view["phase"]["key"], "active")
        self.assertIn("书房", view["sentence"])
        self.assertTrue(view["read_only"])
        self.assertFalse(view["model_called"])
        self.assertEqual(view["services_started"], 0)
        self.assertEqual(view["work"]["title"], "一篇旧作")
        self.assertTrue(view["memory"]["binding_status"] in (None, ""))

    def test_stale_record_is_never_reported_as_running(self):
        report = dict(self.report)
        report["pid_alive"] = False
        living.live_day.status = lambda root, **kwargs: dict(report)
        view = living.living_view(self.root)
        self.assertEqual(view["phase"]["key"], "unknown")
        self.assertEqual(view["phase"]["basis"], "STALE_RECORD")
        self.assertNotIn("正在", view["sentence"])

    def test_stopped_record_does_not_claim_current_activity(self):
        report = dict(self.report)
        report["running_recorded"] = False
        report["pid_alive"] = None
        living.live_day.status = lambda root, **kwargs: dict(report)
        view = living.living_view(self.root)
        self.assertEqual(view["phase"]["key"], "stopped")
        self.assertIn("没有在运行", view["sentence"])
        self.assertNotIn("正在", view["sentence"])

    def test_blocked_and_paused_wakes_are_distinguished(self):
        for word, key in (("BLOCKED", "blocked"), ("PAUSED", "paused"),
                          ("NOT_DUE", "quiet")):
            report = dict(self.report)
            report["summary"] = dict(self.report["summary"])
            report["summary"]["doing"] = dict(self.report["summary"]["doing"],
                                              status_word=word)
            living.live_day.status = lambda root, **kwargs: dict(report)
            view = living.living_view(self.root)
            self.assertEqual(view["phase"]["key"], key, word)

    def test_missing_where_is_reported_unknown_not_invented(self):
        report = dict(self.report)
        report["summary"] = dict(self.report["summary"])
        report["summary"]["where"] = {"status": "UNKNOWN", "unknowns": ["NO_LIVE_OWNER_READ"]}
        living.live_day.status = lambda root, **kwargs: dict(report)
        view = living.living_view(self.root)
        self.assertEqual(view["phase"]["key"], "active")
        self.assertIn("读不到她现在的位置", view["sentence"])
        self.assertNotIn("书房", view["sentence"])

    # -- the canonical foreground activity ------------------------------------------------

    def _state_root(self, payload) -> Path:
        state_root = self.root / "life-state"
        state_root.mkdir(parents=True, exist_ok=True)
        (state_root / "canonical_life_state.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return state_root

    def _stopped_report(self, state_root: Path) -> dict:
        report = dict(self.report)
        report["running_recorded"] = False
        report["pid_alive"] = None
        report["state_root"] = str(state_root)
        return report

    def test_paused_foreground_is_reported_with_title_and_its_own_time_when_stopped(self):
        state_root = self._state_root({
            "revision": 15, "foreground_activity_ref": "actv_91acbd742551228b",
            "activities": {"actv_91acbd742551228b": {
                "activity_id": "actv_91acbd742551228b",
                "activity_kind": "GO_TO_OBSERVED_PLACE", "status": "PAUSED",
                "title": "去她观察到的地方",
                "paused_at": "2026-10-22T23:49:34+08:00",
                "started_at": "2026-10-22T22:11:02+08:00"}},
        })
        living.live_day.status = lambda root, **kwargs: self._stopped_report(state_root)
        view = living.living_view(self.root)
        self.assertEqual(view["phase"]["key"], "paused")
        self.assertEqual(view["phase"]["basis"], "ACTIVITY_PAUSED_NOT_RUNNING")
        self.assertEqual(view["foreground"]["title"], "去她观察到的地方")
        self.assertEqual(view["foreground"]["activity_status"], "PAUSED")
        self.assertEqual(view["foreground"]["paused_at"], "2026-10-22T23:49:34+08:00")
        self.assertIn("去她观察到的地方", view["sentence"])
        self.assertIn("2026-10-22T23:49:34+08:00", view["sentence"])
        self.assertIn("没有在运行", view["sentence"])
        # The observation time is when this page read, never when the activity happened.
        self.assertNotIn(view["observed_at"], view["sentence"])

    def test_an_unreadable_authority_never_claims_there_is_no_activity(self):
        state_root = self.root / "life-state"
        state_root.mkdir(parents=True, exist_ok=True)
        (state_root / "canonical_life_state.json").write_text("{not json", encoding="utf-8")
        living.live_day.status = lambda root, **kwargs: self._stopped_report(state_root)
        view = living.living_view(self.root)
        self.assertEqual(view["foreground"]["status"], "UNVERIFIED")
        self.assertEqual(view["foreground"]["reason_code"], "CANONICAL_STATE_CORRUPT")
        self.assertIn("不能据此说没有活动", view["sentence"])

    def test_no_activity_authority_keeps_the_old_stopped_statement(self):
        report = dict(self.report)
        report["running_recorded"] = False
        report["pid_alive"] = None
        living.live_day.status = lambda root, **kwargs: dict(report)
        view = living.living_view(self.root)
        self.assertEqual(view["foreground"]["reason_code"], "STATE_ROOT_UNKNOWN")
        self.assertEqual(view["phase"]["key"], "stopped")
        self.assertIn("没有在运行", view["sentence"])


class MemoryProjectionTest(unittest.TestCase):
    """A healthy binding must never be displayed as UNBOUND because of a stale field."""

    def setUp(self) -> None:
        self.env = {"NYA02_MEMORY_DB": "/tmp/evidence.db",
                    "NYA02_MEMORY_CONTROLS": "/tmp/controls.db",
                    "NYA02_MEMORY_SUBJECT": "chiyo"}
        self._real_preflight = living.phone_life.check_memory
        self._real_service = living.phone_service.PhoneService

    def tearDown(self) -> None:
        living.phone_life.check_memory = self._real_preflight
        living.phone_service.PhoneService = self._real_service

    def _service(self, payload: dict) -> object:
        class _Stub:
            instance = {"root": "/tmp/phone"}

            def memory(self_inner) -> dict:
                return dict(payload)

        return _Stub()

    def _preflight(self, payload: dict) -> None:
        living.phone_life.check_memory = lambda environment, **kw: dict(payload)

    def test_a_ready_preflight_is_not_overridden_by_a_stale_unbound_field(self):
        self._preflight({"status": "READY", "reason": None, "recall_ready": True,
                         "life_outcomes": 0, "owner": "chiyo", "database": "/secret/path.db"})
        block = living._memory_block(
            self._service({"ok": True, "data": {"binding_status": "UNBOUND",
                                                "recall_ready": True,
                                                "owner": "chiyo"}}), self.env)
        self.assertEqual(block["binding_status"], "READY")
        self.assertTrue(block["recall_ready"])
        self.assertEqual(block["recall_state"], "empty")
        self.assertEqual(block["experience_count"], 0)
        self.assertEqual(block["source"], "life_preflight")
        # No path and no owner leave this layer.
        self.assertNotIn("owner", block)
        self.assertNotIn("database", block)
        self.assertNotIn("/secret/path.db", json.dumps(block))

    def test_an_explicitly_blocked_binding_is_fail_closed(self):
        self._preflight({"status": "BLOCKED", "reason": "MEMORY_DB_MISSING",
                         "recall_ready": False})
        block = living._memory_block(
            self._service({"ok": True, "data": {"binding_status": "READY",
                                                "recall_ready": True}}), self.env)
        self.assertEqual(block["binding_status"], "BLOCKED")
        self.assertFalse(block["recall_ready"])
        self.assertEqual(block["recall_state"], "not_ready")
        self.assertEqual(block["reason"], "MEMORY_DB_MISSING")

    def test_unbound_is_reported_as_optional_without_claiming_recall(self):
        self._preflight({"status": "UNBOUND", "reason": "MEMORY_OPTIONAL_UNBOUND",
                         "recall_ready": False})
        block = living._memory_block(
            self._service({"ok": False, "data": {"binding_status": "UNBOUND",
                                                 "recall_ready": False}}), self.env)
        self.assertEqual(block["binding_status"], "UNBOUND")
        self.assertFalse(block["recall_ready"])
        self.assertEqual(block["recall_state"], "not_ready")

    def test_no_phone_service_makes_no_binding_claim(self):
        block = living._memory_block(None, self.env)
        self.assertIsNone(block["binding_status"])
        self.assertEqual(block["reason"], "NO_PHONE_SERVICE")


if __name__ == "__main__":
    unittest.main(verbosity=2)

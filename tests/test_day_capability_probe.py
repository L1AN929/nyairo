"""v0.4 日常能力验收探针（``scripts/day_capability_probe.py``）的离线测试。

探针不是产品入口：``prepare`` 只调用既有 ``demo.prepare_demo``，``run`` 只调用既有
``live_day.run_segment``。这里不创建真实 Supply/World owner、不调用模型，只验证：

* 边界（<=600 秒、poll >=2 秒、每段新增请求 <=6）在调用既有入口之前就被拒绝；
* 没有模型配置时一个请求都不开始；
* 模型元数据不含密钥，报告固定 24h 为 ``NOT_RUN_BY_USER_SCOPE``；
* ``status`` 是纯读；
* 报告只从既有入口的结果重建，不复制业务逻辑。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import day_capability_probe as probe  # noqa: E402

PRIVATE_KEY = "sk-probe-SHOULD-NOT-APPEAR-7c2d"


def _env(**overrides):
    env = {"NYA02_MODEL_BASE_URL": "http://127.0.0.1:3425/v1",
           "NYA02_MODEL_NAME": "workbuddy-wg/cn:deepseek-v4.1-flash",
           "NYA02_MODEL_API_KEY": PRIVATE_KEY,
           "NYA02_MODEL_PROVIDER": "custom"}
    env.update(overrides)
    return env


class BoundsTest(unittest.TestCase):
    def test_the_default_segment_is_finite_and_cheap(self):
        bounds, error = probe.validate_run_bounds()
        self.assertIsNone(error)
        self.assertEqual(bounds, {"duration_seconds": 180, "poll_seconds": 2,
                                  "max_new_requests": 6})

    def test_a_segment_longer_than_ten_minutes_is_refused(self):
        _bounds, error = probe.validate_run_bounds(duration_seconds=601)
        self.assertEqual(error["reason_code"], "DURATION_OUT_OF_RANGE")
        _bounds, error = probe.validate_run_bounds(duration_seconds=0)
        self.assertEqual(error["reason_code"], "DURATION_OUT_OF_RANGE")

    def test_a_poll_below_two_seconds_is_refused(self):
        _bounds, error = probe.validate_run_bounds(poll_seconds=1)
        self.assertEqual(error["reason_code"], "POLL_OUT_OF_RANGE")

    def test_more_than_six_new_requests_is_refused(self):
        _bounds, error = probe.validate_run_bounds(max_new_requests=7)
        self.assertEqual(error["reason_code"], "REQUEST_CAP_OUT_OF_RANGE")
        _bounds, error = probe.validate_run_bounds(max_new_requests=0)
        self.assertEqual(error["reason_code"], "REQUEST_CAP_OUT_OF_RANGE")


class ModelMetaTest(unittest.TestCase):
    def test_the_key_is_never_in_the_meta(self):
        meta = probe.model_meta(_env())
        self.assertTrue(meta["configured"])
        self.assertTrue(meta["api_key_present"])
        self.assertFalse(meta["api_key_recorded"])
        self.assertEqual(meta["endpoint"], "http://127.0.0.1:3425")
        self.assertNotIn(PRIVATE_KEY, json.dumps(meta, ensure_ascii=False))
        self.assertNotIn("api_key", meta)

    def test_missing_key_means_not_configured(self):
        meta = probe.model_meta({"NYA02_MODEL_BASE_URL": "http://x/v1",
                                 "NYA02_MODEL_NAME": "m"})
        self.assertFalse(meta["configured"])


class PrepareRefusalTest(unittest.TestCase):
    def test_an_existing_root_is_never_touched(self):
        root = Path(tempfile.mkdtemp(prefix="v04-probe-existing-"))
        called = []

        def must_not_run(*args, **kwargs):
            called.append(True)
            raise AssertionError("prepare must not run for an existing root")

        report = probe.prepare_instance(root, prepare_fn=must_not_run)
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "ROOT_ALREADY_EXISTS")
        self.assertEqual(called, [])
        # It does not write a probe report into an instance it refused to touch.
        self.assertFalse((root / probe.PROBE_DIRNAME).exists())


class RunRefusalTest(unittest.TestCase):
    def test_an_out_of_range_bound_is_refused_before_the_entry_is_called(self):
        root = Path(tempfile.mkdtemp(prefix="v04-probe-bounds-"))
        called = []
        report = probe.run_probe(root, environment=_env(), duration_seconds=601,
                                 run_fn=lambda *a, **k: called.append(True))
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "DURATION_OUT_OF_RANGE")
        self.assertEqual(called, [])

    def test_no_model_configuration_starts_nothing(self):
        root = Path(tempfile.mkdtemp(prefix="v04-probe-nomodel-"))
        called = []
        report = probe.run_probe(root, environment={},
                                 run_fn=lambda *a, **k: called.append(True))
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "NO_MODEL_PROVIDER")
        self.assertEqual(called, [])
        self.assertFalse((root / probe.PROBE_DIRNAME).exists())


class RunReportTest(unittest.TestCase):
    """Reuse a scripted segment result: the probe only rebuilds its own report."""

    def _segment(self):
        return {
            "ok": True, "status": "READY", "exit_code": 0, "reason": None, "detail": None,
            "model_calls": 2, "model_calls_cumulative": 2,
            "bounds": {"duration_seconds": 180, "poll_seconds": 2,
                       "max_new_requests": 6},
            "segment_started_at": "2026-10-22T00:00:00+00:00",
            "segment_ended_at": "2026-10-22T00:03:00+00:00",
            "processes_started": 0, "services": {"status": "READY"},
            "services_stopped": {"stopped": True}, "lease": {"status": "RELEASED"},
            "segment_ownership": {"new_run_window_opened": False, "ledger_reset": False},
            "life": {"reason": None},
            "summary": {
                "segment": {"ticks_recorded": 3},
                "changed": {
                    "model_requests": 2, "model_requests_source": "PERSISTED_REQUEST_LEDGER",
                    "cognition_calls": 1, "content_generation_calls": 1,
                    "budget": {"run_id": "run:probe", "utc_day_calls": 2,
                               "run_window_calls": 2},
                    "wakes": {"buckets": {"quiet": 1, "idle": 1, "blocked": 0,
                                          "unknown": 0}},
                    "ticks": [{"tick": 1}], "owner_versions": 1,
                    "owner_version_refs": ["v:1"], "owner_version_unknown": 0,
                    "actions": [], "segment_request_cap": {"applied": True},
                },
                "doing": {"pause": {"state": "RUNNING"}, "activity_ref": "act:1",
                          "decision": "CONTINUE", "outcome": "ACTED"},
                "categories": {"fault": 0, "quiet": 1},
                "where": {"status": "READY", "place_id": "world:home"},
                "hands": {"status": "READY", "holding": []},
                "stop_point": {"status": "READY", "artifact_id": "artifact:work"},
                "next": {"status": "READY", "check_window_at": "2026-10-22T00:06:00+00:00"},
            },
        }

    def test_the_report_rebuilds_known_facts_and_never_claims_24h(self):
        root = Path(tempfile.mkdtemp(prefix="v04-probe-run-"))
        before = {"state": "ok", "run_id": "run:probe", "utc_day_calls": 3,
                  "run_window_calls": 3}
        after = {"state": "ok", "run_id": "run:probe", "utc_day_calls": 5,
                 "run_window_calls": 5}
        with mock.patch.object(probe, "_binding",
                               return_value=(None, {"instance": True}, {}, root)), \
                mock.patch.object(probe, "_ledger_view", side_effect=[before, after]), \
                mock.patch.object(probe, "_load_modules", return_value=(None, None, None, None)):
            report = probe.run_probe(root, environment=_env(),
                                     run_fn=lambda *a, **k: self._segment())

        self.assertTrue(report["ok"])
        self.assertEqual(report["requests"]["delta"],
                         {"utc_day_calls": 2, "run_window_calls": 2})
        self.assertTrue(report["requests"]["same_run_window"])
        self.assertEqual(report["model"]["calls_this_segment"], 2)
        self.assertEqual(report["doing"]["decision"], "CONTINUE")
        self.assertEqual(report["changed"]["owner_version_refs"], ["v:1"])
        self.assertEqual(report["twenty_four_hours"]["status"], "NOT_RUN_BY_USER_SCOPE")
        self.assertFalse(report["twenty_four_hours"]["simulated"])
        self.assertNotIn(PRIVATE_KEY, json.dumps(report, ensure_ascii=False))
        written = Path(report["probe_report"])
        self.assertTrue(written.exists())
        self.assertNotIn(PRIVATE_KEY, written.read_text(encoding="utf-8"))


class ExitCodeTest(unittest.TestCase):
    """The success code ``0`` is a real value, not an empty one.

    ``int(value or EXIT_REFUSED)`` used to rewrite a successful owner answer to ``1``; these
    tests pin the four cases (0 / non-zero / missing / None) at the report, command and CLI
    boundary.  ``ok: true`` alone never invents a success code.
    """

    def _segment(self, **overrides):
        segment = {
            "ok": True, "status": "READY", "exit_code": 0, "reason": None, "detail": None,
            "model_calls": 0, "model_calls_cumulative": 0,
            "bounds": {"duration_seconds": 180, "poll_seconds": 2, "max_new_requests": 6},
            "segment_started_at": "2026-10-22T00:00:00+00:00",
            "segment_ended_at": "2026-10-22T00:03:00+00:00",
            "processes_started": 0, "services": {"status": "READY"},
            "services_stopped": {"stopped": True}, "lease": {"status": "RELEASED"},
            "segment_ownership": {"new_run_window_opened": False, "ledger_reset": False},
            "life": {"reason": None},
            "summary": {
                "segment": {"ticks_recorded": 1},
                "changed": {"model_requests": 0, "budget": {"run_id": "run:probe"}},
                "doing": {}, "categories": {}, "where": {}, "hands": {}, "stop_point": {},
                "next": {},
            },
        }
        segment.update(overrides)
        return segment

    def _run_report(self, segment):
        root = Path(tempfile.mkdtemp(prefix="v04-probe-exit-"))
        before = {"state": "ok", "run_id": "run:probe", "utc_day_calls": 0,
                  "run_window_calls": 0}
        after = dict(before)
        with mock.patch.object(probe, "_binding",
                               return_value=(None, {"instance": True}, {}, root)), \
                mock.patch.object(probe, "_ledger_view", side_effect=[before, after]), \
                mock.patch.object(probe, "_load_modules",
                                  return_value=(None, None, None, None)):
            return probe.run_probe(root, environment=_env(),
                                   run_fn=lambda *a, **k: segment)

    def test_a_successful_segment_keeps_exit_zero(self):
        report = self._run_report(self._segment(exit_code=0))
        self.assertTrue(report["ok"])
        self.assertEqual(report["exit_code"], 0)

    def test_a_nonzero_exit_code_is_preserved(self):
        report = self._run_report(self._segment(ok=False, status="BLOCKED", exit_code=3))
        self.assertEqual(report["exit_code"], 3)

    def test_a_missing_exit_code_fails_closed(self):
        segment = self._segment()
        del segment["exit_code"]
        report = self._run_report(segment)
        self.assertEqual(report["exit_code"], probe.EXIT_REFUSED)

    def test_a_none_exit_code_fails_closed_even_when_ok_is_true(self):
        report = self._run_report(self._segment(exit_code=None))
        self.assertTrue(report["ok"])
        self.assertEqual(report["exit_code"], probe.EXIT_REFUSED)

    def test_status_probe_keeps_the_owner_success_zero(self):
        root = Path(tempfile.mkdtemp(prefix="v04-probe-exit-status-"))
        fake = {"ok": True, "status": "READY", "exit_code": 0,
                "summary": {"changed": {"budget": {"run_id": "run:probe"}}}}
        with mock.patch.object(probe, "_load_modules",
                               return_value=(None, None, None, None)):
            report = probe.status_probe(root, environment=_env(),
                                        status_fn=lambda *a, **k: fake)
        self.assertEqual(report["exit_code"], 0)
        fake.pop("exit_code")
        with mock.patch.object(probe, "_load_modules",
                               return_value=(None, None, None, None)):
            report = probe.status_probe(root, environment=_env(),
                                        status_fn=lambda *a, **k: fake)
        self.assertEqual(report["exit_code"], probe.EXIT_REFUSED)

    def test_the_command_returns_the_reports_own_code(self):
        args = argparse.Namespace(root="x", duration_seconds=180, poll_seconds=2,
                                  max_new_requests=6)
        for payload, expected in (({"exit_code": 0}, 0), ({"exit_code": 3}, 3),
                                  ({}, probe.EXIT_REFUSED),
                                  ({"ok": True, "exit_code": None}, probe.EXIT_REFUSED)):
            with mock.patch.object(probe, "run_probe", return_value=payload), \
                    mock.patch.object(probe, "_emit"):
                self.assertEqual(probe.cmd_run(args), expected, payload)
        for payload, expected in (({"exit_code": 0}, 0),
                                  ({"ok": True}, probe.EXIT_REFUSED)):
            with mock.patch.object(probe, "status_probe", return_value=payload), \
                    mock.patch.object(probe, "_emit"):
                self.assertEqual(probe.cmd_status(argparse.Namespace(root="x")), expected)

    def test_the_real_cli_returns_zero_for_a_successful_read(self):
        with mock.patch.object(probe, "status_probe", return_value={"exit_code": 0}), \
                mock.patch.object(probe, "_emit"):
            self.assertEqual(probe.main(["status", "--root", "x"]), 0)

    def test_the_real_cli_still_returns_one_for_a_missing_instance(self):
        missing = Path(tempfile.mkdtemp(prefix="v04-probe-exit-missing-")) / "absent"
        with mock.patch.object(probe, "_emit"):
            self.assertEqual(probe.main(["run", "--root", str(missing)]),
                             probe.EXIT_REFUSED)


class StatusTest(unittest.TestCase):
    def test_status_is_a_pure_read(self):
        root = Path(tempfile.mkdtemp(prefix="v04-probe-status-"))
        fake = {"ok": True, "status": "READY", "exit_code": 0,
                "summary": {"changed": {"budget": {"run_id": "run:probe"}},
                            "where": {"status": "READY"}},
                "state_root": str(root), "running_recorded": False,
                "lease": {"status": "FREE"}, "model_calls_detail": {}}
        with mock.patch.object(probe, "_load_modules", return_value=(None, None, None, None)):
            report = probe.status_probe(root, environment=_env(),
                                        status_fn=lambda *a, **k: fake)
        self.assertTrue(report["read_only"])
        self.assertFalse(report["writes"])
        self.assertFalse(report["model_called"])
        self.assertEqual(report["twenty_four_hours"]["status"], "NOT_RUN_BY_USER_SCOPE")
        self.assertNotIn(PRIVATE_KEY, json.dumps(report, ensure_ascii=False))
        # A pure read creates nothing.
        self.assertFalse((root / probe.PROBE_DIRNAME).exists())


if __name__ == "__main__":
    unittest.main()

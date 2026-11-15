"""v0.7 live-d 极少聚合离线用例；只碰 scratch，不 prepare/run 正式根。

覆盖三件事：
1. D 身份是封闭的：run/domain、1800 秒 rate 1、history 568、cap 20、UTC 300、总限 1000、
   5 分钟 check interval，且不能指向临时目录之外的根。
2. 未 prepare 时 audit 保持 UNKNOWN（不猜 0），reading cap 也明确是 NOT_PREPARED 而不是目标值。
3. 一次性标记先成后拒、且运行前检查在 latest_start 后拒绝。
"""
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO, REPO / "scripts", REPO / "components",
              REPO / "components/life/scripts", REPO / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import v07_live_d as d07  # noqa: E402


def scratch() -> Path:
    return Path(tempfile.mkdtemp(prefix="v07-live-d-case-"))


class V07LiveDTests(unittest.TestCase):
    def test_01_identity_is_closed_and_one_to_one(self):
        profile = d07.PROFILE
        self.assertEqual(profile["run_id"], "run:v07-live:20261109-d")
        self.assertEqual(profile["domain_id"], "v07:live:20261109-d")
        self.assertEqual((profile["real_seconds"], profile["logical_seconds"]), (1800, 1800))
        self.assertEqual(profile["rate"], 1.0)
        self.assertEqual(profile["logical_anchor"], "2026-11-09T11:00:00+09:00")
        self.assertEqual(profile["latest_start"], "2026-11-09T23:15:00+08:00")
        self.assertEqual(profile["request_deadline"], "2026-11-09T23:50:00+08:00")
        self.assertEqual(profile["historical_requests"], 568)
        self.assertEqual(profile["new_cap"], 20)
        self.assertEqual(profile["utc_day_cap"], 300)
        self.assertEqual(profile["cumulative_total"], 1000)
        self.assertEqual(profile["worst_case_cumulative"], 588)
        self.assertEqual(profile["remaining_after_worst_case"], 412)
        self.assertEqual(profile["check_interval_minutes"], 5)
        self.assertFalse(profile["ambient_events"])
        self.assertTrue(profile["room_living"])
        refused = d07.configure(Path("/tmp"), Path("/tmp/x/authority.db"))
        self.assertEqual(refused["reason_code"], "OFFLINE_PATH_REFUSED")

    def test_02_unprepared_audit_keeps_unknown_not_zero(self):
        root = scratch()
        report = d07.audit(root)
        self.assertEqual(report["reason_code"], "NOT_PREPARED")
        self.assertFalse(report["model_called"])
        self.assertEqual(report["check_interval"]["target_minutes"], 5)
        self.assertEqual(report["check_interval"]["persisted_minutes"], "UNKNOWN")
        self.assertEqual(report["check_interval"]["runner_minutes"], "UNKNOWN")
        self.assertEqual(report["reading_cap"]["max_output_tokens"], "UNKNOWN")
        self.assertEqual(report["reading_cap"]["source"], "NOT_PREPARED")
        self.assertFalse(report["reading_cap"]["measured_by_send"])
        self.assertEqual(sorted(path.name for path in root.iterdir()), [])

        state = root / "state"
        state.mkdir()
        unknown = d07.base._ledger_view_pure(state, d07.IDENTITY.run_id, {})
        self.assertEqual(unknown["state"], "absent")
        self.assertEqual(unknown["run_window_calls"], "UNKNOWN")
        zero = d07.base._ledger_view_pure(
            state, d07.IDENTITY.run_id, {"reserved_calls": 0, "used_high_water": 0})
        self.assertEqual((zero["run_window_calls"], zero["utc_day_calls"]), (0, 0))

    def test_03_one_shot_and_precheck_stop(self):
        refused = d07.run(execute_approved=False)
        self.assertEqual(refused["reason_code"], "EXECUTE_FLAG_MISSING")
        identity = replace(d07.IDENTITY, root=scratch() / "one-shot",
                           authority=scratch() / "authority" / "authority.db")
        (identity.root / "run").mkdir(parents=True, exist_ok=True)
        self.assertTrue(d07._consume_once(identity))
        self.assertFalse(d07._consume_once(identity))
        self.assertTrue((identity.root / "run" / d07.CONSUME_FILENAME).exists())

        base_report = {"ok": True, "prepared": True,
                       "start": {"started": False, "consumed": False},
                       "failure": {"locked": False}, "window": {"present": False}}
        early = d07.IDENTITY.latest_start - timedelta(minutes=1)
        self.assertIsNone(d07.base._run_precheck(d07.IDENTITY, base_report, early))
        self.assertLessEqual(d07.IDENTITY.latest_start + timedelta(seconds=1800),
                             d07.IDENTITY.deadline)
        late = d07.IDENTITY.latest_start + timedelta(seconds=1)
        self.assertEqual(d07.base._run_precheck(d07.IDENTITY, base_report, late),
                         "LATEST_START_PASSED")


if __name__ == "__main__":
    unittest.main()

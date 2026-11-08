#!/usr/bin/env python3
"""G2 b 批 profile 的离线验收（只核本次新增接线）。

本模块只覆盖 b profile 与现有封闭入口的接缝，不重跑 a 的 36 例、不重跑旧 suite / 全库：

1. a 仍是缺省：旧 CLI 不带 ``--batch`` 仍走 a 身份；a 的常量与 profile 未被改动。
2. b 身份：新根/run/domain、history 186、8+40+4=52、采集 9000 真秒、纯读上限 151、
   观察 60 秒、整批 11400、固定绝对截止 02:00:00 与启动线 22:30:00。
3. b CLI fixture prepare/audit/run：prepared/start=null/window absent/ledger=0，audit 纯读，
   新增参数持久可读；虚拟短窗（NO_ACTION→QUIET）不真等 2.5 小时。
4. latest_start 前后机械注入（不改系统时钟）：线前可跑、线后拒绝且不写 window/started。
5. 第二次 run 拒绝；跨 a/b 身份拒绝；夹具根只接 tempdir，b 正式根与仓库正式根都拒。
6. 30 分钟检查间隔/静默规则未被覆写：只改观察窗口与必要的新批参数。

Decision/内容仍是脚本替身，真实模型、真实 provider 与自然等待不在本模块。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"), str(REPO_ROOT / "tests"),
               str(REPO_ROOT / "components"), str(REPO_ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g2_next_live as g2  # noqa: E402
import history_choice_natural_v2 as v2  # noqa: E402


def _fresh_root(prefix: str) -> Path:
    """夹具根：tempdir 下的新 scratch（保留数据，不 rmtree）。"""

    return Path(tempfile.mkdtemp(prefix=prefix)) / "batch"


def _tree_snapshot(root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for directory, _dirnames, filenames in os.walk(str(root)):
        for name in filenames:
            path = Path(directory) / name
            try:
                stat = path.stat()
                out[str(path)] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            except OSError:
                out[str(path)] = {"exists": True}
    return out


#: b 正式 profile 的绝对截止（2026-11-01T02:00）已经过期。夹具只把启动线和
#: 截止一起推到未来；``g2.B_PROFILE`` / ``g2.B_DEADLINE`` 常量的原值不动。
_FIXTURE_DEADLINE = datetime.now(timezone.utc) + timedelta(days=30)


def _future(**overrides: Any) -> g2.LiveProfile:
    """把启动线机械推到未来（不改系统时钟），用于「线前可跑」的正控。"""

    values = {"latest_start": datetime.now(timezone.utc) + timedelta(hours=1),
              "deadline": _FIXTURE_DEADLINE}
    values.update(overrides)
    return g2.replace(g2.B_PROFILE, **values)


def _run_short(root: Path, profile: g2.LiveProfile) -> dict[str, Any]:
    virtual = v2.VirtualTime()
    return g2.run(
        profile=profile, execute_approved=True, offline_fixtures=True, root=root,
        clock=virtual.monotonic, sleep=virtual.sleep,
        total_window_seconds=300, acquisition_window_seconds=60, max_observations=2)


# ---------------------------------------------------------------------------------
# 1. a 仍是缺省，a 的常量未被改动
# ---------------------------------------------------------------------------------

class ADefaultUnchangedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a_root = _fresh_root("g2b-a-default-")
        with contextlib.redirect_stdout(io.StringIO()):
            cls.code = g2.main(["prepare", "--offline-fixtures", "--root", str(cls.a_root)])

    def test_a_profile_constants_are_untouched(self):
        profile = g2.FORMAL_PROFILE
        self.assertEqual(profile.batch_key, "a")
        self.assertIsNone(profile.latest_start)
        self.assertEqual(str(profile.batch_root), "/root/nyairo-g2-natural-20261031-a")
        self.assertEqual(profile.run_id, "run:g2-natural-20261031-a")
        self.assertEqual(profile.expected_history, 185)
        self.assertEqual(profile.acquisition_window_seconds, 1800)
        self.assertEqual(profile.max_observations, 31)
        self.assertEqual(profile.total_window_seconds, 5400)
        self.assertEqual(profile.deadline.isoformat(), "2026-11-01T01:00:00+08:00")
        self.assertIs(g2.formal_profile("a"), g2.FORMAL_PROFILE)
        resolved, refusal = g2._resolve_profile(offline_fixtures=False, root=None)
        self.assertIsNone(refusal)
        self.assertIs(resolved, g2.FORMAL_PROFILE)

    def test_cli_without_batch_still_uses_a_identity(self):
        self.assertEqual(self.code, g2.EXIT_OK)
        prereg = g2._read_json(self.a_root / "experiment" / "g2-natural.json")
        self.assertEqual(prereg["run_id"], g2.RUN_ID)
        self.assertEqual(prereg["domain_id"], g2.DOMAIN_ID)
        self.assertEqual(prereg["history"]["used"], 185)
        self.assertIsNone(prereg.get("latest_start"))
        audit = g2.audit(offline_fixtures=True, root=self.a_root)
        self.assertTrue(audit["ok"], audit)
        self.assertEqual(audit["history"], 185)
        self.assertIsNone(audit["latest_start"])
        self.assertIsNone(audit["caps"]["latest_start"])


# ---------------------------------------------------------------------------------
# 2. b 身份、预算、时间与未改的控制器参数
# ---------------------------------------------------------------------------------

class BIdentityTest(unittest.TestCase):
    def test_b_profile_identity_caps_and_times(self):
        profile = g2.B_PROFILE
        self.assertEqual(profile.batch_key, "b")
        self.assertEqual(str(profile.batch_root), "/root/nyairo-g2-natural-20261031-b")
        self.assertEqual(str(profile.main_root), "/root/nyairo-g2-natural-20261031-b/run/main")
        self.assertEqual(str(profile.authority_db),
                         "/root/nyairo-g2-natural-20261031-b/run/memory-authority/authority.db")
        self.assertEqual(str(profile.branches_root),
                         "/root/nyairo-g2-natural-20261031-b/run/branches")
        self.assertEqual(profile.run_id, "run:g2-natural-20261031-b")
        self.assertEqual(profile.domain_id, "g2:natural:20261031-b")
        self.assertEqual(profile.expected_history, 186)
        self.assertEqual(profile.request_cap, 52)
        self.assertEqual((profile.acquisition_cap, profile.shadow_cap,
                          profile.continuation_cap, profile.diagnostic_cap), (8, 40, 4, 0))
        self.assertEqual(profile.arm_cap, 2)
        self.assertEqual(profile.acquisition_window_seconds, 9000)
        self.assertEqual(profile.observation_interval_seconds, 60)
        self.assertEqual(profile.max_observations, 151)
        self.assertEqual(profile.total_window_seconds, 11400)
        self.assertEqual(profile.deadline.isoformat(), "2026-11-01T02:00:00+08:00")
        self.assertEqual(profile.latest_start.isoformat(), "2026-10-31T22:30:00+08:00")
        self.assertIs(g2.formal_profile("b"), g2.B_PROFILE)
        caps = profile.caps()
        self.assertEqual((caps["acquisition"], caps["shadow"], caps["continuation"]),
                         (8, 40, 4))
        self.assertEqual(caps["expected_history"], 186)
        self.assertEqual(caps["acquisition_window_seconds"], 9000)
        self.assertEqual(caps["max_observations"], 151)
        self.assertEqual(caps["total_window_seconds"], 11400)
        self.assertEqual(caps["latest_start"], "2026-10-31T22:30:00+08:00")
        self.assertEqual(caps["gate"], "off")

    def test_b_keeps_the_same_model_parameters_and_never_echoes_a_key(self):
        params = g2.B_PROFILE.model_params()
        self.assertEqual(params, g2.FORMAL_PROFILE.model_params())
        self.assertEqual(params["base_url"], "https://gw.example.invalid/v1")
        self.assertEqual(params["model"], "cn:deepseek-v4.1-flash")
        self.assertNotIn("api_key", json.dumps(params).lower())

    def test_only_the_observation_range_changes_not_the_controller_rules(self):
        plan = g2._plan_for(g2.B_PROFILE)
        self.assertEqual(plan.window_seconds, 9000)
        self.assertEqual(plan.max_observations, 151)
        self.assertEqual(plan.observation_interval_seconds, 60)
        self.assertEqual(plan.request_cap, 8)
        self.assertEqual(plan.future_shadow_requests, 40)
        self.assertEqual(plan.future_continuation_requests, 4)
        self.assertEqual(plan.future_diagnostic_requests, 0)
        self.assertEqual(plan.arm_request_cap, 2)
        # 30 分钟检查间隔在既有实现里，本批没有覆写它。
        from one_day_life import DEFAULT_CHECK_INTERVAL_MINUTES

        self.assertEqual(DEFAULT_CHECK_INTERVAL_MINUTES, 30)
        self.assertEqual(v2.DEFAULT_PLAN.observation_interval_seconds, 60)

    def test_public_output_keeps_the_start_line_and_drops_private_fields(self):
        blob = json.dumps(g2._public_run({
            "ok": True, "latest_start": "2026-10-31T22:30:00+08:00",
            "prompt": "SECRET_PROMPT", "api_key": "sk-secret",
            "reasoning": "SECRET_REASONING"}), ensure_ascii=False)
        self.assertIn("2026-10-31T22:30:00+08:00", blob)
        for forbidden in ("SECRET_PROMPT", "sk-secret", "SECRET_REASONING"):
            self.assertNotIn(forbidden, blob)


# ---------------------------------------------------------------------------------
# 3. b CLI fixture prepare / audit / run
# ---------------------------------------------------------------------------------

class BCliFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = _fresh_root("g2b-cli-")
        cls.out = io.StringIO()
        with contextlib.redirect_stdout(cls.out):
            cls.prepare_code = g2.main(["prepare", "--offline-fixtures",
                                        "--root", str(cls.root), "--batch", "b"])
            cls.audit_code = g2.main(["audit", "--offline-fixtures",
                                      "--root", str(cls.root), "--batch", "b"])
        cls.audit = g2.audit(offline_fixtures=True, root=cls.root, batch="b")

    def test_prepare_writes_the_b_identity_and_params(self):
        self.assertEqual(self.prepare_code, g2.EXIT_OK)
        prereg = g2._read_json(self.root / "experiment" / "g2-natural.json")
        self.assertEqual(prereg["run_id"], g2.B_RUN_ID)
        self.assertEqual(prereg["domain_id"], g2.B_DOMAIN_ID)
        self.assertEqual(prereg["root"], str(self.root))
        self.assertEqual(prereg["history"], {"used": 186, "total_cap": 300, "new_cap": 52})
        self.assertEqual(prereg["deadline"], "2026-11-01T02:00:00+08:00")
        self.assertEqual(prereg["latest_start"], "2026-10-31T22:30:00+08:00")
        caps = prereg["caps"]
        self.assertEqual((caps["acquisition"], caps["shadow"], caps["continuation"]),
                         (8, 40, 4))
        self.assertEqual(caps["acquisition_window_seconds"], 9000)
        self.assertEqual(caps["max_observations"], 151)
        self.assertEqual(caps["total_window_seconds"], 11400)
        batch = g2._read_json(self.root / "experiment" / "g2_batch.json")
        self.assertEqual(batch["state"], "prepared")
        self.assertIsNone(batch["run"])

    def test_audit_is_pure_read_and_reports_every_new_parameter(self):
        before = _tree_snapshot(self.root)
        again = g2.audit(offline_fixtures=True, root=self.root, batch="b")
        after = _tree_snapshot(self.root)
        self.assertEqual(self.audit_code, g2.EXIT_OK)
        self.assertTrue(again["ok"], again)
        self.assertEqual(before, after, "audit wrote into the experiment root")
        self.assertEqual(again["state"], "prepared")
        self.assertIsNone(again["started_at"])
        self.assertEqual(again["window"], {"present": False})
        self.assertEqual(again["ledger"]["run_window_calls"], 0)
        self.assertEqual(again["history"], 186)
        self.assertEqual(again["deadline"], "2026-11-01T02:00:00+08:00")
        self.assertEqual(again["latest_start"], "2026-10-31T22:30:00+08:00")
        caps = again["caps"]
        self.assertEqual(caps["total"], 52)
        self.assertEqual(caps["acquisition_window_seconds"], 9000)
        self.assertEqual(caps["max_observations"], 151)
        self.assertEqual(caps["total_window_seconds"], 11400)
        self.assertEqual(caps["observation_interval_seconds"], 60)

    def test_expired_start_line_refuses_before_touching_state(self):
        now = datetime.now(timezone.utc)
        expired = g2.replace(g2.B_PROFILE, batch_root=self.root,
                             latest_start=now - timedelta(seconds=1))
        before = _tree_snapshot(self.root)
        result = g2.run(profile=expired, execute_approved=True, offline_fixtures=True,
                        root=self.root)
        after = _tree_snapshot(self.root)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["reason_code"], g2.R_LATEST_START_PASSED)
        self.assertEqual(before, after, "an expired start line must not touch the batch root")
        batch = g2._read_json(self.root / "experiment" / "g2_batch.json")
        self.assertEqual(batch["state"], "prepared")
        self.assertIsNone(batch["run"])

    def test_prepare_again_is_refused(self):
        again = g2.prepare(offline_fixtures=True, root=self.root, batch="b")
        self.assertEqual(again["reason_code"], g2.R_ALREADY_PREPARED)


class BCliRunGateTest(unittest.TestCase):
    """CLI ``run --batch b``：线后拒绝且不写状态；线前走同一个夹具短窗出口。"""

    def test_cli_run_respects_the_start_line(self):
        root = _fresh_root("g2b-cli-run-")
        with contextlib.redirect_stdout(io.StringIO()):
            code = g2.main(["prepare", "--offline-fixtures", "--root", str(root),
                            "--batch", "b"])
        self.assertEqual(code, g2.EXIT_OK)
        now = datetime.now(timezone.utc)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = g2.main(["run", "--execute-approved", "--offline-fixtures",
                            "--root", str(root), "--batch", "b"])
        payload = json.loads(out.getvalue() or "{}")
        window = root / "experiment" / "g2_window.json"
        if now >= g2.B_LATEST_START:
            self.assertEqual(code, g2.EXIT_REFUSED)
            self.assertEqual(payload.get("reason_code"), g2.R_LATEST_START_PASSED)
            self.assertFalse(window.exists())
            self.assertIsNone(g2._read_json(root / "experiment" / "g2_batch.json")["run"])
        else:
            # 线前才会走到更深的入口；夹具短窗应立即 NO_EFFECTIVE_HISTORY。
            self.assertEqual(code, g2.EXIT_OK)
            self.assertEqual(payload.get("result"), g2.RESULT_NO_H)
            self.assertTrue(window.exists())


# ---------------------------------------------------------------------------------
# 4. latest_start 前后机械注入（不改系统时钟）+ 第二次 run 拒绝
# ---------------------------------------------------------------------------------

class StartLineInjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = _fresh_root("g2b-before-line-")
        cls.profile = _future(batch_root=cls.root)
        cls.prepared = g2.prepare(profile=cls.profile, offline_fixtures=True,
                                  root=cls.root, batch="b")
        cls.ran = _run_short(cls.root, cls.profile)

    def test_run_before_the_start_line_follows_the_usual_quiet_exit(self):
        self.assertTrue(self.prepared["ok"], self.prepared)
        self.assertTrue(self.ran["ok"], self.ran)
        self.assertEqual(self.ran["result"], g2.RESULT_NO_H)
        self.assertIsNone(self.ran["acquisition"]["h_ref"])
        self.assertLessEqual(int(self.ran["requests_used"]), 1)
        self.assertTrue((self.root / "experiment" / "g2_window.json").exists())
        batch = g2._read_json(self.root / "experiment" / "g2_batch.json")
        self.assertTrue(batch["run"]["started_at"])

    def test_second_run_is_refused(self):
        second = _run_short(self.root, self.profile)
        self.assertFalse(second["ok"], second)
        self.assertEqual(second["reason_code"], g2.R_ALREADY_STARTED)

    def test_cross_batch_identity_is_refused(self):
        wrong = g2.audit(offline_fixtures=True, root=self.root, batch="a")
        self.assertFalse(wrong["ok"], wrong)
        self.assertEqual(wrong["reason_code"], g2.R_IDENTITY_MISMATCH)
        wrong_run = g2.run(execute_approved=True, offline_fixtures=True, root=self.root,
                           batch="a")
        self.assertEqual(wrong_run["reason_code"], g2.R_IDENTITY_MISMATCH)

    def test_after_the_start_line_nothing_is_written_even_with_a_prepared_root(self):
        fresh = _fresh_root("g2b-after-line-")
        prepared = g2.prepare(profile=_future(batch_root=fresh), offline_fixtures=True,
                              root=fresh, batch="b")
        self.assertTrue(prepared["ok"], prepared)
        expired = g2.replace(g2.B_PROFILE, batch_root=fresh,
                             latest_start=datetime.now(timezone.utc) - timedelta(seconds=1))
        before = _tree_snapshot(fresh)
        result = g2.run(profile=expired, execute_approved=True, offline_fixtures=True,
                        root=fresh)
        after = _tree_snapshot(fresh)
        self.assertEqual(result["reason_code"], g2.R_LATEST_START_PASSED)
        self.assertEqual(before, after)
        self.assertFalse((fresh / "experiment" / "g2_window.json").exists())


# ---------------------------------------------------------------------------------
# 5. 夹具根与正式根
# ---------------------------------------------------------------------------------

class FixtureRootTest(unittest.TestCase):
    def test_the_b_formal_root_can_never_run_with_fixtures(self):
        refused = g2.prepare(offline_fixtures=True, root=g2.B_FORMAL_BATCH_ROOT, batch="b")
        self.assertEqual(refused["reason_code"], g2.R_FIXTURE_ROOT_REFUSED)
        refused_run = g2.run(execute_approved=True, offline_fixtures=True,
                             root=g2.B_FORMAL_BATCH_ROOT, batch="b")
        self.assertEqual(refused_run["reason_code"], g2.R_FIXTURE_ROOT_REFUSED)
        # a 的正式根同样不能夹具运行。
        refused_a = g2.prepare(offline_fixtures=True, root=g2.FORMAL_BATCH_ROOT, batch="b")
        self.assertEqual(refused_a["reason_code"], g2.R_FIXTURE_ROOT_REFUSED)

    def test_a_custom_root_on_the_real_path_is_refused_for_b(self):
        outside = Path(tempfile.mkdtemp(prefix="g2b-outside-")) / "batch"
        refused = g2.prepare(offline_fixtures=False, root=outside, batch="b")
        self.assertEqual(refused["reason_code"], g2.R_FIXTURE_ROOT_REFUSED)

    def test_real_path_keeps_the_fixed_b_root(self):
        resolved, refusal = g2._resolve_profile(offline_fixtures=False, root=None, batch="b")
        self.assertIsNone(refusal)
        self.assertIs(resolved, g2.B_PROFILE)


if __name__ == "__main__":
    unittest.main()

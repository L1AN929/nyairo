"""``scripts/fast_day.py`` 的运行时/生命周期最小回归。

真正的 CLI 子进程冒烟：prepare → status → 零模型 ``--clock-only`` 推进小逻辑
horizon → 同 run 重启 → pause/resume → stop（控制位），并核对双时间字段、
同 domain/run、持久实验上限和自有子进程收尾。

这只是时钟装配与控制的证据，**不是**一次逻辑一天成功，也不冒充她的自主活动；
产品模型调用为 0。所有根都在唯一的 ``/tmp`` 子目录里，跑完保留。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import demo_business_clock as dbc  # noqa: E402
import fast_day  # noqa: E402

LOGICAL = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)


def _root() -> Path:
    return Path(tempfile.mkdtemp(prefix="nyairo-fastday-rt-", dir="/tmp"))


def _cli(*arguments: str, timeout: float = 120.0,
         env: dict[str, str] | None = None) -> tuple[int, dict]:
    environment = dict(os.environ) if env is None else dict(env)
    if env is None:
        # 测试进程自己的环境不该影响 run 身份。
        environment.pop("CHIYO_AUTONOMOUS_RUN_ID", None)
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts" / "fast_day.py"), *arguments],
        capture_output=True, text=True, timeout=timeout, cwd=str(ROOT), env=environment)
    text = result.stdout.strip() or "{}"
    try:
        payload = json.loads(text)
    except ValueError:
        payload = {"_stdout": text, "_stderr": result.stderr[-2000:]}
    return result.returncode, payload


class _Mono:
    def __init__(self, value: float = 500.0) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


class CliClockOnlySmokeTest(unittest.TestCase):
    def test_prepare_status_clock_only_and_stop(self):
        root = _root()
        code, prepared = _cli("prepare", "--clock-only", "--root", str(root), "--run-id", "smoke",
                              "--rate", "24", "--logical-seconds", "60",
                              "--max-new-requests", "82")
        self.assertEqual(code, 0, prepared)
        self.assertTrue(prepared["ok"])
        self.assertEqual(prepared["domain_id"], "fastday:smoke")
        self.assertAlmostEqual(prepared["real_seconds_for_logical_day"], 2.5)

        code, status = _cli("status", "--root", str(root))
        self.assertEqual(code, 0, status)
        self.assertTrue(status["not_real_24h"])
        self.assertEqual(status["mode"], dbc.MODE_COMPRESSED_DEMO)
        self.assertEqual(status["domain_id"], "fastday:smoke")
        self.assertEqual(status["experiment"]["max_new_requests"], 82)
        self.assertEqual(status["experiment"]["hard_config_max"], 96)
        self.assertIn("logical", status)
        self.assertIn("real", status)
        self.assertEqual(status["logical"]["state"], dbc.STATE_FROZEN)

        code, ran = _cli("start", "--root", str(root), "--clock-only",
                         "--poll-seconds", "0.3", "--duration-seconds", "10")
        self.assertEqual(code, 0, ran)
        self.assertTrue(ran["ok"], ran)
        self.assertTrue(ran["ran"]["horizon_reached"])
        self.assertGreaterEqual(ran["ran"]["ticks"], 2)
        # 零模型：没有拉起 resident 子进程，也没有写请求账本。
        self.assertEqual(ran["supervisor"]["child"], {})
        self.assertFalse((root / "state" / "life_request_budget.json").exists())
        self.assertFalse((root / "state" / "resident_summary.json").exists())

        # 时钟推进记录是双时间的，且明确标注“不是她的自主活动”。
        ticks = [json.loads(line) for line in
                 (root / "state" / "fastday" / "clock_ticks.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertGreaterEqual(len(ticks), 2)
        self.assertTrue(ticks[0]["clock_only"])
        self.assertIn("logical_at", ticks[0])
        self.assertIn("real_at", ticks[0])
        self.assertNotEqual(ticks[0]["logical_at"], ticks[0]["real_at"])

        code, final = _cli("status", "--root", str(root))
        self.assertEqual(code, 0, final)
        # 小 horizon 被钳住：60 逻辑秒就是上限，不越界也不 tight loop。
        self.assertEqual(final["logical"]["elapsed_seconds"], 60)
        self.assertEqual(final["logical"]["horizon_remaining_seconds"], 0)
        self.assertEqual(final["run_id"], "smoke")
        self.assertEqual(final["experiment"]["max_new_requests"], 82)
        # 正常到达逻辑 horizon 的 run 已结束：时钟必须落既有 STOPPED，而不是留下 RUNNING 标签。
        self.assertEqual(final["logical"]["state"], dbc.STATE_STOPPED)
        # 已结束的 run 不再重开（既有 start 拒绝语义），不靠残留 RUNNING 让人误以为还能续。
        code, refused = _cli("start", "--clock-only", "--root", str(root),
                             "--poll-seconds", "0.2", "--duration-seconds", "1")
        self.assertEqual(code, 1, refused)
        self.assertEqual(refused["reason_code"], "CLOCK_STOPPED")

    def test_a_short_window_before_horizon_stays_resumable(self):
        root = _root()
        code, _ = _cli("prepare", "--clock-only", "--root", str(root), "--run-id", "short",
                       "--rate", "12", "--logical-seconds", "86400")
        self.assertEqual(code, 0)
        code, ran = _cli("start", "--clock-only", "--root", str(root),
                         "--poll-seconds", "0.3", "--duration-seconds", "1")
        self.assertEqual(code, 0, ran)
        self.assertFalse(ran["ran"]["horizon_reached"], ran)
        code, status = _cli("status", "--root", str(root))
        self.assertEqual(code, 0, status)
        # 未到 horizon 的短窗口保持原有语义：RUNNING 可续，不被终点修复误停。
        self.assertEqual(status["logical"]["state"], dbc.STATE_RUNNING)
        self.assertEqual(status["logical"]["horizon_remaining_seconds"] > 0, True)

    def test_start_before_prepare_is_refused(self):
        code, payload = _cli("start", "--root", str(_root()))
        self.assertEqual(code, 1)
        self.assertEqual(payload["reason_code"], fast_day.REASON_MODE_MISSING)


class ExplicitLimitCliTest(unittest.TestCase):
    """prepare 的显式额度绑定：默认 96/0/82 不变，新总限只在显式调用时生效。"""

    def test_default_prepare_keeps_the_legacy_96_and_82(self):
        root = _root()
        code, prepared = _cli("prepare", "--clock-only", "--root", str(root),
                              "--run-id", "legacy-defaults", "--rate", "24",
                              "--logical-seconds", "30")
        self.assertEqual(code, 0, prepared)
        self.assertEqual(prepared["request_total_limit"], 96)
        self.assertEqual(prepared["historical_requests"], 0)
        self.assertEqual(prepared["max_new_requests"], 82)
        self.assertEqual(prepared["max_cognition_calls_per_day"], 96)
        code, status = _cli("status", "--root", str(root))
        self.assertEqual(code, 0, status)
        self.assertEqual(status["experiment"]["max_new_requests"], 82)
        self.assertEqual(status["experiment"]["hard_config_max"], 96)
        self.assertEqual(status["experiment"]["authorized_total_limit"], 96)
        self.assertEqual(status["request_limits"],
                         {"total_limit": 96, "historical_requests": 0,
                          "max_new_requests": 82, "cognition_calls_per_day": 96})

    def test_out_of_bounds_prepare_is_refused_before_any_root_exists(self):
        cases = [
            ("--request-total-limit", "300", "--historical-requests", "14",
             "--max-new-requests", "287"),
            ("--request-total-limit", "301", "--historical-requests", "0",
             "--max-new-requests", "1"),
            ("--request-total-limit", "-1", "--historical-requests", "0",
             "--max-new-requests", "1"),
            ("--request-total-limit", "300", "--historical-requests", "20",
             "--max-new-requests", "290"),
            ("--request-total-limit", "96", "--historical-requests", "0",
             "--max-new-requests", "96"),
            ("--request-total-limit", "300", "--historical-requests", "14",
             "--max-new-requests", "286", "--max-cognition-calls-per-day", "301"),
        ]
        for index, extra in enumerate(cases):
            root = _root() / "instance"
            code, refused = _cli("prepare", "--clock-only", "--root", str(root),
                                 "--run-id", "limits-%d" % index, "--rate", "24",
                                 "--logical-seconds", "30", *extra)
            self.assertEqual(code, 1, (extra, refused))
            self.assertEqual(refused["reason_code"], fast_day.REASON_REQUEST_LIMIT_INVALID,
                             (extra, refused))
            self.assertFalse(root.exists(), extra)

    def test_explicit_300_14_286_is_accepted_and_reported(self):
        root = _root()
        code, prepared = _cli("prepare", "--clock-only", "--root", str(root),
                              "--run-id", "explicit-b", "--rate", "24",
                              "--logical-seconds", "60", "--request-total-limit", "300",
                              "--historical-requests", "14", "--max-new-requests", "286")
        self.assertEqual(code, 0, prepared)
        code, status = _cli("status", "--root", str(root))
        self.assertEqual(code, 0, status)
        self.assertEqual(status["request_limits"],
                         {"total_limit": 300, "historical_requests": 14,
                          "max_new_requests": 286, "cognition_calls_per_day": 300})
        self.assertEqual(status["experiment"]["max_new_requests"], 286)
        self.assertEqual(status["experiment"]["remaining"], 286)
        self.assertTrue(status["experiment"]["allowed"])


class RestartAndControlTest(unittest.TestCase):
    def test_restart_same_run_keeps_domain_and_persisted_cap(self):
        root = _root()
        # 这个用例只验"同 run 短窗口可重启"：horizon 必须远大于本段真实时长，
        # 否则它到 horizon 后按新终点语义就是已结束的 run（另有专门用例）。
        code, _ = _cli("prepare", "--clock-only", "--root", str(root), "--run-id", "restart-a",
                       "--rate", "24", "--logical-seconds", "86400")
        self.assertEqual(code, 0)
        before = json.loads(dbc.clock_path(root / "state").read_text(encoding="utf-8"))
        cap_before = json.loads(dbc.experiment_path(root / "state").read_text(encoding="utf-8"))
        self.assertEqual(cap_before["max_new_requests"], 82)

        code, _ = _cli("start", "--root", str(root), "--clock-only",
                       "--poll-seconds", "0.2", "--duration-seconds", "8")
        self.assertEqual(code, 0)
        after_first = json.loads(dbc.clock_path(root / "state").read_text(encoding="utf-8"))

        # 同 run 重启：不是新 run，不刷新额度，逻辑时间不倒退。
        code, second = _cli("start", "--root", str(root), "--clock-only",
                            "--poll-seconds", "0.2", "--duration-seconds", "8")
        self.assertEqual(code, 0, second)
        after_second = json.loads(dbc.clock_path(root / "state").read_text(encoding="utf-8"))
        self.assertEqual(after_second["run_id"], before["run_id"])
        self.assertEqual(after_second["domain_id"], before["domain_id"])
        self.assertGreaterEqual(after_second["logical_checkpoint"],
                                after_first["logical_checkpoint"])
        cap_after = json.loads(dbc.experiment_path(root / "state").read_text(encoding="utf-8"))
        self.assertEqual(cap_after["max_new_requests"],
                         cap_before["max_new_requests"])
        self.assertEqual(cap_after["used_high_water"], cap_before["used_high_water"])

    def test_pause_and_resume_write_both_the_clock_and_the_life_admission(self):
        root = _root()
        code, _ = _cli("prepare", "--clock-only", "--root", str(root), "--run-id", "pausable",
                       "--rate", "12")
        self.assertEqual(code, 0)
        code, paused = _cli("pause", "--root", str(root))
        self.assertEqual(code, 0, paused)
        self.assertTrue(paused["paused"])
        control = json.loads((root / "state" / "fastday" / "fastday_control.json")
                             .read_text(encoding="utf-8"))
        self.assertTrue(control["pause_requested"])
        self.assertTrue((root / "state" / "life_pause.json").exists())
        code, resumed = _cli("resume", "--root", str(root))
        self.assertEqual(code, 0, resumed)
        control = json.loads((root / "state" / "fastday" / "fastday_control.json")
                             .read_text(encoding="utf-8"))
        self.assertFalse(control["pause_requested"])

    def test_authority_freezes_on_the_operator_pause_and_resumes(self):
        root = _root()
        dbc.create_clock(root, run_id="p1", rate=12.0)
        mono = _Mono()
        authority = dbc.BusinessClockAuthority(
            root, run_id="p1", rate=12.0, monotonic=mono, wall=lambda: LOGICAL,
            pid=1234, host="test-host")
        authority.acquire()
        mono.advance(5.0)
        dbc.write_control(root, pause_requested=True, reason="test")
        mono.advance(1.0)  # 越过外部轮询缓存
        frozen_at = authority.now()
        mono.advance(1000.0)
        self.assertEqual(authority.now(), frozen_at)
        self.assertEqual(authority.snapshot()["state"], dbc.STATE_FROZEN)
        dbc.write_control(root, pause_requested=False, reason="test")
        mono.advance(1.0)
        authority.now()
        mono.advance(1.0)
        self.assertEqual((authority.now() - frozen_at).total_seconds(), 12.0)

    def test_stop_requests_are_observable_and_terminal(self):
        root = _root()
        code, _ = _cli("prepare", "--clock-only", "--root", str(root), "--run-id", "stoppable",
                       "--rate", "12")
        self.assertEqual(code, 0)
        code, stopped = _cli("stop", "--root", str(root))
        # 还没 start 过：没有自有进程可停，如实报告 IDLE/NO_RUN。
        self.assertEqual(code, 1)
        self.assertEqual(stopped["status"], fast_day.REASON_NO_RUN)


class ChildProcessTest(unittest.TestCase):
    def test_terminate_child_reaps_the_supervisor_owned_process(self):
        root = _root()
        code, _ = _cli("prepare", "--clock-only", "--root", str(root), "--run-id", "child",
                       "--rate", "12")
        self.assertEqual(code, 0)
        supervisor = fast_day.FastDaySupervisor(root)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        supervisor.child = child
        report = supervisor._terminate_child()
        self.assertTrue(report.get("exited"))
        self.assertIsNotNone(child.poll())
        self.assertIsNone(supervisor.child)


class WorldBodyClockSeamTest(unittest.TestCase):
    def test_world_body_service_reads_the_injected_business_clock(self):
        world_service = ROOT / "components" / "world" / "service"
        if str(world_service) not in sys.path:
            sys.path.insert(0, str(world_service))
        import world_body_service as wbsvc

        service = wbsvc.WorldBodyService.__new__(wbsvc.WorldBodyService)
        service._now = None
        service._clock = lambda: LOGICAL
        self.assertEqual(service._moment(), LOGICAL)
        service._clock = None
        service._now = LOGICAL + timedelta(hours=1)
        self.assertEqual(service._moment(), LOGICAL + timedelta(hours=1))
        service._now = None
        self.assertNotEqual(service._moment(), LOGICAL)  # 普通路径仍读真实墙钟

    def test_cycle_business_now_is_injectable(self):
        import autonomous_life_cycle as alc

        cycle = alc.AutonomousLifeCycle.__new__(alc.AutonomousLifeCycle)
        cycle._business_clock = lambda: LOGICAL
        self.assertEqual(cycle._now_iso(), LOGICAL.isoformat(timespec="milliseconds"))
        cycle._business_clock = None
        self.assertNotEqual(cycle._now_iso(), LOGICAL.isoformat(timespec="milliseconds"))

    def test_agenda_due_reads_the_same_business_clock(self):
        """未来事项/依赖到期与 Life 其余业务 path 用同一个逻辑 now，而不是真实墙钟。"""

        import autonomous_life_cycle as alc

        class _Store:
            def __init__(self):
                self.seen: list[str] = []

            def due(self, now_at):
                self.seen.append(str(now_at))
                return []

            def view(self, *, now=None, limit=8):
                self.seen.append(str(now))
                return {"status": "OK", "open_count": 0, "due_refs": [], "due": [], "open": []}

        store = _Store()
        cycle = alc.AutonomousLifeCycle.__new__(alc.AutonomousLifeCycle)
        cycle._business_clock = lambda: LOGICAL
        cycle.future_items = store
        cycle.due_future_items()
        cycle.future_items_view()
        expected = LOGICAL.isoformat(timespec="milliseconds")
        self.assertEqual(store.seen, [expected, expected])

    def test_journey_settlement_reads_the_same_business_clock(self):
        """旅程进度也用同一个域：显式传入业务时刻，而不是让 journey 自己读真实墙钟。"""

        import autonomous_life_cycle as alc

        class _Journey:
            def __init__(self):
                self.seen_now: list[str] = []
                self.seen_submit_port: list[Any] = []

            def advance_journey(self, *, hermes_home, environment, now, settle_only,
                                submit_port=None):
                self.seen_now.append(str(now))
                self.seen_submit_port.append(submit_port)
                return {"status": "in_transit", "reason_code": "IN_TRANSIT"}

        journey = _Journey()
        cycle = alc.AutonomousLifeCycle.__new__(alc.AutonomousLifeCycle)
        cycle._business_clock = lambda: LOGICAL
        cycle.world_home = str(_root())
        cycle.env = {}
        cycle.pause_state_root = _root()
        cycle.pause_gate = lambda: {"blocked": False}
        cycle.last_world_error = None
        # 这个夹具没有真实 runtime/正式端口：结算仍须可执行，拿到的是 None 端口。
        cycle.runtime = SimpleNamespace(world_action_adapter=None)
        original = alc.journey_module
        alc.journey_module = lambda: journey
        try:
            record = cycle.settle_world_journey()
        finally:
            alc.journey_module = original
        self.assertTrue(record["ok"])
        self.assertEqual(journey.seen_now, [LOGICAL.isoformat(timespec="milliseconds")])
        # 没有正式 runtime 时端口如实为 None，不是伪造一个可提交的正式口。
        self.assertEqual(journey.seen_submit_port, [None])


class WallTimeoutTest(unittest.TestCase):
    def test_real_wall_timeout_is_not_stopped_by_a_frozen_business_clock(self):
        """真实墙钟超时到点仍然收尾：逻辑冻结不能把进程永远挂住。"""

        import autonomous_life_resident as resident

        root = _root()
        dbc.create_clock(root, run_id="wall", rate=12.0)
        client = dbc.BusinessClockClient(dbc.clock_path(root / "state"))
        instance = resident.AutonomousLifeResident(
            state_root=root / "state", environment={}, poll_seconds=1,
            clock=lambda: datetime.now(timezone.utc), business_clock=client,
            sleeper=lambda seconds: time.sleep(min(seconds, 0.05)))
        began = time.monotonic()
        result = instance.run(duration_seconds=1)
        elapsed = time.monotonic() - began
        self.assertLess(elapsed, 15.0)
        self.assertGreaterEqual(result["wall_seconds"], 0)
        # tick 因未装配而 NOT_STARTED，但真实 deadline 仍然让循环收尾。
        self.assertIsNone(instance.runtime)


class DurationLimitTest(unittest.TestCase):
    def test_duration_is_capped_at_the_approved_real_window(self):
        self.assertEqual(dbc.MAX_RUN_REAL_SECONDS, 7200.0)
        root = _root()
        code, _ = _cli("prepare", "--clock-only", "--root", str(root), "--run-id", "dur",
                       "--rate", "24", "--logical-seconds", "86400")
        self.assertEqual(code, 0)
        code, refused = _cli("start", "--root", str(root), "--clock-only",
                             "--duration-seconds", "7201")
        self.assertEqual(code, 1, refused)
        self.assertEqual(refused["reason_code"], fast_day.REASON_DURATION_OUT_OF_RANGE)


class RunIdentityBindingTest(unittest.TestCase):
    """``prepare --run-id`` 必须是真正的新 run 身份，不是只存一个显示名。"""

    def test_prepare_binds_the_explicit_run_id_everywhere(self):
        from components.phone import life as phone_life

        # prepare 要求根尚不存在；mkdtemp 已建好父目录。显式 300/14/286 只绑定本实例：
        # 真实例的 UTC 日/run 窗口上限与认知每日配置都必须真的落在实例环境里。
        root = _root() / "instance"
        code, prepared = _cli("prepare", "--root", str(root), "--run-id", "fastday-int",
                              "--logical-seconds", "120", "--real-seconds", "60",
                              "--max-new-requests", "286",
                              "--request-total-limit", "300",
                              "--historical-requests", "14", timeout=900.0)
        self.assertEqual(code, 0, prepared)
        self.assertEqual(prepared["run_id"], "fastday-int")
        self.assertEqual(prepared["ledger_run_id"], "run:fastday-int")
        self.assertEqual(prepared["request_total_limit"], 300)
        self.assertEqual(prepared["historical_requests"], 14)
        self.assertEqual(prepared["max_new_requests"], 286)
        state_root = Path(str(prepared["state_root"]))
        clock = json.loads(dbc.clock_path(state_root).read_text(encoding="utf-8"))
        self.assertEqual(clock["run_id"], "run:fastday-int")
        cap = json.loads(dbc.experiment_path(state_root).read_text(encoding="utf-8"))
        self.assertEqual(cap["run_id"], "run:fastday-int")
        self.assertEqual(cap["max_new_requests"], 286)
        self.assertEqual(cap["authorized_total_limit"], 300)
        self.assertEqual(cap["historical_requests"], 14)
        self.assertEqual(cap["baseline_run_window_calls"], 0)
        wall = json.loads(dbc.wall_path(state_root).read_text(encoding="utf-8"))
        self.assertEqual(wall["run_id"], "run:fastday-int")
        nested = json.loads((root / "one-day" / "instance.json").read_text(encoding="utf-8"))
        self.assertEqual(nested["environment"]["CHIYO_AUTONOMOUS_RUN_ID"], "run:fastday-int")
        self.assertEqual(
            nested["environment"]["CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY"], "300")
        self.assertEqual(
            nested["environment"]["CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW"], "300")
        self.assertEqual(
            nested["environment"]["CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY"], "300")
        binding = phone_life.build_binding(phone_life.load_phone_instance(root),
                                           phone_life.read_nested_life(root))
        self.assertEqual(binding["environment"]["CHIYO_AUTONOMOUS_RUN_ID"],
                         "run:fastday-int")
        self.assertEqual(
            binding["environment"]["CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY"], "300")
        self.assertEqual(
            binding["environment"]["CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY"], "300")
        # 重启沿用同一个身份：run.json、clock、上限都不换 run。
        code, ran = _cli("start", "--root", str(root), "--clock-only",
                         "--poll-seconds", "0.2", "--duration-seconds", "2", timeout=120.0)
        self.assertEqual(code, 0, ran)
        record = json.loads((state_root / "fastday" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(record["run_id"], "run:fastday-int")
        code, status = _cli("status", "--root", str(root))
        self.assertEqual(code, 0, status)
        self.assertEqual(status["ledger_run_id"], "run:fastday-int")
        self.assertEqual(status["run_id"], "fastday-int")
        self.assertEqual(status["clock"]["run_id"], "run:fastday-int")
        self.assertEqual(status["request_limits"],
                         {"total_limit": 300, "historical_requests": 14,
                          "max_new_requests": 286, "cognition_calls_per_day": 300})
        self.assertEqual(status["experiment"]["authorized_total_limit"], 300)
        self.assertEqual(status["experiment"]["historical_requests"], 14)
        self.assertEqual(status["experiment"]["max_new_requests"], 286)
        self.assertEqual(status["experiment"]["hard_config_max"], 300)
        code, again = _cli("start", "--root", str(root), "--clock-only",
                           "--poll-seconds", "0.2", "--duration-seconds", "2", timeout=120.0)
        self.assertEqual(code, 0, again)
        code, status = _cli("status", "--root", str(root))
        self.assertEqual(status["ledger_run_id"], "run:fastday-int")

    def test_inherited_run_identity_conflict_is_refused_before_any_write(self):
        root = _root()
        env = dict(os.environ)
        env["CHIYO_AUTONOMOUS_RUN_ID"] = "run:someone-elses-shell"
        code, refused = _cli("prepare", "--root", str(root), "--run-id", "conflict",
                             "--logical-seconds", "120", "--real-seconds", "60",
                             timeout=120.0, env=env)
        self.assertEqual(code, 1, refused)
        self.assertEqual(refused["reason_code"], fast_day.REASON_INHERITED_RUN_CONFLICT)
        self.assertFalse((root / "fastday.json").exists())
        self.assertEqual(list(root.iterdir()) if root.exists() else [], [])


class WallBudgetCliTest(unittest.TestCase):
    def test_restart_cannot_recover_wall_already_spent(self):
        root = _root()
        code, _ = _cli("prepare", "--clock-only", "--root", str(root), "--run-id", "wall",
                       "--rate", "24", "--logical-seconds", "86400")
        self.assertEqual(code, 0)
        wall_file = dbc.wall_path(root / "state")
        before = json.loads(wall_file.read_text(encoding="utf-8"))
        self.assertEqual(before["consumed_seconds"], 0)
        code, first = _cli("start", "--root", str(root), "--clock-only",
                          "--poll-seconds", "0.2", "--duration-seconds", "2", timeout=120.0)
        self.assertEqual(code, 0, first)
        after_first = json.loads(wall_file.read_text(encoding="utf-8"))
        self.assertGreater(after_first["consumed_seconds"], 0)
        self.assertFalse(after_first["open_since_utc"])
        code, second = _cli("start", "--root", str(root), "--clock-only",
                           "--poll-seconds", "0.2", "--duration-seconds", "2", timeout=120.0)
        self.assertEqual(code, 0, second)
        self.assertLessEqual(second["supervisor"]["duration_limit_seconds"],
                            dbc.MAX_RUN_REAL_SECONDS - after_first["consumed_seconds"])
        after_second = json.loads(wall_file.read_text(encoding="utf-8"))
        self.assertGreaterEqual(after_second["consumed_seconds"],
                                after_first["consumed_seconds"])
        # 剩余墙钟只剩 1.5 秒时，请求 5 秒也只能拿到剩余额度，不会把已耗墙钟借回来。
        data = dict(after_second)
        data["consumed_seconds"] = dbc.MAX_RUN_REAL_SECONDS - 1.5
        data["open_since_utc"] = None
        wall_file.write_text(json.dumps(data), encoding="utf-8")
        code, third = _cli("start", "--root", str(root), "--clock-only",
                          "--poll-seconds", "0.2", "--duration-seconds", "5", timeout=120.0)
        self.assertEqual(code, 0, third)
        self.assertGreater(third["supervisor"]["duration_limit_seconds"], 0)
        self.assertLessEqual(third["supervisor"]["duration_limit_seconds"], 1.6)
        # 把累计墙钟推到上限：下一次 start 具名拒绝，而不是再借 7200 秒。
        data = json.loads(wall_file.read_text(encoding="utf-8"))
        data["consumed_seconds"] = dbc.MAX_RUN_REAL_SECONDS
        data["open_since_utc"] = None
        wall_file.write_text(json.dumps(data), encoding="utf-8")
        code, refused = _cli("start", "--root", str(root), "--clock-only",
                             "--duration-seconds", "2", timeout=120.0)
        self.assertEqual(code, 1, refused)
        self.assertEqual(refused["reason_code"], fast_day.REASON_WALL_BUDGET_EXHAUSTED)

    def test_a_crashed_open_segment_is_deducted_on_the_next_start(self):
        root = _root()
        code, _ = _cli("prepare", "--clock-only", "--root", str(root), "--run-id", "crash",
                       "--rate", "24", "--logical-seconds", "86400")
        self.assertEqual(code, 0)
        wall_file = dbc.wall_path(root / "state")
        data = json.loads(wall_file.read_text(encoding="utf-8"))
        # 模拟上一次进程被 kill：段还开着，起始于 90 秒前。
        data["open_since_utc"] = (datetime.now(timezone.utc)
                                  - timedelta(seconds=90)).isoformat()
        wall_file.write_text(json.dumps(data), encoding="utf-8")
        code, ran = _cli("start", "--root", str(root), "--clock-only",
                         "--poll-seconds", "0.2", "--duration-seconds", "1", timeout=120.0)
        self.assertEqual(code, 0, ran)
        self.assertGreaterEqual(ran["supervisor"]["wall_budget_start"]["recovered_seconds"], 89)
        after = json.loads(wall_file.read_text(encoding="utf-8"))
        self.assertGreaterEqual(after["consumed_seconds"], 90)
        self.assertFalse(after["open_since_utc"])


class RealSubprocessDomainTest(unittest.TestCase):
    def test_a_foreign_domain_is_refused_by_the_real_resident_process(self):
        root = _root()
        dbc.create_clock(root, run_id="r1", rate=12.0)
        env = dict(os.environ)
        env.update({"NYA02_FASTDAY_CLOCK": str(dbc.clock_path(root)),
                    "NYA02_FASTDAY_DOMAIN": "fastday:someone-else",
                    "PYTHONDONTWRITEBYTECODE": "1"})
        result = subprocess.run(
            [sys.executable, "-B",
             str(ROOT / "components" / "life" / "scripts" / "autonomous_life_resident.py"),
             "--state-root", str(root / "state"), "--once"],
            capture_output=True, text=True, timeout=120.0, cwd=str(ROOT), env=env)
        self.assertEqual(result.returncode, 3, result.stderr[-1000:])
        payload = json.loads(result.stdout)
        self.assertFalse(payload["started"]["ok"])
        self.assertEqual(payload["started"]["reason"], dbc.REASON_CLOCK_DOMAIN_MISMATCH)

    def test_another_process_reads_the_same_domain(self):
        root = _root()
        dbc.create_clock(root, run_id="r1", rate=12.0)
        code = (
            "import sys;"
            "sys.path[:0]=[%r,%r];"
            "import demo_business_clock as dbc;"
            "c=dbc.BusinessClockClient(sys.argv[1], expected_domain='fastday:r1',"
            " expected_run_id='r1');"
            "print(c.snapshot()['domain_id'])"
            % (str(ROOT / "components" / "life" / "scripts"), str(ROOT)))
        result = subprocess.run([sys.executable, "-B", "-c", code, str(dbc.clock_path(root))],
                                capture_output=True, text=True, timeout=60.0, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.assertEqual(result.stdout.strip(), "fastday:r1")


class RealOwnerGrantTest(unittest.TestCase):
    """真实 Supply owner 的 grant 校验：过期就拒，逻辑 +24h 不能让有效 grant 失效。"""

    def test_real_owner_refuses_an_expired_grant_and_keeps_a_valid_one(self):
        from components.phone import demo
        import life_artifact_port as lap
        import one_day_life as odl

        root = _root() / "instance"
        prepared = demo.prepare_demo(root, with_world=False)
        subject = str(prepared["subject"])
        workspace = str(prepared["workspace_id"])
        child = demo._supply_child(root, writer_instance="fastday-grant-test",
                                   generation="grant-%d" % os.getpid())
        child.start()
        try:
            self.assertTrue(child.wait_ready(child.socket_path, probe="supply",
                                             timeout=120.0))
            socket_path = str(child.socket_path)
            now = time.time()

            def issue(tag: str, expires: float) -> str:
                answer = odl.supply_call(socket_path, {"op": "issue_grant", "grant": {
                    "subject": subject, "capability": "COMMIT_MANAGED_ARTIFACT",
                    "scope": "artifact:personal",
                    "target": "workspace:%s" % workspace,
                    "authority_scope": "life-supply.artifact.v1",
                    "use_mode": "REUSABLE", "valid_from": now - 7200,
                    "expires_at": expires,
                    "operation_id": "fastday-grant-test:%s" % tag}})
                self.assertEqual(answer.get("status"), "ALLOW", answer)
                return str(answer["grant_id"])

            expired = issue("expired", now - 60)
            valid = issue("valid", now + 3600)
            port = lap.LifeArtifactPort(socket_path, subject)
            refused = port.create_artifact(
                workspace_id=workspace, title="expired", content="x",
                grant_id=expired, operation_key="fastday:grant:expired")
            self.assertFalse(refused.applied, refused.to_dict())

            # 逻辑时钟推进 24 逻辑小时：真实授权仍然按真实时间判定，有效 grant 照常可用。
            clock_root = _root()
            dbc.create_clock(clock_root, run_id="r1", rate=24.0, wall=lambda: LOGICAL)
            mono = _Mono()
            authority = dbc.BusinessClockAuthority(
                clock_root, run_id="r1", rate=24.0, monotonic=mono,
                wall=lambda: LOGICAL, pid=999)
            authority.acquire()
            mono.advance(3600.0)
            # 这个夹具的"真实墙钟"就是 LOGICAL 本身（wall=lambda: LOGICAL）；用真实机器的
            # datetime.now 会让断言随当天时刻漂移，因此按夹具自己的锚点核对"已推进 24 逻辑小时"。
            self.assertGreater(
                (authority.now() - LOGICAL).total_seconds(), 23 * 3600)

            accepted = port.create_artifact(
                workspace_id=workspace, title="valid", content="y",
                grant_id=valid, operation_key="fastday:grant:valid")
            self.assertTrue(accepted.applied, accepted.to_dict())

            # 逻辑时钟已经推进到很远、但 owner 只看真实时间：此刻新签发的过期 grant
            # 仍然必须被拒（不会因为业务时钟/其他锚点而被读成有效）。
            expired_late = issue("expired-after-logical", time.time() - 30)
            refused_late = port.create_artifact(
                workspace_id=workspace, title="late", content="z",
                grant_id=expired_late, operation_key="fastday:grant:expired-late")
            self.assertFalse(refused_late.applied, refused_late.to_dict())
        finally:
            child.stop()


def _bridge_env(instance: dict, socket_path: Path) -> dict[str, str]:
    """The actual bridge environment: same World home, socket and process gate."""

    env = dict(os.environ)
    env.update({
        "CHIYO_WORLD_HOME": str(instance["world_home"]),
        "HERMES_HOME": str(instance["world_home"]),
        "CHIYO_WORLD_ID": str(instance.get("world_id") or ""),
        # 旅程引擎自己读世界包来解析路线；缺了它会退回内建世界，状态文件对不上。
        "CHIYO_WORLD_PACKAGE": str(instance.get("world_package") or ""),
        "WORLD_BODY_SOCKET": str(socket_path),
        "WORLD_BODY_INTEGRATION_MODE": "canonical",
        "WORLD_BODY_INTENT_MUTATION_GATE": "canonical",
        "NYAIRO_WORLD_BRIDGE_DIR": str(
            ROOT / "components" / "world" / "service" / "bridge"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


class RealWorldOwnerClockSafetyTest(unittest.TestCase):
    """真实 World owner 的时钟分域：业务 +24h 不影响真实授权，真实过期仍拒。

    两段都用真实 gateway、真实 Intent Executor 授权与真实 Resolver：
    * 逻辑时钟推进 24 小时（身体后果 ``applied_at`` 落在逻辑域），
      经正式 adapter 的 MOVE 仍然 ``committed``，授权 ``issued/expires/consumed``
      时间戳仍在真实墙钟附近；
    * 另一段把业务时钟放在 2020 年且冻结（比真实过期早），一条真实已过期
      的授权仍被 gateway 以 ``AUTHORIZATION_EXPIRED`` 拒绝——如果消费用的是业务
      时间，它反而会被接受。

    不伪造 Activity、不写旧实例、产品模型 0 调用；根都在唯一的 ``/tmp`` 下。
    """

    BEHIND = datetime(2020, 1, 1, 9, 0, tzinfo=timezone.utc)

    def _ahead_clock(self, state: Path) -> Any:
        run_id, domain = "run:clock-safe", "fastday:clock-safe"
        # horizon 留出余量：先推进 24 逻辑小时，之后还要在同一域里推进到旅程到达。
        horizon = 48 * 3600
        dbc.create_clock(state, run_id=run_id, rate=24.0, wall=lambda: LOGICAL,
                         logical_seconds=horizon, domain_id=domain)
        mono = _Mono()
        authority = dbc.BusinessClockAuthority(
            state, run_id=run_id, domain_id=domain, rate=24.0,
            logical_seconds=horizon, monotonic=mono, wall=lambda: LOGICAL, pid=999)
        authority.acquire()
        mono.advance(3600.0)
        authority.checkpoint()
        return authority, dbc.BusinessClockClient(
            dbc.clock_path(state), expected_domain=domain, expected_run_id=run_id), mono

    def test_real_owner_move_keeps_real_authorization_times_and_refuses_true_expiry(self):
        from components.phone import demo, service as phone_service

        root = _root() / "instance"
        demo.prepare_demo(root, with_world=True, seed_work_artifact=False,
                          run_id="clock-safe")
        instance = dict(phone_service.load_instance(root))
        world_home = Path(str(instance["world_home"]))

        world_service = ROOT / "components" / "world" / "service"
        if str(world_service) not in sys.path:
            sys.path.insert(0, str(world_service))
        import world_action_authorization as waa
        from world_action_bridge_adapter import WorldBridgeResolver

        bridge_dir = ROOT / "components" / "world" / "service" / "bridge"

        # -- A. 业务时钟 +24h：真实授权不误过期，MOVE 真的提交 --------------------
        ahead = _root()
        authority, client, mono = self._ahead_clock(ahead)
        # 夹具的"真实墙钟"就是 LOGICAL（wall=lambda: LOGICAL）；按夹具自己的锚点核对
        # "已推进 24 逻辑小时"，不用真实机器的 datetime.now 让断言随当天时刻漂移。
        self.assertGreater(
            (client.now() - LOGICAL).total_seconds(), 20 * 3600)
        child = demo._world_child(
            root, instance, business_clock=dbc.clock_path(ahead),
            clock_domain="fastday:clock-safe", clock_run_id="run:clock-safe")
        child.start()
        try:
            self.assertTrue(child.wait_ready(child.socket_path, timeout=180.0,
                                             probe="gateway"))
            env = _bridge_env(instance, child.socket_path)
            with mock.patch.dict(os.environ, env, clear=False):
                resolver = WorldBridgeResolver(environment=env, bridge_dir=bridge_dir)
                # 卧室内先到房间入口：Resolver 自己的拓扑规则；计时出发在业务域里到期才到达。
                first = resolver.resolve_action(
                    idempotency_key="clock-safe:home", verb="MOVE", target="home",
                    expected_dependencies={})
                self.assertEqual(first.get("status"), "committed", first)
                mono.advance(5.0)  # rate 24 → +120 逻辑秒，超过 60 秒步行
                authority.checkpoint()

                records = json.loads(
                    (world_home / "run" / "action_authorizations.json").read_text(
                        encoding="utf-8"))["records"]
                consumed = [
                    record for record in records
                    if record.get("execution_id") == first["execution_id"]
                    and record.get("state") == "CONSUMED"]
                self.assertTrue(consumed, records[-3:])
                record = consumed[-1]
                real_now = datetime.now(timezone.utc)
                # 授权三段时间都在真实墙钟附近，而不是逻辑 +24h。
                for field in ("issued_at", "expires_at", "consumed_at"):
                    moment = datetime.fromisoformat(str(record[field]))
                    self.assertLess(abs((moment - real_now).total_seconds()), 300,
                                    {field: record[field], "logical": client.now().isoformat()})
                self.assertLessEqual(
                    datetime.fromisoformat(str(record["expires_at"]))
                    - datetime.fromisoformat(str(record["issued_at"])),
                    timedelta(minutes=10))

                # 身体后果落在业务域：同一个真实动作，两种时间各自在自己的域里。
                consequences = [
                    json.loads(line) for line in
                    (world_home / "data" / "body" / "consequences.jsonl").read_text(
                        encoding="utf-8").splitlines() if line.strip()]
                applied = [datetime.fromisoformat(str(entry["applied_at"]))
                           for entry in consequences if entry.get("applied_at")]
                self.assertTrue(applied, consequences[-2:])
                for moment in applied:
                    # 身体后果在业务域；与夹具锚点 LOGICAL 比，而不是真实墙钟。
                    self.assertGreater(
                        (moment - LOGICAL).total_seconds(), 20 * 3600,
                        {"applied_at": moment.isoformat(),
                         "logical_now": client.now().isoformat()})

                # 让 owner 自己的到期逻辑在业务域里结算这次出发（不是测试伪造位置）。
                scripts_dir = str(ROOT / "components" / "world" / "scripts")
                if scripts_dir not in sys.path:
                    sys.path.insert(0, scripts_dir)
                import world_travel_delivery as wtd
                travel_env = dict(env)
                travel_env["CHIYO_WORLD_TRAVEL_TIME_ENABLED"] = "1"
                realized = wtd.realize_due_travel(
                    hermes_home=str(world_home), environment=travel_env,
                    now=client.now())
                self.assertIn(realized.get("status"), {"arrived", "none", "already"},
                              realized)

                # 权威 lease 的 marker 属于安全/审计面：即使业务时钟在 +24h，
                # acquired_at 也必须是真实墙钟。
                marker = json.loads(
                    (world_home / "run" / "authority.json").read_text(encoding="utf-8"))
                acquired = datetime.fromisoformat(str(marker["acquired_at"]))
                self.assertLess(
                    abs((acquired - datetime.now(timezone.utc)).total_seconds()), 300,
                    {"acquired_at": marker["acquired_at"],
                     "logical": client.now().isoformat()})

                # -- A2. 正式 run 实际使用的计时 adapter：旅程出发也是同一个业务域 ----
                travel_env = dict(env)
                travel_env["CHIYO_WORLD_TRAVEL_TIME_ENABLED"] = "1"
                from world_journey_action_adapter import (
                    JourneyWorldResolver, RealJourneyWorldActionAdapter)

                business_now = client.now()
                journey_adapter = RealJourneyWorldActionAdapter(
                    JourneyWorldResolver(environment=travel_env, now=client.now))
                with mock.patch.dict(os.environ, travel_env, clear=False):
                    departed = journey_adapter.resolver.resolve_action(
                        idempotency_key="clock-safe:journey-street", verb="MOVE",
                        target="street_1", expected_dependencies={})
                self.assertEqual(departed.get("status"), "committed", departed)

                travels = json.loads(
                    (world_home / "data" / "world" / "world_travel_delivery.json")
                    .read_text(encoding="utf-8"))["travels"]
                started = [datetime.fromisoformat(
                    str(travel["started_at"]).replace("Z", "+00:00"))
                    for travel in travels if travel.get("started_at")]
                self.assertTrue(started, travels[-2:])
                # 旅程出发时间落在业务域（+24h），不是真实墙钟。
                self.assertTrue(
                    any(abs((moment - business_now).total_seconds()) < 120
                        for moment in started),
                    {"started_at": [moment.isoformat() for moment in started],
                     "logical": business_now.isoformat()})
                # 业务 now 与夹具锚点同域。
                self.assertGreater(
                    (business_now - LOGICAL).total_seconds(), 20 * 3600)

                # 同一旅程用推进后的业务 now 结算：段到达是业务域内的算术，不是墙钟。
                mono.advance(10.0)
                authority.checkpoint()
                arrival_now = client.now()
                self.assertGreater(
                    (arrival_now - business_now).total_seconds(), 60,
                    {"before": business_now.isoformat(), "after": arrival_now.isoformat()})
                settled = journey_adapter.resolver.journey.advance_journey(
                    hermes_home=str(world_home), environment=travel_env, now=arrival_now,
                    submit_port=journey_adapter.resolver.submit_port)
                self.assertGreaterEqual(int(settled.get("segments_confirmed") or 0), 1,
                                        settled)
        finally:
            child.stop()

        # -- B. 业务时钟停在 2020 年：真实已过期授权仍被真实 gateway 拒绝 ---------
        #     用**全新实例**：业务时钟倒退在同一个 world home 上会触发 body 自身
        #     ``now must not precede last_persisted_at`` 的合法不变量，那是另一个事实，
        #     不是授权分域。全新根没有 last_persisted_at，才能只检验授权消费的时钟。
        behind_root = _root() / "instance"
        demo.prepare_demo(behind_root, with_world=True, seed_work_artifact=False,
                          run_id="clock-behind")
        behind_instance = dict(phone_service.load_instance(behind_root))
        behind_home = Path(str(behind_instance["world_home"]))
        behind = _root()
        dbc.create_clock(behind, run_id="run:clock-behind", rate=24.0,
                         wall=lambda: self.BEHIND, domain_id="fastday:clock-behind")
        stopped = dbc.BusinessClockClient(
            dbc.clock_path(behind), expected_domain="fastday:clock-behind",
            expected_run_id="run:clock-behind")
        self.assertEqual(stopped.now(), self.BEHIND)
        child = demo._world_child(
            behind_root, behind_instance, business_clock=dbc.clock_path(behind),
            clock_domain="fastday:clock-behind", clock_run_id="run:clock-behind")
        child.start()
        try:
            self.assertTrue(child.wait_ready(child.socket_path, timeout=180.0,
                                             probe="gateway"))
            env = _bridge_env(behind_instance, child.socket_path)
            with mock.patch.dict(os.environ, env, clear=False):
                resolver = WorldBridgeResolver(environment=env, bridge_dir=bridge_dir)
                registry = waa.AuthorizationRegistry(behind_home / "run")
                expired = registry.issue(
                    decision_id=None, intent_id="clock-safe:expired",
                    intent_fingerprint="x", execution_id="clock-safe:expired:1",
                    actor="chiyo", verb="MOVE",
                    params={"destination_location_id": "home"},
                    authority_generation=None, ttl_seconds=60,
                    now=time.time() - 3600)
                denied = resolver.client.submit_action(
                    action="MOVE", execution_id="clock-safe:expired:1",
                    params={"destination_location_id": "home"},
                    authorization_id=expired["authorization_id"])
                payload = dict(denied.data or {})
                self.assertEqual(payload.get("reason_code"), "AUTHORIZATION_EXPIRED",
                                 payload)
                self.assertTrue(payload.get("world_unchanged"), payload)
                self.assertEqual(payload.get("status"), "rejected", payload)
        finally:
            child.stop()


if __name__ == "__main__":
    unittest.main()

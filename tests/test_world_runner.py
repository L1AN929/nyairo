"""W03：前台常驻推进器、单实例 run lease、持久暂停与只读状态。

场景对应（施工单 §7）：

* A 无新聊天输入的常驻循环把真实 ≥3 段旅程推到终点（注入 clock/wait，不用 wallclock 冒充）
* B 两个真实子进程竞争 run lease；SIGTERM 干净退出后可重启；kill 后也能恢复
* C pause 只允许已出发段到达确认、不发起下一段；重启仍暂停；resume 不重走已确认段
* D pause 与 advance/工具/once 的并发线性化；损坏控制状态 fail closed
* E status 在缺目录/无 runner/运行/暂停/正常退出/崩溃/陈旧/伪造 PID 下正确分类且零写入
* F 身份冲突/非法路径/interval NaN 与负数/run 固定 now/禁用 feature
* G 坏旅程与坏 travel、图变化：不清理目标、不紧密重试、不假健康、退出码可解释
* H gateway / resolver 的 environment 定向回归 + 旧汐见市兼容

需要 POSIX（fcntl 文件锁）与 tz 数据库；原生 Windows 自动跳过。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_world_journey import JourneyTestCase, _clean_env, posix_only  # noqa: E402

from chiyo_bundle import world_control, world_journey, world_runner  # noqa: E402

GATES = {
    "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
    "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
}


@posix_only
class RunnerTestCase(JourneyTestCase):
    """共用夹具：世界实例 + runner 环境 + 子进程/注入时钟工具。"""

    def run_env(self, world=None) -> dict:
        world = world or self.world
        return world_runner.build_environment(
            world.home, overrides=dict(GATES), world_package=str(world.package_dir)
        )

    # -- 注入时钟（不 sleep 猜时序）---------------------------------------

    def fake_clock(self, *, step_seconds: float = 200.0):
        """返回 (clock, sleeper, utcnow, state)；sleeper 推进 monotonic 与 UTC。"""
        state = {"monotonic": 0.0, "waits": []}
        utc = {"value": self.now}

        def clock() -> float:
            return state["monotonic"]

        def sleeper(seconds: float) -> None:
            state["waits"].append(seconds)
            state["monotonic"] += seconds
            utc["value"] += timedelta(seconds=seconds)

        def utcnow() -> datetime:
            value = utc["value"]
            utc["value"] = value + timedelta(seconds=step_seconds)
            return value

        return clock, sleeper, utcnow, state

    # -- 子进程 -----------------------------------------------------------

    def child_script(self, code: str, world=None) -> str:
        world = world or self.world
        return (
            "import os, sys, time\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            f"sys.path.insert(0, {str(ROOT / 'tests')!r})\n"
            f"sys.path.insert(0, {str(ROOT / 'components' / 'world' / 'scripts')!r})\n"
            "from chiyo_bundle import world_runner\n"
            f"HOME = {str(world.home)!r}\n"
            f"ENV = {dict(self.run_env(world))!r}\n"
            + code
        )

    def spawn(self, code: str, world=None):
        # 子进程用最小世界环境，但禁写 .pyc（不给工作树留缓存垃圾）。
        env = dict(self.run_env(world), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.Popen(
            [sys.executable, "-c", self.child_script(code, world)],
            env=env, cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def await_marker(self, process, marker: str, timeout: float = 60.0) -> str:
        """阻塞读取子进程 stdout 直到出现 marker（真实事件同步）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            if marker in line:
                return line
        stderr = process.stderr.read() if process.poll() is not None else ""
        raise AssertionError(f"child never printed {marker!r}; stderr={stderr!r}")

    def reap(self, process, timeout: float = 60.0):
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:  # pragma: no cover - 失败即测试失败
            process.kill()
            out, err = process.communicate()
            raise AssertionError(f"child did not exit; stdout={out!r} stderr={err!r}")
        return process.returncode, out, err

    def close_pipes(self, process) -> None:
        """kill 掉的子进程没有 communicate：显式关掉管道，避免 ResourceWarning 噪声。"""
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:  # pragma: no cover - 已关闭
                pass

    # -- 便捷读取 ---------------------------------------------------------

    def runner_record(self, world=None) -> dict:
        path = world_runner.runtime_path((world or self.world).home)
        return json.loads(path.read_text(encoding="utf-8"))

    def status(self, world=None, *, env=None, now=None) -> dict:
        world = world or self.world
        return world_runner.runner_status(
            world.home, environment=env or self.run_env(world), now=now
        )

    def heartbeat(self, argv, world=None):
        from scripts import life_heartbeat
        import contextlib
        import io

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = life_heartbeat.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def once_args(self, world=None) -> list:
        world = world or self.world
        return ["once", "--world-home", str(world.home),
                "--world-package", str(world.package_dir),
                "--env", "CHIYO_WORLD_LIVING_SKELETON_ENABLED=1",
                "--env", "CHIYO_WORLD_TRAVEL_TIME_ENABLED=1"]

    def pause(self, world=None, *, paused=True) -> dict:
        world = world or self.world
        return world_runner.set_pause(
            world.home, self.run_env(world), paused=paused, reason="test"
        )


@posix_only
class RunLoopTest(RunnerTestCase):
    """A：常驻循环在无新输入时把真实旅程推到终点。"""

    def test_loop_advances_real_three_segment_journey_without_input(self) -> None:
        world = self.world
        s1, s2, s4 = world.ids["s1"], world.ids["s2"], world.ids["s4"]
        self.assertEqual(self.accept(s4)["status"], "started")
        clock, sleeper, utcnow, state = self.fake_clock(step_seconds=200.0)
        ticks: list = []
        summary = world_runner.run_forever(
            home=world.home,
            environment=self.run_env(world),
            interval=1.0,
            clock=clock,
            sleeper=sleeper,
            utcnow=utcnow,
            max_ticks=8,
            install_signal_handlers=False,
            on_tick=ticks.append,
        )
        self.assertEqual(summary["exit_code"], 0, summary)
        self.assertEqual(summary["stop_reason"], "MAX_TICKS")
        self.assertEqual(self.position(), s4)
        segments = self.journey_state()["active"]["segments"]
        records = self.travels()
        self.assertEqual(len(records), 3)
        self.assertEqual(
            [record["execution_id"] for record in records],
            [segment["execution_id"] for segment in segments],
        )
        self.assertTrue(all(record["status"] == "ARRIVED" for record in records))
        self.assertEqual(
            [segment["status"] for segment in segments],
            ["confirmed", "confirmed", "confirmed"],
        )
        self.assertEqual(self.world_revision(), 4)
        # 真实时间被推进（注入的 UTC 时钟），monotonic 只用于调度等待。
        self.assertTrue(state["waits"])
        self.assertTrue(all(wait <= world_runner.WAIT_CHUNK + 1e-9 for wait in state["waits"]))
        record = self.runner_record(world)
        self.assertEqual(record["status"], "stopped")
        self.assertEqual(record["tick_count"], 8)
        self.assertGreaterEqual(len(ticks), 3)



@posix_only
class ToolContinuationNoteTest(RunnerTestCase):
    """§6：工具中转文案必须跟随真实 runner 观测，不能凭空承诺后台续行。"""

    def test_continuation_note_tracks_real_runner_health(self) -> None:
        world = self.make_world(prefix="w03-note-")
        offline = self.move(world.ids["s4"], world=world)
        self.assertEqual(offline["ok"], True)
        self.assertEqual(offline["destination_location_id"], world.ids["s1"])
        self.assertIn("中转", offline["message"])
        self.assertNotIn("后台推进器正在运行", offline["message"])
        self.assertIn("需要后续推进", offline["message"])
        # 观测到一个真正健康、未暂停的运行实例时才承诺后台续行。
        other = self.make_world(prefix="w03-note2-")
        child = self.spawn(
            "with world_runner.run_lease(HOME):\n"
            "    print('LEASED', flush=True)\n"
            "    world_runner.run_forever(\n"
            "        home=HOME, environment=ENV, interval=0.05,\n"
            "        on_tick=lambda outcome: print('TICK', flush=True))\n",
            other,
        )
        try:
            self.await_marker(child, "LEASED")
            self.await_marker(child, "TICK")
            live = self.move(other.ids["s4"], world=other)
            self.assertEqual(live["ok"], True)
            self.assertEqual(live["destination_location_id"], other.ids["s1"])
            self.assertIn("后台推进器正在运行", live["message"])
            # 暂停后：工具拒绝，且绝不显示"自动服务已运行"。
            self.pause(other)
            paused = self.move(other.ids["s4"], world=other)
            self.assertEqual(paused["ok"], False)
            self.assertEqual(paused["status"], "paused")
            self.assertNotIn("后台推进器正在运行", paused["message"])
        finally:
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)
                self.reap(child, timeout=60)
            self.close_pipes(child)

@posix_only
class RunLeaseTest(RunnerTestCase):
    """B：单实例 run lease 与信号/崩溃后的可重启性。"""

    def test_second_run_process_refuses_fast_without_waiting(self) -> None:
        world = self.world
        holder = self.spawn(
            "with world_runner.run_lease(HOME):\n"
            "    print('LEASED', flush=True)\n"
            "    time.sleep(30)\n",
            world,
        )
        try:
            self.await_marker(holder, "LEASED")
            started = time.monotonic()
            second = self.spawn(
                "try:\n"
                "    with world_runner.run_lease(HOME):\n"
                "        print('LEASED', flush=True)\n"
                "except world_runner.RunnerError as exc:\n"
                "    print('REFUSED', exc.reason_code, flush=True)\n"
                "    raise SystemExit(4)\n",
                world,
            )
            code, out, _ = self.reap(second, timeout=30)
            elapsed = time.monotonic() - started
            self.assertEqual(code, 4)
            self.assertIn("RUN_LEASE_HELD", out)
            # 没有等待到持有者退出：holder 仍然活着。
            self.assertIsNone(holder.poll())
            self.assertLess(elapsed, 20.0)
        finally:
            holder.kill()
            holder.wait(timeout=30)
            self.close_pipes(holder)

    def test_sigterm_releases_lease_and_restart_succeeds(self) -> None:
        world = self.world
        first = self.spawn(
            "with world_runner.run_lease(HOME):\n"
            "    print('LEASED', flush=True)\n"
            "    summary = world_runner.run_forever(\n"
            "        home=HOME, environment=ENV, interval=0.05,\n"
            "        on_tick=lambda outcome: print('TICK', flush=True))\n"
            "    print('DONE', summary['stop_reason'], flush=True)\n"
            "    raise SystemExit(summary['exit_code'])\n",
            world,
        )
        self.await_marker(first, "LEASED")
        self.await_marker(first, "TICK")
        first.send_signal(signal.SIGTERM)
        code, out, err = self.reap(first, timeout=60)
        self.assertEqual(code, 0, (out, err))
        self.assertIn("DONE SIGTERM", out)
        record = self.runner_record(world)
        self.assertEqual(record["status"], "stopped")
        self.assertEqual(record["stop_reason"], "SIGTERM")
        self.assertEqual(world_runner.probe_lease(world.home), world_runner.LIVENESS_FREE)
        # 锁文件没有被删除：重启依赖内核释放 flock，而不是删文件。
        self.assertTrue(world_runner.lease_path(world.home).exists())
        # 重启取得 lease。
        second = self.spawn(
            "with world_runner.run_lease(HOME):\n"
            "    print('LEASED', flush=True)\n",
            world,
        )
        self.await_marker(second, "LEASED")
        code, _, _ = self.reap(second, timeout=30)
        self.assertEqual(code, 0)

    def test_kill_leaves_recoverable_lease_and_crashed_record(self) -> None:
        world = self.world
        first = self.spawn(
            "with world_runner.run_lease(HOME):\n"
            "    print('LEASED', flush=True)\n"
            "    world_runner.run_forever(\n"
            "        home=HOME, environment=ENV, interval=0.05,\n"
            "        on_tick=lambda outcome: print('TICK', flush=True))\n",
            world,
        )
        self.await_marker(first, "LEASED")
        self.await_marker(first, "TICK")
        first.kill()
        first.wait(timeout=30)
        self.close_pipes(first)
        self.assertEqual(world_runner.probe_lease(world.home), world_runner.LIVENESS_FREE)
        report = self.status()
        self.assertEqual(report["runner"]["classification"], world_runner.CLASS_CRASHED)
        self.assertEqual(report["runner"]["health"], world_runner.HEALTH_CRASHED)
        # 崩溃后仍能取得 lease（不删锁文件）。
        self.assertTrue(world_runner.lease_path(world.home).exists())
        second = self.spawn(
            "with world_runner.run_lease(HOME):\n"
            "    print('LEASED', flush=True)\n",
            world,
        )
        self.await_marker(second, "LEASED")
        self.assertEqual(self.reap(second, timeout=30)[0], 0)


@posix_only
class PauseSemanticsTest(RunnerTestCase):
    """C/D：持久暂停语义与线性化。"""

    def test_pause_allows_started_segment_to_arrive_but_not_next(self) -> None:
        world = self.world
        s1, s2, s4 = world.ids["s1"], world.ids["s2"], world.ids["s4"]
        self.accept(s4)
        self.assertTrue(self.pause()["control"]["paused"])
        # 首段已真正出发：暂停后到期仍会结算并确认，但不发起下一段。
        settled = self.advance(now=self.now + timedelta(seconds=300))
        self.assertEqual(settled["status"], "paused")
        self.assertEqual(settled["reason_code"], "JOURNEY_PAUSED")
        self.assertEqual(settled["segments_confirmed"], 1)
        self.assertEqual(self.position(), s1)
        self.assertEqual(len(self.travels()), 1)
        state = self.journey_state()["active"]
        self.assertEqual(state["segments"][0]["status"], "confirmed")
        self.assertEqual(state["segments"][1]["status"], "pending")
        self.assertEqual(state["status"], "pending")
        self.assertNotIn("blocked", json.dumps(state))
        # run 重启后仍然暂停：循环只报告 paused，不启动新段。
        clock, sleeper, utcnow, _ = self.fake_clock(step_seconds=500.0)
        summary = world_runner.run_forever(
            home=world.home, environment=self.run_env(world), interval=0.5,
            clock=clock, sleeper=sleeper, utcnow=utcnow, max_ticks=3,
            install_signal_handlers=False,
        )
        self.assertEqual(summary["last_tick_status"], world_runner.TICK_PAUSED)
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(self.position(), s1)
        record = self.runner_record(world)
        self.assertEqual(record["last_tick_status"], world_runner.TICK_PAUSED)
        # resume 后继续原目标，且不重走已确认段。
        self.assertFalse(self.pause(paused=False)["control"]["paused"])
        resumed = self.advance(now=self.now + timedelta(seconds=500))
        self.assertEqual(resumed["status"], "started")
        self.assertEqual(resumed["destination_location_id"], s2)
        records = self.travels()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["destination_location_id"], s1)
        self.assertEqual(records[0]["status"], "ARRIVED")
        self.assertEqual(records[1]["destination_location_id"], s2)


    def test_settle_only_never_starts_next_segment(self) -> None:
        """显式 settle-only：只结算已出发段，绝不发起下一段（与持久暂停独立）。"""
        world = self.world
        s1, s2 = world.ids["s1"], world.ids["s2"]
        environment = self.run_env(world)
        self.accept(world.ids["s4"])
        before = self.owner_bytes()
        # 第一段还在途：settle-only 不结算、不启动，owner 世界零写入。
        early = world_runner.tick(
            home=world.home, environment=environment,
            now=self.now + timedelta(seconds=100), settle_only=True,
        )
        self.assertEqual(early["journey_status"], "in_transit")
        self.assertEqual(self.owner_bytes(), before)
        self.assertEqual(len(self.travels()), 1)
        # 到期：确认到达，但不发起第二段。
        due = world_runner.tick(
            home=world.home, environment=environment,
            now=self.now + timedelta(seconds=300), settle_only=True,
        )
        self.assertEqual(due["journey_status"], "settle_only")
        self.assertEqual(due["tick_status"], world_runner.TICK_OK)
        self.assertEqual(self.position(), s1)
        self.assertEqual(len(self.travels()), 1)
        state = self.journey_state()["active"]
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["segments"][0]["status"], "confirmed")
        self.assertEqual(state["segments"][1]["status"], "pending")
        self.assertNotIn("blocked", json.dumps(state))
        # 没有持久暂停时，普通推进仍然只启动一段。
        started = world_journey.advance_journey(
            hermes_home=world.home, environment=environment,
            now=self.now + timedelta(seconds=300),
        )
        self.assertEqual(started["status"], "started")
        self.assertEqual(started["destination_location_id"], s2)
        self.assertEqual(len(self.travels()), 2)

    def test_pause_resume_without_journey_or_with_terminal_journey(self) -> None:
        world = self.world
        before = self.owner_bytes()
        self.assertTrue(self.pause()["control"]["paused"])
        self.assertFalse(self.pause(paused=False)["control"]["paused"])
        self.assertEqual(self.owner_bytes(), before)
        self.assertFalse(world_journey.state_path(world.home).exists())
        self.assertEqual(self.travels(), [])
        # 完成态旅程：pause/resume 不伪造移动。
        self.accept(world.ids["s4"])
        self.advance(now=self.now + timedelta(seconds=300))
        self.advance(now=self.now + timedelta(seconds=500))
        self.assertEqual(self.advance(now=self.now + timedelta(seconds=650))["status"], "completed")
        bytes_before = self.snapshot()
        self.pause()
        self.pause(paused=False)
        self.assertEqual(self.snapshot(), bytes_before)

    def test_pause_linearizes_against_concurrent_advance(self) -> None:
        world = self.world
        self.accept(world.ids["s4"])          # 第一段已在途：now+300 到期
        self.assertEqual(len(self.travels()), 1)
        import multiprocessing

        context = multiprocessing.get_context("fork")
        barrier = context.Barrier(2)
        results = context.Queue()
        environment = dict(self.run_env(world))
        moment = self.now + timedelta(seconds=300)

        def pauser():
            barrier.wait()
            results.put(("pause", world_runner.set_pause(
                world.home, environment, paused=True, reason="race")))

        def advancer():
            barrier.wait()
            results.put(("advance", world_journey.advance_journey(
                hermes_home=world.home, environment=environment, now=moment)))

        processes = [context.Process(target=pauser), context.Process(target=advancer)]
        for process in processes:
            process.start()
        payloads = dict(results.get(timeout=60) for _ in processes)
        for process in processes:
            process.join(60)
            self.assertEqual(process.exitcode, 0)
        # 两种次序都必须自洽，且都不得在 pause 确认后启动新段：
        #   pause 先赢 → 第一段照常结算确认，第二段从未启动（travels=1）；
        #   advance 先赢 → 结算第一段并提交第二段（travels=2），但那是 pause
        #                 确认之前就已线性化的提交，之后不得再有第三段。
        control = world_control.read_control(world.home)
        self.assertTrue(control["paused"])
        after = len(self.travels())
        self.assertIn(after, (1, 2))
        self.assertEqual(payloads["pause"]["control"]["paused"], True)
        # 确定性判别：pause 确认后推进（含已出发段到达）不得新增 travel 记录。
        settled = self.advance(now=self.now + timedelta(seconds=500))
        self.assertEqual(settled["status"], "paused")
        self.assertEqual(settled["reason_code"], "JOURNEY_PAUSED")
        self.assertEqual(len(self.travels()), after)
        state = self.journey_state()["active"]
        self.assertIn(state["status"], {"pending", "in_transit"})
        self.assertNotEqual(state["status"], "blocked")
        self.assertEqual(state["segments"][-1]["status"], "pending")
        # 已真正出发的段照常到达：位置落在该段终点，且不再有新段。
        self.assertEqual(self.position(), world.ids["s2"] if after == 2 else world.ids["s1"])
        # 暂停位保持：后续推进不得再启动新段。
        self.assertTrue(world_control.read_control(world.home)["paused"])
        self.assertEqual(self.advance(now=self.now + timedelta(seconds=900))["status"], "paused")
        self.assertEqual(len(self.travels()), after)

    def test_tool_once_and_instant_mode_cannot_bypass_pause(self) -> None:
        world = self.world
        s1, s2, s4 = world.ids["s1"], world.ids["s2"], world.ids["s4"]
        self.pause()
        before = self.owner_bytes()
        # 工具（计时与即时两条路径）都被拒绝。
        timed = self.move(s4)
        self.assertEqual(timed["ok"], False)
        self.assertEqual(timed["status"], "paused")
        self.assertEqual(timed["reason_code"], "JOURNEY_PAUSED")
        instant_env = dict(world.env)
        instant_env["CHIYO_WORLD_TRAVEL_TIME_ENABLED"] = "0"
        instant = self.move(s1, env=instant_env)
        self.assertEqual(instant["ok"], False)
        self.assertEqual(instant["reason_code"], "JOURNEY_PAUSED")
        # 低层 accept / advance 同样被拒绝，且不创建旅程。
        accepted = self.accept(s4)
        self.assertEqual(accepted["status"], "paused")
        self.assertEqual(accepted["reason_code"], "JOURNEY_PAUSED")
        self.assertFalse(world_journey.state_path(world.home).exists())
        self.assertEqual(self.advance()["status"], "none")
        # once 入口只报告 paused，不移动。
        code, out, _ = self.heartbeat(self.once_args(world))
        payload = json.loads(out)
        self.assertEqual(payload["tick_status"], world_runner.TICK_PAUSED)
        self.assertEqual(payload["status"], "none")
        self.assertEqual(code, 0)
        self.assertEqual(self.owner_bytes(), before)
        self.assertEqual(self.position(), "home")
        self.assertEqual(self.travels(), [])

    def test_corrupt_control_state_blocks_new_segments(self) -> None:
        world = self.world
        s2, s3, s4 = world.ids["s2"], world.ids["s3"], world.ids["s4"]
        self.accept(s4)
        self.advance(now=self.now + timedelta(seconds=300))
        control_path = world_control.control_path(world.home)
        control_path.write_text("{not json", encoding="utf-8")
        corrupted = control_path.read_bytes()
        # 已真正出发的第二段允许按真实时间到达并确认（那不是"新段"），
        # 但第三段绝不启动 —— 坏控制状态一律 fail closed。
        blocked = self.advance(now=self.now + timedelta(seconds=500))
        self.assertEqual(blocked["status"], "control_blocked")
        self.assertEqual(blocked["reason_code"], "CONTROL_STATE_CORRUPT")
        self.assertEqual(len(self.travels()), 2)
        self.assertEqual(self.position(), s2)
        self.assertEqual(self.journey_state()["active"]["current_segment"], 2)
        settled = self.owner_bytes()
        # 结算之后：任何入口都不再改动 owner 世界，也不把旅程写成终态。
        again = self.advance(now=self.now + timedelta(seconds=900))
        self.assertEqual(again["status"], "control_blocked")
        self.assertEqual(self.owner_bytes(), settled)
        tool_result = self.move(s3)
        self.assertEqual(tool_result["ok"], False)
        self.assertEqual(tool_result["reason_code"], "CONTROL_STATE_CORRUPT")
        self.assertEqual(self.owner_bytes(), settled)
        code, out, _ = self.heartbeat(self.once_args(world))
        self.assertEqual(json.loads(out)["tick_status"], world_runner.TICK_CONTROL_BLOCKED)
        self.assertEqual(code, 1)
        self.assertEqual(control_path.read_bytes(), corrupted)
        self.assertEqual(len(self.travels()), 2)
        self.assertEqual(self.owner_bytes(), settled)
        # 常驻循环：坏控制状态不算健康，且有限退避（不紧密重试）。
        clock, sleeper, utcnow, state = self.fake_clock(step_seconds=50.0)
        summary = world_runner.run_forever(
            home=world.home, environment=self.run_env(world), interval=1.0,
            clock=clock, sleeper=sleeper, utcnow=utcnow, max_ticks=2,
            install_signal_handlers=False,
        )
        self.assertEqual(summary["last_tick_status"], world_runner.TICK_CONTROL_BLOCKED)
        self.assertEqual(self.runner_record(world)["consecutive_degraded"], 2)
        self.assertGreater(sum(state["waits"]), 2.0)
        self.assertEqual(control_path.read_bytes(), corrupted)
        self.assertEqual(self.owner_bytes(), settled)
        self.assertNotEqual(self.journey_state()["active"]["status"], "blocked")
        # 修复控制状态后仍可继续（非终态拒绝）。
        control_path.unlink()
        resumed = self.advance(now=self.now + timedelta(seconds=900))
        self.assertEqual(resumed["status"], "started")
        self.assertEqual(resumed["destination_location_id"], s4)
        self.assertEqual(len(self.travels()), 3)


@posix_only
class RunnerStatusTest(RunnerTestCase):
    """E：只读状态分类与零写入。"""

    def test_status_missing_directory_and_no_runner_are_zero_create(self) -> None:
        missing = Path(self.temp_dir(prefix="w03-missing-")) / "not-there"
        report = world_runner.runner_status(missing, environment={})
        self.assertEqual(report["runner"]["classification"], world_runner.CLASS_NOT_RUNNING)
        self.assertEqual(report["world"]["world_home_state"], "missing")
        self.assertFalse(missing.exists())
        world = self.world
        listing = sorted(path.name for path in self.state_dir(world).iterdir())
        report = self.status()
        self.assertEqual(report["runner"]["classification"], world_runner.CLASS_NOT_RUNNING)
        self.assertEqual(report["runner"]["record_state"], "absent")
        self.assertEqual(report["world"]["world_home_state"], "initialized")
        self.assertEqual(
            sorted(path.name for path in self.state_dir(world).iterdir()), listing
        )
        self.assertFalse(world_runner.runtime_path(world.home).exists())
        self.assertFalse(world_runner.lease_path(world.home).exists())

    def test_status_is_read_only_and_never_settles_arrivals(self) -> None:
        world = self.world
        self.accept(world.ids["s4"])
        before = self.snapshot()
        mtimes = self.mtimes()
        for moment in (self.now + timedelta(seconds=10), self.now + timedelta(seconds=300),
                       self.now + timedelta(seconds=301), self.now + timedelta(days=5)):
            report = self.status(now=moment)
            self.assertEqual(report["runner"]["classification"], world_runner.CLASS_NOT_RUNNING)
            self.assertEqual(report["journey"]["status"], "in_transit")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.mtimes(), mtimes)
        self.assertEqual(self.position(), "home")
        self.assertEqual(self.world_revision(), 1)
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(self.travels()[0]["status"], "IN_PROGRESS")

    def test_status_reports_running_paused_stopped_and_crashed(self) -> None:
        world = self.world
        child = self.spawn(
            "with world_runner.run_lease(HOME):\n"
            "    print('LEASED', flush=True)\n"
            "    world_runner.run_forever(\n"
            "        home=HOME, environment=ENV, interval=0.05,\n"
            "        on_tick=lambda outcome: print('TICK', flush=True))\n",
            world,
        )
        try:
            self.await_marker(child, "LEASED")
            self.await_marker(child, "TICK")
            running = self.status()
            self.assertEqual(running["runner"]["classification"], world_runner.CLASS_RUNNING)
            self.assertEqual(running["runner"]["health"], world_runner.HEALTH_HEALTHY)
            self.assertEqual(running["runner"]["liveness"], world_runner.LIVENESS_HELD)
            self.assertFalse(running["runner"]["pid_is_liveness_proof"])
            self.assertFalse(running["runner"]["stale"])
            self.assertIn("lease", running["runner"]["liveness_evidence"])
            self.assertTrue(running["runner"]["instance_id"].startswith("run-"))
            # 暂停后状态如实反映（runner 仍在线）。
            self.pause()
            paused = self.status()
            self.assertEqual(paused["control"]["paused"], True)
            self.assertEqual(paused["control"]["state"], world_control.STATE_OK)
            self.pause(paused=False)
            child.send_signal(signal.SIGTERM)
            code, _, _ = self.reap(child, timeout=60)
            self.assertEqual(code, 0)
            stopped = self.status()
            self.assertEqual(stopped["runner"]["classification"], world_runner.CLASS_STOPPED)
            self.assertEqual(stopped["runner"]["health"], world_runner.HEALTH_STOPPED)
            self.assertEqual(stopped["runner"]["stop_reason"], "SIGTERM")
            self.assertEqual(stopped["runner"]["liveness"], world_runner.LIVENESS_FREE)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=30)
                self.close_pipes(child)
        # 崩溃：kill -9 后记录仍写 running，但 lease 已释放 → crashed（不是健康）。
        crashed_child = self.spawn(
            "with world_runner.run_lease(HOME):\n"
            "    print('LEASED', flush=True)\n"
            "    world_runner.run_forever(\n"
            "        home=HOME, environment=ENV, interval=0.05,\n"
            "        on_tick=lambda outcome: print('TICK', flush=True))\n",
            world,
        )
        self.await_marker(crashed_child, "LEASED")
        self.await_marker(crashed_child, "TICK")
        crashed_child.kill()
        crashed_child.wait(timeout=30)
        self.close_pipes(crashed_child)
        crashed = self.status()
        self.assertEqual(crashed["runner"]["classification"], world_runner.CLASS_CRASHED)
        self.assertEqual(crashed["runner"]["health"], world_runner.HEALTH_CRASHED)
        self.assertNotEqual(crashed["runner"]["health"], world_runner.HEALTH_HEALTHY)

    def test_stale_record_and_forged_pid_are_not_healthy(self) -> None:
        world = self.world
        # 陈旧记录：租约仍被本进程持有，但 last_tick_at 早已过期。
        with world_runner.run_lease(world.home):
            world_runner.write_runtime(world.home, {
                "schema_version": world_runner.RUNNER_SCHEMA,
                "instance_id": "run-stale-1",
                "pid": os.getpid(),
                "world_id": "shiomi_city",
                "started_at": world_runner._timestamp(self.now - timedelta(hours=2)),
                "interval_seconds": 1.0,
                "stale_after_seconds": 30.0,
                "status": world_runner.RUNNER_RUNNING,
                "tick_count": 5,
                "last_tick_at": world_runner._timestamp(self.now - timedelta(hours=1)),
                "last_tick_status": world_runner.TICK_OK,
                "last_tick_reason": "TRAVEL_STARTED",
                "consecutive_degraded": 0,
                "next_tick_at": None,
                "stopped_at": None,
                "stop_reason": None,
            })
            stale = self.status()
        self.assertEqual(stale["runner"]["classification"], world_runner.CLASS_STALE)
        self.assertEqual(stale["runner"]["health"], world_runner.HEALTH_STALE)
        self.assertTrue(stale["runner"]["stale"])
        self.assertIn("RUNNER_TICKS_STALE", stale["reason_codes"])
        # 伪造 PID：记录说 running，但没有任何进程持有 lease → 不得视为健康/在线。
        world_runner.write_runtime(world.home, {
            "schema_version": world_runner.RUNNER_SCHEMA,
            "instance_id": "run-forged-1",
            "pid": 999999,
            "world_id": "shiomi_city",
            "started_at": world_runner._timestamp(self.now),
            "interval_seconds": 1.0,
            "stale_after_seconds": 30.0,
            "status": world_runner.RUNNER_RUNNING,
            "tick_count": 1,
            "last_tick_at": world_runner._timestamp(self.now),
            "last_tick_status": world_runner.TICK_OK,
            "last_tick_reason": "TRAVEL_STARTED",
            "consecutive_degraded": 0,
            "next_tick_at": None,
            "stopped_at": None,
            "stop_reason": None,
        })
        forged = self.status()
        self.assertEqual(forged["runner"]["classification"], world_runner.CLASS_CRASHED)
        self.assertNotEqual(forged["runner"]["health"], world_runner.HEALTH_HEALTHY)
        self.assertFalse(forged["runner"]["pid_is_liveness_proof"])
        self.assertEqual(forged["runner"]["liveness"], world_runner.LIVENESS_FREE)
        # 损坏记录：明确 unknown，不显示健康。
        world_runner.runtime_path(world.home).write_text("{broken", encoding="utf-8")
        corrupt = self.status()
        self.assertEqual(corrupt["runner"]["classification"], world_runner.CLASS_UNKNOWN)
        self.assertEqual(corrupt["runner"]["record_state"], "corrupt")
        self.assertEqual(corrupt["runner"]["record_error"], "RUNNER_STATE_UNREADABLE")
        self.assertNotEqual(corrupt["runner"]["health"], world_runner.HEALTH_HEALTHY)

    def test_status_output_is_sanitized(self) -> None:
        world = self.world
        canary = "W03-CANARY-SECRET"
        with patch.dict(os.environ, {"CHIYO_TEST_CANARY": canary}):
            report = self.status()
        payload = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(canary, payload)
        self.assertNotIn("environment", report)
        self.assertNotIn("CHIYO_WORLD_PACKAGE", payload)


@posix_only
class RunnerRefusalTest(RunnerTestCase):
    """F：装配/参数拒绝必须零创建、零移动。"""

    def test_invalid_world_homes_are_refused_without_creating(self) -> None:
        world = self.world
        missing = Path(self.temp_dir(prefix="w03-missing-")) / "nope"
        code, out, err = self.heartbeat(["run", "--world-home", str(missing),
                                         "--max-ticks", "1"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("WORLD_HOME_MISSING", err)
        self.assertFalse(missing.exists())
        not_a_dir = Path(self.temp_dir(prefix="w03-file-")) / "file"
        not_a_dir.write_text("x", encoding="utf-8")
        code, _, err = self.heartbeat(["once", "--world-home", str(not_a_dir)])
        self.assertEqual(code, 2)
        self.assertIn("WORLD_HOME_NOT_DIRECTORY", err)
        uninitialized = Path(self.temp_dir(prefix="w03-empty-"))
        code, _, err = self.heartbeat(["once", "--world-home", str(uninitialized)])
        self.assertEqual(code, 2)
        self.assertIn("WORLD_HOME_NOT_INITIALIZED", err)
        self.assertEqual(sorted(path.name for path in uninitialized.iterdir()), [])
        code, _, err = self.heartbeat(["once", "--world-home", str(ROOT)])
        self.assertEqual(code, 2)
        self.assertIn("WORLD_HOME_IN_SOURCE_TREE", err)
        # 属于另一个世界的 home：身份不匹配即拒绝，不移动。
        other = self.make_world(seed=20261014, prefix="w03-other-")
        code, _, err = self.heartbeat([
            "once", "--world-home", str(other.home),
            "--world-package", str(world.package_dir),
            "--env", "CHIYO_WORLD_LIVING_SKELETON_ENABLED=1",
            "--env", "CHIYO_WORLD_TRAVEL_TIME_ENABLED=1",
        ])
        self.assertEqual(code, 2)
        self.assertIn("WORLD_HOME_IDENTITY_MISMATCH", err)
        self.assertEqual(self.owner_bytes(other), self.owner_bytes(other))

    def test_environment_identity_conflicts_are_refused(self) -> None:
        world = self.world
        before = self.owner_bytes()
        code, out, err = self.heartbeat([
            "once", "--world-home", str(world.home),
            "--env", f"CHIYO_WORLD_HOME={self.temp_dir(prefix='w03-elsewhere-')}",
        ])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("ENVIRONMENT_IDENTITY_CONFLICT", err)
        code, _, err = self.heartbeat([
            "once", "--world-home", str(world.home),
            "--env", f"HERMES_HOME={self.temp_dir(prefix='w03-elsewhere2-')}",
        ])
        self.assertEqual(code, 2)
        self.assertIn("ENVIRONMENT_IDENTITY_CONFLICT", err)
        other = self.make_world(seed=20261014, prefix="w03-pkgconflict-")
        code, _, err = self.heartbeat([
            "once", "--world-home", str(world.home),
            "--world-package", str(world.package_dir),
            "--env", f"CHIYO_WORLD_PACKAGE={other.package_dir}",
        ])
        self.assertEqual(code, 2)
        self.assertIn("ENVIRONMENT_PACKAGE_CONFLICT", err)
        code, _, err = self.heartbeat([
            "once", "--world-home", str(world.home), "--env", "not-a-pair",
        ])
        self.assertEqual(code, 2)
        self.assertIn("INVALID_ENV_OVERRIDE", err)
        self.assertEqual(self.owner_bytes(), before)
        self.assertEqual(self.position(), "home")
        self.assertEqual(self.travels(), [])

    def test_interval_and_fixed_clock_are_refused(self) -> None:
        world = self.world
        for value, reason in (("nan", "INVALID_INTERVAL"), ("inf", "INVALID_INTERVAL"),
                              ("-1", "INVALID_INTERVAL"), ("0.001", "INVALID_INTERVAL"),
                              ("abc", "INVALID_INTERVAL")):
            with self.subTest(interval=value):
                code, out, err = self.heartbeat([
                    "run", "--world-home", str(world.home),
                    "--world-package", str(world.package_dir), "--interval", value,
                    "--max-ticks", "1",
                ])
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn(reason, err)
        code, out, err = self.heartbeat([
            "run", "--world-home", str(world.home),
            "--world-package", str(world.package_dir),
            "--now", self.iso(self.now), "--max-ticks", "1",
        ])
        self.assertEqual(code, 2)
        self.assertIn("FIXED_CLOCK_NOT_ALLOWED", err)
        code, _, err = self.heartbeat([
            "run", "--world-home", str(world.home),
            "--world-package", str(world.package_dir), "--max-ticks", "0",
        ])
        self.assertEqual(code, 2)
        self.assertIn("INVALID_MAX_TICKS", err)
        # 一次也没有创建 runner 记录/租约文件。
        self.assertFalse(world_runner.runtime_path(world.home).exists())
        self.assertFalse(world_runner.lease_path(world.home).exists())
        self.assertEqual(self.travels(), [])

    def test_once_now_must_be_iso_with_timezone(self) -> None:
        world = self.world
        code, _, err = self.heartbeat(["once", "--world-home", str(world.home),
                                       "--world-package", str(world.package_dir),
                                       "--now", "not-a-time"])
        self.assertEqual(code, 2)
        self.assertIn("INVALID_NOW", err)
        code, _, err = self.heartbeat(["once", "--world-home", str(world.home),
                                       "--world-package", str(world.package_dir),
                                       "--now", "2026-10-14T03:00:00"])
        self.assertEqual(code, 2)
        self.assertIn("INVALID_NOW", err)
        self.assertEqual(self.travels(), [])

    def test_feature_gate_off_starts_no_new_segment(self) -> None:
        world = self.world
        s1 = world.ids["s1"]
        self.accept(world.ids["s4"])
        before = self.owner_bytes()
        args = ["once", "--world-home", str(world.home),
                "--world-package", str(world.package_dir),
                "--env", "CHIYO_WORLD_LIVING_SKELETON_ENABLED=1",
                "--env", "CHIYO_WORLD_TRAVEL_TIME_ENABLED=0",
                "--now", self.iso(self.now + timedelta(seconds=300))]
        code, out, _ = self.heartbeat(args)
        payload = json.loads(out)
        self.assertEqual(payload["tick_status"], world_runner.TICK_FEATURE_DISABLED)
        self.assertEqual(code, 0)
        self.assertEqual(self.owner_bytes(), before)
        self.assertEqual(self.position(), "home")
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(self.journey_state()["active"]["status"], "in_transit")
        # gate 恢复后同一次调用继续（非终态拒绝）。
        self.advance(now=self.now + timedelta(seconds=300))
        self.assertEqual(self.position(), s1)


@posix_only
class RunnerDegradedTest(RunnerTestCase):
    """G：坏状态/图变化的降级与严重退出。"""

    def test_corrupt_journey_store_is_severe_nonzero_and_keeps_bytes(self) -> None:
        world = self.world
        self.accept(world.ids["s4"])
        path = world_journey.state_path(world.home)
        path.write_text("{broken", encoding="utf-8")
        corrupted = path.read_bytes()
        code, out, err = self.heartbeat([
            "run", "--world-home", str(world.home),
            "--world-package", str(world.package_dir),
            "--env", "CHIYO_WORLD_LIVING_SKELETON_ENABLED=1",
            "--env", "CHIYO_WORLD_TRAVEL_TIME_ENABLED=1",
            "--interval", "0.05", "--max-ticks", "1",
        ])
        self.assertEqual(code, 3)
        summary = json.loads(out)
        self.assertEqual(summary["stop_reason"], "SEVERE_RUNTIME_STATE")
        self.assertEqual(summary["severe_reason"], "JOURNEY_STATE_CORRUPT")
        record = self.runner_record(world)
        self.assertEqual(record["status"], "stopped")
        self.assertEqual(record["last_tick_status"], world_runner.TICK_SEVERE)
        # 坏记录没有被"修复"或删除。
        self.assertEqual(path.read_bytes(), corrupted)
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(self.position(), "home")

    def test_bad_travel_store_degrades_with_backoff_and_no_cleanup(self) -> None:
        world = self.world
        self.accept(world.ids["s4"])
        travel_path = self.wtd.state_path(world.home)
        travel_path.write_text("{broken", encoding="utf-8")
        corrupted = travel_path.read_bytes()
        target = world.ids["s4"]
        clock, sleeper, utcnow, state = self.fake_clock(step_seconds=100.0)
        marks: list = []
        summary = world_runner.run_forever(
            home=world.home, environment=self.run_env(world), interval=1.0,
            clock=clock, sleeper=sleeper, utcnow=utcnow, max_ticks=4,
            install_signal_handlers=False,
            on_tick=lambda _outcome: marks.append(clock()),
        )
        self.assertEqual(summary["last_tick_status"], world_runner.TICK_DEGRADED)
        self.assertEqual(summary["exit_code"], 0)
        record = self.runner_record(world)
        self.assertEqual(record["consecutive_degraded"], 4)
        self.assertEqual(record["last_tick_reason"], "TRAVEL_STATE_UNREADABLE")
        # 有限退避：连续退化时下一次等待按 interval×2^n 增长（有上限、不紧密重试），
        # 且每次等待被切成 <=WAIT_CHUNK 的片，信号能在 <=chunk 内生效。
        gaps = [round(later - earlier, 3) for earlier, later in zip(marks, marks[1:])]
        self.assertEqual(gaps, [2.0, 4.0, 8.0])
        self.assertGreater(sum(state["waits"]), 8.0)
        self.assertLessEqual(max(state["waits"]), world_runner.WAIT_CHUNK)
        self.assertLessEqual(sum(state["waits"]), world_runner.MAX_BACKOFF * 4)
        self.assertEqual(travel_path.read_bytes(), corrupted)
        self.assertEqual(self.position(), "home")
        # 目标保留、没有随机新建旅程、没有假健康。
        state_payload = self.journey_state()["active"]
        self.assertEqual(state_payload["target_location_id"], target)
        self.assertNotEqual(state_payload["status"], "completed")
        report = self.status()
        self.assertNotEqual(report["runner"]["health"], world_runner.HEALTH_HEALTHY)

    def test_graph_change_degrades_without_discarding_target(self) -> None:
        world = self.world
        s1, s2 = world.ids["s1"], world.ids["s2"]
        self.accept(world.ids["s4"])
        self.change_edge_duration(world, s1, s2, 260)
        code, out, err = self.heartbeat(self.once_args(world))
        payload = json.loads(out)
        self.assertEqual(payload["tick_status"], world_runner.TICK_DEGRADED)
        self.assertEqual(payload["reason_code"], "GRAPH_CHANGED")
        self.assertEqual(code, 1)
        self.assertEqual(payload["target_location_id"], world.ids["s4"])
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(self.position(), "home")


@posix_only
class EnvironmentWiringTest(JourneyTestCase):
    """H：gateway / resolver 的 environment 定向回归。"""

    def test_gateway_move_candidates_use_service_environment(self) -> None:
        from components.world.service.world_body_gateway_api import GatewaySurface

        world = self.make_world(prefix="w03-gw-")
        surface = SimpleNamespace(
            service=SimpleNamespace(config=SimpleNamespace(environment=dict(world.env))),
            _world_live=lambda: {"location": {"place_id": "home"}},
        )
        broken = dict(world.env)
        broken["CHIYO_WORLD_PACKAGE"] = str(self.temp_dir(prefix="w03-broken-") / "missing")
        with patch.dict(os.environ, broken):
            self.wrd.reset_cache()
            candidates = GatewaySurface._reachable_move_candidates(surface)
        self.assertTrue(candidates)
        self.assertEqual([item["location_id"] for item in candidates], [world.ids["s1"]])
        self.assertNotIn("nearby_street", [item["location_id"] for item in candidates])

    def test_resolver_destination_verdict_uses_explicit_environment(self) -> None:
        import world_action_resolver as resolver

        world = self.make_world(prefix="w03-resolver-")
        target = {"kind": "room", "location_id": world.ids["s1"]}
        broken = dict(world.env)
        broken["CHIYO_WORLD_PACKAGE"] = str(self.temp_dir(prefix="w03-broken2-") / "missing")
        with patch.dict(os.environ, broken):
            self.wrd.reset_cache()
            with_env = resolver._m15_destination_verdict(
                target, world_place="home", hermes_home=world.home,
                environment=dict(world.env),
            )
            without_env = resolver._m15_destination_verdict(
                target, world_place="home", hermes_home=world.home, environment=None
            )
        self.assertEqual(with_env, (None, None))
        self.assertEqual(without_env[0], "UNKNOWN_DESTINATION")
        # 旧汐见市兼容：不传 environment 时使用进程环境（汐见市无包）。
        self.wrd.reset_cache()
        shiomi = resolver._m15_destination_verdict(
            {"kind": "room", "location_id": "nearby_street"},
            world_place="home", hermes_home=world.home, environment=_clean_env(),
        )
        self.assertEqual(shiomi, (None, None))


if __name__ == "__main__":
    unittest.main()

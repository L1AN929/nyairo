"""W04：即时模式（关闭真实步行计时）的 pause 提交竞态回归。

被收口的缺陷
============
``world_move_tool.move_handler`` 的即时路径在 W03 里逐段调用
``world_living_skeleton.apply_move_to``，既不取 journey 锁、也不在提交边界重校验
持久暂停闸门。于是出现真实时间序列：

1. 即时 move 读到 gate 放行、选路完成，尚未提交 owner；
2. 另一个线程/进程 ``pause`` 写入并返回成功；
3. 即时 move 继续，把 pause 确认之后的段也提交进 owner 世界。

本文件用**事件/屏障**（不是 sleep 猜时序）确定性地把这个窗口摆出来，断言
“pause 确认后不再提交任何新段”，并且 owner 侧位置改动是真实的：

* ``test_first_segment_not_committed_after_pause``：闸门放行后、首段提交前 pause。
* ``test_mid_route_pause_stops_after_last_confirmed_segment``：多段之间 pause，
  只保留 pause 确认前已完成的那一段。
* ``test_corrupt_control_between_gate_and_commit_fails_closed``：闸门放行后控制
  状态被写坏，同样 fail closed。

观察点是包装函数（``world_journey.journey_lock`` / ``world_living_skeleton.apply_move_to``），
但 pause / 位置改动都走真实 API 与真实 owner 提交。需要 POSIX（fcntl）与 tz 数据库；
原生 Windows 自动跳过。
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_world_journey import JourneyTestCase, posix_only  # noqa: E402

from chiyo_bundle import world_control, world_journey, world_move_tool  # noqa: E402


@posix_only
class InstantPauseRaceTest(JourneyTestCase):
    """即时模式逐段提交与 pause 的线性化。"""

    def instant_env(self, world=None) -> dict:
        world = world or self.world
        env = dict(world.env)
        env["CHIYO_WORLD_TRAVEL_TIME_ENABLED"] = "0"
        return env

    def _run_race(self, *, seam: int, action, world=None):
        """在 move_handler 第 ``seam`` 次取 journey 锁之前，让 ``action`` 先完成。

        返回 ``(result, applied, acquisitions, failures)``：

        * ``result``：工具返回的 JSON 对象；
        * ``applied``：真实提交过的 owner 段（``apply_move_to`` 的入参顺序）；
        * ``acquisitions``：即时循环实际取得的 journey 锁次数；
        * ``failures``：同步失败（用于断言，不参与产品判定）。
        """
        world = world or self.world
        env = self.instant_env(world)
        original_lock = world_journey.journey_lock
        original_apply = self.wls.apply_move_to
        applied: list = []
        acquisitions = {"count": 0}
        at_seam = threading.Event()
        action_done = threading.Event()
        failures: list = []

        def helper():
            if not at_seam.wait(timeout=60):
                failures.append("move_handler never reached the observed commit boundary")
                action_done.set()
                return
            try:
                action(original_lock, world)
            except BaseException as exc:  # pragma: no cover - 夹具失败
                failures.append(f"helper action failed: {exc!r}")
            finally:
                action_done.set()

        @contextlib.contextmanager
        def observed_lock(home):
            acquisitions["count"] += 1
            if acquisitions["count"] == seam:
                at_seam.set()
                if not action_done.wait(timeout=60):
                    failures.append("helper action never completed")
            with original_lock(home):
                yield

        def recording_apply(step, **options):
            applied.append(step)
            return original_apply(step, **options)

        thread = threading.Thread(target=helper, daemon=True)
        thread.start()
        try:
            with mock.patch.object(world_journey, "journey_lock", observed_lock), \
                    mock.patch.object(self.wls, "apply_move_to", recording_apply):
                raw = world_move_tool.move_handler(
                    {"location": world.ids["s4"]}, environment=env, now=self.now
                )
        finally:
            # 负对照（未修复的即时路径从不取 journey 锁）下也要让 helper 收尾，
            # 失败判定交给行为断言，而不是靠等待超时冒充。
            at_seam.set()
            thread.join(timeout=60)
        return json.loads(raw), applied, acquisitions["count"], failures

    # -- 竞态 1：闸门放行后、首段提交前 pause --------------------------------

    def test_first_segment_not_committed_after_pause(self) -> None:
        world = self.world
        before = self.owner_bytes()

        def pause_at_boundary(lock, target_world):
            with lock(target_world.home):
                world_control.set_paused(
                    target_world.home, paused=True,
                    now=self.now + timedelta(seconds=1), reason="w04-race",
                )

        result, applied, acquisitions, failures = self._run_race(
            seam=1, action=pause_at_boundary
        )

        self.assertEqual(failures, [])
        # pause 真实落盘并返回成功。
        self.assertTrue(world_control.read_control(world.home)["paused"])
        # pause 确认后不再提交任何新段：owner 世界与位置都没动。
        self.assertEqual(applied, [])
        self.assertEqual(acquisitions, 1)
        self.assertEqual(self.owner_bytes(), before)
        self.assertEqual(self.position(), "home")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["reason_code"], "JOURNEY_PAUSED")
        self.assertEqual(result["committed_route"], [])
        self.assertEqual(result["location_id"], "home")
        # 绝不虚报到达。
        self.assertNotEqual(result["status"], "arrived")

    # -- 竞态 2：多段路线中途 pause ------------------------------------------

    def test_mid_route_pause_stops_after_last_confirmed_segment(self) -> None:
        world = self.world
        s1 = world.ids["s1"]
        self.assertEqual(self.wrd.active_world(world.env).route_to("home", world.ids["s4"]),
                         [s1, world.ids["s2"], world.ids["s4"]])

        def pause_at_boundary(lock, target_world):
            with lock(target_world.home):
                world_control.set_paused(
                    target_world.home, paused=True,
                    now=self.now + timedelta(seconds=1), reason="w04-mid-route",
                )

        result, applied, acquisitions, failures = self._run_race(
            seam=2, action=pause_at_boundary
        )

        self.assertEqual(failures, [])
        self.assertTrue(world_control.read_control(world.home)["paused"])
        # 每个即时段各取一次 journey 锁 → pause 能在两段之间线性化。
        self.assertEqual(acquisitions, 2)
        # pause 确认前已完成的那一段保留；之后不再提交下一段。
        self.assertEqual(applied, [s1])
        self.assertEqual(self.position(), s1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["reason_code"], "JOURNEY_PAUSED")
        self.assertEqual(result["committed_route"], [s1])
        self.assertEqual(result["location_id"], s1)
        self.assertEqual(result["route"], [s1, world.ids["s2"], world.ids["s4"]])
        self.assertNotEqual(result["status"], "arrived")

    # -- 竞态 3：闸门放行后控制状态损坏 → fail closed ------------------------

    def test_corrupt_control_between_gate_and_commit_fails_closed(self) -> None:
        world = self.world
        before = self.owner_bytes()
        control_path = world_control.control_path(world.home)

        def corrupt_at_boundary(lock, target_world):
            with lock(target_world.home):
                control_path.write_text("{not json", encoding="utf-8")

        result, applied, acquisitions, failures = self._run_race(
            seam=1, action=corrupt_at_boundary
        )

        self.assertEqual(failures, [])
        self.assertEqual(applied, [])
        self.assertEqual(acquisitions, 1)
        self.assertEqual(self.owner_bytes(), before)
        self.assertEqual(self.position(), "home")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "control_blocked")
        self.assertEqual(result["reason_code"], "CONTROL_STATE_CORRUPT")
        self.assertEqual(result["committed_route"], [])


if __name__ == "__main__":
    unittest.main()

"""真实本地 World 服务 + 迟到的响应：owner 已经提交，客户端只超时一次。

实证缺陷（fixture /tmp/nyaw05r.jdK3q/w05inst-fgon6ibm，未改动任何字节）：
``submit_action`` 的 2.0s 读超时在 owner 落账之后才到期，客户端把一次**已经
提交**的 MOVE 报成 ``transport_fault:TIMEOUT``；旧代码既不看 owner 的账本，也不
区分"忙但健康"和"真的坏了"。

本文件用真实本地栈证明修复后的合同（没有真实模型、没有外网）：

* 真 ``WorldBodyService``（真 gateway.sock、真授权租约、真 Resolver、真 travel 记录）；
* owner 的提交**先**完成并落账，然后响应被事件屏障挂住（不是 sleep 竞速）；
* 客户端的提交 deadline 只有 0.3s，所以它必然超时；
* 超时后客户端用**同一个 execution id** 向 owner 读一次判决 → SUCCESS；
* 端到端只发生一次提交：gateway 只收到一次 submit_action，账本 attempts=1，
  travel 只有一条。

夹具根落在 ``/tmp/nyaw05r.jdK3q/owner-unk-*``，永不删除、不清理。
需要 POSIX（fcntl + AF_UNIX）；原生 Windows 自动跳过。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.worldgen.instance import init_instance  # noqa: E402
from test_one_day_life import WorldGateway, posix_only  # noqa: E402

import action_intent as ai  # noqa: E402
import intent_executor as ie  # noqa: E402
import world_body_client as wbc  # noqa: E402
import world_body_gateway_api as gapi  # noqa: E402
import world_living_skeleton as wls  # noqa: E402
import world_runtime_data as wrd  # noqa: E402
import world_travel_delivery as wtd  # noqa: E402

FIXTURE_ROOT = Path("/tmp/nyaw05r.jdK3q")
WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
NOW = datetime(2026, 10, 17, 7, 0, 12, tzinfo=timezone.utc)


def _fixture_root() -> Path:
    """唯一的、保留的夹具根（创建一次，永不删除）。"""

    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_ROOT / ("owner-unk-" + uuid.uuid4().hex[:12])
    root.mkdir(parents=True, exist_ok=False)
    return root


@posix_only
class SlowOwnerVerdictTest(unittest.TestCase):
    def setUp(self) -> None:
        self.saved_env = dict(os.environ)
        self.root = _fixture_root()
        self.home = self.root / "world-home"
        init_instance(WORLD_PACKAGE, self.home)
        world_state = json.loads(
            (self.home / "data" / "world" / "world_state.json").read_text("utf-8"))
        self.world_id = world_state["world_id"]
        (self.home / "run").mkdir(parents=True, exist_ok=True)
        self.env = {
            "CHIYO_WORLD_PACKAGE": str(WORLD_PACKAGE),
            "CHIYO_WORLD_HOME": str(self.home),
            "CHIYO_WORLD_ID": self.world_id,
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
            "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
            "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
            "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
            "WORLD_ACTION_AUTH_REGISTRY_DIR": str(self.home / "run"),
            "WORLD_ACTION_LIFECYCLE_PATH": str(self.home / "run" / "action_lifecycle.jsonl"),
            ai.GATE_ENV: ai.GATE_CANONICAL,
        }
        os.environ.update(self.env)
        self.gateway = WorldGateway(self.home, world_id=self.world_id,
                                    environment=self.env)
        self.commit_done = threading.Event()
        self.release = threading.Event()
        self.gateway_submissions = []

    def tearDown(self) -> None:
        self.release.set()
        try:
            self.gateway.stop()
        finally:
            os.environ.clear()
            os.environ.update(self.saved_env)
            wrd.reset_cache()

    # -- 夹具 ---------------------------------------------------------------

    def _origin_and_destination(self) -> tuple[str, str]:
        world = wrd.active_world(self.env)
        origin = wls.read_move_state(hermes_home=self.home, environment=self.env,
                                     now=NOW)["world_location_id"]
        destinations = sorted(world.reachable_of(origin))
        self.assertTrue(destinations, "世界中没有可达地点")
        return origin, destinations[0]

    @staticmethod
    def _intent(execution_id: str, destination: str) -> dict:
        """与 ``action_intent.build_intent`` 同形的最小意图（手工构造，不经模型）。"""

        return {
            "kind": ai.KIND_INTENT,
            "schema_version": ai.SCHEMA_INTENT,
            "intent_id": "world-intent:owner-unk-" + execution_id.rsplit(":", 1)[-1],
            "decision_ref": None,
            "actor_id": "chiyo",
            "action": "SELECT",
            "verb": "MOVE",
            "params": {"destination_location_id": destination},
            "target_summary": "去 %s" % destination,
            "created_at": "2026-10-17T07:00:12Z",
            "source_provenance": ai.SOURCE,
            "authorization_ref": "pending",
            "execution_id": execution_id,
            "intent_status": ai.STATUS_AUTHORIZED,
            "snapshot_fingerprint": "fixture:owner-unknown",
        }

    def _hold_the_reply_after_the_real_commit(self):
        """把 gateway 的响应挂住：真实提交已经落账，只是回执迟到。

        事件屏障而不是 sleep 竞速 —— 谁先谁后由"提交是否已经返回"决定。
        """

        real_submit = gapi.GatewaySurface.submit_action

        def slow_submit(surface, request):
            result = real_submit(surface, request)  # 真实 Resolver 提交 + 账本 committed
            self.gateway_submissions.append(result)
            self.commit_done.set()
            self.release.wait(30)
            return result

        return mock.patch.object(gapi.GatewaySurface, "submit_action", slow_submit)

    def _release_once_the_client_gave_up(self, client, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and client.metrics.submitted == 0:
            time.sleep(0.005)
        self.release.set()

    # -- 正例：owner 已提交，迟到的回执不制造第二次提交 ---------------------

    def test_a_late_reply_after_a_real_commit_is_resolved_without_a_second_submit(self):
        origin, destination = self._origin_and_destination()
        execution_id = "world-exec:owner-unk-slow-1"
        client = wbc.WorldBodyClient(socket_path=str(self.gateway.socket_path),
                                     mode=wbc.MODE_CANONICAL, timeout=2.0,
                                     mutation_timeout=0.3, read_retries=0)
        watchdog = threading.Thread(
            target=self._release_once_the_client_gave_up, args=(client,), daemon=True)

        started = time.monotonic()
        with self._hold_the_reply_after_the_real_commit():
            watchdog.start()
            result = ie.execute_intent(self._intent(execution_id, destination),
                                       client=client)
        watchdog.join(timeout=35)
        elapsed = time.monotonic() - started

        # 客户端在 owner 落账之后放弃了等待（这正是原始缺陷的时间线）。
        self.assertTrue(self.commit_done.is_set(), "真实 owner 提交没有发生")
        self.assertEqual(result["outcome"], wbc.SUCCESS, result)
        self.assertEqual(result["intent_status"], ai.STATUS_APPLIED, result)
        # 判决来自 owner 的账本查询，而不是那次丢掉的回执。
        self.assertEqual(result["reason"], "owner_verdict:SUCCESS", result)
        self.assertGreaterEqual(elapsed, 0.25, "提交没有真的等满自己的 deadline")

        # 恰好一次提交：gateway 只收到一次，客户端的提交计数也是 1。
        self.assertEqual(len(self.gateway_submissions), 1)
        self.assertEqual(client.metrics.submitted, 1)
        # 判决来自 owner 的账本：一次只读查询，不是第二次提交。
        self.assertGreaterEqual(client.metrics.calls, 2)
        self.assertEqual(client.metrics.action_success, 1)

        # owner 侧证据：账本 committed/move_departed，travel 恰好一条。
        view = self.gateway.service.resolver.reconcile(execution_id)
        self.assertTrue(view["known"], view)
        self.assertEqual(view["verdict"], "SUCCESS", view)
        self.assertEqual(view["state"], "committed", view)
        self.assertEqual(view["attempts"], 1, view)

        state = wtd.load_state(self.home, self.env)
        records = [item for item in state["travels"]
                   if item["execution_id"] == execution_id]
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0]["origin_location_id"], origin)
        self.assertEqual(records[0]["destination_location_id"], destination)
        self.assertEqual(records[0]["status"], "IN_PROGRESS")
        # 位置权威仍在 owner 手里：出发不等于到达。
        self.assertEqual(
            wls.read_move_state(hermes_home=self.home, environment=self.env,
                                now=NOW + timedelta(seconds=1))["world_location_id"],
            origin,
        )

    # -- 负例：owner 还没判定时，客户端保持疑问，也只提交一次 ---------------

    def test_a_slow_owner_that_has_not_decided_keeps_the_doubt_and_submits_once(self):
        _origin, destination = self._origin_and_destination()
        execution_id = "world-exec:owner-unk-slow-2"
        client = wbc.WorldBodyClient(socket_path=str(self.gateway.socket_path),
                                     mode=wbc.MODE_CANONICAL, timeout=2.0,
                                     mutation_timeout=0.3, read_retries=0)

        # 这里不挂住回执，而是让 owner 干脆不落账：查询必须回 NOT_FOUND/UNKNOWN，
        # 客户端只能保持 UNKNOWN —— 不伪造成功、也不重发。
        def never_decides(*_args, **_kwargs):
            return {"kind": "world_body_gateway_surface", "status": "uncertain",
                    "reason_code": "RECONCILIATION_REQUIRED"}

        with mock.patch.object(gapi.GatewaySurface, "submit_action", never_decides):
            result = ie.execute_intent(self._intent(execution_id, destination),
                                       client=client)

        self.assertEqual(result["outcome"], wbc.UNKNOWN, result)
        self.assertEqual(result["intent_status"], ai.STATUS_UNKNOWN, result)
        self.assertEqual(client.metrics.submitted, 1)
        view = self.gateway.service.resolver.reconcile(execution_id)
        self.assertFalse(view["known"], view)
        self.assertEqual(view["verdict"], "NOT_FOUND", view)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)

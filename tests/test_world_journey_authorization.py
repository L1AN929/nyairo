"""多段旅程逐动作正式许可：跨段每一步都走 World 自己的正式授权入口。

承重边界（见 ``2026-10-17-world-journey-ambiguous-submission.md``）：

* 首段授权只覆盖**一次** MOVE；同一趟旅程的第二、三段是新的 Action，必须各自在
  dispatch 时经 ``Intent Executor → gateway.sock → Resolver`` 取得并消费一次性授权。
* 已提交在途的段只按 owner 证据结算，不需要、也不重新铸一张票。
* UNKNOWN 只对账，不重发、不换 execution id、不消费新票。
* gate 关闭时下一段不得出发；缺正式口时失败关闭，不 fallback 到低级本地写。

这些用例用真实 Life cycle / 真实 World gateway / 真实 Resolver 跑，不手造
authorization 通过结论。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import (  # noqa: E402
    StubCognition, StubObservation, StubWriter, _current_place, posix_only,
)
from test_one_day_life_w05r import W05RTestCase  # noqa: E402

import autonomous_life_cycle as alc  # noqa: E402
import life_pause  # noqa: E402
from chiyo_bundle import world_journey as journey  # noqa: E402

JOURNEY_RELATIVE = ("data", "world", "world_journey.json")
TRAVEL_RELATIVE = ("data", "world", "world_travel_delivery.json")
REGISTRY_NAME = "action_authorizations.json"


class _DropReceiptPort:
    """包住真实正式口：只把第一次 ``applied`` 回执换成 uncertain。

    模拟"owner 已提交、回执在路上丢了"。授权与 travel 记录都是真的，因此后续
    推进只能按同一 execution id 对账认领，不能重新提交。
    """

    def __init__(self, inner, *, really_submit: bool = True) -> None:
        self.inner = inner
        self.really_submit = really_submit
        self.calls: list[dict] = []
        self.dropped = False

    @property
    def submissions(self) -> int:
        return getattr(self.inner, "submissions", 0)

    @property
    def last_intent(self):
        return getattr(self.inner, "last_intent", None)

    @property
    def last_result(self):
        return getattr(self.inner, "last_result", None)

    def submit_move(self, **kwargs):
        self.calls.append(dict(kwargs))
        if not self.really_submit and not self.dropped:
            self.dropped = True
            return {"kind": "MOVE_TO", "status": "uncertain", "formal": True,
                    "reason_code": "UNKNOWN", "detail": "transport_fault:GATEWAY_TIMEOUT"}
        result = self.inner.submit_move(**kwargs)
        if not self.dropped and str(result.get("status")) == "applied":
            self.dropped = True
            return {"kind": "MOVE_TO", "status": "uncertain", "formal": True,
                    "reason_code": "UNKNOWN", "detail": "transport_fault:RECEIPT_LOST"}
        return result


@posix_only
class JourneySegmentAuthorizationTest(W05RTestCase):
    """真实 World gateway 上的逐段授权。"""

    # -- 夹具 ---------------------------------------------------------------

    def _fixture(self):
        data = self._prepared_instance()
        env = self.instance_env()
        cognition, writer = StubCognition("move"), StubWriter()
        runtime, _port = self._runtime(cognition, env, data)
        cycle = alc.AutonomousLifeCycle(
            runtime, artifact_port=None,
            world_opportunity_provider=StubObservation(env),
            writer=writer, enabled=True, env=env)
        port = runtime.world_action_adapter.resolver.submit_port
        return SimpleNamespace(runtime=runtime, cycle=cycle, env=env, data=data, port=port)

    def _longest_route(self, env):
        import world_runtime_data as wrd

        world = wrd.active_world(env)
        origin = _current_place(self.world_home())
        seen, frontier = {origin}, [origin]
        while frontier:
            node = frontier.pop()
            for nxt in world.reachable_of(node):
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        target = max(seen, key=lambda dest: len(world.route_to(origin, dest) or [origin]))
        return target, list(world.route_to(origin, target) or [])

    def _journey(self):
        return json.loads(self._journey_path().read_text(encoding="utf-8"))

    def _travels(self):
        path = self.world_home().joinpath(*TRAVEL_RELATIVE)
        if not path.exists():
            return []
        return list(json.loads(path.read_text(encoding="utf-8")).get("travels") or [])

    def _authorizations(self, env):
        path = Path(env["WORLD_BODY_SOCKET"]).parent / REGISTRY_NAME
        if not path.exists():
            return []
        return list(json.loads(path.read_text(encoding="utf-8")).get("records") or [])

    def _accept(self, fixture, target, *, port=None):
        return journey.accept_journey(
            target=target, hermes_home=self.world_home(), environment=fixture.env,
            submit_port=fixture.port if port is None else port,
        )

    def _settle(self, fixture, index):
        stored = self._journey()
        segment = stored["active"]["segments"][index]
        return fixture.cycle.settle_world_journey(
            observed_at=str(segment["expected_arrival_at"]).replace("Z", "+00:00"))

    # -- 1. 正控：每段独立票/执行/owner 回执 ------------------------------

    def test_each_segment_of_one_route_uses_its_own_formal_authorization(self):
        fixture = self._fixture()
        target, route = self._longest_route(fixture.env)
        self.assertGreaterEqual(len(route), 2, route)

        accepted = self._accept(fixture, target)
        self.assertEqual(accepted["status"], "started", accepted)
        self.assertEqual(list(accepted["route"]), route)
        self.assertEqual(fixture.port.submissions, 1)
        first = self._journey()["active"]["segments"][0]
        self.assertEqual(first["execution_id"], fixture.port.last_intent["execution_id"])
        self.assertEqual(len(self._travels()), 1)

        # 第二段由真实 Life settle 自动发起（不是测试手传 port）
        second_settle = self._settle(fixture, 0)
        self.assertTrue(second_settle["ok"], second_settle)
        self.assertIn(second_settle["journey_status"], {"started", "in_transit"},
                      second_settle)
        stored = self._journey()["active"]
        second = stored["segments"][1]
        self.assertEqual(second["status"], "started")
        self.assertEqual(fixture.port.submissions, 2)
        self.assertEqual(second["execution_id"], fixture.port.last_intent["execution_id"])
        self.assertNotEqual(second["execution_id"], first["execution_id"])
        records = self._travels()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["execution_id"], second["execution_id"])
        self.assertEqual(
            [record["execution_id"] for record in records],
            [segment["execution_id"] for segment in stored["segments"][:2]],
        )

        if len(route) >= 3:
            third_settle = self._settle(fixture, 1)
            self.assertIn(third_settle["journey_status"], {"started", "in_transit"},
                          third_settle)
            third = self._journey()["active"]["segments"][2]
            self.assertEqual(fixture.port.submissions, 3)
            self.assertEqual(third["execution_id"], fixture.port.last_intent["execution_id"])
            self.assertEqual(third["execution_id"] not in
                             {first["execution_id"], second["execution_id"]}, True)

        done = self._settle(fixture, len(route) - 1)
        self.assertEqual(done["journey_status"], "completed", done)
        self.assertEqual(_current_place(self.world_home()), target)
        self.assertEqual(len(self._travels()), len(route))

        authorizations = self._authorizations(fixture.env)
        issued = [record for record in authorizations
                  if record.get("execution_id") in
                  {segment["execution_id"] for segment in self._journey()["active"]["segments"]}]
        self.assertEqual(len(issued), len(route))
        self.assertTrue(all(str(record.get("state")).upper() == "CONSUMED"
                            for record in issued))

    # -- 2. gate 关闭：下一段不得出发 ---------------------------------------

    def test_gate_off_after_arrival_never_starts_the_next_segment(self):
        fixture = self._fixture()
        target, route = self._longest_route(fixture.env)
        self.assertGreaterEqual(len(route), 2, route)
        self.assertEqual(self._accept(fixture, target)["status"], "started")
        travels_before = len(self._travels())
        authorizations_before = len(self._authorizations(fixture.env))

        with mock.patch.dict(os.environ, {"WORLD_BODY_INTENT_MUTATION_GATE": "off"}):
            settled = self._settle(fixture, 0)

        self.assertTrue(settled["ok"], settled)
        self.assertEqual(settled["journey_status"], "blocked", settled)
        self.assertEqual(settled["reason_code"], "GATE_OFF", settled)
        # 已到达的首段是合法结算，不回滚、不改记成没发生
        self.assertEqual(_current_place(self.world_home()), route[0])
        stored = self._journey()["active"]
        self.assertEqual(stored["segments"][0]["status"], "confirmed")
        self.assertEqual(stored["segments"][1]["status"], "pending")
        self.assertIsNone(stored["segments"][1]["started_at"])
        # 第二段没有出发、没有铸票、没有新的 travel 或位置副作用
        self.assertEqual(len(self._travels()), travels_before)
        self.assertEqual(len(self._authorizations(fixture.env)), authorizations_before)
        self.assertEqual(fixture.port.submissions, 1)

    # -- 3. 伪票 / 复用首段票 / 旧版本 ---------------------------------------

    def test_a_forged_or_already_consumed_ticket_cannot_start_the_next_segment(self):
        import world_body_client as wbc

        fixture = self._fixture()
        target, route = self._longest_route(fixture.env)
        self.assertGreaterEqual(len(route), 2, route)
        self.assertEqual(self._accept(fixture, target)["status"], "started")
        first = self._journey()["active"]["segments"][0]
        consumed = next(record for record in self._authorizations(fixture.env)
                        if record.get("execution_id") == first["execution_id"])
        self.assertEqual(str(consumed.get("state")).upper(), "CONSUMED")

        # 段间不推进第二段：用首段已消费的票 + 第二段的 execution id 直接提交
        client = wbc.WorldBodyClient(environment=fixture.env)
        destination = route[1]
        forged = client.submit_action(
            action="MOVE", execution_id="world-exec:forged-second-segment",
            params={"destination_location_id": destination}, observed_dependencies={},
            authorization_id="wauth:not-a-real-authorization")
        self.assertEqual(forged.outcome, "FAILURE")
        self.assertEqual(forged.reason, "AUTHORIZATION_NOT_FOUND")

        replay = client.submit_action(
            action="MOVE", execution_id="world-exec:replayed-first-ticket",
            params={"destination_location_id": destination}, observed_dependencies={},
            authorization_id=consumed["authorization_id"])
        self.assertEqual(replay.outcome, "FAILURE")
        self.assertIn(replay.reason,
                      {"AUTHORIZATION_ALREADY_CONSUMED", "EXECUTION_ID_MISMATCH"})
        self.assertEqual(_current_place(self.world_home()), consumed.get("origin", "") or
                         _current_place(self.world_home()))
        self.assertEqual(len(self._travels()), 1)
        self.assertEqual(fixture.port.submissions, 1)

    # -- 4. 缺正式口：失败关闭，不直写 --------------------------------------

    def test_missing_formal_port_fails_closed_without_a_direct_write(self):
        self._prepared_instance()
        env = dict(self.instance_env())
        env["NYAIRO_WORLD_BRIDGE_DIR"] = str(Path(self.temp_dir(prefix="nobridge-")) / "missing")
        before = {
            name: (self.world_home() / "data" / "world" / name).read_bytes()
            if (self.world_home() / "data" / "world" / name).exists() else None
            for name in ("world_state.json", "world_travel_delivery.json")
        }
        target, route = self._longest_route(self.instance_env())
        result = journey.accept_journey(
            target=target, hermes_home=self.world_home(), environment=env)
        self.assertNotEqual(result["status"], "started", result)
        self.assertIn(result["reason_code"],
                      {"SEGMENT_SUBMIT_PORT_UNAVAILABLE", "JOURNEY_UNAVAILABLE"}, result)
        after = {
            name: (self.world_home() / "data" / "world" / name).read_bytes()
            if (self.world_home() / "data" / "world" / name).exists() else None
            for name in ("world_state.json", "world_travel_delivery.json")
        }
        self.assertEqual(after, before, "no direct write may happen without the formal port")

    # -- 5. UNKNOWN 只对账，不重复提交 / 不消费新票 --------------------------

    def test_a_lost_receipt_is_reconciled_under_the_same_execution_only(self):
        fixture = self._fixture()
        target, route = self._longest_route(fixture.env)
        self.assertGreaterEqual(len(route), 2, route)
        port = _DropReceiptPort(fixture.port)
        result = self._accept(fixture, target, port=port)
        # 首段真实提交且 owner 已有 travel，但回执丢失 → 引擎按同 execution 认领
        self.assertIn(result["status"], {"started", "in_transit"}, result)
        self.assertEqual(port.calls[0]["execution_id"], fixture.port.last_intent["execution_id"])
        first_travel = self._travels()
        self.assertEqual(len(first_travel), 1)
        issued_before = len(self._authorizations(fixture.env))

        for _ in range(2):
            self._settle(fixture, 0)
        # 只结算，不重发；同一 execution 的票只被消费一次
        self.assertEqual(len(self._travels()), 2)  # 第二段正常出发一次
        executions = [record.get("execution_id") for record in self._travels()]
        self.assertEqual(len(set(executions)), len(executions))
        self.assertEqual(len(self._authorizations(fixture.env)), issued_before + 1)
        self.assertTrue(all(str(record.get("state")).upper() == "CONSUMED"
                            for record in self._authorizations(fixture.env)))

    def test_an_ambiguous_submission_never_resubmits_and_never_mints(self):
        fixture = self._fixture()
        target, _route = self._longest_route(fixture.env)
        port = _DropReceiptPort(fixture.port, really_submit=False)
        result = self._accept(fixture, target, port=port)
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(len(port.calls), 1)
        self.assertEqual(len(self._travels()), 0)
        self.assertEqual(self._authorizations(fixture.env), [])

        again = journey.advance_journey(
            hermes_home=self.world_home(), environment=fixture.env, submit_port=port)
        self.assertEqual(again["status"], "unknown", again)
        self.assertEqual(len(port.calls), 1, "UNKNOWN must not be resubmitted")
        self.assertEqual(self._authorizations(fixture.env), [])

    # -- 6. pause：只结算已出发，恢复后下一段重新过门 ----------------------

    def test_pause_settles_the_departed_segment_and_never_starts_the_next(self):
        fixture = self._fixture()
        target, route = self._longest_route(fixture.env)
        self.assertGreaterEqual(len(route), 2, route)
        self.assertEqual(self._accept(fixture, target)["status"], "started")
        self.assertEqual(fixture.port.submissions, 1)

        life_pause.set_paused(fixture.cycle.pause_state_root, True, reason="test:auth")
        held = self._settle(fixture, 0)
        self.assertTrue(held["ok"], held)
        self.assertEqual(_current_place(self.world_home()), route[0])
        stored = self._journey()["active"]
        self.assertEqual(stored["segments"][0]["status"], "confirmed")
        self.assertEqual(stored["segments"][1]["status"], "pending")
        self.assertEqual(fixture.port.submissions, 1)

        life_pause.set_paused(fixture.cycle.pause_state_root, False, reason="test:auth")
        resumed = self._settle(fixture, 0)
        self.assertIn(resumed["journey_status"], {"started", "in_transit"}, resumed)
        self.assertEqual(fixture.port.submissions, 2)
        self.assertEqual(self._journey()["active"]["segments"][1]["status"], "started")

    # -- 7. 生产入口全部带上正式口 -----------------------------------------

    def test_every_production_accept_and_advance_call_carries_the_formal_port(self):
        fixture = self._fixture()
        captured: list[tuple[str, dict]] = []

        def fake_advance(**kwargs):
            captured.append(("advance", dict(kwargs)))
            return {"status": "none"}

        def fake_accept(**kwargs):
            captured.append(("accept", dict(kwargs)))
            target = kwargs["target"]
            return {"status": "started", "journey_id": "journey:test",
                    "target_location_id": target, "destination_location_id": target,
                    "route": [target], "estimated_minutes": 5}

        target, _route = self._longest_route(fixture.env)
        with mock.patch.object(journey, "advance_journey", side_effect=fake_advance), \
                mock.patch.object(journey, "accept_journey", side_effect=fake_accept):
            import world_runner

            world_runner.tick(home=self.world_home(), environment=fixture.env)
            import life_heartbeat

            life_heartbeat.advance_once(self.world_home(), fixture.env)
            import world_move_tool as tool

            tool.move_handler({"location": target}, environment=fixture.env)

        kinds = [kind for kind, _kwargs in captured]
        self.assertIn("advance", kinds)
        self.assertIn("accept", kinds)
        for kind, kwargs in captured:
            self.assertIsNotNone(kwargs.get("submit_port"),
                                 "%s was called without the formal port" % kind)


    # -- 8. 到期票：真实 registry + 真实 gateway 拒绝 ---------------------

    def test_an_expired_ticket_is_rejected_by_the_real_gateway_with_zero_effect(self):
        """到期票不是"反正都被消费了"：在真实 gateway 上必须被 EXPIRED 拒绝。"""

        import world_action_authorization as waa
        import world_body_client as wbc

        fixture = self._fixture()
        target, route = self._longest_route(fixture.env)
        origin = _current_place(self.world_home())
        destination = route[0]
        registry = waa.AuthorizationRegistry(
            Path(fixture.env["WORLD_BODY_SOCKET"]).parent)
        params = {"destination_location_id": destination}
        expired = registry.issue(
            decision_id=None, intent_id="world-intent:expired-test",
            intent_fingerprint="fp:expired", execution_id="world-exec:expired-test",
            actor="chiyo", verb="MOVE", params=params, authority_generation=None,
            ttl_seconds=-60,
        )
        self.assertLess(expired["expires_at"], expired["issued_at"])

        client = wbc.WorldBodyClient(environment=fixture.env)
        result = client.submit_action(
            action="MOVE", execution_id="world-exec:expired-test", params=params,
            observed_dependencies={}, authorization_id=expired["authorization_id"])
        self.assertEqual(result.outcome, "FAILURE", result)
        self.assertEqual(result.reason, "AUTHORIZATION_EXPIRED", result)
        # 零副作用：没有 travel、位置没动、旅程没被创建、票仍是 ISSUED（未被消费）
        self.assertEqual(self._travels(), [])
        self.assertEqual(_current_place(self.world_home()), origin)
        self.assertFalse(self._journey_path().exists())
        self.assertEqual(str(registry.lookup(expired["authorization_id"])["state"]).upper(),
                         "ISSUED")

    # -- 9. 段间旧观察版本：真实 executor 铸的票也会被 STALE 拒绝 ----------

    def test_a_stale_inter_segment_observation_is_rejected_not_silently_reused(self):
        import world_body_client as wbc

        fixture = self._fixture()
        target, route = self._longest_route(fixture.env)
        self.assertGreaterEqual(len(route), 2, route)
        self.assertEqual(self._accept(fixture, target)["status"], "started")
        stale_snapshot = wbc.WorldBodyClient(
            environment=fixture.env).get_snapshot()
        self.assertEqual(stale_snapshot.outcome, "OK", stale_snapshot)
        travels_before = len(self._travels())

        # 外部写入者推进世界版本：第二段的授权若仍按旧快照铸出，必须被 Resolver
        # 以 STALE_STATE 拒绝，而不是拿旧版本当有效许可。
        import world_foundation as wf

        path = self.world_home() / "data" / "world" / "world_state.json"
        store = wf.WorldStateStore(path)
        state = store.load()
        state["revision"] = int(state["revision"]) + 1
        store.save(state, expected_revision=state["revision"])

        with mock.patch.object(wbc.WorldBodyClient, "get_snapshot",
                               return_value=stale_snapshot):
            settled = self._settle(fixture, 0)

        self.assertTrue(settled.get("ok"), settled)
        self.assertEqual(settled.get("journey_status"), "blocked", settled)
        self.assertIn("STALE", str(settled.get("reason_code") or ""), settled)
        stored = self._journey()["active"]
        self.assertEqual(stored["segments"][0]["status"], "confirmed")
        self.assertEqual(stored["segments"][1]["status"], "pending")
        # 旧版本票已被 gateway 消费（拒绝发生在消费之后），但没有新的 travel/移动
        self.assertEqual(len(self._travels()), travels_before)
        self.assertEqual(_current_place(self.world_home()), route[0])

    # -- 10. 重启后的 UNKNOWN：只对账、不重发、不铸新票 --------------------

    def test_unknown_survives_a_restart_without_resubmission_or_new_ticket(self):
        fixture = self._fixture()
        target, _route = self._longest_route(fixture.env)
        dropped = _DropReceiptPort(fixture.port, really_submit=False)
        result = self._accept(fixture, target, port=dropped)
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(len(dropped.calls), 1)

        # 重启效果：丢弃旧 cycle/port 对象，只从盘上新构造一个真实正式口。
        from chiyo_bundle import journey_authorization

        restarted = journey_authorization.default_submit_port(
            environment=dict(fixture.env))
        again = journey.advance_journey(
            hermes_home=self.world_home(), environment=fixture.env,
            submit_port=restarted)
        self.assertEqual(again["status"], "unknown", again)
        self.assertEqual(getattr(restarted, "submissions", 0), 0)
        self.assertEqual(len(dropped.calls), 1, "UNKNOWN must not be resubmitted")
        self.assertEqual(self._travels(), [])
        self.assertEqual(self._authorizations(fixture.env), [])

    # -- 11. 服务不可用：失败关闭，没有直写 --------------------------------

    def test_an_unreachable_service_fails_closed_without_a_direct_write(self):
        self._prepared_instance()
        env = dict(self.instance_env())
        env["WORLD_BODY_SOCKET"] = str(self.world_home() / "run" / "missing.sock")
        origin = _current_place(self.world_home())
        before = {
            name: (self.world_home() / "data" / "world" / name).read_bytes()
            if (self.world_home() / "data" / "world" / name).exists() else None
            for name in ("world_state.json", "world_travel_delivery.json")
        }
        target, _route = self._longest_route(self.instance_env())
        result = journey.accept_journey(
            target=target, hermes_home=self.world_home(), environment=env)
        self.assertNotEqual(result["status"], "started", result)
        self.assertEqual(_current_place(self.world_home()), origin)
        after = {
            name: (self.world_home() / "data" / "world" / name).read_bytes()
            if (self.world_home() / "data" / "world" / name).exists() else None
            for name in ("world_state.json", "world_travel_delivery.json")
        }
        self.assertEqual(after, before, "an unreachable service must not write the owner")
        self.assertEqual(self._travels(), [])

    # -- 12. pause + gate off：合法结算已出发段，但绝不启动新段 ------------

    def test_pause_then_gate_off_settles_the_departed_segment_but_never_starts_the_next(self):
        fixture = self._fixture()
        target, route = self._longest_route(fixture.env)
        self.assertGreaterEqual(len(route), 2, route)
        self.assertEqual(self._accept(fixture, target)["status"], "started")
        travels_before = len(self._travels())
        authorizations_before = len(self._authorizations(fixture.env))

        life_pause.set_paused(fixture.cycle.pause_state_root, True, reason="test:pause")
        held = self._settle(fixture, 0)
        self.assertTrue(held["ok"], held)
        self.assertEqual(_current_place(self.world_home()), route[0])
        self.assertEqual(self._journey()["active"]["segments"][1]["status"], "pending")
        self.assertEqual(fixture.port.submissions, 1)

        life_pause.set_paused(fixture.cycle.pause_state_root, False, reason="test:pause")
        with mock.patch.dict(os.environ, {"WORLD_BODY_INTENT_MUTATION_GATE": "off"}):
            blocked = self._settle(fixture, 0)
        self.assertTrue(blocked["ok"], blocked)
        self.assertEqual(blocked["journey_status"], "blocked", blocked)
        self.assertEqual(blocked["reason_code"], "GATE_OFF", blocked)
        # 已出发的首段仍是合法 COMPLETED/confirmed；第二段零出发、零票、零 travel
        self.assertEqual(self._journey()["active"]["segments"][0]["status"], "confirmed")
        self.assertEqual(self._journey()["active"]["segments"][1]["status"], "pending")
        self.assertEqual(fixture.port.submissions, 1)
        self.assertEqual(len(self._travels()), travels_before)
        self.assertEqual(len(self._authorizations(fixture.env)), authorizations_before)


# unittest 会收集继承来的 test_* 方法；父类的用例已经由它自己的模块负责跑，
# 这里只保留本文件新增的授权用例（只置空父类方法名，不影响父模块）。
for _inherited in [name for name in dir(W05RTestCase) if name.startswith("test_")]:
    setattr(JourneySegmentAuthorizationTest, _inherited, None)
del W05RTestCase


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""World 出门被 gate 挡住时：明确"没发"，不伪装成 UNKNOWN，也不改成失败。

覆盖三层：

1. ``fast_day`` 对显式 demo 的 World 写闸门解析/透传：默认 off、显式 off、
   显式 canonical；非法值在起子进程前具名拒绝。只写 resident 自己的 env，
   不改监督进程的 ``os.environ``。
2. Life cycle 对 AR-0 ``PREPARED + PRE_SUBMIT_FAILED`` 的分类：这是"确定没越过
   提交边界"的拒绝（gate off / shadow / invalid），必须报 ``STEP_REFUSED``；
   fenced/submitted 的尝试仍保持 ``STEP_UNSETTLED``，真 UNKNOWN 不被降级。
3. 真 World gateway/registry：gate off 的 POSE 零发送、零票、零副作用；gate
   canonical 的 POSE 真提交并消费一张票；多段旅程每段各自铸/消费一张新票并由
   前台正常结算；丢回执的提交只对账、不重发。
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
    StubCognition, StubObservation, StubWriter, _current_place, posix_only as _posix,
)
from test_one_day_life_w05r import W05RTestCase  # noqa: E402

import action_reality_ledger as ar0  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import fast_day  # noqa: E402
from chiyo_bundle import world_journey as journey  # noqa: E402

JOURNEY_RELATIVE = ("data", "world", "world_journey.json")
TRAVEL_RELATIVE = ("data", "world", "world_travel_delivery.json")
REGISTRY_NAME = "action_authorizations.json"
GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
POSE_TARGET = "bodyact:pose:seated:north"


# ---------------------------------------------------------------------------
# 1. fast_day：闸门值解析与 resident 环境透传
# ---------------------------------------------------------------------------


class FastDayWorldGateTest(unittest.TestCase):
    def test_unset_or_empty_means_the_default_off(self):
        for environment in ({}, {GATE_ENV: ""}, {GATE_ENV: "   "}):
            resolved = fast_day.resolve_world_intent_gate(environment)
            self.assertTrue(resolved["ok"], resolved)
            self.assertEqual(resolved["value"], "off", resolved)
            self.assertEqual(resolved["source"], "default_off", resolved)

    def test_explicit_values_are_normalized_and_kept(self):
        for raw, expected in (("off", "off"), ("CANONICAL", "canonical"),
                              ("shadow", "shadow")):
            resolved = fast_day.resolve_world_intent_gate({GATE_ENV: raw})
            self.assertTrue(resolved["ok"], resolved)
            self.assertEqual(resolved["value"], expected, resolved)
            self.assertEqual(resolved["source"], "explicit", resolved)

    def test_an_unknown_gate_is_refused_by_name(self):
        resolved = fast_day.resolve_world_intent_gate({GATE_ENV: "yes"})
        self.assertFalse(resolved["ok"], resolved)
        self.assertEqual(resolved["reason_code"], fast_day.REASON_WORLD_GATE_INVALID)
        self.assertIn(GATE_ENV, resolved["detail"])

    def _resident_env(self, gate):
        supervisor = fast_day.FastDaySupervisor(Path("/tmp/fastday-gate-test"))
        mode = {"ledger_run_id": "run:gate-test", "domain_id": "fastday:gate-test"}
        binding = {"environment": {"CHIYO_AUTONOMOUS_RUN_ID": "run:gate-test"}}
        with mock.patch.dict(os.environ, {GATE_ENV: gate}, clear=False), \
                mock.patch("components.phone.life.load_phone_instance", return_value={}), \
                mock.patch("components.phone.life.read_nested_life", return_value={}), \
                mock.patch("components.phone.life.build_binding", return_value=binding):
            return supervisor._resident_env(mode, Path("/tmp/fastday-gate-test/state"))

    def test_resident_receives_the_normalized_gate_without_touching_the_parent(self):
        supervisor = fast_day.FastDaySupervisor(Path("/tmp/fastday-gate-test"))
        mode = {"ledger_run_id": "run:gate-test", "domain_id": "fastday:gate-test"}
        binding = {"environment": {"CHIYO_AUTONOMOUS_RUN_ID": "run:gate-test"}}
        with mock.patch.dict(os.environ, {GATE_ENV: "CANONICAL"}, clear=False), \
                mock.patch("components.phone.life.load_phone_instance", return_value={}), \
                mock.patch("components.phone.life.read_nested_life", return_value={}), \
                mock.patch("components.phone.life.build_binding", return_value=binding):
            env = supervisor._resident_env(mode, Path("/tmp/fastday-gate-test/state"))
            self.assertEqual(env[GATE_ENV], "canonical", env)
            # 父进程的选择不被改写/新增：只交给子进程自己的 env。
            self.assertEqual(os.environ.get(GATE_ENV), "CANONICAL")

    def test_resident_defaults_to_off_when_the_supervisor_has_no_gate(self):
        supervisor = fast_day.FastDaySupervisor(Path("/tmp/fastday-gate-test"))
        mode = {"ledger_run_id": "run:gate-test", "domain_id": "fastday:gate-test"}
        binding = {"environment": {"CHIYO_AUTONOMOUS_RUN_ID": "run:gate-test"}}
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch("components.phone.life.load_phone_instance", return_value={}), \
                mock.patch("components.phone.life.read_nested_life", return_value={}), \
                mock.patch("components.phone.life.build_binding", return_value=binding):
            os.environ.pop(GATE_ENV, None)
            env = supervisor._resident_env(mode, Path("/tmp/fastday-gate-test/state"))
        self.assertEqual(env[GATE_ENV], "off", env)

    def test_resident_refuses_an_unknown_gate_before_spawning(self):
        with mock.patch.dict(os.environ, {GATE_ENV: "yes"}, clear=False), \
                mock.patch("components.phone.life.load_phone_instance", return_value={}), \
                mock.patch("components.phone.life.read_nested_life", return_value={}), \
                mock.patch("components.phone.life.build_binding",
                           return_value={"environment": {
                               "CHIYO_AUTONOMOUS_RUN_ID": "run:gate-test"}}):
            with self.assertRaises(RuntimeError) as caught:
                fast_day.FastDaySupervisor(Path("/tmp/fastday-gate-test"))._resident_env(
                    {"ledger_run_id": "run:gate-test"}, Path("/tmp/fastday-gate-test/state"))
        self.assertIn(fast_day.REASON_WORLD_GATE_INVALID, str(caught.exception))


# ---------------------------------------------------------------------------
# 2. cycle 分类：确定没发 = 拒绝；其余未知保持未知
# ---------------------------------------------------------------------------


class PreSubmitClassificationTest(unittest.TestCase):
    @staticmethod
    def _cycle():
        cycle = object.__new__(alc.AutonomousLifeCycle)
        cycle.runtime = SimpleNamespace(
            action_read=SimpleNamespace(list_receipts=lambda _action_id: []))
        cycle.last_outcome = None
        cycle._finish = lambda base, outcome, **kwargs: dict(
            base, outcome=outcome, detail=kwargs.get("detail", ""))
        return cycle

    @staticmethod
    def _out(*, action_status="PREPARED", attempt=None, verb="POSE"):
        submit = {"action": {"status": action_status, "action_id": None,
                             "provider_operation_ref": None,
                             "frozen_payload": {"verb": verb, "target": POSE_TARGET}}}
        if attempt is not None:
            submit["attempt"] = attempt
        return {"step_execution_result": {"submit_result": submit}}

    @staticmethod
    def _production_out(*, action_status="PREPARED", attempt=None, verb="POSE"):
        """``execute_activity_step`` 的真实形状：bridge 输出再嵌一层 ``submit_result``。"""

        action = {"status": action_status, "action_id": "actn:production",
                  "provider_operation_ref": None,
                  "frozen_payload": {"verb": verb, "target": POSE_TARGET}}
        bridge = {"action": action, "receipt": None}
        if attempt is not None:
            bridge["attempt"] = attempt
        return {"step_execution_result": {"submit_result": {
            "submit_result": bridge, "action": action,
            "activity": None, "waiting_applied": False}}}

    def test_the_production_nested_shape_is_also_a_refusal(self):
        """真实链的 attempt 在 bridge 输出内部；只读扁平键时这里曾落回 UNSETTLED。"""

        attempt = {"attempt_id": "aatt:gate-off", "action_id": "actn:production",
                   "status": "PRE_SUBMIT_FAILED",
                   "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                   "error_class": "GATE_OFF", "provider_ref": "world-exec:never-sent"}
        out = self._cycle()._classify_world_step(
            self._production_out(attempt=attempt), {}, "now")
        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_REFUSED, out)
        self.assertEqual(out["pre_submit"]["reason_code"], "GATE_OFF", out)
        self.assertTrue(out["pre_submit"]["not_sent"], out)

    def test_the_nested_shape_without_a_matching_attempt_stays_unsettled(self):
        attempt = {"attempt_id": "aatt:other", "action_id": "actn:other",
                   "status": "PRE_SUBMIT_FAILED",
                   "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                   "error_class": "GATE_OFF"}
        out = self._cycle()._classify_world_step(
            self._production_out(attempt=attempt), {}, "now")
        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_UNSETTLED, out)

    def test_a_definite_pre_submission_failure_is_a_refusal_not_unsettled(self):
        attempt = {"attempt_id": "aatt:gate-off", "status": "PRE_SUBMIT_FAILED",
                   "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                   "error_class": "GATE_OFF", "provider_ref": "world-exec:never-sent"}
        out = self._cycle()._classify_world_step(
            self._out(attempt=attempt), {"observed_at": "2026-10-24T00:00:00Z"}, "now")
        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_REFUSED, out)
        self.assertEqual(out["pre_submit"]["reason_code"], "GATE_OFF", out)
        self.assertTrue(out["pre_submit"]["not_sent"], out)
        self.assertEqual(out["transport_result"],
                         ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT, out)

    def test_a_pre_submit_failure_without_a_code_still_reports_the_not_sent_fact(self):
        attempt = {"attempt_id": "aatt:pre", "status": "PRE_SUBMIT_FAILED",
                   "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                   "error_class": None, "provider_ref": "world-exec:never-sent"}
        out = self._cycle()._classify_world_step(
            self._out(attempt=attempt), {}, "now")
        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_REFUSED, out)
        self.assertEqual(out["pre_submit"]["reason_code"], "PRE_SUBMIT_FAILED", out)
        self.assertTrue(out["pre_submit"]["not_sent"], out)

    def test_a_fenced_or_missing_attempt_stays_unsettled(self):
        for attempt in ({"attempt_id": "aatt:fenced", "status": "FENCED"}, None):
            out = self._cycle()._classify_world_step(
                self._out(attempt=attempt), {}, "now")
            self.assertEqual(out["outcome"], alc.OUTCOME_STEP_UNSETTLED, out)

    def test_a_terminal_action_is_never_relabelled_as_pre_submit(self):
        attempt = {"attempt_id": "aatt:late", "status": "PRE_SUBMIT_FAILED",
                   "transport_result": ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT,
                   "error_class": "GATE_OFF"}
        out = self._cycle()._classify_world_step(
            self._out(action_status="UNKNOWN", attempt=attempt), {}, "now")
        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_UNKNOWN, out)


# ---------------------------------------------------------------------------
# 3. 真 World gateway / registry
# ---------------------------------------------------------------------------


class _LostReceiptPort:
    """真实提交一次，然后把 applied 回执换成 uncertain（回执丢了，但 owner 已出发）。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls = []
        self.dropped = False

    @property
    def submissions(self):
        return getattr(self.inner, "submissions", 0)

    @property
    def last_intent(self):
        return getattr(self.inner, "last_intent", None)

    def submit_move(self, **kwargs):
        self.calls.append(dict(kwargs))
        result = self.inner.submit_move(**kwargs)
        if not self.dropped and str(result.get("status")) == "applied":
            self.dropped = True
            return {"kind": "MOVE_TO", "status": "uncertain", "formal": True,
                    "reason_code": "UNKNOWN", "detail": "transport_fault:RECEIPT_LOST"}
        return result


@_posix
class WorldDispatchChainTest(W05RTestCase):
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

    def _pose(self, env):
        import world_body_client as wbc

        result = wbc.WorldBodyClient(environment=env).get_snapshot()
        self.assertEqual(result.outcome, wbc.OK, result)
        data = dict(result.data or {})
        return {"pose": data.get("pose"), "orientation": data.get("orientation"),
                "world_revision": data.get("world_revision")}

    def test_gate_off_refuses_the_real_world_action_with_zero_effect(self):
        fixture = self._fixture()
        delegate = fixture.runtime.world_action_adapter.resolver.delegate
        before = self._pose(fixture.env)
        with mock.patch.dict(os.environ, {GATE_ENV: "off"}):
            out = delegate.resolve_action(
                idempotency_key="idem:block:pose:off", verb="POSE",
                target=POSE_TARGET, expected_dependencies={})

        self.assertEqual(out["status"], "rejected", out)
        self.assertEqual(out["code"], "GATE_OFF", out)
        self.assertEqual(out["transport_result"],
                         ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT, out)
        # 没铸票、没发 owner、世界没动。
        self.assertEqual(self._authorizations(fixture.env), [])
        self.assertEqual(self._travels(), [])
        self.assertEqual(self._pose(fixture.env), before)

    def test_canonical_gate_really_commits_one_pose_and_consumes_one_ticket(self):
        fixture = self._fixture()
        delegate = fixture.runtime.world_action_adapter.resolver.delegate
        before = self._pose(fixture.env)
        with mock.patch.dict(os.environ, {GATE_ENV: "canonical"}):
            out = delegate.resolve_action(
                idempotency_key="idem:block:pose:on", verb="POSE",
                target=POSE_TARGET, expected_dependencies={})

        self.assertEqual(out["status"], "committed", out)
        after = self._pose(fixture.env)
        self.assertNotEqual((after.get("pose"), after.get("orientation")),
                            (before.get("pose"), before.get("orientation")))
        records = self._authorizations(fixture.env)
        self.assertEqual(len(records), 1, records)
        self.assertEqual(str(records[0].get("state")).upper(), "CONSUMED", records)

    def test_each_canonical_segment_mints_and_consumes_its_own_ticket(self):
        fixture = self._fixture()
        target, route = self._longest_route(fixture.env)
        self.assertGreaterEqual(len(route), 2, route)
        with mock.patch.dict(os.environ, {GATE_ENV: "canonical"}):
            accepted = journey.accept_journey(
                target=target, hermes_home=self.world_home(), environment=fixture.env,
                submit_port=fixture.port)
            self.assertEqual(accepted["status"], "started", accepted)
            first = self._journey()["active"]["segments"][0]
            arrival = str(first["expected_arrival_at"]).replace("Z", "+00:00")
            settled = fixture.cycle.settle_world_journey(observed_at=arrival)

        self.assertTrue(settled["ok"], settled)
        self.assertIn(settled["journey_status"], {"started", "in_transit"}, settled)
        stored = self._journey()["active"]
        second = stored["segments"][1]
        self.assertEqual(second["status"], "started", stored)
        self.assertNotEqual(second["execution_id"], first["execution_id"])
        self.assertEqual(fixture.port.submissions, 2)
        self.assertEqual(len(self._travels()), 2)
        issued = [record for record in self._authorizations(fixture.env)
                  if record.get("execution_id") in
                  {first["execution_id"], second["execution_id"]}]
        self.assertEqual(len(issued), 2, issued)
        self.assertTrue(all(str(record.get("state")).upper() == "CONSUMED"
                            for record in issued), issued)

    def test_a_lost_receipt_is_reconciled_without_resubmission(self):
        fixture = self._fixture()
        target, _route = self._longest_route(fixture.env)
        port = _LostReceiptPort(fixture.port)
        with mock.patch.dict(os.environ, {GATE_ENV: "canonical"}):
            accepted = journey.accept_journey(
                target=target, hermes_home=self.world_home(), environment=fixture.env,
                submit_port=port)
            self.assertIn(accepted["status"], {"started", "in_transit"}, accepted)
            again = journey.advance_journey(
                hermes_home=self.world_home(), environment=fixture.env, submit_port=port,
                settle_only=True)

        self.assertEqual(len(port.calls), 1, "UNKNOWN must not be resubmitted")
        self.assertIn(again["status"], {"in_transit", "unknown", "settle_only"}, again)
        self.assertEqual(len(self._travels()), 1)
        issued = self._authorizations(fixture.env)
        self.assertEqual(len(issued), 1, issued)
        self.assertEqual(str(issued[0].get("state")).upper(), "CONSUMED", issued)


# unittest 会收集继承来的 test_* 方法；父类用例由各自模块负责跑。
for _inherited in [name for name in dir(W05RTestCase) if name.startswith("test_")]:
    setattr(WorldDispatchChainTest, _inherited, None)
del W05RTestCase


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

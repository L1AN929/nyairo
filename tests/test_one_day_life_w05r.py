"""W05R：自主生活收口修正单的定向验证。

覆盖修正单 §1–§5 里被父代理点名的每一个具体缺口，全部对着**真实 owner / 真实服务**跑：

* §1 真正CLI未执行 —— 根因（替身模型给出空 reason_codes，被 AG-1 正确拒绝）在这里
  用真实 ``ProductionAgencyCognitionAdapter`` + loopback 模型钉住；CLI 级证据在
  ``w05r_cli_rehearsal.json``。
* §2 「自己选」被作品固定优先架空 —— 同一实例、同一次候选集里同时有可续作品与已观察
  可达地点，替身选不同 candidate 时分别真的走 Supply owner 与 World owner；真实历史
  结果（新 head / 新位置）改变下一轮的 AG-0 材料与 AG-1 候选。
* §3 World 正式授权 —— 计时段必须经 ``Intent Executor → gateway.sock → Resolver``；
  提交时关闸/伪造授权必须被拒且零副作用。
* §4 pause 确认边界 —— 跨进程锁 + 原子持久化 + 事件屏障；确认后不得有新决策/内容/
  提交/下一段；World 自己的暂停位不被误触。
* §5 模型总调用预算 —— 决策/内容/重试每个实际 attempt 发送前原子持久预留；不返还；
  跨 UTC 日、墙钟回拨、坏状态、单次窗口上限。

需要 POSIX（fcntl + AF_UNIX）与真实 World gateway；原生 Windows 自动跳过。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import (  # noqa: E402
    CycleTest, StubCognition, StubObservation, StubWriter, WorldGateway,
    _current_place, posix_only,
)

import autonomous_life_cycle as alc  # noqa: E402
import life_pause  # noqa: E402
import life_request_budget as lrb  # noqa: E402
import one_day_life as odl  # noqa: E402
import world_journey_action_adapter as wja  # noqa: E402

JOURNEY_RELATIVE = ("data", "world", "world_journey.json")
TRAVEL_RELATIVE = ("data", "world", "world_travel_delivery.json")


class RecordingCognition(StubCognition):
    """在替身决策之外，记下 AG-1 真正收到的 candidate_id 与 allowed_reason_codes。

    这是"真实历史影响下一次输入"的证据来源：比较两轮请求的候选集本身，而不是断言
    某个 head 字符串出现在日志里。
    """

    def __init__(self, policy: str = "artifact") -> None:
        super().__init__(policy)
        self.offered_ids: list[list[str]] = []
        self.allowed_codes: list[list[str]] = []

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.offered_ids.append([str(c.get("candidate_id"))
                                 for c in (getattr(request, "candidates", []) or [])])
        self.allowed_codes.append([str(c) for c in
                                   (getattr(request, "allowed_reason_codes", []) or [])])
        return super().invoke(request, schema_retry_index=schema_retry_index)


@posix_only
class W05RTestCase(CycleTest):
    """在 W05 夹具之上加几个读盘助手。"""

    def _head(self, data) -> dict:
        return odl.supply_call(self.services.supply_path,
                               {"op": "read_head", "subject": data["subject_id"],
                                "artifact_id": data["artifact_id"]})["head"]

    def _journey_path(self) -> Path:
        return self.world_home().joinpath(*JOURNEY_RELATIVE)

    def _travel_path(self) -> Path:
        return self.world_home().joinpath(*TRAVEL_RELATIVE)

    def _world_state_bytes(self) -> bytes:
        return (self.world_home() / "data" / "world" / "world_state.json").read_bytes()

    def _mixed_fixture(self, cognition, *, writer=None, observation=None, block_owner=None):
        """同一实例、同一次候选集：可续作品 + 已观察可达地点同时合格。

        ``writer`` / ``observation`` 可换成会在请求里停住的替身：R1-A 的事件屏障要在
        **模型请求**里、而不是在提交边界里把这一轮按住。``block_owner`` 传入
        ``(entered, release)`` 时，真实 AR-0 owner adapter 的 ``submit`` 会在调用里停住，
        用来制造"有限提交正在进行"的竞态（R1b）。
        """

        data = self._prepared_instance()
        env = self.instance_env()
        runtime, port = self._runtime(cognition, env, data)
        if block_owner is not None:
            entered, release = block_owner
            adapter = runtime.file_adapter
            original_submit = adapter.submit

            def submit(*args, **kwargs):
                entered.set()
                release.wait(30)
                return original_submit(*args, **kwargs)

            adapter.submit = submit
        writer = writer if writer is not None else StubWriter()
        observation = observation if observation is not None else StubObservation(env)
        bind = getattr(observation, "bind_environment", None)
        if callable(bind):
            bind(env)
        assembled = __import__("autonomous_life_assembly").assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(self.instance),
            world_provider=observation, writer=writer, enabled=True)
        self.assertEqual(assembled["state"], "READY", assembled)
        return SimpleNamespace(runtime=runtime, cycle=assembled["cycle"], port=port,
                               cognition=cognition, env=env, data=data, writer=writer,
                               observation=observation)

    def _world_fixture(self, cognition):
        data = self._prepared_instance()
        env = self.instance_env()
        runtime, _port = self._runtime(cognition, env, data)
        cycle = alc.AutonomousLifeCycle(runtime, artifact_port=None,
                                        world_opportunity_provider=StubObservation(env),
                                        writer=StubWriter(), enabled=True, env=env)
        return SimpleNamespace(runtime=runtime, cycle=cycle, cognition=cognition,
                               env=env, data=data)

    def _settle_to_arrival(self, cycle) -> dict:
        stored = json.loads(self._journey_path().read_text(encoding="utf-8"))
        arrival = stored["active"]["segments"][0]["expected_arrival_at"]
        return cycle.settle_world_journey(observed_at=arrival.replace("Z", "+00:00"))

    def _confirm_pause_in_its_own_process(self, state_root, *, reason: str):
        """在**独立进程**里确认 pause，就像操作者真的敲了 ``one_day_life.py pause``。

        这一轮被按在模型/内容请求里时，这个进程必须能立刻返回 —— 不是靠 sleep 猜，而是
        它的退出码和 stdout 就是"确认真的发生了"的证据。
        """

        scripts = str(Path(__file__).resolve().parents[1] / "components" / "life" / "scripts")
        code = ("import sys, json; sys.path.insert(0, %r); import life_pause;"
                " print(json.dumps(life_pause.set_paused(%r, True, reason=%r)))"
                % (scripts, str(state_root), reason))
        return subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, timeout=180)


# =====================================================================================
# §1 真正 CLI 的根因：替身模型的输出必须是 AG-1 合同内的
# =====================================================================================


@posix_only
class DecisionContractTest(W05RTestCase):
    """W05 首轮 CLI 的 ``run_once_outcome=BLOCKED`` 根因，在这里被钉死。"""

    def test_the_loopback_model_answers_inside_the_ag1_contract(self):
        """W05 首轮 ``BLOCKED`` 的根因：替身给空 ``reason_codes``，AG-1 拒得对。"""

        import agency_decision_ag1 as ag1
        from test_one_day_life import StubModel

        allowed = sorted(ag1.VALID_DECISION_REASON_CODES)
        prompt = json.dumps({
            "allowed_reason_codes": allowed,
            "candidates": [
                {"candidate_id": "cand:art", "candidate_kind": "CONSIDER_ARTIFACT"},
                {"candidate_id": "cand:world",
                 "candidate_kind": "CONSIDER_WORLD_OPPORTUNITY"}]})
        model = StubModel(policy="artifact")
        self.addCleanup(model.stop)
        for policy in ("artifact", "move", "no_action", "defer"):
            model.policy = policy
            raw = model._decision(prompt)
            codes = ag1.DecisionReasonCodeRegistry.validate_codes(raw["reason_codes"])
            self.assertTrue(codes, (policy, raw))
            self.assertIn(raw["decision"], ("START", "NO_ACTION", "DEFER"))
        # 空 reason_codes 会被正确判成 INVALID_MODEL_OUTPUT:UNKNOWN_REASON_CODE
        with self.assertRaises(ag1.DecisionValidationError) as caught:
            ag1.DecisionReasonCodeRegistry.validate_codes([])
        self.assertEqual(caught.exception.failure_class,
                         ag1.FAILURE_CLASS_UNKNOWN_REASON_CODE)

    def test_the_ag1_prompt_carries_the_registered_reason_codes(self):
        """替身与真模型看到同一份 allowed_reason_codes —— 这是「替身合法」的依据。"""

        import agency_cognition_adapter as aca
        import agency_decision_ag1 as ag1

        adapter = aca.ProductionAgencyCognitionAdapter(client=object())
        _text, shape = adapter.build_prompt(_a_request())
        self.assertTrue(shape["allowed_reason_codes"])
        for code in shape["allowed_reason_codes"]:
            self.assertTrue(ag1.DecisionReasonCodeRegistry.is_valid(code), code)

    def test_the_loopback_content_payload_is_inside_the_writer_contract(self):
        """W05 首轮 BLOCKED 的第二个原因：替身的内容 kind 不在写入器合同里。"""

        import model_continuation_writer as mcw
        from test_one_day_life import StubModel

        model = StubModel(policy="artifact")
        self.addCleanup(model.stop)
        reply = model._reply({"messages": [
            {"role": "system", "content": "写一段"},
            {"role": "user", "content": "{}"}]})
        payload = json.loads(reply["choices"][0]["message"]["content"])
        self.assertIn(payload["kind"], mcw.ALLOWED_KINDS)
        self.assertTrue(str(payload.get("text") or "").strip())

    def test_the_loopback_model_respects_the_legal_decision_enums(self):
        """替身也尊重 legal_decision_enums：START 不合法时给一个合法的选择。"""

        from test_one_day_life import StubModel

        model = StubModel(policy="artifact")
        self.addCleanup(model.stop)
        base = {"allowed_reason_codes": ["NO_ACTION_CHOSEN", "TIMING_NOT_APPROPRIATE"],
                "candidates": [{"candidate_id": "cand:art", "candidate_kind": "CONSIDER_ARTIFACT",
                                "executable_now": False}]}
        # 候选不可执行 + START 不合法 → NO_ACTION
        raw = model._decision(json.dumps({**base, "legal_decision_enums": ["NO_ACTION", "DEFER"]}))
        self.assertEqual(raw["decision"], "NO_ACTION")
        self.assertIsNone(raw["selected_candidate_id"])
        # 只有 DEFER 合法 → DEFER
        raw = model._decision(json.dumps({**base, "legal_decision_enums": ["DEFER"]}))
        self.assertEqual(raw["decision"], "DEFER")
        # 可执行 + START 合法 → START，并指名那个候选
        live = {"allowed_reason_codes": ["WORLD_OPPORTUNITY_OBSERVED"],
                "legal_decision_enums": ["START", "NO_ACTION"],
                "candidates": [{"candidate_id": "cand:world", "executable_now": True,
                                "candidate_kind": "CONSIDER_WORLD_OPPORTUNITY"}]}
        raw = model._decision(json.dumps(live))
        self.assertEqual(raw["decision"], "START")
        self.assertEqual(raw["selected_candidate_id"], "cand:world")


def _a_request():
    import agency_decision_ag1 as ag1

    return ag1.CognitiveDecisionRequest(
        opportunity_id="opp:w05r", candidate_set_id="cset:w05r",
        context_snapshot_id="snap:w05r", snapshot_fingerprint="fp:w05r",
        candidates=({"candidate_id": "cand:w05r", "source_kind": "RESUME_ELIGIBLE",
                     "source_ref": "src:w05r", "candidate_kind": "CONSIDER_ARTIFACT"},),
        current_activity_summary=None, capacity_summary={"state": "AVAILABLE"},
        legal_decision_enums=("START", "NO_ACTION", "DEFER"),
        allowed_reason_codes=tuple(sorted(ag1.VALID_DECISION_REASON_CODES)),
        observed_fact_keys=("world.location",), policy_version="w05r-test")


# =====================================================================================
# §2 同一候选集里的两条路，以及真实历史对下一次选择的影响
# =====================================================================================


@posix_only
class RoutingTest(W05RTestCase):
    def test_one_candidate_set_offers_both_and_the_decision_picks_the_artifact_owner(self):
        fixture = self._mixed_fixture(RecordingCognition("artifact"))
        data = fixture.data
        before = self._head(data)["version_id"]
        out = fixture.cycle.evaluate_once(trigger_ref="evt:w05r:art", act=True)

        self.assertEqual(out.get("step_path"), "AR0_WORLD_ROUTED", out)
        self.assertEqual(out.get("decision"), "START", out)
        self.assertEqual(out.get("outcome"), "ACTED", out)
        # 同一次候选集里两类候选同时被 AG-1 看到（不是两个实例各给一半）
        offered = fixture.cognition.offered_ids[-1]
        self.assertEqual(len(offered), 2, offered)
        kinds = fixture.cognition.offered[-1]
        self.assertIn("CONSIDER_WORLD_OPPORTUNITY", kinds)
        self.assertTrue([k for k in kinds if k != "CONSIDER_WORLD_OPPORTUNITY"])
        # 真的走了 Supply owner：版本 +1；世界一个字节都没动，也没有旅程
        self.assertNotEqual(self._head(data)["version_id"], before)
        self.assertFalse(self._journey_path().exists())
        self.assertFalse(self._travel_path().exists())

    def test_the_same_candidate_set_can_instead_drive_the_world_owner(self):
        fixture = self._mixed_fixture(RecordingCognition("move"))
        data = fixture.data
        before = self._head(data)["version_id"]
        origin = _current_place(self.world_home())
        world_before = self._world_state_bytes()

        out = fixture.cycle.evaluate_once(trigger_ref="evt:w05r:world", act=True)

        self.assertEqual(out.get("step_path"), "AR0_WORLD_ROUTED", out)
        self.assertEqual(out.get("outcome"), "IN_TRANSIT", out)
        # 同一个候选集，两个 candidate_id；这一次选的是地点那一个
        self.assertEqual(len(fixture.cognition.offered_ids[-1]), 2)
        # 真的走了 World owner：旅程账本存在、在途记录存在；作品一个版本都没动
        self.assertEqual(self._head(data)["version_id"], before)
        self.assertTrue(self._journey_path().is_file())
        self.assertTrue(self._travel_path().is_file())
        # 出发不是到达：位置权威仍是原处
        self.assertEqual(_current_place(self.world_home()), origin)
        self.assertNotEqual(self._world_state_bytes(), b"")
        self.assertTrue(world_before)

    def test_a_real_arrival_changes_the_next_rounds_ag1_candidates(self):
        """真实历史（新位置）改变下一轮 AG-1 真正收到的候选 —— 不是日志里的字符串。"""

        cognition = RecordingCognition("move")
        fixture = self._world_fixture(cognition)
        first = fixture.cycle.evaluate_once(trigger_ref="evt:w05r:h1", act=True)
        self.assertEqual(first.get("outcome"), "IN_TRANSIT", first)
        first_ids = list(cognition.offered_ids[-1])
        self.assertTrue(first_ids)

        settled = self._settle_to_arrival(fixture.cycle)
        self.assertTrue(settled["arrived"], settled)
        arrived_at = _current_place(self.world_home())
        self.assertEqual(arrived_at, settled["observed_location_id"])

        second = fixture.cycle.evaluate_once(trigger_ref="evt:w05r:h2", act=True)
        self.assertIsNotNone(second.get("outcome"))
        second_ids = list(cognition.offered_ids[-1])
        # 可执行下一步真的变了：候选 id 集合不再相同（新的位置给出新的可达地点）
        self.assertNotEqual(second_ids, first_ids,
                            "the next round's candidate set did not change after a real arrival")

    def test_a_real_commit_changes_the_next_rounds_ag0_material(self):
        """真实历史（新 head）改变下一轮的 AG-0 材料 —— 作品路径的同一因果要求。"""

        fixture = self._mixed_fixture(RecordingCognition("artifact"))
        before = fixture.cycle.materials()["continuable_artifact"]
        out = fixture.cycle.evaluate_once(trigger_ref="evt:w05r:m1", act=True)
        self.assertEqual(out.get("outcome"), "ACTED", out)
        after = fixture.cycle.materials()["continuable_artifact"]
        self.assertNotEqual(after["head_version_id"], before["head_version_id"])
        # 材料里的 head 就是 owner 现在报的 head（不是自封的事实）
        self.assertEqual(after["head_version_id"], self._head(fixture.data)["version_id"])


# =====================================================================================
# §3 计时 MOVE 必须走世界自己的正式授权入口
# =====================================================================================


@posix_only
class FormalAuthorizationTest(W05RTestCase):
    def _resolver(self, *, extra_env=None):
        env = self.instance_env()
        if extra_env:
            env.update(extra_env)
        import world_runtime_data as wrd

        wrd.reset_cache()
        return wja.JourneyWorldResolver(environment=env), env

    def test_the_timed_segment_is_submitted_through_the_formal_entry(self):
        import action_reality_ledger as ar0
        import world_runtime_data as wrd

        data = self._prepared_instance()
        resolver, env = self._resolver()
        world = wrd.active_world(env)
        origin = _current_place(self.world_home())
        destination = sorted(world.reachable_of(origin))[0]

        described = resolver.describe()
        self.assertEqual(described["submission_route"], "formal")
        self.assertEqual(described["submission_port_kind"],
                         "formal_owner_move_submit_port")

        out = resolver.resolve_action(idempotency_key="idem:w05r:formal", verb="MOVE",
                                      target=destination, expected_dependencies={})
        self.assertEqual(out["status"], "committed", out)
        self.assertEqual(out["code"], "JOURNEY_DEPARTED")
        self.assertEqual(out["transport_result"], ar0.TRANSPORT_OK_SUCCEEDED)
        self.assertIs(out["details"]["arrived"], False)
        self.assertEqual(out["details"]["location_id"], origin)

        # 正式入口真的被用了：intent 的 execution_id 就是段的 execution_id，
        # 而且 gateway 侧留下了这次提交的授权与执行记录。
        port = resolver.submit_port
        self.assertEqual(port.submissions, 1)
        self.assertIsNotNone(port.last_intent)
        self.assertEqual(port.last_intent["verb"], "MOVE")
        self.assertEqual(port.last_intent["params"]["destination_location_id"], destination)
        self.assertEqual(port.last_result["intent_status"], "APPLIED")
        stored = json.loads(self._journey_path().read_text(encoding="utf-8"))
        segment = stored["active"]["segments"][0]
        self.assertEqual(segment["execution_id"], port.last_intent["execution_id"])
        # owner 侧：位置仍在家，在途记录存在
        self.assertEqual(_current_place(self.world_home()), origin)
        travel = json.loads(self._travel_path().read_text(encoding="utf-8"))
        self.assertEqual([t["execution_id"] for t in travel["travels"]],
                         [segment["execution_id"]])

    def test_closing_the_mutation_gate_refuses_at_submit_time_with_zero_side_effects(self):
        import world_runtime_data as wrd
        from unittest import mock

        data = self._prepared_instance()
        env = self.instance_env()
        world = wrd.active_world(env)
        origin = _current_place(self.world_home())
        destination = sorted(world.reachable_of(origin))[0]
        before = self._world_state_bytes()

        resolver = wja.JourneyWorldResolver(environment=dict(env))
        with mock.patch.dict(os.environ, {"WORLD_BODY_INTENT_MUTATION_GATE": "off"}):
            out = resolver.resolve_action(idempotency_key="idem:w05r:gateoff", verb="MOVE",
                                          target=destination, expected_dependencies={})

        self.assertEqual(out["status"], "rejected", out)
        self.assertEqual(out["code"], "GATE_OFF")
        self.assertEqual(out["transport_result"], ar0_definite_pre_submit())
        self.assertIs(out["details"]["arrived"], False)
        # 零副作用：位置、世界状态字节、在途记录都没变
        self.assertEqual(self._world_state_bytes(), before)
        self.assertEqual(_current_place(self.world_home()), origin)
        self.assertFalse(self._travel_path().exists())
        self.assertEqual(resolver.submit_port.submissions, 0)
        # 旅程账本里只有那次**耐久意图**（提交前写的），没有任何出发证据
        stored = json.loads(self._journey_path().read_text(encoding="utf-8"))
        self.assertEqual(stored["active"]["status"], "blocked")
        self.assertEqual(stored["active"]["error"]["reason_code"], "GATE_OFF")
        self.assertEqual(stored["active"]["segments"][0]["status"], "pending")
        self.assertIsNone(stored["active"]["segments"][0]["started_at"])

    def test_a_forged_authorization_is_refused_by_the_gateway_firewall(self):
        """负控：绕过 Intent Executor 直接拿一个假授权去提交，gateway 必须拒绝。"""

        import world_body_client as wbc
        import world_runtime_data as wrd

        self._prepared_instance()
        env = self.instance_env()
        world = wrd.active_world(env)
        origin = _current_place(self.world_home())
        destination = sorted(world.reachable_of(origin))[0]
        before = self._world_state_bytes()

        client = wbc.WorldBodyClient(environment=env)
        result = client.submit_action(action="MOVE", execution_id="world-exec:w05r:forged",
                                     params={"destination_location_id": destination},
                                     observed_dependencies={},
                                     authorization_id="w05r-not-a-real-authorization")
        self.assertEqual(result.outcome, "FAILURE")
        self.assertEqual(result.reason, "AUTHORIZATION_NOT_FOUND")
        self.assertTrue((result.data or {}).get("world_unchanged"))
        self.assertEqual(self._world_state_bytes(), before)
        self.assertFalse(self._travel_path().exists())

    def test_an_unknown_outcome_is_reconciled_under_the_same_identity(self):
        """UNKNOWN 不能凭"有意图"就算到达：同一 request/execution identity 对账。"""

        import world_runtime_data as wrd

        self._prepared_instance()
        resolver, env = self._resolver()
        world = wrd.active_world(env)
        origin = _current_place(self.world_home())
        destination = sorted(world.reachable_of(origin))[0]

        out = resolver.resolve_action(idempotency_key="idem:w05r:recon", verb="MOVE",
                                      target=destination, expected_dependencies={})
        self.assertEqual(out["status"], "committed", out)
        execution_id = out["execution_id"]

        view = resolver.reconcile_request(idempotency_key="idem:w05r:recon", target=destination)
        self.assertEqual(view["outcome"], "SUCCEEDED")
        self.assertFalse(view["result"]["arrived"])
        self.assertEqual(view["result"]["segments_confirmed"], 0)
        # 执行 id 是从 action 自己的 payload 重算的，对账用同一个 identity
        self.assertEqual(execution_id,
                         resolver.execution_id_for("idem:w05r:recon", "MOVE", destination))
        # 没有任何东西把"出发"写成"到达"
        self.assertEqual(_current_place(self.world_home()), origin)
        self.assertEqual(
            resolver.reconcile_request(idempotency_key="idem:w05r:never", target=destination
                                       )["outcome"], "NOT_FOUND")


def ar0_definite_pre_submit() -> str:
    import action_reality_ledger as ar0

    return ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT


# =====================================================================================
# §4 pause 的确认边界：跨进程锁、原子持久化、事件屏障
# =====================================================================================


@posix_only
class PauseBoundaryTest(W05RTestCase):
    def test_the_commit_boundary_reports_contention_as_held(self):
        root = odl.life_state_root(self.instance) if self.instance.exists() else None
        self._prepared_instance()
        root = odl.life_state_root(self.instance)

        held = threading.Event()
        release = threading.Event()

        def operator():
            with life_pause.pause_lock(root, exclusive=True):
                held.set()
                release.wait(10)

        worker = threading.Thread(target=operator, daemon=True)
        worker.start()
        self.assertTrue(held.wait(10))
        try:
            with life_pause.commit_boundary(root) as boundary:
                # 拿不到共享锁就是 fail closed：视为被按住，而不是放行
                self.assertTrue(boundary["blocked"], boundary)
                self.assertFalse(boundary["acquired"])
                self.assertEqual(boundary["reason_code"], life_pause.REASON_PAUSE_CONTENDED)
        finally:
            release.set()
            worker.join(timeout=5)

        # 锁释放后边界恢复正常
        with life_pause.commit_boundary(root) as boundary:
            self.assertTrue(boundary["acquired"])
            self.assertFalse(boundary["blocked"])

    def test_pause_confirmed_before_the_boundary_stops_the_round_with_no_call(self):
        fixture = self._mixed_fixture(RecordingCognition("artifact"))
        data = fixture.data
        before = self._head(data)["version_id"]
        life_pause.set_paused(odl.life_state_root(self.instance), True, reason="w05r")

        out = fixture.cycle.evaluate_once(trigger_ref="evt:w05r:paused", act=True)
        self.assertEqual(out.get("outcome"), "PAUSED_BY_OPERATOR", out)
        self.assertEqual(out.get("llm_calls"), 0)
        self.assertEqual(fixture.cognition.call_count, 0)
        # 零新提交：作品没动，也没有旅程
        self.assertEqual(self._head(data)["version_id"], before)
        self.assertFalse(self._journey_path().exists())

    def test_a_pause_confirmed_while_the_decision_request_is_in_flight_stops_the_round(self):
        """R1-A 事件屏障：决策请求挂住时，独立进程必须能在放行**之前**确认 pause。

        原合同（修正单 §4）：已经开始的有限提交可以让它结束，但"跨网络的决策请求"不是
        提交。所以这里的不变量是：

        1. 这一轮被按在**模型请求**里时，另一个**进程**的 ``pause`` 必须立刻返回；
        2. 请求放行之后，不得再有新的内容请求、不得有 owner 提交、不得开始下一段；
        3. 确认之后连下一轮决策都不能开始。

        这里不比较"谁先 append"，也不靠 sleep 猜：pause 子进程的退出码就是它真的确认了
        的证据，而它是在 release 之前 join 完成的。
        """

        cognition = BlockingCognition("artifact")
        fixture = self._mixed_fixture(cognition)
        data = fixture.data
        state_root = odl.life_state_root(self.instance)
        head_before = self._head(data)["version_id"]
        content_calls_before = fixture.writer.call_count
        round_done: dict = {}

        def run_round():
            round_done["out"] = fixture.cycle.evaluate_once(
                trigger_ref="evt:w05rr1:decision", act=True)

        worker = threading.Thread(target=run_round, daemon=True)
        worker.start()
        self.assertTrue(cognition.entered.wait(20), "the round never reached its model call")
        confirmed: dict = {}
        try:
            pause_worker = threading.Thread(
                target=lambda: confirmed.setdefault(
                    "proc", self._confirm_pause_in_its_own_process(
                        state_root, reason="w05rr1-decision")), daemon=True)
            pause_worker.start()
            pause_worker.join(timeout=120)
            # 请求仍然挂着（没 release），而确认已经真的发生了。
            self.assertFalse(pause_worker.is_alive(), "the operator's pause never returned")
            self.assertFalse(cognition.release.is_set())
            self.assertTrue(worker.is_alive(), "the round finished before its request was released")
        finally:
            cognition.release.set()
        worker.join(timeout=60)
        pause_worker.join(timeout=60)

        proc = confirmed.get("proc")
        self.assertIsNotNone(proc, "the pause subprocess never ran")
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertFalse(worker.is_alive())
        out = round_done.get("out") or {}
        self.assertEqual(out.get("outcome"), "PAUSED_BY_OPERATOR", out)
        self.assertEqual((out.get("pause_boundary") or {}).get("held_after"),
                         "after_decision_before_adoption", out)
        # 零新内容请求：内容写入器一次都没被叫
        self.assertEqual(fixture.writer.call_count, content_calls_before)
        # 零 owner 提交：作品 head 没动，也没有任何旅程/在途记录
        self.assertEqual(self._head(data)["version_id"], head_before)
        self.assertFalse(self._journey_path().exists())
        self.assertFalse(self._travel_path().exists())
        self.assertTrue(life_pause.read(state_root)["paused"])
        # 确认之后：不再开始新决策、不再生成内容、不再提交
        calls_before = cognition.call_count
        after = fixture.cycle.evaluate_once(trigger_ref="evt:w05rr1:after-pause", act=True)
        self.assertEqual(after.get("outcome"), "PAUSED_BY_OPERATOR", after)
        self.assertEqual(cognition.call_count, calls_before)
        self.assertEqual(self._head(data)["version_id"], head_before)

    def test_a_pause_confirmed_while_the_content_request_is_in_flight_writes_nothing(self):
        """R1-A：内容请求挂住时同样必须能确认，且放行之后零 owner 提交。"""

        writer = BlockingWriter()
        fixture = self._mixed_fixture(RecordingCognition("artifact"), writer=writer)
        data = fixture.data
        state_root = odl.life_state_root(self.instance)
        head_before = self._head(data)["version_id"]
        round_done: dict = {}

        def run_round():
            round_done["out"] = fixture.cycle.evaluate_once(
                trigger_ref="evt:w05rr1:content", act=True)

        worker = threading.Thread(target=run_round, daemon=True)
        worker.start()
        self.assertTrue(writer.entered.wait(20), "the round never reached its content request")
        confirmed: dict = {}
        try:
            pause_worker = threading.Thread(
                target=lambda: confirmed.setdefault(
                    "proc", self._confirm_pause_in_its_own_process(
                        state_root, reason="w05rr1-content")), daemon=True)
            pause_worker.start()
            pause_worker.join(timeout=120)
            self.assertFalse(pause_worker.is_alive(), "the operator's pause never returned")
            self.assertFalse(writer.release.is_set())
            self.assertTrue(worker.is_alive(), "the round finished before its request was released")
        finally:
            writer.release.set()
        worker.join(timeout=60)
        pause_worker.join(timeout=60)

        proc = confirmed.get("proc")
        self.assertIsNotNone(proc, "the pause subprocess never ran")
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        out = round_done.get("out") or {}
        self.assertEqual(out.get("outcome"), "PAUSED_BY_OPERATOR", out)
        self.assertEqual((out.get("pause_boundary") or {}).get("held_after"),
                         "after_content_before_propose", out)
        # 内容请求确实发生过一次（它被放行了），但 owner 一个版本都没写
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self._head(data)["version_id"], head_before)
        self.assertIsNone(out.get("action_id"), out)
        self.assertFalse(self._journey_path().exists())
        self.assertFalse(self._travel_path().exists())

    def test_a_pause_confirmed_after_the_entry_gate_starts_no_request(self):
        """R1-A：入口 gate 已经放行、观察还在读时确认的 pause，不得再启动决策请求。"""

        observation = BlockingObservation()
        cognition = RecordingCognition("artifact")
        fixture = self._mixed_fixture(cognition, observation=observation)
        data = fixture.data
        state_root = odl.life_state_root(self.instance)
        head_before = self._head(data)["version_id"]
        round_done: dict = {}

        def run_round():
            round_done["out"] = fixture.cycle.evaluate_once(
                trigger_ref="evt:w05rr1:gate", act=True)

        worker = threading.Thread(target=run_round, daemon=True)
        worker.start()
        self.assertTrue(observation.entered.wait(20),
                        "the round never got past the entry gate into its observation read")
        confirmed: dict = {}
        try:
            pause_worker = threading.Thread(
                target=lambda: confirmed.setdefault(
                    "proc", self._confirm_pause_in_its_own_process(
                        state_root, reason="w05rr1-gate")), daemon=True)
            pause_worker.start()
            pause_worker.join(timeout=120)
            self.assertFalse(pause_worker.is_alive(), "the operator's pause never returned")
        finally:
            observation.release.set()
        worker.join(timeout=60)
        pause_worker.join(timeout=60)

        proc = confirmed.get("proc")
        self.assertIsNotNone(proc, "the pause subprocess never ran")
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        out = round_done.get("out") or {}
        self.assertEqual(out.get("outcome"), "PAUSED_BY_OPERATOR", out)
        self.assertEqual((out.get("pause_boundary") or {}).get("held_after"),
                         "before_request", out)
        # 请求根本没发出去
        self.assertEqual(cognition.call_count, 0)
        self.assertEqual(out.get("llm_calls"), 0)
        self.assertEqual(self._head(data)["version_id"], head_before)
        self.assertFalse(self._journey_path().exists())

    def test_concurrent_pause_and_resume_do_not_lose_a_revision(self):
        self._prepared_instance()
        root = odl.life_state_root(self.instance)
        life_pause.set_paused(root, False, reason="seed")
        seen: list[int] = []

        def flip(paused: bool):
            view = life_pause.set_paused(root, paused, reason="race")
            with threading.Lock():
                seen.append(view["revision"])

        threads = [threading.Thread(target=flip, args=(index % 2 == 0,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(len(seen), 8)
        # 每次写入都拿到一个互不相同的 revision：没有并发丢修订
        self.assertEqual(len(set(seen)), 8, seen)
        self.assertEqual(life_pause.read(root)["revision"], max(seen))

    def test_the_life_pause_never_touches_the_world_pause(self):
        self._prepared_instance()
        world_control = self.world_home() / "data" / "world" / "world_control.json"
        self.assertFalse(world_control.exists())
        odl.set_pause(self.instance, True, reason="w05r")
        self.assertFalse(world_control.exists())
        odl.set_pause(self.instance, False, reason="w05r")
        self.assertFalse(world_control.exists())

    def test_a_corrupt_pause_state_is_held_not_ignored(self):
        self._prepared_instance()
        root = odl.life_state_root(self.instance)
        life_pause.pause_path(root).write_text("{ not json", encoding="utf-8")
        gate = life_pause.gate(root)
        self.assertTrue(gate["blocked"])
        self.assertEqual(gate["reason_code"], life_pause.REASON_PAUSE_STATE_CORRUPT)
        # 也拦住世界提交：坏状态 = 按住，而不是放行
        with life_pause.commit_boundary(root) as boundary:
            self.assertTrue(boundary["blocked"])
            self.assertEqual(boundary["reason_code"], life_pause.REASON_PAUSE_STATE_CORRUPT)


class BlockingCognition(RecordingCognition):
    """进到模型调用就停住，直到测试放行 —— 用来制造确定性的提交屏障。"""

    def __init__(self, policy: str = "artifact") -> None:
        super().__init__(policy)
        self.entered = threading.Event()
        self.release = threading.Event()

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.entered.set()
        self.release.wait(30)
        return super().invoke(request, schema_retry_index=schema_retry_index)


class BlockingWriter(StubWriter):
    """内容写入器替身：进到内容请求就停住，直到测试放行。

    R1-A 需要证明"内容请求挂住时 pause 仍能确认"，所以停住的位置必须是**内容请求**本身，
    不是它后面的提交边界。
    """

    def __init__(self, note: str = "（替身内容生成）") -> None:
        super().__init__(note)
        self.entered = threading.Event()
        self.release = threading.Event()

    def next_content(self, point, *, observed_at: str) -> str:
        self.entered.set()
        self.release.wait(30)
        return super().next_content(point, observed_at=observed_at)


class BlockingObservation(StubObservation):
    """观察缝替身：入口 gate 已经放行、决策请求还没发出时停住。

    环境由夹具在实例准备好之后注入（``bind_environment``），因为这一轮要停住的地方是
    "入口 gate 已过、观察还在读"，而实例那时才刚建好。
    """

    def __init__(self, environment=None) -> None:
        super().__init__(environment or {})
        self.entered = threading.Event()
        self.release = threading.Event()

    def bind_environment(self, environment) -> None:
        self.environment = dict(environment)

    def __call__(self):
        self.entered.set()
        self.release.wait(30)
        return super().__call__()


class LoopbackModel:
    """A real loopback chat-completions server that records every request it receives.

    R1c 用它证明"暂停确认后 0 次实际 HTTP"是在**真传输**上测的：请求形状、purpose 头、
    响应解析都走 ``OpenAICompatibleTransport`` 自己（不是进程内替身）。
    """

    def __init__(self, *, hold_response: bool = False) -> None:
        outer = self
        self.hits: list[str] = []
        self.purposes: list[str] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self._hold = hold_response

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server naming
                outer.hits.append(self.path)
                purpose = str(self.headers.get("X-Chiyo-Purpose") or "")
                outer.purposes.append(purpose)
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.entered.set()
                if outer._hold:
                    outer.release.wait(30)
                payload = ({"kind": "append_paragraph", "text": "loopback", "self_note": "loopback"}
                           if purpose == "artifact_edit" else
                           {"decision": "NO_ACTION", "selected_candidate_id": None,
                            "reason_codes": ["NO_ACTION_CHOSEN"], "short_rationale": "loopback",
                            "deferred_candidate_ids": []})
                body = json.dumps({"choices": [{"message": {"role": "assistant",
                                                            "content": json.dumps(payload)}}],
                                   "usage": {"prompt_tokens": 3, "completion_tokens": 2,
                                             "total_tokens": 5}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # keep the test output clean
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d/v1" % self.server.server_port

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


class _RealCognitionInner:
    """The real cognition adapter the budget wrapper wraps in production."""

    def __init__(self, client) -> None:
        import agency_cognition_adapter as aca

        self._adapter = aca.ProductionAgencyCognitionAdapter(client=client, timeout_s=5.0,
                                                            max_output_tokens=64)
        self.send_split_capable = getattr(self._adapter, "send_split_capable", False)

    def invoke(self, request, *, schema_retry_index: int = 0):
        return self._adapter.invoke(request, schema_retry_index=schema_retry_index)

    def __getattr__(self, name):
        return getattr(self._adapter, name)


# =====================================================================================
# R1-B：真实到达合法结束前台活动，同一实例随后能合法选另一条分支
# =====================================================================================


@posix_only
class LegalLifecycleTest(W05RTestCase):
    """R1-B：合法生活后续不再被永久前台挡住 —— 靠完成合同，不靠自动清前台。"""

    def test_a_real_arrival_legally_completes_the_world_activity_and_frees_the_foreground(self):
        """同一实例：世界分支真实到达 → Activity 合法 COMPLETED → 作品分支合法 START。"""

        cognition = RecordingCognition("move")
        fixture = self._mixed_fixture(cognition)
        data = fixture.data
        head_before = self._head(data)["version_id"]
        # 先让观察缝记录一次"她看到的地方"，就像 resident 每个 tick 在考虑窗之前做的那样
        # （``settle_journey`` 在窗口之前跑）。不这样做的话，这一轮自己的 ``observed_at``
        # 可能比它刚刚记录的机会早不到一秒，而 AG-0 会正确地把那个机会判成
        # ``OPPORTUNITY_NOT_YET_OPEN``（candidate_sources_ag0.py:2297）—— 那是既有的
        # 秒边界竞态，不在本返修的写集里，所以这里不去修它，只把时序说清楚。
        self.assertTrue(fixture.cycle.materials()["world_opportunities"], "no observed place")

        first = fixture.cycle.evaluate_once(trigger_ref="evt:w05rr1:l1", act=True)
        self.assertEqual(first.get("step_path"), "AR0_WORLD_ROUTED", first)
        self.assertEqual(first.get("outcome"), "IN_TRANSIT", first)
        activity_ref = first.get("activity_ref")
        self.assertTrue(activity_ref, first)
        # 出发不是到达：位置权威仍是原处，作品一个版本都没动
        self.assertEqual(fixture.runtime.canonical_state().get("foreground_activity_ref"),
                         activity_ref)
        self.assertEqual(self._head(data)["version_id"], head_before)

        settled = self._settle_to_arrival(fixture.cycle)
        self.assertTrue(settled["arrived"], settled)
        # 到达经 LR-5 的完成合同把 Activity 合法结束：这是 owner 自己的转换，
        # 不是"演示时自动清前台"，也不是"出发即到达"。
        activity = fixture.runtime.activity_read.get_activity(activity_ref)
        self.assertEqual(activity.get("status"), "COMPLETED", activity)
        self.assertTrue(activity.get("completion_evidence_ref"), activity)
        self.assertIsNone(fixture.runtime.canonical_state().get("foreground_activity_ref"))

        # 同一实例、同一候选集：现在可以合法选另一条分支，并真实作用于 Supply owner
        cognition.policy = "artifact"
        second = fixture.cycle.evaluate_once(trigger_ref="evt:w05rr1:l2", act=True)
        self.assertEqual(second.get("decision"), "START", second)
        self.assertFalse(str(second.get("adoption_status") or "").startswith("REJECTED"), second)
        self.assertEqual(second.get("outcome"), "ACTED", second)
        self.assertNotEqual(self._head(data)["version_id"], head_before)
        self.assertNotEqual(second.get("activity_ref"), activity_ref)

    def test_an_illegal_second_foreground_start_is_still_refused(self):
        """反例：前台还 ACTIVE 时坚持 START，必须被拒 —— 校验没有被放宽。

        替身只从它看到的候选里挑，并且**坚持** START（它不会自己去挑合法的 CONTINUE），
        这正是原单记录的那次拒绝。拒绝可以发生在两个既有合同点上：AG-1 的逐候选决策矩阵
        （``DECISION_CANDIDATE_INCOMPATIBLE``）或 AG-2 §6 的前台占用校验
        （``CANNOT_START_WHILE_FOREGROUND_ACTIVE``）。两条路径都是同一个合同的正确实现，
        所以这里断言"确实被拒且原因指向这次不合法"，而不是把某一条写死 —— 但无论走哪条，
        都不允许静默采纳、也不允许写出第二个版本。
        """

        cognition = RecordingCognition("artifact")
        fixture = self._mixed_fixture(cognition)
        data = fixture.data
        # 同上一例：先记录一次观察，让两条分支都在候选集里（秒边界竞态见上）。
        self.assertTrue(fixture.cycle.materials()["world_opportunities"], "no observed place")
        first = fixture.cycle.evaluate_once(trigger_ref="evt:w05rr1:r1", act=True)
        self.assertEqual(first.get("outcome"), "ACTED", first)
        head_after_first = self._head(data)["version_id"]
        activity_ref = first.get("activity_ref")
        self.assertTrue(activity_ref, first)
        self.assertEqual(fixture.runtime.canonical_state().get("foreground_activity_ref"),
                         activity_ref)

        second = fixture.cycle.evaluate_once(trigger_ref="evt:w05rr1:r2", act=True)
        self.assertIn(second.get("outcome"), {"ADOPTION_REJECTED", "DECISION_FAILED"}, second)
        detail = str(second.get("detail") or "")
        self.assertTrue(
            detail.startswith("CANNOT_START_WHILE_FOREGROUND_ACTIVE")
            or "DECISION_CANDIDATE_INCOMPATIBLE" in detail, second)

        # 拒绝就是拒绝：作品没有第二个版本
        self.assertEqual(self._head(data)["version_id"], head_after_first)
# =====================================================================================
# R1b：请求启动边界（决策/每次retry/内容）与 pause 确认的协调 + 有限提交竞态
# =====================================================================================


@posix_only
class RequestStartBoundaryTest(W05RTestCase):
    """R1b：每个真实请求 attempt 在**发送前**过确认握手，之后不再有"检查后仍启动"。"""

    def test_a_pause_confirmed_after_the_final_check_starts_no_decision_request(self):
        """父级探针 1（树内回归）：最后一次读位返回后确认 pause → 决策请求不得启动。"""

        cognition = RecordingCognition("no_action")
        fixture = self._mixed_fixture(cognition)
        data = fixture.data
        head_before = self._head(data)["version_id"]
        original = fixture.cycle._pause_hold
        confirmed: list = []

        def barrier():
            view = original()
            if not confirmed and not view["blocked"]:
                proc = self._confirm_pause_in_its_own_process(
                    odl.life_state_root(self.instance), reason="w05rr1b-after-final-check")
                self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
                confirmed.append(True)
            return view

        fixture.cycle._pause_hold = barrier
        out = fixture.cycle.evaluate_once(trigger_ref="evt:w05rr1b:final-check", act=True)

        self.assertTrue(confirmed, "the probe never confirmed the pause")
        self.assertEqual(cognition.call_count, 0,
                         "pause was confirmed before the request start, yet cognition started")
        self.assertEqual(out.get("outcome"), "PAUSED_BY_OPERATOR", out)
        self.assertEqual(self._head(data)["version_id"], head_before)

    def test_a_pause_confirmed_after_the_adoption_check_starts_no_content_request(self):
        """父级探针 2（树内回归）：采纳门检查返回后确认 pause → 内容请求不得启动。"""

        cognition = RecordingCognition("artifact")
        fixture = self._mixed_fixture(cognition)
        data = fixture.data
        head_before = self._head(data)["version_id"]
        original = fixture.cycle._pause_gate_hook
        confirmed: list = []

        def barrier(record=None):
            value = original(record)
            proc = self._confirm_pause_in_its_own_process(
                odl.life_state_root(self.instance), reason="w05rr1b-after-adoption-check")
            self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
            confirmed.append(True)
            return value

        fixture.cycle._pause_gate_hook = barrier
        out = fixture.cycle.evaluate_once(trigger_ref="evt:w05rr1b:adoption-check", act=True)

        self.assertTrue(confirmed, "the probe never confirmed the pause")
        self.assertEqual(fixture.writer.call_count, 0,
                         "pause was confirmed before the content start, yet content started")
        self.assertEqual(out.get("outcome"), "PAUSED_BY_OPERATOR", out)
        self.assertEqual(self._head(data)["version_id"], head_before)

    def test_the_wrapper_refuses_a_transport_that_cannot_coordinate_the_send(self):
        """R1c：不能分离 send/response 的传输必须在**任何发送之前**被拒，且预留不返还。"""

        import life_request_budget as lrb

        state_root = odl.life_state_root(self.instance)
        self._prepared_instance()
        sent: list[str] = []

        class Opaque:
            def invoke(self, request, *, schema_retry_index=0):
                sent.append("decision")
                return "ok"

            def complete(self, *args, **kwargs):
                sent.append("content")
                return "ok"

        def admission(kind, attempt=0):
            life_pause.admit_request(state_root, stage="before_%s_request" % kind)

        for kind in ("decision", "retry", "content"):
            budget = lrb.LifeRequestBudget(state_root, run_id="run:w05rr1c-" + kind,
                                           utc_day_max=10, run_window_max=10)
            budget.ensure_window(allow_new=True)
            if kind == "content":
                wrapper = lrb.BudgetedModelClient(Opaque(), budget, admission=admission)
                call = lambda: wrapper.complete("x")  # noqa: E731
            else:
                wrapper = lrb.BudgetedCognitionAdapter(Opaque(), budget, admission=admission)
                retry = 0 if kind == "decision" else 1
                call = lambda: wrapper.invoke(object(), schema_retry_index=retry)  # noqa: E731
            before_calls = budget.view()["utc_day_calls"]
            with self.assertRaises(life_pause.RequestNotSentError) as refused:
                call()
            self.assertEqual(refused.exception.reason_code,
                             life_pause.REASON_REQUEST_NOT_SENT, kind)
            # 预留已经落盘：被拒的 attempt 各 +1、不返还、也不静默重试。
            self.assertEqual(budget.view()["utc_day_calls"] - before_calls, 1, kind)
            self.assertFalse(hasattr(budget, "release"))
        self.assertEqual(sent, [], "an opaque transport was called after all")

    def test_the_real_transport_sends_nothing_after_the_pause_is_confirmed(self):
        """R1c：真 OpenAICompatibleTransport + loopback，确认在发送之前 ⇒ 0 次实收。"""

        import life_request_budget as lrb
        from agency_model_client import AgencyModelClient
        from profile_model_transport import OpenAICompatibleTransport

        state_root = odl.life_state_root(self.instance)
        self._prepared_instance()
        server = LoopbackModel()
        self.addCleanup(server.stop)
        results = []
        for kind, retry in (("decision", 0), ("retry", 1), ("content", 0)):
            budget = lrb.LifeRequestBudget(state_root, run_id="run:w05rr1c-real-" + kind,
                                           utc_day_max=10, run_window_max=10)
            budget.ensure_window(allow_new=True)
            guard = life_pause.RequestSendGuard(state_root)
            purpose = "artifact_edit" if kind == "content" else "agency_cognition"
            transport = OpenAICompatibleTransport(
                base_url=server.base_url, api_key="probe-key", model="loopback",
                provider="custom", timeout_s=5.0, purpose=purpose,
                request_json_object=True, pause_guard=guard)
            client = AgencyModelClient(
                config={"provider": "custom", "model": "loopback",
                        "base_url": server.base_url, "api_key": "probe-key"},
                transport=transport, purpose=purpose, timeout_s=5.0, max_output_tokens=64)

            def admission(k, attempt=0, root=state_root):
                life_pause.admit_request(root, stage="before_%s_request" % k)
                proc = self._confirm_pause_in_its_own_process(root, reason="w05rr1c-real")
                self.assertEqual(proc.returncode, 0, proc.stderr[-300:])

            inner = client if kind == "content" else _RealCognitionInner(client)
            wrapper = (lrb.BudgetedModelClient(inner, budget, admission=admission)
                       if kind == "content"
                       else lrb.BudgetedCognitionAdapter(inner, budget, admission=admission))
            before = len(server.hits)
            before_calls = budget.view()["utc_day_calls"]
            with self.assertRaises(life_pause.PauseConfirmedError) as blocked:
                if kind == "content":
                    wrapper.complete(system_prompt="write", user_prompt="{}")
                else:
                    wrapper.invoke(_a_request(), schema_retry_index=retry)
            # 每个 attempt 的预留都在发送前落盘：被拦的 attempt 各 +1、不返还。
            results.append((kind, len(server.hits) - before,
                            budget.view()["utc_day_calls"] - before_calls,
                            blocked.exception.stage))
            life_pause.set_paused(state_root, False, reason="w05rr1c-real")

        self.assertEqual([item[1] for item in results], [0, 0, 0], results)
        self.assertEqual([item[2] for item in results], [1, 1, 1], results)
        self.assertTrue(all(item[3] == "before_model_send" for item in results), results)
        self.assertEqual(server.hits, [], "the loopback server received a request")

    def test_a_pause_confirmed_while_the_real_response_hangs_blocks_the_next_step(self):
        """R1c：响应已挂起时 pause 仍能确认（响应等待不持锁），且拦下一次发送。"""

        import life_request_budget as lrb
        from agency_model_client import AgencyModelClient
        from profile_model_transport import OpenAICompatibleTransport

        state_root = odl.life_state_root(self.instance)
        self._prepared_instance()
        server = LoopbackModel(hold_response=True)
        self.addCleanup(server.stop)
        budget = lrb.LifeRequestBudget(state_root, run_id="run:w05rr1c-hang",
                                       utc_day_max=10, run_window_max=10)
        budget.ensure_window(allow_new=True)
        guard = life_pause.RequestSendGuard(state_root)
        transport = OpenAICompatibleTransport(
            base_url=server.base_url, api_key="probe-key", model="loopback",
            provider="custom", timeout_s=5.0, purpose="artifact_edit",
            request_json_object=True, pause_guard=guard)
        client = AgencyModelClient(
            config={"provider": "custom", "model": "loopback",
                    "base_url": server.base_url, "api_key": "probe-key"},
            transport=transport, purpose="artifact_edit", timeout_s=5.0, max_output_tokens=64)
        wrapper = lrb.BudgetedModelClient(client, budget, admission=guard)
        done: dict = {}

        def send():
            try:
                done["result"] = wrapper.complete(system_prompt="write", user_prompt="{}")
            except Exception as exc:  # recorded, never hidden
                done["error"] = "%s: %s" % (type(exc).__name__, exc)

        worker = threading.Thread(target=send, daemon=True)
        worker.start()
        self.assertTrue(server.entered.wait(20), "the request never reached the loopback server")
        proc = self._confirm_pause_in_its_own_process(state_root, reason="w05rr1c-hang")
        self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
        # 响应仍挂在那里：pause 没有排在它后面（响应等待不持任何 pause/shared/control 锁）。
        self.assertTrue(worker.is_alive(),
                        "the response was not pending when the pause returned")
        self.assertEqual(len(server.hits), 1)
        server.release.set()
        worker.join(timeout=30)
        self.assertIn("result", done, done)
        with self.assertRaises(life_pause.PauseConfirmedError):
            wrapper.complete(system_prompt="write", user_prompt="{}")
        self.assertEqual(len(server.hits), 1,
                         "a second request was sent after the pause was confirmed")


    def test_the_transport_holds_the_pause_lock_across_the_send_only(self):
        """R1c：capable 传输在 send phase 内持确认锁；暂停确认后 0 次发送。"""

        import life_request_budget as lrb

        state_root = odl.life_state_root(self.instance)
        self._prepared_instance()
        sent: list[str] = []
        guard = life_pause.RequestSendGuard(state_root)

        class Capable:
            send_split_capable = True

            def invoke(self, request, *, schema_retry_index=0):
                with guard.sending("model"):
                    sent.append("decision:%d" % schema_retry_index)
                return "ok"

            def complete(self, *args, **kwargs):
                with guard.sending("model"):
                    sent.append("content")
                return "ok"

        budget = lrb.LifeRequestBudget(state_root, run_id="run:w05rr1c-capable",
                                       utc_day_max=10, run_window_max=10)
        budget.ensure_window(allow_new=True)
        cognition = lrb.BudgetedCognitionAdapter(Capable(), budget, admission=guard)
        client = lrb.BudgetedModelClient(Capable(), budget, admission=guard)
        cognition.invoke(object(), schema_retry_index=0)
        cognition.invoke(object(), schema_retry_index=1)
        client.complete("x")
        self.assertEqual(sent, ["decision:0", "decision:1", "content"])
        self.assertEqual([item["kind"] for item in cognition.attempts], ["decision", "retry"])
        self.assertEqual(budget.view()["utc_day_calls"], 3)

        # 确认 pause：send phase 的入锁重读必须拦住三个 kind，且预留不返还。
        life_pause.set_paused(state_root, True, reason="w05rr1c")
        sent.clear()
        before = budget.view()["utc_day_calls"]
        for call, stage in (
                (lambda: cognition.invoke(object(), schema_retry_index=0), "before_model_send"),
                (lambda: cognition.invoke(object(), schema_retry_index=2), "before_model_send"),
                (lambda: client.complete("x"), "before_model_send")):
            with self.assertRaises(life_pause.PauseConfirmedError) as blocked:
                call()
            self.assertEqual(blocked.exception.stage, stage)
        self.assertEqual(sent, [], "a request was sent after the pause was confirmed")
        self.assertEqual(budget.view()["utc_day_calls"], before + 3)
        life_pause.set_paused(state_root, False, reason="w05rr1c")

    def test_a_pause_confirmed_while_a_finite_owner_call_runs_waits_for_it(self):
        """有限提交竞态：owner 调用进行中确认 pause → 确认等这次提交结束，之后零新提交。"""

        entered = threading.Event()
        release = threading.Event()
        cognition = RecordingCognition("artifact")
        fixture = self._mixed_fixture(cognition, block_owner=(entered, release))
        data = fixture.data
        state_root = odl.life_state_root(self.instance)
        head_before = self._head(data)["version_id"]
        round_done: dict = {}

        def run_round():
            round_done["out"] = fixture.cycle.evaluate_once(
                trigger_ref="evt:w05rr1b:finite-commit", act=True)

        worker = threading.Thread(target=run_round, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(30), "the owner call never started")
        confirmed: dict = {}
        try:
            pause_worker = threading.Thread(
                target=lambda: confirmed.setdefault(
                    "proc", self._confirm_pause_in_its_own_process(
                        state_root, reason="w05rr1b-during-commit")), daemon=True)
            pause_worker.start()
            pause_worker.join(timeout=2.0)
            # 有限提交还握着确认锁：pause 必须等它结束，不能抢在前面返回。
            self.assertTrue(pause_worker.is_alive(),
                            "the pause returned while a finite owner call was still running")
            release.set()
            worker.join(timeout=60)
            pause_worker.join(timeout=60)
        finally:
            release.set()
        worker.join(timeout=60)

        proc = confirmed.get("proc")
        self.assertIsNotNone(proc, "the pause subprocess never ran")
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        out = round_done.get("out") or {}
        self.assertEqual(out.get("outcome"), "ACTED", out)
        head_after = self._head(data)["version_id"]
        self.assertNotEqual(head_after, head_before)
        self.assertEqual(head_after, self._head(data)["version_id"])
        # 确认之后：不再开始新决策、不再生成内容、不再提交
        calls_before = cognition.call_count
        content_calls_before = fixture.writer.call_count
        after = fixture.cycle.evaluate_once(trigger_ref="evt:w05rr1b:after-commit", act=True)
        self.assertEqual(after.get("outcome"), "PAUSED_BY_OPERATOR", after)
        self.assertEqual(cognition.call_count, calls_before)
        self.assertEqual(fixture.writer.call_count, content_calls_before)
        self.assertEqual(self._head(data)["version_id"], head_after)



# =====================================================================================
# §5 请求预算：发送前原子预留、不返还、跨日/回拨/坏状态/窗口上限
# =====================================================================================


class RequestBudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._saved = dict(os.environ)
        self.root = Path(tempfile.mkdtemp(prefix="w05rbudget-"))
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved)
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _budget(self, **kwargs):
        run_id = kwargs.pop("run_id", "run:w05r")
        budget = lrb.LifeRequestBudget(self.root, run_id=run_id, **kwargs)
        budget.ensure_window(allow_new=kwargs.pop("allow_new", True))
        return budget

    def test_a_reservation_is_persisted_before_it_is_used_and_never_refunded(self):
        budget = self._budget(utc_day_max=5, run_window_max=5)
        first = budget.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(first["utc_day_calls"], 1)
        # 重新打开（模拟进程死后重启）：预留还在，没有被退回
        reopened = lrb.LifeRequestBudget(self.root, utc_day_max=5, run_window_max=5,
                                        run_id="run:w05r")
        view = reopened.view()
        self.assertEqual(view["utc_day_calls"], 1)
        self.assertEqual(view["run_window_calls"], 1)
        self.assertIn("no release API", view["refund_policy"])
        self.assertFalse(hasattr(reopened, "release"))

    def test_decision_content_and_retry_all_count_against_both_ceilings(self):
        budget = self._budget(utc_day_max=10, run_window_max=10)
        budget.reserve(lrb.REQUEST_KIND_DECISION)
        budget.reserve(lrb.REQUEST_KIND_CONTENT)
        budget.reserve(lrb.REQUEST_KIND_RETRY)
        view = budget.view()
        self.assertEqual(view["utc_day_calls"], 3)
        self.assertEqual(view["run_window_calls"], 3)
        kinds = [token["kind"] for token in view["recent_tokens"]]
        self.assertEqual(kinds, ["decision", "content", "retry"])

    def test_the_wrappers_reserve_at_the_http_boundary_not_after_it(self):
        budget = self._budget(utc_day_max=10, run_window_max=10)
        sent: list[str] = []

        class Inner:
            def invoke(self, request, *, schema_retry_index=0):
                sent.append("decision:%d" % schema_retry_index)
                return "ok"

            def complete(self, *args, **kwargs):
                sent.append("content")
                return "ok"

        cognition = lrb.BudgetedCognitionAdapter(Inner(), budget)
        client = lrb.BudgetedModelClient(Inner(), budget)
        cognition.invoke(object(), schema_retry_index=0)
        cognition.invoke(object(), schema_retry_index=1)
        client.complete("x")
        self.assertEqual(sent, ["decision:0", "decision:1", "content"])
        self.assertEqual(len(cognition.attempts), 2)
        self.assertEqual(cognition.attempts[0]["kind"], "decision")
        self.assertEqual(cognition.attempts[1]["kind"], "retry")
        self.assertEqual(cognition.attempts[1]["reservation"]["kind"], "retry")
        # 每个 attempt 都有自己的预留 token，且都真的落盘了
        self.assertEqual(budget.view()["utc_day_calls"], 3)
        self.assertEqual(len({item["reservation"]["token"]
                              for item in cognition.attempts}), 2)

    def test_a_refusal_never_reaches_the_provider(self):
        budget = self._budget(utc_day_max=0, run_window_max=10)
        sent: list[str] = []

        class Inner:
            def invoke(self, request, *, schema_retry_index=0):
                sent.append("decision")
                return "ok"

        cognition = lrb.BudgetedCognitionAdapter(Inner(), budget)
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            cognition.invoke(object())
        self.assertEqual(caught.exception.reason_code, lrb.REASON_UTC_DAY_LIMIT)
        self.assertEqual(sent, [])
        self.assertEqual(cognition.attempts, [])
        self.assertEqual(cognition.refusals[0]["kind"], "decision")

    def test_a_corrupt_ledger_fails_closed_instead_of_reading_as_zero(self):
        budget = self._budget(utc_day_max=10, run_window_max=10)
        budget.reserve(lrb.REQUEST_KIND_DECISION)
        lrb.budget_path(self.root).write_text("{ not json", encoding="utf-8")
        check = budget.check()
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason_code"], lrb.REASON_STATE_CORRUPT)
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.reserve(lrb.REQUEST_KIND_DECISION)
        self.assertEqual(caught.exception.reason_code, lrb.REASON_STATE_CORRUPT)
        self.assertEqual(budget.view()["state"], lrb.STATE_CORRUPT)

    def test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset(self):
        budget = self._budget(utc_day_max=2, run_window_max=100)
        # 整条用例自己带时钟：只从预算自己的账本读一次“今天是哪天”，之后每次
        # reserve/check/view 都显式补丁。写死日期会在真实 UTC 恰好等于那一天时把
        # “跨日”补丁变成恒等变换，新的一天分支不可达，于是退化成当天的上限拒绝。
        initial_day = budget.view()["utc_day"]
        midnight = datetime.strptime(initial_day, "%Y-%m-%d")
        next_day = (midnight + timedelta(days=1)).strftime("%Y-%m-%d")
        prev_day = (midnight - timedelta(days=1)).strftime("%Y-%m-%d")
        day_one = midnight.replace(hour=12, tzinfo=timezone.utc)
        day_two = day_one + timedelta(days=1)

        def clock(day: str):
            return unittest.mock.patch.object(lrb, "_utc_day", lambda moment=None: day)

        with clock(initial_day):
            budget.reserve(lrb.REQUEST_KIND_DECISION, now=day_one)
            budget.reserve(lrb.REQUEST_KIND_DECISION, now=day_one)
            self.assertFalse(budget.check()["allowed"])

        # 跨日：新的一天有新的额度，历史保留
        with clock(next_day):
            allowed = budget.check()
            self.assertTrue(allowed["allowed"], allowed)
            self.assertEqual(allowed["utc_day"], next_day)
            budget.reserve(lrb.REQUEST_KIND_DECISION, now=day_two)
            view = budget.view()
            self.assertEqual(view["utc_day"], next_day)
            self.assertEqual(view["utc_day_calls"], 1)
            self.assertEqual(view["history"][initial_day], 2)
            self.assertEqual(view["history"][next_day], 1)
            # 墙钟回拨到前一天：不清已花计数，也不另开一天
            with clock(prev_day):
                rolled_back = budget.view()
                self.assertEqual(rolled_back["utc_day"], next_day)
                self.assertEqual(rolled_back["utc_day_calls"], 1)
                self.assertEqual(rolled_back["history"][initial_day], 2)
                self.assertNotIn(prev_day, rolled_back["history"])
                # 也不返还额度：再花一次就到上限，仍然记在 next_day 名下
                flipped_back = budget.reserve(lrb.REQUEST_KIND_DECISION, now=day_two)
                self.assertEqual(flipped_back["utc_day"], next_day)
                spent = budget.view()
                self.assertEqual(spent["utc_day_calls"], 2)
                self.assertNotIn(prev_day, spent["history"])
                self.assertFalse(budget.check()["allowed"])

    def test_a_plain_restart_continues_the_window_and_only_an_explicit_new_one_opens(self):
        first = lrb.LifeRequestBudget(self.root, utc_day_max=10, run_window_max=10,
                                      run_id="run:w05r")
        self.assertEqual(first.ensure_window(allow_new=True)["state"], "opened")
        first.reserve(lrb.REQUEST_KIND_DECISION)

        # 普通重启：同一 run identity，继续同一个窗口
        restarted = lrb.LifeRequestBudget(self.root, utc_day_max=10, run_window_max=10,
                                          run_id="run:w05r")
        self.assertEqual(restarted.ensure_window()["state"], "existing")
        self.assertEqual(restarted.view()["run_window_calls"], 1)

        # 想在别 identity 下开新窗口：必须显式允许，否则被拒
        other = lrb.LifeRequestBudget(self.root, utc_day_max=10, run_window_max=10,
                                      run_id="run:w05r-new")
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            other.ensure_window()
        self.assertEqual(caught.exception.reason_code, lrb.REASON_RUN_WINDOW_NOT_AUTHORIZED)
        self.assertEqual(other.ensure_window(allow_new=True)["state"], "opened")

    def test_the_run_window_ceiling_stops_the_run_even_below_the_day_ceiling(self):
        budget = self._budget(utc_day_max=100, run_window_max=1)
        budget.reserve(lrb.REQUEST_KIND_DECISION)
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.reserve(lrb.REQUEST_KIND_CONTENT)
        self.assertEqual(caught.exception.reason_code, lrb.REASON_RUN_WINDOW_LIMIT)

    def test_the_one_day_entry_always_writes_both_request_ceilings(self):
        data = _prepared_instance_environment()
        self.assertEqual(data["environment"][odl.REQUEST_UTC_DAY_MAX_ENV], "7")
        self.assertEqual(data["environment"][odl.REQUEST_RUN_WINDOW_MAX_ENV], "9")
        self.assertTrue(data["environment"][odl.RUN_ID_ENV])
        # 没有给 World socket 时不发明一个传输
        self.assertNotIn("WORLD_BODY_SOCKET", data["environment"])


def _prepared_instance_environment() -> dict:
    """只为了读一眼 prepare 写下的环境（不启动任何服务）。"""

    import tempfile
    from unittest import mock

    root = Path(tempfile.mkdtemp(prefix="w05renv-")) / "instance"
    captured: dict = {}

    def fake_supply_call(socket_path, request, **kwargs):
        op = request.get("op")
        if op == "issue_grant":
            return {"status": "ALLOW", "grant_id": "grant:w05r"}
        if op == "provision_workspace":
            return {"status": "OK", "workspace": {"workspace_id": "workspace:w05r"}}
        if op == "create_artifact":
            return {"status": "OK", "artifact_id": "artifact:w05r",
                    "version_id": "artifact-version:w05r"}
        return {"status": "OK"}

    def fake_prepare_world(world_home, package_dir, *, seed, theme, provided):
        return "coastal_town_w05r", "supplied"

    try:
        with mock.patch.object(odl, "supply_call", fake_supply_call), \
                mock.patch.object(odl, "_prepare_world", fake_prepare_world), \
                mock.patch.object(odl.control, "set_enabled", lambda *a, **k: None):
            odl.prepare(instance=root, supply_socket="/tmp/w05r-supply.sock",
                        governance_socket="/tmp/w05r-gov.sock", subject="test:w05r",
                        title="W05R env", request_utc_day_max=7,
                        request_run_window_max=9, run_id="run:w05r-env")
        captured = odl.read_instance(root)
    finally:
        import shutil

        shutil.rmtree(root.parent, ignore_errors=True)
    return captured


if __name__ == "__main__":
    unittest.main()

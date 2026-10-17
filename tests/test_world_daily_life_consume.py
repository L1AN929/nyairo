#!/usr/bin/env python3
"""I（世界日常）：真实机会 → AG-0/AG-1/AG-2 → AR-0 → World owner 的消费与出行。

本文件是 I 包的定向证据，**不继承**既有 TestCase（避免误收别人的用例）。夹具是
真实本地栈：真实 Life Supply owner（socket 服务）、真实 ``WorldBodyService``
（真 lease + 真 gateway.sock + 真授权注册表 + 真 substrate）、真实 World 包
``examples/worlds/daily_neighborhood_v1``、真实 ProductionLifeRuntime + 真实 AR-0
适配器，只有"模型"是确定性替身（施工单允许），从不联网。

消费夹具的声明与初始 placement 只在**初始化时**按真实 owner 合法创建（写
``state/world_object_capabilities.json`` 的 ``consumables`` 块 + 基座 placement），
测试中不凭结果改对象；所有 home 落在 ``/tmp/nyaw05r.jdK3q/i-daily-*`` 并**保留**
（不删除、不清理）。

覆盖：
1. 一次观察同时给出 ≥2 个竞争机会（MOVE 地点 + 一份可消费物件），每条带
   subject/source/time/validity/uncertainty，且 catalog 真的来自世界包时间表；
2. 由 AG-1 选中的 CONSUME 候选经 AG-2 → AR-0 → Intent Executor → gateway →
   Resolver 真的用掉**一份** instance：placement 消失、同一原子保存里落 owner
   回执、Body 恰好一条后果、不冒充到达；
3. 同一次观察里的 MOVE 仍走计时旅程：出发 ≠ 到达；
4. 负控：owner 说不可用的 CONSUME 条目不会被翻译成机会；
5. 安静：没有任何真实材料时 0 模型调用。

需要 POSIX（fcntl + AF_UNIX）；原生 Windows 自动跳过。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

# 复用既有夹具的 owner 侧构件（只 import，不继承它的 TestCase）。
from test_one_day_life import (Services, WorldGateway, StubWriter,  # noqa: E402
                               posix_only)

import one_day_life as odl  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import world_body_substrate as wbs  # noqa: E402
import world_consumable_contract as contract  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402
from world_journey_action_adapter import (JourneyWorldResolver,  # noqa: E402
                                         RealJourneyWorldActionAdapter)

FIXTURE_ROOT = Path("/tmp/nyaw05r.jdK3q")
WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
SUBJECT = "test:i-daily"
FOOD = "i-food-riceball-01"
READING = ("第一页：房间里的桌子与窗。\n%%PAGE%%\n"
           "第二页：窗外是一条街，街角有树。\n%%PAGE%%\n"
           "第三页：天色渐暗，风从海上过来。\n")

ENV_GATES = {"REAL_WORKSPACE_PROVISION": "ON", "REAL_ARTIFACT_ACTION": "ON"}
GATE = {"WORLD_BODY_INTENT_MUTATION_GATE": "canonical"}

#: AG-1 Section 65: 只有这些候选类别可以被 START。替身模型与真模型看同一份候选，
#: 所以它也按这份清单过滤，而不是抓第一个候选硬塞 START。
STARTABLE_KINDS = frozenset({
    "RESPOND_USER_REQUEST", "CONSIDER_WORLD_OPPORTUNITY", "FULFILL_COMMITMENT",
    "PURSUE_GOAL_STEP", "CONSIDER_PERSONAL_OPPORTUNITY",
})


def unique_root(prefix: str = "i-daily-") -> Path:
    """一个唯一的、保留的夹具根（创建一次，永不删除）。"""

    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_ROOT / (prefix + uuid.uuid4().hex[:12])
    root.mkdir(parents=True, exist_ok=False)
    return root


class StubCognition:
    """确定性替身决策：只从 AG-1 真正给的候选里挑，并按 target 命名空间分流。"""

    model_id = "stub:i-daily"
    model_version = "i-daily-stub-v1"

    def __init__(self, *, want: str = "object") -> None:
        self.want = want
        self.call_count = 0
        self.offered: list[list] = []

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.call_count += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        self.offered.append([(c.get("candidate_kind"), tuple(c.get("target_refs") or []))
                             for c in candidates])
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]

        def refs(candidate):
            return [str(r) for r in (candidate.get("target_refs") or [])]

        world = [c for c in candidates
                 if c.get("candidate_kind") == "CONSIDER_WORLD_OPPORTUNITY"
                 and c.get("candidate_kind") in STARTABLE_KINDS]
        if self.want == "object":
            chosen = [c for c in world if any(r.startswith("object:") for r in refs(c))]
        else:
            chosen = [c for c in world if any(r.startswith("place:") for r in refs(c))]
        if not chosen:
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": allowed[:1], "short_rationale": "no legal candidate",
                   "deferred_candidate_ids": []}
        else:
            raw = {"decision": "START", "selected_candidate_id": chosen[0].get("candidate_id"),
                   "reason_codes": allowed[:1], "short_rationale": "stub choice",
                   "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:stub:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


class QuietProvider:
    """没有任何真实机会的观察缝（安静负控用）。"""

    last_observation = None
    last_action_space = None
    last_opportunity_view: list = []
    last_catalog = None

    def __call__(self):
        return []


@posix_only
class DailyWorldConsumeTest(unittest.TestCase):
    """真实 World owner + 真实 Life runtime，一个实例一段连续日常。"""

    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        self._runtimes: list = []
        self.root = unique_root()
        self.instance = self.root / "instance"
        self.services = Services(self.root / "supply")
        self.addCleanup(self.services.stop)
        self._gate = mock.patch.dict(os.environ, GATE)
        self._gate.start()
        self.addCleanup(self._gate.stop)
        self.prepared = self._prepare()
        self.data = odl.read_instance(self.instance)
        self.world_home = Path(str(self.data["world_home"]))
        # 夹具初始化：按真实 owner 的格式声明一份可食物件，并把它放在她此刻所在的
        # 地点（家，世界包里常开）。测试过程中不再改对象。
        self._declare_consumable()
        self._place_food("home")
        self.gateway = WorldGateway(self.world_home, world_id=self.data["world_id"],
                                    environment=odl.instance_environment(self.instance))
        self.addCleanup(self.gateway.stop)

    def tearDown(self) -> None:
        for runtime in self._runtimes:
            try:
                runtime.close()
            except Exception:
                pass
        self._runtimes = []
        os.environ.clear()
        os.environ.update(self._saved_env)

    # -- fixture -----------------------------------------------------------------

    def _prepare(self) -> dict:
        with mock.patch.dict(os.environ, ENV_GATES):
            return odl.prepare(instance=self.instance,
                               supply_socket=self.services.supply_path,
                               governance_socket=self.services.gov_path,
                               subject=SUBJECT, title="I 日常夹具",
                               world_package=WORLD_PACKAGE,
                               reading_title="I 阅读材料", reading_content=READING,
                               create_private_items=True)

    def _declare_consumable(self) -> None:
        path = self.world_home / "state" / "world_object_capabilities.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {}
        if path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
        document.setdefault("schema_version", wbs.CAPABILITIES_SCHEMA)
        document.setdefault("objects", {})
        document["consumables"] = {FOOD: {"consumable": True, "free_to_consume": True}}
        path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True),
                        encoding="utf-8")

    def _place_food(self, place: str) -> None:
        store = wbs.SubstrateStore(wbs.substrate_path(self.world_home))
        state = store.load()
        self.assertIsNotNone(state, "init_instance must have written a substrate")
        state = wbs.place_object(state, FOOD, {"kind": "room", "location_id": place,
                                               "area_id": place})
        store.save(state)

    # -- wiring ------------------------------------------------------------------

    def env(self) -> dict:
        env = odl.instance_environment(self.instance)
        env.update(self.gateway.environment())
        return env

    def runtime(self, cognition, env) -> tuple:
        state_root = odl.life_state_root(self.instance)
        port = lap.LifeArtifactPort(str(self.services.supply_path), self.data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=self.data["artifact_grant_id"],
            workspace_id=self.data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=self.instance / "life", artifact_action_adapter=adapter,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)))
        self._runtimes.append(runtime)
        return runtime, port

    def provider(self, env) -> wmos.WorldMoveOpportunityProvider:
        import action_intent
        import world_body_client as wbc

        return wmos.WorldMoveOpportunityProvider(
            client=wbc.WorldBodyClient(environment=env), action_intent=action_intent,
            bridge_dir=str(ROOT / "components" / "world" / "service" / "bridge"),
            environment=env, world_home=str(self.world_home))

    def cycle(self, *, want: str = "object", provider=None):
        env = self.env()
        cognition = StubCognition(want=want)
        runtime, port = self.runtime(cognition, env)
        cycle = alc.AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id=self.data["subject_id"],
            workspace_id=self.data["workspace_id"],
            artifact_grant_id=self.data["artifact_grant_id"],
            writer=StubWriter(), enabled=True, env=env,
            world_home=str(self.world_home),
            world_opportunity_provider=(provider if provider is not None
                                        else self.provider(env)),
            work_artifact_id=self.data["work_artifact_id"],
            reading_artifact_id=self.data.get("reading_artifact_id"),
            private_items_artifact_id=self.data.get("private_items_artifact_id"))
        return SimpleNamespace(cycle=cycle, cognition=cognition, env=env, runtime=runtime,
                               port=port)

    # -- owner reads -------------------------------------------------------------

    def substrate(self) -> dict:
        return wbs.SubstrateStore(wbs.substrate_path(self.world_home)).load()

    def placement(self, object_id: str):
        return wbs.placement_of(self.substrate(), object_id)

    def receipts(self) -> dict:
        return self.substrate().get("consumptions") or {}

    def consequence_records(self) -> list:
        path = self.world_home / "data" / "body" / "consequences.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def current_place(self) -> str:
        state = json.loads((self.world_home / "data" / "world" / "world_state.json")
                           .read_text(encoding="utf-8"))
        return str(state["location"]["place_id"])

    # -- 1. 竞争机会 --------------------------------------------------------------

    def test_one_observation_offers_competing_kinds_with_sources_and_validity(self):
        env = self.env()
        provider = self.provider(env)
        items = provider()
        moves = [i for i in items if i["target_refs"][0].startswith("place:")]
        consumes = [i for i in items if i["target_refs"][0].startswith("object:")]
        # 同一时刻两类合法机会并存：一个她真能走到的地方 + 一份 owner 说现在可用的物件。
        # MOVE 的候选集就是 World 自己此刻给出的单跳候选（不是这一层自造的路线表）。
        self.assertGreaterEqual(len(moves), 1, items)
        self.assertEqual([i["target_refs"] for i in consumes], [["object:%s" % FOOD]])
        self.assertGreaterEqual(len(moves) + len(consumes), 2, items)

        for item in items:
            self.assertEqual(item["subject_id"], "chiyo.global")
            self.assertTrue(item["observed"])
            self.assertTrue(item["valid_from"] and item["valid_until"])
            self.assertTrue(item["valid_from"] < item["valid_until"])
            self.assertTrue(item["perception_ref"].startswith("percept:"))
            self.assertTrue(item["world_revision_ref"].startswith("wrev:"))
            self.assertEqual(item["location_ref"], "place:home")

        # W 目录真的被读了：营业/距离事实来自世界包自己的时间表，且不是授权。
        catalog = provider.last_catalog
        self.assertEqual(catalog["status"], "available", catalog)
        self.assertEqual(catalog["schedule_source"], "world_package_schedule")
        self.assertIs(catalog["action_grant"], False)
        view = {entry["target_ref"]: entry for entry in provider.last_opportunity_view}
        self.assertEqual(view["object:%s" % FOOD]["source"], "world_gateway_available_actions")
        self.assertEqual(view["object:%s" % FOOD]["availability"], "OPEN")
        self.assertEqual(view["object:%s" % FOOD]["action_grant"], False)
        places = [entry for entry in provider.last_opportunity_view if entry["kind"] == "MOVE"]
        self.assertTrue(all(entry["walk_seconds"] and entry["walk_seconds"] > 0
                            for entry in places), places)

        # 观察只读：目录读完世界状态字节不变。
        before = (self.world_home / "data" / "world" / "world_state.json").read_bytes()
        provider()
        self.assertEqual((self.world_home / "data" / "world" / "world_state.json").read_bytes(),
                         before)

    # -- 2. 真实消费 --------------------------------------------------------------

    def test_a_consume_decision_really_uses_one_instance_through_the_owner_gateway(self):
        fixture = self.cycle(want="object")
        self.assertIsNotNone(self.placement(FOOD), "夹具里的那份必须先在场上")

        out = fixture.cycle.evaluate_once(trigger_ref="evt:i:consume", act=True)
        self.assertEqual(out.get("outcome"), "ACTED", out)
        # 三条真实路径同时在线（作品/阅读/世界），这一步由提交的 Decision 路由到 World。
        self.assertIn("WORLD", str(out.get("step_path")), out)
        self.assertEqual(out.get("action_kind"), "WORLD_ACTION", out)
        self.assertEqual(out.get("world_verb"), "CONSUME", out)
        self.assertEqual(out.get("adoption_status"), "APPLIED", out)

        # owner 侧事实：同一份 instance 真的被用掉，placement 消失。
        self.assertIsNone(self.placement(FOOD), self.substrate())

        # 回执与这次执行身份对齐，且不是"物件不见了"推出来的。
        receipts = self.receipts()
        self.assertEqual(len(receipts), 1, receipts)
        execution_id, receipt = next(iter(receipts.items()))
        self.assertTrue(out.get("consumption_receipt_present"), out)
        self.assertEqual(receipt["object_id"], FOOD)
        self.assertEqual(receipt["execution_id"], execution_id)
        self.assertEqual(receipt["body_id"], "chiyo_body")
        self.assertEqual(receipt["placement_before"]["location_id"], "home")
        self.assertEqual(out.get("consumed_object_id"), FOOD)
        self.assertEqual(out.get("consumption_execution_id"), execution_id)
        self.assertEqual(out.get("consumption_receipt_ref"), receipt["receipt_id"])

        # Body：恰好一条后果，且不是营养/饱足/健康。
        consequences = [c for c in self.consequence_records()
                        if str(c.get("execution_id")) == str(execution_id)]
        self.assertEqual(len(consequences), 1, self.consequence_records())
        blob = json.dumps(consequences[0], ensure_ascii=False).lower()
        for word in ("nutrition", "satiety", "hunger", "health", "recovery"):
            self.assertNotIn(word, blob.replace("not nutrition", "").replace(
                "not satiety", "").replace("not hunger", "").replace(
                "not health", "").replace("not recovery", ""))

        # 消费不是到达：位置没变，也没有把"到达"塞进这一轮。
        self.assertEqual(self.current_place(), "home")
        self.assertIsNone(out.get("journey"))

        # LR-5 收到的是 owner 回执带来的世界事实，不是凭空一句"吃过了"：
        # 完成判据真的被这次回执满足，Activity 真的结束并释放前台（不是"travel 完成"）。
        self.assertNotIn("progress_ingest_error", out, out)
        self.assertEqual(out.get("progress_state"), "COMPLETION_ELIGIBLE", out)
        canonical = fixture.runtime.canonical_state()
        self.assertIsNone(canonical.get("foreground_activity_ref"), canonical)
        self.assertTrue(canonical.get("last_transition_ref"), canonical)

        # 候选真的空了：再观察一次，同一份 instance 不再出现（不复活、不补货）。
        after = self.provider(fixture.env)
        self.assertEqual([i for i in after() if i["target_refs"][0].startswith("object:")], [])
        self.assertIsNone(self.placement(FOOD))
        self.assertEqual(len(self.receipts()), 1, self.receipts())
        self.assertEqual(len(self.consequence_records()), len(consequences))

    # -- 3. 出行仍是计时旅程 ------------------------------------------------------

    def test_a_move_decision_still_departs_without_arriving(self):
        fixture = self.cycle(want="place")
        origin = self.current_place()
        out = fixture.cycle.evaluate_once(trigger_ref="evt:i:move", act=True)
        self.assertEqual(out.get("outcome"), "IN_TRANSIT", out)
        self.assertEqual(out.get("world_verb"), "MOVE", out)
        self.assertEqual(self.current_place(), origin)
        self.assertIsNot(out["journey"]["arrived"], True)
        self.assertTrue(out["journey"]["journey_id"])
        # 出行没有消耗任何物件。
        self.assertEqual(self.receipts(), {})
        self.assertIsNotNone(self.placement(FOOD))

    # -- 4. 负控 ------------------------------------------------------------------

    def test_an_unavailable_consume_entry_is_not_turned_into_an_opportunity(self):
        observation = {"location": {"place_id": "home"}, "world_revision": 3,
                       "world_id": "daily_neighborhood_v1"}
        refused = {"actions": [{"verb": "CONSUME", "available": False,
                                "blocked_reason": "LOCATION_CLOSED", "candidates": []}]}
        self.assertEqual(wmos.observed_consume_opportunities(
            observation, available_actions=refused,
            occurred_at="2026-10-17T00:00:00+00:00",
            observed_at="2026-10-17T00:00:00+00:00"), [])
        # 旧网关没有 CONSUME 条目：同样是"没有机会"，不是"也许可用"。
        self.assertEqual(wmos.observed_consume_opportunities(
            observation, available_actions={"actions": [{"verb": "MOVE", "available": True}]},
            occurred_at="2026-10-17T00:00:00+00:00",
            observed_at="2026-10-17T00:00:00+00:00"), [])
        # 不可读的目录只是"这一轮没有地点事实"，不会变成营业承诺。
        broken = wmos.read_location_catalog(None, None, hermes_home=None)
        self.assertEqual(broken["status"], "unavailable")
        self.assertEqual(broken["opportunities"], [])

    def test_owner_verdict_really_governs_the_offered_object(self):
        """owner 说不可消费时，候选里就没有它（声明换成不可食 → 候选为空）。"""

        path = self.world_home / "state" / "world_object_capabilities.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["consumables"] = {FOOD: {"consumable": False, "free_to_consume": True}}
        path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True),
                        encoding="utf-8")
        try:
            provider = self.provider(self.env())
            items = provider()
            self.assertEqual([i for i in items
                              if i["target_refs"][0].startswith("object:")], [])
        finally:
            document["consumables"] = {FOOD: {"consumable": True, "free_to_consume": True}}
            path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True),
                            encoding="utf-8")

    # -- 5. 安静 ------------------------------------------------------------------

    def test_a_quiet_instance_makes_no_model_call(self):
        env = self.env()
        cognition = StubCognition(want="object")
        runtime, _port = self.runtime(cognition, env)
        cycle = alc.AutonomousLifeCycle(runtime, artifact_port=None, writer=StubWriter(),
                                        enabled=True, env=env,
                                        world_opportunity_provider=QuietProvider())
        out = cycle.evaluate_once(trigger_ref="evt:i:quiet", act=True)
        self.assertEqual(out.get("outcome"), "NO_MATERIAL", out)
        self.assertEqual(cognition.call_count, 0)
        self.assertEqual(self.receipts(), {})
        self.assertIsNotNone(self.placement(FOOD))
        self.assertEqual(self.consequence_records(), [])


if __name__ == "__main__":
    unittest.main()

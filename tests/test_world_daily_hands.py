#!/usr/bin/env python3
"""v0.4 B（世界日常）：真实的"拿起/放下"从观测到 owner 提交，及有界现场视图。

本文件是 B 包的定向证据，**不继承**既有 TestCase（避免误收别人的用例）。两个夹具：

1. ``DailyActionEncodingTest`` 是纯编码/契约用例（不碰 owner，只读代码）：
   单个 target 的确定性、可逆、有界，拒绝一切越界（额外参数、container、hand 目的地、
   未知 verb、畸形 target），step 形状与 body helper 同形，完成事实只承认"读到了"。
   注意它与整个模块一样需要 POSIX 环境（``test_one_day_life`` 在导入时就 import fcntl），
   原生 Windows 上整模块跳过——这是本仓真实 owner 用例的统一前提，不是本文件的选择。
2. ``RealOwnerHandsTest`` 是真实 owner 用例（``posix_only``：需要 AF_UNIX + fcntl）：
   真实 ``WorldBodyService``（真 lease、真 ``gateway.sock``、真授权注册表、真 substrate）、
   真实世界包 ``examples/worlds/daily_neighborhood_v1``、真实 ``WorldBodyClient``、真实
   ``WorldBridgeResolver``（Intent Executor → gateway → Resolver）。home 落在
   ``/tmp/nyaw04b.hands/<unique>`` 并**保留**（不删除、不清理）。

覆盖（只做这一条链，不做资格项目）：
* owner 真拿起 → 真放下 → 从 substrate/快照读回 placement/hands；
* 同一 idempotency key 重试复用同一执行，不改世界；
* stale/refused/unknown 一律不伪装成功，畸形 target 是"提交前确定拒绝"；
* 后置状态可核对，但明确不证明"这次操作"归属；
* ``daily_life_context`` 的名字/位置/营业都有源，远处未观测的物件与容器目的地诚实标未知。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

# 复用既有夹具的 owner 侧构件（只 import，不继承它的 TestCase）。这个夹具与仓库里其它
# 真实 owner 用例一样需要 POSIX（fcntl + AF_UNIX），所以整个模块在原生 Windows 上跳过。
from test_one_day_life import WorldGateway, posix_only  # noqa: E402

import action_reality_ledger as ar0  # noqa: E402
import life_body_action_targets as body_targets  # noqa: E402
import life_daily_action_targets as targets  # noqa: E402
import world_action_bridge_adapter as bridge  # noqa: E402
import world_body_substrate as wbs  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402

FIXTURE_ROOT = Path("/tmp/nyaw04b.hands")
WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
#: The object she really can pick up, and one that is deliberately somewhere else.
CUP = "i-cup-01"
FRAME = "i-frame-02"
GATE = {"WORLD_BODY_INTENT_MUTATION_GATE": "canonical"}


def unique_root(prefix: str = "b-hands-") -> Path:
    """一个唯一的、保留的夹具根（创建一次，永不删除）。"""

    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_ROOT / (prefix + uuid.uuid4().hex[:12])
    root.mkdir(parents=True, exist_ok=False)
    return root


class DailyActionEncodingTest(unittest.TestCase):
    """单 target 编码的契约：确定性、可逆、有界、不越权。"""

    def test_pick_up_round_trip_is_deterministic_and_reversible(self):
        target = targets.encode_daily_action_target(
            "PICK_UP", {"object_id": CUP, "slot_id": "right_hand"})
        self.assertEqual(target, "dailyact:pick_up:right_hand:%s" % CUP)
        # 同一个参数永远给同一个字符串（确定性）。
        self.assertEqual(
            targets.encode_daily_action_target(
                "pick_up", {"slot_id": "right_hand", "object_id": CUP}),
            target)
        parsed = targets.parse_daily_action_target(target)
        self.assertEqual(parsed["verb"], "PICK_UP")
        self.assertEqual(parsed["params"], {"object_id": CUP, "slot_id": "right_hand"})
        self.assertEqual(parsed["object_id"], CUP)
        self.assertEqual(parsed["target_ref"], target)

    def test_place_round_trip_carries_the_whole_placement_target(self):
        room = {"kind": "room", "location_id": "home", "area_id": "bedroom"}
        target = targets.encode_daily_action_target(
            "PLACE", {"object_id": CUP, "target": room})
        parsed = targets.parse_daily_action_target(target)
        self.assertEqual(parsed["verb"], "PLACE")
        self.assertEqual(parsed["params"]["object_id"], CUP)
        self.assertEqual(parsed["params"]["target"], room)
        # A real surface id contains the separator, so the field is encoded -- and comes
        # back byte-identical.
        surface = {"kind": "surface", "location_id": "home",
                   "surface_id": "desk_surface:i-desk-07"}
        target = targets.encode_daily_action_target(
            "PLACE", {"object_id": CUP, "target": surface})
        self.assertNotIn(":", target[len(targets.DAILY_ACTION_REF_PREFIX):].split(":")[0])
        self.assertEqual(targets.parse_daily_action_target(target)["params"]["target"],
                         surface)

    def test_encoding_refuses_anything_the_owner_does_not_admit(self):
        cases = (
            ("DROP", {"object_id": CUP, "slot_id": "left_hand"}),
            ("PICK_UP", {"object_id": CUP, "slot_id": "third_hand"}),
            ("PICK_UP", {"object_id": CUP}),
            ("PICK_UP", {"object_id": CUP, "slot_id": "left_hand", "force": True}),
            ("PICK_UP", "object-id-as-string"),
            ("PLACE", {"object_id": CUP, "target": {"kind": "container",
                                                    "location_id": "home",
                                                    "container_id": "i-box"}}),
            ("PLACE", {"object_id": CUP, "target": {"kind": "hand",
                                                    "slot_id": "left_hand"}}),
            ("PLACE", {"object_id": CUP, "target": {"kind": "room",
                                                    "location_id": "home"}}),
            ("PLACE", {"object_id": CUP}),
            ("PLACE", {"target": {"kind": "room", "location_id": "home",
                                  "area_id": "bedroom"}}),
        )
        for verb, params in cases:
            with self.subTest(verb=verb, params=params):
                with self.assertRaises(targets.DailyActionTargetError):
                    targets.encode_daily_action_target(verb, params)

    def test_parsing_rejects_malformed_and_ignores_other_namespaces(self):
        self.assertIsNone(targets.parse_daily_action_target("place:home"))
        self.assertIsNone(targets.parse_daily_action_target("object:i-cup-01"))
        self.assertIsNone(targets.parse_daily_action_target(""))
        self.assertIsNone(targets.parse_daily_action_target(None))
        for malformed in ("dailyact:pick_up:left_hand",
                          "dailyact:pick_up:left_hand:%s:extra" % CUP,
                          "dailyact:pick_up:third_hand:%s" % CUP,
                          "dailyact:throw:left_hand:%s" % CUP,
                          "dailyact:place:container:home:i-box:%s" % CUP,
                          "dailyact:place:room:home:%s" % CUP,
                          "dailyact:place:room:home:bedroom:%s:x" % CUP):
            with self.subTest(target=malformed):
                with self.assertRaises(targets.DailyActionTargetError):
                    targets.parse_daily_action_target(malformed)

    def test_the_step_has_the_same_shape_as_the_body_helper(self):
        daily = targets.build_daily_action_step(
            "dailyact:pick_up:left_hand:%s" % CUP, origin_ref="place:home@r7")
        body = body_targets.build_body_action_step(
            "bodyact:pose:seated:south", place="home", revision=7)
        self.assertEqual(set(daily), set(body))
        self.assertEqual(set(daily["payload_spec"]), set(body["payload_spec"]))
        self.assertEqual(daily["step_kind"], body["step_kind"])
        self.assertEqual(daily["action_kind"], ar0.ACTION_KIND_WORLD)
        self.assertEqual(daily["payload_spec"]["verb"], "PICK_UP")
        self.assertEqual(daily["payload_spec"]["target"],
                         "dailyact:pick_up:left_hand:%s" % CUP)
        self.assertEqual(daily["payload_spec"]["expected_dependencies"], {})
        self.assertEqual(daily["expected_effect_predicates"],
                         [targets.EFFECT_PICKED_UP])
        # Deterministic per origin, and a different origin is a different action.
        self.assertEqual(daily["idempotency_key"],
                         targets.build_daily_action_step(
                             "dailyact:pick_up:left_hand:%s" % CUP,
                             origin_ref="place:home@r7")["idempotency_key"])
        self.assertNotEqual(daily["idempotency_key"],
                            targets.build_daily_action_step(
                                "dailyact:pick_up:left_hand:%s" % CUP,
                                origin_ref="place:home@r8")["idempotency_key"])
        with self.assertRaises(targets.DailyActionTargetError):
            targets.build_daily_action_step("place:home")

    def test_completion_names_only_what_the_snapshot_can_answer(self):
        pick = "dailyact:pick_up:left_hand:%s" % CUP
        self.assertEqual(targets.daily_action_completion(pick), {
            "fact_key": targets.FACT_KEY_DAILY_PLACEMENT,
            "expected_value": "world_fact:placement:%s:hand:left_hand" % CUP,
        })
        room = targets.encode_daily_action_target("PLACE", {
            "object_id": CUP,
            "target": {"kind": "room", "location_id": "home", "area_id": "bedroom"}})
        self.assertEqual(targets.daily_action_completion(room)["expected_value"],
                         "world_fact:placement:%s:room:home:bedroom" % CUP)

        # Picked up: the snapshot's own hands mapping satisfies it.
        snapshot = {"location": {"place_id": "home", "area_id": "bedroom"},
                    "hands": {"left_hand": CUP, "right_hand": None},
                    "visible_objects": [{"object_id": CUP, "kind": "hand",
                                         "slot_id": "left_hand", "in_hand": True}]}
        fact = targets.daily_action_observed_fact(pick, snapshot)
        self.assertTrue(fact["satisfied"], fact)
        self.assertEqual(fact["value"], "world_fact:placement:%s:hand:left_hand" % CUP)

        # Put down in the room she is standing in: kind is read, the place is the body's
        # own location and it is labelled as such instead of being asserted.
        snapshot = {"location": {"place_id": "home", "area_id": "bedroom"},
                    "hands": {"left_hand": None, "right_hand": None},
                    "visible_objects": [{"object_id": CUP, "kind": "room",
                                         "in_hand": False}]}
        fact = targets.daily_action_observed_fact(room, snapshot)
        self.assertTrue(fact["satisfied"], fact)
        self.assertIn(targets.UNKNOWN_ROOM_LOCATION, fact["unknowns"])

        # Not visible at all: no value, no satisfaction -- never an invented placement.
        fact = targets.daily_action_observed_fact(pick, {
            "location": {"place_id": "home", "area_id": "bedroom"},
            "hands": {"left_hand": None, "right_hand": None},
            "visible_objects": []})
        self.assertIsNone(fact["value"])
        self.assertFalse(fact["satisfied"])

        # No snapshot at all is the same honest answer.
        fact = targets.daily_action_observed_fact(pick, None)
        self.assertFalse(fact["satisfied"])
        self.assertEqual(fact["unknowns"], ["snapshot_unavailable"])


@posix_only
class RealOwnerHandsTest(unittest.TestCase):
    """真实 World owner：观察 → 拿起 → 放下 → 读回，拒绝不伪装成功。"""

    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        self.addCleanup(self._restore_env)
        gate = mock.patch.dict(os.environ, GATE)
        gate.start()
        self.addCleanup(gate.stop)
        self.root = unique_root()
        self.world_home = self.root / "world"

        import world_runtime_data as wrd
        from components.worldgen.instance import init_instance

        package = wrd.active_world({"CHIYO_WORLD_PACKAGE": str(WORLD_PACKAGE)})
        init_instance(WORLD_PACKAGE, self.world_home, world_id=package.world_id)
        self.world_id = package.world_id
        self._declare_capabilities()
        self._place(CUP, "home", "bedroom")
        self._place(FRAME, "park", "park")

        self.service_env = {"CHIYO_WORLD_PACKAGE": str(WORLD_PACKAGE),
                            "CHIYO_WORLD_HOME": str(self.world_home),
                            "WORLD_BODY_INTEGRATION_MODE": "canonical"}
        self.gateway = WorldGateway(self.world_home, world_id=self.world_id,
                                    environment=dict(self.service_env))
        self.addCleanup(self.gateway.stop)
        self.env = dict(self.service_env)
        self.env.update(self.gateway.environment())

        import action_intent
        import world_body_client as wbc

        self.action_intent = action_intent
        self.client = wbc.WorldBodyClient(environment=self.env)
        self.provider = self.provider_for()
        self.resolver = bridge.WorldBridgeResolver(
            client=self.client, environment=self.env, bridge_dir=str(BRIDGE_DIR))

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    # -- fixture -----------------------------------------------------------------

    def _declare_capabilities(self) -> None:
        """Declare both objects exactly the way the World owner reads them."""

        path = self.world_home / "state" / "world_object_capabilities.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {"schema_version": wbs.CAPABILITIES_SCHEMA,
                    "objects": {CUP: {"takeable": "true", "portable": "true",
                                      "placeable": "true"},
                                FRAME: {"takeable": "true", "portable": "true",
                                        "placeable": "true"}}}
        path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True),
                        encoding="utf-8")

    def _place(self, object_id: str, place: str, area: str) -> None:
        store = wbs.SubstrateStore(wbs.substrate_path(self.world_home))
        state = store.load()
        self.assertIsNotNone(state, "init_instance must have written a substrate")
        state = wbs.place_object(state, object_id,
                                 {"kind": "room", "location_id": place, "area_id": area})
        store.save(state)

    def provider_for(self) -> wmos.WorldMoveOpportunityProvider:
        return wmos.WorldMoveOpportunityProvider(
            client=self.client, action_intent=self.action_intent,
            bridge_dir=str(BRIDGE_DIR), environment=self.env,
            world_home=str(self.world_home))

    # -- owner reads -------------------------------------------------------------

    def substrate(self) -> dict:
        return wbs.SubstrateStore(wbs.substrate_path(self.world_home)).load()

    def placement(self, object_id: str):
        return wbs.placement_of(self.substrate(), object_id)

    def snapshot(self) -> dict:
        result = self.client.get_snapshot()
        self.assertEqual(result.outcome, "OK", result)
        return dict(result.data)

    def daily_opportunities(self, items) -> list[dict]:
        out = []
        for item in items:
            ref = str((item.get("target_refs") or [""])[0])
            if not ref.startswith(targets.DAILY_ACTION_REF_PREFIX):
                continue
            out.append(targets.parse_daily_action_target(ref))
        return out

    def submit(self, parsed: dict, *, origin_ref: str = "place:home@r1") -> dict:
        step = targets.build_daily_action_step(parsed["target_ref"],
                                              origin_ref=origin_ref)
        return self.resolver.resolve_action(
            idempotency_key=step["idempotency_key"], verb=parsed["verb"],
            target=parsed["target_ref"], expected_dependencies={})

    # -- 1. 真拿起 ---------------------------------------------------------------

    def test_the_owner_really_picks_up_what_the_observation_offered(self):
        items = self.provider()
        daily = self.daily_opportunities(items)
        picks = [entry for entry in daily if entry["verb"] == "PICK_UP"]
        # owner 只在她真的在这里、真的可取、真的有空手时才给候选：恰好一个。
        self.assertEqual(len(picks), 1, daily)
        parsed = picks[0]
        self.assertEqual(parsed["object_id"], CUP)
        self.assertEqual(parsed["params"]["slot_id"], "left_hand")  # owner 的手槽顺序
        # 每条机会仍是 AG-0 的 record 形状（不新契约）。
        raw = next(item for item in items
                   if str((item.get("target_refs") or [""])[0]) == parsed["target_ref"])
        for key in ("opportunity_id", "perception_ref", "world_revision_ref",
                    "valid_from", "valid_until", "occurred_at", "observed_at",
                    "observed", "status", "revision", "subject_id", "location_ref"):
            self.assertIn(key, raw)
        self.assertTrue(raw["observed"])
        self.assertEqual(raw["location_ref"], "place:home")

        before = self.placement(CUP)
        self.assertEqual(before["kind"], "room")

        result = self.submit(parsed)
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(result["execution_id"],
                         self.resolver.execution_id_for(
                             targets.build_daily_action_step(
                                 parsed["target_ref"],
                                 origin_ref="place:home@r1")["idempotency_key"],
                             "PICK_UP", parsed["target_ref"]))
        self.assertEqual(result["transport_result"], ar0.TRANSPORT_OK_SUCCEEDED)

        # owner 侧事实：物件真的进了那只手。
        placement = self.placement(CUP)
        self.assertEqual(placement["kind"], "hand")
        self.assertEqual(placement["slot_id"], "left_hand")
        self.assertEqual(self.snapshot()["hands"]["left_hand"], CUP)

        # 后置状态可以核对完成事实，但明确不证明"这次执行"归属。
        details = result["details"]
        self.assertIs(details["post_state_only"], True)
        self.assertIs(details["post_state_proves_this_execution"], False)
        self.assertTrue(details["placement_fact"]["satisfied"], details)
        read = self.resolver.read_daily_placement(CUP)
        self.assertTrue(read["observed"], read)
        self.assertEqual(read["placement"]["kind"], "hand")
        fresh = targets.daily_action_observed_fact(
            targets.parse_daily_action_target(parsed["target_ref"]), self.snapshot())
        self.assertTrue(fresh["satisfied"], fresh)

        # 同一个 idempotency key 重试：复用同一执行、世界不动。
        revision = self.substrate()["revision"]
        again = self.submit(parsed)
        self.assertEqual(again["execution_id"], result["execution_id"])
        self.assertEqual(self.substrate()["revision"], revision)

    # -- 2. 真放下 ---------------------------------------------------------------

    def test_the_owner_really_puts_it_down_and_the_fact_follows_the_owner(self):
        pick = [entry for entry in self.daily_opportunities(self.provider())
                if entry["verb"] == "PICK_UP"]
        self.assertEqual(len(pick), 1, pick)
        self.assertEqual(self.submit(pick[0])["status"], "committed")

        # 拿着东西之后才出现"放下"，且目的地就是她真的站着的这间房。
        items = self.provider()
        daily = self.daily_opportunities(items)
        places = [entry for entry in daily if entry["verb"] == "PLACE"]
        self.assertEqual(len(places), 1, daily)
        placed = places[0]
        self.assertEqual(placed["params"]["target"],
                         {"kind": "room", "location_id": "home", "area_id": "bedroom"})

        result = self.submit(placed, origin_ref="place:home@r2")
        self.assertEqual(result["status"], "committed", result)
        self.assertTrue(result["details"]["placement_fact"]["satisfied"], result)

        placement = self.placement(CUP)
        self.assertEqual(placement["kind"], "room")
        self.assertEqual(placement["location_id"], "home")
        self.assertEqual(placement["area_id"], "bedroom")
        hands = self.snapshot()["hands"]
        self.assertIsNone(hands["left_hand"])
        self.assertIsNone(hands["right_hand"])

        # 后置状态不是归属：同一份"拿起"事实在新状态下已经不成立。
        stale = targets.daily_action_observed_fact(
            targets.parse_daily_action_target(pick[0]["target_ref"]), self.snapshot())
        self.assertFalse(stale["satisfied"], stale)
        self.assertIn(targets.UNKNOWN_NOT_IN_HAND, stale["unknowns"])

    # -- 3. 拒绝/未知不伪装成功 ---------------------------------------------------

    def test_a_refusal_or_an_unknown_target_never_looks_like_success(self):
        revision = self.substrate()["revision"]

        # 手里没有东西：owner 拒绝 PLACE（物件在房里，不在手上）。
        not_held = targets.encode_daily_action_target("PLACE", {
            "object_id": CUP,
            "target": {"kind": "room", "location_id": "home", "area_id": "bedroom"}})
        result = self.submit(targets.parse_daily_action_target(not_held))
        self.assertEqual(result["status"], "rejected", result)
        self.assertNotEqual(result["transport_result"], ar0.TRANSPORT_OK_SUCCEEDED)
        self.assertEqual(self.placement(CUP)["kind"], "room")

        # 世界里没有这个物件：同样拒绝，不是"也许拿起来了"。
        ghost = targets.encode_daily_action_target(
            "PICK_UP", {"object_id": "i-ghost-99", "slot_id": "left_hand"})
        result = self.submit(targets.parse_daily_action_target(ghost))
        self.assertEqual(result["status"], "rejected", result)
        self.assertNotEqual(result["transport_result"], ar0.TRANSPORT_OK_SUCCEEDED)

        # 畸形 target：提交前的确定拒绝，什么都没改变，可以同 action id 重试。
        result = self.resolver.resolve_action(
            idempotency_key="idem_step:v02:world:dailyact:home:dailyact:pick_up:left_hand",
            verb="PICK_UP", target="dailyact:pick_up:left_hand",
            expected_dependencies={})
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["code"], "DAILY_ACTION_TARGET_INVALID", result)
        self.assertEqual(result["transport_result"],
                         ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT)
        self.assertEqual(self.substrate()["revision"], revision)

    # -- 4. 有界现场视图 ---------------------------------------------------------

    def test_the_live_context_names_things_and_says_what_it_cannot_know(self):
        items = self.provider()
        context = self.provider.daily_life_context(limit=8)
        self.assertEqual(context["status"], "available", context)
        self.assertEqual(context["reason_code"], None)
        self.assertIs(context["action_grant"], False)

        catalog = self.provider.last_catalog
        self.assertEqual(catalog["status"], "available", catalog)
        home_fact = next(item for item in catalog["opportunities"]
                         if item["location_id"] == "home")
        self.assertEqual(context["location"]["place_id"], "home")
        self.assertEqual(context["location"]["area_id"], "bedroom")
        # 名字来自世界自己的目录，不是把 id 美化了一下。
        self.assertEqual(context["location"]["name"], home_fact["name"])
        expected_area = next(area["name"] for area in home_fact["indoor_areas"]
                             if area["area_id"] == "bedroom")
        self.assertEqual(context["location"]["area_name"], expected_area)
        self.assertEqual(context["location"]["availability"],
                         home_fact["availability"]["status"])

        # 身体是她自己的事实。
        self.assertEqual(context["body"]["pose"], self.substrate()["pose"])
        self.assertEqual(context["body"]["orientation"],
                         self.substrate()["orientation"])
        # 手里什么都没拿：hands 是"真正拿着的手"的列表，空就是空，且明确说"读到了"。
        self.assertEqual(context["body"]["hands"], [])
        self.assertIs(context["body"]["hands_known"], True)
        self.assertEqual(context["body"]["free_slots"],
                         ["left_hand", "right_hand"])
        self.assertIs(context["body"]["hands_usable"], True)

        # 附近的地方是世界目录自己列的那些（这里是家，再按它自己的路程排序）。
        self.assertEqual(context["nearby"][0]["place_id"], "home")
        self.assertTrue(context["nearby"][0]["here"])
        self.assertEqual(context["nearby"][0]["name"], home_fact["name"])
        self.assertTrue(all(entry["place_id"] for entry in context["nearby"]))
        # 就在手边的物件：owner 说"在这里且可取"的那个；放在别处的绝不进来。
        nearby_ids = [entry["object_id"] for entry in context["nearby_objects"]]
        self.assertIn(CUP, nearby_ids)
        self.assertNotIn(FRAME, nearby_ids)


        # 每条机会都带名字/描述/时长/准备，且仍然只是数据（无授权、无打分）。
        for entry in context["opportunities"]:
            self.assertTrue(str(entry["target_ref"]).startswith("dailyact:")
                            or entry["target_ref"])
            self.assertTrue(entry["label"], entry)
            self.assertTrue(entry["description"], entry)
            self.assertIs(entry["action_grant"], False)
            self.assertEqual(entry["availability"], "AVAILABLE")
        pick = next(entry for entry in context["opportunities"]
                    if entry["verb"] == "PICK_UP")
        self.assertIn(CUP, pick["label"])
        self.assertEqual(pick["duration_seconds"], wmos.HAND_MANIPULATION_SECONDS)
        self.assertEqual(pick["duration_source"], wmos.DURATION_SOURCE_OWNER)
        self.assertEqual(pick["preparation_facts"]["slot_id"], "left_hand")
        self.assertEqual(pick["preparation_facts"]["free_slots"],
                         ["left_hand", "right_hand"])
        self.assertTrue(pick["preparation"], pick)

        # 未知诚实：容器目的地、别处物件的位置、远处没亲眼看到的地方。
        codes = set(context["unknowns"])
        self.assertIn("container_destinations_not_available", codes)
        self.assertIn("object_locations_not_exposed", codes)
        self.assertIn("places_not_observed", codes)
        self.assertLessEqual(len(context["unknowns"]), wmos.MAX_CONTEXT_UNKNOWNS)
        # 同一条未知也有带细节的一份（给需要解释的地方用，不是给模型读的第二份）。
        detail_codes = {entry["code"] for entry in context["unknown_details"]}
        self.assertEqual(detail_codes, codes)


        # 裁剪有界且明说：limit=1 时只给一条，并报 truncated。
        narrow = self.provider.daily_life_context(limit=1)
        self.assertEqual(len(narrow["opportunities"]), 1)
        self.assertTrue(narrow["truncated"])
        self.assertEqual(narrow["total_opportunities"],
                         context["total_opportunities"])
        self.assertGreaterEqual(narrow["total_opportunities"], len(items) // 2)

        # 没有任何观测时：诚实说"没有观测"，不编造一天。
        bare = self.provider_for().daily_life_context()
        self.assertEqual(bare["status"], "unavailable")
        self.assertEqual(bare["reason_code"], "NO_OBSERVATION")
        self.assertEqual(bare["opportunities"], [])
        self.assertEqual(bare["unknowns"], ["no_observation"])
        self.assertEqual([entry["code"] for entry in bare["unknown_details"]],
                         ["no_observation"])

    def test_the_life_context_consumer_bounds_it_without_losing_the_facts(self):
        """A 的 consumer（``life_live_context_view``）能直接吃这份 payload。"""

        import life_live_context_view as view

        self.provider()
        bounded = view.read_live_context(self.provider, limit=4)
        self.assertEqual(bounded["source"], view.SOURCE_CONTRACT)
        self.assertEqual(bounded["status"], "available")
        self.assertEqual(bounded["location"]["place_id"], "home")
        self.assertTrue(bounded["opportunities"])
        self.assertLessEqual(len(bounded["opportunities"]), 4)
        for entry in bounded["opportunities"]:
            self.assertTrue(entry["target_ref"])
            self.assertTrue(entry["verb"])
            self.assertNotIn("{", entry["label"])          # 名字，不是被字符串化的 dict
        # unknowns 是短码，不是把 dict 拍平成字符串。
        self.assertTrue(bounded["unknowns"])
        for code in bounded["unknowns"]:
            self.assertNotIn("{", code)
        # 附近的地方经过 consumer 的有界裁剪后仍然带 place_id。
        self.assertTrue(bounded["nearby"])
        self.assertTrue(all(entry["place_id"] for entry in bounded["nearby"]))

    def test_a_pick_up_offer_disappears_when_the_owner_says_it_is_gone(self):

        """owner 侧事实变了，候选就没了 —— 视图不是缓存，更不是承诺。"""

        self.provider()
        self.assertEqual(
            len([entry for entry in self.daily_opportunities(self.provider())
                 if entry["verb"] == "PICK_UP"]), 1)
        with mock.patch.dict(os.environ, GATE):
            self.assertEqual(self.submit(
                [entry for entry in self.daily_opportunities(self.provider())
                 if entry["verb"] == "PICK_UP"][0])["status"], "committed")
        # 已经拿在手上：PICK_UP 不再是她能选的动作，PLACE 才是。
        after = self.daily_opportunities(self.provider())
        self.assertEqual([entry for entry in after if entry["verb"] == "PICK_UP"], [])
        self.assertEqual(len([entry for entry in after if entry["verb"] == "PLACE"]), 1)


if __name__ == "__main__":
    unittest.main()

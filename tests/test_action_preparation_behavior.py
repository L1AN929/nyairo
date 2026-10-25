#!/usr/bin/env python3
"""第二段「动作到准备条件」的本接缝正负控。

被测的只有一处：``world_move_opportunity_source`` 把 **World owner 自己投影的台面**
翻译成 ``dailyact:place:surface:...`` 目标。修好之前，owner 的 ``get_environment``
明明给了 ``{"surface_id", "surface_kind", "location_id"}``，翻译层却按 ``kind`` 找一个
永远不存在的 ``"surface"``，于是拿放只能放到"这间房"，真台面永远进不了候选。

这里不重造编码/owner/resolver：
* 编码、完成事实、fresh snapshot 判据用 ``life_daily_action_targets`` 的既有函数；
* 真实 owner 用例复用 ``test_one_day_life.WorldGateway``（真 socket / 真 lease /
  真 Resolver / 真 substrate），只做本接缝需要的正负控。
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
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import WorldGateway, posix_only  # noqa: E402

import life_daily_action_targets as targets  # noqa: E402
import world_action_bridge_adapter as bridge  # noqa: E402
import world_body_substrate as wbs  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402

FIXTURE_ROOT = Path("/tmp/nyaw04b.preparation")
WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
CUP = "i-cup-01"
FRAME = "i-frame-02"
DESK = "i-desk-01"
DESK_SURFACE = "desk_surface:" + DESK
MOMENT = "2026-10-24T10:00:00+00:00"
GATE = {"WORLD_BODY_INTENT_MUTATION_GATE": "canonical"}


def observation(*, hands=None, revision=7, surface_objects=None):
    """The canonical snapshot shape the provider already reads (no owner call)."""

    visible = []
    for slot, object_id in (hands or {}).items():
        visible.append({"object_id": object_id, "kind": "hand", "slot_id": slot,
                        "in_hand": True})
    for object_id, surface_id in (surface_objects or {}).items():
        visible.append({"object_id": object_id, "kind": "surface",
                        "surface_id": surface_id, "slot_id": None, "in_hand": False})
    return {
        "world_id": "preparation-world",
        "world_revision": revision,
        "location": {"place_id": "home", "area_id": "bedroom"},
        "pose": "standing",
        "orientation": "south",
        "hands": dict(hands or {}),
        "visible_objects": visible,
    }


def available_actions(*, hands=None, pick_objects=(), pose=True):
    hands = dict(hands or {})
    free = [slot for slot in targets.HAND_SLOTS if slot not in hands]
    pick = ([{"object_id": object_id, "slots": list(free)} for object_id in pick_objects]
            if free and pose else [])
    return {"actions": [
        {"verb": "PICK_UP", "available": bool(pick), "candidates": pick,
         "blocked_reason": None if pick else "NO_TAKEABLE_OBJECT"},
        {"verb": "PLACE", "available": bool(hands),
         "blocked_reason": None if hands else "nothing_held"},
    ]}


def owner_surfaces(*surfaces):
    """Exactly the owner projection shape: ``surface_kind``, never placement ``kind``."""

    return [{"surface_id": surface_id, "surface_kind": surface_id.split(":", 1)[0],
             "label": surface_id.split(":", 1)[0].split("_", 1)[0],
             "location_id": "home", "area_id": "bedroom", "items": list(items)}
            for surface_id, items in surfaces]


def daily_targets(items):
    return [str((item.get("target_refs") or [""])[0]) for item in items
            if str((item.get("target_refs") or [""])[0]).startswith("dailyact:")]


class DroppedResponseResolver:
    """The real World resolver, with one answer lost on the way back to AR-0.

    The owner really executes and records the execution; only the client's reading of
    the answer is lost, which is exactly the ambiguous post-submission case AR-0's
    fence and reconciliation exist for.  Every other call (including the World ledger
    lookup used by ``reconcile_action``) goes to the real resolver untouched.
    """

    def __init__(self, inner) -> None:
        self.inner = inner
        self.dropped = False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def resolve_action(self, **kwargs):
        import action_reality_ledger as arl

        result = self.inner.resolve_action(**kwargs)
        if not self.dropped and result.get("status") == "committed":
            self.dropped = True
            return dict(result, status="uncertain", code="RESPONSE_LOST",
                        transport_result=arl.TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
                        detail="the client lost the owner's answer")
        return result


class PreparationTranslationTest(unittest.TestCase):
    """翻译层：owner 的台面事实必须真的改变可放目标，且只改变这一处。"""

    def test_the_owner_surface_projection_becomes_a_real_surface_target(self):
        obs = observation(hands={"left_hand": CUP})
        items = wmos.observed_daily_action_opportunities(
            obs, available_actions=available_actions(hands={"left_hand": CUP}),
            occurred_at=MOMENT, observed_at=MOMENT,
            destinations=owner_surfaces((DESK_SURFACE, [])))

        refs = daily_targets(items)
        places = [refs for refs in refs if refs.startswith("dailyact:place:")]
        self.assertEqual(len(places), 2, places)
        surface = next(ref for ref in places if ":place:surface:" in ref)
        parsed = targets.parse_daily_action_target(surface)
        self.assertEqual(parsed["params"]["target"],
                         {"kind": "surface", "location_id": "home",
                          "surface_id": DESK_SURFACE})
        # 完成事实仍是 B 的既有拼写，不是这里另造的一套。
        self.assertEqual(
            targets.daily_action_completion(surface)["expected_value"],
            "world_fact:placement:%s:surface:%s" % (CUP, DESK_SURFACE))
        # 房间目标仍在：台面是新增路径，不是替换。
        self.assertTrue(any(":place:room:" in ref for ref in places), places)
        # 机会 id 必须过 AG-0 自己的记录合同：编码里的 %3A 不能原样出现在 id 里。
        import candidate_sources_ag0 as ag0
        for item in items:
            ag0.ObservedWorldOpportunity(**item)

    def test_no_surface_is_invented_when_the_owner_exposes_none(self):
        obs = observation(hands={"left_hand": CUP})
        actions = available_actions(hands={"left_hand": CUP})
        for destinations in (None, [], [{"surface_id": DESK_SURFACE,
                                         "location_id": "park"}]):
            refs = daily_targets(wmos.observed_daily_action_opportunities(
                obs, available_actions=actions, occurred_at=MOMENT,
                observed_at=MOMENT, destinations=destinations))
            self.assertFalse([ref for ref in refs if ":place:surface:" in ref], refs)
            self.assertEqual(len([ref for ref in refs if ":place:room:" in ref]), 1, refs)

    def test_malformed_and_duplicate_surfaces_are_dropped_not_repaired(self):
        obs = observation(hands={"left_hand": CUP})
        refs = daily_targets(wmos.observed_daily_action_opportunities(
            obs, available_actions=available_actions(hands={"left_hand": CUP}),
            occurred_at=MOMENT, observed_at=MOMENT,
            destinations=[
                {"surface_kind": "desk_surface", "location_id": "home"},  # no id
                {"surface_id": "", "location_id": "home"},                # empty id
                {"surface_id": DESK_SURFACE, "location_id": "home"},      # real
                {"surface_id": DESK_SURFACE, "location_id": "home"},      # duplicate
                {"surface_id": "bed_surface", "location_id": "home"},     # legacy bare
            ]))
        surfaces = [ref for ref in refs if ":place:surface:" in ref]
        self.assertEqual(len(surfaces), 2, surfaces)
        self.assertTrue(any("desk_surface" in ref for ref in surfaces), surfaces)
        self.assertTrue(any("bed_surface" in ref for ref in surfaces), surfaces)

    def test_the_prepared_state_changes_the_next_targets_not_the_prompt_wording(self):
        # 动作前：杯子在房里，手上空 —— 只有"拿起"。
        picked = observation()
        before = daily_targets(wmos.observed_daily_action_opportunities(
            picked, available_actions=available_actions(pick_objects=(CUP,)),
            occurred_at=MOMENT, observed_at=MOMENT,
            destinations=owner_surfaces((DESK_SURFACE, []))))
        self.assertEqual(len(before), 1, before)
        self.assertTrue(before[0].startswith("dailyact:pick_up:"), before)

        # 动作后：杯子在左手 —— "拿起"消失，出现房内和台面两个真实放法。
        held = observation(hands={"left_hand": CUP})
        after = daily_targets(wmos.observed_daily_action_opportunities(
            held, available_actions=available_actions(hands={"left_hand": CUP}),
            occurred_at=MOMENT, observed_at=MOMENT,
            destinations=owner_surfaces((DESK_SURFACE, []))))
        self.assertFalse([ref for ref in after if ":pick_up:" in ref], after)
        self.assertEqual(len([ref for ref in after if ":place:surface:" in ref]), 1, after)

        # 放到台面后：手上空，owner 的快照说杯子在台面上；"拿起"重新出现。
        placed = observation(surface_objects={CUP: DESK_SURFACE})
        next_round = daily_targets(wmos.observed_daily_action_opportunities(
            placed, available_actions=available_actions(pick_objects=(CUP,)),
            occurred_at=MOMENT, observed_at=MOMENT,
            destinations=owner_surfaces((DESK_SURFACE, [CUP]))))
        self.assertEqual(len(next_round), 1, next_round)
        self.assertTrue(next_round[0].startswith("dailyact:pick_up:"), next_round)
        parsed_place = targets.parse_daily_action_target(
            targets.encode_daily_action_target(
                "PLACE", {"object_id": CUP, "target": {
                    "kind": "surface", "location_id": "home",
                    "surface_id": DESK_SURFACE}}))
        fact = targets.daily_action_observed_fact(parsed_place, placed)
        self.assertTrue(fact["satisfied"], fact)
        self.assertEqual(fact["value"],
                         "world_fact:placement:%s:surface:%s" % (CUP, DESK_SURFACE))

    def test_unreadable_or_absent_is_unknown_never_a_false_placement(self):
        surface_target = targets.encode_daily_action_target(
            "PLACE", {"object_id": CUP, "target": {
                "kind": "surface", "location_id": "home", "surface_id": DESK_SURFACE}})
        parsed = targets.parse_daily_action_target(surface_target)

        still_held = targets.daily_action_observed_fact(
            parsed, observation(hands={"left_hand": CUP}))
        self.assertFalse(still_held["satisfied"], still_held)
        self.assertIn(targets.UNKNOWN_STILL_HELD, still_held["unknowns"])

        gone = targets.daily_action_observed_fact(parsed, observation())
        self.assertFalse(gone["satisfied"], gone)
        self.assertIsNone(gone["value"], gone)
        self.assertIn(targets.UNKNOWN_OBJECT_NOT_VISIBLE, gone["unknowns"])

        unreadable = targets.daily_action_observed_fact(parsed, None)
        self.assertFalse(unreadable["satisfied"], unreadable)
        self.assertIsNone(unreadable["value"], unreadable)
        self.assertIn("snapshot_unavailable", unreadable["unknowns"])

    def test_the_owner_verdict_gates_every_target(self):
        obs = observation()
        self.assertEqual(wmos.observed_daily_action_opportunities(
            obs, available_actions=None, occurred_at=MOMENT, observed_at=MOMENT,
            destinations=owner_surfaces((DESK_SURFACE, []))), [])
        self.assertEqual(daily_targets(wmos.observed_daily_action_opportunities(
            obs, available_actions={"actions": [
                {"verb": "PICK_UP", "available": False, "candidates": []},
                {"verb": "PLACE", "available": False}]},
            occurred_at=MOMENT, observed_at=MOMENT,
            destinations=owner_surfaces((DESK_SURFACE, [])))), [])

    def test_the_same_observation_is_deterministic_and_grants_nothing(self):
        obs = observation(hands={"left_hand": CUP})
        actions = available_actions(hands={"left_hand": CUP})
        first = wmos.observed_daily_action_opportunities(
            obs, available_actions=actions, occurred_at=MOMENT, observed_at=MOMENT,
            destinations=owner_surfaces((DESK_SURFACE, [])))
        second = wmos.observed_daily_action_opportunities(
            obs, available_actions=actions, occurred_at=MOMENT, observed_at=MOMENT,
            destinations=owner_surfaces((DESK_SURFACE, [])))
        self.assertEqual([item["opportunity_id"] for item in first],
                         [item["opportunity_id"] for item in second])
        for item in first:
            self.assertTrue(item["observed"])
            self.assertEqual(item["status"], "AVAILABLE")
            self.assertEqual(item["subject_id"], "chiyo.global")


class ReusablePathStepKeyTest(unittest.TestCase):
    """同一个目标在**新的世界状态**下必须是一次新动作，而不是旧执行的缓存回放。"""

    def _cycle(self, *, revision=1, hands=None, surface=None):
        import autonomous_life_cycle as alc

        target = targets.encode_daily_action_target(
            "PICK_UP", {"object_id": CUP, "slot_id": "left_hand"})
        runtime = SimpleNamespace(
            state_root=Path("/tmp/preparation-step-key"),
            candidate_read=SimpleNamespace(
                get_candidate=lambda ref: {"target_refs": [target]}))
        cycle = alc.AutonomousLifeCycle(runtime, enabled=True)
        visible = []
        if surface is not None:
            visible.append({"object_id": CUP, "kind": "surface",
                            "surface_id": surface, "slot_id": None, "in_hand": False})
        cycle.last_world_observation = {
            "location": {"place_id": "home", "area_id": "bedroom"},
            "world_revision": revision,
            "pose": "standing", "orientation": "south",
            "hands": dict(hands or {}), "visible_objects": visible}
        return cycle

    def test_a_changed_placement_is_a_new_action_not_a_replayed_execution(self):
        decision = SimpleNamespace(selected_candidate_ref="cand:pick")
        on_desk = self._cycle(surface=DESK_SURFACE)._world_step_spec(decision)
        on_desk_again = self._cycle(surface=DESK_SURFACE)._world_step_spec(decision)
        in_hand = self._cycle(hands={"left_hand": CUP})._world_step_spec(decision)

        # 同一状态：同一个执行，重试不会变成第二次动作。
        self.assertEqual(on_desk["idempotency_key"], on_desk_again["idempotency_key"])
        # 世界修订号没变、只有 placement 变了：仍必须是新动作。
        self.assertNotEqual(on_desk["idempotency_key"], in_hand["idempotency_key"])
        self.assertEqual(on_desk["target_ref"], in_hand["target_ref"])
        self.assertEqual(on_desk["payload_spec"]["target"],
                         in_hand["payload_spec"]["target"])

    def test_two_decisions_in_the_identical_state_are_two_actions(self):
        """The identity is the Decision, not the observation it happened to see.

        An ABA cycle can bring every observed placement back to the first one; the
        second Decision must still be a new execution, while re-submitting the *same*
        Decision keeps its key.
        """

        first = SimpleNamespace(selected_candidate_ref="cand:pick", decision_id="dec:aba-1")
        second = SimpleNamespace(selected_candidate_ref="cand:pick", decision_id="dec:aba-2")
        step_first = self._cycle(surface=DESK_SURFACE)._world_step_spec(first)
        step_second = self._cycle(surface=DESK_SURFACE)._world_step_spec(second)
        step_retry = self._cycle(surface=DESK_SURFACE)._world_step_spec(first)

        self.assertNotEqual(step_first["idempotency_key"], step_second["idempotency_key"])
        self.assertEqual(step_first["idempotency_key"], step_retry["idempotency_key"])
        self.assertEqual(step_first["target_ref"], step_second["target_ref"])

    def test_observation_order_and_unrelated_objects_do_not_change_a_decision_key(self):
        """The same Decision keeps its key when nothing about the decision changed.

        Visible-object order, unrelated objects and even the pose are observation
        noise once a Decision is committed; keying on them would either mint a new
        action for an UNKNOWN retry or call an ABA cycle a new action by accident.
        """

        decision = SimpleNamespace(selected_candidate_ref="cand:pick",
                                   decision_id="dec:stable")
        cycle = self._cycle(surface=DESK_SURFACE)
        first = cycle._world_step_spec(decision)
        cycle.last_world_observation["visible_objects"] = [
            {"object_id": CUP, "kind": "surface", "surface_id": DESK_SURFACE,
             "slot_id": None, "in_hand": False},
            {"object_id": FRAME, "kind": "surface", "surface_id": DESK_SURFACE,
             "slot_id": None, "in_hand": False},
            {"object_id": "i-unrelated-03", "kind": "room", "surface_id": None,
             "slot_id": None, "in_hand": False},
        ]
        cycle.last_world_observation["pose"] = "seated"
        second = cycle._world_step_spec(decision)
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])


class SurfaceFieldCompatibilityTest(unittest.TestCase):
    """The ref-safe surface token must round-trip and must not break old targets."""

    SURFACES = (
        "desk_surface:i-desk-01",
        "desk-x3Asurface",
        "a--b",
        "x3A",
        "-x3A",
        "%3A",
        "100%",
        "书桌:台面",
        "é—café",
        "-",
        "--",
        "a/b",
        "/x41",
    )

    @staticmethod
    def _target(surface: str) -> str:
        return targets.encode_daily_action_target(
            "PLACE", {"object_id": CUP, "target": {
                "kind": "surface", "location_id": "home", "surface_id": surface}})

    def test_every_surface_round_trips_and_stays_free_of_percent(self):
        for surface in self.SURFACES:
            target = self._target(surface)
            self.assertNotIn("%", target, (surface, target))
            self.assertRegex(target, r"^dailyact:[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
            parsed = targets.parse_daily_action_target(target)
            self.assertEqual(parsed["params"]["target"]["surface_id"], surface,
                             (surface, target, parsed))

    def test_a_non_ascii_surface_is_a_real_ag0_target(self):
        items = wmos.observed_daily_action_opportunities(
            observation(hands={"left_hand": CUP}),
            available_actions=available_actions(hands={"left_hand": CUP}),
            occurred_at=MOMENT, observed_at=MOMENT,
            destinations=owner_surfaces(("书桌:台面", [])))
        surfaces = [ref for ref in daily_targets(items) if ":place:surface:" in ref]
        self.assertEqual(len(surfaces), 1, surfaces)
        import candidate_sources_ag0 as ag0
        for item in items:
            ag0.ObservedWorldOpportunity(**item)
        self.assertEqual(
            targets.parse_daily_action_target(surfaces[0])["params"]["target"]["surface_id"],
            "书桌:台面")

    def test_the_legacy_percent_spelling_still_parses_unchanged(self):
        legacy = "dailyact:place:surface:home:desk_surface%3Ai-desk-01:i-cup-01"
        parsed = targets.parse_daily_action_target(legacy)
        self.assertEqual(parsed["params"]["target"], {
            "kind": "surface", "location_id": "home",
            "surface_id": "desk_surface:i-desk-01"})
        self.assertEqual(parsed["target_ref"], legacy)
        self.assertEqual(
            targets.parse_daily_action_target(
                "dailyact:place:room:home%3Anorth:bedroom:i-cup-01")["params"]["target"],
            {"kind": "room", "location_id": "home:north", "area_id": "bedroom"})

    def test_a_legacy_literal_dash_is_still_read_as_a_literal_dash(self):
        legacy = "dailyact:place:surface:home:desk_surface-i-desk-01:i-cup-01"
        self.assertEqual(
            targets.parse_daily_action_target(legacy)["params"]["target"]["surface_id"],
            "desk_surface-i-desk-01")
        # `-` 在旧拼写里是普通字面字符，新编码也按字面保留（不再双写），
        # 于是同一个 surface 的旧串与新串必须指同一个对象。
        current = self._target("desk_surface-i-desk-01")
        self.assertEqual(current, legacy)
        self.assertEqual(
            targets.parse_daily_action_target(current)["params"]["target"]["surface_id"],
            "desk_surface-i-desk-01")

    def test_an_old_literal_is_never_reinterpreted_as_an_escape(self):
        """父级反例：旧 surface 字面 ``desk-x3Asurface`` 不能被解码成 ``desk:surface``。

        旧拼写是 ``quote(value, safe="")``，``-``/``-xHH`` 都只是字面；新拼写用
        ``/`` 转义（``/`` 在旧拼写里只会写成 ``%2F``），两种拼写的标记字符不相交。
        """

        legacy = "dailyact:place:surface:home:desk-x3Asurface:i-cup-01"
        parsed = targets.parse_daily_action_target(legacy)
        self.assertEqual(parsed["params"]["target"]["surface_id"], "desk-x3Asurface")
        self.assertNotEqual(parsed["params"]["target"]["surface_id"], "desk:surface")
        self.assertEqual(parsed["target_ref"], legacy)
        # 同一字面在新编码下仍是字面，和新冒号编码是两个不同目标。
        self.assertEqual(self._target("desk-x3Asurface"), legacy)
        self.assertNotEqual(self._target("desk:surface"), legacy)
        self.assertEqual(
            targets.parse_daily_action_target(
                self._target("desk:surface"))["params"]["target"]["surface_id"],
            "desk:surface")

        # 旧拼写里所有 `-`/`-xHH`/非 ASCII 百分号都必须逐字读回。
        for surface in ("a--b", "-x3A", "x3A", "-", "--", "desk_surface-i-desk-01"):
            old = "dailyact:place:surface:home:%s:i-cup-01" % surface
            self.assertEqual(
                targets.parse_daily_action_target(old)["params"]["target"]["surface_id"],
                surface, (surface, old))
        self.assertEqual(
            targets.parse_daily_action_target(
                "dailyact:place:surface:home:%E4%B9%A6%E6%A1%8C:i-cup-01"
            )["params"]["target"]["surface_id"],
            "书桌")


@posix_only
class RealOwnerPreparationTest(unittest.TestCase):
    """真实 owner 的正负控：gate off 不落地，重复观测不动世界，台面真的存在。"""

    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        self.addCleanup(self._restore_env)
        gate = mock.patch.dict(os.environ, GATE)
        gate.start()
        self.addCleanup(gate.stop)
        FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = FIXTURE_ROOT / ("prep-" + uuid.uuid4().hex[:12])
        self.root.mkdir(parents=True, exist_ok=False)
        self.world_home = self.root / "world"

        import world_runtime_data as wrd
        from components.worldgen.instance import init_instance

        package = wrd.active_world({"CHIYO_WORLD_PACKAGE": str(WORLD_PACKAGE)})
        init_instance(WORLD_PACKAGE, self.world_home, world_id=package.world_id)
        self.world_id = package.world_id
        self._declare_capabilities()
        self._declare_owned_desk()
        self._place(CUP, "home", "bedroom")
        self._place(FRAME, "park", "park")
        self._place(DESK, "home", "bedroom")

        env = {"CHIYO_WORLD_PACKAGE": str(WORLD_PACKAGE),
               "CHIYO_WORLD_HOME": str(self.world_home),
               "WORLD_BODY_INTEGRATION_MODE": "canonical"}
        self.gateway = WorldGateway(self.world_home, world_id=self.world_id,
                                    environment=dict(env))
        self.addCleanup(self.gateway.stop)
        self.env = dict(env)
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

    def _declare_capabilities(self) -> None:
        path = self.world_home / "state" / "world_object_capabilities.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {"schema_version": wbs.CAPABILITIES_SCHEMA,
                    "objects": {CUP: {"takeable": "true", "portable": "true",
                                      "placeable": "true"},
                                FRAME: {"takeable": "true", "portable": "true",
                                        "placeable": "true"},
                                DESK: {"takeable": "false", "portable": "false",
                                       "placeable": "false"}}}
        path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True),
                        encoding="utf-8")

    def _declare_owned_desk(self) -> None:
        path = self.world_home / "state" / "world_property_economy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": 1,
            "items": [{"item_instance_id": DESK, "catalog_item_id": "simple_desk",
                       "disposition": "OWNED"}]}, ensure_ascii=False), encoding="utf-8")

    def _place(self, object_id: str, place: str, area: str) -> None:
        store = wbs.SubstrateStore(wbs.substrate_path(self.world_home))
        state = store.load()
        self.assertIsNotNone(state, "init_instance must have written a substrate")
        store.save(wbs.place_object(
            state, object_id, {"kind": "room", "location_id": place, "area_id": area}))

    def provider_for(self) -> wmos.WorldMoveOpportunityProvider:
        return wmos.WorldMoveOpportunityProvider(
            client=self.client, action_intent=self.action_intent,
            bridge_dir=str(BRIDGE_DIR), environment=self.env,
            world_home=str(self.world_home))

    # -- real Decision -> step, and the owner's own submit path --------------

    def snapshot(self) -> dict:
        return dict(self.client.get_snapshot().data)

    def decision_step(self, snapshot: dict, *, target_ref: str, decision_id: str):
        import autonomous_life_cycle as alc

        decision = SimpleNamespace(selected_candidate_ref="cand:" + decision_id,
                                   decision_id=decision_id)
        runtime = SimpleNamespace(
            state_root=self.root / "life",
            candidate_read=SimpleNamespace(
                get_candidate=lambda ref: {"target_refs": [target_ref]}))
        cycle = alc.AutonomousLifeCycle(runtime, enabled=True)
        cycle.last_world_observation = snapshot
        return decision, cycle._world_step_spec(decision)

    def submit_step(self, step: dict, *, resolver=None) -> dict:
        parsed = targets.parse_daily_action_target(step["target_ref"])
        return (resolver or self.resolver).resolve_action(
            idempotency_key=step["idempotency_key"], verb=parsed["verb"],
            target=step["target_ref"], expected_dependencies={})

    def ar0_commands(self, resolver=None):
        """A real AR-0 ledger over this fixture, with the real World adapter."""

        import activity_continuity as ac
        import action_reality_ledger as arl

        lease = ac.ActivityAuthorityLease(self.root / "ar0")
        lease.acquire()
        self.addCleanup(lambda: lease.release() if lease.held else None)
        store = arl.ActionStore(self.root / "ar0", namespace=ac.NAMESPACE_ISOLATED_TEST)
        capability = arl.ActionRealityAuthority.issue_writer_capability(
            lease, namespace=ac.NAMESPACE_ISOLATED_TEST)
        commands = arl.ActionCommandService(
            store=store, lease=lease, capability=capability)
        adapter = bridge.RealWorldActionAdapter(resolver or self.resolver)
        commands.register_adapter(arl.ACTION_KIND_WORLD, adapter)
        executor = arl.ActionRealityAuthority.issue_sandbox_executor_capability(
            namespace=ac.NAMESPACE_ISOLATED_TEST)
        return arl, commands, adapter, executor

    def propose_prepare_submit(self, commands, executor, step: dict, *,
                               decision_id: str, arl) -> tuple[dict, str]:
        proposal = commands.propose_action(
            action_kind=arl.ACTION_KIND_WORLD,
            target_ref=step["target_ref"],
            payload_ref="payload:action-preparation",
            payload_data=step["payload_spec"],
            idempotency_key=step["idempotency_key"],
            source_refs=["test:action-preparation-behavior"],
            decision_ref=decision_id)
        action_id = proposal["action"]["action_id"]
        commands.prepare_action(action_id=action_id)
        outcome = commands.submit_action(
            action_id=action_id, executor_capability=executor)
        return outcome, action_id

    def substrate(self) -> dict:
        return wbs.SubstrateStore(wbs.substrate_path(self.world_home)).load()

    def test_the_owner_really_exposes_the_desk_and_the_placement_survives_a_restart(self):
        items = self.provider()
        refs = daily_targets(items)
        surface = next(ref for ref in refs if ":place:surface:" in ref) \
            if any(":place:surface:" in ref for ref in refs) else None
        # 她一开始空手：台面还不会作为“放”出现，但 owner 的台面投影已经真实存在。
        self.assertIsNone(surface, refs)
        surfaces = self.client.call("get_environment").data.get("surfaces") or []
        self.assertIn(DESK_SURFACE, [item.get("surface_id") for item in surfaces], surfaces)

        parsed = targets.parse_daily_action_target(
            next(ref for ref in refs if ":pick_up:" in ref))
        step = targets.build_daily_action_step(parsed["target_ref"],
                                              origin_ref="place:home@r1")
        picked = self.resolver.resolve_action(
            idempotency_key=step["idempotency_key"], verb=parsed["verb"],
            target=parsed["target_ref"], expected_dependencies={})
        self.assertEqual(picked["status"], "committed", picked)

        refs = daily_targets(self.provider())
        surface = next(ref for ref in refs if ":place:surface:" in ref)
        surface_parsed = targets.parse_daily_action_target(surface)
        self.assertEqual(surface_parsed["params"]["target"]["surface_id"], DESK_SURFACE)
        step = targets.build_daily_action_step(surface, origin_ref="place:home@r2")
        placed = self.resolver.resolve_action(
            idempotency_key=step["idempotency_key"], verb=surface_parsed["verb"],
            target=surface, expected_dependencies={})
        self.assertEqual(placed["status"], "committed", placed)
        self.assertEqual(wbs.placement_of(self.substrate(), CUP)["surface_id"],
                         DESK_SURFACE)

        # 同根重启 owner：位置是 owner 的，不是进程内存的。
        self.gateway.stop()
        self.gateway = WorldGateway(self.world_home, world_id=self.world_id,
                                    environment=dict({
                                        "CHIYO_WORLD_PACKAGE": str(WORLD_PACKAGE),
                                        "CHIYO_WORLD_HOME": str(self.world_home),
                                        "WORLD_BODY_INTEGRATION_MODE": "canonical"}))
        self.addCleanup(self.gateway.stop)
        self.env = {"CHIYO_WORLD_PACKAGE": str(WORLD_PACKAGE),
                    "CHIYO_WORLD_HOME": str(self.world_home),
                    "WORLD_BODY_INTEGRATION_MODE": "canonical"}
        self.env.update(self.gateway.environment())
        import world_body_client as wbc
        self.client = wbc.WorldBodyClient(environment=self.env)
        self.provider = self.provider_for()

        items = self.provider()
        refs = daily_targets(items)
        self.assertEqual(len([ref for ref in refs if ":pick_up:" in ref]), 1, refs)
        surfaces = self.client.call("get_environment").data.get("surfaces") or []
        desk = next(item for item in surfaces if item["surface_id"] == DESK_SURFACE)
        self.assertIn(CUP, desk["items"], desk)
        self.assertEqual(wbs.placement_of(self.substrate(), CUP)["surface_id"],
                         DESK_SURFACE)

    def test_gate_off_refuses_without_touching_the_owner_or_replaying(self):
        refs = daily_targets(self.provider())
        parsed = targets.parse_daily_action_target(
            next(ref for ref in refs if ":pick_up:" in ref))
        step = targets.build_daily_action_step(parsed["target_ref"],
                                              origin_ref="place:home@gate-off")
        before = self.substrate()
        with mock.patch.dict(os.environ, {"WORLD_BODY_INTENT_MUTATION_GATE": "off"}):
            refused = self.resolver.resolve_action(
                idempotency_key=step["idempotency_key"], verb=parsed["verb"],
                target=parsed["target_ref"], expected_dependencies={})
            again = self.resolver.resolve_action(
                idempotency_key=step["idempotency_key"], verb=parsed["verb"],
                target=parsed["target_ref"], expected_dependencies={})
        self.assertEqual(refused["status"], "rejected", refused)
        self.assertEqual(refused["code"], "GATE_OFF", refused)
        self.assertEqual(refused["transport_result"],
                         "DEFINITE_PRE_SUBMISSION_FAILURE", refused)
        self.assertEqual(again["execution_id"], refused["execution_id"])
        after = self.substrate()
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(wbs.placement_of(after, CUP), wbs.placement_of(before, CUP))

    def test_observing_the_same_state_twice_does_not_move_anything(self):
        before = self.substrate()["revision"]
        first = daily_targets(self.provider())
        second = daily_targets(self.provider())
        self.assertEqual(first, second)
        self.assertEqual(self.substrate()["revision"], before)

    def test_aba_cycles_reach_the_same_owner_state_and_every_decision_really_acts(self):
        """pick -> place -> pick -> place -> pick, against the real owner.

        The first place and the second place leave byte-identical observed facts
        (``world_revision`` stays 1 and PICK_UP/PLACE do not bump it).  Every Decision
        must nevertheless get its own execution and really move the cup.
        """

        import autonomous_life_cycle as alc

        arl, commands, _adapter, executor = self.ar0_commands()
        pick = targets.encode_daily_action_target(
            "PICK_UP", {"object_id": CUP, "slot_id": "left_hand"})
        place = targets.encode_daily_action_target(
            "PLACE", {"object_id": CUP, "target": {
                "kind": "surface", "location_id": "home", "surface_id": DESK_SURFACE}})

        steps = []
        results = []
        action_ids = []
        revisions = []
        snapshots = []
        snapshot = self.snapshot()
        for index in range(5):
            target = pick if index % 2 == 0 else place
            decision_id = "dec:aba%012d" % index
            _decision, step = self.decision_step(
                snapshot, target_ref=target, decision_id=decision_id)
            outcome, action_id = self.propose_prepare_submit(
                commands, executor, step, decision_id=decision_id, arl=arl)
            results.append(outcome["action"])
            action_ids.append(action_id)
            steps.append(step)
            revisions.append(int(self.substrate()["revision"]))
            snapshot = self.snapshot()
            snapshots.append(snapshot)

        self.assertEqual([row["status"] for row in results],
                         [arl.ACTION_STATUS_SUCCEEDED] * 5, results)
        self.assertEqual(len(set(action_ids)), 5, action_ids)
        self.assertEqual(revisions, sorted(set(revisions)), revisions)
        # 两次 "放回" 后 owner 的状态与观测完全一样（包括 world_revision）。
        self.assertEqual(snapshots[1], snapshots[3])
        self.assertEqual(
            alc.AutonomousLifeCycle._daily_action_state_token(snapshots[1]),
            alc.AutonomousLifeCycle._daily_action_state_token(snapshots[3]))
        self.assertEqual(wbs.placement_of(self.substrate(), CUP)["kind"], "hand")
        # 第一/第二/第三次拿起是三个不同 Decision、三把不同 key。
        self.assertEqual(len({step["idempotency_key"] for step in steps[0::2]}), 3, steps)
        self.assertEqual(len({step["idempotency_key"] for step in steps[1::2]}), 2, steps)
        # 同一个 Decision/step 再提交仍是同一个 AR-0 执行，且不再动世界。
        before_retry = self.substrate()["revision"]
        retry = commands.submit_action(
            action_id=action_ids[4], executor_capability=executor)
        self.assertTrue(retry["idempotent_replay"], retry)
        self.assertEqual(retry["action"]["action_id"], action_ids[4])
        self.assertEqual(self.substrate()["revision"], before_retry)

    def test_an_unknown_execution_is_reconciled_by_its_original_identity(self):
        """The owner executed; the client lost the answer; the world then changes.

        The same committed Decision must still rebuild the *same* key, must not be
        submitted twice, and must be resolved through AR-0's own reconciliation of the
        original execution -- never by minting a new action from changed observations.
        """

        # 夹具布置：让另一件可拿物件也在同一地点，稍后真的改变观测。
        self._place(FRAME, "home", "bedroom")
        lost = DroppedResponseResolver(self.resolver)
        arl, commands, adapter, executor = self.ar0_commands(resolver=lost)

        pick = targets.encode_daily_action_target(
            "PICK_UP", {"object_id": CUP, "slot_id": "left_hand"})
        decision_id = "dec:unknown0000000001"
        decision, step = self.decision_step(
            self.snapshot(), target_ref=pick, decision_id=decision_id)
        outcome, action_id = self.propose_prepare_submit(
            commands, executor, step, decision_id=decision_id, arl=arl)
        self.assertEqual(outcome["action"]["status"], arl.ACTION_STATUS_UNKNOWN, outcome)
        self.assertEqual(adapter.submit_call_count, 1)
        self.assertEqual(wbs.placement_of(self.substrate(), CUP)["kind"], "hand")

        # 别的物件真的移动：右手拿起相框；观测（hands/visible）与列表都变了。
        frame_pick = targets.encode_daily_action_target(
            "PICK_UP", {"object_id": FRAME, "slot_id": "right_hand"})
        moved = self.submit_step(
            {"target_ref": frame_pick, "idempotency_key": "idem:test:move-frame"},
            resolver=self.resolver)
        self.assertEqual(moved["status"], "committed", moved)
        changed = self.snapshot()
        _decision, retry_step = self.decision_step(
            changed, target_ref=pick, decision_id=decision_id)
        self.assertEqual(retry_step["idempotency_key"], step["idempotency_key"])
        _other, other_step = self.decision_step(
            changed, target_ref=pick, decision_id="dec:other")
        self.assertNotEqual(other_step["idempotency_key"],
                            retry_step["idempotency_key"])

        # 重新 propose 同一把 key 命中原 Action；裸重试被 AR-0 明确拒绝。
        replayed = commands.propose_action(
            action_kind=arl.ACTION_KIND_WORLD, target_ref=step["target_ref"],
            payload_ref="payload:action-preparation",
            payload_data=step["payload_spec"],
            idempotency_key=step["idempotency_key"],
            source_refs=["test:action-preparation-behavior"],
            decision_ref=decision_id)
        self.assertTrue(replayed["idempotent_replay"], replayed)
        self.assertEqual(replayed["action"]["action_id"], action_id)
        with self.assertRaises(arl.UnknownActionRetryForbiddenError):
            commands.submit_action(action_id=action_id, executor_capability=executor)
        self.assertEqual(adapter.submit_call_count, 1)

        # 原 execution 对账：真实 World ledger 确认 SUCCESS，且不再执行一次。
        before_reconcile = self.substrate()["revision"]
        resolved = commands.reconcile_action(action_id=action_id)
        self.assertEqual(resolved["action"]["status"], "SUCCEEDED", resolved)
        self.assertEqual(resolved["reconciliation"]["result"],
                         arl.RECON_RESULT_CONFIRMED_SUCCEEDED, resolved)
        self.assertEqual(self.substrate()["revision"], before_reconcile)
        self.assertEqual(adapter.submit_call_count, 1)

        # 未决执行已解决后，新的 Decision 可以真的做下一件事。
        place = targets.encode_daily_action_target(
            "PLACE", {"object_id": CUP, "target": {
                "kind": "surface", "location_id": "home", "surface_id": DESK_SURFACE}})
        new_decision_id = "dec:afterunknown00001"
        _decision, new_step = self.decision_step(
            self.snapshot(), target_ref=place, decision_id=new_decision_id)
        self.assertNotEqual(new_step["idempotency_key"], step["idempotency_key"])
        new_outcome, _new_action = self.propose_prepare_submit(
            commands, executor, new_step, decision_id=new_decision_id, arl=arl)
        self.assertEqual(new_outcome["action"]["status"], "SUCCEEDED", new_outcome)
        self.assertEqual(wbs.placement_of(self.substrate(), CUP)["surface_id"],
                         DESK_SURFACE)


if __name__ == "__main__":
    unittest.main()

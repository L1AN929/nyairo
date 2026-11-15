#!/usr/bin/env python3
"""0.7 第一条的少量聚合用例：日常 gate、阅读恢复/完成、家具与街区候选、店内购买。

只用真实模块与纯函数；不启动真实 owner、不发模型请求。整链的端到端证据在
``scripts/v07_daily_life_demo.py``，这里证明四处容易被误改的边界：

* 日常入口默认 canonical，显式 off 真的传到子进程装配参数；
* EXHAUSTED 不再被当成“等材料新版本”，阅读默认短间隔是既有 TIME_AT_OR_AFTER；
* 家具/街区候选来自世界包自己的定义，闭店时零候选；
* 店内购买只在营业且在店时出现，完成只认经济 owner 的交易，同 key 不二扣。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import autonomous_life_cycle as alc  # noqa: E402
import life_wait_basis as lwb  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402

import importlib.util  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _pin_bridge_action_intent():
    """Register the bridge's ``action_intent`` as ``sys.modules['action_intent']``.

    The gate tests import ``components.phone.life``, which can leave
    ``components/world/service`` ahead of the ``bridge`` directory on ``sys.path``; a later
    plain ``import action_intent`` then binds the older service copy whose allowlist has no
    ARRANGE/TOGGLE_FIXTURE.  The real runtime only ever imports the bridge copy, so pin it by
    path here — test order must not decide which verbs the owner will accept.
    """

    bridge = ROOT / "components" / "world" / "service" / "bridge" / "action_intent.py"
    current = sys.modules.get("action_intent")
    if current is not None and str(getattr(current, "__file__", "")) == str(bridge):
        return current
    return _load("action_intent", bridge)


nyairo_day = _load("nyairo_day_under_test", ROOT / "scripts" / "nyairo_day.py")
live_day = __import__("components.phone.live_day", fromlist=["live_day"])


class DailyWorldGateTest(unittest.TestCase):
    def test_default_is_canonical_and_explicit_off_wins(self):
        args = argparse.Namespace(world_gate=None, world_actions=None)
        self.assertEqual(nyairo_day._resolve_world_gate(args, None), "canonical")
        self.assertEqual(nyairo_day._resolve_world_gate(args, "off"), "off")
        self.assertEqual(
            nyairo_day._resolve_world_gate(
                argparse.Namespace(world_gate="off", world_actions=True), "canonical"),
            "off")
        self.assertEqual(
            nyairo_day._resolve_world_gate(
                argparse.Namespace(world_gate=None, world_actions=False), "canonical"),
            "off")

    def test_managed_marker_new_default_and_legacy_choice(self):
        self.assertEqual(nyairo_day._managed_world_gate({"world_gate": "canonical"}),
                         "canonical")
        self.assertEqual(nyairo_day._managed_world_gate({}), "canonical")
        # 旧记录只有 world_actions：保留它当时的选择，不在读取时迁移。
        self.assertEqual(nyairo_day._managed_world_gate({"world_actions": False}), "off")
        self.assertEqual(nyairo_day._managed_world_gate({"world_actions": True}), "canonical")

    def test_gate_reaches_the_real_child_assembly(self):
        captured: dict = {}

        def fake_serve(root, **kwargs):
            captured.update(kwargs)
            kwargs["on_ready"]({"port": 1})

        stop = threading.Event()
        stop.set()
        with mock.patch("components.phone.demo.serve_instance", side_effect=fake_serve):
            handle = live_day._default_service_runner(
                ROOT, stop_event=stop, ready_timeout=0.1, world_intent_gate="off")
        self.assertFalse(handle.thread.is_alive())
        self.assertEqual(captured.get("world_intent_gate"), "off")

    def test_daily_serve_entry_passes_canonical_and_explicit_off(self):
        import tempfile

        root = Path(tempfile.mkdtemp(prefix="v07-gate-", dir="/tmp"))
        captured: list = []

        def fake_serve(root_path, **kwargs):
            captured.append(dict(kwargs))

        with mock.patch("components.phone.demo.serve_instance", side_effect=fake_serve):
            self.assertEqual(nyairo_day._serve_forever(root, 1, world_gate="canonical"), 0)
            self.assertEqual(nyairo_day._serve_forever(root, 1, world_gate="off"), 0)
        self.assertEqual([row.get("world_intent_gate") for row in captured],
                         ["canonical", "off"])
        argv: list = []

        class FakePopen:
            pid = 1

            def __init__(self, command, **kwargs):
                argv.append(list(command))

            def poll(self):
                return 0

        with mock.patch("subprocess.Popen", FakePopen):
            nyairo_day._spawn_background(root, 1, world_gate="canonical")
            nyairo_day._spawn_background(root, 1, world_gate="off")
        self.assertIn("--world-gate", argv[0])
        self.assertEqual(argv[0][argv[0].index("--world-gate") + 1], "canonical")
        self.assertEqual(argv[1][argv[1].index("--world-gate") + 1], "off")

    def test_segment_merge_defaults_to_canonical_and_explicit_off_wins(self):
        # The exact function ``run_segment`` uses (not a hand-rebuilt dict).
        default = live_day._segment_feature_overrides()
        self.assertEqual(default["WORLD_BODY_INTENT_MUTATION_GATE"], "canonical")
        explicit_off = live_day._segment_feature_overrides(
            feature_overrides={"WORLD_BODY_INTENT_MUTATION_GATE": "off"})
        self.assertEqual(explicit_off["WORLD_BODY_INTENT_MUTATION_GATE"], "off")
        room = live_day._segment_feature_overrides(room_living=True)
        self.assertEqual(room["WORLD_BODY_INTENT_MUTATION_GATE"], "canonical")
        self.assertTrue(room.get("CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED"))

    def test_real_resident_binding_env_gets_the_gate(self):
        from components.phone import life as phone_life

        phone = {"root": "/tmp/v07-gate-root",
                 "features": {"WORLD_BODY_INTENT_MUTATION_GATE": "off"}}
        canonical = phone_life.map_life_environment(
            phone, None, base_env={},
            feature_overrides={"WORLD_BODY_INTENT_MUTATION_GATE": "canonical"})
        self.assertEqual(canonical["WORLD_BODY_INTENT_MUTATION_GATE"], "canonical")
        off = phone_life.map_life_environment(
            phone, None, base_env={},
            feature_overrides={"WORLD_BODY_INTENT_MUTATION_GATE": "off"})
        self.assertEqual(off["WORLD_BODY_INTENT_MUTATION_GATE"], "off")
        # An instance that recorded the opposite switch is overridden, not merged.
        phone_on = dict(phone, features={"WORLD_BODY_INTENT_MUTATION_GATE": "canonical"})
        explicit_off = phone_life.map_life_environment(
            phone_on, None, base_env={},
            feature_overrides={"WORLD_BODY_INTENT_MUTATION_GATE": "off"})
        self.assertEqual(explicit_off["WORLD_BODY_INTENT_MUTATION_GATE"], "off")

    def test_world_child_environment_receives_the_gate(self):
        from components.phone import demo

        import tempfile

        root = Path(tempfile.mkdtemp(prefix="v07-child-", dir="/tmp"))
        instance = {"world_home": str(root / "world-home"),
                    "world_package": str(root / "pkg"), "world_id": "w1",
                    "subject": "chiyo",
                    "features": {"WORLD_BODY_INTENT_MUTATION_GATE": "off"}}
        # The gate is what the real World child process is started with; only that one key is
        # asserted, and ``_child_environment`` is narrowed so the test never copies the
        # operator's whole environment.
        with mock.patch.object(demo, "_child_environment",
                               side_effect=lambda extra: dict(extra)):
            canonical = demo._world_child(root, instance, world_intent_gate="canonical")
            off = demo._world_child(root, instance, world_intent_gate="off")
        self.assertEqual(canonical.environment["WORLD_BODY_INTENT_MUTATION_GATE"], "canonical")
        self.assertEqual(off.environment["WORLD_BODY_INTENT_MUTATION_GATE"], "off")
        self.assertIn("owner-world", canonical.command)


class ReadingWaitAndCompletionTest(unittest.TestCase):
    def _readiness(self, reason: str):
        return SimpleNamespace(available=False, reason_code=reason, target_ref="artifact:r1",
                               source_revision="artifact-version:v1", activity_ref="actv:1",
                               bound=True)

    def test_exhausted_is_no_longer_a_wait_for_a_new_version(self):
        self.assertEqual(lwb.READING_WAIT_STATES, ())
        self.assertIsNone(lwb.basis_from_readiness(
            self._readiness("READING_EXHAUSTED"), state="EXHAUSTED"))
        # The work dependency is untouched.
        basis = lwb.basis_from_readiness(self._readiness("WORK_NO_CHANGE_CONFIRMED"))
        self.assertIsNotNone(basis)
        self.assertEqual(basis["source"], lwb.SOURCE_WORK_HEAD)

    def test_reading_pause_default_is_the_existing_time_condition(self):
        spec = lwb.reading_pause_default_spec(
            activity_ref="actv:read", observed_at="2026-11-09T10:00:00+09:00")
        self.assertIsNotNone(spec)
        self.assertEqual(spec["condition_kind"], "TIME_AT_OR_AFTER")
        self.assertEqual(spec["source"], lwb.READING_PAUSE_SOURCE)
        self.assertEqual(spec["parameters"]["seconds"], 1800)
        self.assertTrue(spec["basis_ref"].startswith("pausebasis:"))
        target = datetime.fromisoformat(spec["parameters"]["target_time"])
        self.assertEqual(target.hour, 10)
        self.assertEqual(target.minute, 30)

    def test_reading_adoption_contract_completes_on_the_last_page(self):
        fake = SimpleNamespace(
            reading_artifact_id="artifact:read1",
            private_items_artifact_id="artifact:doc1",
            private_material_situation=lambda: {
                "material": {"pages": 3, "version_id": "artifact-version:v1"}},
        )
        spec = alc.AutonomousLifeCycle._reading_adoption_spec(fake)
        self.assertIn("artifact:read1", spec["target_refs"])
        completion = spec["completion_contract_spec"]
        self.assertIsNotNone(completion)
        self.assertEqual(completion["criteria_kind"], "PAGE_POSITION_REACHED")
        self.assertEqual(completion["criteria"]["target_page"], 3)
        self.assertEqual(completion["criteria"]["book_ref"], "artifact:read1")
        # 0.7 B: the contract is bound to the version it was adopted on.
        self.assertEqual(completion["criteria"]["source_version_ref"],
                         "artifact-version:v1")

    def test_page_criterion_does_not_settle_an_old_contract_on_a_new_version(self):
        import activity_progress_completion_lr5 as lr5

        criteria = {"target_page": 3, "book_ref": "artifact:read1",
                    "source_version_ref": "artifact-version:v1"}
        new_version_page3 = {"target_ref": "artifact:read1",
                             "observed_facts": {"page": 3,
                                                "source_version_ref": "artifact-version:v2"}}
        old_version_page3 = {"target_ref": "artifact:read1",
                             "observed_facts": {"page": 3,
                                                "source_version_ref": "artifact-version:v1"}}
        evaluate = lr5.NaturalCompletionEvaluator._eval_criterion_node
        ok, _evs, reason = evaluate(
            criteria_kind=lr5.CRITERIA_PAGE_POSITION_REACHED, criteria=criteria,
            target_refs=["artifact:read1"], verified_evs=[new_version_page3])
        self.assertFalse(ok, reason)
        ok, _evs, _reason = evaluate(
            criteria_kind=lr5.CRITERIA_PAGE_POSITION_REACHED, criteria=criteria,
            target_refs=["artifact:read1"],
            verified_evs=[new_version_page3, old_version_page3])
        self.assertTrue(ok)
        # A contract without the fact keeps the old behaviour.
        ok, _evs, _reason = evaluate(
            criteria_kind=lr5.CRITERIA_PAGE_POSITION_REACHED,
            criteria={"target_page": 3, "book_ref": "artifact:read1"},
            target_refs=["artifact:read1"], verified_evs=[new_version_page3])
        self.assertTrue(ok)

    def test_page_read_evidence_carries_the_page(self):
        seen: dict = {}

        class Port:
            def ingest_progress_evidence(self, evidence):
                seen["evidence"] = evidence
                return {"progress_state": {}, "checkpoint_update": {}}

        fake = SimpleNamespace(
            own_write_recorder=None,
            runtime=SimpleNamespace(ag2_progress_port=Port()),
            _now_iso=lambda: "2026-11-09T10:00:00+00:00",
        )
        base: dict = {}
        alc.AutonomousLifeCycle._record_artifact_progress(
            fake, base, activity_ref="actv:read", artifact_id="artifact:read1",
            version_id="artifact-version:v9", version_number=9,
            correlation_id="corr:1", action_ref="act:1", page=3,
            source_revision="artifact-version:v1")
        facts = seen["evidence"].observed_facts
        self.assertEqual(facts["page"], 3)
        self.assertEqual(facts["source_version_ref"], "artifact-version:v1")
        self.assertEqual(seen["evidence"].target_ref, "artifact:read1")


class HomeAndDistrictCandidatesTest(unittest.TestCase):
    def test_generated_home_offers_the_new_furniture_small_things(self):
        from components.worldgen.tests._setup import make_valid_docs

        docs = make_valid_docs(seed=929711, theme="coastal_town")
        home = docs["rules.json"]["location_rules"]["home"]["affordances"]
        ids = [item["affordance_id"] for item in home]
        for wanted in ("DESK_PAUSE", "WINDOW_PAUSE", "TEA_PAUSE"):
            self.assertIn(wanted, ids)
        stays = dict(wmos._stay_descriptors(
            home, observed_at="2026-11-09T11:00:00+09:00", location_id="home"))
        self.assertEqual(stays.get("DESK_PAUSE"), 300)
        self.assertEqual(stays.get("WINDOW_PAUSE"), 240)
        self.assertEqual(stays.get("TEA_PAUSE"), 300)

    def test_shop_stays_exist_and_closed_shop_offers_nothing(self):
        observation = {"location": {"place_id": "cafe"}, "world_revision": 1,
                       "world_id": "w1"}
        open_catalog = {
            "status": "available",
            "opportunities": [{
                "location_id": "cafe",
                "availability": {"status": "OPEN"},
                "affordances": [{"affordance_id": "SIT", "title": "在咖啡馆坐一会儿",
                                 "suggested_seconds": 300}],
            }],
        }
        stays = wmos.observed_stay_opportunities(
            observation, catalog=open_catalog, occurred_at="2026-11-09T11:00:00+09:00",
            observed_at="2026-11-09T11:00:00+09:00")
        self.assertEqual([item["target_refs"][0] for item in stays],
                         ["stay:cafe:SIT:300"])
        closed_catalog = {
            "status": "available",
            "opportunities": [{
                "location_id": "cafe",
                "availability": {"status": "CLOSED"},
                "affordances": [],
            }],
        }
        self.assertEqual(wmos.observed_stay_opportunities(
            observation, catalog=closed_catalog,
            occurred_at="2026-11-09T23:00:00+09:00",
            observed_at="2026-11-09T23:00:00+09:00"), [])

    def test_move_opportunities_are_bounded_without_ranking(self):
        observation = {"location": {"place_id": "street_1"}, "world_revision": 1,
                       "world_id": "w1"}
        destinations = ["p%02d" % i for i in range(12)]
        action_space = {"candidates": [
            {"verb": "MOVE", "params": {"destination_location_id": name}}
            for name in destinations]}
        rows = wmos.observed_move_opportunities(
            observation, action_space=action_space,
            occurred_at="2026-11-09T11:00:00+09:00",
            observed_at="2026-11-09T11:00:00+09:00")
        self.assertEqual(len(rows), wmos.MAX_MOVE_OPPORTUNITIES)
        # The owner's own order is kept, not re-ranked.
        self.assertEqual([row["target_refs"][0] for row in rows],
                         ["place:%s" % name for name in
                          destinations[:wmos.MAX_MOVE_OPPORTUNITIES]])

    def test_move_bound_reserves_adjacent_streets_inside_the_cap(self):
        observation = {"location": {"place_id": "street_1"}, "world_revision": 1,
                       "world_id": "w1"}
        venues = ["venue%02d" % index for index in range(10)]
        streets = ["street_2", "street_3"]
        action_space = {"candidates": [
            {"verb": "MOVE", "params": {"destination_location_id": name}}
            for name in venues + streets]}
        kinds = {name: "street" for name in streets}
        kinds.update({name: "store" for name in venues})
        rows = wmos.observed_move_opportunities(
            observation, action_space=action_space,
            occurred_at="2026-11-09T11:00:00+09:00",
            observed_at="2026-11-09T11:00:00+09:00", place_kinds=kinds)
        targets = [row["target_refs"][0] for row in rows]
        self.assertEqual(len(targets), wmos.MAX_MOVE_OPPORTUNITIES)
        for street in streets:
            self.assertIn("place:%s" % street, targets)
        remaining = wmos.MAX_MOVE_OPPORTUNITIES - len(streets)
        self.assertEqual(targets[:remaining],
                         ["place:%s" % name for name in venues[:remaining]])

    def test_default_generated_district_has_the_four_places(self):
        from components.worldgen.generator import generate_world

        docs = generate_world(seed=929711, theme="coastal_town")
        locations = [item["location_id"] for item in docs["topology.json"]["locations"]]
        for wanted in ("bookstore", "cafe", "flower_shop", "beach"):
            self.assertIn(wanted, locations)
        # Moving bookstore into fixed_slots must not lose its own reading text.
        self.assertTrue(docs["content.json"]["entries"]["bookstore"].get("reading"))

    def test_default_district_places_are_open_in_day_and_offer_stays(self):
        from components.worldgen.generator import generate_world

        rules = generate_world(seed=929711, theme="coastal_town")[
            "rules.json"]["location_rules"]
        for name in ("bookstore", "cafe", "flower_shop", "beach"):
            rule = rules[name]
            self.assertLessEqual(rule["open_hour"], 11, name)
            self.assertGreater(rule["close_hour"], 11, name)
            descriptors = dict(wmos._stay_descriptors(
                rule["affordances"], observed_at="2026-11-09T11:00:00+09:00",
                location_id=name))
            self.assertTrue(descriptors, name)

    def test_generated_home_has_a_takeable_notebook(self):
        import tempfile

        from components.worldgen import schema
        from components.worldgen.interior import apply_interiors_to_package
        from components.worldgen.tests._setup import make_valid_docs

        package = Path(tempfile.mkdtemp(prefix="v07-home-", dir="/tmp"))
        schema.write_package(package, make_valid_docs(seed=929711, theme="coastal_town"))
        apply_interiors_to_package(package)
        objects = json.loads((package / "objects.json").read_text(
            encoding="utf-8"))["objects"]
        scene = json.loads((package / "scene.json").read_text(encoding="utf-8"))
        home_books = [row for row in objects
                      if row.get("location_id") == "home" and row.get("kind") == "book"]
        self.assertTrue(home_books)
        placement = dict((scene.get("placements") or {}).get(
            home_books[0]["object_id"]) or {})
        self.assertIs(placement.get("portable"), True)
        self.assertIn("pick_up", placement.get("actions") or [])


class StorePurchaseTest(unittest.TestCase):
    """0.7: 店里买东西只走既有经济 owner，Life 只翻译候选与转交 Decision。"""

    @staticmethod
    def _projection(**overrides):
        projection = {
            "kind": "shiomi_home_goods_store_projection",
            "status": "available",
            "store_id": "home_goods_store",
            "at_store": True,
            "purchase_available": True,
            "balance": 10_000,
            "currency": "JPY",
            "delivery": {"mode": "instant_home_delivery", "duration_seconds": 0},
            "catalog": [{
                "catalog_item_id": "bedside_lamp",
                "name": "床头灯",
                "description": "一盏放在床边的小灯",
                "price": 900,
                "currency": "JPY",
                "affordable": True,
            }],
        }
        projection.update(overrides)
        return projection

    @staticmethod
    def _observation(place="home_goods_store"):
        return {"location": {"place_id": place}, "world_revision": 4,
                "world_id": "w1"}

    def test_purchase_candidate_needs_an_open_store_and_being_there(self):
        at_store = self._observation()
        occurred = "2026-11-09T02:00:00+00:00"
        rows = wmos.observed_purchase_opportunities(
            at_store, projection=self._projection(), occurred_at=occurred,
            observed_at=occurred)
        self.assertEqual([row["target_refs"][0] for row in rows], ["buy:bedside_lamp"])
        view = wmos.opportunity_view(
            rows, {}, None,
            purchase_facts={"bedside_lamp": {"name": "床头灯", "price": 900,
                                             "currency": "JPY", "balance": 10_000}})
        self.assertEqual(view[0]["kind"], wmos.PURCHASE_VERB)
        self.assertEqual(view[0]["purchase"]["name"], "床头灯")
        self.assertEqual(view[0]["purchase"]["price"], 900)
        # 闭店 / 不在店里 / 买不起：都不供给，不靠标题冒充可买。
        self.assertEqual(wmos.observed_purchase_opportunities(
            at_store, projection=self._projection(purchase_available=False),
            occurred_at=occurred, observed_at=occurred), [])
        self.assertEqual(wmos.observed_purchase_opportunities(
            self._observation("street_1"), projection=self._projection(),
            occurred_at=occurred, observed_at=occurred), [])
        unaffordable = self._projection(catalog=[{
            "catalog_item_id": "bedside_lamp", "name": "床头灯", "price": 900,
            "affordable": False}])
        self.assertEqual(wmos.observed_purchase_opportunities(
            at_store, projection=unaffordable, occurred_at=occurred,
            observed_at=occurred), [])

    def test_reserved_economy_store_survives_the_move_bound(self):
        observation = {"location": {"place_id": "street_1"}, "world_revision": 4,
                       "world_id": "w1"}
        venues = ["venue%02d" % index for index in range(8)] + ["home_goods_store"]
        streets = ["street_2", "street_3"]
        action_space = {"candidates": [
            {"verb": "MOVE", "params": {"destination_location_id": name}}
            for name in venues + streets]}
        rows = wmos.observed_move_opportunities(
            observation, action_space=action_space,
            occurred_at="2026-11-09T02:00:00+00:00",
            observed_at="2026-11-09T02:00:00+00:00",
            place_kinds={name: "street" for name in streets},
            reserved_destinations=["home_goods_store"])
        targets = [row["target_refs"][0] for row in rows]
        # 名额没有变大：仍然是原来那条上限；经济店和相邻街道都被保留。
        self.assertEqual(len(targets), wmos.MAX_MOVE_OPPORTUNITIES)
        for wanted in ("place:home_goods_store", "place:street_2", "place:street_3"):
            self.assertIn(wanted, targets)
        # 没有保留时行为不变（旧顺序 + 旧上限）。
        plain = wmos.observed_move_opportunities(
            observation, action_space=action_space,
            occurred_at="2026-11-09T02:00:00+00:00",
            observed_at="2026-11-09T02:00:00+00:00")
        self.assertNotIn("place:home_goods_store",
                         [row["target_refs"][0] for row in plain])

    def test_reservations_keep_the_browse_flower_shop_and_the_open_store(self):
        provider = wmos.WorldMoveOpportunityProvider(client=None, action_intent=None)
        provider.last_catalog = {"status": "available", "opportunities": [
            {"location_id": "flower_shop", "availability": {"status": "OPEN"}},
            {"location_id": "cafe", "availability": {"status": "OPEN"}},
        ]}
        provider.last_store_projection = {
            "store_id": "home_goods_store", "availability": {"status": "OPEN"}}
        self.assertEqual(provider._reserved_destinations(),
                         ["flower_shop", "home_goods_store"])
        # 花店的营业与否由它自己的停留层回答；移动名额只保证它没有被上限截掉。
        provider.last_store_projection["availability"]["status"] = "CLOSED"
        self.assertEqual(provider._reserved_destinations(), ["flower_shop"])
        provider.last_catalog["opportunities"][0]["availability"]["status"] = "CLOSED"
        self.assertEqual(provider._reserved_destinations(), ["flower_shop"])

    def test_purchase_adoption_completes_only_on_the_owner_transaction(self):
        import activity_progress_completion_lr5 as lr5

        fake = SimpleNamespace(last_world_opportunity_view=[{
            "target_ref": "buy:bedside_lamp",
            "purchase": {"name": "床头灯", "price": 900, "currency": "JPY"},
        }])
        spec = alc.AutonomousLifeCycle._purchase_adoption_spec(
            fake, "world_fact:place:home_goods_store", "buy:bedside_lamp", lr5)
        self.assertEqual(spec["activity_kind"], alc.PURCHASE_ACTIVITY_KIND)
        self.assertEqual(spec["target_refs"], ["world_fact:buy:bedside_lamp"])
        completion = spec["completion_contract_spec"]
        self.assertEqual(completion["criteria_kind"], lr5.CRITERIA_WORLD_FACT_MATCH)
        self.assertEqual(completion["criteria"]["fact_key"], alc.PURCHASE_FACT_KEY)
        self.assertEqual(completion["criteria"]["expected_value"],
                         "world_fact:buy:bedside_lamp")
        self.assertEqual(dict(completion["criteria"]).get("expected_value"),
                         completion["target_refs"][0])

    def test_same_operation_key_charges_once(self):
        import tempfile

        import world_property_economy as economy

        home = Path(tempfile.mkdtemp(prefix="v07-econ-", dir="/tmp"))
        env = {"CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
               "CHIYO_WORLD_DELIVERY_TIME_ENABLED": "0"}
        genesis = economy.initialize_genesis(hermes_home=home, environment=env)
        self.assertIn(genesis.get("status"), ("created", "already_initialized"))
        # 只把"店开着、人在店里"这一条隔开；钱包、目录、幂等键与写盘都是真的。
        with mock.patch.object(economy, "_store_check",
                               return_value={"status": "available"}):
            first = economy.purchase_item(
                catalog_item_id="bedside_lamp", purchase_intent_id="idem:v07:buy:1",
                main_self_verified=True, hermes_home=home, environment=env,
                now="2026-11-09T02:00:00+00:00")
            second = economy.purchase_item(
                catalog_item_id="bedside_lamp", purchase_intent_id="idem:v07:buy:1",
                main_self_verified=True, hermes_home=home, environment=env,
                now="2026-11-09T02:00:00+00:00")
        self.assertEqual(first["status"], "applied")
        self.assertEqual(second["status"], "already_applied")
        state = economy.load_state(home)
        self.assertEqual(len(state["transactions"]), 1)
        self.assertEqual(len(state["items"]), 1)
        self.assertEqual(state["items"][0]["disposition"], economy.DISPOSITION_OWNED)
        self.assertEqual(first["current_balance"], 10_000 - 900)
        self.assertEqual(second["current_balance"], 10_000 - 900)


class StepReadinessAndRefusalCloseoutTest(unittest.TestCase):
    """单步日常动作的 readiness 与真实 owner 拒绝后的完整 Activity 处理链。"""

    def test_daily_action_reports_no_further_step(self):
        import life_step_readiness as lsr

        readiness = lsr.assess_step_readiness(
            activity={"activity_ref": "actv_daily", "status": "ACTIVE",
                      "activity_kind": "DAILY_ACTION_IN_OBSERVED_WORLD"},
            target_ref="dailyact:place:surface:home:bed_surface:home_notes07",
            source_revision="1")
        self.assertIs(readiness.available, False)
        self.assertEqual(readiness.reason_code, lsr.REASON_DAILY_ACTION_SINGLE_ACTION)
        # BODY / MOVE 的既有单步判断不变。
        body = lsr.assess_step_readiness(
            activity={"activity_ref": "actv_body", "status": "ACTIVE",
                      "activity_kind": "BODY_ACTION_AT_OBSERVED_PLACE"},
            target_ref="bodyact:pose:seated:south", source_revision="1")
        self.assertIs(body.available, False)

    def test_real_owner_refusal_blocks_activity_and_frees_the_foreground(self):
        """真实 World owner 拒绝 → LR-2 BLOCK → 前台释放 → 下一轮能选新行动。

        环境变化发生在候选已经 offer 之后（Decision 的 invoke 里才撤走床面），
        所以这不是手造终态字典：拒绝来自真实 Resolver，收尾来自真实 ActivityCommand。
        """

        import world_body_substrate as wbs

        demo_mod = _load("v07_daily_demo_under_test",
                         ROOT / "scripts" / "v07_daily_life_demo.py")
        demo = demo_mod.Demo()
        try:
            demo.build()
            picked = demo.round("home_object")
            self.assertEqual(dict(picked.get("daily_action") or {}).get("verb"), "PICK_UP")
            original = demo.cognition._raw

            def raw(candidates, allowed):
                path = wbs.substrate_path(str(demo.world_home))
                store = wbs.SubstrateStore(path)
                state = store.load()
                state["placements"].pop("home_bed01", None)
                state["revision"] = int(state["revision"]) + 1
                store.save(state, expected_revision=int(state["revision"]) - 1)
                for candidate in candidates:
                    if any(str(ref).startswith("dailyact:place:surface:")
                           for ref in (candidate.get("target_refs") or [])):
                        return demo.cognition._decision(
                            "START", candidate, "owner-projected surface after env change")
                return original(candidates, allowed)

            demo.cognition._raw = raw
            refused = demo.round("home_object_back")
            self.assertEqual(refused.get("outcome"), "STEP_REFUSED")
            closeout = dict(refused.get("step_closeout") or {})
            self.assertEqual(closeout.get("status"), "BLOCKED")
            self.assertEqual(closeout.get("owner_status"), "BLOCKED")
            self.assertTrue(closeout.get("foreground_released"))
            activity_ref = str(closeout.get("activity_ref") or "")
            self.assertEqual(demo_mod._activity(demo.runtime, activity_ref).get("status"),
                             "BLOCKED")
            self.assertIsNone(demo_mod._foreground(demo.runtime))
            # 失败 Action 与它的 owner 回执原样保留，没有被改写成完成。
            action_ref = str(closeout.get("action_ref") or "")
            action = demo.runtime.action_read.get_action(action_ref) or {}
            self.assertEqual(action.get("status"), "FAILED")
            self.assertTrue(demo.runtime.action_read.list_receipts(action_ref))
            # 下一轮同一实例能真的开始别的行动。
            nxt = demo.round("home_small_thing")
            self.assertEqual(nxt.get("decision"), "START")
            self.assertEqual(nxt.get("adoption_status"), "APPLIED")
            self.assertIsNotNone(demo_mod._foreground(demo.runtime))
        finally:
            demo.close()

    def test_real_owner_arrange_refusal_blocks_activity_and_frees_the_foreground(self):
        """真实 owner 在 ARRANGE 执行前拒绝 → LR-2 BLOCK → 前台释放 → 下一轮能选新行动。

        与上面的 PLACE 拒绝不同，这里走的是真实投影出来的 ``dailyact:arrange:room`` 候选
        与它自己的 owner 完整性检查：归属在候选 offer 之后、Decision 的 ``invoke`` 里才变化，
        因此拒绝来自真实 World Resolver，收尾来自真实 ActivityCommand，而不是手造的终态字典。
        """

        import world_property_economy as economy

        _pin_bridge_action_intent()
        demo_mod = _load("v07_daily_demo_arrange_under_test",
                         ROOT / "scripts" / "v07_daily_life_demo.py")
        demo = demo_mod.Demo()
        try:
            demo.build()
            # One real counter purchase and the owner's own tick: only the "shop open / she is
            # at the counter" precondition is isolated; wallet, catalogue, debit, ownership
            # write and delivery materialization are the owner's.  She is at home/bedroom.
            with mock.patch.object(economy, "_store_check",
                                   return_value={"status": "available"}):
                bought = economy.purchase_item(
                    catalog_item_id="bedside_lamp",
                    purchase_intent_id="idem:v07:arrange-refusal:lamp",
                    main_self_verified=True, hermes_home=str(demo.world_home),
                    environment=demo.env, now=demo.clock.now)
            self.assertEqual(bought["status"], "applied", bought)
            lamp_id = str(bought["item_instance_id"])
            demo.gateway.service.tick()
            self.assertEqual(
                str((demo_mod._substrate_placements(demo.world_home).get(lamp_id) or {})
                    .get("area_id")), "bedroom")

            original = demo.cognition._raw
            captured: dict = {}

            def raw(candidates, allowed):
                for candidate in candidates:
                    refs = [str(ref) for ref in (candidate.get("target_refs") or [])]
                    target = next((ref for ref in refs
                                   if ref.startswith("dailyact:arrange:room:home:")
                                   and ref.endswith(":" + lamp_id)), "")
                    if not target or "START" not in [
                            str(item) for item in (candidate.get("legal_decisions") or [])]:
                        continue
                    captured["target"] = target
                    # She gives the lamp away between the Decision response and execution, so
                    # the ARRANGE target is no longer hers.  This is an ownership change on the
                    # economy owner's own document (a test fixture) -- not a hand-built FAILED
                    # Action and not an edited Activity.
                    state = economy.load_state(demo.world_home)
                    for item in state["items"]:
                        if item["item_instance_id"] == lamp_id:
                            item["disposition"] = economy.DISPOSITION_GIFTED_AWAY
                    economy._save(economy.state_path(demo.world_home), state)
                    demo.cognition._reasons = list(allowed[:1]) or ["NO_LEGAL_CANDIDATE"]
                    return demo.cognition._decision(
                        "START", candidate, "把买下的灯挪到另一个已声明区域")
                return original(candidates, allowed)

            demo.cognition._raw = raw
            refused = demo.round("arrange_lamp")
            # The chosen step really was the owner-projected ARRANGE (not the old PLACE test).
            self.assertTrue(str(captured.get("target") or "").startswith(
                "dailyact:arrange:room:home:"), captured)
            self.assertEqual(refused.get("world_verb"), "ARRANGE", refused)
            self.assertEqual(refused.get("outcome"), "STEP_REFUSED", refused)
            closeout = dict(refused.get("step_closeout") or {})
            self.assertEqual(closeout.get("status"), "BLOCKED")
            self.assertEqual(closeout.get("owner_status"), "BLOCKED")
            self.assertTrue(closeout.get("foreground_released"))
            activity_ref = str(closeout.get("activity_ref") or "")
            self.assertEqual(demo_mod._activity(demo.runtime, activity_ref).get("status"),
                             "BLOCKED")
            self.assertIsNone(demo_mod._foreground(demo.runtime))
            # The failed Action and its owner receipt survive verbatim (never rewritten done).
            action_ref = str(closeout.get("action_ref") or "")
            action = demo.runtime.action_read.get_action(action_ref) or {}
            self.assertEqual(action.get("status"), "FAILED")
            self.assertTrue(demo.runtime.action_read.list_receipts(action_ref))
            # The same instance really can start another legal action next round.
            nxt = demo.round("home_small_thing")
            self.assertEqual(nxt.get("decision"), "START")
            self.assertEqual(nxt.get("adoption_status"), "APPLIED")
            self.assertIsNotNone(demo_mod._foreground(demo.runtime))
        finally:
            demo.close()


def _bridge_action_intent():
    """The live bridge contract (the copy the Life chain imports), loaded by path."""

    path = ROOT / "components" / "world" / "service" / "bridge" / "action_intent.py"
    return _load("v07_bridge_action_intent_under_test", path)


def _daily_targets():
    return __import__("life_daily_action_targets")


class RoomLivingAdmissionTest(unittest.TestCase):
    """本单只窄接两个既有动词：参数越界一律在桥的边界拒绝。"""

    def test_arrange_and_toggle_are_admitted_with_exact_params(self):
        ai = _bridge_action_intent()
        self.assertIn("ARRANGE", ai.EXPOSED_VERBS)
        self.assertIn("TOGGLE_FIXTURE", ai.ACTION_ALLOWLIST)
        arrange = ai.validate_verb_params("ARRANGE", {
            "object_id": "item-1",
            "target": {"kind": "room", "location_id": "home", "area_id": "living"}})
        self.assertEqual(arrange["target"]["area_id"], "living")
        toggle = ai.validate_verb_params("TOGGLE_FIXTURE",
                                         {"object_id": "item-1", "state": "on"})
        self.assertEqual(toggle, {"object_id": "item-1", "state": "on"})

    def test_arrange_rejects_extra_fields_and_bad_targets(self):
        ai = _bridge_action_intent()
        good_target = {"kind": "room", "location_id": "home", "area_id": "living"}
        for bad in (
            {"object_id": "item-1", "target": good_target, "price": 1},
            {"object_id": "item-1", "target": good_target, "identity": "main_self"},
            {"object_id": "item-1", "target": dict(good_target, extra="x")},
            {"object_id": "item-1", "target": dict(good_target, kind="container")},
            {"object_id": "item-1", "target": "home/living"},
            {"object_id": "", "target": good_target},
        ):
            with self.assertRaises(ai.ActionIntentError, msg=bad):
                ai.validate_verb_params("ARRANGE", bad)

    def test_toggle_rejects_blind_and_non_absolute_states(self):
        ai = _bridge_action_intent()
        for bad in ({"object_id": "item-1", "state": "ON"},
                    {"object_id": "item-1", "state": "toggle"},
                    {"object_id": "item-1", "state": True},
                    {"object_id": "item-1", "state": "on", "power": "on"},
                    {"object_id": "item-1"}):
            with self.assertRaises(ai.ActionIntentError, msg=bad):
                ai.validate_verb_params("TOGGLE_FIXTURE", bad)

    def test_room_living_targets_round_trip_with_the_owner_parameters(self):
        targets = _daily_targets()
        arranged = targets.encode_daily_action_target("ARRANGE", {
            "object_id": "item-1",
            "target": {"kind": "room", "location_id": "home", "area_id": "living"}})
        parsed = targets.parse_daily_action_target(arranged)
        self.assertEqual(parsed["params"],
                         {"object_id": "item-1",
                          "target": {"kind": "room", "location_id": "home",
                                     "area_id": "living"}})
        self.assertEqual(targets.daily_action_completion(arranged)["expected_value"],
                         "world_fact:placement:item-1:room:home:living")
        toggled = targets.encode_daily_action_target(
            "TOGGLE_FIXTURE", {"object_id": "item-1", "state": "off"})
        self.assertEqual(targets.parse_daily_action_target(toggled)["verb"],
                         "TOGGLE_FIXTURE")
        self.assertEqual(targets.daily_action_completion(toggled)["expected_value"],
                         "world_fact:fixture:item-1:off")
        # An unknown fixture power is NOT "off": the absolute fact is only satisfied by a
        # recorded power that exactly matches.
        unknown = targets.daily_action_observed_fact(
            targets.parse_daily_action_target(toggled), {},
            available_actions={"actions": [{"verb": "TOGGLE_FIXTURE", "available": True,
                                            "candidates": [{"object_id": "item-1",
                                                            "power": None}]}]})
        self.assertFalse(unknown["satisfied"])
        self.assertIn("FIXTURE_POWER_UNKNOWN", unknown["unknowns"])


class RoomLivingOwnerTest(unittest.TestCase):
    """真实 owner 的最小边界：落地幂等、观察纯读、同 state noop、未拥有/跨宅拒绝。"""

    def test_landing_noop_and_rejections_are_owner_facts(self):
        import world_property_economy as economy
        import world_body_substrate as wbs

        demo_mod = _load("v07_daily_demo_owner_under_test",
                         ROOT / "scripts" / "v07_daily_life_demo.py")
        demo = demo_mod.Demo()
        try:
            demo.build()
            # One real counter purchase (same owner entry the buy step uses).  Only the
            # "shop is open / she is at the counter" precondition is isolated; wallet,
            # catalogue, debit and ownership write are real.
            with mock.patch.object(economy, "_store_check",
                                   return_value={"status": "available"}):
                bought = economy.purchase_item(
                    catalog_item_id="bedside_lamp",
                    purchase_intent_id="idem:v07:owner:lamp",
                    main_self_verified=True, hermes_home=str(demo.world_home),
                    environment=demo.env, now=demo.clock.now)
            self.assertEqual(bought["status"], "applied")
            lamp_id = str(bought["item_instance_id"])
            demo.gateway.service.tick()
            placements = demo_mod._substrate_placements(demo.world_home)
            self.assertEqual(str(placements[lamp_id].get("kind")), "room")
            self.assertEqual(str(placements[lamp_id].get("location_id")), "home")
            # Landing is idempotent: the same seam again is already_materialized, no new item.
            from world_phone_shopping import materialize_owned_item
            again = materialize_owned_item(
                hermes_home=str(demo.world_home), item_instance_id=lamp_id,
                environment=demo.env)
            self.assertEqual(again.get("status"), "already_materialized")
            self.assertEqual(len([row for row in economy.load_state(demo.world_home)["items"]]), 1)

            # A pure observation writes nothing (no placement resurrection / new fixture).
            before_placements = dict(placements)
            before_fixtures = dict(demo_mod._substrate_fixtures(demo.world_home))
            demo.provider.observe()
            self.assertEqual(demo_mod._substrate_placements(demo.world_home), before_placements)
            self.assertEqual(demo_mod._substrate_fixtures(demo.world_home), before_fixtures)

            state = json.loads((demo.world_home / "data" / "world" / "world_state.json")
                               .read_text(encoding="utf-8"))
            place = str((state.get("location") or {}).get("place_id"))
            area = str((state.get("location") or {}).get("area_id"))
            self.assertEqual(place, "home")

            def act(action, params):
                return demo.gateway.service.act({
                    "action": action, "execution_id": "test:%s:%d" % (action, act.seq),
                    "actor_id": "chiyo", "authority": "verified_main_self_tool",
                    "confirm_apply": True, "observed_dependencies": {}, "params": params})
            act.seq = 0

            # The lamp must be in the body's own area before it can be toggled.  If the
            # purchase landed it elsewhere, move it there through the real ARRANGE owner
            # verb; if it is already here, do not pretend a same-target arrange changed it.
            lamp_area = str(placements[lamp_id].get("area_id"))
            if lamp_area != area:
                act.seq += 1
                arranged = act("ARRANGE", {"object_id": lamp_id,
                                           "target": {"kind": "room", "location_id": "home",
                                                      "area_id": area}})
                self.assertEqual(arranged.get("status"), "applied", arranged)

            # Absolute off, then a same-state off is the owner's zero-write noop.
            act.seq += 1
            first = act("TOGGLE_FIXTURE", {"object_id": lamp_id, "state": "off"})
            self.assertEqual(first.get("status"), "applied", first)
            substrate_after = json.loads(
                (demo.world_home / "data" / "world" / "body_substrate.json")
                .read_text(encoding="utf-8"))["revision"]
            act.seq += 1
            repeat = act("TOGGLE_FIXTURE", {"object_id": lamp_id, "state": "off"})
            self.assertEqual(repeat.get("status"), "noop", repeat)
            substrate_repeat = json.loads(
                (demo.world_home / "data" / "world" / "body_substrate.json")
                .read_text(encoding="utf-8"))["revision"]
            self.assertEqual(substrate_repeat, substrate_after,
                             "同 state 的 TOGGLE 写了盘，不是零写 noop")

            # Not-owned / not-a-fixture object is refused by the owner.
            act.seq += 1
            stranger = act("TOGGLE_FIXTURE", {"object_id": "item-not-owned", "state": "on"})
            self.assertEqual(stranger.get("status"), "rejected")
            # Cross-home ARRANGE destination is refused.
            act.seq += 1
            cross = act("ARRANGE", {"object_id": lamp_id,
                                    "target": {"kind": "room", "location_id": "street_1",
                                               "area_id": "main"}})
            self.assertEqual(cross.get("status"), "rejected", cross)

            # A declared *other* area is a legal ARRANGE, but then the lamp is not in the
            # body's area and a TOGGLE must be refused as "not here".
            other = [found for found in economy.declared_home_areas(demo.env) if found != area]
            if other:
                act.seq += 1
                away = act("ARRANGE", {"object_id": lamp_id,
                                       "target": {"kind": "room", "location_id": "home",
                                                  "area_id": sorted(other)[0]}})
                self.assertEqual(away.get("status"), "applied", away)
                act.seq += 1
                not_here = act("TOGGLE_FIXTURE", {"object_id": lamp_id, "state": "on"})
                self.assertEqual(not_here.get("status"), "rejected", not_here)
        finally:
            demo.close()

    def test_arranged_placement_survives_a_repeated_materialize(self):
        """已摆放的家具不会被第二次落地重置：ARRANGE 走真实 owner，再 materialize 是幂等拒绝。

        定点补上原验收要求里的「不重置已摆放」：同一件已购 lamp 先在卧室重复落地是
        ``already_materialized``，经真实 owner ARRANGE 挪到另一已声明区域后，再次
        ``materialize_owned_item`` 既不会把它搬回卧室，也不会多写一个 placement；此前在
        本区域的 off→on 电源记录同样原样保留。
        """

        import world_property_economy as economy
        from world_phone_shopping import materialize_owned_item

        demo_mod = _load("v07_daily_demo_arrange_owner_under_test",
                         ROOT / "scripts" / "v07_daily_life_demo.py")
        demo = demo_mod.Demo()
        try:
            demo.build()
            with mock.patch.object(economy, "_store_check",
                                   return_value={"status": "available"}):
                bought = economy.purchase_item(
                    catalog_item_id="bedside_lamp",
                    purchase_intent_id="idem:v07:arrange-lamp-idem",
                    main_self_verified=True, hermes_home=str(demo.world_home),
                    environment=demo.env, now=demo.clock.now)
            self.assertEqual(bought["status"], "applied", bought)
            lamp_id = str(bought["item_instance_id"])
            demo.gateway.service.tick()
            landed = demo_mod._substrate_placements(demo.world_home).get(lamp_id) or {}
            self.assertEqual(str(landed.get("area_id")), "bedroom")

            # Repeated landing at the canonical target is the owner's own idempotent answer.
            again = materialize_owned_item(
                hermes_home=str(demo.world_home), item_instance_id=lamp_id,
                environment=demo.env)
            self.assertEqual(again.get("status"), "already_materialized", again)

            def act(action, params):
                act.seq += 1
                return demo.gateway.service.act({
                    "action": action, "execution_id": "test:%s:%d" % (action, act.seq),
                    "actor_id": "chiyo", "authority": "verified_main_self_tool",
                    "confirm_apply": True, "observed_dependencies": {}, "params": params})
            act.seq = 0

            # A real owner off→on while the lamp is in the body's own area (the only place the
            # owner admits a fixture toggle); the recorded power must survive the move below.
            self.assertEqual(act("TOGGLE_FIXTURE", {"object_id": lamp_id, "state": "off"})
                             .get("status"), "applied")
            self.assertEqual(act("TOGGLE_FIXTURE", {"object_id": lamp_id, "state": "on"})
                             .get("status"), "applied")
            fixtures_on = dict(demo_mod._substrate_fixtures(demo.world_home))
            self.assertEqual(str((fixtures_on.get(lamp_id) or {}).get("power")), "on")

            # A real owner ARRANGE moves the owned lamp from bedroom into another declared area.
            others = sorted(area for area in economy.declared_home_areas(demo.env)
                            if area != "bedroom")
            self.assertTrue(others, "家没有第二个已声明区域可挪")
            target_area = others[0]
            arranged = act("ARRANGE", {
                "object_id": lamp_id,
                "target": {"kind": "room", "location_id": "home", "area_id": target_area}})
            self.assertEqual(arranged.get("status"), "applied", arranged)
            moved = dict(demo_mod._substrate_placements(demo.world_home))
            self.assertEqual(str((moved.get(lamp_id) or {}).get("area_id")), target_area)
            self.assertNotEqual(target_area, "bedroom")
            count = len(moved)
            revision = json.loads(
                (demo.world_home / "data" / "world" / "body_substrate.json")
                .read_text(encoding="utf-8"))["revision"]

            # Re-running the delivery seam must not reset the arranged placement or the fixture.
            replay = materialize_owned_item(
                hermes_home=str(demo.world_home), item_instance_id=lamp_id,
                environment=demo.env)
            final = dict(demo_mod._substrate_placements(demo.world_home))
            self.assertEqual(str((final.get(lamp_id) or {}).get("area_id")), target_area,
                             "重复落地把已摆放的灯搬回了 %r" % final.get(lamp_id))
            self.assertEqual(len(final), count, "重复落地多写了一个 placement")
            self.assertEqual(
                json.loads((demo.world_home / "data" / "world" / "body_substrate.json")
                           .read_text(encoding="utf-8"))["revision"],
                revision, "重复落地写了盘（错把已摆放当成未落地）")
            self.assertEqual(dict(demo_mod._substrate_fixtures(demo.world_home)), fixtures_on,
                             "重复落地重置了 off→on 的电源记录")
            # Production refuses to overwrite a *different* existing placement rather than
            # answering already_materialized; both mean "do not reset what is already placed".
            self.assertEqual(replay.get("status"), "rejected", replay)
            self.assertEqual(replay.get("reason_code"), "PLACEMENT_CONFLICT", replay)
            self.assertEqual(str((replay.get("placement") or {}).get("area_id")), target_area)
        finally:
            demo.close()


class RepeatedFixtureToggleEvidenceTest(unittest.TestCase):
    """真实连续 off→on→off→on：同一盏灯、同一绝对状态值，四次独立 execution 各自完成。

    修复前，LR-5 只按 ``world_fact:fixture:<id>:<state>`` 这个**值**给完成证据归属：
    第二次 off 会与白天那次 off 撞成
    ``CONSEQUENCE_COMPLETION_EVIDENCE_BELONGS_TO_OTHER_ACTIVITY``。修好后完成证据先按
    本次 execution 的 ``progress_evidence_id`` 归属，四次真实 TOGGLE 各自 COMPLETED，
    证据互不冒用；同 state 的 owner 零写 noop 边界不变（由上面的 owner 用例另证）。
    """

    def test_off_on_off_on_toggles_each_complete_with_their_own_execution_evidence(self):
        import world_property_economy as economy

        demo_mod = _load("v07_daily_demo_repeated_toggle_under_test",
                         ROOT / "scripts" / "v07_daily_life_demo.py")
        demo = demo_mod.Demo()
        try:
            demo.build()
            with mock.patch.object(economy, "_store_check",
                                   return_value={"status": "available"}):
                bought = economy.purchase_item(
                    catalog_item_id="bedside_lamp",
                    purchase_intent_id="idem:v07:repeat-toggle",
                    main_self_verified=True, hermes_home=str(demo.world_home),
                    environment=demo.env, now=demo.clock.now)
            self.assertEqual(bought["status"], "applied", bought)
            lamp_id = str(bought["item_instance_id"])
            demo.gateway.service.tick()
            state = json.loads((demo.world_home / "data" / "world" / "world_state.json")
                               .read_text(encoding="utf-8"))
            self.assertEqual(str((state.get("location") or {}).get("place_id")), "home")
            area = str((state.get("location") or {}).get("area_id"))
            placement = dict(demo_mod._substrate_placements(demo.world_home).get(lamp_id) or {})
            if str(placement.get("area_id")) != area:
                arranged = demo.gateway.service.act({
                    "action": "ARRANGE", "execution_id": "test:repeat-toggle:arrange",
                    "actor_id": "chiyo", "authority": "verified_main_self_tool",
                    "confirm_apply": True, "observed_dependencies": {},
                    "params": {"object_id": lamp_id,
                               "target": {"kind": "room", "location_id": "home",
                                          "area_id": area}}})
                self.assertEqual(arranged.get("status"), "applied", arranged)
            evidence: list[str] = []
            for expected in ("off", "on", "off", "on"):
                out = demo.round_until("toggle_lamp")
                action = dict(out.get("daily_action") or {})
                self.assertEqual(action.get("verb"), "TOGGLE_FIXTURE", out)
                self.assertTrue(action.get("satisfied"), out)
                self.assertEqual(str((action.get("params") or {}).get("state")),
                                 expected, out)
                fixture = dict(demo_mod._substrate_fixtures(demo.world_home)
                               .get(lamp_id) or {})
                self.assertEqual(str(fixture.get("power")), expected, fixture)
                activity = demo_mod._activity(
                    demo.runtime, str(out.get("activity_ref") or ""))
                self.assertEqual(activity.get("status"), "COMPLETED", activity)
                ref = str(activity.get("completion_evidence_ref") or "")
                self.assertTrue(ref, activity)
                self.assertNotIn(ref, evidence,
                                 "第 %d 次 TOGGLE 复用了别的 execution 的完成证据"
                                 % (len(evidence) + 1))
                evidence.append(ref)
            self.assertEqual(len(set(evidence)), 4, evidence)
            eligibilities = list(demo.runtime.progress_read.list_completion_eligibilities())
            for ref in evidence:
                owners = sorted({str(row.get("activity_id")) for row in eligibilities
                                 if ref in list(row.get("authoritative_result_refs") or [])})
                self.assertEqual(len(owners), 1,
                                 "完成证据 %s 被多个 Activity 共享: %s" % (ref, owners))
        finally:
            demo.close()


class EveningCreativeChainTest(unittest.TestCase):
    """0.7 第三阶段：白天 unclear → popt 提案 → owner 建作品 → 同 artifact 续写 → future 到期。"""

    @staticmethod
    def _demo_module():
        return _load("v07_evening_demo_under_test",
                     ROOT / "scripts" / "v07_daily_life_demo.py")

    @staticmethod
    def _walk_to(demo, place):
        """正式 MOVE + 旅程结算，只推进本演示自己的业务时钟。"""

        demo.cognition.place_wish = place
        out = demo.round("go_to")
        journey = dict(out.get("journey") or {})
        settled = demo.cycle.settle_world_journey(
            observed_at=(str(journey.get("expected_arrival_at") or "") or demo.clock.iso()))
        for _ in range(8):
            if settled.get("arrived") or settled.get("journey_status") == "completed":
                break
            settled = demo.cycle.settle_world_journey(
                observed_at=(str(settled.get("expected_arrival_at") or "") or demo.clock.iso()))
        assert settled.get("arrived"), settled
        walk = next((int(item.get("walk_seconds") or 0)
                     for item in (demo.provider.last_opportunity_view or [])
                     if str(item.get("target_ref")) == "place:%s" % place), 0)
        demo.clock.advance(walk or 1, because="走到 %s" % place)

    @staticmethod
    def _finish_reading(demo, demo_mod):
        """读到最后一页：第一页留下正式 ``unclear``，其余页由脚本响应给 ``no_change``。"""

        first = demo.round("read")
        plan = dict(first.get("reading") or {})
        assert int(plan.get("page") or 0) == 1, plan
        annotation = dict(plan.get("annotation") or {})
        assert annotation.get("kind") == "unclear", annotation
        assert annotation.get("state") == "APPLIED", annotation
        assert bool(annotation.get("item_id")), annotation
        reading_activity = str(first.get("activity_ref") or "")
        second = demo.round("continue")
        assert int(dict(second.get("reading") or {}).get("page") or 0) == 2, second
        demo.round("pause_reading")
        demo.clock.advance(31 * 60, because="阅读短间隔到期")
        demo.cycle.maintain_waiting(observed_at=demo.clock.iso())
        demo.cognition.resume_activity = reading_activity
        demo.round("resume")
        if demo_mod._activity(demo.runtime, reading_activity).get("status") != "COMPLETED":
            demo.round("continue")
        assert demo_mod._activity(demo.runtime, reading_activity).get("status") == "COMPLETED"
        return annotation.get("item_id")

    def test_question_becomes_owned_project_and_due_item_is_read_only(self):
        import life_project_options as lpo

        demo_mod = self._demo_module()
        demo = demo_mod.Demo()
        try:
            demo.build()
            # 家里同时有 12 条世界候选；阅读在书店（候选更少）进行，与演示主链一致。
            self._walk_to(demo, "street_1")
            self._walk_to(demo, "bookstore")
            marker_id = self._finish_reading(demo, demo_mod)
            self.assertTrue(marker_id)
            questions = demo.cycle.project_questions_provider()
            self.assertTrue(any(demo_mod.DAY_QUESTION in str(row.get("note") or "")
                                for row in questions), questions)

            heads_before = {
                "work": demo.port.resume_point(str(demo.data["artifact_id"])).head_version_id,
                "reading": demo.port.resume_point(
                    str(demo.data["reading_artifact_id"])).head_version_id,
                "items": demo.port.resume_point(
                    str(demo.data["private_items_artifact_id"])).head_version_id,
            }
            created_round = demo.round("write_project")
            created = dict(created_round.get("project_created") or {})
            self.assertEqual(created_round.get("outcome"), "ACTED", created_round)
            option = next((dict(row) for row in demo.cycle.last_project_options
                           if str(row.get("option_ref")) == str(created.get("option_ref"))), {})
            self.assertEqual(option.get("kind"), lpo.KIND_QUESTION)
            self.assertTrue(str(option.get("basis_ref") or "").startswith("question:"))
            project_artifact = str(created.get("artifact_id") or "")
            self.assertTrue(project_artifact)
            first_head = demo.port.resume_point(project_artifact)
            self.assertGreaterEqual(int(first_head.version_number or 0), 2)
            self.assertLessEqual(len(demo.cognition.offered[-1]["candidates"]), 16)

            demo.cognition.project_bare = demo_mod._bare(project_artifact)
            continued = demo.round("continue_project")
            second_head = demo.port.resume_point(project_artifact)
            self.assertEqual(continued.get("decision"), "CONTINUE", continued)
            self.assertEqual(second_head.artifact_id, project_artifact)
            self.assertGreater(int(second_head.version_number or 0),
                               int(first_head.version_number or 0))
            self.assertGreaterEqual(int(demo.writer.call_count), 1)
            for name, ref in (("work", demo.data["artifact_id"]),
                              ("reading", demo.data["reading_artifact_id"]),
                              ("items", demo.data["private_items_artifact_id"])):
                self.assertEqual(demo.port.resume_point(str(ref)).head_version_id,
                                 heads_before[name], name)
            self.assertLessEqual(len(demo.cognition.offered[-1]["candidates"]), 16)

            # 真实 ABANDON 腾出前台后提交带 due 的 later item；到期前同一候选源没有 fitem。
            abandoned = demo.round("abandon")
            self.assertEqual(abandoned.get("decision"), "ABANDON", abandoned)
            self.assertIsNone(demo_mod._foreground(demo.runtime))
            demo.cognition.later_item = {
                "title": "睡前再想想早晨那个问题", "in_minutes": 60,
                "source_refs": ["artifact:%s" % demo.cognition.project_bare]}
            proposed = demo.round("propose_future")
            writes = [row for row in (proposed.get("future_item_writes") or [])
                      if str(row.get("op")) == "propose"]
            self.assertTrue(writes and writes[0].get("status") == "APPLIED", writes)
            item_ref = str(writes[0].get("item_ref") or "")
            due_at = str(writes[0].get("due_at") or "")
            stored = demo.cycle.future_items.get(item_ref)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.status, "OPEN")
            self.assertIn(project_artifact, stored.source_refs)
            self.assertNotIn(item_ref, [row["item_ref"] for row in
                                        demo.cycle.due_future_items(demo.clock.iso())])
            pre_due, _snap, _meta = demo.cycle.artifact_source.materialize(
                observed_at=demo.clock.iso())
            self.assertFalse(any(str(row.source_ref or "").startswith("fitem:")
                                 for row in pre_due))

            due_moment = datetime.fromisoformat(due_at)
            demo.clock.advance(
                max(1, int((due_moment - demo.clock.now).total_seconds()) + 60),
                because="future item 到期")
            open_before = len(demo.cycle.future_items.open_items())
            # 同一 round 里重复提交同一提案：幂等键必须复用同一条 item（不新增）。
            demo.cognition.later_item = {
                "title": "睡前再想想早晨那个问题", "due_at": due_at,
                "source_refs": ["artifact:%s" % demo.cognition.project_bare]}
            due_round = demo.round("futures")
            self.assertIn(item_ref, [row["item_ref"] for row in
                                     demo.cycle.due_future_items(demo.clock.iso())])
            self.assertTrue(str((demo.cognition.reads[-1] or {}).get("chose") or "")
                            .startswith("fitem:"))
            self.assertEqual(due_round.get("decision"), "NO_ACTION", due_round)
            self.assertEqual(demo.cycle.future_items.get(item_ref).status, "OPEN")
            self.assertEqual(demo.port.resume_point(project_artifact).head_version_id,
                             second_head.head_version_id)
            duplicates = [row for row in (due_round.get("future_item_writes") or [])
                          if str(row.get("op")) == "propose"]
            self.assertTrue(duplicates and duplicates[0].get("status") == "APPLIED")
            self.assertIs(duplicates[0].get("created"), False)
            self.assertEqual(str(duplicates[0].get("item_ref")), item_ref)
            self.assertEqual(len(demo.cycle.future_items.open_items()), open_before)
            self.assertLessEqual(len(demo.cognition.offered[-1]["candidates"]), 16)

            # 没有 owner 时，不提供任何 popt，也不会凭空造作品（review 也属于另一个 owner，
            # 所以这里把两个可选 seam 都显式关掉，只留下"没有项目 owner"这一件事）。
            import life_practice_context as lpc

            artifacts_before = len(demo.port.list_artifacts())
            demo.cycle.practice = lpc.PracticeSeam(None)
            demo.cycle.project_gateway = lpo.ProjectOwnerGateway(None)
            self.assertEqual(demo.cycle.project_options(now=demo.clock.iso()), [])
            self.assertFalse(any(str(row.get("source_ref") or "").startswith("popt:")
                                 for row in demo.cycle._extra_opportunities()))
            self.assertEqual(len(demo.port.list_artifacts()), artifacts_before)
        finally:
            demo.close()


class OwnerRefusalTest(unittest.TestCase):
    """没有 owner / owner 拒绝时，gateway 只按名字拒绝，绝不返回一个伪造的 artifact。"""

    def test_unavailable_and_refusing_owners_never_confirm_a_work(self):
        import life_project_options as lpo

        unavailable = lpo.ProjectOwnerGateway(None)
        self.assertFalse(unavailable.available())
        with self.assertRaises(lpo.ProjectOptionError) as ctx:
            unavailable.create_project(title="t", seed_text="s", source_refs=[],
                                       option_ref="popt:1")
        self.assertEqual(ctx.exception.reason_code, lpo.REASON_OWNER_UNAVAILABLE)

        class Refusing:
            def list_projects(self):
                return {"status": "OK", "projects": []}

            def create_project(self, **kwargs):
                raise RuntimeError("owner said no")

        refusing = lpo.ProjectOwnerGateway(Refusing(), workspace_id="ws-1")
        self.assertTrue(refusing.available())
        with self.assertRaises(lpo.ProjectOptionError) as ctx:
            refusing.create_project(title="t", seed_text="s", source_refs=[],
                                    option_ref="popt:2")
        self.assertEqual(ctx.exception.reason_code, lpo.REASON_OWNER_UNUSABLE)
        self.assertEqual(lpo.build_options(questions=[], due_items=[]), [])


class PausedTargetDoesNotStartTest(unittest.TestCase):
    """PAUSED/WAITING 的目标只能走 RESUME，候选源不得再给一次 fresh START。"""

    def test_due_item_targeting_a_paused_artifact_is_not_reoffered(self):
        import artifact_continuation_source as acs

        target = "artifact:artifact:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

        class Port:
            def available(self):
                return True

            def list_artifacts(self):
                return []

        source = acs.ArtifactContinuationSource(
            Port(), subject_id="test:paused",
            paused_target_provider=lambda: [target],
            extra_opportunity_provider=lambda: [{
                "source_ref": "fitem:due001", "target_refs": [target],
                "title": "睡前再想想", "due_at": "2026-11-09T12:00:00+00:00",
                "kind": "FUTURE_ITEM_DUE", "revision": 1}])
        items, _snap, _meta = source.discover(observed_at="2026-11-09T10:00:00+00:00")
        self.assertEqual(items, [])
        self.assertEqual(source.last_extra_skip_reasons.get("fitem:due001"),
                         "PAUSED_TARGET_REQUIRES_RESUME")


_IMPORT_PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import action_intent as ai

out = {"file": ai.__file__,
       "verbs": [v for v in ("ARRANGE", "TOGGLE_FIXTURE") if v in ai.EXPOSED_VERBS],
       "allowlist": [v for v in ("ARRANGE", "TOGGLE_FIXTURE") if v in ai.ACTION_ALLOWLIST]}
good_arrange = {"object_id": "item-1",
                "target": {"kind": "room", "location_id": "home", "area_id": "living"}}
good_toggle = {"object_id": "item-1", "state": "on"}
out["arrange_ok"] = ai.validate_verb_params("ARRANGE", good_arrange)["target"]["area_id"] == "living"
out["toggle_ok"] = ai.validate_verb_params("TOGGLE_FIXTURE", good_toggle) == good_toggle
bad = [
    ("ARRANGE", {"object_id": "item-1", "target": good_arrange["target"], "price": 1}),
    ("ARRANGE", {"object_id": "item-1", "target": dict(good_arrange["target"], kind="container")}),
    ("ARRANGE", {"object_id": "item-1", "target": "home/living"}),
    ("ARRANGE", {"object_id": "", "target": good_arrange["target"]}),
    ("TOGGLE_FIXTURE", {"object_id": "item-1", "state": "ON"}),
    ("TOGGLE_FIXTURE", {"object_id": "item-1", "state": True}),
    ("TOGGLE_FIXTURE", {"object_id": "item-1"}),
]
out["rejects"] = 0
for verb, params in bad:
    try:
        ai.validate_verb_params(verb, params)
    except ai.ActionIntentError:
        out["rejects"] += 1
intent = {"intent_status": "AUTHORIZED", "verb": "TOGGLE_FIXTURE",
          "execution_id": "exec:probe", "params": {"object_id": "item-1", "state": "toggle"}}
snapshot = {"location": {"place_id": "home"}, "world_revision": 1, "pose": "standing"}
out["submit_rejects"] = 0
try:
    ai.build_submission(intent, fresh_snapshot=snapshot)
except ai.ActionIntentError:
    out["submit_rejects"] = 1
print(json.dumps(out))
'''


class DefaultPracticeAndReviewSeedTest(unittest.TestCase):
    """0.7 第四阶段：默认装配就接上 practice/Memory；回顾种子排除自身结果。"""

    @staticmethod
    def _demo_module():
        return _load("v07_stage4_demo_under_test",
                     ROOT / "scripts" / "v07_daily_life_demo.py")

    def test_prepared_profile_alone_wires_the_default_practice_seam(self):
        demo_mod = self._demo_module()
        demo = demo_mod.Demo()
        try:
            demo.build()
            # The instance's own prepare profile now carries the owner factory's authoritative
            # subject alias.  It is an OWNED name, so a stray host value is stripped first; the
            # value that survives is the prepared same-instance subject, never a hard-coded name.
            self.assertEqual(str(demo.env.get("CHIYO_LIFE_SUPPLY_SUBJECT")),
                             str(demo.data["subject_id"]))
            practice = demo.cycle.practice
            self.assertTrue(practice.available(), practice.availability())
            self.assertTrue(practice.memory_bound(), practice.availability())
            self.assertEqual(str(practice.service.subject_id), str(demo.data["subject_id"]))
            report = dict((getattr(demo.fixture, "assembled", None) or {}).get("practice") or {})
            self.assertEqual(str(report.get("status")), "READY", report)
            # project_from_env / practice_from_env resolve to the same Supply subject/workspace.
            import personal_practice_service as pps
            import private_project_service as ppsvc
            owner = ppsvc.private_projects_from_env(dict(demo.env))
            self.assertEqual(owner.service.subject_id, demo.data["subject_id"])
            self.assertEqual(owner.service.workspace_id, demo.data["workspace_id"])
            service = pps.practice_from_env(None, dict(demo.env))
            self.assertIsNotNone(service, "practice_from_env did not resolve from prepare env")
            self.assertEqual(service.subject_id, demo.data["subject_id"])
        finally:
            demo.close()

    def test_review_operation_key_is_derived_from_the_option_and_experiences(self):
        key = alc.AutonomousLifeCycle._review_operation_key(
            "popt:1", ["lexp:a", "lexp:b"])
        self.assertTrue(key.startswith("review:"), key)
        self.assertEqual(key, alc.AutonomousLifeCycle._review_operation_key(
            "popt:1", ["lexp:a", "lexp:b"]))
        self.assertNotEqual(key, alc.AutonomousLifeCycle._review_operation_key(
            "popt:1", ["lexp:a", "lexp:c"]))

    def test_review_seed_skips_its_own_result_and_keeps_ordinary_experiences(self):
        import life_project_options as lpo

        rows = [
            {"experience_id": "lexp:review", "occurred_at": "2026-11-09T10:00:00+00:00",
             "text": "刚写完的那份回顾", "artifact_ref": "artifact:review1"},
            {"experience_id": "lexp:proj", "occurred_at": "2026-11-09T10:01:00+00:00",
             "text": "写完了一小段作品", "artifact_ref": "artifact:proj1"},
            {"experience_id": "lexp:world", "occurred_at": "2026-11-09T10:02:00+00:00",
             "text": "在真实世界里做成了一件小事", "artifact_ref": None},
        ]
        seed = lpo.review_seed(rows, available=True,
                               excluded_artifact_refs=["artifact:review1"])
        ids = [row["experience_id"] for row in seed["experiences"]]
        self.assertNotIn("lexp:review", ids)
        self.assertIn("lexp:proj", ids)
        self.assertTrue(ids)
        # An unclassified source withholds the whole automatic seed (never "assume ordinary").
        self.assertIsNone(lpo.review_seed(
            rows, available=True, excluded_artifact_refs=["artifact:review1"],
            source_verified=False))
        options = lpo.build_options(
            questions=[], due_items=[], experiences=rows, practice_available=True,
            review_excluded_artifact_refs=["artifact:review1"])
        self.assertTrue(any(row["kind"] == lpo.KIND_REVIEW for row in options), options)


class ShareableSourcesV07Test(unittest.TestCase):
    """0.7 第五阶段：ACTIVE 前台绑定的新作品进入有界来源；非作品/私文不进入。"""

    @staticmethod
    def _cycle(*, activity, sources, reading_title=None):
        cycle = object.__new__(alc.AutonomousLifeCycle)
        cycle.runtime = SimpleNamespace(
            canonical_state=lambda: {"foreground_activity_ref": "actv:1"},
            activity_read=SimpleNamespace(get_activity=lambda ref: dict(activity)))
        cycle.work_artifact_id = "artifact:work1"
        cycle.reading_artifact_id = "artifact:read1"
        cycle.private_items_artifact_id = "artifact:items1"
        cycle.artifact_port = None
        cycle.artifact_source = SimpleNamespace(last_resume_points=dict(sources))
        cycle._round_work_titles = {"artifact:work1": "白天的工作"}
        cycle._reading_situation_cache = (
            {"reading_artifact_id": "artifact:read1",
             "material": {"title": reading_title or "一本书"}}
            if reading_title is not None else None)
        cycle.last_error = ""
        cycle._non_work_artifact_ids = lambda: ["artifact:method1"]
        return cycle

    @staticmethod
    def _point(artifact_id, title, resumable=True):
        return SimpleNamespace(artifact_id=artifact_id, title=title, resumable=resumable,
                               head_version_id="artifact-version:v1", version_number=1)

    def test_active_foreground_artifact_is_first_and_bounded_to_three(self):
        activity = {"activity_id": "actv:1", "status": "ACTIVE",
                    "activity_kind": "PERSONAL_PROJECT_CONTINUATION",
                    "target_ref": "artifact:artifact:book1",
                    "subject_refs": ["artifact:artifact:book1"]}
        cycle = self._cycle(activity=activity, sources={
            "artifact:book1": self._point("artifact:book1", "晚间小册子"),
            "artifact:work1": self._point("artifact:work1", "白天的工作"),
            "artifact:read1": self._point("artifact:read1", "一本书"),
        }, reading_title="一本书")
        sources = cycle.shareable_sources(limit=3)
        ids = [row["artifact_id"] for row in sources]
        self.assertEqual(ids[0], "artifact:book1", sources)
        self.assertEqual(sources[0]["kind"], "foreground_work")
        self.assertEqual(sources[0]["title"], "晚间小册子")
        self.assertEqual(len(sources), 3)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertNotIn("artifact:items1", ids)
        # limit is still the caller's bound, not "everything".
        self.assertEqual(len(cycle.shareable_sources(limit=1)), 1)

    def test_private_items_waiting_foreground_methods_and_share_drafts_are_not_sources(self):
        waiting = {"activity_id": "actv:1", "status": "WAITING",
                   "activity_kind": "PERSONAL_PROJECT_CONTINUATION",
                   "target_ref": "artifact:artifact:book1"}
        sources = {"artifact:book1": self._point("artifact:book1", "晚间小册子"),
                   "artifact:work1": self._point("artifact:work1", "白天的工作")}
        cycle = self._cycle(activity=waiting, sources=sources)
        ids = [row["artifact_id"] for row in cycle.shareable_sources()]
        self.assertNotIn("artifact:book1", ids)
        self.assertEqual(ids, ["artifact:work1", "artifact:read1"])

        private = {"activity_id": "actv:1", "status": "ACTIVE",
                   "activity_kind": "PRIVATE_PAGE_READ",
                   "target_ref": "artifact:artifact:items1",
                   "subject_refs": ["artifact:artifact:items1"]}
        cycle = self._cycle(activity=private, sources={
            "artifact:items1": self._point("artifact:items1", "私人文档")})
        self.assertNotIn("artifact:items1",
                         [row["artifact_id"] for row in cycle.shareable_sources()])

        method = {"activity_id": "actv:1", "status": "ACTIVE",
                  "activity_kind": "PERSONAL_PROJECT_CONTINUATION",
                  "target_ref": "artifact:artifact:method1"}
        cycle = self._cycle(activity=method, sources={
            "artifact:method1": self._point("artifact:method1", "一条做法")})
        self.assertNotIn("artifact:method1",
                         [row["artifact_id"] for row in cycle.shareable_sources()])

        draft = {"activity_id": "actv:1", "status": "ACTIVE",
                 "activity_kind": "PERSONAL_PROJECT_CONTINUATION",
                 "target_ref": "artifact:artifact:share1"}
        cycle = self._cycle(activity=draft, sources={
            "artifact:share1": self._point("artifact:share1", "[share] 一段分享")})
        self.assertNotIn("artifact:share1",
                         [row["artifact_id"] for row in cycle.shareable_sources()])

    def test_the_foreground_object_is_not_double_counted(self):
        activity = {"activity_id": "actv:1", "status": "ACTIVE",
                    "activity_kind": "PERSONAL_PROJECT_CONTINUATION",
                    "target_ref": "artifact:artifact:work1"}
        cycle = self._cycle(activity=activity, sources={
            "artifact:work1": self._point("artifact:work1", "白天的工作")})
        ids = [row["artifact_id"] for row in cycle.shareable_sources()]
        self.assertEqual(ids.count("artifact:work1"), 1, ids)


class SharingLifecycleV07Test(unittest.TestCase):
    """真实 ``LifeSharingService`` + 真实 ``PrivateProjectService``（最底层 owner 为内存合同）。"""

    def setUp(self):
        import test_v04_sharing_service_stage as stage

        self.stage = stage
        self.port = stage.FakeOwnerPort()
        self.source = "artifact:v07-stage5-source"
        self.port.seed(artifact_id=self.source, title="晚间小册子", content=stage.SOURCE_TEXT)
        self.clock = stage.Clock()
        self.projects = stage.pps.PrivateProjectService(
            self.port, stage.WORKSPACE, stage.GRANT)
        self.service = stage.lss.LifeSharingService(self.projects, clock=self.clock)

    def _draft(self, text, title="一段"):
        answer = self.service.prepare_share(source_artifact_id=self.source,
                                            share_text=text, title=title)
        self.assertTrue(answer.get("ok"), answer)
        return dict(answer.get("data") or {})

    def _share(self, share_id):
        answer = self.service.read_share(share_id, include_text=False)
        return answer, dict((answer.get("data") or {}).get("share") or {})

    def _head(self, share_id):
        return str(self._share(share_id)[1].get("head_version_id") or "")

    def _send(self, share_id):
        return self.service.apply_action(share_id=share_id, action="send_local",
                                         expected_head=self._head(share_id))

    def _inbox_count(self):
        answer = self.service.list_shares(include_text=False)
        return (answer.get("data") or {}).get("inbox", {}).get("delivered_count")

    def test_prepare_defer_send_is_one_local_delivery_and_repeat_is_refused(self):
        data = self._draft("今天这一小段先留在这里。")
        share_id = str(data.get("share_id") or "")
        self.assertEqual(str(data.get("state")), "DRAFT")
        deferred = self.service.apply_action(
            share_id=share_id, action="defer", expected_head=self._head(share_id),
            defer_condition="等这页写完")
        self.assertEqual(str((deferred.get("data") or {}).get("state")), "DEFERRED", deferred)
        before = self._inbox_count()
        delivered = self._send(share_id)
        data_out = dict(delivered.get("data") or {})
        receipt = dict(data_out.get("receipt") or {})
        self.assertTrue(delivered.get("ok"), delivered)
        self.assertEqual(str(data_out.get("state")), "LOCAL_DELIVERED")
        self.assertTrue(receipt.get("delivery_id"))
        self.assertIs(receipt.get("local_only"), True)
        self.assertIs(receipt.get("external_channel_sent"), False)
        self.assertIs(receipt.get("read_by_audience"), False)
        self.assertIs(receipt.get("understood"), False)
        self.assertEqual(self._inbox_count(), before + 1)
        again = self._send(share_id)
        self.assertFalse(again.get("ok"), again)
        self.assertEqual(again.get("reason_code"), "SHARE_ALREADY_DELIVERED")
        self.assertEqual(str((again.get("data") or {}).get("receipt", {}).get("delivery_id")),
                         receipt.get("delivery_id"))
        self.assertEqual(self._inbox_count(), before + 1)

    def test_drop_is_terminal_and_never_revokes_an_already_delivered_share(self):
        first = self._draft("先发出去的一段。", "已发出的")
        first_id = str(first.get("share_id") or "")
        self._send(first_id)
        _, delivered = self._share(first_id)
        first_receipt = self._share(first_id)[0]["data"]["receipt"]
        second = self._draft("想撤回的一段。", "要放弃的")
        second_id = str(second.get("share_id") or "")
        dropped = self.service.apply_action(
            share_id=second_id, action="drop", expected_head=self._head(second_id),
            reason="先不说了")
        self.assertEqual(str((dropped.get("data") or {}).get("state")), "DROPPED")
        refused = self._send(second_id)
        self.assertFalse(refused.get("ok"))
        self.assertEqual(refused.get("reason_code"), "SHARE_TERMINAL_STATE")
        _, still = self._share(first_id)
        self.assertEqual(str(still.get("state")), "LOCAL_DELIVERED")
        self.assertEqual(self._share(first_id)[0]["data"]["receipt"]["delivery_id"],
                         first_receipt["delivery_id"])
        self.assertEqual(str(delivered.get("state")), "LOCAL_DELIVERED")

    def test_a_new_source_version_blocks_the_send_before_any_delivery(self):
        data = self._draft("这一版先说这么多。")
        share_id = str(data.get("share_id") or "")
        moved = self.port.commit_version(
            artifact_id=self.source, expected_head=self.port.head_version_id(self.source),
            content="她又写了一版。", grant_id=self.stage.GRANT,
            operation_key="v07:stage5:source:v2")
        self.assertEqual(moved.effect_state, self.stage.lap.EffectState.APPLIED, moved)
        refused = self._send(share_id)
        self.assertFalse(refused.get("ok"), refused)
        self.assertEqual(str(refused.get("status")), "stale_source")
        self.assertEqual(refused.get("reason_code"), "SOURCE_REVISION_CHANGED")
        self.assertEqual(str(self._share(share_id)[1].get("state")), "DRAFT")
        self.assertEqual(self._inbox_count(), 0)

    def test_the_same_prepare_intent_never_creates_a_second_draft(self):
        first = self._draft("同一段话。", "同一个意思")
        # The in-memory owner contract replays an identical operation key back to the *same*
        # artifact (the real owner answers `rejected`); either way there is exactly one draft.
        second = self.service.prepare_share(source_artifact_id=self.source,
                                            share_text="同一段话。", title="同一个意思")
        self.assertEqual(str((second.get("data") or {}).get("share_id") or ""),
                         str(first.get("share_id") or ""))
        self.assertEqual(len(self.service.list_shares(include_text=False)["data"]["shares"]), 1)


class DefaultSharingAndPresenceV07Test(unittest.TestCase):
    """默认装配就是同一个 Supply subject/workspace；普通消息不暂停；typed 邀请真交接/恢复。"""

    @staticmethod
    def _demo_module():
        return _load("v07_stage5_defaults_demo_under_test",
                     ROOT / "scripts" / "v07_daily_life_demo.py")

    def test_default_sharing_ordinary_message_and_typed_presence(self):
        demo_mod = self._demo_module()
        demo = demo_mod.Demo()
        try:
            demo.build()
            # (1) 默认 sharing 服务来自实例自己的 env，与 project owner 同一个 subject/workspace。
            import life_sharing_context as lsc

            service = demo.cycle.sharing_service
            self.assertIsNotNone(service)
            self.assertEqual(type(service).__name__, "LifeSharingService")
            self.assertEqual(str(service.subject_id), str(demo.data["subject_id"]))
            self.assertEqual(str(service.workspace_id), str(demo.data["workspace_id"]))
            from_env, reason = lsc.service_from_env(dict(demo.env))
            self.assertIsNone(reason, reason)
            self.assertEqual(str(from_env.subject_id), str(demo.data["subject_id"]))
            self.assertEqual(str(from_env.workspace_id), str(demo.data["workspace_id"]))

            # 同环境完整用例：就在拥挤的 home/living 里开始阅读，不再走到候选更少的书店
            # 规避。此时 World 自己就给出十几条机会，16 条上限仍必须保住 RESUME_ACTIVITY。
            home_state = json.loads(
                (demo.world_home / "data" / "world" / "world_state.json").read_text(
                    encoding="utf-8"))
            self.assertEqual(str((home_state.get("location") or {}).get("place_id")),
                             "home")
            started = demo.round("read")
            self.assertEqual(started.get("decision"), "START", started)
            private_ref = str(started.get("activity_ref") or "")
            before = demo_mod._activity(demo.runtime, private_ref)
            self.assertEqual(before.get("status"), "ACTIVE")
            # Zero product model: the reply half is explicitly off, so this test cannot pick up a
            # user's provider config.  The transport observation is still real.
            if getattr(demo.cycle.social_contacts, "responder", None) is not None:
                demo.cycle.social_contacts.responder = None

            # (2) 普通消息：既有本地入站 → OBSERVATION_ONLY；Activity 四件事都不动。
            submitted = demo.cycle.contact_inbox.submit({
                "event_kind": "LOCAL_MESSAGE",
                "text": "路过时看见窗里还亮着灯。",
                "source_ref": "channel:unit:stage5",
                "occurred_at": demo.clock.iso(),
                "operation_key": "unit:stage5:message:1",
            })
            self.assertTrue(submitted.get("ok"), submitted)
            tick = demo.cycle.social_contacts.tick(now=demo.clock.iso())
            observed = next((row for row in tick.get("processed") or ()
                             if row.get("kind") == "LOCAL_MESSAGE"), {})
            self.assertEqual(str(observed.get("effect")), "OBSERVATION_ONLY", observed)
            self.assertIs(observed.get("pause_requested"), False)
            self.assertIs(observed.get("activity_started"), False)
            self.assertIn(str(observed.get("communication_availability")),
                          ("OPEN", "LIMITED", "BOUNDARY_DEFERRED"))
            after = demo_mod._activity(demo.runtime, private_ref)
            for key in ("status", "revision", "checkpoint_ref"):
                self.assertEqual(after.get(key), before.get(key), key)
            self.assertEqual(demo_mod._foreground(demo.runtime), private_ref)

            # (3) 负控：NO_ACTION 的邀请不 park；atomic 邀请不强行交接。
            declined_id = "inv:unit:stage5:declined"
            self.assertTrue(demo.cycle.contact_inbox.submit({
                "event_kind": "CO_PRESENCE_INVITATION",
                "invitation_id": declined_id,
                "scope": "随便问问",
                "source_ref": "channel:unit:stage5",
                "occurred_at": demo.clock.iso(),
                "duration_seconds": 7200,
                "requires_engagement": True,
                "operation_key": "unit:stage5:invite:declined",
            }).get("ok"))
            demo.cognition.invitation_ref = "co_presence:does-not-exist"
            demo.cognition.intent = "accept_invitation"
            declined = next((row for row in demo.cycle.social_contacts.tick(
                now=demo.clock.iso()).get("processed") or ()
                if row.get("invitation_id") == declined_id), {})
            self.assertEqual(str(declined.get("outcome")), "DECLINED", declined)
            untouched = demo_mod._activity(demo.runtime, private_ref)
            for key in ("status", "revision", "checkpoint_ref"):
                self.assertEqual(untouched.get(key), before.get(key), key)
            self.assertEqual(demo_mod._foreground(demo.runtime), private_ref)

            atomic_id = "inv:unit:stage5:atomic"
            self.assertTrue(demo.cycle.contact_inbox.submit({
                "event_kind": "CO_PRESENCE_INVITATION",
                "invitation_id": atomic_id,
                "scope": "强行插进来",
                "source_ref": "channel:unit:stage5",
                "occurred_at": demo.clock.iso(),
                "duration_seconds": 7200,
                "requires_engagement": True,
                "atomic": True,
                "operation_key": "unit:stage5:invite:atomic",
            }).get("ok"))
            demo.cognition.intent = "accept_invitation"
            demo.cognition.invitation_ref = "co_presence:%s" % atomic_id
            atomic = next((row for row in demo.cycle.social_contacts.tick(
                now=demo.clock.iso()).get("processed") or ()
                if row.get("invitation_id") == atomic_id), {})
            self.assertEqual(str(atomic.get("outcome")), "DECLINED", atomic)
            self.assertEqual(str(atomic.get("reason_code")), "FOREGROUND_ATOMIC_NOT_INTERRUPTIBLE",
                             atomic)
            self.assertEqual(demo_mod._activity(demo.runtime, private_ref).get("status"), "ACTIVE")

            # (4) 正式 typed 邀请：一次 Decision START → 原 Activity WAITING，joint ACTIVE。
            invitation_id = "inv:unit:stage5:accepted"
            self.assertTrue(demo.cycle.contact_inbox.submit({
                "event_kind": "CO_PRESENCE_INVITATION",
                "invitation_id": invitation_id,
                "scope": "一起待一会儿",
                "source_ref": "channel:unit:stage5",
                "occurred_at": demo.clock.iso(),
                "duration_seconds": 7200,
                "requires_engagement": True,
                "operation_key": "unit:stage5:invite:accepted",
            }).get("ok"))
            demo.cognition.invitation_ref = "co_presence:%s" % invitation_id
            demo.cognition.intent = "accept_invitation"
            accepted = next((row for row in demo.cycle.social_contacts.tick(
                now=demo.clock.iso()).get("processed") or ()
                if row.get("invitation_id") == invitation_id), {})
            self.assertEqual(str(accepted.get("outcome")), "ACCEPTED", accepted)
            joint_ref = str(accepted.get("joint_activity_ref") or "")
            self.assertTrue(joint_ref, accepted)
            private = demo_mod._activity(demo.runtime, private_ref)
            self.assertEqual(private.get("status"), "WAITING", private)
            conditions = dict((demo.runtime.waiting_coordinator.condition_store
                              .load_state() or {}).get("conditions") or {})
            condition = dict(conditions.get(str(private.get("resume_condition_ref"))) or {})
            self.assertTrue(str(condition.get("waiting_on_ref") or "")
                            .startswith("wait_dep:co_presence-"), condition)
            self.assertEqual(demo_mod._foreground(demo.runtime), joint_ref)
            self.assertEqual(demo_mod._activity(demo.runtime, joint_ref).get("status"), "ACTIVE")

            # (5) 显式 end：joint 由 LR-5 正常完成；LR-4 只开资格；原作仍 WAITING。
            self.assertTrue(demo.cycle.contact_inbox.submit({
                "event_kind": "CO_PRESENCE_END",
                "invitation_id": invitation_id,
                "occurred_at": demo.clock.iso(),
                "explicit_end": True,
                "reason": "EXPLICIT_END",
                "operation_key": "unit:stage5:end:1",
            }).get("ok"))
            ended = next((row for row in demo.cycle.social_contacts.tick(
                now=demo.clock.iso()).get("processed") or ()
                if row.get("kind") == "CO_PRESENCE_END"), {})
            self.assertEqual(str(ended.get("outcome")), "ENDED", ended)
            self.assertIs(ended.get("joint_completed"), True)
            self.assertTrue(ended.get("new_eligibility_refs"), ended)
            self.assertEqual(demo_mod._activity(demo.runtime, joint_ref).get("status"), "COMPLETED")
            self.assertEqual(demo_mod._activity(demo.runtime, private_ref).get("status"), "WAITING")
            eligibilities = dict((demo.runtime.waiting_coordinator.eligibility_store
                                  .load_state() or {}).get("eligibilities") or {})
            self.assertTrue(any(str(row.get("activity_id")) == private_ref
                                and str(row.get("status")) == "OPEN"
                                for row in eligibilities.values()), eligibilities)

            # (6) 下一轮新鲜 Decision RESUME 同一 Activity（不是 end 自动恢复）。
            # 这一轮发生在同一个拥挤的 home/living：World 机会很多，但 16 条上限内
            # RESUME 候选必须仍然可见（公平轮询的保底），而不是被 World 挤掉。
            demo.cognition.resume_activity = private_ref
            resumed = demo.round_until("resume")
            offer = (demo.cognition.offered[-1]["candidates"]
                     if demo.cognition.offered else [])
            world_view = list(getattr(demo.cycle, "last_world_opportunity_view", None) or [])
            self.assertGreaterEqual(len(world_view), 10,
                                    {"world_opportunities": len(world_view)})
            self.assertLessEqual(len(offer), 16, {"offer": offer})
            self.assertTrue(any("RESUME" in (row.get("legal") or []) for row in offer),
                            {"offer": offer, "world": len(world_view)})
            self.assertEqual(
                str(resumed.get("decision")), "RESUME",
                {"round": resumed,
                 "read": demo.cognition.reads[-1] if demo.cognition.reads else None,
                 "offer": demo.cognition.offered[-1] if demo.cognition.offered else None})
            back = demo_mod._activity(demo.runtime, private_ref)
            self.assertEqual(str(back.get("activity_id")), private_ref)
            self.assertIn(str(back.get("status")), ("ACTIVE", "COMPLETED"))
        finally:
            demo.close()


class ShareDraftIsNotAWorkTest(unittest.TestCase):
    """分享草稿住在同一个 owner 库里，但它不是可续写作品（B 自己的标题标记决定）。"""

    def test_share_title_is_not_a_continuable_work(self):
        import artifact_continuation_source as acs

        class Port:
            def available(self):
                return True

            def list_artifacts(self):
                return [
                    {"artifact_id": "artifact:book1", "title": "晚间小册子",
                     "lifecycle": "ACTIVE"},
                    {"artifact_id": "artifact:share1", "title": "[share] 一段分享",
                     "lifecycle": "ACTIVE"},
                ]

            def resume_point(self, artifact_id):
                return SimpleNamespace(artifact_id=artifact_id, title="x", resumable=True,
                                       head_version_id="artifact-version:v1",
                                       version_number=1, content="正文")

        source = acs.ArtifactContinuationSource(Port(), subject_id="test:share-title")
        items, _snapshot, _meta = source.discover(observed_at="2026-11-09T10:00:00+00:00")
        self.assertTrue(items, items)
        self.assertIn("artifact:book1", source.last_resume_points)
        self.assertNotIn("artifact:share1", source.last_resume_points)
        self.assertEqual(source.last_share_skip_reasons.get("artifact:share1"),
                         "SHARE_DRAFT_NOT_A_WORK")


class ActionIntentCopiesTest(unittest.TestCase):
    """两个 action_intent 副本在**真实导入顺序**下都认得这两个词，也都拒绝非法参数。"""

    def _probe(self, directory: Path) -> dict:
        completed = subprocess.run(
            [sys.executable, "-B", "-c", _IMPORT_PROBE, str(directory)],
            capture_output=True, text=True, cwd=str(ROOT), timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_service_and_bridge_copies_agree_on_the_narrow_contract(self):
        service_dir = ROOT / "components" / "world" / "service"
        bridge_dir = service_dir / "bridge"
        service = self._probe(service_dir)
        bridge = self._probe(bridge_dir)
        self.assertEqual(Path(service["file"]).resolve(), (service_dir / "action_intent.py")
                         .resolve())
        self.assertEqual(Path(bridge["file"]).resolve(), (bridge_dir / "action_intent.py")
                         .resolve())
        for name, row in (("service", service), ("bridge", bridge)):
            self.assertEqual(row["verbs"], ["ARRANGE", "TOGGLE_FIXTURE"], name)
            self.assertEqual(row["allowlist"], ["ARRANGE", "TOGGLE_FIXTURE"], name)
            self.assertTrue(row["arrange_ok"], name)
            self.assertTrue(row["toggle_ok"], name)
            self.assertEqual(row["rejects"], 7, name)
            self.assertEqual(row["submit_rejects"], 1, name)


def _fair_adapters():
    import copy

    import candidate_sources_ag0 as ag0

    observed = "2026-11-09T10:00:00+00:00"

    def record(source_kind, kind, candidate_id, *, source_ref, targets,
               activity_ref=None, resume_eligibility_ref=None,
               world_opportunity_ref=None):
        return ag0.CandidateRecord(
            candidate_id=candidate_id,
            candidate_kind=kind,
            source_kind=source_kind,
            source_ref=source_ref,
            source_refs=[source_ref],
            source_revision=1,
            activity_ref=activity_ref,
            resume_eligibility_ref=resume_eligibility_ref,
            world_opportunity_ref=world_opportunity_ref,
            perception_ref=("percept:fair:r1" if world_opportunity_ref else None),
            world_revision_ref=("wrev:fair:r1" if world_opportunity_ref else None),
            target_refs=list(targets),
            causal_parent_refs=[source_ref],
            provenance_refs=[source_ref, candidate_id],
            provenance_chain=[{"step": "candidate", "ref": candidate_id, "kind": kind},
                              {"step": "adapter", "ref": "adapter:%s" % source_kind}],
            availability_status=ag0.CANDIDATE_STATUS_OPEN,
            created_at=observed,
            observed_at=observed,
            recorded_at=observed,
            idempotency_key="idem_cand:fair:%s" % candidate_id,
        )

    class Adapter(ag0.CandidateSourceAdapter):
        def __init__(self, source_kind, canonical_owner, records):
            super().__init__()
            self.source_kind = source_kind
            self.canonical_owner = canonical_owner
            self.records = list(records)

        def is_enabled(self):
            return True

        def availability_status(self):
            return {"source_kind": self.source_kind, "status": ag0.ADAPTER_STATUS_READY}

        def materialize(self, *, observed_at):
            rows = [copy.deepcopy(item) for item in self.records]
            return rows, "snap:%s:r1" % self.source_kind, {
                "discovered": len(rows), "emitted": len(rows), "rejected": 0}

        def refresh(self, candidate, *, observed_at):
            return copy.deepcopy(candidate)

    world = [record(
        ag0.SOURCE_WORLD_OPPORTUNITY, ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
        "cand:w%02d" % index, source_ref="wopp:w%02d" % index,
        targets=["world_fact:stay:home:%02d" % index],
        world_opportunity_ref="wopp:w%02d" % index) for index in range(20)]
    personal = [record(
        ag0.SOURCE_PERSONAL_OPPORTUNITY,
        ag0.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
        "cand:p%02d" % index, source_ref="artifact:personal%02d" % index,
        targets=["artifact:personal%02d" % index]) for index in range(2)]
    resume = [record(
        ag0.SOURCE_RESUME_ELIGIBLE, ag0.CANDIDATE_KIND_RESUME_ACTIVITY,
        "cand:resume01", source_ref="actv:resume-activity",
        targets=["artifact:booklet"], activity_ref="actv:resume-activity",
        resume_eligibility_ref="relig:resume-1")]
    return Adapter, world, personal, resume


class FairGlobalCapTest(unittest.TestCase):
    """World 多产源不得把连续性/主体性来源挤出 AG-1 的 16 条上限。"""

    def _build(self, order):
        import candidate_sources_ag0 as ag0
        import agency_decision_ag1 as ag1

        Adapter, world, personal, resume = _fair_adapters()
        kinds = {}
        for row in world:
            kinds[row.candidate_id] = ag0.SOURCE_WORLD_OPPORTUNITY
        for row in personal:
            kinds[row.candidate_id] = ag0.SOURCE_PERSONAL_OPPORTUNITY
        for row in resume:
            kinds[row.candidate_id] = ag0.SOURCE_RESUME_ELIGIBLE
        adapters = {
            ag0.SOURCE_WORLD_OPPORTUNITY: Adapter(
                ag0.SOURCE_WORLD_OPPORTUNITY, "world_owner", order(world)),
            ag0.SOURCE_PERSONAL_OPPORTUNITY: Adapter(
                ag0.SOURCE_PERSONAL_OPPORTUNITY, "supply_owner", order(personal)),
            ag0.SOURCE_RESUME_ELIGIBLE: Adapter(
                ag0.SOURCE_RESUME_ELIGIBLE, "life_activity_authority", order(resume)),
        }
        capability = ag0.CandidateProjectionGuard.issue_capability(
            caller_module="ag0_test_harness")
        materializer = ag0.CandidateMaterializer(
            ag0.CandidateSourceRegistry(adapters), capability=capability,
            global_cap=ag1.MAX_COGNITIVE_CANDIDATES, record_audit=False)
        return materializer.build_candidate_set(
            observed_at="2026-11-09T10:00:00+00:00"), kinds

    def test_world_flood_keeps_resume_and_every_non_empty_source(self):
        import agency_decision_ag1 as ag1
        import candidate_sources_ag0 as ag0

        first, kinds = self._build(lambda rows: list(rows))
        self.assertEqual(ag1.MAX_COGNITIVE_CANDIDATES, 16)
        self.assertEqual(len(first.candidate_refs), 16)
        self.assertTrue(first.global_truncated)
        present = {kinds[ref] for ref in first.candidate_refs}
        self.assertIn(ag0.SOURCE_RESUME_ELIGIBLE, present)
        self.assertIn(ag0.SOURCE_PERSONAL_OPPORTUNITY, present)
        self.assertIn(ag0.SOURCE_WORLD_OPPORTUNITY, present)
        self.assertIn("cand:resume01", first.candidate_refs)
        self.assertTrue(any(ref in first.candidate_refs for ref in ("cand:p00", "cand:p01")))
        world_count = sum(1 for ref in first.candidate_refs
                          if kinds[ref] == ag0.SOURCE_WORLD_OPPORTUNITY)
        self.assertGreater(world_count, 10)

    def test_shuffled_raw_input_yields_the_same_bounded_set(self):
        first, _kinds = self._build(lambda rows: list(rows))
        order = (3, 9, 1, 14, 0, 12, 7, 19, 5, 16, 2, 11, 8, 18, 4, 15, 6, 13, 10, 17)
        second, _kinds2 = self._build(
            lambda rows: [rows[index] for index in order if index < len(rows)])
        self.assertEqual(first.candidate_refs, second.candidate_refs)
        self.assertEqual(first.candidate_set_id, second.candidate_set_id)

    def test_world_action_category_survives_the_round_cap(self):
        """世界里独一份的动作类别不得被候选哈希尾部整条截掉。

        复现父级 demo 晨起失败的条件：同一个 home 观察给出 stay×4 / pose(seated)×3 /
        arrange×2 / pick_up×1 / move×1 / toggle_fixture×1，再加 4 条 PERSONAL 与 1 条
        USER_REQUEST，池子 17 条、AG-1 上限 16。若世界桶只按 ``candidate_id`` 排，末尾的
        ``cand:w99``（唯一的 TOGGLE）会被截掉，晨起开灯就只剩 NO_ACTION；按类别先保一份、
        再按可替代次序让出多余份之后，它必须留下，被截掉的是某个多成员类别的第二份。
        """

        import copy

        import agency_decision_ag1 as ag1
        import candidate_sources_ag0 as ag0

        observed = "2026-11-09T10:00:00+00:00"

        def record(source_kind, kind, candidate_id, *, source_ref, targets,
                   user_event_ref=None):
            return ag0.CandidateRecord(
                candidate_id=candidate_id, candidate_kind=kind, source_kind=source_kind,
                source_ref=source_ref, source_refs=[source_ref], source_revision=1,
                user_event_ref=user_event_ref, target_refs=list(targets),
                perception_ref=("percept:cat:r1"
                                if source_kind == ag0.SOURCE_WORLD_OPPORTUNITY else None),
                world_revision_ref=("wrev:cat:r1"
                                    if source_kind == ag0.SOURCE_WORLD_OPPORTUNITY else None),
                world_opportunity_ref=(source_ref
                                       if source_kind == ag0.SOURCE_WORLD_OPPORTUNITY else None),
                causal_parent_refs=[source_ref], provenance_refs=[source_ref, candidate_id],
                provenance_chain=[{"step": "candidate", "ref": candidate_id, "kind": kind},
                                  {"step": "adapter", "ref": "adapter:%s" % source_kind}],
                availability_status=ag0.CANDIDATE_STATUS_OPEN,
                created_at=observed, observed_at=observed, recorded_at=observed,
                idempotency_key="idem_cand:cat:%s" % candidate_id)

        def world_row(candidate_id, target):
            return record(ag0.SOURCE_WORLD_OPPORTUNITY,
                          ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
                          candidate_id, source_ref="wopp:%s" % candidate_id[5:],
                          targets=[target])

        world = (
            [world_row("cand:w%02d" % index, "stay:home:READ:%d" % (600 + index))
             for index in range(4)]
            + [world_row("cand:w%02d" % index, "bodyact:pose:seated:south")
               for index in (4, 5, 6)]
            + [world_row("cand:w%02d" % index,
                         "dailyact:arrange:room:home:bedroom:item-lamp")
               for index in (7, 8)]
            + [world_row("cand:w09", "dailyact:pick_up:left_hand:home_notes07")]
            + [world_row("cand:w10", "place:street_1")]
            + [world_row("cand:w99", "dailyact:toggle_fixture:on:item-lamp")]
        )
        personal = [record(ag0.SOURCE_PERSONAL_OPPORTUNITY,
                           ag0.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
                           "cand:p%02d" % index, source_ref="artifact:personal%02d" % index,
                           targets=["artifact:personal%02d" % index]) for index in range(4)]
        request = [record(ag0.SOURCE_USER_REQUEST, ag0.CANDIDATE_KIND_RESPOND_USER_REQUEST,
                          "cand:u00", source_ref="ureq:invite-1",
                          targets=["co_presence:inv:1"], user_event_ref="ureq:invite-1")]

        class Adapter(ag0.CandidateSourceAdapter):
            def __init__(inner, source_kind, records):
                super().__init__()
                inner.source_kind = source_kind
                inner.records = list(records)

            def is_enabled(inner):
                return True

            def availability_status(inner):
                return {"source_kind": inner.source_kind, "status": ag0.ADAPTER_STATUS_READY}

            def materialize(inner, *, observed_at):
                rows = [copy.deepcopy(item) for item in inner.records]
                return rows, "snap:%s:r1" % inner.source_kind, {
                    "discovered": len(rows), "emitted": len(rows), "rejected": 0}

            def refresh(inner, candidate, *, observed_at):
                return copy.deepcopy(candidate)

        by_id = {row.candidate_id: row for row in world}

        def build(rows):
            adapters = {
                ag0.SOURCE_WORLD_OPPORTUNITY: Adapter(ag0.SOURCE_WORLD_OPPORTUNITY, rows),
                ag0.SOURCE_PERSONAL_OPPORTUNITY: Adapter(
                    ag0.SOURCE_PERSONAL_OPPORTUNITY, personal),
                ag0.SOURCE_USER_REQUEST: Adapter(ag0.SOURCE_USER_REQUEST, request),
            }
            capability = ag0.CandidateProjectionGuard.issue_capability(
                caller_module="ag0_test_harness")
            materializer = ag0.CandidateMaterializer(
                ag0.CandidateSourceRegistry(adapters), capability=capability,
                global_cap=ag1.MAX_COGNITIVE_CANDIDATES, record_audit=False)
            return materializer.build_candidate_set(observed_at=observed)

        first = build(list(world))
        self.assertEqual(len(first.candidate_refs), 16)
        self.assertTrue(first.global_truncated)
        # 独一份的动作类别必须留下：那盏灯本轮唯一的 TOGGLE 候选。
        self.assertIn("cand:w99", first.candidate_refs)

        kept_world = [by_id[ref] for ref in first.candidate_refs if ref in by_id]
        kept_categories = {ag0.candidate_action_category(row) for row in kept_world}
        offered_categories = {ag0.candidate_action_category(row) for row in world}
        self.assertEqual(offered_categories, kept_categories)
        self.assertEqual(len(kept_world), 11)
        dropped = [row for row in world if row.candidate_id not in first.candidate_refs]
        self.assertEqual(len(dropped), 1)
        dropped_category = ag0.candidate_action_category(dropped[0])
        # 被截掉的是多成员类别里的第二份，而不是某个类别的唯一代表。
        self.assertGreater(
            sum(1 for row in world
                if ag0.candidate_action_category(row) == dropped_category), 1)

        shuffled = [world[index] for index in (5, 11, 0, 7, 2, 9, 4, 10, 1, 8, 3, 6)]
        second = build(shuffled)
        self.assertEqual(first.candidate_refs, second.candidate_refs)
        self.assertEqual(first.candidate_set_id, second.candidate_set_id)


class SleepLikeAdmissionTest(unittest.TestCase):
    """sleep_like 只按 owner 的合同接纳：start 可，stop 不可；完成判据核会话模式。"""

    def _copies(self):
        service = _load("ai_service_sleep", ROOT / "components" / "world" / "service"
                        / "action_intent.py")
        bridge = _load("ai_bridge_sleep", ROOT / "components" / "world" / "service"
                       / "bridge" / "action_intent.py")
        return (("service", service), ("bridge", bridge))

    def test_both_action_intent_copies_admit_start_sleep_like_and_refuse_stop(self):
        for name, ai in self._copies():
            self.assertEqual(tuple(ai.REST_MODES), ("rest", "sleep_like"), name)
            self.assertEqual(ai.validate_verb_params(
                "REST", {"state": "start", "mode": "sleep_like"}),
                {"state": "start", "mode": "sleep_like"}, name)
            self.assertEqual(ai.validate_verb_params(
                "REST", {"state": "stop", "mode": "rest"}),
                {"state": "stop", "mode": "rest"}, name)
            for bad in (
                    {"state": "stop", "mode": "sleep_like"},
                    {"state": "start"},
                    {"state": "start", "mode": "rest", "extra": 1},
                    {"state": "pause", "mode": "rest"},
                    {"state": "start", "mode": "nap"}):
                with self.assertRaises(ai.ActionIntentError, msg=(name, bad)):
                    ai.validate_verb_params("REST", bad)

    def test_body_encoding_round_trips_sleep_and_refuses_stop(self):
        import life_body_action_targets as body

        encoded = body.encode_rest_target("start", "sleep_like")
        self.assertEqual(encoded, "bodyact:rest:start:sleep_like")
        parsed = body.parse_body_action_target(encoded)
        self.assertEqual(parsed["params"], {"state": "start", "mode": "sleep_like"})
        self.assertEqual(parsed["expected_value"], "world_fact:body:recovery:active")
        with self.assertRaises(body.BodyActionTargetError):
            body.encode_rest_target("stop", "sleep_like")
        with self.assertRaises(body.BodyActionTargetError):
            body.parse_body_action_target("bodyact:rest:stop:sleep_like")
        with self.assertRaises(body.BodyActionTargetError):
            body.parse_body_action_target("bodyact:rest:start:nap")
        with self.assertRaises(body.BodyActionTargetError):
            body.parse_body_action_target("bodyact:rest:start")

    def test_completion_fact_requires_the_sleep_mode_not_just_an_active_rest(self):
        import life_body_action_targets as body

        parsed = body.parse_body_action_target("bodyact:rest:start:sleep_like")
        sleep = body.body_action_observed_fact(
            parsed, {}, rest_availability={"current": {
                "recovery_state": "active", "recovery_mode": "sleep_like"}})
        plain = body.body_action_observed_fact(
            parsed, {}, rest_availability={"current": {
                "recovery_state": "active", "recovery_mode": "rest"}})
        unknown = body.body_action_observed_fact(
            parsed, {}, rest_availability={"current": {"recovery_state": "active"}})
        self.assertTrue(sleep["satisfied"])
        self.assertFalse(plain["satisfied"])
        self.assertFalse(unknown["satisfied"])

    def test_opportunity_emitter_only_adds_sleep_when_both_owner_verdicts_hold(self):
        observation = {"location": {"place_id": "home"}, "world_revision": 3,
                       "world_id": "w", "pose": "lying", "orientation": "south"}
        base = {"verb": "REST", "available": True, "start_available": True,
                "stop_available": False, "sleep_like_available": False}
        without = wmos.observed_body_action_opportunities(
            observation, available_actions={"actions": [dict(base)]},
            occurred_at="2026-11-09T10:00:00+00:00",
            observed_at="2026-11-09T10:00:00+00:00")
        with_sleep = wmos.observed_body_action_opportunities(
            observation,
            available_actions={"actions": [dict(base, sleep_like_available=True)]},
            occurred_at="2026-11-09T10:00:00+00:00",
            observed_at="2026-11-09T10:00:00+00:00")
        self.assertIn("bodyact:rest:start:rest",
                      [ref for row in without for ref in row["target_refs"]])
        self.assertNotIn("bodyact:rest:start:sleep_like",
                         [ref for row in without for ref in row["target_refs"]])
        refs = [ref for row in with_sleep for ref in row["target_refs"]]
        self.assertIn("bodyact:rest:start:rest", refs)
        self.assertIn("bodyact:rest:start:sleep_like", refs)


    def test_real_resolver_refuses_sleep_without_lying_or_surface(self):
        """负控直接问真实 Resolver（不造 Activity）：坐姿/无床面分别给出具名拒绝。

        这里对 substrate 的 pose / surface 是**负控专用的合成前置**，只用于让真实
        Resolver 走到两条世界检查；主正链的疲劳/躺姿/床面全部来自真实活动与时序。
        """

        demo_mod = _load("v07_sleep_neg_demo", ROOT / "scripts" / "v07_daily_life_demo.py")
        demo = demo_mod.Demo()
        try:
            demo.build()
            import world_body_substrate as wbs

            store = wbs.SubstrateStore(wbs.substrate_path(str(demo.world_home)))
            resolver = demo.gateway.service.resolver

            def _set_pose(pose):
                state = store.load()
                state["pose"] = pose
                store.save(state, expected_revision=int(state["revision"]))

            def _resolve(tag):
                return resolver.resolve({
                    "execution_id": "exec:neg:%s" % tag,
                    "action": "REST",
                    "authority": "verified_main_self_tool",
                    "confirm_apply": True,
                    "params": {"state": "start", "mode": "sleep_like"},
                    "observed_dependencies": resolver._dependencies()})

            _set_pose("standing")
            standing = _resolve("standing")
            self.assertEqual(standing.get("status"), "rejected", standing)
            self.assertIn("REST_REQUIRES_RESTING_POSE",
                          str(standing.get("legacy_reason_code")), standing)

            _set_pose("seated")
            seated = _resolve("seated")
            self.assertEqual(seated.get("status"), "rejected", seated)
            self.assertIn("SLEEP_LIKE_REQUIRES_LYING_POSE",
                          str(seated.get("legacy_reason_code")), seated)

            state = store.load()
            state["pose"] = "lying"
            state["placements"] = {
                key: value for key, value in (state.get("placements") or {}).items()
                if str((value or {}).get("kind")) != "surface"}
            store.save(state, expected_revision=int(state["revision"]))
            no_surface = _resolve("surface")
            self.assertEqual(no_surface.get("status"), "rejected", no_surface)
            self.assertIn("SLEEP_LIKE_REQUIRES_REST_SURFACE",
                          str(no_surface.get("legacy_reason_code")), no_surface)
        finally:
            demo.close()


class LowBudgetBookmarkTest(unittest.TestCase):
    """预算耗尽时零发送、保留 checkpoint/未完成状态；同目录重开不补额。"""

    def test_exhausted_request_budget_refuses_before_the_adapter_is_called(self):
        import tempfile

        import life_request_budget as lrb

        root = Path(tempfile.mkdtemp(prefix="v07-budget-", dir="/tmp"))
        checkpoint = root / "checkpoint.json"
        checkpoint.write_text('{"activity":"unfinished","checkpoint_ref":"cp:1"}',
                              encoding="utf-8")
        before = checkpoint.read_bytes()

        budget = lrb.LifeRequestBudget(root, utc_day_max=1, run_window_max=1,
                                       run_id="run:v07-budget")
        budget.ensure_window(allow_new=True)
        budget.reserve("decision")

        class _Adapter:
            def __init__(self):
                self.calls = 0

            def invoke(self, request, *, schema_retry_index=0):
                self.calls += 1
                return {}

        inner = _Adapter()
        wrapped = lrb.BudgetedCognitionAdapter(inner, budget)
        with self.assertRaises(lrb.RequestBudgetError):
            wrapped.invoke(object())
        self.assertEqual(inner.calls, 0)
        self.assertEqual(checkpoint.read_bytes(), before)
        view = budget.view()
        self.assertEqual(view["utc_day_calls"], 1)
        self.assertFalse(budget.check()["allowed"])

        reopened = lrb.LifeRequestBudget(root, utc_day_max=1, run_window_max=1,
                                         run_id="run:v07-budget")
        reopened.ensure_window(allow_new=False)
        self.assertFalse(reopened.check()["allowed"])
        self.assertEqual(reopened.view()["utc_day_calls"], view["utc_day_calls"])
        self.assertEqual(reopened.view()["run_window_calls"],
                         view["run_window_calls"])


class StarterReadingLibraryTest(unittest.TestCase):
    """第七阶段 B：主题 + seed 的确定性起始分页读物，以及 prepare 的选择优先级。"""

    def setUp(self):
        import starter_reading_library as srl

        self.srl = srl

    def test_same_combination_is_stable_and_different_combinartions_differ(self):
        for theme in self.srl.THEMES:
            first = self.srl.select_starter_reading(929711, theme)
            again = self.srl.select_starter_reading(929711, theme)
            self.assertEqual(first["title"], again["title"])
            self.assertEqual(first["content"], again["content"])
            self.assertGreaterEqual(first["pages"], 2)
            self.assertEqual(first["content"].count("%%PAGE%%"), first["pages"] - 1)
            self.assertTrue(first["content"].strip())
        # 跨主题不同；同主题抽样不同 seed 也有差异（有限库允许碰撞，不冒充一一对应）。
        coastal = self.srl.select_starter_reading(7, "coastal_town")
        mountain = self.srl.select_starter_reading(7, "mountain_town")
        self.assertNotEqual(coastal["content"], mountain["content"])
        sampled = {self.srl.select_starter_reading(seed, "rural_farming")["entry_id"]
                   for seed in range(40)}
        self.assertGreater(len(sampled), 1, "同一主题下 seed 变化没有产生不同读物")

    def test_prepare_reads_explicit_text_first_then_the_opt_in_library(self):
        import one_day_life as odl

        # 显式文本（包括空字符串）永远是最优先的，且不会被 starter 覆盖。
        explicit = odl.resolve_reading_material(
            reading_content="第一页：我自己的材料。\n%%PAGE%%\n第二页。",
            reading_title="我的材料", starter_reading=True, seed=929711,
            theme="coastal_town")
        self.assertEqual(explicit["title"], "我的材料")
        self.assertIn("我自己的材料", explicit["content"])
        self.assertIsNone(explicit["starter"])
        empty = odl.resolve_reading_material(
            reading_content="", reading_title="空材料", starter_reading=True,
            seed=929711, theme="coastal_town")
        self.assertEqual(empty["content"], "")
        self.assertIsNone(empty["starter"])
        # 没有 opt-in 时维持旧默认：没有阅读材料。
        old_default = odl.resolve_reading_material(
            reading_content=None, reading_title="", starter_reading=False,
            seed=929711, theme="coastal_town")
        self.assertIsNone(old_default["content"])
        # 显式 opt-in 且未给文本时才选主题读物。
        starter = odl.resolve_reading_material(
            reading_content=None, reading_title="", starter_reading=True,
            seed=929711, theme="mountain_town")
        self.assertIsNotNone(starter["content"])
        self.assertIsNotNone(starter["starter"])
        self.assertEqual(starter["title"], starter["starter"]["title"])
        self.assertGreaterEqual(starter["starter"]["pages"], 2)


class NewFurnitureOwnerTest(unittest.TestCase):
    """第七阶段 B：暖光落地灯与窗边小桌是真正能买、能布置、能用的家居。"""

    def test_warm_floor_lamp_and_window_side_table_work_through_the_owners(self):
        import world_body_substrate as wbs
        import world_property_economy as economy
        import world_room_environment as wre

        demo_mod = _load("v07_demo_new_furniture_under_test",
                         ROOT / "scripts" / "v07_daily_life_demo.py")
        demo = demo_mod.Demo()
        try:
            demo.build()
            catalog_ids = {item["catalog_item_id"] for item in economy.CATALOG}
            self.assertIn("warm_floor_lamp", catalog_ids)
            self.assertIn("window_side_table", catalog_ids)
            # 两件都是可 ARRANGE 的家具；灯被声明为可通电光源，桌提供实例级桌面。
            self.assertTrue(economy.catalog_declaration("warm_floor_lamp")["relocatable"])
            self.assertEqual(economy.catalog_declaration("warm_floor_lamp")["light_band"],
                             "moderate")
            self.assertTrue(wre.declaration_of_catalog("warm_floor_lamp")["powerable"])
            self.assertEqual(tuple(economy.catalog_declaration("window_side_table")["surfaces"]),
                             ("desk_surface",))
            # 桌子不被强迫通电：它根本不是声明的光源。
            self.assertIsNone(wre.declaration_of_catalog("window_side_table"))

            with mock.patch.object(economy, "_store_check",
                                   return_value={"status": "available"}):
                lamp = economy.purchase_item(
                    catalog_item_id="warm_floor_lamp",
                    purchase_intent_id="idem:v07:new:lamp", main_self_verified=True,
                    hermes_home=str(demo.world_home), environment=demo.env,
                    now=demo.clock.now)
                table = economy.purchase_item(
                    catalog_item_id="window_side_table",
                    purchase_intent_id="idem:v07:new:table", main_self_verified=True,
                    hermes_home=str(demo.world_home), environment=demo.env,
                    now=demo.clock.now)
            for bought, expected_price in ((lamp, 1_200), (table, 1_600)):
                self.assertEqual(bought["status"], "applied", bought)
                self.assertEqual(bought["price"], expected_price, bought)
            owned = {row["item_instance_id"]: row["disposition"]
                     for row in economy.load_state(demo.world_home)["items"]}
            for bought in (lamp, table):
                self.assertEqual(owned.get(bought["item_instance_id"]), "OWNED", owned)
            lamp_id = str(lamp["item_instance_id"])
            table_id = str(table["item_instance_id"])

            demo.gateway.service.tick()
            placements = demo_mod._substrate_placements(demo.world_home)
            for item_id in (lamp_id, table_id):
                row = placements[item_id]
                self.assertEqual(str(row.get("kind")), "room")
                self.assertEqual(str(row.get("location_id")), "home")

            state = json.loads((demo.world_home / "data" / "world" / "world_state.json")
                               .read_text(encoding="utf-8"))
            area = str((state.get("location") or {}).get("area_id"))
            counter = {"seq": 0}

            def act(action, params):
                counter["seq"] += 1
                return demo.gateway.service.act({
                    "action": action, "execution_id": "test:new-furniture:%d" % counter["seq"],
                    "actor_id": "chiyo", "authority": "verified_main_self_tool",
                    "confirm_apply": True, "observed_dependencies": {}, "params": params})

            for item_id in (lamp_id, table_id):
                if str(placements[item_id].get("area_id")) != area:
                    moved = act("ARRANGE", {"object_id": item_id, "target": {
                        "kind": "room", "location_id": "home", "area_id": area}})
                    self.assertEqual(moved.get("status"), "applied", moved)

            toggled = act("TOGGLE_FIXTURE", {"object_id": lamp_id, "state": "on"})
            self.assertEqual(toggled.get("status"), "applied", toggled)
            light = wre.light_for_home(str(demo.world_home))
            self.assertEqual(light["status"], "available", light)
            self.assertEqual(light["power"], "on", light)
            self.assertEqual(light["band"], "moderate", light)
            # Body 侧的合法环境输入确实由这盏亮着的灯产生。
            self.assertEqual(wre.environment_input(light),
                             {"class": "ENVIRONMENT", "sensory_intensity": 0.45})

            books = [row for row in (demo.world_prepared.get("takeable") or [])
                     if str(row.get("location_id")) == "home"]
            self.assertTrue(books, demo.world_prepared)
            book = str(books[0]["object_id"])
            picked = act("PICK_UP", {"object_id": book, "slot_id": "left_hand"})
            self.assertEqual(picked.get("status"), "applied", picked)
            target_surface = "desk_surface:%s" % table_id
            placed = act("PLACE", {"object_id": book,
                                   "target": {"kind": "surface", "location_id": "home",
                                              "surface_id": target_surface}})
            self.assertEqual(placed.get("status"), "applied", placed)
            live = wbs.SubstrateStore(wbs.substrate_path(demo.world_home)).load()
            row = live["placements"][book]
            self.assertEqual(str(row.get("kind")), "surface")
            self.assertEqual(str(row.get("surface_id")), target_surface)
            # 台面派生的 owner 就是这张自己买下的桌子，不是全局同名面。
            surfaces = wbs.surfaces_here(live, {table_id: "window_side_table"},
                                         location_id="home", area_id=area)
            self.assertTrue(any(item["surface_id"] == target_surface for item in surfaces),
                            surfaces)
        finally:
            demo.close()


class AmbientWorldDateRolloverTest(unittest.TestCase):
    """第七阶段 C：环境事件按世界本地日期确定性换日；作者固定的有限事件不重生。"""

    def setUp(self):
        import world_ambient_events as wae
        import world_runtime_data as wrd

        self.wae = wae
        self.wrd = wrd
        self.wrd.reset_cache()
        self.addCleanup(self.wrd.reset_cache)

    def _generated_package_env(self):
        import tempfile

        from components.worldgen import schema
        from components.worldgen.generator import generate_world

        directory = Path(tempfile.mkdtemp(prefix="v07-ambient-", dir="/tmp"))
        schema.write_package(directory, generate_world(seed=929711, theme="coastal_town"))
        self.wrd.reset_cache()
        return {"CHIYO_WORLD_PACKAGE": str(directory),
                "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "1"}

    def test_generated_single_day_events_roll_over_by_world_local_date(self):
        from zoneinfo import ZoneInfo

        env = self._generated_package_env()
        day_one = date(2026, 11, 10)
        day_two = date(2026, 11, 11)
        first = self.wae.materialize_ambient_events(day_one, environment=env)
        second = self.wae.materialize_ambient_events(day_two, environment=env)
        self.assertTrue(first)
        self.assertTrue(second)
        for row in first:
            self.assertEqual(row["local_date"], day_one.isoformat())
            self.assertEqual(row["provenance"], "world_local_day_deterministic_v1")
        for row in second:
            self.assertEqual(row["local_date"], day_two.isoformat())
        ids_one = {row["event_id"] for row in first}
        ids_two = {row["event_id"] for row in second}
        self.assertFalse(ids_one & ids_two, ids_one & ids_two)
        # 业务时刻读口返回的是当天集合的子集（不是按观察日另造新身份）。
        zone = ZoneInfo("Asia/Tokyo")
        active = self.wae.active_ambient_events(
            day_one, datetime(2026, 11, 10, 20, 0, tzinfo=zone), environment=env)
        self.assertTrue(active)
        self.assertTrue({row["event_id"] for row in active} <= ids_one)

    def test_author_finite_event_changes_phase_then_expires_without_rebirth(self):
        env = {"CHIYO_WORLD_PACKAGE": str(ROOT / "examples" / "worlds"
                                          / "daily_neighborhood_v1"),
               "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "1"}
        self.wrd.reset_cache()
        catalog = self.wae.ambient_template_catalog(env)
        finite = [row for row in catalog["templates"] if row.get("finite")]
        self.assertTrue(finite)
        event = finite[0]
        identity = "ambient-finite-%s" % event["identity"]
        phases = event["phases"]
        self.assertGreaterEqual(len(phases), 2)
        day_a = date.fromisoformat(phases[0]["local_date"])
        day_b = date.fromisoformat(phases[1]["local_date"])
        ends_on = date.fromisoformat(event["ends_on"])

        def find(day):
            for row in self.wae.materialize_ambient_events(day, environment=env):
                if row["event_id"] == identity:
                    return row
            return None

        row_a = find(day_a)
        row_b = find(day_b)
        self.assertIsNotNone(row_a, "作者有限事件在起始阶段日没有出现")
        self.assertIsNotNone(row_b)
        self.assertEqual(row_a["event_id"], row_b["event_id"])  # 固定身份，跨日不变
        self.assertNotEqual(row_a["payload"]["summary"], row_b["payload"]["summary"])  # 换 phase
        self.assertIsNone(find(ends_on + timedelta(days=1)), "期限后同一事件仍然重生")
        for offset in (8, 16):
            self.assertIsNone(find(ends_on + timedelta(days=offset)))


class Stage8OwnDayPhoneNeedsTest(unittest.TestCase):
    """0.7 第八阶段：真实一条链覆盖今日自选/义务、同作上下文、手机只读、线上点心体感。"""

    @staticmethod
    def _demo_module():
        return _load("v07_stage8_demo_under_test",
                     ROOT / "scripts" / "v07_daily_life_demo.py")

    def test_stage8_real_chain_completes(self):
        # 这条链自带逐项真断言：plan_today 幂等、义务经 operator import → 真实 AR-0 续航 →
        # 持久 Action/Decision/owner 回执投影 DONE → 后续候选 OBLIGATION_ALREADY_DONE 扣留、
        # 同作上下文四类真来源（版本/来历/确证结果/做法/本地草稿）、PhoneShopping 购买重放、
        # CONSUME/TOILET 体感档位下降、手机 arrangements/timeline 真读入口骨架且读前后零写。
        # 先钉住 bridge 版 action_intent：测试顺序可能已把 service 版（没有 TOILET 世界动词）留在
        # sys.modules，真实的 runtime 只用 bridge 版，这里按路径重新绑定，不靠测试顺序决定结果。
        _pin_bridge_action_intent()
        demo_mod = self._demo_module()
        self.assertEqual(demo_mod._run_stage8_chain(), 0)

    def test_needs_relief_requires_an_explicit_world_declaration(self):
        import body_lived_needs as bln

        # 缺声明（或没有 lived_need_effects）→ 没有缓解；这就是旧的「CONSUME 不造营养」规则。
        self.assertEqual(bln.declaration_effects({}), {})
        self.assertEqual(bln.declaration_effects(
            {"consumable": True, "lived_need_effects": None}), {})
        self.assertEqual(bln.declaration_effects(
            {"lived_need_effects": {"hunger": 0.65}}), {"hunger": 0.65})

    def test_plan_today_domain_is_idempotent(self):
        import tempfile

        import life_day_plan as ldp

        with tempfile.TemporaryDirectory() as tmp:
            store = ldp.DayPlan(Path(tmp), subject_id="chiyo")
            day = "2026-11-12"
            item = {"item_ref": "fitem:stage8", "status": "OPEN", "title": "补半页",
                    "due_at": "2026-11-12T12:00:00+00:00", "subject_id": "chiyo"}
            now = "2026-11-12T03:00:00+00:00"  # +08:00 业务日 = 2026-11-12
            first = store.plan(item, date=day, decision_ref="decision:1", now=now)
            same = store.plan(item, date=day, decision_ref="decision:1", now=now)
            other = store.plan(item, date=day, decision_ref="decision:2", now=now)
            self.assertTrue(first.get("created"))
            self.assertFalse(same.get("created"))
            # 新 Decision 同 item 同天不重复建（计划是「排进今天」，不是流水）。
            self.assertFalse(other.get("created"))
            self.assertEqual(len(store.for_date(day)), 1)


class Stage9ResearchPrivateMediaClaimsPracticeTest(unittest.TestCase):
    """0.7 第九阶段：同 root 研究闭环、私人日记相册六域时间线、S03 与共同实践真来源。"""

    @staticmethod
    def _demo_module():
        return _load("v07_stage9_demo_under_test",
                     ROOT / "scripts" / "v07_daily_life_demo.py")

    def test_stage9_real_chain_completes(self):
        # 真链自断言：默认装配的研究 seam 恢复后由 RESEARCH_FROM_QUESTION popt 建 marker 研究、
        # AR-0 写版本、CONTINUE 保留 marker/refs 且两 context 实收；零命中≠NOT_WIRED；新资料
        # 换版负控；同实例日记/相册导入、相册不进项目列表/续写候选（真实 refresh 撤回）；
        # 六域时间线 operator/subject 两视角零私文零写；S03 四类状态进正式 Decision 且 claims
        # 窗口内 World/Memory 文件零变；共同实践四类真来源落账、四类伪造来源具名拒绝。
        _pin_bridge_action_intent()
        demo_mod = self._demo_module()
        demo = demo_mod._run_stage8_chain(keep_demo=True)
        try:
            research_id = demo_mod._run_stage9_chain(demo=demo)
            self.assertIsInstance(research_id, str)
            self.assertTrue(research_id.startswith("artifact:"), research_id)
        finally:
            demo.close()

    def test_album_classifier_does_not_claim_unrelated_json(self):
        import json as _json

        from components.phone.album import is_album_manifest

        self.assertFalse(is_album_manifest(""))
        self.assertFalse(is_album_manifest("not json"))
        self.assertFalse(is_album_manifest(_json.dumps({"other": 1})))
        self.assertFalse(is_album_manifest(_json.dumps({"kind": "album.image.v1"})))
        self.assertFalse(is_album_manifest(_json.dumps({
            "kind": "album.image.v1", "caption": "x", "content_type": "image/png",
            "media": {"name": "a.png", "sha256": "0" * 64},
            "source": {"origin": "user_import"}})))  # declared 缺失/不是 self_declared
        self.assertTrue(is_album_manifest(_json.dumps({
            "kind": "album.image.v1", "caption": "x", "content_type": "image/png",
            "media": {"name": "a.png", "sha256": "0" * 64},
            "source": {"origin": "user_import", "declared": "self_declared"}})))


class Stage10ExpressionLossForgetReviewTest(unittest.TestCase):
    """0.7 第十阶段：有限处境表达/忙时回话、损失承接、遗忘屏障与只读恢复检视。"""

    @staticmethod
    def _demo_module():
        return _load("v07_stage10_demo_under_test",
                     ROOT / "scripts" / "v07_daily_life_demo.py")

    def test_stage10_real_chain_completes(self):
        # 真链自断言：三列处境（真实 Body 读/本实例预算/明确观察）与 INVALID 越权见闻零写；
        # 真实 ACTIVE+ATOMIC 前台下的普通来话被读成 BOUNDARY_DEFERRED/no_pause，回复归档
        # life_local_contact、同 key 重放不二调用/二句，超长/空/零预算具名拒绝；真实 owner
        # 退休做法后 AG-1 拒旧 handle 且不提交 Decision，合法下一轮继续；复用第九阶段同一
        # research_id 由 owner 归档并保留版本、已归档目标不换目标不续写、归档来源 send 被
        # SOURCE_ARCHIVED 三件零变；真实
        # ForgetCaseStore 屏障先行、目标公开读口 NOT_FOUND、主经历仍可读、保留副本 →
        # WAITING_RETENTION；runtime_recovery_review.review 纯读且 backup/migration NOT_IMPLEMENTED。
        _pin_bridge_action_intent()
        demo_mod = self._demo_module()
        demo = demo_mod._run_stage8_chain(keep_demo=True)
        try:
            research_id = demo_mod._run_stage9_chain(demo=demo)
            self.assertTrue(str(research_id).startswith("artifact:"), research_id)
            self.assertEqual(demo_mod._run_stage10_chain(
                demo=demo, research_id=research_id), 0)
        finally:
            demo.close()

    def test_stage10_archive_sidework_survives_crowded_offers(self):
        # 拥挤实例回归（诚实目标）：前序同 root 已有多个真实作品（含回顾、研究、分享草稿）
        # 时，第十阶段**不**要求所有作品都进同一轮，也不替换任何候选来源。它只验证：
        # 真实 World/个人来源仍在（表达轮确实同时拿到 World 与个人候选）；归档负控使用
        # 第九阶段真正写出的那个 research_id 完成 prepare/archive/拒续/拒送；主小册子
        # 不被换成别作。
        import candidate_sources_ag0 as ag0

        _pin_bridge_action_intent()
        demo_mod = self._demo_module()
        demo = demo_mod._run_stage8_chain(keep_demo=True)
        try:
            research_id = demo_mod._run_stage9_chain(demo=demo)
            self.assertTrue(str(research_id).startswith("artifact:"), research_id)
            bare_work = str(getattr(demo.cycle, "work_artifact_id", "")
                            or demo.data.get("work_artifact_id") or "")
            booklet_ref = "artifact:%s" % demo_mod._bare(bare_work)
            # 再让 owner 用正常回顾路径写出一份真实回顾，制造“前序已有回顾”的拥挤实例。
            demo.cognition.project_bare = ""
            review_round = demo.round_until("review")
            created_review = dict(review_round.get("review_created") or {})
            review_ref = str(created_review.get("artifact_id") or "")
            self.assertEqual(str(created_review.get("kind")), "review", review_round)
            self.assertTrue(review_ref)
            # 回顾 Activity 先由真实 ABANDON 释放前台，再进入第十阶段。
            demo.cognition.project_bare = demo_mod._bare(review_ref)
            demo_mod._stage10_round(demo, "abandon", attempts=4)

            self.assertEqual(demo_mod._run_stage10_chain(
                demo=demo, booklet=booklet_ref, booklet_bare=demo_mod._bare(bare_work),
                research_id=research_id), 0)

            trace = dict(getattr(demo, "stage10_trace", {}) or {})
            self.assertEqual(trace.get("research_id"), research_id)

            # 复用的是第九阶段这份真实研究，版本已有正式写入；归档不是删除。
            state = demo_mod._stage10_artifact_state(demo.port, research_id)
            self.assertEqual(state.get("lifecycle"), "ARCHIVED")
            self.assertGreaterEqual(int(state.get("version_number") or 0), 2)
            self.assertTrue(str(state.get("content") or "").startswith(
                "<!-- nyairo-research:v1"))
            self.assertEqual(trace.get("archived_lifecycle"), "ARCHIVED")
            self.assertEqual(trace.get("send_refusal_reason"), "SOURCE_ARCHIVED")

            # 真实来源仍在：注册表里每个 admitted 来源都注册了，World 适配器没被替换，
            # 表达轮同时拿到了真实 World 与个人候选。
            matrix = demo.runtime.candidate_registry.get_availability_matrix()
            self.assertEqual(set(matrix), set(ag0.VALID_CANDIDATE_SOURCE_KINDS))
            self.assertNotIn("ADAPTER_NOT_REGISTERED",
                             {str(row.get("reason_code")) for row in matrix.values()})
            self.assertTrue(trace.get("world_adapter_preserved"), trace)
            self.assertGreater(int(trace.get("expression_world_candidates") or 0), 0, trace)
            self.assertGreater(int(trace.get("expression_personal_candidates") or 0), 0, trace)

            # 主小册子没有被归档路径换成别作；归档/拒续/拒送前后 head 一致。
            booklet_after = demo_mod._stage10_artifact_state(demo.port, booklet_ref)
            self.assertNotEqual(booklet_ref, research_id)
            self.assertEqual(booklet_after.get("lifecycle"), "ACTIVE")
            self.assertEqual(trace.get("booklet_head_before_archive"),
                             trace.get("booklet_head_after_send"))
            self.assertEqual(booklet_after.get("head_version_id"),
                             trace.get("booklet_head_after_send"))
        finally:
            demo.close()

    def test_situation_three_columns_keep_authority_and_validity(self):
        import tempfile

        import life_situation_view as lsv

        home = tempfile.mkdtemp(prefix="stage10-situation-home-")
        now = "2026-11-10T04:00:00+00:00"
        observations = [
            {"observation_ref": "observation:test:current",
             "source_ref": "operator:test:note", "observed_at": now,
             "valid_until": "2026-11-10T05:00:00+00:00"},
            {"observation_ref": "observation:test:unverified",
             "source_ref": "operator:test:note", "observed_at": now},
            {"observation_ref": "observation:test:expired",
             "source_ref": "operator:test:note",
             "observed_at": "2026-11-10T01:00:00+00:00",
             "valid_until": "2026-11-10T02:00:00+00:00"},
            {"observation_ref": "observation:test:leak",
             "source_ref": "operator:test:note", "observed_at": now,
             "valid_until": "2026-11-10T05:00:00+00:00", "mood": "calm"},
        ]
        view = lsv.read_life_situation(
            home=home, now=now,
            budget={"source": "life_request_budget.check", "allowed": True,
                    "state": "ok", "utc_day": "2026-11-10", "utc_day_calls": 1,
                    "path": "/tmp/life_request_budget.json"},
            observations=observations)
        self.assertEqual(view["writes_performed"], 0)
        self.assertEqual(set(view["dimensions"]),
                         {lsv.DIMENSION_BODY, lsv.DIMENSION_RESOURCE_BUDGET})
        self.assertEqual(view["body"]["state"], "UNKNOWN")
        self.assertEqual(view["body"]["reason_code"], lsv.REASON_BODY_SOURCE_NOT_INJECTED)
        self.assertEqual(view["resource_budget"]["state"], "AVAILABLE")
        labels = {row["observation_ref"]: row for row in view["knowledge"]["observations"]}
        self.assertEqual(labels["observation:test:current"]["validity"], lsv.VALIDITY_CURRENT)
        self.assertEqual(labels["observation:test:unverified"]["validity"],
                         lsv.VALIDITY_UNVERIFIED)
        self.assertEqual(labels["observation:test:expired"]["validity"], lsv.VALIDITY_EXPIRED)
        self.assertEqual(labels["observation:test:leak"]["validity"], lsv.VALIDITY_INVALID)
        self.assertEqual(labels["observation:test:leak"]["reason_code"],
                         lsv.REASON_OBSERVATION_AUTHORITY_FIELD)
        kwargs = lsv.ag1_capacity_kwargs(view)
        self.assertEqual(set(kwargs), {"body_capacity_state", "body_capacity_ref",
                                       "resource_availability_state", "resource_budget_ref"})


class WorldMoveBusinessClockDomainTest(unittest.TestCase):
    """真实 MOVE 的出发/到达必须和 demo 注入的业务钟同域，不跟 host 墙钟跨日。

    旧现场：World 服务没有显式 clock，Resolver 用 host 墙钟给旅行记 ``started_at`` /
    ``expected_arrival_at``；demo 的 ``indoor_move`` 却拿业务钟相减，一次一分钟的室内移动
    被读成"还剩 257 分钟"，业务日被推过午夜。这里把 host 墙钟显式模拟成业务日之后的
    JST 凌晨（只改服务自己的 now 时间源，不 sleep、不改系统时钟），再走 real owner MOVE。
    """

    def test_real_move_stays_on_the_fixed_business_day_past_host_midnight(self):
        import world_living_skeleton as wls
        import world_travel_delivery as wtd

        jst = timezone(timedelta(hours=9))
        demo_mod = _load("v07_daily_demo_clock_domain_under_test",
                         ROOT / "scripts" / "v07_daily_life_demo.py")
        demo = demo_mod.Demo(start_at=datetime(2026, 11, 10, 11, 0, tzinfo=jst))
        try:
            demo.build()
            # 固定业务钟走到同一天的 20:00（仍是 2026-11-10）。
            demo.clock.advance(9 * 3600, because="固定业务日晚间")
            before = demo.clock.now
            # host 墙钟被显式摆在业务日之后的 JST 00:20。这里的 owner tick 只服务 Body/
            # 到期结算，与"一次 MOVE 的时域"无关；冻结它，避免模拟的 host 时刻顺带推进
            # Body（测试不需要 sleep，也不改系统时钟）。
            demo.gateway.service.tick = lambda: None
            demo.gateway.service._now = (
                datetime(2026, 11, 11, 0, 20, tzinfo=jst).astimezone(timezone.utc))
            before_travels = (wtd.load_state(demo.world_home, demo.env) or {}).get("travels") or []
            before_ids = {str(row.get("travel_id")) for row in before_travels}
            current = wls.read_move_state(hermes_home=str(demo.world_home),
                                          environment=demo.env, now=demo.clock.now)
            here = str((current or {}).get("world_location_id") or "")
            target = "home/bedroom" if here != "home/bedroom" else "home/living"
            reached = demo.indoor_move(target)
            self.assertIs(reached.get("ok"), True, reached)
            self.assertIn(str(reached.get("status")), ("arrived", "already_there"), reached)
            self.assertEqual(demo.clock.now.astimezone(jst).date(), date(2026, 11, 10),
                             {"now": demo.clock.now.isoformat(), "move": reached})
            self.assertLess((demo.clock.now - before).total_seconds(), 3600, reached)
            # 真实 owner 的旅行记录也落在业务时域，不是 host 的次日凌晨；到达由 owner
            # 自己的 duration/due 结算，不是瞬移。
            travels = (wtd.load_state(demo.world_home, demo.env) or {}).get("travels") or []
            created = [row for row in travels
                       if str(row.get("travel_id")) not in before_ids]
            self.assertTrue(created, travels)
            record = created[-1]
            self.assertEqual(str(record.get("status")), "ARRIVED", record)
            for key in ("started_at", "expected_arrival_at"):
                moment = datetime.fromisoformat(str(record[key]).replace("Z", "+00:00"))
                self.assertEqual(moment.astimezone(jst).date(), date(2026, 11, 10),
                                 {key: record[key], "record": record})
        finally:
            demo.close()

    @staticmethod
    def _assert_injected_clock(testcase, demo, host_next_day, jst):
        """Both the first service and a same-root reopen must read the injected clock."""

        service = demo.gateway.service
        # The host wall-clock fallback is explicitly put past JST midnight; the injected
        # business clock must still win everywhere it drives time.
        service._now = host_next_day
        testcase.assertIsNotNone(service._clock,
                                 "World service was constructed without the business clock")
        testcase.assertEqual(service._moment(), demo.clock.now)
        testcase.assertEqual(service.resolver._clock(), demo.clock.now)
        adapter = demo.runtime.world_action_adapter
        testcase.assertIsNotNone(adapter.resolver._clock,
                                 "life journey resolver was not put on the business clock")
        testcase.assertEqual(adapter.resolver._clock(), demo.clock.now)
        testcase.assertEqual(demo.clock.now.astimezone(jst).date(), date(2026, 11, 10))

    @staticmethod
    def _assert_formal_move(testcase, demo, demo_mod, place):
        """One real MOVE departs first, then lands only when the business clock reaches due."""

        before = demo.clock.now
        demo.cognition.place_wish = place
        out = demo.round("go_to")
        testcase.assertEqual(str(out.get("world_verb")), "MOVE", out)
        testcase.assertEqual(str(out.get("outcome")), "IN_TRANSIT", out)
        journey = dict(out.get("journey") or {})
        due = datetime.fromisoformat(
            str(journey.get("expected_arrival_at")).replace("Z", "+00:00"))
        testcase.assertGreater(due, demo.clock.now, {"now": demo.clock.now, "due": due})
        testcase.assertGreater((due - before).total_seconds(), 0.0, journey)
        settled = demo_mod._settle_move(demo, out, place=place)
        testcase.assertTrue(settled.get("arrived"), settled)
        testcase.assertGreaterEqual(demo.clock.now, due)

    def test_first_service_and_reopen_keep_the_clock_past_host_midnight(self):
        """固定业务晚间（2026-11-10 19:00），host JST 已跨日；两段真实 MOVE 不提前落地。

        首次 ``WorldBodyService`` 构造与同 root 冷重开都必须把**同一个**注入业务钟交给
        service / Resolver / Life 旅程引擎（host fallback now 只作为对照被摆到次日凌晨）。
        """

        jst = timezone(timedelta(hours=9))
        host_next_day = datetime(2026, 11, 11, 0, 20, tzinfo=jst).astimezone(timezone.utc)
        demo_mod = _load("v07_daily_demo_midnight_reopen_under_test",
                         ROOT / "scripts" / "v07_daily_life_demo.py")
        demo = demo_mod.Demo(start_at=datetime(2026, 11, 10, 19, 0, tzinfo=jst))
        try:
            demo.build()
            self._assert_injected_clock(self, demo, host_next_day, jst)
            self._assert_formal_move(self, demo, demo_mod, "street_1")
            self._assert_formal_move(self, demo, demo_mod, "street_2")
            reopened = demo.reopen_existing()
            self.assertTrue(reopened.get("gateway_replaced"), reopened)
            self.assertTrue(reopened.get("runtime_replaced"), reopened)
            self._assert_injected_clock(self, demo, host_next_day, jst)
            self._assert_formal_move(self, demo, demo_mod, "street_3")
        finally:
            demo.close()


if __name__ == "__main__":
    unittest.main()

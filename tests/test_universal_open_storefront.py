#!/usr/bin/env python3
"""Universal open storefront & three-layer naming acceptance tests.

Verifies:
1. Zero-write extraction of sellable items from legacy world-package shop intros
   and fixture notes without backend product hard-coding;
2. Deterministic WorldGen ``content.json["goods"]`` across all 4 themes while keeping
   ``package_summary`` / topology / NPCs / objects invariant;
3. World Editor custom shop rooms (``sale_spec`` custom prices + blank shop fallback)
   and strict cross-store refusal;
4. Full real embodied chain: ``WorldMoveOpportunityProvider`` observation ->
   ``purchase_item`` -> instant shop handover & ``materialize_owned_item`` ->
   ``consistency_report`` -> ``WorldResolver`` ``CONSUME`` (with ``lived_need_effects``)
   and ``PICK_UP``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WORLD_SCRIPTS = ROOT / "components" / "world" / "scripts"
WORLD_SERVICE = ROOT / "components" / "world" / "service"
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
for _entry in (str(ROOT), str(WORLD_SCRIPTS), str(WORLD_SERVICE), str(LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.worldgen import schema as wg_schema  # noqa: E402
from components.worldgen.generator import AVAILABLE_THEMES, generate_world  # noqa: E402
from components.worldgen.validator import validate as validate_worldgen  # noqa: E402

import body_lived_needs as bln  # noqa: E402
import body_runtime as br  # noqa: E402
import world_action_resolver as resolver_mod  # noqa: E402
import world_body_binding as binding  # noqa: E402
import world_body_gateway_api as gateway_api  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_body_substrate as substrate  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402
import world_open_storefront as storefront  # noqa: E402
import world_phone_shopping as phone_shopping  # noqa: E402
import world_property_economy as economy  # noqa: E402
import world_travel_delivery as travel  # noqa: E402

OPEN_NOW = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)  # 12:00 +08:00


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_runtime(
    home: Path,
    *,
    world_id: str,
    place: str,
    area: str,
    package_dir: Path | None = None,
    hunger: float = 0.80,
    thirst: float = 0.80,
) -> dict[str, str]:
    (home / "data" / "world").mkdir(parents=True, exist_ok=True)
    (home / "data" / "body").mkdir(parents=True, exist_ok=True)
    (home / "state").mkdir(parents=True, exist_ok=True)
    (home / "run").mkdir(parents=True, exist_ok=True)

    _write_json(
        home / "data" / "world" / "world_state.json",
        {
            "schema_version": "world.foundation.v0",
            "revision": 3,
            "world_id": world_id,
            "timezone": "Asia/Tokyo",
            "location": {"place_id": place, "area_id": area},
            "scene": {"revision": 3, "scene_id": f"{place}.{area}"},
            "facts": [],
        },
    )
    _write_json(
        home / "data" / "world" / "world_timeline.json",
        {
            "schema_version": "world.timeline.v1",
            "revision": 1,
            "world_id": world_id,
            "processes": [],
        },
    )
    (home / "data" / "life_execution.jsonl").write_text("", encoding="utf-8")
    _write_json(
        home / "state" / "world_object_capabilities.json",
        {
            "schema_version": substrate.CAPABILITIES_SCHEMA,
            "objects": {},
            "consumables": {},
        },
    )
    sub = substrate.initial_substrate(world_id=world_id, body_id="chiyo_body")
    substrate.SubstrateStore(substrate.substrate_path(home)).save(sub)

    env: dict[str, str] = {
        economy.FEATURE_ENV: "1",
        economy.WORLD_FEATURE_ENV: "1",
        economy.DAILY_FEATURE_ENV: "1",
        economy.DELIVERY_FEATURE_ENV: "1",
        substrate.FEATURE_ENV: "1",
    }
    if package_dir is not None:
        env["CHIYO_WORLD_PACKAGE"] = str(package_dir)

    economy.initialize_genesis(hermes_home=home, environment=env)

    # Enable lived_needs profile cleanly through official runtime store
    body_dir = home / "data" / "body"
    profile = bln.build_profile(
        hunger=hunger,
        thirst=thirst,
        toilet=0.10,
        note="test_storefront_profile",
    )
    bln.save_profile(body_dir, profile)
    body_store = br.BodyStore(body_dir)
    body_state = br.validate_runtime(br.initial_runtime(at=OPEN_NOW - timedelta(minutes=1)))
    body_state = br.upgrade_to_v2(body_state, bln.initial_state(profile))
    body_store.save_runtime(body_state)
    return env


class UniversalStorefrontTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nyairo-open-store-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_legacy_package_extracts_shop_goods_without_goods_field(self) -> None:
        docs = generate_world(seed=929711, theme="coastal_town")
        # Simulate an older packaged world that has intros & fixture objects, but no content.goods
        for entry in docs["content.json"]["entries"].values():
            if isinstance(entry, dict):
                entry.pop("goods", None)
        docs["content.json"]["entries"]["cafe"]["intro"] = (
            "带着临海露台的小咖啡馆，提供手冲咖啡与简单的三明治，店内的老式音响终日放着轻音乐。"
        )
        docs["content.json"]["entries"]["bookstore"]["intro"] = (
            "巷口有些年头的旧书店，书架间隙狭窄，深处收纳着不少地方志以及带着旧书香气的文库本。"
        )
        docs["content.json"]["entries"]["fish_market"]["intro"] = (
            "镇上最早苏醒的地方，当地渔船卸下的海产在此售卖，空气里弥漫着海盐、冰块与鲜鱼的味道。"
        )
        docs["content.json"]["entries"]["home_goods_store"]["intro"] = (
            "摆满日常器皿与五金件的杂货铺，门口摆着塑料水桶与草帽，满足镇上人居家的琐碎需求。"
        )
        docs["objects.json"]["objects"].extend(
            [
                {
                    "object_id": "conv_counter",
                    "name": "收银台",
                    "kind": "table",
                    "location_id": "convenience_store",
                    "note": "摆着关东煮的收银台。",
                },
                {
                    "object_id": "conv_cooler",
                    "name": "冷藏柜",
                    "kind": "dispenser",
                    "location_id": "convenience_store",
                    "note": "嗡嗡作响的冷藏饮料柜。",
                },
                {
                    "object_id": "conv_mag",
                    "name": "杂志架",
                    "kind": "book",
                    "location_id": "convenience_store",
                    "note": "收银台边的杂志。",
                },
                {
                    "object_id": "fish_stall",
                    "name": "摊位1",
                    "kind": "table",
                    "location_id": "fish_market",
                    "note": "摆满当季蔬果的摊位。",
                },
            ]
        )

        pkg_dir = self.tmp / "legacy_pkg"
        wg_schema.write_package(pkg_dir, docs)
        home = self.tmp / "legacy_home"
        env = _seed_runtime(home, world_id="coastal_town_929711", place="home", area="bedroom", package_dir=pkg_dir)

        sources = storefront.resolve_world_sources(home, env)
        self.assertIsNotNone(sources)
        store_ids = set(storefront.open_store_ids(sources))
        self.assertTrue({"cafe", "bookstore", "convenience_store", "fish_market", "home_goods_store"}.issubset(store_ids))
        self.assertNotIn("home", store_ids)
        self.assertNotIn("school", store_ids)

        def _names(loc_id: str) -> list[str]:
            return [row["name"] for row in storefront.store_shelf_for_location(loc_id, sources=sources)]

        self.assertIn("手冲咖啡", _names("cafe"))
        self.assertIn("三明治", _names("cafe"))
        self.assertNotIn("轻音乐", _names("cafe"))
        self.assertIn("关东煮", _names("convenience_store"))
        self.assertIn("冷藏饮料", _names("convenience_store"))
        self.assertIn("杂志", _names("convenience_store"))
        self.assertIn("地方志", _names("bookstore"))
        self.assertIn("文库本", _names("bookstore"))
        self.assertNotIn("手写价目", _names("bookstore"))
        self.assertIn("海产", _names("fish_market"))
        self.assertIn("当季蔬果", _names("fish_market"))
        self.assertIn("塑料水桶", _names("home_goods_store"))
        self.assertIn("草帽", _names("home_goods_store"))
        self.assertNotIn("杂货铺", _names("home_goods_store"))

    def test_02_worldgen_generates_goods_for_all_themes_keeping_summary_stable(self) -> None:
        summary = wg_schema.package_summary(generate_world(seed=929711, theme="coastal_town"))
        self.assertEqual(summary["world_id"], "coastal_town_929711")
        self.assertEqual(summary["locations"], 19)
        self.assertEqual(summary["edges"], 18)
        self.assertEqual(summary["npcs"], 3)
        self.assertEqual(summary["objects"], 3)

        for theme in AVAILABLE_THEMES:
            docs = generate_world(seed=929711, theme=theme)
            self.assertEqual(validate_worldgen(docs), [], f"{theme} validation failed")
            for loc in docs["topology.json"]["locations"]:
                if loc["kind"] in storefront.COMMERCIAL_LOCATION_KINDS:
                    goods = docs["content.json"]["entries"][loc["location_id"]].get("goods")
                    self.assertTrue(
                        isinstance(goods, list) and len(goods) >= 2,
                        f"{theme}:{loc['location_id']} missing generated goods: {goods!r}",
                    )

    def test_03_world_editor_custom_shop_sale_spec_and_cross_store_refusal(self) -> None:
        home = self.tmp / "editor_home"
        env = _seed_runtime(home, world_id="shiomi_city", place="custom_sweet_shop", area="counter")
        _write_json(
            home / "world" / "nyairo-world.json",
            {
                "format": "nyairo.world-package.v3",
                "requires": "nyairo.editor-runtime.v3",
                "meta": {"id": "custom_city", "name": "自建街区"},
                "rooms": [
                    {"id": "home", "name": "小家", "kind": "room", "hours": None},
                    {"id": "custom_sweet_shop", "name": "街角甜品屋", "kind": "shop", "hours": {"open": 9, "close": 21}},
                    {"id": "custom_tea_bar", "name": "巷口茶室", "kind": "shop", "hours": None},
                ],
                "connections": [],
                "objects": [
                    {
                        "id": "item_daifuku",
                        "name": "草莓大福",
                        "description": "当天现包的软糯点心",
                        "kind": "book",
                        "room_id": "custom_sweet_shop",
                        "portable": True,
                        "sale_spec": {"category": "food", "price_tier": "budget", "price": 350},
                    }
                ],
            },
        )

        proj = economy.store_shop_projection(hermes_home=home, environment=env, now=OPEN_NOW)
        self.assertEqual(proj["status"], "available", proj)
        self.assertEqual(proj["store_id"], "custom_sweet_shop")
        names = {row["name"]: row for row in proj["catalog"]}
        self.assertIn("草莓大福", names)
        daifuku = names["草莓大福"]
        self.assertEqual(daifuku["price"], 350)
        self.assertEqual(daifuku["usable_kind"], "CARRY")

        # Move to blank custom shop (no objects): contextual tea drinks appear automatically
        _write_json(
            home / "data" / "world" / "world_state.json",
            {
                "schema_version": "world.foundation.v0",
                "revision": 4,
                "world_id": "shiomi_city",
                "timezone": "+08:00",
                "location": {"place_id": "custom_tea_bar", "area_id": "counter"},
                "scene": {"revision": 4, "scene_id": "custom_tea_bar.counter"},
                "facts": [],
            },
        )
        tea_proj = economy.store_shop_projection(hermes_home=home, environment=env, now=OPEN_NOW)
        self.assertEqual(tea_proj["status"], "available", tea_proj)
        self.assertTrue(any("茶" in row["name"] or "饮" in row["name"] for row in tea_proj["catalog"]))

        # Cross-store purchase attempt must fail without charging the wallet
        refused = economy.store_purchase_item(
            catalog_item_id=daifuku["catalog_item_id"],
            operation_key="op-cross-shop",
            local_owner={"source": economy.LOCAL_OWNER_SOURCE, "uid": os.getuid(), "subject": economy.OWNER, "home": str(home)},
            hermes_home=home,
            environment=env,
            now=OPEN_NOW,
        )
        self.assertEqual(refused["status"], "rejected")
        self.assertEqual(refused["reason_code"], "ITEM_NOT_SOLD_AT_STORE")
        self.assertEqual(economy.wallet_projection(hermes_home=home, environment=env)["current_balance"], economy.MOVE_IN_GRANT)

    def test_04_end_to_end_observe_buy_materialize_consume_and_pick_up(self) -> None:
        docs = generate_world(seed=929711, theme="coastal_town")
        # Ensure convenience_store exposes both food and drink in content.goods
        docs["content.json"]["entries"]["convenience_store"]["goods"] = [
            "热关东煮|food|standard|暖呼呼的便利店关东煮",
            "冰柠檬汽水|drink|budget|冰柜里冒凉气的汽水",
        ]
        docs["content.json"]["entries"]["bookstore"]["goods"] = [
            "地方志随笔|reading|standard|夹着旧书签的地方小志",
        ]
        pkg_dir = self.tmp / "pkg_e2e"
        wg_schema.write_package(pkg_dir, docs)
        home = self.tmp / "e2e_home"
        env = _seed_runtime(
            home,
            world_id=docs["world.json"]["world_id"],
            place="convenience_store",
            area="convenience_store",
            package_dir=pkg_dir,
            hunger=0.80,
            thirst=0.80,
        )
        # Keep travel/world env aligned with runtime package
        env[travel.TRAVEL_FEATURE_ENV] = "1"

        # Build real service & gateway API directly to observe snapshot & labels
        config = SimpleNamespace(
            world_home=home,
            environment=env,
            scripts_dir=WORLD_SCRIPTS,
            include_test_objects=False,
        )
        with patch.dict(os.environ, env, clear=False):
            shelf = economy.store_shop_projection(hermes_home=home, environment=env, now=OPEN_NOW)
            self.assertEqual(shelf["status"], "available", shelf)
            self.assertTrue(shelf["purchase_available"])
            by_name = {row["name"]: row for row in shelf["catalog"]}
            self.assertIn("热关东煮", by_name)
            self.assertIn("冰柠檬汽水", by_name)

            # Verify observed_purchase_opportunities sees both items at the store
            obs_mock = {
                "world_id": docs["world.json"]["world_id"],
                "world_revision": 3,
                "location": {"place_id": "convenience_store", "area_id": "convenience_store"},
            }
            opps = wmos.observed_purchase_opportunities(
                obs_mock,
                projection=shelf,
                occurred_at=OPEN_NOW.isoformat(),
                observed_at=OPEN_NOW.isoformat(),
            )
            targets = {o["target_refs"][0] for o in opps}
            self.assertIn("buy:" + by_name["热关东煮"]["catalog_item_id"], targets)
            self.assertIn("buy:" + by_name["冰柠檬汽水"]["catalog_item_id"], targets)

            # Purchase both drink and food through economy.purchase_item (even with delivery timer enabled!)
            drink_res = economy.purchase_item(
                catalog_item_id=by_name["冰柠檬汽水"]["catalog_item_id"],
                purchase_intent_id="op-e2e-drink",
                main_self_verified=True,
                hermes_home=home,
                environment=env,
                now=OPEN_NOW,
            )
            self.assertEqual(drink_res["status"], "applied", drink_res)
            self.assertEqual(drink_res["delivery_status"], economy.DELIVERY_STATUS_DELIVERED)
            self.assertEqual(drink_res["delivery_location"], "convenience_store/convenience_store")

            food_res = economy.purchase_item(
                catalog_item_id=by_name["热关东煮"]["catalog_item_id"],
                purchase_intent_id="op-e2e-food",
                main_self_verified=True,
                hermes_home=home,
                environment=env,
                now=OPEN_NOW,
            )
            self.assertEqual(food_res["status"], "applied", food_res)

            # Materialize right at convenience_store (same call autonomous_life_cycle & tick make)
            m_drink = phone_shopping.materialize_owned_item(
                hermes_home=home, item_instance_id=drink_res["item_instance_id"], environment=env
            )
            m_food = phone_shopping.materialize_owned_item(
                hermes_home=home, item_instance_id=food_res["item_instance_id"], environment=env
            )
            self.assertEqual(m_drink["status"], "materialized", m_drink)
            self.assertEqual(m_food["status"], "materialized", m_food)

            surface = gateway_api.GatewaySurface(SimpleNamespace(config=config))
            labels = surface._labels()
            self.assertEqual(labels[drink_res["item_instance_id"]], "冰柠檬汽水")
            self.assertEqual(labels[food_res["item_instance_id"]], "热关东煮")

            report = binding.consistency_report(home)
            self.assertTrue(report["consistent"], report)

            # Consume drink & food through the real WorldResolver -> lived_need_effects apply
            resolver = resolver_mod.WorldResolver(home, environment=env, now=lambda: OPEN_NOW)
            consume_drink = resolver.resolve(
                {
                    "action": "CONSUME",
                    "execution_id": "exec-consume-drink-1",
                    "actor_id": "chiyo",
                    "body_id": "chiyo_body",
                    "authority": resolver_mod.AUTHORITY_VERIFIED_MAIN_SELF,
                    "confirm_apply": True,
                    "observed_dependencies": resolver._dependencies(),
                    "params": {"object_id": drink_res["item_instance_id"]},
                }
            )
            self.assertEqual(consume_drink["status"], "applied", consume_drink)
            self.assertGreater(consume_drink["lived_need_effects"]["thirst"], 0.4)

            consume_food = resolver.resolve(
                {
                    "action": "CONSUME",
                    "execution_id": "exec-consume-food-1",
                    "actor_id": "chiyo",
                    "body_id": "chiyo_body",
                    "authority": resolver_mod.AUTHORITY_VERIFIED_MAIN_SELF,
                    "confirm_apply": True,
                    "observed_dependencies": resolver._dependencies(),
                    "params": {"object_id": food_res["item_instance_id"]},
                }
            )
            self.assertEqual(consume_food["status"], "applied", consume_food)
            self.assertGreater(consume_food["lived_need_effects"]["hunger"], 0.6)

            # Move to bookstore, buy a book, materialize, and pick up into left_hand
            _write_json(
                home / "data" / "world" / "world_state.json",
                {
                    "schema_version": "world.foundation.v0",
                    "revision": 4,
                    "world_id": docs["world.json"]["world_id"],
                    "timezone": "Asia/Tokyo",
                    "location": {"place_id": "bookstore", "area_id": "bookstore"},
                    "scene": {"revision": 4, "scene_id": "bookstore.bookstore"},
                    "facts": [],
                },
            )
            book_shelf = economy.store_shop_projection(hermes_home=home, environment=env, now=OPEN_NOW)
            book_entry = {r["name"]: r for r in book_shelf["catalog"]}["地方志随笔"]
            book_res = economy.purchase_item(
                catalog_item_id=book_entry["catalog_item_id"],
                purchase_intent_id="op-e2e-book",
                main_self_verified=True,
                hermes_home=home,
                environment=env,
                now=OPEN_NOW,
            )
            self.assertEqual(book_res["status"], "applied", book_res)
            phone_shopping.materialize_owned_item(
                hermes_home=home, item_instance_id=book_res["item_instance_id"], environment=env
            )
            pick = resolver.resolve(
                {
                    "action": "PICK_UP",
                    "execution_id": "exec-pick-book-1",
                    "actor_id": "chiyo",
                    "body_id": "chiyo_body",
                    "authority": resolver_mod.AUTHORITY_VERIFIED_MAIN_SELF,
                    "confirm_apply": True,
                    "observed_dependencies": resolver._dependencies(),
                    "params": {"object_id": book_res["item_instance_id"], "slot_id": "left_hand"},
                }
            )
            self.assertEqual(pick["status"], "applied", pick)

    def test_05_open_counter_slots_tier_ranges_and_arbitrary_item_naming(self) -> None:
        # 1. Programmer-authored theme item supplement tables are completely removed.
        self.assertFalse(hasattr(storefront, "_THEME_SUPPLEMENTS_BY_KIND"))

        docs = generate_world(seed=929711, theme="coastal_town")
        pkg_dir = self.tmp / "pkg_slots"
        wg_schema.write_package(pkg_dir, docs)
        home = self.tmp / "slots_home"
        env = _seed_runtime(
            home,
            world_id=docs["world.json"]["world_id"],
            place="convenience_store",
            area="convenience_store",
            package_dir=pkg_dir,
            hunger=0.80,
            thirst=0.80,
        )
        env[travel.TRAVEL_FEATURE_ENV] = "1"
        config = SimpleNamespace(
            world_home=home,
            environment=env,
            scripts_dir=WORLD_SCRIPTS,
            include_test_objects=False,
        )

        with patch.dict(os.environ, env, clear=False):
            sources = storefront.resolve_world_sources(home, env)
            self.assertIsNotNone(sources)
            for shop_id in ("convenience_store", "cafe", "bookstore", "fish_market", "home_goods_store"):
                shelf = storefront.store_shelf_for_location(shop_id, sources=sources)
                open_slots = [row for row in shelf if row.get("open_order") and str(row["catalog_item_id"]).startswith("shop_slot:")]
                self.assertTrue(open_slots, f"{shop_id} missing shop_slot:* open counter entries")
                for row in open_slots:
                    self.assertLess(row["price_min"], row["price_max"], row)

            proj = economy.store_shop_projection(hermes_home=home, environment=env, now=OPEN_NOW)
            self.assertEqual(proj["status"], "available", proj)
            drink_budget = next(
                row for row in proj["catalog"]
                if row["catalog_item_id"] == "shop_slot:convenience_store:drink:budget"
            )
            lo, hi = drink_budget["price_min"], drink_budget["price_max"]
            self.assertEqual((lo, hi), storefront.price_range_for("drink", "budget"))

            # Buy 3 arbitrary drinks on the SAME open slot -> distinct deterministic prices within [lo, hi]
            names = ("冰镇麦茶", "草莓牛奶", "柠檬苏打")
            bought_rows = []
            for idx, name in enumerate(names):
                res = economy.purchase_item(
                    catalog_item_id=drink_budget["catalog_item_id"],
                    purchase_intent_id=f"op-slot-drink-{idx}",
                    main_self_verified=True,
                    hermes_home=home,
                    environment=env,
                    now=OPEN_NOW,
                    custom_item_name=name,
                )
                self.assertEqual(res["status"], "applied", res)
                self.assertEqual(res["item_name"], name)
                self.assertGreaterEqual(res["price"], lo)
                self.assertLessEqual(res["price"], hi)
                bought_rows.append(res)

            self.assertGreater(len({r["price"] for r in bought_rows}), 1, bought_rows)

            # Idempotent replay with same item_name succeeds; swapped item_name conflicts
            replay = economy.purchase_item(
                catalog_item_id=drink_budget["catalog_item_id"],
                purchase_intent_id="op-slot-drink-0",
                main_self_verified=True,
                hermes_home=home,
                environment=env,
                now=OPEN_NOW,
                custom_item_name="冰镇麦茶",
            )
            self.assertEqual(replay["status"], "already_applied", replay)
            self.assertEqual(replay["price"], bought_rows[0]["price"])

            conflict = economy.purchase_item(
                catalog_item_id=drink_budget["catalog_item_id"],
                purchase_intent_id="op-slot-drink-0",
                main_self_verified=True,
                hermes_home=home,
                environment=env,
                now=OPEN_NOW,
                custom_item_name="热可可",
            )
            self.assertEqual(conflict["status"], "rejected")
            self.assertEqual(conflict["reason_code"], "PURCHASE_INTENT_CONFLICT")

            # Verify estimated_price honors exact value in range and clamps out-of-range attempts
            for idx, (est, expected_price) in enumerate(((180, 180), (1, lo), (99999, hi))):
                clamped = economy.purchase_item(
                    catalog_item_id=drink_budget["catalog_item_id"],
                    purchase_intent_id=f"op-slot-clamp-{idx}",
                    main_self_verified=True,
                    hermes_home=home,
                    environment=env,
                    now=OPEN_NOW,
                    custom_item_name="海风柠檬茶",
                    estimated_price=est,
                )
                self.assertEqual(clamped["status"], "applied", clamped)
                self.assertEqual(clamped["price"], expected_price)

            # Materialize, inspect label & inventory, and consume -> hunger/thirst effect applies
            milk_res = bought_rows[1]
            mat = phone_shopping.materialize_owned_item(
                hermes_home=home, item_instance_id=milk_res["item_instance_id"], environment=env
            )
            self.assertEqual(mat["status"], "materialized", mat)
            surface = gateway_api.GatewaySurface(SimpleNamespace(config=config))
            self.assertEqual(surface._labels()[milk_res["item_instance_id"]], "草莓牛奶")

            ps = phone_shopping.PhoneShopping(
                home, env, local_owner_uid=os.getuid() if hasattr(os, "getuid") else 0,
                clock=lambda: OPEN_NOW,
            )
            inv = ps.inventory()
            self.assertTrue(inv["ok"], inv)
            milk_inv = next(r for r in inv["data"]["items"] if r["item_instance_id"] == milk_res["item_instance_id"])
            self.assertEqual(milk_inv["name"], "草莓牛奶")

            resolver = resolver_mod.WorldResolver(home, environment=env, now=lambda: OPEN_NOW)
            consume = resolver.resolve(
                {
                    "action": "CONSUME",
                    "execution_id": "exec-slot-drink-1",
                    "actor_id": "chiyo",
                    "body_id": "chiyo_body",
                    "authority": resolver_mod.AUTHORITY_VERIFIED_MAIN_SELF,
                    "confirm_apply": True,
                    "observed_dependencies": resolver._dependencies(),
                    "params": {"object_id": milk_res["item_instance_id"]},
                }
            )
            self.assertEqual(consume["status"], "applied", consume)
            self.assertGreater(consume["lived_need_effects"]["thirst"], 0.4)

    def test_06_category_guard_and_poetic_book_titles(self) -> None:
        docs = generate_world(seed=929711, theme="coastal_town")
        pkg_dir = self.tmp / "pkg_guard"
        wg_schema.write_package(pkg_dir, docs)
        home = self.tmp / "guard_home"
        env = _seed_runtime(
            home,
            world_id=docs["world.json"]["world_id"],
            place="bookstore",
            area="bookstore",
            package_dir=pkg_dir,
        )

        with patch.dict(os.environ, env, clear=False):
            wallet_before = economy.wallet_projection(hermes_home=home, environment=env)["current_balance"]
            for idx, bad_name in enumerate(("竹荚鱼", "冰镇啤酒", "落地灯")):
                refused = economy.purchase_item(
                    catalog_item_id="shop_slot:bookstore:reading:budget",
                    purchase_intent_id=f"op-bad-book-{idx}",
                    main_self_verified=True,
                    hermes_home=home,
                    environment=env,
                    now=OPEN_NOW,
                    custom_item_name=bad_name,
                )
                self.assertEqual(refused["status"], "rejected", (bad_name, refused))
                self.assertEqual(refused["reason_code"], "ITEM_CATEGORY_MISMATCH", (bad_name, refused))
            self.assertEqual(
                economy.wallet_projection(hermes_home=home, environment=env)["current_balance"],
                wallet_before,
            )

            # Poetic book titles ending in book head nouns succeed cleanly on reading slots
            for idx, book_title in enumerate(("鱼市风物记", "街角咖啡馆随笔集", "海风诗集")):
                ok_book = economy.purchase_item(
                    catalog_item_id="shop_slot:bookstore:reading:budget",
                    purchase_intent_id=f"op-good-book-{idx}",
                    main_self_verified=True,
                    hermes_home=home,
                    environment=env,
                    now=OPEN_NOW,
                    custom_item_name=book_title,
                )
                self.assertEqual(ok_book["status"], "applied", (book_title, ok_book))
                self.assertEqual(ok_book["item_name"], book_title)

            # Furniture cannot be bought on cafe drink slot
            _write_json(
                home / "data" / "world" / "world_state.json",
                {
                    "schema_version": "world.foundation.v0",
                    "revision": 4,
                    "world_id": docs["world.json"]["world_id"],
                    "timezone": "Asia/Tokyo",
                    "location": {"place_id": "cafe", "area_id": "cafe"},
                    "scene": {"revision": 4, "scene_id": "cafe.cafe"},
                    "facts": [],
                },
            )
            bad_cafe = economy.purchase_item(
                catalog_item_id="shop_slot:cafe:drink:budget",
                purchase_intent_id="op-bad-cafe-lamp",
                main_self_verified=True,
                hermes_home=home,
                environment=env,
                now=OPEN_NOW,
                custom_item_name="落地灯",
            )
            self.assertEqual(bad_cafe["status"], "rejected", bad_cafe)
            self.assertEqual(bad_cafe["reason_code"], "ITEM_CATEGORY_MISMATCH", bad_cafe)

    @unittest.skipUnless(os.name == "posix", "AG-1 & PhoneHTTPServer require POSIX fcntl/uid")
    def test_07_ag1_purchase_choice_and_phone_counter_item_name(self) -> None:
        import activity_continuity as ac  # noqa: E402
        import agency_decision_ag1 as ag1  # noqa: E402
        import autonomous_life_cycle as alc  # noqa: E402
        import candidate_sources_ag0 as ag0  # noqa: E402
        from components.phone import server as phone_server, service as phone_service  # noqa: E402

        docs = generate_world(seed=929711, theme="coastal_town")
        pkg_dir = self.tmp / "pkg_ag1"
        wg_schema.write_package(pkg_dir, docs)
        home = self.tmp / "ag1_home"
        env = _seed_runtime(
            home,
            world_id=docs["world.json"]["world_id"],
            place="convenience_store",
            area="convenience_store",
            package_dir=pkg_dir,
        )
        ts = OPEN_NOW.isoformat()

        slot_id = "shop_slot:convenience_store:food:standard"
        target_ref = f"buy:{slot_id}"
        wopp_ref = f"wopp:buy:convenience_store:r3:{slot_id}"
        cand = ag0.CandidateRecord(
            candidate_id="cand:buy_food_slot",
            candidate_kind="CONSIDER_WORLD_OPPORTUNITY",
            source_kind=ag0.SOURCE_WORLD_OPPORTUNITY,
            source_ref=wopp_ref,
            source_refs=[wopp_ref],
            availability_status="OPEN",
            created_at=ts,
            observed_at=ts,
            recorded_at=ts,
            idempotency_key="ik:buy_food_slot",
            target_refs=[target_ref],
            world_opportunity_ref=wopp_ref,
            world_revision_ref="wrev:coastal_town_929711:r3",
            perception_ref="percept:world-property-economy:convenience_store:r3",
            causal_parent_refs=[wopp_ref],
            provenance_refs=[wopp_ref],
        )
        cset = ag0.CandidateSet(
            candidate_set_id="cset:buy",
            subject_id=ag0.GLOBAL_SUBJECT_ID,
            candidate_refs=[cand.candidate_id],
            source_snapshot_refs=[],
            observed_at=ts,
            built_at=ts,
        )
        ctx = ag1.DecisionContextSnapshot(
            context_snapshot_id="dctx:buy",
            opportunity_ref="dopp:buy",
            candidate_set_ref="cset:buy",
            life_frame_ref="lframe:buy",
            captured_at=ts,
            source_revisions={},
            snapshot_fingerprint="sfp:buy",
            current_activity_ref=None,
            current_activity_status=None,
            interruptibility=ag1.INTERRUPTIBILITY_IMMEDIATE,
            known_waiting_basis_ref=None,
            observed_facts={},
        )
        c_by_id = {cand.candidate_id: cand}
        hcr_by_id = {
            cand.candidate_id: ag1.CandidateConstraintResult(
                constraint_result_id="hcres:buy_food_slot",
                candidate_ref=cand.candidate_id,
                status=ag1.CONSTRAINT_STATUS_ALLOWED,
                reason_codes=[],
                constraint_refs=[],
                evaluated_at=ts,
            )
        }
        cap = ag1.CapacityEvaluator.evaluate_capacity(
            allowed_candidates=[cand],
            context_snapshot=ctx,
            captured_at=ts,
            body_capacity_state="AVAILABLE",
            device_availability_state="AVAILABLE",
            resource_availability_state="AVAILABLE",
            privacy_availability_state="AVAILABLE",
            runtime_load_state="AVAILABLE",
        )

        validated = ag1.DecisionOutputValidator.validate_decision_output(
            {
                "decision": ag1.DECISION_START,
                "selected_candidate_id": cand.candidate_id,
                "reason_codes": [ag1.REASON_WORLD_OPPORTUNITY_OBSERVED],
                "short_rationale": "在便利店挑一份盐烤鲑鱼便当。",
                "deferred_candidate_ids": [],
                "purchase_choice": {"item_name": "盐烤鲑鱼便当", "estimated_price": 520},
            },
            candidate_set=cset,
            candidates_by_id=c_by_id,
            constraint_results_by_id=hcr_by_id,
            context_snapshot=ctx,
            capacity_snapshot=cap,
            allowed_method_refs=None,
        )
        self.assertEqual(
            validated["purchase_choice"],
            {"item_name": "盐烤鲑鱼便当", "estimated_price": 520},
        )

        # DecisionRecord.to_dict() omits purchase_choice when None and keeps it when present
        plain_rec = ag1.DecisionRecord(
            decision_id="dec:plain",
            revision=1,
            subject_id=ag0.GLOBAL_SUBJECT_ID,
            decision_opportunity_ref="dopp:buy",
            candidate_set_ref="cset:buy",
            candidate_refs=(cand.candidate_id,),
            context_snapshot_ref="dctx:buy",
            hard_constraint_result_refs=("hcres:buy_food_slot",),
            decision=ag1.DECISION_NO_ACTION,
            selected_candidate_ref=None,
            reason_codes=(ag1.REASON_NO_ACTION_CHOSEN,),
            decision_route=ag1.ROUTE_COGNITIVE_REQUIRED,
            policy_version=ag1.POLICY_VERSION_AG1_V1,
            snapshot_fingerprint="sfp:buy",
            short_rationale=None,
            created_at=ts,
        )
        self.assertNotIn("purchase_choice", plain_rec.to_dict())

        # Malformed purchase_choice raises with schema_field == "purchase_choice"
        for bad_choice in ({"item_name": ""}, {"item_name": "饭团", "extra": 1}, {"item_name": "饭团", "estimated_price": 999999}):
            with self.assertRaises(ag1.DecisionValidationError) as raised:
                ag1.DecisionOutputValidator.validate_decision_output(
                    {
                        "decision": ag1.DECISION_START,
                        "selected_candidate_id": cand.candidate_id,
                        "reason_codes": [ag1.REASON_WORLD_OPPORTUNITY_OBSERVED],
                        "short_rationale": "挑个便当。",
                        "deferred_candidate_ids": [],
                        "purchase_choice": bad_choice,
                    },
                    candidate_set=cset,
                    candidates_by_id=c_by_id,
                    constraint_results_by_id=hcr_by_id,
                    context_snapshot=ctx,
                    capacity_snapshot=cap,
                    allowed_method_refs=None,
                )
            self.assertEqual(ag1.describe_schema_refusal(raised.exception)["field"], "purchase_choice")

        # Execute through AutonomousLifeCycle._world_step_spec & _classify_purchase_step
        with patch.dict(os.environ, env, clear=False):
            rec = ag1.DecisionRecord(
                decision_id="dec:buy1",
                revision=1,
                subject_id=ag0.GLOBAL_SUBJECT_ID,
                decision_opportunity_ref="dopp:buy",
                candidate_set_ref="cset:buy",
                candidate_refs=(cand.candidate_id,),
                context_snapshot_ref="dctx:buy",
                hard_constraint_result_refs=("hcres:buy_food_slot",),
                decision=ag1.DECISION_START,
                selected_candidate_ref=cand.candidate_id,
                reason_codes=(ag1.REASON_WORLD_OPPORTUNITY_OBSERVED,),
                decision_route=ag1.ROUTE_COGNITIVE_REQUIRED,
                policy_version=ag1.POLICY_VERSION_AG1_V1,
                snapshot_fingerprint="sfp:buy",
                short_rationale="买份黑椒鸡排饭团。",
                purchase_choice={"item_name": "黑椒鸡排饭团", "estimated_price": 480},
                created_at=ts,
            )
            fake_cycle = SimpleNamespace(
                world_home=home,
                env=env,
                runtime=SimpleNamespace(
                    candidate_read=SimpleNamespace(
                        get_candidate=lambda _cid: {"target_refs": [target_ref]}
                    )
                ),
                last_world_observation={"location": {"place_id": "convenience_store"}, "world_revision": 3},
                last_world_opportunity_view=[
                    {
                        "target_ref": target_ref,
                        "purchase": {"name": "自选常规餐食", "price": 380, "currency": "JPY"},
                    }
                ],
                _stay_step_spec=lambda *_a, **_kw: {},
                _now=lambda: OPEN_NOW,
                _now_iso=lambda: ts,
                _record_purchase_progress=lambda *_a, **_kw: None,
                _finish=lambda base_obj, outcome, **extra: {**base_obj, "outcome": outcome, **extra},
            )
            step_spec_dict = alc.AutonomousLifeCycle._world_step_spec(fake_cycle, rec)
            step_spec = SimpleNamespace(payload_spec=step_spec_dict["payload_spec"])
            settled = alc.AutonomousLifeCycle._classify_purchase_step(
                fake_cycle, step_spec, {"activity_ref": None}, ts
            )
            self.assertEqual(settled["outcome"], alc.OUTCOME_ACTED, settled)
            self.assertEqual(settled["purchase"]["item_name"], "黑椒鸡排饭团")
            self.assertEqual(settled["purchase"]["price"], 480)

            # Phone HTTP allowlist & PhoneShopping counter purchase with item_name
            self.assertIn("item_name", phone_server.STORE_PURCHASE_FIELDS)
            self.assertNotIn("price", phone_server.STORE_PURCHASE_FIELDS)
            ps = phone_shopping.PhoneShopping(
                home, env, local_owner_uid=os.getuid(), clock=lambda: OPEN_NOW
            )
            counter_res = ps.store_purchase(
                "shop_slot:convenience_store:food:budget",
                "op-phone-onigiri-1",
                item_name="盐烤鲑鱼饭团",
            )
            self.assertTrue(counter_res["ok"], counter_res)
            self.assertEqual(counter_res["data"]["item_name"], "盐烤鲑鱼饭团")
            inv = ps.inventory()
            self.assertTrue(any(r["name"] == "盐烤鲑鱼饭团" for r in inv["data"]["items"]), inv)

    @unittest.skipUnless(os.name == "posix", "PhoneService & PhoneWorld local_owner_uid require POSIX uid")
    def test_08_open_counter_read_projections_and_zero_write(self) -> None:
        import hashlib
        import world_phone_world as phone_world
        from components.phone import day_story_view, day_timeline
        from components.phone import service as phone_service

        docs = generate_world(seed=929711, theme="coastal_town")
        pkg_dir = self.tmp / "pkg_read_proj"
        wg_schema.write_package(pkg_dir, docs)

        phone_root = self.tmp / "phone_read_root"
        home = phone_root / "world"
        env = _seed_runtime(
            home,
            world_id=docs["world.json"]["world_id"],
            place="convenience_store",
            area="convenience_store",
            package_dir=pkg_dir,
        )
        env[travel.TRAVEL_FEATURE_ENV] = "1"

        # Seed nested business clock for 2026-11-17 (+08:00) so timeline & story agree deterministically
        nested = phone_root / "one-day"
        _write_json(nested / "instance.json", {"schema_version": "life.one_day.v1"})
        _write_json(
            nested / "life" / "state" / "fastday" / "fastday_clock.json",
            {
                "kind": "explicit_compressed_demo_clock",
                "schema_version": 1,
                "mode": "COMPRESSED_DEMO",
                "rate": 12.0,
                "state": "STOPPED",
                "domain_id": "fastday:run:open-store-read",
                "run_id": "run:open-store-read",
                "logical_anchor": "2026-11-17T00:00:00+08:00",
                "logical_checkpoint": "2026-11-17T12:30:00+08:00",
                "horizon_logical": "2026-11-18T00:00:00+08:00",
            },
        )
        instance_doc = {
            "root": str(phone_root),
            "subject": "chiyo",
            "world_home": str(home),
            "supply_socket": str(phone_root / "run" / "supply.sock"),
            "private_items_artifact_id": "artifact:private-items",
            "artifact_grant_id": "grant:read-proj",
            "workspace_id": "workspace:read-proj",
            "features": dict(env),
        }
        _write_json(phone_root / "instance.json", instance_doc)

        with patch.dict(os.environ, env, clear=False):
            tea_res = economy.purchase_item(
                catalog_item_id="shop_slot:convenience_store:drink:budget",
                purchase_intent_id="op-read-tea",
                main_self_verified=True,
                hermes_home=home,
                environment=env,
                now=OPEN_NOW,
                custom_item_name="冷绿茶",
            )
            roll_res = economy.purchase_item(
                catalog_item_id="shop_slot:convenience_store:food:budget",
                purchase_intent_id="op-read-roll",
                main_self_verified=True,
                hermes_home=home,
                environment=env,
                now=OPEN_NOW + timedelta(minutes=2),
                custom_item_name="海盐黄油卷",
            )
            self.assertEqual(tea_res["status"], "applied", tea_res)
            self.assertEqual(roll_res["status"], "applied", roll_res)
            tea_id = tea_res["item_instance_id"]
            roll_id = roll_res["item_instance_id"]

            self.assertEqual(
                phone_shopping.materialize_owned_item(
                    hermes_home=home, item_instance_id=tea_id, environment=env
                )["status"],
                "materialized",
            )
            self.assertEqual(
                phone_shopping.materialize_owned_item(
                    hermes_home=home, item_instance_id=roll_id, environment=env
                )["status"],
                "materialized",
            )

            def _fingerprint(base: Path) -> tuple[list[str], dict[str, tuple[int, int, str]]]:
                tree = sorted(str(p.relative_to(base)) for p in base.rglob("*"))
                files: dict[str, tuple[int, int, str]] = {}
                for p in sorted(base.rglob("*")):
                    if p.is_file():
                        st = p.stat()
                        files[str(p.relative_to(base))] = (
                            st.st_mtime_ns,
                            st.st_size,
                            hashlib.sha256(p.read_bytes()).hexdigest(),
                        )
                return tree, files

            tree_before, files_before = _fingerprint(phone_root)

            # 1. world_phone_world snapshot & PhoneService.world() surface human names, never shop_slot:*
            pw = phone_world.PhoneWorld(
                str(home), env, local_owner_uid=os.getuid(), clock=lambda: OPEN_NOW
            )
            self.assertEqual(pw._labels()[tea_id], "冷绿茶")
            self.assertEqual(pw._labels()[roll_id], "海盐黄油卷")
            snap = pw.snapshot()
            self.assertTrue(snap["ok"], snap)
            here_by_id = {row["object_id"]: row for row in snap["data"]["objects"]["here"]}
            self.assertEqual(here_by_id[tea_id]["label"], "冷绿茶")
            self.assertEqual(here_by_id[roll_id]["label"], "海盐黄油卷")
            owned_by_id = {
                row["item_instance_id"]: row
                for row in snap["data"]["objects"]["owned_furniture"]["items"]
            }
            self.assertEqual(owned_by_id[tea_id]["label"], "冷绿茶")
            self.assertEqual(owned_by_id[tea_id]["name"], "冷绿茶")
            self.assertEqual(owned_by_id[roll_id]["label"], "海盐黄油卷")
            self.assertEqual(owned_by_id[roll_id]["name"], "海盐黄油卷")
            for row in snap["data"]["objects"]["here"]:
                self.assertFalse(str(row.get("label") or "").startswith("shop_slot:"), row)

            svc = phone_service.PhoneService(
                instance_doc, environment=env, world_clock=lambda: OPEN_NOW
            )
            world_ans = svc.world()
            self.assertTrue(world_ans["ok"], world_ans)
            self.assertIn(
                "冷绿茶",
                {r["label"] for r in world_ans["data"]["objects"]["here"]},
            )

            # 2. PhoneService.home_curios() returns name == "冷绿茶" & "海盐黄油卷"
            curios = svc.home_curios()
            self.assertTrue(curios["ok"], curios)
            curios_by_id = {r["item_instance_id"]: r for r in curios["data"]["items"]}
            self.assertEqual(curios_by_id[tea_id]["name"], "冷绿茶")
            self.assertEqual(curios_by_id[roll_id]["name"], "海盐黄油卷")

            # 3. PhoneService.home_recipes() ingredient reference includes "冷绿茶" & "海盐黄油卷"
            recipes = svc.home_recipes()
            self.assertTrue(recipes["ok"], recipes)
            ref_block = recipes["data"]["reference"]
            self.assertEqual(ref_block["status"], "available", ref_block)
            ref_names = {r["name"] for r in ref_block["items"]}
            self.assertIn("冷绿茶", ref_names)
            self.assertIn("海盐黄油卷", ref_names)

            # 4. PhoneService.wallet() transactions carry "item_name": "冷绿茶" & "海盐黄油卷"
            wallet = svc.wallet()
            self.assertTrue(wallet["ok"], wallet)
            tx_by_intent = {
                tx["purchase_intent_id"]: tx
                for tx in wallet["data"]["transactions"]
                if tx.get("purchase_intent_id")
            }
            self.assertEqual(tx_by_intent["op-read-tea"]["item_name"], "冷绿茶")
            self.assertEqual(tx_by_intent["op-read-roll"]["item_name"], "海盐黄油卷")

            # 5. day_timeline & day_story_view surface "冷绿茶" & "海盐黄油卷" cleanly
            tl = svc.day_timeline(subject_view=False)
            self.assertTrue(tl["ok"], tl)
            wallet_tl = [
                e for e in tl["data"]["entries"] if e.get("kind") == day_timeline.KIND_WALLET
            ]
            self.assertEqual({e.get("item_name") for e in wallet_tl}, {"冷绿茶", "海盐黄油卷"})

            story = svc.day_story(day="2026-11-17")
            self.assertTrue(story["ok"], story)
            self.assertTrue(story["read_only"])
            self.assertEqual(story["llm_calls"], 0)
            story_titles = [it["title"] for it in story["data"]["story_items"]]
            self.assertIn("添置了「冷绿茶」", story_titles)
            self.assertIn("添置了「海盐黄油卷」", story_titles)
            self.assertNotIn("专心忙了一会儿手头的事", story_titles)
            story_dump = json.dumps(story, ensure_ascii=False)
            self.assertNotIn("shop_slot:", story_dump)
            for item in story["data"]["story_items"]:
                if item["domain"] == "ecology":
                    self.assertEqual(item["because_tag"], day_story_view.BECAUSE_SHOPPING)

            # Standalone read_today_story_zero_write(phone_root, day=...) also works without service
            standalone_story = day_story_view.read_today_story_zero_write(
                phone_root, day="2026-11-17"
            )
            self.assertIn(
                "添置了「冷绿茶」",
                [it["title"] for it in standalone_story["data"]["story_items"]],
            )

            # Clean Chinese fallback when wallet_transaction lacks item_name
            fallback_item = day_story_view._timeline_entry_to_story_item(
                {
                    "kind": "wallet_transaction",
                    "occurred_at": "2026-11-17T13:00:00+08:00",
                    "direction": "DEBIT",
                    "source_ref": "tx-anon",
                },
                resolved_day="2026-11-17",
                default_place="房间里",
                decisions=[],
                used_decision_ids=set(),
            )
            self.assertIsNotNone(fallback_item)
            self.assertEqual(fallback_item["title"], "添置了一件日常小物")
            self.assertEqual(fallback_item["because_tag"], day_story_view.BECAUSE_SHOPPING)

            # 6. All GET reads remain 100% zero-write (byte-for-byte + mtime_ns + file tree)
            tree_after, files_after = _fingerprint(phone_root)
            self.assertEqual(tree_before, tree_after)
            self.assertEqual(files_before, files_after)


if __name__ == "__main__":
    unittest.main()

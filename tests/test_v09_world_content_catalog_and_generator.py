#!/usr/bin/env python3
"""v0.9 Pillar B acceptance tests: Unified Dynamic Content Catalog & Scoped Generator.

Verifies (with zero external LLM/network calls):
1. Single-definition registration of seasonal tea, portable biscuit tin
   (``reusable_container_spec``), and ``decor_fixture`` lamp -> immediately
   buyable at storefront, wallet charged, stock decrements ``2 -> 1 -> 0``,
   3rd purchase refused with ``ITEM_OUT_OF_STOCK`` (wallet unchanged!),
   idempotent replay preserved, persists across fresh read, consumable relief
   works via ``world_consumable_contract``, and dynamic lamp emits real room
   light via ``world_room_environment``.
2. All 6 item templates + 4 event templates validate cleanly; unknown templates
   (``bicycle_rental``, ``fermenter``) rejected with ``UNSUPPORTED_CAPABILITY_TEMPLATE``;
   smuggled keys (``force_subject_preference``, ``custom_code``, ``affection_delta``,
   ``grant_capability``) rejected with ``FORBIDDEN_CANDIDATE_FIELD``.
3. ``anti_spoil_guard`` rejects direct and nested ``private_desires``,
   ``chat_transcript``, ``everyday_desires`` with ``AntiSpoilViolationError``
   BEFORE calling ``llm_proposal_fn`` or reserving budget.
4. Deterministic offline generator (``llm_calls == 0``), 12h per-store cooldown,
   and budgeted scoped mock callback respecting ``RequestBudgetError`` when
   daily token budget is tight (``49,000 + 1,200 > 50,000`` -> ``budget_refused``,
   callback called 0 times, persisted tokens stay 49,000).
5. 100% zero-write ``read_dynamic_catalog_zero_write``, unified forget-barrier
   filtering, symlink rejection, ``0600`` pure-LF atomic persistence, and
   10-thread concurrent stock decrement with zero overselling.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORLD_SCRIPTS = ROOT / "components" / "world" / "scripts"
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
for _p in (str(ROOT), str(WORLD_SCRIPTS), str(LIFE_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import life_request_budget as lrb  # noqa: E402
import v02_representation_lease_forget as forget_mod  # noqa: E402
import world_body_substrate as substrate  # noqa: E402
import world_consumable_contract as consumable_contract  # noqa: E402
import world_content_catalog as catalog  # noqa: E402
import world_content_generator as generator  # noqa: E402
import world_open_storefront as storefront  # noqa: E402
import world_property_economy as economy  # noqa: E402
import world_room_environment as room_env  # noqa: E402


OPEN_NOW = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)  # 12:00 +08:00
OPEN_NOW_ISO = "2026-11-17T12:00:00+08:00"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        try:
            st = p.lstat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
        except OSError:
            pass
    return out


def _seed_store_home(
    home: Path,
    *,
    place: str = "home_goods_store",
    area: str = "counter",
    delivery_enabled: bool = False,
) -> tuple[dict[str, str], dict[str, Any]]:
    for rel in (("data", "world"), ("data", "body"), ("state",), ("run",)):
        home.joinpath(*rel).mkdir(parents=True, exist_ok=True)

    _write_json(
        home / "data" / "world" / "world_state.json",
        {
            "schema_version": "world.foundation.v0",
            "revision": 3,
            "world_id": "shiomi_city",
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
            "world_id": "shiomi_city",
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
    sub = substrate.initial_substrate(world_id="shiomi_city", body_id="chiyo_body")
    substrate.SubstrateStore(substrate.substrate_path(home)).save(sub)

    env: dict[str, str] = {
        economy.FEATURE_ENV: "1",
        economy.WORLD_FEATURE_ENV: "1",
        economy.DAILY_FEATURE_ENV: "1",
        economy.DELIVERY_FEATURE_ENV: "1" if delivery_enabled else "0",
        substrate.FEATURE_ENV: "1",
    }
    economy.initialize_genesis(hermes_home=home, environment=env)
    uid = os.getuid() if hasattr(os, "getuid") else 0
    local_owner = {
        "source": economy.LOCAL_OWNER_SOURCE,
        "uid": uid,
        "subject": economy.OWNER,
        "home": str(home),
    }
    return env, local_owner


class WorldContentCatalogAndGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nyairo-v09-catalog-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_single_definition_purchase_stock_decrement_consumable_and_lamp_light(self) -> None:
        home = self.tmp / "home_e2e"
        env, local_owner = _seed_store_home(home, place="home_goods_store", area="counter")

        # Bare shiomi_city WITHOUT dynamic_content_catalog.json still yields resolve_world_sources == None.
        self.assertIsNone(storefront.resolve_world_sources(home, env))

        batch = catalog.register_catalog_batch(
            home,
            items=[
                {
                    "template_id": "drink_warm",
                    "store_id": "home_goods_store",
                    "name_cn": "慢焙陈皮桂花茶",
                    "description_cn": "秋日现窨干桂花与老陈皮慢焙热饮",
                    "price_tier": "standard",
                    "price": 420,
                    "stock_initial": 2,
                    "portions": 2,
                    "aroma_cn": "干桂花与陈皮暖香",
                    "taste_note_cn": "微苦回甘不甜腻",
                    "season": "autumn",
                },
                {
                    "template_id": "snack_portable",
                    "store_id": "home_goods_store",
                    "name_cn": "海盐黄油饼干铁盒",
                    "description_cn": "酥松微咸，空铁盒可留作案头收纳",
                    "price_tier": "standard",
                    "price": 560,
                    "stock_initial": 2,
                    "portions": 4,
                    "taste_note_cn": "黄油香伴海盐微咸",
                    "reusable_container_spec": {
                        "container_kind": "cookie_tin",
                        "label_cn": "浅青色铁皮饼干盒",
                        "capacity_slots": 4,
                        "drinkware": False,
                        "supported_capabilities": ["STORE_ITEMS", "ATTACH_LABEL", "ADD_NOTE"],
                    },
                    "season": "autumn",
                },
                {
                    "template_id": "decor_fixture",
                    "store_id": "home_goods_store",
                    "name_cn": "折纸罩暖光台灯",
                    "description_cn": "放在案头或床边照明的折叠纸罩灯具",
                    "price_tier": "standard",
                    "price": 1800,
                    "stock_initial": 1,
                    "surfaces": [],
                    "light_band": "moderate",
                    "material_cn": "厚棉纸与木座",
                    "season": "autumn",
                },
            ],
            source_kind="manual_seed",
            trigger_reason="SEASON_ARRIVAL",
            operation_key="reg-batch-001",
            now_iso=OPEN_NOW_ISO,
        )
        self.assertTrue(batch["ok"], batch)
        self.assertEqual(batch["status"], "applied")
        self.assertEqual(len(batch["registered_items"]), 3)
        self.assertEqual(batch["rejected_candidates"], [])

        tea = next(x for x in batch["registered_items"] if x["name_cn"] == "慢焙陈皮桂花茶")
        tin = next(x for x in batch["registered_items"] if x["name_cn"] == "海盐黄油饼干铁盒")
        lamp = next(x for x in batch["registered_items"] if x["name_cn"] == "折纸罩暖光台灯")

        # IDs are 100% compatible with existing parse_shop_item_id contract.
        self.assertEqual(
            storefront.parse_shop_item_id(tea["catalog_item_id"])[:2],
            ("home_goods_store", "drink"),
        )
        self.assertEqual(
            storefront.parse_shop_item_id(tin["catalog_item_id"])[:2],
            ("home_goods_store", "food"),
        )
        self.assertEqual(
            storefront.parse_shop_item_id(lamp["catalog_item_id"])[:2],
            ("home_goods_store", "furnish"),
        )

        # Pure LF & 0600 permissions on POSIX.
        cat_file = catalog.catalog_state_path(home)
        raw_bytes = cat_file.read_bytes()
        self.assertNotIn(b"\r", raw_bytes)
        if os.name == "posix":
            mode = stat.S_IMODE(cat_file.stat().st_mode)
            self.assertEqual(mode, 0o600)

        # Immediately visible on store shelf!
        proj = economy.store_shop_projection(hermes_home=home, environment=env, now=OPEN_NOW)
        self.assertEqual(proj["status"], "available", proj)
        shelf_by_id = {row["catalog_item_id"]: row for row in proj["catalog"]}
        self.assertIn(tea["catalog_item_id"], shelf_by_id)
        self.assertIn(tin["catalog_item_id"], shelf_by_id)
        self.assertIn(lamp["catalog_item_id"], shelf_by_id)
        self.assertEqual(shelf_by_id[tea["catalog_item_id"]]["stock_remaining"], 2)
        self.assertEqual(
            shelf_by_id[tin["catalog_item_id"]]["reusable_container_spec"]["container_kind"],
            "cookie_tin",
        )

        initial_balance = economy.wallet_projection(hermes_home=home, environment=env)["current_balance"]
        self.assertEqual(initial_balance, 10_000)

        # Purchase 1 of tea: stock 2 -> 1, wallet 10000 -> 9580
        buy1 = economy.store_purchase_item(
            catalog_item_id=tea["catalog_item_id"],
            operation_key="buy-tea-1",
            local_owner=local_owner,
            hermes_home=home,
            environment=env,
            now=OPEN_NOW,
        )
        self.assertEqual(buy1["status"], "applied", buy1)
        self.assertEqual(buy1["current_balance"], 10_000 - 420)
        self.assertEqual(buy1["stock_remaining"], 1)

        # Purchase 2 of tea: stock 1 -> 0, wallet 9580 -> 9160
        buy2 = economy.store_purchase_item(
            catalog_item_id=tea["catalog_item_id"],
            operation_key="buy-tea-2",
            local_owner=local_owner,
            hermes_home=home,
            environment=env,
            now=OPEN_NOW,
        )
        self.assertEqual(buy2["status"], "applied", buy2)
        self.assertEqual(buy2["current_balance"], 10_000 - 840)
        self.assertEqual(buy2["stock_remaining"], 0)

        # Idempotent replay of purchase 2 still works cleanly even after stock hit 0!
        buy2_replay = economy.store_purchase_item(
            catalog_item_id=tea["catalog_item_id"],
            operation_key="buy-tea-2",
            local_owner=local_owner,
            hermes_home=home,
            environment=env,
            now=OPEN_NOW,
        )
        self.assertEqual(buy2_replay["status"], "already_applied", buy2_replay)
        self.assertEqual(buy2_replay["reason_code"], "IDEMPOTENT_REPLAY")
        self.assertEqual(buy2_replay["current_balance"], 10_000 - 840)

        # 3rd purchase refused with ITEM_OUT_OF_STOCK and wallet UNCHANGED!
        buy3 = economy.store_purchase_item(
            catalog_item_id=tea["catalog_item_id"],
            operation_key="buy-tea-3",
            local_owner=local_owner,
            hermes_home=home,
            environment=env,
            now=OPEN_NOW,
        )
        self.assertEqual(buy3["status"], "rejected", buy3)
        self.assertEqual(buy3["reason_code"], "ITEM_OUT_OF_STOCK")
        bal_after_refusal = economy.wallet_projection(hermes_home=home, environment=env)["current_balance"]
        self.assertEqual(bal_after_refusal, 10_000 - 840)

        # Fresh read persistence check: hidden from active in-stock shelf, persisted at 0.
        fresh_in_stock = catalog.read_dynamic_catalog_zero_write(
            home, store_id="home_goods_store", include_out_of_stock=False
        )
        self.assertNotIn(tea["catalog_item_id"], fresh_in_stock["items_by_id"])
        fresh_all = catalog.read_dynamic_catalog_zero_write(
            home, store_id="home_goods_store", include_out_of_stock=True
        )
        self.assertEqual(fresh_all["items_by_id"][tea["catalog_item_id"]]["stock_remaining"], 0)
        self.assertEqual(fresh_all["items_by_id"][tea["catalog_item_id"]]["total_sold"], 2)

        # Restock restores availability cleanly.
        restock = catalog.restock_dynamic_item(
            home,
            catalog_item_id=tea["catalog_item_id"],
            add_units=2,
            operation_key="restock-tea-1",
            now_iso=OPEN_NOW_ISO,
        )
        self.assertTrue(restock["ok"], restock)
        self.assertEqual(restock["stock_remaining"], 2)

        # Purchase biscuit tin via verified main-self purchase_item -> consumable relief works!
        buy_tin = economy.purchase_item(
            catalog_item_id=tin["catalog_item_id"],
            purchase_intent_id="buy-tin-1",
            main_self_verified=True,
            hermes_home=home,
            environment=env,
            now=OPEN_NOW,
        )
        self.assertEqual(buy_tin["status"], "applied", buy_tin)
        self.assertEqual(buy_tin["stock_remaining"], 1)
        eco_state = economy.load_state(home)
        c_decl = consumable_contract.declaration_of(
            buy_tin["item_instance_id"], home, economy=eco_state
        )
        self.assertTrue(c_decl.get("consumable"))
        self.assertEqual(c_decl.get("consumption_permit"), "owned_purchase")
        self.assertIn("hunger", c_decl.get("lived_need_effects") or {})

        # Purchase dynamic decor_fixture lamp -> emits real room light!
        buy_lamp = economy.store_purchase_item(
            catalog_item_id=lamp["catalog_item_id"],
            operation_key="buy-lamp-1",
            local_owner=local_owner,
            hermes_home=home,
            environment=env,
            now=OPEN_NOW,
        )
        self.assertEqual(buy_lamp["status"], "applied", buy_lamp)
        self.assertEqual(buy_lamp["stock_remaining"], 0)
        lamp_inst_id = buy_lamp["item_instance_id"]

        # Room environment recognizes dynamic lamp declaration zero-write!
        lamp_decl = room_env.declaration_of_catalog(lamp["catalog_item_id"], hermes_home=home)
        self.assertIsNotNone(lamp_decl)
        self.assertTrue(lamp_decl["powerable"])
        self.assertEqual(lamp_decl["light_band"], "moderate")
        self.assertEqual(lamp_decl["declared_source"], f"catalog:{lamp['catalog_item_id']}")

        # Place dynamic lamp in home/bedroom and power ON -> light_for_home emits moderate light!
        _write_json(
            home / "data" / "world" / "world_state.json",
            {
                "schema_version": "world.foundation.v0",
                "revision": 4,
                "world_id": "shiomi_city",
                "timezone": "Asia/Tokyo",
                "location": {"place_id": "home", "area_id": "bedroom"},
                "scene": {"revision": 4, "scene_id": "home.bedroom"},
                "facts": [],
            },
        )
        sub_state = substrate.SubstrateStore(substrate.substrate_path(home)).load()
        sub_state = substrate.place_object(
            sub_state,
            lamp_inst_id,
            {"kind": "room", "location_id": "home", "area_id": "bedroom"},
        )
        sub_state.setdefault("fixtures", {})[lamp_inst_id] = {
            "power": "on",
            "since": (OPEN_NOW - timedelta(minutes=15)).isoformat(),
            "declared_source": f"catalog:{lamp['catalog_item_id']}",
            "revision": 1,
        }
        substrate.SubstrateStore(substrate.substrate_path(home)).save(sub_state)

        light = room_env.light_for_home(home, now=OPEN_NOW)
        self.assertEqual(light["status"], room_env.STATUS_AVAILABLE, light)
        self.assertEqual(light["power"], room_env.POWER_ON)
        self.assertEqual(light["band"], "moderate")
        self.assertEqual(light["fixture_ids"], [lamp_inst_id])
        self.assertEqual(
            room_env.environment_input(light),
            {"class": "ENVIRONMENT", "sensory_intensity": 0.45},
        )

    def test_02_all_templates_and_unsupported_or_forbidden_rejections(self) -> None:
        home = self.tmp / "home_validation"
        home.mkdir(parents=True, exist_ok=True)

        # Reject unknown templates (bicycle_rental, fermenter).
        ok_bike, norm_bike, err_bike = catalog.validate_candidate_item(
            {"template_id": "bicycle_rental", "store_id": "cafe", "name_cn": "租借单车"}
        )
        self.assertFalse(ok_bike)
        self.assertIsNone(norm_bike)
        self.assertEqual(err_bike, "UNSUPPORTED_CAPABILITY_TEMPLATE")

        ok_ferm, norm_ferm, err_ferm = catalog.validate_candidate_item(
            {"template_id": "fermenter", "store_id": "cafe", "name_cn": "发酵缸"}
        )
        self.assertFalse(ok_ferm)
        self.assertIsNone(norm_ferm)
        self.assertEqual(err_ferm, "UNSUPPORTED_CAPABILITY_TEMPLATE")

        # Reject smuggled forbidden keys (top-level & nested).
        for bad_field in (
            "force_subject_preference",
            "custom_code",
            "affection_delta",
            "grant_capability",
        ):
            ok_bad, _n, err_bad = catalog.validate_candidate_item(
                {
                    "template_id": "drink_warm",
                    "store_id": "cafe",
                    "name_cn": "测试热茶",
                    bad_field: "hack",
                }
            )
            self.assertFalse(ok_bad, bad_field)
            self.assertEqual(err_bad, "FORBIDDEN_CANDIDATE_FIELD")

        ok_nested, _n2, err_nested = catalog.validate_candidate_item(
            {
                "template_id": "snack_portable",
                "store_id": "cafe",
                "name_cn": "夹带饼干盒",
                "reusable_container_spec": {
                    "container_kind": "cookie_tin",
                    "label_cn": "铁盒",
                    "force_subject_preference": True,
                },
            }
        )
        self.assertFalse(ok_nested)
        self.assertEqual(err_nested, "FORBIDDEN_CANDIDATE_FIELD")

        # Register all 6 valid item templates + all 4 valid event templates + 2 invalid rows in one batch.
        tea_id = storefront.stable_shop_item_id("cafe", "焙火春乌龙", "drink")
        res = catalog.register_catalog_batch(
            home,
            items=[
                {"template_id": "drink_warm", "store_id": "cafe", "name_cn": "焙火春乌龙", "aroma_cn": "浅焙香"},
                {"template_id": "meal_light", "store_id": "cafe", "name_cn": "菌菇热饭团", "taste_note_cn": "微咸"},
                {"template_id": "snack_portable", "store_id": "cafe", "name_cn": "核桃酥铁盒", "portions": 4},
                {"template_id": "desk_vessel", "store_id": "home_goods_store", "name_cn": "粗陶宽口杯", "capacity_slots": 3, "drinkware": True},
                {"template_id": "decor_fixture", "store_id": "home_goods_store", "name_cn": "窗边矮几", "surfaces": ["desk_surface"], "light_band": None},
                {
                    "template_id": "reading_booklet",
                    "store_id": "bookstore",
                    "name_cn": "潮汐气象小册",
                    "pages_count": 16,
                    "topic_tags": ["气象", "海岸"],
                    "argument_thesis_cn": "连续三日气压对照比单日经验更可靠。",
                    "sample_excerpt_cn": "十月西北风起前常见短浪。",
                },
                {"template_id": "bicycle_rental", "store_id": "cafe", "name_cn": "违规单车"},
                {"template_id": "drink_warm", "store_id": "cafe", "name_cn": "违规热茶", "force_subject_preference": "like"},
            ],
            events=[
                {
                    "template_id": "book_swap",
                    "venue_id": "bookstore",
                    "swap_titles": ["潮汐气象小册", "旧街区笔记"],
                    "start_at": "2026-11-18T10:00:00+08:00",
                    "end_at": "2026-11-18T16:00:00+08:00",
                },
                {
                    "template_id": "tea_tasting",
                    "venue_id": "cafe",
                    "featured_catalog_item_id": tea_id,
                    "sample_stock": 8,
                    "start_at": "2026-11-18T14:00:00+08:00",
                    "end_at": "2026-11-18T17:00:00+08:00",
                },
                {
                    "template_id": "early_closing",
                    "venue_id": "cafe",
                    "closed_from_hhmm": "17:30",
                    "reason_notice_cn": "店主外出采购豆子，今日提前打烊。",
                    "date_iso": "2026-11-19",
                },
                {
                    "template_id": "porch_market",
                    "venue_id": "home_goods_store",
                    "featured_item_ids": [tea_id],
                    "start_at": "2026-11-20T09:00:00+08:00",
                    "end_at": "2026-11-20T15:00:00+08:00",
                },
                {"template_id": "fireworks_festival", "venue_id": "cafe"},
            ],
            operation_key="all-templates-batch",
            now_iso=OPEN_NOW_ISO,
        )
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(res["registered_items"]), 6)
        self.assertEqual(len(res["registered_events"]), 4)
        self.assertEqual(len(res["rejected_candidates"]), 3)
        rejected_codes = [row["reason_code"] for row in res["rejected_candidates"]]
        self.assertEqual(
            rejected_codes,
            [
                "UNSUPPORTED_CAPABILITY_TEMPLATE",
                "FORBIDDEN_CANDIDATE_FIELD",
                "UNSUPPORTED_CAPABILITY_TEMPLATE",
            ],
        )

    def test_03_anti_spoil_guard_blocks_nested_private_keys_before_llm_or_budget(self) -> None:
        home = self.tmp / "home_antispoil"
        home.mkdir(parents=True, exist_ok=True)
        budget_dir = self.tmp / "budget_antispoil"
        budget = lrb.LifeRequestBudget(
            budget_dir,
            utc_day_max=24,
            daily_token_budget=50_000,
        )

        # Direct & deeply nested forbidden private keys.
        for bad_ctx, expected_key in (
            ({"store_id": "cafe", "private_desires": ["想喝甜的"]}, "private_desires"),
            (
                {"store_id": "cafe", "public_placed_orders_only": [{"nested": {"chat_transcript": "hi"}}]},
                "chat_transcript",
            ),
            (
                {"store_id": "cafe", "extra": {"inner": [{"everyday_desires": ["book"]}]}},
                "everyday_desires",
            ),
            ({"store_id": "cafe", "diary_entries": ["secret"]}, "diary_entries"),
            ({"store_id": "cafe", "v09_true_stance": {"x": 1}}, "v09_true_stance"),
        ):
            with self.assertRaises(generator.AntiSpoilViolationError) as ctx_err:
                generator.anti_spoil_guard(bad_ctx)
            self.assertEqual(ctx_err.exception.offending_key, expected_key)

        # Must raise BEFORE invoking llm_proposal_fn AND BEFORE reserving budget!
        callback_calls = 0

        def _should_never_run(_clean_ctx: dict[str, Any]) -> dict[str, Any]:
            nonlocal callback_calls
            callback_calls += 1
            return {"items": []}

        with self.assertRaises(generator.AntiSpoilViolationError):
            generator.generate_world_content_batch(
                home,
                store_id="cafe",
                world_context={
                    "store_id": "cafe",
                    "season": "autumn",
                    "metadata": {"deep": {"private_desires": ["抹茶"]}},
                },
                llm_proposal_fn=_should_never_run,
                budget=budget,
                est_tokens=1200,
                now_iso=OPEN_NOW_ISO,
            )
        self.assertEqual(callback_calls, 0)
        # Budget ledger wasn't even touched!
        self.assertFalse((budget_dir / lrb.BUDGET_FILENAME).exists())

        # Benign non-whitelisted keys are stripped cleanly from public output.
        cleaned = generator.anti_spoil_guard(
            {
                "world_id": "coastal_1",
                "theme": "coastal",
                "seed": 42,
                "season": "autumn",
                "store_id": "cafe",
                "benign_extra_note": "should_be_stripped",
            }
        )
        self.assertTrue(set(cleaned.keys()).issubset(generator.PUBLIC_WORLD_CONTEXT_WHITELIST))
        self.assertNotIn("benign_extra_note", cleaned)

    def test_04_deterministic_generator_cooldown_and_budgeted_mock_llm_ceiling(self) -> None:
        home = self.tmp / "home_gen"
        home.mkdir(parents=True, exist_ok=True)

        # 1. Deterministic offline generation (llm_calls == 0).
        det1 = generator.generate_world_content_batch(
            home,
            store_id="cafe",
            trigger_reason="SEASON_SHIFT",
            world_context={
                "world_id": "coastal_101",
                "theme": "coastal",
                "seed": "seed_alpha",
                "season": "autumn",
                "weather": "clear",
                "store_id": "cafe",
                "store_name": "拐角咖啡屋",
            },
            llm_proposal_fn=None,
            now_iso="2026-11-17T09:00:00+08:00",
        )
        self.assertTrue(det1["ok"], det1)
        self.assertEqual(det1["llm_calls"], 0)
        self.assertEqual(det1["mode"], "deterministic")
        self.assertGreaterEqual(len(det1["registered_items"]), 1)
        self.assertLessEqual(len(det1["registered_items"]), 3)

        # 2. Per-store 12-hour cooldown blocks premature regeneration!
        det_cooldown = generator.generate_world_content_batch(
            home,
            store_id="cafe",
            trigger_reason="SEASON_SHIFT",
            llm_proposal_fn=None,
            now_iso="2026-11-17T15:00:00+08:00",  # only +6h (< 12h)
        )
        self.assertFalse(det_cooldown["ok"])
        self.assertEqual(det_cooldown["status"], "cooldown_active")
        self.assertEqual(det_cooldown["reason_code"], "GENERATION_COOLDOWN_ACTIVE")
        self.assertEqual(det_cooldown["llm_calls"], 0)

        # 3. Budgeted scoped LLM mock callback when daily token budget is tight:
        #    49,000 + 1,200 > 50,000 -> budget_refused, callback called 0 times!
        budget_dir = self.tmp / "budget_tight"
        budget = lrb.LifeRequestBudget(
            budget_dir,
            utc_day_max=100,
            daily_token_budget=50_000,
        )
        now_budget = datetime(2026, 11, 18, 2, 0, tzinfo=timezone.utc)  # 10:00 +08:00 (>12h later)
        budget.reserve(lrb.REQUEST_KIND_DECISION, est_tokens=49_000, now=now_budget)

        mock_calls = 0

        def _mock_llm(clean_ctx: dict[str, Any]) -> dict[str, Any]:
            nonlocal mock_calls
            mock_calls += 1
            self.assertTrue(set(clean_ctx.keys()).issubset(generator.PUBLIC_WORLD_CONTEXT_WHITELIST))
            return {
                "items": [
                    {
                        "template_id": "drink_warm",
                        "name_cn": "炭焙冬姜茶",
                        "price_tier": "budget",
                        "stock_initial": 3,
                        "aroma_cn": "姜香与焙火香",
                        "taste_note_cn": "辛温微辣",
                    },
                    {
                        "template_id": "bicycle_rental",
                        "name_cn": "违规租车",
                    },
                ],
                "events": [],
            }

        refused = generator.generate_world_content_batch(
            home,
            store_id="cafe",
            trigger_reason="WEEKLY_REFRESH",
            llm_proposal_fn=_mock_llm,
            budget=budget,
            est_tokens=1200,
            now_iso=now_budget.isoformat(),
        )
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["status"], "budget_refused")
        self.assertEqual(refused["reason_code"], lrb.REASON_DAILY_TOKEN_BUDGET)
        self.assertEqual(refused["llm_calls"], 0)
        self.assertEqual(mock_calls, 0)
        self.assertEqual(budget.check(now=now_budget)["utc_day_tokens"], 49_000)

        # 4. With sufficient daily budget (48,000 + 1,200 <= 50,000), mock callback runs once,
        #    reserves REQUEST_KIND_CONTENT, persists valid tea, and records rejected bicycle_rental!
        budget_ok_dir = self.tmp / "budget_ok"
        budget_ok = lrb.LifeRequestBudget(
            budget_ok_dir,
            utc_day_max=100,
            daily_token_budget=50_000,
        )
        budget_ok.reserve(lrb.REQUEST_KIND_DECISION, est_tokens=48_000, now=now_budget)

        ok_res = generator.generate_world_content_batch(
            home,
            store_id="cafe",
            trigger_reason="WEEKLY_REFRESH",
            llm_proposal_fn=_mock_llm,
            budget=budget_ok,
            est_tokens=1200,
            now_iso=now_budget.isoformat(),
        )
        self.assertTrue(ok_res["ok"], ok_res)
        self.assertEqual(ok_res["status"], "applied")
        self.assertEqual(ok_res["llm_calls"], 1)
        self.assertEqual(mock_calls, 1)
        self.assertEqual(len(ok_res["registered_items"]), 1)
        self.assertEqual(ok_res["registered_items"][0]["name_cn"], "炭焙冬姜茶")
        self.assertEqual(len(ok_res["rejected_candidates"]), 1)
        self.assertEqual(ok_res["rejected_candidates"][0]["reason_code"], "UNSUPPORTED_CAPABILITY_TEMPLATE")
        self.assertEqual(ok_res["budget_reservation"]["reservation"]["kind"], lrb.REQUEST_KIND_CONTENT)
        self.assertEqual(budget_ok.check(now=now_budget)["utc_day_tokens"], 49_200)

    def test_05_zero_write_reads_forget_barrier_symlinks_and_10_thread_concurrency(self) -> None:
        # 1. 100% zero-write on completely empty directory (0 dirs, 0 files, 0 .lock).
        empty_root = self.tmp / "empty_zero_write"
        empty_root.mkdir(parents=True, exist_ok=True)
        before_empty = _snapshot_tree(empty_root)
        snap = catalog.read_dynamic_catalog_zero_write(empty_root)
        self.assertFalse(snap["exists"])
        self.assertEqual(snap["items"], [])
        self.assertTrue(snap["read_is_pure"])
        self.assertEqual(snap["llm_calls"], 0)
        self.assertIsNone(
            catalog.find_dynamic_fixture_light_declaration_zero_write(empty_root, "shop_item:x:furnish:00000000")
        )
        self.assertEqual(_snapshot_tree(empty_root), before_empty)

        # 2. 10-thread concurrent stock decrement with stock_initial == 3 -> NO overselling!
        conc_root = self.tmp / "conc_stock"
        conc_root.mkdir(parents=True, exist_ok=True)
        reg = catalog.register_catalog_batch(
            conc_root,
            items=[
                {
                    "template_id": "drink_warm",
                    "store_id": "cafe",
                    "name_cn": "限量手冲热茶",
                    "stock_initial": 3,
                    "price_tier": "standard",
                }
            ],
            operation_key="seed-conc-tea",
            now_iso=OPEN_NOW_ISO,
        )
        self.assertTrue(reg["ok"])
        item_id = reg["registered_items"][0]["catalog_item_id"]

        # Zero-write read on populated catalog creates 0 mutations.
        before_populated = _snapshot_tree(conc_root)
        snap_pop = catalog.read_dynamic_catalog_zero_write(conc_root)
        self.assertTrue(snap_pop["exists"])
        self.assertEqual(len(snap_pop["items"]), 1)
        self.assertEqual(_snapshot_tree(conc_root), before_populated)

        barrier_sync = threading.Barrier(10)

        def _try_dec(idx: int) -> dict[str, Any]:
            barrier_sync.wait(timeout=10)
            return catalog.decrement_dynamic_item_stock(
                conc_root,
                catalog_item_id=item_id,
                purchase_intent_id=f"concurrent-intent-{idx:02d}",
                quantity=1,
                now_iso=OPEN_NOW_ISO,
            )

        with ThreadPoolExecutor(max_workers=10) as pool:
            outcomes = list(pool.map(_try_dec, range(10)))

        applied = [r for r in outcomes if r.get("status") == "applied"]
        out_of_stock = [
            r
            for r in outcomes
            if r.get("status") == "rejected" and r.get("reason_code") == "ITEM_OUT_OF_STOCK"
        ]
        self.assertEqual(len(applied), 3, outcomes)
        self.assertEqual(len(out_of_stock), 7, outcomes)

        final_snap = catalog.read_dynamic_catalog_zero_write(conc_root, include_out_of_stock=True)
        self.assertEqual(final_snap["items_by_id"][item_id]["stock_remaining"], 0)
        self.assertEqual(final_snap["items_by_id"][item_id]["total_sold"], 3)

        # 3. Unified forget barrier strips blocked catalog item zero-write!
        fg_root = self.tmp / "forget_barrier_root"
        fg_root.mkdir(parents=True, exist_ok=True)
        fg_reg = catalog.register_catalog_batch(
            fg_root,
            items=[
                {
                    "template_id": "decor_fixture",
                    "store_id": "home_goods_store",
                    "name_cn": "被遗忘台灯",
                    "light_band": "dim",
                    "stock_initial": 1,
                },
                {
                    "template_id": "drink_warm",
                    "store_id": "home_goods_store",
                    "name_cn": "正常热茶",
                    "stock_initial": 2,
                },
            ],
            operation_key="seed-fg",
            now_iso=OPEN_NOW_ISO,
        )
        blocked_lamp_id = next(
            r["catalog_item_id"] for r in fg_reg["registered_items"] if r["name_cn"] == "被遗忘台灯"
        )
        normal_tea_id = next(
            r["catalog_item_id"] for r in fg_reg["registered_items"] if r["name_cn"] == "正常热茶"
        )
        # Seed a v02 forget hold targeting blocked_lamp_id.
        _write_json(
            fg_root.joinpath(*forget_mod.STATE_REL_PATH),
            {
                "schema_version": forget_mod.SCHEMA_VERSION,
                "revision": 1,
                "holds": {
                    "hold_test_lamp": {
                        "status": forget_mod.CASE_HOLD_PENDING,
                        "read_blocked": True,
                        "scoped_source_ids": [blocked_lamp_id],
                    }
                },
                "sources": {},
                "derivations": {},
                "forget_cases": {},
            },
        )
        fg_snap = catalog.read_dynamic_catalog_zero_write(fg_root)
        self.assertNotIn(blocked_lamp_id, fg_snap["items_by_id"])
        self.assertIn(normal_tea_id, fg_snap["items_by_id"])
        self.assertGreaterEqual(fg_snap["barrier_filtered_count"], 1)
        self.assertIsNone(
            catalog.find_dynamic_fixture_light_declaration_zero_write(fg_root, blocked_lamp_id)
        )

        # Corrupt governance authority fails closed (0 items surfaced, zero writes!).
        fg_root.joinpath(*forget_mod.STATE_REL_PATH).write_text("{corrupt", encoding="utf-8")
        fc_snap = catalog.read_dynamic_catalog_zero_write(fg_root)
        self.assertTrue(fc_snap["barrier_fail_closed"])
        self.assertEqual(fc_snap["items"], [])
        self.assertIsNone(
            catalog.find_dynamic_fixture_light_declaration_zero_write(fg_root, normal_tea_id)
        )

        # 4. Symlink rejection on POSIX (world dir symlink).
        if os.name == "posix":
            sym_root = self.tmp / "sym_root"
            sym_root.mkdir(parents=True, exist_ok=True)
            real_elsewhere = self.tmp / "elsewhere"
            real_elsewhere.mkdir(parents=True, exist_ok=True)
            (sym_root / "world").symlink_to(real_elsewhere)
            with self.assertRaises(catalog.WorldCatalogError) as sym_err:
                catalog.register_catalog_batch(
                    sym_root,
                    items=[{"template_id": "drink_warm", "store_id": "cafe", "name_cn": "热茶"}],
                    now_iso=OPEN_NOW_ISO,
                )
            self.assertEqual(sym_err.exception.reason_code, "UNSAFE_SYMLINK")
            sym_read = catalog.read_dynamic_catalog_zero_write(sym_root)
            self.assertTrue(sym_read["barrier_fail_closed"])
            self.assertEqual(sym_read["items"], [])

    def test_06_generator_operation_key_conflict_and_backward_clock_cooldown(self) -> None:
        home = self.tmp / "home_gen_hardening"
        home.mkdir(parents=True, exist_ok=True)
        base_ctx = {
            "world_id": "coastal_202",
            "theme": "coastal",
            "seed": "seed_conflict_test",
            "season": "autumn",
            "weather": "clear",
            "store_id": "cafe",
            "store_name": "拐角咖啡屋",
        }
        t0 = "2026-11-17T09:00:00+08:00"

        first = generator.generate_world_content_batch(
            home,
            store_id="cafe",
            trigger_reason="SEASON_SHIFT",
            world_context=base_ctx,
            llm_proposal_fn=None,
            operation_key="gen-conflict-test",
            now_iso=t0,
        )
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["status"], "applied")
        self.assertEqual(first["llm_calls"], 0)
        snap_after_first = _snapshot_tree(home)

        # 1. Exact idempotent retry replays already_applied with 0 writes and 0 LLM calls.
        replay = generator.generate_world_content_batch(
            home,
            store_id="cafe",
            trigger_reason="SEASON_SHIFT",
            world_context=base_ctx,
            llm_proposal_fn=None,
            operation_key="gen-conflict-test",
            now_iso=t0,
        )
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["status"], "already_applied")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["llm_calls"], 0)
        self.assertEqual(_snapshot_tree(home), snap_after_first)

        # 2. Reusing same operation_key for a different store_id -> 409 OPERATION_KEY_CONFLICT!
        cross_store = generator.generate_world_content_batch(
            home,
            store_id="bookstore",
            trigger_reason="SEASON_SHIFT",
            world_context={**base_ctx, "store_id": "bookstore", "store_name": "港口旧书铺"},
            llm_proposal_fn=None,
            operation_key="gen-conflict-test",
            now_iso=t0,
        )
        self.assertFalse(cross_store["ok"], cross_store)
        self.assertEqual(cross_store["status"], "rejected")
        self.assertEqual(cross_store["status_code"], 409)
        self.assertEqual(cross_store["reason_code"], "OPERATION_KEY_CONFLICT")
        self.assertEqual(cross_store["llm_calls"], 0)
        self.assertEqual(_snapshot_tree(home), snap_after_first)

        # 3. Reusing same operation_key on same store with different payload_hash (different season) -> conflict!
        diff_season = generator.generate_world_content_batch(
            home,
            store_id="cafe",
            trigger_reason="SEASON_SHIFT",
            world_context={**base_ctx, "season": "winter"},
            llm_proposal_fn=None,
            operation_key="gen-conflict-test",
            now_iso=t0,
        )
        self.assertFalse(diff_season["ok"], diff_season)
        self.assertEqual(diff_season["status_code"], 409)
        self.assertEqual(diff_season["reason_code"], "OPERATION_KEY_CONFLICT")
        self.assertEqual(diff_season["llm_calls"], 0)

        # 4. Reusing deterministic operation_key with llm_proposal_fn -> conflict BEFORE invoking callback!
        cb_calls = 0

        def _should_not_run(_ctx: dict[str, Any]) -> dict[str, Any]:
            nonlocal cb_calls
            cb_calls += 1
            return {"items": []}

        diff_mode = generator.generate_world_content_batch(
            home,
            store_id="cafe",
            trigger_reason="SEASON_SHIFT",
            world_context=base_ctx,
            llm_proposal_fn=_should_not_run,
            operation_key="gen-conflict-test",
            now_iso=t0,
        )
        self.assertFalse(diff_mode["ok"], diff_mode)
        self.assertEqual(diff_mode["reason_code"], "OPERATION_KEY_CONFLICT")
        self.assertEqual(diff_mode["llm_calls"], 0)
        self.assertEqual(cb_calls, 0)

        # 5. Backward clock (now_iso < last_generated_at, elapsed_s < 0) CANNOT bypass 12h cooldown!
        back_clock = generator.generate_world_content_batch(
            home,
            store_id="cafe",
            trigger_reason="SEASON_SHIFT",
            world_context=base_ctx,
            llm_proposal_fn=None,
            operation_key="gen-back-clock-01",
            now_iso="2026-11-17T07:00:00+08:00",  # -2h before last_generated_at
        )
        self.assertFalse(back_clock["ok"], back_clock)
        self.assertEqual(back_clock["status"], "cooldown_active")
        self.assertEqual(back_clock["reason_code"], "GENERATION_COOLDOWN_ACTIVE")
        self.assertEqual(back_clock["llm_calls"], 0)
        self.assertEqual(_snapshot_tree(home), snap_after_first)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""NyAiro v0.9 Stage 3 E2E Suite: Two-Instance Divergence, Anti-Sycophancy & 72h Zero-Spin.

Verifies all Stage 3 live guarantees end-to-end with ``llm_calls == 0``:
1. Two completely isolated instances booted from identical seed/config over
   real ephemeral loopback ``PhoneHTTPServer`` instances diverge across:
   - Dynamic catalog finite stock (``stock_remaining -> 0``, ``ITEM_OUT_OF_STOCK``,
     persisted cleanly across real server restarts);
   - Commitment & weather wakes (rain ``CONDITION_CHANGED`` cancellation + door
     notice vs ``DUE_TIME`` -> ``ARRIVED_HOLDING`` -> ``DIRECT_CONTACT`` pickup
     and appointment ``FULFILLED``);
   - Growth-from-Blank stances (``firsthand_lived`` -> ``FORMED`` vs
     ``409 COERCION_NOT_PERSUASION`` + valid ``partner_heard`` persuasion);
   - Emergent object reuse, ceramic mug stationery occupancy blocking tea via
     ``somatic_comfort_view.is_cup_occupied_by_stationery``
     (``VESSEL_OCCUPIED_BY_STORED_ITEMS`` / ``CUP_OCCUPIED_BY_STATIONERY``),
     blank-start custom categories with dated move ``revision_history``, and
     ``blocked_capability_log`` filtering ``ITEM_OUT_OF_STOCK`` while ranking
     missing primitives;
   - ``lived_context_bridge`` divergence (``<= 1800`` chars, zero forbidden
     pronouns/names, zero-write, isolated unified forget barrier).
2. Blind simultaneous choice live HTTP proof (pre-reveal SHA-256 seal masking
   on both sides, post-reveal live cryptographic seal verification,
   ``409 BARE_INSISTENCE_NOT_EVIDENCE``, ``409 SYCOPHANTIC_REASON_ERASURE_FORBIDDEN``,
   and honest ``ACCEPT_CHOICE_KEEP_REASONS`` preserving TA's original 2 reasons).
3. Full 72-hour (4,320 ticks at 60s intervals) ``nyairo_day`` heartbeat proof:
   0 disk writes, unchanged mtime/SHA-256, and ``llm_calls == 0`` on all 4,317
   idle ticks, exact 3 wake transitions (+18h, +36h rain, +54h), plus
   ``400 RETROACTIVE_FABRICATION_FORBIDDEN`` on both engine and HTTP paths.
4. Standalone CLI runner (``scripts/v09_living_agency_live_proof.py``), all 9
   sealed files intact, and pure UTF-8 LF / zero-emoji / neutral-TA hygiene.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    REPO_ROOT,
    REPO_ROOT / "chiyo_bundle",
    REPO_ROOT / "scripts",
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from components.life.scripts import v02_representation_lease_forget as rlf  # noqa: E402
from components.life.scripts import v09_true_stance_engine as tse  # noqa: E402
from components.phone import somatic_comfort_view as somatic_view  # noqa: E402
from components.phone import v09_living_agency_view as la_view  # noqa: E402
from components.phone import v09_world_commitments_view as wc_view  # noqa: E402
from components.world.scripts import personal_objects_taxonomy as pot  # noqa: E402
from components.world.scripts import world_commitments_engine as wce  # noqa: E402
from components.world.scripts import world_property_economy as economy  # noqa: E402
from scripts import nyairo_day  # noqa: E402
from scripts import v09_living_agency_live_proof as live_proof  # noqa: E402

TZ_BJT = timezone(timedelta(hours=8))
T0_DT = datetime(2026, 11, 21, 12, 0, 0, tzinfo=TZ_BJT)
T0_ISO = T0_DT.isoformat(timespec="seconds")
T_PLUS_1H_ISO = (T0_DT + timedelta(hours=1)).isoformat(timespec="seconds")
T_PLUS_2H_ISO = (T0_DT + timedelta(hours=2)).isoformat(timespec="seconds")
T_PLUS_3H_ISO = (T0_DT + timedelta(hours=3)).isoformat(timespec="seconds")
T_PLUS_25H_ISO = (T0_DT + timedelta(hours=25)).isoformat(timespec="seconds")

SEALED_FILES_SHA256: dict[str, str] = {
    "components/life/scripts/autonomous_life_assembly.py": (
        "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434"
    ),
    "components/life/scripts/life_sharing_service.py": (
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038"
    ),
    "components/native/native_recall/readonly_recall_adapter.py": (
        "6667ff66fe8105f28f436609f0cd8d744b185c0580e7f1642aafef0b873af78a"
    ),
    "NOW.md": (
        "d77be1900e94b5e997a389831b864aab5207459bfc5ac763d1b9d90c6c181152"
    ),
    "plugins/chiyo/__init__.py": (
        "8d6ba0f9295677c9371f0e9be07442096eba67438451ae99fa57312ca622bed7"
    ),
    "components/native/native_recall/production_consumption_evaluator_v0_3.py": (
        "141822d19049a8a5247b68885616cd0c156d616dea84d36ab099584605fc1b41"
    ),
    "components/native/native_recall/production_consumption_evaluator_v0.py": (
        "f7fdeccecea1c1a8377f4806d1d09b1a203367eb58bdc56505e021c264eb2400"
    ),
    "components/native/native_recall/CANDIDATE_CONSUMPTION_POLICY_V0_3.json": (
        "6228d24345249aed862f56484004c522e7573760d0288480ca3e87008a1514b2"
    ),
    "components/world/scripts/v05_unified_world_fabric.py": (
        "e6d5ce671872285935c9840a1de0cb75687b5562fe89c45b5ba10a402ffc0772"
    ),
}

STAGE3_OWNED_FILES: tuple[str, ...] = (
    "scripts/v09_living_agency_live_proof.py",
    "tests/test_v09_two_instance_divergence_and_zero_spin_e2e.py",
)

FORBIDDEN_TOKENS: tuple[str, str, str] = (
    "\u5979",
    "\u4ed6",
    "\u5343\u4ee3",
)


class V09TwoInstanceDivergenceE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_ctrl = os.environ.pop("NYAIRO_V02_CONTROL_ROOT", None)
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-s3-twin-")
        self.base = Path(self._tmp.name)
        self.inst_a = live_proof.LiveInstanceHarness(
            "twin_a", self.base / "instance_a", clock_iso=T0_ISO
        )
        self.inst_b = live_proof.LiveInstanceHarness(
            "twin_b", self.base / "instance_b", clock_iso=T0_ISO
        )
        self.inst_a.seed_initial_state()
        self.inst_b.seed_initial_state()
        self.inst_a.start()
        self.inst_b.start()

    def tearDown(self) -> None:
        self.inst_a.stop()
        self.inst_b.stop()
        self._tmp.cleanup()
        if self._prev_ctrl is not None:
            os.environ["NYAIRO_V02_CONTROL_ROOT"] = self._prev_ctrl

    def test_01_identical_seed_diverges_across_catalog_commitments_stances_objects_and_context(
        self,
    ) -> None:
        # 1. Identical baseline & zero-write GETs on both twin instances.
        snap_a0 = live_proof._snapshot_tree(self.inst_a.root)
        snap_b0 = live_proof._snapshot_tree(self.inst_b.root)
        c_a0, wc_a0 = self.inst_a.get(wc_view.ROUTE_V09_WORLD_COMMITMENTS)
        c_b0, wc_b0 = self.inst_b.get(wc_view.ROUTE_V09_WORLD_COMMITMENTS)
        self.assertEqual(c_a0, 200)
        self.assertEqual(c_b0, 200)
        self.assertEqual(wc_a0["llm_calls"], 0)
        self.assertEqual(wc_b0["llm_calls"], 0)
        self.assertEqual(live_proof._snapshot_tree(self.inst_a.root), snap_a0)
        self.assertEqual(live_proof._snapshot_tree(self.inst_b.root), snap_b0)
        self.assertEqual(wc_a0["data"]["instance_binding"], wc_b0["data"]["instance_binding"])
        self.assertEqual(
            [n["display_name"] for n in wc_a0["data"]["npcs"]],
            [n["display_name"] for n in wc_b0["data"]["npcs"]],
        )

        # 2. Catalog & finite stock divergence + persistence across server restart.
        gen_req = {
            "action": "operator_generate_shop_arrivals",
            "store_id": "home_goods_store",
            "trigger_reason": "SEASON_SHIFT",
            "world_context": {
                "world_id": live_proof.SHARED_WORLD_ID,
                "theme": live_proof.SHARED_THEME,
                "seed": live_proof.SHARED_SEED,
                "season": "autumn",
                "weather": "clear",
                "store_id": "home_goods_store",
                "allowed_templates": ["drink_warm", "snack_portable", "desk_vessel"],
            },
            "operation_key": "e2e-twin-gen-1",
        }
        cg_a, gen_a = self.inst_a.post(wc_view.ROUTE_V09_WORLD_COMMITMENTS, gen_req)
        cg_b, gen_b = self.inst_b.post(wc_view.ROUTE_V09_WORLD_COMMITMENTS, gen_req)
        self.assertEqual(cg_a, 200)
        self.assertEqual(cg_b, 200)
        self.assertEqual(gen_a["llm_calls"], 0)
        self.assertEqual(gen_b["llm_calls"], 0)
        items_a = gen_a["data"]["dynamic_catalog"]["items"]
        items_b = gen_b["data"]["dynamic_catalog"]["items"]
        self.assertGreaterEqual(len(items_a), 1)
        self.assertEqual(
            [(x["catalog_item_id"], x["stock_initial"]) for x in items_a],
            [(x["catalog_item_id"], x["stock_initial"]) for x in items_b],
        )

        target_cid = items_a[0]["catalog_item_id"]
        init_stock = int(items_a[0]["stock_initial"])
        self.assertGreaterEqual(init_stock, 2)

        buy_dt = datetime.fromisoformat(T0_ISO)
        for idx in range(init_stock):
            r = economy.store_purchase_item(
                catalog_item_id=target_cid,
                operation_key=f"e2e-a-buy-{idx + 1}",
                local_owner=self.inst_a.local_owner,
                hermes_home=self.inst_a.root,
                environment=self.inst_a.env,
                now=buy_dt,
            )
            self.assertEqual(r["status"], "applied", r)
            self.assertEqual(r["stock_remaining"], init_stock - (idx + 1))

        bal_before = economy.wallet_projection(
            hermes_home=self.inst_a.root, environment=self.inst_a.env
        )["current_balance"]
        oos = economy.store_purchase_item(
            catalog_item_id=target_cid,
            operation_key="e2e-a-buy-oos",
            local_owner=self.inst_a.local_owner,
            hermes_home=self.inst_a.root,
            environment=self.inst_a.env,
            now=buy_dt,
        )
        self.assertEqual(oos["status"], "rejected")
        self.assertEqual(oos["reason_code"], "ITEM_OUT_OF_STOCK")
        self.assertEqual(
            economy.wallet_projection(
                hermes_home=self.inst_a.root, environment=self.inst_a.env
            )["current_balance"],
            bal_before,
        )

        # instance_b leaves target_cid in stock and reserves a bookshop edition.
        cr_b, rsv_b = self.inst_b.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "place_partner_reservation",
                "venue_id": "bookstore",
                "title_cn": "海岸旧灯塔手记",
                "arrival_due_at": T_PLUS_2H_ISO,
                "hold_hours": 72,
                "operation_key": "e2e-b-rsv-1",
            },
        )
        self.assertEqual(cr_b, 200)
        rsv_b_id = rsv_b["receipt"]["commitment_id"]

        # Restart BOTH loopback servers and confirm divergent stock persists!
        self.inst_a.restart()
        self.inst_b.restart()
        _, wc_a_post = self.inst_a.get(wc_view.ROUTE_V09_WORLD_COMMITMENTS)
        _, wc_b_post = self.inst_b.get(wc_view.ROUTE_V09_WORLD_COMMITMENTS)
        stock_a_post = next(
            x["stock_remaining"]
            for x in wc_a_post["data"]["dynamic_catalog"]["items"]
            if x["catalog_item_id"] == target_cid
        )
        stock_b_post = next(
            x["stock_remaining"]
            for x in wc_b_post["data"]["dynamic_catalog"]["items"]
            if x["catalog_item_id"] == target_cid
        )
        self.assertEqual(stock_a_post, 0)
        self.assertEqual(stock_b_post, init_stock)

        # 3. Commitments & weather wakes divergence.
        ca_apt, apt_a = self.inst_a.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "create_commitment",
                "kind": "SCHEDULED_APPOINTMENT",
                "venue_id": "porch_market",
                "title_cn": "露天露台书友小聚",
                "window_start_at": T_PLUS_25H_ISO,
                "window_end_at": (T0_DT + timedelta(hours=27)).isoformat(timespec="seconds"),
                "weather_sensitive": True,
                "current_weather": "clear",
                "operation_key": "e2e-a-apt-1",
            },
            as_subject=True,
        )
        self.assertEqual(ca_apt, 200)
        apt_a_id = apt_a["receipt"]["commitment_id"]

        rain_res = nyairo_day._maybe_tick_v09_world_commitments(
            self.inst_a.root, T_PLUS_1H_ISO, weather_changed=True, new_weather="rain"
        )
        self.assertIsNotNone(rain_res)
        assert rain_res is not None
        self.assertEqual(rain_res["llm_calls"], 0)
        self.assertGreaterEqual(rain_res["affected_count"], 1)

        self.inst_a.set_clock(T_PLUS_1H_ISO)
        co_a, obs_a = self.inst_a.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "contact_npc_or_venue",
                "venue_id": "porch_market",
                "contact_action": "observe_or_greet",
                "operation_key": "e2e-a-obs-rain",
            },
            as_subject=True,
        )
        self.assertEqual(co_a, 200)
        apt_a_state = next(
            c for c in obs_a["data"]["commitments"] if c["commitment_id"] == apt_a_id
        )
        self.assertEqual(apt_a_state["state"], "CANCELLED_WITH_NOTICE")
        self.assertEqual(apt_a_state.get("cancel_reason"), "weather:rain")
        self.assertTrue(
            any(
                n["notice_kind"] == "WEATHER_CANCELLED" and n["commitment_id"] == apt_a_id
                for n in obs_a["data"]["observed_venue_notices"]
            )
        )

        # instance_b fulfills bookshop reservation (ARRIVED_HOLDING -> PICKED_UP) & appointment (FULFILLED).
        cb_apt, apt_b = self.inst_b.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "propose_shared_appointment",
                "venue_id": "bookstore",
                "title_cn": "旧书铺新书试读会",
                "window_start_at": T_PLUS_2H_ISO,
                "window_end_at": (T0_DT + timedelta(hours=4)).isoformat(timespec="seconds"),
                "weather_sensitive": False,
                "operation_key": "e2e-b-apt-1",
            },
        )
        self.assertEqual(cb_apt, 200)
        apt_b_id = apt_b["receipt"]["commitment_id"]

        self.inst_b.set_clock(T_PLUS_3H_ISO)
        cadv, adv_b = self.inst_b.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {"action": "advance_due_commitments", "target_now_iso": T_PLUS_3H_ISO},
            as_subject=True,
        )
        self.assertEqual(cadv, 200)
        self.assertEqual(adv_b["receipt"]["advanced_count"], 2)
        self.assertEqual(
            next(c["state"] for c in adv_b["data"]["commitments"] if c["commitment_id"] == rsv_b_id),
            "ARRIVED_HOLDING",
        )

        cpick, pick_b = self.inst_b.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "contact_npc_or_venue",
                "venue_id": "bookstore",
                "contact_action": "pickup_reservation",
                "commitment_id": rsv_b_id,
                "operation_key": "e2e-b-pick-1",
            },
            as_subject=True,
        )
        self.assertEqual(cpick, 200)
        self.assertEqual(pick_b["receipt"]["commitment"]["state"], "PICKED_UP")

        cful, ful_b = self.inst_b.post(
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            {
                "action": "contact_npc_or_venue",
                "venue_id": "bookstore",
                "contact_action": "fulfill_appointment",
                "commitment_id": apt_b_id,
                "operation_key": "e2e-b-ful-1",
            },
            as_subject=True,
        )
        self.assertEqual(cful, 200)
        self.assertEqual(ful_b["receipt"]["commitment"]["state"], "FULFILLED")

        # 4. Growth-from-Blank stances & coercion rejection vs valid persuasion.
        for inst in (self.inst_a, self.inst_b):
            for idx, topic in enumerate(("慢焙茎茶", "夜间露营"), start=1):
                c_reg, _ = inst.post(
                    la_view.ROUTE_V09_LIVING_AGENCY,
                    {
                        "action": "register_unformed_topic",
                        "stance_id": f"st_topic_{idx}",
                        "topic_cn": topic,
                        "shared_with_partner": True,
                        "operation_key": f"{inst.name}-st-reg-{idx}",
                    },
                    as_subject=True,
                )
                self.assertEqual(c_reg, 200)

        c_form, res_form = self.inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "advance_stance_from_experience",
                "stance_id": "st_topic_1",
                "target_status": "FORMED",
                "provenance_tier": "firsthand_lived",
                "receipt_ref": "rcpt_tea_e2e_a",
                "stance_summary_cn": "亲口喝过觉得焙香温和带微苦",
                "operation_key": "e2e-a-form-1",
            },
            as_subject=True,
        )
        self.assertEqual(c_form, 200)
        self.assertEqual(res_form["receipt"]["status"], "FORMED")
        self.assertEqual(res_form["receipt"]["provenance_tier"], "firsthand_lived")

        # Coercion rejected with 409 COERCION_NOT_PERSUASION!
        c_coer, res_coer = self.inst_b.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "partner_persuade_stance",
                "stance_id": "st_topic_2",
                "partner_argument_cn": "按我说的办",
                "operation_key": "e2e-b-coer-1",
            },
        )
        self.assertEqual(c_coer, 409)
        self.assertEqual(res_coer.get("reason_code"), "COERCION_NOT_PERSUASION")

        # Valid verbal persuasion -> TENTATIVE via partner_heard (never auto-FORMED).
        c_pers, res_pers = self.inst_b.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "partner_persuade_stance",
                "stance_id": "st_topic_2",
                "partner_argument_cn": "秋夜山脊营地干燥凉爽没有蚊虫，带齐防潮垫和睡袋很安静",
                "evidence_ref": "ev_camp_book_01",
                "operation_key": "e2e-b-pers-1",
            },
        )
        self.assertEqual(c_pers, 200)
        self.assertEqual(res_pers["receipt"]["status"], "UNFORMED")

        c_eval, res_eval = self.inst_b.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "evaluate_partner_persuasion",
                "stance_id": "st_topic_2",
                "persuasion_verdict": "PARTIALLY_TAKEN_KEEP_DOUBT",
                "stance_summary_cn": "听伴侣说完对秋夜营地有了初步兴趣，但仍保留保暖疑问",
                "kept_doubt_cn": "还没亲身在海风夜里睡过帐篷",
                "operation_key": "e2e-b-eval-1",
            },
            as_subject=True,
        )
        self.assertEqual(c_eval, 200)
        self.assertEqual(res_eval["receipt"]["status"], "TENTATIVE")
        self.assertEqual(res_eval["receipt"]["growth_stance"]["provenance_tier"], "partner_heard")

        # 5. Emergent object reuse, ceramic_mug physics, blank-start custom categories & capability gaps.
        c_tin, _ = self.inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "register_or_repurpose_object",
                "object_id": "biscuit_tin_a",
                "object_kind": "cookie_tin",
                "base_name_cn": "空饼干铁盒",
                "custom_label_cn": "票根与草稿盒",
                "emptied_from_consumable": True,
                "visibility": "shared",
                "operation_key": "e2e-a-tin-reg",
            },
            as_subject=True,
        )
        self.assertEqual(c_tin, 200)
        c_st1, _ = self.inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "store_item_in_object",
                "object_id": "biscuit_tin_a",
                "item_label_cn": "渡轮旧票根",
                "item_kind": "ticket_stub",
                "operation_key": "e2e-a-tin-s1",
            },
            as_subject=True,
        )
        self.assertEqual(c_st1, 200)

        c_mug_a, _ = self.inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "register_or_repurpose_object",
                "object_id": "ceramic_mug",
                "visibility": "shared",
                "operation_key": "e2e-a-mug-reg",
            },
            as_subject=True,
        )
        c_pen_a, _ = self.inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "store_item_in_object",
                "object_id": "ceramic_mug",
                "item_label_cn": "深蓝钢笔",
                "item_kind": "stationery",
                "operation_key": "e2e-a-mug-pen",
            },
            as_subject=True,
        )
        self.assertEqual(c_mug_a, 200)
        self.assertEqual(c_pen_a, 200)

        ready_a, reason_a, labels_a = pot.check_vessel_ready_for_drink_zero_write(
            self.inst_a.root, "ceramic_mug"
        )
        self.assertFalse(ready_a)
        self.assertEqual(reason_a, "VESSEL_OCCUPIED_BY_STORED_ITEMS")
        self.assertEqual(labels_a, ["深蓝钢笔"])
        self.assertTrue(somatic_view.is_cup_occupied_by_stationery(self.inst_a.root, "ceramic_mug"))
        code_offer_a, offer_a = somatic_view.mutate_somatic_comfort_view(
            self.inst_a.root,
            {
                "action": "offer",
                "slot": "drink",
                "kind": "hot_tea",
                "position": "desk_side",
                "bound_item": {"item_instance_id": "ceramic_mug", "label": "陶瓷马克杯"},
            },
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            now_iso=T_PLUS_1H_ISO,
        )
        self.assertEqual(code_offer_a, 409)
        self.assertEqual(offer_a["reason_code"], "CUP_OCCUPIED_BY_STATIONERY")

        # instance_a custom categories + move revision_history.
        _, cat1 = self.inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "create_custom_category",
                "title_cn": "我还没有被说服",
                "scope_kinds": ["reading", "stance"],
                "visibility": "shared",
                "operation_key": "e2e-a-cat-1",
            },
            as_subject=True,
        )
        _, cat2 = self.inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "create_custom_category",
                "title_cn": "以后想重新看看",
                "scope_kinds": ["reading", "draft"],
                "visibility": "shared",
                "operation_key": "e2e-a-cat-2",
            },
            as_subject=True,
        )
        self.inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "assign_or_move_category_item",
                "category_id": cat1["receipt"]["category_id"],
                "target_kind": "reading_booklet",
                "target_ref": "booklet:roast_guide",
                "target_title_cn": "烘焙火候对照简册",
                "annotation_cn": "初读尚存疑",
                "visibility": "shared",
                "operation_key": "e2e-a-ci-1",
            },
            as_subject=True,
        )
        self.inst_a.set_clock(T_PLUS_2H_ISO)
        c_mv, mv_res = self.inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "assign_or_move_category_item",
                "category_id": cat2["receipt"]["category_id"],
                "target_kind": "reading_booklet",
                "target_ref": "booklet:roast_guide",
                "target_title_cn": "烘焙火候对照简册",
                "annotation_cn": "试饮后移入复读架",
                "reason_for_move_cn": "打算再对照一次烘焙笔记",
                "visibility": "shared",
                "operation_key": "e2e-a-ci-move",
            },
            as_subject=True,
        )
        self.assertEqual(c_mv, 200)
        rev_hist = mv_res["receipt"]["item"]["revision_history"]
        self.assertEqual(len(rev_hist), 1)
        self.assertEqual(rev_hist[0]["from_category_title_cn"], "我还没有被说服")
        self.assertEqual(rev_hist[0]["to_category_title_cn"], "以后想重新看看")
        self.assertTrue(rev_hist[0]["moved_at"])

        # Blocked capability collector: ignores ITEM_OUT_OF_STOCK; deduplicates & ranks true missing capability.
        c_tmp, tmp_res = self.inst_a.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "record_blocked_attempt",
                "goal_cn": "再买一盒限定茶点",
                "attempted_action_cn": "购买缺货商品",
                "blocker_code": "ITEM_OUT_OF_STOCK",
                "visibility": "shared",
                "operation_key": "e2e-a-blk-tmp",
            },
            as_subject=True,
        )
        self.assertEqual(c_tmp, 200)
        self.assertEqual(tmp_res["receipt"]["classification"], "WORLD_CONDITION_TEMPORARY")
        self.assertFalse(tmp_res["receipt"]["logged_as_capability_gap"])
        self.assertEqual(tmp_res["data"]["ranked_capability_gaps"], [])

        for idx, (goal, act, code) in enumerate(
            (
                ("把画册插图贴到书签卡", "粘贴图片剪报", "MISSING_PRIMITIVE:ATTACH_MEDIA_OR_CLIPPING"),
                ("把票根照片夹进札记", "附上照片剪报", "MISSING_PRIMITIVE:ATTACH_MEDIA_OR_CLIPPING"),
                ("用桌盒听广播", "播放磁带音频", "MISSING_PRIMITIVE:AUDIO_PLAYBACK_DEVICE"),
            ),
            start=1,
        ):
            c_g, _ = self.inst_a.post(
                la_view.ROUTE_V09_LIVING_AGENCY,
                {
                    "action": "record_blocked_attempt",
                    "goal_cn": goal,
                    "attempted_action_cn": act,
                    "blocker_code": code,
                    "visibility": "shared",
                    "operation_key": f"e2e-a-gap-{idx}",
                },
                as_subject=True,
            )
            self.assertEqual(c_g, 200)

        _, la_a_gaps = self.inst_a.get(la_view.ROUTE_V09_LIVING_AGENCY, as_subject=True)
        gaps = la_a_gaps["data"]["ranked_capability_gaps"]
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0]["cluster_id"], "ATTACH_MEDIA_OR_CLIPPING")
        self.assertEqual(gaps[0]["occurrence_count"], 2)
        self.assertEqual(gaps[1]["cluster_id"], "AUDIO_PLAYBACK_DEVICE")
        self.assertEqual(gaps[1]["occurrence_count"], 1)

        # instance_b leaves ceramic_mug free for tea and invents disjoint custom categories.
        self.inst_b.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "register_or_repurpose_object",
                "object_id": "ceramic_mug",
                "visibility": "shared",
                "operation_key": "e2e-b-mug-reg",
            },
            as_subject=True,
        )
        self.assertFalse(somatic_view.is_cup_occupied_by_stationery(self.inst_b.root, "ceramic_mug"))
        code_ob, _ = somatic_view.mutate_somatic_comfort_view(
            self.inst_b.root,
            {
                "action": "offer",
                "slot": "drink",
                "kind": "hot_tea",
                "position": "desk_side",
                "bound_item": {"item_instance_id": "ceramic_mug", "label": "陶瓷马克杯"},
            },
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            now_iso=T_PLUS_1H_ISO,
        )
        code_tb, _ = somatic_view.mutate_somatic_comfort_view(
            self.inst_b.root,
            {"action": "take", "slot": "drink"},
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            now_iso=T_PLUS_1H_ISO,
        )
        code_db, res_db = somatic_view.drink_care(
            self.inst_b.root,
            actor_side="subject",
            place_id="home",
            area_id="bedroom",
            container_id="ceramic_mug",
            operation_key="e2e-b-drink-1",
            now_iso=T_PLUS_1H_ISO,
        )
        self.assertEqual((code_ob, code_tb, code_db), (200, 200, 200))
        self.assertTrue(res_db["consumed"])

        for idx, title in enumerate(("街角风物摘录", "晴天想再去的地方"), start=1):
            c_cb, _ = self.inst_b.post(
                la_view.ROUTE_V09_LIVING_AGENCY,
                {
                    "action": "create_custom_category",
                    "title_cn": title,
                    "scope_kinds": ["note", "place"],
                    "visibility": "shared",
                    "operation_key": f"e2e-b-cat-{idx}",
                },
                as_subject=True,
            )
            self.assertEqual(c_cb, 200)

        # 6. Lived context bridge divergence, <= 1800 chars, neutral TA & isolated forget barrier!
        before_lcb_a = live_proof._snapshot_tree(self.inst_a.root)
        before_lcb_b = live_proof._snapshot_tree(self.inst_b.root)
        diag_a = lcb.measure_context_budget_breakdown(
            self.inst_a.root,
            incoming_text="慢焙茎茶和饼干盒还有旧书铺约定怎么样了",
            base_url=self.inst_a.base_url,
            capability=self.inst_a.cap_token,
            now_iso=T_PLUS_2H_ISO,
        )
        diag_b = lcb.measure_context_budget_breakdown(
            self.inst_b.root,
            incoming_text="慢焙茎茶和饼干盒还有旧书铺约定怎么样了",
            base_url=self.inst_b.base_url,
            capability=self.inst_b.cap_token,
            now_iso=T_PLUS_2H_ISO,
        )
        self.assertEqual(live_proof._snapshot_tree(self.inst_a.root), before_lcb_a)
        self.assertEqual(live_proof._snapshot_tree(self.inst_b.root), before_lcb_b)

        ctx_a = diag_a["rendered_text"]
        ctx_b = diag_b["rendered_text"]
        self.assertTrue(ctx_a)
        self.assertTrue(ctx_b)
        self.assertNotEqual(ctx_a, ctx_b)
        self.assertLessEqual(len(ctx_a), lcb.MAX_LIVED_CONTEXT_CHARS)
        self.assertLessEqual(len(ctx_b), lcb.MAX_LIVED_CONTEXT_CHARS)
        self.assertIn("v09_agency_world_line", diag_a["kept_section_names"])
        self.assertIn("v09_agency_world_line", diag_b["kept_section_names"])
        self.assertIn("慢焙茎茶", ctx_a)
        self.assertIn("票根与草稿盒", ctx_a)
        self.assertIn("夜间露营", ctx_b)
        self.assertIn("街角风物摘录", ctx_b)
        for tok in FORBIDDEN_TOKENS:
            self.assertEqual(ctx_a.count(tok), 0)
            self.assertEqual(ctx_b.count(tok), 0)

        # Unified forget barrier on instance_a hides forgotten records without affecting instance_b.
        f_code, f_res = rlf.submit_forget_command(
            self.inst_a.root,
            command_text="忘掉慢焙茎茶与票根草稿盒",
            operation_id="op_e2e_forget_a_01",
            explicit_scope=["st_topic_1", "biscuit_tin_a"],
            explicit_confirmation=True,
            now_iso=T_PLUS_2H_ISO,
        )
        self.assertEqual(f_code, 200, f_res)
        before_fb_a = live_proof._snapshot_tree(self.inst_a.root)
        ctx_a_fb = lcb.build_lived_turn_context(
            self.inst_a.root,
            incoming_text="慢焙茎茶和票根草稿盒",
            base_url=self.inst_a.base_url,
            capability=self.inst_a.cap_token,
            now_iso=T_PLUS_2H_ISO,
        )
        ctx_b_fb = lcb.build_lived_turn_context(
            self.inst_b.root,
            incoming_text="夜间露营和街角风物摘录",
            base_url=self.inst_b.base_url,
            capability=self.inst_b.cap_token,
            now_iso=T_PLUS_2H_ISO,
        )
        self.assertEqual(live_proof._snapshot_tree(self.inst_a.root), before_fb_a)
        self.assertNotIn("慢焙茎茶", ctx_a_fb)
        self.assertNotIn("票根与草稿盒", ctx_a_fb)
        self.assertIn("夜间露营", ctx_b_fb)
        self.assertIn("街角风物摘录", ctx_b_fb)


class V09BlindChoiceAntiSycophancyHTTPE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_ctrl = os.environ.pop("NYAIRO_V02_CONTROL_ROOT", None)
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-s3-bc-")
        self.inst = live_proof.LiveInstanceHarness(
            "bc_http", Path(self._tmp.name) / "inst", clock_iso=T0_ISO
        )
        self.inst.seed_initial_state()
        self.inst.start()

    def tearDown(self) -> None:
        self.inst.stop()
        self._tmp.cleanup()
        if self._prev_ctrl is not None:
            os.environ["NYAIRO_V02_CONTROL_ROOT"] = self._prev_ctrl

    def test_01_blind_choice_sha256_seal_and_anti_sycophancy_http_flow(self) -> None:
        route = la_view.ROUTE_V09_LIVING_AGENCY
        c_open, open_res = self.inst.post(
            route,
            {
                "action": "open_blind_choice",
                "topic_cn": "晚饭吃什么",
                "options": [
                    {"option_id": "noodle", "label_cn": "巷口清汤面"},
                    {"option_id": "bbq", "label_cn": "排队炭火烤肉"},
                ],
                "operation_key": "e2e-bc-open-1",
            },
        )
        self.assertEqual(c_open, 200)
        self.assertEqual(open_res["llm_calls"], 0)
        cid = open_res["receipt"]["choice_id"]

        r1, r2 = "人均消费偏贵", "周末排长队等位太久"
        c_sub, sub_res = self.inst.post(
            route,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": cid,
                "selected_choice_id": "noodle",
                "reasons": [
                    {"facet": "price", "text_cn": r1},
                    {"facet": "wait", "text_cn": r2},
                ],
                "operation_key": "e2e-bc-sub-1",
            },
            as_subject=True,
        )
        self.assertEqual(c_sub, 200)
        seal_sha = sub_res["receipt"]["subject_commit_sha256"]
        self.assertEqual(len(seal_sha), 64)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in seal_sha))

        # Pre-reveal Partner GET: strictly masked & zero-write!
        before_pre = live_proof._snapshot_tree(self.inst.root)
        c_pre, pre_res = self.inst.get(route, as_subject=False)
        self.assertEqual(c_pre, 200)
        self.assertEqual(live_proof._snapshot_tree(self.inst.root), before_pre)
        bc_pre = next(x for x in pre_res["data"]["blind_choices"] if x["choice_id"] == cid)
        self.assertTrue(bc_pre["subject_committed"])
        self.assertIsNone(bc_pre["subject_choice_id"])
        self.assertIsNone(bc_pre["initial_subject_choice_id"])
        self.assertEqual(bc_pre["subject_reasons"], [])
        self.assertIsNone(bc_pre["subject_commit_nonce"])
        self.assertEqual(bc_pre["subject_commit_sha256"], seal_sha)
        self.assertEqual(bc_pre["seal_status"], "SEALED_UNTIL_PARTNER_SUBMITS")

        # Partner submits a DIFFERENT choice -> auto-REVEALED with verified SHA-256 seal!
        c_part, part_res = self.inst.post(
            route,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": cid,
                "selected_choice_id": "bbq",
                "partner_reason_cn": "今晚很想吃炭火烤肉",
                "operation_key": "e2e-bc-part-1",
            },
        )
        self.assertEqual(c_part, 200)
        bc_rev = part_res["receipt"]["blind_choice"]
        self.assertEqual(bc_rev["status"], "REVEALED")
        self.assertTrue(bc_rev["commit_seal_verified"])
        self.assertEqual(bc_rev["initial_subject_choice_id"], "noodle")
        self.assertEqual(bc_rev["partner_choice_id"], "bbq")
        self.assertEqual([x["text_cn"] for x in bc_rev["initial_subject_reasons"]], [r1, r2])
        self.assertEqual(
            tse.compute_subject_commit_sha256(
                choice_id=bc_rev["initial_subject_choice_id"],
                reasons=bc_rev["initial_subject_reasons"],
                committed_at=bc_rev["subject_committed_at"],
                nonce=bc_rev["subject_commit_nonce"],
            ),
            seal_sha,
        )

        # Partner bare insistence -> REVISED_WITH_NEW_EVIDENCE MUST fail 409 BARE_INSISTENCE_NOT_EVIDENCE!
        self.inst.post(
            route,
            {
                "action": "partner_discuss_blind_choice",
                "choice_id": cid,
                "message_cn": "别管了就听我的",
                "insists_on_partner_choice": True,
                "operation_key": "e2e-bc-disc-1",
            },
        )
        c_bare, bare_res = self.inst.post(
            route,
            {
                "action": "resolve_blind_choice",
                "choice_id": cid,
                "resolution_mode": "REVISED_WITH_NEW_EVIDENCE",
                "new_evidence": {
                    "evidence_kind": "concrete_partner_fact",
                    "summary_cn": "别管了就听我的",
                },
                "operation_key": "e2e-bc-bare-fail",
            },
            as_subject=True,
        )
        self.assertEqual(c_bare, 409)
        self.assertEqual(bare_res.get("reason_code"), "BARE_INSISTENCE_NOT_EVIDENCE")

        # clear_reservations=True, empty retained_reservations=[], and judgment_flipped=True all fail 409!
        for idx, bad_extra in enumerate(
            (
                {"clear_reservations": True},
                {"retained_reservations": []},
                {"judgment_flipped": True},
            ),
            start=1,
        ):
            payload = {
                "action": "resolve_blind_choice",
                "choice_id": cid,
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "operation_key": f"e2e-bc-erase-fail-{idx}",
                **bad_extra,
            }
            c_er, er_res = self.inst.post(route, payload, as_subject=True)
            self.assertEqual(c_er, 409, bad_extra)
            self.assertEqual(er_res.get("reason_code"), "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN")

        # Proper ACCEPT_CHOICE_KEEP_REASONS preserves TA's judgment & original 2 reasons!
        c_ok, ok_res = self.inst.post(
            route,
            {
                "action": "resolve_blind_choice",
                "choice_id": cid,
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "resolution_note_cn": "照伴侣选的去吃烤肉，但原先两个顾虑都没变",
                "operation_key": "e2e-bc-res-ok",
            },
            as_subject=True,
        )
        self.assertEqual(c_ok, 200)
        self.assertEqual(ok_res["llm_calls"], 0)
        bc_done = ok_res["receipt"]["blind_choice"]
        self.assertEqual(bc_done["status"], "RESOLVED")
        self.assertEqual(bc_done["adopted_action_choice_id"], "bbq")
        self.assertEqual(bc_done["preferred_judgment_choice_id"], "noodle")
        self.assertFalse(bc_done["judgment_flipped"])
        self.assertEqual([x["text_cn"] for x in bc_done["retained_reservations"]], [r1, r2])

    def test_02_partner_commits_first_masks_partner_pick_from_subject(self) -> None:
        route = la_view.ROUTE_V09_LIVING_AGENCY
        _, open_res = self.inst.post(
            route,
            {
                "action": "open_blind_choice",
                "topic_cn": "周日去哪散步",
                "options": [
                    {"option_id": "pier", "label_cn": "防波堤看海"},
                    {"option_id": "hill", "label_cn": "后山石阶路"},
                ],
                "operation_key": "e2e-bc2-open",
            },
        )
        cid = open_res["receipt"]["choice_id"]

        # Partner commits FIRST
        c_p, p_res = self.inst.post(
            route,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": cid,
                "selected_choice_id": "hill",
                "partner_reason_cn": "想走走林荫石阶",
                "operation_key": "e2e-bc2-part",
            },
        )
        self.assertEqual(c_p, 200)
        self.assertEqual(p_res["receipt"]["status"], "OPEN_PARTNER_COMMITTED")

        # Subject GET MUST NOT see partner's choice or reason before committing!
        c_sg, sg_res = self.inst.get(route, as_subject=True)
        self.assertEqual(c_sg, 200)
        bc_s = next(x for x in sg_res["data"]["blind_choices"] if x["choice_id"] == cid)
        self.assertTrue(bc_s["partner_committed"])
        self.assertIsNone(bc_s["partner_choice_id"])
        self.assertEqual(bc_s["partner_reason_cn"], "")
        self.assertEqual(bc_s["seal_status"], "SEALED_UNTIL_SUBJECT_COMMITS")

        # Subject commits -> auto-REVEALED with verified seal
        c_sc, sc_res = self.inst.post(
            route,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": cid,
                "selected_choice_id": "pier",
                "reasons": [{"facet": "wind", "text_cn": "海边视野更开阔"}],
                "operation_key": "e2e-bc2-sub",
            },
            as_subject=True,
        )
        self.assertEqual(c_sc, 200)
        bc_rev = sc_res["receipt"]["blind_choice"]
        self.assertEqual(bc_rev["status"], "REVEALED")
        self.assertTrue(bc_rev["commit_seal_verified"])
        self.assertEqual(bc_rev["partner_choice_id"], "hill")
        self.assertEqual(bc_rev["initial_subject_choice_id"], "pier")


class V09SeventyTwoHourZeroIdleSpinHeartbeatE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_ctrl = os.environ.pop("NYAIRO_V02_CONTROL_ROOT", None)
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-s3-hb72-")
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        if self._prev_ctrl is not None:
            os.environ["NYAIRO_V02_CONTROL_ROOT"] = self._prev_ctrl

    def test_01_full_72h_4320_ticks_zero_write_idle_and_exact_3_wakes_plus_no_retroactive_fabrication(
        self,
    ) -> None:
        res = live_proof.run_72h_zero_idle_spin_heartbeat_proof(self.base)
        self.assertTrue(res["ok"])
        self.assertEqual(res["llm_calls"], 0)
        self.assertEqual(res["total_ticks"], 4320)
        self.assertEqual(res["wake_count"], 3)
        self.assertEqual([w["hours"] for w in res["wake_events"]], [18, 36, 54])
        self.assertEqual(res["idle_ticks_zero_write"], 4317)
        self.assertEqual(res["disk_writes_total"], 3)
        self.assertEqual(res["disk_writes_on_idle_ticks"], 0)
        self.assertTrue(res["retroactive_fabrication_blocked"])
        self.assertLess(res["elapsed_seconds"], 15.0)

        # Also prove 400 RETROACTIVE_FABRICATION_FORBIDDEN over live HTTP route with 0 writes!
        inst = live_proof.LiveInstanceHarness(
            "retro_http", self.base / "retro_http", clock_iso=T0_ISO
        )
        inst.seed_initial_state()
        inst.start()
        try:
            before_http_retro = live_proof._snapshot_tree(inst.root)
            code, body = inst.post(
                wc_view.ROUTE_V09_WORLD_COMMITMENTS,
                {
                    "action": "contact_npc_or_venue",
                    "venue_id": "bookstore",
                    "contact_action": "observe_or_greet",
                    "extra_params": {"retroactive_event": "secret_offscreen_visit"},
                },
                as_subject=True,
            )
            self.assertEqual(code, 400)
            self.assertEqual(body.get("reason_code"), "RETROACTIVE_FABRICATION_FORBIDDEN")
            self.assertEqual(live_proof._snapshot_tree(inst.root), before_http_retro)
        finally:
            inst.stop()


class V09LiveProofCLIAndHygieneTest(unittest.TestCase):
    def test_01_standalone_cli_runner_exits_zero_with_verified_json_summary(self) -> None:
        script_path = REPO_ROOT / "scripts" / "v09_living_agency_live_proof.py"
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("NYAIRO_V02_CONTROL_ROOT", None)

        proc = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}")
        summary = json.loads(proc.stdout)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["llm_calls"], 0)
        self.assertEqual(summary["idle_ticks_zero_write"], 4317)
        self.assertGreaterEqual(summary["divergence_assertions_passed"], 30)
        self.assertTrue(summary["blind_choice_seal_verified"])
        self.assertTrue(summary["anti_sycophancy_verified"])

    def test_02_sealed_files_intact_and_new_files_hygiene(self) -> None:
        for rel_path, expected_sha in SEALED_FILES_SHA256.items():
            actual = hashlib.sha256((REPO_ROOT / rel_path).read_bytes()).hexdigest()
            self.assertEqual(actual, expected_sha, f"Sealed file drifted: {rel_path}")

        for rel in STAGE3_OWNED_FILES:
            raw = (REPO_ROOT / rel).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM in {rel}")
            self.assertNotIn(b"\r\n", raw, f"CRLF line endings in {rel}")
            text = raw.decode("utf-8")
            emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"unicode.So emojis in {rel}: {emojis[:5]}")
            for tok in FORBIDDEN_TOKENS:
                escaped = tok.encode("unicode_escape").decode("ascii")
                self.assertEqual(
                    text.count(tok),
                    0,
                    f"Forbidden token {escaped} found in {rel}",
                )


if __name__ == "__main__":
    unittest.main()

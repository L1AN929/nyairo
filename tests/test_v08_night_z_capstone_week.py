#!/usr/bin/env python3
"""Night Z Capstone 7-Day Multi-Domain Integration & Boundary Suite (Worker Z5A).

Verifies on ONE continuous shared root across Day 1 -> Day 7 (`llm_calls == 0`):
- Day 1: Open-counter store purchase (`"冷绿茶"` & `"海盐黄油卷"` via `shop_slot:*`) +
  `CONSUME` lived-need relief + `home_curios` / `day_timeline` / `day_story_view`
  human names + subject saves a 72h slow-letter draft (`T0`, `409 SLOW_LETTER_COOLING_PERIOD`
  before 72h, invisible to partner).
- Day 2: Partner offers physical gift `"青瓷小茶则"` (`offer_item_gift`) ->
  `collect_v02_v03_loop_candidates` (100% zero-write) emits `v03_item_gift:*` ->
  `FAILED` / forged / missing-receipt / `partner` settlement writes 0 bytes ->
  verified `APPLIED` settlement accepts & materializes `"青瓷小茶则"` onto `home_desk`
  in `v08_living_traces_view`.
- Day 3: `saeki_aoi` (`佐伯葵`) & `温井慎一` run offset non-lockstep 3h schedules ->
  partner leaves public coaster note -> daily loop `v04_cafe_resident:*` settles via
  `v04_neighborhood_living.apply_v04_neighborhood_command` + Ch.23 `concern_tinted_shelf`
  scrubs private concerns for partner while tinting for subject.
- Day 4 (`T0 + 73h`): `collect_v02_v03_loop_candidates` surfaces `v03_slow_letter_draft:*`
  (while strictly ignoring partner-authored cooling drafts!) -> verified `APPLIED`
  settlement posts the cooled slow letter into `in_transit` (`text_withheld == True`
  for partner).
- Day 5 (`T0 + 86h`): Slow letter delivered with postmark; `GAP-10` three-way storage
  contract (`PRIVATE` with `item_count=None` vs `EMPTY` vs `POPULATED`) verified.
- Day 6-7: Calm-silence & `<= 1800` char budget verified on `lived_context_bridge`,
  keeping `autonomy_line`, commitments, and `36B` boundaries un-evicted.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
WORLD_SERVICE = REPO_ROOT / "components" / "world" / "service"
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (
    str(REPO_ROOT),
    str(REPO_ROOT / "components"),
    str(WORLD_SCRIPTS),
    str(WORLD_SERVICE),
    str(LIFE_SCRIPTS),
    str(REPO_ROOT / "chiyo_bundle"),
    str(REPO_ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import state_tx_guard as _state_tx_guard  # noqa: E402,F401
from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.phone import day_story_view, day_timeline  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402
from components.phone import v08_living_traces_view as traces_view  # noqa: E402
from components.phone import window_keepsake_view as keepsake_view  # noqa: E402
from components.phone.v03_ecology_view import (  # noqa: E402
    mutate_v03_ecology_view,
    read_v03_ecology_view,
)
from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v03_economy_illness_bridge as eco_bridge  # noqa: E402
from components.world.scripts import v04_neighborhood_living as nb_engine  # noqa: E402
from components.world.scripts import v08_universal_matter as matter_engine  # noqa: E402
from components.worldgen import schema as wg_schema  # noqa: E402
from components.worldgen.generator import generate_world  # noqa: E402

import body_lived_needs as bln  # noqa: E402
import body_runtime as br  # noqa: E402
import world_action_resolver as resolver_mod  # noqa: E402
import world_body_substrate as substrate  # noqa: E402
import world_phone_shopping as phone_shopping  # noqa: E402
import world_property_economy as economy  # noqa: E402
import world_travel_delivery as travel  # noqa: E402

TZ8 = timezone(timedelta(hours=8))
FORBIDDEN_NAME = chr(0x5343) + chr(0x4EE3)
FORBIDDEN_PRONOUN = chr(0x5979)

# Continuous 7-Day timestamps on +08:00 (aligned with fastday_clock logical_checkpoint)
DAY1_SHOP_DT = datetime(2026, 11, 16, 12, 0, 0, tzinfo=TZ8)
DAY1_DRAFT_T0 = datetime(2026, 11, 16, 12, 30, 0, tzinfo=TZ8)
DAY1_EARLY_POST_DT = datetime(2026, 11, 16, 18, 0, 0, tzinfo=TZ8)

DAY2_TIDY_ISO = "2026-11-17T09:30:00+08:00"
DAY2_OFFER_ISO = "2026-11-17T10:00:00+08:00"
DAY2_SETTLE_ISO = "2026-11-17T10:20:00+08:00"

DAY3_1030_ISO = "2026-11-18T10:30:00+08:00"
DAY3_1130_ISO = "2026-11-18T11:30:00+08:00"
DAY3_1230_ISO = "2026-11-18T12:30:00+08:00"
DAY3_COASTER_ISO = "2026-11-18T13:00:00+08:00"
DAY3_SETTLE_ISO = "2026-11-18T13:15:00+08:00"

# T0 + 73h -> 2026-11-19T11:00:00+08:00
DAY4_MATURED_DT = DAY1_DRAFT_T0 + timedelta(hours=73)
DAY4_MATURED_ISO = DAY4_MATURED_DT.isoformat(timespec="seconds")
DAY4_IN_TRANSIT_DT = DAY1_DRAFT_T0 + timedelta(hours=75)
DAY4_IN_TRANSIT_ISO = DAY4_IN_TRANSIT_DT.isoformat(timespec="seconds")

# T0 + 86h -> 2026-11-20T00:00:00+08:00 (after 12h default delivery window from +73h = +85h)
DAY5_DELIVERED_DT = DAY1_DRAFT_T0 + timedelta(hours=86)
DAY5_DELIVERED_ISO = DAY5_DELIVERED_DT.isoformat(timespec="seconds")
DAY5_DRAWER_ISO = "2026-11-20T10:30:00+08:00"

DAY6_CALM_ISO = "2026-11-21T14:00:00+08:00"
DAY7_BUDGET_ISO = "2026-11-22T16:00:00+08:00"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        st = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        elif path.is_symlink():
            snap[rel] = (0, int(st.st_mtime_ns), "symlink")
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _descriptors(collected: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(desc)
        for desc in (collected.get("descriptors") or {}).values()
        if isinstance(desc, dict)
    ]


def _read_letters_zero_write(root: Path, *, now_iso: str, subject_view: bool) -> dict[str, Any]:
    return keepsake_view.letters_block(
        rows=[],
        offset="+08:00",
        clock={"source": "test_clock", "logical_at": now_iso, "reason_code": None},
        subject_view=subject_view,
        scanned=0,
        truncated=False,
        root=root,
        now=now_iso,
    )


def _permitted_cfg(root: Path, *, private_artifacts: bool = True) -> dict[str, Any]:
    return {
        "day_root": str(root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {
            "enabled": True,
            "profile_home": str(root),
            "private_artifacts": private_artifacts,
        },
    }


def _seed_embodied_world_home(
    phone_root: Path,
    *,
    pkg_dir: Path,
    world_id: str,
    at_dt: datetime,
) -> tuple[Path, dict[str, str], dict[str, Any]]:
    world_home = phone_root / "world"
    (world_home / "data" / "world").mkdir(parents=True, exist_ok=True)
    (world_home / "data" / "body").mkdir(parents=True, exist_ok=True)
    (world_home / "state").mkdir(parents=True, exist_ok=True)
    (world_home / "run").mkdir(parents=True, exist_ok=True)
    (phone_root / "phone").mkdir(parents=True, exist_ok=True)

    _write_json(
        world_home / "data" / "world" / "world_state.json",
        {
            "schema_version": "world.foundation.v0",
            "revision": 3,
            "world_id": world_id,
            "timezone": "Asia/Tokyo",
            "location": {"place_id": "convenience_store", "area_id": "convenience_store"},
            "scene": {"revision": 3, "scene_id": "convenience_store.convenience_store"},
            "facts": [],
        },
    )
    _write_json(
        world_home / "data" / "world" / "world_timeline.json",
        {
            "schema_version": "world.timeline.v1",
            "revision": 1,
            "world_id": world_id,
            "processes": [],
        },
    )
    (world_home / "data" / "life_execution.jsonl").write_text("", encoding="utf-8")
    _write_json(
        world_home / "state" / "world_object_capabilities.json",
        {
            "schema_version": substrate.CAPABILITIES_SCHEMA,
            "objects": {},
            "consumables": {},
        },
    )
    sub = substrate.initial_substrate(world_id=world_id, body_id=f"{economy.OWNER}_body")
    substrate.SubstrateStore(substrate.substrate_path(world_home)).save(sub)

    env: dict[str, str] = {
        economy.FEATURE_ENV: "1",
        economy.WORLD_FEATURE_ENV: "1",
        economy.DAILY_FEATURE_ENV: "1",
        economy.DELIVERY_FEATURE_ENV: "1",
        substrate.FEATURE_ENV: "1",
        travel.TRAVEL_FEATURE_ENV: "1",
        "CHIYO_WORLD_PACKAGE": str(pkg_dir),
    }
    economy.initialize_genesis(hermes_home=world_home, environment=env)

    body_dir = world_home / "data" / "body"
    profile = bln.build_profile(
        hunger=0.82,
        thirst=0.84,
        toilet=0.10,
        note="night_z_capstone_profile",
    )
    bln.save_profile(body_dir, profile)
    body_store = br.BodyStore(body_dir)
    body_state = br.validate_runtime(br.initial_runtime(at=at_dt - timedelta(minutes=2)))
    body_state = br.upgrade_to_v2(body_state, bln.initial_state(profile))
    body_store.save_runtime(body_state)

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
            "domain_id": "fastday:run:night-z-capstone",
            "run_id": "run:night-z-capstone",
            "logical_anchor": "2026-11-16T00:00:00+08:00",
            "logical_checkpoint": "2026-11-16T12:30:00+08:00",
            "horizon_logical": "2026-11-24T00:00:00+08:00",
        },
    )
    instance_doc: dict[str, Any] = {
        "root": str(phone_root),
        "subject": economy.OWNER,
        "world_home": str(world_home),
        "supply_socket": str(phone_root / "run" / "supply.sock"),
        "private_items_artifact_id": "artifact:private-items",
        "artifact_grant_id": "grant:night-z-capstone",
        "workspace_id": "workspace:night-z-capstone",
        "features": dict(env),
    }
    _write_json(phone_root / "instance.json", instance_doc)
    return world_home, env, instance_doc


class NightZCapstoneWeekTest(unittest.TestCase):
    """Continuous Day 1 -> Day 7 multi-domain simulation + boundary gates."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-night-z-capstone-")
        self.base_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_empty_root_zero_write_and_calm_silence_gate(self) -> None:
        empty_root = self.base_dir / "never_created_root"
        self.assertFalse(empty_root.exists())

        collected = loop_bridge.collect_v02_v03_loop_candidates(
            empty_root, now_iso=DAY1_DRAFT_T0.isoformat(timespec="seconds")
        )
        self.assertFalse(empty_root.exists(), "collect_v02_v03_loop_candidates must be 100% zero-write")
        self.assertEqual(collected["llm_calls"], 0)
        self.assertTrue(collected["zero_write"])
        self.assertEqual(collected["candidates"], [])

        diag = lcb.measure_context_budget_breakdown(
            empty_root, now_iso=DAY1_DRAFT_T0.isoformat(timespec="seconds")
        )
        self.assertFalse(empty_root.exists(), "measure_context_budget_breakdown must be 100% zero-write")
        self.assertEqual(diag["rendered_text"], "")
        self.assertEqual(diag["total_chars"], 0)
        self.assertTrue(diag["calm_silent"])
        self.assertTrue(diag["within_budget"])

    @unittest.skipUnless(os.name == "posix", "PhoneService & WorldResolver local_owner_uid require POSIX uid")
    def test_02_continuous_seven_day_multi_domain_capstone_week(self) -> None:
        docs = generate_world(seed=929711, theme="coastal_town")
        pkg_dir = self.base_dir / "world_pkg"
        wg_schema.write_package(pkg_dir, docs)

        root = self.base_dir / "shared_week_instance"
        world_home, env, instance_doc = _seed_embodied_world_home(
            root,
            pkg_dir=pkg_dir,
            world_id=docs["world.json"]["world_id"],
            at_dt=DAY1_SHOP_DT,
        )

        current_clock = [DAY1_DRAFT_T0]
        svc = phone_service.PhoneService(
            instance_doc,
            environment=env,
            world_clock=lambda: current_clock[0],
        )

        with patch.dict(os.environ, env, clear=False):
            # =================================================================
            # DAY 1: Open-counter shop_slot:* purchase ("冷绿茶" & "海盐黄油卷")
            #        + CONSUME need relief + human-name read projections
            #        + 72h slow-letter cooling draft saved by subject (T0)
            # =================================================================
            current_clock[0] = DAY1_SHOP_DT
            tea_res = economy.purchase_item(
                catalog_item_id="shop_slot:convenience_store:drink:budget",
                purchase_intent_id="op-z5a-day1-tea",
                main_self_verified=True,
                hermes_home=world_home,
                environment=env,
                now=DAY1_SHOP_DT,
                custom_item_name="冷绿茶",
            )
            roll_res = economy.purchase_item(
                catalog_item_id="shop_slot:convenience_store:food:budget",
                purchase_intent_id="op-z5a-day1-roll",
                main_self_verified=True,
                hermes_home=world_home,
                environment=env,
                now=DAY1_SHOP_DT + timedelta(minutes=2),
                custom_item_name="海盐黄油卷",
            )
            self.assertEqual(tea_res["status"], "applied", tea_res)
            self.assertEqual(roll_res["status"], "applied", roll_res)
            tea_id = tea_res["item_instance_id"]
            roll_id = roll_res["item_instance_id"]

            self.assertEqual(
                phone_shopping.materialize_owned_item(
                    hermes_home=world_home, item_instance_id=tea_id, environment=env
                )["status"],
                "materialized",
            )
            self.assertEqual(
                phone_shopping.materialize_owned_item(
                    hermes_home=world_home, item_instance_id=roll_id, environment=env
                )["status"],
                "materialized",
            )

            # Verify zero-write read projections surface human names ("冷绿茶" & "海盐黄油卷")
            snap_day1_reads = _snapshot_tree(root)
            curios = svc.home_curios()
            self.assertTrue(curios["ok"], curios)
            curios_by_id = {r["item_instance_id"]: r for r in curios["data"]["items"]}
            self.assertEqual(curios_by_id[tea_id]["name"], "冷绿茶")
            self.assertEqual(curios_by_id[roll_id]["name"], "海盐黄油卷")

            tl = svc.day_timeline(subject_view=False)
            self.assertTrue(tl["ok"], tl)
            wallet_tl = [
                e for e in tl["data"]["entries"] if e.get("kind") == day_timeline.KIND_WALLET
            ]
            self.assertEqual({e.get("item_name") for e in wallet_tl}, {"冷绿茶", "海盐黄油卷"})

            story = day_story_view.read_today_story_zero_write(root, day="2026-11-16")
            self.assertTrue(story["ok"], story)
            self.assertEqual(story["llm_calls"], 0)
            story_titles = [it["title"] for it in story["data"]["story_items"]]
            self.assertIn("添置了「冷绿茶」", story_titles)
            self.assertIn("添置了「海盐黄油卷」", story_titles)
            self.assertNotIn("shop_slot:", json.dumps(story, ensure_ascii=False))
            self.assertEqual(_snapshot_tree(root), snap_day1_reads, "Day 1 read projections must be zero-write")

            # CONSUME both items via WorldResolver -> real thirst & hunger relief
            resolver = resolver_mod.WorldResolver(world_home, environment=env, now=lambda: DAY1_SHOP_DT)
            consume_tea = resolver.resolve(
                {
                    "action": "CONSUME",
                    "execution_id": "exec-z5a-consume-tea",
                    "actor_id": economy.OWNER,
                    "body_id": f"{economy.OWNER}_body",
                    "authority": resolver_mod.AUTHORITY_VERIFIED_MAIN_SELF,
                    "confirm_apply": True,
                    "observed_dependencies": resolver._dependencies(),
                    "params": {"object_id": tea_id},
                }
            )
            self.assertEqual(consume_tea["status"], "applied", consume_tea)
            self.assertGreater(consume_tea["lived_need_effects"]["thirst"], 0.4)

            consume_roll = resolver.resolve(
                {
                    "action": "CONSUME",
                    "execution_id": "exec-z5a-consume-roll",
                    "actor_id": economy.OWNER,
                    "body_id": f"{economy.OWNER}_body",
                    "authority": resolver_mod.AUTHORITY_VERIFIED_MAIN_SELF,
                    "confirm_apply": True,
                    "observed_dependencies": resolver._dependencies(),
                    "params": {"object_id": roll_id},
                }
            )
            self.assertEqual(consume_roll["status"], "applied", consume_roll)
            self.assertGreater(consume_roll["lived_need_effects"]["hunger"], 0.4)

            # Subject saves a slow-letter draft at T0 (2026-11-16T10:00:00+08:00)
            current_clock[0] = DAY1_DRAFT_T0
            subj_draft_title = "海堤长椅慢信底稿"
            subj_draft_body = "便利店买完冷绿茶走回巷口，先把今天风声记在信纸上。"
            save_draft_res = svc.home_slow_letter_draft_action(
                action="save_slow_letter_draft",
                actor_side="subject",
                payload={
                    "title": subj_draft_title,
                    "body": subj_draft_body,
                    "postmark_place": "海岬老邮筒",
                    "operation_key": "op-z5a-day1-draft",
                },
            )
            self.assertTrue(save_draft_res["ok"], save_draft_res)
            subj_draft_id = str(save_draft_res["data"]["draft_id"])
            self.assertEqual(
                save_draft_res["data"]["draft"]["cools_until"],
                "2026-11-19T12:30:00+08:00",
            )

            # Premature post before 72h -> 409 SLOW_LETTER_COOLING_PERIOD (zero disk write!)
            current_clock[0] = DAY1_EARLY_POST_DT
            snap_pre_early = _snapshot_tree(root)
            early_post = svc.home_slow_letter_draft_action(
                action="post_drafted_slow_letter",
                actor_side="subject",
                payload={"draft_id": subj_draft_id},
            )
            self.assertFalse(early_post["ok"], early_post)
            self.assertEqual(early_post["http_status"], 409)
            self.assertEqual(early_post["reason_code"], "SLOW_LETTER_COOLING_PERIOD")
            self.assertEqual(_snapshot_tree(root), snap_pre_early)

            # Partner cannot see subject's cooling draft (0 leak, zero-write GET)
            partner_letters_d1 = _read_letters_zero_write(
                root, now_iso=DAY1_EARLY_POST_DT.isoformat(timespec="seconds"), subject_view=False
            )
            self.assertEqual(partner_letters_d1["own_slow_letter_drafts"], [])
            self.assertEqual(partner_letters_d1["own_slow_letter_draft_count"], 0)
            p_d1_dump = json.dumps(partner_letters_d1, ensure_ascii=False)
            self.assertNotIn(subj_draft_title, p_d1_dump)
            self.assertNotIn(subj_draft_body, p_d1_dump)
            self.assertNotIn(subj_draft_id, p_d1_dump)
            self.assertEqual(_snapshot_tree(root), snap_pre_early)

            # While still cooling (<72h), neither daily loop nor lived_context surfaces the draft
            d1_collected = loop_bridge.collect_v02_v03_loop_candidates(
                root, now_iso=DAY1_EARLY_POST_DT.isoformat(timespec="seconds")
            )
            self.assertEqual(
                [
                    d
                    for d in _descriptors(d1_collected)
                    if str(d.get("source_ref") or "").startswith("v03_slow_letter_draft:")
                ],
                [],
            )
            d1_ctx = lcb.build_lived_turn_context(
                root,
                _permitted_cfg(root),
                now_iso=DAY1_EARLY_POST_DT.isoformat(timespec="seconds"),
            )
            self.assertNotIn("慢信草稿", d1_ctx)

            # =================================================================
            # DAY 2: Partner offers physical gift "青瓷小茶则" (offer_item_gift)
            #        -> zero-write collect emits v03_item_gift:* -> FAILED/partner
            #        settlements write 0 bytes -> APPLIED materializes onto home_desk
            # =================================================================
            # Free 1 slot on home_desk by tucking pen_01 into ceramic_cup_01 so home_desk has room
            tidy_rcpt = matter_engine.compose_open_act(
                root,
                {
                    "intent_cn": "把桌面一支木杆铅笔收进粗陶杯腾出一格空位",
                    "steps": [
                        {
                            "op": "PLACE_OR_MOVE",
                            "object_id": "pen_01",
                            "target_parent_id": "ceramic_cup_01",
                        }
                    ],
                },
                now_iso=DAY2_TIDY_ISO,
            )
            self.assertEqual(tidy_rcpt["status"], "applied", tidy_rcpt)

            _, eco_before_gift = read_v03_ecology_view(
                root, has_subject_capability=True, now_iso=DAY2_OFFER_ISO
            )
            wallet_before_gift = eco_before_gift["data"]["economy"]["subject_personal_cents"]

            c_offer, r_offer = mutate_v03_ecology_view(
                root,
                {
                    "action": "offer_item_gift",
                    "item_label_cn": "青瓷小茶则",
                    "item_kind": "utensil",
                    "estimated_jpy": 1800,
                    "target_zone_id": "desk_surface",
                    "note": "路过器物店看到的小茶则，放你桌上随手用。",
                    "operation_key": "op-z5a-day2-offer-gift",
                },
                actor_side="partner",
                now_iso=DAY2_OFFER_ISO,
            )
            self.assertEqual(c_offer, 200, r_offer)
            self.assertEqual(r_offer["llm_calls"], 0)
            gift_id = str(r_offer["receipt"]["gift_id"])

            # lived_context_bridge surfaces compact neutral single-clause cue with 0 disk writes
            snap_d2 = _snapshot_tree(root)
            d2_ctx = lcb.build_lived_turn_context(
                root, _permitted_cfg(root), now_iso=DAY2_SETTLE_ISO
            )
            self.assertEqual(_snapshot_tree(root), snap_d2, "lived_context_bridge must be zero-write")
            self.assertIn("待决小礼物：「青瓷小茶则」（可收可拒零负担）", d2_ctx)
            self.assertLessEqual(len(d2_ctx), lcb.MAX_LIVED_CONTEXT_CHARS)

            # collect_v02_v03_loop_candidates emits v03_item_gift:* with 100% zero disk writes
            d2_collected = loop_bridge.collect_v02_v03_loop_candidates(
                root, now_iso=DAY2_SETTLE_ISO
            )
            self.assertEqual(_snapshot_tree(root), snap_d2, "collect_v02_v03_loop_candidates must be zero-write")
            self.assertEqual(d2_collected["llm_calls"], 0)
            gift_descs = [
                d
                for d in _descriptors(d2_collected)
                if str(d.get("source_ref") or "").startswith("v03_item_gift:")
            ]
            self.assertEqual(len(gift_descs), 1, d2_collected)
            gift_event = loop_bridge.v02_v03_round_event_for_selected_candidate(
                d2_collected, gift_descs[0]["source_ref"], decision_id="dec-z5a-day2-gift"
            )
            self.assertIsNotNone(gift_event)
            assert gift_event is not None

            # FAILED / forged / missing-receipt / partner settlements write 0 bytes!
            for bad_override in (
                {"effect_state": "FAILED"},
                {"actor_side": "partner"},
                {"forged_receipt": True},
                {"receipt_origin": "forged"},
                {"receipt_ref": ""},
            ):
                bad_ev = dict(gift_event, **bad_override)
                bad_res = loop_bridge.settle_v02_v03_round_consequences(
                    root, bad_ev, now_iso=DAY2_SETTLE_ISO
                )
                self.assertFalse(bad_res["settled"], (bad_override, bad_res))
                self.assertEqual(bad_res["optimistic_writes"], 0)
                self.assertEqual(_snapshot_tree(root), snap_d2, f"0 writes violated for {bad_override}")

            # Verified APPLIED settlement accepts & materializes "青瓷小茶则" onto home_desk!
            d2_settled = loop_bridge.settle_v02_v03_round_consequences(
                root, gift_event, now_iso=DAY2_SETTLE_ISO
            )
            self.assertTrue(d2_settled["ok"], d2_settled)
            self.assertTrue(d2_settled["settled"], d2_settled)
            self.assertEqual(d2_settled["status"], "APPLIED")
            self.assertEqual(d2_settled["llm_calls"], 0)
            self.assertIn("v03_ecology.item_gift_escrow", d2_settled["slices_updated"])
            self.assertIn("world.v08_matter_space", d2_settled["slices_updated"])
            mat_oid = str(d2_settled["details"]["item_gift"]["materialized_object_id"])
            self.assertTrue(mat_oid)

            # Idempotent replay on same operation_key writes no second object
            d2_replay = loop_bridge.settle_v02_v03_round_consequences(
                root, gift_event, now_iso=DAY2_SETTLE_ISO
            )
            self.assertTrue(d2_replay["already_applied"], d2_replay)

            c_tr_d2, traces_d2 = traces_view.read_v08_living_traces_view(
                root, has_subject_capability=True, now_iso=DAY2_SETTLE_ISO
            )
            self.assertEqual(c_tr_d2, 200)
            tea_scoop_objs = [
                obj
                for obj in traces_d2["data"]["matter_space"]["objects"]
                if obj["label_cn"] == "青瓷小茶则"
            ]
            self.assertEqual(len(tea_scoop_objs), 1, tea_scoop_objs)
            self.assertEqual(tea_scoop_objs[0]["object_id"], mat_oid)
            self.assertEqual(tea_scoop_objs[0]["parent_id"], "home_desk")

            _, eco_after_gift = read_v03_ecology_view(
                root, has_subject_capability=True, now_iso=DAY2_SETTLE_ISO
            )
            self.assertEqual(
                eco_after_gift["data"]["economy"]["subject_personal_cents"],
                wallet_before_gift,
            )
            escrow_after = next(
                g for g in eco_after_gift["data"]["economy"]["item_gift_escrow"] if g["gift_id"] == gift_id
            )
            self.assertEqual(escrow_after["status"], "ACCEPTED")
            self.assertEqual(escrow_after["omission_debt"], 0)
            self.assertFalse(escrow_after["intimacy_bearing"])

            # =================================================================
            # DAY 3: saeki_aoi (佐伯葵) & 温井慎一 offset non-lockstep 3h schedules
            #        + partner leaves public coaster note -> v04_cafe_resident:* settles
            #        + Ch.23 concern_tinted_shelf scrubs partner & tints subject
            # =================================================================
            snap_d3_sched = _snapshot_tree(root)
            _, nb_1030 = read_v04_neighborhood_view(
                root, has_subject_capability=True, now_iso=DAY3_1030_ISO
            )
            _, nb_1130 = read_v04_neighborhood_view(
                root, has_subject_capability=True, now_iso=DAY3_1130_ISO
            )
            _, nb_1230 = read_v04_neighborhood_view(
                root, has_subject_capability=True, now_iso=DAY3_1230_ISO
            )
            self.assertEqual(_snapshot_tree(root), snap_d3_sched, "schedule reads must be zero-write")

            aoi_1030 = nb_1030["data"]["second_resident"]
            nukui_1030 = nb_1030["data"]["bookstore"]["shopkeeper_life"]
            self.assertEqual(aoi_1030["resident_id"], "saeki_aoi")
            self.assertEqual(aoi_1030["resident_name"], "佐伯葵")
            self.assertEqual(nb_1030["data"]["independent_residents"][0]["shopkeeper_name"], "温井慎一")
            # 10:30 -> 11:30: Saeki Aoi rotates phase, Nukui Shinichi stays in 09:00-12:00 slot
            self.assertEqual(aoi_1030["current_phase"], "morning_roast")
            self.assertEqual(nb_1130["data"]["second_resident"]["current_phase"], "counter_pour_over")
            self.assertEqual(
                nukui_1030["current_task"],
                nb_1130["data"]["bookstore"]["shopkeeper_life"]["current_task"],
            )
            # 11:30 -> 12:30: Nukui Shinichi rotates task, Saeki Aoi stays in counter_pour_over
            self.assertNotEqual(
                nb_1130["data"]["bookstore"]["shopkeeper_life"]["current_task"],
                nb_1230["data"]["bookstore"]["shopkeeper_life"]["current_task"],
            )
            self.assertEqual(
                nb_1130["data"]["second_resident"]["current_phase"],
                nb_1230["data"]["second_resident"]["current_phase"],
            )

            # Partner leaves public coaster note at cafe counter
            c_status, c_res, _ = nb_engine.apply_v04_neighborhood_command(
                root,
                {
                    "action": "interact_cafe_resident",
                    "operation": "leave_cafe_coaster_note",
                    "coaster_note": "今天新烘的浅烘豆很香，路过可以闻闻看。",
                    "operation_key": "op-z5a-day3-partner-coaster",
                },
                actor_side="partner",
                now_iso=DAY3_COASTER_ISO,
            )
            self.assertEqual(c_status, "applied", c_res)
            coaster_id = str(c_res["coaster_id"])

            snap_d3_collect = _snapshot_tree(root)
            d3_collected = loop_bridge.collect_v02_v03_loop_candidates(
                root, now_iso=DAY3_SETTLE_ISO
            )
            self.assertEqual(_snapshot_tree(root), snap_d3_collect, "Day 3 collect must be zero-write")
            cafe_descs = [
                d
                for d in _descriptors(d3_collected)
                if str(d.get("source_ref") or "").startswith("v04_cafe_resident:")
            ]
            self.assertEqual(len(cafe_descs), 1, d3_collected)
            self.assertTrue(cafe_descs[0]["requires_outing"])
            self.assertIn(coaster_id, cafe_descs[0]["source_ref"])

            cafe_event = loop_bridge.v02_v03_round_event_for_selected_candidate(
                d3_collected, cafe_descs[0]["source_ref"], decision_id="dec-z5a-day3-cafe"
            )
            self.assertIsNotNone(cafe_event)
            assert cafe_event is not None

            # Verify settlement calls v04_neighborhood_living.apply_v04_neighborhood_command
            with patch.object(
                nb_engine,
                "apply_v04_neighborhood_command",
                wraps=nb_engine.apply_v04_neighborhood_command,
            ) as spy_apply_nb:
                d3_settled = loop_bridge.settle_v02_v03_round_consequences(
                    root, cafe_event, now_iso=DAY3_SETTLE_ISO
                )
                self.assertEqual(spy_apply_nb.call_count, 1)
                _, call_args, call_kwargs = spy_apply_nb.mock_calls[0]
                self.assertEqual(call_args[1]["action"], "interact_cafe_resident")
                self.assertEqual(call_kwargs["actor_side"], "subject")

            self.assertTrue(d3_settled["settled"], d3_settled)
            self.assertEqual(d3_settled["status"], "APPLIED")
            self.assertIn("v04_neighborhood", d3_settled["slices_updated"])

            # Once settled, the acknowledged coaster note is not re-emitted
            d3_after_collect = loop_bridge.collect_v02_v03_loop_candidates(
                root, now_iso=DAY3_SETTLE_ISO
            )
            self.assertEqual(
                [
                    d
                    for d in _descriptors(d3_after_collect)
                    if str(d.get("source_ref") or "").startswith("v04_cafe_resident:")
                ],
                [],
            )

            # Ch.23 concern_tinted_shelf: seed private inner concern, verify tint for subject & scrub for partner
            secret_concern = "十字隔板折边偏紧怎么修补"
            ep_view.mutate_v04_elastic_play_view(
                root,
                {
                    "action": "set_day_direction",
                    "direction": secret_concern,
                    "current_focus": secret_concern,
                },
                actor_side="subject",
                now_iso=DAY3_SETTLE_ISO,
            )
            snap_d3_shelf = _snapshot_tree(root)
            _, nb_subj = read_v04_neighborhood_view(
                root, has_subject_capability=True, now_iso=DAY3_SETTLE_ISO
            )
            _, nb_part = read_v04_neighborhood_view(
                root, has_subject_capability=False, now_iso=DAY3_SETTLE_ISO
            )
            self.assertEqual(_snapshot_tree(root), snap_d3_shelf, "concern_tinted_shelf GET must be zero-write")

            shelf_subj = nb_subj["data"]["concern_tinted_shelf"]
            shelf_part = nb_part["data"]["concern_tinted_shelf"]
            self.assertIs(shelf_subj["private_concerns_scrubbed"], False)
            self.assertIn(
                "book_paper_joinery",
                {row["book_id"] for row in shelf_subj["noticed_items"]},
            )
            self.assertIs(shelf_part["private_concerns_scrubbed"], True)
            self.assertEqual(shelf_part["active_concerns"], [])
            self.assertEqual(shelf_part["noticed_items"], [])
            self.assertNotIn(secret_concern, json.dumps(nb_part, ensure_ascii=False))

            # =================================================================
            # DAY 4 (+73h): Partner also saves a cooling draft (anti-spoiler test!)
            #               -> collect & lived_context surface ONLY subject's matured
            #               draft -> APPLIED settlement posts into in_transit
            # =================================================================
            current_clock[0] = DAY4_MATURED_DT
            partner_secret_title = "伴侣未投递私密慢信底稿"
            partner_secret_body = "这封伴侣还在修改的草稿绝不能提前出现在上下文或候选里。"
            p_draft_res = keepsake_view.save_slow_letter_draft(
                root,
                actor_side="partner",
                title=partner_secret_title,
                body=partner_secret_body,
                postmark_place="街角邮筒",
                now=DAY4_MATURED_ISO,
                operation_key="op-z5a-day4-partner-draft",
            )
            partner_draft_id = str(p_draft_res["draft_id"])

            snap_d4 = _snapshot_tree(root)
            d4_ctx_subj = lcb.build_lived_turn_context(
                root, _permitted_cfg(root, private_artifacts=True), now_iso=DAY4_MATURED_ISO
            )
            d4_ctx_part = lcb.build_lived_turn_context(
                root, _permitted_cfg(root, private_artifacts=False), now_iso=DAY4_MATURED_ISO
            )
            self.assertEqual(_snapshot_tree(root), snap_d4, "Day 4 context reads must be zero-write")
            self.assertIn(f"慢信草稿「{subj_draft_title}」已静置满72h可投递", d4_ctx_subj)
            self.assertNotIn(partner_secret_title, d4_ctx_subj)
            self.assertNotIn(partner_secret_body, d4_ctx_subj)
            self.assertNotIn(subj_draft_title, d4_ctx_part)
            self.assertNotIn(partner_secret_title, d4_ctx_part)

            d4_collected = loop_bridge.collect_v02_v03_loop_candidates(
                root, now_iso=DAY4_MATURED_ISO
            )
            self.assertEqual(_snapshot_tree(root), snap_d4, "Day 4 collect must be zero-write")
            draft_descs = [
                d
                for d in _descriptors(d4_collected)
                if str(d.get("source_ref") or "").startswith("v03_slow_letter_draft:")
            ]
            self.assertEqual(len(draft_descs), 1, d4_collected)
            self.assertEqual(draft_descs[0]["draft_id"], subj_draft_id)
            self.assertNotIn(partner_draft_id, json.dumps(d4_collected["candidates"], ensure_ascii=False))
            self.assertNotIn(partner_secret_title, json.dumps(d4_collected["descriptors"], ensure_ascii=False))

            draft_event = loop_bridge.v02_v03_round_event_for_selected_candidate(
                d4_collected, draft_descs[0]["source_ref"], decision_id="dec-z5a-day4-post"
            )
            self.assertIsNotNone(draft_event)
            assert draft_event is not None

            d4_settled = loop_bridge.settle_v02_v03_round_consequences(
                root, draft_event, now_iso=DAY4_MATURED_ISO
            )
            self.assertTrue(d4_settled["settled"], d4_settled)
            self.assertEqual(d4_settled["status"], "APPLIED")
            self.assertIn("window_keepsakes.slow_letters", d4_settled["slices_updated"])
            posted_letter_id = str(d4_settled["details"]["slow_letter_post"]["letter_id"])

            # While in transit (T0 + 75h): partner sees in_transit with text_withheld == True
            snap_d4_transit = _snapshot_tree(root)
            p_letters_transit = _read_letters_zero_write(
                root, now_iso=DAY4_IN_TRANSIT_ISO, subject_view=False
            )
            self.assertEqual(_snapshot_tree(root), snap_d4_transit, "in_transit GET must be zero-write")
            transit_row = next(
                r for r in p_letters_transit["in_transit"] if r["letter_id"] == posted_letter_id
            )
            self.assertIs(transit_row["text_withheld"], True)
            self.assertIsNone(transit_row["body"])
            self.assertNotIn(subj_draft_body, json.dumps(p_letters_transit, ensure_ascii=False))

            # =================================================================
            # DAY 5 (+86h): Slow letter delivered with postmark; GAP-10 three-way
            #               storage contract (PRIVATE item_count=None vs EMPTY vs POPULATED)
            # =================================================================
            snap_d5_deliv = _snapshot_tree(root)
            p_letters_deliv = _read_letters_zero_write(
                root, now_iso=DAY5_DELIVERED_ISO, subject_view=False
            )
            self.assertEqual(_snapshot_tree(root), snap_d5_deliv, "delivered GET must be zero-write")
            deliv_row = next(
                r for r in p_letters_deliv["delivered"] if r["letter_id"] == posted_letter_id
            )
            self.assertEqual(deliv_row["title"], subj_draft_title)
            self.assertEqual(deliv_row["body"], subj_draft_body)
            self.assertEqual(deliv_row["postmark_place"], "海岬老邮筒")
            self.assertIs(deliv_row["text_withheld"], False)

            # GAP-10 three-way storage contract before & after populating subject_private_drawer
            _, tr_p_before = traces_view.read_v08_living_traces_view(
                root, has_subject_capability=False, now_iso=DAY5_DRAWER_ISO
            )
            _, tr_s_before = traces_view.read_v08_living_traces_view(
                root, has_subject_capability=True, now_iso=DAY5_DRAWER_ISO
            )
            p_bounds_before = {b["zone_id"]: b for b in tr_p_before["data"]["zone_boundary_states"]}
            s_bounds_before = {b["zone_id"]: b for b in tr_s_before["data"]["zone_boundary_states"]}

            self.assertEqual(p_bounds_before["subject_private_drawer"]["storage_status"], "PRIVATE")
            self.assertIsNone(p_bounds_before["subject_private_drawer"]["item_count"])
            self.assertIsNone(p_bounds_before["subject_private_drawer"]["visible_item_count"])
            self.assertIsNone(p_bounds_before["subject_private_drawer"]["updated_at"])
            self.assertEqual(p_bounds_before["dining_table"]["storage_status"], "EMPTY")
            self.assertEqual(p_bounds_before["dining_table"]["item_count"], 0)
            self.assertEqual(p_bounds_before["home_desk"]["storage_status"], "POPULATED")
            self.assertGreater(p_bounds_before["home_desk"]["item_count"], 0)
            self.assertEqual(s_bounds_before["subject_private_drawer"]["storage_status"], "EMPTY")
            self.assertEqual(s_bounds_before["subject_private_drawer"]["item_count"], 0)

            matter_engine.compose_open_act(
                root,
                {
                    "intent_cn": "把私密旧书签与手写小纸条收进私人抽屉",
                    "steps": [
                        {
                            "op": "INTRODUCE_ITEM",
                            "object_id": "priv_bookmark_z5a",
                            "label_cn": "夹着干花的私人书签",
                            "item_kind": "keepsake",
                            "target_parent_id": "subject_private_drawer",
                        },
                        {
                            "op": "INTRODUCE_ITEM",
                            "object_id": "priv_slip_z5a",
                            "label_cn": "不对外展开的手写纸条",
                            "item_kind": "paper",
                            "target_parent_id": "subject_private_drawer",
                            "paper_like": True,
                        },
                    ],
                },
                now_iso=DAY5_DRAWER_ISO,
            )

            snap_d5_traces = _snapshot_tree(root)
            _, tr_p_after = traces_view.read_v08_living_traces_view(
                root, has_subject_capability=False, now_iso=DAY5_DRAWER_ISO
            )
            _, tr_s_after = traces_view.read_v08_living_traces_view(
                root, has_subject_capability=True, now_iso=DAY5_DRAWER_ISO
            )
            self.assertEqual(_snapshot_tree(root), snap_d5_traces, "living_traces GET must be zero-write")
            p_bounds_after = {b["zone_id"]: b for b in tr_p_after["data"]["zone_boundary_states"]}
            s_bounds_after = {b["zone_id"]: b for b in tr_s_after["data"]["zone_boundary_states"]}
            self.assertEqual(
                p_bounds_after["subject_private_drawer"],
                p_bounds_before["subject_private_drawer"],
                "partner PRIVATE boundary must leak zero item_count or mtime change",
            )
            self.assertEqual(s_bounds_after["subject_private_drawer"]["storage_status"], "POPULATED")
            self.assertEqual(s_bounds_after["subject_private_drawer"]["item_count"], 2)

            # =================================================================
            # DAY 6-7: Calm-silence (0 extra chars for partner draft / accepted gift)
            #          & <= 1800 char budget keeping autonomy, commitments, 36B intact
            # =================================================================
            # Even after +100h, partner's matured cooling draft MUST NEVER leak into subject context!
            snap_d6 = _snapshot_tree(root)
            d6_diag = lcb.measure_context_budget_breakdown(
                root, _permitted_cfg(root), now_iso=DAY6_CALM_ISO
            )
            self.assertEqual(_snapshot_tree(root), snap_d6, "Day 6 budget measure must be zero-write")
            self.assertTrue(d6_diag["within_budget"])
            self.assertLessEqual(d6_diag["total_chars"], lcb.MAX_LIVED_CONTEXT_CHARS)
            self.assertEqual(d6_diag["per_section_chars"].get("interactive_line", 0), 0)
            self.assertNotIn("待决小礼物", d6_diag["rendered_text"])
            self.assertNotIn("慢信草稿", d6_diag["rendered_text"])
            self.assertNotIn(partner_secret_title, d6_diag["rendered_text"])

            # Day 7 crowded 1800-char pressure: seed active refusal lock (autonomy_line + commitment),
            # pending gift + matured subject draft (interactive_line), 36B boundary, and heavy flavor/hint
            _write_json(
                root / "phone" / "autonomy_rhythm.json",
                {
                    "refusal_guard": {
                        "active_locks": [
                            {
                                "lock_id": "lock_z5a_01",
                                "topic_label": "深夜临时加急外包杂务",
                                "topic_key": "深夜临时加急外包杂务",
                                "stance": "REFUSED_FIRM",
                                "expires_at": "2026-11-25T10:00:00+08:00",
                            }
                        ]
                    }
                },
            )
            _write_json(
                root / "phone" / "emotional_tension.json",
                {
                    "tension_summary": {"subject_is_busy": True},
                    "schedule": {"availability_summary": "正在整理本周长稿终校"},
                },
            )
            _write_json(
                root / "phone" / "wardrobe.json",
                {"selection": {"homewear": {"style": "浅灰羊绒开衫"}, "hairband_style": "木簪"}},
            )
            # Offer a Day 7 non-expired item gift so interactive_line is active under heavy pressure
            eco_doc_raw = json.loads((root / "phone" / "v03_ecology.json").read_text(encoding="utf-8"))
            eco_doc_raw["economy"].setdefault("item_gift_escrow", {})["item_gift_d7"] = {
                "gift_id": "item_gift_d7",
                "item_label_cn": "黄铜镇纸",
                "item_kind": "keepsake",
                "status": "OFFERED",
                "offered_at": "2026-11-22T12:00:00+08:00",
                "expires_at": "2026-11-25T12:00:00+08:00",
                "omission_debt": 0,
            }
            _write_json(root / "phone" / "v03_ecology.json", eco_doc_raw)

            heavy_hint = "连贯背景记录" * 160
            snap_d7 = _snapshot_tree(root)
            d7_diag = lcb.measure_context_budget_breakdown(
                root,
                _permitted_cfg(root),
                ephemeral_hint=heavy_hint,
                now_iso=DAY7_BUDGET_ISO,
            )
            self.assertEqual(_snapshot_tree(root), snap_d7, "Day 7 crowded budget check must be zero-write")
            self.assertTrue(d7_diag["within_budget"])
            self.assertLessEqual(d7_diag["total_chars"], 1800)
            kept = d7_diag["kept_section_names"]
            self.assertIn("compact_situation_line", kept)
            self.assertIn("autonomy_line", kept)
            self.assertIn("intimacy_line", kept)
            self.assertIn("interactive_line", kept)
            self.assertIn("待决小礼物：「黄铜镇纸」（可收可拒零负担）", d7_diag["rendered_text"])

    def test_03_expired_gift_closed_cafe_illness_gate_and_anti_spoiler_edges(self) -> None:
        root = self.base_dir / "edge_gates_root"
        t_offer = "2026-11-16T10:00:00+08:00"
        t_expired = "2026-11-19T12:00:00+08:00"  # +74h > 72h TTL
        t_closed = "2026-11-19T22:30:00+08:00"   # cafe closed_rest

        c_off, _ = mutate_v03_ecology_view(
            root,
            {
                "action": "offer_item_gift",
                "item_label_cn": "过期陶碟",
                "item_kind": "vessel",
                "operation_key": "op-edge-exp-gift",
            },
            actor_side="partner",
            now_iso=t_offer,
        )
        self.assertEqual(c_off, 200)

        # Save ONLY a partner-authored slow-letter draft -> must stay 100% invisible even after +74h
        keepsake_view.save_slow_letter_draft(
            root,
            actor_side="partner",
            title="伴侣独自静置的信",
            body="绝不可被主体侧循环或上下文读取",
            now=t_offer,
        )

        # Seed cafe rumor on v04_neighborhood_state.json
        mutate_v04_neighborhood_view(
            root,
            {
                "action": "interact_cafe_resident",
                "operation": "share_cafe_rumor",
                "route_id": "street_to_riverside",
                "claim_cn": "听说河边石阶刚扫过落叶",
            },
            actor_side="subject",
            now_iso=t_offer,
        )

        # 1) At t_expired (+74h, daytime 12:00): expired gift & partner draft emit 0 cues; open cafe emits 1
        snap_exp = _snapshot_tree(root)
        collected_exp = loop_bridge.collect_v02_v03_loop_candidates(root, now_iso=t_expired)
        ctx_exp = lcb.build_lived_turn_context(root, _permitted_cfg(root), now_iso=t_expired)
        self.assertEqual(_snapshot_tree(root), snap_exp)
        refs_exp = [str(d.get("source_ref") or "") for d in _descriptors(collected_exp)]
        self.assertFalse(any(r.startswith("v03_item_gift:") for r in refs_exp), refs_exp)
        self.assertFalse(any(r.startswith("v03_slow_letter_draft:") for r in refs_exp), refs_exp)
        self.assertEqual(sum(1 for r in refs_exp if r.startswith("v04_cafe_resident:")), 1, refs_exp)
        self.assertNotIn("过期陶碟", ctx_exp)
        self.assertNotIn("伴侣独自静置的信", ctx_exp)

        # 2) At t_closed (22:30): closed cafe emits 0 v04_cafe_resident candidates
        collected_closed = loop_bridge.collect_v02_v03_loop_candidates(root, now_iso=t_closed)
        refs_closed = [str(d.get("source_ref") or "") for d in _descriptors(collected_closed)]
        self.assertFalse(any(r.startswith("v04_cafe_resident:") for r in refs_closed), refs_closed)

        # 3) Acute illness (blocks_strenuous_outing == True) filters v04_cafe_resident:* cleanly
        eco_bridge.record_strain_or_rest(
            root,
            event_kind="caught_in_rain",
            intensity=2.0,
            unchanged_damp_clothes=True,
            now_iso=t_expired,
        )
        collected_ill = loop_bridge.collect_v02_v03_loop_candidates(root, now_iso=t_expired)
        self.assertTrue(collected_ill["gating"]["blocks_strenuous_outing"])
        refs_ill = [str(d.get("source_ref") or "") for d in _descriptors(collected_ill)]
        self.assertFalse(any(r.startswith("v04_cafe_resident:") for r in refs_ill), refs_ill)
        filtered_reasons = {
            row["source_ref"]: row["reason_code"]
            for row in collected_ill["gating"]["filtered_candidates"]
        }
        self.assertTrue(
            any(
                k.startswith("v04_cafe_resident:") and v == "ILLNESS_BLOCKS_STRENUOUS_OUTING"
                for k, v in filtered_reasons.items()
            ),
            filtered_reasons,
        )

    def test_04_source_hygiene_pure_lf_and_untouched_invariants(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(
            loop_bridge.FROZEN_ANCHOR_SHA256,
            {
                "components/life/scripts/autonomous_life_assembly.py": (
                    "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434"
                ),
                "components/life/scripts/life_sharing_service.py": (
                    "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038"
                ),
            },
        )
        chiyo_plugin = REPO_ROOT / "plugins" / "chiyo" / "__init__.py"
        if chiyo_plugin.is_file():
            line_count = len(chiyo_plugin.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(line_count, 1200)

        for rel in (
            "components/life/scripts/v02_v03_daily_loop_bridge.py",
            "chiyo_bundle/lived_context_bridge.py",
            "tests/test_v08_night_z_capstone_week.py",
        ):
            raw = (REPO_ROOT / rel).read_bytes()
            self.assertNotIn(b"\r", raw, f"CR/CRLF found in {rel}")

        self_text = Path(__file__).read_text(encoding="utf-8")
        self.assertNotIn(FORBIDDEN_NAME, self_text)
        self.assertNotIn(FORBIDDEN_PRONOUN, self_text)
        for ch in self_text:
            cp = ord(ch)
            is_emoji = (
                0x1F300 <= cp <= 0x1FAFF
                or 0x2600 <= cp <= 0x27BF
                or 0xFE00 <= cp <= 0xFE0F
            )
            self.assertFalse(is_emoji, f"Emoji U+{cp:04X} found in capstone test")


if __name__ == "__main__":
    unittest.main(verbosity=2)

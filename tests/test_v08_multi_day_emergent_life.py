#!/usr/bin/env python3
"""v0.8 / v0.4 / v3.0 small general mechanisms composing into real life.

This is the flagship integration story: a custom generated world, real spatial
physics, a shared paper note that smudges in the wet and clears when it dries,
a partner's divergent reading, a reinterpreted old memory with an immutable
original fact, a mutual idiom and a future-self question that wake on the
partner's own present words -- all with **zero bespoke scenario scripts** and
``llm_calls == 0``.

Day 1
-----
* ``worldgen.generate_world`` makes a ``coastal_town`` package; ``editor_bridge``
  projects it into an editor save and we add a ``sea_breeze_cooler`` (cold,
  6 slots), an ``atelier_stove`` (heating, 2 slots) and a ``sea_salt_lemon``
  ingredient (24h shelf life, 2 portions);
* the partner leaves a real shared paper slip with ``inscribed_text_cn`` on the
  dining table plus a jar of ``camellia_honey``;
* the subject cooks a partial two-step warm drink on the custom stove
  (``stop_after_step=1``), chills the leftover in the custom cooler, keeps a
  room-temperature control, and the real daily-loop bridge scans and settles;
* a future-self question, a cross-domain spark and a mutual ``窗边时间`` idiom
  are planted.

Day 2
-----
* advancing six hours chills the cooler drink and ages it six times more slowly
  than the room-temperature control;
* wiping the counter wets the paper slip: ``note_legibility`` reads ``smudged``
  while the inscription is preserved, then ``clear`` again after >120 minutes of
  room air -- the underlying ``inscribed_text_cn`` never changes;
* the partner and the subject leave side-by-side divergent notices; a second
  memory revision may not tweak the locked ``original_fact_cn``;
* ``build_lived_turn_context`` shows the idiom, the triggered past-self question
  and the situation-focused middle selection inside 1800 chars, and an unshared
  private item stays 100% hidden when ``private_artifacts`` is off;
* the anti-puppet scorecard reports ``llm_calls == 0``, ``averaging_forbidden``,
  no ``HIGH`` risk and the modified sources stay person-neutral.

Run (WSL): ``python -B -m unittest tests.test_v08_multi_day_emergent_life -v``.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
import v08_emergent_echoes as echoes  # noqa: E402
from components.life.scripts import v02_antipuppet_scorecard as scorecard  # noqa: E402
from components.phone import v08_living_traces_view as traces_view  # noqa: E402
from components.worldgen import editor_bridge, generator, loader, schema  # noqa: E402
from components.world.scripts import v08_universal_matter as matter  # noqa: E402
from chiyo_bundle import lived_context_bridge as context_bridge  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _at(minutes: int, base: str = T0) -> str:
    return (datetime.fromisoformat(base) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class MultiDayEmergentLifeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-multi-day-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def _act(self, payload: dict, *, now: str = T0) -> dict:
        return matter.compose_open_act(self.root, payload, now_iso=now)

    def _echo(self, payload: dict, *, actor: str, now: str = T0) -> dict:
        view, status = echoes.apply_emergent_echoes_mutation(
            self.root, payload, actor_side=actor, now_iso=now
        )
        self.assertEqual(status, "APPLIED", payload.get("action"))
        return view

    def _object(self, object_id: str, *, now: str = T0, actor: str = "subject") -> dict:
        for row in matter.read_matter_space_view(
            self.root, actor_side=actor, now_iso=now
        )["objects"]:
            if row["object_id"] == object_id:
                return row
        raise AssertionError(f"object not in view: {object_id}")

    def _world(self) -> None:
        docs = generator.generate_world(seed=929711, theme="coastal_town")
        package_dir = self.root / "generated_world"
        schema.write_package(package_dir, docs)
        package = loader.load(package_dir)
        editor_world = editor_bridge.to_editor_world(package)
        editor_world.pop("_bridge_warnings", None)
        # Author three custom objects through the editor save the matter engine
        # already reads (``world/editor_package.json``).
        editor_world["objects"].extend([
            {
                "id": "sea_breeze_cooler",
                "name": "海风冷台",
                "kind": "cabinet",
                "room_id": "home",
                "matter_profile": {"ambient_zone": "cold", "capacity_slots": 6},
            },
            {
                "id": "atelier_stove",
                "name": "画室小灶",
                "kind": "cabinet",
                "room_id": "home",
                "matter_profile": {"ambient_zone": "heating", "capacity_slots": 2},
            },
            {
                "id": "sea_salt_lemon",
                "name": "盐渍柠檬",
                "kind": "item",
                "room_id": "home",
                "matter_profile": {
                    "item_kind": "ingredient",
                    "shelf_life_hours": 24,
                    "portions": 2,
                },
            },
        ])
        editor_bridge.write_editor_world(self.root / "world" / "editor_package.json", editor_world)

    def _ctx_cfg(self, *, private_artifacts: bool) -> dict:
        return {
            "day_root": str(self.root),
            "memory": True,
            "memory_tool_policy": "restricted",
            "phone": {
                "enabled": False,
                "profile_home": str(self.root),
                "private_artifacts": private_artifacts,
            },
        }

    # -- flagship --------------------------------------------------------
    def test_multi_day_emergent_full_life_story(self) -> None:
        self._world()

        zones = {row["zone_id"] for row in matter.read_matter_space_view(self.root, now_iso=T0)["zones"]}
        self.assertIn("sea_breeze_cooler", zones)
        self.assertIn("atelier_stove", zones)

        # ---- Day 1: partner's shared note + honey --------------------
        slip = matter.partner_bring_item(
            self.root,
            {
                "label_cn": "今天傍晚买了一罐山茶花蜂蜜",
                "item_kind": "paper",
                "target_parent_id": "dining_table",
                "paper_like": True,
                "inscribed_text_cn": "蜂蜜在餐桌右角，别太晚睡",
                "object_id": "honey_note_slip",
            },
            now_iso=T0,
        )
        self.assertEqual(slip["status"], "applied")
        honey = matter.partner_bring_item(
            self.root,
            {
                "label_cn": "山茶花蜂蜜",
                "item_kind": "ingredient",
                "target_parent_id": "dining_table",
                "shelf_life_hours": 720,
                "portions": 3,
                "object_id": "camellia_honey",
            },
            now_iso=T0,
        )
        self.assertEqual(honey["status"], "applied")
        slip_row = self._object("honey_note_slip")
        self.assertEqual(slip_row["inscribed_text_cn"], "蜂蜜在餐桌右角，别太晚睡")
        self.assertEqual(slip_row["note_legibility"], "clear")
        self.assertNotIn("千代", slip_row["derived_role_cn"])

        # ---- Day 1: cook a partial two-step warm drink on the stove ---
        partial = self._act({
            "intent_cn": "在画室小灶上煮一壶盐渍柠檬蜂蜜茶，先只做到出茶这一步",
            "steps": [
                {
                    "op": "TRANSFORM_CREATE",
                    "source_object_ids": ["sea_salt_lemon"],
                    "output_label_cn": "盐渍柠檬蜂蜜茶",
                    "output_kind": "drink",
                    "medium_object_id": "atelier_stove",
                },
                {
                    "op": "PLACE_OR_MOVE",
                    "object_id": "sea_salt_lemon",
                    "target_parent_id": "home_staging_corner",
                },
            ],
            "stop_after_step": 1,
        })
        self.assertEqual(partial["status"], "partially_applied")
        self.assertEqual(partial["stop_reason"], "STOP_AFTER_STEP")
        self.assertEqual(partial["steps_applied"], 1)
        self.assertEqual(partial["llm_calls"], 0)
        tea = partial["step_results"][0]
        self.assertTrue(tea["heated"])
        self.assertEqual(tea["thermal"], "hot")
        tea_id = tea["output_object_id"]

        # Chill the leftover in the custom cold zone.
        chilled = self._act({
            "intent_cn": "把煮好的热茶先放进海风冷台",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": tea_id, "target_parent_id": "sea_breeze_cooler"}],
        })
        self.assertEqual(chilled["status"], "applied")
        self.assertEqual(chilled["step_results"][0]["target_parent_id"], "sea_breeze_cooler")

        # A room-temperature control from the last lemon portion.
        control = self._act({
            "intent_cn": "再煮一壶放在顺手暂放角作对照",
            "steps": [{
                "op": "TRANSFORM_CREATE",
                "source_object_ids": ["sea_salt_lemon"],
                "output_label_cn": "盐渍柠檬蜂蜜茶（桌上对照）",
                "output_kind": "drink",
                "medium_object_id": "atelier_stove",
            }],
        })
        self.assertEqual(control["status"], "applied")
        control_id = control["step_results"][0]["output_object_id"]

        # A private item the partner must never see, sorted first so its hint
        # would surface if the private gate ever leaked.
        private = self._act({
            "intent_cn": "给自己留一份私房柠檬挞",
            "steps": [{
                "op": "INTRODUCE_ITEM",
                "object_id": "aaa_private_tart",
                "label_cn": "私房柠檬挞",
                "item_kind": "prepared_food",
                "target_parent_id": "subject_private_drawer",
                "portions": 1,
            }],
        })
        self.assertEqual(private["status"], "applied")
        private_row = self._object("aaa_private_tart")
        self.assertEqual(private_row["visibility"], "private")

        # ---- Day 1: the real bridge scans and settles cleanly --------
        scan = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        self.assertEqual(scan["llm_calls"], 0)
        self.assertIsInstance(scan["candidates"], list)
        settled = matter.settle_matter_physics(self.root, now_iso=T0)
        self.assertIsInstance(settled, dict)
        tick = bridge.run_v02_v03_daily_tick(
            self.root, now_iso=T0, advance_turn=False, advance_clock=False
        )
        self.assertIsInstance(tick, dict)

        # ---- Day 1: future-self question / spark / mutual idiom ------
        self._echo(
            {
                "action": "plant_future_question",
                "question_cn": "下次煮盐渍柠檬蜂蜜茶时，放半颗还是整颗柠檬更顺口？",
                "anchor_kind": "topic",
                "anchor_ref": "盐渍柠檬蜂蜜茶",
                "keywords": ["柠檬", "蜂蜜茶"],
                "visibility": "shared",
            },
            actor="subject",
        )
        self._echo(
            {
                "action": "record_cross_domain_spark",
                "source_domain": "coastal_walk",
                "source_observation_cn": "退潮后礁石上留下盐霜的纹路",
                "target_domain": "writing_sketch",
                "try_method_cn": "把这圈盐霜纹路画进短文的开头",
                "familiarity": "tried_once",
                "craft_fit": "workable",
                "mood_pull_today": "curious",
                "visibility": "shared",
            },
            actor="subject",
        )
        proposed = self._echo(
            {
                "action": "propose_shared_idiom",
                "phrase": "窗边时间",
                "meaning_cn": "傍晚在窗边留一段安静时间，不说话也不算浪费",
                "origin_summary_cn": "那天一起看海回来，约好每天留一点窗边时间",
            },
            actor="partner",
        )
        idiom_id = proposed["shared_idioms"][-1]["idiom_id"]
        confirmed = self._echo(
            {"action": "confirm_shared_idiom", "idiom_id": idiom_id},
            actor="subject",
        )
        self.assertEqual(confirmed["shared_idioms"][-1]["status"], "confirmed")

        # ---- Day 2: six hours of cooling / slow aging ----------------
        t1 = _at(360)
        matter.settle_matter_physics(self.root, now_iso=t1)
        after_six = matter.read_matter_space_view(self.root, actor_side="subject", now_iso=t1)
        rows = {row["object_id"]: row for row in after_six["objects"]}
        self.assertEqual(rows[tea_id]["thermal"], "cold")
        self.assertEqual(rows[control_id]["thermal"], "room")
        state = matter._read_state_zero_write(self.root)
        chilled_hours = float(state["matter_objects"][tea_id]["effective_exposure_hours"])
        room_hours = float(state["matter_objects"][control_id]["effective_exposure_hours"])
        self.assertAlmostEqual(chilled_hours, 1.0, places=3)
        self.assertAlmostEqual(room_hours, 6.0, places=3)
        self.assertAlmostEqual(room_hours / chilled_hours, 6.0, places=3)

        # ---- Day 2: the wet note smudges, then clears ----------------
        self.assertEqual(self._object("honey_note_slip", now=t1)["note_legibility"], "clear")
        wiped = self._act({
            "intent_cn": "擦餐桌时把那张便签纸也带湿了一点",
            "steps": [{"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": "honey_note_slip"}],
        }, now=t1)
        self.assertEqual(wiped["status"], "applied")
        wet = self._object("honey_note_slip", now=t1)
        self.assertEqual(wet["moisture"], "wet")
        self.assertEqual(wet["note_legibility"], "smudged")
        self.assertIn("沾水晕开", wet["derived_role_cn"])
        self.assertIn("蜂蜜在餐桌右角", wet["derived_role_cn"])
        self.assertEqual(wet["inscribed_text_cn"], "蜂蜜在餐桌右角，别太晚睡")

        dry = self._object("honey_note_slip", now=_at(360 + 121))
        self.assertEqual(dry["moisture"], "dry")
        self.assertEqual(dry["note_legibility"], "clear")
        self.assertNotIn("隐约", dry["derived_role_cn"])
        self.assertIn("蜂蜜在餐桌右角", dry["derived_role_cn"])
        self.assertEqual(dry["inscribed_text_cn"], "蜂蜜在餐桌右角，别太晚睡")

        # ---- Day 2: divergent notices on a shared page --------------
        self._echo(
            {
                "action": "partner_add_divergent_notice",
                "target_ref": "shared_sketchbook_page_01",
                "target_title_cn": "共用的速写本这一页",
                "notice_cn": "右上角那块留白更像海面，先别急着补线条",
            },
            actor="partner",
        )
        notices = self._echo(
            {
                "action": "subject_add_divergent_notice",
                "target_ref": "shared_sketchbook_page_01",
                "target_title_cn": "共用的速写本这一页",
                "notice_cn": "我想在那块留白里加一小片盐霜纹路",
            },
            actor="subject",
        )
        notice = notices["divergent_notices"][-1]
        self.assertIn("海面", notice["partner_notice_cn"])
        self.assertIn("盐霜纹路", notice["subject_notice_cn"])

        # ---- Day 2: reinterpreted memory, original fact locked ------
        first = self._echo(
            {
                "action": "record_memory_reinterpretation",
                "episode_ref": "evening_window_tea",
                "episode_title_cn": "那天窗边的茶",
                "original_fact_cn": "第一次煮盐渍柠檬蜂蜜茶时放了一整颗柠檬",
                "prior_view_cn": "当时只觉得太酸",
                "new_view_cn": "现在觉得那点酸正好醒神",
                "trigger_summary_cn": "今天又煮了一次，尝到同样的酸",
                "shared_with_partner": True,
            },
            actor="subject",
        )
        first_fact = first["memory_reinterpretations"][-1]["original_fact_cn"]
        revised = self._echo(
            {
                "action": "record_memory_reinterpretation",
                "episode_ref": "evening_window_tea",
                "episode_title_cn": "那天窗边的茶",
                "original_fact_cn": "第一次煮茶其实只放了半颗柠檬",
                "new_view_cn": "回头看只是当时火候没把握好",
                "trigger_summary_cn": "重读旧记录时发现的",
                "shared_with_partner": True,
            },
            actor="subject",
        )
        memory = revised["memory_reinterpretations"][-1]
        self.assertEqual(memory["original_fact_cn"], first_fact)
        self.assertEqual(len(memory["revisions"]), 2)
        self.assertEqual(memory["current_view_cn"], "回头看只是当时火候没把握好")

        # ---- Day 2: lived turn context ------------------------------
        incoming = "我们留一点窗边时间吧，明天试试柠檬蜂蜜茶"
        ctx = context_bridge.build_lived_turn_context(
            self.root, self._ctx_cfg(private_artifacts=True), incoming
        )
        self.assertLessEqual(len(ctx), 1800)
        self.assertIn("窗边时间", ctx)
        self.assertIn("留给未来的问题被触发", ctx)
        self.assertIn("盐渍柠檬蜂蜜茶", ctx)
        # Situation focus: the wake-on-cue echoes line outranks the ordinary traces line.
        echo_pos = ctx.find("心里回响")
        traces_pos = ctx.find("屋里动态")
        self.assertGreaterEqual(echo_pos, 0)
        self.assertGreaterEqual(traces_pos, 0)
        self.assertLess(echo_pos, traces_pos)
        # The private item's hint only appears under the private-enabled view.
        self.assertIn("私房柠檬挞", ctx)

        restricted = context_bridge.build_lived_turn_context(
            self.root, self._ctx_cfg(private_artifacts=False), incoming
        )
        self.assertLessEqual(len(restricted), 1800)
        self.assertNotIn("私房柠檬挞", restricted)
        self.assertNotIn("私人小抽屉", restricted)

        # ---- Day 2: anti-puppet scorecard ---------------------------
        card = scorecard.evaluate_antipuppet_scorecard(self.root)
        self.assertEqual(card["llm_calls"], 0)
        self.assertTrue(card["averaging_forbidden"])
        self.assertNotEqual(card["overall_risk"], "HIGH")
        for dimension in card["dimensions"]:
            self.assertNotEqual(dimension["risk"], "HIGH", dimension["dim_id"])

    # -- focused feature test -------------------------------------------
    def test_inscribed_paper_note_smudge_clear_and_validation(self) -> None:
        introduced = self._act({
            "intent_cn": "在书桌上放一张写了字的便签",
            "steps": [{
                "op": "INTRODUCE_ITEM",
                "object_id": "desk_note_01",
                "label_cn": "桌上的便签",
                "item_kind": "paper",
                "target_parent_id": "home_staging_corner",
                "paper_like": True,
                "inscribed_text_cn": "周三记得去看海",
            }],
        })
        self.assertEqual(introduced["status"], "applied")
        note = self._object("desk_note_01")
        self.assertEqual(note["inscribed_text_cn"], "周三记得去看海")
        self.assertEqual(note["note_legibility"], "clear")
        self.assertIn("周三记得去看海", note["derived_role_cn"])

        # Getting the note damp smudges the reading without editing the text.
        self._act({
            "intent_cn": "手没擦干就碰了那张便签",
            "steps": [{"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": "desk_note_01"}],
        })
        damp = self._object("desk_note_01")
        self.assertEqual(damp["note_legibility"], "smudged")
        self.assertEqual(damp["inscribed_text_cn"], "周三记得去看海")

        # 121 minutes of room air dries it: legible again, text intact.
        clear = self._object("desk_note_01", now=_at(121))
        self.assertEqual(clear["note_legibility"], "clear")
        self.assertEqual(clear["inscribed_text_cn"], "周三记得去看海")

        # An inscription is only meaningful on paper / keepsake matter.
        with self.assertRaises(matter.V08MatterError) as ctx:
            self._act({
                "intent_cn": "在鸡蛋上刻字（不该被接受）",
                "steps": [{
                    "op": "INTRODUCE_ITEM",
                    "label_cn": "带字的鸡蛋",
                    "item_kind": "ingredient",
                    "target_parent_id": "home_staging_corner",
                    "inscribed_text_cn": "写着不该出现的东西",
                }],
            })
        self.assertEqual(ctx.exception.reason_code, "INVALID_FIELDS")

        # Whitespace-only text is not a real inscription.
        with self.assertRaises(matter.V08MatterError) as ctx:
            self._act({
                "intent_cn": "写一张空白便签",
                "steps": [{
                    "op": "INTRODUCE_ITEM",
                    "label_cn": "空白便签",
                    "item_kind": "paper",
                    "target_parent_id": "home_staging_corner",
                    "paper_like": True,
                    "inscribed_text_cn": "   ",
                }],
            })
        self.assertEqual(ctx.exception.reason_code, "INVALID_FIELDS")

        # Too long is refused by the engine, not silently truncated.
        with self.assertRaises(matter.V08MatterError) as ctx:
            self._act({
                "intent_cn": "写一张太长的纸片",
                "steps": [{
                    "op": "INTRODUCE_ITEM",
                    "label_cn": "超长便签",
                    "item_kind": "paper",
                    "target_parent_id": "home_staging_corner",
                    "paper_like": True,
                    "inscribed_text_cn": "字" * 121,
                }],
            })
        self.assertEqual(ctx.exception.reason_code, "INVALID_FIELDS")

    def test_phone_partner_bring_item_inscription_gate(self) -> None:
        ok, envelope = traces_view.mutate_v08_living_traces_view(
            self.root,
            {
                "action": "partner_bring_item",
                "label_cn": "伴侣留下的便条",
                "item_kind": "paper",
                "target_parent_id": "dining_table",
                "paper_like": True,
                "inscribed_text_cn": "饭在锅里",
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(ok, 200, envelope)
        self.assertTrue(envelope["ok"])
        objects = {
            row["object_id"]: row
            for row in matter.read_matter_space_view(self.root, now_iso=T0)["objects"]
        }
        written = [row for row in objects.values() if row.get("inscribed_text_cn") == "饭在锅里"]
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]["note_legibility"], "clear")

        for bad in (123, "", "字" * 121):
            code, env = traces_view.mutate_v08_living_traces_view(
                self.root,
                {
                    "action": "partner_bring_item",
                    "label_cn": "不合规便条",
                    "item_kind": "paper",
                    "target_parent_id": "dining_table",
                    "paper_like": True,
                    "inscribed_text_cn": bad,
                },
                actor_side="partner",
                now_iso=T0,
            )
            self.assertEqual(code, 400, bad)
            self.assertEqual(env["reason_code"], "INVALID_FIELDS", bad)

    def test_modified_sources_are_person_neutral_and_model_free(self) -> None:
        for module in (matter, traces_view):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("她", source, module.__name__)
            self.assertNotIn("千代", source, module.__name__)


if __name__ == "__main__":
    unittest.main()

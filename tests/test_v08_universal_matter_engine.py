#!/usr/bin/env python3
"""v0.8 universal matter substrate and open multi-step act composer.

Coverage:
* zero-write reads on an empty root (view / settle / scan) -- no dir, no file,
  no mtime change;
* the default physical space and its live derived roles;
* recipe-free ``TRANSFORM_CREATE`` (real tomato + raw udon -> any caller name),
  a missing ingredient is a real 404 and never invents an object;
* derived uses emerging from one vessel (pens in a cup block drink / wash until
  emptied; a moved vessel carries its children with one parent id);
* honest partial acts (``stop_after_step`` and a later blocked step keep only
  what really landed);
* soiled surfaces / full slots block paper and new objects, then recover after
  cleaning;
* a non-omniscient partner move (subject must OBSERVE), objective spoilage in a
  private drawer (partner sees nothing, no hidden_count, all probes 404);
* fridge aging at 1/6 speed vs. private drawer spoiling;
* DISCARD_ITEM is never counted as intake; the illness gate blocks a strenuous
  act; operation_key replay/conflict; partner proposal + subject decision;
  objective zero-write opportunities; external WorldGen object materialisation;
* person-neutral copy (no hard-coded pronoun/name) and ``llm_calls == 0``.

Run (WSL): ``python -m unittest tests.test_v08_universal_matter_engine -v``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.world.scripts import v08_universal_matter as engine  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _at(minutes: int, base: str = T0) -> str:
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(base) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (int(stat.st_size), int(stat.st_mtime_ns), hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


class V08UniversalMatterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-matter-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _view(self, *, actor_side: str = "subject", now: str = T0) -> dict:
        return engine.read_matter_space_view(self.root, actor_side=actor_side, now_iso=now)

    def _obj(self, object_id: str, *, now: str = T0) -> dict:
        for row in self._view(now=now)["objects"]:
            if row["object_id"] == object_id:
                return row
        raise AssertionError(f"object not in view: {object_id}")

    def _zone(self, zone_id: str, *, now: str = T0) -> dict:
        for row in self._view(now=now)["zones"]:
            if row["zone_id"] == zone_id:
                return row
        raise AssertionError(f"zone not in view: {zone_id}")

    def _act(self, payload: dict, *, now: str = T0) -> dict:
        return engine.compose_open_act(self.root, payload, now_iso=now)

    def _introduce(self, object_id: str, label: str, kind: str, target: str, *, now: str = T0, **extra) -> dict:
        step = {
            "op": "INTRODUCE_ITEM",
            "object_id": object_id,
            "label_cn": label,
            "item_kind": kind,
            "target_parent_id": target,
        }
        step.update(extra)
        return self._act({"intent_cn": f"放入{label}", "steps": [step]}, now=now)

    # -- tests -----------------------------------------------------------

    def test_empty_root_reads_are_100_percent_zero_write(self) -> None:
        before = _snapshot(self.root)
        view = self._view()
        self.assertEqual(view["schema"], engine.VIEW_SCHEMA)
        self.assertEqual(view["llm_calls"], 0)
        self.assertEqual(
            engine.settle_matter_physics(self.root, now_iso=T0),
            {"settled_items": 0, "cooled": 0, "chilled": 0, "spoiled": 0, "dried": 0},
        )
        self.assertEqual(engine.scan_matter_opportunities_zero_write(self.root, now_iso=T0), [])
        engine.read_matter_space_view(self.root, actor_side="partner", now_iso=T0)
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse((self.root / "world").exists())
        self.assertFalse(engine.state_path(self.root).exists())

    def test_default_space_topology(self) -> None:
        zones = {z["zone_id"]: z for z in self._view()["zones"]}
        expected = {
            "home_desk", "home_staging_corner", "home_fridge", "home_fridge_door",
            "kitchen_counter", "kitchen_stove", "dining_table", "kitchen_sink",
            "subject_private_drawer",
        }
        self.assertTrue(expected.issubset(zones))
        self.assertEqual(zones["home_fridge"]["ambient_zone"], "cold")
        self.assertTrue(zones["kitchen_sink"]["water_source"])
        self.assertEqual(zones["subject_private_drawer"]["visibility"], "private")
        for oid in ("ceramic_cup_01", "soup_bowl_01", "pen_01", "pen_02", "sketch_sheet_01"):
            self.assertIn(oid, {o["object_id"] for o in self._view()["objects"]})

    def test_default_derived_roles(self) -> None:
        self.assertEqual(self._obj("ceramic_cup_01")["derived_role_cn"], "空置可用的容器")
        self.assertEqual(self._obj("sketch_sheet_01")["derived_role_cn"], "可铺开的纸页")
        self.assertTrue(self._obj("sketch_sheet_01")["paper_like"])

    def test_transform_is_recipe_free(self) -> None:
        self._introduce("tomato_01", "番茄", "ingredient", "kitchen_counter", cook_state="raw", portions=1, shelf_life_hours=48)
        self._introduce("udon_01", "生乌冬面", "ingredient", "kitchen_counter", cook_state="raw", portions=1, shelf_life_hours=48)
        receipt = self._act({
            "intent_cn": "煮一碗番茄乌冬",
            "steps": [{
                "op": "TRANSFORM_CREATE",
                "source_object_ids": ["tomato_01", "udon_01"],
                "output_label_cn": "无葱番茄乌冬面",
                "target_vessel_id": "soup_bowl_01",
                "medium_object_id": "kitchen_stove",
            }],
        })
        self.assertEqual(receipt["status"], "applied")
        step = receipt["step_results"][0]
        self.assertEqual(step["cook_state"], "cooked")
        self.assertEqual(step["thermal"], "hot")
        self.assertEqual(step["portions_left"], 2)
        dish = self._obj(step["output_object_id"])
        self.assertEqual(dish["freshness"], "fresh")
        self.assertIn("热乎刚做好的成品", dish["derived_role_cn"])
        self.assertEqual(self._obj("soup_bowl_01")["derived_role_cn"], "正盛着无葱番茄乌冬面")
        # The two sources were consumed away.
        self.assertTrue(engine._read_state_zero_write(self.root)["matter_objects"]["tomato_01"]["consumed"])

        # A second, completely different arbitrary recipe through the same API.
        self._introduce("teabag_01", "麦茶包", "ingredient", "kitchen_counter", cook_state="raw")
        self._introduce("hot_water_01", "热水", "ingredient", "kitchen_counter")
        tea = self._act({
            "intent_cn": "冲一杯热麦茶",
            "steps": [{
                "op": "TRANSFORM_CREATE",
                "source_object_ids": ["teabag_01", "hot_water_01"],
                "output_label_cn": "热麦茶",
                "output_kind": "drink",
                "medium_object_id": "kitchen_stove",
            }],
        })
        self.assertEqual(tea["step_results"][0]["output_kind"], "drink")
        self.assertEqual(tea["step_results"][0]["thermal"], "hot")

    def test_transform_missing_ingredient_is_404_and_invents_nothing(self) -> None:
        self._introduce("tomato_01", "番茄", "ingredient", "kitchen_counter", cook_state="raw")
        with self.assertRaises(engine.V08MatterError) as ctx:
            self._act({
                "intent_cn": "撒点小葱",
                "steps": [{
                    "op": "TRANSFORM_CREATE",
                    "source_object_ids": ["tomato_01", "small_onion_01"],
                    "output_label_cn": "葱花番茄",
                }],
            })
        self.assertEqual(ctx.exception.reason_code, "OBJECT_NOT_FOUND")
        self.assertEqual(ctx.exception.status_code, 404)
        state = engine._read_state_zero_write(self.root)
        self.assertNotIn("small_onion_01", state["matter_objects"])
        self.assertFalse(any("葱" in str(o.get("label_cn")) for o in state["matter_objects"].values()))

    def test_cup_derived_use_blocks_drink_and_wash_until_empty(self) -> None:
        self._act({
            "intent_cn": "把两支笔插进空杯",
            "steps": [
                {"op": "PLACE_OR_MOVE", "object_id": "pen_01", "target_parent_id": "ceramic_cup_01"},
                {"op": "PLACE_OR_MOVE", "object_id": "pen_02", "target_parent_id": "ceramic_cup_01"},
            ],
        })
        role = self._obj("ceramic_cup_01")["derived_role_cn"]
        self.assertTrue(role.startswith("临时装着"), role)
        self.assertIn("腾空", role)
        self.assertEqual(self._obj("ceramic_cup_01")["occupancy"], 2)

        with self.assertRaises(engine.V08MatterError) as drink_ctx:
            self._act({"intent_cn": "用这个杯子喝水", "steps": [{"op": "CONSUME_PORTION", "object_id": "ceramic_cup_01"}]})
        self.assertEqual(drink_ctx.exception.reason_code, "VESSEL_OCCUPIED_OR_SOILED")

        self._introduce("tea_leaf_01", "茶叶", "ingredient", "kitchen_counter")
        with self.assertRaises(engine.V08MatterError) as pour_ctx:
            self._act({
                "intent_cn": "往这个杯子泡茶",
                "steps": [{
                    "op": "TRANSFORM_CREATE",
                    "source_object_ids": ["tea_leaf_01"],
                    "output_label_cn": "一杯茶",
                    "output_kind": "drink",
                    "target_vessel_id": "ceramic_cup_01",
                }],
            })
        self.assertEqual(pour_ctx.exception.reason_code, "VESSEL_OCCUPIED_OR_SOILED")

        with self.assertRaises(engine.V08MatterError) as wash_ctx:
            self._act({
                "intent_cn": "洗杯子",
                "steps": [{"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": "ceramic_cup_01", "helper_object_id": "kitchen_sink"}],
            })
        self.assertEqual(wash_ctx.exception.reason_code, "CONTAINER_NOT_EMPTY")

        # Take both pens out, wash the cup, and it is a drinking vessel again.
        self._act({
            "intent_cn": "把笔拿出来并洗杯",
            "steps": [
                {"op": "PLACE_OR_MOVE", "object_id": "pen_01", "target_parent_id": "home_desk"},
                {"op": "PLACE_OR_MOVE", "object_id": "pen_02", "target_parent_id": "home_desk"},
                {"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": "ceramic_cup_01", "helper_object_id": "kitchen_sink"},
            ],
        })
        self.assertEqual(self._obj("ceramic_cup_01")["derived_role_cn"], "空置可用的容器")
        pour = self._act({
            "intent_cn": "泡一杯茶",
            "steps": [{
                "op": "TRANSFORM_CREATE",
                "source_object_ids": ["tea_leaf_01"],
                "output_label_cn": "一杯茶",
                "output_kind": "drink",
                "target_vessel_id": "ceramic_cup_01",
            }],
        })
        self.assertEqual(pour["status"], "applied")

    def test_moving_a_vessel_carries_its_children(self) -> None:
        self._act({
            "intent_cn": "笔放进杯子",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": "pen_01", "target_parent_id": "ceramic_cup_01"}],
        })
        self._act({
            "intent_cn": "把装笔的杯子挪到暂放角",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": "ceramic_cup_01", "target_parent_id": "home_staging_corner"}],
        })
        self.assertEqual(self._obj("ceramic_cup_01")["parent_id"], "home_staging_corner")
        self.assertEqual(self._obj("pen_01")["parent_id"], "ceramic_cup_01")
        self.assertEqual(self._obj("ceramic_cup_01")["children_ids"], ["pen_01"])

    def test_partial_act_keeps_only_what_landed(self) -> None:
        receipt = self._act({
            "intent_cn": "先挪笔，再挪到不存在的角落",
            "steps": [
                {"op": "PLACE_OR_MOVE", "object_id": "pen_01", "target_parent_id": "home_staging_corner"},
                {"op": "PLACE_OR_MOVE", "object_id": "pen_02", "target_parent_id": "nowhere_at_all"},
            ],
        })
        self.assertEqual(receipt["status"], "partially_applied")
        self.assertEqual(receipt["steps_applied"], 1)
        self.assertEqual(receipt["failed_step"]["reason_code"], "INVALID_TARGET")
        self.assertEqual(self._obj("pen_01")["parent_id"], "home_staging_corner")
        self.assertEqual(self._obj("pen_02")["parent_id"], "home_desk")

    def test_stop_after_step_leaves_later_steps_undone(self) -> None:
        receipt = self._act({
            "intent_cn": "只收拾一半就停",
            "steps": [
                {"op": "PLACE_OR_MOVE", "object_id": "sketch_sheet_01", "target_parent_id": "home_staging_corner"},
                {"op": "PLACE_OR_MOVE", "object_id": "pen_01", "target_parent_id": "home_staging_corner"},
            ],
            "stop_after_step": 1,
        })
        self.assertEqual(receipt["status"], "partially_applied")
        self.assertEqual(receipt["stop_reason"], "STOP_AFTER_STEP")
        self.assertEqual(receipt["steps_applied"], 1)
        self.assertEqual(self._obj("sketch_sheet_01")["parent_id"], "home_staging_corner")
        self.assertEqual(self._obj("pen_01")["parent_id"], "home_desk")

    def test_soiled_surface_and_full_slots_block_then_recover(self) -> None:
        # Leave oil on the dining table (a real act soils the surface).
        self._act({
            "intent_cn": "吃完饭溅了油",
            "steps": [{"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": "dining_table"}],
            "soils_surface_id": "dining_table",
        })
        self.assertEqual(self._zone("dining_table")["surface_cleanliness"], "soiled")
        with self.assertRaises(engine.V08MatterError) as paper_ctx:
            self._introduce("flyer_01", "传单", "paper", "dining_table", paper_like=True)
        self.assertEqual(paper_ctx.exception.reason_code, "SURFACE_SOILED")

        # Wipe the table, then fill all three slots.
        self._act({"intent_cn": "擦干净桌面", "steps": [{"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": "dining_table"}]})
        self.assertEqual(self._zone("dining_table")["surface_cleanliness"], "clean")
        for index in range(3):
            self._introduce(f"plate_{index}", f"盘子{index}", "everyday", "dining_table")
        with self.assertRaises(engine.V08MatterError) as full_ctx:
            self._introduce("extra_plate", "多余的盘子", "everyday", "dining_table")
        self.assertEqual(full_ctx.exception.reason_code, "CAPACITY_FULL")

    def test_cleaning_a_vessel_requires_the_sink(self) -> None:
        with self.assertRaises(engine.V08MatterError) as ctx:
            self._act({"intent_cn": "随手洗杯", "steps": [{"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": "ceramic_cup_01"}]})
        self.assertEqual(ctx.exception.reason_code, "INVALID_TARGET")
        ok = self._act({
            "intent_cn": "拿到水槽洗杯",
            "steps": [
                {"op": "PLACE_OR_MOVE", "object_id": "ceramic_cup_01", "target_parent_id": "kitchen_sink"},
                {"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": "ceramic_cup_01"},
            ],
        })
        self.assertEqual(ok["status"], "applied")
        self.assertEqual(self._obj("ceramic_cup_01")["cleanliness"], "clean")

    def test_partner_move_is_not_omniscient_until_observed(self) -> None:
        engine.partner_move_shared_object(
            self.root,
            {"object_id": "pen_01", "target_parent_id": "home_staging_corner"},
            now_iso=T0,
        )
        # Subject projection is still the world she last saw: the pen at the desk,
        # with no God-view stale flag leaking into it.
        pen = self._obj("pen_01")
        self.assertEqual(pen["parent_id"], "home_desk")
        self.assertEqual(pen["subject_last_seen_parent"], "home_desk")
        self.assertNotIn("impression_stale", pen)
        # The partner God-view does know the real spot and the stale impression.
        partner_pen = next(
            row for row in self._view(actor_side="partner")["objects"]
            if row["object_id"] == "pen_01"
        )
        self.assertEqual(partner_pen["parent_id"], "home_staging_corner")
        self.assertTrue(partner_pen["impression_stale"])

        receipt = self._act({"intent_cn": "看一眼笔在哪", "steps": [{"op": "OBSERVE_OBJECT", "object_id": "pen_01"}]})
        observed = receipt["step_results"][0]
        self.assertTrue(observed["moved_since_last_seen"])
        self.assertEqual(observed["previous_seen_parent"], "home_desk")
        self.assertEqual(observed["observed_parent"], "home_staging_corner")
        self.assertNotIn("impression_stale", self._obj("pen_01"))
        self.assertEqual(self._obj("pen_01")["parent_id"], "home_staging_corner")
        self.assertEqual(self._obj("pen_01")["subject_last_seen_parent"], "home_staging_corner")

    def test_fridge_slows_aging_private_drawer_spoils_without_leaking(self) -> None:
        self._act({
            "intent_cn": "酸奶进冰箱，蛋糕进私人抽屉",
            "steps": [
                {"op": "INTRODUCE_ITEM", "object_id": "yogurt_01", "label_cn": "酸奶", "item_kind": "ingredient",
                 "target_parent_id": "home_fridge", "shelf_life_hours": 6},
                {"op": "INTRODUCE_ITEM", "object_id": "cake_01", "label_cn": "小蛋糕", "item_kind": "prepared_food",
                 "target_parent_id": "subject_private_drawer", "shelf_life_hours": 2, "cook_state": "cooked"},
            ],
        })
        counts = engine.settle_matter_physics(self.root, now_iso=_at(120))
        self.assertGreaterEqual(counts["settled_items"], 1)
        state = engine._read_state_zero_write(self.root)
        self.assertEqual(state["matter_objects"]["yogurt_01"]["freshness"], "fresh")
        self.assertAlmostEqual(state["matter_objects"]["yogurt_01"]["effective_exposure_hours"], 1.0 / 3.0, places=4)
        self.assertEqual(state["matter_objects"]["cake_01"]["freshness"], "spoiled")
        self.assertEqual(state["matter_objects"]["cake_01"]["visibility"], "private")

        # A year later the private drawer still spoils objectively, but the
        # partner projection never sees it and never even counts it.
        partner = engine.read_matter_space_view(self.root, actor_side="partner", now_iso="2027-11-16T09:00:00+08:00")
        self.assertNotIn("subject_private_drawer", {z["zone_id"] for z in partner["zones"]})
        self.assertNotIn("cake_01", {o["object_id"] for o in partner["objects"]})
        self.assertNotIn("hidden_count", json.dumps(partner, ensure_ascii=False))
        subject_cake = self._obj("cake_01", now="2027-11-16T09:00:00+08:00")
        self.assertEqual(subject_cake["freshness"], "spoiled")

        for call in (
            lambda: engine.partner_bring_item(self.root, {"label_cn": "糖", "item_kind": "everyday", "target_parent_id": "subject_private_drawer"}),
            lambda: engine.partner_move_shared_object(self.root, {"object_id": "cake_01", "target_parent_id": "home_desk"}),
            lambda: engine.partner_propose_object_use(self.root, {"object_id": "cake_01", "proposed_use_cn": "吃掉"}),
        ):
            with self.assertRaises(engine.V08MatterError) as ctx:
                call()
            self.assertEqual(ctx.exception.reason_code, "OBJECT_NOT_FOUND")
            self.assertEqual(ctx.exception.status_code, 404)

    def test_discarding_spoiled_food_is_not_intake(self) -> None:
        self._introduce("old_soup", "隔夜汤", "prepared_food", "dining_table", cook_state="cooked", portions=1, shelf_life_hours=1)
        engine.settle_matter_physics(self.root, now_iso=_at(180))
        self.assertEqual(engine._read_state_zero_write(self.root)["matter_objects"]["old_soup"]["freshness"], "spoiled")
        with self.assertRaises(engine.V08MatterError) as eat_ctx:
            self._act({"intent_cn": "吃点剩汤", "steps": [{"op": "CONSUME_PORTION", "object_id": "old_soup"}]})
        self.assertEqual(eat_ctx.exception.reason_code, "ITEM_SPOILED")
        receipt = self._act({
            "intent_cn": "倒掉变质的汤",
            "steps": [{"op": "DISCARD_ITEM", "object_id": "old_soup", "reason_cn": "变质了"}],
        })
        self.assertFalse(receipt["step_results"][0]["counted_as_intake"])
        record = engine._read_state_zero_write(self.root)["discards"][-1]
        self.assertFalse(record["counted_as_intake"])

    def test_illness_gate_blocks_strenuous_outing(self) -> None:
        (self.root / "phone").mkdir(parents=True, exist_ok=True)
        (self.root / "phone" / "v03_ecology.json").write_text(
            json.dumps({"illness": {"blocks_strenuous_outing": True}}, ensure_ascii=False),
            encoding="utf-8",
        )
        with self.assertRaises(engine.V08MatterError) as ctx:
            self._act({
                "intent_cn": "带病出门搬东西",
                "steps": [{"op": "PLACE_OR_MOVE", "object_id": "pen_01", "target_parent_id": "home_staging_corner"}],
                "strenuous_outing": True,
            })
        self.assertEqual(ctx.exception.reason_code, "ILLNESS_GATE_BLOCKED")
        self.assertFalse(engine.state_path(self.root).exists())

    def test_operation_key_is_idempotent_and_conflicts_on_changed_load(self) -> None:
        self._introduce("rice_01", "米饭", "prepared_food", "dining_table", cook_state="cooked", portions=3)
        payload = {
            "intent_cn": "吃一口米饭",
            "steps": [{"op": "CONSUME_PORTION", "object_id": "rice_01", "portions": 1}],
            "operation_key": "eat-once",
        }
        first = self._act(payload)
        self.assertEqual(first["step_results"][0]["portions_left"], 2)
        replay = self._act(payload)
        self.assertTrue(replay["already_applied"])
        self.assertEqual(replay["step_results"][0]["portions_left"], 2)
        self.assertEqual(engine._read_state_zero_write(self.root)["matter_objects"]["rice_01"]["portions_left"], 2)

        changed = {
            "intent_cn": "吃两口米饭",
            "steps": [{"op": "CONSUME_PORTION", "object_id": "rice_01", "portions": 2}],
            "operation_key": "eat-once",
        }
        with self.assertRaises(engine.V08MatterError) as ctx:
            self._act(changed)
        self.assertEqual(ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

    def test_partner_proposal_is_adopted_by_the_subject(self) -> None:
        engine.partner_propose_object_use(
            self.root,
            {"object_id": "ceramic_cup_01", "proposed_use_cn": "临时插画笔"},
            now_iso=T0,
        )
        self.assertIn("伴侣建议：临时插画笔（待决定）", self._obj("ceramic_cup_01")["derived_role_cn"])
        engine.respond_proposed_use(
            self.root,
            {"object_id": "ceramic_cup_01", "decision": "adopt"},
            now_iso=T0,
        )
        self.assertIn("已采纳用法：临时插画笔", self._obj("ceramic_cup_01")["derived_role_cn"])
        status = engine._read_state_zero_write(self.root)["matter_objects"]["ceramic_cup_01"]["proposed_use"]["status"]
        self.assertEqual(status, "adopted")

    def test_scan_reports_objective_zero_write_opportunities(self) -> None:
        # Objective fact 1: a soiled shared surface.
        self._act({
            "intent_cn": "桌面沾了油",
            "steps": [{"op": "CLEAN_SURFACE_OR_VESSEL", "target_id": "dining_table"}],
            "soils_surface_id": "dining_table",
        })
        before = _snapshot(self.root)
        candidates = engine.scan_matter_opportunities_zero_write(self.root, now_iso=T0)
        self.assertEqual(_snapshot(self.root), before)  # zero write
        self.assertLessEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["availability_basis"], "MATTER_USED_OR_CLUTTERED")

        # Objective fact 2 on a fresh root: perishable food at room temperature.
        fresh_root = Path(tempfile.mkdtemp(prefix="v08-matter-scan-"))
        try:
            engine.compose_open_act(fresh_root, {
                "intent_cn": "放一盒豆腐在桌上",
                "steps": [{"op": "INTRODUCE_ITEM", "object_id": "tofu_01", "label_cn": "豆腐",
                           "item_kind": "ingredient", "target_parent_id": "home_staging_corner", "shelf_life_hours": 24}],
            }, now_iso=T0)
            food = engine.scan_matter_opportunities_zero_write(fresh_root, now_iso=T0)
            self.assertIn("MATTER_PERISHABLE_AT_ROOM", [c["availability_basis"] for c in food])
        finally:
            import shutil
            shutil.rmtree(fresh_root, ignore_errors=True)

        # Objective fact 3: an unseen partner movement is not an oracle.  Pre-miss
        # the subject's scan must stay silent about it; only the partner God-view
        # may surface it.  Once the subject really reaches and misses, the scan
        # can honestly offer "go look" (still without naming the new spot).
        stale_root = Path(tempfile.mkdtemp(prefix="v08-matter-stale-"))
        try:
            engine.partner_move_shared_object(stale_root, {"object_id": "pen_01", "target_parent_id": "home_staging_corner"}, now_iso=T0)
            pre_miss = engine.scan_matter_opportunities_zero_write(stale_root, now_iso=T0)
            self.assertNotIn("MATTER_UNSEEN_MOVE", [c["availability_basis"] for c in pre_miss])
            partner_scan = engine.scan_matter_opportunities_zero_write(stale_root, actor_side="partner", now_iso=T0)
            self.assertIn("MATTER_UNSEEN_MOVE", [c["availability_basis"] for c in partner_scan])

            with self.assertRaises(engine.V08MatterError) as ctx:
                engine.compose_open_act(stale_root, {
                    "intent_cn": "顺手拿桌上的笔",
                    "steps": [{"op": "PLACE_OR_MOVE", "object_id": "pen_01", "target_parent_id": "home_desk"}],
                }, now_iso=T0)
            self.assertEqual(ctx.exception.reason_code, "OBJECT_NOT_AT_EXPECTED_SPOT")
            post_miss = engine.scan_matter_opportunities_zero_write(stale_root, now_iso=T0)
            self.assertIn("v08_matter:observe:pen_01", [c["target_ref"] for c in post_miss])
        finally:
            import shutil
            shutil.rmtree(stale_root, ignore_errors=True)

    def test_external_open_affordance_object_materialises(self) -> None:
        package = self.root / "world_package"
        package.mkdir(parents=True)
        (package / "objects.json").write_text(
            json.dumps({
                "objects": [{
                    "object_id": "lucky_charm",
                    "name": "旅行带回的小挂件",
                    "kind": "keepsake",
                    "location_id": "home",
                }],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        self.assertIn("lucky_charm", {o["object_id"] for o in self._view()["objects"]})
        receipt = self._act({
            "intent_cn": "把挂件挪到暂放角",
            "steps": [{"op": "PLACE_OR_MOVE", "object_id": "lucky_charm", "target_parent_id": "home_staging_corner"}],
        })
        self.assertEqual(receipt["status"], "applied")
        state = engine._read_state_zero_write(self.root)
        self.assertTrue(state["matter_objects"]["lucky_charm"]["from_open_affordance"])
        self.assertEqual(state["matter_objects"]["lucky_charm"]["parent_id"], "home_staging_corner")

    def test_source_is_person_neutral_and_model_free(self) -> None:
        source = Path(engine.__file__).read_text(encoding="utf-8")
        self.assertNotIn("她", source)
        self.assertNotIn("千代", source)
        # Receipts are deterministic; no product model call happens anywhere.
        receipt = self._introduce("apple_01", "苹果", "ingredient", "dining_table", shelf_life_hours=72)
        self.assertEqual(receipt["llm_calls"], 0)
        for row in self._view()["objects"]:
            role = row["derived_role_cn"]
            self.assertNotIn("她", role)
            self.assertNotIn("千代", role)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""0.5 Step 3 验收测试：流动烤红薯推车、退潮浅滩、二手孤品、寄售微生计、并肩散步、隔日包裹、声味隔墙与平静闸门（0 模型调用）。"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(REPO_ROOT / "components"), str(LIFE_SCRIPTS), str(WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import v04_neighborhood_living as nb  # noqa: E402
import v08_universal_matter as matter  # noqa: E402
import world_open_storefront as storefront  # noqa: E402
import world_phone_shopping as shopping  # noqa: E402
import world_travel_delivery as travel  # noqa: E402

NOW_T0 = "2026-11-18T17:30:00+08:00"


def _snap(root: Path) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            out[rel] = (int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
    return out


class TestV05Step3WorldMomentsAndCalmGate(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v05-step3-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_v05_09_passing_sweet_potato_cart_window_and_zero_push(self) -> None:
        """V05-09/10：秋冬季 15 分钟流动烤红薯窗口确定性出现，严禁外发推送，夏季不刷出。"""
        self.assertIsNone(storefront.compute_daily_passing_cart_window("seed_town", 3, "summer"))
        hit_day = next(
            d for d in range(1, 30)
            if storefront.compute_daily_passing_cart_window("seed_town", d, "autumn") is not None
        )
        win = storefront.compute_daily_passing_cart_window("seed_town", hit_day, "autumn")
        assert win is not None
        self.assertEqual(win["duration_minutes"], 15)
        self.assertFalse(win["outbound_push_allowed"])
        self.assertEqual(win["llm_calls"], 0)

        h = win["start_minute_of_day"] // 60
        m = win["start_minute_of_day"] % 60
        in_win_iso = f"2026-11-18T{h:02d}:{m + 3:02d}:00+08:00"
        m_out = win["start_minute_of_day"] + 20
        out_win_iso = f"2026-11-18T{m_out // 60:02d}:{m_out % 60:02d}:00+08:00"

        indoor_cracked = storefront.project_passing_street_stall(
            "seed_town", now_iso=in_win_iso, season="autumn", current_location="home", window_state="cracked", day_index=hit_day
        )
        self.assertTrue(indoor_cracked["audible"])
        self.assertFalse(indoor_cracked["purchasable"])
        self.assertFalse(indoor_cracked["outbound_push_allowed"])

        at_alley = storefront.project_passing_street_stall(
            "seed_town", now_iso=in_win_iso, season="autumn", current_location="home_alley", window_state="closed", day_index=hit_day
        )
        self.assertTrue(at_alley["purchasable"])
        self.assertEqual(at_alley["price_jpy"], 260)

        entry, err = storefront.resolve_passing_cart_purchase(
            "seed_town", now_iso=in_win_iso, season="autumn", current_location="home_alley", day_index=hit_day
        )
        self.assertIsNone(err)
        assert entry is not None
        self.assertEqual(entry["price_jpy"], 260)

        expired, err2 = storefront.resolve_passing_cart_purchase(
            "seed_town", now_iso=out_win_iso, season="autumn", current_location="home_alley", day_index=hit_day
        )
        self.assertIsNone(expired)
        self.assertEqual(err2, "OUTSIDE_CART_WINDOW")

    def test_v05_11_12_reef_low_tide_and_secondhand_curio_provenance(self) -> None:
        """V05-11/12：海岸退潮 2 小时开放，二手画册与海玻璃携带来历与前人微痕迹。"""
        self.assertFalse(matter.is_coastal_low_tide_open("mountain_town", 1, 17))
        self.assertTrue(matter.is_coastal_low_tide_open("coastal_town", 1, 17))

        r_reef = matter.collect_secondhand_or_reef_curio(
            self.root,
            world_seed="seed_coast",
            day_index=1,
            source_kind="reef",
            world_theme="coastal_town",
            hour=17,
            operation_key="reef_1",
            now_iso=NOW_T0,
        )
        r_book = matter.collect_secondhand_or_reef_curio(
            self.root,
            world_seed="seed_coast",
            day_index=1,
            source_kind="bookstore",
            operation_key="book_1",
            now_iso=NOW_T0,
        )
        self.assertIn("退潮", str(r_reef.get("provenance_cn") or ""))
        self.assertTrue(len(str(r_book.get("tucked_note_cn") or "")) > 0)
        view = matter.read_matter_space_view(self.root, actor_side="subject", now_iso=NOW_T0)
        objs = list(view.get("objects") or view.get("matter_objects") or [])
        self.assertGreaterEqual(len(objs), 2)

    def test_v05_13_slow_project_and_consignment_wallet_credit(self) -> None:
        """V05-13 & 微生计寄售：花架四阶段推进，寄售作品经由慢销进入 earned_wallet。"""
        self.assertIn("松木板", nb.street_slow_project_stage(1, start_day=1))
        self.assertIn("秋海棠", nb.street_slow_project_stage(5, start_day=1))

        c_res = nb.consign_work_at_venue(
            self.root,
            consignment_id="cg_card_1",
            venue="cafe_postcard",
            work_title_cn="防波堤薄雾明信片",
            work_ref="artifact:postcard_01",
            unit_price_jpy=420,
            posted_day=1,
            operation_key="cg_1",
            now_iso=NOW_T0,
        )
        self.assertEqual(c_res["earned_wallet"], 0)
        s_day2 = nb.settle_consignment_sales(
            self.root, world_seed="seed_a", current_day=2, operation_key="settle_d2", now_iso=NOW_T0
        )
        self.assertEqual(s_day2["earned_wallet"], 0)
        s_day5 = nb.settle_consignment_sales(
            self.root, world_seed="seed_a", current_day=5, operation_key="settle_d5", now_iso=NOW_T0
        )
        self.assertEqual(s_day5["earned_wallet"], 420)
        snap = nb.read_v05_world_moments_zero_write(self.root, world_seed="seed_a", day_index=5, week_index=0)
        self.assertEqual(snap["earned_wallet_jpy"], 420)

    def test_v05_14_15_parcel_next_day_entrance_and_cat_empty_box(self) -> None:
        """V05-14/15：隔日玄关包裹未到前不可拆，到达拆箱后空纸箱可被猫占领，且不破坏原 travel/matter。"""
        shopping.place_next_day_parcel_order(
            self.root, order_id="ord_p1", item_name_cn="24色水彩铁盒", current_day=1, arrive_hour=15, operation_key="p1"
        )
        self.assertEqual(shopping.settle_arrived_entryway_parcels(self.root, current_day=1, current_hour=18), [])
        with self.assertRaises(ValueError):
            shopping.unpack_arrived_entryway_parcel(self.root, order_id="ord_p1")

        self.assertEqual(shopping.settle_arrived_entryway_parcels(self.root, current_day=2, current_hour=16), ["ord_p1"])
        unpacked = shopping.unpack_arrived_entryway_parcel(
            self.root, order_id="ord_p1", cat_present=True, operation_key="u1"
        )
        self.assertTrue(unpacked["delivery"]["empty_box_occupied_by_cat"])

        # matter 玄关实体同步拆箱占猫纸箱 + 100% 零写读检查
        m_unpack = matter.unpack_parcel_at_entrance(
            self.root, parcel_id="m_p1", item_label_cn="旧画册", cat_present=True, delivered_to_entrance=True, operation_key="mu1", now_iso=NOW_T0
        )
        self.assertTrue(m_unpack["empty_box_occupied_by_cat"])

    def test_v05_companionship_stroll_and_wait_two_minutes(self) -> None:
        """第 70 章双人并肩散步（WALKING_TOGETHER）、等两分钟与 10 分钟环线漫步。"""
        w1 = travel.start_walking_together(
            self.root, "home", "nearby_street", now_iso="2026-11-18T17:30:00+08:00", operation_key="wk1"
        )
        self.assertEqual(w1["stroll"]["mode"], "WALKING_TOGETHER")
        self.assertEqual(w1["stroll"]["impatience_penalty"], 0)
        w2 = travel.pause_wait_two_minutes(self.root, spot_label="便利店门口", wait_seconds=120, now_iso="2026-11-18T17:35:00+08:00", operation_key="wk2")
        self.assertEqual(w2["stroll"]["mode"], "WAITING_OUTSIDE")
        self.assertEqual(w2["stroll"]["wait_seconds"], 120)
        w3 = travel.start_loop_stroll(self.root, center_location_id="home", duration_minutes=10, now_iso="2026-11-18T17:40:00+08:00", operation_key="wk3")
        self.assertEqual(w3["stroll"]["mode"], "LOOP_STROLL")
        self.assertFalse(w3["stroll"]["destination_required"])

        before = _snap(self.root)
        stroll_view = travel.read_companionship_stroll_zero_write(self.root)
        self.assertEqual(_snap(self.root), before)
        self.assertEqual(stroll_view["history_count"], 3)

    def test_v05_31_36_wall_scent_corkboard_lamp_diet_boundaries(self) -> None:
        """V05-31~36：隔墙传声不泄真名、待住静默、6 钉软木墙无损退回相册、借光让步与饮食忌口。"""
        with self.assertRaises(matter.V08MatterError):
            matter.perceive_neighbor_signal(
                raw_actor_name="温井慎一",
                sound_desc_cn="温井慎一收起雨伞走过连廊",
                scent_desc_cn="",
                insulation_level=1,
            )
        safe = matter.perceive_neighbor_signal(
            raw_actor_name="温井慎一",
            sound_desc_cn="门口连廊有收雨伞与脚步声",
            scent_desc_cn="现磨咖啡香",
            insulation_level=1,
        )
        self.assertIn("隐约传来", safe["sound_cn"])
        silent = matter.perceive_neighbor_signal(sound_desc_cn="雨声", scent_desc_cn="桂花香", insulation_level=1, just_entered_place=False)
        self.assertEqual(silent["sound_cn"], "")
        double_glazed = matter.perceive_neighbor_signal(sound_desc_cn="车铃", scent_desc_cn="咖啡香", insulation_level=3)
        self.assertEqual(double_glazed["sound_cn"], "")

        for i in range(7):
            res_pin = matter.pin_corkboard_card(self.root, f"card_{i}", operation_key=f"pin_{i}", now_iso=NOW_T0)
        self.assertEqual(len(res_pin["pinned"]), 6)
        self.assertEqual(res_pin["returned_to_album"], ["card_0"])

        neg = matter.negotiate_shared_lamp_move(requested_zone="bedside", subject_using_lamp_at="desk")
        self.assertEqual(neg["outcome"], "COUNTER_COMPROMISE")

        menu = [
            {"name_cn": "炸猪排便当", "contains_meat": True},
            {"name_cn": "厚蛋烧三明治", "contains_meat": False},
            {"name_cn": "青菜卧蛋面", "contains_meat": False},
        ]
        self.assertEqual(matter.choose_evening_food(menu, no_meat_eggs_only=True), ["厚蛋烧三明治", "青菜卧蛋面"])

    def test_v05_37_38_calm_gate_thirty_day_audit_zero_llm(self) -> None:
        """V05-37/38：连续 30 天时钟推进平静闸门硬校验：每周>=3平淡日，稀有插曲>=12天间隔，0外发推送，0大模型。"""
        push_count = 0
        for week_idx in range(5):
            plan = nb.schedule_week_with_calm_gate("nagiura_calm_30d", week_idx)
            self.assertEqual(len(plan), 7)
            quiet_days = sum(1 for x in plan if x is None)
            self.assertGreaterEqual(quiet_days, 3)

        for day in range(1, 31):
            ev = storefront.compute_daily_passing_cart_window("nagiura_calm_30d", day, "autumn")
            if ev is not None:
                self.assertEqual(ev["llm_calls"], 0)
                if ev["outbound_push_allowed"]:
                    push_count += 1
        self.assertEqual(push_count, 0)

        nb.record_rare_interlude_check(self.root, interlude_kind="thunder_blackout", current_day=3, operation_key="r1", now_iso=NOW_T0)
        with self.assertRaises(nb.V04NeighborhoodError):
            nb.record_rare_interlude_check(self.root, interlude_kind="first_snow", current_day=10, operation_key="r2", now_iso=NOW_T0)
        ok_rare = nb.record_rare_interlude_check(self.root, interlude_kind="first_snow", current_day=16, operation_key="r3", now_iso=NOW_T0)
        self.assertTrue(ok_rare["allowed"])
        self.assertEqual(ok_rare["llm_calls"], 0)

        # zero-write read audit on root
        before = _snap(self.root)
        view = nb.read_v05_world_moments_zero_write(self.root, world_seed="nagiura_calm_30d", day_index=16, week_index=2)
        self.assertEqual(_snap(self.root), before)
        self.assertFalse(view["outbound_push_allowed"])
        self.assertEqual(view["llm_calls"], 0)


if __name__ == "__main__":
    unittest.main()

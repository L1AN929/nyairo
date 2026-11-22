#!/usr/bin/env python3
"""Comprehensive tests for Nyairo 0.5 Home Craft & Town Public Memory (Ch. 67, 97, 98, 101, 103, 106, 112)."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import sys
import tempfile
import threading
import unicodedata
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from components.phone import server as phone_server
from components.phone import service as phone_service
from components.phone import v05_home_craft_view as craft_view
from components.phone import v05_living_town_view as town_view
from components.world.scripts import v05_home_craft_engine as craft_engine
from components.world.scripts import v05_living_town_engine as town_engine
from components.world.scripts import v05_season_solar_pulse as season_pulse
from components.world.scripts import v05_unified_world_fabric as fabric


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


class TestV05SensoryMapAndPublicMemory(unittest.TestCase):
    def test_01_zero_write_get_sensory_map_and_starter_public_memory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-craft-zw-") as td:
            root = Path(td)
            nonexistent = root / "missing_day_root"
            c_lt, b_lt = town_view.read_v05_living_town_view(
                nonexistent,
                has_subject_capability=False,
                now_iso="2026-10-23T16:30:00+08:00",
            )
            c_hc, b_hc = craft_view.read_v05_home_craft_view(
                nonexistent,
                has_subject_capability=False,
                now_iso="2026-10-23T16:30:00+08:00",
            )
            self.assertEqual(c_lt, 200)
            self.assertEqual(c_hc, 200)
            self.assertFalse(nonexistent.exists())
            self.assertEqual(b_lt["llm_calls"], 0)
            self.assertEqual(b_hc["llm_calls"], 0)
            self.assertTrue(b_lt["zero_write"])
            self.assertTrue(b_hc["zero_write"])

            # Empty directory also strictly zero-write
            before = _snapshot_tree(root)
            c_lt2, b_lt2 = town_view.read_v05_living_town_view(root, now_iso="2026-03-20T08:00:00+08:00")
            c_hc2, b_hc2 = craft_view.read_v05_home_craft_view(root, now_iso="2026-03-20T08:00:00+08:00")
            self.assertEqual(c_lt2, 200)
            self.assertEqual(c_hc2, 200)
            self.assertEqual(before, _snapshot_tree(root))

            # Ch.97 sound_scent_map has all 7 spots and is mirrored top-level + data
            ssm = b_lt2["sound_scent_map"]
            self.assertEqual(ssm, b_lt2["data"]["sound_scent_map"])
            self.assertEqual([row["spot_id"] for row in ssm], list(town_engine.CORE_SPOT_IDS))
            for row in ssm:
                for key in ("spot_id", "name_cn", "scent_cn", "sound_cn", "tactile_air_cn", "season", "weather"):
                    self.assertIn(key, row)
                    self.assertTrue(str(row[key]).strip())

            # Ch.101 read-only starter book & starter bulletin notice
            self.assertEqual(len(b_lt2["drifting_bookbox"]), 1)
            self.assertEqual(b_lt2["drifting_bookbox"][0]["status"], "IN_BOX")
            self.assertEqual(len(b_lt2["bulletin_followups"]), 1)
            self.assertEqual(b_lt2["bulletin_followups"][0]["notice_id"], "notice_alley_cat")

            # Ch.67 read-only 6 starter room objects with zero numeric wear/durability
            self.assertEqual(len(b_hc2["object_patina"]), 6)
            for obj in b_hc2["object_patina"]:
                self.assertIn(obj["micro_zone"], craft_engine.VALID_MICRO_ZONES)
                self.assertEqual(obj["patina_notes"], [])
                self.assertFalse(obj["retired"])
                for banned in ("durability", "durability_pct", "wear_pct", "hp", "condition_score"):
                    self.assertNotIn(banned, obj)

    def test_02_sound_scent_map_varies_by_season_weather_and_hour(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-ssm-") as td:
            root = Path(td)
            # Four seasons differ visibly
            season_scents: dict[str, str] = {}
            for season, iso_ts in (
                ("spring", "2026-03-20T10:00:00+08:00"),
                ("summer", "2026-06-21T10:00:00+08:00"),
                ("autumn", "2026-09-23T10:00:00+08:00"),
                ("winter", "2026-12-22T10:00:00+08:00"),
            ):
                _, body = town_view.read_v05_living_town_view(root, now_iso=iso_ts)
                by_id = {r["spot_id"]: r for r in body["sound_scent_map"]}
                self.assertEqual(by_id["bakery_corner"]["season"], season)
                season_scents[season] = by_id["bakery_corner"]["scent_cn"]
            self.assertEqual(len(set(season_scents.values())), 4)

            # Morning vs night sound differs
            _, am = town_view.read_v05_living_town_view(root, now_iso="2026-10-18T08:00:00+08:00")
            _, pm = town_view.read_v05_living_town_view(root, now_iso="2026-10-18T23:00:00+08:00")
            am_pier = {r["spot_id"]: r for r in am["sound_scent_map"]}["river_pier"]
            pm_pier = {r["spot_id"]: r for r in pm["sound_scent_map"]}["river_pier"]
            self.assertNotEqual(am_pier["sound_cn"], pm_pier["sound_cn"])

            # Clear vs rain vs snow differs
            _write_json(root / "world" / "world_state.json", {"weather": "rain", "season": "autumn"})
            _, rainy = town_view.read_v05_living_town_view(root, now_iso="2026-10-18T14:00:00+08:00")
            _write_json(root / "world" / "world_state.json", {"weather": "snow", "season": "winter"})
            _, snowy = town_view.read_v05_living_town_view(root, now_iso="2026-12-22T14:00:00+08:00")
            rain_home = {r["spot_id"]: r for r in rainy["sound_scent_map"]}["home"]
            snow_home = {r["spot_id"]: r for r in snowy["sound_scent_map"]}["home"]
            self.assertNotEqual(rain_home["tactile_air_cn"], snow_home["tactile_air_cn"])
            self.assertEqual(rain_home["weather"], "rain")
            self.assertEqual(snow_home["weather"], "snow")

    def test_03_drifting_bookbox_and_bulletin_followups(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-pubmem-") as td:
            root = Path(td)
            # Forged actor_side / author_side rejected with 400
            c_forge1, _ = town_view.handle_v05_living_town_action(
                root,
                {"action": "leave_drifting_book", "book_title": "旧书", "slip_note_cn": "附言", "actor_side": "subject"},
                has_subject_capability=False,
            )
            c_forge2, _ = town_view.handle_v05_living_town_action(
                root,
                {"action": "post_bulletin_followup", "followup_cn": "新消息", "author_side": "subject"},
                has_subject_capability=False,
            )
            self.assertEqual(c_forge1, 400)
            self.assertEqual(c_forge2, 400)

            # Partner leaves a book
            c1, b1 = town_view.handle_v05_living_town_action(
                root,
                {
                    "action": "leave_drifting_book",
                    "book_title": "雨天植物手绘集",
                    "slip_note_cn": "夹了一片压平的银杏叶，翻完放回木箱就行。",
                    "operation_key": "op_book_1",
                },
                has_subject_capability=False,
                now_iso="2026-11-19T14:00:00+08:00",
            )
            self.assertEqual(c1, 200)
            book = b1["action_receipt"]["book"]
            self.assertEqual(book["left_by_side"], "partner")
            self.assertEqual(book["spot_id"], "old_bookshop")
            self.assertEqual(book["status"], "IN_BOX")
            book_id = book["book_id"]

            # Subject borrows the book, then returns with a margin note
            c2, b2 = town_view.handle_v05_living_town_action(
                root,
                {
                    "action": "borrow_or_annotate_drifting_book",
                    "book_id": book_id,
                    "op": "borrow",
                    "margin_note_cn": "先借回书桌边翻两天。",
                },
                has_subject_capability=True,
                now_iso="2026-11-19T15:00:00+08:00",
            )
            self.assertEqual(c2, 200)
            self.assertEqual(b2["action_receipt"]["book"]["status"], "BORROWED")

            c3, b3 = town_view.handle_v05_living_town_action(
                root,
                {
                    "action": "borrow_or_annotate_drifting_book",
                    "book_id": book_id,
                    "op": "return_with_note",
                    "margin_note_cn": "第十二页那幅野蕨画得很像空坪墙根那丛。",
                },
                has_subject_capability=True,
                now_iso="2026-11-20T10:00:00+08:00",
            )
            self.assertEqual(c3, 200)
            ret_book = b3["action_receipt"]["book"]
            self.assertEqual(ret_book["status"], "IN_BOX")
            self.assertEqual(len(ret_book["margin_notes"]), 2)
            self.assertEqual(ret_book["margin_notes"][-1]["author_side"], "subject")

            # Bulletin followups: default notice_alley_cat + brand-new topic + cap at 6 followups
            for idx in range(7):
                cf, bf = town_view.handle_v05_living_town_action(
                    root,
                    {
                        "action": "post_bulletin_followup",
                        "followup_cn": f"巷口橘猫戴回小铜铃后的第 {idx + 1} 次晒太阳目击。",
                    },
                    has_subject_capability=(idx % 2 == 1),
                    now_iso=f"2026-11-20T1{idx}:00:00+08:00",
                )
                self.assertEqual(cf, 200)
            alley_notice = next(
                n for n in bf["bulletin_followups"] if n["notice_id"] == "notice_alley_cat"
            )
            self.assertEqual(len(alley_notice["followups"]), 6)

            c_new, b_new = town_view.handle_v05_living_town_action(
                root,
                {
                    "action": "post_bulletin_followup",
                    "title_cn": "旧书铺周末晒书通知",
                    "followup_cn": "天晴就把两箱画册搬到檐下通风，欢迎顺手翻翻。",
                },
                has_subject_capability=False,
                now_iso="2026-11-21T09:00:00+08:00",
            )
            self.assertEqual(c_new, 200)
            self.assertEqual(len(b_new["bulletin_followups"]), 2)

            # Cap drifting_bookbox at 16
            for idx in range(18):
                c_b, b_b = town_view.handle_v05_living_town_action(
                    root,
                    {
                        "action": "leave_drifting_book",
                        "book_title": f"街角手账第{idx + 1}册",
                        "slip_note_cn": "留给下一位路过的人。",
                    },
                    has_subject_capability=False,
                    now_iso="2026-11-21T10:00:00+08:00",
                )
                self.assertEqual(c_b, 200)
            self.assertEqual(len(b_b["drifting_bookbox"]), 16)


class TestV05HomeCraftEngineAndView(unittest.TestCase):
    def test_01_object_patina_and_gentle_retirement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-patina-") as td:
            root = Path(td)
            # Log patina + mended + retired_gently
            c1, b1 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "log_object_patina_or_repair",
                    "object_id": "ceramic_mug",
                    "event_kind": "patina",
                    "note_cn": "杯沿浸出淡淡茶渍，冬天捧着刚好顺手。",
                },
                has_subject_capability=False,
                now_iso="2026-11-19T18:00:00+08:00",
            )
            self.assertEqual(c1, 200)
            self.assertEqual(b1["action_receipt"]["entry"]["author_side"], "partner")
            self.assertFalse(b1["action_receipt"]["object"]["retired"])

            c2, b2 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "log_object_patina_or_repair",
                    "object_id": "ceramic_mug",
                    "event_kind": "mended",
                    "note_cn": "小磕口用金缮细细补好，倒热水依然稳当。",
                },
                has_subject_capability=True,
                now_iso="2026-11-20T09:00:00+08:00",
            )
            self.assertEqual(c2, 200)
            self.assertEqual(b2["action_receipt"]["entry"]["author_side"], "subject")
            self.assertEqual(len(b2["action_receipt"]["object"]["patina_notes"]), 2)

            c3, b3 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "log_object_patina_or_repair",
                    "object_id": "fountain_pen",
                    "event_kind": "retired_gently",
                    "note_cn": "笔尖写顺了整整三年，洗净收进木盒里歇着。",
                },
                has_subject_capability=True,
                now_iso="2026-11-21T10:00:00+08:00",
            )
            self.assertEqual(c3, 200)
            self.assertTrue(b3["action_receipt"]["object"]["retired"])

            # Retired object cannot be proposed for rearrangement (409)
            c4, b4 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "propose_arrangement_change",
                    "object_id": "fountain_pen",
                    "target_zone": "window_sill",
                    "reason_cn": "想挪去窗台",
                },
                has_subject_capability=False,
            )
            self.assertEqual(c4, 409)
            self.assertEqual(b4["reason_code"], "OBJECT_RETIRED")

    def test_02_arrangement_negotiation_no_forced_teleport_and_compromise(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-arrange-") as td:
            root = Path(td)
            # Partner proposes moving warm_desk_lamp from desk -> window_sill
            c1, b1 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "propose_arrangement_change",
                    "object_id": "warm_desk_lamp",
                    "target_zone": "window_sill",
                    "reason_cn": "傍晚亮灯时窗边光影会更柔和。",
                },
                has_subject_capability=False,
                now_iso="2026-11-19T18:10:00+08:00",
            )
            self.assertEqual(c1, 200)
            prop1 = b1["action_receipt"]["proposal"]
            self.assertEqual(prop1["status"], "OPEN")
            self.assertEqual(prop1["proposed_by"], "partner")
            self.assertFalse(b1["action_receipt"]["object_teleported"])

            # Lamp MUST NOT have teleported yet!
            lamp = next(o for o in b1["object_patina"] if o["object_id"] == "warm_desk_lamp")
            self.assertEqual(lamp["micro_zone"], "desk")

            # Subject resolves with compromise_middle -> hearth_table
            c2, b2 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "resolve_arrangement_compromise",
                    "proposal_id": prop1["proposal_id"],
                    "decision": "compromise_middle",
                    "compromise_zone": "hearth_table",
                    "note_cn": "放餐桌暖灯旁，看书和喝茶两边都照得到。",
                },
                has_subject_capability=True,
                now_iso="2026-11-19T18:15:00+08:00",
            )
            self.assertEqual(c2, 200)
            self.assertEqual(b2["action_receipt"]["proposal"]["status"], "COMPROMISED")
            self.assertEqual(b2["action_receipt"]["moved_to_zone"], "hearth_table")
            lamp2 = next(o for o in b2["object_patina"] if o["object_id"] == "warm_desk_lamp")
            self.assertEqual(lamp2["micro_zone"], "hearth_table")

            # Re-resolving already-settled proposal fails cleanly with 409
            c_dup, b_dup = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "resolve_arrangement_compromise",
                    "proposal_id": prop1["proposal_id"],
                    "decision": "keep_current_gently",
                },
                has_subject_capability=False,
            )
            self.assertEqual(c_dup, 409)
            self.assertEqual(b_dup["reason_code"], "PROPOSAL_ALREADY_RESOLVED")

            # Second proposal: keep_current_gently leaves object untouched
            _, b3 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "propose_arrangement_change",
                    "object_id": "linen_throw",
                    "target_zone": "entryway_shelf",
                    "reason_cn": "出门前方便顺手披一下。",
                },
                has_subject_capability=True,
                now_iso="2026-11-19T19:00:00+08:00",
            )
            pid2 = b3["action_receipt"]["proposal"]["proposal_id"]
            c4, b4 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "resolve_arrangement_compromise",
                    "proposal_id": pid2,
                    "decision": "keep_current_gently",
                    "note_cn": "夜里凉，还是先留在床头角最顺手。",
                },
                has_subject_capability=False,
                now_iso="2026-11-19T19:05:00+08:00",
            )
            self.assertEqual(c4, 200)
            self.assertEqual(b4["action_receipt"]["proposal"]["status"], "KEPT_CURRENT")
            throw = next(o for o in b4["object_patina"] if o["object_id"] == "linen_throw")
            self.assertEqual(throw["micro_zone"], "bedside")

            # Third proposal: accept_as_proposed moves object cleanly to target_zone
            _, b5 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "propose_arrangement_change",
                    "object_id": "wooden_chair",
                    "target_zone": "window_sill",
                    "reason_cn": "靠窗坐着晒午后太阳更舒服。",
                },
                has_subject_capability=False,
                now_iso="2026-11-19T19:10:00+08:00",
            )
            pid3 = b5["action_receipt"]["proposal"]["proposal_id"]
            c6, b6 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "resolve_arrangement_compromise",
                    "proposal_id": pid3,
                    "decision": "accept_as_proposed",
                },
                has_subject_capability=True,
                now_iso="2026-11-19T19:12:00+08:00",
            )
            self.assertEqual(c6, 200)
            self.assertEqual(b6["action_receipt"]["proposal"]["status"], "ACCEPTED")
            chair = next(o for o in b6["object_patina"] if o["object_id"] == "wooden_chair")
            self.assertEqual(chair["micro_zone"], "window_sill")

    def test_03_moving_carton_pack_and_unpack_promotes_to_object_patina(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-carton-") as td:
            root = Path(td)
            c1, b1 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "pack_moving_carton",
                    "origin_town_cn": "旧海港石阶镇",
                    "items": [
                        {"label_cn": "蓝釉小风铃", "keepsake_note_cn": "以前挂在阳台东侧，起风会轻响。"},
                        {"label_cn": "旧锡茶罐", "keepsake_note_cn": "装过一整秋的焙茶，罐盖还留着茶香。"},
                    ],
                },
                has_subject_capability=False,
                now_iso="2026-11-19T11:00:00+08:00",
            )
            self.assertEqual(c1, 200)
            carton = b1["action_receipt"]["carton"]
            self.assertEqual(carton["status"], "UNPACKING")
            self.assertEqual(b1["moving_carton"]["unpacked_remaining_total"], 2)
            cid = carton["carton_id"]
            item1_id = carton["items"][0]["item_id"]
            item2_id = carton["items"][1]["item_id"]

            # Unpack first item -> carton still UNPACKING, item promoted into object_patina!
            c2, b2 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "unpack_carton_item",
                    "carton_id": cid,
                    "item_id": item1_id,
                    "target_zone": "window_sill",
                    "settle_note_cn": "先挂在窗台风口边听听新镇的风。",
                },
                has_subject_capability=True,
                now_iso="2026-11-19T11:10:00+08:00",
            )
            self.assertEqual(c2, 200)
            self.assertEqual(b2["action_receipt"]["carton_status"], "UNPACKING")
            self.assertEqual(b2["moving_carton"]["unpacked_remaining_total"], 1)
            added1_id = b2["action_receipt"]["added_object"]["object_id"]
            patina_ids = [o["object_id"] for o in b2["object_patina"]]
            self.assertIn(added1_id, patina_ids)

            # Unpack second item -> carton becomes ALL_SETTLED
            c3, b3 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "unpack_carton_item",
                    "carton_id": cid,
                    "item_id": item2_id,
                    "target_zone": "hearth_table",
                },
                has_subject_capability=False,
                now_iso="2026-11-19T11:20:00+08:00",
            )
            self.assertEqual(c3, 200)
            self.assertEqual(b3["action_receipt"]["carton_status"], "ALL_SETTLED")
            self.assertEqual(b3["moving_carton"]["unpacked_remaining_total"], 0)
            self.assertEqual(len(b3["object_patina"]), 8)

            # Can log patina on the newly unpacked keepsake!
            c4, b4 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "log_object_patina_or_repair",
                    "object_id": added1_id,
                    "event_kind": "patina",
                    "note_cn": "新窗台的秋风把它吹响了第一声。",
                },
                has_subject_capability=True,
                now_iso="2026-11-19T11:30:00+08:00",
            )
            self.assertEqual(c4, 200)
            self.assertEqual(len(b4["action_receipt"]["object"]["patina_notes"]), 2)

    def test_04_parallel_postcard_real_png_embed_extract_roundtrip_and_guards(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-postcard-") as td:
            root = Path(td)
            # Mint postcard on winter solstice
            c_mint, b_mint = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "mint_parallel_postcard",
                    "greeting_cn": "冬至夜里暖灯亮着，给你留了一杯温姜茶的念想，愿你那头的小镇也安稳。",
                    "sender_label_cn": "坡顶小屋的阿宁",
                    "town_name_cn": "灯塔海坡镇",
                    "operation_key": "op_pc_mint_1",
                },
                has_subject_capability=False,
                now_iso="2026-12-22T20:15:00+08:00",
            )
            self.assertEqual(c_mint, 200)
            rec = b_mint["action_receipt"]
            png_b64 = rec["postcard_png_base64"]
            raw_png = base64.b64decode(png_b64)
            self.assertTrue(raw_png.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(hashlib.sha256(raw_png).hexdigest(), rec["sha256_hex"])

            # Verify dimensions in IHDR == 160x104
            w, h = struct.unpack(">II", raw_png[16:24])
            self.assertEqual((w, h), (160, 104))

            # Verify DIRECTLY via v05_unified_world_fabric.extract_postcard_png_chunk!
            extracted_direct = fabric.extract_postcard_png_chunk(raw_png)
            self.assertIsNotNone(extracted_direct)
            assert extracted_direct is not None
            self.assertEqual(extracted_direct["town"], "灯塔海坡镇")
            self.assertIn("冬至夜里暖灯亮着", extracted_direct["note"])

            # Verify NO bulky base64 stored in world/v05_home_craft.json, and NO local paths leaked!
            saved_text = (root / "world" / "v05_home_craft.json").read_text(encoding="utf-8")
            self.assertNotIn("postcard_png_base64", saved_text)
            self.assertNotIn(str(root), saved_text)
            self.assertNotIn(str(root), raw_png.decode("latin1"))
            self.assertEqual(
                set(rec["metadata"].keys()),
                {"schema", "town_name_cn", "season", "solar_term_name_cn", "greeting_cn", "sender_label_cn", "sent_date"},
            )
            self.assertEqual(rec["metadata"]["schema"], "nyairo.v05.postcard.v1")
            self.assertEqual(rec["metadata"]["season"], "winter")
            self.assertEqual(rec["metadata"]["solar_term_name_cn"], "冬至")

            # Import the minted PNG into a SECOND independent town!
            root2 = Path(td) / "parallel_friend_town"
            c_imp, b_imp = craft_view.handle_v05_home_craft_action(
                root2,
                {
                    "action": "import_parallel_postcard",
                    "png_base64": png_b64,
                    "from_friend_note_cn": "从朋友那台电脑寄来的冬至明信片。",
                },
                has_subject_capability=False,
                now_iso="2026-12-23T09:00:00+08:00",
            )
            self.assertEqual(c_imp, 200)
            pinned = b_imp["parallel_postcards"]["received"][0]
            self.assertEqual(pinned["town_name_cn"], "灯塔海坡镇")
            self.assertEqual(pinned["season"], "winter")
            self.assertEqual(pinned["solar_term_name_cn"], "冬至")
            self.assertEqual(pinned["sender_label_cn"], "坡顶小屋的阿宁")
            self.assertEqual(pinned["greeting_cn"], "冬至夜里暖灯亮着，给你留了一杯温姜茶的念想，愿你那头的小镇也安稳。")
            self.assertEqual(pinned["from_friend_note_cn"], "从朋友那台电脑寄来的冬至明信片。")

            # Import PNG created purely via fabric.embed_postcard_png_chunk (without companion chunk)
            bare_png = craft_engine.render_seasonal_postcard_png(season="spring", weather="clear", hour=9)
            fabric_only_png = fabric.embed_postcard_png_chunk(
                bare_png,
                origin_town="松阴山镇",
                stamp_code="spr-05-2026-03-20",
                gift_slot="wild_mountain_tea",
                FrontNoteCn="春分快乐，山风很软。",
                hops=1,
            )
            c_bare, b_bare = craft_view.handle_v05_home_craft_action(
                root2,
                {
                    "action": "import_parallel_postcard",
                    "png_base64": base64.b64encode(fabric_only_png).decode("ascii"),
                },
                has_subject_capability=False,
                now_iso="2026-03-21T10:00:00+08:00",
            )
            self.assertEqual(c_bare, 200)
            self.assertEqual(b_bare["action_receipt"]["postcard"]["town_name_cn"], "松阴山镇")
            self.assertEqual(b_bare["action_receipt"]["postcard"]["solar_term_name_cn"], "春分")

            # Reject corrupt / plain / non-PNG / oversized inputs with 400 INVALID_POSTCARD_PNG
            for bad_b64 in (
                base64.b64encode(b"not a png at all").decode("ascii"),
                base64.b64encode(bare_png).decode("ascii"),  # valid PNG, missing postcard chunk
                base64.b64encode(raw_png[:-10] + bytes([raw_png[-10] ^ 0xFF]) + raw_png[-9:]).decode("ascii"),  # corrupt CRC
                base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"A" * (260 * 1024)).decode("ascii"),  # > 256 KB
                "!!!not-base64!!!",
            ):
                c_bad, b_bad = craft_view.handle_v05_home_craft_action(
                    root2,
                    {"action": "import_parallel_postcard", "png_base64": bad_b64},
                    has_subject_capability=False,
                )
                self.assertEqual(c_bad, 400)
                self.assertEqual(b_bad["reason_code"], "INVALID_POSTCARD_PNG")

    def test_05_real_http_server_roundtrip_and_security_gates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-craft-http-") as td:
            root = Path(td)
            inst = {
                "root": str(root),
                "subject": "chiyo.global",
                "world_home": str(root / "world"),
                "supply_socket": str(root / "run" / "supply.sock"),
                "private_items_artifact_id": "art_private_items",
                "artifact_grant_id": "grant_private_items",
                "workspace_id": "ws_default",
            }
            svc = phone_service.PhoneService(inst)
            csrf = "test-v05-home-craft-csrf"
            srv = phone_server.PhoneHTTPServer(("127.0.0.1", 0), service=svc, csrf_token=csrf)
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                base = f"http://127.0.0.1:{port}"
                # Zero-write GET check on both living-town and home-craft
                before = _snapshot_tree(root)
                for route in ("/api/phone/v05/living-town", "/api/phone/v05/home-craft"):
                    with urllib.request.urlopen(base + route, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        doc = json.loads(resp.read().decode("utf-8"))
                        self.assertTrue(doc["ok"])
                        self.assertEqual(doc["llm_calls"], 0)
                        self.assertTrue(doc["zero_write"])
                self.assertEqual(before, _snapshot_tree(root))

                # Missing CSRF returns 403
                bad_req = urllib.request.Request(
                    base + "/api/phone/v05/home-craft",
                    data=json.dumps({"action": "mint_parallel_postcard", "greeting_cn": "你好"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx_err:
                    urllib.request.urlopen(bad_req, timeout=5)
                self.assertEqual(ctx_err.exception.code, 403)

                # Forged actor_side returns 400
                forge_req = urllib.request.Request(
                    base + "/api/phone/v05/home-craft",
                    data=json.dumps({
                        "action": "log_object_patina_or_repair",
                        "object_id": "ceramic_mug",
                        "event_kind": "patina",
                        "note_cn": "测试",
                        "actor_side": "subject",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx_err2:
                    urllib.request.urlopen(forge_req, timeout=5)
                self.assertEqual(ctx_err2.exception.code, 400)

                # Real HTTP mint -> import postcard roundtrip
                mint_req = urllib.request.Request(
                    base + "/api/phone/v05/home-craft",
                    data=json.dumps({
                        "action": "mint_parallel_postcard",
                        "greeting_cn": "窗外刚放晴，寄一片海风给你。",
                        "town_name_cn": "海坡小屋",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                    method="POST",
                )
                with urllib.request.urlopen(mint_req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    mint_doc = json.loads(resp.read().decode("utf-8"))
                    b64_png = mint_doc["action_receipt"]["postcard_png_base64"]

                imp_req = urllib.request.Request(
                    base + "/api/phone/v05/home-craft",
                    data=json.dumps({
                        "action": "import_parallel_postcard",
                        "png_base64": b64_png,
                        "from_friend_note_cn": "HTTP 实测回传明信片",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                    method="POST",
                )
                with urllib.request.urlopen(imp_req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    imp_doc = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(len(imp_doc["parallel_postcards"]["received"]), 1)
                    self.assertEqual(
                        imp_doc["parallel_postcards"]["received"][0]["greeting_cn"],
                        "窗外刚放晴，寄一片海风给你。",
                    )

                # Real HTTP drifting bookbox & bulletin followup on living-town
                book_req = urllib.request.Request(
                    base + "/api/phone/v05/living-town",
                    data=json.dumps({
                        "action": "leave_drifting_book",
                        "book_title": "渡口风物志",
                        "slip_note_cn": "等船时随手翻完的一本小书。",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                    method="POST",
                )
                with urllib.request.urlopen(book_req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    book_doc = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(len(book_doc["drifting_bookbox"]), 2)
                    self.assertEqual(len(book_doc["sound_scent_map"]), 7)
            finally:
                srv.shutdown()
                srv.server_close()

    def test_06_file_hygiene_lf_no_bom_zero_emoji_neutral_pronoun(self) -> None:
        owned_files = [
            REPO_ROOT / "components" / "world" / "scripts" / "v05_living_town_engine.py",
            REPO_ROOT / "components" / "phone" / "v05_living_town_view.py",
            REPO_ROOT / "components" / "world" / "scripts" / "v05_home_craft_engine.py",
            REPO_ROOT / "components" / "phone" / "v05_home_craft_view.py",
            REPO_ROOT / "tests" / "test_v05_home_craft_and_sensory_map.py",
        ]
        for path in owned_files:
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {path}")
            self.assertNotIn(b"\r\n", raw, f"CRLF found in {path}")
            text = raw.decode("utf-8")
            for idx, ch in enumerate(text):
                self.assertNotEqual(
                    unicodedata.category(ch),
                    "So",
                    f"Emoji/So char {ch!r} at index {idx} in {path}",
                )
            if path.name != "test_v05_home_craft_and_sensory_map.py":
                self.assertNotIn("她", text, f"Non-neutral pronoun found in {path}")
                self.assertNotIn("千代", text, f"Hardcoded character name found in {path}")

    def test_07_live_clock_now_iso_none_utc_conversion_and_external_stamp_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-live-clock-") as td:
            root = Path(td)
            live_before = datetime.now(season_pulse.DEFAULT_TZ).replace(microsecond=0)
            lt_live = town_engine.read_living_town_snapshot(root, now_iso=None)
            hc_live = craft_engine.read_home_craft_snapshot(root, now_iso=None)
            live_after = datetime.now(season_pulse.DEFAULT_TZ).replace(microsecond=0)

            expected_pulse = season_pulse.project_season_solar_pulse(now_iso=live_before)
            lt_dt = datetime.fromisoformat(lt_live["observed_at"])
            hc_dt = datetime.fromisoformat(hc_live["observed_at"])

            # (a) now_iso=None resolves to live wall clock/season/solar-term, NEVER hardcoded 2026-10-18 / 寒露
            self.assertGreaterEqual(lt_dt, live_before)
            self.assertLessEqual(lt_dt, live_after)
            self.assertGreaterEqual(hc_dt, live_before)
            self.assertLessEqual(hc_dt, live_after)
            self.assertEqual(lt_live["world_context"]["date"], live_before.strftime("%Y-%m-%d"))
            self.assertEqual(hc_live["world_context"]["date"], live_before.strftime("%Y-%m-%d"))
            self.assertEqual(lt_live["world_context"]["season"], expected_pulse["season"])
            self.assertEqual(hc_live["world_context"]["season"], expected_pulse["season"])
            self.assertEqual(
                lt_live["season_solar"]["solar_term"]["term_name_cn"],
                expected_pulse["solar_term"]["term_name_cn"],
            )
            self.assertEqual(
                hc_live["season_solar"]["solar_term"]["term_name_cn"],
                expected_pulse["solar_term"]["term_name_cn"],
            )

            # (b) UTC "Z" converts into UTC+8 night (2026-11-19T14:20:00Z == 22:20+08, NOT hour=14 midday)
            utc_iso = "2026-11-19T14:20:00Z"
            lt_utc = town_engine.read_living_town_snapshot(root, now_iso=utc_iso)
            hc_utc = craft_engine.read_home_craft_snapshot(root, now_iso=utc_iso)
            self.assertEqual(lt_utc["world_context"]["hour"], 22)
            self.assertEqual(hc_utc["world_context"]["hour"], 22)
            self.assertTrue(lt_utc["observed_at"].endswith("+08:00"))
            self.assertTrue(hc_utc["observed_at"].endswith("+08:00"))
            home_ssm = next(r for r in lt_utc["sound_scent_map"] if r["spot_id"] == "home")
            self.assertIn("深夜", home_ssm["sound_cn"])
            self.assertNotIn("午后", home_ssm["sound_cn"])

            # (c) Bare external postcard stamp (non-standard stamp without season prefix) uses now_dt calendar season
            bare_png = craft_engine.render_seasonal_postcard_png(season="spring", weather="clear", hour=10)
            ext_png = fabric.embed_postcard_png_chunk(
                bare_png,
                origin_town="青溪古镇",
                stamp_code="CUSTOM-STAMP-01",
                gift_slot="wild_mountain_tea",
                FrontNoteCn="春游路过寄来一张明信片。",
                hops=1,
            )
            c_ext, b_ext = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "import_parallel_postcard",
                    "png_base64": base64.b64encode(ext_png).decode("ascii"),
                },
                has_subject_capability=False,
                now_iso="2026-04-12T10:00:00+08:00",
            )
            self.assertEqual(c_ext, 200)
            self.assertEqual(b_ext["action_receipt"]["postcard"]["season"], "spring")


if __name__ == "__main__":
    unittest.main()

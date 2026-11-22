#!/usr/bin/env python3
"""Deterministic brain, tool-routing, security & soak wiring tests for Nyairo 0.5
Home Craft & Living Town Public Memory (Ch. 67, 97, 98, 101, 103, 106, 112).

Verifies:
- Frozen invariants: len(LIFE_UPDATE_SURFACES) == 37, MAX_LIVED_CONTEXT_CHARS == 1800,
  len(plugins.chiyo._PHONE_AND_WORLD_GUIDANCE) == 1199, len(soak_runner.GET_ROUTES) == 24.
- Dynamic routing for all 8 subject actions across /api/phone/v05/living-town and
  /api/phone/v05/home-craft, updated _SURFACE_SUPPORTED_HINTS, and
  PARTNER_IMPERSONATION_REFUSED on pack_moving_carton & import_parallel_postcard.
- Zero-write lived_context_bridge disk loader for <root>/world/v05_home_craft.json,
  all 5 new compact plain-Chinese cues inside _format_v05_living_town_and_spark,
  Calm Gate (total_chars == 0 on empty root, v04_line == 0 on calm root), <= 1800 chars,
  and file hygiene (pure LF, UTF-8 no BOM, zero unicode.So emojis, neutral TA).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "chiyo_bundle",
    REPO_ROOT / "scripts",
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from scripts import v02_wall_clock_soak as soak_runner  # noqa: E402

T_CALM = "2026-11-16T11:00:00+08:00"
T_EVENING = "2026-11-16T19:30:00+08:00"


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _load_chiyo_plugin_module() -> Any:
    plugin_path = REPO_ROOT / "plugins" / "chiyo" / "__init__.py"
    spec = importlib.util.spec_from_file_location("v05_home_craft_plugin_check", plugin_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestV05HomeCraftStaticInvariantsAndSoak(unittest.TestCase):
    def test_01_frozen_invariants_soak_routes_and_file_hygiene(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)

        plugin_mod = _load_chiyo_plugin_module()
        self.assertEqual(len(plugin_mod._PHONE_AND_WORLD_GUIDANCE), 1199)

        self.assertEqual(len(soak_runner.GET_ROUTES), 24)
        self.assertEqual(len(set(soak_runner.GET_ROUTES)), 24)
        self.assertIn("/api/phone/v05/living-town", soak_runner.GET_ROUTES)
        self.assertIn("/api/phone/v05/inner-spark", soak_runner.GET_ROUTES)
        self.assertIn("/api/phone/v05/home-craft", soak_runner.GET_ROUTES)

        owned_files = (
            REPO_ROOT / "chiyo_bundle" / "phone_tool.py",
            REPO_ROOT / "chiyo_bundle" / "lived_context_bridge.py",
            REPO_ROOT / "scripts" / "v02_wall_clock_soak.py",
            REPO_ROOT / "tests" / "test_v02_fix_brain_autoproducers_and_security.py",
            Path(__file__).resolve(),
        )
        for path in owned_files:
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM found in {path}")
            self.assertNotIn(b"\r\n", raw, f"CRLF found in {path}")
            text = raw.decode("utf-8")
            emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"unicode.So emoji found in {path}: {emojis[:5]}")

        self_text = Path(__file__).resolve().read_text(encoding="utf-8")
        self.assertNotIn("\u5979", self_text)
        self.assertNotIn("\u5343\u4ee3", self_text)


class TestV05HomeCraftPhoneToolRoutingAndSecurity(unittest.TestCase):
    def test_01_supported_hints_include_all_eight_subject_actions(self) -> None:
        nb_hint = phone_tool._surface_supported_hint("v04_neighborhood")
        self.assertIsNotNone(nb_hint)
        assert nb_hint is not None
        for act in (
            "leave_drifting_book",
            "borrow_or_annotate_drifting_book",
            "post_bulletin_followup",
        ):
            self.assertIn(act, nb_hint["subject_actions"])
            self.assertIn(act, nb_hint["core_fields"])

        ep_hint = phone_tool._surface_supported_hint("v04_elastic_play")
        self.assertIsNotNone(ep_hint)
        assert ep_hint is not None
        for act in (
            "log_object_patina_or_repair",
            "propose_arrangement_change",
            "resolve_arrangement_compromise",
            "unpack_carton_item",
            "mint_parallel_postcard",
        ):
            self.assertIn(act, ep_hint["subject_actions"])
            self.assertIn(act, ep_hint["core_fields"])

        for blocked in ("pack_moving_carton", "import_parallel_postcard"):
            self.assertNotIn(blocked, ep_hint["subject_actions"])
            self.assertIn(blocked, phone_tool._PARTNER_ONLY_SUBACTIONS["v04_elastic_play"])

    def test_02_dynamic_routing_for_all_eight_subject_actions(self) -> None:
        town_cases = (
            (
                "leave_drifting_book",
                {
                    "action": "leave_drifting_book",
                    "book_title": "海风旧札",
                    "slip_note_cn": "雨天翻着很安静",
                    "spot_id": "old_bookshop",
                },
            ),
            (
                "borrow_or_annotate_drifting_book",
                {
                    "action": "borrow_or_annotate_drifting_book",
                    "book_id": "book_001",
                    "op": "return_with_note",
                    "margin_note_cn": "第三章写得真柔和",
                },
            ),
            (
                "post_bulletin_followup",
                {
                    "action": "post_bulletin_followup",
                    "notice_id": "notice_alley_cat",
                    "title_cn": "巷口橘猫近况",
                    "followup_cn": "傍晚在面包铺檐下晒了一会儿暖气",
                },
            ),
        )
        for act, payload in town_cases:
            surf, path, body, cap_req = phone_tool._prepare_life_update(
                {"target_surface": "v04_neighborhood", "payload": payload},
                key=f"op_lt_{act}",
            )
            self.assertEqual(surf, "v04_neighborhood")
            self.assertEqual(path, "/api/phone/v05/living-town", act)
            self.assertTrue(cap_req, act)
            self.assertEqual(body["operation_key"], f"op_lt_{act}")
            self.assertEqual(body["action"], act)
            if act == "borrow_or_annotate_drifting_book":
                self.assertEqual(body.get("op"), "return_with_note")

        home_craft_cases = (
            (
                "log_object_patina_or_repair",
                {
                    "action": "log_object_patina_or_repair",
                    "object_id": "ceramic_mug",
                    "event_kind": "patina",
                    "note_cn": "杯口温润了一圈茶色",
                },
            ),
            (
                "propose_arrangement_change",
                {
                    "action": "propose_arrangement_change",
                    "object_id": "window_plant_pot",
                    "target_zone": "desk",
                    "reason_cn": "这周阴雨想挪近暖灯旁",
                },
            ),
            (
                "resolve_arrangement_compromise",
                {
                    "action": "resolve_arrangement_compromise",
                    "proposal_id": "prop_001",
                    "decision": "compromise_middle",
                    "compromise_zone": "hearth_table",
                    "note_cn": "放茶桌边彼此都顺手",
                },
            ),
            (
                "unpack_carton_item",
                {
                    "action": "unpack_carton_item",
                    "carton_id": "carton_001",
                    "item_id": "item_001",
                    "target_zone": "entryway_shelf",
                    "settle_note_cn": "先搁在玄关架上",
                },
            ),
            (
                "mint_parallel_postcard",
                {
                    "action": "mint_parallel_postcard",
                    "greeting_cn": "今晚屋檐落小雨，愿那头安好",
                    "sender_label_cn": "TA",
                    "town_name_cn": "海坡小镇",
                },
            ),
        )
        for act, payload in home_craft_cases:
            surf, path, body, cap_req = phone_tool._prepare_life_update(
                {"target_surface": "v04_elastic_play", "payload": payload},
                key=f"op_hc_{act}",
            )
            self.assertEqual(surf, "v04_elastic_play")
            self.assertEqual(path, "/api/phone/v05/home-craft", act)
            self.assertTrue(cap_req, act)
            self.assertEqual(body["operation_key"], f"op_hc_{act}")
            self.assertEqual(body["action"], act)

        # Existing v04 & v05 inner-spark routing stays intact
        _, path_v04_nb, _, _ = phone_tool._prepare_life_update(
            {"target_surface": "v04_neighborhood", "payload": {"action": "water_or_move_plant", "operation": "water"}},
            key="op_keep_v04_nb",
        )
        self.assertEqual(path_v04_nb, "/api/phone/v04/neighborhood")

        _, path_v04_ep, _, _ = phone_tool._prepare_life_update(
            {"target_surface": "v04_elastic_play", "payload": {"action": "set_day_direction", "direction": "慢读"}},
            key="op_keep_v04_ep",
        )
        self.assertEqual(path_v04_ep, "/api/phone/v04/elastic-play")

        _, path_spark, _, _ = phone_tool._prepare_life_update(
            {"target_surface": "v04_elastic_play", "payload": {"action": "record_subject_desire", "text": "喝热茶"}},
            key="op_keep_spark",
        )
        self.assertEqual(path_spark, "/api/phone/v05/inner-spark")

    def test_03_partner_impersonation_refused_and_restricted_memory_gate(self) -> None:
        blocked_payloads = (
            {
                "action": "pack_moving_carton",
                "origin_town_cn": "旧海港",
                "items": [{"label_cn": "旧陶壶", "keepsake_note_cn": "搬家带着的"}],
            },
            {
                "action": "PACK_MOVING_CARTON",
                "origin_town_cn": "旧海港",
                "items": [{"label_cn": "旧陶壶", "keepsake_note_cn": "搬家带着的"}],
            },
            {
                "action": "import_parallel_postcard",
                "png_base64": "iVBORw0KGgo=",
                "from_friend_note_cn": "朋友寄来的",
            },
            {
                "action": "IMPORT_PARALLEL_POSTCARD",
                "png_base64": "iVBORw0KGgo=",
            },
        )
        for payload in blocked_payloads:
            with self.assertRaises(phone_tool.PhoneToolRefused) as ctx:
                phone_tool._prepare_life_update(
                    {"target_surface": "v04_elastic_play", "payload": payload},
                    key="op_block_partner",
                )
            self.assertEqual(ctx.exception.reason_code, "PARTNER_IMPERSONATION_REFUSED", payload)

        # Spoofed actor_side / identity keys are also rejected pre-flight
        with self.assertRaises(phone_tool.PhoneToolRefused):
            phone_tool._prepare_life_update(
                {
                    "target_surface": "v04_elastic_play",
                    "payload": {
                        "action": "mint_parallel_postcard",
                        "greeting_cn": "问候",
                        "actor_side": "partner",
                    },
                },
                key="op_spoof",
            )

        # Restricted memory without private_artifacts blocks v04_elastic_play home-craft writes
        with tempfile.TemporaryDirectory(prefix="v05-hc-memgate-") as td:
            prof = Path(td)
            uid = os.getuid() if hasattr(os, "getuid") else 0
            _write_json(
                prof / "chiyo" / "config.json",
                {
                    "cli_owner": True,
                    "local_owner_uid": uid,
                    "memory": True,
                    "memory_tool_policy": "restricted",
                    "phone": {
                        "enabled": True,
                        "write_enabled": True,
                        "private_artifacts": False,
                        "base_url": "http://127.0.0.1:3899",
                        "profile_home": str(prof),
                    },
                },
            )
            raw = phone_tool.phone_handler(
                {
                    "action": "phone_life_update",
                    "target_surface": "v04_elastic_play",
                    "payload": {
                        "action": "log_object_patina_or_repair",
                        "object_id": "ceramic_mug",
                        "event_kind": "patina",
                        "note_cn": "试写",
                    },
                    "operation_key": "op_gate_hc",
                },
                hermes_home=str(prof),
                platform="cli",
                conversation_key="cli:local",
            )
            doc = json.loads(raw)
            self.assertFalse(doc.get("ok"))
            self.assertEqual(doc.get("reason_code"), "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")


class TestV05HomeCraftLivedContextBridgeAndCalmGate(unittest.TestCase):
    def test_01_empty_root_and_calm_settled_room_stay_silent_zero_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-hc-calm-") as td:
            root = Path(td)
            before_empty = _snapshot_tree(root)

            # 1) Unconfigured empty root -> calm_silent == True, total_chars == 0, zero-write
            bd_empty = lcb.measure_context_budget_breakdown(
                root, cfg={}, incoming_text="你好", now_iso=T_CALM
            )
            self.assertTrue(bd_empty["calm_silent"])
            self.assertEqual(bd_empty["total_chars"], 0)
            self.assertEqual(bd_empty["rendered_text"], "")
            self.assertEqual(_snapshot_tree(root), before_empty)

            # 2) Configured calm root with phone dir only -> v04_line == 0, v05_town_spark_line == 0
            (root / "phone").mkdir(parents=True)
            cfg = {"day_root": str(root), "phone": {"enabled": False, "private_artifacts": True}}
            before_calm = _snapshot_tree(root)
            bd_calm = lcb.measure_context_budget_breakdown(
                root, cfg=cfg, incoming_text="你好", now_iso=T_CALM
            )
            self.assertEqual(_snapshot_tree(root), before_calm)
            self.assertEqual(bd_calm["per_section_chars"].get("v04_line", 0), 0)
            self.assertEqual(bd_calm["per_section_chars"].get("v05_town_spark_line", 0), 0)

            # 3) Settled/empty v05_home_craft.json on disk -> still zero home-craft cues
            _write_json(
                root / "world" / "v05_home_craft.json",
                {
                    "schema_version": "world.v05.home_craft.v1",
                    "object_patina": [
                        {
                            "object_id": "ceramic_mug",
                            "name_cn": "粗陶茶杯",
                            "micro_zone": "desk",
                            "retired": False,
                            "patina_notes": [],
                        }
                    ],
                    "home_arrangement": {
                        "proposals": [
                            {
                                "proposal_id": "prop_done",
                                "object_id": "ceramic_mug",
                                "object_name_cn": "粗陶茶杯",
                                "from_zone": "desk",
                                "target_zone": "window_sill",
                                "proposed_by": "partner",
                                "reason_cn": "透透气",
                                "status": "ACCEPTED",
                                "resolution_note_cn": "已挪过去",
                            }
                        ]
                    },
                    "moving_carton": {
                        "cartons": [
                            {
                                "carton_id": "c_done",
                                "origin_town_cn": "旧镇",
                                "status": "ALL_SETTLED",
                                "items": [
                                    {
                                        "item_id": "i_done",
                                        "label_cn": "旧书签",
                                        "unpacked": True,
                                        "placed_zone": "desk",
                                    }
                                ],
                            }
                        ]
                    },
                    "parallel_postcards": {"sent": [], "received": []},
                },
            )
            before_settled = _snapshot_tree(root)
            bd_settled = lcb.measure_context_budget_breakdown(
                root, cfg=cfg, incoming_text="你好", now_iso=T_CALM
            )
            self.assertEqual(_snapshot_tree(root), before_settled)
            self.assertEqual(bd_settled["per_section_chars"].get("v04_line", 0), 0)
            self.assertEqual(bd_settled["per_section_chars"].get("v05_town_spark_line", 0), 0)
            for absent in ("此刻街气:", "漂流书箱", "布置商量:", "玄关搬家纸箱", "收到平行小镇明信片:"):
                self.assertNotIn(absent, bd_settled["rendered_text"])

            # 4) Formatter stays None when state_exists is explicitly False
            self.assertIsNone(
                lcb._format_v05_living_town_and_spark(
                    {
                        "state_exists": False,
                        "sound_scent_map": [{"spot_id": "home", "name_cn": "小屋", "scent_cn": "茶香", "sound_cn": "风声"}],
                        "drifting_bookbox": [{"book_title": "旧书", "slip_note_cn": "留条"}],
                    },
                    {"state_exists": False},
                    home_craft_doc={
                        "state_exists": False,
                        "moving_carton": {"cartons": [{"status": "UNPACKING", "items": [{"unpacked": False}]}]},
                    },
                    include_private=True,
                )
            )

    def test_02_surfaces_all_five_lived_cues_from_disk_zero_write_within_1800(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-hc-rich-") as td:
            root = Path(td)
            (root / "phone").mkdir(parents=True)

            _write_json(
                root / "world" / "v05_living_town.json",
                {
                    "schema_version": "world.v05.living_town.v1",
                    "revision": 2,
                    "updated_at": T_EVENING,
                    "ta_presence": {
                        "location_id": "old_bookshop",
                        "solo_walk_lock": False,
                        "companion_note": "",
                    },
                    "sound_scent_map": [
                        {
                            "spot_id": "home",
                            "name_cn": "海坡小屋",
                            "scent_cn": "暖茶与旧木桌面味",
                            "sound_cn": "窗框微风轻响",
                            "tactile_air_cn": "微润室内暖意",
                        },
                        {
                            "spot_id": "old_bookshop",
                            "name_cn": "潮汐旧书铺",
                            "scent_cn": "老纸页混着淡淡松木香",
                            "sound_cn": "慢翻书页与门口风铃",
                            "tactile_air_cn": "干爽微凉",
                        },
                    ],
                    "drifting_bookbox": [
                        {
                            "book_id": "book_starter",
                            "book_title": "潮声手记",
                            "spot_id": "old_bookshop",
                            "status": "IN_BOX",
                            "left_by_side": "partner",
                            "slip_note_cn": "适合雨夜翻看",
                            "margin_notes": [
                                {
                                    "note_cn": "读到灯塔那页心里一暖",
                                    "author_side": "subject",
                                    "created_at": T_EVENING,
                                }
                            ],
                            "created_at": T_CALM,
                        }
                    ],
                    "bulletin_followups": [
                        {
                            "notice_id": "notice_alley_cat",
                            "title_cn": "巷口橘猫",
                            "followups": [{"followup_cn": "今晚又来打呼噜了"}],
                        }
                    ],
                },
            )

            _write_json(
                root / "world" / "v05_home_craft.json",
                {
                    "schema_version": "world.v05.home_craft.v1",
                    "revision": 3,
                    "updated_at": T_EVENING,
                    "object_patina": [
                        {
                            "object_id": "ceramic_mug",
                            "name_cn": "粗陶茶杯",
                            "micro_zone": "desk",
                            "retired": False,
                            "patina_notes": [{"event_kind": "patina", "note_cn": "杯身越用越温润"}],
                        }
                    ],
                    "home_arrangement": {
                        "proposals": [
                            {
                                "proposal_id": "prop_closed",
                                "object_id": "wooden_chair",
                                "object_name_cn": "旧木椅",
                                "from_zone": "desk",
                                "target_zone": "bedside",
                                "proposed_by": "partner",
                                "reason_cn": "旧提议",
                                "status": "KEPT_CURRENT",
                                "resolution_note_cn": "先不动",
                            },
                            {
                                "proposal_id": "prop_open_1",
                                "object_id": "warm_desk_lamp",
                                "object_name_cn": "书桌暖灯",
                                "from_zone": "desk",
                                "target_zone": "window_sill",
                                "proposed_by": "partner",
                                "reason_cn": "雨夜看窗外更柔",
                                "status": "OPEN",
                                "resolution_note_cn": "",
                            },
                        ]
                    },
                    "moving_carton": {
                        "cartons": [
                            {
                                "carton_id": "carton_old_port",
                                "origin_town_cn": "旧海港小镇",
                                "status": "UNPACKING",
                                "items": [
                                    {"item_id": "it_1", "label_cn": "铜风铃", "unpacked": True, "placed_zone": "window_sill"},
                                    {"item_id": "it_2", "label_cn": "旧陶罐", "unpacked": False, "placed_zone": None},
                                    {"item_id": "it_3", "label_cn": "亚麻桌布", "unpacked": False, "placed_zone": None},
                                ],
                            },
                            {
                                "carton_id": "carton_mountain",
                                "origin_town_cn": "松谷小镇",
                                "status": "UNPACKING",
                                "items": [
                                    {"item_id": "it_4", "label_cn": "松果挂饰", "unpacked": False, "placed_zone": None},
                                ],
                            },
                            {
                                "carton_id": "carton_settled",
                                "origin_town_cn": "已完成箱",
                                "status": "ALL_SETTLED",
                                "items": [
                                    {"item_id": "it_5", "label_cn": "旧杯垫", "unpacked": True, "placed_zone": "desk"},
                                ],
                            },
                        ]
                    },
                    "parallel_postcards": {
                        "sent": [],
                        "received": [
                            {
                                "postcard_id": "pc_001",
                                "town_name_cn": "松风镇",
                                "season": "winter",
                                "solar_term_name_cn": "立冬",
                                "greeting_cn": "山街今晚飘了细雪，问候那边",
                                "sender_label_cn": "阿青",
                            }
                        ],
                    },
                },
            )

            before = _snapshot_tree(root)
            cfg = {"day_root": str(root), "phone": {"enabled": False, "private_artifacts": True}}

            ctx = lcb.build_lived_turn_context(
                root, cfg=cfg, incoming_text="晚上屋里和街上怎么样", now_iso=T_EVENING
            )
            bd = lcb.measure_context_budget_breakdown(
                root, cfg=cfg, incoming_text="晚上屋里和街上怎么样", now_iso=T_EVENING
            )

            self.assertEqual(_snapshot_tree(root), before, "lived_context_bridge MUST be 100% zero-write")
            self.assertTrue(bd["within_budget"])
            self.assertLessEqual(len(ctx), lcb.MAX_LIVED_CONTEXT_CHARS)
            self.assertLessEqual(bd["total_chars"], 1800)

            self.assertIn("0.5小镇与心火线索", ctx)
            self.assertIn("此刻街气:", ctx)
            self.assertIn("漂流书箱《潮声手记》", ctx)
            self.assertIn("读到灯塔那页心里一暖", ctx)
            self.assertIn("布置商量:书桌暖灯挪到窗台", ctx)
            self.assertIn("玄关搬家纸箱尚余3件待拆", ctx)
            self.assertIn("收到平行小镇明信片:松风镇(立冬)", ctx)
            self.assertIn("山街今晚飘了细雪", ctx)

    def test_03_live_clock_and_simulated_seasons_stay_unified_without_autumn_contradiction(self) -> None:
        from components.world.scripts import v05_home_craft_engine as hc_engine
        from components.world.scripts import v05_living_town_engine as lt_engine
        from components.world.scripts import v05_season_solar_pulse as ssp

        season_labels = {
            "spring": "春日",
            "summer": "夏时",
            "autumn": "秋意",
            "winter": "冬景",
        }
        with tempfile.TemporaryDirectory(prefix="v05-hc-clock-") as td:
            root = Path(td)
            (root / "phone").mkdir(parents=True)

            # Seed real on-disk v05_living_town.json & v05_home_craft.json with no world_state season lock
            lt_engine.leave_drifting_book(
                root,
                book_title="岸边散记",
                slip_note_cn="翻着很静",
                spot_id="old_bookshop",
                author_side="partner",
                operation_key="op_clk_book",
            )
            hc_engine.propose_arrangement_change(
                root,
                object_id="warm_desk_lamp",
                target_zone="window_sill",
                reason_cn="窗边光柔",
                author_side="partner",
                operation_key="op_clk_prop",
            )
            cfg = {"day_root": str(root), "phone": {"enabled": False, "private_artifacts": True}}
            before = _snapshot_tree(root)

            # 1) Production live-clock path: render_lived_companion_context(..., now_iso=None)
            live_pulse = ssp.project_season_solar_pulse(now_iso=None)
            live_dt = ssp.parse_iso_to_default_tz(None)
            live_term = str(live_pulse["solar_term"]["term_name_cn"])
            live_season_label = season_labels[str(live_pulse["season"])]
            live_snap = lt_engine.read_living_town_snapshot(root, now_iso=live_dt.isoformat())
            live_home_spot = next(
                s for s in live_snap["sound_scent_map"] if s.get("spot_id") == "home"
            )
            live_sound_snippet = lcb._one_line(live_home_spot.get("sound_cn"), 16)

            live_ctx = lcb.render_lived_companion_context(
                root, cfg, "今晚外面什么味儿", ephemeral_hint="", now_iso=None
            )
            self.assertEqual(_snapshot_tree(root), before)
            self.assertLessEqual(len(live_ctx), lcb.MAX_LIVED_CONTEXT_CHARS)
            self.assertIn("时令:", live_ctx)
            self.assertIn(live_term, live_ctx)
            self.assertIn(live_season_label, live_ctx)
            self.assertIn("此刻街气:", live_ctx)
            self.assertIn(f"{live_term}时节", live_ctx)
            self.assertIn(live_sound_snippet, live_ctx)
            if live_term != "寒露":
                self.assertNotIn("寒露时节", live_ctx)

            # 2) Simulated spring, winter, and UTC-Z winter-night timestamps stay 100% unified
            sim_cases = (
                ("2026-03-20T10:00:00+08:00", "今日春分·春日", "春分时节", "寒露"),
                ("2026-12-22T21:30:00+08:00", "今日冬至·冬景", "冬至时节", "寒露"),
                ("2026-12-22T13:30:00Z", "今日冬至·冬景", "冬至时节", "午后"),
            )
            for ts, exp_season_bit, exp_scent_term, forbidden_bit in sim_cases:
                ctx_sim = lcb.render_lived_companion_context(
                    root, cfg, "今晚外面什么味儿", ephemeral_hint="", now_iso=ts
                )
                self.assertEqual(_snapshot_tree(root), before)
                self.assertLessEqual(len(ctx_sim), lcb.MAX_LIVED_CONTEXT_CHARS)
                self.assertIn(f"时令:{exp_season_bit}", ctx_sim, (ts, ctx_sim))
                self.assertIn("此刻街气:", ctx_sim, (ts, ctx_sim))
                self.assertIn(exp_scent_term, ctx_sim, (ts, ctx_sim))
                self.assertNotIn(forbidden_bit, ctx_sim, (ts, ctx_sim))
                self.assertNotIn("秋意", ctx_sim, (ts, ctx_sim))


if __name__ == "__main__":
    unittest.main()

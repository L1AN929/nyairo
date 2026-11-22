#!/usr/bin/env python3
"""Unit & contract tests for v0.8 lossless suitcase relocation and G4 migration_loss_manifest."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.life.scripts import v02_runtime_governance_migration as rm_engine  # noqa: E402
from components.phone import v02_runtime_migration_view as rm_view  # noqa: E402
from components.phone import v03_encounters_view  # noqa: E402
from components.phone import v08_composable_body_view  # noqa: E402
from components.world.scripts import v08_microzone_ecology as microzone_engine  # noqa: E402
from components.world.scripts import v08_universal_matter as matter_engine  # noqa: E402

NOW_T0 = "2026-11-18T10:00:00+08:00"
NOW_T1 = "2026-11-18T10:30:00+08:00"

FULL_9KEY_MATTER_PROFILE: dict[str, Any] = {
    "ambient_zone": "cold",
    "water_source": False,
    "capacity_slots": 6,
    "item_kind": "everyday",
    "paper_like": False,
    "shelf_life_hours": 36.5,
    "portions": 3,
    "material_units": 4,
    "assembly_role": "host_frame",
}


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        st = p.lstat()
        if p.is_symlink():
            snap[rel] = (0, int(st.st_mtime_ns), f"symlink:{os.readlink(p)}")
        elif p.is_file():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), digest)
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _contains_emoji(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        if (
            0x1F300 <= cp <= 0x1FAFF
            or 0x2600 <= cp <= 0x27BF
            or 0xFE00 <= cp <= 0xFE0F
        ):
            return True
    return False


class TestV08SuitcaseLosslessRelocation(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v08-suitcase-reloc-"))
        self.src_root = self.tmp / "src_day"
        self.dst_root = self.tmp / "dst_day"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_full_v08_instance(self, root: Path) -> None:
        # 1. Character pack (neutral resident) + host binding to strip
        _write_json(
            root / "phone" / "character_pack_state.json",
            {
                "pack_id": "pack:harbor-maker",
                "display_name": "海湾木工居民",
                "pronouns": "TA",
                "version": "1.0.0",
                "deploy_only": {"host_gpu": "local_cuda"},
                "api_key": "sk-NEVER-EXPORT-PACK-KEY-1234567890",
            },
        )

        # 2. Phone slices across records_only / records_and_objects / practice
        _write_json(
            root / "phone" / "window_keepsakes.json",
            {
                "schema": "phone.home.keepsakes.v1",
                "lockscreen": {"album_artifact_id": "album:harbor_sunset_01"},
                "pins": [{"share_id": "share:postcard_01", "pinned_at": NOW_T0}],
            },
        )
        _write_json(
            root / "phone" / "longterm_gifts_lantern.json",
            {
                "schema": "phone.longterm.gifts_lantern.v1",
                "shared_gifts": [
                    {
                        "gift_id": "gift_01",
                        "title": "手刻樟木书签",
                        "stage": "gifted",
                        "draft_seed_note": "起初只是边角木料试刻",
                        "finished_note": "打磨上蜡后送出",
                    }
                ],
                "later_reflections": [
                    {"reflection_id": "refl_01", "note": "半年后再看刀痕更顺手", "shared": True}
                ],
            },
        )
        _write_json(
            root / "phone" / "v04_elastic_play.json",
            {
                "schema": "nyairo.v04.elastic_play_state.v1",
                "revision": 2,
                "play_sessions": [
                    {"play_id": "play_01", "topic_cn": "纸折小船浮力试做", "outcome": "kept_note"}
                ],
                "craft_genealogy": [
                    {"genealogy_id": "gen_01", "technique_cn": "折边防水压痕"}
                ],
            },
        )
        # Seed canonical companion path used by v03_epistemic_encounters.py (phone/v03_encounters.json)
        _write_json(
            root / "phone" / "v03_encounters.json",
            {
                "schema_version": "life.v03.encounters.v1",
                "revision": 3,
                "encounters": [
                    {
                        "encounter_id": "enc_01",
                        "title": "旧港志第三章",
                        "stage": "NUANCED_RECOGNITION",
                        "seen_pages": [1, 2, 5],
                        "partial_disagreement_kept": "堤岸年代推断仍存疑",
                    }
                ],
                "shared_materials": [],
                "npc_errands_and_hearsay": [],
            },
        )
        _write_json(
            root / "phone" / "v08_emergent_echoes.json",
            {
                "schema_version": "phone.v08.emergent_echoes.v1",
                "revision": 4,
                "future_self_questions": [
                    {"question_id": "fsq_01", "question_cn": "入冬后院子薄荷还能留几枝", "status": "open"}
                ],
                "shared_idioms": [
                    {"idiom_id": "idiom_01", "phrase_cn": "留半盏灯", "status": "confirmed"}
                ],
                "personal_rituals": [
                    {"ritual_id": "rit_01", "title_cn": "雨天先擦干窗框再铺纸", "status": "active"}
                ],
            },
        )
        # Seed canonical companion path used by v08_composable_body.py (body/v08_composable_body_pack.json)
        v08_composable_body_view.mutate_v08_composable_body_view(
            root,
            {
                "action": "configure_body_pack",
                "pack_id": "menstrual_cycle",
                "enabled": True,
                "anchor_iso": "2026-11-15T08:00:00+08:00",
                "cycle_length_days": 28,
                "active_days": 5,
                "discomfort_profile": "mild_cramps",
                "operation_key": "cb-cfg-01",
            },
            actor_side="subject",
            now_iso=NOW_T0,
        )

        # 3. World slices: real v08_matter_space.json with nested carrier bag + subject_private_drawer
        matter_engine.compose_open_act(
            root,
            {
                "intent_cn": "整理外出帆布挎包与私密抽屉草稿",
                "steps": [
                    {
                        "op": "INTRODUCE_ITEM",
                        "object_id": "canvas_carrier_bag",
                        "label_cn": "帆布外出挎包",
                        "item_kind": "vessel",
                        "target_parent_id": "home_staging_corner",
                        "capacity_slots": 4,
                    },
                    {
                        "op": "INTRODUCE_ITEM",
                        "object_id": "thermos_in_bag",
                        "label_cn": "挎包里的保温小壶",
                        "item_kind": "everyday",
                        "target_parent_id": "canvas_carrier_bag",
                        "portions": 2,
                    },
                    {
                        "op": "INTRODUCE_ITEM",
                        "object_id": "private_sketch_card",
                        "label_cn": "未公开速写底卡",
                        "item_kind": "paper",
                        "target_parent_id": "subject_private_drawer",
                        "paper_like": True,
                    },
                ],
            },
            now_iso=NOW_T0,
        )
        _write_json(
            root / "world" / "v08_open_affordance.json",
            {
                "schema_version": "world.v08.open_affordance.v1",
                "revision": 2,
                "traces": [
                    {
                        "trace_id": "tr_coaster_01",
                        "object_id": "home_desk",
                        "note_cn": "杯垫边留着铅笔印",
                        "fade_mode": "until_touched",
                        "visibility": "shared",
                    }
                ],
                "routines": [
                    {
                        "routine_id": "rt_morning_tea",
                        "title_cn": "晨起开窗温杯",
                        "steps": ["open_window", "warm_cup"],
                    }
                ],
            },
        )
        _write_json(
            root / "world" / "v08_microzone_ecology.json",
            {
                "schema_version": 1,
                "revision": 3,
                "window_state": "ajar",
                "lamp_angle": "soft_side",
                "active_spot": "window_side",
                "updated_at": NOW_T0,
            },
        )
        _write_json(
            root / "world" / "v04_neighborhood_state.json",
            {
                "schema": "world.v04.neighborhood_living.v1",
                "revision": 2,
                "weather": {"kind": "drizzle", "airflow": "medium", "season": "autumn"},
                "plants": {"window_mint": {"hydration": 58.0}},
            },
        )

        # 4. Custom world-package/ with open-storefront sale_spec & 9-key matter_profile
        _write_json(
            root / "world-package" / "manifest.json",
            {
                "package_schema": "nyairo.world-package.v1",
                "world_id": "harbor_bakery_town_v1",
                "name": "海湾烘焙小镇",
            },
        )
        _write_json(
            root / "world-package" / "locations.json",
            {
                "locations": [
                    {"location_id": "home", "name": "海湾小屋"},
                    {"location_id": "harbor_bakery", "name": "坡道烘焙铺"},
                ]
            },
        )
        _write_json(
            root / "world-package" / "routes.json",
            {
                "routes": [
                    {"from": "home", "to": "harbor_bakery", "walk_seconds": 240}
                ]
            },
        )
        _write_json(
            root / "world-package" / "rules.json",
            {
                "location_rules": {
                    "harbor_bakery": {"open_hhmm": "08:30", "close_hhmm": "19:30"}
                }
            },
        )
        _write_json(
            root / "world-package" / "objects.json",
            {
                "schema_version": "worldgen.objects.v1",
                "objects": [
                    {
                        "object_id": "bakery_display_cooler",
                        "name": "烘焙铺冷柜架",
                        "kind": "cooler",
                        "location_id": "harbor_bakery",
                        "matter_profile": copy.deepcopy(FULL_9KEY_MATTER_PROFILE),
                    },
                    {
                        "object_id": "salted_butter_roll",
                        "name": "海盐黄油卷",
                        "kind": "food",
                        "location_id": "harbor_bakery",
                        "portable": True,
                        "sale_spec": {
                            "category": "food",
                            "price_tier": "budget",
                            "price": 280,
                        },
                        "matter_profile": {
                            "item_kind": "food",
                            "shelf_life_hours": 18,
                            "portions": 2,
                        },
                    },
                ],
            },
        )
        _write_json(
            root / "world-package" / "scene.json",
            {
                "rooms": [
                    {
                        "room_id": "harbor_bakery",
                        "placements": [
                            {"object_id": "bakery_display_cooler", "x": 2, "y": 1, "capacity": 6}
                        ],
                    }
                ]
            },
        )
        _write_json(
            root / "world-package" / "content.json",
            {
                "entries": [
                    {"entry_id": "board_01", "location_id": "harbor_bakery", "text": "今日新出炉海盐卷"}
                ]
            },
        )

        # 5. 36B adult_intimacy & solo private ledgers that MUST NEVER be exported
        _write_json(
            root / "phone" / "adult_intimacy.json",
            {
                "schema": "phone.adult.intimacy.v1",
                "adult_gate": {"adult_confirmed": True},
                "solo_and_memory": {"private_entries": ["SECRET_36B_NEVER_EXPORT"]},
            },
        )
        _write_json(
            root / "phone" / "solo_partition.json",
            {"partition": "SOLO", "entries": ["SECRET_SOLO_NEVER_EXPORT"]},
        )

    def test_01_owned_files_pure_lf_character_neutral_and_zero_emoji(self) -> None:
        owned = (
            ROOT / "components" / "life" / "scripts" / "v02_runtime_governance_migration.py",
            ROOT / "components" / "phone" / "v02_runtime_migration_view.py",
            ROOT / "tests" / "test_v08_suitcase_lossless_relocation.py",
        )
        forbidden_tokens = ("\u5343\u4ee3", "\u5979")
        for fpath in owned:
            raw = fpath.read_bytes()
            self.assertNotIn(b"\r\n", raw, f"CRLF forbidden in {fpath}")
            self.assertNotIn(b"\r", raw, f"CR forbidden in {fpath}")
            text = raw.decode("utf-8")
            for tok in forbidden_tokens:
                self.assertNotIn(tok, text, f"Forbidden hardcoded persona token {tok!r} in {fpath}")
            self.assertFalse(_contains_emoji(text), f"Emoji forbidden in {fpath}")

        self.assertIn("window_keepsakes.json", rm_engine.SUITCASE_RECORDS_ONLY_SLICES)
        self.assertIn("longterm_gifts_lantern.json", rm_engine.SUITCASE_RECORDS_ONLY_SLICES)
        self.assertIn("v04_elastic_play.json", rm_engine.SUITCASE_RECORDS_AND_OBJECTS_EXTRA_SLICES)
        self.assertIn("v03_epistemic_encounters.json", rm_engine.SUITCASE_RECORDS_AND_OBJECTS_EXTRA_SLICES)
        self.assertIn("v08_emergent_echoes.json", rm_engine.SUITCASE_PRACTICE_EXTRA_SLICES)
        self.assertIn("v08_composable_body.json", rm_engine.SUITCASE_PRACTICE_EXTRA_SLICES)
        self.assertEqual(
            rm_engine.SUITCASE_ALLOWED_WORLD_FILES,
            (
                "v08_matter_space.json",
                "v08_open_affordance.json",
                "v08_microzone_ecology.json",
                "v04_neighborhood_state.json",
            ),
        )
        self.assertEqual(
            rm_engine.SUITCASE_ALLOWED_WORLD_PACKAGE_FILES,
            (
                "manifest.json",
                "locations.json",
                "routes.json",
                "rules.json",
                "objects.json",
                "scene.json",
                "content.json",
            ),
        )

    def test_02_get_runtime_migration_view_100pct_zero_write_with_v08_assets(self) -> None:
        # Cold unmaterialized root
        snap_cold_before = _snapshot_tree(self.tmp)
        c_cold, env_cold = rm_view.read_v02_runtime_migration_view(
            self.src_root, has_subject_capability=False, now_iso=NOW_T0
        )
        self.assertEqual(c_cold, 200)
        self.assertEqual(env_cold["llm_calls"], 0)
        self.assertFalse(self.src_root.exists())
        self.assertEqual(_snapshot_tree(self.tmp), snap_cold_before)
        cold_manifest = env_cold["migration_suitcase"]["migration_loss_manifest"]
        self.assertTrue(cold_manifest["tombstone_anti_resurrection_enforced"])

        # Richly populated v0.8 root
        self._seed_full_v08_instance(self.src_root)
        snap_before = _snapshot_tree(self.src_root)
        c_p, env_p = rm_view.read_v02_runtime_migration_view(
            self.src_root, has_subject_capability=False, now_iso=NOW_T0
        )
        c_s, env_s = rm_view.read_v02_runtime_migration_view(
            self.src_root, has_subject_capability=True, now_iso=NOW_T0
        )
        snap_after = _snapshot_tree(self.src_root)
        self.assertEqual(c_p, 200)
        self.assertEqual(c_s, 200)
        self.assertEqual(env_p["llm_calls"], 0)
        self.assertEqual(env_s["llm_calls"], 0)
        self.assertEqual(snap_before, snap_after, "GET read_v02_runtime_migration_view must be 100% zero-write")

        # Private drawer item must never leak into partner GET preview
        partner_dump = json.dumps(env_p, ensure_ascii=False)
        self.assertNotIn("未公开速写底卡", partner_dump)
        self.assertNotIn("SECRET_36B_NEVER_EXPORT", partner_dump)
        self.assertNotIn("SECRET_SOLO_NEVER_EXPORT", partner_dump)

        manifest_preview = env_p["migration_suitcase"]["migration_loss_manifest"]
        self.assertEqual(
            manifest_preview["carried_domains"],
            [
                "phone_slices",
                "character_pack",
                "world_matter_and_traces",
                "custom_world_package",
                "tombstone_watermarks",
            ],
        )

    def test_03_full_v08_lossless_roundtrip_export_verify_and_import_to_fresh_machine(self) -> None:
        self._seed_full_v08_instance(self.src_root)

        exp_code, exp_env = rm_view.mutate_v02_runtime_migration_view(
            self.src_root,
            {
                "action": "export_suitcase",
                "profile_mode": "full_practice",
                "keep_practice": True,
                "operation_key": "exp-v08-full-01",
            },
            actor_side="subject",
            now_iso=NOW_T0,
        )
        self.assertEqual(exp_code, 200, exp_env)
        self.assertEqual(exp_env["llm_calls"], 0)
        bundle = exp_env["data"]["suitcase_bundle"]
        summary = exp_env["data"]["summary"]

        # Verify checksum & 36B / secret redlines on exported bundle
        self.assertEqual(
            bundle["bundle_sha256"],
            rm_engine.compute_suitcase_bundle_sha256(bundle),
        )
        bundle_blob = json.dumps(bundle, ensure_ascii=False)
        self.assertNotIn("SECRET_36B_NEVER_EXPORT", bundle_blob)
        self.assertNotIn("SECRET_SOLO_NEVER_EXPORT", bundle_blob)
        self.assertNotIn("sk-NEVER-EXPORT-PACK-KEY", bundle_blob)
        self.assertNotIn("adult_intimacy.json", bundle["slices"])
        self.assertNotIn("solo_partition.json", bundle["slices"])

        # G4 manifest present on both bundle and summary
        for mf in (bundle["migration_loss_manifest"], summary["migration_loss_manifest"]):
            self.assertEqual(
                mf["carried_domains"],
                [
                    "phone_slices",
                    "character_pack",
                    "world_matter_and_traces",
                    "custom_world_package",
                    "tombstone_watermarks",
                ],
            )
            left_domains = {row["domain"] for row in mf["intentionally_left_behind"]}
            self.assertIn("host_credentials_and_api_keys", left_domains)
            self.assertIn("active_36b_permits_and_solo_partition", left_domains)
            self.assertIn("transient_sockets_and_locks", left_domains)
            self.assertTrue(mf["tombstone_anti_resurrection_enforced"])

        # Import onto a brand-new machine dst_root with changed model & headless carrier
        imp_code, imp_env = rm_view.mutate_v02_runtime_migration_view(
            self.dst_root,
            {
                "action": "import_suitcase",
                "bundle": bundle,
                "target_model_id": "nyairo-v08-target-model",
                "target_carrier_mode": "headless",
                "operation_key": "imp-v08-full-01",
            },
            actor_side="subject",
            now_iso=NOW_T1,
        )
        self.assertEqual(imp_code, 200, imp_env)
        self.assertEqual(imp_env["llm_calls"], 0)
        receipt = imp_env["data"]["import_receipt"]

        # Verify restored_world_slices, restored_world_package_files & G4 manifest on receipt
        self.assertEqual(
            receipt["restored_world_slices"],
            [
                "v08_matter_space.json",
                "v08_open_affordance.json",
                "v08_microzone_ecology.json",
                "v04_neighborhood_state.json",
            ],
        )
        self.assertEqual(
            receipt["restored_world_package_files"],
            [
                "manifest.json",
                "locations.json",
                "routes.json",
                "rules.json",
                "objects.json",
                "scene.json",
                "content.json",
            ],
        )
        imp_mf = receipt["migration_loss_manifest"]
        self.assertTrue(imp_mf["tombstone_anti_resurrection_enforced"])
        recheck_ids = {item["skill_id"] for item in imp_mf["capability_recheck_required"]}
        self.assertIn("nuanced_draft_stitching", recheck_ids)
        self.assertIn("window_sketch_tone_tuning", recheck_ids)
        self.assertIn("interactive_layout_live_preview", recheck_ids)

        # Verify custom world-package/ restored with exact sale_spec & 9-key matter_profile
        restored_objects_doc = json.loads(
            (self.dst_root / "world-package" / "objects.json").read_text(encoding="utf-8")
        )
        objs_by_id = {o["object_id"]: o for o in restored_objects_doc["objects"]}
        self.assertEqual(
            objs_by_id["bakery_display_cooler"]["matter_profile"],
            FULL_9KEY_MATTER_PROFILE,
        )
        self.assertEqual(
            objs_by_id["salted_butter_roll"]["sale_spec"],
            {"category": "food", "price_tier": "budget", "price": 280},
        )

        # Verify nested carrier bag + subject_private_drawer restored in world/v08_matter_space.json
        self.assertTrue((self.dst_root / "world" / "v08_matter_space.json").is_file())
        sub_matter = matter_engine.read_matter_space_view(
            self.dst_root, actor_side="subject", now_iso=NOW_T1
        )
        sub_objs = {o["object_id"]: o for o in sub_matter["objects"]}
        self.assertIn("canvas_carrier_bag", sub_objs)
        self.assertEqual(sub_objs["canvas_carrier_bag"]["parent_id"], "home_staging_corner")
        self.assertIn("thermos_in_bag", sub_objs)
        self.assertEqual(sub_objs["thermos_in_bag"]["parent_id"], "canvas_carrier_bag")
        self.assertIn("private_sketch_card", sub_objs)
        self.assertEqual(sub_objs["private_sketch_card"]["parent_id"], "subject_private_drawer")

        # Partner view on dst_root still hides subject_private_drawer contents
        partner_matter = matter_engine.read_matter_space_view(
            self.dst_root, actor_side="partner", now_iso=NOW_T1
        )
        partner_obj_ids = {o["object_id"] for o in partner_matter["objects"]}
        self.assertIn("thermos_in_bag", partner_obj_ids)
        self.assertNotIn("private_sketch_card", partner_obj_ids)

        # Verify world/v08_open_affordance.json & world/v08_microzone_ecology.json (+ microzone engine read)
        self.assertTrue((self.dst_root / "world" / "v08_open_affordance.json").is_file())
        self.assertTrue((self.dst_root / "world" / "v08_microzone_ecology.json").is_file())
        mz_proj = microzone_engine.read_microzone_ecology_view(
            self.dst_root, actor_side="subject", now_iso=NOW_T1
        )
        self.assertEqual(mz_proj["window_state"], "ajar")
        self.assertEqual(mz_proj["lamp_angle"], "soft_side")

        # Verify all post-v0.7 phone slices + canonical companion engine reads on dst_root
        for expected_phone_file in (
            "window_keepsakes.json",
            "longterm_gifts_lantern.json",
            "v04_elastic_play.json",
            "v03_epistemic_encounters.json",
            "v08_emergent_echoes.json",
            "v08_composable_body.json",
        ):
            self.assertTrue(
                (self.dst_root / "phone" / expected_phone_file).is_file(),
                f"Missing restored phone slice: {expected_phone_file}",
            )

        # Composable body engine reads canonical body/v08_composable_body_pack.json on dst_root
        cb_code, cb_view = v08_composable_body_view.read_v08_composable_body_view(
            self.dst_root, has_subject_capability=True, now_iso=NOW_T1
        )
        self.assertEqual(cb_code, 200)
        self.assertTrue(cb_view["data"]["packs"]["menstrual_cycle"]["enabled"])

        # Epistemic encounters engine reads canonical phone/v03_encounters.json on dst_root
        enc_code, enc_view = v03_encounters_view.read_v03_encounters_view(
            self.dst_root, has_subject_capability=True, now_iso=NOW_T1
        )
        self.assertEqual(enc_code, 200)
        self.assertEqual(len(enc_view["data"]["encounters"]), 1)
        self.assertEqual(enc_view["data"]["encounters"][0]["encounter_id"], "enc_01")

        # Adult intimacy & solo partition must NOT exist on dst_root
        self.assertFalse((self.dst_root / "phone" / "adult_intimacy.json").exists())
        self.assertFalse((self.dst_root / "phone" / "solo_partition.json").exists())

    def test_04_tombstone_anti_resurrection_across_phone_world_slices_and_world_package(self) -> None:
        self._seed_full_v08_instance(self.src_root)

        # Seed tombstoned IDs in source recall projection AND matching items in phone + world + world-package
        _write_json(
            self.src_root / "phone" / "v02_recall_projection.json",
            {
                "schema": "nyairo.v02.recall_projection.v1",
                "memories": {
                    "mem_alive_01": {"summary": "保留的海岸散步记忆"},
                    "dead_echo_q_01": {"summary": "必须被清除的旧记忆"},
                },
                "tombstones": [
                    {"memory_id": "dead_echo_q_01", "reason": "user_deleted", "tombstoned_at": NOW_T0},
                    {"memory_id": "dead_world_obj_01", "reason": "user_deleted", "tombstoned_at": NOW_T0},
                    {"memory_id": "dead_trace_01", "reason": "user_deleted", "tombstoned_at": NOW_T0},
                    {"memory_id": "dead_pkg_obj_01", "reason": "user_deleted", "tombstoned_at": NOW_T0},
                ],
            },
        )
        # Inject dead IDs into source phone slice, world matter, world affordance & world-package
        echoes_path = self.src_root / "phone" / "v08_emergent_echoes.json"
        echoes_doc = json.loads(echoes_path.read_text(encoding="utf-8"))
        echoes_doc["future_self_questions"].append(
            {"question_id": "dead_echo_q_01", "question_cn": "不应导出的已删问题", "status": "open"}
        )
        _write_json(echoes_path, echoes_doc)

        ms_path = self.src_root / "world" / "v08_matter_space.json"
        ms_doc = json.loads(ms_path.read_text(encoding="utf-8"))
        ms_doc["matter_objects"]["dead_world_obj_01"] = {
            "object_id": "dead_world_obj_01",
            "label_cn": "不应复活的旧物件",
            "parent_id": "home_desk",
        }
        _write_json(ms_path, ms_doc)

        oa_path = self.src_root / "world" / "v08_open_affordance.json"
        oa_doc = json.loads(oa_path.read_text(encoding="utf-8"))
        oa_doc["traces"].append(
            {"trace_id": "dead_trace_01", "object_id": "home_desk", "note_cn": "不应复活的痕迹"}
        )
        _write_json(oa_path, oa_doc)

        wp_obj_path = self.src_root / "world-package" / "objects.json"
        wp_obj_doc = json.loads(wp_obj_path.read_text(encoding="utf-8"))
        wp_obj_doc["objects"].append(
            {"object_id": "dead_pkg_obj_01", "name": "已撤回物件", "kind": "prop", "location_id": "home"}
        )
        _write_json(wp_obj_path, wp_obj_doc)

        exp_code, exp_env = rm_view.mutate_v02_runtime_migration_view(
            self.src_root,
            {"action": "export_suitcase", "profile_mode": "full_practice"},
            actor_side="subject",
            now_iso=NOW_T0,
        )
        self.assertEqual(exp_code, 200)
        bundle = exp_env["data"]["suitcase_bundle"]

        # Export-side scrubbing: dead IDs must not appear outside tombstone containers
        self.assertNotIn("dead_echo_q_01", json.dumps(bundle["slices"]["v08_emergent_echoes.json"]))
        self.assertNotIn("dead_world_obj_01", json.dumps(bundle["world_slices"]["v08_matter_space.json"]))
        self.assertNotIn("dead_trace_01", json.dumps(bundle["world_slices"]["v08_open_affordance.json"]))
        self.assertNotIn("dead_pkg_obj_01", json.dumps(bundle["world_package"]["objects.json"]))

        # Also pre-seed dst_root disk with resurrected copies in world/ and world-package/ and import a
        # records_only bundle (which does NOT overwrite world/ or world-package/) to prove in-place scrubbing!
        _write_json(
            self.dst_root / "world" / "v08_matter_space.json",
            {
                "schema_version": "world.v08.universal_matter.v1",
                "matter_objects": {
                    "dead_world_obj_01": {"object_id": "dead_world_obj_01", "label_cn": "妄图原地复活"},
                    "alive_cup": {"object_id": "alive_cup", "label_cn": "保留茶杯"},
                },
            },
        )
        _write_json(
            self.dst_root / "world-package" / "objects.json",
            {
                "objects": [
                    {"object_id": "dead_pkg_obj_01", "name": "妄图世界包复活"},
                    {"object_id": "alive_pkg_lamp", "name": "保留台灯"},
                ]
            },
        )
        _, rec_only_exp = rm_view.mutate_v02_runtime_migration_view(
            self.src_root,
            {"action": "export_suitcase", "profile_mode": "records_only"},
            actor_side="subject",
            now_iso=NOW_T0,
        )
        rec_bundle = rec_only_exp["data"]["suitcase_bundle"]
        self.assertNotIn("world_slices", rec_bundle)
        self.assertNotIn("world_package", rec_bundle)

        imp_code, _ = rm_view.mutate_v02_runtime_migration_view(
            self.dst_root,
            {"action": "import_suitcase", "bundle": rec_bundle},
            actor_side="subject",
            now_iso=NOW_T1,
        )
        self.assertEqual(imp_code, 200)

        dst_ms = json.loads((self.dst_root / "world" / "v08_matter_space.json").read_text(encoding="utf-8"))
        self.assertNotIn("dead_world_obj_01", dst_ms["matter_objects"])
        self.assertIn("alive_cup", dst_ms["matter_objects"])

        dst_wp_objs = json.loads((self.dst_root / "world-package" / "objects.json").read_text(encoding="utf-8"))
        dst_wp_ids = [o["object_id"] for o in dst_wp_objs["objects"]]
        self.assertNotIn("dead_pkg_obj_01", dst_wp_ids)
        self.assertIn("alive_pkg_lamp", dst_wp_ids)

    def test_05_records_only_leaves_world_and_practice_behind_honestly_in_manifest(self) -> None:
        self._seed_full_v08_instance(self.src_root)

        exp_code, exp_env = rm_view.mutate_v02_runtime_migration_view(
            self.src_root,
            {"action": "export_suitcase", "profile_mode": "records_only"},
            actor_side="partner",
            now_iso=NOW_T0,
        )
        self.assertEqual(exp_code, 200)
        bundle = exp_env["data"]["suitcase_bundle"]
        mf = bundle["migration_loss_manifest"]

        # records_only carries window_keepsakes & longterm_gifts_lantern, omits world & practice
        self.assertIn("window_keepsakes.json", bundle["slices"])
        self.assertIn("longterm_gifts_lantern.json", bundle["slices"])
        self.assertNotIn("v04_elastic_play.json", bundle["slices"])
        self.assertNotIn("v03_epistemic_encounters.json", bundle["slices"])
        self.assertNotIn("v08_emergent_echoes.json", bundle["slices"])
        self.assertNotIn("v08_composable_body.json", bundle["slices"])
        self.assertNotIn("world_slices", bundle)
        self.assertNotIn("world_package", bundle)

        self.assertNotIn("world_matter_and_traces", mf["carried_domains"])
        self.assertNotIn("custom_world_package", mf["carried_domains"])
        left_domains = {row["domain"] for row in mf["intentionally_left_behind"]}
        self.assertIn("world_objects", left_domains)
        self.assertIn("practice_habits", left_domains)
        self.assertIn("active_36b_permits_and_solo_partition", left_domains)
        self.assertIn("host_credentials_and_api_keys", left_domains)

    def test_06_tamper_traversal_unknown_filename_secret_and_symlink_guards(self) -> None:
        self._seed_full_v08_instance(self.src_root)
        # Also seed embedded secret & host path inside world_slices & world-package to test export stripping
        _write_json(
            self.src_root / "world" / "v08_open_affordance.json",
            {
                "schema_version": "world.v08.open_affordance.v1",
                "traces": [
                    {
                        "trace_id": "tr_clean",
                        "note_cn": "调试路径 /root/secret_box 与令牌 sk-proj-abcdef1234567890ABCD",
                        "api_key": "must-be-stripped-key",
                    }
                ],
            },
        )
        _, exp_env = rm_view.mutate_v02_runtime_migration_view(
            self.src_root,
            {"action": "export_suitcase", "profile_mode": "full_practice"},
            actor_side="subject",
            now_iso=NOW_T0,
        )
        bundle = exp_env["data"]["suitcase_bundle"]
        raw_str = json.dumps(bundle, ensure_ascii=False)
        self.assertNotIn("must-be-stripped-key", raw_str)
        self.assertNotIn("/root/secret_box", raw_str)
        self.assertNotIn("sk-proj-abcdef1234567890ABCD", raw_str)

        # 1) Tampered world_slices without recomputing sha -> SUITCASE_CHECKSUM_MISMATCH
        tampered_ws = copy.deepcopy(bundle)
        tampered_ws["world_slices"]["v08_microzone_ecology.json"]["window_state"] = "open"
        c1, e1 = rm_view.mutate_v02_runtime_migration_view(
            self.dst_root, {"action": "import_suitcase", "bundle": tampered_ws}, actor_side="partner"
        )
        self.assertEqual(c1, 400)
        self.assertEqual(e1["reason_code"], "SUITCASE_CHECKSUM_MISMATCH")

        # 2) Tampered world_package without recomputing sha -> SUITCASE_CHECKSUM_MISMATCH
        tampered_wp = copy.deepcopy(bundle)
        tampered_wp["world_package"]["objects.json"]["objects"][1]["sale_spec"]["price"] = 1
        c2, e2 = rm_view.mutate_v02_runtime_migration_view(
            self.dst_root, {"action": "import_suitcase", "bundle": tampered_wp}, actor_side="partner"
        )
        self.assertEqual(c2, 400)
        self.assertEqual(e2["reason_code"], "SUITCASE_CHECKSUM_MISMATCH")

        # 3) Path traversal / unknown filename in slices, world_slices, world_package -> FORBIDDEN_SLICE_FILENAME
        for field_key, bad_name in (
            ("slices", "../escape.json"),
            ("slices", "adult_intimacy.json"),
            ("slices", "solo_partition.json"),
            ("world_slices", "../v08_matter_space.json"),
            ("world_slices", "unknown_world_dump.json"),
            ("world_package", "../../etc/passwd"),
            ("world_package", "arbitrary_script.json"),
        ):
            forged_fn = copy.deepcopy(bundle)
            forged_fn[field_key] = {bad_name: {"ok": True}}
            forged_fn["bundle_sha256"] = rm_engine.compute_suitcase_bundle_sha256(forged_fn)
            cf, ef = rm_view.mutate_v02_runtime_migration_view(
                self.dst_root, {"action": "import_suitcase", "bundle": forged_fn}, actor_side="partner"
            )
            self.assertEqual(cf, 400, f"Expected 400 for {field_key}:{bad_name}")
            self.assertEqual(ef["reason_code"], "FORBIDDEN_SLICE_FILENAME")

        # 4) Forged secret key in world_slices / world_package -> SUITCASE_SECRET_KEY_FORBIDDEN
        for field_key, sub_file in (
            ("world_slices", "v08_matter_space.json"),
            ("world_package", "manifest.json"),
        ):
            forged_sec = copy.deepcopy(bundle)
            forged_sec[field_key][sub_file]["client_secret"] = "leaked-secret"
            forged_sec["bundle_sha256"] = rm_engine.compute_suitcase_bundle_sha256(forged_sec)
            cs, es = rm_view.mutate_v02_runtime_migration_view(
                self.dst_root, {"action": "import_suitcase", "bundle": forged_sec}, actor_side="partner"
            )
            self.assertEqual(cs, 400)
            self.assertEqual(es["reason_code"], "SUITCASE_SECRET_KEY_FORBIDDEN")

        # 5) Symlink target on dst_root -> SYMLINK_TARGET_FORBIDDEN
        if hasattr(os, "symlink"):
            sym_dst = self.tmp / "sym_dst"
            outside = self.tmp / "outside_dir"
            outside.mkdir(parents=True, exist_ok=True)
            sym_dst.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(outside, sym_dst / "world-package")
                has_symlink = True
            except OSError:
                has_symlink = False
            if has_symlink:
                csym, esym = rm_view.mutate_v02_runtime_migration_view(
                    sym_dst, {"action": "import_suitcase", "bundle": bundle}, actor_side="partner"
                )
                self.assertEqual(csym, 400)
                self.assertEqual(esym["reason_code"], "SYMLINK_TARGET_FORBIDDEN")

    def test_07_legacy_v07_bundle_compat_world_home_fallback_and_2mb_cap(self) -> None:
        # 1) Legacy v0.7 bundle omitting world_slices, world_package, migration_loss_manifest
        legacy_bundle: dict[str, Any] = {
            "schema": "nyairo.suitcase.bundle.v1",
            "bundle_version": "0.7.0",
            "exported_at": NOW_T0,
            "profile_mode": "records_only",
            "effective_profile_mode": "records_only",
            "keep_practice": True,
            "character_pack": None,
            "slices": {
                "desk_tomorrow_note.json": {
                    "schema": "nyairo.desk_tomorrow_note.v1",
                    "partner_note": "旧版旅行箱兼容验证",
                }
            },
            "tombstone_ledger": [],
        }
        legacy_bundle["bundle_sha256"] = rm_engine.compute_suitcase_bundle_sha256(legacy_bundle)

        c_leg, env_leg = rm_view.mutate_v02_runtime_migration_view(
            self.dst_root,
            {"action": "import_suitcase", "bundle": legacy_bundle},
            actor_side="partner",
            now_iso=NOW_T1,
        )
        self.assertEqual(c_leg, 200, env_leg)
        rec_leg = env_leg["data"]["import_receipt"]
        self.assertEqual(rec_leg["restored_world_slices"], [])
        self.assertEqual(rec_leg["restored_world_package_files"], [])
        self.assertTrue(rec_leg["migration_loss_manifest"]["tombstone_anti_resurrection_enforced"])

        # 2) world-home/world/ fallback discovery + >2 MB world-package cap on export
        wh_root = self.tmp / "wh_src"
        _write_json(
            wh_root / "world-home" / "world" / "v08_microzone_ecology.json",
            {"schema_version": 1, "window_state": "open", "lamp_angle": "direct"},
        )
        _write_json(
            wh_root / "world-package" / "content.json",
            {"entries": [{"entry_id": "huge", "text": "x" * (2 * 1024 * 1024 + 1024)}]},
        )
        c_wh, env_wh = rm_view.mutate_v02_runtime_migration_view(
            wh_root,
            {"action": "export_suitcase", "profile_mode": "records_and_objects"},
            actor_side="partner",
            now_iso=NOW_T0,
        )
        self.assertEqual(c_wh, 200)
        wh_bundle = env_wh["data"]["suitcase_bundle"]
        self.assertIn("v08_microzone_ecology.json", wh_bundle.get("world_slices", {}))
        self.assertNotIn("world_package", wh_bundle)
        wh_left = {r["domain"] for r in wh_bundle["migration_loss_manifest"]["intentionally_left_behind"]}
        self.assertIn("oversized_custom_world_package", wh_left)

    def test_08_selective_practice_filter_and_idempotent_replay(self) -> None:
        self._seed_full_v08_instance(self.src_root)

        exp_cmd = {
            "action": "export_suitcase",
            "profile_mode": "selective_practice",
            "keep_skill_ids": ["phone_slice:v08_emergent_echoes.json"],
            "operation_key": "op-sel-exp-08",
        }
        c1, r1 = rm_view.mutate_v02_runtime_migration_view(
            self.src_root, exp_cmd, actor_side="subject", now_iso=NOW_T0
        )
        c2, r2 = rm_view.mutate_v02_runtime_migration_view(
            self.src_root, exp_cmd, actor_side="subject", now_iso=NOW_T1
        )
        self.assertEqual(c1, 200)
        self.assertEqual(c2, 200)
        self.assertEqual(r1["command_status"], "APPLIED")
        self.assertEqual(r2["command_status"], "DUPLICATE")
        self.assertEqual(r1["revision"], r2["revision"])
        b1 = r1["data"]["suitcase_bundle"]
        self.assertIn("v08_emergent_echoes.json", b1["slices"])
        self.assertNotIn("v08_composable_body.json", b1["slices"])
        self.assertIn("v04_elastic_play.json", b1["slices"])
        self.assertIn("v08_matter_space.json", b1["world_slices"])

        imp_cmd = {
            "action": "import_suitcase",
            "bundle": b1,
            "target_model_id": "nyairo-model-next",
            "operation_key": "op-sel-imp-08",
        }
        ci1, ri1 = rm_view.mutate_v02_runtime_migration_view(
            self.dst_root, imp_cmd, actor_side="subject", now_iso=NOW_T1
        )
        ci2, ri2 = rm_view.mutate_v02_runtime_migration_view(
            self.dst_root, imp_cmd, actor_side="subject", now_iso=NOW_T1
        )
        self.assertEqual(ci1, 200)
        self.assertEqual(ci2, 200)
        self.assertEqual(ri1["command_status"], "APPLIED")
        self.assertEqual(ri2["command_status"], "DUPLICATE")
        self.assertEqual(ri1["data"]["import_receipt"], ri2["data"]["import_receipt"])
        self.assertEqual(
            ri1["data"]["migration_loss_manifest"],
            ri2["data"]["migration_loss_manifest"],
        )

    def test_09_real_emergent_echoes_and_living_traces_view_roundtrip(self) -> None:
        from components.phone import v08_emergent_echoes_view as echoes_view
        from components.phone import v08_living_traces_view as traces_view

        c_eq, _ = echoes_view.mutate_v08_emergent_echoes_view(
            self.src_root,
            {
                "action": "plant_future_question",
                "question_cn": "明年春天木工台要不要换一块硬枫台面",
                "anchor_kind": "object",
                "anchor_ref": "home_desk",
                "keywords": ["木工台", "春天"],
                "visibility": "shared",
                "operation_key": "eq-plant-01",
            },
            actor_side="subject",
            now_iso=NOW_T0,
        )
        self.assertEqual(c_eq, 200)

        c_tr, _ = traces_view.mutate_v08_living_traces_view(
            self.src_root,
            {
                "action": "leave_partner_trace",
                "object_id": "home_desk",
                "gesture_cn": "在桌角轻轻放好",
                "state_sticker_cn": "桌角压了一片风干迷迭香",
                "fade_mode": "until_touched",
                "operation_key": "tr-leave-01",
            },
            actor_side="partner",
            now_iso=NOW_T0,
        )
        self.assertEqual(c_tr, 200)

        _, exp_env = rm_view.mutate_v02_runtime_migration_view(
            self.src_root,
            {"action": "export_suitcase", "profile_mode": "full_practice"},
            actor_side="subject",
            now_iso=NOW_T0,
        )
        bundle = exp_env["data"]["suitcase_bundle"]

        c_imp, _ = rm_view.mutate_v02_runtime_migration_view(
            self.dst_root,
            {"action": "import_suitcase", "bundle": bundle},
            actor_side="subject",
            now_iso=NOW_T1,
        )
        self.assertEqual(c_imp, 200)

        c_eq_dst, eq_dst = echoes_view.read_v08_emergent_echoes_view(
            self.dst_root, has_subject_capability=True, now_iso=NOW_T1
        )
        self.assertEqual(c_eq_dst, 200)
        q_texts = [q["question_cn"] for q in eq_dst["data"]["future_self_questions"]]
        self.assertIn("明年春天木工台要不要换一块硬枫台面", q_texts)

        c_tr_dst, tr_dst = traces_view.read_v08_living_traces_view(
            self.dst_root, has_subject_capability=False, now_iso=NOW_T1
        )
        self.assertEqual(c_tr_dst, 200)
        self.assertIn("桌角压了一片风干迷迭香", json.dumps(tr_dst["data"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()

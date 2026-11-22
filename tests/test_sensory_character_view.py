#!/usr/bin/env python3
"""开源角色包与可组合身体 / 多通道感官场与文字声景卡：单元测试 + 真实 loopback HTTP 测试。"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(Path(__file__).resolve().parent),
    str(ROOT / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import capability as phone_capability  # noqa: E402
from components.phone import sensory_character_view as scv  # noqa: E402
from components.phone import somatic_comfort_view as somatic_view  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "sensory-char-cap-token-20261111"

FROZEN_ANCHORS = {
    "components/life/scripts/autonomous_life_assembly.py":
        "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
}

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "真实 Supply/World/Body owner 需要 POSIX（在 WSL/Linux 上运行）",
)


def _sample_world(
    *,
    place_id: str = "home",
    place_label: str = "家",
    area_id: str = "bedroom",
    home: bool = True,
    local_time: str = "2026-11-11T13:00:00+09:00",
    local_date: str = "2026-11-11",
    light_power: str | None = "off",
    light_band: str | None = None,
    declared_source: str | None = "catalog:bedside_lamp",
    fixture_ids: list[str] | None = None,
) -> dict:
    return {
        "ok": True,
        "status": "applied",
        "data": {
            "location": {
                "status": "available",
                "place_id": place_id,
                "label": place_label,
                "area_id": area_id,
                "home": home,
            },
            "world": {
                "local_time": local_time,
                "local_date": local_date,
                "revision": 3,
            },
            "destinations": {
                "places": [
                    {
                        "place_id": "nearby_street",
                        "label": "家门外街道",
                        "here": False,
                        "reach": "reachable",
                        "route": ["home", "nearby_street"],
                    },
                    {
                        "place_id": "school",
                        "label": "学校",
                        "here": False,
                        "reach": "reachable",
                        "route": ["home", "nearby_street", "school"],
                    },
                ]
            },
            "local_environment": {
                "status": "available",
                "light": {
                    "status": "available" if light_power is not None else "unknown",
                    "power": light_power,
                    "band": light_band,
                    "declared_source": declared_source,
                    "fixture_ids": list(fixture_ids or []),
                },
            },
            "travel": {"status": "idle"},
        },
    }


def _sample_body(*, signals: list[dict] | None = None, pose: str = "seated") -> dict:
    return {
        "ok": True,
        "status": "available",
        "data": {
            "pose": pose,
            "orientation": "north",
            "recovery": {"state": "idle", "mode": None},
            "signals": list(signals or []),
            "needs": {"hunger": "none", "thirst": "mild", "toilet": "none"},
            "rest_evidence": None,
            "signals_status": "available",
        },
    }


def _valid_custom_pack(**overrides: object) -> dict:
    pack: dict = {
        "kind": "proposed.character_pack.v1",
        "pack_id": "aki_reader",
        "version": 3,
        "display_name": "秋",
        "fictional": True,
        "pronouns": "她/TA",
        "adult": {"life_stage": "working_adult", "declared_age": 24},
        "relation_to_user": "partner",
        "address": {"subject_calls_user": ["哥哥"], "user_calls_subject": ["小秋"]},
        "persona": {"core": "安静整理旧唱片与窗边读书笔记的独立创作者。"},
        "body": {"preset": "neutral"},
        "sensory_preferences": {
            "noise_sensitivity": "high",
            "light_preference": "soft_warm",
            "scent_note_enabled": True,
        },
        "starter_accessories": [
            {"kind": "headphones", "label": "降噪耳机", "mode": "ambient_filter"},
        ],
    }
    pack.update(overrides)
    return pack


class SensoryCharacterUnitTest(unittest.TestCase):
    """Pure unit tests for Domain A (character pack & body) and Domain B (sensory field)."""

    def test_01_frozen_anchor_sha256_untouched(self) -> None:
        for relative, expected in FROZEN_ANCHORS.items():
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(digest, expected, relative)

    def test_02_neutral_default_and_zero_write_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_state, pack_reason = scv.read_character_pack_state(root)
            self.assertIsNone(pack_reason)
            projected = scv.project_character_pack(pack_state, subject_view=False)
            self.assertEqual(projected["schema"], "phone.character.pack.v1")
            self.assertEqual(projected["source"], "default_neutral_preset")
            self.assertEqual(projected["revision"], 0)
            self.assertEqual(projected["active_pack"]["pack_id"], "neutral_resident")
            self.assertEqual(projected["active_pack"]["display_name"], "TA")
            self.assertEqual(projected["active_pack"]["pronouns"], "TA")
            self.assertFalse(projected["boundaries"]["locked_to_chiyo"])
            self.assertFalse(scv.character_pack_state_path(root).exists())

            cards_state, cards_reason = scv.read_sensory_cards_state(root)
            self.assertIsNone(cards_reason)
            self.assertFalse(scv.sensory_cards_state_path(root).exists())
            self.assertEqual(cards_state["revision"], 0)

    def test_03_four_builtin_presets_valid_and_complete(self) -> None:
        presets = scv.available_presets_list()
        self.assertEqual(
            [row["preset_id"] for row in presets],
            ["neutral_resident", "chiyo", "haru", "sola"],
        )
        self.assertTrue(presets[0]["is_default"])
        base_state = scv.default_character_pack_state()
        for pid in ("neutral_resident", "chiyo", "haru", "sola"):
            updated = scv.apply_select_preset(
                base_state, preset_id=pid, now_iso="2026-11-11T12:00:00+08:00"
            )
            self.assertEqual(updated["pack_doc"]["pack_id"], pid)
            self.assertEqual(updated["optional_processes"], [])
        with self.assertRaises(scv.CharacterPackValidationError) as ctx:
            scv.apply_select_preset(
                base_state, preset_id="nonexistent", now_iso="2026-11-11T12:00:00+08:00"
            )
        self.assertEqual(ctx.exception.reason_code, "PRESET_UNKNOWN")

    def test_04_canonical_pack_ref_key_order_invariance(self) -> None:
        pack_a = _valid_custom_pack()
        pack_b = {
            "starter_accessories": [
                {"mode": "ambient_filter", "label": "降噪耳机", "kind": "headphones"}
            ],
            "sensory_preferences": {
                "scent_note_enabled": True,
                "light_preference": "soft_warm",
                "noise_sensitivity": "high",
            },
            "persona": {"core": "安静整理旧唱片与窗边读书笔记的独立创作者。"},
            "address": {"user_calls_subject": ["小秋"], "subject_calls_user": ["哥哥"]},
            "relation_to_user": "partner",
            "adult": {"declared_age": 24, "life_stage": "working_adult"},
            "pronouns": "她/TA",
            "fictional": True,
            "display_name": "秋",
            "version": 3,
            "pack_id": "aki_reader",
            "body": {"preset": "neutral"},
            "kind": "proposed.character_pack.v1",
        }
        norm_a = scv.validate_and_normalize_pack(pack_a)
        norm_b = scv.validate_and_normalize_pack(pack_b)
        ref_a = scv.compute_pack_ref(norm_a)
        ref_b = scv.compute_pack_ref(norm_b)
        self.assertEqual(ref_a, ref_b)
        self.assertEqual(ref_a[0], "aki_reader")
        self.assertEqual(ref_a[1], 3)
        self.assertEqual(len(ref_a[2]), 16)

    def test_05_pack_kind_and_fictional_firewalls(self) -> None:
        with self.assertRaises(scv.CharacterPackValidationError) as ctx_kind:
            scv.validate_and_normalize_pack(_valid_custom_pack(kind="wrong.kind.v1"))
        self.assertEqual(ctx_kind.exception.reason_code, "PACK_KIND_UNKNOWN")

        for bad_fictional in (False, "true", 1, None):
            with self.assertRaises(scv.CharacterPackValidationError) as ctx_fic:
                scv.validate_and_normalize_pack(_valid_custom_pack(fictional=bad_fictional))
            self.assertEqual(ctx_fic.exception.reason_code, "PACK_NOT_FICTIONAL")

        with self.assertRaises(scv.CharacterPackValidationError) as ctx_extra:
            scv.validate_and_normalize_pack(_valid_custom_pack(mystery_field=123))
        self.assertEqual(ctx_extra.exception.reason_code, "PACK_FIELD_UNKNOWN:mystery_field")

    def test_06_recursive_forbidden_key_firewall(self) -> None:
        # Top-level deploy-only key
        with self.assertRaises(scv.CharacterPackValidationError) as ctx1:
            scv.validate_and_normalize_pack(_valid_custom_pack(adult_confirmed=True))
        self.assertEqual(ctx1.exception.reason_code, "PACK_FIELD_NOT_ALLOWED:adult_confirmed")

        # Nested dict user-only key
        with self.assertRaises(scv.CharacterPackValidationError) as ctx2:
            scv.validate_and_normalize_pack(
                _valid_custom_pack(sexual_seed={"preferred_tier": "TIER_2"})
            )
        self.assertEqual(
            ctx2.exception.reason_code, "PACK_FIELD_NOT_ALLOWED:sexual_seed.preferred_tier"
        )

        # Deeply nested list/dict smuggled user_genitals
        with self.assertRaises(scv.CharacterPackValidationError) as ctx3:
            scv.validate_and_normalize_pack(
                _valid_custom_pack(
                    obligations=[
                        {"title": "design_work", "meta": [{"user_genitals": "smuggled"}]}
                    ]
                )
            )
        self.assertEqual(
            ctx3.exception.reason_code,
            "PACK_FIELD_NOT_ALLOWED:obligations[0].meta[0].user_genitals",
        )

    def test_07_sexual_seed_power_exchange_and_draft_clamp_and_privacy(self) -> None:
        with self.assertRaises(scv.CharacterPackValidationError) as ctx_pe:
            scv.validate_and_normalize_pack(
                _valid_custom_pack(
                    sexual_seed={"stances": {"POWER_EXCHANGE": "curious"}}
                )
            )
        self.assertEqual(ctx_pe.exception.reason_code, "PACK_FIELD_INVALID:sexual_seed.stances")

        pack = _valid_custom_pack(
            sexual_seed={
                "seed_status": "CONFIRMED",
                "orientation": "all",
                "stances": {"GENTLE_AFFECTION": "open"},
                "solo_inclination": "low",
            }
        )
        state = scv.apply_custom_pack(
            scv.default_character_pack_state(),
            pack=pack,
            now_iso="2026-11-11T12:00:00+08:00",
        )
        self.assertEqual(state["pack_doc"]["sexual_seed"]["seed_status"], "DRAFT")

        partner_proj = scv.project_character_pack(state, subject_view=False)
        seed_sum = partner_proj["active_pack"]["seed_summary"]
        self.assertEqual(
            seed_sum,
            {
                "present": True,
                "effective_seed_status": "DRAFT",
                "pack_never_confirms_seed": True,
            },
        )
        dumped = json.dumps(partner_proj, ensure_ascii=False)
        for forbidden_leak in (
            "sexual_seed",
            "GENTLE_AFFECTION",
            "solo_inclination",
            "optional_processes",
        ):
            self.assertNotIn(forbidden_leak, dumped)

    def test_08_adult_gate_preview_minor_relation_institution_and_gege_partner(self) -> None:
        # 16 -> loads and adult_gate_preview fails with DECLARED_MINOR
        minor_pack = scv.validate_and_normalize_pack(
            _valid_custom_pack(adult={"life_stage": "university_student", "declared_age": 16})
        )
        minor_gate = scv.evaluate_adult_gate_preview(minor_pack)
        self.assertEqual(minor_gate["verdict"], "FAIL")
        self.assertIn("DECLARED_MINOR", minor_gate["reasons"])
        self.assertTrue(minor_gate["pack_never_opens_gate"])

        # Invalid declared_age types rejected
        for bad_age in (True, "18", 18.5, -1):
            with self.assertRaises(scv.CharacterPackValidationError) as ctx_age:
                scv.validate_and_normalize_pack(
                    _valid_custom_pack(
                        adult={"life_stage": "working_adult", "declared_age": bad_age}
                    )
                )
            self.assertEqual(ctx_age.exception.reason_code, "PACK_FIELD_INVALID:adult.declared_age")

        # blood_relative -> FORBIDDEN_RELATION FAIL; "sibling" -> 400 invalid
        rel_pack = scv.validate_and_normalize_pack(
            _valid_custom_pack(relation_to_user="blood_relative")
        )
        rel_gate = scv.evaluate_adult_gate_preview(rel_pack)
        self.assertEqual(rel_gate["verdict"], "FAIL")
        self.assertIn("FORBIDDEN_RELATION", rel_gate["reasons"])

        with self.assertRaises(scv.CharacterPackValidationError) as ctx_sib:
            scv.validate_and_normalize_pack(_valid_custom_pack(relation_to_user="sibling"))
        self.assertEqual(ctx_sib.exception.reason_code, "PACK_FIELD_INVALID:relation_to_user")

        # minor_education -> MINOR_INSTITUTION FAIL
        inst_pack = scv.validate_and_normalize_pack(
            _valid_custom_pack(
                obligations=[{"institution_kind": "minor_education", "label": "high_school"}]
            )
        )
        inst_gate = scv.evaluate_adult_gate_preview(inst_pack)
        self.assertEqual(inst_gate["verdict"], "FAIL")
        self.assertIn("MINOR_INSTITUTION", inst_gate["reasons"])

        # "哥哥" + "partner" allowed and requires deploy attestation
        gege_pack = scv.validate_and_normalize_pack(
            _valid_custom_pack(
                relation_to_user="partner",
                address={"subject_calls_user": ["哥哥"], "user_calls_subject": []},
            )
        )
        gege_gate = scv.evaluate_adult_gate_preview(gege_pack)
        self.assertEqual(
            gege_gate,
            {
                "verdict": "REQUIRES_DEPLOY_ATTESTATION",
                "reasons": ["ATTESTATION_NOT_IN_PACK"],
                "pack_never_opens_gate": True,
            },
        )

    def test_09_composable_body_and_accessory_mode_rules(self) -> None:
        state = scv.default_character_pack_state()
        ref_before = scv.compute_pack_ref(state["pack_doc"])
        updated = scv.apply_update_identity(
            state,
            fields_present={
                "display_name": "小晴",
                "pronouns": "她",
                "everyday_accessories": [
                    {"kind": "headphones", "label": "通勤耳机", "mode": "ambient_filter"}
                ],
            },
            now_iso="2026-11-11T12:30:00+08:00",
        )
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(updated["optional_processes"], [])
        self.assertNotEqual(scv.compute_pack_ref(updated["pack_doc"]), ref_before)

        # Glasses cannot use ambient_filter
        with self.assertRaises(scv.CharacterPackValidationError) as ctx_acc:
            scv.apply_update_identity(
                updated,
                fields_present={
                    "everyday_accessories": [
                        {"kind": "glasses", "label": "细框眼镜", "mode": "ambient_filter"}
                    ]
                },
                now_iso="2026-11-11T12:35:00+08:00",
            )
        self.assertEqual(ctx_acc.exception.reason_code, "ACCESSORY_MODE_INVALID")

        # Empty update_identity rejected
        with self.assertRaises(scv.CharacterPackValidationError) as ctx_empty:
            scv.apply_update_identity(
                updated,
                fields_present={"action": "update_identity"},
                now_iso="2026-11-11T12:36:00+08:00",
            )
        self.assertEqual(ctx_empty.exception.reason_code, "IDENTITY_UPDATE_EMPTY")

        # Optional physiology only visible in subject_view
        with_phys = scv.apply_set_optional_physiology(
            updated,
            optional_processes=["menstruation_lite"],
            now_iso="2026-11-11T12:40:00+08:00",
        )
        p_partner = scv.project_character_pack(with_phys, subject_view=False)
        p_her = scv.project_character_pack(with_phys, subject_view=True)
        self.assertNotIn("optional_processes", p_partner["active_pack"]["body_summary"])
        self.assertEqual(
            p_her["active_pack"]["body_summary"]["optional_processes"],
            ["menstruation_lite"],
        )

    def test_10_auditory_window_attenuation_headphones_and_adjacent_unseen_sounds(self) -> None:
        ambient_map = {
            "home": [
                {"event_type": "home_window_sound", "payload": {"summary": "窗外雨声变大"}}
            ],
            "nearby_street": [
                {
                    "event_type": "nearby_street_school_children",
                    "payload": {"summary": "放学时段有几个学生从街口经过"},
                }
            ],
        }
        neighbors = [{"place_id": "nearby_street", "label": "家门外街道"}]
        world = _sample_world()
        body = _sample_body()
        char_neutral = scv.default_character_pack_state()
        cards = scv.empty_sensory_cards_state()

        expected_pairs = [
            ("open", "none", "clear"),
            ("ajar", "partial_window", "softened"),
            ("closed", "closed_window_not_soundproof", "muffled"),
        ]
        for win_mode, exp_att, exp_clarity in expected_pairs:
            som = somatic_view.empty_state()
            som, _, _ = somatic_view.set_window(
                som, win_mode, "2026-11-11T13:00:00+09:00"
            )
            proj = scv.derive_sensory_field(
                world_result=world,
                body_view=body,
                somatic_state=som,
                somatic_reason=None,
                character_state=char_neutral,
                cards_state=cards,
                cards_reason=None,
                now_iso="2026-11-11T13:00:00+09:00",
                ambient_by_place=ambient_map,
                adjacent_places=neighbors,
            )
            aud = proj["channels"]["auditory"]
            self.assertEqual(aud["attenuation"], exp_att)
            self.assertEqual(aud["clarity"], exp_clarity)
            self.assertFalse(aud["closed_door_or_window_is_absolute_soundproof"])
            self.assertEqual(len(aud["adjacent_unseen_sounds"]), 1)
            adj = aud["adjacent_unseen_sounds"][0]
            self.assertEqual(adj["from_place_id"], "nearby_street")
            self.assertFalse(adj["visual_access"])
            self.assertEqual(adj["identity_certainty"], "sound_only_unverified")
            self.assertEqual(adj["clarity"], "distant_or_doorway")

        # Sola wears headphones with ambient_filter -> headphones_ambient_filter
        char_sola = scv.apply_select_preset(
            char_neutral, preset_id="sola", now_iso="2026-11-11T13:00:00+09:00"
        )
        som_open = somatic_view.empty_state()
        som_open, _, _ = somatic_view.set_window(
            som_open, "open", "2026-11-11T13:00:00+09:00"
        )
        proj_sola = scv.derive_sensory_field(
            world_result=world,
            body_view=body,
            somatic_state=som_open,
            somatic_reason=None,
            character_state=char_sola,
            cards_state=cards,
            cards_reason=None,
            now_iso="2026-11-11T13:00:00+09:00",
            ambient_by_place=ambient_map,
            adjacent_places=neighbors,
        )
        self.assertEqual(
            proj_sola["channels"]["auditory"]["attenuation"], "headphones_ambient_filter"
        )

    def test_11_visual_daytime_glare_vs_warm_lamp_vs_night_unlit(self) -> None:
        body = _sample_body()
        cards = scv.empty_sensory_cards_state()
        char_chiyo = scv.apply_select_preset(
            scv.default_character_pack_state(),
            preset_id="chiyo",
            now_iso="2026-11-11T13:00:00+09:00",
        )
        som = somatic_view.empty_state()
        som, _, _ = somatic_view.set_window(
            som, "open", "2026-11-11T13:00:00+09:00"
        )
        som, _, _ = somatic_view.set_local_position(
            som,
            zone="window_side",
            place_id="home",
            area_id="bedroom",
            now_iso="2026-11-11T13:00:00+09:00",
        )
        # 13:00 open window at window_side without rain -> window_glare
        proj_glare = scv.derive_sensory_field(
            world_result=_sample_world(local_time="2026-11-11T13:00:00+09:00", light_power="off"),
            body_view=body,
            somatic_state=som,
            somatic_reason=None,
            character_state=char_chiyo,
            cards_state=cards,
            cards_reason=None,
            now_iso="2026-11-11T13:00:00+09:00",
            ambient_by_place={},
            adjacent_places=[],
        )
        self.assertEqual(proj_glare["channels"]["visual"]["lighting_band"], "window_glare")
        self.assertEqual(
            proj_glare["channels"]["visual"]["reading_suitability"], "glare_near_window"
        )
        self.assertTrue(proj_glare["channels"]["visual"]["visible_is_not_reachable"])
        self.assertEqual(proj_glare["channels"]["sensory_load"]["load_band"], "elevated")
        codes = [
            row["code"]
            for row in proj_glare["channels"]["sensory_load"]["suggested_adjustments"]
        ]
        self.assertIn("move_from_window_glare", codes)

        # Move to desk_side at 21:00 with warm_floor_lamp ON -> warm_reading_light & comfortable
        som_desk, _, _ = somatic_view.set_local_position(
            som,
            zone="desk_side",
            place_id="home",
            area_id="bedroom",
            now_iso="2026-11-11T21:00:00+09:00",
        )
        proj_warm = scv.derive_sensory_field(
            world_result=_sample_world(
                local_time="2026-11-11T21:00:00+09:00",
                light_power="on",
                light_band="moderate",
                declared_source="catalog:warm_floor_lamp",
            ),
            body_view=body,
            somatic_state=som_desk,
            somatic_reason=None,
            character_state=char_chiyo,
            cards_state=cards,
            cards_reason=None,
            now_iso="2026-11-11T21:00:00+09:00",
            ambient_by_place={},
            adjacent_places=[],
        )
        self.assertEqual(
            proj_warm["channels"]["visual"]["lighting_band"], "warm_reading_light"
        )
        self.assertEqual(proj_warm["channels"]["visual"]["reading_suitability"], "comfortable")

        # 21:00 with lamp OFF -> unlit_indoor & too_dim
        proj_dark = scv.derive_sensory_field(
            world_result=_sample_world(
                local_time="2026-11-11T21:00:00+09:00", light_power="off"
            ),
            body_view=body,
            somatic_state=som_desk,
            somatic_reason=None,
            character_state=char_chiyo,
            cards_state=cards,
            cards_reason=None,
            now_iso="2026-11-11T21:00:00+09:00",
            ambient_by_place={},
            adjacent_places=[],
        )
        self.assertEqual(proj_dark["channels"]["visual"]["lighting_band"], "unlit_indoor")
        self.assertEqual(proj_dark["channels"]["visual"]["reading_suitability"], "too_dim")

    def test_12_olfactory_tea_cooling_damp_coat_stuffiness_and_preference_gate(self) -> None:
        world = _sample_world(local_time="2026-11-11T15:00:00+09:00")
        body = _sample_body()
        cards = scv.empty_sensory_cards_state()
        char_state = scv.default_character_pack_state()

        som = somatic_view.empty_state()
        som, _, _ = somatic_view.set_window(
            som, "closed", "2026-11-11T15:00:00+09:00"
        )
        som, _, _, _ = somatic_view.offer_care(
            som,
            slot="drink",
            kind="hot_tea",
            actor_side="partner",
            position="desk_side",
            place_id="home",
            area_id="bedroom",
            now_iso="2026-11-11T15:00:00+09:00",
            bound_item=None,
        )
        som, _, _ = somatic_view.wear_outerwear(
            som,
            garment_id="coat-1",
            label="薄风衣",
            dryness="damp",
            now_iso="2026-11-11T15:00:00+09:00",
        )
        som, _, _ = somatic_view.hang_outerwear(
            som,
            garment_id="coat-1",
            hang_position="hook",
            dryness="damp",
            now_iso="2026-11-11T15:01:00+09:00",
        )

        # At 15:05 (5 min after tea placed): tea_steam + indoor_damp_cloth both active!
        proj_5m = scv.derive_sensory_field(
            world_result=world,
            body_view=body,
            somatic_state=som,
            somatic_reason=None,
            character_state=char_state,
            cards_state=cards,
            cards_reason=None,
            now_iso="2026-11-11T15:05:00+09:00",
            ambient_by_place={},
            adjacent_places=[],
        )
        cues_5m = [c["cue_id"] for c in proj_5m["channels"]["olfactory"]["cues"]]
        self.assertIn("tea_steam", cues_5m)
        self.assertIn("indoor_damp_cloth", cues_5m)
        self.assertFalse(proj_5m["channels"]["olfactory"]["creates_food_inventory"])
        self.assertTrue(proj_5m["channels"]["sensory_load"]["indoor_stuffy"])
        adj_codes = [
            r["code"] for r in proj_5m["channels"]["sensory_load"]["suggested_adjustments"]
        ]
        self.assertIn("open_window_to_ventilate", adj_codes)

        # At 15:35 (35 min after tea placed -> room_temp) & open window + rain -> tea_steam gone,
        # indoor_damp_cloth replaced by ventilating_draft + rain_breeze!
        som_open, _, _ = somatic_view.set_window(
            som, "open", "2026-11-11T15:35:00+09:00"
        )
        proj_35m = scv.derive_sensory_field(
            world_result=world,
            body_view=body,
            somatic_state=som_open,
            somatic_reason=None,
            character_state=char_state,
            cards_state=cards,
            cards_reason=None,
            now_iso="2026-11-11T15:35:00+09:00",
            ambient_by_place={
                "home": [{"event_type": "home_window_sound", "payload": {"summary": "窗外雨声变大"}}]
            },
            adjacent_places=[],
        )
        cues_35m = [c["cue_id"] for c in proj_35m["channels"]["olfactory"]["cues"]]
        self.assertNotIn("tea_steam", cues_35m)
        self.assertNotIn("indoor_damp_cloth", cues_35m)
        self.assertIn("ventilating_draft", cues_35m)
        self.assertIn("rain_breeze", cues_35m)

        # Disable scent_note_enabled -> disabled_by_preference & cues == []
        char_no_scent = scv.apply_update_identity(
            char_state,
            fields_present={
                "sensory_preferences": {
                    "noise_sensitivity": "moderate",
                    "light_preference": "balanced",
                    "scent_note_enabled": False,
                }
            },
            now_iso="2026-11-11T15:35:00+09:00",
        )
        proj_no_scent = scv.derive_sensory_field(
            world_result=world,
            body_view=body,
            somatic_state=som_open,
            somatic_reason=None,
            character_state=char_no_scent,
            cards_state=cards,
            cards_reason=None,
            now_iso="2026-11-11T15:35:00+09:00",
            ambient_by_place={},
            adjacent_places=[],
        )
        self.assertEqual(
            proj_no_scent["channels"]["olfactory"]["status"], "disabled_by_preference"
        )
        self.assertEqual(proj_no_scent["channels"]["olfactory"]["cues"], [])

    def test_13_tactile_thermal_and_sensory_load_boundaries(self) -> None:
        world = _sample_world()
        body = _sample_body(
            signals=[{"signal_type": "sensory_load_elevated", "qualitative_band": "mild"}]
        )
        som = somatic_view.empty_state()
        proj = scv.derive_sensory_field(
            world_result=world,
            body_view=body,
            somatic_state=som,
            somatic_reason=None,
            character_state=scv.default_character_pack_state(),
            cards_state=scv.empty_sensory_cards_state(),
            cards_reason=None,
            now_iso="2026-11-11T13:00:00+09:00",
            ambient_by_place={},
            adjacent_places=[],
        )
        tt = proj["channels"]["tactile_thermal"]
        self.assertEqual(set(tt.keys()), {"thermal", "moisture", "posture", "tactile"})
        self.assertEqual(tt["posture"]["quality"], "seated_support_unverified")
        sload = proj["channels"]["sensory_load"]
        self.assertTrue(sload["body_signal_elevated"])
        self.assertEqual(sload["load_band"], "elevated")
        self.assertFalse(sload["maps_to_emotion_or_pathology"])

    def test_14_sound_cards_quiet_fallback_cap_and_privacy(self) -> None:
        world = _sample_world()
        body = _sample_body()
        som = somatic_view.empty_state()
        cards_state = scv.empty_sensory_cards_state()
        base_proj = scv.derive_sensory_field(
            world_result=world,
            body_view=body,
            somatic_state=som,
            somatic_reason=None,
            character_state=scv.default_character_pack_state(),
            cards_state=cards_state,
            cards_reason=None,
            now_iso="2026-11-11T22:00:00+09:00",
            ambient_by_place={},
            adjacent_places=[],
        )
        # Quiet room fallback -> ["quiet_ambient_room"] & quiet_fallback=True
        cards_state, card1, replayed, conflict = scv.apply_capture_sound_card(
            cards_state,
            sensory_projection=base_proj,
            title="深夜安静的书桌",
            impression_note="连钟表声都很轻",
            shared=False,
            operation_key="op-quiet-1",
            now_iso="2026-11-11T22:00:00+09:00",
        )
        self.assertFalse(replayed)
        self.assertIsNone(conflict)
        self.assertTrue(card1["quiet_fallback"])
        self.assertEqual(card1["heard_sources"], ["quiet_ambient_room"])
        self.assertFalse(card1["has_audio_recording"])
        self.assertEqual(card1["medium"], "text_sound_card")

        # Replay same operation_key -> already_applied
        cards_state_replay, card1_replay, replayed2, conflict2 = scv.apply_capture_sound_card(
            cards_state,
            sensory_projection=base_proj,
            title="深夜安静的书桌",
            shared=False,
            operation_key="op-quiet-1",
            now_iso="2026-11-11T22:01:00+09:00",
        )
        self.assertTrue(replayed2)
        self.assertIsNone(conflict2)
        self.assertEqual(card1_replay["card_id"], card1["card_id"])
        self.assertEqual(cards_state_replay["revision"], cards_state["revision"])

        # Partner view hides private card AND leaks zero private_count / total_count
        partner_view = scv.derive_sensory_field(
            world_result=world,
            body_view=body,
            somatic_state=som,
            somatic_reason=None,
            character_state=scv.default_character_pack_state(),
            cards_state=cards_state,
            cards_reason=None,
            now_iso="2026-11-11T22:02:00+09:00",
            subject_view=False,
            ambient_by_place={},
            adjacent_places=[],
        )
        sc_block = partner_view["sound_cards"]
        self.assertEqual(sc_block["items"], [])
        self.assertEqual(sc_block["visible_count"], 0)
        self.assertNotIn("private_count", sc_block)
        self.assertNotIn("total_count", sc_block)

        # Fill up to 12 cards, 13th -> SOUND_CARD_LIMIT
        for idx in range(2, 13):
            cards_state, _, _, err = scv.apply_capture_sound_card(
                cards_state,
                sensory_projection=base_proj,
                title=f"片段-{idx}",
                shared=(idx % 2 == 0),
                operation_key=f"op-{idx}",
                now_iso=f"2026-11-11T22:{idx:02d}:00+09:00",
            )
            self.assertIsNone(err)
        _, _, _, limit_err = scv.apply_capture_sound_card(
            cards_state,
            sensory_projection=base_proj,
            title="第13张",
            operation_key="op-13",
            now_iso="2026-11-11T22:30:00+09:00",
        )
        self.assertEqual(limit_err, "SOUND_CARD_LIMIT")

    def test_14b_outdoor_place_auditory_and_visual_projection(self) -> None:
        outdoor_world = _sample_world(
            place_id="nearby_street",
            place_label="家门外街道",
            area_id="street",
            home=False,
            local_time="2026-11-11T16:00:00+09:00",
        )
        proj = scv.derive_sensory_field(
            world_result=outdoor_world,
            body_view=_sample_body(pose="standing"),
            somatic_state=somatic_view.empty_state(),
            somatic_reason=None,
            character_state=scv.default_character_pack_state(),
            cards_state=scv.empty_sensory_cards_state(),
            cards_reason=None,
            now_iso="2026-11-11T16:00:00+09:00",
            ambient_by_place={
                "nearby_street": [
                    {
                        "event_type": "street_construction",
                        "payload": {"summary": "路边正在修排水管"},
                    }
                ]
            },
            adjacent_places=[],
        )
        self.assertEqual(
            proj["channels"]["auditory"]["window_state"], "outdoor_direct"
        )
        self.assertEqual(
            proj["channels"]["auditory"]["attenuation"], "outdoor_direct"
        )
        self.assertEqual(proj["channels"]["visual"]["lighting_band"], "outdoor_open")

    def test_14c_bedside_lamp_pool_and_accessory_private_note_stripping(self) -> None:
        state = scv.apply_update_identity(
            scv.default_character_pack_state(),
            fields_present={
                "everyday_accessories": [
                    {
                        "kind": "watch",
                        "label": "旧机械表",
                        "mode": "wear",
                        "note": "日常走时",
                        "private_note": "只留给自己的纪念刻字",
                    }
                ]
            },
            now_iso="2026-11-11T22:10:00+09:00",
        )
        partner_pack = scv.project_character_pack(state, subject_view=False)
        her_pack = scv.project_character_pack(state, subject_view=True)
        self.assertNotIn(
            "private_note", partner_pack["active_pack"]["everyday_accessories"][0]
        )
        self.assertEqual(
            her_pack["active_pack"]["everyday_accessories"][0]["private_note"],
            "只留给自己的纪念刻字",
        )

        proj = scv.derive_sensory_field(
            world_result=_sample_world(
                local_time="2026-11-11T22:10:00+09:00",
                light_power="on",
                light_band="dim",
                declared_source="catalog:bedside_lamp",
            ),
            body_view=_sample_body(),
            somatic_state=somatic_view.empty_state(),
            somatic_reason=None,
            character_state=state,
            cards_state=scv.empty_sensory_cards_state(),
            cards_reason=None,
            now_iso="2026-11-11T22:10:00+09:00",
            subject_view=False,
            ambient_by_place={},
            adjacent_places=[],
        )
        self.assertEqual(proj["channels"]["visual"]["lighting_band"], "dim_lamp_pool")
        self.assertEqual(proj["channels"]["visual"]["reading_suitability"], "comfortable")
        self.assertNotIn(
            "private_note", proj["character"]["everyday_accessories"][0]
        )


@POSIX_ONLY
class SensoryCharacterLoopbackHTTPTest(unittest.TestCase):
    """Real Supply/World/Body owners + real loopback HTTP tests."""

    @classmethod
    def setUpClass(cls) -> None:
        import sensory_character_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls.root = Path(tempfile.mkdtemp(prefix="sensory-char-http-"))
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-11T12:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        scv.character_pack_state_path(self.stack.root).unlink(missing_ok=True)
        scv.sensory_cards_state_path(self.stack.root).unlink(missing_ok=True)
        somatic_view.state_path(self.stack.root).unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 14, 12, 0, tzinfo=timezone(timedelta(hours=9))
        )

    def _call(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        csrf: bool = False,
        capability: str | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        data = (
            None
            if body is None
            else json.dumps(body, ensure_ascii=False).encode("utf-8")
        )
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if csrf:
            request.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            request.add_header("X-Phone-Capability", capability)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                return answer.status, json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _her_post(self, path: str, body: dict) -> tuple[int, dict]:
        return self._call("POST", path, body, csrf=True, capability=CAP_TOKEN)

    def _partner_post(self, path: str, body: dict) -> tuple[int, dict]:
        return self._call("POST", path, body, csrf=True)

    def test_15_http_zero_write_gets_and_neutral_default(self) -> None:
        pack_file = scv.character_pack_state_path(self.stack.root)
        cards_file = scv.sensory_cards_state_path(self.stack.root)
        self.assertFalse(pack_file.exists())
        self.assertFalse(cards_file.exists())

        code1, res1 = self._call("GET", "/api/phone/character/pack")
        self.assertEqual(code1, 200)
        data1 = res1["data"]
        self.assertEqual(data1["active_pack"]["pack_id"], "neutral_resident")
        self.assertEqual(data1["active_pack"]["display_name"], "TA")
        self.assertEqual(data1["source"], "default_neutral_preset")
        self.assertFalse(data1["boundaries"]["locked_to_chiyo"])
        self.assertEqual(len(data1["available_presets"]), 4)

        code2, res2 = self._call("GET", "/api/phone/body/sensory")
        self.assertEqual(code2, 200)
        data2 = res2["data"]
        self.assertEqual(data2["schema"], "phone.body.sensory_field.v1")
        self.assertEqual(
            set(data2["channels"].keys()),
            {"auditory", "visual", "olfactory", "tactile_thermal", "sensory_load"},
        )

        # Strictly zero-write on GET!
        self.assertFalse(pack_file.exists())
        self.assertFalse(cards_file.exists())

    def test_16_http_presets_custom_pack_and_contract_38_firewalls(self) -> None:
        # Select preset sola
        code, res = self._partner_post(
            "/api/phone/character/pack",
            {"action": "select_preset", "preset_id": "sola"},
        )
        self.assertEqual(code, 200)
        self.assertEqual(res["data"]["active_pack"]["display_name"], "空")
        self.assertEqual(
            res["data"]["active_pack"]["everyday_accessories"][0]["mode"],
            "ambient_filter",
        )

        # Unknown preset -> 400 PRESET_UNKNOWN
        code_bad, res_bad = self._partner_post(
            "/api/phone/character/pack",
            {"action": "select_preset", "preset_id": "ghost_preset"},
        )
        self.assertEqual(code_bad, 400)
        self.assertEqual(res_bad["reason_code"], "PRESET_UNKNOWN")

        # Forbidden nested field -> 400 PACK_FIELD_NOT_ALLOWED:...
        bad_pack = _valid_custom_pack(
            persona={"core": "测试", "nested": [{"adult_confirmed": True}]}
        )
        code_f, res_f = self._partner_post(
            "/api/phone/character/pack",
            {"action": "apply_custom_pack", "pack": bad_pack},
        )
        self.assertEqual(code_f, 400)
        self.assertEqual(
            res_f["reason_code"],
            "PACK_FIELD_NOT_ALLOWED:persona.nested[0].adult_confirmed",
        )

        # Valid custom pack with sexual_seed CONFIRMED -> clamped to DRAFT & withheld in partner GET
        custom = _valid_custom_pack(
            sexual_seed={"seed_status": "CONFIRMED", "stances": {"WARM_CLOSENESS": "yes"}}
        )
        code_ok, res_ok = self._partner_post(
            "/api/phone/character/pack",
            {"action": "apply_custom_pack", "pack": custom},
        )
        self.assertEqual(code_ok, 200)
        self.assertEqual(
            res_ok["data"]["active_pack"]["seed_summary"]["effective_seed_status"],
            "DRAFT",
        )
        self.assertNotIn("WARM_CLOSENESS", json.dumps(res_ok, ensure_ascii=False))

    def test_17_http_composable_body_capability_gate_and_privacy(self) -> None:
        # Changing pronouns/name never enables optional_processes
        code_id, res_id = self._partner_post(
            "/api/phone/character/pack",
            {
                "action": "update_identity",
                "display_name": "千夏",
                "pronouns": "她",
                "subject_calls_user": ["哥哥"],
            },
        )
        self.assertEqual(code_id, 200)
        _, her_check = self._call(
            "GET", "/api/phone/character/pack", capability=CAP_TOKEN
        )
        self.assertEqual(
            her_check["data"]["active_pack"]["body_summary"]["optional_processes"], []
        )

        # Without capability -> 403 on set_optional_physiology
        code_403, _ = self._partner_post(
            "/api/phone/character/pack",
            {
                "action": "set_optional_physiology",
                "optional_processes": ["menstruation_lite"],
            },
        )
        self.assertEqual(code_403, 403)

        # With capability -> 200; visible in her view, absent in partner_default view
        code_200, res_200 = self._her_post(
            "/api/phone/character/pack",
            {
                "action": "set_optional_physiology",
                "optional_processes": ["menstruation_lite"],
            },
        )
        self.assertEqual(code_200, 200)
        self.assertEqual(
            res_200["data"]["active_pack"]["body_summary"]["optional_processes"],
            ["menstruation_lite"],
        )

        _, partner_get = self._call("GET", "/api/phone/character/pack")
        self.assertNotIn(
            "optional_processes", partner_get["data"]["active_pack"]["body_summary"]
        )
        _, her_get = self._call(
            "GET", "/api/phone/character/pack", capability=CAP_TOKEN
        )
        self.assertEqual(
            her_get["data"]["active_pack"]["body_summary"]["optional_processes"],
            ["menstruation_lite"],
        )

        # Invalid accessory mode (glasses + ambient_filter) -> 400
        code_acc, res_acc = self._partner_post(
            "/api/phone/character/pack",
            {
                "action": "update_identity",
                "everyday_accessories": [
                    {"kind": "glasses", "label": "平光镜", "mode": "ambient_filter"}
                ],
            },
        )
        self.assertEqual(code_acc, 400)
        self.assertEqual(res_acc["reason_code"], "ACCESSORY_MODE_INVALID")

    def test_18_http_live_window_attenuation_and_tea_cooling_integration(self) -> None:
        # Open window via somatic garment-window endpoint
        c_open, _ = self._her_post(
            "/api/phone/body/garment-window",
            {"action": "set_window", "state": "open"},
        )
        self.assertEqual(c_open, 200)
        _, s_open = self._call("GET", "/api/phone/body/sensory")
        self.assertEqual(s_open["data"]["channels"]["auditory"]["attenuation"], "none")
        self.assertEqual(s_open["data"]["channels"]["auditory"]["clarity"], "clear")

        # Ajar window -> partial_window
        self._her_post(
            "/api/phone/body/garment-window",
            {"action": "set_window", "state": "ajar"},
        )
        _, s_ajar = self._call("GET", "/api/phone/body/sensory")
        self.assertEqual(
            s_ajar["data"]["channels"]["auditory"]["attenuation"], "partial_window"
        )

        # Close window -> closed_window_not_soundproof
        self._her_post(
            "/api/phone/body/garment-window",
            {"action": "set_window", "state": "closed"},
        )
        _, s_closed = self._call("GET", "/api/phone/body/sensory")
        self.assertEqual(
            s_closed["data"]["channels"]["auditory"]["attenuation"],
            "closed_window_not_soundproof",
        )
        self.assertFalse(
            s_closed["data"]["channels"]["auditory"][
                "closed_door_or_window_is_absolute_soundproof"
            ]
        )

        # Offer hot_tea -> tea_steam olfactory cue appears
        c_tea, _ = self._partner_post(
            "/api/phone/body/care-offering",
            {
                "action": "offer",
                "slot": "drink",
                "kind": "hot_tea",
                "actor_side": "partner",
                "position": "hand_side",
            },
        )
        self.assertEqual(c_tea, 200)
        _, s_tea = self._call("GET", "/api/phone/body/sensory")
        cue_ids = [c["cue_id"] for c in s_tea["data"]["channels"]["olfactory"]["cues"]]
        self.assertIn("tea_steam", cue_ids)

    def test_19_http_sound_cards_capability_idempotency_privacy_and_share_lifecycle(
        self,
    ) -> None:
        # Capture without capability -> 403
        code_403, _ = self._partner_post(
            "/api/phone/body/sensory",
            {"action": "capture_sound_card", "title": "午后窗边"},
        )
        self.assertEqual(code_403, 403)

        # Capture private card with operation_key
        code_cap, res_cap = self._her_post(
            "/api/phone/body/sensory",
            {
                "action": "capture_sound_card",
                "title": "雨声记事",
                "impression_note": "雨点敲在窗框上很均匀",
                "shared": False,
                "operation_key": "http-scard-key-1",
            },
        )
        self.assertEqual(code_cap, 200)
        self.assertEqual(res_cap["status"], "applied")
        card = res_cap["data"]["sound_card"]
        card_id = card["card_id"]
        self.assertFalse(card["has_audio_recording"])
        self.assertEqual(card["medium"], "text_sound_card")

        # Replay same operation_key -> 200 already_applied, no duplicate created
        code_rep, res_rep = self._her_post(
            "/api/phone/body/sensory",
            {
                "action": "capture_sound_card",
                "title": "雨声记事",
                "shared": False,
                "operation_key": "http-scard-key-1",
            },
        )
        self.assertEqual(code_rep, 200)
        self.assertEqual(res_rep["status"], "already_applied")
        self.assertEqual(res_rep["data"]["sound_card"]["card_id"], card_id)

        # Partner default GET: card invisible, zero private_count or total_count!
        _, p_get = self._call("GET", "/api/phone/body/sensory")
        self.assertEqual(p_get["data"]["sound_cards"]["items"], [])
        self.assertEqual(p_get["data"]["sound_cards"]["visible_count"], 0)
        self.assertNotIn("private_count", p_get["data"]["sound_cards"])
        self.assertNotIn("total_count", p_get["data"]["sound_cards"])

        # Her GET: sees card & total_count == 1
        _, h_get = self._call("GET", "/api/phone/body/sensory", capability=CAP_TOKEN)
        self.assertEqual(len(h_get["data"]["sound_cards"]["items"]), 1)
        self.assertEqual(h_get["data"]["sound_cards"]["total_count"], 1)

        # Share card -> now visible in partner_default WITHOUT operation_key
        code_sh, _ = self._her_post(
            "/api/phone/body/sensory",
            {"action": "set_sound_card_share", "card_id": card_id, "shared": True},
        )
        self.assertEqual(code_sh, 200)
        _, p_get2 = self._call("GET", "/api/phone/body/sensory")
        self.assertEqual(len(p_get2["data"]["sound_cards"]["items"]), 1)
        self.assertNotIn("operation_key", p_get2["data"]["sound_cards"]["items"][0])

        # Remove card & check 404 on second remove
        code_rm, _ = self._her_post(
            "/api/phone/body/sensory",
            {"action": "remove_sound_card", "card_id": card_id},
        )
        self.assertEqual(code_rm, 200)
        code_404, res_404 = self._her_post(
            "/api/phone/body/sensory",
            {"action": "remove_sound_card", "card_id": card_id},
        )
        self.assertEqual(code_404, 404)
        self.assertEqual(res_404["reason_code"], "SOUND_CARD_NOT_FOUND")

    def test_20_http_capture_sound_card_world_unavailable_conflict(self) -> None:
        saved_world = self.stack.service.world
        self.stack.service.world = lambda: {
            "ok": False,
            "status": "unavailable",
            "reason_code": "WORLD_DOWN",
            "data": {},
        }
        try:
            code, res = self._her_post(
                "/api/phone/body/sensory",
                {"action": "capture_sound_card", "title": "断联测试"},
            )
            self.assertEqual(code, 409)
            self.assertEqual(res["reason_code"], "WORLD_UNAVAILABLE")
        finally:
            self.stack.service.world = saved_world

    def test_21_http_partner_sensory_notes_cap_remove_and_clear(self) -> None:
        note_ids: list[str] = []
        for idx in range(1, 4):
            code, res = self._partner_post(
                "/api/phone/body/sensory",
                {"action": "leave_sensory_note", "text": f"天冷记得关小一点窗-{idx}"},
            )
            self.assertEqual(code, 200)
            note_ids.append(res["data"]["note"]["note_id"])

        # 4th note -> 409 PARTNER_SENSORY_NOTE_LIMIT
        code_4th, res_4th = self._partner_post(
            "/api/phone/body/sensory",
            {"action": "leave_sensory_note", "text": "第四条超出上限"},
        )
        self.assertEqual(code_4th, 409)
        self.assertEqual(res_4th["reason_code"], "PARTNER_SENSORY_NOTE_LIMIT")

        # Remove one note, verify missing note -> 404
        code_rm, _ = self._partner_post(
            "/api/phone/body/sensory",
            {"action": "remove_sensory_note", "note_id": note_ids[0]},
        )
        self.assertEqual(code_rm, 200)
        code_404, res_404 = self._partner_post(
            "/api/phone/body/sensory",
            {"action": "remove_sensory_note", "note_id": note_ids[0]},
        )
        self.assertEqual(code_404, 404)
        self.assertEqual(res_404["reason_code"], "SENSORY_NOTE_NOT_FOUND")

        # Clear remaining notes
        code_clr, res_clr = self._partner_post(
            "/api/phone/body/sensory",
            {"action": "clear_sensory_notes"},
        )
        self.assertEqual(code_clr, 200)
        self.assertEqual(res_clr["data"]["cleared_count"], 2)

    def test_22_http_strict_query_extra_fields_empty_optional_and_bad_capability(
        self,
    ) -> None:
        # Query string rejected with 400 UNKNOWN_QUERY on both GET endpoints
        c_q1, r_q1 = self._call("GET", "/api/phone/character/pack?foo=1")
        self.assertEqual(c_q1, 400)
        self.assertEqual(r_q1["reason_code"], "UNKNOWN_QUERY")

        c_q2, r_q2 = self._call("GET", "/api/phone/body/sensory?bar=2")
        self.assertEqual(c_q2, 400)
        self.assertEqual(r_q2["reason_code"], "UNKNOWN_QUERY")

        # Bad capability header fails closed with 403 on GET and on partner POST!
        c_bad_get, _ = self._call(
            "GET", "/api/phone/character/pack", capability="wrong-cap-token"
        )
        self.assertEqual(c_bad_get, 403)
        c_bad_post, _ = self._call(
            "POST",
            "/api/phone/body/sensory",
            {"action": "leave_sensory_note", "text": "测试"},
            csrf=True,
            capability="wrong-cap-token",
        )
        self.assertEqual(c_bad_post, 403)

        # Extra field on per-action whitelist -> 400 UNKNOWN_FIELD:...
        c_extra, r_extra = self._partner_post(
            "/api/phone/character/pack",
            {
                "action": "select_preset",
                "preset_id": "haru",
                "display_name": "混入字段",
            },
        )
        self.assertEqual(c_extra, 400)
        self.assertTrue(r_extra["reason_code"].startswith("UNKNOWN_FIELD:"))

        # Empty-string optional fields rejected with 400 instead of silently ignored!
        c_empty_title, _ = self._her_post(
            "/api/phone/body/sensory",
            {"action": "capture_sound_card", "title": "   "},
        )
        self.assertEqual(c_empty_title, 400)

        c_empty_imp, _ = self._her_post(
            "/api/phone/body/sensory",
            {"action": "capture_sound_card", "impression_note": ""},
        )
        self.assertEqual(c_empty_imp, 400)

        c_empty_op, _ = self._her_post(
            "/api/phone/body/sensory",
            {"action": "capture_sound_card", "operation_key": ""},
        )
        self.assertEqual(c_empty_op, 400)

    def test_23_http_missing_csrf_refused_with_403(self) -> None:
        c1, r1 = self._call(
            "POST",
            "/api/phone/character/pack",
            {"action": "select_preset", "preset_id": "haru"},
            csrf=False,
        )
        self.assertEqual(c1, 403)
        self.assertEqual(r1["reason_code"], "CSRF_TOKEN_MISSING")

        c2, r2 = self._call(
            "POST",
            "/api/phone/body/sensory",
            {"action": "leave_sensory_note", "text": "没有CSRF"},
            csrf=False,
        )
        self.assertEqual(c2, 403)
        self.assertEqual(r2["reason_code"], "CSRF_TOKEN_MISSING")

    def test_24_corrupt_state_files_fail_closed_on_write(self) -> None:
        pack_path = scv.character_pack_state_path(self.stack.root)
        pack_path.parent.mkdir(parents=True, exist_ok=True)
        pack_path.write_text("{corrupt-json", encoding="utf-8")
        c_pack, r_pack = self._partner_post(
            "/api/phone/character/pack",
            {"action": "select_preset", "preset_id": "haru"},
        )
        self.assertEqual(c_pack, 409)
        self.assertEqual(r_pack["reason_code"], "CHARACTER_PACK_STATE_CORRUPT")
        pack_path.unlink(missing_ok=True)

        cards_path = scv.sensory_cards_state_path(self.stack.root)
        cards_path.write_text("{corrupt-cards", encoding="utf-8")
        c_cards, r_cards = self._partner_post(
            "/api/phone/body/sensory",
            {"action": "leave_sensory_note", "text": "损坏时不覆盖"},
        )
        self.assertEqual(c_cards, 409)
        self.assertEqual(r_cards["reason_code"], "SENSORY_CARDS_STATE_CORRUPT")


if __name__ == "__main__":
    unittest.main()

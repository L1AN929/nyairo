#!/usr/bin/env python3
"""Deterministic verification suite for Fix Worker 1:
- TA Brain (`chiyo_bundle/lived_context_bridge.py`): zero-write reads, <= 1800 char cap,
  calm zero-char gate, partner privacy filtering, and Wave A / B1 / B2 + skill/drift surfacing.
- Tool Surface & Security (`chiyo_bundle/phone_tool.py`): LIFE_UPDATE_SURFACES == 37,
  updated hints, partner-impersonation blocks, subject_ref guard, restricted-memory gate.
- Autonomous Producers (`components/life/scripts/v02_v03_daily_loop_bridge.py`):
  F-175/F-176 AG0 CandidateRecords, second-order enforcement details & receipts,
  bounded inquiry auto-conclusion -> belief_revisions + S03 claim, and soft-wrapped
  auto skill book / drift snapshot / room expectations / ecology chain / body history.
- 21-route zero-write soak coverage (`scripts/v02_wall_clock_soak.py`).
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "chiyo_bundle",
    REPO_ROOT / "scripts",
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from chiyo_bundle import lived_context_bridge  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import v02_agency_depth_engine as ad_engine  # noqa: E402
from components.life.scripts import v02_autobiography_skill_provenance as asp  # noqa: E402
from components.life.scripts import v02_midscale_ecology_engine as eco_engine  # noqa: E402
from components.life.scripts import v02_personality_drift_and_model_recheck as drift_engine  # noqa: E402
from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.life.scripts import v02_world_news_carrier_lineage as wl_engine  # noqa: E402
from scripts import v02_wall_clock_soak as soak_runner  # noqa: E402

T0 = "2026-11-19T10:00:00+08:00"
T1 = "2026-11-19T11:30:00+08:00"
T2 = "2026-11-19T13:00:00+08:00"
T_LATER_13H = "2026-11-19T23:30:00+08:00"
T_LATE_NIGHT = "2026-11-19T23:15:00+08:00"

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]"
)
_AG0_FORBIDDEN_KEYS = frozenset({
    "intimacy_score",
    "affection_score",
    "love_points",
    "obedience_score",
    "debt_score",
    "compliance_score",
    "guilt_score",
    "hunger",
    "energy",
    "fatigue",
    "stamina",
})


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    snap: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        try:
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_size, int(st.st_mtime_ns))
        except OSError:
            continue
    return snap


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


class TestStaticInvariantsAndSoakRoutes(unittest.TestCase):
    def test_01_surface_counts_budget_soak_routes_and_file_hygiene(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(lived_context_bridge.MAX_LIVED_CONTEXT_CHARS, 1800)
        self.assertIn("subject_ref", phone_tool._LIFE_FORBIDDEN_IDENTITY_KEYS)

        self.assertEqual(len(soak_runner.GET_ROUTES), 24)
        self.assertIn("/api/phone/v02/ecology-growth", soak_runner.GET_ROUTES)
        self.assertIn("/api/phone/v02/world-lineage", soak_runner.GET_ROUTES)
        self.assertIn("/api/phone/v05/living-town", soak_runner.GET_ROUTES)
        self.assertIn("/api/phone/v05/inner-spark", soak_runner.GET_ROUTES)
        self.assertIn("/api/phone/v05/home-craft", soak_runner.GET_ROUTES)

        owned_files = [
            REPO_ROOT / "chiyo_bundle" / "phone_tool.py",
            REPO_ROOT / "chiyo_bundle" / "lived_context_bridge.py",
            REPO_ROOT / "components" / "life" / "scripts" / "v02_v03_daily_loop_bridge.py",
            REPO_ROOT / "scripts" / "v02_wall_clock_soak.py",
            Path(__file__).resolve(),
        ]
        for path in owned_files:
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM found in {path}")
            self.assertNotIn(b"\r\n", raw, f"CRLF found in {path}")
            text = raw.decode("utf-8")
            self.assertIsNone(_EMOJI_RE.search(text), f"Emoji found in {path}")


class TestPhoneToolHintsAndSecurity(unittest.TestCase):
    def test_01_supported_hints_cover_wave_b1_b2_and_item_ref_note(self) -> None:
        ad_hint = phone_tool._surface_supported_hint("v02_agency_depth")
        self.assertIsNotNone(ad_hint)
        for act in (
            "resolve_commitment_value_conflict",
            "snapshot_room_expectations",
            "record_absence_observation",
            "record_ecology_chain_link",
            "record_practice_standard_evolution",
        ):
            self.assertIn(act, ad_hint["subject_actions"])
            self.assertIn(act, ad_hint["core_fields"])

        rm_hint = phone_tool._surface_supported_hint("v02_runtime_migration")
        self.assertIsNotNone(rm_hint)
        for act in (
            "register_skill_release",
            "record_skill_execution_outcome",
            "record_targeted_method_patch",
            "retire_skill_release",
            "revoke_skill_source_material",
            "register_standard_revision",
            "capture_personality_drift_snapshot",
            "record_body_history_entry",
            "record_meaning_version",
            "record_lineage_branch",
            "record_layered_news_item",
            "register_external_fact_source",
            "sync_carrier_profile_and_adapt_habits",
        ):
            self.assertIn(act, rm_hint["subject_actions"])
            self.assertIn(act, rm_hint["core_fields"])
        mv_fields = " ".join(rm_hint["core_fields"]["record_meaning_version"])
        self.assertIn("item_ref", mv_fields)
        self.assertIn("item_ref", str(rm_hint.get("notes_cn") or ""))

    def test_02_partner_only_impersonation_blocked(self) -> None:
        blocked_cases = [
            ("v02_runtime_migration", {"action": "log_partner_taste_observation", "domain": "tea", "observation_cn": "x"}),
            ("v02_runtime_migration", {"action": "request_skill_migration_recheck", "method_id": "m1"}),
            ("v02_runtime_migration", {"action": "propose_meaning_note", "subject_title_cn": "杯子", "note_cn": "旧物"}),
            ("v02_agency_depth", {"action": "propose_commitment_tradeoff", "new_value_cn": "轻一点", "suggested_decision": "keep_and_adapt"}),
            ("v02_agency_depth", {"action": "suggest_ecology_spark", "theme_cn": "散步", "spark_text_cn": "去河边"}),
            ("v02_agency_depth", {"action": "acknowledge_absence_observation", "observation_id": "abs_1", "note_cn": "我收起来了"}),
            ("v02_agency_depth", {"action": "propose_rescaled_copractice", "note_cn": "换个节奏"}),
        ]
        for surface, payload in blocked_cases:
            with self.assertRaises(phone_tool.PhoneToolRefused) as ctx:
                phone_tool._prepare_life_update(
                    {"target_surface": surface, "payload": payload},
                    key="k_imp_1",
                )
            self.assertEqual(ctx.exception.reason_code, "PARTNER_IMPERSONATION_REFUSED", (surface, payload))

        self.assertIn(
            "propose_rescaled_copractice",
            phone_tool._PARTNER_ONLY_SUBACTIONS.get("v02_honest_ux", frozenset()),
        )

    def test_03_subject_ref_remains_forbidden_while_item_ref_passes_preflight(self) -> None:
        with self.assertRaises(phone_tool.PhoneToolRefused) as ctx:
            phone_tool._prepare_life_update(
                {
                    "target_surface": "v02_runtime_migration",
                    "payload": {
                        "action": "record_meaning_version",
                        "subject_ref": "obj_cup",
                        "subject_title_cn": "蓝瓷杯",
                        "current_meaning_cn": "现在更像日常老友",
                    },
                },
                key="k_mv_bad",
            )
        self.assertIn(
            ctx.exception.reason_code,
            ("CLIENT_IDENTITY_REFUSED", "IDENTITY_SPOOFING_REFUSED", "PARTNER_IMPERSONATION_REFUSED"),
        )

        surf, path, body, cap_req = phone_tool._prepare_life_update(
            {
                "target_surface": "v02_runtime_migration",
                "payload": {
                    "action": "record_meaning_version",
                    "item_ref": "obj_cup",
                    "subject_title_cn": "蓝瓷杯",
                    "current_meaning_cn": "现在更像日常老友",
                },
            },
            key="k_mv_ok",
        )
        self.assertEqual(surf, "v02_runtime_migration")
        self.assertEqual(path, "/api/phone/v02/runtime-migration")
        self.assertEqual(body.get("item_ref"), "obj_cup")
        self.assertTrue(cap_req)

    def test_04_restricted_memory_gates_all_new_runtime_migration_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-fix1-rm-gate-") as td:
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
            gated_actions = (
                "register_skill_release",
                "record_skill_execution_outcome",
                "record_targeted_method_patch",
                "retire_skill_release",
                "revoke_skill_source_material",
                "register_standard_revision",
                "capture_personality_drift_snapshot",
                "record_body_history_entry",
                "record_meaning_version",
                "record_lineage_branch",
                "record_layered_news_item",
                "register_external_fact_source",
            )
            for act in gated_actions:
                raw = phone_tool.phone_handler(
                    {
                        "action": "phone_life_update",
                        "target_surface": "v02_runtime_migration",
                        "payload": {"action": act},
                        "operation_key": f"op_gate_{act}",
                    },
                    hermes_home=str(prof),
                    platform="cli",
                    conversation_key="cli:local",
                )
                doc = json.loads(raw)
                self.assertFalse(doc.get("ok"), (act, doc))
                self.assertEqual(doc.get("reason_code"), "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED", (act, doc))


class TestLivedContextBridgeBrain(unittest.TestCase):
    def test_01_calm_zero_char_gate_and_zero_boilerplate_on_empty_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-fix1-calm-") as td:
            root = Path(td)
            bd = lived_context_bridge.measure_context_budget_breakdown(
                root, cfg={}, incoming_text="你好", now_iso=T0
            )
            self.assertTrue(bd["calm_silent"])
            self.assertEqual(bd["total_chars"], 0)
            self.assertEqual(bd["rendered_text"], "")
            self.assertEqual(_snapshot_tree(root), {})

            # Configured root with phone dir + calm default state files -> zero extra deep lines
            (root / "phone").mkdir(parents=True, exist_ok=True)
            cfg = {"day_root": str(root), "phone": {"enabled": False, "private_artifacts": True}}
            baseline = lived_context_bridge.build_lived_turn_context(root, cfg, "你好", now_iso=T0)

            _write_json(root / "state" / "v02_midscale_ecology.json", eco_engine.build_default_state())
            _write_json(root / "state" / "v02_world_news_carrier_lineage.json", wl_engine.build_default_state())
            _write_json(root / "state" / "v02_personality_drift_recheck.json", drift_engine.build_default_state())
            before = _snapshot_tree(root)
            after_calm = lived_context_bridge.build_lived_turn_context(root, cfg, "你好", now_iso=T0)
            self.assertEqual(before, _snapshot_tree(root))
            self.assertEqual(after_calm, baseline)
            self.assertNotIn("0.2成长与生态", after_calm)
            self.assertNotIn("0.2信息分层与载体", after_calm)

    def test_02_surfaces_all_new_cues_within_1800_chars_and_respects_partner_privacy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-fix1-rich-") as td:
            root = Path(td)
            (root / "phone").mkdir(parents=True, exist_ok=True)

            # 1. Skill book: PRACTICED_MASTERY + queued partner recheck + shared & private method patches
            asp.register_skill_release(
                root,
                {
                    "method_id": "watercolor_wash",
                    "tool_version": "v1",
                    "model_id": "m1",
                    "scope": "painting",
                    "title_cn": "水彩湿画晕染",
                    "attribution": "PRACTICED_MASTERY",
                    "criteria_id": "c_paint",
                    "standard_revision": "r1",
                    "held_out": True,
                    "ablation_gain_vs_bare_model": 0.25,
                    "status": "VALIDATED",
                    "operation_key": "op_sk_mastery",
                },
                now_iso=T0,
            )
            asp.request_skill_recheck(
                root,
                {
                    "method_id": "watercolor_wash",
                    "note_cn": "换模型后请复验一下",
                    "operation_key": "op_sk_recheck",
                },
                now_iso=T0,
            )
            asp.record_targeted_method_patch(
                root,
                {
                    "method_id": "private_diary_method",
                    "scope": "notes",
                    "failure_symptom_cn": "SECRET_PATCH_SYMPTOM",
                    "root_cause_hypothesis": "SECRET_PATCH_CAUSE",
                    "method_change_cn": "SECRET_PATCH_CHANGE",
                    "shared": False,
                    "operation_key": "op_patch_priv",
                },
                now_iso=T0,
            )
            asp.record_targeted_method_patch(
                root,
                {
                    "method_id": "public_tea_method",
                    "scope": "tea",
                    "failure_symptom_cn": "水温偏高",
                    "root_cause_hypothesis": "沸水直冲",
                    "method_change_cn": "先晾半分钟再冲",
                    "shared": True,
                    "operation_key": "op_patch_pub",
                },
                now_iso=T0,
            )

            # 2. Personality drift: partner observation + open model recheck pack
            drift_engine.record_taste_or_standard_shift(
                root,
                {
                    "domain": "tea",
                    "observation_cn": "最近更偏爱清淡乌龙",
                    "shared": True,
                    "operation_key": "op_obs_1",
                },
                actor_side="partner",
                now_iso=T0,
            )
            drift_engine.create_model_preference_recheck_pack(
                root,
                {
                    "from_model_id": "m1",
                    "to_model_id": "m2",
                    "operation_key": "op_rpack_1",
                },
                actor_side="partner",
                now_iso=T0,
            )

            # 3. Agency depth Wave A: open inquiry with partner clue + pending partner tradeoff
            ad_engine.apply_agency_depth_mutation(
                root,
                {
                    "action": "open_bounded_inquiry",
                    "question": "窗台绿萝为什么发黄",
                    "shared": True,
                    "operation_key": "op_inq_open",
                },
                actor_side="subject",
                now_iso=T0,
            )
            ad_engine.apply_agency_depth_mutation(
                root,
                {
                    "action": "share_inquiry_clue",
                    "inquiry_id": "inq_0001",
                    "clue_text": "这两天午后西晒很强",
                    "operation_key": "op_inq_clue",
                },
                actor_side="partner",
                now_iso=T0,
            )
            ad_engine.apply_agency_depth_mutation(
                root,
                {
                    "action": "propose_commitment_tradeoff",
                    "resp_title": "每周浇花",
                    "new_value_cn": "改成隔三天看土干湿",
                    "suggested_decision": "keep_and_adapt",
                    "operation_key": "op_trd_1",
                },
                actor_side="partner",
                now_iso=T0,
            )

            # 4. Wave B1 Ecology: shared + private absence observations, partner spark, chain
            eco_engine.record_absence_observation(
                root,
                {
                    "observation_id": "abs_pub_1",
                    "item_id": "blue_mug",
                    "title_cn": "蓝瓷杯",
                    "kind": "MOVED_OBJECT",
                    "expected_place_cn": "书桌",
                    "observed_place_cn": "茶盘",
                    "shared": True,
                    "operation_key": "op_abs_pub",
                },
                now_iso=T0,
            )
            eco_engine.acknowledge_absence_observation(
                root,
                {
                    "observation_id": "abs_pub_1",
                    "note_cn": "早上顺手端去洗了",
                    "who_moved": "PARTNER",
                    "operation_key": "op_abs_ack",
                },
                actor_side="partner",
                now_iso=T0,
            )
            eco_engine.record_absence_observation(
                root,
                {
                    "observation_id": "abs_priv_1",
                    "item_id": "secret_box",
                    "title_cn": "SECRET_PRIVATE_BOX",
                    "kind": "MISSING_OBJECT",
                    "expected_place_cn": "抽屉",
                    "shared": False,
                    "operation_key": "op_abs_priv",
                },
                now_iso=T0,
            )
            eco_engine.apply_midscale_ecology_mutation(
                root,
                {
                    "action": "suggest_ecology_spark",
                    "theme_cn": "海堤散步",
                    "spark_text_cn": "傍晚退潮去看礁石",
                    "operation_key": "op_spark_1",
                },
                actor_side="partner",
                now_iso=T0,
            )
            eco_engine.record_ecology_chain_link(
                root,
                {
                    "chain_id": "ch_1",
                    "theme_cn": "海风手账",
                    "step_kind": "reading",
                    "ref_id": "book:harbor",
                    "title_cn": "读海堤风物志",
                    "shared": True,
                    "operation_key": "op_ch_1",
                },
                now_iso=T0,
            )

            # 5. Wave B2 World-Lineage: 3-layer news + restricted carrier + meaning note + private meaning version
            wl_engine.sync_carrier_profile_and_adapt_habits(
                root,
                {
                    "carrier_kind": "headless_cli",
                    "carrier_label_cn": "纯文字无界面",
                    "visual_preview": False,
                    "audio_output": False,
                    "desk_stage": False,
                    "camera_input": False,
                    "operation_key": "op_car_1",
                },
                actor_side="partner",
                now_iso=T0,
            )
            wl_engine.record_layered_news_item(
                root,
                {
                    "layer": "LOCAL_WORLD_NEWS",
                    "headline_cn": "海堤灯塔换了暖黄新灯泡",
                    "shared": True,
                    "operation_key": "op_n_local",
                },
                actor_side="subject",
                now_iso=T0,
            )
            wl_engine.record_layered_news_item(
                root,
                {
                    "layer": "EXTERNAL_REAL_FACT",
                    "headline_cn": "现实世界下周降温预报",
                    "source_ref": "https://weather.example.org/forecast",
                    "shared": True,
                    "operation_key": "op_n_ext",
                },
                actor_side="partner",
                now_iso=T0,
            )
            wl_engine.record_layered_news_item(
                root,
                {
                    "layer": "UNVERIFIED_HEARSAY",
                    "headline_cn": "听说街角老书店要进新画册",
                    "shared": True,
                    "operation_key": "op_n_hear",
                },
                actor_side="partner",
                now_iso=T0,
            )
            wl_engine.propose_meaning_note(
                root,
                {
                    "subject_title_cn": "旧铜铃",
                    "note_cn": "搬家第一天挂在窗边",
                    "operation_key": "op_mn_1",
                },
                actor_side="partner",
                now_iso=T0,
            )
            wl_engine.record_meaning_version(
                root,
                {
                    "subject_ref": "priv_diary_item",
                    "subject_title_cn": "SECRET_PRIVATE_MEANING",
                    "current_meaning_cn": "SECRET_MEANING_BODY",
                    "shared": False,
                    "operation_key": "op_mv_priv",
                },
                actor_side="subject",
                now_iso=T0,
            )

            before = _snapshot_tree(root)

            # Subject perspective: sees everything within <= 1800 chars, zero disk writes
            cfg_subject = {
                "day_root": str(root),
                "phone": {"enabled": False, "private_artifacts": True},
            }
            bd_sub = lived_context_bridge.measure_context_budget_breakdown(
                root, cfg=cfg_subject, incoming_text="聊聊今天的见闻", now_iso=T0
            )
            self.assertEqual(before, _snapshot_tree(root), "Subject context read must be 100% zero-write")
            self.assertTrue(bd_sub["within_budget"])
            self.assertLessEqual(bd_sub["total_chars"], 1800)
            txt_sub = bd_sub["rendered_text"]
            self.assertIn("watercolor_wash", txt_sub)
            self.assertIn("水彩湿画晕染", txt_sub)
            self.assertIn("蓝瓷杯", txt_sub)
            self.assertIn("早上顺手端去洗了", txt_sub)
            self.assertIn("每周浇花", txt_sub)
            self.assertIn("NO_VISUAL_PREVIEW", txt_sub)
            self.assertIn("NO_AUDIO_OUTPUT", txt_sub)
            self.assertIn("[小镇虚构动态]", txt_sub)
            self.assertIn("[外部现实信息·独立于小镇勿混淆]", txt_sub)
            self.assertIn("[口头听闻·未核证]", txt_sub)
            self.assertIn("旧铜铃", txt_sub)

            # Partner / restricted memory perspective: strips ALL shared==False entries, zero writes
            cfg_partner = {
                "day_root": str(root),
                "memory": True,
                "memory_tool_policy": "restricted",
                "phone": {"enabled": False, "private_artifacts": False},
            }
            bd_partner = lived_context_bridge.measure_context_budget_breakdown(
                root, cfg=cfg_partner, incoming_text="聊聊今天的见闻", now_iso=T0
            )
            self.assertEqual(before, _snapshot_tree(root), "Partner context read must be 100% zero-write")
            self.assertTrue(bd_partner["within_budget"])
            self.assertLessEqual(bd_partner["total_chars"], 1800)
            txt_partner = bd_partner["rendered_text"]
            self.assertNotIn("SECRET_PRIVATE_BOX", txt_partner)
            self.assertNotIn("SECRET_PRIVATE_MEANING", txt_partner)
            self.assertNotIn("SECRET_MEANING_BODY", txt_partner)
            self.assertNotIn("private_diary_method", txt_partner)
            # Shared cues still surface cleanly for partner
            self.assertIn("蓝瓷杯", txt_partner)
            self.assertIn("[小镇虚构动态]", txt_partner)
            self.assertIn("[外部现实信息·独立于小镇勿混淆]", txt_partner)
            self.assertIn("NO_VISUAL_PREVIEW", txt_partner)


class TestDailyLoopAutonomousProducers(unittest.TestCase):
    def test_01_empty_root_zero_write_and_va_gate_blocks_producer_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-fix1-va-") as td:
            root = Path(td)
            before = _snapshot_tree(root)
            collected = loop_bridge.collect_v02_v03_loop_candidates(root, now_iso=T0)
            self.assertEqual(before, _snapshot_tree(root))
            self.assertEqual(collected["candidates"], [])

            # Unauthorized partner actor -> REFUSED, zero autonomous producer writes
            refused = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "actor_side": "partner",
                    "effect_state": "APPLIED",
                    "action_kind": "REST",
                    "receipt_ref": "rcpt:bad_partner",
                    "operation_key": "op_bad_partner",
                },
                now_iso=T0,
            )
            self.assertFalse(refused["ok"])
            self.assertEqual(before, _snapshot_tree(root))

            # effect_state == UNKNOWN -> DEFERRED_UNKNOWN, zero autonomous producer writes
            deferred = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "actor_side": "subject",
                    "effect_state": "UNKNOWN",
                    "action_kind": "REST",
                    "receipt_ref": "rcpt:unk_1",
                    "operation_key": "op_unk_1",
                },
                now_iso=T0,
            )
            self.assertFalse(deferred["settled"])
            self.assertEqual(before, _snapshot_tree(root))

    def test_02_multi_round_execution_auto_populates_all_producers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-fix1-multi-") as td:
            root = Path(td)

            # Seed world_state.json with real objects so F-175 room expectations auto-snapshots
            _write_json(
                root / "world" / "world_state.json",
                {
                    "room_objects": [
                        {"id": "ceramic_teapot", "name_cn": "白瓷茶壶", "location": "home_desk", "present": True},
                        {"id": "linen_notebook", "name_cn": "亚麻手账本", "location": "home_desk", "present": True},
                    ]
                },
            )
            # Seed workbench tray & bookstore bookmark for real settled craft & reading rounds
            _write_json(
                root / "phone" / "home_workbench.json",
                {
                    "schema": "nyairo.phone.home_workbench.v1",
                    "tray": [{"note_id": "tray_1", "title": "海堤水彩草稿", "status": "OPEN"}],
                },
            )
            _write_json(
                root / "phone" / "outing_bench_bookstore.json",
                {
                    "schema_version": 1,
                    "revision": 1,
                    "updated_at": T0,
                    "bench_visits": [],
                    "window_bookmarks": [
                        {
                            "bookmark_id": "wbk_tide_almanac",
                            "slot_index": 0,
                            "book_code": "tide_almanac_notes",
                            "title": "潮汐与岸边小历",
                            "note": "看到第四章",
                            "shared": True,
                            "saved_at": T0,
                            "updated_at": T0,
                        }
                    ],
                    "partner_window_note": None,
                },
            )

            # Round 1: Settled reading round -> auto skill book (reading), auto drift snapshot #1,
            # auto room expectations baseline, auto ecology chain link ("reading")
            r1 = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "actor_side": "subject",
                    "effect_state": "APPLIED",
                    "action_kind": "FOLLOW_BOOKSTORE_BOOKMARK",
                    "action_family": "follow_bookstore_bookmark",
                    "book_code": "tide_almanac_notes",
                    "title": "翻看《潮汐与岸边小历》",
                    "receipt_ref": "rcpt:r1_read",
                    "operation_key": "op_r1_read",
                },
                now_iso=T0,
            )
            self.assertTrue(r1["settled"], r1)

            # Verify room expectations auto-baseline captured
            eco_view_1 = eco_engine.read_midscale_ecology_view(root, has_subject_capability=True, now_iso=T0)
            self.assertGreaterEqual(eco_view_1["room_expectations_count"], 2)
            # Verify ecology chain link recorded
            self.assertGreaterEqual(len(eco_view_1["ecology_chains"]), 1)
            self.assertIn("reading", eco_view_1["ecology_chains"][0]["step_kinds_path"])

            # Verify skill book auto-release + outcome
            sb_1 = asp.project_personal_skill_book_zero_write(root, has_subject_capability=True, now_iso=T0)
            read_entries = [e for e in sb_1["entries"] if e["method_id"] == "reading_bookmark_flow"]
            self.assertEqual(len(read_entries), 1)
            self.assertGreaterEqual(read_entries[0]["method_efficacy"]["successes"], 1)

            # Verify drift snapshot #1 captured
            dr_1 = drift_engine.read_personality_drift_and_recheck_view(root, has_subject_capability=True, now_iso=T0)
            self.assertEqual(dr_1["snapshot_count"], 1)

            # Round 2: Settled inquiry with step_budget=2 -> step 1 records evidence, step 2 auto-concludes!
            ad_engine.apply_agency_depth_mutation(
                root,
                {
                    "action": "open_bounded_inquiry",
                    "question": "茶壶壶嘴为什么会渗水",
                    "topic_tag": "teapot",
                    "candidate_explanations": [
                        {"summary_cn": "壶盖密封圈老化导致倒水回流", "stance": "plausible"}
                    ],
                    "step_budget": 2,
                    "shared": True,
                    "operation_key": "op_inq_open_2",
                },
                actor_side="subject",
                now_iso=T0,
            )
            c_step1 = loop_bridge.collect_v02_v03_loop_candidates(root, now_iso=T1)
            inq_ref = next(
                d["source_ref"]
                for d in c_step1["descriptors"].values()
                if d.get("action_family") == "advance_bounded_inquiry"
            )
            ev_step1 = loop_bridge.v02_v03_round_event_for_selected_candidate(
                c_step1, inq_ref, decision_id="dec_inq_1"
            )
            self.assertNotIn("conclude", ev_step1)
            s_step1 = loop_bridge.settle_v02_v03_round_consequences(root, ev_step1, now_iso=T1)
            self.assertTrue(s_step1["settled"], s_step1)
            self.assertEqual(s_step1["details"]["bounded_inquiry"]["status"], "INVESTIGATING")

            # Step 2 hits final step_budget -> auto-concludes into belief_revisions + S03 claim!
            c_step2 = loop_bridge.collect_v02_v03_loop_candidates(root, now_iso=T2)
            ev_step2 = loop_bridge.v02_v03_round_event_for_selected_candidate(
                c_step2, inq_ref, decision_id="dec_inq_2"
            )
            self.assertTrue(ev_step2.get("conclude"), ev_step2)
            self.assertIn("壶盖密封圈老化", str(ev_step2.get("conclusion") or ""))
            s_step2 = loop_bridge.settle_v02_v03_round_consequences(root, ev_step2, now_iso=T2)
            self.assertTrue(s_step2["settled"], s_step2)
            self.assertEqual(s_step2["details"]["bounded_inquiry"]["status"], "CONCLUDED")
            self.assertIn("v02_agency_depth.belief_revisions", s_step2["slices_updated"])
            ad_proj = ad_engine.project_v02_agency_depth(root, has_subject_capability=True, now_iso=T2)
            self.assertGreaterEqual(len(ad_proj["belief_revisions"]), 1)
            self.assertIn("壶盖密封圈老化", ad_proj["belief_revisions"][0]["revised_belief_cn"])

            # Drift snapshot throttled within 12h (still 1 snapshot at T2)
            dr_2 = drift_engine.read_personality_drift_and_recheck_view(root, has_subject_capability=True, now_iso=T2)
            self.assertEqual(dr_2["snapshot_count"], 1)

            # Round 3: Settled REST round > 12h later + second_order_constrained_candidates ->
            # auto body history + auto drift snapshot #2 + auto rule_enforcements!
            r3 = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "actor_side": "subject",
                    "effect_state": "APPLIED",
                    "action_kind": "REST",
                    "care_event_kind": "bed_rest_block",
                    "quiet_note": "靠着靠垫歇了半小时",
                    "receipt_ref": "rcpt:r3_rest",
                    "operation_key": "op_r3_rest",
                    "second_order_constrained_candidates": [
                        {
                            "rule_id": "rule_late_night",
                            "rule_title": "深夜不开重活",
                            "candidate_ref": "workbench:heavy_draft",
                            "candidate_title": "重画整页分镜",
                            "reason_cn": "已过深夜窗口，延后重活",
                        }
                    ],
                },
                now_iso=T_LATER_13H,
            )
            self.assertTrue(r3["settled"], r3)
            self.assertIn("v02_agency_depth.rule_enforcements", r3["slices_updated"])

            # Verify rule_enforcements persisted
            ad_proj_3 = ad_engine.project_v02_agency_depth(root, has_subject_capability=True, now_iso=T_LATER_13H)
            self.assertTrue(
                any(e.get("rule_id") == "rule_late_night" for e in ad_proj_3.get("rule_enforcements") or []),
                ad_proj_3.get("rule_enforcements"),
            )

            # Verify body history auto-populated
            wl_view = wl_engine.read_world_news_carrier_lineage_view(
                root, has_subject_capability=True, now_iso=T_LATER_13H
            )
            self.assertGreaterEqual(len(wl_view["body_history"]), 1)

            # Verify drift snapshot #2 captured after >= 12h elapsed
            dr_3 = drift_engine.read_personality_drift_and_recheck_view(
                root, has_subject_capability=True, now_iso=T_LATER_13H
            )
            self.assertEqual(dr_3["snapshot_count"], 2)

            # Verify ecology chain grew with inquiry step
            eco_view_3 = eco_engine.read_midscale_ecology_view(
                root, has_subject_capability=True, now_iso=T_LATER_13H
            )
            self.assertIn("inquiry", eco_view_3["ecology_chains"][0]["step_kinds_path"])

    def test_03_f175_f176_candidates_emitted_ag0_compliant_and_second_order_details(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-fix1-cands-") as td:
            root = Path(td)
            # Seed baseline + move object so F-175 detects real unacknowledged absence
            _write_json(
                root / "world" / "world_state.json",
                {
                    "room_objects": [
                        {"id": "blue_cup", "name_cn": "蓝瓷杯", "location": "home_desk", "present": True},
                    ]
                },
            )
            eco_engine.snapshot_room_expectations(root, {"operation_key": "op_snap_base"}, now_iso=T0)
            _write_json(
                root / "world" / "world_state.json",
                {
                    "room_objects": [
                        {"id": "blue_cup", "name_cn": "蓝瓷杯", "location": "porch", "present": True},
                    ]
                },
            )

            # Seed real paused autobiographical episode so F-176 derives spark opportunity
            asp.record_autobiographical_episode(
                root,
                {
                    "operation_key": "op_ep_paused_1",
                    "episode_id": "ep_paused_harbor",
                    "period_id": "2026-11",
                    "domain": "painting",
                    "title": "海堤雾色小样",
                    "summary": "画了一半先搁下",
                    "outcome": "paused",
                    "visibility": "shared",
                    "occurred_at": T0,
                    "source_ref": "work:harbor_sketch",
                },
                now_iso=T0,
            )

            # Seed endorsed late-night second-order rule + heavy workbench sprint candidate
            from components.phone import workbench_curio_view

            rl_state, _ = ad_engine.apply_agency_depth_mutation(
                root,
                {
                    "action": "manage_second_order_rule",
                    "rule_op": "propose",
                    "title": "晚间不安排高强度重活冲刺",
                    "trigger_window": "22:00-07:00",
                    "shared": True,
                    "operation_key": "op_rl_prop",
                },
                actor_side="subject",
                now_iso=T0,
            )
            created_rule_id = rl_state["second_order_rules"][-1]["rule_id"]
            ad_engine.apply_agency_depth_mutation(
                root,
                {
                    "action": "manage_second_order_rule",
                    "rule_op": "endorse",
                    "rule_id": created_rule_id,
                    "operation_key": "op_rl_end",
                },
                actor_side="subject",
                now_iso=T0,
            )
            t_state = workbench_curio_view.empty_tray_state()
            t_state, _ = workbench_curio_view.set_tray(
                t_state,
                {
                    "images": [],
                    "materials": [],
                    "work": {
                        "artifact_id": "artifact:sprint_chapter",
                        "baseline_version_id": "artifact-version:v3",
                    },
                    "note": "高强度长篇重活冲刺",
                },
                T0,
            )
            workbench_curio_view.write_tray_state(root, t_state)

            before = _snapshot_tree(root)
            collected = loop_bridge.collect_v02_v03_loop_candidates(root, now_iso=T_LATE_NIGHT)
            self.assertEqual(before, _snapshot_tree(root), "collect must stay 100% zero-write")

            descs = list(collected["descriptors"].values())
            abs_descs = [d for d in descs if d.get("action_family") == "inspect_room_absence"]
            spark_descs = [d for d in descs if d.get("action_family") == "explore_autobiography_spark"]
            self.assertEqual(len(abs_descs), 1, descs)
            self.assertEqual(len(spark_descs), 1, descs)

            # Verify AG0 compliance (zero forbidden keys, structured source_ref & provenance_refs)
            for cand in collected["candidates"]:
                self.assertTrue(set(cand.keys()).isdisjoint(_AG0_FORBIDDEN_KEYS), cand)
                self.assertIn(":", str(cand.get("source_ref") or ""))
                self.assertTrue(cand.get("provenance_refs"))
            for desc in (abs_descs[0], spark_descs[0]):
                self.assertTrue(set(desc.keys()).isdisjoint(_AG0_FORBIDDEN_KEYS), desc)

            # Verify second_order_constrained_candidates exposes all required receipt fields
            constrained = collected.get("second_order_constrained_candidates") or []
            self.assertGreaterEqual(len(constrained), 1, collected)
            for row in constrained:
                self.assertTrue(row.get("rule_id"), row)
                self.assertTrue(row.get("rule_title"), row)
                self.assertTrue(row.get("candidate_ref"), row)
                self.assertTrue(row.get("candidate_title"), row)
                self.assertTrue(row.get("reason_cn"), row)

    def test_04_inquiry_auto_concludes_at_two_evidence_notes_plus_supported_explanation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-fix1-inq2-") as td:
            root = Path(td)
            ad_engine.apply_agency_depth_mutation(
                root,
                {
                    "action": "open_bounded_inquiry",
                    "question": "木桌抽屉为什么总卡住",
                    "topic_tag": "desk_drawer",
                    "candidate_explanations": [
                        {"summary_cn": "梅雨季木轨受潮膨胀", "stance": "supported"}
                    ],
                    "step_budget": 6,
                    "shared": True,
                    "operation_key": "op_inq2_open",
                },
                actor_side="subject",
                now_iso=T0,
            )
            for idx in (1, 2):
                ad_engine.apply_agency_depth_mutation(
                    root,
                    {
                        "action": "record_inquiry_evidence",
                        "inquiry_id": "inq_0001",
                        "note": f"摸到木滑轨潮湿微胀 第{idx}次观察",
                        "source_ref": f"obs:drawer:{idx}",
                        "operation_key": f"op_inq2_ev_{idx}",
                    },
                    actor_side="subject",
                    now_iso=T0,
                )

            settled = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "actor_side": "subject",
                    "effect_state": "APPLIED",
                    "action_kind": "ADVANCE_BOUNDED_INQUIRY",
                    "inquiry_id": "inq_0001",
                    "receipt_ref": "rcpt:inq2_auto",
                    "operation_key": "op_inq2_auto",
                },
                now_iso=T1,
            )
            self.assertTrue(settled["settled"], settled)
            self.assertEqual(settled["details"]["bounded_inquiry"]["status"], "CONCLUDED")
            self.assertIn("梅雨季木轨受潮膨胀", settled["details"]["bounded_inquiry"]["conclusion"])
            proj = ad_engine.project_v02_agency_depth(root, has_subject_capability=True, now_iso=T1)
            self.assertEqual(len(proj["belief_revisions"]), 1)


if __name__ == "__main__":
    unittest.main()

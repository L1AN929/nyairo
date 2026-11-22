#!/usr/bin/env python3
"""Deterministic verification suite for v0.2 Final Closure (Brain, Tool Surface & Daily Loop).

Verifies:
1. Frozen invariants & file hygiene:
   - ``len(phone_tool.LIFE_UPDATE_SURFACES) == 37``
   - ``len(plugins.chiyo._PHONE_AND_WORLD_GUIDANCE) == 1199``
   - ``lived_context_bridge.MAX_LIVED_CONTEXT_CHARS == 1800``
   - Pure LF, UTF-8 no BOM, zero ``unicode.So`` emojis, neutral ``TA``, sealed anchors untouched.
2. ``chiyo_bundle/phone_tool.py``:
   - Partner-only anti-impersonation & anti-self-termination blocks on ``v02_runtime_migration``
     (retirement prepare/confirm/cancel, proactive config/ack-cancel, propose_rescaled_copractice).
   - Updated ``_SURFACE_SUPPORTED_HINTS`` for ``v02_crossmodal`` (``ingest_audio_note``) and
     ``v02_runtime_migration`` (``enqueue_proactive_message``, ``record_self_claim``,
     ``observe_external_edit``, ``rebuild_projection_from_canonical``, ``confirm_rescaled_copractice``).
   - ``confirm_rescaled_copractice`` cleanly delegates to ``/api/phone/v02/honest-ux`` while
     keeping ``len(LIFE_UPDATE_SURFACES) == 37``.
   - Restricted-memory gating blocks all new subject writes when ``private_artifacts`` is False.
3. ``chiyo_bundle/lived_context_bridge.py``:
   - Calm empty/unconfigured root returns ``""`` (``calm_silent == True``, ``total_chars == 0``).
   - Calm configured default files emit zero extra v02 deep/growth lines.
   - Surfaces ``F-025`` audio notes (respecting ``USER_SUPPLIED`` vs ``UNAVAILABLE_LOCAL_ASR`` and
     strict ``shared`` redaction), ``F-189`` pending rescaled co-practice, ``F-076`` period
     ``self_claim``, ``F-113`` proactive outbound (only when enabled or non-empty queue), and
     ``F-148`` retirement status (only when != ``ACTIVE``), all strictly ``<= 1800`` chars and
     100% zero-write.
4. ``components/life/scripts/v02_v03_daily_loop_bridge.py``:
   - ``F-148``: ``RETIRED_ARCHIVE_SEALED`` automatically forces ``DORMANT_NO_COGNITION`` with
     ``retired_sealed == True``, zero candidates, and 100% zero writes in ``run_v02_v03_daily_tick``.
   - ``F-189``: pending partner rescaled co-practice proposal emits a bounded AG0 ``CandidateRecord``
     zero-write, selects cleanly, and settles through ``v02_honest_ux_view``.
   - ``F-187``: ``settle_v02_v03_round_consequences`` automatically records a bounded workspace audit
     when runtime migration state exists (zero writes on unauthorized/UNKNOWN rounds, zero crashes
     when runtime migration state is absent).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "chiyo_bundle",
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from chiyo_bundle import lived_context_bridge  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import v02_autobiography_skill_provenance as asp  # noqa: E402
from components.life.scripts import v02_representation_lease_forget as rlf  # noqa: E402
from components.life.scripts import v02_runtime_governance_migration as rm_engine  # noqa: E402
from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.phone import v02_crossmodal_view as cm_view  # noqa: E402
from components.phone import v02_honest_ux_view as hux_view  # noqa: E402

T0 = "2026-11-19T10:00:00+08:00"
T1 = "2026-11-19T11:30:00+08:00"
T_PREV_HOST = "2026-11-18T10:00:00+08:00"

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
            st = p.lstat()
            snap[str(p.relative_to(root))] = (int(st.st_size), int(st.st_mtime_ns))
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


def _load_chiyo_plugin_guidance_len() -> int:
    plugin_path = REPO_ROOT / "plugins" / "chiyo" / "__init__.py"
    if "agent.context_compressor" not in sys.modules:
        agent_pkg = types.ModuleType("agent")
        cc_mod = types.ModuleType("agent.context_compressor")

        class _StubCompressor:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs

            def on_session_reset(self) -> None:
                pass

            def get_status(self) -> dict[str, Any]:
                return {}

        cc_mod.ContextCompressor = _StubCompressor  # type: ignore[attr-defined]
        sys.modules["agent"] = agent_pkg
        sys.modules["agent.context_compressor"] = cc_mod
    if "hermes_constants" not in sys.modules:
        hc_mod = types.ModuleType("hermes_constants")
        hc_mod.get_hermes_home = lambda: REPO_ROOT  # type: ignore[attr-defined]
        sys.modules["hermes_constants"] = hc_mod
    spec = importlib.util.spec_from_file_location("chiyo_plugin_v02_closure_check", plugin_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return len(mod._PHONE_AND_WORLD_GUIDANCE)


class TestFrozenInvariantsAndHygiene(unittest.TestCase):
    def test_01_frozen_invariants_and_owned_file_hygiene(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(lived_context_bridge.MAX_LIVED_CONTEXT_CHARS, 1800)
        self.assertEqual(_load_chiyo_plugin_guidance_len(), 1199)
        self.assertNotIn("v02_runtime_migration", phone_tool.PRIVATE_ARTIFACT_ACTIONS)
        self.assertNotIn("v02_runtime_migration", phone_tool.LIFE_UPDATE_PRIVATE_SURFACES)

        anchors = loop_bridge.verify_frozen_anchors(REPO_ROOT)
        self.assertTrue(anchors["ok"], anchors)

        owned_files = (
            REPO_ROOT / "chiyo_bundle" / "phone_tool.py",
            REPO_ROOT / "chiyo_bundle" / "lived_context_bridge.py",
            REPO_ROOT / "components" / "life" / "scripts" / "v02_v03_daily_loop_bridge.py",
            Path(__file__).resolve(),
        )
        for fpath in owned_files:
            raw = fpath.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM found in {fpath}")
            self.assertNotIn(b"\r\n", raw, f"CRLF found in {fpath}")
            text = raw.decode("utf-8")
            so_chars = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(so_chars, [], f"unicode.So symbol(s) found in {fpath}: {so_chars[:5]}")


class TestPhoneToolFinalClosure(unittest.TestCase):
    def test_01_partner_only_retirement_and_proactive_actions_blocked(self) -> None:
        blocked_cases = (
            ("v02_runtime_migration", {"action": "prepare_dignified_retirement", "reason_cn": "测试"}),
            ("v02_runtime_migration", {"action": "confirm_dignified_retirement", "confirmation_phrase": "确认温和封存归档"}),
            ("v02_runtime_migration", {"action": "cancel_dignified_retirement"}),
            ("v02_runtime_migration", {"action": "configure_proactive_outbound", "enabled": True}),
            ("v02_runtime_migration", {"action": "acknowledge_or_cancel_proactive_message", "message_id": "m1", "decision": "acknowledge"}),
            ("v02_runtime_migration", {"action": "propose_rescaled_copractice", "title": "轻接力"}),
        )
        for surface, payload in blocked_cases:
            with self.assertRaises(phone_tool.PhoneToolRefused) as ctx:
                phone_tool._prepare_life_update(
                    {"target_surface": surface, "payload": payload},
                    key="op_block_test",
                )
            self.assertEqual(
                ctx.exception.reason_code,
                "PARTNER_IMPERSONATION_REFUSED",
                (surface, payload),
            )

    def test_02_supported_hints_and_rescaled_copractice_routing(self) -> None:
        cm_hint = phone_tool._surface_supported_hint("v02_crossmodal")
        self.assertIsNotNone(cm_hint)
        self.assertIn("ingest_audio_note", cm_hint["subject_actions"])
        self.assertIn("ingest_audio_note", cm_hint["core_fields"])

        rm_hint = phone_tool._surface_supported_hint("v02_runtime_migration")
        self.assertIsNotNone(rm_hint)
        for act in (
            "enqueue_proactive_message",
            "record_self_claim",
            "observe_external_edit",
            "rebuild_projection_from_canonical",
            "confirm_rescaled_copractice",
        ):
            self.assertIn(act, rm_hint["subject_actions"])
            self.assertIn(act, rm_hint["core_fields"])

        # confirm_rescaled_copractice invoked via v02_runtime_migration routes cleanly to /api/phone/v02/honest-ux
        surf, path, body, cap_req = phone_tool._prepare_life_update(
            {
                "target_surface": "v02_runtime_migration",
                "payload": {
                    "action": "confirm_rescaled_copractice",
                    "practice_id": "cop_1",
                    "scale_mode": "light_relay",
                    "confirmed": True,
                },
            },
            key="op_cop_route_1",
        )
        self.assertEqual(surf, "v02_runtime_migration")
        self.assertEqual(path, "/api/phone/v02/honest-ux")
        self.assertEqual(body["operation_key"], "op_cop_route_1")
        self.assertTrue(cap_req)

        # Standard runtime_migration & crossmodal actions keep their native endpoints
        for target_surf, payload, expected_path in (
            (
                "v02_crossmodal",
                {"action": "ingest_audio_note", "title_cn": "窗外雨声", "duration_seconds": 12.5, "audio_format": "wav"},
                "/api/phone/v02/crossmodal",
            ),
            (
                "v02_runtime_migration",
                {"action": "enqueue_proactive_message", "message_cn": "雨停了记得收伞", "reason_cn": "顺手提醒"},
                "/api/phone/v02/runtime-migration",
            ),
            (
                "v02_runtime_migration",
                {"action": "record_self_claim", "period_id": "2026-11", "claim_key": "pace", "domain": "daily", "statement": "更喜欢慢节奏午后"},
                "/api/phone/v02/runtime-migration",
            ),
            (
                "v02_runtime_migration",
                {"action": "observe_external_edit", "artifact_id": "art_1"},
                "/api/phone/v02/runtime-migration",
            ),
            (
                "v02_runtime_migration",
                {"action": "rebuild_projection_from_canonical", "artifact_id": "art_1"},
                "/api/phone/v02/runtime-migration",
            ),
        ):
            s_out, p_out, b_out, c_req = phone_tool._prepare_life_update(
                {"target_surface": target_surf, "payload": payload},
                key=f"op_{payload['action']}",
            )
            self.assertEqual(s_out, target_surf)
            self.assertEqual(p_out, expected_path)
            self.assertEqual(b_out["operation_key"], f"op_{payload['action']}")
            self.assertTrue(c_req)

    def test_03_restricted_memory_gates_all_new_subject_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-closure-rm-gate-") as td:
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
            gated_cases = (
                ("v02_crossmodal", {"action": "ingest_audio_note", "title_cn": "私密录音"}),
                ("v02_runtime_migration", {"action": "enqueue_proactive_message", "message_cn": "问候"}),
                ("v02_runtime_migration", {"action": "record_self_claim", "period_id": "2026-11", "statement": "自述"}),
                ("v02_runtime_migration", {"action": "observe_external_edit", "artifact_id": "a1"}),
                ("v02_runtime_migration", {"action": "rebuild_projection_from_canonical", "artifact_id": "a1"}),
                ("v02_runtime_migration", {"action": "confirm_rescaled_copractice", "practice_id": "cop_1"}),
                # Alias key ("op") must also remain gated under restricted memory
                ("v02_runtime_migration", {"op": "record_self_claim", "period_id": "2026-11", "statement": "别名尝试"}),
            )
            for surf, payload in gated_cases:
                raw = phone_tool.phone_handler(
                    {
                        "action": "phone_life_update",
                        "target_surface": surf,
                        "payload": payload,
                        "operation_key": f"op_gate_{surf}_{payload.get('action') or payload.get('op')}",
                    },
                    hermes_home=str(prof),
                    platform="cli",
                    conversation_key="cli:local",
                )
                doc = json.loads(raw)
                self.assertFalse(doc.get("ok"), (surf, payload, doc))
                self.assertEqual(doc.get("reason_code"), "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED", (surf, payload, doc))


class TestLivedContextBridgeFinalClosure(unittest.TestCase):
    def test_01_calm_silence_on_empty_and_default_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-closure-calm-") as td:
            root = Path(td)
            bd = lived_context_bridge.measure_context_budget_breakdown(
                root, cfg={}, incoming_text="你好", now_iso=T0
            )
            self.assertTrue(bd["calm_silent"])
            self.assertEqual(bd["total_chars"], 0)
            self.assertEqual(bd["rendered_text"], "")
            self.assertEqual(_snapshot_tree(root), {})

            # Configured root with calm default closure fields -> zero extra deep/growth lines
            (root / "phone").mkdir(parents=True, exist_ok=True)
            cfg = {"day_root": str(root), "phone": {"enabled": False, "private_artifacts": True}}
            baseline = lived_context_bridge.build_lived_turn_context(root, cfg, "你好", now_iso=T0)

            _write_json(
                root / "phone" / "v02_crossmodal.json",
                {
                    "schema": "nyairo.v02.crossmodal.v1",
                    "audio_notes": [],
                    "palette_studies": [],
                    "reading_uncertainty_marks": [],
                },
            )
            _write_json(
                root / "phone" / "v02_runtime_migration.json",
                {
                    "schema": "nyairo.v02.runtime_migration.v1",
                    "mode_config": {"mode": "NORMAL", "budget_scale_pct": 100},
                    "retirement": {"status": "ACTIVE", "prepared_at": None, "sealed_at": None, "farewell_note_cn": ""},
                    "proactive_outbound": {
                        "enabled": False,
                        "quiet_start_hm": "22:30",
                        "quiet_end_hm": "07:30",
                        "daily_cap": 2,
                        "min_cooldown_minutes": 180,
                        "sent_today_count": 0,
                        "pending_queue": [],
                    },
                },
            )
            _write_json(
                root / "phone" / "v02_honest_ux_state.json",
                {
                    "schema": "nyairo.v02.honest_ux.v1",
                    "revision": 0,
                    "proposed_rescaled_copractices": {},
                },
            )
            before = _snapshot_tree(root)
            after_calm = lived_context_bridge.build_lived_turn_context(root, cfg, "你好", now_iso=T0)
            self.assertEqual(before, _snapshot_tree(root))
            self.assertEqual(after_calm, baseline)
            self.assertNotIn("声音便笺", after_calm)
            self.assertNotIn("主动联络", after_calm)
            self.assertNotIn("封存", after_calm)
            self.assertNotIn("缩减共同练习", after_calm)
            self.assertNotIn("阶段自述", after_calm)

    def test_02_surfaces_all_five_cues_respects_budget_and_private_redaction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-closure-cues-") as td:
            root = Path(td)
            (root / "phone").mkdir(parents=True, exist_ok=True)

            # F-025: seed via real v02_crossmodal_view (newest-first storage):
            # older shared audio note at T0, newer private audio note at T1
            c_an1, _r_an1 = cm_view.mutate_v02_crossmodal_view(
                root,
                {
                    "action": "ingest_audio_note",
                    "title_cn": "窗外小雨声",
                    "duration_seconds": 18.0,
                    "audio_format": "wav",
                    "source_kind": "ambient_memo",
                    "transcript_cn": "檐角滴水声很清脆",
                    "ambient_tag_cn": "雨天",
                    "shared": True,
                    "operation_key": "op_an_pub_1",
                },
                actor_side="partner",
                now_iso=T0,
            )
            self.assertEqual(c_an1, 200)
            c_an2, _r_an2 = cm_view.mutate_v02_crossmodal_view(
                root,
                {
                    "action": "ingest_audio_note",
                    "title_cn": "PRIV_VOICE_MEMO",
                    "duration_seconds": 9.0,
                    "audio_format": "ogg",
                    "source_kind": "mic_capture",
                    "transcript_cn": "PRIV_TRANSCRIPT",
                    "ambient_tag_cn": "夜里",
                    "shared": False,
                    "operation_key": "op_an_priv_2",
                },
                actor_side="subject",
                now_iso=T1,
            )
            self.assertEqual(c_an2, 200)

            # F-113 + F-148: enabled proactive outbound with queued item + PENDING_CONFIRMATION retirement
            _write_json(
                root / "phone" / "v02_runtime_migration.json",
                {
                    "schema": "nyairo.v02.runtime_migration.v1",
                    "mode_config": {"mode": "NORMAL", "budget_scale_pct": 100},
                    "retirement": {
                        "status": "PENDING_CONFIRMATION",
                        "prepared_at": T0,
                        "sealed_at": None,
                        "farewell_note_cn": "谢谢这一段长久陪伴",
                    },
                    "proactive_outbound": {
                        "enabled": True,
                        "quiet_start_hm": "22:30",
                        "quiet_end_hm": "07:30",
                        "daily_cap": 3,
                        "min_cooldown_minutes": 120,
                        "sent_today_count": 1,
                        "pending_queue": [
                            {
                                "message_id": "pm_1",
                                "message_cn": "傍晚转凉记得添衣",
                                "status": "QUEUED",
                            }
                        ],
                    },
                },
            )

            # F-189: partner rescaled co-practice proposal via real honest_ux_view
            c_prop, _r_prop = hux_view.mutate_v02_honest_ux_view(
                root,
                {
                    "action": "propose_rescaled_copractice",
                    "practice_id": "cop_tea_walk",
                    "title": "周末傍晚慢走",
                    "scale_mode": "light_relay",
                    "note_cn": "改成隔周轻接力",
                    "operation_key": "op_prop_tea_walk",
                },
                actor_side="partner",
                now_iso=T0,
            )
            self.assertEqual(c_prop, 200)

            # F-076: shared + private period self_claims via real autobiography engine
            asp.record_self_claim(
                root,
                {
                    "period_id": "2026-11",
                    "claim_key": "shared_pace",
                    "domain": "rhythm",
                    "statement": "这阵子喜欢安静慢慢来",
                    "visibility": "shared",
                    "operation_key": "op_clm_pub",
                },
                now_iso=T0,
            )
            asp.record_self_claim(
                root,
                {
                    "period_id": "2026-11",
                    "claim_key": "private_reflection",
                    "domain": "inner",
                    "statement": "PRIV_SELF_CLAIM",
                    "visibility": "private",
                    "operation_key": "op_clm_priv",
                },
                now_iso=T1,
            )

            before = _snapshot_tree(root)

            # Subject perspective: sees latest private audio & private self-claim + all closure cues, <= 1800 chars, 0 writes
            cfg_sub = {"day_root": str(root), "phone": {"enabled": False, "private_artifacts": True}}
            bd_sub = lived_context_bridge.measure_context_budget_breakdown(
                root, cfg=cfg_sub, incoming_text="聊聊近况", now_iso=T1
            )
            self.assertEqual(before, _snapshot_tree(root))
            self.assertTrue(bd_sub["within_budget"])
            self.assertLessEqual(bd_sub["total_chars"], 1800)
            txt_sub = bd_sub["rendered_text"]
            self.assertIn("体面封存待二次确认", txt_sub)
            self.assertIn("主动联络:已开启", txt_sub)
            self.assertIn("待阅1条", txt_sub)
            self.assertIn("声音便笺:PRIV_VOICE_MEMO「PRIV_TRANSCRIPT」[未公开]", txt_sub)
            self.assertIn("伴侣提议缩减共同练习:周末傍晚慢走", txt_sub)
            self.assertIn("阶段自述(2026-11):PRIV_SELF_CLAIM[仅己见]", txt_sub)

            # Restricted / partner perspective: NEVER leaks private audio or private self-claim; shared cues still surface!
            cfg_partner = {
                "day_root": str(root),
                "memory": True,
                "memory_tool_policy": "restricted",
                "phone": {"enabled": False, "private_artifacts": False},
            }
            bd_pub = lived_context_bridge.measure_context_budget_breakdown(
                root, cfg=cfg_partner, incoming_text="聊聊近况", now_iso=T1
            )
            self.assertEqual(before, _snapshot_tree(root))
            self.assertTrue(bd_pub["within_budget"])
            self.assertLessEqual(bd_pub["total_chars"], 1800)
            txt_pub = bd_pub["rendered_text"]
            self.assertNotIn("PRIV_VOICE_MEMO", txt_pub)
            self.assertNotIn("PRIV_TRANSCRIPT", txt_pub)
            self.assertNotIn("PRIV_SELF_CLAIM", txt_pub)
            self.assertIn("伴侣提议缩减共同练习:周末傍晚慢走", txt_pub)
            self.assertIn("阶段自述(2026-11):这阵子喜欢安静慢慢来", txt_pub)
            self.assertIn("体面封存待二次确认", txt_pub)

            # Direct formatter check (newest-first engine order): include_private_cm=False skips private
            # audio and falls back to shared audio, and UNAVAILABLE_LOCAL_ASR never surfaces transcript_cn!
            cm_doc = {
                "audio_notes": [
                    {
                        "audio_note_id": "an_priv_only",
                        "title_cn": "SECRET_AUDIO_2",
                        "transcript_status": "USER_SUPPLIED",
                        "transcript_cn": "SECRET_BODY_2",
                        "shared": False,
                    },
                    {
                        "audio_note_id": "an_pub_asr",
                        "title_cn": "街角风铃",
                        "transcript_status": "UNAVAILABLE_LOCAL_ASR",
                        "transcript_cn": "SHOULD_NOT_SHOW_WITHOUT_USER_SUPPLIED",
                        "shared": True,
                    },
                ]
            }
            line_pub = lived_context_bridge._format_v02_deep_surfaces(
                agency_doc=None,
                crossmodal_doc=cm_doc,
                runtime_doc={
                    "retirement_status": {
                        "status": "RETIRED_ARCHIVE_SEALED",
                        "farewell_note_cn": "温柔道别",
                    }
                },
                include_private_ad=False,
                include_private_cm=False,
                now_dt=datetime(2026, 11, 19, 11, 30, tzinfo=timezone(timedelta(hours=8))),
            )
            self.assertIsNotNone(line_pub)
            assert line_pub is not None
            self.assertIn("声音便笺:街角风铃", line_pub)
            self.assertNotIn("SHOULD_NOT_SHOW_WITHOUT_USER_SUPPLIED", line_pub)
            self.assertNotIn("SECRET_AUDIO_2", line_pub)
            self.assertNotIn("SECRET_BODY_2", line_pub)
            self.assertIn("已温和封存归档", line_pub)

    def test_03_f025_newest_audio_note_wins_and_explicit_forget_hmac_tombstone_suppresses_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-closure-audio-forget-") as td:
            root = Path(td)
            (root / "phone").mkdir(parents=True, exist_ok=True)
            cfg_sub = {"day_root": str(root), "phone": {"enabled": False, "private_artifacts": True}}

            c1, r1 = cm_view.mutate_v02_crossmodal_view(
                root,
                {
                    "action": "ingest_audio_note",
                    "title_cn": "OLDEST_NOTE_T0",
                    "duration_seconds": 10.0,
                    "audio_format": "wav",
                    "source_kind": "ambient_memo",
                    "transcript_cn": "OLD_T0",
                    "shared": True,
                    "operation_key": "op_aud_old",
                },
                actor_side="partner",
                now_iso=T0,
            )
            self.assertEqual(c1, 200, r1)
            oldest_id = str((r1.get("action_receipt") or {}).get("audio_note_id") or "")
            self.assertTrue(oldest_id, r1)

            c2, r2 = cm_view.mutate_v02_crossmodal_view(
                root,
                {
                    "action": "ingest_audio_note",
                    "title_cn": "NEWEST_NOTE_T1",
                    "duration_seconds": 12.0,
                    "audio_format": "ogg",
                    "source_kind": "mic_capture",
                    "transcript_cn": "NEW_T1",
                    "shared": True,
                    "operation_key": "op_aud_new",
                },
                actor_side="subject",
                now_iso=T1,
            )
            self.assertEqual(c2, 200, r2)
            newest_id = str((r2.get("action_receipt") or {}).get("audio_note_id") or "")
            self.assertTrue(newest_id and newest_id != oldest_id, (oldest_id, newest_id))

            # Newest note MUST win over oldest note (0 writes)
            before_1 = _snapshot_tree(root)
            ctx_1 = lived_context_bridge.build_lived_turn_context(root, cfg_sub, "听听声音便笺", now_iso=T1)
            self.assertEqual(before_1, _snapshot_tree(root))
            self.assertIn("声音便笺:NEWEST_NOTE_T1「NEW_T1」", ctx_1)
            self.assertNotIn("OLDEST_NOTE_T0", ctx_1)

            # Explicit Forget HMAC tombstone on newest audio note MUST strictly suppress it!
            f_code, f_body = rlf.execute_explicit_forget(
                root,
                source_ids=[newest_id],
                operation_id="op_forget_newest_audio",
                actor_side="subject",
                now_iso=T1,
            )
            self.assertEqual(f_code, 200, f_body)

            before_2 = _snapshot_tree(root)
            ctx_2 = lived_context_bridge.build_lived_turn_context(root, cfg_sub, "听听声音便笺", now_iso=T1)
            self.assertEqual(before_2, _snapshot_tree(root))
            self.assertNotIn("NEWEST_NOTE_T1", ctx_2)
            self.assertNotIn("NEW_T1", ctx_2)
            self.assertIn("声音便笺:OLDEST_NOTE_T0「OLD_T0」", ctx_2)

            # Forgetting the remaining audio note leaves zero audio cue leaked
            f_code2, f_body2 = rlf.execute_explicit_forget(
                root,
                source_ids=[oldest_id],
                operation_id="op_forget_oldest_audio",
                actor_side="subject",
                now_iso=T1,
            )
            self.assertEqual(f_code2, 200, f_body2)
            before_3 = _snapshot_tree(root)
            ctx_3 = lived_context_bridge.build_lived_turn_context(root, cfg_sub, "听听声音便笺", now_iso=T1)
            self.assertEqual(before_3, _snapshot_tree(root))
            self.assertNotIn("OLDEST_NOTE_T0", ctx_3)
            self.assertNotIn("OLD_T0", ctx_3)
            self.assertNotIn("声音便笺:", ctx_3)


class TestDailyLoopFinalClosure(unittest.TestCase):
    def test_01_f148_retired_archive_sealed_halts_tick_zero_candidates_zero_writes(self) -> None:
        for rel_path in (
            ("phone", "v02_runtime_migration.json"),
            ("state", "v02_runtime_governance_migration.json"),
        ):
            with self.subTest(runtime_file="/".join(rel_path)):
                with tempfile.TemporaryDirectory(prefix="v02-closure-retire-") as td:
                    root = Path(td)
                    # Seed rich active clues that would normally emit candidates & write tick state
                    hux_view.mutate_v02_honest_ux_view(
                        root,
                        {
                            "action": "propose_rescaled_copractice",
                            "practice_id": "cop_active",
                            "title": "画画轻接力",
                            "scale_mode": "light_relay",
                            "operation_key": "op_prop_retire_check",
                        },
                        actor_side="partner",
                        now_iso=T0,
                    )
                    _write_json(
                        root / "phone" / "desk_tomorrow_note.json",
                        {"schema": "nyairo.phone.desk_tomorrow.v1", "title": "明天画海堤", "status": "OPEN"},
                    )
                    _write_json(
                        root.joinpath(*rel_path),
                        {
                            "schema": "nyairo.v02.runtime_migration.v1",
                            "mode_config": {"mode": "NORMAL", "budget_scale_pct": 100},
                            "retirement": {
                                "status": "RETIRED_ARCHIVE_SEALED",
                                "prepared_at": T0,
                                "sealed_at": T1,
                                "farewell_note_cn": "珍藏回忆，温和封存",
                            },
                        },
                    )
                    before = _snapshot_tree(root)

                    tick = loop_bridge.run_v02_v03_daily_tick(
                        root,
                        now_iso=T1,
                        previous_host_iso=T_PREV_HOST,
                        advance_turn=True,
                        advance_clock=True,
                    )
                    self.assertEqual(before, _snapshot_tree(root), "Retired seal tick must be 100% zero-write")
                    self.assertTrue(tick["ok"])
                    self.assertTrue(tick["retired_sealed"])
                    self.assertEqual(tick["runtime_mode"], "DORMANT_NO_COGNITION")
                    self.assertEqual(tick["candidate_count"], 0)
                    self.assertEqual(tick["collected"]["candidates"], [])
                    self.assertTrue(tick["collected"]["dormant_suppressed"])
                    self.assertEqual(tick["selected_action"]["decision_kind"], "DORMANT_NO_COGNITION")
                    self.assertTrue(tick["selected_action"]["retired_sealed"])
                    self.assertEqual(tick["llm_calls"], 0)

    def test_02_f189_pending_rescaled_copractice_emits_ag0_candidate_and_settles(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-closure-cop-") as td:
            root = Path(td)
            code, _ = hux_view.mutate_v02_honest_ux_view(
                root,
                {
                    "action": "propose_rescaled_copractice",
                    "practice_id": "cop_evening_read",
                    "title": "晚间共读半章",
                    "scale_mode": "quiet_checkin",
                    "note_cn": "太忙时只报个平安就好",
                    "operation_key": "op_prop_read_1",
                },
                actor_side="partner",
                now_iso=T0,
            )
            self.assertEqual(code, 200)

            before = _snapshot_tree(root)
            collected = loop_bridge.collect_v02_v03_loop_candidates(root, now_iso=T0)
            self.assertEqual(before, _snapshot_tree(root), "Candidate collection must be 100% zero-write")

            cop_descs = [
                d
                for d in collected["descriptors"].values()
                if d.get("action_family") == "consider_rescaled_copractice"
            ]
            self.assertEqual(len(cop_descs), 1, collected["descriptors"])
            cop_desc = cop_descs[0]
            self.assertEqual(cop_desc["practice_id"], "cop_evening_read")
            self.assertEqual(cop_desc["scale_mode"], "quiet_checkin")
            self.assertTrue(cop_desc["zero_debt"])
            self.assertEqual(set(cop_desc.keys()) & _AG0_FORBIDDEN_KEYS, set())

            # Matching CandidateRecord must be AG0-compliant (no forbidden keys)
            cop_cand = next(
                c for c in collected["candidates"] if c["candidate_id"] == cop_desc["candidate_id"]
            )
            self.assertEqual(set(cop_cand.keys()) & _AG0_FORBIDDEN_KEYS, set())

            # Deterministic selector chooses it and builds executable round event
            sel = loop_bridge.select_deterministic_loop_action(collected)
            self.assertEqual(sel["decision_kind"], "SELECT_CANDIDATE")
            self.assertEqual(sel["action_family"], "consider_rescaled_copractice")

            ev = loop_bridge.v02_v03_round_event_for_selected_candidate(
                collected, cop_desc["source_ref"], now_iso=T1, decision_id="dec_cop_1"
            )
            self.assertIsNotNone(ev)
            assert ev is not None
            self.assertEqual(ev["action_kind"], "CONFIRM_RESCALED_COPRACTICE")
            self.assertEqual(ev["practice_id"], "cop_evening_read")

            # Settle round -> confirms proposal in honest_ux_state -> disappears from next scan!
            settled = loop_bridge.settle_v02_v03_round_consequences(root, ev, now_iso=T1)
            self.assertTrue(settled["settled"], settled)
            self.assertIn("v02_honest_ux.rescaled_copractices", settled["slices_updated"])

            _rc, view_after = hux_view.read_v02_honest_ux_view(root, has_subject_capability=True, now_iso=T1)
            matched = [
                r
                for r in (view_after.get("data") or {}).get("rescaled_copractices") or []
                if r.get("practice_id") == "cop_evening_read"
            ]
            self.assertEqual(len(matched), 1)
            self.assertTrue(matched[0]["subject_confirmed"])
            self.assertEqual(matched[0]["status"], "CONFIRMED")

            collected_after = loop_bridge.collect_v02_v03_loop_candidates(root, now_iso=T1)
            remaining_cop = [
                d
                for d in collected_after["descriptors"].values()
                if d.get("action_family") == "consider_rescaled_copractice"
            ]
            self.assertEqual(remaining_cop, [])

    def test_03_f187_auto_workspace_audit_on_settled_rounds_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v02-closure-wsa-") as td:
            root = Path(td)

            # 1. When runtime migration file does NOT exist -> settlement succeeds cleanly and does NOT create it
            r_no_rm = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "actor_side": "subject",
                    "effect_state": "APPLIED",
                    "action_kind": "REST",
                    "title": "靠窗小憩",
                    "receipt_ref": "rcpt:wsa_no_rm",
                    "operation_key": "op_wsa_no_rm",
                },
                now_iso=T0,
            )
            self.assertTrue(r_no_rm["settled"], r_no_rm)
            self.assertFalse((root / "phone" / "v02_runtime_migration.json").exists())

            # 2. Initialize runtime migration state on disk
            rm_engine.apply_runtime_migration_mutation(
                root,
                {"action": "set_budget_mode", "mode": "NORMAL", "budget_scale_pct": 100, "operation_key": "op_init_rm"},
                actor_side="subject",
                now_iso=T0,
            )
            self.assertTrue((root / "phone" / "v02_runtime_migration.json").is_file())
            proj_before = rm_engine.project_v02_runtime_migration(root, has_subject_capability=True, now_iso=T0)
            self.assertEqual(len(proj_before.get("workspace_audits") or []), 0)

            # Unauthorized partner or UNKNOWN round -> ZERO workspace audits recorded (V-A iron law)
            refused = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "actor_side": "partner",
                    "effect_state": "APPLIED",
                    "action_kind": "REST",
                    "receipt_ref": "rcpt:bad_p",
                    "operation_key": "op_bad_p",
                },
                now_iso=T0,
            )
            self.assertFalse(refused["settled"])
            unknown = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "actor_side": "subject",
                    "effect_state": "UNKNOWN",
                    "action_kind": "REST",
                    "receipt_ref": "rcpt:unk_p",
                    "operation_key": "op_unk_p",
                },
                now_iso=T0,
            )
            self.assertFalse(unknown["settled"])
            proj_still_zero = rm_engine.project_v02_runtime_migration(root, has_subject_capability=True, now_iso=T0)
            self.assertEqual(len(proj_still_zero.get("workspace_audits") or []), 0)

            # 3. Real APPLIED settled round -> auto-records bounded workspace audit (redacting private draft titles for partner)
            r_ok = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "actor_side": "subject",
                    "effect_state": "APPLIED",
                    "action_kind": "REST",
                    "title": "SECRET_DRAFT_TITLE",
                    "private": True,
                    "receipt_ref": "rcpt:wsa_ok_1",
                    "operation_key": "op_wsa_ok_1",
                    "second_order_constrained_candidates": [
                        {
                            "rule_id": "rule_night",
                            "rule_title": "深夜轻活动",
                            "candidate_ref": "outing:far_hill",
                            "candidate_title": "远途爬坡",
                            "reason_cn": "按规矩延后远途外出",
                        }
                    ],
                },
                now_iso=T1,
            )
            self.assertTrue(r_ok["settled"], r_ok)

            proj_sub = rm_engine.project_v02_runtime_migration(root, has_subject_capability=True, now_iso=T1)
            audits_sub = proj_sub.get("workspace_audits") or []
            self.assertEqual(len(audits_sub), 1, audits_sub)
            self.assertEqual(audits_sub[0]["wake_reason"], "REST")
            self.assertEqual(audits_sub[0]["entered_workspace_count"], 1)
            self.assertEqual(audits_sub[0]["visible_candidate_count"], 2)
            self.assertEqual(audits_sub[0]["admitted_candidates"][0]["title"], "SECRET_DRAFT_TITLE")

            # Partner perspective must redact the private draft title cleanly
            proj_partner = rm_engine.project_v02_runtime_migration(root, has_subject_capability=False, now_iso=T1)
            audits_partner_json = json.dumps(proj_partner.get("workspace_audits") or [], ensure_ascii=False)
            self.assertNotIn("SECRET_DRAFT_TITLE", audits_partner_json)


if __name__ == "__main__":
    unittest.main()

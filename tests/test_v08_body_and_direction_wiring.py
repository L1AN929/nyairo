"""Wiring verification for v0.8 composable body + open day directions + attention cues.

Covers:
  * ``_PHONE_AND_WORLD_GUIDANCE`` stays ``<= 1200`` chars and lists ``v08_composable_body``
    while preserving every token ``test_hermes_phone_brain_wiring`` requires;
  * a disabled / absent composable body emits ``""`` (zero token waste);
  * an active feeling / pending garment care emits one concise cue under
    ``private_artifacts=True`` and stays 100% hidden when ``private_artifacts=False``;
  * ``day_directions`` are tagged as "尚未发生，非既成经历" and the ``v08_body:`` daily-loop
    scan / event / settle path works cleanly (real engine, zero writes before selection);
  * no user-visible string hardcodes ``她`` or ``千代``.

Run:
``PYTHONDONTWRITEBYTECODE=1 python -B -m unittest tests.test_v08_body_and_direction_wiring -v``
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chiyo_bundle import lived_context_bridge as bridge  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402

_BASE_READS = [
    "desk_presence", "desk_notes", "home_window", "home_keepsakes",
    "home_letters", "home_workbench", "home_curios", "home_trio",
    "home_reunion_anchor", "home_recipes", "home_tentative_plans",
    "wardrobe", "pocket_lists", "outing_bench_bookstore",
    "outing_routes_market", "outing_gallery_blueprint",
    "longterm_habits", "longterm_gifts_lantern", "longterm_resume_hub",
    "body_somatic", "body_garment_window", "body_care", "body_sensory",
    "character_pack", "autonomy_rhythm", "emotional_tension",
    "stance_containment", "adult_intimacy", "v03_clock",
    "v03_ecology", "v03_embodied_social",
]
_EXTRA_TOKENS = [
    'target_surface="', "phone_life_update", "world_action", "TOILET",
    "work_context", "v08_living_traces", "v08_emergent_echoes",
    "v03_encounters", "compose_open_act", "v08_composable_body",
]


def _load_plugin():
    plugin_path = ROOT / "plugins" / "chiyo" / "__init__.py"
    spec = importlib.util.spec_from_file_location("chiyo_root_plugin_init", plugin_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_no_hardcoded_pronoun(testcase: unittest.TestCase, text: str) -> None:
    testcase.assertNotIn("她", text)
    testcase.assertNotIn("千代", text)


def _write_engine_state(root: Path, *, pending_garment_care: bool = False,
                        note_cn: str = "") -> Path:
    """Write an engine-shaped composable-body pack file (what the real engine reads)."""

    body_dir = root / "body"
    body_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": "body.v08.composable_pack.v1",
        "revision": 3,
        "packs": {
            "menstrual_cycle": {
                "enabled": True,
                "anchor_iso": "2026-11-10T08:00:00+00:00",
                "cycle_length_days": 28,
                "active_days": 5,
            },
            "nocturnal_emission": {
                "enabled": True,
                "pending_garment_care": pending_garment_care,
                "garment_care_note_cn": note_cn,
            },
        },
        "operations": {},
    }
    path = body_dir / "v08_composable_body_pack.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return path


class GuidanceBudgetTest(unittest.TestCase):
    def test_guidance_budget_tokens_and_body_surface(self) -> None:
        plugin = _load_plugin()
        guidance = plugin._PHONE_AND_WORLD_GUIDANCE
        self.assertLessEqual(len(guidance), 1200, f"guidance too long: {len(guidance)}")
        for token in _BASE_READS + _EXTRA_TOKENS:
            self.assertIn(token, guidance, f"missing required token: {token}")
        _assert_no_hardcoded_pronoun(self, guidance)


class PhoneToolSurfaceWiringTest(unittest.TestCase):
    def test_composable_body_registered_on_every_surface_set(self) -> None:
        self.assertIn("v08_composable_body", phone_tool.ACTIONS)
        self.assertIn("v08_composable_body", phone_tool.READ_ACTIONS)
        self.assertIn("v08_composable_body", phone_tool.PRIVATE_ARTIFACT_ACTIONS)
        self.assertIn("v08_composable_body", phone_tool._SUBJECT_VIEW_READ_ACTIONS)
        self.assertIn("v08_composable_body", phone_tool.LIFE_UPDATE_SURFACES)
        self.assertIn("v08_composable_body", phone_tool.LIFE_UPDATE_PRIVATE_SURFACES)
        self.assertEqual(
            phone_tool._PARTNER_ONLY_SUBACTIONS["v08_composable_body"],
            frozenset({"offer_ordinary_care"}),
        )
        hint = phone_tool._SURFACE_SUPPORTED_HINTS["v08_composable_body"]
        for action in (
            "configure_body_pack", "use_cycle_supply", "bind_sleep_activity",
            "settle_sleep_wake", "resolve_garment_care",
            "share_body_care_hint_once", "clear_private_body_history",
        ):
            self.assertIn(action, hint["subject_actions"])
        echoes = phone_tool._SURFACE_SUPPORTED_HINTS["v08_emergent_echoes"]
        self.assertIn("sketch_day_direction", echoes["subject_actions"])
        self.assertIn("advance_or_interrupt_direction", echoes["subject_actions"])


class ComposableBodyFormatterTest(unittest.TestCase):
    def test_absent_and_disabled_emit_nothing(self) -> None:
        self.assertEqual(
            bridge._format_v08_composable_body_micro(None, include_private=True), ""
        )
        self.assertEqual(
            bridge._format_v08_composable_body_micro(
                {"menstrual_cycle": {"enabled": False, "current_feeling_cn": "小腹发沉"}},
                include_private=True,
            ),
            "",
        )
        # Enabled but quiet -> no cue.
        self.assertEqual(
            bridge._format_v08_composable_body_micro(
                {"menstrual_cycle": {"enabled": True}}, include_private=True
            ),
            "",
        )

    def test_active_feeling_and_garment_care_concise(self) -> None:
        feeling = bridge._format_v08_composable_body_micro(
            {"menstrual_cycle": {"enabled": True,
                                 "current_feeling_cn": "小腹有点发沉，想先歇一会儿"}},
            include_private=True,
        )
        self.assertIn("当下体感", feeling)
        self.assertLessEqual(len(feeling), 120)
        _assert_no_hardcoded_pronoun(self, feeling)

        garment = bridge._format_v08_composable_body_micro(
            {"nocturnal_emission": {"enabled": True, "pending_garment_care": True,
                                    "garment_care_note_cn": "夜里沾到了"}},
            include_private=True,
        )
        self.assertIn("衣物留意", garment)
        self.assertLessEqual(len(garment), 120)
        _assert_no_hardcoded_pronoun(self, garment)

    def test_restricted_view_hides_body(self) -> None:
        doc = {"menstrual_cycle": {"enabled": True, "current_feeling_cn": "小腹发沉"},
               "nocturnal_emission": {"enabled": True, "pending_garment_care": True}}
        self.assertEqual(
            bridge._format_v08_composable_body_micro(doc, include_private=False), ""
        )


class BodyRenderIntegrationTest(unittest.TestCase):
    def _render(self, root: Path, *, private: bool) -> str:
        return bridge.render_lived_companion_context(
            root,
            {
                "day_root": str(root),
                "memory": True,
                "memory_tool_policy": "restricted",
                "phone": {"enabled": True, "private_artifacts": private},
            },
        )

    def test_pending_garment_care_visible_only_when_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_engine_state(root, pending_garment_care=True,
                                note_cn="夜里沾到了，换下来洗")
            permitted = self._render(root, private=True)
            self.assertIn("衣物留意", permitted)
            self.assertLessEqual(len(permitted), bridge.MAX_LIVED_CONTEXT_CHARS)
            _assert_no_hardcoded_pronoun(self, permitted)
            restricted = self._render(root, private=False)
            self.assertNotIn("衣物留意", restricted)
            self.assertNotIn("夜里沾到了", restricted)

    def test_quiet_pack_adds_no_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_engine_state(root, pending_garment_care=False)
            rendered = self._render(root, private=True)
            self.assertNotIn("身体照料", rendered)
            self.assertNotIn("衣物留意", rendered)


class DayDirectionAndAttentionTest(unittest.TestCase):
    def test_day_direction_tagged_not_yet_happened(self) -> None:
        now = datetime.now(tz=timezone.utc)
        doc = {
            "day_directions": [{
                "status": "direction_only",
                "horizon_note_cn": "今天想在窗边慢慢画完海堤草稿",
                "visibility": "shared",
                "executed_in_world": False,
            }]
        }
        out = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="", include_private=True, now_dt=now
        )
        self.assertIn("尚未发生", out)
        self.assertIn("非既成经历", out)
        _assert_no_hardcoded_pronoun(self, out)

    def test_private_day_direction_withheld_without_private(self) -> None:
        now = datetime.now(tz=timezone.utc)
        doc = {
            "day_directions": [{
                "status": "direction_only",
                "horizon_note_cn": "今天想在窗边慢慢画完海堤草稿",
                "visibility": "private",
            }]
        }
        self.assertEqual(
            bridge._format_v08_emergent_echoes_micro(
                doc, incoming_text="", include_private=False, now_dt=now
            ),
            "",
        )

    def test_attention_cues_private_only(self) -> None:
        doc = {"attention_cues": [
            {"title_cn": "海边植物图鉴", "matched_concern_cn": "海堤草稿"},
            {"title_cn": "旧街侦探小说"},
        ]}
        seen = bridge._format_v03_epistemic_encounters_micro(doc, include_private=True)
        self.assertIn("眼下更留意的", seen)
        self.assertIn("海边植物图鉴", seen)
        self.assertEqual(
            bridge._format_v03_epistemic_encounters_micro(doc, include_private=False), ""
        )


class DailyLoopBodyCandidateTest(unittest.TestCase):
    def test_garment_care_scan_event_and_settle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_engine_state(root, pending_garment_care=True,
                                note_cn="夜里沾到了")

            collected = loop_bridge.collect_v02_v03_loop_candidates(
                root, now_iso="2026-11-16T09:00:00+08:00"
            )
            descs = collected.get("descriptors") or {}
            hits = [
                (cid, d) for cid, d in descs.items()
                if str(d.get("source_ref", "")).startswith("v08_body:")
            ]
            self.assertEqual(len(hits), 1, hits)
            _cid, desc = hits[0]
            self.assertEqual(desc["source_ref"], "v08_body:resolve_garment_care")
            self.assertEqual(desc["body_action_kind"], "RESOLVE_GARMENT_CARE")
            self.assertIn(desc["garment_care_mode"], ("wash_garment", "change_garment"))
            self.assertFalse(desc["strenuous_outing"])
            _assert_no_hardcoded_pronoun(self, json.dumps(desc, ensure_ascii=False))

            event = loop_bridge._v02v03_event_from_descriptor(
                _cid, desc, decision_id="dec-body-1"
            )
            self.assertIsNotNone(event)
            self.assertEqual(event["action_kind"], "RESOLVE_GARMENT_CARE")
            self.assertIn("operation_key", event)
            replay = loop_bridge._v02v03_event_from_descriptor(
                _cid, desc, decision_id="dec-body-1"
            )
            self.assertEqual(event["operation_key"], replay["operation_key"])

            round_event = {
                **event,
                "effect_state": "APPLIED",
                "actor_side": "life_resident",
                "receipt_ref": f"v02v03:{_cid}",
                "receipt_origin": "owner_port",
            }
            settled = loop_bridge.settle_v02_v03_round_consequences(
                root, round_event, now_iso="2026-11-16T09:00:00+08:00"
            )
            self.assertTrue(settled.get("ok"), settled)
            self.assertEqual(settled.get("status"), "APPLIED")
            self.assertIn("body.v08_composable_pack", settled.get("slices_updated") or [])

            projection = loop_bridge._read_composable_body_projection_zero_write(root)
            self.assertFalse(projection.get("pending_garment_care"))

    def test_supply_change_candidate_and_event(self) -> None:
        rec, desc = loop_bridge._v08_body_candidate_from_projection(
            {"menstrual_cycle": {"enabled": True, "needs_supply_change": True}}
        )
        self.assertIsNotNone(rec)
        assert desc is not None
        self.assertEqual(desc["source_ref"], "v08_body:use_cycle_supply")
        self.assertEqual(desc["body_action_kind"], "USE_CYCLE_SUPPLY")
        self.assertFalse(desc["strenuous_outing"])
        event = loop_bridge._v02v03_event_from_descriptor(
            rec.candidate_id, desc, decision_id="dec-supply-1"
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["action_kind"], "USE_CYCLE_SUPPLY")

    def test_highlight_precedence_and_zero_write_on_scan(self) -> None:
        # garment care outranks a simultaneous supply need.
        rec, desc = loop_bridge._v08_body_candidate_from_projection(
            {
                "packs": {
                    "nocturnal_emission": {"enabled": True, "pending_garment_care": True},
                    "menstrual_cycle": {"enabled": True, "needs_supply_change": True},
                }
            }
        )
        self.assertIsNotNone(rec)
        assert desc is not None
        self.assertEqual(desc["body_action_kind"], "RESOLVE_GARMENT_CARE")

    def test_quiet_projection_yields_no_candidate(self) -> None:
        rec, desc = loop_bridge._v08_body_candidate_from_projection({})
        self.assertIsNone(rec)
        self.assertIsNone(desc)


if __name__ == "__main__":
    unittest.main()

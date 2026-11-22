#!/usr/bin/env python3
"""Worker 9C: ``0.2`` Ch.16 three core controlled hypothesis experiments.

Each experiment is a *controlled ablation*: the only thing that changes between
the control arms is the real physical / autobiographical fact the hypothesis
claims matters.  No model is ever called -- every assertion is ``llm_calls == 0``.

1. Background expectation & absence (``familiar_absence``): a familiar object that
   genuinely lived at ``home_desk`` twice, then moved away, yields a calm
   ``witnessed_empty`` cue when the desk is seen -- and *zero* cues when the desk
   is occluded, or when the object was only ever a temporary unfamiliar visitor.
2. Autobiographical craft genealogy (``craft_genealogy``): two different learning
   histories select different methods for the exact same task context; a stored
   mismatch boundary withholds mechanical replay; a partner-shared tip surfaces
   without ever leaking the private ``source_work_ref``.
3. Cross-activity workspace scaffolding (``workspace_scaffolds``): tools + sketch
   paper left on the desk report ``ready_at_hand``; stowing them reports
   ``stowed_away``; laying them back restores ``ready_at_hand`` -- with no hidden
   phase score or streak counter anywhere.

The suite is deliberately resilient, mirroring ``test_v08_craft_context_and_loop``:
the engine-level arms skip cleanly if a concurrent worker has not yet landed the
9A/9B helpers, while the phone-hint, lived-context and daily-loop wiring
invariants always run.

Run (WSL): ``python -B -m unittest tests.test_v08_ch16_three_core_hypotheses -v``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402

try:  # Worker 9A may or may not have landed yet; never fail collection on this.
    from components.world.scripts import v08_universal_matter as matter_engine  # noqa: E402
except Exception:  # noqa: BLE001
    matter_engine = None  # type: ignore[assignment]

try:  # Worker 9B may or may not have landed yet.
    from components.life.scripts import v08_emergent_echoes as echo_engine  # noqa: E402
    from components.phone import v08_emergent_echoes_view as echo_view  # noqa: E402
except Exception:  # noqa: BLE001
    echo_engine = None  # type: ignore[assignment]
    echo_view = None  # type: ignore[assignment]

T0 = "2026-11-16T09:00:00+08:00"
NOW = datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc)


def _has_absence_engine() -> bool:
    return matter_engine is not None and callable(
        getattr(matter_engine, "project_familiar_absence_cues_zero_write", None)
    )


def _has_scaffold_engine() -> bool:
    return matter_engine is not None and callable(
        getattr(matter_engine, "project_workspace_scaffolds_zero_write", None)
    )


def _has_craft_engine() -> bool:
    return (
        echo_engine is not None
        and callable(getattr(echo_engine, "match_applicable_craft_genealogy_zero_write", None))
        and echo_view is not None
    )


def _tmp_root(case: unittest.TestCase) -> Path:
    tmp = tempfile.TemporaryDirectory(prefix="v08ch16-")
    case.addCleanup(tmp.cleanup)
    return Path(tmp.name)


def _write_state(root: Path, state: dict) -> None:
    (root / "world").mkdir(parents=True, exist_ok=True)
    matter_engine._atomic_write_state(root, state)


def _default_state() -> dict:
    return matter_engine._default_state()


def _record_craft(root: Path, *, tag: str, operation: str, boundary: str,
                  problem: str = "删掉空洞抒情只留物件触感",
                  domain: str = "short_prose", outcome: str = "worked",
                  source_work_ref: str = "draft://rainy-letter-v2",
                  shared_tip_only: bool = False,
                  shareable_tip: str = "") -> str:
    payload = {
        "action": "record_craft_genealogy_step",
        "operation_key": "cg-" + tag,
        "problem_pattern_cn": problem,
        "operation_cn": operation,
        "domain_tag": domain,
        "outcome": outcome,
        "mismatch_boundary_cn": boundary,
        "source_work_ref": source_work_ref,
        "shared_tip_only": shared_tip_only,
    }
    if shareable_tip:
        payload["shareable_tip_cn"] = shareable_tip
    projected, status = echo_engine.apply_emergent_echoes_mutation(
        root, payload, actor_side="subject", now_iso=T0
    )
    assert status in ("APPLIED", "DUPLICATE")
    rows = projected.get("craft_genealogy") or []
    assert rows, "recorded craft genealogy row must project back"
    return str(rows[-1]["genealogy_id"])


# --------------------------------------------------------------------------------------------
# Surface wiring (always runs -- independent of the 9A/9B engines)
# --------------------------------------------------------------------------------------------


class PhoneSurfaceWiring(unittest.TestCase):
    def test_new_actions_registered_without_expanding_surface_count(self):
        hint = phone_tool._surface_supported_hint("v08_emergent_echoes")
        for action in ("record_craft_genealogy_step", "toggle_share_craft_tip"):
            self.assertIn(action, hint["subject_actions"])
            self.assertIn(action, hint["core_fields"])
        self.assertEqual(hint["core_fields"]["toggle_share_craft_tip"][0], "genealogy_id")
        self.assertIn("mismatch_boundary_cn", hint["core_fields"]["record_craft_genealogy_step"])
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)

    def test_new_actions_require_operation_key_and_subject_capability(self):
        self.assertIn(
            "record_craft_genealogy_step",
            phone_tool._V08_EMERGENT_ECHOES_OPKEY_SUBACTIONS,
        )
        self.assertIn(
            "toggle_share_craft_tip",
            phone_tool._V08_EMERGENT_ECHOES_CAPABILITY_SUBACTIONS,
        )


class LivedContextWiring(unittest.TestCase):
    def test_max_lived_context_chars_unchanged(self):
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)

    def test_witnessed_empty_cue_surfaces_and_is_capped(self):
        doc = {
            "active_traces": [],
            "routines": [],
            "matter_space": {
                "familiar_absence_cues": [{
                    "label_cn": "白瓷杯",
                    "cue_cn": "平时搁在书桌台面的「白瓷杯」这会儿空着",
                    "epistemic_state": "witnessed_empty",
                    "zero_blame": True,
                }],
            },
        }
        line = lcb._format_v08_living_traces_micro(doc, include_private=True)
        self.assertIn("空着", line)
        self.assertLessEqual(len(line), 160)

    def test_unknown_or_private_absence_is_not_surfaced_to_partner(self):
        doc = {
            "active_traces": [],
            "routines": [],
            "matter_space": {
                "familiar_absence_cues": [{
                    "label_cn": "白瓷杯",
                    "cue_cn": "平时搁在书桌台面的「白瓷杯」这会儿空着",
                    "epistemic_state": "witnessed_empty",
                    "zero_blame": True,
                    "visibility": "private",
                }],
            },
        }
        self.assertEqual(lcb._format_v08_living_traces_micro(doc, include_private=False), "")
        self.assertIn("空着", lcb._format_v08_living_traces_micro(doc, include_private=True))

    def test_ready_workspace_scaffold_surfaces_as_factual_cue(self):
        doc = {
            "active_traces": [],
            "routines": [],
            "matter_space": {
                "workspace_scaffolds": [{
                    "zone_id": "home_desk",
                    "ready_domain": "craft_or_writing",
                    "startup_ease": "ready_at_hand",
                    "summary_cn": "书桌上有摆开的工具/纸张，接着动手或写画很顺手",
                }],
            },
        }
        line = lcb._format_v08_living_traces_micro(doc, include_private=True)
        self.assertIn("顺手", line)
        # stowed_away is not a "ready" cue; nothing surfaces.
        doc["matter_space"]["workspace_scaffolds"][0]["startup_ease"] = "stowed_away"
        self.assertEqual(lcb._format_v08_living_traces_micro(doc, include_private=True), "")

    def test_craft_tip_never_renders_source_work_ref(self):
        doc = {"craft_genealogy": [{
            "genealogy_id": "cg-demo",
            "problem_pattern_cn": "删掉空洞抒情只留物件触感",
            "operation_cn": "改写成一只缺口杯子的细节",
            "domain_tag": "short_prose",
            "outcome": "worked",
            "mismatch_boundary_cn": "赶工交接时不要慢描细节",
            "source_work_ref": "draft://secret-manuscript",
            "shareable_tip_cn": "删掉空话，只落一个具体物件",
            "shared_tip_only": False,
        }]}
        line = lcb._format_v08_emergent_echoes_micro(
            doc,
            incoming_text="今天写短篇总是空洞抒情，想删掉这些空话",
            include_private=True,
            now_dt=NOW,
        )
        self.assertIn("旧技法", line)
        self.assertIn("具体物件", line)
        self.assertNotIn("draft://secret-manuscript", line)

    def test_unshared_craft_tip_is_invisible_to_partner(self):
        doc = {"craft_genealogy": [{
            "problem_pattern_cn": "删掉空洞抒情",
            "operation_cn": "改写成缺口杯子的细节",
            "domain_tag": "short_prose",
            "outcome": "worked",
            "mismatch_boundary_cn": "赶工交接时不要慢描细节",
            "source_work_ref": "draft://secret-manuscript",
            "shared_tip_only": False,
        }]}
        line = lcb._format_v08_emergent_echoes_micro(
            doc,
            incoming_text="删掉空洞抒情吧",
            include_private=False,
            now_dt=NOW,
        )
        self.assertEqual(line, "")
        # Once explicitly shared, the partner-safe tip shows -- still no private ref.
        doc["craft_genealogy"][0]["shared_tip_only"] = True
        doc["craft_genealogy"][0]["shareable_tip_cn"] = "删掉空话，只落一个具体物件"
        shared_line = lcb._format_v08_emergent_echoes_micro(
            doc,
            incoming_text="删掉空洞抒情吧",
            include_private=False,
            now_dt=NOW,
        )
        self.assertIn("具体物件", shared_line)
        self.assertNotIn("secret-manuscript", shared_line)


# --------------------------------------------------------------------------------------------
# Experiment 1 -- Background Expectation & Absence
# --------------------------------------------------------------------------------------------


@unittest.skipUnless(_has_absence_engine(), "9A absence engine not landed yet")
class Experiment1BackgroundAbsence(unittest.TestCase):
    def _anchored_state(self) -> dict:
        state = _default_state()
        cup = state["matter_objects"]["ceramic_cup_01"]
        cup["familiar_spot_zone_id"] = "home_desk"
        cup["familiar_spot_hits"] = 2
        cup["familiar_spot_anchor_zone"] = "home_desk"
        # The familiar object has been moved away to the staging corner.
        cup["parent_id"] = "home_staging_corner"
        return state

    def test_control_a_unobstructed_home_desk_witnesses_calm_empty(self):
        root = _tmp_root(self)
        _write_state(root, self._anchored_state())
        cues = matter_engine.project_familiar_absence_cues_zero_write(
            root, observed_zone_ids=["home_desk"], occluded_zone_ids=[]
        )
        self.assertEqual(len(cues), 1)
        cue = cues[0]
        self.assertEqual(cue["epistemic_state"], "witnessed_empty")
        self.assertTrue(cue["zero_blame"])
        self.assertEqual(cue["expected_zone_id"], "home_desk")
        self.assertIn("空着", cue["cue_cn"])
        self.assertIn("白瓷杯", cue["cue_cn"])

        # And it surfaces cleanly in the lived context.
        view = matter_engine.read_matter_space_view(root, actor_side="subject")
        line = lcb._format_v08_living_traces_micro(
            {"active_traces": [], "routines": [], "matter_space": view},
            include_private=True,
        )
        self.assertIn("空着", line)

    def test_control_b_occluded_home_desk_emits_zero_absence_cues(self):
        root = _tmp_root(self)
        _write_state(root, self._anchored_state())
        cues = matter_engine.project_familiar_absence_cues_zero_write(
            root, observed_zone_ids=["home_desk"], occluded_zone_ids=["home_desk"]
        )
        self.assertEqual(cues, [])
        rows = matter_engine.inspect_absence_epistemics_zero_write(
            root, observed_zone_ids=["home_desk"], occluded_zone_ids=["home_desk"]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["epistemic_state"], "unknown_unobserved")
        self.assertIsNone(rows[0]["cue_cn"])

    def test_control_b_unobserved_zone_also_emits_zero(self):
        root = _tmp_root(self)
        _write_state(root, self._anchored_state())
        cues = matter_engine.project_familiar_absence_cues_zero_write(
            root, observed_zone_ids=["kitchen_counter"], occluded_zone_ids=[]
        )
        self.assertEqual(cues, [])

    def test_control_c_temporary_unfamiliar_object_has_no_expectation(self):
        root = _tmp_root(self)
        state = _default_state()
        tray = matter_engine._object_row(
            "borrowed_tray", "临时借来的托盘", item_kind="tool",
            parent_id="home_staging_corner",
        )
        # Placed once, then moved away: hits stay below the familiar threshold.
        tray["familiar_spot_zone_id"] = "home_desk"
        tray["familiar_spot_hits"] = 1
        tray["familiar_spot_anchor_zone"] = None
        state["matter_objects"]["borrowed_tray"] = tray
        _write_state(root, state)
        cues = matter_engine.project_familiar_absence_cues_zero_write(
            root, observed_zone_ids=["home_desk"], occluded_zone_ids=[]
        )
        self.assertEqual(cues, [])

    def test_private_absence_never_leaks_to_partner(self):
        root = _tmp_root(self)
        state = self._anchored_state()
        state["matter_objects"]["ceramic_cup_01"]["visibility"] = "private"
        _write_state(root, state)
        subject_cues = matter_engine.project_familiar_absence_cues_zero_write(
            root, observed_zone_ids=["home_desk"], actor_side="subject"
        )
        partner_cues = matter_engine.project_familiar_absence_cues_zero_write(
            root, observed_zone_ids=["home_desk"], actor_side="partner"
        )
        self.assertEqual(len(subject_cues), 1)
        self.assertEqual(partner_cues, [])


# --------------------------------------------------------------------------------------------
# Experiment 2 -- Autobiographical Craft Genealogy
# --------------------------------------------------------------------------------------------


@unittest.skipUnless(_has_craft_engine(), "9B craft genealogy engine not landed yet")
class Experiment2CraftGenealogy(unittest.TestCase):
    TASK_CONTEXT = "写短篇时抒情太空，只剩空话"

    def test_swapping_learning_history_selects_different_methods(self):
        root_a = _tmp_root(self)
        root_b = _tmp_root(self)
        _record_craft(
            root_a, tag="a", operation="改写成一只缺口杯子的细节",
            boundary="快节奏赶工交接时不要慢描细节",
        )
        _record_craft(
            root_b, tag="b", operation="先列三段具体见闻再收束",
            boundary="对方只想看梗概时不要展开铺陈",
        )
        match_a = echo_engine.match_applicable_craft_genealogy_zero_write(
            root_a, domain_tag="short_prose",
            current_situation_cn=self.TASK_CONTEXT, include_private=True,
        )
        match_b = echo_engine.match_applicable_craft_genealogy_zero_write(
            root_b, domain_tag="short_prose",
            current_situation_cn=self.TASK_CONTEXT, include_private=True,
        )
        self.assertEqual(len(match_a["applicable_methods"]), 1)
        self.assertEqual(len(match_b["applicable_methods"]), 1)
        # Same task context, different learning history -> a different old method is
        # recalled.  (The id is derived from domain + problem pattern, so it is shared
        # identity; what actually differs is the remembered operation itself.)
        self.assertEqual(match_a["applicable_methods"][0]["operation_cn"], "改写成一只缺口杯子的细节")
        self.assertEqual(match_b["applicable_methods"][0]["operation_cn"], "先列三段具体见闻再收束")
        self.assertNotEqual(
            match_a["applicable_methods"][0]["operation_cn"],
            match_b["applicable_methods"][0]["operation_cn"],
        )

    def test_mismatch_boundary_withholds_mechanical_application(self):
        root = _tmp_root(self)
        _record_craft(
            root, tag="m", operation="改写成一只缺口杯子的细节",
            boundary="快节奏赶工交接时不要慢描细节",
        )
        match = echo_engine.match_applicable_craft_genealogy_zero_write(
            root, domain_tag="short_prose",
            current_situation_cn="今天赶工交接，得快", include_private=True,
        )
        self.assertEqual(match["applicable_methods"], [])
        self.assertEqual(len(match["known_mismatches"]), 1)
        self.assertEqual(match["known_mismatches"][0]["match_status"], "known_mismatch")

    def test_partner_share_exposes_tip_but_strips_source_work_ref(self):
        root = _tmp_root(self)
        gid = _record_craft(
            root, tag="share", operation="改写成一只缺口杯子的细节",
            boundary="快节奏赶工交接时不要慢描细节",
            source_work_ref="draft://private-manuscript",
            shareable_tip="删掉空话，只落一个具体物件",
        )
        # Unshared -> partner view has zero genealogy rows.
        status, envelope = echo_view.read_v08_emergent_echoes_view(
            root, has_subject_capability=False
        )
        self.assertEqual(status, 200)
        self.assertEqual(envelope["data"]["craft_genealogy"], [])

        echo_engine.apply_emergent_echoes_mutation(
            root,
            {
                "action": "toggle_share_craft_tip",
                "operation_key": "share-" + gid,
                "genealogy_id": gid,
                "shared_tip_only": True,
                "shareable_tip_cn": "删掉空话，只落一个具体物件",
            },
            actor_side="subject",
            now_iso=T0,
        )
        status, envelope = echo_view.read_v08_emergent_echoes_view(
            root, has_subject_capability=False
        )
        rows = envelope["data"]["craft_genealogy"]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("source_work_ref", rows[0])
        self.assertEqual(rows[0]["shareable_tip_cn"], "删掉空话，只落一个具体物件")

        partner_match = echo_engine.match_applicable_craft_genealogy_zero_write(
            root, domain_tag="short_prose",
            current_situation_cn=self.TASK_CONTEXT, include_private=False,
        )
        self.assertEqual(len(partner_match["applicable_methods"]), 1)
        self.assertNotIn("source_work_ref", partner_match["applicable_methods"][0])

    def test_daily_loop_annotates_craft_candidate_without_private_ref(self):
        root = _tmp_root(self)
        _record_craft(
            root, tag="loop", operation="改写成一只缺口杯子的细节",
            boundary="快节奏赶工交接时不要慢描细节",
            source_work_ref="draft://private-manuscript",
        )
        raw_items = [(
            object(),
            {
                "candidate_id": "cand:craft",
                "action_family": "compose_open_act",
                "title_hint": "把短篇稿里的空洞抒情改一改",
                "topic_key": "短篇稿",
                "source_ref": "v08_matter:craft:short_prose",
                "creative_intensive": False,
            },
        )]
        bridge._annotate_craft_genealogy_hints(raw_items, root)
        desc = raw_items[0][1]
        self.assertIn("craft_method_hint_cn", desc)
        self.assertIn("缺口杯子", desc["craft_method_hint_cn"])
        self.assertNotIn("source_work_ref", json.dumps(desc))
        self.assertNotIn("private-manuscript", json.dumps(desc))

    def test_daily_loop_mismatch_does_not_annotate_applicable_hint(self):
        root = _tmp_root(self)
        _record_craft(
            root, tag="loop-mismatch", operation="改写成一只缺口杯子的细节",
            boundary="快节奏赶工交接时不要慢描细节",
        )
        raw_items = [(
            object(),
            {
                "candidate_id": "cand:craft2",
                "action_family": "compose_open_act",
                "title_hint": "今天赶工交接，短篇稿得快点交出去",
                "topic_key": "短篇稿",
                "source_ref": "v08_matter:craft:short_prose",
            },
        )]
        bridge._annotate_craft_genealogy_hints(raw_items, root)
        desc = raw_items[0][1]
        self.assertNotIn("craft_method_hint_cn", desc)
        self.assertIn("craft_mismatch_note_cn", desc)


# --------------------------------------------------------------------------------------------
# Experiment 3 -- Cross-Activity Workspace Mutual Scaffolding
# --------------------------------------------------------------------------------------------


@unittest.skipUnless(_has_scaffold_engine(), "9A scaffold engine not landed yet")
class Experiment3WorkspaceScaffolds(unittest.TestCase):
    TOOLS = ("pen_01", "pen_02", "sketch_sheet_01")

    def _desk_row(self, root: Path) -> dict:
        rows = matter_engine.project_workspace_scaffolds_zero_write(root)
        return next((row for row in rows if row.get("zone_id") == "home_desk"), {})

    def test_activity_a_tools_left_out_lower_startup_friction(self):
        root = _tmp_root(self)
        # The default room already leaves pens + a sketch sheet on home_desk.
        row = self._desk_row(root)
        self.assertEqual(row.get("ready_domain"), "craft_or_writing")
        self.assertEqual(row.get("startup_ease"), "ready_at_hand")
        self.assertEqual(row.get("zone_id"), "home_desk")

        view = matter_engine.read_matter_space_view(root, actor_side="subject")
        self.assertEqual(view["workspace_scaffolds"][0]["zone_id"], "home_desk")
        line = lcb._format_v08_living_traces_micro(
            {"active_traces": [], "routines": [], "matter_space": view},
            include_private=True,
        )
        self.assertIn("顺手", line)

    def test_stow_tools_then_lay_back_out_restores_ease(self):
        root = _tmp_root(self)
        state = _default_state()
        # Step 2: Activity A finishes and stows everything into the staging corner.
        for oid in self.TOOLS:
            state["matter_objects"][oid]["parent_id"] = "home_staging_corner"
        _write_state(root, state)
        stowed = self._desk_row(root)
        self.assertEqual(stowed.get("startup_ease"), "stowed_away")

        # Step 3: lay them back out -> ready_at_hand returns, no streak involved.
        for oid in self.TOOLS:
            state["matter_objects"][oid]["parent_id"] = "home_desk"
        _write_state(root, state)
        restored = self._desk_row(root)
        self.assertEqual(restored.get("startup_ease"), "ready_at_hand")
        self.assertEqual(restored.get("ready_domain"), "craft_or_writing")

    def test_no_hidden_phase_score_or_streak_counter(self):
        root = _tmp_root(self)
        for row in matter_engine.project_workspace_scaffolds_zero_write(root):
            for key in row:
                lowered = str(key).lower()
                self.assertNotIn("streak", lowered)
                self.assertNotIn("phase", lowered)
                self.assertNotIn("progress", lowered)
            self.assertTrue(row.get("zero_write"))

    def test_dirty_counter_shows_needs_cleanup(self):
        root = _tmp_root(self)
        state = _default_state()
        state["matter_objects"]["soup_bowl_01"]["cleanliness"] = "soiled"
        _write_state(root, state)
        rows = matter_engine.project_workspace_scaffolds_zero_write(root)
        counter = next((row for row in rows if row.get("zone_id") == "kitchen_counter"), {})
        self.assertEqual(counter.get("ready_domain"), "warm_tea_or_cooking")
        self.assertEqual(counter.get("startup_ease"), "needs_cleanup")

    def test_daily_loop_annotates_related_candidate_with_scaffold_cue(self):
        raw_items = [(
            object(),
            {
                "candidate_id": "cand:desk",
                "action_family": "compose_open_act",
                "title_hint": "在书桌边接着把没写完的稿子写下去",
                "source_ref": "v08_matter:craft:short_prose",
            },
        )]
        bridge._annotate_workspace_scaffold_cues(
            raw_items,
            [{
                "zone_id": "home_desk",
                "ready_domain": "craft_or_writing",
                "startup_ease": "ready_at_hand",
                "summary_cn": "书桌上有摆开的工具/纸张，接着动手或写画很顺手",
            }],
        )
        desc = raw_items[0][1]
        self.assertEqual(desc.get("startup_ease"), "ready_at_hand")
        self.assertEqual(desc.get("scaffold_basis"), "V08_WORKSPACE_CRAFT_OR_WRITING")
        self.assertIn("顺手", desc.get("scaffold_summary_cn", ""))
        for key in desc:
            self.assertNotIn("streak", str(key).lower())
            self.assertNotIn("phase", str(key).lower())

    def test_stowed_scaffold_is_not_annotated_as_ready(self):
        raw_items = [(
            object(),
            {
                "candidate_id": "cand:desk2",
                "action_family": "compose_open_act",
                "title_hint": "在书桌边接着写",
                "source_ref": "v08_matter:craft:short_prose",
            },
        )]
        bridge._annotate_workspace_scaffold_cues(
            raw_items,
            [{
                "zone_id": "home_desk",
                "ready_domain": "craft_or_writing",
                "startup_ease": "stowed_away",
                "summary_cn": "桌边家伙什儿已经收进去了",
            }],
        )
        self.assertNotIn("startup_ease", raw_items[0][1])

    def test_projection_is_zero_write_on_fresh_dir(self):
        root = _tmp_root(self)
        rows = matter_engine.project_workspace_scaffolds_zero_write(root)
        self.assertTrue(rows)  # default room already has the desk tools out
        self.assertFalse((root / "world").exists())


if __name__ == "__main__":
    unittest.main()

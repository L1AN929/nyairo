#!/usr/bin/env python3
"""v0.8 Ch.16: background expectation, noticing absence, and workspace scaffolding.

Coverage:
* an unfamiliar item moved once never conjures a familiar expectation;
* two genuine subject contacts in one zone anchor that spot, so moving the thing
  away yields one calm ``witnessed_empty`` cue -- but an occluded or merely
  unobserved spot stays ``unknown_unobserved`` and emits nothing;
* a private object's absence never leaks through the partner door;
* workspace startup ease is read live off real objects: tools & paper at hand ->
  ``ready_at_hand``, stowed into a drawer/corner -> ``stowed_away``, laid back
  out -> ``ready_at_hand`` again, and a dirty bowl -> ``needs_cleanup``;
* every projection stays 100% zero-write on a fresh root.

Run (WSL):
``python -B -m unittest tests.test_v08_absence_and_scaffolding -v``.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
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
from components.phone import v08_living_traces_view as traces_view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _at(minutes: int, base: str = T0) -> str:
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


class V08AbsenceAndScaffoldingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-absence-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _act(self, steps: list[dict], *, now: str, intent: str = "随手做点什么") -> dict:
        return engine.compose_open_act(
            self.root, {"intent_cn": intent, "steps": steps}, now_iso=now
        )

    def _observe(self, object_id: str, *, now: str) -> dict:
        return self._act([{"op": "OBSERVE_OBJECT", "object_id": object_id}], now=now, intent="看一眼")

    def _move(self, object_id: str, target: str, *, now: str) -> dict:
        return self._act(
            [{"op": "PLACE_OR_MOVE", "object_id": object_id, "target_parent_id": target}],
            now=now,
            intent="挪一下",
        )

    def _introduce(self, object_id: str, label: str, kind: str, target: str, *, now: str, **extra) -> dict:
        step = {
            "op": "INTRODUCE_ITEM",
            "object_id": object_id,
            "label_cn": label,
            "item_kind": kind,
            "target_parent_id": target,
        }
        step.update(extra)
        return self._act([step], now=now, intent=f"放入{label}")

    def _cues(self, **kwargs) -> list[dict]:
        return engine.project_familiar_absence_cues_zero_write(self.root, now_iso=T0, **kwargs)

    def _scaffold(self, zone_id: str) -> dict | None:
        for row in engine.project_workspace_scaffolds_zero_write(self.root, now_iso=T0):
            if row["zone_id"] == zone_id:
                return row
        return None

    # -- 1. no expectation without repeated real contact -----------------

    def test_unfamiliar_item_moved_once_has_no_absence_cue(self) -> None:
        # pen_02 has never been observed or used; one move must not invent a spot.
        self._move("pen_02", "home_staging_corner", now=_at(0))
        self.assertEqual(self._cues(), [])
        self.assertEqual(engine.inspect_absence_epistemics_zero_write(self.root, now_iso=T0), [])

    # -- 2. anchored spot + witnessed-empty vs. occluded -----------------

    def test_witnessed_empty_emits_calm_cue_and_occlusion_stays_unknown(self) -> None:
        # Two genuine subject observations at home_desk anchor the cup there.
        self._observe("ceramic_cup_01", now=_at(0))
        self._observe("ceramic_cup_01", now=_at(1))
        self._move("ceramic_cup_01", "home_staging_corner", now=_at(2))

        cues = self._cues()
        self.assertEqual(len(cues), 1, cues)
        cue = cues[0]
        self.assertEqual(cue["object_id"], "ceramic_cup_01")
        self.assertEqual(cue["expected_zone_id"], "home_desk")
        self.assertEqual(cue["epistemic_state"], "witnessed_empty")
        self.assertTrue(cue["zero_blame"])
        self.assertIn("空着", cue["cue_cn"])
        self.assertIn("书桌台面", cue["cue_cn"])

        # The same spot, occluded, yields zero cues and stays honest.
        self.assertEqual(self._cues(occluded_zone_ids=["home_desk"]), [])
        rows = engine.inspect_absence_epistemics_zero_write(
            self.root, occluded_zone_ids=["home_desk"], now_iso=T0
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["epistemic_state"], "unknown_unobserved")
        self.assertIsNone(rows[0]["cue_cn"])

        # Merely not having looked there yet is also unknown, not "gone".
        self.assertEqual(self._cues(observed_zone_ids=["kitchen_counter"]), [])

    # -- 3. private absence never leaks to the partner door --------------

    def test_private_absence_is_never_leaked_to_partner(self) -> None:
        self._introduce(
            "keepsake_01", "一张旧票根", "keepsake", "home_staging_corner",
            now=_at(0), visibility="private",
        )
        self._observe("keepsake_01", now=_at(1))
        self._observe("keepsake_01", now=_at(2))
        self._move("keepsake_01", "subject_private_drawer", now=_at(3))

        subject_cues = self._cues(actor_side="subject")
        self.assertEqual([c["object_id"] for c in subject_cues], ["keepsake_01"])
        self.assertEqual(self._cues(actor_side="partner"), [])
        self.assertEqual(
            engine.inspect_absence_epistemics_zero_write(self.root, actor_side="partner", now_iso=T0),
            [],
        )

        partner_view = engine.read_matter_space_view(self.root, actor_side="partner", now_iso=T0)
        self.assertEqual(partner_view["familiar_absence_cues"], [])
        self.assertNotIn(
            "keepsake_01", {row["object_id"] for row in partner_view["objects"]}
        )

    # -- 4. workspace scaffolding is live, stowable and reversible -------

    def test_workspace_scaffold_ready_stowed_ready_and_dirty(self) -> None:
        desk = self._scaffold("home_desk")
        self.assertIsNotNone(desk)
        self.assertEqual(desk["ready_domain"], "craft_or_writing")
        self.assertEqual(desk["startup_ease"], "ready_at_hand")

        # Stow every tool and the sketch paper into the staging corner.
        for idx, oid in enumerate(("pen_01", "pen_02", "sketch_sheet_01")):
            self._move(oid, "home_staging_corner", now=_at(idx))
        desk = self._scaffold("home_desk")
        self.assertIsNotNone(desk)
        self.assertEqual(desk["startup_ease"], "stowed_away")

        # Lay them back out: ease returns, purely from the real objects.
        for idx, oid in enumerate(("pen_01", "pen_02", "sketch_sheet_01")):
            self._move(oid, "home_desk", now=_at(10 + idx))
        desk = self._scaffold("home_desk")
        self.assertIsNotNone(desk)
        self.assertEqual(desk["startup_ease"], "ready_at_hand")

        # A bowl gone dirty after a meal needs a wash before it is useful again.
        self._introduce(
            "stew_01", "一碗炖菜", "prepared_food", "soup_bowl_01",
            now=_at(20), portions=1,
        )
        self._act(
            [{"op": "CONSUME_PORTION", "object_id": "soup_bowl_01", "portions": 1}],
            now=_at(21),
            intent="吃完这碗",
        )
        kitchen = self._scaffold("kitchen_counter")
        self.assertIsNotNone(kitchen)
        self.assertEqual(kitchen["startup_ease"], "needs_cleanup")

    # -- 5. everything stays 100% zero-write -----------------------------

    def test_absence_and_scaffold_projections_are_zero_write(self) -> None:
        before = _snapshot(self.root)
        engine.project_familiar_absence_cues_zero_write(self.root, now_iso=T0)
        engine.inspect_absence_epistemics_zero_write(self.root, now_iso=T0)
        engine.project_workspace_scaffolds_zero_write(self.root, now_iso=T0)
        engine.project_workspace_scaffolds_zero_write(self.root, actor_side="partner", now_iso=T0)
        view = engine.read_matter_space_view(self.root, now_iso=T0)
        engine.read_matter_space_view(self.root, actor_side="partner", now_iso=T0)

        self.assertEqual(view["llm_calls"], 0)
        self.assertIn("familiar_absence_cues", view)
        self.assertIn("workspace_scaffolds", view)
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse((self.root / "world").exists())
        self.assertFalse(engine.state_path(self.root).exists())

    # -- 6. the lived-context summary carries the calm scaffold ---------

    def test_lived_context_summary_carries_one_calm_scaffold(self) -> None:
        summary = traces_view.summarize_living_traces_for_context(self.root, now_iso=T0)
        self.assertEqual(summary["llm_calls"], 0)
        texts = [hint["text_cn"] for hint in summary["matter_hints"]]
        self.assertTrue(any("书桌上有摆开" in text for text in texts), texts)
        # It never exceeds the three-hint cap.
        self.assertLessEqual(len(summary["matter_hints"]), 3)


if __name__ == "__main__":
    unittest.main()

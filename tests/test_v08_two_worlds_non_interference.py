#!/usr/bin/env python3
"""Red-team: two worlds, non-interference (pre-miss epistemic masking).

These tests are adversarial.  They build the *same* subject history twice and
then let reality diverge underneath it.  Until the subject legitimately observes
or reaches a believed spot, the two worlds must be byte-identical on every
subject-facing surface: the read view, the living-traces hints, the objective
opportunity scan, and the zero-write co-placement preview.

They also cover:

* sealed carriers -- a sealed body and its hidden contents never leak into the
  subject projection, hints, scan or preview;
* an honest reach-miss -- a 409 ``OBJECT_NOT_AT_EXPECTED_SPOT`` that names only
  the believed spot and never the real ``parent_id``, followed by real discovery;
* the shared ``state_tx_guard``: concurrent read-modify-write, submission-order
  (``op_seq``) receipt eviction and long-horizon retry reconciliation
  (``already_applied`` / ``RETRY_WINDOW_EXPIRED``).

Run (WSL): ``python -m unittest tests.test_v08_two_worlds_non_interference -v``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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

from components.phone import v08_living_traces_view as traces_view  # noqa: E402
from components.world.scripts import v08_universal_matter as engine  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"


def _stable(payload: object) -> str:
    """A canonical string so ``==`` really means byte-identical."""

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _write_matter_state(root: Path, doc: dict) -> None:
    path = engine.state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _sealed_world(root: Path, inscription: str) -> None:
    """A sealed carrier with a hidden body/child; only ``inscription`` differs."""

    carrier = engine._object_row(
        "sealed_carrier", "封好的纸封", item_kind="paper", parent_id="home_desk",
        capacity_slots=2, paper_like=True,
    )
    carrier.update({
        "sealed": True,
        "unsealed_for_subject": False,
        "inscribed_text_cn": inscription,
    })
    child = engine._object_row(
        "hidden_note", "里面的小纸条", item_kind="paper",
        parent_id="sealed_carrier", paper_like=True,
    )
    child["inscribed_text_cn"] = inscription
    _write_matter_state(root, {
        "schema_version": engine.SCHEMA_VERSION,
        "revision": 1,
        "subject_view_revision": 1,
        "next_op_seq": 0,
        "committed_op_ring": [],
        "evicted_up_to_revision": 0,
        "matter_objects": {
            "sealed_carrier": carrier,
            "hidden_note": child,
        },
        "recent_acts": [],
        "operations": {},
        "discards": [],
    })


class TwoWorldsNonInterferenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-two-worlds-")
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _world(self, name: str, *, move_cup_to_fridge: bool) -> Path:
        root = self.base / name
        root.mkdir(parents=True, exist_ok=True)
        # One identical subject act so both worlds own a real state file and a
        # non-empty objective opportunity (a room-temperature perishable).
        engine.compose_open_act(root, {
            "intent_cn": "把一盒豆腐放在备餐台上",
            "steps": [{
                "op": "INTRODUCE_ITEM", "object_id": "tofu_01", "label_cn": "豆腐",
                "item_kind": "ingredient", "target_parent_id": "kitchen_counter",
                "shelf_life_hours": 24,
            }],
        }, now_iso=T0)
        if move_cup_to_fridge:
            # Reality diverges: the partner moves the cup the subject last saw
            # on the desk into the fridge.  The subject's head must not follow.
            engine.partner_move_shared_object(
                root,
                {"object_id": "ceramic_cup_01", "target_parent_id": "home_fridge"},
                now_iso=T0,
            )
        return root

    def _subject_surfaces(self, root: Path, preview_payload: dict) -> dict:
        space = engine.read_matter_space_view(root, actor_side="subject", now_iso=T0)
        preview = engine.preview_placement_zero_write(
            root, preview_payload, actor_side="subject", now_iso=T0,
        )
        return {
            "view": space,
            "hints": traces_view._matter_hints(space, include_private=True),
            "context_hints": traces_view.summarize_living_traces_for_context(
                root, include_private=True, now_iso=T0
            )["matter_hints"],
            "scan": engine.scan_matter_opportunities_zero_write(
                root, actor_side="subject", now_iso=T0
            ),
            "preview": preview,
        }

    # -- 1. parallel worlds ---------------------------------------------

    def test_01_hidden_partner_move_is_byte_identical_until_miss_or_observation(self) -> None:
        a = self._world("world-a", move_cup_to_fridge=False)
        b = self._world("world-b", move_cup_to_fridge=True)

        cup_preview = {"object_id": "ceramic_cup_01", "target_parent_id": "home_fridge"}
        surfaces_a = self._subject_surfaces(a, cup_preview)
        surfaces_b = self._subject_surfaces(b, cup_preview)
        self.assertEqual(
            _stable(surfaces_a), _stable(surfaces_b),
            "a hidden partner move leaked into a subject-facing surface",
        )

        # No stale flag, and no God-view spot, ever appears in the subject view.
        space = surfaces_a["view"]
        for row in space["objects"]:
            self.assertNotIn("impression_stale", row)
        cup = next(row for row in space["objects"] if row["object_id"] == "ceramic_cup_01")
        self.assertEqual(cup["parent_id"], "home_desk")

        # The partner God-view does see the truth.
        partner = engine.read_matter_space_view(b, actor_side="partner", now_iso=T0)
        partner_cup = next(r for r in partner["objects"] if r["object_id"] == "ceramic_cup_01")
        self.assertEqual(partner_cup["parent_id"], "home_fridge")
        self.assertTrue(partner_cup["impression_stale"])

    def test_02_sealed_body_is_byte_identical_and_never_leaks(self) -> None:
        root_a = self.base / "sealed-a"
        root_b = self.base / "sealed-b"
        _sealed_world(root_a, "secret-A")
        _sealed_world(root_b, "secret-B")

        sealed_preview = {"target_parent_id": "sealed_carrier", "item_kind": "everyday"}
        surfaces_a = self._subject_surfaces(root_a, sealed_preview)
        surfaces_b = self._subject_surfaces(root_b, sealed_preview)
        self.assertEqual(
            _stable(surfaces_a), _stable(surfaces_b),
            "the sealed body leaked into a subject-facing surface",
        )

        blob = _stable(surfaces_a)
        self.assertNotIn("secret-A", blob)
        self.assertNotIn("secret-B", blob)

        # The sealed carrier is visible; its body and contents are not.
        view = surfaces_a["view"]
        ids = {row["object_id"] for row in view["objects"]}
        self.assertIn("sealed_carrier", ids)
        self.assertNotIn("hidden_note", ids)
        carrier = next(row for row in view["objects"] if row["object_id"] == "sealed_carrier")
        self.assertIsNone(carrier["inscribed_text_cn"])
        self.assertEqual(carrier["occupancy"], 0)
        self.assertEqual(carrier["children_ids"], [])

        # Preview never probes the sealed internals nor the hidden child.
        preview = engine.preview_placement_zero_write(
            root_a, {"target_parent_id": "sealed_carrier", "item_kind": "everyday"},
            actor_side="subject", now_iso=T0,
        )
        self.assertTrue(preview["can_place"])
        self.assertEqual(preview["slots_used_before"], 0)
        with self.assertRaises(engine.V08MatterError) as ctx:
            engine.preview_placement_zero_write(
                root_a, {"object_id": "hidden_note", "target_parent_id": "home_desk"},
                actor_side="subject", now_iso=T0,
            )
        self.assertEqual(ctx.exception.reason_code, "OBJECT_NOT_FOUND")

        # The scan never advertises the hidden child either.
        scan = engine.scan_matter_opportunities_zero_write(root_a, actor_side="subject", now_iso=T0)
        self.assertNotIn("hidden_note", _stable(scan))

    # -- 3. honest reach-miss -------------------------------------------

    def test_03_reach_miss_names_only_the_believed_spot_then_reveals_truth(self) -> None:
        root = self._world("miss-world", move_cup_to_fridge=True)

        with self.assertRaises(engine.V08MatterError) as ctx:
            engine.compose_open_act(root, {
                "intent_cn": "顺手去书桌上拿杯子",
                "steps": [{"op": "PLACE_OR_MOVE", "object_id": "ceramic_cup_01",
                           "target_parent_id": "home_desk"}],
            }, now_iso=T0)
        error = ctx.exception
        self.assertEqual(error.reason_code, "OBJECT_NOT_AT_EXPECTED_SPOT")
        self.assertEqual(error.status_code, 409)
        # The believed spot is named; the real spot is never leaked.
        self.assertIn("书桌台面", error.message)
        self.assertNotIn("home_fridge", error.message)
        self.assertNotIn("共享冰箱", error.message)

        state = engine._read_state_zero_write(root)
        cup = state["matter_objects"]["ceramic_cup_01"]
        self.assertFalse(cup["impression_stale"])
        self.assertTrue(cup["not_at_believed_spot"])
        self.assertEqual(cup["last_miss_parent"], "home_desk")
        # No receipt / recent act carries the real spot either.
        self.assertEqual(_stable(state.get("operations") or {}).count("home_fridge"), 0)
        self.assertNotIn("home_fridge", _stable(state.get("recent_acts") or []))

        # A repeated grab is still an honest miss, not a new oracle.
        with self.assertRaises(engine.V08MatterError) as again:
            engine.compose_open_act(root, {
                "intent_cn": "再摸一下桌面",
                "steps": [{"op": "PLACE_OR_MOVE", "object_id": "ceramic_cup_01",
                           "target_parent_id": "home_desk"}],
            }, now_iso=T0)
        self.assertEqual(again.exception.reason_code, "OBJECT_NOT_AT_EXPECTED_SPOT")

        # Real discovery: she looks where it actually went.
        engine.compose_open_act(root, {
            "intent_cn": "拉开冰箱看看",
            "steps": [{"op": "OBSERVE_OBJECT", "object_id": "ceramic_cup_01"}],
        }, now_iso=T0)
        revealed = engine.read_matter_space_view(root, actor_side="subject", now_iso=T0)
        revealed_cup = next(r for r in revealed["objects"] if r["object_id"] == "ceramic_cup_01")
        self.assertEqual(revealed_cup["parent_id"], "home_fridge")
        self.assertNotIn("impression_stale", revealed_cup)
        raw = engine._read_state_zero_write(root)["matter_objects"]["ceramic_cup_01"]
        self.assertFalse(raw["not_at_believed_spot"])
        self.assertIsNone(raw["last_miss_parent"])

    # -- 4. transaction guard under contention --------------------------

    def test_04_concurrent_rmw_keeps_every_commit(self) -> None:
        root = self.base / "concurrent"
        root.mkdir(parents=True, exist_ok=True)

        def _bring(index: int) -> dict:
            return engine.compose_open_act(root, {
                "intent_cn": f"把第{index}罐放进冰箱",
                "steps": [{
                    "op": "INTRODUCE_ITEM", "object_id": f"jar_{index}",
                    "label_cn": f"第{index}罐", "item_kind": "everyday",
                    "target_parent_id": "home_fridge",
                }],
                "operation_key": f"concurrent-bring-{index}",
            }, now_iso=T0)

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(_bring, range(6)))

        self.assertTrue(all(r["status"] == "applied" for r in results))
        state = engine._read_state_zero_write(root)
        for index in range(6):
            self.assertIn(f"jar_{index}", state["matter_objects"])
        self.assertEqual(state["revision"], 6)
        self.assertEqual(state["subject_view_revision"], 6)
        self.assertEqual(len(state["operations"]), 6)
        op_seqs = sorted(row["op_seq"] for row in state["operations"].values())
        self.assertEqual(op_seqs, [1, 2, 3, 4, 5, 6])

    def test_05_op_seq_eviction_is_submission_ordered_and_retry_is_honest(self) -> None:
        state = engine._default_state()
        state["revision"] = 500
        # Submit in reverse-key order so lexicographic ordering can never pass
        # for submission ordering.
        for index in range(205):
            key = f"op-{204 - index:03d}"
            engine._store_operation(
                state,
                operation_key=key,
                fingerprint=f"fp-{key}",
                receipt={"status": "applied", "action": "noop", "operation_key": key},
                committed_at=T0,
            )

        engine._trim_collections(state)
        ops = state["operations"]
        self.assertEqual(len(ops), 200)
        # The five oldest submissions (largest keys) are gone, the rest stay.
        for index in range(200, 205):
            self.assertNotIn(f"op-{index:03d}", ops)
        self.assertIn("op-199", ops)
        self.assertIn("op-000", ops)
        self.assertEqual(state["evicted_up_to_revision"], 500)

        # A replay long after the row was trimmed is reconciled, not re-applied.
        replay = engine._check_replay(
            state,
            operation_key="op-204",
            fingerprint="fp-op-204",
            root=None,
        )
        self.assertIsNotNone(replay)
        self.assertTrue(replay["already_applied"])

        # Past the retention window an ancient retry is explicitly expired.
        with self.assertRaises(engine.V08MatterError) as ctx:
            engine._check_replay(
                state,
                operation_key="never-committed",
                fingerprint="fp-never",
                root=None,
                expected_revision=0,
            )
        self.assertEqual(ctx.exception.reason_code, "RETRY_WINDOW_EXPIRED")


if __name__ == "__main__":
    unittest.main(verbosity=2)

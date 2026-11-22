#!/usr/bin/env python3
"""Concurrent read-modify-write boundaries for character pack, sensory cards and body.

Coverage:
* concurrent ``character_pack_state.json`` identity / optional-physiology updates keep
  every revision (strictly increasing, no lost update, JSON never corrupt);
* concurrent ``sensory_cards.json`` sound-card / partner-note appends serialize through
  ``state_tx_guard.state_file_lock`` with zero loss;
* concurrent ``v08_composable_body`` care offers serialize, the same ``operation_key``
  replays idempotently, evicted receipts replay from ``committed_op_ring`` in true
  submission order, and pure reads stay 100% zero-write.

Run (Windows): ``python -B -m unittest tests.test_v08_rmw_character_body_concurrency -v``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import state_tx_guard as tx_guard  # noqa: E402
from components.phone import sensory_character_view as scv  # noqa: E402
from components.world.scripts import v08_composable_body as engine  # noqa: E402

NOW = "2026-11-16T12:00:00+08:00"


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


def _run_threads(
    target: Callable[[int], Any], count: int
) -> tuple[list[Any], list[BaseException]]:
    """Start ``count`` threads that hit the same barrier, then call ``target(i)``."""

    results: list[Any] = [None] * count
    errors: list[BaseException] = [None] * count  # type: ignore[list-item]
    barrier = threading.Barrier(count, timeout=10)

    def _worker(index: int) -> None:
        try:
            barrier.wait()
            results[index] = target(index)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the caller
            errors[index] = exc

    threads = [threading.Thread(target=_worker, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results, errors


class StateFileLockNestedReentryTest(unittest.TestCase):
    """Regression: a fresh, not-yet-created parent must not split the lock key.

    On Windows Python 3.13 ``Path.resolve()`` returns a plain ``C:\\...`` path while
    ``phone/`` is missing and an extended-length ``\\\\?\\C:\\...`` path once the outer
    ``state_file_lock`` creates that directory.  Two keys meant two ``_PathLock``
    entries, so a same-thread nested acquire dropped to depth 1 and re-locked its own
    ``msvcrt`` byte forever.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="q2-tx-guard-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_canonical_key_is_stable_across_parent_creation(self) -> None:
        state = self.root / "phone" / "state.json"
        before = tx_guard._canonical_lock_key(state)
        state.parent.mkdir(parents=True, exist_ok=True)
        after = tx_guard._canonical_lock_key(state)
        self.assertEqual(before, after)
        self.assertIs(tx_guard._path_entry(state), tx_guard._path_entry(state))

    def test_nested_lock_in_fresh_directory_does_not_self_deadlock(self) -> None:
        state = self.root / "phone" / "state.json"
        self.assertFalse(state.parent.exists(), "parent must start missing")
        observed: dict[str, Any] = {}

        def _nested() -> None:
            with tx_guard.state_file_lock(state) as outer:
                with tx_guard.state_file_lock(state) as inner:
                    observed["outer"] = outer
                    observed["inner"] = inner
                    observed["depth"] = tx_guard._path_entry(state).depth

        thread = threading.Thread(target=_nested, daemon=True)
        thread.start()
        thread.join(timeout=15)
        self.assertFalse(thread.is_alive(), "nested state_file_lock self-deadlocked")
        self.assertEqual(observed.get("outer"), observed.get("inner"))
        self.assertEqual(observed.get("depth"), 2)
        self.assertTrue(observed.get("outer", Path()).exists())


class CharacterPackConcurrencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="q2-char-pack-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_concurrent_identity_and_physiology_keep_every_revision(self) -> None:
        count = 16

        def target(index: int) -> dict[str, Any]:
            if index % 2 == 0:
                return scv.apply_character_pack_action(
                    self.root,
                    {"action": "update_identity", "display_name": f"角色-{index:02d}"},
                    now_iso=NOW,
                )
            processes = ["menstruation_lite"] if index % 4 == 1 else []
            return scv.apply_character_pack_action(
                self.root,
                {"action": "set_optional_physiology", "optional_processes": processes},
                subject_view=True,
                now_iso=NOW,
            )

        results, errors = _run_threads(target, count)
        self.assertEqual([exc for exc in errors if exc is not None], [])
        ok_results = [row for row in results if isinstance(row, dict) and row.get("ok")]
        self.assertEqual(len(ok_results), count)

        revisions = sorted(int(row["data"]["revision"]) for row in ok_results)
        self.assertEqual(revisions, list(range(1, count + 1)), "no revision lost")

        state, reason = scv.read_character_pack_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(state["revision"], count)
        self.assertIn(
            state["pack_doc"]["display_name"],
            {f"角色-{index:02d}" for index in range(0, count, 2)},
        )
        self.assertIn(state["optional_processes"], ([], ["menstruation_lite"]))

        raw = scv.character_pack_state_path(self.root).read_text(encoding="utf-8")
        self.assertEqual(json.loads(raw)["revision"], count, "JSON must stay valid")

    def test_export_pack_is_zero_write_and_lock_free(self) -> None:
        scv.apply_character_pack_action(
            self.root,
            {"action": "update_identity", "display_name": "零写入"},
            now_iso=NOW,
        )
        before = _snapshot(self.root)
        exported = scv.apply_character_pack_action(
            self.root, {"action": "export_pack"}, now_iso=NOW
        )
        self.assertTrue(exported.get("ok"))
        self.assertEqual(before, _snapshot(self.root), "export is a pure read")


class SensoryCardsConcurrencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="q2-sensory-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_concurrent_sound_cards_and_notes_zero_loss(self) -> None:
        notes = scv.MAX_PARTNER_SENSORY_NOTES
        cards = 8
        projection = {
            "world": {
                "status": "available",
                "place_id": "room_1",
                "place_label": "房间",
            },
            "channels": {
                "auditory": {
                    "local_sounds": [{"summary": "窗外有雨声"}],
                    "adjacent_unseen_sounds": [],
                    "window_state": "open",
                }
            },
        }

        def target(index: int) -> dict[str, Any]:
            if index < notes:
                return scv.apply_sensory_action(
                    self.root,
                    {"action": "leave_sensory_note", "text": f"感官小记-{index}"},
                    now_iso=NOW,
                )
            card_index = index - notes
            return scv.apply_sensory_action(
                self.root,
                {
                    "action": "capture_sound_card",
                    "title": f"声音-{card_index}",
                    "operation_key": f"op-card-{card_index}",
                },
                sensory_projection=projection,
                now_iso=NOW,
            )

        results, errors = _run_threads(target, notes + cards)
        self.assertEqual([exc for exc in errors if exc is not None], [])
        ok_results = [row for row in results if isinstance(row, dict) and row.get("ok")]
        self.assertEqual(len(ok_results), notes + cards)

        revisions = sorted(int(row["data"]["revision"]) for row in ok_results)
        self.assertEqual(revisions, list(range(1, notes + cards + 1)))

        state, reason = scv.read_sensory_cards_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(state["revision"], notes + cards)
        self.assertEqual(len(state["partner_sensory_notes"]), notes)
        self.assertEqual(len(state["sound_cards"]), cards)
        self.assertEqual(
            sorted(note["text"] for note in state["partner_sensory_notes"]),
            sorted(f"感官小记-{index}" for index in range(notes)),
        )
        card_ids = [card["card_id"] for card in state["sound_cards"]]
        self.assertEqual(len(card_ids), len(set(card_ids)))

        raw = scv.sensory_cards_state_path(self.root).read_text(encoding="utf-8")
        self.assertEqual(json.loads(raw)["revision"], notes + cards, "JSON must stay valid")


class ComposableBodyConcurrencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="q2-body-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self) -> dict[str, Any]:
        return json.loads(engine.state_path(self.root).read_text(encoding="utf-8"))

    def test_concurrent_care_offers_zero_loss(self) -> None:
        count = 12

        def target(index: int) -> dict[str, Any]:
            return engine.offer_ordinary_care(
                self.root,
                {
                    "action": "offer_ordinary_care",
                    "care_kind": "warm_drink",
                    "note_cn": f"热饮-{index}",
                    "operation_key": f"care-{index}",
                },
                actor_side="partner",
                now_iso=NOW,
            )

        results, errors = _run_threads(target, count)
        self.assertEqual([exc for exc in errors if exc is not None], [])
        self.assertEqual(len([row for row in results if isinstance(row, dict)]), count)

        state = self._state()
        self.assertEqual(state["revision"], count)
        self.assertEqual(len(state["partner_care_log"]), count)
        self.assertEqual(
            {row["note_cn"] for row in state["partner_care_log"]},
            {f"热饮-{index}" for index in range(count)},
        )
        sequences = sorted(
            int(row["op_seq"]) for row in state["operations"].values()
        )
        self.assertEqual(sequences, list(range(1, count + 1)))

    def test_same_operation_key_replays_without_duplicate_side_effect(self) -> None:
        payload = {
            "action": "offer_ordinary_care",
            "care_kind": "fresh_supplies",
            "note_cn": "同一条关怀",
            "operation_key": "same-operation-key",
        }

        def target(_index: int) -> dict[str, Any]:
            return engine.offer_ordinary_care(
                self.root, dict(payload), actor_side="partner", now_iso=NOW
            )

        results, errors = _run_threads(target, 8)
        self.assertEqual([exc for exc in errors if exc is not None], [])
        applied = [row for row in results if not row.get("already_applied")]
        replayed = [row for row in results if row.get("already_applied")]
        self.assertEqual(len(applied), 1)
        self.assertEqual(len(replayed), 7)

        state = self._state()
        self.assertEqual(state["revision"], 1)
        self.assertEqual(len(state["partner_care_log"]), 1)

    def test_evicted_receipts_replay_by_op_seq_order(self) -> None:
        original = engine.MAX_OPERATION_RECEIPTS
        engine.MAX_OPERATION_RECEIPTS = 3
        try:
            keys = ["z-first", "y-second", "x-third", "w-fourth", "v-fifth"]
            for key in keys:
                engine.configure_body_pack(
                    self.root,
                    {
                        "action": "configure_body_pack",
                        "pack_id": "nocturnal_emission",
                        "enabled": False,
                        "operation_key": key,
                    },
                    now_iso=NOW,
                )
            state = self._state()
            self.assertLessEqual(len(state["operations"]), 3)
            # Submission order, not alphabetical order: "v-fifth" is newest and must stay.
            self.assertEqual(set(state["operations"]), {"x-third", "w-fourth", "v-fifth"})
            self.assertEqual(
                {row["operation_key"] for row in state["committed_op_ring"]},
                {"z-first", "y-second"},
            )

            before = _snapshot(self.root)
            replay = engine.configure_body_pack(
                self.root,
                {
                    "action": "configure_body_pack",
                    "pack_id": "nocturnal_emission",
                    "enabled": False,
                    "operation_key": "z-first",
                },
                now_iso=NOW,
            )
            self.assertTrue(replay.get("already_applied"))
            self.assertEqual(before, _snapshot(self.root), "evicted replay adds no write")
        finally:
            engine.MAX_OPERATION_RECEIPTS = original

    def test_read_paths_are_strictly_zero_write(self) -> None:
        fresh = self.root / "fresh"
        fresh.mkdir()
        before = _snapshot(fresh)
        engine.read_composable_body_zero_write(
            fresh, has_subject_capability=True, now_iso=NOW
        )
        engine.read_composable_body_zero_write(
            fresh, has_subject_capability=False, now_iso=NOW
        )
        engine.project_composable_body_zero_write(
            fresh, has_subject_capability=True, now_iso=NOW
        )
        self.assertEqual(before, _snapshot(fresh))
        self.assertFalse((fresh / "body").exists())


if __name__ == "__main__":
    unittest.main()

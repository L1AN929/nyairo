#!/usr/bin/env python3
"""Round O1 tests: stance_containment single-writer RMW + long-horizon replay.

Coverage:
* two concurrent writers carrying the same ``expected_revision`` against
  ``<root>/phone/stance_containment.json``: exactly one applies, the other gets a
  real 409 ``REVISION_CONFLICT`` after the lock is held and the state is re-read
  from disk (the first write is never silently overwritten);
* a key pushed out of the 64-entry active receipt ledger replays from the bounded
  ``committed_op_ring`` with ``already_applied`` and produces no second dish;
* the same key with a different payload stays a 409 ``OPERATION_KEY_CONFLICT``;
* an unknown key older than the eviction watermark is refused as
  ``RETRY_WINDOW_EXPIRED`` instead of being applied as a fresh request.

Run on Windows:
``python -B -m unittest tests.test_v08_rmw_stance_concurrency tests.test_stance_containment_view -v``
"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.phone import stance_containment_view as view  # noqa: E402

NOW = "2026-11-16T09:00:00+08:00"


def _pages_payload(scope_ref: str, operation_key: str, *, expected: int = 0) -> dict[str, Any]:
    return {
        "action": "record_obtained_pages",
        "scope_ref": scope_ref,
        "obtained_pages": [1, 2],
        "expected_revision": expected,
        "operation_key": operation_key,
    }


def _dish_payload(operation_key: str, *, expected: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "prepare_table_dish",
        "title": "一盘小饼干",
        "kind": "snack_plate",
        "portions": 3,
        "operation_key": operation_key,
    }
    if expected is not None:
        payload["expected_revision"] = expected
    return payload


class ConcurrentRevisionTest(unittest.TestCase):
    def test_same_expected_revision_has_exactly_one_winner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="o1-stance-rmw-") as tmp:
            root = Path(tmp)
            barrier = threading.Barrier(2)
            outcomes: list[tuple[str, str]] = []
            guard = threading.Lock()

            def attempt(scope_ref: str, operation_key: str) -> None:
                barrier.wait(timeout=10)
                try:
                    view.apply_stance_containment_action(
                        root,
                        _pages_payload(scope_ref, operation_key, expected=0),
                        now=NOW,
                        subject_view=True,
                    )
                    verdict = ("ok", "")
                except Exception as exc:  # noqa: BLE001 - recorded for the assertion
                    verdict = ("err", getattr(exc, "reason_code", repr(exc)))
                with guard:
                    outcomes.append(verdict)

            threads = [
                threading.Thread(target=attempt, args=("book-a", "op-concurrent-a")),
                threading.Thread(target=attempt, args=("book-b", "op-concurrent-b")),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
                self.assertFalse(thread.is_alive())

            self.assertEqual(
                sum(1 for kind, _code in outcomes if kind == "ok"), 1, outcomes
            )
            self.assertEqual(
                [code for kind, code in outcomes if kind == "err"],
                ["REVISION_CONFLICT"],
                outcomes,
            )

            state, reason = view.read_stance_containment_state(root)
            self.assertIsNone(reason)
            self.assertEqual(state["revision"], 1)
            self.assertEqual(len(state["operation_receipts"]), 1)
            self.assertEqual(
                sum(
                    len(entry.get("obtained_pages") or [])
                    for entry in state["stance_domain"]["obtained_pages_by_scope"].values()
                ),
                2,
            )


class LongHorizonReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="o1-stance-retry-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_evicted_dish_key_replays_without_second_dish(self) -> None:
        created = view.apply_stance_containment_action(
            self.root,
            _dish_payload("op-dish-first"),
            now=NOW,
            subject_view=True,
        )
        self.assertEqual(created["status"], "applied")
        first_revision = created["revision"]
        self.assertEqual(len(created["shared_desk_table"]["table_dishes"]), 1)

        # Push the original receipt out of the 64-entry active ledger while its
        # entry is still inside the 256-entry commit ring.
        for index in range(view.MAX_OPERATION_RECEIPTS + 6):
            view.apply_stance_containment_action(
                self.root,
                _pages_payload(f"scope-{index}", f"op-filler-{index}", expected=first_revision + index),
                now=NOW,
                subject_view=True,
            )

        state, reason = view.read_stance_containment_state(self.root)
        self.assertIsNone(reason)
        active_keys = {rec["operation_key"] for rec in state["operation_receipts"]}
        ring_keys = {rec["operation_key"] for rec in state["committed_op_ring"]}
        self.assertNotIn("op-dish-first", active_keys)
        self.assertIn("op-dish-first", ring_keys)
        revision_before_replay = state["revision"]
        self.assertEqual(len(state["desk_table_domain"]["table_dishes"]), 1)

        replayed = view.apply_stance_containment_action(
            self.root,
            _dish_payload("op-dish-first", expected=first_revision),
            now=NOW,
            subject_view=True,
        )
        self.assertEqual(replayed["status"], "already_applied")
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["revision"], revision_before_replay)
        # No second plate of biscuits was cooked: idempotent ring replay.
        self.assertEqual(len(replayed["shared_desk_table"]["table_dishes"]), 1)

        # Same key, different payload -> explicit conflict, never a replay.
        changed = _dish_payload("op-dish-first", expected=first_revision)
        changed["title"] = "另一盘点心"
        with self.assertRaises(view.StanceContainmentConflictError) as ctx:
            view.apply_stance_containment_action(
                self.root, changed, now=NOW, subject_view=True
            )
        self.assertEqual(ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

        # Unknown key older than the eviction watermark -> explicit expired retry.
        with self.assertRaises(view.StanceContainmentConflictError) as ctx2:
            view.apply_stance_containment_action(
                self.root,
                _dish_payload("op-ancient", expected=0),
                now=NOW,
                subject_view=True,
            )
        self.assertEqual(ctx2.exception.reason_code, "RETRY_WINDOW_EXPIRED")

        final_state, final_reason = view.read_stance_containment_state(self.root)
        self.assertIsNone(final_reason)
        self.assertEqual(len(final_state["desk_table_domain"]["table_dishes"]), 1)


if __name__ == "__main__":
    unittest.main()

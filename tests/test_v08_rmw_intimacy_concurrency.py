#!/usr/bin/env python3
"""Round Q1 tests: adult_intimacy single-writer RMW + bounded commit-order replay.

Coverage:
* a concurrent ``stop_scene`` / ``revoke_intimacy_consent`` always wins over a
  concurrent ``advance_beat``: the final permit is ``STOPPED`` and a late beat
  can never revive it;
* concurrent writers never lose revision increments and never corrupt the
  ``<root>/phone/adult_intimacy.json`` document;
* once more than ``MAX_IDEMPOTENCY_CACHE`` distinct keys are written, the oldest
  key is evicted by ``op_seq`` (never alphabetically) into the bounded
  ``committed_op_ring`` and a replay of it still returns ``idempotent_replay``
  without a second side effect; the same key with a different payload stays a
  409 ``EXECUTION_ID_REUSED``.

Run on Windows:
``python -B -m unittest tests.test_v08_rmw_intimacy_concurrency tests.test_adult_intimacy_view -v``
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
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

from components.phone import adult_intimacy_view as view  # noqa: E402

T0 = datetime(2026, 11, 16, 21, 0, 0, tzinfo=timezone.utc)


def _open_ready_env(root: Path) -> None:
    """Deployer-attested gate + seed + route + partner limits, mirroring the view tests."""
    view.apply_adult_intimacy_action(
        root,
        {
            "action": "configure_adult_gate",
            "adult_confirmed": True,
            "life_stage": "working_adult",
            "attested_by": "deployer",
            "feature_enabled": True,
            "deploy_tier_cap": "EXPLICIT",
        },
        now=T0,
    )
    view.apply_adult_intimacy_action(
        root, {"action": "configure_body_profile", "preset": "neutral"}, now=T0
    )
    view.apply_adult_intimacy_action(
        root,
        {
            "action": "confirm_preference_seed",
            "attested_by": "deployer",
            "desire_baseline": "moderate",
            "stances": {
                "KISSING": "YES",
                "CARESSING_CLOTHED:user_to_subject": "YES",
                "INTERCOURSE": "YES",
            },
        },
        now=T0,
    )
    view.apply_adult_intimacy_action(
        root,
        {
            "action": "register_render_route",
            "route_id": "adult_verified_route",
            "max_tier": "EXPLICIT",
            "adult_content_allowed": True,
            "policy_basis": "deployer_verified_adult_route",
            "set_default": True,
        },
        now=T0,
    )
    view.apply_adult_intimacy_action(
        root,
        {
            "action": "update_partner_limits",
            "allow_subject_initiation": True,
            "preferred_tier": "EXPLICIT",
            "stances": {
                "KISSING": "YES",
                "CARESSING_CLOTHED:user_to_subject": "YES",
                "INTERCOURSE": "YES",
            },
        },
        now=T0,
    )


def _propose_and_accept(root: Path) -> str:
    proposed = view.apply_adult_intimacy_action(
        root,
        {
            "action": "propose_scene",
            "operation_key": "q1_propose",
            "proposer": "partner",
            "categories": ["KISSING"],
        },
        now=T0,
    )
    pid = proposed["action_receipt"]["permit_id"]
    view.apply_adult_intimacy_action(
        root,
        {
            "action": "respond_scene",
            "permit_id": pid,
            "responder": "subject",
            "decision": "ACCEPT",
        },
        now=T0,
    )
    return pid


def _read_state(root: Path) -> dict[str, Any]:
    path = view.adult_intimacy_state_path(root)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class StopAndRevocationRaceTests(unittest.TestCase):
    def _run_race(self, root: Path, pid: str, stop_payload: dict[str, Any]) -> list[str]:
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        guard = threading.Lock()

        def advance() -> None:
            barrier.wait(timeout=10)
            try:
                view.apply_adult_intimacy_action(
                    root,
                    {
                        "action": "advance_beat",
                        "permit_id": pid,
                        "operation_key": "q1_race_beat",
                        "categories": ["KISSING"],
                    },
                    now=T0 + timedelta(seconds=10),
                )
                verdict = "advanced"
            except view.AdultIntimacyError as exc:  # noqa: BLE001 - recorded for assertions
                verdict = f"blocked:{exc.reason_code}"
            with guard:
                outcomes.append(verdict)

        def stop() -> None:
            barrier.wait(timeout=10)
            view.apply_adult_intimacy_action(
                root, stop_payload, now=T0 + timedelta(seconds=10)
            )
            with guard:
                outcomes.append("stopped")

        threads = [threading.Thread(target=advance), threading.Thread(target=stop)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())
        return outcomes

    def test_stop_scene_racing_advance_beat_always_wins(self) -> None:
        with tempfile.TemporaryDirectory(prefix="q1-intimacy-stop-") as tmp:
            root = Path(tmp)
            _open_ready_env(root)
            pid = _propose_and_accept(root)

            outcomes = self._run_race(
                root,
                pid,
                {
                    "action": "stop_scene",
                    "permit_id": pid,
                    "by": "partner",
                    "transition": "STOPPED",
                },
            )
            self.assertIn("stopped", outcomes)

            state = _read_state(root)
            self.assertEqual(state["permits"]["records"][pid]["state"], "STOPPED")
            self.assertIsNone(state["permits"]["active_permit_id"])

            # A late beat after the stop must never revive the scene.
            with self.assertRaises(view.AdultIntimacyConflictError):
                view.apply_adult_intimacy_action(
                    root,
                    {
                        "action": "advance_beat",
                        "permit_id": pid,
                        "operation_key": "q1_late_beat",
                        "categories": ["KISSING"],
                    },
                    now=T0 + timedelta(seconds=20),
                )
            self.assertEqual(
                _read_state(root)["permits"]["records"][pid]["state"], "STOPPED"
            )

    def test_revoke_consent_racing_advance_beat_never_revives(self) -> None:
        with tempfile.TemporaryDirectory(prefix="q1-intimacy-revoke-") as tmp:
            root = Path(tmp)
            _open_ready_env(root)
            pid = _propose_and_accept(root)

            outcomes = self._run_race(
                root,
                pid,
                {
                    "action": "revoke_intimacy_consent",
                    "by": "partner",
                    "erase_memory": True,
                    "close_adult_gate": True,
                },
            )
            self.assertIn("stopped", outcomes)

            state = _read_state(root)
            rec = state["permits"]["records"][pid]
            self.assertEqual(rec["state"], "STOPPED")
            self.assertEqual(rec.get("end_reason"), "CONSENT_REVOKED")
            self.assertIsNone(state["permits"]["active_permit_id"])
            self.assertFalse(state["gate"]["adult_confirmed"])
            self.assertFalse(state["partner_limits"]["allow_subject_initiation"])

            # No new scene can be opened after a revocation raced to the front.
            with self.assertRaises(view.AdultIntimacyPermissionError):
                view.apply_adult_intimacy_action(
                    root,
                    {
                        "action": "propose_scene",
                        "operation_key": "q1_after_revoke",
                        "proposer": "partner",
                        "categories": ["KISSING"],
                    },
                    now=T0 + timedelta(seconds=20),
                )


class ConcurrentWriterRevisionTests(unittest.TestCase):
    def test_concurrent_writers_keep_revision_increments_and_valid_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="q1-intimacy-rev-") as tmp:
            root = Path(tmp)
            _open_ready_env(root)
            baseline = _read_state(root)["revision"]

            writers = 8
            barrier = threading.Barrier(writers)
            revisions: list[int] = []
            guard = threading.Lock()

            def write(index: int) -> None:
                barrier.wait(timeout=10)
                projection = view.apply_adult_intimacy_action(
                    root,
                    {
                        "action": "update_body_context",
                        "execution_id": f"q1-concurrent-{index}",
                        "fatigue": round(index / 20.0, 4),
                    },
                    now=T0 + timedelta(seconds=index),
                )
                with guard:
                    revisions.append(projection["revision"])

            threads = [
                threading.Thread(target=write, args=(index,))
                for index in range(writers)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
                self.assertFalse(thread.is_alive())

            self.assertEqual(len(revisions), writers)
            self.assertEqual(len(set(revisions)), writers, revisions)

            state = _read_state(root)  # 能解析即证明 JSON 未被并发写坏
            self.assertEqual(state["revision"], baseline + writers)
            cache = state["permits"]["idempotency_cache"]
            self.assertEqual(len(cache), writers)
            seqs = [row["op_seq"] for row in cache.values()]
            self.assertEqual(len(set(seqs)), writers, seqs)


class BoundedReplayTests(unittest.TestCase):
    def test_evicted_key_replays_from_ring_without_second_effect(self) -> None:
        with tempfile.TemporaryDirectory(prefix="q1-intimacy-ring-") as tmp:
            root = Path(tmp)
            _open_ready_env(root)

            first_payload = {
                "action": "update_body_context",
                "execution_id": "q1-op-first",
                "fatigue": 0.5,
            }
            first = view.apply_adult_intimacy_action(root, first_payload, now=T0)
            self.assertEqual(first["revision"], _read_state(root)["revision"])

            fillers = view.MAX_IDEMPOTENCY_CACHE + 12
            for index in range(fillers):
                view.apply_adult_intimacy_action(
                    root,
                    {
                        "action": "update_body_context",
                        "execution_id": f"q1-filler-{index}",
                        "fatigue": 0.25,
                    },
                    now=T0,
                )

            state = _read_state(root)
            cache = state["permits"]["idempotency_cache"]
            ring = state["permits"]["committed_op_ring"]
            self.assertEqual(len(cache), view.MAX_IDEMPOTENCY_CACHE)
            self.assertNotIn("q1-op-first", cache)
            self.assertIn("q1-op-first", {row["operation_key"] for row in ring})
            revision_before = state["revision"]
            fatigue_before = state["body"]["fatigue"]

            replayed = view.apply_adult_intimacy_action(
                root, dict(first_payload), now=T0
            )
            self.assertTrue(replayed["action_receipt"]["idempotent_replay"])
            self.assertEqual(replayed["revision"], revision_before)

            after = _read_state(root)
            self.assertEqual(after["revision"], revision_before)
            self.assertEqual(after["body"]["fatigue"], fatigue_before)

            changed = dict(first_payload)
            changed["fatigue"] = 0.95
            with self.assertRaises(view.AdultIntimacyConflictError) as ctx:
                view.apply_adult_intimacy_action(root, changed, now=T0)
            self.assertEqual(ctx.exception.reason_code, "EXECUTION_ID_REUSED")

            # Push the key out of the 256-entry ring as well: the ancient retry is
            # refused with an explicit "sync current state" window error instead of
            # being silently applied as a brand-new request.
            for index in range(fillers, fillers + view.MAX_COMMITTED_OP_RING):
                view.apply_adult_intimacy_action(
                    root,
                    {
                        "action": "update_body_context",
                        "execution_id": f"q1-filler-{index}",
                        "fatigue": 0.25,
                    },
                    now=T0,
                )
            ring_after = _read_state(root)["permits"]["committed_op_ring"]
            self.assertNotIn("q1-op-first", {row["operation_key"] for row in ring_after})
            with self.assertRaises(view.AdultIntimacyConflictError) as expired:
                view.apply_adult_intimacy_action(root, dict(first_payload), now=T0)
            self.assertEqual(expired.exception.reason_code, "RETRY_WINDOW_EXPIRED")


if __name__ == "__main__":
    unittest.main()

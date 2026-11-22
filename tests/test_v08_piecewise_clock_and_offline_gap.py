#!/usr/bin/env python3
"""Ch.43 §五–§六: piecewise event-boundary settlement & offline recovery report.

Covers ``components/life/scripts/v03_clock_evaporation.py``:

1. A 6h offline gap cut at T+2h by BOTH an ``evaporable`` TTL expiry and a
   caller regime change settles the expiry BEFORE the regime change at the
   same timestamp; ``piecewise_segments_count >= 2``; the structured
   ``offline_recovery_report`` is present and never fabricates activities.
2. A multi-day gap carrying more than ``MAX_CATCHUP_BOUNDARY_SLICES`` boundaries
   is capped at 24 segments with ``batch_coalesced == True`` and the result is
   deterministic across two independent roots (restart-safe).
3. Iron Laws 1 & 2: unresolved actions stay ``UNKNOWN`` (``guessed_by_clock ==
   False``) and ``cognitive_backfill_forbidden == True`` across the gap.
4. ``op_ledger`` eviction follows submission order ``op_seq`` (never the
   alphabetical key order produced by ``sort_keys=True``); evicted-but-retained
   keys replay idempotently, while keys older than the retry window return
   ``409 RETRY_WINDOW_EXPIRED``.
5. ``read_v03_clock_view`` stays 100% zero-write.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components.life.scripts import v03_clock_evaporation as clock  # noqa: E402

T0 = "2026-04-06T10:00:00+08:00"
T0_PLUS_2H = "2026-04-06T12:00:00+08:00"
T0_PLUS_6H = "2026-04-06T16:00:00+08:00"


class PiecewiseClockTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _advance(self, payload: dict, now_iso: str) -> tuple[int, dict]:
        status, envelope = clock.mutate_v03_clock_view(
            self.root, payload, actor_side="system", now_iso=now_iso
        )
        return status, envelope

    def _seed_clock_at_t0(self) -> None:
        status, envelope = self._advance(
            {"action": "advance_projection", "sync_v02": False}, T0
        )
        self.assertEqual(status, 200, envelope)
        self.assertTrue(envelope["ok"], envelope)

    def _read_state(self) -> dict:
        state, _exists = clock.read_v03_clock_state(self.root)
        return state

    # ------------------------------------------------------------------
    # 1. event-boundary ordering + offline recovery report
    # ------------------------------------------------------------------

    def test_01_expiry_settles_before_regime_change_at_same_boundary(self) -> None:
        self._seed_clock_at_t0()

        status, envelope = clock.mutate_v03_clock_view(
            self.root,
            {
                "action": "record_evaporable",
                "domain_kind": "fleeting_thought",
                "text": "窗外雨云压下来的一瞬",
                "ttl_seconds": 2 * 3600,
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(status, 200, envelope)
        self.assertEqual(
            envelope["data"]["accountability_partitions"]["evaporable_pool"]["active_count"], 1
        )

        status, envelope = clock.mutate_v03_clock_view(
            self.root,
            {
                "action": "advance_projection",
                "sync_v02": False,
                "boundary_events": [
                    {
                        "at_iso": T0_PLUS_2H,
                        "kind": "weather_shift",
                        "weather": "rain",
                        "airflow": "gusty",
                        "window_state": "closed",
                        "note_cn": "雨云压下来，窗边风势转急",
                    }
                ],
            },
            actor_side="system",
            now_iso=T0_PLUS_6H,
        )
        self.assertEqual(status, 200, envelope)
        receipt = envelope["data"]["action_receipt"]

        # 1) both the internal expiry and the caller regime change cut the gap
        self.assertEqual(receipt["evaporated_count"], 1)
        self.assertGreaterEqual(receipt["piecewise_segments_count"], 2)
        self.assertGreaterEqual(receipt["boundary_cuts_applied"], 1)

        log = receipt["piecewise_settlement_log"]
        expiry_indexes = [
            i for i, row in enumerate(log)
            if row["kind"] == "evaporable_expired" and row["at"] == T0_PLUS_2H
        ]
        regime_indexes = [
            i for i, row in enumerate(log)
            if row["kind"] == "regime_change" and row["at"] == T0_PLUS_2H
        ]
        self.assertTrue(expiry_indexes, log)
        self.assertTrue(regime_indexes, log)
        self.assertLess(
            expiry_indexes[0], regime_indexes[0],
            "due expiries at t_k must settle BEFORE the regime change at t_k",
        )

        # 2) structured report present in the receipt
        report = receipt["offline_recovery_report"]
        self.assertTrue(report["environmental_processes_settled"])
        self.assertGreaterEqual(report["piecewise_segments_count"], 2)
        self.assertGreaterEqual(report["boundary_cuts_applied"], 1)
        self.assertFalse(report["batch_coalesced"])
        self.assertTrue(report["character_activities_paused_at_breakpoint"])
        self.assertEqual(report["fabricated_offline_actions"], 0)
        self.assertIsInstance(report["recovery_summary_cn"], str)
        self.assertIn("未替角色补写", report["recovery_summary_cn"])

        # 3) the read projection exposes the settled report zero-write
        _status, read_env = clock.read_v03_clock_view(
            self.root, has_subject_capability=False, now_iso=T0_PLUS_6H
        )
        self.assertEqual(
            read_env["data"]["offline_recovery_report"], report
        )

    # ------------------------------------------------------------------
    # 2. bounded catch-up + determinism
    # ------------------------------------------------------------------

    def _run_many_boundaries(self, root: Path) -> dict:
        status, envelope = clock.mutate_v03_clock_view(
            root, {"action": "advance_projection", "sync_v02": False}, actor_side="system", now_iso=T0
        )
        self.assertEqual(status, 200, envelope)
        now_dt = clock._parse_iso(T0)
        events = [
            {
                "at_iso": (now_dt + clock.timedelta(hours=hour)).isoformat(timespec="seconds"),
                "kind": "weather_shift",
                "weather": "rain",
                "note_cn": f"第{hour}小时边界",
            }
            for hour in range(1, 31)
        ]
        target = (now_dt + clock.timedelta(hours=48)).isoformat(timespec="seconds")
        status, envelope = clock.mutate_v03_clock_view(
            root,
            {
                "action": "advance_projection",
                "sync_v02": False,
                "boundary_events": events,
            },
            actor_side="system",
            now_iso=target,
        )
        self.assertEqual(status, 200, envelope)
        return envelope["data"]

    def test_02_more_than_24_boundaries_coalesce_and_stay_deterministic(self) -> None:
        data_a = self._run_many_boundaries(self.root)
        receipt = data_a["action_receipt"]
        report = receipt["offline_recovery_report"]

        self.assertTrue(report["batch_coalesced"])
        self.assertEqual(report["piecewise_segments_count"], clock.MAX_CATCHUP_BOUNDARY_SLICES)
        self.assertEqual(report["boundary_cuts_applied"], clock.MAX_CATCHUP_BOUNDARY_SLICES - 1)
        self.assertEqual(report["fabricated_offline_actions"], 0)

        state_a = self._read_state()

        # Independent restart-equivalent root: identical inputs -> identical state.
        with tempfile.TemporaryDirectory() as other:
            data_b = self._run_many_boundaries(Path(other))
            state_b, _exists = clock.read_v03_clock_state(Path(other))

        self.assertEqual(data_b["action_receipt"]["offline_recovery_report"],
                         receipt["offline_recovery_report"])
        self.assertEqual(state_a, state_b)

    # ------------------------------------------------------------------
    # 3. Iron Laws 1 & 2 across the gap
    # ------------------------------------------------------------------

    def test_03_unknown_actions_never_guessed_and_cognition_never_backfilled(self) -> None:
        status, envelope = clock.mutate_v03_clock_view(
            self.root,
            {
                "action": "register_unknown_action",
                "action_id": "unk_01",
                "domain": "world.window",
                "summary": "出门前到底有没有关窗，记不清了",
            },
            actor_side="system",
            now_iso=T0,
        )
        self.assertEqual(status, 200, envelope)

        status, envelope = clock.mutate_v03_clock_view(
            self.root,
            {
                "action": "advance_projection",
                "sync_v02": False,
                "boundary_events": [
                    {"at_iso": T0_PLUS_2H, "kind": "weather_shift", "note_cn": "风声转急"}
                ],
            },
            actor_side="system",
            now_iso=T0_PLUS_6H,
        )
        self.assertEqual(status, 200, envelope)
        data = envelope["data"]

        pending = data["iron_laws"]["pending_unknown_actions"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["effect_state"], "UNKNOWN")
        self.assertFalse(pending[0]["guessed_by_clock"])
        self.assertTrue(data["iron_laws"]["cognitive_backfill_forbidden"])
        self.assertTrue(data["clocks"]["cognitive_clock"]["cognitive_backfill_forbidden"])
        self.assertEqual(data["clocks"]["cognitive_clock"]["causal_ticks"], 0)
        self.assertEqual(data["action_receipt"]["unknown_actions_guessed"], 0)
        self.assertEqual(data["action_receipt"]["cognitive_backfill_count"], 0)

        state = self._read_state()
        self.assertEqual(state["cognitive_clock"]["causal_ticks"], 0)
        self.assertTrue(state["cognitive_clock"]["cognitive_backfill_forbidden"])
        self.assertEqual(state["world_clock"]["pending_unknown_actions"][0]["effect_state"], "UNKNOWN")
        self.assertFalse(state["world_clock"]["pending_unknown_actions"][0]["guessed_by_clock"])

    # ------------------------------------------------------------------
    # 4. op_ledger eviction by op_seq + retry-window semantics
    # ------------------------------------------------------------------

    def _issue_op(self, key: str) -> tuple[int, dict]:
        return self._advance(
            {"action": "advance_projection", "sync_v02": False, "operation_key": key},
            T0,
        )

    def test_04_op_ledger_evicts_by_op_seq_not_alphabetical_key_order(self) -> None:
        # 138 older ops carry a "z_" prefix, 2 newer ones an "a_" prefix.  Because
        # state is persisted with sort_keys=True, a naive ``keys()[0]`` eviction
        # would throw away the NEWEST rows first; submission-order eviction must
        # keep them and drop the oldest z_ rows.
        for index in range(138):
            status, envelope = self._issue_op(f"z_{index:03d}")
            self.assertEqual(status, 200, envelope)
        for key in ("a_000", "a_001"):
            status, envelope = self._issue_op(key)
            self.assertEqual(status, 200, envelope)

        state = self._read_state()
        ledger = state["op_ledger"]
        self.assertEqual(len(ledger), clock.MAX_OP_LEDGER)
        self.assertIn("a_000", ledger)
        self.assertIn("a_001", ledger)
        self.assertNotIn("z_000", ledger)
        self.assertNotIn("z_011", ledger)

        seqs = sorted(int(row["op_seq"]) for row in ledger.values())
        # 140 committed ops -> op_seq 1..140; the newest 128 survive.
        self.assertEqual(seqs, list(range(140 - clock.MAX_OP_LEDGER + 1, 141)))
        self.assertGreater(state["evicted_up_to_revision"], 0)

        current_revision = state["revision"]

        # Evicted but still in the committed ring -> idempotent replay, no write.
        status, envelope = self._issue_op("z_000")
        self.assertEqual(status, 200, envelope)
        self.assertEqual(envelope["status"], "already_applied")
        self.assertTrue(envelope["data"]["action_receipt"]["already_applied"])
        self.assertEqual(
            clock.read_v03_clock_state(self.root)[0]["revision"], current_revision
        )

        # Active key replays idempotently as well.
        status, envelope = self._issue_op("a_001")
        self.assertEqual(status, 200, envelope)
        self.assertEqual(envelope["status"], "already_applied")
        self.assertEqual(
            clock.read_v03_clock_state(self.root)[0]["revision"], current_revision
        )

        # Simulate the ring and the journal also being pushed out: the retry is now
        # beyond the retained window and must be an explicit 409, never a new write.
        state_path = clock.v03_clock_state_path(self.root)
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        raw.pop("committed_op_ring", None)
        raw["evicted_up_to_revision"] = current_revision
        state_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        status, envelope = self._advance(
            {
                "action": "advance_projection",
                "sync_v02": False,
                "operation_key": "z_000",
                "expected_revision": 1,
            },
            T0,
        )
        self.assertEqual(status, 409, envelope)
        self.assertEqual(envelope["reason_code"], "RETRY_WINDOW_EXPIRED")
        self.assertEqual(
            clock.read_v03_clock_state(self.root)[0]["revision"], current_revision
        )

    # ------------------------------------------------------------------
    # 5. zero-write read
    # ------------------------------------------------------------------

    def test_05_read_view_is_100_percent_zero_write(self) -> None:
        self._seed_clock_at_t0()
        state_path = clock.v03_clock_state_path(self.root)
        before_bytes = state_path.read_bytes()
        before_listing = sorted(os.listdir(state_path.parent))

        status, envelope = clock.read_v03_clock_view(
            self.root, has_subject_capability=True, now_iso=T0_PLUS_6H
        )
        self.assertEqual(status, 200)
        self.assertTrue(envelope["ok"])
        self.assertIn("offline_recovery_report", envelope["data"])
        self.assertEqual(
            envelope["data"]["offline_recovery_report"]["fabricated_offline_actions"], 0
        )

        self.assertEqual(state_path.read_bytes(), before_bytes)
        self.assertEqual(sorted(os.listdir(state_path.parent)), before_listing)


if __name__ == "__main__":
    unittest.main()

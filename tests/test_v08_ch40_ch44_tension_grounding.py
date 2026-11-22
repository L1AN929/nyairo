#!/usr/bin/env python3
"""v0.8 Ch.40 / Ch.42 / Ch.44 grounding tests for ``emotional_tension_view``.

Covers three foundations:
1. cross-store ``SOURCE_GONE`` for ``matter:`` / ``v04_card:`` / ``v04_project:``
   refs, plus automatic read-projection release after the real source is gone;
2. ``state_tx_guard`` write serialisation with ordered receipt eviction and a
   bounded ``committed_op_ring`` that replays safely without a second effect;
3. ``scan_emotional_tension_cues_zero_write`` calm empty state and truthful,
   privacy-safe summary.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from components.phone import emotional_tension_view as et

BJT = timezone(timedelta(hours=8))


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _matter_doc(objects: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": 1, "matter_objects": objects}


class TensionGroundingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.t0 = datetime(2026, 11, 11, 10, 0, 0, tzinfo=BJT)
        self.t_free = datetime(2026, 11, 11, 16, 0, 0, tzinfo=BJT)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- helpers ---------------------------------------------------------

    def _matter_path(self, root: Path | None = None) -> Path:
        return (root or self.root) / "world" / "v08_matter_space.json"

    def _state_bytes(self, root: Path | None = None) -> bytes:
        return et.emotional_tension_state_path(root or self.root).read_bytes()

    # ---- 1. Ch.42 / Ch.44 §二 cross-store SOURCE_GONE -------------------

    def test_01_matter_source_gone_rejects_and_projection_drops(self) -> None:
        _write_json(
            self._matter_path(),
            _matter_doc({
                "obj-miss-1": {"object_id": "obj-miss-1", "label_cn": "海边捡的贝壳"},
                "obj-fav-1": {"object_id": "obj-fav-1", "label_cn": "未完成的画"},
            }),
        )

        note = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "add_missing_note",
                "kind": "FOUND",
                "source_ref": "matter:obj-miss-1",
                "summary": "捡到一个好看的贝壳",
            },
            now=self.t_free,
            subject_view=True,
        )
        self.assertEqual(note["status"], "applied")
        self.assertEqual(note["kept_count"], 1)

        favor = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "ask_partner_favor",
                "favor_kind": "look_once",
                "target_object_ref": "matter:obj-fav-1",
                "summary": "帮我看一眼这幅画",
            },
            now=self.t_free,
            subject_view=True,
        )
        self.assertEqual(favor["status"], "applied")
        self.assertEqual(
            favor["asking_partner"]["open_favor"]["target_object_ref"],
            "matter:obj-fav-1",
        )

        before = et.read_emotional_tension(
            self.root, now=self.t_free + timedelta(minutes=1), subject_view=True
        )
        self.assertEqual(len(before["missing_partner"]["kept_notes"]), 1)
        self.assertIsNotNone(before["asking_partner"]["open_favor"])

        # The real sources get consumed elsewhere; the projection must let go.
        state_bytes = self._state_bytes()
        phone_dir = self.root / "phone"
        listing = sorted(p.name for p in phone_dir.iterdir())
        _write_json(
            self._matter_path(),
            _matter_doc({
                "obj-miss-1": {
                    "object_id": "obj-miss-1",
                    "label_cn": "海边捡的贝壳",
                    "consumed": True,
                },
                "obj-fav-1": {
                    "object_id": "obj-fav-1",
                    "label_cn": "未完成的画",
                    "discarded": True,
                },
            }),
        )

        after = et.read_emotional_tension(
            self.root, now=self.t_free + timedelta(minutes=5), subject_view=True
        )
        self.assertEqual(after["missing_partner"]["kept_notes"], [])
        self.assertIsNone(after["asking_partner"]["open_favor"])
        # Read path wrote nothing to disk.
        self.assertEqual(self._state_bytes(), state_bytes)
        self.assertEqual(sorted(p.name for p in phone_dir.iterdir()), listing)

        # Fresh writes against a gone source are refused with 409 SOURCE_GONE.
        with self.assertRaises(et.EmotionalTensionConflictError) as ctx:
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "add_missing_note",
                    "kind": "FOUND",
                    "source_ref": "matter:obj-miss-1",
                    "summary": "再记一次",
                },
                now=self.t_free + timedelta(minutes=6),
                subject_view=True,
            )
        self.assertEqual(ctx.exception.reason_code, "SOURCE_GONE")
        self.assertEqual(ctx.exception.status_code, 409)

        with self.assertRaises(et.EmotionalTensionConflictError) as ctx:
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "ask_partner_favor",
                    "favor_kind": "look_once",
                    "target_object_ref": "matter:obj-fav-1",
                    "summary": "帮我看看",
                },
                now=self.t_free + timedelta(minutes=6),
                subject_view=True,
            )
        self.assertEqual(ctx.exception.reason_code, "SOURCE_GONE")

        # Un-prefixed legacy ids keep working exactly as before.
        legacy = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "add_missing_note",
                "kind": "MADE",
                "source_ref": "legacy-shell-01",
                "summary": "写完一页材料",
            },
            now=self.t_free + timedelta(minutes=7),
            subject_view=True,
        )
        self.assertEqual(legacy["status"], "applied")
        legacy_favor = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "ask_partner_favor",
                "favor_kind": "help_briefly",
                "target_object_ref": "legacy-sketch-02",
                "summary": "帮我递一下那叠纸",
            },
            now=self.t_free + timedelta(minutes=8),
            subject_view=True,
        )
        self.assertEqual(legacy_favor["status"], "applied")

        legacy_view = et.read_emotional_tension(
            self.root, now=self.t_free + timedelta(minutes=9), subject_view=True
        )
        refs = {n["source_ref"] for n in legacy_view["missing_partner"]["kept_notes"]}
        self.assertIn("legacy-shell-01", refs)
        self.assertEqual(
            legacy_view["asking_partner"]["open_favor"]["target_object_ref"],
            "legacy-sketch-02",
        )

    def test_02_card_and_project_source_gone(self) -> None:
        _write_json(
            self.root / "world" / "v04_neighborhood_state.json",
            {"craft_bench": {"cards": {"card-1": {"card_id": "card-1", "title": "小卡片"}}}},
        )
        _write_json(
            self.root / "phone" / "v04_elastic_play.json",
            {"personal_projects": [{"project_id": "proj-1", "status": "active"}]},
        )

        et.apply_emotional_tension_action(
            self.root,
            {
                "action": "add_missing_note",
                "kind": "MADE",
                "source_ref": "v04_card:card-1",
                "summary": "画完一张小卡片",
            },
            now=self.t_free,
            subject_view=True,
        )
        et.apply_emotional_tension_action(
            self.root,
            {
                "action": "add_missing_note",
                "kind": "LEARNED",
                "source_ref": "v04_project:proj-1",
                "summary": "学会一节新谱子",
            },
            now=self.t_free,
            subject_view=True,
        )

        # Both sources disappear in their owning repositories.
        _write_json(
            self.root / "world" / "v04_neighborhood_state.json",
            {"craft_bench": {"cards": {}}},
        )
        _write_json(
            self.root / "phone" / "v04_elastic_play.json",
            {"personal_projects": [{"project_id": "proj-1", "status": "deleted"}]},
        )

        view = et.read_emotional_tension(
            self.root, now=self.t_free + timedelta(minutes=3), subject_view=True
        )
        self.assertEqual(view["missing_partner"]["kept_notes"], [])

        for ref in ("v04_card:card-1", "v04_project:proj-1"):
            with self.assertRaises(et.EmotionalTensionConflictError) as ctx:
                et.apply_emotional_tension_action(
                    self.root,
                    {
                        "action": "add_missing_note",
                        "kind": "MADE",
                        "source_ref": ref,
                        "summary": "还想留着",
                    },
                    now=self.t_free + timedelta(minutes=4),
                    subject_view=True,
                )
            self.assertEqual(ctx.exception.reason_code, "SOURCE_GONE")

        # A completely missing store file also counts as gone, without creating it.
        with self.assertRaises(et.EmotionalTensionConflictError) as ctx:
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "add_missing_note",
                    "kind": "MADE",
                    "source_ref": "v04_card:ghost-card",
                    "summary": "不存在的东西",
                },
                now=self.t_free + timedelta(minutes=5),
                subject_view=True,
            )
        self.assertEqual(ctx.exception.reason_code, "SOURCE_GONE")

    # ---- 2. Receipt eviction ring & concurrent CAS ----------------------

    def test_03_receipt_eviction_replays_safely_and_concurrent_cas(self) -> None:
        total = et.MAX_OPERATION_RECEIPTS + 6
        for idx in range(total):
            out = et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "set_interaction_context",
                    "partner_urgent": False,
                    "operation_key": f"evict-op-{idx}",
                },
                now=self.t0,
                subject_view=False,
            )
            self.assertEqual(out["status"], "applied")
        state, err = et.read_emotional_tension_state(self.root)
        self.assertIsNone(err)
        self.assertEqual(len(state["operation_receipts"]), et.MAX_OPERATION_RECEIPTS)
        self.assertGreaterEqual(len(state["committed_op_ring"]), total - et.MAX_OPERATION_RECEIPTS)
        seqs = [rec["op_seq"] for rec in state["operation_receipts"]]
        self.assertEqual(seqs, sorted(seqs))
        revision_before = state["revision"]
        ledger_before = len(state["operation_receipts"]) + len(state["committed_op_ring"])

        # The very first key was evicted into the ring: replay is a safe no-op.
        replay = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "set_interaction_context",
                "partner_urgent": False,
                "operation_key": "evict-op-0",
            },
            now=self.t0,
            subject_view=False,
        )
        self.assertEqual(replay["status"], "already_applied")
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["changed"])
        self.assertEqual(replay["revision"], revision_before)
        state_after, _ = et.read_emotional_tension_state(self.root)
        self.assertEqual(state_after["revision"], revision_before)
        self.assertEqual(
            len(state_after["operation_receipts"]) + len(state_after["committed_op_ring"]),
            ledger_before,
        )

        # Same evicted key with a different payload is still a 409 mismatch.
        with self.assertRaises(et.EmotionalTensionConflictError) as ctx:
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "set_interaction_context",
                    "partner_urgent": True,
                    "operation_key": "evict-op-0",
                },
                now=self.t0,
                subject_view=False,
            )
        self.assertEqual(ctx.exception.reason_code, "OPERATION_KEY_PAYLOAD_MISMATCH")

        # Two writers racing on the same stale expected_revision: exactly one
        # wins, the other gets REVISION_CONFLICT -- no lost update.
        barrier = threading.Barrier(2)
        successes: list[dict[str, Any]] = []
        failures: list[BaseException] = []

        def _writer(suffix: int) -> None:
            barrier.wait(timeout=10)
            try:
                result = et.apply_emotional_tension_action(
                    self.root,
                    {
                        "action": "set_interaction_context",
                        "partner_urgent": False,
                        "operation_key": f"conc-op-{suffix}",
                        "expected_revision": revision_before,
                    },
                    now=self.t0,
                    subject_view=False,
                )
                successes.append(result)
            except BaseException as exc:  # noqa: BLE001 - recorded for assertions
                failures.append(exc)

        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], et.EmotionalTensionConflictError)
        self.assertEqual(failures[0].reason_code, "REVISION_CONFLICT")
        final_state, _ = et.read_emotional_tension_state(self.root)
        self.assertEqual(final_state["revision"], revision_before + 1)

    # ---- 3. Zero-write cue scan -----------------------------------------

    def test_04_scan_zero_write_empty_and_truthful(self) -> None:
        empty_root = self.root / "empty-instance"
        empty_root.mkdir()
        calm = et.scan_emotional_tension_cues_zero_write(empty_root, now=self.t0)
        self.assertFalse(calm["in_low_mood"])
        self.assertEqual(calm["active_spaces_count"], 0)
        self.assertEqual(calm["open_ruptures_count"], 0)
        self.assertEqual(calm["unsurfaced_missing_notes_count"], 0)
        self.assertIsNone(calm["open_favor"])
        self.assertEqual(calm["declined_kinds_until_sleep"], [])
        self.assertIsNone(calm["latest_expired_favor_or_rejected_apology"])
        self.assertTrue(calm["read_only"])
        # A missing state file creates nothing at all.
        self.assertEqual(list(empty_root.iterdir()), [])

        # Normal scenario: pick a seed whose deterministic low-mood window is live.
        seed = None
        window = None
        for idx in range(400):
            candidate = f"scan-seed-{idx}"
            span = et.derive_weekly_low_mood_window(candidate, self.t0)
            if span is not None:
                seed, window = candidate, span
                break
        self.assertIsNotNone(window)
        inside = datetime.fromisoformat(window["start_at"]) + timedelta(minutes=30)
        private_summary = "画完一张只有自己知道的小卡片"

        et.apply_emotional_tension_action(
            self.root,
            {"action": "configure_instance_seed", "instance_seed": seed},
            now=inside,
            subject_view=True,
        )
        et.apply_emotional_tension_action(
            self.root,
            {
                "action": "add_missing_note",
                "kind": "MADE",
                "source_ref": "legacy-scan-01",
                "summary": private_summary,
            },
            now=inside,
            subject_view=True,
        )
        asked = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "ask_partner_favor",
                "favor_kind": "look_once",
                "target_object_ref": "legacy-scan-card",
                "summary": "帮我看看配色",
            },
            now=inside,
            subject_view=True,
        )
        et.apply_emotional_tension_action(
            self.root,
            {
                "action": "respond_favor",
                "favor_id": asked["favor_id"],
                "decision": "DECLINE",
            },
            now=inside + timedelta(minutes=1),
            subject_view=False,
        )
        second = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "ask_partner_favor",
                "favor_kind": "sit_awhile",
                "summary": "陪我坐一会儿",
            },
            now=inside + timedelta(minutes=2),
            subject_view=True,
        )
        self.assertEqual(second["status"], "applied")
        et.apply_emotional_tension_action(
            self.root,
            {
                "action": "open_rupture",
                "scope": "HOUSEHOLD",
                "description": "说好的收拾又没做",
                "incident_kind": "COMMITMENT_BROKEN",
                "incident_ref": "inc-scan-1",
            },
            now=inside + timedelta(minutes=3),
            subject_view=True,
        )
        et.apply_emotional_tension_action(
            self.root,
            {
                "action": "declare_need_space",
                "incident_ref": "inc-scan-1",
                "space_style": "UNTIL_SLEEP",
                "spoken_aloud": True,
            },
            now=inside + timedelta(minutes=4),
            subject_view=True,
        )
        state_bytes = self._state_bytes()
        phone_listing = sorted(
            p.name for p in (self.root / "phone").iterdir()
        )
        snapshot = et.scan_emotional_tension_cues_zero_write(
            self.root, now=inside + timedelta(minutes=5)
        )
        self.assertTrue(snapshot["in_low_mood"])
        self.assertEqual(snapshot["active_spaces_count"], 1)
        self.assertEqual(snapshot["open_ruptures_count"], 1)
        self.assertEqual(snapshot["unsurfaced_missing_notes_count"], 1)
        self.assertIsNotNone(snapshot["open_favor"])
        self.assertTrue(snapshot["open_favor"]["no_obligation_when_declined"])
        self.assertEqual(snapshot["declined_kinds_until_sleep"], ["look_once"])
        self.assertIsNone(snapshot["latest_expired_favor_or_rejected_apology"])
        # Never leaks private note text.
        self.assertNotIn(private_summary, json.dumps(snapshot, ensure_ascii=False))
        self.assertTrue(snapshot["private_content_excluded"])

        # Two hours later the ignored favor has expired; the scan reports the
        # read-only identifier without renewing anything.
        later = et.scan_emotional_tension_cues_zero_write(
            self.root, now=inside + timedelta(hours=2, minutes=10)
        )
        self.assertIsNone(later["open_favor"])
        latest = later["latest_expired_favor_or_rejected_apology"]
        self.assertIsNotNone(latest)
        self.assertEqual(latest["kind"], "FAVOR_EXPIRED")

        # Both scans were 100% read-only.
        self.assertEqual(self._state_bytes(), state_bytes)
        self.assertEqual(
            sorted(p.name for p in (self.root / "phone").iterdir()), phone_listing
        )


if __name__ == "__main__":
    unittest.main()

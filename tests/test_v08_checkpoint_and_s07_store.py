#!/usr/bin/env python3
"""v0.8 Design Debt Closure Tests: 36B Auto-Checkpoint Capture & S07 Durable Affect Store.

Covers:
1. Design Debt IV.3: Zero-write auto-capture of ACTIVE foreground Life activity
   from ``canonical_life_state.json`` on permit confirmation entry into ACTIVE,
   resumability on STOP / AFTERCARE, single-consumption idempotency via
   ``consume_resumable_checkpoint``, immediate ``STOP`` word halt (0 LLM calls),
   and unchanged ``saved=False`` when no active foreground activity exists.
2. Design Debt I.1: Durable zero-tombstone ``S07AffectStore`` helpers in
   ``s07_affect_owner.py`` (``affect_store_path``, ``read_affect_records_zero_write``,
   ``record_durable_affect``, ``project_durable_s07_affect_zero_write``):
   zero-write reads/projections on missing & populated roots, deterministic
   linear & exponential integer decay, silent zero-tombstone pruning on write,
   ``MAX_STORED_AFFECT_RECORDS`` cap, ``operation_key`` idempotency across active
   receipts and ``committed_op_ring``, and smuggling refusal.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(REPO_ROOT), str(REPO_ROOT / "components" / "life" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.life.scripts import s07_affect_owner as s07  # noqa: E402
from components.phone import adult_intimacy_view as aiv  # noqa: E402
from components.phone.adult_intimacy_view import (  # noqa: E402
    apply_adult_intimacy_action,
    consume_resumable_checkpoint,
)
from tests.test_adult_intimacy_view import T0, _open_ready_env  # noqa: E402


def _read_permit_record(root: Path, permit_id: str) -> dict:
    doc = json.loads(aiv.adult_intimacy_state_path(root).read_text(encoding="utf-8"))
    return doc["permits"]["records"][permit_id]


def _write_canonical_life_state(state_file: Path, payload: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    state_file.write_bytes(blob.encode("utf-8"))


class AdultIntimacyAutoCheckpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v08-aiv-ckpt-"))
        self.root = self.tmp / "instance"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _propose_and_accept(self, *, tag: str, extra_accept_fields: dict | None = None) -> str:
        _open_ready_env(self.root, now=T0)
        proposed = apply_adult_intimacy_action(
            self.root,
            {
                "action": "propose_scene",
                "operation_key": f"{tag}_prop",
                "proposer": "partner",
                "categories": ["KISSING"],
            },
            now=T0,
        )
        pid = proposed["action_receipt"]["permit_id"]
        accept_payload = {
            "action": "respond_scene",
            "permit_id": pid,
            "responder": "subject",
            "decision": "ACCEPT",
        }
        if extra_accept_fields:
            accept_payload.update(extra_accept_fields)
        apply_adult_intimacy_action(self.root, accept_payload, now=T0)
        return pid

    def test_01_auto_captures_active_foreground_activity_and_consumes_once_on_stop(self) -> None:
        canon_path = self.root / "one-day" / "life" / "state" / "canonical_life_state.json"
        _write_canonical_life_state(
            canon_path,
            {
                "schema_version": "1.0",
                "subject_id": "chiyo.global",
                "revision": 3,
                "foreground_activity_ref": "actv_botanical_notes_01",
                "activities": {
                    "actv_botanical_notes_01": {
                        "activity_id": "actv_botanical_notes_01",
                        "activity_kind": "reading",
                        "title": "Reading botanical notes",
                        "status": "ACTIVE",
                    }
                },
            },
        )
        before_bytes = canon_path.read_bytes()
        before_mtime_ns = canon_path.stat().st_mtime_ns

        pid = self._propose_and_accept(tag="auto1")

        # Zero-write invariant on canonical_life_state.json
        self.assertEqual(canon_path.read_bytes(), before_bytes)
        self.assertEqual(canon_path.stat().st_mtime_ns, before_mtime_ns)

        rec = _read_permit_record(self.root, pid)
        ckpt = rec["checkpoint"]
        self.assertEqual(ckpt["activity_ref"], "actv_botanical_notes_01")
        self.assertTrue(ckpt["saved"])
        self.assertFalse(ckpt["resumable"])
        self.assertEqual(ckpt["capture_source"], "canonical_life_state_auto")
        self.assertEqual(ckpt["activity_title"], "Reading botanical notes")

        # While ACTIVE (resumable=False), consume_resumable_checkpoint does not consume
        early = consume_resumable_checkpoint(
            self.root,
            permit_id=pid,
            now=T0 + timedelta(seconds=10),
            decision_id="decision:early",
        )
        self.assertTrue(early["ok"])
        self.assertTrue(early["already_consumed"])
        self.assertEqual(early["llm_calls"], 0)

        # Transition to STOPPED -> resumable becomes True
        apply_adult_intimacy_action(
            self.root,
            {"action": "stop_scene", "permit_id": pid, "by": "subject"},
            now=T0 + timedelta(seconds=30),
        )
        rec_stopped = _read_permit_record(self.root, pid)
        self.assertTrue(rec_stopped["checkpoint"]["resumable"])

        # First consumption succeeds cleanly
        first = consume_resumable_checkpoint(
            self.root,
            permit_id=pid,
            now=T0 + timedelta(seconds=45),
            decision_id="decision:resume_01",
        )
        self.assertTrue(first["ok"])
        self.assertFalse(first["already_consumed"])
        self.assertEqual(first["activity_ref"], "actv_botanical_notes_01")
        self.assertEqual(first["decision_id"], "decision:resume_01")
        self.assertEqual(first["llm_calls"], 0)

        # Second consumption is idempotent (already_consumed=True)
        second = consume_resumable_checkpoint(
            self.root,
            permit_id=pid,
            now=T0 + timedelta(seconds=50),
            decision_id="decision:resume_02",
        )
        self.assertTrue(second["ok"])
        self.assertTrue(second["already_consumed"])
        self.assertEqual(second["activity_ref"], "actv_botanical_notes_01")
        self.assertEqual(second["resumed_at"], first["resumed_at"])
        self.assertEqual(second["llm_calls"], 0)

    def test_02_aftercare_transition_marks_resumable_and_consumes_cleanly(self) -> None:
        # Exercise alternate <day_root>/life/state/canonical_life_state.json + list activities
        canon_path = self.root / "life" / "state" / "canonical_life_state.json"
        _write_canonical_life_state(
            canon_path,
            {
                "schema_version": "1.0",
                "foreground_activity_ref": "actv_desk_sketch_02",
                "activities": [
                    {
                        "activity_id": "actv_desk_sketch_02",
                        "state": "ACTIVE",
                        "title": "Desk sketch study",
                    }
                ],
            },
        )
        pid = self._propose_and_accept(tag="auto2")
        rec = _read_permit_record(self.root, pid)
        self.assertEqual(rec["checkpoint"]["activity_ref"], "actv_desk_sketch_02")
        self.assertTrue(rec["checkpoint"]["saved"])

        # Enter AFTERCARE via advance_beat(phase="AFTERCARE", categories=[])
        apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "operation_key": "auto2_aftercare_beat",
                "phase": "AFTERCARE",
                "categories": [],
            },
            now=T0 + timedelta(seconds=60),
        )
        rec_aftercare = _read_permit_record(self.root, pid)
        self.assertEqual(rec_aftercare["state"], "AFTERCARE")
        self.assertTrue(rec_aftercare["checkpoint"]["resumable"])

        consumed = consume_resumable_checkpoint(
            self.root,
            permit_id=pid,
            now=T0 + timedelta(seconds=90),
            decision_id="decision:aftercare_resume",
        )
        self.assertTrue(consumed["ok"])
        self.assertFalse(consumed["already_consumed"])
        self.assertEqual(consumed["activity_ref"], "actv_desk_sketch_02")
        self.assertEqual(consumed["llm_calls"], 0)

    def test_03_stop_word_halts_immediately_with_zero_llm_calls(self) -> None:
        canon_path = self.root / "one-day" / "life" / "state" / "canonical_life_state.json"
        _write_canonical_life_state(
            canon_path,
            {
                "foreground_activity_ref": "actv_tea_prep_03",
                "activities": {
                    "actv_tea_prep_03": {
                        "activity_id": "actv_tea_prep_03",
                        "status": "ACTIVE",
                        "title": "Steeping evening tea",
                    }
                },
            },
        )
        pid = self._propose_and_accept(tag="stopw")
        out = apply_adult_intimacy_action(
            self.root,
            {
                "action": "advance_beat",
                "permit_id": pid,
                "operation_key": "stopw_beat_1",
                "categories": ["KISSING"],
                "partner_utterance": "停",
            },
            now=T0 + timedelta(seconds=15),
        )
        rcpt = out["action_receipt"]
        self.assertEqual(rcpt["state"], "STOPPED")
        self.assertTrue(rcpt["stop_word_triggered"])
        self.assertTrue(rcpt["no_interrogation"])
        self.assertTrue(rcpt["no_grudge"])
        self.assertEqual(rcpt["relationship_penalty"], 0)

        rec = _read_permit_record(self.root, pid)
        self.assertEqual(rec["state"], "STOPPED")
        self.assertTrue(rec["checkpoint"]["resumable"])

        consumed = consume_resumable_checkpoint(
            self.root,
            permit_id=pid,
            now=T0 + timedelta(seconds=20),
            decision_id="decision:after_stop_word",
        )
        self.assertTrue(consumed["ok"])
        self.assertFalse(consumed["already_consumed"])
        self.assertEqual(consumed["activity_ref"], "actv_tea_prep_03")
        self.assertEqual(consumed["llm_calls"], 0)

    def test_04_no_canonical_state_or_terminal_or_symlink_keeps_saved_false(self) -> None:
        # 1. Helper on non-existent directory creates zero directories/files
        ghost_root = self.tmp / "ghost_day_root"
        self.assertEqual(aiv._read_active_foreground_activity_zero_write(ghost_root), (None, None))
        self.assertFalse(ghost_root.exists())

        # 2. No canonical_life_state.json -> saved == False unchanged
        pid_empty = self._propose_and_accept(tag="empty")
        rec_empty = _read_permit_record(self.root, pid_empty)
        self.assertIsNone(rec_empty["checkpoint"]["activity_ref"])
        self.assertFalse(rec_empty["checkpoint"]["saved"])
        self.assertFalse(rec_empty["checkpoint"]["resumable"])
        self.assertNotIn("capture_source", rec_empty["checkpoint"])

        # 3. Terminal COMPLETED activity in canonical_life_state.json is NOT captured
        term_root = self.tmp / "term_instance"
        _write_canonical_life_state(
            term_root / "one-day" / "life" / "state" / "canonical_life_state.json",
            {
                "foreground_activity_ref": "actv_done_99",
                "activities": {
                    "actv_done_99": {
                        "activity_id": "actv_done_99",
                        "status": "COMPLETED",
                        "title": "Finished task",
                    }
                },
            },
        )
        self.assertEqual(aiv._read_active_foreground_activity_zero_write(term_root), (None, None))

        # 4. Symlinked canonical_life_state.json is rejected
        sym_root = self.tmp / "sym_instance"
        real_target = self.tmp / "outside_canon.json"
        _write_canonical_life_state(
            real_target,
            {
                "foreground_activity_ref": "actv_symlink_evil",
                "activities": {"actv_symlink_evil": {"status": "ACTIVE"}},
            },
        )
        sym_state_dir = sym_root / "one-day" / "life" / "state"
        sym_state_dir.mkdir(parents=True, exist_ok=True)
        sym_file = sym_state_dir / "canonical_life_state.json"
        try:
            os.symlink(real_target, sym_file)
            symlink_created = True
        except OSError:
            symlink_created = False
        if symlink_created:
            self.assertEqual(aiv._read_active_foreground_activity_zero_write(sym_root), (None, None))


class S07DurableAffectStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v08-s07-store-"))
        self.home = self.tmp / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.state_root = self.tmp / "state"
        self.t0 = datetime(2026, 11, 17, 10, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_zero_write_on_missing_and_empty_roots(self) -> None:
        # Missing state_root: creates zero directories or files
        missing_sr = self.tmp / "never_created_state"
        self.assertEqual(s07.read_affect_records_zero_write(missing_sr), [])
        self.assertFalse(missing_sr.exists())

        proj_missing = s07.project_durable_s07_affect_zero_write(
            missing_sr,
            home=self.home,
            reference_instant=self.t0.isoformat(),
        )
        self.assertFalse(missing_sr.exists())
        self.assertTrue(proj_missing["identity_bound"])
        self.assertFalse(proj_missing["has_live_affect"])
        self.assertEqual(proj_missing["records"], [])
        self.assertEqual(proj_missing["writes_performed"], 0)
        self.assertTrue(proj_missing["zero_tombstone"])
        self.assertEqual(proj_missing["protocol"]["llm_calls"], 0)

        # Existing empty state_root: touches zero files, zero lock files, zero mtime changes
        self.state_root.mkdir(parents=True, exist_ok=True)
        mtime_before = self.state_root.stat().st_mtime_ns
        self.assertEqual(s07.read_affect_records_zero_write(self.state_root), [])
        proj_empty = s07.project_durable_s07_affect_zero_write(
            self.state_root,
            home=self.home,
            reference_instant=self.t0,
        )
        self.assertEqual(list(self.state_root.iterdir()), [])
        self.assertEqual(self.state_root.stat().st_mtime_ns, mtime_before)
        self.assertFalse(proj_empty["has_live_affect"])
        self.assertEqual(proj_empty["empty_reason_code"], s07.REASON_NO_AFFECT_INPUT)

    def test_02_record_mints_persists_and_populated_projection_is_zero_write(self) -> None:
        exp_iso = (self.t0 + timedelta(hours=12)).isoformat()
        res = s07.record_durable_affect(
            self.state_root,
            home=self.home,
            reference_instant=self.t0.isoformat(),
            record_kind="mood_shift",
            intensity=65,
            source_refs=["world:event:rain_01"],
            expires_at=exp_iso,
            decay_mode="linear",
            decay_per_hour_milli=2000,
            uncertainty="mild_context",
            operation_key="op_mint_01",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "applied")
        self.assertFalse(res["already_applied"])
        self.assertTrue(res["zero_tombstone"])
        self.assertEqual(res["llm_calls"], 0)
        self.assertEqual(res["stored_count"], 1)

        store_file = s07.affect_store_path(self.state_root)
        self.assertEqual(store_file.name, "s07_affect_records.json")
        self.assertTrue(store_file.is_file())

        # Snapshot disk bytes + mtimes before read & projection
        raw_before = store_file.read_bytes()
        file_mtime_before = store_file.stat().st_mtime_ns
        dir_entries_before = sorted(p.name for p in self.state_root.iterdir())

        records = s07.read_affect_records_zero_write(self.state_root)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["record_id"], res["record_id"])
        self.assertEqual(records[0]["intensity"], 65)

        proj = s07.project_durable_s07_affect_zero_write(
            self.state_root,
            home=self.home,
            reference_instant=self.t0.isoformat(),
        )
        self.assertTrue(proj["has_live_affect"])
        self.assertEqual(proj["live_record_refs"], [res["record_id"]])
        self.assertEqual(proj["live_records"][0]["effective_intensity"], 65)
        self.assertEqual(proj["live_records"][0]["uncertainty"], "mild_context")
        self.assertEqual(proj["writes_performed"], 0)

        self.assertEqual(store_file.read_bytes(), raw_before)
        self.assertEqual(store_file.stat().st_mtime_ns, file_mtime_before)
        self.assertEqual(sorted(p.name for p in self.state_root.iterdir()), dir_entries_before)

    def test_03_deterministic_linear_and_exponential_decay_across_instants(self) -> None:
        far_exp = (self.t0 + timedelta(hours=48)).isoformat()
        r_lin = s07.record_durable_affect(
            self.state_root,
            home=self.home,
            reference_instant=self.t0,
            record_id="affect:lin_01",
            record_kind="mood_shift",
            intensity=60,
            source_refs=["world:read:lin"],
            expires_at=far_exp,
            decay_mode="linear",
            decay_per_hour_milli=5000,  # -5 per hour
        )
        r_exp = s07.record_durable_affect(
            self.state_root,
            home=self.home,
            reference_instant=self.t0,
            record_id="affect:exp_01",
            record_kind="drive",
            intensity=64,
            source_refs=["world:read:exp"],
            expires_at=far_exp,
            decay_mode="exponential",
            decay_half_life_seconds=3600,  # halves every hour
        )
        self.assertEqual(r_lin["record_id"], "affect:lin_01")
        self.assertEqual(r_exp["record_id"], "affect:exp_01")

        # +2h: linear = 60 - 10 = 50; exponential = 64 >> 2 = 16
        t_plus_2h = self.t0 + timedelta(hours=2)
        view_2h_a = s07.project_durable_s07_affect_zero_write(
            self.state_root, home=self.home, reference_instant=t_plus_2h
        )
        view_2h_b = s07.project_durable_s07_affect_zero_write(
            self.state_root, home=self.home, reference_instant=t_plus_2h
        )
        self.assertEqual(view_2h_a, view_2h_b)
        by_id = {item["record_id"]: item["effective_intensity"] for item in view_2h_a["live_records"]}
        self.assertEqual(by_id, {"affect:lin_01": 50, "affect:exp_01": 16})

        # +13h: linear faded to 0 (60 - 65 <= 0), exponential = 0 (64 >> 13 == 0)
        t_plus_13h = self.t0 + timedelta(hours=13)
        view_13h = s07.project_durable_s07_affect_zero_write(
            self.state_root, home=self.home, reference_instant=t_plus_13h
        )
        self.assertFalse(view_13h["has_live_affect"])
        self.assertEqual(view_13h["faded_count"], 2)
        self.assertEqual(view_13h["live_records"], [])

    def test_04_zero_tombstone_pruning_on_next_write_and_max_64_cap(self) -> None:
        # 1) One record that expires in 30m, one that fades to 0 in 1h
        s07.record_durable_affect(
            self.state_root,
            home=self.home,
            reference_instant=self.t0,
            record_id="affect:vanish_expired",
            record_kind="affect_pulse",
            intensity=80,
            source_refs=["world:event:short"],
            expires_at=(self.t0 + timedelta(minutes=30)).isoformat(),
            decay_per_hour_milli=0,
            operation_key="op_vanish_exp",
        )
        s07.record_durable_affect(
            self.state_root,
            home=self.home,
            reference_instant=self.t0,
            record_id="affect:vanish_faded",
            record_kind="mood_shift",
            intensity=10,
            source_refs=["world:event:fade"],
            expires_at=(self.t0 + timedelta(hours=10)).isoformat(),
            decay_mode="linear",
            decay_per_hour_milli=20000,  # loses 20/h -> 0 within 30m
            operation_key="op_vanish_fade",
        )
        self.assertEqual(len(s07.read_affect_records_zero_write(self.state_root)), 2)

        # Next write at T0 + 2h silently prunes both with zero tombstones
        t_later = self.t0 + timedelta(hours=2)
        keep_res = s07.record_durable_affect(
            self.state_root,
            home=self.home,
            reference_instant=t_later,
            record_id="affect:survivor",
            record_kind="drive",
            intensity=50,
            source_refs=["world:event:survivor"],
            expires_at=(t_later + timedelta(hours=6)).isoformat(),
            decay_per_hour_milli=1000,
        )
        self.assertEqual(keep_res["pruned_count"], 2)
        self.assertEqual(keep_res["stored_count"], 1)
        self.assertTrue(keep_res["zero_tombstone"])

        raw_store_text = s07.affect_store_path(self.state_root).read_text(encoding="utf-8")
        self.assertNotIn("affect:vanish_expired", raw_store_text)
        self.assertNotIn("affect:vanish_faded", raw_store_text)
        store_json = json.loads(raw_store_text)
        self.assertNotIn("tombstones", store_json)
        self.assertEqual([r["record_id"] for r in store_json["records"]], ["affect:survivor"])

        # 2) Cap at MAX_STORED_AFFECT_RECORDS (64)
        self.assertEqual(s07.MAX_STORED_AFFECT_RECORDS, 64)
        for idx in range(70):
            s07.record_durable_affect(
                self.state_root,
                home=self.home,
                reference_instant=t_later + timedelta(seconds=idx),
                record_id=f"affect:cap_{idx:03d}",
                record_kind="affect_pulse",
                intensity=40,
                source_refs=["world:event:cap"],
                expires_at=(t_later + timedelta(hours=8)).isoformat(),
                decay_per_hour_milli=0,
            )
        capped = s07.read_affect_records_zero_write(self.state_root)
        self.assertEqual(len(capped), s07.MAX_STORED_AFFECT_RECORDS)
        self.assertEqual(capped[-1]["record_id"], "affect:cap_069")
        self.assertEqual(capped[0]["record_id"], "affect:cap_006")

    def test_05_operation_key_idempotency_active_and_ring_eviction(self) -> None:
        exp_iso = (self.t0 + timedelta(hours=8)).isoformat()
        first = s07.record_durable_affect(
            self.state_root,
            home=self.home,
            reference_instant=self.t0,
            record_kind="mood_shift",
            intensity=45,
            source_refs=["world:event:idem"],
            expires_at=exp_iso,
            decay_per_hour_milli=0,
            operation_key="op_idem_alpha",
        )
        self.assertEqual(first["status"], "applied")
        self.assertFalse(first["already_applied"])

        # Immediate replay -> status == "already_applied", no second record minted
        replay_active = s07.record_durable_affect(
            self.state_root,
            home=self.home,
            reference_instant=self.t0,
            record_kind="mood_shift",
            intensity=45,
            source_refs=["world:event:idem"],
            expires_at=exp_iso,
            decay_per_hour_milli=0,
            operation_key="op_idem_alpha",
        )
        self.assertEqual(replay_active["status"], "already_applied")
        self.assertTrue(replay_active["already_applied"])
        self.assertTrue(replay_active["idempotent_replay"])
        self.assertEqual(replay_active["record_id"], first["record_id"])
        self.assertEqual(replay_active["stored_count"], 1)

        # Conflicting payload under same operation_key is refused
        with self.assertRaises(s07.S07AffectOwnerError):
            s07.record_durable_affect(
                self.state_root,
                home=self.home,
                reference_instant=self.t0,
                record_kind="mood_shift",
                intensity=90,
                source_refs=["world:event:idem"],
                expires_at=exp_iso,
                decay_per_hour_milli=0,
                operation_key="op_idem_alpha",
            )

        # Push > MAX_AFFECT_OP_RECEIPTS operations so op_idem_alpha evicts into committed_op_ring
        for i in range(135):
            s07.record_durable_affect(
                self.state_root,
                home=self.home,
                reference_instant=self.t0 + timedelta(seconds=i + 1),
                record_kind="affect_pulse",
                intensity=30,
                source_refs=["world:event:fill"],
                expires_at=exp_iso,
                decay_per_hour_milli=0,
                operation_key=f"op_fill_{i:03d}",
            )
        store_doc = json.loads(s07.affect_store_path(self.state_root).read_text(encoding="utf-8"))
        self.assertNotIn("op_idem_alpha", store_doc["operations"])
        self.assertTrue(
            any(r.get("operation_key") == "op_idem_alpha" for r in store_doc["committed_op_ring"])
        )

        replay_ring = s07.record_durable_affect(
            self.state_root,
            home=self.home,
            reference_instant=self.t0,
            record_kind="mood_shift",
            intensity=45,
            source_refs=["world:event:idem"],
            expires_at=exp_iso,
            decay_per_hour_milli=0,
            operation_key="op_idem_alpha",
        )
        self.assertEqual(replay_ring["status"], "already_applied")
        self.assertTrue(replay_ring["already_applied"])
        self.assertEqual(replay_ring["reconciled_from"], "committed_op_ring")

    def test_06_forbidden_smuggling_rejected_with_zero_disk_writes(self) -> None:
        untouched_sr = self.tmp / "smuggle_guard_state"
        exp_iso = (self.t0 + timedelta(hours=4)).isoformat()

        for smuggled_kw, expected_code in (
            ({"intimacy_score": 88}, s07.REASON_AFFECT_SMUGGLING_REFUSED),
            ({"mood_delta": -10}, s07.REASON_AFFECT_SMUGGLING_REFUSED),
            ({"affection_score": 5}, s07.REASON_AFFECT_SMUGGLING_REFUSED),
            ({"tone": "cheerful"}, s07.REASON_AFFECT_TONE_INFERENCE_REFUSED),
            ({"fatigue": 30}, s07.REASON_AFFECT_BODY_FACT_REFUSED),
        ):
            with self.assertRaises(s07.S07AffectOwnerError) as ctx:
                s07.record_durable_affect(
                    untouched_sr,
                    home=self.home,
                    reference_instant=self.t0,
                    record_kind="mood_shift",
                    intensity=40,
                    source_refs=["world:event:1"],
                    expires_at=exp_iso,
                    **smuggled_kw,
                )
            self.assertIn(expected_code, str(ctx.exception))
            self.assertFalse(untouched_sr.exists())

    def test_07_touched_files_hygiene_pure_lf_and_persona_neutral(self) -> None:
        touched = (
            REPO_ROOT / "components" / "phone" / "adult_intimacy_view.py",
            REPO_ROOT / "components" / "life" / "scripts" / "s07_affect_owner.py",
            REPO_ROOT / "tests" / "test_v08_checkpoint_and_s07_store.py",
        )
        forbidden_tokens = ("\u5343\u4ee3", "\u5979")
        for path in touched:
            raw = path.read_bytes()
            self.assertNotIn(b"\r\n", raw, f"CRLF found in {path}")
            text = raw.decode("utf-8")
            for tok in forbidden_tokens:
                self.assertNotIn(tok, text, f"Forbidden persona token in {path}")
            # Verify zero supplementary-plane emoji characters
            self.assertTrue(
                all(ord(ch) <= 0xFFFF for ch in text),
                f"Emoji / non-BMP character found in {path}",
            )


if __name__ == "__main__":
    unittest.main()

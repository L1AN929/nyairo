#!/usr/bin/env python3
"""Contract tests for G0 V-C anti-resurrection barrier, D05 device epoch guard, and Ch.15 §10.I chatless review."""

from __future__ import annotations

from contextlib import closing
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from components.life.scripts import v02_recall_projection as rp_engine
from components.life.scripts import v02_runtime_governance_migration as rm_engine
from components.phone import v02_runtime_migration_view as rm_view


EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]"
)

OWNED_FILES: tuple[Path, ...] = (
    Path("components/life/scripts/v02_runtime_governance_migration.py"),
    Path("components/phone/v02_runtime_migration_view.py"),
    Path("components/life/scripts/v02_recall_projection.py"),
    Path("tests/test_v02_vc_barrier_and_silent_review.py"),
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, bytes]]:
    snap: dict[str, tuple[int, int, bytes]] = {}
    if not root.exists():
        return snap
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        dp = Path(dirpath)
        rel_dir = dp.relative_to(root).as_posix()
        st_d = dp.stat()
        snap[f"D:{rel_dir}"] = (st_d.st_mode, 0, b"")
        for fn in filenames:
            fp = dp / fn
            st_f = fp.stat()
            snap[f"F:{fp.relative_to(root).as_posix()}"] = (
                st_f.st_mode,
                st_f.st_mtime_ns,
                fp.read_bytes(),
            )
    return snap


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class TestV02VcBarrierAndSilentReview(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_hygiene_pure_lf_no_emoji_neutral_persona(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        for rel in OWNED_FILES:
            fp = repo_root / rel
            raw = fp.read_bytes()
            self.assertNotIn(b"\r", raw, f"CR byte found in {rel}")
            text = raw.decode("utf-8")
            self.assertNotIn("\u5343\u4ee3", text, f"Forbidden character name in {rel}")
            self.assertNotIn("\u5979", text, f"Forbidden gendered pronoun in {rel}")
            self.assertIsNone(EMOJI_RE.search(text), f"Emoji found in {rel}")

    def test_02_zero_write_gets_missing_empty_and_populated(self) -> None:
        missing_root = self.root / "missing_day"
        before_missing = _snapshot_tree(missing_root)
        st, body = rm_view.read_v02_runtime_migration_view(missing_root, has_subject_capability=False)
        self.assertEqual(st, 200)
        self.assertEqual(body["llm_calls"], 0)
        self.assertTrue(body["chatless_life_review"]["chat_window_hidden"])
        self.assertTrue(body["chatless_life_review"]["dialogue_ignored"])
        self.assertTrue(body["chatless_life_review"]["nonverbal_evidence_only"])
        self.assertTrue(body["vc_authority_status"]["enforced"])
        self.assertEqual(body["device_presence"]["lease_epoch"], 0)
        self.assertFalse(missing_root.exists())
        self.assertEqual(_snapshot_tree(missing_root), before_missing)

        empty_root = self.root / "empty_day"
        empty_root.mkdir(parents=True, exist_ok=True)
        before_empty = _snapshot_tree(empty_root)
        for cap in (False, True):
            st_e, body_e = rm_view.read_v02_runtime_migration_view(empty_root, has_subject_capability=cap)
            self.assertEqual(st_e, 200)
            self.assertEqual(body_e["llm_calls"], 0)
            proj_e = rm_engine.project_v02_runtime_migration(empty_root, has_subject_capability=cap)
            self.assertEqual(proj_e["llm_calls"], 0)
            rev_e = rm_engine.project_chatless_life_review(empty_root, has_subject_capability=cap)
            self.assertEqual(rev_e["llm_calls"], 0)
        self.assertEqual(_snapshot_tree(empty_root), before_empty)

        pop_root = self.root / "populated_day"
        _write_json(
            pop_root / "phone" / "workbench_curio.json",
            {"tray": {"work": {"artifact_id": "art_1", "title": "Ceramic Cup", "delivery_state": "LOCAL_DELIVERED", "shared": True}}},
        )
        _write_json(
            pop_root / "world" / "v08_matter_space.json",
            {"zones": {"desk": {"label": "Desk"}, "subject_private_drawer": {"label": "Secret Drawer"}}, "matter_objects": []},
        )
        before_pop = _snapshot_tree(pop_root)
        for cap in (False, True):
            st_p, body_p = rm_view.read_v02_runtime_migration_view(pop_root, has_subject_capability=cap)
            self.assertEqual(st_p, 200)
            self.assertEqual(body_p["llm_calls"], 0)
            self.assertEqual(body_p["chatless_life_review"]["llm_calls"], 0)
        self.assertEqual(_snapshot_tree(pop_root), before_pop)

    def test_03_vc_watermark_sha256_only_and_tombstone_wiring(self) -> None:
        secret_content = "PRIVATE_MEMORY_PLAINTEXT_MUST_NEVER_LEAK_IN_WATERMARK"
        rp_engine.record_episodic_trace(
            self.root,
            memory_id="mem_vc_01",
            topic="Tea ceremony note",
            exact_detail=secret_content,
            gist_summary="Tea ceremony gist",
            now_iso="2026-11-18T08:00:00+08:00",
        )
        ts_receipt = rp_engine.tombstone_memory(
            self.root,
            memory_id="mem_vc_01",
            reason="user_deleted",
            now_iso="2026-11-18T08:05:00+08:00",
        )
        self.assertTrue(ts_receipt["ok"])
        self.assertEqual(ts_receipt["status_code"], 200)

        gov_receipt = rm_engine.record_revocation_tombstone(
            self.root,
            "gov_vc_02",
            reason="revoked_by_subject",
            now_iso="2026-11-18T08:06:00+08:00",
        )
        self.assertEqual(gov_receipt["status"], "TOMBSTONED")
        self.assertEqual(gov_receipt["llm_calls"], 0)

        wm_path = self.root / "governance" / "v02_vc_authority_watermark.json"
        self.assertTrue(wm_path.is_file())
        wm_raw = wm_path.read_text(encoding="utf-8")
        self.assertNotIn("mem_vc_01", wm_raw)
        self.assertNotIn("gov_vc_02", wm_raw)
        self.assertNotIn(secret_content, wm_raw)

        wm_doc = json.loads(wm_raw)
        self.assertFalse(wm_doc["plaintext_stored"])
        self.assertGreaterEqual(wm_doc["authority_generation"], 2)
        d1 = rp_engine.compute_vc_item_digest("mem_vc_01")
        d2 = rm_engine.compute_vc_item_digest("gov_vc_02")
        self.assertEqual(d1, rm_engine.compute_vc_item_digest("mem_vc_01"))
        self.assertIn(d1, wm_doc["sealed_digests"])
        self.assertIn(d2, wm_doc["sealed_digests"])

        # Re-admission / re-indexing via recall engine must fail closed with 409 MEMORY_TOMBSTONED
        readmit = rp_engine.record_episodic_trace(
            self.root,
            memory_id="mem_vc_01",
            topic="Attempt resurrect",
            exact_detail="New attempt",
            now_iso="2026-11-18T08:07:00+08:00",
        )
        self.assertFalse(readmit["ok"])
        self.assertEqual(readmit["status_code"], 409)
        self.assertEqual(readmit["error_code"], "MEMORY_TOMBSTONED")

        recall_view = rp_engine.project_recall_set(
            self.root,
            include_unavailable=True,
            now_iso="2026-11-18T08:08:00+08:00",
        )
        tomb_items = [it for it in recall_view["items"] if it["memory_id"] == "mem_vc_01"]
        self.assertEqual(len(tomb_items), 1)
        self.assertEqual(tomb_items[0]["clarity"], "UNAVAILABLE")
        self.assertEqual(tomb_items[0]["decay_mode"], rp_engine.DECAY_GOVERNANCE_TOMBSTONE)
        self.assertIsNone(tomb_items[0]["projected_text"])

        st, body = rm_view.read_v02_runtime_migration_view(self.root, has_subject_capability=False)
        self.assertEqual(st, 200)
        vc_stat = body["vc_authority_status"]
        self.assertTrue(vc_stat["enforced"])
        self.assertFalse(vc_stat["plaintext_stored"])
        self.assertEqual(vc_stat["sealed_digest_count"], 2)
        self.assertGreaterEqual(vc_stat["authority_generation"], 2)

    def test_04_vc_cross_backup_rollback_never_resurrects(self) -> None:
        live = self.root / "live"
        backup = self.root / "old_backup"

        # Build initial populated workspace with items before deletion
        _write_json(
            live / "phone" / "v02_recall_projection.json",
            {
                "schema": "nyairo.v02.recall_projection.v1",
                "memories": {
                    "mem_dead_1": {"memory_id": "mem_dead_1", "subject_label": "Old private secret", "clarity_tier": "CRYSTAL"},
                    "mem_alive_1": {"memory_id": "mem_alive_1", "subject_label": "Alive memory", "clarity_tier": "WARM"},
                },
                "tombstones": {},
                "clarity_counts": {"CRYSTAL": 1, "WARM": 1, "FADING": 0, "ARCHIVED": 0, "TOMBSTONED": 0},
            },
        )
        _write_json(
            live / "memory" / "v02_recall_projection_state.json",
            {
                "schema": "nyairo.v02.recall_projection.v1",
                "memories": {
                    "mem_dead_1": {"memory_id": "mem_dead_1", "subject_label": "Old private secret", "clarity_tier": "CRYSTAL"},
                    "mem_alive_1": {"memory_id": "mem_alive_1", "subject_label": "Alive memory", "clarity_tier": "WARM"},
                },
                "tombstones": {},
                "clarity_counts": {"CRYSTAL": 1, "WARM": 1, "FADING": 0, "ARCHIVED": 0, "TOMBSTONED": 0},
            },
        )
        _write_json(
            live / "phone" / "v02_common_ground.json",
            {
                "items": {
                    "cg_dead_1": {"ground_id": "cg_dead_1", "ground_status": "SHARED_WITH_PARTNER", "topic": "Revoked ground"},
                    "cg_alive_1": {"ground_id": "cg_alive_1", "ground_status": "SHARED_WITH_PARTNER", "topic": "Valid ground"},
                }
            },
        )
        _write_json(
            live / "phone" / "longterm_gifts_lantern.json",
            {
                "shared_gifts": [
                    {"gift_id": "gift_dead_1", "stage": "gifted", "title": "Revoked gift"},
                    {"gift_id": "gift_alive_1", "stage": "gifted", "title": "Kept gift"},
                ],
                "later_reflections": [],
            },
        )
        _write_json(
            live / "phone" / "outing_bench_bookstore.json",
            {
                "window_bookmarks": [
                    {"bookmark_id": "bm_dead_1", "title": "Deleted bookmark", "shared": True},
                    {"bookmark_id": "bm_alive_1", "title": "Kept bookmark", "shared": True},
                ]
            },
        )
        _write_json(
            live / "world" / "v08_matter_space.json",
            {
                "zones": {"desk": {"label": "Desk"}},
                "matter_objects": [
                    {"object_id": "obj_dead_1", "label": "Deleted vase", "zone": "desk"},
                    {"object_id": "obj_alive_1", "label": "Kept lamp", "zone": "desk"},
                ],
                "recent_acts": [
                    {"act_id": "act_dead", "object_id": "obj_dead_1", "zone": "desk"},
                ],
            },
        )
        _write_json(
            live / "world-package" / "objects.json",
            [
                {"item_id": "wp_dead_1", "name": "Deleted prop"},
                {"item_id": "wp_alive_1", "name": "Kept prop"},
            ],
        )
        # Old governance file with zero tombstones (pre-revocation backup state)
        _write_json(
            live / "phone" / "v02_runtime_governance.json",
            {"schema": "nyairo.v02.runtime_governance.v1", "tombstones": {}},
        )
        # Old SQLite controls.db lacking recent revocations, plus one historic delete row
        db_path = live / "memory" / "controls.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(db_path))) as conn:
            conn.execute("CREATE TABLE memory_controls (operation_id TEXT, kind TEXT, target_event TEXT, created_at TEXT)")
            conn.execute(
                "INSERT INTO memory_controls VALUES ('op_hist', 'DELETE', 'db_dead_1', '2026-11-17T00:00:00+08:00')"
            )
            conn.commit()

        # Take backup snapshot of BOTH old evidence and old controls BEFORE deletions
        shutil.copytree(live, backup)

        # Now delete/revoke items on live workspace -> produces external V-C watermark seal
        rp_engine.tombstone_memory(live, memory_id="mem_dead_1", now_iso="2026-11-18T09:00:00+08:00")
        for did in ("cg_dead_1", "gift_dead_1", "bm_dead_1", "obj_dead_1", "wp_dead_1"):
            rm_engine.record_revocation_tombstone(live, did, now_iso="2026-11-18T09:01:00+08:00")
        ext_seal = json.loads((live / "governance" / "v02_vc_authority_watermark.json").read_text(encoding="utf-8"))

        # Simulate backup rollback: wipe live and restore OLD evidence + OLD controls.db + OLD governance
        shutil.rmtree(live)
        shutil.copytree(backup, live)
        # Also include an item matching db_dead_1 in old backup evidence
        _write_json(
            live / "phone" / "desk_tomorrow_note.json",
            {"waiting_items": [{"item_id": "db_dead_1", "title": "Should die via db", "shared": True}, {"item_id": "note_alive_1", "title": "Kept note", "shared": True}]},
        )

        # Partner is strictly forbidden (403 FORBIDDEN_ACTOR_SIDE) from running reconcile_backup_restore
        st_403, body_403 = rm_view.mutate_v02_runtime_migration_view(
            live,
            actor_side="partner",
            payload={"action": "reconcile_backup_restore", "external_watermark_seal": ext_seal},
            now_iso="2026-11-18T10:00:00+08:00",
        )
        self.assertEqual(st_403, 403)
        self.assertEqual(body_403["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # Invalid external seal rejected (400 INVALID_FIELDS)
        st_bad, body_bad = rm_view.mutate_v02_runtime_migration_view(
            live,
            actor_side="subject",
            payload={
                "action": "reconcile_backup_restore",
                "external_watermark_seal": {"sealed_digests": ["not-a-sha256-digest"]},
            },
            now_iso="2026-11-18T10:00:00+08:00",
        )
        self.assertEqual(st_bad, 400)
        self.assertEqual(body_bad["reason_code"], "INVALID_FIELDS")

        # Subject executes cross-backup V-C reconciliation with idempotency key
        st_200, body_200 = rm_view.mutate_v02_runtime_migration_view(
            live,
            actor_side="subject",
            payload={
                "action": "reconcile_backup_restore",
                "external_watermark_seal": ext_seal,
                "operation_key": "vc-reconcile-01",
            },
            now_iso="2026-11-18T10:01:00+08:00",
        )
        self.assertEqual(st_200, 200)
        self.assertEqual(body_200["command_status"], "APPLIED")
        self.assertEqual(body_200["llm_calls"], 0)
        receipt = body_200["vc_reconcile_receipt"]
        self.assertGreaterEqual(receipt["restore_epoch"], 1)
        self.assertGreaterEqual(receipt["sealed_digest_count"], 7)
        self.assertGreaterEqual(receipt["stripped_item_count"], 7)

        # Idempotent replay must return DUPLICATE without bumping restore_epoch again
        st_dup, body_dup = rm_view.mutate_v02_runtime_migration_view(
            live,
            actor_side="subject",
            payload={
                "action": "reconcile_backup_restore",
                "external_watermark_seal": ext_seal,
                "operation_key": "vc-reconcile-01",
            },
            now_iso="2026-11-18T10:02:00+08:00",
        )
        self.assertEqual(st_dup, 200)
        self.assertEqual(body_dup["command_status"], "DUPLICATE")
        self.assertTrue(body_dup["idempotent_replay"])
        self.assertEqual(body_dup["vc_authority_status"]["restore_epoch"], receipt["restore_epoch"])

        # Same operation_key with different payload must raise 409 OPERATION_KEY_CONFLICT
        st_conf, body_conf = rm_view.mutate_v02_runtime_migration_view(
            live,
            actor_side="subject",
            payload={
                "action": "reconcile_backup_restore",
                "external_watermark_seal": None,
                "operation_key": "vc-reconcile-01",
            },
            now_iso="2026-11-18T10:03:00+08:00",
        )
        self.assertEqual(st_conf, 409)
        self.assertEqual(body_conf["reason_code"], "OPERATION_KEY_CONFLICT")

        # Verify every revoked item was stripped from disk evidence & memories
        rp_disk = json.loads((live / "phone" / "v02_recall_projection.json").read_text(encoding="utf-8"))
        self.assertNotIn("mem_dead_1", rp_disk["memories"])
        self.assertIn("mem_alive_1", rp_disk["memories"])
        self.assertIn("mem_dead_1", rp_disk["tombstones"])

        gov_disk = json.loads((live / "phone" / "v02_runtime_governance.json").read_text(encoding="utf-8"))
        for dead in ("mem_dead_1", "cg_dead_1", "gift_dead_1", "bm_dead_1", "obj_dead_1", "wp_dead_1", "db_dead_1"):
            self.assertIn(dead, gov_disk["tombstones"])

        cg_disk = json.loads((live / "phone" / "v02_common_ground.json").read_text(encoding="utf-8"))
        self.assertNotIn("cg_dead_1", cg_disk["items"])
        self.assertIn("cg_alive_1", cg_disk["items"])

        gifts_disk = json.loads((live / "phone" / "longterm_gifts_lantern.json").read_text(encoding="utf-8"))
        self.assertEqual([g["gift_id"] for g in gifts_disk["shared_gifts"]], ["gift_alive_1"])

        bm_disk = json.loads((live / "phone" / "outing_bench_bookstore.json").read_text(encoding="utf-8"))
        self.assertEqual([b["bookmark_id"] for b in bm_disk["window_bookmarks"]], ["bm_alive_1"])

        world_disk = json.loads((live / "world" / "v08_matter_space.json").read_text(encoding="utf-8"))
        self.assertEqual([o["object_id"] for o in world_disk["matter_objects"]], ["obj_alive_1"])

        wp_disk = json.loads((live / "world-package" / "objects.json").read_text(encoding="utf-8"))
        self.assertEqual([p["item_id"] for p in wp_disk], ["wp_alive_1"])

        # Re-indexing / projecting recall, chatless review, or exporting suitcase MUST NOT resurrect deleted IDs
        _, read_body = rm_view.read_v02_runtime_migration_view(live, has_subject_capability=True)
        review_str = json.dumps(read_body["chatless_life_review"], ensure_ascii=False)
        for dead in ("mem_dead_1", "cg_dead_1", "gift_dead_1", "bm_dead_1", "obj_dead_1", "wp_dead_1", "db_dead_1"):
            self.assertNotIn(dead, review_str)
        self.assertIn("gift_alive_1", review_str)
        self.assertIn("bm_alive_1", review_str)
        self.assertIn("cg_alive_1", review_str)

    def test_05_d05_device_presence_claim_handoff_and_stale_epoch(self) -> None:
        st0, body0 = rm_view.read_v02_runtime_migration_view(self.root, has_subject_capability=True)
        self.assertEqual(st0, 200)
        self.assertEqual(body0["device_presence"]["lease_epoch"], 0)
        self.assertIsNone(body0["device_presence"]["active_device_id"])
        self.assertTrue(body0["device_presence"]["epoch_guard_enforced"])

        # Device A claims lease
        st1, body1 = rm_view.mutate_v02_runtime_migration_view(
            self.root,
            actor_side="subject",
            payload={
                "action": "claim_device_presence_lease",
                "device_id": "desktop_win",
                "continuation_ref": "workbench:cup_v2",
                "lease_seconds": 1200,
                "operation_key": "d05-claim-1",
            },
            now_iso="2026-11-18T11:00:00+08:00",
        )
        self.assertEqual(st1, 200)
        dp1 = body1["device_presence"]
        self.assertEqual(dp1["lease_epoch"], 1)
        self.assertEqual(dp1["active_device_id"], "desktop_win")
        self.assertEqual(dp1["continuation_ref"], "workbench:cup_v2")
        self.assertTrue(dp1["lease_active"])

        # Stale device attempting claim with old expected_epoch=0 gets 409 STALE_DEVICE_EPOCH
        st_stale, body_stale = rm_view.mutate_v02_runtime_migration_view(
            self.root,
            actor_side="partner",
            payload={
                "action": "claim_device_presence_lease",
                "device_id": "stale_tablet",
                "expected_epoch": 0,
            },
            now_iso="2026-11-18T11:01:00+08:00",
        )
        self.assertEqual(st_stale, 409)
        self.assertEqual(body_stale["reason_code"], "STALE_DEVICE_EPOCH")

        # Wrong owner handoff rejected with 409 DEVICE_LEASE_OWNER_MISMATCH
        st_own, body_own = rm_view.mutate_v02_runtime_migration_view(
            self.root,
            actor_side="subject",
            payload={
                "action": "handoff_device_presence_lease",
                "from_device_id": "other_phone",
                "to_device_id": "phone_ios",
                "lease_epoch": 1,
            },
            now_iso="2026-11-18T11:02:00+08:00",
        )
        self.assertEqual(st_own, 409)
        self.assertEqual(body_own["reason_code"], "DEVICE_LEASE_OWNER_MISMATCH")

        # Valid handoff from desktop_win to phone_ios at lease_epoch=1 preserves continuation_ref
        st2, body2 = rm_view.mutate_v02_runtime_migration_view(
            self.root,
            actor_side="subject",
            payload={
                "action": "handoff_device_presence_lease",
                "from_device_id": "desktop_win",
                "to_device_id": "phone_ios",
                "lease_epoch": 1,
                "operation_key": "d05-handoff-1",
            },
            now_iso="2026-11-18T11:03:00+08:00",
        )
        self.assertEqual(st2, 200)
        dp2 = body2["device_presence"]
        self.assertEqual(dp2["lease_epoch"], 2)
        self.assertEqual(dp2["active_device_id"], "phone_ios")
        self.assertEqual(dp2["continuation_ref"], "workbench:cup_v2")
        self.assertEqual(dp2["last_handoff_from"], "desktop_win")
        self.assertEqual(dp2["last_handoff_to"], "phone_ios")

        # Stale handoff with lease_epoch=1 fails with 409 STALE_DEVICE_EPOCH
        st_stale_ho, body_stale_ho = rm_view.mutate_v02_runtime_migration_view(
            self.root,
            actor_side="subject",
            payload={
                "action": "handoff_device_presence_lease",
                "from_device_id": "phone_ios",
                "to_device_id": "desktop_win",
                "lease_epoch": 1,
            },
            now_iso="2026-11-18T11:04:00+08:00",
        )
        self.assertEqual(st_stale_ho, 409)
        self.assertEqual(body_stale_ho["reason_code"], "STALE_DEVICE_EPOCH")

        # Readback persists active device & epoch across views
        st_read, body_read = rm_view.read_v02_runtime_migration_view(
            self.root, has_subject_capability=False, now_iso="2026-11-18T11:05:00+08:00"
        )
        self.assertEqual(st_read, 200)
        self.assertEqual(body_read["device_presence"]["lease_epoch"], 2)
        self.assertEqual(body_read["device_presence"]["active_device_id"], "phone_ios")

    def test_06_chatless_life_review_six_domains_and_partner_privacy(self) -> None:
        # Plant a fake chat transcript file that MUST be completely ignored
        chat_decoy = "FAKE_CHAT_TRANSCRIPT_SHOULD_NOT_APPEAR"
        _write_json(
            self.root / "phone" / "chat_transcript.json",
            {"messages": [{"role": "user", "text": chat_decoy}]},
        )

        # Domain 1: works_and_versions
        _write_json(
            self.root / "phone" / "workbench_curio.json",
            {
                "works": [
                    {
                        "work_id": "work_pub_1",
                        "artifact_id": "art_pub_1",
                        "version_id": "v2",
                        "title": "Public Pottery Sketch",
                        "delivery_state": "LOCAL_DELIVERED",
                        "shared": True,
                        "note": "PRIVATE_WORK_NOTE_MUST_HIDE",
                    },
                    {
                        "work_id": "work_priv_1",
                        "artifact_id": "art_priv_1",
                        "version_id": "v1",
                        "title": "PRIVATE_UNPUBLISHED_WORK_TITLE",
                        "delivery_state": "DRAFT",
                        "shared": False,
                    },
                ]
            },
        )
        _write_json(
            self.root / "phone" / "longterm_gifts_lantern.json",
            {
                "shared_gifts": [
                    {
                        "gift_id": "gift_pub_1",
                        "stage": "gifted",
                        "title": "Lantern Charm",
                        "draft_seed_note": "PRIVATE_SEED_NOTE_HIDE",
                        "shared": True,
                    },
                    {
                        "gift_id": "gift_draft_1",
                        "stage": "draft",
                        "title": "PRIVATE_DRAFT_GIFT_TITLE",
                        "shared": False,
                    },
                ],
                "later_reflections": [
                    {"reflection_id": "ref_priv", "source_artifact_id": "art_pub_1", "reflection_note": "PRIVATE_REFLECTION_HIDE", "shared": False},
                    {"reflection_id": "ref_pub", "source_artifact_id": "art_pub_1", "reflection_note": "Public reflection", "shared": True},
                ],
            },
        )

        # Domain 2: room_layout_traces
        _write_json(
            self.root / "world" / "v08_matter_space.json",
            {
                "zones": {
                    "desk": {"label": "Window Desk", "visibility": "public"},
                    "subject_private_drawer": {"label": "PRIVATE_DRAWER_LABEL_HIDE", "visibility": "private"},
                },
                "matter_objects": [
                    {"object_id": "obj_desk_1", "label": "Tea Mug", "zone": "desk"},
                    {"object_id": "obj_drawer_secret", "label": "PRIVATE_DRAWER_OBJECT_HIDE", "zone": "subject_private_drawer"},
                ],
                "recent_acts": [
                    {"act_id": "act_pub", "action": "placed", "zone": "desk", "object_id": "obj_desk_1"},
                    {"act_id": "act_priv", "action": "tucked", "zone": "subject_private_drawer", "object_id": "obj_drawer_secret"},
                ],
            },
        )

        # Domain 3: reading_bookmarks
        _write_json(
            self.root / "phone" / "outing_bench_bookstore.json",
            {
                "window_bookmarks": [
                    {"bookmark_id": "bm_pub_1", "title": "Shared Botany Chapter", "shared": True, "note": "Shared margin note"},
                    {"bookmark_id": "bm_priv_1", "title": "PRIVATE_BOOKMARK_HIDE", "shared": False, "note": "PRIVATE_BOOKMARK_NOTE_HIDE"},
                ]
            },
        )
        _write_json(
            self.root / "phone" / "v02_crossmodal.json",
            {
                "reading_uncertainty_marks": [
                    {"mark_id": "cm_pub_1", "title": "Shared Poem Line", "share_with_room": True},
                    {"mark_id": "cm_priv_1", "title": "PRIVATE_CROSSMODAL_HIDE", "share_with_room": False},
                ]
            },
        )

        # Domain 4: methods_and_endings
        _write_json(
            self.root / "phone" / "v02_agency_depth.json",
            {
                "legacy_tool_endings": [
                    {"ending_id": "end_pub_1", "title": "Old script retired cleanly", "shared": True},
                    {"ending_id": "end_priv_1", "title": "PRIVATE_ENDING_HIDE", "shared": False},
                ],
                "validated_methods": [
                    {"method_id": "meth_pub_1", "title": "Morning check routine", "validated": True, "shared": True},
                    {"method_id": "meth_priv_1", "title": "PRIVATE_METHOD_HIDE", "validated": True, "shared": False},
                ],
                "regret_reflections": [
                    {"id": "reg_priv", "summary": "PRIVATE_REGRET_MUST_NEVER_APPEAR"},
                ],
            },
        )

        # Domain 5: closed_and_waiting_items
        _write_json(
            self.root / "phone" / "desk_tomorrow_note.json",
            {
                "closed_items": [
                    {"note_id": "n_closed_pub", "title": "Shared finished errand", "status": "closed", "shared": True},
                    {"note_id": "n_closed_priv", "title": "PRIVATE_CLOSED_TITLE_HIDE", "status": "closed", "shared": False},
                ],
                "waiting_items": [
                    {"note_id": "n_wait_pub", "title": "Shared tomorrow reminder", "status": "waiting", "shared": True},
                    {"note_id": "n_wait_priv", "title": "PRIVATE_WAITING_TITLE_HIDE", "status": "waiting", "shared": False},
                ],
            },
        )

        # Domain 6: verified_deliveries
        _write_json(
            self.root / "phone" / "v02_common_ground.json",
            {
                "items": {
                    "cg_pub_1": {"ground_id": "cg_pub_1", "ground_status": "SHARED_WITH_PARTNER", "delivery_state": "LOCAL_DELIVERED", "topic": "Shared promise"},
                    "cg_priv_1": {"ground_id": "cg_priv_1", "ground_status": "PRIVATE_UNSHARED", "topic": "PRIVATE_GROUND_TOPIC_HIDE"},
                    "cg_pend_1": {"ground_id": "cg_pend_1", "ground_status": "PENDING_CLARIFICATION", "topic": "PRIVATE_PENDING_TOPIC_HIDE"},
                }
            },
        )

        # Verify partner perspective: strict redaction & zero private leaks
        snap_before = _snapshot_tree(self.root)
        st_p, body_p = rm_view.read_v02_runtime_migration_view(self.root, has_subject_capability=False)
        self.assertEqual(st_p, 200)
        self.assertEqual(_snapshot_tree(self.root), snap_before)
        rev_p = body_p["chatless_life_review"]
        self.assertTrue(rev_p["chat_window_hidden"])
        self.assertTrue(rev_p["dialogue_ignored"])
        self.assertTrue(rev_p["nonverbal_evidence_only"])
        self.assertEqual(rev_p["llm_calls"], 0)

        rev_p_json = json.dumps(rev_p, ensure_ascii=False)
        for secret in (
            chat_decoy,
            "PRIVATE_WORK_NOTE_MUST_HIDE",
            "PRIVATE_UNPUBLISHED_WORK_TITLE",
            "PRIVATE_SEED_NOTE_HIDE",
            "PRIVATE_DRAFT_GIFT_TITLE",
            "PRIVATE_REFLECTION_HIDE",
            "PRIVATE_DRAWER_LABEL_HIDE",
            "PRIVATE_DRAWER_OBJECT_HIDE",
            "PRIVATE_BOOKMARK_HIDE",
            "PRIVATE_BOOKMARK_NOTE_HIDE",
            "PRIVATE_CROSSMODAL_HIDE",
            "PRIVATE_ENDING_HIDE",
            "PRIVATE_METHOD_HIDE",
            "PRIVATE_REGRET_MUST_NEVER_APPEAR",
            "PRIVATE_CLOSED_TITLE_HIDE",
            "PRIVATE_WAITING_TITLE_HIDE",
            "PRIVATE_GROUND_TOPIC_HIDE",
            "PRIVATE_PENDING_TOPIC_HIDE",
        ):
            self.assertNotIn(secret, rev_p_json)

        drawer_zones = [
            z for z in rev_p["room_layout_traces"]["zones"] if z["zone_id"] == "subject_private_drawer"
        ]
        self.assertEqual(len(drawer_zones), 1)
        self.assertEqual(drawer_zones[0]["visibility"], "PRIVATE")
        self.assertIsNone(drawer_zones[0]["item_count"])
        self.assertEqual(rev_p["room_layout_traces"]["object_count"], 1)

        cw_p = rev_p["closed_and_waiting_items"]
        self.assertEqual(cw_p["closed_count"], 2)
        self.assertEqual(cw_p["waiting_count"], 2)
        self.assertEqual(cw_p["private_redacted_count"], 2)
        self.assertEqual([x["note_id"] for x in cw_p["closed_items"]], ["n_closed_pub"])
        self.assertEqual([x["note_id"] for x in cw_p["waiting_items"]], ["n_wait_pub"])

        self.assertEqual([x["ground_id"] for x in rev_p["verified_deliveries"]["items"]], ["cg_pub_1"])

        # Verify subject perspective sees full private evidence across all 6 domains while still ignoring chat
        st_s, body_s = rm_view.read_v02_runtime_migration_view(self.root, has_subject_capability=True)
        self.assertEqual(st_s, 200)
        self.assertEqual(_snapshot_tree(self.root), snap_before)
        rev_s = body_s["chatless_life_review"]
        self.assertNotIn(chat_decoy, json.dumps(rev_s, ensure_ascii=False))
        self.assertEqual(rev_s["room_layout_traces"]["object_count"], 2)
        drawer_s = [
            z for z in rev_s["room_layout_traces"]["zones"] if z["zone_id"] == "subject_private_drawer"
        ]
        self.assertEqual(drawer_s[0]["item_count"], 1)
        self.assertEqual(len(rev_s["closed_and_waiting_items"]["closed_items"]), 2)
        self.assertEqual(len(rev_s["closed_and_waiting_items"]["waiting_items"]), 2)
        self.assertEqual(len(rev_s["verified_deliveries"]["items"]), 3)


if __name__ == "__main__":
    unittest.main()

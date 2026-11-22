#!/usr/bin/env python3
"""Stage 4: Dual Forget Plane Bridge, 8 Phone Views Barrier, and Representation Lease Enforcement.

Verifies (zero-LLM, ``llm_calls == 0``):
1. ``DERIVATION_SURFACES`` expansion & expanded ``is_entity_forget_blocked`` / ``filter_collection_by_barrier``.
2. Native ``controls.db`` / ``authority.db`` -> ``load_unified_forget_barrier_readonly`` merge is
   100% zero-write (``before_files == after_files``, zero ``-wal``/``-shm``), and corrupt / rolled-back
   ``authority.db`` or ``controls.db`` triggers ``fail_closed == True``.
3. Explicit ``v02`` forget tombstone mirrors typed ``blocked_sources`` into native
   ``MemoryDeletionAuthority`` on POSIX; rolling back v02 JSON sidecars to a pre-forget snapshot
   CANNOT resurrect the forgotten item.
4. All 8 phone views (``v05_living_town_view``, ``v05_home_craft_view``, ``v05_dreams_desires_view``,
   ``v04_neighborhood_view``, ``v04_elastic_play_view``, ``v03_ecology_view``, ``v02_agency_depth_view``,
   ``v08_composable_body_view``) enforce the zero-write forget barrier on empty, missing, and populated
   roots, filter forgotten items while preserving siblings, and fail closed cleanly on corrupt authority.
5. ``components/phone/service.py`` enforces ``enforce_active_representation_lease`` on ``private_diary``
   (``diary_write``) and ``personal_artifact`` (``create_project``, ``create_derived_project``,
   ``commit_project``, ``research_create``, ``research_update``): allows with no lease, allows with
   active lease, blocks with 403 when revoked or expired.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    ROOT,
    ROOT / "components" / "life" / "scripts",
    ROOT / "components" / "world" / "scripts",
    ROOT / "components" / "native",
    ROOT / "components" / "native" / "src",
    ROOT / "components" / "native" / "memory_runtime_v1",
    ROOT / "components" / "phone",
):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from components.life.scripts import v02_representation_lease_forget as rlf  # noqa: E402
from components.phone import (  # noqa: E402
    service as phone_service,
    v02_agency_depth_view,
    v03_ecology_view,
    v04_elastic_play_view,
    v04_neighborhood_view,
    v05_dreams_desires_view,
    v05_home_craft_view,
    v05_living_town_view,
    v08_composable_body_view,
)

T0 = "2026-11-20T10:00:00+08:00"
T_ACTIVE = "2026-11-20T10:30:00+08:00"
T_EXPIRED = "2026-11-20T14:00:00+08:00"
OWNER = "chiyo"
CONVERSATION = "life:chiyo"


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    """Capture exact file inventory, sizes, mtime_ns, and SHA-256 digests under ``root``."""
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        st = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _file_list(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*"))


class Stage4DualForgetContractTest(unittest.TestCase):
    def test_01_derivation_surfaces_and_entity_scalar_keys_and_filter_collection(self) -> None:
        expected_first_six = (
            "episode",
            "period",
            "p12_narrative",
            "vector_index",
            "belief",
            "drafts",
        )
        self.assertEqual(rlf.DERIVATION_SURFACES[:6], expected_first_six)
        for surf in (
            "v03_ecology",
            "v04_neighborhood",
            "v04_elastic_play",
            "v05_living_town",
            "v05_home_craft",
            "v05_inner_spark",
            "v08_composable_body",
            "v08_living_traces",
            "v08_emergent_echoes",
            "lived_context",
        ):
            self.assertIn(surf, rlf.DERIVATION_SURFACES)

        required_scalar_keys = (
            "desire_id",
            "dream_id",
            "notice_id",
            "proposal_id",
            "postcard_id",
            "concern_ref",
            "book_id",
            "project_id",
            "tension_id",
            "encounter_id",
            "outbound_id",
            "event_id",
            "turn_id",
            "focus_ref",
            "episode_key",
            "memory_id",
            "visit_id",
            "followup_id",
            "shortcut_id",
            "spectacle_id",
            "pref_id",
            "adjustment_id",
            "clue_id",
            "enforcement_id",
            "hypothesis_id",
            "s03_claim_id",
            "source_regret_id",
            "coaster_id",
            "errand_id",
            "rumor_id",
            "box_id",
            "consignment_id",
            "concern_id",
            "plant_id",
            "patch_id",
            "divider_id",
            "draft_id",
            "edit_id",
            "branch_id",
            "partner_annotation_id",
            "subject_annotation_id",
            "deliberation_id",
            "transaction_id",
            "receipt_id",
            "support_id",
            "last_outbound_id",
            "pending_outbound_id",
            "frost_id",
        )
        # Ensure zero duplicates and no structural/config keys in _ENTITY_BARRIER_SCALAR_KEYS
        self.assertEqual(len(rlf._ENTITY_BARRIER_SCALAR_KEYS), len(set(rlf._ENTITY_BARRIER_SCALAR_KEYS)))
        for structural_key in ("spot_id", "place_id", "pack_id", "zone_id"):
            self.assertNotIn(structural_key, rlf._ENTITY_BARRIER_SCALAR_KEYS)

        for key in required_scalar_keys:
            target = f"tgt_{key}"
            barrier = {"barrier_active": True, "fail_closed": False, "blocked_ids": {target}}
            self.assertTrue(
                rlf.is_entity_forget_blocked({key: target, "title": "demo"}, barrier),
                f"Expected key {key!r} to trigger is_entity_forget_blocked",
            )
            self.assertFalse(
                rlf.is_entity_forget_blocked({key: "other_item"}, barrier),
                f"Unexpected block for non-matching value on {key!r}",
            )

        # Nested source_refs tuples & dicts
        barrier_ref = {"barrier_active": True, "fail_closed": False, "blocked_ids": {"enc_blocked"}}
        self.assertTrue(
            rlf.is_entity_forget_blocked(
                {"title": "nested", "source_refs": [("v05_town", "enc_blocked")]},
                barrier_ref,
            )
        )
        self.assertTrue(
            rlf.is_entity_forget_blocked(
                {"title": "typed", "source_refs": [{"kind": "v02_forget", "ref": "enc_blocked"}]},
                barrier_ref,
            )
        )

        # filter_collection_by_barrier filters blocked items and returns [] on fail_closed
        items = [
            {"desire_id": "des_keep", "text": "keep"},
            {"desire_id": "des_drop", "text": "drop"},
        ]
        filtered = rlf.filter_collection_by_barrier(
            items, {"barrier_active": True, "fail_closed": False, "blocked_ids": {"des_drop"}}
        )
        self.assertEqual([it["desire_id"] for it in filtered], ["des_keep"])

        fail_closed_out = rlf.filter_collection_by_barrier(
            items, {"barrier_active": True, "fail_closed": True, "blocked_ids": set()}
        )
        self.assertEqual(fail_closed_out, [])

        # Lease scopes normalize cleanly
        for scope in ("episodic_memory", "private_diary", "personal_artifact", "skill_book"):
            self.assertEqual(rlf._normalize_rep_scope(scope), scope)

    def test_02_zero_write_missing_native_dbs_never_created(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s4-missing-") as td:
            root = Path(td) / "day_root"
            root.mkdir()
            before = _snapshot_tree(Path(td))
            barrier = rlf.load_unified_forget_barrier_readonly(root)
            after = _snapshot_tree(Path(td))
            self.assertEqual(before, after)
            self.assertFalse(barrier["fail_closed"])
            self.assertFalse(barrier["barrier_active"])
            self.assertEqual(barrier["native_blocked_count"], 0)
            self.assertEqual(barrier["native_sources_checked"], [])

    def test_03_native_controls_and_authority_zero_write_merge_and_corrupt_fail_closed(self) -> None:
        from app.evidence import EvidenceStore
        from life_experience_port import build_outcome_event
        from memory_authority import MemoryDeletionAuthority
        from memory_control import MemoryControlLedger

        with tempfile.TemporaryDirectory(prefix="s4-native-barrier-") as td:
            base = Path(td)
            day_root = base / "inst"
            mem_dir = day_root / "memory"
            auth_dir = base / f"{day_root.name}-memory-authority"
            mem_dir.mkdir(parents=True)
            auth_dir.mkdir(parents=True)

            evidence_db = mem_dir / "evidence.db"
            controls_db = mem_dir / "controls.db"
            authority_db = auth_dir / "authority.db"

            ev_store = EvidenceStore(evidence_db)
            ev1 = build_outcome_event(
                subject=OWNER,
                source_ref="v05_town:enc_native_1",
                text="native event 1",
                occurred_at=datetime.now(timezone.utc).isoformat(),
            )
            ev_store.append(ev1)

            MemoryControlLedger(
                controls_db, evidence_db, owner=OWNER, conversation=CONVERSATION
            ).initialize()
            auth = MemoryDeletionAuthority(
                authority_db, owner=OWNER, conversation=CONVERSATION
            )
            auth.initialize()
            auth.commit(
                operation_id="op-auth-1",
                target_event=ev1.event_id,
                turn_id="turn_native_42",
                blocked_event_ids=[ev1.event_id, "evt_extra_99"],
                fingerprint="fp-auth-1",
                blocked_sources=[
                    {"kind": "v02_forget", "namespace": OWNER, "ref": "des_native_blocked"}
                ],
            )
            ledger = MemoryControlLedger(
                controls_db,
                evidence_db,
                owner=OWNER,
                conversation=CONVERSATION,
                authority=str(authority_db),
            )
            ledger.apply(
                operation_id="op-ctrl-1",
                kind="DELETE",
                target=ev1.event_id,
                actor=OWNER,
            )

            before_files = _file_list(base)
            before_snap = _snapshot_tree(base)
            barrier = rlf.load_unified_forget_barrier_readonly(day_root)
            after_files = _file_list(base)
            after_snap = _snapshot_tree(base)

            self.assertEqual(before_files, after_files)
            self.assertEqual(before_snap, after_snap)
            for fname in after_files:
                self.assertFalse(fname.endswith("-wal"), f"Unexpected WAL sidecar: {fname}")
                self.assertFalse(fname.endswith("-shm"), f"Unexpected SHM sidecar: {fname}")

            self.assertFalse(barrier["fail_closed"])
            self.assertTrue(barrier["barrier_active"])
            self.assertGreaterEqual(barrier["native_blocked_count"], 4)
            self.assertEqual(len(barrier["native_sources_checked"]), 2)
            self.assertIn(ev1.event_id, barrier["blocked_ids"])
            self.assertIn("evt_extra_99", barrier["blocked_ids"])
            self.assertIn("turn_native_42", barrier["blocked_ids"])
            self.assertIn("des_native_blocked", barrier["blocked_ids"])
            self.assertTrue(rlf.is_entity_forget_blocked({"desire_id": "des_native_blocked"}, barrier))
            self.assertTrue(rlf.is_entity_forget_blocked({"turn_id": "turn_native_42"}, barrier))
            self.assertTrue(rlf.is_entity_forget_blocked({"event_id": "evt_extra_99"}, barrier))

            # Corrupt authority.db -> must fail closed with zero disk writes
            authority_db.write_bytes(b"CORRUPT_AUTHORITY_BYTES_NOT_SQLITE")
            corrupt_before = _snapshot_tree(base)
            corrupt_barrier = rlf.load_unified_forget_barrier_readonly(day_root)
            corrupt_after = _snapshot_tree(base)
            self.assertEqual(corrupt_before, corrupt_after)
            self.assertTrue(corrupt_barrier["fail_closed"])
            self.assertTrue(corrupt_barrier["barrier_active"])
            self.assertEqual(
                rlf.filter_collection_by_barrier([{"desire_id": "any_item"}], corrupt_barrier),
                [],
            )

    @unittest.skipIf(os.name != "posix", "Native authority mirror runs on POSIX")
    def test_04_v02_forget_mirrors_to_authority_and_survives_json_rollback(self) -> None:
        from memory_authority import MemoryDeletionAuthority
        from memory_control import MemoryControlLedger

        with tempfile.TemporaryDirectory(prefix="s4-anti-resurrect-") as td:
            base = Path(td)
            day_root = base / "inst"
            mem_dir = day_root / "memory"
            auth_dir = base / f"{day_root.name}-memory-authority"
            ctrl_root = base / "v02-control"
            mem_dir.mkdir(parents=True)
            auth_dir.mkdir(parents=True)
            ctrl_root.mkdir(parents=True)

            evidence_db = mem_dir / "evidence.db"
            controls_db = mem_dir / "controls.db"
            authority_db = auth_dir / "authority.db"
            MemoryControlLedger(
                controls_db, evidence_db, owner=OWNER, conversation=CONVERSATION
            ).initialize()
            MemoryDeletionAuthority(
                authority_db, owner=OWNER, conversation=CONVERSATION
            ).initialize()

            # 1. Ingest a v02 source and snapshot pre-forget JSON sidecars
            st_in, _ = rlf.ingest_source(
                day_root,
                source_id="enc_secret_rollback_01",
                text="sensitive town encounter",
                operation_id="op_ing_rb1",
                control_root=ctrl_root,
                now_iso=T0,
            )
            self.assertEqual(st_in, 200)

            gov_backup = base / "gov_before.json"
            ctrl_backup = base / "ctrl_before.json"
            gov_path = day_root / "governance" / "v02_representation_lease_forget_state.json"
            ctrl_path = ctrl_root / "control_authority" / "v02_control_highwater_ledger.json"
            shutil.copy2(gov_path, gov_backup)
            shutil.copy2(ctrl_path, ctrl_backup)

            # 2. Execute explicit v02 forget -> must mirror typed blocked_sources into authority.db
            st_fg, resp_fg = rlf.execute_explicit_forget(
                day_root,
                source_ids=["enc_secret_rollback_01"],
                operation_id="op_fg_rb1",
                control_root=ctrl_root,
                now_iso=T_ACTIVE,
            )
            self.assertEqual(st_fg, 200)
            self.assertTrue(resp_fg.get("native_authority_mirrored"))

            auth_proj = MemoryDeletionAuthority(
                authority_db, owner=OWNER, conversation=CONVERSATION
            ).projection()
            self.assertTrue(auth_proj.healthy)
            self.assertIn("enc_secret_rollback_01", auth_proj.blocked_source_refs)
            self.assertIn(("v02_forget", OWNER, "enc_secret_rollback_01"), auth_proj.blocked_sources)

            # 3. Roll back BOTH v02 JSON sidecar files to pre-forget snapshot!
            shutil.copy2(gov_backup, gov_path)
            shutil.copy2(ctrl_backup, ctrl_path)

            # 4. Anti-resurrection proof: unified barrier STILL blocks enc_secret_rollback_01 with 0 writes!
            before = _snapshot_tree(base)
            barrier = rlf.load_unified_forget_barrier_readonly(day_root, control_root=ctrl_root)
            after = _snapshot_tree(base)
            self.assertEqual(before, after)
            self.assertFalse(barrier["fail_closed"])
            self.assertTrue(barrier["barrier_active"])
            self.assertIn("enc_secret_rollback_01", barrier["blocked_ids"])
            self.assertTrue(
                rlf.is_entity_forget_blocked({"encounter_id": "enc_secret_rollback_01"}, barrier)
            )


class Stage4EightPhoneViewsForgetBarrierTest(unittest.TestCase):
    def _assert_zero_write_view(self, root: Path, fn: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        before = _snapshot_tree(root)
        code, body = fn(root, **kwargs)
        after = _snapshot_tree(root)
        self.assertEqual(before, after, f"Zero-write violation in {fn.__name__}")
        self.assertEqual(code, 200)
        return code, body

    def test_01_all_8_views_zero_write_on_missing_and_empty_roots(self) -> None:
        views = [
            v05_living_town_view.read_v05_living_town_view,
            v05_home_craft_view.read_v05_home_craft_view,
            v05_dreams_desires_view.read_v05_inner_spark_view,
            v04_neighborhood_view.read_v04_neighborhood_view,
            v04_elastic_play_view.read_v04_elastic_play_view,
            v03_ecology_view.read_v03_ecology_view,
            v02_agency_depth_view.read_v02_agency_depth_view,
            v08_composable_body_view.read_v08_composable_body_view,
        ]
        with tempfile.TemporaryDirectory(prefix="s4-views-empty-") as td:
            base = Path(td)
            missing_root = base / "does_not_exist"
            empty_root = base / "empty_dir"
            empty_root.mkdir()
            for root in (missing_root, empty_root):
                for fn in views:
                    for subj in (False, True):
                        before = _snapshot_tree(base)
                        code, body = fn(root, has_subject_capability=subj, now_iso=T0)
                        after = _snapshot_tree(base)
                        self.assertEqual(code, 200)
                        self.assertTrue(body.get("ok"))
                        self.assertEqual(before, after, f"Write detected in {fn.__name__} on {root.name}")

    def test_02_v05_living_town_view_filters_forgotten_items_zero_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s4-v05-town-") as td:
            root = Path(td)
            c1, r1 = v05_living_town_view.handle_v05_living_town_action(
                root,
                {
                    "action": "leave_drifting_book",
                    "book_title": "海边诗集",
                    "slip_note_cn": "留给下一位路人",
                    "spot_id": "old_bookshop",
                    "operation_key": "op_book_drop",
                },
                has_subject_capability=True,
                now_iso=T0,
            )
            self.assertEqual(c1, 200)
            c2, r2 = v05_living_town_view.handle_v05_living_town_action(
                root,
                {
                    "action": "post_bulletin_followup",
                    "title_cn": "雨季修路提醒",
                    "followup_cn": "路口已经重铺石板",
                    "operation_key": "op_notice_drop",
                },
                has_subject_capability=True,
                now_iso=T0,
            )
            self.assertEqual(c2, 200)

            _, before_view = self._assert_zero_write_view(
                root, v05_living_town_view.read_v05_living_town_view, has_subject_capability=True, now_iso=T0
            )
            book_ids = [b["book_id"] for b in before_view["drifting_bookbox"]]
            notice_ids = [n["notice_id"] for n in before_view["bulletin_followups"]]
            self.assertGreaterEqual(len(book_ids), 2)
            self.assertGreaterEqual(len(notice_ids), 2)
            drop_book = book_ids[-1]
            drop_notice = notice_ids[-1]

            st, _ = rlf.execute_explicit_forget(
                root, source_ids=[drop_book, drop_notice], operation_id="op_fg_v05_town", now_iso=T_ACTIVE
            )
            self.assertEqual(st, 200)

            _, after_view = self._assert_zero_write_view(
                root, v05_living_town_view.read_v05_living_town_view, has_subject_capability=True, now_iso=T_ACTIVE
            )
            self.assertNotIn(drop_book, [b["book_id"] for b in after_view["drifting_bookbox"]])
            self.assertNotIn(drop_notice, [n["notice_id"] for n in after_view["bulletin_followups"]])
            self.assertGreaterEqual(len(after_view["drifting_bookbox"]), 1)

    def test_03_v05_home_craft_view_filters_forgotten_proposals_and_postcards(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s4-v05-craft-") as td:
            root = Path(td)
            c1, _ = v05_home_craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "propose_arrangement_change",
                    "object_id": "warm_desk_lamp",
                    "target_zone": "window_sill",
                    "reason_cn": "靠窗光线柔和些",
                    "operation_key": "op_hc_prop1",
                },
                has_subject_capability=True,
                now_iso=T0,
            )
            self.assertEqual(c1, 200)
            c2, _ = v05_home_craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "mint_parallel_postcard",
                    "greeting_cn": "寄一张秋日小卡",
                    "sender_label_cn": "TA",
                    "town_name_cn": "静巷镇",
                    "operation_key": "op_hc_pc1",
                },
                has_subject_capability=True,
                now_iso=T0,
            )
            self.assertEqual(c2, 200)

            _, before_view = self._assert_zero_write_view(
                root, v05_home_craft_view.read_v05_home_craft_view, has_subject_capability=True, now_iso=T0
            )
            prop_id = before_view["home_arrangement"]["proposals"][0]["proposal_id"]
            pc_id = before_view["parallel_postcards"]["sent"][0]["postcard_id"]

            st, _ = rlf.execute_explicit_forget(
                root, source_ids=[prop_id, pc_id], operation_id="op_fg_v05_hc", now_iso=T_ACTIVE
            )
            self.assertEqual(st, 200)

            _, after_view = self._assert_zero_write_view(
                root, v05_home_craft_view.read_v05_home_craft_view, has_subject_capability=True, now_iso=T_ACTIVE
            )
            self.assertEqual(after_view["home_arrangement"]["proposals"], [])
            self.assertEqual(after_view["home_arrangement"]["open_count"], 0)
            self.assertEqual(after_view["parallel_postcards"]["sent"], [])

    def test_04_v05_dreams_desires_view_filters_forgotten_desire_and_dream(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s4-v05-spark-") as td:
            root = Path(td)
            c1, _ = v05_dreams_desires_view.handle_v05_inner_spark_action(
                root,
                {
                    "action": "record_subject_desire",
                    "layer": "micro_craving",
                    "text": "想喝一杯桂花热茶",
                    "shared": True,
                    "operation_key": "op_des_1",
                },
                has_subject_capability=True,
                now_iso=T0,
            )
            self.assertEqual(c1, 200)
            c2, _ = v05_dreams_desires_view.handle_v05_inner_spark_action(
                root,
                {
                    "action": "record_subject_desire",
                    "layer": "micro_craving",
                    "text": "想把书桌重新理一遍",
                    "shared": True,
                    "operation_key": "op_des_2",
                },
                has_subject_capability=True,
                now_iso=T0,
            )
            self.assertEqual(c2, 200)

            _, before_view = self._assert_zero_write_view(
                root, v05_dreams_desires_view.read_v05_inner_spark_view, has_subject_capability=True, now_iso=T0
            )
            items = before_view["desires"]["items"]
            self.assertEqual(len(items), 2)
            drop_desire = items[0]["desire_id"]
            keep_desire = items[1]["desire_id"]

            st, _ = rlf.execute_explicit_forget(
                root, source_ids=[drop_desire], operation_id="op_fg_des", now_iso=T_ACTIVE
            )
            self.assertEqual(st, 200)

            _, after_view = self._assert_zero_write_view(
                root, v05_dreams_desires_view.read_v05_inner_spark_view, has_subject_capability=True, now_iso=T_ACTIVE
            )
            remaining_ids = [it["desire_id"] for it in after_view["desires"]["items"]]
            self.assertEqual(remaining_ids, [keep_desire])
            self.assertEqual(after_view["desires"]["counts"]["total_visible"], 1)

    def test_05_v04_neighborhood_view_filters_forgotten_notice(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s4-v04-nb-") as td:
            root = Path(td)
            c1, _ = v04_neighborhood_view.mutate_v04_neighborhood_view(
                root,
                {
                    "action": "post_public_notice",
                    "notice_id": "nb_notice_drop",
                    "title": "旧书店临时休业",
                    "body": "周三下午盘点",
                    "operation_key": "op_nb_n1",
                },
                actor_side="partner",
                now_iso=T0,
            )
            self.assertEqual(c1, 200)
            c2, _ = v04_neighborhood_view.mutate_v04_neighborhood_view(
                root,
                {
                    "action": "post_public_notice",
                    "notice_id": "nb_notice_keep",
                    "title": "街角面包房新出炉",
                    "body": "下午三点有热面包",
                    "operation_key": "op_nb_n2",
                },
                actor_side="partner",
                now_iso=T0,
            )
            self.assertEqual(c2, 200)

            st, _ = rlf.execute_explicit_forget(
                root, source_ids=["nb_notice_drop"], operation_id="op_fg_nb", now_iso=T_ACTIVE
            )
            self.assertEqual(st, 200)

            _, after_view = self._assert_zero_write_view(
                root, v04_neighborhood_view.read_v04_neighborhood_view, has_subject_capability=True, now_iso=T_ACTIVE
            )
            notice_ids = [n.get("notice_id") for n in after_view["public_notices"]]
            self.assertNotIn("nb_notice_drop", notice_ids)
            self.assertIn("nb_notice_keep", notice_ids)
            pub_notice_ids = [n.get("notice_id") for n in after_view["public_life"]["public_notices"]]
            self.assertNotIn("nb_notice_drop", pub_notice_ids)

    def test_06_v04_elastic_play_view_filters_forgotten_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s4-v04-ep-") as td:
            root = Path(td)
            c1, _ = v04_elastic_play_view.mutate_v04_elastic_play_view(
                root,
                {
                    "action": "curate_personal_project",
                    "project_op": "start",
                    "project_id": "proj_drop_01",
                    "purpose_cn": "记录雨声草图",
                    "medium_kind": "sketch_note",
                    "operation_key": "op_ep_p1",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(c1, 200)
            c2, _ = v04_elastic_play_view.mutate_v04_elastic_play_view(
                root,
                {
                    "action": "curate_personal_project",
                    "project_op": "start",
                    "project_id": "proj_keep_02",
                    "purpose_cn": "整理植物标本笔记",
                    "medium_kind": "sketch_note",
                    "operation_key": "op_ep_p2",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(c2, 200)

            st, _ = rlf.execute_explicit_forget(
                root, source_ids=["proj_drop_01"], operation_id="op_fg_ep", now_iso=T_ACTIVE
            )
            self.assertEqual(st, 200)

            _, after_view = self._assert_zero_write_view(
                root, v04_elastic_play_view.read_v04_elastic_play_view, has_subject_capability=True, now_iso=T_ACTIVE
            )
            proj_ids = [p.get("project_id") for p in after_view["personal_projects"]]
            self.assertNotIn("proj_drop_01", proj_ids)
            self.assertIn("proj_keep_02", proj_ids)

    def test_07_v03_ecology_view_filters_forgotten_goal_and_claim(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s4-v03-eco-") as td:
            root = Path(td)
            c1, _ = v03_ecology_view.mutate_v03_ecology_view(
                root,
                {
                    "action": "set_savings_goal",
                    "goal_id": "goal_drop_1",
                    "item_title": "旧胶片机",
                    "target_cents": 12000,
                    "shared_with_partner": True,
                    "operation_key": "op_eco_g1",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(c1, 200)
            c2, _ = v03_ecology_view.mutate_v03_ecology_view(
                root,
                {
                    "action": "set_savings_goal",
                    "goal_id": "goal_keep_2",
                    "item_title": "陶瓷茶壶",
                    "target_cents": 8000,
                    "shared_with_partner": True,
                    "operation_key": "op_eco_g2",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(c2, 200)

            st, _ = rlf.execute_explicit_forget(
                root, source_ids=["goal_drop_1"], operation_id="op_fg_eco", now_iso=T_ACTIVE
            )
            self.assertEqual(st, 200)

            _, after_view = self._assert_zero_write_view(
                root, v03_ecology_view.read_v03_ecology_view, has_subject_capability=True, now_iso=T_ACTIVE
            )
            goal_ids = [g.get("goal_id") for g in after_view["data"]["economy"]["savings_goals"]]
            self.assertNotIn("goal_drop_1", goal_ids)
            self.assertIn("goal_keep_2", goal_ids)

    def test_08_v02_agency_depth_view_filters_forgotten_rule_and_summary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s4-v02-ad-") as td:
            root = Path(td)
            c1, _ = v02_agency_depth_view.mutate_v02_agency_depth_view(
                root,
                {
                    "action": "manage_second_order_rule",
                    "rule_op": "propose",
                    "title": "晚间不喝浓茶",
                    "trigger_window": "evening",
                    "reason_cn": "怕睡不着",
                    "shared": True,
                    "operation_key": "op_ad_r1",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(c1, 200)
            c2, _ = v02_agency_depth_view.mutate_v02_agency_depth_view(
                root,
                {
                    "action": "manage_second_order_rule",
                    "rule_op": "propose",
                    "title": "午后出门带伞",
                    "trigger_window": "afternoon",
                    "reason_cn": "天气多变",
                    "shared": True,
                    "operation_key": "op_ad_r2",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(c2, 200)

            _, before_view = self._assert_zero_write_view(
                root, v02_agency_depth_view.read_v02_agency_depth_view, has_subject_capability=True, now_iso=T0
            )
            all_rule_ids = [r.get("rule_id") for r in before_view["second_order_rules"]]
            self.assertEqual(len(all_rule_ids), 2)
            drop_rule, keep_rule = all_rule_ids[0], all_rule_ids[1]

            st, _ = rlf.execute_explicit_forget(
                root, source_ids=[drop_rule], operation_id="op_fg_ad", now_iso=T_ACTIVE
            )
            self.assertEqual(st, 200)

            _, after_view = self._assert_zero_write_view(
                root, v02_agency_depth_view.read_v02_agency_depth_view, has_subject_capability=True, now_iso=T_ACTIVE
            )
            rule_ids = [r.get("rule_id") for r in after_view["second_order_rules"]]
            self.assertNotIn(drop_rule, rule_ids)
            self.assertIn(keep_rule, rule_ids)

            before_sum = _snapshot_tree(root)
            summary = v02_agency_depth_view.summarize_agency_depth_for_context(
                root, include_private=True, now_iso=T_ACTIVE
            )
            self.assertEqual(before_sum, _snapshot_tree(root))
            self.assertNotIn(drop_rule, json.dumps(summary, ensure_ascii=False))

    def test_09_v08_composable_body_view_filters_forgotten_sleep_activity_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s4-v08-body-") as td:
            base = Path(td)
            root = base / "inst"
            root.mkdir()
            c0, _ = v08_composable_body_view.mutate_v08_composable_body_view(
                root,
                {
                    "action": "configure_body_pack",
                    "pack_id": "nocturnal_emission",
                    "enabled": True,
                    "operation_key": "op_body_cfg",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(c0, 200)
            c1, _ = v08_composable_body_view.mutate_v08_composable_body_view(
                root,
                {
                    "action": "bind_sleep_activity",
                    "sleep_activity_id": "sleep_act_drop_01",
                    "started_at_iso": "2026-11-19T23:00:00+08:00",
                    "planned_wake_iso": "2026-11-20T07:00:00+08:00",
                    "operation_key": "op_sleep_1",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(c1, 200)
            c2, _ = v08_composable_body_view.mutate_v08_composable_body_view(
                root,
                {
                    "action": "bind_sleep_activity",
                    "sleep_activity_id": "sleep_act_keep_02",
                    "started_at_iso": "2026-11-20T23:00:00+08:00",
                    "planned_wake_iso": "2026-11-21T07:00:00+08:00",
                    "operation_key": "op_sleep_2",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(c2, 200)

            st, _ = rlf.execute_explicit_forget(
                root, source_ids=["sleep_act_drop_01"], operation_id="op_fg_body", now_iso=T_ACTIVE
            )
            self.assertEqual(st, 200)

            _, after_view = self._assert_zero_write_view(
                root, v08_composable_body_view.read_v08_composable_body_view, has_subject_capability=True, now_iso=T_ACTIVE
            )
            act_ids = [a.get("sleep_activity_id") for a in after_view["sleep_activities"]]
            self.assertNotIn("sleep_act_drop_01", act_ids)
            self.assertIn("sleep_act_keep_02", act_ids)

            # Corrupt authority.db -> view must fail closed cleanly with 0 disk writes
            auth_dir = base / f"{root.name}-memory-authority"
            auth_dir.mkdir()
            (auth_dir / "authority.db").write_bytes(b"CORRUPT_BODY_AUTHORITY")
            before_fc = _snapshot_tree(base)
            code_fc, body_fc = v08_composable_body_view.read_v08_composable_body_view(
                root, has_subject_capability=True, now_iso=T_ACTIVE
            )
            after_fc = _snapshot_tree(base)
            self.assertEqual(code_fc, 200)
            self.assertEqual(before_fc, after_fc)
            self.assertEqual(body_fc["sleep_activities"], [])
            self.assertIsNone(body_fc["shared_care_hint"])

    @unittest.skipIf(os.name != "posix", "Native authority mirror runs on POSIX")
    def test_10_visit_id_and_pref_id_forget_mirror_and_json_rollback_anti_resurrection(self) -> None:
        from memory_authority import MemoryDeletionAuthority
        from memory_control import MemoryControlLedger

        with tempfile.TemporaryDirectory(prefix="s4-visit-pref-rb-") as td:
            base = Path(td)
            root = base / "inst"
            mem_dir = root / "memory"
            auth_dir = base / f"{root.name}-memory-authority"
            mem_dir.mkdir(parents=True)
            auth_dir.mkdir(parents=True)

            evidence_db = mem_dir / "evidence.db"
            controls_db = mem_dir / "controls.db"
            authority_db = auth_dir / "authority.db"
            MemoryControlLedger(
                controls_db, evidence_db, owner=OWNER, conversation=CONVERSATION
            ).initialize()
            MemoryDeletionAuthority(
                authority_db, owner=OWNER, conversation=CONVERSATION
            ).initialize()

            # 1. Seed 2 third-space visits (visit_id) in v05_living_town_view
            c_v1, _ = v05_living_town_view.handle_v05_living_town_action(
                root,
                {
                    "action": "visit_third_space",
                    "spot_id": "coin_laundromat",
                    "note_cn": "TOKEN_TOWN_BLK",
                    "operation_key": "op_ts_blk",
                },
                has_subject_capability=True,
                now_iso=T0,
            )
            self.assertEqual(c_v1, 200)
            c_v2, _ = v05_living_town_view.handle_v05_living_town_action(
                root,
                {
                    "action": "visit_third_space",
                    "spot_id": "hillside_overlook",
                    "note_cn": "TOKEN_TOWN_KEEP",
                    "operation_key": "op_ts_keep",
                },
                has_subject_capability=True,
                now_iso=T0,
            )
            self.assertEqual(c_v2, 200)

            # 2. Seed 2 preferences (pref_id) in v02_agency_depth_view
            c_p1, _ = v02_agency_depth_view.mutate_v02_agency_depth_view(
                root,
                {
                    "action": "record_choice_episode",
                    "domain": "tea",
                    "chosen": "TOKEN_PREF_BLK",
                    "options": ["TOKEN_PREF_BLK", "barley_tea"],
                    "choice_mode": "spontaneous",
                    "shared": True,
                    "operation_key": "op_pref_blk",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(c_p1, 200)
            c_p2, _ = v02_agency_depth_view.mutate_v02_agency_depth_view(
                root,
                {
                    "action": "record_choice_episode",
                    "domain": "music",
                    "chosen": "TOKEN_PREF_KEEP",
                    "options": ["TOKEN_PREF_KEEP", "silence"],
                    "choice_mode": "spontaneous",
                    "shared": True,
                    "operation_key": "op_pref_keep",
                },
                actor_side="subject",
                now_iso=T0,
            )
            self.assertEqual(c_p2, 200)

            _, town_before = self._assert_zero_write_view(
                root, v05_living_town_view.read_v05_living_town_view, has_subject_capability=True, now_iso=T0
            )
            visits_before = town_before["third_spaces"]["recent_third_space_visits"]
            drop_visit = next(v["visit_id"] for v in visits_before if v.get("note_cn") == "TOKEN_TOWN_BLK")
            keep_visit = next(v["visit_id"] for v in visits_before if v.get("note_cn") == "TOKEN_TOWN_KEEP")

            _, ad_before = self._assert_zero_write_view(
                root, v02_agency_depth_view.read_v02_agency_depth_view, has_subject_capability=True, now_iso=T0
            )
            prefs_before = ad_before["preferences"]
            drop_pref = next(p["pref_id"] for p in prefs_before if p.get("chosen") == "TOKEN_PREF_BLK")
            keep_pref = next(p["pref_id"] for p in prefs_before if p.get("chosen") == "TOKEN_PREF_KEEP")

            # 3. Ingest both IDs into v02 forget ledger and snapshot pre-forget JSON sidecars
            st_i1, _ = rlf.ingest_source(
                root, source_id=drop_visit, text="visit", operation_id="op_ing_v", now_iso=T0
            )
            st_i2, _ = rlf.ingest_source(
                root, source_id=drop_pref, text="pref", operation_id="op_ing_p", now_iso=T0
            )
            self.assertEqual((st_i1, st_i2), (200, 200))

            gov_path = root / "governance" / "v02_representation_lease_forget_state.json"
            ctrl_path = root / "control_authority" / "v02_control_highwater_ledger.json"
            gov_backup = base / "gov_vp_before.json"
            ctrl_backup = base / "ctrl_vp_before.json"
            shutil.copy2(gov_path, gov_backup)
            shutil.copy2(ctrl_path, ctrl_backup)

            # 4. Explicit forget -> mirrors into native authority.db
            st_fg, resp_fg = rlf.execute_explicit_forget(
                root, source_ids=[drop_visit, drop_pref], operation_id="op_fg_vp", now_iso=T_ACTIVE
            )
            self.assertEqual(st_fg, 200)
            self.assertTrue(resp_fg.get("native_authority_mirrored"))

            auth_proj = MemoryDeletionAuthority(
                authority_db, owner=OWNER, conversation=CONVERSATION
            ).projection()
            self.assertTrue(auth_proj.healthy)
            self.assertIn(drop_visit, auth_proj.blocked_source_refs)
            self.assertIn(drop_pref, auth_proj.blocked_source_refs)

            # 5. Pre-rollback view reads must hide forgotten items and keep siblings (0 writes)
            _, town_mid = self._assert_zero_write_view(
                root, v05_living_town_view.read_v05_living_town_view, has_subject_capability=True, now_iso=T_ACTIVE
            )
            mid_visit_ids = [v["visit_id"] for v in town_mid["third_spaces"]["recent_third_space_visits"]]
            self.assertNotIn(drop_visit, mid_visit_ids)
            self.assertIn(keep_visit, mid_visit_ids)
            self.assertNotIn("TOKEN_TOWN_BLK", json.dumps(town_mid, ensure_ascii=False))
            self.assertIn("TOKEN_TOWN_KEEP", json.dumps(town_mid, ensure_ascii=False))

            _, ad_mid = self._assert_zero_write_view(
                root, v02_agency_depth_view.read_v02_agency_depth_view, has_subject_capability=True, now_iso=T_ACTIVE
            )
            mid_pref_ids = [p["pref_id"] for p in ad_mid["preferences"]]
            self.assertNotIn(drop_pref, mid_pref_ids)
            self.assertIn(keep_pref, mid_pref_ids)
            self.assertNotIn("TOKEN_PREF_BLK", json.dumps(ad_mid["preferences"], ensure_ascii=False))
            self.assertIn("TOKEN_PREF_KEEP", json.dumps(ad_mid["preferences"], ensure_ascii=False))

            # 6. Roll back BOTH v02 JSON sidecar files to pre-forget snapshot!
            shutil.copy2(gov_backup, gov_path)
            shutil.copy2(ctrl_backup, ctrl_path)

            # 7. Anti-resurrection proof across BOTH views with 0 disk writes (no -wal/-shm)
            before_rb = _snapshot_tree(base)
            c_t, town_after = v05_living_town_view.read_v05_living_town_view(
                root, has_subject_capability=True, now_iso=T_ACTIVE
            )
            c_a, ad_after = v02_agency_depth_view.read_v02_agency_depth_view(
                root, has_subject_capability=True, now_iso=T_ACTIVE
            )
            after_rb = _snapshot_tree(base)
            self.assertEqual((c_t, c_a), (200, 200))
            self.assertEqual(before_rb, after_rb)

            after_visit_ids = [v["visit_id"] for v in town_after["third_spaces"]["recent_third_space_visits"]]
            self.assertNotIn(drop_visit, after_visit_ids)
            self.assertIn(keep_visit, after_visit_ids)
            self.assertNotIn("TOKEN_TOWN_BLK", json.dumps(town_after, ensure_ascii=False))
            self.assertIn("TOKEN_TOWN_KEEP", json.dumps(town_after, ensure_ascii=False))

            after_pref_ids = [p["pref_id"] for p in ad_after["preferences"]]
            self.assertNotIn(drop_pref, after_pref_ids)
            self.assertIn(keep_pref, after_pref_ids)
            self.assertNotIn("TOKEN_PREF_BLK", json.dumps(ad_after["preferences"], ensure_ascii=False))
            self.assertIn("TOKEN_PREF_KEEP", json.dumps(ad_after["preferences"], ensure_ascii=False))


class Stage4PhoneServiceRepresentationLeaseTest(unittest.TestCase):
    def _make_service(self, root: Path, *, now_iso: str) -> phone_service.PhoneService:
        instance = {
            "root": str(root),
            "subject": OWNER,
            "world_home": str(root / "world"),
            "supply_socket": str(root / "run" / "supply.sock"),
            "private_items_artifact_id": "art_private_items",
            "artifact_grant_id": "grant_1",
            "workspace_id": "ws_1",
        }
        return phone_service.PhoneService(instance, clock=lambda: now_iso)

    def _wire_mock_backends(self, svc: phone_service.PhoneService) -> dict[str, MagicMock]:
        fake_outcome = MagicMock(applied=True, owner_status="APPLIED", effect_state="COMMITTED", detail="")
        fake_items = MagicMock()
        fake_items.add_diary.return_value = fake_outcome
        fake_items.item.return_value = {"created_at": T0, "chars": 12}

        fake_projects = MagicMock()
        fake_projects.create_project.return_value = {
            "ok": True,
            "status": "applied",
            "artifact_id": "proj_1",
            "version_id": "ver_1",
            "version_number": 1,
        }
        fake_projects.create_derived_project.return_value = {
            "ok": True,
            "status": "applied",
            "artifact_id": "proj_derived_1",
            "version_id": "ver_1",
            "version_number": 1,
        }
        fake_projects.commit_project.return_value = {
            "ok": True,
            "status": "applied",
            "artifact_id": "proj_1",
            "version_id": "ver_2",
            "version_number": 2,
        }

        fake_research = MagicMock()
        fake_research.create_research.return_value = {
            "ok": True,
            "status": "ok",
            "artifact_id": "res_1",
            "version_id": "rver_1",
        }
        fake_research.update_research.return_value = {
            "ok": True,
            "status": "ok",
            "artifact_id": "res_1",
            "version_id": "rver_2",
        }

        svc._items = MagicMock(return_value=fake_items)  # type: ignore[method-assign]
        svc._head = MagicMock(return_value=("head_1", 1))  # type: ignore[method-assign]
        svc._projects_service = MagicMock(return_value=fake_projects)  # type: ignore[method-assign]
        svc._continuable_guard = MagicMock(return_value=None)  # type: ignore[method-assign]
        svc._sync_project_to_rlf_representation = MagicMock()  # type: ignore[method-assign]
        svc._research_service = MagicMock(return_value=fake_research)  # type: ignore[method-assign]
        return {"items": fake_items, "projects": fake_projects, "research": fake_research}

    def test_01_diary_write_enforces_private_diary_lease(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s4-lease-diary-") as td:
            root = Path(td)
            # 1. No lease -> allowed (backward-compatible)
            svc_none = self._make_service(root, now_iso=T_ACTIVE)
            mocks = self._wire_mock_backends(svc_none)
            res_none = svc_none.diary_write(body="今天天气不错", operation_key="op_diary_1")
            self.assertTrue(res_none["ok"])
            self.assertEqual(mocks["items"].add_diary.call_count, 1)

            # 2. Active lease -> allowed
            rlf.upsert_or_revoke_representation_lease(
                root, action="issue", scope="private_diary", ttl_minutes=120, now_iso=T0
            )
            svc_active = self._make_service(root, now_iso=T_ACTIVE)
            mocks_act = self._wire_mock_backends(svc_active)
            res_act = svc_active.diary_write(body="午后喝了热茶", operation_key="op_diary_2")
            self.assertTrue(res_act["ok"])
            self.assertEqual(mocks_act["items"].add_diary.call_count, 1)

            # 3. Expired lease -> 403 REPRESENTATION_LEASE_EXPIRED & backend NOT called
            svc_exp = self._make_service(root, now_iso=T_EXPIRED)
            mocks_exp = self._wire_mock_backends(svc_exp)
            res_exp = svc_exp.diary_write(body="不应写入", operation_key="op_diary_3")
            self.assertFalse(res_exp["ok"])
            self.assertEqual(res_exp.get("http_status"), 403)
            self.assertEqual(res_exp.get("reason_code"), "REPRESENTATION_LEASE_EXPIRED")
            self.assertEqual(mocks_exp["items"].add_diary.call_count, 0)

            # 4. Revoked lease -> 403 REPRESENTATION_LEASE_REVOKED & backend NOT called
            rlf.upsert_or_revoke_representation_lease(
                root, action="revoke", scope="private_diary", reason="暂停私密日记写入", now_iso=T_ACTIVE
            )
            svc_rev = self._make_service(root, now_iso=T_ACTIVE)
            mocks_rev = self._wire_mock_backends(svc_rev)
            res_rev = svc_rev.diary_write(body="吊销后不应写入", operation_key="op_diary_4")
            self.assertFalse(res_rev["ok"])
            self.assertEqual(res_rev.get("http_status"), 403)
            self.assertEqual(res_rev.get("reason_code"), "REPRESENTATION_LEASE_REVOKED")
            self.assertEqual(mocks_rev["items"].add_diary.call_count, 0)

    def test_02_project_and_research_mutations_enforce_personal_artifact_lease(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s4-lease-artifact-") as td:
            root = Path(td)
            # 1. No lease -> all project/research create & commit mutations allowed
            svc_none = self._make_service(root, now_iso=T_ACTIVE)
            mocks_none = self._wire_mock_backends(svc_none)
            self.assertTrue(
                svc_none.create_project(title="笔记", content="正文", operation_key="op_p1")["ok"]
            )
            self.assertTrue(
                svc_none.create_derived_project(
                    title="衍生",
                    content="正文",
                    source_artifact_id="src_1",
                    source_version_id="sv_1",
                    purpose="continue",
                    operation_key="op_pd1",
                )["ok"]
            )
            self.assertTrue(
                svc_none.commit_project(
                    artifact_id="proj_1", content="v2", expected_head="ver_1", operation_key="op_pc1"
                )["ok"]
            )
            self.assertTrue(
                svc_none.research_create(
                    question="问题", source_refs=["mat_1"], operation_key="op_rc1"
                )["ok"]
            )
            self.assertTrue(
                svc_none.research_update(
                    artifact_id="res_1", content="更新", expected_head="rver_1", operation_key="op_ru1"
                )["ok"]
            )
            self.assertEqual(mocks_none["projects"].create_project.call_count, 1)
            self.assertEqual(mocks_none["research"].create_research.call_count, 1)

            # 2. Active lease -> allowed
            rlf.upsert_or_revoke_representation_lease(
                root, action="issue", scope="personal_artifact", ttl_minutes=120, now_iso=T0
            )
            svc_act = self._make_service(root, now_iso=T_ACTIVE)
            self._wire_mock_backends(svc_act)
            self.assertTrue(
                svc_act.create_project(title="租约内", content="正文", operation_key="op_p2")["ok"]
            )

            # 3. Expired lease -> all 5 mutations return 403 REPRESENTATION_LEASE_EXPIRED
            svc_exp = self._make_service(root, now_iso=T_EXPIRED)
            mocks_exp = self._wire_mock_backends(svc_exp)
            for res in (
                svc_exp.create_project(title="x", content="y", operation_key="op_e1"),
                svc_exp.create_derived_project(
                    title="x",
                    content="y",
                    source_artifact_id="s1",
                    source_version_id="v1",
                    purpose="continue",
                    operation_key="op_e2",
                ),
                svc_exp.commit_project(
                    artifact_id="proj_1", content="y", expected_head="v1", operation_key="op_e3"
                ),
                svc_exp.research_create(question="q", source_refs=["m1"], operation_key="op_e4"),
                svc_exp.research_update(
                    artifact_id="res_1", content="y", expected_head="v1", operation_key="op_e5"
                ),
            ):
                self.assertFalse(res["ok"])
                self.assertEqual(res.get("http_status"), 403)
                self.assertEqual(res.get("reason_code"), "REPRESENTATION_LEASE_EXPIRED")
            self.assertEqual(mocks_exp["projects"].create_project.call_count, 0)
            self.assertEqual(mocks_exp["projects"].commit_project.call_count, 0)
            self.assertEqual(mocks_exp["research"].create_research.call_count, 0)
            self.assertEqual(mocks_exp["research"].update_research.call_count, 0)

            # 4. Revoked lease -> all 5 mutations return 403 REPRESENTATION_LEASE_REVOKED
            rlf.upsert_or_revoke_representation_lease(
                root, action="revoke", scope="personal_artifact", reason="吊销个人作品写入", now_iso=T_ACTIVE
            )
            svc_rev = self._make_service(root, now_iso=T_ACTIVE)
            mocks_rev = self._wire_mock_backends(svc_rev)
            for res in (
                svc_rev.create_project(title="x", content="y", operation_key="op_r1"),
                svc_rev.create_derived_project(
                    title="x",
                    content="y",
                    source_artifact_id="s1",
                    source_version_id="v1",
                    purpose="continue",
                    operation_key="op_r2",
                ),
                svc_rev.commit_project(
                    artifact_id="proj_1", content="y", expected_head="v1", operation_key="op_r3"
                ),
                svc_rev.research_create(question="q", source_refs=["m1"], operation_key="op_r4"),
                svc_rev.research_update(
                    artifact_id="res_1", content="y", expected_head="v1", operation_key="op_r5"
                ),
            ):
                self.assertFalse(res["ok"])
                self.assertEqual(res.get("http_status"), 403)
                self.assertEqual(res.get("reason_code"), "REPRESENTATION_LEASE_REVOKED")
            self.assertEqual(mocks_rev["projects"].create_project.call_count, 0)
            self.assertEqual(mocks_rev["research"].update_research.call_count, 0)


if __name__ == "__main__":
    unittest.main()

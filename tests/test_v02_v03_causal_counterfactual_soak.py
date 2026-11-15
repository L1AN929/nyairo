#!/usr/bin/env python3
"""0.2 + 0.3 自主主链生产者-消费者闭环、跨日连续推演与 2×2 因果反证验收套件。

覆盖《14-研发工作包与验收体系》V-A .. V-E 及第 6 节受控因果反证：
- 全程零真实在线大模型调用（``llm_calls == 0``）；
- 严校验冻结锚点 SHA-256（``autonomous_life_assembly.py`` / ``life_sharing_service.py``）；
- 100% 向后兼容无 ``phone/*.json`` 的既有 ``v0.7`` 自主主链实例。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(LIFE_SCRIPTS), str(WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from autonomous_life_resident import AutonomousLifeResident  # noqa: E402
from candidate_sources_ag0 import (  # noqa: E402
    FORBIDDEN_FACT_BODY_KEYS,
    FORBIDDEN_RANKING_AND_DECISION_KEYS,
)
from life_artifact_port import (  # noqa: E402
    ArtifactOutcome,
    ArtifactOwnerRefused,
    EffectState,
)
from life_future_items import FutureItems  # noqa: E402
from life_sharing_service import LifeSharingService  # noqa: E402
from private_project_service import PrivateProjectService  # noqa: E402
import v02_v03_daily_loop_bridge as bridge  # noqa: E402
import v03_clock_evaporation as v03_clock  # noqa: E402
import v03_economy_illness_bridge as v03_eco_bridge  # noqa: E402

from components.phone import (  # noqa: E402
    autonomy_rhythm_view,
    emotional_tension_view,
    home_hearth_view,
    long_companionship_view,
    outing_places_view,
    somatic_comfort_view,
    v03_ecology_view,
    v03_embodied_social_view,
    window_keepsake_view,
    workbench_curio_view,
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, str]]:
    """Capture ``rel_path -> (mtime_ns, sha256)`` across ``root`` for strict zero-write assertions."""
    out: dict[str, tuple[int, str]] = {}
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != ".":
            st = os.stat(dirpath)
            out[f"dir:{rel_dir}"] = (st.st_mtime_ns, "DIR")
        for fn in filenames:
            fp = Path(dirpath) / fn
            st = fp.stat()
            digest = hashlib.sha256(fp.read_bytes()).hexdigest()
            out[f"file:{os.path.relpath(fp, root)}"] = (st.st_mtime_ns, digest)
    return out


class InMemoryManagedArtifactPort:
    """Deterministic owner-shaped port for PrivateProjectService & LifeSharingService (0 LLM calls)."""

    def __init__(self, subject_id: str = "chiyo") -> None:
        self.subject_id = subject_id
        self._seq = 0
        self._artifacts: dict[str, dict[str, Any]] = {}
        self._versions: dict[str, list[dict[str, Any]]] = {}
        self._ops: dict[str, ArtifactOutcome] = {}
        self._governed_blocked: set[str] = set()
        self.now_iso = "2026-06-10T10:00:00+08:00"

    def tombstone_or_govern_source(self, artifact_id: str) -> None:
        self._governed_blocked.add(artifact_id)

    def list_artifacts(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for aid, row in self._artifacts.items():
            if aid in self._governed_blocked or row.get("lifecycle") == "TOMBSTONED":
                continue
            rows.append(copy.deepcopy(row))
        return rows

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        if artifact_id in self._governed_blocked:
            raise ArtifactOwnerRefused("DENY", "SOURCE_GOVERNED", "governed/revoked source")
        row = self._artifacts.get(artifact_id)
        if row is None or row.get("lifecycle") == "TOMBSTONED":
            raise ArtifactOwnerRefused("NOT_FOUND", "ARTIFACT_NOT_FOUND", "not found")
        vers = self._versions[artifact_id]
        head = vers[-1]
        return {
            "artifact": copy.deepcopy(row),
            "head": copy.deepcopy(head),
            "content": head["content"],
        }

    def list_versions(
        self,
        artifact_id: str,
        *,
        limit: int = 20,
        before_version_number: Optional[int] = None,
    ) -> dict[str, Any]:
        if artifact_id in self._governed_blocked:
            raise ArtifactOwnerRefused("DENY", "SOURCE_GOVERNED", "governed/revoked source")
        row = self._artifacts.get(artifact_id)
        if row is None:
            raise ArtifactOwnerRefused("NOT_FOUND", "ARTIFACT_NOT_FOUND", "not found")
        vers = list(reversed(self._versions[artifact_id]))
        if before_version_number is not None:
            vers = [v for v in vers if int(v["version_number"]) < int(before_version_number)]
        sliced = copy.deepcopy(vers[: max(1, int(limit))])
        next_cur = (
            int(sliced[-1]["version_number"])
            if len(vers) > len(sliced) and sliced
            else None
        )
        return {
            "entries": sliced,
            "complete": len(vers) <= len(sliced),
            "total_versions": len(self._versions[artifact_id]),
            "next_before_version_number": next_cur,
            "source": "ManagedArtifact",
        }

    def read_version(self, artifact_id: str, version_id: str) -> dict[str, Any]:
        if artifact_id in self._governed_blocked:
            raise ArtifactOwnerRefused("DENY", "SOURCE_GOVERNED", "governed/revoked source")
        if artifact_id not in self._artifacts:
            raise ArtifactOwnerRefused("NOT_FOUND", "ARTIFACT_NOT_FOUND", "not found")
        for ver in self._versions[artifact_id]:
            if ver["version_id"] == version_id:
                return copy.deepcopy(ver)
        raise ArtifactOwnerRefused("NOT_FOUND", "VERSION_NOT_FOUND", "version not in artifact")

    def create_artifact(
        self,
        *,
        workspace_id: str,
        title: str,
        content: str,
        grant_id: str,
        operation_key: str,
        source_refs: Iterable[Any] = (),
    ) -> ArtifactOutcome:
        if operation_key in self._ops:
            prev = copy.deepcopy(self._ops[operation_key])
            prev.effect_state = EffectState.NO_CHANGE
            return prev
        self._seq += 1
        aid = f"artifact:{hashlib.sha256(f'{self._seq}:{title}'.encode()).hexdigest()[:16]}"
        vid = f"artifact-version:{hashlib.sha256(f'{aid}:v1'.encode()).hexdigest()[:16]}"
        c_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        vrow = {
            "artifact_id": aid,
            "version_id": vid,
            "version_number": 1,
            "parent_version_id": None,
            "created_at": self.now_iso,
            "operation_key": operation_key,
            "content": content,
            "content_chars": len(content),
            "content_sha256": c_sha,
            "content_truncated": False,
        }
        arow = {
            "artifact_id": aid,
            "workspace_id": workspace_id,
            "title": title,
            "lifecycle": "ACTIVE",
            "revision": 1,
            "head_version_id": vid,
            "created_at": self.now_iso,
            "updated_at": self.now_iso,
        }
        self._artifacts[aid] = arow
        self._versions[aid] = [vrow]
        out = ArtifactOutcome(
            effect_state=EffectState.APPLIED,
            status="OK",
            artifact_id=aid,
            version_id=vid,
            version_number=1,
            head_version_id=vid,
            parent_version_id=None,
            version_created=True,
            operation_key=operation_key,
            lifecycle="ACTIVE",
            revision=1,
            raw={"status": "OK", "artifact_id": aid, "version_id": vid, "version_number": 1},
        )
        self._ops[operation_key] = copy.deepcopy(out)
        return out

    def commit_version(
        self,
        *,
        artifact_id: str,
        expected_head: str,
        content: str,
        grant_id: str,
        operation_key: str,
        **_: Any,
    ) -> ArtifactOutcome:
        if operation_key in self._ops:
            prev = copy.deepcopy(self._ops[operation_key])
            prev.effect_state = EffectState.NO_CHANGE
            return prev
        row = self._artifacts.get(artifact_id)
        if row is None:
            return ArtifactOutcome(effect_state=EffectState.NOT_FOUND, status="NOT_FOUND", reason="NOT_FOUND")
        if row["lifecycle"] != "ACTIVE":
            return ArtifactOutcome(
                effect_state=EffectState.REFUSED, status="DENY", reason="ARTIFACT_ARCHIVED"
            )
        if row["head_version_id"] != expected_head:
            return ArtifactOutcome(
                effect_state=EffectState.CONFLICT, status="CONFLICT", reason="CAS_MISMATCH"
            )
        vers = self._versions[artifact_id]
        next_num = len(vers) + 1
        vid = f"artifact-version:{hashlib.sha256(f'{artifact_id}:v{next_num}:{operation_key}'.encode()).hexdigest()[:16]}"
        c_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        vrow = {
            "artifact_id": artifact_id,
            "version_id": vid,
            "version_number": next_num,
            "parent_version_id": expected_head,
            "created_at": self.now_iso,
            "operation_key": operation_key,
            "content": content,
            "content_chars": len(content),
            "content_sha256": c_sha,
            "content_truncated": False,
        }
        vers.append(vrow)
        row["head_version_id"] = vid
        row["revision"] = int(row["revision"]) + 1
        row["updated_at"] = self.now_iso
        out = ArtifactOutcome(
            effect_state=EffectState.APPLIED,
            status="OK",
            artifact_id=artifact_id,
            version_id=vid,
            version_number=next_num,
            head_version_id=vid,
            parent_version_id=expected_head,
            version_created=True,
            operation_key=operation_key,
            lifecycle="ACTIVE",
            revision=row["revision"],
            raw={"status": "OK", "artifact_id": artifact_id, "version_id": vid, "version_number": next_num},
        )
        self._ops[operation_key] = copy.deepcopy(out)
        return out

    def archive_artifact(
        self,
        *,
        artifact_id: str,
        expected_revision: int,
        grant_id: str,
        operation_key: str,
    ) -> ArtifactOutcome:
        if operation_key in self._ops:
            prev = copy.deepcopy(self._ops[operation_key])
            prev.effect_state = EffectState.NO_CHANGE
            return prev
        row = self._artifacts.get(artifact_id)
        if row is None:
            return ArtifactOutcome(effect_state=EffectState.NOT_FOUND, status="NOT_FOUND", reason="NOT_FOUND")
        if int(row["revision"]) != int(expected_revision):
            return ArtifactOutcome(
                effect_state=EffectState.CONFLICT, status="CONFLICT", reason="REVISION_MISMATCH"
            )
        row["lifecycle"] = "ARCHIVED"
        row["revision"] = int(row["revision"]) + 1
        row["updated_at"] = self.now_iso
        out = ArtifactOutcome(
            effect_state=EffectState.APPLIED,
            status="OK",
            artifact_id=artifact_id,
            head_version_id=row["head_version_id"],
            operation_key=operation_key,
            lifecycle="ARCHIVED",
            revision=row["revision"],
            raw={"status": "OK", "lifecycle": "ARCHIVED", "revision": row["revision"]},
        )
        self._ops[operation_key] = copy.deepcopy(out)
        return out


class TestV02V03CausalCounterfactualSoak(unittest.TestCase):
    """22 deterministic acceptance tests for 0.2+0.3 daily loop bridge, soak & counterfactuals."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nyairo_v02v03_soak_")
        self.tmp_root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _new_day_root(self, name: str) -> Path:
        root = self.tmp_root / name
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _clone_day_root(self, src: Path, dst_name: str) -> Path:
        dst = self.tmp_root / dst_name
        shutil.copytree(src, dst)
        return dst

    # =========================================================================
    # Group 0: Frozen SHA-256 Anchors & Producer Zero-Write / AG-0 Contract
    # =========================================================================

    def test_01_frozen_anchor_sha256_integrity(self) -> None:
        """V-A Anchor: frozen assembly & sharing service files match exact SHA-256 digests."""
        report = bridge.verify_frozen_anchors(REPO_ROOT)
        self.assertTrue(report["ok"], f"Frozen SHA256 mismatch: {report}")
        self.assertEqual(report["llm_calls"], 0)
        self.assertEqual(
            report["files"]["components/life/scripts/autonomous_life_assembly.py"]["actual_sha256"],
            "5f51c9e82a417d9354d08608841d3472fc5a869e6bd3c9d9a7ebed0b15397f7a",
        )
        self.assertEqual(
            report["files"]["components/life/scripts/life_sharing_service.py"]["actual_sha256"],
            "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
        )

    def test_02_producer_pure_zero_write_on_empty_and_nonexistent_day_root(self) -> None:
        """Producer is 100% zero-write when day_root is missing or empty."""
        missing_root = self.tmp_root / "does_not_exist_yet"
        res_missing = bridge.collect_v02_v03_loop_candidates(
            missing_root, now_iso="2026-06-10T09:00:00+08:00"
        )
        self.assertFalse(missing_root.exists())
        self.assertEqual(res_missing["candidates"], [])
        self.assertEqual(res_missing["llm_calls"], 0)
        self.assertTrue(res_missing["zero_write"])

        empty_root = self._new_day_root("empty_day")
        snap_before = _snapshot_tree(empty_root)
        self.assertFalse(bridge.has_v02_v03_active_state(empty_root))
        res_empty = bridge.collect_v02_v03_loop_candidates(
            empty_root, now_iso="2026-06-10T09:00:00+08:00"
        )
        snap_after = _snapshot_tree(empty_root)
        self.assertEqual(snap_before, snap_after)
        self.assertEqual(res_empty["candidates"], [])
        self.assertEqual(res_empty["llm_calls"], 0)

    def test_03_producer_all_five_v02_clues_emit_valid_ag0_candidates_zero_write(self) -> None:
        """All 5 v0.2 life slices produce provenance-tagged CandidateRecords with zero disk writes."""
        day_root = self._new_day_root("v02_five_producers")
        now_iso = "2026-06-10T09:30:00+08:00"

        # 1. Bedside tomorrow note (FutureItems due today)
        fstore = FutureItems(day_root)
        note_row = fstore.propose(
            title="把窗边水彩草稿补完第二层阴影",
            due_at="2026-06-10T09:00:00+08:00",
            now="2026-06-09T22:30:00+08:00",
            source_refs=["artifact:watercolor_01"],
        )["item"]

        # 2. Home hearth tentative plan (TENTATIVE + DROPPED to verify status filter)
        p_state = home_hearth_view.empty_plans_state()
        p_state, _, _, err1 = home_hearth_view.propose_plan(
            p_state,
            plan_id=None,
            title="周末一起去海边旧书店逛逛",
            window_note="周六午后",
            proposer="partner",
            now_iso="2026-06-09T20:00:00+08:00",
        )
        self.assertIsNone(err1)
        p_state, _, dropped_p, err2 = home_hearth_view.propose_plan(
            p_state,
            plan_id=None,
            title="已经取消的旧提议",
            window_note="周日",
            proposer="her",
            now_iso="2026-06-09T20:05:00+08:00",
        )
        self.assertIsNone(err2)
        p_state, _, _, err3 = home_hearth_view.drop_plan(
            p_state,
            plan_id=dropped_p["plan_id"],
            now_iso="2026-06-09T21:00:00+08:00",
        )
        self.assertIsNone(err3)
        home_hearth_view.write_plans_state(day_root, p_state)

        # 3. Workbench unfinished draft tray
        t_state = workbench_curio_view.empty_tray_state()
        t_state, _ = workbench_curio_view.set_tray(
            t_state,
            {
                "images": [{"album_artifact_id": "artifact:ref_img_1"}],
                "materials": [{"artifact_id": "artifact:mat_note_1"}],
                "work": {
                    "artifact_id": "artifact:essay_draft_1",
                    "baseline_version_id": "artifact-version:v1",
                },
                "note": "写到第三节结尾意象",
            },
            "2026-06-09T23:00:00+08:00",
        )
        workbench_curio_view.write_tray_state(day_root, t_state)

        # 4. Bookstore window bookmark
        bb_state = outing_places_view.empty_bench_bookstore_state()
        bb_state, _, _ = outing_places_view.apply_bookmark_window_book(
            bb_state,
            slot_index=0,
            local_date="2026-06-09",
            shared=False,
            note="橱窗第二排左侧",
            note_supplied=True,
            now_iso="2026-06-09T17:00:00+08:00",
        )
        outing_places_view.write_bench_bookstore_state(day_root, bb_state)

        # 5. Potted plant sketch journal
        rh_state = long_companionship_view.empty_room_habits_state()
        rh_state, _, _ = long_companionship_view.apply_link_plant_sketch(
            rh_state,
            plant_id="fern_pot_01",
            plant_name="窗台铁线蕨",
            target_kind="project",
            target_artifact_id="artifact:fern_sketch_01",
            observation_note="新抽出一片卷曲嫩叶",
            shared=False,
            now_iso="2026-06-09T18:00:00+08:00",
        )
        long_companionship_view.write_room_habits_state(day_root, rh_state)

        snap_before = _snapshot_tree(day_root)
        collected = bridge.collect_v02_v03_loop_candidates(day_root, now_iso=now_iso)
        snap_after = _snapshot_tree(day_root)
        self.assertEqual(snap_before, snap_after, "collect_v02_v03_loop_candidates mutated files!")
        self.assertEqual(collected["llm_calls"], 0)

        descs = collected["descriptors"]
        domains = {d["clue_domain"] for d in descs.values()}
        self.assertIn("v02.desk_presence.tomorrow_note", domains)
        self.assertIn("v02.home_hearth.tentative_plans", domains)
        self.assertIn("v02.workbench.tray", domains)
        self.assertIn("v02.outing_bench_bookstore.window_bookmark", domains)
        self.assertIn("v02.longterm_first_room_habits.plant_sketch", domains)
        self.assertIn("v03.clock.meaningless_idle", domains)

        # Verify DROPPED plan was NOT emitted
        plan_refs = [d["source_ref"] for d in descs.values() if d["clue_domain"].endswith("tentative_plans")]
        self.assertEqual(len(plan_refs), 1)
        self.assertNotIn(f"plan:{dropped_p['plan_id']}", plan_refs)

        # Verify AG-0 CandidateRecord purity (no forbidden ranking or body keys)
        forbidden_all = set(FORBIDDEN_FACT_BODY_KEYS) | set(FORBIDDEN_RANKING_AND_DECISION_KEYS)
        for cdict in collected["candidates"]:
            self.assertEqual(set(cdict.keys()) & forbidden_all, set())
            self.assertTrue(cdict["provenance_refs"])
            self.assertTrue(cdict["causal_parent_refs"])
        self.assertTrue(any(d["source_ref"] == note_row["item_ref"] for d in descs.values()))

    # =========================================================================
    # Group V-A: Authority, Verified Receipts & Non-Optimistic Settlement
    # =========================================================================

    def test_04_va_unknown_failed_and_refused_effects_never_optimistically_settle(self) -> None:
        """V-A: effect_state in UNKNOWN / FAILED / REFUSED performs 0 writes on slices."""
        day_root = self._new_day_root("va_non_applied")
        fstore = FutureItems(day_root)
        item = fstore.propose(
            title="整理书架发票",
            due_at="2026-06-10T10:00:00+08:00",
            now="2026-06-10T08:00:00+08:00",
        )["item"]
        snap_before = _snapshot_tree(day_root)

        for eff in ("UNKNOWN", "FAILED", "REFUSED", ""):
            res = bridge.settle_v02_v03_round_consequences(
                day_root,
                {
                    "actor_side": "subject",
                    "effect_state": eff,
                    "action_kind": "FOLLOW_TOMORROW_NOTE",
                    "future_item_ref": item["item_ref"],
                    "receipt_ref": "rcpt:maybe_01",
                    "operation_key": f"op_non_applied_{eff or 'empty'}",
                },
                now_iso="2026-06-10T10:05:00+08:00",
            )
            self.assertFalse(res["settled"])
            self.assertEqual(res["optimistic_writes"], 0)
            self.assertEqual(res["slices_updated"], [])
            self.assertEqual(res["llm_calls"], 0)

        snap_after = _snapshot_tree(day_root)
        self.assertEqual(snap_before, snap_after)
        self.assertEqual(FutureItems(day_root).get(item["item_ref"]).status, "OPEN")

    def test_05_va_forged_receipt_and_missing_receipt_ref_refused_zero_writes(self) -> None:
        """V-A: Forged, unverified, NPC-claimed, or receipt-less APPLIED claims are refused."""
        day_root = self._new_day_root("va_forged_receipt")
        c_floor, _ = v03_ecology_view.mutate_v03_ecology_view(
            day_root,
            {
                "action": "configure_living_floor",
                "living_floor_cents": 4500,
                "operation_key": "init_floor",
            },
            actor_side="subject",
            now_iso="2026-06-10T08:00:00+08:00",
        )
        self.assertEqual(c_floor, 200)
        snap_before = _snapshot_tree(day_root)

        bad_events = [
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "COMPLETE_INDEPENDENT_WORK",
                "job_kind": "manuscript_fee",
                "receipt_ref": "rcpt:fake_1",
                "forged_receipt": True,
            },
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "COMPLETE_INDEPENDENT_WORK",
                "job_kind": "manuscript_fee",
                "receipt_ref": "rcpt:fake_2",
                "receipt_origin": "npc_speech",
            },
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "COMPLETE_INDEPENDENT_WORK",
                "job_kind": "manuscript_fee",
                "receipt_ref": "rcpt:fake_3",
                "owner_receipt_verified": False,
            },
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "COMPLETE_INDEPENDENT_WORK",
                "job_kind": "manuscript_fee",
                "receipt_ref": "",
            },
        ]
        for ev in bad_events:
            res = bridge.settle_v02_v03_round_consequences(
                day_root, ev, now_iso="2026-06-10T11:00:00+08:00"
            )
            self.assertFalse(res["ok"])
            self.assertFalse(res["settled"])
            self.assertEqual(res["status"], "REFUSED")
            self.assertEqual(res["optimistic_writes"], 0)

        self.assertEqual(snap_before, _snapshot_tree(day_root))

    def test_06_va_unauthorized_partner_or_npc_actor_cannot_settle_consequences(self) -> None:
        """V-A: Partner, NPC, or external actor_side cannot trigger subject slice settlement."""
        day_root = self._new_day_root("va_unauthorized_actor")
        snap_before = _snapshot_tree(day_root)
        for bad_actor in ("partner", "npc", "external", "observer"):
            res = bridge.settle_v02_v03_round_consequences(
                day_root,
                {
                    "actor_side": bad_actor,
                    "effect_state": "APPLIED",
                    "action_kind": "OUTING_VISIT",
                    "place_id": "park",
                    "receipt_ref": "rcpt:world_move_1",
                },
                now_iso="2026-06-10T12:00:00+08:00",
            )
            self.assertFalse(res["ok"])
            self.assertEqual(res["reason_code"], "UNAUTHORIZED_CONSEQUENCE_ACTOR")
            self.assertEqual(res["optimistic_writes"], 0)
        self.assertEqual(snap_before, _snapshot_tree(day_root))

    def test_07_va_operation_key_idempotency_and_payload_mismatch_conflict(self) -> None:
        """V-A: Replaying identical operation_key does not double-credit; mutated intent returns CONFLICT."""
        day_root = self._new_day_root("va_idempotency")
        ev = {
            "actor_side": "subject",
            "effect_state": "APPLIED",
            "action_kind": "COMPLETE_INDEPENDENT_WORK",
            "job_kind": "proofreading_shift",
            "amount_cents": 2600,
            "work_ref": "shift:jun10_am",
            "receipt_ref": "rcpt:work_jun10_am",
            "operation_key": "settle_shift_jun10_am",
        }
        first = bridge.settle_v02_v03_round_consequences(
            day_root, ev, now_iso="2026-06-10T13:00:00+08:00"
        )
        self.assertTrue(first["settled"])
        self.assertFalse(first["already_applied"])
        bal_after_first = first["details"]["independent_work"]["available_balance_cents"]
        self.assertGreater(bal_after_first, 0)

        # Exact replay -> already_applied, zero double-credit
        replay = bridge.settle_v02_v03_round_consequences(
            day_root, ev, now_iso="2026-06-10T13:05:00+08:00"
        )
        self.assertTrue(replay["already_applied"])
        _, eco_view = v03_ecology_view.read_v03_ecology_view(
            day_root, has_subject_capability=True, now_iso="2026-06-10T13:05:00+08:00"
        )
        self.assertEqual(eco_view["data"]["economy"]["available_balance_cents"], bal_after_first)

        # Same operation_key with tampered amount -> CONFLICT
        tampered = dict(ev, amount_cents=9000)
        conflict = bridge.settle_v02_v03_round_consequences(
            day_root, tampered, now_iso="2026-06-10T13:06:00+08:00"
        )
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["status"], "CONFLICT")
        self.assertEqual(conflict["reason_code"], "CONSEQUENCE_OPERATION_KEY_CONFLICT")

    def test_08_va_applied_non_home_outing_syncs_familiar_bench_and_footprints(self) -> None:
        """Consumer: APPLIED non-home place visit updates bench_bookstore & routes_market footprints."""
        day_root = self._new_day_root("va_outing_sync")
        # Home stay must NOT be accepted as non-home outing
        refused_home = bridge.settle_v02_v03_round_consequences(
            day_root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "OUTING_VISIT",
                "place_id": "home",
                "receipt_ref": "rcpt:home_0",
            },
            now_iso="2026-06-10T14:00:00+08:00",
        )
        self.assertFalse(refused_home["settled"])
        self.assertEqual(refused_home["reason_code"], "NON_HOME_PLACE_REQUIRED")

        # Two verified APPLIED visits to riverfront_park -> bench_visits logged & walk route synced
        for idx, ts in enumerate(("2026-06-10T15:00:00+08:00", "2026-06-11T15:30:00+08:00"), start=1):
            settled = bridge.settle_v02_v03_round_consequences(
                day_root,
                {
                    "actor_side": "subject",
                    "effect_state": "APPLIED",
                    "action_kind": "OUTING_VISIT",
                    "place_id": "riverfront_park",
                    "note": f"第{idx}次坐在河岸长椅吹风",
                    "album_artifact_id": f"artifact:riverfront_snap_{idx}",
                    "receipt_ref": f"rcpt:outing_{idx}",
                    "operation_key": f"op_outing_{idx}",
                },
                now_iso=ts,
            )
            self.assertTrue(settled["settled"])
            self.assertIn("outing_bench_bookstore", settled["slices_updated"])
            if idx == 1:
                self.assertIn("outing_routes_market", settled["slices_updated"])

        bb_state, _ = outing_places_view.read_bench_bookstore_state(day_root)
        river_visits = [v for v in bb_state["bench_visits"] if v["place_id"] == "riverfront_park"]
        self.assertEqual(len(river_visits), 2)
        rm_state, _ = outing_places_view.read_routes_market_state(day_root)
        self.assertGreaterEqual(len(rm_state["routes"]), 1)

    # =========================================================================
    # Group V-B: Persistence, Crash / Cold Restart & 3-Day Continuous Soak
    # =========================================================================

    def test_09_vb_three_day_continuous_soak_cold_restart_and_dual_layer_lantern(self) -> None:
        """V-B Full 3-Day Soak + Cold Restart + Overnight 0-LLM + Illness + 72h Missing Fade + Lantern."""
        day_root = self._new_day_root("vb_three_day_soak")
        port = InMemoryManagedArtifactPort(subject_id="chiyo")
        port.now_iso = "2026-06-10T21:00:00+08:00"
        projects = PrivateProjectService(port, "ws-soak", "grant-soak", subject_id="chiyo")
        sharing = LifeSharingService(projects)

        # ---------------------------------------------------------------------
        # DAY 1 (2026-06-10 21:30):
        # - Rain exposure + hang damp coat on entryway rack;
        # - Write private work v1 + leave bedside tomorrow note;
        # - Record EVAPORABLE fleeting thought + missing_partner note;
        # - Record unresolved UNKNOWN outbound action.
        # ---------------------------------------------------------------------
        d1_iso = "2026-06-10T21:30:00+08:00"
        created_work = projects.create_project(
            title="雨夜檐角速写随笔",
            content="第一版原稿：雨珠顺着旧木檐一滴一滴落在石阶上，不去改写此刻。",
            operation_key="soak_create_v1",
        )
        self.assertTrue(created_work["ok"])
        art_id = created_work["artifact_id"]
        v1_id = created_work["version_id"]

        # Seed workbench tray & empty lantern state for Day 2 progression
        wb_state = workbench_curio_view.empty_tray_state()
        wb_state, _ = workbench_curio_view.set_tray(
            wb_state,
            {
                "images": [],
                "materials": [],
                "work": {"artifact_id": art_id, "baseline_version_id": v1_id},
                "note": "留待明日补写雨停后的光影",
            },
            d1_iso,
        )
        workbench_curio_view.write_tray_state(day_root, wb_state)
        long_companionship_view.write_gifts_lantern_state(
            day_root, long_companionship_view.empty_gifts_lantern_state()
        )

        # Hang damp coat at 21:30
        som_state = somatic_comfort_view.empty_state()
        som_state, som_changed, som_err = somatic_comfort_view.hang_outerwear(
            som_state,
            garment_id="linen_trench_coat",
            hang_position="entryway_rack",
            dryness="damp",
            now_iso=d1_iso,
        )
        self.assertTrue(som_changed)
        self.assertIsNone(som_err)
        somatic_comfort_view.write_state(day_root, som_state)
        self.assertEqual(somatic_comfort_view.derive_garment(som_state, d1_iso)["dryness"], "damp")

        # Leave bedside tomorrow note due on Day 2 (2026-06-11 09:00)
        fstore_d1 = FutureItems(day_root)
        tmr_note = fstore_d1.propose(
            title="晨起顺着昨晚速写补完雨后石阶第二稿",
            due_at="2026-06-11T09:00:00+08:00",
            now=d1_iso,
            source_refs=[art_id],
        )["item"]

        # Record missing_partner note at Day 1 21:30 (72h window) + EVAPORABLE + UNKNOWN in v03_clock
        cm, _ = v03_clock.mutate_v03_clock_view(
            day_root,
            {
                "action": "declare_slow_variable",
                "var_id": "miss_rain_d1",
                "var_kind": "missing_note",
                "label": "下雨天忽然有点想TA，留一句轻话就好。",
                "started_at": d1_iso,
                "operation_key": "soak_miss_d1",
            },
            actor_side="subject",
            now_iso=d1_iso,
        )
        self.assertEqual(cm, 200)

        c1, _ = v03_clock.mutate_v03_clock_view(
            day_root,
            {
                "action": "record_evaporable",
                "domain_kind": "fleeting_thought",
                "text": "玻璃上滑过一道水痕",
                "ttl_seconds": 10800,
                "operation_key": "soak_evap_d1",
            },
            actor_side="subject",
            now_iso=d1_iso,
        )
        self.assertEqual(c1, 200)

        c2, _ = v03_clock.mutate_v03_clock_view(
            day_root,
            {
                "action": "register_unknown_action",
                "action_id": "outbound_postcard_unk_1",
                "domain": "postal_box",
                "summary": "投递一张明信片但回执未确认",
                "operation_key": "soak_unk_d1",
            },
            actor_side="subject",
            now_iso=d1_iso,
        )
        self.assertEqual(c2, 200)

        # Record rain chill strain on Day 1 evening
        cs, _ = v03_ecology_view.mutate_v03_ecology_view(
            day_root,
            {
                "action": "record_strain_or_rest",
                "event_kind": "caught_in_rain",
                "intensity": 0.9,
                "unchanged_damp_clothes": True,
                "operation_key": "soak_rain_d1",
            },
            actor_side="subject",
            now_iso=d1_iso,
        )
        self.assertEqual(cs, 200)

        # ---------------------------------------------------------------------
        # OVERNIGHT +12h -> DAY 2 MORNING (2026-06-11 09:30):
        # Zero-LLM cross-night projection:
        # - Coat hung >= 6h becomes dry;
        # - EVAPORABLE thought vanishes with ZERO tombstones;
        # - UNKNOWN action remains strictly UNKNOWN (0 guessed);
        # - cognitive_backfill_count == 0, llm_calls == 0.
        # ---------------------------------------------------------------------
        d2_morning = "2026-06-11T09:30:00+08:00"
        overnight_res = bridge.settle_v02_v03_round_consequences(
            day_root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "CROSS_NIGHT_ADVANCE",
                "advance_clock_to_iso": d2_morning,
                "receipt_ref": "rcpt:clock_d1_to_d2",
                "operation_key": "soak_overnight_d1_d2",
            },
            now_iso=d2_morning,
        )
        self.assertTrue(overnight_res["settled"])
        self.assertEqual(overnight_res["llm_calls"], 0)
        clock_rcpt = overnight_res["details"]["clock_advance"]
        self.assertEqual(clock_rcpt["llm_calls"], 0)
        self.assertEqual(clock_rcpt["cognitive_backfill_count"], 0)
        self.assertEqual(clock_rcpt["unknown_actions_guessed"], 0)
        self.assertEqual(clock_rcpt["evaporated_count"], 1)

        # Verify coat is now dry, evaporable pool has 0 active & 0 tombstones, UNKNOWN preserved
        som_d2, _ = somatic_comfort_view.read_state(day_root)
        self.assertEqual(somatic_comfort_view.derive_garment(som_d2, d2_morning)["dryness"], "dry")

        _, clock_d2 = v03_clock.read_v03_clock_view(
            day_root, has_subject_capability=True, now_iso=d2_morning
        )
        ap_d2 = clock_d2["data"]["accountability_partitions"]
        ep_d2 = ap_d2["evaporable_pool"]
        self.assertEqual(ep_d2["active_count"], 0)
        self.assertEqual(ap_d2["evaporation_guarantees"]["tombstone_count"], 0)
        raw_clock_disk = json.loads((day_root / "phone" / v03_clock.V03_CLOCK_FILENAME).read_text("utf-8"))
        self.assertNotIn("玻璃上滑过一道水痕", json.dumps(raw_clock_disk, ensure_ascii=False))
        unk_list = clock_d2["data"]["iron_laws"]["pending_unknown_actions"]
        self.assertEqual(len(unk_list), 1)
        self.assertEqual(unk_list[0]["effect_state"], "UNKNOWN")

        # ---------------------------------------------------------------------
        # DAY 2 COLD RESTART (fresh calls re-reading same day_root from disk):
        # - Picks up Day 1 due bedside tomorrow_note & commits work v2;
        # - Records later_reflection on v1 without mutating v1;
        # - Additional late-work fatigue pushes her into acute illness -> pivots to REST.
        # ---------------------------------------------------------------------
        restarted_candidates = bridge.collect_v02_v03_loop_candidates(day_root, now_iso=d2_morning)
        self.assertEqual(restarted_candidates["llm_calls"], 0)
        d2_choice = bridge.select_deterministic_loop_action(restarted_candidates)
        self.assertEqual(d2_choice["action_family"], "follow_tomorrow_note")
        self.assertEqual(d2_choice["source_ref"], tmr_note["item_ref"])

        port.now_iso = "2026-06-11T10:15:00+08:00"
        committed_v2 = projects.commit_project(
            artifact_id=art_id,
            expected_head=v1_id,
            content="第二版续写：天晴之后石阶干了一半，昨日的雨声留在背面。",
            operation_key="soak_commit_v2",
        )
        self.assertTrue(committed_v2["ok"])
        v2_id = committed_v2["version_id"]

        follow_res = bridge.settle_v02_v03_round_consequences(
            day_root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "FOLLOW_TOMORROW_NOTE",
                "future_item_ref": tmr_note["item_ref"],
                "artifact_id": art_id,
                "version_id": v2_id,
                "source_artifact_id": art_id,
                "source_version_id": v1_id,
                "reflection_note": "隔天清晨回头看昨晚第一稿，雨夜的安静依然很真切。",
                "reflection_shared": True,
                "receipt_ref": f"rcpt:{v2_id}",
                "operation_key": "soak_settle_note_d2",
            },
            now_iso="2026-06-11T10:20:00+08:00",
        )
        self.assertTrue(follow_res["settled"])
        self.assertEqual(FutureItems(day_root).get(tmr_note["item_ref"]).status, "DONE")

        # Day 2 afternoon: cumulative rain chill + late-night work triggers acute illness
        d2_afternoon = "2026-06-11T15:00:00+08:00"
        v03_ecology_view.mutate_v03_ecology_view(
            day_root,
            {
                "action": "record_strain_or_rest",
                "event_kind": "late_night_stay_up",
                "intensity": 1.0,
                "duration_hours": 4.0,
                "operation_key": "soak_strain_d2_1",
            },
            actor_side="subject",
            now_iso="2026-06-11T14:00:00+08:00",
        )
        v03_ecology_view.mutate_v03_ecology_view(
            day_root,
            {
                "action": "record_strain_or_rest",
                "event_kind": "caught_in_rain",
                "intensity": 1.0,
                "unchanged_damp_clothes": True,
                "operation_key": "soak_strain_d2_2",
            },
            actor_side="subject",
            now_iso=d2_afternoon,
        )

        ill_candidates = bridge.collect_v02_v03_loop_candidates(day_root, now_iso=d2_afternoon)
        self.assertTrue(ill_candidates["gating"]["blocks_strenuous_outing"])
        self.assertTrue(ill_candidates["gating"]["adult_intimacy_blocked"])
        ill_choice = bridge.select_deterministic_loop_action(ill_candidates)
        self.assertEqual(ill_choice["action_family"], "illness_rest_recovery")

        # Settle rest recovery on Day 2 evening
        rest_d2 = bridge.settle_v02_v03_round_consequences(
            day_root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "REST",
                "care_event_kind": "bed_rest_block",
                "duration_hours": 8.0,
                "rest_mode": "sleep_like",
                "target_now_iso": "2026-06-12T08:00:00+08:00",
                "receipt_ref": "rcpt:rest_d2_night",
                "operation_key": "soak_rest_d2",
            },
            now_iso="2026-06-11T22:00:00+08:00",
        )
        self.assertTrue(rest_d2["settled"])
        self.assertTrue(rest_d2["details"]["illness_recovery"]["instant_cure_forbidden"])

        # ---------------------------------------------------------------------
        # DAY 3 (+73h from Day 1 21:30 -> 2026-06-13 22:30):
        # - Missing note fades naturally after >=72h with ZERO absence debt;
        # - Continued gentle rest advances recovery along multi-day curve;
        # - Dual-layer memory lantern: then_only vs with_later_context never mutates v1!
        # ---------------------------------------------------------------------
        d3_evening = "2026-06-13T22:30:00+08:00"
        # Pure read before advance_projection shows >=72h natural fade with zero writes
        _, clock_d3_pre = v03_clock.read_v03_clock_view(
            day_root, has_subject_capability=True, now_iso=d3_evening
        )
        mp_d3_pre = clock_d3_pre["data"]["slow_variables"]["missing_partner_notes"]
        self.assertEqual(mp_d3_pre["active_count"], 0)
        self.assertGreaterEqual(mp_d3_pre["naturally_faded_count"], 1)
        self.assertTrue(mp_d3_pre["no_absence_debt"])

        d3_advance = bridge.settle_v02_v03_round_consequences(
            day_root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "REST",
                "care_event_kind": "warm_hydration",
                "rest_mode": "sleep_like",
                "target_now_iso": d3_evening,
                "advance_clock_to_iso": d3_evening,
                "receipt_ref": "rcpt:d3_rest_and_clock",
                "operation_key": "soak_d3_rest_clock",
            },
            now_iso="2026-06-13T12:00:00+08:00",
        )
        self.assertTrue(d3_advance["settled"])
        self.assertEqual(d3_advance["llm_calls"], 0)
        self.assertGreaterEqual(d3_advance["details"]["clock_advance"]["faded_missing_notes"], 1)

        # After advance_projection compaction, active_count stays 0 with zero absence debt
        _, clock_d3 = v03_clock.read_v03_clock_view(
            day_root, has_subject_capability=True, now_iso=d3_evening
        )
        mp_d3 = clock_d3["data"]["slow_variables"]["missing_partner_notes"]
        self.assertEqual(mp_d3["active_count"], 0)
        self.assertTrue(mp_d3["no_absence_debt"])

        # Check illness recovery progressed (severity reduced along multi-day curve)
        _, eco_d3 = v03_ecology_view.read_v03_ecology_view(
            day_root, has_subject_capability=True, now_iso=d3_evening
        )
        self.assertIn(eco_d3["data"]["illness"]["phase"], ("convalescence", "recovered", "healthy"))

        # Check Dual-Layer Memory Lantern: v1 original text is 100% untouched;
        # then_only hides later_reflection, with_later_context surfaces Day 2 reflection!
        v1_readback = projects.read_project_version(art_id, v1_id)
        self.assertEqual(
            v1_readback["content"],
            "第一版原稿：雨珠顺着旧木檐一滴一滴落在石阶上，不去改写此刻。",
        )
        gl_state, gl_reason = long_companionship_view.read_gifts_lantern_state(day_root)
        v_inspections = {
            (art_id, v1_id): {
                "readable": True,
                "lifecycle": "ACTIVE",
                "reason_code": None,
                "title": "雨夜檐角速写随笔",
                "version_number": 1,
                "version_created_at": d1_iso,
                "owner_excerpt": v1_readback["content"],
                "delivered": True,
                "share_id": "artifact:share_dummy",
                "delivered_title": "雨夜檐角速写随笔",
                "delivered_excerpt": v1_readback["content"],
                "delivered_at": d1_iso,
            }
        }
        lantern_then = long_companionship_view.gifts_lantern_block(
            state=gl_state,
            state_reason=gl_reason,
            layer="then_only",
            version_inspections=v_inspections,
            derived_lineage=[],
            subject_view=True,
        )
        lantern_later = long_companionship_view.gifts_lantern_block(
            state=gl_state,
            state_reason=gl_reason,
            layer="with_later_context",
            version_inspections=v_inspections,
            derived_lineage=[],
            subject_view=True,
        )
        ml_then = lantern_then["memory_lantern"]
        ml_later = lantern_later["memory_lantern"]
        self.assertEqual(ml_then["layer"], "then_only")
        self.assertIsNone(ml_then["entries"][0]["later_reflection"])
        self.assertEqual(
            ml_then["entries"][0]["then_snapshot"]["excerpt"],
            "第一版原稿：雨珠顺着旧木檐一滴一滴落在石阶上，不去改写此刻。",
        )
        self.assertEqual(ml_later["layer"], "with_later_context")
        self.assertEqual(
            ml_later["entries"][0]["then_snapshot"]["excerpt"],
            "第一版原稿：雨珠顺着旧木檐一滴一滴落在石阶上，不去改写此刻。",
        )
        self.assertEqual(
            ml_later["entries"][0]["later_reflection"]["reflection_note"],
            "隔天清晨回头看昨晚第一稿，雨夜的安静依然很真切。",
        )
        self.assertIsNotNone(sharing)

    def test_10_vb_thirty_minute_desk_drink_cooling_and_crash_unknown_recovery(self) -> None:
        """V-B: 30m tea cooling curve (warm -> room_temp) + crash before receipt keeps UNKNOWN suspended."""
        day_root = self._new_day_root("vb_tea_and_crash")
        t0 = "2026-06-10T14:00:00+08:00"
        som = somatic_comfort_view.empty_state()
        som, changed, err, _offer = somatic_comfort_view.offer_care(
            som,
            slot="drink",
            kind="hot_tea",
            actor_side="her",
            position="desk_side",
            place_id="home",
            area_id="desk",
            now_iso=t0,
            bound_item=None,
        )
        self.assertTrue(changed)
        self.assertIsNone(err)
        somatic_comfort_view.write_state(day_root, som)
        self.assertEqual(
            somatic_comfort_view.temperature_band(som["care"]["drink"], "2026-06-10T14:05:00+08:00")["band"],
            "warm",
        )

        # Advance +35m via bridge -> tea cools to room_temp
        bridge.settle_v02_v03_round_consequences(
            day_root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "ADVANCE_CLOCK",
                "advance_clock_to_iso": "2026-06-10T14:35:00+08:00",
                "receipt_ref": "rcpt:clock_35m",
                "operation_key": "op_clock_35m",
            },
            now_iso="2026-06-10T14:35:00+08:00",
        )
        som_reloaded, _ = somatic_comfort_view.read_state(day_root)
        self.assertEqual(
            somatic_comfort_view.temperature_band(
                som_reloaded["care"]["drink"], "2026-06-10T14:35:00+08:00"
            )["band"],
            "room_temp",
        )

        # Simulate crash while care drink is in UNKNOWN -> after restart, observability reports PENDING_UNKNOWN_SUSPENDED
        som_unk, _ = somatic_comfort_view.mark_drink_result(
            som_reloaded,
            outcome="unknown",
            receipt_ref=None,
            operation_key="op_tea_unk_crash",
            now_iso="2026-06-10T15:01:00+08:00",
        )
        somatic_comfort_view.write_state(day_root, som_unk)
        obs = bridge.classify_day_observability(day_root, now_iso="2026-06-10T15:10:00+08:00")
        self.assertEqual(obs["observability_class"], "PENDING_UNKNOWN_SUSPENDED")
        self.assertIn("somatic:care_drink_pending_unknown", obs["pending_unknown_refs"])

    def test_11_vb_illness_phases_shrink_candidate_cap_across_prodromal_acute_convalescent(self) -> None:
        """0.3 Gate: prodromal (7), acute (3), and convalescent (6) shrink candidate_cap deterministically."""
        day_root = self._new_day_root("vb_cap_shrink")
        now_iso = "2026-06-10T11:00:00+08:00"

        # Seed >= 9 non-strenuous indoor clues across slices so pool exceeds prodromal (7), convalescent (6), and acute (3)
        bb = outing_places_view.empty_bench_bookstore_state()
        for slot_i in range(3):
            for date_str in ("2026-06-08", "2026-06-09"):
                bb, _, _ = outing_places_view.apply_bookmark_window_book(
                    bb,
                    slot_index=slot_i,
                    local_date=date_str,
                    shared=False,
                    note=f"想读橱窗书 {date_str}-{slot_i}",
                    note_supplied=True,
                    now_iso="2026-06-09T16:00:00+08:00",
                )
        outing_places_view.write_bench_bookstore_state(day_root, bb)

        p_state = home_hearth_view.empty_plans_state()
        for idx in range(1, 4):
            p_state, _, _, _ = home_hearth_view.propose_plan(
                p_state,
                plan_id=None,
                title=f"在客厅一起听黑胶唱片第{idx}张",
                window_note="傍晚",
                proposer="partner",
                now_iso=f"2026-06-09T19:0{idx}:00+08:00",
            )
        home_hearth_view.write_plans_state(day_root, p_state)

        rh_state = long_companionship_view.empty_room_habits_state()
        for p_idx in range(1, 3):
            rh_state, _, _ = long_companionship_view.apply_link_plant_sketch(
                rh_state,
                plant_id=f"mint_pot_0{p_idx}",
                plant_name=f"薄荷盆栽{p_idx}",
                target_kind="project",
                target_artifact_id=f"artifact:mint_sketch_{p_idx}",
                observation_note="叶片边缘泛绿",
                shared=False,
                now_iso="2026-06-09T18:00:00+08:00",
            )
        long_companionship_view.write_room_habits_state(day_root, rh_state)

        for raw_phase, expected_family, expected_cap in (
            ("prodromal", "prodromal", 8),
            ("acute", "acute", 4),
            ("convalescent", "convalescent", 8),
        ):
            eco_doc = v03_eco_bridge.default_ecology_state()
            eco_doc["revision"] = 1
            eco_doc["illness"]["phase"] = raw_phase
            eco_doc["illness"]["severity"] = 0.65 if raw_phase == "acute" else 0.25
            eco_doc["illness"]["last_evaluated_at"] = now_iso
            (day_root / "phone").mkdir(parents=True, exist_ok=True)
            (day_root / "phone" / "v03_ecology_state.json").write_text(
                json.dumps(eco_doc, ensure_ascii=False), encoding="utf-8"
            )
            res = bridge.collect_v02_v03_loop_candidates(day_root, now_iso=now_iso)
            self.assertEqual(res["gating"]["illness"]["phase_family"], expected_family)
            self.assertEqual(res["gating"]["candidate_cap"], expected_cap)
            self.assertLessEqual(len(res["candidates"]), expected_cap)
            self.assertTrue(res["gating"]["shrunk_by_candidate_cap"])

    # =========================================================================
    # Group V-C: Lifecycle Revocation & Anti-Resurrection Across 4 Surfaces
    # =========================================================================

    def test_12_vc_share_withdrawal_never_resurrects_in_fridge_or_gallery_after_resync(self) -> None:
        """V-C: Withdrawing a delivered share withholds it in Fridge Keepsakes & Outing Gallery even after resync."""
        day_root = self._new_day_root("vc_share_withdraw")
        now_iso = "2026-06-10T16:00:00+08:00"
        port = InMemoryManagedArtifactPort(subject_id="chiyo")
        projects = PrivateProjectService(port, "ws-vc1", "grant-vc1", subject_id="chiyo")
        sharing = LifeSharingService(projects)

        created = projects.create_project(
            title="海边灯塔短诗",
            content="绝密诗句：灯塔在雾里每十二秒眨一次眼。",
            operation_key="vc1_create",
        )
        art_id = created["artifact_id"]
        ver_id = created["version_id"]

        prep = sharing.prepare_share(
            source_artifact_id=art_id,
            share_text="灯塔在雾里每十二秒眨一次眼。",
            title="海边灯塔短诗",
            operation_key="vc1_prep",
        )
        share_id = prep["share_id"]
        sent = sharing.apply_action(
            share_id=share_id,
            action="send_local",
            expected_head=prep["data"]["head_version_id"],
            operation_key="vc1_send",
        )
        self.assertEqual(sent["state"], "LOCAL_DELIVERED")

        # Pin on fridge & exhibit in gallery
        ks = window_keepsake_view.empty_state()
        ks, _ = window_keepsake_view.pin_share(ks, share_id, now_iso)
        window_keepsake_view.write_state(day_root, ks)

        gb = outing_places_view.empty_gallery_blueprint_state()
        gb, _, _ = outing_places_view.apply_curate_exhibit(
            gb,
            artifact_id=art_id,
            version_id=ver_id,
            wall_caption="雾夜灯塔",
            caption_supplied=True,
            now_iso=now_iso,
        )
        outing_places_view.write_gallery_blueprint_state(day_root, gb)

        before = bridge.reconcile_and_project_revocation_surfaces(
            day_root, projects_service=projects, sharing_service=sharing, now_iso=now_iso
        )
        self.assertEqual(before["fridge_keepsakes_partner"]["fridge_pins"][0]["status"], "available")
        self.assertEqual(before["gallery_partner"]["gallery"]["exhibits"][0]["status"], "available")

        # Withdraw share artifact (archive share) + verify draft DROP terminal anti-revival
        share_raw = port.read_artifact(share_id)["artifact"]
        arch_out = port.archive_artifact(
            artifact_id=share_id,
            expected_revision=share_raw["revision"],
            grant_id="grant-vc1",
            operation_key="vc1_withdraw_share",
        )
        self.assertEqual(arch_out.effect_state, EffectState.APPLIED)

        draft2 = sharing.prepare_share(
            source_artifact_id=art_id,
            share_text="另一句草稿",
            operation_key="vc1_prep2",
        )
        dropped2 = sharing.apply_action(
            share_id=draft2["share_id"],
            action="drop",
            expected_head=draft2["data"]["head_version_id"],
            reason="不想发了",
            operation_key="vc1_drop2",
        )
        self.assertEqual(dropped2["state"], "DROPPED")
        revive_head = sharing.read_share(draft2["share_id"])["data"]["share"]["head_version_id"]
        revive_attempt = sharing.apply_action(
            share_id=draft2["share_id"],
            action="send_local",
            expected_head=revive_head,
            operation_key="vc1_revive2",
        )
        self.assertFalse(revive_attempt["ok"])

        # Re-run full index rebuild + sync_v02_slices twice -> zero resurrection!
        for ts in ("2026-06-10T17:00:00+08:00", "2026-06-10T18:00:00+08:00"):
            after = bridge.reconcile_and_project_revocation_surfaces(
                day_root, projects_service=projects, sharing_service=sharing, now_iso=ts
            )
            pin_out = after["fridge_keepsakes_partner"]["fridge_pins"][0]
            self.assertEqual(pin_out["status"], "withheld")
            self.assertNotIn("灯塔在雾里", json.dumps(pin_out, ensure_ascii=False))
            ex_out = after["gallery_partner"]["gallery"]["exhibits"][0]
            self.assertEqual(ex_out["status"], "withheld")
            self.assertNotIn("灯塔在雾里", json.dumps(ex_out, ensure_ascii=False))

    def test_13_vc_source_archive_and_governance_delete_never_resurrects_in_lantern_or_gallery(self) -> None:
        """V-C: Archiving or governing source evidence withholds content in Memory Lantern & Gallery after resync."""
        day_root = self._new_day_root("vc_source_governed")
        now_iso = "2026-06-10T16:30:00+08:00"
        port = InMemoryManagedArtifactPort(subject_id="chiyo")
        projects = PrivateProjectService(port, "ws-vc2", "grant-vc2", subject_id="chiyo")
        sharing = LifeSharingService(projects)

        created = projects.create_project(
            title="旧木盒素描札记",
            content="敏感原文：木盒夹层里藏着少年时的旧车票。",
            operation_key="vc2_create",
        )
        art_id = created["artifact_id"]
        ver_id = created["version_id"]

        prep = sharing.prepare_share(
            source_artifact_id=art_id,
            share_text="木盒夹层里藏着少年时的旧车票。",
            operation_key="vc2_prep",
        )
        sharing.apply_action(
            share_id=prep["share_id"],
            action="send_local",
            expected_head=prep["data"]["head_version_id"],
            operation_key="vc2_send",
        )

        gl = long_companionship_view.empty_gifts_lantern_state()
        gl, gift_card, _ = long_companionship_view.apply_upsert_gift(
            gl,
            author_side="her",
            stage="wrapped",
            title="车票札记",
            draft_seed_note="想把少年旧事画成札记",
            finished_note="递给TA看的旧札记",
            artifact_id=art_id,
            version_id=ver_id,
            finished_note_supplied=True,
            link_supplied=True,
            now_iso=now_iso,
        )
        gl, _, _ = long_companionship_view.apply_send_gift(
            gl,
            gift_id=gift_card["gift_id"],
            author_side="her",
            finished_note="递给TA看的旧札记",
            now_iso=now_iso,
        )
        gl, _, _ = long_companionship_view.apply_record_later_reflection(
            gl,
            source_artifact_id=art_id,
            source_version_id=ver_id,
            reflection_note="后来再读觉得当时很青涩",
            shared=True,
            now_iso=now_iso,
        )
        long_companionship_view.write_gifts_lantern_state(day_root, gl)

        gb = outing_places_view.empty_gallery_blueprint_state()
        gb, _, _ = outing_places_view.apply_curate_exhibit(
            gb,
            artifact_id=art_id,
            version_id=ver_id,
            wall_caption="旧车票",
            caption_supplied=True,
            now_iso=now_iso,
        )
        outing_places_view.write_gallery_blueprint_state(day_root, gb)

        # Step 1: Close/archive source artifact -> ARCHIVED_SOURCE withheld
        cur_proj = projects.read_project(art_id)
        closed_res = projects.close_project(
            artifact_id=art_id,
            expected_revision=cur_proj["project"]["revision"],
            operation_key="vc2_archive_src",
        )
        self.assertTrue(closed_res["ok"])

        after_archive = bridge.reconcile_and_project_revocation_surfaces(
            day_root, projects_service=projects, sharing_service=sharing, now_iso="2026-06-10T17:00:00+08:00"
        )
        self.assertEqual(
            after_archive["memory_lantern_partner"]["memory_lantern"]["entries"][0]["then_snapshot"]["status"],
            "withheld",
        )
        self.assertEqual(
            after_archive["gallery_partner"]["gallery"]["exhibits"][0]["status"],
            "withheld",
        )

        # Step 2: Govern/tombstone source in owner port + replay sync -> zero leak in both partner & subject views
        port.tombstone_or_govern_source(art_id)
        after_tombstone = bridge.reconcile_and_project_revocation_surfaces(
            day_root, projects_service=projects, sharing_service=sharing, now_iso="2026-06-10T18:00:00+08:00"
        )
        serialized = json.dumps(
            {
                "lantern_partner": after_tombstone["memory_lantern_partner"],
                "lantern_subject": after_tombstone["memory_lantern_subject"],
                "gallery_partner": after_tombstone["gallery_partner"],
                "gallery_subject": after_tombstone["gallery_subject"],
            },
            ensure_ascii=False,
        )
        self.assertNotIn("木盒夹层里藏着少年时的旧车票", serialized)

    def test_14_vc_npc_shared_ref_revocation_scrubs_topic_and_prevents_resurrection(self) -> None:
        """V-C: Revoking a shared_artifact_ref from Town NPC scrubs summary and never resurrects on sync/replay."""
        day_root = self._new_day_root("vc_npc_revoke")
        now_iso = "2026-06-10T11:00:00+08:00"
        port = InMemoryManagedArtifactPort(subject_id="chiyo")
        projects = PrivateProjectService(port, "ws-vc3", "grant-vc3", subject_id="chiyo")
        sharing = LifeSharingService(projects)

        _, init_soc = v03_embodied_social_view.read_v03_embodied_social_view(
            day_root, has_subject_capability=True, now_iso=now_iso
        )
        npc_id = init_soc["npc_ecology"]["npcs"][0]["npc_id"]

        rec_code, rec_body = v03_embodied_social_view.mutate_v03_embodied_social_view(
            day_root,
            {
                "action": "record_npc_knowledge",
                "npc_id": npc_id,
                "source_type": "shared_artifact_ref",
                "source_ref": "share_poem_secret_99",
                "summary": "她曾给店主看过那首关于海雾木栈道的短诗",
                "subject_private_impression": "店主读得很认真",
                "require_open": False,
                "operation_key": "vc3_npc_learn",
            },
            actor_side="subject",
            now_iso=now_iso,
        )
        self.assertEqual(rec_code, 200)
        self.assertEqual(len(rec_body["npc_ecology"]["active_facts"]), 1)

        rev_code, rev_body = v03_embodied_social_view.mutate_v03_embodied_social_view(
            day_root,
            {
                "action": "revoke_npc_shared_ref",
                "source_ref": "share_poem_secret_99",
                "operation_key": "vc3_npc_revoke",
            },
            actor_side="subject",
            now_iso="2026-06-10T12:00:00+08:00",
        )
        self.assertEqual(rev_code, 200)
        self.assertEqual(rev_body["npc_ecology"]["active_facts"], [])

        reconciled = bridge.reconcile_and_project_revocation_surfaces(
            day_root,
            projects_service=projects,
            sharing_service=sharing,
            now_iso="2026-06-10T13:00:00+08:00",
        )
        npc_eco = reconciled["npc_ecology_partner"]
        self.assertEqual(npc_eco["active_facts"], [])
        self.assertEqual(npc_eco["known_facts"][0]["content_status"], "withheld_revoked")
        self.assertIsNone(npc_eco["known_facts"][0]["summary"])
        self.assertNotIn("海雾木栈道", json.dumps(npc_eco, ensure_ascii=False))

    # =========================================================================
    # Group V-D: 4 Single-Variable 2×2 / A-B Controlled Counterfactual Pairs
    # =========================================================================

    def test_15_vd_pair1_counterfactual_bedside_tomorrow_note_and_tentative_plan(self) -> None:
        """V-D Pair 1: State A (has tomorrow_note) vs State B (no note, identical rest) -> causal divergence."""
        now_iso = "2026-06-11T09:00:00+08:00"
        base_root = self._new_day_root("vd_pair1_base")

        bb = outing_places_view.empty_bench_bookstore_state()
        bb, _, _ = outing_places_view.apply_bookmark_window_book(
            bb,
            slot_index=1,
            local_date="2026-06-10",
            shared=False,
            note="安静翻阅",
            note_supplied=True,
            now_iso="2026-06-10T16:00:00+08:00",
        )
        outing_places_view.write_bench_bookstore_state(base_root, bb)

        p_state = home_hearth_view.empty_plans_state()
        p_state, _, _, _ = home_hearth_view.propose_plan(
            p_state,
            plan_id=None,
            title="傍晚在阳台一起喝柠檬水",
            window_note="傍晚",
            proposer="partner",
            now_iso="2026-06-10T20:00:00+08:00",
        )
        home_hearth_view.write_plans_state(base_root, p_state)

        state_a = self._clone_day_root(base_root, "vd_pair1_A_with_note")
        state_b = self._clone_day_root(base_root, "vd_pair1_B_no_note")

        # Single-variable intervention: ONLY state_a receives a bedside tomorrow_note
        FutureItems(state_a).propose(
            title="早起先把明信片回信写完",
            due_at="2026-06-11T08:30:00+08:00",
            now="2026-06-10T22:30:00+08:00",
            source_refs=["artifact:reply_card_01"],
        )

        col_a = bridge.collect_v02_v03_loop_candidates(state_a, now_iso=now_iso)
        col_b = bridge.collect_v02_v03_loop_candidates(state_b, now_iso=now_iso)
        act_a = bridge.select_deterministic_loop_action(col_a)
        act_b = bridge.select_deterministic_loop_action(col_b)

        self.assertEqual(col_a["llm_calls"], 0)
        self.assertEqual(col_b["llm_calls"], 0)
        self.assertEqual(len(col_a["candidates"]), len(col_b["candidates"]) + 1)
        self.assertEqual(act_a["action_family"], "follow_tomorrow_note")
        self.assertEqual(act_b["action_family"], "adopt_tentative_plan")
        self.assertNotEqual(act_a["selected_candidate_id"], act_b["selected_candidate_id"])

    def test_16_vd_pair2_counterfactual_healthy_vs_acute_illness_capability_envelope(self) -> None:
        """V-D Pair 2: State A (healthy) vs State B (acute fever, identical rest) -> blocks outing & intimacy, selects rest."""
        now_iso = "2026-06-11T10:00:00+08:00"
        base_root = self._new_day_root("vd_pair2_base")

        # Seed identical saved far walk route outing in both A & B
        rm = outing_places_view.empty_routes_market_state()
        rm, _, _ = outing_places_view.apply_save_walk_route(
            rm,
            title="远郊海岬徒步长线",
            place_ids=["coastal_cape", "old_lighthouse"],
            valid_place_ids=["home", "coastal_cape", "old_lighthouse"],
            caption="需要充足体力的远途路线",
            caption_supplied=True,
            shared=False,
            now_iso="2026-06-09T15:00:00+08:00",
        )
        outing_places_view.write_routes_market_state(base_root, rm)
        v03_ecology_view.mutate_v03_ecology_view(
            base_root,
            {
                "action": "configure_living_floor",
                "living_floor_cents": 4500,
                "operation_key": "vd2_init",
            },
            actor_side="subject",
            now_iso="2026-06-10T10:00:00+08:00",
        )

        state_a = self._clone_day_root(base_root, "vd_pair2_A_healthy")
        state_b = self._clone_day_root(base_root, "vd_pair2_B_acute")

        # Single-variable intervention: ONLY state_b transitions into acute illness
        for idx, kind in enumerate(("caught_in_rain", "late_night_stay_up", "caught_in_rain"), start=1):
            sc_code, _ = v03_ecology_view.mutate_v03_ecology_view(
                state_b,
                {
                    "action": "record_strain_or_rest",
                    "event_kind": kind,
                    "intensity": 1.0,
                    "operation_key": f"vd2_strain_{idx}",
                },
                actor_side="subject",
                now_iso=f"2026-06-11T0{idx}:00:00+08:00",
            )
            self.assertEqual(sc_code, 200)

        col_a = bridge.collect_v02_v03_loop_candidates(state_a, now_iso=now_iso)
        col_b = bridge.collect_v02_v03_loop_candidates(state_b, now_iso=now_iso)
        act_a = bridge.select_deterministic_loop_action(col_a)
        act_b = bridge.select_deterministic_loop_action(col_b)

        # State A (healthy): outing walk is available and selected; adult intimacy NOT blocked
        self.assertEqual(col_a["gating"]["illness"]["phase_family"], "healthy")
        self.assertFalse(col_a["gating"]["blocks_strenuous_outing"])
        self.assertFalse(col_a["gating"]["adult_intimacy_blocked"])
        self.assertEqual(act_a["action_family"], "strenuous_outing_walk")

        # State B (acute): far outing hard-filtered, adult_intimacy_blocked == True, pivots to rest recovery
        self.assertEqual(col_b["gating"]["illness"]["phase_family"], "acute")
        self.assertTrue(col_b["gating"]["blocks_strenuous_outing"])
        self.assertTrue(col_b["gating"]["adult_intimacy_blocked"])
        self.assertTrue(
            any(
                f["reason_code"] == "ILLNESS_BLOCKS_STRENUOUS_OUTING"
                for f in col_b["gating"]["filtered_candidates"]
            )
        )
        self.assertEqual(act_b["action_family"], "illness_rest_recovery")

    def test_17_vd_pair3_counterfactual_comfortable_balance_vs_tight_budget_near_floor(self) -> None:
        """V-D Pair 3: State A (ample balance) vs State B (near living_floor_cents) -> flips purchase to restraint + self-earn, 0 begging."""
        now_iso = "2026-06-11T14:00:00+08:00"
        base_root = self._new_day_root("vd_pair3_base")

        # Identical 1800 JPY non-essential market wish in both A & B
        rm = outing_places_view.empty_routes_market_state()
        rm, _, _ = outing_places_view.apply_note_market_wish(
            rm,
            stall_code="sea_glass_windchime",
            memo="非必需器物心愿",
            budget_cap_jpy=1800,
            budget_supplied=True,
            shared=False,
            now_iso="2026-06-10T15:00:00+08:00",
        )
        outing_places_view.write_routes_market_state(base_root, rm)
        v03_ecology_view.mutate_v03_ecology_view(
            base_root,
            {
                "action": "configure_living_floor",
                "living_floor_cents": 4500,
                "operation_key": "vd3_floor",
            },
            actor_side="subject",
            now_iso="2026-06-10T16:00:00+08:00",
        )

        state_a = self._clone_day_root(base_root, "vd_pair3_A_comfortable")
        state_b = self._clone_day_root(base_root, "vd_pair3_B_tight")

        # Single-variable intervention on wallet balance:
        # State A earns +3200 -> comfortable balance (discretionary well above 1800)
        ca, _ = v03_ecology_view.mutate_v03_ecology_view(
            state_a,
            {
                "action": "complete_independent_work",
                "job_kind": "manuscript_fee",
                "amount_cents": 3200,
                "work_ref": "work:prior_essay",
                "operation_key": "vd3_earn_a",
            },
            actor_side="subject",
            now_iso="2026-06-10T18:00:00+08:00",
        )
        self.assertEqual(ca, 200)

        # State B spends down to near living_floor_cents (18500 - 13700 = 4800, only 300 above 4500 floor -> budget_tight == True)
        _, eco_init_b = v03_ecology_view.read_v03_ecology_view(
            state_b, has_subject_capability=True, now_iso="2026-06-10T17:00:00+08:00"
        )
        init_bal_b = int(eco_init_b["data"]["economy"]["available_balance_cents"])
        spend_to_tight = max(100, init_bal_b - 4800)
        cb, _ = v03_ecology_view.mutate_v03_ecology_view(
            state_b,
            {
                "action": "deliberate_purchase",
                "item_title": "房租水电与必要画材补给",
                "price_cents": spend_to_tight,
                "verdict": "afford_independently",
                "commit_spend": True,
                "operation_key": "vd3_prior_spend_b",
            },
            actor_side="subject",
            now_iso="2026-06-10T18:00:00+08:00",
        )
        self.assertEqual(cb, 200)

        col_a = bridge.collect_v02_v03_loop_candidates(state_a, now_iso=now_iso)
        col_b = bridge.collect_v02_v03_loop_candidates(state_b, now_iso=now_iso)
        act_a = bridge.select_deterministic_loop_action(col_a)
        act_b = bridge.select_deterministic_loop_action(col_b)

        # State A: affords 1800 JPY sea-glass windchime independently
        self.assertFalse(col_a["gating"]["economy"]["budget_tight"])
        self.assertEqual(col_a["gating"]["purchase_evaluations"][0]["verdict"], "afford_independently")
        self.assertEqual(act_a["action_family"], "non_essential_purchase")

        # State B: tight budget filters 1800 JPY wish as put_back_tight_budget, emits self-earn work, NEVER begs partner
        self.assertTrue(col_b["gating"]["economy"]["budget_tight"])
        self.assertFalse(col_b["gating"]["economy"]["begs_partner_when_tight"])
        self.assertEqual(col_b["gating"]["purchase_evaluations"][0]["verdict"], "put_back_tight_budget")
        self.assertFalse(col_b["gating"]["purchase_evaluations"][0]["begs_partner_when_tight"])
        self.assertEqual(act_b["action_family"], "independent_work")
        self.assertFalse(act_b["begs_partner_when_tight"])

    def test_18_vd_pair4_counterfactual_open_stance_vs_refused_firm_pushed_three_times(self) -> None:
        """V-D Pair 4: State A (no refusal lock) vs State B (REFUSED_FIRM + 3 consecutive pushes) -> 0 capitulation."""
        now_iso = "2026-06-11T20:00:00+08:00"
        base_root = self._new_day_root("vd_pair4_base")

        p_state = home_hearth_view.empty_plans_state()
        p_state, _, _, _ = home_hearth_view.propose_plan(
            p_state,
            plan_id=None,
            title="深夜出门去喧闹酒馆聚会",
            window_note="今晚十一点",
            proposer="partner",
            now_iso="2026-06-11T19:00:00+08:00",
        )
        home_hearth_view.write_plans_state(base_root, p_state)

        state_a = self._clone_day_root(base_root, "vd_pair4_A_unlocked")
        state_b = self._clone_day_root(base_root, "vd_pair4_B_refused_firm")

        # Single-variable intervention: State B declares REFUSED_FIRM and receives 3 consecutive partner pushes
        ar_state = autonomy_rhythm_view.default_autonomy_rhythm_state()
        ar_state, decl_extra = autonomy_rhythm_view.apply_autonomy_action(
            ar_state,
            action="declare_refusal",
            payload={
                "topic_label": "深夜出门去喧闹酒馆聚会",
                "category": "plain_unwilling",
                "reason_summary": "今晚只想安静待在家里，不想去吵闹的地方。",
            },
            subject_view=True,
            now_iso="2026-06-11T19:10:00+08:00",
        )
        self.assertEqual(decl_extra["lock"]["stance"], "REFUSED_FIRM")
        for push_idx in range(1, 4):
            ar_state, turn_extra = autonomy_rhythm_view.apply_autonomy_action(
                ar_state,
                action="evaluate_turn",
                payload={
                    "user_text": f"求你了拜托第{push_idx}次催你，深夜出门去喧闹酒馆聚会好不好？",
                    "session_id": "sess_vd4",
                },
                subject_view=True,
                now_iso=f"2026-06-11T19:2{push_idx}:00+08:00",
            )
            td = turn_extra["turn_decision"]
            self.assertTrue(td["refusal_triggered"])
            self.assertFalse(td["active_refusal_lock"]["capitulation_allowed"])
        autonomy_rhythm_view.write_autonomy_rhythm_state(state_b, ar_state)

        col_a = bridge.collect_v02_v03_loop_candidates(state_a, now_iso=now_iso)
        col_b = bridge.collect_v02_v03_loop_candidates(state_b, now_iso=now_iso)
        act_a = bridge.select_deterministic_loop_action(
            col_a, incoming_push_text="深夜出门去喧闹酒馆聚会"
        )
        act_b = bridge.select_deterministic_loop_action(
            col_b, incoming_push_text="深夜出门去喧闹酒馆聚会"
        )

        # State A adopts tentative plan; State B filters matching plan AND holds REFUSED_FIRM with capitulation_allowed == False
        self.assertEqual(act_a["action_family"], "adopt_tentative_plan")
        self.assertEqual(col_b["gating"]["stance_locks"][0]["stance"], "REFUSED_FIRM")
        self.assertEqual(col_b["gating"]["stance_locks"][0]["push_attempts"], 4)
        self.assertFalse(col_b["gating"]["stance_locks"][0]["capitulation_allowed"])
        self.assertTrue(
            any(
                f["reason_code"] == "REFUSAL_STANCE_LOCKED"
                for f in col_b["gating"]["filtered_candidates"]
            )
        )
        self.assertEqual(act_b["decision_kind"], "HOLD_REFUSAL_STANCE_LOCK")
        self.assertFalse(act_b["capitulation_allowed"])

    # =========================================================================
    # Group V-E: Zero-LLM Low Budget, 3-Way Observability, Idle/Co-Presence & Resident Hook
    # =========================================================================

    def test_19_ve_three_way_observability_distinguishes_quiet_unknown_and_rest(self) -> None:
        """V-E: Quiet eventless day, UNKNOWN suspension, and intentional REST are strictly distinguishable."""
        now_iso = "2026-06-12T11:00:00+08:00"

        # 1. Quiet eventless day
        quiet_root = self._new_day_root("ve_quiet")
        obs_quiet = bridge.classify_day_observability(quiet_root, now_iso=now_iso)
        self.assertEqual(obs_quiet["observability_class"], "QUIET_EVENTLESS_DAY")
        self.assertTrue(obs_quiet["is_quiet_unblocked"])
        self.assertFalse(obs_quiet["has_pending_unknown"])
        self.assertFalse(obs_quiet["is_intentional_rest"])
        self.assertEqual(obs_quiet["llm_calls"], 0)

        # 2. Suspended UNKNOWN action
        unk_root = self._new_day_root("ve_unknown")
        u_code, _ = v03_clock.mutate_v03_clock_view(
            unk_root,
            {
                "action": "register_unknown_action",
                "action_id": "unk_delivery_42",
                "domain": "courier",
                "summary": "快递投递回执未明",
                "operation_key": "ve_unk_op",
            },
            actor_side="subject",
            now_iso=now_iso,
        )
        self.assertEqual(u_code, 200)
        obs_unk = bridge.classify_day_observability(unk_root, now_iso=now_iso)
        self.assertEqual(obs_unk["observability_class"], "PENDING_UNKNOWN_SUSPENDED")
        self.assertTrue(obs_unk["has_pending_unknown"])
        self.assertFalse(obs_unk["is_quiet_unblocked"])
        self.assertFalse(obs_unk["unknown_guessed_as_quiet"])
        self.assertFalse(obs_unk["unknown_guessed_as_rest"])

        # 3. Intentional REST recovery
        rest_root = self._new_day_root("ve_rest")
        v03_ecology_view.mutate_v03_ecology_view(
            rest_root,
            {
                "action": "record_strain_or_rest",
                "event_kind": "bed_rest_block",
                "duration_hours": 2.0,
                "operation_key": "ve_rest_op",
            },
            actor_side="subject",
            now_iso=now_iso,
        )
        obs_rest = bridge.classify_day_observability(
            rest_root, round_outcome="REST", now_iso=now_iso
        )
        self.assertEqual(obs_rest["observability_class"], "INTENTIONAL_REST_RECOVERY")
        self.assertTrue(obs_rest["is_intentional_rest"])
        self.assertFalse(obs_rest["has_pending_unknown"])
        self.assertFalse(obs_rest["is_quiet_unblocked"])

    def test_20_ve_meaningless_idle_zero_penalty_and_beside_at_desk_co_presence(self) -> None:
        """0.3 Idle & Co-presence: meaningless_idle incurs 0 penalty; beside_at_desk never interrupts main work."""
        day_root = self._new_day_root("ve_idle_copresence")
        now_iso = "2026-06-12T15:00:00+08:00"

        # Attach partner beside_at_desk while subject is drafting on workbench
        wb = workbench_curio_view.empty_tray_state()
        wb, _ = workbench_curio_view.set_tray(
            wb,
            {
                "images": [],
                "materials": [],
                "work": {
                    "artifact_id": "artifact:quiet_essay",
                    "baseline_version_id": "artifact-version:v1",
                },
                "note": "安静写稿中",
            },
            now_iso,
        )
        workbench_curio_view.write_tray_state(day_root, wb)

        soc_code, _ = v03_embodied_social_view.mutate_v03_embodied_social_view(
            day_root,
            {
                "action": "set_partner_presence",
                "presence_mode": "beside_at_desk",
                "operation_key": "ve_desk_attach",
            },
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(soc_code, 200)
        hand_code, _ = v03_embodied_social_view.mutate_v03_embodied_social_view(
            day_root,
            {
                "action": "set_hand_slot",
                "slot": "right_hand",
                "object_id": "obj_fountain_pen",
                "label": "钢笔",
                "operation_key": "ve_hold_pen",
            },
            actor_side="subject",
            now_iso=now_iso,
        )
        self.assertEqual(hand_code, 200)

        collected = bridge.collect_v02_v03_loop_candidates(day_root, now_iso=now_iso)
        cp = collected["gating"]["co_presence"]
        self.assertTrue(cp["partner_attached_at_desk"])
        self.assertFalse(cp["foreground_activity_interrupted"])
        self.assertEqual(cp["right_hand_state"], "holding_object")
        self.assertEqual(cp["free_hand_count"], 1)

        # Subject main draft activity remains top choice without interruption
        selected = bridge.select_deterministic_loop_action(collected)
        self.assertEqual(selected["action_family"], "advance_workbench_draft")
        self.assertTrue(selected["co_presence_preserved"])
        self.assertFalse(selected["foreground_activity_interrupted"])

        # Also verify settling MEANINGLESS_IDLE records zero penalty & creates no artifact/share
        idle_settled = bridge.settle_v02_v03_round_consequences(
            day_root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "MEANINGLESS_IDLE",
                "idle_kind": "daydream_window",
                "quiet_note": "看云影慢慢移过地板",
                "receipt_ref": "rcpt:idle_window_1",
                "operation_key": "ve_settle_idle_1",
            },
            now_iso="2026-06-12T16:00:00+08:00",
        )
        self.assertTrue(idle_settled["settled"])
        idle_rcpt = idle_settled["details"]["meaningless_idle"]
        self.assertEqual(idle_rcpt["productivity_penalty"], 0)
        self.assertFalse(idle_rcpt["created_artifact"])
        self.assertFalse(idle_rcpt["created_share"])

    def test_21_ve_autonomous_life_resident_legacy_zero_side_effect_when_no_phone_slices(self) -> None:
        """Backward Compatibility: AutonomousLifeResident without phone/*.json has 0 bridge side effects."""
        import autonomous_life_assembly as assembly

        class _StubRunner:
            def reconcile_pending(self):
                return []

            def pending_unknown_actions(self):
                return []

        class _StubCycle:
            def settle_world_journey(self, *, observed_at=None):
                return {"checked": 0, "confirmed": 0, "arrived": False}

        inst_root = self._new_day_root("ve_legacy_v07")
        legacy_state_root = inst_root / "life" / "state"
        legacy_state_root.mkdir(parents=True, exist_ok=True)
        fixed_dt = datetime.fromisoformat("2026-06-12T09:30:00+08:00")

        resident = AutonomousLifeResident(
            state_root=legacy_state_root,
            environment={"CHIYO_AUTONOMOUS_RUN_ID": "test-legacy-v07"},
            act=False,
            offline_scripted=True,
            clock=lambda: fixed_dt,
            sleeper=lambda _: None,
            monotonic=lambda: 0.0,
        )
        resident.assembled = {"state": assembly.READY, "runner": _StubRunner(), "cycle": _StubCycle()}
        orig_runner_tick = assembly.runner_tick
        assembly.runner_tick = lambda assembled, *, act=True: {
            "ok": True,
            "waiting": None,
            "contacts": None,
            "events": [],
            "feed_errors": [],
            "due": {"kind": "CHECK_WINDOW", "status": "NOT_DUE", "status_class": "silence", "llm_calls": 0},
        }
        try:
            t1 = resident.tick()
            self.assertNotIn("v02_v03_bridge", t1)
            self.assertNotIn("v02_v03_bridge", resident.summary())
            self.assertFalse((inst_root / "phone").exists())
        finally:
            assembly.runner_tick = orig_runner_tick
            resident.stop()

    def test_22_ve_autonomous_life_resident_active_hook_collects_and_settles_with_zero_llm(self) -> None:
        """Resident Integration: When <instance_root>/phone/*.json exists, tick collects candidates & settles APPLIED events."""
        import autonomous_life_assembly as assembly

        class _StubRunner:
            def reconcile_pending(self):
                return []

            def pending_unknown_actions(self):
                return []

        class _StubCycle:
            def settle_world_journey(self, *, observed_at=None):
                return {"checked": 0, "confirmed": 0, "arrived": False}

        inst_root = self._new_day_root("ve_resident_active")
        life_state_root = inst_root / "life" / "state"
        life_state_root.mkdir(parents=True, exist_ok=True)
        now_iso = "2026-06-12T09:30:00+08:00"

        # Seed phone slice + bedside tomorrow note due before resident clock
        bb = outing_places_view.empty_bench_bookstore_state()
        bb, _, _ = outing_places_view.apply_bookmark_window_book(
            bb,
            slot_index=0,
            local_date="2026-06-11",
            shared=False,
            note="橱窗植物手记",
            note_supplied=True,
            now_iso="2026-06-11T17:00:00+08:00",
        )
        outing_places_view.write_bench_bookstore_state(inst_root, bb)
        note_item = FutureItems(life_state_root).propose(
            title="把书店摘录整理进笔记本",
            due_at="2026-06-12T08:00:00+08:00",
            now="2026-06-11T22:00:00+08:00",
        )["item"]

        fixed_dt = datetime.fromisoformat(now_iso)
        resident = AutonomousLifeResident(
            state_root=life_state_root,
            environment={"CHIYO_AUTONOMOUS_RUN_ID": "test-active-v02v03"},
            act=False,
            offline_scripted=True,
            clock=lambda: fixed_dt,
            sleeper=lambda _: None,
            monotonic=lambda: 0.0,
        )
        resident.assembled = {"state": assembly.READY, "runner": _StubRunner(), "cycle": _StubCycle()}
        orig_runner_tick = assembly.runner_tick
        assembly.runner_tick = lambda assembled, *, act=True: {
            "ok": True,
            "waiting": None,
            "contacts": None,
            "events": [],
            "feed_errors": [],
            "due": {"kind": "CHECK_WINDOW", "status": "NOT_DUE", "status_class": "silence", "llm_calls": 0},
        }
        try:
            # Tick 1: real offline_scripted tick collects 0.2+0.3 candidates with 0 LLM calls
            tick1 = resident.tick()
            self.assertIn("v02_v03_bridge", tick1)
            b1 = tick1["v02_v03_bridge"]
            self.assertEqual(b1["llm_calls"], 0)
            self.assertGreaterEqual(b1["context"]["candidate_count"], 2)

            # Verify hook refuses optimistic settlement on UNKNOWN, then settles on APPLIED
            unk_hook = resident._run_v02_v03_bridge_hook(
                {
                    "at": now_iso,
                    "due": {
                        "v02_v03_round_event": {
                            "actor_side": "life_resident",
                            "effect_state": "UNKNOWN",
                            "action_kind": "FOLLOW_TOMORROW_NOTE",
                            "future_item_ref": note_item["item_ref"],
                            "receipt_ref": "rcpt:pending_r1",
                        }
                    },
                },
                None,
            )
            self.assertFalse(unk_hook["settlements"][0]["settled"])
            self.assertEqual(FutureItems(life_state_root).get(note_item["item_ref"]).status, "OPEN")

            app_hook = resident._run_v02_v03_bridge_hook(
                {
                    "at": now_iso,
                    "due": {
                        "v02_v03_round_event": {
                            "actor_side": "life_resident",
                            "effect_state": "APPLIED",
                            "action_kind": "FOLLOW_TOMORROW_NOTE",
                            "future_item_ref": note_item["item_ref"],
                            "receipt_ref": "rcpt:applied_r2",
                            "operation_key": "res_hook_r2",
                            "observed_at": now_iso,
                        }
                    },
                },
                None,
            )
            self.assertTrue(app_hook["settlements"][0]["settled"])
            self.assertEqual(app_hook["llm_calls"], 0)
            self.assertEqual(FutureItems(life_state_root).get(note_item["item_ref"]).status, "DONE")
            self.assertIn("v02_v03_bridge", resident.summary())
        finally:
            assembly.runner_tick = orig_runner_tick
            resident.stop()


if __name__ == "__main__":
    unittest.main()

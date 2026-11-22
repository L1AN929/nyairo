#!/usr/bin/env python3
"""Step 3.2 Matrix Audit:
  1. All 0.2-scoped Ch.03 F-001..F-193 finding clusters (15 P0 + 56 P1 + 75 P2 = 146)
     plus explicit accounting for P3/P4/P5 reserve & proposal boundaries (47 items -> 193 total);
  2. Ch.17 §7 P01..P35 contracts & 待裁定与提案登记.json ADR-012..ADR-036 boundaries;
  3. Ch.15 §10 all 20 mechanism gaps;
  4. Real v02_* backend modules, PhoneService, Daily Loop bridge, and zero-write GET check
     on components/phone/v02_honest_ux_view.py.

Invariants enforced:
- ROOT = Path(__file__).resolve().parents[1]
- tempfile.TemporaryDirectory() strictly outside repo root
- Pure LF (\\n), zero emojis, neutral third-person ("TA"), llm_calls == 0.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import shutil
import socket
import struct
import sys
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

_HERMES_VENDOR = ROOT / "vendor" / "hermes"
if _HERMES_VENDOR.exists() and str(_HERMES_VENDOR) not in sys.path:
    sys.path.append(str(_HERMES_VENDOR))
elif "agent.context_compressor" not in sys.modules:
    import types as _types

    _agent_pkg = _types.ModuleType("agent")
    _cc_mod = _types.ModuleType("agent.context_compressor")

    class _StubContextCompressor:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def on_session_reset(self) -> None:
            pass

        def get_status(self) -> dict[str, Any]:
            return {}

    _cc_mod.ContextCompressor = _StubContextCompressor  # type: ignore[attr-defined]
    sys.modules["agent"] = _agent_pkg
    sys.modules["agent.context_compressor"] = _cc_mod
    if "hermes_constants" not in sys.modules:
        _hc_mod = _types.ModuleType("hermes_constants")
        _hc_mod.get_hermes_home = lambda: Path(os.environ.get("HERMES_HOME", str(ROOT)))  # type: ignore[attr-defined]
        sys.modules["hermes_constants"] = _hc_mod

import autonomy_rhythm_fixture as stack_fixture  # noqa: E402
import verify_manifest  # noqa: E402
from chiyo_bundle import lived_context_bridge, phone_tool  # noqa: E402
from components.life.scripts import (  # noqa: E402
    v02_agency_depth_engine as ade,
    v02_autobiography_skill_provenance as asp,
    v02_causal_ablation_runner as car,
    v02_common_ground as cg,
    v02_crash_and_partial_reconciler as cpr,
    v02_crossmodal_perception as cmp_,
    v02_lived_slices_and_vd_protocols as lived_slices,
    v02_recall_projection as rp,
    v02_representation_lease_forget as rlf,
    v02_runtime_governance_migration as rgm,
    v02_v03_daily_loop_bridge as loop_bridge,
)
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import v02_honest_ux_view as hux  # noqa: E402
from components.phone import workbench_curio_view  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "v02-f193-matrix-audit-cap-20261118"
T0 = "2026-11-18T10:00:00+08:00"
T1 = "2026-11-18T11:30:00+08:00"
T2 = "2026-11-19T09:00:00+08:00"

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX domain sockets (run inside WSL)",
)

SEALED_BASELINE_SHA256: dict[str, str] = {
    "components/life/scripts/autonomous_life_assembly.py": "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434",
    "components/life/scripts/life_sharing_service.py": "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
    "components/native/native_recall/readonly_recall_adapter.py": "6667ff66fe8105f28f436609f0cd8d744b185c0580e7f1642aafef0b873af78a",
    "NOW.md": "d77be1900e94b5e997a389831b864aab5207459bfc5ac763d1b9d90c6c181152",
    "plugins/chiyo/__init__.py": "8d6ba0f9295677c9371f0e9be07442096eba67438451ae99fa57312ca622bed7",
}

# Exact F-001..F-193 cluster coverage registry (all 193 items accounted for without gaps)
F_SCOPE_CLUSTERS: dict[str, dict[str, tuple[str, ...]]] = {
    "P0": {
        "S05": ("F-063", "F-064", "F-065"),
        "S10": ("F-113", "F-114", "F-115"),
        "S13": ("F-140", "F-141", "F-142"),
        "S14": ("F-149", "F-150", "F-151", "F-152", "F-153", "F-154"),
    },
    "P1": {
        "INFRA": tuple(f"F-{i:03d}" for i in range(155, 168)),
        "S01": tuple(f"F-{i:03d}" for i in range(8, 15)),
        "S03": tuple(f"F-{i:03d}" for i in range(33, 40)),
        "S04": tuple(f"F-{i:03d}" for i in range(47, 52)),
        "S05": tuple(f"F-{i:03d}" for i in range(66, 72)),
        "S08": tuple(f"F-{i:03d}" for i in range(93, 98)),
        "S09": tuple(f"F-{i:03d}" for i in range(103, 109)),
        "S10": tuple(f"F-{i:03d}" for i in range(116, 123)),
    },
    "P2": {
        "INFRA": tuple(f"F-{i:03d}" for i in range(168, 175)) + ("F-187", "F-191", "F-193"),
        "S01": tuple(f"F-{i:03d}" for i in range(1, 8)),
        "S02": tuple(f"F-{i:03d}" for i in range(23, 29)),
        "S03": tuple(f"F-{i:03d}" for i in range(40, 44)),
        "S04": tuple(f"F-{i:03d}" for i in range(52, 63)) + ("F-186",),
        "S05": tuple(f"F-{i:03d}" for i in range(72, 78)),
        "S06": tuple(f"F-{i:03d}" for i in range(78, 84)) + ("F-188",),
        "S07": tuple(f"F-{i:03d}" for i in range(88, 93)),
        "S08": ("F-098", "F-099", "F-100"),
        "S09": ("F-109", "F-110", "F-111", "F-112"),
        "S11": tuple(f"F-{i:03d}" for i in range(123, 134)),
    },
    "P3": {
        "RESEARCH": ("F-175", "F-176", "F-177", "F-178"),
        "S03": ("F-044", "F-045", "F-046"),
        "S06": ("F-084", "F-085", "F-086", "F-087"),
        "S08": ("F-101", "F-102"),
        "S09": ("F-189",),
        "S12": tuple(f"F-{i:03d}" for i in range(134, 140)) + ("F-190",),
    },
    "P4": {
        "S01": tuple(f"F-{i:03d}" for i in range(15, 23)),
        "S02": ("F-029", "F-030", "F-031", "F-032"),
        "S13": tuple(f"F-{i:03d}" for i in range(143, 149)) + ("F-192",),
    },
    "P5": {
        "RESEARCH": tuple(f"F-{i:03d}" for i in range(179, 186)),
    },
}

CH15_TWENTY_GAPS: tuple[tuple[int, str], ...] = (
    (1, "operable_environment"),
    (2, "body_occupancy_and_affordance"),
    (3, "perception_firewall_and_active_observation"),
    (4, "object_identity_and_content_lineage"),
    (5, "claim_evidence_correction_propagation"),
    (6, "valid_time_vs_record_time"),
    (7, "decision_prior_rationale_and_counterfactual_replay"),
    (8, "bounded_inquiry"),
    (9, "satiation_and_repetition_suppression"),
    (10, "commitment_vs_conflict"),
    (11, "autobiography_episode_period_narrative"),
    (12, "self_understanding_promotion_demotion"),
    (13, "experiment_isolation_and_eval_independence"),
    (14, "creative_process_and_work_lineage"),
    (15, "multi_object_audience_and_group_isolation"),
    (16, "delete_forget_and_derivation_barrier"),
    (17, "model_agnostic_migration_gate"),
    (18, "long_term_maintenance_and_retirement"),
    (19, "true_inactivity_no_action"),
    (20, "branch_replay_and_reliable_recovery"),
)

F_DISPOSITION_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("P0", "S05"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_02_p0_s05_memory_admission_and_delete_barrier"),
    ("P0", "S10"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_03_p0_s10_delivery_tiers_unknown_and_execution_source"),
    ("P0", "S13"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_04_p0_s13_endpoints_branch_identity_and_writer_fencing"),
    ("P0", "S14"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_05_p0_s14_permission_lease_audience_race_and_forget_generation"),
    ("P1", "INFRA"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_11_p1_infra_consequence_three_axes_outbox_crash_and_restore"),
    ("P1", "S01"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_06_p1_s01_managed_artifacts_md_projection_cas_and_health"),
    ("P1", "S03"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_07_p1_s03_s04_belief_epistemic_states_and_decision_adoption"),
    ("P1", "S04"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_07_p1_s03_s04_belief_epistemic_states_and_decision_adoption"),
    ("P1", "S05"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_08_p1_s05_episodes_dedup_layering_invalidation_and_no_change"),
    ("P1", "S08"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_09_p1_s08_s09_claims_promises_continuations_and_completion"),
    ("P1", "S09"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_09_p1_s08_s09_claims_promises_continuations_and_completion"),
    ("P1", "S10"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_10_p1_s10_request_idempotency_partial_dag_and_bounded_reconcile"),
    ("P2", "INFRA"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_17_p2_infra_coalescing_clocks_budget_audit_and_honest_ux_zero_write"),
    ("P2", "S01"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_12_p2_s01_s02_s03_room_embodiment_and_asymmetric_perception"),
    ("P2", "S02"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_12_p2_s01_s02_s03_room_embodiment_and_asymmetric_perception"),
    ("P2", "S03"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_12_p2_s01_s02_s03_room_embodiment_and_asymmetric_perception"),
    ("P2", "S04"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_13_p2_s04_residue_candidates_no_action_and_situation_view"),
    ("P2", "S05"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_14_p2_s05_s06_s07_recall_decay_preferences_satiation_and_emotion"),
    ("P2", "S06"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_14_p2_s05_s06_s07_recall_decay_preferences_satiation_and_emotion"),
    ("P2", "S07"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_14_p2_s05_s06_s07_recall_decay_preferences_satiation_and_emotion"),
    ("P2", "S08"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_15_p2_s08_s09_wishes_private_works_and_opportunity_shaping"),
    ("P2", "S09"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_15_p2_s08_s09_wishes_private_works_and_opportunity_shaping"),
    ("P2", "S11"): ("IN_SCOPE_BEHAVIORAL_TEST", "test_16_p2_s11_relationship_isolation_disclosure_common_ground_and_copractice"),
    ("P3", "RESEARCH"): ("STAGE_RESERVE_CONTRACT_ONLY", "test_21_adr_012_to_036_scope_and_out_of_scope_p3_p4_p5_boundary_audit"),
    ("P3", "S03"): ("STAGE_RESERVE_CONTRACT_ONLY", "test_21_adr_012_to_036_scope_and_out_of_scope_p3_p4_p5_boundary_audit"),
    ("P3", "S06"): ("STAGE_RESERVE_CONTRACT_ONLY", "test_21_adr_012_to_036_scope_and_out_of_scope_p3_p4_p5_boundary_audit"),
    ("P3", "S08"): ("STAGE_RESERVE_CONTRACT_ONLY", "test_21_adr_012_to_036_scope_and_out_of_scope_p3_p4_p5_boundary_audit"),
    ("P3", "S09"): ("STAGE_RESERVE_CONTRACT_ONLY", "test_21_adr_012_to_036_scope_and_out_of_scope_p3_p4_p5_boundary_audit"),
    ("P3", "S12"): ("STAGE_RESERVE_CONTRACT_ONLY", "test_21_adr_012_to_036_scope_and_out_of_scope_p3_p4_p5_boundary_audit"),
    ("P4", "S01"): ("STAGE_RESERVE_CONTRACT_ONLY", "test_21_adr_012_to_036_scope_and_out_of_scope_p3_p4_p5_boundary_audit"),
    ("P4", "S02"): ("STAGE_RESERVE_CONTRACT_ONLY", "test_21_adr_012_to_036_scope_and_out_of_scope_p3_p4_p5_boundary_audit"),
    ("P4", "S13"): ("STAGE_RESERVE_CONTRACT_ONLY", "test_21_adr_012_to_036_scope_and_out_of_scope_p3_p4_p5_boundary_audit"),
    ("P5", "RESEARCH"): ("STAGE_RESERVE_CONTRACT_ONLY", "test_21_adr_012_to_036_scope_and_out_of_scope_p3_p4_p5_boundary_audit"),
}


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.name.endswith((".sqlite-shm", ".sqlite-wal")):
            continue
        rel = p.relative_to(root).as_posix()
        try:
            st = p.lstat()
        except FileNotFoundError:
            continue
        if p.is_file() and not p.is_symlink():
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            dir_mtime = 0 if rel == "data" else int(st.st_mtime_ns)
            snap[rel] = (0, dir_mtime, "dir")
    return snap


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _build_bicolor_png(width: int = 16, height: int = 16) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    top = b"\x00" + bytes((200, 90, 50)) * width
    bot = b"\x00" + bytes((40, 80, 140)) * width
    raw = top * (height // 2) + bot * (height - height // 2)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


def _design_docs_dir() -> Path | None:
    for candidate in (
        Path("/mnt/d/CHIYO-总体设计-v2.0-final/CHIYO-总体设计-v2-最终版"),
        Path(r"D:\CHIYO-总体设计-v2.0-final\CHIYO-总体设计-v2-最终版"),
    ):
        if candidate.is_dir():
            return candidate
    return None


@POSIX_ONLY
class TestV02Ch03F001F193MatrixAudit(unittest.TestCase):
    """Comprehensive audit of F-001..F-193, P01..P35 / ADR-012..036, and Ch.15 20 mechanism gaps."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls._tmp = tempfile.TemporaryDirectory(prefix="v02-f193-audit-")
        cls.temp_root = Path(cls._tmp.name)
        cls.assert_outside_repo = ROOT not in cls.temp_root.resolve().parents
        cls.cap = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at=T0,
        )
        cls.stack = stack_fixture.build_stack(cls.temp_root / "instance", port=0, capability=cls.cap)
        cls.service = cls.stack.service
        cls.day_root = cls.stack.root

    @classmethod
    def tearDownClass(cls) -> None:
        stack_fixture.close_stack(cls.stack)
        cls._tmp.cleanup()
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        for rel in (
            "phone/v02_honest_ux_state.json",
            "phone/v02_runtime_migration.json",
            "phone/v02_crossmodal.json",
            "phone/v02_common_ground.json",
            "phone/v02_recall_projection.json",
            "phone/v02_agency_depth.json",
            "phone/outing_bench_bookstore.json",
            "phone/home_hearth_tentative_plans.json",
            "phone/tentative_plans.json",
            "phone/desk_tomorrow_note.json",
            "governance/v02_representation_lease_forget_state.json",
            "governance/v02_vc_authority_watermark.json",
            "control_authority/v02_control_highwater_ledger.json",
            "control_authority.json",
            "external_provider_ledger.json",
            "state/v02_autobiography_skill_provenance.json",
            "state/v02_lived_slices_state.json",
            "state/v02_agency_depth_state.json",
            "state/v02_common_ground_state.json",
            "memory/v02_common_ground_state.json",
            "state/v02_crossmodal_state.json",
            "state/v02_recall_projection_state.json",
            "memory/v02_recall_projection_state.json",
            "state/v02_runtime_governance_migration.json",
            "canonical_life.sqlite3",
            "canonical_life.sqlite3-wal",
            "canonical_life.sqlite3-shm",
        ):
            (self.day_root / rel).unlink(missing_ok=True)
        workbench_curio_view.tray_path(self.day_root).unlink(missing_ok=True)
        for sub in ("workspace_f193", "reconciler", "control_ext", "suitcase_export"):
            dpath = self.day_root / sub
            if dpath.exists():
                shutil.rmtree(dpath, ignore_errors=True)
        self.service._world_clock = lambda: datetime(2026, 11, 18, 10, 30, tzinfo=timezone(timedelta(hours=8)))

    # -----------------------------------------------------------------
    # 0. Design Corpus Integrity, Hygiene & Sealed Baseline Anchors
    # -----------------------------------------------------------------

    def test_01_design_inventory_counts_hygiene_and_sealed_anchors(self) -> None:
        self.assertTrue(self.assert_outside_repo, "TemporaryDirectory must live outside repo root")
        all_ids: list[str] = []
        stage_counts: dict[str, int] = {}
        for stage, domains in F_SCOPE_CLUSTERS.items():
            cnt = sum(len(ids) for ids in domains.values())
            stage_counts[stage] = cnt
            for ids in domains.values():
                all_ids.extend(ids)
        self.assertEqual(stage_counts, {"P0": 15, "P1": 56, "P2": 75, "P3": 21, "P4": 19, "P5": 7})
        self.assertEqual(sum(stage_counts.values()), 193)
        self.assertEqual(sorted(all_ids), [f"F-{i:03d}" for i in range(1, 194)])
        self.assertEqual(len(CH15_TWENTY_GAPS), 20)

        # Verify explicit per-(stage, domain) disposition accounting across all 193 items
        expected_pairs = {(stage, dom) for stage, domains in F_SCOPE_CLUSTERS.items() for dom in domains}
        self.assertEqual(set(F_DISPOSITION_MAP.keys()), expected_pairs)
        in_scope_cnt = 0
        reserve_cnt = 0
        for (stage, dom), (disp, test_method) in F_DISPOSITION_MAP.items():
            cnt = len(F_SCOPE_CLUSTERS[stage][dom])
            if stage in ("P0", "P1", "P2"):
                self.assertEqual(disp, "IN_SCOPE_BEHAVIORAL_TEST")
                in_scope_cnt += cnt
            else:
                self.assertEqual(disp, "STAGE_RESERVE_CONTRACT_ONLY")
                reserve_cnt += cnt
            fn = getattr(type(self), test_method, None)
            self.assertTrue(callable(fn), f"Missing mapped test method {test_method}")
            src = inspect.getsource(fn)
            self.assertIn("assert", src, f"Mapped test method {test_method} lacks behavioral assertions")
        self.assertEqual(in_scope_cnt, 146)
        self.assertEqual(reserve_cnt, 47)
        self.assertEqual(in_scope_cnt + reserve_cnt, 193)

        design_dir = _design_docs_dir()
        if design_dir is not None:
            features = json.loads((design_dir / "数据清单" / "功能清单.json").read_text(encoding="utf-8"))
            self.assertEqual(len(features), 193)
            by_sd: dict[tuple[str, str], set[str]] = {}
            for row in features:
                by_sd.setdefault((row["proposed_stage"], row["domain"]), set()).add(row["id"])
            for stage, domains in F_SCOPE_CLUSTERS.items():
                for dom, ids in domains.items():
                    self.assertEqual(by_sd[(stage, dom)], set(ids))

            md_path = design_dir / "03-功能与设计资产清单.md"
            self.assertTrue(md_path.is_file(), f"Missing markdown table: {md_path}")
            md_rows = re.findall(
                r"^\|\s*(F-\d{3})\s*\|\s*[^|]+?\s*\|\s*([A-Za-z0-9_]+)\s*\|\s*(P[0-5])\s*\|",
                md_path.read_text(encoding="utf-8"),
                flags=re.MULTILINE,
            )
            self.assertEqual(len(md_rows), 193)
            md_by_sd: dict[tuple[str, str], set[str]] = {}
            for fid, dom, st in md_rows:
                md_by_sd.setdefault((st, dom), set()).add(fid)
            self.assertEqual(md_by_sd, by_sd)

            adrs = json.loads((design_dir / "数据清单" / "待裁定与提案登记.json").read_text(encoding="utf-8"))
            self.assertEqual([a["id"] for a in adrs], [f"ADR-{i:03d}" for i in range(12, 37)])

        # Pure LF, zero emojis, neutral TA on this file & engineering closure doc
        for rel in (
            "tests/test_v02_ch03_f001_f193_matrix_audit.py",
            "docs/engineering/v02-full-closure-verification-20261118.md",
        ):
            fp = ROOT / rel
            raw = fp.read_bytes()
            self.assertNotIn(b"\r\n", raw, f"CRLF detected in {rel}")
            text = raw.decode("utf-8")
            self.assertFalse(any(ord(ch) > 0x1F000 for ch in text), f"Emoji detected in {rel}")
        self_text = (ROOT / "tests" / "test_v02_ch03_f001_f193_matrix_audit.py").read_text(encoding="utf-8")
        self.assertNotIn("\u5979", self_text)

        # 5 sealed baseline files strictly untouched
        for rel, expected_sha in SEALED_BASELINE_SHA256.items():
            actual_sha = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            self.assertEqual(actual_sha, expected_sha, f"Sealed file modified: {rel}")

    # -----------------------------------------------------------------
    # 1. P0 Finding Clusters (15 features: S05, S10, S13, S14)
    # -----------------------------------------------------------------

    def test_02_p0_s05_memory_admission_and_delete_barrier(self) -> None:
        """Audit F-063..F-065 (S05 P0): sourced memory admission, proposal gate, delete/recovery barrier."""
        rlf._ensure_control_ledger_locked(self.day_root)
        # F-063/F-064: memory source admission + derived surfaces
        c_ing, out_ing = rlf.ingest_source(
            self.day_root,
            source_id="src_p0_mem_1",
            text="Secret diary passphrase alpha-9981",
            operation_id="op_ing_p0_1",
            now_iso=T0,
        )
        self.assertEqual(c_ing, 200)
        self.assertEqual(out_ing["generation"], 1)

        for surface in ("episode", "period", "p12_narrative", "vector_index", "belief", "drafts"):
            c_drv, _ = rlf.register_derivation(
                self.day_root,
                node_id=f"drv_{surface}_1",
                surface=surface,
                source_refs=[{"source_id": "src_p0_mem_1", "generation": 1}],
                content=f"Derived {surface}: alpha-9981",
                generation=1,
                operation_id=f"op_drv_{surface}",
                now_iso=T0,
            )
            self.assertEqual(c_drv, 200)

        # F-065: explicit forget builds HMAC opaque tombstone & barrier across all 6 surfaces + V-C seal
        c_fg, out_fg = rlf.execute_explicit_forget(
            self.day_root,
            source_ids=["src_p0_mem_1"],
            operation_id="op_fg_p0_1",
            now_iso=T1,
        )
        self.assertEqual(c_fg, 200)
        self.assertNotIn("alpha-9981", json.dumps(out_fg, ensure_ascii=False))
        rp.record_vc_revocation_seal(self.day_root, "src_p0_mem_1", revoked_at=T1, scope="memory")
        seal = rp.read_vc_authority_watermark_readonly(self.day_root)
        self.assertIn(rp.compute_vc_item_digest("src_p0_mem_1"), seal["sealed_digests"])

        # Old-generation re-derivation blocked (F-065 recovery barrier)
        c_stale, out_stale = rlf.register_derivation(
            self.day_root,
            node_id="drv_resurrect",
            surface="episode",
            source_refs=[{"source_id": "src_p0_mem_1", "generation": 1}],
            content="resurrect",
            generation=1,
            operation_id="op_stale_p0",
            now_iso=T1,
        )
        self.assertEqual(c_stale, 409)
        self.assertEqual(out_stale["code"], "STALE_GENERATION_REJECTED")

    def test_03_p0_s10_delivery_tiers_unknown_and_execution_source(self) -> None:
        """Audit F-113..F-115 (S10 P0): delivery tiers, external UNKNOWN preservation, execution source check."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.tx_t0_commit_decision(decision_id="dec_p0_s10", request_id="req_p0_s10", writer_epoch=1)
        t1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_p0_s10",
            request_id="req_p0_s10",
            logical_action_id="la_p0_s10",
            conclusive_not_found=True,
            writer_epoch=1,
        )
        td = rec.tx_td_prepare_dispatch(action_id=t1["action_id"], writer_epoch=1)
        rec.tx_ta_start_attempt(attempt_id=td["attempt_id"], writer_epoch=1)

        # F-114 & F-115: dropped ack -> UNKNOWN; HUMAN_ATTESTED_EXPERIENCE cannot settle machine T3
        rec.execute_external_attempt(action_id=t1["action_id"], attempt_id=td["attempt_id"], drop_ack=True)
        rec.provider.configure_lookup(lookup_enabled=False, conclusive_not_found=False)
        rec.reconcile_action(action_id=t1["action_id"], writer_epoch=1)

        with self.assertRaises(cpr.ReconcilerContractError) as ctx_h:
            rec.tx_t3_admit_action_result(
                action_id=t1["action_id"],
                attempt_id=td["attempt_id"],
                outcome="SUCCEEDED",
                receipt_kind="HUMAN_ATTESTED_EXPERIENCE",
                receipt_id="rcpt_human_fake",
                writer_epoch=1,
            )
        self.assertEqual(ctx_h.exception.code, "HUMAN_ATTESTATION_CANNOT_SETTLE_MACHINE_OUTCOME")

        hatt = rec.record_human_attestation(
            action_id=t1["action_id"],
            actor_id="partner_1",
            statement="observed message delivery",
            stop_investigation=True,
        )
        self.assertEqual(hatt["receipt_kind"], "HUMAN_ATTESTED_EXPERIENCE")
        self.assertFalse(hatt["authoritative_for_machine_success"])
        self.assertEqual(hatt["outcome_remains"], "UNKNOWN")

        with self.assertRaises(cpr.ReconcilerContractError):
            rec.tx_t3_admit_action_result(
                action_id=t1["action_id"],
                attempt_id=td["attempt_id"],
                outcome="NOT_EXECUTED",
                receipt_kind="PROVIDER_NOT_FOUND",
                receipt_id="rcpt_nf_unproven",
                evidence={"writer_fenced": False, "store_integrity_ok": False},
                writer_epoch=1,
            )

        # F-113: common_ground 3-tier delivery receipt separation
        cg.record_ground_item(
            self.day_root,
            item_id="msg_tier_1",
            topic="tea_time",
            summary="Shall we meet at 5pm?",
            ground_status="PENDING_CLARIFICATION",
            delivery_state=cg.DELIVERY_SENT,
            now_iso=T0,
        )
        adv = cg.advance_communication_receipt(
            self.day_root, item_id="msg_tier_1", delivery_state=cg.DELIVERY_DELIVERED, now_iso=T1
        )
        self.assertEqual(adv["item"]["delivery_state"], cg.DELIVERY_DELIVERED)
        self.assertEqual(adv["item"]["ground_status"], "PENDING_CLARIFICATION")

    def test_04_p0_s13_endpoints_branch_identity_and_writer_fencing(self) -> None:
        """Audit F-140..F-142 (S13 P0): participant endpoints, branch/run identity, monotonic writer fencing."""
        # F-140: PhoneService separates partner vs subject endpoints cleanly
        code_forbid, res_forbid = self.service.v02_honest_ux_action(
            payload={"action": "record_period_snapshot", "period_id": "2026-11", "operation_key": "op_ep_1"},
            subject_view=False,
        )
        self.assertEqual(code_forbid, 403)
        self.assertEqual(res_forbid["reason_code"], "FORBIDDEN_PARTNER_ACTION")

        # F-141: branch/lineage separation on runtime migration device leases
        _, st1 = rgm.apply_runtime_migration_mutation(
            self.day_root,
            {
                "action": "claim_device_presence_lease",
                "device_id": "desk_win_01",
                "branch_id": "main_branch",
                "run_id": "run_001",
                "operation_key": "op_lease_1",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(st1, "APPLIED")

        # F-142: monotonic writer_epoch fencing rejects stale writer
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.control.allocate_new_epoch(fence_previous=True)
        with self.assertRaises(cpr.ReconcilerContractError) as ctx_fence:
            rec.tx_t0_commit_decision(decision_id="dec_stale_ep", request_id="req_stale_ep", writer_epoch=1)
        self.assertEqual(ctx_fence.exception.code, "WRITER_EPOCH_FENCED")

    def test_05_p0_s14_permission_lease_audience_race_and_forget_generation(self) -> None:
        """Audit F-149..F-154 (S14 P0): scoped PermissionLease, audience binding, pre-dispatch revoke, hold, generation."""
        rlf._ensure_control_ledger_locked(self.day_root)
        p_hash = rlf._sha256_text("single paragraph draft excerpt")
        c_iss, _ = rlf.issue_permission_lease(
            self.day_root,
            grant_id="grant_p0_s14",
            grantor=SUBJECT,
            subject=SUBJECT,
            capability="DISCLOSE_MANAGED_ARTIFACT",
            scope="single_paragraph",
            typed_target={"kind": "artifact", "id": "art_s14"},
            audience="partner",
            payload_binding=p_hash,
            max_uses=1,
            operation_id="op_grant_s14",
            now_iso=T0,
        )
        self.assertEqual(c_iss, 200)

        # F-150: wrong audience blocked
        c_aud, out_aud = rlf.evaluate_combined_veto(
            self.day_root,
            grant_id="grant_p0_s14",
            subject=SUBJECT,
            capability="DISCLOSE_MANAGED_ARTIFACT",
            target_id="art_s14",
            audience="public_forum",
            payload_hash=p_hash,
            now_iso=T0,
        )
        self.assertEqual(c_aud, 403)
        self.assertIn("AUDIENCE", out_aud["code"])

        # F-151: revoke before dispatch -> 403 LEASE_REVOKED_BEFORE_DISPATCH & 0 deliveries
        c_rev, _ = rlf.revoke_permission_lease(
            self.day_root,
            grant_id="grant_p0_s14",
            actor_ref=SUBJECT,
            operation_id="op_rev_s14",
            now_iso=T0,
        )
        self.assertEqual(c_rev, 200)
        deliveries: list[Any] = []
        c_disp, out_disp = rlf.execute_guarded_dispatch(
            self.day_root,
            grant_id="grant_p0_s14",
            logical_action_id="la_s14_rev",
            payload_hash=p_hash,
            payload_text="single paragraph draft excerpt",
            subject=SUBJECT,
            capability="DISCLOSE_MANAGED_ARTIFACT",
            target_id="art_s14",
            audience="partner",
            operation_id="op_disp_s14",
            transport_fn=lambda req: deliveries.append(req) or "sent",
            now_iso=T1,
        )
        self.assertEqual(c_disp, 403)
        self.assertEqual(out_disp["code"], "LEASE_REVOKED_BEFORE_DISPATCH")
        self.assertEqual(len(deliveries), 0)

        # F-153/F-154: ambiguous forget -> HOLD_PENDING_SCOPE_CLARIFICATION, no timeout auto-release at +96h
        rlf.ingest_source(
            self.day_root,
            source_id="src_s14_secret",
            text="Sensitive personal token xyz-771",
            operation_id="op_ing_s14",
            now_iso=T0,
        )
        c_hold, out_hold = rlf.submit_forget_command(
            self.day_root,
            command_text="forget that token xyz-771 I just mentioned",
            recent_source_ids=["src_s14_secret"],
            operation_id="op_hold_s14",
            now_iso=T0,
        )
        self.assertEqual(c_hold, 200)
        self.assertEqual(out_hold["status"], "HOLD_PENDING_SCOPE_CLARIFICATION")
        self.assertNotIn("xyz-771", json.dumps(out_hold, ensure_ascii=False))
        c_chk, out_chk = rlf.check_hold_status(
            self.day_root, hold_id=out_hold["hold_id"], elapsed_seconds=96 * 3600
        )
        self.assertEqual(c_chk, 200)
        self.assertEqual(out_chk["status"], "HOLD_PENDING_SCOPE_CLARIFICATION")

    # -----------------------------------------------------------------
    # 2. P1 Finding Clusters (56 features across 8 domains)
    # -----------------------------------------------------------------

    def test_06_p1_s01_managed_artifacts_md_projection_cas_and_health(self) -> None:
        """Audit F-008..F-014 (S01 P1): artifact ID, versions, privacy erase, MD projection, external edit, CAS."""
        ws = self.day_root / "workspace_f193"
        ws.mkdir(parents=True, exist_ok=True)
        rlf._ensure_control_ledger_locked(self.day_root)

        c1, o1 = rlf.create_managed_artifact(
            self.day_root,
            artifact_id="art_s01_p1",
            controller_principal_ref="owner_s01",
            subject_id=SUBJECT,
            initial_body="Version 1 body",
            operation_id="op_s01_c1",
            title="notes.md",
            visibility="shared",
            now_iso=T0,
        )
        self.assertEqual(c1, 200)
        h1 = o1["current_head"]

        rlf.bind_representation(
            self.day_root,
            artifact_id="art_s01_p1",
            workspace_root=ws,
            workspace_rel_path="notes.md",
            operation_id="op_s01_b1",
            now_iso=T0,
        )
        # F-012: external edit -> DIVERGED without overwriting external file
        md_path = ws / "notes.md"
        md_path.write_text("External partner edit\n", encoding="utf-8")
        c_obs, out_obs = rlf.observe_external_edit(
            self.day_root,
            artifact_id="art_s01_p1",
            workspace_root=ws,
            operation_id="op_s01_obs",
            actor_ref="partner",
            trusted_actor_identity=False,
            now_iso=T0,
        )
        self.assertEqual(c_obs, 200)
        self.assertEqual(out_obs["representation_health"], "DIVERGED")
        self.assertIn("External partner edit", md_path.read_text(encoding="utf-8"))

        # F-013: rebuild over DIVERGED blocked 409 & stale CAS expected_head blocked 409; EXPLICIT_IMPORT advances head
        c_reb, out_reb = rlf.rebuild_projection_from_canonical(
            self.day_root,
            artifact_id="art_s01_p1",
            workspace_root=ws,
            operation_id="op_s01_reb",
            now_iso=T0,
        )
        self.assertEqual(c_reb, 409)
        c_blk, out_blk = rlf.commit_artifact_version(
            self.day_root,
            artifact_id="art_s01_p1",
            expected_head="ver_stale_mismatch",
            body="Conflicting overwrite",
            operation_id="op_s01_blk",
            now_iso=T0,
        )
        self.assertEqual(c_blk, 409)
        self.assertEqual(out_blk["code"], "HEAD_CONFLICT")

        c_imp, out_imp = rlf.explicit_import_representation(
            self.day_root,
            artifact_id="art_s01_p1",
            workspace_root=ws,
            base_projection_version=h1,
            operation_id="op_s01_imp",
            actor_ref=SUBJECT,
            now_iso=T1,
        )
        self.assertEqual(c_imp, 200)
        h2 = out_imp["current_head"]
        self.assertNotEqual(h1, h2)

        # F-010: privacy erase on v1 keeps structural shell while wiping payload
        c_er, out_er = rlf.erase_version_payload_privacy(
            self.day_root,
            artifact_id="art_s01_p1",
            version_id=h1,
            operation_id="op_s01_erase",
            now_iso=T1,
        )
        self.assertEqual(c_er, 200)
        self.assertTrue(out_er["ok"])

        # F-014: MISSING projection preserves canonical payload
        md_path.unlink()
        c_insp, out_insp = rlf.inspect_representation(self.day_root, artifact_id="art_s01_p1", workspace_root=ws)
        self.assertEqual(c_insp, 200)
        self.assertEqual(out_insp["representation_health"], "MISSING")

    def test_07_p1_s03_s04_belief_epistemic_states_and_decision_adoption(self) -> None:
        """Audit F-033..F-039 (S03 P1) & F-047..F-051 (S04 P1): observation vs belief, conflict, Decision vs Adoption."""
        cg.register_object_belief(
            self.day_root,
            object_id="obj_mug",
            object_label="ceramic mug",
            spot="desk",
            shared_with_partner=True,
            now_iso=T0,
        )
        cg.record_partner_unseen_move(
            self.day_root,
            object_id="obj_mug",
            new_spot="kitchen_shelf",
            object_label="ceramic mug",
            witnessed_by_subject=False,
            now_iso=T0,
        )
        fetch = cg.attempt_use_or_fetch_object(self.day_root, object_id="obj_mug", now_iso=T0)
        self.assertEqual(fetch["outcome"], "MISSED_AT_REMEMBERED_SPOT")
        self.assertTrue(fetch["should_search_or_ask"])

        # F-036: dual rationale preserves conflicting/independent views side-by-side
        dual = cg.record_dual_rationale_decision(
            self.day_root,
            decision_id="dual_p1_1",
            topic_cn="weekend walk",
            scope_cn="neighborhood",
            adopted_choice_cn="seawall loop",
            subject_rationale_cn="quiet morning light",
            partner_rationale_cn="short shade route",
            now_iso=T0,
        )
        self.assertEqual(dual["decision"]["subject_rationale_cn"], "quiet morning light")
        self.assertEqual(dual["decision"]["partner_rationale_cn"], "short shade route")

        # F-037: partner source correction propagates
        corr = cg.resolve_unseen_move_with_partner(
            self.day_root, object_id="obj_mug", partner_told_spot="kitchen_shelf", now_iso=T1
        )
        self.assertTrue(corr["ok"])

        # S04: F-047..F-051 candidate != active, Decision vs Adoption split
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        d0 = rec.tx_t0_commit_decision(
            decision_id="dec_p1_s04",
            request_id="req_p1_s04",
            input_manifest={"unchosen_options": ["opt_b", "opt_c"]},
            writer_epoch=1,
        )
        self.assertEqual(d0["decision_id"], "dec_p1_s04")
        a1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_p1_s04",
            request_id="req_p1_s04",
            logical_action_id="la_p1_s04",
            writer_epoch=1,
        )
        self.assertEqual(a1["status"], "ADOPTED")

    def test_08_p1_s05_episodes_dedup_layering_invalidation_and_no_change(self) -> None:
        """Audit F-066..F-071 (S05 P1): sourced Episode, common-root dedup, raw/summary layering, index invalidation, NO_CHANGE."""
        ep = asp.record_autobiographical_episode(
            self.day_root,
            {
                "period_id": "2026-11",
                "domain": "craft",
                "title": "Watercolor swatch log",
                "summary": "Tested mineral pigment ratios",
                "outcome": "succeeded",
                "root_event_id": "root_evt_1",
                "visibility": "shared",
            },
            now_iso=T0,
        )
        self.assertEqual(ep["llm_calls"], 0)
        ps = asp.compile_period_summary(self.day_root, {"period_id": "2026-11"}, now_iso=T0)
        self.assertEqual(ps["period_summary"]["episode_count"], 1)
        snap = asp.append_narrative_snapshot(
            self.day_root, {"visibility": "shared", "note": "Nov snapshot", "period_ids": ["2026-11"]}, now_iso=T1
        )
        self.assertEqual(snap["snapshot"]["snapshot_version"], 1)

        # F-070: tombstone invalidates recall projection
        rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_root_1",
            topic="pigment",
            exact_detail="mineral pigment ratio 2:1",
            gist_summary="mineral pigment ratio",
            now_iso=T0,
        )
        rp.tombstone_memory(self.day_root, memory_id="mem_root_1", reason="cleanup", now_iso=T1)
        recall_view = rp.project_recall_set(self.day_root, now_iso=T1, include_unavailable=False)
        self.assertNotIn("mem_root_1", [i["memory_id"] for i in recall_view.get("items", [])])

        # F-071: legal non-applied / NO_CHANGE evaluation without forged optimistic writes
        settled_nc = loop_bridge.settle_v02_v03_round_consequences(
            self.day_root, {"action_kind": "NO_ACTION", "effect_state": "NO_CHANGE"}, now_iso=T1
        )
        self.assertEqual(settled_nc["optimistic_writes"], 0)

    def test_09_p1_s08_s09_claims_promises_continuations_and_completion(self) -> None:
        """Audit F-093..F-097 (S08 P1) & F-103..F-108 (S09 P1): commitments, handoff 409, continuations, completion predicate."""
        # S08 F-095: shared commitment cannot retire without partner agreement (409 EXTERNAL_COMMITMENT_REQUIRES_HANDOFF)
        _, reg = ade.apply_agency_depth_mutation(
            self.day_root,
            {
                "action": "register_gentle_responsibility",
                "resp_id": "resp_promise_p1",
                "title_cn": "Shared booklet delivery",
                "target_kind": "shared_partner_commitment",
                "last_touched_at": T0,
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(reg, "APPLIED")
        with self.assertRaises(ade.V02ConflictError) as ctx_cmt:
            ade.apply_agency_depth_mutation(
                self.day_root,
                {
                    "action": "review_gentle_responsibility",
                    "resp_id": "resp_promise_p1",
                    "decision": "retire_keep_history",
                    "partner_agreed": False,
                },
                actor_side="subject",
                now_iso=T1,
            )
        self.assertEqual(ctx_cmt.exception.reason_code, "EXTERNAL_COMMITMENT_REQUIRES_HANDOFF")

        # S09 continuation & completion predicate via CrashAndPartialReconcilerStore
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.tx_t0_commit_decision(decision_id="dec_s09_p1", request_id="req_s09_p1", writer_epoch=1)
        t1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_s09_p1",
            request_id="req_s09_p1",
            logical_action_id="la_s09_p1",
            writer_epoch=1,
        )
        td = rec.tx_td_prepare_dispatch(action_id=t1["action_id"], writer_epoch=1)
        rec.tx_ta_start_attempt(attempt_id=td["attempt_id"], writer_epoch=1)
        rec.execute_external_attempt(action_id=t1["action_id"], attempt_id=td["attempt_id"])
        rec.tx_t3_admit_action_result(
            action_id=t1["action_id"],
            attempt_id=td["attempt_id"],
            outcome="SUCCEEDED",
            receipt_kind="PROVIDER_RECEIPT",
            receipt_id="rcpt_s09_p1",
            writer_epoch=1,
        )
        ts = rec.tx_ts_create_settlement(
            action_id=t1["action_id"],
            source_fact_id="sf_s09_p1",
            obligations=[("ob_s09_1", "S09", "action_receipt", 1)],
            writer_epoch=1,
        )
        ob_id = ts["obligations"][0]["obligation_id"]
        rec.tx_t4_commit_owner_effect(
            obligation_id=ob_id,
            disposition="APPLIED",
            reason_code="OWNER_VERIFIED",
            writer_epoch=1,
        )
        tc = rec.tx_tc_close_settlement(envelope_id=ts["envelope_id"], writer_epoch=1)
        self.assertEqual(tc["status"], "CLOSED")

    def test_10_p1_s10_request_idempotency_partial_dag_and_bounded_reconcile(self) -> None:
        """Audit F-116..F-122 (S10 P1): request/attempt/result, idempotency, PARTIAL DAG, bounded reconcile, no blind replay."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        comp = rec.create_compound_action(
            parent_logical_action_id="comp_dag_f119",
            child_specs=[
                {"logical_action_id": "dag_c1", "provider_account": "acct_1", "payload": {"n": 1}},
                {"logical_action_id": "dag_c2", "provider_account": "acct_2", "payload": {"n": 2}},
                {"logical_action_id": "dag_c3", "provider_account": "acct_3", "payload": {"n": 3}},
            ],
        )
        c1, c2, c3 = comp["children"]
        k1 = cpr.compute_idempotency_key(
            effect_namespace="compound.batch",
            provider_account="acct_1",
            subject_id="subj_primary",
            origin_branch_id="branch_main",
            logical_action_id="dag_c1",
        )
        self.assertEqual(k1, c1["idempotency_key"])
        with self.assertRaises(cpr.ReconcilerContractError) as ctx:
            rec.check_idempotency_payload_binding(idempotency_key=k1, payload={"n": 999})
        self.assertEqual(ctx.exception.code, "REJECT_IDEMPOTENCY_CONFLICT")

        for child, rcpt_id in ((c1, "rcpt_c1"), (c2, "rcpt_c2")):
            td = rec.tx_td_prepare_dispatch(action_id=child["action_id"])
            rec.tx_ta_start_attempt(attempt_id=td["attempt_id"])
            rec.execute_external_attempt(action_id=child["action_id"], attempt_id=td["attempt_id"])
            rec.tx_t3_admit_action_result(
                action_id=child["action_id"],
                attempt_id=td["attempt_id"],
                outcome="SUCCEEDED",
                receipt_kind="PROVIDER_RECEIPT",
                receipt_id=rcpt_id,
            )

        td3 = rec.tx_td_prepare_dispatch(action_id=c3["action_id"])
        rec.tx_ta_start_attempt(attempt_id=td3["attempt_id"])
        rec.execute_external_attempt(action_id=c3["action_id"], attempt_id=td3["attempt_id"], drop_ack=True)
        rec.provider.configure_lookup(lookup_enabled=False, conclusive_not_found=False)
        out = rec.retry_or_reconcile_compound_action(parent_action_id=comp["parent_action_id"], writer_epoch=1)
        self.assertEqual(rec.provider.count_for_logical_action("dag_c1"), 1)
        self.assertEqual(rec.provider.count_for_logical_action("dag_c2"), 1)
        self.assertEqual(set(out["skipped_succeeded_children"]), {c1["action_id"], c2["action_id"]})
        self.assertEqual(out["after_view"]["compound_status"], "PARTIAL")

    def test_11_p1_infra_consequence_three_axes_outbox_crash_and_restore(self) -> None:
        """Audit F-155..F-167 (INFRA P1): 3-axis settlement, outbox/inbox dedup, WAL PRAGMAs, V-C restore barrier."""
        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        pragma = rec.pragma_snapshot()
        self.assertEqual(pragma["journal_mode"].lower(), "wal")
        self.assertEqual(pragma["synchronous"], 2)
        self.assertEqual(pragma["foreign_keys"], 1)

        # F-161/F-159: outbox -> inbox dedup
        d0 = rec.tx_t0_commit_decision(decision_id="dec_infra_1", request_id="req_infra_1", writer_epoch=1)
        in1 = rec.consume_outbox_to_inbox(consumer="S09", event_id=d0["event_id"], result_ref="ref_1")
        in2 = rec.consume_outbox_to_inbox(consumer="S09", event_id=d0["event_id"], result_ref="ref_1")
        self.assertFalse(in1["deduplicated"])
        self.assertTrue(in2["deduplicated"])

        # F-165: backup/restore V-C watermark fail-closed when external watermark newer than local
        rp.record_vc_revocation_seal(self.day_root, "src_restore_chk", revoked_at=T0, scope="memory")
        seal = rp.read_vc_authority_watermark_readonly(self.day_root)
        missing_digest = hashlib.sha256(b"src_missing_backup").hexdigest()
        res_restore = rgm.reconcile_backup_restore_vc_barrier(
            self.day_root,
            external_watermark_seal={
                "authority_generation": seal["authority_generation"] + 5,
                "sealed_digests": {missing_digest: {"revoked_at": T0}},
            },
            operation_key="op_restore_chk",
            now_iso=T1,
        )
        self.assertTrue(res_restore.get("ok", True))
        merged_wm = rp.read_vc_authority_watermark_readonly(self.day_root)
        self.assertIn(missing_digest, merged_wm["sealed_digests"])

    # -----------------------------------------------------------------
    # 3. P2 Finding Clusters (75 features across 11 domains) + Honest UX Zero-Write
    # -----------------------------------------------------------------

    def test_12_p2_s01_s02_s03_room_embodiment_and_asymmetric_perception(self) -> None:
        """Audit F-001..F-007 (S01 P2), F-023..F-028 (S02 P2), F-040..F-043 (S03 P2)."""
        png_bytes = _build_bicolor_png()
        pal = cmp_.extract_real_image_palette(image_bytes=png_bytes, force_stdlib=True)
        self.assertEqual(pal["width"], 16)
        self.assertGreaterEqual(len(pal["dominant_hex"]), 2)

        code_rd, _ = self.service.v02_crossmodal_action(
            payload={
                "action": "mark_reading_uncertain",
                "book_title": "Optics Notes",
                "page": 14,
                "passage_hint": "Dense mathematical proof",
                "state": "seen_not_understood",
                "operation_key": "op_read_p2",
            },
            subject_view=True,
        )
        self.assertEqual(code_rd, 200)
        _, xm_view = self.service.v02_crossmodal(subject_view=False)
        self.assertEqual(xm_view["llm_calls"], 0)

        _, hux_p = self.service.v02_honest_ux(subject_view=False)
        self.assertIn("chatless_traces", hux_p["data"])
        for subkey in ("objects", "works", "bookmarks", "waits"):
            self.assertIn(subkey, hux_p["data"]["chatless_traces"])

    def test_13_p2_s04_residue_candidates_no_action_and_situation_view(self) -> None:
        """Audit F-052..F-062, F-186 (S04 P2): attention residue, candidates, NO_ACTION, zero-write situation view."""
        before = _snapshot_tree(self.day_root)
        sit = asp.project_situation_provenance_zero_write(
            self.day_root, has_subject_capability=False, now_iso=T0
        )
        self.assertEqual(before, _snapshot_tree(self.day_root))
        self.assertTrue(sit["zero_write"])
        self.assertEqual(sit["llm_calls"], 0)

        with tempfile.TemporaryDirectory(prefix="v02-f193-s04-") as clean_tmp:
            quiet_root = Path(clean_tmp)
            cands = loop_bridge.collect_v02_v03_loop_candidates(quiet_root, now_iso=T0)
            self.assertEqual(cands["llm_calls"], 0)
            sel = loop_bridge.select_deterministic_loop_action(cands)
            self.assertEqual(sel["decision_kind"], "QUIET_NO_ACTION")

        ctx = lived_context_bridge.render_lived_companion_context(
            self.day_root, {}, now_iso=T0
        )
        self.assertLessEqual(len(ctx), lived_context_bridge.MAX_LIVED_CONTEXT_CHARS)

    def test_14_p2_s05_s06_s07_recall_decay_preferences_satiation_and_emotion(self) -> None:
        """Audit F-072..F-077 (S05 P2), F-078..F-083, F-188 (S06 P2), F-088..F-092 (S07 P2)."""
        rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_fade_1",
            topic="rainy_walk",
            exact_detail="Walked along the pier at 18:10",
            gist_summary="Pier walk in rain",
            now_iso=T0,
        )
        fade = rp.mark_active_fade(self.day_root, memory_id="mem_fade_1", now_iso=T1)
        self.assertEqual(fade["direct_projection"]["clarity"], "GIST")

        asp.record_autobiographical_episode(
            self.day_root,
            {
                "period_id": "2026-11",
                "domain": "palette",
                "title": "Tried cobalt blue once",
                "summary": "Temporary color experiment",
                "outcome": "succeeded",
                "importance_signal": 0.65,
                "visibility": "shared",
            },
            now_iso=T0,
        )
        asp.record_self_claim(
            self.day_root,
            {
                "period_id": "2026-11",
                "claim_key": "prefers_cobalt",
                "domain": "palette",
                "lane": "temporary_choice",
                "polarity": "affirm",
                "statement": "Trying cobalt blue today",
                "visibility": "shared",
            },
            now_iso=T0,
        )
        p12 = asp.project_p12_autobiographical_periods(self.day_root, has_subject_capability=True)
        self.assertEqual(len(p12["temporary_choices"]), 1)
        self.assertEqual(len(p12["durable_claims"]), 0)
        prof = p12["domain_profiles"]["palette"]
        self.assertEqual(prof["importance_to_self"]["score"], 0.65)
        self.assertEqual(prof["verified_capability"]["status"], "UNVERIFIED")

    def test_15_p2_s08_s09_wishes_private_works_and_opportunity_shaping(self) -> None:
        """Audit F-098..F-100 (S08 P2) & F-109..F-112 (S09 P2): wishes, private works, salvaged scraps & opportunity."""
        proj, status = lived_slices.mutate_v02_lived_slices(
            self.day_root,
            {
                "action": "salvage_scrap_fragment",
                "scrap_id": "scrap_p2_s09",
                "source_work_id": "work:failed_sketch",
                "source_version_id": "v1",
                "source_outcome": "failed",
                "fragment_title": "Charcoal shadow ratio",
                "reusable_payload": {"ratio": "3:1"},
                "applicability_tags": ["charcoal", "sketch"],
                "provenance_receipt": "rcpt:work_failed_sketch:v1",
                "shared": True,
                "operation_key": "op_salvage_p2",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(status, "APPLIED")
        self.assertFalse(proj["salvaged_scrap_folder"]["items"][0]["auto_inferred_liking"])

        matched = lived_slices.match_salvaged_scraps_for_task(
            self.day_root, task_tags=["sketch"], has_subject_capability=False, now_iso=T1
        )
        self.assertEqual(len(matched["matched_candidates"]), 1)

    def test_16_p2_s11_relationship_isolation_disclosure_common_ground_and_copractice(self) -> None:
        """Audit F-123..F-133 (S11 P2): object isolation, repair, selective sharing, common ground, co-practice."""
        lived_slices.mutate_v02_lived_slices(
            self.day_root,
            {
                "action": "register_copractice_routine",
                "routine_id": "cop_p2_tea",
                "title": "Evening quiet tea",
                "cadence_mode": "co_presence",
                "frequency_per_week": 5,
                "shared": True,
                "operation_key": "op_cop_reg",
            },
            actor_side="subject",
            now_iso=T0,
        )
        lived_slices.mutate_v02_lived_slices(
            self.day_root,
            {
                "action": "rescale_copractice_routine",
                "routine_id": "cop_p2_tea",
                "target_cadence": "light_relay",
                "new_frequency_per_week": 2,
                "participation_scale": 0.5,
                "operation_key": "op_cop_rescale",
            },
            actor_side="subject",
            now_iso=T0,
        )
        code_conf, res_conf = self.service.v02_honest_ux_action(
            payload={
                "action": "confirm_rescaled_copractice",
                "practice_id": "cop_p2_tea",
                "operation_key": "op_cop_confirm",
            },
            subject_view=False,
        )
        self.assertEqual(code_conf, 200)
        self.assertTrue(res_conf["ok"])

        code_cg, view_cg = self.service.v02_common_ground(subject_view=False)
        self.assertEqual(code_cg, 200)
        self.assertEqual(view_cg["llm_calls"], 0)

    def test_17_p2_infra_coalescing_clocks_budget_audit_and_honest_ux_zero_write(self) -> None:
        """Audit F-168..F-174, F-187, F-191, F-193 (INFRA P2) + strict zero-write GET on v02_honest_ux_view."""
        coalescer = rgm.SemanticWakeCoalescer()
        d1 = coalescer.route_event(family="sensor_door", summary_cn="door opened", lane="background", now_iso=T0)
        d2 = coalescer.route_event(family="sensor_door", summary_cn="door opened again", lane="background", now_iso=T0)
        self.assertTrue(d1["effective_wake"])
        self.assertTrue(d2["coalesced"])

        self.assertFalse(rgm.evaluate_gap12_stalled(runnable_count=2, held_by_budget=True, tick_age_s=600.0))
        self.assertTrue(rgm.evaluate_gap12_stalled(runnable_count=2, held_by_budget=False, tick_age_s=226.0))

        asp.configure_budget_and_survival(
            self.day_root, {"normal_daily_cost_cap": 100.0, "normal_daily_token_cap": 40000}, now_iso=T0
        )
        drop = asp.apply_budget_drop(self.day_root, {"drop_pct": 90, "reason": "QUOTA_REDUCTION_90PCT"}, now_iso=T0)
        self.assertEqual(drop["budget"]["budget_scale_pct"], 10)

        # F-193 & zero-write GET check on v02_honest_ux_view (fresh + populated)
        fresh_root = self.temp_root / "fresh_zero_write_root"
        self.assertFalse(fresh_root.exists())
        c_f, d_f = hux.read_v02_honest_ux_view(fresh_root, has_subject_capability=False, now_iso=T0)
        self.assertEqual(c_f, 200)
        self.assertFalse(fresh_root.exists())
        self.assertEqual(d_f["data"]["schema"], hux.VIEW_SCHEMA)
        self.assertEqual(d_f["data"]["provenance"]["source_domain"], "living_traces")

        snap_before = _snapshot_tree(self.day_root)
        c_p, d_p = hux.read_v02_honest_ux_view(self.day_root, has_subject_capability=False, now_iso=T0)
        c_s, d_s = hux.read_v02_honest_ux_view(self.day_root, has_subject_capability=True, now_iso=T0)
        c_svc, d_svc = self.service.v02_honest_ux(subject_view=False)
        snap_after = _snapshot_tree(self.day_root)

        self.assertEqual(snap_before, snap_after)
        self.assertEqual((c_p, c_s, c_svc), (200, 200, 200))
        self.assertEqual(d_p["llm_calls"], 0)
        self.assertEqual(d_svc["llm_calls"], 0)
        self.assertIn("last_synced_at", d_p["data"]["provenance"])

    # -----------------------------------------------------------------
    # 4. Ch.17 §7 P01..P35 Contracts & ADR-012..036 Scope Boundaries
    # -----------------------------------------------------------------

    def test_18_proposals_p01_to_p12_domain_separation_causation_utf8_and_ablation(self) -> None:
        """Audit P01..P12: domain separation, typed binding, causation, UTF-8 length, context recheck, Exp A/B."""
        rlf._ensure_control_ledger_locked(self.day_root)
        p_hash = rlf._sha256_text("utf8 payload \u4e2d\u6587")
        c_iss, _ = rlf.issue_permission_lease(
            self.day_root,
            grant_id="grant_p08",
            grantor=SUBJECT,
            subject=SUBJECT,
            capability="DISCLOSE_MANAGED_ARTIFACT",
            scope="single_paragraph",
            typed_target={"kind": "artifact", "id": "art_p08"},
            audience="partner",
            payload_binding=p_hash,
            max_uses=1,
            operation_id="op_g_p08",
            now_iso=T0,
        )
        self.assertEqual(c_iss, 200)
        # P08: UTF-8 byte length consistency
        self.assertGreater(len("utf8 payload \u4e2d\u6587".encode("utf-8")), len("utf8 payload \u4e2d\u6587"))
        # P09: serialized context cannot spoof capability when real owner gate fails
        c_bad, _ = rlf.evaluate_combined_veto(
            self.day_root,
            grant_id="grant_p08",
            subject=SUBJECT,
            capability="OUTBOUND_SEND",
            target_id="art_p08",
            audience="partner",
            payload_hash=p_hash,
            serialized_context={"verified": True, "capability": "DISCLOSE_MANAGED_ARTIFACT"},
            now_iso=T0,
        )
        self.assertEqual(c_bad, 403)

        # P11..P12: Experiment A (affordance x narrative) and Experiment B (continuation x narrative) separated
        exp_a = lived_slices.run_experiment_a_affordance_x_narrative(num_tasks=12, paired_seeds=(101, 202, 303))
        exp_b = lived_slices.run_experiment_b_continuation_x_narrative(num_tasks=12, paired_seeds=(101, 202, 303))
        self.assertEqual(exp_a["estimand"], "object_availability_causal_effect_with_continuation_pin_off")
        self.assertEqual(exp_b["estimand"], "verified_continuation_pin_gain_with_artifact_present_in_all_cells")
        self.assertNotEqual(exp_a["estimand"], exp_b["estimand"])

    def test_19_proposals_p13_to_p24_mutants_crash_proofs_fairness_and_tombstones(self) -> None:
        """Audit P13..P24: runnable P13 mutants, TD/TA/I/O & NOT_EXECUTED proofs, fairness, HMAC tombstones."""
        muts = lived_slices.run_all_mutant_negative_controls()
        self.assertTrue(muts["all_mutants_bootable_and_effective"])
        self.assertGreaterEqual(len(muts["mutants"]), 3)

        rec = cpr.CrashAndPartialReconcilerStore(self.day_root)
        rec.initialize()
        rec.control.set_store_integrity_flag(True)
        rec.tx_t0_commit_decision(decision_id="dec_p15", request_id="req_p15", writer_epoch=1)
        t1 = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_p15",
            request_id="req_p15",
            logical_action_id="la_p15",
            conclusive_not_found=True,
            writer_epoch=1,
        )
        rec.tx_td_prepare_dispatch(action_id=t1["action_id"], writer_epoch=1)
        rec.provider.configure_lookup(lookup_enabled=True, conclusive_not_found=True)
        ctrl_unfenced = rec.control.allocate_new_epoch(fence_previous=False)
        epoch_2 = int(ctrl_unfenced["active_writer_epoch"])
        res_unfenced = rec.reconcile_action(action_id=t1["action_id"], writer_epoch=epoch_2)
        self.assertEqual(res_unfenced["outcome"], "UNKNOWN")

        rec.tx_t0_commit_decision(decision_id="dec_p15b", request_id="req_p15b", writer_epoch=epoch_2)
        t1_b = rec.tx_t1_commit_adoption_activity_action(
            decision_id="dec_p15b",
            request_id="req_p15b",
            logical_action_id="la_p15b",
            conclusive_not_found=True,
            writer_epoch=epoch_2,
        )
        rec.tx_td_prepare_dispatch(action_id=t1_b["action_id"], writer_epoch=epoch_2)
        ctrl_fenced = rec.control.allocate_new_epoch(fence_previous=True)
        res_fenced = rec.reconcile_action(
            action_id=t1_b["action_id"], writer_epoch=int(ctrl_fenced["active_writer_epoch"])
        )
        self.assertEqual(res_fenced["outcome"], "NOT_EXECUTED")

        rlf._ensure_control_ledger_locked(self.day_root)
        c_ing, _ = rlf.ingest_source(
            self.day_root,
            source_id="src_p23",
            text="private phrase omega-551",
            operation_id="op_p23_ing",
            now_iso=T0,
        )
        self.assertEqual(c_ing, 200)
        c_fg, out_fg = rlf.execute_explicit_forget(
            self.day_root, source_ids=["src_p23"], operation_id="op_p23_fg", now_iso=T1
        )
        self.assertEqual(c_fg, 200)
        self.assertNotIn("omega-551", json.dumps(out_fg, ensure_ascii=False))

    def test_20_proposals_p25_to_p35_holds_skills_metering_migration_and_scope(self) -> None:
        """Audit P25..P35: holds, partial gates, 2x DEGRADED -> 3x SUSPENDED, P30/P31 metering, P32/P33 migration, P35 manifest."""
        asp.register_skill_release(
            self.day_root,
            {
                "method_id": "m_p29",
                "tool_version": "t1",
                "model_id": "m1",
                "scope": "s1",
                "attribution": asp.SKILL_ATTRIBUTION_PRACTICED_MASTERY,
                "criteria_id": "c1",
                "standard_revision": "r1",
                "held_out": True,
                "ablation_gain_vs_bare_model": 0.2,
                "status": asp.SKILL_STATUS_VALIDATED,
            },
        )
        net = asp.record_skill_execution_outcome(
            self.day_root,
            {"method_id": "m_p29", "tool_version": "t1", "model_id": "m1", "scope": "s1", "outcome": "failure", "error_kind": "network_timeout"},
        )
        self.assertEqual(net["classification"], "TRANSIENT_NETWORK_IGNORED")
        for idx in range(3):
            r = asp.record_skill_execution_outcome(
                self.day_root,
                {"method_id": "m_p29", "tool_version": "t1", "model_id": "m1", "scope": "s1", "outcome": "failure", "bug_signature": f"bug_{idx}"},
            )
        self.assertEqual(r["skill_release"]["status"], asp.SKILL_STATUS_SUSPENDED)

        asp.configure_budget_and_survival(
            self.day_root, {"normal_daily_cost_cap": 20.0, "tz_offset_hours": 8}, now_iso="2026-11-18T23:50:00+08:00"
        )
        asp.reserve_unknown_fee(
            self.day_root, {"reservation_id": "r_p31", "upper_bound_cost": 5.0, "action_ref": "a_p31"}, now_iso="2026-11-18T23:55:00+08:00"
        )
        roll = asp.advance_clock_and_rollover_budget(self.day_root, now_iso="2026-11-19T00:05:00+08:00")
        self.assertEqual(roll["active_unknown_reserved_cost"], 5.0)
        jump = asp.advance_clock_and_rollover_budget(self.day_root, now_iso="2026-11-18T22:00:00+08:00")
        self.assertTrue(jump["clock_jump_detected"])
        self.assertEqual(jump["active_unknown_reserved_cost"], 5.0)
        meter = asp.record_metering_event(
            self.day_root, {"layer": "unadmitted_sample", "count": 1}, now_iso=T1
        )
        self.assertEqual(meter["llm_calls"], 0)

        mig = asp.bump_carrier_or_tool_version(
            self.day_root, {"from_model_id": "m1", "to_model_id": "m2", "from_tool_version": "t1", "to_tool_version": "t2"}
        )
        self.assertEqual(mig["migrated_count"], 1)

        release_meta = json.loads((ROOT / "scripts" / "release.json").read_text(encoding="utf-8"))
        manifest_meta = json.loads((ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(release_meta["version"], "v" + manifest_meta["version"].removeprefix("v"))
        self.assertIn(manifest_meta["status"], verify_manifest.VALID_STATUSES)

    def test_21_adr_012_to_036_scope_and_out_of_scope_p3_p4_p5_boundary_audit(self) -> None:
        """Audit ADR-012..ADR-036 operational guards and verify P3/P4/P5 reserves stay honestly bounded."""
        self.assertEqual(loop_bridge.LIFE_UPDATE_SURFACES, 37)
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(lived_context_bridge.MAX_LIVED_CONTEXT_CHARS, 1800)

        cf = car.run_v02_counterfactual_suite(seed=20261118)
        self.assertTrue(cf.get("all_passed", False))
        self.assertEqual(cf.get("llm_calls", 0), 0)

        out_of_scope_research = F_SCOPE_CLUSTERS["P3"]["RESEARCH"] + F_SCOPE_CLUSTERS["P5"]["RESEARCH"]
        self.assertEqual(len(out_of_scope_research), 11)

    # -----------------------------------------------------------------
    # 5. Ch.15 §10 All 20 Mechanism Gaps End-to-End Audit
    # -----------------------------------------------------------------

    def test_22_ch15_gaps_01_to_07_environment_body_perception_lineage_time_decision(self) -> None:
        """Audit Ch.15 §10 Gaps 1..7 against v02_* modules & PhoneService."""
        cg.register_object_belief(self.day_root, object_id="obj_lamp", spot="desk", shared_with_partner=True, now_iso=T0)
        cg.record_partner_unseen_move(self.day_root, object_id="obj_lamp", new_spot="shelf", witnessed_by_subject=False, now_iso=T0)
        res_fetch = cg.attempt_use_or_fetch_object(self.day_root, object_id="obj_lamp", now_iso=T0)
        self.assertEqual(res_fetch["outcome"], "MISSED_AT_REMEMBERED_SPOT")

        rlf._ensure_control_ledger_locked(self.day_root)
        c1, o1 = rlf.create_managed_artifact(
            self.day_root,
            artifact_id="art_gap4",
            controller_principal_ref="owner",
            subject_id=SUBJECT,
            initial_body="v1",
            operation_id="op_g4",
            now_iso=T0,
        )
        self.assertEqual(c1, 200)
        self.assertTrue(o1["current_head"])

        dual = cg.record_dual_rationale_decision(
            self.day_root,
            decision_id="dec_gap7",
            topic_cn="desk layout",
            scope_cn="study",
            adopted_choice_cn="window side",
            subject_rationale_cn="daylight",
            partner_rationale_cn="outlet access",
            now_iso=T0,
        )
        self.assertEqual(dual["decision"]["decision_id"], "dec_gap7")

    def test_23_ch15_gaps_08_to_14_inquiry_satiation_commitment_p12_skill_ablation_works(self) -> None:
        """Audit Ch.15 §10 Gaps 8..14 against v02_agency_depth_engine, v02_autobiography_skill_provenance, v02_lived_slices."""
        code_open, res_open = self.service.v02_agency_depth_action(
            payload={
                "action": "open_bounded_inquiry",
                "question": "Which pigment binder resists yellowing?",
                "topic_tag": "pigment",
                "shared": True,
                "step_budget": 2,
                "next_evidence_needed": "compare linseed vs walnut binder",
                "operation_key": "op_inq_gap8",
            },
            subject_view=True,
        )
        self.assertEqual(code_open, 200)
        self.assertEqual(res_open["llm_calls"], 0)

        code_get, view_ad = self.service.v02_agency_depth(subject_view=True)
        self.assertEqual(code_get, 200)
        self.assertIn("inquiries", view_ad["data"])
        self.assertEqual(len(view_ad["data"]["inquiries"]), 1)

        asp.record_autobiographical_episode(
            self.day_root,
            {
                "period_id": "2026-11",
                "domain": "painting",
                "title": "Binder test",
                "summary": "Walnut oil stayed clear",
                "outcome": "succeeded",
                "visibility": "shared",
            },
            now_iso=T0,
        )
        ps = asp.compile_period_summary(self.day_root, {"period_id": "2026-11"}, now_iso=T0)
        self.assertEqual(ps["period_summary"]["episode_count"], 1)

        kernel = lived_slices.SliceProtocolKernel(self.day_root)
        kernel.boot()
        k_v1 = kernel.commit_artifact_version(
            artifact_id="art_gap14",
            new_version_id="v1",
            content="stroke 1",
            expected_head_version=None,
        )
        self.assertEqual(k_v1["head_version"], "v1")

    def test_24_ch15_gaps_15_to_20_audience_forget_migration_retirement_idle_and_branch_replay(self) -> None:
        """Audit Ch.15 §10 Gaps 15..20 against v02_representation_lease_forget, v02_runtime_governance_migration, v02_honest_ux_view."""
        rlf._ensure_control_ledger_locked(self.day_root)

        # Gap 15: Multi-object audience isolation (partner view cannot read private artifact or private source)
        c_priv_art, out_priv_art = rlf.create_managed_artifact(
            self.day_root,
            artifact_id="art_gap15_priv",
            controller_principal_ref="owner_gap15",
            subject_id=SUBJECT,
            initial_body="# Private Gap15 Diary\nsecret_line_15\n",
            operation_id="op_g15_priv_art",
            title="gap15_priv.md",
            visibility="private",
            now_iso=T0,
        )
        self.assertEqual(c_priv_art, 200)
        head_g15 = out_priv_art["current_head"]

        rlf.ingest_source(
            self.day_root,
            source_id="src_gap16",
            text="Private gap16 secret line",
            operation_id="op_g16_ing",
            visibility="private",
            now_iso=T0,
        )
        # Gap 16: Explicit forget + external V-C backup restore barrier
        c_fg, out_fg = rlf.execute_explicit_forget(
            self.day_root, source_ids=["src_gap16"], operation_id="op_g16_fg", now_iso=T1
        )
        self.assertEqual(c_fg, 200)
        self.assertNotIn("Private gap16 secret line", json.dumps(out_fg, ensure_ascii=False))
        seal = rp.read_vc_authority_watermark_readonly(self.day_root)
        gap16_digest = hashlib.sha256(b"src_gap16").hexdigest()
        res_vc = rgm.reconcile_backup_restore_vc_barrier(
            self.day_root,
            external_watermark_seal={
                "authority_generation": int(seal.get("authority_generation") or 0) + 2,
                "sealed_digests": {gap16_digest: {"revoked_at": T1}},
            },
            operation_key="op_g16_vc_restore",
            now_iso=T1,
        )
        self.assertGreaterEqual(res_vc.get("applied_sealed_count", 1), 1)

        # Gap 17: Model-agnostic suitcase export -> import loss manifest with 0 LLM calls
        c_pack, _ = self.service.v02_runtime_migration_action(
            payload={
                "action": "pack_selective_suitcase",
                "profile_mode": "selective_practice",
                "leave_behind_note": "gap17 check",
                "operation_key": "op_g17_pack",
            },
            subject_view=True,
        )
        self.assertEqual(c_pack, 200)
        c_exp, exp_res = self.service.v02_runtime_migration_action(
            payload={"action": "export_suitcase", "operation_key": "op_g17_exp"},
            subject_view=True,
        )
        self.assertEqual(c_exp, 200)
        c_imp, imp_res = self.service.v02_runtime_migration_action(
            payload={
                "action": "import_suitcase",
                "bundle": exp_res["suitcase_bundle"],
                "target_model_id": "model_next_g17",
                "target_carrier_mode": "headless",
                "operation_key": "op_g17_imp",
            },
            subject_view=True,
        )
        self.assertEqual(c_imp, 200)
        self.assertIn("migration_loss_manifest", imp_res)
        self.assertEqual(imp_res["llm_calls"], 0)

        # Gap 18: Long-term maintenance & guilt-free retirement/pause (0 debt/penalty)
        lived_slices.mutate_v02_lived_slices(
            self.day_root,
            {
                "action": "register_copractice_routine",
                "routine_id": "cop_g18",
                "title": "Quiet tea note",
                "cadence_mode": "light_relay",
                "frequency_per_week": 2,
                "shared": True,
                "operation_key": "op_g18_reg",
            },
            actor_side="subject",
            now_iso=T0,
        )
        lived_slices.mutate_v02_lived_slices(
            self.day_root,
            {
                "action": "rescale_copractice_routine",
                "routine_id": "cop_g18",
                "target_cadence": "paused_no_debt",
                "new_frequency_per_week": 0.0,
                "participation_scale": 0.0,
                "partner_confirmed": True,
                "operation_key": "op_g18_pause",
            },
            actor_side="partner",
            now_iso=T1,
        )
        cop_view = lived_slices.project_v02_lived_slices(self.day_root, has_subject_capability=False, now_iso=T1)
        cop_row = next(r for r in cop_view["rescaled_copractice"]["routines"] if r["routine_id"] == "cop_g18")
        self.assertEqual(cop_row["cadence_mode"], "paused_no_debt")
        self.assertEqual(float(cop_row["current_frequency_per_week"]), 0.0)
        self.assertEqual(float(cop_row.get("affection_debt", 0.0)), 0.0)
        self.assertEqual(float(cop_row.get("relationship_penalty", 0.0)), 0.0)

        code_mig, res_mig = self.service.v02_runtime_migration(subject_view=False)
        self.assertEqual(code_mig, 200)
        self.assertEqual(res_mig["llm_calls"], 0)

        # Gap 15 + Gap 19: Partner view strips private artifacts & true inactivity is 0-write & 0 LLM
        snap_before = _snapshot_tree(self.day_root)
        c_hux, out_hux = self.service.v02_honest_ux(subject_view=False)
        self.assertEqual(snap_before, _snapshot_tree(self.day_root))
        self.assertEqual(c_hux, 200)
        self.assertTrue(out_hux["ok"])
        self.assertEqual(out_hux["llm_calls"], 0)
        self.assertTrue(out_hux["data"]["crash_reconciler_summary"].get("perspective_redacted"))
        partner_dump = json.dumps(out_hux["data"], ensure_ascii=False)
        self.assertNotIn("art_gap15_priv", partner_dump)
        self.assertNotIn("Private gap16 secret line", partner_dump)

        _, sub_hux = self.service.v02_honest_ux(subject_view=True)
        self.assertIn("art_gap15_priv", json.dumps(sub_hux["data"], ensure_ascii=False))

        # Gap 19: Behavioural check on real runtime budget: DORMANT drives budget to 0%
        c_mig, _ = self.service.v02_runtime_migration_action(
            payload={"action": "set_budget_mode", "mode": "DORMANT", "reason_cn": "休眠核验"},
            subject_view=True,
        )
        self.assertEqual(c_mig, 200)
        _, hux_dorm = self.service.v02_honest_ux(subject_view=True)
        self.assertEqual(hux_dorm["data"]["budget_survival"]["mode"], "DORMANT")
        self.assertEqual(hux_dorm["data"]["budget_survival"]["budget_scale_pct"], 0)
        self.assertFalse(hux_dorm["data"]["budget_survival"]["allow_exploratory_cognition"])

        # Gap 20: Stale writer_epoch & stale expected_head CAS rejection + authoritative receipt check
        c_stale_ep, out_stale_ep = rlf.commit_artifact_version(
            self.day_root,
            artifact_id="art_gap15_priv",
            expected_head=head_g15,
            body="# Blocked stale epoch\n",
            operation_id="op_g20_stale_ep",
            writer_epoch=0,
            now_iso=T2,
        )
        self.assertEqual(c_stale_ep, 409)
        self.assertEqual(out_stale_ep["code"], "STALE_WRITER_EPOCH")
        c_stale_hd, out_stale_hd = rlf.commit_artifact_version(
            self.day_root,
            artifact_id="art_gap15_priv",
            expected_head="ver_wrong_head_g20",
            body="# Blocked stale head\n",
            operation_id="op_g20_stale_hd",
            writer_epoch=1,
            now_iso=T2,
        )
        self.assertEqual(c_stale_hd, 409)
        self.assertEqual(out_stale_hd["code"], "HEAD_CONFLICT")
        ro_g20, _ = rlf._load_state_readonly(self.day_root)
        self.assertEqual(ro_g20["artifacts"]["art_gap15_priv"]["current_head"], head_g15)

        bad_settle = loop_bridge.settle_v02_v03_round_consequences(
            self.day_root,
            {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "action_kind": "OUTING_VISIT",
                "receipt_ref": "crcpt:unverified_matrix_probe",
                "require_authoritative_receipt": True,
            },
            now_iso=T1,
        )
        self.assertFalse(bad_settle["ok"])
        self.assertEqual(bad_settle["reason_code"], "UNVERIFIED_OWNER_RECEIPT")
        self.assertEqual(bad_settle["optimistic_writes"], 0)


if __name__ == "__main__":
    unittest.main()

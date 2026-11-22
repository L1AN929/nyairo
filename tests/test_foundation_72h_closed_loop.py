#!/usr/bin/env python3
"""Final 72-Hour Accelerated Closed-Loop Causal Proof for the NyAiro Five-Pillar Foundation.

Single, deterministic, zero-LLM (``llm_calls == 0``) multi-day integration test
starting from a brand-new clean ``nyairo_day.py`` managed instance
(``_ensure_instance`` + ``bootstrap_living_foundation_state`` with external
``authority.db``, ``controls.db``, ``v02-control/``, ``evidence.db``,
``episode.db``, and ``v02/canonical_life.sqlite3``) and advancing 72 simulated
hours (``2026-04-02T08:00:00+08:00`` -> ``2026-04-05T08:00:00+08:00``) plus a
``+16d`` recall probe, asserting all 7 phases of Blueprint section 5 in one
continuous living world with zero manual UI/CRUD state faking.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unicodedata
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    REPO_ROOT,
    REPO_ROOT / "scripts",
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "world" / "service",
    REPO_ROOT / "components" / "phone",
    REPO_ROOT / "components" / "native",
    REPO_ROOT / "components" / "native" / "src",
    REPO_ROOT / "components" / "native" / "memory_runtime_v1",
):
    if _entry.exists() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import (  # noqa: E402
    autonomous_life_cycle as cycle_mod,
    candidate_sources_ag0 as ag0,
    proactive_outreach_producer as outreach_prod,
    s07_affect_owner as s07_affect,
    v02_crash_and_partial_reconciler as reconciler,
    v02_recall_projection as v02_rp,
    v02_representation_lease_forget as v02_rlf,
    v02_v03_daily_loop_bridge as loop_bridge,
    v03_clock_evaporation as v03_clk,
    v04_elastic_play_learning as v04_ep,
    v05_dreams_desires_foreshadow as v05_spark,
    world_move_opportunity_source as wmos,
)
from components.native.memory_runtime_v1 import production_resolver as prod_res  # noqa: E402
from components.phone import (  # noqa: E402
    adult_intimacy_view as aiv,
    autonomy_rhythm_view as arv,
    server as phone_server,
    service as phone_service,
    somatic_comfort_view as scv,
    v05_living_town_view as v05_town_view,
)
from components.world.scripts import (  # noqa: E402
    body_health_projection as bhp,
    body_lived_needs as bln,
    instance_paths as ip,
    v03_economy_illness_bridge as v03_eco,
    v04_neighborhood_living as v04_nb,
    v05_home_craft_engine as v05_craft,
    v05_living_town_engine as v05_town,
    v05_unified_world_fabric as v05_fabric,
    v08_composable_body as v08_cb,
    v08_microzone_ecology as v08_mz,
    world_living_skeleton as skeleton,
)
from scripts import nyairo_day  # noqa: E402
from scripts import v02_wall_clock_soak as soak_runner  # noqa: E402

try:
    from components.world.scripts import body_runtime as br  # noqa: E402
except Exception:  # pragma: no cover - non-POSIX fallback
    br = None  # type: ignore[assignment]

IS_POSIX = os.name == "posix" and br is not None
TZ_BJT = timezone(timedelta(hours=8))

# Deterministic spring date (2026-04-02 .. 2026-04-05) outside Sep-Nov so
# calendar season == "spring" (never locked to "autumn"), S1 == "clear",
# S2 == "drizzle", Day 2 wake dream hits naturally, and Day 3 S1 == "rain".
DAY1 = "2026-04-02"
DAY2 = "2026-04-03"
DAY3 = "2026-04-04"
DAY4 = "2026-04-05"
DAY20 = "2026-04-21"

SEALED_AND_FROZEN_REL_PATHS: tuple[str, ...] = (
    "components/life/scripts/autonomous_life_assembly.py",
    "components/life/scripts/life_sharing_service.py",
    "components/native/native_recall/readonly_recall_adapter.py",
    "NOW.md",
    "plugins/chiyo/__init__.py",
    "components/native/native_recall/production_consumption_evaluator_v0_3.py",
    "components/native/native_recall/production_consumption_evaluator_v0.py",
    "components/native/native_recall/CANDIDATE_CONSUMPTION_POLICY_V0_3.json",
    "components/world/scripts/v05_unified_world_fabric.py",
)


def _iso_dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _sidecar_artifacts(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.name.endswith(("-wal", "-shm", ".lock", ".tmp"))
    )


def _sqlite_count(db_path: Path, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
) -> tuple[int, dict[str, Any]]:
    req_headers = dict(headers or {})
    data_bytes: Optional[bytes] = None
    if body is not None:
        data_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"raw": raw}
        return int(err.code), parsed


def _load_plugin_module() -> Any:
    plugin_path = REPO_ROOT / "plugins" / "chiyo" / "__init__.py"
    spec = importlib.util.spec_from_file_location("foundation_72h_plugin_check", plugin_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_ag0_record(
    *,
    candidate_id: str,
    source_kind: str,
    candidate_kind: str,
    source_ref: str,
    targets: list[str],
    observed_at: str,
    activity_ref: Optional[str] = None,
) -> ag0.CandidateRecord:
    is_world = source_kind == ag0.SOURCE_WORLD_OPPORTUNITY
    return ag0.CandidateRecord(
        candidate_id=candidate_id,
        candidate_kind=candidate_kind,
        source_kind=source_kind,
        source_ref=source_ref,
        source_refs=[source_ref],
        source_revision=1,
        activity_ref=activity_ref,
        user_event_ref=None,
        commitment_ref=None,
        perception_ref="percept:72h:r3" if is_world else None,
        world_revision_ref="wrev:72h:r3" if is_world else None,
        world_opportunity_ref=source_ref if is_world else None,
        target_refs=list(targets),
        causal_parent_refs=[source_ref],
        provenance_refs=[source_ref, candidate_id],
        provenance_chain=[{"step": "candidate", "ref": candidate_id}],
        availability_status=ag0.CANDIDATE_STATUS_OPEN,
        created_at=observed_at,
        observed_at=observed_at,
        recorded_at=observed_at,
        idempotency_key=f"idem_72h:{candidate_id}",
    )


class _LoopbackPhoneHarness:
    def __init__(
        self,
        inst_root: Path,
        *,
        csrf_token: str = "foundation-72h-csrf",
        world_clock: Any = None,
    ) -> None:
        self.inst_root = inst_root
        self.csrf_token = csrf_token
        self.instance = phone_service.load_instance(inst_root)
        self.svc = phone_service.PhoneService(self.instance, world_clock=world_clock)
        self.srv = phone_server.PhoneHTTPServer(
            ("127.0.0.1", 0),
            service=self.svc,
            csrf_token=csrf_token,
        )
        self.base_url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self._thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self) -> "_LoopbackPhoneHarness":
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def _snapshot_json_sidecars(roots: tuple[Path, ...]) -> dict[Path, bytes]:
    snap: dict[Path, bytes] = {}
    for base in roots:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.json")):
            if p.is_file() and not p.is_symlink():
                snap[p] = p.read_bytes()
    return snap


def _restore_json_sidecars(roots: tuple[Path, ...], pre_snap: dict[Path, bytes]) -> None:
    for base in roots:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.json")):
            if p.is_file() and not p.is_symlink() and p not in pre_snap:
                p.unlink()
    for path, raw_bytes in pre_snap.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw_bytes)


class TestFoundation72HourClosedLoop(unittest.TestCase):
    def setUp(self) -> None:
        fixture_base_env = os.environ.get("NYAIRO_FORGET_FIXTURE_ROOT")
        base_parent: Optional[str] = None
        if fixture_base_env:
            Path(fixture_base_env).mkdir(parents=True, exist_ok=True)
            base_parent = fixture_base_env
        elif IS_POSIX:
            base_parent = "/tmp"
        self.sandbox = Path(tempfile.mkdtemp(prefix="f72h_", dir=base_parent))
        self.addCleanup(lambda: shutil.rmtree(self.sandbox, ignore_errors=True))
        self._prev_ctrl_env = os.environ.get("NYAIRO_V02_CONTROL_ROOT")
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        if self._prev_ctrl_env is None:
            os.environ.pop("NYAIRO_V02_CONTROL_ROOT", None)
        else:
            os.environ["NYAIRO_V02_CONTROL_ROOT"] = self._prev_ctrl_env

    @unittest.skipUnless(IS_POSIX, "Full managed nyairo_day + POSIX M0/M1/BodyStore proof requires Linux/WSL")
    def test_01_72h_continuous_closed_loop_all_seven_phases(self) -> None:
        from app.evidence import LIFE_ORIGIN  # noqa: E402
        from app.m1 import EpisodeStore  # noqa: E402
        from components.native.memory_runtime_v1.life_experience_port import (  # noqa: E402
            LifeExperiencePort,
        )
        from memory_authority import MemoryDeletionAuthority  # noqa: E402
        from memory_control import MemoryControlLedger  # noqa: E402

        total_llm_calls = 0

        def _acc_llm(payload: Any) -> None:
            nonlocal total_llm_calls
            if isinstance(payload, dict) and "llm_calls" in payload:
                val = int(payload["llm_calls"] or 0)
                self.assertEqual(val, 0, f"Unexpected non-zero llm_calls: {payload}")
                total_llm_calls += val

        # =====================================================================
        # PHASE 1 — Hour 00 (Day 1 08:00): Clean Cold-Start & Calm Gate Proof
        # =====================================================================
        inst_root = (self.sandbox / "day72").resolve()
        auth_dir = (self.sandbox / "day72-memory-authority").resolve()
        auth_db = auth_dir / "authority.db"
        ctrl_root = auth_dir / "v02-control"
        os.environ["NYAIRO_V02_CONTROL_ROOT"] = str(ctrl_root)

        t_d1_0800 = f"{DAY1}T08:00:00+08:00"
        with mock.patch.object(nyairo_day, "_now_iso", return_value=t_d1_0800):
            ensure_rep = nyairo_day._ensure_instance(
                inst_root,
                authority=auth_db,
                run_id="proof72h",
            )
        self.assertTrue(ensure_rep["ok"], ensure_rep)
        self.assertEqual(ensure_rep["status"], "PREPARED")
        boot = ensure_rep["living_foundation_bootstrap"]
        self.assertIsNotNone(boot)
        self.assertEqual(boot["created_count"], 7)
        _acc_llm(boot)

        # Verify lived_needs enabled (0.0, 0.0, 0.0) & zero shadow body/lived_needs.json
        self.assertEqual(
            ensure_rep["config"]["lived_needs"],
            {"enabled": True, "hunger": 0.0, "thirst": 0.0, "toilet": 0.0},
        )
        self.assertFalse((inst_root / "body" / "lived_needs.json").exists())

        paths = ip.resolve_instance_paths(inst_root)
        self.assertTrue(paths.is_managed_instance)
        self.assertTrue(paths.memory_evidence_db.is_file())
        self.assertTrue(paths.memory_controls_db.is_file())
        self.assertIsNotNone(paths.memory_authority_db)
        assert paths.memory_authority_db is not None
        self.assertTrue(paths.memory_authority_db.is_file())
        self.assertTrue((paths.body_dir / "lived_needs_profile.json").is_file())
        self.assertTrue(paths.world_state_json.is_file())

        # Verify world_home and world_home/data/body normalize upward to inst_root
        self.assertEqual(ip.resolve_instance_paths(paths.world_home).day_root, inst_root)
        self.assertEqual(ip.resolve_instance_paths(paths.body_dir).day_root, inst_root)

        # Initialize sibling M1 episode.db and v02/canonical_life.sqlite3
        episode_db = paths.memory_evidence_db.parent / "episode.db"
        EpisodeStore(episode_db)
        self.assertTrue(episode_db.is_file())
        reconciler.CrashAndPartialReconcilerStore(inst_root / "v02").initialize()
        self.assertTrue((inst_root / "v02" / "canonical_life.sqlite3").is_file())

        # Calm Gate: compact situation == "" & loopback GET /api/phone/outbound/pending == 0 with 0 disk writes
        before_calm_snap = _snapshot_tree(inst_root)
        before_calm_sidecars = _sidecar_artifacts(inst_root)
        self.assertEqual(
            lcb._format_compact_current_situation(day_root=inst_root, now_iso=t_d1_0800),
            "",
        )
        with _LoopbackPhoneHarness(inst_root, world_clock=lambda: _iso_dt(t_d1_0800)) as harness:
            code_p1, doc_p1 = _http_json(harness.base_url + "/api/phone/outbound/pending")
        self.assertEqual(code_p1, 200)
        self.assertTrue(doc_p1["ok"])
        self.assertTrue(doc_p1["zero_write"])
        _acc_llm(doc_p1)
        self.assertEqual(doc_p1["data"]["pending_count"], 0)
        self.assertEqual(doc_p1["data"]["items"], [])
        self.assertEqual(_snapshot_tree(inst_root), before_calm_snap)
        self.assertEqual(_sidecar_artifacts(inst_root), before_calm_sidecars)

        # First heartbeat tick (force_init=False) -> status == "ticked" & dynamic season == "spring"
        t_d1_0805 = f"{DAY1}T08:05:00+08:00"
        tick_p1 = nyairo_day.run_v03_background_clock_tick(
            inst_root, now_iso=t_d1_0805, force_init=False
        )
        self.assertTrue(tick_p1["ok"], tick_p1)
        self.assertEqual(tick_p1["status"], "ticked")
        self.assertIn("v03_clock", tick_p1["slices_ticked"])
        self.assertIn("v04_neighborhood", tick_p1["slices_ticked"])
        _acc_llm(tick_p1)

        nb_disk = json.loads((inst_root / "world" / "v04_neighborhood_state.json").read_text(encoding="utf-8"))
        self.assertIsNone(nb_disk["environment"]["season"])
        self.assertFalse(nb_disk["environment"]["season_explicit"])
        self.assertEqual(nb_disk["environment"]["weather"], "clear")

        v04_p1 = v04_nb.project_v04_neighborhood(inst_root, now_iso=t_d1_0805)
        self.assertEqual(v04_p1["data"]["environment"]["season"], "spring")
        spark_p1 = v05_spark.project_v05_inner_spark_state(
            inst_root, has_subject_capability=True, now_iso=t_d1_0805
        )
        self.assertEqual(spark_p1["season_solar"]["season"], "spring")
        v08_p1 = v08_mz.resolve_local_conditions_zero_write(inst_root, now_iso=t_d1_0805)
        self.assertEqual(v08_p1["season"], "spring")

        # =====================================================================
        # PHASE 2 — Hours 01-06 (Day 1 09:00 -> 14:00): Anti-Bedroom-Loop & World Ground Truth
        # =====================================================================
        t_d1_1230 = f"{DAY1}T12:30:00+08:00"
        stuffiness = loop_bridge.compute_desk_stuffiness(
            ["continue_note", "continue_note"],
            is_final_closeout_step=False,
        )
        self.assertTrue(stuffiness["stuffy"], stuffiness)
        self.assertEqual(stuffiness["streak"], 2)
        self.assertGreater(float(stuffiness["discount"]), 0.0)
        self.assertTrue(bool(stuffiness.get("hint_cn")))
        # Final closeout step must still be exempt from desk stuffiness interruption
        exempt = loop_bridge.compute_desk_stuffiness(
            ["continue_note", "continue_note"],
            is_final_closeout_step=True,
        )
        self.assertFalse(exempt["stuffy"])

        ag0_records = [
            _make_ag0_record(
                candidate_id="cand:a00_note",
                source_kind=ag0.SOURCE_CURRENT_ACTIVITY,
                candidate_kind=ag0.CANDIDATE_KIND_CONTINUE_CURRENT,
                source_ref="act:desk_note_1",
                targets=["artifact:desk_note_1"],
                observed_at=t_d1_1230,
                activity_ref="act:desk_note_1",
            ),
            _make_ag0_record(
                candidate_id="cand:p01_note2",
                source_kind=ag0.SOURCE_PERSONAL_OPPORTUNITY,
                candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
                source_ref="artifact:desk_note_2",
                targets=["artifact:desk_note_2"],
                observed_at=t_d1_1230,
            ),
            _make_ag0_record(
                candidate_id="cand:w00_stay",
                source_kind=ag0.SOURCE_WORLD_OPPORTUNITY,
                candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
                source_ref="wopp:stay_home_daze",
                targets=["stay:home:DAZE:180"],
                observed_at=t_d1_1230,
            ),
            _make_ag0_record(
                candidate_id="cand:w99_park",
                source_kind=ag0.SOURCE_WORLD_OPPORTUNITY,
                candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
                source_ref="wopp:move_park",
                targets=["place:park"],
                observed_at=t_d1_1230,
            ),
        ]
        # Baseline (streak=0) keeps CONTINUE_CURRENT first; streak=2 promotes outdoor move!
        base_bounded = ag0.CandidateMaterializer.bounded_candidates_by_source(
            ag0_records, 2, recent_note_streak=0, is_final_closeout_step=False
        )
        self.assertIn("cand:a00_note", [c.candidate_id for c in base_bounded])
        shifted_bounded = ag0.CandidateMaterializer.bounded_candidates_by_source(
            ag0_records, 2, recent_note_streak=2, is_final_closeout_step=False
        )
        self.assertEqual(shifted_bounded[0].candidate_id, "cand:w99_park")
        self.assertNotIn("cand:a00_note", [c.candidate_id for c in shifted_bounded])

        # world_move_opportunity_source enriches outdoor moves with non-empty reason_hint_cn (zero-write!)
        raw_moves = [
            {
                "opportunity_id": "wopp:move:home:r2:bookstore",
                "perception_ref": "percept:72h:home:r2",
                "world_revision_ref": "wrev:72h:r2",
                "target_refs": ["place:bookstore"],
                "valid_from": t_d1_1230,
                "valid_until": f"{DAY1}T12:40:00+08:00",
                "occurred_at": t_d1_1230,
                "observed_at": t_d1_1230,
                "observed": True,
                "status": "AVAILABLE",
                "revision": 1,
                "subject_id": ag0.GLOBAL_SUBJECT_ID,
                "location_ref": "place:home",
            },
            {
                "opportunity_id": "wopp:move:home:r2:park",
                "perception_ref": "percept:72h:home:r2",
                "world_revision_ref": "wrev:72h:r2",
                "target_refs": ["place:park"],
                "valid_from": t_d1_1230,
                "valid_until": f"{DAY1}T12:40:00+08:00",
                "occurred_at": t_d1_1230,
                "observed_at": t_d1_1230,
                "observed": True,
                "status": "AVAILABLE",
                "revision": 1,
                "subject_id": ag0.GLOBAL_SUBJECT_ID,
                "location_ref": "place:home",
            },
        ]
        snap_before_moves = _snapshot_tree(inst_root)
        enriched_moves = wmos.enrich_move_opportunities_with_life_reasons(
            raw_moves,
            day_root=inst_root,
            observation={"location": {"place_id": "home"}},
            now_iso=t_d1_1230,
            recent_note_streak=2,
        )
        self.assertEqual(_snapshot_tree(inst_root), snap_before_moves)
        by_target = {row["target_refs"][0]: row for row in enriched_moves}
        self.assertTrue(bool(by_target["place:bookstore"].get("reason_hint_cn")))
        self.assertTrue(bool(by_target["place:park"].get("reason_hint_cn")))

        # Verify AutonomousLifeCycle tracks recent_note_streak=2 and surfaces desk_stuffiness + reason_hint_cn
        recorded_rhythm: list[dict[str, Any]] = []
        mat_stub = SimpleNamespace(
            set_breathing_rhythm_context=lambda **kw: recorded_rhythm.append(dict(kw))
        )

        class _QuietCoordinator:
            def run_decision_and_adoption_cycle(self, **_kwargs: Any) -> dict[str, Any]:
                return {
                    "correlation_id": "corr:72h_r3",
                    "decision_result": {
                        "decision_record": SimpleNamespace(
                            decision="NO_ACTION",
                            decision_id="dec:72h_r3",
                            decision_route="RULE_RESOLVED",
                            reason_codes=[],
                            short_rationale="",
                            selected_candidate_ref=None,
                        )
                    },
                    "adoption_result": {},
                }

        rt_stub = SimpleNamespace(
            state_root=paths.life_state_root,
            coordinator=_QuietCoordinator(),
            candidate_materializer=mat_stub,
            candidate_registry=SimpleNamespace(register_adapter=lambda _a: None),
            activity_read=SimpleNamespace(list_open_activities=lambda: []),
            canonical_state=lambda: {"foreground_activity_ref": None},
            kill_switches=SimpleNamespace(to_dict=lambda: {}),
            world_opportunity_source=None,
            world_action_adapter=None,
            artifact_action_adapter=None,
        )
        cycle = cycle_mod.AutonomousLifeCycle(
            rt_stub,
            enabled=True,
            max_rounds_per_day=10,
            world_home=str(paths.world_home),
            env={"CHIYO_AUTONOMOUS_LIFE_ENABLED": "true"},
        )
        cycle.materials = lambda *, now=None: {  # type: ignore[method-assign]
            "has_material": True,
            "foreground_activity": None,
            "future_items": {},
        }
        cycle.step_readiness = lambda *, act=True: SimpleNamespace(  # type: ignore[method-assign]
            available=True,
            bound=False,
            target_ref=None,
            activity_ref=None,
            reason_code=None,
            to_dict=lambda: {"available": True},
            fact_keys=lambda: {},
        )
        cycle.reconsideration_block = lambda **kw: {"blocked": False}  # type: ignore[method-assign]
        cycle.read_situation = lambda now: {"reason_code": None, "observed_at": now}  # type: ignore[method-assign]
        cycle.capacity_kwargs = lambda sit: {}  # type: ignore[method-assign]
        cycle._recent_settled_action_kinds = ["continue_note", "continue_note"]
        cycle.last_world_opportunity_view = [
            {
                "verb": "MOVE",
                "target_ref": "place:park",
                "reason_hint_cn": str(by_target["place:park"]["reason_hint_cn"]),
            }
        ]
        r3_eval = cycle.evaluate_once(
            trigger_ref="trig:72h_r3", observed_at=t_d1_1230, act=False
        )
        self.assertTrue(r3_eval["breathing_rhythm"]["stuffy"])
        self.assertEqual(r3_eval["breathing_rhythm"]["recent_note_streak"], 2)
        self.assertFalse(r3_eval["breathing_rhythm"]["outdoor_push_suppressed"])
        self.assertTrue(bool(r3_eval["breathing_rhythm"]["reason_hint_cn"]))
        self.assertEqual(recorded_rhythm[0]["recent_note_streak"], 2)

        # Deterministic loop selector breaks bedroom loop and selects outdoor move!
        sel_r3 = loop_bridge.select_deterministic_loop_action(
            {
                "candidates": [{"candidate_id": "cand:draft_r3"}, {"candidate_id": "cand:walk_r3"}],
                "descriptors": {
                    "cand:draft_r3": {
                        "candidate_id": "cand:draft_r3",
                        "action_family": "advance_workbench_draft",
                        "source_ref": "draft:r3",
                    },
                    "cand:walk_r3": {
                        "candidate_id": "cand:walk_r3",
                        "action_family": "strenuous_outing_walk",
                        "source_ref": "outing:park",
                    },
                },
                "gating": {"illness": {"phase_family": "healthy"}, "weather": "clear"},
            },
            recent_note_streak=2,
        )
        self.assertEqual(sel_r3["selected_candidate_id"], "cand:walk_r3")
        self.assertTrue(sel_r3["desk_stuffiness"]["stuffy"])

        # Settle arrival at outdoor park in world_state.json -> unified projection across v04/v05/v08 (0 writes!)
        t_d1_1330 = f"{DAY1}T13:30:00+08:00"
        ws_doc = json.loads(paths.world_state_json.read_text(encoding="utf-8"))
        ws_doc["location"] = {"place_id": "park", "area_id": "park"}
        ws_doc["revision"] = int(ws_doc.get("revision") or 1) + 1
        paths.world_state_json.write_text(
            json.dumps(ws_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        snap_before_loc = _snapshot_tree(inst_root)
        loc_from_root = ip.read_canonical_world_location_zero_write(inst_root)
        loc_from_wh = ip.read_canonical_world_location_zero_write(paths.world_home)
        self.assertEqual(loc_from_root, loc_from_wh)
        self.assertTrue(loc_from_root["found"])
        self.assertEqual(loc_from_root["world_place_id"], "park")
        self.assertEqual(loc_from_root["v04_place_id"], "riverside")
        self.assertEqual(loc_from_root["v05_spot_id"], "hillside_overlook")
        self.assertEqual(loc_from_root["v08_spot_id"], "open_yard")
        self.assertTrue(loc_from_root["is_outdoor"])

        v04_park = v04_nb.project_v04_neighborhood(inst_root, now_iso=t_d1_1330)
        self.assertEqual(v04_park["data"]["journey"]["current_place_id"], "riverside")
        v05_park = v05_town._extract_world_context_zero_write(inst_root, _iso_dt(t_d1_1330))
        self.assertEqual(v05_park["ta_location_id"], "hillside_overlook")
        v08_park = v08_mz.resolve_local_conditions_zero_write(inst_root, now_iso=t_d1_1330)
        self.assertEqual(v08_park["active_spot_id"], "open_yard")
        self.assertEqual(_snapshot_tree(inst_root), snap_before_loc)

        # =====================================================================
        # PHASE 3 — Hours 06-15 (Day 1 14:00 -> 23:00):
        # Autonomous Weather -> Rain Walk -> Damp Outerwear -> Neglect Strain -> Illness -> Care/Sleep
        # =====================================================================
        assert br is not None
        body_store = br.BodyStore(paths.body_dir)
        body_store.save_profile(br.build_profile())
        ln_prof = bln.load_profile(paths.body_dir)
        self.assertIsNotNone(ln_prof)
        ln_init = bln.initial_state(ln_prof)
        ln_init["last_live_at"] = t_d1_0800
        rt_d1 = br.initial_runtime(at=t_d1_0800, lived_needs=ln_init)
        t_d1_1330_b = f"{DAY1}T13:30:00+08:00"
        t_d1_1400 = f"{DAY1}T14:00:00+08:00"
        adv_d1 = br.advance(
            rt_d1,
            from_time=t_d1_1330_b,
            to_time=t_d1_1400,
            inputs=[
                {
                    "class": br.INPUT_CLASS_PHYSICAL,
                    "movement_duration_seconds": 5400.0,
                    "effort": "heavy",
                },
                {
                    "class": br.INPUT_CLASS_ENVIRONMENT,
                    "temperature_tendency": -0.65,
                },
            ],
        )
        rt_d1_before = rt_d1
        rt_d1 = adv_d1["state"]
        ln_d1, _ = bln.apply_advance(rt_d1["lived_needs"], hours=6.0)
        ln_d1["last_live_at"] = t_d1_1400
        rt_d1["lived_needs"] = ln_d1
        body_store.save_runtime(rt_d1)
        body_store.append_transition(
            br.build_transition(
                rt_d1_before,
                rt_d1,
                at=t_d1_1400,
                cause_class=adv_d1["cause_class"],
                cause_classes=adv_d1["cause_classes"],
                input_summary=adv_d1["inputs_summary"],
            )
        )
        self.assertGreaterEqual(rt_d1["somatic"]["fatigue"], 0.72)
        self.assertGreaterEqual(rt_d1["lived_needs"]["levels"]["thirst"], 0.70)

        # Advance 6-hour autonomous weather pulse into rainy slot (2026-04-02:S2 -> "drizzle")
        tick_d1_1400 = nyairo_day.run_v03_background_clock_tick(
            inst_root, now_iso=t_d1_1400, force_init=False
        )
        self.assertTrue(tick_d1_1400["ok"])
        _acc_llm(tick_d1_1400)
        wp_s2 = (tick_d1_1400.get("v04_neighborhood_receipt") or {}).get("weather_pulse") or {}
        self.assertIn(wp_s2.get("weather"), ("drizzle", "rain"))
        live_rain_weather = str(wp_s2["weather"])

        # Assert world_living_skeleton.observe_world reports live rain (source == v04_neighborhood_weather, 0 writes!)
        obs_env = {
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_PACKAGE": str(paths.world_package_dir),
        }
        snap_before_obs = _snapshot_tree(inst_root)
        obs_rain = skeleton.observe_world(
            hermes_home=paths.world_home,
            now=_iso_dt(t_d1_1400),
            environment=obs_env,
        )
        self.assertEqual(_snapshot_tree(inst_root), snap_before_obs)
        self.assertEqual(obs_rain["status"], "available", obs_rain)
        self.assertEqual(obs_rain["weather"]["condition"], live_rain_weather)
        self.assertEqual(obs_rain["weather"]["source"], "v04_neighborhood_weather")

        # Settle outdoor walk in rain (plus v04 rain outing so Day 1 rain walk lands in lived_memory_sink)
        t_d1_1415 = f"{DAY1}T14:15:00+08:00"
        exp_res = scv.record_outdoor_weather_exposure(
            inst_root,
            place_id="park",
            weather=live_rain_weather,
            duration_seconds=720.0,
            has_umbrella=False,
            operation_key="op_72h_rain_walk_d1",
            now_iso=t_d1_1415,
        )
        self.assertTrue(exp_res["ok"] and exp_res["exposed"], exp_res)
        _acc_llm(exp_res)
        self.assertIn(exp_res["dryness"], ("damp", "wet"))
        self.assertEqual(exp_res["dampness_source"], scv.VERIFIED_OUTDOOR_EXPOSURE_SOURCE)
        som_d1_wet, som_err = scv.read_state(inst_root)
        self.assertIsNone(som_err)
        self.assertEqual(som_d1_wet["garment"]["state"], "wear")
        self.assertIn(som_d1_wet["garment"]["dryness"], ("damp", "wet"))
        self.assertEqual(
            som_d1_wet["garment"]["dampness_source"],
            scv.VERIFIED_OUTDOOR_EXPOSURE_SOURCE,
        )

        st_jny, _, _ = v04_nb.apply_v04_neighborhood_command(
            inst_root,
            {
                "action": "start_journey",
                "target_place": "riverside",
                "operation_key": "op_72h_d1_rain_jny",
            },
            actor_side="subject",
            now_iso=t_d1_1415,
        )
        self.assertEqual(st_jny, "applied")

        # Return home with damp outerwear unchanged + elevated fatigue; advance through neglect strain ticks
        ws_home = json.loads(paths.world_state_json.read_text(encoding="utf-8"))
        ws_home["location"] = {"place_id": "home", "area_id": "bedroom"}
        ws_home["revision"] = int(ws_home.get("revision") or 1) + 1
        paths.world_state_json.write_text(
            json.dumps(ws_home, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        neglect_recorded_count = 0
        for tick_iso in (
            f"{DAY1}T15:15:00+08:00",
            f"{DAY1}T15:50:00+08:00",
            f"{DAY1}T16:25:00+08:00",
        ):
            t_neg = nyairo_day.run_v03_background_clock_tick(
                inst_root, now_iso=tick_iso, force_init=False
            )
            self.assertTrue(t_neg["ok"], t_neg)
            _acc_llm(t_neg)
            eco_r = t_neg.get("ecology_receipt") or {}
            if eco_r.get("neglect_strain_recorded"):
                neglect_recorded_count += 1
            eco_now, _ = v03_eco.read_stored_ecology_zero_write(inst_root)
            if eco_now["illness"]["phase"] == "prodrome":
                break
        self.assertGreaterEqual(neglect_recorded_count, 1)
        eco_prodrome, _ = v03_eco.read_stored_ecology_zero_write(inst_root)
        self.assertEqual(eco_prodrome["illness"]["phase"], "prodrome")
        self.assertEqual(eco_prodrome["illness"]["strain_events"][-1]["event_kind"], "caught_in_rain")

        # Assert adult_intimacy_view AND body_health_projection live-read elevated fatigue/illness zero-write!
        t_d1_1630 = f"{DAY1}T16:30:00+08:00"
        snap_before_live = _snapshot_tree(inst_root)
        ai_live = aiv.read_adult_intimacy(inst_root, now=t_d1_1630, subject_view=True)
        bh_live = bhp.body_health_view(paths.world_home, at=_iso_dt(t_d1_1630))
        self.assertEqual(_snapshot_tree(inst_root), snap_before_live)
        self.assertTrue(ai_live["body_and_desire"]["live_body_overlay"]["applied"])
        self.assertAlmostEqual(
            ai_live["body_and_desire"]["fatigue"],
            float(rt_d1["somatic"]["fatigue"]),
            places=3,
        )
        self.assertGreaterEqual(ai_live["body_and_desire"]["fatigue"], 0.65)
        self.assertEqual(
            ai_live["body_and_desire"]["live_body_overlay"]["v03_illness_phase"],
            "prodrome",
        )
        self.assertEqual(bh_live["illness_authority"], "v03_ecology")
        self.assertEqual(bh_live["writes_performed"], 0)

        # Assert loop bridge emits acute self-care candidates: hang_outerwear + drink_warm_water + REST(sleep_like)
        snap_before_col = _snapshot_tree(inst_root)
        collected_p3 = loop_bridge.collect_v02_v03_loop_candidates(inst_root, now_iso=t_d1_1630)
        self.assertEqual(_snapshot_tree(inst_root), snap_before_col)
        _acc_llm(collected_p3)
        care_emitted = set(collected_p3["gating"].get("acute_body_care_emitted") or [])
        self.assertIn("hang_outerwear", care_emitted)
        self.assertIn("drink_warm_water", care_emitted)
        self.assertIn("REST(sleep_like)", care_emitted)

        # Execute hang_outerwear + drink_warm_water via real daily loop consequence bridge
        descriptors_p3 = collected_p3["descriptors"]
        hang_desc = next(d for d in descriptors_p3.values() if d["action_family"] == "hang_outerwear")
        hang_ev = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected_p3, hang_desc["source_ref"], now_iso=t_d1_1630, decision_id="dec_72h_hang"
        )
        assert hang_ev is not None
        hang_settled = loop_bridge.settle_v02_v03_round_consequences(
            inst_root, hang_ev, now_iso=t_d1_1630
        )
        self.assertTrue(hang_settled["settled"], hang_settled)

        drink_desc = next(d for d in descriptors_p3.values() if d["action_family"] == "drink_warm_water")
        drink_ev = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected_p3, drink_desc["source_ref"], now_iso=t_d1_1630, decision_id="dec_72h_drink"
        )
        assert drink_ev is not None
        drink_settled = loop_bridge.settle_v02_v03_round_consequences(
            inst_root, drink_ev, now_iso=t_d1_1630
        )
        self.assertTrue(drink_settled["settled"], drink_settled)
        self.assertEqual(
            drink_settled["details"]["lived_needs_relief"]["authority"],
            "body_runtime",
        )
        self.assertFalse((inst_root / "body" / "lived_needs.json").exists())

        # Enable nocturnal_emission pack & execute REST(sleep_like) at Day 1 22:45 (+6h15m after hang!)
        t_d1_2245 = f"{DAY1}T22:45:00+08:00"
        v08_cb.configure_body_pack(
            inst_root,
            {
                "pack_id": v08_cb.PACK_NOCTURNAL,
                "enabled": True,
                "probability_permille": 200,
                "operation_key": "op_72h_enable_nocturnal",
            },
            actor_side="subject",
            now_iso=t_d1_2245,
        )
        rt_sleep_before = body_store.load_runtime()
        assert rt_sleep_before is not None
        adv_sleep = br.advance(
            rt_sleep_before,
            from_time=f"{DAY1}T22:40:00+08:00",
            to_time=t_d1_2245,
            inputs=[{"class": br.INPUT_CLASS_RECOVERY, "mode": "sleep_like", "state": "start"}],
        )
        rt_sleep = adv_sleep["state"]
        self.assertEqual(rt_sleep["recovery"]["state"], "active")
        self.assertEqual(rt_sleep["recovery"]["mode"], "sleep_like")
        body_store.save_runtime(rt_sleep)
        body_store.append_transition(
            br.build_transition(
                rt_sleep_before,
                rt_sleep,
                at=t_d1_2245,
                cause_class=adv_sleep["cause_class"],
                cause_classes=adv_sleep["cause_classes"],
                input_summary=adv_sleep["inputs_summary"],
            )
        )
        sleep_bind = v08_cb.auto_bind_world_sleep_if_enabled(
            paths.world_home,
            sleep_activity_id="world_sleep:72h_d1",
            started_at_iso=t_d1_2245,
            planned_wake_iso=f"{DAY2}T07:40:00+08:00",
        )
        self.assertTrue(sleep_bind["ok"] and sleep_bind["bound"], sleep_bind)
        cb_view = v08_cb.read_composable_body_zero_write(
            inst_root, has_subject_capability=True, now_iso=t_d1_2245
        )
        self.assertEqual(len(cb_view["sleep_activities"]), 1)
        self.assertEqual(cb_view["sleep_activities"][0]["sleep_activity_id"], "world_sleep:72h_d1")

        # Tick at Day 1 22:50 (+6h20m after 16:30 hang_outerwear) -> v03_clock dries coat to "dry" & marks asleep!
        t_d1_2250 = f"{DAY1}T22:50:00+08:00"
        tick_d1_sleep = nyairo_day.run_v03_background_clock_tick(
            inst_root, now_iso=t_d1_2250, force_init=False
        )
        self.assertTrue(tick_d1_sleep["ok"], tick_d1_sleep)
        _acc_llm(tick_d1_sleep)
        som_dried, _ = scv.read_state(inst_root)
        self.assertEqual(som_dried["garment"]["state"], "hang")
        self.assertEqual(som_dried["garment"]["dryness"], "dry")
        self.assertIsNone(som_dried["garment"]["drying"])

        # transitions.jsonl stays strictly <= MAX_TRANSITIONS and < 2 MB
        tr_list = body_store.read_transitions()
        self.assertLessEqual(len(tr_list), br.MAX_TRANSITIONS)
        if paths.body_transitions_jsonl.is_file():
            self.assertLess(paths.body_transitions_jsonl.stat().st_size, 2 * 1024 * 1024)

        # =====================================================================
        # PHASE 4 — Hours 15-24 (Day 1 23:00 -> Day 2 08:00):
        # Sleep Deferral, Quiet-Hours Gate, Early Wake Release & Wake-Only Morning Dream
        # =====================================================================
        t_d1_2340 = f"{DAY1}T23:40:00+08:00"
        ev_night = arv.apply_autonomy_rhythm_action(
            inst_root,
            {
                "action": "evaluate_turn",
                "user_text": "早点歇着，明早醒来再聊。",
                "urgent": False,
                "session_id": "nyairo-desktop",
            },
            subject_view=False,
            now_iso=t_d1_2340,
        )
        self.assertTrue(ev_night["ok"])
        self.assertEqual(ev_night["turn_decision"]["delivery_mode"], "DEFERRED_HOURS")
        self.assertEqual(ev_night["turn_decision"]["defer_reason"], "SLEEPING")
        night_msg_id = ev_night["turn_decision"]["msg_id"]

        # Quiet hours proactive outreach at 23:45 -> QUIET_HOURS_ACTIVE
        t_d1_2345 = f"{DAY1}T23:45:00+08:00"
        quiet_out = outreach_prod.evaluate_and_enqueue_lived_outreach(
            inst_root,
            trigger_kind="finished_craft_work",
            context_facts={"work_id": "quiet_test_box", "title": "夜半试做木盒", "completed": True},
            now_iso=t_d1_2345,
        )
        self.assertFalse(quiet_out["enqueued"])
        self.assertEqual(quiet_out["reason_code"], "QUIET_HOURS_ACTIVE")
        _acc_llm(quiet_out)

        # Pre-dawn message at Day 2 05:30 while still sleeping (deliver_after == 09:30 > 07:40!)
        t_d2_0530 = f"{DAY2}T05:30:00+08:00"
        ev_predawn = arv.apply_autonomy_rhythm_action(
            inst_root,
            {
                "action": "evaluate_turn",
                "user_text": "醒来记得看看窗台上的春芽。",
                "urgent": False,
                "session_id": "nyairo-desktop",
            },
            subject_view=False,
            now_iso=t_d2_0530,
        )
        self.assertTrue(ev_predawn["ok"])
        self.assertEqual(ev_predawn["turn_decision"]["defer_reason"], "SLEEPING")
        self.assertEqual(ev_predawn["turn_decision"]["deliver_after"], f"{DAY2}T09:30:00+08:00")
        predawn_msg_id = ev_predawn["turn_decision"]["msg_id"]

        # Complete sleep_like recovery at Day 2 07:40 (BEFORE fallback deliver_after 09:30!)
        t_d2_0740 = f"{DAY2}T07:40:00+08:00"
        rt_wake_before = body_store.load_runtime()
        assert rt_wake_before is not None
        adv_wake = br.advance(
            rt_wake_before,
            from_time=t_d1_2245,
            to_time=t_d2_0740,
            inputs=[{"class": br.INPUT_CLASS_RECOVERY, "mode": "sleep_like", "state": "stop"}],
        )
        rt_wake = adv_wake["state"]
        self.assertEqual(rt_wake["recovery"]["state"], "completed")
        body_store.save_runtime(rt_wake)
        body_store.append_transition(
            br.build_transition(
                rt_wake_before,
                rt_wake,
                at=t_d2_0740,
                cause_class=adv_wake["cause_class"],
                cause_classes=adv_wake["cause_classes"],
                input_summary=adv_wake["inputs_summary"],
            )
        )

        tick_d2_wake = nyairo_day.run_v03_background_clock_tick(
            inst_root, now_iso=t_d2_0740, force_init=False
        )
        self.assertTrue(tick_d2_wake["ok"], tick_d2_wake)
        _acc_llm(tick_d2_wake)

        # 1. autonomy_rhythm read live wake state & promoted deferred messages (including early release!)
        self.assertIn("autonomy_rhythm", tick_d2_wake["slices_ticked"])
        rhythm_rcpt = tick_d2_wake["autonomy_rhythm_receipt"]
        self.assertIn(night_msg_id, rhythm_rcpt["promoted_msg_ids"])
        self.assertIn(predawn_msg_id, rhythm_rcpt["early_released_msg_ids"])
        rhythm_after_wake, _ = arv.read_autonomy_rhythm_state(inst_root)
        by_mid = {m["msg_id"]: m for m in rhythm_after_wake["pending_messages"]}
        self.assertEqual(by_mid[night_msg_id]["status"], "READY_TO_REPLY")
        self.assertEqual(by_mid[predawn_msg_id]["status"], "READY_TO_REPLY")
        self.assertEqual(by_mid[predawn_msg_id]["promoted_reason"], "early_live_context_release")

        # 2. v05_inner_spark settled wake-only morning dream (phase == "just_woke")
        self.assertIn("v05_inner_spark", tick_d2_wake["slices_ticked"])
        spark_rcpt = tick_d2_wake["v05_inner_spark_receipt"]
        self.assertTrue(spark_rcpt["morning_dream_settled"])
        spark_disk = json.loads((inst_root / "life" / "v05_inner_spark.json").read_text(encoding="utf-8"))
        self.assertEqual(spark_disk["sleep_wake_state"]["phase"], "just_woke")
        self.assertEqual(spark_disk["sleep_wake_state"]["last_dream_roll_date"], DAY2)

        # 3. Verify subsequent daytime non-sleep rounds NEVER re-trigger settle_morning_dream_pulse!
        with mock.patch.object(
            v05_spark,
            "settle_morning_dream_pulse",
            wraps=v05_spark.settle_morning_dream_pulse,
        ) as spy_dream:
            for idx, ak in enumerate(("OUTING_VISIT", "COMMIT_WORK_VERSION", "V04_WATER_PLANT"), start=1):
                loop_bridge.settle_v02_v03_round_consequences(
                    inst_root,
                    {
                        "actor_side": "life_resident",
                        "effect_state": "APPLIED",
                        "action_kind": ak,
                        "receipt_ref": f"rcpt:72h_day2_awake_{idx}",
                        "operation_key": f"op_72h_day2_awake_{idx}",
                    },
                    now_iso=f"{DAY2}T09:{10 * idx:02d}:00+08:00",
                )
            self.assertEqual(spy_dream.call_count, 0)

        # =====================================================================
        # PHASE 5 — Hours 24-48 (Day 2 08:00 -> Day 3 08:00):
        # Full-Spectrum 2+2+1 Desires, Wallet/Rain Arbitration & Honest Pollable Outbox Delivery
        # =====================================================================
        t_d2_1430 = f"{DAY2}T14:30:00+08:00"
        rt_d2 = body_store.load_runtime()
        assert rt_d2 is not None
        ln_d2, _ = bln.apply_advance(rt_d2["lived_needs"], hours=7.0)
        ln_d2["last_live_at"] = t_d2_1430
        rt_d2["lived_needs"] = ln_d2
        body_store.save_runtime(rt_d2)
        self.assertGreaterEqual(rt_d2["lived_needs"]["levels"]["hunger"], 0.65)
        self.assertGreaterEqual(rt_d2["lived_needs"]["levels"]["thirst"], 0.65)

        v04_ep.apply_elastic_play_mutation(
            inst_root,
            {
                "action": "curate_personal_project",
                "project_op": "start",
                "project_id": "proj_72h_spring_zine",
                "purpose_cn": "春雨海堤速写小册",
                "medium_kind": "sketch_note",
                "next_step_cn": "补完书店屋檐水迹一页",
                "operation_key": "op_72h_proj_start",
            },
            actor_side="subject",
            now_iso=f"{DAY2}T10:30:00+08:00",
        )
        # Quiet partner gap: last_partner_message_at > 24h earlier
        rhythm_doc_d2 = json.loads((inst_root / "phone" / "autonomy_rhythm.json").read_text(encoding="utf-8"))
        rhythm_doc_d2["last_partner_message_at"] = f"{DAY1}T09:00:00+08:00"
        (inst_root / "phone" / "autonomy_rhythm.json").write_text(
            json.dumps(rhythm_doc_d2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        em_res = v05_spark.emerge_autonomous_desires(inst_root, now_iso=t_d2_1430)
        self.assertTrue(em_res["ok"])
        _acc_llm(em_res)
        self.assertLessEqual(em_res["active_counts"]["micro_craving"], 2)
        self.assertLessEqual(em_res["active_counts"]["mid_itch"], 2)
        self.assertLessEqual(em_res["active_counts"]["long_aspiration"], 1)
        spark_d2_view = v05_spark.project_v05_inner_spark_state(
            inst_root, has_subject_capability=True, now_iso=t_d2_1430
        )
        active_desires = [
            d for d in spark_d2_view["desires"]["items"] if d.get("status") == "active"
        ]
        self.assertGreaterEqual(len(active_desires), 2)
        for d in active_desires:
            self.assertNotEqual(d["category"], "intimate_warmth")

        # Record a paid impulse-spend desire & tighten discretionary wallet margin -> budget_tight arbitration!
        paid_rec = v05_spark.record_subject_desire(
            inst_root,
            layer="mid_itch",
            text="想去精品手冲馆点限定甜点配单品豆",
            category="appetite_craving",
            estimated_cost_jpy=1200,
            requires_outing=False,
            intensity=0.88,
            now_iso=f"{DAY2}T14:40:00+08:00",
            actor_side="subject",
        )
        paid_desire_id = paid_rec["desire"]["desire_id"]
        self.assertLessEqual(paid_rec["active_counts"]["micro_craving"], 2)
        self.assertLessEqual(paid_rec["active_counts"]["mid_itch"], 2)
        self.assertLessEqual(paid_rec["active_counts"]["long_aspiration"], 1)

        snap_before_arb = _snapshot_tree(inst_root)
        emitted_arb, _notes_arb = loop_bridge.arbitrate_desire_candidates_for_loop(
            inst_root,
            desires=None,
            economy_info={"discretionary_margin_cents": 500, "budget_tight": True},
            illness_info={"is_actively_ill": False, "phase": "healthy"},
            body_snapshot={"fatigue": 0.25},
            weather="clear",
            now_iso=f"{DAY2}T14:45:00+08:00",
        )
        self.assertEqual(_snapshot_tree(inst_root), snap_before_arb)
        paid_alt = next(
            (c for c in emitted_arb if c.get("desire_id") == paid_desire_id),
            None,
        )
        self.assertIsNotNone(paid_alt, emitted_arb)
        assert paid_alt is not None
        self.assertEqual(paid_alt["action_family"], "pursue_desire_alternative")
        self.assertEqual(paid_alt["alternative_kind"], "zero_cost_home")
        self.assertEqual(paid_alt["estimated_cost_jpy"], 0)
        self.assertEqual(paid_alt["friction_reason"], "budget_tight")
        self.assertEqual(paid_alt["frustration_debt"], 0)

        # Record arbitration friction -> friction_count increments with zero frustration debt
        fric_res = loop_bridge.record_desire_arbitration_friction(
            inst_root,
            desire_id=paid_desire_id,
            friction_reason="budget_tight",
            now_iso=f"{DAY2}T14:46:00+08:00",
        )
        self.assertTrue(fric_res["ok"])
        self.assertGreaterEqual(int(fric_res["friction_count"]), 1)
        self.assertEqual(fric_res.get("frustration_debt", 0), 0)

        # Fulfill a craft/tea desire -> status == "fulfilled" (sinks into canonical memory!)
        tea_rec = v05_spark.record_subject_desire(
            inst_root,
            layer="micro_craving",
            text="在书桌边温一壶春茶并折一只牛皮纸书签",
            category="craft_ambition",
            estimated_cost_jpy=0,
            intensity=0.90,
            shared=True,
            now_iso=f"{DAY2}T15:00:00+08:00",
            actor_side="subject",
        )
        tea_desire_id = tea_rec["desire"]["desire_id"]
        tea_ful = v05_spark.resolve_subject_desire(
            inst_root,
            desire_id=tea_desire_id,
            status="fulfilled",
            resolution_note="茶温正好，书签已经夹进画册",
            now_iso=f"{DAY2}T15:20:00+08:00",
            actor_side="subject",
        )
        self.assertEqual(tea_ful["desire"]["status"], "fulfilled")
        self.assertTrue(tea_ful["memory_sink"]["recorded"])

        # Verify honest_outbox proactive item in v03_ecology.json -> MUST be APPROVED_FOR_DELIVERY!
        eco_pre_poll, _ = v03_eco.read_stored_ecology_zero_write(inst_root)
        pending_pro_recs = [
            rec
            for rec in (eco_pre_poll.get("reality_bridge") or {}).get("outbound_records", [])
            if rec.get("status") == "APPROVED_FOR_DELIVERY"
        ]
        if not pending_pro_recs:
            out_enq = outreach_prod.evaluate_and_enqueue_lived_outreach(
                inst_root,
                trigger_kind="finished_craft_work",
                context_facts={
                    "work_id": "craft_box_72h_d2",
                    "title": "手工松木书签盒",
                    "completed": True,
                    "status": "finished",
                },
                now_iso=f"{DAY2}T15:30:00+08:00",
            )
            self.assertTrue(out_enq["enqueued"], out_enq)
            self.assertEqual(out_enq["outbound_status"], "APPROVED_FOR_DELIVERY")
            self.assertIsNone(out_enq["delivered_at"])
            eco_pre_poll, _ = v03_eco.read_stored_ecology_zero_write(inst_root)
            pending_pro_recs = [
                rec
                for rec in (eco_pre_poll.get("reality_bridge") or {}).get("outbound_records", [])
                if rec.get("status") == "APPROVED_FOR_DELIVERY"
            ]
        self.assertGreaterEqual(len(pending_pro_recs), 1)
        pro_rec = pending_pro_recs[0]
        pro_outbound_id = pro_rec["outbound_id"]
        self.assertEqual(pro_rec["status"], "APPROVED_FOR_DELIVERY")
        self.assertIsNone(pro_rec.get("delivered_at"))

        # Real loopback HTTP GET /api/phone/outbound/pending (0 disk writes!) & POST /api/phone/outbound/ack
        t_d2_1600 = f"{DAY2}T16:00:00+08:00"
        snap_before_http_poll = _snapshot_tree(inst_root)
        sidecars_before_http_poll = _sidecar_artifacts(inst_root)
        with _LoopbackPhoneHarness(
            inst_root, csrf_token="csrf-72h-p5", world_clock=lambda: _iso_dt(t_d2_1600)
        ) as harness:
            c_get, d_get = _http_json(harness.base_url + "/api/phone/outbound/pending")
            self.assertEqual(c_get, 200)
            self.assertTrue(d_get["ok"])
            self.assertTrue(d_get["zero_write"])
            _acc_llm(d_get)
            self.assertEqual(_snapshot_tree(inst_root), snap_before_http_poll)
            self.assertEqual(_sidecar_artifacts(inst_root), sidecars_before_http_poll)

            items = d_get["data"]["items"]
            pro_items = [it for it in items if it["item_kind"] == "proactive_outreach"]
            def_items = [it for it in items if it["item_kind"] == "deferred_reply_ready"]
            self.assertGreaterEqual(len(pro_items), 1)
            self.assertGreaterEqual(len(def_items), 1)
            self.assertTrue(
                any(
                    it["item_id"] == pro_outbound_id and it["status"] == "APPROVED_FOR_DELIVERY"
                    for it in pro_items
                )
            )
            self.assertTrue(
                any(
                    it["item_id"] == predawn_msg_id and it["status"] == "READY_TO_REPLY"
                    for it in def_items
                )
            )
            # Zero leaked private/internal transport fields
            serialized_poll = json.dumps(d_get, ensure_ascii=False)
            for forbidden_key in (
                "private_note",
                "subject_draft",
                "internal_token",
                "fence_id",
                "operation_key",
            ):
                self.assertNotIn(forbidden_key, serialized_poll)

            # POST /api/phone/outbound/ack (ack_delivered) + idempotent same-operation_key replay!
            ack_payload = {
                "action": "ack_delivered",
                "item_id": pro_outbound_id,
                "item_kind": "proactive_outreach",
                "operation_key": "op_72h_ack_pro_delivered_1",
                "transport_receipt_id": "tx_72h_desktop_1",
            }
            c_ack, d_ack = _http_json(
                harness.base_url + "/api/phone/outbound/ack",
                method="POST",
                headers={"X-CSRF-Token": "csrf-72h-p5"},
                body=ack_payload,
            )
            self.assertEqual(c_ack, 200, d_ack)
            self.assertTrue(d_ack["ok"])
            self.assertFalse(d_ack["already_applied"])
            self.assertEqual(d_ack["data"]["item_status"], "DELIVERED")
            _acc_llm(d_ack)

            c_rep, d_rep = _http_json(
                harness.base_url + "/api/phone/outbound/ack",
                method="POST",
                headers={"X-CSRF-Token": "csrf-72h-p5"},
                body=ack_payload,
            )
            self.assertEqual(c_rep, 200, d_rep)
            self.assertTrue(d_rep["already_applied"])
            self.assertEqual(d_rep["status"], "already_applied")

        eco_after_ack, _ = v03_eco.read_stored_ecology_zero_write(inst_root)
        acked_rec = next(
            rec
            for rec in eco_after_ack["reality_bridge"]["outbound_records"]
            if rec["outbound_id"] == pro_outbound_id
        )
        self.assertEqual(acked_rec["status"], "DELIVERED")
        self.assertIsNotNone(acked_rec["delivered_at"])

        # Second proactive outreach < 48h later -> rejected by OUTBOUND_INTERVAL_TOO_SOON!
        too_soon = outreach_prod.evaluate_and_enqueue_lived_outreach(
            inst_root,
            trigger_kind="rare_town_spectacle",
            context_facts={
                "spectacle_id": "spring_petal_wind",
                "title_cn": "河畔落瓣微风",
                "spot_id": "river_pier",
                "description_cn": "河畔一阵风卷过细白春瓣。",
            },
            now_iso=f"{DAY3}T07:35:00+08:00",
        )
        self.assertFalse(too_soon["enqueued"])
        self.assertEqual(too_soon["reason_code"], "OUTBOUND_INTERVAL_TOO_SOON")
        _acc_llm(too_soon)

        # Day 2 evening lived town & craft episodes (populating real episodes for Phase 6 sink + forget proof)
        notice_target_id = "notice_day2_cat_bell"
        notice_res = v05_town.post_bulletin_followup(
            inst_root,
            notice_id=notice_target_id,
            title_cn="河岸橘猫铜铃",
            followup_cn="在河埠石阶旁捡到橘猫小铜铃",
            now_iso=f"{DAY2}T17:10:00+08:00",
        )
        self.assertTrue(notice_res["memory_sink"]["recorded"])
        notice_mem_id = notice_res["memory_sink"]["memory_id"]

        favor_res = v05_town.log_neighbor_favor(
            inst_root,
            regular_id="auntie_lin",
            favor_kind="return_umbrella",
            note_cn="顺手把借来的晴雨伞送回面包铺",
            now_iso=f"{DAY2}T17:40:00+08:00",
        )
        self.assertTrue(favor_res["memory_sink"]["recorded"])
        kept_favor_mem_id = favor_res["memory_sink"]["memory_id"]

        pc_res = v05_craft.mint_parallel_postcard(
            inst_root,
            greeting_cn="春雨初霁，寄一张河埠手绘明信片",
            author_side="subject",
            now_iso=f"{DAY2}T18:20:00+08:00",
        )
        self.assertTrue(pc_res["memory_sink"]["recorded"])

        # Day 3 (Hours 48-72): advance autonomous weather pulse into rainy slot & prove wet-coat drying end-to-end
        tick_d3_0800 = nyairo_day.run_v03_background_clock_tick(
            inst_root, now_iso=f"{DAY3}T08:00:00+08:00", force_init=False
        )
        self.assertTrue(tick_d3_0800["ok"])
        _acc_llm(tick_d3_0800)
        wp_d3 = (tick_d3_0800.get("v04_neighborhood_receipt") or {}).get("weather_pulse") or {}
        self.assertIn(wp_d3.get("weather"), ("drizzle", "rain"))

        wet_exp = scv.record_outdoor_weather_exposure(
            inst_root,
            place_id="park",
            weather="rain",
            duration_seconds=720.0,
            has_umbrella=False,
            operation_key="op_72h_d3_heavy_rain",
            now_iso=f"{DAY3}T08:15:00+08:00",
        )
        self.assertEqual(wet_exp["dryness"], "wet")
        h_code, _ = scv.mutate_somatic_comfort_view(
            inst_root,
            {"action": "hang_outerwear", "garment_id": "daily_coat", "hang_position": "entryway_rack"},
            actor_side="subject",
            now_iso=f"{DAY3}T08:25:00+08:00",
        )
        self.assertEqual(h_code, 200)
        clk_mid_code, clk_mid = v03_clk.read_v03_clock_view(
            inst_root, now_iso=f"{DAY3}T09:25:00+08:00", has_subject_capability=True
        )
        self.assertEqual(clk_mid_code, 200)
        self.assertGreaterEqual(len(clk_mid["data"]["slow_variables"]["outerwear_drying"]), 1)
        tick_d3_dry = nyairo_day.run_v03_background_clock_tick(
            inst_root, now_iso=f"{DAY3}T14:35:00+08:00", force_init=False
        )
        self.assertTrue(tick_d3_dry["ok"])
        _acc_llm(tick_d3_dry)
        som_d3_dried, _ = scv.read_state(inst_root)
        self.assertEqual(som_d3_dried["garment"]["dryness"], "dry")

        # Advance cleanly to Hour 72 (Day 4 08:00)
        t_d4_0800 = f"{DAY4}T08:00:00+08:00"
        tick_d4 = nyairo_day.run_v03_background_clock_tick(
            inst_root, now_iso=t_d4_0800, force_init=False
        )
        self.assertTrue(tick_d4["ok"])
        _acc_llm(tick_d4)

        # =====================================================================
        # PHASE 6 — Hours 48-72 (Day 4 08:00) + +16d Probe:
        # 4-Store Memory Sink, Salience Budget, Ch. 131 Two-Stage Recall & Dual-Plane Anti-Resurrection
        # =====================================================================
        # 6A. 4-Store Memory Sink Proof
        m0_life_count = _sqlite_count(
            paths.memory_evidence_db, "evidence_events", "source_origin=?", (LIFE_ORIGIN,)
        )
        self.assertGreaterEqual(m0_life_count, 4, f"evidence.db life_outcomes={m0_life_count}")
        self.assertGreaterEqual(_sqlite_count(episode_db, "processed_evidence"), 4)
        self.assertGreaterEqual(_sqlite_count(episode_db, "episodes"), 1)

        s07_path = s07_affect.affect_store_path(inst_root / "life")
        self.assertTrue(s07_path.is_file())
        s07_doc = json.loads(s07_path.read_text(encoding="utf-8"))
        # S07 pulses carry 6h natural TTL so older Day 1-2 records expire cleanly from active `records`,
        # while committed operations confirm >= 4 lived episodes landed in life/s07_affect_records.json.
        self.assertGreaterEqual(len(s07_doc.get("records") or []), 1)
        self.assertGreaterEqual(len(s07_doc.get("operations") or {}), 4)

        rp_path = inst_root / "memory" / "v02_recall_projection_state.json"
        self.assertTrue(rp_path.is_file())
        rp_doc = json.loads(rp_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(rp_doc.get("memories") or {}), 4)
        notice_rp_rec = rp_doc["memories"][notice_mem_id]
        self.assertTrue(bool(notice_rp_rec.get("episode_key")))
        self.assertIn(notice_target_id, json.dumps(notice_rp_rec.get("source_refs"), ensure_ascii=False))

        canon_db = reconciler.canonical_ledger_path(inst_root)
        self.assertTrue(canon_db.is_file())
        self.assertGreaterEqual(_sqlite_count(canon_db, "live_cycle_settlements"), 4)

        # 6B. Salience & Budget Proof (<= 1800 chars, Tier-0 promoted sensory_line & v05_town_spark_line)
        rt_d4_before = body_store.load_runtime()
        assert rt_d4_before is not None
        adv_d4 = br.advance(
            rt_d4_before,
            from_time=f"{DAY4}T07:00:00+08:00",
            to_time=t_d4_0800,
            inputs=[{"class": br.INPUT_CLASS_ENVIRONMENT, "temperature_tendency": -0.75}],
        )
        rt_d4 = adv_d4["state"]
        body_store.save_runtime(rt_d4)
        body_store.append_transition(
            br.build_transition(
                rt_d4_before,
                rt_d4,
                at=t_d4_0800,
                cause_class=adv_d4["cause_class"],
                cause_classes=adv_d4["cause_classes"],
                input_summary=adv_d4["inputs_summary"],
            )
        )

        cfg = {"day_root": str(inst_root), "phone": {"enabled": False, "private_artifacts": True}}
        salience_query = "你今天冷不冷，身体还好吗？镇上天气怎么样？"
        snap_before_sal = _snapshot_tree(inst_root)
        sal_diag = lcb.measure_context_budget_breakdown(
            inst_root,
            cfg=cfg,
            incoming_text=salience_query,
            ephemeral_hint="背" * 800,
            now_iso=t_d4_0800,
        )
        self.assertEqual(_snapshot_tree(inst_root), snap_before_sal)
        self.assertLessEqual(len(sal_diag["rendered_text"]), lcb.MAX_LIVED_CONTEXT_CHARS)
        self.assertTrue(sal_diag["within_budget"])
        self.assertLessEqual(sal_diag["per_section_chars"].get("ephemeral_hint", 0), 450)
        self.assertIn("sensory_line", sal_diag["kept_section_names"])
        self.assertIn("v05_town_spark_line", sal_diag["kept_section_names"])

        # 6C. Ch. 131 14-Day Two-Stage Recall Proof (+16 days -> Day 20)
        t_day20 = f"{DAY20}T10:00:00+08:00"
        snap_before_d20 = _snapshot_tree(inst_root)

        # 1) Spontaneous turn (consulted_archive=False) in v02_recall_projection
        rp_d20 = v02_rp.read_v02_recall_projection_view(
            inst_root, now_iso=t_day20, has_subject_capability=True
        )
        by_rp_id = {it["memory_id"]: it for it in rp_d20["direct_recall_items"]}
        self.assertIn(notice_mem_id, by_rp_id)
        self.assertIn(kept_favor_mem_id, by_rp_id)
        for mid in (notice_mem_id, kept_favor_mem_id):
            self.assertIn(by_rp_id[mid]["clarity"], (v02_rp.CLARITY_RANGE, v02_rp.CLARITY_GIST))
            self.assertIsNone(by_rp_id[mid]["exact_detail"])
            self.assertFalse(by_rp_id[mid]["is_verbatim_quote"])
            self.assertNotIn("T17:", by_rp_id[mid]["projected_text"])

        # Spontaneous turn in lived_context_bridge renders real fuzzy cues
        lcb_d20 = lcb.render_lived_companion_context(
            inst_root,
            cfg=cfg,
            incoming_text="还记得前阵子在镇上发生的事吗？",
            ephemeral_hint="",
            now_iso=t_day20,
        )
        self.assertIn("久远日常记忆（大概印象，不伪造逐字原话）：", lcb_d20)
        self.assertEqual(_snapshot_tree(inst_root), snap_before_d20)

        # Spontaneous vs archive-checked turn in ProductionNativeMemoryResolver._consume over REAL evidence.db rows!
        m0_conn = sqlite3.connect(str(paths.memory_evidence_db))
        m0_conn.row_factory = sqlite3.Row
        try:
            raw_m0_rows = [
                dict(row_item)
                for row_item in m0_conn.execute(
                    "SELECT * FROM evidence_events WHERE source_origin=? ORDER BY occurred_at, event_id",
                    (LIFE_ORIGIN,),
                ).fetchall()
            ]
        finally:
            m0_conn.close()
        m0_events: list[dict[str, Any]] = []
        for row_item in raw_m0_rows:
            row_copy = dict(row_item)
            row_copy["source_refs"] = json.loads(row_copy.pop("source_refs_json") or "[]")
            m0_events.append(row_copy)

        notice_m0_ev = next(
            ev for ev in m0_events if notice_target_id in json.dumps(ev["source_refs"], ensure_ascii=False)
        )
        kept_m0_ev = next(
            ev for ev in m0_events if kept_favor_mem_id in json.dumps(ev["source_refs"], ensure_ascii=False)
        )
        trigger_ev = {
            "event_id": "evt_72h_d20_trigger",
            "speaker": "user",
            "occurred_at": t_day20,
            "conversation_id": "life:chiyo",
            "turn_id": "turn-d20-now",
            "primary_source_ref_id": "src:d20_trigger",
            "content": "还记得四月初在镇上发生的事吗？",
        }
        all_resolver_events = [notice_m0_ev, kept_m0_ev, trigger_ev]

        def _run_native_resolver(*, consulted_archive: bool) -> list[Any]:
            resolver = prod_res.ProductionNativeMemoryResolver.__new__(
                prod_res.ProductionNativeMemoryResolver
            )
            resolver.max_memories = 8
            resolver.environment = {}
            resolver.stats = prod_res.ResolverStats()
            resolver._evaluator = SimpleNamespace(
                evaluate_record=lambda _rec: SimpleNamespace(decision="CONSUME")
            )
            reader = SimpleNamespace(
                events=lambda: list(all_resolver_events),
                build_trigger=lambda ev, all_ev: SimpleNamespace(
                    visible_event_ids=("evt_72h_d20_trigger",)
                ),
                m1_data=lambda: ([], {}),
            )
            resolver._adapter = SimpleNamespace(reader=reader)
            got = {
                "availability": {
                    "selected_refs": ["cand_notice", "cand_favor"],
                    "surface_payload": {},
                },
                "candidates": [
                    {
                        "candidate_id": "cand_notice",
                        "episode_id": "ep_notice",
                        "source_event_ids": [notice_m0_ev["event_id"]],
                    },
                    {
                        "candidate_id": "cand_favor",
                        "episode_id": "ep_favor",
                        "source_event_ids": [kept_m0_ev["event_id"]],
                    },
                ],
            }
            return resolver._consume(
                got,
                {"event_id": "evt_72h_d20_trigger"},
                {"ok": True},
                {
                    "user_id": "chiyo",
                    "session_id": "sess-72h",
                    "conversation_id": "life:chiyo",
                    "turn_id": "turn-d20-now",
                },
                consulted_archive=consulted_archive,
                now_iso=t_day20,
                day_root=inst_root,
            )

        spont_objs = _run_native_resolver(consulted_archive=False)
        self.assertEqual(len(spont_objs), 2)
        for obj in spont_objs:
            self.assertIn(obj.uncertainty, ("RANGE_IMPRESSION", "GIST_IMPRESSION"))
            self.assertTrue(("大概记得" in obj.text) or ("隐约记得" in obj.text), obj.text)
            self.assertNotIn("T17:", obj.text)

        # 2) Verified archive turn (consulted_archive=True): restores exact detail + ATTRIBUTION_ARCHIVE_CHECKED
        arch_objs = _run_native_resolver(consulted_archive=True)
        self.assertEqual(len(arch_objs), 2)
        for obj in arch_objs:
            self.assertEqual(obj.uncertainty, v02_rp.ATTRIBUTION_ARCHIVE_CHECKED)
        self.assertTrue(any("河岸橘猫铜铃" in o.text for o in arch_objs))

        rp_arch_item = v02_rp.project_memory_item(
            rp_doc["memories"][notice_mem_id],
            now_iso=t_day20,
            consulted_archive=True,
        )
        self.assertEqual(rp_arch_item["clarity"], v02_rp.CLARITY_EXACT)
        self.assertEqual(rp_arch_item["attribution_mode"], v02_rp.ATTRIBUTION_ARCHIVE_CHECKED)
        self.assertIn("河岸橘猫铜铃", rp_arch_item["exact_detail"])

        # 6D. Dual-Plane Anti-Resurrection Forget Proof (including FULL JSON sidecar rollback!)
        pre_forget_json_snap = _snapshot_json_sidecars((inst_root, ctrl_root))

        fg_status, fg_resp = v02_rlf.execute_explicit_forget(
            inst_root,
            source_ids=[notice_target_id],
            operation_id="op_72h_forget_notice_cat_bell",
            control_root=ctrl_root,
            now_iso=t_day20,
        )
        self.assertEqual(fg_status, 200, fg_resp)
        self.assertTrue(fg_resp.get("native_authority_mirrored"), fg_resp)

        def _assert_forgotten_everywhere(stage_label: str) -> None:
            snap_chk = _snapshot_tree(inst_root)
            # 1. v05_living_town_view
            c_tv, tv_doc = v05_town_view.read_v05_living_town_view(
                inst_root, has_subject_capability=True, now_iso=t_day20
            )
            self.assertEqual(c_tv, 200, stage_label)
            self.assertNotIn(notice_target_id, json.dumps(tv_doc, ensure_ascii=False), stage_label)
            self.assertNotIn("河岸橘猫铜铃", json.dumps(tv_doc, ensure_ascii=False), stage_label)

            # 2. v02_recall_projection (subject + partner)
            rp_sub = v02_rp.read_v02_recall_projection_view(
                inst_root, now_iso=t_day20, has_subject_capability=True
            )
            self.assertNotIn(notice_mem_id, rp_sub["proactive_surfaceable_ids"], stage_label)
            self.assertNotIn("河岸橘猫铜铃", json.dumps(rp_sub, ensure_ascii=False), stage_label)
            rp_par = v02_rp.read_v02_recall_projection_view(
                inst_root, now_iso=t_day20, has_subject_capability=False
            )
            self.assertNotIn("河岸橘猫铜铃", json.dumps(rp_par, ensure_ascii=False), stage_label)
            self.assertIn(kept_favor_mem_id, rp_sub["proactive_surfaceable_ids"], stage_label)

            # 3. lived_context_bridge (full render AND _format_compact_current_situation)
            lcb_out = lcb.render_lived_companion_context(
                inst_root,
                cfg=cfg,
                incoming_text="镇上布告栏和橘猫铜铃怎么样",
                ephemeral_hint="",
                now_iso=t_day20,
            )
            self.assertNotIn("河岸橘猫铜铃", lcb_out, stage_label)
            self.assertNotIn("小铜铃", lcb_out, stage_label)
            compact_out = lcb._format_compact_current_situation(
                day_root=inst_root, include_private=True, now_iso=t_day20
            )
            self.assertNotIn("河岸橘猫铜铃", compact_out, stage_label)
            self.assertNotIn("小铜铃", compact_out, stage_label)

            # 4. Native controls.db / authority.db + ProductionNativeMemoryResolver
            auth_proj = MemoryDeletionAuthority(
                paths.memory_authority_db, owner="chiyo", conversation="life:chiyo"
            ).projection()
            self.assertTrue(auth_proj.healthy, stage_label)
            self.assertIn(notice_target_id, auth_proj.blocked_source_refs, stage_label)
            ctrl_proj = MemoryControlLedger(
                paths.memory_controls_db,
                paths.memory_evidence_db,
                owner="chiyo",
                conversation="life:chiyo",
                authority=str(paths.memory_authority_db),
                authority_min_seq=0,
            ).projection()
            self.assertTrue(ctrl_proj.healthy, stage_label)
            self.assertIn(notice_target_id, ctrl_proj.blocked_source_refs, stage_label)

            port = LifeExperiencePort(
                paths.memory_evidence_db,
                controls_db=paths.memory_controls_db,
                authority_db=paths.memory_authority_db,
                authority_min_seq=0,
                subject="chiyo",
            )
            port_recs = port.recent(limit=20)
            self.assertFalse(
                any("河岸橘猫铜铃" in rec.text for rec in port_recs),
                stage_label,
            )

            res_objs = _run_native_resolver(consulted_archive=True)
            self.assertFalse(
                any("河岸橘猫铜铃" in o.text for o in res_objs),
                stage_label,
            )
            self.assertTrue(
                any("林姨" in o.text or "晴雨伞" in o.text for o in res_objs),
                stage_label,
            )
            self.assertEqual(_snapshot_tree(inst_root), snap_chk, stage_label)

        _assert_forgotten_everywhere("immediately_after_forget")

        # Roll back ALL JSON sidecar files under BOTH inst_root and ctrl_root to pre-forget snapshot!
        _restore_json_sidecars((inst_root, ctrl_root), pre_forget_json_snap)

        barrier_rb = v02_rlf.load_unified_forget_barrier_readonly(
            inst_root, control_root=ctrl_root
        )
        self.assertFalse(barrier_rb["fail_closed"])
        self.assertTrue(barrier_rb["barrier_active"])
        self.assertIn(notice_target_id, barrier_rb["blocked_ids"])

        _assert_forgotten_everywhere("after_full_json_sidecar_rollback")

        # =====================================================================
        # PHASE 7 — Final Global Invariant Seal Check
        # =====================================================================
        manifest_doc = json.loads((REPO_ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
        manifest_files = manifest_doc["files"]
        for rel in SEALED_AND_FROZEN_REL_PATHS:
            self.assertIn(rel, manifest_files, f"Missing from MANIFEST.json: {rel}")
            actual_sha = hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest()
            self.assertEqual(
                actual_sha,
                manifest_files[rel],
                f"Sealed/frozen SHA-256 mismatch on {rel}",
            )

        plugin_mod = _load_plugin_module()
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(len(plugin_mod._PHONE_AND_WORLD_GUIDANCE), 1199)
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)
        self.assertEqual(len(soak_runner.GET_ROUTES), 24)
        self.assertEqual(total_llm_calls, 0)

    def test_02_test_file_hygiene_lf_no_bom_no_emoji_neutral_ta(self) -> None:
        raw = Path(__file__).resolve().read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM forbidden")
        self.assertNotIn(b"\r", raw, "CR/CRLF forbidden")
        text = raw.decode("utf-8")
        so_chars = [ch for ch in text if unicodedata.category(ch) == "So"]
        self.assertEqual(so_chars, [], f"unicode.So characters found: {so_chars[:5]}")


if __name__ == "__main__":
    unittest.main()

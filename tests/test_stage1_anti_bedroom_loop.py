#!/usr/bin/env python3
"""Stage 1 Anti-Bedroom-Loop, Life Reasons, Timeout Soft-Landing & Path Alignment Tests.

Covers:
1. candidate_sources_ag0: recent_note_streak == 0 stays bit-identical; recent_note_streak >= 2
   promotes outdoor SOURCE_WORLD_OPPORTUNITY (place:) ahead of CONTINUE_CURRENT (unless
   is_final_closeout_step=True).
2. world_move_opportunity_source: enrich_move_opportunities_with_life_reasons emits real
   reason_hint_cn cues from v04_neighborhood, v05_living_town, v03_economy and
   build_outing_reason, 100% zero-write, preserving exact opportunity_id & target_refs,
   and suppressing outdoor cues during rain/storm.
3. autonomous_life_cycle & autonomous_life_resident: tracks recent_note_streak and
   is_final_closeout_step, feeds CandidateMaterializer, surfaces compute_desk_stuffiness
   + reason_hint_cn (suppressing outdoor push when weather in ("rain", "storm")), wires
   handle_round_timeout_soft_landing cleanly on cognition transport timeout, and resolves
   bootstrapped managed roots via resolve_instance_paths.
4. v02_v03_daily_loop_bridge: resolves one-day/world-home/state/world_property_economy.json
   and v02/canonical_life.sqlite3 via resolve_instance_paths (zero-write), and only calls
   settle_morning_dream_pulse on SLEEP / MORNING_WAKE / woke_from_sleep=True (never on
   non-sleep rounds even when life/v05_inner_spark.json exists).
"""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(LIFE_SCRIPTS), str(WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import autonomous_life_cycle as cycle_mod
import autonomous_life_resident as resident_mod
import candidate_sources_ag0 as ag0
import v02_v03_daily_loop_bridge as bridge
import world_move_opportunity_source as wmos

OBSERVED = "2026-11-20T14:30:00+08:00"


def _make_record(
    *,
    candidate_id: str,
    source_kind: str,
    candidate_kind: str,
    source_ref: str,
    targets: list[str],
    activity_ref: str | None = None,
    user_event_ref: str | None = None,
    commitment_ref: str | None = None,
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
        user_event_ref=user_event_ref,
        commitment_ref=commitment_ref,
        perception_ref="percept:stage1:r1" if is_world else None,
        world_revision_ref="wrev:stage1:r1" if is_world else None,
        world_opportunity_ref=source_ref if is_world else None,
        target_refs=list(targets),
        causal_parent_refs=[source_ref],
        provenance_refs=[source_ref, candidate_id],
        provenance_chain=[{"step": "candidate", "ref": candidate_id}],
        availability_status=ag0.CANDIDATE_STATUS_OPEN,
        created_at=OBSERVED,
        observed_at=OBSERVED,
        recorded_at=OBSERVED,
        idempotency_key=f"idem_stage1:{candidate_id}",
    )


class _StaticAdapter(ag0.CandidateSourceAdapter):
    def __init__(self, source_kind: str, records: list[ag0.CandidateRecord]) -> None:
        super().__init__()
        self.source_kind = source_kind
        self._records = list(records)

    def is_enabled(self) -> bool:
        return True

    def availability_status(self) -> dict[str, str]:
        return {"source_kind": self.source_kind, "status": ag0.ADAPTER_STATUS_READY}

    def materialize(self, *, observed_at: str):
        rows = [copy.deepcopy(r) for r in self._records]
        return rows, f"snap:{self.source_kind}:r1", {
            "discovered": len(rows),
            "emitted": len(rows),
            "rejected": 0,
        }

    def refresh(self, candidate: ag0.CandidateRecord, *, observed_at: str) -> ag0.CandidateRecord:
        return copy.deepcopy(candidate)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


class TestCandidateSourcesAntiBedroomLoop(unittest.TestCase):
    def _build_materializer(
        self,
        *,
        global_cap: int = 16,
    ) -> tuple[ag0.CandidateMaterializer, list[ag0.CandidateRecord]]:
        curr = [
            _make_record(
                candidate_id="cand:a00",
                source_kind=ag0.SOURCE_CURRENT_ACTIVITY,
                candidate_kind=ag0.CANDIDATE_KIND_CONTINUE_CURRENT,
                source_ref="act:note_writing_1",
                targets=["artifact:bedroom_note_1"],
                activity_ref="act:note_writing_1",
            )
        ]
        personal = [
            _make_record(
                candidate_id="cand:p01",
                source_kind=ag0.SOURCE_PERSONAL_OPPORTUNITY,
                candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
                source_ref="artifact:bedroom_note_2",
                targets=["artifact:bedroom_note_2"],
            ),
            _make_record(
                candidate_id="cand:p02",
                source_kind=ag0.SOURCE_PERSONAL_OPPORTUNITY,
                candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
                source_ref="fitem:v02v03-street_notice",
                targets=["v04_street_notice:n1"],
            ),
        ]
        world = [
            _make_record(
                candidate_id="cand:w00",
                source_kind=ag0.SOURCE_WORLD_OPPORTUNITY,
                candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
                source_ref="wopp:stay_home_daze",
                targets=["stay:home:DAZE:180"],
            ),
            _make_record(
                candidate_id="cand:w01",
                source_kind=ag0.SOURCE_WORLD_OPPORTUNITY,
                candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
                source_ref="wopp:pose_seated",
                targets=["bodyact:pose:seated:south"],
            ),
            _make_record(
                candidate_id="cand:w99",
                source_kind=ag0.SOURCE_WORLD_OPPORTUNITY,
                candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
                source_ref="wopp:move_street_1",
                targets=["place:street_1"],
            ),
        ]
        adapters = {
            ag0.SOURCE_CURRENT_ACTIVITY: _StaticAdapter(ag0.SOURCE_CURRENT_ACTIVITY, curr),
            ag0.SOURCE_PERSONAL_OPPORTUNITY: _StaticAdapter(ag0.SOURCE_PERSONAL_OPPORTUNITY, personal),
            ag0.SOURCE_WORLD_OPPORTUNITY: _StaticAdapter(ag0.SOURCE_WORLD_OPPORTUNITY, world),
        }
        cap_tok = ag0.CandidateProjectionGuard.issue_capability(caller_module="ag0_test_harness")
        mat = ag0.CandidateMaterializer(
            ag0.CandidateSourceRegistry(adapters),
            capability=cap_tok,
            global_cap=global_cap,
            record_audit=False,
        )
        return mat, curr + personal + world

    def test_streak_zero_is_bit_identical_to_baseline(self) -> None:
        mat, all_recs = self._build_materializer(global_cap=16)
        default_set = mat.build_candidate_set(observed_at=OBSERVED)
        zero_set = mat.build_candidate_set(
            observed_at=OBSERVED,
            recent_note_streak=0,
            is_final_closeout_step=False,
        )
        one_set = mat.build_candidate_set(
            observed_at=OBSERVED,
            recent_note_streak=1,
            is_final_closeout_step=False,
        )
        self.assertEqual(default_set.candidate_refs, zero_set.candidate_refs)
        self.assertEqual(default_set.candidate_set_id, zero_set.candidate_set_id)
        self.assertEqual(default_set.candidate_refs, one_set.candidate_refs)
        # Under default sorting, cand:a00 (CONTINUE_CURRENT) sorts first by candidate_id
        self.assertEqual(default_set.candidate_refs[0], "cand:a00")

        # Tight cap = 2 with streak=0 keeps CURRENT_ACTIVITY first in round-robin
        tight_default = ag0.CandidateMaterializer.bounded_candidates_by_source(all_recs, 2)
        tight_zero = ag0.CandidateMaterializer.bounded_candidates_by_source(
            all_recs, 2, recent_note_streak=0, is_final_closeout_step=False
        )
        self.assertEqual([c.candidate_id for c in tight_default], [c.candidate_id for c in tight_zero])
        self.assertIn("cand:a00", [c.candidate_id for c in tight_default])

    def test_streak_two_promotes_outdoor_world_move_ahead_of_continue_current(self) -> None:
        mat, all_recs = self._build_materializer(global_cap=16)
        shifted = mat.build_candidate_set(
            observed_at=OBSERVED,
            recent_note_streak=2,
            is_final_closeout_step=False,
        )
        # place:street_1 (cand:w99) must sit ahead of CONTINUE_CURRENT (cand:a00)
        self.assertEqual(shifted.candidate_refs[0], "cand:w99")
        self.assertLess(
            shifted.candidate_refs.index("cand:w99"),
            shifted.candidate_refs.index("cand:a00"),
        )
        # Non-artifact personal cue (cand:p02) precedes artifact note continuation (cand:p01)
        self.assertLess(
            shifted.candidate_refs.index("cand:p02"),
            shifted.candidate_refs.index("cand:p01"),
        )

        # Tight cap = 2: round 0 gives WORLD_OPPORTUNITY (place:street_1) & PERSONAL_OPPORTUNITY
        # (non-note) seats ahead of CONTINUE_CURRENT!
        tight_shifted = ag0.CandidateMaterializer.bounded_candidates_by_source(
            all_recs, 2, recent_note_streak=2, is_final_closeout_step=False
        )
        tight_ids = [c.candidate_id for c in tight_shifted]
        self.assertEqual(tight_ids, ["cand:w99", "cand:p02"])
        self.assertNotIn("cand:a00", tight_ids)

    def test_final_closeout_step_exempts_from_anti_loop_demotion(self) -> None:
        mat, all_recs = self._build_materializer(global_cap=16)
        baseline = mat.build_candidate_set(observed_at=OBSERVED, recent_note_streak=0)
        closeout = mat.build_candidate_set(
            observed_at=OBSERVED,
            recent_note_streak=4,
            is_final_closeout_step=True,
        )
        self.assertEqual(closeout.candidate_refs, baseline.candidate_refs)
        self.assertEqual(closeout.candidate_set_id, baseline.candidate_set_id)

        tight_closeout = ag0.CandidateMaterializer.bounded_candidates_by_source(
            all_recs, 2, recent_note_streak=4, is_final_closeout_step=True
        )
        self.assertIn("cand:a00", [c.candidate_id for c in tight_closeout])

    def test_round_scoped_materializer_context_binding(self) -> None:
        mat, _ = self._build_materializer(global_cap=16)
        mat.set_breathing_rhythm_context(recent_note_streak=3, is_final_closeout_step=False)
        cset = mat.build_candidate_set(observed_at=OBSERVED)
        self.assertEqual(cset.candidate_refs[0], "cand:w99")
        mat.set_breathing_rhythm_context(recent_note_streak=0, is_final_closeout_step=False)
        reset_cset = mat.build_candidate_set(observed_at=OBSERVED)
        self.assertEqual(reset_cset.candidate_refs[0], "cand:a00")


class TestWorldMoveOpportunityLifeReasons(unittest.TestCase):
    def _sample_moves(self) -> list[dict]:
        return [
            {
                "opportunity_id": "wopp:move:home:r3:convenience_store",
                "perception_ref": "percept:gateway-snapshot:home:r3",
                "world_revision_ref": "wrev:coastal:r3",
                "target_refs": ["place:convenience_store"],
                "valid_from": OBSERVED,
                "valid_until": "2026-11-20T14:35:00+08:00",
                "occurred_at": OBSERVED,
                "observed_at": OBSERVED,
                "observed": True,
                "status": "AVAILABLE",
                "revision": 1,
                "subject_id": ag0.GLOBAL_SUBJECT_ID,
                "location_ref": "place:home",
            },
            {
                "opportunity_id": "wopp:move:home:r3:river_pier",
                "perception_ref": "percept:gateway-snapshot:home:r3",
                "world_revision_ref": "wrev:coastal:r3",
                "target_refs": ["place:river_pier"],
                "valid_from": OBSERVED,
                "valid_until": "2026-11-20T14:35:00+08:00",
                "occurred_at": OBSERVED,
                "observed_at": OBSERVED,
                "observed": True,
                "status": "AVAILABLE",
                "revision": 1,
                "subject_id": ag0.GLOBAL_SUBJECT_ID,
                "location_ref": "place:home",
            },
            {
                "opportunity_id": "wopp:move:home:r3:bookstore",
                "perception_ref": "percept:gateway-snapshot:home:r3",
                "world_revision_ref": "wrev:coastal:r3",
                "target_refs": ["place:bookstore"],
                "valid_from": OBSERVED,
                "valid_until": "2026-11-20T14:35:00+08:00",
                "occurred_at": OBSERVED,
                "observed_at": OBSERVED,
                "observed": True,
                "status": "AVAILABLE",
                "revision": 1,
                "subject_id": ag0.GLOBAL_SUBJECT_ID,
                "location_ref": "place:home",
            },
        ]

    def test_enrich_emits_real_reason_hints_zero_write_and_preserves_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "world").mkdir(parents=True)
            (root / "one-day" / "world-home" / "state").mkdir(parents=True)
            (root / "world" / "v04_neighborhood_state.json").write_text(
                json.dumps({"environment": {"weather": "clear", "airflow": "medium"}}),
                encoding="utf-8",
            )
            (root / "world" / "v05_living_town.json").write_text(
                json.dumps({"theme": "coastal", "low_tide_open": True, "revision": 1}),
                encoding="utf-8",
            )
            (root / "one-day" / "world-home" / "state" / "world_property_economy.json").write_text(
                json.dumps({"available_balance_cents": 120000, "supplies": {"milk": 0}}),
                encoding="utf-8",
            )
            before = _snapshot_tree(root)
            raw_moves = self._sample_moves()
            orig_snapshot = copy.deepcopy(raw_moves)

            enriched = wmos.enrich_move_opportunities_with_life_reasons(
                raw_moves,
                day_root=root,
                observation={"location": {"place_id": "home"}},
                now_iso="2026-11-20T15:00:00+08:00",
            )
            after = _snapshot_tree(root)
            self.assertEqual(before, after)
            # Original caller dicts untouched
            self.assertEqual(raw_moves, orig_snapshot)

            by_target = {row["target_refs"][0]: row for row in enriched}
            for orig in orig_snapshot:
                t = orig["target_refs"][0]
                self.assertEqual(by_target[t]["opportunity_id"], orig["opportunity_id"])
                self.assertEqual(by_target[t]["target_refs"], orig["target_refs"])

            self.assertIn("牛奶", by_target["place:convenience_store"]["reason_hint_cn"])
            self.assertIn("退潮", by_target["place:river_pier"]["reason_hint_cn"])
            self.assertTrue(bool(by_target["place:bookstore"]["reason_hint_cn"]))

            # Flows cleanly into opportunity_view & _context_opportunity description
            hints = {row["opportunity_id"]: row["reason_hint_cn"] for row in enriched if row.get("reason_hint_cn")}
            view = wmos.opportunity_view(raw_moves, reason_hints=hints)
            pier_view = next(v for v in view if v["target_ref"] == "place:river_pier")
            self.assertIn("退潮", pier_view["reason_hint_cn"])
            ctx_opp = wmos._context_opportunity(
                pier_view,
                labels={},
                place_names={"home": "小屋", "river_pier": "老防波堤渡口"},
                place_id="home",
                area_name=None,
                surface_labels={},
                body={},
            )
            self.assertIn("退潮", ctx_opp["description"])

    def test_rain_and_storm_suppress_outdoor_reason_hints(self) -> None:
        for weather_kind in ("rain", "storm"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "world").mkdir(parents=True)
                (root / "world" / "v04_neighborhood_state.json").write_text(
                    json.dumps({"environment": {"weather": weather_kind, "airflow": "strong"}}),
                    encoding="utf-8",
                )
                (root / "world" / "v05_living_town.json").write_text(
                    json.dumps({"theme": "coastal", "low_tide_open": True, "revision": 1}),
                    encoding="utf-8",
                )
                enriched = wmos.enrich_move_opportunities_with_life_reasons(
                    self._sample_moves(),
                    day_root=root,
                    observation={"location": {"place_id": "home"}},
                    now_iso="2026-11-20T15:00:00+08:00",
                    recent_note_streak=3,
                )
                by_target = {row["target_refs"][0]: row for row in enriched}
                # river_pier is outdoor -> outdoor outing cue must be suppressed in rain/storm
                self.assertNotIn("reason_hint_cn", by_target["place:river_pier"])


class TestCycleBreathingRhythmAndTimeoutSoftLanding(unittest.TestCase):
    def _make_minimal_cycle(
        self,
        day_root: Path,
        *,
        coordinator: object,
        materializer: object | None = None,
    ) -> cycle_mod.AutonomousLifeCycle:
        state_root = day_root / "one-day" / "life" / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        (day_root / "instance.json").write_text(
            json.dumps({"world_home": str(day_root / "one-day" / "world-home")}),
            encoding="utf-8",
        )
        if materializer is None:
            materializer = SimpleNamespace(
                _calls=[],
                set_breathing_rhythm_context=lambda **kw: materializer._calls.append(dict(kw)),
            )
        runtime = SimpleNamespace(
            state_root=state_root,
            coordinator=coordinator,
            candidate_materializer=materializer,
            candidate_registry=SimpleNamespace(register_adapter=lambda _a: None),
            activity_read=SimpleNamespace(list_open_activities=lambda: []),
            canonical_state=lambda: {"foreground_activity_ref": None},
            kill_switches=SimpleNamespace(to_dict=lambda: {}),
            world_opportunity_source=None,
            world_action_adapter=None,
            artifact_action_adapter=None,
        )
        cycle = cycle_mod.AutonomousLifeCycle(
            runtime,
            enabled=True,
            max_rounds_per_day=10,
            world_home=str(day_root / "one-day" / "world-home"),
            env={"CHIYO_AUTONOMOUS_LIFE_ENABLED": "true"},
        )
        # Stub minimal material & readiness so _evaluate_once_round reaches coordinator cleanly
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
        return cycle

    def test_cycle_feeds_streak_and_suppresses_on_rain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            day_root = Path(tmp)
            (day_root / "world").mkdir(parents=True)
            (day_root / "world" / "v04_neighborhood_state.json").write_text(
                json.dumps({"environment": {"weather": "clear"}}),
                encoding="utf-8",
            )
            recorded_rhythm: list[dict] = []
            mat = SimpleNamespace(
                set_breathing_rhythm_context=lambda **kw: recorded_rhythm.append(dict(kw))
            )

            class _QuietCoordinator:
                def run_decision_and_adoption_cycle(self, **kwargs):
                    self.last_situation_context = kwargs.get("situation_context")
                    return {
                        "correlation_id": "corr:stage1",
                        "decision_result": {
                            "decision_record": SimpleNamespace(
                                decision="NO_ACTION",
                                decision_id="dec:stage1",
                                decision_route="RULE_RESOLVED",
                                reason_codes=[],
                                short_rationale="",
                                selected_candidate_ref=None,
                            )
                        },
                        "adoption_result": {},
                    }

            coord = _QuietCoordinator()
            cycle = self._make_minimal_cycle(day_root, coordinator=coord, materializer=mat)
            cycle._recent_settled_action_kinds = ["continue_note", "continue_note"]
            cycle.last_world_opportunity_view = [
                {
                    "verb": "MOVE",
                    "target_ref": "place:river_pier",
                    "reason_hint_cn": "下午刚好退潮露出了旧石阶，拎包散步去海堤看看",
                }
            ]

            res = cycle.evaluate_once(trigger_ref="trig:stage1_clear", observed_at=OBSERVED, act=False)
            self.assertTrue(res["breathing_rhythm"]["stuffy"])
            self.assertEqual(res["breathing_rhythm"]["recent_note_streak"], 2)
            self.assertEqual(res["breathing_rhythm"]["effective_note_streak"], 2)
            self.assertFalse(res["breathing_rhythm"]["outdoor_push_suppressed"])
            self.assertIn("连写两段", res["breathing_rhythm"]["desk_stuffiness_hint_cn"])
            self.assertIn("退潮", res["breathing_rhythm"]["reason_hint_cn"])
            # First call set streak=2, finally call reset streak=0
            self.assertEqual(recorded_rhythm[0]["recent_note_streak"], 2)
            self.assertEqual(recorded_rhythm[-1]["recent_note_streak"], 0)
            self.assertIn("breathing_rhythm", coord.last_situation_context)

            # Now set weather to rain -> outdoor push MUST be suppressed!
            recorded_rhythm.clear()
            (day_root / "world" / "v04_neighborhood_state.json").write_text(
                json.dumps({"environment": {"weather": "rain"}}),
                encoding="utf-8",
            )
            res_rain = cycle.evaluate_once(trigger_ref="trig:stage1_rain", observed_at=OBSERVED, act=False)
            self.assertTrue(res_rain["breathing_rhythm"]["outdoor_push_suppressed"])
            self.assertEqual(res_rain["breathing_rhythm"]["effective_note_streak"], 0)
            self.assertEqual(res_rain["breathing_rhythm"]["reason_hint_cn"], "")
            self.assertEqual(recorded_rhythm[0]["recent_note_streak"], 0)

    def test_timeout_soft_landing_on_transport_exception_and_ag1_model_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            day_root = Path(tmp)

            # Path A: coordinator raises TimeoutError directly
            class _TimeoutRaisingCoordinator:
                def run_decision_and_adoption_cycle(self, **kwargs):
                    raise TimeoutError("TRANSPORT_TIMEOUT: cognition socket timed out")

            cycle_a = self._make_minimal_cycle(day_root, coordinator=_TimeoutRaisingCoordinator())
            out_a = cycle_a.evaluate_once(trigger_ref="trig:to_a", observed_at=OBSERVED, act=False)
            self.assertEqual(out_a["outcome"], cycle_mod.OUTCOME_DECISION_FAILED)
            self.assertEqual(out_a["soft_landing"], "IDLE_GLANCE_WINDOW")
            self.assertEqual(out_a["llm_retries"], 0)
            self.assertFalse(out_a["foreground_locked"])
            self.assertEqual(out_a["timeout_soft_landing"]["duration_minutes"], 5)
            self.assertIsNotNone(cycle_a.last_timeout_soft_landing)

            # Path B: AG-1 returns decision_record=None with MODEL_TIMEOUT failure_class
            class _ModelTimeoutResultCoordinator:
                def run_decision_and_adoption_cycle(self, **kwargs):
                    return {
                        "correlation_id": "corr:to_b",
                        "decision_result": {
                            "decision_record": None,
                            "failure_class": "DECISION_ATTEMPT_FAILED:MODEL_TIMEOUT",
                        },
                        "adoption_result": {},
                    }

            cycle_b = self._make_minimal_cycle(day_root, coordinator=_ModelTimeoutResultCoordinator())
            out_b = cycle_b.evaluate_once(trigger_ref="trig:to_b", observed_at=OBSERVED, act=False)
            self.assertEqual(out_b["outcome"], cycle_mod.OUTCOME_DECISION_FAILED)
            self.assertEqual(out_b["soft_landing"], "IDLE_GLANCE_WINDOW")
            self.assertEqual(out_b["llm_retries"], 0)
            self.assertFalse(out_b["foreground_locked"])
            self.assertEqual(out_b["timeout_soft_landing"]["status"], "IDLE_GLANCE_WINDOW")


class TestBridgePathsActiveStateAndMorningDreamFix(unittest.TestCase):
    def test_non_sleep_rounds_never_trigger_morning_dream_while_wake_does(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "life").mkdir(parents=True)
            (root / "phone").mkdir(parents=True)
            # Pre-existing v05_inner_spark.json used to trigger the P1 bug on EVERY round!
            (root / "life" / "v05_inner_spark.json").write_text(
                json.dumps({"schema_version": "life.v05.inner_spark.v1", "revision": 1}),
                encoding="utf-8",
            )
            with mock.patch(
                "components.life.scripts.v05_dreams_desires_foreshadow.settle_morning_dream_pulse"
            ) as mock_dream:
                for idx, ak in enumerate(
                    ("OUTING_VISIT", "COMMIT_WORK_VERSION", "REST", "BED_REST", "V04_WATER_PLANT"),
                    start=1,
                ):
                    bridge.settle_v02_v03_round_consequences(
                        root,
                        {
                            "actor_side": "life_resident",
                            "effect_state": "APPLIED",
                            "action_kind": ak,
                            "receipt_ref": f"rcpt:non_sleep_{idx}",
                            "operation_key": f"op_non_sleep_{idx}",
                        },
                        now_iso="2026-11-20T14:00:00+08:00",
                    )
                self.assertEqual(mock_dream.call_count, 0)

                # Explicit wake-from-sleep via action_kind == SLEEP / MORNING_WAKE / woke_from_sleep=True
                bridge.settle_v02_v03_round_consequences(
                    root,
                    {
                        "actor_side": "life_resident",
                        "effect_state": "APPLIED",
                        "action_kind": "MORNING_WAKE",
                        "receipt_ref": "rcpt:wake_1",
                        "operation_key": "op_wake_1",
                    },
                    now_iso="2026-11-20T07:30:00+08:00",
                )
                self.assertEqual(mock_dream.call_count, 1)

                bridge.settle_v02_v03_round_consequences(
                    root,
                    {
                        "actor_side": "life_resident",
                        "effect_state": "APPLIED",
                        "action_kind": "REST",
                        "woke_from_sleep": True,
                        "receipt_ref": "rcpt:wake_2",
                        "operation_key": "op_wake_2",
                    },
                    now_iso="2026-11-20T07:45:00+08:00",
                )
                self.assertEqual(mock_dream.call_count, 2)

    def test_bridge_resolves_one_day_economy_and_v02_canonical_sqlite_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Managed instance layout
            (root / "instance.json").write_text(
                json.dumps({"world_home": str(root / "one-day" / "world-home")}),
                encoding="utf-8",
            )
            eco_dir = root / "one-day" / "world-home" / "state"
            eco_dir.mkdir(parents=True)
            eco_file = eco_dir / "world_property_economy.json"
            eco_file.write_text(
                json.dumps(
                    {
                        "available_balance_cents": 240000,
                        "living_floor_cents": 50000,
                        "currency": "JPY",
                    }
                ),
                encoding="utf-8",
            )
            v02_dir = root / "v02"
            v02_dir.mkdir(parents=True)
            db_path = v02_dir / "canonical_life.sqlite3"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE actions (action_id TEXT PRIMARY KEY, outcome TEXT, provider_account TEXT, effect_namespace TEXT)"
            )
            conn.execute(
                "CREATE TABLE action_receipts ("
                "receipt_id TEXT PRIMARY KEY, action_id TEXT, receipt_kind TEXT, "
                "claimed_outcome TEXT, authoritative_for_machine_success INTEGER, "
                "actor_id TEXT, evidence_json TEXT)"
            )
            conn.execute(
                "INSERT INTO actions VALUES ('act:1', 'SUCCEEDED', 'world_owner', 'world')"
            )
            conn.execute(
                "INSERT INTO action_receipts VALUES ("
                "'crcpt:stage1_ok', 'act:1', 'OWNER_CONFIRMED', 'SUCCEEDED', 1, 'world_owner', '{}')"
            )
            conn.commit()
            conn.close()

            life_state = root / "one-day" / "life" / "state"
            life_state.mkdir(parents=True)
            before = _snapshot_tree(root)

            # 1. Economy probe via managed subpath or root finds one-day/world-home/state/world_property_economy.json
            env_info = bridge._read_ecology_envelope_zero_write(life_state, OBSERVED)
            self.assertTrue(env_info["present"])
            self.assertTrue(env_info["economy"]["world_economy_initialized"])
            self.assertEqual(env_info["economy"]["available_balance_cents"], 240000)

            # 2. Canonical ledger probe finds v02/canonical_life.sqlite3 with zero writes / no WAL/SHM created
            ok, reason = bridge._verify_authoritative_owner_receipt_zero_write(
                life_state,
                {
                    "action_id": "act:1",
                    "action_kind": "OUTING_VISIT",
                    "require_authoritative_receipt": True,
                },
                "crcpt:stage1_ok",
            )
            self.assertTrue(ok, reason)
            self.assertEqual(_snapshot_tree(root), before)

    def test_has_v02_v03_active_state_and_resident_day_root_on_bootstrapped_managed_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            self.assertFalse(bridge.has_v02_v03_active_state(empty))
            self.assertEqual(_snapshot_tree(empty), {})

            managed = Path(tmp) / "managed"
            life_state = managed / "one-day" / "life" / "state"
            world_home = managed / "one-day" / "world-home"
            (managed / "run").mkdir(parents=True)
            (managed / "phone").mkdir(parents=True)
            life_state.mkdir(parents=True)
            world_home.mkdir(parents=True)
            (managed / "run" / "nyairo-day-managed.json").write_text("{}", encoding="utf-8")
            (managed / "phone" / "v03_clock_state.json").write_text("{}", encoding="utf-8")

            before = _snapshot_tree(managed)
            self.assertTrue(bridge.has_v02_v03_active_state(managed))
            self.assertTrue(bridge.has_v02_v03_active_state(life_state))
            self.assertTrue(bridge.has_v02_v03_active_state(world_home))

            # Resident & Cycle both normalize one-day/life/state -> managed day_root
            fake_resident = SimpleNamespace(
                state_root=life_state,
                env={"CHIYO_WORLD_HOME": str(world_home)},
            )
            resolved_res = resident_mod.AutonomousLifeResident._resolve_v02_v03_day_root(fake_resident)
            self.assertEqual(resolved_res, managed)

            fake_cycle = SimpleNamespace(
                state_root=life_state,
                world_home=str(world_home),
                env={"CHIYO_WORLD_HOME": str(world_home)},
                _v02_v03_day_root=None,
            )
            resolved_cyc = cycle_mod.AutonomousLifeCycle._resolve_v02_v03_day_root_for_cycle(
                fake_cycle, bridge
            )
            self.assertEqual(resolved_cyc, managed)
            self.assertEqual(_snapshot_tree(managed), before)

    def test_deterministic_loop_selector_promotes_outing_when_stuffy_and_suppresses_in_rain(self) -> None:
        collected_clear = {
            "candidates": [
                {"candidate_id": "cand:draft_1"},
                {"candidate_id": "cand:walk_1"},
            ],
            "descriptors": {
                "cand:draft_1": {
                    "candidate_id": "cand:draft_1",
                    "action_family": "advance_workbench_draft",
                    "source_ref": "draft:1",
                },
                "cand:walk_1": {
                    "candidate_id": "cand:walk_1",
                    "action_family": "strenuous_outing_walk",
                    "source_ref": "outing:river",
                },
            },
            "gating": {"illness": {"phase_family": "healthy"}, "weather": "clear"},
        }
        # Streak 0 -> baseline keeps workbench draft
        sel0 = bridge.select_deterministic_loop_action(collected_clear, recent_note_streak=0)
        self.assertEqual(sel0["selected_candidate_id"], "cand:draft_1")
        # Streak 2 & clear -> promotes outing walk + surfaces desk_stuffiness & reason_hint_cn
        sel2 = bridge.select_deterministic_loop_action(collected_clear, recent_note_streak=2)
        self.assertEqual(sel2["selected_candidate_id"], "cand:walk_1")
        self.assertTrue(sel2["desk_stuffiness"]["stuffy"])
        self.assertIn("吹吹风", sel2["reason_hint_cn"])
        # Streak 2 & closeout exempt -> keeps workbench draft
        sel_close = bridge.select_deterministic_loop_action(
            collected_clear, recent_note_streak=2, is_final_closeout_step=True
        )
        self.assertEqual(sel_close["selected_candidate_id"], "cand:draft_1")
        # Streak 2 & rain -> suppresses outdoor push
        collected_rain = copy.deepcopy(collected_clear)
        collected_rain["gating"]["weather"] = "rain"
        sel_rain = bridge.select_deterministic_loop_action(collected_rain, recent_note_streak=2)
        self.assertEqual(sel_rain["selected_candidate_id"], "cand:draft_1")
        self.assertTrue(sel_rain["outdoor_push_suppressed"])


if __name__ == "__main__":
    unittest.main()

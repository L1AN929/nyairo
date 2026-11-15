#!/usr/bin/env python3
"""idle2: a GO_TO Activity's one Life step is its departure; arrival is not a step.

The real idle2 batch (``run:fastday-idle2-20261106``) shows the same
``GO_TO_OBSERVED_PLACE`` Activity holding the foreground for twelve decision
rounds.  Every round AG-1 was offered ``CONTINUE`` (its readiness said
``WORLD_STEP_AVAILABLE``) and every round the execution side built no step and
reported ``NO_STEP_AVAILABLE`` with detail ``adopted, but no executable step
exists in this revision``.

The reason is the same shape as the BODY single-action finding, for a different
mechanism: while a World move Activity holds the foreground, ``_world_step_spec``
is never built (``world_step`` is gated on ``not has_foreground``), and the
journey engine starts its own next timed segment after each confirmed one.  So
Life has exactly one step for this Activity -- the departure submission -- and
nothing else.  Arrival is settled by the World owner's own observation
(``settle_world_journey``), never by a second Life step or a second Decision.

These tests pin that answer down in ``life_step_readiness`` (the same typed
``current_step_unavailable_true`` fact the BODY rule already uses) and then run
one formal short chain: a real departure through the real World gateway, and a
second round that must not offer CONTINUE and must not touch the World.

Run:  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_idle2_no_step.py
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
for entry in (str(ROOT), str(ROOT / "components"), str(LIFE_SCRIPTS),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_step_readiness as lsr  # noqa: E402
import life_wait_basis as lwb  # noqa: E402

if os.name == "posix":  # agency_decision_ag1 imports fcntl; POSIX-only, like the module itself
    import agency_decision_ag1 as ag1  # noqa: E402
else:  # pragma: no cover - documented host limit
    ag1 = None

POSIX_ONLY = unittest.skipUnless(
    os.name == "posix", "agency_decision_ag1 needs a POSIX host (fcntl)")


GO_TO_ACTIVITY = "actv:go-to-1"
GO_TO_TARGET = "place:street_1"
GO_TO_WRAPPED = "world_fact:place:street_1"
MOVE_REASON = "WORLD_MOVE_NO_FURTHER_STEP"


def _go_to_activity(kind: str = "GO_TO_OBSERVED_PLACE", status: str = "ACTIVE") -> dict:
    return {"activity_ref": GO_TO_ACTIVITY, "activity_id": GO_TO_ACTIVITY,
            "activity_kind": kind, "status": status}


def _readiness(*, target=GO_TO_TARGET, activity=None, status="ACTIVE",
               kind="GO_TO_OBSERVED_PLACE", **kwargs) -> "lsr.StepReadiness":
    return lsr.assess_step_readiness(
        activity=(activity if activity is not None else _go_to_activity(kind, status)),
        target_ref=target, source_revision="3", **kwargs)


class WorldMoveReadinessTests(unittest.TestCase):
    """The GO_TO branch of the readiness table, and the boundaries around it."""

    def test_a_go_to_activity_has_no_further_step(self) -> None:
        for target in (GO_TO_TARGET, GO_TO_WRAPPED):
            with self.subTest(target=target):
                ready = _readiness(target=target)
                self.assertIs(ready.available, False, ready.to_dict())
                self.assertEqual(ready.reason_code, MOVE_REASON,
                                 ready.to_dict())
                self.assertTrue(ready.bound, ready.to_dict())
                self.assertEqual(ready.activity_ref, GO_TO_ACTIVITY, ready.to_dict())
                self.assertEqual(ready.activity_kind, "GO_TO_OBSERVED_PLACE", ready.to_dict())
                # the one key AG-1 consumes, carrying the boolean True (never a string)
                self.assertEqual(ready.fact_keys(), {lsr.STEP_UNAVAILABLE_FACT_KEY: True},
                                 ready.to_dict())
                self.assertIs(ready.fact_keys()[lsr.STEP_UNAVAILABLE_FACT_KEY], True)
        self.assertEqual(lsr.REASON_WORLD_MOVE_NO_FURTHER_STEP, MOVE_REASON)

    def test_a_place_target_is_one_move_under_any_kind(self) -> None:
        """The namespace is the encoded single move; no other Activity family carries ``place:``."""

        ready = _readiness(kind="SOMETHING_ELSE")

        self.assertIs(ready.available, False, ready.to_dict())
        self.assertEqual(ready.reason_code, MOVE_REASON,
                         ready.to_dict())
        self.assertEqual(lsr.namespace_of(GO_TO_TARGET), lsr.TARGET_PLACE)

    def test_unbound_or_closed_go_to_is_unknown_not_a_no_step_fact(self) -> None:
        no_activity = lsr.assess_step_readiness(activity=None, target_ref=GO_TO_TARGET)
        no_target = _readiness(target=None)
        closed = _readiness(status="PAUSED")
        foreign = _readiness(target="mystery:thing")

        self.assertIsNone(no_activity.available, no_activity.to_dict())
        self.assertEqual(no_activity.reason_code, lsr.REASON_NO_FOREGROUND)
        self.assertIsNone(no_target.available, no_target.to_dict())
        self.assertEqual(no_target.reason_code, lsr.REASON_NO_BOUND_TARGET)
        self.assertIsNone(closed.available, closed.to_dict())
        self.assertEqual(closed.reason_code, lsr.REASON_FOREGROUND_NOT_ACTIVE)
        self.assertIsNone(foreign.available, foreign.to_dict())
        self.assertEqual(foreign.reason_code, lsr.REASON_UNKNOWN_TARGET)
        for ready in (no_activity, no_target, closed, foreign):
            self.assertEqual(ready.fact_keys(), {})

    def test_reading_and_stay_steps_are_unchanged(self) -> None:
        """Positive controls: the Activity families that *do* have another step keep it."""

        reading = lsr.assess_step_readiness(
            activity={"activity_ref": "activity:read-1",
                      "activity_kind": "READ_PRIVATE_MATERIAL", "status": "ACTIVE"},
            target_ref="artifact:abc123", source_revision="artifactversion:h1",
            reading_state={"state": "IN_PROGRESS", "available": True})
        stay = lsr.assess_step_readiness(
            activity={"activity_ref": "activity:stay-1",
                      "activity_kind": lsr.STAY_ACTIVITY_KIND, "status": "ACTIVE"},
            target_ref="stay:bench:10:00", source_revision="12")
        unwired = lsr.assess_step_readiness(
            activity={"activity_ref": "activity:obj-1",
                      "activity_kind": "CONSUME_OBSERVED_OBJECT", "status": "ACTIVE"},
            target_ref="object:cup", source_revision="3", world_step_enabled=False)

        self.assertIs(reading.available, True, reading.to_dict())
        self.assertEqual(reading.reason_code, lsr.REASON_READING_PREFIX + "IN_PROGRESS")
        self.assertIs(stay.available, True, stay.to_dict())
        self.assertEqual(stay.reason_code, lsr.REASON_STAY_CHECKPOINT)
        self.assertIs(unwired.available, False, unwired.to_dict())
        self.assertEqual(unwired.reason_code, lsr.REASON_WORLD_STEP_NOT_WIRED)
        self.assertEqual(reading.fact_keys(), {})
        self.assertEqual(stay.fact_keys(), {})

    def test_the_move_reason_never_becomes_a_wait_dependency(self) -> None:
        """WAIT needs a named dependency on another owner's next version.  This is not one."""

        move = _readiness()

        self.assertIsNone(lwb.basis_from_readiness(move), move.to_dict())
        # positive control: the existing named dependency still produces its basis
        work_no_change = lsr.StepReadiness(
            available=False, reason_code=lsr.REASON_WORK_NO_CHANGE,
            activity_ref="activity:work-1", activity_kind="CONTINUE_PERSONAL_ARTIFACT",
            target_ref="artifact:abc123", source_revision="artifactversion:h1")
        self.assertIsInstance(lwb.basis_from_readiness(work_no_change), dict)


@POSIX_ONLY
class WorldMoveAg1FactTests(unittest.TestCase):
    """The move reason rides the exact typed fact channel AG-1 already consumes."""

    def test_the_fact_key_is_the_ag1_constant(self) -> None:
        self.assertEqual(lsr.STEP_UNAVAILABLE_FACT_KEY,
                         ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE)
        facts = _readiness().fact_keys()
        self.assertTrue(ag1.observed_fact_is_true(
            facts, ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE))
        for value in ("true", 1, False):
            with self.subTest(value=value):
                self.assertFalse(ag1.observed_fact_is_true(
                    {lsr.STEP_UNAVAILABLE_FACT_KEY: value},
                    ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE))


# =====================================================================================
# One formal short chain: a real departure, then a round that must not offer CONTINUE
# =====================================================================================


class MoveThenContinueCognition:
    """Scripted Decision (no model): START the first world candidate, then honour a
    legal CONTINUE exactly like the idle2 batch did, otherwise choose NO_ACTION."""

    model_id = "stub:idle2-no-step"
    model_version = "idle2-no-step-v1"

    def __init__(self) -> None:
        self.call_count = 0
        self.offered: list[list[str]] = []
        self.legal: list[dict[str, list[str]]] = []

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.call_count += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        legal_all = [str(name) for name in (getattr(request, "legal_decision_enums", []) or [])]
        self.offered.append([str(c.get("candidate_kind")) for c in candidates])
        self.legal.append({str(c.get("candidate_id")):
                           [str(name) for name in (c.get("legal_decisions") or [])]
                           for c in candidates})

        def legal_for(candidate, name):
            per = [str(item) for item in (candidate.get("legal_decisions") or [])]
            if per:
                return name in per
            return not legal_all or name in legal_all

        if self.call_count == 1:
            for candidate in candidates:
                if (candidate.get("candidate_kind") == "CONSIDER_WORLD_OPPORTUNITY"
                        and legal_for(candidate, "START")):
                    return self._reply({
                        "decision": "START",
                        "selected_candidate_id": candidate.get("candidate_id"),
                        "reason_codes": [ag1.REASON_WORLD_OPPORTUNITY_OBSERVED],
                        "short_rationale": "scripted: go to an observed place",
                        "deferred_candidate_ids": []})
        for candidate in candidates:
            if legal_for(candidate, "CONTINUE"):
                return self._reply({
                    "decision": "CONTINUE",
                    "selected_candidate_id": candidate.get("candidate_id"),
                    "reason_codes": [ag1.REASON_CONTINUITY_PREFERRED],
                    "short_rationale": "scripted: continue the current activity",
                    "deferred_candidate_ids": []})
        return self._reply({
            "decision": "NO_ACTION",
            "selected_candidate_id": None,
            "reason_codes": [ag1.REASON_NO_ACTION_CHOSEN],
            "short_rationale": "scripted: nothing legal to continue",
            "deferred_candidate_ids": []})

    def _reply(self, raw):
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:idle2-no-step:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


if os.name == "posix":
    from test_one_day_life import (  # noqa: E402
        CycleTest as _CycleTest, StubObservation as _StubObservation,
        StubWriter as _StubWriter, _current_place as _current_place)

    class WorldMoveFormalChainTests(_CycleTest):
        """Real runtime + real AG-1/AG-2 + real Supply/World owners, scripted Decisions."""

        def tearDown(self) -> None:
            # Close runtimes while their state root still exists, then keep every scratch
            # tree: this check retains all data (no rmtree / TemporaryDirectory).
            for runtime in getattr(self, "_runtimes", []):
                try:
                    runtime.close()
                except Exception:
                    pass
            self._runtimes = []

        def _world_fixture(self, cognition):
            import autonomous_life_assembly as assembly
            import one_day_life as odl

            data = self._prepared_instance()
            env = self.instance_env()
            runtime, _port = self._runtime(cognition, env, data)
            assembled = assembly.assemble_autonomous_life(
                runtime, environment=env, state_root=odl.life_state_root(self.instance),
                world_provider=_StubObservation(env), writer=_StubWriter(), enabled=True)
            self.assertEqual(assembled["state"], "READY", assembled)
            cycle = assembled["cycle"]
            cycle.closeout_plan = lambda: None
            return SimpleNamespace(runtime=runtime, cycle=cycle, cognition=cognition,
                                   env=env, data=data, assembled=assembled)

        def _world_bytes(self) -> dict[str, bytes]:
            home = self.world_home()
            names = ("data/world/world_state.json", "data/world/world_journey.json",
                     "data/world/world_travel_delivery.json")
            out = {}
            for name in names:
                path = home / name
                out[name] = path.read_bytes() if path.exists() else b""
            return out

        def test_a_real_departure_leaves_no_continue_step_and_changes_nothing(self) -> None:
            cognition = MoveThenContinueCognition()
            fixture = self._world_fixture(cognition)

            first = fixture.cycle.evaluate_once(trigger_ref="evt:idle2n:m1", act=True)
            self.assertEqual(first.get("outcome"), "IN_TRANSIT", first)
            self.assertEqual(first.get("decision"), "START", first)
            origin = _current_place(self.world_home())

            # The move is real: journey + travel ledger exist and the position has not changed.
            home = self.world_home()
            self.assertTrue((home / "data/world/world_journey.json").is_file())
            self.assertTrue((home / "data/world/world_travel_delivery.json").is_file())
            self.assertEqual(_current_place(home), origin)
            before = self._world_bytes()

            # Round two: still in transit, the GO_TO Activity still holds the foreground.
            readiness = fixture.cycle.step_readiness(act=True)
            self.assertEqual(readiness.activity_kind, "GO_TO_OBSERVED_PLACE",
                             readiness.to_dict())
            self.assertIs(readiness.available, False, readiness.to_dict())
            self.assertEqual(readiness.reason_code, MOVE_REASON,
                             readiness.to_dict())

            second = fixture.cycle.evaluate_once(trigger_ref="evt:idle2n:m2", act=True)
            self.assertNotEqual(second.get("outcome"), "NO_STEP_AVAILABLE", second)
            # The CONTINUE candidate stayed in the request (with its other exits) but AG-1 no
            # longer offers CONTINUE on it.
            legal = cognition.legal[-1]
            continue_candidates = [cid for cid, names in legal.items() if "CONTINUE" in names]
            self.assertEqual(continue_candidates, [], legal)
            self.assertTrue(any("PAUSE" in names or "ABANDON" in names
                                for names in legal.values()), legal)
            # ... and the second round did not move the World or replay the journey.
            self.assertEqual(self._world_bytes(), before)

    # ``CycleTest`` carries the W05 fixture's own test methods; this check runs exactly one
    # formal short chain, not that archive.  Non-callable entries are skipped by the loader.
    for _name in dir(_CycleTest):
        if _name.startswith("test_") and _name != (
                "test_a_real_departure_leaves_no_continue_step_and_changes_nothing"):
            setattr(WorldMoveFormalChainTests, _name, None)
    del _name
    # Do not let the imported W05 fixture class be collected as a second test suite.
    del _CycleTest


if __name__ == "__main__":
    unittest.main(verbosity=2)

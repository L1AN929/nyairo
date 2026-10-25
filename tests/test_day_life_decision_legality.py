#!/usr/bin/env python3
"""v0.4 day-life legality: AG-1 offers only decisions the owners will actually accept.

What this file pins down (and nothing more)
-------------------------------------------
The single-foreground contract belongs to the Activity store and to AG-2.  AG-1's job is to
*not offer* a decision that those owners are certain to refuse:

    LR-2  start_activity      -> refuses START while a foreground Activity is open
                                 (activity_continuity.py:1911-1922)
    LR-4  explicit_resume     -> refuses RESUME of another Activity while one holds the
                                 foreground (activity_continuity.py:2144-2157)
    AG-2  adoption pre-check  -> refuses START while the foreground Activity is ACTIVE
                                 (life_integration_ag2.py:2637-2641)

and, separately, that leaving a foreground Activity is *not* gated on body/resource
executability: PAUSE/ABANDON are the exits from the Activity she is already in, so a spent
budget must not trap her inside it.  CONTINUE keeps the execution gate, and ATOMIC still
withholds START/RESUME/PAUSE/ABANDON.

The life cycle reports "this Activity has no executable step left" through the *typed fact
channel* (``DecisionContextSnapshot.observed_facts``), key ``current_step_unavailable_true``.
That fact withdraws the current CONTINUE only: it grants no other decision, changes no capacity
or world fact, and a merely truthy value (``"true"``, ``1``) is not the fact.

Every case here is built from real AG-0 ``CandidateRecord``/``CandidateSet`` objects, a real
``DecisionContextSnapshot``, the real ``CapacityEvaluator`` and the real
``DecisionOutputValidator``.  No store, no lease, no model, no network, no clock.

Run:  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_day_life_decision_legality.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"

sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

if os.name == "posix":  # agency_decision_ag1 imports fcntl; the whole AG-1 module is POSIX-only
    import agency_decision_ag1 as ag1  # noqa: E402
    import candidate_sources_ag0 as ag0  # noqa: E402
else:  # pragma: no cover - documented host limit, not a silent skip of a real failure
    ag1 = None
    ag0 = None

import life_step_readiness as lsr  # noqa: E402

POSIX_ONLY = unittest.skipUnless(
    os.name == "posix", "agency_decision_ag1 needs a POSIX host (fcntl)"
)

TS = "2026-10-26T06:00:00+00:00"
ACT_READING = "actv:reading1"
ACT_NOTEBOOK = "actv:notebook2"

ALL_AVAILABLE = {
    "body_capacity_state": "AVAILABLE",
    "device_availability_state": "AVAILABLE",
    "resource_availability_state": "AVAILABLE",
    "privacy_availability_state": "AVAILABLE",
    "runtime_load_state": "AVAILABLE",
}
RESOURCE_SPENT = dict(ALL_AVAILABLE, resource_availability_state="UNAVAILABLE")


def _candidate(
    candidate_id: str,
    candidate_kind: str,
    source_kind: str,
    *,
    activity_ref=None,
    resume_eligibility_ref=None,
    goal_ref=None,
    goal_step_ref=None,
    world_opportunity_ref=None,
    perception_ref=None,
    world_revision_ref=None,
):
    """A real AG-0 CandidateRecord with the provenance its source kind requires."""
    anchor = candidate_id.split(":", 1)[-1]
    return ag0.CandidateRecord(
        candidate_id=candidate_id,
        candidate_kind=candidate_kind,
        source_kind=source_kind,
        source_ref="src:%s" % anchor,
        availability_status=ag0.CANDIDATE_STATUS_OPEN,
        created_at=TS,
        observed_at=TS,
        recorded_at=TS,
        idempotency_key="idem:%s" % anchor,
        activity_ref=activity_ref,
        resume_eligibility_ref=resume_eligibility_ref,
        goal_ref=goal_ref,
        goal_step_ref=goal_step_ref,
        world_opportunity_ref=world_opportunity_ref,
        perception_ref=perception_ref,
        world_revision_ref=world_revision_ref,
        causal_parent_refs=["src:%s" % anchor],
        provenance_refs=["src:%s" % anchor],
    )


def _continue_candidate():
    return _candidate(
        "cand:continue_reading",
        ag0.CANDIDATE_KIND_CONTINUE_CURRENT,
        ag0.SOURCE_CURRENT_ACTIVITY,
        activity_ref=ACT_READING,
    )


def _world_candidate():
    return _candidate(
        "cand:world_place",
        ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
        ag0.SOURCE_WORLD_OPPORTUNITY,
        world_opportunity_ref="wopp:street_front",
        perception_ref="percept:street_front",
        world_revision_ref="wrev:street_front:3",
    )


def _resume_candidate():
    return _candidate(
        "cand:resume_notebook",
        ag0.CANDIDATE_KIND_RESUME_ACTIVITY,
        ag0.SOURCE_RESUME_ELIGIBLE,
        activity_ref=ACT_NOTEBOOK,
        resume_eligibility_ref="relig:notebook2",
    )


def _resume_candidate_for(activity_ref, anchor):
    """A RESUME_ELIGIBLE candidate for a named Activity (OPEN eligibility, real provenance)."""
    return _candidate(
        "cand:resume_%s" % anchor,
        ag0.CANDIDATE_KIND_RESUME_ACTIVITY,
        ag0.SOURCE_RESUME_ELIGIBLE,
        activity_ref=activity_ref,
        resume_eligibility_ref="relig:%s" % anchor,
    )


def _goal_attention_candidate():
    """A goal with no decomposed step: START was never legal for it (Section 65)."""
    return _candidate(
        "cand:goal_attention",
        ag0.CANDIDATE_KIND_CONSIDER_EXPLICIT_GOAL,
        ag0.SOURCE_EXPLICIT_GOAL,
        goal_ref="goal:booklet",
    )


def _context(
    *,
    current_activity_ref=None,
    current_activity_status=None,
    interruptibility=None,
    observed_facts=None,
    known_waiting_basis_ref=None,
):
    return ag1.DecisionContextSnapshot(
        context_snapshot_id="dctx:daylife:legality",
        opportunity_ref="dopp:daylife:legality",
        candidate_set_ref="cset:daylife:legality",
        life_frame_ref="lframe:daylife:legality",
        captured_at=TS,
        source_revisions={},
        snapshot_fingerprint="sfp:daylife:legality",
        current_activity_ref=current_activity_ref,
        current_activity_status=current_activity_status,
        interruptibility=(
            ag1.INTERRUPTIBILITY_IMMEDIATE if interruptibility is None else interruptibility
        ),
        known_waiting_basis_ref=known_waiting_basis_ref,
        observed_facts=dict(observed_facts or {}),
    )


@POSIX_ONLY
class DayLifeLegalityFixture(unittest.TestCase):
    """Real-object fixtures shared by the two legality classes; it defines no test method."""

    def _fixture(
        self,
        candidates,
        *,
        current_activity_ref=None,
        current_activity_status=None,
        interruptibility=None,
        observed_facts=None,
        capacity=None,
    ):
        context = _context(
            current_activity_ref=current_activity_ref,
            current_activity_status=current_activity_status,
            interruptibility=interruptibility,
            observed_facts=observed_facts,
        )
        capacity_snapshot = ag1.CapacityEvaluator.evaluate_capacity(
            allowed_candidates=list(candidates),
            context_snapshot=context,
            captured_at=TS,
            **(ALL_AVAILABLE if capacity is None else capacity),
        )
        candidate_set = ag0.CandidateSet(
            candidate_set_id="cset:daylife:legality",
            subject_id=ag0.GLOBAL_SUBJECT_ID,
            candidate_refs=[c.candidate_id for c in candidates],
            source_snapshot_refs=["src:daylife:snapshot"],
            observed_at=TS,
            built_at=TS,
        )
        constraint_results = {
            c.candidate_id: ag1.CandidateConstraintResult(
                constraint_result_id="hcres:%s" % c.candidate_id.split(":", 1)[-1],
                candidate_ref=c.candidate_id,
                status=ag1.CONSTRAINT_STATUS_ALLOWED,
                reason_codes=["HARD_CONSTRAINT_PASSED"],
                constraint_refs=[],
                evaluated_at=TS,
            )
            for c in candidates
        }
        return context, capacity_snapshot, candidate_set, constraint_results

    @staticmethod
    def _legal(candidate, context, capacity_snapshot):
        return list(
            ag1.DecisionOutputValidator.compute_legal_decisions_for_candidate(
                candidate,
                context_snapshot=context,
                capacity_snapshot=capacity_snapshot,
            )
        )

@POSIX_ONLY
class DayLifeDecisionLegalityTest(DayLifeLegalityFixture):
    """Exact cases: what AG-1 offers for one candidate, given one real context and capacity."""

    # -- 1. the必拒 START is not offered while the foreground is ACTIVE -----

    def test_start_of_another_activity_is_withheld_while_the_foreground_is_active(self):
        """ACTIVE foreground + another Activity's candidate => no START, and the validator agrees."""
        continue_cand, world_cand = _continue_candidate(), _world_candidate()
        context, capacity, candidate_set, constraint_results = self._fixture(
            [continue_cand, world_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_ACTIVE,
        )

        legal = self._legal(world_cand, context, capacity)

        self.assertNotIn(ag1.DECISION_START, legal, legal)
        self.assertIn(ag1.DECISION_DEFER, legal, legal)
        # the same candidate the owner *can* run is still offered on the current Activity
        self.assertIn(
            ag1.DECISION_CONTINUE, self._legal(continue_cand, context, capacity)
        )
        with self.assertRaises(ag1.DecisionValidationError) as caught:
            ag1.DecisionOutputValidator.validate_decision_output(
                {
                    "decision": ag1.DECISION_START,
                    "selected_candidate_id": world_cand.candidate_id,
                    "reason_codes": [ag1.REASON_WORLD_OPPORTUNITY_OBSERVED],
                    "deferred_candidate_ids": [],
                },
                candidate_set=candidate_set,
                candidates_by_id={c.candidate_id: c for c in (continue_cand, world_cand)},
                constraint_results_by_id=constraint_results,
                context_snapshot=context,
                capacity_snapshot=capacity,
            )
        self.assertEqual(
            caught.exception.failure_class, ag1.FAILURE_CLASS_ILLEGAL_DECISION_MATRIX
        )

    # -- 2. ... but nothing was removed blanket-style ----------------------

    def test_start_is_still_offered_when_nothing_is_in_the_foreground(self):
        world_cand = _world_candidate()
        context, capacity, _cset, _results = self._fixture([world_cand])

        self.assertIn(ag1.DECISION_START, self._legal(world_cand, context, capacity))

    # -- 3. a second RESUME is not offered while the foreground is ACTIVE --

    def test_a_second_resume_is_withheld_while_the_foreground_is_active(self):
        resume_cand = _resume_candidate()
        busy_context, busy_capacity, _cs, _cr = self._fixture(
            [resume_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_ACTIVE,
        )
        idle_context, idle_capacity, _cs2, _cr2 = self._fixture([resume_cand])

        self.assertNotIn(
            ag1.DECISION_RESUME, self._legal(resume_cand, busy_context, busy_capacity)
        )
        self.assertIn(
            ag1.DECISION_DEFER, self._legal(resume_cand, busy_context, busy_capacity)
        )
        # with an empty foreground the very same candidate is resumable again
        self.assertIn(
            ag1.DECISION_RESUME, self._legal(resume_cand, idle_context, idle_capacity)
        )

    # -- 4. a spent budget must not trap her inside the Activity -----------

    def test_pause_and_abandon_survive_a_blocked_budget(self):
        continue_cand = _continue_candidate()
        context, capacity, _cs, _cr = self._fixture(
            [continue_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_ACTIVE,
            capacity=RESOURCE_SPENT,
        )
        self.assertEqual(
            capacity.candidate_executability[continue_cand.candidate_id]["executability"],
            ag1.EXEC_NOT_NOW,
        )

        legal = self._legal(continue_cand, context, capacity)

        self.assertNotIn(ag1.DECISION_CONTINUE, legal, legal)  # CONTINUE keeps the gate
        self.assertIn(ag1.DECISION_PAUSE, legal, legal)
        self.assertIn(ag1.DECISION_ABANDON, legal, legal)
        self.assertIn(ag1.DECISION_DEFER, legal, legal)

    # -- 5. the healthy path is unchanged ---------------------------------

    def test_continue_pause_and_abandon_are_offered_when_capacity_is_available(self):
        continue_cand = _continue_candidate()
        context, capacity, _cs, _cr = self._fixture(
            [continue_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_ACTIVE,
        )

        legal = self._legal(continue_cand, context, capacity)

        self.assertIn(ag1.DECISION_CONTINUE, legal, legal)
        self.assertIn(ag1.DECISION_PAUSE, legal, legal)
        self.assertIn(ag1.DECISION_ABANDON, legal, legal)
        self.assertIn(ag1.DECISION_DEFER, legal, legal)
        self.assertNotIn(ag1.DECISION_WAIT, legal, legal)  # no known waiting basis
        self.assertNotIn(ag1.DECISION_START, legal, legal)

    # -- 6. ATOMIC is never relaxed ---------------------------------------

    def test_the_atomic_boundary_still_withholds_pause_and_abandon(self):
        continue_cand, world_cand = _continue_candidate(), _world_candidate()
        context, capacity, _cs, _cr = self._fixture(
            [continue_cand, world_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_ACTIVE,
            interruptibility=ag1.INTERRUPTIBILITY_ATOMIC,
        )

        legal = self._legal(continue_cand, context, capacity)

        self.assertIn(ag1.DECISION_CONTINUE, legal, legal)
        self.assertNotIn(ag1.DECISION_PAUSE, legal, legal)
        self.assertNotIn(ag1.DECISION_ABANDON, legal, legal)
        self.assertNotIn(ag1.DECISION_START, self._legal(world_cand, context, capacity))
        self.assertIn(ag1.DECISION_DEFER, legal, legal)

    # -- 7. the sourced no-step fact withdraws the current CONTINUE only --

    def test_the_current_step_unavailable_fact_withdraws_only_the_current_continue(self):
        continue_cand = _continue_candidate()
        context, capacity, _cs, _cr = self._fixture(
            [continue_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_ACTIVE,
            observed_facts={ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE: True},
        )

        legal = self._legal(continue_cand, context, capacity)

        self.assertNotIn(ag1.DECISION_CONTINUE, legal, legal)
        # the legal exits survive, and the fact changed nothing about executability
        self.assertIn(ag1.DECISION_PAUSE, legal, legal)
        self.assertIn(ag1.DECISION_ABANDON, legal, legal)
        self.assertIn(ag1.DECISION_DEFER, legal, legal)
        self.assertEqual(
            capacity.candidate_executability[continue_cand.candidate_id]["executability"],
            ag1.EXEC_NOW,
        )

    # -- 8. False is not the fact -----------------------------------------

    def test_the_fact_with_a_false_value_leaves_the_continue_alone(self):
        continue_cand = _continue_candidate()
        context, capacity, _cs, _cr = self._fixture(
            [continue_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_ACTIVE,
            observed_facts={ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE: False},
        )

        self.assertIn(
            ag1.DECISION_CONTINUE, self._legal(continue_cand, context, capacity)
        )

    # -- 9. a truthy-looking value is not the fact ------------------------

    def test_a_truthy_looking_value_is_not_the_fact(self):
        continue_cand = _continue_candidate()
        for value in ("true", "True", 1, "yes"):
            with self.subTest(value=value):
                context, capacity, _cs, _cr = self._fixture(
                    [continue_cand],
                    current_activity_ref=ACT_READING,
                    current_activity_status=ag1.STATUS_ACTIVE,
                    observed_facts={ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE: value},
                )
                self.assertIn(
                    ag1.DECISION_CONTINUE,
                    self._legal(continue_cand, context, capacity),
                )

    # -- 10. the fact grants nothing and removes nothing else -------------

    def test_the_fact_grants_no_other_decision(self):
        resume_cand, world_cand, goal_cand = (
            _resume_candidate(),
            _world_candidate(),
            _goal_attention_candidate(),
        )
        candidates = [resume_cand, world_cand, goal_cand]
        fact = {ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE: True}
        context, capacity, _cs, _cr = self._fixture(candidates, observed_facts=fact)
        plain_context, plain_capacity, _cs2, _cr2 = self._fixture(candidates)

        # identical legality with and without the fact for every candidate that is not the
        # current Activity: it neither authorises nor withdraws anything else.
        for cand in candidates:
            self.assertEqual(
                self._legal(cand, context, capacity),
                self._legal(cand, plain_context, plain_capacity),
                cand.candidate_id,
            )
        self.assertIn(ag1.DECISION_RESUME, self._legal(resume_cand, context, capacity))
        self.assertIn(ag1.DECISION_START, self._legal(world_cand, context, capacity))
        # a goal without a decomposed step still cannot be STARTed (Section 65)
        self.assertNotIn(
            ag1.DECISION_START, self._legal(goal_cand, context, capacity)
        )

    # -- 11. the readiness table itself feeds the fact (BODY = one action, no next step) --

    def test_a_body_activitys_readiness_withdraws_only_the_current_continue(self):
        """The fact comes from the real readiness table, not a hand-written dict."""

        body = lsr.assess_step_readiness(
            activity={"activity_ref": ACT_READING,
                      "activity_kind": lsr.BODY_ACTIVITY_KIND, "status": "ACTIVE"},
            target_ref="bodyact:pose:seated:north", source_revision="12")
        self.assertIs(body.available, False, body.to_dict())
        self.assertEqual(body.reason_code, lsr.REASON_BODY_SINGLE_ACTION, body.to_dict())
        continue_cand = _continue_candidate()
        context, capacity, _cs, _cr = self._fixture(
            [continue_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_ACTIVE,
            observed_facts=body.fact_keys(),
        )

        legal = self._legal(continue_cand, context, capacity)

        self.assertNotIn(ag1.DECISION_CONTINUE, legal, legal)
        # every existing exit survives, and nothing new is offered
        self.assertIn(ag1.DECISION_PAUSE, legal, legal)
        self.assertIn(ag1.DECISION_ABANDON, legal, legal)
        self.assertIn(ag1.DECISION_DEFER, legal, legal)
        self.assertNotIn(ag1.DECISION_WAIT, legal, legal)   # still no known waiting basis
        self.assertNotIn(ag1.DECISION_START, legal, legal)
        self.assertEqual(
            capacity.candidate_executability[continue_cand.candidate_id]["executability"],
            ag1.EXEC_NOW,
        )

        # positive control: the same channel, but a reading Activity with a page left, is not a
        # no-step fact at all -- it emits no key and CONTINUE stays exactly as it was.
        reading = lsr.assess_step_readiness(
            activity={"activity_ref": ACT_READING,
                      "activity_kind": "READ_PRIVATE_MATERIAL", "status": "ACTIVE"},
            target_ref="artifact:abc123", source_revision="artifactversion:h1",
            reading_state={"state": "IN_PROGRESS", "available": True})
        self.assertEqual(reading.fact_keys(), {})
        reading_context, reading_capacity, _c2, _r2 = self._fixture(
            [continue_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_ACTIVE,
            observed_facts=reading.fact_keys(),
        )
        self.assertIn(ag1.DECISION_CONTINUE,
                      self._legal(continue_cand, reading_context, reading_capacity))


# ---------------------------------------------------------------------------
# Occupied-foreground counter-cases: the reference, not the ACTIVE status alone
# ---------------------------------------------------------------------------
@POSIX_ONLY
class DayLifeOccupiedForegroundTest(DayLifeLegalityFixture):
    """A legacy PAUSED pointer still occupies the slot; only "gone" or WAITING frees START/RESUME.
    v0.4 background pause: a *newly* paused Activity clears the pointer and is not represented
    by these synthetic contexts; a PAUSED record written before that rule has no release marker
    and still blocks the slot until its own owner path changes it.  LR-2 refuses START while
    any foreground ref is held (activity_continuity.py:1911-1922) and lets it through only for
    a WAITING foreground with ``allow_background_waiting`` (AG-2 passes exactly that,
    life_integration_ag2.py:3093-3096).  LR-4/LR-2 refuse RESUME of another Activity under the
    same condition, and the adopting owner no longer pauses the running Activity to make room.
    LR-2 refuses ABANDON of an Activity that is not the foreground unless it is WAITING or a
    released PAUSED Activity (activity_continuity.py terminal branch); AG-2 still withholds
    PAUSED ABANDON by its own safety rule.
    """
    def test_start_is_withheld_while_a_paused_foreground_still_holds_the_reference(self):
        world_cand = _world_candidate()
        context, capacity, candidate_set, constraint_results = self._fixture(
            [world_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_PAUSED,
        )
        legal = self._legal(world_cand, context, capacity)
        self.assertNotIn(ag1.DECISION_START, legal, legal)
        self.assertIn(ag1.DECISION_DEFER, legal, legal)
        with self.assertRaises(ag1.DecisionValidationError) as caught:
            ag1.DecisionOutputValidator.validate_decision_output(
                {
                    "decision": ag1.DECISION_START,
                    "selected_candidate_id": world_cand.candidate_id,
                    "reason_codes": [ag1.REASON_WORLD_OPPORTUNITY_OBSERVED],
                    "deferred_candidate_ids": [],
                },
                candidate_set=candidate_set,
                candidates_by_id={world_cand.candidate_id: world_cand},
                constraint_results_by_id=constraint_results,
                context_snapshot=context,
                capacity_snapshot=capacity,
            )
        self.assertEqual(
            caught.exception.failure_class, ag1.FAILURE_CLASS_ILLEGAL_DECISION_MATRIX
        )
    def test_a_second_resume_is_withheld_while_a_paused_foreground_holds_the_reference(self):
        resume_cand = _resume_candidate()
        context, capacity, _cs, _cr = self._fixture(
            [resume_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_PAUSED,
        )
        legal = self._legal(resume_cand, context, capacity)
        self.assertNotIn(ag1.DECISION_RESUME, legal, legal)
        self.assertIn(ag1.DECISION_DEFER, legal, legal)
    def test_another_activitys_eligible_candidate_offers_no_abandon(self):
        other_cand = _resume_candidate_for(ACT_NOTEBOOK, "notebook2")
        own_cand = _resume_candidate_for(ACT_READING, "reading1")
        context, capacity, _cs, _cr = self._fixture(
            [other_cand, own_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_PAUSED,
        )
        other_legal = self._legal(other_cand, context, capacity)
        own_legal = self._legal(own_cand, context, capacity)
        # another Activity: neither RESUME (LR-4) nor ABANDON (LR-2) may be offered
        self.assertNotIn(ag1.DECISION_RESUME, other_legal, other_legal)
        self.assertNotIn(ag1.DECISION_ABANDON, other_legal, other_legal)
        self.assertIn(ag1.DECISION_DEFER, other_legal, other_legal)
        # the matching foreground Activity keeps RESUME, but not ABANDON: a PAUSED
        # foreground's ABANDON exit is withheld until "no unresolved side effect" can be
        # proven from an existing owner fact (PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE).
        self.assertIn(ag1.DECISION_RESUME, own_legal, own_legal)
        self.assertNotIn(ag1.DECISION_ABANDON, own_legal, own_legal)
    def test_start_is_offered_with_no_foreground_and_with_a_waiting_foreground(self):
        world_cand = _world_candidate()
        idle_context, idle_capacity, _c1, _r1 = self._fixture([world_cand])
        waiting_context, waiting_capacity, _c2, _r2 = self._fixture(
            [world_cand],
            current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_WAITING,
        )
        self.assertIn(
            ag1.DECISION_START, self._legal(world_cand, idle_context, idle_capacity)
        )
        # a WAITING foreground is the one state both owners allow background waiting for
        self.assertIn(
            ag1.DECISION_START, self._legal(world_cand, waiting_context, waiting_capacity)
        )

if __name__ == "__main__":
    unittest.main(verbosity=2)

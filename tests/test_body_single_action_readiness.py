#!/usr/bin/env python3
"""v0.4 day-life: a BODY Activity has one action and therefore no further step.

What this file pins down (and nothing more)
-------------------------------------------
``life_step_readiness`` answers one question for the *foreground* Activity: is there one step
this process can really execute?  A BODY Activity (``BODY_ACTION_AT_OBSERVED_PLACE``) carries
exactly **one** body target, one idempotency key (place + revision + target) and one completion
condition (``WORLD_FACT_MATCH``).  No second step is defined anywhere in this tree: there is no
same-id retry policy and no "repeat the same pose" progress.  So the honest answer for it is
``False`` with a named reason -- which is exactly the value the cycle already projects as the
typed fact ``current_step_unavailable_true``, and AG-1 already withdraws only the current
CONTINUE on it.

The cases below are pure: no store, no lease, no model, no network, no clock.

Run:  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_body_single_action_readiness.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"

sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

import life_step_readiness as lsr  # noqa: E402
import life_wait_basis as lwb  # noqa: E402

if os.name == "posix":  # agency_decision_ag1 imports fcntl; POSIX-only, like the module itself
    import agency_decision_ag1 as ag1  # noqa: E402
else:  # pragma: no cover - documented host limit, never a silent skip of a real failure
    ag1 = None

POSIX_ONLY = unittest.skipUnless(
    os.name == "posix", "agency_decision_ag1 needs a POSIX host (fcntl)"
)

BODY_ACTIVITY = "activity:body-1"
BODY_TARGET = "bodyact:pose:seated:north"
BODY_WRAPPED = "world_fact:bodyact:pose:lying:south"


def _body_activity(kind: str = lsr.BODY_ACTIVITY_KIND, status: str = "ACTIVE") -> dict:
    return {"activity_ref": BODY_ACTIVITY, "activity_id": BODY_ACTIVITY,
            "activity_kind": kind, "status": status}


def _readiness(*, target=BODY_TARGET, activity=None, status="ACTIVE",
               kind=lsr.BODY_ACTIVITY_KIND, **kwargs) -> "lsr.StepReadiness":
    return lsr.assess_step_readiness(
        activity=(activity if activity is not None else _body_activity(kind, status)),
        target_ref=target, source_revision="12", **kwargs)


class BodySingleActionReadinessTests(unittest.TestCase):
    """The body branch of the readiness table, and the boundaries around it."""

    def test_a_body_activity_with_its_own_target_has_no_further_step(self) -> None:
        ready = _readiness()

        self.assertIs(ready.available, False, ready.to_dict())
        self.assertEqual(ready.reason_code, lsr.REASON_BODY_SINGLE_ACTION, ready.to_dict())
        self.assertTrue(ready.bound, ready.to_dict())
        self.assertEqual(ready.activity_ref, BODY_ACTIVITY, ready.to_dict())
        self.assertEqual(ready.target_ref, BODY_TARGET, ready.to_dict())
        self.assertEqual(ready.activity_kind, lsr.BODY_ACTIVITY_KIND, ready.to_dict())
        # the one key AG-1 consumes, carrying the boolean True (never a string)
        self.assertEqual(ready.fact_keys(), {lsr.STEP_UNAVAILABLE_FACT_KEY: True},
                         ready.to_dict())
        self.assertIs(ready.fact_keys()[lsr.STEP_UNAVAILABLE_FACT_KEY], True)

    def test_the_lr5_wrapper_around_the_same_target_is_folded(self) -> None:
        """The Activity owner records LR-5's ``world_fact:`` wrapper; it is the same one action."""

        ready = _readiness(target=BODY_WRAPPED)

        self.assertIs(ready.available, False, ready.to_dict())
        self.assertEqual(ready.reason_code, lsr.REASON_BODY_SINGLE_ACTION, ready.to_dict())
        self.assertEqual(lsr.namespace_of(BODY_WRAPPED), lsr.TARGET_BODY_ACTION)
        self.assertEqual(ready.fact_keys(), {lsr.STEP_UNAVAILABLE_FACT_KEY: True})

    def test_the_formal_body_kind_is_authoritative_for_its_own_shape(self) -> None:
        """Even a record that bound the place instead still is one body action, not a World walk."""

        ready = _readiness(target="place:home", kind=lsr.BODY_ACTIVITY_KIND)

        self.assertIs(ready.available, False, ready.to_dict())
        self.assertEqual(ready.reason_code, lsr.REASON_BODY_SINGLE_ACTION, ready.to_dict())
        self.assertEqual(ready.fact_keys(), {lsr.STEP_UNAVAILABLE_FACT_KEY: True})

    def test_a_bodyact_target_is_one_action_even_under_another_kind(self) -> None:
        """The namespace is the encoded single action; nothing else carries a ``bodyact:`` ref."""

        ready = _readiness(kind="SOMETHING_ELSE")

        self.assertIs(ready.available, False, ready.to_dict())
        self.assertEqual(ready.reason_code, lsr.REASON_BODY_SINGLE_ACTION, ready.to_dict())

    def test_a_paused_or_closed_body_activity_is_not_judged_as_a_step(self) -> None:
        for status in ("PAUSED", "WAITING", "COMPLETED", "ABANDONED"):
            with self.subTest(status=status):
                ready = _readiness(status=status)
                self.assertIsNone(ready.available, ready.to_dict())
                self.assertEqual(ready.reason_code, lsr.REASON_FOREGROUND_NOT_ACTIVE,
                                 ready.to_dict())
                self.assertEqual(ready.fact_keys(), {})

    def test_an_unbound_or_absent_foreground_is_unknown_not_no_step(self) -> None:
        no_activity = lsr.assess_step_readiness(activity=None, target_ref=BODY_TARGET)
        no_target = _readiness(target=None)

        self.assertIsNone(no_activity.available, no_activity.to_dict())
        self.assertEqual(no_activity.reason_code, lsr.REASON_NO_FOREGROUND)
        self.assertIsNone(no_target.available, no_target.to_dict())
        self.assertEqual(no_target.reason_code, lsr.REASON_NO_BOUND_TARGET)
        self.assertEqual(no_activity.fact_keys(), {})
        self.assertEqual(no_target.fact_keys(), {})

    def test_an_unknown_target_namespace_never_becomes_a_no_step_fact(self) -> None:
        ready = _readiness(target="mystery:thing")

        self.assertIsNone(ready.available, ready.to_dict())
        self.assertEqual(ready.reason_code, lsr.REASON_UNKNOWN_TARGET, ready.to_dict())
        self.assertEqual(ready.fact_keys(), {})

    def test_a_caller_that_may_not_act_is_not_a_fact_about_the_step(self) -> None:
        ready = _readiness(caller_can_act=False)

        self.assertIsNone(ready.available, ready.to_dict())
        self.assertEqual(ready.reason_code, lsr.REASON_CALLER_CANNOT_ACT, ready.to_dict())
        self.assertEqual(ready.fact_keys(), {})

    def test_reading_and_stay_steps_are_unchanged(self) -> None:
        """Positive controls: the two Activity families that *do* have another step keep it."""

        reading = lsr.assess_step_readiness(
            activity={"activity_ref": "activity:read-1",
                      "activity_kind": "READ_PRIVATE_MATERIAL", "status": "ACTIVE"},
            target_ref="artifact:abc123", source_revision="artifactversion:h1",
            reading_state={"state": "IN_PROGRESS", "available": True})
        stay = lsr.assess_step_readiness(
            activity={"activity_ref": "activity:stay-1",
                      "activity_kind": lsr.STAY_ACTIVITY_KIND, "status": "ACTIVE"},
            target_ref="stay:bench:10:00", source_revision="12")

        self.assertIs(reading.available, True, reading.to_dict())
        self.assertEqual(reading.reason_code, lsr.REASON_READING_PREFIX + "IN_PROGRESS")
        self.assertIs(stay.available, True, stay.to_dict())
        self.assertEqual(stay.reason_code, lsr.REASON_STAY_CHECKPOINT)
        self.assertEqual(reading.fact_keys(), {})
        self.assertEqual(stay.fact_keys(), {})

    def test_the_body_reason_never_becomes_a_wait_dependency(self) -> None:
        """WAIT needs a *named* dependency on another owner's next version.  This is not one."""

        body = _readiness()
        exhausted_reading = lsr.assess_step_readiness(
            activity={"activity_ref": "activity:read-1",
                      "activity_kind": "READ_PRIVATE_MATERIAL", "status": "ACTIVE"},
            target_ref="artifact:abc123", source_revision="artifactversion:h1",
            reading_state={"state": "EXHAUSTED", "available": False})

        self.assertIsNone(lwb.basis_from_readiness(body), body.to_dict())
        # positive control: the existing named dependency still produces its basis
        basis = lwb.basis_from_readiness(exhausted_reading, state="EXHAUSTED")
        self.assertIsInstance(basis, dict, exhausted_reading.to_dict())


@POSIX_ONLY
class BodySingleActionAg1FactTests(unittest.TestCase):
    """The fact key and the boolean shape are exactly the ones AG-1 already consumes."""

    def test_the_fact_key_is_the_ag1_constant(self) -> None:
        self.assertEqual(lsr.STEP_UNAVAILABLE_FACT_KEY, ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE)

    def test_ag1_reads_the_body_fact_as_true_and_nothing_else(self) -> None:
        ready = _readiness()
        facts = ready.fact_keys()

        self.assertTrue(ag1.observed_fact_is_true(facts, ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE))
        # a string, an int and False are not the fact (the existing AG-1 rule)
        for value in ("true", 1, False):
            with self.subTest(value=value):
                self.assertFalse(ag1.observed_fact_is_true(
                    {lsr.STEP_UNAVAILABLE_FACT_KEY: value},
                    ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE))


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""A（AFFECT/SHORT-APPRAISAL-1）→ D 的最小消费者：短期评价只作为有界考虑上下文。

This file proves one thing only: the cycle **consumes** A's projection inside a real round
(AG-0 → AG-1 → AG-2), and it consumes only items A still calls VALID.

What is *not* proven here, on purpose: there is no S07 affect owner in the tree, so this is an
experimental, rebuildable projection fed by an explicit input in the test -- never a feeling the
system invents about itself, and never a Candidate, a permission or a stable fact.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "native" / "integration"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import one_day_life as odl  # noqa: E402
import short_appraisal_view as sav  # noqa: E402
from test_life_v1_integration import DScenarioBase  # noqa: E402
from test_one_day_life import StubCognition, posix_only  # noqa: E402

NOW = "2026-10-16T07:00:00+00:00"
VALID_UNTIL = "2026-10-16T07:30:00+00:00"
APPRAISAL = {
    "appraisal_ref": "appraisal:t1",
    "source_refs": ["world:observation:1"],
    "observed_at": "2026-10-16T06:59:00+00:00",
    "valid_until": VALID_UNTIL,
    "declared_scope": "the desk in front of her",
    "uncertainty": "declared",
    "intensity": 0.4,
}


class RecordingCognition(StubCognition):
    """The loopback decision branch, keeping the request it was really handed."""

    def __init__(inner, policy: str = "no_action") -> None:
        super().__init__(policy)
        inner.requests = []

    def invoke(inner, request, *, schema_retry_index: int = 0):
        inner.requests.append(request)
        return super().invoke(request, schema_retry_index=schema_retry_index)


@posix_only
class AppraisalContextTest(DScenarioBase):

    # -- fixture helpers ----------------------------------------------------------------

    def _cycle(self, policy: str = "no_action"):
        prepared = self.prepare_scenario()
        env = self.instance_env()
        cognition = RecordingCognition(policy)
        runtime, port = self.runtime_with_memory(cognition, env, prepared.data, prepared.memory)
        fixture = self.assemble(runtime, env, cognition=cognition)
        fixture.runtime, fixture.port, fixture.data = runtime, port, prepared.data
        fixture.memory, fixture.env, fixture.cognition = prepared.memory, env, cognition
        return fixture

    def _view(self, runtime, *, appraisals=(APPRAISAL,), corrections=(), lost=(), now=NOW):
        """A's own projection, built from the explicit input the caller passes in."""
        subject = str(runtime.canonical_state().get("subject_id"))
        return sav.ShortAppraisalView(
            home=str(odl.life_state_root(self.instance)), subject_id=subject).build(
                now=now, appraisals=appraisals, corrections=corrections,
                lost_source_refs=lost)

    @staticmethod
    def _appraisal_items(request) -> list[dict]:
        return [dict(item) for item in (request.prior_experiences or ())
                if isinstance(item, dict) and item.get("kind") == "short_appraisal"]

    # -- 1. a valid sourced appraisal really reaches the decision context ----------------

    def test_a_valid_sourced_appraisal_reaches_the_decision_context(self):
        fixture = self._cycle()
        runtime, cycle, cognition = fixture.runtime, fixture.cycle, fixture.cognition
        cycle.appraisal_source = lambda: self._view(runtime)
        appraisal_round = cycle.evaluate_once(trigger_ref="evt:a:appraisal", act=True)
        self.assertEqual(appraisal_round["outcome"], "NO_ACTION", appraisal_round)
        self.assertEqual(cognition.call_count, 1, cognition.call_count)
        # the projection was really read and reported, with its own audit trail
        recorded = appraisal_round["appraisal"]
        self.assertTrue(recorded["has_valid_appraisal"], recorded)
        self.assertEqual(recorded["valid_appraisal_refs"], ["appraisal:t1"], recorded)
        self.assertIn("appraisal:t1", recorded["source_revisions"]["appraisals"], recorded)
        # ... and AG-1 really received it as bounded context (this is the consumer)
        self.assertEqual(len(cognition.requests), 1, cognition.requests)
        items = self._appraisal_items(cognition.requests[0])
        self.assertEqual(len(items), 1, items)
        item = items[0]
        self.assertEqual(item["appraisal_ref"], "appraisal:t1", item)
        self.assertEqual(item["source_refs"], ["world:observation:1"], item)
        self.assertEqual(item["valid_until"], VALID_UNTIL, item)
        self.assertEqual(item["observed_at"], "2026-10-16T06:59:00+00:00", item)
        self.assertEqual(item["declared_scope"], "the desk in front of her", item)
        # the three refusals: no authority, no candidate, no stable fact / tone inference
        self.assertIsNone(item["authority"], item)
        self.assertFalse(item["candidate"], item)
        self.assertFalse(item["stable_fact"], item)
        self.assertFalse(item["tone_inference_used"], item)

    # -- 2. corrected / expired / lost-source appraisals are not consumed ---------------

    def test_a_correction_or_expiry_keeps_the_appraisal_out_of_the_context(self):
        fixture = self._cycle()
        runtime, cycle, cognition = fixture.runtime, fixture.cycle, fixture.cognition
        # control: the same appraisal *is* consumed while it holds
        cycle.appraisal_source = lambda: self._view(runtime)
        first = cycle.evaluate_once(trigger_ref="evt:a:control", act=True)
        self.assertEqual(len(self._appraisal_items(cognition.requests[-1])), 1, first)
        # a valid correction targeting it (with its own source) leaves nothing to consume
        cycle.appraisal_source = lambda: self._view(
            runtime, corrections=[{"correction_ref": "correction:c1",
                                   "corrects_refs": ["appraisal:t1"],
                                   "source_ref": "world:observation:1"}])
        corrected = cycle.evaluate_once(trigger_ref="evt:a:corrected", act=True)
        self.assertFalse(corrected["appraisal"]["has_valid_appraisal"], corrected["appraisal"])
        self.assertEqual(corrected["appraisal"]["empty_reason_code"],
                         sav.REASON_APPRAISAL_CORRECTED, corrected["appraisal"])
        self.assertEqual(self._appraisal_items(cognition.requests[-1]), [],
                         cognition.requests[-1].prior_experiences)
        # ... and so does a passed valid_until, decided against the injected reference instant
        cycle.appraisal_source = lambda: self._view(runtime, now=VALID_UNTIL)
        expired = cycle.evaluate_once(trigger_ref="evt:a:expired", act=True)
        self.assertFalse(expired["appraisal"]["has_valid_appraisal"], expired["appraisal"])
        self.assertEqual(expired["appraisal"]["empty_reason_code"],
                         sav.REASON_APPRAISAL_EXPIRED, expired["appraisal"])
        self.assertEqual(self._appraisal_items(cognition.requests[-1]), [],
                         cognition.requests[-1].prior_experiences)
        # ... and a lost source too (the projection, not the cycle, decides this)
        cycle.appraisal_source = lambda: self._view(
            runtime, lost=["world:observation:1"])
        lost = cycle.evaluate_once(trigger_ref="evt:a:lost", act=True)
        self.assertFalse(lost["appraisal"]["has_valid_appraisal"], lost["appraisal"])
        self.assertEqual(lost["appraisal"]["empty_reason_code"],
                         sav.REASON_APPRAISAL_SOURCE_LOST, lost["appraisal"])
        self.assertEqual(self._appraisal_items(cognition.requests[-1]), [],
                         cognition.requests[-1].prior_experiences)

    # -- 3. no rounding cost, no Candidate, no authority --------------------------------

    def test_an_empty_appraisal_costs_no_call_and_never_becomes_a_candidate(self):
        fixture = self._cycle()
        runtime, cycle, cognition = fixture.runtime, fixture.cycle, fixture.cognition
        # unwired and empty-in-input are different facts, both reported honestly
        self.assertIsNone(cycle.read_appraisal())
        cycle.appraisal_source = lambda: self._view(runtime, appraisals=())
        empty_view = cycle.read_appraisal()
        self.assertFalse(empty_view["has_valid_appraisal"], empty_view)
        self.assertEqual(empty_view["empty_reason_code"], sav.REASON_NO_APPRAISAL_INPUT,
                         empty_view)
        # a quiet window stays quiet: an empty appraisal never creates a round or a call
        saved_port, cycle.artifact_port = cycle.artifact_port, None
        cycle.world_opportunity_provider = lambda: []
        quiet = cycle.evaluate_once(trigger_ref="evt:a:quiet", act=False)
        self.assertEqual(quiet["outcome"], "NO_MATERIAL", quiet)
        self.assertEqual(quiet["llm_calls"], 0, quiet)
        self.assertEqual(cognition.call_count, 0, cognition.call_count)
        cycle.artifact_port = saved_port
        # with a valid one, the projection still cannot mint a Candidate or a permission
        cycle.appraisal_source = lambda: self._view(runtime)
        items = cycle.appraisal_context()
        self.assertEqual([item["appraisal_ref"] for item in items], ["appraisal:t1"], items)
        for item in items:
            self.assertIsNone(item["authority"], item)
            self.assertFalse(item["candidate"], item)
            self.assertFalse(item["stable_fact"], item)
        view = cycle.read_appraisal()
        self.assertEqual(view["candidates_emitted"], 0, view)
        self.assertEqual(view["actions_authorized"], 0, view)
        self.assertEqual(view["writes_performed"], 0, view)
        # the round really runs and the AG-0/AG-1 candidate set carries no appraisal ref
        out = cycle.evaluate_once(trigger_ref="evt:a:nocand", act=True)
        self.assertEqual(out["outcome"], "NO_ACTION", out)
        offered = str([dict(c) for c in cognition.requests[-1].candidates])
        self.assertNotIn("appraisal", offered)
        self.assertNotIn("APPRAISAL", offered)
        self.assertEqual(len(self._appraisal_items(cognition.requests[-1])), 1,
                         cognition.requests[-1].prior_experiences)


if __name__ == "__main__":
    unittest.main()

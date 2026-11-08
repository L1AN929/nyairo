#!/usr/bin/env python3
"""The reason-code offer in the real request and the validator's registry are one contract.

Context
-------
One live attempt was filed as ``INVALID_MODEL_OUTPUT:UNKNOWN_REASON_CODE`` with
``field=reason_codes`` / ``rule=UNREGISTERED_CODE``; the specific code was sanitized away and
is unrecoverable.  A validator refusal is correct behaviour, so the question this file answers
is narrower: does the request the model actually receives on the production path carry the
same closed reason-code set the validator will accept, or did the two drift?

What is exercised (real objects, no hand-built schema)
------------------------------------------------------
* the real ``AgencyModelClient`` over a scripted transport -- the transport is the final wire
  boundary and this file reads exactly what was handed to it (system prompt + user prompt);
* the real ``ProductionAgencyCognitionAdapter.invoke`` builds that request from the same
  ``VALID_DECISION_REASON_CODES`` that ``DecisionReasonCodeRegistry.validate_codes`` uses;
* the real ``DecisionOutputValidator`` validates the model answer that was shaped from the
  captured request, plus the negative controls (unregistered code, bool/string/empty list,
  invented candidate).

No output schema is sent: the provider path is ``response_format={"type":"json_object"}`` only,
so the prompt's ``allowed_reason_codes`` is the model's sole authority.  This file pins that
the prompt names no second, hand-written code list either.

Offline: the existing g1 guard (AF_INET/AF_INET6/DNS/create_connection refused, AF_UNIX allowed)
is installed for the class.  Scratch is ``mkdtemp`` and kept; nothing is deleted.  No real
provider call is made.

Run:  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_decision_reason_contract.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(LIFE_SCRIPTS)]

if os.name == "posix":  # agency_decision_ag1 imports fcntl; the whole AG-1 module is POSIX-only
    import agency_cognition_adapter as aa  # noqa: E402
    import agency_decision_ag1 as ag1  # noqa: E402
    import agency_model_client as mc  # noqa: E402
    import candidate_sources_ag0 as ag0  # noqa: E402
else:  # pragma: no cover - documented host limit, not a silent skip of a real failure
    aa = ag1 = mc = ag0 = None

POSIX_ONLY = unittest.skipUnless(
    os.name == "posix", "agency_decision_ag1 needs a POSIX host (fcntl)"
)

TS = "2026-10-26T06:00:00+00:00"
ACT_READING = "actv:reading1"

WORLD_CANDIDATE = {
    "candidate_id": "cand:world_place",
    "candidate_kind": "CONSIDER_WORLD_OPPORTUNITY",
    "source_kind": "WORLD_OPPORTUNITY",
    "source_ref": "src:world_place",
    "target_refs": ["wopp:street_front"],
    "legal_decisions": ["START", "DEFER", "NO_ACTION"],
    "bounded_description": "a place she can see",
}
CONTINUE_CANDIDATE = {
    "candidate_id": "cand:continue_reading",
    "candidate_kind": "CONTINUE_CURRENT",
    "source_kind": "CURRENT_ACTIVITY",
    "source_ref": "src:continue_reading",
    "target_refs": [ACT_READING],
    "legal_decisions": ["CONTINUE", "PAUSE", "NO_ACTION"],
    "bounded_description": "the Activity already running",
}


class ScriptedTransport:
    """The final wire boundary: records the exact prompts and returns a scripted answer."""

    backend = "scripted_offline"
    provider = "stub-provider"
    model = "stub-model"

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []

    def complete(self, *, system_prompt, user_prompt, timeout_s=None, max_output_tokens=None):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "timeout_s": timeout_s,
            "max_output_tokens": max_output_tokens,
        })
        text = self.reply(json.loads(user_prompt))
        return mc.ModelCallResult(
            purpose="agency_cognition", provider=self.provider, model=self.model,
            text=text, latency_ms=1, status=200,
            input_tokens=1, output_tokens=1, total_tokens=2,
        )


def _request():
    return ag1.CognitiveDecisionRequest(
        opportunity_id="dopp:reason-contract",
        candidate_set_id="cset:reason-contract",
        context_snapshot_id="dctx:reason-contract",
        snapshot_fingerprint="sfp:reason-contract",
        candidates=(WORLD_CANDIDATE, CONTINUE_CANDIDATE),
        current_activity_summary=None,
        capacity_summary={},
        legal_decision_enums=("START", "CONTINUE", "NO_ACTION"),
        allowed_reason_codes=tuple(sorted(ag1.VALID_DECISION_REASON_CODES)),
        observed_fact_keys=(),
        policy_version="policy:reason-contract",
    )


def _adapter(transport, *, max_prompt_chars=aa.MAX_PROMPT_CHARS):
    client = mc.AgencyModelClient(
        config={"provider": "stub-provider", "model": "stub-model"},
        transport=transport, purpose="agency_cognition",
    )
    return aa.ProductionAgencyCognitionAdapter(
        client=client, timeout_s=8.0, max_output_tokens=512, max_prompt_chars=max_prompt_chars,
    )


def _answer_from_request(prompt):
    """A model answer that follows the request it was actually given, not a guessed set."""
    codes = [str(code) for code in (prompt.get("allowed_reason_codes") or [])]
    return json.dumps({
        "decision": "NO_ACTION",
        "selected_candidate_id": None,
        "reason_codes": codes[:1],
        "short_rationale": None,
        "deferred_candidate_ids": [],
    }, ensure_ascii=False)


def _context(*, current_activity_ref=None, current_activity_status=None):
    return ag1.DecisionContextSnapshot(
        context_snapshot_id="dctx:reason-contract",
        opportunity_ref="dopp:reason-contract",
        candidate_set_ref="cset:reason-contract",
        life_frame_ref="lframe:reason-contract",
        captured_at=TS,
        source_revisions={},
        snapshot_fingerprint="sfp:reason-contract",
        current_activity_ref=current_activity_ref,
        current_activity_status=current_activity_status,
        interruptibility=ag1.INTERRUPTIBILITY_IMMEDIATE,
        known_waiting_basis_ref=None,
        observed_facts={},
    )


def _record(candidate_id, candidate_kind, source_kind, *, activity_ref=None,
            world_opportunity_ref=None):
    anchor = candidate_id.split(":", 1)[-1]
    return ag0.CandidateRecord(
        candidate_id=candidate_id,
        candidate_kind=candidate_kind,
        source_kind=source_kind,
        source_ref="src:%s" % anchor,
        availability_status=ag0.CANDIDATE_STATUS_OPEN,
        created_at=TS, observed_at=TS, recorded_at=TS,
        idempotency_key="idem:%s" % anchor,
        activity_ref=activity_ref,
        world_opportunity_ref=world_opportunity_ref,
        perception_ref="percept:%s" % anchor if world_opportunity_ref else None,
        world_revision_ref="wrev:%s:1" % anchor if world_opportunity_ref else None,
        causal_parent_refs=["src:%s" % anchor],
        provenance_refs=["src:%s" % anchor],
    )


def _validator_fixture(records, *, current_activity_ref=None, current_activity_status=None):
    context = _context(current_activity_ref=current_activity_ref,
                       current_activity_status=current_activity_status)
    capacity = ag1.CapacityEvaluator.evaluate_capacity(
        allowed_candidates=list(records), context_snapshot=context, captured_at=TS,
        body_capacity_state="AVAILABLE", device_availability_state="AVAILABLE",
        resource_availability_state="AVAILABLE", privacy_availability_state="AVAILABLE",
        runtime_load_state="AVAILABLE",
    )
    candidate_set = ag0.CandidateSet(
        candidate_set_id="cset:reason-contract", subject_id=ag0.GLOBAL_SUBJECT_ID,
        candidate_refs=[c.candidate_id for c in records],
        source_snapshot_refs=["src:reason-contract"], observed_at=TS, built_at=TS,
    )
    constraint_results = {
        c.candidate_id: ag1.CandidateConstraintResult(
            constraint_result_id="hcres:%s" % c.candidate_id.split(":", 1)[-1],
            candidate_ref=c.candidate_id, status=ag1.CONSTRAINT_STATUS_ALLOWED,
            reason_codes=["HARD_CONSTRAINT_PASSED"], constraint_refs=[], evaluated_at=TS,
        )
        for c in records
    }
    return candidate_set, {c.candidate_id: c for c in records}, constraint_results, context, capacity


def _validate(payload, fixture):
    candidate_set, candidates_by_id, constraint_results, context, capacity = fixture
    return ag1.DecisionOutputValidator.validate_decision_output(
        payload,
        candidate_set=candidate_set,
        candidates_by_id=candidates_by_id,
        constraint_results_by_id=constraint_results,
        context_snapshot=context,
        capacity_snapshot=capacity,
    )


@POSIX_ONLY
class DecisionReasonContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import g1_real_adoption as guard_mod

        # mkdtemp is kept on purpose: the guard takes a root and this suite never deletes scratch.
        cls._scratch = Path(tempfile.mkdtemp(prefix="rc1-decision-reason-contract-"))
        cls._guard = guard_mod._offline_network_guard(cls._scratch)
        cls._guard.__enter__()
        selfcheck = guard_mod._offline_guard_selfcheck()
        wanted = ("inet_refused", "inet6_refused", "dns_refused", "connect_refused", "unix_ok")
        if not all(selfcheck.get(name) for name in wanted):
            cls._guard.__exit__(None, None, None)
            raise AssertionError("offline network guard self-check failed: %r" % (selfcheck,))

    @classmethod
    def tearDownClass(cls):
        cls._guard.__exit__(None, None, None)

    # -- 1. the final request carries exactly the validator's registry, nothing else -----

    def test_01_the_formal_request_offers_exactly_the_registry_closed_set(self):
        transport = ScriptedTransport(_answer_from_request)
        adapter = _adapter(transport)
        response = adapter.invoke(_request())

        self.assertEqual(1, len(transport.calls))
        call = transport.calls[-1]
        system_prompt, user_prompt = call["system_prompt"], call["user_prompt"]
        captured = json.loads(user_prompt)
        advertised = captured["allowed_reason_codes"]
        registry = set(ag1.VALID_DECISION_REASON_CODES)

        self.assertEqual(set(advertised), registry)
        self.assertEqual(len(advertised), len(registry), "duplicate codes in the request")
        for code in advertised:
            self.assertEqual([code], ag1.DecisionReasonCodeRegistry.validate_codes([code]))
        self.assertFalse(ag1.DecisionReasonCodeRegistry.is_valid(
            sorted(registry)[0].lower()), "membership must stay case-sensitive")

        # The prompt states the type and the authority for the field, and names no second list.
        for phrase in ("a JSON array of one or more strings",
                       "each copied exactly from",
                       "not a single string, and never an empty array"):
            self.assertIn(phrase, system_prompt)
        for code in registry:
            self.assertNotIn(code, system_prompt,
                             "the system prompt must not hard-code a reason code")

        # No provider-side output schema is sent; the prompt is the only wire authority.
        self.assertEqual({"system_prompt", "user_prompt", "timeout_s", "max_output_tokens"},
                         set(call))
        self.assertNotIn("response_format", call)
        self.assertNotIn("json_schema", call)

        # The answer the model was able to give from that request is accepted by the validator.
        fixture = _validator_fixture([])
        validated = _validate(response.raw_output, fixture)
        self.assertEqual("NO_ACTION", validated["decision"])
        self.assertIn(validated["reason_codes"][0], registry)

    # -- 2. legal START / CONTINUE / NO_ACTION answers still validate ---------------------

    def test_02_legal_start_continue_and_no_action_answers_are_accepted(self):
        world = _record("cand:world_place", ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
                        ag0.SOURCE_WORLD_OPPORTUNITY, world_opportunity_ref="wopp:street_front")
        continue_c = _record("cand:continue_reading", ag0.CANDIDATE_KIND_CONTINUE_CURRENT,
                             ag0.SOURCE_CURRENT_ACTIVITY, activity_ref=ACT_READING)

        start_fixture = _validator_fixture([world])
        start = _validate({
            "decision": "START", "selected_candidate_id": "cand:world_place",
            "reason_codes": [ag1.REASON_WORLD_OPPORTUNITY_OBSERVED],
            "deferred_candidate_ids": [],
        }, start_fixture)
        self.assertEqual("START", start["decision"])

        continue_fixture = _validator_fixture(
            [continue_c], current_activity_ref=ACT_READING,
            current_activity_status=ag1.STATUS_ACTIVE)
        cont = _validate({
            "decision": "CONTINUE", "selected_candidate_id": "cand:continue_reading",
            "reason_codes": [ag1.REASON_CURRENT_ACTIVITY_CONTINUITY],
            "deferred_candidate_ids": [],
        }, continue_fixture)
        self.assertEqual("CONTINUE", cont["decision"])

        no_action = _validate({
            "decision": "NO_ACTION", "selected_candidate_id": None,
            "reason_codes": [ag1.REASON_NO_ACTION_CHOSEN],
            "deferred_candidate_ids": [],
        }, continue_fixture)
        self.assertEqual("NO_ACTION", no_action["decision"])

        for code in (ag1.REASON_WORLD_OPPORTUNITY_OBSERVED,
                     ag1.REASON_CURRENT_ACTIVITY_CONTINUITY,
                     ag1.REASON_NO_ACTION_CHOSEN):
            self.assertIn(code, ag1.VALID_DECISION_REASON_CODES)

    # -- 3. the exact refusal the live attempt hit is unchanged ---------------------------

    def test_03_unregistered_reason_codes_and_wrong_types_keep_their_exact_refusal(self):
        fixture = _validator_fixture([])

        with self.assertRaises(ag1.DecisionValidationError) as caught:
            _validate({
                "decision": "NO_ACTION", "selected_candidate_id": None,
                "reason_codes": ["MADE_UP_REASON"], "deferred_candidate_ids": [],
            }, fixture)
        refused = caught.exception
        self.assertEqual(ag1.FAILURE_CLASS_UNKNOWN_REASON_CODE, refused.failure_class)
        detail = ag1.describe_schema_refusal(refused)
        self.assertEqual(("reason_codes", "UNREGISTERED_CODE"),
                         (detail["field"], detail["rule"]))
        self.assertNotIn("MADE_UP_REASON", json.dumps(detail, ensure_ascii=False))

        for value, expected_type in ((True, "bool"), ("NO_ACTION_CHOSEN", "string")):
            with self.subTest(value=value):
                with self.assertRaises(ag1.DecisionValidationError) as caught:
                    _validate({
                        "decision": "NO_ACTION", "selected_candidate_id": None,
                        "reason_codes": value, "deferred_candidate_ids": [],
                    }, fixture)
                type_detail = ag1.describe_schema_refusal(caught.exception)
                self.assertEqual(("reason_codes", "NOT_A_LIST", expected_type),
                                 (type_detail["field"], type_detail["rule"],
                                  type_detail["value_type"]))

        with self.assertRaises(ag1.DecisionValidationError) as caught:
            _validate({
                "decision": "NO_ACTION", "selected_candidate_id": None,
                "reason_codes": [], "deferred_candidate_ids": [],
            }, fixture)
        empty = ag1.describe_schema_refusal(caught.exception)
        self.assertEqual(("reason_codes", "EMPTY_NOT_ALLOWED"), (empty["field"], empty["rule"]))

        with self.assertRaises(ag1.DecisionValidationError) as caught:
            _validate({
                "decision": "NO_ACTION", "selected_candidate_id": "cand:ghost",
                "reason_codes": [ag1.REASON_NO_ACTION_CHOSEN],
                "deferred_candidate_ids": [],
            }, fixture)
        self.assertEqual(ag1.FAILURE_CLASS_INVENTED_CANDIDATE, caught.exception.failure_class)

    # -- 4. a compressed request may narrow the offer, never widen it --------------------

    def test_04_a_compressed_request_never_advertises_a_code_the_validator_refuses(self):
        transport = ScriptedTransport(_answer_from_request)
        adapter = _adapter(transport, max_prompt_chars=900)
        response = adapter.invoke(_request())
        self.assertTrue(adapter.last_prompt_trim.get("trimmed"),
                        "this case only proves anything when the structural trim really ran")

        captured = json.loads(transport.calls[-1]["user_prompt"])
        advertised = [str(code) for code in captured["allowed_reason_codes"]]
        registry = set(ag1.VALID_DECISION_REASON_CODES)
        self.assertTrue(advertised, "the request must always name at least one code")
        self.assertTrue(set(advertised) <= registry)
        for code in advertised:
            self.assertEqual([code], ag1.DecisionReasonCodeRegistry.validate_codes([code]))
        validated = _validate(response.raw_output, _validator_fixture([]))
        self.assertIn(validated["reason_codes"][0], registry)


if __name__ == "__main__":
    unittest.main()

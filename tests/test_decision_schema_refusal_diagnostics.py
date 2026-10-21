#!/usr/bin/env python3
"""v0.4 schema repair: a refused model answer must say *which field and which rule* refused it.

What this file pins, and why
----------------------------
A real local-Magpie attempt answered HTTP 200 / ``stop`` / 657 characters, the answer was a JSON
object, and the whole attempt was filed as::

    DECISION_ATTEMPT_FAILED:INVALID_SCHEMA    retry_count=0    decision_record=None

That class covers ~30 distinct refusals inside ``validate_decision_output`` (unknown top-level key,
``decision`` not a string, ``reason_codes`` not an array, ``short_rationale`` over its bound,
``later_item``/``method_proposal``/``sharing`` refused by their own domains, ...).  The refusal
*message* names the field, but it can also quote the model ("unknown keys: [...]",
"reason_code=...", "method_ref=..."), so it can never be journalled.  ``decide`` kept only the
class (``agency_decision_ag1.py`` line 4661) and ``_record_failed_attempt`` persisted only that
class, so the next run could not say what was wrong either.

These tests are written against that gap, not against a guess about the model:

* every refusal branch must name a **field** and a **rule** from a closed vocabulary;
* nothing the model wrote may reach the record -- no key names, no values, no rationale text
  (a sentinel key *and* a sentinel value are used below, and neither may appear);
* the ``DECISION_ATTEMPT_FAILED`` journal payload stays byte-compatible with the six keys older
  entries carry when there is no detail;
* the Decision prompt now states the exact types/bounds the validator enforces (an array for
  ``reason_codes``/``deferred_candidate_ids``, the real ``short_rationale`` bound), so a
  prompt-faithful answer is not refused for a type the prompt never mentioned.

Nothing here relaxes a check: every malformed payload below is *still* refused, with the same
failure class it had before.

Run:  PYTHONDONTWRITEBYTECODE=1 python3 tests/test_decision_schema_refusal_diagnostics.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"

sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

if os.name == "posix":  # agency_decision_ag1 imports fcntl; the AG-1 module is POSIX-only
    import activity_continuity as ac  # noqa: E402
    import agency_cognition_adapter as aa  # noqa: E402
    import agency_decision_ag1 as ag1  # noqa: E402
    import candidate_sources_ag0 as ag0  # noqa: E402
else:  # pragma: no cover - documented host limit, not a silent skip of a real failure
    ac = aa = ag1 = ag0 = None

POSIX_ONLY = unittest.skipUnless(
    os.name == "posix", "agency_decision_ag1 needs a POSIX host (fcntl)"
)

TS = "2026-10-26T06:00:00+00:00"

#: The decide-path fixtures are dated *in the past* on purpose: the pipeline stamps its own
#: ``recorded_at`` from the real clock, and evidence with ``observed_at`` in the future would read
#: as a clock bug in the journal instead of a synthetic fixture.
HARNESS_TS = "2026-10-21T02:00:00+00:00"

#: A complete, legal NO_ACTION answer.  Everything else below is this answer with one thing broken.
GOOD = {
    "decision": "NO_ACTION",
    "selected_candidate_id": None,
    "reason_codes": ["NO_ACTION_CHOSEN"],
    "short_rationale": "nothing legal right now",
    "deferred_candidate_ids": [],
}

#: Sentinels that must never appear in a refusal record: one private-looking key, one value.
SECRET_KEY = "notes_about_her_private_day"
SECRET_VALUE = "SENTINEL-PRIVATE-VALUE-DO-NOT-RECORD"


def _variant(**changes):
    payload = dict(GOOD)
    payload.update(changes)
    return payload


def _fixture():
    """Real AG-1 objects: one empty candidate set, no store, no lease, no model, no clock."""
    context = ag1.DecisionContextSnapshot(
        context_snapshot_id="dctx:schemas", opportunity_ref="dopp:schemas",
        candidate_set_ref="cset:schemas", life_frame_ref="lframe:schemas", captured_at=TS,
        source_revisions={}, snapshot_fingerprint="sfp:schemas",
        current_activity_ref=None, current_activity_status=None,
        interruptibility=ag1.INTERRUPTIBILITY_IMMEDIATE, known_waiting_basis_ref=None,
        observed_facts={},
    )
    capacity = ag1.CapacityEvaluator.evaluate_capacity(
        allowed_candidates=[], context_snapshot=context, captured_at=TS,
        body_capacity_state="AVAILABLE", device_availability_state="AVAILABLE",
        resource_availability_state="AVAILABLE", privacy_availability_state="AVAILABLE",
        runtime_load_state="AVAILABLE",
    )
    candidate_set = ag0.CandidateSet(
        candidate_set_id="cset:schemas", subject_id=ag0.GLOBAL_SUBJECT_ID, candidate_refs=[],
        source_snapshot_refs=[], observed_at=TS, built_at=TS,
    )
    return context, capacity, candidate_set


@POSIX_ONLY
class DecisionSchemaRefusalTest(unittest.TestCase):
    """One refusal branch per case; each asserts the *named* reason, never a guess."""

    @classmethod
    def setUpClass(cls):
        cls.context, cls.capacity, cls.candidate_set = _fixture()

    # -- helpers ---------------------------------------------------------------------

    def _validate(self, payload):
        return ag1.DecisionOutputValidator.validate_decision_output(
            payload,
            candidate_set=self.candidate_set,
            candidates_by_id={},
            constraint_results_by_id={},
            context_snapshot=self.context,
            capacity_snapshot=self.capacity,
            allowed_method_refs=None,
        )

    def _refusal(self, payload):
        """Validate a payload that MUST still be refused, and return its value-free record."""
        with self.assertRaises(Exception) as caught:
            self._validate(payload)
        exc = caught.exception
        self.assertIn(
            type(exc).__name__, {"DecisionValidationError", "HiddenReasoningForbiddenError"}, exc,
        )
        return ag1.describe_schema_refusal(exc)

    # -- the answer that is fine is still fine --------------------------------------

    def test_a_legal_answer_is_still_accepted(self):
        validated = self._validate(dict(GOOD))
        self.assertEqual("NO_ACTION", validated["decision"])
        self.assertEqual(["NO_ACTION_CHOSEN"], validated["reason_codes"])

    # -- the branch that hides everything: an extra key ------------------------------

    def test_an_unknown_key_is_named_by_rule_count_and_alias_never_by_name(self):
        detail = self._refusal(_variant(confidence=0.9, **{SECRET_KEY: SECRET_VALUE}))
        self.assertEqual("decision_output", detail["field"])
        self.assertEqual("UNKNOWN_TOP_LEVEL_KEYS", detail["rule"])
        self.assertEqual(2, detail["unknown_key_count"])
        # Only *our own* alias vocabulary may be echoed; the model's key name never is.
        self.assertEqual(["confidence"], detail.get("unknown_key_alias_hits"))
        blob = json.dumps(detail, ensure_ascii=False)
        self.assertNotIn(SECRET_KEY, blob)
        self.assertNotIn(SECRET_VALUE, blob)
        self.assertNotIn("confidence", blob.replace('"confidence"', ""))

    # -- wrong types on required fields ---------------------------------------------

    def test_wrong_typed_required_fields_name_the_field_the_rule_and_the_json_type(self):
        cases = (
            ("decision", {"action": "NO_ACTION"}, "object"),
            ("selected_candidate_id", 3, "number"),
            ("deferred_candidate_ids", "cand:one", "string"),
            ("reason_codes", "NO_ACTION_CHOSEN", "string"),
            ("short_rationale", {"text": "x"}, "object"),
        )
        for field, value, expected_type in cases:
            with self.subTest(field=field):
                detail = self._refusal(_variant(**{field: value}))
                self.assertEqual(field, detail["field"])
                self.assertEqual(expected_type, detail["value_type"])
                self.assertIn(
                    detail["rule"],
                    {"MISSING_OR_WRONG_TYPE", "NOT_A_LIST"},
                    detail,
                )
                # The refusal class is exactly what the old code recorded: nothing was relaxed.
                self.assertEqual(
                    "DECISION_ATTEMPT_FAILED:INVALID_SCHEMA", detail["failure_class"], detail,
                )

    # -- bounds and registries -------------------------------------------------------

    def test_an_overlong_short_rationale_names_its_field_and_rule(self):
        detail = self._refusal(_variant(short_rationale="x" * (ag1.MAX_SHORT_RATIONALE_CHARS + 5)))
        self.assertEqual("short_rationale", detail["field"])
        self.assertEqual("TOO_LONG", detail["rule"])

    def test_the_rationale_boundary_is_still_280_strict(self):
        """The prompt change is advice; the enforced boundary must be exactly where it was:
        280 characters pass, 281 are refused.  Nothing here may widen it."""

        at_limit = _variant(short_rationale="x" * ag1.MAX_SHORT_RATIONALE_CHARS)
        accepted = self._validate(at_limit)
        self.assertEqual(ag1.MAX_SHORT_RATIONALE_CHARS,
                         len(accepted["short_rationale"]))
        over = self._refusal(_variant(short_rationale="x" * (ag1.MAX_SHORT_RATIONALE_CHARS + 1)))
        self.assertEqual(("short_rationale", "TOO_LONG"), (over["field"], over["rule"]))

    def test_reason_codes_that_are_empty_or_unregistered_are_named_without_the_code(self):
        empty = self._refusal(_variant(reason_codes=[]))
        self.assertEqual(("reason_codes", "EMPTY_NOT_ALLOWED"), (empty["field"], empty["rule"]))
        invented = self._refusal(_variant(reason_codes=["MADE_UP_BY_THE_MODEL"]))
        self.assertEqual(("reason_codes", "UNREGISTERED_CODE"),
                         (invented["field"], invented["rule"]))
        self.assertNotIn("MADE_UP_BY_THE_MODEL", json.dumps(invented, ensure_ascii=False))

    # -- nested contracts ------------------------------------------------------------

    def test_nested_contracts_name_their_own_field(self):
        detail = self._refusal(_variant(later_item={"title": "later", "park_current": "yes"}))
        self.assertEqual(("later_item", "REFUSED_BY_DOMAIN"), (detail["field"], detail["rule"]))
        proposal = self._refusal(_variant(method_proposal={"title": "try this"}))
        self.assertEqual("method_proposal", proposal["field"])
        sharing = self._refusal(_variant(sharing={"op": "bogus"}))
        self.assertEqual("sharing", sharing["field"])

    # -- the pre-fix shape, as a control ---------------------------------------------

    def test_a_message_only_refusal_cannot_name_anything(self):
        """What every raise site looked like before the repair: class only, no field, no rule.

        This is the control for the assertions above: an exception built the old way cannot
        produce a field, so the named-field tests are not passing vacuously.
        """

        old_style = ag1.DecisionValidationError(
            "unknown keys in decision output: ['%s']" % SECRET_KEY
        )
        detail = ag1.describe_schema_refusal(old_style)
        self.assertEqual("DECISION_ATTEMPT_FAILED:INVALID_SCHEMA", detail["failure_class"])
        self.assertEqual(("unknown", "UNCLASSIFIED"), (detail["field"], detail["rule"]))
        self.assertNotIn(SECRET_KEY, json.dumps(detail, ensure_ascii=False))

    def test_a_forbidden_reasoning_answer_is_no_longer_filed_as_a_schema_error(self):
        """``<think>``/scratchpad in the rationale carries no failure class of its own before this
        repair, so ``decide``'s fallback labelled it ``INVALID_SCHEMA``."""

        with self.assertRaises(ag1.HiddenReasoningForbiddenError) as caught:
            ag1.sanitize_short_rationale("<think> hidden</think>", observed_facts={})
        self.assertEqual(
            "INVALID_MODEL_OUTPUT:FORBIDDEN_HIDDEN_REASONING", caught.exception.failure_class
        )
        self.assertEqual(
            "INVALID_MODEL_OUTPUT:FORBIDDEN_UNIVERSAL_UTILITY",
            ag1.UniversalUtilityForbiddenError.failure_class,
        )


@POSIX_ONLY
class FailureJournalPayloadTest(unittest.TestCase):
    """The durable channel: what a failed attempt leaves in ``agency_decision_journal.jsonl``."""

    ARGS = dict(opportunity_id="dopp:journal", attempt_id="datt:journal",
                failure_class=ag1.FAILURE_CLASS_INVALID_SCHEMA, failure_ref="dfail:journal",
                model_run_ref="mrun:journal")

    def test_without_a_detail_the_payload_is_the_original_six_keys(self):
        payload = ag1.failure_journal_payload(**self.ARGS)
        self.assertEqual(
            ["attempt_id", "event_type", "failure_class", "failure_ref", "model_run_ref",
             "opportunity_id"],
            sorted(payload),
        )
        self.assertEqual("DECISION_ATTEMPT_FAILED", payload["event_type"])
        self.assertNotIn("schema_refusal", payload)

    def test_with_a_detail_only_whitelisted_value_free_keys_are_persisted(self):
        detail = {"failure_class": ag1.FAILURE_CLASS_INVALID_SCHEMA, "field": "short_rationale",
                  "rule": "TOO_LONG", "value_type": "string",
                  "raw_answer": SECRET_VALUE, SECRET_KEY: SECRET_VALUE}
        payload = ag1.failure_journal_payload(**self.ARGS, schema_detail=detail)
        self.assertEqual(
            {"failure_class": ag1.FAILURE_CLASS_INVALID_SCHEMA, "field": "short_rationale",
             "rule": "TOO_LONG", "value_type": "string"},
            payload["schema_refusal"],
        )
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(SECRET_VALUE, blob)
        self.assertNotIn(SECRET_KEY, blob)

    def test_an_empty_or_missing_detail_adds_nothing(self):
        for detail in (None, {}, {"unlisted": "x"}):
            with self.subTest(detail=detail):
                payload = ag1.failure_journal_payload(**self.ARGS, schema_detail=detail)
                self.assertNotIn("schema_refusal", payload)

    def test_sanitize_keeps_none_as_none(self):
        self.assertIsNone(ag1.sanitize_schema_refusal(None))
        self.assertIsNone(ag1.sanitize_schema_refusal({}))


@POSIX_ONLY
class PromptContractTest(unittest.TestCase):
    """The prompt must state what the validator enforces -- the repair's other half."""

    def test_the_prompt_states_the_required_types_it_enforces(self):
        prompt = aa.SYSTEM_PROMPT
        for phrase in (
            "a JSON array of one or more strings",   # reason_codes
            "each copied exactly from",              # ... from allowed_reason_codes
            "a JSON array of candidate_id values",   # deferred_candidate_ids
            "not a single string",                   # both arrays
            "An extra key of your own",              # unknown keys are refused
        ):
            self.assertIn(phrase, prompt, phrase)

    def test_the_prompt_states_the_real_rationale_bound(self):
        self.assertEqual(ag1.MAX_SHORT_RATIONALE_CHARS, aa.MAX_RATIONALE_CHARS)
        self.assertIn("%d characters" % ag1.MAX_SHORT_RATIONALE_CHARS, aa.SYSTEM_PROMPT)
        self.assertNotIn("__MAX_RATIONALE_CHARS__", aa.SYSTEM_PROMPT)

    def test_the_prompt_lists_method_proposal_among_the_allowed_keys(self):
        """``method_proposal`` was legal in the validator but missing from the prompt's own
        "these keys and nothing else" list -- an answer that used it was accepted, and an answer
        that avoided it for fear of the list lost a legal field."""

        keys_line = aa.SYSTEM_PROMPT.split("Answer with these keys and nothing else:", 1)[1]
        keys_line = keys_line.split("An extra key", 1)[0]
        for key in ("decision", "selected_candidate_id", "reason_codes", "short_rationale",
                    "deferred_candidate_ids", "later_item", "future_item_actions", "method_ref",
                    "method_trial", "method_proposal", "sharing"):
            self.assertIn(key, keys_line, key)

    def test_the_prompt_asks_for_a_short_public_reason_and_says_what_it_is_not(self):
        """The real window's refusal was ``short_rationale`` TOO_LONG; the prompt must say the
        difference between deliberating and publishing a reason, and give real headroom."""

        prompt = aa.SYSTEM_PROMPT
        # public reason vs internal deliberation
        self.assertIn("never your internal deliberation", prompt)
        self.assertIn("only say to yourself stay out of the answer", prompt)
        # one short sentence, advised well under the enforced ceiling
        self.assertIn("ONE short sentence", prompt)
        self.assertIn("<= %d characters" % aa.RATIONALE_ADVICE_CHARS, prompt)
        # characters, never tokens
        self.assertIn("characters, not", prompt)
        self.assertIn("tokens", prompt)
        # the hard ceiling is still stated, and the advice is strictly below it
        self.assertIn("%d characters is the hard ceiling" % ag1.MAX_SHORT_RATIONALE_CHARS, prompt)
        self.assertLess(aa.RATIONALE_ADVICE_CHARS, ag1.MAX_SHORT_RATIONALE_CHARS)
        # no unresolved placeholders leaked into the prompt
        self.assertNotIn("__RATIONALE_ADVICE_CHARS__", prompt)
        self.assertNotIn("__MAX_RATIONALE_CHARS__", prompt)

    def test_the_prompt_leaves_null_and_the_decision_choice_to_the_model(self):
        """``null`` stays legal (the model produces it; nothing rewrites the answer afterwards),
        and the shape example must not nudge START/NO_ACTION/DEFER either way."""

        prompt = aa.SYSTEM_PROMPT
        self.assertIn("null is equally legal", prompt)
        self.assertIn("Only you choose it", prompt)
        self.assertIn("weighs one legal decision word over another", prompt)
        # the shape block is a shape: both null and a filled id are shown as legitimate
        self.assertIn("both a null and a filled selected_candidate_id are legitimate shapes", prompt)
        shape = prompt.split("Shape only.", 1)[1].split("\n- ", 1)[0]
        self.assertIn('"decision": "<one of the legal decision words>"', shape)
        self.assertIn('"selected_candidate_id": "<candidate_id, or null>"', shape)
        for banned in ("START", "NO_ACTION", "DEFER", "CONTINUE", "RESUME", "PAUSE", "ABANDON"):
            self.assertNotIn(banned, shape, banned)

    def test_the_prompt_still_carries_the_normal_life_context_and_her_autonomy(self):
        """A conciseness fix must not delete the day: the situation blocks and "no output owed"
        stay, so the shortening cannot be bought by removing what she may decide on."""

        prompt = aa.SYSTEM_PROMPT
        for anchor in ("situation_context.world", "situation_context.body",
                       "situation_context.contact", "situation_context.sharing",
                       "one continuous day", "No schedule is imposed on her and no output is owed",
                       "choosing NO_ACTION is correct and expected"):
            self.assertIn(anchor, prompt)


# ---------------------------------------------------------------------------------------------
# the real failed attempt: AgencyDecisionService.evaluate_opportunity -> store -> journal file
#
# The earlier half of this file calls the validator directly.  That is how the deleted
# ``capacity_snapshot`` parameter survived: the validator never sees that call.  These cases run
# the real service (real store, real lease, real signed capability) with a stub cognition adapter,
# so a broken failure path cannot pass.
# ---------------------------------------------------------------------------------------------

#: Where the throw-away stores go.  Each run makes one unique directory and **leaves it in place**
#: (no TemporaryDirectory, no tearDown deletion): the WAL and journal of a failed attempt are the
#: evidence this repair is about.  Override with ``NYAIRO_SCHEMA_TEST_ROOT`` to keep it near the
#: caller; the default is the platform temp dir.
FIXTURE_ROOT = Path(os.environ.get("NYAIRO_SCHEMA_TEST_ROOT")
                    or Path(tempfile.gettempdir()) / "nyairo-decision-schema-fixtures")


class _StubCognition:
    """Replays canned raw_output values, one per ``invoke()`` -- exactly like a provider would."""

    send_split_capable = False

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def invoke(self, request, *, schema_retry_index=0):
        index = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        return ag1.CognitiveDecisionResponse(
            model_run_ref="mrun:stub:%s" % uuid.uuid4().hex[:8],
            model_id="stub:offline", model_version="stub-v1",
            raw_output=self.outputs[index], latency_ms=1,
        )


def _world_candidate(candidate_id="cand:schema"):
    """One real AG-0 candidate with the provenance its source kind requires."""

    anchor = candidate_id.split(":", 1)[-1]
    return ag0.CandidateRecord(
        candidate_id=candidate_id,
        candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
        source_kind=ag0.SOURCE_WORLD_OPPORTUNITY,
        source_ref="src:%s" % anchor,
        availability_status=ag0.CANDIDATE_STATUS_OPEN,
        created_at=HARNESS_TS, observed_at=HARNESS_TS, recorded_at=HARNESS_TS,
        idempotency_key="idem:%s" % anchor,
        world_opportunity_ref="wopp:%s" % anchor,
        perception_ref="percept:%s" % anchor,
        world_revision_ref="wrev:%s:1" % anchor,
        causal_parent_refs=["src:%s" % anchor],
        provenance_refs=["src:%s" % anchor],
    )


def _opportunity_for(candidate_set):
    tag = uuid.uuid4().hex[:8]
    return ag1.DecisionOpportunity(
        opportunity_id="dopp:%s" % tag,
        trigger_kind="EXPLICIT_REEVALUATION",
        trigger_ref="evt:%s" % tag,
        candidate_set_ref=candidate_set.candidate_set_id,
        occurred_at=HARNESS_TS, observed_at=HARNESS_TS, recorded_at=HARNESS_TS,
        causal_parent_refs=["src:%s" % tag],
        idempotency_key="idem:%s" % tag,
        status=ag1.OPPORTUNITY_STATUS_OPEN,
        current_activity_ref=None,
    )


class _Harness:
    """A real AG-1 service over a fresh store in the fixture root, plus a stub cognition adapter."""

    def __init__(self, outputs, *, max_schema_retries=0):
        FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = FIXTURE_ROOT / ("run-" + uuid.uuid4().hex[:12])
        self.store_root = self.root / "agency"
        self.store = ag1.AgencyDecisionStore(self.store_root)
        self.lease = ac.ActivityAuthorityLease(self.root)
        self.lease.acquire()
        self.capability = ag1.AgencyDecisionAuthority.issue_capability(
            self.lease, caller_module="ag1_test_harness", store_root=self.store_root,
            namespace=ag1.NAMESPACE_ISOLATED_TEST,
        )
        self.adapter = _StubCognition(outputs)
        self.service = ag1.AgencyDecisionService(
            store=self.store, lease=self.lease, capability=self.capability,
            cognition_adapter=self.adapter, max_schema_retries=max_schema_retries,
        )

    def decide(self):
        candidates = [_world_candidate()]
        candidate_set = ag0.CandidateSet(
            candidate_set_id="cset:schema", subject_id=ag0.GLOBAL_SUBJECT_ID,
            candidate_refs=[c.candidate_id for c in candidates],
            source_snapshot_refs=["src:snapshot"], observed_at=HARNESS_TS, built_at=HARNESS_TS,
        )
        return self.service.evaluate_opportunity(
            opportunity=_opportunity_for(candidate_set),
            candidate_set=candidate_set,
            candidates=candidates,
            body_capacity_state="AVAILABLE",
            device_availability_state="AVAILABLE",
            resource_availability_state="AVAILABLE",
            privacy_availability_state="AVAILABLE",
            runtime_load_state="AVAILABLE",
        )

    def journal_payloads(self):
        return [entry["payload"] for entry in self.store.read_journal()]

    def journal_text(self):
        return self.store.journal_path.read_text(encoding="utf-8")

    def close(self):
        # Releasing the flock is lease lifecycle, not fixture cleanup: nothing is deleted.
        self.lease.release()


@POSIX_ONLY
class FailedAttemptPersistenceTest(unittest.TestCase):
    """The gap behind the undiagnosable real attempt: the refusal never reached a file."""

    def setUp(self):
        self.harness = None

    def tearDown(self):
        if self.harness is not None:
            self.harness.close()

    def _run(self, outputs, **kwargs):
        self.harness = _Harness(outputs, **kwargs)
        return self.harness.decide()

    def test_a_refused_answer_is_persisted_with_its_field_and_rule(self):
        result = self._run([_variant(confidence=0.9)])

        # The behaviour that was already correct: no fabrication, same class, no DecisionRecord.
        self.assertIsNone(result["decision_record"])
        self.assertEqual("DECISION_ATTEMPT_FAILED:INVALID_SCHEMA", result["failure_class"])
        self.assertEqual("decision_output", result["schema_refusal"]["field"])
        self.assertEqual("UNKNOWN_TOP_LEVEL_KEYS", result["schema_refusal"]["rule"])

        payload = self.harness.journal_payloads()[-1]
        self.assertEqual("DECISION_ATTEMPT_FAILED", payload["event_type"])
        refusal = payload["schema_refusal"]
        self.assertEqual("decision_output", refusal["field"])
        self.assertEqual("UNKNOWN_TOP_LEVEL_KEYS", refusal["rule"])
        self.assertEqual(1, refusal["unknown_key_count"])
        self.assertEqual(["confidence"], refusal["unknown_key_alias_hits"])

        # And nothing about the historical attempt record changed: no refusal column was added.
        state = self.harness.store.load_state()
        self.assertEqual({}, state["decisions_by_id"])
        for attempt in state["attempts_by_id"].values():
            self.assertNotIn("schema_refusal", attempt)
            self.assertEqual("DECISION_ATTEMPT_FAILED:INVALID_SCHEMA", attempt["failure_class"])

    def test_nothing_the_model_wrote_reaches_the_journal_file(self):
        self._run([_variant(**{SECRET_KEY: SECRET_VALUE})])
        blob = self.harness.journal_text()
        self.assertIn("schema_refusal", blob)
        self.assertNotIn(SECRET_KEY, blob)
        self.assertNotIn(SECRET_VALUE, blob)

    def test_a_hostile_value_cannot_smuggle_text_into_a_refusal_record(self):
        """A key-only whitelist is not enough: the *values* have to be re-derived too."""

        # 1. Nothing nameable at all -> no record is written (an all-placeholder record is noise).
        unrunnable = {
            "failure_class": SECRET_VALUE, "field": SECRET_VALUE, "rule": SECRET_VALUE,
            "value_type": SECRET_VALUE, "unknown_key_count": SECRET_VALUE,
            SECRET_KEY: SECRET_VALUE,
        }
        self.assertIsNone(ag1.sanitize_schema_refusal(unrunnable))

        # 2. A real class + hostile everything-else -> fixed words, never the injected text.
        hostile = {
            "failure_class": ag1.FAILURE_CLASS_INVALID_SCHEMA,
            "field": SECRET_VALUE, "rule": SECRET_VALUE, "value_type": SECRET_VALUE,
            "unknown_key_count": SECRET_VALUE,
            "unknown_key_alias_hits": [SECRET_VALUE, "confidence"],
            "unknown_key_shapes": {SECRET_KEY: SECRET_VALUE, "ascii_identifier": 2},
            SECRET_KEY: SECRET_VALUE,
        }
        clean = ag1.sanitize_schema_refusal(hostile)
        self.assertEqual(
            {"failure_class": ag1.FAILURE_CLASS_INVALID_SCHEMA,
             "field": "unknown", "rule": "UNCLASSIFIED",
             "unknown_key_alias_hits": ["confidence"],
             "unknown_key_shapes": {"ascii_identifier": 2}},
            clean,
        )
        built = ag1.failure_journal_payload(
            opportunity_id="dopp:hostile", attempt_id="datt:hostile",
            failure_class=ag1.FAILURE_CLASS_INVALID_SCHEMA, failure_ref="dfail:hostile",
            model_run_ref=None, schema_detail=hostile,
        )
        unbuilt = ag1.failure_journal_payload(
            opportunity_id="dopp:hostile", attempt_id="datt:hostile",
            failure_class=ag1.FAILURE_CLASS_INVALID_SCHEMA, failure_ref="dfail:hostile",
            model_run_ref=None, schema_detail=unrunnable,
        )
        for blob in (json.dumps(clean, ensure_ascii=False),
                     json.dumps(built, ensure_ascii=False),
                     json.dumps(unbuilt, ensure_ascii=False)):
            self.assertNotIn(SECRET_VALUE, blob)
            self.assertNotIn(SECRET_KEY, blob)
        self.assertNotIn("schema_refusal", unbuilt)

    def test_a_retry_that_ends_in_unparsable_text_carries_no_stale_refusal(self):
        """The second answer was not a schema choice at all: it must not inherit the first one."""

        result = self._run([_variant(confidence=0.9), "not a json object at all"],
                           max_schema_retries=1)
        self.assertEqual("DECISION_ATTEMPT_FAILED:INVALID_JSON", result["failure_class"])
        self.assertNotIn("schema_refusal", self.harness.journal_payloads()[-1])

    def test_a_retry_that_ends_in_a_refusal_reports_only_its_own_answer(self):
        result = self._run(["not a json object at all", _variant(deferred_candidate_ids="cand:x")],
                           max_schema_retries=1)
        self.assertEqual("DECISION_ATTEMPT_FAILED:INVALID_SCHEMA", result["failure_class"])
        refusal = self.harness.journal_payloads()[-1]["schema_refusal"]
        self.assertEqual("deferred_candidate_ids", refusal["field"])
        self.assertEqual("NOT_A_LIST", refusal["rule"])
        self.assertEqual("string", refusal["value_type"])


def main():
    return unittest.main(module=__name__, exit=False, verbosity=2)


if __name__ == "__main__":
    sys.exit(0 if main().result.wasSuccessful() else 1)

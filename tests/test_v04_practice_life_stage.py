"""v0.4 C: experiences -> a bounded review option -> a real review -> a scoped method -> a trial.

What this file pins down (and nothing else)
------------------------------------------
* **pure -- the review option**: a review option exists only when B's practice workspace is wired
  *and* A's Memory really returns an experience; it carries exactly those experience ids + a
  bounded excerpt, a different experience set is a *new* option, and no experience means no option
  at all (never an option that promises to write about a past nobody can read).
* **pure -- the seam refuses honestly**: an unwired workspace answers ``NOT_WIRED`` / nothing (never
  an empty-but-successful read), a service missing a documented method is ``UNUSABLE``, a Memory
  that cannot recall says so in ``last_error``, and a trial without readable evidence is refused
  while ``UNKNOWN`` stays available.
* **pure -- method handles**: ``method_ref`` is deterministic, bounded, and only ever resolved
  inside the round's own read set (an invented handle resolves to nothing).
* **pure -- the content writer**: a ``practice_context`` half rides into the prompt next to (and
  separate from) ``research_context``; the earlier bare research mapping keeps its old meaning; and
  ``no_change`` stays a legal answer that writes nothing.
* **pure -- quiet state**: the fingerprint is built from ids / heads / state words only, so a new
  method head or a state change is a new situation while a clock tick is not -- and no text enters.
* **pure -- AG-1's practice contract**: a method trial and a method proposal are validated on their
  own terms (bounded keys, the same practice, a legal outcome, non-empty evidence refs), and a
  committed DecisionRecord round-trips both without losing them.
* **the loop** (POSIX only -- the life modules import ``fcntl``): one whole practice experience on
  the **real** owners -- Life Supply on its own Unix socket, a real M0 Memory database, AG-1's raw
  JSON validation, AG-2 adoption, AR-0's managed-artifact adapter -- where a confirmed work result
  becomes a recallable experience, that experience makes a review option appear, the Decision
  selects it, B's service creates the review and AR-0 writes it, a scoped method draft is saved
  from the Decision's own proposal, the *next* writing prompt really carries that method's
  instruction/scope/uncertainty, and the follow-up result is tied to the method version with an
  evidence-backed trial (HELPED -> TRIAL, a counterexample -> CONTESTED).  Plus: deleting one
  experience through the existing control ledger withdraws the derived text from every context.

Stand-ins, named as such
------------------------
* ``_ScriptedModelClient`` -- a stand-in for the product model: it answers one fixed JSON edit and
  records every ``user_prompt``.  No credential is read and no network is used.
* ``_PracticeCognition`` -- a stand-in for the decision model: it answers a raw JSON object, which
  real AG-1 validates.  It never builds a Decision itself, and every scripted answer is marked as
  such in this docstring: **the real Gemini behaviour is NOT exercised here.**
* ``_FakeService`` / ``_FakeMemory`` in the pure tests -- stand-ins for B's envelope shape and A's
  read methods only.

Fixtures live under ``/tmp/nyairo-v04-practice-fixtures`` and are **kept** (no TemporaryDirectory,
no unlink): a failing run must leave its state on disk.

Run (POSIX host)::

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_v04_practice_life_stage
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

import life_practice_context as lpc  # noqa: E402
import life_project_options as lpo  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

TS0 = "2026-11-10T09:00:00+00:00"
EXPERIENCE_ID = "exp:1111111111111111"
EXPERIENCE_TEXT = "写完第三章并提交了版本 2"
NOTE = "为什么写到第三章就卡住？"
REVIEW_BODY = "回顾：第三章卡住是因为没先写结论。"
METHOD_INSTRUCTION = "先写一句结论，再补三段理由"
METHOD_SCOPE = "只在这个长文项目里"
METHOD_UNCERTAINTY = "不知道短文本是否也适用"

FIXTURE_ROOT = Path(os.environ.get("NYAIRO_PRACTICE_FIXTURE_ROOT")
                    or "/tmp/nyairo-v04-practice-fixtures")


def fixture_dir(name: str) -> Path:
    """A per-run, per-case directory under the kept fixture root (never cleaned up)."""

    path = FIXTURE_ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{name}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def experience_row(**overrides):
    row = {"experience_id": EXPERIENCE_ID, "occurred_at": TS0,
           "excerpt": EXPERIENCE_TEXT, "artifact_ref": "artifact:aa",
           "version_ref": "artifactversion:bb", "outcome_kind": "SIGNIFICANT_PROGRESS"}
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------------------------
# pure: the review option
# ---------------------------------------------------------------------------------------------


class PracticeReviewOptionTest(unittest.TestCase):
    def test_an_option_exists_only_when_the_workspace_and_an_experience_are_really_there(self):
        idle = lpo.build_options(experiences=[experience_row()], practice_available=False)
        self.assertEqual(idle, [])
        empty = lpo.build_options(experiences=[], practice_available=True)
        self.assertEqual(empty, [])
        wired = lpo.build_options(experiences=[experience_row()], practice_available=True)
        self.assertEqual([row["kind"] for row in wired], [lpo.KIND_REVIEW])
        self.assertTrue(wired[0]["review"])
        self.assertEqual(wired[0]["experiences"][0]["experience_id"], EXPERIENCE_ID)

    def test_the_option_carries_the_real_experience_and_says_it_is_a_draft(self):
        wired = lpo.build_options(experiences=[experience_row()], practice_available=True)
        option = wired[0]
        self.assertIn(EXPERIENCE_TEXT, option["seed_text"])
        self.assertEqual([ref for ref in option["source_refs"]],
                         ["experience:%s" % EXPERIENCE_ID])
        self.assertTrue(option["uncertainty"], option)
        self.assertTrue(option["scope"], option)

    def test_a_different_experience_is_a_new_option_not_a_reused_one(self):
        first = lpo.build_options(experiences=[experience_row()], practice_available=True)[0]
        second = lpo.build_options(experiences=[experience_row(experience_id="exp:2222")],
                                   practice_available=True)[0]
        self.assertNotEqual(first["option_ref"], second["option_ref"])

    def test_a_question_option_survives_beside_a_review_option(self):
        options = lpo.build_options(
            questions=[{"marker_id": "unclear:aa", "note": NOTE}],
            experiences=[experience_row()], practice_available=True, limit=2)
        kinds = [row["kind"] for row in options]
        self.assertIn(lpo.KIND_REVIEW, kinds)
        self.assertIn(lpo.KIND_QUESTION, kinds)


# ---------------------------------------------------------------------------------------------
# pure: the seam's refusals
# ---------------------------------------------------------------------------------------------


class _FakeMemory:
    """A's read surface, standing in for ``LifeMemoryService`` (envelope shape only)."""

    def __init__(self, rows=None, *, ok=True, reason=None):
        self.rows = list(rows or ())
        self.ok = ok
        self.reason = reason
        self.reads = []

    def list_experiences(self, *, query="", limit=20, since=None):
        if not self.ok:
            return {"ok": False, "status": "UNBOUND", "reason": self.reason, "data": {}}
        return {"ok": True, "status": "READY",
                "data": {"experiences": self.rows[:limit]}}

    def read_experience(self, experience_id):
        self.reads.append(experience_id)
        for row in self.rows:
            if row.get("experience_id") == experience_id:
                return {"ok": True, "status": "READY", "data": {"experience": row}}
        return {"ok": False, "status": "NOT_FOUND", "data": {"experience": None}}


class _FakeService:
    """B's documented surface, standing in for ``PersonalPracticeService`` (envelope shape only)."""

    def __init__(self, *, methods=None, reviews=None, trial=None):
        self.methods = list(methods or ())
        self.reviews = list(reviews or ())
        self.trial = trial
        self.calls = []
        self.create_calls = []
        self.trial_calls = []

    def list_reviews(self, *, limit=12):
        return {"ok": True, "status": "OK", "data": {"projects": self.reviews[:limit]}}

    def read_review(self, artifact_id):
        return {"ok": True, "status": "OK",
                "data": {"project": {"artifact_id": artifact_id, "title": "回顾"},
                         "kind": "review", "body": REVIEW_BODY, "content_available": True,
                         "question": NOTE, "experience_basis": "experience",
                         "experience_ids": [EXPERIENCE_ID], "experience_checks": [],
                         "governance": {"blocked": False}}}

    def create_review(self, *, question, experience_ids=(), title=None, operation_key=None):
        self.create_calls.append(dict(question=question, experience_ids=list(experience_ids),
                                      title=title, operation_key=operation_key))
        return {"ok": True, "status": "applied",
                "data": {"project": {"artifact_id": "artifact:review01", "title": title},
                         "operation_key": operation_key}}

    def list_methods(self, *, query="", limit=12):
        self.calls.append(("list_methods", query))
        rows = self.methods
        if query:
            rows = [row for row in rows if query in json.dumps(row, ensure_ascii=False)]
        return {"ok": True, "status": "OK", "data": {"projects": rows[:limit]}}

    def read_method(self, artifact_id):
        return {"ok": True, "status": "OK",
                "data": {"project": {"artifact_id": artifact_id, "title": "做法"},
                         "kind": "method", "body": "做法", "content_available": True,
                         "instruction": METHOD_INSTRUCTION, "scope": METHOD_SCOPE,
                         "uncertainty": METHOD_UNCERTAINTY, "method_state": "TENTATIVE",
                         "experience_ids": [EXPERIENCE_ID], "review_refs": [], "trials": [],
                         "governance": {"blocked": False}}}

    def create_method(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        return {"ok": True, "status": "applied",
                "data": {"project": {"artifact_id": "artifact:method01", "title": kwargs.get("title")},
                         "method_state": "TENTATIVE"}}

    def record_trial(self, *, artifact_id, outcome, detail="", experience_ids=(),
                     expected_head=None):
        self.trial_calls.append(dict(artifact_id=artifact_id, outcome=outcome,
                                     experience_ids=list(experience_ids),
                                     expected_head=expected_head))
        return {"ok": True, "status": "applied",
                "data": {"artifact_id": artifact_id, "outcome": outcome,
                         "method_state": "TRIAL", "evidence_usable": True,
                         "trials_recorded": 1, "status": "applied"}}


class PracticeSeamRefusalTest(unittest.TestCase):
    def test_an_unwired_seam_reads_nothing_and_says_why(self):
        seam = lpc.PracticeSeam(None)
        self.assertFalse(seam.available())
        self.assertEqual(seam.availability()["status"], "NOT_WIRED")
        self.assertEqual(seam.experiences(), [])
        self.assertEqual(seam.last_error, "MEMORY_NOT_WIRED")
        self.assertEqual(seam.methods(), [])
        self.assertEqual(seam.last_error, lpc.REASON_SERVICE_MISSING)
        self.assertIsNone(seam.writing_context("artifact:x"))
        with self.assertRaises(lpc.PracticeSeamError):
            seam.create_review(question="q", experience_ids=[EXPERIENCE_ID])

    def test_a_service_missing_a_documented_method_is_unusable_rather_than_empty(self):
        partial = SimpleNamespace(list_reviews=lambda **k: {"ok": True, "data": {}})
        seam = lpc.PracticeSeam(partial)
        self.assertEqual(seam.availability()["status"], "UNUSABLE")
        self.assertIn("create_review", seam.availability()["missing"])
        self.assertEqual(seam.methods(), [])

    def test_a_memory_that_cannot_recall_is_named_not_read_as_empty(self):
        seam = lpc.PracticeSeam(_FakeService(), memory=_FakeMemory(ok=False, reason="MEMORY_BLOCKED"))
        self.assertEqual(seam.experiences(), [])
        self.assertEqual(seam.last_error, "MEMORY_BLOCKED")

    def test_a_trial_needs_readable_evidence_and_unknown_stays_available(self):
        service = _FakeService()
        memory = _FakeMemory([experience_row()])
        seam = lpc.PracticeSeam(service, memory=memory)
        seam.methods()
        with self.assertRaises(lpc.PracticeSeamError) as caught:
            seam.record_trial(artifact_id="artifact:m1", outcome="HELPED",
                              experience_ids=["exp:missing"],
                              expected_head="artifactversion:1")
        self.assertEqual(caught.exception.reason_code, lpc.REASON_NO_EVIDENCE)
        unknown = seam.record_trial(artifact_id="artifact:m1", outcome="UNKNOWN",
                                    expected_head="artifactversion:1")
        self.assertEqual(unknown["outcome"], "UNKNOWN")
        self.assertEqual(service.trial_calls[-1]["outcome"], "UNKNOWN")
        helped = seam.record_trial(artifact_id="artifact:m1", outcome="HELPED",
                                   experience_ids=[EXPERIENCE_ID],
                                   expected_head="artifactversion:1")
        self.assertTrue(helped["evidence_usable"])
        self.assertEqual(helped["method_state"], "TRIAL")


class PracticeMethodHandleTest(unittest.TestCase):
    def test_the_handle_is_deterministic_bounded_and_never_an_owner_id(self):
        first = lpc.method_ref_for("artifact:abcdef0123456789")
        self.assertEqual(first, lpc.method_ref_for("abcdef0123456789"))
        self.assertTrue(lpc.is_method_ref(first))
        self.assertNotIn("abcdef0123456789", first)
        self.assertFalse(lpc.is_method_ref("artifact:abcdef"))
        self.assertFalse(lpc.is_method_ref(""))


class _SeamMethodStub:
    def __init__(self, rows):
        self.rows = rows

    def available(self):
        return True

    def methods(self, *, query="", limit=None):
        return list(self.rows)


class PracticeQuietStateTest(unittest.TestCase):
    def test_a_new_head_or_a_state_change_moves_the_parts_and_no_text_enters(self):
        service = _FakeService(methods=[
            {"artifact_id": "artifact:m1", "title": "做法", "instruction": METHOD_INSTRUCTION,
             "scope": METHOD_SCOPE, "uncertainty": METHOD_UNCERTAINTY,
             "method_state": "TENTATIVE", "head_version_id": "artifactversion:1"}])
        seam = lpc.PracticeSeam(service, memory=_FakeMemory([experience_row()]))
        parts = seam.fingerprint_parts()
        self.assertIn("artifact:m1", parts["practice_methods"])
        self.assertIn("artifactversion:1", parts["practice_methods"])
        self.assertNotIn(EXPERIENCE_TEXT, json.dumps(parts, ensure_ascii=False))
        self.assertNotIn(METHOD_INSTRUCTION, json.dumps(parts, ensure_ascii=False))
        self.assertNotIn(TS0, parts["practice_experiences"].split("@")[0])

        service.methods[0]["head_version_id"] = "artifactversion:2"
        service.methods[0]["method_state"] = "CONTESTED"
        moved = seam.fingerprint_parts()
        self.assertNotEqual(parts["practice_methods"], moved["practice_methods"])


# ---------------------------------------------------------------------------------------------
# pure: the content writer carries practice context (never as memory)
# ---------------------------------------------------------------------------------------------


class _ScriptedModelClient:
    """The product model, scripted: one fixed JSON answer, plus every prompt it was given."""

    def __init__(self, payload=None) -> None:
        self.payload = payload or {"kind": "append_paragraph", "text": REVIEW_BODY,
                                   "self_note": "stub: 写一段"}
        self.prompts: list = []
        self.call_count = 0

    def complete(self, *, system_prompt: str, user_prompt: str, timeout_s: float = 0.0,
                 max_output_tokens: int = 0):
        self.call_count += 1
        self.prompts.append({"system": system_prompt, "user": user_prompt})
        from agency_model_client import ModelCallResult

        return ModelCallResult(purpose="chat", provider="stub", model="stub", text=json.dumps(
            self.payload, ensure_ascii=False), latency_ms=1)


class ModelWriterPracticeContextTest(unittest.TestCase):
    def _point(self, content: str = "# 手记\n\n第一段。\n"):
        from life_artifact_port import ArtifactResumePoint

        return ArtifactResumePoint(artifact_id="artifact:aa", title="手记", content=content,
                                   head_version_id="artifactversion:1", version_number=1,
                                   lifecycle="ACTIVE", revision=1)

    def _writer(self, client):
        from model_continuation_writer import ModelContinuationWriter

        return ModelContinuationWriter(client, max_new_chars=200)

    def test_practice_context_rides_into_the_prompt_beside_research_context(self):
        client = _ScriptedModelClient()
        writer = self._writer(client)
        writer.next_content(self._point(), observed_at=TS0, context={
            "research_context": {"kind": "research_document_context", "own_research": {}},
            "practice_context": {"kind": "practice_document_context",
                                 "methods": [{"method_ref": "method:0123456789abcdef",
                                              "instruction": METHOD_INSTRUCTION,
                                              "scope": METHOD_SCOPE,
                                              "uncertainty": METHOD_UNCERTAINTY}]}})
        payload = json.loads(client.prompts[-1]["user"])
        self.assertIn("research_context", payload)
        self.assertEqual(payload["practice_context"]["methods"][0]["instruction"],
                         METHOD_INSTRUCTION)
        self.assertEqual(payload["practice_context"]["methods"][0]["scope"], METHOD_SCOPE)
        self.assertEqual(payload["practice_context"]["methods"][0]["uncertainty"],
                         METHOD_UNCERTAINTY)

    def test_the_earlier_bare_research_mapping_keeps_its_old_meaning(self):
        client = _ScriptedModelClient()
        writer = self._writer(client)
        writer.next_content(self._point(), observed_at=TS0,
                            context={"kind": "research_document_context", "own_research": {}})
        payload = json.loads(client.prompts[-1]["user"])
        self.assertEqual(payload["research_context"]["kind"], "research_document_context")
        self.assertNotIn("practice_context", payload)

    def test_no_change_is_still_legal_and_writes_nothing(self):
        from model_continuation_writer import ModelNoChange

        client = _ScriptedModelClient({"kind": "no_change", "text": ""})
        writer = self._writer(client)
        with self.assertRaises(ModelNoChange):
            writer.next_content(self._point(), observed_at=TS0,
                                context={"practice_context": {"methods": []}})
        self.assertEqual(writer.no_change_count, 1)


# ---------------------------------------------------------------------------------------------
# pure: AG-1's practice contract
# ---------------------------------------------------------------------------------------------

@POSIX_ONLY  # AG-1 imports fcntl
class DecisionPracticeContractTest(unittest.TestCase):
    def setUp(self):
        import agency_decision_ag1 as ag1

        self.ag1 = ag1
        self.ref = lpc.method_ref_for("artifact:abcdef0123456789")

    def test_a_trial_must_name_the_same_practice_and_a_legal_outcome(self):
        with self.assertRaises(self.ag1.DecisionValidationError):
            self.ag1.validate_method_trial({"method_ref": "method:other", "outcome": "HELPED"},
                                           method_ref=self.ref)
        with self.assertRaises(self.ag1.DecisionValidationError):
            self.ag1.validate_method_trial({"method_ref": self.ref, "outcome": "GREAT"},
                                           method_ref=self.ref)
        with self.assertRaises(self.ag1.DecisionValidationError):
            self.ag1.validate_method_trial({"method_ref": self.ref, "outcome": "HELPED",
                                            "self_note": "sneaky"}, method_ref=self.ref)
        clean = self.ag1.validate_method_trial(
            {"method_ref": self.ref, "outcome": "not_helped", "detail": " 没起作用 ",
             "experience_ids": [EXPERIENCE_ID, EXPERIENCE_ID]}, method_ref=self.ref)
        self.assertEqual(clean["outcome"], "NOT_HELPED")
        self.assertEqual(clean["experience_ids"], [EXPERIENCE_ID])

    def test_a_proposal_keeps_only_its_own_bounded_keys(self):
        clean = self.ag1.validate_method_proposal({
            "title": " 先写结论 ", "instruction": METHOD_INSTRUCTION, "scope": METHOD_SCOPE,
            "uncertainty": METHOD_UNCERTAINTY, "experience_ids": [EXPERIENCE_ID]})
        self.assertEqual(clean["title"], "先写结论")
        with self.assertRaises(self.ag1.DecisionValidationError):
            self.ag1.validate_method_proposal({"title": "x", "instruction": "y",
                                               "belief": "I am a writer"})
        with self.assertRaises(self.ag1.DecisionValidationError):
            self.ag1.validate_method_proposal({"title": "x"})

    def test_a_committed_record_round_trips_the_practice_it_named(self):
        proposal = self.ag1.validate_method_proposal(
            {"title": "先写结论", "instruction": METHOD_INSTRUCTION, "scope": METHOD_SCOPE})
        record = self.ag1.DecisionRecord(
            decision_id="dec:0123456789abcdef", revision=1, subject_id="chiyo.global",
            decision_opportunity_ref="dopp:0123456789abcdef",
            candidate_set_ref="cset:0123456789abcdef", candidate_refs=(),
            context_snapshot_ref="dctx:0123456789abcdef", hard_constraint_result_refs=(),
            decision="NO_ACTION", reason_codes=("NO_ACTION_CHOSEN",),
            decision_route="COGNITIVE_REQUIRED", policy_version="v1",
            snapshot_fingerprint="sfp:0123456789abcdef",
            created_at="2026-11-10T00:00:00+00:00", method_ref=self.ref,
            method_trial={"method_ref": self.ref, "outcome": "UNKNOWN"},
            method_proposal=proposal)
        rebuilt = self.ag1.DecisionRecord.from_dict(record.to_dict())
        self.assertEqual(rebuilt.method_ref, self.ref)
        self.assertEqual(rebuilt.method_trial["outcome"], "UNKNOWN")
        self.assertEqual(rebuilt.method_proposal["instruction"], METHOD_INSTRUCTION)

    def test_a_record_that_proposed_no_practice_keeps_none_after_round_trip(self):
        record = self.ag1.DecisionRecord(
            decision_id="dec:fedcba9876543210", revision=1, subject_id="chiyo.global",
            decision_opportunity_ref="dopp:fedcba9876543210",
            candidate_set_ref="cset:fedcba9876543210", candidate_refs=(),
            context_snapshot_ref="dctx:fedcba9876543210", hard_constraint_result_refs=(),
            decision="NO_ACTION", reason_codes=("NO_ACTION_CHOSEN",),
            decision_route="COGNITIVE_REQUIRED", policy_version="v1",
            snapshot_fingerprint="sfp:fedcba9876543210",
            created_at="2026-11-10T00:00:00+00:00")
        self.assertIsNone(record.method_proposal)
        self.assertIsNone(self.ag1.DecisionRecord.from_dict(record.to_dict()).method_proposal)


# ---------------------------------------------------------------------------------------------
# the loop: real Supply + real M0 + AG-1 raw JSON + AG-2 + AR-0
# ---------------------------------------------------------------------------------------------

try:  # the world fixture base is pure python; it is only *used* on POSIX below
    from components.worldgen.tests._setup import WorldGenTestCase as _WorldGenBase
except Exception:  # pragma: no cover - fixture tree absent
    _WorldGenBase = unittest.TestCase


class _PracticeCognition:
    """The decision model, scripted: it answers a raw JSON object and nothing else.

    Real AG-1 validates it and real AG-2 adopts it; it never builds a Decision itself.  ``script``
    is the exact answer for the next call (a test sets it), otherwise a legal NO_ACTION is given.
    """

    model_id = "stub:practice-chain"
    model_version = "practice-chain-v1"

    def __init__(self) -> None:
        self.call_count = 0
        self.requests: list = []
        self.script: dict | None = None

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.call_count += 1
        self.requests.append(request)
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]
        if self.script is not None:
            raw = dict(self.script)
            raw.setdefault("reason_codes", reason)
            self.script = None
        else:
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": reason, "short_rationale": "stub: nothing to do",
                   "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:practice:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


@POSIX_ONLY
class PracticeLifeChainTest(_WorldGenBase):
    """One whole practice experience on the real owners.

    Real: Life Supply (own Unix socket), a real M0 Memory database + the admission consumer,
    B's practice workspace, AG-0 candidates, AG-1 constraints/raw-JSON validation, AG-2 adoption,
    AR-0's managed-artifact adapter.  Scripted: the decision answer, the content answer -- the real
    model's own autonomy is *not* claimed here.
    """

    def setUp(self) -> None:
        super().setUp()
        import tests.test_one_day_life as fx

        self._fx = fx
        self._runtimes: list = []
        self.services = fx.Services(Path(self.temp_dir(prefix="practice-svc-")))
        self.addCleanup(self.services.stop)

    def tearDown(self) -> None:
        for runtime in getattr(self, "_runtimes", []):
            try:
                runtime.close()
            except Exception:  # a close failure must not hide the test result
                pass
        self._runtimes = []
        super().tearDown()

    # -- the fixture instance -----------------------------------------------------------

    def _chain(self, name: str = "chain"):
        from unittest import mock

        import autonomous_life_assembly as assembly
        import life_artifact_port as lap
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp
        import managed_artifact_action_adapter as maaa
        import one_day_life as odl
        from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
        from life_experience_port import LifeExperiencePort
        from model_continuation_writer import ModelContinuationWriter
        from components.worldgen.instance import init_instance
        from components.worldgen.tests._setup import make_valid_docs

        package_dir = self.temp_package(make_valid_docs(seed=929711, theme="coastal_town"),
                                        prefix="practice-pkg-")
        world_home = Path(self.temp_dir(prefix="practice-world-")) / "world-home"
        init_instance(package_dir, world_home)
        instance = Path(self.temp_dir(prefix="practice-inst-")) / "instance"
        with mock.patch.dict(os.environ, self._fx.ENV_GATES):
            prepared = odl.prepare(instance=instance, supply_socket=self.services.supply_path,
                                   governance_socket=self.services.gov_path,
                                   subject="test:practice", title="Practice fixture",
                                   world_package=package_dir, create_private_items=True)
        self.assertTrue(prepared.get("ok"), prepared)
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        memory_dir = Path(self.temp_dir(prefix="practice-memory-"))
        evidence_db = memory_dir / "memory.sqlite3"
        controls_db = memory_dir / "controls.sqlite3"
        env.update({
            "NYAIRO_LIFE_SUPPLY_SOCKET": str(self.services.supply_path),
            "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
            "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
            "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
            # The instance's own Memory binding (A's names, exactly as the resident reads them).
            "NYA02_MEMORY_DB": str(evidence_db),
            "NYA02_MEMORY_CONTROLS_DB": str(controls_db),
            "NYA02_MEMORY_SUBJECT": "chiyo",
        })
        if data.get("private_items_artifact_id"):
            env.setdefault("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                           str(data["private_items_artifact_id"]))
        cognition = _PracticeCognition()
        client = _ScriptedModelClient()
        writer = ModelContinuationWriter(client, max_new_chars=300)
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        memory_port = LifeExperiencePort(str(evidence_db), controls_db=str(controls_db),
                                         subject="chiyo")
        experience_consumer = MemoryAdmissionExperienceConsumer(port=memory_port, enabled=True)
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            odl.life_state_root(instance), kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter,
            experience_consumer=experience_consumer)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(instance),
            writer=writer, enabled=True)
        self.assertEqual(assembled["state"], assembly.READY, assembled)
        self.assertEqual(assembled["practice"]["status"], "READY", assembled["practice"])
        self._runtimes.append(runtime)
        return SimpleNamespace(runtime=runtime, assembled=assembled, cycle=assembled["cycle"],
                               cognition=cognition, client=client, writer=writer, env=env,
                               data=data, port=port, instance=instance, services=self.services,
                               memory_port=memory_port, consumer=experience_consumer,
                               evidence_db=evidence_db, controls_db=controls_db)

    # -- steps -------------------------------------------------------------------------

    def _write_question(self, fixture):
        import private_daily_items as pdi
    def _import_material(self, fixture):
        """One real imported material, so a question seed can become a research work she can start."""

        import research_workspace_service as rws
        # The instance's own Memory governance ledger, created exactly the way ``enable_instance_memory``
        # does it in production (an explicit control ledger beside the evidence DB).  Without it the
        # read path is governed-closed -- which is what the seam reports as BLOCKED, not "no past".
        from life_experience_port import life_conversation_id
        from memory_control import MemoryControlLedger

        MemoryControlLedger(str(fixture.controls_db), str(fixture.evidence_db), owner="chiyo",
                            conversation=life_conversation_id("chiyo")).initialize()
        service = rws.research_from_env(fixture.port, dict(fixture.env))
        self.assertIsNotNone(service, "B's research workspace did not resolve from the env")
        answer = service.import_material(
            title="田野笔记", text="第一章：村里的水井在旱季仍然有水。",
            source_kind="user_supplied", origin="操作者导入的文本（口述记录）",
            operation_key="op:material:practice:1")
        self.assertTrue(answer.get("ok"), answer)
        material = answer["data"]["material"]
        return str(material["artifact_id"]), str(material["source_revision"])

    def _write_question(self, fixture, material_id: str, revision: str):
        import private_daily_items as pdi

        items_id = str(fixture.data["private_items_artifact_id"])
        store = pdi.PrivateDailyItems(fixture.port, items_id,
                                      workspace_id=str(fixture.data.get("workspace_id") or ""),
                                      grant_id=str(fixture.data["artifact_grant_id"]))
        result = store.mark_unclear(source_id=material_id, source_revision=revision,
                                    note=NOTE, marker_id="unclear:practice-1",
                                    operation_key="op:unclear:practice:1")
        self.assertTrue(getattr(result, "ok", True), result)
        return store

    def _select_option(self, fixture, *, review: bool):
        """Script one START on the option this round really offers, and return its ref."""

        options = fixture.cycle.project_options()
        wanted = [row for row in options if bool(row.get("review")) is review]
        self.assertTrue(wanted, options)
        option_ref = str(wanted[0]["option_ref"])
        candidates = fixture.runtime.candidate_materializer.build_candidate_set(
            observed_at=TS0).candidate_refs
        selected = None
        for candidate_ref in candidates:
            payload = fixture.runtime.candidate_read.get_candidate(str(candidate_ref))
            if str((payload or {}).get("source_ref")) == option_ref:
                selected = str(candidate_ref)
                break
        self.assertIsNotNone(selected, "the option never became a candidate")
        fixture.cognition.script = {"decision": "START", "selected_candidate_id": selected,
                                    "deferred_candidate_ids": []}
        return option_ref

    def _end_foreground(self, fixture, *, tag: str):
        """Close the running Activity through the owner, so a *new* work can be adopted next.

        One foreground Activity at a time is AG-2's own rule, not a test convenience: a second
        START while she is mid-work is refused.  This exercises the owner's ABANDON path exactly
        as the loop does, and the round's own report is asserted to be a real outcome.
        """

        fixture.cognition.script = {"decision": "ABANDON", "selected_candidate_id": None,
                                    "deferred_candidate_ids": []}
        closed = fixture.cycle.evaluate_once(trigger_ref="evt:practice:end-%s" % tag, act=True)
        self.assertNotEqual(closed.get("outcome"), "DECISION_FAILED", closed)
        return closed

    # -- the chain ---------------------------------------------------------------------

    def test_a_confirmed_result_becomes_an_experience_and_her_own_review(self):
        fixture = self._chain("practice-review")
        material_id, revision = self._import_material(fixture)
        self._write_question(fixture, material_id, revision)

        first = fixture.cycle.project_options()
        self.assertTrue(first, "no option was formed at all")
        self.assertFalse([row for row in first if row.get("review")], first)

        # 2. She starts that project; AR-0 writes the version, and the *owner-confirmed* result
        #    reaches Memory through AG-2's handoff (with C's confirmed description).
        fixture.client.payload = {"kind": "append_paragraph", "text": "第一版正文。",
                                  "self_note": "stub: 写第一版"}
        self._select_option(fixture, review=False)
        written = fixture.cycle.evaluate_once(trigger_ref="evt:practice:1", act=True)
        self.assertTrue((written.get("project_created") or {}).get("artifact_id"),
                        {key: written.get(key) for key in
                         ("outcome", "detail", "decision", "selected_candidate_ref",
                          "adoption_status", "project_options", "step_path", "last_error",
                          "practice_error", "no_step")})
        handoff = written.get("experience_handoff") or {}
        self.assertTrue(handoff.get("handoff_id"), written)
        self.assertEqual(handoff.get("disposition"), "ACCEPT", written)
        self.assertTrue(handoff.get("confirmed_used"), handoff)

        practices = fixture.cycle.practice
        self.assertTrue(practices.memory_bound())
        experiences = practices.experiences()
        self.assertTrue(experiences, "the confirmed result never became recallable: %r" % (
            {"handoff": written.get("experience_handoff"),
             "handoff_error": written.get("experience_handoff_error"),
             "consumer": fixture.consumer.describe(),
             "memory_status": practices.memory.status()},))
        memory = practices.memory
        self.assertEqual(memory.status()["data"]["owner"], "chiyo")
        self.assertTrue(memory.status()["data"]["recall_ready"])
        experience_id = experiences[0]["experience_id"]
        self.assertTrue(memory.read_experience(experience_id)["ok"])
        # The sentence is about what the owner holds, not "some artifact moved".
        # The sentence is about what the owner holds, not "some artifact moved": the row's own text
        # carries the bounded description of the version that was really written.
        full = memory.read_experience(experience_id)["data"]["experience"]["text"]
        self.assertIn("第一版正文", full)
        # The research Activity she just started still holds the foreground: close it through the
        # owner before a *new* work can be started (one foreground Activity is AG-2's own rule).
        self._end_foreground(fixture, tag="review")

        # 3. Now a review option exists, and the decision really sees the experience + its refs.
        option_ref = self._select_option(fixture, review=True)
        context = fixture.cycle.decision_situation_context(now=TS0)
        context_ids = [row["experience_id"] for row in context["practice"]["experiences"]]
        self.assertIn(experience_id, context_ids)
        review_option = [row for row in context["project_options"] if row["review"]][0]
        self.assertEqual(review_option["option_ref"], option_ref)
        self.assertIn(experience_id,
                      [row["experience_id"] for row in review_option["experiences"]])

        # 4. Choosing it: B's workspace creates the review on the real owner, AR-0 writes it.
        fixture.client.payload = {"kind": "append_paragraph", "text": REVIEW_BODY,
                                  "self_note": "stub: 写具体回顾"}
        out = fixture.cycle.evaluate_once(trigger_ref="evt:practice:2", act=True)
        created = out.get("review_created") or {}
        self.assertTrue(created.get("artifact_id"), out)
        review_id = str(created["artifact_id"])
        receipt = out.get("step_receipt") or {}
        self.assertEqual(receipt.get("artifact_id"), review_id)
        self.assertEqual(int(receipt.get("version_number") or 0), 2)
        document = fixture.port.read_artifact(review_id).get("content") or ""
        self.assertIn(REVIEW_BODY, document)
        parsed = lpc.practice_module().parse_practice_content(document)
        self.assertEqual(parsed["kind"], "review")
        self.assertEqual(parsed["experience_basis"], "experience")
        self.assertIn(experience_id, parsed["experience_ids"])
        # The writer's prompt really carried the review's own subject (document context only).
        prompt = json.loads(fixture.client.prompts[-1]["user"])
        self.assertIn(experience_id,
                      prompt["practice_context"]["own_review"]["experience_ids"])

    def test_a_scoped_method_is_saved_then_really_used_and_evaluated(self):
        fixture = self._chain("practice-method")
        material_id, revision = self._import_material(fixture)
        self._write_question(fixture, material_id, revision)

        # 1. Round one: a confirmed write happens, and the Decision *proposes* one scoped practice.
        #    A proposal with no evidence yet is legal -- it is a draft, not a belief.
        fixture.client.payload = {"kind": "append_paragraph", "text": "第一版正文。",
                                  "self_note": "stub: 写第一版"}
        self._select_option(fixture, review=False)
        fixture.cognition.script["method_proposal"] = {
            "title": "先写结论再补理由", "instruction": METHOD_INSTRUCTION,
            "scope": METHOD_SCOPE, "uncertainty": METHOD_UNCERTAINTY}
        first = fixture.cycle.evaluate_once(trigger_ref="evt:practice:1", act=True)
        ready = self.assert_created_method(fixture, first)
        method_id = str(ready["artifact_id"])
        method_ref = lpc.method_ref_for(method_id)
        self.assertEqual(ready["method_ref"], method_ref)
        self.assertEqual(ready["method_state"], "TENTATIVE")
        self.assertTrue(ready.get("decision_id"), ready)
        experience_id = fixture.cycle.practice.experiences()[0]["experience_id"]

        # Replaying the *same* Decision's save intent (the same owner operation key the cycle
        # derived) must return that draft, never mint a second one.
        import autonomous_life_cycle as alc

        replay_key = alc.AutonomousLifeCycle._method_operation_key(
            SimpleNamespace(decision_id=ready["decision_id"]),
            {"title": "先写结论再补理由", "instruction": METHOD_INSTRUCTION})
        try:
            replay = fixture.cycle.practice.create_method(
                title="先写结论再补理由", instruction=METHOD_INSTRUCTION, scope=METHOD_SCOPE,
                uncertainty=METHOD_UNCERTAINTY, operation_key=replay_key)
        except lpc.PracticeSeamError as exc:
            # The owner's own answer to a replayed key: already committed, nothing new written.
            self.assertIn("OPERATION_ALREADY_COMMITTED", str(exc))
        else:
            self.assertEqual(replay["artifact_id"], method_id, replay)
        self.assertEqual([row["method_ref"] for row in fixture.cycle.practice.methods(query="")],
                         [method_ref])

        # 2. Round two: the review round.  Its writing prompt really carries the saved practice
        #    (instruction / scope / uncertainty), and the Decision names the practice it follows --
        #    with an evidence-backed verdict on it.
        self._end_foreground(fixture, tag="review")
        option_ref = self._select_option(fixture, review=True)
        fixture.cognition.script.update({
            "method_ref": method_ref,
            "method_trial": {"method_ref": method_ref, "outcome": "HELPED",
                             "detail": "这次先写结论，确实顺一些",
                             "experience_ids": [experience_id]}})
        fixture.client.payload = {"kind": "append_paragraph", "text": REVIEW_BODY,
                                  "self_note": "stub: 写具体回顾"}
        out = fixture.cycle.evaluate_once(trigger_ref="evt:practice:2", act=True)
        created = out.get("review_created") or {}
        self.assertTrue(created.get("artifact_id"), out)
        review_id = str(created["artifact_id"])
        self.assertEqual((out.get("step_receipt") or {}).get("artifact_id"), review_id)
        prompt = json.loads(fixture.client.prompts[-1]["user"])
        practice_context = prompt.get("practice_context") or {}
        self.assertIn("methods", practice_context,
                      {"context": practice_context,
                       "seam_error": fixture.cycle.practice.last_error,
                       "round_error": fixture.cycle.last_practice_error,
                       "listed": fixture.cycle.practice.methods(query="")})
        methods = practice_context["methods"]
        self.assertEqual([row["method_ref"] for row in methods], [method_ref])
        self.assertEqual(methods[0]["instruction"], METHOD_INSTRUCTION)
        self.assertEqual(methods[0]["scope"], METHOD_SCOPE)
        self.assertEqual(methods[0]["uncertainty"], METHOD_UNCERTAINTY)
        self.assertIn("own_review", practice_context)
        usage = out.get("method_usage") or {}
        self.assertEqual(usage.get("method_ref"), method_ref)
        self.assertEqual(usage.get("artifact_id"), method_id)
        self.assertTrue(usage.get("version_ref"), usage)
        self.assertTrue(usage.get("consequence_refs"), usage)
        self.assertIn("artifactversion:", json.dumps(usage["consequence_refs"]))
        trial = out.get("method_trial") or {}
        self.assertEqual(trial.get("outcome"), "HELPED", out)
        self.assertTrue(trial.get("evidence_usable"), trial)
        self.assertEqual(trial.get("method_state"), "TRIAL")
        read_back = fixture.cycle.practice.read_method(method_id)
        self.assertEqual(read_back["method_state"], "TRIAL")
        self.assertEqual(len(read_back["trials"]), 1)
        self.assertTrue(read_back["trials"][0]["evidence_usable"])

        # 3. A counterexample is preserved rather than swallowed.  This is the *optional, explicit*
        #    evaluation action the round may take (no extra model call): one bounded trial through
        #    B's owner, against evidence A can still read -- never "the version was written, so it
        #    must have helped".
        counter = fixture.cycle.practice.record_trial(
            artifact_id=method_id, outcome="NOT_HELPED",
            detail="这次先写结论反而卡住", experience_ids=[experience_id],
            expected_head=read_back["head_version_id"])
        self.assertEqual(counter["outcome"], "NOT_HELPED")
        self.assertTrue(counter["evidence_usable"])
        self.assertEqual(counter["method_state"], "CONTESTED")
        read_back = fixture.cycle.practice.read_method(method_id)
        self.assertEqual(read_back["method_state"], "CONTESTED")
        self.assertEqual(read_back["counterexamples"], 1)
        # ... and an evaluation with no readable evidence is refused: UNKNOWN stays the honest word.
        with self.assertRaises(lpc.PracticeSeamError) as caught:
            fixture.cycle.practice.record_trial(
                artifact_id=method_id, outcome="HELPED", detail="没有证据也说有效",
                experience_ids=[], expected_head=read_back["head_version_id"])
        self.assertEqual(caught.exception.reason_code, lpc.REASON_NO_EVIDENCE)

    def test_a_deleted_experience_is_withdrawn_from_every_derived_context(self):
        from memory_control import MemoryControlLedger

        fixture = self._chain("practice-governance")
        material_id, revision = self._import_material(fixture)
        self._write_question(fixture, material_id, revision)
        fixture.client.payload = {"kind": "append_paragraph", "text": "第一版正文。",
                                  "self_note": "stub: 写第一版"}
        self._select_option(fixture, review=False)
        fixture.cycle.evaluate_once(trigger_ref="evt:practice:1", act=True)
        experience = fixture.cycle.practice.experiences()[0]
        experience_id, event_id = experience["experience_id"], experience["event_id"]

        self._end_foreground(fixture, tag="governance")
        self._select_option(fixture, review=True)
        fixture.client.payload = {"kind": "append_paragraph", "text": REVIEW_BODY,
                                  "self_note": "stub: 写具体回顾"}
        out = fixture.cycle.evaluate_once(trigger_ref="evt:practice:2", act=True)
        review_id = str((out.get("review_created") or {})["artifact_id"])
        self.assertIn(REVIEW_BODY, fixture.port.read_artifact(review_id).get("content") or "")

        # The operator's own deletion path (the existing control ledger -- no physical delete).
        ledger = MemoryControlLedger(str(fixture.controls_db), str(fixture.evidence_db),
                                     owner="chiyo", conversation="life:chiyo")
        self.assertEqual(ledger.apply(operation_id="op:practice:delete:1", kind="DELETE",
                                      target=str(event_id), actor="chiyo"), "APPLIED")

        practices = fixture.cycle.practice
        self.assertEqual(practices.experiences(), [])
        self.assertIsNone(practices.read_experience(experience_id))
        withheld = practices.read_review(review_id)
        self.assertIsNotNone(withheld)
        self.assertFalse(withheld["content_available"], withheld)
        self.assertEqual(withheld["excerpt"], "")
        context = practices.writing_context(review_id)
        self.assertIsNotNone(context)
        self.assertEqual(context["own_review"],
                         {"artifact_id": review_id, "available": False})
        self.assertNotIn(REVIEW_BODY, json.dumps(context, ensure_ascii=False))
        self.assertFalse([row for row in fixture.cycle.project_options() if row.get("review")])

    # -- helpers -----------------------------------------------------------------------

    def assert_created_method(self, fixture, out):
        created = out.get("method_created") or {}
        self.assertTrue(created.get("artifact_id"), out)
        return created


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

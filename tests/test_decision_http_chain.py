#!/usr/bin/env python3
"""The formal decision request reaches a *real* OpenAI-compatible HTTP payload.

Why this file exists
--------------------
The previous contract test (``tests/test_decision_reason_contract.py``) proved that the adapter's
prompt and the validator's registry agree, but it started from a hand-built
``CognitiveDecisionRequest`` and stopped at a scripted ``transport.complete``.  It never ran the
production request builder and never let ``profile_model_transport.OpenAICompatibleTransport``
serialize a wire body.  This file closes exactly that seam and nothing else:

    real ``AgencyDecisionService.evaluate_opportunity``
      -> the service's own ``CognitiveDecisionRequest`` construction
      -> real ``ProductionAgencyCognitionAdapter.invoke`` / ``build_prompt``
      -> real ``AgencyModelClient.complete``
      -> real ``OpenAICompatibleTransport._build_payload`` / ``begin_send``
      -> **the last hop only** is replaced: ``http.client.HTTPSConnection`` is stood in for by an
         in-memory connection that records the bytes the transport wrote and answers a synthetic
         200 -- no socket, no DNS, no server, no provider.

What is a fixture substitute, what is real
------------------------------------------
* substituted: the HTTP connection object (``_MemoryConnection``) -- the final network send and
  the response read.  It is installed by swapping ``http.client.HTTPSConnection`` for the
  duration of a case; nothing inside ``profile_model_transport`` is patched or monkey-patched.
* substituted: the model's answer (a synthetic chat-completions 200 body).
* real: the AG-1 service, store, lease, capability, context/candidate/capacity ports, the
  ``CognitiveDecisionRequest`` built by ``evaluate_opportunity``, the adapter, the model client,
  the transport payload builder, the response parser and ``DecisionOutputValidator``.

So the captured body is a *code value* produced by the real transport, not a hand-written request;
whether a remote provider ever received it is unknown and is not claimed.  ``max_tokens`` and
``temperature`` are the values this code path sends, not a measurement of provider behaviour.

Offline: the existing g1 guard (AF_INET/AF_INET6/DNS/create_connection refused, AF_UNIX allowed)
is installed for the class.  Scratch is ``mkdtemp`` and kept; nothing is deleted.

Run:  MSYS_NO_PATHCONV=1 wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && \
        /tmp/rc104-venv/bin/python -B -m unittest tests.test_decision_http_chain'
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(LIFE_SCRIPTS)]

if os.name == "posix":  # agency_decision_ag1 imports fcntl; the AG-1 module is POSIX-only
    import activity_continuity as ac  # noqa: E402
    import agency_cognition_adapter as aa  # noqa: E402
    import agency_decision_ag1 as ag1  # noqa: E402
    import agency_model_client as mc  # noqa: E402
    import candidate_sources_ag0 as ag0  # noqa: E402
    import profile_model_transport as pmt  # noqa: E402
else:  # pragma: no cover - documented host limit, not a silent skip of a real failure
    ac = aa = ag1 = mc = ag0 = pmt = None

POSIX_ONLY = unittest.skipUnless(
    os.name == "posix", "agency_decision_ag1 needs a POSIX host (fcntl)"
)

TS = "2026-10-21T02:00:00+00:00"
CANDIDATE_ID = "cand:http_chain"


class _MemoryResponse:
    """The 200 the real transport's ``finish_send`` parses; never a socket."""

    def __init__(self, body: bytes) -> None:
        self.status = 200
        self._body = body

    def read(self) -> bytes:
        return self._body


class _NullSocket:
    """Stands in for the connected socket so ``connection.close()`` stays harmless."""

    def close(self) -> None:
        return None


class _MemoryConnection(http.client.HTTPConnection):
    """The last hop: records the request bytes and answers from memory.

    ``begin_send`` still runs ``_target`` and ``_build_payload``; only the connect/write/read
    against a real socket is replaced.  It subclasses the stdlib connection so the transport's
    ``putrequest``/``putheader``/``endheaders`` sequence is the real one.
    """

    reply_body: bytes = b"{}"
    instances: list["_MemoryConnection"] = []

    def __init__(self, host, port=None, timeout=None, **kwargs):  # noqa: D401 - stdlib shape
        super().__init__(host, port, timeout=timeout)
        self.host = host
        self.port = port
        self.method = None
        self.path = None
        self.sent: list[bytes] = []
        self.closed = False
        type(self).instances.append(self)

    def connect(self) -> None:  # never touches socket.create_connection
        self.sock = _NullSocket()

    def putrequest(self, method, url, *args, **kwargs) -> None:
        self.method, self.path = method, url
        super().putrequest(method, url, *args, **kwargs)

    def send(self, data) -> None:  # py3: bytes; recording is the whole point
        self.sent.append(bytes(data))

    def getresponse(self):
        return _MemoryResponse(self.reply_body)

    def close(self) -> None:
        self.closed = True

    def request_payload(self) -> dict:
        """The chat-completions JSON the real transport serialized, decoded in memory."""
        for chunk in self.sent:
            try:
                doc = json.loads(chunk.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(doc, dict) and "messages" in doc and "model" in doc:
                return doc
        raise AssertionError("the transport never wrote a chat-completions payload")


def _wire_body(decision: dict) -> bytes:
    """A synthetic provider 200 whose only answer content is the given decision object."""

    content = json.dumps(decision, ensure_ascii=False)
    return json.dumps({
        "id": "synthetic-response",
        "model": "synthetic-model",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 42, "completion_tokens": 12, "total_tokens": 54},
    }, ensure_ascii=False).encode("utf-8")


def _world_candidate():
    """One real AG-0 candidate with the provenance WORLD_OPPORTUNITY requires."""

    return ag0.CandidateRecord(
        candidate_id=CANDIDATE_ID,
        candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY,
        source_kind=ag0.SOURCE_WORLD_OPPORTUNITY,
        source_ref="src:http_chain",
        availability_status=ag0.CANDIDATE_STATUS_OPEN,
        created_at=TS, observed_at=TS, recorded_at=TS,
        idempotency_key="idem:http_chain",
        world_opportunity_ref="wopp:http_chain",
        perception_ref="percept:http_chain",
        world_revision_ref="wrev:http_chain:1",
        causal_parent_refs=["src:http_chain"],
        provenance_refs=["src:http_chain"],
    )


class _ReserveLog:
    """Minimal budget port: records reserve/settle so an auto-retry would be visible."""

    def __init__(self) -> None:
        self.reserved = 0
        self.settled: list[str] = []

    def reserve(self, *, request_key):
        self.reserved += 1
        return {"request_key": request_key}

    def settle(self, reservation, *, outcome, detail=None, result=None):
        self.settled.append(outcome)


class _Harness:
    """The real production stack from service down to the transport, over a fresh store."""

    def __init__(self, decision: dict) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="rc1-decision-http-chain-"))
        self.store_root = self.root / "agency"
        self.store = ag1.AgencyDecisionStore(self.store_root)
        self.lease = ac.ActivityAuthorityLease(self.root)
        self.lease.acquire()
        self.capability = ag1.AgencyDecisionAuthority.issue_capability(
            self.lease, caller_module="ag1_test_harness", store_root=self.store_root,
            namespace=ag1.NAMESPACE_ISOLATED_TEST,
        )

        _MemoryConnection.instances = []
        _MemoryConnection.reply_body = _wire_body(decision)

        # Synthetic, explicitly non-secret provider config.  Nothing reads env/config here.
        self.transport = pmt.OpenAICompatibleTransport(
            base_url="https://synthetic.invalid/v1",
            api_key="synthetic-not-a-secret",
            model="synthetic-model",
            provider="synthetic",
            timeout_s=2.0,
            purpose="agency_cognition",
            temperature=0.0,
            request_json_object=True,
        )
        self.client = mc.AgencyModelClient(
            config={"provider": "synthetic", "model": "synthetic-model",
                    "base_url": "https://synthetic.invalid/v1",
                    "api_key": "synthetic-not-a-secret"},
            transport=self.transport, purpose="agency_cognition",
        )
        self.budget = _ReserveLog()
        self.adapter = aa.ProductionAgencyCognitionAdapter(
            client=self.client, budget=self.budget, timeout_s=2.0, max_output_tokens=512,
        )
        self.service = ag1.AgencyDecisionService(
            store=self.store, lease=self.lease, capability=self.capability,
            cognition_adapter=self.adapter,
        )

    def decide(self):
        candidates = [_world_candidate()]
        candidate_set = ag0.CandidateSet(
            candidate_set_id="cset:http_chain", subject_id=ag0.GLOBAL_SUBJECT_ID,
            candidate_refs=[c.candidate_id for c in candidates],
            source_snapshot_refs=["src:http_chain"], observed_at=TS, built_at=TS,
        )
        opportunity = ag1.DecisionOpportunity(
            opportunity_id="dopp:http_chain",
            trigger_kind="EXPLICIT_REEVALUATION",
            trigger_ref="evt:http_chain",
            candidate_set_ref=candidate_set.candidate_set_id,
            occurred_at=TS, observed_at=TS, recorded_at=TS,
            causal_parent_refs=["src:http_chain"],
            idempotency_key="idem:http_chain",
            status=ag1.OPPORTUNITY_STATUS_OPEN,
            current_activity_ref=None,
        )
        return self.service.evaluate_opportunity(
            opportunity=opportunity,
            candidate_set=candidate_set,
            candidates=candidates,
            body_capacity_state="AVAILABLE",
            device_availability_state="AVAILABLE",
            resource_availability_state="AVAILABLE",
            privacy_availability_state="AVAILABLE",
            runtime_load_state="AVAILABLE",
        )

    def close(self) -> None:
        # Releasing the flock is lease lifecycle, not fixture cleanup: nothing is deleted.
        self.lease.release()


NO_ACTION = {
    "decision": "NO_ACTION",
    "selected_candidate_id": None,
    "reason_codes": ["NO_ACTION_CHOSEN"],
    "short_rationale": None,
    "deferred_candidate_ids": [],
}
START = {
    "decision": "START",
    "selected_candidate_id": CANDIDATE_ID,
    "reason_codes": ["WORLD_OPPORTUNITY_OBSERVED"],
    "deferred_candidate_ids": [],
}
UNREGISTERED = {
    "decision": "NO_ACTION",
    "selected_candidate_id": None,
    "reason_codes": ["MADE_UP_BY_THE_MODEL"],
    "deferred_candidate_ids": [],
}


@POSIX_ONLY
class DecisionHttpChainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import g1_real_adoption as guard_mod

        # mkdtemp is kept on purpose: the guard takes a root and this suite never deletes scratch.
        cls._scratch = Path(tempfile.mkdtemp(prefix="rc1-decision-http-chain-guard-"))
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

    def setUp(self):
        self._original_https = http.client.HTTPSConnection
        http.client.HTTPSConnection = _MemoryConnection

        def _restore():
            http.client.HTTPSConnection = self._original_https

        self.addCleanup(_restore)
        self.harness = None

    def tearDown(self):
        if self.harness is not None:
            self.harness.close()

    def _run(self, decision):
        self.harness = _Harness(decision)
        return self.harness.decide()

    # -- 1. the real transport serializes the service's own request ---------------------

    def test_01_the_real_transport_serializes_the_services_request(self):
        result = self._run(NO_ACTION)
        harness = self.harness

        self.assertIsInstance(harness.transport, pmt.OpenAICompatibleTransport)
        self.assertIs(harness.client._transport, harness.transport)
        self.assertEqual(1, len(_MemoryConnection.instances),
                         "the chain must make exactly one wire attempt")

        connection = _MemoryConnection.instances[-1]
        self.assertEqual("POST", connection.method)
        payload = connection.request_payload()

        # Exactly the keys _build_payload writes for this transport configuration.
        self.assertEqual(
            {"model", "messages", "max_tokens", "temperature", "stream", "response_format"},
            set(payload), "the wire body is not the shape the real transport builds")
        self.assertEqual("synthetic-model", payload["model"])
        self.assertIs(False, payload["stream"])
        self.assertEqual(0.0, payload["temperature"])
        expected_cap = max(harness.adapter.max_output_tokens,
                           ag1.DEFAULT_COGNITION_MAX_TOKENS,
                           aa.MIN_DECISION_OUTPUT_TOKENS)
        self.assertEqual(expected_cap, payload["max_tokens"])
        self.assertEqual({"type": "json_object"}, payload["response_format"])
        for absent in ("response_schema", "json_schema", "output_schema", "schema"):
            self.assertNotIn(absent, payload,
                             "the provider path must send no output schema, only JSON mode")

        messages = payload["messages"]
        self.assertEqual(["system", "user"], [m["role"] for m in messages])
        self.assertEqual(aa.SYSTEM_PROMPT, messages[0]["content"])
        prompt = json.loads(messages[1]["content"])
        self.assertEqual([CANDIDATE_ID], [c["candidate_id"] for c in prompt["candidates"]])
        self.assertIn("NO_ACTION", prompt["legal_decision_enums"])

        # The offered closed set is the validator's registry.  If the production capacity policy
        # narrowed it, it must still be a legal non-empty subset; never a widened or foreign set.
        advertised = [str(code) for code in prompt["allowed_reason_codes"]]
        registry = set(ag1.VALID_DECISION_REASON_CODES)
        self.assertTrue(advertised)
        self.assertTrue(set(advertised) <= registry)
        if harness.adapter.last_prompt_trim.get("trimmed"):
            self.assertLess(len(set(advertised)), len(registry))
        else:
            self.assertEqual(registry, set(advertised))
        for code in advertised:
            self.assertEqual([code], ag1.DecisionReasonCodeRegistry.validate_codes([code]))

        # The answer that came back over this wire commits through the real validator.
        self.assertEqual(ag1.ATTEMPT_STATUS_SUCCEEDED, result["status"])
        record = result["decision_record"]
        self.assertIsNotNone(record)
        self.assertEqual("NO_ACTION", record.decision)
        self.assertEqual([CANDIDATE_ID], list(record.candidate_refs))
        self.assertTrue(record.decision_id.startswith("dec:"))
        self.assertTrue(set(record.reason_codes) <= registry)
        self.assertEqual(1, harness.transport.call_count)

    # -- 2. a legal START answer commits through the same chain -------------------------

    def test_02_a_legal_start_answer_commits_through_the_same_chain(self):
        result = self._run(START)
        harness = self.harness

        self.assertEqual(1, len(_MemoryConnection.instances))
        self.assertEqual(ag1.ATTEMPT_STATUS_SUCCEEDED, result["status"])
        record = result["decision_record"]
        self.assertIsNotNone(record)
        self.assertEqual("START", record.decision)
        self.assertEqual(CANDIDATE_ID, record.selected_candidate_ref)
        self.assertEqual(("WORLD_OPPORTUNITY_OBSERVED",), record.reason_codes)
        self.assertIn(record.selected_candidate_ref, record.candidate_refs)

        committed = harness.store.load_state()["decisions_by_id"]
        self.assertEqual([record.decision_id], list(committed))
        self.assertEqual(1, harness.adapter.call_count)

    # -- 3. an unregistered code fails the formal path, with no retry and no commit ------

    def test_03_an_unregistered_reason_code_fails_formally_without_retry(self):
        result = self._run(UNREGISTERED)
        harness = self.harness

        self.assertEqual(ag1.ATTEMPT_STATUS_FAILED, result["status"])
        self.assertIsNone(result["decision_record"])
        self.assertEqual(ag1.FAILURE_CLASS_UNKNOWN_REASON_CODE, result["failure_class"])
        detail = result["schema_refusal"]
        self.assertEqual(("reason_codes", "UNREGISTERED_CODE"),
                         (detail["field"], detail["rule"]))
        self.assertNotIn("MADE_UP_BY_THE_MODEL", json.dumps(detail, ensure_ascii=False))

        # The refusal did not quietly retry: one reserve, one settle, one wire attempt.
        self.assertEqual(1, harness.budget.reserved)
        self.assertEqual(["OK"], harness.budget.settled)
        self.assertEqual(1, harness.service.cognition_call_count)
        self.assertEqual(1, harness.adapter.call_count)
        self.assertEqual(1, len(_MemoryConnection.instances))
        self.assertEqual(0, result["attempt"].retry_count)

        # No downstream action and no DecisionRecord.
        for name in ("activity_mutation_count", "action_creation_count", "world_write_count",
                     "memory_write_count", "goal_write_count", "commitment_write_count",
                     "agenda_creation_count", "resume_condition_creation_count", "outbound_count"):
            self.assertEqual(0, getattr(harness.service, name), name)
        self.assertEqual({}, harness.store.load_state()["decisions_by_id"])


if __name__ == "__main__":
    unittest.main()

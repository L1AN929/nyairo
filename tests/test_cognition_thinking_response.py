"""LPC0B-R3: a thinking provider's answer shape must not be mistaken for bad JSON.

What this file pins, and why
----------------------------
The configured ``cn:deepseek-v4.1-flash`` is a reasoning model.  Its thinking tokens are counted
*inside* ``usage.completion_tokens`` (``completion_tokens_details.reasoning_tokens``), so they
are paid for out of the same ``max_tokens`` as the answer.  The decision request used to be sent
with a 512-token ceiling, and an actual one-day run came back as::

    model_calls=1  DECISION_FAILED DECISION_ATTEMPT_FAILED:INVALID_JSON  actions=0

Two structural defects produced that word, and neither of them was "the model wrote bad JSON":

* ``finish_reason="length"`` with ``content: ""`` was raised by the transport as
  ``PROVIDER_MALFORMED_RESPONSE("empty completion")``, which the cognition adapter maps to
  ``INVALID_JSON``.  A cap that ran out before the answer started was therefore reported as the
  model emitting unparsable JSON;
* ``message.reasoning_content`` -- a derivation -- was not inspected at all, so "reasoning only,
  no answer" was indistinguishable from an empty body, and nothing prevented a future edit from
  promoting that text into the answer.

These tests reproduce the old response behaviour on a **loopback** chat-completions server: no
credential is read, no real provider is called, and only response *shape* is exchanged.  Every
assertion below fails on the pre-fix code, which is the point.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import agency_cognition_adapter as aca          # noqa: E402
import agency_decision_ag1 as ag1               # noqa: E402
import agency_model_client as amc               # noqa: E402
from profile_model_transport import OpenAICompatibleTransport  # noqa: E402

#: A real, complete decision object.  When a model puts THIS in ``reasoning_content`` and leaves
#: ``content`` empty it is still not a decision: nothing here may read it out of the derivation.
DECISION = {
    "decision": "NO_ACTION",
    "selected_candidate_id": None,
    "reason_codes": ["NO_ACTION_CHOSEN"],
    "short_rationale": "nothing legal right now",
    "deferred_candidate_ids": [],
}


def _a_request(opportunity_id: str = "opp:r3"):
    """A synthetic AG-1 cognition request.  No instance data, no private text."""
    return ag1.CognitiveDecisionRequest(
        opportunity_id=opportunity_id, candidate_set_id="cset:r3",
        context_snapshot_id="snap:r3", snapshot_fingerprint="fp:r3-" + opportunity_id,
        candidates=({"candidate_id": "cand:r3", "source_kind": "RESUME_ELIGIBLE",
                     "source_ref": "src:r3", "candidate_kind": "CONSIDER_ARTIFACT"},),
        current_activity_summary=None, capacity_summary={"state": "AVAILABLE"},
        legal_decision_enums=("START", "NO_ACTION", "DEFER"),
        allowed_reason_codes=tuple(sorted(ag1.VALID_DECISION_REASON_CODES)),
        observed_fact_keys=("world.location",), policy_version="r3-test")


class Reply:
    """One canned provider response: the *shape* of a thinking model's answer."""

    def __init__(self, *, content: str = "", reasoning: str | None = None,
                 finish_reason: str = "stop", status: int = 200) -> None:
        self.content = content
        self.reasoning = reasoning
        self.finish_reason = finish_reason
        self.status = status


class ThinkingModel:
    """Loopback chat-completions server that replays canned shapes and records the request body."""

    def __init__(self, replies) -> None:
        outer = self
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.hits = 0

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server naming
                outer.hits += 1
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length)
                try:
                    outer.requests.append(json.loads(raw.decode("utf-8")))
                except ValueError:
                    outer.requests.append({})
                reply = outer.replies.pop(0) if outer.replies else Reply()
                if reply.status != 200:
                    body = json.dumps({"error": {"message": "unsupported parameter",
                                                 "type": "invalid_request_error"}}).encode()
                    self.send_response(reply.status)
                else:
                    message = {"role": "assistant", "content": reply.content}
                    if reply.reasoning is not None:
                        message["reasoning_content"] = reply.reasoning
                    body = json.dumps({
                        "id": "probe", "model": "thinking-probe",
                        "choices": [{"index": 0, "message": message,
                                     "finish_reason": reply.finish_reason}],
                        "usage": {"prompt_tokens": 900, "completion_tokens": 512,
                                  "total_tokens": 1412,
                                  "completion_tokens_details": {"reasoning_tokens": 500}},
                    }).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # keep the test output clean
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d/v1" % self.server.server_port

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


class ThinkingResponseTest(unittest.TestCase):
    def _adapter(self, replies, *, max_output_tokens: int = 512):
        model = ThinkingModel(replies)
        self.addCleanup(model.stop)
        transport = OpenAICompatibleTransport(
            base_url=model.base_url, api_key="loopback-key", model="thinking-probe",
            provider="custom", timeout_s=5.0, purpose="agency_cognition",
            request_json_object=True)
        client = amc.AgencyModelClient(
            config={"provider": "custom", "model": "thinking-probe",
                    "base_url": model.base_url, "api_key": "loopback-key"},
            transport=transport, purpose="agency_cognition", timeout_s=5.0,
            max_output_tokens=max_output_tokens)
        adapter = aca.ProductionAgencyCognitionAdapter(
            client=client, timeout_s=5.0, max_output_tokens=max_output_tokens)
        return adapter, model

    # -- the failure that was actually observed ---------------------------------------

    def test_a_cap_truncated_answer_is_not_reported_as_invalid_json(self):
        """finish_reason=length + empty content used to arrive as INVALID_JSON."""

        adapter, model = self._adapter([Reply(content="", reasoning=json.dumps(DECISION),
                                              finish_reason="length")])
        with self.assertRaises(ag1.CognitionAdapterError) as caught:
            adapter.invoke(_a_request())

        failure = caught.exception.failure_class
        self.assertNotEqual(failure, ag1.FAILURE_CLASS_INVALID_JSON,
                            "a cap-truncated answer is still being reported as bad JSON")
        self.assertEqual(failure, "DECISION_ATTEMPT_FAILED:OUTPUT_TRUNCATED", failure)
        # The provider WAS reached and billed: the attempt is counted, not silently dropped.
        self.assertEqual(model.hits, 1)
        self.assertEqual(adapter.stats()["provider_calls"], 1)
        # And the reasoning it did produce never became an answer.
        self.assertNotIn("NO_ACTION", str(caught.exception))

    def test_a_reasoning_only_answer_never_becomes_a_decision(self):
        """A complete decision inside reasoning_content, with content empty, is a refusal."""

        adapter, model = self._adapter([Reply(content="", reasoning=json.dumps(DECISION),
                                              finish_reason="stop")])
        with self.assertRaises(ag1.CognitionAdapterError) as caught:
            adapter.invoke(_a_request("opp:r3-reasoning-only"))

        failure = caught.exception.failure_class
        self.assertEqual(failure, "DECISION_ATTEMPT_FAILED:REASONING_ONLY_NO_ANSWER", failure)
        self.assertNotEqual(failure, ag1.FAILURE_CLASS_INVALID_JSON)
        self.assertEqual(model.hits, 1)
        # No epoch slot was filled, so this answer cannot be replayed as a decision later.
        self.assertEqual(adapter.stats()["distinct_epochs_paid"], 0)

    # -- the request that made that failure possible ----------------------------------

    def test_the_decision_request_reserves_thinking_headroom(self):
        """512 total tokens leaves a thinking model no room to answer in."""

        adapter, model = self._adapter([Reply(content=json.dumps(DECISION))])
        response = adapter.invoke(_a_request("opp:r3-headroom"))

        sent = model.requests[0]
        self.assertGreaterEqual(int(sent["max_tokens"]), aca.MIN_DECISION_OUTPUT_TOKENS,
                                "the decision request still has no thinking headroom")
        self.assertGreaterEqual(aca.MIN_DECISION_OUTPUT_TOKENS, 1024)
        # JSON mode is still asked for: the room is for thinking, not for prose.
        self.assertEqual(sent.get("response_format"), {"type": "json_object"})
        # The configured cap is what the adapter *reports*; only the wire ceiling moved.
        self.assertEqual(adapter.max_output_tokens, 512)
        # And the answer itself still arrives through the strict entry unchanged.
        self.assertIsInstance(response.raw_output, dict)
        self.assertEqual(response.raw_output["decision"], "NO_ACTION")

    # -- the strict JSON entry stays strict -------------------------------------------

    def test_a_fully_fenced_answer_is_unwrapped(self):
        body = json.dumps(DECISION)
        fenced = ("```json\n%s\n```" % body,
                  "```json\r\n%s\r\n```" % body,
                  "```\n%s\n```   " % body,
                  "```json %s ```" % body)
        adapter, _model = self._adapter([Reply(content=text) for text in fenced])
        for index, text in enumerate(fenced):
            with self.subTest(fence=index):
                response = adapter.invoke(_a_request("opp:r3-fence-%d" % index))
                self.assertIsInstance(response.raw_output, dict, text[:40])
                self.assertEqual(response.raw_output["decision"], "NO_ACTION")

    def test_prose_around_a_fence_is_not_guessed_into_a_decision(self):
        """A decision is never read out of prose; that still fails AG-1's strict entry."""

        prose = "Sure! Here is my decision:\n```json\n%s\n```\nLet me know." % json.dumps(DECISION)
        adapter, _model = self._adapter([Reply(content=prose)])
        response = adapter.invoke(_a_request("opp:r3-prose"))

        self.assertIsInstance(response.raw_output, str,
                              "prose was parsed into a decision object")
        with self.assertRaises(ag1.DecisionValidationError) as caught:
            ag1.DecisionOutputValidator.validate_decision_output(
                {"not": "a decision"}, candidate_set=None, candidates_by_id={},
                constraint_results_by_id={}, context_snapshot=None, capacity_snapshot=None)
        self.assertIn("decision output", str(caught.exception).lower())

    # -- the other way the same word was produced -------------------------------------

    def test_a_refused_request_is_not_reported_as_bad_model_output(self):
        """A 400 (e.g. an unsupported response_format) is not 'the model wrote bad JSON'."""

        adapter, model = self._adapter([Reply(status=400)])
        with self.assertRaises(ag1.CognitionAdapterError) as caught:
            adapter.invoke(_a_request("opp:r3-400"))

        failure = caught.exception.failure_class
        self.assertEqual(failure, "DECISION_ATTEMPT_FAILED:MODEL_REQUEST_REJECTED", failure)
        self.assertNotEqual(failure, ag1.FAILURE_CLASS_INVALID_JSON)
        self.assertEqual(model.hits, 1)

    # -- transport-level shape reporting ----------------------------------------------

    def test_the_transport_reports_answer_shape_without_reading_reasoning(self):
        reasoning = "step 1: weigh the candidates. " + json.dumps(DECISION)
        content = json.dumps(DECISION)
        model = ThinkingModel([Reply(content=content, reasoning=reasoning)])
        self.addCleanup(model.stop)
        transport = OpenAICompatibleTransport(
            base_url=model.base_url, api_key="loopback-key", model="thinking-probe",
            timeout_s=5.0, purpose="agency_cognition")

        result = transport.complete(system_prompt="s", user_prompt="u")

        self.assertEqual(result.text, content, "reasoning was promoted into the answer text")
        self.assertNotIn("step 1:", result.text)
        self.assertEqual(result.finish_reason, "stop")
        self.assertTrue(result.reasoning_present)
        self.assertEqual(result.reasoning_chars, len(reasoning.strip()))
        self.assertEqual(result.content_chars, len(content))


if __name__ == "__main__":
    unittest.main(verbosity=2)

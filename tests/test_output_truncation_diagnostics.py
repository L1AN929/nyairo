"""输出截断的定向诊断与修复：transport -> adapter -> resident journal。

2026-10-22 第二段真实日常里，一次成功认知请求记录 ``max_output_tokens_sent=2048``、
``reasoning_chars=7760``、``content_chars=199``；随后两次真实请求以
``PROVIDER_OUTPUT_TRUNCATED`` 失败，但失败行没有 finish_reason / 字符数 / usage 计数，
无法判断截断具体发生在哪一步。这些测试在本机 loopback 上复现同一失败分支，钉住三件事：

* ``finish_reason="length"`` 一律按截断拒绝（空正文、部分 JSON、甚至完整 JSON 都不例外），
  与 ``REASONING_ONLY``、真正的坏 JSON 三类互不串；
* 失败路径把「实际送出的 cap、finish_reason、字符数、provider 报的整数 token 数」经 adapter
  传到 resident 的安全 sink；缺失的计数保持缺失，不补 0；
* 输出 cap 只在既有 adapter seam 上抬高到单一有界下限，payload 可断言、调用数不变、
  预算消耗不变。

没有真实网关、没有凭证读取、没有任何真实产品模型调用。
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
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
import autonomous_life_assembly as assembly     # noqa: E402
import autonomous_life_resident as resident     # noqa: E402
from profile_model_transport import OpenAICompatibleTransport  # noqa: E402

#: 任何一行 journal 都不许出现的私文标记。
PRIVATE_MARKER = "私文-TRUNC-SHOULD-NOT-APPEAR-4b7c"

DECISION = {
    "decision": "NO_ACTION",
    "selected_candidate_id": None,
    "reason_codes": ["NO_ACTION_CHOSEN"],
    "short_rationale": "nothing legal right now",
    "deferred_candidate_ids": [],
}


def _request(opportunity_id: str = "opp:trunc"):
    return ag1.CognitiveDecisionRequest(
        opportunity_id=opportunity_id, candidate_set_id="cset:trunc",
        context_snapshot_id="snap:trunc", snapshot_fingerprint="fp:trunc-" + opportunity_id,
        candidates=({"candidate_id": "cand:trunc", "source_kind": "RESUME_ELIGIBLE",
                     "source_ref": "src:trunc", "candidate_kind": "CONSIDER_ARTIFACT"},),
        current_activity_summary=None, capacity_summary={"state": "AVAILABLE"},
        legal_decision_enums=("START", "NO_ACTION", "DEFER"),
        allowed_reason_codes=tuple(sorted(ag1.VALID_DECISION_REASON_CODES)),
        observed_fact_keys=("world.location",), policy_version="trunc-test")


class Loopback:
    """脚本化的 chat-completions 服务器：只回形状，不接真实 provider。"""

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
                reply = outer.replies.pop(0) if outer.replies else {"content": ""}
                if reply.get("delay"):
                    time.sleep(float(reply["delay"]))
                message = {"role": "assistant", "content": reply.get("content", "")}
                if "reasoning" in reply:
                    message["reasoning_content"] = reply["reasoning"]
                usage = reply.get("usage") or {"prompt_tokens": 900, "completion_tokens": 512,
                                               "total_tokens": 1412}
                body = json.dumps({
                    "id": "probe", "model": "trunc-probe",
                    "choices": [{"index": 0, "message": message,
                                 "finish_reason": reply.get("finish_reason", "stop")}],
                    "usage": usage,
                }).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

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


def _transport(model: Loopback, *, timeout_s: float = 5.0) -> OpenAICompatibleTransport:
    return OpenAICompatibleTransport(
        base_url=model.base_url, api_key="loopback-key", model="trunc-probe",
        provider="custom", timeout_s=timeout_s, purpose="agency_cognition",
        request_json_object=True)


def _adapter(model: Loopback, *, max_output_tokens: int = 512, observer=None,
             timeout_s: float = 5.0, transport_timeout_s: float | None = None):
    client = amc.AgencyModelClient(
        config={"provider": "custom", "model": "trunc-probe",
                "base_url": model.base_url, "api_key": "loopback-key"},
        transport=_transport(model, timeout_s=transport_timeout_s or timeout_s),
        purpose="agency_cognition", timeout_s=timeout_s,
        max_output_tokens=max_output_tokens)
    return aca.ProductionAgencyCognitionAdapter(
        client=client, timeout_s=timeout_s, max_output_tokens=max_output_tokens,
        observer=observer)


def _env(model: Loopback) -> dict[str, str]:
    return {"NYA02_MODEL_BASE_URL": model.base_url, "NYA02_MODEL_NAME": "trunc-probe",
            "NYA02_MODEL_API_KEY": "loopback-key", "NYA02_MODEL_PROVIDER": "custom"}


def _resident(root: Path) -> resident.AutonomousLifeResident:
    return resident.AutonomousLifeResident(state_root=root, environment={},
                                            poll_seconds=2, act=False)


def _rows(root: Path) -> list[dict]:
    path = root / resident.RESIDENT_JOURNAL_FILENAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


class TransportShapeTest(unittest.TestCase):
    """transport 层：length 判定与失败错误携带的安全元数据。"""

    def test_empty_content_with_length_finish_reports_truncation_and_shape(self):
        reasoning = "think " * 40
        model = Loopback([{"content": "", "reasoning": reasoning, "finish_reason": "length",
                           "usage": {"prompt_tokens": 900, "completion_tokens": 4096,
                                     "total_tokens": 4996,
                                     "completion_tokens_details": {"reasoning_tokens": 4080}}}])
        self.addCleanup(model.stop)

        with self.assertRaises(amc.AgencyModelClientError) as caught:
            _transport(model).complete(system_prompt="s", user_prompt="u")

        self.assertEqual(caught.exception.failure_class, amc.PROVIDER_OUTPUT_TRUNCATED)
        metadata = caught.exception.metadata
        self.assertEqual(metadata["finish_reason"], "length")
        self.assertEqual(metadata["content_chars"], 0)
        self.assertEqual(metadata["reasoning_chars"], len(reasoning.strip()))
        self.assertEqual(metadata["input_tokens"], 900)
        self.assertEqual(metadata["output_tokens"], 4096)
        self.assertEqual(metadata["total_tokens"], 4996)
        self.assertEqual(metadata["reasoning_tokens"], 4080)
        # 正文与推理文本绝不进入错误元数据。
        self.assertNotIn("think", json.dumps(metadata, ensure_ascii=False))

    def test_a_length_finish_with_partial_json_is_truncation_not_bad_json(self):
        model = Loopback([{"content": '{"decision": "NO_ACT', "finish_reason": "length"}])
        self.addCleanup(model.stop)
        adapter = _adapter(model)

        with self.assertRaises(ag1.CognitionAdapterError) as caught:
            adapter.invoke(_request("opp:trunc-partial"))

        self.assertEqual(caught.exception.failure_class,
                         "DECISION_ATTEMPT_FAILED:OUTPUT_TRUNCATED")
        self.assertNotEqual(caught.exception.failure_class, ag1.FAILURE_CLASS_INVALID_JSON)
        self.assertEqual(model.hits, 1)

    def test_a_length_finish_with_complete_json_is_still_refused(self):
        """即使截断后的正文恰好能解析，也绝不把 length 当成功。"""

        model = Loopback([{"content": json.dumps(DECISION), "finish_reason": "length"}])
        self.addCleanup(model.stop)
        adapter = _adapter(model)

        with self.assertRaises(ag1.CognitionAdapterError) as caught:
            adapter.invoke(_request("opp:trunc-complete"))

        self.assertEqual(caught.exception.failure_class,
                         "DECISION_ATTEMPT_FAILED:OUTPUT_TRUNCATED")
        # 没有任何答案进入 epoch 槽，之后也不可能被当成决定重放。
        self.assertEqual(adapter.stats()["distinct_epochs_paid"], 0)

    def test_reasoning_only_and_genuinely_bad_json_do_not_cross_classify(self):
        model = Loopback([
            {"content": "", "reasoning": json.dumps(DECISION), "finish_reason": "stop"},
            {"content": "I could not decide.", "finish_reason": "stop"},
        ])
        self.addCleanup(model.stop)
        adapter = _adapter(model)

        with self.assertRaises(ag1.CognitionAdapterError) as caught:
            adapter.invoke(_request("opp:trunc-reasoning-only"))
        self.assertEqual(caught.exception.failure_class,
                         "DECISION_ATTEMPT_FAILED:REASONING_ONLY_NO_ANSWER")

        # 真正不是 JSON 的正文仍按原规则交给 AG-1：adapter 不替它补、不改判成截断。
        response = adapter.invoke(_request("opp:trunc-bad-json"))
        self.assertIsInstance(response.raw_output, str)
        self.assertEqual(response.raw_output, "I could not decide.")


class OutputCapTest(unittest.TestCase):
    """cap 在 adapter seam 上单一有界调整，payload 可断言、调用数不变。"""

    def test_the_wire_cap_is_the_new_bounded_floor(self):
        model = Loopback([{"content": json.dumps(DECISION)}])
        self.addCleanup(model.stop)
        adapter = _adapter(model, max_output_tokens=512)

        adapter.invoke(_request("opp:trunc-cap"))

        self.assertEqual(aca.MIN_DECISION_OUTPUT_TOKENS, 4096)
        self.assertEqual(model.hits, 1)
        self.assertEqual(int(model.requests[0]["max_tokens"]),
                         aca.MIN_DECISION_OUTPUT_TOKENS)
        # 配置值本身不被改写：只是线上天花板抬高。
        self.assertEqual(adapter.max_output_tokens, 512)

    def test_an_explicit_higher_config_is_preserved(self):
        model = Loopback([{"content": json.dumps(DECISION)}])
        self.addCleanup(model.stop)
        adapter = _adapter(model, max_output_tokens=8192)

        adapter.invoke(_request("opp:trunc-cap-high"))

        self.assertEqual(int(model.requests[0]["max_tokens"]), 8192)
        self.assertGreater(8192, aca.MIN_DECISION_OUTPUT_TOKENS)


class FailureShapeTest(unittest.TestCase):
    """失败元数据只取已知字段；恶意值与非整数不得变成计数。"""

    def test_missing_or_malicious_token_counts_are_omitted_not_zeroed(self):
        model = Loopback([{
            "content": "", "finish_reason": "length",
            "usage": {"prompt_tokens": PRIVATE_MARKER, "completion_tokens": -1,
                      "total_tokens": True,
                      "completion_tokens_details": {"reasoning_tokens": PRIVATE_MARKER}},
        }])
        self.addCleanup(model.stop)

        with self.assertRaises(amc.AgencyModelClientError) as caught:
            _transport(model).complete(system_prompt="s", user_prompt="u")

        metadata = caught.exception.metadata
        for absent in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens"):
            self.assertNotIn(absent, metadata)
        self.assertNotIn(PRIVATE_MARKER, json.dumps(metadata, ensure_ascii=False))

    def test_the_adapter_rebuilds_only_known_shape_fields(self):
        class Exploding:
            metadata = {
                "finish_reason": PRIVATE_MARKER,
                "content_chars": True,
                "reasoning_chars": -3,
                "input_tokens": PRIVATE_MARKER,
                "output_tokens": True,
                "total_tokens": 12,
                "reasoning_tokens": 7,
                "unknown_private": PRIVATE_MARKER,
            }

        shape = aca.ProductionAgencyCognitionAdapter.failure_output_shape(
            Exploding(), output_cap=4096)

        self.assertEqual(shape, {"max_output_tokens_sent": 4096, "total_tokens": 12,
                                 "reasoning_tokens": 7})


class ResidentPersistenceTest(unittest.TestCase):
    """失败形状经 assembly -> adapter -> resident 安全落盘。"""

    def test_a_truncated_failure_is_persisted_with_shape_and_no_private_text(self):
        reasoning = "推理内容 " + PRIVATE_MARKER + " " + "x" * 60
        model = Loopback([{
            "content": "", "reasoning": reasoning, "finish_reason": "length",
            "usage": {"prompt_tokens": 1200, "completion_tokens": 4096,
                      "total_tokens": 5296,
                      "completion_tokens_details": {"reasoning_tokens": 4090}},
        }])
        self.addCleanup(model.stop)
        root = Path(tempfile.mkdtemp(prefix="trunc-diag-"))
        sink = _resident(root)
        adapter = assembly.model_cognition(_env(model), observer=sink._cognition_diagnostic)

        with self.assertRaises(ag1.CognitionAdapterError):
            adapter.invoke(_request("opp:trunc-persist"))

        rows = [row for row in _rows(root)
                if row.get("kind") == resident.COGNITION_DIAGNOSTIC_KIND]
        failed = next(row for row in rows if row["event"] == "cognition_send_failed")
        self.assertEqual(failed["error_category"], "PROVIDER_OUTPUT_TRUNCATED")
        self.assertTrue(failed["response_received"])
        self.assertEqual(failed["finish_reason"], "length")
        self.assertEqual(failed["content_chars"], 0)
        self.assertEqual(failed["reasoning_chars"], len(reasoning.strip()))
        self.assertEqual(failed["max_output_tokens_sent"], 4096)
        self.assertEqual(failed["input_tokens"], 1200)
        self.assertEqual(failed["output_tokens"], 4096)
        self.assertEqual(failed["total_tokens"], 5296)
        self.assertEqual(failed["reasoning_tokens"], 4090)
        raw = (root / resident.RESIDENT_JOURNAL_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn(PRIVATE_MARKER, raw)

    def test_a_malicious_shape_record_is_rebuilt_value_by_value(self):
        root = Path(tempfile.mkdtemp(prefix="trunc-diag-"))
        sink = _resident(root)
        sink._cognition_diagnostic({
            "event": "cognition_send_failed",
            "error_category": "PROVIDER_OUTPUT_TRUNCATED",
            "output_shape": {
                "finish_reason": PRIVATE_MARKER,
                "content_chars": 0,
                "reasoning_chars": PRIVATE_MARKER,
                "max_output_tokens_sent": 4096,
                "input_tokens": PRIVATE_MARKER,
                "output_tokens": True,
                "total_tokens": -5,
                "reasoning_tokens": 4090,
                "unknown_private": PRIVATE_MARKER,
            },
        })

        row = [item for item in _rows(root)
               if item.get("kind") == resident.COGNITION_DIAGNOSTIC_KIND][-1]
        self.assertEqual(row["finish_reason"], "UNKNOWN")
        self.assertEqual(row["content_chars"], 0)
        self.assertEqual(row["max_output_tokens_sent"], 4096)
        self.assertEqual(row["reasoning_tokens"], 4090)
        for absent in ("reasoning_chars", "input_tokens", "output_tokens", "total_tokens",
                       "unknown_private"):
            self.assertNotIn(absent, row)
        raw = (root / resident.RESIDENT_JOURNAL_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn(PRIVATE_MARKER, raw)


class BudgetAndCompatibilityTest(unittest.TestCase):
    """没有 observer 的旧用法、以及截断失败的预算消耗与调用计数保持不变。"""

    def test_without_an_observer_a_truncated_call_still_sends_once_and_raises(self):
        model = Loopback([{"content": "", "finish_reason": "length"}])
        self.addCleanup(model.stop)
        adapter = assembly.model_cognition(_env(model))

        with self.assertRaises(ag1.CognitionAdapterError) as caught:
            adapter.invoke(_request("opp:trunc-no-observer"))

        self.assertEqual(caught.exception.failure_class,
                         "DECISION_ATTEMPT_FAILED:OUTPUT_TRUNCATED")
        self.assertEqual(model.hits, 1)
        self.assertEqual(adapter.stats()["provider_calls"], 1)

    def test_a_truncated_failure_consumes_exactly_one_request_and_is_not_refunded(self):
        model = Loopback([{"content": "", "finish_reason": "length"}])
        self.addCleanup(model.stop)
        root = Path(tempfile.mkdtemp(prefix="trunc-budget-"))
        env = _env(model)
        env.update({
            "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY": "96",
            "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW": "96",
            "CHIYO_AUTONOMOUS_RUN_ID": "run:trunc",
        })
        budget = assembly.request_budget(env, root / "state")
        sink = _resident(Path(tempfile.mkdtemp(prefix="trunc-budget-sink-")))
        adapter = assembly.model_cognition(env, requests=budget,
                                           observer=sink._cognition_diagnostic)

        with self.assertRaises(ag1.CognitionAdapterError):
            adapter.invoke(_request("opp:trunc-budget"))

        view = budget.view()
        self.assertEqual(view["utc_day_calls"], 1)
        self.assertEqual(view["run_window_calls"], 1)
        self.assertFalse(hasattr(budget, "release"))

    def test_a_truncated_answer_arriving_after_the_deadline_is_not_promoted(self):
        """截断异常即使晚于 deadline 也仍是 provider failure：计数一次、不进 epoch 槽。"""

        class SlowTruncatingClient:
            provider = "fake"
            model = "fake"

            def __init__(self) -> None:
                self.calls = 0

            def complete(self, **_kwargs):
                self.calls += 1
                time.sleep(0.2)
                raise amc.AgencyModelClientError(
                    amc.PROVIDER_OUTPUT_TRUNCATED,
                    "finish_reason=length content_chars=0 reasoning_chars=1",
                    metadata={"finish_reason": "length", "content_chars": 0,
                              "reasoning_chars": 1})

        client = SlowTruncatingClient()
        adapter = aca.ProductionAgencyCognitionAdapter(client=client, timeout_s=0.1)

        with self.assertRaises(ag1.CognitionAdapterError) as caught:
            adapter.invoke(_request("opp:trunc-late"))

        self.assertEqual(caught.exception.failure_class,
                         "DECISION_ATTEMPT_FAILED:OUTPUT_TRUNCATED")
        self.assertEqual(client.calls, 1)
        self.assertEqual(adapter.stats()["provider_calls"], 1)
        self.assertEqual(adapter.stats()["late_results_promoted"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

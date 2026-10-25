"""v0.4 日常连续生活：正式认知诊断接线（assembly -> adapter -> resident journal）。

这些测试走**正式装配缝**：``autonomous_life_assembly.model_cognition`` 构造真实
``ProductionAgencyCognitionAdapter``，provider 是本机 loopback 的脚本化替身；resident 的
``_cognition_diagnostic`` 把 adapter 的遥测写进它自己已经拥有的 journal。没有任何真实网关、
没有凭证读取、没有第二个账本。

覆盖：
* 正常响应与时序事件被记录；
* HTTP 503 / timeout 被安全分类；
* 模型写的键名/值、provider、异常正文等私文一律不落盘；
* required 发送标记写失败时，请求一次都没有发出去；
* 不传 observer 的旧用法逐字兼容；
* observer 不改变持久化请求账本的计数；
* 诊断记录不参与 ``_replay_journal`` 的 wake 计数与 tick 计数。
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

import agency_decision_ag1 as ag1                    # noqa: E402
import autonomous_life_assembly as assembly          # noqa: E402
import autonomous_life_resident as resident          # noqa: E402

#: 模型可能写进键名/值、provider 字段或异常正文里的私文标记；任何一行 journal 都不许出现。
PRIVATE_MARKER = "私文-SHOULD-NOT-APPEAR-9e1f"

DECISION = {
    "decision": "NO_ACTION",
    "selected_candidate_id": None,
    "reason_codes": ["NO_ACTION_CHOSEN"],
    "short_rationale": "nothing legal right now",
    "deferred_candidate_ids": [],
}


def _fixture_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="v04-model-diag-"))


def _request():
    return ag1.CognitiveDecisionRequest(
        opportunity_id="opp:diag", candidate_set_id="cset:diag",
        context_snapshot_id="snap:diag", snapshot_fingerprint="fp:diag",
        candidates=({"candidate_id": "cand:diag", "source_kind": "RESUME_ELIGIBLE",
                     "source_ref": "src:diag", "candidate_kind": "CONSIDER_ARTIFACT"},),
        current_activity_summary=None, capacity_summary={"state": "AVAILABLE"},
        legal_decision_enums=("START", "NO_ACTION", "DEFER"),
        allowed_reason_codes=tuple(sorted(ag1.VALID_DECISION_REASON_CODES)),
        observed_fact_keys=("world.location",), policy_version="diag-test")


class _Loopback:
    """One scripted chat-completions server.  No credential, no real provider."""

    def __init__(self, replies) -> None:
        outer = self
        self.replies = list(replies)
        self.hits = 0
        self.requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server naming
                outer.hits += 1
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length)
                try:
                    outer.requests.append(json.loads(raw.decode("utf-8")))
                except ValueError:
                    outer.requests.append({})
                reply = outer.replies.pop(0) if outer.replies else {"status": 200,
                                                                    "content": json.dumps(DECISION)}
                if reply.get("delay"):
                    time.sleep(float(reply["delay"]))
                status = int(reply.get("status", 200))
                if status != 200:
                    body = json.dumps({"error": {"message": reply.get(
                        "message", "provider refused"), "type": "server_error"}}).encode()
                else:
                    body = json.dumps({
                        "id": "probe", "model": "diag-probe",
                        "choices": [{"index": 0,
                                     "message": {"role": "assistant",
                                                 "content": reply.get("content", "")},
                                     "finish_reason": reply.get("finish_reason", "stop")}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                  "total_tokens": 15},
                    }).encode()
                try:
                    self.send_response(status)
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


def _env(loopback: _Loopback, *, timeout_s: float | None = None) -> dict[str, str]:
    return {"NYA02_MODEL_BASE_URL": loopback.base_url, "NYA02_MODEL_NAME": "diag-probe",
            "NYA02_MODEL_API_KEY": "loopback-key", "NYA02_MODEL_PROVIDER": "custom"}


def _resident(root: Path) -> resident.AutonomousLifeResident:
    return resident.AutonomousLifeResident(state_root=root, environment={},
                                            poll_seconds=2, act=False)


def _rows(root: Path) -> list[dict]:
    path = root / resident.RESIDENT_JOURNAL_FILENAME
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _diagnostics(root: Path) -> list[dict]:
    return [row for row in _rows(root) if row.get("kind") == resident.COGNITION_DIAGNOSTIC_KIND]


class AssemblySeamTest(unittest.TestCase):
    """正常响应 / 503 / timeout 都从 ``assembly.model_cognition`` 这一条真实缝进来。"""

    def _adapter(self, loopback, root, **kwargs):
        self.addCleanup(loopback.stop)
        return assembly.model_cognition(_env(loopback), observer=kwargs.pop(
            "observer", None), **kwargs)

    def test_a_normal_answer_writes_send_and_result_diagnostics(self):
        loopback = _Loopback([{"status": 200, "content": json.dumps({
            **DECISION,
            "private_note": PRIVATE_MARKER,
            "short_rationale": "nothing legal right now " + PRIVATE_MARKER,
        })}])
        root = _fixture_root()
        sink = _resident(root)
        adapter = self._adapter(loopback, root, observer=sink._cognition_diagnostic)

        response = adapter.invoke(_request())

        self.assertEqual(loopback.hits, 1)
        self.assertIsNotNone(response)
        rows = _diagnostics(root)
        events = [row["event"] for row in rows]
        self.assertIn("cognition_send_started", events)
        self.assertIn("cognition_send_response_received", events)
        self.assertIn("cognition_result", events)
        result = next(row for row in rows if row["event"] == "cognition_result")
        self.assertEqual(result["parse_state"], "json_object")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["schema_retry_index"], 0)
        # output_keys 只留数量：模型写的键名一个都不落盘。
        self.assertEqual(result["output_key_count"], len(DECISION) + 1)
        self.assertGreaterEqual(result["content_chars"], 1)
        # 私文既不在结果行，也不在整份 journal 的任何一行。
        raw = (root / resident.RESIDENT_JOURNAL_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn(PRIVATE_MARKER, raw)

    def test_http_503_is_classified_as_a_provider_error(self):
        loopback = _Loopback([{"status": 503, "message": "upstream exploded " + PRIVATE_MARKER}])
        root = _fixture_root()
        sink = _resident(root)
        adapter = self._adapter(loopback, root, observer=sink._cognition_diagnostic)

        with self.assertRaises(ag1.CognitionAdapterError):
            adapter.invoke(_request())

        rows = _diagnostics(root)
        failed = next(row for row in rows if row["event"] == "cognition_send_failed")
        self.assertEqual(failed["error_category"], "PROVIDER_SERVER_ERROR")
        self.assertTrue(failed["response_received"])
        # 2026-10-24: the transport now carries the status it already read; the previous
        # assertion pinned the old loss (status dropped) rather than a contract.
        self.assertEqual(failed["response_status"], 503)
        provider = next(row for row in rows if row["event"] == "cognition_provider_error")
        self.assertEqual(provider["failure_class"],
                         "DECISION_ATTEMPT_FAILED:MODEL_PROVIDER_ERROR")
        raw = (root / resident.RESIDENT_JOURNAL_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn(PRIVATE_MARKER, raw)

    def test_a_timeout_is_classified_without_a_response(self):
        loopback = _Loopback([{"status": 200, "content": json.dumps(DECISION), "delay": 1.0}])
        root = _fixture_root()
        sink = _resident(root)
        adapter = self._adapter(loopback, root, observer=sink._cognition_diagnostic,
                                timeout_s=0.2)

        with self.assertRaises(ag1.CognitionAdapterError):
            adapter.invoke(_request())

        rows = _diagnostics(root)
        failed = next(row for row in rows if row["event"] == "cognition_send_failed")
        self.assertEqual(failed["error_category"], "TRANSPORT_TIMEOUT")
        self.assertFalse(failed["response_received"])
        self.assertNotIn("response_status", failed)


class SafeSinkTest(unittest.TestCase):
    """sink 逐值重建：恶意 key/value 私文不落盘，未知值降级或丢弃。"""

    def test_a_malicious_record_is_rebuilt_value_by_value(self):
        root = _fixture_root()
        sink = _resident(root)
        sink._cognition_diagnostic({
            "event": "cognition_result",
            "provider": PRIVATE_MARKER,
            "model": PRIVATE_MARKER,
            "output_keys": [PRIVATE_MARKER, PRIVATE_MARKER + "-2", "decision"],
            "decision_kind_if_valid": PRIVATE_MARKER,
            "selected_candidate_id_if_valid": PRIVATE_MARKER,
            "error_category": PRIVATE_MARKER,
            "parse_state": PRIVATE_MARKER,
            "response_status": PRIVATE_MARKER,
            "latency_ms": PRIVATE_MARKER,
            "schema_retry_index": True,
            "transport_attempt_id": PRIVATE_MARKER,
            "model_run_ref": "mrun:real:abc123",
            "output_shape": {
                "content_chars": 12,
                "reasoning_chars": PRIVATE_MARKER,
                "finish_reason": PRIVATE_MARKER,
                "max_output_tokens_sent": 2048,
                "unknown_private": PRIVATE_MARKER,
            },
        })

        row = _diagnostics(root)[-1]
        self.assertEqual(row["event"], "cognition_result")
        self.assertEqual(row["output_key_count"], 3)
        self.assertEqual(row["error_category"], "UNKNOWN")
        self.assertEqual(row["parse_state"], "UNKNOWN")
        self.assertEqual(row["finish_reason"], "UNKNOWN")
        self.assertEqual(row["content_chars"], 12)
        self.assertEqual(row["max_output_tokens_sent"], 2048)
        self.assertEqual(row["model_run_ref"], "mrun:real:abc123")
        for absent in ("provider", "model", "output_keys", "decision_kind_if_valid",
                       "selected_candidate_id_if_valid", "response_status", "latency_ms",
                       "schema_retry_index", "transport_attempt_id", "unknown_private",
                       "reasoning_chars"):
            self.assertNotIn(absent, row)
        raw = (root / resident.RESIDENT_JOURNAL_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn(PRIVATE_MARKER, raw)

    def test_an_unknown_event_is_dropped(self):
        root = _fixture_root()
        sink = _resident(root)
        sink._cognition_diagnostic({"event": PRIVATE_MARKER, "response_status": 200})
        self.assertEqual(_diagnostics(root), [])


class SendBoundaryTest(unittest.TestCase):
    """required 发送标记写失败 = 请求一次都不发；optional 诊断不破坏合法决定。"""

    def test_a_journal_failure_before_the_send_marker_sends_nothing(self):
        loopback = _Loopback([{"status": 200, "content": json.dumps(DECISION)}])
        self.addCleanup(loopback.stop)
        root = _fixture_root()
        sink = _resident(root)

        def broken_journal(_record):
            raise OSError("journal is not writable")

        sink._journal = broken_journal
        adapter = assembly.model_cognition(_env(loopback),
                                           observer=sink._cognition_diagnostic)
        with self.assertRaises(OSError):
            adapter.invoke(_request())
        self.assertEqual(loopback.hits, 0,
                         "a send whose boundary could not be recorded must not happen")

    def test_a_journal_failure_after_the_send_does_not_corrupt_the_decision(self):
        loopback = _Loopback([{"status": 200, "content": json.dumps(DECISION)}])
        self.addCleanup(loopback.stop)
        root = _fixture_root()
        sink = _resident(root)
        calls = {"n": 0}

        def flaky_journal(record):
            calls["n"] += 1
            # send_started 与 response_received 是 required 的持久边界，必须写成功；
            # 之后的 cognition_result 是 optional，写失败只应被 adapter 自己吞掉。
            if calls["n"] > 2:
                raise OSError("journal became unavailable")
            sink.__class__._journal(sink, record)

        sink._journal = flaky_journal
        adapter = assembly.model_cognition(_env(loopback),
                                           observer=sink._cognition_diagnostic)
        response = adapter.invoke(_request())
        self.assertEqual(loopback.hits, 1)
        self.assertIsNotNone(response)


class BackwardCompatibilityTest(unittest.TestCase):
    """不传 observer 的旧用法逐字兼容，且账本计数不因 observer 改变。"""

    def test_without_an_observer_nothing_is_written_and_the_call_still_happens(self):
        loopback = _Loopback([{"status": 200, "content": json.dumps(DECISION)}])
        self.addCleanup(loopback.stop)
        adapter = assembly.model_cognition(_env(loopback))
        response = adapter.invoke(_request())
        self.assertIsNotNone(response)
        self.assertEqual(loopback.hits, 1)
        self.assertEqual(adapter.stats()["provider_calls"], 1)

    def test_the_persisted_request_ledger_counts_the_same_with_or_without_an_observer(self):
        def spend(observer):
            loopback = _Loopback([{"status": 200, "content": json.dumps(DECISION)}])
            self.addCleanup(loopback.stop)
            root = _fixture_root()
            state_root = root / "state"
            env = _env(loopback)
            env.update({
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY": "96",
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW": "96",
                "CHIYO_AUTONOMOUS_RUN_ID": "run:diag",
            })
            budget = assembly.request_budget(env, state_root)
            adapter = assembly.model_cognition(
                env, requests=budget,
                observer=(None if observer is None else observer))
            adapter.invoke(_request())
            view = budget.view()
            return int(view["utc_day_calls"]), int(view["run_window_calls"])

        sink = _resident(_fixture_root())
        self.assertEqual(spend(None), (1, 1))
        self.assertEqual(spend(sink._cognition_diagnostic), (1, 1))


class JournalReplayTest(unittest.TestCase):
    """诊断记录不是 wake：重放 journal 后的 tick 与 counters 与加诊断前一致。"""

    def test_a_diagnostic_does_not_add_a_phantom_wake(self):
        root = _fixture_root()
        sink = _resident(root)
        sink._journal({"at": "2026-10-22T00:00:00+00:00", "tick": 1,
                       "events": [], "due": None, "journey": None})
        sink._cognition_diagnostic({"event": "cognition_result", "parse_state": "json_object",
                                    "schema_retry_index": 0})

        replay = _resident(root)
        replay._replay_journal()
        self.assertEqual(replay.ticks, 1)
        self.assertEqual(replay.counters["wakes"], 1)
        self.assertEqual(replay.counters["check_windows"], 0)
        self.assertEqual(replay.counters["model_calls"], 0)


if __name__ == "__main__":
    unittest.main()

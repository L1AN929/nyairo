"""f 短日 12 次 4xx 拒绝：HTTP 状态在 transport 层被丢弃的回归。

2026-10-24 的 f 实例 12 次决策请求全部以 ``PROVIDER_REQUEST_REJECTED`` 失败并计费，
但 resident journal 里 ``response_status`` 全是空值：transport 在
``finish_send`` 已经读到了 HTTP 状态，却在构造 ``AgencyModelClientError`` 时没把它
带上，adapter 只能记 None。按 transport 的既有映射，这个失败类只会由
「4xx 且不是 401/403/429」产生，但到底是 400、404 还是 422 无法回看。

本测试只用本机 loopback HTTP，不读密钥、不发外部请求、不触碰任何真实实例：

* base_url 里的 ``/V1`` 原样保留，最终路径恰好是 ``/V1/chat/completions``，
  不重复 ``/v1``、不改大小写、不走 ``/responses``；
* 请求体仍是既有 chat-completions 形状（``model/messages/max_tokens/stream``，
  ``response_format=json_object``），不为任何 provider 特判；
* 4xx 状态跟着异常走：400/404 保持 ``PROVIDER_REQUEST_REJECTED``，
  401/403 → ``PROVIDER_AUTH_ERROR``，429 → ``PROVIDER_RATE_LIMITED``，
  5xx → ``PROVIDER_SERVER_ERROR``，且 ``exc.status`` 是数字；
* 经 adapter 到 observer 的记录带数字 ``response_status``，映射仍是
  ``DECISION_ATTEMPT_FAILED:MODEL_REQUEST_REJECTED``，不再把 400 和 404 混成一个空值。

外部网关实际支持哪个路径、为何返回 4xx，本测试证明不了，也不改用户给的 ``/V1``。
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import agency_decision_ag1 as ag1                    # noqa: E402
import agency_model_client as amc                    # noqa: E402
import autonomous_life_assembly as assembly          # noqa: E402
from profile_model_transport import OpenAICompatibleTransport  # noqa: E402

MODEL = "gemini-probe"
KEY = "loopback-key"


class _Loopback:
    """Scripted chat-completions server; records the exact path and JSON body."""

    def __init__(self, replies) -> None:
        outer = self
        self.replies = list(replies)
        self.paths: list[str] = []
        self.requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server naming
                outer.paths.append(self.path)
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length)
                try:
                    outer.requests.append(json.loads(raw.decode("utf-8")))
                except ValueError:
                    outer.requests.append({})
                reply = (outer.replies.pop(0) if outer.replies
                         else {"status": 200, "content": "{}"})
                status = int(reply.get("status", 200))
                body = json.dumps({"error": {"message": "refused",
                                             "type": "invalid_request_error"}}).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def origin(self) -> str:
        return "http://127.0.0.1:%d" % self.server.server_port

    @property
    def base_url(self) -> str:
        # 与 f 实例配置同形：路径段是大写 /V1。
        return self.origin + "/V1"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


def _request():
    return ag1.CognitiveDecisionRequest(
        opportunity_id="opp:reject", candidate_set_id="cset:reject",
        context_snapshot_id="snap:reject", snapshot_fingerprint="fp:reject",
        candidates=({"candidate_id": "cand:reject", "source_kind": "RESUME_ELIGIBLE",
                     "source_ref": "src:reject", "candidate_kind": "CONSIDER_ARTIFACT"},),
        current_activity_summary=None, capacity_summary={"state": "AVAILABLE"},
        legal_decision_enums=("START", "NO_ACTION", "DEFER"),
        allowed_reason_codes=tuple(sorted(ag1.VALID_DECISION_REASON_CODES)),
        observed_fact_keys=("world.location",), policy_version="reject-test")


def _env(loopback: _Loopback) -> dict[str, str]:
    return {"NYA02_MODEL_BASE_URL": loopback.base_url, "NYA02_MODEL_NAME": MODEL,
            "NYA02_MODEL_API_KEY": KEY, "NYA02_MODEL_PROVIDER": "custom"}


class UrlShapeTest(unittest.TestCase):
    """最终 URL 与请求体是既有 chat-completions 形状，不做 provider 特判。"""

    def test_uppercase_v1_path_is_preserved_and_not_duplicated(self):
        loopback = _Loopback([{"status": 400}])
        self.addCleanup(loopback.stop)
        transport = OpenAICompatibleTransport(
            base_url=loopback.base_url, api_key=KEY, model=MODEL,
            provider="custom", request_json_object=True)

        with self.assertRaises(amc.AgencyModelClientError) as caught:
            transport.complete(system_prompt="sys", user_prompt="usr",
                               max_output_tokens=64)

        self.assertEqual(caught.exception.failure_class, amc.PROVIDER_REQUEST_REJECTED)
        self.assertEqual(loopback.paths, ["/V1/chat/completions"])
        body = loopback.requests[0]
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["max_tokens"], 64)
        self.assertIs(body["stream"], False)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])

    def test_trailing_slash_does_not_double_the_separator(self):
        loopback = _Loopback([{"status": 400}])
        self.addCleanup(loopback.stop)
        transport = OpenAICompatibleTransport(
            base_url=loopback.base_url + "/", api_key=KEY, model=MODEL)

        with self.assertRaises(amc.AgencyModelClientError):
            transport.complete(system_prompt="sys", user_prompt="usr")

        self.assertEqual(loopback.paths, ["/V1/chat/completions"])


class StatusCarriedTest(unittest.TestCase):
    """transport 已经读到的 HTTP 状态必须跟着异常走。"""

    def test_each_http_status_keeps_its_class_and_number(self):
        cases = ((400, amc.PROVIDER_REQUEST_REJECTED),
                 (404, amc.PROVIDER_REQUEST_REJECTED),
                 (422, amc.PROVIDER_REQUEST_REJECTED),
                 (401, amc.PROVIDER_AUTH_ERROR),
                 (403, amc.PROVIDER_AUTH_ERROR),
                 (429, amc.PROVIDER_RATE_LIMITED),
                 (500, amc.PROVIDER_SERVER_ERROR),
                 (503, amc.PROVIDER_SERVER_ERROR))
        for status, failure_class in cases:
            with self.subTest(status=status):
                loopback = _Loopback([{"status": status}])
                self.addCleanup(loopback.stop)
                transport = OpenAICompatibleTransport(
                    base_url=loopback.base_url, api_key=KEY, model=MODEL)
                with self.assertRaises(amc.AgencyModelClientError) as caught:
                    transport.complete(system_prompt="sys", user_prompt="usr")
                self.assertEqual(caught.exception.failure_class, failure_class)
                self.assertEqual(caught.exception.status, status)

    def test_timeout_still_has_no_status(self):
        """连接阶段失败没有响应，不能凭空补一个状态。"""
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        transport = OpenAICompatibleTransport(
            base_url="http://127.0.0.1:%d/V1" % port, api_key=KEY, model=MODEL,
            timeout_s=0.5)
        with self.assertRaises(amc.AgencyModelClientError) as caught:
            transport.complete(system_prompt="sys", user_prompt="usr")
        self.assertIn(caught.exception.failure_class,
                      (amc.TRANSPORT_CONNECTION, amc.TRANSPORT_TIMEOUT))
        self.assertIsNone(caught.exception.status)


class AdapterObservationTest(unittest.TestCase):
    """经正式 adapter 缝，observer 记录里出现数字状态，映射不变。"""

    def test_request_rejection_records_numeric_status(self):
        loopback = _Loopback([{"status": 404}])
        self.addCleanup(loopback.stop)
        records: list[dict] = []
        adapter = assembly.model_cognition(_env(loopback), observer=records.append,
                                          timeout_s=5.0)
        self.assertIsNotNone(adapter)

        with self.assertRaises(ag1.CognitionAdapterError) as caught:
            adapter.invoke(_request())

        self.assertEqual(caught.exception.failure_class,
                         "DECISION_ATTEMPT_FAILED:MODEL_REQUEST_REJECTED")
        failed = next(r for r in records if r.get("event") == "cognition_send_failed")
        self.assertEqual(failed["error_category"], amc.PROVIDER_REQUEST_REJECTED)
        self.assertEqual(failed["response_status"], 404)
        self.assertTrue(failed["response_received"])
        # 失败行只带数字状态，不带响应正文。
        self.assertNotIn("detail", failed)
        self.assertNotIn("body", failed)


if __name__ == "__main__":
    unittest.main()

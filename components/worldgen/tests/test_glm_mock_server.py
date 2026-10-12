"""GLM 内容链路端到端测试：本地 mock OpenAI 兼容服务器，不发真实请求。"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ._setup import make_valid_docs
from components.worldgen.providers import glm


class _MockHandler(BaseHTTPRequestHandler):
    behavior = {"mode": "ok"}

    def log_message(self, *args):  # 静默
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        if not self.path.endswith("/chat/completions"):
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("Authorization") != "Bearer test-key-123":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "bad key"}')
            return
        mode = self.behavior["mode"]
        if mode == "ok":
            docs_hint = body["messages"][1]["content"]
            entries = {
                "world": {"tagline": "一座安静的海边小镇。", "festivals": ["港口祭"]},
            }
            # 从提示里抓地点 id，全部给出 intro。
            import re

            for match in re.findall(r"id=([a-z0-9_]+)", docs_hint):
                entries[match] = {"intro": f"{match} 的介绍。", "flavor": "一角细节。"}
            content = json.dumps({"entries": entries}, ensure_ascii=False)
        elif mode == "bad_json":
            content = "这不是 JSON"
        else:
            content = json.dumps({"entries": {"world": {"tagline": "x"}}}, ensure_ascii=False)
        payload = {
            "choices": [{"message": {"role": "assistant", "content": content}}]
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class GLMEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/v4"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_enrich_full_chain(self) -> None:
        docs = make_valid_docs(seed=4242)
        result = glm.enrich(
            docs, base_url=self.base_url, api_key="test-key-123", model="mock-model", timeout=10
        )
        content = result["content"]
        self.assertEqual(content["source"], "glm")
        self.assertEqual(content["model"], "mock-model")
        self.assertIn("world", content["entries"])
        for location in docs["topology.json"]["locations"]:
            self.assertIn(location["location_id"], content["entries"])
        # 无 v3 mock 未提供 ambient_candidates → 空列表；用量有记录
        self.assertEqual(result["ambient_candidates"], [])
        self.assertIn("total_tokens", result["usage"])

    def test_enrich_rejects_bad_json_output(self) -> None:
        docs = make_valid_docs(seed=4243)
        _MockHandler.behavior["mode"] = "bad_json"
        try:
            with self.assertRaises(glm.GLMProviderError):
                glm.enrich(
                    docs, base_url=self.base_url, api_key="test-key-123", timeout=10
                )
        finally:
            _MockHandler.behavior["mode"] = "ok"

    def test_enrich_rejects_incomplete_entries(self) -> None:
        docs = make_valid_docs(seed=4244)
        _MockHandler.behavior["mode"] = "incomplete"
        try:
            with self.assertRaises(glm.GLMProviderError):
                glm.enrich(
                    docs, base_url=self.base_url, api_key="test-key-123", timeout=10
                )
        finally:
            _MockHandler.behavior["mode"] = "ok"

    def test_enrich_rejects_http_error(self) -> None:
        docs = make_valid_docs(seed=4245)
        with self.assertRaises(glm.GLMProviderError):
            glm.enrich(
                docs, base_url=self.base_url, api_key="wrong-key", timeout=10
            )


if __name__ == "__main__":
    unittest.main()

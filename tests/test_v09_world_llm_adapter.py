#!/usr/bin/env python3
"""Offline unit tests for v0.9 External LLM World Content Adapter & HTTP wiring.

Uses an isolated local loopback fake OpenAI-compatible HTTP server (0 external
network calls, 0 writes to /root/.nyairo-day) to verify:
1. Zero-write config resolution priority (env -> rc1_profile -> hermes -> none),
   secret-free public status metadata, and stale WSL 172.16.0.0/12 .1 bridge IP
   auto-healing without disk writes.
2. Full POST /api/phone/v09/world-commitments generation_mode='llm' path:
   - anti_spoil_guard blocks private desires BEFORE HTTP call or budget reserve,
   - markdown-fenced JSON + reasoning_content + usage parsed cleanly,
   - catalog items & finite stock persisted atomically,
   - real token usage recorded in life_request_budget.py,
   - idempotent replay hits 0 extra LLM calls,
   - 100% secret redaction across responses, errors, catalog, and budget files.
3. generation_mode='offline' (or omitted) stays strictly llm_calls == 0;
   invalid generation_mode returns 400 INVALID_GENERATION_MODE.
4. Unconfigured LLM -> 503 LLM_UNCONFIGURED; bad JSON / HTTP 500 (even with
   echoed secrets) / length truncation -> 502 LLM_GENERATION_FAILED with 0
   catalog corruption.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unicodedata
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from components.life.scripts import life_request_budget as lrb  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import v09_world_commitments_view as wc_view  # noqa: E402
from components.world.scripts import instance_paths  # noqa: E402
from components.world.scripts import world_content_catalog as wcc  # noqa: E402
from components.world.scripts import world_content_llm_adapter as adapter  # noqa: E402

T0_ISO = "2026-11-21T10:00:00+08:00"
TEST_SECRET_KEY = "sk-nyairo-test-secret-key-998877665544"


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
) -> tuple[int, dict[str, Any]]:
    req_headers = dict(headers or {})
    data_bytes: Optional[bytes] = None
    if body is not None:
        data_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw}
        return int(err.code), parsed


class _FakeOpenAIServer:
    """Thread-safe local fake OpenAI-compatible /v1/chat/completions HTTP server."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.mode: str = "fenced_ok"
        self.response_payload: Optional[dict[str, Any]] = None
        self._lock = threading.Lock()
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
                try:
                    req_json = json.loads(raw_body)
                except ValueError:
                    req_json = {"raw": raw_body}

                with parent._lock:
                    parent.requests.append(
                        {
                            "path": self.path,
                            "auth": self.headers.get("Authorization"),
                            "user_agent": self.headers.get("User-Agent"),
                            "purpose": self.headers.get("X-Chiyo-Purpose"),
                            "body": req_json,
                        }
                    )
                    mode = parent.mode
                    custom = parent.response_payload

                if mode == "http_500_secret_echo":
                    status = 500
                    body = json.dumps(
                        {
                            "error": {
                                "message": f"upstream failure for key {TEST_SECRET_KEY} Bearer {TEST_SECRET_KEY}"
                            }
                        }
                    ).encode("utf-8")
                elif mode == "bad_json":
                    status = 200
                    body = json.dumps(
                        {
                            "id": "cmpl_bad_json",
                            "model": "fake/deepseek-v4.1-flash",
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "stop",
                                    "message": {
                                        "role": "assistant",
                                        "content": "抱歉，这里没有输出 JSON 结构。",
                                    },
                                }
                            ],
                            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                elif mode == "truncated_length":
                    status = 200
                    body = json.dumps(
                        {
                            "id": "cmpl_trunc",
                            "model": "fake/deepseek-v4.1-flash",
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "length",
                                    "message": {
                                        "role": "assistant",
                                        "reasoning_content": "先想一下这间店适合什么……",
                                        "content": '{"items": [{"template_id": "drink_warm"',
                                    },
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 200,
                                "completion_tokens": 1600,
                                "total_tokens": 1800,
                                "completion_tokens_details": {"reasoning_tokens": 1550},
                            },
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                elif mode == "custom" and custom is not None:
                    status = 200
                    body = json.dumps(custom, ensure_ascii=False).encode("utf-8")
                else:
                    # Default: markdown-fenced JSON + thinking reasoning_content + real token usage
                    content_obj = {
                        "items": [
                            {
                                "template_id": "drink_warm",
                                "name_cn": "海雾炙烤玄米茶",
                                "description_cn": "微火烘炒玄米与冬焙茶拼配的暖手热杯",
                                "price_tier": "standard",
                                "price": 460,
                                "stock_initial": 4,
                                "portions": 1,
                                "aroma_cn": "烘炒米香与淡炭香",
                                "taste_note_cn": "清爽微苦回甘",
                            },
                            {
                                "template_id": "snack_portable",
                                "name_cn": "粗盐芝麻薄脆铁盒",
                                "description_cn": "低糖微咸手工薄脆，吃完的方铁盒可留作案头收纳",
                                "price_tier": "standard",
                                "price": 620,
                                "stock_initial": 3,
                                "portions": 4,
                                "taste_note_cn": "烘烤白芝麻与淡海盐",
                                "reusable_container_spec": {
                                    "container_kind": "cookie_tin",
                                    "label_cn": "雾灰方铁皮点心盒",
                                    "capacity_slots": 4,
                                    "drinkware": False,
                                    "supported_capabilities": [
                                        "STORE_ITEMS",
                                        "ATTACH_LABEL",
                                        "ADD_NOTE",
                                    ],
                                    "material_cn": "马口铁",
                                },
                            },
                        ],
                        "events": [],
                    }
                    fenced = "```json\n" + json.dumps(content_obj, ensure_ascii=False, indent=2) + "\n```"
                    status = 200
                    body = json.dumps(
                        {
                            "id": "cmpl_ok_1",
                            "model": "fake/deepseek-v4.1-flash",
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "stop",
                                    "message": {
                                        "role": "assistant",
                                        "reasoning_content": "结合海边秋季与小店调性，先拟两件克制日常商品。",
                                        "content": fenced,
                                    },
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 412,
                                "completion_tokens": 288,
                                "total_tokens": 700,
                                "completion_tokens_details": {"reasoning_tokens": 120},
                            },
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")

                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = int(self._server.server_port)
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self.requests)

    def clear_requests(self) -> None:
        with self._lock:
            self.requests.clear()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3)


class WorldContentLLMAdapterConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nyairo-v09-llm-cfg-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_config_priority_and_secret_free_status_zero_write(self) -> None:
        rc1_dir = self.tmp / "rc1"
        hermes_dir = self.tmp / "hermes"
        rc1_dir.mkdir()
        hermes_dir.mkdir()

        (rc1_dir / "config.yaml").write_text(
            "model:\n"
            "  provider: custom\n"
            "  default: rc1/deepseek-v4.1-flash\n"
            "  base_url: http://127.0.0.1:4100/v1\n"
            "  api_key: ${RC1_SECRET_KEY}\n",
            encoding="utf-8",
            newline="\n",
        )
        (rc1_dir / ".env").write_text(
            f"RC1_SECRET_KEY={TEST_SECRET_KEY}\n",
            encoding="utf-8",
            newline="\n",
        )
        (hermes_dir / "config.yaml").write_text(
            "model:\n"
            "  provider: opencode-go\n"
            "  default: deepseek-v4-flash\n",
            encoding="utf-8",
            newline="\n",
        )
        (hermes_dir / ".env").write_text(
            "OPENCODE_GO_BASE_URL=https://opencode.example.invalid/v1\n"
            "OPENCODE_GO_API_KEY=sk-hermes-secret-1234567890\n",
            encoding="utf-8",
            newline="\n",
        )

        before = _snapshot_tree(self.tmp)

        # Priority 1: env overrides rc1_profile & hermes
        env_p1 = {
            "NYA02_MODEL_BASE_URL": "http://127.0.0.1:4999/v1?secret=should_not_appear",
            "NYA02_MODEL_NAME": "env/model-alpha",
            "NYA02_MODEL_API_KEY": TEST_SECRET_KEY,
            "NYA02_MODEL_PROVIDER": "env-provider",
            "NYAIRO_RC1_PROFILE": str(rc1_dir),
            "HERMES_HOME": str(hermes_dir),
        }
        st_p1 = adapter.read_external_llm_status_zero_write(env_p1, gateway_ip_fn=lambda: None)
        self.assertEqual(set(st_p1.keys()), set(adapter.SAFE_STATUS_KEYS))
        self.assertTrue(st_p1["configured"])
        self.assertEqual(st_p1["source"], "env")
        self.assertEqual(st_p1["provider"], "env-provider")
        self.assertEqual(st_p1["model_name"], "env/model-alpha")
        self.assertEqual(st_p1["endpoint_host"], "127.0.0.1:4999")
        self.assertTrue(st_p1["api_key_present"])
        self.assertEqual(st_p1["default_generation_mode"], "offline")
        self.assertNotIn(TEST_SECRET_KEY, json.dumps(st_p1))
        self.assertNotIn("should_not_appear", json.dumps(st_p1))

        # Priority 2: rc1_profile (with ${RC1_SECRET_KEY} expanded from .env)
        env_p2 = {"NYAIRO_RC1_PROFILE": str(rc1_dir), "HERMES_HOME": str(hermes_dir)}
        cfg_p2 = adapter.resolve_external_llm_config_zero_write(env_p2, gateway_ip_fn=lambda: None)
        self.assertTrue(cfg_p2["configured"])
        self.assertEqual(cfg_p2["source"], "rc1_profile")
        self.assertEqual(cfg_p2["model_name"], "rc1/deepseek-v4.1-flash")
        self.assertEqual(cfg_p2["api_key"], TEST_SECRET_KEY)
        st_p2 = adapter.read_external_llm_status_zero_write(env_p2, gateway_ip_fn=lambda: None)
        self.assertEqual(st_p2["source"], "rc1_profile")
        self.assertEqual(st_p2["endpoint_host"], "127.0.0.1:4100")
        self.assertNotIn("api_key", st_p2)
        self.assertNotIn(TEST_SECRET_KEY, json.dumps(st_p2))

        # Priority 3: hermes fallback when rc1_profile missing
        env_p3 = {
            "NYAIRO_RC1_PROFILE": str(self.tmp / "missing_rc1"),
            "HERMES_HOME": str(hermes_dir),
        }
        st_p3 = adapter.read_external_llm_status_zero_write(env_p3, gateway_ip_fn=lambda: None)
        self.assertTrue(st_p3["configured"])
        self.assertEqual(st_p3["source"], "hermes")
        self.assertEqual(st_p3["provider"], "opencode-go")
        self.assertEqual(st_p3["model_name"], "deepseek-v4-flash")
        self.assertEqual(st_p3["endpoint_host"], "opencode.example.invalid")
        self.assertNotIn("sk-hermes-secret", json.dumps(st_p3))

        # Priority 4: none configured
        env_none = {
            "NYAIRO_RC1_PROFILE": str(self.tmp / "missing_rc1"),
            "HERMES_HOME": str(self.tmp / "missing_hermes"),
        }
        st_none = adapter.read_external_llm_status_zero_write(env_none, gateway_ip_fn=lambda: None)
        self.assertFalse(st_none["configured"])
        self.assertEqual(st_none["source"], "none")
        self.assertFalse(st_none["api_key_present"])
        self.assertEqual(st_none["default_generation_mode"], "offline")

        # 100% zero-write across all resolutions
        self.assertEqual(_snapshot_tree(self.tmp), before)

    def test_02_stale_wsl_bridge_ip_auto_heals_without_disk_writes(self) -> None:
        rc1_dir = self.tmp / "rc1_stale"
        rc1_dir.mkdir()
        cfg_file = rc1_dir / "config.yaml"
        original_yaml = (
            "model:\n"
            "  provider: custom\n"
            "  default: workbuddy-wg/cn:deepseek-v4.1-flash\n"
            "  base_url: http://172.23.128.1:3425/v1\n"
            f"  api_key: {TEST_SECRET_KEY}\n"
        )
        cfg_file.write_text(original_yaml, encoding="utf-8", newline="\n")
        before = _snapshot_tree(self.tmp)

        env = {
            "NYAIRO_RC1_PROFILE": str(rc1_dir),
            "HERMES_HOME": str(self.tmp / "no_hermes"),
        }
        # Stale 172.23.128.1 -> live gateway 172.22.128.1
        resolved = adapter.resolve_external_llm_config_zero_write(
            env, gateway_ip_fn=lambda: "172.22.128.1"
        )
        self.assertTrue(resolved["configured"])
        self.assertTrue(resolved["stale_bridge_detected"])
        self.assertEqual(resolved["live_gateway_host"], "172.22.128.1")
        self.assertEqual(resolved["base_url"], "http://172.22.128.1:3425/v1")
        self.assertEqual(resolved["endpoint_host"], "172.22.128.1:3425")

        status = adapter.read_external_llm_status_zero_write(
            env, gateway_ip_fn=lambda: "172.22.128.1"
        )
        self.assertTrue(status["stale_bridge_detected"])
        self.assertEqual(status["endpoint_host"], "172.22.128.1:3425")
        self.assertNotIn(TEST_SECRET_KEY, json.dumps(status))

        # Matching gateway IP -> stale_bridge_detected is False
        same_gw = adapter.resolve_external_llm_config_zero_write(
            env, gateway_ip_fn=lambda: "172.23.128.1"
        )
        self.assertFalse(same_gw["stale_bridge_detected"])
        self.assertEqual(same_gw["base_url"], "http://172.23.128.1:3425/v1")

        # Verify zero disk writes: config.yaml on disk stays byte-identical!
        self.assertEqual(cfg_file.read_text(encoding="utf-8"), original_yaml)
        self.assertEqual(_snapshot_tree(self.tmp), before)

        with mock.patch.dict(os.environ, adapter.os.environ, clear=False):
            pass
        fake_cp = mock.Mock(returncode=0, stdout="default via 172.22.128.1 dev eth0 proto kernel\n")
        with mock.patch.object(adapter.os, "name", "posix"), mock.patch.object(
            adapter.subprocess, "run", return_value=fake_cp
        ):
            self.assertEqual(adapter.detect_wsl_default_gateway_ip(), "172.22.128.1")

    def test_03_record_actual_usage_idempotent_and_total_tokens_fallback(self) -> None:
        norm = adapter._normalize_usage({"prompt_tokens": 300, "completion_tokens": 120, "total_tokens": 0})
        self.assertEqual(norm["total_tokens"], 420)

        life_root = self.tmp / "budget_idemp"
        budget = adapter.open_content_request_budget(life_root)
        from datetime import datetime, timezone
        res = budget.reserve("content", est_tokens=1200, now=datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc))
        first = adapter.record_actual_usage_in_request_budget(
            budget,
            res,
            {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            model_name="deepseek-v4.1-flash",
            provider="custom",
        )
        self.assertTrue(first["recorded"])
        self.assertEqual(first["day_tokens"], 1500)

        # Re-recording the exact same reservation must NOT double-charge day_tokens
        second = adapter.record_actual_usage_in_request_budget(
            budget,
            res,
            {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            model_name="deepseek-v4.1-flash",
            provider="custom",
        )
        self.assertTrue(second["recorded"])
        self.assertEqual(second["day_tokens"], 1500)


class WorldContentLLMHTTPIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nyairo-v09-llm-http-")
        self.root = Path(self._tmp.name) / "inst"
        self.root.mkdir(parents=True, exist_ok=True)
        self.fake_llm = _FakeOpenAIServer()
        self.csrf = "csrf-v09-llm-test"
        self.cap_token = "cap_v09_llm_test_0123456789abcdef0123456789"
        cap = phone_capability.Capability(
            token=self.cap_token,
            subject="chiyo.global",
            endpoint="http://127.0.0.1:0",
            issued_at=T0_ISO,
        )
        svc = phone_service.PhoneService(
            {
                "root": str(self.root),
                "subject": "chiyo.global",
                "world_home": str(self.root / "one-day" / "world-home"),
                "supply_socket": str(self.root / "run" / "supply.sock"),
                "private_items_artifact_id": "art_private_items",
                "artifact_grant_id": "grant_private_items",
                "workspace_id": "ws_default",
            },
            clock=lambda: T0_ISO,
        )
        self.srv = phone_server.create_server(svc, csrf_token=self.csrf, port=0, capability=cap)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.srv.server_address
        self.base_url = f"http://{host}:{port}"
        self.url = self.base_url + wc_view.ROUTE_V09_WORLD_COMMITMENTS

        self._configured_env = {
            "NYA02_MODEL_BASE_URL": self.fake_llm.base_url,
            "NYA02_MODEL_NAME": "fake/deepseek-v4.1-flash",
            "NYA02_MODEL_API_KEY": TEST_SECRET_KEY,
            "NYA02_MODEL_PROVIDER": "custom-fake",
            "NYAIRO_RC1_PROFILE": str(Path(self._tmp.name) / "no_rc1"),
            "HERMES_HOME": str(Path(self._tmp.name) / "no_hermes"),
        }
        self._unconfigured_env = {
            "NYAIRO_RC1_PROFILE": str(Path(self._tmp.name) / "no_rc1"),
            "HERMES_HOME": str(Path(self._tmp.name) / "no_hermes"),
        }

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=3)
        self.fake_llm.close()
        self._tmp.cleanup()

    def _partner_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}

    def _subject_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf, "X-Phone-Capability": self.cap_token}

    def test_01_get_status_zero_write_and_full_llm_generation_flow(self) -> None:
        with mock.patch.dict(os.environ, self._configured_env, clear=True):
            # 1. GET /api/phone/v09/world-commitments -> 100% zero-write, 0 LLM requests, secret-free status
            before_get = _snapshot_tree(self.root)
            code_get, body_get = _http_json(self.url)
            self.assertEqual(code_get, 200)
            self.assertEqual(self.fake_llm.request_count, 0)
            self.assertEqual(_snapshot_tree(self.root), before_get)
            status_obj = body_get["data"]["llm_generator_status"]
            self.assertEqual(set(status_obj.keys()), set(adapter.SAFE_STATUS_KEYS))
            self.assertTrue(status_obj["configured"])
            self.assertEqual(status_obj["provider"], "custom-fake")
            self.assertEqual(status_obj["model_name"], "fake/deepseek-v4.1-flash")
            self.assertEqual(status_obj["endpoint_host"], f"127.0.0.1:{self.fake_llm.port}")
            self.assertTrue(status_obj["api_key_present"])
            self.assertEqual(status_obj["default_generation_mode"], "offline")
            self.assertEqual(status_obj["source"], "env")
            self.assertNotIn(TEST_SECRET_KEY, json.dumps(body_get))

            # 2. anti_spoil_guard blocks private desires BEFORE HTTP call & BEFORE budget write!
            for bad_ctx in (
                {"world_id": "shiomi_city", "private_desires": ["想偷偷吃抹茶蛋糕"]},
                {"world_id": "shiomi_city", "everyday_desires": ["想买蓝釉杯"]},
                {"world_id": "shiomi_city", "chat_transcript": ["昨天聊到的秘密"]},
            ):
                code_spoil, body_spoil = _http_json(
                    self.url,
                    method="POST",
                    headers=self._partner_headers(),
                    body={
                        "action": "operator_generate_shop_arrivals",
                        "store_id": "cafe",
                        "generation_mode": "llm",
                        "world_context": bad_ctx,
                        "operation_key": "op-spoil-blocked",
                    },
                )
                self.assertEqual(code_spoil, 400, body_spoil)
                self.assertEqual(body_spoil["reason_code"], "ANTI_SPOIL_PRIVATE_KEY_FORBIDDEN")
            self.assertEqual(self.fake_llm.request_count, 0)
            self.assertEqual(_snapshot_tree(self.root), before_get)

            # 3. Full generation_mode='llm' -> parses markdown-fenced JSON, persists stock, records budget tokens!
            code_llm, body_llm = _http_json(
                self.url,
                method="POST",
                headers=self._partner_headers(),
                body={
                    "action": "operator_generate_shop_arrivals",
                    "store_id": "cafe",
                    "trigger_reason": "OPERATOR_AI_RESTOCK",
                    "generation_mode": "llm",
                    "world_context": {
                        "world_id": "shiomi_city",
                        "theme": "coastal",
                        "season": "autumn",
                        "weather": "clear",
                    },
                    "operation_key": "op-llm-cafe-1",
                },
            )
            self.assertEqual(code_llm, 200, body_llm)
            self.assertTrue(body_llm["ok"])
            self.assertEqual(body_llm["source_kind"], "llm")
            self.assertEqual(body_llm["generation_mode"], "llm")
            self.assertEqual(body_llm["llm_calls"], 1)
            self.assertEqual(body_llm["model_name"], "fake/deepseek-v4.1-flash")
            self.assertEqual(body_llm["provider"], "custom-fake")
            self.assertEqual(body_llm["usage"]["prompt_tokens"], 412)
            self.assertEqual(body_llm["usage"]["completion_tokens"], 288)
            self.assertEqual(body_llm["usage"]["total_tokens"], 700)
            self.assertEqual(body_llm["usage"]["reasoning_tokens"], 120)
            self.assertEqual(self.fake_llm.request_count, 1)
            self.assertNotIn(TEST_SECRET_KEY, json.dumps(body_llm))

            # Verify fake server received clean public prompt & proper headers
            sent = self.fake_llm.requests[0]
            self.assertEqual(sent["auth"], f"Bearer {TEST_SECRET_KEY}")
            self.assertTrue(str(sent["user_agent"]).startswith("nyairo-life/"))
            self.assertNotIn("private_desires", json.dumps(sent["body"], ensure_ascii=False))

            # Verify catalog items + stock persisted cleanly on disk
            reg_items = body_llm["receipt"]["registered_items"]
            self.assertEqual(len(reg_items), 2)
            names = {row["name_cn"] for row in reg_items}
            self.assertEqual(names, {"海雾炙烤玄米茶", "粗盐芝麻薄脆铁盒"})
            cat_snap = wcc.read_dynamic_catalog_zero_write(self.root, store_id="cafe")
            self.assertEqual(len(cat_snap["items"]), 2)
            tea = next(x for x in cat_snap["items"] if x["name_cn"] == "海雾炙烤玄米茶")
            self.assertEqual(tea["stock_initial"], 4)
            self.assertEqual(tea["stock_remaining"], 4)
            self.assertEqual(tea["price"], 460)
            self.assertNotIn(TEST_SECRET_KEY, wcc.catalog_state_path(self.root).read_text(encoding="utf-8"))

            # Verify real token usage recorded in life_request_budget.py!
            life_state = instance_paths.resolve_instance_paths(self.root).life_state_root
            budget = lrb.LifeRequestBudget(life_state, utc_day_max=96, daily_token_budget=50_000)
            b_view = budget.view()
            self.assertEqual(b_view["state"], lrb.STATE_OK)
            self.assertEqual(b_view["utc_day_calls"], 1)
            self.assertGreaterEqual(b_view["utc_day_tokens"], 700)
            recent = b_view["recent_tokens"][-1]
            self.assertEqual(recent["kind"], lrb.REQUEST_KIND_CONTENT)
            self.assertEqual(recent["actual_tokens"], 700)
            self.assertEqual(recent["prompt_tokens"], 412)
            self.assertEqual(recent["completion_tokens"], 288)
            self.assertEqual(recent["model"], "fake/deepseek-v4.1-flash")
            self.assertNotIn(TEST_SECRET_KEY, (life_state / lrb.BUDGET_FILENAME).read_text(encoding="utf-8"))

            # 4. Idempotent replay of the LLM op does NOT invoke the external LLM again!
            code_rep, body_rep = _http_json(
                self.url,
                method="POST",
                headers=self._partner_headers(),
                body={
                    "action": "operator_generate_shop_arrivals",
                    "store_id": "cafe",
                    "trigger_reason": "OPERATOR_AI_RESTOCK",
                    "generation_mode": "llm",
                    "world_context": {
                        "world_id": "shiomi_city",
                        "theme": "coastal",
                        "season": "autumn",
                        "weather": "clear",
                    },
                    "operation_key": "op-llm-cafe-1",
                },
            )
            self.assertEqual(code_rep, 200, body_rep)
            self.assertEqual(body_rep["status"], "already_applied")
            self.assertEqual(body_rep["llm_calls"], 0)
            self.assertEqual(self.fake_llm.request_count, 1)

            # 5. Subject-only generate_world_content_batch also supports generation_mode='llm'
            code_subj, body_subj = _http_json(
                self.url,
                method="POST",
                headers=self._subject_headers(),
                body={
                    "action": "generate_world_content_batch",
                    "store_id": "bookstore",
                    "generation_mode": "llm",
                    "operation_key": "op-llm-book-1",
                },
            )
            self.assertEqual(code_subj, 200, body_subj)
            self.assertEqual(body_subj["view"], "subject")
            self.assertEqual(body_subj["source_kind"], "llm")
            self.assertEqual(body_subj["llm_calls"], 1)
            self.assertEqual(self.fake_llm.request_count, 2)

    def test_02_offline_mode_and_invalid_generation_mode(self) -> None:
        with mock.patch.dict(os.environ, self._configured_env, clear=True):
            # Default omitted generation_mode -> offline, llm_calls == 0
            code_def, body_def = _http_json(
                self.url,
                method="POST",
                headers=self._partner_headers(),
                body={
                    "action": "operator_generate_shop_arrivals",
                    "store_id": "bookstore",
                    "operation_key": "op-offline-default",
                },
            )
            self.assertEqual(code_def, 200, body_def)
            self.assertEqual(body_def["llm_calls"], 0)
            self.assertEqual(body_def["generation_mode"], "offline")
            self.assertEqual(body_def["source_kind"], "offline")
            self.assertEqual(self.fake_llm.request_count, 0)

            # Explicit generation_mode='offline' -> llm_calls == 0
            code_off, body_off = _http_json(
                self.url,
                method="POST",
                headers=self._partner_headers(),
                body={
                    "action": "operator_generate_shop_arrivals",
                    "store_id": "cafe",
                    "generation_mode": "offline",
                    "operation_key": "op-offline-explicit",
                },
            )
            self.assertEqual(code_off, 200, body_off)
            self.assertEqual(body_off["llm_calls"], 0)
            self.assertEqual(self.fake_llm.request_count, 0)

            # Invalid generation_mode -> 400 INVALID_GENERATION_MODE, 0 writes
            before_bad = _snapshot_tree(self.root)
            for bad_mode in ("turbo", "", "LLM", 123, None, True):
                code_bad, body_bad = _http_json(
                    self.url,
                    method="POST",
                    headers=self._partner_headers(),
                    body={
                        "action": "operator_generate_shop_arrivals",
                        "store_id": "home_goods_store",
                        "generation_mode": bad_mode,
                        "operation_key": "op-invalid-mode",
                    },
                )
                self.assertEqual(code_bad, 400, (bad_mode, body_bad))
                self.assertEqual(body_bad["reason_code"], "INVALID_GENERATION_MODE")
            self.assertEqual(self.fake_llm.request_count, 0)
            self.assertEqual(_snapshot_tree(self.root), before_bad)

    def test_03_unconfigured_503_and_provider_failures_502_zero_catalog_corruption(self) -> None:
        # 1. Unconfigured LLM -> 503 LLM_UNCONFIGURED + Chinese guidance + 0 writes
        with mock.patch.dict(os.environ, self._unconfigured_env, clear=True):
            before_unconf = _snapshot_tree(self.root)
            code_503, body_503 = _http_json(
                self.url,
                method="POST",
                headers=self._partner_headers(),
                body={
                    "action": "operator_generate_shop_arrivals",
                    "store_id": "cafe",
                    "generation_mode": "llm",
                    "operation_key": "op-unconf-1",
                },
            )
            self.assertEqual(code_503, 503, body_503)
            self.assertEqual(body_503["reason_code"], "LLM_UNCONFIGURED")
            self.assertIn("尚未配置外接大模型", body_503["detail"])
            self.assertEqual(self.fake_llm.request_count, 0)
            self.assertEqual(_snapshot_tree(self.root), before_unconf)

        # 2. Seed one offline catalog item first, then prove 502 failures never corrupt catalog
        with mock.patch.dict(os.environ, self._configured_env, clear=True):
            code_seed, _ = _http_json(
                self.url,
                method="POST",
                headers=self._partner_headers(),
                body={
                    "action": "operator_generate_shop_arrivals",
                    "store_id": "bookstore",
                    "generation_mode": "offline",
                    "operation_key": "op-seed-before-fail",
                },
            )
            self.assertEqual(code_seed, 200)
            cat_path = wcc.catalog_state_path(self.root)
            cat_before_bytes = cat_path.read_bytes()

            for idx, fail_mode in enumerate(("bad_json", "http_500_secret_echo", "truncated_length")):
                self.fake_llm.mode = fail_mode
                code_fail, body_fail = _http_json(
                    self.url,
                    method="POST",
                    headers=self._partner_headers(),
                    body={
                        "action": "operator_generate_shop_arrivals",
                        "store_id": "cafe",
                        "generation_mode": "llm",
                        "force_cooldown_bypass": True,
                        "operation_key": f"op-fail-{idx}",
                    },
                )
                self.assertEqual(code_fail, 502, (fail_mode, body_fail))
                self.assertEqual(body_fail["reason_code"], "LLM_GENERATION_FAILED")
                self.assertNotIn(TEST_SECRET_KEY, json.dumps(body_fail))
                # Catalog file untouched (0 corruption / half-written state!)
                self.assertEqual(cat_path.read_bytes(), cat_before_bytes)

            # Also test empty items list with finish_reason='stop' & usage > est_tokens -> 502 AND overage recorded!
            self.fake_llm.mode = "custom"
            self.fake_llm.response_payload = {
                "id": "cmpl_empty_items",
                "model": "fake/deepseek-v4.1-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"items": []}'},
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 1600, "total_tokens": 1800},
            }
            code_empty, body_empty = _http_json(
                self.url,
                method="POST",
                headers=self._partner_headers(),
                body={
                    "action": "operator_generate_shop_arrivals",
                    "store_id": "cafe",
                    "generation_mode": "llm",
                    "force_cooldown_bypass": True,
                    "operation_key": "op-fail-empty-overage",
                },
            )
            self.assertEqual(code_empty, 502, body_empty)
            self.assertEqual(cat_path.read_bytes(), cat_before_bytes)

            # Both bad_json (150) and empty-items overage (1800) are recorded despite 502!
            life_state = instance_paths.resolve_instance_paths(self.root).life_state_root
            budget = lrb.LifeRequestBudget(life_state, utc_day_max=96, daily_token_budget=50_000)
            b_view = budget.view()
            actuals = {int(row.get("actual_tokens") or 0) for row in b_view.get("recent_tokens") or []}
            self.assertIn(150, actuals, b_view)
            self.assertIn(1800, actuals, b_view)
            self.assertEqual(b_view["utc_day_tokens"], 5400, b_view)

    def test_04_owned_files_hygiene_pure_lf_no_bom_zero_emoji_neutral_pronoun(self) -> None:
        owned = (
            "components/world/scripts/world_content_llm_adapter.py",
            "components/phone/v09_world_commitments_view.py",
            "tests/test_v09_world_llm_adapter.py",
            "scripts/v09_real_llm_live_roundtrip.py",
        )
        for rel in owned:
            raw = (REPO_ROOT / rel).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {rel}")
            self.assertNotIn(b"\r\n", raw, f"CRLF found in {rel}")
            text = raw.decode("utf-8")
            emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"unicode.So emoji in {rel}: {emojis[:5]}")
            self.assertEqual(text.count("\u5979"), 0, f"hardcoded pronoun in {rel}")
            self.assertEqual(text.count("\u4ed6"), 0, f"hardcoded pronoun in {rel}")
            self.assertEqual(text.count("\u5343\u4ee3"), 0, f"hardcoded persona name in {rel}")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""End-to-end local protocol soak & counterfactual causal tests for v0.2 + v0.3 + 36B.

Runs 100% locally on loopback (127.0.0.1:0 PhoneHTTPServer + 127.0.0.1:0 Mock OpenAI
/v1/chat/completions server) with zero external LLM calls, zero HTTP_PROXY interference,
HTTPS_PROXY=http://127.0.0.1:1, cleared external API keys, and neutral persona defaults.
"""
from __future__ import annotations

import hashlib
import http.client
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from chiyo_bundle import lived_context_bridge  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import everyday_desire_lifecycle as v03_desires  # noqa: E402
from components.life.scripts import life_dwell as v03_dwell  # noqa: E402
from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from plugins import chiyo as chiyo_plugin  # noqa: E402

SUBJECT = "companion_subject"
CSRF_TOKEN = "soak-csrf-token-20261114"
CAP_TOKEN = "soak-subject-cap-token-20261114"
EXT_KEY_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.name.endswith((".sqlite-shm", ".sqlite-wal")):
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        try:
            st = p.lstat()
        except FileNotFoundError:
            continue
        if p.is_file() and not p.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


class _MockChatCompletionsServer:
    """Loopback 127.0.0.1:0 OpenAI /v1/chat/completions recorder (zero external calls)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/v1/chat/completions":
                    self.send_response(404)
                    self.end_headers()
                    return
                raw_len = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(raw_len).decode("utf-8"))
                outer.requests.append(body)
                resp = {
                    "id": f"chatcmpl-local-{len(outer.requests)}",
                    "object": "chat.completion",
                    "created": 1794628800,
                    "model": body.get("model", "local-mock-v1"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "嗯，我在听。"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 64, "completion_tokens": 6, "total_tokens": 70},
                }
                encoded = json.dumps(resp, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = int(self._httpd.server_address[1])
        self.endpoint = f"http://127.0.0.1:{self.port}/v1/chat/completions"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2.0)


class V03FullLiveProtocolSoakTest(unittest.TestCase):
    """Real loopback PhoneHTTPServer + Mock /v1/chat/completions multi-day soak & causal suite."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._saved_env = dict(os.environ)
        for k in EXT_KEY_NAMES:
            os.environ.pop(k, None)
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("http_proxy", None)
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:1"
        os.environ["https_proxy"] = "http://127.0.0.1:1"
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"

        cls.tmpdir = Path(tempfile.mkdtemp(prefix="v03-full-soak-"))
        cls.root = cls.tmpdir / "day_root"
        (cls.root / "world").mkdir(parents=True, exist_ok=True)

        cls.mock_llm = _MockChatCompletionsServer()
        cap_path = cls.tmpdir / "phone.cap"
        cap_path.write_text(CAP_TOKEN + "\n", encoding="utf-8")
        cls.cap_path = cap_path

        instance = {
            "root": str(cls.root),
            "subject": SUBJECT,
            "world_home": str(cls.root / "world"),
            "supply_socket": str(cls.root / "run" / "supply.sock"),
            "private_items_artifact_id": "art_priv_soak",
            "artifact_grant_id": "grant_soak",
            "workspace_id": "ws_soak",
        }
        cls.clock_dt = datetime(2026, 11, 14, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        cls.service = phone_service.PhoneService(
            instance,
            world_clock=lambda: cls.clock_dt,
        )
        cap_obj = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-14T09:00:00+08:00",
        )
        cls.httpd = phone_server.create_server(
            cls.service,
            csrf_token=CSRF_TOKEN,
            port=0,
            capability=cap_obj,
        )
        cls.phone_port = int(cls.httpd.server_address[1])
        cls.base_url = f"http://127.0.0.1:{cls.phone_port}"
        cls.cap_obj = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint=cls.base_url,
            issued_at="2026-11-14T09:00:00+08:00",
        )
        cls.httpd.set_capability(cls.cap_obj)
        cls._http_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls._http_thread.start()

        cls.profile_home = cls.tmpdir / "hermes_profile"
        (cls.profile_home / "chiyo").mkdir(parents=True, exist_ok=True)
        (cls.profile_home / "chiyo" / "config.json").write_text(
            json.dumps(
                {
                    "cli_owner": True,
                    "local_owner_uid": os.getuid() if hasattr(os, "getuid") else 0,
                    "memory": False,
                    "memory_tool_policy": "restricted",
                    "phone": {
                        "enabled": True,
                        "base_url": cls.base_url,
                        "profile_home": str(cls.root),
                        "capability": CAP_TOKEN,
                        "write_enabled": True,
                        "private_artifacts": True,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._http_thread.join(timeout=2.0)
        cls.mock_llm.close()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def _http(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        as_subject: bool = False,
        with_csrf: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers: dict[str, str] = {
            "Host": f"127.0.0.1:{self.phone_port}",
            "Accept": "application/json",
        }
        body_bytes = None
        if method == "POST":
            body_bytes = json.dumps(
                payload if payload is not None else {}, ensure_ascii=False
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body_bytes))
            if with_csrf:
                headers["X-CSRF-Token"] = CSRF_TOKEN
                headers["Origin"] = self.base_url
                headers["Sec-Fetch-Site"] = "same-origin"
        if as_subject:
            headers["X-Phone-Capability"] = CAP_TOKEN
        if extra_headers:
            headers.update(extra_headers)

        conn = http.client.HTTPConnection("127.0.0.1", self.phone_port, timeout=5.0)
        try:
            conn.request(method, path, body=body_bytes, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            return int(resp.status), (json.loads(raw) if raw else {})
        finally:
            conn.close()

    def _send_to_mock_llm(self, lived_ctx: str, user_text: str) -> dict[str, Any]:
        payload = {
            "model": "local-mock-v1",
            "messages": [
                {"role": "system", "content": lived_ctx},
                {"role": "user", "content": user_text},
            ],
        }
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.mock_llm.port, timeout=5.0)
        try:
            conn.request(
                "POST",
                "/v1/chat/completions",
                body=body_bytes,
                headers={
                    "Host": f"127.0.0.1:{self.mock_llm.port}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body_bytes)),
                },
            )
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            self.assertEqual(resp.status, 200)
            return json.loads(raw)
        finally:
            conn.close()

    def test_01_character_pack_export_import_attest_and_clear_session_threads(self) -> None:
        self.assertLessEqual(len(chiyo_plugin._PHONE_AND_WORLD_GUIDANCE), 1200)
        loop_bridge.verify_frozen_anchors(ROOT)

        before_export = _snapshot_tree(self.root)
        code, exported = self._http(
            "POST",
            "/api/phone/character/pack",
            {"action": "export_pack", "include_sexual_seed": False},
        )
        after_export = _snapshot_tree(self.root)
        self.assertEqual(code, 200, exported)
        self.assertTrue(exported["ok"])
        self.assertEqual(before_export, after_export, "export_pack must be 100% zero-write")

        pack_doc = dict(exported["data"]["pack"])
        pack_doc["pack_id"] = "linche-neutral-v1"
        pack_doc["version"] = 2
        pack_doc["display_name"] = "林澈"
        pack_doc["pronouns"] = "TA"
        pack_doc["adult"] = {"life_stage": "working_adult", "declared_age": 24}
        pack_doc["persona"] = {"core": "安静温润、说话留有分寸，习惯在窗边修理旧收音机。"}

        code_imp, imp_body = self._http(
            "POST",
            "/api/phone/character/pack",
            {"action": "import_pack_json", "pack_json": json.dumps(pack_doc, ensure_ascii=False)},
        )
        self.assertEqual(code_imp, 200, imp_body)
        self.assertEqual(imp_body["data"]["active_pack"]["display_name"], "林澈")
        self.assertEqual(imp_body["data"]["active_pack"]["pronouns"], "TA")

        code_att, att_body = self._http(
            "POST",
            "/api/phone/character/pack",
            {
                "action": "attest_character_pack",
                "pack_id": "linche-neutral-v1",
                "version": 2,
                "adult_confirmed": True,
                "fictional_confirmed": True,
                "life_stage": "working_adult",
                "declared_age": 24,
                "attested_by": "deployer",
                "note": "verified neutral adult pack",
            },
        )
        self.assertEqual(code_att, 200, att_body)
        self.assertTrue(att_body["data"]["pack_sha256"])
        self.assertEqual(att_body["data"]["attested_by"], "deployer")

        # Hold session thread -> clear_session_threads on new session
        c_hold, b_hold = self._http(
            "POST",
            "/api/phone/autonomy/rhythm",
            {"action": "hold_subject_topic", "topic_label": "窗台绿植换盆", "private_note": "等有空再聊"},
            as_subject=True,
        )
        self.assertEqual(c_hold, 200, b_hold)
        c_clr, b_clr = self._http(
            "POST",
            "/api/phone/autonomy/rhythm",
            {"action": "clear_session_threads", "session_id": "sess_day2_fresh"},
            as_subject=True,
        )
        self.assertEqual(c_clr, 200, b_clr)
        self.assertTrue(b_clr["ok"])

    def test_02_zero_write_all_get_surfaces_and_csrf_gate(self) -> None:
        # Missing CSRF on POST must hard-reject 403 CSRF_TOKEN_MISSING without touching disk
        snap_before_csrf = _snapshot_tree(self.root)
        c_no_csrf, b_no_csrf = self._http(
            "POST",
            "/api/phone/v03/ecology",
            {"action": "earn_income", "amount_cents": 1500, "source": "proofreading"},
            as_subject=True,
            with_csrf=False,
        )
        self.assertEqual(c_no_csrf, 403, b_no_csrf)
        self.assertEqual(_snapshot_tree(self.root), snap_before_csrf)

        get_paths = (
            "/api/phone/character/pack",
            "/api/phone/body/sensory",
            "/api/phone/autonomy/rhythm",
            "/api/phone/emotion/tension",
            "/api/phone/stance/containment",
            "/api/phone/intimacy/adult",
            "/api/phone/v03/clock",
            "/api/phone/v03/ecology",
            "/api/phone/v03/embodied-social",
            "/api/phone/v04/neighborhood",
            "/api/phone/v04/builder-trylive",
            "/api/phone/v04/elastic-play",
            "/api/phone/v02/agency-depth",
            "/api/phone/v02/crossmodal",
            "/api/phone/v02/runtime-migration",
            "/api/phone/v02/recall-projection",
            "/api/phone/v02/common-ground",
        )
        snap_before_gets = _snapshot_tree(self.root)
        for p in get_paths:
            code_p, body_p = self._http("GET", p, as_subject=False)
            self.assertEqual(code_p, 200, f"GET {p} partner failed: {body_p}")
            code_s, body_s = self._http("GET", p, as_subject=True)
            self.assertEqual(code_s, 200, f"GET {p} subject failed: {body_s}")
        self.assertEqual(
            _snapshot_tree(self.root),
            snap_before_gets,
            "All GET endpoints across v02/v03/v04 must be 100% zero-write",
        )

    def test_03_v02_recall_common_ground_and_lived_context_counterfactual_soak(self) -> None:
        # 1. Record episodic trace & verify 4-level recall projection via live HTTP
        c_rec, b_rec = self._http(
            "POST",
            "/api/phone/v02/recall-projection",
            {
                "action": "record_episodic_trace",
                "memory_id": "mem_rainy_bookstore_01",
                "topic": "雨天旧书店避雨",
                "exact_detail": "在街角旧书店买了薄荷色书签，还借了店主的伞",
                "gist_summary": "下雨天一起去过街角旧书店",
                "category": "共同经历",
                "occurred_at": "2026-11-13T17:20:00+08:00",
                "emotional_salience": 0.82,
            },
            as_subject=True,
        )
        self.assertEqual(c_rec, 200, b_rec)
        self.assertTrue(b_rec["ok"])

        # 2. Record private unshared experience vs shared item in common ground
        c_cg, b_cg = self._http(
            "POST",
            "/api/phone/v02/common-ground",
            {
                "action": "record_ground_item",
                "item_id": "cg_mint_bookmark",
                "topic": "薄荷色书签夹进了散文集",
                "ground_status": "PRIVATE_UNSHARED",
                "item_kind": "experience",
            },
            as_subject=True,
        )
        self.assertEqual(c_cg, 200, b_cg)
        self.assertTrue(b_cg["ok"])

        # Partner default view must redact PRIVATE_UNSHARED details
        c_cg_partner, b_cg_partner = self._http("GET", "/api/phone/v02/common-ground", as_subject=False)
        self.assertEqual(c_cg_partner, 200)
        partner_dump = json.dumps(b_cg_partner, ensure_ascii=False)
        self.assertNotIn("薄荷色书签夹进了散文集", partner_dump)

        # 3. Dwell & desire lifecycle pure local round-trip + zero-write context projection
        dwell_entered = v03_dwell.apply_life_dwell_action(
            self.root,
            {
                "action": "start_dwell",
                "place_node": "window_side",
                "label": "靠窗看散文集",
                "declared_seconds": 1200,
            },
            now_iso="2026-11-14T10:05:00+08:00",
        )
        self.assertEqual(dwell_entered["action_receipt"]["action"], "start_dwell")
        desire_seeded = v03_desires.apply_everyday_desire_action(
            self.root,
            {
                "action": "emerge_concern",
                "domain": "comfort",
                "origin_kind": "immediate_opportunity",
                "origin_refs": ["window_breeze_01"],
                "expression": "想泡一杯热桂花乌龙",
                "persistence": "retained",
            },
            now_iso="2026-11-14T10:06:00+08:00",
        )
        self.assertEqual(desire_seeded["action_receipt"]["action"], "emerge_concern")

        snap_ctx_before = _snapshot_tree(self.root)
        ctx_normal = lived_context_bridge.build_lived_turn_context(
            self.root,
            incoming_text="你在忙什么呀？",
            base_url=self.base_url,
            capability=CAP_TOKEN,
        )
        self.assertEqual(_snapshot_tree(self.root), snap_ctx_before, "build_lived_turn_context must be zero-write")
        self.assertIn("林澈", ctx_normal)
        self.assertLessEqual(len(ctx_normal), lived_context_bridge.MAX_LIVED_CONTEXT_CHARS)

        llm_resp = self._send_to_mock_llm(ctx_normal, "你在忙什么呀？")
        self.assertEqual(llm_resp["choices"][0]["message"]["content"], "嗯，我在听。")
        self.assertEqual(len(self.mock_llm.requests), 1)

        # 4. Counterfactual: declare REFUSED_FIRM via phone_tool -> context surfaces refusal lock
        tool_res = json.loads(
            phone_tool.phone_act(
                "autonomy_rhythm",
                {
                    "action": "declare_refusal",
                    "topic_label": "代写周报差事",
                    "category": "errand_outsourcing",
                    "reason_summary": "不想被当成写报告工具",
                },
                hermes_home=str(self.profile_home),
            )
        )
        self.assertTrue(tool_res.get("ok"), tool_res)

        ctx_refused = lived_context_bridge.build_lived_turn_context(
            self.root,
            incoming_text="帮我把周报写了",
            base_url=self.base_url,
            capability=CAP_TOKEN,
        )
        self.assertNotEqual(ctx_normal, ctx_refused)
        self.assertIn("代写周报差事", ctx_refused)

        # Daily loop candidate collection must also remain 100% zero-write
        snap_loop_before = _snapshot_tree(self.root)
        loop_bundle = loop_bridge.collect_v02_v03_loop_candidates(
            self.root, now_iso="2026-11-14T10:15:00+08:00"
        )
        self.assertEqual(_snapshot_tree(self.root), snap_loop_before)
        self.assertFalse(loop_bundle["resident_context_summary"]["capitulation_allowed"])
        self.assertEqual(loop_bundle["resident_context_summary"]["active_stance_lock_count"], 1)


if __name__ == "__main__":
    unittest.main()

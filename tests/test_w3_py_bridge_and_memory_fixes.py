#!/usr/bin/env python3
"""Wave-3 regression suite for Python & Hermes bridge deep-fixes (P0-1, BUG-TALK-03/06, P1-1..4, M3, M4, H5)."""
from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
for _sub in (
    REPO_ROOT / "components" / "phone",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "world" / "scripts",
):
    if str(_sub) not in sys.path:
        sys.path.insert(0, str(_sub))

from chiyo_bundle import lived_context_bridge, phone_tool  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import v04_builder_trylive_view  # noqa: E402


def _ensure_hermes_stubs(home_dir: Path):
    """Install lightweight Hermes host stubs if running outside full Hermes venv."""
    if "hermes_constants" not in sys.modules:
        hc = types.ModuleType("hermes_constants")
        hc.get_hermes_home = lambda: home_dir
        sys.modules["hermes_constants"] = hc
    if "agent.context_compressor" not in sys.modules:
        if "agent" not in sys.modules:
            sys.modules["agent"] = types.ModuleType("agent")
        cc = types.ModuleType("agent.context_compressor")

        class _StubCompressor:
            def __init__(self, **kwargs):
                self._kw = kwargs

            def on_session_reset(self):
                pass

            def get_status(self):
                return {}

        cc.ContextCompressor = _StubCompressor
        sys.modules["agent.context_compressor"] = cc


class _MiniPhoneHandler(BaseHTTPRequestHandler):
    profile_home = ""

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path in ("/state", "/api/phone/state"):
            body = json.dumps({
                "ok": True,
                "status": "ok",
                "csrf_token": "test-csrf-w3",
                "instance": {
                    "fingerprint": phone_tool._profile_fingerprint(self.profile_home),
                },
                "data": {"profile_home": self.profile_home},
            }).encode("utf-8")
        elif self.path == "/api/phone/wallet":
            body = json.dumps({
                "ok": True,
                "status": "ok",
                "data": {"balance": 6000, "currency": "CNY"},
            }).encode("utf-8")
        else:
            body = json.dumps({"ok": True, "status": "ok", "data": {}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Wave3PythonFixesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="w3_py_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # Task 2a: strip_injected_turn_envelope + M0 zero envelope pollution
    # ------------------------------------------------------------------
    def test_01_strip_injected_turn_envelope_handles_single_multi_and_nested(self) -> None:
        single = (
            "[当前真实处境与自主节律（非对方台词，请勿复述）]\n"
            "她正在窗边看雨。\n\n"
            "晚上吃什么呀？"
        )
        self.assertEqual(lived_context_bridge.strip_injected_turn_envelope(single), "晚上吃什么呀？")

        multi_with_marker = (
            "[当前真实处境与自主节律（非对方台词，仅供本轮参考）]\n"
            "第一段导引：刚忙完手头的事。\n\n"
            "第二段导引：外面还在下毛毛雨。\n\n"
            "【伴侣此刻发来的原话】\n"
            "要不要煮碗热面？"
        )
        self.assertEqual(
            lived_context_bridge.strip_injected_turn_envelope(multi_with_marker),
            "要不要煮碗热面？",
        )

        nested = (
            "[当前真实处境与自主节律（非对方台词）]\n外层提示\n\n"
            "【伴侣此刻发来的原话】\n"
            "[当前真实处境与自主节律（仅本轮生效，请自然体现）]\n内层提示\n\n"
            "真正的原话在这里"
        )
        self.assertEqual(
            lived_context_bridge.strip_injected_turn_envelope(nested),
            "真正的原话在这里",
        )

        plain = "[普通书名备注] 今天读完第三章啦"
        self.assertEqual(lived_context_bridge.strip_injected_turn_envelope(plain), plain)

    def test_02_select_context_never_writes_injected_envelope_into_m0(self) -> None:
        _ensure_hermes_stubs(self.tmp)
        from chiyo_bundle import hermes_plugin

        uid = os.getuid() if hasattr(os, "getuid") else None
        cfg_dir = self.tmp / "chiyo"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(
            json.dumps({
                "owner": "local-owner",
                "cli_owner": True,
                "local_owner_uid": uid,
                "memory": True,
                "gateway_bindings": {"api_server": ["dm:local-owner"]},
            }),
            encoding="utf-8",
        )

        recorded_events: list[str] = []

        class _FakeBridge:
            def write_user_event(self, **kw):
                recorded_events.append(kw["user_text"])
                return {"status": "inserted", "event_id": "ev-1"}

        class _FakeProj:
            controls = ()
            healthy = True

            def recalls(self, items):
                return items, []

            def active_corrections(self):
                return []

        fake_svc = types.SimpleNamespace(
            owner="local-owner",
            memory=True,
            binding={"conversation_id": "conv-1"},
            life_wrapper=None,
            native=types.SimpleNamespace(
                m37_bridge=_FakeBridge(),
                memory_controls=types.SimpleNamespace(projection=lambda: _FakeProj()),
                m37_resolver=lambda **kw: [],
                world_body=None,
            ),
        )

        # Ensure app.m3 and telegram_adapter stubs exist if not on path
        fake_m3 = types.ModuleType("app.m3")
        fake_m3.classify_intent = lambda txt: ("P0", {})
        fake_tg = types.ModuleType("telegram_adapter")
        fake_tg.utc_now = lambda: "2026-11-13T08:00:00+00:00"

        with mock.patch.object(hermes_plugin, "get_hermes_home", return_value=self.tmp), \
             mock.patch.object(hermes_plugin, "services", return_value=fake_svc), \
             mock.patch.dict(sys.modules, {"app": types.ModuleType("app"), "app.m3": fake_m3, "telegram_adapter": fake_tg}):
            eng = hermes_plugin.ChiyoContextEngine()
            eng.on_session_start("sess-m0-clean", platform="api_server", conversation_id="dm:local-owner")
            wrapped_input = (
                "[当前真实处境与自主节律（非对方台词，请勿当成伴侣在说）]\n"
                "内部导引绝不能进M0_SECRET_GUIDANCE_TOKEN\n\n"
                "【伴侣此刻发来的原话】\n"
                "我们周末去逛书店吧"
            )
            out_msgs = eng.select_context(
                [{"role": "user", "content": wrapped_input}],
                incoming_message={"role": "user", "content": wrapped_input},
            )

        self.assertEqual(recorded_events, ["我们周末去逛书店吧"])
        self.assertNotIn("M0_SECRET_GUIDANCE_TOKEN", recorded_events[0])
        self.assertIsNotNone(out_msgs)
        self.assertNotIn("M0_SECRET_GUIDANCE_TOKEN", out_msgs[-1]["content"])
        self.assertIn("我们周末去逛书店吧", out_msgs[-1]["content"])

    # ------------------------------------------------------------------
    # Task 2b: BUG-TALK-06 gateway session_id auth for chiyo_phone
    # ------------------------------------------------------------------
    def test_03_chiyo_phone_session_id_blocks_unbound_telegram_and_allows_bound_api_server(self) -> None:
        _ensure_hermes_stubs(self.tmp)
        from chiyo_bundle import hermes_plugin

        hermes_plugin.clear_session_auth_context()
        profile_home = self.tmp / "day_inst"
        profile_home.mkdir(parents=True, exist_ok=True)

        class _H(_MiniPhoneHandler):
            pass

        _H.profile_home = str(profile_home.resolve())
        httpd = HTTPServer(("127.0.0.1", 0), _H)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            base_url = f"http://127.0.0.1:{httpd.server_port}"
            uid = os.getuid() if hasattr(os, "getuid") else None
            cfg_dir = self.tmp / "chiyo"
            cfg_dir.mkdir(parents=True, exist_ok=True)

            # Case 1: cli_owner=True, unbound external telegram session MUST be rejected via session_id!
            cfg_cli_true = {
                "owner": "local-owner",
                "cli_owner": True,
                "local_owner_uid": uid,
                "memory": True,
                "gateway_bindings": {
                    "telegram": ["telegram:bot:dm:42"],
                    "api_server": ["dm:local-owner"],
                },
                "phone": {
                    "enabled": True,
                    "base_url": base_url,
                    "profile_home": str(profile_home.resolve()),
                },
            }
            (cfg_dir / "config.json").write_text(json.dumps(cfg_cli_true), encoding="utf-8")

            with mock.patch.object(hermes_plugin, "get_hermes_home", return_value=self.tmp), \
                 mock.patch.object(hermes_plugin, "services", return_value=None):
                eng_stranger = hermes_plugin.ChiyoContextEngine()
                eng_stranger.on_session_start(
                    "sess-tg-stranger",
                    platform="telegram",
                    conversation_id="telegram:bot:dm:999",
                )
                gate_res = hermes_plugin.memory_tool_gate(
                    "chiyo_phone",
                    {"action": "wallet"},
                    session_id="sess-tg-stranger",
                )
                self.assertIsInstance(gate_res, dict)
                self.assertEqual(gate_res.get("action"), "block")

                tool_raw = phone_tool.phone_handler(
                    {"action": "wallet"},
                    hermes_home=self.tmp,
                    session_id="sess-tg-stranger",
                )
                tool_res = json.loads(tool_raw)
                self.assertFalse(tool_res["ok"])
                self.assertEqual(tool_res["reason_code"], "PHONE_ENTRY_NOT_AUTHORIZED")

            # Case 2: cli_owner=False, bound api_server DM ("dm:local-owner") MUST succeed via session_id!
            cfg_cli_false = dict(cfg_cli_true, cli_owner=False)
            (cfg_dir / "config.json").write_text(json.dumps(cfg_cli_false), encoding="utf-8")

            with mock.patch.object(hermes_plugin, "get_hermes_home", return_value=self.tmp), \
                 mock.patch.object(hermes_plugin, "services", return_value=None):
                eng_owner = hermes_plugin.ChiyoContextEngine()
                eng_owner.on_session_start(
                    "sess-api-owner",
                    platform="api_server",
                    conversation_id="dm:local-owner",
                )
                gate_ok = hermes_plugin.memory_tool_gate(
                    "chiyo_phone",
                    {"action": "wallet"},
                    session_id="sess-api-owner",
                )
                self.assertIsNone(gate_ok)

                ok_raw = phone_tool.phone_handler(
                    {"action": "wallet"},
                    hermes_home=self.tmp,
                    session_id="sess-api-owner",
                )
                ok_res = json.loads(ok_raw)
                self.assertTrue(ok_res["ok"], ok_res)
                self.assertEqual(((ok_res.get("result") or {}).get("data") or {}).get("balance"), 6000)

                # Delegated child session via session_id MUST be refused
                eng_child = hermes_plugin.ChiyoContextEngine()
                eng_child.on_session_start(
                    "sess-api-delegated",
                    platform="api_server",
                    conversation_id="dm:local-owner",
                    parent_session_id="sess-api-owner",
                )
                del_raw = phone_tool.phone_handler(
                    {"action": "wallet"},
                    hermes_home=self.tmp,
                    session_id="sess-api-delegated",
                )
                del_res = json.loads(del_raw)
                self.assertFalse(del_res["ok"])
                self.assertEqual(del_res["reason_code"], "PHONE_DELEGATED_SOURCE_REFUSED")
        finally:
            httpd.shutdown()
            httpd.server_close()

    # ------------------------------------------------------------------
    # Task 3: lived_context_bridge ephemeral hint, budget <2500, desktop fallback, v03/v04 & priority
    # ------------------------------------------------------------------
    def test_04_ephemeral_turn_hint_one_shot_unlink_and_desktop_identity_fallback(self) -> None:
        day_root = self.tmp / "day"
        phone_dir = day_root / "phone"
        run_dir = day_root / "run"
        phone_dir.mkdir(parents=True)
        run_dir.mkdir(parents=True)
        cap_file = run_dir / "phone-capability.json"
        cap_file.write_text(json.dumps({"endpoint": "http://127.0.0.1:9", "token": "x" * 24}), encoding="utf-8")

        # .nyairo-desktop.json fallback when character_pack is unset
        (self.tmp / ".nyairo-desktop.json").write_text(
            json.dumps({"name": "小满", "pronoun": "她"}),
            encoding="utf-8",
        )

        hint_file = phone_dir / "ephemeral_turn_hint.json"
        hint_file.write_text(
            json.dumps({
                "created_at_unix": time.time(),
                "ttl_seconds": 90,
                "guidance": "刚从院子收完衣服进屋，呼吸微喘。",
            }),
            encoding="utf-8",
        )

        cfg = {"phone": {"enabled": True, "capability_file": str(cap_file)}}
        first = lived_context_bridge.render_lived_companion_context(self.tmp, cfg)
        self.assertFalse(hint_file.exists(), "ephemeral_turn_hint.json must be unlinked on first read")
        self.assertTrue(
            first.startswith("[当前真实处境与自主节律（仅本轮生效，请自然体现，勿复述本说明）]\n刚从院子收完衣服进屋"),
            first,
        )
        self.assertIn("个体身份：小满（代词：她）", first)

        # Second call must NOT contain the one-shot hint
        second = lived_context_bridge.render_lived_companion_context(self.tmp, cfg)
        self.assertNotIn("刚从院子收完衣服进屋", second)

        # Expired hint is also immediately unlinked and NOT injected
        hint_file.write_text(
            json.dumps({
                "created_at_unix": time.time() - 600,
                "ttl_seconds": 60,
                "guidance": "EXPIRED_HINT_MUST_NOT_APPEAR",
            }),
            encoding="utf-8",
        )
        third = lived_context_bridge.render_lived_companion_context(self.tmp, cfg)
        self.assertFalse(hint_file.exists())
        self.assertNotIn("EXPIRED_HINT_MUST_NOT_APPEAR", third)

    def test_05_budget_threshold_v03_v04_fields_and_priority_truncation(self) -> None:
        day_root = self.tmp / "rich_day"
        phone_dir = day_root / "phone"
        world_dir = day_root / "world"
        phone_dir.mkdir(parents=True)
        world_dir.mkdir(parents=True)

        # Initial ¥60 (6000 cents) must NOT trigger tight budget mode even if budget_tight=True
        (phone_dir / "v03_ecology.json").write_text(
            json.dumps({
                "economy": {
                    "available_balance_cents": 6000,
                    "budget_tight": True,
                    "partner_stipend_escrow": [
                        {"status": "PENDING_ESCROW", "amount_cents": 3500, "note": "买热饮"}
                    ],
                },
                "illness": {
                    "phase": "CONVALESCENT",
                    "recovery_supports": [{"kind": "温水与退烧贴"}],
                },
            }),
            encoding="utf-8",
        )
        # Canonical v03_embodied_social_state.json filename
        (phone_dir / "v03_embodied_social_state.json").write_text(
            json.dumps({
                "embodied_contact": {"partner_presence": "same_room"},
            }),
            encoding="utf-8",
        )
        (world_dir / "v04_neighborhood_state.json").write_text(
            json.dumps({
                "environment": {
                    "weather": "drizzle",
                    "ground_wetness": "wet",
                    "wind_level": "breezy",
                    "temperature_feel": "chilly",
                    "power_water_notice": "下午三点短时停水检修",
                },
                "outing_continuity": {
                    "target_place_id": "bookstore",
                    "paused_at_node": "covered_walkway",
                    "halt_reason": "避雨稍停",
                },
                "plant_lifecycle": {"hydration": 22.5, "stage": "wilting_recoverable"},
                "public_notices": [{"title": "旧书交换市集周六开放"}],
                "shared_shelf": {"items": [{"label": "黄铜书签尺", "holder": "in_shelf"}]},
            }),
            encoding="utf-8",
        )
        (phone_dir / "v04_elastic_play.json").write_text(
            json.dumps({
                "day_direction": {"status": "active", "direction": "整理阳台花盆"},
                "shared_notebook": {"entries": [{"text": "记下巷口新开的面包房"}]},
                "temporary_game": {
                    "active": True,
                    "status": "active",
                    "game_kind": "odd_naming",
                    "prop_label": "旧纽扣罐",
                    "turns": ["第一回：叫它月亮零钱", "第二回：叫它冬日船票"],
                },
                "prepared_surprises": [
                    {"status": "prepared_waiting", "title": "SPOILER_SECRET_991"}
                ],
            }),
            encoding="utf-8",
        )
        # Huge low-priority anchor to force 1800-char budget pressure
        (phone_dir / "wardrobe.json").write_text(
            json.dumps({"homewear_style": "棉麻家居服" * 120}),
            encoding="utf-8",
        )

        ctx_60 = lived_context_bridge.render_lived_companion_context(
            self.tmp, {"day_root": str(day_root)}
        )
        self.assertLessEqual(len(ctx_60), lived_context_bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertNotIn("手头攒钱克制期", ctx_60)
        self.assertNotIn("SPOILER_SECRET_991", ctx_60)
        for must_have in (
            "odd_naming",
            "旧纽扣罐",
            "冬日船票",
            "待回应伴侣零花钱托管:¥35(买热饮)",
            "生病照料支持:温水与退烧贴",
            "细雨",
            "下午三点短时停水检修",
            "covered_walkway",
            "门廊盆栽偏干发蔫",
            "旧书交换市集周六开放",
            "黄铜书签尺",
            "记下巷口新开的面包房",
        ):
            self.assertIn(must_have, ctx_60, f"Missing {must_have!r} in:\n{ctx_60}")

        # Lower balance below ¥25 (1800 cents) -> tight budget hint activates
        (phone_dir / "v03_ecology.json").write_text(
            json.dumps({"economy": {"available_balance_cents": 1800, "budget_tight": True}}),
            encoding="utf-8",
        )
        ctx_18 = lived_context_bridge.render_lived_companion_context(
            self.tmp, {"day_root": str(day_root)}
        )
        self.assertIn("手头攒钱克制期", ctx_18)

    # ------------------------------------------------------------------
    # Task 4: PhoneService nested one-day memory env + enable_memory authority_path
    # ------------------------------------------------------------------
    def test_06_phone_service_effective_memory_env_and_enable_authority_path(self) -> None:
        from components.phone import life as phone_life

        inst_root = self.tmp / "rc1-inst"
        nested_dir = inst_root / "one-day"
        nested_dir.mkdir(parents=True)
        db_key = phone_life.memory_env_names()["database"][0]
        owner_key = phone_life.memory_env_names()["owner"][0]
        (nested_dir / "instance.json").write_text(
            json.dumps({
                "environment": {
                    db_key: str(nested_dir / "memory" / "evidence.sqlite"),
                    owner_key: "nested-owner-id",
                }
            }),
            encoding="utf-8",
        )

        # Dummy bare PhoneService-like instance testing _effective_memory_env / memory_owner / enable_memory
        svc = object.__new__(phone_service.PhoneService)
        svc.root = inst_root
        svc.environment = {}
        svc.instance = {}

        with mock.patch.object(
            phone_life,
            "read_nested_life",
            return_value=json.loads((nested_dir / "instance.json").read_text(encoding="utf-8")),
        ):
            eff = svc._effective_memory_env()
            self.assertEqual(eff.get(db_key), str(nested_dir / "memory" / "evidence.sqlite"))
            self.assertEqual(svc.memory_owner(), "nested-owner-id")

        # Unbound instance calling enable_memory passes authority_path when signature accepts it
        unbound_root = self.tmp / "fresh-inst"
        unbound_root.mkdir(parents=True)
        svc2 = object.__new__(phone_service.PhoneService)
        svc2.root = unbound_root
        svc2.environment = {}
        svc2.instance = {}

        captured: dict[str, object] = {}

        def _fake_enabler_with_auth(instance_dir, *, owner=None, operation_key=None, authority_path=None):
            captured["instance_dir"] = instance_dir
            captured["owner"] = owner
            captured["operation_key"] = operation_key
            captured["authority_path"] = authority_path
            return {"ok": True, "status": "READY", "owner": owner, "restart_required": True}

        with mock.patch.object(phone_life, "read_nested_life", side_effect=RuntimeError("none")), \
             mock.patch.object(phone_life, "enable_instance_memory", _fake_enabler_with_auth):
            ans = svc2.enable_memory(operation_key="op-mem-1")
        self.assertTrue(ans["ok"], ans)
        self.assertEqual(
            captured["authority_path"],
            unbound_root.parent / "fresh-inst-memory-authority.sqlite",
        )

        # Mock enabler WITHOUT authority_path parameter is still called cleanly without TypeError
        def _legacy_stub(instance_dir, *, owner=None, operation_key=None):
            return {"ok": True, "status": "READY", "owner": owner}

        with mock.patch.object(phone_life, "read_nested_life", side_effect=RuntimeError("none")), \
             mock.patch.object(phone_life, "enable_instance_memory", _legacy_stub):
            ans2 = svc2.enable_memory(operation_key="op-mem-2")
        self.assertTrue(ans2["ok"], ans2)

    # ------------------------------------------------------------------
    # Task 5: world-editor active WorldGen package loading & Sec-Fetch-Site guard
    # ------------------------------------------------------------------
    def test_07_world_editor_loads_active_worldgen_package_and_preserves_guard(self) -> None:
        from components.worldgen.generator import generate_world
        from components.worldgen.schema import write_package

        wg_dir = self.tmp / "active_town"
        docs = generate_world(seed=42, theme="coastal_town", world_name="海风小镇")
        write_package(wg_dir, docs)
        expected_id = docs["world.json"]["world_id"]

        ed_dir = REPO_ROOT / "components" / "world-editor"
        if str(ed_dir) not in sys.path:
            sys.path.insert(0, str(ed_dir))
        spec = importlib.util.spec_from_file_location("we_server_w3", ed_dir / "server.py")
        assert spec and spec.loader
        we_server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(we_server)

        data_dir = self.tmp / "editor_data"
        # First create default cafe draft (revision 0), then reopen with world_package_dir!
        srv0 = we_server.create_server(data_dir, port=0)
        srv0.server_close()

        srv = we_server.create_server(data_dir, port=0, world_package_dir=wg_dir)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            host = f"127.0.0.1:{srv.server_port}"
            conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
            conn.request("GET", "/api/bootstrap", headers={"Host": host, "Sec-Fetch-Site": "same-origin"})
            resp = conn.getresponse()
            boot = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(resp.status, 200)
            self.assertEqual(boot["draft"]["package"]["meta"]["id"], expected_id)
            self.assertEqual(boot.get("active_world_id"), expected_id)

            conn.request("GET", "/api/active-world", headers={"Host": host, "Sec-Fetch-Site": "same-origin"})
            r_act = conn.getresponse()
            act_doc = json.loads(r_act.read().decode("utf-8"))
            self.assertEqual(r_act.status, 200)
            self.assertEqual(act_doc["package"]["meta"]["id"], expected_id)
            self.assertEqual(act_doc["world_id"], expected_id)

            # Sec-Fetch-Site: cross-site MUST still be rejected with 403!
            conn.request("GET", "/api/active-world", headers={"Host": host, "Sec-Fetch-Site": "cross-site"})
            r_cross = conn.getresponse()
            _ = r_cross.read()
            self.assertEqual(r_cross.status, 403)
            conn.close()
        finally:
            srv.shutdown()
            srv.server_close()

    # ------------------------------------------------------------------
    # Task 6: v04_builder_trylive_view optional pronoun support
    # ------------------------------------------------------------------
    def test_08_v04_builder_trylive_optional_pronoun_field(self) -> None:
        day_root = self.tmp / "trylive_day"
        day_root.mkdir(parents=True)

        code_def, body_def = v04_builder_trylive_view.read_v04_builder_trylive_view(day_root)
        self.assertEqual(code_def, 200)
        self.assertEqual(body_def["identity"]["pronoun"], "TA")

        code_she, body_she = v04_builder_trylive_view.mutate_v04_builder_trylive_view(
            day_root,
            {
                "action": "audit_routes_or_rehearse",
                "rehearse_route_id": "home_to_bookstore",
                "pronoun": "她",
                "operation_key": "op-pron-she",
            },
            actor_side="partner",
        )
        self.assertEqual(code_she, 200, body_she)
        self.assertEqual(body_she["identity"]["pronoun"], "她")

        code_bad, body_bad = v04_builder_trylive_view.mutate_v04_builder_trylive_view(
            day_root,
            {
                "action": "audit_routes_or_rehearse",
                "pronoun": "非法代词",
            },
            actor_side="partner",
        )
        self.assertEqual(code_bad, 400)
        self.assertEqual(body_bad["reason_code"], "INVALID_FIELDS")


if __name__ == "__main__":
    unittest.main()

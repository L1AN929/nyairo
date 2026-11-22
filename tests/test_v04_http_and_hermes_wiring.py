#!/usr/bin/env python3
"""Real loopback HTTP + Hermes ``phone_tool`` + ``lived_context_bridge`` + ``nyairo_day`` clock
integration tests for all three v0.4 surfaces:
  1. ``/api/phone/v04/neighborhood`` (``phone.v04.neighborhood.v1``)
  2. ``/api/phone/v04/builder-trylive`` (``phone.v04.builder_trylive.v1``)
  3. ``/api/phone/v04/elastic-play`` (``phone.v04.elastic_play.v1``)

Strictly zero paid LLM calls (``llm_calls == 0``).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
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
    str(Path(__file__).resolve().parent),
    str(ROOT / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

_HERMES_VENDOR = ROOT / "vendor" / "hermes"
if _HERMES_VENDOR.exists() and str(_HERMES_VENDOR) not in sys.path:
    sys.path.append(str(_HERMES_VENDOR))
elif "agent.context_compressor" not in sys.modules:
    import types as _types

    _agent_pkg = _types.ModuleType("agent")
    _cc_mod = _types.ModuleType("agent.context_compressor")

    class _StubContextCompressor:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def on_session_reset(self) -> None:
            pass

        def get_status(self) -> dict[str, Any]:
            return {}

    _cc_mod.ContextCompressor = _StubContextCompressor  # type: ignore[attr-defined]
    sys.modules["agent"] = _agent_pkg
    sys.modules["agent.context_compressor"] = _cc_mod
    if "hermes_constants" not in sys.modules:
        _hc_mod = _types.ModuleType("hermes_constants")
        _hc_mod.get_hermes_home = lambda: Path(os.environ.get("HERMES_HOME", str(ROOT)))  # type: ignore[attr-defined]
        sys.modules["hermes_constants"] = _hc_mod

from chiyo_bundle import lived_context_bridge, phone_tool  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
import nyairo_day  # noqa: E402

FROZEN_ANCHORS = {
    "components/life/scripts/autonomous_life_assembly.py":
        "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
}

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX domain sockets (run inside WSL)",
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.name.endswith((".sqlite-shm", ".sqlite-wal")):
            continue
        rel = str(p.relative_to(root))
        try:
            st = p.lstat()
        except FileNotFoundError:
            continue
        if p.is_file() and not p.is_symlink():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), digest)
        else:
            # SQLite WAL read-only SELECTs create/unlink ephemeral .sqlite-shm mmap files in data/
            dir_mtime = 0 if rel == "data" else int(st.st_mtime_ns)
            snap[rel] = (0, dir_mtime, "dir")
    return snap


class V04FrozenAnchorsAndSchemaTest(unittest.TestCase):
    def test_00_frozen_anchors_byte_identical(self) -> None:
        for rel_path, expected_sha in FROZEN_ANCHORS.items():
            full = ROOT / rel_path
            actual = hashlib.sha256(full.read_bytes()).hexdigest()
            self.assertEqual(actual, expected_sha, f"Frozen anchor mutated: {rel_path}")

    def test_01_phone_tool_and_plugin_guidance_include_v04(self) -> None:
        expected_v04 = {
            "v04_neighborhood": "/api/phone/v04/neighborhood",
            "v04_builder_trylive": "/api/phone/v04/builder-trylive",
            "v04_elastic_play": "/api/phone/v04/elastic-play",
        }
        for name, route in expected_v04.items():
            self.assertEqual(phone_tool.READ_ACTIONS.get(name), route)
            self.assertEqual(phone_tool.LIFE_UPDATE_SURFACES.get(name), route)
            self.assertIn(name, phone_tool.ACTIONS)
        self.assertIn("v04_neighborhood", phone_tool.PRIVATE_ARTIFACT_ACTIONS)
        self.assertIn("v04_elastic_play", phone_tool.PRIVATE_ARTIFACT_ACTIONS)
        self.assertIn("v04_neighborhood", phone_tool.LIFE_UPDATE_PRIVATE_SURFACES)
        self.assertIn("v04_elastic_play", phone_tool.LIFE_UPDATE_PRIVATE_SURFACES)

        plugin_src = (ROOT / "plugins" / "chiyo" / "__init__.py").read_text(encoding="utf-8")
        for name in expected_v04:
            self.assertIn(name, plugin_src)


@POSIX_ONLY
class V04LoopbackAndHermesIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = fixture.SUBJECT
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v04-http-hermes-"))
        cls.server_root = cls.temp_dir / "instance"
        cls.stack = fixture.build_stack(cls.server_root, port=0)
        fixture.serve_stack(cls.stack)
        cls.base_url = cls.stack.base
        cls.cap_path, cls.cap_obj = phone_capability.issue(
            cls.stack.root,
            subject=fixture.SUBJECT,
            endpoint=cls.base_url,
        )
        cls.stack.server.set_capability(cls.cap_obj)
        cls.profile_home = cls.temp_dir / "hermes_profile"
        (cls.profile_home / "chiyo").mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        for rel in (
            "world/v04_neighborhood_state.json",
            "phone/v04_neighborhood_state.json",
            "phone/v04_builder_sandbox.json",
            "phone/v04_elastic_play.json",
        ):
            (self.stack.root / rel).unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 13, 9, 30, tzinfo=timezone(timedelta(hours=8))
        )
        cfg = {
            "cli_owner": True,
            "local_owner_uid": os.getuid(),
            "memory": False,
            "memory_tool_policy": "restricted",
            "phone": {
                "enabled": True,
                "base_url": self.base_url,
                "profile_home": str(self.stack.root),
                "write_enabled": True,
                "private_artifacts": True,
            },
        }
        (self.profile_home / "chiyo" / "config.json").write_text(
            json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
        )

    def _http(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        csrf: bool = False,
        capability: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=raw, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        if raw is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _tool(self, action: str, **kwargs: Any) -> dict[str, Any]:
        raw = phone_tool.phone_handler({"action": action, **kwargs}, hermes_home=str(self.profile_home))
        return json.loads(raw)

    def test_02_all_three_v04_get_routes_zero_write_and_guards(self) -> None:
        routes = (
            ("/api/phone/v04/neighborhood", "phone.v04.neighborhood.v1"),
            ("/api/phone/v04/builder-trylive", "phone.v04.builder_trylive.v1"),
            ("/api/phone/v04/elastic-play", "phone.v04.elastic_play.v1"),
        )
        before = _snapshot_tree(self.stack.root)
        for path, expected_schema in routes:
            code, res = self._http("GET", path)
            self.assertEqual(code, 200, f"{path}: {res}")
            self.assertTrue(res.get("ok"), res)
            data = res.get("data") or {}
            self.assertEqual(data.get("schema"), expected_schema)
            self.assertEqual(data.get("revision"), 0)

            c403, _ = self._http("GET", path, capability="invalid-cap-token")
            self.assertEqual(c403, 403)
            c400, _ = self._http("GET", path + "?unexpected=1")
            self.assertEqual(c400, 400)
        after = _snapshot_tree(self.stack.root)
        self.assertEqual(before, after, "GET on v04 routes must be 100% zero-write")

    def test_03_builder_trylive_sandbox_isolation_and_csrf_guards(self) -> None:
        c_no_csrf, _ = self._http(
            "POST",
            "/api/phone/v04/builder-trylive",
            {"action": "audit_routes_or_rehearse"},
        )
        self.assertEqual(c_no_csrf, 403)

        c_uid, _ = self._http(
            "POST",
            "/api/phone/v04/builder-trylive",
            {"action": "audit_routes_or_rehearse", "uid": "root"},
            csrf=True,
        )
        self.assertIn(c_uid, (400, 403, 409))

        c_run, res_run = self._http(
            "POST",
            "/api/phone/v04/builder-trylive",
            {
                "action": "run_trylive_hour",
                "preset_id": "yard_rain_rescue",
                "injections": ["rain", "restart_gap"],
                "operation_key": "trylive-op-1",
            },
            csrf=True,
        )
        self.assertEqual(c_run, 200, res_run)
        self.assertTrue(res_run.get("ok"), res_run)
        self.assertFalse(
            (self.stack.root / "world" / "v04_neighborhood_state.json").exists(),
            "Try-live sandbox must NEVER pollute live world/v04_neighborhood_state.json",
        )
        self.assertFalse(
            (self.stack.root / "phone" / "v04_elastic_play.json").exists(),
            "Try-live sandbox must NEVER pollute live phone/v04_elastic_play.json",
        )

    def test_04_hermes_phone_tool_lived_context_and_clock_tick(self) -> None:
        # 1. Read all three v04 surfaces via Hermes phone_tool
        for act in ("v04_neighborhood", "v04_builder_trylive", "v04_elastic_play"):
            r = self._tool(act)
            self.assertTrue(r.get("ok"), f"{act} failed: {r}")
            self.assertEqual(r.get("http_status"), 200)

        # 2. Partner cannot set_day_direction without capability -> 403
        c_partner_forbid, _ = self._http(
            "POST",
            "/api/phone/v04/elastic-play",
            {"action": "set_day_direction", "direction": "下午慢慢理书签"},
            csrf=True,
        )
        self.assertEqual(c_partner_forbid, 403)

        # 3. Model cannot impersonate partner on respond_prepared_surprise
        impersonate = self._tool(
            "phone_life_update",
            target_surface="v04_elastic_play",
            payload={
                "action": "respond_prepared_surprise",
                "surprise_id": "sp-1",
                "response": "accept",
            },
        )
        self.assertFalse(impersonate.get("ok"))
        self.assertEqual(impersonate.get("reason_code"), "PARTNER_IMPERSONATION_REFUSED")

        # 4. Subject sets elastic day direction & prepares a waiting surprise (anti-spoiler test)
        r_dir = self._tool(
            "phone_life_update",
            target_surface="v04_elastic_play",
            operation_key="v04-dir-1",
            payload={
                "action": "set_day_direction",
                "direction": "午后整理干燥花书签",
            },
        )
        self.assertTrue(r_dir.get("ok"), r_dir)

        secret_phrase = "绝密惊喜压花卡片内容不该剧透"
        r_surp = self._tool(
            "phone_life_update",
            target_surface="v04_elastic_play",
            operation_key="v04-surp-1",
            payload={
                "action": "prepare_or_offer_surprise",
                "stage": "prepare",
                "kind": "bookmark_note",
                "title": secret_phrase,
                "content": secret_phrase,
            },
        )
        self.assertTrue(r_surp.get("ok"), r_surp)

        # Partner GET without capability must NOT see secret_phrase while prepared_waiting
        c_pub_ep, pub_ep = self._http("GET", "/api/phone/v04/elastic-play")
        self.assertEqual(c_pub_ep, 200)
        self.assertNotIn(secret_phrase, json.dumps(pub_ep, ensure_ascii=False))

        # 5. Subject hangs a cloth in v04_neighborhood, then background clock ticks it with 0 LLM calls
        r_cloth = self._tool(
            "phone_life_update",
            target_surface="v04_neighborhood",
            operation_key="v04-cloth-1",
            payload={
                "action": "hang_or_collect_cloth",
                "op": "hang",
                "cloth_id": "linen_cloth",
                "label": "亚麻茶巾",
            },
        )
        self.assertTrue(r_cloth.get("ok"), r_cloth)

        # Lived context renders <= 1800 chars, zero-write on disk, and never leaks secret_phrase
        cfg = json.loads((self.profile_home / "chiyo" / "config.json").read_text(encoding="utf-8"))
        snap_before_ctx = _snapshot_tree(self.stack.root)
        ctx_text = lived_context_bridge.render_lived_companion_context(self.profile_home, cfg)
        snap_after_ctx = _snapshot_tree(self.stack.root)
        self.assertEqual(snap_before_ctx, snap_after_ctx, "lived_context_bridge must stay zero-write")
        self.assertTrue(len(ctx_text) <= lived_context_bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertIn("午后整理干燥花书签", ctx_text)
        self.assertNotIn(secret_phrase, ctx_text)

        # Zero-LLM background clock tick advances v04_neighborhood cleanly
        tick_res = nyairo_day.run_v03_background_clock_tick(
            self.stack.root,
            now_iso="2026-11-13T13:30:00+08:00",
            force_init=False,
        )
        self.assertTrue(tick_res.get("ok"), tick_res)
        self.assertEqual(tick_res.get("llm_calls"), 0)
        self.assertFalse(tick_res.get("model_called"))
        self.assertIn("v04_neighborhood", tick_res.get("slices_ticked") or [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""End-to-end wiring tests for Wave-5B3:
  1. ``chiyo_bundle/phone_tool.py`` (``v02_agency_depth``, ``v02_crossmodal``, ``v02_runtime_migration``)
  2. ``plugins/chiyo/__init__.py`` (``_PHONE_AND_WORLD_GUIDANCE <= 1200``)
  3. ``chiyo_bundle/lived_context_bridge.py`` (zero-write, ``<= 1800`` chars, zero regret/unshared-conflict leakage)
  4. ``components/life/scripts/v02_v03_daily_loop_bridge.py`` (zero-write satiation downweight,
     second-order rule window deferral, ``DORMANT_NO_COGNITION`` zero candidates, and
     ``LOW_BUDGET_CONSERVATION`` ``<= 2`` checkpoint/commitment priority)

Strictly zero paid LLM calls (``llm_calls == 0``).
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

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
from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import v02_agency_depth_view, v02_crossmodal_view, v02_runtime_migration_view  # noqa: E402

FROZEN_ANCHORS = {
    "components/life/scripts/autonomous_life_assembly.py":
        "5f51c9e82a417d9354d08608841d3472fc5a869e6bd3c9d9a7ebed0b15397f7a",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
}

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX domain sockets (run inside WSL)",
)


def _make_tiny_rgb_png(r: int, g: int, b: int, width: int = 8, height: int = 8) -> bytes:
    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes([r, g, b]) * width
    idat = zlib.compress(row * height)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


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
            dir_mtime = 0 if rel == "data" else int(st.st_mtime_ns)
            snap[rel] = (0, dir_mtime, "dir")
    return snap


def _load_chiyo_plugin_module() -> Any:
    plugin_path = ROOT / "plugins" / "chiyo" / "__init__.py"
    spec = importlib.util.spec_from_file_location("chiyo_plugin_v02_test", plugin_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class V02StaticSchemaAndGuidanceTests(unittest.TestCase):
    def test_01_frozen_anchors_byte_identical(self) -> None:
        for rel_path, expected_sha in FROZEN_ANCHORS.items():
            full = ROOT / rel_path
            actual = hashlib.sha256(full.read_bytes()).hexdigest()
            self.assertEqual(actual, expected_sha, f"Frozen anchor mutated: {rel_path}")

    def test_02_phone_tool_registers_all_three_v02_deep_surfaces(self) -> None:
        expected = {
            "v02_agency_depth": "/api/phone/v02/agency-depth",
            "v02_crossmodal": "/api/phone/v02/crossmodal",
            "v02_runtime_migration": "/api/phone/v02/runtime-migration",
        }
        for name, route in expected.items():
            self.assertEqual(phone_tool.READ_ACTIONS.get(name), route)
            self.assertEqual(phone_tool.LIFE_UPDATE_SURFACES.get(name), route)
            self.assertIn(name, phone_tool.ACTIONS)
            self.assertIn(name, phone_tool._SUBJECT_VIEW_READ_ACTIONS)
            self.assertIn(name, phone_tool._SURFACE_SUPPORTED_HINTS)
            self.assertIn(name, phone_tool.PHONE_TOOL_SCHEMA["description"])
            self.assertIn(
                name,
                phone_tool.PHONE_TOOL_SCHEMA["parameters"]["properties"]["target_surface"]["enum"],
            )
            self.assertIn(
                name,
                phone_tool.PHONE_TOOL_SCHEMA["parameters"]["properties"]["target_surface"]["description"],
            )

        self.assertIn("v02_agency_depth", phone_tool.PRIVATE_ARTIFACT_ACTIONS)
        self.assertIn("v02_crossmodal", phone_tool.PRIVATE_ARTIFACT_ACTIONS)
        self.assertNotIn("v02_runtime_migration", phone_tool.PRIVATE_ARTIFACT_ACTIONS)

        self.assertIn("v02_agency_depth", phone_tool.LIFE_UPDATE_PRIVATE_SURFACES)
        self.assertIn("v02_crossmodal", phone_tool.LIFE_UPDATE_PRIVATE_SURFACES)
        self.assertNotIn("v02_runtime_migration", phone_tool.LIFE_UPDATE_PRIVATE_SURFACES)

    def test_03_plugin_guidance_includes_v02_actions_and_respects_1200_char_cap(self) -> None:
        mod = _load_chiyo_plugin_module()
        guidance = mod._PHONE_AND_WORLD_GUIDANCE
        self.assertLessEqual(len(guidance), 1200, f"Guidance exceeded 1200 chars: {len(guidance)}")
        for act in ("v02_agency_depth", "v02_crossmodal", "v02_runtime_migration"):
            self.assertIn(act, guidance)
            self.assertIn(act, phone_tool.ACTIONS)
        self.assertEqual(lived_context_bridge.MAX_LIVED_CONTEXT_CHARS, 1800)

    def test_04_anti_impersonation_and_identity_spoofing_preflight_guards(self) -> None:
        # Partner-only withdraw_partner_option blocked for model
        with self.assertRaises(phone_tool.PhoneToolRefused) as ctx_imp:
            phone_tool._prepare_life_update(
                {
                    "target_surface": "v02_agency_depth",
                    "payload": {"action": "withdraw_partner_option", "option_id": "opt_0001"},
                },
                "op-imp-1",
            )
        self.assertEqual(ctx_imp.exception.reason_code, "PARTNER_IMPERSONATION_REFUSED")

        # Subject-allowed actions pass preflight and inject operation_key + require capability
        for surf, act, extra in (
            (
                "v02_agency_depth",
                "record_choice_episode",
                {"domain": "reading", "chosen": "散文集", "options": ["散文集", "诗集"], "choice_mode": "spontaneous"},
            ),
            (
                "v02_agency_depth",
                "offer_low_cost_option",
                {"domain": "tea", "option_title": "焙茶"},
            ),
            (
                "v02_crossmodal",
                "try_color_swatches",
                {"swatches": ["#336699", "#EEDDCC"]},
            ),
            (
                "v02_runtime_migration",
                "set_budget_mode",
                {"mode": "LOW_BUDGET", "budget_scale_pct": 15},
            ),
        ):
            s_out, path_out, body_out, cap_req = phone_tool._prepare_life_update(
                {"target_surface": surf, "payload": {"action": act, **extra}},
                f"op-ok-{surf}-{act}",
            )
            self.assertEqual(s_out, surf)
            self.assertEqual(path_out, phone_tool.LIFE_UPDATE_SURFACES[surf])
            self.assertEqual(body_out["operation_key"], f"op-ok-{surf}-{act}")
            self.assertTrue(cap_req)

        # Forged uid / actor_side="partner" rejected
        with self.assertRaises(phone_tool.PhoneToolRefused) as ctx_uid:
            phone_tool._prepare_life_update(
                {
                    "target_surface": "v02_runtime_migration",
                    "payload": {"action": "set_budget_mode", "mode": "NORMAL", "uid": 0},
                },
                "op-uid-1",
            )
        self.assertEqual(ctx_uid.exception.reason_code, "CLIENT_IDENTITY_REFUSED")

    def test_05_gentle_alias_normalization_on_v02_agency_depth(self) -> None:
        _, _, b_conf, _ = phone_tool._prepare_life_update(
            {
                "target_surface": "v02_agency_depth",
                "payload": {
                    "action": "park_or_resolve_conflict",
                    "operation": "park",
                    "title": "想早睡又想改完画稿",
                    "side_a": "早点休息",
                    "side_b": "趁手感改画稿",
                },
            },
            "op-norm-1",
        )
        self.assertEqual(b_conf["conflict_op"], "park")
        self.assertNotIn("operation", b_conf)

        _, _, b_inc, _ = phone_tool._prepare_life_update(
            {
                "target_surface": "v02_agency_depth",
                "payload": {
                    "action": "manage_open_incubation",
                    "incubation_op": "open",
                    "topic": "旧书签背面褪色印记的来历",
                },
            },
            "op-norm-2",
        )
        self.assertEqual(b_inc["inc_op"], "open")
        self.assertNotIn("incubation_op", b_inc)


@POSIX_ONLY
class V02LoopbackHermesAndContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = fixture.SUBJECT
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v02-hermes-bridge-"))
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
            "state/v02_agency_depth_state.json",
            "phone/v02_crossmodal.json",
            "phone/v02_runtime_migration.json",
        ):
            (self.stack.root / rel).unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 13, 21, 30, tzinfo=timezone(timedelta(hours=8))
        )
        self._write_profile_cfg(private_artifacts=True, restricted_memory=False)

    def _write_profile_cfg(
        self,
        *,
        private_artifacts: bool = True,
        restricted_memory: bool = False,
        include_cap: bool = True,
    ) -> dict[str, Any]:
        phone_sec: dict[str, Any] = {
            "enabled": True,
            "base_url": self.base_url,
            "profile_home": str(self.stack.root),
            "write_enabled": True,
            "private_artifacts": private_artifacts,
        }
        if include_cap:
            phone_sec["capability_file"] = str(self.cap_path)
        else:
            phone_sec["capability_file"] = str(self.temp_dir / "nonexistent-cap.json")
        cfg = {
            "cli_owner": True,
            "local_owner_uid": os.getuid(),
            "memory": restricted_memory,
            "memory_tool_policy": "restricted" if restricted_memory else "unrestricted",
            "phone": phone_sec,
        }
        (self.profile_home / "chiyo" / "config.json").write_text(
            json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
        )
        return cfg

    def _tool(self, action: str, **kwargs: Any) -> dict[str, Any]:
        raw = phone_tool.phone_handler(
            {"action": action, **kwargs},
            hermes_home=str(self.profile_home),
        )
        return json.loads(raw)

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
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_06_hermes_read_all_three_v02_surfaces_zero_write(self) -> None:
        # Warm up stack world-home bootstrap once before taking zero-write snapshot
        warm = self._tool("world")
        self.assertTrue(warm.get("ok"), warm)
        before = _snapshot_tree(self.stack.root)
        for act, expected_schema in (
            ("v02_agency_depth", "nyairo.v02.agency_depth.v1"),
            ("v02_crossmodal", "nyairo.v02.crossmodal.v1"),
            ("v02_runtime_migration", "nyairo.v02.runtime_migration.v1"),
        ):
            res = self._tool(act)
            self.assertTrue(res.get("ok"), f"{act} failed: {res}")
            self.assertEqual(res.get("http_status"), 200)
            data = (res.get("result") or {}).get("data") or {}
            self.assertEqual(data.get("schema"), expected_schema)
            self.assertEqual(data.get("llm_calls", 0), 0)
        after = _snapshot_tree(self.stack.root)
        self.assertEqual(before, after, "Read actions on v02 deep surfaces must be 100% zero-write")

    def test_07_restricted_memory_private_artifacts_gate(self) -> None:
        self._write_profile_cfg(private_artifacts=False, restricted_memory=True)
        for priv_act in ("v02_agency_depth", "v02_crossmodal"):
            r_read = self._tool(priv_act)
            self.assertFalse(r_read.get("ok"))
            self.assertEqual(r_read.get("reason_code"), "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")

            r_write = self._tool(
                "phone_life_update",
                target_surface=priv_act,
                payload={"action": "try_color_swatches", "swatches": ["#112233"]},
            )
            self.assertFalse(r_write.get("ok"))
            self.assertEqual(r_write.get("reason_code"), "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")

        # Public runtime-migration surface remains readable under restricted memory
        r_pub = self._tool("v02_runtime_migration")
        self.assertTrue(r_pub.get("ok"), r_pub)

    def test_08_hermes_agency_depth_mutations_idempotency_and_self_healing_hints(self) -> None:
        # Bad subaction returns 400 + supported_actions_hint
        bad = self._tool(
            "phone_life_update",
            target_surface="v02_agency_depth",
            payload={"action": "nonexistent_v02_op"},
        )
        self.assertFalse(bad.get("ok"))
        self.assertEqual(bad.get("http_status"), 400)
        self.assertIn("record_choice_episode", bad["supported_actions_hint"]["subject_actions"])

        # Spontaneous choice + satiation + unshared conflict + regret + 2nd-order rule
        r1 = self._tool(
            "phone_life_update",
            target_surface="v02_agency_depth",
            operation_key="ad-ep-1",
            payload={
                "action": "record_choice_episode",
                "domain": "reading",
                "chosen": "海边植物图鉴",
                "options": ["海边植物图鉴", "旧街侦探小说"],
                "choice_mode": "spontaneous",
                "shared": True,
            },
        )
        self.assertTrue(r1.get("ok"), r1)
        self.assertEqual(r1.get("status"), "applied")

        # Idempotent replay with same operation_key
        r1_dup = self._tool(
            "phone_life_update",
            target_surface="v02_agency_depth",
            operation_key="ad-ep-1",
            payload={
                "action": "record_choice_episode",
                "domain": "reading",
                "chosen": "海边植物图鉴",
                "options": ["海边植物图鉴", "旧街侦探小说"],
                "choice_mode": "spontaneous",
                "shared": True,
            },
        )
        self.assertTrue(r1_dup.get("ok"), r1_dup)
        self.assertEqual(r1_dup.get("status"), "already_applied")

        r_sat = self._tool(
            "phone_life_update",
            target_surface="v02_agency_depth",
            operation_key="ad-sat-1",
            payload={
                "action": "record_exposure_satiation",
                "domain": "reading",
                "title": "海边植物图鉴",
                "intensity": 0.92,
                "shared": True,
            },
        )
        self.assertTrue(r_sat.get("ok"), r_sat)

    def test_09_hermes_crossmodal_palette_swatches_and_marks(self) -> None:
        png_b64 = base64.b64encode(_make_tiny_rgb_png(52, 108, 176)).decode("ascii")
        r_pal = self._tool(
            "phone_life_update",
            target_surface="v02_crossmodal",
            operation_key="cm-pal-1",
            payload={
                "action": "analyze_image_palette",
                "title": "私藏海雾小卡",
                "image_base64": png_b64,
                "share_with_room": False,
            },
        )
        self.assertTrue(r_pal.get("ok"), r_pal)

        r_pad = self._tool(
            "phone_life_update",
            target_surface="v02_crossmodal",
            operation_key="cm-pad-1",
            payload={
                "action": "try_color_swatches",
                "swatches": ["#346CB0", "#F2E8D5"],
                "note_cn": "试试信纸边角配色",
            },
        )
        self.assertTrue(r_pad.get("ok"), r_pad)

        r_mk = self._tool(
            "phone_life_update",
            target_surface="v02_crossmodal",
            operation_key="cm-mk-1",
            payload={
                "action": "mark_reading_uncertain",
                "book_title": "潮汐光学讲义",
                "page": 42,
                "passage_hint": "折射率随盐度梯度变化推导",
                "share_with_room": False,
            },
        )
        self.assertTrue(r_mk.get("ok"), r_mk)

        # Partner HTTP GET without capability must NOT see unshared palette or reading mark
        c_pub, pub_doc = self._http("GET", "/api/phone/v02/crossmodal")
        self.assertEqual(c_pub, 200)
        dumped = json.dumps(pub_doc, ensure_ascii=False)
        self.assertNotIn("私藏海雾小卡", dumped)
        self.assertNotIn("潮汐光学讲义", dumped)
        self.assertIn("#346CB0", dumped)

    def test_10_hermes_runtime_migration_mutations(self) -> None:
        r_carrier = self._tool(
            "phone_life_update",
            target_surface="v02_runtime_migration",
            operation_key="rm-car-1",
            payload={
                "action": "record_carrier_check",
                "model_id": "carrier-next-v2",
                "carrier_name": "desktop_local",
                "preview_available": False,
            },
        )
        self.assertTrue(r_carrier.get("ok"), r_carrier)

        r_mode = self._tool(
            "phone_life_update",
            target_surface="v02_runtime_migration",
            operation_key="rm-mode-1",
            payload={
                "action": "set_budget_mode",
                "mode": "LOW_BUDGET",
                "budget_scale_pct": 10,
                "reason_cn": "夜间省算力档",
            },
        )
        self.assertTrue(r_mode.get("ok"), r_mode)

    def test_11_lived_context_http_zero_write_cues_and_strict_no_regret_or_conflict_leak(self) -> None:
        secret_regret = "SECRET_REGRET_NEVER_SURFACE_901"
        secret_conflict = "SECRET_UNSHARED_CONFLICT_902"

        self._tool(
            "phone_life_update",
            target_surface="v02_agency_depth",
            operation_key="ctx-sat-1",
            payload={
                "action": "record_exposure_satiation",
                "domain": "手作纸艺",
                "title": "折纸",
                "intensity": 0.95,
                "shared": True,
            },
        )
        self._tool(
            "phone_life_update",
            target_surface="v02_agency_depth",
            operation_key="ctx-rule-1",
            payload={
                "action": "manage_second_order_rule",
                "rule_op": "endorse",
                "title": "十点后不新开大稿",
                "trigger_window": "late_night",
                "shared": True,
            },
        )
        self._tool(
            "phone_life_update",
            target_surface="v02_agency_depth",
            operation_key="ctx-conf-1",
            payload={
                "action": "park_or_resolve_conflict",
                "conflict_op": "park",
                "title": secret_conflict,
                "side_a": secret_conflict + "_A",
                "side_b": secret_conflict + "_B",
                "shared": False,
            },
        )
        self._tool(
            "phone_life_update",
            target_surface="v02_agency_depth",
            operation_key="ctx-reg-1",
            payload={
                "action": "record_regret_reflection",
                "regret_kind": "process_mistake",
                "summary": secret_regret,
                "method_adjustment_cn": secret_regret + "_ADJ",
                "shared": False,
            },
        )
        self._tool(
            "phone_life_update",
            target_surface="v02_crossmodal",
            operation_key="ctx-pad-1",
            payload={
                "action": "try_color_swatches",
                "swatches": ["#4A7C59", "#EDE6D6"],
            },
        )
        self._tool(
            "phone_life_update",
            target_surface="v02_runtime_migration",
            operation_key="ctx-rm-1",
            payload={
                "action": "set_budget_mode",
                "mode": "LOW_BUDGET",
                "budget_scale_pct": 12,
            },
        )

        cfg = self._write_profile_cfg(private_artifacts=True, restricted_memory=False, include_cap=True)
        snap_before = _snapshot_tree(self.stack.root)
        rendered = lived_context_bridge.render_lived_companion_context(self.profile_home, cfg)
        snap_after = _snapshot_tree(self.stack.root)
        self.assertEqual(snap_before, snap_after, "lived_context_bridge must stay 100% zero-write")
        self.assertLessEqual(len(rendered), lived_context_bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertIn("手作纸艺", rendered)
        self.assertIn("十点后不新开大稿", rendered)
        self.assertIn("#4A7C59", rendered)
        self.assertIn("省电保守", rendered)
        self.assertNotIn(secret_regret, rendered)
        self.assertNotIn(secret_conflict, rendered)

    def test_12_lived_context_partner_perspective_withholds_unshared_v02_items(self) -> None:
        priv_rule = "PRIVATE_SELF_RULE_881"
        priv_book = "PRIVATE_UNCERTAIN_BOOK_882"
        self._tool(
            "phone_life_update",
            target_surface="v02_agency_depth",
            operation_key="p-rule-1",
            payload={
                "action": "manage_second_order_rule",
                "rule_op": "endorse",
                "title": priv_rule,
                "trigger_window": "late_night",
                "shared": False,
            },
        )
        self._tool(
            "phone_life_update",
            target_surface="v02_crossmodal",
            operation_key="p-book-1",
            payload={
                "action": "mark_reading_uncertain",
                "book_title": priv_book,
                "page": 9,
                "passage_hint": "私密批注段落",
                "share_with_room": False,
            },
        )
        self._tool(
            "phone_life_update",
            target_surface="v02_runtime_migration",
            operation_key="p-dorm-1",
            payload={"action": "set_budget_mode", "mode": "DORMANT"},
        )

        # Without subject capability, HTTP falls back to partner perspective
        cfg_no_cap = self._write_profile_cfg(private_artifacts=True, restricted_memory=False, include_cap=False)
        rendered_partner = lived_context_bridge.render_lived_companion_context(self.profile_home, cfg_no_cap)
        self.assertLessEqual(len(rendered_partner), lived_context_bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertNotIn(priv_rule, rendered_partner)
        self.assertNotIn(priv_book, rendered_partner)
        self.assertIn("休眠保存", rendered_partner)


class V02OfflineDiskAndLoopBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v02-loop-bridge-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_rich_life_candidates(self, day_root: Path, *, now_iso: str) -> None:
        from components.phone import (
            home_hearth_view,
            outing_places_view,
            workbench_curio_view,
        )

        phone_dir = day_root / "phone"
        phone_dir.mkdir(parents=True, exist_ok=True)
        # 1) Bedside tomorrow note (due commitment)
        (phone_dir / "desk_tomorrow_note.json").write_text(
            json.dumps(
                {
                    "note_id": "tmr_1",
                    "status": "OPEN",
                    "title": "给玄关水培薄荷换水",
                    "due_at": now_iso,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # 2) Unfinished workbench tray (in-progress checkpoint, creative_intensive=True)
        t_state = workbench_curio_view.empty_tray_state()
        t_state, _ = workbench_curio_view.set_tray(
            t_state,
            {
                "images": [],
                "materials": [],
                "work": {
                    "artifact_id": "artifact:sea_sketch_01",
                    "baseline_version_id": "artifact-version:v1",
                },
                "note": "未完海堤水彩草稿",
            },
            now_iso,
        )
        workbench_curio_view.write_tray_state(day_root, t_state)

        # 3) Bookstore window bookmark (reading domain)
        bb_state = outing_places_view.empty_bench_bookstore_state()
        bb_state, _, _ = outing_places_view.apply_bookmark_window_book(
            bb_state,
            slot_index=0,
            local_date="2026-11-13",
            shared=True,
            note="橱窗第一本",
            note_supplied=True,
            now_iso=now_iso,
        )
        outing_places_view.write_bench_bookstore_state(day_root, bb_state)

        # 4) Saved walk route (strenuous outing roaming)
        (phone_dir / "outing_routes_market.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "revision": 1,
                    "updated_at": now_iso,
                    "routes": [
                        {
                            "route_id": "rt_1",
                            "title": "远郊灯塔长途步道",
                            "place_ids": ["lighthouse_trail"],
                            "album_artifact_id": None,
                            "caption": None,
                            "shared": True,
                            "created_at": now_iso,
                            "updated_at": now_iso,
                        }
                    ],
                    "market_wishes": [],
                    "partner_market_tip": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # 5) Tentative new plan (TENTATIVE)
        p_state = home_hearth_view.empty_plans_state()
        p_state, _, _, _ = home_hearth_view.propose_plan(
            p_state,
            plan_id=None,
            title="新开大型木工书架计划",
            window_note="周末",
            proposer="partner",
            now_iso=now_iso,
        )
        home_hearth_view.write_plans_state(day_root, p_state)

    def test_13_lived_context_offline_disk_fallback_deleted_album_barrier_and_char_cap(self) -> None:
        day_root = self.tmp / "offline_day"
        album_dir = day_root / "phone" / "album"
        album_dir.mkdir(parents=True, exist_ok=True)
        img_file = album_dir / "sunset_card.png"
        img_file.write_bytes(_make_tiny_rgb_png(210, 110, 70))

        v02_crossmodal_view.mutate_v02_crossmodal_view(
            day_root,
            {
                "action": "analyze_image_palette",
                "title": "晚霞相册原图",
                "album_id": "sunset_card",
                "share_with_room": True,
            },
            actor_side="subject",
        )
        # Delete underlying media file -> offline lived_context_bridge must withhold stale palette!
        img_file.unlink()

        v02_agency_depth_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "record_exposure_satiation",
                "domain": "水彩临摹",
                "title": "水彩",
                "intensity": 0.9,
                "shared": True,
            },
            actor_side="subject",
        )
        long_hint = "提示段落" * 350
        cfg = {
            "day_root": str(day_root),
            "memory": False,
            "phone": {"enabled": True, "private_artifacts": True},
        }
        before = _snapshot_tree(day_root)
        rendered = lived_context_bridge.render_lived_companion_context(
            self.tmp, cfg, ephemeral_hint=long_hint
        )
        after = _snapshot_tree(day_root)
        self.assertEqual(before, after, "Offline disk fallback must stay 100% zero-write")
        self.assertLessEqual(len(rendered), lived_context_bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertNotIn("晚霞相册原图", rendered, "Deleted album source palette must not resurrect")

    def test_14_daily_loop_bridge_empty_root_100_percent_zero_write(self) -> None:
        empty_root = self.tmp / "brand_new_empty_root"
        empty_root.mkdir(parents=True, exist_ok=True)
        before = _snapshot_tree(empty_root)
        out = loop_bridge.collect_v02_v03_loop_candidates(
            empty_root, now_iso="2026-11-13T14:00:00+08:00"
        )
        after = _snapshot_tree(empty_root)
        self.assertEqual(before, after)
        self.assertEqual(out["llm_calls"], 0)
        self.assertFalse(out["dormant_suppressed"])

    def test_15_daily_loop_satiation_multiplier_downweights_candidate(self) -> None:
        from components.phone import long_companionship_view, outing_places_view

        day_root = self.tmp / "satiation_day"
        now_iso = "2026-11-13T15:00:00+08:00"
        day_root.mkdir(parents=True, exist_ok=True)
        # Seed two candidates: reading bookmark + plant sketch journal
        bb_state = outing_places_view.empty_bench_bookstore_state()
        bb_state, bm_entry, _ = outing_places_view.apply_bookmark_window_book(
            bb_state,
            slot_index=0,
            local_date="2026-11-13",
            shared=True,
            note="橱窗诗集",
            note_supplied=True,
            now_iso=now_iso,
        )
        outing_places_view.write_bench_bookstore_state(day_root, bb_state)

        rh_state = long_companionship_view.empty_room_habits_state()
        rh_state, _, _ = long_companionship_view.apply_link_plant_sketch(
            rh_state,
            plant_id="fern_01",
            plant_name="铁线蕨",
            target_kind="project",
            target_artifact_id="artifact:fern_sketch_01",
            observation_note="铁线蕨新叶观察",
            shared=False,
            now_iso=now_iso,
        )
        long_companionship_view.write_room_habits_state(day_root, rh_state)

        # Saturate 'reading' domain -> multiplier 0.25 (< 1.0)
        v02_agency_depth_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "record_exposure_satiation",
                "domain": "reading",
                "title": str(bm_entry.get("title") or "阅读"),
                "intensity": 0.95,
                "shared": True,
            },
            actor_side="subject",
            now_iso=now_iso,
        )

        before = _snapshot_tree(day_root)
        collected = loop_bridge.collect_v02_v03_loop_candidates(day_root, now_iso=now_iso)
        after = _snapshot_tree(day_root)
        self.assertEqual(before, after, "collect_v02_v03_loop_candidates must stay 100% zero-write")
        self.assertGreaterEqual(len(collected["satiation_adjusted"]), 1)

        sat_entry = collected["satiation_adjusted"][0]
        self.assertLess(sat_entry["multiplier"], 1.0)
        self.assertLess(sat_entry["adjusted_weight"], 1.0)

        # Deterministic selector naturally picks fresh plant journal over saturated reading bookmark
        decision = loop_bridge.select_deterministic_loop_action(collected)
        self.assertEqual(decision["action_family"], "observe_plant_journal")
        self.assertFalse(decision.get("satiation_adjusted"))

    def test_16_daily_loop_second_order_rule_late_night_defers_heavy_new_work(self) -> None:
        from components.phone import long_companionship_view, workbench_curio_view

        day_root = self.tmp / "rule_window_day"
        late_iso = "2026-11-13T23:15:00+08:00"
        afternoon_iso = "2026-11-13T15:15:00+08:00"
        day_root.mkdir(parents=True, exist_ok=True)

        # Heavy creative workbench draft + gentle plant observation
        t_state = workbench_curio_view.empty_tray_state()
        t_state, _ = workbench_curio_view.set_tray(
            t_state,
            {
                "images": [],
                "materials": [],
                "work": {
                    "artifact_id": "artifact:heavy_novel",
                    "baseline_version_id": "artifact-version:v2",
                },
                "note": "长篇重头章节大改",
            },
            "2026-11-13T18:00:00+08:00",
        )
        workbench_curio_view.write_tray_state(day_root, t_state)

        rh_state = long_companionship_view.empty_room_habits_state()
        rh_state, _, _ = long_companionship_view.apply_link_plant_sketch(
            rh_state,
            plant_id="mint_01",
            plant_name="薄荷盆栽",
            target_kind="project",
            target_artifact_id="artifact:mint_note_01",
            observation_note="睡前看一眼薄荷叶",
            shared=False,
            now_iso="2026-11-13T18:05:00+08:00",
        )
        long_companionship_view.write_room_habits_state(day_root, rh_state)
        v02_agency_depth_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "manage_second_order_rule",
                "rule_op": "endorse",
                "title": "深夜十点后不新开重认知创作",
                "trigger_window": "late_night",
                "shared": False,
            },
            actor_side="subject",
            now_iso="2026-11-13T20:00:00+08:00",
        )

        # At 23:15 (inside late_night window): workbench heavy draft is deferred & downweighted
        late_collected = loop_bridge.collect_v02_v03_loop_candidates(day_root, now_iso=late_iso)
        self.assertGreaterEqual(len(late_collected["second_order_rule_adjusted"]), 1)
        late_dec = loop_bridge.select_deterministic_loop_action(late_collected)
        self.assertEqual(late_dec["action_family"], "observe_plant_journal")

        # At 15:15 (outside late_night window): workbench draft is NOT deferred and wins normally
        aft_collected = loop_bridge.collect_v02_v03_loop_candidates(day_root, now_iso=afternoon_iso)
        self.assertEqual(aft_collected["second_order_rule_adjusted"], [])
        aft_dec = loop_bridge.select_deterministic_loop_action(aft_collected)
        self.assertEqual(aft_dec["action_family"], "advance_workbench_draft")

    def test_17_daily_loop_dormant_no_cognition_trims_to_zero_candidates(self) -> None:
        day_root = self.tmp / "dormant_day"
        now_iso = "2026-11-13T16:00:00+08:00"
        self._seed_rich_life_candidates(day_root, now_iso=now_iso)
        v02_runtime_migration_view.mutate_v02_runtime_migration_view(
            day_root,
            {"action": "set_budget_mode", "mode": "DORMANT", "reason_cn": "整夜休眠停机"},
            actor_side="subject",
            now_iso=now_iso,
        )

        before = _snapshot_tree(day_root)
        collected = loop_bridge.collect_v02_v03_loop_candidates(day_root, now_iso=now_iso)
        after = _snapshot_tree(day_root)
        self.assertEqual(before, after, "Dormant candidate collection must stay 100% zero-write")
        self.assertTrue(collected["dormant_suppressed"])
        self.assertTrue(collected["gating"]["dormant_suppressed"])
        self.assertEqual(len(collected["candidates"]), 0)
        self.assertEqual(collected["resident_context_summary"]["candidate_count"], 0)

        dec = loop_bridge.select_deterministic_loop_action(collected)
        self.assertEqual(dec["decision_kind"], "DORMANT_NO_COGNITION")
        self.assertIsNone(dec["selected_candidate_id"])
        self.assertTrue(dec["dormant_suppressed"])

    def test_18_daily_loop_low_budget_conservation_pauses_roaming_and_caps_at_two(self) -> None:
        day_root = self.tmp / "low_budget_day"
        now_iso = "2026-11-13T16:30:00+08:00"
        self._seed_rich_life_candidates(day_root, now_iso=now_iso)
        # Also add courtyard rain rescue so >2 checkpoint/urgent items exist to test <=2 cap
        world_dir = day_root / "world"
        world_dir.mkdir(parents=True, exist_ok=True)
        (world_dir / "v04_neighborhood_state.json").write_text(
            json.dumps(
                {
                    "weather_and_ground": {"weather": "rain"},
                    "courtyard": {"drying_rack": {"items": [{"cloth_id": "c1", "label": "茶巾", "state": "hanging"}]}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        v02_runtime_migration_view.mutate_v02_runtime_migration_view(
            day_root,
            {"action": "set_budget_mode", "mode": "LOW_BUDGET", "budget_scale_pct": 10},
            actor_side="subject",
            now_iso=now_iso,
        )

        before = _snapshot_tree(day_root)
        collected = loop_bridge.collect_v02_v03_loop_candidates(day_root, now_iso=now_iso)
        after = _snapshot_tree(day_root)
        self.assertEqual(before, after, "Low-budget candidate collection must stay 100% zero-write")
        self.assertTrue(collected["low_budget_conservation"])
        self.assertLessEqual(len(collected["candidates"]), 2)
        self.assertLessEqual(collected["gating"]["candidate_cap"], 2)

        surviving_families = {
            collected["descriptors"][c["candidate_id"]]["action_family"]
            for c in collected["candidates"]
        }
        # Roaming walk, bookstore browse, and new PROPOSED project must be paused
        self.assertNotIn("strenuous_outing_walk", surviving_families)
        self.assertNotIn("follow_bookstore_bookmark", surviving_families)
        self.assertNotIn("adopt_tentative_plan", surviving_families)
        # Committed tomorrow note and in-progress workbench checkpoint prioritized within <= 2 cap
        self.assertEqual(surviving_families, {"follow_tomorrow_note", "advance_workbench_draft"})

    def test_19_proposed_unendorsed_partner_rule_does_not_auto_lock_candidates(self) -> None:
        day_root = self.tmp / "proposed_only_day"
        late_iso = "2026-11-13T23:20:00+08:00"
        self._seed_rich_life_candidates(day_root, now_iso=late_iso)
        # Partner proposes a gentle rule, NOT yet endorsed/trialed by subject
        v02_agency_depth_view.mutate_v02_agency_depth_view(
            day_root,
            {
                "action": "propose_shared_gentle_rule",
                "title": "晚上早点收工",
                "trigger_window": "late_night",
                "reason_cn": "温和建议不自动锁死",
            },
            actor_side="partner",
            now_iso="2026-11-13T20:00:00+08:00",
        )
        collected = loop_bridge.collect_v02_v03_loop_candidates(day_root, now_iso=late_iso)
        self.assertEqual(collected["second_order_rule_adjusted"], [])

    def test_20_import_error_fallback_safety_in_daily_loop_bridge(self) -> None:
        day_root = self.tmp / "import_fallback_day"
        now_iso = "2026-11-13T12:00:00+08:00"
        self._seed_rich_life_candidates(day_root, now_iso=now_iso)
        with mock.patch.dict(
            sys.modules,
            {
                "components.phone.v02_agency_depth_view": None,
                "v02_agency_depth_engine": None,
                "components.phone.v02_runtime_migration_view": None,
                "v02_runtime_governance_migration": None,
            },
        ):
            collected = loop_bridge.collect_v02_v03_loop_candidates(day_root, now_iso=now_iso)
            self.assertGreaterEqual(len(collected["candidates"]), 1)
            self.assertFalse(collected["dormant_suppressed"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Wave 2 End-to-End Closure Tests:
  1. Real loopback ``PhoneHTTPServer`` ``GET`` / ``POST /api/phone/v02/honest-ux``;
  2. Partner 403 boundary gates (5 subject-only actions) & 4 partner actions + idempotency;
  3. Zero-write GET projections, partner privacy stripping, zero secret echo in forget holds;
  4. Daily Loop bridge (``v02_v03_daily_loop_bridge.py``) integration of Forget barrier,
     Representation ``DIVERGED``, ``UNVALIDATED_AFTER_MIGRATION``, and salvaged scrap matching;
  5. ``lived_context_bridge.py`` & real ``ChiyoContextEngine`` barrier suppression, calm == 0
     chars gate, ``MAX_LIVED_CONTEXT_CHARS == 1800``, ``LIFE_UPDATE_SURFACES == 37``,
     ``len(_PHONE_AND_WORLD_GUIDANCE) == 1199``, ``llm_calls == 0``, pure LF.
"""
from __future__ import annotations

import hashlib
import importlib.util
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

from chiyo_bundle import hermes_plugin, lived_context_bridge, phone_tool  # noqa: E402
from components.life.scripts import (  # noqa: E402
    v02_agency_depth_engine as ad_engine,
    v02_autobiography_skill_provenance as asp,
    v02_crash_and_partial_reconciler as crash_rec,
    v02_lived_slices_and_vd_protocols as lived_slices,
    v02_representation_lease_forget as rlf,
    v02_v03_daily_loop_bridge as loop_bridge,
)
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import v02_honest_ux_view  # noqa: E402
from components.phone import workbench_curio_view  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "v02-honest-ux-cap-token-20261118"
T0 = "2026-11-18T10:00:00+08:00"
T1 = "2026-11-18T11:30:00+08:00"

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
        rel = p.relative_to(root).as_posix()
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
    spec = importlib.util.spec_from_file_location("chiyo_plugin_e2e_check", plugin_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestV02Wave2StaticInvariants(unittest.TestCase):
    def test_01_static_invariants_pure_lf_no_emoji_neutral_ta_and_counts(self) -> None:
        owned_files = (
            ROOT / "components" / "phone" / "v02_honest_ux_view.py",
            ROOT / "components" / "phone" / "service.py",
            ROOT / "components" / "phone" / "server.py",
            ROOT / "components" / "life" / "scripts" / "v02_v03_daily_loop_bridge.py",
            ROOT / "chiyo_bundle" / "lived_context_bridge.py",
            ROOT / "tests" / "test_v02_wave2_end_to_end_closure.py",
        )
        for fp in owned_files:
            raw = fp.read_bytes()
            self.assertNotIn(b"\r\n", raw, f"CRLF found in {fp}")

        hux_src = (ROOT / "components" / "phone" / "v02_honest_ux_view.py").read_text(encoding="utf-8")
        self.assertNotIn("千代", hux_src)
        self.assertEqual(loop_bridge.LIFE_UPDATE_SURFACES, 37)
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(lived_context_bridge.MAX_LIVED_CONTEXT_CHARS, 1800)
        plugin_mod = _load_chiyo_plugin_module()
        self.assertEqual(len(plugin_mod._PHONE_AND_WORLD_GUIDANCE), 1199)
        self.assertFalse((ROOT / "tmp_test").exists(), "tmp_test must not exist in repo root")


@POSIX_ONLY
class TestV02Wave2EndToEndClosure(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v02-wave2-e2e-"))
        cls.server_root = cls.temp_dir / "instance"
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-18T08:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.server_root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base_url = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        for rel in (
            "phone/v02_honest_ux_state.json",
            "phone/v02_runtime_migration.json",
            "phone/v02_crossmodal.json",
            "phone/outing_bench_bookstore.json",
            "phone/home_hearth_tentative_plans.json",
            "phone/tentative_plans.json",
            "phone/desk_tomorrow_note.json",
            "governance/v02_representation_lease_forget_state.json",
            "control_authority/v02_control_highwater_ledger.json",
            "state/v02_autobiography_skill_provenance.json",
            "state/v02_lived_slices_state.json",
            "state/v02_agency_depth_state.json",
            "canonical_life.sqlite3",
            "canonical_life.sqlite3-wal",
            "canonical_life.sqlite3-shm",
        ):
            (self.stack.root / rel).unlink(missing_ok=True)
        workbench_curio_view.tray_path(self.stack.root).unlink(missing_ok=True)
        for extra_dir in ("workspace_e2e", "reconciler"):
            dpath = self.stack.root / extra_dir
            if dpath.exists():
                shutil.rmtree(dpath, ignore_errors=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 18, 10, 30, tzinfo=timezone(timedelta(hours=8))
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

    def _seed_diverged_md(self, art_id: str = "art_md_1", visibility: str = "shared") -> Path:
        ws = self.stack.root / "workspace_e2e"
        ws.mkdir(parents=True, exist_ok=True)
        rlf._ensure_control_ledger_locked(self.stack.root)
        rlf.create_managed_artifact(
            self.stack.root,
            artifact_id=art_id,
            controller_principal_ref="owner_1",
            subject_id=SUBJECT,
            initial_body="# Seawall Notes\nLine 1\n",
            operation_id=f"op_create_{art_id}",
            title="seawall_notes.md",
            visibility=visibility,
            now_iso=T0,
        )
        rlf.bind_representation(
            self.stack.root,
            artifact_id=art_id,
            workspace_root=ws,
            workspace_rel_path=f"{art_id}.md",
            operation_id=f"op_bind_{art_id}",
            now_iso=T0,
        )
        md_file = ws / f"{art_id}.md"
        md_file.write_text("# Seawall Notes\nExternal edit line 2\n", encoding="utf-8")
        rlf.observe_external_edit(
            self.stack.root,
            artifact_id=art_id,
            workspace_root=ws,
            operation_id=f"op_obs_{art_id}",
            actor_ref="external_editor",
            trusted_actor_identity=False,
            now_iso=T0,
        )
        return md_file

    def test_02_get_honest_ux_zero_write_fresh_and_populated_roots(self) -> None:
        self.assertIsInstance(self.stack.server, phone_server.PhoneHTTPServer)

        # Fresh root: zero-write in both partner and subject views
        before_fresh = _snapshot_tree(self.stack.root)
        c_p, d_p = self._http("GET", "/api/phone/v02/honest-ux")
        c_s, d_s = self._http("GET", "/api/phone/v02/honest-ux", capability=CAP_TOKEN)
        after_fresh = _snapshot_tree(self.stack.root)
        self.assertEqual(before_fresh, after_fresh)
        self.assertEqual(c_p, 200)
        self.assertEqual(c_s, 200)
        self.assertTrue(d_p["ok"])
        self.assertEqual(d_p["llm_calls"], 0)
        for key in (
            "provenance",
            "chatless_traces",
            "md_conflicts",
            "forget_cases",
            "rescaled_copractices",
            "periods",
            "scraps",
            "personal_tools",
            "skill_attributions",
            "budget_survival",
            "crash_reconciler_summary",
        ):
            self.assertIn(key, d_p["data"])
            self.assertIn(key, d_s["data"])
        self.assertEqual(set(d_p["data"]["chatless_traces"].keys()), {"objects", "works", "bookmarks", "waits"})

        # Query string & bad capability rejected
        c_q, _ = self._http("GET", "/api/phone/v02/honest-ux?foo=1")
        self.assertEqual(c_q, 400)
        c_bad, _ = self._http("GET", "/api/phone/v02/honest-ux", capability="wrong-cap")
        self.assertEqual(c_bad, 403)

        # Populated root: still 100% zero-write
        self._seed_diverged_md("art_pop_1", visibility="shared")
        lived_slices.mutate_v02_lived_slices(
            self.stack.root,
            {
                "action": "register_copractice_routine",
                "routine_id": "cop_pop_1",
                "title": "Evening walk relay",
                "cadence_mode": "light_relay",
                "frequency_per_week": 2,
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )
        before_pop = _snapshot_tree(self.stack.root)
        c_p2, d_p2 = self._http("GET", "/api/phone/v02/honest-ux")
        c_s2, d_s2 = self._http("GET", "/api/phone/v02/honest-ux", capability=CAP_TOKEN)
        after_pop = _snapshot_tree(self.stack.root)
        self.assertEqual(before_pop, after_pop)
        self.assertEqual(c_p2, 200)
        self.assertEqual(c_s2, 200)
        self.assertEqual(d_p2["data"]["md_conflicts"][0]["state"], "DIVERGED")
        self.assertEqual(d_s2["data"]["rescaled_copractices"][0]["practice_id"], "cop_pop_1")

    def test_03_partner_privacy_and_zero_secret_echo_in_forget_cases(self) -> None:
        secret = "LEAK_RAW_SECRET_999"
        rlf._ensure_control_ledger_locked(self.stack.root)
        rlf.ingest_source(
            self.stack.root,
            source_id="src_priv_turn",
            text=f"private utterance {secret}",
            visibility="private",
            operation_id="op_src_priv",
            now_iso=T0,
        )
        rlf.submit_forget_command(
            self.stack.root,
            command_text=f"忘掉刚才那段 {secret}",
            operation_id="op_hold_priv",
            recent_source_ids=["src_priv_turn"],
            actor_side="subject",
            shared_with_partner=False,
            now_iso=T0,
        )
        rlf.submit_forget_command(
            self.stack.root,
            command_text="忘掉刚才那段 shared scope",
            operation_id="op_hold_shared",
            recent_source_ids=["src_priv_turn"],
            actor_side="partner",
            shared_with_partner=True,
            now_iso=T0,
        )

        # Private vs shared periods
        asp.record_autobiographical_episode(
            self.stack.root,
            {
                "period_id": "2026-10",
                "domain": "sketch",
                "title": "Private failure",
                "summary": "Unshared rough note",
                "outcome": "abandoned",
                "visibility": "private",
            },
            now_iso=T0,
        )
        asp.compile_period_summary(self.stack.root, {"period_id": "2026-10"}, now_iso=T0)
        asp.record_autobiographical_episode(
            self.stack.root,
            {
                "period_id": "2026-11",
                "domain": "coastal",
                "title": "Shared walk",
                "summary": "Walked by seawall",
                "outcome": "succeeded",
                "visibility": "shared",
            },
            now_iso=T0,
        )
        asp.compile_period_summary(self.stack.root, {"period_id": "2026-11"}, now_iso=T0)

        # Private vs shared scrap & personal script
        lived_slices.mutate_v02_lived_slices(
            self.stack.root,
            {
                "action": "salvage_scrap_fragment",
                "scrap_id": "scrap_priv",
                "source_work_id": "work:priv",
                "source_version_id": "v1",
                "source_outcome": "unfinished",
                "fragment_title": "Private fragment",
                "reusable_payload": {"k": "v"},
                "applicability_tags": ["private_tag"],
                "provenance_receipt": "rcpt:p1",
                "shared": False,
            },
            actor_side="subject",
            now_iso=T0,
        )
        lived_slices.mutate_v02_lived_slices(
            self.stack.root,
            {
                "action": "salvage_scrap_fragment",
                "scrap_id": "scrap_pub",
                "source_work_id": "work:pub",
                "source_version_id": "v1",
                "source_outcome": "unfinished",
                "fragment_title": "Shared coastal palette",
                "reusable_payload": {"base": "#6E8294"},
                "applicability_tags": ["coastal"],
                "provenance_receipt": "rcpt:p2",
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )
        lived_slices.mutate_v02_lived_slices(
            self.stack.root,
            {
                "action": "register_personal_script",
                "script_id": "tool_priv",
                "title": "Private script",
                "options": [
                    {"option_id": "opt_1", "label": "Step 1", "used_count": 3, "maintenance_cost": 1.0},
                    {"option_id": "opt_2", "label": "Step 2", "used_count": 0, "maintenance_cost": 1.0},
                ],
                "shared": False,
            },
            actor_side="subject",
            now_iso=T0,
        )
        lived_slices.mutate_v02_lived_slices(
            self.stack.root,
            {
                "action": "register_personal_script",
                "script_id": "tool_pub",
                "title": "Shared script",
                "options": [
                    {"option_id": "opt_1", "label": "Step 1", "used_count": 3, "maintenance_cost": 1.0},
                    {"option_id": "opt_2", "label": "Step 2", "used_count": 0, "maintenance_cost": 1.0},
                ],
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )

        code_p, doc_p = self._http("GET", "/api/phone/v02/honest-ux")
        self.assertEqual(code_p, 200)
        raw_p = json.dumps(doc_p, ensure_ascii=False)
        self.assertNotIn(secret, raw_p)
        for forbidden_key in ("original_text", "matched_quote", "raw_secret_text"):
            self.assertNotIn(forbidden_key, raw_p)

        data_p = doc_p["data"]
        self.assertEqual([p["period_id"] for p in data_p["periods"]], ["2026-11"])
        self.assertEqual([s["scrap_id"] for s in data_p["scraps"]], ["scrap_pub"])
        self.assertEqual([t["script_id"] for t in data_p["personal_tools"]], ["tool_pub"])
        self.assertEqual(len(data_p["forget_cases"]), 1)
        self.assertEqual(set(data_p["forget_cases"][0].keys()), {"case_id", "status", "target_domain"})

        code_s, doc_s = self._http("GET", "/api/phone/v02/honest-ux", capability=CAP_TOKEN)
        self.assertEqual(code_s, 200)
        self.assertNotIn(secret, json.dumps(doc_s, ensure_ascii=False))
        self.assertEqual(len(doc_s["data"]["forget_cases"]), 2)
        self.assertEqual(len(doc_s["data"]["periods"]), 2)
        self.assertEqual(len(doc_s["data"]["scraps"]), 2)
        self.assertEqual(len(doc_s["data"]["personal_tools"]), 2)

    def test_04_post_common_guards_csrf_empty_body_and_identity_fields(self) -> None:
        # Missing CSRF -> 403
        c_nocsrf, _ = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {"action": "mark_representation_divergence_seen", "artifact_id": "a1"},
            csrf=False,
        )
        self.assertEqual(c_nocsrf, 403)

        # Empty object -> 400
        c_empty, _ = self._http("POST", "/api/phone/v02/honest-ux", {}, csrf=True)
        self.assertEqual(c_empty, 400)

        # Forbidden identity smuggling -> 409 CLIENT_IDENTITY_REFUSED
        c_id, d_id = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {"action": "mark_representation_divergence_seen", "artifact_id": "a1", "uid": 0},
            csrf=True,
        )
        self.assertEqual(c_id, 409)
        self.assertIn("CLIENT_IDENTITY_REFUSED", str(d_id.get("reason_code") or d_id.get("error_code") or ""))

        # Allowed identity fields: artifact_id, actor, source, owner -> 200
        c_ok, d_ok = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "mark_representation_divergence_seen",
                "artifact_id": "art_allow_1",
                "actor": "partner_ui",
                "source": "desktop",
                "owner": "partner",
                "operation_key": "op_allow_id_1",
            },
            csrf=True,
        )
        self.assertEqual(c_ok, 200)
        self.assertTrue(d_ok["ok"])

    def test_05_all_five_subject_only_actions_return_403_for_partner(self) -> None:
        subject_payloads = [
            {"action": "explicit_import_representation", "artifact_id": "art_x"},
            {"action": "approve_pending_forget_hold", "case_id": "hold_x"},
            {"action": "record_period_snapshot", "period_id": "2026-11"},
            {"action": "salvage_work_scrap", "scrap_id": "sc_x"},
            {"action": "simplify_personal_tool", "script_id": "tl_x"},
        ]
        before = _snapshot_tree(self.stack.root)
        for p in subject_payloads:
            # Partner door (no capability) even with actor_side="subject" smuggling -> 403
            c1, d1 = self._http(
                "POST",
                "/api/phone/v02/honest-ux",
                {**p, "actor_side": "subject"},
                csrf=True,
            )
            self.assertEqual(c1, 403, f"Expected 403 for {p['action']}, got {c1} {d1}")
            self.assertEqual(d1.get("reason"), "FORBIDDEN_PARTNER_ACTION")
            self.assertEqual(d1.get("reason_code"), "FORBIDDEN_PARTNER_ACTION")

            # Subject door explicitly declaring actor_side="partner" -> 403
            c2, d2 = self._http(
                "POST",
                "/api/phone/v02/honest-ux",
                {**p, "actor_side": "partner"},
                csrf=True,
                capability=CAP_TOKEN,
            )
            self.assertEqual(c2, 403)
            self.assertEqual(d2.get("reason_code"), "FORBIDDEN_PARTNER_ACTION")
        self.assertEqual(before, _snapshot_tree(self.stack.root))

    def test_06_all_four_partner_actions_work_end_to_end_and_idempotent(self) -> None:
        self._seed_diverged_md("art_p_1", visibility="shared")
        lived_slices.mutate_v02_lived_slices(
            self.stack.root,
            {
                "action": "register_copractice_routine",
                "routine_id": "cop_p_1",
                "title": "Quiet tea check-in",
                "cadence_mode": "light_relay",
                "frequency_per_week": 2,
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )

        # 1. mark_representation_divergence_seen (plus idempotency + conflict)
        p_div = {
            "action": "mark_representation_divergence_seen",
            "artifact_id": "art_p_1",
            "representation_state": "DIVERGED",
            "operation_key": "op_partner_div_1",
        }
        c1, r1 = self._http("POST", "/api/phone/v02/honest-ux", p_div, csrf=True)
        self.assertEqual(c1, 200)
        self.assertEqual(r1["data"]["actor_side"], "partner")
        c1_dup, r1_dup = self._http("POST", "/api/phone/v02/honest-ux", p_div, csrf=True)
        self.assertEqual(c1_dup, 200)
        self.assertEqual(r1_dup["status"], "already_applied")
        c1_conf, _ = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {**p_div, "artifact_id": "art_other"},
            csrf=True,
        )
        self.assertEqual(c1_conf, 409)

        # 2. request_forget_scope_hold: empty conversation fails closed (409), then shared source succeeds + idempotent replay
        c2_empty, r2_empty = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "request_forget_scope_hold",
                "scope_hint": "recent_turn",
                "target_domain": "conversation",
                "operation_key": "op_partner_hold_empty_0",
            },
            csrf=True,
        )
        self.assertEqual(c2_empty, 409)
        self.assertEqual(r2_empty.get("error_code"), "FORGET_NO_ELIGIBLE_CANDIDATES")

        c_src, _ = rlf.ingest_source(
            self.stack.root,
            source_id="src_partner_conv_1",
            text="shared tea chat",
            visibility="shared",
            topic="topic_tea",
            session_id="sess_p_1",
            operation_id="op_seed_p_src_1",
            now_iso=T0,
        )
        self.assertEqual(c_src, 200)

        c2, r2 = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "request_forget_scope_hold",
                "scope_hint": "recent_turn",
                "target_domain": "conversation",
                "operation_key": "op_partner_hold_1",
            },
            csrf=True,
        )
        self.assertEqual(c2, 200)
        hold_id = r2["data"]["case_id"]
        self.assertEqual(r2["data"]["status"], rlf.CASE_HOLD_PENDING)
        c2_rep, r2_rep = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "request_forget_scope_hold",
                "scope_hint": "recent_turn",
                "target_domain": "conversation",
                "operation_key": "op_partner_hold_1",
            },
            csrf=True,
        )
        self.assertEqual(c2_rep, 200)
        self.assertTrue(r2_rep.get("idempotent_replay"))

        # 3. clarify_forget_scope
        c3, r3 = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "clarify_forget_scope",
                "case_id": hold_id,
                "scope_choice": "current_topic",
                "operation_key": "op_partner_clarify_1",
            },
            csrf=True,
        )
        self.assertEqual(c3, 200)
        self.assertEqual(r3["data"]["status"], rlf.CASE_RETRACTED)

        # 4. confirm_rescaled_copractice
        c4, r4 = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "confirm_rescaled_copractice",
                "practice_id": "cop_p_1",
                "scale_mode": "light_relay",
                "confirmed": True,
                "operation_key": "op_partner_cop_1",
            },
            csrf=True,
        )
        self.assertEqual(c4, 200)
        self.assertTrue(r4["data"]["partner_confirmed"])

        # GET reflects all 4 mutations
        cg, dg = self._http("GET", "/api/phone/v02/honest-ux")
        self.assertEqual(cg, 200)
        self.assertTrue(dg["data"]["md_conflicts"][0]["divergence_seen"])
        self.assertEqual(dg["data"]["forget_cases"][0]["status"], rlf.CASE_RETRACTED)
        self.assertTrue(dg["data"]["rescaled_copractices"][0]["partner_confirmed"])

    def test_07_all_five_subject_only_actions_execute_cleanly_with_capability(self) -> None:
        self._seed_diverged_md("art_subj_1", visibility="shared")
        rlf.ingest_source(
            self.stack.root,
            source_id="src_subj_1",
            text="sensitive line",
            visibility="private",
            operation_id="op_src_s1",
            now_iso=T0,
        )
        _, hold_out = rlf.submit_forget_command(
            self.stack.root,
            command_text="忘掉刚才那段",
            operation_id="op_hold_s1",
            recent_source_ids=["src_subj_1"],
            now_iso=T0,
        )
        hold_id = hold_out["hold_id"]
        asp.record_autobiographical_episode(
            self.stack.root,
            {
                "period_id": "2026-11",
                "domain": "painting",
                "title": "Harbor sketch",
                "summary": "Completed sketch",
                "outcome": "succeeded",
                "visibility": "shared",
            },
            now_iso=T0,
        )
        lived_slices.mutate_v02_lived_slices(
            self.stack.root,
            {
                "action": "register_personal_script",
                "script_id": "tool_subj_1",
                "title": "Export notes",
                "options": [
                    {"option_id": "opt_keep", "label": "Core export", "used_count": 5, "maintenance_cost": 1.0},
                    {"option_id": "opt_drop", "label": "Unused sync", "used_count": 0, "maintenance_cost": 2.0},
                ],
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )

        # 1. explicit_import_representation (stale base -> 409 BLOCKED_CONFLICT, valid base -> 200)
        c_stale, d_stale = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "explicit_import_representation",
                "artifact_id": "art_subj_1",
                "base_projection_version": "ver_wrong",
                "operation_key": "op_s_imp_stale",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_stale, 409)
        self.assertEqual(d_stale["reason_code"], "BLOCKED_CONFLICT")

        c1, r1 = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "explicit_import_representation",
                "artifact_id": "art_subj_1",
                "operation_key": "op_s_imp_ok",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c1, 200)
        self.assertEqual(r1["data"]["representation_health"], "CURRENT")

        # 2. approve_pending_forget_hold
        c2, r2 = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "approve_pending_forget_hold",
                "case_id": hold_id,
                "resolution": "confirm_delete",
                "operation_key": "op_s_fgt_ok",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c2, 200)
        self.assertEqual(r2["data"]["status"], rlf.CASE_COMPLETE)

        # 3. record_period_snapshot
        c3, r3 = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "record_period_snapshot",
                "period_id": "2026-11",
                "visibility": "shared",
                "note": "Monthly snapshot",
                "operation_key": "op_s_snap_ok",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c3, 200)
        self.assertTrue(r3["data"]["snapshot_id"])

        # 4. salvage_work_scrap
        c4, r4 = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "salvage_work_scrap",
                "scrap_id": "scrap_s_1",
                "source_work_id": "work:h1",
                "source_version_id": "v1",
                "source_outcome": "unfinished",
                "fragment_title": "Seafoam glaze recipe",
                "reusable_payload": {"glaze": "seafoam"},
                "applicability_tags": ["ceramic", "coastal"],
                "provenance_receipt": "rcpt:sc1",
                "shared": True,
                "operation_key": "op_s_scrap_ok",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c4, 200)
        self.assertEqual(r4["data"]["scrap_id"], "scrap_s_1")

        # 5. simplify_personal_tool
        c5, r5 = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "simplify_personal_tool",
                "script_id": "tool_subj_1",
                "removed_option_ids": ["opt_drop"],
                "reason": "Keep core export only",
                "operation_key": "op_s_tool_ok",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c5, 200)
        self.assertEqual(r5["data"]["script_id"], "tool_subj_1")

    def test_08_daily_loop_bridge_barrier_divergence_unvalidated_and_scrap_matching(self) -> None:
        # 1. Seed Forget hold for art_held, and DIVERGED .md for art_keep
        rlf._ensure_control_ledger_locked(self.stack.root)
        rlf.create_managed_artifact(
            self.stack.root,
            artifact_id="art_held",
            controller_principal_ref="owner_1",
            subject_id=SUBJECT,
            initial_body="# Held note\n",
            operation_id="op_held_c",
            visibility="private",
            now_iso=T0,
        )
        rlf.submit_forget_command(
            self.stack.root,
            command_text="忘掉刚才那段",
            operation_id="op_held_f",
            recent_source_ids=["art_held"],
            now_iso=T0,
        )
        self._seed_diverged_md("art_keep", visibility="shared")

        # 2. Seed tomorrow note referencing art_held and workbench tray referencing art_keep + coastal scrap
        phone_dir = self.stack.root / "phone"
        phone_dir.mkdir(parents=True, exist_ok=True)
        (phone_dir / "desk_tomorrow_note.json").write_text(
            json.dumps({
                "note_id": "note_held_1",
                "title": "Held note",
                "artifact_id": "art_held",
                "status": "OPEN",
            }),
            encoding="utf-8",
        )
        tray_state, _ = workbench_curio_view.set_tray(
            workbench_curio_view.empty_tray_state(),
            {
                "images": [],
                "materials": [],
                "work": {"artifact_id": "art_keep", "baseline_version_id": "ver_1"},
                "note": "coastal watercolor study",
            },
            T0,
        )
        workbench_curio_view.write_tray_state(self.stack.root, tray_state)
        lived_slices.mutate_v02_lived_slices(
            self.stack.root,
            {
                "action": "salvage_scrap_fragment",
                "scrap_id": "scrap_coastal_1",
                "source_work_id": "work:old_coastal",
                "source_version_id": "v2",
                "source_outcome": "abandoned",
                "fragment_title": "Mist gray coastal wash",
                "reusable_payload": {"tone": "#6E8294"},
                "applicability_tags": ["coastal", "watercolor"],
                "provenance_receipt": "rcpt:coastal",
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )
        asp.register_skill_release(
            self.stack.root,
            {
                "method_id": "skill_migrated_1",
                "tool_version": "v2",
                "model_id": "m2",
                "scope": "painting",
                "attribution": "PRACTICED_MASTERY",
                "criteria_id": "c1",
                "standard_revision": "r1",
                "held_out": True,
                "ablation_gain_vs_bare_model": 0.15,
                "status": "VALIDATED",
            },
            now_iso=T0,
        )
        asp.bump_carrier_or_tool_version(
            self.stack.root,
            {
                "from_model_id": "m2",
                "to_model_id": "m3",
                "from_tool_version": "v2",
                "to_tool_version": "v3",
                "method_ids": ["skill_migrated_1"],
            },
            now_iso=T1,
        )

        before = _snapshot_tree(self.stack.root)
        collected = loop_bridge.collect_v02_v03_loop_candidates(self.stack.root, now_iso=T1)
        after = _snapshot_tree(self.stack.root)
        self.assertEqual(before, after, "collect_v02_v03_loop_candidates must be zero-write")
        self.assertEqual(collected["llm_calls"], 0)

        # Held item filtered by barrier
        filtered = collected["gating"]["filtered_candidates"]
        self.assertTrue(
            any(f.get("reason_code") == "FORGET_BARRIER_HELD" for f in filtered),
            f"Expected FORGET_BARRIER_HELD in {filtered}",
        )
        # Diverged item annotated, never silently overwritten
        descs = collected["descriptors"]
        keep_descs = [d for d in descs.values() if d.get("artifact_ref") == "art_keep"]
        self.assertTrue(keep_descs, f"Expected art_keep descriptor in {descs}")
        self.assertTrue(keep_descs[0].get("representation_diverged"))
        self.assertTrue(keep_descs[0].get("auto_overwrite_blocked"))
        self.assertTrue(keep_descs[0].get("matched_scraps"))

        summary = collected["resident_context_summary"]
        self.assertTrue(summary["barrier_active"])
        self.assertIn("art_keep", summary["diverged_artifact_ids"])
        self.assertIn("skill_migrated_1", summary["unvalidated_after_migration_skills"])
        self.assertIn("scrap_coastal_1", summary["matched_scrap_ids"])

        cb = loop_bridge.check_barrier_and_divergence(self.stack.root)
        self.assertTrue(cb["barrier_active"])
        self.assertTrue(cb["diverged"])

    def test_09_lived_context_bridge_calm_zero_chars_and_budget_cap(self) -> None:
        # Unconfigured root -> calm_silent == True (0 chars)
        with tempfile.TemporaryDirectory(prefix="v02-calm-") as td:
            calm_root = Path(td)
            bd_empty = lived_context_bridge.measure_context_budget_breakdown(
                calm_root,
                cfg={},
                incoming_text="hello",
                now_iso=T0,
            )
            self.assertTrue(bd_empty["calm_silent"])
            self.assertEqual(bd_empty["total_chars"], 0)
            self.assertEqual(bd_empty["rendered_text"], "")

        # Quiet / calm files on configured root -> 0 extra chars vs absent
        cfg = {"day_root": str(self.stack.root), "phone": {"enabled": False, "private_artifacts": True}}
        out_absent = lived_context_bridge.build_lived_turn_context(
            self.stack.root, cfg, "hello", now_iso=T0
        )
        # Seed CURRENT (non-diverged) .md and VALIDATED skill and non-matching scrap
        ws = self.stack.root / "workspace_e2e"
        ws.mkdir(parents=True, exist_ok=True)
        rlf._ensure_control_ledger_locked(self.stack.root)
        rlf.create_managed_artifact(
            self.stack.root,
            artifact_id="art_calm",
            controller_principal_ref="owner_1",
            subject_id=SUBJECT,
            initial_body="# Calm\n",
            operation_id="op_calm_c",
            visibility="shared",
            now_iso=T0,
        )
        rlf.bind_representation(
            self.stack.root,
            artifact_id="art_calm",
            workspace_root=ws,
            workspace_rel_path="calm.md",
            operation_id="op_calm_b",
            now_iso=T0,
        )
        out_quiet = lived_context_bridge.build_lived_turn_context(
            self.stack.root, cfg, "hello", now_iso=T0
        )
        self.assertEqual(out_quiet, out_absent)

        # Now make .md DIVERGED + add UNVALIDATED skill + matching scrap -> within 1800 chars
        (ws / "calm.md").write_text("# Calm\nEdited externally\n", encoding="utf-8")
        lived_slices.mutate_v02_lived_slices(
            self.stack.root,
            {
                "action": "salvage_scrap_fragment",
                "scrap_id": "scrap_calm_match",
                "source_work_id": "work:c1",
                "source_version_id": "v1",
                "source_outcome": "unfinished",
                "fragment_title": "Harbor Mist Swatch",
                "reusable_payload": {"c": "#123456"},
                "applicability_tags": ["harbor"],
                "provenance_receipt": "rcpt:h1",
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )
        asp.register_skill_release(
            self.stack.root,
            {
                "method_id": "skill_recheck_1",
                "tool_version": "v1",
                "model_id": "m1",
                "scope": "notes",
                "attribution": "MODEL_NATIVE",
                "criteria_id": "c1",
                "standard_revision": "r1",
                "held_out": True,
                "ablation_gain_vs_bare_model": 0.1,
                "status": "VALIDATED",
            },
            now_iso=T0,
        )
        asp.bump_carrier_or_tool_version(
            self.stack.root,
            {
                "from_model_id": "m1",
                "to_model_id": "m1",
                "from_tool_version": "v1",
                "to_tool_version": "v2",
                "method_ids": ["skill_recheck_1"],
            },
            now_iso=T1,
        )
        before = _snapshot_tree(self.stack.root)
        bd_active = lived_context_bridge.measure_context_budget_breakdown(
            self.stack.root, cfg, "想继续画 harbor 那张", now_iso=T1
        )
        self.assertEqual(before, _snapshot_tree(self.stack.root))
        self.assertTrue(bd_active["within_budget"])
        self.assertLessEqual(bd_active["total_chars"], 1800)
        self.assertIn("skill_recheck_1", bd_active["rendered_text"])
        self.assertIn("Harbor Mist Swatch", bd_active["rendered_text"])
        self.assertIn("工作区文档外部有改动", bd_active["rendered_text"])

    def test_10_chiyo_context_engine_barrier_suppression_and_end_to_end_select_context(self) -> None:
        # Seed agency depth placed memory cues: one held by Forget barrier, one visible
        ad_engine.apply_agency_depth_mutation(
            self.stack.root,
            {
                "action": "place_memory_cue",
                "cue_id": "cue_secret_hold",
                "artifact_ref": "artifact:cue_secret_hold",
                "spot_id": "desk_corner",
                "cue_text_cn": "SECRET_HELD_CUE_999",
                "shared": True,
                "operation_key": "op_cue_1",
            },
            actor_side="subject",
            now_iso=T0,
        )
        ad_engine.apply_agency_depth_mutation(
            self.stack.root,
            {
                "action": "place_memory_cue",
                "cue_id": "cue_safe_open",
                "artifact_ref": "artifact:cue_safe_open",
                "spot_id": "window_sill",
                "cue_text_cn": "SAFE_OPEN_SHELL",
                "shared": True,
                "operation_key": "op_cue_2",
            },
            actor_side="subject",
            now_iso=T0,
        )
        rlf._ensure_control_ledger_locked(self.stack.root)
        rlf.ingest_source(
            self.stack.root,
            source_id="cue_secret_hold",
            text="SECRET_HELD_RAW_999",
            visibility="shared",
            operation_id="op_ing_cue",
            now_iso=T0,
        )
        rlf.submit_forget_command(
            self.stack.root,
            command_text="忘掉刚才那段 SECRET_HELD_RAW_999",
            operation_id="op_hold_cue",
            recent_source_ids=["cue_secret_hold"],
            now_iso=T0,
        )
        self._seed_diverged_md("art_ce_1", visibility="shared")

        with tempfile.TemporaryDirectory(prefix="v02-hermes-home-") as hdir:
            hermes_home = Path(hdir)
            chiyo_dir = hermes_home / "chiyo"
            chiyo_dir.mkdir(parents=True, exist_ok=True)
            uid = os.getuid() if hasattr(os, "getuid") else None
            cfg_doc = {
                "owner": SUBJECT,
                "cli_owner": True,
                "local_owner_uid": uid,
                "memory": False,
                "life": False,
                "day_root": str(self.stack.root),
                "phone": {
                    "enabled": True,
                    "base_url": self.base_url,
                    "day_root": str(self.stack.root),
                    "private_artifacts": True,
                    "capability": CAP_TOKEN,
                },
            }
            (chiyo_dir / "config.json").write_text(json.dumps(cfg_doc), encoding="utf-8")

            saved_hermes_env = os.environ.get("HERMES_HOME")
            saved_get_home = getattr(hermes_plugin, "get_hermes_home", None)
            os.environ["HERMES_HOME"] = str(hermes_home)
            hermes_plugin.get_hermes_home = lambda: hermes_home  # type: ignore[assignment]
            hermes_plugin.clear_session_auth_context()
            try:
                engine = hermes_plugin.ChiyoContextEngine()
                engine.on_session_start("sess_hux_1", platform="cli")
                before = _snapshot_tree(self.stack.root)
                selected = engine.select_context(
                    [{"role": "user", "content": "桌边现在有什么？"}],
                    incoming_message={"content": "桌边现在有什么？"},
                )
                after = _snapshot_tree(self.stack.root)
                self.assertEqual(before, after, "ChiyoContextEngine.select_context must be zero-write")
                self.assertIsNotNone(selected)
                combined = "\n".join(str(m.get("content") or "") for m in (selected or []))
                self.assertNotIn("SECRET_HELD_CUE_999", combined)
                self.assertNotIn("SECRET_HELD_RAW_999", combined)
                self.assertIn("SAFE_OPEN_SHELL", combined)
            finally:
                hermes_plugin.clear_session_auth_context()
                if saved_get_home is not None:
                    hermes_plugin.get_hermes_home = saved_get_home  # type: ignore[assignment]
                if saved_hermes_env is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = saved_hermes_env

    def test_11_crash_reconciler_summary_unknown_surfaces_in_honest_ux_get(self) -> None:
        rec_store = crash_rec.CrashAndPartialReconcilerStore(self.stack.root)
        rec_store.initialize()
        rec_store.create_decision(decision_id="dec_hux_1")
        t1 = rec_store.tx_t1_commit_adoption_activity_action(
            decision_id="dec_hux_1",
            request_id="req_hux_1",
            logical_action_id="la_hux_1",
        )
        rec_store.create_action_attempt(attempt_id="att_hux_1", action_id=t1["action_id"])
        rec_store.tx_t3_admit_action_result(
            action_id=t1["action_id"],
            attempt_id="att_hux_1",
            outcome="UNKNOWN",
            receipt_kind="PROVIDER_RECEIPT",
            receipt_id="rcpt_hux_unk_1",
        )

        before = _snapshot_tree(self.stack.root)
        code_p, doc_p = self._http("GET", "/api/phone/v02/honest-ux")
        code, doc = self._http("GET", "/api/phone/v02/honest-ux", capability=CAP_TOKEN)
        after = _snapshot_tree(self.stack.root)
        self.assertEqual(before, after)
        self.assertEqual(code_p, 200)
        summary_p = doc_p["data"]["crash_reconciler_summary"]
        self.assertTrue(summary_p.get("perspective_redacted"))
        self.assertFalse(summary_p["db_exists"])
        self.assertEqual(summary_p["unknown_actions"], 0)
        self.assertEqual(doc_p["data"]["provenance"]["epistemic_status"], "VERIFIED")
        self.assertEqual(code, 200)
        summary = doc["data"]["crash_reconciler_summary"]
        self.assertTrue(summary["db_exists"])
        self.assertEqual(summary["unknown_actions"], 1)
        self.assertTrue(doc["data"]["provenance"]["has_unknown"])
        self.assertEqual(doc["data"]["provenance"]["epistemic_status"], "UNKNOWN")

    def test_12_forget_barrier_suppresses_held_items_across_honest_ux_get(self) -> None:
        rlf._ensure_control_ledger_locked(self.stack.root)
        rlf.ingest_source(
            self.stack.root,
            source_id="scrap_held_1",
            text="held scrap source",
            visibility="shared",
            operation_id="op_sc_src",
            now_iso=T0,
        )
        lived_slices.mutate_v02_lived_slices(
            self.stack.root,
            {
                "action": "salvage_scrap_fragment",
                "scrap_id": "scrap_held_1",
                "source_work_id": "work:held_src",
                "source_version_id": "v1",
                "source_outcome": "unfinished",
                "fragment_title": "Held fragment should vanish",
                "reusable_payload": {"k": "1"},
                "applicability_tags": ["held_tag"],
                "provenance_receipt": "rcpt:held",
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )
        lived_slices.mutate_v02_lived_slices(
            self.stack.root,
            {
                "action": "salvage_scrap_fragment",
                "scrap_id": "scrap_visible_1",
                "source_work_id": "work:visible_src",
                "source_version_id": "v1",
                "source_outcome": "unfinished",
                "fragment_title": "Visible fragment stays",
                "reusable_payload": {"k": "2"},
                "applicability_tags": ["vis_tag"],
                "provenance_receipt": "rcpt:vis",
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )

        # Put scrap_held_1 under Forget hold via partner POST
        c_hold, _ = self._http(
            "POST",
            "/api/phone/v02/honest-ux",
            {
                "action": "request_forget_scope_hold",
                "scope_hint": "recent_turn",
                "recent_source_ids": ["scrap_held_1"],
                "operation_key": "op_hold_scrap_1",
            },
            csrf=True,
        )
        self.assertEqual(c_hold, 200)

        for cap_hdr in (None, CAP_TOKEN):
            code, doc = self._http("GET", "/api/phone/v02/honest-ux", capability=cap_hdr)
            self.assertEqual(code, 200)
            scrap_ids = [s["scrap_id"] for s in doc["data"]["scraps"]]
            self.assertNotIn("scrap_held_1", scrap_ids)
            self.assertIn("scrap_visible_1", scrap_ids)


if __name__ == "__main__":
    unittest.main()

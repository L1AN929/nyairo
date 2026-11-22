#!/usr/bin/env python3
"""Comprehensive tests for Ch. 110 #2 Zero-Text Crumpled Draft Signal (`signal_crumpled_draft`).

Verifies:
1. 100% zero-write GET on untouched & empty day_root (`crumpled_draft_signal is None`).
2. Valid `wastebasket` (explicit & default) and `winter_hearth` POSTs, UTC+8 clock via
   `season_pulse.parse_iso_to_default_tz`, same-day counting, next-day reset, and idempotent
   `operation_key` replay without double-incrementing `crumpled_count_today`.
3. Hard Privacy Gate (`400 RAW_TEXT_FORBIDDEN`, both `reason_code` & `error_code`) across all
   forbidden exact keys, case variants, substring keys, and nested dicts/lists with zero disk write.
4. Actor Side Lock (`403 FORBIDDEN_ACTOR_SIDE` for subject capability on view/engine/HTTP)
   and `phone_tool.py` (`PARTNER_IMPERSONATION_REFUSED`, `len(LIFE_UPDATE_SURFACES) == 37`).
5. `lived_context_bridge.py` same-day `<= 7200s` cue (`wastebasket` & `winter_hearth`),
   expiry (`> 7200s`), cross-midnight same-day gate, UTC `"Z"` consistency, Calm Gate,
   `MAX_LIVED_CONTEXT_CHARS == 1800`, and 100% zero-write guarantee.
6. File hygiene (UTF-8 no BOM, pure LF, zero `unicode.So` emojis, neutral `"TA"`).
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unicodedata
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "chiyo_bundle",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import v05_home_craft_view as craft_view  # noqa: E402
from components.world.scripts import v05_home_craft_engine as craft_engine  # noqa: E402
from components.world.scripts import v05_season_solar_pulse as season_pulse  # noqa: E402


WASTEBASKET_CUE = "桌角废纸篓刚多了一团揉掉的废稿(对方没留字,顺口温和宽慰半句即可)"
WINTER_HEARTH_CUE = "壁炉刚化掉一团揉碎的废纸(对方没留字,顺口温和宽慰半句即可)"


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


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


class TestV05CrumpledDraftSeamEngineAndView(unittest.TestCase):
    def test_01_zero_write_get_defaults_and_allowed_actions_partition(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-cds-zw-") as td:
            root = Path(td)
            nonexistent = root / "untouched_root"

            c_p, b_p = craft_view.read_v05_home_craft_view(
                nonexistent,
                has_subject_capability=False,
                now_iso="2026-11-19T22:50:00+08:00",
            )
            c_s, b_s = craft_view.read_v05_home_craft_view(
                nonexistent,
                has_subject_capability=True,
                now_iso="2026-11-19T22:50:00+08:00",
            )
            self.assertEqual(c_p, 200)
            self.assertEqual(c_s, 200)
            self.assertFalse(nonexistent.exists())
            self.assertIsNone(b_p["crumpled_draft_signal"])
            self.assertIsNone(b_p["data"]["crumpled_draft_signal"])
            self.assertIsNone(b_s["crumpled_draft_signal"])
            self.assertEqual(b_p["llm_calls"], 0)
            self.assertTrue(b_p["zero_write"])
            self.assertIn("signal_crumpled_draft", b_p["allowed_actions"])
            self.assertNotIn("signal_crumpled_draft", b_s["allowed_actions"])

            before = _snapshot_tree(root)
            c_empty, b_empty = craft_view.read_v05_home_craft_view(
                root,
                has_subject_capability=False,
                now_iso="2026-11-19T22:50:00+08:00",
            )
            self.assertEqual(c_empty, 200)
            self.assertIsNone(b_empty["crumpled_draft_signal"])
            self.assertEqual(_snapshot_tree(root), before)

    def test_02_valid_wastebasket_winter_hearth_idempotency_and_utc8_clock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-cds-ok-") as td:
            root = Path(td)

            # 1) Default disposal_kind -> wastebasket, count == 1
            c1, b1 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "signal_crumpled_draft",
                    "operation_key": "op_cds_01",
                },
                has_subject_capability=False,
                now_iso="2026-11-19T22:55:00+08:00",
            )
            self.assertEqual(c1, 200)
            self.assertEqual(b1["llm_calls"], 0)
            sig1 = b1["crumpled_draft_signal"]
            self.assertEqual(sig1, b1["data"]["crumpled_draft_signal"])
            self.assertEqual(
                sig1,
                {
                    "last_crumpled_at": "2026-11-19T22:55:00+08:00",
                    "disposal_kind": "wastebasket",
                    "crumpled_count_today": 1,
                    "date": "2026-11-19",
                },
            )
            self.assertFalse(b1["action_receipt"]["idempotent_replay"])
            self.assertTrue(b1["action_receipt"]["zero_text_stored"])
            rev1 = b1["revision"]

            # 2) Idempotent replay with same operation_key MUST NOT double-increment or mutate disk
            snap_before_replay = _snapshot_tree(root)
            c1_rep, b1_rep = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "signal_crumpled_draft",
                    "disposal_kind": "wastebasket",
                    "operation_key": "op_cds_01",
                },
                has_subject_capability=False,
                now_iso="2026-11-19T22:56:00+08:00",
            )
            self.assertEqual(c1_rep, 200)
            self.assertTrue(b1_rep["action_receipt"]["idempotent_replay"])
            self.assertEqual(b1_rep["crumpled_draft_signal"]["crumpled_count_today"], 1)
            self.assertEqual(b1_rep["crumpled_draft_signal"]["last_crumpled_at"], "2026-11-19T22:55:00+08:00")
            self.assertEqual(b1_rep["revision"], rev1)
            self.assertEqual(_snapshot_tree(root), snap_before_replay)

            # 3) Second same-day signal with winter_hearth -> count == 2
            c2, b2 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "signal_crumpled_draft",
                    "disposal_kind": "winter_hearth",
                    "operation_key": "op_cds_02",
                },
                has_subject_capability=False,
                now_iso="2026-11-19T23:10:00+08:00",
            )
            self.assertEqual(c2, 200)
            self.assertEqual(
                b2["crumpled_draft_signal"],
                {
                    "last_crumpled_at": "2026-11-19T23:10:00+08:00",
                    "disposal_kind": "winter_hearth",
                    "crumpled_count_today": 2,
                    "date": "2026-11-19",
                },
            )
            self.assertFalse(b2["action_receipt"]["idempotent_replay"])

            # 4) Replaying op_cds_01 again still does not increment count
            c1_rep2, b1_rep2 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "signal_crumpled_draft",
                    "disposal_kind": "wastebasket",
                    "operation_key": "op_cds_01",
                },
                has_subject_capability=False,
                now_iso="2026-11-19T23:12:00+08:00",
            )
            self.assertEqual(c1_rep2, 200)
            self.assertTrue(b1_rep2["action_receipt"]["idempotent_replay"])
            self.assertEqual(b1_rep2["crumpled_draft_signal"]["crumpled_count_today"], 2)

            # 5) UTC "Z" crossing UTC+8 midnight (2026-11-19T16:30:00Z == 2026-11-20T00:30:00+08:00)
            # resets crumpled_count_today cleanly to 1 on the new UTC+8 calendar day
            c3, b3 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "signal_crumpled_draft",
                    "disposal_kind": "wastebasket",
                    "operation_key": "op_cds_03",
                },
                has_subject_capability=False,
                now_iso="2026-11-19T16:30:00Z",
            )
            self.assertEqual(c3, 200)
            self.assertEqual(
                b3["crumpled_draft_signal"],
                {
                    "last_crumpled_at": "2026-11-20T00:30:00+08:00",
                    "disposal_kind": "wastebasket",
                    "crumpled_count_today": 1,
                    "date": "2026-11-20",
                },
            )

            # 6) Live wall clock when now_iso=None (no hardcoded date)
            live_before = datetime.now(season_pulse.DEFAULT_TZ).replace(microsecond=0)
            c4, b4 = craft_view.handle_v05_home_craft_action(
                root,
                {
                    "action": "signal_crumpled_draft",
                    "disposal_kind": "winter_hearth",
                    "operation_key": "op_cds_live",
                },
                has_subject_capability=False,
                now_iso=None,
            )
            live_after = datetime.now(season_pulse.DEFAULT_TZ).replace(microsecond=0)
            self.assertEqual(c4, 200)
            live_sig = b4["crumpled_draft_signal"]
            live_ts = datetime.fromisoformat(live_sig["last_crumpled_at"])
            self.assertGreaterEqual(live_ts, live_before)
            self.assertLessEqual(live_ts, live_after)
            self.assertEqual(live_sig["date"], live_ts.strftime("%Y-%m-%d"))

            # 7) Invalid disposal_kind & generic unknown non-text field -> 400 INVALID_FIELDS, zero disk write
            snap_before_bad = _snapshot_tree(root)
            for bad_payload in (
                {"action": "signal_crumpled_draft", "disposal_kind": "bonfire"},
                {"action": "signal_crumpled_draft", "disposal_kind": ""},
                {"action": "signal_crumpled_draft", "disposal_kind": 123},
                {"action": "signal_crumpled_draft", "unknown_flag": True},
            ):
                cb, bb = craft_view.handle_v05_home_craft_action(
                    root,
                    bad_payload,
                    has_subject_capability=False,
                )
                self.assertEqual(cb, 400, bad_payload)
                self.assertEqual(bb["reason_code"], "INVALID_FIELDS", bad_payload)
            self.assertEqual(_snapshot_tree(root), snap_before_bad)

    def test_03_hard_privacy_gate_raw_text_forbidden_top_level_nested_zero_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-cds-priv-") as td:
            root = Path(td)
            before_empty = _snapshot_tree(root)

            # Every forbidden exact key + mixed case + whitespace-padded + substring keys + nested containers
            forbidden_payloads: list[dict[str, Any]] = [
                {"action": "signal_crumpled_draft", "text": "secret draft"},
                {"action": "signal_crumpled_draft", "TEXT": "SECRET"},
                {"action": "signal_crumpled_draft", " text ": "padded secret"},
                {"action": "signal_crumpled_draft", "content": "secret content"},
                {"action": "signal_crumpled_draft", "body": "secret body"},
                {"action": "signal_crumpled_draft", "note": "secret note"},
                {"action": "signal_crumpled_draft", "note_cn": "私密草稿"},
                {"action": "signal_crumpled_draft", "draft": "unsent words"},
                {"action": "signal_crumpled_draft", "raw_text": "raw"},
                {"action": "signal_crumpled_draft", "message": "msg"},
                {"action": "signal_crumpled_draft", "snippet": "snip"},
                {"action": "signal_crumpled_draft", "quote": "quoted"},
                {"action": "signal_crumpled_draft", "summary": "sum"},
                {"action": "signal_crumpled_draft", "summary_cn": "摘要"},
                {"action": "signal_crumpled_draft", "title": "t"},
                {"action": "signal_crumpled_draft", "title_cn": "标题"},
                {"action": "signal_crumpled_draft", "html": "<p>hi</p>"},
                {"action": "signal_crumpled_draft", "markdown": "# hi"},
                {"action": "signal_crumpled_draft", "file": "draft.txt"},
                {"action": "signal_crumpled_draft", "data": "abc"},
                {"action": "signal_crumpled_draft", "base64": "YWJj"},
                {"action": "signal_crumpled_draft", "user_draft_copy": "hidden"},
                {"action": "signal_crumpled_draft", "memo_content_v2": "hidden"},
                {"action": "signal_crumpled_draft", "emailBody": "hidden"},
                {"action": "signal_crumpled_draft", "scratch_note_1": "hidden"},
                {"action": "signal_crumpled_draft", "plainText": "hidden"},
                # Nested inside dict, list, and deeply nested objects (must trigger BEFORE unknown-field check!)
                {"action": "signal_crumpled_draft", "meta": {"text": "nested secret"}},
                {"action": "signal_crumpled_draft", "meta": {"inner": {"Raw_Text": "deep secret"}}},
                {"action": "signal_crumpled_draft", "items": [{"draft_snippet": "list secret"}]},
                {"action": "signal_crumpled_draft", "wrap": [{"a": {"b": {"Note": "very deep"}}}]},
                {"action": "signal_crumpled_draft", "extra": {"base64": "c2VjcmV0"}},
                {"action": "signal_crumpled_draft", "extra": {"file": "secret.md"}},
            ]

            for payload in forbidden_payloads:
                code, body = craft_view.handle_v05_home_craft_action(
                    root,
                    payload,
                    has_subject_capability=False,
                    now_iso="2026-11-19T22:55:00+08:00",
                )
                self.assertEqual(code, 400, payload)
                self.assertFalse(body["ok"], payload)
                self.assertEqual(body["reason_code"], "RAW_TEXT_FORBIDDEN", payload)
                self.assertEqual(body["error_code"], "RAW_TEXT_FORBIDDEN", payload)
                self.assertEqual(body["llm_calls"], 0, payload)

            # ZERO disk writes occurred on untouched root!
            self.assertEqual(_snapshot_tree(root), before_empty)
            self.assertFalse((root / "world" / "v05_home_craft.json").exists())

            # Also verify zero disk mutation on an already-populated root!
            c_ok, _ = craft_view.handle_v05_home_craft_action(
                root,
                {"action": "signal_crumpled_draft", "disposal_kind": "wastebasket", "operation_key": "op_seed"},
                has_subject_capability=False,
                now_iso="2026-11-19T22:55:00+08:00",
            )
            self.assertEqual(c_ok, 200)
            snap_seeded = _snapshot_tree(root)

            c_rej, b_rej = craft_view.handle_v05_home_craft_action(
                root,
                {"action": "signal_crumpled_draft", "disposal_kind": "winter_hearth", "meta": {"content": "secret"}},
                has_subject_capability=False,
                now_iso="2026-11-19T22:58:00+08:00",
            )
            self.assertEqual(c_rej, 400)
            self.assertEqual(b_rej["reason_code"], "RAW_TEXT_FORBIDDEN")
            self.assertEqual(b_rej["error_code"], "RAW_TEXT_FORBIDDEN")
            self.assertEqual(_snapshot_tree(root), snap_seeded)

    def test_04_actor_side_lock_subject_forbidden_and_http_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v05-cds-actor-") as td:
            root = Path(td)
            before = _snapshot_tree(root)

            # 1) View with has_subject_capability=True -> 403 FORBIDDEN_ACTOR_SIDE, zero write
            c_sub, b_sub = craft_view.handle_v05_home_craft_action(
                root,
                {"action": "signal_crumpled_draft", "disposal_kind": "wastebasket"},
                has_subject_capability=True,
                now_iso="2026-11-19T22:55:00+08:00",
            )
            self.assertEqual(c_sub, 403)
            self.assertEqual(b_sub["reason_code"], "FORBIDDEN_ACTOR_SIDE")
            self.assertEqual(b_sub["error_code"], "FORBIDDEN_ACTOR_SIDE")

            # 2) mutate_v05_home_craft_view with actor_side="subject" / "her" -> 403 FORBIDDEN_ACTOR_SIDE
            for side in ("subject", "her"):
                c_m, b_m = craft_view.mutate_v05_home_craft_view(
                    root,
                    {"action": "signal_crumpled_draft", "disposal_kind": "winter_hearth"},
                    actor_side=side,
                )
                self.assertEqual(c_m, 403)
                self.assertEqual(b_m["reason_code"], "FORBIDDEN_ACTOR_SIDE")

            # 3) Direct engine call with author_side="subject" -> V05HomeCraftError(403, FORBIDDEN_ACTOR_SIDE)
            with self.assertRaises(craft_engine.V05HomeCraftError) as ctx_eng:
                craft_engine.signal_crumpled_draft(
                    root,
                    disposal_kind="wastebasket",
                    author_side="subject",
                )
            self.assertEqual(ctx_eng.exception.status_code, 403)
            self.assertEqual(ctx_eng.exception.reason_code, "FORBIDDEN_ACTOR_SIDE")
            self.assertEqual(_snapshot_tree(root), before)

            # 4) Real PhoneHTTPServer roundtrip (GET zero-write, POST 400 RAW_TEXT_FORBIDDEN,
            #    POST 403 FORBIDDEN_ACTOR_SIDE for subject cap, POST 200 partner + idempotent replay)
            inst = {
                "root": str(root),
                "subject": "chiyo.global",
                "world_home": str(root / "world"),
                "supply_socket": str(root / "run" / "supply.sock"),
                "private_items_artifact_id": "art_private_items",
                "artifact_grant_id": "grant_private_items",
                "workspace_id": "ws_default",
            }
            svc = phone_service.PhoneService(inst)
            csrf = "test-cds-csrf"
            subject_cap_token = "test_cds_subject_capability_token_0123456789"
            cap_obj = phone_capability.Capability(
                token=subject_cap_token,
                subject="chiyo.global",
                endpoint="http://127.0.0.1:0",
                issued_at="2026-11-19T22:50:00+08:00",
            )
            srv = phone_server.PhoneHTTPServer(
                ("127.0.0.1", 0),
                service=svc,
                csrf_token=csrf,
                capability=cap_obj,
            )
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                base = f"http://127.0.0.1:{port}"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                # GET zero-write
                before_http = _snapshot_tree(root)
                with opener.open(base + "/api/phone/v05/home-craft", timeout=15) as resp:
                    self.assertEqual(resp.status, 200)
                    get_doc = json.loads(resp.read().decode("utf-8"))
                    self.assertIsNone(get_doc["crumpled_draft_signal"])
                    self.assertTrue(get_doc["zero_write"])
                self.assertEqual(_snapshot_tree(root), before_http)

                # POST with raw text -> 400 RAW_TEXT_FORBIDDEN, zero write
                raw_text_req = urllib.request.Request(
                    base + "/api/phone/v05/home-craft",
                    data=json.dumps({
                        "action": "signal_crumpled_draft",
                        "disposal_kind": "wastebasket",
                        "nested": {"raw_text": "never store"},
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx_400:
                    opener.open(raw_text_req, timeout=5)
                self.assertEqual(ctx_400.exception.code, 400)
                err_400 = json.loads(ctx_400.exception.read().decode("utf-8"))
                self.assertEqual(err_400["reason_code"], "RAW_TEXT_FORBIDDEN")
                self.assertEqual(err_400["error_code"], "RAW_TEXT_FORBIDDEN")
                self.assertEqual(_snapshot_tree(root), before_http)

                # POST with subject capability -> 403 FORBIDDEN_ACTOR_SIDE, zero write
                subj_req = urllib.request.Request(
                    base + "/api/phone/v05/home-craft",
                    data=json.dumps({
                        "action": "signal_crumpled_draft",
                        "disposal_kind": "wastebasket",
                    }).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrf,
                        "X-Phone-Actor": "model",
                        "X-Phone-Capability": subject_cap_token,
                        "Authorization": f"Bearer {subject_cap_token}",
                    },
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx_403:
                    opener.open(subj_req, timeout=5)
                self.assertEqual(ctx_403.exception.code, 403)
                err_403 = json.loads(ctx_403.exception.read().decode("utf-8"))
                self.assertEqual(err_403["reason_code"], "FORBIDDEN_ACTOR_SIDE")
                self.assertEqual(_snapshot_tree(root), before_http)

                # POST partner valid -> 200 OK, count == 1; replay -> count still 1
                ok_payload = json.dumps({
                    "action": "signal_crumpled_draft",
                    "disposal_kind": "wastebasket",
                    "operation_key": "http_op_1",
                }).encode("utf-8")
                ok_req1 = urllib.request.Request(
                    base + "/api/phone/v05/home-craft",
                    data=ok_payload,
                    headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                    method="POST",
                )
                with opener.open(ok_req1, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    doc1 = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(doc1["crumpled_draft_signal"]["crumpled_count_today"], 1)
                    self.assertEqual(doc1["crumpled_draft_signal"]["disposal_kind"], "wastebasket")

                ok_req2 = urllib.request.Request(
                    base + "/api/phone/v05/home-craft",
                    data=ok_payload,
                    headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                    method="POST",
                )
                with opener.open(ok_req2, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    doc2 = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(doc2["crumpled_draft_signal"]["crumpled_count_today"], 1)
                    self.assertTrue(doc2["action_receipt"]["idempotent_replay"])
            finally:
                srv.shutdown()
                srv.server_close()


class TestV05CrumpledDraftPhoneToolAndBridge(unittest.TestCase):
    def test_01_phone_tool_partner_impersonation_refused_and_37_surfaces(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertIn(
            "signal_crumpled_draft",
            phone_tool._PARTNER_ONLY_SUBACTIONS["v04_elastic_play"],
        )
        ep_hint = phone_tool._surface_supported_hint("v04_elastic_play")
        self.assertIsNotNone(ep_hint)
        assert ep_hint is not None
        self.assertNotIn("signal_crumpled_draft", ep_hint["subject_actions"])

        for act_variant in ("signal_crumpled_draft", "SIGNAL_CRUMPLED_DRAFT"):
            with self.assertRaises(phone_tool.PhoneToolRefused) as ctx:
                phone_tool._prepare_life_update(
                    {
                        "target_surface": "v04_elastic_play",
                        "payload": {
                            "action": act_variant,
                            "disposal_kind": "wastebasket",
                        },
                    },
                    key="op_refuse_cds",
                )
            self.assertEqual(ctx.exception.reason_code, "PARTNER_IMPERSONATION_REFUSED")

    def test_02_lived_context_bridge_within_7200s_expired_midnight_utc_and_calm_gate(self) -> None:
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)

        with tempfile.TemporaryDirectory(prefix="v05-cds-lcb-") as td:
            root = Path(td)
            before_empty = _snapshot_tree(root)

            # 1) Empty root stays 100% calm silent & zero-write
            bd_empty = lcb.measure_context_budget_breakdown(
                root, cfg={}, incoming_text="你好", now_iso="2026-11-19T23:00:00+08:00"
            )
            self.assertTrue(bd_empty["calm_silent"])
            self.assertEqual(bd_empty["total_chars"], 0)
            self.assertEqual(_snapshot_tree(root), before_empty)

            # 2) Configured calm root with phone/ + null crumpled_draft_signal -> v05_town_spark_line == 0
            (root / "phone").mkdir(parents=True)
            cfg = {"day_root": str(root), "phone": {"enabled": False, "private_artifacts": True}}
            _write_json(
                root / "world" / "v05_home_craft.json",
                {
                    "schema_version": "world.v05.home_craft.v1",
                    "revision": 1,
                    "crumpled_draft_signal": None,
                },
            )
            before_null = _snapshot_tree(root)
            bd_null = lcb.measure_context_budget_breakdown(
                root, cfg=cfg, incoming_text="你好", now_iso="2026-11-19T23:00:00+08:00"
            )
            self.assertEqual(_snapshot_tree(root), before_null)
            self.assertEqual(bd_null["per_section_chars"].get("v05_town_spark_line", 0), 0)
            self.assertNotIn("废纸篓", bd_null["rendered_text"])
            self.assertNotIn("壁炉", bd_null["rendered_text"])

            # 3) Active wastebasket signal within 7200s (+300s and exact +7200s boundary)
            craft_engine.signal_crumpled_draft(
                root,
                disposal_kind="wastebasket",
                author_side="partner",
                operation_key="op_lcb_1",
                now_iso="2026-11-19T10:00:00+08:00",
            )
            before_wb = _snapshot_tree(root)
            ctx_wb = lcb.build_lived_turn_context(
                root, cfg=cfg, incoming_text="先歇会儿", now_iso="2026-11-19T10:05:00+08:00"
            )
            self.assertEqual(_snapshot_tree(root), before_wb)
            self.assertLessEqual(len(ctx_wb), lcb.MAX_LIVED_CONTEXT_CHARS)
            self.assertIn(WASTEBASKET_CUE, ctx_wb)
            self.assertNotIn(WINTER_HEARTH_CUE, ctx_wb)

            # Exact 7200s boundary -> still shown
            ctx_exact = lcb.build_lived_turn_context(
                root, cfg=cfg, incoming_text="先歇会儿", now_iso="2026-11-19T12:00:00+08:00"
            )
            self.assertIn(WASTEBASKET_CUE, ctx_exact)

            # 4) Expired (> 7200s, same day: +7201s) -> cue disappears cleanly, Calm Gate holds
            bd_exp = lcb.measure_context_budget_breakdown(
                root, cfg=cfg, incoming_text="先歇会儿", now_iso="2026-11-19T12:00:01+08:00"
            )
            self.assertEqual(_snapshot_tree(root), before_wb)
            self.assertNotIn(WASTEBASKET_CUE, bd_exp["rendered_text"])
            self.assertEqual(bd_exp["per_section_chars"].get("v05_town_spark_line", 0), 0)

            # 5) Future timestamp (negative age) -> cue absent
            bd_fut = lcb.measure_context_budget_breakdown(
                root, cfg=cfg, incoming_text="先歇会儿", now_iso="2026-11-19T09:59:00+08:00"
            )
            self.assertNotIn(WASTEBASKET_CUE, bd_fut["rendered_text"])

            # 6) Active winter_hearth signal -> hearth cue shown; next calendar day across midnight -> absent
            craft_engine.signal_crumpled_draft(
                root,
                disposal_kind="winter_hearth",
                author_side="partner",
                operation_key="op_lcb_2",
                now_iso="2026-11-19T23:30:00+08:00",
            )
            before_wh = _snapshot_tree(root)
            ctx_wh = lcb.build_lived_turn_context(
                root, cfg=cfg, incoming_text="夜里冷", now_iso="2026-11-19T23:45:00+08:00"
            )
            self.assertEqual(_snapshot_tree(root), before_wh)
            self.assertLessEqual(len(ctx_wh), lcb.MAX_LIVED_CONTEXT_CHARS)
            self.assertIn(WINTER_HEARTH_CUE, ctx_wh)
            self.assertNotIn(WASTEBASKET_CUE, ctx_wh)

            # 1 hour later, but next UTC+8 calendar day -> same-day gate suppresses cue
            ctx_next_day = lcb.build_lived_turn_context(
                root, cfg=cfg, incoming_text="夜里冷", now_iso="2026-11-20T00:30:00+08:00"
            )
            self.assertNotIn(WINTER_HEARTH_CUE, ctx_next_day)

            # 7) UTC "Z" midnight consistency (2026-11-19T16:30:00Z -> 2026-11-20T00:30:00+08:00)
            craft_engine.signal_crumpled_draft(
                root,
                disposal_kind="wastebasket",
                author_side="partner",
                operation_key="op_lcb_utc",
                now_iso="2026-11-19T16:30:00Z",
            )
            before_utc = _snapshot_tree(root)
            ctx_utc = lcb.build_lived_turn_context(
                root, cfg=cfg, incoming_text="刚整理完", now_iso="2026-11-19T17:15:00Z"
            )
            self.assertEqual(_snapshot_tree(root), before_utc)
            self.assertIn(WASTEBASKET_CUE, ctx_utc)

            # 8) Formatter stays None when state_exists is explicitly False
            self.assertIsNone(
                lcb._format_v05_living_town_and_spark(
                    None,
                    None,
                    home_craft_doc={
                        "state_exists": False,
                        "crumpled_draft_signal": {
                            "last_crumpled_at": "2026-11-19T22:55:00+08:00",
                            "disposal_kind": "wastebasket",
                            "crumpled_count_today": 1,
                            "date": "2026-11-19",
                        },
                    },
                    now_dt=season_pulse.parse_iso_to_default_tz("2026-11-19T23:00:00+08:00"),
                )
            )

    def test_03_file_hygiene_all_five_owned_files(self) -> None:
        owned_files = (
            REPO_ROOT / "components" / "world" / "scripts" / "v05_home_craft_engine.py",
            REPO_ROOT / "components" / "phone" / "v05_home_craft_view.py",
            REPO_ROOT / "chiyo_bundle" / "phone_tool.py",
            REPO_ROOT / "chiyo_bundle" / "lived_context_bridge.py",
            Path(__file__).resolve(),
        )
        strict_neutral = {
            "v05_home_craft_engine.py",
            "v05_home_craft_view.py",
            "test_v05_crumpled_draft_seam.py",
        }
        for path in owned_files:
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM in {path}")
            self.assertNotIn(b"\r\n", raw, f"CRLF found in {path}")
            text = raw.decode("utf-8")
            emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"unicode.So emoji in {path}: {emojis[:5]}")
            if path.name in strict_neutral:
                self.assertNotIn("\u5979", text, f"Non-neutral pronoun in {path}")
                self.assertNotIn("\u5343\u4ee3", text, f"Hardcoded name in {path}")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Regression & concurrency test suite for v0.8 live P0/P1/P2 backend fixes (#273, #274).

Covers:
1. [P0-2 & P0-3] Private desire leak & forget-barrier bypass on GET /api/phone/v03/clock
   and direct ``everyday_desire_lifecycle.project_everyday_desires`` (100% zero-write).
2. [P0-4] File locking via ``state_tx_guard.state_file_lock`` in
   ``v03_economy_illness_bridge``, ``outbound_outbox_view``, and ``v05_home_craft_engine``:
   - 10 concurrent distinct ``offer_stipend`` (across distinct dates -> 10/10 persisted;
     same day -> exact daily cap 3/10 applied + 7/10 409, zero lost updates);
   - 10 concurrent same-``operation_key`` ``offer_item_gift`` -> 1 APPLIED + 9 DUPLICATE;
   - 10 concurrent ``POST /api/phone/outbound/ack`` ``claim`` (distinct keys) on one
     ``APPROVED_FOR_DELIVERY`` item -> 1 HTTP 200 + 9 HTTP 409 conflict;
   - 10 concurrent ``POST /api/phone/outbound/ack`` ``ack_delivered`` (distinct keys) ->
     1 HTTP 200 + 9 HTTP 409 conflict, ``unanswered_streak == 1``;
   - 10 concurrent ``v05_home_craft_engine.log_object_patina_or_repair`` -> 10/10 persisted.
3. [P0-1 Backend] ``PhoneHTTPServer.request_queue_size >= 64`` (128) & 30-thread burst.
4. [P1-2] ``V05InnerSparkError`` argument order in ``v05_dreams_desires_view`` (clean 400/403
   JSON, zero Python ``TypeError`` leakage).
5. [P1-5] Deterministic outreach ``expires_at`` TTLs + stale 22h rain ping excluded on
   sunny/dry morning and on age > 6h in ``GET /api/phone/outbound/pending`` (zero-write).
6. [P2-3] Safe stale ``supply-serve-*.sock`` cleanup in ``scripts/nyairo_day.py`` (never
   unlinking live PIDs, active listeners, or non-sockets).
7. Hygiene (UTF-8 no BOM, pure LF, zero ``unicode.So``) & frozen invariants (37/1199/1800/24).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import unicodedata
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(REPO_ROOT),
    str(REPO_ROOT / "components"),
    str(REPO_ROOT / "components" / "life" / "scripts"),
    str(REPO_ROOT / "components" / "world" / "scripts"),
    str(REPO_ROOT / "components" / "phone"),
    str(REPO_ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from chiyo_bundle import lived_context_bridge  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import everyday_desire_lifecycle as desire_lc  # noqa: E402
from components.life.scripts import proactive_outreach_producer as outreach_prod  # noqa: E402
from components.life.scripts import v02_representation_lease_forget as rlf  # noqa: E402
from components.life.scripts import v03_clock_evaporation as v03_clock  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import outbound_outbox_view as outbox_view  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import v05_dreams_desires_view as spark_view  # noqa: E402
from components.world.scripts import v03_economy_illness_bridge as eco_bridge  # noqa: E402
from components.world.scripts import v04_neighborhood_living as v04_nb  # noqa: E402
from components.world.scripts import v05_home_craft_engine as craft_engine  # noqa: E402
from scripts import nyairo_day  # noqa: E402
from scripts import v02_wall_clock_soak as soak_runner  # noqa: E402

TZ_BJT = timezone(timedelta(hours=8))
T_MORNING = datetime(2026, 11, 21, 9, 45, 0, tzinfo=TZ_BJT)
T_MORNING_ISO = T_MORNING.isoformat(timespec="seconds")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return snap


def _make_instance(root: Path) -> dict[str, Any]:
    return {
        "root": str(root),
        "subject": "chiyo.global",
        "world_home": str(root / "one-day" / "world-home"),
        "supply_socket": str(root / "run" / "supply.sock"),
        "private_items_artifact_id": "art_private_items",
        "artifact_grant_id": "grant_private_items",
        "workspace_id": "ws_default",
    }


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
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"raw": raw}
        return int(err.code), parsed


def _load_chiyo_plugin_module() -> Any:
    plugin_path = REPO_ROOT / "plugins" / "chiyo" / "__init__.py"
    spec = importlib.util.spec_from_file_location("v08_p0_plugin_check", plugin_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _LoopbackHarness:
    def __init__(
        self,
        root: Path,
        *,
        world_clock: Any = None,
        with_capability: bool = True,
        csrf_token: str = "v08-p0-live-fix-csrf",
    ) -> None:
        self.root = root
        self.csrf_token = csrf_token
        self.svc = phone_service.PhoneService(_make_instance(root), world_clock=world_clock)
        self.capability_token: Optional[str] = None
        cap_obj = None
        if with_capability:
            self.capability_token = "v08-p0-subject-cap-token-0123456789abcdef"
            cap_obj = phone_capability.Capability(
                token=self.capability_token,
                subject="chiyo.global",
                endpoint="http://127.0.0.1:0",
                issued_at="2026-11-21T09:00:00Z",
            )
        self.srv = phone_server.PhoneHTTPServer(
            ("127.0.0.1", 0),
            service=self.svc,
            csrf_token=csrf_token,
            capability=cap_obj,
        )
        self.port = self.srv.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self) -> "_LoopbackHarness":
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.srv.shutdown()
        self.srv.server_close()


class TestP0PrivacyAndForgetBarrierOnV03Clock(unittest.TestCase):
    def test_01_private_desire_hidden_and_forgotten_desire_never_resurrects_on_v03_clock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v08-p0-priv-forget-") as td:
            root = Path(td)
            # Ensure everyday_desires.json exists so inner-spark sync mirrors into it
            desire_lc.apply_everyday_desire_action(
                root,
                {
                    "action": "emerge_concern",
                    "concern_ref": "c_public_tea",
                    "domain": "comfort",
                    "origin_kind": "body_need",
                    "origin_refs": ["茶杯空了"],
                    "expression": "想泡一杯热焙茶",
                    "persistence": "retained",
                    "operation_key": "op_pub_tea",
                },
                now_iso=T_MORNING_ISO,
                channel_visibility="subject_private",
            )

            # Seed one private subject desire (shared=False) and one shared desire (shared=True) via inner-spark
            c_priv, d_priv = spark_view.handle_v05_inner_spark_action(
                root,
                {
                    "action": "record_subject_desire",
                    "layer": "mid_itch",
                    "text": "私密念想 CANARY_PRIV_DESIRE_SPARK_91827",
                    "origin": "self_emerged",
                    "shared": False,
                    "operation_key": "priv_desire_spark_1",
                },
                has_subject_capability=True,
                now_iso=T_MORNING_ISO,
            )
            self.assertEqual(c_priv, 200, d_priv)

            c_pub, d_pub = spark_view.handle_v05_inner_spark_action(
                root,
                {
                    "action": "record_subject_desire",
                    "layer": "mid_itch",
                    "text": "想再听一次 CANARY_FORGET_RESURRECT_91827 唱片",
                    "origin": "self_emerged",
                    "shared": True,
                    "operation_key": "pub_desire_spark_1",
                },
                has_subject_capability=True,
                now_iso=T_MORNING_ISO,
            )
            self.assertEqual(c_pub, 200, d_pub)
            shared_desire_id = str(d_pub["data"]["action_receipt"]["desire"]["desire_id"])

            # Also inject private=True and visibility='private' rows directly into everyday_desires.json
            ed_path = desire_lc.everyday_desire_state_path(root)
            ed_doc = json.loads(ed_path.read_text(encoding="utf-8"))
            ed_doc["retained_concerns"].append({
                "concern_ref": "c_private_flag",
                "desire_id": "des_priv_flag",
                "domain": "exploration",
                "origin_kind": "sustained_interest",
                "origin_refs": ["手账本"],
                "expression": "CANARY_PRIVATE_BOOL_FLAG_55112",
                "persistence": "retained",
                "status": "ACTIVE",
                "private": True,
            })
            ed_doc["retained_concerns"].append({
                "concern_ref": "c_visibility_private",
                "desire_id": "des_vis_priv",
                "domain": "creation",
                "origin_kind": "sustained_interest",
                "origin_refs": ["素描本"],
                "expression": "CANARY_VISIBILITY_PRIVATE_55113",
                "persistence": "retained",
                "status": "ACTIVE",
                "visibility": "private",
            })
            ed_doc["turn_only_buffer"].append({
                "concern_ref": "c_turn_priv",
                "desire_id": "des_turn_priv",
                "domain": "comfort",
                "origin_kind": "body_need",
                "origin_refs": ["窗边"],
                "expression": "CANARY_TURN_ONLY_PRIVATE_55114",
                "persistence": "turn_only",
                "status": "ACTIVE",
                "born_turn": int(ed_doc.get("turn_index") or 0),
                "scene_id": str(ed_doc.get("current_scene_id") or "study_desk"),
                "created_at": T_MORNING_ISO,
                "ttl_seconds": 900,
                "shared": False,
            })
            ed_path.write_text(json.dumps(ed_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

            with _LoopbackHarness(root, world_clock=lambda: T_MORNING, with_capability=True) as h:
                # 1) Unauthenticated partner GET /api/phone/v03/clock: zero private leaks & 100% zero-write
                snap_before = _snapshot_tree(root)
                code_partner, body_partner = _http_json(h.base_url + "/api/phone/v03/clock")
                self.assertEqual(code_partner, 200)
                self.assertEqual(snap_before, _snapshot_tree(root))
                partner_blob = json.dumps(body_partner, ensure_ascii=False)
                for canary in (
                    "CANARY_PRIV_DESIRE_SPARK_91827",
                    "CANARY_PRIVATE_BOOL_FLAG_55112",
                    "CANARY_VISIBILITY_PRIVATE_55113",
                    "CANARY_TURN_ONLY_PRIVATE_55114",
                ):
                    self.assertNotIn(canary, partner_blob)
                # Shared desire is visible before forget
                self.assertIn("CANARY_FORGET_RESURRECT_91827", partner_blob)

                # Subject view still sees TA's own private desires
                code_subj, body_subj = _http_json(
                    h.base_url + "/api/phone/v03/clock",
                    headers={"X-Phone-Capability": h.capability_token or ""},
                )
                self.assertEqual(code_subj, 200)
                self.assertEqual(snap_before, _snapshot_tree(root))
                self.assertIn("CANARY_PRIV_DESIRE_SPARK_91827", json.dumps(body_subj, ensure_ascii=False))

                # 2) Execute explicit forget on shared_desire_id -> must vanish on GET /api/phone/v03/clock
                forget_code, forget_res = rlf.submit_forget_command(
                    root,
                    command_text="删掉记录和相关记忆",
                    operation_id="op_forget_desire_v08_1",
                    explicit_scope=[shared_desire_id],
                    explicit_confirmation=True,
                    now_iso=T_MORNING_ISO,
                )
                self.assertEqual(forget_code, 200, forget_res)
                self.assertTrue(forget_res.get("ok"), forget_res)

                snap_after_forget = _snapshot_tree(root)
                code_p2, body_p2 = _http_json(h.base_url + "/api/phone/v03/clock")
                code_s2, body_s2 = _http_json(
                    h.base_url + "/api/phone/v03/clock",
                    headers={"X-Phone-Capability": h.capability_token or ""},
                )
                self.assertEqual(code_p2, 200)
                self.assertEqual(code_s2, 200)
                self.assertEqual(snap_after_forget, _snapshot_tree(root))
                self.assertNotIn("CANARY_FORGET_RESURRECT_91827", json.dumps(body_p2, ensure_ascii=False))
                self.assertNotIn("CANARY_FORGET_RESURRECT_91827", json.dumps(body_s2, ensure_ascii=False))
                # Unforgotten shared concern remains intact
                self.assertIn("想泡一杯热焙茶", json.dumps(body_p2, ensure_ascii=False))

            # 3) Fail-closed forget barrier returns empty lists with zero disk writes
            snap_fc = _snapshot_tree(root)
            fc_proj = desire_lc.project_everyday_desires(
                root,
                now_iso=T_MORNING_ISO,
                channel_visibility="public_or_shared",
                forget_barrier={"fail_closed": True, "barrier_active": True},
            )
            self.assertEqual(snap_fc, _snapshot_tree(root))
            self.assertEqual(fc_proj["retained_concerns"], [])
            self.assertEqual(fc_proj["turn_only_concerns"], [])
            self.assertEqual(fc_proj["auto_subsided_concerns"], [])
            self.assertIsNone(fc_proj["last_arbitration"])


class TestP0ConcurrencyAndLocking(unittest.TestCase):
    def test_01_ten_concurrent_distinct_offer_stipend_all_persisted_and_same_day_cap_serialized(self) -> None:
        # Part A: 10 distinct wall days -> 10/10 APPLIED & 10/10 persisted on disk (zero lost updates)
        with tempfile.TemporaryDirectory(prefix="v08-p0-stipend-10d-") as td:
            root = Path(td)
            barrier = threading.Barrier(10)

            def _worker(idx: int) -> dict[str, Any]:
                day_dt = T_MORNING + timedelta(days=idx)
                barrier.wait(timeout=10)
                return eco_bridge.offer_stipend(
                    root,
                    amount_cents=500,
                    note=f"零用支援 #{idx}",
                    operation_key=f"conc_stip_dist_{idx:02d}",
                    now_iso=day_dt.isoformat(timespec="seconds"),
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(_worker, range(10)))

            self.assertEqual(len(results), 10)
            self.assertTrue(all(r.get("command_status") == "APPLIED" for r in results))
            state, err = eco_bridge.read_stored_ecology_zero_write(root)
            self.assertIsNone(err)
            escrows = state["economy"]["stipend_escrow"]
            self.assertEqual(len(escrows), 10)
            persisted_keys = {v.get("operation_key") for v in escrows.values()}
            self.assertEqual(persisted_keys, {f"conc_stip_dist_{i:02d}" for i in range(10)})

        # Part B: 10 distinct keys on SAME day -> exactly DAILY_STIPEND_MAX_OFFERS (3) succeed + 7 fail 409,
        # and all 3/3 persisted on disk (serialized cap check + zero lost updates)
        with tempfile.TemporaryDirectory(prefix="v08-p0-stipend-sameday-") as td:
            root = Path(td)
            barrier = threading.Barrier(10)

            def _worker_same_day(idx: int) -> tuple[int, str]:
                barrier.wait(timeout=10)
                try:
                    res = eco_bridge.offer_stipend(
                        root,
                        amount_cents=500,
                        note=f"同日支援 #{idx}",
                        operation_key=f"conc_stip_same_day_{idx:02d}",
                        now_iso=T_MORNING_ISO,
                    )
                    return 200, str(res.get("command_status"))
                except eco_bridge.V03ConflictError as exc:
                    return exc.status_code, exc.reason_code

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                outcomes = list(pool.map(_worker_same_day, range(10)))

            ok_cnt = sum(1 for code, _ in outcomes if code == 200)
            cap_cnt = sum(1 for code, reason in outcomes if code == 409 and reason == "STIPEND_CAP_EXCEEDED")
            self.assertEqual(ok_cnt, eco_bridge.DAILY_STIPEND_MAX_OFFERS)
            self.assertEqual(cap_cnt, 10 - eco_bridge.DAILY_STIPEND_MAX_OFFERS)
            state, err = eco_bridge.read_stored_ecology_zero_write(root)
            self.assertIsNone(err)
            self.assertEqual(len(state["economy"]["stipend_escrow"]), eco_bridge.DAILY_STIPEND_MAX_OFFERS)

    def test_02_ten_concurrent_same_key_offer_item_gift_one_applied_nine_duplicate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v08-p0-gift-idem-") as td:
            root = Path(td)
            barrier = threading.Barrier(10)

            def _worker(_idx: int) -> str:
                barrier.wait(timeout=10)
                res = eco_bridge.offer_item_gift(
                    root,
                    item_label_cn="黄铜书签",
                    item_kind="keepsake",
                    estimated_jpy=800,
                    operation_key="conc_gift_same_op_1",
                    now_iso=T_MORNING_ISO,
                )
                return str(res.get("command_status"))

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                statuses = list(pool.map(_worker, range(10)))

            self.assertEqual(statuses.count("APPLIED"), 1, statuses)
            self.assertEqual(statuses.count("DUPLICATE"), 9, statuses)
            state, err = eco_bridge.read_stored_ecology_zero_write(root)
            self.assertIsNone(err)
            self.assertEqual(len(state["economy"]["item_gift_escrow"]), 1)

    def test_03_ten_concurrent_http_outbound_ack_claim_and_deliver_single_winner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v08-p0-ack-race-") as td:
            root = Path(td)
            # Enqueue two honest_outbox items (on two days to respect 48h spacing)
            r1 = eco_bridge.attempt_proactive_outbound(
                root,
                text="窗边阳光很好，顺手给 TA 记一笔。",
                channel="desktop_note",
                reason_kind="genuine_life_share",
                delivery_mode="honest_outbox",
                operation_key="op_race_out_1",
                now_iso=(T_MORNING - timedelta(days=3)).isoformat(timespec="seconds"),
            )
            out_id_claim = str(r1["receipt"]["outbound_id"])

            r2 = eco_bridge.attempt_proactive_outbound(
                root,
                text="刚泡好一壶乌龙茶，窗外风很轻。",
                channel="desktop_note",
                reason_kind="genuine_life_share",
                delivery_mode="honest_outbox",
                operation_key="op_race_out_2",
                now_iso=T_MORNING_ISO,
            )
            out_id_deliv = str(r2["receipt"]["outbound_id"])

            with _LoopbackHarness(root, world_clock=lambda: T_MORNING, with_capability=True) as h:
                url = h.base_url + "/api/phone/outbound/ack"
                headers = {"X-CSRF-Token": h.csrf_token}

                # 1) 10 concurrent DISTINCT-key claims on out_id_claim -> 1 HTTP 200 + 9 HTTP 409
                barrier_claim = threading.Barrier(10)

                def _claim_worker(idx: int) -> tuple[int, dict[str, Any]]:
                    barrier_claim.wait(timeout=10)
                    return _http_json(
                        url,
                        method="POST",
                        headers=headers,
                        body={
                            "action": "claim",
                            "item_id": out_id_claim,
                            "item_kind": "proactive_outreach",
                            "operation_key": f"race_claim_key_{idx:02d}",
                            "transport_receipt_id": f"tr_claim_{idx:02d}",
                        },
                    )

                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                    claim_results = list(pool.map(_claim_worker, range(10)))

                claim_codes = [c for c, _ in claim_results]
                self.assertEqual(claim_codes.count(200), 1, claim_results)
                self.assertEqual(claim_codes.count(409), 9, claim_results)

                # 2) 10 concurrent DISTINCT-key ack_delivered on out_id_deliv -> 1 HTTP 200 + 9 HTTP 409
                barrier_deliv = threading.Barrier(10)

                def _deliv_worker(idx: int) -> tuple[int, dict[str, Any]]:
                    barrier_deliv.wait(timeout=10)
                    return _http_json(
                        url,
                        method="POST",
                        headers=headers,
                        body={
                            "action": "ack_delivered",
                            "item_id": out_id_deliv,
                            "item_kind": "proactive_outreach",
                            "operation_key": f"race_deliv_key_{idx:02d}",
                            "transport_receipt_id": f"tr_deliv_{idx:02d}",
                        },
                    )

                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                    deliv_results = list(pool.map(_deliv_worker, range(10)))

                deliv_codes = [c for c, _ in deliv_results]
                self.assertEqual(deliv_codes.count(200), 1, deliv_results)
                self.assertEqual(deliv_codes.count(409), 9, deliv_results)

                state, err = eco_bridge.read_stored_ecology_zero_write(root)
                self.assertIsNone(err)
                self.assertEqual(int(state["reality_bridge"]["unanswered_streak"]), 1)

    def test_04_ten_concurrent_home_craft_patina_logs_all_persisted_and_read_zero_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v08-p0-craft-lock-") as td:
            root = Path(td)
            # Zero-write read before any write
            snap0 = _snapshot_tree(root)
            snap_read = craft_engine.read_home_craft_snapshot(root, now_iso=T_MORNING_ISO)
            self.assertTrue(snap_read["zero_write"])
            self.assertEqual(snap0, _snapshot_tree(root))

            barrier = threading.Barrier(10)

            def _craft_worker(idx: int) -> dict[str, Any]:
                barrier.wait(timeout=10)
                return craft_engine.log_object_patina_or_repair(
                    root,
                    object_id="ceramic_mug",
                    event_kind="patina",
                    note_cn=f"并行记录茶杯温润痕迹 #{idx}",
                    author_side="partner",
                    operation_key=f"craft_conc_{idx:02d}",
                    now_iso=T_MORNING_ISO,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                craft_results = list(pool.map(_craft_worker, range(10)))

            self.assertEqual(len(craft_results), 10)
            self.assertTrue(all(r.get("ok") for r in craft_results))

            snap_after_writes = _snapshot_tree(root)
            after_view = craft_engine.read_home_craft_snapshot(root, now_iso=T_MORNING_ISO)
            self.assertEqual(snap_after_writes, _snapshot_tree(root))
            mug = next(o for o in after_view["object_patina"] if o["object_id"] == "ceramic_mug")
            notes_text = {n["note_cn"] for n in mug["patina_notes"]}
            for idx in range(10):
                self.assertIn(f"并行记录茶杯温润痕迹 #{idx}", notes_text)


class TestP0ServerBacklogAndP1SparkErrors(unittest.TestCase):
    def test_01_phone_http_server_backlog_and_concurrent_burst(self) -> None:
        self.assertGreaterEqual(phone_server.PhoneHTTPServer.request_queue_size, 64)
        self.assertEqual(phone_server.PhoneHTTPServer.request_queue_size, 128)

        with tempfile.TemporaryDirectory(prefix="v08-p0-burst-") as td:
            root = Path(td)
            with _LoopbackHarness(root, world_clock=lambda: T_MORNING, with_capability=False) as h:
                n_clients = 30
                barrier = threading.Barrier(n_clients)

                def _hit(_idx: int) -> int:
                    barrier.wait(timeout=10)
                    code, _ = _http_json(h.base_url + "/api/phone/outbound/pending")
                    return code

                with concurrent.futures.ThreadPoolExecutor(max_workers=n_clients) as pool:
                    codes = list(pool.map(_hit, range(n_clients)))
                self.assertEqual(codes, [200] * n_clients)

    def test_02_inner_spark_validation_errors_clean_400_403_no_typeerror(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v08-p1-spark-err-") as td:
            root = Path(td)
            bad_payloads = [
                {"action": "nonexistent_action"},
                {"action": "sketch_window_frost", "text": "hi", "bogus_field": 1},
                {"action": "sketch_window_frost", "text": "hi", "actor_side": "subject"},
                {"action": ""},
                {" action": "sketch_window_frost"},
                {"action": "sketch_window_frost", "text": "hi", "strokes": [[[[[[["too_deep"]]]]]]]},
            ]
            for payload in bad_payloads:
                with self.subTest(payload=payload):
                    code, body = spark_view.handle_v05_inner_spark_action(
                        root,
                        payload,
                        has_subject_capability=False,
                        now_iso=T_MORNING_ISO,
                    )
                    self.assertEqual(code, 400, body)
                    self.assertFalse(body["ok"])
                    self.assertEqual(body["reason_code"], "INVALID_FIELDS")
                    detail = str(body.get("detail") or "")
                    self.assertNotIn("multiple values", detail)
                    self.assertNotIn("TypeError", detail)

            # Subject-only action rejected cleanly with 403 FORBIDDEN_ACTOR_SIDE
            code_forb, body_forb = spark_view.handle_v05_inner_spark_action(
                root,
                {
                    "action": "record_subject_desire",
                    "layer": "mid_itch",
                    "text": "伴侣越权测试",
                },
                has_subject_capability=False,
                now_iso=T_MORNING_ISO,
            )
            self.assertEqual(code_forb, 403, body_forb)
            self.assertEqual(body_forb["reason_code"], "FORBIDDEN_ACTOR_SIDE")


class TestP1StaleWeatherOutreachExpiry(unittest.TestCase):
    def test_01_producer_sets_deterministic_ttls_and_outbox_expires_on_ttl(self) -> None:
        cases = [
            (
                "sudden_weather_care",
                {"weather": "rain", "previous_weather": "clear", "sudden_shift": True, "weather_slot_key": "s1"},
                4,
            ),
            (
                "vivid_morning_dream",
                {"dream_id": "drm_ttl_1", "fragment_cn": "梦见窗外渡口漂着纸船", "vivid": True},
                12,
            ),
            (
                "rare_town_spectacle",
                {"spectacle_id": "sp_ttl_1", "title_cn": "晚霞长虹", "description_cn": "整片海湾泛出暖金色"},
                12,
            ),
            (
                "finished_craft_work",
                {"work_id": "wk_ttl_1", "title": "木头书立", "completed": True},
                48,
            ),
            (
                "matured_missing_note",
                {"note_id": "mn_ttl_1", "summary": "旧书摊翻到海图明信片", "source_domain": "daily_life"},
                48,
            ),
        ]
        t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=TZ_BJT)
        t0_iso = t0.isoformat(timespec="seconds")

        for trigger_kind, facts, expected_hours in cases:
            with self.subTest(trigger_kind=trigger_kind):
                with tempfile.TemporaryDirectory(prefix=f"v08-ttl-{trigger_kind}-") as td:
                    root = Path(td)
                    res = outreach_prod.evaluate_and_enqueue_lived_outreach(
                        root,
                        trigger_kind=trigger_kind,
                        context_facts=facts,
                        now_iso=t0_iso,
                    )
                    self.assertTrue(res["enqueued"], res)
                    expected_exp = (t0 + timedelta(hours=expected_hours)).isoformat(timespec="seconds")
                    self.assertEqual(res["expires_at"], expected_exp)

                    # Still visible before TTL
                    before_iso = (t0 + timedelta(hours=expected_hours) - timedelta(minutes=5)).isoformat(timespec="seconds")
                    snap_before = _snapshot_tree(root)
                    p_before = outbox_view.read_pending_outbound_outbox_zero_write(root, now_iso=before_iso)
                    self.assertEqual(snap_before, _snapshot_tree(root))
                    self.assertEqual(p_before["pending_count"], 1)
                    self.assertEqual(p_before["items"][0]["expires_at"], expected_exp)

                    # Excluded at/after TTL (100% zero-write)
                    after_iso = (t0 + timedelta(hours=expected_hours, minutes=1)).isoformat(timespec="seconds")
                    p_after = outbox_view.read_pending_outbound_outbox_zero_write(root, now_iso=after_iso)
                    self.assertEqual(snap_before, _snapshot_tree(root))
                    self.assertEqual(p_after["pending_count"], 0)
                    self.assertEqual(p_after["items"], [])

    def test_02_stale_22h_legacy_rain_ping_excluded_on_sunny_morning_and_age_gt_6h_zero_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v08-stale-rain-") as td:
            root = Path(td)
            # Seed live-like legacy 22h old rain ping (expires_at=None, reason_kind=genuine_life_share + outreach_weather_)
            # AND a second legacy item with reason_kind='sudden_weather_care' and expires_at=None.
            eco = eco_bridge.default_ecology_state()
            eco["revision"] = 4
            eco["updated_at"] = "2026-11-20T12:00:41+00:00"
            eco["reality_bridge"]["outbound_records"] = [
                {
                    "outbound_id": "out_1125f18739066d6d",
                    "operation_key": "outreach_weather_legacy_22h",
                    "channel": "desktop_note",
                    "reason_kind": "genuine_life_share",
                    "text": "外头突然落雨起风了，出门的话记得顺手带把伞。",
                    "status": "APPROVED_FOR_DELIVERY",
                    "ack_state": "QUEUED_FOR_TRANSPORT",
                    "created_at": "2026-11-20T12:00:41Z",
                    "wall_day": "2026-11-20",
                    "delivered_at": None,
                    "expires_at": None,
                },
                {
                    "outbound_id": "out_fresh_weather_legacy_1h",
                    "operation_key": "outreach_weather_fresh_1h",
                    "channel": "desktop_note",
                    "reason_kind": "sudden_weather_care",
                    "text": "外头突然飘起细雨，出门顺手留意外套别淋潮。",
                    "status": "APPROVED_FOR_DELIVERY",
                    "ack_state": "QUEUED_FOR_TRANSPORT",
                    "created_at": "2026-11-21T08:45:00+08:00",
                    "wall_day": "2026-11-21",
                    "delivered_at": None,
                    "expires_at": None,
                },
            ]
            eco_path = eco_bridge.ecology_state_path(root)
            eco_path.parent.mkdir(parents=True, exist_ok=True)
            eco_path.write_text(json.dumps(eco, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

            # 1) Without v04_neighborhood: 22h old rain ping is excluded (> 6h), 1h old weather ping remains visible
            snap_1 = _snapshot_tree(root)
            pending_no_nb = outbox_view.read_pending_outbound_outbox_zero_write(root, now_iso=T_MORNING_ISO)
            self.assertEqual(snap_1, _snapshot_tree(root))
            self.assertEqual([it["item_id"] for it in pending_no_nb["items"]], ["out_fresh_weather_legacy_1h"])

            # 2) With v04_neighborhood weather='rain': fresh 1h ping stays visible, 22h stale ping still excluded
            v04_nb.apply_v04_neighborhood_command(
                root,
                {"action": "set_local_weather", "weather": "rain", "airflow": "strong", "operation_key": "op_w_rain"},
                actor_side="subject",
                now_iso=T_MORNING_ISO,
            )
            snap_rain = _snapshot_tree(root)
            pending_rain = outbox_view.read_pending_outbound_outbox_zero_write(root, now_iso=T_MORNING_ISO)
            self.assertEqual(snap_rain, _snapshot_tree(root))
            self.assertEqual([it["item_id"] for it in pending_rain["items"]], ["out_fresh_weather_legacy_1h"])

            # 3) Sunny dry morning (v04_neighborhood weather='clear', dry ground): BOTH rain pings excluded on HTTP GET
            v04_nb.apply_v04_neighborhood_command(
                root,
                {"action": "set_local_weather", "weather": "clear", "airflow": "weak", "operation_key": "op_w_clear"},
                actor_side="subject",
                now_iso=T_MORNING_ISO,
            )
            with _LoopbackHarness(root, world_clock=lambda: T_MORNING, with_capability=False) as h:
                snap_clear = _snapshot_tree(root)
                code, doc = _http_json(h.base_url + "/api/phone/outbound/pending")
                self.assertEqual(code, 200)
                self.assertEqual(snap_clear, _snapshot_tree(root))
                self.assertEqual(doc["data"]["pending_count"], 0)
                self.assertEqual(doc["data"]["items"], [])


class TestP2OrphanSupplySocketCleanup(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix" and hasattr(socket, "AF_UNIX"), "UNIX sockets require POSIX")
    def test_01_cleanup_removes_only_dead_orphan_supply_serve_sockets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v08-sock-clean-", dir="/tmp") as td:
            root = Path(td)
            run_dir = root / "run"
            run_dir.mkdir(parents=True, exist_ok=True)

            # 1) Stale orphan socket (dead PID, closed listener) -> MUST be unlinked
            stale_sock = run_dir / "supply-serve-1700000000-999999.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s_dead:
                s_dead.bind(str(stale_sock))

            # 2) Socket with LIVE embedded PID (our own PID) -> MUST NOT be unlinked
            live_pid_sock = run_dir / f"supply-serve-1700000001-{os.getpid()}.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s_live_pid:
                s_live_pid.bind(str(live_pid_sock))

            # 3) Socket with dead PID but ACTIVE listener -> MUST NOT be unlinked
            listening_sock = run_dir / "supply-serve-1700000002-999998.sock"
            s_listen = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s_listen.bind(str(listening_sock))
            s_listen.listen(2)

            # 4) Regular non-socket file matching name -> MUST NOT be unlinked
            regular_file = run_dir / "supply-serve-1700000003-999997.sock"
            regular_file.write_text("not a socket\n", encoding="utf-8", newline="\n")

            # 5) Other socket name (gateway.sock) -> MUST NOT be unlinked
            gateway_sock = run_dir / "gateway.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s_gw:
                s_gw.bind(str(gateway_sock))

            try:
                removed = nyairo_day._cleanup_stale_supply_sockets(root)
                self.assertEqual(removed, [stale_sock.name])
                self.assertFalse(stale_sock.exists())
                self.assertTrue(live_pid_sock.exists())
                self.assertTrue(listening_sock.exists())
                self.assertTrue(regular_file.exists())
                self.assertTrue(gateway_sock.exists())

                # Verify cmd_stop dead-PID path also cleans stale sockets
                stale_sock_2 = run_dir / "supply-serve-1700000004-999996.sock"
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s_dead2:
                    s_dead2.bind(str(stale_sock_2))
                nyairo_day._write_json(
                    nyairo_day._record_path(root),
                    {"schema": nyairo_day.SCHEMA_RECORD, "pid": 999996, "port": 3999, "root": str(root), "status": "RUNNING"},
                )
                rc = nyairo_day.cmd_stop(argparse.Namespace(root=str(root), json=True))
                self.assertEqual(rc, nyairo_day.EXIT_OK)
                self.assertFalse(stale_sock_2.exists())
                self.assertTrue(live_pid_sock.exists())
                self.assertTrue(listening_sock.exists())
            finally:
                s_listen.close()


class TestHygieneAndFrozenInvariants(unittest.TestCase):
    def test_01_owned_files_utf8_no_bom_pure_lf_zero_so_and_frozen_invariants(self) -> None:
        chiyo_plugin = _load_chiyo_plugin_module()
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(len(chiyo_plugin._PHONE_AND_WORLD_GUIDANCE), 1199)
        self.assertEqual(lived_context_bridge.MAX_LIVED_CONTEXT_CHARS, 1800)
        self.assertEqual(len(soak_runner.GET_ROUTES), 24)

        owned_files = [
            REPO_ROOT / "components" / "life" / "scripts" / "everyday_desire_lifecycle.py",
            REPO_ROOT / "components" / "life" / "scripts" / "v03_clock_evaporation.py",
            REPO_ROOT / "components" / "world" / "scripts" / "v03_economy_illness_bridge.py",
            REPO_ROOT / "components" / "phone" / "outbound_outbox_view.py",
            REPO_ROOT / "components" / "life" / "scripts" / "proactive_outreach_producer.py",
            REPO_ROOT / "components" / "phone" / "server.py",
            REPO_ROOT / "components" / "phone" / "v05_dreams_desires_view.py",
            REPO_ROOT / "components" / "world" / "scripts" / "v05_home_craft_engine.py",
            REPO_ROOT / "scripts" / "nyairo_day.py",
            Path(__file__).resolve(),
        ]
        for fpath in owned_files:
            raw = fpath.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM in {fpath}")
            self.assertNotIn(b"\r", raw, f"CR found in {fpath}")
            text = raw.decode("utf-8")
            so_chars = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(so_chars, [], f"unicode.So symbols found in {fpath}: {so_chars[:5]}")


if __name__ == "__main__":
    unittest.main()

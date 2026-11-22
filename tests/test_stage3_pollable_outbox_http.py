#!/usr/bin/env python3
"""Stage 3 Worker 3 HTTP tests for the unified pollable outbound outbox.

Verifies Blueprint §3.2 C end-to-end over real loopback ``PhoneHTTPServer``:
- 100% zero-write ``GET /api/phone/outbound/pending`` across missing, empty,
  freshly initialized (``nyairo_day.py init`` / bootstrap), and populated roots
  (``before_files == after_files``, zero ``-wal``/``-shm``/``.lock`` sidecars).
- Merged chronologically sorted listing of BOTH ``proactive_outreach``
  (``APPROVED_FOR_DELIVERY`` / ``CLAIMED_BY_TRANSPORT``) and
  ``deferred_reply_ready`` (``READY_TO_REPLY``) with strict private-field stripping
  and honest ``quiet_hours_active`` / ``quiet_hours_held`` signaling.
- ``POST /api/phone/outbound/ack`` CSRF ``403`` guard, invalid capability ``403``
  guard, subject ``ack_read`` ``403`` guard, forged ``actor_side`` ``400`` guard,
  unknown field ``400`` guard, missing ``operation_key`` ``400`` guard, invalid
  ``action`` ``400`` guard, full ``claim -> ack_delivered -> ack_read`` +
  ``ack_failed`` / ``ack_unknown`` + idempotent ``operation_key`` replay removing
  delivered items from ``pending``.
- Frozen quantitative invariants (``LIFE_UPDATE_SURFACES == 37``,
  ``_PHONE_AND_WORLD_GUIDANCE == 1199``, ``MAX_LIVED_CONTEXT_CHARS == 1800``,
  ``soak_runner.GET_ROUTES == 24``) and file hygiene (pure LF, UTF-8 no BOM,
  zero ``unicode.So`` emojis, neutral ``"TA"``).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
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
for _p in (
    REPO_ROOT,
    REPO_ROOT / "chiyo_bundle",
    REPO_ROOT / "scripts",
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import autonomy_rhythm_view as rhythm_view  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import outbound_outbox_view as outbox_view  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.world.scripts import v03_economy_illness_bridge as eco_bridge  # noqa: E402
from scripts import nyairo_day  # noqa: E402
from scripts import v02_wall_clock_soak as soak_runner  # noqa: E402

TZ_BJT = timezone(timedelta(hours=8))
T_MORNING = datetime(2026, 11, 20, 9, 30, 0, tzinfo=TZ_BJT)
T_QUIET = datetime(2026, 11, 20, 2, 15, 0, tzinfo=TZ_BJT)


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


def _sidecar_artifacts(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.name.endswith(("-wal", "-shm", ".lock", ".tmp"))
    )


def _assert_no_new_sidecar_artifacts(
    tc: unittest.TestCase,
    root: Path,
    before_sidecars: Optional[list[str]] = None,
) -> None:
    after = _sidecar_artifacts(root)
    if before_sidecars is None:
        tc.assertEqual(after, [], f"Unexpected WAL/SHM/lock sidecars found: {after}")
        return
    created = sorted(set(after) - set(before_sidecars))
    tc.assertEqual(created, [], f"GET created WAL/SHM/lock sidecars: {created}")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _load_chiyo_plugin_module() -> Any:
    plugin_path = REPO_ROOT / "plugins" / "chiyo" / "__init__.py"
    spec = importlib.util.spec_from_file_location("stage3_outbox_plugin_check", plugin_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
        with urllib.request.urlopen(req, timeout=5) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"raw": raw}
        return int(err.code), parsed


def _seed_proactive_and_deferred_state(root: Path) -> None:
    eco = eco_bridge.default_ecology_state()
    eco["revision"] = 3
    eco["updated_at"] = "2026-11-20T08:15:00+08:00"
    eco["reality_bridge"]["outbound_records"] = [
        {
            "outbound_id": "out_desktop_note_001",
            "fence_id": "secret_fence_token_001",
            "operation_key": "internal_op_secret_001",
            "channel": "desktop_note",
            "recipient_ref": "partner_internal_uid_999",
            "reason_kind": "genuine_life_share",
            "text": "早上醒来窗玻璃结了一层薄霜，顺手给你温了一杯麦茶。",
            "content_dedup_hash": "deadbeef1234",
            "status": "APPROVED_FOR_DELIVERY",
            "delivery_ack_state": "QUEUED_FOR_TRANSPORT",
            "epistemic_state": "queued_for_transport",
            "partner_read_confirmed": False,
            "delivered_is_not_read": True,
            "created_at": "2026-11-20T08:05:00+08:00",
            "wall_day": "2026-11-20",
            "delivered_at": None,
            "expires_at": None,
            "read_at": None,
            "private_note": "PRIVATE_SUBJECT_DRAFT_DO_NOT_LEAK",
            "subject_draft": "SENSITIVE_DRAFT_SECRET",
            "internal_token": "tok_internal_never_expose",
        },
        {
            "outbound_id": "out_mobile_ping_002",
            "fence_id": "secret_fence_token_002",
            "operation_key": "internal_op_secret_002",
            "channel": "mobile_ping",
            "recipient_ref": "partner_internal_uid_999",
            "reason_kind": "weather_care",
            "text": "刚才外头飘起小雨，出门别忘了带伞。",
            "status": "CLAIMED_BY_TRANSPORT",
            "delivery_ack_state": "CLAIMED_BY_TRANSPORT",
            "epistemic_state": "claimed_by_transport",
            "partner_read_confirmed": False,
            "delivered_is_not_read": True,
            "created_at": "2026-11-20T08:12:00+08:00",
            "claimed_at": "2026-11-20T08:13:00+08:00",
            "wall_day": "2026-11-20",
            "private_note": "ANOTHER_PRIVATE_NOTE",
        },
        {
            "outbound_id": "out_already_delivered_003",
            "channel": "desktop_note",
            "reason_kind": "genuine_life_share",
            "text": "这条已经投递过了，不该出现在 pending 里。",
            "status": "DELIVERED",
            "created_at": "2026-11-20T07:50:00+08:00",
            "delivered_at": "2026-11-20T07:51:00+08:00",
            "wall_day": "2026-11-20",
        },
        {
            "outbound_id": "out_withdrawn_004",
            "channel": "desktop_note",
            "reason_kind": "genuine_life_share",
            "text": "这条已经撤回了。",
            "status": "WITHDRAWN",
            "created_at": "2026-11-20T07:40:00+08:00",
            "wall_day": "2026-11-20",
        },
    ]
    _write_json(eco_bridge.ecology_state_path(root), eco)

    rhythm = rhythm_view.default_autonomy_rhythm_state()
    rhythm["revision"] = 2
    rhythm["updated_at"] = "2026-11-20T08:10:00+08:00"
    rhythm["availability"] = "READY_TO_REPLY"
    rhythm["readiness_promoted"] = True
    rhythm["subject_override"] = {
        "mode": "deep_focus",
        "until": "2026-11-20T11:00:00+08:00",
        "set_at": "2026-11-20T09:15:00+08:00",
        "delay_minutes": 105,
    }
    rhythm["pending_messages"] = [
        {
            "msg_id": "msg_rhythm_001",
            "full_text": "醒了吗，今天降温记得加一件外套。",
            "preview_text": "醒了吗，今天降温记得加一件外套。",
            "session_id": "nyairo-desktop",
            "queued_at": "2026-11-20T07:30:00+08:00",
            "deliver_after": "2026-11-20T08:06:00+08:00",
            "delay_minutes": 36,
            "status": "READY_TO_REPLY",
            "urgent": False,
            "reason": "SLEEPING",
            "reason_code": "SLEEPING",
            "is_service_error": False,
            "not_cold_war": True,
        },
        {
            "msg_id": "msg_rhythm_due_002",
            "full_text": "中午要不要顺路去买热乎的面包？",
            "preview_text": "中午要不要顺路去买热乎的面包？",
            "session_id": "wechat-bridge",
            "queued_at": "2026-11-20T07:50:00+08:00",
            "deliver_after": "2026-11-20T08:09:00+08:00",
            "delay_minutes": 19,
            "status": "QUEUED_ON_PHONE",
            "urgent": False,
            "reason": "OUT_ON_ERRAND",
            "reason_code": "OUT_ON_ERRAND",
            "is_service_error": False,
            "not_cold_war": True,
        },
        {
            "msg_id": "msg_rhythm_future_003",
            "full_text": "这条还没到时间，不应该提前放行。",
            "preview_text": "这条还没到时间，不应该提前放行。",
            "session_id": "nyairo-desktop",
            "queued_at": "2026-11-20T09:20:00+08:00",
            "deliver_after": "2026-11-20T11:00:00+08:00",
            "delay_minutes": 100,
            "status": "QUEUED_ON_PHONE",
            "urgent": False,
            "reason": "DEEP_FOCUS",
            "reason_code": "DEEP_FOCUS",
            "is_service_error": False,
            "not_cold_war": True,
        },
    ]
    _write_json(rhythm_view.autonomy_rhythm_state_path(root), rhythm)


class _LoopbackPhoneHarness:
    def __init__(
        self,
        root: Path,
        *,
        csrf_token: str = "stage3-outbox-csrf",
        world_clock: Any = None,
        with_capability: bool = False,
    ) -> None:
        self.root = root
        self.csrf_token = csrf_token
        self.svc = phone_service.PhoneService(
            _make_instance(root),
            world_clock=world_clock,
        )
        self.capability_token: Optional[str] = None
        cap_obj = None
        if with_capability:
            self.capability_token = "stage3-subject-capability-token-0123456789abcdef"
            cap_obj = phone_capability.Capability(
                token=self.capability_token,
                subject="chiyo.global",
                endpoint="http://127.0.0.1:0",
                issued_at="2026-11-20T09:00:00Z",
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

    def __enter__(self) -> "_LoopbackPhoneHarness":
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.srv.shutdown()
        self.srv.server_close()


class TestStage3PollableOutboxHTTP(unittest.TestCase):
    def test_01_zero_write_get_pending_on_missing_empty_init_and_populated_roots(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s3-outbox-zw-") as td:
            base_dir = Path(td)

            # 1) Non-existent directory: must not even create the directory
            missing_root = base_dir / "does_not_exist_yet"
            with _LoopbackPhoneHarness(
                missing_root, world_clock=lambda: T_MORNING
            ) as h:
                code, doc = _http_json(h.base_url + "/api/phone/outbound/pending")
            self.assertEqual(code, 200)
            self.assertFalse(missing_root.exists())
            self.assertTrue(doc["ok"])
            self.assertEqual(doc["status"], "ok")
            self.assertTrue(doc["zero_write"])
            self.assertEqual(doc["llm_calls"], 0)
            self.assertEqual(doc["data"]["schema"], "phone.outbound.pending.v1")
            self.assertEqual(doc["data"]["pending_count"], 0)
            self.assertEqual(doc["data"]["items"], [])
            self.assertFalse(doc["data"]["quiet_hours_active"])

            # 2) Empty existing directory: strictly zero writes
            empty_root = base_dir / "empty_root"
            empty_root.mkdir(parents=True, exist_ok=True)
            before_empty = _snapshot_tree(empty_root)
            with _LoopbackPhoneHarness(
                empty_root, world_clock=lambda: T_MORNING
            ) as h:
                code, doc = _http_json(h.base_url + "/api/phone/outbound/pending")
            self.assertEqual(code, 200)
            self.assertEqual(doc["data"]["pending_count"], 0)
            self.assertEqual(before_empty, _snapshot_tree(empty_root))
            _assert_no_new_sidecar_artifacts(self, empty_root)

            # 3) Freshly initialized root (real nyairo_day.py init on POSIX, fallback bootstrap)
            init_root = base_dir / "fresh_init_root"
            if os.name == "posix":
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(REPO_ROOT / "scripts" / "nyairo_day.py"),
                        "init",
                        "--root",
                        str(init_root),
                    ],
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                if proc.returncode != 0:
                    init_root.mkdir(parents=True, exist_ok=True)
                    nyairo_day.bootstrap_living_foundation_state(
                        init_root, now_iso=T_MORNING.isoformat(timespec="seconds")
                    )
            else:
                init_root.mkdir(parents=True, exist_ok=True)
                nyairo_day.bootstrap_living_foundation_state(
                    init_root, now_iso=T_MORNING.isoformat(timespec="seconds")
                )

            before_init = _snapshot_tree(init_root)
            before_init_sidecars = _sidecar_artifacts(init_root)
            with _LoopbackPhoneHarness(
                init_root, world_clock=lambda: T_MORNING
            ) as h:
                code, doc = _http_json(h.base_url + "/api/phone/outbound/pending")
                # Unknown query string is rejected with 400 and still writes nothing
                q_code, _ = _http_json(h.base_url + "/api/phone/outbound/pending?extra=1")
            self.assertEqual(code, 200)
            self.assertEqual(q_code, 400)
            self.assertTrue(doc["ok"])
            self.assertEqual(doc["status"], "ok")
            self.assertTrue(doc["zero_write"])
            self.assertEqual(doc["llm_calls"], 0)
            self.assertEqual(doc["data"]["pending_count"], 0)
            self.assertEqual(doc["data"]["items"], [])
            self.assertEqual(before_init, _snapshot_tree(init_root))
            _assert_no_new_sidecar_artifacts(self, init_root, before_init_sidecars)

            # 4) Populated root: strictly zero writes on repeated GET polls
            pop_root = base_dir / "populated_root"
            pop_root.mkdir(parents=True, exist_ok=True)
            _seed_proactive_and_deferred_state(pop_root)
            before_pop = _snapshot_tree(pop_root)
            with _LoopbackPhoneHarness(
                pop_root, world_clock=lambda: T_MORNING, with_capability=True
            ) as h:
                c1, d1 = _http_json(h.base_url + "/api/phone/outbound/pending")
                c2, d2 = _http_json(
                    h.base_url + "/api/phone/outbound/pending",
                    headers={"X-Phone-Capability": h.capability_token or ""},
                )
                c_bad_cap, _ = _http_json(
                    h.base_url + "/api/phone/outbound/pending",
                    headers={"X-Phone-Capability": "wrong-capability-token"},
                )
            self.assertEqual(c1, 200)
            self.assertEqual(c2, 200)
            self.assertEqual(c_bad_cap, 403)
            self.assertEqual(d1["data"]["pending_count"], 4)
            self.assertEqual(d2["data"]["pending_count"], 4)
            self.assertEqual(before_pop, _snapshot_tree(pop_root))
            _assert_no_new_sidecar_artifacts(self, pop_root)

    def test_02_merged_sorted_listing_privacy_stripping_and_quiet_hours(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s3-outbox-merge-") as td:
            root = Path(td)
            _seed_proactive_and_deferred_state(root)

            # Daytime poll: quiet_hours_active is False, items sorted ascending
            with _LoopbackPhoneHarness(
                root, world_clock=lambda: T_MORNING, with_capability=True
            ) as h:
                code, doc = _http_json(h.base_url + "/api/phone/outbound/pending")
                self.assertEqual(code, 200)
                self.assertTrue(doc["ok"])
                self.assertEqual(doc["status"], "ok")
                self.assertTrue(doc["zero_write"])
                self.assertEqual(doc["llm_calls"], 0)
                data = doc["data"]
                self.assertEqual(data["schema"], "phone.outbound.pending.v1")
                self.assertFalse(data["quiet_hours_active"])
                self.assertEqual(data["pending_count"], 4)

                ids = [it["item_id"] for it in data["items"]]
                self.assertEqual(
                    ids,
                    [
                        "out_desktop_note_001",
                        "msg_rhythm_001",
                        "msg_rhythm_due_002",
                        "out_mobile_ping_002",
                    ],
                )

                # Verify exact merged shapes
                first_pro = data["items"][0]
                self.assertEqual(first_pro["item_kind"], "proactive_outreach")
                self.assertEqual(first_pro["channel"], "desktop_note")
                self.assertEqual(first_pro["status"], "APPROVED_FOR_DELIVERY")
                self.assertEqual(first_pro["reason_kind"], "genuine_life_share")
                self.assertIn("薄霜", first_pro["text"])
                self.assertEqual(first_pro["created_at"], "2026-11-20T08:05:00+08:00")
                self.assertFalse(first_pro["quiet_hours_held"])
                self.assertTrue(first_pro["deliverable_now"])

                first_def = data["items"][1]
                self.assertEqual(first_def["item_kind"], "deferred_reply_ready")
                self.assertEqual(first_def["channel"], "session_reply")
                self.assertEqual(first_def["session_id"], "nyairo-desktop")
                self.assertEqual(first_def["status"], "READY_TO_REPLY")
                self.assertIn("外套", first_def["user_full_text"])
                self.assertEqual(first_def["ready_at"], "2026-11-20T08:06:00+08:00")
                self.assertFalse(first_def["quiet_hours_held"])
                self.assertTrue(first_def["deliverable_now"])

                # Never leaks private subject drafts, internal tokens, fence ids, or operation keys
                serialized = json.dumps(doc, ensure_ascii=False)
                for forbidden in (
                    "PRIVATE_SUBJECT_DRAFT_DO_NOT_LEAK",
                    "SENSITIVE_DRAFT_SECRET",
                    "tok_internal_never_expose",
                    "secret_fence_token_001",
                    "internal_op_secret_001",
                    "partner_internal_uid_999",
                    "deadbeef1234",
                    "ANOTHER_PRIVATE_NOTE",
                    "private_note",
                    "subject_draft",
                    "internal_token",
                    "fence_id",
                ):
                    self.assertNotIn(forbidden, serialized)

                # Even with subject capability, transport surface stays stripped
                c_sub, d_sub = _http_json(
                    h.base_url + "/api/phone/outbound/pending",
                    headers={"X-Phone-Capability": h.capability_token or ""},
                )
                self.assertEqual(c_sub, 200)
                sub_serialized = json.dumps(d_sub, ensure_ascii=False)
                self.assertNotIn("PRIVATE_SUBJECT_DRAFT_DO_NOT_LEAK", sub_serialized)
                self.assertNotIn("tok_internal_never_expose", sub_serialized)

            # Quiet-hours poll (02:15+08:00 inside 23:30-07:30): reports quiet_hours_active=True
            # and marks proactive items with quiet_hours_held=True, deliverable_now=False
            before_quiet = _snapshot_tree(root)
            with _LoopbackPhoneHarness(root, world_clock=lambda: T_QUIET) as hq:
                code_q, doc_q = _http_json(hq.base_url + "/api/phone/outbound/pending")
            self.assertEqual(code_q, 200)
            self.assertTrue(doc_q["data"]["quiet_hours_active"])
            pro_items_q = [
                it for it in doc_q["data"]["items"] if it["item_kind"] == "proactive_outreach"
            ]
            self.assertGreaterEqual(len(pro_items_q), 1)
            for it in pro_items_q:
                self.assertTrue(it["quiet_hours_held"])
                self.assertFalse(it["deliverable_now"])
            self.assertEqual(before_quiet, _snapshot_tree(root))

    def test_03_post_ack_security_guards_csrf_forged_fields_missing_op_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s3-outbox-sec-") as td:
            root = Path(td)
            _seed_proactive_and_deferred_state(root)
            before = _snapshot_tree(root)

            with _LoopbackPhoneHarness(
                root, world_clock=lambda: T_MORNING, with_capability=True
            ) as h:
                url = h.base_url + "/api/phone/outbound/ack"
                csrf_hdr = {"X-CSRF-Token": h.csrf_token}
                valid_base = {
                    "action": "claim",
                    "item_id": "out_desktop_note_001",
                    "item_kind": "proactive_outreach",
                    "operation_key": "op_sec_valid_1",
                }

                # 1) Missing CSRF -> 403
                c, _ = _http_json(url, method="POST", body=valid_base)
                self.assertEqual(c, 403)

                # 2) Invalid CSRF -> 403
                c, _ = _http_json(
                    url,
                    method="POST",
                    body=valid_base,
                    headers={"X-CSRF-Token": "wrong-csrf"},
                )
                self.assertEqual(c, 403)

                # 3) Invalid capability header -> 403 (never silently downgraded)
                c, _ = _http_json(
                    url,
                    method="POST",
                    body=valid_base,
                    headers={**csrf_hdr, "X-Phone-Capability": "wrong-cap"},
                )
                self.assertEqual(c, 403)

                # 4) Forged actor_side -> 400
                c, b = _http_json(
                    url,
                    method="POST",
                    body={**valid_base, "actor_side": "subject"},
                    headers=csrf_hdr,
                )
                self.assertEqual(c, 400)
                self.assertFalse(b["ok"])

                # 5) Forged author_side -> 400
                c, _ = _http_json(
                    url,
                    method="POST",
                    body={**valid_base, "author_side": "subject"},
                    headers=csrf_hdr,
                )
                self.assertEqual(c, 400)

                # 6) Unknown extra field -> 400
                c, _ = _http_json(
                    url,
                    method="POST",
                    body={**valid_base, "force_bypass": True},
                    headers=csrf_hdr,
                )
                self.assertEqual(c, 400)

                # 7) Missing operation_key -> 400
                no_key = {k: v for k, v in valid_base.items() if k != "operation_key"}
                c, _ = _http_json(url, method="POST", body=no_key, headers=csrf_hdr)
                self.assertEqual(c, 400)

                # 8) Blank operation_key -> 400
                c, _ = _http_json(
                    url,
                    method="POST",
                    body={**valid_base, "operation_key": "   "},
                    headers=csrf_hdr,
                )
                self.assertEqual(c, 400)

                # 9) Invalid action -> 400
                c, _ = _http_json(
                    url,
                    method="POST",
                    body={**valid_base, "action": "delete_everything"},
                    headers=csrf_hdr,
                )
                self.assertEqual(c, 400)

                # 10) Invalid item_kind -> 400
                c, _ = _http_json(
                    url,
                    method="POST",
                    body={**valid_base, "item_kind": "unknown_kind"},
                    headers=csrf_hdr,
                )
                self.assertEqual(c, 400)

                # 11) Subject side cannot forge partner ack_read -> 403
                c, _ = _http_json(
                    url,
                    method="POST",
                    body={**valid_base, "action": "ack_read", "operation_key": "op_sub_read"},
                    headers={**csrf_hdr, "X-Phone-Capability": h.capability_token or ""},
                )
                self.assertEqual(c, 403)

                # 12) Unknown item_id -> 404
                c, _ = _http_json(
                    url,
                    method="POST",
                    body={**valid_base, "item_id": "out_nonexistent_999", "operation_key": "op_nf_1"},
                    headers=csrf_hdr,
                )
                self.assertEqual(c, 404)

                # 13) Unknown query param -> 400
                c, _ = _http_json(
                    url + "?foo=bar",
                    method="POST",
                    body=valid_base,
                    headers=csrf_hdr,
                )
                self.assertEqual(c, 400)

            # All rejected requests must perform zero disk writes
            self.assertEqual(before, _snapshot_tree(root))

    def test_04_proactive_full_lifecycle_claim_deliver_read_and_idempotent_replay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s3-outbox-pro-life-") as td:
            root = Path(td)
            eco = eco_bridge.default_ecology_state()
            eco["revision"] = 1
            eco["updated_at"] = "2026-11-20T08:00:00+08:00"
            eco["reality_bridge"]["outbound_records"] = [
                {
                    "outbound_id": "out_life_001",
                    "channel": "desktop_note",
                    "reason_kind": "genuine_life_share",
                    "text": "窗台那盆薄荷新抽了两片叶。",
                    "status": "APPROVED_FOR_DELIVERY",
                    "delivery_ack_state": "QUEUED_FOR_TRANSPORT",
                    "epistemic_state": "queued_for_transport",
                    "partner_read_confirmed": False,
                    "delivered_is_not_read": True,
                    "created_at": "2026-11-20T08:05:00+08:00",
                    "wall_day": "2026-11-20",
                    "delivered_at": None,
                    "expires_at": None,
                    "read_at": None,
                },
                {
                    "outbound_id": "out_fail_002",
                    "channel": "mobile_ping",
                    "reason_kind": "weather_care",
                    "text": "下午有阵雨。",
                    "status": "APPROVED_FOR_DELIVERY",
                    "delivery_ack_state": "QUEUED_FOR_TRANSPORT",
                    "epistemic_state": "queued_for_transport",
                    "partner_read_confirmed": False,
                    "delivered_is_not_read": True,
                    "created_at": "2026-11-20T08:07:00+08:00",
                    "wall_day": "2026-11-20",
                    "delivered_at": None,
                },
                {
                    "outbound_id": "out_unk_003",
                    "channel": "mobile_ping",
                    "reason_kind": "weather_care",
                    "text": "晚风有点凉。",
                    "status": "APPROVED_FOR_DELIVERY",
                    "delivery_ack_state": "QUEUED_FOR_TRANSPORT",
                    "epistemic_state": "queued_for_transport",
                    "partner_read_confirmed": False,
                    "delivered_is_not_read": True,
                    "created_at": "2026-11-20T08:09:00+08:00",
                    "wall_day": "2026-11-20",
                    "delivered_at": None,
                },
            ]
            _write_json(eco_bridge.ecology_state_path(root), eco)

            with _LoopbackPhoneHarness(root, world_clock=lambda: T_MORNING) as h:
                hdr = {"X-CSRF-Token": h.csrf_token}
                pending_url = h.base_url + "/api/phone/outbound/pending"
                ack_url = h.base_url + "/api/phone/outbound/ack"

                # Initial pending shows 3 proactive items
                c0, d0 = _http_json(pending_url)
                self.assertEqual(c0, 200)
                self.assertEqual(d0["data"]["pending_count"], 3)

                # 1) claim -> CLAIMED_BY_TRANSPORT (still visible in pending!)
                c_cl, d_cl = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "claim",
                        "item_id": "out_life_001",
                        "item_kind": "proactive_outreach",
                        "operation_key": "op_claim_life_1",
                        "transport_receipt_id": "tr_claim_001",
                    },
                )
                self.assertEqual(c_cl, 200)
                self.assertTrue(d_cl["ok"])
                self.assertFalse(d_cl["already_applied"])
                self.assertEqual(d_cl["data"]["item_status"], "CLAIMED_BY_TRANSPORT")
                self.assertEqual(d_cl["llm_calls"], 0)

                # Idempotent replay of claim
                c_cl_rep, d_cl_rep = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "claim",
                        "item_id": "out_life_001",
                        "item_kind": "proactive_outreach",
                        "operation_key": "op_claim_life_1",
                        "transport_receipt_id": "tr_claim_001",
                    },
                )
                self.assertEqual(c_cl_rep, 200)
                self.assertTrue(d_cl_rep["already_applied"])
                self.assertEqual(d_cl_rep["status"], "already_applied")

                # Verify item is still in pending with status CLAIMED_BY_TRANSPORT
                c1, d1 = _http_json(pending_url)
                self.assertEqual(c1, 200)
                claimed_row = next(
                    it for it in d1["data"]["items"] if it["item_id"] == "out_life_001"
                )
                self.assertEqual(claimed_row["status"], "CLAIMED_BY_TRANSPORT")

                # 2) ack_delivered -> DELIVERED (removed from pending, unanswered_streak == 1)
                c_del, d_del = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_delivered",
                        "item_id": "out_life_001",
                        "item_kind": "proactive_outreach",
                        "operation_key": "op_deliv_life_1",
                        "transport_receipt_id": "tr_deliv_001",
                    },
                )
                self.assertEqual(c_del, 200)
                self.assertTrue(d_del["ok"])
                self.assertFalse(d_del["already_applied"])
                self.assertEqual(d_del["data"]["item_status"], "DELIVERED")

                # Replay ack_delivered with SAME operation_key -> idempotent, streak stays 1
                c_del_rep, d_del_rep = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_delivered",
                        "item_id": "out_life_001",
                        "item_kind": "proactive_outreach",
                        "operation_key": "op_deliv_life_1",
                        "transport_receipt_id": "tr_deliv_001",
                    },
                )
                self.assertEqual(c_del_rep, 200)
                self.assertTrue(d_del_rep["already_applied"])

                saved_after_deliv, _ = eco_bridge.read_stored_ecology_zero_write(root)
                self.assertEqual(
                    int(saved_after_deliv["reality_bridge"].get("unanswered_streak") or 0),
                    1,
                )

                # Delivered item is removed from GET /api/phone/outbound/pending
                before_poll = _snapshot_tree(root)
                c2, d2 = _http_json(pending_url)
                self.assertEqual(c2, 200)
                self.assertEqual(
                    [it["item_id"] for it in d2["data"]["items"]],
                    ["out_fail_002", "out_unk_003"],
                )
                self.assertEqual(before_poll, _snapshot_tree(root))

                # 3) ack_read -> READ (resets unanswered_streak to 0)
                c_rd, d_rd = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_read",
                        "item_id": "out_life_001",
                        "item_kind": "proactive_outreach",
                        "operation_key": "op_read_life_1",
                    },
                )
                self.assertEqual(c_rd, 200)
                self.assertTrue(d_rd["ok"])
                self.assertEqual(d_rd["data"]["item_status"], "READ")

                saved_after_read, _ = eco_bridge.read_stored_ecology_zero_write(root)
                self.assertEqual(
                    int(saved_after_read["reality_bridge"].get("unanswered_streak") or 0),
                    0,
                )

                # Replay ack_read -> idempotent
                c_rd_rep, d_rd_rep = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_read",
                        "item_id": "out_life_001",
                        "item_kind": "proactive_outreach",
                        "operation_key": "op_read_life_1",
                    },
                )
                self.assertEqual(c_rd_rep, 200)
                self.assertTrue(d_rd_rep["already_applied"])

                # 4) ack_failed and ack_unknown remove remaining two items without raising streak
                c_f, d_f = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_failed",
                        "item_id": "out_fail_002",
                        "item_kind": "proactive_outreach",
                        "operation_key": "op_fail_002",
                    },
                )
                self.assertEqual(c_f, 200)
                self.assertEqual(d_f["data"]["item_status"], "FAILED")

                c_u, d_u = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_unknown",
                        "item_id": "out_unk_003",
                        "item_kind": "proactive_outreach",
                        "operation_key": "op_unk_003",
                    },
                )
                self.assertEqual(c_u, 200)
                self.assertEqual(d_u["data"]["item_status"], "UNKNOWN")

                c_end, d_end = _http_json(pending_url)
                self.assertEqual(c_end, 200)
                self.assertEqual(d_end["data"]["pending_count"], 0)
                self.assertEqual(d_end["data"]["items"], [])

    def test_04b_honest_outbox_attempt_to_http_pending_and_ack_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s3-outbox-honest-e2e-") as td:
            root = Path(td)
            # Enqueue through real v03_economy_illness_bridge honest_outbox gate
            attempt_res = eco_bridge.attempt_proactive_outbound(
                root,
                text="刚泡好一壶焙茶，外头风软了不少。",
                channel="desktop_note",
                reason_kind="genuine_life_share",
                delivery_mode="honest_outbox",
                operation_key="op_honest_e2e_1",
                now_iso="2026-11-20T09:15:00+08:00",
            )
            self.assertTrue(attempt_res["ok"])
            receipt = attempt_res.get("receipt") or {}
            out_id = str(receipt.get("outbound_id") or "")
            self.assertTrue(out_id)
            self.assertEqual(receipt.get("outbound_status"), "APPROVED_FOR_DELIVERY")

            with _LoopbackPhoneHarness(root, world_clock=lambda: T_MORNING) as h:
                hdr = {"X-CSRF-Token": h.csrf_token}
                pending_url = h.base_url + "/api/phone/outbound/pending"
                ack_url = h.base_url + "/api/phone/outbound/ack"

                # Polled pending includes the honest_outbox record
                before_get = _snapshot_tree(root)
                c0, d0 = _http_json(pending_url)
                self.assertEqual(c0, 200)
                self.assertEqual(d0["data"]["pending_count"], 1)
                self.assertEqual(d0["data"]["items"][0]["item_id"], out_id)
                self.assertEqual(d0["data"]["items"][0]["status"], "APPROVED_FOR_DELIVERY")
                self.assertEqual(before_get, _snapshot_tree(root))

                # claim -> ack_delivered -> ack_read
                c1, d1 = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "claim",
                        "item_id": out_id,
                        "item_kind": "proactive_outreach",
                        "operation_key": "op_e2e_claim_1",
                        "transport_receipt_id": "desktop_tr_1",
                    },
                )
                self.assertEqual(c1, 200)
                self.assertEqual(d1["data"]["item_status"], "CLAIMED_BY_TRANSPORT")

                c2, d2 = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_delivered",
                        "item_id": out_id,
                        "item_kind": "proactive_outreach",
                        "operation_key": "op_e2e_deliv_1",
                        "transport_receipt_id": "desktop_tr_1",
                    },
                )
                self.assertEqual(c2, 200)
                self.assertEqual(d2["data"]["item_status"], "DELIVERED")

                # Pending now empty
                c_after, d_after = _http_json(pending_url)
                self.assertEqual(c_after, 200)
                self.assertEqual(d_after["data"]["pending_count"], 0)

                c3, d3 = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_read",
                        "item_id": out_id,
                        "item_kind": "proactive_outreach",
                        "operation_key": "op_e2e_read_1",
                    },
                )
                self.assertEqual(c3, 200)
                self.assertEqual(d3["data"]["item_status"], "READ")

    def test_05_deferred_reply_lifecycle_and_idempotent_replay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s3-outbox-def-life-") as td:
            root = Path(td)
            rhythm = rhythm_view.default_autonomy_rhythm_state()
            rhythm["revision"] = 1
            rhythm["updated_at"] = "2026-11-20T08:05:00+08:00"
            rhythm["availability"] = "READY_TO_REPLY"
            rhythm["readiness_promoted"] = True
            rhythm["pending_messages"] = [
                {
                    "msg_id": "msg_rhythm_live_001",
                    "full_text": "刚忙完，晚点一起喝杯热可可吧。",
                    "preview_text": "刚忙完，晚点一起喝杯热可可吧。",
                    "session_id": "nyairo-desktop",
                    "queued_at": "2026-11-20T07:45:00+08:00",
                    "deliver_after": "2026-11-20T08:06:00+08:00",
                    "delay_minutes": 21,
                    "status": "READY_TO_REPLY",
                    "urgent": False,
                    "reason": "SLEEPING",
                    "reason_code": "SLEEPING",
                    "is_service_error": False,
                    "not_cold_war": True,
                }
            ]
            _write_json(rhythm_view.autonomy_rhythm_state_path(root), rhythm)

            with _LoopbackPhoneHarness(root, world_clock=lambda: T_MORNING) as h:
                hdr = {"X-CSRF-Token": h.csrf_token}
                pending_url = h.base_url + "/api/phone/outbound/pending"
                ack_url = h.base_url + "/api/phone/outbound/ack"

                c0, d0 = _http_json(pending_url)
                self.assertEqual(c0, 200)
                self.assertEqual(d0["data"]["pending_count"], 1)
                self.assertEqual(d0["data"]["items"][0]["item_kind"], "deferred_reply_ready")
                self.assertEqual(d0["data"]["items"][0]["item_id"], "msg_rhythm_live_001")

                # Claim -> stays visible in pending as CLAIMED_BY_TRANSPORT
                c_cl, d_cl = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "claim",
                        "item_id": "msg_rhythm_live_001",
                        "item_kind": "deferred_reply_ready",
                        "operation_key": "op_def_claim_1",
                        "transport_receipt_id": "tr_def_001",
                    },
                )
                self.assertEqual(c_cl, 200)
                self.assertTrue(d_cl["ok"])
                self.assertEqual(d_cl["data"]["item_status"], "CLAIMED_BY_TRANSPORT")

                c_mid, d_mid = _http_json(pending_url)
                self.assertEqual(c_mid, 200)
                self.assertEqual(d_mid["data"]["pending_count"], 1)
                self.assertEqual(d_mid["data"]["items"][0]["status"], "CLAIMED_BY_TRANSPORT")

                # Deliver -> removed from pending
                c_del, d_del = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_delivered",
                        "item_id": "msg_rhythm_live_001",
                        "item_kind": "deferred_reply_ready",
                        "operation_key": "op_def_deliv_1",
                        "transport_receipt_id": "tr_def_002",
                    },
                )
                self.assertEqual(c_del, 200)
                self.assertTrue(d_del["ok"])
                self.assertFalse(d_del["already_applied"])
                self.assertEqual(d_del["data"]["item_status"], "DELIVERED")

                # Replay deliver -> idempotent
                c_rep, d_rep = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_delivered",
                        "item_id": "msg_rhythm_live_001",
                        "item_kind": "deferred_reply_ready",
                        "operation_key": "op_def_deliv_1",
                        "transport_receipt_id": "tr_def_002",
                    },
                )
                self.assertEqual(c_rep, 200)
                self.assertTrue(d_rep["already_applied"])

                # ack_read -> READ (and idempotent replay)
                c_rd, d_rd = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_read",
                        "item_id": "msg_rhythm_live_001",
                        "item_kind": "deferred_reply_ready",
                        "operation_key": "op_def_read_1",
                    },
                )
                self.assertEqual(c_rd, 200)
                self.assertTrue(d_rd["ok"])
                self.assertEqual(d_rd["data"]["item_status"], "READ")

                c_rd_rep, d_rd_rep = _http_json(
                    ack_url,
                    method="POST",
                    headers=hdr,
                    body={
                        "action": "ack_read",
                        "item_id": "msg_rhythm_live_001",
                        "item_kind": "deferred_reply_ready",
                        "operation_key": "op_def_read_1",
                    },
                )
                self.assertEqual(c_rd_rep, 200)
                self.assertTrue(d_rd_rep["already_applied"])

                # Removed from pending, and GET stays 100% zero-write
                before_end = _snapshot_tree(root)
                c_end, d_end = _http_json(pending_url)
                self.assertEqual(c_end, 200)
                self.assertEqual(d_end["data"]["pending_count"], 0)
                self.assertEqual(d_end["data"]["items"], [])
                self.assertEqual(before_end, _snapshot_tree(root))

    def test_06_frozen_invariants_and_owned_file_hygiene(self) -> None:
        # Frozen quantitative invariants
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)
        plugin_mod = _load_chiyo_plugin_module()
        self.assertEqual(len(plugin_mod._PHONE_AND_WORLD_GUIDANCE), 1199)
        self.assertEqual(len(soak_runner.GET_ROUTES), 24)
        self.assertEqual(len(set(soak_runner.GET_ROUTES)), 24)
        self.assertNotIn("/api/phone/outbound/pending", soak_runner.GET_ROUTES)

        # Pure LF, UTF-8 no BOM, zero unicode.So emojis across all 4 owned files
        owned_files = (
            REPO_ROOT / "components" / "phone" / "outbound_outbox_view.py",
            REPO_ROOT / "components" / "phone" / "service.py",
            REPO_ROOT / "components" / "phone" / "server.py",
            Path(__file__).resolve(),
        )
        for path in owned_files:
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM in {path}")
            self.assertNotIn(b"\r\n", raw, f"CRLF in {path}")
            text = raw.decode("utf-8")
            emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"unicode.So emoji in {path}: {emojis[:5]}")

        # Neutral pronoun & zero hardcoded character name in our new files
        for path in (
            REPO_ROOT / "components" / "phone" / "outbound_outbox_view.py",
            Path(__file__).resolve(),
        ):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u5979", text, f"Gendered pronoun in {path}")
            self.assertNotIn("\u5343\u4ee3", text, f"Hardcoded character name in {path}")

        # Module exports sanity check
        self.assertEqual(outbox_view.OUTBOX_PENDING_SCHEMA, "phone.outbound.pending.v1")
        self.assertEqual(
            outbox_view.OUTBOUND_ACK_ALLOWED_FIELDS,
            frozenset({"action", "item_id", "item_kind", "operation_key", "transport_receipt_id"}),
        )


if __name__ == "__main__":
    unittest.main()

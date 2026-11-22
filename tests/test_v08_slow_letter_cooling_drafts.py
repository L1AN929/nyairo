#!/usr/bin/env python3
"""v3.0 Ch.10 Section 1.2 Slow-Letter True 72h Draft-to-Post Lifecycle tests.

Covers:
1. Full 72h cooling lifecycle for both partner and subject over real HTTP loopback
   (``POST /api/phone/home/keepsakes``, ``POST /api/phone/home/letters``, and
   ``GET /api/phone/home/letters``);
2. 100% zero-write GET invariant (byte hash + ``mtime_ns`` + file tree) and strict
   bilateral privacy (0 cross-side draft items, titles, timestamps, or hidden counts);
3. Revise during cooling window updates text/place while preserving original
   ``drafted_at`` and ``cools_until = drafted_at + 72h``;
4. Premature post at ``T0 + 71h59m`` returns ``409 SLOW_LETTER_COOLING_PERIOD`` with
   ``cools_until`` and ``remaining_cooling_seconds`` and zero disk mutation;
5. Cross-author revise/discard/post returns ``404 DRAFT_NOT_FOUND`` (anti-probing);
6. Post at ``T0 + 72h`` atomically moves draft into ``in_transit`` slow letter
   (``text_withheld=True`` for recipient while in transit, readable with postmark once
   ``now >= deliver_after``);
7. Discard draft returns ``omission_debt == 0``; ``operation_key`` idempotency via
   ``committed_op_ring`` replays safely without duplicate drafts or letters;
8. CSRF/capability enforcement, ``400`` on client-smuggled ``author_side`` /
   ``actor_side`` / ``uid`` / ``score`` keys, per-author 10-draft cap, ``delay_hours``
   clamping ``1..168``, legacy ``post_slow_letter`` compatibility, and untouched
   ``LIFE_UPDATE_SURFACES == 37``.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "chiyo_bundle"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import window_keepsake_view as view  # noqa: E402

TZ8 = timezone(timedelta(hours=8))
T0 = datetime(2026, 11, 14, 10, 0, 0, tzinfo=TZ8)
SUBJECT = "chiyo"
CSRF_TOKEN = "slow-letter-cooling-csrf-token"
CAP_TOKEN = "slow-letter-cooling-cap-token-01"


class _EmptySharing:
    def listing(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "status": phone_service.STATUS_OK,
            "data": {"shares": [], "truncated": False},
        }


def _dir_fingerprint(root: Path) -> tuple[list[str], dict[str, tuple[int, int, str]]]:
    tree = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    files: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            files[str(p.relative_to(root))] = (
                st.st_mtime_ns,
                st.st_size,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
    return tree, files


class SlowLetterCoolingDraftsHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="v08-slow-letter-drafts-"))
        self.addCleanup(self._cleanup)
        self.now = T0
        instance = {
            "root": str(self.root),
            "subject": SUBJECT,
            "world_home": str(self.root / "world"),
            "supply_socket": str(self.root / "run" / "supply.sock"),
            "private_items_artifact_id": "artifact:private-items",
            "artifact_grant_id": "grant:slow-letter",
            "workspace_id": "workspace:slow-letter",
        }
        self.service = phone_service.PhoneService(
            instance,
            environment={},
            world_clock=lambda: self.now,
            sharing=_EmptySharing(),
        )
        cap = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at=T0.isoformat(timespec="seconds"),
        )
        self.server = phone_server.PhoneHTTPServer(
            ("127.0.0.1", 0),
            service=self.service,
            csrf_token=CSRF_TOKEN,
            capability=cap,
        )
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _cleanup(self) -> None:
        if getattr(self, "server", None) is not None:
            self.server.shutdown()
            self.server.server_close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _http(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        csrf: bool = False,
        capability: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=raw, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", CSRF_TOKEN)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        if raw is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _run_full_72h_lifecycle(self, *, author_is_subject: bool, post_route: str) -> None:
        author_cap = CAP_TOKEN if author_is_subject else None
        recipient_cap = None if author_is_subject else CAP_TOKEN
        expected_author = "subject" if author_is_subject else "partner"

        self.now = T0
        # Baseline recipient GET before any draft exists (must remain 100% zero-write)
        tree_0, files_0 = _dir_fingerprint(self.root)
        c_base, recipient_baseline = self._http(
            "GET", "/api/phone/home/letters", capability=recipient_cap
        )
        self.assertEqual(c_base, 200, recipient_baseline)
        self.assertEqual((tree_0, files_0), _dir_fingerprint(self.root))

        # 1) Draft at T0
        secret_title = f"海边邮筒慢信初稿-{expected_author}"
        secret_body = f"第一天的风吹过堤岸，先把这句话收进抽屉里（{expected_author}）。"
        c1, r1 = self._http(
            "POST",
            post_route,
            {
                "action": "save_slow_letter_draft",
                "title": secret_title,
                "body": secret_body,
                "postmark_place": "海岬老邮筒",
                "operation_key": f"op-save-{expected_author}",
            },
            csrf=True,
            capability=author_cap,
        )
        self.assertEqual(c1, 200, r1)
        self.assertEqual(r1["status"], "applied")
        draft_id = r1["data"]["draft_id"]
        draft_row = r1["data"]["draft"]
        self.assertEqual(draft_row["author_side"], expected_author)
        self.assertEqual(draft_row["status"], "cooling_draft")
        self.assertEqual(draft_row["drafted_at"], "2026-11-14T10:00:00+08:00")
        self.assertEqual(draft_row["cools_until"], "2026-11-17T10:00:00+08:00")
        self.assertFalse(draft_row["ready_to_post"])
        self.assertEqual(draft_row["remaining_cooling_seconds"], 259200)

        # GET /api/phone/home/letters is 100% zero-write and enforces strict bilateral privacy
        tree_before_get, files_before_get = _dir_fingerprint(self.root)
        c_own, own_letters = self._http(
            "GET", "/api/phone/home/letters", capability=author_cap
        )
        self.assertEqual(c_own, 200, own_letters)
        own_drafts = own_letters["data"]["own_slow_letter_drafts"]
        self.assertEqual(len(own_drafts), 1)
        self.assertEqual(own_letters["data"]["own_slow_letter_draft_count"], 1)
        self.assertEqual(own_drafts[0]["draft_id"], draft_id)
        self.assertFalse(own_drafts[0]["ready_to_post"])
        self.assertEqual(own_drafts[0]["remaining_cooling_seconds"], 259200)

        c_other, other_letters = self._http(
            "GET", "/api/phone/home/letters", capability=recipient_cap
        )
        self.assertEqual(c_other, 200, other_letters)
        self.assertEqual(other_letters["data"]["own_slow_letter_drafts"], [])
        self.assertEqual(other_letters["data"]["own_slow_letter_draft_count"], 0)
        other_dump = json.dumps(other_letters, ensure_ascii=False)
        self.assertNotIn(secret_title, other_dump)
        self.assertNotIn(secret_body, other_dump)
        self.assertNotIn(draft_id, other_dump)
        # Strict zero-knowledge non-interference: recipient response is byte-for-byte identical to baseline!
        self.assertEqual(other_letters, recipient_baseline)
        self.assertEqual((tree_before_get, files_before_get), _dir_fingerprint(self.root))

        # 2) Revise at T0 + 24h updates text while keeping cools_until = T0 + 72h
        self.now = T0 + timedelta(hours=24)
        revised_body = f"第二天重新读了一遍，把句子改得更安静一些（{expected_author}）。"
        c2, r2 = self._http(
            "POST",
            post_route,
            {
                "action": "revise_slow_letter_draft",
                "draft_id": draft_id,
                "body": revised_body,
                "operation_key": f"op-revise-{expected_author}",
            },
            csrf=True,
            capability=author_cap,
        )
        self.assertEqual(c2, 200, r2)
        self.assertEqual(r2["data"]["draft"]["body"], revised_body)
        self.assertEqual(r2["data"]["draft"]["drafted_at"], "2026-11-14T10:00:00+08:00")
        self.assertEqual(r2["data"]["draft"]["cools_until"], "2026-11-17T10:00:00+08:00")
        self.assertEqual(r2["data"]["draft"]["updated_at"], "2026-11-15T10:00:00+08:00")
        self.assertEqual(r2["data"]["remaining_cooling_seconds"], 172800)

        # 3) Premature post at T0 + 71h59m -> 409 SLOW_LETTER_COOLING_PERIOD, zero disk mutation
        self.now = T0 + timedelta(hours=71, minutes=59)
        tree_pre_cool, files_pre_cool = _dir_fingerprint(self.root)
        c3, r3 = self._http(
            "POST",
            post_route,
            {
                "action": "post_drafted_slow_letter",
                "draft_id": draft_id,
            },
            csrf=True,
            capability=author_cap,
        )
        self.assertEqual(c3, 409, r3)
        self.assertEqual(r3["reason_code"], "SLOW_LETTER_COOLING_PERIOD")
        self.assertEqual(r3["data"]["cools_until"], "2026-11-17T10:00:00+08:00")
        self.assertEqual(r3["data"]["remaining_cooling_seconds"], 60)
        self.assertEqual((tree_pre_cool, files_pre_cool), _dir_fingerprint(self.root))

        # 4) Cross-author revise / discard / post -> 404 DRAFT_NOT_FOUND (anti-probing, zero disk write)
        for cross_payload in (
            {"action": "revise_slow_letter_draft", "draft_id": draft_id, "body": "试探改写"},
            {"action": "post_drafted_slow_letter", "draft_id": draft_id},
            {"action": "discard_slow_letter_draft", "draft_id": draft_id},
        ):
            c_cross, r_cross = self._http(
                "POST",
                post_route,
                cross_payload,
                csrf=True,
                capability=recipient_cap,
            )
            self.assertEqual(c_cross, 404, (cross_payload, r_cross))
            self.assertEqual(r_cross["reason_code"], "DRAFT_NOT_FOUND")
        self.assertEqual((tree_pre_cool, files_pre_cool), _dir_fingerprint(self.root))

        # 5) At T0 + 72h: author sees ready_to_post=True, posts cleanly into in_transit slow letter
        self.now = T0 + timedelta(hours=72)
        c_ready, ready_doc = self._http(
            "GET", "/api/phone/home/letters", capability=author_cap
        )
        self.assertEqual(c_ready, 200)
        self.assertTrue(ready_doc["data"]["own_slow_letter_drafts"][0]["ready_to_post"])
        self.assertEqual(ready_doc["data"]["own_slow_letter_drafts"][0]["remaining_cooling_seconds"], 0)

        c_post, r_post = self._http(
            "POST",
            post_route,
            {
                "action": "post_drafted_slow_letter",
                "draft_id": draft_id,
                "operation_key": f"op-post-{expected_author}",
            },
            csrf=True,
            capability=author_cap,
        )
        self.assertEqual(c_post, 200, r_post)
        self.assertEqual(r_post["status"], "applied")
        self.assertEqual(r_post["data"]["status"], "in_transit")
        self.assertEqual(r_post["data"]["drafted_at"], "2026-11-14T10:00:00+08:00")
        self.assertEqual(r_post["data"]["sent_at"], "2026-11-17T10:00:00+08:00")
        # Default delay_hours == 12 -> 22:00 +08:00
        self.assertEqual(r_post["data"]["deliver_after"], "2026-11-17T22:00:00+08:00")
        letter_id = r_post["data"]["letter_id"]

        # Idempotent replay of post_drafted_slow_letter returns already_applied with zero duplicate letters
        c_replay, r_replay = self._http(
            "POST",
            post_route,
            {
                "action": "post_drafted_slow_letter",
                "draft_id": draft_id,
                "operation_key": f"op-post-{expected_author}",
            },
            csrf=True,
            capability=author_cap,
        )
        self.assertEqual(c_replay, 200, r_replay)
        self.assertEqual(r_replay["status"], "already_applied")
        self.assertTrue(r_replay["data"]["duplicate"])
        self.assertEqual(r_replay["data"]["letter_id"], letter_id)

        # Draft is removed from own_slow_letter_drafts
        _, after_post_own = self._http("GET", "/api/phone/home/letters", capability=author_cap)
        self.assertEqual(after_post_own["data"]["own_slow_letter_drafts"], [])

        # While in transit (T0 + 74h < T0 + 84h): recipient gets text_withheld=True, body=None
        self.now = T0 + timedelta(hours=74)
        tree_transit, files_transit = _dir_fingerprint(self.root)
        c_tr, tr_doc = self._http("GET", "/api/phone/home/letters", capability=recipient_cap)
        self.assertEqual(c_tr, 200, tr_doc)
        self.assertEqual((tree_transit, files_transit), _dir_fingerprint(self.root))
        tr_row = next(r for r in tr_doc["data"]["in_transit"] if r["letter_id"] == letter_id)
        self.assertTrue(tr_row["text_withheld"])
        self.assertIsNone(tr_row["body"])
        self.assertIsNone(tr_row["text"])
        self.assertNotIn(revised_body, json.dumps(tr_doc, ensure_ascii=False))

        # Once now >= deliver_after (T0 + 84h): recipient reads delivered slow letter with postmark (zero-write!)
        self.now = T0 + timedelta(hours=84)
        tree_deliv, files_deliv = _dir_fingerprint(self.root)
        c_deliv, deliv_doc = self._http(
            "GET", "/api/phone/home/letters", capability=recipient_cap
        )
        self.assertEqual(c_deliv, 200, deliv_doc)
        self.assertEqual((tree_deliv, files_deliv), _dir_fingerprint(self.root))
        delivered_row = next(
            r for r in deliv_doc["data"]["delivered"] if r["letter_id"] == letter_id
        )
        self.assertFalse(delivered_row["text_withheld"])
        self.assertEqual(delivered_row["body"], revised_body)
        self.assertEqual(delivered_row["postmark"]["place"], "海岬老邮筒")
        self.assertEqual(delivered_row["postmark"]["drafted_at"], "2026-11-14T10:00:00+08:00")
        self.assertEqual(delivered_row["postmark"]["sent_at"], "2026-11-17T10:00:00+08:00")
        self.assertEqual(delivered_row["postmark"]["status"], "delivered")

    def test_01_partner_72h_lifecycle_on_keepsakes_route(self) -> None:
        self._run_full_72h_lifecycle(
            author_is_subject=False, post_route="/api/phone/home/keepsakes"
        )

    def test_02_subject_72h_lifecycle_on_letters_route(self) -> None:
        self._run_full_72h_lifecycle(
            author_is_subject=True, post_route="/api/phone/home/letters"
        )

    def test_03_discard_zero_omission_debt_and_idempotent_op_ring(self) -> None:
        self.now = T0
        c1, r1 = self._http(
            "POST",
            "/api/phone/home/keepsakes",
            {
                "action": "save_slow_letter_draft",
                "title": "写了一半收起来的信",
                "body": "想想还是当面再聊吧。",
                "operation_key": "op-discard-save-1",
            },
            csrf=True,
        )
        self.assertEqual(c1, 200, r1)
        draft_id = r1["data"]["draft_id"]
        rev1 = r1["data"]["revision"]

        # Replay save with same operation_key -> already_applied, revision unchanged
        c1_dup, r1_dup = self._http(
            "POST",
            "/api/phone/home/keepsakes",
            {
                "action": "save_slow_letter_draft",
                "title": "写了一半收起来的信",
                "body": "想想还是当面再聊吧。",
                "operation_key": "op-discard-save-1",
            },
            csrf=True,
        )
        self.assertEqual(c1_dup, 200)
        self.assertEqual(r1_dup["status"], "already_applied")
        self.assertEqual(r1_dup["data"]["revision"], rev1)

        # Reuse same operation_key with different body -> 409 OPERATION_FINGERPRINT_MISMATCH
        c_conf, r_conf = self._http(
            "POST",
            "/api/phone/home/keepsakes",
            {
                "action": "save_slow_letter_draft",
                "title": "写了一半收起来的信",
                "body": "篡改后的不同内容。",
                "operation_key": "op-discard-save-1",
            },
            csrf=True,
        )
        self.assertEqual(c_conf, 409, r_conf)
        self.assertEqual(r_conf["reason_code"], "OPERATION_FINGERPRINT_MISMATCH")

        # Discard draft -> omission_debt == 0, cleanly removed
        self.now = T0 + timedelta(hours=5)
        c_dis, r_dis = self._http(
            "POST",
            "/api/phone/home/keepsakes",
            {
                "action": "discard_slow_letter_draft",
                "draft_id": draft_id,
                "operation_key": "op-discard-1",
            },
            csrf=True,
        )
        self.assertEqual(c_dis, 200, r_dis)
        self.assertEqual(r_dis["data"]["omission_debt"], 0)
        self.assertTrue(r_dis["data"]["discarded"])
        rev_dis = r_dis["data"]["revision"]

        # Replay discard with same operation_key -> 200 already_applied (not 404!)
        c_dis2, r_dis2 = self._http(
            "POST",
            "/api/phone/home/keepsakes",
            {
                "action": "discard_slow_letter_draft",
                "draft_id": draft_id,
                "operation_key": "op-discard-1",
            },
            csrf=True,
        )
        self.assertEqual(c_dis2, 200, r_dis2)
        self.assertEqual(r_dis2["status"], "already_applied")
        self.assertEqual(r_dis2["data"]["omission_debt"], 0)
        self.assertEqual(r_dis2["data"]["revision"], rev_dis)

        # Discard again with a fresh key -> 404 DRAFT_NOT_FOUND
        c_gone, r_gone = self._http(
            "POST",
            "/api/phone/home/keepsakes",
            {"action": "discard_slow_letter_draft", "draft_id": draft_id},
            csrf=True,
        )
        self.assertEqual(c_gone, 404, r_gone)
        self.assertEqual(r_gone["reason_code"], "DRAFT_NOT_FOUND")

    def test_04_security_gates_smuggled_fields_cap_and_delay_clamping(self) -> None:
        for route in ("/api/phone/home/keepsakes", "/api/phone/home/letters"):
            # Missing CSRF -> 403
            c_nocsrf, r_nocsrf = self._http(
                "POST",
                route,
                {"action": "save_slow_letter_draft", "title": "信", "body": "正文"},
            )
            self.assertEqual(c_nocsrf, 403, r_nocsrf)
            self.assertEqual(r_nocsrf["reason_code"], "CSRF_TOKEN_MISSING")

            # Invalid capability -> 403 (never silently downgraded to partner)
            c_badcap, r_badcap = self._http(
                "POST",
                route,
                {"action": "save_slow_letter_draft", "title": "信", "body": "正文"},
                csrf=True,
                capability="invalid-cap-token",
            )
            self.assertEqual(c_badcap, 403, r_badcap)
            self.assertEqual(r_badcap["reason_code"], "CAPABILITY_INVALID")

            # Client-smuggled author_side / actor_side / uid / score / cools_until -> 400
            for smuggled_key, smuggled_val in (
                ("author_side", "subject"),
                ("actor_side", "subject"),
                ("uid", 0),
                ("score", 99),
                ("omission_debt", 5),
                ("cools_until", "2020-01-01T00:00:00+08:00"),
                ("drafted_at", "2020-01-01T00:00:00+08:00"),
                ("unknown_extra", "x"),
            ):
                c_smug, r_smug = self._http(
                    "POST",
                    route,
                    {
                        "action": "save_slow_letter_draft",
                        "title": "信",
                        "body": "正文",
                        smuggled_key: smuggled_val,
                    },
                    csrf=True,
                )
                self.assertEqual(c_smug, 400, (route, smuggled_key, r_smug))

        self.assertFalse(view.state_path(self.root).exists())

        # Per-author 10 active cooling drafts cap (partner hitting 10 does not block subject)
        self.now = T0
        partner_ids: list[str] = []
        for idx in range(10):
            code, res = self._http(
                "POST",
                "/api/phone/home/letters",
                {
                    "action": "save_slow_letter_draft",
                    "title": f"伴侣手稿-{idx}",
                    "body": f"第 {idx} 封慢慢写的草稿。",
                },
                csrf=True,
            )
            self.assertEqual(code, 200, res)
            partner_ids.append(res["data"]["draft_id"])

        c_11, r_11 = self._http(
            "POST",
            "/api/phone/home/letters",
            {
                "action": "save_slow_letter_draft",
                "title": "第11封超限草稿",
                "body": "超出单侧 10 封上限。",
            },
            csrf=True,
        )
        self.assertEqual(c_11, 409, r_11)
        self.assertEqual(r_11["reason_code"], "SLOW_LETTER_DRAFT_LIMIT_REACHED")

        # Subject can still save own draft independently and sees 0 partner drafts
        c_sub, r_sub = self._http(
            "POST",
            "/api/phone/home/letters",
            {
                "action": "save_slow_letter_draft",
                "title": "主体独立草稿",
                "body": "单侧配额互不挤占。",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_sub, 200, r_sub)
        sub_draft_id = r_sub["data"]["draft_id"]
        _, sub_get = self._http("GET", "/api/phone/home/letters", capability=CAP_TOKEN)
        self.assertEqual(sub_get["data"]["own_slow_letter_draft_count"], 1)

        # Verify delay_hours clamping (0 -> 1h, 500 -> 168h) after 72h cooling
        self.now = T0 + timedelta(hours=72)
        c_min, r_min = self._http(
            "POST",
            "/api/phone/home/letters",
            {
                "action": "post_drafted_slow_letter",
                "draft_id": partner_ids[0],
                "delay_hours": 0,
            },
            csrf=True,
        )
        self.assertEqual(c_min, 200, r_min)
        self.assertEqual(r_min["data"]["delay_hours"], 1.0)
        self.assertEqual(r_min["data"]["deliver_after"], "2026-11-17T11:00:00+08:00")

        c_max, r_max = self._http(
            "POST",
            "/api/phone/home/letters",
            {
                "action": "post_drafted_slow_letter",
                "draft_id": sub_draft_id,
                "delay_hours": 500,
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_max, 200, r_max)
        self.assertEqual(r_max["data"]["delay_hours"], 168.0)
        self.assertEqual(r_max["data"]["deliver_after"], "2026-11-24T10:00:00+08:00")

        # Legacy post_slow_letter without draft_id remains 100% backward-compatible
        direct = view.post_slow_letter(
            self.root,
            title="兼容直发测试慢信",
            body="不带草稿 ID 的直接投递入口保持原行为。",
            now="2026-11-17T12:00:00+08:00",
        )
        self.assertEqual(direct["drafted_at"], "2026-11-17T12:00:00+08:00")

        # Frozen phone_tool LIFE_UPDATE_SURFACES == 37 stays completely untouched
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)


if __name__ == "__main__":
    unittest.main()

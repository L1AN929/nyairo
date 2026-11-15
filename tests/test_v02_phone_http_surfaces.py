#!/usr/bin/env python3
"""Real loopback ``PhoneHTTPServer`` integration tests for all three v0.2 phone HTTP surfaces:
  1. ``/api/phone/v02/agency-depth`` (``nyairo.v02.agency_depth.v1``)
  2. ``/api/phone/v02/crossmodal`` (``nyairo.v02.crossmodal.v1``)
  3. ``/api/phone/v02/runtime-migration`` (``nyairo.v02.runtime_migration.v1``)

Covers:
- 100% zero-write GET projections (partner & subject perspectives);
- Unknown query string rejection (``400 UNKNOWN_QUERY``);
- Missing CSRF interception (``403``), empty JSON dict rejection (``400``), client identity smuggling refusal (``409``);
- Partner CSRF POST success + idempotency + ``403 FORBIDDEN_ACTOR_SIDE`` on subject-only actions;
- Subject ``X-Phone-Capability`` header unlocking subject perspective & subject-only mutations;
- Real >120KB PNG base64 upload via ``POST /api/phone/v02/crossmodal`` (``analyze_image_palette``) extracting exact ``#RRGGBB`` hex colors without ``413`` / ``BODY_NOT_JSON``.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
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

from components.phone import capability as phone_capability  # noqa: E402
from components.phone import server as phone_server  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "v02-phone-http-cap-token-20261113"
HEX_RE = re.compile(r"^#[0-9A-F]{6}$")

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
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), digest)
        else:
            dir_mtime = 0 if rel == "data" else int(st.st_mtime_ns)
            snap[rel] = (0, dir_mtime, "dir")
    return snap


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _synth_large_bicolor_png(
    width: int = 240,
    height: int = 240,
    top_rgb: tuple[int, int, int] = (238, 92, 44),
    bottom_rgb: tuple[int, int, int] = (24, 52, 108),
) -> bytes:
    """Build a valid 8-bit RGB PNG >120KB using store-level zlib blocks so real bytes exceed 64KB."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    top_row = b"\x00" + bytes(top_rgb) * width
    bottom_row = b"\x00" + bytes(bottom_rgb) * width
    half = height // 2
    raw_scanlines = top_row * half + bottom_row * (height - half)
    idat = zlib.compress(raw_scanlines, level=0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


class V02FrozenAnchorsAndLFTest(unittest.TestCase):
    def test_00_frozen_anchors_and_lf_line_endings(self) -> None:
        for rel_path, expected_sha in FROZEN_ANCHORS.items():
            full = ROOT / rel_path
            actual = hashlib.sha256(full.read_bytes()).hexdigest()
            self.assertEqual(actual, expected_sha, f"Frozen anchor mutated: {rel_path}")

        owned_files = (
            ROOT / "components" / "phone" / "service.py",
            ROOT / "components" / "phone" / "server.py",
            ROOT / "tests" / "test_v02_phone_http_surfaces.py",
        )
        for fpath in owned_files:
            self.assertNotIn(b"\r\n", fpath.read_bytes(), f"CRLF detected in {fpath}")


@POSIX_ONLY
class V02PhoneHTTPSurfacesIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v02-phone-http-"))
        cls.server_root = cls.temp_dir / "instance"
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-13T12:00:00+08:00",
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
            "state/v02_agency_depth_state.json",
            "phone/v02_crossmodal.json",
            "phone/v02_runtime_migration.json",
        ):
            (self.stack.root / rel).unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 13, 14, 30, tzinfo=timezone(timedelta(hours=8))
        )

    def _http_raw(
        self,
        method: str,
        path: str,
        raw_body: bytes | None = None,
        *,
        csrf: bool = False,
        capability: str | None = None,
        content_type: str = "application/json",
    ) -> tuple[int, dict[str, Any]]:
        req = urllib.request.Request(self.base_url + path, data=raw_body, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        if raw_body is not None:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

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
        return self._http_raw(
            method,
            path,
            raw,
            csrf=csrf,
            capability=capability,
        )

    def test_01_all_three_v02_get_routes_zero_write_unknown_query_and_bad_capability(self) -> None:
        self.assertIsInstance(self.stack.server, phone_server.PhoneHTTPServer)
        routes = (
            ("/api/phone/v02/agency-depth", "nyairo.v02.agency_depth.v1", "partner", "subject"),
            ("/api/phone/v02/crossmodal", "nyairo.v02.crossmodal.v1", "partner", "subject"),
            ("/api/phone/v02/runtime-migration", "nyairo.v02.runtime_migration.v1", "partner_default", "her_private"),
        )
        before = _snapshot_tree(self.stack.root)
        for path, expected_schema, expected_partner_persp, expected_subject_persp in routes:
            code_p, res_p = self._http("GET", path)
            self.assertEqual(code_p, 200, f"{path}: {res_p}")
            self.assertTrue(res_p.get("ok"), res_p)
            self.assertEqual(res_p.get("status"), "applied")
            data_p = res_p.get("data") or {}
            self.assertEqual(data_p.get("schema"), expected_schema)
            self.assertEqual(data_p.get("perspective"), expected_partner_persp)
            self.assertEqual(data_p.get("revision"), 0)
            self.assertEqual(data_p.get("llm_calls"), 0)

            code_s, res_s = self._http("GET", path, capability=CAP_TOKEN)
            self.assertEqual(code_s, 200, f"{path} subject: {res_s}")
            self.assertEqual((res_s.get("data") or {}).get("perspective"), expected_subject_persp)

            code_q, res_q = self._http("GET", path + "?unexpected=1")
            self.assertEqual(code_q, 400)
            self.assertFalse(res_q.get("ok"))
            self.assertEqual(res_q.get("reason_code"), "UNKNOWN_QUERY")

            code_bad_cap, _ = self._http("GET", path, capability="forged-capability-token")
            self.assertEqual(code_bad_cap, 403)

        after = _snapshot_tree(self.stack.root)
        self.assertEqual(before, after, "GET on all v02 phone HTTP surfaces must be 100% zero-write")

    def test_02_post_common_guards_missing_csrf_empty_object_query_and_identity_fence(self) -> None:
        routes = (
            "/api/phone/v02/agency-depth",
            "/api/phone/v02/crossmodal",
            "/api/phone/v02/runtime-migration",
        )
        for path in routes:
            c_no_csrf, res_no_csrf = self._http("POST", path, {"action": "noop"})
            self.assertEqual(c_no_csrf, 403, f"{path} missing CSRF must be 403: {res_no_csrf}")

            c_q, res_q = self._http("POST", path + "?extra=1", {"action": "noop"}, csrf=True)
            self.assertEqual(c_q, 400)
            self.assertEqual(res_q.get("reason_code"), "UNKNOWN_QUERY")

            c_bad_cap, _ = self._http(
                "POST",
                path,
                {"action": "noop"},
                csrf=True,
                capability="invalid-cap",
            )
            self.assertEqual(c_bad_cap, 403)

            c_empty, res_empty = self._http("POST", path, {}, csrf=True)
            self.assertEqual(c_empty, 400, f"{path} empty dict must be 400: {res_empty}")
            self.assertFalse(res_empty.get("ok"))

            c_not_obj, _ = self._http_raw("POST", path, b"[]", csrf=True)
            self.assertEqual(c_not_obj, 400)

            c_uid, res_uid = self._http(
                "POST",
                path,
                {"action": "noop", "uid": "root"},
                csrf=True,
            )
            self.assertEqual(c_uid, 409, f"{path} smuggled uid must be 409: {res_uid}")
            self.assertIn("CLIENT_IDENTITY_REFUSED", str(res_uid.get("reason_code") or ""))

    def test_03_v02_agency_depth_partner_post_403_escalation_and_subject_capability(self) -> None:
        path = "/api/phone/v02/agency-depth"

        # 1. Partner allowed action succeeds with CSRF + replays idempotently
        payload_opt = {
            "action": "offer_low_cost_option",
            "domain": "tea",
            "option_title": "焙茶热泡",
            "note": "不喝也没关系，放在茶盘边",
            "operation_key": "v02-ad-opt-1",
        }
        c_ok, res_ok = self._http("POST", path, payload_opt, csrf=True)
        self.assertEqual(c_ok, 200, res_ok)
        self.assertTrue(res_ok.get("ok"))
        self.assertEqual(res_ok.get("status"), "applied")
        self.assertEqual(len(res_ok["data"]["partner_options"]), 1)

        c_dup, res_dup = self._http("POST", path, payload_opt, csrf=True)
        self.assertEqual(c_dup, 200, res_dup)
        self.assertEqual(res_dup.get("status"), "already_applied")

        # 2. Partner without capability cannot call subject-only actions -> 403
        c_forbid_regret, res_forbid = self._http(
            "POST",
            path,
            {
                "action": "record_regret_reflection",
                "regret_kind": "process_mistake",
                "summary": "调色前没先试小样",
            },
            csrf=True,
        )
        self.assertEqual(c_forbid_regret, 403, res_forbid)
        self.assertEqual(res_forbid.get("reason_code"), "FORBIDDEN_ACTOR_SIDE")

        c_forbid_ep, _ = self._http(
            "POST",
            path,
            {
                "action": "record_choice_episode",
                "domain": "music",
                "chosen": "雨声白噪",
                "options": ["雨声白噪", "古典钢琴"],
                "choice_mode": "spontaneous",
            },
            csrf=True,
        )
        self.assertEqual(c_forbid_ep, 403)

        # 3. Subject capability header unlocks subject-only mutations & private perspective
        secret_regret = "刚才急着上墨把留白弄脏了，下次先在边角试笔"
        c_sub_reg, res_sub_reg = self._http(
            "POST",
            path,
            {
                "action": "record_regret_reflection",
                "regret_kind": "process_mistake",
                "summary": secret_regret,
                "method_adjustment_cn": "落笔前在试纸确认水分",
                "shared": False,
                "operation_key": "v02-ad-regret-1",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_sub_reg, 200, res_sub_reg)
        self.assertEqual(res_sub_reg.get("status"), "applied")

        secret_pref_title = "深夜独自听旧磁带沙沙声"
        c_sub_ep, res_sub_ep = self._http(
            "POST",
            path,
            {
                "action": "record_choice_episode",
                "domain": "night_sound",
                "chosen": secret_pref_title,
                "options": [secret_pref_title, "播客访谈"],
                "choice_mode": "spontaneous",
                "shared": False,
                "operation_key": "v02-ad-ep-1",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_sub_ep, 200, res_sub_ep)

        # Partner GET withholds regrets and unshared preferences
        c_get_p, get_p = self._http("GET", path)
        self.assertEqual(c_get_p, 200)
        raw_partner_json = json.dumps(get_p, ensure_ascii=False)
        self.assertNotIn(secret_regret, raw_partner_json)
        self.assertNotIn(secret_pref_title, raw_partner_json)
        self.assertEqual(get_p["data"]["regret_reflections"], [])
        self.assertTrue(get_p["data"]["regret_reflections_withheld"])

        # Subject GET with capability unlocks regrets and unshared preferences
        c_get_s, get_s = self._http("GET", path, capability=CAP_TOKEN)
        self.assertEqual(c_get_s, 200)
        self.assertEqual(get_s["data"]["perspective"], "subject")
        self.assertFalse(get_s["data"]["regret_reflections_withheld"])
        self.assertEqual(len(get_s["data"]["regret_reflections"]), 1)
        self.assertEqual(get_s["data"]["regret_reflections"][0]["summary"], secret_regret)
        raw_subject_json = json.dumps(get_s, ensure_ascii=False)
        self.assertIn(secret_pref_title, raw_subject_json)

    def test_04_v02_crossmodal_large_png_palette_403_escalation_and_subject_capability(self) -> None:
        path = "/api/phone/v02/crossmodal"

        # 1. Synthesize a real >120KB PNG and upload via partner CSRF POST
        large_png = _synth_large_bicolor_png(
            width=240,
            height=240,
            top_rgb=(238, 92, 44),     # #EE5C2C
            bottom_rgb=(24, 52, 108),  # #18346C
        )
        self.assertGreater(len(large_png), 120 * 1024, "Test PNG bytes must exceed 120KB")
        b64_png = "data:image/png;base64," + base64.b64encode(large_png).decode("ascii")

        c_img, res_img = self._http(
            "POST",
            path,
            {
                "action": "analyze_image_palette",
                "title": "窗台黄昏与夜色交界大图",
                "image_base64": b64_png,
                "share_with_room": True,
                "operation_key": "v02-cm-large-png-1",
            },
            csrf=True,
        )
        self.assertEqual(c_img, 200, res_img)
        self.assertTrue(res_img.get("ok"))
        self.assertEqual(res_img.get("status"), "applied")
        studies = res_img["data"]["palette_studies"]
        self.assertEqual(len(studies), 1)
        dom_hex = studies[0]["dominant_hex"]
        self.assertGreaterEqual(len(dom_hex), 2)
        for hx in dom_hex:
            self.assertRegex(hx, HEX_RE)
        self.assertIn("#EE5C2C", dom_hex)
        self.assertIn("#18346C", dom_hex)
        self.assertEqual(studies[0]["width"], 240)
        self.assertEqual(studies[0]["height"], 240)
        self.assertEqual(res_img["data"]["llm_calls"], 0)
        study_id = studies[0]["study_id"]

        # Idempotent replay of the large PNG upload
        c_img_dup, res_img_dup = self._http(
            "POST",
            path,
            {
                "action": "analyze_image_palette",
                "title": "窗台黄昏与夜色交界大图",
                "image_base64": b64_png,
                "share_with_room": True,
                "operation_key": "v02-cm-large-png-1",
            },
            csrf=True,
        )
        self.assertEqual(c_img_dup, 200)
        self.assertEqual(res_img_dup.get("status"), "already_applied")

        # 2. Partner without capability cannot call subject-only apply_palette_to_work -> 403
        c_forbid, res_forbid = self._http(
            "POST",
            path,
            {
                "action": "apply_palette_to_work",
                "study_id": study_id,
                "work_title": "海边速写草稿",
                "selected_hex": ["#EE5C2C", "#18346C"],
            },
            csrf=True,
        )
        self.assertEqual(c_forbid, 403, res_forbid)
        self.assertEqual(res_forbid.get("reason_code"), "FORBIDDEN_ACTOR_SIDE")

        # 3. Subject capability unlocks private study + apply_palette_to_work
        private_study_title = "私人未公开水彩试色底稿"
        small_png = _synth_large_bicolor_png(
            width=24,
            height=24,
            top_rgb=(88, 156, 132),
            bottom_rgb=(88, 156, 132),
        )
        c_priv, res_priv = self._http(
            "POST",
            path,
            {
                "action": "analyze_image_palette",
                "title": private_study_title,
                "image_base64": base64.b64encode(small_png).decode("ascii"),
                "share_with_room": False,
                "operation_key": "v02-cm-priv-1",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_priv, 200, res_priv)

        c_apply, res_apply = self._http(
            "POST",
            path,
            {
                "action": "apply_palette_to_work",
                "study_id": study_id,
                "work_title": "海边速写草稿",
                "selected_hex": ["#EE5C2C", "#18346C"],
                "usage_note_cn": "只借用冷暖交界色，不当成今天亲眼见过",
                "share_with_room": True,
                "operation_key": "v02-cm-apply-1",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_apply, 200, res_apply)
        self.assertEqual(res_apply.get("status"), "applied")
        self.assertEqual(len(res_apply["data"]["work_palette_applications"]), 1)

        # Partner GET strips private study; Subject GET sees both studies
        _, get_p = self._http("GET", path)
        self.assertNotIn(private_study_title, json.dumps(get_p, ensure_ascii=False))
        self.assertEqual(get_p["data"]["private_studies_withheld_count"], 1)

        _, get_s = self._http("GET", path, capability=CAP_TOKEN)
        self.assertIn(private_study_title, json.dumps(get_s, ensure_ascii=False))
        self.assertEqual(get_s["data"]["private_studies_withheld_count"], 0)

        # 4. Oversized body (> 6 MiB) still rejected with 413 BODY_TOO_LARGE before reading body
        host_port = self.base_url.split("://", 1)[1]
        conn = http.client.HTTPConnection(host_port, timeout=10)
        try:
            conn.putrequest("POST", path)
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(6 * 1024 * 1024 + 256))
            conn.putheader("X-CSRF-Token", self.stack.csrf_token)
            conn.endheaders()
            resp_413 = conn.getresponse()
            res_413 = json.loads(resp_413.read().decode("utf-8"))
            self.assertEqual(resp_413.status, 413, res_413)
            self.assertEqual(res_413.get("reason_code"), "BODY_TOO_LARGE")
        finally:
            conn.close()

    def test_05_v02_runtime_migration_partner_post_403_escalation_and_subject_capability(self) -> None:
        path = "/api/phone/v02/runtime-migration"

        # 1. Partner allowed action with CSRF succeeds + idempotent replay
        payload_budget = {
            "action": "set_budget_mode",
            "mode": "CONSERVATION",
            "budget_scale_pct": 45,
            "reason_cn": "下午省电安静待机",
            "operation_key": "v02-rm-budget-1",
        }
        c_ok, res_ok = self._http("POST", path, payload_budget, csrf=True)
        self.assertEqual(c_ok, 200, res_ok)
        self.assertTrue(res_ok.get("ok"))
        self.assertEqual(res_ok.get("status"), "applied")
        self.assertEqual(res_ok["data"]["runtime_mode"]["mode"], "LOW_BUDGET")

        c_dup, res_dup = self._http("POST", path, payload_budget, csrf=True)
        self.assertEqual(c_dup, 200, res_dup)
        self.assertEqual(res_dup.get("status"), "already_applied")

        # 2. Partner without capability cannot call subject-only actions -> 403
        c_forbid_rev, res_forbid_rev = self._http(
            "POST",
            path,
            {
                "action": "reverify_migrated_skill",
                "skill_id": "watercolor_wash",
                "passed": True,
            },
            csrf=True,
        )
        self.assertEqual(c_forbid_rev, 403, res_forbid_rev)
        self.assertEqual(res_forbid_rev.get("reason_code"), "FORBIDDEN_ACTOR_SIDE")

        c_forbid_aud, _ = self._http(
            "POST",
            path,
            {
                "action": "record_workspace_audit",
                "wake_reason": "desk_lamp_on",
                "visible_candidate_count": 4,
                "entered_workspace_count": 2,
            },
            csrf=True,
        )
        self.assertEqual(c_forbid_aud, 403)

        # 3. Subject capability unlocks reverify_migrated_skill & private_draft_titles in workspace audit
        c_sub_rev, res_sub_rev = self._http(
            "POST",
            path,
            {
                "action": "reverify_migrated_skill",
                "skill_id": "all",
                "passed": True,
                "note_cn": "新环境试涂渐变正常",
                "operation_key": "v02-rm-rev-1",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_sub_rev, 200, res_sub_rev)
        self.assertEqual(res_sub_rev.get("status"), "applied")

        secret_draft_title = "未公开的冬日长信私人草稿第3页"
        c_sub_aud, res_sub_aud = self._http(
            "POST",
            path,
            {
                "action": "record_workspace_audit",
                "wake_reason": "quiet_evening_check",
                "visible_candidate_count": 5,
                "entered_workspace_count": 2,
                "admitted_candidates": ["read_tea_note", "tidy_brushes"],
                "trimmed_candidates": ["heavy_refactor"],
                "trimmed_reasons": ["conservation_budget"],
                "private_draft_titles": [secret_draft_title],
                "selection_rule": "conservation_top2",
                "operation_key": "v02-rm-audit-1",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_sub_aud, 200, res_sub_aud)
        self.assertEqual(res_sub_aud.get("status"), "applied")

        # Partner GET redacts private_draft_titles; Subject GET unlocks them
        _, get_p = self._http("GET", path)
        self.assertEqual(get_p["data"]["perspective"], "partner_default")
        self.assertNotIn(secret_draft_title, json.dumps(get_p, ensure_ascii=False))

        _, get_s = self._http("GET", path, capability=CAP_TOKEN)
        self.assertEqual(get_s["data"]["perspective"], "her_private")
        self.assertIn(secret_draft_title, json.dumps(get_s, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()

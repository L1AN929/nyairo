#!/usr/bin/env python3
"""v0.9 Stage 2 HTTP, 60s Heartbeat, Daily Loop Bridge & Context Bridge Tests.

Covers Blueprint §6.1-6.4 with ``llm_calls == 0``:
1. Real loopback ``PhoneHTTPServer`` tests for ``/api/phone/v09/living-agency``
   and ``/api/phone/v09/world-commitments`` (zero-write GET across empty/init/populated
   roots, CSRF 403, capability 403, subject-only 403, smuggled fields 400/409,
   partner pre-reveal SHA-256 seal masking, forget-barrier hiding).
2. ``scripts/nyairo_day.py`` 60s heartbeat O(1) zero-spin idle skip (0 disk writes)
   vs exact wake on due arrival & weather change (rain cancellation).
3. ``v02_v03_daily_loop_bridge`` producer candidates (including illness envelope &
   acute-illness ``v03_idle:daydream_window`` protection) + receipt-gated settlement
   + ``lived_memory_sink`` domains (``v09_stance``, ``v09_commitment``).
4. ``chiyo_bundle/lived_context_bridge.py`` ``v09_agency_world_line`` rendering
   (``<= 1800`` chars, Calm Gate ``""`` on empty root, situational boost ``+2``,
   forget-barrier clean, strict full-file zero-pronoun/name check).
5. All 9 sealed/frozen files, quantitative invariants (37 / 1199 / 1800 / 24 / 128),
   and pure-LF / UTF-8-no-BOM / zero-emoji hygiene across all 9 owned files.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
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
from components.life.scripts import lived_memory_sink  # noqa: E402
from components.life.scripts import v02_representation_lease_forget as rlf  # noqa: E402
from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.life.scripts import v09_true_stance_engine as tse  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone import v09_living_agency_view as la_view  # noqa: E402
from components.phone import v09_world_commitments_view as wc_view  # noqa: E402
from components.world.scripts import personal_objects_taxonomy as pot  # noqa: E402
from components.world.scripts import v03_economy_illness_bridge as eco_bridge  # noqa: E402
from components.world.scripts import world_commitments_engine as wce  # noqa: E402
from components.world.scripts import world_content_catalog as wcc  # noqa: E402
from scripts import nyairo_day  # noqa: E402
from scripts import v02_wall_clock_soak as soak_runner  # noqa: E402

TZ_BJT = timezone(timedelta(hours=8))
T0_ISO = "2026-11-21T10:00:00+08:00"
T_PLUS_1H_ISO = "2026-11-21T11:00:00+08:00"
T_PLUS_25H_ISO = "2026-11-22T11:00:00+08:00"

SEALED_FILES_SHA256: dict[str, str] = {
    "components/life/scripts/autonomous_life_assembly.py": (
        "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434"
    ),
    "components/life/scripts/life_sharing_service.py": (
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038"
    ),
    "components/native/native_recall/readonly_recall_adapter.py": (
        "6667ff66fe8105f28f436609f0cd8d744b185c0580e7f1642aafef0b873af78a"
    ),
    "NOW.md": (
        "d77be1900e94b5e997a389831b864aab5207459bfc5ac763d1b9d90c6c181152"
    ),
    "plugins/chiyo/__init__.py": (
        "8d6ba0f9295677c9371f0e9be07442096eba67438451ae99fa57312ca622bed7"
    ),
    "components/native/native_recall/production_consumption_evaluator_v0_3.py": (
        "141822d19049a8a5247b68885616cd0c156d616dea84d36ab099584605fc1b41"
    ),
    "components/native/native_recall/production_consumption_evaluator_v0.py": (
        "f7fdeccecea1c1a8377f4806d1d09b1a203367eb58bdc56505e021c264eb2400"
    ),
    "components/native/native_recall/CANDIDATE_CONSUMPTION_POLICY_V0_3.json": (
        "6228d24345249aed862f56484004c522e7573760d0288480ca3e87008a1514b2"
    ),
    "components/world/scripts/v05_unified_world_fabric.py": (
        "e6d5ce671872285935c9840a1de0cb75687b5562fe89c45b5ba10a402ffc0772"
    ),
}

OWNED_FILES: tuple[str, ...] = (
    "components/phone/v09_living_agency_view.py",
    "components/phone/v09_world_commitments_view.py",
    "components/phone/service.py",
    "components/phone/server.py",
    "scripts/nyairo_day.py",
    "components/life/scripts/v02_v03_daily_loop_bridge.py",
    "components/life/scripts/lived_memory_sink.py",
    "chiyo_bundle/lived_context_bridge.py",
    "tests/test_v09_http_context_and_loop_wiring.py",
)


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


def _sidecars(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.name.endswith(("-wal", "-shm", ".lock", ".tmp"))
    )


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
        except Exception:
            parsed = {"raw": raw}
        return int(err.code), parsed


def _start_phone_http_server(
    root: Path,
    *,
    csrf_token: str = "csrf-v09-test-token",
    clock_iso: str = T0_ISO,
) -> tuple[phone_server.PhoneHTTPServer, threading.Thread, str, str]:
    token_str = "cap_v09_test_token_0123456789abcdef0123456789"
    cap = phone_capability.Capability(
        token=token_str,
        subject="chiyo.global",
        endpoint="http://127.0.0.1:0",
        issued_at=clock_iso,
    )
    svc = phone_service.PhoneService(
        _make_instance(root),
        clock=lambda: clock_iso,
        world_clock=lambda: datetime.fromisoformat(clock_iso),
    )
    srv = phone_server.create_server(svc, csrf_token=csrf_token, port=0, capability=cap)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    host, port = srv.server_address
    base_url = f"http://{host}:{port}"
    return srv, thread, base_url, cap.token


def _load_chiyo_plugin_module() -> Any:
    plugin_path = REPO_ROOT / "plugins" / "chiyo" / "__init__.py"
    spec = importlib.util.spec_from_file_location("v09_s2c_chiyo_plugin", plugin_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class V09LivingAgencyHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-la-http-")
        self.root = Path(self._tmp.name)
        self.csrf = "csrf-v09-living-agency"
        self.srv, self.thread, self.base_url, self.cap_token = _start_phone_http_server(
            self.root, csrf_token=self.csrf, clock_iso=T0_ISO
        )

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=3)
        self._tmp.cleanup()

    def _partner_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}

    def _subject_headers(self) -> dict[str, str]:
        return {
            "X-CSRF-Token": self.csrf,
            "X-Phone-Capability": self.cap_token,
        }

    def test_01_zero_write_get_empty_and_initialized_root(self) -> None:
        url = self.base_url + la_view.ROUTE_V09_LIVING_AGENCY

        # 1. Empty root: 100% zero-write
        before = _snapshot_tree(self.root)
        code, body = _http_json(url)
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["schema"], "phone.v09.living_agency.v1")
        self.assertEqual(body["view"], "partner")
        self.assertTrue(body["zero_write"])
        self.assertEqual(body["llm_calls"], 0)
        data = body["data"]
        for key in (
            "blind_choices",
            "growth_stances",
            "repurposed_objects",
            "custom_categories",
            "categorized_items",
            "capability_gaps_summary",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["custom_categories"], [])
        self.assertEqual(_snapshot_tree(self.root), before)
        self.assertEqual(_sidecars(self.root), [])

        # 2. Initialized instance root (phone/ + run/instance.json) stays 100% zero-write
        (self.root / "phone").mkdir(parents=True, exist_ok=True)
        (self.root / "run").mkdir(parents=True, exist_ok=True)
        (self.root / "run" / "nyairo-day.instance.json").write_text(
            json.dumps({"schema": "nyairo.day.instance.v1", "world_id": "w_test"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        before_init = _snapshot_tree(self.root)
        code_s, body_s = _http_json(url, headers={"X-Phone-Capability": self.cap_token})
        self.assertEqual(code_s, 200)
        self.assertEqual(body_s["view"], "subject")
        self.assertEqual(_snapshot_tree(self.root), before_init)
        self.assertEqual(_sidecars(self.root), [])

        # Unknown query rejected with 400
        code_q, body_q = _http_json(url + "?foo=bar")
        self.assertEqual(code_q, 400)
        self.assertEqual(body_q.get("reason_code"), "UNKNOWN_QUERY")

    def test_02_csrf_capability_smuggling_and_actor_guards(self) -> None:
        url = self.base_url + la_view.ROUTE_V09_LIVING_AGENCY

        # Missing CSRF -> 403
        code, _ = _http_json(
            url,
            method="POST",
            body={"action": "register_unformed_topic", "topic_cn": "晚间海风散步"},
        )
        self.assertEqual(code, 403)

        # Wrong capability -> 403
        code, _ = _http_json(
            url,
            method="POST",
            headers={"X-CSRF-Token": self.csrf, "X-Phone-Capability": "wrong-capability-token-9999"},
            body={"action": "register_unformed_topic", "topic_cn": "晚间海风散步"},
        )
        self.assertEqual(code, 403)

        # Smuggled actor_side / author_side -> 400
        for bad_key in ("actor_side", "author_side"):
            code, body = _http_json(
                url,
                method="POST",
                headers=self._partner_headers(),
                body={
                    "action": "register_unformed_topic",
                    bad_key: "subject",
                    "topic_cn": "晚间海风散步",
                },
            )
            self.assertEqual(code, 400, bad_key)
            self.assertEqual(body.get("reason_code"), "SMUGGLED_ACTOR_FIELD", bad_key)

        # Smuggled clear_reservations (including mixed-case / nested) -> 409 SYCOPHANTIC_REASON_ERASURE_FORBIDDEN
        for bad_erasure_body in (
            {
                "action": "open_blind_choice",
                "topic_cn": "晚饭吃什么",
                "options": [{"option_id": "a", "label_cn": "家常面馆"}, {"option_id": "b", "label_cn": "排队烤肉"}],
                "clear_reservations": True,
            },
            {
                "action": "open_blind_choice",
                "topic_cn": "晚饭吃什么",
                "options": [{"option_id": "a", "label_cn": "家常面馆", "Clear_Reservations": True}, {"option_id": "b", "label_cn": "排队烤肉"}],
            },
        ):
            code, body = _http_json(
                url,
                method="POST",
                headers=self._partner_headers(),
                body=bad_erasure_body,
            )
            self.assertEqual(code, 409)
            self.assertEqual(body.get("reason_code"), "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN")

        # Mixed-case nested forbidden keys (e.g. Custom_Code / Code) -> 400 FORBIDDEN_CLIENT_KEY
        for bad_nested_key in ("Custom_Code", "Code", "Force_Flip"):
            code, body = _http_json(
                url,
                method="POST",
                headers=self._partner_headers(),
                body={
                    "action": "open_blind_choice",
                    "topic_cn": "晚饭吃什么",
                    "options": [{"option_id": "a", "label_cn": "家常面馆", bad_nested_key: "evil"}, {"option_id": "b", "label_cn": "排队烤肉"}],
                },
            )
            self.assertEqual(code, 400, bad_nested_key)
            self.assertEqual(body.get("reason_code"), "FORBIDDEN_CLIENT_KEY", bad_nested_key)

        # Subject-only actions without capability -> 403 FORBIDDEN_ACTOR_SIDE
        for subj_action in (
            "subject_commit_blind_choice",
            "resolve_blind_choice",
            "advance_stance_from_experience",
            "evaluate_partner_persuasion",
            "set_stance_shared",
            "register_or_repurpose_object",
            "store_item_in_object",
            "create_custom_category",
            "record_blocked_attempt",
        ):
            code, body = _http_json(
                url,
                method="POST",
                headers=self._partner_headers(),
                body={"action": subj_action},
            )
            self.assertEqual(code, 403, subj_action)
            self.assertEqual(body.get("reason_code"), "FORBIDDEN_ACTOR_SIDE", subj_action)

        # Partner-only action with subject capability -> 403 FORBIDDEN_ACTOR_SIDE
        code, body = _http_json(
            url,
            method="POST",
            headers=self._subject_headers(),
            body={"action": "partner_submit_blind_choice", "choice_id": "bc_1", "selected_choice_id": "a"},
        )
        self.assertEqual(code, 403)
        self.assertEqual(body.get("reason_code"), "FORBIDDEN_ACTOR_SIDE")

    def test_03_blind_choice_sha256_seal_masking_taxonomy_and_forget_barrier(self) -> None:
        url = self.base_url + la_view.ROUTE_V09_LIVING_AGENCY

        # 1. Partner opens blind choice
        code, body = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "open_blind_choice",
                "topic_cn": "晚饭吃什么",
                "options": [
                    {"option_id": "noodle", "label_cn": "巷口清汤面"},
                    {"option_id": "bbq", "label_cn": "网红排队烤肉"},
                ],
                "operation_key": "la-bc-open-1",
            },
        )
        self.assertEqual(code, 200)
        self.assertEqual(body["llm_calls"], 0)
        choice_id = body["receipt"]["choice_id"]

        # 2. Subject commits sealed choice + 2 reasons
        code, body = _http_json(
            url,
            method="POST",
            headers=self._subject_headers(),
            body={
                "action": "subject_commit_blind_choice",
                "choice_id": choice_id,
                "selected_choice_id": "noodle",
                "reasons": [
                    {"facet": "budget", "text_cn": "烤肉人均偏贵"},
                    {"facet": "queue", "text_cn": "周末等位太久"},
                ],
                "operation_key": "la-bc-subj-1",
            },
        )
        self.assertEqual(code, 200)
        seal_sha = body["receipt"]["subject_commit_sha256"]
        self.assertEqual(len(seal_sha), 64)

        # 3. Partner GET before submitting -> strict SHA-256 pre-reveal seal masking & zero-write!
        before_seal_get = _snapshot_tree(self.root)
        code_p, body_p = _http_json(url)
        self.assertEqual(code_p, 200)
        self.assertEqual(_snapshot_tree(self.root), before_seal_get)
        bc_p = next(c for c in body_p["data"]["blind_choices"] if c["choice_id"] == choice_id)
        self.assertTrue(bc_p["subject_committed"])
        self.assertEqual(bc_p["subject_commit_sha256"], seal_sha)
        self.assertIsNone(bc_p["subject_choice_id"])
        self.assertIsNone(bc_p["initial_subject_choice_id"])
        self.assertEqual(bc_p["subject_reasons"], [])
        self.assertEqual(bc_p["seal_status"], "SEALED_UNTIL_PARTNER_SUBMITS")

        # 4. Partner submits -> auto-REVEALED with commit_seal_verified == True
        code, body = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "partner_submit_blind_choice",
                "choice_id": choice_id,
                "selected_choice_id": "bbq",
                "partner_reason_cn": "难得想吃热腾腾烤肉",
                "operation_key": "la-bc-part-1",
            },
        )
        self.assertEqual(code, 200)
        bc_rev = body["receipt"]["blind_choice"]
        self.assertEqual(bc_rev["status"], "REVEALED")
        self.assertTrue(bc_rev["commit_seal_verified"])
        self.assertEqual(bc_rev["initial_subject_choice_id"], "noodle")

        # 5. Partner discusses -> Subject resolves ACCEPT_CHOICE_KEEP_REASONS
        code, _ = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "partner_discuss_blind_choice",
                "choice_id": choice_id,
                "message_cn": "今晚我特别想吃烤肉，我们可以早点去排队",
                "insists_on_partner_choice": True,
                "operation_key": "la-bc-disc-1",
            },
        )
        self.assertEqual(code, 200)

        code, body = _http_json(
            url,
            method="POST",
            headers=self._subject_headers(),
            body={
                "action": "resolve_blind_choice",
                "choice_id": choice_id,
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "resolution_note_cn": "可以照你选的去吃烤肉，但我担心的价格和等位问题还在",
                "operation_key": "la-bc-res-1",
            },
        )
        self.assertEqual(code, 200)
        resolved_bc = body["receipt"]["blind_choice"]
        self.assertFalse(resolved_bc["judgment_flipped"])
        self.assertEqual(resolved_bc["adopted_action_choice_id"], "bbq")
        self.assertEqual(resolved_bc["preferred_judgment_choice_id"], "noodle")
        self.assertEqual(len(resolved_bc["retained_reservations"]), 2)

        # 6. Growth stances (1 private + 1 shared) & Pillar D object + category + capability gap
        code, body = _http_json(
            url,
            method="POST",
            headers=self._subject_headers(),
            body={
                "action": "register_unformed_topic",
                "topic_cn": "私人日记习惯",
                "shared_with_partner": False,
                "operation_key": "la-st-priv-1",
            },
        )
        self.assertEqual(code, 200)
        priv_stance_id = body["receipt"]["stance_id"]

        code, body = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "register_unformed_topic",
                "topic_cn": "慢焙茎茶风味",
                "operation_key": "la-st-pub-1",
            },
        )
        self.assertEqual(code, 200)
        pub_stance_id = body["receipt"]["stance_id"]

        code, body = _http_json(
            url,
            method="POST",
            headers=self._subject_headers(),
            body={
                "action": "register_or_repurpose_object",
                "object_id": "tin_cookies_01",
                "object_kind": "cookie_tin",
                "base_name_cn": "旧饼干铁盒",
                "custom_label_cn": "旧票根与草稿盒",
                "emptied_from_consumable": True,
                "visibility": "shared",
                "operation_key": "la-obj-1",
            },
        )
        self.assertEqual(code, 200)

        code, body = _http_json(
            url,
            method="POST",
            headers=self._subject_headers(),
            body={
                "action": "create_custom_category",
                "title_cn": "以后想重新看看",
                "scope_kinds": ["reading_booklet", "note"],
                "visibility": "shared",
                "operation_key": "la-cat-1",
            },
        )
        self.assertEqual(code, 200)
        cat_id = body["receipt"]["category"]["category_id"]

        code, _ = _http_json(
            url,
            method="POST",
            headers=self._subject_headers(),
            body={
                "action": "record_blocked_attempt",
                "goal_cn": "把收音机磁带放进铁盒播放",
                "attempted_action_cn": "点击播放旧磁带音频",
                "blocker_code": "MISSING_PRIMITIVE:AUDIO_PLAYBACK",
                "visibility": "shared",
                "operation_key": "la-gap-1",
            },
        )
        self.assertEqual(code, 200)

        # Partner GET sees shared stance, object, category, capability gap, and NOT private stance!
        before_pop = _snapshot_tree(self.root)
        code_get, body_get = _http_json(url)
        self.assertEqual(code_get, 200)
        self.assertEqual(_snapshot_tree(self.root), before_pop)
        partner_sids = {s["stance_id"] for s in body_get["data"]["growth_stances"]}
        self.assertIn(pub_stance_id, partner_sids)
        self.assertNotIn(priv_stance_id, partner_sids)
        self.assertGreaterEqual(body_get["data"]["capability_gaps_summary"]["total_clusters"], 1)

        # 7. Unified Forget Barrier hides choice_id, pub_stance_id, tin_cookies_01, and cat_id
        f_code, f_res = rlf.submit_forget_command(
            self.root,
            command_text="忘掉这次选择、主见、铁盒和自建分类",
            operation_id="op_forget_v09_la_http_01",
            explicit_scope=[choice_id, pub_stance_id, "tin_cookies_01", cat_id],
            explicit_confirmation=True,
            now_iso=T_PLUS_1H_ISO,
        )
        self.assertEqual(f_code, 200, f_res)
        before_fb = _snapshot_tree(self.root)
        code_fb, body_fb = _http_json(url, headers={"X-Phone-Capability": self.cap_token})
        self.assertEqual(code_fb, 200)
        self.assertEqual(_snapshot_tree(self.root), before_fb)
        self.assertFalse(any(c["choice_id"] == choice_id for c in body_fb["data"]["blind_choices"]))
        self.assertFalse(any(s["stance_id"] == pub_stance_id for s in body_fb["data"]["growth_stances"]))
        self.assertFalse(any(o["object_id"] == "tin_cookies_01" for o in body_fb["data"]["repurposed_objects"]))
        self.assertFalse(any(c["category_id"] == cat_id for c in body_fb["data"]["custom_categories"]))

    def test_04_excessive_nesting_depth_fails_closed_400(self) -> None:
        deep: Any = {"clear_reservations": True}
        for _ in range(13):
            deep = {"nested": deep}

        url_la = self.base_url + la_view.ROUTE_V09_LIVING_AGENCY
        code_la, body_la = _http_json(
            url_la,
            method="POST",
            headers=self._partner_headers(),
            body={"action": "register_unformed_topic", "topic_cn": "深度校验", "extra": deep},
        )
        self.assertEqual(code_la, 400)
        self.assertEqual(body_la.get("reason_code"), "INVALID_FIELDS")

        with self.assertRaises(phone_tool.PhoneToolRefused) as pt_err:
            phone_tool._prepare_v09_action(
                "v09_living_agency_act",
                {"action": "v09_living_agency_act", "payload": {"action": "open_blind_choice", "extra": deep}},
                "op-depth-1",
            )
        self.assertEqual(pt_err.exception.reason_code, "INVALID_PAYLOAD")


class V09WorldCommitmentsHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-wc-http-")
        self.root = Path(self._tmp.name)
        self.csrf = "csrf-v09-world-commitments"
        self.srv, self.thread, self.base_url, self.cap_token = _start_phone_http_server(
            self.root, csrf_token=self.csrf, clock_iso=T0_ISO
        )

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=3)
        self._tmp.cleanup()

    def _partner_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}

    def _subject_headers(self) -> dict[str, str]:
        return {
            "X-CSRF-Token": self.csrf,
            "X-Phone-Capability": self.cap_token,
        }

    def test_01_zero_write_get_and_query_guard(self) -> None:
        before = _snapshot_tree(self.root)
        code, body = _http_json(self.base_url + wc_view.ROUTE_V09_WORLD_COMMITMENTS)
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["schema"], "phone.v09.world_commitments.v1")
        self.assertEqual(body["view"], "partner")
        self.assertTrue(body["zero_write"])
        self.assertEqual(body["llm_calls"], 0)
        data = body["data"]
        for key in (
            "dynamic_catalog",
            "npcs",
            "commitments",
            "active_commitments",
            "venue_notices",
            "observed_venue_notices",
            "min_next_due_at",
        ):
            self.assertIn(key, data)
        self.assertEqual(len(data["npcs"]), 4)
        self.assertEqual(_snapshot_tree(self.root), before)
        self.assertEqual(_sidecars(self.root), [])

        # Subject capability GET is also 100% zero-write
        code_s, body_s = _http_json(
            self.base_url + wc_view.ROUTE_V09_WORLD_COMMITMENTS,
            headers={"X-Phone-Capability": self.cap_token},
        )
        self.assertEqual(code_s, 200)
        self.assertEqual(body_s["view"], "subject")
        self.assertEqual(_snapshot_tree(self.root), before)

        # Unknown query string rejected with 400
        code_q, body_q = _http_json(self.base_url + wc_view.ROUTE_V09_WORLD_COMMITMENTS + "?bad=1")
        self.assertEqual(code_q, 400)
        self.assertEqual(body_q.get("reason_code"), "UNKNOWN_QUERY")
        self.assertEqual(_snapshot_tree(self.root), before)

    def test_02_csrf_capability_smuggling_and_actor_guards(self) -> None:
        url = self.base_url + wc_view.ROUTE_V09_WORLD_COMMITMENTS

        # Missing CSRF -> 403
        code, _ = _http_json(
            url,
            method="POST",
            body={"action": "place_partner_reservation", "venue_id": "bookstore", "title_cn": "海风诗集"},
        )
        self.assertEqual(code, 403)

        # Bad capability -> 403
        code, _ = _http_json(
            url,
            method="POST",
            headers={"X-CSRF-Token": self.csrf, "X-Phone-Capability": "wrong-token-1234567890"},
            body={"action": "place_partner_reservation", "venue_id": "bookstore", "title_cn": "海风诗集"},
        )
        self.assertEqual(code, 403)

        # Smuggled actor_side -> 400
        code, body = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "place_partner_reservation",
                "actor_side": "subject",
                "venue_id": "bookstore",
                "title_cn": "海风诗集",
            },
        )
        self.assertEqual(code, 400)
        self.assertEqual(body.get("reason_code"), "SMUGGLED_ACTOR_FIELD")

        # Subject-only action without capability -> 403 FORBIDDEN_ACTOR_SIDE
        for subj_action in (
            "create_commitment",
            "contact_npc_or_venue",
            "advance_due_commitments",
            "generate_world_content_batch",
        ):
            code, body = _http_json(
                url,
                method="POST",
                headers=self._partner_headers(),
                body={"action": subj_action, "venue_id": "bookstore"},
            )
            self.assertEqual(code, 403, subj_action)
            self.assertEqual(body.get("reason_code"), "FORBIDDEN_ACTOR_SIDE", subj_action)

        # Nested mixed-case retroactive / custom_code / code / actor smuggling inside catalog_item_spec -> 400 before engine write!
        before_smuggle = _snapshot_tree(self.root)
        code_m1, body_m1 = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "place_partner_reservation",
                "venue_id": "bookstore",
                "title_cn": "海风诗集",
                "catalog_item_spec": {"Retroactive_Event": "smuggled", "custom_code": "evil"},
                "operation_key": "op-smuggle-retro-1",
            },
        )
        self.assertEqual(code_m1, 400)
        self.assertEqual(body_m1.get("reason_code"), "RETROACTIVE_FABRICATION_FORBIDDEN")

        for bad_spec_key in ("custom_code", "Custom_Code", "code", "Code", "Grant_Capability"):
            code_m2, body_m2 = _http_json(
                url,
                method="POST",
                headers=self._partner_headers(),
                body={
                    "action": "place_partner_reservation",
                    "venue_id": "bookstore",
                    "title_cn": "海风诗集",
                    "catalog_item_spec": {"name_cn": "海风诗集", bad_spec_key: "evil"},
                    "operation_key": f"op-smuggle-code-{bad_spec_key}",
                },
            )
            self.assertEqual(code_m2, 400, bad_spec_key)
            self.assertEqual(body_m2.get("reason_code"), "FORBIDDEN_CLIENT_KEY", bad_spec_key)

        code_m3, body_m3 = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "place_partner_reservation",
                "venue_id": "bookstore",
                "title_cn": "海风诗集",
                "catalog_item_spec": {"Author_Side": "subject"},
                "operation_key": "op-smuggle-actor-1",
            },
        )
        self.assertEqual(code_m3, 400)
        self.assertEqual(body_m3.get("reason_code"), "SMUGGLED_ACTOR_FIELD")
        self.assertEqual(_snapshot_tree(self.root), before_smuggle)

        # Legitimate nested catalog_item_spec with price still succeeds -> 200
        code_ok, body_ok = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "place_partner_reservation",
                "venue_id": "bookstore",
                "title_cn": "海风修缮笔记",
                "arrival_due_at": T_PLUS_25H_ISO,
                "catalog_item_spec": {
                    "template_id": "reading_booklet",
                    "store_id": "bookstore",
                    "name_cn": "海风修缮笔记",
                    "price": 32,
                    "stock_initial": 1,
                },
                "operation_key": "op-legit-spec-price-1",
            },
        )
        self.assertEqual(code_ok, 200, body_ok)
        self.assertTrue(body_ok["ok"])

    def test_03_partner_and_subject_full_commitment_and_catalog_flow(self) -> None:
        url = self.base_url + wc_view.ROUTE_V09_WORLD_COMMITMENTS

        # Partner places reservation + idempotent replay
        code, body = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "place_partner_reservation",
                "venue_id": "bookstore",
                "title_cn": "旧灯塔植物图鉴",
                "arrival_due_at": "2026-11-22T09:00:00+08:00",
                "operation_key": "op-rsv-001",
            },
        )
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "applied")
        self.assertEqual(body["llm_calls"], 0)
        cid = body["receipt"]["commitment_id"]
        self.assertTrue(cid)

        code_dup, body_dup = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "place_partner_reservation",
                "venue_id": "bookstore",
                "title_cn": "旧灯塔植物图鉴",
                "arrival_due_at": "2026-11-22T09:00:00+08:00",
                "operation_key": "op-rsv-001",
            },
        )
        self.assertEqual(code_dup, 200)
        self.assertEqual(body_dup["status"], "already_applied")

        # Partner proposes shared appointment
        code_apt, body_apt = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "propose_shared_appointment",
                "venue_id": "cafe",
                "title_cn": "周日下午焙茶小聚",
                "window_start_at": "2026-11-22T14:00:00+08:00",
                "window_end_at": "2026-11-22T15:30:00+08:00",
                "operation_key": "op-apt-001",
            },
        )
        self.assertEqual(code_apt, 200)
        self.assertTrue(body_apt["ok"])

        # Partner generates deterministic shop arrivals (llm_calls == 0)
        code_gen, body_gen = _http_json(
            url,
            method="POST",
            headers=self._partner_headers(),
            body={
                "action": "operator_generate_shop_arrivals",
                "store_id": "bookstore",
                "trigger_reason": "SEASON_SHIFT",
                "operation_key": "op-gen-001",
            },
        )
        self.assertEqual(code_gen, 200)
        self.assertEqual(body_gen["llm_calls"], 0)
        cat_items = body_gen["data"]["dynamic_catalog"]["items"]
        self.assertGreaterEqual(len(cat_items), 1)
        gen_item_id = cat_items[0]["catalog_item_id"]

        # Subject advances due commitments -> reservation arrives
        code_adv, body_adv = _http_json(
            url,
            method="POST",
            headers=self._subject_headers(),
            body={
                "action": "advance_due_commitments",
                "target_now_iso": T_PLUS_25H_ISO,
            },
        )
        self.assertEqual(code_adv, 200)
        self.assertGreaterEqual(body_adv["receipt"]["advanced_count"], 1)

        # Subject direct contact with smuggled retroactive key -> 400
        code_bad, body_bad = _http_json(
            url,
            method="POST",
            headers=self._subject_headers(),
            body={
                "action": "contact_npc_or_venue",
                "venue_id": "bookstore",
                "contact_action": "observe_or_greet",
                "extra_params": {"subject_mood": "excited"},
            },
        )
        self.assertEqual(code_bad, 400)
        self.assertEqual(body_bad.get("reason_code"), "RETROACTIVE_FABRICATION_FORBIDDEN")

        # Subject picks up arrived reservation -> PICKED_UP
        code_pick, body_pick = _http_json(
            url,
            method="POST",
            headers=self._subject_headers(),
            body={
                "action": "contact_npc_or_venue",
                "venue_id": "bookstore",
                "contact_action": "pickup_reservation",
                "commitment_id": cid,
                "operation_key": "op-pick-001",
            },
        )
        self.assertEqual(code_pick, 200)
        self.assertEqual(body_pick["receipt"]["commitment"]["state"], "PICKED_UP")

        # Zero-write GET on populated root
        before_pop = _snapshot_tree(self.root)
        code_get, body_get = _http_json(url)
        self.assertEqual(code_get, 200)
        self.assertEqual(_snapshot_tree(self.root), before_pop)
        self.assertTrue(any(c["commitment_id"] == cid for c in body_get["data"]["commitments"]))

        # Unified Forget Barrier hides blocked commitment & catalog item
        f_code, f_res = rlf.submit_forget_command(
            self.root,
            command_text="删掉该预订与目录记录",
            operation_id="op_forget_v09_wc_http_01",
            explicit_scope=[cid, gen_item_id],
            explicit_confirmation=True,
            now_iso=T_PLUS_25H_ISO,
        )
        self.assertEqual(f_code, 200, f_res)
        before_fb = _snapshot_tree(self.root)
        code_fb, body_fb = _http_json(url)
        self.assertEqual(code_fb, 200)
        self.assertEqual(_snapshot_tree(self.root), before_fb)
        self.assertFalse(any(c["commitment_id"] == cid for c in body_fb["data"]["commitments"]))
        self.assertFalse(
            any(i["catalog_item_id"] == gen_item_id for i in body_fb["data"]["dynamic_catalog"]["items"])
        )

    def test_02_world_commitments_excessive_nesting_depth_fails_closed_400(self) -> None:
        deep: Any = {"subject_mood": "anxious"}
        for _ in range(13):
            deep = {"nested": deep}

        url_wc = self.base_url + wc_view.ROUTE_V09_WORLD_COMMITMENTS
        code_wc, body_wc = _http_json(
            url_wc,
            method="POST",
            headers=self._partner_headers(),
            body={"action": "operator_generate_shop_arrivals", "store_id": "cafe", "extra": deep},
        )
        self.assertEqual(code_wc, 400)
        self.assertEqual(body_wc.get("reason_code"), "INVALID_FIELDS")


class NyairoDayV09HeartbeatTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-hb-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_zero_spin_idle_skip_and_exact_wake_on_due_and_rain(self) -> None:
        # 1. Empty root: 0 disk writes
        before_empty = _snapshot_tree(self.root)
        res_empty = nyairo_day._maybe_tick_v09_world_commitments(self.root, T0_ISO)
        self.assertIsNone(res_empty)
        self.assertEqual(_snapshot_tree(self.root), before_empty)

        tick_empty = nyairo_day.run_v03_background_clock_tick(
            self.root, now_iso=T0_ISO, force_init=False
        )
        self.assertEqual(tick_empty["status"], "skipped_uninitialized")
        self.assertEqual(tick_empty["llm_calls"], 0)
        self.assertEqual(_snapshot_tree(self.root), before_empty)

        # 2. Seed one reservation due at T+24h and one weather-sensitive outdoor stall due at T+30h
        rsv = wce.create_commitment(
            self.root,
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "潮汐笔记选集",
                "arrival_due_at": "2026-11-22T10:00:00+08:00",
                "current_weather": "clear",
            },
            actor_side="subject",
            operation_key="hb-rsv-1",
            now_iso=T0_ISO,
        )
        stall = wce.create_commitment(
            self.root,
            {
                "kind": "INDEPENDENT_NPC_PLAN",
                "effect_kind": "OUTDOOR_STALL",
                "venue_id": "porch_market",
                "title_cn": "周末露台旧物摊",
                "start_at": "2026-11-22T16:00:00+08:00",
                "end_at": "2026-11-22T19:00:00+08:00",
                "weather_sensitive": True,
            },
            actor_side="subject",
            operation_key="hb-stall-1",
            now_iso=T0_ISO,
        )
        rsv_id = rsv["commitment_id"]
        stall_id = stall["commitment_id"]

        # 3. Idle tick at T+1h with unchanged weather -> O(1) skip, 0 disk writes!
        before_idle = _snapshot_tree(self.root)
        res_idle = nyairo_day._maybe_tick_v09_world_commitments(
            self.root, T_PLUS_1H_ISO, weather_changed=False, new_weather="clear"
        )
        self.assertIsNone(res_idle)
        self.assertEqual(_snapshot_tree(self.root), before_idle)

        # 4. Rain arrival at T+1h -> wakes immediately and cancels outdoor stall!
        res_rain = nyairo_day._maybe_tick_v09_world_commitments(
            self.root, T_PLUS_1H_ISO, weather_changed=True, new_weather="rain"
        )
        self.assertIsNotNone(res_rain)
        assert res_rain is not None
        self.assertEqual(res_rain["llm_calls"], 0)
        self.assertGreaterEqual(res_rain["affected_count"], 1)
        snap_after_rain = wce.read_world_commitments_zero_write(
            self.root, actor_side="partner", now_iso=T_PLUS_1H_ISO
        )
        by_id = {c["commitment_id"]: c for c in snap_after_rain["commitments"]}
        self.assertEqual(by_id[stall_id]["state"], "CANCELLED_BY_WEATHER")
        self.assertEqual(by_id[rsv_id]["state"], "ORDERED")

        # Subsequent idle tick with same rain weather -> 0 disk writes again!
        before_rain_idle = _snapshot_tree(self.root)
        res_rain_idle = nyairo_day._maybe_tick_v09_world_commitments(
            self.root, T_PLUS_1H_ISO, weather_changed=False, new_weather="rain"
        )
        self.assertIsNone(res_rain_idle)
        self.assertEqual(_snapshot_tree(self.root), before_rain_idle)

        # 5. Due arrival at T+25h via full run_v03_background_clock_tick -> wakes & advances!
        tick_due = nyairo_day.run_v03_background_clock_tick(
            self.root, now_iso=T_PLUS_25H_ISO, force_init=False
        )
        self.assertTrue(tick_due["ok"])
        self.assertEqual(tick_due["llm_calls"], 0)
        self.assertIn("v09_world_commitments", tick_due["slices_ticked"])
        snap_after_due = wce.read_world_commitments_zero_write(
            self.root, actor_side="partner", now_iso=T_PLUS_25H_ISO
        )
        by_id_due = {c["commitment_id"]: c for c in snap_after_due["commitments"]}
        self.assertEqual(by_id_due[rsv_id]["state"], "ARRIVED_HOLDING")


class V09DailyLoopBridgeAndMemorySinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-loop-bridge-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_full_v09_scene(self) -> dict[str, str]:
        # 1. Arrived reservation (ARRIVED_HOLDING)
        rsv = wce.create_commitment(
            self.root,
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "海边植物手绘集",
                "arrival_due_at": T0_ISO,
                "hold_hours": 72,
            },
            actor_side="subject",
            operation_key="seed-rsv-1",
            now_iso="2026-11-20T10:00:00+08:00",
        )
        wce.advance_due_commitments(self.root, now_iso=T_PLUS_1H_ISO)
        cid = rsv["commitment_id"]

        # 2. Reading booklet in dynamic catalog + matching UNFORMED stance
        bk_reg = wcc.register_catalog_batch(
            self.root,
            items=[
                {
                    "template_id": "reading_booklet",
                    "store_id": "bookstore",
                    "name_cn": "慢焙茎茶手记",
                    "price": 380,
                    "stock_initial": 3,
                    "pages_count": 12,
                    "topic_tags": ["慢焙茎茶", "焙茶"],
                    "argument_thesis_cn": "慢火焙制的茎茶回甘较慢但苦韵偏清",
                    "sample_excerpt_cn": "浅火翻炒三轮后静置半日。",
                }
            ],
            operation_key="seed-bk-1",
            now_iso=T0_ISO,
        )
        self.assertEqual(len(bk_reg["registered_items"]), 1, bk_reg)
        st = tse.apply_true_stance_command(
            self.root,
            {
                "action": "register_unformed_topic",
                "topic_cn": "慢焙茎茶",
                "shared_with_partner": True,
                "operation_key": "seed-st-1",
            },
            actor_side="subject",
            now_iso=T0_ISO,
        )
        stance_id = st["stance_id"]

        # 3. Open blind choice awaiting subject
        bc = tse.apply_true_stance_command(
            self.root,
            {
                "action": "open_blind_choice",
                "topic_cn": "周末去哪走走",
                "options": [
                    {"option_id": "pier", "label_cn": "防波堤吹风"},
                    {"option_id": "book", "label_cn": "旧书铺翻画册"},
                ],
                "operation_key": "seed-bc-1",
            },
            actor_side="partner",
            now_iso=T0_ISO,
        )
        choice_id = bc["choice_id"]

        # 4. Emptied reusable cookie tin without custom label yet
        pot.register_or_repurpose_object(
            self.root,
            object_id="tin_empty_01",
            object_kind="cookie_tin",
            base_name_cn="旧饼干铁盒",
            emptied_from_consumable=True,
            visibility="shared",
            actor_side="subject",
            operation_key="seed-tin-1",
            now_iso=T0_ISO,
        )
        return {"cid": cid, "stance_id": stance_id, "choice_id": choice_id, "tin_id": "tin_empty_01"}

    def test_01_producer_surfaces_all_four_v09_candidates_zero_write_and_respects_illness(self) -> None:
        ids = self._seed_full_v09_scene()

        before = _snapshot_tree(self.root)
        collected = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T_PLUS_1H_ISO)
        self.assertEqual(_snapshot_tree(self.root), before)
        self.assertTrue(collected["zero_write"])
        self.assertEqual(collected["llm_calls"], 0)

        families = {d["action_family"] for d in collected["descriptors"].values()}
        for expected in (
            "pickup_arrived_commitment",
            "commit_blind_choice",
            "reflect_unformed_stance",
            "tidy_custom_shelf_or_tin",
        ):
            self.assertIn(expected, families, f"Missing v0.9 candidate family: {expected}")

        # Acute illness blocks strenuous outing (pickup_arrived_commitment) AND preserves v03_idle:daydream_window!
        eco_path = self.root / "phone" / eco_bridge.STATE_FILENAME
        eco_path.parent.mkdir(parents=True, exist_ok=True)
        eco_path.write_text(
            json.dumps(
                {
                    "schema": "world.v03.economy_illness_bridge.v1",
                    "revision": 1,
                    "updated_at": T_PLUS_1H_ISO,
                    "illness": {
                        "phase": "acute_illness",
                        "severity": 0.72,
                        "rest_active": False,
                        "episode_id": "ep_acute_test_01",
                    },
                    "economy": {
                        "living_floor_cents": 3000,
                        "available_balance_cents": 8000,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        before_ill = _snapshot_tree(self.root)
        collected_ill = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T_PLUS_1H_ISO)
        self.assertEqual(_snapshot_tree(self.root), before_ill)
        surviving_fams = {d["action_family"] for d in collected_ill["descriptors"].values()}
        surviving_refs = {d["source_ref"] for d in collected_ill["descriptors"].values()}
        self.assertNotIn("pickup_arrived_commitment", surviving_fams)
        self.assertIn("v03_idle:daydream_window", surviving_refs)
        self.assertTrue(
            any(
                f.get("reason_code") == "ILLNESS_BLOCKS_STRENUOUS_OUTING"
                and f"v09_commitment:pickup:{ids['cid']}" == f.get("source_ref")
                for f in collected_ill["gating"]["filtered_candidates"]
            )
        )

    def test_02_receipt_gated_settlement_and_lived_memory_sink_domains(self) -> None:
        ids = self._seed_full_v09_scene()

        # Non-APPLIED / partner actor -> 0 optimistic writes!
        before_refuse = _snapshot_tree(self.root)
        r_unk = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "action_kind": "PICKUP_ARRIVED_COMMITMENT",
                "commitment_id": ids["cid"],
                "venue_id": "bookstore",
                "actor_side": "subject",
                "effect_state": "UNKNOWN",
                "receipt_ref": "rcpt_unk_1",
            },
            now_iso=T_PLUS_1H_ISO,
        )
        self.assertFalse(r_unk["settled"])
        self.assertEqual(r_unk["optimistic_writes"], 0)
        self.assertEqual(_snapshot_tree(self.root), before_refuse)

        r_part = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "action_kind": "PICKUP_ARRIVED_COMMITMENT",
                "commitment_id": ids["cid"],
                "venue_id": "bookstore",
                "actor_side": "partner",
                "effect_state": "APPLIED",
                "receipt_ref": "rcpt_part_1",
            },
            now_iso=T_PLUS_1H_ISO,
        )
        self.assertFalse(r_part["settled"])
        self.assertEqual(r_part["reason_code"], "UNAUTHORIZED_CONSEQUENCE_ACTOR")
        self.assertEqual(_snapshot_tree(self.root), before_refuse)

        # 1. APPLIED pickup_arrived_commitment -> PICKED_UP + lived_memory_sink (v09_commitment) + idempotent replay
        ev_pickup = {
            "action_kind": "PICKUP_ARRIVED_COMMITMENT",
            "action_family": "pickup_arrived_commitment",
            "commitment_id": ids["cid"],
            "venue_id": "bookstore",
            "actor_side": "subject",
            "effect_state": "APPLIED",
            "receipt_ref": "rcpt_v09_pickup_01",
            "operation_key": "op_settle_pickup_01",
        }
        res_pick = loop_bridge.settle_v02_v03_round_consequences(self.root, ev_pickup, now_iso=T_PLUS_1H_ISO)
        self.assertTrue(res_pick["ok"])
        self.assertTrue(res_pick["settled"])
        self.assertIn("v09_world_commitments", res_pick["slices_updated"])
        self.assertEqual(res_pick["details"]["v09_commitment"]["commitment"]["state"], "PICKED_UP")
        sink_cmt = res_pick["details"]["lived_memory_sink"]
        self.assertTrue(sink_cmt["ok"])
        self.assertEqual(sink_cmt["domain"], "v09_commitment")
        self.assertTrue(sink_cmt["recorded"])

        res_pick_dup = loop_bridge.settle_v02_v03_round_consequences(self.root, ev_pickup, now_iso=T_PLUS_1H_ISO)
        self.assertTrue(res_pick_dup["already_applied"])

        # 2. APPLIED reflect_unformed_stance -> FORMED + lived_memory_sink (v09_stance)
        res_st = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "action_kind": "REFLECT_UNFORMED_STANCE",
                "stance_id": ids["stance_id"],
                "provenance_tier": "firsthand_lived",
                "target_status": "FORMED",
                "stance_summary_cn": "亲口尝过慢焙茎茶，确实偏苦但尾韵干净",
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "receipt_ref": "rcpt_tea_tasting_01",
                "operation_key": "op_settle_stance_01",
            },
            now_iso=T_PLUS_1H_ISO,
        )
        self.assertTrue(res_st["settled"])
        self.assertIn("v09_true_stance.growth_stances", res_st["slices_updated"])
        self.assertEqual(res_st["details"]["v09_growth_stance"]["status"], "FORMED")
        sink_st = res_st["details"]["lived_memory_sink"]
        self.assertTrue(sink_st["ok"])
        self.assertEqual(sink_st["domain"], "v09_stance")
        self.assertTrue(sink_st["recorded"])

        # 3. APPLIED tidy_custom_shelf_or_tin -> labels empty tin cleanly
        res_tidy = loop_bridge.settle_v02_v03_round_consequences(
            self.root,
            {
                "action_kind": "TIDY_CUSTOM_SHELF_OR_TIN",
                "taxonomy_action": "attach_label_to_object",
                "object_id": ids["tin_id"],
                "custom_label_cn": "旧票根盒",
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "receipt_ref": "rcpt_tidy_01",
                "operation_key": "op_settle_tidy_01",
            },
            now_iso=T_PLUS_1H_ISO,
        )
        self.assertTrue(res_tidy["settled"])
        self.assertIn("v09_personal_objects_taxonomy", res_tidy["slices_updated"])
        self.assertEqual(res_tidy["details"]["v09_taxonomy"]["object"]["custom_label_cn"], "旧票根盒")


class V09LivedContextBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-lcb-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_calm_gate_on_blank_root_and_zero_write(self) -> None:
        missing_root = self.root / "never_created"
        out = lcb.build_lived_turn_context(missing_root, cfg={}, incoming_text="晚饭吃什么", now_iso=T0_ISO)
        self.assertEqual(out, "")
        self.assertFalse(missing_root.exists())

        # Empty initialized dir without v0.9 files -> _format_v09_living_agency_micro returns ""
        empty_root = self.root / "empty_existing"
        empty_root.mkdir()
        before = _snapshot_tree(empty_root)
        self.assertIsNone(
            lcb._read_v09_living_agency_and_world_zero_write(empty_root, include_private=True, now_iso=T0_ISO)
        )
        self.assertEqual(lcb._format_v09_living_agency_micro(None), "")
        self.assertEqual(_snapshot_tree(empty_root), before)

    def test_02_renders_v09_agency_world_line_boost_barrier_and_zero_pronoun(self) -> None:
        (self.root / "phone").mkdir(parents=True, exist_ok=True)
        # Seed resolved blind choice with retained reservations
        bc = tse.apply_true_stance_command(
            self.root,
            {
                "action": "open_blind_choice",
                "topic_cn": "晚饭吃什么",
                "options": [
                    {"option_id": "a", "label_cn": "家常面"},
                    {"option_id": "b", "label_cn": "排队烤肉"},
                ],
                "operation_key": "lcb-bc-1",
            },
            actor_side="partner",
            now_iso=T0_ISO,
        )
        cid = bc["choice_id"]
        tse.apply_true_stance_command(
            self.root,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": cid,
                "selected_choice_id": "a",
                "reasons": [{"facet": "price", "text_cn": "价格偏贵"}, {"facet": "wait", "text_cn": "等位太久"}],
                "operation_key": "lcb-bc-2",
            },
            actor_side="subject",
            now_iso=T0_ISO,
        )
        tse.apply_true_stance_command(
            self.root,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": cid,
                "selected_choice_id": "b",
                "partner_reason_cn": "想吃烤肉",
                "operation_key": "lcb-bc-3",
            },
            actor_side="partner",
            now_iso=T0_ISO,
        )
        tse.apply_true_stance_command(
            self.root,
            {
                "action": "resolve_blind_choice",
                "choice_id": cid,
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "operation_key": "lcb-bc-4",
            },
            actor_side="subject",
            now_iso=T0_ISO,
        )

        # Seed formed + unformed stance
        st = tse.apply_true_stance_command(
            self.root,
            {"action": "register_unformed_topic", "topic_cn": "慢焙茎茶", "shared_with_partner": True, "operation_key": "lcb-st-1"},
            actor_side="subject",
            now_iso=T0_ISO,
        )
        sid = st["stance_id"]
        tse.apply_true_stance_command(
            self.root,
            {
                "action": "advance_stance_from_experience",
                "stance_id": sid,
                "target_status": "FORMED",
                "provenance_tier": "firsthand_lived",
                "receipt_ref": "rcpt_tea_01",
                "stance_summary_cn": "觉得偏苦",
                "operation_key": "lcb-st-2",
            },
            actor_side="subject",
            now_iso=T0_ISO,
        )

        # Seed arrived reservation + labelled tin
        rsv = wce.create_commitment(
            self.root,
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "旧书册子",
                "arrival_due_at": T0_ISO,
            },
            actor_side="subject",
            operation_key="lcb-rsv-1",
            now_iso="2026-11-20T10:00:00+08:00",
        )
        wce.advance_due_commitments(self.root, now_iso=T_PLUS_1H_ISO)
        rsv_id = rsv["commitment_id"]

        pot.register_or_repurpose_object(
            self.root,
            object_id="tin_01",
            object_kind="cookie_tin",
            base_name_cn="旧饼干盒",
            custom_label_cn="票根盒",
            emptied_from_consumable=True,
            visibility="shared",
            actor_side="subject",
            operation_key="lcb-tin-1",
            now_iso=T0_ISO,
        )
        pot.store_item_in_object(
            self.root,
            object_id="tin_01",
            item_label_cn="电车旧票根",
            actor_side="subject",
            operation_key="lcb-tin-2",
            now_iso=T0_ISO,
        )

        # Check zero-write full context render + <= 1800 + <= 160 micro section + situational boost +2
        before = _snapshot_tree(self.root)
        diag = lcb.measure_context_budget_breakdown(
            self.root,
            incoming_text="晚饭选择和旧书铺预订的册子怎么样了",
            now_iso=T_PLUS_1H_ISO,
        )
        self.assertEqual(_snapshot_tree(self.root), before)
        rendered = diag["rendered_text"]
        self.assertLessEqual(len(rendered), lcb.MAX_LIVED_CONTEXT_CHARS)
        self.assertIn("v09_agency_world_line", diag["kept_section_names"])
        self.assertLessEqual(diag["per_section_chars"]["v09_agency_world_line"], 160)
        self.assertIn("共同选择", rendered)
        self.assertEqual(rendered.count("\u5979"), 0)
        self.assertEqual(rendered.count("\u4ed6"), 0)
        self.assertEqual(rendered.count("\u5343\u4ee3"), 0)

        self.assertEqual(lcb._situation_boost("v09_agency_world_line", rendered, "晚饭怎么选择"), 2)
        self.assertEqual(lcb._situation_boost("v09_agency_world_line", rendered, "旧书铺预订到了吗"), 2)
        self.assertEqual(lcb._situation_boost("v09_agency_world_line", rendered, "饼干盒标签怎么贴"), 2)
        self.assertEqual(lcb._situation_boost("v09_agency_world_line", rendered, ""), 0)

        # Forget barrier strips all blocked v0.9 records cleanly!
        f_code, f_res = rlf.submit_forget_command(
            self.root,
            command_text="忘记全部测试条目",
            operation_id="op_forget_v09_lcb_01",
            explicit_scope=[cid, sid, rsv_id, "tin_01"],
            explicit_confirmation=True,
            now_iso=T_PLUS_1H_ISO,
        )
        self.assertEqual(f_code, 200, f_res)
        before_fb = _snapshot_tree(self.root)
        diag_fb = lcb.measure_context_budget_breakdown(
            self.root,
            incoming_text="晚饭选择和旧书铺预订的册子怎么样了",
            now_iso=T_PLUS_1H_ISO,
        )
        self.assertEqual(_snapshot_tree(self.root), before_fb)
        self.assertNotIn("晚饭吃什么", diag_fb["rendered_text"])
        self.assertNotIn("慢焙茎茶", diag_fb["rendered_text"])
        self.assertNotIn("旧书册子", diag_fb["rendered_text"])
        self.assertNotIn("票根盒", diag_fb["rendered_text"])

    def test_03_observed_venue_notice_text_cn_surfaces_in_lived_context(self) -> None:
        (self.root / "phone").mkdir(parents=True, exist_ok=True)
        # Seed an outdoor stall, rain-cancel it so 0 active commitments remain + real text_cn notice posted
        wce.create_commitment(
            self.root,
            {
                "kind": "INDEPENDENT_NPC_PLAN",
                "effect_kind": "OUTDOOR_STALL",
                "venue_id": "porch_market",
                "title_cn": "街角屋檐手作小摊",
                "start_at": T_PLUS_1H_ISO,
                "end_at": T_PLUS_25H_ISO,
                "current_weather": "clear",
            },
            actor_side="subject",
            operation_key="lcb-stall-rain-1",
            now_iso=T0_ISO,
        )
        changed = wce.notify_world_condition_changed(
            self.root,
            condition_kind="weather",
            new_value="rain",
            now_iso=T0_ISO,
        )
        self.assertEqual(changed["affected_count"], 1)
        posted_notices = [n for n in changed["notices_posted"] if n.get("venue_id") == "porch_market"]
        self.assertTrue(posted_notices)
        notice_text_cn = str(posted_notices[0].get("text_cn") or "")
        self.assertTrue(notice_text_cn)

        # Before subject visits porch_market: notice is unobserved -> stays silent (epistemic gate)
        before_obs = _snapshot_tree(self.root)
        unobs_ctx = lcb.build_lived_turn_context(
            self.root,
            cfg={},
            incoming_text="街角手作小摊今天开吗",
            now_iso=T0_ISO,
        )
        self.assertEqual(_snapshot_tree(self.root), before_obs)
        self.assertNotIn(notice_text_cn[:6], unobs_ctx)

        # Subject visits porch_market -> observed_venue_notices now holds real text_cn notice
        wce.contact_npc_or_venue(
            self.root,
            venue_id="porch_market",
            contact_action="observe_or_greet",
            actor_side="subject",
            now_iso=T0_ISO,
        )
        before_ctx = _snapshot_tree(self.root)
        diag = lcb.measure_context_budget_breakdown(
            self.root,
            incoming_text="街角手作小摊今天开吗",
            now_iso=T0_ISO,
        )
        self.assertEqual(_snapshot_tree(self.root), before_ctx)
        rendered = diag["rendered_text"]
        self.assertIn("v09_agency_world_line", diag["kept_section_names"])
        self.assertLessEqual(len(rendered), lcb.MAX_LIVED_CONTEXT_CHARS)
        self.assertLessEqual(diag["per_section_chars"]["v09_agency_world_line"], 160)
        self.assertIn(notice_text_cn[:8], rendered)
        self.assertEqual(rendered.count("\u5979"), 0)
        self.assertEqual(rendered.count("\u4ed6"), 0)
        self.assertEqual(rendered.count("\u5343\u4ee3"), 0)


class FrozenInvariantsAndHygieneTest(unittest.TestCase):
    def test_01_all_nine_sealed_files_byte_identical(self) -> None:
        for rel_path, expected_sha in SEALED_FILES_SHA256.items():
            actual = hashlib.sha256((REPO_ROOT / rel_path).read_bytes()).hexdigest()
            self.assertEqual(actual, expected_sha, f"Sealed file drifted: {rel_path}")

    def test_02_frozen_quantitative_invariants_and_zero_pronoun_law(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(loop_bridge.LIFE_UPDATE_SURFACES, 37)
        plugin_mod = _load_chiyo_plugin_module()
        self.assertEqual(len(plugin_mod._PHONE_AND_WORLD_GUIDANCE), 1199)
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)
        self.assertEqual(len(soak_runner.GET_ROUTES), 24)
        self.assertEqual(phone_server.PhoneHTTPServer.request_queue_size, 128)
        self.assertIn("v09_stance", lived_memory_sink.VALID_LIVED_DOMAINS)
        self.assertIn("v09_commitment", lived_memory_sink.VALID_LIVED_DOMAINS)

        lcb_text = (REPO_ROOT / "chiyo_bundle" / "lived_context_bridge.py").read_text(encoding="utf-8")
        self.assertEqual(lcb_text.count("\u5979"), 0)
        self.assertEqual(lcb_text.count("\u4ed6"), 0)
        self.assertEqual(lcb_text.count("\u5343\u4ee3"), 0)

    def test_03_owned_files_hygiene_pure_lf_utf8_no_bom_zero_emoji(self) -> None:
        for rel in OWNED_FILES:
            raw = (REPO_ROOT / rel).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM in {rel}")
            self.assertNotIn(b"\r\n", raw, f"CRLF found in {rel}")
            text = raw.decode("utf-8")
            emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"unicode.So emojis found in {rel}: {emojis[:5]}")


if __name__ == "__main__":
    unittest.main()

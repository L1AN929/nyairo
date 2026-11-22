#!/usr/bin/env python3
"""Real loopback HTTP acceptance for v0.8 waves J to Q (neighborhood -> RMW slices).

Coverage
--------
* Window-safe (always runs on Windows/WSL): every HTTP view module and the
  ``server`` route constants import cleanly, and pure view reads on an empty
  root stay 100% zero-write.
* ``@POSIX_ONLY`` loopback integration (run inside WSL), one real
  ``autonomy_rhythm_fixture`` HTTP server:
  - all eleven GET routes answer 200 without touching a byte, report
    ``llm_calls == 0`` where they expose it, refuse a wrong capability with
    403 and refuse every POST that forgets CSRF with 403;
  - Wave J/K ``/api/phone/v04/neighborhood``: season weather, the subject-only
    bookstore door and a real plant watering;
  - Wave L ``/api/phone/v04/elastic-play``: distinct annotations, private two
    phrasings and a subject-only prepared surprise;
  - Wave M ``/api/phone/v08/living-traces`` + ``/api/phone/emotion/tension``:
    scalding-tea refusal, a private corner name and cross-store
    ``SOURCE_GONE``;
  - Wave N ``/api/phone/v08/emergent-echoes``: private ritual curation and the
    strict no habit/streak counter boundary;
  - Waves O/P/Q: real ``operation_key`` replay idempotency on the slices that
    expose it (stance, sensory cards, adult intimacy) plus honest read-only /
    revision-boundary assertions for the slices that do not.

Run (Windows): ``python -B -m unittest tests.test_v08_http_waves_j_to_q -v``.
Run (WSL):     ``python -B -m unittest tests.test_v08_http_waves_j_to_q -v``.
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
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import adult_intimacy_view as aiv  # noqa: E402
from components.phone import autonomy_rhythm_view as arv  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import emotional_tension_view as etv  # noqa: E402
from components.phone import sensory_character_view as scv  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import somatic_comfort_view as so_view  # noqa: E402
from components.phone import stance_containment_view as scv_stance  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402
from components.phone import v04_neighborhood_view as nb_view  # noqa: E402
from components.phone import v08_emergent_echoes_view as echoes_view  # noqa: E402
from components.phone import v08_living_traces_view as traces_view  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "v03-http-cap-token-20261112"

GET_ROUTES = (
    "/api/phone/v04/neighborhood",
    "/api/phone/v04/elastic-play",
    "/api/phone/v08/living-traces",
    "/api/phone/v08/emergent-echoes",
    "/api/phone/emotion/tension",
    "/api/phone/stance/containment",
    "/api/phone/autonomy/rhythm",
    "/api/phone/body/somatic",
    "/api/phone/intimacy/adult",
    "/api/phone/character/pack",
    "/api/phone/body/sensory",
)

#: ``/api/phone/body/somatic`` is a read-only door; every other route has a POST.
POST_ROUTES = tuple(path for path in GET_ROUTES if path != "/api/phone/body/somatic")

READ_ACTION_ROUTES = {
    "v04_neighborhood": "/api/phone/v04/neighborhood",
    "v04_elastic_play": "/api/phone/v04/elastic-play",
    "v08_living_traces": "/api/phone/v08/living-traces",
    "v08_emergent_echoes": "/api/phone/v08/emergent-echoes",
    "emotional_tension": "/api/phone/emotion/tension",
    "stance_containment": "/api/phone/stance/containment",
    "autonomy_rhythm": "/api/phone/autonomy/rhythm",
    "body_somatic": "/api/phone/body/somatic",
    "adult_intimacy": "/api/phone/intimacy/adult",
    "character_pack": "/api/phone/character/pack",
    "body_sensory": "/api/phone/body/sensory",
}

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX domain sockets (run inside WSL)",
)


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    """Full size + mtime + content fingerprint of every entry under ``root``."""

    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        # The ``data`` directory's own mtime refreshes whenever a read-only
        # SQLite WAL connection touches ``life_supply.sqlite``; every regular
        # file inside it stays byte-identical. Skip only that directory-level
        # entry, keep the strict per-file fingerprint for all business dirs.
        if path.is_dir() and path.name == "data":
            continue
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


class WindowSafeModulesAndReadsTest(unittest.TestCase):
    """Runs on Windows and WSL: imports and empty-root pure reads only."""

    def test_00_view_modules_and_route_constants_importable(self) -> None:
        from components.phone import server

        self.assertEqual(
            server.ROUTE_V08_LIVING_TRACES, "/api/phone/v08/living-traces"
        )
        self.assertEqual(
            server.ROUTE_V08_EMERGENT_ECHOES, "/api/phone/v08/emergent-echoes"
        )
        for action, route in READ_ACTION_ROUTES.items():
            self.assertEqual(phone_tool.READ_ACTIONS.get(action), route, action)

        for callable_ref in (
            nb_view.read_v04_neighborhood_view,
            nb_view.mutate_v04_neighborhood_view,
            ep_view.read_v04_elastic_play_view,
            ep_view.mutate_v04_elastic_play_view,
            traces_view.read_v08_living_traces_view,
            traces_view.mutate_v08_living_traces_view,
            echoes_view.read_v08_emergent_echoes_view,
            echoes_view.mutate_v08_emergent_echoes_view,
            etv.read_emotional_tension,
            etv.apply_emotional_tension_action,
            scv_stance.read_stance_containment,
            scv_stance.apply_stance_containment_action,
            arv.read_autonomy_rhythm_state,
            arv.apply_autonomy_rhythm_action,
            so_view.read_state,
            aiv.read_adult_intimacy,
            aiv.apply_adult_intimacy_action,
            scv.read_character_pack_state,
            scv.read_sensory_cards_state,
        ):
            self.assertTrue(callable(callable_ref))

    def test_01_empty_root_pure_reads_are_zero_write(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="v08-http-waves-read-"))
        try:
            before = _snapshot(root)
            nb_view.read_v04_neighborhood_view(
                root, now_iso="2026-11-13T11:00:00+08:00"
            )
            ep_view.read_v04_elastic_play_view(root)
            traces_view.read_v08_living_traces_view(
                root, now_iso="2026-11-13T11:00:00+08:00"
            )
            echoes_view.read_v08_emergent_echoes_view(
                root, now_iso="2026-11-13T11:00:00+08:00"
            )
            etv.read_emotional_tension(root, now="2026-11-13T11:00:00+08:00")
            scv_stance.read_stance_containment(
                root, now="2026-11-13T11:00:00+08:00"
            )
            arv.read_autonomy_rhythm_state(root)
            so_view.read_state(root)
            aiv.read_adult_intimacy(root, now="2026-11-13T11:00:00+08:00")
            scv.read_character_pack_state(root)
            scv.read_sensory_cards_state(root)
            self.assertEqual(_snapshot(root), before, "empty-root reads must not write")
        finally:
            shutil.rmtree(root, ignore_errors=True)


@POSIX_ONLY
class V08HTTPWavesJtoQTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls.root = Path(tempfile.mkdtemp(prefix="v08-http-waves-"))
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-13T11:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        shutil.rmtree(cls.root, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        # One frozen business clock: bookstore opens at 11:00 and the scalding tea
        # stays hot for the whole test, so refusals are deterministic.
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 13, 11, 0, tzinfo=timezone(timedelta(hours=8))
        )

    def _call(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        csrf: bool = False,
        capability: str | None = None,
    ) -> tuple[int, dict]:
        data = (
            None
            if body is None
            else json.dumps(body, ensure_ascii=False).encode("utf-8")
        )
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    # -- Wave zero: every GET is zero-write, CSRF and capability stay hard -----
    def test_00_all_get_routes_zero_write_and_guards(self) -> None:
        before = _snapshot(Path(self.stack.root))
        for path in GET_ROUTES:
            code, res = self._call("GET", path)
            self.assertEqual(code, 200, (path, res))
            self.assertTrue(res.get("ok"), (path, res))
            for block in (res, res.get("data") or {}):
                if isinstance(block, dict) and "llm_calls" in block:
                    self.assertEqual(block["llm_calls"], 0, path)

            # A wrong credential never degrades into a partner read.
            cbad, _ = self._call("GET", path, capability="wrong-token")
            self.assertEqual(cbad, 403, path)

        # A POST without CSRF is refused before any body is trusted.
        for path in POST_ROUTES:
            cno, _ = self._call("POST", path, {"action": "probe_no_csrf"})
            self.assertEqual(cno, 403, path)

        self.assertEqual(_snapshot(Path(self.stack.root)), before,
                         "GET/POST guards must not touch a byte")

    # -- Wave J/K: neighborhood weather, bookstore door & plant care -----------
    def test_01_wave_jk_neighborhood(self) -> None:
        code, res = self._call(
            "POST",
            "/api/phone/v04/neighborhood",
            {"action": "set_local_weather", "weather": "clear", "season": "winter"},
            csrf=True,
        )
        self.assertEqual(code, 200, res)
        self.assertTrue(res.get("ok"), res)

        code_bad, res_bad = self._call(
            "POST",
            "/api/phone/v04/neighborhood",
            {"action": "set_local_weather", "weather": "clear", "season": "monsoon"},
            csrf=True,
        )
        self.assertEqual(code_bad, 400, res_bad)
        self.assertEqual(res_bad.get("reason_code"), "INVALID_FIELDS")

        # Bookstore is the subject's own door: subject 200, partner 403.
        code_sub, res_sub = self._call(
            "POST",
            "/api/phone/v04/neighborhood",
            {
                "action": "bookstore_service",
                "service": "browse_book",
                "book_id": "book_botanical_plates",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(code_sub, 200, res_sub)
        self.assertTrue(res_sub.get("ok"), res_sub)

        code_partner, res_partner = self._call(
            "POST",
            "/api/phone/v04/neighborhood",
            {
                "action": "bookstore_service",
                "service": "browse_book",
                "book_id": "book_botanical_plates",
            },
            csrf=True,
        )
        self.assertEqual(code_partner, 403, res_partner)

        code_water, res_water = self._call(
            "POST",
            "/api/phone/v04/neighborhood",
            {"action": "water_or_move_plant", "operation": "water", "water_amount": 80},
            csrf=True,
        )
        self.assertEqual(code_water, 200, res_water)
        self.assertTrue(res_water.get("ok"), res_water)

    # -- Wave L: elastic play distinct notes, private drafts & surprise --------
    def test_02_wave_l_elastic_play(self) -> None:
        play = "/api/phone/v04/elastic-play"
        code_s, res_s = self._call(
            "POST",
            play,
            {
                "action": "share_distinct_annotation",
                "material_title": "海堤漫步录",
                "page": 42,
                "noticed_detail_cn": "她注意到潮水退去后留在堤缝里的一层白盐",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(code_s, 200, res_s)
        code_p, res_p = self._call(
            "POST",
            play,
            {
                "action": "share_distinct_annotation",
                "material_title": "海堤漫步录",
                "page": 42,
                "noticed_detail_cn": "他记住的是结尾那句没说完的话",
            },
            csrf=True,
        )
        self.assertEqual(code_p, 200, res_p)

        code_get, res_get = self._call("GET", play)
        self.assertEqual(code_get, 200, res_get)
        pairs = (res_get.get("data") or {}).get("distinct_annotation_pairs") or []
        self.assertEqual(len(pairs), 1, res_get)
        self.assertIs(pairs[0].get("comprehension_score_exists"), False)

        # Subject-only phrasings with an imagined reply never reach the partner.
        imagined = "TA 大概会回一句：好，你慢慢来。"
        code_tw, res_tw = self._call(
            "POST",
            play,
            {
                "action": "save_two_phrasings",
                "context_note": "想先说第一稿",
                "phrasing_a": "我今天想先把作品写完。",
                "phrasing_b": "今天让我先把手上的东西收个尾，好吗？",
                "chosen": "undecided",
                "imagined_reply_cn": imagined,
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(code_tw, 200, res_tw)

        code_pget, res_pget = self._call("GET", play)
        self.assertEqual(code_pget, 200, res_pget)
        partner_data = res_pget.get("data") or {}
        self.assertEqual(partner_data.get("private_two_phrasings"), [])
        self.assertIs(partner_data.get("private_two_phrasings_withheld"), True)
        dumped = json.dumps(partner_data, ensure_ascii=False)
        self.assertNotIn(imagined, dumped)
        self.assertNotIn("imagined_reply_cn", dumped)

        surprise = {
            "action": "prepare_or_offer_surprise",
            "stage": "prepare",
            "surprise_id": "surp-http-ginkgo",
            "kind": "pressed_leaf_card",
            "title": "夹在书里的银杏书签",
            "content": "等你不忙的时候再看",
        }
        code_prep, res_prep = self._call(
            "POST", play, surprise, csrf=True, capability=CAP_TOKEN
        )
        self.assertEqual(code_prep, 200, res_prep)
        code_forbid, _ = self._call("POST", play, surprise, csrf=True)
        self.assertEqual(code_forbid, 403)

    # -- Wave M: living traces, scalding tea, private corner & SOURCE_GONE -----
    def test_03_wave_m_matter_and_tension(self) -> None:
        traces = "/api/phone/v08/living-traces"
        code_intro, res_intro = self._call(
            "POST",
            traces,
            {
                "action": "compose_open_act",
                "intent_cn": "沏了一杯热茶放在餐桌上",
                "steps": [
                    {
                        "op": "INTRODUCE_ITEM",
                        "object_id": "hot_tea_http_01",
                        "label_cn": "刚沏的热茶",
                        "item_kind": "drink",
                        "target_parent_id": "dining_table",
                        "portions": 2,
                        "thermal": "hot",
                        "boil_scalding": True,
                    }
                ],
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(code_intro, 200, res_intro)
        tea_id = ((res_intro.get("data") or {}).get("step_results") or [{}])[0].get(
            "object_id"
        )
        self.assertEqual(tea_id, "hot_tea_http_01", res_intro)

        # Drinking too early is an honest 409, not a silent sip.
        code_sip, res_sip = self._call(
            "POST",
            traces,
            {
                "action": "compose_open_act",
                "intent_cn": "立刻喝一口",
                "steps": [{"op": "CONSUME_PORTION", "object_id": tea_id}],
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(code_sip, 409, res_sip)
        self.assertEqual(res_sip.get("reason_code"), "DRINK_SCALDING_WAIT")

        corner = {
            "action": "name_personal_corner",
            "zone_id": "window_side",
            "nickname_cn": "雨棚",
            "shared_with_partner": False,
        }
        code_name, res_name = self._call(
            "POST", traces, corner, csrf=True, capability=CAP_TOKEN
        )
        self.assertEqual(code_name, 200, res_name)
        code_corner_partner, _ = self._call("POST", traces, corner, csrf=True)
        self.assertEqual(code_corner_partner, 403)

        code_pget, res_pget = self._call("GET", traces)
        self.assertEqual(code_pget, 200, res_pget)
        self.assertNotIn("雨棚", json.dumps(res_pget, ensure_ascii=False))

        code_sget, res_sget = self._call("GET", traces, capability=CAP_TOKEN)
        self.assertEqual(code_sget, 200, res_sget)
        self.assertIn("雨棚", json.dumps(res_sget, ensure_ascii=False))

        # A write that names a matter object already gone is 409 SOURCE_GONE.
        code_gone, res_gone = self._call(
            "POST",
            "/api/phone/emotion/tension",
            {
                "action": "add_missing_note",
                "kind": "FOUND",
                "source_ref": "matter:missing_01",
                "summary": "记下一个已经不在了的东西",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(code_gone, 409, res_gone)
        self.assertEqual(res_gone.get("reason_code"), "SOURCE_GONE")

    # -- Wave N: emergent echoes personal ritual stays private -----------------
    def test_04_wave_n_emergent_echoes(self) -> None:
        echoes = "/api/phone/v08/emergent-echoes"
        title = "泡茶前先把杯子烫一遍"
        ritual = {
            "action": "curate_personal_ritual",
            "ritual_op": "sketch_custom",
            "title_cn": title,
            "origin_trace_cn": "那天手边正好有热水，顺手烫了一下，茶确实更稳",
            "context_scope_cn": "晚上在家写东西时",
            "visibility": "private",
        }
        code_s, res_s = self._call(
            "POST", echoes, ritual, csrf=True, capability=CAP_TOKEN
        )
        self.assertEqual(code_s, 200, res_s)

        # The partner door cannot curate a private ritual.
        code_p, _ = self._call("POST", echoes, ritual, csrf=True)
        self.assertEqual(code_p, 403)

        # Forged habit / streak counters are refused at the gate.
        for counter in ("habit_score", "streak_days"):
            bad = dict(ritual)
            bad[counter] = 3
            code_bad, res_bad = self._call(
                "POST", echoes, bad, csrf=True, capability=CAP_TOKEN
            )
            self.assertEqual(code_bad, 400, (counter, res_bad))

        code_pget, res_pget = self._call("GET", echoes)
        self.assertEqual(code_pget, 200, res_pget)
        partner_data = res_pget.get("data") or {}
        self.assertEqual(partner_data.get("personal_rituals"), [])
        self.assertNotIn(title, json.dumps(partner_data, ensure_ascii=False))

        code_sget, res_sget = self._call("GET", echoes, capability=CAP_TOKEN)
        self.assertEqual(code_sget, 200, res_sget)
        self.assertIn(title, json.dumps(res_sget, ensure_ascii=False))

    # -- Waves O/P/Q: operation_key replay where it exists, honest bounds ------
    def test_05_wave_opq_rmw_and_operation_key(self) -> None:
        # 1. Stance/containment exposes operation_key: one write, one replay.
        stance_path = "/api/phone/stance/containment"
        stance = {
            "action": "propose_stance",
            "scope_ref": "book:http-idem",
            "source_revision": "v1",
            "page_or_anchor": "p1",
            "facet": "appearance",
            "text": "装帧很素净",
            "operation_key": "http-op-stance-1",
        }
        code_s1, res_s1 = self._call(
            "POST", stance_path, stance, csrf=True, capability=CAP_TOKEN
        )
        self.assertEqual(code_s1, 200, res_s1)
        self.assertEqual((res_s1.get("data") or {}).get("revision"), 1)
        code_s2, res_s2 = self._call(
            "POST", stance_path, stance, csrf=True, capability=CAP_TOKEN
        )
        self.assertEqual(code_s2, 200, res_s2)
        self.assertEqual(res_s2.get("status"), "already_applied", res_s2)
        self.assertIs((res_s2.get("data") or {}).get("replayed"), True)
        self.assertEqual((res_s2.get("data") or {}).get("revision"), 1)

        # 2. Body sensory card capture exposes operation_key: one card, no double.
        sensory = "/api/phone/body/sensory"
        card = {
            "action": "capture_sound_card",
            "title": "雨声记事",
            "impression_note": "雨点敲在窗框上很均匀",
            "shared": False,
            "operation_key": "http-op-card-1",
        }
        code_c1, res_c1 = self._call(
            "POST", sensory, card, csrf=True, capability=CAP_TOKEN
        )
        self.assertEqual(code_c1, 200, res_c1)
        card_id = ((res_c1.get("data") or {}).get("sound_card") or {}).get("card_id")
        self.assertTrue(card_id)
        code_c2, res_c2 = self._call(
            "POST", sensory, card, csrf=True, capability=CAP_TOKEN
        )
        self.assertEqual(code_c2, 200, res_c2)
        self.assertEqual(res_c2.get("status"), "already_applied", res_c2)
        self.assertIs((res_c2.get("data") or {}).get("replayed"), True)
        self.assertEqual(
            ((res_c2.get("data") or {}).get("sound_card") or {}).get("card_id"),
            card_id,
        )

        # 3. Adult intimacy: gate setup via HTTP, then one proposal replayed.
        adult = "/api/phone/intimacy/adult"
        gate_setup = (
            {
                "action": "configure_adult_gate",
                "adult_confirmed": True,
                "life_stage": "working_adult",
                "attested_by": "deployer",
                "feature_enabled": True,
                "deploy_tier_cap": "EXPLICIT",
                "solo_enabled": False,
            },
            {"action": "configure_body_profile", "preset": "neutral"},
            {
                "action": "confirm_preference_seed",
                "attested_by": "deployer",
                "desire_baseline": "moderate",
                "solo_inclination": "none",
                "stances": {
                    "KISSING": "YES",
                    "CARESSING_CLOTHED:user_to_subject": "YES",
                    "CARESSING_BARE:user_to_subject": "YES",
                    "UNDRESSING:user_to_subject": "YES",
                    "MANUAL:user_to_subject": "YES",
                    "ORAL:user_to_subject": "MAYBE",
                    "INTERCOURSE": "YES",
                    "ANAL:user_to_subject": "MAYBE",
                    "TOYS:user_to_subject": "MAYBE",
                },
            },
            {
                "action": "register_render_route",
                "route_id": "adult_verified_route",
                "max_tier": "EXPLICIT",
                "adult_content_allowed": True,
                "policy_basis": "deployer_verified_adult_route",
                "set_default": True,
            },
            {
                "action": "update_partner_limits",
                "allow_subject_initiation": True,
                "preferred_tier": "EXPLICIT",
                "stances": {
                    "KISSING": "YES",
                    "CARESSING_CLOTHED:user_to_subject": "YES",
                    "CARESSING_BARE:user_to_subject": "YES",
                    "UNDRESSING:user_to_subject": "YES",
                    "MANUAL:user_to_subject": "YES",
                    "ORAL:user_to_subject": "YES",
                    "INTERCOURSE": "YES",
                },
            },
        )
        for step in gate_setup:
            code_gate, res_gate = self._call("POST", adult, step, csrf=True)
            self.assertEqual(code_gate, 200, (step["action"], res_gate))

        proposal = {
            "action": "propose_scene",
            "operation_key": "http-op-scene-1",
            "proposer": "partner",
            "categories": ["KISSING"],
        }
        code_a1, res_a1 = self._call("POST", adult, proposal, csrf=True)
        self.assertEqual(code_a1, 200, res_a1)
        receipt1 = (res_a1.get("data") or {}).get("action_receipt") or {}
        permit_id = receipt1.get("permit_id")
        permit_revision = (res_a1.get("data") or {}).get("permit_revision")
        self.assertTrue(permit_id)

        code_a2, res_a2 = self._call("POST", adult, proposal, csrf=True)
        self.assertEqual(code_a2, 200, res_a2)
        receipt2 = (res_a2.get("data") or {}).get("action_receipt") or {}
        self.assertEqual(receipt2.get("permit_id"), permit_id)
        self.assertIs(receipt2.get("idempotent_replay"), True)
        self.assertEqual(
            (res_a2.get("data") or {}).get("permit_revision"), permit_revision
        )

        # 4. Autonomy: no operation_key surface; a repeated subject act is a new
        #    revision (never a silent duplicate), and a forged key is refused.
        rhythm = "/api/phone/autonomy/rhythm"
        code_r0, res_r0 = self._call("GET", rhythm)
        self.assertEqual(code_r0, 200, res_r0)
        self.assertEqual((res_r0.get("data") or {}).get("revision"), 0)

        topic = {
            "action": "hold_subject_topic",
            "topic_label": "画里的光",
            "private_note": "下次想聊",
        }
        code_r1, res_r1 = self._call(
            "POST", rhythm, topic, csrf=True, capability=CAP_TOKEN
        )
        self.assertEqual(code_r1, 200, res_r1)
        self.assertEqual((res_r1.get("data") or {}).get("revision"), 1)
        code_r2, res_r2 = self._call(
            "POST", rhythm, topic, csrf=True, capability=CAP_TOKEN
        )
        self.assertEqual(code_r2, 200, res_r2)
        self.assertEqual((res_r2.get("data") or {}).get("revision"), 2)

        code_rok, res_rok = self._call(
            "POST",
            rhythm,
            dict(topic, operation_key="unsupported-key"),
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(code_rok, 400, res_rok)

        # 5. Character pack: a real preset switch bumps the revision; there is no
        #    operation_key field on this door.
        pack = "/api/phone/character/pack"
        select = {"action": "select_preset", "preset_id": "haru"}
        code_h1, res_h1 = self._call("POST", pack, select, csrf=True)
        self.assertEqual(code_h1, 200, res_h1)
        self.assertEqual((res_h1.get("data") or {}).get("revision"), 1)
        self.assertEqual(
            ((res_h1.get("data") or {}).get("active_pack") or {}).get("display_name"),
            "晴",
        )
        code_h2, res_h2 = self._call("POST", pack, select, csrf=True)
        self.assertEqual(code_h2, 200, res_h2)
        self.assertEqual((res_h2.get("data") or {}).get("revision"), 2)

        code_pok, res_pok = self._call(
            "POST",
            pack,
            dict(select, operation_key="unsupported-key"),
            csrf=True,
        )
        self.assertEqual(code_pok, 400, res_pok)

        # 6. Body somatic is read-only: identical reads, and no POST door at all.
        code_b1, res_b1 = self._call("GET", "/api/phone/body/somatic")
        code_b2, res_b2 = self._call("GET", "/api/phone/body/somatic")
        self.assertEqual(code_b1, 200, res_b1)
        self.assertEqual(res_b1, res_b2)
        code_bp, _ = self._call(
            "POST", "/api/phone/body/somatic", {}, csrf=True, capability=CAP_TOKEN
        )
        # No POST branch is registered in ``do_POST`` for this read-only door,
        # so a real POST falls through to the generic 404 (not 405).
        self.assertEqual(code_bp, 404)


if __name__ == "__main__":
    unittest.main()

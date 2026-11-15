#!/usr/bin/env python3
"""Real loopback HTTP integration tests for all 3 v3.0 (0.3) phone routes + frozen anchor check."""
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

from components.phone import adult_intimacy_view as aiv  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import emotional_tension_view as etv  # noqa: E402
from components.phone import stance_containment_view as scv  # noqa: E402
from components.phone import v03_embodied_social_view as esv  # noqa: E402
from components.life.scripts import v03_clock_evaporation as clk  # noqa: E402
from components.world.scripts import v03_economy_illness_bridge as eco  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "v03-http-cap-token-20261112"

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


class FrozenAnchorIntegrityTest(unittest.TestCase):
    def test_00_frozen_life_anchors_byte_unchanged(self) -> None:
        for rel_path, expected_sha in FROZEN_ANCHORS.items():
            full = ROOT / rel_path
            actual = hashlib.sha256(full.read_bytes()).hexdigest()
            self.assertEqual(actual, expected_sha, f"Frozen anchor mutated: {rel_path}")


@POSIX_ONLY
class V03LoopbackHTTPIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls.root = Path(tempfile.mkdtemp(prefix="v03-http-routes-"))
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-12T12:00:00+08:00",
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
        etv.emotional_tension_state_path(self.stack.root).unlink(missing_ok=True)
        scv.stance_containment_state_path(self.stack.root).unlink(missing_ok=True)
        aiv.adult_intimacy_state_path(self.stack.root).unlink(missing_ok=True)
        clk.v03_clock_state_path(self.stack.root).unlink(missing_ok=True)
        eco.ecology_state_path(self.stack.root).unlink(missing_ok=True)
        esv.v03_embodied_social_state_path(self.stack.root).unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 12, 15, 0, tzinfo=timezone(timedelta(hours=8))
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
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_01_all_three_get_routes_zero_write_and_bad_capability_403(self) -> None:
        routes = [
            ("/api/phone/emotion/tension", "phone.emotional.tension.v1", etv.emotional_tension_state_path(self.stack.root)),
            ("/api/phone/stance/containment", "phone.stance.containment.v1", scv.stance_containment_state_path(self.stack.root)),
            ("/api/phone/intimacy/adult", "phone.adult.intimacy.v1", aiv.adult_intimacy_state_path(self.stack.root)),
        ]
        for path, schema, state_file in routes:
            self.assertFalse(state_file.exists())
            code, res = self._call("GET", path)
            self.assertEqual(code, 200, path)
            self.assertTrue(res["ok"])
            self.assertEqual(res["data"]["schema"], schema)
            self.assertEqual(res["data"]["revision"], 0)
            self.assertFalse(state_file.exists(), f"GET wrote state file on {path}")

            # Wrong capability header -> 403
            c403, _ = self._call("GET", path, capability="wrong-token")
            self.assertEqual(c403, 403)

            # Query string -> 400
            c400, _ = self._call("GET", path + "?extra=1")
            self.assertEqual(c400, 400)

    def test_02_emotional_tension_http_post_guards_and_repair_flow(self) -> None:
        # Missing CSRF -> 403
        c_no_csrf, _ = self._call("POST", "/api/phone/emotion/tension", {"action": "partner_arrive"})
        self.assertEqual(c_no_csrf, 403)

        # Partner cannot open_rupture -> 403
        c_forbid, _ = self._call(
            "POST",
            "/api/phone/emotion/tension",
            {
                "action": "open_rupture",
                "scope": "US",
                "description": "失约",
                "incident_ref": "inc-http-1",
                "incident_kind": "COMMITMENT_BROKEN",
            },
            csrf=True,
        )
        self.assertEqual(c_forbid, 403)

        # Subject opens rupture -> 200
        c_ok, r_ok = self._call(
            "POST",
            "/api/phone/emotion/tension",
            {
                "action": "open_rupture",
                "rupture_id": "rup-http-1",
                "scope": "US",
                "description": "约好的事没提前说",
                "incident_ref": "inc-http-1",
                "incident_kind": "COMMITMENT_BROKEN",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_ok, 200)
        self.assertEqual(r_ok["data"]["rupture_status"], "OPEN")

        # Partner apology does NOT resolve rupture -> stays OPEN
        c_ap, r_ap = self._call(
            "POST",
            "/api/phone/emotion/tension",
            {
                "action": "attempt_apology_or_gift_repair",
                "rupture_id": "rup-http-1",
                "attempt_kind": "APOLOGY",
                "note": "对不起",
            },
            csrf=True,
        )
        self.assertEqual(c_ap, 200)
        self.assertFalse(r_ap["data"]["resolved_rupture"])
        self.assertEqual(r_ap["data"]["rupture_status"], "OPEN")

        # Unknown field -> 400
        c_bad, _ = self._call(
            "POST",
            "/api/phone/emotion/tension",
            {"action": "partner_arrive", "bogus": 1},
            csrf=True,
        )
        self.assertEqual(c_bad, 400)

    def test_03_stance_containment_http_copresence_and_portion_conservation(self) -> None:
        # Partner brings 1-portion snack -> 200
        c_br, r_br = self._call(
            "POST",
            "/api/phone/stance/containment",
            {
                "action": "partner_bring_snack",
                "title": "热烤红薯",
                "kind": "snack_plate",
                "portions": 1,
                "initial_temperature": "hot",
            },
            csrf=True,
        )
        self.assertEqual(c_br, 200)
        dish_id = r_br["data"]["dish_id"]

        # Take 1 portion -> empty plate; taking a 2nd portion -> 409 DISH_EMPTY_NO_PORTIONS_LEFT
        c_t1, r_t1 = self._call(
            "POST",
            "/api/phone/stance/containment",
            {"action": "take_portion", "dish_id": dish_id, "consumer": "partner", "portions": 1},
            csrf=True,
        )
        self.assertEqual(c_t1, 200)
        self.assertTrue(r_t1["data"]["is_empty_plate"])

        c_t2, r_t2 = self._call(
            "POST",
            "/api/phone/stance/containment",
            {"action": "take_portion", "dish_id": dish_id, "consumer": "partner", "portions": 1},
            csrf=True,
        )
        self.assertEqual(c_t2, 409)
        self.assertEqual(r_t2["reason_code"], "DISH_EMPTY_NO_PORTIONS_LEFT")

    def test_04_adult_intimacy_http_safety_gate_solo_privacy_and_unconditional_revoke(self) -> None:
        # Monetized / gift consent field -> 403 MONETIZED_OR_GIFT_CONSENT_FORBIDDEN
        c_gift, r_gift = self._call(
            "POST",
            "/api/phone/intimacy/adult",
            {"action": "update_partner_limits", "preferred_tier": "SUGGESTIVE", "gift_id": "ring-1"},
            csrf=True,
        )
        self.assertEqual(c_gift, 403)
        self.assertEqual(r_gift["reason_code"], "MONEY_OR_GIFT_CANNOT_UNLOCK_CONSENT")

        # Partner without capability cannot evaluate subject solo window -> 403
        c_solo, _ = self._call(
            "POST",
            "/api/phone/intimacy/adult",
            {"action": "evaluate_solo_window", "slot_id": "2026-11-12-night", "co_presence": "NONE", "alone": True, "busy": False},
            csrf=True,
        )
        self.assertEqual(c_solo, 403)

        # Deployer confirms adult gate -> 200; partner revokes consent unconditionally -> 200
        c_gate, r_gate = self._call(
            "POST",
            "/api/phone/intimacy/adult",
            {
                "action": "configure_adult_gate",
                "adult_confirmed": True,
                "life_stage": "independent_adult",
                "attested_by": "deployer",
                "feature_enabled": True,
            },
            csrf=True,
        )
        self.assertEqual(c_gate, 200)
        self.assertTrue(r_gate["data"]["adult_gate"]["gate_open"])

        c_rev, r_rev = self._call(
            "POST",
            "/api/phone/intimacy/adult",
            {
                "action": "revoke_intimacy_consent",
                "by": "partner",
                "erase_memory": True,
                "close_adult_gate": True,
            },
            csrf=True,
        )
        self.assertEqual(c_rev, 200)
        self.assertFalse(r_rev["data"]["adult_gate"]["gate_open"])
        self.assertEqual(r_rev["data"]["action_receipt"]["relationship_penalty"], 0)

        # Unauthenticated partner cannot spoof by="subject" on revoke/erase -> 403 CAPABILITY_REQUIRED
        for spoof_action in ("erase_private_memory", "revoke_intimacy_consent"):
            c_spoof, r_spoof = self._call(
                "POST",
                "/api/phone/intimacy/adult",
                {"action": spoof_action, "by": "subject"},
                csrf=True,
            )
            self.assertEqual(c_spoof, 403, spoof_action)
            self.assertEqual(r_spoof["reason_code"], "CAPABILITY_REQUIRED")

        # Omitted "by" without capability binds to "partner", while valid capability allows by="subject"
        c_omitted, r_omitted = self._call(
            "POST",
            "/api/phone/intimacy/adult",
            {"action": "revoke_intimacy_consent"},
            csrf=True,
        )
        self.assertEqual(c_omitted, 200)
        self.assertEqual(r_omitted["data"]["action_receipt"]["by"], "partner")

        c_subj_ok, r_subj_ok = self._call(
            "POST",
            "/api/phone/intimacy/adult",
            {"action": "revoke_intimacy_consent", "by": "subject"},
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_subj_ok, 200)
        self.assertEqual(r_subj_ok["data"]["action_receipt"]["by"], "subject")

    def test_05_v03_clock_ecology_and_embodied_social_http_routes(self) -> None:
        new_routes = [
            ("/api/phone/v03/clock", clk.v03_clock_state_path(self.stack.root)),
            ("/api/phone/v03/ecology", eco.ecology_state_path(self.stack.root)),
            ("/api/phone/v03/embodied-social", esv.v03_embodied_social_state_path(self.stack.root)),
        ]
        for path, state_file in new_routes:
            self.assertFalse(state_file.exists(), path)
            code, res = self._call("GET", path)
            self.assertEqual(code, 200, path)
            self.assertTrue(res["ok"], path)
            self.assertIn(res["status"], ("ok", "applied"), path)
            self.assertIsInstance(res.get("data"), dict, path)
            self.assertFalse(state_file.exists(), f"GET mutated disk on {path}")

            c403, _ = self._call("GET", path, capability="wrong-token")
            self.assertEqual(c403, 403, path)
            c400, _ = self._call("GET", path + "?extra=1")
            self.assertEqual(c400, 400, path)

        # Partner without capability cannot call subject-only clock record_evaporable -> 403
        c_clk_forbid, _ = self._call(
            "POST",
            "/api/phone/v03/clock",
            {"action": "record_evaporable", "text": "刚才窗外飞过一只灰鸽子"},
            csrf=True,
        )
        self.assertEqual(c_clk_forbid, 403)

        # Subject with capability records evaporable thought -> 200
        c_clk_ok, r_clk_ok = self._call(
            "POST",
            "/api/phone/v03/clock",
            {"action": "record_evaporable", "text": "刚才窗外飞过一只灰鸽子", "ttl_seconds": 3600},
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(c_clk_ok, 200, r_clk_ok)
        self.assertEqual(r_clk_ok["data"]["action_receipt"]["no_tombstone"], True)

        # Partner can offer stipend escrow on /v03/ecology (within cap), cannot settle subject work -> 403
        c_eco_forbid, _ = self._call(
            "POST",
            "/api/phone/v03/ecology",
            {
                "action": "complete_independent_work",
                "job_kind": "proofreading",
                "work_ref": "w1",
                "amount_cents": 2000,
                "title": "校对补贴",
            },
            csrf=True,
        )
        self.assertEqual(c_eco_forbid, 403)

        c_eco_ok, r_eco_ok = self._call(
            "POST",
            "/api/phone/v03/ecology",
            {"action": "offer_stipend", "amount_cents": 1500, "note": "顺手买点热饮"},
            csrf=True,
        )
        self.assertEqual(c_eco_ok, 200, r_eco_ok)
        self.assertEqual(r_eco_ok["status"], "applied")

        # Partner can set presence on /v03/embodied-social, cannot record private style migration -> 403
        c_emb_forbid, _ = self._call(
            "POST",
            "/api/phone/v03/embodied-social",
            {
                "action": "record_style_migration",
                "title": "淡彩速写笔触",
                "origin_context": "alley_sketch",
                "stage": "NOTICED",
                "shared": False,
            },
            csrf=True,
        )
        self.assertEqual(c_emb_forbid, 403)

        c_emb_ok, r_emb_ok = self._call(
            "POST",
            "/api/phone/v03/embodied-social",
            {"action": "set_partner_presence", "presence_mode": "same_room_apart"},
            csrf=True,
        )
        self.assertEqual(c_emb_ok, 200, r_emb_ok)
        self.assertTrue(r_emb_ok["ok"])


if __name__ == "__main__":
    unittest.main()

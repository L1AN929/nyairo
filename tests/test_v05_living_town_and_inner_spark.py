#!/usr/bin/env python3
"""End-to-end tests for v0.5 Living Town (Ch.117-129), Inner Spark (Ch.102/109/111/114/130), HTTP server, phone_tool & brain context."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from chiyo_bundle import lived_context_bridge as lcb
from chiyo_bundle import phone_tool
from components.phone import server as phone_server
from components.phone import service as phone_service
from components.phone import v05_dreams_desires_view as spark_view
from components.phone import v05_living_town_view as town_view


def _snapshot_tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


class TestV05LivingTownAndInnerSpark(unittest.TestCase):
    def test_zero_write_get_both_views_on_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            before = _snapshot_tree(root)
            c1, b1 = town_view.read_v05_living_town_view(root, has_subject_capability=False, now_iso="2026-11-19T19:30:00+08:00")
            c2, b2 = spark_view.read_v05_inner_spark_view(root, has_subject_capability=False, now_iso="2026-11-19T19:30:00+08:00")
            after = _snapshot_tree(root)
            self.assertEqual(c1, 200)
            self.assertEqual(c2, 200)
            self.assertEqual(before, after)
            self.assertEqual(b1["llm_calls"], 0)
            self.assertEqual(b2["llm_calls"], 0)
            self.assertGreaterEqual(len(b1["regulars"]), 5)
            self.assertIn("elevation_map", b1)
            self.assertIn("alley_kitten", b1)
            self.assertIn("third_spaces", b1)

    def test_living_town_dual_map_regulars_favors_kitten_and_agency_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Partner cannot call subject-only actions (403)
            c_deny, b_deny = town_view.handle_v05_living_town_action(
                root,
                {"action": "set_subject_town_state", "ta_location_id": "river_pier"},
                has_subject_capability=False,
                now_iso="2026-11-19T19:30:00+08:00",
            )
            self.assertEqual(c_deny, 403)
            self.assertEqual(b_deny["reason_code"], "FORBIDDEN_ACTOR_SIDE")

            # Forged actor_side rejected (400)
            c_forge, _ = town_view.handle_v05_living_town_action(
                root,
                {"action": "greet_or_feed_kitten", "actor_side": "subject"},
                has_subject_capability=False,
                now_iso="2026-11-19T19:30:00+08:00",
            )
            self.assertEqual(c_forge, 400)

            # Subject sets TA out at coin_laundromat with solo_walk_lock=True
            c_subj, b_subj = town_view.handle_v05_living_town_action(
                root,
                {"action": "set_subject_town_state", "ta_location_id": "coin_laundromat", "solo_walk_lock": True},
                has_subject_capability=True,
                now_iso="2026-11-19T19:30:00+08:00",
            )
            self.assertEqual(c_subj, 200)
            self.assertEqual(b_subj["ta_presence"]["location_id"], "coin_laundromat")

            # Partner invites walk together -> TA respects solo_walk_lock and declines gently!
            c_inv, b_inv = town_view.handle_v05_living_town_action(
                root,
                {"action": "invite_walk_together", "target_spot_id": "hillside_overlook", "bringing_umbrella": True},
                has_subject_capability=False,
                now_iso="2026-11-19T19:35:00+08:00",
            )
            self.assertEqual(c_inv, 200)
            self.assertFalse(b_inv["action_receipt"]["accepted"])

            # Subject unlocks solo_walk_lock -> now invite succeeds & computes real slope walk!
            town_view.handle_v05_living_town_action(
                root,
                {"action": "set_subject_town_state", "solo_walk_lock": False},
                has_subject_capability=True,
                now_iso="2026-11-19T19:36:00+08:00",
            )
            c_inv2, b_inv2 = town_view.handle_v05_living_town_action(
                root,
                {"action": "invite_walk_together", "target_spot_id": "hillside_overlook", "bringing_umbrella": True},
                has_subject_capability=False,
                now_iso="2026-11-19T19:37:00+08:00",
            )
            self.assertEqual(c_inv2, 200)
            self.assertTrue(b_inv2["action_receipt"]["accepted"])
            self.assertEqual(b_inv2["partner_presence"]["status"], "walking_together")

            # Visit coin laundromat, greet kitten, log neighbor favor
            c_laund, b_laund = town_view.handle_v05_living_town_action(
                root,
                {"action": "visit_third_space", "spot_id": "coin_laundromat", "garment_name_cn": "风衣", "note_cn": "烘衣机轰隆响"},
                has_subject_capability=False,
                now_iso="2026-11-19T19:40:00+08:00",
            )
            self.assertEqual(c_laund, 200)
            c_kit, b_kit = town_view.handle_v05_living_town_action(
                root,
                {"action": "greet_or_feed_kitten", "interaction_kind": "feed", "nickname": "阿橘", "note_cn": "给了点小鱼干"},
                has_subject_capability=False,
                now_iso="2026-11-19T19:45:00+08:00",
            )
            self.assertEqual(c_kit, 200)
            self.assertEqual(b_kit["alley_kitten"]["nickname"], "阿橘")
            self.assertGreaterEqual(b_kit["alley_kitten"]["feeding_count"], 1)

            c_fav, b_fav = town_view.handle_v05_living_town_action(
                root,
                {"action": "log_neighbor_favor", "regular_id": "auntie_lin", "favor_kind": "return_umbrella", "note_cn": "把借的红伞还回去了"},
                has_subject_capability=False,
                now_iso="2026-11-19T19:50:00+08:00",
            )
            self.assertEqual(c_fav, 200)
            self.assertTrue(b_fav["action_receipt"]["ok"])

    def test_inner_spark_anti_puppeteer_anti_spoiler_frost_melt_and_dream_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Partner cannot create or resolve TA desires (403!)
            c_p_des, _ = spark_view.handle_v05_inner_spark_action(
                root,
                {"action": "record_subject_desire", "layer": "micro_craving", "text": "半夜想吃烤吐司"},
                has_subject_capability=False,
                now_iso="2026-11-19T08:00:00+08:00",
            )
            self.assertEqual(c_p_des, 403)

            # Subject creates one shared desire & one private desire
            c_s1, b_s1 = spark_view.handle_v05_inner_spark_action(
                root,
                {"action": "record_subject_desire", "layer": "micro_craving", "text": "想喝热姜茶", "shared": True},
                has_subject_capability=True,
                now_iso="2026-11-19T08:00:00+08:00",
            )
            self.assertEqual(c_s1, 200)
            desire_id = b_s1["action_receipt"]["desire"]["desire_id"]
            spark_view.handle_v05_inner_spark_action(
                root,
                {"action": "record_subject_desire", "layer": "long_aspiration", "text": "秘密心愿不公开", "shared": False},
                has_subject_capability=True,
                now_iso="2026-11-19T08:01:00+08:00",
            )

            # Partner sees shared desire, NEVER private desire, and can leave a gentle partner_note
            _, p_view = spark_view.read_v05_inner_spark_view(root, has_subject_capability=False, now_iso="2026-11-19T08:05:00+08:00")
            visible_texts = [d["text"] for d in p_view["desires"]["items"]]
            self.assertIn("想喝热姜茶", visible_texts)
            self.assertNotIn("秘密心愿不公开", visible_texts)

            c_resp, b_resp = spark_view.handle_v05_inner_spark_action(
                root,
                {"action": "respond_to_desire", "desire_id": desire_id, "partner_note": "晚上回屋一起煮一壶"},
                has_subject_capability=False,
                now_iso="2026-11-19T08:06:00+08:00",
            )
            self.assertEqual(c_resp, 200)

            # Subject tucks a secret page note on page 42 -> partner MUST NOT see spoiler at page 10!
            spark_view.handle_v05_inner_spark_action(
                root,
                {"action": "tuck_page_note", "book_title": "海边旧书", "target_page": 42, "note_text": "翻到这里给你留了一张干花书签", "current_page": 10},
                has_subject_capability=True,
                now_iso="2026-11-19T08:10:00+08:00",
            )
            _, p_before = spark_view.read_v05_inner_spark_view(root, has_subject_capability=False, now_iso="2026-11-19T08:11:00+08:00")
            self.assertNotIn("干花书签", json.dumps(p_before, ensure_ascii=False))

            # Advance reading page to 42 -> note naturally reveals!
            c_adv, b_adv = spark_view.handle_v05_inner_spark_action(
                root,
                {"action": "advance_reading_page", "book_title": "海边旧书", "new_page": 42},
                has_subject_capability=False,
                now_iso="2026-11-19T08:12:00+08:00",
            )
            self.assertEqual(c_adv, 200)
            self.assertIn("干花书签", json.dumps(b_adv["action_receipt"], ensure_ascii=False))

            # Window frost written at night -> visible at night, melts by noon sun (zero-write!)
            c_fr, b_fr = spark_view.handle_v05_inner_spark_action(
                root,
                {"action": "sketch_window_frost", "text": "晚安小雨", "weather": "rain", "temp_c": 5.0},
                has_subject_capability=False,
                now_iso="2026-11-19T22:00:00+08:00",
            )
            self.assertEqual(c_fr, 200)
            self.assertFalse(b_fr["window_frost"]["melted"])
            _, noon_view = spark_view.read_v05_inner_spark_view(root, has_subject_capability=False, now_iso="2026-11-20T13:00:00+08:00")
            self.assertTrue(noon_view["window_frost"]["melted"])

            # Object chemistry: half tangerine offering after refusal
            c_ch, b_ch = spark_view.handle_v05_inner_spark_action(
                root,
                {"action": "trigger_object_chemistry", "kind": "half_tangerine_offering", "params": {"refusal_lock_active": True}},
                has_subject_capability=False,
                now_iso="2026-11-19T22:05:00+08:00",
            )
            self.assertEqual(c_ch, 200)
            self.assertTrue(b_ch["recent_chemistry_sparks"])

    def test_http_server_phone_tool_and_lived_context_bridge_end_to_end(self) -> None:
        # Preserve frozen phone_tool invariant: len(LIFE_UPDATE_SURFACES) == 37
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)

        # phone_tool routing for v05 subject actions + partner impersonation refusal
        surf, path, body, cap = phone_tool._prepare_life_update(
            {"target_surface": "v04_neighborhood", "payload": {"action": "set_subject_town_state", "ta_location_id": "river_pier"}},
            "op_pt_v05_1",
        )
        self.assertEqual(surf, "v04_neighborhood")
        self.assertEqual(path, "/api/phone/v05/living-town")
        self.assertTrue(cap)
        self.assertEqual(body["operation_key"], "op_pt_v05_1")

        surf2, path2, body2, cap2 = phone_tool._prepare_life_update(
            {"target_surface": "v04_elastic_play", "payload": {"action": "record_subject_desire", "layer": "micro_craving", "text": "想吹风"}},
            "op_pt_v05_2",
        )
        self.assertEqual(surf2, "v04_elastic_play")
        self.assertEqual(path2, "/api/phone/v05/inner-spark")
        self.assertTrue(cap2)

        with self.assertRaises(phone_tool.PhoneToolRefused):
            phone_tool._prepare_life_update(
                {"target_surface": "v04_neighborhood", "payload": {"action": "move_partner_to_spot", "target_spot_id": "river_pier"}},
                "op_pt_v05_deny",
            )

        # Real HTTP Server roundtrip for /api/phone/v05/living-town & /api/phone/v05/inner-spark
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
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
            csrf = "test-v05-csrf-token"
            srv = phone_server.PhoneHTTPServer(("127.0.0.1", 0), service=svc, csrf_token=csrf)
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                base = f"http://127.0.0.1:{port}"
                # Zero-write GET check
                before = _snapshot_tree(root)
                for route in ("/api/phone/v05/living-town", "/api/phone/v05/inner-spark"):
                    with urllib.request.urlopen(base + route, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        doc = json.loads(resp.read().decode("utf-8"))
                        self.assertTrue(doc["ok"])
                        self.assertEqual(doc["llm_calls"], 0)
                self.assertEqual(before, _snapshot_tree(root))

                # Real POST partner action on living-town
                req = urllib.request.Request(
                    base + "/api/phone/v05/living-town",
                    data=json.dumps({"action": "move_partner_to_spot", "target_spot_id": "river_pier", "bringing_umbrella": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    doc = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(doc["partner_presence"]["location_id"], "river_pier")

                # Real POST window frost on inner-spark
                req2 = urllib.request.Request(
                    base + "/api/phone/v05/inner-spark",
                    data=json.dumps({"action": "sketch_window_frost", "text": "听雨写字", "weather": "rain", "temp_c": 6.0}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                    method="POST",
                )
                with urllib.request.urlopen(req2, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)

                # lived_context_bridge picks up 0.5 cues zero-write & <= 1800 chars!
                ctx = lcb.build_lived_turn_context(root, incoming_text="晚上你在哪儿", now_iso="2026-11-19T22:00:00+08:00")
                self.assertLessEqual(len(ctx), lcb.MAX_LIVED_CONTEXT_CHARS)
                self.assertIn("0.5小镇与心火线索", ctx)
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()

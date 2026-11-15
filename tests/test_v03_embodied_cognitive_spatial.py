#!/usr/bin/env python3
"""Comprehensive v0.3 integration & unit tests for S4 embodied, kitchen, two-hand interlock,
slow variables, dwell/HOST_GAP/glitches, and cognitive-spatial boundaries.

Covers:
1. Deliverable 1 (Ch. 06–07 & GAP-02/06 & ERR-03):
   - Partner RBAC on ``respond_contact`` (allowed when ``pending_initiator == "subject"``,
     forbidden ``403 FORBIDDEN_ACTOR_SIDE`` when ``pending_initiator == "partner"``);
   - ``UserAvatarProxy`` (``doorway_shoe_zone``, ``bed_side_user``, poses, ``expires_at`` timeout);
   - Active contact max ``1800s`` natural release;
   - Bed ``rumple_index``, ``tidy_bed``, and 12-minute (``720s``) half-life residual warmth cooling;
   - ``cps_`` session ID prefix, ``CoPresenceAdapter.liveness_probe``, and ``GAP-02`` cascade
     pausing ``ACTIVE`` ``ScenePermit`` in ``phone/adult_intimacy.json`` to ``PAUSED_PRESENCE_UNKNOWN``.
2. Deliverable 2 (Ch. 15–18 & GAP-08 & ERR-09/GAP-10):
   - Unified cross-module two-hand interlock between ``stance_containment_view`` and ``v03_embodied_social_view``;
   - Pen/stationery in ``ceramic_mug`` returning ``409 CUP_OCCUPIED_BY_STATIONERY`` across kitchen, somatic, and table drink flows;
   - ``somatic_comfort_view`` ``drink`` genuinely relieving ``body_lived_needs.thirst`` (while preserving unknown action guard);
   - Kitchen 6 zones (``chiyo_snack_drawer`` ``403 PRIVATE_REFUSED`` & ``GAP-10`` projection, ``sink`` ``dirty_dishes >= 3`` guard & ``wash_dishes``, ``KitchenAppliance`` microwave/kettle/stove);
   - ``GAP-08`` integer ``ConsumeReceipt(amount_mg, energy_mJ)`` + ``DiscardReceipt(energy_mJ=0)`` + Ch. 18 36h onigiri lifecycle.
3. Deliverable 3 (Ch. 01–03, 08, 21, 23–27, 30–35 & GAP-09 & ERR-10):
   - Ch. 01 non-numeric ``relationship_anchors``;
   - Ch. 02 & Ch. 26 town 3 slow lifecycles, ``NPCCommitmentProposal`` (``GAP-09``), multi-source rumor ``epistemic_basis``, bookstore card feedback, and NPC dual-field aliases (``label``/``name``, ``place_label``/``shop``, ``own_ongoing_task``/``current_activity``);
   - Ch. 03/08/21 ``life_dwell`` (``declared_seconds``, ``>15m`` ``HOST_GAP``, ``FLOW/STAGNANT/WEAVE``, 6 everyday glitches) wired into ``v03_clock_evaporation``;
   - Ch. 23/24/25/30/35 unread page guard (``409 VIEW_PAGE_NOT_OBTAINED``), non-xray closed drawer & ``ITEM_NOT_AT_EXPECTED_SPOT`` + scoped ``observe_current_room``, ``entryway_tray`` & cohabitation negotiation, hobby 3 axes, and ``everyday_desire_lifecycle`` (``turn_only`` fade-out + 6-concern conflict arbitration).
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components.life.scripts import (  # noqa: E402
    attention_view_projector,
    everyday_desire_lifecycle,
    life_attention_stance_containment,
    life_dwell,
    v03_clock_evaporation,
)
from components.phone import (  # noqa: E402
    somatic_comfort_view,
    stance_containment_view,
    v03_embodied_social_view,
)


def _seed_lived_needs(day_root: Path, *, hunger: float = 0.55, thirst: float = 0.65) -> Path:
    bln = somatic_comfort_view._load_bln_module()
    ln_path = somatic_comfort_view._lived_needs_file_path(day_root)
    ln_path.parent.mkdir(parents=True, exist_ok=True)
    init_state = bln.initial_state(bln.build_profile(hunger=hunger, thirst=thirst, toilet=0.18))
    ln_path.write_text(json.dumps(init_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ln_path


def _seed_active_adult_scene_permit(day_root: Path, permit_id: str = "sp_active_001") -> Path:
    phone_dir = day_root / "phone"
    phone_dir.mkdir(parents=True, exist_ok=True)
    p = phone_dir / "adult_intimacy.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": 1,
                "permits": {
                    "records": {
                        permit_id: {
                            "permit_id": permit_id,
                            "state": "ACTIVE",
                            "status": "ACTIVE",
                            "permit_rev": 1,
                            "activated_at": "2026-11-14T20:00:00+08:00",
                        }
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return p


class TestDeliverable1EmbodiedContactAvatarAndGap02(unittest.TestCase):
    """P0 contact RBAC, UserAvatarProxy, 1800s natural release, 12m bed cooling, cps_ & GAP-02."""

    def test_partner_respond_contact_rbac_and_no_self_countersign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            t0 = "2026-11-14T19:00:00+08:00"

            # 1. Bring partner within reach
            code, res = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "set_partner_presence", "presence_mode": "beside_on_sofa", "pose": "sitting"},
                actor_side="partner",
                now_iso=t0,
            )
            self.assertEqual(code, 200)
            cps = res["data"]["embodied_contact"]["copresence_session"]
            self.assertTrue(str(cps["session_id"]).startswith("cps_"))

            # 2. Partner proposes contact -> pending_initiator == "partner" -> partner CANNOT self-answer!
            code, _ = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "propose_contact", "contact_kind": "hold_one_hand"},
                actor_side="partner",
                now_iso="2026-11-14T19:01:00+08:00",
            )
            self.assertEqual(code, 200)

            code_self, err_self = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "respond_contact", "decision": "accept"},
                actor_side="partner",
                now_iso="2026-11-14T19:01:10+08:00",
            )
            self.assertEqual(code_self, 403)
            self.assertEqual(err_self["reason_code"], "FORBIDDEN_ACTOR_SIDE")

            # Subject declines cleanly so we can test subject-initiated proposals
            code_sub_dec, _ = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "respond_contact", "decision": "decline"},
                actor_side="subject",
                now_iso="2026-11-14T19:01:20+08:00",
            )
            self.assertEqual(code_sub_dec, 200)

            # 3. Subject proposes -> partner CAN gentle_shift, decline, and accept!
            for idx, decision in enumerate(("gentle_shift", "decline", "accept")):
                t_prop = f"2026-11-14T19:0{idx + 2}:00+08:00"
                t_resp = f"2026-11-14T19:0{idx + 2}:15+08:00"
                c_p, r_p = v03_embodied_social_view.mutate_v03_embodied_social_view(
                    root,
                    {"action": "propose_contact", "contact_kind": "hold_one_hand"},
                    actor_side="subject",
                    now_iso=t_prop,
                )
                self.assertEqual(c_p, 200)
                self.assertEqual(
                    r_p["data"]["embodied_contact"]["pending_initiator"],
                    "subject",
                )

                c_r, r_r = v03_embodied_social_view.mutate_v03_embodied_social_view(
                    root,
                    {"action": "respond_contact", "decision": decision},
                    actor_side="partner",
                    now_iso=t_resp,
                )
                self.assertEqual(c_r, 200, f"Partner respond_contact({decision}) failed: {r_r}")
                if decision == "accept":
                    self.assertIsNotNone(r_r["data"]["embodied_contact"]["active_contact"])
                    v03_embodied_social_view.mutate_v03_embodied_social_view(
                        root,
                        {"action": "release_contact", "reason": "done_check"},
                        actor_side="partner",
                        now_iso="2026-11-14T19:05:00+08:00",
                    )
                else:
                    self.assertIsNone(r_r["data"]["embodied_contact"]["active_contact"])

    def test_avatar_zones_1800s_natural_release_12m_bed_cooling_and_gap02_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            permit_path = _seed_active_adult_scene_permit(root, "sp_bed_01")

            # 1. doorway_shoe_zone is present in home but NOT within arm's reach for contact
            c1, r1 = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {
                    "action": "set_partner_presence",
                    "presence_mode": "doorway_shoe_zone",
                    "pose": "standing",
                    "ttl_seconds": 600,
                },
                actor_side="partner",
                now_iso="2026-11-14T21:00:00+08:00",
            )
            self.assertEqual(c1, 200)
            ec1 = r1["data"]["embodied_contact"]
            self.assertEqual(ec1["user_avatar_proxy"]["zone"], "doorway_shoe_zone")
            self.assertFalse(ec1["within_reach"])

            c_unreach, e_unreach = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "propose_contact", "contact_kind": "hold_one_hand"},
                actor_side="partner",
                now_iso="2026-11-14T21:01:00+08:00",
            )
            self.assertEqual(c_unreach, 409)
            self.assertEqual(e_unreach["reason_code"], "NOT_WITHIN_REACH")

            # 2. Move to bed_side_user with lying_side -> rumple_index > 0 & reachable
            c2, r2 = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {
                    "action": "set_partner_presence",
                    "presence_mode": "bed_side_user",
                    "pose": "lying_side",
                    "ttl_seconds": 7200,
                },
                actor_side="partner",
                now_iso="2026-11-14T21:02:00+08:00",
            )
            self.assertEqual(c2, 200)
            bed2 = r2["data"]["embodied_contact"]["bed_physics"]
            self.assertTrue(bed2["occupied_by_partner"])
            self.assertGreaterEqual(bed2["rumple_index"], 0.35)
            self.assertEqual(bed2["residual_warmth"], 1.0)

            # Liveness probe sees active cps_ session on both adapters
            probe_social = v03_embodied_social_view.CoPresenceAdapter.liveness_probe(
                root, now_iso="2026-11-14T21:02:30+08:00"
            )
            probe_stance = stance_containment_view.CoPresenceAdapter(root).liveness_probe(
                now="2026-11-14T21:02:30+08:00"
            )
            self.assertTrue(probe_social["alive"])
            self.assertTrue(str(probe_social["session_id"]).startswith("cps_"))
            self.assertTrue(probe_stance["alive"])
            self.assertEqual(probe_stance["session_id"], probe_social["session_id"])

            # 3. Start active contact and verify 1800s natural release frees hand slots cleanly
            v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "propose_contact", "contact_kind": "hold_one_hand"},
                actor_side="subject",
                now_iso="2026-11-14T21:03:00+08:00",
            )
            c_acc, r_acc = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "respond_contact", "decision": "accept"},
                actor_side="partner",
                now_iso="2026-11-14T21:03:10+08:00",
            )
            self.assertEqual(c_acc, 200)
            self.assertIsNotNone(r_acc["data"]["embodied_contact"]["active_contact"])

            # At +1805s (21:33:15), zero-write GET shows natural release & free hands
            _, view_after_30m = v03_embodied_social_view.read_v03_embodied_social_view(
                root, has_subject_capability=True, now_iso="2026-11-14T21:33:15+08:00"
            )
            ec_after = view_after_30m["data"]["embodied_contact"]
            self.assertIsNone(ec_after["active_contact"])
            self.assertEqual(ec_after["last_contact_event"]["event"], "natural_release_timeout")
            self.assertEqual(ec_after["hand_slots"]["right_hand"]["state"], "free")

            # 4. Exit co-presence at 21:40:00 -> cascades ACTIVE ScenePermit to PAUSED_PRESENCE_UNKNOWN (GAP-02)
            c_exit, r_exit = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "exit_copresence"},
                actor_side="partner",
                now_iso="2026-11-14T21:40:00+08:00",
            )
            self.assertEqual(c_exit, 200)
            self.assertTrue(r_exit["result"]["adult_permit_cascade"]["cascaded"])
            permit_doc = json.loads(permit_path.read_text(encoding="utf-8"))
            self.assertEqual(
                permit_doc["permits"]["records"]["sp_bed_01"]["state"],
                "PAUSED_PRESENCE_UNKNOWN",
            )

            # 5. Bed residual warmth cools with exact 12-minute (720s) half-life; rumple persists until tidy_bed
            _, view_12m = v03_embodied_social_view.read_v03_embodied_social_view(
                root, has_subject_capability=True, now_iso="2026-11-14T21:52:00+08:00"
            )
            bed_12m = view_12m["data"]["embodied_contact"]["bed_physics"]
            self.assertAlmostEqual(bed_12m["residual_warmth"], 0.5, places=4)
            self.assertGreater(bed_12m["rumple_index"], 0.0)

            _, view_24m = v03_embodied_social_view.read_v03_embodied_social_view(
                root, has_subject_capability=True, now_iso="2026-11-14T22:04:00+08:00"
            )
            self.assertAlmostEqual(
                view_24m["data"]["embodied_contact"]["bed_physics"]["residual_warmth"], 0.25, places=4
            )

            c_tidy, r_tidy = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "tidy_bed"},
                actor_side="subject",
                now_iso="2026-11-14T22:05:00+08:00",
            )
            self.assertEqual(c_tidy, 200)
            self.assertEqual(r_tidy["data"]["embodied_contact"]["bed_physics"]["rumple_index"], 0.0)


class TestDeliverable2HandsPenMugSomaticAndKitchen(unittest.TestCase):
    """Cross-module two-hand interlock, CUP_OCCUPIED_BY_STATIONERY, thirst relief, kitchen 6 zones & 36h onigiri."""

    def test_bidirectional_two_hand_interlock_across_stance_and_embodied_social(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            t0 = "2026-11-14T10:00:00+08:00"

            # 1. Pick up fountain_pen in right_hand via stance_containment_view
            res_pick = stance_containment_view.apply_stance_containment_action(
                root,
                {"action": "pick_up_item", "item_id": "fountain_pen", "hand_slot": "right_hand"},
                now=t0,
                subject_view=True,
            )
            self.assertTrue(res_pick["ok"])

            # v03_embodied_social_view immediately sees right_hand occupied -> refuses right_hand pickup & 2-hand hug
            c_same, e_same = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "set_hand_slot", "slot": "right_hand", "object_id": "tea_cup", "label": "茶杯"},
                actor_side="subject",
                now_iso="2026-11-14T10:00:10+08:00",
            )
            self.assertEqual(c_same, 409)
            self.assertEqual(e_same["reason_code"], "HAND_SLOT_OCCUPIED")

            # Put fountain_pen back on study_desk -> frees right_hand
            stance_containment_view.apply_stance_containment_action(
                root,
                {"action": "place_on_surface", "item_id": "fountain_pen", "place_id": "study_desk"},
                now="2026-11-14T10:00:20+08:00",
                subject_view=True,
            )

            # 2. Reverse direction: occupy left_hand in v03_embodied_social_view -> blocks left_hand in stance_containment_view
            c_left, _ = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "set_hand_slot", "slot": "left_hand", "object_id": "kettle_pot", "label": "手冲壶"},
                actor_side="subject",
                now_iso="2026-11-14T10:00:30+08:00",
            )
            self.assertEqual(c_left, 200)

            with self.assertRaises(stance_containment_view.StanceContainmentConflictError) as ctx:
                stance_containment_view.apply_stance_containment_action(
                    root,
                    {"action": "pick_up_item", "item_id": "fountain_pen", "hand_slot": "left_hand"},
                    now="2026-11-14T10:00:40+08:00",
                    subject_view=True,
                )
            self.assertEqual(ctx.exception.reason_code, "HAND_SLOT_OCCUPIED")

    def test_pen_in_ceramic_mug_blocks_tea_and_somatic_drink_relieves_thirst(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ln_path = _seed_lived_needs(root, hunger=0.50, thirst=0.70)
            t0 = "2026-11-14T14:00:00+08:00"

            # Put fountain_pen into ceramic_mug!
            res_put = stance_containment_view.apply_stance_containment_action(
                root,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "fountain_pen",
                    "container_id": "ceramic_mug",
                    "hand_slot": "right_hand",
                },
                now=t0,
                subject_view=True,
            )
            self.assertTrue(res_put["ok"])

            # Restock tea leaf and attempt to brew tea into ceramic_mug -> 409 CUP_OCCUPIED_BY_STATIONERY!
            v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "restock_pantry", "ingredient_id": "tea_leaves", "units_added": 3},
                actor_side="subject",
                now_iso="2026-11-14T14:01:00+08:00",
            )
            c_brew, e_brew = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {
                    "action": "cook_or_brew",
                    "title": "热焙茶",
                    "serving_kind": "drink",
                    "container_id": "ceramic_mug",
                    "ingredients_used": [{"ingredient_id": "tea_leaves", "units_used": 1}],
                    "portions": 1,
                },
                actor_side="subject",
                now_iso="2026-11-14T14:01:30+08:00",
            )
            self.assertEqual(c_brew, 409)
            self.assertEqual(e_brew["reason_code"], "CUP_OCCUPIED_BY_STATIONERY")

            # Table warm_drink into ceramic_mug also rejected with CUP_OCCUPIED_BY_STATIONERY
            with self.assertRaises(stance_containment_view.StanceContainmentConflictError) as t_ctx:
                stance_containment_view.apply_stance_containment_action(
                    root,
                    {
                        "action": "prepare_table_dish",
                        "title": "手冲热茶",
                        "kind": "warm_drink",
                        "portions": 1,
                        "container_id": "ceramic_mug",
                    },
                    now="2026-11-14T14:02:00+08:00",
                    subject_view=True,
                )
            self.assertEqual(t_ctx.exception.reason_code, "CUP_OCCUPIED_BY_STATIONERY")

            # Offer + take drink in somatic_comfort_view; attempting drink with ceramic_mug still fails while pen inside
            c_off, _ = somatic_comfort_view.mutate_somatic_comfort_view(
                root,
                {"action": "offer", "slot": "drink", "kind": "warm_water", "position": "desk_side"},
                actor_side="partner",
                now_iso="2026-11-14T14:02:30+08:00",
            )
            self.assertEqual(c_off, 200)
            c_take, _ = somatic_comfort_view.mutate_somatic_comfort_view(
                root,
                {"action": "take", "slot": "drink"},
                actor_side="subject",
                now_iso="2026-11-14T14:02:45+08:00",
            )
            self.assertEqual(c_take, 200)
            c_blk, e_blk = somatic_comfort_view.mutate_somatic_comfort_view(
                root,
                {"action": "drink", "slot": "drink", "container_id": "ceramic_mug"},
                actor_side="subject",
                now_iso="2026-11-14T14:03:00+08:00",
            )
            self.assertEqual(c_blk, 409)
            self.assertEqual(e_blk["reason_code"], "CUP_OCCUPIED_BY_STATIONERY")

            # Remove pen from ceramic_mug -> somatic drink now succeeds (200) and genuinely lowers thirst!
            stance_containment_view.apply_stance_containment_action(
                root,
                {"action": "take_from_container", "item_id": "fountain_pen", "container_id": "ceramic_mug"},
                now="2026-11-14T14:03:15+08:00",
                subject_view=True,
            )
            stance_containment_view.apply_stance_containment_action(
                root,
                {"action": "place_on_surface", "item_id": "fountain_pen", "place_id": "study_desk"},
                now="2026-11-14T14:03:20+08:00",
                subject_view=True,
            )

            c_drk, r_drk = somatic_comfort_view.mutate_somatic_comfort_view(
                root,
                {"action": "drink", "slot": "drink", "container_id": "ceramic_mug"},
                actor_side="subject",
                now_iso="2026-11-14T14:03:30+08:00",
            )
            self.assertEqual(c_drk, 200)
            self.assertTrue(r_drk["consumed"])
            self.assertIsInstance(r_drk["consume_receipt"]["amount_mg"], int)
            self.assertIsInstance(r_drk["consume_receipt"]["energy_mJ"], int)
            ln_after = json.loads(ln_path.read_text(encoding="utf-8"))
            self.assertLess(ln_after["levels"]["thirst"], 0.70)

            # Unknown illegal somatic action still rejected with 400 CARE_ACTION_UNSUPPORTED
            c_bad, e_bad = somatic_comfort_view.mutate_somatic_comfort_view(
                root,
                {"action": "teleport_magic_potion"},
                actor_side="subject",
                now_iso="2026-11-14T14:04:00+08:00",
            )
            self.assertEqual(c_bad, 400)
            self.assertEqual(e_bad["reason_code"], "CARE_ACTION_UNSUPPORTED")

    def test_kitchen_6_zones_snack_drawer_privacy_sink_guard_receipts_and_36h_onigiri(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ln_path = _seed_lived_needs(root, hunger=0.75, thirst=0.50)

            # 1. Partner GET sees 6 zones with chiyo_snack_drawer redacted as {status: PRIVATE, reason_code: PRIVATE_REFUSED}
            _, p_view = v03_embodied_social_view.read_v03_embodied_social_view(
                root, has_subject_capability=False, now_iso="2026-11-14T08:00:00+08:00"
            )
            zones = p_view["data"]["kitchen"]["zones"]
            self.assertEqual(
                set(zones.keys()),
                {"pantry_cabinet", "fridge", "prep_counter", "stove_kettle_zone", "sink", "chiyo_snack_drawer"},
            )
            self.assertEqual(zones["chiyo_snack_drawer"]["status"], "PRIVATE")
            self.assertEqual(zones["chiyo_snack_drawer"]["reason_code"], "PRIVATE_REFUSED")

            # Partner unauthorized inspect_kitchen_zone(chiyo_snack_drawer) -> 403 PRIVATE_REFUSED (ERR-09)
            c_priv, e_priv = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "inspect_kitchen_zone", "zone_id": "chiyo_snack_drawer"},
                actor_side="partner",
                now_iso="2026-11-14T08:01:00+08:00",
            )
            self.assertEqual(c_priv, 403)
            self.assertEqual(e_priv["reason_code"], "PRIVATE_REFUSED")

            # Subject grants permission -> partner inspect succeeds
            v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "set_snack_drawer_permission", "partner_authorized": True},
                actor_side="subject",
                now_iso="2026-11-14T08:02:00+08:00",
            )
            c_ok_priv, _ = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "inspect_kitchen_zone", "zone_id": "chiyo_snack_drawer"},
                actor_side="partner",
                now_iso="2026-11-14T08:02:30+08:00",
            )
            self.assertEqual(c_ok_priv, 200)

            # Appliances operate deterministically
            c_app, r_app = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "operate_kitchen_appliance", "appliance_id": "electric_kettle", "operation": "boil_kettle"},
                actor_side="subject",
                now_iso="2026-11-14T08:03:00+08:00",
            )
            self.assertEqual(c_app, 200)
            self.assertEqual(r_app["data"]["kitchen"]["appliances"]["electric_kettle"]["state"], "boiled")

            # 2. Restock rice & nori, run Ch. 18 36-hour onigiri physical chain + discard receipt + sink >= 3 guard
            c_restock, _ = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {
                    "action": "restock_pantry",
                    "items": [
                        {
                            "ingredient_id": "short_grain_rice",
                            "label": "越光大米",
                            "category": "staple",
                            "unit_name": "份",
                            "units_added": 6,
                        },
                        {
                            "ingredient_id": "nori_sheet",
                            "label": "烤海苔",
                            "category": "seasoning",
                            "unit_name": "张",
                            "units_added": 6,
                        },
                        {"ingredient_id": "eggs", "units_added": 6},
                    ],
                },
                actor_side="subject",
                now_iso="2026-11-14T08:05:00+08:00",
            )
            self.assertEqual(c_restock, 200)

            # Cook onigiri at T=08:10
            c_onigiri, r_onigiri = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {
                    "action": "cook_or_brew",
                    "title": "海苔盐饭团",
                    "serving_kind": "meal",
                    "ingredients_used": [
                        {"ingredient_id": "short_grain_rice", "units_used": 1},
                        {"ingredient_id": "nori_sheet", "units_used": 1},
                    ],
                    "portions": 1,
                },
                actor_side="subject",
                now_iso="2026-11-14T08:10:00+08:00",
            )
            self.assertEqual(c_onigiri, 200)
            onigiri_id = r_onigiri["result"]["dish"]["dish_id"]

            # Hot dish cannot go straight into fridge -> 409 DISH_TOO_HOT_FOR_FRIDGE
            c_hot, e_hot = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "refrigerate_dish", "dish_id": onigiri_id},
                actor_side="subject",
                now_iso="2026-11-14T08:15:00+08:00",
            )
            self.assertEqual(c_hot, 409)
            self.assertEqual(e_hot["reason_code"], "DISH_TOO_HOT_FOR_FRIDGE")

            # After cooling (+50m at 09:00), refrigerate succeeds
            c_ref, _ = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "refrigerate_dish", "dish_id": onigiri_id},
                actor_side="subject",
                now_iso="2026-11-14T09:00:00+08:00",
            )
            self.assertEqual(c_ref, 200)

            # +36 hours later (2026-11-15T21:00:00+08:00): cold refrigerated meal requires reheat before eating
            c_cold_eat, e_cold_eat = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "consume_dish", "dish_id": onigiri_id},
                actor_side="subject",
                now_iso="2026-11-15T21:00:00+08:00",
            )
            self.assertEqual(c_cold_eat, 409)
            self.assertEqual(e_cold_eat["reason_code"], "DISH_NEEDS_REHEAT")

            # Reheat in microwave -> consume -> integer ConsumeReceipt(amount_mg, energy_mJ) & hunger lowered!
            c_reh, _ = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "reheat_dish", "dish_id": onigiri_id},
                actor_side="subject",
                now_iso="2026-11-15T21:02:00+08:00",
            )
            self.assertEqual(c_reh, 200)

            c_eat, r_eat = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "consume_dish", "dish_id": onigiri_id},
                actor_side="subject",
                now_iso="2026-11-15T21:05:00+08:00",
            )
            self.assertEqual(c_eat, 200)
            c_rcpt = r_eat["result"]["consumption_receipt"]
            self.assertIsInstance(c_rcpt["amount_mg"], int)
            self.assertIsInstance(c_rcpt["energy_mJ"], int)
            self.assertGreater(c_rcpt["amount_mg"], 0)
            self.assertGreater(c_rcpt["energy_mJ"], 0)
            hunger_after_onigiri = json.loads(ln_path.read_text(encoding="utf-8"))["levels"]["hunger"]
            self.assertLess(hunger_after_onigiri, 0.75)

            # 3. Discard dish produces energy_mJ == 0, does NOT lower hunger, and blocks resurrection;
            #    accumulating 3 dirty dishes in sink blocks new cooking until wash_dishes!
            for idx in range(2):
                _, r_tmp = v03_embodied_social_view.mutate_v03_embodied_social_view(
                    root,
                    {
                        "action": "cook_or_brew",
                        "title": f"煎蛋小碟{idx}",
                        "serving_kind": "meal",
                        "ingredients_used": [{"ingredient_id": "eggs", "units_used": 1}],
                        "portions": 1,
                    },
                    actor_side="subject",
                    now_iso=f"2026-11-15T21:1{idx}:00+08:00",
                )
                did = r_tmp["result"]["dish"]["dish_id"]
                c_disc, r_disc = v03_embodied_social_view.mutate_v03_embodied_social_view(
                    root,
                    {"action": "discard_dish", "dish_id": did, "reason": "burnt_edge"},
                    actor_side="subject",
                    now_iso=f"2026-11-15T21:1{idx}:30+08:00",
                )
                self.assertEqual(c_disc, 200)
                d_rcpt = r_disc["result"]["discard_receipt"]
                self.assertEqual(d_rcpt["energy_mJ"], 0)
                self.assertFalse(d_rcpt["recorded_as_intake"])

                # Anti-resurrection check
                c_resurrect, e_resurrect = v03_embodied_social_view.mutate_v03_embodied_social_view(
                    root,
                    {"action": "consume_dish", "dish_id": did},
                    actor_side="subject",
                    now_iso=f"2026-11-15T21:1{idx}:45+08:00",
                )
                self.assertEqual(c_resurrect, 409)
                self.assertEqual(e_resurrect["reason_code"], "DISH_ALREADY_DISCARDED")

            # Hunger was NOT reduced by the two discarded dishes!
            self.assertEqual(
                json.loads(ln_path.read_text(encoding="utf-8"))["levels"]["hunger"],
                hunger_after_onigiri,
            )

            # Sink now has 3 dirty dishes (1 eaten onigiri + 2 discarded plates) -> blocks new plating!
            c_sink, e_sink = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {
                    "action": "cook_or_brew",
                    "title": "第四盘煎蛋",
                    "serving_kind": "meal",
                    "ingredients_used": [{"ingredient_id": "eggs", "units_used": 1}],
                    "portions": 1,
                },
                actor_side="subject",
                now_iso="2026-11-15T21:20:00+08:00",
            )
            self.assertEqual(c_sink, 409)
            self.assertEqual(e_sink["reason_code"], "SINK_DIRTY_DISHES_FULL")

            # Wash dishes clears sink -> cooking succeeds again!
            c_wash, r_wash = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "wash_dishes"},
                actor_side="partner",
                now_iso="2026-11-15T21:22:00+08:00",
            )
            self.assertEqual(c_wash, 200)
            self.assertEqual(r_wash["data"]["kitchen"]["zones"]["sink"]["dirty_dishes"], 0)

            c_after_wash, _ = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {
                    "action": "cook_or_brew",
                    "title": "洗完碗后的热蛋卷",
                    "serving_kind": "meal",
                    "ingredients_used": [{"ingredient_id": "eggs", "units_used": 1}],
                    "portions": 1,
                },
                actor_side="subject",
                now_iso="2026-11-15T21:25:00+08:00",
            )
            self.assertEqual(c_after_wash, 200)


class TestDeliverable3RelationshipTownDwellAndCognitiveSpatial(unittest.TestCase):
    """Ch. 01–03, 08, 21, 23–27, 30–35 & GAP-09 & ERR-10 comprehensive verification."""

    def test_relationship_anchors_town_dual_aliases_3_lifecycles_commitments_rumor_and_hobby_3_axes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 2026-11-14 is Saturday; 14:30 is inside bookstore & craft open windows
            t_open = "2026-11-14T14:30:00+08:00"

            # 1. Town NPC dual-field aliases: label/name, place_label/shop, own_ongoing_task/current_activity
            _, view0 = v03_embodied_social_view.read_v03_embodied_social_view(
                root, has_subject_capability=False, now_iso=t_open
            )
            npcs = view0["data"]["town_npcs"]["npcs"]
            self.assertGreaterEqual(len(npcs), 4)
            for row in npcs:
                self.assertTrue(row["display_name"])
                self.assertEqual(row["label"], row["display_name"])
                self.assertEqual(row["name"], row["display_name"])
                self.assertTrue(row["shop_name"])
                self.assertEqual(row["place_label"], row["shop_name"])
                self.assertEqual(row["shop"], row["shop_name"])
                self.assertTrue(row["current_status_note"])
                self.assertEqual(row["own_ongoing_task"], row["current_status_note"])
                self.assertEqual(row["current_activity"], row["current_status_note"])
                self.assertFalse(row["tracks_affection_score"])

            bookstore_npc_id = next(r["npc_id"] for r in npcs if r["location_id"] == "bookstore")

            # 2. Ch. 01 Non-numeric relationship anchors (partner + neighbor, private secret redaction, zero score)
            for payload in (
                {
                    "action": "record_relationship_anchor",
                    "target_id": "partner",
                    "anchor_category": "trust_by_domain",
                    "domain": "creative_feedback",
                    "trust_stance": "honest_first_reader",
                },
                {
                    "action": "record_relationship_anchor",
                    "target_id": "partner",
                    "anchor_category": "boundary",
                    "boundary_statement": "不未经商量翻看未写完的手稿页",
                },
                {
                    "action": "record_relationship_anchor",
                    "target_id": "partner",
                    "anchor_category": "anchor_object",
                    "object_kind": "borrowed_umbrella",
                    "label": "灰格纹长柄伞",
                    "story_note": "阵雨傍晚在车站口共撑回家的那把伞",
                },
                {
                    "action": "record_relationship_anchor",
                    "target_id": "partner",
                    "anchor_category": "anchor_object",
                    "object_kind": "failed_dish",
                    "label": "第一次煎散的厚蛋烧",
                    "story_note": "虽然翻面碎了还是分着吃完了",
                },
                {
                    "action": "record_relationship_anchor",
                    "target_id": bookstore_npc_id,
                    "anchor_category": "anchor_object",
                    "object_kind": "dried_specimen",
                    "label": "夹在旧植物志里的风干银杏叶",
                    "story_note": "书店店主留给常客的秋叶书签",
                    "shared_with_partner": True,
                },
                {
                    "action": "record_relationship_anchor",
                    "target_id": "partner",
                    "anchor_category": "shared_language",
                    "phrase": "去煮一壶热麦茶缓一缓",
                    "meaning_context": "写卡壳时默契停下来歇十分钟的暗号",
                },
                {
                    "action": "record_relationship_anchor",
                    "target_id": "partner",
                    "anchor_category": "shared_secret",
                    "secret_summary": "偷偷给下个月准备的手作书签草图",
                    "shared_with_partner": False,
                },
            ):
                c_ra, _ = v03_embodied_social_view.mutate_v03_embodied_social_view(
                    root, payload, actor_side="subject", now_iso=t_open
                )
                self.assertEqual(c_ra, 200)

            _, subj_ra_view = v03_embodied_social_view.read_v03_embodied_social_view(
                root, has_subject_capability=True, now_iso=t_open
            )
            _, part_ra_view = v03_embodied_social_view.read_v03_embodied_social_view(
                root, has_subject_capability=False, now_iso=t_open
            )
            self.assertTrue(subj_ra_view["data"]["relationship_anchors"]["numeric_score_forbidden"])
            self.assertEqual(len(subj_ra_view["data"]["relationship_anchors"]["partner"]["shared_secrets"]), 1)
            self.assertEqual(len(part_ra_view["data"]["relationship_anchors"]["partner"]["shared_secrets"]), 0)

            # 3. Ch. 02 & Ch. 26: 3 slow lifecycles (shop handover, stray cat physical feeding count, street musician >=21d trace)
            # Stray cat does NOT advance by mere passage of time, ONLY by physical feed_stray_cat!
            _, view_nov25 = v03_embodied_social_view.read_v03_embodied_social_view(
                root, has_subject_capability=False, now_iso="2026-11-25T15:00:00+08:00"
            )
            sl_nov25 = view_nov25["data"]["town_npcs"]["slow_lifecycles"]
            self.assertEqual(sl_nov25["old_shop_handover"]["phase"], "new_keeper_settled_with_old_sign")
            self.assertEqual(sl_nov25["stray_cat"]["physical_feeding_count"], 0)
            self.assertEqual(sl_nov25["stray_cat"]["familiarity_stage"], "wary_at_wall_top")
            self.assertEqual(sl_nov25["street_musician"]["status"], "departed_left_trace")
            self.assertIsNotNone(sl_nov25["street_musician"]["sheet_music_trace"])

            for i in range(3):
                c_cat, r_cat = v03_embodied_social_view.mutate_v03_embodied_social_view(
                    root,
                    {"action": "feed_stray_cat", "food_label": "无盐小鱼干"},
                    actor_side="subject",
                    now_iso=f"2026-11-15T16:0{i}:00+08:00",
                )
                self.assertEqual(c_cat, 200)
            self.assertEqual(r_cat["result"]["stray_cat"]["physical_feeding_count"], 3)
            self.assertEqual(r_cat["result"]["stray_cat"]["familiarity_stage"], "waits_by_step_tail_up")

            # 4. GAP-09 NPCCommitmentProposal + multi-source rumor epistemic_basis + bookstore card feedback
            c_com, r_com = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {
                    "action": "propose_npc_commitment",
                    "npc_id": bookstore_npc_id,
                    "location_id": "bookstore",
                    "window_start_iso": "2026-11-16T15:00:00+08:00",
                    "window_end_iso": "2026-11-16T17:00:00+08:00",
                    "purpose": "去旧书店取预留的地方植物画册",
                },
                actor_side="subject",
                now_iso=t_open,
            )
            self.assertEqual(c_com, 200)
            cid = r_com["result"]["commitment"]["commitment_id"]

            c_res_com, r_res_com = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "resolve_npc_commitment", "commitment_id": cid, "outcome": "fulfilled", "note": "顺利取到书"},
                actor_side="subject",
                now_iso="2026-11-16T15:30:00+08:00",
            )
            self.assertEqual(c_res_com, 200)
            self.assertEqual(r_res_com["result"]["commitment"]["status"], "fulfilled")

            c_rumor, r_rumor = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {
                    "action": "record_npc_knowledge",
                    "npc_id": bookstore_npc_id,
                    "source_type": "town_public_notice",
                    "hearsay_sources": ["街角焙茶铺老板", "邮亭街坊"],
                    "summary": "听说旧书店周末会在门廊摆一箱旧明信片",
                    "require_open": False,
                },
                actor_side="subject",
                now_iso=t_open,
            )
            self.assertEqual(c_rumor, 200)
            self.assertEqual(r_rumor["result"]["fact"]["epistemic_basis"], "multi_source_rumor")

            c_card, r_card = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {
                    "action": "submit_bookstore_card_feedback",
                    "npc_id": bookstore_npc_id,
                    "book_ref": "book:coastal_flora_v1",
                    "card_note": "第三章关于海雾苔藓的手绘插图很耐看",
                    "feedback_kind": "resonated",
                    "shared_on_board": True,
                },
                actor_side="subject",
                now_iso=t_open,
            )
            self.assertEqual(c_card, 200)
            self.assertEqual(r_card["result"]["bookstore_card"]["feedback_kind"], "resonated")

            # 5. Ch. 24 Hobby 3 axes (capability / stability / enjoyment + shelved_unfinished; numeric xp rejected!)
            c_xp_bad, e_xp_bad = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {"action": "record_style_migration", "title": "水彩速写", "stage": "active_curiosity", "xp": 100},
                actor_side="subject",
                now_iso=t_open,
            )
            self.assertEqual(c_xp_bad, 400)
            self.assertEqual(e_xp_bad["reason_code"], "INVALID_FIELDS")

            c_hob, r_hob = v03_embodied_social_view.mutate_v03_embodied_social_view(
                root,
                {
                    "action": "record_style_migration",
                    "title": "打磨溪水石镇纸",
                    "origin_context": "craft_practice",
                    "stage": "shelved_unfinished",
                    "capability": "finding_grip",
                    "stability": "steady_when_unhurried",
                    "shared": True,
                },
                actor_side="subject",
                now_iso=t_open,
            )
            self.assertEqual(c_hob, 200)
            s_item = r_hob["result"]["style_item"]
            self.assertTrue(s_item["shelved_unfinished"])
            self.assertEqual(s_item["skill_three_axes"]["capability"], "finding_grip")
            self.assertEqual(s_item["skill_three_axes"]["stability"], "steady_when_unhurried")
            self.assertEqual(s_item["skill_three_axes"]["enjoyment"], "resting_for_now")
            self.assertTrue(s_item["skill_three_axes"]["numeric_xp_forbidden"])

    def test_life_dwell_host_gap_subjective_time_and_all_everyday_glitches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            t0 = "2026-11-14T15:00:00+08:00"

            # 1. Start bounded dwell (declared_seconds=600) -> expires cleanly without auto-renew
            r_dwell = v03_clock_evaporation.apply_v03_clock_action(
                root,
                {"action": "start_dwell", "declared_seconds": 600, "place_node": "window_side", "label": "看雨滴"},
                actor_side="subject",
                now_iso=t0,
            )
            self.assertTrue(r_dwell["ok"])
            self.assertEqual(r_dwell["data"]["life_dwell"]["active_dwell"]["status"], "ACTIVE")

            # Read after 700s -> EXPIRED_NO_AUTO_RENEW (zero disk write, does not reset began_at)
            _exp_code, r_exp = v03_clock_evaporation.read_v03_clock_view(
                root, has_subject_capability=True, now_iso="2026-11-14T15:11:40+08:00"
            )
            self.assertEqual(_exp_code, 200)
            self.assertEqual(r_exp["data"]["life_dwell"]["active_dwell"]["status"], "EXPIRED_NO_AUTO_RENEW")

            r_end = v03_clock_evaporation.apply_v03_clock_action(
                root,
                {"action": "end_dwell", "reason": "tea_kettle_whistled"},
                actor_side="subject",
                now_iso="2026-11-14T15:12:00+08:00",
            )
            self.assertTrue(r_end["ok"])
            self.assertIsNone(r_end["data"]["life_dwell"]["active_dwell"])

            # 2. ERR-10: >15m (>900s) offline gap honestly records HOST_GAP (and normalizes legacy HOST_DOWN -> HOST_GAP)
            v03_clock_evaporation.apply_v03_clock_action(
                root, {"action": "advance_projection", "sync_v02": False}, actor_side="subject", now_iso="2026-11-14T15:15:00+08:00"
            )
            # Short 5-minute advance -> SHORT_ADVANCE (NOT HOST_GAP)
            r_short = v03_clock_evaporation.apply_v03_clock_action(
                root, {"action": "advance_projection", "sync_v02": False}, actor_side="subject", now_iso="2026-11-14T15:20:00+08:00"
            )
            self.assertEqual(r_short["data"]["action_receipt"]["kind"], "SHORT_ADVANCE")
            self.assertFalse(r_short["data"]["action_receipt"]["host_gap_recorded"])

            # 30-minute offline gap -> automatically records canonical HOST_GAP with zero cognitive backfill!
            r_long = v03_clock_evaporation.apply_v03_clock_action(
                root, {"action": "advance_projection", "sync_v02": False}, actor_side="subject", now_iso="2026-11-14T15:50:00+08:00"
            )
            self.assertEqual(r_long["data"]["action_receipt"]["kind"], "HOST_GAP")
            self.assertTrue(r_long["data"]["action_receipt"]["host_gap_recorded"])
            self.assertEqual(r_long["data"]["action_receipt"]["cognitive_backfill_count"], 0)

            # Legacy HOST_DOWN explicitly submitted -> normalized to canonical HOST_GAP
            r_legacy = v03_clock_evaporation.apply_v03_clock_action(
                root,
                {
                    "action": "record_host_gap",
                    "from_iso": "2026-11-14T12:00:00+08:00",
                    "to_iso": "2026-11-14T12:30:00+08:00",
                    "kind": "HOST_DOWN",
                },
                actor_side="subject",
                now_iso="2026-11-14T15:51:00+08:00",
            )
            self.assertTrue(r_legacy["ok"])
            self.assertEqual(r_legacy["data"]["action_receipt"]["host_gap"]["kind"], "HOST_GAP")

            # 3. Subjective time 3 modes: FLOW / STAGNANT / WEAVE
            r_flow = v03_clock_evaporation.apply_v03_clock_action(
                root,
                {
                    "action": "update_subjective_time",
                    "focused_work_minutes": 55,
                    "meaningful_event_count": 2,
                    "idle_or_waiting_minutes": 5,
                },
                actor_side="subject",
                now_iso="2026-11-14T16:00:00+08:00",
            )
            self.assertEqual(r_flow["data"]["subjective_time"]["mode"], "FLOW")

            r_stag = v03_clock_evaporation.apply_v03_clock_action(
                root,
                {
                    "action": "update_subjective_time",
                    "focused_work_minutes": 0,
                    "meaningful_event_count": 0,
                    "idle_or_waiting_minutes": 65,
                },
                actor_side="subject",
                now_iso="2026-11-14T17:05:00+08:00",
            )
            self.assertEqual(r_stag["data"]["subjective_time"]["mode"], "STAGNANT")

            r_weave = v03_clock_evaporation.apply_v03_clock_action(
                root,
                {
                    "action": "update_subjective_time",
                    "focused_work_minutes": 20,
                    "meaningful_event_count": 2,
                    "idle_or_waiting_minutes": 15,
                },
                actor_side="subject",
                now_iso="2026-11-14T17:30:00+08:00",
            )
            self.assertEqual(r_weave["data"]["subjective_time"]["mode"], "WEAVE")

            # 4. All 6 everyday glitches (trace legal, persona archive forbidden, damp coat adds chill load!)
            glitch_payloads = [
                {
                    "action": "trigger_everyday_glitch",
                    "glitch_kind": "misplaced_item_search",
                    "item_id": "brass_bookmark",
                    "expected_spot": "study_desk",
                    "actual_spot": "sofa_side",
                    "search_seconds": 180,
                },
                {
                    "action": "trigger_everyday_glitch",
                    "glitch_kind": "spilled_water_wipe_desk",
                    "surface_id": "study_desk",
                    "wiped_clean": True,
                },
                {
                    "action": "trigger_everyday_glitch",
                    "glitch_kind": "forgot_umbrella_damp_coat",
                    "garment_id": "knit_cardigan",
                    "garment_label": "薄针织开衫",
                },
                {
                    "action": "trigger_everyday_glitch",
                    "glitch_kind": "eraser_torn_page",
                    "target_ref": "sketchbook:page_7",
                },
                {
                    "action": "trigger_everyday_glitch",
                    "glitch_kind": "playlist_typo",
                    "intended_title": "晚风散步",
                    "actual_title": "碗风散步",
                },
                {
                    "action": "trigger_everyday_glitch",
                    "glitch_kind": "stone_polishing_settled_incomplete",
                    "object_id": "river_pebble_01",
                    "completed_units": 2,
                    "remaining_units": 3,
                },
            ]
            last_glitch_res = None
            for idx, gp in enumerate(glitch_payloads):
                last_glitch_res = v03_clock_evaporation.apply_v03_clock_action(
                    root, gp, actor_side="subject", now_iso=f"2026-11-14T18:0{idx}:00+08:00"
                )
                self.assertTrue(last_glitch_res["ok"])
            assert last_glitch_res is not None
            dwell_proj = last_glitch_res["data"]["life_dwell"]
            self.assertEqual(len(dwell_proj["everyday_glitches"]["traces"]), 6)
            self.assertFalse(dwell_proj["everyday_glitches"]["persona_tag_extracted"])
            self.assertGreater(dwell_proj["chill_load"]["projected_load"], 0.0)

    def test_cognitive_spatial_unread_page_guard_non_xray_drawer_entryway_cohabitation_and_desire_lifecycle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            t0 = "2026-11-14T11:00:00+08:00"

            # 1. Ch. 30 Unread page guard: pages [1, 2, 3] obtained -> p.42 rejected with 409 VIEW_PAGE_NOT_OBTAINED!
            stance_containment_view.apply_stance_containment_action(
                root,
                {
                    "action": "record_obtained_pages",
                    "scope_ref": "book:sea_glass_notes",
                    "obtained_pages": [1, 2, 3],
                    "total_pages": 120,
                },
                now=t0,
                subject_view=True,
            )
            with self.assertRaises(stance_containment_view.StanceContainmentConflictError) as pg_ctx:
                stance_containment_view.apply_stance_containment_action(
                    root,
                    {
                        "action": "propose_stance",
                        "scope_ref": "book:sea_glass_notes",
                        "source_revision": "rev1",
                        "page_or_anchor": "p.42",
                        "facet": "content",
                        "text": "第 42 页关于退潮时间表的推算很细致",
                    },
                    now="2026-11-14T11:01:00+08:00",
                    subject_view=True,
                )
            self.assertEqual(pg_ctx.exception.reason_code, "VIEW_PAGE_NOT_OBTAINED")

            # After obtaining page 42, propose_stance + revise_stance succeed and form a misunderstanding->revision chain!
            stance_containment_view.apply_stance_containment_action(
                root,
                {
                    "action": "record_obtained_pages",
                    "scope_ref": "book:sea_glass_notes",
                    "obtained_pages": [1, 2, 3, 42],
                    "total_pages": 120,
                },
                now="2026-11-14T11:02:00+08:00",
                subject_view=True,
            )
            res_s1 = stance_containment_view.apply_stance_containment_action(
                root,
                {
                    "action": "propose_stance",
                    "scope_ref": "book:sea_glass_notes",
                    "source_revision": "rev1",
                    "page_or_anchor": "p.42",
                    "facet": "content",
                    "text": "起初以为作者写的是春潮水位",
                },
                now="2026-11-14T11:03:00+08:00",
                subject_view=True,
            )
            s1_id = res_s1["action_receipt"]["target_id"]
            stance_containment_view.apply_stance_containment_action(
                root,
                {
                    "action": "revise_stance",
                    "replaces_stance_id": s1_id,
                    "text": "对照脚注后发现其实记录的是秋分大潮水位",
                },
                now="2026-11-14T11:04:00+08:00",
                subject_view=True,
            )

            # 2. Closed drawer non-xray + first-reach miss ITEM_NOT_AT_EXPECTED_SPOT + scoped observe_current_room
            stance_containment_view.apply_stance_containment_action(
                root,
                {
                    "action": "register_container",
                    "container_id": "desk_drawer",
                    "label": "书桌木抽屉",
                    "place_id": "study_desk",
                    "capacity": 4,
                    "accepted_classes": ["writing_tool", "stationery", "small_item"],
                    "open_state": "open",
                },
                now="2026-11-14T11:05:00+08:00",
                subject_view=True,
            )
            stance_containment_view.apply_stance_containment_action(
                root,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "fountain_pen",
                    "container_id": "desk_drawer",
                    "hand_slot": "right_hand",
                },
                now="2026-11-14T11:05:30+08:00",
                subject_view=True,
            )
            stance_containment_view.apply_stance_containment_action(
                root,
                {"action": "set_container_open_state", "container_id": "desk_drawer", "open_state": "closed"},
                now="2026-11-14T11:06:00+08:00",
                subject_view=True,
            )

            proj_closed = stance_containment_view.read_stance_containment(
                root, now="2026-11-14T11:06:10+08:00", subject_view=True
            )
            cues = proj_closed["attention_view"]["visible_cues"]
            # Closed desk_drawer exterior is visible, but fountain_pen inside it is NOT omnisciently x-rayed!
            self.assertFalse(any(c.get("ref") == "fountain_pen" or c.get("ref") == "item:fountain_pen" for c in cues))
            drawer_cue = next(c for c in cues if c.get("ref") in ("desk_drawer", "container:desk_drawer"))
            self.assertFalse(drawer_cue["interior_visible"])

            # Partner moves pen_cup away to bookshelf while subject does NOT witness -> first reach raises ITEM_NOT_AT_EXPECTED_SPOT
            stance_containment_view.apply_stance_containment_action(
                root,
                {"action": "set_container_open_state", "container_id": "desk_drawer", "open_state": "open"},
                now="2026-11-14T11:07:00+08:00",
                subject_view=True,
            )
            stance_containment_view.apply_stance_containment_action(
                root,
                {"action": "take_from_container", "item_id": "fountain_pen", "container_id": "desk_drawer"},
                now="2026-11-14T11:07:10+08:00",
                subject_view=True,
            )
            stance_containment_view.apply_stance_containment_action(
                root,
                {"action": "put_in_container", "item_id": "fountain_pen", "container_id": "pen_cup"},
                now="2026-11-14T11:07:20+08:00",
                subject_view=True,
            )
            stance_containment_view.apply_stance_containment_action(
                root,
                {
                    "action": "partner_move_container",
                    "container_id": "pen_cup",
                    "to_place_id": "bookshelf",
                    "subject_witnessed": False,
                },
                now="2026-11-14T11:08:00+08:00",
                subject_view=False,
            )
            with self.assertRaises(stance_containment_view.StanceContainmentConflictError) as miss_ctx:
                stance_containment_view.apply_stance_containment_action(
                    root,
                    {"action": "pick_up_item", "item_id": "fountain_pen", "hand_slot": "right_hand"},
                    now="2026-11-14T11:08:30+08:00",
                    subject_view=True,
                )
            self.assertEqual(miss_ctx.exception.reason_code, "ITEM_NOT_AT_EXPECTED_SPOT")

            # Scoped observe_current_room(scope="container", container_id="desk_drawer") does NOT omnisciently refresh bookshelf
            obs_scoped = stance_containment_view.apply_stance_containment_action(
                root,
                {"action": "observe_current_room", "scope": "container", "container_id": "desk_drawer"},
                now="2026-11-14T11:09:00+08:00",
                subject_view=True,
            )
            self.assertTrue(obs_scoped["ok"])

            # 3. Ch. 25 entryway_tray buffer + cohabitation negotiation (suggest_item_use, lend_item, preview_lamp_move)
            containers_ids = {
                c["container_id"]
                for c in obs_scoped["object_containment"]["containers"]
            }
            self.assertIn("entryway_tray", containers_ids)

            sugg_res = stance_containment_view.apply_stance_containment_action(
                root,
                {
                    "action": "suggest_item_use",
                    "actor": "partner",
                    "target_id": "entryway_tray",
                    "suggested_use": "出门钥匙和公交卡统一放在玄关托盘里",
                    "target_container_id": "entryway_tray",
                },
                now="2026-11-14T11:10:00+08:00",
                subject_view=False,
            )
            sugg_id = sugg_res["action_receipt"]["target_id"]
            resp_sugg = stance_containment_view.apply_stance_containment_action(
                root,
                {"action": "respond_item_use_suggestion", "suggestion_id": sugg_id, "decision": "accept"},
                now="2026-11-14T11:10:30+08:00",
                subject_view=True,
            )
            self.assertEqual(resp_sugg["cohabitation"]["suggestions"][-1]["status"], "ACCEPTED")

            # Partner cannot unilaterally lend subject's private item without consent
            with self.assertRaises(stance_containment_view.StanceContainmentPermissionError):
                stance_containment_view.apply_stance_containment_action(
                    root,
                    {"action": "lend_item", "actor": "partner", "item_id": "fountain_pen", "mode": "lend"},
                    now="2026-11-14T11:11:00+08:00",
                    subject_view=False,
                )
            lend_ok = stance_containment_view.apply_stance_containment_action(
                root,
                {"action": "lend_item", "actor": "subject", "item_id": "fountain_pen", "mode": "lend"},
                now="2026-11-14T11:11:15+08:00",
                subject_view=True,
            )
            self.assertEqual(lend_ok["cohabitation"]["loans"]["fountain_pen"]["status"], "LENT")

            lamp_prev = stance_containment_view.apply_stance_containment_action(
                root,
                {
                    "action": "preview_lamp_move",
                    "actor": "partner",
                    "from_place_id": "desk_left",
                    "to_place_id": "desk_right",
                    "commit": False,
                },
                now="2026-11-14T11:12:00+08:00",
                subject_view=False,
            )
            self.assertEqual(lamp_prev["cohabitation"]["lamp_preview"]["status"], "PREVIEW")

            # 4. Ch. 35 everyday_desire_lifecycle: turn_only natural fade-out + 6-concern conflict arbitration
            v03_clock_evaporation.apply_v03_clock_action(
                root,
                {
                    "action": "emerge_concern",
                    "domain": "exploration",
                    "origin_kind": "immediate_opportunity",
                    "origin_refs": ["window:rain_clouds"],
                    "expression": "想顺手瞄一眼窗外云层",
                    "persistence": "turn_only",
                    "cost_scale": "micro_inline",
                },
                actor_side="subject",
                now_iso="2026-11-14T11:15:00+08:00",
            )
            v03_clock_evaporation.apply_v03_clock_action(
                root,
                {
                    "action": "emerge_concern",
                    "domain": "possession",
                    "origin_kind": "sustained_interest",
                    "origin_refs": ["shop:watercolor_paper_pack"],
                    "expression": "想买一包棉浆水彩纸",
                    "persistence": "retained",
                    "cost_scale": "macro_outing",
                    "estimated_cost_jpy": 3200,
                },
                actor_side="subject",
                now_iso="2026-11-14T11:15:10+08:00",
            )
            v03_clock_evaporation.apply_v03_clock_action(
                root,
                {
                    "action": "emerge_concern",
                    "domain": "intimacy",
                    "origin_kind": "relational_wish",
                    "origin_refs": ["evening:quiet_couch"],
                    "expression": "晚点想靠在一起安静待一会儿",
                    "persistence": "retained",
                    "cost_scale": "needs_breakpoint",
                },
                actor_side="subject",
                now_iso="2026-11-14T11:15:20+08:00",
            )

            arb_res = v03_clock_evaporation.apply_v03_clock_action(
                root,
                {
                    "action": "arbitrate_concerns",
                    "foreground_activity": {"activity_id": "ink_linework", "interruptibility": "ATOMIC"},
                    "budget_context": {"available_jpy": 1500},
                    "channel_visibility": "ordinary_work",
                    "adult_gate_open": False,
                },
                actor_side="subject",
                now_iso="2026-11-14T11:16:00+08:00",
            )
            self.assertTrue(arb_res["ok"])
            arb = arb_res["data"]["everyday_desires"]["last_arbitration"]
            self.assertTrue(arb["total_utility_score_forbidden"])
            # Intimacy concern is strictly withheld on ordinary_work channel; over-budget possession is deferred!
            self.assertTrue(
                any(fc.get("domain") == "intimacy" for fc in arb.get("filtered_concerns", []))
            )
            self.assertTrue(
                any(fc.get("reason_code") == "DEFERRED_BY_BUDGET_ENVELOPE" for fc in arb.get("filtered_concerns", []))
            )

            # Advancing turn silently fades turn_only concern with zero frustration debt!
            fade_res = v03_clock_evaporation.apply_v03_clock_action(
                root,
                {"action": "advance_desire_turn"},
                actor_side="subject",
                now_iso="2026-11-14T11:17:00+08:00",
            )
            self.assertEqual(fade_res["data"]["action_receipt"]["faded_turn_only_count"], 1)
            self.assertEqual(fade_res["data"]["action_receipt"]["frustration_debt"], 0)
            self.assertEqual(len(fade_res["data"]["everyday_desires"]["turn_only_concerns"]), 0)

    def test_zero_write_reads_and_pure_stdlib_ast_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty_root = Path(tmp) / "untouched_day_root"
            v03_embodied_social_view.read_v03_embodied_social_view(
                empty_root, has_subject_capability=False, now_iso="2026-11-14T12:00:00+08:00"
            )
            stance_containment_view.read_stance_containment(
                empty_root, now="2026-11-14T12:00:00+08:00", subject_view=False
            )
            life_dwell.project_life_dwell(empty_root, now_iso="2026-11-14T12:00:00+08:00")
            everyday_desire_lifecycle.project_everyday_desires(empty_root, now_iso="2026-11-14T12:00:00+08:00")
            v03_clock_evaporation.read_v03_clock_view(
                empty_root, has_subject_capability=False, now_iso="2026-11-14T12:00:00+08:00"
            )
            self.assertFalse(empty_root.exists(), "GET/read projections must be 100% zero-write!")

        forbidden_imports = {"random", "secrets", "openai", "anthropic", "httpx", "requests", "aiohttp"}
        target_files = [
            REPO_ROOT / "components" / "world" / "scripts" / "v03_embodied_npc_ecology.py",
            REPO_ROOT / "components" / "phone" / "v03_embodied_social_view.py",
            REPO_ROOT / "components" / "phone" / "stance_containment_view.py",
            REPO_ROOT / "components" / "life" / "scripts" / "life_attention_stance_containment.py",
            REPO_ROOT / "components" / "phone" / "somatic_comfort_view.py",
            REPO_ROOT / "components" / "life" / "scripts" / "v03_clock_evaporation.py",
            REPO_ROOT / "components" / "life" / "scripts" / "life_dwell.py",
            REPO_ROOT / "components" / "life" / "scripts" / "attention_view_projector.py",
            REPO_ROOT / "components" / "life" / "scripts" / "everyday_desire_lifecycle.py",
        ]
        for fpath in target_files:
            tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root_mod = alias.name.split(".")[0]
                        self.assertNotIn(root_mod, forbidden_imports, f"Forbidden import {root_mod} in {fpath.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root_mod = node.module.split(".")[0]
                    self.assertNotIn(root_mod, forbidden_imports, f"Forbidden import {root_mod} in {fpath.name}")


if __name__ == "__main__":
    unittest.main()

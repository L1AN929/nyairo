#!/usr/bin/env python3
"""Unit tests for v3.0 GAP-05 Partner Physical Gift Escrow -> Room Matter Materialization (Ch.04 / ADR-039)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components.phone import autonomy_rhythm_view as rhythm_view
from components.phone.v03_ecology_view import mutate_v03_ecology_view, read_v03_ecology_view
from components.phone.v08_living_traces_view import read_v08_living_traces_view
from components.world.scripts import v03_economy_illness_bridge as bridge
from components.world.scripts import v08_universal_matter as matter
from components.world.scripts import world_property_economy as wpe


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Capture relative path -> (st_mtime_ns, st_size) for strict zero-write assertions."""
    out: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(root))] = (st.st_mtime_ns, st.st_size)
    return out


class TestV08Gap05PartnerGiftEscrow(unittest.TestCase):
    """Comprehensive tests for 72h partner physical item gift escrow & v08_universal_matter materialization."""

    def test_01_offer_and_accept_materializes_real_matter_on_desk_surface_wallet_untouched(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nyairo-gap05-accept-") as tmp:
            day_root = Path(tmp) / "day_root"
            day_root.mkdir(parents=True, exist_ok=True)

            wpe.initialize_genesis(
                hermes_home=day_root,
                environment={wpe.FEATURE_ENV: "1"},
                created_at="2026-11-14T08:00:00+08:00",
            )
            c_work, r_work = mutate_v03_ecology_view(
                day_root,
                {
                    "action": "complete_independent_work",
                    "job_kind": "proofreading_shift",
                    "work_ref": "ch04_shift_01",
                    "amount_cents": 1_500,
                },
                actor_side="subject",
                now_iso="2026-11-14T09:00:00+08:00",
            )
            self.assertEqual(c_work, 200)
            wallet_before = r_work["data"]["economy"]["wallet_split"].copy()
            avail_before = r_work["data"]["economy"]["available_balance_cents"]

            # Free one slot on home_desk by placing pen_01 into ceramic_cup_01
            matter.compose_open_act(
                day_root,
                {
                    "intent_cn": "腾出书桌一角",
                    "steps": [
                        {
                            "op": "PLACE_OR_MOVE",
                            "object_id": "pen_01",
                            "target_parent_id": "ceramic_cup_01",
                        }
                    ],
                },
                actor_side="subject",
                now_iso="2026-11-14T09:30:00+08:00",
            )

            # Partner offers physical gift targeting desk_surface
            c_off, r_off = mutate_v03_ecology_view(
                day_root,
                {
                    "action": "offer_item_gift",
                    "item_label_cn": "黄铜小书镇",
                    "item_kind": "decor",
                    "estimated_jpy": 2_800,
                    "target_zone_id": "desk_surface",
                    "note": "书展顺手带的书镇",
                    "operation_key": "op_gift_offer_01",
                },
                actor_side="partner",
                now_iso="2026-11-14T10:00:00+08:00",
            )
            self.assertEqual(c_off, 200)
            self.assertEqual(r_off["llm_calls"], 0)
            gift_id = r_off["receipt"]["gift_id"]
            self.assertTrue(gift_id.startswith("item_gift_"))
            self.assertFalse(r_off["receipt"]["materialized_immediately"])
            self.assertFalse(r_off["receipt"]["wallet_credited_immediately"])

            eco_off = r_off["data"]["economy"]
            self.assertEqual(eco_off["item_gift_escrow_pending_count"], 1)
            self.assertEqual(eco_off["wallet_split"], wallet_before)
            self.assertEqual(eco_off["available_balance_cents"], avail_before)

            gift_row = eco_off["item_gift_escrow"][0]
            self.assertEqual(gift_row["gift_id"], gift_id)
            self.assertEqual(gift_row["status"], "OFFERED")
            self.assertEqual(gift_row["offered_at"], "2026-11-14T10:00:00+08:00")
            self.assertEqual(gift_row["expires_at"], "2026-11-17T10:00:00+08:00")
            self.assertIsNone(gift_row["decided_at"])
            self.assertIsNone(gift_row["materialized_object_id"])
            self.assertEqual(gift_row["provenance"], "partner_gift")
            self.assertFalse(gift_row["intimacy_bearing"])
            self.assertFalse(gift_row["consent_grant"])
            self.assertTrue(gift_row["mood_delta_forbidden"])
            self.assertEqual(gift_row["omission_debt"], 0)

            # While still OFFERED in escrow, object is NOT materialized in v08_universal_matter
            _, lt_before = read_v08_living_traces_view(
                day_root,
                has_subject_capability=True,
                now_iso="2026-11-14T10:05:00+08:00",
            )
            labels_before = {
                obj["label_cn"]
                for obj in (lt_before["data"]["matter_space"].get("objects") or [])
            }
            self.assertNotIn("黄铜小书镇", labels_before)

            # Subject accepts the gift -> materializes into v08_universal_matter on home_desk
            c_acc, r_acc = mutate_v03_ecology_view(
                day_root,
                {
                    "action": "decide_item_gift",
                    "gift_id": gift_id,
                    "decision": "accept",
                    "note": "正好压画稿边角",
                    "operation_key": "op_gift_accept_01",
                },
                actor_side="subject",
                now_iso="2026-11-14T10:20:00+08:00",
            )
            self.assertEqual(c_acc, 200)
            self.assertEqual(r_acc["llm_calls"], 0)
            mat_obj_id = r_acc["receipt"]["materialized_object_id"]
            self.assertIsInstance(mat_obj_id, str)
            self.assertTrue(len(mat_obj_id) > 0)
            self.assertEqual(r_acc["receipt"]["gift_status"], "ACCEPTED")
            self.assertEqual(r_acc["receipt"]["materialized_zone_id"], "home_desk")
            self.assertEqual(r_acc["receipt"]["omission_debt"], 0)
            self.assertFalse(r_acc["receipt"]["intimacy_bearing"])

            eco_acc = r_acc["data"]["economy"]
            self.assertEqual(eco_acc["item_gift_escrow_pending_count"], 0)
            self.assertEqual(eco_acc["wallet_split"], wallet_before)
            self.assertEqual(eco_acc["self_earned_cents"], 1_500)
            self.assertEqual(eco_acc["gift_balance_cents"], 0)
            self.assertEqual(eco_acc["available_balance_cents"], avail_before)

            accepted_row = eco_acc["item_gift_escrow"][0]
            self.assertEqual(accepted_row["status"], "ACCEPTED")
            self.assertEqual(accepted_row["decided_at"], "2026-11-14T10:20:00+08:00")
            self.assertEqual(accepted_row["materialized_object_id"], mat_obj_id)
            self.assertEqual(accepted_row["omission_debt"], 0)
            self.assertFalse(accepted_row["intimacy_bearing"])

            # Verify read_v08_living_traces_view sees the real physical object on home_desk
            lt_code_s, lt_subj = read_v08_living_traces_view(
                day_root,
                has_subject_capability=True,
                now_iso="2026-11-14T10:25:00+08:00",
            )
            lt_code_p, lt_part = read_v08_living_traces_view(
                day_root,
                has_subject_capability=False,
                now_iso="2026-11-14T10:25:00+08:00",
            )
            self.assertEqual(lt_code_s, 200)
            self.assertEqual(lt_code_p, 200)
            for view_env in (lt_subj, lt_part):
                objs = {
                    o["object_id"]: o
                    for o in (view_env["data"]["matter_space"].get("objects") or [])
                }
                self.assertIn(mat_obj_id, objs)
                mat_obj = objs[mat_obj_id]
                self.assertEqual(mat_obj["label_cn"], "黄铜小书镇")
                self.assertEqual(mat_obj["item_kind"], "keepsake")
                self.assertEqual(mat_obj["parent_id"], "home_desk")
                self.assertIsNone(mat_obj.get("lent_by"))

    def test_02_friendly_item_kind_aliases_and_crowded_default_desk_overflow(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nyairo-gap05-kinds-") as tmp:
            day_root = Path(tmp) / "day_root"
            day_root.mkdir(parents=True, exist_ok=True)

            samples = [
                ("栗子铜锣烧", "snack", "prepared_food", None),
                ("热焙茶小罐", "beverage", "drink", "dining_table"),
                ("旧版植物图谱", "book", "paper", "home_staging_corner"),
            ]
            created_object_ids: list[tuple[str, str]] = []
            for idx, (label, alias_kind, expected_matter_kind, zone) in enumerate(samples, start=1):
                offer_body: dict[str, object] = {
                    "action": "offer_item_gift",
                    "item_label_cn": label,
                    "item_kind": alias_kind,
                    "estimated_jpy": 600 * idx,
                }
                if zone is not None:
                    offer_body["target_zone_id"] = zone
                c_o, r_o = mutate_v03_ecology_view(
                    day_root,
                    offer_body,
                    actor_side="partner",
                    now_iso=f"2026-11-14T11:0{idx}:00+08:00",
                )
                self.assertEqual(c_o, 200)
                self.assertEqual(r_o["receipt"]["item_kind"], expected_matter_kind)
                gid = r_o["receipt"]["gift_id"]

                c_a, r_a = mutate_v03_ecology_view(
                    day_root,
                    {
                        "action": "decide_item_gift",
                        "gift_id": gid,
                        "decision": "accept",
                    },
                    actor_side="subject",
                    now_iso=f"2026-11-14T11:1{idx}:00+08:00",
                )
                self.assertEqual(c_a, 200)
                created_object_ids.append((r_a["receipt"]["materialized_object_id"], expected_matter_kind))

            _, lt_view = read_v08_living_traces_view(
                day_root,
                has_subject_capability=True,
                now_iso="2026-11-14T11:30:00+08:00",
            )
            objects_by_id = {
                o["object_id"]: o
                for o in (lt_view["data"]["matter_space"].get("objects") or [])
            }
            for obj_id, expected_kind in created_object_ids:
                self.assertIn(obj_id, objects_by_id)
                self.assertEqual(objects_by_id[obj_id]["item_kind"], expected_kind)

    def test_03_decline_leaves_zero_penalty_zero_debt_and_creates_no_matter_object(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nyairo-gap05-decline-") as tmp:
            day_root = Path(tmp) / "day_root"
            day_root.mkdir(parents=True, exist_ok=True)

            _, r_off = mutate_v03_ecology_view(
                day_root,
                {
                    "action": "offer_item_gift",
                    "item_label_cn": "浓香型室内扩香瓶",
                    "estimated_jpy": 3_500,
                    "note": "放桌上试试",
                },
                actor_side="partner",
                now_iso="2026-11-14T12:00:00+08:00",
            )
            gid = r_off["receipt"]["gift_id"]

            c_dec, r_dec = mutate_v03_ecology_view(
                day_root,
                {
                    "action": "decide_item_gift",
                    "gift_id": gid,
                    "decision": "decline",
                    "note": "画画时闻浓香味容易分神，先不用啦",
                    "private_note": "私下记一笔气味偏好",
                },
                actor_side="subject",
                now_iso="2026-11-14T12:15:00+08:00",
            )
            self.assertEqual(c_dec, 200)
            self.assertEqual(r_dec["receipt"]["gift_status"], "DECLINED")
            self.assertIsNone(r_dec["receipt"]["materialized_object_id"])
            self.assertEqual(r_dec["receipt"]["omission_debt"], 0)
            self.assertEqual(r_dec["receipt"]["relationship_penalty"], 0)
            self.assertTrue(r_dec["receipt"]["mood_delta_forbidden"])
            self.assertFalse((day_root / "world" / "v08_matter_space.json").exists())

            _, partner_get = read_v03_ecology_view(
                day_root,
                has_subject_capability=False,
                now_iso="2026-11-14T12:20:00+08:00",
            )
            p_gift = partner_get["data"]["economy"]["item_gift_escrow"][0]
            self.assertEqual(p_gift["status"], "DECLINED")
            self.assertEqual(p_gift["omission_debt"], 0)
            self.assertEqual(p_gift["decision_note"], "画画时闻浓香味容易分神，先不用啦")
            self.assertNotIn("private_note", p_gift)

    def test_04_72h_expiry_zero_write_projection_late_accept_409_and_lazy_sweep(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nyairo-gap05-expiry-") as tmp:
            day_root = Path(tmp) / "day_root"
            day_root.mkdir(parents=True, exist_ok=True)

            _, r_off = mutate_v03_ecology_view(
                day_root,
                {
                    "action": "offer_item_gift",
                    "item_label_cn": "玻璃晴雨小摆件",
                    "estimated_jpy": 1_800,
                },
                actor_side="partner",
                now_iso="2026-11-14T10:00:00+08:00",
            )
            gid = r_off["receipt"]["gift_id"]

            # 71h59m later -> still OFFERED
            _, r_71h = read_v03_ecology_view(
                day_root,
                has_subject_capability=True,
                now_iso="2026-11-17T09:59:00+08:00",
            )
            self.assertEqual(r_71h["data"]["economy"]["item_gift_escrow"][0]["status"], "OFFERED")
            self.assertEqual(r_71h["data"]["economy"]["item_gift_escrow_pending_count"], 1)

            # 73h later -> zero-write GET projects EXPIRED without touching disk
            snap_before_get = _tree_snapshot(day_root)
            c_73h, r_73h = read_v03_ecology_view(
                day_root,
                has_subject_capability=True,
                now_iso="2026-11-17T11:00:00+08:00",
            )
            snap_after_get = _tree_snapshot(day_root)
            self.assertEqual(c_73h, 200)
            self.assertEqual(snap_before_get, snap_after_get)
            exp_row = r_73h["data"]["economy"]["item_gift_escrow"][0]
            self.assertEqual(exp_row["status"], "EXPIRED")
            self.assertEqual(exp_row["omission_debt"], 0)
            self.assertEqual(r_73h["data"]["economy"]["item_gift_escrow_pending_count"], 0)

            # Late accept at +73h -> 409 GIFT_ESCROW_EXPIRED, zero disk write, no matter created
            snap_before_err = _tree_snapshot(day_root)
            c_late, r_late = mutate_v03_ecology_view(
                day_root,
                {
                    "action": "decide_item_gift",
                    "gift_id": gid,
                    "decision": "accept",
                },
                actor_side="subject",
                now_iso="2026-11-17T11:00:00+08:00",
            )
            snap_after_err = _tree_snapshot(day_root)
            self.assertEqual(c_late, 409)
            self.assertEqual(r_late["reason_code"], "GIFT_ESCROW_EXPIRED")
            self.assertEqual(r_late["omission_debt"], 0)
            self.assertEqual(snap_before_err, snap_after_err)
            self.assertFalse((day_root / "world" / "v08_matter_space.json").exists())

            # Late partner withdraw at +73h -> 409 GIFT_ESCROW_EXPIRED
            c_w_late, r_w_late = mutate_v03_ecology_view(
                day_root,
                {"action": "withdraw_item_gift", "gift_id": gid},
                actor_side="partner",
                now_iso="2026-11-17T11:05:00+08:00",
            )
            self.assertEqual(c_w_late, 409)
            self.assertEqual(r_w_late["reason_code"], "GIFT_ESCROW_EXPIRED")

            # Subsequent normal clock/ecology mutation lazily sweeps stored status to EXPIRED
            c_adv, _ = mutate_v03_ecology_view(
                day_root,
                {
                    "action": "advance_illness_recovery",
                    "target_now_iso": "2026-11-17T12:00:00+08:00",
                },
                actor_side="subject",
                now_iso="2026-11-17T12:00:00+08:00",
            )
            self.assertEqual(c_adv, 200)
            stored_state, err = bridge.read_stored_ecology_zero_write(day_root)
            self.assertIsNone(err)
            self.assertEqual(stored_state["economy"]["item_gift_escrow"][gid]["status"], "EXPIRED")
            self.assertEqual(stored_state["economy"]["item_gift_escrow"][gid]["omission_debt"], 0)

    def test_05_withdraw_before_vs_after_decision_newest_first_order_and_404(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nyairo-gap05-withdraw-") as tmp:
            day_root = Path(tmp) / "day_root"
            day_root.mkdir(parents=True, exist_ok=True)

            # Missing gift -> 404 GIFT_NOT_FOUND
            c_404, r_404 = mutate_v03_ecology_view(
                day_root,
                {"action": "withdraw_item_gift", "gift_id": "item_gift_nonexistent"},
                actor_side="partner",
                now_iso="2026-11-14T10:00:00+08:00",
            )
            self.assertEqual(c_404, 404)
            self.assertEqual(r_404["reason_code"], "GIFT_NOT_FOUND")

            _, off1 = mutate_v03_ecology_view(
                day_root,
                {"action": "offer_item_gift", "item_label_cn": "第一份书签"},
                actor_side="partner",
                now_iso="2026-11-14T10:01:00+08:00",
            )
            g1 = off1["receipt"]["gift_id"]

            _, off2 = mutate_v03_ecology_view(
                day_root,
                {"action": "offer_item_gift", "item_label_cn": "第二份木质笔搁"},
                actor_side="partner",
                now_iso="2026-11-14T10:05:00+08:00",
            )
            g2 = off2["receipt"]["gift_id"]

            # Projection is sorted newest-first
            projected_ids = [g["gift_id"] for g in off2["data"]["economy"]["item_gift_escrow"]]
            self.assertEqual(projected_ids, [g2, g1])
            self.assertEqual(off2["data"]["economy"]["item_gift_escrow_pending_count"], 2)

            # Withdraw g1 before decision -> WITHDRAWN_BY_PARTNER
            c_w1, r_w1 = mutate_v03_ecology_view(
                day_root,
                {"action": "withdraw_item_gift", "gift_id": g1},
                actor_side="partner",
                now_iso="2026-11-14T10:10:00+08:00",
            )
            self.assertEqual(c_w1, 200)
            self.assertEqual(r_w1["receipt"]["gift_status"], "WITHDRAWN_BY_PARTNER")
            self.assertEqual(r_w1["data"]["economy"]["item_gift_escrow_pending_count"], 1)

            # Second withdraw on g1 or subject accept on withdrawn g1 -> 409 GIFT_NOT_PENDING
            c_w1_again, r_w1_again = mutate_v03_ecology_view(
                day_root,
                {"action": "withdraw_item_gift", "gift_id": g1},
                actor_side="partner",
                now_iso="2026-11-14T10:11:00+08:00",
            )
            self.assertEqual(c_w1_again, 409)
            self.assertEqual(r_w1_again["reason_code"], "GIFT_NOT_PENDING")

            c_dec_w1, r_dec_w1 = mutate_v03_ecology_view(
                day_root,
                {"action": "decide_item_gift", "gift_id": g1, "decision": "accept"},
                actor_side="subject",
                now_iso="2026-11-14T10:12:00+08:00",
            )
            self.assertEqual(c_dec_w1, 409)
            self.assertEqual(r_dec_w1["reason_code"], "GIFT_NOT_PENDING")

            # Subject accepts g2 -> partner withdraw after decision fails with 409 GIFT_NOT_PENDING
            c_acc2, _ = mutate_v03_ecology_view(
                day_root,
                {"action": "decide_item_gift", "gift_id": g2, "decision": "accept"},
                actor_side="subject",
                now_iso="2026-11-14T10:15:00+08:00",
            )
            self.assertEqual(c_acc2, 200)

            c_w2_after, r_w2_after = mutate_v03_ecology_view(
                day_root,
                {"action": "withdraw_item_gift", "gift_id": g2},
                actor_side="partner",
                now_iso="2026-11-14T10:16:00+08:00",
            )
            self.assertEqual(c_w2_after, 409)
            self.assertEqual(r_w2_after["reason_code"], "GIFT_NOT_PENDING")

    def test_06_rbac_403_private_drawer_403_and_transactional_anti_bribery_403(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nyairo-gap05-guards-") as tmp:
            day_root = Path(tmp) / "day_root"
            day_root.mkdir(parents=True, exist_ok=True)

            # 1) Partner calling decide_item_gift -> 403 FORBIDDEN_ACTOR_SIDE
            c_p_dec, r_p_dec = mutate_v03_ecology_view(
                day_root,
                {"action": "decide_item_gift", "gift_id": "item_gift_any", "decision": "accept"},
                actor_side="partner",
                now_iso="2026-11-14T10:00:00+08:00",
            )
            self.assertEqual(c_p_dec, 403)
            self.assertEqual(r_p_dec["reason_code"], "FORBIDDEN_ACTOR_SIDE")

            # 2) Subject calling offer_item_gift or withdraw_item_gift -> 403 FORBIDDEN_ACTOR_SIDE
            c_s_off, r_s_off = mutate_v03_ecology_view(
                day_root,
                {"action": "offer_item_gift", "item_label_cn": "自发礼物"},
                actor_side="subject",
                now_iso="2026-11-14T10:01:00+08:00",
            )
            self.assertEqual(c_s_off, 403)
            self.assertEqual(r_s_off["reason_code"], "FORBIDDEN_ACTOR_SIDE")

            c_s_wd, r_s_wd = mutate_v03_ecology_view(
                day_root,
                {"action": "withdraw_item_gift", "gift_id": "item_gift_any"},
                actor_side="subject",
                now_iso="2026-11-14T10:02:00+08:00",
            )
            self.assertEqual(c_s_wd, 403)
            self.assertEqual(r_s_wd["reason_code"], "FORBIDDEN_ACTOR_SIDE")

            # 3) Private drawer or any private target zone -> 403 PRIVATE_ZONE_FORBIDDEN
            for bad_zone in ("subject_private_drawer", "private_drawer", "private_keep_box"):
                c_priv, r_priv = mutate_v03_ecology_view(
                    day_root,
                    {
                        "action": "offer_item_gift",
                        "item_label_cn": "陶瓷胸针",
                        "target_zone_id": bad_zone,
                    },
                    actor_side="partner",
                    now_iso="2026-11-14T10:05:00+08:00",
                )
                self.assertEqual(c_priv, 403)
                self.assertEqual(r_priv["reason_code"], "PRIVATE_ZONE_FORBIDDEN")

            # 4) Transactional / consent-buying note, label, or context_tag -> 403
            for bad_payload in (
                {
                    "action": "offer_item_gift",
                    "item_label_cn": "丝绒礼盒",
                    "note": "收下礼物就乖乖听话别拒绝我",
                },
                {
                    "action": "offer_item_gift",
                    "item_label_cn": "答应亲密交换券",
                },
                {
                    "action": "offer_item_gift",
                    "item_label_cn": "普通银链",
                    "context_tag": "adult_intimacy",
                },
            ):
                c_tx, r_tx = mutate_v03_ecology_view(
                    day_root,
                    bad_payload,
                    actor_side="partner",
                    now_iso="2026-11-14T10:10:00+08:00",
                )
                self.assertEqual(c_tx, 403)
                self.assertIn(
                    r_tx["reason_code"],
                    {"TRANSACTIONAL_FUNDING_FORBIDDEN", "TRANSACTIONAL_GIFT_FORBIDDEN"},
                )

            # 5) Active REFUSED_FIRM lock blocks gift offers with 403
            r_state0, _ = rhythm_view.read_autonomy_rhythm_state(day_root)
            r_state1, _ = rhythm_view.apply_autonomy_action(
                r_state0,
                action="declare_refusal",
                payload={"topic_label": "深夜强行打断休息", "category": "errand_outsourcing"},
                subject_view=True,
                now_iso="2026-11-14T11:00:00+08:00",
            )
            rhythm_view.write_autonomy_rhythm_state(day_root, r_state1)

            c_lock, r_lock = mutate_v03_ecology_view(
                day_root,
                {"action": "offer_item_gift", "item_label_cn": "赔罪蛋糕"},
                actor_side="partner",
                now_iso="2026-11-14T11:05:00+08:00",
            )
            self.assertEqual(c_lock, 403)
            self.assertEqual(r_lock["reason_code"], "TRANSACTIONAL_FUNDING_FORBIDDEN")

    def test_07_max_open_cap_idempotent_operation_key_and_legacy_state_compat(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nyairo-gap05-idem-") as tmp:
            day_root = Path(tmp) / "day_root"
            day_root.mkdir(parents=True, exist_ok=True)

            # Legacy state file without item_gift_escrow key stays 100% compatible
            legacy = bridge.default_ecology_state()
            legacy["economy"].pop("item_gift_escrow", None)
            eco_file = bridge.ecology_state_path(day_root)
            eco_file.parent.mkdir(parents=True, exist_ok=True)
            eco_file.write_text(json.dumps(legacy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            snap0 = _tree_snapshot(day_root)
            c_get0, r_get0 = read_v03_ecology_view(
                day_root,
                has_subject_capability=False,
                now_iso="2026-11-14T09:00:00+08:00",
            )
            self.assertEqual(c_get0, 200)
            self.assertEqual(_tree_snapshot(day_root), snap0)
            self.assertEqual(r_get0["data"]["economy"]["item_gift_escrow"], [])
            self.assertEqual(r_get0["data"]["economy"]["item_gift_escrow_pending_count"], 0)

            # Idempotent offer replay + MAX_OPEN_ITEM_GIFTS = 5 enforcement
            offer_payload = {
                "action": "offer_item_gift",
                "item_label_cn": "手作陶杯_1",
                "item_kind": "vessel",
                "estimated_jpy": 1_200,
                "target_zone_id": "dining_table",
                "operation_key": "idem_gift_offer_1",
            }
            c_o1, r_o1 = mutate_v03_ecology_view(
                day_root,
                offer_payload,
                actor_side="partner",
                now_iso="2026-11-14T09:10:00+08:00",
            )
            c_o1_dup, r_o1_dup = mutate_v03_ecology_view(
                day_root,
                offer_payload,
                actor_side="partner",
                now_iso="2026-11-14T09:11:00+08:00",
            )
            self.assertEqual(c_o1, 200)
            self.assertEqual(c_o1_dup, 200)
            self.assertEqual(r_o1["command_status"], "APPLIED")
            self.assertEqual(r_o1_dup["command_status"], "DUPLICATE")
            self.assertEqual(r_o1["receipt"]["gift_id"], r_o1_dup["receipt"]["gift_id"])
            first_gid = r_o1["receipt"]["gift_id"]

            # Tampered payload with same operation_key -> 409 OPERATION_FINGERPRINT_MISMATCH
            c_mis, r_mis = mutate_v03_ecology_view(
                day_root,
                {**offer_payload, "estimated_jpy": 9_900},
                actor_side="partner",
                now_iso="2026-11-14T09:12:00+08:00",
            )
            self.assertEqual(c_mis, 409)
            self.assertEqual(r_mis["reason_code"], "OPERATION_FINGERPRINT_MISMATCH")

            # Fill remaining 4 open slots -> total 5 open offers
            for idx in range(2, bridge.MAX_OPEN_ITEM_GIFTS + 1):
                c_fill, _ = mutate_v03_ecology_view(
                    day_root,
                    {
                        "action": "offer_item_gift",
                        "item_label_cn": f"手作小物_{idx}",
                        "operation_key": f"idem_gift_offer_{idx}",
                    },
                    actor_side="partner",
                    now_iso=f"2026-11-14T09:2{idx}:00+08:00",
                )
                self.assertEqual(c_fill, 200)

            # 6th concurrent open offer -> 409 GIFT_ESCROW_FULL
            c_full, r_full = mutate_v03_ecology_view(
                day_root,
                {
                    "action": "offer_item_gift",
                    "item_label_cn": "超限第六件礼物",
                    "operation_key": "idem_gift_offer_6",
                },
                actor_side="partner",
                now_iso="2026-11-14T09:30:00+08:00",
            )
            self.assertEqual(c_full, 409)
            self.assertEqual(r_full["reason_code"], "GIFT_ESCROW_FULL")

            # Idempotent accept replay materializes exactly 1 object in v08_universal_matter
            accept_payload = {
                "action": "decide_item_gift",
                "gift_id": first_gid,
                "decision": "accept",
                "operation_key": "idem_gift_accept_1",
            }
            c_acc1, r_acc1 = mutate_v03_ecology_view(
                day_root,
                accept_payload,
                actor_side="subject",
                now_iso="2026-11-14T09:40:00+08:00",
            )
            c_acc1_dup, r_acc1_dup = mutate_v03_ecology_view(
                day_root,
                accept_payload,
                actor_side="subject",
                now_iso="2026-11-14T09:41:00+08:00",
            )
            self.assertEqual(c_acc1, 200)
            self.assertEqual(c_acc1_dup, 200)
            self.assertEqual(r_acc1["command_status"], "APPLIED")
            self.assertEqual(r_acc1_dup["command_status"], "DUPLICATE")
            self.assertEqual(
                r_acc1["receipt"]["materialized_object_id"],
                r_acc1_dup["receipt"]["materialized_object_id"],
            )

    def test_08_zero_write_get_and_strict_field_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nyairo-gap05-valid-") as tmp:
            day_root = Path(tmp) / "day_root"
            day_root.mkdir(parents=True, exist_ok=True)

            # Validation rejections leave 0 bytes on disk
            invalid_cases = [
                ({"action": "offer_item_gift", "item_label_cn": "x" * 41}, "partner"),
                ({"action": "offer_item_gift", "item_label_cn": "合法书签", "estimated_jpy": -1}, "partner"),
                ({"action": "offer_item_gift", "item_label_cn": "合法书签", "estimated_jpy": 50_001}, "partner"),
                ({"action": "offer_item_gift", "item_label_cn": "合法书签", "item_kind": "spaceship"}, "partner"),
                ({"action": "offer_item_gift", "item_label_cn": "合法书签", "target_zone_id": "unknown_zone"}, "partner"),
                ({"action": "offer_item_gift", "item_label_cn": "合法书签", "note": "n" * 161}, "partner"),
                ({"action": "offer_item_gift", "item_label_cn": "合法书签", "mood_delta": 5}, "partner"),
                ({"action": "decide_item_gift", "gift_id": "item_gift_1", "decision": "maybe"}, "subject"),
            ]
            for payload, side in invalid_cases:
                code, _ = mutate_v03_ecology_view(
                    day_root,
                    payload,
                    actor_side=side,
                    now_iso="2026-11-14T10:00:00+08:00",
                )
                self.assertEqual(code, 400, msg=f"Expected 400 for {payload}")

            self.assertEqual(_tree_snapshot(day_root), {})


if __name__ == "__main__":
    unittest.main()

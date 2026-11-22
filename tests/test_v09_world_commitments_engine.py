#!/usr/bin/env python3
"""v0.9 Pillar C unit & concurrency test suite for ``world_commitments_engine.py``.

Covers:
1. Instance-bound persistent NPC unification (distinct seeds -> distinct identities;
   same instance -> stable across reloads; WorldGen ``npcs.json`` adoption from both
   ``one-day/world-package`` and ``world-package``; legacy alias resolution).
2. 72-hour zero-idle-spin proof: dormant NPC with no open commitments produces
   0 disk writes (``check_heartbeat_needed_zero_write == False``) and 0 LLM calls
   over 72 simulated hours while ``derive_npc_presence_zero_write`` remains accurate.
3. Full ``BOOK_OR_ITEM_RESERVATION`` lifecycle:
   - ``ORDERED`` -> ``ARRIVED_HOLDING`` -> ``PICKED_UP`` via ``DIRECT_CONTACT``
   - ``ORDERED`` -> ``ARRIVED_HOLDING`` -> ``HOLD_EXPIRED`` at +72h with honest
     notice ``"到货保留期满未取，已转上架"`` and real shelf release via
     ``world_content_catalog.register_catalog_batch``
   - ``supply_delayed`` exception path with budgeted ``decision_callback`` (allowed
     and budget-refused fallback with ``llm_calls == 0``)
4. ``SCHEDULED_APPOINTMENT`` lifecycle (``FULFILLED``, ``MISSED_BY_SUBJECT``,
   rain -> ``RESCHEDULED_INDOOR`` and ``CANCELLED_WITH_NOTICE``).
5. ``INDEPENDENT_NPC_PLAN`` (``TEMPORARY_CLOSE`` weekend book fair closes shop &
   leaves door notice whether TA visits or misses it; ``NEW_TOPIC_READY``;
   ``CONDITION_CHANGED`` live ``"rain"`` cancels ``OUTDOOR_STALL`` / ``porch_market``).
6. Epistemic boundary (``subject_observed_venues`` / ``observed_by_subject``) so TA
   does not omnisciently know unobserved door notices before visiting.
7. ``400 RETROACTIVE_FABRICATION_FORBIDDEN`` when smuggling ``subject_mood``,
   ``private_desire``, ``recent_chat_topic``, ``fabricate_backstory``, or
   ``retroactive_event`` into ``contact_npc_or_venue`` (0 disk writes).
8. 10-thread concurrent ``create_commitment`` (10/10 persisted, same
   ``operation_key`` idempotent, conflicting payload -> 409), symlink rejection,
   ``0600`` atomic UTF-8 LF persistence, unified forget-barrier filtering &
   fail-closed read behavior, and 100% zero-write reads.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(REPO_ROOT),
    str(REPO_ROOT / "components" / "world" / "scripts"),
    str(REPO_ROOT / "components" / "life" / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import life_request_budget as _lrb  # noqa: E402
from components.life.scripts import v02_representation_lease_forget as _rlf  # noqa: E402
from components.world.scripts import world_commitments_engine as engine  # noqa: E402
from components.world.scripts import world_content_catalog as catalog  # noqa: E402


TZ8 = timezone(timedelta(hours=8))
T0 = "2026-11-21T09:00:00+08:00"


def _at_hours(hours: float, base: str = T0) -> str:
    dt = datetime.fromisoformat(base) + timedelta(hours=hours)
    return dt.isoformat(timespec="seconds")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    snap: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        st = p.lstat()
        size = int(st.st_size) if (p.is_file() and not p.is_symlink()) else 0
        snap[rel] = (size, int(st.st_mtime_ns))
    return snap


def _write_json(path: Path, doc: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class TestV09WorldCommitmentsEngine(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-npc-commitments-")
        self.base = Path(self._tmp.name)
        self.root = self.base / "inst_main"
        self.root.mkdir(parents=True, exist_ok=True)
        _write_json(
            self.root / "run" / "nyairo-day.instance.json",
            {
                "schema": "nyairo.day.managed.v1",
                "instance_seed": "seed_main_2026",
                "world_package": {"world_id": "coastal_2026", "theme": "coastal_town", "seed": 2026},
            },
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # 1. Instance-Bound Persistent NPC Unification
    # ------------------------------------------------------------------

    def test_01_distinct_seeds_produce_distinct_npcs_and_reload_is_stable(self) -> None:
        inst_a = self.base / "inst_seed_a"
        inst_b = self.base / "inst_seed_b"
        _write_json(
            inst_a / "one-day" / "world-package" / "world.json",
            {"world_id": "coastal_alpha", "theme": "coastal_town", "seed": 1001},
        )
        _write_json(
            inst_b / "one-day" / "world-package" / "world.json",
            {"world_id": "coastal_beta", "theme": "coastal_town", "seed": 9997},
        )

        before_a = _snapshot_tree(inst_a)
        before_b = _snapshot_tree(inst_b)

        roster_a1 = engine.resolve_instance_npc_roster_zero_write(inst_a)
        roster_a2 = engine.resolve_instance_npc_roster_zero_write(inst_a)
        roster_b = engine.resolve_instance_npc_roster_zero_write(inst_b)

        # Zero-write guarantee
        self.assertEqual(before_a, _snapshot_tree(inst_a))
        self.assertEqual(before_b, _snapshot_tree(inst_b))

        # 4 canonical roles, identical across reloads for same instance
        self.assertEqual(len(roster_a1), 4)
        self.assertEqual(roster_a1, roster_a2)
        self.assertEqual(
            [row["role_slot"] for row in roster_a1],
            list(engine.CANONICAL_ROLE_SLOTS),
        )

        # Distinct seeds yield genuinely distinct NPC IDs and names
        ids_a = {row["npc_id"] for row in roster_a1}
        ids_b = {row["npc_id"] for row in roster_b}
        self.assertTrue(ids_a.isdisjoint(ids_b))
        names_a = [row["display_name"] for row in roster_a1]
        names_b = [row["display_name"] for row in roster_b]
        self.assertNotEqual(names_a, names_b)
        self.assertEqual(len(set(names_a)), 4)
        self.assertEqual(len(set(names_b)), 4)

        # Legacy alias unification (v05 auntie_lin & v03 npc_coastal_bookstore)
        tea_npc = engine.resolve_npc_by_id_or_alias_zero_write(inst_a, "auntie_lin")
        self.assertIsNotNone(tea_npc)
        assert tea_npc is not None
        self.assertEqual(tea_npc["role_slot"], "tea_bakery_owner")

        book_npc = engine.resolve_npc_by_id_or_alias_zero_write(inst_a, "npc_coastal_bookstore")
        self.assertIsNotNone(book_npc)
        assert book_npc is not None
        self.assertEqual(book_npc["role_slot"], "bookstore_keeper")

        # Subpath resolution via instance_paths (e.g. passing one-day/world-home)
        sub_home = inst_a / "one-day" / "world-home"
        sub_home.mkdir(parents=True, exist_ok=True)
        _write_json(inst_a / "instance.json", {"world_home": "one-day/world-home"})
        self.assertEqual(
            engine.resolve_instance_npc_roster_zero_write(sub_home),
            roster_a1,
        )

    def test_02_worldgen_npcs_json_adoption_primary_and_fallback(self) -> None:
        inst_wg = self.base / "inst_worldgen"
        # Use <day_root>/world-package fallback path (hyphenated)
        _write_json(
            inst_wg / "world-package" / "world.json",
            {"world_id": "harbor_wg_42", "theme": "coastal_town", "seed": 42},
        )
        _write_json(
            inst_wg / "world-package" / "npcs.json",
            {
                "npcs": [
                    {
                        "npc_id": "coastal_npc_bookstore_01",
                        "display_name": "沈知秋",
                        "public_role": "港口旧书铺店主",
                        "home_base_area": "bookstore",
                        "public_description": "每天上午整理旧画册与海图。",
                        "windows": [
                            {
                                "weekdays": [1, 2, 3, 4, 5, 6],
                                "start_minute": 9 * 60,
                                "end_minute": 18 * 60,
                                "location_id": "bookstore",
                            }
                        ],
                    }
                ]
            },
        )
        before = _snapshot_tree(inst_wg)
        roster = engine.resolve_instance_npc_roster_zero_write(inst_wg)
        self.assertEqual(before, _snapshot_tree(inst_wg))

        by_role = {row["role_slot"]: row for row in roster}
        bk = by_role["bookstore_keeper"]
        self.assertEqual(bk["source_kind"], "worldgen_adopted")
        self.assertEqual(bk["npc_id"], "coastal_npc_bookstore_01")
        self.assertEqual(bk["display_name"], "沈知秋")
        self.assertIn("uncle_zhou", bk["legacy_alias_ids"])
        self.assertEqual(bk["open_windows"][0]["start_hhmm"], "09:00")
        self.assertEqual(bk["open_windows"][0]["end_hhmm"], "18:00")
        # Unspecified canonical roles deterministically derived
        self.assertEqual(by_role["tea_bakery_owner"]["source_kind"], "instance_seeded")

    # ------------------------------------------------------------------
    # 2. 72-Hour Zero-Idle-Spin
    # ------------------------------------------------------------------

    def test_03_72_hour_zero_idle_spin_zero_writes_zero_llm(self) -> None:
        before_snap = _snapshot_tree(self.root)
        roster = engine.resolve_instance_npc_roster_zero_write(self.root)
        tea_npc = next(r for r in roster if r["role_slot"] == "tea_bakery_owner")

        open_hours_seen = 0
        closed_hours_seen = 0
        for h in range(72):
            ts = _at_hours(h)
            self.assertFalse(
                engine.check_heartbeat_needed_zero_write(
                    self.root,
                    now_iso=ts,
                    current_weather="clear",
                )
            )
            adv = engine.advance_due_commitments(
                self.root,
                now_iso=ts,
                current_weather="clear",
            )
            self.assertFalse(adv["disk_written"])
            self.assertEqual(adv["advanced_count"], 0)
            self.assertEqual(adv["llm_calls"], 0)

            presence = engine.derive_npc_presence_zero_write(tea_npc, ts, ())
            self.assertEqual(presence["llm_calls"], 0)
            if presence["is_open"]:
                open_hours_seen += 1
                self.assertEqual(presence["presence_state"], "AT_VENUE")
            else:
                closed_hours_seen += 1
                self.assertEqual(presence["presence_state"], "OFF_HOURS")

        # Across 72h, tea/bakery owner was both open and off-hours naturally
        self.assertGreater(open_hours_seen, 0)
        self.assertGreater(closed_hours_seen, 0)
        # Zero disk mutations, zero files, zero .lock created over 72h
        self.assertEqual(before_snap, _snapshot_tree(self.root))
        self.assertFalse(engine.commitments_state_path(self.root).exists())

    # ------------------------------------------------------------------
    # 3. Full BOOK_OR_ITEM_RESERVATION Lifecycle (Pickup, Expiry->Shelf, Delay)
    # ------------------------------------------------------------------

    def test_04_reservation_ordered_to_arrived_to_picked_up(self) -> None:
        arr_at = _at_hours(24)
        created = engine.create_commitment(
            self.root,
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "npc_id": "uncle_zhou",
                "venue_id": "old_bookshop",
                "title_cn": "海岸线手绘风物集",
                "arrival_due_at": arr_at,
                "hold_hours": 72,
            },
            actor_side="subject",
            operation_key="op_rsv_pickup_01",
            now_iso=T0,
        )
        self.assertTrue(created["ok"])
        self.assertEqual(created["llm_calls"], 0)
        cid = created["commitment_id"]
        self.assertEqual(created["commitment"]["state"], "ORDERED")
        self.assertEqual(created["min_next_due_at"], arr_at)

        # Before arrival: O(1) idle skip -> False, zero disk writes
        snap_before_idle = _snapshot_tree(self.root)
        self.assertFalse(
            engine.check_heartbeat_needed_zero_write(
                self.root,
                now_iso=_at_hours(12),
                current_weather="clear",
            )
        )
        adv_early = engine.advance_due_commitments(
            self.root,
            now_iso=_at_hours(12),
            current_weather="clear",
        )
        self.assertFalse(adv_early["disk_written"])
        self.assertEqual(adv_early["llm_calls"], 0)
        self.assertEqual(snap_before_idle, _snapshot_tree(self.root))

        # At arrival_due_at: wakes and transitions ORDERED -> ARRIVED_HOLDING
        self.assertTrue(
            engine.check_heartbeat_needed_zero_write(self.root, now_iso=arr_at)
        )
        adv_arr = engine.advance_due_commitments(self.root, now_iso=arr_at)
        self.assertTrue(adv_arr["disk_written"])
        self.assertEqual(adv_arr["llm_calls"], 0)
        self.assertEqual(adv_arr["advanced_count"], 1)
        self.assertEqual(adv_arr["transitions"][0]["to_state"], "ARRIVED_HOLDING")
        expected_hold_until = _at_hours(24 + 72)
        self.assertEqual(adv_arr["min_next_due_at"], expected_hold_until)

        # DIRECT_CONTACT pickup before expiry -> PICKED_UP
        pickup_ts = _at_hours(30)
        picked = engine.contact_npc_or_venue(
            self.root,
            venue_id="bookstore",
            contact_action="pickup_reservation",
            commitment_id=cid,
            actor_side="subject",
            operation_key="op_pickup_01",
            now_iso=pickup_ts,
        )
        self.assertTrue(picked["ok"])
        self.assertEqual(picked["llm_calls"], 0)
        self.assertEqual(picked["commitment"]["state"], "PICKED_UP")
        self.assertIsNone(picked["min_next_due_at"])
        self.assertEqual(picked["subject_observed_at"], pickup_ts)

        # Idempotent replay with same operation_key even at a slightly later timestamp
        picked_replay = engine.contact_npc_or_venue(
            self.root,
            venue_id="bookstore",
            contact_action="pickup_reservation",
            commitment_id=cid,
            actor_side="subject",
            operation_key="op_pickup_01",
            now_iso=_at_hours(30.5),
        )
        self.assertTrue(picked_replay["already_applied"])
        self.assertEqual(picked_replay["status"], "already_applied")
        self.assertEqual(picked_replay["commitment"]["state"], "PICKED_UP")

    def test_05_reservation_hold_expired_releases_to_catalog_shelf(self) -> None:
        arr_at = _at_hours(10)
        item_spec = {
            "template_id": "reading_booklet",
            "store_id": "bookstore",
            "name_cn": "旧港灯塔修缮笔记",
            "price": 32,
            "stock_initial": 1,
            "pages_count": 12,
            "topic_tags": ["harbor", "craft"],
            "argument_thesis_cn": "旧石砌灯塔靠慢干石灰砂浆耐过海风盐雾。",
            "sample_excerpt_cn": "修补裂缝前需先听潮水退尽后的墙体回声。",
        }
        created = engine.create_commitment(
            self.root,
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "旧港灯塔修缮笔记",
                "arrival_due_at": arr_at,
                "hold_hours": 72,
                "catalog_item_spec": item_spec,
            },
            actor_side="subject",
            operation_key="op_rsv_expire_01",
            now_iso=T0,
        )
        cid = created["commitment_id"]

        # Advance to arrival -> ARRIVED_HOLDING
        adv1 = engine.advance_due_commitments(self.root, now_iso=arr_at)
        self.assertEqual(adv1["transitions"][0]["to_state"], "ARRIVED_HOLDING")
        self.assertEqual(adv1["llm_calls"], 0)

        # Advance +72h -> HOLD_EXPIRED, posts honest notice & releases to dynamic catalog
        exp_ts = _at_hours(10 + 72)
        adv2 = engine.advance_due_commitments(self.root, now_iso=exp_ts)
        self.assertEqual(adv2["llm_calls"], 0)
        self.assertEqual(adv2["transitions"][0]["to_state"], "HOLD_EXPIRED")
        self.assertTrue(adv2["transitions"][0]["released_to_shelf"])
        self.assertTrue(
            any(engine.HOLD_EXPIRED_NOTICE_CN in n["text_cn"] for n in adv2["notices_posted"])
        )
        self.assertIsNone(adv2["min_next_due_at"])

        # Verify dynamic_content_catalog actually received the released item!
        cat_view = catalog.read_dynamic_catalog_zero_write(self.root, store_id="bookstore")
        names = [row["name_cn"] for row in cat_view["items"]]
        self.assertIn("旧港灯塔修缮笔记", names)

        # Subsequent pickup attempt fails honestly with 409 HOLD_EXPIRED
        with self.assertRaises(engine.WorldCommitmentsError) as ctx:
            engine.contact_npc_or_venue(
                self.root,
                venue_id="bookstore",
                contact_action="pickup_reservation",
                commitment_id=cid,
                actor_side="subject",
                now_iso=_at_hours(10 + 75),
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.reason_code, "HOLD_EXPIRED")

    def test_06_supply_delayed_exception_callback_and_budget_refusal(self) -> None:
        arr_at = _at_hours(12)
        c1 = engine.create_commitment(
            self.root,
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "绝版植物标本图鉴",
                "arrival_due_at": arr_at,
                "supply_delayed": True,
                "delay_reason_cn": "外埠书商调货遇台风延误",
            },
            actor_side="subject",
            operation_key="op_delay_01",
            now_iso=T0,
        )
        cid = c1["commitment_id"]

        # Use a real LifeRequestBudget with daily_token_budget=500 (< 600 est_tokens) -> refused!
        budget_dir = self.root / "life" / "budget_state"
        budget_dir.mkdir(parents=True, exist_ok=True)
        budget = _lrb.LifeRequestBudget(
            budget_dir,
            utc_day_max=5,
            daily_token_budget=500,
        )
        # Daily token cap 500 < DEFAULT_DECISION_EST_TOKENS (600) -> pre-flight refused
        callback_calls: list[dict[str, Any]] = []

        def _mock_decision(ctx: dict[str, Any]) -> dict[str, Any]:
            callback_calls.append(ctx)
            return {"delay_hours": 48, "notice_cn": "书商通知需延后两天到货"}

        adv_refused = engine.advance_due_commitments(
            self.root,
            now_iso=arr_at,
            decision_callback=_mock_decision,
            budget=budget,
        )
        self.assertEqual(len(callback_calls), 0)
        self.assertEqual(adv_refused["llm_calls"], 0)
        self.assertEqual(adv_refused["transitions"][0]["to_state"], "DELAYED_RESCHEDULED")
        self.assertTrue(adv_refused["transitions"][0]["budget_refused"])

        # Now test an allowed decision_callback on a second delayed reservation
        arr_at_2 = _at_hours(20)
        engine.create_commitment(
            self.root,
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "cafe",
                "title_cn": "秋季头采焙茶罐",
                "arrival_due_at": arr_at_2,
                "supply_delayed": True,
                "delay_reason_cn": "茶厂烘焙排期延后",
            },
            actor_side="partner",
            operation_key="op_delay_02",
            now_iso=T0,
        )
        adv_allowed = engine.advance_due_commitments(
            self.root,
            now_iso=arr_at_2,
            decision_callback=_mock_decision,
            budget=None,
        )
        self.assertEqual(len(callback_calls), 1)
        self.assertEqual(adv_allowed["llm_calls"], 1)
        self.assertEqual(adv_allowed["transitions"][0]["to_state"], "DELAYED_RESCHEDULED")
        self.assertNotIn("private_desire", callback_calls[0])
        self.assertNotIn("subject_mood", callback_calls[0])
        # First commitment still tracked cleanly
        view = engine.read_world_commitments_zero_write(self.root, actor_side="partner", now_iso=arr_at_2)
        c_map = {row["commitment_id"]: row for row in view["commitments"]}
        self.assertEqual(c_map[cid]["state"], "DELAYED_RESCHEDULED")

    # ------------------------------------------------------------------
    # 4. INDEPENDENT_NPC_PLAN (TEMPORARY_CLOSE & NEW_TOPIC_READY) + CONDITION_CHANGED
    # ------------------------------------------------------------------

    def test_07_independent_npc_plan_temporary_close_and_epistemic_boundary(self) -> None:
        start_at = _at_hours(2)
        end_at = _at_hours(10)
        created = engine.create_commitment(
            self.root,
            {
                "kind": "INDEPENDENT_NPC_PLAN",
                "effect_kind": "TEMPORARY_CLOSE",
                "venue_id": "bookstore",
                "title_cn": "周末去隔壁港口参加旧书市集",
                "start_at": start_at,
                "end_at": end_at,
                "door_notice_cn": "周末外出参加港口旧书市集，本店休业至晚间七点。",
            },
            actor_side="subject",
            operation_key="op_plan_bookfair",
            now_iso=T0,
        )
        self.assertEqual(created["commitment"]["state"], "PLANNED")

        # Advance into window WITHOUT TA visiting -> shop closes & door notice posted!
        mid_ts = _at_hours(4)
        adv = engine.advance_due_commitments(self.root, now_iso=mid_ts)
        self.assertEqual(adv["llm_calls"], 0)
        self.assertEqual(adv["transitions"][0]["to_state"], "IN_PROGRESS")
        self.assertTrue(
            any("港口旧书市集" in n["text_cn"] for n in adv["notices_posted"])
        )

        # Epistemic check BEFORE TA visits:
        # 1) Partner view sees the public door notice with observed_by_subject == False
        partner_view = engine.read_world_commitments_zero_write(
            self.root,
            actor_side="partner",
            now_iso=mid_ts,
        )
        self.assertEqual(len(partner_view["venue_notices"]), 1)
        self.assertFalse(partner_view["venue_notices"][0]["observed_by_subject"])
        partner_bk = next(n for n in partner_view["npcs"] if n["role_slot"] == "bookstore_keeper")
        self.assertFalse(partner_bk["presence"]["is_open"])
        self.assertEqual(partner_bk["presence"]["presence_state"], "TEMPORARILY_CLOSED")

        # 2) Subject view does NOT omnisciently know unobserved door notice before visiting!
        snap_pre_read = _snapshot_tree(self.root)
        subject_before = engine.read_world_commitments_zero_write(
            self.root,
            actor_side="subject",
            now_iso=mid_ts,
        )
        self.assertEqual(snap_pre_read, _snapshot_tree(self.root))
        self.assertEqual(subject_before["venue_notices"], [])
        self.assertEqual(subject_before["unobserved_notice_count"], 1)
        subj_bk_before = next(n for n in subject_before["npcs"] if n["role_slot"] == "bookstore_keeper")
        self.assertNotIn("港口旧书市集", subj_bk_before["presence"]["away_reason_cn"])

        # 3) TA walks to bookstore (DIRECT_CONTACT) -> discovers it closed & sees door notice!
        visit = engine.contact_npc_or_venue(
            self.root,
            venue_id="bookstore",
            contact_action="observe_or_greet",
            actor_side="subject",
            now_iso=mid_ts,
        )
        self.assertTrue(visit["ok"])
        self.assertFalse(visit["venue_open"])
        self.assertEqual(visit["presence"]["presence_state"], "TEMPORARILY_CLOSED")
        self.assertEqual(len(visit["observed_notices"]), 1)
        self.assertTrue(visit["observed_notices"][0]["observed_by_subject"])

        # 4) Now subject view legitimately knows the door notice!
        subject_after = engine.read_world_commitments_zero_write(
            self.root,
            actor_side="subject",
            now_iso=mid_ts,
        )
        self.assertEqual(len(subject_after["venue_notices"]), 1)
        self.assertTrue(subject_after["venue_notices"][0]["observed_by_subject"])
        self.assertEqual(subject_after["unobserved_notice_count"], 0)

        # 5) Even if TA misses the entire book fair on another instance, fast-forward leaves honest notice
        inst_miss = self.base / "inst_miss_fair"
        engine.create_commitment(
            inst_miss,
            {
                "kind": "INDEPENDENT_NPC_PLAN",
                "effect_kind": "TEMPORARY_CLOSE",
                "venue_id": "bookstore",
                "title_cn": "周末书展闭店",
                "start_at": start_at,
                "end_at": end_at,
                "door_notice_cn": "周末外出参加书展，闭店一天。",
            },
            now_iso=T0,
        )
        adv_after_end = engine.advance_due_commitments(inst_miss, now_iso=_at_hours(12))
        states = [t["to_state"] for t in adv_after_end["transitions"]]
        self.assertEqual(states, ["IN_PROGRESS", "COMPLETED"])
        self.assertTrue(any("书展" in n["text_cn"] for n in adv_after_end["notices_posted"]))

    def test_08_condition_changed_rain_cancels_outdoor_stall_and_reschedules_appt(self) -> None:
        stall_res = engine.create_commitment(
            self.root,
            {
                "kind": "INDEPENDENT_NPC_PLAN",
                "effect_kind": "OUTDOOR_STALL",
                "venue_id": "porch_market",
                "title_cn": "街角屋檐手作小摊",
                "start_at": _at_hours(5),
                "end_at": _at_hours(9),
                "current_weather": "clear",
            },
            actor_side="subject",
            operation_key="op_stall_01",
            now_iso=T0,
        )
        stall_id = stall_res["commitment_id"]

        appt_res = engine.create_commitment(
            self.root,
            {
                "kind": "SCHEDULED_APPOINTMENT",
                "venue_id": "porch_market",
                "title_cn": "露天茶摊试饮碰面",
                "window_start_at": _at_hours(6),
                "window_end_at": _at_hours(7),
                "weather_sensitive": True,
                "indoor_fallback_venue_id": "cafe",
            },
            actor_side="partner",
            operation_key="op_appt_weather_01",
            now_iso=T0,
        )
        appt_id = appt_res["commitment_id"]

        # While clear and before due time: heartbeat returns False
        self.assertFalse(
            engine.check_heartbeat_needed_zero_write(
                self.root,
                now_iso=_at_hours(1),
                current_weather="clear",
            )
        )
        # Live weather turns to rain -> heartbeat immediately returns True!
        self.assertTrue(
            engine.check_heartbeat_needed_zero_write(
                self.root,
                now_iso=_at_hours(1),
                current_weather="rain",
            )
        )

        # Trigger 2: CONDITION_CHANGED ("weather" -> "rain")
        changed = engine.notify_world_condition_changed(
            self.root,
            condition_kind="weather",
            new_value="rain",
            now_iso=_at_hours(1),
        )
        self.assertTrue(changed["ok"])
        self.assertTrue(changed["disk_written"])
        self.assertEqual(changed["llm_calls"], 0)
        self.assertEqual(changed["affected_count"], 2)

        by_id = {row["commitment_id"]: row for row in changed["affected_commitments"]}
        self.assertEqual(by_id[stall_id]["to_state"], "CANCELLED_BY_WEATHER")
        self.assertEqual(by_id[appt_id]["to_state"], "RESCHEDULED_INDOOR")
        self.assertEqual(by_id[appt_id]["venue_id"], "cafe")
        self.assertGreaterEqual(len(changed["notices_posted"]), 2)
        self.assertTrue(
            any(n["venue_id"] == "porch_market" and n["notice_kind"] == "WEATHER_CANCELLED" for n in changed["notices_posted"])
        )

        # Now fulfill the rescheduled indoor appointment at cafe during its window!
        fulfill = engine.contact_npc_or_venue(
            self.root,
            venue_id="cafe",
            contact_action="fulfill_appointment",
            commitment_id=appt_id,
            actor_side="subject",
            now_iso=_at_hours(6.5),
        )
        self.assertTrue(fulfill["ok"])
        self.assertEqual(fulfill["commitment"]["state"], "FULFILLED")

    def test_09_new_topic_ready_and_appointment_missed(self) -> None:
        engine.create_commitment(
            self.root,
            {
                "kind": "INDEPENDENT_NPC_PLAN",
                "effect_kind": "NEW_TOPIC_READY",
                "venue_id": "cafe",
                "title_cn": "读完旧茶谱并整理烘焙笔记",
                "topic_summary_cn": "浅火茎茶在阴天冲泡时水温降两度更清甜",
                "start_at": _at_hours(1),
                "end_at": _at_hours(3),
            },
            now_iso=T0,
        )
        appt = engine.create_commitment(
            self.root,
            {
                "kind": "SCHEDULED_APPOINTMENT",
                "venue_id": "home_goods_store",
                "title_cn": "邮亭核对旧明信片",
                "window_start_at": _at_hours(2),
                "window_end_at": _at_hours(3),
            },
            now_iso=T0,
        )
        adv = engine.advance_due_commitments(self.root, now_iso=_at_hours(4))
        self.assertEqual(adv["llm_calls"], 0)
        view = engine.read_world_commitments_zero_write(self.root, actor_side="partner", now_iso=_at_hours(4))
        c_by_id = {c["commitment_id"]: c for c in view["commitments"]}
        self.assertEqual(c_by_id[appt["commitment_id"]]["state"], "MISSED_BY_SUBJECT")
        tea_npc = next(n for n in view["npcs"] if n["role_slot"] == "tea_bakery_owner")
        self.assertIn("浅火茎茶在阴天冲泡时水温降两度更清甜", tea_npc["presence"]["ready_topics"])

    # ------------------------------------------------------------------
    # 5. Anti-Retroactive-Fabrication Firewall (400)
    # ------------------------------------------------------------------

    def test_10_retroactive_fabrication_forbidden_400_zero_write(self) -> None:
        before = _snapshot_tree(self.root)
        forbidden_payloads = [
            {"subject_mood": "sad"},
            {"private_desire": "wants rare book"},
            {"recent_chat_topic": "favorite cake"},
            {"fabricate_backstory": True},
            {"retroactive_event": "pretend shop stocked gift"},
            {"nested": {"deep": {"private_desire": "smuggled"}}},
        ]
        for extra in forbidden_payloads:
            with self.assertRaises(engine.WorldCommitmentsError) as ctx:
                engine.contact_npc_or_venue(
                    self.root,
                    venue_id="bookstore",
                    contact_action="observe_or_greet",
                    actor_side="subject",
                    now_iso=T0,
                    extra_params=extra,
                )
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.reason_code, "RETROACTIVE_FABRICATION_FORBIDDEN")
        self.assertEqual(before, _snapshot_tree(self.root))

    # ------------------------------------------------------------------
    # 6. 10-Thread Concurrency, Idempotency, Symlink & 0600 LF Integrity
    # ------------------------------------------------------------------

    def test_11_ten_thread_concurrent_create_and_idempotency(self) -> None:
        barrier = threading.Barrier(10)
        results: list[dict[str, Any]] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def _worker(idx: int) -> None:
            try:
                barrier.wait(timeout=10)
                res = engine.create_commitment(
                    self.root,
                    {
                        "kind": "BOOK_OR_ITEM_RESERVATION",
                        "venue_id": "bookstore",
                        "title_cn": f"并发预订册子_{idx:02d}",
                        "arrival_due_at": _at_hours(12 + idx),
                    },
                    actor_side="subject",
                    operation_key=f"op_conc_distinct_{idx:02d}",
                    now_iso=T0,
                )
                with lock:
                    results.append(res)
            except BaseException as exc:  # pragma: no cover
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 10)
        cids = {r["commitment_id"] for r in results}
        self.assertEqual(len(cids), 10)

        view = engine.read_world_commitments_zero_write(self.root, actor_side="partner", now_iso=T0)
        self.assertEqual(len(view["commitments"]), 10)
        self.assertEqual(view["revision"], 10)

        # Now 10 threads with the SAME operation_key -> exactly 1 new commitment!
        dup_barrier = threading.Barrier(10)
        dup_results: list[dict[str, Any]] = []
        dup_errors: list[BaseException] = []

        def _dup_worker() -> None:
            try:
                dup_barrier.wait(timeout=10)
                res = engine.create_commitment(
                    self.root,
                    {
                        "kind": "BOOK_OR_ITEM_RESERVATION",
                        "venue_id": "cafe",
                        "title_cn": "同一幂等键预订茶盒",
                        "arrival_due_at": _at_hours(30),
                    },
                    actor_side="partner",
                    operation_key="op_conc_same_key",
                    now_iso=T0,
                )
                with lock:
                    dup_results.append(res)
            except BaseException as exc:  # pragma: no cover
                with lock:
                    dup_errors.append(exc)

        dup_threads = [threading.Thread(target=_dup_worker) for _ in range(10)]
        for t in dup_threads:
            t.start()
        for t in dup_threads:
            t.join(timeout=15)

        self.assertEqual(dup_errors, [])
        self.assertEqual(len(dup_results), 10)
        self.assertEqual(len({r["commitment_id"] for r in dup_results}), 1)
        applied_count = sum(1 for r in dup_results if not r.get("already_applied"))
        replay_count = sum(1 for r in dup_results if r.get("already_applied"))
        self.assertEqual(applied_count, 1)
        self.assertEqual(replay_count, 9)

        # Conflicting payload on same operation_key -> 409 OPERATION_KEY_CONFLICT
        with self.assertRaises(engine.WorldCommitmentsError) as ctx:
            engine.create_commitment(
                self.root,
                {
                    "kind": "BOOK_OR_ITEM_RESERVATION",
                    "venue_id": "cafe",
                    "title_cn": "篡改后的不同内容",
                    "arrival_due_at": _at_hours(30),
                },
                actor_side="partner",
                operation_key="op_conc_same_key",
                now_iso=T0,
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

        # Verify pure LF, UTF-8 no BOM, and 0600 permissions on POSIX
        state_file = engine.commitments_state_path(self.root)
        raw = state_file.read_bytes()
        self.assertEqual(raw.count(b"\r"), 0)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        if os.name == "posix":
            mode = stat.S_IMODE(state_file.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_12_symlink_rejected_on_write(self) -> None:
        if os.name != "posix":  # pragma: no cover - symlink test runs on WSL/POSIX
            self.skipTest("POSIX symlink test")
        sym_root = self.base / "sym_inst"
        os.symlink(str(self.root), str(sym_root))
        with self.assertRaises(engine.WorldCommitmentsError) as ctx:
            engine.create_commitment(
                sym_root,
                {
                    "kind": "BOOK_OR_ITEM_RESERVATION",
                    "venue_id": "bookstore",
                    "title_cn": "符号链接测试",
                    "arrival_due_at": _at_hours(5),
                },
                now_iso=T0,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.reason_code, "UNSAFE_SYMLINK")

    # ------------------------------------------------------------------
    # 7. Unified Forget Barrier Filtering, Fail-Closed & Zero-Write Reads
    # ------------------------------------------------------------------

    def test_13_forget_barrier_filters_commitment_notice_npc_and_fail_closed(self) -> None:
        c_keep = engine.create_commitment(
            self.root,
            {
                "kind": "INDEPENDENT_NPC_PLAN",
                "effect_kind": "TEMPORARY_CLOSE",
                "venue_id": "bookstore",
                "title_cn": "保留的书店休业计划",
                "start_at": _at_hours(1),
                "end_at": _at_hours(5),
                "door_notice_cn": "旧书铺下午外出进书。",
            },
            now_iso=T0,
        )
        c_forget = engine.create_commitment(
            self.root,
            {
                "kind": "INDEPENDENT_NPC_PLAN",
                "effect_kind": "TEMPORARY_CLOSE",
                "venue_id": "cafe",
                "title_cn": "需要遗忘的茶铺计划",
                "start_at": _at_hours(1),
                "end_at": _at_hours(5),
                "door_notice_cn": "茶铺临时外出采购。",
            },
            now_iso=T0,
        )
        engine.advance_due_commitments(self.root, now_iso=_at_hours(2))

        pre_view = engine.read_world_commitments_zero_write(self.root, actor_side="partner", now_iso=_at_hours(2))
        self.assertEqual(len(pre_view["commitments"]), 2)
        self.assertEqual(len(pre_view["venue_notices"]), 2)
        craft_npc_id = next(n["npc_id"] for n in pre_view["npcs"] if n["role_slot"] == "craft_regular")
        forget_cid = c_forget["commitment_id"]
        keep_notice_id = next(
            n["notice_id"]
            for n in pre_view["venue_notices"]
            if n["commitment_id"] == c_keep["commitment_id"]
        )

        # Submit explicit forget command blocking forget_cid, keep_notice_id, and craft_npc_id
        f_code, f_res = _rlf.submit_forget_command(
            self.root,
            command_text="删掉记录和相关记忆",
            operation_id="op_forget_v09_commitments_01",
            explicit_scope=[forget_cid, keep_notice_id, craft_npc_id],
            explicit_confirmation=True,
            now_iso=_at_hours(2.5),
        )
        self.assertEqual(f_code, 200, f_res)

        snap_before_read = _snapshot_tree(self.root)
        post_view = engine.read_world_commitments_zero_write(self.root, actor_side="partner", now_iso=_at_hours(2.5))
        self.assertEqual(snap_before_read, _snapshot_tree(self.root))
        self.assertTrue(post_view["zero_write"])
        self.assertEqual(post_view["llm_calls"], 0)
        self.assertTrue(post_view["forget_barrier_active"])
        self.assertFalse(post_view["forget_barrier_fail_closed"])

        # Blocked commitment, both notices (one via commitment_id, one via direct notice_id), and blocked NPC stripped
        remaining_cids = {c["commitment_id"] for c in post_view["commitments"]}
        self.assertIn(c_keep["commitment_id"], remaining_cids)
        self.assertNotIn(forget_cid, remaining_cids)

        remaining_notice_ids = {n["notice_id"] for n in post_view["venue_notices"]}
        self.assertNotIn(keep_notice_id, remaining_notice_ids)
        self.assertEqual(post_view["venue_notices"], [])

        remaining_npc_ids = {n["npc_id"] for n in post_view["npcs"]}
        self.assertNotIn(craft_npc_id, remaining_npc_ids)
        self.assertEqual(len(remaining_npc_ids), 3)

        # Corrupt control ledger -> fail-closed with 0 disk writes
        ctrl_path = self.root / "control_authority" / "v02_control_highwater_ledger.json"
        ctrl_path.write_text("{corrupt-json", encoding="utf-8")
        snap_corrupt = _snapshot_tree(self.root)
        closed_view = engine.read_world_commitments_zero_write(self.root, actor_side="partner", now_iso=_at_hours(3))
        self.assertEqual(snap_corrupt, _snapshot_tree(self.root))
        self.assertTrue(closed_view["forget_barrier_fail_closed"])
        self.assertEqual(closed_view["npcs"], [])
        self.assertEqual(closed_view["commitments"], [])
        self.assertEqual(closed_view["venue_notices"], [])

    # ------------------------------------------------------------------
    # 8. Pure-LF, Zero-Emoji & Neutral TA Hygiene Gate
    # ------------------------------------------------------------------

    def test_14_source_file_hygiene_lf_no_bom_zero_emoji_neutral_ta(self) -> None:
        engine_path = REPO_ROOT / "components" / "world" / "scripts" / "world_commitments_engine.py"
        raw = engine_path.read_bytes()
        self.assertEqual(raw.count(b"\r"), 0, "Engine source must be pure LF")
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "Engine source must have no UTF-8 BOM")
        text = raw.decode("utf-8")
        self.assertEqual(text.count("她"), 0)
        self.assertEqual(text.count("他"), 0)
        self.assertEqual(text.count("千代"), 0)
        emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
        self.assertEqual(emojis, [], f"Found forbidden unicode.So characters: {emojis!r}")


    def test_15_pickup_during_temporary_close_refuses_store_closed_and_records_observation(self) -> None:
        rsv = engine.create_commitment(
            self.root,
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "旧港素描册",
                "arrival_due_at": _at_hours(1),
            },
            actor_side="subject",
            now_iso=T0,
        )
        engine.create_commitment(
            self.root,
            {
                "kind": "INDEPENDENT_NPC_PLAN",
                "effect_kind": "TEMPORARY_CLOSE",
                "venue_id": "bookstore",
                "title_cn": "午后外出收书",
                "start_at": _at_hours(2),
                "end_at": _at_hours(6),
                "door_notice_cn": "午后外出收书，傍晚恢复营业。",
            },
            now_iso=T0,
        )
        engine.advance_due_commitments(self.root, now_iso=_at_hours(3))

        # Before visiting, subject does not know the closed door notice
        pre = engine.read_world_commitments_zero_write(self.root, actor_side="subject", now_iso=_at_hours(3))
        self.assertEqual(pre["venue_notices"], [])

        # Walking to pickup during temporary close refuses 409 STORE_CLOSED and observes the door notice
        with self.assertRaises(engine.WorldCommitmentsError) as ctx:
            engine.contact_npc_or_venue(
                self.root,
                venue_id="bookstore",
                contact_action="pickup_reservation",
                commitment_id=rsv["commitment_id"],
                actor_side="subject",
                now_iso=_at_hours(3),
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.reason_code, "STORE_CLOSED")

        post = engine.read_world_commitments_zero_write(self.root, actor_side="subject", now_iso=_at_hours(3))
        self.assertTrue(any("午后外出收书" in n["text_cn"] and n["observed_by_subject"] for n in post["venue_notices"]))

    def test_16_create_commitment_blocks_retroactive_keys_and_backdated_timestamps(self) -> None:
        snap_before = _snapshot_tree(self.root)

        # 1. Recursive & case-insensitive FORBIDDEN_RETROACTIVE_KEYS blocked at engine level (0 writes)
        smuggled_payloads: tuple[dict[str, Any], ...] = (
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "正常书名",
                "arrival_due_at": _at_hours(2),
                "Subject_Mood": "lonely",
            },
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "正常书名二",
                "arrival_due_at": _at_hours(2),
                "catalog_item_spec": {
                    "template_id": "reading_booklet",
                    "name_cn": "海边植物小册",
                    "nested": [{"Retroactive_Event": "smuggled"}],
                },
            },
            {
                "kind": "SCHEDULED_APPOINTMENT",
                "venue_id": "cafe",
                "title_cn": "碰面喝茶",
                "window_start_at": _at_hours(3),
                "Fabricate_Backstory": True,
            },
        )
        for bad_payload in smuggled_payloads:
            with self.assertRaises(engine.WorldCommitmentsError) as ctx_key:
                engine.create_commitment(self.root, bad_payload, actor_side="subject", now_iso=T0)
            self.assertEqual(ctx_key.exception.status_code, 400)
            self.assertEqual(ctx_key.exception.reason_code, "RETROACTIVE_FABRICATION_FORBIDDEN")

        # 2. Arbitrarily backdated arrival_due_at / window_start_at / start_at rejected (0 writes)
        with self.assertRaises(engine.WorldCommitmentsError) as ctx_rsv:
            engine.create_commitment(
                self.root,
                {
                    "kind": "BOOK_OR_ITEM_RESERVATION",
                    "venue_id": "bookstore",
                    "title_cn": "倒签十二天前的预订",
                    "arrival_due_at": _at_hours(-288),
                },
                actor_side="partner",
                now_iso=T0,
            )
        self.assertEqual(ctx_rsv.exception.status_code, 400)
        self.assertEqual(ctx_rsv.exception.reason_code, "RETROACTIVE_FABRICATION_FORBIDDEN")

        with self.assertRaises(engine.WorldCommitmentsError) as ctx_apt:
            engine.create_commitment(
                self.root,
                {
                    "kind": "SCHEDULED_APPOINTMENT",
                    "venue_id": "cafe",
                    "title_cn": "倒签两小时前的约定",
                    "window_start_at": _at_hours(-2),
                    "window_end_at": _at_hours(-1),
                },
                actor_side="subject",
                now_iso=T0,
            )
        self.assertEqual(ctx_apt.exception.status_code, 400)
        self.assertEqual(ctx_apt.exception.reason_code, "RETROACTIVE_FABRICATION_FORBIDDEN")

        with self.assertRaises(engine.WorldCommitmentsError) as ctx_plan:
            engine.create_commitment(
                self.root,
                {
                    "kind": "INDEPENDENT_NPC_PLAN",
                    "effect_kind": "TEMPORARY_CLOSE",
                    "venue_id": "bookstore",
                    "title_cn": "倒签过去的休业计划",
                    "start_at": _at_hours(-3),
                    "end_at": _at_hours(1),
                },
                actor_side="subject",
                now_iso=T0,
            )
        self.assertEqual(ctx_plan.exception.status_code, 400)
        self.assertEqual(ctx_plan.exception.reason_code, "RETROACTIVE_FABRICATION_FORBIDDEN")

        # Zero disk writes on every rejected call above
        self.assertEqual(_snapshot_tree(self.root), snap_before)

        # 3. Same-instant (arrival_due_at == T0) & future commitments still succeed cleanly
        ok_now = engine.create_commitment(
            self.root,
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "当刻即达预订",
                "arrival_due_at": T0,
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertTrue(ok_now["ok"])
        self.assertEqual(ok_now["llm_calls"], 0)

    def test_17_contact_enforces_forget_barrier_venue_match_and_npc_isolation(self) -> None:
        # Wrong-npc commitment at same venue must NOT match a different NPC
        self.assertFalse(
            engine._commitment_matches_npc(
                {"npc_id": "npc_other_person", "venue_id": "bookstore"},
                {"npc_id": "npc_bookstore_keeper", "role_slot": "bookstore_keeper", "venue_id": "bookstore"},
            )
        )
        self.assertTrue(
            engine._commitment_matches_npc(
                {"npc_id": "", "venue_id": "bookstore"},
                {"npc_id": "npc_bookstore_keeper", "role_slot": "bookstore_keeper", "venue_id": "bookstore"},
            )
        )

        # Create reservation + notice at bookstore
        rsv = engine.create_commitment(
            self.root,
            {
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "海边诗集",
                "arrival_due_at": _at_hours(1),
            },
            actor_side="subject",
            now_iso=T0,
        )
        rsv_id = rsv["commitment_id"]
        appt = engine.create_commitment(
            self.root,
            {
                "kind": "SCHEDULED_APPOINTMENT",
                "venue_id": "cafe",
                "title_cn": "午后喝茶碰面",
                "window_start_at": _at_hours(2),
                "window_end_at": _at_hours(4),
            },
            actor_side="subject",
            now_iso=T0,
        )
        appt_id = appt["commitment_id"]
        plan_ntc = engine.create_commitment(
            self.root,
            {
                "kind": "INDEPENDENT_NPC_PLAN",
                "effect_kind": "TEMPORARY_CLOSE",
                "venue_id": "craft_studio",
                "title_cn": "材料采购外出",
                "start_at": _at_hours(1),
                "end_at": _at_hours(5),
                "door_notice_cn": "材料采购外出，晚间回来。",
            },
            now_iso=T0,
        )
        engine.advance_due_commitments(self.root, now_iso=_at_hours(2.5))

        # Explicit commitment_id at wrong venue_id must refuse 404 COMMITMENT_NOT_FOUND
        with self.assertRaises(engine.WorldCommitmentsError) as wrong_pickup:
            engine.contact_npc_or_venue(
                self.root,
                venue_id="cafe",
                contact_action="pickup_reservation",
                commitment_id=rsv_id,
                actor_side="subject",
                now_iso=_at_hours(2.5),
            )
        self.assertEqual(wrong_pickup.exception.status_code, 404)
        self.assertEqual(wrong_pickup.exception.reason_code, "COMMITMENT_NOT_FOUND")

        with self.assertRaises(engine.WorldCommitmentsError) as wrong_appt:
            engine.contact_npc_or_venue(
                self.root,
                venue_id="bookstore",
                contact_action="fulfill_appointment",
                commitment_id=appt_id,
                actor_side="subject",
                now_iso=_at_hours(2.5),
            )
        self.assertEqual(wrong_appt.exception.status_code, 404)
        self.assertEqual(wrong_appt.exception.reason_code, "COMMITMENT_NOT_FOUND")

        # Block bookstore NPC, reservation, and craft_studio notice via forget barrier
        view_pre = engine.read_world_commitments_zero_write(self.root, actor_side="partner", now_iso=_at_hours(2.5))
        book_npc = next(n["npc_id"] for n in view_pre["npcs"] if n["venue_id"] == "bookstore")
        studio_ntc = next(n["notice_id"] for n in view_pre["venue_notices"] if n["commitment_id"] == plan_ntc["commitment_id"])
        f_code, _ = _rlf.submit_forget_command(
            self.root,
            command_text="忘记书店店主、预订与工坊告示",
            operation_id="op_forget_contact_01",
            explicit_scope=[book_npc, rsv_id, studio_ntc],
            explicit_confirmation=True,
            now_iso=_at_hours(2.6),
        )
        self.assertEqual(f_code, 200)

        res_book = engine.contact_npc_or_venue(
            self.root,
            venue_id="bookstore",
            contact_action="observe_or_greet",
            actor_side="subject",
            now_iso=_at_hours(2.6),
        )
        self.assertIsNone(res_book["npc"])

        res_studio = engine.contact_npc_or_venue(
            self.root,
            venue_id="craft_studio",
            contact_action="observe_or_greet",
            actor_side="subject",
            now_iso=_at_hours(2.6),
        )
        self.assertEqual(res_studio["observed_notices"], [])

        with self.assertRaises(engine.WorldCommitmentsError) as blocked_pick:
            engine.contact_npc_or_venue(
                self.root,
                venue_id="bookstore",
                contact_action="pickup_reservation",
                commitment_id=rsv_id,
                actor_side="subject",
                now_iso=_at_hours(2.6),
            )
        self.assertEqual(blocked_pick.exception.status_code, 404)

        # Corrupt control ledger -> 503 FORGET_BARRIER_FAIL_CLOSED
        ctrl_path = self.root / "control_authority" / "v02_control_highwater_ledger.json"
        ctrl_path.write_text("{corrupt-json", encoding="utf-8")
        with self.assertRaises(engine.WorldCommitmentsError) as fail_closed:
            engine.contact_npc_or_venue(
                self.root,
                venue_id="bookstore",
                contact_action="observe_or_greet",
                actor_side="subject",
                now_iso=_at_hours(2.7),
            )
        self.assertEqual(fail_closed.exception.status_code, 503)
        self.assertEqual(fail_closed.exception.reason_code, "FORGET_BARRIER_FAIL_CLOSED")


if __name__ == "__main__":
    unittest.main()

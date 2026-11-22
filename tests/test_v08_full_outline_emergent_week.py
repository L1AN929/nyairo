#!/usr/bin/env python3
"""Full-outline emergent week -- Parts A+B (Days 1-7, chains 1-6, one shared root).

This is the capstone acceptance file for the "one continuous everyday chain on a
single instance" rule.  All seven days run as one sequential story on ONE
``self.root`` with ``llm_calls == 0``; nothing is reset between chains.

Chain 1 (Day 1) -- weather -> yard drying -> piecewise clock cut ``clear -> rain``
-> porch shelter & a tolerant plant that never dies offline.
Chain 2 (Day 2) -- paper-cut integer conservation -> displayed public card while
the private draft stays isolated from the partner projection.
Chain 3 (Days 3-4) -- borrow a shared-shelf ruler -> honest partial pack into a
canvas bag -> carry it on a journey -> lean the bag on a chair -> a Day-4 cold
reload derives the ruler's zone through the carrier chain without rewriting the
child row -> return the borrowed item.

Part B (Days 5-7) extends the same class and reuses ``_run_days_1_to_4`` on
the same root: Chain 4 discovers a roadblock honestly and reroutes under the
eaves; Chain 5 borrows a real bookstore book, bookmarks page 42 with a tucked
leaf, and returns it after closing; Chain 6 keeps a visited-place booklet
honest, lets a source-lost concern subside with zero write, stays inside the
1800-char compact-context budget, and places a reed into a zero-code vase.

Run (native Windows Python)::

    python -B -m unittest tests.test_v08_full_outline_emergent_week -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from components.life.scripts import everyday_desire_lifecycle as edl  # noqa: E402
from components.life.scripts import v03_clock_evaporation as clock  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402
from components.phone import v04_neighborhood_view as neighborhood_view  # noqa: E402
from components.world.scripts import v04_neighborhood_living as neighborhood  # noqa: E402
from components.world.scripts import v08_universal_matter as matter  # noqa: E402

# Day 1 -- 2026-11-16
DAY1_0900 = "2026-11-16T09:00:00+08:00"
DAY1_1100 = "2026-11-16T11:00:00+08:00"
DAY1_1110 = "2026-11-16T11:10:00+08:00"
DAY1_1500 = "2026-11-16T15:00:00+08:00"
# Day 2 -- 2026-11-17
DAY2_0900 = "2026-11-17T09:00:00+08:00"
DAY2_1400 = "2026-11-17T14:00:00+08:00"
DAY2_1405 = "2026-11-17T14:05:00+08:00"
DAY2_1410 = "2026-11-17T14:10:00+08:00"
DAY2_1415 = "2026-11-17T14:15:00+08:00"
# Day 3 -- 2026-11-18
DAY3_0920 = "2026-11-18T09:20:00+08:00"
DAY3_0925 = "2026-11-18T09:25:00+08:00"
DAY3_0930 = "2026-11-18T09:30:00+08:00"
DAY3_0940 = "2026-11-18T09:40:00+08:00"
DAY3_0941 = "2026-11-18T09:41:00+08:00"
DAY3_1000 = "2026-11-18T10:00:00+08:00"
DAY3_1001 = "2026-11-18T10:01:00+08:00"
DAY3_1100 = "2026-11-18T11:00:00+08:00"
DAY3_1110 = "2026-11-18T11:10:00+08:00"
DAY3_1300 = "2026-11-18T13:00:00+08:00"
DAY3_1320 = "2026-11-18T13:20:00+08:00"
# Day 4 -- 2026-11-19
DAY4_0900 = "2026-11-19T09:00:00+08:00"
DAY4_0910 = "2026-11-19T09:10:00+08:00"
DAY4_0920 = "2026-11-19T09:20:00+08:00"
# Day 5 -- 2026-11-20 (Chain 4: roadblock -> sheltered reroute -> drizzle turn-back)
DAY5_0900 = "2026-11-20T09:00:00+08:00"
DAY5_0905 = "2026-11-20T09:05:00+08:00"
DAY5_0930 = "2026-11-20T09:30:00+08:00"
DAY5_0932 = "2026-11-20T09:32:00+08:00"
DAY5_0933 = "2026-11-20T09:33:00+08:00"
DAY5_0934 = "2026-11-20T09:34:00+08:00"
DAY5_1015 = "2026-11-20T10:15:00+08:00"
DAY5_1020 = "2026-11-20T10:20:00+08:00"
DAY5_1030 = "2026-11-20T10:30:00+08:00"
DAY5_1031 = "2026-11-20T10:31:00+08:00"
DAY5_1032 = "2026-11-20T10:32:00+08:00"
DAY5_1100 = "2026-11-20T11:00:00+08:00"
# Day 6 -- 2026-11-21 (Chain 5: open borrow -> page-42 leaf bookmark -> after-hours box)
DAY6_1100 = "2026-11-21T11:00:00+08:00"
DAY6_1110 = "2026-11-21T11:10:00+08:00"
DAY6_1120 = "2026-11-21T11:20:00+08:00"
DAY6_1130 = "2026-11-21T11:30:00+08:00"
DAY6_2030 = "2026-11-21T20:30:00+08:00"
# Day 7 -- 2026-11-22 (Chain 6: visited-place booklet, source-loss subsidence, budget)
DAY7_0900 = "2026-11-22T09:00:00+08:00"
DAY7_1000 = "2026-11-22T10:00:00+08:00"
DAY7_1010 = "2026-11-22T10:10:00+08:00"
DAY7_1020 = "2026-11-22T10:20:00+08:00"
DAY7_1100 = "2026-11-22T11:00:00+08:00"
DAY7_1200 = "2026-11-22T12:00:00+08:00"
DAY7_1300 = "2026-11-22T13:00:00+08:00"
DAY7_1310 = "2026-11-22T13:10:00+08:00"
DAY7_1800 = "2026-11-22T18:00:00+08:00"

PRIVATE_DRAFT = "只写给自己看的草稿"
PUBLIC_INSCRIPTION = "展在书店的一张干净小卡"
BORROWED_RULER_ID = "nb_shared_shelf_paper_ruler"
BORROWED_BOOK_ID = "nb_bookstore_book_paper_joinery"
BOOKSTORE_BOOK_ITEM_ID = "book_paper_joinery"
#: A page body that lives in the raw notebook profile and must never surface in
#: any projection -- a tuck anchor is a position, not a grant to the page text.
NOTEBOOK_PAGE_SECRET = "第四十二页写着一行只该留在纸页上的字"
NOTEBOOK_PROFILE = {
    "kind": "book",
    "readable": True,
    "pages": 200,
    "open_state": "open",
    "page_42_text_cn": NOTEBOOK_PAGE_SECRET,
}


def _stable(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _row_by_id(space: dict, object_id: str) -> dict:
    return next(row for row in space["objects"] if row["object_id"] == object_id)


def _write_json(path: Path, doc: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_materials(root: Path, rows: list[dict]) -> None:
    _write_json(root / "phone" / "v03_encounters.json", {"shared_materials": rows})


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
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


def _stat_tuple(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return int(stat.st_size), int(stat.st_mtime_ns)


class FullOutlineEmergentWeekTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-full-outline-week-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _mutate(
        self,
        root: Path,
        payload: dict,
        *,
        actor_side: str,
        now_iso: str,
    ) -> dict:
        status, envelope = neighborhood_view.mutate_v04_neighborhood_view(
            root, payload, actor_side=actor_side, now_iso=now_iso
        )
        self.assertEqual(status, 200, envelope)
        self.assertTrue(envelope.get("ok"), envelope)
        self.assertEqual(envelope.get("llm_calls"), 0, envelope)
        return envelope

    def _read(self, root: Path, *, now_iso: str, subject: bool) -> dict:
        status, envelope = neighborhood_view.read_v04_neighborhood_view(
            root, has_subject_capability=subject, now_iso=now_iso
        )
        self.assertEqual(status, 200, envelope)
        self.assertEqual(envelope.get("llm_calls"), 0, envelope)
        return envelope

    def _seed_matter_space(self, root: Path, *, now_iso: str) -> None:
        """Author the real home space: add a study desk zone and its wooden chair.

        ``INTRODUCE_ITEM`` only accepts the shipped item-kind vocabulary, so the
        furniture chair is seeded as authored matter exactly the way the desktop
        world editor authors structural furniture.
        """
        state = matter._default_state()
        state["zones"]["study_desk"] = matter._zone_row(
            "study_desk",
            "书桌台面",
            ambient_zone="room",
            water_source=False,
            capacity_slots=4,
        )
        state["matter_objects"]["wooden_chair"] = matter._object_row(
            "wooden_chair", "木椅", item_kind="furniture", parent_id="study_desk"
        )
        state["revision"] = 1
        state["subject_view_revision"] = 1
        state["created_at"] = now_iso
        state["updated_at"] = now_iso
        path = matter.state_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _open_act(self, root: Path, *, intent: str, steps: list, now_iso: str) -> dict:
        receipt = matter.compose_open_act(
            root, {"intent_cn": intent, "steps": steps}, now_iso=now_iso
        )
        self.assertEqual(receipt["status"], "applied", receipt)
        return receipt

    # ------------------------------------------------------------------
    # Days 1-4, one continuous chain on ONE shared root
    # ------------------------------------------------------------------

    def _run_days_1_to_4(self, root: Path) -> None:
        # ================= Day 1 -- Chain 1 =================
        # Clear morning; hang the cloth out on the yard line.
        day1 = self._mutate(
            root,
            {"action": "set_local_weather", "weather": "clear", "airflow": "medium"},
            actor_side="partner",
            now_iso=DAY1_0900,
        )
        self.assertEqual(day1["weather_and_ground"]["weather"], "clear")

        hang = self._mutate(
            root,
            {
                "action": "hang_or_collect_cloth",
                "item_id": "wet_cloth_main",
                "operation": "hang",
                "target_support": "yard_line",
            },
            actor_side="subject",
            now_iso=DAY1_0900,
        )
        cloth = hang["drying_items"]["wet_cloth_main"]
        self.assertEqual(cloth["support_id"], "yard_line")

        # Seed the clock at 09:00, leave one fleeting thought, then settle across
        # a caller regime change at 11:00 (clear -> rain) until 15:00.
        status, envelope = clock.mutate_v03_clock_view(
            root, {"action": "advance_projection"}, actor_side="subject", now_iso=DAY1_0900
        )
        self.assertEqual(status, 200, envelope)

        status, envelope = clock.mutate_v03_clock_view(
            root,
            {
                "action": "record_evaporable",
                "thought_id": "evap_cloud_01",
                "domain_kind": "fleeting_thought",
                "accountability_tier": "EVAPORABLE",
                "text": "云飘过去了",
                "ttl_seconds": 7200,
            },
            actor_side="subject",
            now_iso=DAY1_0900,
        )
        self.assertEqual(status, 200, envelope)

        status, envelope = clock.mutate_v03_clock_view(
            root,
            {
                "action": "advance_projection",
                "boundary_events": [
                    {
                        "at_iso": DAY1_1100,
                        "kind": "weather_shift",
                        "weather": "rain",
                        "airflow": "gusty",
                        "window_state": "closed",
                        "note_cn": "午后转雨",
                    }
                ],
            },
            actor_side="subject",
            now_iso=DAY1_1500,
        )
        self.assertEqual(status, 200, envelope)
        receipt = envelope["data"]["action_receipt"]
        self.assertEqual(receipt["evaporated_count"], 1, receipt)
        self.assertGreaterEqual(receipt["piecewise_segments_count"], 2, receipt)
        self.assertEqual(receipt["boundary_cuts_applied"], 1, receipt)
        self.assertEqual(
            receipt["offline_recovery_report"]["fabricated_offline_actions"], 0, receipt
        )
        self.assertTrue(
            envelope["data"]["clocks"]["cognitive_clock"]["cognitive_backfill_forbidden"]
        )
        self.assertTrue(envelope["data"]["iron_laws"]["unknown_actions_never_guessed_by_clock"])

        # The real weather turns to rain; move the cloth under the porch shelter.
        rain = self._mutate(
            root,
            {"action": "set_local_weather", "weather": "rain", "airflow": "strong"},
            actor_side="partner",
            now_iso=DAY1_1100,
        )
        self.assertEqual(rain["weather_and_ground"]["weather"], "rain")

        moved = self._mutate(
            root,
            {
                "action": "hang_or_collect_cloth",
                "item_id": "wet_cloth_main",
                "operation": "move",
                "target_support": "porch_hook",
            },
            actor_side="subject",
            now_iso=DAY1_1110,
        )
        sheltered = moved["drying_items"]["wet_cloth_main"]
        self.assertEqual(sheltered["support_id"], "porch_hook")

        # Overnight processes settle to Day 2 09:00; the plant never dies offline.
        advanced = neighborhood.advance_v04_neighborhood_processes(root, now_iso=DAY2_0900)
        self.assertTrue(advanced["ok"], advanced)
        self.assertEqual(advanced["llm_calls"], 0)

        day2_morning = self._read(root, now_iso=DAY2_0900, subject=True)
        self.assertIs(day2_morning["potted_plant"]["never_dies_offline"], True)

        # ================= Day 2 -- Chain 2 =================
        self._mutate(
            root,
            {
                "action": "craft_paper_or_box",
                "operation": "cut_card",
                "card_id": "card_priv",
                "units": 2,
                "scrap_loss": 1,
                "title": "私事备忘",
                "draft_text": PRIVATE_DRAFT,
            },
            actor_side="subject",
            now_iso=DAY2_1400,
        )
        self._mutate(
            root,
            {
                "action": "craft_paper_or_box",
                "operation": "cut_card",
                "card_id": "card_pub",
                "units": 1,
                "scrap_loss": 0,
                "title": "给书店的小卡",
            },
            actor_side="subject",
            now_iso=DAY2_1400,
        )
        self._mutate(
            root,
            {
                "action": "craft_paper_or_box",
                "operation": "inscribe_card",
                "card_id": "card_pub",
                "text": PUBLIC_INSCRIPTION,
                "shared_or_displayed": True,
            },
            actor_side="subject",
            now_iso=DAY2_1405,
        )
        display = self._mutate(
            root,
            {
                "action": "bookstore_service",
                "service": "submit_display_card",
                "card_id": "card_pub",
            },
            actor_side="subject",
            now_iso=DAY2_1410,
        )
        self.assertEqual(display["bookstore"]["display_cards"][0]["card_id"], "card_pub")

        subject_view = self._read(root, now_iso=DAY2_1415, subject=True)
        self.assertIs(subject_view["craft_bench"]["integer_conservation_holds"], True)
        subject_cards = subject_view["craft_bench"]["cards_by_id"]
        self.assertEqual(subject_cards["card_priv"]["draft_text"], PRIVATE_DRAFT)
        self.assertEqual(subject_cards["card_pub"]["inscribed_text"], PUBLIC_INSCRIPTION)

        # Partner cold read: never sees the private draft, does see the shared card.
        partner_view = self._read(root, now_iso=DAY2_1415, subject=False)
        partner_cards = partner_view["craft_bench"]["cards_by_id"]
        private_card = partner_cards["card_priv"]
        self.assertIs(private_card["withheld"], True)
        self.assertIs(private_card["draft_withheld"], True)
        self.assertIsNone(private_card["draft_text"])
        self.assertIsNone(private_card["inscribed_text"])
        public_card = partner_cards["card_pub"]
        self.assertIs(public_card["shared_or_displayed"], True)
        self.assertIs(public_card["withheld"], False)
        self.assertEqual(public_card["inscribed_text"], PUBLIC_INSCRIPTION)
        self.assertNotIn(PRIVATE_DRAFT, _stable(partner_view))

        # ================= Days 3-4 -- Chain 3 =================
        self._seed_matter_space(root, now_iso=DAY3_0920)

        # Borrow the shared-shelf paper ruler into the staging corner.
        borrow_receipt = self._open_act(
            root,
            intent="向街角共享架借一把木尺",
            now_iso=DAY3_0920,
            steps=[
                {
                    "op": "BORROW_NEIGHBORHOOD_ITEM",
                    "source_ring": "shared_shelf",
                    "neighborhood_item_id": "paper_ruler",
                    "target_parent_id": "home_staging_corner",
                    "loan_hours": 48,
                }
            ],
        )
        borrow = borrow_receipt["step_results"][0]
        self.assertEqual(borrow["status"], "applied", borrow)
        self.assertEqual(borrow["object_id"], BORROWED_RULER_ID)
        self.assertEqual(borrow["loan_meta"]["loan_status"], "borrowed")
        self.assertFalse(borrow["loan_meta"]["affection_score_coupled"])

        # A second thing goes to the same staging spot.
        intro = self._open_act(
            root,
            intent="把折叠伞顺手放在门口暂放角",
            now_iso=DAY3_0925,
            steps=[
                {
                    "op": "INTRODUCE_ITEM",
                    "object_id": "umbrella_01",
                    "label_cn": "折叠伞",
                    "item_kind": "everyday",
                    "target_parent_id": "home_staging_corner",
                }
            ],
        )
        self.assertEqual(intro["step_results"][0]["status"], "applied")

        # The partner quietly moves the umbrella into the fridge.
        partner_move = matter.partner_move_shared_object(
            root,
            {"object_id": "umbrella_01", "target_parent_id": "home_fridge"},
            now_iso=DAY3_0930,
        )
        self.assertEqual(partner_move["status"], "applied")
        self.assertIs(partner_move["impression_stale"], True)

        # Packing honestly keeps the real prefix and never learns the hiding spot.
        pack_receipt = self._open_act(
            root,
            intent="把木尺和折叠伞都装进帆布挎包",
            now_iso=DAY3_0940,
            steps=[
                {
                    "op": "PACK_FOR_OUTING",
                    "carrier_id": "canvas_bag",
                    "item_ids": [BORROWED_RULER_ID, "umbrella_01"],
                    "expected_item_zones": {
                        BORROWED_RULER_ID: "home_staging_corner",
                        "umbrella_01": "home_staging_corner",
                    },
                }
            ],
        )
        pack = pack_receipt["step_results"][0]
        self.assertEqual(pack["status"], "partially_packed", pack)
        self.assertIn(BORROWED_RULER_ID, pack["packed_item_ids"])
        self.assertEqual(
            [row["object_id"] for row in pack["unpacked_items"]], ["umbrella_01"]
        )
        self.assertEqual(
            pack["unpacked_items"][0]["reason_code"], "OBJECT_NOT_AT_EXPECTED_SPOT"
        )
        self.assertNotIn("home_fridge", _stable(pack_receipt))

        subject_space = matter.read_matter_space_view(
            root, actor_side="subject", now_iso=DAY3_0941
        )
        self.assertEqual(subject_space["llm_calls"], 0)
        self.assertNotIn("umbrella_01", _stable(subject_space))
        packed_ruler = _row_by_id(subject_space, BORROWED_RULER_ID)
        self.assertEqual(packed_ruler["parent_id"], "canvas_bag")

        # Carry the bag out.  The in-transit projection is a pure zero-write read.
        self._mutate(
            root,
            {
                "action": "start_journey",
                "target_place": "bookstore",
                "carry_item_ids": ["canvas_bag"],
            },
            actor_side="subject",
            now_iso=DAY3_1000,
        )
        matter_path = matter.state_path(root)
        v04_path = root / "world" / "v04_neighborhood_state.json"
        before = (matter_path.stat().st_mtime_ns, matter_path.read_bytes(),
                  v04_path.stat().st_mtime_ns, v04_path.read_bytes())
        in_transit = matter.read_matter_space_view(
            root, actor_side="subject", now_iso=DAY3_1001
        )
        after = (matter_path.stat().st_mtime_ns, matter_path.read_bytes(),
                 v04_path.stat().st_mtime_ns, v04_path.read_bytes())
        self.assertEqual(before, after, "a zero-write read wrote to disk")
        transit_ruler = _row_by_id(in_transit, BORROWED_RULER_ID)
        self.assertIs(transit_ruler["carried_on_journey"], True)
        self.assertEqual(transit_ruler["effective_place_id"], "in_transit")

        # Walk to the bookstore, then back home.
        self._mutate(
            root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso=DAY3_1100,
        )
        at_bookstore = matter.read_matter_space_view(
            root, actor_side="subject", now_iso=DAY3_1100
        )
        self.assertEqual(
            _row_by_id(at_bookstore, BORROWED_RULER_ID)["effective_place_id"], "bookstore"
        )
        self._mutate(
            root,
            {
                "action": "start_journey",
                "target_place": "home",
                "carry_item_ids": ["canvas_bag"],
            },
            actor_side="subject",
            now_iso=DAY3_1110,
        )
        self._mutate(
            root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso=DAY3_1300,
        )

        # Before leaning the bag, freeze the child row's physical facts.
        child_before = matter._read_state_zero_write(root)["matter_objects"][
            BORROWED_RULER_ID
        ]
        child_facts_before = (
            child_before["parent_id"],
            child_before.get("updated_at"),
            child_before.get("state_clock_iso"),
        )

        lean = self._open_act(
            root,
            intent="把帆布挎包靠在书桌边的木椅旁",
            now_iso=DAY3_1320,
            steps=[
                {
                    "op": "PLACE_OR_MOVE",
                    "object_id": "canvas_bag",
                    "target_parent_id": "wooden_chair",
                    "placement_relation": "leaning_on",
                }
            ],
        )
        self.assertEqual(lean["step_results"][0]["status"], "applied")

        child_after = matter._read_state_zero_write(root)["matter_objects"][
            BORROWED_RULER_ID
        ]
        child_facts_after = (
            child_after["parent_id"],
            child_after.get("updated_at"),
            child_after.get("state_clock_iso"),
        )
        self.assertEqual(
            child_facts_before, child_facts_after, "leaning the carrier rewrote the child"
        )

        # Day 4 cold reload: the ruler's place is derived through the chain.
        cold = matter.read_matter_space_view(root, actor_side="subject", now_iso=DAY4_0900)
        cold_ruler = _row_by_id(cold, BORROWED_RULER_ID)
        self.assertEqual(cold_ruler["effective_zone_id"], "study_desk")
        self.assertEqual(cold_ruler["carrier_chain"], ["canvas_bag", "wooden_chair"])
        self.assertEqual(cold_ruler["parent_id"], "canvas_bag")
        self.assertEqual(cold_ruler["effective_place_id"], "home")
        self.assertEqual(
            matter._read_state_zero_write(root)["matter_objects"][BORROWED_RULER_ID][
                "parent_id"
            ],
            "canvas_bag",
        )

        # Take the ruler out of the bag and return it to the shared shelf.
        self._open_act(
            root,
            intent="把木尺从挎包里取出来放回书桌",
            now_iso=DAY4_0910,
            steps=[
                {
                    "op": "PLACE_OR_MOVE",
                    "object_id": BORROWED_RULER_ID,
                    "target_parent_id": "study_desk",
                }
            ],
        )
        return_receipt = self._open_act(
            root,
            intent="把借来的木尺还给街角共享架",
            now_iso=DAY4_0920,
            steps=[{"op": "RETURN_BORROWED_ITEM", "object_id": BORROWED_RULER_ID}],
        )
        returned = return_receipt["step_results"][0]
        self.assertEqual(returned["loan_status"], "returned_received", returned)
        self.assertIs(returned["returned_out"], True)
        self.assertFalse(returned["affection_score_coupled"])

        final_space = matter.read_matter_space_view(
            root, actor_side="subject", now_iso=DAY4_0920
        )
        self.assertNotIn(
            BORROWED_RULER_ID, {row["object_id"] for row in final_space["objects"]}
        )
        raw_ruler = matter._read_state_zero_write(root)["matter_objects"][
            BORROWED_RULER_ID
        ]
        self.assertEqual(raw_ruler["loan_meta"]["loan_status"], "returned_received")

    # ------------------------------------------------------------------
    # Days 5-7, chains 4-6, still on the SAME shared root
    # ------------------------------------------------------------------

    def _run_days_5_to_7(self, root: Path) -> None:
        # ================= Day 5 -- Chain 4 =================
        # The partner closes the open shortcut while the subject is indoors.
        self._mutate(
            root,
            {
                "action": "set_roadblock",
                "route_id": "open_shortcut",
                "blocked": True,
                "reason": "临时围栏施工",
            },
            actor_side="partner",
            now_iso=DAY5_0900,
        )

        # No omniscient leak: standing at home she has not discovered it yet.
        indoors = self._read(root, now_iso=DAY5_0905, subject=True)
        self.assertEqual(indoors["roadblocks"], [])

        # She walks home -> bookstore; two short legs bring her to the yard
        # junction, where the real closure is discovered honestly.
        departed = self._mutate(
            root,
            {"action": "start_journey", "target_place": "bookstore"},
            actor_side="subject",
            now_iso=DAY5_0930,
        )
        self.assertEqual(departed["result"]["outcome"], "departed")

        halted = self._mutate(
            root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso=DAY5_0932,
        )
        halted_journey = halted["journey"]
        self.assertEqual(halted_journey["state"], "paused_at_node")
        self.assertEqual(halted_journey["halt_reason"], "ROADBLOCK_ENCOUNTERED")
        self.assertEqual(halted_journey["current_place_id"], "yard")
        self.assertIn("open_shortcut", halted_journey["discovered_roadblocks"])
        self.assertTrue(
            any(rb["route_id"] == "open_shortcut" for rb in halted["roadblocks"]),
            halted["roadblocks"],
        )

        # Honest shelved detour: rebuild the remaining legs under the eaves.
        rerouted = self._mutate(
            root,
            {
                "action": "advance_or_turnback_journey",
                "operation": "reroute",
                "preferred_branch": "eaves_walkway",
            },
            actor_side="subject",
            now_iso=DAY5_0933,
        )
        self.assertEqual(rerouted["result"]["outcome"], "rerouted")
        rerouted_journey = self._read(root, now_iso=DAY5_0934, subject=True)["journey"]
        self.assertEqual(rerouted_journey["state"], "in_transit")
        self.assertIs(rerouted_journey["active_route_sheltered"], True)
        self.assertEqual(rerouted_journey["active_route_id"], "eaves_walkway")
        self.assertIsNone(rerouted_journey["current_place_id"])
        self.assertTrue(bool(rerouted_journey["transit_location_cn"]))

        arrived = self._mutate(
            root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso=DAY5_1015,
        )
        self.assertEqual(arrived["journey"]["state"], "arrived")
        self.assertEqual(arrived["journey"]["current_place_id"], "bookstore")

        # Drizzle on the way back; turning around mid-leg preserves walked time.
        self._mutate(
            root,
            {"action": "set_local_weather", "weather": "drizzle", "airflow": "medium"},
            actor_side="partner",
            now_iso=DAY5_1020,
        )
        self._mutate(
            root,
            {"action": "start_journey", "target_place": "home"},
            actor_side="subject",
            now_iso=DAY5_1030,
        )
        walked_before_turnback = self._read(
            root, now_iso=DAY5_1031, subject=True
        )["journey"]["accumulated_walk_seconds"]
        self.assertGreater(walked_before_turnback, 0.0)

        turned = self._mutate(
            root,
            {
                "action": "advance_or_turnback_journey",
                "operation": "turn_back",
                "return_to_place": "home",
            },
            actor_side="subject",
            now_iso=DAY5_1032,
        )
        turn_result = turned["result"]
        self.assertEqual(turn_result["operation"], "turn_back")
        self.assertGreater(
            turn_result["accumulated_walk_seconds"],
            walked_before_turnback,
            "turning back must never roll back the elapsed walk",
        )

        home_again = self._mutate(
            root,
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor_side="subject",
            now_iso=DAY5_1100,
        )
        self.assertEqual(home_again["journey"]["state"], "arrived")
        self.assertEqual(home_again["journey"]["current_place_id"], "home")
        self.assertIs(home_again["journey"]["is_turning_back"], True)

        # ================= Day 6 -- Chain 5 =================
        # Borrow one real bookstore book into the home staging corner.
        borrow_receipt = self._open_act(
            root,
            intent="在旧书店开门时借一本手工志",
            now_iso=DAY6_1100,
            steps=[
                {
                    "op": "BORROW_NEIGHBORHOOD_ITEM",
                    "source_ring": "bookstore",
                    "neighborhood_item_id": BOOKSTORE_BOOK_ITEM_ID,
                    "target_parent_id": "home_staging_corner",
                    "loan_days": 7,
                }
            ],
        )
        borrowed = borrow_receipt["step_results"][0]
        self.assertEqual(borrowed["status"], "applied", borrowed)
        self.assertEqual(borrowed["object_id"], BORROWED_BOOK_ID)
        self.assertEqual(borrowed["loan_meta"]["loan_status"], "borrowed")
        self.assertFalse(borrowed["loan_meta"]["affection_score_coupled"])

        # An open notebook and a dry leaf; the leaf is tucked at page 42.
        self._open_act(
            root,
            intent="把摊开的笔记本和一片干叶放到书桌上",
            now_iso=DAY6_1110,
            steps=[
                {
                    "op": "INTRODUCE_ITEM",
                    "object_id": "notebook_01",
                    "label_cn": "摊开的笔记本",
                    "item_kind": "paper",
                    "paper_like": True,
                    "target_parent_id": "study_desk",
                    "capacity_slots": 2,
                },
                {
                    "op": "INTRODUCE_ITEM",
                    "object_id": "dry_leaf",
                    "label_cn": "干叶书签",
                    "item_kind": "keepsake",
                    "target_parent_id": "study_desk",
                },
            ],
        )
        # Author the open/readable book profile on the real row (plain data only).
        state = matter._read_state_zero_write(root)
        state["matter_objects"]["notebook_01"]["open_state"] = "open"
        state["matter_objects"]["notebook_01"]["matter_profile"] = dict(
            NOTEBOOK_PROFILE
        )
        matter._bump(state, DAY6_1110)
        matter._atomic_write_state(root, state)

        tucked = self._open_act(
            root,
            intent="把干叶夹进笔记本第四十二页",
            now_iso=DAY6_1120,
            steps=[
                {
                    "op": "PLACE_INSIDE",
                    "object_id": "dry_leaf",
                    "target_parent_id": "notebook_01",
                    "placement_relation": "tucked_in",
                    "anchor_page": 42,
                }
            ],
        )
        self.assertEqual(tucked["step_results"][0]["anchor_page"], 42)

        # Closing and moving the notebook across zones keeps the page mark,
        # and never turns the anchor into a grant to the page's text.
        self._open_act(
            root,
            intent="合上笔记本把它挪到顺手暂放角",
            now_iso=DAY6_1130,
            steps=[
                {
                    "op": "PLACE_OR_MOVE",
                    "object_id": "notebook_01",
                    "target_parent_id": "home_staging_corner",
                    "open_state": "closed",
                }
            ],
        )
        after_move = matter.read_matter_space_view(
            root, actor_side="subject", now_iso=DAY6_1130
        )
        self.assertEqual(after_move["llm_calls"], 0)
        notebook_row = _row_by_id(after_move, "notebook_01")
        leaf_row = _row_by_id(after_move, "dry_leaf")
        self.assertEqual(notebook_row["open_state"], "closed")
        self.assertEqual(leaf_row["parent_id"], "notebook_01")
        self.assertEqual(leaf_row["anchor_page"], 42)
        self.assertNotIn(NOTEBOOK_PAGE_SECRET, _stable(after_move))

        # After the 19:00 closing the store never fakes the shopkeeper taking it.
        after_hours = self._open_act(
            root,
            intent="闭店后把书投进书店门口归还箱",
            now_iso=DAY6_2030,
            steps=[{"op": "RETURN_BORROWED_ITEM", "object_id": BORROWED_BOOK_ID}],
        )
        returned = after_hours["step_results"][0]
        self.assertEqual(returned["status"], "applied", returned)
        self.assertEqual(returned["loan_status"], "return_box_pending")
        self.assertIs(returned["shopkeeper_received"], False)
        raw_loan = matter._read_state_zero_write(root)["matter_objects"][
            BORROWED_BOOK_ID
        ]["loan_meta"]
        self.assertEqual(raw_loan["loan_status"], "return_box_pending")
        self.assertIs(raw_loan["shopkeeper_received"], False)

        # ================= Day 7 -- Chain 6 =================
        # Visited-place booklet: an unvisited page stays honestly blank.
        status, body = ep_view.mutate_v04_elastic_play_view(
            root,
            {
                "action": "curate_personal_project",
                "op": "start",
                "medium_kind": "place_booklet",
                "purpose_cn": "把逛过的角落记几笔",
            },
            actor_side="subject",
            now_iso=DAY7_0900,
        )
        self.assertEqual(status, 200, body)
        project = body["data"]["personal_projects"][0]
        project_id = project["project_id"]
        self.assertEqual(project["status"], "active")
        self.assertEqual(project["confidence_penalty"], 0)

        status, body = ep_view.mutate_v04_elastic_play_view(
            root,
            {
                "action": "curate_personal_project",
                "op": "add_field_note",
                "project_id": project_id,
                "place_id": "forest_trail",
                "entry_status": "visited_noted",
                "note_cn": "林子边的风很安静",
            },
            actor_side="subject",
            now_iso=DAY7_1000,
        )
        self.assertEqual(status, 409, body)
        self.assertEqual(body["reason_code"], "UNVISITED_PLACE_MUST_STAY_BLANK")

        status, body = ep_view.mutate_v04_elastic_play_view(
            root,
            {
                "action": "curate_personal_project",
                "op": "add_field_note",
                "project_id": project_id,
                "place_id": "bookstore",
                "entry_status": "visited_noted",
                "note_cn": "靠窗那张旧木桌的光很慢",
            },
            actor_side="subject",
            now_iso=DAY7_1010,
        )
        self.assertEqual(status, 200, body)

        status, body = ep_view.mutate_v04_elastic_play_view(
            root,
            {
                "action": "curate_personal_project",
                "op": "pause_not_feeling_it",
                "project_id": project_id,
            },
            actor_side="subject",
            now_iso=DAY7_1020,
        )
        self.assertEqual(status, 200, body)
        paused = next(
            p
            for p in body["data"]["personal_projects"]
            if p["project_id"] == project_id
        )
        self.assertEqual(paused["status"], "resting_not_feeling_it")
        self.assertEqual(paused["confidence_penalty"], 0)
        self.assertEqual(body["data"]["llm_calls"], 0)

        # A structured origin that was never observed cannot become a concern.
        with self.assertRaises(edl.EverydayDesireError) as ctx:
            edl.apply_everyday_desire_action(
                root,
                {
                    "action": "emerge_concern",
                    "domain": "exploration",
                    "origin_kind": "immediate_opportunity",
                    "origin_refs": ["object:does_not_exist"],
                    "expression": "看看",
                },
                now_iso=DAY7_1000,
            )
        self.assertEqual(ctx.exception.http_status, 409)
        self.assertEqual(
            ctx.exception.reason_code, "CONCERN_ORIGIN_NOT_OBSERVED"
        )

        # A real opened material may hold a retained concern...
        _seed_materials(
            root,
            [
                {
                    "material_id": "material_01",
                    "status": "OPENED_READ",
                    "title": "雨水小记",
                    "title_cn": "雨水小记",
                }
            ],
        )
        applied = edl.apply_everyday_desire_action(
            root,
            {
                "action": "emerge_concern",
                "domain": "creation",
                "origin_kind": "sustained_interest",
                "origin_refs": ["material:material_01"],
                "expression": "想把这段抄进小册",
                "persistence": "retained",
            },
            now_iso=DAY7_1100,
        )
        self.assertEqual(applied["status"], "applied", applied)
        concern_ref = applied["action_receipt"]["concern"]["concern_ref"]

        # ...and when that source is withdrawn it subsides quietly, never
        # writing a single byte or charging penalty/resentment/illness.
        _seed_materials(
            root,
            [
                {
                    "material_id": "material_01",
                    "status": "WITHDRAWN",
                    "title": "雨水小记",
                    "title_cn": "雨水小记",
                }
            ],
        )
        desire_state_path = edl.everyday_desire_state_path(root)
        before_stat = _stat_tuple(desire_state_path)
        before_tree = _snapshot_tree(root)
        projection = edl.project_everyday_desires(root, now_iso=DAY7_1800)
        self.assertEqual(_stat_tuple(desire_state_path), before_stat)
        self.assertEqual(_snapshot_tree(root), before_tree)
        self.assertEqual(projection["retained_concerns"], [])
        self.assertEqual(len(projection["auto_subsided_concerns"]), 1)
        subsided = projection["auto_subsided_concerns"][0]
        self.assertEqual(subsided["concern_ref"], concern_ref)
        self.assertEqual(subsided["reason"], "origin_no_longer_present")
        self.assertEqual(subsided["penalty"], 0)
        self.assertEqual(subsided["resentment_delta"], 0)
        self.assertEqual(subsided["illness_delta"], 0)
        self.assertEqual(projection["llm_calls"], 0)

        # The compact lived-context header stays inside the 1800-char budget
        # and the telemetry read touches no file.
        budget_before = _snapshot_tree(root)
        budget = lcb.measure_context_budget_breakdown(root, now_iso=DAY7_1800)
        self.assertEqual(_snapshot_tree(root), budget_before)
        self.assertIs(budget["within_budget"], True)
        self.assertLessEqual(budget["total_chars"], 1800)

        # A zero-code container: data-only vase row, then a real reed inside it.
        state = matter._read_state_zero_write(root)
        state["matter_objects"]["ceramic_vase"] = matter._object_row(
            "ceramic_vase",
            "青瓷花瓶",
            item_kind="container",
            parent_id="home_staging_corner",
            capacity_slots=2,
        )
        matter._bump(state, DAY7_1200)
        matter._atomic_write_state(root, state)

        self._open_act(
            root,
            intent="把干芦苇收进青瓷花瓶",
            now_iso=DAY7_1310,
            steps=[
                {
                    "op": "INTRODUCE_ITEM",
                    "object_id": "dried_reed",
                    "label_cn": "干芦苇",
                    "item_kind": "keepsake",
                    "target_parent_id": "home_staging_corner",
                },
                {
                    "op": "PLACE_INSIDE",
                    "object_id": "dried_reed",
                    "target_parent_id": "ceramic_vase",
                },
            ],
        )

        cold_view = matter.read_matter_space_view(
            root, actor_side="subject", now_iso=DAY7_1800
        )
        self.assertEqual(cold_view["llm_calls"], 0)
        self.assertEqual(_row_by_id(cold_view, "ceramic_vase")["item_kind"], "container")
        reed_row = _row_by_id(cold_view, "dried_reed")
        self.assertEqual(reed_row["parent_id"], "ceramic_vase")
        self.assertEqual(reed_row["placement_relation"], "inside")
        self.assertEqual(_row_by_id(cold_view, "dry_leaf")["anchor_page"], 42)
        self.assertNotIn(NOTEBOOK_PAGE_SECRET, _stable(cold_view))

    def test_01_seven_day_chains_1_to_3_on_shared_root(self) -> None:
        self._run_days_1_to_4(self.root)

    def test_02_full_seven_day_six_chains_emergent_week(self) -> None:
        self._run_days_1_to_4(self.root)
        self._run_days_5_to_7(self.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)

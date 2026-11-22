#!/usr/bin/env python3
"""Tonight's Waves J-O capstone: one continuous seven-day night chain.

Every day below runs sequentially on the SAME ``self.root`` instance root -- no
reset, no second fixture -- with ``llm_calls == 0`` throughout.  The file is the
integration proof that the J-O surfaces compose into one coherent everyday life:

Day 1  old-bookstore counter, rotating shopkeeper task, sourced street rumor,
       two people reading one page with distinct annotations, and a scalding
       cup that honestly waits.
Day 2  the rumor is checked firsthand and the subject detours under the eaves;
       a private ``home_desk`` nickname never reaches the partner; a parched
       porch plant shows up in the daily scan and recovers when watered.
Day 3  a work-bound divergence is paused as ``keep both`` (zero daily nagging)
       and a temporary game stays purely fictional -- no real object moves.
Day 4  the 12th market is missed at home; the 13th only shows physical leftover
       traces, and a prepared surprise stays spoiler-free until offered/revealed.
Day 5  winter water saving, an evening micro-zone warmth read, and a personal
       ritual that fades into a *real* retained technique with zero debt.
Day 6  a consumed ``matter:`` object makes a cross-store note end in
       ``409 SOURCE_GONE`` and auto-release; a page-37 bookmark stays honest.
Day 7  spring watering crosses a new-leaf milestone; a cold-restart loop of 20
       reads touches zero bytes, stays inside the 1800-char budget, and all
       three frozen anchors are verified untouched.

Run (native Windows Python)::

    python -B -m unittest tests.test_v08_night_capstone_week -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from components.life.scripts import v04_elastic_play_learning as ep_engine  # noqa: E402
from components.life.scripts import v08_emergent_echoes as echoes  # noqa: E402
from components.phone import emotional_tension_view as et  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402
from components.phone import v08_emergent_echoes_view as echoes_view  # noqa: E402
from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v04_neighborhood_living as neighborhood  # noqa: E402
from components.world.scripts import v08_microzone_ecology as micro  # noqa: E402
from components.world.scripts import v08_universal_matter as matter  # noqa: E402

TZ8 = timezone(timedelta(hours=8))

# Day 1 -- 2026-11-09, early autumn, clear.
DAY1_0850 = "2026-11-09T08:50:00+08:00"
DAY1_0900 = "2026-11-09T09:00:00+08:00"
DAY1_1500 = "2026-11-09T15:00:00+08:00"
DAY1_1505 = "2026-11-09T15:05:00+08:00"
DAY1_1510 = "2026-11-09T15:10:00+08:00"
DAY1_1516 = "2026-11-09T15:16:00+08:00"
DAY1_1520 = "2026-11-09T15:20:00+08:00"
# Day 2 -- 2026-11-10, rumor checked and the parched plant recovers.
DAY2_0830 = "2026-11-10T08:30:00+08:00"
DAY2_0900 = "2026-11-10T09:00:00+08:00"
DAY2_0905 = "2026-11-10T09:05:00+08:00"
DAY2_0910 = "2026-11-10T09:10:00+08:00"
DAY2_0915 = "2026-11-10T09:15:00+08:00"
DAY2_0920 = "2026-11-10T09:20:00+08:00"
DAY2_0925 = "2026-11-10T09:25:00+08:00"
DAY2_0927 = "2026-11-10T09:27:00+08:00"
DAY2_0940 = "2026-11-10T09:40:00+08:00"
DAY2_0945 = "2026-11-10T09:45:00+08:00"
DAY2_0955 = "2026-11-10T09:55:00+08:00"
# Day 3 -- 2026-11-11, kept divergence and a fictional game.
DAY3_0900 = "2026-11-11T09:00:00+08:00"
DAY3_0905 = "2026-11-11T09:05:00+08:00"
DAY3_0910 = "2026-11-11T09:10:00+08:00"
DAY3_0915 = "2026-11-11T09:15:00+08:00"
DAY3_0920 = "2026-11-11T09:20:00+08:00"
DAY3_0925 = "2026-11-11T09:25:00+08:00"
# Day 4 -- 2026-11-12 (missed market) and 2026-11-13 (residual traces).
DAY4_1209 = "2026-11-12T09:00:00+08:00"
DAY4_1212 = "2026-11-12T12:00:00+08:00"
DAY4_1310 = "2026-11-13T10:30:00+08:00"
DAY4_1311 = "2026-11-13T11:00:00+08:00"
DAY4_1312 = "2026-11-13T11:10:00+08:00"
DAY4_1313 = "2026-11-13T11:12:00+08:00"
DAY4_1315 = "2026-11-13T11:15:00+08:00"
DAY4_1320 = "2026-11-13T11:20:00+08:00"
DAY4_1330 = "2026-11-13T11:30:00+08:00"
DAY4_1340 = "2026-11-13T11:40:00+08:00"
# Day 5 -- 2026-11-14, winter water saving, warmth, personal ritual.
DAY5_0900 = "2026-11-14T09:00:00+08:00"
DAY5_0905 = "2026-11-14T09:05:00+08:00"
DAY5_2000 = "2026-11-14T20:00:00+08:00"
DAY5_2005 = "2026-11-14T20:05:00+08:00"
DAY5_2010 = "2026-11-14T20:10:00+08:00"
DAY5_2015 = "2026-11-14T20:15:00+08:00"
DAY5_2020 = "2026-11-14T20:20:00+08:00"
# Day 6 -- 2026-11-15, cross-store SOURCE_GONE and an honest bookmark.
DAY6_1000 = "2026-11-15T10:00:00+08:00"
DAY6_1005 = "2026-11-15T10:05:00+08:00"
DAY6_1007 = "2026-11-15T10:07:00+08:00"
DAY6_1010 = "2026-11-15T10:10:00+08:00"
DAY6_1012 = "2026-11-15T10:12:00+08:00"
DAY6_1013 = "2026-11-15T10:13:00+08:00"
DAY6_1014 = "2026-11-15T10:14:00+08:00"
DAY6_1015 = "2026-11-15T10:15:00+08:00"
DAY6_1016 = "2026-11-15T10:16:00+08:00"
DAY6_1017 = "2026-11-15T10:17:00+08:00"
DAY6_1018 = "2026-11-15T10:18:00+08:00"
# Day 7 -- 2026-11-16, spring new leaf, cold restart and closing checks.
DAY7_0000 = "2026-11-16T00:00:00+08:00"
DAY7_0002 = "2026-11-16T00:02:00+08:00"
DAY7_0005 = "2026-11-16T00:05:00+08:00"
DAY7_2355 = "2026-11-16T23:55:00+08:00"
DAY7_2359 = "2026-11-16T23:59:00+08:00"

RUMOR_ROUTE = "open_shortcut"
PRIVATE_NICKNAME = "雨棚"
DIVERGENCE_WORK_REF = "artifact:night_capstone_draft"
DIVERGENCE_MEMO = "原版更含蓄，改版更清楚，先两版都留着"
PRIVATE_RITUAL_TITLE = "睡前把第二天要穿的衣服搭在椅背上"
SURPRISE_TITLE = "夹在旧书里的银杏书签"
SURPRISE_CONTENT = "等你不忙的时候再看"

FROZEN_ANCHORS = (
    "components/life/scripts/autonomous_life_assembly.py",
    "components/life/scripts/life_sharing_service.py",
    "components/native/native_recall/readonly_recall_adapter.py",
)

# Known-good content digests for the frozen anchors.  Each is asserted as an
# exact SHA256 equality, so a content rewrite fails regardless of how the
# checkout reports file modes.  ``git diff`` is deliberately NOT used: under
# WSL's /mnt/d cross-filesystem mount the index stat bits and CRLF config can
# misreport a clean file as dirty, so only byte-identical content is trusted.
FROZEN_ANCHOR_SHA256 = {
    "components/life/scripts/autonomous_life_assembly.py": (
        "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434"
    ),
    "components/life/scripts/life_sharing_service.py": (
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038"
    ),
    "components/native/native_recall/readonly_recall_adapter.py": (
        "6667ff66fe8105f28f436609f0cd8d744b185c0580e7f1642aafef0b873af78a"
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
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


def _row_by_id(rows: list[dict[str, Any]], object_id: str) -> dict[str, Any]:
    for row in rows:
        if row.get("object_id") == object_id:
            return row
    raise AssertionError(f"object not in view: {object_id}")


class NightCapstoneWeekTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="v08-night-capstone-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self._anchor_hashes = {
            rel: _sha256(ROOT / rel)
            for rel in FROZEN_ANCHORS
            if (ROOT / rel).is_file()
        }

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _snap(self) -> dict[str, tuple[int, int, str]]:
        return _snapshot(self.root)

    def _nb(self, now_iso: str, *, subject: bool = True) -> dict[str, Any]:
        code, body = read_v04_neighborhood_view(
            self.root, has_subject_capability=subject, now_iso=now_iso
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body["llm_calls"], 0)
        return body

    def _nb_mutate(
        self, payload: dict[str, Any], *, actor: str, now_iso: str
    ) -> tuple[int, dict[str, Any]]:
        return mutate_v04_neighborhood_view(
            self.root, payload, actor_side=actor, now_iso=now_iso
        )

    def _nb_state(self) -> dict[str, Any]:
        state, exists = neighborhood.load_state_readonly(self.root)
        self.assertTrue(exists)
        return state

    def _play(self, now_iso: str, *, subject: bool = True) -> dict[str, Any]:
        code, body = ep_view.read_v04_elastic_play_view(
            self.root, has_subject_capability=subject, now_iso=now_iso
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body["data"]["llm_calls"], 0)
        return body["data"]

    def _play_mutate(
        self, payload: dict[str, Any], *, actor: str, now_iso: str
    ) -> tuple[int, dict[str, Any]]:
        return ep_view.mutate_v04_elastic_play_view(
            self.root, payload, actor_side=actor, now_iso=now_iso
        )

    def _matter_act(self, payload: dict[str, Any], *, now_iso: str) -> dict[str, Any]:
        return matter.compose_open_act(self.root, payload, now_iso=now_iso)

    def _matter_view(self, now_iso: str, *, actor: str = "subject") -> dict[str, Any]:
        view = matter.read_matter_space_view(
            self.root, actor_side=actor, now_iso=now_iso
        )
        self.assertEqual(view["llm_calls"], 0)
        return view

    def _echoes(self, now_iso: str, *, subject: bool = True) -> dict[str, Any]:
        projected = echoes.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=subject, now_iso=now_iso
        )
        self.assertEqual(projected["llm_calls"], 0)
        return projected

    def _assert_budget(self, now_iso: str) -> dict[str, Any]:
        before = self._snap()
        budget = lcb.measure_context_budget_breakdown(self.root, now_iso=now_iso)
        self.assertEqual(self._snap(), before, "budget measurement must be zero-write")
        self.assertIs(budget["within_budget"], True, budget)
        self.assertLessEqual(budget["total_chars"], 1800)
        self.assertLessEqual(len(budget["rendered_text"]), lcb.MAX_LIVED_CONTEXT_CHARS)
        return budget

    def _assert_anchor_intact(self, rel: str) -> None:
        # Strict one-to-one byte check against the known-good digest.  This is
        # the only anchor gate: it is stable on both native Windows and WSL,
        # unlike ``git diff`` which flags spurious mode/CRLF changes over NTFS.
        digest = _sha256(ROOT / rel)
        expected = FROZEN_ANCHOR_SHA256[rel]
        self.assertEqual(
            digest,
            expected,
            f"frozen anchor content changed: {rel} (sha256={digest})",
        )

    # ------------------------------------------------------------------
    # The one continuous seven-day chain
    # ------------------------------------------------------------------
    def test_night_capstone_seven_day_chain(self) -> None:
        # Frozen anchors must be byte-identical before anything runs.
        for rel in self._anchor_hashes:
            self._assert_anchor_intact(rel)

        # Calm defaults stay strictly zero-char silent and create no file.
        pristine = self.root / "pristine-empty"
        before = self._snap()
        self.assertEqual(
            lcb.render_lived_companion_context(
                pristine, {"phone": {"enabled": False}}, now_iso=DAY1_0900
            ),
            "",
        )
        self.assertFalse(pristine.exists(), "a calm render must not create the root")
        self.assertEqual(
            lcb._format_v08_living_traces_micro(
                {},
                include_private=True,
                now_dt=datetime.fromisoformat(DAY1_0900),
            ),
            "",
        )
        self.assertEqual(self._snap(), before)

        self._day_1_bookstore_rumor_tea_and_annotation()
        self._day_2_rumor_confirmed_plant_and_private_corner()
        self._day_3_kept_divergence_and_fictional_game()
        self._day_4_missed_market_residual_traces_and_surprise()
        self._day_5_winter_warmth_and_personal_ritual()
        self._day_6_source_gone_and_honest_bookmark()
        self._day_7_spring_leaf_cold_restart_and_closeout()

    # ------------------------------------------------------------------
    # Day 1
    # ------------------------------------------------------------------
    def _day_1_bookstore_rumor_tea_and_annotation(self) -> None:
        code, _ = self._nb_mutate(
            {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
            actor="partner",
            now_iso=DAY1_0850,
        )
        self.assertEqual(code, 200)

        # Rotating shopkeeper task is a pure clock function, read-only.
        before = self._snap()
        early = self._nb(DAY1_0900)
        late = self._nb(DAY1_1500)
        self.assertEqual(self._snap(), before, "shopkeeper reads must be zero-write")
        early_life = early["bookstore"]["shopkeeper_life"]
        late_life = late["bookstore"]["shopkeeper_life"]
        self.assertEqual(early_life["shopkeeper_name"], neighborhood.SHOPKEEPER_NAME)
        self.assertEqual(early_life["privilege_boundary"], "public_counter_only")
        self.assertIs(early_life["private_room_knowledge"], False)
        self.assertIs(early_life["private_drafts_exposed"], False)
        self.assertNotEqual(early_life["current_task"], late_life["current_task"])
        self.assertEqual(early["environment"]["weather"], "clear")
        self.assertEqual(early["environment"]["season"], "autumn")

        # A sourced rumor is hearsay only: it never edits real world truth.
        code, result = self._nb_mutate(
            {
                "action": "bookstore_service",
                "service": "share_shopkeeper_rumor",
                "route_id": RUMOR_ROUTE,
            },
            actor="subject",
            now_iso=DAY1_1505,
        )
        self.assertEqual(code, 200, result)
        rumor = result["result"]
        self.assertEqual(rumor["source_name"], neighborhood.SHOPKEEPER_NAME)
        self.assertIs(rumor["verified_firsthand"], False)
        state = self._nb_state()
        self.assertEqual(state["world_truth"]["roadblocks"], {})
        self.assertEqual(state["journey"]["discovered_roadblocks"], [])

        # A scalding cup refuses an immediate sip with an honest 409.
        receipt = self._matter_act(
            {
                "intent_cn": "沏一杯刚烧开的热茶",
                "steps": [
                    {
                        "op": "INTRODUCE_ITEM",
                        "object_id": "night_tea_01",
                        "label_cn": "刚沏的热茶",
                        "item_kind": "drink",
                        "target_parent_id": "dining_table",
                        "portions": 2,
                        "thermal": "hot",
                        "boil_scalding": True,
                    }
                ],
            },
            now_iso=DAY1_1510,
        )
        self.assertEqual(receipt["status"], "applied")
        tea = _row_by_id(self._matter_view(DAY1_1510)["objects"], "night_tea_01")
        self.assertTrue(tea["scalding"])
        self.assertEqual(tea["scalding_wait_minutes"], 5)
        self.assertEqual(tea["scalding_until_iso"], "2026-11-09T15:15:00+08:00")

        before = self._snap()
        with self.assertRaises(matter.V08MatterError) as ctx:
            self._matter_act(
                {
                    "intent_cn": "马上喝一口",
                    "steps": [{"op": "CONSUME_PORTION", "object_id": "night_tea_01"}],
                },
                now_iso=DAY1_1510,
            )
        self.assertEqual(ctx.exception.reason_code, "DRINK_SCALDING_WAIT")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self._snap(), before, "a scalding refusal is not a write")
        self.assertEqual(
            _row_by_id(self._matter_view(DAY1_1510)["objects"], "night_tea_01")[
                "portions_left"
            ],
            2,
        )

        receipt = self._matter_act(
            {
                "intent_cn": "茶凉了一点，喝一口",
                "steps": [{"op": "CONSUME_PORTION", "object_id": "night_tea_01"}],
            },
            now_iso=DAY1_1516,
        )
        self.assertEqual(receipt["status"], "applied")
        tea = _row_by_id(self._matter_view(DAY1_1516)["objects"], "night_tea_01")
        self.assertEqual(tea["portions_left"], 1)
        self.assertFalse(tea["scalding"])

        # Two people on one page: distinct attention, no comprehension score.
        code, body = self._play_mutate(
            {
                "action": "share_distinct_annotation",
                "material_title": "夜航西飞",
                "page": 88,
                "noticed_detail_cn": "她注意到页脚有一道很淡的铅笔线，像谁犹豫过",
            },
            actor="subject",
            now_iso=DAY1_1520,
        )
        self.assertEqual(code, 200, body)
        code, body = self._play_mutate(
            {
                "action": "share_distinct_annotation",
                "material_title": "夜航西飞",
                "page": 88,
                "noticed_detail_cn": "他记住的是同一段里那个没说完的停顿",
            },
            actor="partner",
            now_iso=DAY1_1520,
        )
        self.assertEqual(code, 200, body)
        data = self._play(DAY1_1520, subject=False)
        self.assertEqual(len(data["distinct_annotations"]), 2)
        self.assertEqual(len(data["distinct_annotation_pairs"]), 1)
        pair = data["distinct_annotation_pairs"][0]
        self.assertEqual(pair["page"], 88)
        self.assertIs(pair["comprehension_score_exists"], False)
        self.assertIs(pair["forced_divergence"], False)

        self._assert_budget(DAY1_1520)

    # ------------------------------------------------------------------
    # Day 2
    # ------------------------------------------------------------------
    def _day_2_rumor_confirmed_plant_and_private_corner(self) -> None:
        # Seed the real porch plant as parched-then-recoverable at dawn.
        state = self._nb_state()
        plant = state["potted_plant"]
        plant["hydration"] = 5.0
        plant["stage"] = "wilting_recoverable"
        plant["segment_start_hydration"] = 5.0
        plant["segment_start_growth"] = float(plant.get("growth_points") or 0.0)
        plant["segment_started_at"] = DAY2_0830
        plant["last_anchor_at"] = DAY2_0830
        plant["seasonal_state"] = "resting_dry"
        neighborhood.save_state_atomic(self.root, state)

        # The daily scan offers a gentle watering candidate.
        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=DAY2_0900)
        self.assertEqual(collected["llm_calls"], 0)
        water_rows = [
            desc
            for desc in collected["descriptors"].values()
            if desc.get("action_family") == "care_porch_plant"
        ]
        self.assertTrue(water_rows, collected["descriptors"])
        self.assertEqual(water_rows[0].get("v04_action"), "water_or_move_plant")
        self.assertEqual(
            (water_rows[0].get("v04_payload") or {}).get("operation"), "water"
        )

        code, _ = self._nb_mutate(
            {
                "action": "water_or_move_plant",
                "operation": "water",
                "water_amount": 80,
            },
            actor="subject",
            now_iso=DAY2_0905,
        )
        self.assertEqual(code, 200)
        plant_view = self._nb(DAY2_0905)["potted_plant"]
        self.assertGreaterEqual(plant_view["wilt_recovered_count"], 1)
        self.assertGreater(plant_view["hydration"], 50.0)

        # A private corner name on home_desk never reaches the partner.
        receipt = self._matter_act(
            {
                "intent_cn": "给书桌起个只有自己叫的小名",
                "steps": [
                    {
                        "op": "NAME_PERSONAL_CORNER",
                        "zone_id": "home_desk",
                        "nickname_cn": PRIVATE_NICKNAME,
                        "shared_with_partner": False,
                    }
                ],
            },
            now_iso=DAY2_0910,
        )
        self.assertEqual(receipt["status"], "applied")
        owner_view = self._matter_view(DAY2_0910, actor="subject")
        owner_names = {
            row["zone_id"]: row for row in owner_view["zone_nicknames"]
        }
        self.assertEqual(owner_names["home_desk"]["nickname_cn"], PRIVATE_NICKNAME)
        self.assertIs(owner_names["home_desk"]["shared_with_partner"], False)

        before = self._snap()
        partner_view = self._matter_view(DAY2_0910, actor="partner")
        self.assertEqual(self._snap(), before, "partner matter read must be zero-write")
        self.assertEqual(partner_view["zone_nicknames"], [])
        self.assertNotIn(
            PRIVATE_NICKNAME, json.dumps(partner_view, ensure_ascii=False)
        )

        # The rumor becomes firsthand only at the real route entrance.
        code, _ = self._nb_mutate(
            {
                "action": "set_roadblock",
                "route_id": RUMOR_ROUTE,
                "blocked": True,
                "reason": "院门到街角的近路临时围挡",
            },
            actor="partner",
            now_iso=DAY2_0915,
        )
        self.assertEqual(code, 200)
        code, _ = self._nb_mutate(
            {"action": "start_journey", "target_place": "street_corner"},
            actor="subject",
            now_iso=DAY2_0920,
        )
        self.assertEqual(code, 200)
        code, _ = self._nb_mutate(
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor="subject",
            now_iso=DAY2_0925,
        )
        self.assertEqual(code, 200)
        state = self._nb_state()
        self.assertEqual(state["journey"]["halt_reason"], "ROADBLOCK_ENCOUNTERED")
        self.assertEqual(state["journey"]["current_place_id"], "yard")
        self.assertIn(RUMOR_ROUTE, state["journey"]["discovered_roadblocks"])
        rumor = state["public_life"]["bookstore"]["shopkeeper_life"]["heard_rumors"][0]
        self.assertIs(rumor["verified_firsthand"], True)
        self.assertIn("围挡", rumor["verification_note_cn"])

        # Detour under the sheltered eaves walkway and arrive honestly.
        code, rerouted = self._nb_mutate(
            {
                "action": "advance_or_turnback_journey",
                "operation": "reroute",
                "preferred_branch": "eaves_walkway",
            },
            actor="subject",
            now_iso=DAY2_0927,
        )
        self.assertEqual(code, 200, rerouted)
        self.assertIn("eaves_walkway", rerouted["result"]["planned_route_ids"])
        code, _ = self._nb_mutate(
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor="subject",
            now_iso=DAY2_0940,
        )
        self.assertEqual(code, 200)
        state = self._nb_state()
        self.assertEqual(state["journey"]["state"], "arrived")
        self.assertEqual(state["journey"]["current_place_id"], "street_corner")

        # Walk back home under the eaves so later days start at home.
        code, _ = self._nb_mutate(
            {
                "action": "start_journey",
                "target_place": "home",
                "preferred_branch": "eaves_walkway",
            },
            actor="subject",
            now_iso=DAY2_0945,
        )
        self.assertEqual(code, 200)
        code, _ = self._nb_mutate(
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor="subject",
            now_iso=DAY2_0955,
        )
        self.assertEqual(code, 200)
        self.assertEqual(self._nb_state()["journey"]["current_place_id"], "home")

        self._assert_budget(DAY2_0955)

    # ------------------------------------------------------------------
    # Day 3
    # ------------------------------------------------------------------
    def _day_3_kept_divergence_and_fictional_game(self) -> None:
        code, body = self._play_mutate(
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "propose",
                "experiment_id": "exp-night-capstone",
                "title": "夜色开头要不要更直接",
                "question": "不读后文，能不能看出是谁在说话？",
                "original_snippet": "那年的夜车，她一直没上。",
                "candidate_snippet": "那年夜里，她走到站台，又折回来。",
                "target_work_ref": DIVERGENCE_WORK_REF,
            },
            actor="subject",
            now_iso=DAY3_0900,
        )
        self.assertEqual(code, 200, body)
        code, body = self._play_mutate(
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "pause_keep_both",
                "experiment_id": "exp-night-capstone",
                "target_work_ref": DIVERGENCE_WORK_REF,
                "divergence_memo_cn": DIVERGENCE_MEMO,
            },
            actor="subject",
            now_iso=DAY3_0905,
        )
        self.assertEqual(code, 200, body)
        data = self._play(DAY3_0905)
        memo_row = next(
            row
            for row in data["experiments"]
            if row["experiment_id"] == "exp-night-capstone"
        )
        self.assertEqual(memo_row["status"], "kept_both_unreplaced")
        self.assertIs(memo_row["nag_daily"], False)
        self.assertEqual(memo_row["affection_penalty"], 0)
        self.assertEqual(memo_row["reopen_trigger_work_ref"], DIVERGENCE_WORK_REF)

        # Ordinary context is silent; only naming the exact work wakes the memo.
        ordinary = lcb._format_v04_neighborhood_and_elastic(
            v04_nb=None,
            v04_ep=data,
            incoming_text="今天院子里的风很轻",
            include_private=True,
        )
        self.assertNotIn("保留分歧", ordinary or "")
        self.assertNotIn(DIVERGENCE_MEMO, ordinary or "")
        named = lcb._format_v04_neighborhood_and_elastic(
            v04_nb=None,
            v04_ep=data,
            incoming_text="想再翻翻 artifact:night_capstone_draft 那段",
            include_private=True,
        )
        self.assertIn("保留分歧", named or "")
        self.assertIn(DIVERGENCE_MEMO, named or "")

        # A temporary game uses a real prop label but mutates nothing physical.
        matter_before = (self.root / "world" / "v08_matter_space.json").read_bytes()
        cup_before = _row_by_id(
            self._matter_view(DAY3_0910)["objects"], "ceramic_cup_01"
        )
        code, body = self._play_mutate(
            {
                "action": "play_temporary_game",
                "game_op": "start",
                "game_kind": "object_logbook",
                "prop_label": "白瓷杯",
                "rule_summary": "给杯子写一段夜航日志",
                "fictional_prop_object_id": "ceramic_cup_01",
                "fictional_role_cn": "桌面飞船",
            },
            actor="partner",
            now_iso=DAY3_0910,
        )
        self.assertEqual(code, 200, body)
        game = body["data"]["temporary_game"]
        self.assertTrue(game["active"])
        self.assertEqual(game["fictional_role_cn"], "桌面飞船")
        self.assertIs(game["fantasy_scope_isolated"], True)
        self.assertIs(game["world_object_mutated"], False)
        self.assertIs(game["world_travel_fabricated"], False)

        code, body = self._play_mutate(
            {
                "action": "play_temporary_game",
                "game_op": "play_turn",
                "turn_text": "飞船穿过桌面星云，杯子还稳稳放在桌角",
            },
            actor="subject",
            now_iso=DAY3_0915,
        )
        self.assertEqual(code, 200, body)
        code, body = self._play_mutate(
            {"action": "play_temporary_game", "game_op": "stop"},
            actor="partner",
            now_iso=DAY3_0920,
        )
        self.assertEqual(code, 200, body)
        self.assertIs(body["data"]["temporary_game"]["active"], False)
        self.assertEqual(
            (self.root / "world" / "v08_matter_space.json").read_bytes(),
            matter_before,
            "fiction must never touch the real matter store",
        )
        cup_after = _row_by_id(
            self._matter_view(DAY3_0920)["objects"], "ceramic_cup_01"
        )
        self.assertEqual(cup_after["parent_id"], cup_before["parent_id"])
        self.assertEqual(cup_after["portions_left"], cup_before["portions_left"])

        self._assert_budget(DAY3_0925)

    # ------------------------------------------------------------------
    # Day 4
    # ------------------------------------------------------------------
    def _day_4_missed_market_residual_traces_and_surprise(self) -> None:
        code, _ = self._nb_mutate(
            {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
            actor="partner",
            now_iso=DAY4_1209,
        )
        self.assertEqual(code, 200)

        # Noon on the 12th: home, not the market -- attendance stays false.
        noon = self._nb(DAY4_1212)
        self.assertEqual(noon["public_life"]["seasonal_festival"]["phase"], "active")
        self.assertIs(
            noon["public_life"]["seasonal_festival"]["attended_firsthand"], False
        )
        self.assertEqual(self._nb_state()["public_life"]["attended_festivals"], [])

        # Prepare a surprise; the partner cannot see it before it is offered.
        code, _ = self._play_mutate(
            {
                "action": "prepare_or_offer_surprise",
                "stage": "prepare",
                "surprise_id": "surp-night-ginkgo",
                "kind": "pressed_leaf_card",
                "title": SURPRISE_TITLE,
                "content": SURPRISE_CONTENT,
            },
            actor="subject",
            now_iso=DAY4_1310,
        )
        self.assertEqual(code, 200)
        partner_data = self._play(DAY4_1310, subject=False)
        self.assertEqual(partner_data["prepared_surprises"], [])
        partner_dump = json.dumps(partner_data, ensure_ascii=False)
        self.assertNotIn(SURPRISE_TITLE, partner_dump)
        self.assertNotIn("surp-night-ginkgo", partner_dump)

        # The 13th visit only finds physical residual traces.
        code, _ = self._nb_mutate(
            {
                "action": "start_journey",
                "target_place": "street_corner",
                "preferred_branch": "eaves_walkway",
            },
            actor="subject",
            now_iso=DAY4_1311,
        )
        self.assertEqual(code, 200)
        code, _ = self._nb_mutate(
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor="subject",
            now_iso=DAY4_1312,
        )
        self.assertEqual(code, 200)
        late = self._nb(DAY4_1313)
        festival = late["public_life"]["seasonal_festival"]
        self.assertEqual(festival["phase"], "residual_traces")
        self.assertIs(festival["attended_firsthand"], False)
        traces = "".join(festival["residual_traces"])
        self.assertIn("纸灯笼串", traces)
        self.assertIn("回收箱", traces)
        self.assertEqual(self._nb_state()["public_life"]["attended_festivals"], [])

        # Offer it, then let the partner open it; no spoiler before the offer.
        code, _ = self._play_mutate(
            {
                "action": "prepare_or_offer_surprise",
                "stage": "offer",
                "surprise_id": "surp-night-ginkgo",
            },
            actor="subject",
            now_iso=DAY4_1315,
        )
        self.assertEqual(code, 200)
        before = self._snap()
        readiness = ep_engine.project_surprise_readiness_zero_write(self.root)
        self.assertEqual(self._snap(), before, "readiness scan must be zero-write")
        self.assertIs(readiness["has_offered_unopened"], True)
        self.assertIs(readiness["reveals_content"], False)
        self.assertNotIn(SURPRISE_TITLE, json.dumps(readiness, ensure_ascii=False))

        offered_partner = self._play(DAY4_1315, subject=False)
        offered = offered_partner["prepared_surprises"][0]
        self.assertEqual(offered["status"], "offered")
        self.assertIs(offered["content_withheld_until_reveal"], True)
        self.assertNotIn(SURPRISE_TITLE, json.dumps(offered_partner, ensure_ascii=False))

        code, _ = self._play_mutate(
            {
                "action": "respond_prepared_surprise",
                "surprise_id": "surp-night-ginkgo",
                "decision": "reveal",
            },
            actor="partner",
            now_iso=DAY4_1320,
        )
        self.assertEqual(code, 200)
        revealed_partner = self._play(DAY4_1320, subject=False)
        revealed = revealed_partner["prepared_surprises"][0]
        self.assertEqual(revealed["status"], "revealed")
        self.assertEqual(revealed["title"], SURPRISE_TITLE)
        self.assertEqual(revealed["content"], SURPRISE_CONTENT)
        readiness = ep_engine.project_surprise_readiness_zero_write(self.root)
        self.assertIs(readiness["has_offered_unopened"], False)

        # Return home under the eaves for the rest of the week.
        code, _ = self._nb_mutate(
            {
                "action": "start_journey",
                "target_place": "home",
                "preferred_branch": "eaves_walkway",
            },
            actor="subject",
            now_iso=DAY4_1330,
        )
        self.assertEqual(code, 200)
        code, _ = self._nb_mutate(
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            actor="subject",
            now_iso=DAY4_1340,
        )
        self.assertEqual(code, 200)
        self.assertEqual(self._nb_state()["journey"]["current_place_id"], "home")

        self._assert_budget(DAY4_1340)

    # ------------------------------------------------------------------
    # Day 5
    # ------------------------------------------------------------------
    def _day_5_winter_warmth_and_personal_ritual(self) -> None:
        code, _ = self._nb_mutate(
            {"action": "set_local_weather", "weather": "clear", "season": "winter"},
            actor="partner",
            now_iso=DAY5_0900,
        )
        self.assertEqual(code, 200)
        code, _ = self._nb_mutate(
            {
                "action": "water_or_move_plant",
                "operation": "water",
                "water_amount": 80,
            },
            actor="subject",
            now_iso=DAY5_0905,
        )
        self.assertEqual(code, 200)

        # Winter evening: the open yard is cold and the window seat is warmer.
        before = self._snap()
        conditions = micro.resolve_local_conditions_zero_write(
            self.root, now_iso=DAY5_2000
        )
        self.assertEqual(self._snap(), before, "micro-zone read must be zero-write")
        self.assertEqual(conditions["llm_calls"], 0)
        self.assertEqual(conditions["season"], "winter")
        self.assertIs(conditions["is_night"], True)
        self.assertEqual(conditions["seasonal_hint_cn"], "冬日院风微寒，窗边或柔灯下更舒服")
        spots = {row["spot_id"]: row for row in conditions["spots"]}
        self.assertIs(spots["open_yard"]["seasonal_chill"], True)
        self.assertIs(spots["window_side"]["recommended_seat"], True)

        evening = self._nb(DAY5_2000)
        self.assertEqual(evening["potted_plant"]["seasonal_state"], "winter_dormant")
        self.assertGreater(evening["potted_plant"]["hydration"], 25.0)

        # A private small habit evolves, then fades into a real retained technique.
        code, body = echoes_view.mutate_v08_emergent_echoes_view(
            self.root,
            {
                "action": "record_craft_genealogy_step",
                "problem_pattern_cn": "夜里换下的衣服堆在椅子上，第二天找起来乱",
                "operation_cn": "睡前先挑好第二天的衣服搭在椅背",
                "domain_tag": "home_sorting",
                "outcome": "worked",
                "mismatch_boundary_cn": "太累的晚上不勉强，直接睡",
            },
            actor_side="subject",
            now_iso=DAY5_2005,
        )
        self.assertEqual(code, 200, body)
        genealogy_id = self._echoes(DAY5_2005)["craft_genealogy"][0]["genealogy_id"]

        code, body = echoes_view.mutate_v08_emergent_echoes_view(
            self.root,
            {
                "action": "curate_personal_ritual",
                "ritual_op": "sketch_custom",
                "title_cn": PRIVATE_RITUAL_TITLE,
                "origin_trace_cn": "连着几天早上赶时间，前一晚顺手搭好就从容多了",
                "context_scope_cn": "晚上在家准备睡觉时",
                "visibility": "private",
            },
            actor_side="subject",
            now_iso=DAY5_2005,
        )
        self.assertEqual(code, 200, body)
        sketch = self._echoes(DAY5_2005)["personal_rituals"][0]
        ritual_id = sketch["ritual_id"]
        self.assertEqual(sketch["status"], "provisional_custom")
        self.assertIs(sketch["auto_declared_by_count"], False)
        self.assertEqual(sketch["omission_debt"], 0)

        code, body = echoes_view.mutate_v08_emergent_echoes_view(
            self.root,
            {
                "action": "curate_personal_ritual",
                "ritual_op": "reuse_variant",
                "ritual_id": ritual_id,
                "new_title_cn": "睡前把明天的衣服搭好",
                "variant_note_cn": "换成出门要穿的整套先摆好，早上更顺",
                "context_scope_cn": "第二天要早起的晚上",
            },
            actor_side="subject",
            now_iso=DAY5_2010,
        )
        self.assertEqual(code, 200, body)
        reused = self._echoes(DAY5_2010)["personal_rituals"][0]
        self.assertEqual(reused["status"], "reused_variant")
        self.assertIs(reused["auto_declared_by_count"], False)
        self.assertEqual(reused["omission_debt"], 0)

        code, body = echoes_view.mutate_v08_emergent_echoes_view(
            self.root,
            {
                "action": "curate_personal_ritual",
                "ritual_op": "fade_or_omit",
                "ritual_id": ritual_id,
                "linked_genealogy_id": genealogy_id,
            },
            actor_side="subject",
            now_iso=DAY5_2015,
        )
        self.assertEqual(code, 200, body)
        faded = self._echoes(DAY5_2015)["personal_rituals"][0]
        self.assertEqual(faded["status"], "technique_retained")
        self.assertEqual(faded["linked_genealogy_id"], genealogy_id)
        self.assertEqual(faded["omission_debt"], 0)
        self.assertIs(faded["auto_declared_by_count"], False)

        before = self._snap()
        partner_echoes = self._echoes(DAY5_2020, subject=False)
        self.assertEqual(self._snap(), before, "echoes reads must be zero-write")
        self.assertEqual(partner_echoes["personal_rituals"], [])
        self.assertNotIn(
            PRIVATE_RITUAL_TITLE, json.dumps(partner_echoes, ensure_ascii=False)
        )

        self._assert_budget(DAY5_2020)

    # ------------------------------------------------------------------
    # Day 6
    # ------------------------------------------------------------------
    def _day_6_source_gone_and_honest_bookmark(self) -> None:
        self._matter_act(
            {
                "intent_cn": "把海边捡的贝壳放上书架",
                "steps": [
                    {
                        "op": "INTRODUCE_ITEM",
                        "object_id": "sea_shell_01",
                        "label_cn": "海边捡的贝壳",
                        "item_kind": "keepsake",
                        "target_parent_id": "home_staging_corner",
                    }
                ],
            },
            now_iso=DAY6_1000,
        )
        note = et.apply_emotional_tension_action(
            self.root,
            {
                "action": "add_missing_note",
                "kind": "FOUND",
                "source_ref": "matter:sea_shell_01",
                "summary": "捡到一个有细纹的贝壳",
            },
            now=datetime.fromisoformat(DAY6_1005),
            subject_view=True,
        )
        self.assertEqual(note["status"], "applied")

        before = self._snap()
        live = et.scan_emotional_tension_cues_zero_write(
            self.root, now=datetime.fromisoformat(DAY6_1007)
        )
        self.assertEqual(self._snap(), before, "tension scan must be zero-write")
        self.assertEqual(live["unsurfaced_missing_notes_count"], 1)

        # The real object is consumed; the note must let go and refuse new refs.
        self._matter_act(
            {
                "intent_cn": "贝壳碎了，扔掉",
                "steps": [
                    {
                        "op": "DISCARD_ITEM",
                        "object_id": "sea_shell_01",
                        "reason_cn": "摔碎了一角",
                    }
                ],
            },
            now_iso=DAY6_1010,
        )
        state_path = et.emotional_tension_state_path(self.root)
        state_bytes = state_path.read_bytes()
        before = self._snap()
        released = et.read_emotional_tension(
            self.root, now=datetime.fromisoformat(DAY6_1012), subject_view=True
        )
        self.assertEqual(released["missing_partner"]["kept_notes"], [])
        scanned = et.scan_emotional_tension_cues_zero_write(
            self.root, now=datetime.fromisoformat(DAY6_1014)
        )
        self.assertEqual(scanned["unsurfaced_missing_notes_count"], 0)
        collected = bridge.collect_v02_v03_loop_candidates(
            self.root, now_iso=DAY6_1015
        )
        summary = collected["gating"]["emotional_tension_summary"]
        self.assertEqual(summary["unsurfaced_missing_notes_count"], 0)
        self.assertIsNone(summary["open_favor"])
        for desc in (collected.get("descriptors") or {}).values():
            self.assertFalse(
                str(desc.get("source_ref") or "").startswith("matter:"),
                desc.get("source_ref"),
            )
        self.assertEqual(self._snap(), before, "release reads must be zero-write")
        self.assertEqual(state_path.read_bytes(), state_bytes)

        with self.assertRaises(et.EmotionalTensionConflictError) as ctx:
            et.apply_emotional_tension_action(
                self.root,
                {
                    "action": "add_missing_note",
                    "kind": "FOUND",
                    "source_ref": "matter:sea_shell_01",
                    "summary": "还想再记一次",
                },
                now=datetime.fromisoformat(DAY6_1013),
                subject_view=True,
            )
        self.assertEqual(ctx.exception.reason_code, "SOURCE_GONE")
        self.assertEqual(ctx.exception.status_code, 409)

        # An honest bookmark: page 37, two pages back, never claims understanding.
        self._matter_act(
            {
                "intent_cn": "把旧小说放到手边",
                "steps": [
                    {
                        "op": "INTRODUCE_ITEM",
                        "object_id": "night_book_01",
                        "label_cn": "旧小说",
                        "item_kind": "paper",
                        "target_parent_id": "home_staging_corner",
                        "paper_like": True,
                    }
                ],
            },
            now_iso=DAY6_1016,
        )
        receipt = self._matter_act(
            {
                "intent_cn": "夹上书签，回退两页重看",
                "steps": [
                    {
                        "op": "NOTE_ASSOCIATION",
                        "target_id": "night_book_01",
                        "text": "读到夜雨那章",
                        "bookmark_page": 37,
                        "revisit_offset": -2,
                    }
                ],
            },
            now_iso=DAY6_1017,
        )
        mark = receipt["step_results"][0]["reading_mark"]
        self.assertEqual(mark["bookmark_page"], 37)
        self.assertEqual(mark["revisit_offset"], -2)
        self.assertIs(mark["understood"], False)

        before = self._snap()
        subject_book = _row_by_id(
            self._matter_view(DAY6_1018, actor="subject")["objects"], "night_book_01"
        )
        partner_book = _row_by_id(
            self._matter_view(DAY6_1018, actor="partner")["objects"], "night_book_01"
        )
        self.assertEqual(self._snap(), before, "bookmark reads must be zero-write")
        self.assertEqual(subject_book["reading_mark"]["bookmark_page"], 37)
        self.assertIs(subject_book["reading_mark"]["understood"], False)
        self.assertIs(partner_book["reading_mark"]["understood"], False)

        self._assert_budget(DAY6_1018)

    # ------------------------------------------------------------------
    # Day 7
    # ------------------------------------------------------------------
    def _day_7_spring_leaf_cold_restart_and_closeout(self) -> None:
        code, _ = self._nb_mutate(
            {"action": "set_local_weather", "weather": "clear", "season": "spring"},
            actor="partner",
            now_iso=DAY7_0000,
        )
        self.assertEqual(code, 200)
        leaves_before = self._nb(DAY7_0002)["potted_plant"]["leaf_count"]
        code, _ = self._nb_mutate(
            {
                "action": "water_or_move_plant",
                "operation": "water",
                "water_amount": 80,
            },
            actor="subject",
            now_iso=DAY7_0005,
        )
        self.assertEqual(code, 200)

        # Spring water + light cross the next new-leaf milestone.
        advanced = neighborhood.advance_v04_neighborhood_processes(
            self.root, now_iso=DAY7_2355
        )
        self.assertEqual(advanced["llm_calls"], 0)
        plant = self._nb(DAY7_2359)["potted_plant"]
        self.assertGreater(plant["leaf_count"], leaves_before)
        self.assertTrue(plant["last_new_leaf_at"])
        self.assertIn("新绿叶", plant["new_leaf_note_cn"])

        # Cold restart: twenty read calls in a row touch zero bytes.
        self.assertTrue(neighborhood.load_state_readonly(self.root)[1])
        reads: list[Callable[[], Any]] = [
            lambda: self._nb(DAY7_2359, subject=True),
            lambda: self._nb(DAY7_2359, subject=False),
            lambda: self._matter_view(DAY7_2359, actor="subject"),
            lambda: self._matter_view(DAY7_2359, actor="partner"),
            lambda: self._play(DAY7_2359, subject=True),
            lambda: self._play(DAY7_2359, subject=False),
            lambda: self._echoes(DAY7_2359, subject=True),
            lambda: self._echoes(DAY7_2359, subject=False),
            lambda: et.read_emotional_tension(
                self.root, now=datetime.fromisoformat(DAY7_2359), subject_view=True
            ),
            lambda: et.scan_emotional_tension_cues_zero_write(
                self.root, now=datetime.fromisoformat(DAY7_2359)
            ),
            lambda: micro.resolve_local_conditions_zero_write(
                self.root, now_iso=DAY7_2359
            ),
            lambda: bridge.collect_v02_v03_loop_candidates(
                self.root, now_iso=DAY7_2359
            ),
            lambda: ep_engine.project_surprise_readiness_zero_write(self.root),
            lambda: lcb.render_lived_companion_context(
                self.root, {"phone": {"enabled": False}}, now_iso=DAY7_2359
            ),
            lambda: lcb.measure_context_budget_breakdown(
                self.root, now_iso=DAY7_2359
            ),
            lambda: self._nb(DAY7_2355, subject=True),
            lambda: self._matter_view(DAY7_2355, actor="subject"),
            lambda: self._play(DAY7_2355, subject=False),
            lambda: self._echoes(DAY7_2355, subject=True),
            lambda: self._assert_budget(DAY7_2359),
        ]
        self.assertEqual(len(reads), 20)
        before = self._snap()
        for read in reads:
            read()
        self.assertEqual(
            self._snap(), before, "twenty cold reads must be strictly zero-write"
        )

        budget = self._assert_budget(DAY7_2359)
        self.assertIs(budget["within_budget"], True)
        self.assertLessEqual(budget["total_chars"], 1800)

        # Frozen anchors: content unchanged (byte-identical to start and known good).
        for rel, digest in self._anchor_hashes.items():
            self.assertEqual(_sha256(ROOT / rel), digest, f"anchor rewritten: {rel}")
            self._assert_anchor_intact(rel)
        for rel in FROZEN_ANCHORS:
            if rel not in self._anchor_hashes:
                self.assertFalse((ROOT / rel).exists(), f"unexpected anchor appeared: {rel}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

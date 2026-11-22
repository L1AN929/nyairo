#!/usr/bin/env python3
"""v0.8 Wave J1: Ch.26 living shopkeeper, secondhand rumors & measured card-spot help.

Covers ``扩充草案 26-他人生活与有分寸的交往`` §一–§七 over the real owners:

* the shopkeeper keeps a current task that rotates purely deterministically by
  clock (``llm_calls == 0``), a bounded memory of concrete public counter
  events, and a hard ``public_counter_only`` privilege boundary with zero
  private-room knowledge and zero private draft leakage;
* a secondhand rumor is recorded with its source and ``verified_firsthand =
  False`` and NEVER leaks into ``journey.discovered_roadblocks`` or
  ``world_truth.roadblocks`` -- only standing at the route entrance flips it,
  either as a confirmed roadblock or as a refuted false alarm;
* a suggested card spot resolves honestly after a cold restart / 24h settlement
  (deterministic hash picks ``adopted`` or ``relocated``) with a grounded
  physical reason and zero fabricated customer praise, both via auto-settle and
  via explicit ``decide_card_spot``;
* projections stay 100% zero-write, unknown fields are rejected with
  ``400 INVALID_FIELDS``, and ``partner`` calling ``bookstore_service`` still
  gets ``403 FORBIDDEN_ACTOR_SIDE`` while ``set_roadblock`` stays allowed.

Run (native Python)::

    python -B -m unittest tests.test_v08_ch26_living_shopkeeper -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(REPO_ROOT / "components"), str(_WORLD_SCRIPTS), str(REPO_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v04_neighborhood_living as engine  # noqa: E402

T_OPEN = "2026-11-16T11:00:00+08:00"
T_ROTATED = "2026-11-16T15:00:00+08:00"
ROUTE = "street_to_riverside"
DRAFT_TEXT = "私人练习废稿：这句不要贴在店里"


def _tree_snapshot(root: Path) -> list[tuple[str, int, int]]:
    rows: list[tuple[str, int, int]] = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        rows.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    return rows


class Ch26LivingShopkeeperTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="v08ch26-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    # ---------------------------------------------------------------- helpers
    def _mutate(self, payload: dict, *, actor: str = "subject", now_iso: str = T_OPEN):
        return mutate_v04_neighborhood_view(
            self.root, payload, actor_side=actor, now_iso=now_iso
        )

    def _raw_state(self) -> dict:
        state, exists = engine.load_state_readonly(self.root)
        self.assertTrue(exists)
        return state

    def _life(self) -> dict:
        return dict(self._raw_state()["public_life"]["bookstore"]["shopkeeper_life"])

    def _make_inscribed_card(self, card_id: str = "card_spot", *, now_iso: str = T_OPEN) -> None:
        code, _ = self._mutate(
            {"action": "craft_paper_or_box", "operation": "cut_card", "card_id": card_id},
            now_iso=now_iso,
        )
        self.assertEqual(code, 200)
        code, _ = self._mutate(
            {
                "action": "craft_paper_or_box",
                "operation": "inscribe_card",
                "card_id": card_id,
                "title": "画册荐书签",
                "text": "第三章关于石阶青苔的配图很耐看。",
                "draft_text": DRAFT_TEXT,
            },
            now_iso=now_iso,
        )
        self.assertEqual(code, 200)

    # ------------------------------------------------- 1. living shopkeeper
    def test_01_shopkeeper_task_rotates_deterministically_with_hard_boundary(self) -> None:
        before = _tree_snapshot(self.root)
        code, view = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T_OPEN
        )
        self.assertEqual(code, 200)
        after = _tree_snapshot(self.root)
        self.assertEqual(before, after, "projection must not create or touch files")

        life = view["bookstore"]["shopkeeper_life"]
        self.assertEqual(life["shopkeeper_name"], engine.SHOPKEEPER_NAME)
        self.assertEqual(life["privilege_boundary"], "public_counter_only")
        self.assertIs(life["private_room_knowledge"], False)
        self.assertIs(life["private_drafts_exposed"], False)
        self.assertIs(life["warehouse_access"], False)
        self.assertIs(life["inventory_rewrite_authority"], False)
        self.assertIn(life["current_task"], [k for k, _ in engine.SHOPKEEPER_TASKS])
        self.assertTrue(life["current_task_cn"])
        self.assertLessEqual(len(life["ambient_changes"]), engine.MAX_SHOPKEEPER_AMBIENT_CHANGES)
        first_task = life["current_task"]

        _, later = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T_ROTATED
        )
        later_life = later["bookstore"]["shopkeeper_life"]
        self.assertNotEqual(first_task, later_life["current_task"])
        # Pure wall-clock function, identical across repeated calls.
        _, again = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T_ROTATED
        )
        self.assertEqual(
            later_life["current_task"], again["bookstore"]["shopkeeper_life"]["current_task"]
        )
        self.assertEqual(later["llm_calls"], 0)

        # Private drafts never leak through any perspective or the substrate.
        self._make_inscribed_card()
        _, partner = read_v04_neighborhood_view(
            self.root, has_subject_capability=False, now_iso=T_OPEN
        )
        self.assertNotIn(DRAFT_TEXT, json.dumps(partner, ensure_ascii=False))
        self.assertIsNone(partner["craft_bench"]["cards_by_id"]["card_spot"]["draft_text"])
        self.assertIs(partner["bookstore"]["shopkeeper_life"]["private_room_knowledge"], False)

    # --------------------------------------------- 2. secondhand rumors
    def test_02_rumor_is_attributed_and_not_omniscient_until_entrance(self) -> None:
        code, result = self._mutate(
            {
                "action": "bookstore_service",
                "service": "share_shopkeeper_rumor",
                "route_id": ROUTE,
            }
        )
        self.assertEqual(code, 200)
        self.assertEqual(result["result"]["source_name"], engine.SHOPKEEPER_NAME)
        self.assertIs(result["result"]["verified_firsthand"], False)

        state = self._raw_state()
        self.assertEqual(state["journey"]["discovered_roadblocks"], [])
        self.assertEqual(state["world_truth"]["roadblocks"], {})
        rumor = state["public_life"]["bookstore"]["shopkeeper_life"]["heard_rumors"][0]
        self.assertEqual(rumor["route_id"], ROUTE)
        self.assertIs(rumor["verified_firsthand"], False)
        self.assertEqual(rumor["claim_cn"], engine.DEFAULT_RUMOR_CLAIM_CN)

        # Hearsay alone must not light up the subject's roadblock knowledge.
        _, view = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso="2026-11-16T11:01:00+08:00"
        )
        self.assertEqual(view["roadblocks"], [])
        self.assertFalse(
            view["journey"]["routes_catalog"][ROUTE]["known_blocked_to_subject"]
        )

        # Walk there freely -> the false alarm is refuted firsthand.
        code, _ = self._mutate(
            {"action": "start_journey", "target_place": "riverside"},
            now_iso="2026-11-16T11:02:00+08:00",
        )
        self.assertEqual(code, 200)
        code, _ = self._mutate(
            {
                "action": "advance_or_turnback_journey",
                "operation": "advance",
            },
            now_iso="2026-11-16T12:00:00+08:00",
        )
        self.assertEqual(code, 200)
        state = self._raw_state()
        self.assertEqual(state["journey"]["state"], "arrived")
        self.assertEqual(state["journey"]["current_place_id"], "riverside")
        rumor = state["public_life"]["bookstore"]["shopkeeper_life"]["heard_rumors"][0]
        self.assertIs(rumor["verified_firsthand"], True)
        self.assertIn("是通的", rumor["refutation_note_cn"])
        self.assertEqual(state["journey"]["discovered_roadblocks"], [])

    def test_03_rumor_confirmed_when_real_roadblock_reached(self) -> None:
        code, _ = self._mutate(
            {
                "action": "bookstore_service",
                "service": "share_shopkeeper_rumor",
                "route_id": ROUTE,
                "claim_cn": "听说河边那条路在挖沟",
            }
        )
        self.assertEqual(code, 200)
        code, _ = self._mutate(
            {
                "action": "set_roadblock",
                "route_id": ROUTE,
                "blocked": True,
                "reason": "河边路段临时围挡",
            },
            actor="partner",
            now_iso="2026-11-16T11:05:00+08:00",
        )
        self.assertEqual(code, 200)

        code, _ = self._mutate(
            {"action": "start_journey", "target_place": "riverside"},
            now_iso="2026-11-16T11:10:00+08:00",
        )
        self.assertEqual(code, 200)
        # Before reaching the entrance, the rumor is still only hearsay.
        rumor = self._life()["heard_rumors"][0]
        self.assertIs(rumor["verified_firsthand"], False)

        code, _ = self._mutate(
            {"action": "advance_or_turnback_journey", "operation": "advance"},
            now_iso="2026-11-16T12:00:00+08:00",
        )
        self.assertEqual(code, 200)
        state = self._raw_state()
        self.assertEqual(state["journey"]["state"], "paused_at_node")
        self.assertEqual(state["journey"]["halt_reason"], "ROADBLOCK_ENCOUNTERED")
        self.assertIn(ROUTE, state["journey"]["discovered_roadblocks"])
        rumor = state["public_life"]["bookstore"]["shopkeeper_life"]["heard_rumors"][0]
        self.assertIs(rumor["verified_firsthand"], True)
        self.assertIn("围挡", rumor["verification_note_cn"])

    # --------------------------------------- 3. card spot help & outcome
    def test_04_card_spot_auto_resolves_after_24h_without_praise(self) -> None:
        self._make_inscribed_card()
        code, result = self._mutate(
            {
                "action": "bookstore_service",
                "service": "suggest_card_spot",
                "card_id": "card_spot",
                "spot_note": "靠窗第二格左边",
            }
        )
        self.assertEqual(code, 200)
        suggestion_id = result["result"]["suggestion_id"]
        self.assertEqual(result["result"]["status"], "pending_shopkeeper_review")
        life = self._life()
        self.assertEqual(life["card_spot_suggestions"][0]["status"], "pending_shopkeeper_review")
        kinds = [row["kind"] for row in life["remembered_interactions"]]
        self.assertIn("suggest_card_spot", kinds)
        self.assertFalse(any("夸" in row["note_cn"] or "赞" in row["note_cn"] for row in life["remembered_interactions"]))

        # Cold restart: state is re-read from disk; 24h later the shop decides.
        advance = engine.advance_v04_neighborhood_processes(
            self.root, now_iso="2026-11-17T11:30:00+08:00"
        )
        self.assertEqual(advance["llm_calls"], 0)
        self.assertEqual(advance["changes"]["card_spots_decided"], 1)

        life = self._life()
        resolved = life["card_spot_suggestions"][0]
        expected = (
            "adopted"
            if int(hashlib.sha256(suggestion_id.encode("utf-8")).hexdigest()[:8], 16) % 2 == 0
            else "relocated"
        )
        self.assertEqual(resolved["status"], expected)
        self.assertTrue(resolved["resolved_at"])
        self.assertTrue(resolved["outcome_reason_cn"])
        for banned in ("夸", "赞", "顾客", "好评"):
            self.assertNotIn(banned, resolved["outcome_reason_cn"])
        if expected == "adopted":
            self.assertIn("靠窗", resolved["outcome_reason_cn"])
        else:
            self.assertIn("书脊", resolved["outcome_reason_cn"])

        _, view = read_v04_neighborhood_view(
            self.root, has_subject_capability=False, now_iso="2026-11-17T11:35:00+08:00"
        )
        projected = view["bookstore"]["shopkeeper_life"]["card_spot_suggestions"][0]
        self.assertEqual(projected["status"], expected)
        self.assertTrue(view["bookstore"]["shopkeeper_life"]["ambient_changes"])
        self.assertNotIn(DRAFT_TEXT, json.dumps(view, ensure_ascii=False))

    def test_05_card_spot_explicit_decide_and_uninscribed_refusal(self) -> None:
        code, _ = self._mutate(
            {"action": "craft_paper_or_box", "operation": "cut_card", "card_id": "card_plain"}
        )
        self.assertEqual(code, 200)
        code, err = self._mutate(
            {
                "action": "bookstore_service",
                "service": "suggest_card_spot",
                "card_id": "card_plain",
                "spot_note": "柜台边",
            }
        )
        self.assertEqual(code, 409)
        self.assertEqual(err["reason_code"], "CARD_NOT_INSCRIBED")

        self._make_inscribed_card(card_id="card_fine")
        code, result = self._mutate(
            {
                "action": "bookstore_service",
                "service": "suggest_card_spot",
                "card_id": "card_fine",
                "spot_note": "靠窗第二格左边",
            }
        )
        self.assertEqual(code, 200)
        suggestion_id = result["result"]["suggestion_id"]

        code, decided = self._mutate(
            {
                "action": "bookstore_service",
                "service": "decide_card_spot",
                "suggestion_id": suggestion_id,
                "outcome": "relocated",
                "reason_cn": "靠窗那格下午反光，他挪去了柜台斜架",
            },
            now_iso="2026-11-16T11:30:00+08:00",
        )
        self.assertEqual(code, 200)
        self.assertEqual(decided["result"]["status"], "relocated")
        self.assertEqual(decided["result"]["outcome_reason_cn"], "靠窗那格下午反光，他挪去了柜台斜架")
        life = self._life()
        self.assertEqual(life["card_spot_suggestions"][0]["status"], "relocated")
        self.assertFalse(
            any("夸" in row["note_cn"] or "赞" in row["note_cn"] for row in life["remembered_interactions"])
        )

        # Repeat decide is an honest replay of the already-resolved outcome.
        code, replay = self._mutate(
            {
                "action": "bookstore_service",
                "service": "decide_card_spot",
                "suggestion_id": suggestion_id,
                "outcome": "adopted",
            },
            now_iso="2026-11-16T11:40:00+08:00",
        )
        self.assertEqual(code, 200)
        self.assertTrue(replay["result"]["replay"])
        self.assertEqual(replay["result"]["status"], "relocated")

    # --------------------------------- 4. zero-write & security invariants
    def test_06_zero_write_reads_and_whitelist_security(self) -> None:
        self._make_inscribed_card()
        code, _ = self._mutate(
            {
                "action": "bookstore_service",
                "service": "suggest_card_spot",
                "card_id": "card_spot",
                "spot_note": "靠窗第二格左边",
            }
        )
        self.assertEqual(code, 200)
        code, _ = self._mutate(
            {
                "action": "bookstore_service",
                "service": "share_shopkeeper_rumor",
                "route_id": ROUTE,
            },
            now_iso="2026-11-16T11:10:00+08:00",
        )
        self.assertEqual(code, 200)

        before = _tree_snapshot(self.root)
        engine.project_v04_neighborhood(self.root, has_subject_capability=True, now_iso=T_OPEN)
        engine.project_v04_neighborhood(self.root, has_subject_capability=False, now_iso=T_OPEN)
        read_v04_neighborhood_view(self.root, has_subject_capability=True, now_iso=T_OPEN)
        read_v04_neighborhood_view(self.root, has_subject_capability=False, now_iso=T_OPEN)
        self.assertEqual(before, _tree_snapshot(self.root), "reads must be 100% zero-write")

        # Unknown field -> 400 INVALID_FIELDS.
        code, err = self._mutate(
            {
                "action": "bookstore_service",
                "service": "share_shopkeeper_rumor",
                "route_id": ROUTE,
                "bogus_field": 1,
            }
        )
        self.assertEqual(code, 400)
        self.assertEqual(err["reason_code"], "INVALID_FIELDS")

        # Blank / unknown route -> 400.
        code, err = self._mutate(
            {
                "action": "bookstore_service",
                "service": "share_shopkeeper_rumor",
                "route_id": "not_a_route",
            }
        )
        self.assertEqual(code, 400)
        self.assertEqual(err["reason_code"], "INVALID_FIELDS")

        # Partner must never drive bookstore interactions...
        code, err = self._mutate(
            {
                "action": "bookstore_service",
                "service": "browse_book",
                "book_id": "book_botanical_plates",
            },
            actor="partner",
        )
        self.assertEqual(code, 403)
        self.assertEqual(err["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # ...but the existing partner roadblock authority is untouched.
        code, _ = self._mutate(
            {"action": "set_roadblock", "route_id": "open_shortcut", "blocked": True},
            actor="partner",
            now_iso="2026-11-16T11:20:00+08:00",
        )
        self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)

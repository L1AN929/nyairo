#!/usr/bin/env python3
"""v0.8 universal matter -> daily main loop (one generic open-act door).

Covers, against the real ``v02_v03_daily_loop_bridge`` and the real
``v08_universal_matter`` physics engine (0 product-model calls):

* an empty root collects with 100% zero writes and never creates
  ``world/v08_matter_space.json``;
* a raw ingredient in the fridge next to a free stove auto-produces a
  ``v08_matter:*`` candidate, cooks a real hot dish through a real receipt, and
  eats one of its portions on the next round;
* a ``used`` vessel on the dining table auto-produces a tidy/wash candidate that
  really places it in the sink and washes it;
* a partner moving a shared object auto-produces an observe candidate that
  refreshes ``subject_last_seen_parent`` (no knowledge teleporting);
* the V-A guards (unauthorized actor / missing receipt / FAILED / UNKNOWN) all
  refuse with zero optimistic writes;
* the same ``operation_key`` replayed never cooks or eats twice;
* the real ``AutonomousLifeResident`` hook settles a selected ``v08_matter:``
  candidate through the real consequence ledger.

Run (WSL): ``python -m unittest tests.test_v08_matter_daily_loop -v``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
from components.world.scripts import v08_universal_matter as engine  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
MATTER_SLICE = "world.v08_matter_space"


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


class _MatterDailyBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-matter-daily-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- seeding ---------------------------------------------------------
    def act(self, payload: dict, *, now: str = T0) -> dict:
        return engine.compose_open_act(self.root, payload, now_iso=now)

    def introduce(self, object_id: str, label: str, kind: str, target: str, **extra: Any) -> dict:
        step = {
            "op": "INTRODUCE_ITEM",
            "object_id": object_id,
            "label_cn": label,
            "item_kind": kind,
            "target_parent_id": target,
        }
        step.update(extra)
        return self.act({"intent_cn": f"放入{label}", "steps": [step]})

    def mark_used(self, object_id: str) -> None:
        state = engine._read_state_zero_write(self.root)
        state["matter_objects"][object_id]["cleanliness"] = "used"
        engine._atomic_write_state(self.root, state)

    # -- collection / event / settlement ---------------------------------
    def collect(self, *, now: str = T0) -> dict:
        return bridge.collect_v02_v03_loop_candidates(self.root, now_iso=now)

    @staticmethod
    def descriptors(collected: dict) -> list[dict]:
        return [
            dict(desc)
            for desc in (collected.get("descriptors") or {}).values()
            if isinstance(desc, dict)
        ]

    def matter_refs(self, collected: dict) -> list[str]:
        return [
            str(desc.get("source_ref") or "")
            for desc in self.descriptors(collected)
            if str(desc.get("source_ref") or "").startswith("v08_matter:")
        ]

    def descriptor(self, collected: dict, source_ref: str) -> dict:
        for desc in self.descriptors(collected):
            if desc.get("source_ref") == source_ref:
                return desc
        raise AssertionError(f"missing candidate {source_ref!r}")

    def event(self, collected: dict, source_ref: str, *, decision_id: str) -> dict:
        event = bridge.v02_v03_round_event_for_selected_candidate(
            collected, source_ref, decision_id=decision_id
        )
        self.assertIsInstance(event, dict, source_ref)
        return event

    def settle(self, event: dict, *, now: str = T0) -> dict:
        return bridge.settle_v02_v03_round_consequences(self.root, event, now_iso=now)

    def state(self) -> dict:
        return engine._read_state_zero_write(self.root)

    def foods(self) -> list[dict]:
        return [
            obj
            for obj in self.state()["matter_objects"].values()
            if str(obj.get("item_kind")) == "prepared_food"
        ]


class MatterDailyLoopTest(_MatterDailyBase):
    def test_01_empty_root_collect_is_zero_write_and_never_creates_matter_state(self) -> None:
        empty = Path(tempfile.mkdtemp(prefix="v08-matter-empty-"))
        try:
            before = _snapshot(empty)
            collected = bridge.collect_v02_v03_loop_candidates(empty, now_iso=T0)
            after = _snapshot(empty)
            self.assertEqual(after, before)
            self.assertFalse((empty / "world").exists())
            self.assertFalse(engine.state_path(empty).exists())
            refs = [
                str(desc.get("source_ref") or "")
                for desc in (collected.get("descriptors") or {}).values()
                if isinstance(desc, dict)
            ]
            self.assertFalse([ref for ref in refs if ref.startswith("v08_matter:")])
        finally:
            import shutil

            shutil.rmtree(empty, ignore_errors=True)

    def test_02_fridge_raw_ingredient_and_free_stove_cooks_then_eats_half(self) -> None:
        # A real raw ingredient in the fridge (cold ambient, so it is not a
        # room-temperature perishable) next to a free stove.
        self.introduce(
            "tomato_01", "番茄", "ingredient", "home_fridge",
            cook_state="raw", portions=1, shelf_life_hours=48,
        )
        collected = self.collect()
        self.assertIn("v08_matter:cook", self.matter_refs(collected))

        cook_event = self.event(collected, "v08_matter:cook", decision_id="matter-cook-1")
        self.assertEqual(cook_event["action_kind"], "OPEN_MATTER_ACT")
        self.assertFalse(cook_event["open_act_payload"].get("strenuous_outing"))
        cooked = self.settle(cook_event)
        self.assertEqual(cooked["status"], "APPLIED", cooked)
        self.assertIn(MATTER_SLICE, cooked["slices_updated"])
        self.assertEqual(cooked["llm_calls"], 0)

        foods = self.foods()
        self.assertEqual(len(foods), 1)
        dish = foods[0]
        self.assertEqual(dish["cook_state"], "cooked")
        self.assertEqual(dish["thermal"], "hot")
        self.assertEqual(dish["portions_left"], 2)

        # The next round offers eating that real cooked dish; one portion is eaten.
        collected_next = self.collect()
        eat_refs = [ref for ref in self.matter_refs(collected_next) if ref.startswith("v08_matter:eat:")]
        self.assertTrue(eat_refs, self.matter_refs(collected_next))
        eat_event = self.event(collected_next, eat_refs[0], decision_id="matter-eat-1")
        eaten = self.settle(eat_event)
        self.assertEqual(eaten["status"], "APPLIED", eaten)
        dish_after = self.foods()[0]
        self.assertEqual(dish_after["portions_left"], 1)
        self.assertFalse(dish_after["consumed"])

    def test_03_used_vessel_on_dining_table_is_washed(self) -> None:
        self.introduce("dirty_bowl_01", "用过的碗", "vessel", "dining_table")
        self.mark_used("dirty_bowl_01")
        collected = self.collect()
        self.assertIn("v08_matter:tidy_clean", self.matter_refs(collected))
        steps = self.descriptor(collected, "v08_matter:tidy_clean")["open_act_payload"]["steps"]
        self.assertTrue(any(step.get("op") == "CLEAN_SURFACE_OR_VESSEL" for step in steps))

        event = self.event(collected, "v08_matter:tidy_clean", decision_id="matter-tidy-1")
        settled = self.settle(event)
        self.assertEqual(settled["status"], "APPLIED", settled)
        bowl = self.state()["matter_objects"]["dirty_bowl_01"]
        self.assertEqual(bowl["cleanliness"], "clean")
        self.assertEqual(bowl["parent_id"], "kitchen_sink")

    def test_04_partner_moved_object_is_observed_and_seen_parent_refreshed(self) -> None:
        engine.partner_move_shared_object(
            self.root,
            {"object_id": "pen_01", "target_parent_id": "home_staging_corner"},
            now_iso=T0,
        )
        # Pre-miss the subject is not handed an oracle; a real reach that misses
        # is what earns the honest "go look" candidate.
        pre_miss = self.collect()
        self.assertFalse(
            [ref for ref in self.matter_refs(pre_miss) if ref.startswith("v08_matter:observe:")]
        )
        with self.assertRaises(engine.V08MatterError) as ctx:
            engine.compose_open_act(
                self.root,
                {"intent_cn": "顺手拿桌上的笔",
                 "steps": [{"op": "PLACE_OR_MOVE", "object_id": "pen_01",
                            "target_parent_id": "home_desk"}]},
                now_iso=T0,
            )
        self.assertEqual(ctx.exception.reason_code, "OBJECT_NOT_AT_EXPECTED_SPOT")
        collected = self.collect()
        observe_refs = [
            ref for ref in self.matter_refs(collected) if ref.startswith("v08_matter:observe:")
        ]
        self.assertTrue(observe_refs, self.matter_refs(collected))
        ref = observe_refs[0]
        event = self.event(collected, ref, decision_id="matter-observe-1")
        steps = event["open_act_payload"]["steps"]
        self.assertEqual(steps[0]["op"], "OBSERVE_OBJECT")

        settled = self.settle(event)
        self.assertEqual(settled["status"], "APPLIED", settled)
        pen = self.state()["matter_objects"]["pen_01"]
        self.assertEqual(pen["subject_last_seen_parent"], "home_staging_corner")
        self.assertFalse(pen["impression_stale"])

    def test_05_va_guards_refuse_with_zero_optimistic_writes(self) -> None:
        self.introduce("dirty_bowl_02", "用过的碗", "vessel", "dining_table")
        self.mark_used("dirty_bowl_02")
        collected = self.collect()
        base = self.event(collected, "v08_matter:tidy_clean", decision_id="matter-guard-1")

        before = _snapshot(self.root)

        unauthorized = copy.deepcopy(base)
        unauthorized["actor_side"] = "partner"
        res = self.settle(unauthorized)
        self.assertEqual(res["status"], "REFUSED")
        self.assertEqual(res["reason_code"], "UNAUTHORIZED_CONSEQUENCE_ACTOR")
        self.assertEqual(res["optimistic_writes"], 0)

        missing_receipt = copy.deepcopy(base)
        missing_receipt.pop("receipt_ref", None)
        res = self.settle(missing_receipt)
        self.assertEqual(res["status"], "REFUSED")
        self.assertEqual(res["reason_code"], "MISSING_OWNER_RECEIPT_REF")
        self.assertEqual(res["optimistic_writes"], 0)

        for effect_state in ("FAILED", "UNKNOWN"):
            tampered = copy.deepcopy(base)
            tampered["effect_state"] = effect_state
            res = self.settle(tampered)
            self.assertFalse(res["settled"], res)
            self.assertEqual(res["optimistic_writes"], 0)
        self.assertEqual(_snapshot(self.root), before)
        bowl = self.state()["matter_objects"]["dirty_bowl_02"]
        self.assertEqual(bowl["cleanliness"], "used")

    def test_06_same_operation_key_replay_never_cooks_or_eats_twice(self) -> None:
        self.introduce(
            "tomato_02", "番茄", "ingredient", "home_fridge",
            cook_state="raw", portions=1, shelf_life_hours=48,
        )
        collected = self.collect()
        cook_event = self.event(collected, "v08_matter:cook", decision_id="matter-replay-1")
        first = self.settle(cook_event)
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertEqual(len(self.foods()), 1)

        replay = self.settle(copy.deepcopy(cook_event))
        self.assertTrue(replay.get("already_applied"), replay)
        self.assertEqual(len(self.foods()), 1, "a replay must not cook a second dish")

        collected_next = self.collect()
        eat_refs = [ref for ref in self.matter_refs(collected_next) if ref.startswith("v08_matter:eat:")]
        self.assertTrue(eat_refs)
        eat_event = self.event(collected_next, eat_refs[0], decision_id="matter-replay-eat-1")
        self.settle(eat_event)
        self.assertEqual(self.foods()[0]["portions_left"], 1)
        eat_replay = self.settle(copy.deepcopy(eat_event))
        self.assertTrue(eat_replay.get("already_applied"), eat_replay)
        self.assertEqual(self.foods()[0]["portions_left"], 1, "a replay must not eat a second portion")

    def test_07_resident_hook_settles_selected_matter_candidate(self) -> None:
        import autonomous_life_resident as resident_mod

        self.introduce(
            "tomato_03", "番茄", "ingredient", "home_fridge",
            cook_state="raw", portions=1, shelf_life_hours=48,
        )
        collected = self.collect()

        # The deterministic selector can pick this generic matter family for real.
        selected = bridge.select_deterministic_loop_action(collected)
        selected_desc = (collected.get("descriptors") or {}).get(
            str(selected.get("selected_candidate_id") or "")
        )
        self.assertIsInstance(selected_desc, dict, selected)
        self.assertEqual(selected_desc.get("action_family"), "compose_open_act")
        self.assertTrue(str(selected_desc.get("source_ref") or "").startswith("v08_matter:"))

        # The real Resident hook settles one explicitly selected matter candidate
        # through the real consequence ledger (cook, not merely an observe).
        raw_ref = "v08_matter:cook"
        mapped_ref = "fitem:v02v03-%s" % bridge._safe_token(raw_ref).replace("_", "-")
        self_root = self.root

        class _Reader:
            def get_candidate(self, ref: str) -> dict[str, Any]:
                return {
                    "candidate_id": "cand:v08-matter-resident",
                    "source_ref": mapped_ref,
                    "origin_source_ref": raw_ref,
                    "target_refs": [raw_ref],
                }

        class _Cycle:
            runtime = SimpleNamespace(candidate_read=_Reader())

        class _ResidentStub:
            enable_v02_v03_bridge = True
            assembled = {"cycle": _Cycle()}

            def _resolve_v02_v03_day_root(self) -> Path:
                return self_root

            def _resolve_practice_service_for_feedback(self) -> None:
                return None

        record = {
            "logical_at": T0,
            "due": {
                "decision": "START",
                "status": "ACTED",
                "selected_candidate_ref": mapped_ref,
                "extra": {
                    "selected_candidate_ref": mapped_ref,
                    "decision_id": "matter-resident-1",
                },
            },
        }
        out = resident_mod.AutonomousLifeResident._run_v02_v03_bridge_hook(
            _ResidentStub(), record, None, pre_collected=collected
        )
        self.assertIsInstance(out, dict)
        self.assertNotIn("error", out, out)
        settlements = out.get("settlements") or []
        self.assertTrue(settlements, out)
        self.assertTrue(any(row.get("status") == "APPLIED" for row in settlements), settlements)
        foods = self.foods()
        self.assertEqual(len(foods), 1)
        self.assertEqual(foods[0]["cook_state"], "cooked")

        ledger_path = self.root / "phone" / bridge.CONSEQUENCE_LEDGER_FILENAME
        self.assertTrue(ledger_path.is_file())
        ledger = json.loads(ledger_path.read_text("utf8"))
        self.assertTrue(ledger.get("receipts"))


if __name__ == "__main__":
    unittest.main()

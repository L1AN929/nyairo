#!/usr/bin/env python3
"""v0.8 Wave 6: 0.2 (v2.0) daily-loop execution over the real owners (0 product-model calls).

Covers, against the real ``v02_v03_daily_loop_bridge`` and its owner views:

* bedside tomorrow note (``tomorrow_note:*`` + ``life_future_items``): one-shot close,
  replay idempotent, a second Decision on a closed note is a safe no-op;
* tentative plan (``plan:*``): adoption resolves that one plan through
  ``home_hearth_view.drop_plan`` (the owner has no ACCEPTED state by contract), other
  plans are untouched, and an illness-blocked strenuous plan is refused with 0 writes;
* workbench draft (``artifact:*`` / ``tray:*``): the tray note advances, no content is
  generated and no fake version is committed;
* bookstore window bookmark (``book:*``): the bookmark's follow note/timestamp advance;
* saved walk route (``route:*``): healthy walk advances route + visit log, an
  ``ILLNESS_BLOCKS_STRENUOUS_OUTING`` body refuses it with 0 writes;
* potted-plant journal (``plant:*``): the sketch link observation advances;
* common-ground pending clarification (``common_ground:*``): resolved through
  ``v02_common_ground.mark_shared_with_partner`` and the pending queue shrinks;
* the real ``AutonomousLifeCycle`` + ``AutonomousLifeResident`` seam with a scripted
  (zero-LLM) cognition selecting a tomorrow-note clue.

Run:  python3 tests/test_v08_v02_daily_loop_execution.py
"""

from __future__ import annotations

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
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "components" / "native" / "integration"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
import v02_common_ground as common_ground  # noqa: E402
from life_future_items import FutureItems  # noqa: E402
from components.phone import home_hearth_view as hearth_view  # noqa: E402
from components.phone import long_companionship_view as longterm_view  # noqa: E402
from components.phone import outing_places_view as outing_view  # noqa: E402
from components.phone import workbench_curio_view as workbench_view  # noqa: E402

T0 = "2026-11-15T09:00:00+08:00"
T1 = "2026-11-15T10:30:00+08:00"
T2 = "2026-11-15T15:00:00+08:00"
ECO_SCHEMA = "v03.ecology.bridge.v1"


def _eco_doc(*, phase: str = "healthy", severity: float = 0.0) -> dict:
    return {
        "schema": ECO_SCHEMA,
        "schema_version": 1,
        "revision": 0,
        "illness": {
            "phase": phase,
            "severity": severity,
            "strain_load": 0.5 if phase != "healthy" else 0.0,
            "rest_active": False,
        },
    }


class _V02DailyBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-v02-daily-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- seed helpers -------------------------------------------------------------
    def write_ecology(self, *, phase: str = "healthy", severity: float = 0.0) -> None:
        (self.root / "phone" / "v03_ecology.json").write_text(
            json.dumps(_eco_doc(phase=phase, severity=severity)), encoding="utf8"
        )

    def seed_desk_note(self, *, note_id: str = "bedside_1") -> None:
        (self.root / "phone" / "desk_tomorrow_note.json").write_text(
            json.dumps(
                {
                    "note_id": note_id,
                    "title": "明早把窗台的薄荷换个位置",
                    "status": "OPEN",
                    "due_at": T0,
                    "created_at": T0,
                },
                ensure_ascii=False,
            ),
            encoding="utf8",
        )

    def seed_future_item(self, *, title: str = "给旧相机换背带") -> str:
        proposed = FutureItems(self.root).propose(title=title, due_at=T0, now=T0)
        return str(proposed["item"]["item_ref"])

    def seed_plan(self, *, plan_id: str, title: str, window_note: str = "") -> None:
        state, reason = hearth_view.read_plans_state(self.root)
        if reason is not None:
            state = hearth_view.empty_plans_state()
        state, _changed, _card, err = hearth_view.propose_plan(
            state,
            plan_id=plan_id,
            title=title,
            window_note=window_note,
            proposer="her",
            now_iso=T0,
        )
        self.assertIsNone(err, err)
        hearth_view.write_plans_state(self.root, state)

    def seed_workbench(self) -> None:
        state = workbench_view.empty_tray_state()
        state, changed = workbench_view.set_tray(
            state,
            {
                "images": [],
                "materials": [],
                "work": {"artifact_id": "artifact:draft-1", "baseline_version_id": "ver-1"},
                "note": "纸样还没上色",
            },
            T0,
        )
        self.assertTrue(changed)
        workbench_view.write_tray_state(self.root, state)

    def seed_bookmark(self, *, book_code: str = "tide_almanac_notes") -> None:
        outing_view.write_bench_bookstore_state(
            self.root,
            {
                "schema_version": 1,
                "revision": 0,
                "updated_at": None,
                "bench_visits": [],
                "window_bookmarks": [
                    {
                        "bookmark_id": "wbk-1",
                        "slot_index": 0,
                        "book_code": book_code,
                        "title": "潮汐与岸边小历",
                        "note": "傍晚想翻两页",
                        "shared": False,
                        "saved_at": T0,
                        "updated_at": T0,
                    }
                ],
                "partner_window_note": None,
            },
        )

    def seed_route(self, *, route_id: str = "route-riverside-1") -> None:
        outing_view.write_routes_market_state(
            self.root,
            {
                "schema_version": 1,
                "revision": 0,
                "updated_at": None,
                "routes": [
                    {
                        "route_id": route_id,
                        "title": "河岸慢走",
                        "place_ids": ["riverside_path", "old_bookstreet"],
                        "album_artifact_id": None,
                        "caption": None,
                        "shared": False,
                        "created_at": T0,
                        "updated_at": T0,
                    }
                ],
                "market_wishes": [],
                "partner_market_tip": None,
            },
        )

    def seed_plant(self, *, plant_id: str = "mint-1") -> None:
        state = longterm_view.empty_room_habits_state()
        state, _link, changed = longterm_view.apply_link_plant_sketch(
            state,
            plant_id=plant_id,
            plant_name="窗台薄荷",
            target_kind="album",
            target_artifact_id="alb-plant-1",
            observation_note="叶尖带一点浅绿锯齿",
            shared=False,
            now_iso=T0,
        )
        self.assertTrue(changed)
        longterm_view.write_room_habits_state(self.root, state)

    def seed_common_ground(self, *, item_id: str = "cg_umbrella") -> None:
        result = common_ground.record_ground_item(
            self.root,
            item_id=item_id,
            topic="伞放在哪",
            summary="昨天顺手把伞收起来了",
            ground_status=common_ground.PENDING_CLARIFICATION,
        )
        self.assertTrue(result.get("ok"), result)

    # -- collection / event / settlement -----------------------------------------
    def collect(self, now_iso: str = T0) -> dict:
        return bridge.collect_v02_v03_loop_candidates(self.root, now_iso=now_iso)

    @staticmethod
    def descriptor(collected: dict, source_ref: str) -> dict:
        for desc in collected.get("descriptors", {}).values():
            if desc.get("source_ref") == source_ref:
                return desc
        raise AssertionError(f"missing candidate {source_ref!r}")

    def event_from(self, collected: dict, source_ref: str, *, decision_id: str) -> dict:
        """Build one event from a *captured* collection snapshot.

        Reusing the snapshot keeps the descriptor available after the first settlement
        resolved the underlying item (the live collector would no longer offer it), which
        is exactly what lets us assert replay / already-resolved behaviour.
        """

        event = bridge.v02_v03_round_event_for_selected_candidate(
            collected, source_ref, decision_id=decision_id
        )
        self.assertIsInstance(event, dict, source_ref)
        return event

    def event_for(self, source_ref: str, *, decision_id: str, now_iso: str = T0) -> dict:
        return self.event_from(
            self.collect(now_iso=now_iso), source_ref, decision_id=decision_id
        )

    def settle(self, event: dict, now_iso: str = T0) -> dict:
        return bridge.settle_v02_v03_round_consequences(
            self.root, event, now_iso=now_iso
        )

    def ledger_receipts(self) -> dict:
        path = self.root / "phone" / bridge.CONSEQUENCE_LEDGER_FILENAME
        if not path.is_file():
            return {}
        doc = json.loads(path.read_text(encoding="utf8"))
        return dict(doc.get("receipts") or {})

    def assert_ledger_has(self, result: dict) -> None:
        receipts = self.ledger_receipts()
        self.assertIn(result["operation_key"], receipts)
        self.assertEqual(receipts[result["operation_key"]]["result"]["status"], "APPLIED")


class TomorrowNoteTest(_V02DailyBase):
    def test_01_desk_note_is_followed_once_and_replay_is_a_noop(self) -> None:
        self.seed_desk_note()
        collected = self.collect()
        desc = self.descriptor(collected, "tomorrow_note:bedside_1")
        self.assertEqual(desc["action_family"], "follow_tomorrow_note")

        event = self.event_from(collected, "tomorrow_note:bedside_1", decision_id="note-1")
        self.assertEqual(event["action_kind"], "FOLLOW_TOMORROW_NOTE")
        self.assertTrue(event["close_desk_note"])
        self.assertEqual(event["note_id"], "bedside_1")

        first = self.settle(event)
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertIn("desk_tomorrow_note", first["slices_updated"])
        self.assertEqual(
            json.loads((self.root / "phone" / "desk_tomorrow_note.json").read_text("utf8"))[
                "status"
            ],
            "DONE",
        )
        self.assert_ledger_has(first)

        # Same Decision replayed -> cached, not a second close.
        replay = self.settle(event)
        self.assertTrue(replay.get("already_applied"), replay)

        # A different Decision on an already-closed note is a safe no-op, never an error.
        again = self.settle(
            self.event_from(collected, "tomorrow_note:bedside_1", decision_id="note-2"),
            now_iso=T1,
        )
        self.assertEqual(again["status"], "APPLIED", again)
        self.assertTrue(again["details"]["tomorrow_note"]["desk_note_already_done"])
        self.assertEqual(again.get("llm_calls", 0), 0)

    def test_02_future_item_is_closed_and_second_decision_is_idempotent(self) -> None:
        ref = self.seed_future_item()
        collected = self.collect()
        desc = self.descriptor(collected, ref)
        self.assertEqual(desc["action_family"], "follow_tomorrow_note")
        event = self.event_from(collected, ref, decision_id="fitem-1")
        self.assertEqual(event["action_kind"], "FOLLOW_TOMORROW_NOTE")
        self.assertEqual(event["future_item_ref"], ref)

        first = self.settle(event)
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertIn("life_future_items", first["slices_updated"])
        self.assertEqual(FutureItems(self.root).get(ref).status, "DONE")
        self.assert_ledger_has(first)

        replay = self.settle(event)
        self.assertTrue(replay.get("already_applied"), replay)
        again = self.settle(
            self.event_from(collected, ref, decision_id="fitem-2"), now_iso=T1
        )
        self.assertEqual(again["status"], "APPLIED", again)
        self.assertTrue(again["details"]["tomorrow_note"]["already_closed"])
        self.assertEqual(FutureItems(self.root).get(ref).status, "DONE")


class TentativePlanTest(_V02DailyBase):
    def test_01_adoption_resolves_only_the_named_plan(self) -> None:
        self.seed_plan(plan_id="plan-window", title="一起整理窗台")
        self.seed_plan(plan_id="plan-tea", title="傍晚一起煮一壶茶")
        collected = self.collect()
        desc = self.descriptor(collected, "plan:plan-window")
        self.assertEqual(desc["action_family"], "adopt_tentative_plan")
        self.assertFalse(desc["strenuous_outing"])

        event = self.event_from(collected, "plan:plan-window", decision_id="plan-1")
        self.assertEqual(event["action_kind"], "ADOPT_TENTATIVE_PLAN")
        first = self.settle(event)
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertIn("home_tentative_plans", first["slices_updated"])
        self.assertEqual(first["details"]["tentative_plan"]["resolution"], "ADOPTED")
        self.assert_ledger_has(first)

        state, reason = hearth_view.read_plans_state(self.root)
        self.assertIsNone(reason)
        by_id = {row["plan_id"]: row for row in state["plans"]}
        self.assertEqual(by_id["plan-window"]["status"], "DROPPED")
        self.assertEqual(by_id["plan-tea"]["status"], "TENTATIVE")

        # Replay is cached.
        self.assertTrue(self.settle(event).get("already_applied"))

        # A new Decision can advance the *next* plan; the resolved one stays put.
        second = self.settle(
            self.event_from(collected, "plan:plan-tea", decision_id="plan-2"), now_iso=T1
        )
        self.assertEqual(second["status"], "APPLIED", second)
        state, _reason = hearth_view.read_plans_state(self.root)
        by_id = {row["plan_id"]: row for row in state["plans"]}
        self.assertEqual(by_id["plan-tea"]["status"], "DROPPED")
        self.assertEqual(by_id["plan-window"]["status"], "DROPPED")

        # Re-adopting an already-resolved plan is a safe no-op, not an error.
        settled_again = self.settle(
            self.event_from(collected, "plan:plan-window", decision_id="plan-3"),
            now_iso=T2,
        )
        self.assertEqual(settled_again["status"], "APPLIED", settled_again)
        self.assertTrue(settled_again["details"]["tentative_plan"]["already_resolved"])
        self.assertEqual(settled_again.get("llm_calls", 0), 0)

    def test_02_illness_refuses_a_strenuous_plan_with_zero_writes(self) -> None:
        self.seed_plan(plan_id="plan-hike", title="去河岸徒步走到旧书街")
        desc = self.descriptor(self.collect(), "plan:plan-hike")
        self.assertTrue(desc["strenuous_outing"])
        event = self.event_for("plan:plan-hike", decision_id="plan-ill")

        # Now the body blocks strenuous outing; the command must be refused, no state write.
        self.write_ecology(phase="acute_illness", severity=0.6)
        before = (self.root / "phone" / "tentative_plans.json").read_text("utf8")
        refused = self.settle(event)
        self.assertEqual(refused["status"], "REFUSED", refused)
        self.assertEqual(refused["reason_code"], "ILLNESS_BLOCKS_STRENUOUS_OUTING")
        self.assertEqual(refused["optimistic_writes"], 0)
        self.assertEqual(
            (self.root / "phone" / "tentative_plans.json").read_text("utf8"), before
        )
        self.assertNotIn(event["operation_key"], self.ledger_receipts())


class WorkbenchTest(_V02DailyBase):
    def test_01_tray_note_advances_without_faking_a_version(self) -> None:
        self.seed_workbench()
        desc = self.descriptor(self.collect(), "artifact:draft-1")
        self.assertEqual(desc["action_family"], "advance_workbench_draft")

        event = self.event_for("artifact:draft-1", decision_id="bench-1")
        self.assertEqual(event["action_kind"], "ADVANCE_WORKBENCH")
        self.assertEqual(event["artifact_id"], "artifact:draft-1")
        self.assertIn("〔bench1〕", event["tray_note"])
        before_state, _reason = workbench_view.read_tray_state(self.root)
        rev_before = before_state["revision"]

        first = self.settle(event)
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertIn("workbench_tray", first["slices_updated"])
        state, reason = workbench_view.read_tray_state(self.root)
        self.assertIsNone(reason)
        self.assertEqual(state["revision"], rev_before + 1)
        self.assertEqual(state["tray"]["note"], event["tray_note"])
        # The real draft identity is preserved; no version was fabricated.
        self.assertEqual(state["tray"]["work"]["artifact_id"], "artifact:draft-1")
        self.assertEqual(state["tray"]["work"]["baseline_version_id"], "ver-1")
        self.assert_ledger_has(first)

        self.assertTrue(self.settle(event).get("already_applied"))

        # A new Decision advances the tray note again.
        second = self.settle(
            self.event_for("artifact:draft-1", decision_id="bench-2"), now_iso=T1
        )
        self.assertEqual(second["status"], "APPLIED", second)
        state, _reason = workbench_view.read_tray_state(self.root)
        self.assertEqual(state["revision"], rev_before + 2)
        self.assertIn("〔bench2〕", state["tray"]["note"])
        self.assertEqual(second.get("llm_calls", 0), 0)


class BookstoreBookmarkTest(_V02DailyBase):
    def test_01_bookmark_follow_note_advances_and_replays_idempotently(self) -> None:
        self.seed_bookmark()
        desc = self.descriptor(self.collect(), "book:tide_almanac_notes")
        self.assertEqual(desc["action_family"], "follow_bookstore_bookmark")
        event = self.event_for("book:tide_almanac_notes", decision_id="book-1")
        self.assertEqual(event["action_kind"], "FOLLOW_BOOKSTORE_BOOKMARK")

        first = self.settle(event)
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertIn("outing_bench_bookstore", first["slices_updated"])
        state, reason = outing_view.read_bench_bookstore_state(self.root)
        self.assertIsNone(reason)
        mark = state["window_bookmarks"][0]
        self.assertIn("翻了几页", mark["note"])
        self.assertEqual(mark["updated_at"], T0)
        self.assertEqual(state["revision"], 1)
        self.assert_ledger_has(first)

        self.assertTrue(self.settle(event).get("already_applied"))

        second = self.settle(
            self.event_for("book:tide_almanac_notes", decision_id="book-2"), now_iso=T1
        )
        self.assertEqual(second["status"], "APPLIED", second)
        state, _reason = outing_view.read_bench_bookstore_state(self.root)
        self.assertIn("〔book2〕", state["window_bookmarks"][0]["note"])
        self.assertEqual(state["revision"], 2)
        self.assertEqual(second.get("llm_calls", 0), 0)


class StrenuousWalkTest(_V02DailyBase):
    def test_01_healthy_walk_advances_route_and_visit_log(self) -> None:
        self.write_ecology()
        self.seed_route()
        desc = self.descriptor(self.collect(), "route:route-riverside-1")
        self.assertEqual(desc["action_family"], "strenuous_outing_walk")
        self.assertTrue(desc["strenuous_outing"])
        event = self.event_for("route:route-riverside-1", decision_id="walk-1")
        self.assertEqual(event["action_kind"], "STRENUOUS_OUTING_WALK")
        self.assertEqual(event["route_stops"], ["riverside_path", "old_bookstreet"])

        first = self.settle(event)
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertIn("outing_routes_market", first["slices_updated"])
        self.assertIn("outing_bench_bookstore", first["slices_updated"])
        routes_state, reason = outing_view.read_routes_market_state(self.root)
        self.assertIsNone(reason)
        route = routes_state["routes"][0]
        self.assertIn("走完了一趟", route["caption"])
        self.assertEqual(route["updated_at"], T0)
        bench_state, _bench_reason = outing_view.read_bench_bookstore_state(self.root)
        self.assertEqual(len(bench_state["bench_visits"]), 1)
        self.assertEqual(bench_state["bench_visits"][0]["place_id"], "riverside_path")
        self.assert_ledger_has(first)

        self.assertTrue(self.settle(event).get("already_applied"))

    def test_02_illness_blocks_the_walk_with_zero_writes(self) -> None:
        self.write_ecology()
        self.seed_route()
        event = self.event_for("route:route-riverside-1", decision_id="walk-ill")

        # The body now blocks strenuous outing: refuse, and change nothing on disk.
        self.write_ecology(phase="acute_illness", severity=0.6)
        before_routes = (self.root / "phone" / "outing_routes_market.json").read_text("utf8")
        refused = self.settle(event, now_iso=T1)
        self.assertEqual(refused["status"], "REFUSED", refused)
        self.assertEqual(refused["reason_code"], "ILLNESS_BLOCKS_STRENUOUS_OUTING")
        self.assertEqual(refused["optimistic_writes"], 0)
        self.assertEqual(
            (self.root / "phone" / "outing_routes_market.json").read_text("utf8"),
            before_routes,
        )
        bench_state, _reason = outing_view.read_bench_bookstore_state(self.root)
        self.assertEqual(bench_state["bench_visits"], [])
        self.assertNotIn(event["operation_key"], self.ledger_receipts())


class PlantJournalTest(_V02DailyBase):
    def test_01_plant_observation_advances_and_replays_idempotently(self) -> None:
        self.seed_plant()
        desc = self.descriptor(self.collect(), "plant:mint-1")
        self.assertEqual(desc["action_family"], "observe_plant_journal")
        event = self.event_for("plant:mint-1", decision_id="plant-1")
        self.assertEqual(event["action_kind"], "OBSERVE_PLANT_JOURNAL")
        self.assertEqual(event["plant_id"], "mint-1")
        before_state, _reason = longterm_view.read_room_habits_state(self.root)
        rev_before = before_state["revision"]

        first = self.settle(event)
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertIn("longterm_room_habits", first["slices_updated"])
        state, reason = longterm_view.read_room_habits_state(self.root)
        self.assertIsNone(reason)
        link = state["plant_sketch_links"][0]
        self.assertIn("又看了一眼", link["observation_note"])
        self.assertEqual(link["updated_at"], T0)
        self.assertEqual(state["revision"], rev_before + 1)
        self.assert_ledger_has(first)

        self.assertTrue(self.settle(event).get("already_applied"))

        second = self.settle(
            self.event_for("plant:mint-1", decision_id="plant-2"), now_iso=T1
        )
        self.assertEqual(second["status"], "APPLIED", second)
        state, _reason = longterm_view.read_room_habits_state(self.root)
        self.assertEqual(state["revision"], rev_before + 2)
        self.assertIn("〔plant2〕", state["plant_sketch_links"][0]["observation_note"])
        self.assertEqual(second.get("llm_calls", 0), 0)


class CommonGroundTest(_V02DailyBase):
    def test_01_clarification_leaves_the_pending_queue(self) -> None:
        self.seed_common_ground()
        collected = self.collect()
        desc = self.descriptor(collected, "common_ground:cg_umbrella")
        self.assertEqual(desc["action_family"], "clarify_common_ground")
        event = self.event_from(collected, "common_ground:cg_umbrella", decision_id="cg-1")
        self.assertEqual(event["action_kind"], "CLARIFY_COMMON_GROUND")
        self.assertEqual(event["cg_id"], "cg_umbrella")

        pending_before = len(
            common_ground.summarize_common_ground_for_cognition(self.root).get(
                "pending_clarifications"
            )
            or []
        )
        self.assertEqual(pending_before, 1)

        first = self.settle(event)
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertIn("v02_common_ground", first["slices_updated"])
        summary = common_ground.summarize_common_ground_for_cognition(self.root)
        self.assertEqual(summary.get("pending_clarifications"), [])
        view = common_ground.read_v02_common_ground_view(self.root)
        item = next(row for row in view["items"] if row["item_id"] == "cg_umbrella")
        self.assertEqual(item["ground_status"], common_ground.SHARED_WITH_PARTNER)
        self.assert_ledger_has(first)

        self.assertTrue(self.settle(event).get("already_applied"))

        # A different Decision on an already-clarified item is a safe no-op.
        again = self.settle(
            self.event_from(collected, "common_ground:cg_umbrella", decision_id="cg-2"),
            now_iso=T1,
        )
        self.assertEqual(again["status"], "APPLIED", again)
        self.assertTrue(again["details"]["common_ground"]["already_clarified"])
        self.assertEqual(again.get("llm_calls", 0), 0)


# ---------------------------------------------------------------------------
# Real cycle -> resident seam (POSIX only: the fixture uses real Supply/World
# owner sockets, exactly like ``test_one_day_life``).
# ---------------------------------------------------------------------------

try:  # pragma: no cover - platform gate only
    from test_one_day_life import CycleTest as _CycleTest, StubWriter, posix_only
    import agency_decision_ag1 as ag1
    import autonomous_life_assembly as assembly
    import autonomous_life_cycle as alc
    import autonomous_life_resident as resident
    import autonomous_life_runner as life_runner
    import one_day_life as odl

    _FIXTURE_ERROR = None
except Exception as _exc:  # noqa: BLE001 - native Windows has no fcntl / AF_UNIX
    _FIXTURE_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
    _CycleTest = unittest.TestCase
    posix_only = unittest.skipIf(True, "real owner fixture needs POSIX")


needs_fixture = unittest.skipIf(
    globals().get("_FIXTURE_ERROR") is not None,
    "real owner fixture unavailable: %s" % globals().get("_FIXTURE_ERROR", ""),
)


class _ScriptedSourceCognition:
    """Zero-product-model decision: only ever selects a candidate whose source_ref matches."""

    def __init__(self, *, select_source_prefix: str) -> None:
        self.select_source_prefix = select_source_prefix
        self.call_count = 0
        self.seen_sources: list[str] = []

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.call_count += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        chosen = None
        for cand in candidates:
            source = str(cand.get("source_ref") or "")
            self.seen_sources.append(source)
            if source.startswith(self.select_source_prefix):
                chosen = cand
                break
        if chosen is not None:
            raw = {
                "decision": ag1.DECISION_START,
                "selected_candidate_id": chosen.get("candidate_id"),
                "reason_codes": allowed[:1],
                "short_rationale": "stub: 顺着这件真实的日常小事走一小步。",
                "deferred_candidate_ids": [],
            }
        else:
            raw = {
                "decision": ag1.DECISION_NO_ACTION,
                "selected_candidate_id": None,
                "reason_codes": allowed[:1],
                "short_rationale": "stub: 今天先不动这一件。",
                "deferred_candidate_ids": [],
            }
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:stub:%d" % self.call_count,
            model_id="stub:v08-wave6",
            model_version="v08-wave6-stub-v1",
            raw_output=raw,
            latency_ms=1,
        )


@posix_only
@needs_fixture
class CycleResidentDailySeamTest(_CycleTest):
    """Real cycle + real resident tick, scripted cognition, real 0.2 owner writes."""

    def _build(self, cognition):
        data = self._prepared_instance()
        env = self.instance_env()
        writer = StubWriter()
        runtime, port = self._runtime(cognition, env, data)
        cycle = alc.AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id=data["subject_id"],
            writer=writer, enabled=True, env=env,
        )
        return data, env, cycle

    def _instance_root(self) -> Path:
        return Path(self.instance)

    def _tick_built(self, env, cycle, now_iso: str) -> dict:
        from datetime import datetime as _dt

        fixed = _dt.fromisoformat(now_iso)
        runner_obj = life_runner.AutonomousLifeRunner(
            cycle, state_root=odl.life_state_root(self.instance), env=env,
            clock=lambda: fixed, interval_minutes=0,
        )
        ticked = resident.AutonomousLifeResident(
            state_root=odl.life_state_root(self.instance), environment=env, act=True,
            clock=lambda: fixed, sleeper=lambda _seconds: None, monotonic=lambda: 0.0,
        )
        ticked.assembled = {
            "state": assembly.READY, "runner": runner_obj, "cycle": cycle,
        }
        try:
            return ticked.tick()
        finally:
            ticked.stop()

    def test_a_tomorrow_note_is_selected_and_settled_by_the_resident(self) -> None:
        now_iso = "2026-11-15T09:00:00+00:00"
        cognition = _ScriptedSourceCognition(
            select_source_prefix="fitem:v02v03-tomorrow_note-bedside_1"
        )
        _data, env, cycle = self._build(cognition)
        inst = self._instance_root()
        (inst / "phone").mkdir(parents=True, exist_ok=True)
        (inst / "phone" / "desk_tomorrow_note.json").write_text(
            json.dumps(
                {
                    "note_id": "bedside_1",
                    "title": "明早把窗台的薄荷换个位置",
                    "status": "OPEN",
                    "due_at": now_iso,
                    "created_at": now_iso,
                },
                ensure_ascii=False,
            ),
            encoding="utf8",
        )

        record = self._tick_built(env, cycle, now_iso)
        due = record.get("due") or {}
        self.assertEqual(due.get("decision"), "START", record)
        self.assertEqual(due.get("status"), "ACTED", record)
        self.assertEqual(cognition.call_count, 1)

        note = json.loads((inst / "phone" / "desk_tomorrow_note.json").read_text("utf8"))
        self.assertEqual(note["status"], "DONE")

        bridge_view = record.get("v02_v03_bridge") or {}
        settlements = bridge_view.get("settlements") or []
        self.assertTrue(settlements, bridge_view)
        self.assertTrue(
            any(
                "desk_tomorrow_note" in (row.get("slices_updated") or [])
                or row.get("already_applied")
                for row in settlements
            ),
            settlements,
        )
        report = (due.get("extra") or {}).get("v02_v03_daily") or record.get(
            "v02_v03_daily"
        ) or {}
        if report:
            self.assertEqual(report.get("action_kind"), "FOLLOW_TOMORROW_NOTE")

    def test_b_tentative_plan_is_adopted_by_the_resident(self) -> None:
        now_iso = "2026-11-15T09:00:00+00:00"
        cognition = _ScriptedSourceCognition(
            select_source_prefix="fitem:v02v03-plan-plan-seam01"
        )
        _data, env, cycle = self._build(cognition)
        inst = self._instance_root()
        (inst / "phone").mkdir(parents=True, exist_ok=True)
        state = hearth_view.empty_plans_state()
        state, _changed, _card, err = hearth_view.propose_plan(
            state,
            plan_id="plan-seam01",
            title="一起整理窗台",
            window_note="",
            proposer="her",
            now_iso=now_iso,
        )
        self.assertIsNone(err, err)
        hearth_view.write_plans_state(inst, state)

        record = self._tick_built(env, cycle, now_iso)
        due = record.get("due") or {}
        self.assertEqual(due.get("decision"), "START", record)
        self.assertEqual(due.get("status"), "ACTED", record)

        plans_state, reason = hearth_view.read_plans_state(inst)
        self.assertIsNone(reason)
        plan = next(row for row in plans_state["plans"] if row["plan_id"] == "plan-seam01")
        self.assertEqual(plan["status"], "DROPPED")
        bridge_view = record.get("v02_v03_bridge") or {}
        self.assertTrue(
            any(
                "home_tentative_plans" in (row.get("slices_updated") or [])
                or row.get("already_applied")
                for row in (bridge_view.get("settlements") or [])
            ),
            bridge_view,
        )


if __name__ == "__main__":
    unittest.main()

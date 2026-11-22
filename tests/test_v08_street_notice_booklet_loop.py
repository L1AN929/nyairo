#!/usr/bin/env python3
"""v0.8 Wave 3: street journey / public notice / place booklet / bookstore display loop.

Covers the real owners only (0 product-model calls):
* ``has_v02_v03_active_state`` zero-write behaviour with and without a real
  ``world/v04_neighborhood_state.json``;
* the four new Ch.46 B/E/F daily candidates emitted by
  ``collect_v02_v03_loop_candidates`` and settled through
  ``settle_v02_v03_round_consequences`` (idempotent replay included);
* privacy grading (an unread notice never leaks its body; the private booklet is
  withheld from the partner view), illness creative gating, closed-store gating;
* the real ``AutonomousLifeCycle.evaluate_once`` + real ``AutonomousLifeResident.tick``
  seam with a scripted (zero-LLM) cognition: selecting ``fitem:v02v03-v04_*`` really
  writes ``world/v04_neighborhood_state.json``, while a quiet/refused round writes
  nothing.

Run:  python3 tests/test_v08_street_notice_booklet_loop.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "native" / "integration"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v04_neighborhood_living as engine  # noqa: E402

T0 = "2026-11-15T09:00:00+08:00"
T_OPEN = "2026-11-15T10:30:00+08:00"
T_CLOSED = "2026-11-15T21:00:00+08:00"


def _iso_add(minutes: int, base: str = T0) -> str:
    dt = datetime.fromisoformat(base) + timedelta(minutes=minutes)
    return dt.isoformat(timespec="seconds")


def _write_ecology(root: Path, phase: str, severity: float) -> None:
    (root / "phone").mkdir(parents=True, exist_ok=True)
    (root / "phone" / "v03_ecology.json").write_text(
        json.dumps({
            "schema": "v03.ecology.bridge.v1",
            "schema_version": 1,
            "revision": 1,
            "illness": {
                "phase": phase,
                "severity": severity,
                "strain_load": 0.5 if phase != "healthy" else 0.0,
                "rest_active": False,
            },
        }),
        encoding="utf8",
    )


def _tree_snapshot(root: Path) -> list[tuple[str, int, int]]:
    rows: list[tuple[str, int, int]] = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        rows.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    return rows


class _V04Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-street-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- owners -------------------------------------------------------------------
    def init_neighborhood(self, *, now_iso: str = T0) -> None:
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": "clear", "airflow": "medium"},
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "applied")

    def collect(self, now_iso: str) -> dict:
        return bridge.collect_v02_v03_loop_candidates(self.root, now_iso=now_iso)

    @staticmethod
    def descriptor(collected: dict, source_ref: str) -> dict:
        for desc in collected.get("descriptors", {}).values():
            if desc.get("source_ref") == source_ref:
                return desc
        raise AssertionError(f"missing candidate {source_ref!r}")

    @staticmethod
    def source_refs(collected: dict) -> set[str]:
        return {
            str(desc.get("source_ref") or "")
            for desc in collected.get("descriptors", {}).values()
        }

    @staticmethod
    def event_for(desc: dict, *, tag: str) -> dict:
        return {
            "actor_side": "life_resident",
            "effect_state": "APPLIED",
            "action_kind": desc.get("v04_action_kind"),
            "v04_action": desc["v04_action"],
            "v04_payload": dict(desc.get("v04_payload") or {}),
            "source_ref": desc["source_ref"],
            "receipt_ref": f"test:{tag}",
            "operation_key": f"test_{tag}",
        }

    def settle(self, event: dict, *, now_iso: str) -> dict:
        return bridge.settle_v02_v03_round_consequences(self.root, event, now_iso=now_iso)

    def state(self) -> dict:
        state, exists = engine.load_state_readonly(self.root)
        self.assertTrue(exists)
        return state


class HasActiveStateTest(unittest.TestCase):
    def test_empty_root_is_a_zero_write_false(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v08-empty-") as tmp:
            root = Path(tmp)
            before = _tree_snapshot(root)
            self.assertFalse(bridge.has_v02_v03_active_state(root))
            self.assertEqual(_tree_snapshot(root), before)

    def test_real_neighborhood_state_file_is_active_without_phone_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v08-v04only-") as tmp:
            root = Path(tmp)
            code, body = mutate_v04_neighborhood_view(
                root,
                {"action": "set_local_weather", "weather": "clear"},
                actor_side="partner",
                now_iso=T0,
            )
            self.assertEqual(code, 200, body)
            self.assertTrue((root / "world" / "v04_neighborhood_state.json").is_file())
            self.assertFalse((root / "phone").exists())
            self.assertTrue(bridge.has_v02_v03_active_state(root))


class StreetDailyCandidatesTest(_V04Base):
    def _start_journey(self) -> None:
        self.init_neighborhood()
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {"action": "start_journey", "target_place": "street_corner"},
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "applied")

    def _post_notice(self, *, now_iso: str = T0) -> str:
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {
                "action": "post_public_notice",
                "title": "周末旧物交换角",
                "body": "周六上午小院里有一场旧物交换，欢迎带一件闲置来。",
                "valid_hours": 48,
            },
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        return str(body["result"]["notice_id"])

    def _inscribe_card(self, *, now_iso: str = T0) -> str:
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {
                "action": "craft_paper_or_box",
                "operation": "cut_card",
                "units": 1,
                "card_id": "card_display",
                "title": "街角的纸卡",
                "draft_text": "先写给往后自己的一句话。",
            },
            actor_side="subject",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {
                "action": "craft_paper_or_box",
                "operation": "inscribe_card",
                "card_id": "card_display",
                "text": "今天也把步子放慢一点。",
            },
            actor_side="subject",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        return "card_display"

    def test_01_journey_step_collect_settle_and_idempotent_replay(self) -> None:
        self._start_journey()
        collected = self.collect(T0)
        journey_sources = [
            ref for ref in self.source_refs(collected) if ref.startswith("v04_journey_step:")
        ]
        self.assertTrue(journey_sources, collected.get("descriptors"))
        desc = self.descriptor(collected, journey_sources[0])
        self.assertTrue(desc["requires_outing"])
        self.assertEqual(desc["v04_action"], "advance_or_turnback_journey")
        self.assertIn(desc["v04_payload"]["operation"], ("advance", "resume", "turn_back"))

        event = self.event_for(desc, tag="journey")
        settled = self.settle(event, now_iso=_iso_add(10))
        self.assertEqual(settled["status"], "APPLIED", settled)
        self.assertIn("v04_neighborhood", settled["slices_updated"])
        journey = self.state()["journey"]
        self.assertIn(journey["state"], ("in_transit", "arrived", "at_place", "paused_at_node"))
        self.assertTrue(
            journey.get("accumulated_walk_seconds") or journey.get("current_place_id")
        )
        repeat = self.settle(event, now_iso=_iso_add(10))
        self.assertTrue(repeat["already_applied"], repeat)

    def test_02_notice_collect_read_privacy_and_expiry(self) -> None:
        self.init_neighborhood()
        notice_id = self._post_notice()
        collected = self.collect(T0)
        src = f"v04_public_notice:{notice_id}"
        desc = self.descriptor(collected, src)
        self.assertFalse(desc["requires_outing"])
        self.assertFalse(desc["creative_intensive"])
        # The body is never carried by the candidate and the subject view withholds it.
        self.assertNotIn("旧物交换，欢迎", json.dumps(desc, ensure_ascii=False))
        self.assertTrue(desc.get("body_withheld_until_read"))
        code, subject_view = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T0
        )
        self.assertEqual(code, 200)
        notice = next(
            n for n in subject_view["data"]["public_life"]["public_notices"]
            if n["notice_id"] == notice_id
        )
        self.assertIsNone(notice["body"])
        self.assertTrue(notice["body_withheld_until_read"])

        settled = self.settle(self.event_for(desc, tag="notice"), now_iso=T0)
        self.assertEqual(settled["status"], "APPLIED", settled)
        notice_row = self.state()["public_life"]["public_notices"][0]
        self.assertTrue(notice_row["read_by_subject"])
        # Once read, the same clue is no longer offered.
        self.assertNotIn(src, self.source_refs(self.collect(T0)))
        # Expired notices are not offered either.
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {
                "action": "post_public_notice",
                "notice_id": "notice_expired",
                "title": "已经过期的纸",
                "body": "这张纸过期了。",
                "valid_hours": 1,
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        self.assertNotIn(
            "v04_public_notice:notice_expired",
            self.source_refs(self.collect(_iso_add(180))),
        )

    def test_03_place_booklet_collect_record_and_partner_withheld(self) -> None:
        self.init_neighborhood()
        collected = self.collect(T0)
        booklet_sources = [
            ref for ref in self.source_refs(collected) if ref.startswith("v04_place_booklet:")
        ]
        self.assertIn("v04_place_booklet:yard", booklet_sources)
        desc = self.descriptor(collected, "v04_place_booklet:yard")
        self.assertFalse(desc["requires_outing"])
        self.assertTrue(desc["creative_intensive"])
        self.assertIs(desc["v04_payload"]["shared_with_partner"], False)
        self.assertEqual(desc["v04_payload"]["operation"], "record_entry")

        settled = self.settle(self.event_for(desc, tag="booklet"), now_iso=T0)
        self.assertEqual(settled["status"], "APPLIED", settled)
        booklet = self.state()["public_life"]["place_booklet"]
        self.assertIn("yard", booklet["entries"])
        self.assertEqual(booklet["status"], "active")
        self.assertFalse(booklet["shared_with_partner"])
        self.assertNotIn("v04_place_booklet:yard", self.source_refs(self.collect(T0)))

        # A private booklet is withheld from the partner view.
        code, partner_view = read_v04_neighborhood_view(
            self.root, has_subject_capability=False, now_iso=T0
        )
        self.assertEqual(code, 200)
        partner_booklet = partner_view["data"]["public_life"]["place_booklet"]
        self.assertTrue(partner_booklet["withheld"])
        self.assertEqual(partner_booklet["entries"], [])

    def test_04_bookstore_display_open_gate_and_idempotent_settle(self) -> None:
        self.init_neighborhood()
        card_id = self._inscribe_card()
        # Closed store: no display candidate.
        closed = self.collect(T_CLOSED)
        self.assertFalse([
            ref for ref in self.source_refs(closed) if ref.startswith("v04_bookstore_display:")
        ])
        # Open store: the inscribed card is offered, and settling it really displays it.
        opened = self.collect(T_OPEN)
        src = f"v04_bookstore_display:{card_id}"
        desc = self.descriptor(opened, src)
        self.assertTrue(desc["requires_outing"])
        self.assertFalse(desc["creative_intensive"])
        event = self.event_for(desc, tag="display")
        settled = self.settle(event, now_iso=T_OPEN)
        self.assertEqual(settled["status"], "APPLIED", settled)
        card = self.state()["craft_bench"]["cards"][card_id]
        self.assertTrue(card["displayed_at_bookstore"])
        display_cards = self.state()["public_life"]["bookstore"]["display_cards"]
        self.assertEqual([row["card_id"] for row in display_cards], [card_id])
        repeat = self.settle(event, now_iso=T_OPEN)
        self.assertTrue(repeat["already_applied"], repeat)
        self.assertEqual(
            len(self.state()["public_life"]["bookstore"]["display_cards"]), 1
        )

    def test_05_illness_blocks_booklet_not_notice(self) -> None:
        self.init_neighborhood()
        self._post_notice()
        _write_ecology(self.root, "acute_illness", 0.6)
        sick = self.collect(T0)
        filtered = sick["gating"]["filtered_candidates"]
        booklet_blocked = [
            row for row in filtered
            if str(row.get("source_ref", "")).startswith("v04_place_booklet:")
        ]
        self.assertTrue(booklet_blocked)
        self.assertTrue(all(
            row["reason_code"] == "ILLNESS_CREATIVE_BLOCK" for row in booklet_blocked
        ))
        notice_sources = [
            ref for ref in self.source_refs(sick) if ref.startswith("v04_public_notice:")
        ]
        self.assertTrue(notice_sources)
        self.assertFalse([
            row for row in filtered
            if str(row.get("source_ref", "")).startswith("v04_public_notice:")
        ])


# ---------------------------------------------------------------------------
# Real cycle -> resident seam (POSIX only: the fixture uses the real Supply
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


class _ScriptedCognition:
    """Zero-product-model decision: only ever selects a candidate it was shown."""

    def __init__(self, *, decision: str = "START", select_prefix: str = "v04_") -> None:
        self.decision = decision
        self.select_prefix = select_prefix
        self.call_count = 0

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.call_count += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        chosen = None
        for cand in candidates:
            targets = [str(ref) for ref in (cand.get("target_refs") or [])]
            if any(ref.startswith(self.select_prefix) for ref in targets):
                chosen = cand
                break
        if self.decision == "START" and chosen is not None:
            raw = {
                "decision": ag1.DECISION_START,
                "selected_candidate_id": chosen.get("candidate_id"),
                "reason_codes": allowed[:1],
                "short_rationale": "stub: 顺着街区里这件真实的小事走一小步。",
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
            model_id="stub:v08-wave3",
            model_version="v08-wave3-stub-v1",
            raw_output=raw,
            latency_ms=1,
        )


@posix_only
@needs_fixture
class CycleResidentSeamTest(_CycleTest):
    """Real cycle + real resident tick, scripted cognition, real v0.4 owner write."""

    NOW = "2026-11-15T06:30:00+00:00"

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
        # The bridge's day root is the instance root that owns ``phone/``; the same
        # resolution the resident uses.
        return Path(self.instance)

    def _seed_open_notice(self, now_iso: str) -> str:
        inst = self._instance_root()
        (inst / "phone").mkdir(parents=True, exist_ok=True)
        code, body = mutate_v04_neighborhood_view(
            inst,
            {"action": "set_local_weather", "weather": "clear", "airflow": "medium"},
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        code, body = mutate_v04_neighborhood_view(
            inst,
            {
                "action": "post_public_notice",
                "title": "街角的周末旧物交换",
                "body": "周六上午小院有旧物交换。",
                "valid_hours": 48,
            },
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        return str(body["result"]["notice_id"])

    def _state_bytes(self) -> bytes:
        path = self._instance_root() / "world" / "v04_neighborhood_state.json"
        return path.read_bytes() if path.is_file() else b""

    def test_a_start_writes_the_real_neighborhood_state(self) -> None:
        now_iso = self.NOW
        notice_id = self._seed_open_notice(now_iso)
        before = self._state_bytes()
        cognition = _ScriptedCognition(decision="START", select_prefix="v04_public_notice:")
        _data, env, cycle = self._build(cognition)
        fixed = datetime.fromisoformat(now_iso)

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
            record = ticked.tick()
        finally:
            ticked.stop()

        due = record.get("due") or {}
        self.assertEqual(due.get("decision"), "START", record)
        self.assertEqual(due.get("status"), "ACTED", record)
        self.assertEqual(cognition.call_count, 1)
        after = self._state_bytes()
        self.assertNotEqual(after, before)
        state = json.loads(after.decode("utf-8"))
        notice = next(
            n for n in state["public_life"]["public_notices"]
            if n["notice_id"] == notice_id
        )
        self.assertTrue(notice["read_by_subject"])
        # The cycle's own settlement and the resident hook's replay share one operation key:
        # exactly one real v0.4 mutation receipt exists for this tick.
        self.assertEqual(len(state["operation_receipts"]), 1, state["operation_receipts"])
        bridge_view = record.get("v02_v03_bridge") or {}
        self.assertTrue(bridge_view.get("settlements"), bridge_view)

    def test_b_quiet_round_never_writes(self) -> None:
        now_iso = self.NOW
        self._seed_open_notice(now_iso)
        before = self._state_bytes()
        cognition = _ScriptedCognition(decision="NO_ACTION")
        _data, env, cycle = self._build(cognition)
        fixed = datetime.fromisoformat(now_iso)
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
            record = ticked.tick()
        finally:
            ticked.stop()
        due = record.get("due") or {}
        self.assertEqual(due.get("decision"), "NO_ACTION", record)
        self.assertEqual(self._state_bytes(), before)

    def test_c_refused_settlement_is_named_and_writes_no_entry(self) -> None:
        now_iso = self.NOW
        inst = self._instance_root()
        (inst / "phone").mkdir(parents=True, exist_ok=True)
        code, body = mutate_v04_neighborhood_view(
            inst,
            {"action": "set_local_weather", "weather": "clear", "airflow": "medium"},
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        collected = bridge.collect_v02_v03_loop_candidates(inst, now_iso=now_iso)
        desc = None
        for row in collected.get("descriptors", {}).values():
            if str(row.get("source_ref") or "") == "v04_place_booklet:yard":
                desc = dict(row)
                break
        self.assertIsNotNone(desc, collected.get("descriptors"))

        # The place stops being firsthand-visited between the offer and the step: the
        # owner must refuse the entry, and nothing may be written.
        state, exists = engine.load_state_readonly(inst)
        self.assertTrue(exists)
        state["places"]["yard"]["knowledge_status"] = "told_clue"
        engine.save_state_atomic(inst, state)
        before = self._state_bytes()

        cognition = _ScriptedCognition(decision="START", select_prefix="v04_place_booklet:")
        _data, env, cycle = self._build(cognition)
        cycle._v02_v03_day_root = inst
        cycle._prepared_v02_v03_collected = collected
        fixed = datetime.fromisoformat(now_iso)
        cycle._now = lambda: fixed
        out = cycle.evaluate_once(trigger_ref="evt:v08wave3:refused", observed_at=now_iso)
        self.assertEqual(out.get("outcome"), "STEP_REFUSED", out)
        self.assertEqual(self._state_bytes(), before)
        booklet = json.loads(before.decode("utf-8"))["public_life"]["place_booklet"]
        self.assertNotIn("yard", booklet["entries"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""v0.8 Daily Loop waves J..N end-to-end seam.

Two complementary layers:

* ``WavesJToNDailyRoundDirectTests`` -- pure, fast, native-Windows/WSL safe.
  Wave K plant recovery, Wave L shared-notebook missing-text soft landing and
  Wave M scalding-drink gentle hold, all through the real daily-loop bridge and
  the real lived-context builder (zero product-model calls).
* ``WavesJToNCycleResidentE2ETests`` -- the real ``AutonomousLifeCycle`` +
  ``AutonomousLifeResident.tick`` seam with a scripted (zero-LLM) cognition.
  POSIX-only: the fixture uses real Supply/World owner sockets.

Run (native Windows) ::

    python -B -m unittest tests.test_v08_daily_loop_waves_j_to_n_e2e -v

Run (WSL, for the POSIX-only class) ::

    python3 -m unittest tests.test_v08_daily_loop_waves_j_to_n_e2e -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

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

from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402

# Bind the shared transaction guard before the daily-loop bridge is imported.
# On native Windows the bridge installs a no-op ``fcntl`` shim into
# ``sys.modules``; if ``state_tx_guard`` imports that shim first its OS-level
# lock silently degrades to a no-op.  This import makes the guard pick its real
# ``msvcrt`` fallback first (same rationale as the Ch.26 / Ch.41 sister tests).
from components.life.scripts import state_tx_guard as _state_tx_guard  # noqa: E402,F401

from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402
from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v04_neighborhood_living as nb_engine  # noqa: E402
from components.world.scripts import v08_universal_matter as matter  # noqa: E402

# --- direct-round constants (native Windows) -------------------------------
T_WILT_BASE = "2026-09-01T10:00:00+08:00"
T_WILTED = "2026-09-16T10:00:00+08:00"
PLANT_PREFIX = "v04_porch_plant:"

T_NOTEBOOK = "2026-11-16T20:00:00+08:00"
T_NOTEBOOK_COLLECT = "2026-11-16T20:10:00+08:00"
SHARED_DOMAIN = "v04.elastic.shared_notebook"

T_TEA = "2026-11-16T09:00:00+08:00"

# --- real cycle/resident fixture constants (POSIX) -------------------------
E2E_T_BASE = "2026-11-01T10:00:00+08:00"
E2E_T_WILTED = "2026-11-16T10:00:00+08:00"
E2E_T_OPEN = "2026-11-16T11:00:00+08:00"
E2E_SHOP_PREFIX = "v04_bookstore_shopkeeper:"
E2E_SHOP_ROUTE = "street_to_riverside"


def _snap(root: Path) -> dict[str, tuple[int, int, str]]:
    out: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            out[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            out[rel] = (0, int(stat.st_mtime_ns), "dir")
    return out


def _permitted_cfg(root: Path) -> dict[str, Any]:
    return {
        "day_root": str(root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {"enabled": True, "private_artifacts": True},
    }


def _descriptors(collected: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(desc)
        for desc in (collected.get("descriptors") or {}).values()
        if isinstance(desc, dict)
    ]


# ---------------------------------------------------------------------------
# Native-Windows direct rounds (no POSIX gate).
# ---------------------------------------------------------------------------


class WavesJToNDailyRoundDirectTests(unittest.TestCase):
    """Wave K/L/M through the real bridge + lived-context builder."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="v08-waves-j-n-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    # -- Wave K: wilting-recoverable plant becomes a rescuable cue ----------
    def test_branch_a_wilting_plant_recovers_and_context_reads_within_budget(
        self,
    ) -> None:
        (self.root / "phone").mkdir(parents=True)
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
            actor_side="partner",
            now_iso=T_WILT_BASE,
        )
        self.assertEqual(code, 200, body)
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {"action": "water_or_move_plant", "operation": "move", "target_place": "yard"},
            actor_side="subject",
            now_iso=T_WILT_BASE,
        )
        self.assertEqual(code, 200, body)

        nb_engine.advance_v04_neighborhood_processes(self.root, now_iso=T_WILTED)
        _, wilted = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T_WILTED
        )
        self.assertEqual(wilted["potted_plant"]["stage"], "wilting_recoverable")
        hydration_before = float(wilted["potted_plant"]["hydration"])

        before = _snap(self.root)
        collected = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T_WILTED)
        self.assertEqual(_snap(self.root), before, "collect must be zero-write")
        self.assertEqual(collected.get("llm_calls"), 0)

        plant_descs = [
            desc
            for desc in _descriptors(collected)
            if str(desc.get("source_ref") or "").startswith(PLANT_PREFIX)
        ]
        self.assertEqual(len(plant_descs), 1, collected)
        desc = plant_descs[0]

        event = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, desc["source_ref"]
        )
        self.assertIsInstance(event, dict, desc["source_ref"])
        self.assertEqual(event["v04_action"], "water_or_move_plant")

        settled = loop_bridge.settle_v02_v03_round_consequences(
            self.root, event, now_iso=T_WILTED
        )
        self.assertEqual(settled["status"], "APPLIED", settled)
        self.assertEqual(int(settled.get("llm_calls", 0)), 0)

        _, recovered = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T_WILTED
        )
        plant = recovered["potted_plant"]
        self.assertGreater(float(plant["hydration"]), hydration_before)
        self.assertNotEqual(plant["stage"], "wilting_recoverable")
        self.assertGreaterEqual(int(plant["wilt_recovered_count"]), 1)

        rendered = lcb.build_lived_turn_context(
            self.root, _permitted_cfg(self.root), now_iso=T_WILTED
        )
        self.assertIsInstance(rendered, str)
        self.assertLessEqual(len(rendered), 1800)
        self.assertLessEqual(len(rendered), lcb.MAX_LIVED_CONTEXT_CHARS)

    # -- Wave L: shared-notebook reply with no text soft-lands, zero write ---
    def test_branch_b_shared_notebook_without_text_soft_lands(self) -> None:
        (self.root / "phone").mkdir(parents=True)
        code, body = ep_view.mutate_v04_elastic_play_view(
            self.root,
            {"action": "append_shared_notebook", "text": "窗边那只灰雀，你看见了吗？"},
            actor_side="partner",
            now_iso=T_NOTEBOOK,
        )
        self.assertEqual(code, 200, body)

        collected = loop_bridge.collect_v02_v03_loop_candidates(
            self.root, now_iso=T_NOTEBOOK_COLLECT
        )
        note_descs = [
            desc
            for desc in _descriptors(collected)
            if desc.get("clue_domain") == SHARED_DOMAIN
        ]
        self.assertEqual(len(note_descs), 1, collected)
        desc = note_descs[0]

        event = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, desc["source_ref"]
        )
        self.assertIsInstance(event, dict, desc["source_ref"])
        self.assertEqual(event["action_kind"], "ELASTIC_SHARED_NOTEBOOK")

        before = _snap(self.root)
        soft = loop_bridge.settle_v02_v03_round_consequences(
            self.root, event, now_iso=T_NOTEBOOK_COLLECT
        )
        self.assertTrue(soft["soft_landed"], soft)
        self.assertFalse(soft["settled"], soft)
        self.assertEqual(soft["reason_code"], "ELASTIC_SHARED_NOTEBOOK_TEXT_MISSING")
        self.assertEqual(int(soft.get("optimistic_writes", 0)), 0)
        self.assertEqual(_snap(self.root), before, "a text-less reply must not write")

        # No fabricated subject entry was appended; the partner line stands alone.
        _, view = ep_view.read_v04_elastic_play_view(
            self.root, has_subject_capability=True
        )
        entries = view["data"]["shared_notebook"]["entries"]
        self.assertEqual(len(entries), 1, entries)
        self.assertEqual(entries[-1]["author"], "partner")
        self.assertEqual(entries[-1]["text"], "窗边那只灰雀，你看见了吗？")

    # -- Wave M: scalding drink refuses an immediate sip, never fakes it -----
    def test_branch_c_scalding_drink_holds_and_context_never_claims_a_sip(
        self,
    ) -> None:
        tea_root = self.root
        (tea_root / "phone").mkdir(parents=True)
        intro = matter.compose_open_act(
            tea_root,
            {
                "intent_cn": "沏了一杯刚烧开的热茶",
                "steps": [
                    {
                        "op": "INTRODUCE_ITEM",
                        "object_id": "tea-1",
                        "label_cn": "刚沏的热茶",
                        "item_kind": "drink",
                        "target_parent_id": "dining_table",
                        "portions": 2,
                        "thermal": "hot",
                        "boil_scalding": True,
                    }
                ],
            },
            now_iso=T_TEA,
        )
        self.assertEqual(intro["status"], "applied", intro)

        # The engine refuses the too-early sip with the honest 409.
        with self.assertRaises(matter.V08MatterError) as ctx:
            matter.compose_open_act(
                tea_root,
                {
                    "intent_cn": "立刻喝一口",
                    "steps": [{"op": "CONSUME_PORTION", "object_id": "tea-1"}],
                },
                now_iso=T_TEA,
            )
        self.assertEqual(ctx.exception.reason_code, "DRINK_SCALDING_WAIT")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self._tea_portions(T_TEA), 2)

        # The daily-loop bridge turns the same refusal into a gentle hold, still
        # with zero optimistic writes and no fake sip receipt.
        held = loop_bridge.execute_normalized_micro_intent(
            tea_root,
            {
                "intent_cn": "立刻喝一口",
                "steps": [{"op": "CONSUME_PORTION", "object_id": "tea-1"}],
            },
            now_iso=T_TEA,
        )
        self.assertFalse(held["ok"], held)
        self.assertTrue(held["soft_landed"], held)
        self.assertEqual(held["status"], "SOFT_LANDED")
        self.assertEqual(held["reason_code"], "DRINK_SCALDING_WAIT")
        self.assertFalse(held["drunk"])
        self.assertFalse(held["thirst_quenched"])
        self.assertNotIn("receipt", held)
        self.assertEqual(self._tea_portions(T_TEA), 2)

        # The next-turn context names the honest wait and never claims a sip.
        rendered = lcb.build_lived_turn_context(
            tea_root, _permitted_cfg(tea_root), now_iso=T_TEA
        )
        self.assertLessEqual(len(rendered), 1800)
        self.assertIn("手边热饮还烫嘴", rendered)
        for banned in ("已经喝", "喝下了", "喝完", "喝了一口"):
            self.assertNotIn(banned, rendered)

    def _tea_portions(self, now_iso: str) -> int:
        view = matter.read_matter_space_view(
            self.root, actor_side="subject", now_iso=now_iso
        )
        for row in view["objects"]:
            if row["object_id"] == "tea-1":
                return int(row["portions_left"])
        raise AssertionError("tea-1 missing from the matter projection")


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


class _ScriptedCognition:
    """Zero-product-model decision: only ever selects a candidate it was shown."""

    def __init__(self, *, select_prefix: str) -> None:
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
            model_id="stub:v08-waves-j-n",
            model_version="v08-waves-j-n-stub-v1",
            raw_output=raw,
            latency_ms=1,
        )


@posix_only
@needs_fixture
class WavesJToNCycleResidentE2ETests(_CycleTest):
    """Real cycle + real resident tick, scripted cognition, real owner writes."""

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
            return ticked.tick()
        finally:
            ticked.stop()

    def test_01_wilting_plant_recovers_through_resident_tick(self) -> None:
        cognition = _ScriptedCognition(select_prefix=PLANT_PREFIX)
        _data, env, cycle = self._build(cognition)
        inst = self._instance_root()
        (inst / "phone").mkdir(parents=True, exist_ok=True)

        code, body = mutate_v04_neighborhood_view(
            inst,
            {"action": "set_local_weather", "weather": "clear", "season": "autumn"},
            actor_side="partner",
            now_iso=E2E_T_BASE,
        )
        self.assertEqual(code, 200, body)
        code, body = mutate_v04_neighborhood_view(
            inst,
            {"action": "water_or_move_plant", "operation": "move", "target_place": "yard"},
            actor_side="subject",
            now_iso=E2E_T_BASE,
        )
        self.assertEqual(code, 200, body)
        nb_engine.advance_v04_neighborhood_processes(inst, now_iso=E2E_T_WILTED)
        _, wilted = read_v04_neighborhood_view(
            inst, has_subject_capability=True, now_iso=E2E_T_WILTED
        )
        self.assertEqual(wilted["potted_plant"]["stage"], "wilting_recoverable")
        hydration_before = float(wilted["potted_plant"]["hydration"])

        record = self._tick_built(env, cycle, E2E_T_WILTED)
        due = record.get("due") or {}
        self.assertEqual(due.get("decision"), "START", record)
        self.assertEqual(due.get("status"), "ACTED", record)

        _, recovered = read_v04_neighborhood_view(
            inst, has_subject_capability=True, now_iso=E2E_T_WILTED
        )
        plant = recovered["potted_plant"]
        self.assertGreater(float(plant["hydration"]), hydration_before)
        self.assertGreaterEqual(int(plant["wilt_recovered_count"]), 1)
        self.assertNotEqual(plant["stage"], "wilting_recoverable")

        # The partner's own read sees the same recovery (it is public state).
        _, partner = read_v04_neighborhood_view(
            inst, has_subject_capability=False, now_iso=E2E_T_WILTED
        )
        partner_plant = partner["potted_plant"]
        self.assertGreaterEqual(int(partner_plant["wilt_recovered_count"]), 1)
        self.assertNotEqual(partner_plant["stage"], "wilting_recoverable")

        rendered = lcb.build_lived_turn_context(
            inst, _permitted_cfg(inst), now_iso=E2E_T_WILTED
        )
        self.assertIsInstance(rendered, str)
        self.assertLessEqual(len(rendered), 1800)

    def test_02_shopkeeper_rumor_stays_unverified_hearsay_after_tick(self) -> None:
        cognition = _ScriptedCognition(select_prefix=E2E_SHOP_PREFIX)
        _data, env, cycle = self._build(cognition)
        inst = self._instance_root()
        (inst / "phone").mkdir(parents=True, exist_ok=True)

        code, body = mutate_v04_neighborhood_view(
            inst,
            {
                "action": "bookstore_service",
                "service": "share_shopkeeper_rumor",
                "route_id": E2E_SHOP_ROUTE,
            },
            actor_side="subject",
            now_iso=E2E_T_OPEN,
        )
        self.assertEqual(code, 200, body)
        self.assertIs(body["result"]["verified_firsthand"], False)

        collected = loop_bridge.collect_v02_v03_loop_candidates(inst, now_iso=E2E_T_OPEN)
        shop = [
            desc
            for desc in _descriptors(collected)
            if str(desc.get("source_ref") or "").startswith(E2E_SHOP_PREFIX)
        ]
        self.assertEqual(len(shop), 1, collected)

        self._tick_built(env, cycle, E2E_T_OPEN)

        _, view = read_v04_neighborhood_view(
            inst, has_subject_capability=True, now_iso=E2E_T_OPEN
        )
        rumors = view["bookstore"]["shopkeeper_life"]["heard_rumors"]
        self.assertTrue(rumors, view["bookstore"]["shopkeeper_life"])
        for row in rumors:
            self.assertIs(row["verified_firsthand"], False)

        rendered = lcb.build_lived_turn_context(
            inst, _permitted_cfg(inst), now_iso=E2E_T_OPEN
        )
        self.assertIn("听说:", rendered)
        self.assertIn("待实地核实", rendered)
        for banned in ("已核实", "已确认", "封路"):
            self.assertNotIn(banned, rendered)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""v3.0 Ch.02 Slow-Variable Incubation Kernel tests (added 2026-11-14).

Covers the ``slow_ledger`` layer added to ``components.life.scripts.v03_clock_evaporation``
on top of the three legacy physical micro slow variables (which stay untouched):

1. incubation -> threshold -> manifestation full chain (elapsed-driven only)
2. one-time manifestation receipts (replay can never physically write twice)
3. category cooldown families (commercial 120d / interpersonal 90d, hold-not-drop,
   family isolation)
4. monthly drama-entropy budget (<= 2 major / business month, overflow queued)
5. forced calm period (21d low-absorption window after a major manifestation)
6. same-seed determinism (identical sequences -> identical slow_ledger state)
7. causal edge registry (rule/witnessed/inferred grades, lag, append-only,
   inferred edges must carry their inference basis)
8. zero-RNG discipline (AST + source grep: no random/secrets anywhere)
9. perception tiers + v04 knowledge_status bridge (unknown -> told_clue only,
   "亲历前不得记安静度" untouched)
10. active variable budget (<= 12), drop_manifestation contract consumption

Run:  python -m unittest tests.test_v03_slow_variable_incubation -v
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components.life.scripts import v03_clock_evaporation as clock_evap  # noqa: E402
from components.world.scripts import v04_neighborhood_living as v04  # noqa: E402

WORLD_SEED = 929711
T0 = "2026-11-01T08:00:00+08:00"
DRIFT_PER_HOUR = 60
RIPEN_SECONDS = 7200  # 2h


def _expected_initial_milli(var_name: str, world_seed: int = WORLD_SEED) -> int:
    digest = hashlib.sha256(f"{world_seed}|{var_name}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % clock_evap.ECO_SEED_LATENT_MILLI_SPAN


def _advance(root: Path, now_iso: str) -> dict:
    status, envelope = clock_evap.mutate_v03_clock_view(
        root,
        {"action": "advance_projection", "sync_v02": False},
        actor_side="system",
        now_iso=now_iso,
    )
    assert status == 200 and envelope["ok"], envelope
    return envelope["data"]


def _declare(root: Path, var_name: str, *, now_iso: str, family: str = "commercial_vacancy",
             major: bool = False, tier: str = "near", scene: str | None = None,
             drift: int = DRIFT_PER_HOUR, ripen_after_seconds: int = RIPEN_SECONDS,
             world_seed: int | None = WORLD_SEED) -> tuple[str, dict]:
    """Declare an eco variable, then re-declare with a threshold that ripens it after ``ripen_after_seconds``."""
    payload: dict = {
        "action": "declare_eco_variable",
        "var_name": var_name,
        "label": f"慢变量{var_name}",
        "drift_milli_per_hour": drift,
        "cooldown_family": family,
        "entropy_major": major,
        "perception_tier": tier,
    }
    if scene:
        payload["manifest_scene"] = scene
    if world_seed is not None:
        payload["world_seed"] = world_seed
    first = clock_evap.apply_v03_clock_action(root, dict(payload), actor_side="system", now_iso=now_iso)
    assert first["ok"], first
    var_id = first["data"]["action_receipt"]["var_id"]
    initial = next(
        x["value_milli"] for x in first["data"]["slow_ledger"]["eco_variables"] if x["var_id"] == var_id
    )
    delta_milli = (drift * ripen_after_seconds) // 3600
    payload["var_id"] = var_id
    payload["manifest_threshold_milli"] = initial + delta_milli
    second = clock_evap.apply_v03_clock_action(root, payload, actor_side="system", now_iso=now_iso)
    assert second["ok"], second
    return var_id, first["data"]


def _shift(iso: str, seconds: int) -> str:
    from datetime import datetime, timedelta

    dt = datetime.fromisoformat(iso)
    return (dt + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _var_view(data: dict, var_id: str) -> dict:
    return next(x for x in data["slow_ledger"]["eco_variables"] if x["var_id"] == var_id)


class SlowVariableIncubationTest(unittest.TestCase):
    """Deterministic tests for the Ch.02 incubation kernel (zero RNG, zero LLM)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # Zero-RNG discipline
    # ------------------------------------------------------------------

    def test_01_zero_rng_ast_and_source_grep(self) -> None:
        targets = (
            REPO_ROOT / "components" / "life" / "scripts" / "v03_clock_evaporation.py",
            REPO_ROOT / "components" / "life" / "scripts" / "s07_affect_owner.py",
            REPO_ROOT / "components" / "world" / "scripts" / "v04_neighborhood_living.py",
        )
        forbidden_modules = {"random", "secrets", "numpy.random", "faker"}
        for path in targets:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name.split(".")[0], forbidden_modules,
                            f"{path.name} imports {alias.name}",
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(
                        node.module.split(".")[0], forbidden_modules,
                        f"{path.name} imports from {node.module}",
                    )
            source = path.read_text(encoding="utf-8")
            for needle in (
                "import random",
                "from random",
                "import secrets",
                "random.random",
                "getrandbits",
                "randrange",
                "randint",
                "shuffle(",
            ):
                self.assertNotIn(needle, source, f"{path.name} mentions {needle!r}")

    # ------------------------------------------------------------------
    # Seed derivation (ADR-038 spirit)
    # ------------------------------------------------------------------

    def test_02_seed_derived_initial_value_and_seed_immutability(self) -> None:
        var_id, data = _declare(self.root, "bookstore_transfer_pressure", now_iso=T0)
        var = _var_view(data, var_id)
        expected = _expected_initial_milli("bookstore_transfer_pressure")
        self.assertEqual(var["value_milli"], expected)
        self.assertTrue(0 <= expected < clock_evap.ECO_SEED_LATENT_MILLI_SPAN)
        self.assertEqual(data["slow_ledger"]["world_seed"], WORLD_SEED)
        self.assertEqual(var["phase"], "incubating")

        # same seed + same var_name on a fresh root -> identical initial value
        with tempfile.TemporaryDirectory() as other:
            other_id, other_data = _declare(Path(other), "bookstore_transfer_pressure", now_iso=T0)
            self.assertEqual(
                _var_view(other_data, other_id)["value_milli"], expected,
                "same seed must derive the same initial value (ADR-038)",
            )

        # the world seed is genesis-only: a different seed later is a conflict
        result = clock_evap.apply_v03_clock_action(
            self.root,
            {
                "action": "declare_eco_variable",
                "var_name": "another_street_var",
                "drift_milli_per_hour": 1,
                "cooldown_family": "interpersonal_move",
                "perception_tier": "mid",
                "world_seed": 12345,
            },
            actor_side="system",
            now_iso=T0,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "SLOW_LEDGER_SEED_IMMUTABLE")

    # ------------------------------------------------------------------
    # Incubation -> threshold -> manifestation full chain
    # ------------------------------------------------------------------

    def test_03_incubation_threshold_manifestation_full_chain(self) -> None:
        var_id, _ = _declare(
            self.root,
            "bookstore_transfer_pressure",
            now_iso=T0,
            major=True,
            scene="旧书店门上贴出转让告示",
        )
        t1 = _shift(T0, RIPEN_SECONDS)
        data = _advance(self.root, t1)

        counters = data["action_receipt"]["slow_ledger"]
        self.assertEqual(counters["ripened"], 1)
        self.assertEqual(counters["enqueued"], 1)
        self.assertEqual(counters["manifested"], 1)
        self.assertEqual(counters["held"], 0)

        var = _var_view(data, var_id)
        self.assertEqual(var["phase"], "settled")
        self.assertEqual(var["manifested_at"], t1)
        self.assertTrue(var["has_active_contract"])

        # threshold value crossed deterministically by integer milli drift
        expected_value = min(
            clock_evap.ECO_VALUE_MILLI_MAX,
            _expected_initial_milli("bookstore_transfer_pressure")
            + (DRIFT_PER_HOUR * RIPEN_SECONDS) // 3600,
        )
        self.assertEqual(var["value_milli"], expected_value)

        ledger = data["slow_ledger"]["manifestation_ledger"]
        self.assertEqual(len(ledger), 1)
        rec = ledger[0]
        self.assertEqual(rec["cooldown_family"], "commercial_vacancy")
        self.assertTrue(rec["entropy_major"])
        self.assertEqual(rec["perception_tier"], "near")
        self.assertEqual(rec["scene"], "旧书店门上贴出转让告示")
        self.assertTrue(rec["receipt"]["one_time"])
        self.assertEqual(rec["receipt"]["place_id"], "bookstore")
        self.assertEqual(data["slow_ledger"]["queue_status_counts"], {})

        # v04 received exactly one one-time physical state, through its owner port
        state, _exists = v04.load_state_readonly(self.root)
        states = state["places"]["bookstore"]["physical_states"]
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["source_ref"], rec["manifestation_id"])
        self.assertTrue(states[0]["one_time"])

        # the read projection is pure (no extra write happened on GET)
        before = (clock_evap.v03_clock_state_path(self.root)).read_text(encoding="utf-8")
        status, envelope = clock_evap.read_v03_clock_view(self.root, now_iso=t1)
        self.assertEqual(status, 200)
        self.assertEqual(
            clock_evap.v03_clock_state_path(self.root).read_text(encoding="utf-8"), before
        )
        self.assertEqual(envelope["data"]["slow_ledger"]["manifestation_ledger"][0]["manifestation_id"],
                         rec["manifestation_id"])

    # ------------------------------------------------------------------
    # One-time receipts
    # ------------------------------------------------------------------

    def test_04_manifestation_receipt_is_one_time(self) -> None:
        var_id, _ = _declare(self.root, "shopfront_change", now_iso=T0, major=False)
        t1 = _shift(T0, RIPEN_SECONDS)
        data = _advance(self.root, t1)
        manifestation_id = _var_view(data, var_id)["manifestation_id"]
        self.assertIsNotNone(manifestation_id)

        # replay the same advance instant: elapsed == 0, nothing new manifests
        data2 = _advance(self.root, t1)
        self.assertEqual(data2["action_receipt"]["slow_ledger"]["manifested"], 0)
        self.assertEqual(len(data2["slow_ledger"]["manifestation_ledger"]), 1)

        state, _ = v04.load_state_readonly(self.root)
        self.assertEqual(len(state["places"]["bookstore"]["physical_states"]), 1)

        # replaying the v04 owner command with the same operation key is deduplicated
        label = state["places"]["bookstore"]["physical_states"][0]["label"]
        status, result, _projection = v04.apply_v04_neighborhood_command(
            self.root,
            {
                "action": "record_poi_physical_state",
                "place_id": "bookstore",
                "state_id": f"poi_state_{manifestation_id}",
                "label": label,
                "source_ref": manifestation_id,
                "perception_tier": "near",
                "manifested_at": t1,
                "operation_key": f"v03-slow-var:{manifestation_id}",
            },
            actor_side="system",
            now_iso=t1,
        )
        self.assertEqual(status, "already_applied")
        state2, _ = v04.load_state_readonly(self.root)
        self.assertEqual(len(state2["places"]["bookstore"]["physical_states"]), 1)

        # a different operation key with the same one-time state_id is refused
        with self.assertRaises(v04.V04NeighborhoodError) as ctx:
            v04.apply_v04_neighborhood_command(
                self.root,
                {
                    "action": "record_poi_physical_state",
                    "place_id": "bookstore",
                    "state_id": f"poi_state_{manifestation_id}",
                    "label": label,
                    "source_ref": manifestation_id,
                    "perception_tier": "near",
                    "operation_key": "some-other-key",
                },
                actor_side="system",
                now_iso=t1,
            )
        self.assertEqual(ctx.exception.reason_code, "POI_STATE_EXISTS")

    # ------------------------------------------------------------------
    # Cooldown families (120d commercial / 90d interpersonal), hold-not-drop
    # ------------------------------------------------------------------

    def test_05_family_cooldown_holds_then_releases_with_family_isolation(self) -> None:
        var1, _ = _declare(self.root, "bookshop_pressure", now_iso=T0, family="commercial_vacancy", major=True)
        var2, _ = _declare(self.root, "second_shop_pressure", now_iso=T0, family="commercial_vacancy", major=False)
        var3, _ = _declare(self.root, "neighbor_move_rumor", now_iso=T0, family="interpersonal_move", major=False)

        t1 = _shift(T0, RIPEN_SECONDS)
        data = _advance(self.root, t1)
        counters = data["action_receipt"]["slow_ledger"]
        self.assertEqual(counters["manifested"], 2)  # var1 (major) + var3 (other family)
        self.assertEqual(counters["held"], 1)  # var2: same commercial family

        var2_view = _var_view(data, var2)
        self.assertEqual(var2_view["phase"], "ripe")
        entry = next(
            e for e in data["slow_ledger"]["manifestation_queue"] if e["var_id"] == var2
        )
        self.assertEqual(entry["status"], "queued")
        self.assertEqual(entry["hold_reason"], "family_cooldown")
        # held, not dropped: hold_until is exactly last commercial manifestation + 120d
        self.assertEqual(entry["hold_until"], _shift(t1, clock_evap.COOLDOWN_FAMILY_SECONDS["commercial_vacancy"]))

        # interpersonal cooldown: 90d.  var4 ripens 45d after var3's manifestation.
        t3 = _shift(t1, 45 * 86400)
        var4, first_data = _declare(self.root, "next_door_move", now_iso=t3, family="interpersonal_move", major=False)
        initial = _var_view(first_data, var4)["value_milli"]
        # raise the threshold so it ripens exactly +2h later (re-declare path)
        clock_evap.apply_v03_clock_action(
            self.root,
            {
                "action": "declare_eco_variable",
                "var_id": var4,
                "var_name": "next_door_move",
                "drift_milli_per_hour": DRIFT_PER_HOUR,
                "cooldown_family": "interpersonal_move",
                "perception_tier": "mid",
                "manifest_threshold_milli": initial + (DRIFT_PER_HOUR * RIPEN_SECONDS) // 3600,
            },
            actor_side="system",
            now_iso=t3,
        )
        t4 = _shift(t3, RIPEN_SECONDS)
        data3 = _advance(self.root, t4)
        entry4 = next(
            e for e in data3["slow_ledger"]["manifestation_queue"] if e["var_id"] == var4
        )
        self.assertEqual(entry4["hold_reason"], "family_cooldown")
        self.assertEqual(entry4["hold_until"], _shift(t1, clock_evap.COOLDOWN_FAMILY_SECONDS["interpersonal_move"]))

        # +91d: the interpersonal cooldown (90d) releases var4; var2 (commercial,
        # 120d) is still held
        t5 = _shift(t1, clock_evap.COOLDOWN_FAMILY_SECONDS["interpersonal_move"] + 86400)
        data4 = _advance(self.root, t5)
        self.assertEqual(_var_view(data4, var4)["phase"], "settled")
        entry2 = next(e for e in data4["slow_ledger"]["manifestation_queue"] if e["var_id"] == var2)
        self.assertEqual(entry2["status"], "queued")

        # +121d: the commercial cooldown releases var2
        t6 = _shift(t1, clock_evap.COOLDOWN_FAMILY_SECONDS["commercial_vacancy"] + 86400)
        data5 = _advance(self.root, t6)
        self.assertEqual(data5["action_receipt"]["slow_ledger"]["manifested"], 1)
        self.assertEqual(_var_view(data5, var2)["phase"], "settled")
        self.assertEqual(data5["slow_ledger"]["manifestation_queue"], [])

    # ------------------------------------------------------------------
    # Monthly drama-entropy budget (<= 2 major / business month)
    # ------------------------------------------------------------------

    def test_06_entropy_budget_monthly_cap(self) -> None:
        c1, _ = _declare(self.root, "shop_a_pressure", now_iso=T0, family="commercial_vacancy", major=True)
        i1, _ = _declare(self.root, "move_a_pressure", now_iso=T0, family="interpersonal_move", major=True)
        c2, _ = _declare(self.root, "shop_b_pressure", now_iso=T0, family="commercial_vacancy", major=True)

        t1 = _shift(T0, RIPEN_SECONDS)
        data = _advance(self.root, t1)
        counters = data["action_receipt"]["slow_ledger"]
        # two majors manifested, the third major of the same month queued
        self.assertEqual(counters["manifested"], 2)
        self.assertEqual(counters["held"], 1)
        entry = next(e for e in data["slow_ledger"]["manifestation_queue"] if e["var_id"] == c2)
        self.assertEqual(entry["hold_reason"], "entropy_budget")

        guards = data["slow_ledger"]["steady_state_guards"]
        self.assertEqual(guards["entropy_budget"]["monthly_major_cap"], 2)
        self.assertEqual(guards["entropy_budget"]["used_this_month"], 2)
        self.assertEqual(guards["entropy_budget"]["business_timezone"], "+08:00")
        self.assertEqual(guards["entropy_budget"]["month_key"], "2026-11")

        # next business month: the budget resets, but the family cooldown from c1
        # (30d ago) still holds the third major
        t2 = "2026-12-01T08:00:00+08:00"
        data2 = _advance(self.root, t2)
        entry2 = next(e for e in data2["slow_ledger"]["manifestation_queue"] if e["var_id"] == c2)
        self.assertEqual(entry2["status"], "queued")
        self.assertEqual(entry2["hold_reason"], "family_cooldown")

        # +121d after c1's manifestation: both guards pass
        t3 = _shift(t1, clock_evap.COOLDOWN_FAMILY_SECONDS["commercial_vacancy"] + 2 * 86400)
        data3 = _advance(self.root, t3)
        self.assertEqual(_var_view(data3, c2)["phase"], "settled")
        self.assertEqual(data3["slow_ledger"]["steady_state_guards"]["entropy_budget"]["used_this_month"], 1)

    # ------------------------------------------------------------------
    # Forced calm period (21d low-absorption window after a major)
    # ------------------------------------------------------------------

    def test_07_forced_calm_period_defers_major_enqueue(self) -> None:
        # A: interpersonal major, manifests at T1.
        a_id, _ = _declare(self.root, "move_a_pressure", now_iso=T0, family="interpersonal_move", major=True)
        t1 = _shift(T0, RIPEN_SECONDS)
        self.assertEqual(_advance(self.root, t1)["action_receipt"]["slow_ledger"]["manifested"], 1)

        # B: commercial major ripening 4h AFTER A's manifestation -> calm window blocks
        # the enqueue itself (the variable stays ripe, nothing is queued).
        b_id, _ = _declare(self.root, "shop_a_pressure", now_iso=T0, family="commercial_vacancy",
                           major=True, ripen_after_seconds=RIPEN_SECONDS + 4 * 3600)
        t2 = _shift(t1, 4 * 3600)
        data = _advance(self.root, t2)
        counters = data["action_receipt"]["slow_ledger"]
        self.assertEqual(counters["enqueue_held"], 1)
        self.assertEqual(counters["enqueued"], 0)
        b_view = _var_view(data, b_id)
        self.assertEqual(b_view["phase"], "ripe")
        self.assertEqual(b_view["enqueue_hold_reason"], "calm_period")
        self.assertEqual(data["slow_ledger"]["manifestation_queue"], [])

        # 21d calm window (design "2–4 周" -> 21d): after it passes, B enqueues and
        # manifests (fresh family, month budget still free).
        t3 = _shift(t1, clock_evap.CALM_PERIOD_SECONDS + 86400)
        data2 = _advance(self.root, t3)
        self.assertEqual(data2["action_receipt"]["slow_ledger"]["manifested"], 1)
        self.assertEqual(_var_view(data2, b_id)["phase"], "settled")
        calm = data2["slow_ledger"]["steady_state_guards"]["calm_period"]
        self.assertEqual(calm["days"], 21)
        self.assertIsNotNone(calm["calm_until"])

    # ------------------------------------------------------------------
    # Same seed -> same result (deterministic regression)
    # ------------------------------------------------------------------

    def test_08_same_seed_same_sequence_same_result(self) -> None:
        def run(root: Path) -> tuple[dict, dict]:
            _declare(root, "bookshop_pressure", now_iso=T0, family="commercial_vacancy", major=True)
            _declare(root, "neighbor_move_rumor", now_iso=T0, family="interpersonal_move", major=False)
            t1 = _shift(T0, RIPEN_SECONDS)
            _advance(root, t1)
            t2 = _shift(T0, RIPEN_SECONDS + 3600)
            data = _advance(root, t2)
            v04_state, _ = v04.load_state_readonly(root)
            return data["slow_ledger"], v04_state["places"]["bookstore"]["physical_states"]

        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b:
            ledger_a, states_a = run(Path(root_a))
            ledger_b, states_b = run(Path(root_b))
            self.assertEqual(
                json.dumps(ledger_a, sort_keys=True, ensure_ascii=False),
                json.dumps(ledger_b, sort_keys=True, ensure_ascii=False),
                "two identical runs from the same seed must produce identical ledger state",
            )
            self.assertEqual(
                json.dumps(states_a, sort_keys=True, ensure_ascii=False),
                json.dumps(states_b, sort_keys=True, ensure_ascii=False),
            )

    # ------------------------------------------------------------------
    # Causal edge registry
    # ------------------------------------------------------------------

    def test_09_causal_edge_registry_grades_lag_and_append_only(self) -> None:
        var_a, _ = _declare(self.root, "street_fabric_warmth", now_iso=T0, family="interpersonal_move", major=False)
        var_b, _ = _declare(self.root, "shopfront_pressure", now_iso=T0, family="commercial_vacancy", major=False)

        def edge(**payload: object) -> dict:
            payload["action"] = "add_causal_edge"
            return clock_evap.apply_v03_clock_action(self.root, payload, actor_side="system", now_iso=T0)

        # rule edge: structural cause, lag range, no refs needed
        ok = edge(edge_id="CE-001", from_var="street_fabric_warmth", to_var="shopfront_pressure",
                  grade="rule", lag_seconds=14 * 86400, lag_max_seconds=60 * 86400,
                  coupling="+0.08/unitΔ", condition="from.phase in [ripe, manifesting]")
        self.assertTrue(ok["ok"])
        self.assertFalse(ok["data"]["action_receipt"]["already_registered"])

        # witnessed edge with backing refs
        ok2 = edge(edge_id="CE-002", from_var="street_fabric_warmth", to_var="shopfront_pressure",
                   grade="witnessed", lag_seconds=3600, source_refs=["obs:2026-10-31:1", "mem:ep:77"])
        self.assertTrue(ok2["ok"])

        # inferred edge WITHOUT its inference basis -> refused (no fabrication)
        bad = edge(edge_id="CE-003", from_var="shopfront_pressure", to_var="street_fabric_warmth",
                   grade="inferred", lag_seconds=86400)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason_code"], "CAUSAL_INFERRED_REQUIRES_SOURCE_REFS")

        # inferred edge with refs is accepted
        ok3 = edge(edge_id="CE-003", from_var="shopfront_pressure", to_var="street_fabric_warmth",
                   grade="inferred", lag_seconds=86400, source_refs=["belief:41"])
        self.assertTrue(ok3["ok"])

        # invalid grade / inverted lag range refused
        bad_grade = edge(edge_id="CE-004", from_var="a", to_var="b", grade="guessed", lag_seconds=60)
        self.assertFalse(bad_grade["ok"])
        self.assertEqual(bad_grade["reason_code"], "CAUSAL_GRADE_INVALID")
        bad_lag = edge(edge_id="CE-004", from_var="a", to_var="b", grade="rule",
                       lag_seconds=60 * 86400, lag_max_seconds=86400)
        self.assertFalse(bad_lag["ok"])
        self.assertEqual(bad_lag["reason_code"], "CAUSAL_LAG_INVERTED")

        # append-only: identical replay is a no-op; content change is a conflict
        replay = edge(edge_id="CE-001", from_var="street_fabric_warmth", to_var="shopfront_pressure",
                      grade="rule", lag_seconds=14 * 86400, lag_max_seconds=60 * 86400,
                      coupling="+0.08/unitΔ", condition="from.phase in [ripe, manifesting]")
        self.assertTrue(replay["ok"])
        self.assertTrue(replay["data"]["action_receipt"]["already_registered"])
        mutated = edge(edge_id="CE-001", from_var="street_fabric_warmth", to_var="shopfront_pressure",
                       grade="rule", lag_seconds=15 * 86400, lag_max_seconds=60 * 86400)
        self.assertFalse(mutated["ok"])
        self.assertEqual(mutated["reason_code"], "CAUSAL_EDGE_IMMUTABLE")

        status, envelope = clock_evap.read_v03_clock_view(self.root, now_iso=T0)
        edges = envelope["data"]["slow_ledger"]["causal_edges"]
        self.assertEqual([e["edge_id"] for e in edges], ["CE-001", "CE-002", "CE-003"])
        self.assertEqual(edges[0]["lag_seconds"], 14 * 86400)
        self.assertEqual(edges[0]["lag_max_seconds"], 60 * 86400)
        self.assertEqual(edges[2]["grade"], "inferred")
        self.assertEqual(edges[2]["source_refs"], ["belief:41"])

    # ------------------------------------------------------------------
    # Perception tiers & the v04 knowledge_status bridge
    # ------------------------------------------------------------------

    def test_10_perception_tier_enum_and_knowledge_bridge(self) -> None:
        bad = clock_evap.apply_v03_clock_action(
            self.root,
            {
                "action": "declare_eco_variable",
                "var_name": "shop_pressure",
                "drift_milli_per_hour": 10,
                "cooldown_family": "commercial_vacancy",
                "perception_tier": "omniscient",
            },
            actor_side="system",
            now_iso=T0,
        )
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason_code"], "PERCEPTION_TIER_INVALID")

        bad_family = clock_evap.apply_v03_clock_action(
            self.root,
            {
                "action": "declare_eco_variable",
                "var_name": "shop_pressure2",
                "drift_milli_per_hour": 10,
                "cooldown_family": "sudden_viral_hit",
                "perception_tier": "near",
            },
            actor_side="system",
            now_iso=T0,
        )
        self.assertFalse(bad_family["ok"])
        self.assertEqual(bad_family["reason_code"], "COOLDOWN_FAMILY_INVALID")

        # bridge: put the bookstore into "unknown" first, then let a manifestation
        # surface it as rumor (told_clue) — never as visited, never with quietness.
        state, _ = v04.load_state_readonly(self.root)
        state["places"]["bookstore"]["knowledge_status"] = "unknown"
        state["places"]["bookstore"]["clue_note"] = None
        v04.save_state_atomic(self.root, state)

        var_id, _ = _declare(self.root, "shop_pressure3", now_iso=T0, major=False, tier="distant",
                             scene="旧书店门上贴出转让告示")
        t1 = _shift(T0, RIPEN_SECONDS)
        data = _advance(self.root, t1)
        rec = data["slow_ledger"]["manifestation_ledger"][0]
        self.assertEqual(rec["perception_tier"], "distant")
        self.assertEqual(rec["receipt"]["knowledge_status_after"], "told_clue")

        state2, _ = v04.load_state_readonly(self.root)
        place = state2["places"]["bookstore"]
        self.assertEqual(place["knowledge_status"], "told_clue")
        self.assertIn("转让告示", place["clue_note"])
        self.assertEqual(place["told_clue_from"], rec["manifestation_id"])
        self.assertNotEqual(place["knowledge_status"], "visited")
        # "亲历前不得记安静度": the manifestation never writes a quietness entry
        self.assertEqual(state2["public_life"]["place_booklet"]["entries"], {})

    # ------------------------------------------------------------------
    # Active variable budget & drop contract
    # ------------------------------------------------------------------

    def test_11_active_variable_budget_and_drop_manifestation(self) -> None:
        # 12 active variables are the Ch.02 hard budget; zero-drift vars never ripen
        for i in range(clock_evap.MAX_ACTIVE_ECO_VARIABLES):
            result = clock_evap.apply_v03_clock_action(
                self.root,
                {
                    "action": "declare_eco_variable",
                    "var_name": f"quiet_var_{i:02d}",
                    "drift_milli_per_hour": 0,
                    "cooldown_family": "commercial_vacancy" if i % 2 == 0 else "interpersonal_move",
                    "perception_tier": "mid",
                    "manifest_threshold_milli": clock_evap.ECO_VALUE_MILLI_MAX,
                },
                actor_side="system",
                now_iso=T0,
            )
            self.assertTrue(result["ok"], result)
        overflow = clock_evap.apply_v03_clock_action(
            self.root,
            {
                "action": "declare_eco_variable",
                "var_name": "one_var_too_many",
                "drift_milli_per_hour": 0,
                "cooldown_family": "commercial_vacancy",
                "perception_tier": "mid",
            },
            actor_side="system",
            now_iso=T0,
        )
        self.assertFalse(overflow["ok"])
        self.assertEqual(overflow["reason_code"], "SLOW_LEDGER_ACTIVE_BUDGET_EXCEEDED")

        # drop path: A (commercial minor) manifests at t1; B (same family) ripens
        # +2h later and is HELD by the family cooldown -> drop its queued contract
        with tempfile.TemporaryDirectory() as root2:
            drop_root = Path(root2)
            var_a, _ = _declare(drop_root, "first_shop_pressure", now_iso=T0, family="commercial_vacancy", major=False)
            var_b, _ = _declare(drop_root, "held_shop_pressure", now_iso=T0, family="commercial_vacancy",
                                major=False, ripen_after_seconds=RIPEN_SECONDS + 2 * 3600)
            t1 = _shift(T0, RIPEN_SECONDS)
            self.assertEqual(_advance(drop_root, t1)["action_receipt"]["slow_ledger"]["manifested"], 1)
            t2 = _shift(t1, 2 * 3600)
            data = _advance(drop_root, t2)
            self.assertEqual(data["action_receipt"]["slow_ledger"]["held"], 1)
            entry = next(
                e for e in data["slow_ledger"]["manifestation_queue"] if e["var_id"] == var_b
            )
            self.assertEqual(entry["status"], "queued")

            drop = clock_evap.apply_v03_clock_action(
                drop_root,
                {"action": "drop_manifestation", "manifestation_id": entry["manifestation_id"],
                 "drop_reason": "world_owner_cancelled"},
                actor_side="system",
                now_iso=t2,
            )
            self.assertTrue(drop["ok"])
            self.assertEqual(drop["data"]["action_receipt"]["status"], "dropped")

            # the dropped contract is terminal: no re-enqueue on later advances
            data2 = _advance(drop_root, _shift(t2, 86400))
            self.assertEqual(data2["action_receipt"]["slow_ledger"]["enqueued"], 0)
            self.assertEqual(data2["slow_ledger"]["queue_status_counts"], {"dropped": 1})
            view = _var_view(data2, var_b)
            self.assertEqual(view["phase"], "ripe")
            self.assertTrue(view["has_active_contract"])
            drop2 = clock_evap.apply_v03_clock_action(
                drop_root,
                {"action": "drop_manifestation", "manifestation_id": entry["manifestation_id"]},
                actor_side="system",
                now_iso=_shift(t2, 86400),
            )
            self.assertFalse(drop2["ok"])
            self.assertEqual(drop2["reason_code"], "MANIFESTATION_NOT_QUEUED")

    # ------------------------------------------------------------------
    # Legacy templates untouched
    # ------------------------------------------------------------------

    def test_12_legacy_physical_slow_variables_still_settle(self) -> None:
        result = clock_evap.apply_v03_clock_action(
            self.root,
            {"action": "declare_slow_variable", "var_kind": "damp_outerwear",
             "label": "外套", "initial_state": "damp", "started_at": T0},
            actor_side="system",
            now_iso=T0,
        )
        self.assertTrue(result["ok"])
        data = _advance(self.root, _shift(T0, clock_evap.OUTERWEAR_DRYING_SECONDS))
        coat = data["slow_variables"]["outerwear_drying"][0]
        self.assertEqual(coat["projected_dryness"], "dry")
        # and the kernel layer coexists with it (empty ledger, no crash)
        self.assertEqual(data["slow_ledger"]["eco_variables"], [])


if __name__ == "__main__":
    unittest.main()

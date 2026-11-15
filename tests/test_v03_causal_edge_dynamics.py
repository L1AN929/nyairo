#!/usr/bin/env python3
"""v3.0 Ch.02 §1.2 causal-edge -> dynamics coupling tests (maturation wave 2026-11-14).

Covers the maturation of the slow-variable incubation kernel on top of
``tests.test_v03_slow_variable_incubation`` (which keeps passing untouched):

1. rule edges (structural cause) set/narrow the target variable's drift band and
   volatility band ("商圈地铁施工等外部工程通过 rule 边压低人流") — declared slope
   stays immutable, effective slope is clamped in registry order, deterministically;
2. witnessed edges (first-hand transmission) inject ONE bounded one-shot drift
   pulse (|pulse| <= ``WITNESSED_PULSE_MILLI_LIMIT``, clamped into [0,1000],
   audited append-only in ``edge_effect_ledger``);
3. inferred edges NEVER enter dynamics (§1.2 "只进 S03/S05，绝不允许回写 S01
   世界账本") — they are projection-only and refuse any dynamics payload;
4. accumulated witnessed contact narrows volatility one closed tier per
   ``WITNESS_NARROW_THRESHOLD`` edges (§1.1 "volatility: 噪声带宽（千代接触时
   收窄）" — contact refines the world, closed tier ladder, floor 0);
5. manifestation contracts route to the POI named by ``binds`` (closed-validated
   against the v04 blueprint; undeclared keeps the bookstore default);
6. dispatch into a POI that is not currently open HOLDS the contract
   (``hold_reason="POI_CLOSED"``, queued for retry) — parallel to the
   family_cooldown / entropy_budget holds, never dropped;
7. same seed + same edge sequence -> identical ledger and v04 physical state.

Run:  python -m unittest tests.test_v03_causal_edge_dynamics -v
"""

from __future__ import annotations

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
T0 = "2026-11-01T08:00:00+08:00"      # bookstore CLOSED (opens 10:00)
T_OPEN = "2026-11-01T10:00:00+08:00"  # bookstore just opened
T_LATE = "2026-11-01T19:30:00+08:00"  # bookstore closed again (after 19:00)
T_NEXT_OPEN = "2026-11-02T10:00:00+08:00"


def _expected_initial_milli(var_name: str, world_seed: int = WORLD_SEED) -> int:
    digest = hashlib.sha256(f"{world_seed}|{var_name}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % clock_evap.ECO_SEED_LATENT_MILLI_SPAN


def _shift(iso: str, seconds: int) -> str:
    from datetime import datetime, timedelta

    dt = datetime.fromisoformat(iso)
    return (dt + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _declare(
    root: Path,
    var_name: str,
    *,
    now_iso: str,
    drift: int = 0,
    volatility: int = 0,
    family: str = "commercial_vacancy",
    tier: str = "near",
    binds: str | None = None,
    threshold_milli: int | None = None,
    ripen_after_seconds: int | None = None,
    major: bool = False,
    scene: str | None = None,
) -> tuple[str, dict]:
    """Declare an eco variable; optionally set a threshold that ripens it after ``ripen_after_seconds``."""
    payload: dict = {
        "action": "declare_eco_variable",
        "var_name": var_name,
        "label": f"慢变量{var_name}",
        "drift_milli_per_hour": drift,
        "volatility_milli": volatility,
        "cooldown_family": family,
        "entropy_major": major,
        "perception_tier": tier,
        "world_seed": WORLD_SEED,
    }
    if binds is not None:
        payload["binds"] = binds
    if scene is not None:
        payload["manifest_scene"] = scene
    first = clock_evap.apply_v03_clock_action(root, dict(payload), actor_side="system", now_iso=now_iso)
    assert first["ok"], first
    var_id = first["data"]["action_receipt"]["var_id"]
    initial = next(
        x["value_milli"] for x in first["data"]["slow_ledger"]["eco_variables"] if x["var_id"] == var_id
    )
    if threshold_milli is not None:
        payload["var_id"] = var_id
        payload["manifest_threshold_milli"] = threshold_milli
    elif ripen_after_seconds is not None:
        payload["var_id"] = var_id
        payload["manifest_threshold_milli"] = initial + (drift * ripen_after_seconds) // 3600
    if "var_id" in payload:
        second = clock_evap.apply_v03_clock_action(root, payload, actor_side="system", now_iso=now_iso)
        assert second["ok"], second
    return var_id, first["data"]


def _edge(root: Path, now_iso: str, **payload: object) -> dict:
    payload["action"] = "add_causal_edge"
    return clock_evap.apply_v03_clock_action(root, payload, actor_side="system", now_iso=now_iso)


def _advance(root: Path, now_iso: str) -> dict:
    status, envelope = clock_evap.mutate_v03_clock_view(
        root,
        {"action": "advance_projection", "sync_v02": False},
        actor_side="system",
        now_iso=now_iso,
    )
    assert status == 200 and envelope["ok"], envelope
    return envelope["data"]


def _var_view(data: dict, var_id: str) -> dict:
    return next(x for x in data["slow_ledger"]["eco_variables"] if x["var_id"] == var_id)


def _edge_effect_kinds(data: dict) -> list[str]:
    return [e["kind"] for e in data["slow_ledger"]["edge_effect_ledger"]]


class CausalEdgeDynamicsTest(unittest.TestCase):
    """Grade-gated causal-edge dynamics: rule bands, witnessed pulses, inferred isolation."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # 1. rule edges narrow the drift band and the volatility band
    # ------------------------------------------------------------------

    def test_01_rule_edge_narrows_drift_and_volatility_bands(self) -> None:
        var_id, _ = _declare(
            self.root, "shopfront_pressure", now_iso=T0, drift=60, volatility=400,
        )

        ok = _edge(
            self.root, T0, edge_id="CE-R1", from_var="subway_construction", to_var=var_id,
            grade="rule", lag_seconds=14 * 86400,
            drift_band_min_milli_per_hour=0, drift_band_max_milli_per_hour=20,
            volatility_band_min_milli=0, volatility_band_max_milli=256,
            coupling="-0.9x during construction",
        )
        self.assertTrue(ok["ok"], ok)
        # rule bands are applied at settlement time, never written at registration
        self.assertEqual(ok["data"]["action_receipt"]["effects_applied"], [])

        view = ok["data"]["slow_ledger"]["eco_variables"][0]
        self.assertEqual(view["drift_milli_per_hour"], 60)          # declared slope immutable
        self.assertEqual(view["effective_drift_milli_per_hour"], 20)  # clamped into band
        self.assertEqual(view["volatility_milli"], 400)             # live value untouched
        self.assertEqual(view["effective_volatility_milli"], 256)   # band projection

        # one hour of incubation drifts by the CLAMPED slope, not the declared one
        data = _advance(self.root, _shift(T0, 3600))
        var = _var_view(data, var_id)
        self.assertEqual(var["value_milli"], _expected_initial_milli("shopfront_pressure") + 20)
        counters = data["action_receipt"]["slow_ledger"]
        self.assertEqual(counters["rule_band_clamped"], 1)

        # a second rule edge narrows further, in registry order (sequential clamping)
        ok2 = _edge(
            self.root, T0, edge_id="CE-R2", from_var="street_closure", to_var=var_id,
            grade="rule", drift_band_min_milli_per_hour=0, drift_band_max_milli_per_hour=10,
        )
        self.assertTrue(ok2["ok"], ok2)
        data2 = _advance(self.root, _shift(T0, 2 * 3600))
        self.assertEqual(
            _var_view(data2, var_id)["value_milli"],
            _expected_initial_milli("shopfront_pressure") + 20 + 10,
        )

        # --- boundary validation with reason codes ---
        inverted = _edge(
            self.root, T0, edge_id="CE-BAD1", from_var="a", to_var=var_id, grade="rule",
            drift_band_min_milli_per_hour=30, drift_band_max_milli_per_hour=10,
        )
        self.assertFalse(inverted["ok"])
        self.assertEqual(inverted["reason_code"], "CAUSAL_DRIFT_BAND_INVERTED")
        out_of_range = _edge(
            self.root, T0, edge_id="CE-BAD2", from_var="a", to_var=var_id, grade="rule",
            drift_band_min_milli_per_hour=0, drift_band_max_milli_per_hour=1001,
        )
        self.assertFalse(out_of_range["ok"])
        self.assertEqual(out_of_range["reason_code"], "CAUSAL_DRIFT_BAND_INVALID")
        half_band = _edge(
            self.root, T0, edge_id="CE-BAD3", from_var="a", to_var=var_id, grade="rule",
            drift_band_min_milli_per_hour=0,
        )
        self.assertFalse(half_band["ok"])
        self.assertEqual(half_band["reason_code"], "CAUSAL_DRIFT_BAND_INVALID")
        vol_inverted = _edge(
            self.root, T0, edge_id="CE-BAD4", from_var="a", to_var=var_id, grade="rule",
            volatility_band_min_milli=300, volatility_band_max_milli=100,
        )
        self.assertFalse(vol_inverted["ok"])
        self.assertEqual(vol_inverted["reason_code"], "CAUSAL_VOLATILITY_BAND_INVERTED")
        vol_range = _edge(
            self.root, T0, edge_id="CE-BAD5", from_var="a", to_var=var_id, grade="rule",
            volatility_band_min_milli=-1, volatility_band_max_milli=100,
        )
        self.assertFalse(vol_range["ok"])
        self.assertEqual(vol_range["reason_code"], "CAUSAL_VOLATILITY_BAND_INVALID")
        on_witnessed = _edge(
            self.root, T0, edge_id="CE-BAD6", from_var="a", to_var=var_id, grade="witnessed",
            source_refs=["obs:1"],
            drift_band_min_milli_per_hour=0, drift_band_max_milli_per_hour=10,
        )
        self.assertFalse(on_witnessed["ok"])
        self.assertEqual(on_witnessed["reason_code"], "CAUSAL_BAND_GRADE_MISMATCH")
        unknown_target = _edge(
            self.root, T0, edge_id="CE-BAD7", from_var="a", to_var="SV-does-not-exist",
            grade="rule", drift_band_min_milli_per_hour=0, drift_band_max_milli_per_hour=10,
        )
        self.assertFalse(unknown_target["ok"])
        self.assertEqual(unknown_target["reason_code"], "CAUSAL_TARGET_VARIABLE_MISSING")

    # ------------------------------------------------------------------
    # 2. witnessed edges inject ONE bounded, audited drift pulse
    # ------------------------------------------------------------------

    def test_02_witnessed_pulse_bounded_one_shot_and_audited(self) -> None:
        var_id, first = _declare(
            self.root, "shopfront_pressure", now_iso=T0, drift=0, volatility=0,
        )
        value0 = _expected_initial_milli("shopfront_pressure")
        self.assertEqual(_var_view(first, var_id)["value_milli"], value0)

        ok = _edge(
            self.root, T0, edge_id="CE-W1", from_var="owner_talked_to_chiyono",
            to_var=var_id, grade="witnessed", lag_seconds=0, pulse_milli=50,
            source_refs=["obs:2026-11-01:1"],
        )
        self.assertTrue(ok["ok"], ok)
        effects = ok["data"]["action_receipt"]["effects_applied"]
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]["kind"], "drift_pulse")
        self.assertEqual(effects[0]["value_before"], value0)
        self.assertEqual(effects[0]["value_after"], value0 + 50)
        var = _var_view(ok["data"], var_id)
        self.assertEqual(var["value_milli"], value0 + 50)

        # append-only audit: exactly one pulse entry, linked to the edge
        audit = ok["data"]["slow_ledger"]["edge_effect_ledger"]
        self.assertEqual(_edge_effect_kinds(ok["data"]), ["drift_pulse"])
        self.assertEqual(audit[0]["edge_id"], "CE-W1")
        self.assertEqual(audit[0]["var_id"], var_id)
        self.assertEqual(audit[0]["delta_milli"], 50)
        self.assertTrue(audit[0]["effect_id"])

        # append-only replay: identical edge re-registration never re-pulses
        replay = _edge(
            self.root, T0, edge_id="CE-W1", from_var="owner_talked_to_chiyono",
            to_var=var_id, grade="witnessed", lag_seconds=0, pulse_milli=50,
            source_refs=["obs:2026-11-01:1"],
        )
        self.assertTrue(replay["ok"])
        self.assertTrue(replay["data"]["action_receipt"]["already_registered"])
        self.assertIsNone(replay["data"]["action_receipt"].get("effects_applied"))
        self.assertEqual(_var_view(replay["data"], var_id)["value_milli"], value0 + 50)
        self.assertEqual(_edge_effect_kinds(replay["data"]), ["drift_pulse"])

        # a second witnessed edge pulses again (one shot per edge, not per variable)
        ok2 = _edge(
            self.root, T0, edge_id="CE-W2", from_var="notice_board_seen",
            to_var=var_id, grade="witnessed", pulse_milli=100,
        )
        self.assertTrue(ok2["ok"])
        self.assertEqual(_var_view(ok2["data"], var_id)["value_milli"], value0 + 150)

        # --- boundary validation with reason codes ---
        too_big = _edge(
            self.root, T0, edge_id="CE-BADP1", from_var="a", to_var=var_id,
            grade="witnessed", pulse_milli=clock_evap.WITNESSED_PULSE_MILLI_LIMIT + 1,
        )
        self.assertFalse(too_big["ok"])
        self.assertEqual(too_big["reason_code"], "WITNESSED_PULSE_INVALID")
        on_rule = _edge(
            self.root, T0, edge_id="CE-BADP2", from_var="a", to_var=var_id,
            grade="rule", pulse_milli=10,
        )
        self.assertFalse(on_rule["ok"])
        self.assertEqual(on_rule["reason_code"], "CAUSAL_PULSE_GRADE_MISMATCH")
        missing_target = _edge(
            self.root, T0, edge_id="CE-BADP3", from_var="a", to_var="SV-ghost",
            grade="witnessed", pulse_milli=10,
        )
        self.assertFalse(missing_target["ok"])
        self.assertEqual(missing_target["reason_code"], "CAUSAL_TARGET_VARIABLE_MISSING")
        # failed pulses never registered the edge nor touched the audit trail
        status, fresh = clock_evap.read_v03_clock_view(self.root, now_iso=T0)
        self.assertEqual(status, 200)
        self.assertEqual(_edge_effect_kinds(fresh["data"]), ["drift_pulse", "drift_pulse"])
        edges_now = fresh["data"]["slow_ledger"]["causal_edges"]
        self.assertEqual([e["edge_id"] for e in edges_now], ["CE-W1", "CE-W2"])

        # upper clamp: pulses cannot push the value past the 0..1000 quantum ceiling
        pulses = 0
        data = ok2["data"]
        while _var_view(data, var_id)["value_milli"] < clock_evap.ECO_VALUE_MILLI_MAX:
            pulses += 1
            data = _edge(
                self.root, T0, edge_id=f"CE-UP{pulses:02d}", from_var="c",
                to_var=var_id, grade="witnessed", pulse_milli=100,
            )["data"]
            self.assertLessEqual(pulses, 15)
        self.assertEqual(_var_view(data, var_id)["value_milli"], clock_evap.ECO_VALUE_MILLI_MAX)

        # lower clamp: a negative pulse bottoms out at 0 (probe a seed name < 100)
        probe_name = None
        for i in range(64):
            candidate = f"floor_probe_{i:02d}"
            if _expected_initial_milli(candidate) < 100:
                probe_name = candidate
                break
        self.assertIsNotNone(probe_name)
        floor_id, floor_first = _declare(self.root, probe_name, now_iso=T0, drift=0, volatility=0)
        self.assertEqual(_var_view(floor_first, floor_id)["value_milli"], _expected_initial_milli(probe_name))
        floor_data = _edge(
            self.root, T0, edge_id="CE-DOWN1", from_var="c", to_var=floor_id,
            grade="witnessed", pulse_milli=-100,
        )["data"]
        self.assertEqual(_var_view(floor_data, floor_id)["value_milli"], 0)

        # frozen target: a settled variable is fixed into the static world layer
        # (own family, so the earlier commercial manifestation cannot hold it)
        settled_id, _ = _declare(
            self.root, "already_settled_var", now_iso=T0, drift=60,
            family="interpersonal_move", ripen_after_seconds=2 * 3600,
        )
        t1 = _advance(self.root, T_OPEN)  # 10:00 -> bookstore open -> manifestation lands
        self.assertEqual(_var_view(t1, settled_id)["phase"], "settled")
        frozen = _edge(
            self.root, T_OPEN, edge_id="CE-FROZEN", from_var="c", to_var=settled_id,
            grade="witnessed", pulse_milli=10,
        )
        self.assertFalse(frozen["ok"])
        self.assertEqual(frozen["reason_code"], "CAUSAL_TARGET_FROZEN")

    # ------------------------------------------------------------------
    # 3. inferred edges never enter dynamics (projection only)
    # ------------------------------------------------------------------

    def test_03_inferred_edges_never_enter_dynamics(self) -> None:
        var_id, _ = _declare(
            self.root, "shopfront_pressure", now_iso=T0, drift=60, volatility=400,
        )
        ok = _edge(
            self.root, T0, edge_id="CE-I1", from_var="shopfront_pressure",
            to_var=var_id, grade="inferred", lag_seconds=86400,
            source_refs=["belief:41"],
        )
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(ok["data"]["action_receipt"]["effects_applied"], [])

        # one hour: pure declared drift; the inferred edge contributed nothing
        data = _advance(self.root, _shift(T0, 3600))
        self.assertEqual(
            _var_view(data, var_id)["value_milli"],
            _expected_initial_milli("shopfront_pressure") + 60,
        )
        self.assertEqual(_edge_effect_kinds(data), [])
        self.assertEqual(data["action_receipt"]["slow_ledger"]["rule_band_clamped"], 0)

        # inference may never carry world-side dynamics — not even a zero pulse
        bad_pulse = _edge(
            self.root, T0, edge_id="CE-I2", from_var="a", to_var=var_id,
            grade="inferred", pulse_milli=0, source_refs=["belief:42"],
        )
        self.assertFalse(bad_pulse["ok"])
        self.assertEqual(bad_pulse["reason_code"], "CAUSAL_INFERRED_NO_DYNAMICS")
        bad_band = _edge(
            self.root, T0, edge_id="CE-I3", from_var="a", to_var=var_id,
            grade="inferred", volatility_band_min_milli=0, volatility_band_max_milli=0,
            source_refs=["belief:43"],
        )
        self.assertFalse(bad_band["ok"])
        self.assertEqual(bad_band["reason_code"], "CAUSAL_INFERRED_NO_DYNAMICS")

    # ------------------------------------------------------------------
    # 4. accumulated witnessed contact narrows volatility one closed tier
    # ------------------------------------------------------------------

    def test_04_witnessed_accumulation_narrows_volatility_closed_tiers(self) -> None:
        var_id, first = _declare(
            self.root, "shopfront_pressure", now_iso=T0, drift=0, volatility=400,
        )
        self.assertEqual(_var_view(first, var_id)["volatility_milli"], 400)

        expected_narrows = []
        current = 400
        data = first
        for i in range(1, 17):
            data = _edge(
                self.root, T0, edge_id=f"CE-C{i:02d}", from_var=f"visit_{i}",
                to_var=var_id, grade="witnessed",
            )["data"]
            var = _var_view(data, var_id)
            self.assertEqual(var["witnessed_contact_count"], i)
            if i % clock_evap.WITNESS_NARROW_THRESHOLD == 0 and current > 0:
                narrowed = clock_evap._narrow_volatility_one_tier(current)
                self.assertEqual(var["volatility_milli"], narrowed)
                expected_narrows.append((current, narrowed))
                current = narrowed
            else:
                self.assertEqual(var["volatility_milli"], current)

        # closed ladder walked to the floor: 400->256->128->64->32->16->8->0
        self.assertEqual(expected_narrows, [
            (400, 256), (256, 128), (128, 64), (64, 32), (32, 16), (16, 8), (8, 0),
        ])
        self.assertEqual(_var_view(data, var_id)["volatility_milli"], 0)
        self.assertEqual(_var_view(data, var_id)["declared_volatility_milli"], 400)
        # every narrowing is audited append-only, linked to its witnessed edge
        audit = data["slow_ledger"]["edge_effect_ledger"]
        self.assertEqual(_edge_effect_kinds(data), ["volatility_narrow"] * 7)
        self.assertEqual(
            [(e["from_milli"], e["to_milli"]) for e in audit], expected_narrows,
        )
        self.assertEqual(audit[0]["edge_id"], "CE-C02")
        # declared volatility stays recoverable; live value is the narrowed one
        view = data["slow_ledger"]["eco_variables"][0]
        self.assertEqual(view["effective_volatility_milli"], 0)

    # ------------------------------------------------------------------
    # 5. binds routes the manifestation to the target POI (closed validation)
    # ------------------------------------------------------------------

    def test_05_binds_routes_manifestation_and_validates_closed_set(self) -> None:
        # closed validation: binds must be an existing v04 place
        for bad_binds in ("attic", "BOOKSTORE", "riverside_x"):
            bad = clock_evap.apply_v03_clock_action(
                self.root,
                {
                    "action": "declare_eco_variable",
                    "var_name": f"bad_binds_var_{bad_binds}",
                    "drift_milli_per_hour": 10,
                    "cooldown_family": "commercial_vacancy",
                    "perception_tier": "near",
                    "binds": bad_binds,
                },
                actor_side="system",
                now_iso=T0,
            )
            self.assertFalse(bad["ok"])
            self.assertEqual(bad["reason_code"], "SLOW_VAR_BINDS_INVALID")

        # riverside ripens at 09:00 while the bookstore is closed — riverside has
        # no open window, so the manifestation lands immediately there
        var_id, _ = _declare(
            self.root, "riverside_junk_pile", now_iso=T0, drift=60,
            binds="riverside", ripen_after_seconds=3600,
            scene="河边长椅边多出一摞清仓纸箱",
        )
        data = _advance(self.root, _shift(T0, 3600))
        counters = data["action_receipt"]["slow_ledger"]
        self.assertEqual(counters["manifested"], 1)
        self.assertEqual(counters["poi_closed_held"], 0)
        self.assertEqual(_var_view(data, var_id)["phase"], "settled")
        rec = data["slow_ledger"]["manifestation_ledger"][0]
        self.assertEqual(rec["receipt"]["place_id"], "riverside")

        state, _ = v04.load_state_readonly(self.root)
        self.assertEqual(len(state["places"]["riverside"]["physical_states"]), 1)
        self.assertEqual(state["places"]["riverside"]["physical_states"][0]["source_ref"], rec["manifestation_id"])
        self.assertEqual(len(state["places"]["bookstore"]["physical_states"]), 0)
        # the knowledge bridge works on the bound POI, not just the bookstore
        self.assertEqual(state["places"]["riverside"]["knowledge_status"], "told_clue")

        # undeclared binds keeps the bookstore-compatible default (own family so
        # the earlier riverside commercial manifestation cannot hold it)
        default_id, _ = _declare(
            self.root, "default_route_var", now_iso=T0, drift=60,
            family="interpersonal_move", ripen_after_seconds=2 * 3600,  # 10:00, open
        )
        data2 = _advance(self.root, T_OPEN)
        self.assertEqual(_var_view(data2, default_id)["phase"], "settled")
        rec2 = data2["slow_ledger"]["manifestation_ledger"][-1]
        self.assertEqual(rec2["receipt"]["place_id"], "bookstore")

    # ------------------------------------------------------------------
    # 6. dispatch into a closed POI holds the contract (POI_CLOSED), retry later
    # ------------------------------------------------------------------

    def test_06_poi_closed_holds_manifestation_until_reopen(self) -> None:
        # morning case: ripens 09:00 (bookstore closed until 10:00) -> held
        var_id, _ = _declare(
            self.root, "morning_transfer_pressure", now_iso=T0, drift=60,
            binds="bookstore", ripen_after_seconds=3600,
        )
        data = _advance(self.root, _shift(T0, 3600))
        counters = data["action_receipt"]["slow_ledger"]
        self.assertEqual(counters["ripened"], 1)
        self.assertEqual(counters["enqueued"], 1)
        self.assertEqual(counters["poi_closed_held"], 1)
        self.assertEqual(counters["manifested"], 0)
        var = _var_view(data, var_id)
        self.assertEqual(var["phase"], "ripe")  # held, not dropped
        entry = next(e for e in data["slow_ledger"]["manifestation_queue"] if e["var_id"] == var_id)
        self.assertEqual(entry["status"], "queued")
        self.assertEqual(entry["hold_reason"], "POI_CLOSED")
        self.assertEqual(entry["hold_until"], T_OPEN)
        self.assertEqual(entry["binds"], "bookstore")
        state, _ = v04.load_state_readonly(self.root)
        self.assertEqual(len(state["places"]["bookstore"]["physical_states"]), 0)

        # next round after reopening: the queued contract retries and manifests
        data2 = _advance(self.root, T_OPEN)
        counters2 = data2["action_receipt"]["slow_ledger"]
        self.assertEqual(counters2["manifested"], 1)
        self.assertEqual(counters2["poi_closed_held"], 0)
        self.assertEqual(_var_view(data2, var_id)["phase"], "settled")
        self.assertEqual(data2["slow_ledger"]["queue_status_counts"], {})
        state2, _ = v04.load_state_readonly(self.root)
        self.assertEqual(len(state2["places"]["bookstore"]["physical_states"]), 1)

        # evening case: ripens 19:30 (after 19:00) -> held until NEXT day 10:00
        with tempfile.TemporaryDirectory() as root2:
            evening_root = Path(root2)
            ev_id, _ = _declare(
                evening_root, "evening_transfer_pressure", now_iso=T0, drift=30,
                binds="bookstore", ripen_after_seconds=11 * 3600 + 1800,
            )
            ev_data = _advance(evening_root, T_LATE)
            ev_counters = ev_data["action_receipt"]["slow_ledger"]
            self.assertEqual(ev_counters["poi_closed_held"], 1)
            ev_entry = next(
                e for e in ev_data["slow_ledger"]["manifestation_queue"] if e["var_id"] == ev_id
            )
            self.assertEqual(ev_entry["hold_reason"], "POI_CLOSED")
            self.assertEqual(ev_entry["hold_until"], T_NEXT_OPEN)
            self.assertEqual(_var_view(ev_data, ev_id)["phase"], "ripe")
            ev_data2 = _advance(evening_root, T_NEXT_OPEN)
            self.assertEqual(ev_data2["action_receipt"]["slow_ledger"]["manifested"], 1)
            self.assertEqual(_var_view(ev_data2, ev_id)["phase"], "settled")

    # ------------------------------------------------------------------
    # 7. witnessed pulse can push a variable over its threshold (full chain)
    # ------------------------------------------------------------------

    def test_07_witnessed_pulse_drives_threshold_crossing(self) -> None:
        value0 = _expected_initial_milli("pulse_ripe_var")
        var_id, _ = _declare(
            self.root, "pulse_ripe_var", now_iso=T0, drift=0, binds="riverside",
            threshold_milli=value0 + 50,
        )
        ok = _edge(
            self.root, T0, edge_id="CE-P1", from_var="regular_customer_told_owner",
            to_var=var_id, grade="witnessed", pulse_milli=50,
        )
        self.assertTrue(ok["ok"])
        self.assertEqual(_var_view(ok["data"], var_id)["value_milli"], value0 + 50)

        data = _advance(self.root, _shift(T0, 3600))
        counters = data["action_receipt"]["slow_ledger"]
        self.assertEqual(counters["ripened"], 1)
        self.assertEqual(counters["enqueued"], 1)
        self.assertEqual(counters["manifested"], 1)
        self.assertEqual(_var_view(data, var_id)["phase"], "settled")
        rec = data["slow_ledger"]["manifestation_ledger"][0]
        self.assertEqual(rec["receipt"]["place_id"], "riverside")

    # ------------------------------------------------------------------
    # 8. same seed + same edge sequence -> identical result (determinism)
    # ------------------------------------------------------------------

    def test_08_same_seed_same_edge_sequence_identical_result(self) -> None:
        def run(root: Path) -> tuple[dict, list]:
            var_id, _ = _declare(
                root, "determinism_var", now_iso=T0, drift=0, volatility=400,
                binds="riverside", threshold_milli=_expected_initial_milli("determinism_var") + 60,
            )
            _edge(
                root, T0, edge_id="CE-R1", from_var="subway_construction", to_var=var_id,
                grade="rule", drift_band_min_milli_per_hour=0, drift_band_max_milli_per_hour=10,
            )
            _edge(
                root, T0, edge_id="CE-W1", from_var="visit_a", to_var=var_id,
                grade="witnessed", pulse_milli=30,
            )
            _edge(
                root, T0, edge_id="CE-W2", from_var="visit_b", to_var=var_id,
                grade="witnessed",
            )
            _advance(root, _shift(T0, 3600))
            _edge(
                root, _shift(T0, 3600), edge_id="CE-W3", from_var="visit_c",
                to_var=var_id, grade="witnessed", pulse_milli=30,
            )
            data = _advance(root, _shift(T0, 2 * 3600))
            v04_state, _ = v04.load_state_readonly(root)
            return data["slow_ledger"], v04_state["places"]["riverside"]["physical_states"]

        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b:
            ledger_a, states_a = run(Path(root_a))
            ledger_b, states_b = run(Path(root_b))
            self.assertEqual(
                json.dumps(ledger_a, sort_keys=True, ensure_ascii=False),
                json.dumps(ledger_b, sort_keys=True, ensure_ascii=False),
                "identical seed + identical edge sequence must give identical ledger state",
            )
            self.assertEqual(
                json.dumps(states_a, sort_keys=True, ensure_ascii=False),
                json.dumps(states_b, sort_keys=True, ensure_ascii=False),
            )
            # the run itself: rule band kept drift at 0, pulses drove the crossing
            settled = ledger_a["manifestation_ledger"][0]
            self.assertEqual(settled["receipt"]["place_id"], "riverside")
            effects = ledger_a["edge_effect_ledger"]
            self.assertEqual(_edge_effect_kinds({"slow_ledger": ledger_a}), [
                "drift_pulse", "volatility_narrow", "drift_pulse",
            ])
            self.assertEqual(effects[0]["edge_id"], "CE-W1")
            self.assertEqual(effects[1]["edge_id"], "CE-W2")


if __name__ == "__main__":
    unittest.main()

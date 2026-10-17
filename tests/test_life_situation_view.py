#!/usr/bin/env python3
"""PERCEPT/SITUATION-1 tests: the injectable situation read.

What is real here
-----------------
* the Body column comes from the **real** read path — real ``body_runtime`` state
  → real ``WorldBodyService.body_summary`` → real ``GatewaySurface`` → real unix
  socket → real ``WorldBodyClient`` → real ``WorldBodyReadPort`` → the real
  ``body_capacity_source`` adapter (the bridge fixture is imported from
  ``tests/test_body_capacity_source.py``, this package's own test, rather than
  copied);
* the budget column is fed by the **real** ``LifeRequestBudget.view()`` /
  ``check()`` reads over a ledger fixture in this test's own temporary root — the
  fixture is written by this test, and the situation view itself never spends;
* the AG-1 seam runs the real ``CapacityEvaluator``.

Nothing here claims a live deployment, a production profile or a real model.

Run:  python3 tests/test_life_situation_view.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

import body_capacity_source as bcs  # noqa: E402
import life_request_budget as lrb  # noqa: E402
import life_situation_view as lsv  # noqa: E402


def _load_by_path(name: str, path: Path):
    """Load one module from an explicit file (no name ambiguity)."""

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: This package's own Body-bridge test module: the real socket fixture, the real
#: service shim and the real burdened-body builder are reused, not re-implemented.
bridge = _load_by_path("situation_body_bridge_fixture", ROOT / "tests" / "test_body_capacity_source.py")

#: The session's native fixture root (unique per work package).  Temporary dirs go
#: under it when it exists; nothing pre-existing is ever removed.
NATIVE_FIXTURE_ROOT = Path("/tmp/nyaw05r.jdK3q")

SUBJECT = "chiyo.global"


def _fixture_dir(prefix: str) -> tempfile.TemporaryDirectory:
    base = NATIVE_FIXTURE_ROOT if NATIVE_FIXTURE_ROOT.is_dir() else None
    return tempfile.TemporaryDirectory(prefix=prefix, dir=str(base) if base else None)


def body_view(
    state: str,
    *,
    ref: str | None = None,
    reason: str = "TEST_FIXTURE",
    freshness: str | None = "fresh",
    evidence=(),
) -> dict:
    """A body-capacity view of the shape the real adapter returns.

    Used only where the test is about the *other* columns; the tests that are
    about the Body column go through the real bridge instead.
    """

    return {
        "schema_version": bcs.SCHEMA_BODY_CAPACITY_VIEW,
        "kind": bcs.KIND_BODY_CAPACITY_VIEW,
        "source": "world_body_read_port.get_body_signals",
        "subject_id": SUBJECT,
        "home_ref": "home:fixture",
        "source_outcome": "OK",
        "degraded": False,
        "freshness": freshness,
        "observed_at": None,
        "observed_body_id": "chiyo_body",
        "observed_revision": "bodyrev:3",
        "observed_recovery_state": None,
        "identity_bound": True,
        "revision_bound": True,
        "observation_time_bound": True,
        "body_capacity_state": state,
        "body_capacity_ref": ref,
        "reason_code": reason,
        "evidence": list(evidence),
        "qualitative_only": True,
        "raw_values_exposed": False,
        "read_only": True,
        "reads_performed": 1,
        "writes_performed": 0,
        "mutations_attempted": 0,
        "candidates_emitted": 0,
        "actions_authorized": 0,
    }


class SituationTestCase(unittest.TestCase):
    """Shared lifecycle: temporary homes and closed sockets per test."""

    def setUp(self) -> None:
        self._servers: list = []
        self._dirs: list[tempfile.TemporaryDirectory] = []

    def tearDown(self) -> None:
        for server in self._servers:
            server.close()
        for handle in self._dirs:
            handle.cleanup()

    def make_home(self) -> Path:
        handle = _fixture_dir("a-situation-home-")
        self._dirs.append(handle)
        return Path(handle.name)

    def make_dir(self, prefix: str) -> Path:
        handle = _fixture_dir(prefix)
        self._dirs.append(handle)
        return Path(handle.name)

    def make_port(self, state, moment):
        surface = bridge.GatewaySurface(bridge.service_shim(state, moment))
        server = bridge.UnixSocketGatewayServer(surface)
        self._servers.append(server)
        client = bridge.wbc.WorldBodyClient(
            socket_path=server.path, mode=bridge.wbc.MODE_CANONICAL, read_retries=0
        )
        return bridge.wbrp.WorldBodyReadPort(client), server


# ---------------------------------------------------------------------------
# 1. the real Body read reaches the body column, and only the body column
# ---------------------------------------------------------------------------


class RealBodyColumn(SituationTestCase):
    def test_real_burdened_body_is_limited_and_costs_one_read(self):
        home = self.make_home()
        state, moment = bridge.burdened_state(3)
        port, server = self.make_port(state, moment)

        view = lsv.read_life_situation(
            home=home, body_port=port, now=moment.isoformat(),
            budget={"source": "life_request_budget.check", "allowed": True,
                    "state": "ok", "utc_day": "2026-10-16", "utc_day_calls": 0},
        )

        self.assertEqual(view["schema_version"], lsv.SCHEMA_LIFE_SITUATION_VIEW)
        self.assertEqual(view["body"]["state"], bcs.CAPACITY_LIMITED)
        self.assertEqual(view["body"]["ref"], "bodycap:chiyo_body:bodyrev:3")
        self.assertEqual(view["body"]["evidence"], ["fatigue_noticeable:mild"])
        self.assertEqual(view["body"]["reason_code"], bcs.REASON_OK_LIMITED)
        self.assertEqual(view["body"]["derived_by"], "body_capacity_source.read_body_capacity")
        self.assertEqual(view["dimensions"][lsv.DIMENSION_BODY], bcs.CAPACITY_LIMITED)
        self.assertEqual(view["limiting_dimensions"], [lsv.DIMENSION_BODY])
        self.assertEqual(view["reads_performed"], 1)
        self.assertEqual(server.commands, ["get_body_signals"])
        self.assertEqual(view["subject_id"], SUBJECT)
        self.assertTrue(view["identity_bound"])
        self.assertEqual(view["observed_at"], moment.isoformat())

    def test_two_evidenced_bodies_constrain_differently(self):
        home = self.make_home()
        burdened, moment = bridge.burdened_state(3)
        port_a, _ = self.make_port(burdened, moment)
        resting = bridge.br.initial_runtime(at="2026-10-16T00:00:00+00:00")
        port_b, _ = self.make_port(resting, moment)

        view_a = lsv.read_life_situation(
            home=home, body_port=port_a, now=moment.isoformat(),
            budget={"source": "life_request_budget.check", "allowed": True, "state": "ok",
                    "utc_day": "2026-10-16", "utc_day_calls": 0},
        )
        view_b = lsv.read_life_situation(
            home=home, body_port=port_b, now=moment.isoformat(),
            budget={"source": "life_request_budget.check", "allowed": True, "state": "ok",
                    "utc_day": "2026-10-16", "utc_day_calls": 0},
        )

        # A: evidenced burden constrains; B: no burden evidence is UNKNOWN, and it
        # is not a limit and certainly not health.
        self.assertEqual(view_a["limiting_dimensions"], [lsv.DIMENSION_BODY])
        self.assertEqual(view_a["unknown_dimensions"], [])
        self.assertEqual(view_b["limiting_dimensions"], [])
        self.assertEqual(view_b["unknown_dimensions"], [lsv.DIMENSION_BODY])
        self.assertEqual(view_b["body"]["state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view_b["body"]["reason_code"], bcs.REASON_NO_BURDEN_EVIDENCE)
        self.assertNotEqual(view_a["body"]["state"], bcs.CAPACITY_AVAILABLE)
        self.assertNotEqual(view_b["body"]["state"], bcs.CAPACITY_AVAILABLE)

    def test_a_stale_body_read_is_unknown_never_health(self):
        home = self.make_home()
        state, moment = bridge.burdened_state(3)
        port, _ = self.make_port(state, moment)

        view = lsv.read_life_situation(
            home=home, body_port=port, now=(moment + timedelta(hours=20)).isoformat()
        )

        self.assertEqual(view["body"]["state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["body"]["reason_code"], bcs.REASON_VIEW_STALE)
        self.assertEqual(view["body"]["freshness"], bridge.bread.FRESHNESS_STALE)
        self.assertIn(lsv.DIMENSION_BODY, view["unknown_dimensions"])
        self.assertEqual(view["blocking_dimensions"], [])
        self.assertNotEqual(view["body"]["state"], bcs.CAPACITY_AVAILABLE)

    def test_no_body_input_is_unknown_and_never_available(self):
        home = self.make_home()

        view = lsv.read_life_situation(home=home, now="2026-10-16T06:00:00+00:00")

        self.assertEqual(view["body"]["state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["body"]["reason_code"], lsv.REASON_BODY_SOURCE_NOT_INJECTED)
        self.assertIsNone(view["body"]["ref"])
        self.assertEqual(view["reads_performed"], 0)
        self.assertIn(lsv.DIMENSION_BODY, view["unknown_dimensions"])
        self.assertNotEqual(view["body"]["state"], bcs.CAPACITY_AVAILABLE)

    def test_a_malformed_body_view_is_refused_not_trusted(self):
        home = self.make_home()

        view = lsv.read_life_situation(
            home=home, body=body_view("BROKEN"), now="2026-10-16T06:00:00+00:00"
        )

        self.assertEqual(view["body"]["state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(view["body"]["reason_code"], lsv.REASON_BODY_VIEW_INVALID)


# ---------------------------------------------------------------------------
# 2. the same body under different budgets: the body facts do not move
# ---------------------------------------------------------------------------


class BodyAndBudgetStaySeparate(SituationTestCase):
    def test_changing_the_budget_does_not_change_the_body_column(self):
        home = self.make_home()
        state, moment = bridge.burdened_state(3)
        port, _ = self.make_port(state, moment)

        generous = {"source": "life_request_budget.check", "allowed": True, "state": "ok",
                    "utc_day": "2026-10-16", "utc_day_calls": 1, "utc_day_max": 8,
                    "run_id": "run:a", "run_window_calls": 1, "run_window_max": 8}
        exhausted = {"source": "life_request_budget.check", "allowed": False, "state": "ok",
                     "reason_code": lrb.REASON_UTC_DAY_LIMIT, "utc_day": "2026-10-16",
                     "utc_day_calls": 8, "utc_day_max": 8, "run_id": "run:a",
                     "run_window_calls": 8, "run_window_max": 8}

        with_budget = lsv.read_life_situation(
            home=home, body_port=port, now=moment.isoformat(), budget=generous
        )
        without_budget = lsv.read_life_situation(
            home=home, body_port=port, now=moment.isoformat(), budget=exhausted
        )

        self.assertEqual(with_budget["body"], without_budget["body"])
        self.assertEqual(
            with_budget["source_revisions"][lsv.DIMENSION_BODY],
            without_budget["source_revisions"][lsv.DIMENSION_BODY],
        )
        self.assertNotEqual(
            with_budget["resource_budget"]["state"], without_budget["resource_budget"]["state"]
        )
        # the body column carries no budget vocabulary at all
        blob = json.dumps(with_budget["body"], sort_keys=True).lower()
        for word in ("budget", "quota", "token", "credit", "allowance", "exhausted"):
            self.assertNotIn(word, blob, word)

    def test_a_body_question_never_becomes_a_budget_question(self):
        home = self.make_home()

        view = lsv.read_life_situation(
            home=home,
            body=body_view(bcs.CAPACITY_LIMITED, ref="bodycap:chiyo_body:bodyrev:3"),
            now="2026-10-16T06:00:00+00:00",
            budget={"source": "life_request_budget.check", "allowed": True, "state": "ok",
                    "utc_day": "2026-10-16", "utc_day_calls": 0},
        )

        self.assertEqual(view["body"]["reason_code"], "TEST_FIXTURE")
        self.assertEqual(
            view["resource_budget"]["reason_code"], lsv.REASON_RESOURCE_BUDGET_ALLOWED
        )
        self.assertNotEqual(view["body"]["derived_by"], view["resource_budget"]["derived_by"])
        self.assertTrue(view["resource_budget"]["owner_asserted"])


# ---------------------------------------------------------------------------
# 3. the budget column: the owner's read, including its fail-closed codes
# ---------------------------------------------------------------------------


class RealBudgetReads(SituationTestCase):
    def _ledger(self, root: Path, *, day_calls: int, run_id: str) -> lrb.LifeRequestBudget:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        payload = {
            "schema_version": lrb.SCHEMA_VERSION,
            "kind": lrb.KIND,
            "active_day": today,
            "days": {today: day_calls},
            "windows": {run_id: {"calls": day_calls, "opened_at": "2026-10-16T00:00:00+00:00",
                                 "closed_at": None}},
            "run_id": run_id,
            "tokens": [],
            "opened_at": "2026-10-16T00:00:00+00:00",
            "updated_at": "2026-10-16T00:00:00+00:00",
        }
        (root / lrb.BUDGET_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
        return lrb.LifeRequestBudget(root, utc_day_max=8, run_window_max=8, run_id=run_id)

    def _budget_read(self, budget: lrb.LifeRequestBudget, check: dict, view: dict) -> dict:
        return {
            "source": "life_request_budget.check",
            "allowed": check["allowed"],
            "reason_code": check.get("reason_code") or None,
            "state": view["state"],
            "path": view["path"],
            "utc_day": view["utc_day"],
            "run_id": view["run_id"],
            "utc_day_calls": view["utc_day_calls"],
            "utc_day_max": view["utc_day_max"],
            "run_window_calls": view["run_window_calls"],
            "run_window_max": view["run_window_max"],
        }

    def test_a_real_allowed_read_is_available_and_bound(self):
        root = self.make_dir("a-situation-budget-")
        budget = self._ledger(root, day_calls=1, run_id="run:allowed")
        read = self._budget_read(budget, budget.check(), budget.view())

        view = lsv.read_life_situation(
            home=self.make_home(), body=body_view(bcs.CAPACITY_LIMITED, ref="bodycap:b:bodyrev:1"),
            now="2026-10-16T06:00:00+00:00", budget=read,
        )

        self.assertEqual(view["resource_budget"]["state"], bcs.CAPACITY_AVAILABLE)
        self.assertEqual(
            view["resource_budget"]["reason_code"], lsv.REASON_RESOURCE_BUDGET_ALLOWED
        )
        self.assertTrue(view["resource_budget"]["bound"])
        self.assertTrue(view["resource_budget"]["ref"].startswith("budget:ledger:"))
        self.assertNotIn(str(root), json.dumps(view))

    def test_a_real_exhausted_read_is_unavailable_with_the_owners_reason(self):
        root = self.make_dir("a-situation-budget-")
        budget = self._ledger(root, day_calls=8, run_id="run:spent")
        check = budget.check()
        self.assertFalse(check["allowed"])

        view = lsv.read_life_situation(
            home=self.make_home(), now="2026-10-16T06:00:00+00:00",
            budget=self._budget_read(budget, check, budget.view()),
        )

        self.assertEqual(view["resource_budget"]["state"], bcs.CAPACITY_UNAVAILABLE)
        self.assertEqual(
            view["resource_budget"]["reason_code"], lsv.REASON_RESOURCE_BUDGET_REFUSED
        )
        self.assertEqual(view["resource_budget"]["owner_reason_code"], check["reason_code"])
        self.assertEqual(view["blocking_dimensions"], [lsv.DIMENSION_RESOURCE_BUDGET])

    def test_a_corrupt_ledger_is_unknown_not_exhausted(self):
        root = self.make_dir("a-situation-budget-")
        (root / lrb.BUDGET_FILENAME).write_text("{not json", encoding="utf-8")
        budget = lrb.LifeRequestBudget(root, utc_day_max=8, run_id="run:corrupt")
        check = budget.check()
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason_code"], lrb.REASON_STATE_CORRUPT)

        view = lsv.read_life_situation(
            home=self.make_home(), now="2026-10-16T06:00:00+00:00",
            budget=self._budget_read(budget, check, budget.view()),
        )

        self.assertEqual(view["resource_budget"]["state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(
            view["resource_budget"]["reason_code"], lsv.REASON_RESOURCE_BUDGET_READ_UNUSABLE
        )
        self.assertFalse(view["resource_budget"]["owner_asserted"])
        self.assertEqual(view["blocking_dimensions"], [])
        self.assertIn(lsv.DIMENSION_RESOURCE_BUDGET, view["unknown_dimensions"])

    def test_reads_this_module_does_not_understand_are_unknown(self):
        home = self.make_home()
        now = "2026-10-16T06:00:00+00:00"

        missing_verdict = lsv.read_life_situation(
            home=home, now=now, budget={"source": "life_request_budget.view", "state": "ok",
                                        "utc_day": "2026-10-16"}
        )
        no_source = lsv.read_life_situation(home=home, now=now, budget={"allowed": True})
        not_injected = lsv.read_life_situation(home=home, now=now)
        unbound = lsv.read_life_situation(
            home=home, now=now, budget={"source": "life_request_budget.check", "allowed": True}
        )

        self.assertEqual(
            missing_verdict["resource_budget"]["reason_code"],
            lsv.REASON_RESOURCE_BUDGET_VERDICT_MISSING,
        )
        self.assertEqual(no_source["resource_budget"]["reason_code"],
                         lsv.REASON_RESOURCE_BUDGET_SOURCE_MISSING)
        self.assertEqual(not_injected["resource_budget"]["reason_code"],
                         lsv.REASON_RESOURCE_BUDGET_NOT_INJECTED)
        # an owner-asserted verdict without a ledger identity is still reported,
        # but it is explicitly unbound rather than given an invented reference
        self.assertEqual(unbound["resource_budget"]["state"], bcs.CAPACITY_AVAILABLE)
        self.assertIsNone(unbound["resource_budget"]["ref"])
        self.assertFalse(unbound["resource_budget"]["bound"])
        self.assertIsNone(unbound["source_revisions"][lsv.DIMENSION_RESOURCE_BUDGET])

    def test_the_budget_reference_is_stable_and_path_free(self):
        root = self.make_dir("a-situation-budget-")
        budget = self._ledger(root, day_calls=2, run_id="run:stable")
        read = self._budget_read(budget, budget.check(), budget.view())
        home = self.make_home()

        first = lsv.read_life_situation(home=home, now="2026-10-16T06:00:00+00:00", budget=read)
        second = lsv.read_life_situation(home=home, now="2026-10-16T07:00:00+00:00", budget=read)

        self.assertEqual(
            first["source_revisions"][lsv.DIMENSION_RESOURCE_BUDGET],
            second["source_revisions"][lsv.DIMENSION_RESOURCE_BUDGET],
        )
        self.assertNotIn(str(root), json.dumps(first))


# ---------------------------------------------------------------------------
# 4. the knowledge column: acquired observations and their declared validity
# ---------------------------------------------------------------------------


class KnowledgeColumn(SituationTestCase):
    NOW = "2026-10-16T06:00:00+00:00"

    def observation(self, ref="obs:1", **overrides):
        item = {
            "observation_ref": ref,
            "source_ref": "world:read:1",
            "observed_at": "2026-10-16T05:00:00+00:00",
            "valid_until": "2026-10-16T07:00:00+00:00",
        }
        item.update(overrides)
        return item

    def build(self, observations, *, reference=True):
        return lsv.read_life_situation(
            home=self.make_home(),
            body=body_view(bcs.CAPACITY_LIMITED, ref="bodycap:chiyo_body:bodyrev:3"),
            now=self.NOW if reference else None,
            observations=observations,
        )

    def item(self, view, index=0):
        return view["knowledge"]["observations"][index]

    def test_a_declared_bound_decides_current_expired_or_unverified(self):
        view = self.build([
            self.observation("obs:current"),
            self.observation("obs:expired", valid_until="2026-10-16T05:30:00+00:00"),
            self.observation("obs:unverified", valid_until=None),
        ])

        self.assertEqual(self.item(view, 0)["validity"], lsv.VALIDITY_CURRENT)
        self.assertEqual(self.item(view, 0)["reason_code"], lsv.REASON_KNOWLEDGE_CURRENT)
        self.assertEqual(self.item(view, 1)["validity"], lsv.VALIDITY_EXPIRED)
        self.assertEqual(self.item(view, 1)["reason_code"], lsv.REASON_OBSERVATION_EXPIRED)
        self.assertEqual(self.item(view, 2)["validity"], lsv.VALIDITY_UNVERIFIED)
        self.assertEqual(
            self.item(view, 2)["reason_code"], lsv.REASON_OBSERVATION_VALIDITY_MISSING
        )
        self.assertEqual(view["knowledge"]["current_refs"], ["obs:current"])
        self.assertEqual(view["knowledge"]["counts"]["expired"], 1)
        self.assertTrue(view["knowledge"]["declared_bound_only"])
        self.assertFalse(view["knowledge"]["local_threshold_used"])

    def test_without_a_reference_instant_nothing_is_current(self):
        view = self.build([self.observation()], reference=False)

        self.assertEqual(self.item(view)["validity"], lsv.VALIDITY_UNVERIFIED)
        self.assertEqual(self.item(view)["reason_code"], lsv.REASON_NO_REFERENCE_INSTANT)
        self.assertEqual(view["knowledge"]["current_refs"], [])
        self.assertIsNone(view["observed_at"])

    def test_the_observation_range_is_only_what_was_declared(self):
        view = self.build([
            self.observation("obs:a"),
            {"observation_ref": "obs:no-source", "observed_at": "2026-10-16T05:00:00+00:00",
             "valid_until": "2026-10-16T07:00:00+00:00"},
            {"observation_ref": "obs:no-ref"},
            {"source_ref": "world:read:1", "observed_at": "2026-10-16T05:00:00+00:00"},
            "not-a-mapping",
            self.observation("obs:a"),
        ])

        reasons = [item["reason_code"] for item in view["knowledge"]["observations"]]
        self.assertEqual(reasons, [
            lsv.REASON_KNOWLEDGE_CURRENT,
            lsv.REASON_OBSERVATION_SOURCE_MISSING,
            lsv.REASON_OBSERVATION_SOURCE_MISSING,
            lsv.REASON_OBSERVATION_REF_MISSING,
            lsv.REASON_OBSERVATION_NOT_MAPPING,
            lsv.REASON_OBSERVATION_DUPLICATE,
        ])
        self.assertEqual(view["knowledge"]["current_refs"], ["obs:a"])
        self.assertEqual(view["knowledge"]["counts"]["total"], 6)

    def test_an_observation_cannot_smuggle_a_body_or_affect_fact(self):
        for leaked in ("body_capacity_state", "fatigue", "mood", "health"):
            view = self.build([self.observation("obs:leak", **{leaked: "strong"})])
            self.assertEqual(self.item(view)["reason_code"],
                             lsv.REASON_OBSERVATION_AUTHORITY_FIELD, leaked)
            self.assertEqual(view["knowledge"]["current_refs"], [], leaked)
            self.assertNotIn(leaked, json.dumps(view["body"]), leaked)

    def test_subject_and_clock_discipline_on_observations(self):
        view = self.build([
            self.observation("obs:other-subject", subject_id="someone.else"),
            self.observation("obs:future", observed_at="2026-10-16T09:00:00+00:00",
                             valid_until="2026-10-16T10:00:00+00:00"),
            self.observation("obs:inverted", valid_until="2026-10-16T04:00:00+00:00"),
            self.observation("obs:mine", subject_id=SUBJECT),
        ])

        self.assertEqual(self.item(view, 0)["reason_code"], lsv.REASON_OBSERVATION_SUBJECT_MISMATCH)
        self.assertEqual(self.item(view, 1)["reason_code"], lsv.REASON_OBSERVATION_TIME_AHEAD)
        self.assertEqual(
            self.item(view, 2)["reason_code"], lsv.REASON_OBSERVATION_VALIDITY_INVALID
        )
        self.assertEqual(self.item(view, 3)["validity"], lsv.VALIDITY_CURRENT)
        self.assertEqual(self.item(view, 3)["subject_id"], SUBJECT)

    def test_knowledge_is_not_a_capacity_dimension(self):
        view = self.build([self.observation("obs:expired", valid_until="2026-10-16T05:30:00+00:00")])

        # an expired observation changes the knowledge column only: the body is
        # still the body, and the missing budget read is still its own dimension
        self.assertEqual(view["limiting_dimensions"], [lsv.DIMENSION_BODY])
        self.assertEqual(view["unknown_dimensions"], [lsv.DIMENSION_RESOURCE_BUDGET])
        self.assertEqual(view["knowledge"]["current_refs"], [])
        self.assertTrue(view["knowledge"]["knowledge_is_not_a_capacity_dimension"])
        self.assertNotIn("knowledge", view["dimensions"])
        self.assertNotIn("knowledge", view["source_revisions"])

    def test_a_bounded_observation_payload_is_carried_as_knowledge(self):
        """The knowledge column carries the real bounded-observation shape.

        ``GatewaySurface.get_observation`` (``world_body_gateway_api.py:373-388``)
        returns exactly these fields over the read port.  The acquisition is not
        live here (the bridge fixture serves body reads only, and
        ``get_observation`` needs more of the service than a body shim provides),
        so the payload is reproduced field for field while the Body read stays
        real; nothing in the view claims the observation was live.
        """

        home = self.make_home()
        state, moment = bridge.burdened_state(3)
        port, _ = self.make_port(state, moment)
        observed = {
            "kind": "chiyo.world_body_gateway",
            "world_id": "world:fixture",
            "location": {"place_id": "place:room", "area_id": "area:desk"},
            "pose": "sitting",
            "hands_occupied": [],
            "visible_object_count": 2,
            "note": "bounded observation only; no raw body values, no full world JSON",
            "observation_ref": "world:observation:1",
            "source_ref": "world_body_read_port.get_observation",
            "observed_at": moment.isoformat(),
            "valid_until": (moment + timedelta(hours=1)).isoformat(),
        }

        view = lsv.read_life_situation(
            home=home, body_port=port, now=moment.isoformat(), observations=[observed]
        )

        self.assertEqual(view["knowledge"]["current_refs"], ["world:observation:1"])
        self.assertEqual(view["body"]["state"], bcs.CAPACITY_LIMITED)
        self.assertEqual(view["reads_performed"], 1)


# ---------------------------------------------------------------------------
# 5. AG-1 seam: both dimensions arrive, state always with its reference
# ---------------------------------------------------------------------------


class Ag1Seam(SituationTestCase):
    def _candidate_and_context(self, captured: str):
        import agency_decision_ag1 as ag1
        import candidate_sources_ag0 as ag0

        candidate = ag0.CandidateRecord(
            candidate_id="cand:situation:1",
            candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
            source_kind=ag0.SOURCE_PERSONAL_OPPORTUNITY,
            source_ref="src:situation:1",
            availability_status=ag0.CANDIDATE_STATUS_OPEN,
            created_at=captured, observed_at=captured, recorded_at=captured,
            idempotency_key="idem:situation:1",
            causal_parent_refs=["src:situation:1"],
            provenance_refs=["src:situation:1"],
        )
        snapshot = ag1.DecisionContextSnapshot(
            context_snapshot_id="dctx:situation:1",
            opportunity_ref="dopp:situation:1",
            candidate_set_ref="cset:situation:1",
            life_frame_ref="lframe:situation:1",
            captured_at=captured,
            source_revisions={},
            snapshot_fingerprint="sfp:situation:1",
        )
        return ag1, candidate, snapshot

    def _evaluate(self, view, captured: str):
        ag1, candidate, snapshot = self._candidate_and_context(captured)
        return ag1, ag1.CapacityEvaluator.evaluate_capacity(
            allowed_candidates=[candidate],
            context_snapshot=snapshot,
            captured_at=captured,
            **lsv.ag1_capacity_kwargs(view),
        )

    def test_kwargs_always_pair_state_with_reference(self):
        home = self.make_home()
        view = lsv.read_life_situation(
            home=home,
            body=body_view(bcs.CAPACITY_LIMITED, ref="bodycap:chiyo_body:bodyrev:3"),
            now="2026-10-16T06:00:00+00:00",
            budget={"source": "life_request_budget.check", "allowed": False,
                    "state": "ok", "reason_code": lrb.REASON_UTC_DAY_LIMIT,
                    "utc_day": "2026-10-16", "utc_day_calls": 8},
        )

        kwargs = lsv.ag1_capacity_kwargs(view)

        self.assertEqual(set(kwargs), {
            "body_capacity_state", "body_capacity_ref",
            "resource_availability_state", "resource_budget_ref",
        })
        self.assertNotIn("device_availability_state", kwargs)
        self.assertNotIn("privacy_availability_state", kwargs)
        self.assertEqual(kwargs["body_capacity_state"], bcs.CAPACITY_LIMITED)
        self.assertEqual(kwargs["resource_availability_state"], bcs.CAPACITY_UNAVAILABLE)

    def test_the_resource_dimension_reaches_ag1_as_a_resource_block(self):
        home = self.make_home()
        view = lsv.read_life_situation(
            home=home,
            body=body_view(bcs.CAPACITY_LIMITED, ref="bodycap:chiyo_body:bodyrev:3"),
            now="2026-10-16T06:00:00+00:00",
            budget={"source": "life_request_budget.check", "allowed": False,
                    "state": "ok", "reason_code": lrb.REASON_UTC_DAY_LIMIT,
                    "utc_day": "2026-10-16", "utc_day_calls": 8},
        )

        ag1, result = self._evaluate(view, "2026-10-16T06:00:00+00:00")
        executability = result.candidate_executability["cand:situation:1"]

        self.assertEqual(result.overall_capacity_state, ag1.CAPACITY_UNAVAILABLE)
        self.assertEqual(executability["executability"], ag1.EXEC_NOT_NOW)
        self.assertEqual(executability["blocking_reasons"], [ag1.REASON_RESOURCE_LIMIT])
        self.assertTrue(executability["candidate_remains_open"])

    def test_an_unknown_body_stays_a_soft_unknown_for_ag1(self):
        home = self.make_home()
        view = lsv.read_life_situation(home=home, now="2026-10-16T06:00:00+00:00")

        ag1, result = self._evaluate(view, "2026-10-16T06:00:00+00:00")
        executability = result.candidate_executability["cand:situation:1"]

        self.assertEqual(result.body_capacity_state, ag1.CAPACITY_UNKNOWN)
        self.assertEqual(executability["executability"], ag1.EXEC_UNKNOWN)
        self.assertEqual(executability["blocking_reasons"], [])
        self.assertTrue(executability["candidate_remains_open"])

    def test_a_non_view_is_refused(self):
        with self.assertRaises(lsv.LifeSituationViewError):
            lsv.ag1_capacity_kwargs({"dimensions": {"body": {}}})
        with self.assertRaises(lsv.LifeSituationViewError):
            lsv.ag1_capacity_kwargs({"body": {"state": "BROKEN", "ref": None},
                                     "resource_budget": {"state": "UNKNOWN", "ref": None},
                                     "dimensions": {"body": "BROKEN"}})
        with self.assertRaises(lsv.LifeSituationViewError):
            lsv.ag1_capacity_kwargs(
                {"dimensions": {"body": "UNKNOWN"},
                 "body": {"state": "UNKNOWN", "ref": None},
                 "resource_budget": {"state": "UNKNOWN", "ref": None}},
                unknown_capacity_policy="DEFER_FOREVER",
            )


# ---------------------------------------------------------------------------
# 6. zero authority, restart, and caller faults
# ---------------------------------------------------------------------------


class ZeroAuthority(SituationTestCase):
    def test_a_build_writes_nothing(self):
        home = self.make_home()
        (home / "state").mkdir()
        (home / "state" / "world.json").write_text('{"revision": 7}', encoding="utf-8")
        before = bridge.home_manifest(home)

        state, moment = bridge.burdened_state(3)
        port, server = self.make_port(state, moment)
        source = lsv.LifeSituationView(home=home, body_port=port)
        for _ in range(3):
            view = source.build(
                now=moment.isoformat(),
                budget={"source": "life_request_budget.check", "allowed": True,
                        "state": "ok", "utc_day": "2026-10-16", "utc_day_calls": 0},
                observations=[{"observation_ref": "obs:1", "source_ref": "world:read:1",
                               "observed_at": moment.isoformat(),
                               "valid_until": (moment + timedelta(hours=1)).isoformat()}],
            )

        self.assertEqual(bridge.home_manifest(home), before)
        self.assertEqual(server.commands, ["get_body_signals"] * 3)
        self.assertEqual(view["writes_performed"], 0)
        self.assertEqual(view["mutations_attempted"], 0)
        self.assertEqual(view["candidates_emitted"], 0)
        self.assertEqual(view["actions_authorized"], 0)
        self.assertIsNone(view["persistent_store"])
        self.assertFalse(view["cache_held"])
        self.assertTrue(view["rebuildable"])
        self.assertEqual(source.last_view, view)

    def test_the_module_has_no_write_or_authority_surface(self):
        # the module docstring is prose: it *names* the things this module must
        # never do, so the check runs against the code that follows it
        code = inspect.getsource(lsv).split('"""', 2)[2]
        for forbidden in ("open(", "write_text", "write_bytes", "json.dump", "sqlite3",
                          "shutil", "os.remove", "os.replace", "mkdir", "reserve(",
                          "ensure_window", "import agency_decision_ag1",
                          "import candidate_sources_ag0", "from agency_decision_ag1",
                          "from candidate_sources_ag0"):
            self.assertNotIn(forbidden, code, forbidden)
        for name in dir(lsv):
            if name.startswith("_"):
                continue
            self.assertNotRegex(
                name.lower(),
                r"adopt|submit|authoriz|authoris|dispatch|persist|spend|reserve|consume",
                f"public name {name!r} looks like an authority verb",
            )
        parameters = inspect.signature(lsv.read_life_situation).parameters
        for name in parameters:
            self.assertNotRegex(name, r"reserve|consume|spend|quota|token|credit|allowance")

    def test_restart_rebuilds_the_same_view_from_the_owner(self):
        home = self.make_home()
        state, moment = bridge.burdened_state(3)
        port, server = self.make_port(state, moment)

        first = lsv.read_life_situation(home=home, body_port=port, now=moment.isoformat())
        second = lsv.read_life_situation(home=home, body_port=port, now=moment.isoformat())

        self.assertEqual(first, second)
        self.assertEqual(server.commands, ["get_body_signals"] * 2)

    def test_caller_faults_are_raised_not_guessed(self):
        home = self.make_home()
        state, moment = bridge.burdened_state(3)
        port, _ = self.make_port(state, moment)

        with self.assertRaises(lsv.LifeSituationViewError):
            lsv.read_life_situation(home=home, body=body_view(bcs.CAPACITY_LIMITED), body_port=port)
        with self.assertRaises(lsv.LifeSituationViewError):
            lsv.read_life_situation(home=home, body_port=port, now="not-a-time")
        with self.assertRaises(lsv.LifeSituationViewError):
            lsv.read_life_situation(home=home, body_port=port, expected_revision="bodyrev")
        with self.assertRaises(lsv.LifeSituationViewError):
            lsv.read_life_situation(home=home, body_port=NS(), now=moment.isoformat())
        with self.assertRaises(lsv.LifeSituationViewError):
            lsv.read_life_situation(home=home, body_port=port, subject_id="someone.else")
        with self.assertRaises(lsv.LifeSituationViewError):
            lsv.LifeSituationView(home=home, body_port=port, subject_id="someone.else")
        with self.assertRaises(lsv.LifeSituationViewError):
            lsv.LifeSituationView(home="/nonexistent/instance-home")

    def test_an_unusable_home_or_subject_owner_binds_nothing(self):
        state, moment = bridge.burdened_state(3)
        port, server = self.make_port(state, moment)

        no_home = lsv.read_life_situation(home="/nonexistent/instance-home", body_port=port,
                                          now=moment.isoformat())
        self.assertFalse(no_home["identity_bound"])
        self.assertIsNone(no_home["home_ref"])
        self.assertEqual(no_home["body"]["state"], bcs.CAPACITY_UNKNOWN)
        self.assertEqual(no_home["body"]["reason_code"], bcs.REASON_HOME_UNBOUND)
        self.assertEqual(no_home["resource_budget"]["reason_code"], bcs.REASON_HOME_UNBOUND)
        self.assertEqual(server.commands, [])

        saved = sys.modules.get("activity_continuity")
        try:
            sys.modules["activity_continuity"] = None
            no_subject = lsv.read_life_situation(
                home=self.make_home(), body_port=port, now=moment.isoformat()
            )
        finally:
            if saved is None:
                sys.modules.pop("activity_continuity", None)
            else:
                sys.modules["activity_continuity"] = saved

        self.assertFalse(no_subject["identity_bound"])
        self.assertEqual(
            no_subject["body"]["reason_code"], bcs.REASON_SUBJECT_OWNER_UNAVAILABLE
        )
        self.assertEqual(server.commands, [])

    def test_a_view_never_reports_a_health_score_or_a_merged_number(self):
        home = self.make_home()
        state, moment = bridge.burdened_state(3)
        port, _ = self.make_port(state, moment)

        view = lsv.read_life_situation(
            home=home, body_port=port, now=moment.isoformat(),
            budget={"source": "life_request_budget.check", "allowed": True, "state": "ok",
                    "utc_day": "2026-10-16", "utc_day_calls": 0},
            observations=[{"observation_ref": "obs:1", "source_ref": "world:read:1",
                           "observed_at": moment.isoformat(),
                           "valid_until": (moment + timedelta(hours=1)).isoformat()}],
        )

        blob = json.dumps(view, sort_keys=True).lower()
        for word in ("mood", "emotion", "affect", "feeling", "personality", "relationship",
                     "score", "overall_health"):
            self.assertNotIn(word, blob, word)
        self.assertEqual(view["dimensions"], {lsv.DIMENSION_BODY: bcs.CAPACITY_LIMITED,
                                             lsv.DIMENSION_RESOURCE_BUDGET: bcs.CAPACITY_AVAILABLE})


if __name__ == "__main__":
    unittest.main(verbosity=2)

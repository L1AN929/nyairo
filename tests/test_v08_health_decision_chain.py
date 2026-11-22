#!/usr/bin/env python3
"""v0.8: from the body owner to a committed Decision/Adoption (and one real step).

Two layers are kept apart on purpose
------------------------------------
1. ``HealthReadChainTest`` / ``DecisionAdoptionChainTest`` are **component**
   tests.  They bind real ``WorldBodyService`` methods to a shim, write a health
   record directly and run the real AG-1/AG-2 services.  They are accurate about
   that substitution and do not claim a full owner execution chain.
2. ``RealOwnerReadingChainTest`` is the **owner chain**: a fresh instance
   prepared through ``one_day_life.prepare`` (real Life Supply owner, real
   ``WorldBodyService`` on its own unix gateway socket), a real v03 illness
   course written by ``apply_v03_ecology_command``, the real
   ``read_life_situation`` + ``AutonomousLifeCycle`` capacity kwargs, the real
   AG-1 / AG-2 services, and the real AR-0 reading step that advances the
   private bookmark.  Only the cognition answer is a scripted stand-in (no
   network, no product model).

Every root here is created with ``tempfile.mkdtemp`` and is deliberately kept:
this file registers no ``shutil.rmtree``/``TemporaryDirectory`` cleanup, so the
instance trees remain on disk as evidence.

Run:  python3 tests/test_v08_health_decision_chain.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "native" / "integration"),
    str(ROOT / "tests"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import body_consequence as bcon  # noqa: E402
import body_health_projection as bhp  # noqa: E402
import body_runtime as br  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_situation_view as lsv  # noqa: E402
import test_active_abandon_reachable as abandon_fixture  # noqa: E402
import test_body_capacity_source as body_bridge  # noqa: E402
from world_body_gateway_api import GatewaySurface  # noqa: E402
from world_body_service import WorldBodyService  # noqa: E402

#: The real-owner chain reuses the proven one-day helpers.  The import is
#: guarded so this module stays importable where POSIX-only owners are absent.
try:
    from test_one_day_life import (  # noqa: E402
        OneDayTestCase,
        StubObservation,
        StubWriter,
        posix_only,
    )
except Exception:  # noqa: BLE001 - the POSIX-only owner helpers are unavailable
    OneDayTestCase = None
    posix_only = unittest.skipIf(True, "POSIX-only owner helpers unavailable")

MOMENT = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)
NOW = MOMENT.isoformat()
BODY_ID = br.BODY_ID

#: Three real pages; the reading step reads exactly one of them.
READING_TEXT = ("第一页：房间里的桌子与窗。\n%%PAGE%%\n"
                "第二页：窗外是一条街，街角有树。\n%%PAGE%%\n"
                "第三页：天色渐暗，风从海上过来。\n")


# ---------------------------------------------------------------------------
# one real instance root: runtime + health record
# ---------------------------------------------------------------------------


def _loaded_runtime(hours: int = 3):
    state = br.initial_runtime(at="2026-11-15T00:00:00+00:00")
    moment = datetime(2026, 11, 15, 0, 0, tzinfo=timezone.utc)
    for _ in range(hours):
        report = br.advance(
            state,
            moment.isoformat(),
            (moment + timedelta(hours=1)).isoformat(),
            {
                "class": "PHYSICAL_RESULT",
                "movement_duration_seconds": 3600,
                "effort": "heavy",
                "pose": "standing",
            },
        )
        state = report["state"]
        moment += timedelta(hours=1)
    return state, moment


def _write_course(root: Path, *, phase="acute_illness", severity=0.6):
    body_dir = root / "data" / "body"
    state, moment = _loaded_runtime()
    br.BodyStore(body_dir).save_runtime(state)
    record = bhp.admit_exposure(
        bhp.initial_record(body_id=BODY_ID, subject_id="self", home_ref="", at=moment),
        episode_id="ep:real-1",
        at=moment,
        origin="world_exposure",
        severity=severity,
    )
    bhp.HealthStore(body_dir).save(record)
    # The v03 owner writes the same identity/time fields.  Keeping the file
    # healthy here lets the persisted record speak; the illness-path tests below
    # write their own file.
    (root / "phone").mkdir(parents=True, exist_ok=True)
    (root / "phone" / "v03_ecology.json").write_text("{}", encoding="utf8")
    return state, moment, record


def _gateway(root, state, moment, record):
    shim = NS(
        body_state=state, health_state=record, _now=moment, _clock=None,
        config=NS(world_home=Path(root)),
    )
    shim._moment = types.MethodType(WorldBodyService._moment, shim)
    shim.body_summary = types.MethodType(WorldBodyService.body_summary, shim)
    shim.health_record = types.MethodType(WorldBodyService.health_record, shim)
    return shim


def _phone_service_module():
    path = ROOT / "components" / "phone" / "service.py"
    spec = importlib.util.spec_from_file_location("phone_service_v08_chain", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["phone_service_v08_chain"] = module
    spec.loader.exec_module(module)
    return module


def _phone_shim(root):
    module = _phone_service_module()
    shim = NS(world_home=Path(root), root=Path(root),
              _somatic_now_iso=lambda: NOW)
    shim.body_health = types.MethodType(module.PhoneService.body_health, shim)
    shim._load_body_health_module = module.PhoneService._load_body_health_module
    return shim


def _manifest(root: Path):
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rows.append((str(path.relative_to(root)), path.stat().st_size,
                         path.stat().st_mtime_ns))
    return rows


class HealthReadChainTest(unittest.TestCase):
    def setUp(self):
        # Kept on purpose: the instance root is evidence, not throwaway state.
        self.root = Path(tempfile.mkdtemp(prefix="v08-chain-"))
        self.state, self.moment, self.record = _write_course(self.root)

    def test_gateway_and_phone_share_one_real_course(self):
        shim = _gateway(self.root, self.state, self.moment, self.record)
        gateway = GatewaySurface(shim).handle("get_body_health")["result"]
        self.assertEqual(gateway["health"]["status"], "ACUTE_ILLNESS")
        self.assertTrue(gateway["capacity"]["creative_block"])
        self.assertEqual(gateway["capacity"]["candidate_cap"], 4)

        phone = _phone_shim(self.root).body_health()[1]
        self.assertEqual(phone["health_status"], "ACUTE_ILLNESS")
        self.assertEqual(phone["capacity"]["candidate_cap"], 4)
        self.assertTrue(phone["capacity"]["creative_block"])
        self.assertNotIn("severity", (phone.get("episode") or {}))
        self.assertIn("severity_band", phone.get("episode") or {})

        # Two reads change nothing on disk: these are read surfaces.
        before = _manifest(self.root)
        _phone_shim(self.root).body_health()
        GatewaySurface(shim).handle("get_body_health")
        self.assertEqual(_manifest(self.root), before)

    def test_missing_runtime_is_never_healthy(self):
        empty = Path(tempfile.mkdtemp(prefix="v08-empty-", dir=str(self.root)))
        phone = _phone_shim(empty).body_health()[1]
        self.assertEqual(phone["health_status"], "UNATTESTED")
        self.assertFalse(phone["attested"])
        self.assertEqual(phone["reason_code"], "RUNTIME_UNAVAILABLE")

        nosignal = _gateway(empty, None, self.moment, None)
        gateway = GatewaySurface(nosignal).handle("get_body_health")["result"]
        self.assertEqual(gateway["health"]["status"], "UNATTESTED")
        self.assertEqual(gateway["capacity"]["state"], "UNKNOWN")
        self.assertEqual(gateway["writes_performed"], 0)

    def test_v03_course_identity_is_stable_across_reads(self):
        illness = {
            "phase": "acute_illness",
            "severity": 0.6,
            "episode_id": "ep:v03-real",
            "episode_opened_at": "2026-11-14T20:00:00+00:00",
            "acute_entered_at": "2026-11-14T21:00:00+00:00",
            "phase_entered_at": "2026-11-15T06:00:00+00:00",
        }
        (self.root / "phone" / "v03_ecology.json").write_text(
            json.dumps({"illness": illness}), encoding="utf8")
        first = _gateway(self.root, self.state, self.moment, self.record).health_record()
        later = _gateway(
            self.root, self.state, self.moment + timedelta(hours=5), self.record
        ).health_record()
        self.assertEqual(first.episode.episode_id, "ep:v03-real")
        self.assertEqual(later.episode.episode_id, "ep:v03-real")
        self.assertEqual(first.episode.opened_at, later.episode.opened_at)
        self.assertEqual(first.health_since, later.health_since)

        (self.root / "phone" / "v03_ecology.json").write_text(
            json.dumps({"illness": dict(illness, phase="prodrome", severity=0.2)}),
            encoding="utf8",
        )
        prodrome = _gateway(
            self.root, self.state, self.moment, self.record
        ).health_record()
        self.assertEqual(prodrome.status.value, "STRAINED")
        payload = GatewaySurface(
            _gateway(self.root, self.state, self.moment, prodrome)
        ).handle("get_body_health")["result"]
        self.assertEqual(payload["health"]["status"], "STRAINED")


class ChronicLoadWiringTest(unittest.TestCase):
    """A real measured physical advance feeds chronic load and debt, once.

    ``inputs_summary.load_units`` is the runtime's **hours-equivalent** of
    physical effort (``body_runtime._load_units``: movement hours x effort
    class + posture hours x pose strain).  It is therefore a load-hours number,
    not a dimensionless tick count, and ``_persist_consequence`` passes it as
    ``load_hours`` -- the same unit ``advance_record`` expects.  The owner's
    ``body_consequence.apply_for_execution`` is exactly-once per
    ``execution_id``; the replay below reuses that guarantee instead of adding
    a second ledger.
    """

    def setUp(self):
        # Kept on purpose: the instance root is evidence, not throwaway state.
        self.root = Path(tempfile.mkdtemp(prefix="v08-load-"))
        self.body_dir = self.root / "data" / "body"
        self.start = datetime(2026, 11, 15, 0, 0, tzinfo=timezone.utc)
        self.state = br.initial_runtime(at=self.start.isoformat())
        self.record = bhp.initial_record(
            body_id=BODY_ID, subject_id="self", home_ref="", at=self.start)
        self.store = bhp.HealthStore(self.body_dir)
        self.store.save(self.record)

    def _service_shim(self):
        shim = NS(
            body_state=self.state, health_state=self.record, health_store=self.store,
            _now=self.start, _clock=None,
            body_store=br.BodyStore(self.body_dir),
            counters={"writes": 0},
        )
        shim._moment = types.MethodType(WorldBodyService._moment, shim)
        shim._advance_health_record = types.MethodType(
            WorldBodyService._advance_health_record, shim)
        shim._persist_consequence = types.MethodType(
            WorldBodyService._persist_consequence, shim)
        return shim

    def _move_result(self, execution_id: str = "exec:v08-load-1") -> dict:
        return {
            "kind": "world_action_result",
            "execution_id": execution_id,
            "action": "MOVE",
            "status": "applied",
            "reason_code": "move_arrived",
            "legacy_reason_code": "move_arrived",
            "occurred_at": (self.start + timedelta(minutes=30)).isoformat(),
        }

    def _apply(self, shim, ledger, *, at):
        return bcon.apply_for_execution(
            self._move_result(),
            body_id=BODY_ID,
            ledger=ledger,
            runtime_state=shim.body_state,
            at=at,
            world_facts={
                "movement_seconds": 1800.0,
                "movement_source": bcon.MEASUREMENT_MEASURED_TRAVEL,
                "world_id": "world:v08-test",
                "world_revision": 1,
            },
        )

    def test_measured_load_is_billed_once_and_idle_time_is_not(self):
        shim = self._service_shim()
        ledger = bcon.ConsequenceLedger(self.body_dir / "consequences.jsonl")
        at = self.start + timedelta(minutes=30)

        first = self._apply(shim, ledger, at=at)
        self.assertTrue(first["applied"], first.get("reason"))
        advance = first["advance"]
        # MOVE is the "moderate" effort class, so 1800 s of walking is 0.5
        # load-hours -- the unit carried into the health record.
        self.assertAlmostEqual(
            advance["inputs_summary"]["load_units"], 0.5, delta=1e-9)
        self.assertAlmostEqual(advance["elapsed_seconds"], 1800.0, delta=1e-9)
        shim.body_state = shim._persist_consequence(first)
        billed = shim.health_state
        self.assertGreater(billed.fatigue.chronic, 0.0)
        self.assertGreater(billed.fatigue.debt, 0.0)
        billed_revision = billed.revision

        # Same execution replayed: the owner ledger answers "duplicate" and
        # produces no advance, so the health record must not move.
        replay = self._apply(shim, ledger, at=at)
        self.assertFalse(replay["applied"], replay.get("reason"))
        self.assertEqual(replay["reason"], "duplicate_execution_id")
        self.assertNotIn("advance", replay)
        shim._persist_consequence(replay)
        self.assertEqual(shim.health_state.revision, billed_revision)
        self.assertEqual(shim.health_state.fatigue.chronic, billed.fatigue.chronic)
        self.assertEqual(shim.health_state.fatigue.debt, billed.fatigue.debt)
        self.assertEqual(
            len(ledger.applied_execution_ids() & {"exec:v08-load-1"}), 1)

        # An idle advance carries no load: it must not be billed as debt.
        idle = br.advance(
            shim.body_state, at.isoformat(),
            (at + timedelta(minutes=10)).isoformat(), [],
        )
        self.assertEqual(idle["inputs_summary"]["load_units"], 0.0)
        shim._persist_consequence({
            "state": idle["state"], "transition": None, "advance": idle,
        })
        self.assertEqual(shim.health_state.fatigue.debt, billed.fatigue.debt)


# ---------------------------------------------------------------------------
# real situation read -> real DecisionService -> real Adoption
# ---------------------------------------------------------------------------


def _reading_decision_candidate(now, *, cid, semantic, target):
    anchor = cid.split(":", 1)[-1]
    return ag0.CandidateRecord(
        candidate_id=cid,
        candidate_kind=ag0.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
        source_kind=ag0.SOURCE_PERSONAL_OPPORTUNITY,
        source_ref="src:%s" % anchor,
        availability_status=ag0.CANDIDATE_STATUS_OPEN,
        created_at=now, observed_at=now, recorded_at=now,
        idempotency_key="idem:%s" % anchor,
        target_refs=[target],
        semantic_identity=semantic,
        causal_parent_refs=["src:%s" % anchor],
        provenance_refs=["src:%s" % anchor],
    )


class _CapturingStub(abandon_fixture._StubCognition):
    def __init__(self):
        super().__init__()
        self.last_candidate_ids: list[str] = []

    def invoke(self, request, *, schema_retry_index=0):
        self.last_candidate_ids = [
            str(item.get("candidate_id")) for item in (request.candidates or [])
        ]
        return super().invoke(request, schema_retry_index=schema_retry_index)


def _bare_artifact_id(ref) -> str:
    """The object identity behind an artifact ref (every ``artifact:`` dropped).

    The Supply owner mints ``artifact:<id>`` while the admitted
    PERSONAL_OPPORTUNITY source spells the same object ``artifact:artifact:<id>``;
    matching has to be about the object, not the spelling.
    """
    text = str(ref or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def _offered_reading_cognition(script, *, reading_artifact_id, work_artifact_id):
    """Scripted cognition that can only choose the *real offered* reading candidate.

    The base ``ScriptedCognition`` matches by substring.  This subclass compares
    the artifact identity itself (every ``artifact:`` prefix dropped), and it
    records the safe metadata of what AG-1 really offered this round (candidate
    id / kind / source / targets), so a candidate cap that dropped the reading
    option is reported as that named fact instead of surfacing later as an
    opaque ``NO_ACTION``.
    """

    from test_subject_reading_cycle import ScriptedCognition

    class _Offered(ScriptedCognition):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.offered_refs: list[dict] = []

        def invoke(self, request, *, schema_retry_index=0):
            self.offered_refs = [
                {
                    "candidate_id": str(c.get("candidate_id") or ""),
                    "candidate_kind": str(c.get("candidate_kind") or ""),
                    "source_kind": str(c.get("source_kind") or ""),
                    "source_ref": str(c.get("source_ref") or ""),
                    "target_refs": [str(r) for r in (c.get("target_refs") or [])],
                }
                for c in (getattr(request, "candidates", []) or [])
            ]
            return super().invoke(request, schema_retry_index=schema_retry_index)

        def _pick(self, request, wanted):
            wanted_id = _bare_artifact_id(wanted)
            matches = []
            for candidate in (getattr(request, "candidates", []) or []):
                candidate = dict(candidate)
                refs = [str(r) for r in (candidate.get("target_refs") or [])]
                refs.append(str(candidate.get("source_ref") or ""))
                if any(ref.startswith("closeout:") for ref in refs):
                    continue
                if not any(_bare_artifact_id(ref) == wanted_id for ref in refs):
                    continue
                legal = [str(x) for x in (candidate.get("legal_decisions") or [])]
                matches.append((candidate, legal))
            for candidate, legal in matches:
                if "CONTINUE" in legal:
                    return candidate, legal
            for candidate, legal in matches:
                if "START" in legal:
                    return candidate, legal
            return (matches[0] if matches else (None, []))

        def reading_offered(self) -> bool:
            """Did AG-1 really put this instance's reading material in front of me?"""

            wanted = _bare_artifact_id(self.reading_artifact_id)
            return any(
                _bare_artifact_id(ref) == wanted
                for row in self.offered_refs
                for ref in [row["source_ref"]] + row["target_refs"]
            )

    return _Offered(
        script,
        reading_artifact_id=reading_artifact_id,
        work_artifact_id=work_artifact_id,
    )


class DecisionAdoptionChainTest(unittest.TestCase):
    def setUp(self):
        # Kept on purpose: the instance root is evidence, not throwaway state.
        self.root = Path(tempfile.mkdtemp(prefix="v08-dec-"))
        self.state, self.moment, self.record = _write_course(self.root)

    def _body_kwargs(self):
        """Real gateway socket -> real body_capacity_source -> real situation view."""

        shim = _gateway(self.root, self.state, self.moment, self.record)
        surface = GatewaySurface(shim)
        server = body_bridge.UnixSocketGatewayServer(surface)
        try:
            client = body_bridge.wbc.WorldBodyClient(
                socket_path=server.path, mode=body_bridge.wbc.MODE_CANONICAL,
                read_retries=0,
            )
            port = body_bridge.wbrp.WorldBodyReadPort(client)
            view = lsv.read_life_situation(
                home=self.root,
                body_port=port,
                now=NOW,
                budget={"source": "life_request_budget.check", "allowed": True,
                        "state": "ok", "utc_day": "2026-11-15", "utc_day_calls": 0},
            )
        finally:
            server.close()
        self.assertEqual(view["body"]["state"], "LIMITED")
        self.assertEqual(view["body"]["candidate_cap"], 4)
        self.assertTrue(view["body"]["creative_block"])
        cycle = alc.AutonomousLifeCycle(
            NS(state_root=Path(tempfile.mkdtemp(prefix="v08-cyc-"))), enabled=True, env={}
        )
        kwargs = cycle.capacity_kwargs(view)
        self.assertEqual(kwargs["body_candidate_cap"], 4)
        self.assertTrue(kwargs["body_creative_block"])
        return kwargs

    def _evaluate(self, harness, *, start, candidates, stub_decision, selected, key):
        now = NOW
        candidate_set = ag0.CandidateSet(
            candidate_set_id="cset:%s" % key,
            subject_id=ag0.GLOBAL_SUBJECT_ID,
            candidate_refs=[c.candidate_id for c in candidates],
            source_snapshot_refs=["snap:%s" % key],
            observed_at=now, built_at=now,
        )
        harness.stub.raw_output = {
            "decision": stub_decision,
            "selected_candidate_id": selected,
            "reason_codes": [
                ag1.REASON_CURRENT_ACTIVITY_CONTINUITY
                if stub_decision == ag1.DECISION_CONTINUE
                else ag1.REASON_NO_ACTION_CHOSEN
            ],
            "short_rationale": "读一会儿书，或者先休息。",
            "deferred_candidate_ids": [],
        }
        opportunity = ag1.DecisionOpportunity(
            opportunity_id="dopp:%s" % key,
            trigger_kind="EXPLICIT_REEVALUATION",
            trigger_ref="evt:%s" % key,
            candidate_set_ref=candidate_set.candidate_set_id,
            occurred_at=now, observed_at=now, recorded_at=now,
            causal_parent_refs=[candidate_set.candidate_set_id],
            idempotency_key="idem:%s" % key,
            current_activity_ref=harness.foreground_ref() if start else None,
            status=ag1.OPPORTUNITY_STATUS_OPEN,
        )
        call = {
            "device_availability_state": "AVAILABLE",
            "resource_availability_state": "AVAILABLE",
            "privacy_availability_state": "AVAILABLE",
            "runtime_load_state": "AVAILABLE",
        }
        call.update(self._body_kwargs())
        return harness.ag1_service.evaluate_opportunity(
            opportunity=opportunity,
            candidate_set=candidate_set,
            candidates=candidates,
            **call,
        )

    def test_creative_rejected_before_cap_and_reading_continues(self):
        harness = abandon_fixture._Harness()
        harness.stub = _CapturingStub()
        harness.ag1_service.cognition_adapter = harness.stub
        try:
            harness.start()
            _set, current = harness.materialize(observed_at=NOW)
            reading = current[0]
            self.assertEqual(reading.candidate_kind, ag0.CANDIDATE_KIND_CONTINUE_CURRENT)
            creative = _reading_decision_candidate(
                NOW, cid="cand:workbench", semantic="WORKBENCH_UNFINISHED_TRAY",
                target="artifact:work-1")
            result = self._evaluate(
                harness, start=True, candidates=[reading, creative],
                stub_decision=ag1.DECISION_CONTINUE, selected=reading.candidate_id,
                key="continue",
            )
            self.assertEqual(result["status"], "SUCCEEDED")
            decision = result["decision_record"]
            self.assertEqual(decision.decision, ag1.DECISION_CONTINUE)
            rejected = [
                r for r in result["constraint_results"]
                if r.candidate_ref == "cand:workbench"
            ]
            self.assertTrue(any(
                "HARD_REJECT:BODY_CREATIVE_BLOCK" in (r.reason_codes or [])
                for r in rejected
            ))
            self.assertIn(reading.candidate_id, harness.stub.last_candidate_ids)

            adopted = harness.adoption.adopt_decision(decision, occurred_at=NOW)
            self.assertFalse(adopted["idempotent_replay"])
            self.assertEqual(adopted["activity"]["activity_id"], reading.activity_ref)
            self.assertEqual(
                harness.activity(reading.activity_ref)["status"], "ACTIVE"
            )
        finally:
            harness.close()

    def test_four_creatives_do_not_eat_the_cap_before_rest(self):
        harness = abandon_fixture._Harness()
        harness.stub = _CapturingStub()
        harness.ag1_service.cognition_adapter = harness.stub
        try:
            creatives = [
                _reading_decision_candidate(
                    NOW, cid="cand:w%d" % i,
                    semantic="WORKBENCH_UNFINISHED_TRAY",
                    target="artifact:work-%d" % i)
                for i in range(4)
            ]
            rest = _reading_decision_candidate(
                NOW, cid="cand:rest", semantic="REST_AT_HOME", target="bodyact:rest:1")
            result = self._evaluate(
                harness, start=False, candidates=creatives + [rest],
                stub_decision=ag1.DECISION_NO_ACTION, selected=None, key="cap",
            )
            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertEqual(harness.stub.last_candidate_ids, ["cand:rest"])
            self.assertEqual(
                [r.candidate_ref for r in result["constraint_results"]
                 if "HARD_REJECT:BODY_CREATIVE_BLOCK" in (r.reason_codes or [])],
                ["cand:w0", "cand:w1", "cand:w2", "cand:w3"],
            )
        finally:
            harness.close()


# ---------------------------------------------------------------------------
# real owner chain: real Supply + real WorldBodyService socket + real AR step
# ---------------------------------------------------------------------------


if OneDayTestCase is not None:

    @posix_only
    class RealOwnerReadingChainTest(OneDayTestCase):
        """Real v03 course -> real body gateway -> real cycle -> real reading step.

        Only the cognition answer is scripted.  The illness course is written by
        v03's own ``apply_v03_ecology_command`` (no hand-written ecology file),
        and the bookmark movement is a new private-items version committed by
        the Supply owner, not an in-memory assertion.
        """

        def tearDown(self) -> None:
            """Close the owners but keep this run's instance root as evidence."""
            for runtime in getattr(self, "_runtimes", []):
                try:
                    runtime.close()
                except Exception:  # noqa: BLE001 - a close failure must not hide the result
                    pass
            self._runtimes = []
            self._teardown()  # stops the Supply owner, restores the process env

        # -- setup helpers ---------------------------------------------------

        def _prepared(self) -> dict:
            result = self.prepare_instance(
                reading_title="v08 真实阅读链",
                reading_content=READING_TEXT,
                create_private_items=True,
            )
            self.assertTrue(result["ok"], result)
            import one_day_life as odl

            return odl.read_instance(self.instance)

        def _seed_acute_course(self) -> tuple[str, list[dict]]:
            """Two formal rain exposures through the v03 owner's own command."""
            import v03_economy_illness_bridge as v03

            now_iso = datetime.now(timezone.utc).isoformat()
            receipts = []
            for index in (1, 2):
                answer = v03.apply_v03_ecology_command(
                    self.instance,
                    {
                        "action": "record_strain_or_rest",
                        "event_kind": "caught_in_rain",
                        "intensity": 1.0,
                        "operation_key": "v08-real-rain-%d" % index,
                    },
                    actor_side="subject",
                    now_iso=now_iso,
                )
                illness = (answer.get("data") or {}).get("illness") or {}
                receipts.append({
                    "operation_key": "v08-real-rain-%d" % index,
                    "command_status": answer.get("command_status"),
                    "revision": answer.get("revision"),
                    "phase": illness.get("phase"),
                    "severity": illness.get("severity"),
                    "episode_id": illness.get("episode_id"),
                })
            self.assertEqual(receipts[-1]["phase"], "acute_illness", receipts[-1])
            self.assertTrue(receipts[-1]["episode_id"], receipts[-1])
            return now_iso, receipts

        def _body_port(self, env: dict):
            import autonomous_life_assembly as assembly

            body = assembly.resolve_body_port(env)
            self.assertEqual(body["status"], "READY", body)
            return body["port"]

        def _situation(self, env: dict, port) -> dict:
            return lsv.read_life_situation(
                home=Path(env["CHIYO_WORLD_HOME"]),
                body_port=port,
                now=datetime.now(timezone.utc).isoformat(),
                budget={"source": "life_request_budget.check", "allowed": True,
                        "state": "ok", "utc_day": "2026-11-15", "utc_day_calls": 0},
            )

        def _runtime(self, cognition, env, data):
            import life_artifact_port as lap
            import life_integration_ag2 as ag2
            import life_runtime_production as lrp
            import managed_artifact_action_adapter as maaa
            import one_day_life as odl
            from world_journey_action_adapter import (
                JourneyWorldResolver,
                RealJourneyWorldActionAdapter,
            )

            port = lap.LifeArtifactPort(
                str(self.services.supply_path), data["subject_id"])
            adapter = maaa.SupplyManagedArtifactActionAdapter(
                port,
                grant_id=data["artifact_grant_id"],
                workspace_id=data["workspace_id"])
            switches = ag2.LifeRuntimeKillSwitches(
                life_runtime_enabled=True, agency_enabled=True,
                action_execution_enabled=True, proactive_enabled=False)
            runtime = lrp.build_production_life_runtime(
                odl.life_state_root(self.instance),
                kill_switches=switches,
                cognition_adapter=cognition,
                install_root=self.instance / "life",
                artifact_action_adapter=adapter,
                world_action_adapter=RealJourneyWorldActionAdapter(
                    JourneyWorldResolver(environment=env)),
            )
            self._runtimes.append(runtime)
            self.addCleanup(runtime.close)
            return runtime

        def _head(self, artifact_id: str) -> dict:
            import one_day_life as odl

            data = odl.read_instance(self.instance)
            return odl.supply_call(
                self.services.supply_path,
                {"op": "read_head", "subject": data["subject_id"],
                 "artifact_id": artifact_id},
            )["head"]

        def _reading_positions(self, data: dict) -> list[dict]:
            import one_day_life as odl
            import private_daily_items as pdi

            content = odl.supply_call(
                self.services.supply_path,
                {"op": "read_artifact", "subject": data["subject_id"],
                 "artifact_id": data["private_items_artifact_id"]},
            )["content"]
            document = pdi.decode_document(content)
            source_id = "artifact:%s" % str(
                data["reading_artifact_id"]).split("artifact:", 1)[-1]
            items = document["items"]
            order = [item_id for item_id in (document.get("order") or [])
                     if item_id in items]
            order += [item_id for item_id in items if item_id not in order]
            return [items[item_id] for item_id in order
                    if items[item_id].get("item_type") == pdi.ITEM_READING_POSITION
                    and str(items[item_id].get("source_id")) == source_id]

        # -- the one real owner round ---------------------------------------

        def test_acute_course_reaches_the_round_and_the_reading_step_advances_the_bookmark(self):
            import autonomous_life_assembly as assembly
            import one_day_life as odl

            data = self._prepared()
            seeded_at, receipts = self._seed_acute_course()
            env = self.instance_env()
            port = self._body_port(env)
            # One real due "later" note, written through its own owner store.
            # It is ordinary non-creative life, so it guarantees more legal
            # candidates than the cap without depending on whether a World
            # opportunity happened to be observed this instance.
            import life_future_items as lfi

            lfi.FutureItems(odl.life_state_root(self.instance)).propose(
                title="傍晚把窗台擦一遍",
                due_at="2026-11-15T00:00:00+00:00",
                now=seeded_at)

            signals = port.body()
            self.assertEqual(signals["outcome"], "OK", signals)
            health = (signals["data"] or {}).get("health") or {}
            self.assertEqual(health.get("status"), "ACUTE_ILLNESS", health)
            self.assertEqual(health.get("capacity_state"), "LIMITED", health)
            self.assertEqual(health.get("candidate_cap"), 4, health)
            self.assertTrue(health.get("creative_block"), health)

            view = self._situation(env, port)
            self.assertEqual(view["body"]["state"], "LIMITED", view["body"])
            self.assertEqual(view["body"]["candidate_cap"], 4, view["body"])
            self.assertTrue(view["body"]["creative_block"], view["body"])

            before_doc = self._head(data["private_items_artifact_id"])
            before_work = self._head(data["work_artifact_id"])

            cognition = _offered_reading_cognition(
                ["READ"],
                reading_artifact_id=data["reading_artifact_id"],
                work_artifact_id=data["work_artifact_id"],
            )
            runtime = self._runtime(cognition, env, data)
            assembled = assembly.assemble_autonomous_life(
                runtime, environment=env,
                state_root=odl.life_state_root(self.instance),
                world_provider=StubObservation(env), writer=StubWriter(),
                enabled=True)
            self.assertEqual(assembled["state"], assembly.READY, assembled)
            cycle = assembled["cycle"]
            # Every real candidate source stays on: the v02/v03 day-loop feed
            # (illness care / idle cues), the bound work as continuation +
            # closeout, the reading material and the World opportunity.  The
            # acute round must narrow this real set down to four options
            # deterministically, not by a hash-order lottery.

            out = cycle.evaluate_once(
                trigger_ref="evt:v08:real-reading", act=True)
            # Named first: a cap/offer that dropped the reading option is a
            # different fact from "she chose nothing", and this is the fact the
            # 2026-11-15 parent run hit.
            self.assertTrue(cognition.reading_offered(), cognition.offered_refs)
            offered = cognition.offered_refs
            offered_refs = [
                ref for row in offered for ref in [row["source_ref"]] + row["target_refs"]
            ]
            self.assertEqual(len(offered), 4, offered)
            self.assertTrue(
                any("v03_care:bed_rest_block" in ref
                    or "v03_care-bed_rest_block" in ref for ref in offered_refs),
                offered)
            self.assertTrue(
                any("v03_care:warm_hydration" in ref
                    or "v03_care-warm_hydration" in ref for ref in offered_refs),
                offered)
            self.assertGreaterEqual(
                len(out.get("body_candidate_cap_dropped") or []), 1, out)
            self.assertEqual(out.get("body_candidate_cap"), 4, out)
            self.assertEqual(out["outcome"], "ACTED", out)
            self.assertEqual(out["step_path"], "READING_AR0", out)
            self.assertEqual((out.get("reading") or {}).get("page"), 1,
                             out.get("reading"))
            self.assertTrue(out["capacity_kwargs"].get("body_creative_block"),
                            out.get("capacity_kwargs"))
            self.assertEqual(out["capacity_kwargs"].get("body_candidate_cap"), 4,
                             out.get("capacity_kwargs"))
            self.assertEqual(out.get("capacity_state"), "LIMITED",
                             out.get("capacity_state"))
            for key in ("decision_id", "adoption_ref", "activity_ref",
                        "integration_id", "correlation_id"):
                self.assertTrue(out.get(key), (key, out))

            after_doc = self._head(data["private_items_artifact_id"])
            after_work = self._head(data["work_artifact_id"])
            self.assertNotEqual(after_doc["version_id"], before_doc["version_id"],
                                "the reading step did not advance the bookmark")
            self.assertEqual(after_work["version_id"], before_work["version_id"],
                             "a reading step must not commit the work artifact")
            positions = self._reading_positions(data)
            self.assertEqual([row["position"] for row in positions], [1],
                             positions)

            # The gate was active in this very round: no creative rejection was
            # aimed at the reading candidate the owner then executed.
            state = runtime.decision_store.load_state()
            selected = out["selected_candidate_ref"]
            creative_rejections = [
                row for row in (state.get("constraint_results_by_id") or {}).values()
                if ag1.HARD_REJECT_BODY_CREATIVE_BLOCK in (row.get("reason_codes") or [])
            ]
            self.assertGreaterEqual(len(creative_rejections), 2,
                                    creative_rejections)
            self.assertFalse(
                any(row.get("candidate_ref") == selected
                    for row in creative_rejections),
                creative_rejections,
            )

            print(json.dumps({
                "v08_real_chain": {
                    "seeded_at": seeded_at,
                    "v03_receipts": receipts,
                    "root": str(self.instance),
                    "decision_id": out.get("decision_id"),
                    "adoption_ref": out.get("adoption_ref"),
                    "activity_ref": out.get("activity_ref"),
                    "integration_id": out.get("integration_id"),
                    "correlation_id": out.get("correlation_id"),
                    "selected_candidate_ref": selected,
                    "capacity_kwargs": out.get("capacity_kwargs"),
                    "reading": out.get("reading"),
                    "private_items_head_before": before_doc["version_id"],
                    "private_items_head_after": after_doc["version_id"],
                    "work_head_before": before_work["version_id"],
                    "work_head_after": after_work["version_id"],
                    "creative_rejections": len(creative_rejections),
                }
            }, ensure_ascii=False, sort_keys=True))

        # -- the full illness -> recovery -> work chain ----------------------

        def _health(self, port) -> dict:
            signals = port.body()
            return ((signals.get("data") or {}).get("health") or {})

        def _settle(self, *, action_kind, operation_key, moment, **extra):
            import v02_v03_daily_loop_bridge as vbridge

            event = {
                "actor_side": "subject",
                "effect_state": "APPLIED",
                "receipt_ref": "rcpt:%s" % operation_key,
                "action_kind": action_kind,
                "target_now_iso": moment.isoformat(),
                "operation_key": operation_key,
            }
            event.update(extra)
            return vbridge.settle_v02_v03_round_consequences(
                self.instance, event, now_iso=moment.isoformat())

        def test_formal_recovery_reoffers_the_work_and_advances_it(self):
            """Same real root: acute refuses the work, formal care heals, the work returns.

            The illness course is written by v03's own command (not a JSON edit);
            recovery runs through ``settle_v02_v03_round_consequences`` with the
            owner-confirmed REST / WARM_HYDRATION / ADVANCE_CLOCK events; the
            work is then offered again by the same artifact source and really
            advances a version.
            """

            import autonomous_life_assembly as assembly
            import one_day_life as odl

            data = self._prepared()
            seeded_at, _receipts = self._seed_acute_course()
            env = self.instance_env()
            port = self._body_port(env)
            self.assertEqual(self._health(port).get("status"), "ACUTE_ILLNESS")

            cognition = _offered_reading_cognition(
                ["READ", "READ", "READ", "WORK"],
                reading_artifact_id=data["reading_artifact_id"],
                work_artifact_id=data["work_artifact_id"],
            )
            runtime = self._runtime(cognition, env, data)
            assembled = assembly.assemble_autonomous_life(
                runtime, environment=env,
                state_root=odl.life_state_root(self.instance),
                world_provider=StubObservation(env), writer=StubWriter(),
                enabled=True)
            self.assertEqual(assembled["state"], assembly.READY, assembled)
            cycle = assembled["cycle"]

            acute = cycle.evaluate_once(trigger_ref="evt:v08:recover-acute", act=True)
            self.assertEqual(acute["outcome"], "ACTED", acute)
            self.assertEqual(acute["step_path"], "READING_AR0", acute)
            self.assertEqual((acute.get("reading") or {}).get("page"), 1,
                             acute.get("reading"))
            work_before = self._head(data["work_artifact_id"])
            self.assertEqual(self._health(port).get("status"), "ACUTE_ILLNESS")

            # -- formal v03 recovery (owner-confirmed round events only) ------
            moment = datetime.now(timezone.utc)
            settled_receipts: list[dict] = []
            for index in range(1, 13):
                moment = moment + timedelta(hours=6)
                settled = self._settle(
                    action_kind="REST", operation_key="v08-recover-rest-%d" % index,
                    moment=moment, care_event_kind="bed_rest_block",
                    rest_mode="sleep_like")
                self.assertEqual(settled.get("status"), "APPLIED", settled)
                settled_receipts.append({
                    "operation_key": settled.get("operation_key"),
                    "slices": settled.get("slices_updated"),
                })
                if self._health(port).get("status") == "HEALTHY":
                    break
            moment = moment + timedelta(hours=1)
            warm = self._settle(
                action_kind="WARM_HYDRATION", operation_key="v08-recover-warm",
                moment=moment, care_event_kind="warm_hydration",
                rest_mode="sleep_like")
            self.assertEqual(warm.get("status"), "APPLIED", warm)
            moment = moment + timedelta(hours=1)
            clock = self._settle(
                action_kind="ADVANCE_CLOCK", operation_key="v08-recover-clock",
                moment=moment, advance_clock_to_iso=moment.isoformat(),
                rest_mode="sleep_like")
            self.assertEqual(clock.get("status"), "APPLIED", clock)

            health = self._health(port)
            self.assertEqual(health.get("status"), "HEALTHY", health)
            self.assertFalse(health.get("creative_block"), health)
            view = self._situation(env, port)
            self.assertFalse(view["body"]["creative_block"], view["body"])
            self.assertIsNone(view["body"]["candidate_cap"], view["body"])

            # -- the same root re-offers the work, and it really advances -----
            second = cycle.evaluate_once(trigger_ref="evt:v08:recover-read2", act=True)
            self.assertEqual(second["outcome"], "ACTED", second)
            self.assertEqual((second.get("reading") or {}).get("page"), 2,
                             second.get("reading"))
            third = cycle.evaluate_once(trigger_ref="evt:v08:recover-read3", act=True)
            self.assertEqual(third["outcome"], "ACTED", third)
            self.assertEqual((third.get("reading") or {}).get("page"), 3,
                             third.get("reading"))
            self.assertEqual(self._head(data["work_artifact_id"])["version_id"],
                             work_before["version_id"])

            work_round = cycle.evaluate_once(trigger_ref="evt:v08:recover-work", act=True)
            self.assertEqual(work_round["outcome"], "ACTED", work_round)
            work_after = self._head(data["work_artifact_id"])
            self.assertNotEqual(work_after["version_id"], work_before["version_id"],
                                "the recovered body must be able to continue the work")
            self.assertGreater(int(work_after.get("version_number") or 0),
                               int(work_before.get("version_number") or 0))

            print(json.dumps({
                "v08_recovery_chain": {
                    "seeded_at": seeded_at,
                    "root": str(self.instance),
                    "acute_decision": acute.get("decision_id"),
                    "acute_reading_page": (acute.get("reading") or {}).get("page"),
                    "settled": settled_receipts,
                    "warm_slices": warm.get("slices_updated"),
                    "clock_slices": clock.get("slices_updated"),
                    "recovered_health": health.get("status"),
                    "recovered_candidate_cap": view["body"]["candidate_cap"],
                    "work_head_before": work_before["version_id"],
                    "work_head_after": work_after["version_id"],
                    "work_version_before": work_before.get("version_number"),
                    "work_version_after": work_after.get("version_number"),
                }
            }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main()

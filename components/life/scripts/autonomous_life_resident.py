#!/usr/bin/env python3
"""B8-6: the resident process that lets autonomous life continue without a user message.

Why a separate process
----------------------
B8-2 left ``runner_tick()`` with no caller but the CLI, and the obvious place to hook it --
Hermes' ``pre_llm_call`` -- would make her autonomous life depend on someone talking to her.
This process owns one state root and drives the same Runner, the same Cycle and the same event
feeds that B8-2 assembled.  It adds a loop and a record, and nothing else.

The one rule that matters
-------------------------
One state root, one writer.  The canonical writer lease that ``ProductionLifeRuntime`` takes
*is* the mutual exclusion; this process never cancels a lease to resolve a conflict, and a
second process for the same root is refused by the existing ``ActivityLockError``.

What it deliberately does not do
--------------------------------
* No scheduler platform, no task queue, no database of its own.
* No back-fill: a check window missed while the process was down is not replayed.
* No model call when no window is due and no real event arrived.
* No message to anyone: the proactive switch stays off in this batch.

Startup, in the order the work order gives it
---------------------------------------------
read the instance configuration and the state root -> build the *one* real runtime -> take the
existing writer lease -> assemble Cycle/Runner/EventFeedPoller -> reconcile pending/UNKNOWN once
-> enter the low-frequency loop.

Usage
-----
    python3 autonomous_life_resident.py --state-root <dir> [--poll-seconds 180]
    python3 autonomous_life_resident.py --status --state-root <dir>
    python3 autonomous_life_resident.py --once --state-root <dir>
    python3 autonomous_life_resident.py --max-ticks 5 --poll-seconds 1 --state-root <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit
import life_pause

HERE = Path(__file__).resolve().parent
_COMPONENTS = HERE.parents[1]
for _path in (str(HERE), str(_COMPONENTS / "native"), str(_COMPONENTS / "native" / "src"),
              str(_COMPONENTS / "native" / "memory_runtime_v1"),
              str(_COMPONENTS.parent / "chiyo_bundle")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

RESIDENT_SUMMARY_FILENAME = "resident_summary.json"
RESIDENT_JOURNAL_FILENAME = "resident_journal.jsonl"
DEFAULT_POLL_SECONDS = 180
SOAK_24H_SECONDS = 24 * 3600
SOAK_7D_SECONDS = 7 * 24 * 3600

STATE_ROOT_ENV = ("NYA02_RESIDENT_STATE", "NYA02_LIFE_STATE")
INSTALL_ROOT_ENV = ("CHIYO_LIFE_INSTALL_ROOT", "NYA02_RESIDENT_INSTALL_ROOT")
MEMORY_DB_ENV = ("NYA02_MEMORY_DB", "CHIYO_LIFE_MEMORY_DB")
MEMORY_CONTROLS_ENV = ("NYA02_MEMORY_CONTROLS_DB", "CHIYO_LIFE_MEMORY_CONTROLS_DB")
MEMORY_SUBJECT_ENV = ("NYA02_MEMORY_SUBJECT", "NYA02_SUBJECT")
MODEL_BASE_URL_ENV = "NYA02_MODEL_BASE_URL"

#: The class names the runner already uses, named here so the record reads as words.
CLASS_BLOCKED = "blocked"
CLASS_ACTED = "acted"
CLASS_UNKNOWN = "unknown"
REASON_MODEL_CONNECTION = "BLOCKED: MODEL_CONNECTION"

def _cycle_constant(name: str, fallback: str) -> str:
    """One of the cycle's own constants, read from the cycle instead of re-typed here.

    The record and the chain have to spell an outcome the same way.  A literal the cycle never
    emits counts as zero forever, which is exactly what ``counters.defer`` did: the cycle
    reports ``OUTCOME_DEFERRED`` while the record was compared against ``"DEFER"``.
    """

    try:
        import autonomous_life_cycle as alc
    except Exception:
        return fallback
    return str(getattr(alc, name, fallback))


#: The cycle's own word for a round it deferred, so the two cannot drift apart.
OUTCOME_DEFERRED = _cycle_constant("OUTCOME_DEFERRED", "DEFERRED")


def _flag(environment: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(environment.get(name, "true" if default else "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _first(environment: Mapping[str, str], names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        value = str(environment.get(name) or "").strip()
        if value:
            return value
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _compact_round_view(view: Any, keys: tuple[str, ...]) -> Optional[dict[str, Any]]:
    """A bounded, JSON-safe projection of one reader's last answer.

    The resident's record must stay serializable and small: a reader's raw view is kept
    only where it is named here, and anything else is dropped rather than guessed.
    """

    if not isinstance(view, Mapping):
        return None
    out: dict[str, Any] = {}
    for key in keys:
        if key in view:
            out[key] = view[key]
    try:
        json.dumps(out, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"unserializable": True}
    return out



def _parse(text: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def probe_model_provider(environment: Mapping[str, str], *,
                         timeout_s: float = 5.0) -> dict[str, Any]:
    """A bounded, *free* reachability pre-check of the configured provider endpoint.

    It opens one TCP connection to the provider's host and port and closes it.  No request is
    sent, so this costs nothing and can never be counted as a model call; it only separates
    "the endpoint is unreachable" from "the endpoint answered".  A window that then fails is
    reported as ``BLOCKED: MODEL_CONNECTION`` rather than as her choosing to do nothing.
    """

    base_url = str(environment.get(MODEL_BASE_URL_ENV) or "").strip()
    if not base_url:
        return {"status": "NOT_CONFIGURED", "detail": "no %s" % MODEL_BASE_URL_ENV}
    parts = urlsplit(base_url if "//" in base_url else "https://" + base_url)
    host = parts.hostname
    if not host:
        return {"status": "NOT_CONFIGURED", "detail": "unparsable base_url"}
    port = parts.port or (443 if parts.scheme != "http" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            pass
    except OSError as exc:
        return {"status": "UNREACHABLE", "host": host, "port": port,
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    return {"status": "REACHABLE", "host": host, "port": port}

def set_paused(state_root: Path | str, paused: bool, *, reason: str = "operator") -> dict[str, Any]:
    """The life-side persistent pause (W05).  A pure file write: works on a stopped instance.

    This is deliberately **not** the World's pause (``world_control.json``).  Stopping her
    thinking and stopping the World's next travel segment are two different switches over two
    different owners, so neither command may flip the other: ``pause`` here never writes the
    World's control file, and the World runner's ``pause``/``resume`` never write this one.
    """

    return life_pause.set_paused(state_root, paused, reason=reason)


def resolve_world_action_adapter(environment: Mapping[str, str], world: Mapping[str, Any]) -> Any:
    """The AR-0 World adapter for this instance (W05).

    With ``CHIYO_WORLD_TRAVEL_TIME_ENABLED`` on, the action goes through the W04 journey engine:
    a MOVE commits a *timed departure* through the same single World owner, and the arrival is
    settled later by real time.  With it off, the previous instant path is kept unchanged, so a
    profile that never turned walking time on behaves exactly as before.

    Either way this returns an adapter or ``None``: a World that is not configured, unreachable or
    not ready yields no adapter, and the cycle then cannot move her at all.
    """

    if not isinstance(world, Mapping) or world.get("status") != "READY":
        return None
    if _flag(environment, "CHIYO_WORLD_TRAVEL_TIME_ENABLED", False):
        from world_journey_action_adapter import (JourneyWorldResolver,
                                                  RealJourneyWorldActionAdapter)

        return RealJourneyWorldActionAdapter(JourneyWorldResolver(environment=environment))
    from world_action_bridge_adapter import RealWorldActionAdapter, WorldBridgeResolver

    return RealWorldActionAdapter(
        WorldBridgeResolver(environment=environment, bridge_dir=world.get("bridge_dir")))


class AutonomousLifeResident:
    """One state root, one runtime, one loop, one honest record."""

    def __init__(self, *, state_root: Path | str, environment: Optional[Mapping[str, str]] = None,
                 poll_seconds: int = DEFAULT_POLL_SECONDS, act: bool = True,
                 clock: Any = None, sleeper: Any = None) -> None:
        self.state_root = Path(state_root)
        self.env = dict(os.environ if environment is None else environment)
        self.poll_seconds = max(1, int(poll_seconds))
        self.act = bool(act)
        self._clock = clock or _now
        self._sleeper = sleeper or time.sleep
        self.runtime: Any = None
        self.assembled: Any = None
        self.last_tick: Optional[dict[str, Any]] = None
        self.last_error: Optional[str] = None
        #: W05: the last journey settlement record, kept for the tick record and the report.
        self.last_settlement: Optional[dict[str, Any]] = None
        self.started_at: Optional[str] = None
        self.soak_started_at: Optional[str] = None
        self.stopped_at: Optional[str] = None
        self.starts = 0
        self.ticks = 0
        self.model_probe: dict[str, Any] = {}
        #: Kept on the instance, so a later write (a tick, or a stop) cannot drop them.
        self.baseline_facts: dict[str, Any] = {}
        self.reconciled_at_start: list[dict[str, Any]] = []
        self.counters: dict[str, Any] = {
            "wakes": 0, "by_class": {}, "outcomes": {}, "model_calls": 0,
            "check_windows": 0, "event_wakes": 0, "real_events": 0,
            "windows_with_material": 0, "no_action": 0, "defer": 0,
            "blocked_reasons": {}, "actions": 0, "owner_confirmed_versions": 0,
            "unknown_settlements": 0, "reconciliations": 0,
            "pending_unknown_last": 0, "pending_unknown_max": 0,
            "journey_settlements": 0, "journey_arrivals": 0,
            "journey_arrival_refusals": 0, "paused_windows": 0,
        }
        self._stop = threading.Event()
        self._stop_reason: Optional[str] = None

    # -- paths ---------------------------------------------------------------------------

    @property
    def summary_path(self) -> Path:
        return self.state_root / RESIDENT_SUMMARY_FILENAME

    @property
    def journal_path(self) -> Path:
        return self.state_root / RESIDENT_JOURNAL_FILENAME

    def read_status(self) -> dict[str, Any]:
        """The persisted record, readable from any process without touching the runtime."""
        try:
            return json.loads(self.summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"kind": "autonomous_life_resident", "state_root": str(self.state_root),
                    "present": False}

    def _write_summary(self, payload: Mapping[str, Any]) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        tmp = self.summary_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=1, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, self.summary_path)

    def _journal(self, record: Mapping[str, Any]) -> None:
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")

    # -- memory boundary (optional, and only when a database is configured) ---------------

    def _memory_ports(self) -> tuple[Any, Any, Any]:
        """The M0 admission consumer, the bounded experience view and the read port.

        ``(None, None, None)`` when no Memory database is configured.  The port is handed
        back as well because C's continuity reader needs the *same* M0 owner boundary: it
        must read the admitted past through the owner, not through a second handle.
        """

        database = _first(self.env, MEMORY_DB_ENV)
        if not database:
            return None, None, None
        try:
            from life_experience_port import LifeExperiencePort
            from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
        except Exception as exc:
            self.last_error = "MEMORY_IMPORT_FAILED: %s: %s" % (type(exc).__name__, exc)
            return None, None, None
        subject = _first(self.env, MEMORY_SUBJECT_ENV) or "chiyo"
        try:
            port = LifeExperiencePort(database, controls_db=_first(self.env, MEMORY_CONTROLS_ENV),
                                      subject=subject)
        except Exception as exc:
            self.last_error = "MEMORY_PORT_FAILED: %s: %s" % (type(exc).__name__, exc)
            return None, None, None
        return (MemoryAdmissionExperienceConsumer(port=port, enabled=True),
                lambda: port.prompt_view(), port)

    def _continuity_source(self, runtime: Any) -> Any:
        """C's read-only continuity reader over the owners this process already holds.

        The binding's subject is the *Memory* owner (``chiyo``), which is a different
        concept from the canonical Activity subject (``chiyo.global``) and from the Supply
        workspace subject: C requires the past binding to name the Memory owner it really
        is, and the Activity side to be this runtime's own controlled home.
        """

        memory = _first(self.env, MEMORY_DB_ENV)
        if not memory:
            return None
        try:
            from life_continuity_inputs import ContinuityBinding, LifeContinuityInputs
            from life_experience_port import LifeExperiencePort
            from memory_control import MemoryControlLedger
            from life_experience_port import life_conversation_id
        except Exception as exc:
            self.last_error = "CONTINUITY_IMPORT_FAILED: %s: %s" % (type(exc).__name__, exc)
            return None
        owner = _first(self.env, MEMORY_SUBJECT_ENV) or "chiyo"
        controls = _first(self.env, MEMORY_CONTROLS_ENV) or memory
        home = str(self.env.get("CHIYO_WORLD_HOME") or self.state_root)
        try:
            ledger = MemoryControlLedger(controls, memory, owner=owner,
                                         conversation=life_conversation_id(owner))
            return LifeContinuityInputs(
                binding=ContinuityBinding(
                    subject=owner, home=home, now=_iso(self._clock()),
                    owner_refs={
                        "memory": "memory:%s" % owner,
                        "activity": "activity:canonical",
                        "agenda": "agenda:lr4",
                        "artifact": "artifact:supply",
                    }),
                past_source=LifeExperiencePort(memory, controls_db=controls, subject=owner),
                activity_source=getattr(runtime, "activity_read", None),
                agenda_source=getattr(runtime, "waiting_coordinator", None),
                governance_probe=lambda: {"healthy": bool(ledger.projection().healthy)})
        except Exception as exc:
            self.last_error = "CONTINUITY_SOURCE_FAILED: %s: %s" % (type(exc).__name__, exc)
            return None

    # -- start / stop --------------------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Build the one runtime, take the lease, assemble, and reconcile once."""

        import autonomous_life_assembly as assembly
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp

        self.state_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_root, 0o700)
        previous = self.read_status()
        moment = self._clock()
        self.started_at = _iso(moment)
        # The soak clock is the *first* start of this state root, not this process: a restart
        # must not restart the observation window, or "24 hours" would mean nothing.
        self.soak_started_at = previous.get("started_at") or self.started_at
        self.starts = int(previous.get("starts") or 0) + 1
        self.model_probe = probe_model_provider(self.env)
        self._replay_journal()
        self._load_model_env_file()

        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=_flag(self.env, "LIFE_RUNTIME_ENABLED", True),
            agency_enabled=_flag(self.env, "AGENCY_ENABLED", True),
            action_execution_enabled=_flag(self.env, "ACTION_EXECUTION_ENABLED", True),
            # No autonomous message to anyone.  This batch keeps external proactivity off.
            proactive_enabled=False)

        supply = assembly.resolve_supply_configuration(self.env)
        port = adapter = None
        if not supply["missing"]:
            from life_artifact_port import LifeArtifactPort
            from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter

            port = LifeArtifactPort(supply["supply_socket"], supply["subject_id"])
            adapter = SupplyManagedArtifactActionAdapter(
                port, grant_id=supply["artifact_grant_id"],
                workspace_id=self.env.get("CHIYO_LIFE_SUPPLY_WORKSPACE_ID"))

        # One resolution of the World seam, used for both halves: the observation feed and the
        # action adapter.  A World that is not configured, unreachable or not ready yields no
        # provider and a named reason, and the feed is then recorded UNAVAILABLE.
        world = assembly.resolve_world_provider(self.env)
        world_adapter = None
        world_adapter = resolve_world_action_adapter(self.env, world)

        consumer, experience_provider, memory_port = self._memory_ports()
        # W05R: the persisted request budget is built *before* either model surface, so the
        # ceilings apply to the very first decision and the very first content generation.
        # A window that this caller may not open is a refusal, not a silent new allowance.
        try:
            requests = assembly.request_budget(self.env, self.state_root)
        except Exception as exc:
            self.last_error = "REQUEST_BUDGET_REFUSED: %s: %s" % (type(exc).__name__, exc)
            return {"ok": False, "reason": "REQUEST_BUDGET_REFUSED", "detail": self.last_error}
        # W05R-R1b: the decision transport is admitted at each attempt's send boundary
        # against this instance's own pause bit (the same state root the operator writes).
        admission = assembly.pause_request_admission(self.state_root)
        cognition = assembly.model_cognition(self.env, requests=requests,
                                             admission=admission, pause_guard=admission)
        try:
            self.runtime = lrp.build_production_life_runtime(
                self.state_root, kill_switches=switches, cognition_adapter=cognition,
                install_root=self.state_root, artifact_action_adapter=adapter,
                world_action_adapter=world_adapter, experience_consumer=consumer)
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return {"ok": False, "reason": "RUNTIME_REFUSED", "detail": self.last_error}

        # D (life-v1): C's read-only continuity reader, built from the same Memory owner
        # boundary this process already holds.  It is handed over as a *factory* so every read
        # carries a fresh binding instant and a fresh control-ledger probe instead of one
        # frozen at startup.  With no Memory database configured there is no reader, and the
        # cycle records PAST_SOURCE_NOT_WIRED rather than "no history".
        continuity_source = (None if not _first(self.env, MEMORY_DB_ENV)
                             else (lambda: self._continuity_source(self.runtime)))
        # A (life-v1): there is no S07 affect owner in this tree, so the resident has no
        # short-appraisal source of its own and passes none.  The seam (cycle -> assembly ->
        # decision context) is wired and reports ``appraisal_wired: false`` instead of inventing
        # a feeling, a mood or a stable fact; an explicit experimental input may supply one.
        appraisal_source = None
        assembled = assembly.assemble_autonomous_life(
            self.runtime, environment=self.env, state_root=self.state_root,
            experience_provider=experience_provider, world_provider=world["provider"],
            requests=requests, continuity_source=continuity_source,
            appraisal_source=appraisal_source)
        if assembled.get("state") != assembly.READY:
            self.assembled = assembled
            self.last_error = assembled.get("reason")
            return {"ok": False, "reason": assembled.get("reason", "NOT_READY"),
                    "detail": assembled.get("detail"), "missing": assembled.get("missing")}
        self.assembled = assembled

        # One reconciliation before the loop: a result left UNKNOWN by a previous process is
        # settled here, under the original operation, before any new work is considered.
        reconciled = self.assembled["runner"].reconcile_pending()
        self.counters["reconciliations"] += len(reconciled)

        payload = self.summary()
        self.reconciled_at_start = list(reconciled)
        self.baseline_facts = self.baseline()
        payload["reconciled_at_start"] = self.reconciled_at_start
        payload["baseline"] = self.baseline_facts
        self._write_summary(payload)
        return {"ok": True, "reason": "", "state_root": str(self.state_root),
                "world": assembled.get("world"), "feeds": assembled.get("feeds"),
                "autonomous_enabled": assembled.get("enabled"),
                "model": {"probe": self.model_probe, "cognition": cognition is not None,
                          # The assembly does not hand the writer back on its own key: the writer
                          # it built *is* the cycle's writer, and that is the one that will really
                          # produce the text.  Reading it there is what makes the record match the
                          # chain that was assembled, instead of reporting "no writer" forever.
                          "content_writer": getattr(assembled.get("cycle"), "writer", None)
                                            is not None},
                "reconciled_at_start": reconciled}

    def baseline(self) -> dict[str, Any]:
        """What the world looked like at this start, read from the owners, never guessed."""

        out: dict[str, Any] = {}
        try:
            runtime_state = dict(self.runtime.canonical_state() or {})
            out["foreground_activity_ref"] = runtime_state.get("foreground_activity_ref")
            out["revision"] = runtime_state.get("revision")
        except Exception as exc:
            out["runtime_state_error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            point = self.assembled["artifact_port"].latest_resumable()
            out["work"] = point.to_dict() if point is not None else None
        except Exception as exc:
            out["work_error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            out["pending_unknown_actions"] = [
                a.get("action_id") for a in self.assembled["runner"].pending_unknown_actions()]
        except Exception as exc:
            out["pending_error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            out["budget"] = self.assembled["cycle"].budget.snapshot()
        except Exception as exc:
            out["budget_error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            out["world"] = self.assembled.get("world")
            out["world_opportunities"] = list(
                getattr(self.assembled["cycle"], "last_world_opportunities", []) or [])
        except Exception as exc:
            out["world_error"] = "%s: %s" % (type(exc).__name__, exc)
        return out

    def request_stop(self, *, reason: str = "operator") -> None:
        self._stop_reason = reason
        self._stop.set()

    def stop(self) -> dict[str, Any]:
        """Release the runtime, and with it the canonical writer lease."""

        payload = self.summary()
        runtime, self.runtime = self.runtime, None
        if runtime is not None:
            try:
                runtime.close()
            except Exception as exc:
                self.last_error = "CLOSE_FAILED: %s: %s" % (type(exc).__name__, exc)
        self.assembled = None
        self.stopped_at = _iso(self._clock())
        payload.update({"running": False, "stopped_at": self.stopped_at,
                        "stop_reason": self._stop_reason, "last_error": self.last_error})
        self._write_summary(payload)
        return {"ok": True, "stopped_at": self.stopped_at, "reason": self._stop_reason}

    # -- the loop ------------------------------------------------------------------------

    def tick(self) -> dict[str, Any]:
        """One bounded step: poll the real feeds, then run the check window if it is due."""

        import autonomous_life_assembly as assembly

        if self.assembled is None or self.assembled.get("state") != assembly.READY:
            return {"ok": False, "reason": "NOT_STARTED"}

        # W05: real time first.  An already departed travel segment arrives on its own schedule,
        # and a journey that completed is turned into an arrival here -- against the World's own
        # observation, never against the requested target.  Nothing about a journey is settled by
        # the decision window, and the decision window is not gated on the journey.
        settled = self.settle_journey()

        result = assembly.runner_tick(self.assembled, act=self.act)
        try:
            reconciled = self.assembled["runner"].reconcile_pending()
        except Exception as exc:  # a read failure is reported, never hidden
            self.last_error = "RECONCILE_FAILED: %s: %s" % (type(exc).__name__, exc)
            reconciled = []
        self.counters["reconciliations"] += len(reconciled)

        record = {"at": _iso(self._clock()), "tick": self.ticks + 1,
                  "events": list(result.get("events") or []),
                  "feed_errors": list(result.get("feed_errors") or []),
                  "due": result.get("due"), "reconciled": reconciled,
                  "journey": settled}
        self.ticks += 1
        self.last_tick = record
        self._journal(record)
        self._accumulate(record)
        self._write_summary(self.summary())
        return record

    def _accumulate(self, record: Mapping[str, Any]) -> None:
        for wake in [record.get("due") or {}] + list(record.get("events") or []):
            self._count(wake)

    def _count(self, wake: Mapping[str, Any]) -> None:
        status_class = str(wake.get("status_class") or "")
        outcome = str(wake.get("outcome") or "")
        kind = str(wake.get("kind") or "")
        status = str(wake.get("status") or "")

        self.counters["wakes"] += 1
        self.counters["by_class"][status_class] = \
            self.counters["by_class"].get(status_class, 0) + 1
        if outcome:
            self.counters["outcomes"][outcome] = self.counters["outcomes"].get(outcome, 0) + 1
        self.counters["model_calls"] += int(wake.get("llm_calls") or 0)

        if kind == "CHECK_WINDOW" and status != "NOT_DUE":
            self.counters["check_windows"] += 1
        if kind == "EVENT":
            self.counters["event_wakes"] += 1
            if status not in {"DUPLICATE_EVENT", "NOT_DUE"}:
                self.counters["real_events"] += 1
        if outcome and outcome != "NO_MATERIAL":
            self.counters["windows_with_material"] += 1
        if outcome == "NO_ACTION":
            self.counters["no_action"] += 1
        if outcome == OUTCOME_DEFERRED:
            self.counters["defer"] += 1
        if status == "PAUSED_BY_OPERATOR":
            # W05: the life-side pause held the window.  Nothing was considered, so this is
            # neither a choice she made nor a technical refusal.
            self.counters["paused_windows"] += 1
        if status_class == CLASS_BLOCKED:
            text = ("%s %s" % (outcome, wake.get("detail") or "")).strip()
            # A provider that cannot be reached is not a choice she made, so it is named
            # separately instead of being folded into "she did nothing".
            reason = (REASON_MODEL_CONNECTION
                      if "MODEL_CONNECTION" in text or "MODEL_UNREACHABLE" in text
                      else text[:120] or "BLOCKED")
            self.counters["blocked_reasons"][reason] = \
                self.counters["blocked_reasons"].get(reason, 0) + 1
        if status_class == CLASS_ACTED:
            self.counters["actions"] += 1
            if outcome == "ACTED":
                self.counters["owner_confirmed_versions"] += 1
        if status_class == CLASS_UNKNOWN:
            self.counters["unknown_settlements"] += 1
        try:
            pending = len(self.assembled["runner"].pending_unknown_actions())
        except Exception:
            pending = 0
        self.counters["pending_unknown_last"] = pending
        self.counters["pending_unknown_max"] = max(self.counters["pending_unknown_max"], pending)

    def run(self, *, max_ticks: Optional[int] = None,
            duration_seconds: Optional[int] = None) -> dict[str, Any]:
        """The low-frequency loop.  Nothing happens between windows, on purpose."""

        began = self._clock()
        deadline = (began.timestamp() + float(duration_seconds)) if duration_seconds else None
        while not self._stop.is_set():
            self.tick()
            if max_ticks is not None and self.ticks >= int(max_ticks):
                break
            if deadline is not None and self._clock().timestamp() >= deadline:
                break
            # A one-second interruptible wait: the loop never spins per second, and a stop
            # signal is still honoured promptly.
            waited = 0.0
            while waited < self.poll_seconds and not self._stop.is_set():
                step = min(1.0, self.poll_seconds - waited)
                self._sleeper(step)
                waited += step
        return {"ticks": self.ticks, "stopped": self._stop.is_set(),
                "stop_reason": self._stop_reason,
                "observed_seconds": int((self._clock() - began).total_seconds())}

    # -- the journey half (W05) -----------------------------------------------------------

    def settle_journey(self) -> dict[str, Any]:
        """Let real time settle the W04 world journey.  Never starts a new target.

        This runs on every tick, before the consideration window: an already departed segment
        has to be able to arrive even in a tick that then decides nothing.
        """
        cycle = (self.assembled or {}).get("cycle")
        if cycle is None or not hasattr(cycle, "settle_world_journey"):
            return {"ok": False, "reason_code": "NO_CYCLE"}
        try:
            record = dict(cycle.settle_world_journey() or {})
        except Exception as exc:  # a real failure is reported, never hidden
            self.last_error = "JOURNEY_SETTLE_FAILED: %s: %s" % (type(exc).__name__, exc)
            return {"ok": False, "reason_code": "JOURNEY_SETTLE_FAILED",
                    "detail": self.last_error}
        self.last_settlement = record
        self.counters["journey_settlements"] += 1
        if record.get("arrived"):
            self.counters["journey_arrivals"] += 1
        if record.get("arrival_refused"):
            self.counters["journey_arrival_refusals"] += 1
        return record

    def journey_view(self) -> dict[str, Any]:
        """A read-only view of the journey, for ``status`` and the 24h report."""

        import autonomous_life_cycle as alc

        cycle = (self.assembled or {}).get("cycle")
        home = getattr(cycle, "world_home", None) if cycle is not None else None
        view: dict[str, Any] = {
            "configured": bool(home),
            "last_settlement": self.last_settlement,
            "arrivals": self.counters.get("journey_arrivals", 0),
            "settlements": self.counters.get("journey_settlements", 0),
        }
        if not home:
            return view
        try:
            journey = alc.journey_module()
            view["projection"] = journey.observe_journey(hermes_home=home,
                                                        environment=self.env)
        except Exception as exc:
            view["error"] = "%s: %s" % (type(exc).__name__, exc)
        return view

    def _replay_journal(self) -> None:
        """Rebuild the counters from the durable journal, so a restart keeps the record.

        The counters live in memory, so without this a restart would silently reset "how many
        windows ran, and what happened" to zero while the journal still held the truth.
        """

        try:
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            self._accumulate(record)
            self.ticks = max(self.ticks, int(record.get("tick") or 0))

    def _load_model_env_file(self) -> None:
        """Take the provider credentials from a 0600 file, never from the command line.

        A key handed over as ``env VAR=... python3`` is world-readable in ``ps``; a 0600 file the
        service identity owns is not.  An already-set variable still wins, so an operator who
        exported one deliberately is not overridden.
        """

        try:
            text = (self.state_root.parent / "model_env").read_text(encoding="utf-8")
        except OSError:
            return
        for line in text.splitlines():
            name, _, value = line.partition("=")
            name = name.strip()
            if name.startswith("NYA02_MODEL_") and value.strip() and not self.env.get(name):
                self.env[name] = value.strip()

    def _model_calls_detail(self) -> dict[str, Any]:
        """The provider requests, counted from the persisted ledger when there is one.

        W05R: ``max_cognition_calls_per_day`` never bounded the content generation, and the
        writer's own ``call_count`` is per process, so a restart made the day look cheaper
        than it was.  When the instance carries a ``LifeRequestBudget`` the numbers below
        come from that ledger instead: it counts every attempt -- decision, content
        generation and schema retry -- and it survives a restart.
        """

        cycle = (self.assembled or {}).get("cycle")
        requests = getattr(getattr(cycle, "budget", None), "requests", None)
        if requests is not None:
            view = requests.view()
            return {"cognition_calls": int(self.counters.get("model_calls") or 0),
                    "content_generation_calls": getattr(
                        getattr(cycle, "writer", None), "call_count", None),
                    "total_requests": view.get("utc_day_calls"),
                    "content_generation_scope": "counted by the persisted request ledger",
                    "daily_budget_counts": "decision + content + retry requests",
                    "request_budget": view}
        cognition = int(self.counters.get("model_calls") or 0)
        writer = getattr(cycle, "writer", None)
        content = getattr(writer, "call_count", None)
        total = cognition + content if isinstance(content, int) else None
        return {"cognition_calls": cognition,
                "content_generation_calls": content,
                "total_requests": total,
                "content_generation_scope": "this process (not replayed from the journal)",
                "daily_budget_counts": "cognition calls only"}

    # -- the record ----------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        moment = self._clock()
        started = _parse(self.soak_started_at)
        observed = int((moment - started).total_seconds()) if started else 0
        assembled = self.assembled or {}
        cycle = assembled.get("cycle")
        try:
            budget = cycle.budget.snapshot() if cycle is not None else None
        except Exception:
            budget = None
        return {
            "kind": "autonomous_life_resident",
            "pid": os.getpid(),
            "state_root": str(self.state_root),
            "started_at": self.soak_started_at,
            "this_process_started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "running": self.runtime is not None,
            "starts": self.starts,
            "ticks": self.ticks,
            "last_tick_at": (self.last_tick or {}).get("at"),
            "observed_seconds": observed,
            # A wall clock can only report that the time threshold has been reached.  It cannot
            # read the journal, cannot prove the process stayed up and cannot see the owner's
            # results, so it must never mint the acceptance label itself: `SOAK_VERIFIED_24H`
            # is a judgement a reviewer records after reading those three, not a number this
            # counter can produce.  The recorded history is left exactly as it is.
            "soak_24h": ("SOAK_THRESHOLD_REACHED_24H" if observed >= SOAK_24H_SECONDS
                         else "SOAK_RUNNING_24H"),
            "soak_7d": ("SOAK_THRESHOLD_REACHED_7D" if observed >= SOAK_7D_SECONDS
                        else "SOAK_RUNNING_7D" if observed >= SOAK_24H_SECONDS
                        else "SOAK_PENDING_7D"),
            "poll_seconds": self.poll_seconds,
            "act": self.act,
            "autonomous_enabled": (getattr(assembled.get("runner"), "enabled", None)
                                   if assembled.get("runner") is not None
                                   else assembled.get("enabled")),
            "interval_minutes": getattr(assembled.get("runner"), "interval_minutes", None),
            "feeds": assembled.get("feeds"),
            "world": assembled.get("world"),
            # W05: the two switches are reported separately on purpose -- the Life's own pause
            # and the World's own pause are different owners' bits.
            # D (life-v1): what was wired and what the last round actually read.  The two
            # readers are reported as facts, not as "everything is fine".
            "work_artifact_id": assembled.get("work_artifact_id"),
            "situation_wiring": assembled.get("situation"),
            "continuity_wired": assembled.get("continuity_wired"),
            "last_situation": _compact_round_view(
                getattr(cycle, "last_situation", None),
                ("reason_code", "observed_at", "identity_bound", "body", "resource_budget",
                 "dimensions", "limiting_dimensions", "unknown_dimensions", "knowledge")),
            "last_continuity": _compact_round_view(
                getattr(cycle, "last_continuity", None),
                ("past_state", "next_step", "source_states", "refusals", "llm_calls",
                 "read_error", "watermark_write_error")),
            "work_selection": getattr(cycle, "last_resume_selection", None),
            "pause": life_pause.read(self.state_root),
            "journey": self.journey_view(),
            "model": {"probe": self.model_probe,
                      "cognition_attached": getattr(cycle, "runtime", None) is not None},
            "budget": budget,
            "counters": self.counters,
            # A reader must not read the cognition counter as the whole provider bill: one acted
            # round makes a decision call *and* a content call, and only the first is budgeted.
            "model_calls_detail": self._model_calls_detail(),
            "baseline": self.baseline_facts,
            "reconciled_at_start": self.reconciled_at_start,
            "last_error": self.last_error,
        }


def _install_signal_handlers(resident: AutonomousLifeResident) -> None:
    def handler(signum, _frame):  # pragma: no cover - exercised by the real process
        resident.request_stop(reason="signal:%s" % signum)

    for name in ("SIGTERM", "SIGINT"):
        number = getattr(signal, name, None)
        if number is not None:
            try:
                signal.signal(number, handler)
            except (ValueError, OSError):
                pass


def _resolve_state_root(args, environment: Mapping[str, str]) -> Optional[str]:
    if args.state_root:
        return args.state_root
    found = _first(environment, STATE_ROOT_ENV)
    if found:
        return found
    install = args.install_root or _first(environment, INSTALL_ROOT_ENV)
    return str(Path(install) / "state") if install else None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="The resident driver for autonomous life (B8-6).")
    parser.add_argument("--state-root", help="the Life state root this process owns")
    parser.add_argument("--install-root", help="the instance root; state root defaults to <it>/state")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--max-ticks", type=int)
    parser.add_argument("--duration-seconds", type=int)
    parser.add_argument("--once", action="store_true", help="exactly one tick, then stop")
    parser.add_argument("--status", action="store_true", help="print the persisted record and exit")
    parser.add_argument("--pause", action="store_true",
                        help="persist the life-side pause (stops new decisions) and exit")
    parser.add_argument("--resume", action="store_true",
                        help="clear the life-side pause and exit")
    parser.add_argument("--reason", default="operator", help="recorded with pause/resume")
    parser.add_argument("--no-act", action="store_true", help="consider but never execute")
    args = parser.parse_args(argv)

    environment = dict(os.environ)
    state_root = _resolve_state_root(args, environment)
    if not state_root:
        print(json.dumps({"ok": False, "reason": "NO_STATE_ROOT",
                          "detail": "pass --state-root or set %s" % STATE_ROOT_ENV[0]},
                         ensure_ascii=False))
        return 2

    resident = AutonomousLifeResident(state_root=state_root, environment=environment,
                                      poll_seconds=args.poll_seconds, act=not args.no_act)
    if args.status:
        print(json.dumps(resident.read_status(), ensure_ascii=False, indent=1, sort_keys=True))
        return 0
    if args.pause or args.resume:
        # A pure file write, so it works on a stopped instance and never needs the model.
        view = set_paused(state_root, bool(args.pause), reason=args.reason)
        print(json.dumps({"ok": True, "pause": view}, ensure_ascii=False, indent=1,
                         sort_keys=True))
        return 0

    started = resident.start()
    print(json.dumps({"started": started}, ensure_ascii=False, indent=1, sort_keys=True),
          flush=True)
    if not started.get("ok"):
        resident.stop()
        return 3

    _install_signal_handlers(resident)
    try:
        if args.once:
            resident.tick()
        else:
            result = resident.run(max_ticks=args.max_ticks,
                                  duration_seconds=args.duration_seconds)
            print(json.dumps({"ran": result}, ensure_ascii=False, sort_keys=True), flush=True)
    finally:
        stopped = resident.stop()
    print(json.dumps({"stopped": stopped, "record": resident.read_status()},
                     ensure_ascii=False, indent=1, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

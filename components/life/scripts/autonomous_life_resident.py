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

    def _memory_ports(self) -> tuple[Any, Any]:
        """The M0 admission consumer and the bounded experience view, or ``(None, None)``."""

        database = _first(self.env, MEMORY_DB_ENV)
        if not database:
            return None, None
        try:
            from life_experience_port import LifeExperiencePort
            from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
        except Exception as exc:
            self.last_error = "MEMORY_IMPORT_FAILED: %s: %s" % (type(exc).__name__, exc)
            return None, None
        subject = _first(self.env, MEMORY_SUBJECT_ENV) or "chiyo"
        try:
            port = LifeExperiencePort(database, controls_db=_first(self.env, MEMORY_CONTROLS_ENV),
                                      subject=subject)
        except Exception as exc:
            self.last_error = "MEMORY_PORT_FAILED: %s: %s" % (type(exc).__name__, exc)
            return None, None
        return (MemoryAdmissionExperienceConsumer(port=port, enabled=True),
                lambda: port.prompt_view())

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
        if world["status"] == "READY":
            from world_action_bridge_adapter import RealWorldActionAdapter, WorldBridgeResolver

            world_adapter = RealWorldActionAdapter(
                WorldBridgeResolver(environment=self.env, bridge_dir=world["bridge_dir"]))

        consumer, experience_provider = self._memory_ports()
        cognition = assembly.model_cognition(self.env)

        try:
            self.runtime = lrp.build_production_life_runtime(
                self.state_root, kill_switches=switches, cognition_adapter=cognition,
                install_root=self.state_root, artifact_action_adapter=adapter,
                world_action_adapter=world_adapter, experience_consumer=consumer)
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return {"ok": False, "reason": "RUNTIME_REFUSED", "detail": self.last_error}

        assembled = assembly.assemble_autonomous_life(
            self.runtime, environment=self.env, state_root=self.state_root,
            experience_provider=experience_provider, world_provider=world["provider"])
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
                  "due": result.get("due"), "reconciled": reconciled}
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
            # Only a window that really lasted 24 hours may carry the verified label.
            "soak_24h": ("SOAK_VERIFIED_24H" if observed >= SOAK_24H_SECONDS
                         else "SOAK_RUNNING_24H"),
            "soak_7d": ("SOAK_VERIFIED_7D" if observed >= SOAK_7D_SECONDS
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
            "model": {"probe": self.model_probe,
                      "cognition_attached": getattr(cycle, "runtime", None) is not None},
            "budget": budget,
            "counters": self.counters,
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

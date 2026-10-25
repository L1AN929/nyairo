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
import re
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
import life_runtime_coverage as runtime_coverage
import resident_projection_counts as projection_counts

HERE = Path(__file__).resolve().parent
_COMPONENTS = HERE.parents[1]
for _path in (str(HERE), str(_COMPONENTS / "native"), str(_COMPONENTS / "native" / "src"),
              str(_COMPONENTS / "native" / "memory_runtime_v1"),
              str(_COMPONENTS.parent / "chiyo_bundle")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

RESIDENT_SUMMARY_FILENAME = "resident_summary.json"
RESIDENT_JOURNAL_FILENAME = "resident_journal.jsonl"
#: The process ledger: one row per process that ever owned this state root, with that process's
#: own monotonic measurement.  Kept beside the summary so the summary can stay small, and so a
#: restart can tell "the previous process closed" from "the previous process vanished".
RESIDENT_COVERAGE_FILENAME = "resident_coverage.json"
DEFAULT_POLL_SECONDS = 180
SOAK_24H_SECONDS = runtime_coverage.SOAK_24H_SECONDS
SOAK_7D_SECONDS = runtime_coverage.SOAK_7D_SECONDS

STATE_ROOT_ENV = ("NYA02_RESIDENT_STATE", "NYA02_LIFE_STATE")
INSTALL_ROOT_ENV = ("CHIYO_LIFE_INSTALL_ROOT", "NYA02_RESIDENT_INSTALL_ROOT")
MEMORY_DB_ENV = ("NYA02_MEMORY_DB", "CHIYO_LIFE_MEMORY_DB")
MEMORY_CONTROLS_ENV = ("NYA02_MEMORY_CONTROLS_DB", "CHIYO_LIFE_MEMORY_CONTROLS_DB")
MEMORY_SUBJECT_ENV = ("NYA02_MEMORY_SUBJECT", "NYA02_SUBJECT")
MODEL_BASE_URL_ENV = "NYA02_MODEL_BASE_URL"
#: 显式压缩演示模式（COMPRESSED_DEMO）只由 ``scripts/fast_day.py`` 写入。缺席即普通
#: 真实时间路径，不加载任何业务时钟。
FASTDAY_CLOCK_ENV = "NYA02_FASTDAY_CLOCK"
FASTDAY_DOMAIN_ENV = "NYA02_FASTDAY_DOMAIN"
FASTDAY_RUN_ID_ENV = "NYA02_FASTDAY_RUN_ID"

#: The class names the runner already uses, named here so the record reads as words.
CLASS_BLOCKED = "blocked"
CLASS_ACTED = "acted"
CLASS_UNKNOWN = "unknown"
REASON_MODEL_CONNECTION = "BLOCKED: MODEL_CONNECTION"

#: The cognition adapter's telemetry, written to *this* resident's own journal.  It is a
#: diagnostic record, not a tick: ``_accumulate`` skips it so replaying the journal rebuilds
#: exactly the counters it rebuilt before this record existed.
COGNITION_DIAGNOSTIC_KIND = "cognition_diagnostic"

#: Closed vocabularies.  A value outside them is either dropped or reported ``UNKNOWN``;
#: nothing the model wrote is copied through as a free-form string.
_SAFE_COGNITION_EVENTS = frozenset({
    "cognition_send_started", "cognition_send_response_received", "cognition_send_failed",
    "cognition_provider_error", "cognition_result", "cognition_late_result",
    "cognition_budget_refused", "cognition_epoch_replay",
})
_SAFE_ERROR_CATEGORIES = frozenset({
    "BUDGET_EXHAUSTED", "TRANSPORT_TIMEOUT", "TRANSPORT_CONNECTION", "PROVIDER_RATE_LIMITED",
    "PROVIDER_SERVER_ERROR", "PROVIDER_AUTH_ERROR", "PROVIDER_MALFORMED_RESPONSE",
    "PROVIDER_UNAVAILABLE", "CONFIG_MISSING", "PROVIDER_OUTPUT_TRUNCATED",
    "PROVIDER_REASONING_ONLY", "PROVIDER_EMPTY_CONTENT", "PROVIDER_REQUEST_REJECTED",
})
_SAFE_FAILURE_CLASSES = frozenset({
    "DECISION_ATTEMPT_FAILED:MODEL_TIMEOUT", "DECISION_ATTEMPT_FAILED:MODEL_CONNECTION",
    "DECISION_ATTEMPT_FAILED:MODEL_RATE_LIMITED",
    "DECISION_ATTEMPT_FAILED:MODEL_PROVIDER_ERROR",
    "DECISION_ATTEMPT_FAILED:MODEL_AUTH_ERROR", "DECISION_ATTEMPT_FAILED:MODEL_UNAVAILABLE",
    "DECISION_ATTEMPT_FAILED:INVALID_JSON",
    "DECISION_ATTEMPT_FAILED:OUTPUT_TRUNCATED",
    "DECISION_ATTEMPT_FAILED:REASONING_ONLY_NO_ANSWER",
    "DECISION_ATTEMPT_FAILED:EMPTY_MODEL_OUTPUT",
    "DECISION_ATTEMPT_FAILED:MODEL_REQUEST_REJECTED",
    "DECISION_ATTEMPT_FAILED:COGNITION_BUDGET_EXHAUSTED",
    "DECISION_ATTEMPT_FAILED:LATE_RESULT_DISCARDED",
})
_SAFE_PARSE_STATES = frozenset({"raw_text", "json_object", "json_non_object", "unparsed_text"})
_SAFE_FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_calls",
                                  "function_call"})
_SAFE_BUDGET_SCOPES = frozenset({"utc_day", "run_window", "budget"})
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9:_.+\-]{1,160}")


def _bounded_int(value: Any, *, low: int, high: int) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if low <= value <= high else None


def _safe_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _safe_enum(value: Any, allowed: frozenset[str]) -> Optional[str]:
    return value if isinstance(value, str) and value in allowed else None


def _safe_token(value: Any) -> Optional[str]:
    """An identifier the adapter generated itself (attempt id, run ref, hashed id)."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if _SAFE_TOKEN.fullmatch(text) else None


def safe_cognition_diagnostic(record: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Rebuild one adapter telemetry record value by value, or ``None`` to drop it.

    The adapter's record is *not* safe to persist as-is: it carries ``output_keys`` (model
    written key names), ``decision_kind_if_valid`` and the provider/model strings, any of
    which may contain private text.  Only known events and closed-vocabulary/bounded values
    are rebuilt here; prompt, completion, reasoning, exception messages, headers, tokens and
    arbitrary key names never enter the result.
    """

    if not isinstance(record, Mapping):
        return None
    event = _safe_enum(record.get("event"), _SAFE_COGNITION_EVENTS)
    if event is None:
        return None
    out: dict[str, Any] = {"event": event}

    if "error_category" in record:
        out["error_category"] = (_safe_enum(record.get("error_category"),
                                            _SAFE_ERROR_CATEGORIES) or "UNKNOWN")
    if "failure_class" in record:
        # A mapped failure class is a short closed-vocabulary word; anything else is unknown.
        out["failure_class"] = (_safe_enum(record.get("failure_class"),
                                          _SAFE_FAILURE_CLASSES) or "UNKNOWN")
    if "parse_state" in record:
        out["parse_state"] = _safe_enum(record.get("parse_state"), _SAFE_PARSE_STATES) or "UNKNOWN"
    if "scope" in record:
        out["scope"] = _safe_enum(record.get("scope"), _SAFE_BUDGET_SCOPES) or "UNKNOWN"

    for name, high in (("schema_retry_index", 64), ("response_status", 599),
                       ("candidate_count", 256), ("latency_ms", 3_600_000),
                       ("elapsed_ms", 3_600_000), ("deadline_ms", 3_600_000)):
        value = _bounded_int(record.get(name), low=0, high=high)
        if value is not None:
            out[name] = value

    for name in ("late", "response_verifiable", "response_received", "late_result_received",
                 "late_result_promoted"):
        value = _safe_bool(record.get(name))
        if value is not None:
            out[name] = value

    for name in ("transport_attempt_id", "model_run_ref", "opportunity_id"):
        token = _safe_token(record.get(name))
        if token is not None:
            out[name] = token
    started_at = _safe_token(record.get("started_at"))
    if started_at is not None:
        out["started_at"] = started_at

    # ``output_keys`` names are model-written and may contain private text: keep only a count.
    keys = record.get("output_keys")
    if isinstance(keys, (list, tuple)):
        out["output_key_count"] = min(len(keys), 256)

    shape = record.get("output_shape")
    if isinstance(shape, Mapping):
        for name, high in (("content_chars", 10_000_000), ("reasoning_chars", 10_000_000),
                           ("parse_error_offset", 10_000_000),
                           ("max_output_tokens_sent", 10_000_000),
                           # Provider-reported integer token counts.  A missing or
                           # non-integer value is omitted, never written as a fabricated 0.
                           ("input_tokens", 10_000_000), ("output_tokens", 10_000_000),
                           ("total_tokens", 10_000_000), ("reasoning_tokens", 10_000_000)):
            value = _bounded_int(shape.get(name), low=0, high=high)
            if value is not None:
                out[name] = value
        for name in ("fenced", "json_parsed", "reasoning_present"):
            value = _safe_bool(shape.get(name))
            if value is not None:
                out[name] = value
        if "finish_reason" in shape:
            out["finish_reason"] = (_safe_enum(shape.get("finish_reason"),
                                               _SAFE_FINISH_REASONS) or "UNKNOWN")
    return out

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


def resolve_world_action_adapter(environment: Mapping[str, str], world: Mapping[str, Any],
                                 *, clock: Any = None) -> Any:
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

        # 显式压缩演示模式：出发/到达与 Life 结算读同一个业务域。普通路径 ``clock=None``，
        # 旅程引擎保持原来的真实墙钟；一次性授权与 gateway 消费始终走真实墙钟。
        return RealJourneyWorldActionAdapter(
            JourneyWorldResolver(environment=environment, now=clock))
    from world_action_bridge_adapter import RealWorldActionAdapter, WorldBridgeResolver

    return RealWorldActionAdapter(
        WorldBridgeResolver(environment=environment, bridge_dir=world.get("bridge_dir")))


class AutonomousLifeResident:
    """One state root, one runtime, one loop, one honest record."""

    def __init__(self, *, state_root: Path | str, environment: Optional[Mapping[str, str]] = None,
                 poll_seconds: int = DEFAULT_POLL_SECONDS, act: bool = True,
                 clock: Any = None, sleeper: Any = None, monotonic: Any = None,
                 segment_request_cap: Optional[int] = None,
                 business_clock: Any = None, offline_scripted: bool = False) -> None:
        """``segment_request_cap`` is *this process only*: at most that many more HTTP
        attempts than the persisted ledger had already spent when the segment started.

        It can only ever **lower** the instance's own ceilings
        (``min(configured, used + cap)``): it never opens a run window, never resets the
        ledger and never raises a limit, so a short operator segment (for example 600s /
        6 requests) cannot buy itself a fresh allowance.
        """
        self.state_root = Path(state_root)
        self.env = dict(os.environ if environment is None else environment)
        self.poll_seconds = max(1, int(poll_seconds))
        #: Only a per-process *lowering* of the persisted ceilings (see the docstring above).
        self.segment_request_cap: Optional[int] = (None if segment_request_cap is None
                                                  else max(1, int(segment_request_cap)))
        self.segment_cap: dict[str, Any] = {"requested": self.segment_request_cap,
                                            "applied": False, "source": "PROCESS_SEGMENT_ONLY"}
        self.act = bool(act)
        #: 真实墙钟：只用于进程覆盖账、lease 与真实超时。压缩演示模式不改它。
        self._clock = clock or _now
        #: 业务时钟（COMPRESSED_DEMO 时注入）。它只影响业务到期/安静/业务事件时序；
        #: 上面的 ``_clock`` 与 ``_monotonic`` 保持真实。缺席即普通真实时间路径。
        self._business_clock = business_clock
        self.business_clock_error: Optional[dict[str, Any]] = None
        #: 只为零模型冒烟：装配一个**不发请求**的既有确定性 writer 占位。它只能和
        #: ``act=False`` 一起用；summary 会明确标出，绝不当成她的写作或真实模型链。
        self.offline_scripted = bool(offline_scripted)
        #: ``time.monotonic()`` is the only clock that can prove *this* process stayed up: it is
        #: per process and per boot, so it is never added to another process's measurement and
        #: never used to bridge a gap.  Injected in tests, real everywhere else.
        self._monotonic = monotonic or time.monotonic
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
        #: R: this process's own identity and measurement, kept apart from the state root.
        self.process_id: Optional[str] = None
        self.process_open = False
        self._process_monotonic_start: Optional[float] = None
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
            "owner_version_unknown": 0, "owner_version_refs": {},
            "unknown_settlements": 0, "reconciliations": 0,
            "pending_unknown_last": 0, "pending_unknown_max": 0,
            "journey_settlements": 0, "journey_arrivals": 0,
            "journey_arrival_refusals": 0, "paused_windows": 0,
            #: Windows whose local rule resolved an empty/all-rejected candidate set.  They
            #: spent no model request, so they are counted apart from ``model_calls``.
            "rule_resolved_windows": 0,
        }
        #: 读面去重状态：同一完成旅程/连续静默窗口在每个 tick 都会重新投影一次，
        #: 不能按投影行数累加。实时 tick 与 journal replay 共用同一套算术。
        # 有界缓存只做加速；被淘汰的旅程/拒绝键在缓存未命中时回读既有 journal。
        self._projections = projection_counts.ProjectionCounter(
            history=self._journal_journey_history)
        self._stop = threading.Event()
        self._stop_reason: Optional[str] = None

    # -- 业务时钟（显式压缩演示模式，默认缺席） ------------------------------------------

    def _resolve_business_clock(self) -> Any:
        """从显式环境取业务时钟客户端；缺/坏/域不符返回 ``None`` 并由调用方拒绝启动。

        普通生产没有 ``NYA02_FASTDAY_CLOCK``，这里什么也不做，路径与以前完全一致。
        """

        if self._business_clock is not None:
            return self._business_clock
        path = str(self.env.get(FASTDAY_CLOCK_ENV) or "").strip()
        if not path:
            return None
        try:
            import demo_business_clock as dbc
        except Exception as exc:
            self.business_clock_error = {
                "reason_code": "CLOCK_IMPORT_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
            return None
        try:
            client = dbc.BusinessClockClient(
                path,
                expected_domain=(str(self.env.get(FASTDAY_DOMAIN_ENV) or "").strip() or None),
                expected_run_id=(str(self.env.get(FASTDAY_RUN_ID_ENV) or "").strip() or None))
            client.load()
        except dbc.BusinessClockError as exc:
            # 缺时钟/损坏/域不匹配：具名拒绝，绝不退回系统墙钟（那会悄悄混钟）。
            self.business_clock_error = {"reason_code": exc.reason_code,
                                         "detail": exc.detail}
            return None
        except Exception as exc:
            self.business_clock_error = {
                "reason_code": "CLOCK_UNAVAILABLE",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
            return None
        return client

    def _business_now(self) -> datetime:
        clock = self._business_clock
        if clock is None:
            return self._clock()
        return clock.now()

    def _business_iso(self) -> str:
        return _iso(self._business_now())

    def time_domain(self) -> dict[str, Any]:
        """双时间域：业务时刻与真实时刻分开报告，谁都不能冒充另一个。"""

        if self._business_clock is None:
            return {"mode": "REAL_TIME", "domain_id": None, "rate": 1.0,
                    "logical_at": _iso(self._clock()), "real_at": _iso(self._clock()),
                    "not_real_24h": False, "note": "普通真实时间路径"}
        try:
            snap = self._business_clock.snapshot()
        except Exception as exc:
            return {"mode": "COMPRESSED_DEMO", "domain_id": None, "rate": None,
                    "logical_at": None, "real_at": _iso(self._clock()),
                    "not_real_24h": True,
                    "error": "%s: %s" % (type(exc).__name__, exc)}
        return {"mode": snap.get("mode", "COMPRESSED_DEMO"),
                "domain_id": snap.get("domain_id"), "run_id": snap.get("run_id"),
                "rate": snap.get("rate"), "state": snap.get("state"),
                "logical_at": snap.get("logical_at"),
                "logical_elapsed_seconds": snap.get("logical_elapsed_seconds"),
                "horizon_logical": snap.get("horizon_logical"),
                "horizon_remaining_seconds": snap.get("horizon_remaining_seconds"),
                "real_at": _iso(self._clock()),
                "not_real_24h": True,
                "note": "压缩逻辑日，不是真实 24 小时；真实超时/权限/请求 UTC 日不加速"}

    # -- paths ---------------------------------------------------------------------------

    @property
    def summary_path(self) -> Path:
        return self.state_root / RESIDENT_SUMMARY_FILENAME

    @property
    def journal_path(self) -> Path:
        return self.state_root / RESIDENT_JOURNAL_FILENAME

    @property
    def coverage_path(self) -> Path:
        return self.state_root / RESIDENT_COVERAGE_FILENAME

    # -- the process ledger (R) ----------------------------------------------------------

    def _coverage_read(self) -> dict[str, Any]:
        """The persisted process ledger, or ``{}`` when this state root never wrote one.

        Only a real ledger counts.  A summary without one is *not* upgraded into a ledger: an
        old record's wall span cannot be turned back into a per-process measurement.
        """

        try:
            data = json.loads(self.coverage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or not isinstance(data.get("processes"), list):
            return {}
        return data

    def _coverage_rows(self) -> list[dict[str, Any]]:
        rows = self._coverage_read().get("processes") or []
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    def _coverage_write(self, rows: list[dict[str, Any]]) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        payload = {"kind": runtime_coverage.KIND,
                   "schema_version": runtime_coverage.SCHEMA_VERSION,
                   "state_root": str(self.state_root),
                   "processes": list(rows)}
        tmp = self.coverage_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, self.coverage_path)

    def _live_online_seconds(self) -> Optional[int]:
        """What *this* process has measured itself alive for, from its own monotonic clock."""

        if self._process_monotonic_start is None:
            return None
        return max(0, int(self._monotonic() - self._process_monotonic_start))

    def _open_process_record(self) -> None:
        """Append this process to the ledger, unclosed.  A previous unclosed row is left alone.

        A row that never reached its own stop is the evidence that the process vanished, so it
        is never "repaired" on the next start: the interval after its last heartbeat stays
        unknown, and only a real stop can turn a process's interval into coverage.
        """

        moment = self.started_at or _iso(self._clock())
        self.started_at = moment
        self._process_monotonic_start = self._monotonic()
        self.process_id = runtime_coverage.process_identity(
            pid=os.getpid(), started_at=moment, host=socket.gethostname())
        self.process_open = True
        rows = self._coverage_rows()
        rows.append(runtime_coverage.new_process_record(
            process_id=self.process_id, pid=os.getpid(), started_at=moment,
            monotonic_started=self._process_monotonic_start))
        self._coverage_write(rows)

    def _update_process_record(self, *, close: bool = False) -> None:
        """Refresh (or close) this process's own row.  Every other row is left untouched."""

        if not self.process_id:
            return
        rows = self._coverage_rows()
        moment = _iso(self._clock())
        online = self._live_online_seconds()
        for index, row in enumerate(rows):
            if row.get("process_id") != self.process_id:
                continue
            if close:
                rows[index] = runtime_coverage.close_process_record(
                    row, closed_at=moment, online_seconds=online)
            else:
                rows[index] = runtime_coverage.heartbeat(row, at=moment, online_seconds=online)
        self._coverage_write(rows)

    def _first_recorded_start(self) -> Optional[str]:
        """The first start this state root ever recorded, from the ledger when it has one."""

        rows = self._coverage_rows()
        if not rows:
            return None
        return str(rows[0].get("started_at") or "") or None

    def runtime_coverage(self) -> dict[str, Any]:
        """Proven coverage, kept apart from the wall span and from what is unknown."""

        rows = self._coverage_rows()
        moment = self._clock()
        for index, row in enumerate(rows):
            # The live process's own row is refreshed from its monotonic clock, so a summary
            # taken between two ticks is still its real measurement and not the last heartbeat.
            if self.process_id and row.get("process_id") == self.process_id and self.process_open:
                rows[index] = runtime_coverage.heartbeat(
                    row, at=_iso(moment), online_seconds=self._live_online_seconds())
        view = runtime_coverage.coverage(
            rows, now=moment,
            legacy_seconds=(0 if rows else
                            runtime_coverage.legacy_summary_seconds(self.read_status())),
            current_process_id=self.process_id if self.process_open else None)
        return view

    def recorded_coverage(self, *, now: Any = None) -> dict[str, Any]:
        """The ledger as *any* reader sees it: written rows only, no live process claimed.

        ``runtime_coverage()`` refreshes this process's own row from its own monotonic clock,
        which is only true inside the process that is running.  A report -- or any second
        process -- must not borrow that: it reads the ledger as written, so an unclosed row's
        tail (from its last heartbeat to ``now``) stays ``unknown`` instead of being counted as
        measured life, and a summary with no ledger contributes its wall span to ``unknown``
        instead of to coverage.
        """

        rows = self._coverage_rows()
        moment = now if isinstance(now, datetime) else self._clock()
        return runtime_coverage.coverage(
            rows, now=moment,
            legacy_seconds=(0 if rows else
                            runtime_coverage.legacy_summary_seconds(self.read_status())),
            current_process_id=None)

    def _coverage_fields(self) -> dict[str, Any]:
        """The record's coverage block, with the old key kept and honestly re-based."""

        view = self.runtime_coverage()
        labels = runtime_coverage.soak_labels(view)
        covered = int(view.get("covered_seconds") or 0)
        return {
            # The old key is kept so a reader keeps working, but it is no longer the wall span:
            # it is the seconds some process actually measured itself alive for.
            "observed_seconds": covered,
            "observed_seconds_basis": "PROVEN_PROCESS_ONLINE_SECONDS",
            "wall_span_seconds": view.get("wall_span_seconds"),
            "process_online_seconds": view.get("process_online_seconds"),
            "covered_seconds": covered,
            "offline_gap_seconds": view.get("offline_gap_seconds"),
            "unknown_seconds": view.get("unknown_seconds"),
            "coverage_complete": view.get("coverage_complete"),
            "continuous": view.get("continuous"),
            "overlap_seconds": view.get("overlap_seconds"),
            "time_ambiguity": view.get("time_ambiguity"),
            "covered_exceeds_wall_span": view.get("covered_exceeds_wall_span"),
            "wall_rollback": view.get("wall_rollback"),
            "process_id": self.process_id,
            "process_open": self.process_open,
            "soak_24h": labels["soak_24h"],
            "soak_7d": labels["soak_7d"],
            "soak_basis": labels["soak_basis"],
            "soak_verified": labels["soak_verified"],
            "coverage": view,
        }

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

    def _cognition_diagnostic(self, record: Mapping[str, Any]) -> None:
        """Bind the cognition adapter's telemetry to this resident's own journal.

        The sink is the journal the resident already owns.  There is no second ledger, no
        debug store and no shadow-runtime import.  Writing a diagnostic that cannot be
        rebuilt safely drops it; an I/O failure is *not* swallowed, because the adapter's
        required send marker calls this before a provider request and a send whose boundary
        could not be durably recorded must not happen.  Optional records are caught by the
        adapter itself and never break a legal decision.
        """

        diagnostic = safe_cognition_diagnostic(record)
        if diagnostic is None:
            return
        payload: dict[str, Any] = {"kind": COGNITION_DIAGNOSTIC_KIND,
                                   "at": _iso(self._clock())}
        payload.update(diagnostic)
        self._journal(payload)

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

    # -- the operator segment cap (lower-only) -------------------------------------------

    def _apply_segment_cap(self, requests: Any) -> Optional[dict[str, Any]]:
        """Lower this process's ceilings to ``used + segment_request_cap``; refuse if unverifiable.

        The ledger is the only source of "already spent": its own ``view()`` reports the active
        UTC day and this run window's counts.  Nothing here writes the ledger, opens a window or
        raises a ceiling -- ``min(configured, used + cap)`` is the whole rule, so the cap can only
        make this segment cheaper than the instance already allows.  When the requested cap is
        absent this is a no-op.  When the usage cannot be read (corrupt/unreadable ledger) the
        promise could be false, so the segment is refused before any model surface exists.
        """

        if self.segment_request_cap is None:
            return None
        cap = int(self.segment_request_cap)
        if requests is None:
            return {"ok": False, "reason": "SEGMENT_REQUEST_CAP_UNVERIFIED",
                    "detail": ("a segment request cap was asked for, but this instance carries no "
                               "persisted request budget to lower (never inventing a ceiling)")}
        try:
            view = dict(requests.view() or {})
        except Exception as exc:
            return {"ok": False, "reason": "SEGMENT_REQUEST_CAP_UNVERIFIED",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        day_used = view.get("utc_day_calls")
        window_used = view.get("run_window_calls")
        day_max = getattr(requests, "utc_day_max", None)
        window_max = getattr(requests, "run_window_max", None)
        if (day_max is not None and not isinstance(day_used, int)) \
                or (window_max is not None and not isinstance(window_used, int)):
            return {"ok": False, "reason": "SEGMENT_REQUEST_CAP_UNVERIFIED",
                    "detail": ("the persisted request ledger did not report what it already spent "
                               "(state=%s), so 'at most %d more' cannot be promised"
                               % (view.get("state"), cap))}
        applied_day = (None if day_max is None
                       else min(int(day_max), int(day_used) + cap))
        applied_window = (None if window_max is None
                          else min(int(window_max), int(window_used) + cap))
        requests.utc_day_max = applied_day
        requests.run_window_max = applied_window
        self.segment_cap = {
            "requested": cap,
            "applied": True,
            "source": "PROCESS_SEGMENT_ONLY",
            "raised_any_ceiling": False,
            "utc_day": {"configured_max": day_max, "used_at_start": day_used,
                        "applied_max": applied_day},
            "run_window": {"configured_max": window_max, "used_at_start": window_used,
                           "applied_max": applied_window,
                           "run_id": view.get("run_id")},
            "note": ("min(configured, used_at_start + requested); the same run window and the "
                     "same ledger are continued, and no new window is opened"),
        }
        return None
    # -- start / stop --------------------------------------------------------------------

    def _experiment_capped_budget(self, requests: Any) -> Any:
        """显式压缩模式：把实验硬上限放到**每一次预留**的边界上。

        普通路径（没有 ``NYA02_FASTDAY_CLOCK``）原样返回同一个账本对象。显式模式下
        缺 run 身份、时钟域不符、实验上限文件缺失/损坏都抛 ``BusinessClockError``，
        由调用方具名拒绝启动；绝不退化成「没有上限」。
        """

        path = str(self.env.get(FASTDAY_CLOCK_ENV) or "").strip()
        if not path or requests is None:
            return requests
        import demo_business_clock as dbc

        clock = dbc.read_clock_file(path)
        run_id = str(self.env.get("CHIYO_AUTONOMOUS_RUN_ID") or "").strip()
        if not run_id:
            raise dbc.BusinessClockError(
                dbc.REASON_EXPERIMENT_CAP_UNREADABLE,
                "实例没有 run 身份；实验上限无法与账本绑定")
        if clock.get("run_id") != run_id:
            raise dbc.BusinessClockError(
                dbc.REASON_EXPERIMENT_CAP_UNREADABLE,
                "时钟 run %r 与实例账本 run %r 不一致" % (clock.get("run_id"), run_id))
        experiment = Path(path).parent / "experiment_budget.json"
        try:
            stored = json.loads(experiment.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise dbc.BusinessClockError(
                dbc.REASON_EXPERIMENT_CAP_UNREADABLE,
                "%s: %s" % (type(exc).__name__, exc)) from exc
        limit = stored.get("max_new_requests") if isinstance(stored, Mapping) else None
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise dbc.BusinessClockError(dbc.REASON_EXPERIMENT_CAP_UNREADABLE,
                                         "实验上限文件没有可用的 max_new_requests")
        # 显式新总限（例如 300/历史 14/新增 286）只属于这个实例的元数据；旧文件缺字段
        # 时按默认 96/0 解释。文件里的授权总限不能高于实例环境声明的总限。
        total = stored.get("authorized_total_limit", dbc.HARD_CONFIG_MAX) \
            if isinstance(stored, Mapping) else dbc.HARD_CONFIG_MAX
        historical = stored.get("historical_requests", 0) \
            if isinstance(stored, Mapping) else 0
        declared = None
        try:
            raw = str(self.env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY") or "").strip()
            declared = int(raw) if raw else None
        except ValueError:
            declared = None
        if declared is not None and isinstance(total, int) and total > declared:
            raise dbc.BusinessClockError(
                dbc.REASON_EXPERIMENT_CAP_UNREADABLE,
                "实验上限元数据的授权总限 %r 高于实例配置 %r" % (total, declared))
        cap = dbc.ExperimentRequestCap(self.state_root, run_id=run_id,
                                       max_new_requests=limit, total_limit=total,
                                       historical_requests=historical)
        return dbc.CappedRequestBudget(requests, cap)

    def start(self) -> dict[str, Any]:
        """Build the one runtime, take the lease, assemble, and reconcile once."""

        import autonomous_life_assembly as assembly
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp

        if self.offline_scripted and self.act:
            # 脚本替身只证明装配/控制，不能产生她的文字或真实决定。
            return {"ok": False, "reason": "OFFLINE_SCRIPTED_CANNOT_ACT",
                    "detail": "offline_scripted 只能和 act=False（--no-act）一起用"}
        self.state_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_root, 0o700)
        previous = self.read_status()
        moment = self._clock()
        self.started_at = _iso(moment)
        # The soak clock is the *first* start of this state root, not this process: a restart
        # must not restart the observation window, or "24 hours" would mean nothing.  R: when a
        # summary was lost but the process ledger survived, the ledger's first start is used --
        # a restart still does not reset the window, and it still does not make the window real.
        self.soak_started_at = (previous.get("started_at") or self._first_recorded_start()
                                or self.started_at)
        self.starts = max(int(previous.get("starts") or 0), len(self._coverage_rows())) + 1
        # R: this process's own row, with its own monotonic measurement.  Written before any
        # window, so a crash from here on is visible as an unclosed interval instead of silence.
        self._open_process_record()
        # 显式压缩演示模式的业务时钟：缺/坏/域不符在这里具名拒绝，绝不混钟。
        self._business_clock = self._resolve_business_clock()
        if self.business_clock_error is not None:
            self.last_error = "%s: %s" % (self.business_clock_error.get("reason_code"),
                                          self.business_clock_error.get("detail") or "")
            return {"ok": False,
                    "reason": self.business_clock_error.get("reason_code", "CLOCK_UNAVAILABLE"),
                    "detail": self.business_clock_error.get("detail")}
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
        world = assembly.resolve_world_provider(
            self.env,
            clock=(self._business_now if self._business_clock is not None else None))
        world_adapter = None
        world_adapter = resolve_world_action_adapter(
            self.env, world,
            clock=(self._business_now if self._business_clock is not None else None))

        consumer, experience_provider, memory_port = self._memory_ports()
        # W05R: the persisted request budget is built *before* either model surface, so the
        # ceilings apply to the very first decision and the very first content generation.
        # A window that this caller may not open is a refusal, not a silent new allowance.
        try:
            requests = assembly.request_budget(self.env, self.state_root)
        except Exception as exc:
            self.last_error = "REQUEST_BUDGET_REFUSED: %s: %s" % (type(exc).__name__, exc)
            return {"ok": False, "reason": "REQUEST_BUDGET_REFUSED", "detail": self.last_error}
        # Operator segment cap: only a *lowering* of the ceilings this instance already
        # carries, applied before any model surface exists.  A cap that cannot be verified
        # against the persisted ledger refuses the segment instead of running unbounded.
        cap_refusal = self._apply_segment_cap(requests)
        if cap_refusal is not None:
            self.last_error = str(cap_refusal.get("detail") or "")
            return cap_refusal
        try:
            requests = self._experiment_capped_budget(requests)
        except Exception as exc:
            reason = getattr(exc, "reason_code", None) or "EXPERIMENT_CAP_REFUSED"
            self.last_error = "%s: %s" % (reason, getattr(exc, "detail", str(exc)))
            return {"ok": False, "reason": reason, "detail": str(self.last_error)}
        # W05R-R1b: the decision transport is admitted at each attempt's send boundary
        # against this instance's own pause bit (the same state root the operator writes).
        admission = assembly.pause_request_admission(self.state_root)
        cognition = assembly.model_cognition(self.env, requests=requests,
                                             admission=admission, pause_guard=admission,
                                             observer=self._cognition_diagnostic)
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
        offline_writer = None
        if self.offline_scripted:
            import autonomous_life_cycle as _alc

            offline_writer = _alc.DeterministicContinuationWriter(
                note="（零模型冒烟：没有产生她的文字）")
        assembled = assembly.assemble_autonomous_life(
            self.runtime, environment=self.env, state_root=self.state_root,
            experience_provider=experience_provider, world_provider=world["provider"],
            requests=requests, continuity_source=continuity_source,
            appraisal_source=appraisal_source, writer=offline_writer,
            # 业务时钟只在这里贯通；真实墙钟仍留给进程覆盖/lease/真实超时。
            clock=(self._business_now if self._business_clock is not None else None))
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
        # rc1-C: the work this process will *actually* continue is the cycle's own selection
        # (bound work, or the admitted history's artifact, and -- only when neither exists --
        # the newest ACTIVE artifact that is not one of this instance's non-work documents).
        # ``latest_resumable`` is the port's generic recency rule and does not know which
        # artifact is a piece of writing, so a baseline taken from it alone could report the
        # reading material or the private-items document as "the work" while the round
        # continued something else -- or nothing.
        cycle = self.assembled.get("cycle")
        selection_reader = getattr(cycle, "resume_selection", None)
        try:
            if callable(selection_reader):
                point, selection = selection_reader()
                out["work"] = point.to_dict() if point is not None else None
                out["work_selection"] = selection
                out["work_selection_source"] = "CYCLE_SELECTION"
            else:  # pragma: no cover - every assembled cycle has the reader
                point = self.assembled["artifact_port"].latest_resumable()
                out["work"] = point.to_dict() if point is not None else None
                out["work_selection_source"] = "LATEST_RESUMABLE"
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

        # Taken first, while the cycle is still attached: the payload keeps what the round read.
        payload = self.summary()
        runtime, self.runtime = self.runtime, None
        if runtime is not None:
            try:
                runtime.close()
            except Exception as exc:
                self.last_error = "CLOSE_FAILED: %s: %s" % (type(exc).__name__, exc)
        self.assembled = None
        self.stopped_at = _iso(self._clock())
        # R: this process closes its own row with its own final monotonic measurement.  This is
        # the only thing that turns an interval into coverage; a process that never gets here
        # leaves its row unclosed and its tail unknown.
        self._update_process_record(close=True)
        self.process_open = False
        payload.update({"running": False, "stopped_at": self.stopped_at,
                        "stop_reason": self._stop_reason, "last_error": self.last_error})
        payload.update(self._coverage_fields())
        self._write_summary(payload)
        return {"ok": True, "stopped_at": self.stopped_at, "reason": self._stop_reason,
                "coverage": self.runtime_coverage()}

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
                  # 双时间：``at`` 永远是真实墙钟，``logical_at`` 只在显式演示模式出现。
                  "logical_at": (self._business_iso()
                                 if self._business_clock is not None else None),
                  "events": list(result.get("events") or []),
                  "feed_errors": list(result.get("feed_errors") or []),
                  "due": result.get("due"), "reconciled": reconciled,
                  "journey": settled}
        self.ticks += 1
        self.last_tick = record
        self._journal(record)
        self._accumulate(record)
        # R: the heartbeat is written every tick, so a crash leaves a *measured* last instant
        # behind instead of a start time that would be read as "up since then".
        self._update_process_record()
        self._write_summary(self.summary())
        return record

    def _projection_counter(self) -> projection_counts.ProjectionCounter:
        """读面去重状态；``__new__`` 构造的旧夹具（只设 counters）也能惰性拿到一份。"""

        counter = getattr(self, "_projections", None)
        if counter is None:
            counter = projection_counts.ProjectionCounter(
                history=self._journal_journey_history)
            self._projections = counter
        return counter

    def _journal_journey_history(self, journey_id: str,
                                 before_tick: Any) -> Mapping[str, Any] | None:
        """按目标旅程回读既有 journal 的**先前行**。

        有界缓存只做加速：一个旅程被淘汰后再次出现时，历史最大
        ``segments_confirmed`` 与拒绝键必须从持久行恢复，不能把旧段重新算成新到达。
        边界用当前行的 ``tick``：只读 ``tick < before_tick`` 的行，所以
        ``tick`` 先落盘、后 ``_accumulate`` 的顺序不会把当前行当成过去事件，重放时
        也不会扫描未来行。读不出来返回 ``None``（未知），由计数侧丢弃而不是假计。
        """

        if isinstance(before_tick, bool) or not isinstance(before_tick, int):
            return None
        try:
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            # 还没有 journal：这不是读失败，而是“此前没有任何行”。
            return {"max_confirmed": 0, "refusals": set()}
        except OSError:
            return None
        max_confirmed = 0
        refusals: set[tuple[int, str]] = set()
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, Mapping):
                continue
            tick = record.get("tick")
            if isinstance(tick, bool) or not isinstance(tick, int) or tick >= before_tick:
                continue
            journey = record.get("journey")
            if not isinstance(journey, Mapping) or journey.get("journey_id") != journey_id:
                continue
            if journey.get("ok") is not True:
                continue
            confirmed = journey.get("segments_confirmed")
            if (isinstance(confirmed, int) and not isinstance(confirmed, bool)
                    and confirmed >= 0):
                max_confirmed = max(max_confirmed, confirmed)
            reason = journey.get("arrival_refused")
            if isinstance(reason, str) and reason.strip() and confirmed is not None:
                refusals.add((confirmed, reason.strip()))
        return {"max_confirmed": max_confirmed, "refusals": refusals}

    def _accumulate(self, record: Mapping[str, Any]) -> None:
        # A cognition diagnostic is not a wake.  Counting it would make a journal replay add
        # one phantom wake per provider event and change every counter after a restart.
        if record.get("kind") == COGNITION_DIAGNOSTIC_KIND:
            return
        # 旅程结算：只按 owner 已确认段的单调增量计到达。同一条完成投影在每个 tick
        # 都会再读一次，``arrived`` 布尔值不能当身份；``segments_confirmed`` 才是
        # 段级、可跨重放重建的证据。
        # ``record["tick"]`` 是当前行的明确边界：tick 先落盘再累计，恢复历史时只读
        # tick 更小的行，不会把当前行或重放时的未来行提前当成过去事件。
        arrivals, refusals = self._projection_counter().journey_delta(
            record.get("journey"), before=record.get("tick"))
        self.counters["journey_arrivals"] += arrivals
        self.counters["journey_arrival_refusals"] += refusals
        # 顺序与 runner_tick 的真实调用一致：事件先写 journal，check window 最后。
        for wake in list(record.get("events") or []) + [record.get("due") or {}]:
            self._count(wake)

    def _count(self, wake: Mapping[str, Any]) -> None:
        if not isinstance(wake, Mapping):
            return
        # 请求数只认真实记录里的 llm_calls；被去重的静默投影本来就没有请求，但仍先累计，
        # 保证任何真实调用都不会因为跳过一条 wake 而丢失。
        self.counters["model_calls"] += int(wake.get("llm_calls") or 0)
        # 只有 runner 真的写进 autonomous_wakes.jsonl 的记录才算一个 wake：非静默全写，
        # 连续静默只写第一条。合成 tick dict（没有 at/status_class）不算。
        # 空 due（``{}``）只出现在旧的手工 journal 行，按旧口径保留；真实 runner 总会
        # 给出一条 WakeRecord，不会走到这里。
        if wake and not self._projection_counter().wake_is_recorded(wake):
            return

        status_class = str(wake.get("status_class") or "")
        outcome = str(wake.get("outcome") or "")
        kind = str(wake.get("kind") or "")
        status = str(wake.get("status") or "")

        self.counters["wakes"] += 1
        self.counters["by_class"][status_class] = \
            self.counters["by_class"].get(status_class, 0) + 1
        if outcome:
            self.counters["outcomes"][outcome] = self.counters["outcomes"].get(outcome, 0) + 1
        if (outcome in {"NO_ACTION", OUTCOME_DEFERRED}
                and str((wake.get("extra") or {}).get("decision_route") or "")
                == "RULE_RESOLVED"):
            # A local rule decided this window (empty or all-hard-rejected candidate set).
            # It is a legal quiet window, not a provider call; counting it apart keeps the
            # summary honest even when an older journal row carried a stale ``llm_calls``.
            self.counters["rule_resolved_windows"] = int(
                self.counters.get("rule_resolved_windows") or 0) + 1

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
            version = self._owner_version(wake)
            if version is not None:
                self.counters["owner_confirmed_versions"] += 1
                refs = self.counters["owner_version_refs"]
                refs[version] = refs.get(version, 0) + 1
                for stale in sorted(refs)[:-20]:
                    refs.pop(stale, None)
            else:
                # An ACTED wake proves the chain ran.  It does not prove a version was created
                # by the owner, and the two are no longer counted as the same thing.
                self.counters["owner_version_unknown"] += 1
        if status_class == CLASS_UNKNOWN:
            self.counters["unknown_settlements"] += 1
        try:
            pending = len(self.assembled["runner"].pending_unknown_actions())
        except Exception:
            pending = 0
        self.counters["pending_unknown_last"] = pending
        self.counters["pending_unknown_max"] = max(self.counters["pending_unknown_max"], pending)

    def _owner_version(self, wake: Mapping[str, Any]) -> Optional[str]:
        """The owner-created version this wake *names*, or None when it names nothing.

        The runner's wake record is the only place this process can read the owner's receipt
        from, and today it carries ``extra`` with the budget snapshot only.  The minimal field
        contract this reads (recorded in ``docs/engineering/v04-day-life-runtime-result.md``
        for the runner's writer) is ``extra.owner_version_created`` plus one of
        ``extra.owner_version_id`` / ``extra.step_path``.  Absent that, the answer is UNKNOWN:
        it is never inferred from ``outcome == ACTED``, from an action id, or from a counter.
        """

        extra = wake.get("extra")
        if not isinstance(extra, Mapping):
            return None
        created = extra.get("owner_version_created")
        named = extra.get("owner_version_id") or extra.get("owner_version_ref")
        if isinstance(named, str) and named.strip():
            return named.strip()
        step_path = extra.get("step_path")
        if created is True and isinstance(step_path, str) and step_path.strip():
            return step_path.strip()
        return None

    def _wait(self, seconds: float) -> None:
        """A bounded, interruptible wait: never longer than asked, and never past a deadline."""

        waited = 0.0
        total = max(0.0, float(seconds))
        while waited < total and not self._stop.is_set():
            step = min(1.0, total - waited)
            self._sleeper(step)
            waited += step

    def run(self, *, max_ticks: Optional[int] = None,
            duration_seconds: Optional[int] = None) -> dict[str, Any]:
        """The low-frequency loop.  Nothing happens between windows, on purpose.

        R: the deadline is wall clock (an operator's ``--duration-seconds`` means real hours,
        not process uptime), and the wait between ticks is clipped to what is left of it.  The
        old loop slept a whole ``poll_seconds`` in one-second steps no matter how little time
        was left, so a 60-second run with a 180-second poll took three minutes to stop.
        """

        began = self._clock()
        began_monotonic = self._monotonic()
        deadline = (began.timestamp() + float(duration_seconds)) if duration_seconds else None
        while not self._stop.is_set():
            self.tick()
            if max_ticks is not None and self.ticks >= int(max_ticks):
                break
            # 显式压缩演示模式：逻辑 horizon 到了就收尾，不靠空转把窗口撑住。
            # 真实墙钟的 ``duration_seconds`` 仍然独立生效（下面），冻结不会绕过它。
            if self._business_clock is not None:
                try:
                    if int(self._business_clock.snapshot()
                           .get("horizon_remaining_seconds") or 0) <= 0:
                        break
                except Exception as exc:  # 读不到 horizon 是命名的失败，不是静默继续
                    self.last_error = "CLOCK_READ_FAILED: %s: %s" % (
                        type(exc).__name__, exc)
                    break
            remaining = (None if deadline is None
                         else deadline - self._clock().timestamp())
            if remaining is not None and remaining <= 0:
                break
            wait = float(self.poll_seconds) if remaining is None else min(
                float(self.poll_seconds), remaining)
            if wait <= 0:
                break
            self._wait(wait)
        return {"ticks": self.ticks, "stopped": self._stop.is_set(),
                "stop_reason": self._stop_reason,
                # This process's own monotonic measurement, and the wall span it lived in.  The
                # two are reported separately for the same reason the summary reports them.
                "observed_seconds": max(0, int(self._monotonic() - began_monotonic)),
                "process_online_seconds": max(0, int(self._monotonic() - began_monotonic)),
                "wall_seconds": max(0, int((self._clock() - began).total_seconds())),
                "deadline_seconds": (None if deadline is None
                                     else int(deadline - began.timestamp()))}

    # -- the journey half (W05) -----------------------------------------------------------

    def settle_journey(self, *, observed_at: Optional[str] = None) -> dict[str, Any]:
        """Let the business clock settle the W04 world journey.  Never starts a new target.

        This runs on every tick, before the consideration window: an already departed segment
        has to be able to arrive even in a tick that then decides nothing.  The *travel clock*
        is a business clock: in the explicit demo mode it is the same compressed domain as the
        rest of the day, and in normal production it is the real one.
        """
        cycle = (self.assembled or {}).get("cycle")
        if cycle is None or not hasattr(cycle, "settle_world_journey"):
            return {"ok": False, "reason_code": "NO_CYCLE"}
        try:
            record = dict(cycle.settle_world_journey(
                observed_at=observed_at or self._business_iso()) or {})
        except Exception as exc:  # a real failure is reported, never hidden
            self.last_error = "JOURNEY_SETTLE_FAILED: %s: %s" % (type(exc).__name__, exc)
            return {"ok": False, "reason_code": "JOURNEY_SETTLE_FAILED",
                    "detail": self.last_error}
        self.last_settlement = record
        # 结算检查次数按真实循环递增（每个 tick 一次）。到达数不在这里加：同一条完成
        # 投影会在后续每个 tick 再读一次，计数由 ``_accumulate`` 按 owner 已确认段的
        # 单调增量统一处理，实时路径与 journal replay 因此使用同一口径。
        self.counters["journey_settlements"] += 1
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

    def _writer_calls(self, cycle: Any, name: str) -> Optional[int]:
        """One model surface's own request count for *this* process, or None when unknown."""

        writer = getattr(cycle, name, None)
        count = getattr(writer, "call_count", None)
        return count if isinstance(count, int) and not isinstance(count, bool) else None

    def _model_calls_detail(self) -> dict[str, Any]:
        """The provider requests, counted from the persisted ledger when there is one.

        W05R: ``max_cognition_calls_per_day`` never bounded the content generation, and the
        writer's own ``call_count`` is per process, so a restart made the day look cheaper
        than it was.  When the instance carries a ``LifeRequestBudget`` the total comes from
        that ledger instead: it counts every attempt -- decision, content generation, the
        private reading response and schema retry -- and it survives a restart.

        R: the reading response is a real model request about private material, so it is a
        surface of its own here.  Reading only ``cycle.writer`` made the content total miss it,
        and a missing surface is reported as ``None``/unknown rather than as 0.
        """

        cycle = (self.assembled or {}).get("cycle")
        cognition = int(self.counters.get("model_calls") or 0)
        content = self._writer_calls(cycle, "writer")
        reading = self._writer_calls(cycle, "reading_response_writer")
        surfaces = {"content_generation": content, "reading_response": reading}
        measured = [count for count in (content, reading) if isinstance(count, int)]
        surface_total = sum(measured) if measured else None
        requests = getattr(getattr(cycle, "budget", None), "requests", None)
        base = {"cognition_calls": cognition,
                #: Model-served decision rounds in this process.  A rule-resolved NO_ACTION
                #: (empty candidate set) never reaches the provider and is not counted here.
                "decision_rounds": cognition,
                "rule_resolved_windows": int(
                    self.counters.get("rule_resolved_windows") or 0),
                "content_generation_calls": surface_total,
                "reading_response_calls": reading,
                "surfaces": surfaces,
                "surface_scope": "this process (not replayed from the journal)",
                "reading_response_counted": reading is not None,
                #: The request total comes from the persisted ledger and includes failed and
                #: retried attempts.  No "successful round trips" figure is derived from the
                #: counters: an unknown transport stays unknown instead of becoming a success.
                "count_semantics": ("decision_rounds = wake.llm_calls of model-served rounds; "
                                    "total_requests = persisted reservations (decision + "
                                    "content + reading_response + retry)")}
        if requests is not None:
            view = requests.view()
            ledger_total = view.get("utc_day_calls")
            if isinstance(ledger_total, int) and not isinstance(ledger_total, bool):
                return {**base,
                        "total_requests": ledger_total,
                        "total_source": "PERSISTED_REQUEST_LEDGER",
                        "content_generation_scope": "counted by the persisted request ledger",
                        "daily_budget_counts": ("decision + content + reading_response + "
                                                "retry requests"),
                        "ledger_state": view.get("state"),
                        "request_budget": view}
            # The ledger exists but cannot be read: that is unknown, not a cheap day.
            return {**base,
                    "total_requests": None,
                    "total_source": "LEDGER_UNREADABLE",
                    "ledger_state": view.get("state"),
                    "ledger_reason": view.get("reason_code"),
                    "content_generation_scope": "ledger unreadable; per-surface counts only",
                    "daily_budget_counts": "unknown",
                    "request_budget": view}
        return {**base,
                "total_requests": (cognition + surface_total
                                   if surface_total is not None else None),
                "total_source": "PROCESS_COUNTERS_ONLY",
                "content_generation_scope": "this process (not replayed from the journal)",
                "daily_budget_counts": "cognition calls only",
                "ledger_state": "ABSENT"}

    # -- the record ----------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
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
            # R: `observed_seconds` used to be `now - first_start`, a wall span that counted
            # downtime as life.  The key is kept for old readers, but it is now proven coverage
            # (`covered_seconds`), and the wall span, the measured process time, the offline
            # gaps and the unknown remainder are reported beside it.  No number here mints
            # `SOAK_VERIFIED_24H`: that label is a reviewer's judgement over the journal, the
            # process ledger and the owners' results, so `soak_verified` stays NOT_VERIFIED.
            **self._coverage_fields(),
            "poll_seconds": self.poll_seconds,
            # 业务/真实双时间域。普通路径是 REAL_TIME；只有显式压缩演示模式才出现
            # COMPRESSED_DEMO，并明确写出「不是真实 24 小时」。
            "time_domain": self.time_domain(),
            "business_clock_error": self.business_clock_error,
            "act": self.act,
            "autonomous_enabled": (getattr(assembled.get("runner"), "enabled", None)
                                   if assembled.get("runner") is not None
                                   else assembled.get("enabled")),
            "interval_minutes": getattr(assembled.get("runner"), "interval_minutes", None),
            "feeds": assembled.get("feeds"),
            "world": assembled.get("world"),
            # 装配事实：runtime 是否真建起来、装配状态/原因、owner 是否 READY、Memory
            # 绑定是否真的进了这个进程。零模型冒烟的替身也在这里明说，不冒充真实模型链。
            "offline_scripted": self.offline_scripted,
            "runtime_assembly": {
                "runtime_present": self.runtime is not None,
                "state": assembled.get("state"),
                "reason": assembled.get("reason"),
                "detail": assembled.get("detail"),
                "missing": assembled.get("missing"),
                "world_status": (assembled.get("world") or {}).get("status")
                if isinstance(assembled.get("world"), Mapping) else None,
                "owner_ready": (assembled.get("world") or {}).get("status") == "READY"
                if isinstance(assembled.get("world"), Mapping) else None,
            },
            "memory_binding": {
                "database_configured": bool(_first(self.env, MEMORY_DB_ENV)),
                "controls_configured": bool(_first(self.env, MEMORY_CONTROLS_ENV)),
                "subject": _first(self.env, MEMORY_SUBJECT_ENV),
                "continuity_wired": assembled.get("continuity_wired"),
            },
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
            # Operator segment cap (lower-only).  Reported as it really was applied, so a
            # reader can tell "this segment could spend at most N more" from the instance's own
            # persisted ceilings.
            "segment_request_cap": self.segment_cap,
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
    parser.add_argument("--offline-scripted", action="store_true",
                        dest="offline_scripted",
                        help="零模型冒烟：装配一个不发请求的既有确定性 writer 占位；"
                             "必须与 --no-act 一起用，summary 会明确标注")
    args = parser.parse_args(argv)

    environment = dict(os.environ)
    state_root = _resolve_state_root(args, environment)
    if not state_root:
        print(json.dumps({"ok": False, "reason": "NO_STATE_ROOT",
                          "detail": "pass --state-root or set %s" % STATE_ROOT_ENV[0]},
                         ensure_ascii=False))
        return 2

    resident = AutonomousLifeResident(state_root=state_root, environment=environment,
                                      poll_seconds=args.poll_seconds, act=not args.no_act,
                                      offline_scripted=args.offline_scripted)
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

#!/usr/bin/env python3
"""Read-only runtime health: quiet, stopped and backlogged are three different facts.

Why this module exists
----------------------
An operator asking "is she alive right now?" had two numbers to guess from, and neither one
answers the question:

* ``resident_summary.json`` carries ``running``, but that key is written by the process that
  holds the runtime.  A process that crashed never rewrites it, so ``running: true`` can
  survive a dead process for as long as the file lives.
* a pid is a number.  A recorded pid that still exists may belong to a completely different
  process (pid reuse), and a pid that is absent proves the recorded process is gone -- but it
  does not prove the queue is empty, healthy or stalled.

So the projection keeps four things apart and reads each from the authority that owns it:

1. **recorded** -- what the persisted summary/coverage ledger says, including the fact that
   ``running`` is *the record's claim*, not a status probe;
2. **process** -- a read-only probe of the recorded ``(host, pid, started_at)`` identity against
   this host's ``/proc``.  Matching start time is what makes a pid the same process; the mere
   existence of a pid is not evidence.  A different host, a missing identity or a platform
   without ``/proc`` is ``UNKNOWN`` -- never "alive", never "dead";
3. **progress** -- the last real tick/heartbeat instant, reported as an instant and an age.
   There is **no threshold** here: a long model/owner call and a legitimate WAIT are not
   deadlocks, so a quiet process stays quiet;
4. **queue** -- the persisted AR-0 action state (open submission intents and unresolved
   actions), read directly, never through ``load_state`` (which replays the WAL).  Backlog is
   reported as backlog.  Without a formal timeout/failure fact it is *not* called stalled; a
   recorded definite failure is shown with its original status, apart from an idle queue.

Nothing here writes, starts, stops, recovers, retries, consumes or takes a lease.  It is a
projection over existing authorities, not a second source of truth.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

KIND = "runtime_health_review"
SCHEMA_VERSION = 1

SUMMARY_FILENAME = "resident_summary.json"
COVERAGE_FILENAME = "resident_coverage.json"
ACTION_STATE_FILENAME = "action_reality_state.json"

LIFE_DIRNAME = "life"
LIFE_STATE_DIRNAME = "state"

#: The top-level answer.  Each name says exactly which facts were combined.
VERDICT_ALIVE_QUIET = "ALIVE_QUIET"
VERDICT_ALIVE_PENDING = "ALIVE_WITH_PENDING"
VERDICT_STOPPED = "STOPPED"
VERDICT_RECORD_STALE = "RECORD_STALE_PROCESS_ABSENT"
VERDICT_NO_RECORD = "NO_RECORD"
VERDICT_UNKNOWN = "UNKNOWN"

PROCESS_MATCHED = "MATCHED"
PROCESS_NOT_RUNNING = "NOT_RUNNING"
PROCESS_UNKNOWN = "UNKNOWN"

QUEUE_IDLE = "IDLE"
QUEUE_PENDING = "PENDING"
QUEUE_LAST_FAILURE = "LAST_FAILURE"
QUEUE_ABSENT = "ABSENT"
QUEUE_UNKNOWN = "UNKNOWN"

#: Attempt statuses that are a definite, recorded end of one submit attempt.  They are shown
#: with their original reason; they are not backlog.
ATTEMPT_FAILURE_STATUSES = frozenset({"PRE_SUBMIT_FAILED", "FAILED", "REJECTED"})
ACTION_STATUS_UNKNOWN = "UNKNOWN"

#: The recorded start instant is taken by Python just after the OS started the process, and
#: ``/proc`` start time has clock-tick granularity; a generous-but-finite tolerance keeps a
#: reused pid (started at a different time) from being mistaken for the recorded process.
DEFAULT_START_TOLERANCE_SECONDS = 15.0

#: Only these attempt fields are projected.  No payload, no request text, no narration.
_ATTEMPT_FIELDS = ("status", "error_class", "error_ref", "started_at", "submitted_at")


def _aware(value: Any) -> Optional[datetime]:
    """One instant, always timezone-aware.  A naive timestamp is read as UTC, never local."""

    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _age_seconds(moment: Optional[datetime], now: datetime) -> Optional[int]:
    if moment is None:
        return None
    return max(0, int((now - moment).total_seconds()))


def _read_json(path: Path) -> tuple[bool, Any, Optional[str]]:
    try:
        return True, json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return False, None, "NOT_FOUND"
    except (OSError, ValueError) as exc:
        return False, None, "%s: %s" % (type(exc).__name__, exc)


def state_root_for(instance_root: Path | str) -> Path:
    """The Life state root of a one-day instance.  No instance file is required to read it."""

    return Path(instance_root) / LIFE_DIRNAME / LIFE_STATE_DIRNAME


def _split_process_id(value: Any) -> tuple[Optional[str], Any, Optional[str]]:
    """``host:pid:started_at`` -> its three parts.  The start instant contains colons, so only
    the first two separators are split."""

    if not isinstance(value, str) or not value:
        return None, None, None
    parts = value.split(":", 2)
    if len(parts) != 3:
        return None, None, None
    host, pid, started_at = parts
    try:
        pid_value: Any = int(pid)
    except (TypeError, ValueError):
        pid_value = pid
    return host or None, pid_value, started_at or None


def _recorded_identity(summary: Mapping[str, Any],
                       coverage: Mapping[str, Any]) -> dict[str, Any]:
    """The process identity the record itself carries: host, pid, start instant.

    Prefer the last coverage row (it is the only place a host is recorded).  A summary alone
    has a pid but no host, and a pid without a host cannot be tied to this machine, so it is
    reported as an identity with ``host: None`` rather than assumed local.
    """

    rows = [row for row in (coverage.get("processes") or []) if isinstance(row, Mapping)]
    if rows:
        last = rows[-1]
        host, pid, started_at = _split_process_id(last.get("process_id"))
        return {"source": "COVERAGE_LEDGER", "host": host,
                "pid": last.get("pid", pid), "started_at": started_at or last.get("started_at")}
    pid = summary.get("pid")
    started_at = summary.get("this_process_started_at") or summary.get("started_at")
    try:
        pid = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid = None
    return {"source": "SUMMARY_ONLY", "host": None, "pid": pid, "started_at": started_at}


def _boot_time(proc_root: Path) -> Optional[float]:
    try:
        for line in (proc_root / "stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _proc_start_epoch(pid: int, proc_root: Path) -> tuple[Optional[bool], Optional[float]]:
    """(exists, start epoch).  ``(None, None)`` means the probe could not read ``/proc``."""

    stat_path = proc_root / str(pid) / "stat"
    try:
        text = stat_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, None
    except OSError:
        return True, None
    # The second field (``comm``) may contain spaces and parentheses; split after its last
    # closing parenthesis so field positions stay correct.
    close = text.rfind(")")
    fields = text[close + 2:].split() if close >= 0 else []
    if len(fields) < 20:
        return True, None
    try:
        start_ticks = int(fields[19])  # field 22 (starttime), ``state`` is fields[0]
        boot = _boot_time(proc_root)
        hertz = float(os.sysconf("SC_CLK_TCK"))
    except (TypeError, ValueError, OSError):
        return True, None
    if boot is None:
        return True, None
    return True, boot + start_ticks / hertz


def _cmdline_matches(pid: int, needles: list[str], proc_root: Path) -> Optional[bool]:
    """Whether this process's own command line names the instance/state root.  None = unreadable."""

    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    text = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
    return any(needle and needle in text for needle in needles)


def probe_process(identity: Mapping[str, Any], *, hostname: Optional[str] = None,
                  proc_root: Path | str = "/proc",
                  tolerance_seconds: float = DEFAULT_START_TOLERANCE_SECONDS,
                  needles: Optional[list[str]] = None) -> dict[str, Any]:
    """Tie the recorded identity to one live process, or say why that cannot be done."""

    proc = Path(proc_root)
    current_host = hostname if hostname is not None else socket.gethostname()
    recorded_host = identity.get("host")
    pid = identity.get("pid")
    started = _aware(identity.get("started_at"))
    out: dict[str, Any] = {
        "status": PROCESS_UNKNOWN,
        "basis": None,
        "recorded_host": recorded_host,
        "checked_host": current_host,
        "pid": pid,
        "started_at_recorded": identity.get("started_at"),
        "start_epoch_seen": None,
        "cmdline_matched": None,
        "note": None,
    }

    if not isinstance(pid, int) or pid <= 0:
        out["basis"] = "NO_RECORDED_PID"
        out["note"] = "记录里没有可核验的 pid：无法判断进程是否在，不能当成活着或已停"
        return out
    if not recorded_host:
        out["basis"] = "NO_RECORDED_HOST"
        proc_state = _proc_start_epoch(pid, proc)
        out["pid_present"] = bool(proc_state[0])
        out["note"] = ("记录只有 pid 没有 host；pid 存在只说明某个进程占着这个号，"
                       "不能证明就是这份记录的那个进程")
        return out
    if str(recorded_host) != str(current_host):
        out["basis"] = "RECORDED_HOST_DIFFERS"
        out["note"] = ("记录里的进程在另一台主机上；本机无对应宿主证据，不猜它现在死活")
        return out
    if os.name != "posix" or not proc.is_dir():
        out["basis"] = "PROC_UNAVAILABLE"
        out["note"] = "本平台没有 /proc 启动时间证据，无法核验 pid 是否同一进程"
        return out

    exists, start_epoch = _proc_start_epoch(pid, proc)
    out["start_epoch_seen"] = start_epoch
    if exists is False:
        out["status"] = PROCESS_NOT_RUNNING
        out["basis"] = "PROC_MISSING"
        out["note"] = "记录的 pid 在当前主机上不存在：这个进程已经不在"
        return out
    if start_epoch is None or started is None:
        out["basis"] = "START_TIME_UNREADABLE"
        out["note"] = "读不到进程启动时间或记录启动时间，无法确认是不是同一个进程"
        return out
    delta = abs(float(start_epoch) - started.timestamp())
    out["start_seconds_delta"] = round(delta, 3)
    if delta > float(tolerance_seconds):
        out["status"] = PROCESS_NOT_RUNNING
        out["basis"] = "PID_REUSED"
        out["note"] = ("同一个 pid 现在属于另一个启动时间不同的进程；记录里的那个进程已经不在")
        return out
    out["status"] = PROCESS_MATCHED
    out["basis"] = "PROC_START_TIME_MATCH"
    out["cmdline_matched"] = _cmdline_matches(pid, list(needles or []), proc)
    out["note"] = "启动时间匹配，是本机同一个进程；这只证明进程在，不证明它在推进"
    return out


def _last_attempt_failure(state: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """The most recent definite attempt end, from whitelisted fields only."""

    best: Optional[dict[str, Any]] = None
    best_at: Optional[datetime] = None
    for attempt_id, row in dict(state.get("attempts") or {}).items():
        if not isinstance(row, Mapping):
            continue
        if str(row.get("status") or "") not in ATTEMPT_FAILURE_STATUSES:
            continue
        at = _aware(row.get("submitted_at") or row.get("started_at"))
        if best is not None and (at is None or (best_at is not None and at <= best_at)):
            continue
        best = {"attempt_id": attempt_id, "action_id": row.get("action_id")}
        best.update({key: row.get(key) for key in _ATTEMPT_FIELDS})
        best_at = at
    return best


def queue_facts(state_root: Path | str) -> dict[str, Any]:
    """AR-0's persisted banner: open submission intents and unresolved actions, read directly.

    Direct JSON read on purpose: ``ActionStore.load_state`` replays the WAL, and a health
    reader must not perform that write.  An absent file is *absent*, not an empty healthy
    queue.
    """

    path = Path(state_root) / ACTION_STATE_FILENAME
    ok, state, error = _read_json(path)
    if not ok and error == "NOT_FOUND":
        return {"source": ACTION_STATE_FILENAME, "verdict": QUEUE_ABSENT, "state_present": False,
                "open_submission_intents": None, "unresolved_actions": None,
                "recent_failure": None, "revision": None, "updated_at": None,
                "note": "没有动作账本文件：这份根上没有 AR-0 动作队列可读，不等于队列为空"}
    if not ok or not isinstance(state, Mapping):
        return {"source": ACTION_STATE_FILENAME, "verdict": QUEUE_UNKNOWN, "state_present": True,
                "detail": error or "NOT_A_MAPPING", "open_submission_intents": None,
                "unresolved_actions": None, "recent_failure": None, "revision": None,
                "updated_at": None, "note": "动作账本存在但读不懂，不能判断队列状态"}

    fences = dict(state.get("open_submission_intents") or {})
    unresolved = [action_id for action_id, row in dict(state.get("actions") or {}).items()
                  if isinstance(row, Mapping)
                  and str(row.get("status") or "") == ACTION_STATUS_UNKNOWN]
    recent_failure = _last_attempt_failure(state)
    pending = len(fences) + len(unresolved)
    if pending > 0:
        verdict = QUEUE_PENDING
        note = ("有待处理的持久事实（未确认的提交意图或待对账动作）；"
                "没有正式超时/失败合同，只报积压与最后进度，不判卡死")
    elif recent_failure is not None:
        verdict = QUEUE_LAST_FAILURE
        note = "队列没有积压；最近一次动作尝试有明确失败结局，按原状态显示，与安静零待处理分开"
    else:
        verdict = QUEUE_IDLE
        note = "没有未确认的提交意图，也没有待对账动作"
    return {
        "source": ACTION_STATE_FILENAME,
        "verdict": verdict,
        "state_present": True,
        "open_submission_intents": len(fences),
        "open_submission_intent_ids": sorted(fences)[:20],
        "unresolved_actions": len(unresolved),
        "unresolved_action_ids": sorted(unresolved)[:20],
        "recent_failure": recent_failure,
        "revision": state.get("revision"),
        "updated_at": state.get("updated_at"),
        "note": note,
    }


def _coverage_view(coverage: Mapping[str, Any], summary: Mapping[str, Any],
                   now: datetime) -> dict[str, Any]:
    """The coverage view from the ledger rows, through the owner module's own arithmetic.

    ``resident_summary.coverage`` is the same computation taken by the process that wrote the
    summary; a reader uses it when the ledger file is absent, so a two-year-old record and a
    fresh one are not read with two different formulas.
    """

    rows = [row for row in (coverage.get("processes") or []) if isinstance(row, Mapping)]
    if rows:
        try:
            import life_runtime_coverage as runtime_coverage

            return runtime_coverage.coverage(rows, now=now, current_process_id=None)
        except Exception:  # fall through to the summary's own view, or to nothing
            pass
    from_summary = summary.get("coverage")
    return dict(from_summary) if isinstance(from_summary, Mapping) else {}


def _recorded_view(summary: Mapping[str, Any], coverage: Mapping[str, Any],
                   now: datetime) -> dict[str, Any]:
    processes = [row for row in (coverage.get("processes") or []) if isinstance(row, Mapping)]
    last_heartbeat = processes[-1].get("last_heartbeat_at") if processes else None
    view = _coverage_view(coverage, summary, now)
    return {
        "present": True,
        "running_claim": summary.get("running"),
        "pid": summary.get("pid"),
        "started_at": summary.get("started_at"),
        "this_process_started_at": summary.get("this_process_started_at"),
        "stopped_at": summary.get("stopped_at"),
        "starts": summary.get("starts"),
        "ticks": summary.get("ticks"),
        "last_tick_at": summary.get("last_tick_at"),
        "offline_scripted": summary.get("offline_scripted"),
        "coverage": {
            "process_count": view.get("process_count"),
            "covered_seconds": view.get("covered_seconds"),
            "closed_process_seconds": view.get("closed_process_seconds"),
            "open_tail_seconds": view.get("open_tail_seconds"),
            "wall_span_seconds": view.get("wall_span_seconds"),
            "offline_gap_seconds": view.get("offline_gap_seconds"),
            "unknown_seconds": view.get("unknown_seconds"),
            "coverage_complete": view.get("coverage_complete"),
            "continuous": view.get("continuous"),
            "last_heartbeat_at": last_heartbeat or (
                (view.get("processes") or [{}])[-1].get("last_heartbeat_at")
                if view.get("processes") else None),
            "last_heartbeat_age_seconds": _age_seconds(
                _aware(last_heartbeat or (
                    (view.get("processes") or [{}])[-1].get("last_heartbeat_at")
                    if view.get("processes") else None)), now),
        },
        "claim_note": ("running 是写这份记录的进程当时的自述，不是现在的状态探测；"
                       "进程崩了它不会自己变"),
    }


def _verdict(recorded_present: bool, process_status: str, running_claim: Any,
             queue_verdict: str) -> str:
    if not recorded_present:
        return VERDICT_NO_RECORD
    if process_status == PROCESS_MATCHED:
        return VERDICT_ALIVE_PENDING if queue_verdict == QUEUE_PENDING else VERDICT_ALIVE_QUIET
    if process_status == PROCESS_NOT_RUNNING:
        # The record said "running" and the process is gone: that is a stale claim, not a live
        # instance and not a healthy quiet one.
        return VERDICT_RECORD_STALE if running_claim is True else VERDICT_STOPPED
    return VERDICT_UNKNOWN


_EXPLAIN = {
    VERDICT_ALIVE_QUIET: "进程在（启动时间核验匹配），队列没有积压：这是安静，不是停机",
    VERDICT_ALIVE_PENDING: "进程在，队列里有未处理的持久事实：在跑且有待处理，不据此判卡死",
    VERDICT_STOPPED: "记录没有声称在跑，进程也不在当前主机：这是已停",
    VERDICT_RECORD_STALE: "记录里还写着 running，但记录的那个进程已经不在：这是旧记录残留，不是当前在线",
    VERDICT_NO_RECORD: "这个根上没有运行记录：不编造在线，也不编造已停",
    VERDICT_UNKNOWN: "无法核验进程身份（缺身份、跨主机或本平台无 /proc）：如实未知，不当成活着或已停",
}


def health(state_root: Path | str, *, now: Optional[datetime] = None,
           hostname: Optional[str] = None, proc_root: Path | str = "/proc",
           needles: Optional[list[str]] = None) -> dict[str, Any]:
    """The whole read-only projection.  It reads files and ``/proc``; it writes nothing."""

    root = Path(state_root)
    moment = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    summary_ok, summary, summary_error = _read_json(root / SUMMARY_FILENAME)
    coverage_ok, coverage_doc, coverage_error = _read_json(root / COVERAGE_FILENAME)
    summary_map = summary if isinstance(summary, Mapping) else {}
    coverage_map = coverage_doc if isinstance(coverage_doc, Mapping) else {}

    recorded_present = bool(summary_ok and isinstance(summary, Mapping))
    recorded = (_recorded_view(summary_map, coverage_map, moment) if recorded_present
                else {"present": False, "reason": summary_error or "NOT_FOUND"})
    if not coverage_ok:
        recorded["coverage_source"] = {"present": False, "reason": coverage_error or "NOT_FOUND"}

    identity = _recorded_identity(summary_map, coverage_map)
    probe_needles = list(needles or [])
    for candidate in (root, root.parent, root.parent.parent):
        text = str(candidate)
        if text and text not in probe_needles:
            probe_needles.append(text)
    process = probe_process(identity, hostname=hostname, proc_root=proc_root,
                            needles=probe_needles)

    last_tick_at = summary_map.get("last_tick_at") if recorded_present else None
    last_tick = _aware(last_tick_at)
    queue = queue_facts(root)
    tick_age = _age_seconds(last_tick, moment)
    if not recorded_present:
        progress_verdict = "UNKNOWN"
    elif last_tick is not None:
        progress_verdict = "HAS_PROGRESS_FACT"
    else:
        progress_verdict = "NO_PROGRESS_FACT"
    progress = {
        "last_tick_at": last_tick_at,
        "since_last_tick_seconds": tick_age,
        "last_heartbeat_at": (recorded.get("coverage") or {}).get("last_heartbeat_at")
        if recorded_present else None,
        "since_last_heartbeat_seconds": (recorded.get("coverage") or {}).get(
            "last_heartbeat_age_seconds") if recorded_present else None,
        "verdict": progress_verdict,
        "no_timeout_rule": True,
        "note": ("这里只报最后真实进度与距今秒数；没有“多少秒没动就卡死”的规则，"
                 "长模型/owner 调用和正当 WAIT 不会被判死锁"),
    }

    verdict = _verdict(recorded_present, process["status"],
                       summary_map.get("running"), queue["verdict"])
    cannot_judge: list[str] = []
    if process["status"] == PROCESS_UNKNOWN:
        cannot_judge.append("进程是否在：%s" % (process.get("basis") or "UNKNOWN"))
    if queue["verdict"] in {QUEUE_ABSENT, QUEUE_UNKNOWN}:
        cannot_judge.append("队列是否有待处理事实：%s" % queue["verdict"])
    if progress_verdict == "NO_PROGRESS_FACT":
        cannot_judge.append("任务是否有进展：记录里没有 tick 时间")
    cannot_judge.append("是否卡死：没有正式超时/失败合同，不判断")

    return {
        "kind": KIND,
        "schema_version": SCHEMA_VERSION,
        "instance_state_root": str(root),
        "checked_at": _iso(moment),
        "hostname": hostname if hostname is not None else socket.gethostname(),
        "read_only": True,
        "verdict": verdict,
        "explain": _EXPLAIN[verdict],
        "recorded": recorded,
        "identity": identity,
        "process": process,
        "progress": progress,
        "queue": queue,
        "cannot_judge": cannot_judge,
        "boundaries": [
            "这是运行健康事实，不是她的个人经历；进程在/安静不证明她在生活",
            "不取 Activity lease、不恢复 WAL、不重试/消费/重发、不写实例文件",
            "只读本根状态与当前主机 /proc；跨主机或无 /proc 一律 UNKNOWN",
        ],
    }


__all__ = [
    "ACTION_STATE_FILENAME",
    "COVERAGE_FILENAME",
    "KIND",
    "PROCESS_MATCHED",
    "PROCESS_NOT_RUNNING",
    "PROCESS_UNKNOWN",
    "QUEUE_ABSENT",
    "QUEUE_IDLE",
    "QUEUE_LAST_FAILURE",
    "QUEUE_PENDING",
    "QUEUE_UNKNOWN",
    "SCHEMA_VERSION",
    "SUMMARY_FILENAME",
    "VERDICT_ALIVE_PENDING",
    "VERDICT_ALIVE_QUIET",
    "VERDICT_NO_RECORD",
    "VERDICT_RECORD_STALE",
    "VERDICT_STOPPED",
    "VERDICT_UNKNOWN",
    "health",
    "probe_process",
    "queue_facts",
    "state_root_for",
]

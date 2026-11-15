#!/usr/bin/env python3
"""手机主屏的「此刻」：真实状态的安全投影，外加一个本机受控运行窗口。

这个模块只做两件事，且都不新造生活真相：

* :func:`living_view` —— **纯读**。它把 ``phone.live_day.status`` 已经重建出来的
  where / hands / doing / stop_point / next，连同预算账本、Memory 绑定状态与监督进程自己
  的记录，收成一个手机页面能直接渲染的投影。它不启动服务、不建库、不绑 socket、不发模型
  请求，也不把历史 wake 说成「她现在正在做」。
* :class:`Supervisor` / :func:`run_bounded_window` —— **受控入口**。手机页面与
  ``scripts/usable_day.py`` 都只能通过它启动一段有限的窗口（≤600 秒、本功能累计 ≤4 次
  真实请求），暂停走既有的 ``life_pause``，停止只停本监督自己启动的子进程。预算账本是
  既有的 ``LifeRequestBudget``；本模块只在实例的 ``run/`` 下记一份**监督自己的**窗口账本，
  用来在进程重启后仍从持久账本推回已用次数，绝不刷新上限。

为什么 PAUSED 出口在这块里被收紧
--------------------------------
上一张单让 PAUSED 前台活动可以被 ABANDON。可「她有没有未决副作用 / 承诺绑定」这件事，
现有接口给不出可证事实：Activity 记录没有承诺字段，AR-0 的 Action 用 ``actv_<id>`` 而
canonical 活动用 ``actv:<id>``，两套词表不相等。读不到不能当没有，所以这里不删除引用、
不扩 id 词表、也不自动 ABANDON：AG-1/AG-2 会把这条出口扣住并给出具名原因
（``PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE``），页面显示真实阻塞。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from . import life as phone_life
from . import live_day
from . import service as phone_service

try:  # POSIX only: the whole life runtime already requires it.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

SCHEMA_LIVING = "phone.living.v1"
SCHEMA_WINDOW = "phone.living.window.v1"

#: Fallback ceiling when the persisted LifeRequestBudget has no configured day/window ceiling.
FUNCTION_REQUEST_CAP = 96
#: One explicit window is bounded by both the order and ``live_day``'s own ceiling.
MAX_WINDOW_SECONDS = 3600
MAX_WINDOW_REQUESTS = 12
LOW_BUDGET_MAX_WINDOW_SECONDS = 300
LOW_BUDGET_MAX_WINDOW_REQUESTS = 2
DEFAULT_WINDOW_SECONDS = 120
GOVERNANCE_DORMANT_PAUSE_REASON = "governance:dormant_no_cognition"

WINDOW_FILENAME = "usable-window.json"
SUPERVISOR_FILENAME = "usable-supervisor.json"
WINDOW_LOG = "usable-window.log"
WINDOW_ATTEMPTS = "usable-window.attempts.jsonl"
MANAGED_DAY_FILENAME = "nyairo-day.instance.json"
SERVE_MARKER_FILENAME = "phone.serve.json"

STATE_OK = "READY"
STATE_UNKNOWN = "UNKNOWN"
STATE_UNAVAILABLE = "UNAVAILABLE"


# ---------------------------------------------------------------------------------------------
# tiny io helpers
# ---------------------------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def run_dir(root: Path | str) -> Path:
    return phone_life.phone_root(root) / "run"


def _state_root(root: Path | str) -> Optional[Path]:
    try:
        nested = phone_life.read_nested_life(phone_life.phone_root(root))
        if nested is None:
            return None
        return phone_life.life_state_root(root, nested)
    except Exception:
        return None


def _recorded_switches(root: Path | str) -> dict[str, bool]:
    """Read run switches persisted by ``nyairo_day`` or the last ``phone.demo`` serve."""

    rdir = run_dir(root)
    managed = _read_json(rdir / MANAGED_DAY_FILENAME)
    if managed:
        return {
            "room_living": bool(managed.get("room_living")),
            "ambient_events": bool(managed.get("ambient_events")),
            "world_actions": bool(managed.get("world_actions")),
        }
    marker = _read_json(rdir / SERVE_MARKER_FILENAME)
    return {
        "room_living": bool(marker.get("room_living")),
        "ambient_events": bool(marker.get("ambient_events")),
        "world_actions": str(marker.get("world_intent_gate") or "").strip().lower() == "canonical",
    }


def _switch_overrides(switches: Mapping[str, bool]) -> Optional[dict[str, str]]:
    from . import demo

    overrides = dict(demo._serve_feature_overrides(
        room_living=bool(switches.get("room_living")),
        ambient_events=bool(switches.get("ambient_events")),
    ) or {})
    if switches.get("world_actions"):
        overrides["WORLD_BODY_INTENT_MUTATION_GATE"] = "canonical"
    return overrides or None


def _bound_environment(root: Path | str, environment: Optional[Mapping[str, str]]
                       ) -> tuple[dict[str, str], Optional[Path]]:
    root_path = phone_life.phone_root(root)
    try:
        phone = phone_life.load_phone_instance(root_path)
        nested = phone_life.read_nested_life(root_path)
    except Exception:
        return dict(environment or os.environ), None
    if nested is None:
        return dict(environment or os.environ), None
    switches = _recorded_switches(root_path)
    try:
        bound = phone_life.map_life_environment(
            phone, nested,
            base_env=dict(os.environ if environment is None else environment),
            feature_overrides=_switch_overrides(switches))
    except Exception:
        return dict(environment or os.environ), None
    return bound, phone_life.life_state_root(root_path, nested)


# ---------------------------------------------------------------------------------------------
# the existing request ledger (never a second budget)
# ---------------------------------------------------------------------------------------------


def ledger_view(environment: Mapping[str, str], state_root: Optional[Path]) -> dict[str, Any]:
    """The persisted ledger as the resident would read it.  Never opens a window."""

    if state_root is None:
        return {"state": STATE_UNAVAILABLE, "reason": "NESTED_LIFE_INSTANCE_MISSING"}
    try:
        from life_request_budget import LifeRequestBudget

        budget = LifeRequestBudget(
            state_root,
            utc_day_max=_int_or_none(environment.get(
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY")),
            run_window_max=_int_or_none(environment.get(
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW")),
            run_id=environment.get("CHIYO_AUTONOMOUS_RUN_ID"))
        view = budget.view()
    except Exception as exc:
        return {"state": STATE_UNAVAILABLE, "reason": "LEDGER_UNREADABLE",
                "error_type": type(exc).__name__}
    out = {key: view.get(key) for key in
           ("state", "utc_day", "utc_day_calls", "utc_day_max", "run_id",
            "run_window_calls", "run_window_max", "windows")}
    if out.get("state") not in ("ok", "absent"):
        out["state"] = STATE_UNAVAILABLE
        out.setdefault("reason", "LEDGER_NOT_READY")
    return out


# ---------------------------------------------------------------------------------------------
# window audit log + capacity derived directly from the persisted LifeRequestBudget
# ---------------------------------------------------------------------------------------------


def _window_path(root: Path | str) -> Path:
    return run_dir(root) / WINDOW_FILENAME


def _window_mirror(root: Path | str) -> Path:
    return phone_life.nested_life_dir(phone_life.phone_root(root)) / "run" / WINDOW_FILENAME


def _read_window_doc(root: Path | str, state_root: Optional[Path] = None) -> dict[str, Any]:
    for path in (_window_path(root), _window_mirror(root)):
        if path.exists():
            doc = _read_json(path)
            if doc:
                return doc
    return {}


def _write_window_doc(root: Path | str, state_root: Optional[Path],
                      payload: Mapping[str, Any]) -> None:
    _atomic_write(_window_path(root), payload)
    try:
        _atomic_write(_window_mirror(root), payload)
    except OSError:
        pass


def _append_attempt(root: Path | str, row: Mapping[str, Any]) -> None:
    path = run_dir(root) / WINDOW_ATTEMPTS
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def _ledger_capacity(ledger: Mapping[str, Any],
                     environment: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """Compute used / remaining / ceiling from the authoritative LifeRequestBudget view."""

    state = str(ledger.get("state") or "")
    if state not in ("ok", "absent"):
        reason = str(ledger.get("reason") or "LEDGER_UNREADABLE")
        return {
            "cap": FUNCTION_REQUEST_CAP,
            "used": 0,
            "remaining": 0,
            "reason": reason,
            "readable": False,
        }

    day_calls = max(0, _int_or_none(ledger.get("utc_day_calls")) or 0)
    win_calls = max(0, _int_or_none(ledger.get("run_window_calls")) or 0)
    day_max = _int_or_none(ledger.get("utc_day_max"))
    win_max = _int_or_none(ledger.get("run_window_max"))
    override_cap = _int_or_none((environment or {}).get("CHIYO_PHONE_LIVING_REQUEST_CAP"))

    candidates: list[tuple[int, int, int]] = []  # (remaining, cap, used)
    if day_max is not None and day_max >= 0:
        candidates.append((max(0, day_max - day_calls), day_max, day_calls))
    if win_max is not None and win_max >= 0:
        candidates.append((max(0, win_max - win_calls), win_max, win_calls))
    if override_cap is not None and override_cap >= 0:
        eff_used = max(day_calls, win_calls)
        candidates.append((max(0, override_cap - eff_used), override_cap, eff_used))

    if not candidates:
        eff_used = max(day_calls, win_calls)
        candidates.append((max(0, FUNCTION_REQUEST_CAP - eff_used),
                           FUNCTION_REQUEST_CAP, eff_used))

    remaining, cap, used = min(candidates, key=lambda item: (item[0], item[1]))
    reason = "FUNCTION_WINDOW_CAP_EXHAUSTED" if remaining <= 0 else None
    return {
        "cap": int(cap),
        "used": int(used),
        "remaining": int(remaining),
        "reason": reason,
        "readable": True,
    }


def _governance_window_policy(root: Path | str, state_root: Optional[Path] = None) -> dict[str, Any]:
    """Read v0.2 governance mode (DORMANT_NO_COGNITION / LOW_BUDGET_CONSERVATION) with zero writes."""
    raw_root = Path(root)
    p_root = phone_life.phone_root(raw_root)
    candidates: list[Path] = [
        raw_root / "phone" / "v02_runtime_migration.json",
        raw_root / "v02_runtime_migration.json",
        p_root / "v02_runtime_migration.json",
        p_root / "phone" / "v02_runtime_migration.json",
    ]
    if p_root.name in ("phone", "phone_instance", "one-day"):
        candidates.append(p_root.parent / "phone" / "v02_runtime_migration.json")
        candidates.append(p_root.parent / "v02_runtime_migration.json")
    if state_root is not None:
        sr = Path(state_root)
        candidates.append(sr / "v02_runtime_migration.json")
        if sr.name in ("state", "life-state", "life_state"):
            candidates.append(sr.parent / "phone" / "v02_runtime_migration.json")
            if sr.parent.name == "life":
                candidates.append(sr.parent.parent / "phone" / "v02_runtime_migration.json")

    mode_token = "NORMAL"
    budget_scale_pct = 100
    for cand in candidates:
        if cand.is_file():
            doc = _read_json(cand)
            mc = doc.get("mode_config") if isinstance(doc.get("mode_config"), Mapping) else {}
            raw_mode = str(mc.get("mode") or doc.get("mode") or "NORMAL").strip().upper()
            if raw_mode:
                mode_token = raw_mode
            scale_val = _int_or_none(mc.get("budget_scale_pct"))
            if scale_val is not None:
                budget_scale_pct = max(0, min(100, scale_val))
            break

    if mode_token in ("DORMANT", "DORMANT_NO_COGNITION"):
        return {
            "governance_mode": "DORMANT_NO_COGNITION",
            "governance_paused": True,
            "window_tightened": True,
            "budget_scale_pct": 0,
            "effective_max_window_seconds": 0,
            "effective_max_window_requests": 0,
            "reason": "DORMANT_NO_COGNITION_PAUSED",
        }
    if mode_token in ("LOW_BUDGET", "CONSERVATION", "LOW_BUDGET_CONSERVATION"):
        return {
            "governance_mode": "LOW_BUDGET_CONSERVATION",
            "governance_paused": False,
            "window_tightened": True,
            "budget_scale_pct": budget_scale_pct,
            "effective_max_window_seconds": LOW_BUDGET_MAX_WINDOW_SECONDS,
            "effective_max_window_requests": LOW_BUDGET_MAX_WINDOW_REQUESTS,
            "reason": None,
        }
    return {
        "governance_mode": "NORMAL_AUTONOMY",
        "governance_paused": False,
        "window_tightened": False,
        "budget_scale_pct": budget_scale_pct,
        "effective_max_window_seconds": MAX_WINDOW_SECONDS,
        "effective_max_window_requests": MAX_WINDOW_REQUESTS,
        "reason": None,
    }


def sync_governance_mode(root: Path | str, mode: str, *, budget_scale_pct: int = 100,
                         reason: str = "") -> dict[str, Any]:
    """Synchronize v0.2 governance mode into living pause/tightened window controls."""
    upper_mode = str(mode or "NORMAL").strip().upper()
    _, state_root = _bound_environment(root, None)
    if state_root is None:
        # Also detect direct <day_root>/life/state or <day_root>/life-state when phone instance is unconfigured
        raw_root = Path(root)
        for cand_sr in (raw_root / "life" / "state", raw_root / "life-state"):
            if cand_sr.is_dir():
                state_root = cand_sr
                break

    pause_synced = False
    if state_root is not None and state_root.is_dir():
        try:
            import life_pause

            current_pause = dict(life_pause.read(state_root))
            if upper_mode in ("DORMANT", "DORMANT_NO_COGNITION"):
                life_pause.set_paused(state_root, True, reason=GOVERNANCE_DORMANT_PAUSE_REASON)
                pause_synced = True
            elif bool(current_pause.get("paused")) and str(
                current_pause.get("reason") or ""
            ).startswith(GOVERNANCE_DORMANT_PAUSE_REASON):
                life_pause.set_paused(state_root, False, reason="governance:resumed_from_dormant")
                pause_synced = True
        except Exception:
            pause_synced = False

    policy = _governance_window_policy(root, state_root)
    return {
        "synced": True,
        "pause_synced": pause_synced,
        "governance_mode": policy["governance_mode"],
        "governance_paused": policy["governance_paused"],
        "window_tightened": policy["window_tightened"],
        "effective_max_window_seconds": policy["effective_max_window_seconds"],
        "effective_max_window_requests": policy["effective_max_window_requests"],
        "reason_note": str(reason or "")[:120],
    }


def window_status(root: Path | str, *, environment: Optional[Mapping[str, str]] = None
                  ) -> dict[str, Any]:
    env, state_root = _bound_environment(root, environment)
    ledger = ledger_view(env, state_root)
    doc = _read_window_doc(root, state_root)
    cap_info = _ledger_capacity(ledger, env)
    gov = _governance_window_policy(root, state_root)
    eff_max_sec = int(gov["effective_max_window_seconds"])
    eff_max_req = int(gov["effective_max_window_requests"])
    remaining = 0 if gov["governance_paused"] else cap_info["remaining"]
    reason = gov["reason"] or cap_info["reason"]
    return {
        "schema": SCHEMA_WINDOW,
        "function_cap": cap_info["cap"],
        "max_window_seconds": eff_max_sec if gov["window_tightened"] else MAX_WINDOW_SECONDS,
        "max_window_requests": eff_max_req if gov["window_tightened"] else MAX_WINDOW_REQUESTS,
        "ceiling_window_seconds": MAX_WINDOW_SECONDS,
        "ceiling_window_requests": MAX_WINDOW_REQUESTS,
        "effective_max_window_seconds": eff_max_sec,
        "effective_max_window_requests": eff_max_req,
        "governance_mode": gov["governance_mode"],
        "governance_paused": gov["governance_paused"],
        "window_tightened": gov["window_tightened"],
        "used": cap_info["used"],
        "remaining": remaining,
        "stored_high": _int_or_none(doc.get("used_high")) or 0,
        "calculated_from_ledger": cap_info["used"] if cap_info["readable"] else None,
        "baseline": _int_or_none(doc.get("ledger_baseline_run_window_calls")),
        "ledger": ledger,
        "state_root": str(state_root) if state_root else None,
        "windows": list(doc.get("windows") or [])[-8:],
        "last_window": doc.get("last_window"),
        "baseline_lost": False,
        "reason": reason,
        "note": ("配额直接读取实例持久请求账本（当日与运行窗口上限），"
                 "重启不会清零，跨日按账本自然轮转；账本损坏读不到时拒绝启动"),
    }


class _WindowBusy(RuntimeError):
    pass


class _window_lock:
    """POSIX flock over the supervisor's own window ledger.

    A crash releases it with the process; a second window can never run concurrently.
    """

    def __init__(self, root: Path | str) -> None:
        self.path = run_dir(root) / "usable-window.lock"
        self.handle = None

    def __enter__(self) -> "_window_lock":
        if fcntl is None:
            raise _WindowBusy("WINDOW_LOCK_UNSUPPORTED_ON_PLATFORM")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise _WindowBusy("WINDOW_ALREADY_RUNNING") from None
        self.handle = handle
        return self

    def __exit__(self, *_exc: Any) -> bool:
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        return False


def run_bounded_window(
    root: Path | str,
    *,
    duration_seconds: Any = DEFAULT_WINDOW_SECONDS,
    max_new_requests: Any = 1,
    environment: Optional[Mapping[str, str]] = None,
    run_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Run one explicit, bounded window under the shared feature cap.

    Refusals happen **before** anything is started: an unreadable ledger, a spent cap, or a
    second concurrent window all answer ``ok: false`` with a named reason and no model request.
    """

    duration = _int_or_none(duration_seconds)
    requested = _int_or_none(max_new_requests)
    gov_early = _governance_window_policy(root, None)
    if gov_early["governance_paused"]:
        return _refused("DORMANT_NO_COGNITION_PAUSED",
                        "governance mode is DORMANT_NO_COGNITION; cognition windows are paused")
    eff_sec_limit = int(gov_early["effective_max_window_seconds"])
    eff_req_limit = int(gov_early["effective_max_window_requests"])
    if duration is None or duration <= 0 or duration > eff_sec_limit:
        return _refused("DURATION_OUT_OF_RANGE", "duration_seconds must be 1..%d"
                        % eff_sec_limit)
    if requested is None or requested <= 0 or requested > eff_req_limit:
        return _refused("REQUEST_CAP_OUT_OF_RANGE", "max_new_requests must be 1..%d"
                        % eff_req_limit)

    env, state_root = _bound_environment(root, environment)
    if state_root is None:
        return _refused("NESTED_LIFE_INSTANCE_MISSING",
                        "this phone root has no nested Life instance")
    gov_bound = _governance_window_policy(root, state_root)
    if gov_bound["governance_paused"]:
        return _refused("DORMANT_NO_COGNITION_PAUSED",
                        "governance mode is DORMANT_NO_COGNITION; cognition windows are paused")
    if duration > int(gov_bound["effective_max_window_seconds"]):
        return _refused("DURATION_OUT_OF_RANGE", "duration_seconds must be 1..%d"
                        % int(gov_bound["effective_max_window_seconds"]))
    if requested > int(gov_bound["effective_max_window_requests"]):
        return _refused("REQUEST_CAP_OUT_OF_RANGE", "max_new_requests must be 1..%d"
                        % int(gov_bound["effective_max_window_requests"]))

    try:
        with _window_lock(root):
            before = ledger_view(env, state_root)
            cap_before = _ledger_capacity(before, env)
            if not cap_before["readable"]:
                return _refused("LEDGER_UNREADABLE",
                                "the persisted request ledger cannot be read; no window was opened")
            lease = phone_life.check_single_lease(state_root)
            if str(lease.get("status")) != "FREE":
                # Another writer (or an unverifiable lock) is out of this feature's hands:
                # report the conflict instead of stealing the lease.
                return _refused("LEASE_NOT_FREE",
                                "the single-writer lease is %s; no window was opened"
                                % lease.get("status"))
            remaining = int(cap_before["remaining"])
            if remaining <= 0:
                return _refused("FUNCTION_WINDOW_CAP_EXHAUSTED",
                                "all %d requests of the current budget ceiling are already spent"
                                % int(cap_before["cap"]))
            allowed = min(int(requested), remaining)
            doc = _read_window_doc(root, state_root)
            if not doc:
                doc = {
                    "schema": SCHEMA_WINDOW,
                    "created_at": _now_iso(),
                    "ledger_baseline_run_window_calls": _int_or_none(
                        before.get("run_window_calls")) or 0,
                    "ledger_baseline_run_id": before.get("run_id"),
                    "ledger_baseline_utc_day_calls": _int_or_none(before.get("utc_day_calls")) or 0,
                    "used_high": int(cap_before["used"]),
                    "windows": [],
                }
                _write_window_doc(root, state_root, doc)
            started_at = _now_iso()
            _append_attempt(root, {"at": started_at, "duration_seconds": int(duration),
                                   "allowed_requests": int(allowed), "pid": os.getpid()})
            switches = _recorded_switches(root)
            if run_fn is None:
                segment = live_day.run_segment(
                    root, duration_seconds=int(duration), poll_seconds=2,
                    max_new_requests=int(allowed), act=True,
                    start_services=live_day.SERVICE_AUTO,
                    room_living=switches["room_living"],
                    ambient_events=switches["ambient_events"],
                    environment=env)
            else:
                segment = run_fn(root, duration_seconds=int(duration), poll_seconds=2,
                                 max_new_requests=int(allowed), act=True,
                                 start_services=live_day.SERVICE_AUTO, environment=env)
            after = ledger_view(env, state_root)
            before_day = _int_or_none(before.get("utc_day_calls")) or 0
            after_day = _int_or_none(after.get("utc_day_calls"))
            before_win = _int_or_none(before.get("run_window_calls")) or 0
            after_win = _int_or_none(after.get("run_window_calls"))
            spent = None
            if after_day is not None and after_win is not None:
                spent = max(0, int(after_day) - int(before_day),
                            int(after_win) - int(before_win))
            elif after_day is not None:
                spent = max(0, int(after_day) - int(before_day))
            elif after_win is not None:
                spent = max(0, int(after_win) - int(before_win))
            cap_after = _ledger_capacity(after, env)
            row = {
                "started_at": started_at,
                "ended_at": _now_iso(),
                "requested_seconds": int(duration),
                "allowed_requests": int(allowed),
                "spent_requests": spent,
                "status": segment.get("status"),
                "reason": segment.get("reason"),
                "exit_code": segment.get("exit_code"),
                "segment_status": segment.get("status"),
            }
            windows = list(doc.get("windows") or [])[-15:]
            windows.append(row)
            doc.update({
                "used_high": max(int(doc.get("used_high") or 0), int(cap_after["used"])),
                "windows": windows,
                "last_window": row,
                "updated_at": _now_iso(),
            })
            _write_window_doc(root, state_root, doc)
    except _WindowBusy as exc:
        return _refused(str(exc), "another explicit window is already running")
    except Exception as exc:  # a real failure is reported, never hidden
        return _refused("WINDOW_FAILED", type(exc).__name__)

    report = dict(segment)
    report.update({
        "schema": SCHEMA_WINDOW,
        "window": row,
        "budget": window_status(root, environment=environment),
        "model_called": bool(segment.get("model_called")),
        "instance_reset": False,
        "new_run_requested": False,
    })
    return report


def _refused(reason: str, detail: str = "") -> dict[str, Any]:
    return {"schema": SCHEMA_WINDOW, "ok": False, "status": "BLOCKED", "exit_code": 1,
            "reason": reason, "detail": detail, "model_called": False,
            "instance_reset": False, "new_run_requested": False}


# ---------------------------------------------------------------------------------------------
# operator pause / stop (both reuse the owners that already own those facts)
# ---------------------------------------------------------------------------------------------


def set_paused(root: Path | str, paused: bool, *, reason: str = "operator:phone"
               ) -> dict[str, Any]:
    """The Life's own persistent pause bit (``life_pause``), never the World's control."""

    _, state_root = _bound_environment(root, None)
    if state_root is None:
        return {"ok": False, "status": "BLOCKED", "reason": "NESTED_LIFE_INSTANCE_MISSING"}
    try:
        import life_pause

        result = life_pause.set_paused(state_root, bool(paused), reason=str(reason)[:120])
    except Exception as exc:
        return {"ok": False, "status": "BLOCKED", "reason": "PAUSE_WRITE_FAILED",
                "detail": type(exc).__name__}
    return {"ok": True, "status": "ok", "paused": bool(paused),
            "read": _safe_pause_read(state_root), "owner": "life_pause", "result": result}


def _safe_pause_read(state_root: Path | str) -> dict[str, Any]:
    try:
        import life_pause

        return dict(life_pause.read(state_root))
    except Exception as exc:
        return {"state": STATE_UNKNOWN, "paused": None, "detail": type(exc).__name__}


def pause_state(root: Path | str) -> dict[str, Any]:
    _, state_root = _bound_environment(root, None)
    if state_root is None:
        return {"state": STATE_UNKNOWN, "paused": None,
                "detail": "NESTED_LIFE_INSTANCE_MISSING"}
    return _safe_pause_read(state_root)


# ---------------------------------------------------------------------------------------------
# the supervisor process (one phone surface, one explicit window at a time)
# ---------------------------------------------------------------------------------------------


def supervisor_path(root: Path | str) -> Path:
    return run_dir(root) / SUPERVISOR_FILENAME


class Supervisor:
    """The phone-usable supervisor's own controls.

    It never starts a model by itself: ``serve`` only brings up the phone surface (the same
    ``demo.serve_instance`` the operator already uses, which starts Supply/World and binds
    loopback HTTP).  A model window exists only after an explicit ``start_window`` call, and it
    runs in a child process so this supervisor can keep serving the read-only phone view.
    """

    def __init__(self, root: Path | str, *, environment: Optional[Mapping[str, str]] = None,
                 port: Optional[int] = None) -> None:
        self.root = phone_life.phone_root(root)
        self.environment = dict(environment or os.environ)
        self.port = port
        self.pid = os.getpid()
        self._lock = threading.Lock()
        self._child: Optional[subprocess.Popen] = None
        self._child_started_at: Optional[str] = None
        self._record: dict[str, Any] = {}
        self._write_record()

    # -- persisted, minimal record the page can read even without this object ---------------

    def _write_record(self) -> None:
        payload = {
            "schema": "phone.living.supervisor.v1",
            "pid": self.pid,
            "port": self.port,
            "root": str(self.root),
            "started_at": self._record.get("started_at") or _now_iso(),
            "updated_at": _now_iso(),
            "child": self._child_snapshot(),
        }
        try:
            _atomic_write(supervisor_path(self.root), payload)
        except OSError:
            pass
        self._record = payload

    def _child_snapshot(self) -> Optional[dict[str, Any]]:
        child = self._child
        if child is None:
            return None
        alive = child.poll() is None
        return {"pid": child.pid, "alive": alive, "exit_code": child.poll(),
                "started_at": self._child_started_at}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": "phone.living.supervisor.v1",
                "running": True,
                "pid": self.pid,
                "port": self.port,
                "child": self._child_snapshot(),
                "windows": window_status(self.root, environment=self.environment),
                "paused": pause_state(self.root),
                "source": "supervisor_process",
            }

    # -- explicit bounded window -----------------------------------------------------------

    def start_window(self, *, duration_seconds: Any = DEFAULT_WINDOW_SECONDS,
                     max_new_requests: Any = 1) -> dict[str, Any]:
        duration = _int_or_none(duration_seconds)
        requested = _int_or_none(max_new_requests)
        budget = window_status(self.root, environment=self.environment)
        if budget.get("governance_paused"):
            return _refused("DORMANT_NO_COGNITION_PAUSED",
                            "governance mode is DORMANT_NO_COGNITION; cognition windows are paused")
        eff_sec_limit = int(budget.get("effective_max_window_seconds") or MAX_WINDOW_SECONDS)
        eff_req_limit = int(budget.get("effective_max_window_requests") or MAX_WINDOW_REQUESTS)
        if duration is None or duration <= 0 or duration > eff_sec_limit:
            return _refused("DURATION_OUT_OF_RANGE", "duration_seconds must be 1..%d"
                            % eff_sec_limit)
        if requested is None or requested <= 0 or requested > eff_req_limit:
            return _refused("REQUEST_CAP_OUT_OF_RANGE", "max_new_requests must be 1..%d"
                            % eff_req_limit)
        ledger = dict(budget.get("ledger") or {})
        if str(ledger.get("state") or "") not in ("ok", "absent"):
            return _refused("LEDGER_UNREADABLE",
                            "the persisted request ledger cannot be read; no window was opened")
        if budget.get("remaining", 0) <= 0:
            return _refused("FUNCTION_WINDOW_CAP_EXHAUSTED",
                            "no request budget remains for this feature")
        script = Path(__file__).resolve().parents[2] / "scripts" / "usable_day.py"
        if not script.exists():
            return _refused("SUPERVISOR_ENTRY_MISSING", "scripts/usable_day.py is not installed")
        with self._lock:
            if self._child is not None and self._child.poll() is None:
                return _refused("WINDOW_ALREADY_RUNNING", "one explicit window at a time")
            log_path = run_dir(self.root) / WINDOW_LOG
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log = log_path.open("a", encoding="utf-8")
            try:
                child = subprocess.Popen(
                    [sys.executable, "-B", str(script), "window", "--root", str(self.root),
                     "--duration-seconds", str(duration),
                     "--max-new-requests", str(min(int(requested), int(budget["remaining"])))],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    cwd=str(script.parents[1]), close_fds=True)
            finally:
                log.close()
            self._child = child
            self._child_started_at = _now_iso()
            self._write_record()
            # The HTTP request must not block for the whole segment: a watcher updates the
            # record when the child ends, and GET /living reports the child's liveness.
            watcher = threading.Thread(target=self._watch_child, name="usable-window-watch",
                                       daemon=True)
            watcher.start()
            return {"schema": SCHEMA_WINDOW, "ok": True, "status": "STARTED",
                    "pid": child.pid,
                    "window": window_status(self.root, environment=self.environment),
                    "model_called": None,
                    "note": "一个明确的有界窗口已在子进程启动；本次 HTTP 调用不等待它结束"}

    def _watch_child(self) -> None:
        child = self._child
        if child is None:
            return
        try:
            child.wait()
        finally:
            with self._lock:
                self._write_record()

    def stop_window(self) -> dict[str, Any]:
        """Stop **only** the child this supervisor started."""

        with self._lock:
            child = self._child
            if child is None or child.poll() is not None:
                return {"ok": False, "status": "IDLE", "reason": "NO_SUPERVISED_WINDOW",
                        "detail": ("this supervisor started no live window; it will not signal "
                                   "a resident it did not start")}
            if os.name != "posix":
                return {"ok": False, "status": "BLOCKED",
                        "reason": "SIGNAL_STOP_UNSUPPORTED_ON_PLATFORM"}
            try:
                child.send_signal(signal.SIGTERM)
            except OSError as exc:
                return {"ok": False, "status": "BLOCKED", "reason": "SIGNAL_FAILED",
                        "detail": type(exc).__name__}
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline and child.poll() is None:
                time.sleep(0.2)
            if child.poll() is None:
                child.terminate()
            self._write_record()
            return {"ok": True, "status": "STOPPED", "pid": child.pid,
                    "exit_code": child.poll(), "supervised_only": True}

    def set_paused(self, paused: bool) -> dict[str, Any]:
        result = set_paused(self.root, bool(paused), reason="operator:phone-usable")
        self._write_record()
        return result


# ---------------------------------------------------------------------------------------------
# the read-only projection the phone renders
# ---------------------------------------------------------------------------------------------


def _stopped_reason(report: Mapping[str, Any]) -> tuple[bool, str]:
    """Is a resident really running right now?  A stale record is never "running"."""

    if report.get("running_recorded") is not True:
        return False, "NOT_RUNNING"
    alive = report.get("pid_alive")
    if alive is True:
        return True, "RUNNING"
    if alive is False:
        return False, "STALE_RECORD"
    return False, "PID_UNKNOWN"


def _phase(summary: Mapping[str, Any], report: Mapping[str, Any],
           pause: Mapping[str, Any], window: Mapping[str, Any],
           foreground: Optional[Mapping[str, Any]] = None,
           governance: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    running, run_reason = _stopped_reason(report)
    doing = dict(summary.get("doing") or {})
    pause_block = dict(doing.get("pause") or {})
    paused = pause_block.get("paused")
    if paused is None:
        paused = pause.get("paused")
    status_word = str(doing.get("status_word") or "")
    child = dict(window.get("child") or {})
    child_alive = bool(child.get("alive"))
    gov = dict(governance or {})
    if gov.get("governance_paused"):
        return {"key": "paused", "basis": "DORMANT_NO_COGNITION"}
    if paused is True:
        return {"key": "paused", "basis": "OPERATOR_PAUSE"}
    if running and status_word == "PAUSED":
        return {"key": "paused", "basis": "ACTIVITY_PAUSED"}
    if running and status_word in {"BLOCKED", "NO_STEP_AVAILABLE", "DISABLED"}:
        return {"key": "blocked", "basis": status_word}
    if running and status_word in {"ACTED", "IN_TRANSIT"}:
        return {"key": "active", "basis": status_word}
    if running and status_word in {"NO_ACTION", "DEFER", "ABANDONED"}:
        return {"key": "idle", "basis": status_word}
    if running:
        return {"key": "quiet", "basis": status_word or "NO_CONSIDERED_WAKE"}
    #: Not running is not the same as "no activity": the canonical authority can still hold a
    #: PAUSED foreground pointer.  The process being gone never erases that fact.
    fg = dict(foreground or {})
    if str(fg.get("status")) == "READ" and fg.get("activity_status") == "PAUSED":
        return {"key": "paused", "basis": "ACTIVITY_PAUSED_NOT_RUNNING"}
    if run_reason == "STALE_RECORD":
        return {"key": "unknown", "basis": "STALE_RECORD"}
    if child_alive:
        return {"key": "quiet", "basis": "WINDOW_CHILD_RUNNING_NO_RESIDENT_RECORD"}
    return {"key": "stopped", "basis": run_reason}


def _sentence(phase: Mapping[str, Any], summary: Mapping[str, Any],
              report: Mapping[str, Any],
              foreground: Optional[Mapping[str, Any]] = None) -> str:
    """One factual line.  Nothing is invented for a missing owner fact."""

    fg = dict(foreground or {})
    fg_title = str(fg.get("title") or "").strip()
    fg_at = str(fg.get("paused_at") or fg.get("updated_at") or "").strip()
    where = dict(summary.get("where") or {})
    place = str(where.get("place_name") or where.get("place_id") or "").strip()
    place_text = place if place else None
    stop = dict(summary.get("stop_point") or {})
    work = str(stop.get("title") or "").strip()
    doing = dict(summary.get("doing") or {})
    ref = str(doing.get("activity_ref") or "").strip()
    key = str(phase.get("key"))
    if key == "active":
        if place_text:
            return "她正在%s，有一件活动还在进行。" % place_text
        return "她有一件活动正在进行，但读不到她现在的位置。"
    if key == "paused":
        if phase.get("basis") == "DORMANT_NO_COGNITION":
            return "当前处于休眠托管档（DORMANT_NO_COGNITION）：后台认知与运行窗口已暂停，不补造经历。"
        if phase.get("basis") == "OPERATOR_PAUSE":
            return "运行已被明确暂停：不会开始新的决定，也不会发出新的请求。"
        if phase.get("basis") == "ACTIVITY_PAUSED_NOT_RUNNING":
            if fg_title:
                tail = ("，暂停于 %s" % fg_at) if fg_at else ""
                return "她把这件活动停在这里：%s（PAUSED%s）；当前没有在运行。" % (
                    fg_title, tail)
            return "她有一件活动停在 PAUSED；当前没有在运行，活动标题没有读到。"
        return "她把活动停在这里（活动状态 PAUSED），没有继续执行。"
    if key == "blocked":
        detail = str(doing.get("outcome") or doing.get("decision") or "").strip()
        if detail:
            return "她刚被挡住，活动没有推进（%s）。" % detail
        return "她刚被挡住，活动没有推进。"
    if key == "idle":
        last = place_text or "上次已知的位置"
        return "她目前没有新的动作；上一次在%s。" % last
    if key == "quiet":
        next_at = (summary.get("next") or {}).get("check_window_at")
        if next_at:
            return "她在安静里；下一次自然窗口是 %s。" % next_at
        return "她在安静里，还没有可用的下一次自然窗口。"
    if key == "stopped":
        if str(fg.get("status")) == "READ" and fg.get("activity_status") in {"ACTIVE", "WAITING"}:
            title = fg_title or "未命名活动"
            return ("现在没有在运行；活动「%s」是 %s，但没有正在执行的进程。"
                    % (title, fg.get("activity_status")))
        if str(fg.get("status")) == "UNVERIFIED":
            return ("现在没有在运行；活动权威读不到（%s），不能据此说没有活动。"
                    % (fg.get("reason_code") or "UNKNOWN"))
        if work:
            return "现在没有在运行。上次停在《%s》。" % work
        activity = fg_title or str(stop.get("foreground_activity_ref") or ref or "").strip()
        if activity:
            return "现在没有在运行。上次停在这件活动：%s。" % activity
        if place_text:
            return "现在没有在运行。上次在%s。" % place_text
        return "现在没有在运行，也没有读到上次的停点。"
    reason = str(report.get("reason") or "UNKNOWN")
    return "读不到她的当前状态（%s）。" % reason


def living_view(
    root: Path | str,
    *,
    environment: Optional[Mapping[str, str]] = None,
    service: Optional[phone_service.PhoneService] = None,
    supervisor: Optional[Supervisor] = None,
    limit: int = live_day.DEFAULT_SUMMARY_LIMIT,
    live: bool = False,
) -> dict[str, Any]:
    """The phone's read-only "now" projection.  Never starts, writes or calls a model.

    ``live=True`` asks the *already running* World owner for one bounded observation (the phone
    page uses it while the supervisor's own children are up).  A missing or late owner is still
    reported ``UNKNOWN`` with a reason; it is never replaced by a remembered location.
    """

    root_path = phone_life.phone_root(root)
    report = live_day.status(root_path, live=bool(live), limit=limit)
    summary = dict(report.get("summary") or {})
    window = (supervisor.snapshot() if supervisor is not None
              else _read_supervisor(root_path))
    pause = (window.get("paused") if supervisor is not None else None) or pause_state(root_path)
    state_root = report.get("state_root")
    foreground = _foreground_activity(state_root)
    bound_env, _state_root = _bound_environment(root_path, environment)
    governance = _governance_window_policy(
        root,
        Path(str(state_root)) if state_root else _state_root,
    )
    phase = _phase(summary, report, pause, window, foreground, governance)
    memory = _memory_block(service, bound_env)
    reading = _reading_block(service)
    stop = dict(summary.get("stop_point") or {})
    actions = list((summary.get("changed") or {}).get("actions") or [])[-5:]
    last_wake = dict((summary.get("changed") or {}).get("last_wake") or {})
    observed_at = summary.get("segment", {}).get("ended_at") or _now_iso()
    return {
        "schema": SCHEMA_LIVING,
        "read_only": True,
        "writes": False,
        "model_called": False,
        "services_started": 0,
        "observed_at": observed_at,
        "instance": str(root_path),
        "phase": phase,
        "sentence": _sentence(phase, summary, report, foreground),
        "foreground": foreground,
        "where": summary.get("where"),
        "journey": summary.get("journey"),
        "hands": summary.get("hands"),
        "doing": summary.get("doing"),
        "recent": {
            "last_wake": last_wake or None,
            "actions": actions,
            "silence": (summary.get("doing") or {}).get("last_silence"),
        },
        "work": {
            "artifact_id": stop.get("artifact_id"),
            "title": stop.get("title"),
            "head_version_id": stop.get("head_version_id"),
            "resumable": stop.get("resumable"),
            "status": stop.get("status"),
            "unknown_reason": stop.get("unknown_reason"),
            "stopped_at": stop.get("stopped_at"),
            "stop_reason": stop.get("stop_reason"),
            "activity_ref": (stop.get("foreground_activity_ref")
                             or (summary.get("doing") or {}).get("activity_ref")),
        },
        "reading": reading,
        "next": summary.get("next"),
        "memory": memory,
        "run": {
            "resident": {
                "running_recorded": report.get("running_recorded"),
                "pid": report.get("pid"),
                "pid_alive": report.get("pid_alive"),
                "lease": report.get("lease"),
                "note": report.get("note"),
                "status": report.get("status"),
                "reason": report.get("reason"),
            },
            "pause": pause,
            "governance": governance,
            "window": window,
            "capacity": window.get("windows") or window_status(root),
            "supervisor": {
                "attached": bool(window.get("running")),
                "pid": window.get("pid"),
                "port": window.get("port"),
                "child": window.get("child"),
            },
        },
        "limits": {"wakes": int(limit), "private_text_included": False},
        "categories": (summary.get("categories") or {}),
        "budget": (summary.get("changed") or {}).get("budget"),
        "status": report.get("status"),
        "reason": report.get("reason"),
        "detail": report.get("detail"),
        "note": ("此刻只写读到的事实：历史 ACTIVE 记录不会因为页面打开而变成『正在做』；"
                 "Memory 绑定 READY 只表示可读，不表示它影响了这次选择"),
    }


def _read_supervisor(root: Path | str) -> dict[str, Any]:
    doc = _read_json(supervisor_path(root))
    if not doc:
        return {"running": False, "reason": "SUPERVISOR_NOT_RECORDED",
                "windows": window_status(root), "child": None}
    doc["running"] = False
    doc["source"] = "supervisor_record"
    doc["windows"] = window_status(root)
    doc["child"] = doc.get("child") or None
    return doc


CANONICAL_STATE_FILENAME = "canonical_life_state.json"


def _foreground_activity(state_root: Any) -> dict[str, Any]:
    """The foreground Activity from the *authority's own file* — pure read, creates nothing.

    ``one_day_life.activity_view`` owns the verification (READ / UNVERIFIED + reason code), so
    this layer never re-implements "is it open".  The title and the source times are copied from
    the same record, read-only; they are what the page may show instead of a raw activity id.
    A stopped process does not mean the activity disappeared.
    """

    out: dict[str, Any] = {
        "status": "UNKNOWN", "reason_code": "STATE_ROOT_UNKNOWN", "ref": None,
        "title": None, "activity_status": None, "activity_kind": None,
        "paused_at": None, "started_at": None, "updated_at": None,
        "revision": None, "source": CANONICAL_STATE_FILENAME,
    }
    if state_root in (None, ""):
        return out
    try:
        odl = phone_life._life_modules()["odl"]
        view = dict(odl.activity_view(Path(str(state_root))))
    except Exception as exc:
        out.update({"reason_code": "ACTIVITY_VIEW_UNAVAILABLE",
                    "detail": type(exc).__name__})
        return out
    out["status"] = str(view.get("status") or "UNKNOWN")
    out["reason_code"] = view.get("reason_code")
    out["ref"] = view.get("foreground_activity_ref")
    out["activity_status"] = view.get("foreground_status")
    out["activity_kind"] = view.get("foreground_kind")
    out["revision"] = view.get("revision")
    if out["status"] != "READ" or not out["ref"]:
        return out
    try:
        filename = str(getattr(odl, "CANONICAL_ACTIVITY_STATE_FILENAME", CANONICAL_STATE_FILENAME))
        data = json.loads((Path(str(state_root)) / filename).read_text(encoding="utf-8"))
        activities = data.get("activities") if isinstance(data, Mapping) else None
        act = activities.get(out["ref"]) if isinstance(activities, Mapping) else None
        if isinstance(act, Mapping):
            for key in ("title", "paused_at", "started_at", "updated_at"):
                value = act.get(key)
                if isinstance(value, str) and value.strip():
                    out[key] = value.strip()
    except Exception as exc:
        out["detail"] = "ACTIVITY_FIELDS_UNREADABLE:%s" % type(exc).__name__
    return out


def _memory_block(service: Optional[phone_service.PhoneService],
                  environment: Mapping[str, str]) -> dict[str, Any]:
    """One reconciled Memory answer, from the same read-only sources life-preflight uses.

    Two read-only probes are asked and one answer is reported:

    * ``phone.life.check_memory`` — the preflight's own ``mode=ro`` file + governance projection;
      this is the authority for "bound and recallable".
    * ``PhoneService.memory()`` — A's product seam, kept for the ``installed`` fact.

    A *positive* health claim is never overridden by a stale ``UNBOUND`` field on the other
    answer (that contradiction was the bug this replaces); an explicit ``BLOCKED``/``CONFIGURED``
    verdict **is** fail-closed and wins.  No path, owner or secret leaves this function.
    """

    if service is None:
        return {"status": STATE_UNKNOWN, "binding_status": None, "recall_ready": None,
                "recall_state": "unknown", "experience_count": None, "source": None,
                "reason": "NO_PHONE_SERVICE", "note": "Memory 绑定不等于它会参与选择"}
    preflight: Optional[dict[str, Any]] = None
    preflight_error: Optional[str] = None
    try:
        preflight = dict(phone_life.check_memory(dict(environment)))
    except Exception as exc:
        preflight_error = type(exc).__name__
    try:
        # The bound Life environment (nested instance + this phone's live values) is what the
        # resident actually reads.  Using the facade's process env alone would report a bound
        # Memory as UNBOUND just because the operator did not export its three names.
        probe = phone_service.PhoneService(service.instance, environment=dict(environment))
        answer = probe.memory()
    except Exception as exc:
        answer = {"ok": False, "reason_code": "MEMORY_READ_FAILED", "data": {},
                  "detail": type(exc).__name__}
    data = dict(answer.get("data") or {}) if isinstance(answer.get("data"), Mapping) else {}
    service_binding = str(data.get("binding_status") or "")
    service_recall = data.get("recall_ready")
    service_ok = bool(answer.get("ok"))
    pf_status = str((preflight or {}).get("status") or "")
    source: Optional[str] = None
    if pf_status in {"BLOCKED", "CONFIGURED"}:
        binding: str = pf_status
        recall = bool((preflight or {}).get("recall_ready"))
        source = "life_preflight"
    elif pf_status == "READY":
        binding = "READY"
        recall = bool((preflight or {}).get("recall_ready"))
        source = "life_preflight"
    elif service_ok and service_recall is True:
        # READY envelope from the product seam: a stale "UNBOUND" field on the same answer
        # must not be shown as if the binding were missing.
        binding = "READY"
        recall = True
        source = "memory_service"
    elif pf_status == "UNBOUND":
        binding = "UNBOUND"
        recall = False
        source = "life_preflight"
    elif service_binding:
        binding = service_binding
        recall = service_recall is True
        source = "memory_service"
    else:
        binding = "UNKNOWN"
        recall = None
    raw_count = (preflight or {}).get("life_outcomes")
    count = (int(raw_count) if isinstance(raw_count, int) and not isinstance(raw_count, bool)
             else None)
    if binding == "READY" and recall:
        recall_state = "empty" if count == 0 else ("ready" if count else "unknown")
    elif binding in {"UNBOUND", "BLOCKED"}:
        recall_state = "not_ready"
    else:
        recall_state = "unknown"
    return {
        "status": STATE_OK if binding == "READY" else STATE_UNAVAILABLE,
        "binding_status": binding,
        "recall_ready": recall,
        "recall_state": recall_state,
        "experience_count": count,
        "source": source,
        "service_binding_status": service_binding or None,
        "reason": (preflight or {}).get("reason") or answer.get("reason_code"),
        "preflight_error": preflight_error,
        "note": "记忆接通只表示服务可读，不代表它影响了她这次的选择",
    }


def _reading_block(service: Optional[phone_service.PhoneService]) -> dict[str, Any]:
    if service is None:
        return {"status": STATE_UNKNOWN, "reason": "NO_PHONE_SERVICE"}
    try:
        answer = service.reading()
    except Exception as exc:
        return {"status": STATE_UNKNOWN, "reason": "READING_UNAVAILABLE",
                "detail": type(exc).__name__}
    data = dict(answer.get("data") or {})
    source = dict(data.get("source") or {})
    return {
        "status": "READY" if answer.get("ok") else STATE_UNAVAILABLE,
        "title": source.get("title"),
        "page": data.get("page"),
        "pages": data.get("pages"),
        "reason": answer.get("reason_code"),
    }


__all__ = [
    "DEFAULT_WINDOW_SECONDS",
    "FUNCTION_REQUEST_CAP",
    "MAX_WINDOW_REQUESTS",
    "MAX_WINDOW_SECONDS",
    "Supervisor",
    "ledger_view",
    "living_view",
    "pause_state",
    "run_bounded_window",
    "set_paused",
    "supervisor_path",
    "window_status",
]

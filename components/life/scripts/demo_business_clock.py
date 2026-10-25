#!/usr/bin/env python3
"""显式压缩演示模式（COMPRESSED_DEMO）：一个独立的业务时钟域。

这是唯一一份业务时间权威。它**不改系统时间**，不 patch ``datetime``/``time``，
也不缩短任何真实超时：业务 now 由本进程的真实单调时钟 elapsed 乘以比例算出，
真实的安全面（provider 超时、lease、权限过期、请求账本 UTC 日、审计时间）继续
读系统墙钟，完全不受这里的影响。

为什么单独一份
--------------
普通生产路径没有这个文件、也不加载它；``REAL_TIME`` 仍是默认。只有显式
``scripts/fast_day.py prepare`` 出来的新根才会写 ``fastday/clock.json``，
且只有 ``mode == COMPRESSED_DEMO`` 才会被 ``BusinessClockClient`` 接受。

时间模型
--------
* 逻辑日固定 24 逻辑小时；真实时长由 ``rate`` 决定，推荐 3600–7200 真实秒（1–2h）。
* ``rate`` 必须有限、>0、<=24；负数/0/NaN/Inf/超界一律具名拒绝，不静默回退。
* 单进程内：``logical = anchor_logical + (monotonic() - anchor_mono) * rate``，
  到达 horizon 后钳在 horizon。
* 停服：逻辑时间冻结在最后一次检查点；重启从检查点继续，不按墙钟补跑
  （``downtime_frozen`` 如实记录）。
* 一个 domain 同一时刻只允许一个 authority；并发获取具名拒绝。
  其它进程只能通过 ``BusinessClockClient`` 读同一域。

安全边界
--------
本模块只管理业务时间。它不写、不改：``life_request_budget``（真实 UTC 日与
run 计数）、lease、权限 grant、provider deadline、审计 ``recorded_at``。
"""
from __future__ import annotations

import json
import math
import os
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional

try:  # POSIX only; the Life state root already requires it.
    import fcntl
except ImportError:  # pragma: no cover - Windows has no flock
    fcntl = None  # type: ignore[assignment]

KIND = "explicit_compressed_demo_clock"
SCHEMA_VERSION = 1
MODE_COMPRESSED_DEMO = "COMPRESSED_DEMO"
MODE_REAL_TIME = "REAL_TIME"

CLOCK_DIRNAME = "fastday"
CLOCK_FILENAME = "fastday_clock.json"
#: 外部控制位单独一份文件：pause/resume/stop 由另一个进程写，读方低频轮询，
#: 不与被推进的 checkpoint 抢写同一个文件。
CONTROL_FILENAME = "fastday_control.json"

DEFAULT_RATE = 12.0
MAX_RATE = 24.0
DEFAULT_LOGICAL_SECONDS = 24 * 3600
#: 1–2 小时完成一个逻辑日的推荐真实区间（不是安全上限，只是默认换算）。
MIN_REAL_SECONDS = 3600
MAX_REAL_SECONDS = 7200
#: 一次压缩日实验允许的真实墙钟上限：用户批准的是 1–2 小时，不能借 horizon 放大。
MAX_RUN_REAL_SECONDS = 7200.0

#: authority 心跳超过这个真实秒数即视为失联，允许重启接管。
AUTHORITY_STALE_SECONDS = 120.0
#: 外部暂停/停止请求的最短轮询间隔（真实秒），避免每次 now() 都 stat。
EXTERNAL_POLL_SECONDS = 0.2

STATE_RUNNING = "RUNNING"
STATE_FROZEN = "FROZEN"
STATE_STOPPED = "STOPPED"

#: 关闭 fail-closed 的原因码。
REASON_CLOCK_MISSING = "CLOCK_MISSING"
REASON_CLOCK_CORRUPT = "CLOCK_CORRUPT"
REASON_CLOCK_DOMAIN_MISMATCH = "CLOCK_DOMAIN_MISMATCH"
REASON_CLOCK_AUTHORITY_HELD = "CLOCK_AUTHORITY_HELD"
REASON_CLOCK_STOPPED = "CLOCK_STOPPED"
REASON_CLOCK_RATE_INVALID = "CLOCK_RATE_INVALID"
REASON_CLOCK_HORIZON_REACHED = "CLOCK_HORIZON_REACHED"
REASON_CLOCK_ALREADY_EXISTS = "CLOCK_ALREADY_EXISTS"
REASON_EXPERIMENT_CAP_INVALID = "EXPERIMENT_CAP_INVALID"
REASON_EXPERIMENT_CAP_EXHAUSTED = "EXPERIMENT_CAP_EXHAUSTED"
REASON_EXPERIMENT_CAP_UNREADABLE = "EXPERIMENT_CAP_UNREADABLE"
#: 持久账本比本闸门自己记的预留数还少：只能解释为回滚/删档/换账本，
#: 不允许读成 0 继续预留，也不自动补写账本或退款。
REASON_EXPERIMENT_LEDGER_ROLLBACK = "EXPERIMENT_LEDGER_ROLLBACK"
#: 真实墙钟累计账（本功能自己的元数据）不可读/身份不符。
REASON_WALL_BUDGET_UNREADABLE = "WALL_BUDGET_UNREADABLE"
REASON_WALL_BUDGET_EXHAUSTED = "WALL_BUDGET_EXHAUSTED"


class BusinessClockError(RuntimeError):
    """一个具名的失败；调用方按 reason_code 处理，不猜。"""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__("%s: %s" % (reason_code, detail) if detail else reason_code)
        self.reason_code = reason_code
        self.detail = detail


def real_now() -> datetime:
    """真实墙钟，带 UTC 时区。安全面继续用它，本模块不改它。"""

    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def validate_rate(rate: Any) -> float:
    """rate 必须是有限、>0、<=24 的实数；否则具名拒绝（不静默回退）。"""

    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise BusinessClockError(REASON_CLOCK_RATE_INVALID,
                                 "rate 必须是数值，收到 %r" % (rate,))
    value = float(rate)
    if not math.isfinite(value) or value <= 0.0 or value > MAX_RATE:
        raise BusinessClockError(
            REASON_CLOCK_RATE_INVALID,
            "rate 必须有限且 0<rate<=%g，收到 %r" % (MAX_RATE, rate))
    return value


def rate_for_real_seconds(real_seconds: Any,
                          logical_seconds: float = DEFAULT_LOGICAL_SECONDS) -> float:
    """一个逻辑日放进多少真实秒，换算成 rate。"""

    if isinstance(real_seconds, bool) or not isinstance(real_seconds, (int, float)):
        raise BusinessClockError(REASON_CLOCK_RATE_INVALID,
                                 "real_seconds 必须是数值，收到 %r" % (real_seconds,))
    value = float(real_seconds)
    if not math.isfinite(value) or value <= 0.0:
        raise BusinessClockError(REASON_CLOCK_RATE_INVALID,
                                 "real_seconds 必须有限且为正，收到 %r" % (real_seconds,))
    return validate_rate(float(logical_seconds) / value)


def clock_path(state_root: Path | str) -> Path:
    return Path(state_root) / CLOCK_DIRNAME / CLOCK_FILENAME


def clock_dir(state_root: Path | str) -> Path:
    return Path(state_root) / CLOCK_DIRNAME


def control_path(state_root: Path | str) -> Path:
    return clock_dir(state_root) / CONTROL_FILENAME


def write_control(state_root: Path | str, *, pause_requested: Optional[bool] = None,
                  stop_requested: Optional[bool] = None,
                  reason: str = "operator") -> dict[str, Any]:
    """外部控制位（纯文件写，可在实例停止时写）。只改显式给出的那一位。"""

    path = control_path(state_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, Mapping):
        data = {}
    payload = dict(data)
    if pause_requested is not None:
        payload["pause_requested"] = bool(pause_requested)
    if stop_requested is not None:
        payload["stop_requested"] = bool(stop_requested)
    payload["reason"] = str(reason or "operator")
    payload["updated_utc"] = _iso(real_now())
    _write_json(path, payload)
    return payload


def read_control(state_root: Path | str) -> dict[str, Any]:
    try:
        data = json.loads(control_path(state_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def _control_view(state_root: Path | str) -> dict[str, Any]:
    control = read_control(state_root)
    return {"pause_requested": bool(control.get("pause_requested")),
            "stop_requested": bool(control.get("stop_requested")),
            "reason": control.get("reason"),
            "updated_utc": control.get("updated_utc")}


def domain_for(run_id: str) -> str:
    return "fastday:%s" % str(run_id).strip()


def _pid_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # 平台不支持 signal 0（例如 Windows）：无法证明存活，交给心跳判断。
        return False
    return True


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def read_clock_file(path: Path | str) -> dict[str, Any]:
    """读并校验时钟文件；缺失/损坏/字段非法一律 fail closed。"""

    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BusinessClockError(REASON_CLOCK_MISSING, str(target))
    except (OSError, ValueError) as exc:
        raise BusinessClockError(REASON_CLOCK_CORRUPT,
                                 "%s: %s" % (type(exc).__name__, exc))
    if not isinstance(data, Mapping):
        raise BusinessClockError(REASON_CLOCK_CORRUPT, "根不是对象")
    if data.get("kind") != KIND:
        raise BusinessClockError(REASON_CLOCK_CORRUPT,
                                 "kind=%r" % (data.get("kind"),))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise BusinessClockError(REASON_CLOCK_CORRUPT,
                                 "schema_version=%r" % (data.get("schema_version"),))
    if data.get("mode") != MODE_COMPRESSED_DEMO:
        raise BusinessClockError(REASON_CLOCK_CORRUPT, "mode=%r" % (data.get("mode"),))
    rate = validate_rate(data.get("rate"))
    domain = str(data.get("domain_id") or "").strip()
    run_id = str(data.get("run_id") or "").strip()
    if not domain or not run_id:
        raise BusinessClockError(REASON_CLOCK_CORRUPT, "缺少 domain_id/run_id")
    anchor = _parse(data.get("logical_anchor"))
    checkpoint = _parse(data.get("logical_checkpoint"))
    horizon = _parse(data.get("horizon_logical"))
    if anchor is None or checkpoint is None or horizon is None:
        raise BusinessClockError(REASON_CLOCK_CORRUPT, "缺少逻辑锚点/检查点/horizon")
    if horizon < anchor:
        raise BusinessClockError(REASON_CLOCK_CORRUPT, "horizon 早于 anchor")
    if checkpoint < anchor or checkpoint > horizon:
        raise BusinessClockError(REASON_CLOCK_CORRUPT, "检查点越界")
    if str(data.get("state") or "") not in {STATE_RUNNING, STATE_FROZEN, STATE_STOPPED}:
        raise BusinessClockError(REASON_CLOCK_CORRUPT, "state=%r" % (data.get("state"),))
    return {**dict(data), "rate": rate, "domain_id": domain, "run_id": run_id}


class BusinessClockClient:
    """只读客户端：任何非 authority 进程都只能读同一域的时刻。

    它不写文件、不推进时间。缺文件、损坏、域不匹配都抛 ``BusinessClockError``，
    调用方据此具名拒绝，而不是退回系统墙钟（那会悄悄混钟）。
    """

    def __init__(self, path: Path | str, *, expected_domain: Optional[str] = None,
                 expected_run_id: Optional[str] = None) -> None:
        self.path = Path(path)
        self.expected_domain = (str(expected_domain).strip() if expected_domain else None)
        self.expected_run_id = (str(expected_run_id).strip() if expected_run_id else None)

    def load(self) -> dict[str, Any]:
        data = read_clock_file(self.path)
        if self.expected_domain is not None and data["domain_id"] != self.expected_domain:
            raise BusinessClockError(
                REASON_CLOCK_DOMAIN_MISMATCH,
                "期望域 %s，时钟域 %s" % (self.expected_domain, data["domain_id"]))
        if self.expected_run_id is not None and data["run_id"] != self.expected_run_id:
            raise BusinessClockError(
                REASON_CLOCK_DOMAIN_MISMATCH,
                "期望 run %s，时钟 run %s" % (self.expected_run_id, data["run_id"]))
        return data

    def now(self) -> datetime:
        data = self.load()
        return _parse(data.get("logical_checkpoint")) or _parse(data.get("logical_anchor"))

    def snapshot(self) -> dict[str, Any]:
        data = self.load()
        logical = _parse(data.get("logical_checkpoint"))
        anchor = _parse(data.get("logical_anchor"))
        horizon = _parse(data.get("horizon_logical"))
        return {
            "mode": MODE_COMPRESSED_DEMO,
            "domain_id": data["domain_id"],
            "run_id": data["run_id"],
            "rate": data["rate"],
            "state": data.get("state"),
            "logical_at": _iso(logical),
            "logical_anchor": _iso(anchor),
            "logical_elapsed_seconds": int((logical - anchor).total_seconds()),
            "horizon_logical": _iso(horizon),
            "horizon_remaining_seconds": int((horizon - logical).total_seconds()),
            "real_at": _iso(real_now()),
            "authority": data.get("authority"),
            "downtime_frozen": bool(data.get("downtime_frozen")),
            "control": _control_view(self.path.parent.parent),
        }


class BusinessClockAuthority:
    """一个域里唯一的业务时间权威；由 ``fast_day start`` 进程持有。

    真实性能/超时永远不读它：那一边继续用系统墙钟与 ``time.monotonic``。
    """

    def __init__(
        self,
        state_root: Path | str,
        *,
        run_id: str,
        domain_id: Optional[str] = None,
        rate: float = DEFAULT_RATE,
        logical_seconds: float = DEFAULT_LOGICAL_SECONDS,
        monotonic: Optional[Callable[[], float]] = None,
        wall: Optional[Callable[[], datetime]] = None,
        pid: Optional[int] = None,
        host: Optional[str] = None,
    ) -> None:
        self.path = clock_path(state_root)
        self.state_root = Path(state_root)
        self.run_id = str(run_id).strip()
        if not self.run_id:
            raise BusinessClockError(REASON_CLOCK_CORRUPT, "run_id 为空")
        self.domain_id = str(domain_id or domain_for(self.run_id)).strip()
        self.rate = validate_rate(rate)
        self.logical_seconds = float(logical_seconds)
        if not math.isfinite(self.logical_seconds) or self.logical_seconds <= 0:
            raise BusinessClockError(REASON_CLOCK_RATE_INVALID,
                                     "logical_seconds 必须为正的有限值")
        self._monotonic = monotonic or time.monotonic
        self._wall = wall or real_now
        self.pid = int(os.getpid() if pid is None else pid)
        self.host = host or socket.gethostname()
        self._mono_anchor: Optional[float] = None
        self._logical_anchor: Optional[datetime] = None
        self._horizon: Optional[datetime] = None
        self._state: Optional[str] = None
        self._data: dict[str, Any] = {}
        self._last_external_poll = -1e9
        #: 只有「外部控制位导致的冻结」才会被外部清除自动解除；程序内 freeze() 不会被
        #: 一次空的 control 轮询悄悄 resume。
        self._external_pause_active = False
        self.acquired = False
        self.report: dict[str, Any] = {}
        # 初始锚点只用于写/读第一份文件；``acquire`` 会按现场重新决定。
        self._logical_anchor = self._wall()
        self._horizon = self._logical_anchor + timedelta(seconds=self.logical_seconds)
        self._state = STATE_FROZEN

    # -- helpers ---------------------------------------------------------

    def _authority_alive(self, authority: Mapping[str, Any]) -> bool:
        if not isinstance(authority, Mapping):
            return False
        other_pid = authority.get("pid")
        if other_pid == self.pid and authority.get("host") == self.host:
            return False  # 自己上一次的残留，不阻止本次接管
        if authority.get("host") == self.host:
            # 同机：pid 是否存活就是事实，不拿心跳去把一个已死进程说活。
            return _pid_alive(other_pid)
        heartbeat = _parse(authority.get("heartbeat_utc"))
        if heartbeat is None:
            return False
        age = (self._wall() - heartbeat).total_seconds()
        return -AUTHORITY_STALE_SECONDS <= age <= AUTHORITY_STALE_SECONDS

    def _base_payload(self) -> dict[str, Any]:
        return {
            "kind": KIND,
            "schema_version": SCHEMA_VERSION,
            "mode": MODE_COMPRESSED_DEMO,
            "domain_id": self.domain_id,
            "run_id": self.run_id,
            "rate": self.rate,
            "logical_seconds": self.logical_seconds,
            "logical_anchor": _iso(self._logical_anchor),
            "logical_checkpoint": _iso(self._logical_anchor),
            "horizon_logical": _iso(self._horizon),
            "state": self._state,
            "authority": {
                "pid": self.pid,
                "host": self.host,
                "process_id": "%s:%d" % (self.host, self.pid),
                "acquired_utc": _iso(self._wall()),
                "heartbeat_utc": _iso(self._wall()),
            },
            "downtime_frozen": False,
            "ticks": 0,
            "created_utc": _iso(self._wall()),
            "updated_utc": _iso(self._wall()),
            "note": "压缩逻辑日，不是真实 24 小时；真实超时/权限/预算时钟不加速",
        }

    def _save(self, **changes: Any) -> dict[str, Any]:
        payload = dict(self._data or self._base_payload())
        payload.update(changes)
        payload["logical_checkpoint"] = _iso(self._logical_now_uncached())
        payload["state"] = self._state
        authority = dict(payload.get("authority") or {})
        authority.update({"pid": self.pid, "host": self.host,
                          "heartbeat_utc": _iso(self._wall())})
        payload["authority"] = authority
        payload["updated_utc"] = _iso(self._wall())
        self._data = payload
        _write_json(self.path, payload)
        return payload

    def _logical_now_uncached(self) -> datetime:
        if self._logical_anchor is None or self._horizon is None:
            raise BusinessClockError(REASON_CLOCK_CORRUPT, "authority 未初始化")
        if self._state != STATE_RUNNING or self._mono_anchor is None:
            return self._logical_anchor
        elapsed = max(0.0, self._monotonic() - self._mono_anchor)
        moment = self._logical_anchor + timedelta(seconds=elapsed * self.rate)
        if moment > self._horizon:
            return self._horizon
        return moment

    # -- lifecycle -------------------------------------------------------

    def acquire(self) -> dict[str, Any]:
        """取域权威；已存在的同域文件只接管失联租约，运行中的并发权威具名拒绝。"""

        if self.path.exists():
            data = read_clock_file(self.path)
            if data["domain_id"] != self.domain_id:
                raise BusinessClockError(
                    REASON_CLOCK_DOMAIN_MISMATCH,
                    "文件域 %s，期望 %s" % (data["domain_id"], self.domain_id))
            if data["run_id"] != self.run_id:
                raise BusinessClockError(
                    REASON_CLOCK_DOMAIN_MISMATCH,
                    "文件 run %s，期望 %s" % (data["run_id"], self.run_id))
            if data.get("state") == STATE_STOPPED:
                raise BusinessClockError(REASON_CLOCK_STOPPED,
                                         "该 run 已停止；不重新开跑，请新 run")
            if self._authority_alive(data.get("authority") or {}):
                raise BusinessClockError(
                    REASON_CLOCK_AUTHORITY_HELD,
                    "%s 的权威仍存活" % ((data.get("authority") or {}).get("process_id"),))
            self._logical_anchor = _parse(data.get("logical_checkpoint"))
            self._horizon = _parse(data.get("horizon_logical"))
            self._state = str(data.get("state") or STATE_FROZEN)
            self._data = dict(data)
            takeover = True
            # 停服期间的逻辑时间冻结在检查点，不按墙钟补跑：恢复运行但锚点固定在
            # 检查点 —— 既不倒退，也不把离线时间算成她的生活。操作员显式 pause 的
            # 时钟保持冻结，直到 resume。
            downtime_frozen = True
            operator_paused = bool(read_control(self.state_root).get("pause_requested"))
            self._external_pause_active = operator_paused
            self._state = STATE_FROZEN if operator_paused else STATE_RUNNING
        else:
            self._logical_anchor = self._wall()
            self._horizon = self._logical_anchor + timedelta(seconds=self.logical_seconds)
            self._state = STATE_RUNNING
            self._data = self._base_payload()
            takeover = False
            downtime_frozen = False
        self._mono_anchor = self._monotonic()
        self.acquired = True
        self.report = {
            "ok": True,
            "domain_id": self.domain_id,
            "run_id": self.run_id,
            "took_over": takeover,
            "downtime_frozen": downtime_frozen,
            "state": self._state,
            "logical_at": _iso(self._logical_now_uncached()),
            "real_at": _iso(self._wall()),
            "rate": self.rate,
        }
        self._data["downtime_frozen"] = bool(downtime_frozen)
        self._save()
        if self._state == STATE_RUNNING and downtime_frozen:
            # 恢复运行但锚点固定在检查点：重启没有补跑，也没有倒退。
            self._mono_anchor = self._monotonic()
            self._save()
        return dict(self.report)

    def _refresh_external(self) -> None:
        """外部 pause/resume/stop 请求：低频读文件，读到就落实到本进程状态。"""

        moment = self._monotonic()
        if moment - self._last_external_poll < EXTERNAL_POLL_SECONDS:
            return
        self._last_external_poll = moment
        control = read_control(self.state_root)
        if control.get("stop_requested"):
            self.stop()
            return
        paused = bool(control.get("pause_requested"))
        if paused and self._state == STATE_RUNNING:
            self._external_pause_active = True
            self.freeze(reason=str(control.get("reason") or "operator"))
        elif (not paused) and self._state == STATE_FROZEN and self._external_pause_active:
            self._external_pause_active = False
            self.resume()

    # -- the business time ----------------------------------------------

    def now(self) -> datetime:
        if not self.acquired:
            raise BusinessClockError(REASON_CLOCK_MISSING, "authority 尚未 acquire")
        self._refresh_external()
        return self._logical_now_uncached()

    def now_iso(self) -> str:
        return _iso(self.now())

    @property
    def exhausted(self) -> bool:
        return self._state != STATE_STOPPED and self.now() >= (self._horizon or self.now())

    def freeze(self, *, reason: str = "operator") -> dict[str, Any]:
        if self._state == STATE_RUNNING:
            self._logical_anchor = self._logical_now_uncached()
            self._state = STATE_FROZEN
            self._mono_anchor = None
            self._data["frozen_reason"] = str(reason or "operator")
            self._save()
        return {"state": self._state, "logical_at": _iso(self._logical_now_uncached()),
                "reason": reason}

    def resume(self) -> dict[str, Any]:
        self._external_pause_active = False
        if self._state == STATE_FROZEN:
            self._state = STATE_RUNNING
            self._mono_anchor = self._monotonic()
            self._save()
        return {"state": self._state, "logical_at": _iso(self._logical_now_uncached())}

    def checkpoint(self, *, ticks: Optional[int] = None) -> dict[str, Any]:
        if not self.acquired:
            raise BusinessClockError(REASON_CLOCK_MISSING, "authority 尚未 acquire")
        changes: dict[str, Any] = {}
        if ticks is not None:
            changes["ticks"] = int(ticks)
        return self._save(**changes)

    def stop(self) -> dict[str, Any]:
        if not self.acquired:
            return {"state": self._state, "ok": False, "reason": REASON_CLOCK_MISSING}
        self._logical_anchor = self._logical_now_uncached()
        self._state = STATE_STOPPED
        self._mono_anchor = None
        payload = self._save()
        return {"ok": True, "state": payload.get("state"),
                "logical_at": payload.get("logical_checkpoint"),
                "real_at": _iso(self._wall())}

    def snapshot(self) -> dict[str, Any]:
        logical = self._logical_now_uncached()
        horizon = self._horizon or logical
        return {
            "mode": MODE_COMPRESSED_DEMO,
            "domain_id": self.domain_id,
            "run_id": self.run_id,
            "rate": self.rate,
            "state": self._state,
            "logical_at": _iso(logical),
            "logical_anchor": _iso(self._logical_anchor),
            "logical_elapsed_seconds": int((logical - self._logical_anchor).total_seconds()),
            "horizon_logical": _iso(horizon),
            "horizon_remaining_seconds": max(0, int((horizon - logical).total_seconds())),
            "real_at": _iso(self._wall()),
            "authority": self._data.get("authority"),
            "downtime_frozen": bool(self._data.get("downtime_frozen")),
            "note": "压缩逻辑日，不是真实 24 小时",
        }


def create_clock(state_root: Path | str, *, run_id: str, rate: float,
                 logical_seconds: float = DEFAULT_LOGICAL_SECONDS,
                 wall: Optional[Callable[[], datetime]] = None,
                 domain_id: Optional[str] = None) -> dict[str, Any]:
    """写一个新域的初始时钟文件；已存在则具名拒绝（不静默覆盖）。"""

    path = clock_path(state_root)
    if path.exists():
        raise BusinessClockError(REASON_CLOCK_ALREADY_EXISTS, str(path))
    wall = wall or real_now
    authority = BusinessClockAuthority(
        state_root, run_id=run_id, rate=rate, logical_seconds=logical_seconds, wall=wall,
        domain_id=domain_id)
    payload = authority._base_payload()
    # 只是一个「已准备、未启动」的时钟：没有活着的 authority，不能被当成并发占用。
    payload["authority"] = None
    payload["state"] = STATE_FROZEN
    _write_json(path, payload)
    return read_clock_file(path)


# -- experiment hard cap ----------------------------------------------------

EXPERIMENT_FILENAME = "experiment_budget.json"
CAP_LOCK_FILENAME = "experiment_budget.lock"
#: 默认/旧配置的总请求上限。普通 ``prepare`` 路径与旧元数据都按它解释，行为不变。
HARD_CONFIG_MAX = 96
#: 默认配置下实验额外上限的旧上限（96 总配置不提高）。
MAX_EXPERIMENT_REQUESTS = 82
#: 用户已授权的共同总请求上限。总上限只能显式绑定到单个新实例，不能变成全项目默认。
MAX_TOTAL_REQUESTS_LIMIT = 300


def experiment_cap_ceiling(total_limit: int, historical_requests: int = 0) -> int:
    """本实例允许的实验新增上限：``总限 - 历史基线``，旧默认另受 82 约束。

    * 默认（``total_limit == HARD_CONFIG_MAX``）保持旧的 ``<=82``；
    * 显式提高的总限（例如 300/历史 14）只允许 ``<= 总限 - 历史``，避免
      「历史 + 新增」超过共同授权总量，也避免借改元数据再拿一份额度。
    """

    total = int(total_limit)
    historical = int(historical_requests)
    ceiling = total - historical
    if total <= HARD_CONFIG_MAX:
        ceiling = min(ceiling, MAX_EXPERIMENT_REQUESTS)
    return max(0, ceiling)


def experiment_path(state_root: Path | str) -> Path:
    return clock_dir(state_root) / EXPERIMENT_FILENAME


def cap_lock_path(state_root: Path | str) -> Path:
    return clock_dir(state_root) / CAP_LOCK_FILENAME


@contextmanager
def _cap_exclusive(state_root: Path | str) -> Iterator[None]:
    """把「读实验上限 + 预留账本 + 记高水位」放在同一把跨进程锁里。

    没有它，两个 writer（或一次 wake 的并发路径）可以同时通过上限检查、各自预留，
    最后一起超过 cap。锁文件只属于本实验的 state root。
    """

    path = cap_lock_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:  # pragma: no cover
                pass
        handle.close()


class ExperimentRequestCap:
    """一次 fastday 实验的额外硬上限，持久、跨重启、跨真实 UTC 日都不刷新。

    默认（总限 96）仍是旧的 ``<=82``；显式新总限（例如用户授权的 300）按
    ``新建上限 <= 总限 - 历史基线`` 绑定，并且只写在这一个新实例的元数据里，
    不改全项目默认。上限文件同时记录授权总限与历史基线；旧文件没有这两个字段时
    按 96/0 解释（兼容旧 96/82，不静默升级）。

    已用量取「同一 run 窗口的账本计数 - 实验开始时基线」与「历史高水位」的较大值：
    账本被重置/回滚只会让用量看起来更小，高水位保证它不会因此退款；账本读不出来
    就 fail closed，绝不猜 0。历史基线只用于证明「历史 + 新增 <= 总限」，**不会**
    写进本 run 的真实计数。
    """

    def __init__(self, state_root: Path | str, *, run_id: str,
                 max_new_requests: int, total_limit: Optional[int] = None,
                 historical_requests: int = 0) -> None:
        self.path = experiment_path(state_root)
        self.state_root = Path(state_root)
        self.run_id = str(run_id).strip()
        for name, value in (("max_new_requests", max_new_requests),
                            ("total_limit", HARD_CONFIG_MAX if total_limit is None
                             else total_limit),
                            ("historical_requests", historical_requests)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise BusinessClockError(REASON_EXPERIMENT_CAP_INVALID,
                                         "%s 必须是整数" % name)
        total = int(HARD_CONFIG_MAX if total_limit is None else total_limit)
        historical = int(historical_requests)
        if total < 1 or total > MAX_TOTAL_REQUESTS_LIMIT:
            raise BusinessClockError(
                REASON_EXPERIMENT_CAP_INVALID,
                "total_limit 必须在 1..%d 之间（授权上限不能变成全项目默认）"
                % MAX_TOTAL_REQUESTS_LIMIT)
        if historical < 0 or historical > total:
            raise BusinessClockError(REASON_EXPERIMENT_CAP_INVALID,
                                     "historical_requests 必须在 0..total_limit 之间")
        ceiling = experiment_cap_ceiling(total, historical)
        if max_new_requests < 1 or max_new_requests > ceiling:
            raise BusinessClockError(
                REASON_EXPERIMENT_CAP_INVALID,
                "max_new_requests 必须在 1..%d 之间（总限 %d - 历史 %d%s）"
                % (ceiling, total, historical,
                   "，默认 96 总配置不提高" if total <= HARD_CONFIG_MAX else ""))
        self.max_new_requests = int(max_new_requests)
        self.total_limit = total
        self.historical_requests = historical

    def create(self, *, baseline_run_window_calls: int = 0) -> dict[str, Any]:
        if self.path.exists():
            raise BusinessClockError(REASON_CLOCK_ALREADY_EXISTS, str(self.path))
        payload = {
            "kind": "fastday_experiment_request_cap",
            "schema_version": 1,
            "run_id": self.run_id,
            "max_new_requests": self.max_new_requests,
            #: 显式绑定到本实例的授权总限与历史基线：默认仍是 96/0。
            "authorized_total_limit": self.total_limit,
            "historical_requests": self.historical_requests,
            #: 兼容旧读面：旧字段现在表示本实例的授权总限（默认仍是 96）。
            "hard_config_max": self.total_limit,
            "baseline_run_window_calls": max(0, int(baseline_run_window_calls)),
            "used_high_water": 0,
            #: 本闸门自己记的预留数：账本被回滚/替换时它不跟着变小。
            "reserved_calls": 0,
            "created_utc": _iso(real_now()),
        }
        _write_json(self.path, payload)
        return payload

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BusinessClockError(REASON_EXPERIMENT_CAP_UNREADABLE,
                                     "%s: %s" % (type(exc).__name__, exc))
        if not isinstance(data, Mapping) or data.get("run_id") != self.run_id:
            raise BusinessClockError(REASON_EXPERIMENT_CAP_UNREADABLE,
                                     "run_id 不匹配或文件损坏")
        limit = data.get("max_new_requests")
        # 旧元数据没有这两个字段：按默认 96/0 解释，绝不悄悄升级成新额度。
        total = data.get("authorized_total_limit", HARD_CONFIG_MAX)
        historical = data.get("historical_requests", 0)
        if isinstance(total, bool) or not isinstance(total, int) \
                or total < 1 or total > MAX_TOTAL_REQUESTS_LIMIT:
            raise BusinessClockError(REASON_EXPERIMENT_CAP_UNREADABLE,
                                     "authorized_total_limit 越界")
        if isinstance(historical, bool) or not isinstance(historical, int) \
                or historical < 0 or historical > total:
            raise BusinessClockError(REASON_EXPERIMENT_CAP_UNREADABLE,
                                     "historical_requests 越界")
        ceiling = experiment_cap_ceiling(total, historical)
        if isinstance(limit, bool) or not isinstance(limit, int) \
                or limit < 1 or limit > ceiling:
            raise BusinessClockError(REASON_EXPERIMENT_CAP_UNREADABLE,
                                     "max_new_requests 越界（上限 %d）" % ceiling)
        # 持久元数据必须与本实例构造时绑定的授权一致：只改文件把额度抬高时拒绝。
        if total != self.total_limit or historical != self.historical_requests:
            raise BusinessClockError(
                REASON_EXPERIMENT_CAP_UNREADABLE,
                "授权元数据与实例配置不一致：文件 %d/%d，实例 %d/%d"
                % (total, historical, self.total_limit, self.historical_requests))
        if limit != self.max_new_requests:
            raise BusinessClockError(
                REASON_EXPERIMENT_CAP_UNREADABLE,
                "实验上限与实例配置不一致：文件 %d，实例 %d"
                % (limit, self.max_new_requests))
        return dict(data)

    def view(self, ledger_view: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """当前剩余额度。账本不可读 -> fail closed（remaining=None, allowed=False）。"""

        data = self.load()
        baseline = int(data.get("baseline_run_window_calls") or 0)
        high_water = int(data.get("used_high_water") or 0)
        reserved = int(data.get("reserved_calls") or 0)
        high_water = max(high_water, reserved)
        limit = int(data["max_new_requests"])
        base = {"max_new_requests": limit,
                "hard_config_max": int(data.get("authorized_total_limit", HARD_CONFIG_MAX)),
                "authorized_total_limit": int(data.get("authorized_total_limit",
                                                       HARD_CONFIG_MAX)),
                "historical_requests": int(data.get("historical_requests") or 0),
                "run_id": data.get("run_id"),
                "source": "PERSISTED_EXPERIMENT_CAP"}
        if ledger_view is None:
            return {**base, "used": high_water, "remaining": max(0, limit - high_water),
                    "allowed": high_water < limit, "ledger": "NOT_READ"}
        window = ledger_view.get("run_window_calls")
        run_id = str(ledger_view.get("run_id") or "")
        ledger_state = ledger_view.get("state")
        windows = ledger_view.get("windows")
        # 账本被整体替换成**另一个 run** 的账本时，我们自己的窗口不在里面；旧实现把
        # 这读成 0，等于给新 run 免费额度。这里按账本自己的 run 身份和窗口表 fail closed。
        if run_id and run_id != self.run_id:
            return {**base, "used": None, "remaining": None, "allowed": False,
                    "ledger": "RUN_MISMATCH", "ledger_run_id": run_id,
                    "reason_code": REASON_EXPERIMENT_CAP_UNREADABLE}
        if (window is None and isinstance(windows, Mapping) and windows
                and self.run_id not in windows):
            return {**base, "used": None, "remaining": None, "allowed": False,
                    "ledger": "RUN_MISMATCH", "ledger_run_id": None,
                    "ledger_windows": sorted(str(key) for key in windows)[-8:],
                    "reason_code": REASON_EXPERIMENT_CAP_UNREADABLE}
        if window is None and ledger_state in (None, "ok", "absent"):
            # 这个 run 还没有打开请求窗口：该 run 的已用就是 0（不是「未知」）。
            window = 0
        if isinstance(window, bool) or not isinstance(window, int) \
                or ledger_state not in (None, "ok", "absent"):
            return {**base, "used": None, "remaining": None, "allowed": False,
                    "ledger": "UNREADABLE", "ledger_state": ledger_view.get("state"),
                    "reason_code": REASON_EXPERIMENT_CAP_UNREADABLE}
        observed = max(0, int(window) - baseline)
        # 账本看到的预留数比本闸门自己记下的还少（或整块账本被删）——这不是「还剩额度」，
        # 而是持久状态被回滚/替换过。下一次预留前就具名拒绝：不读成 0、不补写账本、
        # 不退款、也不继续预留。全新实例第一次预留时 reserved=0，不会走到这里。
        if reserved > 0 and observed < reserved:
            return {**base, "used": None, "remaining": None, "allowed": False,
                    "ledger": "ROLLED_BACK", "ledger_run_window_calls": int(window),
                    "reserved_calls": reserved, "baseline": baseline,
                    "reason_code": REASON_EXPERIMENT_LEDGER_ROLLBACK}
        used = max(high_water, observed)
        if used > high_water:
            self._write_high_water(used)
        return {**base, "used": used, "remaining": max(0, limit - used),
                "allowed": used < limit, "ledger": "OK",
                "ledger_run_window_calls": int(window), "baseline": baseline,
                "used_high_water": max(high_water, used)}

    def note_reservation(self) -> dict[str, Any]:
        """记一次真实的成功预留；账本被回滚/整体替换时这个计数也不跟着变小。"""

        data = self.load()
        data["reserved_calls"] = int(data.get("reserved_calls") or 0) + 1
        data["used_high_water"] = max(int(data.get("used_high_water") or 0),
                                      int(data["reserved_calls"]))
        data["updated_utc"] = _iso(real_now())
        _write_json(self.path, data)
        return data

    def _write_high_water(self, used: int) -> None:
        data = self.load()
        data["used_high_water"] = int(used)
        data["updated_utc"] = _iso(real_now())
        _write_json(self.path, data)


WALL_FILENAME = "fastday_wall.json"
WALL_KIND = "fastday_wall_budget"
WALL_SCHEMA_VERSION = 1


def wall_path(state_root: Path | str) -> Path:
    return clock_dir(state_root) / WALL_FILENAME


class WallBudget:
    """一个 run 的**真实墙钟**累计用量（本功能自己的元数据，不是生活时间）。

    压缩的是业务时间；一个逻辑日仍然必须真实地过完 1–2 小时。``pause`` 只冻结逻辑
    时钟，墙钟照走；重启也不能把暂停/停机时已经花掉的墙钟「借回来」。因此这里记的
    是持久累计真实秒数，而不是从逻辑余量反推：

    * ``open`` 在 ``start`` 真正开跑前打开一段，``close`` 在正常收尾时把这段真实
      elapsed 加进 ``consumed_seconds``；
    * 进程被 kill/崩溃后段仍开着：下一次 ``recover`` 按墙钟时间戳**保守**扣掉整段
      （停机时间也算进去），不凭逻辑余量补退款；
    * 账本损坏或 run 身份不符一律 fail closed；首次缺失只按 0 初始化。
    """

    def __init__(self, state_root: Path | str, *, run_id: str,
                 max_seconds: float = MAX_RUN_REAL_SECONDS,
                 now: Optional[Callable[[], datetime]] = None) -> None:
        self.path = wall_path(state_root)
        self.state_root = Path(state_root)
        self.run_id = str(run_id).strip()
        if not self.run_id:
            raise BusinessClockError(REASON_WALL_BUDGET_UNREADABLE, "run_id 为空")
        if not math.isfinite(float(max_seconds)) or float(max_seconds) <= 0:
            raise BusinessClockError(REASON_WALL_BUDGET_UNREADABLE,
                                     "max_seconds 必须为正的有限值")
        self.max_seconds = min(float(max_seconds), MAX_RUN_REAL_SECONDS)
        self._now = now or real_now

    # -- raw io -----------------------------------------------------------

    def _read(self) -> Optional[dict[str, Any]]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise BusinessClockError(REASON_WALL_BUDGET_UNREADABLE,
                                     "%s: %s" % (type(exc).__name__, exc))
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise BusinessClockError(REASON_WALL_BUDGET_UNREADABLE, str(exc))
        if not isinstance(data, Mapping) or data.get("kind") != WALL_KIND \
                or data.get("schema_version") != WALL_SCHEMA_VERSION:
            raise BusinessClockError(REASON_WALL_BUDGET_UNREADABLE, "schema 不符")
        if str(data.get("run_id") or "") != self.run_id:
            raise BusinessClockError(REASON_WALL_BUDGET_UNREADABLE,
                                     "run 身份不符：%r" % (data.get("run_id"),))
        consumed = data.get("consumed_seconds")
        if isinstance(consumed, bool) or not isinstance(consumed, (int, float)) \
                or not math.isfinite(float(consumed)) or float(consumed) < 0:
            raise BusinessClockError(REASON_WALL_BUDGET_UNREADABLE, "consumed 非法")
        opened = data.get("open_since_utc")
        if opened is not None and _parse(opened) is None:
            raise BusinessClockError(REASON_WALL_BUDGET_UNREADABLE, "open_since 非法")
        return dict(data)

    def _write(self, *, consumed: float, open_since: Optional[datetime],
               open_pid: Optional[int] = None,
               open_host: Optional[str] = None) -> dict[str, Any]:
        payload = {
            "kind": WALL_KIND,
            "schema_version": WALL_SCHEMA_VERSION,
            "run_id": self.run_id,
            "max_seconds": MAX_RUN_REAL_SECONDS,
            "consumed_seconds": round(max(0.0, float(consumed)), 3),
            "open_since_utc": _iso(open_since) if open_since is not None else None,
            "open_pid": int(open_pid) if open_since is not None and open_pid else None,
            "open_host": (str(open_host) if open_since is not None and open_host else None),
            "updated_utc": _iso(self._now()),
            "note": "只记本实验的真实墙钟累计；业务逻辑时间不在这里，暂停/停机也不退款",
        }
        _write_json(self.path, payload)
        return payload

    def _ensure(self) -> dict[str, Any]:
        data = self._read()
        if data is not None:
            return data
        return self._write(consumed=0.0, open_since=None)

    # -- view -------------------------------------------------------------

    def view(self) -> dict[str, Any]:
        data = self._read()
        if data is None:
            consumed = 0.0
            open_since = None
        else:
            consumed = float(data.get("consumed_seconds") or 0.0)
            open_since = _parse(data.get("open_since_utc"))
        remaining = max(0.0, self.max_seconds - consumed)
        return {
            "state": "ok" if data is not None else "absent",
            "run_id": self.run_id,
            "max_seconds": self.max_seconds,
            "consumed_seconds": round(consumed, 3),
            "remaining_seconds": round(remaining, 3),
            "open": open_since is not None,
            "open_since_utc": _iso(open_since) if open_since is not None else None,
            "allowed": remaining > 0.0,
            "path": str(self.path),
        }

    def create(self) -> dict[str, Any]:
        if self.path.exists():
            raise BusinessClockError(REASON_CLOCK_ALREADY_EXISTS, str(self.path))
        self._write(consumed=0.0, open_since=None)
        return self.view()

    # -- segments ---------------------------------------------------------

    def recover(self) -> dict[str, Any]:
        """上一次进程没有正常收尾：把还开着的整段墙钟**保守**记进去。

        只用持久墙钟时间戳估算；负值钳 0（不退款），超过剩余就钳在账本里，
        下一次 ``open`` 会因为 ``remaining<=0`` 被拒。没有正常收尾记录时是 no-op。
        """

        data = self._read()
        if data is None:
            self._write(consumed=0.0, open_since=None)
            state = self.view()
            state["recovered_seconds"] = 0.0
            return state
        consumed = float(data.get("consumed_seconds") or 0.0)
        opened = _parse(data.get("open_since_utc"))
        recovered = 0.0
        if opened is not None:
            # 段主仍活着（同机 pid 可证）时，这一段属于另一个正在运行的 supervisor：
            # 不能把它当崩溃残留扣掉/清空，否则会偷走它还在用的墙钟段。
            open_pid = data.get("open_pid")
            open_host = str(data.get("open_host") or "")
            if open_host == socket.gethostname() and _pid_alive(open_pid) \
                    and int(open_pid) != os.getpid():
                raise BusinessClockError(
                    REASON_WALL_BUDGET_UNREADABLE,
                    "墙钟段仍被存活进程 %s 持有；不重复恢复" % (open_pid,))
            recovered = max(0.0, (self._now() - opened).total_seconds())
            consumed += recovered
            self._write(consumed=consumed, open_since=None)
        state = self.view()
        state["recovered_seconds"] = round(recovered, 3)
        return state

    def open(self) -> dict[str, Any]:
        data = self._ensure()
        if _parse(data.get("open_since_utc")) is not None:
            raise BusinessClockError(REASON_WALL_BUDGET_UNREADABLE,
                                     "上一段尚未 recover，不重复打开")
        consumed = float(data.get("consumed_seconds") or 0.0)
        self._write(consumed=consumed, open_since=self._now(),
                    open_pid=os.getpid(), open_host=socket.gethostname())
        return self.view()

    def close(self) -> dict[str, Any]:
        data = self._read()
        if data is None:
            return self.view()
        consumed = float(data.get("consumed_seconds") or 0.0)
        opened = _parse(data.get("open_since_utc"))
        if opened is not None:
            consumed += max(0.0, (self._now() - opened).total_seconds())
        self._write(consumed=consumed, open_since=None)
        return self.view()


class CappedRequestBudget:
    """在**每一次请求预留**的边界上强制实验硬上限（不只是 supervisor 启动前看一次）。

    它包住实例自己的 ``LifeRequestBudget``（同一个账本、同一把锁、同一条 fail-closed
    路径），在真正预留之前读实验上限，预留成功后把高水位推进。一次 wake 里发多个请求
    也逐个计费；失败的请求已经预留，不退款。
    """

    def __init__(self, inner: Any, cap: ExperimentRequestCap) -> None:
        self.inner = inner
        self.cap = cap

    def _ledger_view(self) -> dict[str, Any]:
        try:
            return dict(self.inner.view())
        except Exception as exc:  # 读不出来就是不可读，不猜 0
            return {"state": "unreadable", "detail": type(exc).__name__}

    def _refusal(self, reason_code: str, detail: str) -> Exception:
        from life_request_budget import RequestBudgetError

        return RequestBudgetError(reason_code, detail)

    def reserve(self, kind: Any = None, *, now: Any = None) -> dict[str, Any]:
        from life_request_budget import REQUEST_KIND_DECISION

        kind = REQUEST_KIND_DECISION if kind is None else kind
        # 读上限 + 预留 + 记高水位在同一把跨进程锁里：两个 writer / 一次 wake 的并发
        # 路径不能各自看到「还剩 1」然后一起花掉。崩溃留下的中间态只会让下一次检查
        # 更保守，不会被读成免费额度。
        with _cap_exclusive(self.cap.state_root):
            try:
                view = self.cap.view(self._ledger_view())
            except BusinessClockError as exc:
                raise self._refusal(exc.reason_code or REASON_EXPERIMENT_CAP_UNREADABLE,
                                    exc.detail) from exc
            if not view.get("allowed"):
                raise self._refusal(
                    str(view.get("reason_code") or REASON_EXPERIMENT_CAP_EXHAUSTED),
                    "实验上限 used=%s max=%s" % (view.get("used"),
                                                 view.get("max_new_requests")))
            record = self.inner.reserve(kind, now=now)
            # 预留即计费：成功写入账本之后才推进高水位；没有退款路径。写高水位失败时
            # 账本里的这次预留仍然被下一次 view() 的 observed 看见，不会变成免费额度。
            try:
                self.cap.note_reservation()
                self.cap.view(self._ledger_view())
            except BusinessClockError:
                pass
        return record

    def view(self) -> dict[str, Any]:
        """保留被包装账本的完整读面，并把实验上限作为具名子视图附加。

        包装器只增加一道「每次预留前」的实验硬上限，不能改变 ``LifeRequestBudget``
        的读契约：``RoundBudget.cognition_used_today``、resident 的请求总量和 cycle
        的 ``budget_column`` 读的都是 ``utc_day_calls``/``state``/``run_id`` 等账本
        字段。旧实现只回实验视图，这些字段全部缺失，上层把空账本读成「账本不可读」，
        认知预算显示 96 已用尽、每个 wake 在预留前就 BLOCKED（真实 fastday-a 的
        首个故障）。真实字段与实验字段互不冒充：账本字段留在顶层，完整实验视图放进
        ``experiment_cap``；``used``/``remaining``/``allowed``/``ledger`` 等实验读口
        继续留在顶层，兼容既有调用者。
        """

        ledger = self._ledger_view()
        try:
            experiment = dict(self.cap.view(ledger))
        except BusinessClockError as exc:
            experiment = {"state": "UNAVAILABLE", "reason_code": exc.reason_code,
                          "detail": exc.detail, "allowed": False,
                          "used": None, "remaining": None}
        merged = dict(ledger)
        for key in ("used", "remaining", "allowed", "ledger", "max_new_requests",
                    "hard_config_max"):
            if key in experiment:
                merged[key] = experiment[key]
        merged["experiment_cap"] = experiment
        return merged

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


__all__ = [
    "AUTHORITY_STALE_SECONDS",
    "BusinessClockAuthority",
    "BusinessClockClient",
    "BusinessClockError",
    "CappedRequestBudget",
    "DEFAULT_LOGICAL_SECONDS",
    "DEFAULT_RATE",
    "ExperimentRequestCap",
    "HARD_CONFIG_MAX",
    "MAX_EXPERIMENT_REQUESTS",
    "MAX_TOTAL_REQUESTS_LIMIT",
    "experiment_cap_ceiling",
    "REASON_EXPERIMENT_LEDGER_ROLLBACK",
    "REASON_WALL_BUDGET_EXHAUSTED",
    "REASON_WALL_BUDGET_UNREADABLE",
    "WALL_FILENAME",
    "WallBudget",
    "MAX_RATE",
    "MAX_RUN_REAL_SECONDS",
    "MODE_COMPRESSED_DEMO",
    "MODE_REAL_TIME",
    "REASON_CLOCK_AUTHORITY_HELD",
    "REASON_CLOCK_CORRUPT",
    "REASON_CLOCK_DOMAIN_MISMATCH",
    "REASON_CLOCK_HORIZON_REACHED",
    "REASON_CLOCK_MISSING",
    "REASON_CLOCK_RATE_INVALID",
    "REASON_CLOCK_STOPPED",
    "REASON_EXPERIMENT_CAP_EXHAUSTED",
    "REASON_EXPERIMENT_CAP_INVALID",
    "REASON_EXPERIMENT_CAP_UNREADABLE",
    "STATE_FROZEN",
    "STATE_RUNNING",
    "STATE_STOPPED",
    "clock_path",
    "create_clock",
    "domain_for",
    "experiment_path",
    "rate_for_real_seconds",
    "read_clock_file",
    "real_now",
    "validate_rate",
]

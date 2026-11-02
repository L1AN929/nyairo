#!/usr/bin/env python3
"""历史影响选择 v2（A 路线）：自然采集的**离线**只读观察控制器。

这个文件只做一件事：把「让主体在原有规则下自然选择，我们只在旁边看」写成一个可离线运行的
控制器，并把每一条硬门（请求上限、观察窗口、观察次数、历史门、可比场景门）放在它该在的位置。
它不新增业务能力、不改任何 owner/AG-1/AG-2/AR-0/LR-2/LR-4/Memory 规则、不开 World 写闸门，
也不替模型做选择或造来源。

三条不可越过的边界（本人 2026-10-29 本窗口的指令）：

* **纯读观察 ≠ 推进。** :class:`ReadOnlyObserver` 只读状态元数据（Activity 状态、reconsideration
  记录数、资格数、请求账本计数、wake 尾部），不写 Activity/Decision/reconsideration/资格/owner，
  不调用模型。它的零写由**离线测试**证明：受控 read port ＋ 少量指定业务文件前后 bytes/元数据
  比较，不做运行时全树扫描/哈希（那既慢又会把并发 owner 的正常写入误算成观察器的错）。
  实验观察计数只写本实验自己的 scratch 记录。
* **推进只走现有 runner 的原触发/due 门。** 控制器每轮调用的是生产里的 ``runner_tick``
  （``maintain`` → 事件轮询 → ``run_due``），而不是拿观察序号当 trigger 去 ``evaluate_once``。
  窗口未到就是 ``NOT_DUE``，同一状态已经考虑过就是 ``QUIET``——两条都是 0 请求，控制器只等着。
  它绝不主动 ``release_reconsideration``、不改 TTL、不写 head、不注入事项、不强制
  START/PAUSE/ABANDON/RESUME。
* **没有真实运行入口。** 本模块没有 ``prepare``/``run`` 子命令：真实执行入口是关着的，一律返回
  ``PRODUCT_RUN_NOT_AUTHORIZED``。唯一的命令是 ``offline-check``：跑新测试，并在一个本地
  ``AF_UNIX`` scratch 实例上驱动真实的 runtime/runner/reconsideration 接线，用虚拟时钟加速，
  不真等 1800 秒、不发任何网络请求、不占任何产品预算。

离线和真实的分界，写死在这里：

* 真实路径要用的时钟是 ``time.monotonic``（安全窗口）与系统墙钟（业务时间）。
  :class:`VirtualTime` **只在离线测试/演示里**替代它们；它明确不代表自然过去了 1800 秒。
* 离线演示里唯一被替换的是最底层模型 transport（回固定的合法答复）与时钟；runtime、runner、
  reconsideration store、请求账本、Supply owner、M0 读口都是真的。
* 输出的所有 JSON 只含 reason code / 枚举 / 计数 / 不透明 ref，不含 prompt、响应正文、推理过程、
  环境变量或密钥。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unittest
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "native"),
               str(REPO_ROOT / "components" / "native" / "memory_runtime_v1"),
               str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

#: v1（预登记 harness）只**读**复用：不修改它的任何常量、不碰它的落盘格式、不重跑它的 20 对。
import history_choice_experiment as baseline  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2

# ---------------------------------------------------------------------------------
# 结束原因（写进记录与输出，全部是 reason code，不是结论）
# ---------------------------------------------------------------------------------

PRODUCT_RUN_NOT_AUTHORIZED = "PRODUCT_RUN_NOT_AUTHORIZED"
STOP_REQUEST_CAP = "ACQUISITION_REQUEST_CAP"
STOP_OBSERVATION_LIMIT = "OBSERVATION_LIMIT"
STOP_WINDOW_ELAPSED = "OBSERVATION_WINDOW_ELAPSED"
STOP_H_AND_SCENARIO_FROZEN = "H_AND_SCENARIO_FROZEN"
STOP_PROVIDER_FAILURE = "PROVIDER_OR_TRANSPORT_FAILURE"
STOP_OBSERVER_WROTE = "OBSERVER_WRITE_DETECTED"
STOP_LEDGER_UNREADABLE = "REQUEST_LEDGER_UNREADABLE"
STOP_DEADLINE_REACHED = "OBSERVATION_DEADLINE_REACHED"
STOP_BATCH_ALREADY_STARTED = "BATCH_ALREADY_STARTED"
STOP_UNEXPECTED = "UNEXPECTED_ERROR"

RESULT_FROZEN = "H_AND_SCENARIO_FROZEN"
RESULT_NO_H = "NO_EFFECTIVE_HISTORY"
RESULT_NOT_IDENTIFIABLE = "SCENARIO_NOT_IDENTIFIABLE"
RESULT_ABORTED = "BATCH_ABORTED"

#: 这些结束不是「观察完了」，而是批次被中止：不许写成 NO_EFFECTIVE_HISTORY 之类的结论。
ABORT_STOPS = frozenset({STOP_PROVIDER_FAILURE, STOP_OBSERVER_WROTE,
                         STOP_LEDGER_UNREADABLE, STOP_DEADLINE_REACHED,
                         STOP_UNEXPECTED, STOP_BATCH_ALREADY_STARTED})

#: 开始就说明白的事实：两件中性作品没有已证独立自然 head 更新源（不保证窗口内建起历史）。
NATURAL_SOURCE_NOTE = (
    "两件中性练习作品由 operator 在准备时创建一次；仓库里没有找到已证的独立自然写入者会"
    "在观察窗口内单独更新它们的 head。主体自己的合法动作（START/内容/CONTINUE 等）仍可能"
    "产生真实新版本，但那要她先选择；本控制器不补源、不注入 head/事项/资格，也不去旧实例里找。")


@dataclass(frozen=True)
class ExperimentPlan:
    """一次 v2 批次的身份与参数。参数不是授权：没有真实 running 入口，52 只是技术上限。"""

    run_name: str = "histchoice-natural-v2"
    run_id: str = "run:histchoice-natural-v2"
    domain_id: str = "histchoice:natural-v2"
    #: 本批采集的**发送前**硬上限：决定 + 内容 + 失败预留都算，写在同一本持久账本里。
    request_cap: int = 8
    #: 观察窗口（真实秒）与观察间隔：窗口用 monotonic，业务时间不快进。
    window_seconds: int = 1800
    observation_interval_seconds: int = 60
    max_observations: int = 31
    #: 未来候选总额 8+40+4＋诊断 0；每臂 2。都只是代码里的技术上限，不构成任何新授权。
    future_candidate_total: int = 52
    future_shadow_requests: int = 40
    future_continuation_requests: int = 4
    future_diagnostic_requests: int = 0
    arm_request_cap: int = 2
    utc_day_cap: int = 300
    #: 真实根：v2 本单**不准备也不运行**任何真实根。
    real_root: Optional[str] = None
    experiment_dirname: str = "experiment-natural-v2"

    def acquisition_parts(self) -> dict[str, int]:
        return {"request_cap": self.request_cap, "window_seconds": self.window_seconds,
                "observation_interval_seconds": self.observation_interval_seconds,
                "max_observations": self.max_observations}

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_name": self.run_name, "run_id": self.run_id, "domain_id": self.domain_id,
            **self.acquisition_parts(),
            "future_candidate_total": self.future_candidate_total,
            "future_shadow_requests": self.future_shadow_requests,
            "future_continuation_requests": self.future_continuation_requests,
            "future_diagnostic_requests": self.future_diagnostic_requests,
            "arm_request_cap": self.arm_request_cap,
            "utc_day_cap": self.utc_day_cap,
            "real_root": self.real_root,
            "real_run_entry": PRODUCT_RUN_NOT_AUTHORIZED,
            "natural_source_note": NATURAL_SOURCE_NOTE,
        }


DEFAULT_PLAN = ExperimentPlan()


# ---------------------------------------------------------------------------------
# 时间：真实路径用 monotonic + 墙钟；VirtualTime 只在离线
# ---------------------------------------------------------------------------------

class VirtualTime:
    """离线替身时钟（测试/演示专用）。**不是**自然经过的时间，不得当真实窗口证据。"""

    def __init__(self, *, start: Optional[datetime] = None) -> None:
        self.seconds = 0.0
        self._base = start or datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.seconds

    def sleep(self, seconds: float) -> None:
        step = max(0.0, float(seconds))
        self.sleeps.append(step)
        self.seconds += step

    def now(self) -> datetime:
        return self._base + timedelta(seconds=self.seconds)

    def now_iso(self) -> str:
        return self.now().isoformat(timespec="milliseconds")

    @property
    def virtual(self) -> bool:
        return True


# ---------------------------------------------------------------------------------
# 实验自己的 scratch 记录（只写计数/枚举/ref；不写正文）
# ---------------------------------------------------------------------------------

_SAFE_ROW_KEYS = (
    "kind", "at", "index", "elapsed_s", "snapshot", "advance", "requests",
    "advance_skipped", "h_ref", "h_gate", "note",
)


class ExperimentRecord:
    """观察记录：追加式 JSONL，落在本实验自己的 scratch 目录里，绝不动业务状态。"""

    def __init__(self, directory: Path | str, *, plan: ExperimentPlan = DEFAULT_PLAN) -> None:
        self.directory = Path(directory)
        self.plan = plan
        self.path = self.directory / "observations.jsonl"
        self.batch_path = self.directory / "batch.json"

    # -- 批次 -------------------------------------------------------------

    def begin_batch(self, *, note: str = "") -> dict[str, Any]:
        """本实验的批次只允许开始一次；不开新批次来刷新观察计数或预算。"""

        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        if self.batch_path.exists():
            previous = _read_json(self.batch_path)
            return {"ok": False, "reason_code": STOP_BATCH_ALREADY_STARTED,
                    "started_at": previous.get("started_at")}
        payload = {"plan": self.plan.as_dict(), "started_at": _iso(), "note": note,
                   "virtual_clock": True}
        _write_json(self.batch_path, payload)
        return {"ok": True, "started_at": payload["started_at"]}

    # -- 行 ---------------------------------------------------------------

    def note(self, kind: str, **fields: Any) -> dict[str, Any]:
        row: dict[str, Any] = {"kind": str(kind), "at": _iso()}
        for key in _SAFE_ROW_KEYS:
            if key in fields:
                row[key] = fields[key]
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return row

    def rows(self) -> list[dict[str, Any]]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
        return out


def _is_virtual(clock: Any) -> bool:
    """这一轮用的是不是离线虚拟时钟（真实路径必须是 ``time.monotonic``）。"""

    if getattr(clock, "virtual", False):
        return True
    owner = getattr(clock, "__self__", None)
    return bool(getattr(owner, "virtual", False))


def _iso(moment: Optional[datetime] = None) -> str:    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="milliseconds")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------------
# 纯读观察器
# ---------------------------------------------------------------------------------

class ObserverWroteError(RuntimeError):
    """观察路径上发现「自己去写业务状态」时用的具名失败。

    生产观察器本身不做全树自证（见 :class:`ReadOnlyObserver`）；这个异常留给显式的
    自检/测试注入路径，控制器收到就整批中止，不掩盖。
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(STOP_OBSERVER_WROTE)
        self.reason_code = STOP_OBSERVER_WROTE
        self.detail = detail


class ReadOnlyObserver:
    """纯读观察器：只读状态元数据，零写、零模型。

    它拿到的每个端口都是只读的（``ActivityReadService`` 自述 "Possesses ZERO write capability"；
    reconsideration/账本/wake 日志都是普通 JSON 文件的直接读，不取账本锁、不建目录、不写任何状态）。
    ``snapshot()`` 不再做运行时全树扫描/哈希：那既有性能代价，也会把并发的正常 owner 写入
    误算成观察器的错。零写这一条由离线测试用受控 read port ＋ 少量指定文件的前后比较来证明。
    """

    def __init__(self, *, state_root: Path | str, run_id: str,
                 activity_read: Any = None, wake_log: Optional[Path | str] = None) -> None:
        self.state_root = Path(state_root)
        self.run_id = str(run_id)
        self.activity_read = activity_read
        self.wake_log = Path(wake_log) if wake_log else None
        self.observations = 0

    # -- 纯读片段 ---------------------------------------------------------

    def _activities(self) -> dict[str, Any]:
        if self.activity_read is None:
            return {"available": False, "reason_code": "ACTIVITY_READ_NOT_WIRED"}
        try:
            rows = self.activity_read.list_open_activities()
        except Exception as exc:  # 读失败如实报告，不当成「没有活动」
            return {"available": False,
                    "reason_code": "%s: %s" % (type(exc).__name__, str(exc)[:80])}
        items = []
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            items.append({"activity_ref": str(row.get("activity_id") or ""),
                          "status": str(row.get("status") or ""),
                          "revision": int(row.get("revision") or 0)})
        items.sort(key=lambda item: item["activity_ref"])
        return {"available": True, "count": len(items), "activities": items[:6]}

    def _reconsideration(self) -> dict[str, Any]:
        try:
            from life_step_readiness import ReconsiderationLedger
        except Exception as exc:
            return {"available": False, "reason_code": "IMPORT_FAILED: %s" % type(exc).__name__}
        store = ReconsiderationLedger(self.state_root)
        try:
            entries = store.entries()
            situations = store.situations()
            live = store.live_entries()
            live_situations = [row for row in situations
                               if not store.expired(row)]
        except Exception as exc:
            return {"available": False,
                    "reason_code": "%s: %s" % (type(exc).__name__, str(exc)[:80])}
        # 真正决定「同一状态不再问」的是 situations（整情境指纹），entries 是按键的旧记录。
        return {"available": True, "entries": len(entries), "live_entries": len(live),
                "situations": len(situations), "live_situations": len(live_situations),
                "ttl_seconds": int(store.ttl_seconds)}

    def _eligibilities(self) -> dict[str, Any]:
        """资格计数：直接读 owner 自己的状态文件，不通过会取锁/建目录的 store。

        观察只需要一个计数字段；用 store 的读口会顺手创建锁文件，那本身就是一次写。
        """

        path = self.state_root / "waiting_agenda" / "eligibility" / "resume_eligibility_state.json"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"available": True, "total": 0, "open": 0, "open_refs": [],
                    "reason_code": "STATE_ABSENT"}
        except OSError as exc:
            return {"available": False, "reason_code": "UNREADABLE: %s" % type(exc).__name__}
        try:
            state = json.loads(text)
        except ValueError:
            return {"available": False, "reason_code": "ELIGIBILITY_STATE_CORRUPT"}
        rows = (state or {}).get("eligibilities") or {}
        open_refs = sorted(str(key) for key, row in rows.items()
                           if isinstance(row, Mapping) and str(row.get("status")) == "OPEN")
        return {"available": True, "total": len(rows), "open": len(open_refs),
                "open_refs": open_refs[:6]}

    def _requests(self) -> dict[str, Any]:
        """请求账本的持久计数：直接读 JSON，不取锁、不创建锁文件。"""

        try:
            import life_request_budget as budget_module
            path = budget_module.budget_path(self.state_root)
        except Exception as exc:
            return {"available": False, "reason_code": "IMPORT_FAILED: %s" % type(exc).__name__}
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"available": False, "reason_code": "BUDGET_FILE_ABSENT"}
        except OSError as exc:
            return {"available": False, "reason_code": "UNREADABLE: %s" % type(exc).__name__}
        try:
            data = json.loads(text)
        except ValueError:
            return {"available": False, "reason_code": "REQUEST_BUDGET_STATE_CORRUPT"}
        windows = data.get("windows") or {}
        window = windows.get(self.run_id) or {}
        days = data.get("days") or {}
        active = str(data.get("active_day") or "")
        return {"available": True,
                "run_window_calls": int(window.get("calls") or 0),
                "run_window_present": bool(window),
                "utc_day": active or None,
                "utc_day_calls": int(days.get(active) or 0) if active else 0}

    def _wakes(self) -> dict[str, Any]:
        path = self.wake_log or (self.state_root / "autonomous_wakes.jsonl")
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"available": False, "reason_code": "WAKE_LOG_ABSENT", "lines": 0}
        except OSError as exc:
            return {"available": False, "reason_code": "UNREADABLE: %s" % type(exc).__name__,
                    "lines": 0}
        lines = [line for line in text.splitlines() if line.strip()]
        last: dict[str, Any] = {}
        if lines:
            try:
                row = json.loads(lines[-1])
                if isinstance(row, Mapping):
                    last = {"kind": str(row.get("kind") or ""),
                            "status": str(row.get("status") or ""),
                            "llm_calls": int(row.get("llm_calls") or 0)}
            except ValueError:
                last = {}
        return {"available": True, "lines": len(lines), "last": last}

    # -- 快照 -------------------------------------------------------------

    def snapshot(self, *, index: Optional[int] = None) -> dict[str, Any]:
        """一次纯读快照。只读白名单字段，不扫描目录、不算哈希、不写任何文件。"""

        self.observations += 1
        return {
            "index": index,
            "activities": self._activities(),
            "reconsideration": self._reconsideration(),
            "eligibilities": self._eligibilities(),
            "requests": self._requests(),
            "wakes": self._wakes(),
        }


# ---------------------------------------------------------------------------------
# 发送前门：阶段额度（与持久账本 ceiling 叠在一起，min 语义）
# ---------------------------------------------------------------------------------

class PlanGateLedger:
    """``baseline.HistoryGateCognitionAdapter`` 需要的那一个方法，按 v2 计划实现。

    它只回答「这一阶段还能不能发」；真正不可绕过的那道门是 ``LifeRequestBudget.reserve()``，
    这里多一道是为了在发送前就能给出具名 reason code，而不是等到 reserve 抛错。
    """

    def __init__(self, plan: ExperimentPlan = DEFAULT_PLAN) -> None:
        self.plan = plan

    @staticmethod
    def _used(view: Mapping[str, Any]) -> Optional[int]:
        calls = (view or {}).get("run_window_calls")
        return None if calls is None else int(calls)

    def assert_room(self, stage: str, view: Mapping[str, Any], *,
                    reserving: int = 0) -> dict[str, Any]:
        used = self._used(view)
        cap = int(self.plan.request_cap)
        if used is None:
            raise baseline.BatchRefused(STOP_LEDGER_UNREADABLE,
                                        "run window counters are unreadable")
        remaining = max(0, cap - used - int(reserving))
        if remaining <= 0:
            raise baseline.BatchRefused(STOP_REQUEST_CAP,
                                        "acquisition spent %d of %d requests"
                                        % (used, cap))
        return {"stage": stage, "cap": cap, "used": used, "stage_remaining": remaining}


def budget_ceiling(plan: ExperimentPlan = DEFAULT_PLAN) -> int:
    """采集批次的持久账本 ceiling：从「本批起点已用」再往上加 ``request_cap``。"""

    return int(plan.request_cap)


# ---------------------------------------------------------------------------------
# H（有效历史）与结构门
# ---------------------------------------------------------------------------------

class HistoryDetector:
    """H 的机械规则：首个 owner 确认 ``version_created=True``、且其对应正式 M0 经历可读的作品。

    不挑「看起来更有影响」的那一条：只看本轮真实记录（runner 转发过来的 owner 证据）＋
    本轮 Decision 自己选中的候选的 artifact 目标 ＋ 正式 M0 读口的可读性。
    """

    RULE = ("first owner-confirmed version_created=True whose artifact target has a "
            "readable formal M0 experience")

    def __init__(self, *, candidate_read: Any = None, memory_port: Any = None) -> None:
        self.candidate_read = candidate_read
        self.memory_port = memory_port

    def _artifact_targets(self, candidate_ref: Optional[str]) -> list[str]:
        if not candidate_ref or self.candidate_read is None:
            return []
        try:
            row = self.candidate_read.get_candidate(str(candidate_ref)) or {}
        except Exception:
            return []
        targets = []
        for ref in row.get("target_refs") or ():
            text = str(ref)
            if text.startswith("artifact:"):
                targets.append(text)
        return targets

    def candidate(self, advance: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        extra = advance.get("owner") or {}
        if extra.get("owner_version_created") is not True:
            return None
        selected = advance.get("selected_candidate_ref")
        for target in self._artifact_targets(selected):
            evidence = baseline.m0_has_artifact(self.memory_port, target)
            if evidence.get("readable"):
                return {"artifact_ref": target,
                        "owner_version_id": extra.get("owner_version_id"),
                        "experience_id": evidence.get("experience_id"),
                        "selected_candidate_ref": selected}
        return None


def freeze_and_check(bundle: Mapping[str, Any], *, h_ref: str,
                     work_refs: Sequence[str], attempts: int = 4,
                     sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """冻结一次当前状态，并跑**正式** AG-1 结构门（离线替身只回一条合法 NO_ACTION，0 网络 0 预算）。

    H 的对应经历必须真的出现在冻结历史里；否则这次冻结不作数（``HISTORY_NOT_ADMITTED``）。
    """

    scenario = baseline.freeze_scenario(bundle, h_ref=h_ref)
    bare = baseline._bare_artifact(h_ref)

    def _in_history(view: Mapping[str, Any]) -> bool:
        return any(bare and bare in str(item.get("artifact_ref") or "")
                   for item in view.get("prior_experiences") or ())

    for _attempt in range(max(1, int(attempts))):
        if _in_history(scenario):
            break
        sleep(0.05)
        scenario["prior_experiences"] = list(bundle["cycle"].consideration_context() or [])
    if not _in_history(scenario):
        return {"ok": False, "reason_code": baseline.REASON_HISTORY_NOT_ADMITTED,
                "detail": "the H experience is not in the frozen history",
                "frozen": None, "gate": None}

    gate = structural_gate(bundle, scenario, work_refs=work_refs)
    return {"ok": bool(gate.get("ok")), "reason_code": gate.get("reason_code") or "",
            "detail": gate.get("detail") or "", "frozen": scenario, "gate": gate}


def structural_gate(bundle: Mapping[str, Any], frozen: Mapping[str, Any], *,
                    work_refs: Sequence[str], transport: Any = None) -> dict[str, Any]:
    """正式结构门：H + 另一件作品各自可执行，且 NO_ACTION 合法。

    请求由**正式** AG-1 adapter 构造并校验，底层 transport 默认是 v1 的离线替身
    （回一条合法 NO_ACTION）：不联网、不占持久预算、只写实验自己的 shadow store。
    """

    recorded: dict[str, Any] = {}
    offline_transport = transport is None
    if offline_transport:
        transport = baseline._offline_no_action_transport()

    class _Recorder:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        @property
        def send_split_capable(self) -> bool:
            return bool(getattr(self.inner, "send_split_capable", False))

        def build_prompt(self, request: Any) -> tuple[str, dict[str, Any]]:
            return self.inner.build_prompt(request)

        def invoke(self, request: Any, *, schema_retry_index: int = 0) -> Any:
            text, payload = self.inner.build_prompt(request)
            recorded["request"] = request
            recorded["payload"] = payload
            recorded["text"] = text
            return self.inner.invoke(request, schema_retry_index=schema_retry_index)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    env = dict(bundle["assembled_env"])
    adapter = _Recorder(baseline.raw_cognition_adapter(env, transport=transport,
                                                       admission=bundle["admission"]))
    shadow_root = Path(str(bundle["root"])) / "experiment" / "capture-state"
    service = baseline._make_shadow_service(bundle["runtime"], shadow_root, adapter)
    counter = int(bundle.get("capture_index", 0) or 0)
    bundle["capture_index"] = counter + 1
    opportunity = baseline.new_opportunity(bundle["runtime"], frozen,
                                          trigger_ref="exp:capture:%02d" % counter)
    try:
        result = service.evaluate_opportunity(
            opportunity=opportunity, **baseline.shadow_kwargs(frozen, frozen["prior_experiences"]))
    except Exception as exc:
        return {"ok": False, "reason_code": baseline.REASON_SCENARIO_NOT_IDENTIFIABLE,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    request = recorded.get("request")
    payload = recorded.get("payload")
    if request is None or payload is None:
        return {"ok": False, "reason_code": baseline.REASON_SCENARIO_NOT_IDENTIFIABLE,
                "detail": "no cognition request was built (rule-resolved path)",
                "result_status": (result or {}).get("status"),
                "failure_class": (result or {}).get("failure_class")}
    candidates = list(getattr(request, "candidates", ()) or ())
    legal_enums = [str(item) for item in (getattr(request, "legal_decision_enums", ()) or ())]
    executable_kinds = {"START", "CONTINUE", "RESUME"}
    h_bare = baseline._bare_artifact(frozen["h_ref"])
    h_candidates = [row for row in candidates
                    if any(baseline._bare_artifact(ref) == h_bare
                           for ref in (row.get("target_refs") or ()))]
    executable = [row for row in candidates
                  if executable_kinds & set(row.get("legal_decisions") or ())]
    h_executable = [row for row in h_candidates
                    if executable_kinds & set(row.get("legal_decisions") or ())]
    other_works = {baseline._bare_artifact(ref) for ref in work_refs}
    other_works.discard(h_bare)
    other_work_executable = [
        row for row in executable
        if any(baseline._bare_artifact(ref) in other_works
               for ref in (row.get("target_refs") or ()))]
    ok = bool(h_executable) and bool(other_work_executable) and "NO_ACTION" in legal_enums
    reason = ""
    if not ok:
        reason = (baseline.REASON_SECOND_WORK_MISSING
                  if h_executable and "NO_ACTION" in legal_enums
                  else baseline.REASON_SCENARIO_NOT_IDENTIFIABLE)
    return {
        "ok": ok, "reason_code": reason,
        "offline_transport": bool(offline_transport),
        "candidate_count": len(candidates), "executable_count": len(executable),
        "h_executable": bool(h_executable), "h_candidate_count": len(h_candidates),
        "other_work_executable": bool(other_work_executable),
        "other_work_count": len(other_works),
        "no_action_legal": "NO_ACTION" in legal_enums,
        "legal_decision_enums": sorted(set(legal_enums)),
        "candidate_ids": [str(row.get("candidate_id") or "") for row in candidates],
        "candidate_targets": [
            {"candidate_id": str(row.get("candidate_id") or ""),
             "targets": [str(ref)[:60] for ref in (row.get("target_refs") or ())],
             "legal": sorted(str(item) for item in (row.get("legal_decisions") or ()))}
            for row in candidates],
        "baseline": {"fingerprint": baseline.prompt_identity(payload),
                     "basis": baseline.identity_basis(payload)},
        "capture_decision": getattr(result.get("decision_record"), "decision", None),
    }


# ---------------------------------------------------------------------------------
# 观察控制器
# ---------------------------------------------------------------------------------

_ADVANCE_KEYS = ("kind", "reason", "status", "status_class", "trigger_ref", "llm_calls",
                 "outcome", "decision", "failure_class", "detail_code",
                 "selected_candidate_ref", "events", "waiting_state", "feed_errors")
_OWNER_KEYS = ("owner_version_created", "owner_version_id", "step_path")


def advance_record(raw: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """把一次推进报告归一成白名单字段（枚举/计数/ref），顺手摘出 owner 证据。"""

    if not isinstance(raw, Mapping):
        return {"kind": "NONE", "status": "UNKNOWN", "llm_calls": 0, "owner": {}}
    out: dict[str, Any] = {}
    for key in _ADVANCE_KEYS:
        if key in raw:
            out[key] = raw[key]
    detail = str(raw.get("detail") or "")
    out["detail_code"] = detail.split(":", 1)[0][:64]
    extra = raw.get("extra") if isinstance(raw.get("extra"), Mapping) else {}
    source = dict(extra) if extra else {}
    if "owner_version_created" not in source and "owner_version_created" in raw:
        source["owner_version_created"] = raw.get("owner_version_created")
    if "owner_version_id" not in source and "owner_version_id" in raw:
        source["owner_version_id"] = raw.get("owner_version_id")
    if "selected_candidate_ref" not in out and source.get("selected_candidate_ref"):
        out["selected_candidate_ref"] = source.get("selected_candidate_ref")
    out["owner"] = {key: source.get(key) for key in _OWNER_KEYS}
    out["llm_calls"] = int(out.get("llm_calls") or 0)
    return out


def _latch_stop(reason: Any) -> str:
    """把发送锁自己记的原因翻成 v2 的结束码；未知的一律按中止处理，不当成结论。"""

    text = str(reason or "")
    if text == baseline.REASON_DEADLINE_PASSED:
        return STOP_DEADLINE_REACHED
    if text == baseline.REASON_PROVIDER_FAILURE:
        return STOP_PROVIDER_FAILURE
    return STOP_PROVIDER_FAILURE if text else STOP_UNEXPECTED


class NaturalObservationController:
    """A 路线采集：纯读观察 + 现有 runner 的自然推进，直到某个具名边界。

    顺序固定，每一步都只有一种解释：

    0. 循环头先核门（截止／观察次数／请求 cap／monotonic 窗口）：已经越界就**不观察、不推进**。
       恰好落在窗口终点只允许最后一次纯读，不再推进。
    1. ``observe()``：纯读快照。它**不**决定要不要问模型。
    2. ``advance()`` 前再核一次窗口／截止／账本：观察本身跨过窗口就不推进，本轮记
       ``NOT_ADVANCED``。推进是生产 runner 的一次 tick（``maintain`` + 原 due 门 + 原
       reconsideration 门）：未到期是 ``NOT_DUE``、同状态是 ``QUIET``，两者都是 0 请求。
    3. 记一行观察；然后按固定顺序检查边界：中止 → 请求上限 → H/结构门 → 截止 → 观察次数 → 窗口。
    4. 没到边界就等到下一个 60 秒格点；错过就向前对齐到真实时间之后的格点，**不追赶**。
    """

    def __init__(self, *, plan: ExperimentPlan, observer: ReadOnlyObserver,
                 advance: Callable[[], Mapping[str, Any]], budget: Any,
                 record: ExperimentRecord, h_detector: Optional[HistoryDetector] = None,
                 freeze: Optional[Callable[[str], Mapping[str, Any]]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 deadline: Optional[Callable[[], bool]] = None,
                 latch: Any = None) -> None:
        self.plan = plan
        self.observer = observer
        self.advance = advance
        self.budget = budget
        self.record = record
        self.h_detector = h_detector
        self.freeze = freeze
        self.clock = clock
        self.sleep = sleep
        self.deadline = deadline
        #: 两个发送口共用的「立刻停发」锁（``SendLatch``）；它先于报告知道失败。
        self.latch = latch
        self.previous_window_max = getattr(budget, "run_window_max", None)

    # -- 账本 -------------------------------------------------------------

    def _requests(self) -> dict[str, Any]:
        view = self.budget.view()
        return {"run_window_calls": view.get("run_window_calls"),
                "run_window_max": view.get("run_window_max"),
                "utc_day_calls": view.get("utc_day_calls"),
                "state": view.get("state")}

    def _used(self) -> Optional[int]:
        try:
            calls = self.budget.view().get("run_window_calls")
        except Exception:
            return None
        return None if calls is None else int(calls)

    def _apply_ceiling(self) -> dict[str, Any]:
        """把本批 ceiling 落到持久账本对象上：从此刻已用再往上加 ``request_cap``。

        与实例自己的 ``run_window_max`` 取 min，绝不放宽外层；
        ``reserve()`` 是发送前的最后一道门，所以第 9 次发送根本到不了 transport。
        """

        base = self.previous_window_max
        cap = int(self.plan.request_cap)
        ceiling = cap if base is None else min(int(base), cap)
        self.budget.run_window_max = max(0, ceiling)
        return {"ceiling": self.budget.run_window_max, "previous": base}

    def _restore_ceiling(self) -> None:
        self.budget.run_window_max = self.previous_window_max

    # -- 主循环 -----------------------------------------------------------

    def run(self) -> dict[str, Any]:
        started = self.clock()
        applied = self._apply_ceiling()
        observations: list[dict[str, Any]] = []
        window = float(self.plan.window_seconds)
        interval = float(self.plan.observation_interval_seconds)
        max_observations = int(self.plan.max_observations)
        cap = int(self.plan.request_cap)
        stop_reason = ""
        h_ref: Optional[str] = None
        h_detail: dict[str, Any] = {}
        frozen: Optional[Mapping[str, Any]] = None
        gate: Optional[Mapping[str, Any]] = None
        try:
            while True:
                # ---- 循环头：先核门，再决定要不要观察 ----
                # 睡眠过冲、重开时已满账本、上一次观察耗时很长，都可能让本轮其实已经越界；
                # 这些情况一律不再观察、更不再推进。
                if self.deadline is not None and self.deadline():
                    stop_reason = STOP_DEADLINE_REACHED
                    break
                if len(observations) >= max_observations:
                    stop_reason = STOP_OBSERVATION_LIMIT
                    break
                used = self._used()
                if used is None:
                    stop_reason = STOP_LEDGER_UNREADABLE
                    break
                if used >= cap:
                    stop_reason = STOP_REQUEST_CAP
                    break
                elapsed = self.clock() - started
                if elapsed > window:
                    stop_reason = STOP_WINDOW_ELAPSED
                    break
                # 恰好落在窗口终点：方案允许最后一次纯读，但不推进。
                final_read_only = elapsed >= window
                index = len(observations) + 1
                try:
                    snapshot = self.observer.snapshot(index=index)
                except ObserverWroteError as exc:
                    self.record.note("observer_write", index=index, note=str(exc.detail)[:200])
                    stop_reason = STOP_OBSERVER_WROTE
                    break
                # ---- advance 前再核一次：观察本身可能跨过窗口/截止或花掉额度 ----
                skipped = final_read_only
                skip_reason = STOP_WINDOW_ELAPSED if skipped else ""
                if not skipped and self.deadline is not None and self.deadline():
                    skipped, skip_reason = True, STOP_DEADLINE_REACHED
                if not skipped:
                    used = self._used()
                    if used is None:
                        skipped, skip_reason = True, STOP_LEDGER_UNREADABLE
                    elif used >= cap:
                        skipped, skip_reason = True, STOP_REQUEST_CAP
                    elif self.clock() - started >= window:
                        skipped, skip_reason = True, STOP_WINDOW_ELAPSED
                if skipped:
                    advance = advance_record(None)
                    advance["kind"] = "NOT_ADVANCED"
                    advance["status"] = "NOT_ADVANCED"
                else:
                    advance = advance_record(self.advance())
                requests = self._requests()
                row = {"index": index, "elapsed_s": round(elapsed, 3), "snapshot": snapshot,
                       "advance": advance, "requests": requests}
                if skip_reason:
                    row["advance_skipped"] = skip_reason
                observations.append(row)

                used = requests.get("run_window_calls")
                h_candidate = None
                if self.h_detector is not None:
                    h_candidate = self.h_detector.candidate(advance)
                if h_candidate and h_ref is None:
                    h_ref = str(h_candidate["artifact_ref"])
                    h_detail = dict(h_candidate)
                if h_ref is not None and frozen is None and self.freeze is not None:
                    attempt = self.freeze(h_ref) or {}
                    if attempt.get("ok") and attempt.get("frozen") is not None:
                        frozen = attempt["frozen"]
                        gate = attempt.get("gate")
                        row["h_gate"] = {"ok": True, "reason_code": ""}
                    else:
                        row["h_gate"] = {"ok": False,
                                         "reason_code": attempt.get("reason_code") or
                                         baseline.REASON_SCENARIO_NOT_IDENTIFIABLE}
                if h_ref is not None:
                    row["h_ref"] = h_ref
                # 顺序固定：中止 → 请求上限 → 冻结 → 截止 → 观察次数 → 窗口。
                if self.latch is not None and getattr(self.latch, "closed", False):
                    stop_reason = _latch_stop(getattr(self.latch, "stop_reason", ""))
                elif skipped and skip_reason:
                    stop_reason = skip_reason
                elif not skipped and self._provider_failed(advance):
                    stop_reason = STOP_PROVIDER_FAILURE
                elif used is None:
                    stop_reason = STOP_LEDGER_UNREADABLE
                elif int(used) >= cap:
                    stop_reason = STOP_REQUEST_CAP
                elif frozen is not None:
                    stop_reason = STOP_H_AND_SCENARIO_FROZEN
                elif self.deadline is not None and self.deadline():
                    stop_reason = STOP_DEADLINE_REACHED
                elif len(observations) >= max_observations:
                    stop_reason = STOP_OBSERVATION_LIMIT
                elif self.clock() - started >= window:
                    stop_reason = STOP_WINDOW_ELAPSED
                self.record.note("observation", **row)
                if stop_reason:
                    break
                # ---- 下一个观察点：只向前对齐到真实时间之后的 60 秒格点 ----
                # 不追赶因为一次观察太久而错过的格点，否则会变成连续密集观察/推进。
                next_at = started + len(observations) * interval
                now = self.clock()
                if next_at <= now:
                    missed = int((now - next_at) // interval) + 1
                    next_at += missed * interval
                if next_at - started > window:
                    stop_reason = STOP_WINDOW_ELAPSED
                    break
                self.sleep(next_at - now)
        except Exception as exc:
            self.record.note("error", note="%s: %s" % (type(exc).__name__, str(exc)[:160]))
            stop_reason = STOP_UNEXPECTED
        finally:
            self._restore_ceiling()

        outcome = self._outcome(stop_reason, h_ref=h_ref, frozen=frozen)
        summary = {
            "stop_reason": stop_reason or STOP_UNEXPECTED,
            "result": outcome,
            "observations": len(observations),
            "window_elapsed_s": round(self.clock() - started, 3),
            "requests_used": self._used(),
            "request_cap": self.plan.request_cap,
            "ceiling": applied,
            "h_ref": h_ref,
            "h_detail": h_detail,
            "frozen": ({"observed_at": (frozen or {}).get("observed_at"),
                        "candidate_count": len((frozen or {}).get("candidate_refs") or []),
                        "prior_experience_count": len((frozen or {}).get("prior_experiences") or [])}
                       if frozen is not None else None),
            "gate": ({key: gate.get(key) for key in (
                "ok", "reason_code", "candidate_count", "executable_count",
                "h_executable", "other_work_executable", "no_action_legal",
                "legal_decision_enums", "candidate_targets", "offline_transport",
                "capture_decision") if gate is not None and key in gate}
                     if gate is not None else None),
            "timeline": [self._timeline_row(row) for row in observations],
            "virtual_clock": _is_virtual(self.clock),
        }
        self.record.note("summary", note=json.dumps(
            {key: summary[key] for key in ("stop_reason", "result", "observations",
                                           "requests_used")}, ensure_ascii=False))
        return summary

    @staticmethod
    def _timeline_row(row: Mapping[str, Any]) -> dict[str, Any]:
        advance = row.get("advance") or {}
        snapshot = row.get("snapshot") or {}
        requests = row.get("requests") or {}
        activities = snapshot.get("activities") or {}
        return {
            "index": row.get("index"),
            "elapsed_s": row.get("elapsed_s"),
            "status": advance.get("status"),
            "kind": advance.get("kind"),
            "trigger_ref": advance.get("trigger_ref"),
            "llm_calls": advance.get("llm_calls"),
            "events": len(advance.get("events") or []),
            "waiting_state": advance.get("waiting_state"),
            "open_activities": activities.get("count"),
            "open_eligibilities": (snapshot.get("eligibilities") or {}).get("open"),
            "reconsideration_situations": (snapshot.get("reconsideration") or {}).get(
                "live_situations"),
            "requests": requests.get("run_window_calls"),
            "advance_skipped": row.get("advance_skipped"),
            "h_ref": row.get("h_ref"),
            "h_gate": row.get("h_gate"),
        }

    @staticmethod
    def _provider_failed(advance: Mapping[str, Any]) -> bool:
        failure = advance.get("failure_class")
        if baseline.classify_failure(failure) == baseline.CLASS_FAILURE:
            return True
        return str(advance.get("outcome") or "") == "CALL_FAILURE"

    def _outcome(self, stop_reason: str, *, h_ref: Optional[str],
                 frozen: Optional[Mapping[str, Any]]) -> str:
        if frozen is not None:
            return RESULT_FROZEN
        if stop_reason in ABORT_STOPS:
            return RESULT_ABORTED
        return RESULT_NOT_IDENTIFIABLE if h_ref else RESULT_NO_H


# ---------------------------------------------------------------------------------
# 离线 scratch 实例（仅 offline-check 使用；不 prepare 任何真实根）
# ---------------------------------------------------------------------------------

def prepare_scratch_instance(root: Path | str, *, plan: ExperimentPlan = DEFAULT_PLAN,
                             with_memory: bool = True,
                             subject: str = "chiyo") -> dict[str, Any]:
    """在一个**全新**临时目录里建一次隔离实例：两件中性作品 + 真实 owner 绑定 + 独立 Memory。

    身份用 v2 自己的 ``run_id``/operation key；不复用 v1 的 ``prepare``（它把 v1 的 run 身份与
    预登记文件名写死）。0 产品调用、0 网络。
    """

    root_path = Path(str(root))
    if root_path.exists():
        return {"ok": False, "reason_code": "ROOT_NOT_FRESH", "root": str(root_path)}
    from components.phone import demo, life as phone_life, service as phone_service

    try:
        prepared = demo.prepare_demo(
            root_path, with_world=True, seed_work_artifact=False,
            reading_title="", reading_content=None, run_id=plan.run_id,
            request_utc_day_max=int(plan.utc_day_cap),
            request_run_window_max=int(plan.request_cap),
            max_cognition_calls_per_day=int(plan.utc_day_cap))
    except Exception as exc:
        return {"ok": False, "reason_code": "PREPARE_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    if not isinstance(prepared, Mapping) or prepared.get("ok") is not True:
        return {"ok": False, "reason_code": "PREPARE_FAILED",
                "detail": "demo.prepare_demo did not report ok"}

    instance = phone_service.load_instance(root_path)
    subject_id = str(instance.get("subject") or subject)
    workspace_id = str(instance.get("workspace_id") or "")
    grant_id = str(instance.get("artifact_grant_id") or "")
    if not (subject_id and workspace_id and grant_id):
        return {"ok": False, "reason_code": "PREPARE_FAILED",
                "detail": "instance lacks subject/workspace/grant"}

    supply = None
    try:
        supply = baseline.start_supply_owner(root_path, tag="v2-offline")
        works = _provision_works(plan=plan, subject=subject_id, workspace_id=workspace_id,
                                 grant_id=grant_id, supply_socket=str(supply.socket_path))
    except Exception as exc:
        return {"ok": False, "reason_code": "WORK_ARTIFACT_CREATE_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    finally:
        if supply is not None:
            try:
                supply.stop()
            except Exception:
                pass

    memory_status = "NOT_REQUESTED"
    authority = root_path.parent / ("%s-memory-authority" % root_path.name) / "authority.db"
    if with_memory:
        try:
            authority.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(authority.parent, 0o700)
            except OSError:
                pass
            memory = phone_life.enable_instance_memory(
                root_path, subject_id, "%s:memory" % plan.run_name,
                authority_path=authority)
            memory_status = str(memory.get("status") or memory.get("reason") or "")
            if memory.get("ok") is not True:
                return {"ok": False, "reason_code": "MEMORY_BIND_FAILED",
                        "detail": memory_status, "works": works}
        except Exception as exc:
            return {"ok": False, "reason_code": "MEMORY_BIND_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160]), "works": works}

    env = scratch_env(root_path, plan=plan)
    state_root = Path(str(env.get("NYA02_RESIDENT_STATE") or ""))
    if not state_root or not str(state_root).startswith(str(root_path)):
        return {"ok": False, "reason_code": "PREPARE_FAILED",
                "detail": "state root is not inside the scratch instance"}
    return {"ok": True, "root": str(root_path), "state_root": str(state_root),
            "subject": subject_id, "workspace_id": workspace_id,
            "artifact_grant_id": grant_id, "works": works,
            "memory": memory_status, "memory_authority": str(authority)}


def _provision_works(*, plan: ExperimentPlan, subject: str, workspace_id: str,
                     grant_id: str, supply_socket: str) -> list[dict[str, Any]]:
    """两件同类、中性、等长的 operator 材料（与 v1 同文本、同标题），v2 自己的 operation key。"""

    from life_artifact_port import LifeArtifactPort

    if len(baseline.WORK_SEED_TEXT) != len(baseline.WORK_SEED_TEXT_2):
        raise baseline.BatchRefused("PREPARE_FAILED", "the two seed texts must be equal length")
    port = LifeArtifactPort(supply_socket, subject)
    created: list[dict[str, Any]] = []
    for index, (title, text) in enumerate(zip(baseline.WORK_TITLES,
                                              (baseline.WORK_SEED_TEXT,
                                               baseline.WORK_SEED_TEXT_2))):
        outcome = port.create_artifact(
            workspace_id=workspace_id, title=title, content=text, grant_id=grant_id,
            operation_key="%s:work:%d" % (plan.run_name, index))
        if not outcome.applied or not outcome.artifact_id:
            raise baseline.BatchRefused("WORK_ARTIFACT_CREATE_FAILED",
                                        "owner refused %s" % title)
        created.append({"title": title, "artifact_id": str(outcome.artifact_id),
                        "version_id": str(outcome.version_id or ""),
                        "status": str(outcome.status)})
    return created


def scratch_env(root: Path | str, *, plan: ExperimentPlan = DEFAULT_PLAN) -> dict[str, str]:
    """实例绑定环境；只接受 v2 自己的 run 身份（v1 根/旧根一律不读）。"""

    from components.phone import life as phone_life

    phone = phone_life.load_phone_instance(root)
    nested = phone_life.read_nested_life(root)
    if nested is None:
        raise baseline.BatchRefused(baseline.REASON_NOT_PREPARED, "no nested life instance")
    binding = phone_life.build_binding(phone, nested, base_env=dict(os.environ))
    env = dict(binding["environment"])
    if str(env.get("CHIYO_AUTONOMOUS_RUN_ID") or "") != plan.run_id:
        raise baseline.BatchRefused(baseline.REASON_NOT_PREPARED,
                                    "instance run identity differs from the v2 plan")
    return env


#: 模型相关环境变量的命名前缀。离线路径把它们全部清掉，只用本模块自己的假配置。
_MODEL_ENV_PREFIX = "NYA02_MODEL_"


def offline_model_env(plan: ExperimentPlan = DEFAULT_PLAN) -> dict[str, str]:
    """离线模型配置：地址是不可用的假域名，配合最底层 HTTP connect 硬拒使用。"""

    return {"NYA02_MODEL_PROVIDER": "custom",
            "NYA02_MODEL_BASE_URL": "https://offline.invalid/v1",
            "NYA02_MODEL_NAME": "offline-model",
            "NYA02_MODEL_API_KEY": "offline-not-a-key",
            "CHIYO_AUTONOMOUS_RUN_ID": plan.run_id}


# ---------------------------------------------------------------------------------
# 真实运行入口：明确关闭
# ---------------------------------------------------------------------------------

def product_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """真实 prepare/run 都是关着的：本单只有离线观察，没有新预算、没有产品调用。"""

    return {"ok": False, "reason_code": PRODUCT_RUN_NOT_AUTHORIZED,
            "detail": ("v2 的自然采集目前只有离线观察控制器；真实 prepare/run 入口未开启，"
                       "需要本人另批预算与运行授权。"),
            "plan": DEFAULT_PLAN.as_dict(), "requests_sent": 0, "model_called": 0}


# ---------------------------------------------------------------------------------
# offline-check：新测试 + 一条真实接缝的离线演示
# ---------------------------------------------------------------------------------

class CountingTransport(baseline.CaptureTransport):
    """离线最底层 transport：只计数、只回一条固定答复，永不联网。"""

    def __init__(self, text: str = "{}", *, label: str = "capture") -> None:
        super().__init__(text=text)
        self.label = label

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        from agency_model_client import ModelCallResult

        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or ""), "label": self.label})
        return ModelCallResult(purpose="offline", provider=self.provider, model=self.model,
                               text=self._text, latency_ms=1, status=200,
                               usage_source="none")


def _no_action_text() -> str:
    return json.dumps({"decision": "NO_ACTION", "reason_codes": ["NO_ACTION_CHOSEN"]})


def _run_unit_tests() -> dict[str, Any]:
    try:
        suite = unittest.TestLoader().discover(
            start_dir=str(REPO_ROOT / "tests"), pattern="test_history_choice_natural_v2.py")
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    return {"ok": bool(result.wasSuccessful()), "run": int(result.testsRun),
            "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(getattr(result, "skipped", []) or [])}


def _hard_disconnect(cls: type, method: str) -> Callable[[], None]:
    original = getattr(cls, method, None)

    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("OFFLINE_HTTP_FORBIDDEN")

    setattr(cls, method, _blocked)

    def restore() -> None:
        if original is not None:
            setattr(cls, method, original)

    return restore


class ScriptedStartTransport(CountingTransport):
    """演示用决策替身：第一次挑一个可执行的 artifact 候选选 START，之后一律 NO_ACTION。

    这是**夹具**，不是模型行为：它只证明「一次真实的 agent 决定 → 真实 writer → 真实 owner
    版本 → 正式 M0 准入」这条链可以把 H 建起来，以及结构门随后会怎么判。
    """

    def __init__(self) -> None:
        super().__init__("{}", label="scripted-start")
        self.started = False

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        from agency_model_client import ModelCallResult

        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or ""), "label": self.label})
        answer: dict[str, Any] = {"decision": "NO_ACTION",
                                  "reason_codes": ["NO_ACTION_CHOSEN"]}
        if not self.started:
            try:
                payload = json.loads(user_prompt)
                startable = [
                    row for row in (payload.get("candidates") or [])
                    if isinstance(row, Mapping)
                    and {"START", "CONTINUE", "RESUME"} & set(row.get("legal_decisions") or ())
                    and any(str(ref).startswith("artifact:")
                            for ref in (row.get("target_refs") or ()))]
            except ValueError:
                startable = []
            if startable:
                self.started = True
                answer = {"decision": "START",
                          "selected_candidate_id": startable[0]["candidate_id"],
                          "reason_codes": ["OPEN_ENDED_CONTINUATION"]}
        return ModelCallResult(purpose="offline", provider=self.provider, model=self.model,
                               text=json.dumps(answer, ensure_ascii=False), latency_ms=1,
                               status=200, usage_source="none")


class ScriptedAppendTransport(CountingTransport):
    """演示用内容替身：总是回一段非空的中性追加文字（只为了真的产生一个 owner 版本）。"""

    def __init__(self) -> None:
        super().__init__("{}", label="scripted-append")

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        from agency_model_client import ModelCallResult

        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or ""), "label": self.label})
        return ModelCallResult(
            purpose="offline", provider=self.provider, model=self.model,
            text=json.dumps({"kind": "append_paragraph",
                             "text": "末尾补了一句中性的练习说明。"}, ensure_ascii=False),
            latency_ms=1, status=200, usage_source="none")


def _open_scratch(scratch_parent: Path, name: str, plan: ExperimentPlan, *,
                  decision_transport: Any, content_transport: Any,
                  deadline_check: Optional[Callable[[], bool]] = None) -> dict[str, Any]:
    """起一个离线 scratch 实例并把真实 runtime/runner 装好（不联网、不占产品预算）。"""

    from life_request_budget import LifeRequestBudget

    root = Path(scratch_parent) / name
    prepared = prepare_scratch_instance(root, plan=plan)
    if not prepared.get("ok"):
        return {"ok": False, "reason_code": prepared.get("reason_code"),
                "detail": prepared.get("detail"), "scratch_root": str(root)}
    context: dict[str, Any] = {"ok": True, "root": root, "prepared": prepared,
                               "supply": None, "bundle": None}
    try:
        context["supply"] = baseline.start_supply_owner(root, tag="v2-demo")
        env = scratch_env(root, plan=plan)
        # 不透传任何 source 模型环境：先把父进程可能带的模型变量清掉，只留离线那一套。
        for key in [name for name in env if name.startswith(_MODEL_ENV_PREFIX)]:
            env.pop(key, None)
        env.update({key: value for key, value in offline_model_env(plan).items()
                    if key.startswith(_MODEL_ENV_PREFIX)})
        # 让每次观察都落在一个真实 due 窗口里（离线夹具值，不改生产默认）。
        env["CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES"] = "0"
        state_root = Path(str(env.get("NYA02_RESIDENT_STATE") or ""))
        budget = LifeRequestBudget(state_root, utc_day_max=int(plan.utc_day_cap),
                                   run_window_max=int(plan.request_cap),
                                   run_id=plan.run_id)
        budget.ensure_window(allow_new=True)
        # 两个发送口（决策 adapter 与内容 client）共用同一把「立刻停发」锁。
        latch = baseline.SendLatch(deadline_check or (lambda: False))
        bundle = baseline.build_experiment_runtime(
            env, root=root, state_root=state_root, budget=budget,
            ledger=PlanGateLedger(plan), stage="acquisition",
            cognition_transport=decision_transport, content_transport=content_transport,
            latch=latch)
        bundle["assembled_env"] = env
        context.update({"env": env, "state_root": state_root, "budget": budget,
                        "bundle": bundle, "decision_transport": decision_transport,
                        "content_transport": content_transport, "latch": latch})
        return context
    except Exception as exc:
        _close_scratch(context)
        return {"ok": False, "reason_code": "SCRATCH_SETUP_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                "scratch_root": str(root)}


def _close_scratch(context: Mapping[str, Any]) -> None:
    bundle = context.get("bundle")
    if bundle is not None:
        baseline.release_runtime(bundle)
    supply = context.get("supply")
    if supply is not None:
        try:
            supply.stop()
        except Exception:
            pass


def _advance_from_runner(bundle: Mapping[str, Any]) -> Callable[[], Mapping[str, Any]]:
    """生产 runner 的一次 tick：maintain → 事件 → 原 due 门。控制器只看报告。"""

    def advance() -> Mapping[str, Any]:
        from autonomous_life_assembly import runner_tick

        tick = runner_tick(bundle["assembled"], act=True)
        due = dict(tick.get("due") or {})
        due["events"] = [{"kind": event.get("kind"), "status": event.get("status"),
                          "llm_calls": int(event.get("llm_calls") or 0)}
                         for event in (tick.get("events") or [])]
        due["waiting_state"] = (tick.get("waiting") or {}).get("state")
        due["feed_errors"] = len(tick.get("feed_errors") or [])
        return due

    return advance


def _controller_for(context: Mapping[str, Any], plan: ExperimentPlan, *,
                    record: ExperimentRecord, virtual: VirtualTime,
                    freeze: Optional[Callable[[str], Mapping[str, Any]]] = None
                    ) -> "NaturalObservationController":
    bundle = context["bundle"]
    return NaturalObservationController(
        plan=plan,
        observer=ReadOnlyObserver(state_root=context["state_root"], run_id=plan.run_id,
                                  activity_read=bundle["runtime"].activity_read),
        advance=_advance_from_runner(bundle), budget=context["budget"], record=record,
        h_detector=HistoryDetector(candidate_read=bundle["runtime"].candidate_read,
                                   memory_port=bundle["memory"].get("port")),
        freeze=freeze, clock=virtual.monotonic, sleep=virtual.sleep,
        latch=context.get("latch"))


def _quiet_phase(scratch_parent: Path, plan: ExperimentPlan) -> dict[str, Any]:
    """阶段一：一次真实决定得到 NO_ACTION，之后同一状态一路 QUIET，一个请求都不再发。"""

    decide = CountingTransport(_no_action_text(), label="decision")
    content = CountingTransport(json.dumps({"kind": "no_change", "text": ""}), label="content")
    context = _open_scratch(scratch_parent, "instance-quiet", plan,
                            decision_transport=decide, content_transport=content)
    if not context.get("ok"):
        return context
    try:
        virtual = VirtualTime()
        record = ExperimentRecord(scratch_parent / plan.experiment_dirname, plan=plan)
        if not record.begin_batch(note="offline demo: quiet phase").get("ok"):
            return {"ok": False, "reason_code": STOP_BATCH_ALREADY_STARTED}
        summary = _controller_for(context, plan, record=record, virtual=virtual).run()
        calls = len(decide.calls) + len(content.calls)
        timeline = summary.get("timeline") or []
        quiet_after_first = [row for row in timeline[1:]
                             if int(row.get("llm_calls") or 0) == 0]
        checks = {
            "observations_recorded": len(timeline) >= 3,
            "first_observation_requested": bool(timeline)
            and int(timeline[0].get("llm_calls") or 0) >= 1,
            "quiet_after_first_request": len(quiet_after_first) >= 2,
            "requests_within_cap": int(summary.get("requests_used") or 0) <= plan.request_cap,
            "transport_matches_ledger": calls == int(summary.get("requests_used") or 0),
            "named_stop_reason": bool(summary.get("stop_reason")),
        }
        blocking = [key for key, value in checks.items() if not value]
        return {"ok": not blocking, "blocking": blocking, "checks": checks,
                "summary": summary, "transport_calls": calls,
                "scratch_root": str(context["root"]),
                "record_dir": str(record.directory)}
    finally:
        _close_scratch(context)


def _h_phase(scratch_parent: Path, plan: ExperimentPlan) -> dict[str, Any]:
    """阶段二：脚本裁决 START → 真实 writer → 真实 owner 版本 → 正式 M0 → H → 结构门。

    裁决与内容是**夹具**；owner、writer 端口、M0 准入、结构门、冻结都是真的。
    这里要看到的是：H 能被机械规则认出来，以及「H 占着前台、另一件不可执行」时
    结构门如实给出 ``SECOND_WORK_CANDIDATE_MISSING``，而不是被当成「没有历史影响」。
    """

    decide = ScriptedStartTransport()
    content = ScriptedAppendTransport()
    context = _open_scratch(scratch_parent, "instance-h", plan,
                            decision_transport=decide, content_transport=content)
    if not context.get("ok"):
        return context
    try:
        virtual = VirtualTime()
        record = ExperimentRecord(scratch_parent / plan.experiment_dirname, plan=plan)
        work_refs = ["artifact:%s" % row["artifact_id"]
                     for row in context["prepared"]["works"]]
        frozen_calls: list[dict[str, Any]] = []

        def freeze(h_ref: str) -> Mapping[str, Any]:
            result = freeze_and_check(context["bundle"], h_ref=h_ref,
                                      work_refs=work_refs, sleep=virtual.sleep)
            frozen_calls.append({"ok": bool(result.get("ok")),
                                 "reason_code": result.get("reason_code"),
                                 "gate": {key: (result.get("gate") or {}).get(key) for key in (
                                     "ok", "candidate_count", "executable_count", "h_executable",
                                     "other_work_executable", "no_action_legal",
                                     "legal_decision_enums", "offline_transport",
                                     "capture_decision")}})
            return result

        phase_plan = replace(plan, max_observations=3)
        summary = _controller_for(context, phase_plan, record=record, virtual=virtual,
                                  freeze=freeze).run()
        calls = len(decide.calls) + len(content.calls)
        gate = (frozen_calls[0]["gate"] if frozen_calls else {}) or {}
        timeline = summary.get("timeline") or []
        # 新 owner 版本之后，原 runner 门自己重新开放（又发了一次请求），而不是被我们释放/强推。
        reopened = [row for row in timeline[1:] if int(row.get("llm_calls") or 0) >= 1]
        quiet_after = [row for row in timeline[-1:] if int(row.get("llm_calls") or 0) == 0]
        checks = {
            "h_established": bool(summary.get("h_ref")),
            "gate_reopened_on_the_new_version": bool(reopened),
            "quiet_again_afterwards": bool(quiet_after),
            "owner_version_recorded": bool(summary.get("h_detail", {}).get("owner_version_id")),
            "m0_experience_readable": bool(summary.get("h_detail", {}).get("experience_id")),
            "freeze_attempted": bool(frozen_calls),
            "structural_gate_evaluated": bool(gate.get("candidate_count")),
            "gate_used_the_offline_transport": gate.get("offline_transport") is True,
            "outcome_is_named": summary.get("result") in (RESULT_FROZEN,
                                                          RESULT_NOT_IDENTIFIABLE),
            "requests_within_cap": int(summary.get("requests_used") or 0) <= plan.request_cap,
            "transport_matches_ledger": calls == int(summary.get("requests_used") or 0),
        }
        blocking = [key for key, value in checks.items() if not value]
        return {"ok": not blocking, "blocking": blocking, "checks": checks,
                "summary": summary, "structural": frozen_calls, "transport_calls": calls,
                "works": [row["artifact_id"] for row in context["prepared"]["works"]],
                "scratch_root": str(context["root"]),
                "note": ("START/内容裁决是夹具；owner、writer 端口、M0 准入、结构门都是真的。"
                         "它证明门的位置与判定，不证明模型会这样选。")}
    finally:
        _close_scratch(context)


def offline_live_seam_demo() -> dict[str, Any]:
    """两段离线演示（虚拟时钟加速、最底层 HTTP 硬断）：接线与门是真的，行为不是证据。"""

    import http.client
    import tempfile

    plan = DEFAULT_PLAN
    restore_http = _hard_disconnect(http.client.HTTPConnection, "connect")
    scratch_parent = Path(tempfile.mkdtemp(prefix="histchoice-v2-offline-"))
    try:
        quiet = _quiet_phase(scratch_parent, plan)
        h_phase = _h_phase(scratch_parent, plan)
        checks = {
            "quiet_phase": bool(quiet.get("ok")),
            "h_phase": bool(h_phase.get("ok")),
            "no_network": True,
        }
        blocking = [key for key, value in checks.items() if not value]
        return {"ok": not blocking, "blocking": blocking, "checks": checks,
                "quiet": quiet, "h_phase": h_phase,
                "scratch_parent": str(scratch_parent),
                "note": ("离线接线演示：模型 transport 与时钟是替身；不是真实模型采用，"
                         "也不是 1800 秒自然经过。scratch 全部保留，不清理。")}
    finally:
        restore_http()


def measure_request_parameters() -> dict[str, Any]:
    """离线实测决策/内容两条路真实会发出的 max_tokens/temperature/stream（与 v2 计划一致）。"""

    from agency_decision_ag1 import CognitiveDecisionRequest
    from life_artifact_port import ArtifactResumePoint
    from profile_model_transport import OpenAICompatibleTransport

    env = offline_model_env()
    decision_transport = CountingTransport(_no_action_text(), label="measure-decision")
    cognition = baseline.raw_cognition_adapter(env, transport=decision_transport)
    request = CognitiveDecisionRequest(
        opportunity_id="opp:offline", candidate_set_id="cset:offline",
        context_snapshot_id="ctx:offline", snapshot_fingerprint="fp:offline",
        candidates=(), current_activity_summary=None,
        capacity_summary={"overall_capacity_state": "AVAILABLE"},
        legal_decision_enums=("NO_ACTION", "DEFER"),
        allowed_reason_codes=("NO_ACTION_CHOSEN",),
        observed_fact_keys=(), policy_version="ag0-v1")
    decision_cap = int(cognition.decision_output_cap(request))
    focus = decision_transport
    cognition.invoke(request)
    content_transport = CountingTransport(json.dumps({"kind": "no_change", "text": ""}),
                                         label="measure-content")
    writer = baseline.build_content_writer(env, budget=None, admission=None,
                                           transport=content_transport)
    point = ArtifactResumePoint(artifact_id="artifact:offline", title="练习甲",
                                head_version_id="ver:1", version_number=1, content="x",
                                lifecycle="ACTIVE", revision=1)
    try:
        writer.next_content(point, observed_at=_iso())
    except Exception:
        pass
    content_cap = int(content_transport.calls[0]["max_output_tokens"]) \
        if content_transport.calls else -1
    wire_decision = json.loads(OpenAICompatibleTransport(
        base_url="https://offline.invalid/v1", api_key="x", model="offline-model",
        temperature=baseline.MODEL_TEMPERATURE)._build_payload("s", "u", decision_cap))
    wire_content = json.loads(OpenAICompatibleTransport(
        base_url="https://offline.invalid/v1", api_key="x", model="offline-model",
        temperature=baseline.MODEL_TEMPERATURE)._build_payload("s", "u", content_cap))
    actual = {"decision_max_tokens": int(wire_decision.get("max_tokens") or -1),
              "decision_temperature": wire_decision.get("temperature"),
              "decision_stream": wire_decision.get("stream"),
              "content_max_tokens": int(wire_content.get("max_tokens") or -1),
              "content_temperature": wire_content.get("temperature"),
              "content_stream": wire_content.get("stream"),
              "decision_calls": len(focus.calls),
              "content_calls": len(content_transport.calls)}
    expected = {"decision_max_tokens": baseline.DECISION_OUTPUT_CAP,
                "decision_temperature": baseline.MODEL_TEMPERATURE,
                "decision_stream": baseline.MODEL_STREAM,
                "content_max_tokens": baseline.CONTENT_OUTPUT_CAP,
                "content_temperature": baseline.MODEL_TEMPERATURE,
                "content_stream": baseline.MODEL_STREAM}
    mismatches = {key: {"expected": expected[key], "actual": actual.get(key)}
                  for key in expected if actual.get(key) != expected[key]}
    return {"ok": not mismatches and actual["decision_calls"] == 1,
            "expected": expected, "actual": actual, "mismatches": mismatches}


def offline_check() -> dict[str, Any]:
    """唯一命令：新测试 + 参数实测 + 真实接缝离线演示 + 真实入口关闭。全部在硬断网下。"""

    import http.client

    restore = _hard_disconnect(http.client.HTTPConnection, "connect")
    try:
        gates: dict[str, Any] = {"unit_tests": _run_unit_tests()}
        try:
            gates["request_parameters"] = measure_request_parameters()
        except Exception as exc:
            gates["request_parameters"] = {
                "ok": False, "reason": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
        try:
            gates["live_seam"] = offline_live_seam_demo()
        except Exception as exc:
            gates["live_seam"] = {"ok": False,
                                  "reason": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
        refusal = product_run()
        gates["product_run_closed"] = {
            "ok": refusal.get("reason_code") == PRODUCT_RUN_NOT_AUTHORIZED,
            "reason_code": refusal.get("reason_code")}
        blocking = [name for name in ("unit_tests", "request_parameters", "live_seam",
                                      "product_run_closed")
                    if not (gates.get(name) or {}).get("ok")]
        return {"ok": not blocking, "blocking": blocking, "gates": gates,
                "plan": DEFAULT_PLAN.as_dict(),
                "model_called": 0, "request_sent": 0, "formal_root_created": False,
                "network": "hard-disconnected (http.client.HTTPConnection.connect)"}
    finally:
        restore()


# ---------------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------------

def _print_timeline(summary: Mapping[str, Any]) -> None:
    print("观察时间线（离线，虚拟时钟；起点=0s）：")
    for row in summary.get("timeline") or []:
        print("  #%02d t=%6.1fs status=%-14s kind=%-12s llm=%d events=%s "
              "activities=%s eligibilities=%s reconsider=%s requests=%s"
              % (int(row.get("index") or 0), float(row.get("elapsed_s") or 0.0),
                 str(row.get("status")), str(row.get("kind")),
                 int(row.get("llm_calls") or 0), row.get("events"),
                 row.get("open_activities", "-"), row.get("open_eligibilities", "-"),
                 row.get("reconsideration_situations", "-"), row.get("requests")))
        if row.get("h_ref"):
            print("      H=%s gate=%s" % (row.get("h_ref"), row.get("h_gate")))
    print("结束：%s / 结果：%s / 观察 %s 次 / 请求 %s（cap %s）"
          % (summary.get("stop_reason"), summary.get("result"),
             summary.get("observations"), summary.get("requests_used"),
             summary.get("request_cap")))


def cmd_offline_check(_argv: Sequence[str]) -> int:
    result = offline_check()
    print(json.dumps({key: result[key] for key in ("ok", "blocking", "model_called",
                                                   "request_sent", "network")},
                     ensure_ascii=False, indent=1))
    for name, gate in (result.get("gates") or {}).items():
        print("[%s] ok=%s%s" % (name, gate.get("ok"),
                                "" if gate.get("ok") else " %s" % (
                                    gate.get("reason") or gate.get("blocking")
                                    or gate.get("detail") or gate.get("reason_code") or "")))
    live = (result.get("gates") or {}).get("live_seam") or {}
    quiet = live.get("quiet") or {}
    if isinstance(quiet.get("summary"), Mapping):
        print("== 阶段一：同状态 QUIET（真实 runner + 真实 reconsideration 门）==")
        _print_timeline(quiet["summary"])
    h_phase = live.get("h_phase") or {}
    if isinstance(h_phase.get("summary"), Mapping):
        print("== 阶段二：真实 owner 版本 → H → 结构门（START/内容为夹具）==")
        _print_timeline(h_phase["summary"])
        summary = h_phase["summary"]
        print("   H=%s version=%s experience=%s"
              % (summary.get("h_ref"),
                 (summary.get("h_detail") or {}).get("owner_version_id"),
                 (summary.get("h_detail") or {}).get("experience_id")))
        for attempt in h_phase.get("structural") or []:
            print("   结构门 ok=%s reason=%s %s" % (
                attempt.get("ok"), attempt.get("reason_code"), attempt.get("gate")))
    if live:
        print("说明：%s" % live.get("note"))
        print("scratch（保留，不清理）：%s" % live.get("scratch_parent"))
    if not result.get("ok"):
        print("阻断面：%s" % ", ".join(result.get("blocking") or []))
        return EXIT_ERROR
    return EXIT_OK


def cmd_product_refused(_argv: Sequence[str]) -> int:
    print(json.dumps(product_run(), ensure_ascii=False, indent=1))
    return EXIT_REFUSED


COMMANDS = {"offline-check": cmd_offline_check,
            "prepare": cmd_product_refused,
            "run": cmd_product_refused}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="历史影响选择 v2（A 路线）离线观察控制器；真实 prepare/run 未开启。")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
    return COMMANDS[args.command]([])


if __name__ == "__main__":
    raise SystemExit(main())

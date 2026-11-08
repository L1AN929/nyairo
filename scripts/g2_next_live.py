#!/usr/bin/env python3
"""G2 A 路线唯一真实批的封闭启动入口（离线可测；真实 prepare/audit/run 只在本人批准后由父级操作）。

本文件是**封闭启动适配**，不是第二套实验仪器。它把既有三件实现接成一个有单一身份的批：

* ``history_choice_natural_v2`` —— A 路线的自然观察控制器、只读观察器、H 检测、冻结与结构门；
* ``history_choice_experiment`` —— 影子两臂的中性文本替换、发送前单变量门、20 对固定顺序、
  持久请求额度、真实承接回合、``clone_branch`` 隔离复制；
* ``g2_next_offline`` —— 52 的账本口径、同一冻结目标的两臂映射与统计出口。

固定身份（不得借旧 run）：

    root      = /root/nyairo-g2-natural-20261031-a
    main      = root/run/main
    authority = root/run/memory-authority
    branches  = root/run/branches
    run_id    = run:g2-natural-20261031-a
    domain    = g2:natural:20261031-a
    history   = 185 / 总 300 / 新增硬 cap 52（8 采集 + 40 影子 + 4 承接 + 0 诊断）

边界（写死在代码里）：

* ``prepare`` 只建本批自己的根/实例/作品/Memory authority/预算窗口；root 或任一子根已存在
  （含符号链接）一律拒绝，绝不覆盖。
* ``audit`` 纯读：不建目录、不建库、不取账本锁、不起服务、不 claim lease、不 source 密钥。
* ``run`` 只在显式 ``--execute-approved`` 下执行，且整批只能开始一次：窗口/started 持久落盘后，
  第二次运行、失败后重启、中断重放一律拒绝；进程崩溃不续跑。
* 真实路径拒绝任何夹具替身；夹具路径必须显式 ``--offline-fixtures`` 且只接受 tempdir 前缀的
  新 scratch，正式根永远不能夹具运行。夹具运行把 ``http.client.HTTPConnection.connect`` 硬断。
* 所有输出只含固定 reason code / 枚举 / ID / 计数 / 布尔 / 时间 / 路径；不含 prompt、正文、
  推理、异常原文、headers、环境变量或密钥，也不含 ``recent_tokens``。

本模块不修改任何 ``components/``、旧 ``tests/``、旧批 root/run、默认 gate 或生产规则。
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import sys
import tempfile
import time
import uuid
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

#: 只读复用既有仪器：不改它们的常量、落盘格式或旧结果。
import history_choice_experiment as baseline  # noqa: E402
import history_choice_natural_v2 as v2  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2

# ---------------------------------------------------------------------------------
# 固定身份（唯一来源；改这里等于改批，必须在派工前由父级更新预登记）
# ---------------------------------------------------------------------------------

RUN_NAME = "g2-natural-20261031-a"
RUN_ID = "run:" + RUN_NAME
DOMAIN_ID = "g2:natural:20261031-a"
FORMAL_BATCH_ROOT = Path("/root/nyairo-g2-natural-20261031-a")

EXPECTED_HISTORY = 185
UTC_DAY_CAP = 300
REQUEST_CAP = 52
ACQUISITION_CAP = 8
SHADOW_CAP = 40
CONTINUATION_CAP = 4
ARM_REQUEST_CAP = 2
DIAGNOSTIC_CAP = 0

ACQUISITION_WINDOW_SECONDS = 1800
OBSERVATION_INTERVAL_SECONDS = 60
MAX_OBSERVATIONS = 31
TOTAL_WINDOW_SECONDS = 5400
DEADLINE = datetime(2026, 11, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=8)))

# ---------------------------------------------------------------------------------
# b 批身份：同一入口里的第二份封闭 profile。a 仍是缺省，a 的常量/行为不变。
#
# 只改时间观察范围与必要的新批参数（采集 9000 真秒=2.5 小时、纯读上限 151、
# 整批 11400 秒=3 小时 10 分、固定绝对截止与 latest_start）。latest_start 是**过期拒绝门**：
# 真实 run 在 2026-10-31T22:30:00+08:00 及之后一律拒绝，不滚动、不延到 02:00、不缩采集凑可跑。
# 旧时段（采集 10800/纯读 181/整批 14400/latest_start 21:08:24/截止 00:38:24）从未真实运行，
# 已被本人 2026-10-31 21:54 直接批准的新时段覆盖，历史保留。
# ---------------------------------------------------------------------------------

BATCH_DEFAULT = "a"
BATCH_B = "b"
BATCH_C = "c"
BATCH_D2 = "d2"
BATCH_CHOICES = (BATCH_DEFAULT, BATCH_B, BATCH_C, BATCH_D2)

B_RUN_NAME = "g2-natural-20261031-b"
B_RUN_ID = "run:" + B_RUN_NAME
B_DOMAIN_ID = "g2:natural:20261031-b"
B_FORMAL_BATCH_ROOT = Path("/root/nyairo-g2-natural-20261031-b")

B_EXPECTED_HISTORY = 186
B_ACQUISITION_WINDOW_SECONDS = 9000
B_MAX_OBSERVATIONS = 151
B_TOTAL_WINDOW_SECONDS = 11400
B_DEADLINE = datetime(2026, 11, 1, 2, 0, 0, tzinfo=timezone(timedelta(hours=8)))
B_LATEST_START = datetime(2026, 10, 31, 22, 30, 0, tzinfo=timezone(timedelta(hours=8)))

#: C 批（探索）：新根/新身份/历史 234 / 总 30 = 采集 8 + 影子 20（10 对）+ 保留 2。
#: 本轮**不执行两臂承接**（最低 4 请求与 cap 不相容），停在影子选择边界。
C_RUN_NAME = "g2-explore-20261103-c"
C_RUN_ID = "run:" + C_RUN_NAME
C_DOMAIN_ID = "g2:explore:20261103-c"
C_FORMAL_BATCH_ROOT = Path("/tmp/nyairo-g2-explore-20261103-c")
C_EXPECTED_HISTORY = 234
C_REQUEST_CAP = 30
C_ACQUISITION_CAP = 8
C_SHADOW_CAP = 20
C_CONTINUATION_CAP = 0
C_RESERVED_CAP = 2
C_PAIRS = 10
C_ACQUISITION_WINDOW_SECONDS = 5400
C_OBSERVATION_INTERVAL_SECONDS = 60
C_MAX_OBSERVATIONS = 91
C_TOTAL_WINDOW_SECONDS = 7200
C_DEADLINE = datetime(2026, 11, 3, 21, 45, 0, tzinfo=timezone(timedelta(hours=8)))
C_LATEST_START = datetime(2026, 11, 3, 19, 30, 0, tzinfo=timezone(timedelta(hours=8)))

#: D2 批（C 的故障后继，不是补跑）：新根/新身份/历史 239/总 30 = 采集 8 + 影子 20（10 对）
#: + 保留 2。与 C 唯一的运行时差异是检查节拍 10 分钟（增加 90 分钟采集窗内的定时检查
#: 机会，不改变首次故障停批、不承诺网关抗抖动）。失败后今晚不再重试。
D2_RUN_NAME = "g2-explore-20261103-d2"
D2_RUN_ID = "run:" + D2_RUN_NAME
D2_DOMAIN_ID = "g2:explore:20261103-d2"
D2_FORMAL_BATCH_ROOT = Path("/tmp/nyairo-g2-explore-20261103-d2")
D2_EXPECTED_HISTORY = 239
D2_REQUEST_CAP = 30
D2_ACQUISITION_CAP = 8
D2_SHADOW_CAP = 20
D2_CONTINUATION_CAP = 0
D2_RESERVED_CAP = 2
D2_PAIRS = 10
D2_ACQUISITION_WINDOW_SECONDS = 5400
D2_OBSERVATION_INTERVAL_SECONDS = 60
D2_MAX_OBSERVATIONS = 91
D2_TOTAL_WINDOW_SECONDS = 7200
D2_DEADLINE = datetime(2026, 11, 3, 21, 0, 0, tzinfo=timezone(timedelta(hours=8)))
D2_LATEST_START = datetime(2026, 11, 3, 19, 0, 0, tzinfo=timezone(timedelta(hours=8)))
#: 跨批累计额度只做账目区分；单根 UTC 300 保险丝仍由 LifeRequestBudget 执行。
D2_CUMULATIVE_TOTAL = 1000
D2_CHECK_INTERVAL_MINUTES = 10

#: 实例持久环境里的检查节拍名（one_day_life 默认 30）。
ENV_CHECK_INTERVAL = "CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES"

MODEL_PROVIDER = "custom"
MODEL_BASE_URL = "https://gw.example.invalid/v1"
MODEL_NAME = "cn:deepseek-v4.1-flash"
MODEL_TEMPERATURE = 0.0
MODEL_STREAM = False
DECISION_OUTPUT_CAP = 4096
CONTENT_OUTPUT_CAP = 2048

GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
GATE_OFF = "off"
#: 探索批显式关闭 ambient 背景，防从父 env 继承出两臂不同的额外背景。
AMBIENT_ENV = "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED"
AMBIENT_OFF = "0"

EXPERIMENT_DIRNAME = "experiment"
PREREG_FILENAME = "g2-natural.json"
BATCH_FILENAME = "g2_batch.json"
WINDOW_FILENAME = "g2_window.json"
STOP_FILENAME = "g2_stop.json"
STAGES_FILENAME = "g2_stages.json"
RUN_REPORT_FILENAME = "g2_run_report.json"
NATURAL_DIRNAME = "natural"

#: 输出白名单：CLI 只投影这些键。异常原文/私文一律不进结果。
SAFE_KEYS = frozenset({
    "ok", "reason_code", "result", "state", "fixture", "batch", "run_id", "domain_id",
    "root", "main", "authority", "branches", "state_root", "deadline", "history",
    "history_used", "caps", "model_params", "window", "ledger", "isolation", "started_at",
    "prepared_at", "stop", "shadow", "continuation", "statistics", "requests_used",
    "acquisition", "gate", "h_ref", "h_evidence", "checks", "blocking", "pairs_complete",
    "pairs_target", "pairs", "reserved", "exploratory_only",
    "prepared", "maintain_trace", "present", "lines", "tick", "observed_at",
    "checked", "changed", "satisfied", "emitted_eligibilities", "feed_events",
    "world_revision_changed", "world_revision_basis", "world_signature_changed",
    "world_signature_basis", "quiet_reason", "deadline_wall",
    "ambient",
    "a_h", "b_h", "conclusion", "mcnemar_two_sided_p", "delta", "status", "decision",
    "selected_candidate_ref", "used", "third_refused", "artifacts", "version_ref",
    "activity_ref", "branch", "copied", "stage", "stage_calls", "stage_cap",
    "latest_start", "check_interval_minutes", "persisted_check_interval_minutes",
    "cumulative_total", "utc_day", "expected_history", "total",
})

# ---------------------------------------------------------------------------------
# reason codes（固定枚举；自由文本一律换成 fallback）
# ---------------------------------------------------------------------------------

R_ROOT_NOT_FRESH = "ROOT_NOT_FRESH"
R_SUBROOT_EXISTS = "SUBROOT_ALREADY_EXISTS"
R_AUTHORITY_NOT_FRESH = "MEMORY_AUTHORITY_NOT_FRESH"
R_ALREADY_PREPARED = "ALREADY_PREPARED"
R_NOT_PREPARED = "NOT_PREPARED"
R_HALF_STATE = "HALF_PREPARED_STATE"
R_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
R_PREPARE_FAILED = "PREPARE_FAILED"
R_WORK_CREATE_FAILED = "WORK_ARTIFACT_CREATE_FAILED"
R_MEMORY_BIND_FAILED = "MEMORY_BIND_FAILED"
R_LEDGER_NOT_ZERO = "LEDGER_NOT_ZERO"
R_LEDGER_UNREADABLE = "LEDGER_UNREADABLE"
R_LEDGER_DRIFT = "LEDGER_DRIFT"
R_ALREADY_STARTED = "RUN_ALREADY_STARTED"
R_EXECUTE_FLAG_MISSING = "RUN_REQUIRES_EXECUTE_APPROVED"
R_DEADLINE_PASSED = "DEADLINE_PASSED"
R_LATEST_START_PASSED = "LATEST_START_PASSED"
R_FIXTURE_IN_REAL_PATH = "FIXTURE_IN_REAL_PATH"
R_FIXTURE_ROOT_REFUSED = "FIXTURE_ROOT_REFUSED"
R_NO_MODEL_CONFIG = "NO_REAL_MODEL_CONFIG"
R_MODEL_PARAM_MISMATCH = "MODEL_PARAM_MISMATCH"
R_WORLD_HOME_UNAVAILABLE = "WORLD_HOME_UNAVAILABLE"
R_WORLD_HOME_OUTSIDE_ROOT = "WORLD_HOME_OUTSIDE_ROOT"
R_ISOLATION_UNPROVEN = "ISOLATION_UNPROVEN"
R_GATE_MISMATCH = "WORLD_GATE_MISMATCH"
R_STAGE_CAP = baseline.REASON_STAGE_CAP
R_TOTAL_CAP = baseline.REASON_TOTAL_CAP
R_PROVIDER_FAILURE = baseline.REASON_PROVIDER_FAILURE
R_SECOND_VARIABLE = baseline.REASON_SECOND_VARIABLE
R_SCENARIO_NOT_IDENTIFIABLE = baseline.REASON_SCENARIO_NOT_IDENTIFIABLE
R_NO_EFFECTIVE_HISTORY = baseline.REASON_NO_EFFECTIVE_HISTORY
R_BRANCH_ISOLATION = baseline.REASON_BRANCH_ISOLATION
R_RESUME_MAPPING_UNVERIFIED = "RESUME_MAPPING_UNVERIFIED_OFFLINE"
R_SHADOW_CHOICE_UNMAPPED = "SHADOW_CHOICE_UNMAPPED"
R_SHADOW_TARGET_MISMATCH = "SHADOW_TARGET_MISMATCH"
R_SHADOW_CHOICE_NOT_EXECUTABLE = "SHADOW_CHOICE_NOT_EXECUTABLE"
R_PRECONDITION_REFUSED = "CONTINUATION_PRECONDITION_REFUSED"
R_CONTINUATION_OUT_OF_SCOPE = "CONTINUATION_NOT_IN_SCOPE"
R_NOT_RUN = "NOT_RUN"
R_UNEXPECTED = "UNEXPECTED_ERROR"
R_FIXTURE_NOT_A_RESULT = "FIXTURE_RUN_NOT_A_REAL_RESULT"

RESULT_FROZEN = v2.RESULT_FROZEN
RESULT_NO_H = v2.RESULT_NO_H
RESULT_NOT_IDENTIFIABLE = v2.RESULT_NOT_IDENTIFIABLE
RESULT_ABORTED = v2.RESULT_ABORTED
RESULT_NOT_TESTED = "NOT_TESTED_INCOMPLETE"
RESULT_DETECTED = "DETECTED"
RESULT_NO_DIFFERENCE = "NO_DETECTED_DIFFERENCE"
RESULT_EXPLORATORY = "EXPLORATORY_ONLY"

#: 这些停止属于「批次被中止」，不许写成 NO_EFFECTIVE_HISTORY 之类的结论。
_ABORT_REASONS = frozenset({
    R_PROVIDER_FAILURE, R_LEDGER_UNREADABLE, R_LEDGER_DRIFT, R_UNEXPECTED,
    R_BRANCH_ISOLATION, R_ISOLATION_UNPROVEN, R_FIXTURE_IN_REAL_PATH,
})


class LiveRefused(RuntimeError):
    """具名拒绝；只带固定 reason code。"""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail


# ---------------------------------------------------------------------------------
# 身份与路径
# ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveProfile:
    batch_root: Path = FORMAL_BATCH_ROOT
    run_name: str = RUN_NAME
    run_id: str = RUN_ID
    domain_id: str = DOMAIN_ID
    expected_history: int = EXPECTED_HISTORY
    utc_day_cap: int = UTC_DAY_CAP
    request_cap: int = REQUEST_CAP
    acquisition_cap: int = ACQUISITION_CAP
    shadow_cap: int = SHADOW_CAP
    continuation_cap: int = CONTINUATION_CAP
    arm_cap: int = ARM_REQUEST_CAP
    diagnostic_cap: int = DIAGNOSTIC_CAP
    acquisition_window_seconds: int = ACQUISITION_WINDOW_SECONDS
    observation_interval_seconds: int = OBSERVATION_INTERVAL_SECONDS
    max_observations: int = MAX_OBSERVATIONS
    total_window_seconds: int = TOTAL_WINDOW_SECONDS
    deadline: datetime = DEADLINE
    #: 只有 b 有启动线；a 保持 None（原行为不变）。
    latest_start: Optional[datetime] = None
    #: 供 CLI/测试选择与 a/b 身份区分；a 仍是缺省。
    batch_key: str = BATCH_DEFAULT
    #: 影子两臂的**对数**（每对一个 A、一个 B）；a/b 仍是完整 20 对原型。
    pairs: int = int(baseline.PAIR_COUNT)
    #: 探索批：不套 20 对统计判据，结论只标 EXPLORATORY_ONLY。
    exploratory: bool = False
    #: 本批保留但不使用的额度（30 = 8 + 20 + 2 里的最后 2）。
    reserved_cap: int = 0
    #: c 的根布局是 batch_root/{main,memory-authority,branches}；a/b 保持 run/ 下原布局。
    flat_layout: bool = False
    #: 仅探索批：把每 tick 的维护摘要写进本实验自己的根内文件（产品不读）。
    maintain_trace_path: Optional[Path] = None
    #: 本批实例的检查节拍（分钟）；None 表示保持实例既有值（a/b/c 行为不变）。
    check_interval_minutes: Optional[int] = None
    #: 跨批累计额度；只用于 prereg/audit 账目区分，None 表示不区分（a/b/c 不变）。
    cumulative_total: Optional[int] = None

    @property
    def run_dir(self) -> Path:
        return self.batch_root if self.flat_layout else (self.batch_root / "run")

    @property
    def main_root(self) -> Path:
        return self.run_dir / "main"

    @property
    def authority_dir(self) -> Path:
        return self.run_dir / "memory-authority"

    @property
    def authority_db(self) -> Path:
        return self.authority_dir / "authority.db"

    @property
    def branches_root(self) -> Path:
        return self.run_dir / "branches"

    @property
    def experiment_dir(self) -> Path:
        return self.batch_root / EXPERIMENT_DIRNAME

    @property
    def prereg_path(self) -> Path:
        return self.experiment_dir / PREREG_FILENAME

    @property
    def batch_path(self) -> Path:
        return self.experiment_dir / BATCH_FILENAME

    @property
    def window_path(self) -> Path:
        return self.experiment_dir / WINDOW_FILENAME

    @property
    def stop_path(self) -> Path:
        return self.experiment_dir / STOP_FILENAME

    @property
    def stages_path(self) -> Path:
        return self.experiment_dir / STAGES_FILENAME

    @property
    def run_report_path(self) -> Path:
        return self.experiment_dir / RUN_REPORT_FILENAME

    def caps(self) -> dict[str, Any]:
        return {"acquisition": self.acquisition_cap, "shadow": self.shadow_cap,
                "continuation": self.continuation_cap, "diagnostic": self.diagnostic_cap,
                "total": self.request_cap, "arm": self.arm_cap,
                "pairs": self.pairs, "reserved": self.reserved_cap,
                "exploratory_only": bool(self.exploratory),
                "utc_day": self.utc_day_cap, "expected_history": self.expected_history,
                "acquisition_window_seconds": self.acquisition_window_seconds,
                "observation_interval_seconds": self.observation_interval_seconds,
                "max_observations": self.max_observations,
                "total_window_seconds": self.total_window_seconds,
                "latest_start": (self.latest_start.isoformat()
                                 if self.latest_start is not None else None),
                "check_interval_minutes": self.check_interval_minutes,
                "cumulative_total": self.cumulative_total,
                "gate": GATE_OFF,
                "ambient": (AMBIENT_OFF if self.exploratory else None)}

    def model_params(self) -> dict[str, Any]:
        # 不输出 key（这里根本不保存 key）。
        return {"provider": MODEL_PROVIDER, "base_url": MODEL_BASE_URL,
                "model": MODEL_NAME, "temperature": MODEL_TEMPERATURE,
                "stream": MODEL_STREAM, "decision_output_cap": DECISION_OUTPUT_CAP,
                "content_output_cap": CONTENT_OUTPUT_CAP}

    def paths(self) -> dict[str, str]:
        return {"root": str(self.batch_root), "main": str(self.main_root),
                "authority": str(self.authority_db), "branches": str(self.branches_root),
                "experiment": str(self.experiment_dir)}


FORMAL_PROFILE = LiveProfile()

#: b 批：新根/新身份/186 历史/9000 采集/151 纯读/11400 整批/固定绝对截止与启动线。
B_PROFILE = LiveProfile(
    batch_root=B_FORMAL_BATCH_ROOT,
    run_name=B_RUN_NAME,
    run_id=B_RUN_ID,
    domain_id=B_DOMAIN_ID,
    expected_history=B_EXPECTED_HISTORY,
    acquisition_window_seconds=B_ACQUISITION_WINDOW_SECONDS,
    max_observations=B_MAX_OBSERVATIONS,
    total_window_seconds=B_TOTAL_WINDOW_SECONDS,
    deadline=B_DEADLINE,
    latest_start=B_LATEST_START,
    batch_key=BATCH_B,
)


#: c 批（探索）：root/{main,memory-authority,branches} 平铺，234 历史，30 = 8 + 20 + 2，
#: 10 对、不做承接、只出探索性结论。绝对 21:45 CST 停新请求、19:30 CST 最晚启动。
C_PROFILE = LiveProfile(
    batch_root=C_FORMAL_BATCH_ROOT,
    run_name=C_RUN_NAME,
    run_id=C_RUN_ID,
    domain_id=C_DOMAIN_ID,
    expected_history=C_EXPECTED_HISTORY,
    request_cap=C_REQUEST_CAP,
    acquisition_cap=C_ACQUISITION_CAP,
    shadow_cap=C_SHADOW_CAP,
    continuation_cap=C_CONTINUATION_CAP,
    arm_cap=0,
    diagnostic_cap=0,
    acquisition_window_seconds=C_ACQUISITION_WINDOW_SECONDS,
    observation_interval_seconds=C_OBSERVATION_INTERVAL_SECONDS,
    max_observations=C_MAX_OBSERVATIONS,
    total_window_seconds=C_TOTAL_WINDOW_SECONDS,
    deadline=C_DEADLINE,
    latest_start=C_LATEST_START,
    batch_key=BATCH_C,
    pairs=C_PAIRS,
    exploratory=True,
    reserved_cap=C_RESERVED_CAP,
    flat_layout=True,
    maintain_trace_path=C_FORMAL_BATCH_ROOT / "maintain_trace.jsonl",
)


#: d2 批（C 的故障后继）：同 C 的根布局与额度组成，历史 239、检查节拍 10 分钟、
#: 跨批累计 1000 与单根 UTC 300 分开记账。绝对 21:00 CST 停新请求、19:00 CST 最晚启动。
D2_PROFILE = LiveProfile(
    batch_root=D2_FORMAL_BATCH_ROOT,
    run_name=D2_RUN_NAME,
    run_id=D2_RUN_ID,
    domain_id=D2_DOMAIN_ID,
    expected_history=D2_EXPECTED_HISTORY,
    request_cap=D2_REQUEST_CAP,
    acquisition_cap=D2_ACQUISITION_CAP,
    shadow_cap=D2_SHADOW_CAP,
    continuation_cap=D2_CONTINUATION_CAP,
    arm_cap=0,
    diagnostic_cap=0,
    acquisition_window_seconds=D2_ACQUISITION_WINDOW_SECONDS,
    observation_interval_seconds=D2_OBSERVATION_INTERVAL_SECONDS,
    max_observations=D2_MAX_OBSERVATIONS,
    total_window_seconds=D2_TOTAL_WINDOW_SECONDS,
    deadline=D2_DEADLINE,
    latest_start=D2_LATEST_START,
    batch_key=BATCH_D2,
    pairs=D2_PAIRS,
    exploratory=True,
    reserved_cap=D2_RESERVED_CAP,
    flat_layout=True,
    maintain_trace_path=D2_FORMAL_BATCH_ROOT / "maintain_trace.jsonl",
    check_interval_minutes=D2_CHECK_INTERVAL_MINUTES,
    cumulative_total=D2_CUMULATIVE_TOTAL,
)


def formal_profile(batch: str = BATCH_DEFAULT) -> LiveProfile:
    """按批选正式 profile；缺省仍是 a，未知批一律退回 a 不值猜。"""

    key = str(batch)
    if key == BATCH_B:
        return B_PROFILE
    if key == BATCH_C:
        return C_PROFILE
    if key == BATCH_D2:
        return D2_PROFILE
    return FORMAL_PROFILE


def _formal_roots() -> tuple[Path, ...]:
    return (FORMAL_BATCH_ROOT, B_FORMAL_BATCH_ROOT, C_FORMAL_BATCH_ROOT,
            D2_FORMAL_BATCH_ROOT)


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="milliseconds")


def _read_json(path: Path | str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def _write_json(path: Path | str, payload: Mapping[str, Any], *, private: bool = True) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, indent=1, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, target)


def _exists(path: Path) -> bool:
    """存在或是一个符号链接（符号链接本身也算占用，不能覆盖）。"""

    try:
        return os.path.lexists(str(path))
    except OSError:
        return True


def _within(path: Path | str, root: Path | str) -> bool:
    try:
        Path(str(path)).resolve().relative_to(Path(str(root)).resolve())
        return True
    except (ValueError, OSError):
        return False


def _under_tempdir(path: Path | str) -> bool:
    temp = Path(tempfile.gettempdir()).resolve()
    return _within(path, temp)


def _resolve_profile(*, offline_fixtures: bool, root: Optional[str | Path],
                     batch: str = BATCH_DEFAULT
                     ) -> tuple[Optional[LiveProfile], Optional[dict[str, Any]]]:
    """真实模式固定该批的正式根；夹具模式只接 tempdir 下的新 scratch（a/b 正式根都拒）。"""

    formal = formal_profile(batch)
    if not offline_fixtures:
        if root is not None and Path(str(root)).expanduser() != formal.batch_root:
            return None, {"ok": False, "reason_code": R_FIXTURE_ROOT_REFUSED,
                          "detail": "the real path uses its fixed root; a custom root "
                                    "requires --offline-fixtures"}
        return formal, None
    if root is None:
        return None, {"ok": False, "reason_code": R_FIXTURE_ROOT_REFUSED,
                      "detail": "--offline-fixtures requires a fresh scratch root"}
    candidate = Path(str(root)).expanduser()
    for formal_root in _formal_roots():
        if _within(candidate, formal_root) or candidate.resolve() == formal_root.resolve():
            return None, {"ok": False, "reason_code": R_FIXTURE_ROOT_REFUSED,
                          "detail": "a formal root can never run with fixtures"}
    if not _under_tempdir(candidate):
        return None, {"ok": False, "reason_code": R_FIXTURE_ROOT_REFUSED,
                      "detail": "fixture roots must live under the system tempdir"}
    resolved = replace(formal, batch_root=candidate)
    if formal.maintain_trace_path is not None:
        # 夹具 scratch 绝不能把维护行写回正式根。
        resolved = replace(resolved, maintain_trace_path=(
            candidate / Path(str(formal.maintain_trace_path)).name))
    return resolved, None


def _identity_refusal(profile: LiveProfile, prereg: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    checks = {
        "run_id": profile.run_id,
        "domain_id": profile.domain_id,
        "root": str(profile.batch_root),
    }
    for field, expected in checks.items():
        if str(prereg.get(field) or "") != str(expected):
            return {"ok": False, "reason_code": R_IDENTITY_MISMATCH, "field": field}
    deadline = str(prereg.get("deadline") or "")
    if deadline and deadline != profile.deadline.isoformat():
        return {"ok": False, "reason_code": R_IDENTITY_MISMATCH, "field": "deadline"}
    expected_latest = profile.latest_start.isoformat() if profile.latest_start else ""
    if expected_latest and str(prereg.get("latest_start") or "") != expected_latest:
        return {"ok": False, "reason_code": R_IDENTITY_MISMATCH,
                "field": "latest_start"}
    caps = dict(prereg.get("caps") or {})
    if int(caps.get("expected_history") or -1) != profile.expected_history:
        return {"ok": False, "reason_code": R_IDENTITY_MISMATCH, "field": "history"}
    if int(caps.get("total") or -1) != profile.request_cap:
        return {"ok": False, "reason_code": R_IDENTITY_MISMATCH, "field": "request_cap"}
    if profile.check_interval_minutes is not None:
        if int(caps.get("check_interval_minutes") or -1) != int(profile.check_interval_minutes):
            return {"ok": False, "reason_code": R_IDENTITY_MISMATCH,
                    "field": "check_interval_minutes"}
    return None


# ---------------------------------------------------------------------------------
# 预算：一本持久 52 账本 + 每阶段发送前硬门
# ---------------------------------------------------------------------------------

def _budget(profile: LiveProfile, state_root: Path | str):
    from life_request_budget import LifeRequestBudget

    return LifeRequestBudget(state_root, utc_day_max=profile.utc_day_cap,
                             run_window_max=profile.request_cap, run_id=profile.run_id)


def _safe_budget(view: Mapping[str, Any]) -> dict[str, Any]:
    return {key: (view or {}).get(key) for key in
            ("run_window_calls", "utc_day_calls", "run_window_max", "utc_day_max", "state")}


def _read_ledger_direct(profile: LiveProfile, state_root: Path | str) -> dict[str, Any]:
    """**纯读**账本 JSON：不取锁、不建目录、不建库。用于 audit 与开跑前核对。"""

    try:
        from life_request_budget import budget_path

        path = budget_path(state_root)
    except Exception:
        return {"state": "UNREADABLE", "run_window_calls": None,
                "run_window_present": None}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"state": "absent", "run_window_calls": 0, "run_window_present": False,
                "run_window_max": profile.request_cap, "utc_day_calls": 0}
    except OSError:
        return {"state": "UNREADABLE", "run_window_calls": None,
                "run_window_present": None}
    try:
        data = json.loads(text)
    except ValueError:
        return {"state": "corrupt", "run_window_calls": None, "run_window_present": None}
    windows = data.get("windows") if isinstance(data, Mapping) else None
    window = (windows or {}).get(profile.run_id) if isinstance(windows, Mapping) else None
    days = data.get("days") or {}
    active = str(data.get("active_day") or "")
    return {
        "state": "ok" if (days or windows) else "absent",
        "run_window_calls": int((window or {}).get("calls") or 0) if window is not None else None,
        "run_window_present": window is not None,
        "run_window_max": profile.request_cap,
        "utc_day": active or None,
        "utc_day_calls": int(days.get(active) or 0) if active else 0,
    }


class LiveStageLedger:
    """阶段额度门：每次发送前按同一本持久账本已用数判「阶段 8/40/4、总量 52」。

    它只回答「能不能发」；真正不可绕过的是 ``LifeRequestBudget.reserve()``。阶段起点写在
    本实验自己的 stages 文件里（不另立请求计数）。
    """

    def __init__(self, profile: LiveProfile, budget: Any) -> None:
        self.profile = profile
        self.budget = budget

    def _load(self) -> dict[str, Any]:
        data = _read_json(self.profile.stages_path)
        return data if data else {"stages": {}}

    def start_stage(self, stage: str) -> dict[str, Any]:
        view = self.budget.view()
        used = view.get("run_window_calls")
        if used is None:
            raise LiveRefused(R_LEDGER_UNREADABLE, "run window counters are unreadable")
        data = self._load()
        stages = dict(data.get("stages") or {})
        entry = {"start_calls": int(used), "cap": self._cap(stage)}
        stages[stage] = entry
        data["stages"] = stages
        _write_json(self.profile.stages_path, data)
        return dict(entry)

    def _cap(self, stage: str) -> int:
        return {"acquisition": self.profile.acquisition_cap,
                "shadow": self.profile.shadow_cap,
                "continuation": self.profile.continuation_cap}.get(stage, 0)

    def assert_room(self, stage: str, view: Mapping[str, Any], *,
                    reserving: int = 0) -> dict[str, Any]:
        calls = (view or {}).get("run_window_calls")
        if isinstance(calls, bool) or not isinstance(calls, int):
            raise baseline.BatchRefused(R_LEDGER_UNREADABLE,
                                        "run window counters are unreadable")
        used = int(calls)
        entry = (self._load().get("stages") or {}).get(stage) or {}
        start = entry.get("start_calls")
        if isinstance(start, bool) or not isinstance(start, int):
            raise baseline.BatchRefused(R_STAGE_CAP, "stage %r has no recorded start" % stage)
        stage_used = max(0, used - start)
        cap = int(entry.get("cap") or self._cap(stage))
        # 每次发送前的检查都至少占 1 格；reserving=0 是「这次要发一条」的默认口。
        slot = max(1, int(reserving))
        if stage_used + slot > cap:
            raise baseline.BatchRefused(R_STAGE_CAP,
                                        "stage %s used %d of %d" % (stage, stage_used, cap))
        if used + slot > self.profile.request_cap:
            raise baseline.BatchRefused(R_TOTAL_CAP,
                                        "batch used %d of %d" % (used, self.profile.request_cap))
        return {"stage": stage, "used": used, "stage_used": stage_used,
                "stage_remaining": cap - stage_used,
                "total_remaining": self.profile.request_cap - used}

    # -- 影子样本的尝试行（只落本实验自己的 JSONL；不另立请求计数） ----------

    @property
    def journal_path(self) -> Path:
        return self.profile.experiment_dir / "g2_attempts.jsonl"

    def begin_attempt(self, *, stage: str, kind: str, slot: Optional[int] = None,
                      arm: Optional[str] = None) -> str:
        attempt_id = "g2a:%s" % uuid.uuid4().hex[:16]
        row = {"attempt_id": attempt_id, "at": _iso(), "stage": stage, "kind": kind,
               "slot": slot, "arm": arm, "status": "reserved"}
        self._append_attempt(row)
        return attempt_id

    def finish_attempt(self, attempt_id: str, *, status: str, **extra: Any) -> None:
        row = {"attempt_id": attempt_id, "at": _iso(), "status": str(status)}
        row.update({key: value for key, value in extra.items()
                    if isinstance(value, (str, int, float, bool)) or value is None})
        self._append_attempt(row)

    def _append_attempt(self, row: Mapping[str, Any]) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class PersistentSendLatch(baseline.SendLatch):
    """批次级发送锁；第一次停止原因持久落盘，跨进程重开仍能读到。"""

    def __init__(self, profile: LiveProfile, deadline_check: Callable[[], bool]) -> None:
        super().__init__(deadline_check)
        self.profile = profile

    def stop(self, reason_code: str) -> None:
        first = not self.stop_reason
        super().stop(reason_code)
        if first:
            try:
                if not _exists(self.profile.stop_path):
                    _write_json(self.profile.stop_path, {
                        "schema": "g2.live.stop.v1", "run_id": self.profile.run_id,
                        "reason_code": str(reason_code), "at": _iso()})
            except Exception:
                # 落盘失败由本进程的锁继续兜底；不吞成成功。
                pass


# ---------------------------------------------------------------------------------
# 环境映射与隔离
# ---------------------------------------------------------------------------------

_WORLD_ENV_NAMES = ("CHIYO_WORLD_HOME", "HERMES_HOME", "WORLD_ACTION_AUTH_REGISTRY_DIR",
                    "WORLD_ACTION_LIFECYCLE_PATH", "WORLD_ACTIVITY_STATE",
                    "WORLD_EXPERIENCE_SPOOL", "WORLD_SELF_MAINTENANCE_STATE")
_MEMORY_STRICT_NAMES = ("NYA02_MEMORY_DB", "CHIYO_LIFE_MEMORY_DB",
                        "NYA02_MEMORY_CONTROLS_DB", "CHIYO_LIFE_MEMORY_CONTROLS_DB")
_MEMORY_AUTHORITY_NAMES = ("NYA02_MEMORY_AUTHORITY_DB", "CHIYO_LIFE_MEMORY_AUTHORITY_DB")


def _instance_world_home(root: Path | str) -> Optional[Path]:
    try:
        from components.phone import service as phone_service

        instance = dict(phone_service.load_instance(Path(str(root))))
    except Exception:
        return None
    text = str(instance.get("world_home") or "").strip()
    return Path(text) if text else None


def _require_world_home(root: Path | str) -> Path:
    home = _instance_world_home(root)
    if home is None:
        raise LiveRefused(R_WORLD_HOME_UNAVAILABLE, "instance world_home is missing or unreadable")
    if not home.is_absolute():
        raise LiveRefused(R_WORLD_HOME_UNAVAILABLE, "instance world_home is not absolute")
    try:
        resolved = home.resolve()
        root_resolved = Path(str(root)).resolve()
    except OSError as exc:
        raise LiveRefused(R_WORLD_HOME_UNAVAILABLE, type(exc).__name__) from exc
    if not _within(resolved, root_resolved):
        raise LiveRefused(R_WORLD_HOME_OUTSIDE_ROOT, "world_home resolves outside the instance")
    if not resolved.is_dir():
        raise LiveRefused(R_WORLD_HOME_UNAVAILABLE, "world_home directory does not exist")
    return resolved


def _world_isolation_env(home: Path) -> dict[str, str]:
    run_dir = home / "run"
    return {"CHIYO_WORLD_HOME": str(home), "HERMES_HOME": str(home),
            "WORLD_ACTION_AUTH_REGISTRY_DIR": str(run_dir),
            "WORLD_ACTION_LIFECYCLE_PATH": str(run_dir / "action_lifecycle.jsonl"),
            "WORLD_ACTIVITY_STATE": str(run_dir / "activity_state.json"),
            "WORLD_EXPERIENCE_SPOOL": str(run_dir / "experience_candidates.jsonl"),
            "WORLD_SELF_MAINTENANCE_STATE": str(run_dir / "self_maintenance_state.json")}


def _aliases(root: Path | str, env: Mapping[str, str]) -> dict[str, str]:
    """同一 owner 的另一种拼写（与既有 v05 装配一致），不新增权限。"""

    out: dict[str, str] = {}
    try:
        import one_day_life as odl

        data = odl.read_instance(Path(str(root)) / "one-day")
    except Exception:
        return out
    pairs = {
        "NYAIRO_LIFE_SUBJECT": data.get("subject_id") or env.get("NYA02_SUBJECT"),
        "NYAIRO_LIFE_WORKSPACE_ID": (data.get("workspace_id")
                                     or env.get("CHIYO_LIFE_SUPPLY_WORKSPACE_ID")),
        "NYAIRO_ARTIFACT_GRANT": (data.get("artifact_grant_id")
                                  or env.get("NYA02_ARTIFACT_GRANT")),
        "NYAIRO_LIFE_SUPPLY_SOCKET": (env.get("LIFE_SUPPLY_SOCKET")
                                      or env.get("CHIYO_NATIVE_LIFE_SUPPLY_SOCKET")),
    }
    if data.get("private_items_artifact_id"):
        pairs["NYAIRO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID"] = data["private_items_artifact_id"]
    if data.get("reading_artifact_id"):
        pairs["NYAIRO_LIFE_READING_ARTIFACT_ID"] = data["reading_artifact_id"]
    for name, value in pairs.items():
        if str(value or "").strip():
            out[name] = str(value)
    return out


def _offline_model_env(profile: LiveProfile) -> dict[str, str]:
    return {"NYA02_MODEL_PROVIDER": "custom",
            "NYA02_MODEL_BASE_URL": "https://offline.invalid/v1",
            "NYA02_MODEL_NAME": "offline-model",
            "NYA02_MODEL_API_KEY": "offline-not-a-key",
            "CHIYO_AUTONOMOUS_RUN_ID": profile.run_id}


def live_env(profile: LiveProfile, *, offline: bool = False, fixtures: bool = False,
             base_env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """本批 main 实例的运行时环境：run 身份、gate off、World 写面钉在 main 内。"""

    from components.phone import life as phone_life

    root = profile.main_root
    phone = phone_life.load_phone_instance(root)
    nested = phone_life.read_nested_life(root)
    if nested is None:
        raise LiveRefused(R_NOT_PREPARED, "no nested life instance")
    binding = phone_life.build_binding(
        phone, nested, base_env=dict(base_env if base_env is not None else os.environ))
    env = dict(binding["environment"])
    if str(env.get("CHIYO_AUTONOMOUS_RUN_ID") or "") != profile.run_id:
        raise LiveRefused(R_IDENTITY_MISMATCH, "instance run identity differs")
    env.update(_aliases(root, env))
    env[GATE_ENV] = GATE_OFF
    if profile.exploratory:
        env[AMBIENT_ENV] = AMBIENT_OFF
    env.update(_world_isolation_env(_require_world_home(root)))
    if offline:
        for name in [item for item in env if item.startswith("NYA02_MODEL_")]:
            env.pop(name, None)
        env.update(_offline_model_env(profile))
    if fixtures:
        env[ENV_CHECK_INTERVAL] = "0"
    elif profile.check_interval_minutes is not None:
        env[ENV_CHECK_INTERVAL] = str(int(profile.check_interval_minutes))
    return env


def branch_env(profile: LiveProfile, branch: Path | str, *, fixtures: bool = False,
               offline: bool = True) -> dict[str, str]:
    """分支实例的环境：同样 gate off，所有写面钉在分支内（authority 精确共享）。"""

    from components.phone import life as phone_life

    branch_path = Path(str(branch))
    phone = phone_life.load_phone_instance(branch_path)
    nested = phone_life.read_nested_life(branch_path)
    if nested is None:
        raise LiveRefused(R_NOT_PREPARED, "no nested life instance in the branch")
    binding = phone_life.build_binding(
        phone, nested, base_env=dict(os.environ))
    env = dict(binding["environment"])
    if str(env.get("CHIYO_AUTONOMOUS_RUN_ID") or "") != profile.run_id:
        raise LiveRefused(R_IDENTITY_MISMATCH, "branch run identity differs")
    env.update(_aliases(branch_path, env))
    env[GATE_ENV] = GATE_OFF
    if profile.exploratory:
        env[AMBIENT_ENV] = AMBIENT_OFF
    env.update(_world_isolation_env(_require_world_home(branch_path)))
    if offline:
        for name in [item for item in env if item.startswith("NYA02_MODEL_")]:
            env.pop(name, None)
        env.update(_offline_model_env(profile))
    if fixtures:
        env[ENV_CHECK_INTERVAL] = "0"
    elif profile.check_interval_minutes is not None:
        env[ENV_CHECK_INTERVAL] = str(int(profile.check_interval_minutes))
    return env


#: 实际 phase env 里必须存在、且必须解析在实例根内的写面。
_REQUIRED_INSTANCE_PATHS = (
    "CHIYO_WORLD_HOME", "HERMES_HOME", "WORLD_ACTION_AUTH_REGISTRY_DIR",
    "WORLD_ACTION_LIFECYCLE_PATH", "WORLD_ACTIVITY_STATE", "WORLD_EXPERIENCE_SPOOL",
    "WORLD_SELF_MAINTENANCE_STATE", "WORLD_BODY_SOCKET",
    "CHIYO_LIFE_INSTALL_ROOT", "NYA02_RESIDENT_STATE",
    "NYA02_MEMORY_DB", "NYA02_MEMORY_CONTROLS_DB",
)
_REQUIRED_SUPPLY_SOCKETS = ("LIFE_SUPPLY_SOCKET", "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET")


def isolation_view(env: Mapping[str, str], instance_root: Path | str, *,
                   batch_root: Path | str, authority_db: Path | str) -> dict[str, Any]:
    """实际进程 env 的写面解析：所有相关路径必须在实例根内，authority 必须精确共享。

    缺项、相对路径、根外路径与符号链接逃逸都算失败；不接受「缺了就跳过」。
    """

    instance = Path(str(instance_root))
    batch = Path(str(batch_root))
    authority = Path(str(authority_db))
    world_home = str(env.get("CHIYO_WORLD_HOME") or env.get("HERMES_HOME") or "").strip()
    checks: dict[str, bool] = {"gate_is_off": str(env.get(GATE_ENV) or "") == GATE_OFF}
    missing: list[str] = []
    not_absolute: list[str] = []
    outside: list[str] = []

    def check_path(name: str) -> None:
        value = str(env.get(name) or "").strip()
        if not value:
            missing.append(name)
            return
        path = Path(value)
        if not path.is_absolute():
            not_absolute.append(name)
            return
        ok = _within(path, instance)
        checks["%s_is_within_instance" % name.lower()] = ok
        if not ok:
            outside.append(name)

    for name in _REQUIRED_INSTANCE_PATHS:
        check_path(name)
    supply_present = False
    for name in _REQUIRED_SUPPLY_SOCKETS:
        value = str(env.get(name) or "").strip()
        if not value:
            continue
        supply_present = True
        path = Path(value)
        if not path.is_absolute():
            not_absolute.append(name)
            continue
        ok = _within(path, instance)
        checks["%s_is_within_instance" % name.lower()] = ok
        if not ok:
            outside.append(name)
    checks["supply_socket_is_present"] = supply_present

    authority_values = [str(env.get(name) or "").strip() for name in _MEMORY_AUTHORITY_NAMES]
    present_authority = [value for value in authority_values if value]
    checks["authority_is_present"] = bool(present_authority)
    checks["authority_is_the_shared_experiment_path"] = bool(present_authority) and all(
        Path(value).is_absolute() and Path(value).resolve() == authority.resolve()
        for value in present_authority)
    checks["authority_is_within_batch_root"] = _within(authority, batch)
    checks["no_missing_paths"] = not missing
    checks["all_paths_absolute"] = not not_absolute
    checks["all_paths_within_instance"] = not outside
    blocking = [name for name, value in checks.items() if not value]
    return {"ok": not blocking, "blocking": blocking, "checks": checks,
            "missing": missing, "not_absolute": not_absolute, "outside": outside,
            "gate": env.get(GATE_ENV), "world_home": world_home or None,
            "instance_root": str(instance), "authority": str(authority),
            "batch_root": str(batch)}


def validate_isolation(env: Mapping[str, str], instance_root: Path | str, *,
                       batch_root: Path | str, authority_db: Path | str) -> dict[str, Any]:
    view = isolation_view(env, instance_root, batch_root=batch_root,
                          authority_db=authority_db)
    if not view.get("ok"):
        raise LiveRefused(R_ISOLATION_UNPROVEN, ",".join(view.get("blocking") or []))
    return view


def _verify_model_config(env: Mapping[str, str]) -> dict[str, Any]:
    import autonomous_life_assembly as assembly

    config = assembly._model_config(env)
    if not config:
        return {"ok": False, "reason_code": R_NO_MODEL_CONFIG}
    actual = {"provider": str(config.get("provider") or "custom"),
              "base_url": str(config.get("base_url") or "").rstrip("/"),
              "model": str(config.get("model") or "")}
    wanted = {"provider": MODEL_PROVIDER, "base_url": MODEL_BASE_URL.rstrip("/"),
              "model": MODEL_NAME}
    if actual != wanted:
        return {"ok": False, "reason_code": R_MODEL_PARAM_MISMATCH,
                "expected": wanted, "actual": actual}
    if (baseline.MODEL_TEMPERATURE != MODEL_TEMPERATURE
            or bool(baseline.MODEL_STREAM) != bool(MODEL_STREAM)
            or int(baseline.DECISION_OUTPUT_CAP) != DECISION_OUTPUT_CAP
            or int(baseline.CONTENT_OUTPUT_CAP) != CONTENT_OUTPUT_CAP):
        return {"ok": False, "reason_code": R_MODEL_PARAM_MISMATCH,
                "expected": {"temperature": MODEL_TEMPERATURE, "stream": MODEL_STREAM,
                             "decision_output_cap": DECISION_OUTPUT_CAP,
                             "content_output_cap": CONTENT_OUTPUT_CAP}}
    return {"ok": True, "provider": actual["provider"], "base_url": actual["base_url"],
            "model": actual["model"]}


# ---------------------------------------------------------------------------------
# H 检测：owner 版本与正式 M0 经历必须精确对齐
# ---------------------------------------------------------------------------------

def _bare_version(value: Any) -> str:
    text = str(value or "")
    for prefix in ("artifactversion:", "artifact-version:"):
        while text.startswith(prefix):
            text = text[len(prefix):]
    return text


class ExactHistoryDetector:
    """首个 owner 确认 version_created=True、且同一 artifact/version 的正式 M0 经历可读。"""

    RULE = ("first owner-confirmed version_created=True whose exact artifact/version has a "
            "readable formal M0 experience")

    def __init__(self, *, candidate_read: Any, memory_port: Any) -> None:
        self.candidate_read = candidate_read
        self.memory_port = memory_port
        self.last_mismatch: dict[str, Any] = {}
        #: artifact ref -> 该 H 被确证时的 owner 版本；冻结复查用它做精确 version 对齐。
        self.exact: dict[str, str] = {}

    def _artifact_targets(self, candidate_ref: Optional[str]) -> list[str]:
        if not candidate_ref or self.candidate_read is None:
            return []
        try:
            row = self.candidate_read.get_candidate(str(candidate_ref)) or {}
        except Exception:
            return []
        return [str(ref) for ref in (row.get("target_refs") or ())
                if str(ref).startswith("artifact:")]

    def candidate(self, advance: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        extra = advance.get("owner") or {}
        if extra.get("owner_version_created") is not True:
            return None
        selected = advance.get("selected_candidate_ref")
        owner_version = extra.get("owner_version_id")
        for target in self._artifact_targets(selected):
            evidence = baseline.m0_has_artifact(self.memory_port, target)
            same_version = (_bare_version(owner_version)
                            and _bare_version(owner_version)
                            == _bare_version(evidence.get("version_ref")))
            if evidence.get("readable") and same_version:
                self.last_mismatch = {}
                self.exact[str(target)] = str(owner_version or "")
                return {"artifact_ref": target, "owner_version_id": owner_version,
                        "experience_id": evidence.get("experience_id"),
                        "experience_version_ref": evidence.get("version_ref"),
                        "selected_candidate_ref": selected}
            if evidence.get("readable"):
                self.last_mismatch = {"artifact_ref": target,
                                      "owner_version_id": owner_version,
                                      "experience_version_ref": evidence.get("version_ref"),
                                      "reason_code": "EXPERIENCE_VERSION_MISMATCH"}
        return None


# ---------------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------------

def _start_supply(root: Path | str, tag: str) -> Any:
    return baseline.start_supply_owner(root, tag=tag)


def _stop_quietly(service: Any) -> None:
    if service is None:
        return
    try:
        service.stop()
    except Exception:
        pass


#: 维护观察行的固定字段（只写这些；不写 prompt/正文/异常原文）。
TRACE_FIELDS = ("tick", "observed_at", "checked", "changed", "satisfied",
                "emitted_eligibilities", "feed_events", "world_revision_changed",
                "world_revision_basis", "world_signature_changed", "world_signature_basis",
                "quiet_reason", "status")


def _maintain_trace_observer(path: Path | str) -> Callable[[Mapping[str, Any]], None]:
    """每 tick 一行维护摘要，追加到本实验根内；写失败由调用方记类型，不改变控制流。"""

    target = Path(str(path))

    def observe(row: Mapping[str, Any]) -> None:
        safe = {key: row.get(key) for key in TRACE_FIELDS}
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    return observe


def _provision_works(profile: LiveProfile, *, subject: str, workspace_id: str,
                     grant_id: str, supply_socket: str) -> list[dict[str, Any]]:
    from life_artifact_port import LifeArtifactPort

    if len(baseline.WORK_SEED_TEXT) != len(baseline.WORK_SEED_TEXT_2):
        raise LiveRefused(R_PREPARE_FAILED, "seed texts differ in length")
    port = LifeArtifactPort(supply_socket, subject)
    created: list[dict[str, Any]] = []
    for index, (title, text) in enumerate(zip(baseline.WORK_TITLES,
                                              (baseline.WORK_SEED_TEXT,
                                               baseline.WORK_SEED_TEXT_2))):
        outcome = port.create_artifact(
            workspace_id=workspace_id, title=title, content=text, grant_id=grant_id,
            operation_key="%s:work:%d" % (profile.run_name, index))
        if not outcome.applied or not outcome.artifact_id:
            raise LiveRefused(R_WORK_CREATE_FAILED, str(outcome.status)[:40])
        created.append({"title": title, "artifact_id": str(outcome.artifact_id),
                        "version_id": str(outcome.version_id or ""),
                        "status": str(outcome.status)})
    return created


def _state_root(profile: LiveProfile) -> Path:
    from components.phone import life as phone_life

    phone = phone_life.load_phone_instance(profile.main_root)
    nested = phone_life.read_nested_life(profile.main_root)
    if nested is None:
        raise LiveRefused(R_NOT_PREPARED, "no nested life instance")
    binding = phone_life.build_binding(phone, nested, base_env=dict(os.environ))
    raw = str(binding["environment"].get("NYA02_RESIDENT_STATE") or "")
    root = Path(raw) if raw else None
    if root is None or not _within(root, profile.main_root):
        raise LiveRefused(R_PREPARE_FAILED, "state root is not inside the main instance")
    return root


def _nested_check_interval(profile: LiveProfile) -> Optional[int]:
    """读实例持久环境里的检查节拍（分钟）；缺项/坏值返回 None，不猜。"""

    from components.phone import life as phone_life

    try:
        nested = phone_life.read_nested_life(profile.main_root)
    except Exception:
        return None
    raw = str(((nested or {}).get("environment") or {}).get(ENV_CHECK_INTERVAL) or "").strip()
    try:
        return int(raw)
    except ValueError:
        return None


def _persist_check_interval(profile: LiveProfile) -> Optional[int]:
    """把本批的检查节拍写进实例自己的持久环境；未配置则完全不动（a/b/c 不变）。"""

    if profile.check_interval_minutes is None:
        return None
    import one_day_life as odl
    from components.phone import life as phone_life

    nested = phone_life.read_nested_life(profile.main_root)
    if nested is None:
        raise LiveRefused(R_PREPARE_FAILED, "no nested life instance")
    value = int(profile.check_interval_minutes)
    environment = dict(nested.get("environment") or {})
    environment[ENV_CHECK_INTERVAL] = str(value)
    nested["environment"] = environment
    target = odl.instance_path(phone_life.nested_life_dir(profile.main_root))
    _write_json(target, nested)
    return value


def prepare(profile: Optional[LiveProfile] = None, *, offline_fixtures: bool = False,
            root: Optional[str | Path] = None, batch: str = BATCH_DEFAULT) -> dict[str, Any]:
    """建一次本批实例：两件中性作品、真 owner、独立 Memory authority、52 持久窗口。0 产品调用。"""

    base, refusal = _resolve_profile(offline_fixtures=offline_fixtures, root=root,
                                     batch=batch)
    if refusal is not None:
        return refusal
    # 夹具模式保留调用方的 profile 字段（deadline 等夹具值），但根与 trace 必须落在
    # 解析出的 scratch 副本里 —— 绝不把传入的正式 profile 写回正式根。
    if offline_fixtures:
        if profile is None:
            profile = base
        else:
            profile = replace(profile, batch_root=base.batch_root,
                              maintain_trace_path=base.maintain_trace_path)
    else:
        profile = profile or base
    if not offline_fixtures and profile.batch_root != formal_profile(profile.batch_key).batch_root:
        return {"ok": False, "reason_code": R_FIXTURE_ROOT_REFUSED,
                "detail": "the real path never writes a custom root", "fixture": False}
    if _exists(profile.prereg_path):
        return {"ok": False, "reason_code": R_ALREADY_PREPARED,
                "root": str(profile.batch_root), "fixture": bool(offline_fixtures)}
    try:
        authority_taken = bool(_exists(profile.authority_dir)
                               and any(profile.authority_dir.iterdir()))
    except OSError:
        authority_taken = True
    if authority_taken:
        return {"ok": False, "reason_code": R_AUTHORITY_NOT_FRESH,
                "authority": str(profile.authority_dir), "fixture": bool(offline_fixtures)}
    if _exists(profile.batch_root):
        return {"ok": False, "reason_code": R_ROOT_NOT_FRESH, "root": str(profile.batch_root),
                "fixture": bool(offline_fixtures)}
    for sub in (profile.main_root, profile.authority_dir, profile.branches_root):
        if _exists(sub):
            return {"ok": False, "reason_code": R_SUBROOT_EXISTS, "path": str(sub),
                    "fixture": bool(offline_fixtures)}

    from components.phone import demo, life as phone_life, service as phone_service

    try:
        profile.batch_root.mkdir(parents=True, exist_ok=False)
        try:
            os.chmod(profile.batch_root, 0o700)
        except OSError:
            pass
        profile.run_dir.mkdir(parents=True, exist_ok=True)
        profile.experiment_dir.mkdir(parents=True, exist_ok=True)
        profile.branches_root.mkdir(parents=True, exist_ok=True)
        prepared = demo.prepare_demo(
            profile.main_root, with_world=True, seed_work_artifact=False,
            reading_title="", reading_content=None, run_id=profile.run_id,
            request_utc_day_max=profile.utc_day_cap,
            request_run_window_max=profile.request_cap,
            max_cognition_calls_per_day=profile.utc_day_cap)
    except Exception as exc:
        return {"ok": False, "reason_code": R_PREPARE_FAILED,
                "kind": type(exc).__name__, "fixture": bool(offline_fixtures)}
    if not isinstance(prepared, Mapping) or prepared.get("ok") is not True:
        return {"ok": False, "reason_code": R_PREPARE_FAILED, "fixture": bool(offline_fixtures)}

    # 检查节拍必须写进实例的持久配置（d2=10）；a/b/c 的 check_interval 为 None，完全不碰。
    try:
        persisted_check = _persist_check_interval(profile)
    except LiveRefused as exc:
        return {"ok": False, "reason_code": exc.reason_code, "fixture": bool(offline_fixtures)}
    except Exception as exc:
        return {"ok": False, "reason_code": R_PREPARE_FAILED,
                "kind": type(exc).__name__, "fixture": bool(offline_fixtures)}

    instance = phone_service.load_instance(profile.main_root)
    subject = str(instance.get("subject") or "")
    workspace_id = str(instance.get("workspace_id") or "")
    grant_id = str(instance.get("artifact_grant_id") or "")
    if not (subject and workspace_id and grant_id):
        return {"ok": False, "reason_code": R_PREPARE_FAILED,
                "detail": "instance lacks subject/workspace/grant",
                "fixture": bool(offline_fixtures)}

    supply = None
    try:
        supply = _start_supply(profile.main_root, "g2-live-prepare")
        works = _provision_works(profile, subject=subject, workspace_id=workspace_id,
                                 grant_id=grant_id, supply_socket=str(supply.socket_path))
    except LiveRefused as exc:
        return {"ok": False, "reason_code": exc.reason_code, "fixture": bool(offline_fixtures)}
    except Exception as exc:
        return {"ok": False, "reason_code": R_WORK_CREATE_FAILED,
                "kind": type(exc).__name__, "fixture": bool(offline_fixtures)}
    finally:
        _stop_quietly(supply)

    try:
        profile.authority_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(profile.authority_dir, 0o700)
        except OSError:
            pass
        memory = phone_life.enable_instance_memory(
            profile.main_root, subject, "%s:memory" % profile.run_name,
            authority_path=profile.authority_db)
    except Exception as exc:
        return {"ok": False, "reason_code": R_MEMORY_BIND_FAILED,
                "kind": type(exc).__name__, "fixture": bool(offline_fixtures)}
    if memory.get("ok") is not True:
        return {"ok": False, "reason_code": R_MEMORY_BIND_FAILED,
                "status": str(memory.get("status") or memory.get("reason") or "")[:64],
                "fixture": bool(offline_fixtures)}

    try:
        state_root = _state_root(profile)
        budget = _budget(profile, state_root)
        budget.ensure_window(allow_new=True)
        view = budget.view()
        if int(view.get("run_window_calls") or 0) != 0:
            return {"ok": False, "reason_code": R_LEDGER_NOT_ZERO,
                    "ledger": _safe_budget(view), "fixture": bool(offline_fixtures)}
    except Exception as exc:
        return {"ok": False, "reason_code": R_LEDGER_NOT_ZERO,
                "kind": type(exc).__name__, "fixture": bool(offline_fixtures)}

    prereg = {
        "schema": "g2.natural.live.prereg.v1", "batch": profile.run_name,
        "run_id": profile.run_id, "domain_id": profile.domain_id,
        "root": str(profile.batch_root), "main": str(profile.main_root),
        "state_root": str(state_root), "authority": str(profile.authority_db),
        "branches": str(profile.branches_root), "subject": subject,
        "workspace_id": workspace_id, "artifact_grant_id": grant_id, "works": works,
        "history": {"used": profile.expected_history,
                    "total_cap": (profile.cumulative_total
                                  if profile.cumulative_total is not None
                                  else profile.utc_day_cap),
                    "utc_day_cap": profile.utc_day_cap,
                    "new_cap": profile.request_cap},
        "check_interval_minutes": persisted_check,
        "cumulative_total": profile.cumulative_total,
        "caps": profile.caps(), "model_params": profile.model_params(),
        "deadline": profile.deadline.isoformat(),
        "latest_start": (profile.latest_start.isoformat()
                         if profile.latest_start is not None else None),
        "prepared_at": _iso(),
        "fixture": bool(offline_fixtures),
        "note": ("固定身份/参数/预算在第一次真实请求之前写在这里；之后只读。"
                 "本批不 source 密钥、不加载模型配置。"),
    }
    _write_json(profile.prereg_path, prereg)
    _write_json(profile.batch_path, {
        "schema": "g2.natural.batch.v1", "batch": profile.run_name,
        "run_id": profile.run_id, "domain_id": profile.domain_id,
        "state": "prepared", "prepared_at": _iso(), "root": str(profile.batch_root),
        "state_root": str(state_root), "subject": subject, "run": None, "stop": None,
        "fixture": bool(offline_fixtures),
    })
    return {"ok": True, "fixture": bool(offline_fixtures), "batch": profile.run_name,
            "run_id": profile.run_id, "domain_id": profile.domain_id,
            "state": "prepared", "started_at": None, "history": profile.expected_history,
            "caps": profile.caps(), "model_params": profile.model_params(),
            "root": str(profile.batch_root), "main": str(profile.main_root),
            "authority": str(profile.authority_db), "branches": str(profile.branches_root),
            "state_root": str(state_root),
            "artifacts": [row["artifact_id"] for row in works],
            "check_interval_minutes": persisted_check,
            "ledger": {"run_window_calls": 0, "run_window_max": profile.request_cap,
                       "state": "ok"}}


# ---------------------------------------------------------------------------------
# audit（纯读）
# ---------------------------------------------------------------------------------

def _window_view(profile: LiveProfile) -> dict[str, Any]:
    window = _read_json(profile.window_path)
    if not window:
        return {"present": False}
    return {"present": True, "run_id": window.get("run_id"),
            "window_seconds": window.get("window_seconds"),
            "deadline_monotonic": window.get("deadline_monotonic"),
            "started_wall": window.get("started_wall"),
            "deadline_wall": window.get("deadline_wall")}


def _deadline_passed(profile: LiveProfile) -> bool:
    window = _read_json(profile.window_path)
    if not window:
        return True
    try:
        if time.monotonic() >= float(window["deadline_monotonic"]):
            return True
        wall = datetime.fromisoformat(str(window["deadline_wall"]))
    except (KeyError, TypeError, ValueError):
        return True
    return datetime.now(timezone.utc) >= wall


def audit(profile: Optional[LiveProfile] = None, *, offline_fixtures: bool = False,
          root: Optional[str | Path] = None, batch: str = BATCH_DEFAULT) -> dict[str, Any]:
    """纯读审计：不建目录/库、不取账本锁、不起服务、不 claim lease、不加载密钥。"""

    base, refusal = _resolve_profile(offline_fixtures=offline_fixtures, root=root,
                                     batch=batch)
    if refusal is not None:
        return refusal
    # 夹具模式保留调用方的 profile 字段（deadline 等夹具值），但根与 trace 必须落在
    # 解析出的 scratch 副本里 —— 绝不把传入的正式 profile 写回正式根。
    if offline_fixtures:
        if profile is None:
            profile = base
        else:
            profile = replace(profile, batch_root=base.batch_root,
                              maintain_trace_path=base.maintain_trace_path)
    else:
        profile = profile or base
    if not offline_fixtures and profile.batch_root != formal_profile(profile.batch_key).batch_root:
        return {"ok": False, "reason_code": R_FIXTURE_ROOT_REFUSED, "fixture": False}
    if not _exists(profile.prereg_path):
        return {"ok": False, "reason_code": R_NOT_PREPARED, "fixture": bool(offline_fixtures),
                "root": str(profile.batch_root)}
    prereg = _read_json(profile.prereg_path)
    bad = _identity_refusal(profile, prereg)
    if bad is not None:
        return {**bad, "fixture": bool(offline_fixtures)}
    persisted_check = _nested_check_interval(profile)
    if (profile.check_interval_minutes is not None
            and persisted_check != int(profile.check_interval_minutes)):
        return {"ok": False, "reason_code": R_IDENTITY_MISMATCH,
                "field": "check_interval_minutes",
                "check_interval_minutes": profile.check_interval_minutes,
                "persisted_check_interval_minutes": persisted_check,
                "fixture": bool(offline_fixtures)}
    batch = _read_json(profile.batch_path)
    if not batch:
        return {"ok": False, "reason_code": R_HALF_STATE, "fixture": bool(offline_fixtures)}
    state_root_text = str(prereg.get("state_root") or "")
    state_root = Path(state_root_text) if state_root_text else None
    if state_root is None or not _within(state_root, profile.main_root):
        return {"ok": False, "reason_code": R_HALF_STATE, "field": "state_root",
                "fixture": bool(offline_fixtures)}
    ledger = _read_ledger_direct(profile, state_root)
    window = _window_view(profile)
    started = (batch.get("run") or {}).get("started_at")
    state = str(batch.get("state") or "")

    if not started:
        blocking: list[str] = []
        if state != "prepared":
            blocking.append("state_is_not_prepared")
        if window.get("present"):
            blocking.append("run_window_present_before_start")
        if ledger.get("run_window_calls") is None:
            blocking.append("ledger_unreadable")
        elif int(ledger.get("run_window_calls") or 0) != 0:
            blocking.append("ledger_not_zero")
        if blocking:
            reason = R_LEDGER_UNREADABLE if "ledger_unreadable" in blocking else (
                R_LEDGER_NOT_ZERO if "ledger_not_zero" in blocking else R_HALF_STATE)
            return {"ok": False, "reason_code": reason, "blocking": blocking,
                    "state": state, "started_at": None, "window": window,
                    "ledger": ledger, "fixture": bool(offline_fixtures)}
    elif ledger.get("run_window_calls") is None:
        return {"ok": False, "reason_code": R_LEDGER_UNREADABLE, "state": state,
                "started_at": started, "window": window, "ledger": ledger,
                "fixture": bool(offline_fixtures)}

    return {
        "ok": True, "fixture": bool(offline_fixtures), "batch": profile.run_name,
        "run_id": profile.run_id, "domain_id": profile.domain_id,
        "root": str(profile.batch_root), "main": str(profile.main_root),
        "authority": str(profile.authority_db), "branches": str(profile.branches_root),
        "state_root": str(state_root),
        "state": state, "prepared_at": batch.get("prepared_at"),
        "started_at": started, "window": window, "ledger": _safe_budget(ledger),
        "check_interval_minutes": profile.check_interval_minutes,
        "persisted_check_interval_minutes": persisted_check,
        "cumulative_total": profile.cumulative_total,
        "history": profile.expected_history, "caps": profile.caps(),
        "model_params": profile.model_params(), "deadline": profile.deadline.isoformat(),
        "latest_start": (profile.latest_start.isoformat()
                         if profile.latest_start is not None else None),
        "stop": batch.get("stop"), "isolation": {
            "gate": GATE_OFF, "main": str(profile.main_root),
            "authority": str(profile.authority_db), "branches": str(profile.branches_root),
            "all_within_root": all(_within(path, profile.batch_root) for path in (
                profile.main_root, profile.authority_db, profile.branches_root))},
    }


# ---------------------------------------------------------------------------------
# 自然采集、影子、承接
# ---------------------------------------------------------------------------------

def _plan_for(profile: LiveProfile, *, acquisition_window: Optional[int] = None,
              max_observations: Optional[int] = None):
    return replace(
        v2.DEFAULT_PLAN, run_name=profile.run_name, run_id=profile.run_id,
        domain_id=profile.domain_id, request_cap=profile.acquisition_cap,
        window_seconds=int(acquisition_window if acquisition_window is not None
                           else profile.acquisition_window_seconds),
        observation_interval_seconds=profile.observation_interval_seconds,
        max_observations=int(max_observations if max_observations is not None
                             else profile.max_observations),
        future_candidate_total=profile.request_cap,
        future_shadow_requests=profile.shadow_cap,
        future_continuation_requests=profile.continuation_cap,
        future_diagnostic_requests=profile.diagnostic_cap,
        arm_request_cap=profile.arm_cap, utc_day_cap=profile.utc_day_cap,
        real_root=str(profile.batch_root))


def _advance(bundle: Mapping[str, Any],
             fixture_hook: Optional[Callable[..., None]] = None,
             latch: Any = None) -> Callable[[], Mapping[str, Any]]:
    tick_index = {"n": 0}
    inner = v2._advance_from_runner(bundle)

    def advance() -> Mapping[str, Any]:
        tick_index["n"] += 1
        if fixture_hook is not None:
            try:
                fixture_hook(tick_index["n"], bundle)
            except TypeError:
                fixture_hook(tick_index["n"])
        result = inner()
        # v2 会把 detail 归一到 detail_code，传输失败的 marker 就在这里先抓一次，
        # 让发送锁在下一轮 reserve/transport 之前持久关闭。
        detail = str((result or {}).get("detail") or "")
        if latch is not None and any(marker in detail
                                     for marker in baseline._TRANSPORT_FAILURE_MARKERS):
            latch.stop(R_PROVIDER_FAILURE)
        return result

    return advance


def _h_evidence_view(detector: Optional[ExactHistoryDetector],
                     summary: Mapping[str, Any]) -> dict[str, Any]:
    detail = dict(summary.get("h_detail") or {})
    owner_version = detail.get("owner_version_id")
    experience_version = detail.get("experience_version_ref")
    return {"artifact_ref": summary.get("h_ref"),
            "owner_version_id": owner_version,
            "experience_id": detail.get("experience_id"),
            "experience_version_ref": experience_version,
            "version_aligned": bool(owner_version and experience_version
                                    and _bare_version(owner_version)
                                    == _bare_version(experience_version)),
            "mismatch": dict(getattr(detector, "last_mismatch", {}) or {})}


def _h_activity(frozen: Optional[Mapping[str, Any]], h_ref: str,
                runtime: Any = None) -> str:
    """H 的冻结 Activity：优先取冻结经历里的 activity_ref（PAUSED 时前台为空）。"""

    bare = _bare_artifact(h_ref)
    for row in (frozen or {}).get("prior_experiences") or ():
        if not isinstance(row, Mapping):
            continue
        if bare and bare in _bare_artifact(row.get("artifact_ref")):
            value = str(row.get("activity_ref") or "")
            if value:
                return value
    if runtime is not None:
        try:
            return str(runtime.current_activity_ref() or "")
        except Exception:
            return ""
    return ""


def _target_observed_at(frozen: Mapping[str, Any]) -> str:
    """夹具承接：把 observed_at 推到既有 reconsideration TTL 之后（同状态重考，不是清 quiet）。"""

    from life_step_readiness import DEFAULT_TTL_SECONDS

    raw = str(frozen.get("observed_at") or "")
    moment = datetime.fromisoformat(raw) if raw else datetime.now(timezone.utc)
    return (moment + timedelta(seconds=int(DEFAULT_TTL_SECONDS) + 120)).isoformat()


def _conclusion_from_stats(stats: Mapping[str, Any]) -> str:
    raw = str((stats or {}).get("conclusion") or "")
    if raw == "HISTORY_PACKAGE_CHANGED_H_CHOICE":
        return RESULT_DETECTED
    if raw == "NO_DETECTED_DIFFERENCE":
        return RESULT_NO_DIFFERENCE
    if raw == "EXPLORATORY_ONLY":
        return RESULT_EXPLORATORY
    return RESULT_NOT_TESTED


def _shadow_targets_for_arm(shadow: Mapping[str, Any], arm: str) -> list[dict[str, Any]]:
    return [row for row in (shadow.get("samples") or []) if str(row.get("arm")) == arm]


def _shadow_choice(shadow: Mapping[str, Any], gate_targets: Sequence[Mapping[str, Any]],
                   h_ref: str, arm: str) -> dict[str, Any]:
    rows = _shadow_targets_for_arm(shadow, arm)
    if not rows:
        return {"ok": False, "reason_code": R_SHADOW_CHOICE_NOT_EXECUTABLE,
                "detail": "no shadow sample for this arm"}
    row = sorted(rows, key=lambda item: int(item.get("pair") or 0))[0]
    decision = str(row.get("decision") or "")
    selected = str(row.get("selected_candidate_ref") or "")
    if not selected or not decision:
        return {"ok": False, "reason_code": R_SHADOW_CHOICE_NOT_EXECUTABLE,
                "decision": decision or None}
    info = next((item for item in gate_targets
                 if str(item.get("candidate_id") or "") == selected), None)
    if info is None:
        return {"ok": False, "reason_code": R_SHADOW_CHOICE_UNMAPPED,
                "selected_candidate_ref": selected}
    targets = [str(ref) for ref in (info.get("targets") or ())]
    legal = [str(code) for code in (info.get("legal") or ())]
    if _bare_artifact(h_ref) not in {_bare_artifact(ref) for ref in targets}:
        return {"ok": False, "reason_code": R_SHADOW_TARGET_MISMATCH,
                "selected_candidate_ref": selected, "targets": targets}
    if decision not in ("CONTINUE", "RESUME") or decision not in legal:
        return {"ok": False, "reason_code": R_SHADOW_CHOICE_NOT_EXECUTABLE,
                "selected_candidate_ref": selected, "decision": decision,
                "legal": legal}
    return {"ok": True, "decision": decision, "selected_candidate_ref": selected,
            "targets": targets, "legal": legal, "pair": row.get("pair")}


def _bare_artifact(ref: Any) -> str:
    return baseline._bare_artifact(ref)


def _open_eligibility_for(state_root: Path | str, activity_ref: str) -> bool:
    state_file = Path(str(state_root)) / "waiting_agenda" / "eligibility" \
        / "resume_eligibility_state.json"
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    rows = (data or {}).get("eligibilities") or {}
    return any(isinstance(row, Mapping) and str(row.get("status")) == "OPEN"
               and str(row.get("activity_id") or "") == activity_ref
               for row in rows.values())


def _execution_precheck(runtime: Any, state_root: Path | str, choice: Mapping[str, Any],
                        h_ref: str, activity_ref: str, observed_at: str) -> dict[str, Any]:
    checks: dict[str, bool] = {
        "shadow_decision_is_executable": choice.get("decision") in ("CONTINUE", "RESUME"),
        "shadow_candidate_targets_the_frozen_work": _bare_artifact(h_ref) in {
            _bare_artifact(ref) for ref in (choice.get("targets") or ())},
        "chosen_decision_is_legal": choice.get("decision") in set(choice.get("legal") or ()),
    }
    current = runtime.current_activity_ref()
    if choice.get("decision") == "CONTINUE":
        checks["foreground_is_the_frozen_activity"] = bool(activity_ref) and current == activity_ref
    else:
        activity = runtime.activity_read.get_activity(str(activity_ref)) or {}
        checks["frozen_activity_is_paused"] = str(activity.get("status") or "") == "PAUSED"
        checks["foreground_is_not_another_activity"] = current in (None, "", activity_ref)
        checks["open_eligibility_for_frozen_activity"] = _open_eligibility_for(
            state_root, str(activity_ref))
    present: list[Mapping[str, Any]] = []
    try:
        material = runtime.candidate_materializer.build_candidate_set(observed_at=observed_at)
        for ref in list(getattr(material, "candidate_refs", []) or []):
            payload = runtime.candidate_read.get_candidate(str(ref)) or {}
            if str(payload.get("candidate_id") or ref) == str(choice.get("selected_candidate_ref")):
                present.append(payload)
    except Exception:
        present = []
    checks["shadow_candidate_is_still_present_at_the_same_work"] = bool(present) and any(
        _bare_artifact(h_ref) in {_bare_artifact(ref) for ref in (row.get("target_refs") or ())}
        for row in present)
    blocking = [name for name, value in checks.items() if not value]
    return {"ok": not blocking, "blocking": blocking, "checks": checks,
            "candidate_id": choice.get("selected_candidate_ref"),
            "foreground": current}


class _ScriptedChoiceTransport(baseline.CaptureTransport):
    """夹具：只对同一冻结目标按影子选择作答；不做第二变量、不 fallback。"""

    def __init__(self, decision: str, target_ref: str) -> None:
        super().__init__(text="{}")
        self.decision = str(decision)
        self.target_ref = str(target_ref)
        self.matched: list[str] = []

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        from agency_model_client import ModelCallResult

        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or "")})
        answer: dict[str, Any] = {"decision": "NO_ACTION",
                                  "reason_codes": ["NO_ACTION_CHOSEN"]}
        try:
            payload = json.loads(user_prompt or "{}")
        except ValueError:
            payload = {}
        wanted = _bare_artifact(self.target_ref)
        for row in (payload.get("candidates") or []):
            if not isinstance(row, Mapping):
                continue
            refs = {_bare_artifact(ref) for ref in (row.get("target_refs") or ())}
            legal = [str(code) for code in (row.get("legal_decisions") or ())]
            if wanted in refs and self.decision in legal:
                self.matched.append(str(row.get("candidate_id") or ""))
                answer = {"decision": self.decision,
                          "selected_candidate_id": str(row.get("candidate_id") or ""),
                          "reason_codes": ["OPEN_ENDED_CONTINUATION"]}
                break
        return ModelCallResult(purpose="offline", provider=self.provider, model=self.model,
                               text=json.dumps(answer, ensure_ascii=False), latency_ms=1,
                               status=200, usage_source="none")


class _FixtureAutoChoiceTransport(baseline.CaptureTransport):
    """夹具影子替身：只对 H 目标优先 RESUME、其次 CONTINUE；不改候选/合法集。"""

    def __init__(self, target_ref: str) -> None:
        super().__init__(text="{}")
        self.target_ref = str(target_ref)
        self.matched: list[str] = []

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        from agency_model_client import ModelCallResult

        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or "")})
        answer: dict[str, Any] = {"decision": "NO_ACTION",
                                  "reason_codes": ["NO_ACTION_CHOSEN"]}
        try:
            payload = json.loads(user_prompt or "{}")
        except ValueError:
            payload = {}
        wanted = _bare_artifact(self.target_ref)
        for preferred in ("RESUME", "CONTINUE"):
            for row in (payload.get("candidates") or []):
                if not isinstance(row, Mapping):
                    continue
                refs = {_bare_artifact(ref) for ref in (row.get("target_refs") or ())}
                legal = [str(code) for code in (row.get("legal_decisions") or ())]
                if wanted in refs and preferred in legal:
                    self.matched.append(str(row.get("candidate_id") or ""))
                    return ModelCallResult(
                        purpose="offline", provider=self.provider, model=self.model,
                        text=json.dumps({"decision": preferred,
                                         "selected_candidate_id": str(
                                             row.get("candidate_id") or ""),
                                         "reason_codes": ["OPEN_ENDED_CONTINUATION"]},
                                        ensure_ascii=False),
                        latency_ms=1, status=200, usage_source="none")
        return ModelCallResult(purpose="offline", provider=self.provider, model=self.model,
                               text=json.dumps(answer, ensure_ascii=False), latency_ms=1,
                               status=200, usage_source="none")


def _fixture_shadow_factory(target_ref: str) -> Callable[..., Any]:
    def factory(_stage: str, _arm: Optional[str] = None) -> Any:
        return _FixtureAutoChoiceTransport(target_ref)

    return factory


def _fixture_decision_factory(decision: str, target_ref: str) -> Callable[..., Any]:
    def factory(_stage: str, _arm: Optional[str] = None) -> Any:
        return _ScriptedChoiceTransport(decision, target_ref)

    return factory


def _fixture_content_factory() -> Callable[..., Any]:
    def factory(_stage: str, _arm: Optional[str] = None) -> Any:
        return v2.ScriptedAppendTransport()

    return factory


def _safe_round(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: record.get(key) for key in
            ("outcome", "decision", "selected_candidate_ref", "owner_version_created",
             "owner_version_id", "step_work_target", "activity_ref", "action_status",
             "receipt_effect_state", "requests")}


def _continue_arm(profile: LiveProfile, *, arm: str, root: Path, env: Mapping[str, str],
                  state_root: Path, budget: Any, stage_ledger: LiveStageLedger,
                  choice: Mapping[str, Any], h_ref: str, activity_ref: str,
                  observed_at: str, neutralise: bool, latch: Any,
                  decision_transport: Any, content_transport: Any,
                  fixtures: bool, supply_already_started: bool = False,
                  frozen_version_ref: str = "") -> dict[str, Any]:
    """一臂承接：正式 Decision（真实或夹具）→ Action → owner 效果；错目标/不匹配拒。"""

    supply = None
    bundle = None
    created_supply = str(root) != str(profile.main_root)
    try:
        if created_supply:
            if not supply_already_started:
                supply = _start_supply(root, "g2-live-%s" % arm)
            env = branch_env(profile, root, fixtures=fixtures, offline=fixtures)
            state_root = Path(str(env.get("NYA02_RESIDENT_STATE") or ""))
        bundle = baseline.build_experiment_runtime(
            env, root=root, state_root=state_root, budget=budget, ledger=stage_ledger,
            stage="continuation", cognition_transport=decision_transport,
            content_transport=content_transport,
            neutralise_experience_history=bool(neutralise), latch=latch)
        bundle["assembled_env"] = dict(env)
        baseline.set_bundle_stage(bundle, "continuation")
        precheck = _execution_precheck(bundle["runtime"], state_root, choice, h_ref,
                                       activity_ref, observed_at)
        result: dict[str, Any] = {"status": R_NOT_RUN, "arm": arm,
                                  "reason_code": R_PRECONDITION_REFUSED,
                                  "precheck": precheck, "used": 0}
        if not precheck.get("ok"):
            return result
        before = int(budget.view().get("run_window_calls") or 0)
        with baseline.arm_ceiling(budget, profile.arm_cap) as limit:
            report = bundle["cycle"].evaluate_once(
                trigger_ref="g2:target:%s" % arm,
                trigger_kind=baseline._idle_trigger_kind(), act=True,
                observed_at=observed_at)
            after = int(budget.view().get("run_window_calls") or 0)
            used = after - before
            third_refused = None
            if fixtures and used >= profile.arm_cap:
                from life_request_budget import RequestBudgetError, REASON_RUN_WINDOW_LIMIT

                try:
                    budget.reserve("decision")
                    third_refused = False
                except RequestBudgetError as exc:
                    third_refused = exc.reason_code == REASON_RUN_WINDOW_LIMIT
        detail = str((report or {}).get("detail") or "")
        if any(marker in detail for marker in baseline._TRANSPORT_FAILURE_MARKERS):
            latch.stop(R_PROVIDER_FAILURE)
        record = baseline._round_record(report)
        receipt = report.get("step_receipt") or {}
        decision = report.get("decision") or record.get("decision")
        target = str(report.get("step_work_target") or record.get("step_work_target") or "")
        activity = str(report.get("activity_ref") or receipt.get("activity_ref") or "")
        owner_version = str(report.get("owner_version_id")
                            or receipt.get("version_id") or "")
        checks = {
            "decision_matches_shadow_choice": decision == choice.get("decision"),
            "same_frozen_artifact": _bare_artifact(target) == _bare_artifact(h_ref),
            "same_frozen_activity": bool(activity_ref) and activity == activity_ref,
            "owner_version_created": report.get("owner_version_created") is True,
            "new_owner_version": bool(owner_version) and (
                not frozen_version_ref
                or _bare_version(owner_version) != _bare_version(frozen_version_ref)),
            "effect_applied": receipt.get("effect_state") == "APPLIED",
            "within_arm_cap": used <= profile.arm_cap,
        }
        blocking = [name for name, value in checks.items() if not value]
        status = "RAN" if not blocking else (
            "MISMATCH_NOT_ACCEPTED" if not checks["decision_matches_shadow_choice"]
            else "NOT_RUN")
        return {"status": status, "arm": arm, "reason_code": "" if not blocking else
                ("SHADOW_DECISION_MISMATCH" if not checks["decision_matches_shadow_choice"]
                 else R_PRECONDITION_REFUSED),
                "checks": checks, "blocking": blocking, "used": used,
                "decision": decision, "selected_candidate_ref":
                    report.get("selected_candidate_ref") or record.get("selected_candidate_ref"),
                "target": target, "activity_ref": activity, "version_ref": owner_version,
                "third_refused": third_refused, "arm_ceiling": dict(limit),
                "precheck": precheck, "root": str(root)}
    except baseline.BatchRefused as exc:
        return {"status": R_NOT_RUN, "arm": arm, "reason_code": exc.reason_code,
                "used": 0, "root": str(root)}
    except Exception as exc:
        return {"status": R_NOT_RUN, "arm": arm, "reason_code": R_UNEXPECTED,
                "kind": type(exc).__name__, "used": 0, "root": str(root)}
    finally:
        if bundle is not None:
            try:
                bundle["runtime"].close()
            except Exception:
                pass
            baseline.release_runtime(bundle)
        _stop_quietly(supply)


def _close_bundle(bundle: Optional[Mapping[str, Any]]) -> None:
    if bundle is None:
        return
    try:
        bundle["runtime"].close()
    except Exception:
        pass
    baseline.release_runtime(bundle)


def _write_window(profile: LiveProfile, *, total_window_seconds: Optional[int] = None
                  ) -> dict[str, Any]:
    if _exists(profile.window_path):
        raise LiveRefused(R_ALREADY_STARTED, "the run window already exists")
    seconds = int(total_window_seconds if total_window_seconds is not None
                  else profile.total_window_seconds)
    now_wall = datetime.now(timezone.utc)
    start_mono = time.monotonic()
    wall_deadline = min(now_wall + timedelta(seconds=seconds), profile.deadline)
    payload = {"schema": "g2.live.window.v1", "run_id": profile.run_id,
               "window_seconds": seconds, "started_monotonic": start_mono,
               "deadline_monotonic": start_mono + float(seconds),
               "started_wall": _iso(now_wall), "deadline_wall": _iso(wall_deadline),
               "deadline_basis": "min(now+%ds, %s)" % (seconds, profile.deadline.isoformat())}
    _write_json(profile.window_path, payload)
    return payload


def _public_run(payload: Mapping[str, Any]) -> dict[str, Any]:
    """CLI 只投影白名单键；内部已构造的字段再按白名单裁一次。"""

    def project(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): project(item) for key, item in value.items()
                    if str(key) in SAFE_KEYS}
        if isinstance(value, (list, tuple)):
            return [project(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(type(value).__name__)

    return project(payload)


# ---------------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------------

def _run_impl(profile: Optional[LiveProfile] = None, *, execute_approved: bool,
              offline_fixtures: bool = False, root: Optional[str | Path] = None,
              decision_transport: Any = None, content_transport: Any = None,
              cognition_factory: Optional[Callable[..., Any]] = None,
              content_factory: Optional[Callable[..., Any]] = None,
              clock: Optional[Callable[[], float]] = None,
              sleep: Optional[Callable[[float], None]] = None,
              total_window_seconds: Optional[int] = None,
              acquisition_window_seconds: Optional[int] = None,
              max_observations: Optional[int] = None,
              fixture_hook: Optional[Callable[[int], None]] = None,
              batch: str = BATCH_DEFAULT) -> dict[str, Any]:
    """唯一真实入口（``--execute-approved``）。夹具只允许显式开关 + tempdir scratch。"""

    base, refusal = _resolve_profile(offline_fixtures=offline_fixtures, root=root,
                                     batch=batch)
    if refusal is not None:
        return refusal
    # 夹具模式保留调用方的 profile 字段（deadline 等夹具值），但根与 trace 必须落在
    # 解析出的 scratch 副本里 —— 绝不把传入的正式 profile 写回正式根。
    if offline_fixtures:
        if profile is None:
            profile = base
        else:
            profile = replace(profile, batch_root=base.batch_root,
                              maintain_trace_path=base.maintain_trace_path)
    else:
        profile = profile or base
    fixtures = bool(offline_fixtures)
    if not fixtures and profile.batch_root != formal_profile(profile.batch_key).batch_root:
        return {"ok": False, "reason_code": R_FIXTURE_ROOT_REFUSED,
                "detail": "the real path never writes a custom root", "fixture": False}
    if fixtures:
        if decision_transport is None:
            decision_transport = v2.CountingTransport(
                json.dumps({"decision": "NO_ACTION",
                            "reason_codes": ["NO_ACTION_CHOSEN"]}), label="fixture-decision")
        if content_transport is None:
            content_transport = v2.CountingTransport(
                json.dumps({"kind": "no_change", "text": ""}), label="fixture-content")
    fixture_inputs = any(item is not None for item in (
        decision_transport, content_transport, cognition_factory, content_factory,
        clock, sleep, total_window_seconds, acquisition_window_seconds,
        max_observations, fixture_hook))
    if not fixtures and fixture_inputs:
        return {"ok": False, "reason_code": R_FIXTURE_IN_REAL_PATH,
                "detail": "the real transport path refuses fixture doubles",
                "fixture": False}
    if not execute_approved:
        return {"ok": False, "reason_code": R_EXECUTE_FLAG_MISSING, "fixture": fixtures}
    # b 的启动线是批次级过期拒绝门：到点及之后一律不再开新窗口（不读状态、不写 window/
    # started、不发请求）。a 的 latest_start 为 None，行为不变。
    if (profile.latest_start is not None
            and datetime.now(timezone.utc) >= profile.latest_start):
        return {"ok": False, "reason_code": R_LATEST_START_PASSED,
                "latest_start": profile.latest_start.isoformat(),
                "deadline": profile.deadline.isoformat(), "fixture": fixtures}
    if not _exists(profile.prereg_path):
        return {"ok": False, "reason_code": R_NOT_PREPARED, "fixture": fixtures}
    prereg = _read_json(profile.prereg_path)
    bad = _identity_refusal(profile, prereg)
    if bad is not None:
        return {**bad, "fixture": fixtures}
    batch = _read_json(profile.batch_path)
    if not batch:
        return {"ok": False, "reason_code": R_HALF_STATE, "fixture": fixtures}
    if (batch.get("run") or {}).get("started_at"):
        return {"ok": False, "reason_code": R_ALREADY_STARTED,
                "started_at": (batch.get("run") or {}).get("started_at"),
                "fixture": fixtures}
    if str(batch.get("state") or "") != "prepared":
        return {"ok": False, "reason_code": R_NOT_PREPARED,
                "state": batch.get("state"), "fixture": fixtures}
    if _exists(profile.window_path):
        return {"ok": False, "reason_code": R_ALREADY_STARTED,
                "detail": "the run window already exists", "fixture": fixtures}
    if datetime.now(timezone.utc) >= profile.deadline:
        return {"ok": False, "reason_code": R_DEADLINE_PASSED,
                "deadline": profile.deadline.isoformat(), "fixture": fixtures}

    state_root_text = str(prereg.get("state_root") or "")
    state_root = Path(state_root_text) if state_root_text else None
    if state_root is None or not _within(state_root, profile.main_root):
        return {"ok": False, "reason_code": R_HALF_STATE, "field": "state_root",
                "fixture": fixtures}
    ledger = _read_ledger_direct(profile, state_root)
    if ledger.get("run_window_calls") is None:
        return {"ok": False, "reason_code": R_LEDGER_UNREADABLE, "fixture": fixtures}
    if int(ledger.get("run_window_calls") or 0) != 0:
        return {"ok": False, "reason_code": R_LEDGER_NOT_ZERO, "ledger": _safe_budget(ledger),
                "fixture": fixtures}

    try:
        env = live_env(profile, offline=fixtures, fixtures=fixtures)
        isolation = validate_isolation(env, profile.main_root,
                                       batch_root=profile.batch_root,
                                       authority_db=profile.authority_db)
    except LiveRefused as exc:
        return {"ok": False, "reason_code": exc.reason_code, "fixture": fixtures}
    if not fixtures:
        model = _verify_model_config(env)
        if not model.get("ok"):
            return {"ok": False, "reason_code": model.get("reason_code"),
                    "expected": model.get("expected"), "actual": model.get("actual"),
                    "fixture": False}

    now_clock = clock or time.monotonic
    sleep_fn = sleep or time.sleep
    started_clock = float(now_clock())
    total_seconds = int(total_window_seconds if total_window_seconds is not None
                        else profile.total_window_seconds)
    acquisition_seconds = int(acquisition_window_seconds
                              if acquisition_window_seconds is not None
                              else profile.acquisition_window_seconds)
    max_obs = int(max_observations if max_observations is not None
                  else profile.max_observations)

    def deadline_check() -> bool:
        if float(now_clock()) - started_clock >= total_seconds:
            return True
        return datetime.now(timezone.utc) >= profile.deadline

    # started 必须在任何请求之前持久写；窗口只写这一次。
    try:
        window = _write_window(profile, total_window_seconds=total_seconds)
    except LiveRefused as exc:
        return {"ok": False, "reason_code": exc.reason_code, "fixture": fixtures}
    batch["run"] = {"started_at": _iso(), "pid": os.getpid(), "status": "running",
                    "fixture": fixtures}
    batch["state"] = "running"
    _write_json(profile.batch_path, batch)

    budget = _budget(profile, state_root)
    try:
        budget.ensure_window(allow_new=False)
    except Exception as exc:
        _write_json(profile.stop_path, {"schema": "g2.live.stop.v1",
                                        "run_id": profile.run_id,
                                        "reason_code": R_LEDGER_UNREADABLE,
                                        "at": _iso()})
        return {"ok": False, "reason_code": R_LEDGER_UNREADABLE,
                "kind": type(exc).__name__, "fixture": fixtures}

    latch = PersistentSendLatch(profile, deadline_check)
    stages = LiveStageLedger(profile, budget)
    report: dict[str, Any] = {
        "ok": True, "fixture": fixtures, "batch": profile.run_name,
        "run_id": profile.run_id, "domain_id": profile.domain_id,
        "history": profile.expected_history,
        "check_interval_minutes": profile.check_interval_minutes,
        "persisted_check_interval_minutes": _nested_check_interval(profile),
        "cumulative_total": profile.cumulative_total,
        "caps": profile.caps(),
        "model_params": profile.model_params(), "deadline": profile.deadline.isoformat(),
        "latest_start": (profile.latest_start.isoformat()
                         if profile.latest_start is not None else None),
        "root": str(profile.batch_root), "main": str(profile.main_root),
        "authority": str(profile.authority_db), "branches": str(profile.branches_root),
        "window": {"present": True, "run_id": window.get("run_id"),
                   "window_seconds": window.get("window_seconds"),
                   "started_wall": window.get("started_wall"),
                   "deadline_wall": window.get("deadline_wall")},
        "isolation": isolation, "started_at": batch["run"]["started_at"],
        "acquisition": None, "shadow": None, "continuation": None,
        "statistics": None, "stop": None, "requests_used": 0,
        "result": None,
    }
    main_supply = None
    bundle = None
    branch_supply = None
    try:
        main_supply = _start_supply(profile.main_root, "g2-live-run")
        isolated_env = live_env(profile, offline=fixtures, fixtures=fixtures)
        validate_isolation(isolated_env, profile.main_root, batch_root=profile.batch_root,
                           authority_db=profile.authority_db)
        env = isolated_env
        # 采集
        stages.start_stage("acquisition")
        plan = _plan_for(profile, acquisition_window=acquisition_seconds,
                         max_observations=max_obs)
        bundle = baseline.build_experiment_runtime(
            env, root=profile.main_root, state_root=state_root, budget=budget,
            ledger=stages, stage="acquisition",
            cognition_transport=decision_transport, content_transport=content_transport,
            latch=latch)
        bundle["assembled_env"] = dict(env)
        if profile.maintain_trace_path is not None:
            try:
                bundle["assembled"]["tick_observer"] = _maintain_trace_observer(
                    profile.maintain_trace_path)
            except Exception:
                # 观察装不上不阻止真实批；status 会如实报 UNKNOWN。
                pass
        captured: dict[str, Any] = {}
        work_refs = [str(row.get("artifact_id") or "") for row in (prereg.get("works") or [])]
        work_refs = ["artifact:%s" % ref if not str(ref).startswith("artifact:") else str(ref)
                     for ref in work_refs if ref]

        def freeze_cb(h_ref: str) -> Mapping[str, Any]:
            # 有确证 owner 版本时按 artifact + version 精确复查；没有就保持原 artifact 身份。
            attempt = v2.freeze_and_check(
                bundle, h_ref=h_ref, work_refs=work_refs,
                h_version=(detector.exact.get(str(h_ref)) or None), sleep=sleep_fn)
            captured["attempt"] = attempt
            return attempt

        detector = ExactHistoryDetector(
            candidate_read=bundle["runtime"].candidate_read,
            memory_port=(bundle.get("memory") or {}).get("port"))
        record = v2.ExperimentRecord(profile.experiment_dir / NATURAL_DIRNAME)
        controller = v2.NaturalObservationController(
            plan=plan,
            observer=v2.ReadOnlyObserver(state_root=state_root, run_id=profile.run_id,
                                         activity_read=bundle["runtime"].activity_read),
            advance=_advance(bundle, fixture_hook, latch), budget=budget, record=record,
            h_detector=detector, freeze=freeze_cb, clock=now_clock, sleep=sleep_fn,
            deadline=deadline_check, latch=latch)
        acquisition = controller.run()
        last_gate = next((row.get("h_gate") for row in reversed(
            acquisition.get("timeline") or []) if row.get("h_gate")), {}) or {}
        gate_reason = ((acquisition.get("gate") or {}).get("reason_code")
                       or last_gate.get("reason_code") or None)
        report["acquisition"] = {
            "result": acquisition.get("result"), "stop_reason": acquisition.get("stop_reason"),
            "observations": acquisition.get("observations"),
            "requests_used": acquisition.get("requests_used"),
            "h_ref": acquisition.get("h_ref"),
            "h_evidence": _h_evidence_view(detector, acquisition),
            "gate": gate_reason,
            "frozen": bool(captured.get("attempt", {}).get("frozen")),
        }
        report["requests_used"] = int(budget.view().get("run_window_calls") or 0)

        if latch.closed:
            report["result"] = RESULT_ABORTED
            report["stop"] = latch.stop_reason
        elif acquisition.get("result") == RESULT_NO_H:
            report["result"] = RESULT_NO_H
            report["stop"] = acquisition.get("stop_reason")
        elif acquisition.get("result") == RESULT_ABORTED:
            report["result"] = RESULT_ABORTED
            report["stop"] = acquisition.get("stop_reason")
        elif acquisition.get("result") != RESULT_FROZEN:
            report["result"] = RESULT_NOT_IDENTIFIABLE
            report["stop"] = acquisition.get("stop_reason")
        else:
            attempt = captured.get("attempt") or {}
            frozen = attempt.get("frozen")
            gate = attempt.get("gate") or {}
            if frozen is None:
                report["result"] = RESULT_NOT_IDENTIFIABLE
                report["stop"] = R_SCENARIO_NOT_IDENTIFIABLE
            elif not gate.get("baseline"):
                report["result"] = RESULT_ABORTED
                report["stop"] = R_UNEXPECTED
            else:
                report["gate"] = {key: gate.get(key) for key in
                                  ("ok", "reason_code", "candidate_count",
                                   "executable_count", "h_executable",
                                   "other_work_executable", "no_action_legal",
                                   "legal_decision_enums", "candidate_targets",
                                   "offline_transport")}
                # 影子 40（夹具才用脚本替身；真实路径 factory 为 None，走真实 provider）
                stages.start_stage("shadow")
                shadow_factory = cognition_factory
                if shadow_factory is None and fixtures:
                    shadow_factory = _fixture_shadow_factory(str(frozen.get("h_ref") or ""))
                shadow = baseline.run_shadow(
                    bundle, frozen=frozen, baseline=gate.get("baseline") or {},
                    ledger=stages, budget=budget, env=env,
                    deadline_check=deadline_check, cognition_factory=shadow_factory,
                    latch=latch, pair_count=profile.pairs)
                report["shadow"] = {
                    "pairs_complete": len([row for row in (shadow.get("pairs") or [])
                                           if row.get("A") and row.get("B")]),
                    "pairs_target": profile.pairs,
                    "stop_reason": shadow.get("stop_reason"),
                    "requests_used": shadow.get("requests_used"),
                }
                report["requests_used"] = int(budget.view().get("run_window_calls") or 0)
                if latch.closed:
                    report["result"] = RESULT_ABORTED
                    report["stop"] = latch.stop_reason
                elif shadow.get("stop_reason"):
                    if shadow.get("stop_reason") == R_PROVIDER_FAILURE:
                        latch.stop(R_PROVIDER_FAILURE)
                        report["result"] = RESULT_ABORTED
                    else:
                        report["result"] = RESULT_NOT_TESTED
                    report["stop"] = shadow.get("stop_reason")
                elif report["shadow"]["pairs_complete"] != profile.pairs:
                    report["result"] = RESULT_NOT_TESTED
                    report["stop"] = "SHADOW_INCOMPLETE"
                elif profile.continuation_cap <= 0:
                    # 探索批：两臂执行承接的最低 4 请求与 cap 不相容，停在影子选择边界。
                    # 不拿保留的 2 个额度去凑承接，也不把影子样本改写成两臂结果。
                    report["continuation"] = {
                        arm: {"status": R_NOT_RUN, "arm": arm,
                              "reason_code": R_CONTINUATION_OUT_OF_SCOPE, "used": 0}
                        for arm in ("A", "B")}
                    stats = baseline.shadow_statistics(
                        shadow, report["continuation"], pair_count=profile.pairs)
                    report["statistics"] = {
                        "pairs_complete": stats.get("pairs_complete"),
                        "pairs_target": stats.get("pairs_target"),
                        "exploratory_only": stats.get("exploratory_only"),
                        "a_h": stats.get("a_h"), "b_h": stats.get("b_h"),
                        "delta": stats.get("delta"),
                        "mcnemar_two_sided_p": stats.get("mcnemar_two_sided_p"),
                        "conclusion": stats.get("conclusion"),
                    }
                    report["result"] = _conclusion_from_stats(stats)
                else:
                    report["result"] = RESULT_NOT_TESTED
                    # 承接：先把同一 runtime 正常关闭，再在 A 任何写之前 clone B。
                    _close_bundle(bundle)
                    bundle = None
                    branch = profile.branches_root / "b"
                    clone = baseline.clone_branch(
                        profile.main_root, branch, expected_run_id=profile.run_id,
                        shared_authority=profile.authority_db)
                    report["branch"] = {key: clone.get(key) for key in
                                        ("ok", "reason_code", "outside", "checked",
                                         "shared_authority", "copy")}
                    if not clone.get("ok"):
                        # 隔离合同没建立：承接两臂如实 NOT_RUN，不新增合同、不 fallback。
                        # 影子本身的统计仍然独立成立。
                        report["continuation"] = {
                            arm: {"status": R_NOT_RUN, "arm": arm,
                                  "reason_code": R_BRANCH_ISOLATION, "used": 0}
                            for arm in ("A", "B")}
                        report["stop"] = R_BRANCH_ISOLATION
                        stats = baseline.shadow_statistics(
                            shadow, report["continuation"])
                        report["statistics"] = {
                            "pairs_complete": stats.get("pairs_complete"),
                            "a_h": stats.get("a_h"), "b_h": stats.get("b_h"),
                            "mcnemar_two_sided_p": stats.get("mcnemar_two_sided_p"),
                            "delta": stats.get("delta"),
                            "conclusion": stats.get("conclusion"),
                        }
                        report["result"] = _conclusion_from_stats(stats)
                    else:
                        stages.start_stage("continuation")
                        gate_targets = list(gate.get("candidate_targets") or [])
                        h_ref = str(frozen.get("h_ref") or "")
                        activity_ref = _h_activity(frozen, h_ref)
                        frozen_version_ref = str(
                            (acquisition.get("h_detail") or {}).get("owner_version_id") or "")
                        # 夹具才把同状态重考推到 TTL 之后；真实路径用当下墙钟，不追赶、不快进。
                        observed_at = _target_observed_at(frozen) if fixtures else _iso()
                        choices = {arm: _shadow_choice(shadow, gate_targets, h_ref, arm)
                                   for arm in ("A", "B")}
                        if fixtures:
                            # 夹具：调用方给了工厂就用它；否则只按影子选择作答，不 fallback。
                            factories = {}
                            for arm in ("A", "B"):
                                decision = (choices[arm].get("decision")
                                            if choices[arm].get("ok") else "NO_ACTION")
                                if cognition_factory is not None:
                                    d_factory = cognition_factory
                                else:
                                    d_factory = _fixture_decision_factory(decision, h_ref)
                                c_factory = (content_factory if content_factory is not None
                                             else _fixture_content_factory())
                                factories[arm] = (d_factory, c_factory)
                        else:
                            factories = {arm: (None, None) for arm in ("A", "B")}
                        # B 的起点在 A 任何写之前读出。
                        branch_supply = _start_supply(branch, "g2-live-branch-b")
                        branch_env_map = branch_env(profile, branch, fixtures=fixtures,
                                                    offline=fixtures)
                        branch_validation = validate_isolation(
                            branch_env_map, branch, batch_root=profile.batch_root,
                            authority_db=profile.authority_db)
                        from components.phone import service as phone_service
                        from life_artifact_port import LifeArtifactPort

                        branch_subject = str(dict(
                            phone_service.load_instance(branch)).get("subject") or "")
                        branch_port = LifeArtifactPort(
                            str(branch_env_map.get("LIFE_SUPPLY_SOCKET")
                                or branch_env_map.get("CHIYO_NATIVE_LIFE_SUPPLY_SOCKET") or ""),
                            branch_subject)
                        bare_h = _bare_artifact(h_ref)
                        owner_ref = "artifact:%s" % bare_h
                        branch_head_before = branch_port.read_head(owner_ref) or {}
                        main_port = LifeArtifactPort(
                            str(env.get("LIFE_SUPPLY_SOCKET")
                                or env.get("CHIYO_NATIVE_LIFE_SUPPLY_SOCKET") or ""),
                            str(prereg.get("subject") or ""))
                        main_head_before = main_port.read_head(owner_ref) or {}

                        arms: dict[str, Any] = {}
                        branch_head_after_a: dict[str, Any] = {}
                        main_head_after_a: dict[str, Any] = {}
                        for arm in ("A", "B"):
                            choice = choices[arm]
                            if latch.closed:
                                arms[arm] = {"status": R_NOT_RUN, "arm": arm,
                                             "reason_code": latch.stop_reason,
                                             "used": 0}
                                continue
                            if not choice.get("ok"):
                                arms[arm] = {"status": R_NOT_RUN, "arm": arm,
                                             "reason_code": choice.get("reason_code"),
                                             "used": 0}
                                continue
                            d_factory, c_factory = factories[arm]
                            d_transport = d_factory("continuation", arm) if d_factory else None
                            c_transport = c_factory("continuation", arm) if c_factory else None
                            arm_root = profile.main_root if arm == "A" else branch
                            arm_state = state_root if arm == "A" else Path(str(
                                branch_env_map.get("NYA02_RESIDENT_STATE") or ""))
                            arms[arm] = _continue_arm(
                                profile, arm=arm, root=arm_root,
                                env=env if arm == "A" else branch_env_map,
                                state_root=arm_state, budget=budget, stage_ledger=stages,
                                choice=choice, h_ref=h_ref, activity_ref=activity_ref,
                                observed_at=observed_at, neutralise=(arm == "B"),
                                latch=latch, decision_transport=d_transport,
                                content_transport=c_transport, fixtures=fixtures,
                                supply_already_started=(arm == "B"),
                                frozen_version_ref=frozen_version_ref)
                            if arm == "A":
                                # A 写完之后、B 开始之前：分支必须仍是冻结起点。
                                branch_head_after_a = branch_port.read_head(owner_ref) or {}
                                main_head_after_a = main_port.read_head(owner_ref) or {}
                        branch_head_after_b = branch_port.read_head(owner_ref) or {}
                        report["continuation"] = arms
                        branch_before_version = str(branch_head_before.get("version_id") or "")
                        branch_after_a_version = str(branch_head_after_a.get("version_id") or "")
                        report["branch"].update({
                            "isolation_ok": bool(branch_validation.get("ok")),
                            "branch_head_before": branch_before_version or None,
                            "branch_head_after_arm_a": branch_after_a_version or None,
                            "branch_head_after_arm_b": str(
                                branch_head_after_b.get("version_id") or "") or None,
                            "main_head_before": main_head_before.get("version_id"),
                            "main_head_after_arm_a": main_head_after_a.get("version_id"),
                            "frozen_version_ref": frozen_version_ref or None,
                            # B 的起点必须在 A 任何写前后保持同一个 head（clone 在 A 之前）。
                            "b_start_unpolluted": bool(
                                branch_before_version
                                and branch_before_version == branch_after_a_version
                                and (not frozen_version_ref
                                     or _bare_version(branch_before_version)
                                     == _bare_version(frozen_version_ref))),
                            "arm_versions_distinct": bool(
                                (arms.get("A") or {}).get("version_ref")
                                and (arms.get("B") or {}).get("version_ref")
                                and (arms["A"]["version_ref"] != arms["B"]["version_ref"])),
                        })
                        report["requests_used"] = int(
                            budget.view().get("run_window_calls") or 0)
                        stats = baseline.shadow_statistics(
                            shadow, {"A": arms.get("A") or {}, "B": arms.get("B") or {}})
                        report["statistics"] = {
                            "pairs_complete": stats.get("pairs_complete"),
                            "a_h": stats.get("a_h"), "b_h": stats.get("b_h"),
                            "mcnemar_two_sided_p": stats.get("mcnemar_two_sided_p"),
                            "delta": stats.get("delta"),
                            "conclusion": stats.get("conclusion"),
                        }
                        report["result"] = _conclusion_from_stats(stats)
                        if latch.closed:
                            report["result"] = RESULT_ABORTED
                            report["stop"] = latch.stop_reason
    except LiveRefused as exc:
        report["result"] = RESULT_ABORTED
        report["stop"] = exc.reason_code
    except baseline.BatchRefused as exc:
        report["result"] = RESULT_NOT_TESTED
        report["stop"] = exc.reason_code
    except Exception as exc:
        report["result"] = RESULT_ABORTED
        report["stop"] = R_UNEXPECTED
        report["kind"] = type(exc).__name__
    finally:
        try:
            _close_bundle(bundle)
        except Exception:
            pass
        _stop_quietly(branch_supply)
        _stop_quietly(main_supply)

    # 结果收口：失败/停止持久落盘，batch 只写一次。
    final = report.get("result") or RESULT_ABORTED
    stop_reason = str(report.get("stop") or "")
    if latch.closed and final != RESULT_ABORTED:
        final = RESULT_ABORTED
        stop_reason = latch.stop_reason
    report["result"] = final
    if _exists(profile.stop_path):
        persisted = str(_read_json(profile.stop_path).get("reason_code") or "")
        if persisted and not stop_reason:
            stop_reason = persisted
        report["stop"] = stop_reason or persisted or None
    elif stop_reason:
        try:
            _write_json(profile.stop_path, {"schema": "g2.live.stop.v1",
                                            "run_id": profile.run_id,
                                            "reason_code": stop_reason, "at": _iso()})
        except Exception:
            pass
        report["stop"] = stop_reason
    batch = _read_json(profile.batch_path) or batch
    batch["state"] = ("completed" if final in (RESULT_DETECTED, RESULT_NO_DIFFERENCE)
                      else "aborted" if final == RESULT_ABORTED
                      else "finished")
    batch["result"] = final
    batch["stop"] = ({"reason_code": stop_reason, "at": _iso()} if stop_reason else None)
    batch["requests_used"] = int(budget.view().get("run_window_calls") or 0) \
        if budget is not None else None
    try:
        _write_json(profile.batch_path, batch)
    except Exception:
        pass
    report["requests_used"] = batch.get("requests_used")
    report["ledger"] = _safe_budget(budget.view()) if budget is not None else None
    try:
        _write_json(profile.run_report_path, _public_run(report))
    except Exception:
        pass
    return report


def run(profile: Optional[LiveProfile] = None, *, execute_approved: bool,
        offline_fixtures: bool = False, root: Optional[str | Path] = None,
        decision_transport: Any = None, content_transport: Any = None,
        cognition_factory: Optional[Callable[..., Any]] = None,
        content_factory: Optional[Callable[..., Any]] = None,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        total_window_seconds: Optional[int] = None,
        acquisition_window_seconds: Optional[int] = None,
        max_observations: Optional[int] = None,
        fixture_hook: Optional[Callable[[int], None]] = None,
        batch: str = BATCH_DEFAULT) -> dict[str, Any]:
    """夹具路径在整段运行期间硬断 HTTP；真实路径原样交给实现。"""

    if offline_fixtures:
        restore = baseline._hard_disconnect(http.client.HTTPConnection, "connect")
        try:
            return _run_impl(
                profile, execute_approved=execute_approved, offline_fixtures=True,
                root=root, decision_transport=decision_transport,
                content_transport=content_transport, cognition_factory=cognition_factory,
                content_factory=content_factory, clock=clock, sleep=sleep,
                total_window_seconds=total_window_seconds,
                acquisition_window_seconds=acquisition_window_seconds,
                max_observations=max_observations, fixture_hook=fixture_hook,
                batch=batch)
        finally:
            restore()
    return _run_impl(
        profile, execute_approved=execute_approved, offline_fixtures=False, root=root,
        decision_transport=decision_transport, content_transport=content_transport,
        cognition_factory=cognition_factory, content_factory=content_factory,
        clock=clock, sleep=sleep, total_window_seconds=total_window_seconds,
        acquisition_window_seconds=acquisition_window_seconds,
        max_observations=max_observations, fixture_hook=fixture_hook, batch=batch)


# ---------------------------------------------------------------------------------
# 夹具用的进程内 phase 探测（供新 suite 的“真实子进程装配 + 默认写面零新增”）
# ---------------------------------------------------------------------------------

def phase_probe(profile: LiveProfile, *, offline_fixtures: bool = True) -> dict[str, Any]:
    """在真实子进程里装配一次正式 runtime，gate off，跑一轮 runner tick。

    只用于新 suite 的隔离验证：所有写面 env 必须解析在本批根内；返回隔离视图与 gate。
    """

    if not offline_fixtures:
        raise LiveRefused(R_FIXTURE_IN_REAL_PATH)
    if not _exists(profile.prereg_path):
        raise LiveRefused(R_NOT_PREPARED)
    state_root = _state_root(profile)
    env = live_env(profile, offline=True, fixtures=True)
    isolation = validate_isolation(env, profile.main_root, batch_root=profile.batch_root,
                                   authority_db=profile.authority_db)
    supply = None
    bundle = None
    try:
        supply = _start_supply(profile.main_root, "g2-live-probe")
        env = live_env(profile, offline=True, fixtures=True)
        budget = _budget(profile, state_root)
        budget.ensure_window(allow_new=False)
        latch = PersistentSendLatch(profile, lambda: False)
        stages = LiveStageLedger(profile, budget)
        stages.start_stage("acquisition")
        bundle = baseline.build_experiment_runtime(
            env, root=profile.main_root, state_root=state_root, budget=budget,
            ledger=stages, stage="acquisition",
            cognition_transport=v2.CountingTransport(
                json.dumps({"decision": "NO_ACTION",
                            "reason_codes": ["NO_ACTION_CHOSEN"]}), label="probe-decision"),
            content_transport=v2.CountingTransport(
                json.dumps({"kind": "no_change", "text": ""}), label="probe-content"),
            latch=latch)
        bundle["assembled_env"] = dict(env)
        from autonomous_life_assembly import runner_tick

        tick = runner_tick(bundle["assembled"], act=True)
        gate = dict(isolation)
        gate["tick_status"] = str((tick or {}).get("status") or "")
        home = str(isolation.get("world_home") or "")
        gate["lifecycle_exists"] = bool(
            home and (Path(home) / "run" / "action_lifecycle.jsonl").exists())
        return {"ok": True, "isolation": gate, "gate": env.get(GATE_ENV),
                "run_id": env.get("CHIYO_AUTONOMOUS_RUN_ID"),
                "state_root": str(state_root)}
    finally:
        _close_bundle(bundle)
        _stop_quietly(supply)


# ---------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------

def _emit(payload: Mapping[str, Any], *, code: int) -> int:
    sys.stdout.write(json.dumps(_public_run(payload), ensure_ascii=False, indent=1,
                              sort_keys=True) + "\n")
    sys.stdout.flush()
    return int(code)


def _fixture_cli_transports(profile: LiveProfile) -> tuple[Any, Any]:
    return (v2.CountingTransport(json.dumps({"decision": "NO_ACTION",
                                             "reason_codes": ["NO_ACTION_CHOSEN"]}),
                                label="cli-decision"),
            v2.CountingTransport(json.dumps({"kind": "no_change", "text": ""}),
                                 label="cli-content"))


def cmd_prepare(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g2_next_live prepare")
    parser.add_argument("--offline-fixtures", action="store_true")
    parser.add_argument("--root", default=None)
    parser.add_argument("--batch", choices=list(BATCH_CHOICES), default=BATCH_DEFAULT)
    args = parser.parse_args(list(argv))
    result = prepare(offline_fixtures=bool(args.offline_fixtures), root=args.root,
                     batch=str(args.batch))
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_audit(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g2_next_live audit")
    parser.add_argument("--offline-fixtures", action="store_true")
    parser.add_argument("--root", default=None)
    parser.add_argument("--batch", choices=list(BATCH_CHOICES), default=BATCH_DEFAULT)
    args = parser.parse_args(list(argv))
    result = audit(offline_fixtures=bool(args.offline_fixtures), root=args.root,
                   batch=str(args.batch))
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g2_next_live run")
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--offline-fixtures", action="store_true")
    parser.add_argument("--root", default=None)
    parser.add_argument("--batch", choices=list(BATCH_CHOICES), default=BATCH_DEFAULT)
    args = parser.parse_args(list(argv))
    fixtures = bool(args.offline_fixtures)
    decision = content = None
    clock = sleep = None
    total_window = acquisition_window = max_observations = None
    if fixtures:
        base, refusal = _resolve_profile(offline_fixtures=True, root=args.root,
                                        batch=str(args.batch))
        if refusal is None and base is not None:
            decision, content = _fixture_cli_transports(base)
        # 夹具 CLI 用虚拟时钟，避免测试真的等 1800 秒；真实路径永远是 wall/monotonic。
        virtual = v2.VirtualTime()
        clock, sleep = virtual.monotonic, virtual.sleep
        total_window, acquisition_window, max_observations = 300, 60, 2
    result = run(execute_approved=bool(args.execute_approved),
                 offline_fixtures=fixtures, root=args.root,
                 decision_transport=decision, content_transport=content,
                 clock=clock, sleep=sleep, total_window_seconds=total_window,
                 acquisition_window_seconds=acquisition_window,
                 max_observations=max_observations, batch=str(args.batch))
    if result.get("ok"):
        code = EXIT_OK
    elif result.get("reason_code"):
        code = EXIT_REFUSED
    else:
        code = EXIT_ERROR
    return _emit(result, code=code)


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        sys.stderr.write(__doc__ or "")
        return EXIT_OK if arguments else EXIT_REFUSED
    handler = {"prepare": cmd_prepare, "audit": cmd_audit, "run": cmd_run}.get(arguments[0])
    if handler is None:
        sys.stderr.write("unknown command: %s\n" % (arguments[0],))
        return EXIT_REFUSED
    return int(handler(arguments[1:]))


if __name__ == "__main__":
    raise SystemExit(main())

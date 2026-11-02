#!/usr/bin/env python3
"""G1 既有同作品闭环：限定真实模型采用 CLI（offline-check / prepare / audit / run）。

它解决的问题

    前面的 ``g1_same_work_recovery_demo.py`` 用脚本 Decision 把「中断 → 重启 → UNKNOWN 对账 →
    CAS 冲突 → NO_ACTION → 继续」串成了同一条链。那份证据说明的是**机制**能接上，不能说明
    真实模型自己会沿着它走。本入口把同一件作品交给真实模型：它自己挑候选、自己写内容；
    实验 operator 只做两件被计划明确允许的事——一次全局 pause 中断，以及两处各一次的
    既有提交边界故障注入（丢回执、并发 head 推进）。

四个子命令（``--batch`` 缺省 a；a/b/c 是 ``BATCH_PROFILES`` 里的 G1 批，
d/e 由独立的 ``D_PROFILE``/``E_PROFILE`` 注册，不塞回旧集合）

    python -B scripts/g1_real_adoption.py offline-check [--batch a|b|c|d|e]
    python -B scripts/g1_real_adoption.py prepare --root <profile.root> [--batch a|b|c|d|e]
    python -B scripts/g1_real_adoption.py audit   --root <profile.root> [--batch a|b|c|d|e]
    python -B scripts/g1_real_adoption.py run     --root <profile.root> [--batch a|b|c|d|e] \
                                                  --execute-approved [--capture-candidates]

d 不是 G1 批：它只有**一个自然 phase**，不装 SubmissionInjector、不暂停/重启、不注入
UNKNOWN/CAS、不导入 operator 材料，也不按 G1 六个场景判结果。它观察的是同一个
``pause_condition.how`` 在**同次最终结构化请求**里是否含有两条固定句子（两个独立布尔）。

固定边界（计划 ``docs/engineering/g1-limited-real-adoption-plan-20261030.md`` 与
``g1-limited-real-adoption-b-plan-20261030.md``）

* a 批：root ``/root/nyairo-g1-real-20261030``、run ``run:g1-real-20261030``、
  域 ``g1:real:20261030``、历史基线 98。
* b 批：root ``/root/nyairo-g1-real-20261030-b``、独立 authority
  ``/root/nyairo-g1-real-20261030-b-memory-authority``、run ``run:g1-real-20261030-b``、
  域 ``g1:real:20261030-b``、历史基线 101。根或 authority 已存在就停，不换名追成功。
* c 批：root ``/root/nyairo-g1-real-20261030-c``、独立 authority
  ``/root/nyairo-g1-real-20261030-c-memory-authority``、run ``run:g1-real-20261030-c``、
  域 ``g1:real:20261030-c``、历史基线 121，绝对截止 2026-10-31 01:00 CST。
* d 模式（非 G1 的单次材料观测）：root ``/root/nyairo-pause-observe-20261030-d``、独立
  authority ``…-d-memory-authority``、run ``run:pause-observe-20261030-d``、
  域 ``pause-observe:20261030-d``、历史基线 141，同一个 2026-10-31 01:00 CST 截止。
  它不在 ``BATCH_PROFILES`` 里，由独立的 ``D_PROFILE``/``MODE_PROFILES`` 注册；
  prepare 只留 ``demo.prepare_demo`` 建的空实例结构（不导入材料、不造 operator 对照），
  natural phase 用同一实例根起**标准 World owner**（``demo.owner_children`` 的 world child，
  gate 显式 off），让真实候选进 AG-0。
* e 模式（非 G1 的单次世界动作观测）：root ``/root/nyairo-world-observe-20261031-e``、独立
  authority ``…-e-memory-authority``、run ``run:world-observe-20261031-e``、
  域 ``world-observe:20261031-e``、历史基线 148、绝对截止 2026-11-01 01:00 CST。
  它同样独立注册（``E_PROFILE``），与 d 共用**单 natural phase**，但 World gate 在本进程树
  的三处环境映射里显式 ``canonical``：``_runtime_env``、phase 子进程 env、自有 World child env。
  父 shell/实例默认/``.env``/a/b/c/d 一律不动；d 仍是显式 ``off``。prepare 仍只建空实例，
  不导入材料、不注入。
* 所有决定/内容/失败共用**同一本持久请求账本**，本批新增硬上限 20；历史基线只记录、
  不读旧实例。20 不是每个子进程 20：P1/P2 用同一个 state_root 与同一个 run_id。
* 总运行窗口 1800 真实秒（monotonic，父进程在首个请求前持久写；跨 PID 共用，重开不延长），
  且不晚于所选批次的绝对截止（a/b 为 2026-10-30 17:00 CST，c 为 2026-10-31 01:00 CST；
  prereg/window/config/audit 都带同一个值）。业务时钟不快进。
* provider 沿既有 custom：地址由运行期 ``NYA02_MODEL_BASE_URL`` **显式提供**（仓库不保留
  真实网关地址）；``cn:deepseek-v4.1-flash`` / 温度 0 / 非 stream / 决策输出 4096 /
  内容输出 2048；不传 seed/top_p。缺地址时真实 prepare/run 在写窗口或发请求前拒绝。
  密钥只来自父级进程环境，不读取、不回显、不落盘。
* 默认 World gate 不动；d 的 World child 显式 ``off``，父 shell 的 canonical 不继承；
  无 World 动作必需；不外发、不碰旧 root、不删数据、不提交推送。

真实 run 的**决定权在模型**：本文件不预选候选、不改合法集、不指定 CONTINUE/NO_ACTION。
P2 的每个目标都由模型自选；没出现就按窗口/额度停并记 ``NOT_COVERED``，不补跑。

离线检查只跑本入口：HTTP connect 硬拒、决策/内容为**明确替身**、owner/runtime 是真的；
覆盖共享跨 PID 额度、每次发送前的截止/失败门、UNKNOWN 只对原 operation、CAS 真拒且只注入
一次、prepare/run 开关、audit 安全字段与正规 stop/drain。离线窗口/时刻可缩短，不据此声称
自然等待或真实模型成功。
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"),
               str(REPO_ROOT / "tests"), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "supply"),
               str(REPO_ROOT / "components" / "world" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2

# ---------------------------------------------------------------------------------------------
# 计划里的固定值（唯一来源；改这里等于改方案）
# ---------------------------------------------------------------------------------------------

#: 已预登记的三批固定身份。**只有这里的三批**：CLI 只暴露 ``--batch a|b|c``，
#: 不开放任意 root/run/预算/截止参数。三批的窗口/上限/模型参数相同，差异只在身份、
#: 历史基线，以及 c 自己的绝对截止（a/b 仍是 2026-10-30 17:00 CST）。
@dataclasses.dataclass(frozen=True)
class BatchProfile:
    key: str
    root: Path
    run_name: str
    domain_id: str
    expected_history: int
    deadline: datetime

    @property
    def run_id(self) -> str:
        return "run:" + self.run_name

    @property
    def authority_dir(self) -> Path:
        """独立 Memory authority：同 root 名加 ``-memory-authority`` 后缀。"""

        return self.root.parent / ("%s-memory-authority" % self.root.name)

    @property
    def authority_db(self) -> Path:
        return self.authority_dir / "authority.db"


BATCH_PROFILES: dict[str, BatchProfile] = {
    "a": BatchProfile("a", Path("/root/nyairo-g1-real-20261030"),
                      "g1-real-20261030", "g1:real:20261030", 98,
                      datetime(2026, 10, 30, 17, 0, 0,
                               tzinfo=timezone(timedelta(hours=8)))),
    "b": BatchProfile("b", Path("/root/nyairo-g1-real-20261030-b"),
                      "g1-real-20261030-b", "g1:real:20261030-b", 101,
                      datetime(2026, 10, 30, 17, 0, 0,
                               tzinfo=timezone(timedelta(hours=8)))),
    "c": BatchProfile("c", Path("/root/nyairo-g1-real-20261030-c"),
                      "g1-real-20261030-c", "g1:real:20261030-c", 121,
                      datetime(2026, 10, 31, 1, 0, 0,
                               tzinfo=timezone(timedelta(hours=8)))),
}

#: d 不是 G1 批，只做一次「材料变更后的暂停选择」观测：同样的窗口/上限/模型参数，
#: 但只有一个自然 phase，没有任何注入。它**独立注册**，不塞回 ``BATCH_PROFILES``——
#: 旧集合/旧映射保持三批原样，旧硬编码枚举断言因此不需要跟着改。
D_PROFILE = BatchProfile("d", Path("/root/nyairo-pause-observe-20261030-d"),
                         "pause-observe-20261030-d", "pause-observe:20261030-d", 141,
                         datetime(2026, 10, 31, 1, 0, 0,
                                  tzinfo=timezone(timedelta(hours=8))))

#: e 也不是 G1 批：一次「世界动作临时许可」的自然观测。与 d 共用单 natural phase，
#: 差别只在封闭身份，以及 World gate 只在**本进程树的三处环境映射**里显式 canonical
#: （runtime env / phase 子进程 env / 自有 World child env）。父 shell、实例默认、
#: ``.env`` 和 a/b/c/d 都不跟着变；d 仍显式 off。
E_PROFILE = BatchProfile("e", Path("/root/nyairo-world-observe-20261031-e"),
                         "world-observe-20261031-e", "world-observe:20261031-e", 148,
                         datetime(2026, 11, 1, 1, 0, 0,
                                  tzinfo=timezone(timedelta(hours=8))))

#: f 也不是 a/b/c 的历史批：G1 既有链的下一次限定采用（2026-10-31 决策单）。
#: 身份/截止/history 168 是封闭值；World gate 只在本进程树里显式 ``off``，
#: 所有 World 写面按实例 ``world_home`` 严格隔离（复用 e 的守卫），但**不启动**
#: World 服务、不做世界动作验收——这是作品续写批，不是世界批。
F_PROFILE = BatchProfile("f", Path("/root/nyairo-g1-real-20261031-f"),
                         "g1-real-20261031-f", "g1:real:20261031-f", 168,
                         datetime(2026, 11, 1, 1, 0, 0,
                                  tzinfo=timezone(timedelta(hours=8))))

#: 运行期可选身份：``_select_batch``/``_check_root``/CLI 都走这张表；
#: ``BATCH_PROFILES`` 仍是 a/b/c 三批的原始映射。
MODE_PROFILES: dict[str, BatchProfile] = {**BATCH_PROFILES, D_PROFILE.key: D_PROFILE,
                                          E_PROFILE.key: E_PROFILE, F_PROFILE.key: F_PROFILE}
#: 旧枚举**原样保留**（d 的既有断言钉着 ("a","b","c","d")）；CLI 另用
#: ``CLI_BATCH_CHOICES`` 多认 e。f 只由 ``g1_next_live`` 通过 ``_use_batch`` 进入，
#: 不扩这个旧 CLI 元组，d/e 的历史断言一个值都不动。
BATCH_CHOICES = tuple(BATCH_PROFILES) + (D_PROFILE.key,)
CLI_BATCH_CHOICES = BATCH_CHOICES + (E_PROFILE.key,)
DEFAULT_BATCH = "a"

#: 走「单 natural phase」的批（d/e）；a/b/c 仍是 p1/p2 的 G1 链。
NATURAL_PROFILES = frozenset({D_PROFILE.key, E_PROFILE.key})


def _is_natural(profile: Optional[BatchProfile] = None) -> bool:
    return (profile or _identity()).key in NATURAL_PROFILES


def _world_intent_gate(profile: Optional[BatchProfile] = None) -> Optional[str]:
    """该批**自己**钦定的 World gate 值；a/b/c 保持 ``None``（不碰原行为）。

    e 是 ``canonical``；d 是显式 ``off``（父 shell 里可能有的 canonical 不能漏进来）。
    返回值只用于本进程树的 env 映射，绝不写 ``os.environ``。
    """

    key = (profile or _identity()).key
    if key == E_PROFILE.key:
        return "canonical"
    if key in (D_PROFILE.key, F_PROFILE.key):
        # d 是自然观测；f 是作品续写。两者都不要求世界动作，gate 显式 off。
        return "off"
    return None

#: 默认批 a 的同名常量原样保留（旧测试/旧文档仍指同一份映射）；
#: 运行期身份一律经 ``_identity()`` 解析，不能直接拿这些常量当"当前批"。
PREREG_ROOT = BATCH_PROFILES[DEFAULT_BATCH].root
RUN_NAME = BATCH_PROFILES[DEFAULT_BATCH].run_name
RUN_ID = BATCH_PROFILES[DEFAULT_BATCH].run_id
DOMAIN_ID = BATCH_PROFILES[DEFAULT_BATCH].domain_id
#: 兼容常量：仍是 a 批的 17:00 截止。运行期的窗口/发送前/开跑前一律读
#: ``_identity().deadline``，不是这个常量——c 的 01:00 截止不能顺手延到 a/b。
DEADLINE = BATCH_PROFILES[DEFAULT_BATCH].deadline

REQUEST_CAP = 20          # 本批新增请求硬上限（决定 + 内容 + 失败预留都算）
EXPECTED_HISTORY = BATCH_PROFILES[DEFAULT_BATCH].expected_history  # 只记录，不读旧实例
WINDOW_SECONDS = 1800     # 总运行窗口（真实秒，monotonic，跨 PID）
CHECK_INTERVAL_MINUTES = 1  # runner 自己的检查窗口；prepare 前的固定实例参数
TICK_INTERVAL_SECONDS = 5.0
MAX_TICKS = 900
#: 离线自然 phase 的轮数上限：只用来把接线证明压到几十秒，真实 run 不用这条。
OFFLINE_NATURAL_MAX_ROUNDS = 4
UTC_DAY_CAP = 300

MODEL_PROVIDER = "custom"
#: 仓库不保留真实网关地址；真实入口从运行期 ``NYA02_MODEL_BASE_URL`` 显式取值。
#: 空串表示“未配置”，真实 prepare/run 在任何写入或请求前 fail-closed。
MODEL_BASE_URL = ""
MODEL_NAME = "cn:deepseek-v4.1-flash"
MODEL_TEMPERATURE = 0.0
DECISION_OUTPUT_CAP = 4096
CONTENT_OUTPUT_CAP = 2048

EXPERIMENT_DIRNAME = "experiment-g1"
PREREG_FILENAME = "g1_prereg.json"
BATCH_FILENAME = "g1_batch.json"
WINDOW_FILENAME = "g1_window.json"
RUN_REPORT_FILENAME = "g1_run_report.json"
PHASE_FILENAME = "g1-phase-%s.json"
STDERR_FILENAME = "g1-stderr-%s.log"
STOP_FILENAME = "g1_batch_stop.json"
CANDIDATE_TRACE_FILENAME = "g1-candidate-trace-%s.jsonl"

#: 实验观测（``--capture-candidates``，默认关闭）。只记 id/固定枚举/布尔/计数/顺序，
#: 不记 prompt 全文、正文、reasoning、title，也不做通用字段递归。
CANDIDATE_TRACE_SCHEMA = "g1-candidate-trace.v1"
CANDIDATE_TRACE_MAX_CANDIDATES = 40   # 每阶段观测副本上限；源集合本身不被裁剪
CANDIDATE_TRACE_MAX_RECORDS = 4000    # 整个 phase 文件的行数上限（有界）
STAGE_CANDIDATE_SET = "CANDIDATE_SET_MATERIALIZED"
STAGE_REQUEST = "REQUEST_LEGAL_SET"
STAGE_PROMPT = "PROMPT_FINAL"
STAGE_PROMPT_UNSENT = "PROMPT_BUILT_NOT_SENT"
STAGE_ROUND = "ROUND"
STAGE_EVENT = "EVENT"
#: 原 tick 之后的只读旁证；明确标 ``POST_TICK_READ``，不是发送前的原子快照。
STAGE_SNAPSHOT = "POST_TICK_SNAPSHOT"

#: 固定枚举白名单。每项都取自现成正式类型定义；不在表里的值一律记 ``UNKNOWN`` 并计数，
#: 原值不落盘（避免任意 ASCII 字符串被当成合法枚举）。
_TRACE_SOURCE_KINDS = frozenset({
    "CURRENT_ACTIVITY", "RESUME_ELIGIBLE", "USER_REQUEST", "COMMITMENT",
    "EXPLICIT_GOAL", "WORLD_OPPORTUNITY", "PERSONAL_OPPORTUNITY", "BLOCKED_ACTIVITY",
})
_TRACE_CANDIDATE_KINDS = frozenset({
    "CONTINUE_CURRENT", "RESUME_ACTIVITY", "RESPOND_USER_REQUEST", "FULFILL_COMMITMENT",
    "PURSUE_GOAL_STEP", "CONSIDER_EXPLICIT_GOAL", "CONSIDER_WORLD_OPPORTUNITY",
    "CONSIDER_PERSONAL_OPPORTUNITY", "BLOCKED_ACTIVITY",
})
_TRACE_DECISIONS = frozenset({
    "START", "CONTINUE", "PAUSE", "WAIT", "RESUME", "ABANDON", "DEFER", "NO_ACTION",
})
_TRACE_ACTIVITY_STATUSES = frozenset({
    "ACTIVE", "PAUSED", "WAITING", "BLOCKED", "COMPLETED", "FAILED", "ABANDONED",
    "CANCELLED", "EXPIRED",
})
_TRACE_INTERRUPTIBILITY = frozenset({"FREE", "LIGHT", "FOCUSED", "ATOMIC", "IMMEDIATE",
                                     "SAFE_BOUNDARY"})
_TRACE_PAUSE_SOURCES = frozenset({
    "WORK_HEAD_AFTER_NO_CHANGE", "READING_MATERIAL_NEXT_VERSION", "BODY_CAPACITY_RESTED",
    "PARK_UNTIL_CHOSEN_TIME",
})
_TRACE_PAUSE_WHY_NOT = frozenset({
    "NO_OFFERED_CONDITION", "NO_RUNNING_ACTIVITY", "BODY_NOT_READ", "BODY_NOT_LIMITED",
    "BODY_HAS_NO_NAMED_BURDEN",
})
_TRACE_WAKE_KINDS = frozenset({"EVENT", "CHECK_WINDOW"})
_TRACE_WAKE_STATUSES = frozenset({
    "DISABLED", "BLOCKED", "NOT_DUE", "DUPLICATE_EVENT", "IDLE_NO_CANDIDATE", "NO_ACTION",
    "DEFER", "PAUSED", "ABANDONED", "ACTED", "UNKNOWN", "UNSETTLED", "ERROR",
    "NO_STEP_AVAILABLE", "QUIET", "IN_TRANSIT", "PAUSED_BY_OPERATOR", "CONTACT_DECISION_HANDLED",
})
_TRACE_OUTCOMES = frozenset({
    "DISABLED", "BUDGET_EXHAUSTED", "NO_MATERIAL", "NO_ACTION", "DEFERRED", "SHARE_ACTED",
    "PAUSED", "ABANDONED", "DECISION_FAILED", "ADOPTION_REJECTED", "ACTED", "STEP_REFUSED",
    "STEP_UNKNOWN", "ALREADY_DONE", "STEP_UNSETTLED", "IN_TRANSIT", "PAUSED_BY_OPERATOR",
    "NO_STEP_AVAILABLE", "QUIET",
})
_TRACE_ACTION_STATUSES = frozenset({
    "PROPOSED", "PREPARED", "SCHEDULED", "SUBMITTED", "ACKNOWLEDGED", "SUCCEEDED", "FAILED",
    "CANCELLED", "UNKNOWN",
})
_TRACE_DECISION_ROUTES = frozenset({"RULE_RESOLVED", "COGNITIVE_REQUIRED", "NO_DECISION"})
_TRACE_REASON_CODES = frozenset({
    "NO_LEGAL_CANDIDATE", "CAPACITY_UNAVAILABLE", "CAPACITY_LIMITED",
    "CAPACITY_UNKNOWN_DEFERRED", "ATOMIC_IN_PROGRESS", "SAFE_BOUNDARY_PAUSE_RECORDED",
    "CONTINUITY_PREFERRED", "CURRENT_ACTIVITY_CONTINUITY", "TIME_WINDOW_CLOSING",
    "REQUEST_CURRENTLY_FEASIBLE", "USER_REQUEST_VALID", "USER_REQUEST_DEFERRED",
    "SWITCH_COST_HIGH", "SWITCH_COST_ACCEPTABLE", "RESOURCE_LIMIT", "NO_ACTION_CHOSEN",
    "RESUME_ELIGIBLE_READY", "COMMITMENT_FEASIBLE", "COMMITMENT_DEFERRED_BY_CAPACITY",
    "GOAL_STEP_ACTIONABLE", "GOAL_ATTENTION_DEFERRED", "WORLD_OPPORTUNITY_OBSERVED",
    "KNOWN_BLOCKING_WAIT", "ABANDON_NONTERMINAL_CHOSEN", "OPEN_ENDED_CONTINUATION",
    "PURE_CONSUMPTION_CHOSEN", "TIMING_NOT_APPROPRIATE", "ARTIFACT_HEAD_ADVANCED",
    "ARTIFACT_OWNER_REFUSED",
})
_TRACE_CONDITION_STATUSES = frozenset({
    "PENDING", "SATISFIED", "STALE", "CANCELLED", "EXPIRED", "SUPERSEDED",
})
_TRACE_CONDITION_KINDS = frozenset({
    "EVENT_MATCH", "TIME_AT_OR_AFTER", "TIME_WINDOW_OPEN", "EXTERNAL_RESULT_EVENT",
    "OBSERVED_CONDITION", "ALL", "ANY",
})
_TRACE_ELIGIBILITY_STATUSES = frozenset({
    "OPEN", "CONSUMED", "STALE", "CANCELLED", "EXPIRED", "SUPERSEDED",
})
_TRACE_TRIGGERS = frozenset({"p1", "p2", "p2-paused"})
_TRACE_EVENT_KINDS = frozenset({"OPERATOR_PAUSE_SET", "OPERATOR_PAUSE_RELEASED"})
_TRACE_STATE_CLASSES = frozenset({"blocked", "silence", "choice", "acted", "unknown"})

#: 已定义的 ID 前缀与载荷形状。未知前缀/形状一律不落盘。载荷只允许 ASCII 的
#: 十六进制或结构化片段，绝不接受自由文本。
_TRACE_ID_PAYLOADS = {
    "cand": r"^[0-9a-f]{16}$",
    "semcand": r"^[0-9a-f]{16}$",
    "cset": r"^[0-9a-f]{16}$",
    "dec": r"^[0-9a-f]{16}$",
    "actn": r"^[0-9a-f]{16}$",
    "rcond": r"^[0-9a-f]{16}$",
    "relig": r"^[0-9a-f]{16}$",
    "agnd": r"^[0-9a-f]{16}$",
    "acc": r"^[0-9a-f]{16}$",
    "waitbasis": r"^[0-9a-f]{16}$",
    "wait_dep": r"^artifact\.[0-9a-f]{16}\.[0-9a-f]{16}$",
    # 正式链的 owner id 是十六进制/UUID；版本号还会带一层已知的
    # ``artifact-version:`` 前缀（真实样本见 G1 批次报告）。
    "artifact": r"^[0-9a-fA-F-]{8,64}$",
    "artifactversion": r"^(?:artifact-version:)?[0-9a-fA-F-]{8,64}$",
    "act": r"^[0-9a-fA-F-]{8,64}$",
    "actv": r"^[0-9a-fA-F-]{8,64}$",
    "activity": r"^[0-9a-fA-F-]{8,64}$",
}
_TRACE_CANDIDATE_ID_PREFIXES = frozenset({"cand", "semcand"})
_TRACE_ACTIVITY_ID_PREFIXES = frozenset({"act", "actv", "activity"})
#: 这些前缀在正式链里也可能写成 ``prefix_payload``（Activity id 就是这样）。
_TRACE_UNDERSCORE_ID_PREFIXES = frozenset({"act", "actv", "activity"})

#: 异常消息可能夹带私文/路径，只保留有界异常类型名；不在表内一律 ``ERROR``。
_TRACE_ERROR_TYPES = frozenset({
    "OSError", "PermissionError", "FileNotFoundError", "IsADirectoryError", "NotADirectoryError",
    "ValueError", "TypeError", "KeyError", "AttributeError", "IndexError", "RuntimeError",
    "LookupError", "TimeoutError", "BrokenPipeError", "UnicodeDecodeError", "UnicodeError",
    "ImportError", "ModuleNotFoundError", "NotImplementedError", "AssertionError",
    "JSONDecodeError", "UnsupportedOperation",
})
#: prompt/发送失败分类的有界白名单（正式 failure_class 与预算原因码）。
_TRACE_FAILURE_TOKENS = frozenset({
    "MODEL_TIMEOUT", "MODEL_REFUSAL", "INVALID_JSON", "INVALID_SCHEMA",
    "DECISION_ATTEMPT_FAILED:MODEL_TIMEOUT", "NO_VALID_DECISION:MODEL_REFUSAL",
    "DECISION_ATTEMPT_FAILED:INVALID_JSON", "DECISION_ATTEMPT_FAILED:INVALID_SCHEMA",
    "INVALID_MODEL_OUTPUT:INVENTED_CANDIDATE",
    "INVALID_MODEL_OUTPUT:HARD_CONSTRAINT_OVERRIDE_FORBIDDEN",
    "INVALID_MODEL_OUTPUT:ILLEGAL_DECISION_ENUM",
    "INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE",
    "INVALID_MODEL_OUTPUT:ATOMIC_BOUNDARY_VIOLATION",
    "INVALID_MODEL_OUTPUT:UNKNOWN_REASON_CODE", "STALE_BEFORE_COMMIT",
    "DECISION_ATTEMPT_FAILED:INTERRUPTED_DURING_COGNITION",
    "DECISION_ATTEMPT_FAILED:CANDIDATE_EXPLOSION", "INVALID_MODEL_OUTPUT:INVENTED_METHOD_REF",
    "INVALID_MODEL_OUTPUT:INVALID_METHOD_TRIAL",
    "INVALID_MODEL_OUTPUT:FORBIDDEN_HIDDEN_REASONING",
    "INVALID_MODEL_OUTPUT:FORBIDDEN_UNIVERSAL_UTILITY",
    "PROVIDER_RATE_LIMITED", "PROVIDER_SERVER_ERROR", "PROVIDER_MALFORMED_RESPONSE",
    "PROVIDER_AUTH_ERROR", "PROVIDER_UNAVAILABLE", "PROVIDER_OUTPUT_TRUNCATED",
    "PROVIDER_REASONING_ONLY", "PROVIDER_EMPTY_CONTENT", "PROVIDER_REQUEST_REJECTED",
    "COGNITION_BUDGET_EXHAUSTED", "REQUEST_BUDGET_STATE_CORRUPT",
    "REQUEST_BUDGET_UTC_DAY_LIMIT", "REQUEST_BUDGET_RUN_WINDOW_LIMIT",
    "REQUEST_BUDGET_RUN_WINDOW_NOT_AUTHORIZED", "REQUEST_BUDGET_UNREADABLE",
    "REQUEST_NOT_SENT", "PROMPT_BUILT_NOT_SENT", "NO_STRUCTURED_PROMPT",
})
#: 「直接证据表明没有发出」的失败分类/原因码；其余异常一律 UNKNOWN，异常本身也可能是发送后超时。
_TRACE_CERTIFIED_NOT_SENT = frozenset({
    "REQUEST_BUDGET_STATE_CORRUPT", "REQUEST_BUDGET_UTC_DAY_LIMIT",
    "REQUEST_BUDGET_RUN_WINDOW_LIMIT", "REQUEST_BUDGET_RUN_WINDOW_NOT_AUTHORIZED",
    "REQUEST_BUDGET_UNREADABLE", "COGNITION_BUDGET_EXHAUSTED", "REQUEST_NOT_SENT",
})
SEND_STATUS_SENT = "SENT"
SEND_STATUS_NOT_SENT = "NOT_SENT"
SEND_STATUS_UNKNOWN = "UNKNOWN"
_TRACE_SEND_EVIDENCE = _TRACE_FAILURE_TOKENS | frozenset({
    "RESPONSE_RETURNED", "PRE_SEND_REFUSAL", "DIRECT_BUILD",
})
_TRACE_ERROR_TOKENS = _TRACE_FAILURE_TOKENS | _TRACE_ERROR_TYPES

#: d 批**唯一**新增的观测：两条编译期常量，逐字判断是否出现在同一次 ``build_prompt``
#: 返回的最终结构化 shape 的 ``pause_condition.how`` 里。只做精确子串判断——不 regex、
#: 不做关键词/归一化扫描，也不把命中当成发送成功或模型理解。两条文本分别对应
#: ``AutonomousLifeCycle.pause_condition_view`` 写进 ``how`` 的 ABANDON 代价句与恢复尾段。
D_PAUSE_FACT_SENTENCE = (
    "While the Activity is PAUSED, ABANDON is not available through the formal decision "
    "path; if you want to abandon it, make that choice while it is still ACTIVE.")
D_PAUSE_TAIL = (
    "OPEN and nothing resumes by itself, and a later Decision may choose RESUME; a "
    "background RESUME never preempts another foreground activity")

#: d 的自然 phase 名（``g1-phase-natural.json`` / ``g1-candidate-trace-natural.jsonl``）。
PHASE_NATURAL = "natural"

#: 原 operation 查询只有在 owner 明确回「确证成功」时才算已对账；其余一律不升级。
RECONCILE_SUCCESS_RESULTS = frozenset({"CONFIRMED_SUCCEEDED"})
#: CAS 拒的权威原因：owner 自己回的 ``CONFLICT``（adapter 只在 stale expected_head 分支产生）。
CAS_OWNER_CONFLICT = "CONFLICT"

PHASE_PREFIX = "G1_PHASE "
OFFLINE_PREFIX = "g1-real-offline-"

#: 中性 operator 材料（与既有 demo 同一份来源文本；不是她的经历，也不含私人内容）。
MATERIAL_TITLE = "田野笔记"
MATERIAL_TEXT = "第一章：村里的水井在旱季仍然有水。"
MATERIAL_NOTE = "为什么写到第三章就卡住？"
CONTROL_TITLE = "operator 对照作品"
CONTROL_TEXT = "这是实验 operator 事先放下的对照文本，不属于她的经历，也不参与任何自然成果判定。"

REASON_ROOT_NOT_ABSOLUTE = "ROOT_NOT_ABSOLUTE"
REASON_ROOT_NOT_PREREGISTERED = "ROOT_NOT_PREREGISTERED"
REASON_ROOT_NOT_FRESH = "ROOT_NOT_FRESH"
REASON_ALREADY_PREPARED = "ROOT_ALREADY_PREPARED"
REASON_PREPARE_FAILED = "PREPARE_FAILED"
REASON_MATERIAL_FAILED = "MATERIAL_SETUP_FAILED"
REASON_MEMORY_BIND_FAILED = "MEMORY_BIND_FAILED"
REASON_LEDGER_NOT_ZERO = "LEDGER_NOT_ZERO_AFTER_PREPARE"
REASON_LEDGER_UNREADABLE = "REQUEST_LEDGER_UNREADABLE"
REASON_NOT_PREPARED = "EXPERIMENT_NOT_PREPARED"
REASON_EXECUTE_FLAG_MISSING = "RUN_REQUIRES_EXECUTE_APPROVED"
REASON_DEADLINE_PASSED = "RUN_WINDOW_DEADLINE_PASSED"
REASON_ALREADY_STARTED = "RUN_ALREADY_STARTED"
REASON_NO_MODEL_CONFIG = "NO_REAL_MODEL_CONFIG"
REASON_MODEL_PARAM_MISMATCH = "PREREG_MODEL_PARAM_MISMATCH"
REASON_SUPPLY_UNAVAILABLE = "SUPPLY_OWNER_UNAVAILABLE"
REASON_WORLD_NOT_READY = "WORLD_OWNER_NOT_READY"
REASON_ASSEMBLY_NOT_READY = "ASSEMBLY_NOT_READY"
REASON_REQUEST_CAP = "REQUEST_CAP_REACHED"
REASON_PROVIDER_FAILURE = "PROVIDER_OR_TRANSPORT_FAILURE"
REASON_WINDOW_ELAPSED = "OBSERVATION_WINDOW_ELAPSED"
REASON_RUN_FINISHED = "RUN_FINISHED"
REASON_UNEXPECTED = "UNEXPECTED_ERROR"
REASON_PHASE_FAILED = "PHASE_PROCESS_FAILED"
REASON_UNKNOWN_BATCH = "UNKNOWN_BATCH"
REASON_BATCH_IDENTITY_MISMATCH = "BATCH_IDENTITY_MISMATCH"
REASON_AUTHORITY_NOT_FRESH = "MEMORY_AUTHORITY_NOT_FRESH"
#: 自然批的 World gate 必须就是该批自己的固定值（e=canonical，d=off），
#: 缺字段/带别的批的值/试图用父进程继承值替换都算身份不一致。
REASON_WORLD_GATE_MISMATCH = "WORLD_GATE_MISMATCH"
#: e 的路径守卫：实例 world_home 缺失/不可读/相对/解析后落在根外（含符号链接逃逸）
#: 一律在起服务、发请求、写 started/window 之前拒绝；子进程自己的 env 与固定映射
#: 不一致时同样拒绝，不能只回一个 false 继续跑。
REASON_WORLD_HOME_UNAVAILABLE = "WORLD_HOME_UNAVAILABLE"
REASON_WORLD_HOME_OUTSIDE_ROOT = "WORLD_HOME_OUTSIDE_ROOT"
REASON_WORLD_ENV_MISMATCH = "WORLD_CHILD_ENV_MISMATCH"
#: 离线夹具参考时刻只能用在 mkdtemp 根里；本进程一旦带上它却指向真实根，写入之前拒绝。
REASON_OFFLINE_CLOCK_REFUSED = "OFFLINE_CLOCK_REFUSED"

CLASS_FAILURE = "CALL_FAILURE"


class Refused(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(("%s: %s" % (reason_code, detail)) if detail else reason_code)
        self.reason_code = reason_code
        self.detail = detail


#: 当前进程选择的批次。默认 a（与原入口完全一致）；CLI 显式 ``--batch`` 或子进程
#: 从 config 里读回时才会切换，且切换只在这里发生。
_ACTIVE_BATCH: BatchProfile = BATCH_PROFILES[DEFAULT_BATCH]


def _identity() -> BatchProfile:
    """当前批次的封闭身份。所有 run_id/域/root/历史基线都从这里取，不读模块常量。"""

    return _ACTIVE_BATCH


def _select_batch(key: Any) -> BatchProfile:
    profile = MODE_PROFILES.get(str(key or "").strip())
    if profile is None:
        raise Refused(REASON_UNKNOWN_BATCH, "unknown batch %r" % (key,))
    return profile


@contextlib.contextmanager
def _use_batch(key: Any):
    global _ACTIVE_BATCH

    profile = _select_batch(key)
    previous = _ACTIVE_BATCH
    _ACTIVE_BATCH = profile
    try:
        yield profile
    finally:
        _ACTIVE_BATCH = previous


# ---------------------------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------------------------


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="milliseconds")


# ---------------------------------------------------------------------------------------------
# 离线夹具的显式参考时刻：只存在于本进程内存，不写环境里的任意时间、不落盘
# ---------------------------------------------------------------------------------------------

#: 参考时刻只由 ``offline_clock()`` / ``enter_offline_fixture()`` 在**本进程**设置。
#: 它不读环境里的时间戳，也不写进窗口/批/prereg 任何落盘文件——窗口没有"我是离线"这类
#: 标记，正式进程看到同一份窗口仍按真实墙钟判。
#:
#: 父级确实要用子进程跑 phase 时，只在**这一次实际离线调用路径**（``start_run(offline=True)``
#: → ``_run_phase_process``）构造的子进程 env 里放一个布尔标记；子进程还必须同时满足
#: config.offline、scratch 根两道校验才采纳，缺一项都退回真实墙钟/明确拒绝。
OFFLINE_FIXTURE_ENV = "NYAIRO_G1_OFFLINE_FIXTURE"

#: 本进程当前是否处于离线夹具参考时刻（None 表示正式进程，用真实墙钟）。
_OFFLINE_REFERENCE: Optional[datetime] = None


def offline_reference(profile: Optional[BatchProfile] = None) -> datetime:
    """离线夹具的参考时刻：本批截止前 1 小时，绝不把截止延到今天。"""

    profile = profile or _identity()
    return profile.deadline - timedelta(hours=1)


def offline_reference_iso(profile: Optional[BatchProfile] = None) -> str:
    return offline_reference(profile).isoformat()


def _is_offline_scratch_root(root: Path | str) -> bool:
    """离线夹具参考时刻只对系统临时目录下的夹具根生效；真实根一律不算。"""

    try:
        resolved = Path(str(root)).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
    except OSError:
        return False
    return resolved != temp_root and temp_root in resolved.parents


def _offline_clock_refusal(root: Path | str, *,
                           offline: Optional[bool] = None) -> Optional[dict[str, Any]]:
    """本进程处于离线夹具参考时刻却指向真实根：任何写入之前失败关闭。

    ``offline`` 是调用方**实际**要用的离线布尔（``prepare``/``start_run`` 的实参）：
    参考会话里再传 ``offline=False`` 等于想静默退回真实墙钟/加载真实模型配置，
    同样在任何写入之前拒绝，不静默退回。只拿得到 root 的内部口（``_write_window``、
    ``_deadline_passed`` 等）不传这个参数，仍按原来的 scratch 判定挡住真实根。
    """

    if _OFFLINE_REFERENCE is None:
        return None
    if offline is False:
        return {"ok": False, "reason_code": REASON_OFFLINE_CLOCK_REFUSED,
                "detail": ("an offline fixture session refuses an explicit offline=False "
                           "call instead of silently falling back to the real clock"),
                "root": str(root), "offline": False}
    if not _is_offline_scratch_root(root):
        return {"ok": False, "reason_code": REASON_OFFLINE_CLOCK_REFUSED,
                "detail": "offline fixture reference is only valid inside a scratch root",
                "root": str(root)}
    return None


def _wall_now() -> datetime:
    """开跑/写窗口用的墙钟；只有本进程显式进入离线夹具时才冻结。"""

    return _OFFLINE_REFERENCE or datetime.now(timezone.utc)


@contextlib.contextmanager
def offline_clock(profile: Optional[BatchProfile] = None):
    """临时进入离线夹具参考时刻（只改本进程内存，真实根仍写入前被拒）。"""

    global _OFFLINE_REFERENCE
    previous = _OFFLINE_REFERENCE
    _OFFLINE_REFERENCE = offline_reference(profile)
    try:
        yield
    finally:
        _OFFLINE_REFERENCE = previous


def enter_offline_fixture(profile: Optional[BatchProfile] = None) -> None:
    """显式进入离线夹具参考时刻；只给离线夹具/测试用，正式入口从不调用。"""

    global _OFFLINE_REFERENCE
    _OFFLINE_REFERENCE = offline_reference(profile)


def _read_json(path: Path | str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _write_json(path: Path | str, payload: Mapping[str, Any], *, private: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)


def _experiment_dir(root: Path | str) -> Path:
    return Path(root) / EXPERIMENT_DIRNAME


def _bare(value: Any) -> str:
    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


#: 同一个 owner 版本在现有链上被证明只有三种拼法，指向的是同一个版本：
#: * ``artifact-version:<uuid4 hex>``：owner 铸版本 id 时加的前缀
#:   （``components/supply/lifysupply/artifact/store.py:707``）；
#: * ``artifactversion:artifact-version:<hex>``：Life 在 owner 原样外面再包一层命名空间
#:   （``autonomous_life_cycle._owner_evidence`` 第 9638 行）；
#: * ``artifactversion:<hex>``：Life 先剥掉 owner 前缀再包一层
#:   （``autonomous_life_cycle.py`` 第 4232—4234、9315 行）。
#: 重复包装、裸 hex、别的命名空间都不认——比较只用来识别，原始 refs 照原样保留。
_VERSION_REF_RE = re.compile(
    r"^(?:artifactversion:artifact-version:|artifactversion:|artifact-version:)([0-9a-f]{32})$")


def _version_token(value: Any) -> str:
    """把上面三种既有写法还原成 owner 版本号本身；不认识的写法一律返回空串。"""

    match = _VERSION_REF_RE.match(str(value or "").strip())
    return match.group(1) if match else ""


def _same_version_ref(left: Any, right: Any) -> bool:
    """同一 owner 版本的不同合法写法算同一个；空值、未知格式、异版本绝不互相冒认。"""

    token = _version_token(left)
    return bool(token) and token == _version_token(right)


def _owner_version_from_wakes(wakes: Sequence[Mapping[str, Any]]) -> tuple[bool, str]:
    """本轮 owner 自己确认新建的版本；以第一条明确 ``True`` 的记录为准，其余不猜。"""

    for wake in wakes or ():
        if wake.get("owner_version_created") is True:
            return True, str(wake.get("owner_version_id") or "")
    return False, ""


def _p1_confirmed(*, owner_created: bool, owner_version_id: str, receipt_artifact: str,
                  receipt_activity: str, excluded: Sequence[str],
                  point: Mapping[str, Any]) -> bool:
    """A 必须是「本轮 owner 明确新建的版本 + 本轮 receipt 指向这件作品与正式 Activity」。

    ``point`` 是 owner 对**某一具体作品**重读回来的续作点：``artifact`` 是 owner 自报的对象
    身份，``head``/``versions`` 是这个对象的当前 head。两件事都要对上——owner 读的必须正是
    receipt 指向的那件作品，且它的 head 就是本轮记录的那个版本 ref（三种既有写法都算）。
    换一件作品的 head 进来、或把值凑成一样，都不能让结论变成 True；读不到一律失败关闭。
    """

    return bool(owner_created
                and owner_version_id
                and receipt_artifact
                and receipt_artifact not in {_bare(ref) for ref in excluded}
                and receipt_activity
                and int(point.get("versions") or 0) >= 1
                and _same_ref(point.get("artifact"), receipt_artifact)
                and _same_version_ref(point.get("head"), owner_version_id))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_root(root: Path, *, expected: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """根必须是所选批次预登记的绝对路径；另一批的根单独给具名理由。

    ``expected`` 缺省时按**当前批次**解析（不是导入时绑定的旧默认），错批启动因此失败关闭。
    """

    expected_path = Path(str(expected)) if expected is not None else _identity().root
    if not root.is_absolute():
        return {"ok": False, "reason_code": REASON_ROOT_NOT_ABSOLUTE, "root": str(root)}
    if str(root) != str(expected_path):
        others = [profile for key, profile in MODE_PROFILES.items()
                  if key != _identity().key and str(profile.root) == str(root)]
        if others:
            return {"ok": False, "reason_code": REASON_BATCH_IDENTITY_MISMATCH,
                    "expected": str(expected_path), "root": str(root),
                    "root_belongs_to": others[0].key}
        return {"ok": False, "reason_code": REASON_ROOT_NOT_PREREGISTERED,
                "expected": str(expected_path), "root": str(root)}
    return None


def _verify_instance_identity(root: Path | str) -> Optional[dict[str, Any]]:
    """已 prepare 的根必须属于**当前批次**；错批/缺身份一律失败关闭。

    prereg 与 batch 两个落盘文件都要核：run_id、域、root 与当前 profile 完全一致；
    ``state_root`` 必须在根内。旧批 a 的 prereg 没有 ``batch`` 字段，按缺省 a 读回；
    其它缺批/错批记录都不认。
    """

    profile = _identity()
    root_path = Path(str(root))
    experiment = _experiment_dir(root_path)
    prereg = _read_json(experiment / PREREG_FILENAME)
    if not prereg:
        return {"ok": False, "reason_code": REASON_NOT_PREPARED, "root": str(root_path)}
    recorded = str(prereg.get("batch") or "")
    if recorded != profile.key and not (not recorded and profile.key == DEFAULT_BATCH):
        return {"ok": False, "reason_code": REASON_BATCH_IDENTITY_MISMATCH,
                "field": "batch", "expected": profile.key, "actual": recorded}
    for field, wanted in (("run_id", profile.run_id), ("domain_id", profile.domain_id),
                          ("root", str(root_path))):
        if str(prereg.get(field) or "") != wanted:
            return {"ok": False, "reason_code": REASON_BATCH_IDENTITY_MISMATCH,
                    "field": field, "expected": wanted, "actual": prereg.get(field)}
    if not str(prereg.get("state_root") or "").startswith(str(root_path)):
        return {"ok": False, "reason_code": REASON_BATCH_IDENTITY_MISMATCH,
                "field": "state_root", "actual": prereg.get("state_root")}
    # prereg 里写的截止必须就是所选批次自己的截止：a/b 的旧记录仍是 17:00，c 是 01:00；
    # 缺字段/写错批次的截止都拒绝，不能靠运行时换一个更宽的时间。
    recorded_deadline = str(prereg.get("deadline") or "")
    try:
        deadline_matches = (datetime.fromisoformat(recorded_deadline) == profile.deadline)
    except ValueError:
        deadline_matches = False
    if not deadline_matches:
        return {"ok": False, "reason_code": REASON_BATCH_IDENTITY_MISMATCH,
                "field": "deadline", "expected": profile.deadline.isoformat(),
                "actual": recorded_deadline or None}
    batch = _read_json(experiment / BATCH_FILENAME)
    if batch:
        for field, wanted in (("run_id", profile.run_id), ("domain_id", profile.domain_id)):
            if str(batch.get(field) or "") != wanted:
                return {"ok": False, "reason_code": REASON_BATCH_IDENTITY_MISMATCH,
                        "field": "batch.%s" % field, "expected": wanted,
                        "actual": batch.get(field)}
    return None


# ---------------------------------------------------------------------------------------------
# 共享运行窗口：monotonic + 墙钟，父进程写一次，两个子进程都只读
# ---------------------------------------------------------------------------------------------


def _window_path(root: Path | str) -> Path:
    return _experiment_dir(root) / WINDOW_FILENAME


def _write_window(root: Path | str, *, window_seconds: int = WINDOW_SECONDS) -> dict[str, Any]:
    path = _window_path(root)
    if path.exists():
        raise Refused(REASON_ALREADY_STARTED, "the run window already exists")
    clock_refusal = _offline_clock_refusal(root)
    if clock_refusal is not None:
        # 真实根不允许借离线时钟写一个"还没到期"的窗口。
        raise Refused(REASON_OFFLINE_CLOCK_REFUSED,
                      str(clock_refusal.get("detail") or ""))
    now_wall = _wall_now()
    start_mono = time.monotonic()
    deadline = _identity().deadline
    wall_deadline = min(now_wall + timedelta(seconds=int(window_seconds)), deadline)
    payload = {
        "schema": "g1.window.v1", "run_id": _identity().run_id,
        "window_seconds": int(window_seconds),
        "started_monotonic": start_mono,
        "deadline_monotonic": start_mono + float(window_seconds),
        "started_wall": _iso(now_wall),
        "deadline_wall": _iso(wall_deadline),
        "deadline_basis": "min(now+window, %s)" % deadline.isoformat(),
    }
    _write_json(path, payload, private=True)
    return payload


def _deadline_passed(root: Path | str) -> bool:
    """每次发送前都要过这一道；窗口文件缺失/损坏一律 fail-closed。

    时间基准是**本进程**的墙钟：正式进程读真实时间，只有显式进入离线夹具的进程才读
    参考时刻。窗口落盘里没有任何"我是离线"的标记可用，读同一份窗口的正式进程仍会
    按真实时间判它是否到期。
    """

    window = _read_json(_window_path(root))
    if not window:
        return True
    try:
        if time.monotonic() >= float(window["deadline_monotonic"]):
            return True
        wall = datetime.fromisoformat(str(window["deadline_wall"]))
    except (KeyError, TypeError, ValueError):
        return True
    return _wall_now() >= wall


def _window_view(root: Path | str) -> dict[str, Any]:
    window = _read_json(_window_path(root))
    if not window:
        return {"present": False}
    return {"present": True, "run_id": window.get("run_id"),
            "window_seconds": window.get("window_seconds"),
            "started_wall": window.get("started_wall"),
            "deadline_wall": window.get("deadline_wall"),
            "deadline_basis": window.get("deadline_basis")}


def _stop_path(root: Path | str) -> Path:
    return _experiment_dir(root) / STOP_FILENAME


def _record_batch_stop(root: Path | str, reason_code: str) -> None:
    """P1 的失败必须能被 P2 的新锁读到：第一次写下的原因即最终原因。"""

    if not reason_code:
        return
    path = _stop_path(root)
    if path.exists():
        return
    try:
        _write_json(path, {"schema": "g1.batchstop.v1", "run_id": _identity().run_id,
                           "reason_code": str(reason_code), "at": _iso()}, private=True)
    except Exception:  # noqa: BLE001 - 落盘失败由本进程的锁继续兜底
        pass


def _durable_stop(root: Path | str) -> str:
    row = _read_json(_stop_path(root))
    return str(row.get("reason_code") or "")


# ---------------------------------------------------------------------------------------------
# 持久请求账本与三道发送前门
# ---------------------------------------------------------------------------------------------


def _budget(state_root: Path | str):
    from life_request_budget import LifeRequestBudget

    return LifeRequestBudget(state_root, utc_day_max=UTC_DAY_CAP,
                             run_window_max=REQUEST_CAP, run_id=_identity().run_id)


def _ledger_calls(budget: Any) -> Optional[int]:
    try:
        view = budget.view()
    except Exception:
        return None
    if str(view.get("state") or "") == "CORRUPT":
        return None
    calls = view.get("run_window_calls")
    # 账本还不存在 = 0 次；只有损坏/读不出来才是 None（fail-closed）。
    return 0 if calls is None else int(calls)


def _ledger_view(budget: Any) -> dict[str, Any]:
    try:
        view = budget.view()
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        return {"state": "UNREADABLE", "detail": "%s: %s" % (type(exc).__name__, exc)}
    return {"state": view.get("state"), "run_id": view.get("run_id"),
            "run_window_calls": view.get("run_window_calls"),
            "run_window_max": view.get("run_window_max"),
            "utc_day_calls": view.get("utc_day_calls"),
            "history": dict(view.get("history") or {})}


class SendLatch:
    """批次级「立刻停发」锁：截止或供应商失败后，后面的发送连预留都不发生。

    ``on_stop`` 让停因落盘；P2 是另一个子进程、另一把锁，只有落盘的停因才能阻止它复活发送。
    """

    def __init__(self, deadline_check: Callable[[], bool], *,
                 on_stop: Optional[Callable[[str], None]] = None) -> None:
        self.deadline_check = deadline_check
        self.on_stop = on_stop
        self.stop_reason = ""
        self.refused_sends = 0

    @property
    def closed(self) -> bool:
        return bool(self.stop_reason)

    def stop(self, reason_code: str) -> None:
        if not self.stop_reason:
            self.stop_reason = str(reason_code)
            if self.on_stop is not None:
                try:
                    self.on_stop(self.stop_reason)
                except Exception:  # noqa: BLE001 - 记不下来也不能放行发送
                    pass

    def check(self) -> None:
        if self.deadline_check():
            self.stop(REASON_DEADLINE_PASSED)
        if self.stop_reason:
            self.refused_sends += 1
            raise GateClosed(self.stop_reason, "send latch closed")


class GateClosed(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail


def _failure_of(result: Any) -> Any:
    failure = getattr(result, "failure_class", None)
    if failure is None and isinstance(result, Mapping):
        failure = result.get("failure_class")
    return failure


def _is_failure(failure: Any) -> bool:
    import history_choice_experiment as base

    return base.classify_failure(failure) == CLASS_FAILURE


def _certified_not_sent_token(exc: BaseException) -> str:
    """只有明确「发送前就被拒绝」的异常才返回 token；其余（可能发送后超时）返回空。"""

    try:
        import life_pause

        if isinstance(exc, life_pause.RequestNotSentError):
            return "REQUEST_NOT_SENT"
    except Exception:  # noqa: BLE001 - classification must never break the send path
        pass
    for attribute in ("reason_code", "failure_class"):
        token = str(getattr(exc, attribute, "") or "")
        if token in _TRACE_CERTIFIED_NOT_SENT:
            return token
    return ""


class G1CognitionGate:
    """决策口：发送锁 → 本批额度 → 内层正式 adapter（那里才真正 reserve）。

    同一 attempt 的 schema 重试不放行（一次尝试一次计费，没有自动第二次）。
    ``trace`` 只做观测（默认 None）：在**同一次**调用前后取内层已经产生的 request /
    prompt 结构，不二次构造、不读 prompt 文本、不改任何参数。
    """

    def __init__(self, inner: Any, *, latch: SendLatch, budget: Any,
                 trace: Optional[CandidateTrace] = None) -> None:
        self.inner = inner
        self.latch = latch
        self.budget = budget
        # 关闭时不保留 trace 对象：发送路径连一次只读计数都不多做的保证在这里落地。
        self.trace = trace if (trace is not None and trace.enabled) else None
        self.sends = 0

    @property
    def send_split_capable(self) -> bool:
        return bool(getattr(self.inner, "send_split_capable", False))

    def build_prompt(self, request: Any) -> tuple[str, dict[str, Any]]:
        return self.inner.build_prompt(request)

    def invoke(self, request: Any, *, schema_retry_index: int = 0) -> Any:
        if int(schema_retry_index or 0) > 0:
            raise GateClosed("MODEL_ATTEMPT_RETRY_REFUSED", "schema retry is a second attempt")
        self.latch.check()
        calls = _ledger_calls(self.budget)
        if calls is None:
            raise GateClosed(REASON_LEDGER_UNREADABLE, "run window count is unreadable")
        if calls >= REQUEST_CAP:
            raise GateClosed(REASON_REQUEST_CAP, "batch used %d of %d" % (calls, REQUEST_CAP))
        self.sends += 1
        if self.trace is not None:
            self.trace.record_request(request)
        # 默认关闭时连这次只读计数都不做：业务路径与原来完全一致。
        seen_before = self._prompt_count() if self.trace is not None else -1
        try:
            result = self.inner.invoke(request, schema_retry_index=schema_retry_index)
        except Exception as exc:
            refusal = _certified_not_sent_token(exc)
            if refusal:
                self._trace_prompt(seen_before, send_status=SEND_STATUS_NOT_SENT,
                                   evidence="PRE_SEND_REFUSAL", error=refusal)
            else:
                # 异常本身也可能是发送后超时/丢答：没有直接证据就不判未发送。
                self._trace_prompt(seen_before, send_status=SEND_STATUS_UNKNOWN,
                                   evidence="", error=_trace_error(exc))
            # 抛异常等于结果未知：先停整批，再把异常原样交给上层，绝不让下一次发送发生。
            self.latch.stop(REASON_PROVIDER_FAILURE)
            raise
        # 正常返回只证明拿到了响应，不证明实际网络发送（也可能来自重放）。
        self._trace_prompt(seen_before, send_status=SEND_STATUS_UNKNOWN,
                           evidence="RESPONSE_RETURNED", error=_failure_of(result))
        if _is_failure(_failure_of(result)):
            self.latch.stop(REASON_PROVIDER_FAILURE)
        return result

    def _prompt_count(self) -> int:
        """内层 adapter 已构造的 prompt 条数；拿不到返回 -1（只读，不触发任何构造）。"""

        try:
            rows = getattr(self.inner, "requests_seen", None)
            return len(rows) if isinstance(rows, list) else -1
        except Exception:  # noqa: BLE001 - observability never breaks the send
            return -1

    def _trace_prompt(self, before: int, *, send_status: str, evidence: str,
                      error: Any) -> None:
        if self.trace is None:
            return
        try:
            token = ""
            if isinstance(error, str):
                token = _trace_token(error, _TRACE_ERROR_TOKENS) or ""
            elif error is not None:
                # 未知对象/异常不抄 str()：只给固定分类。
                token = _trace_error(error) if isinstance(error, BaseException) else ""
            # 读取的是「本次 build 返回后」内层已经存在的最后一条结构化 shape；
            # 不调用 build_prompt，也不重新排序/裁剪。
            rows = getattr(self.inner, "requests_seen", None)
            shape = rows[-1] if isinstance(rows, list) and before >= 0 and len(rows) > before \
                else None
            self.trace.record_prompt(shape, send_status=send_status, evidence=evidence,
                                     error=token)
        except Exception as exc:  # noqa: BLE001
            self.trace.error = self.trace.error or _trace_error(exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class G1ContentGate:
    """内容口：与决策口共用同一把锁和同一本账本。"""

    def __init__(self, inner: Any, *, latch: SendLatch, budget: Any) -> None:
        self.inner = inner
        self.latch = latch
        self.budget = budget
        self.sends = 0

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.latch.check()
        calls = _ledger_calls(self.budget)
        if calls is None:
            raise GateClosed(REASON_LEDGER_UNREADABLE, "run window count is unreadable")
        if calls >= REQUEST_CAP:
            raise GateClosed(REASON_REQUEST_CAP, "batch used %d of %d" % (calls, REQUEST_CAP))
        self.sends += 1
        try:
            result = self.inner.complete(*args, **kwargs)
        except Exception:
            # content.complete 抛异常时，旧实现不会关锁，后续发送仍可能发生。
            self.latch.stop(REASON_PROVIDER_FAILURE)
            raise
        if _is_failure(_failure_of(result)):
            self.latch.stop(REASON_PROVIDER_FAILURE)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


# ---------------------------------------------------------------------------------------------
# 实例环境 / 真实 owner / 正式 runtime
# ---------------------------------------------------------------------------------------------


def _offline_model_env() -> dict[str, str]:
    return {"NYA02_MODEL_PROVIDER": MODEL_PROVIDER,
            "NYA02_MODEL_BASE_URL": "https://offline.invalid/v1",
            "NYA02_MODEL_NAME": "offline-stand-in-model",
            "NYA02_MODEL_API_KEY": "offline-not-a-key",
            "CHIYO_AUTONOMOUS_RUN_ID": _identity().run_id}


def _world_isolation_env(world_home: Path | str) -> dict[str, str]:
    """自然批所有 World 写面共用的显式路径（只构造 mapping，不写 ``os.environ``）。

    既有 bridge 读路径的顺序是：显式 env → ``CHIYO_WORLD_HOME``/``HERMES_HOME`` →
    仓库默认 ``./data/world/...``。只给 home 时，`<home>/run` 必须先存在才会命中；
    再给两个显式变量，写面就不再依赖目录探测，也不会回落到仓库默认。
    """

    home = Path(str(world_home))
    run_dir = home / "run"
    return {
        "CHIYO_WORLD_HOME": str(home),
        "HERMES_HOME": str(home),
        "WORLD_ACTION_AUTH_REGISTRY_DIR": str(run_dir),
        "WORLD_ACTION_LIFECYCLE_PATH": str(run_dir / "action_lifecycle.jsonl"),
        # 另外三个 bridge 存储同样有"显式 env → home/run → 仓库默认"的回退链。
        # World child 由 phase 子进程的 os.environ 继承构造，所以这里一并钉死，
        # 父 shell 里若有指向仓库默认的同名变量也不会漏进本实例。
        "WORLD_ACTIVITY_STATE": str(run_dir / "activity_state.json"),
        "WORLD_EXPERIENCE_SPOOL": str(run_dir / "experience_candidates.jsonl"),
        "WORLD_SELF_MAINTENANCE_STATE": str(run_dir / "self_maintenance_state.json"),
    }


def _runtime_env(root: Path | str, *, offline: bool = False) -> dict[str, str]:
    from components.phone import life as phone_life

    phone = phone_life.load_phone_instance(root)
    nested = phone_life.read_nested_life(root)
    if nested is None:
        raise Refused(REASON_NOT_PREPARED, "no nested life instance")
    binding = phone_life.build_binding(phone, nested, base_env=dict(os.environ))
    env = dict(binding["environment"])
    if str(env.get("CHIYO_AUTONOMOUS_RUN_ID") or "") != _identity().run_id:
        raise Refused(REASON_NOT_PREPARED, "instance run identity differs from the plan")
    # 把实例自己的实名绑定带给研究/项目/端口工厂（与既有 v05 装配同一组别名）；
    # 这些名字只是同一份 owner 的另一种拼写，不代表新的权限。
    import one_day_life as odl

    data = odl.read_instance(Path(root) / "one-day")
    aliases = {
        "NYAIRO_LIFE_SUBJECT": str(data.get("subject_id") or env.get("NYA02_SUBJECT") or ""),
        "NYAIRO_LIFE_WORKSPACE_ID": str(data.get("workspace_id")
                                        or env.get("CHIYO_LIFE_SUPPLY_WORKSPACE_ID") or ""),
        "NYAIRO_ARTIFACT_GRANT": str(data.get("artifact_grant_id")
                                     or env.get("NYA02_ARTIFACT_GRANT") or ""),
        "NYAIRO_LIFE_SUPPLY_SOCKET": str(env.get("LIFE_SUPPLY_SOCKET")
                                         or env.get("CHIYO_NATIVE_LIFE_SUPPLY_SOCKET") or ""),
    }
    if data.get("private_items_artifact_id"):
        aliases["NYAIRO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID"] = str(
            data["private_items_artifact_id"])
        aliases.setdefault("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                           str(data["private_items_artifact_id"]))
    if data.get("reading_artifact_id"):
        aliases["NYAIRO_LIFE_READING_ARTIFACT_ID"] = str(data["reading_artifact_id"])
    env.update({name: value for name, value in aliases.items() if value})
    # runner 自己的检查窗口：prepare 前的固定实例参数，不是每轮改 trigger。
    env["CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES"] = str(CHECK_INTERVAL_MINUTES)
    if offline:
        for key in [name for name in env if name.startswith("NYA02_MODEL_")]:
            env.pop(key, None)
        env.update(_offline_model_env())
        # 离线夹具：让每次 tick 都落在自己的检查窗口里（不是生产默认）。
        env["CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES"] = "0"
    # 自然批的 World gate 只写进**返回给 runtime/assembly/World adapter 的环境映射**：
    # e=canonical，d=显式 off；a/b/c 返回 None，这一行不执行，原行为不变。
    # 不写 os.environ、不写实例默认、不写 .env。
    gate = _world_intent_gate(_identity())
    if gate is not None:
        env["WORLD_BODY_INTENT_MUTATION_GATE"] = gate
    if _identity().key in (E_PROFILE.key, F_PROFILE.key):
        # e/f：实例的 world_home 必须存在、可读、绝对且解析后仍在根内；缺/坏一律 Refused。
        # 绝不从父 shell 继承别的 home/registry/lifecycle，也不猜一个路径自动创建。
        # f 只做 gate off 下的写面隔离，不启动 World 服务。
        env.update(_world_isolation_env(_require_instance_world_home(root)))
    elif _is_natural(_identity()):
        # d 保持 b6 交付时的宽松投影；a/b/c 不进入这里，原行为不变。
        home = _instance_world_home(root)
        if home:
            env.update(_world_isolation_env(home))
    return env


def _model_config_from_env(env: Mapping[str, str]) -> Optional[dict[str, Any]]:
    import autonomous_life_assembly as assembly

    return assembly._model_config(env)


def _explicit_model_base_url(env: Mapping[str, str]) -> str:
    """真实入口唯一认可的地址来源：运行期显式 ``NYA02_MODEL_BASE_URL``。

    不从模块常量、profile 默认值或父进程之外的来源猜；空值就是“未配置”。
    """

    return str(env.get("NYA02_MODEL_BASE_URL") or "").strip()


def _verify_model_config(env: Mapping[str, str]) -> dict[str, Any]:
    """真实 run 发送前的参数核对；只回结构事实，绝不回 key。"""

    configured_base_url = _explicit_model_base_url(env)
    if not configured_base_url:
        # 缺地址在任何配置/请求/写入之前拒绝；不退回仓库默认或 profile 猜测。
        return {"ok": False, "reason_code": REASON_NO_MODEL_CONFIG}
    config = _model_config_from_env(env)
    if not config:
        return {"ok": False, "reason_code": REASON_NO_MODEL_CONFIG}
    actual = {"provider": str(config.get("provider") or "custom"),
              "base_url": str(config.get("base_url") or "").rstrip("/"),
              "model": str(config.get("model") or "")}
    wanted = {"provider": MODEL_PROVIDER, "base_url": configured_base_url.rstrip("/"),
              "model": MODEL_NAME}
    if actual != wanted:
        return {"ok": False, "reason_code": REASON_MODEL_PARAM_MISMATCH,
                "expected": wanted, "actual": actual}
    return {"ok": True, "provider": actual["provider"], "base_url": actual["base_url"],
            "model": actual["model"], "api_key_present": bool(config.get("api_key"))}


def _start_supply(root: Path | str, *, tag: str):
    import history_choice_experiment as base

    return base.start_supply_owner(root, tag=tag)


STOP_SIGNAL_WAIT_SECONDS = 10.0


def _socket_live(path: Any) -> bool:
    """只读探测：这个路径上是否还有活着的 listener。"""

    if not path:
        return False
    try:
        from lifesupply.service.life_supply import socket_is_live

        return bool(socket_is_live(Path(str(path))))
    except Exception:  # noqa: BLE001 - 探测失败不等于有活进程
        return False


def _stop_supply(child: Any) -> dict[str, Any]:
    """只停我们自己起的那个 owner 子进程，不碰/不删 socket。

    先发 SIGINT：既有 ``phone.py owner-supply`` 入口捕获 ``KeyboardInterrupt`` 后会关掉
    listener 与服务再退出；它按原设计保留 socket 文件作为证据，所以这里只核对**没有活
    listener**，不替它 unlink。SIGINT 不走就退回 ``ChildService.stop`` 的 SIGTERM 路径。
    """

    if child is None:
        return {"pid": None, "exit_code": None, "socket_path": None,
                "socket_live_after": False}
    import signal

    process = getattr(child, "process", None)
    if process is not None and process.poll() is None:
        try:
            process.send_signal(signal.SIGINT)
        except OSError:
            pass
        deadline = time.monotonic() + STOP_SIGNAL_WAIT_SECONDS
        while time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.05)
    report = child.stop() or {}
    socket_path = getattr(child, "socket_path", None)
    return {"pid": report.get("pid"), "exit_code": report.get("exit_code"),
            "socket_path": str(socket_path) if socket_path else None,
            "socket_exists_after": bool(socket_path and Path(socket_path).exists()),
            "socket_live_after": _socket_live(socket_path),
            "graceful_stop_line": _socket_stop_tokens(getattr(child, "log_path", None))}


_TOKEN_RE = re.compile(r"graceful stop on signal (\d+).*socket_handling=([A-Z_]+).*"
                       r"drained=(True|False).*inflight=(\d+)")


def _socket_stop_tokens(log_path: Any) -> dict[str, Any]:
    """从 owner 自己的日志尾里只取出结构化收尾字段；不保留正文行。"""

    if not log_path:
        return {"seen": False}
    try:
        with open(log_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 65536))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return {"seen": False}
    matches = _TOKEN_RE.findall(tail)
    if not matches:
        return {"seen": False}
    signal_no, handling, drained, inflight = matches[-1]
    return {"seen": True, "signal": int(signal_no), "socket_handling": handling,
            "drained": drained == "True", "inflight": int(inflight)}


def _start_world(root: Path | str, *, tag: str) -> Any:
    """起本根自己的标准 World owner 子进程（只起这一个，不碰任何别的进程）。

    复用 ``components.phone.demo.owner_children`` 的 world child：同一命令、同一日志、
    同一环境。gate 显式传**本批自己的值**：e=``canonical``，d=``off``；父 shell 里可能有的
    ``canonical`` 不会因此漏进 d。旧 generation 的 socket 只由 ``demo.retire_socket``
    改名保留，不 unlink。返回的 child 由调用方用 ``_stop_world`` 停；起不来时这里先停掉再抛。
    """

    from components.phone import demo, service as phone_service

    root_path = Path(str(root))
    instance = dict(phone_service.load_instance(root_path))
    generation = "g1w-%s-%d-%d" % (str(tag)[:16], int(time.time()), os.getpid())
    world = demo.owner_children(
        root_path, generation=generation, writer_instance="g1:%d" % os.getpid(),
        world_intent_gate=(_world_intent_gate(_identity()) or "off"))["world"]
    world_home = Path(str(instance["world_home"]))
    demo.retire_socket(world_home / "run" / "gateway.sock", generation)
    world.start()
    try:
        gateway = Path(str(instance.get("world_body_socket")
                           or world_home / "run" / "gateway.sock"))
        if not world.wait_ready(gateway, timeout=demo.CHILD_READY_TIMEOUT,
                                probe="gateway"):
            raise Refused(REASON_WORLD_NOT_READY, "the world owner did not become ready")
    except Exception:
        try:
            world.stop()
        except Exception:  # noqa: BLE001 - the original failure is what must surface
            pass
        raise
    return world


def _stop_world(child: Any) -> dict[str, Any]:
    """标准 ChildService 停止：SIGTERM→有界等待→SIGKILL；不删 socket，只核活 listener。"""

    if child is None:
        return {"pid": None, "exit_code": None, "socket_path": None,
                "socket_live_after": False}
    report = child.stop() or {}
    socket_path = getattr(child, "socket_path", None)
    return {"pid": report.get("pid"), "exit_code": report.get("exit_code"),
            "socket_path": str(socket_path) if socket_path else None,
            "socket_exists_after": bool(socket_path and Path(socket_path).exists()),
            "socket_live_after": _socket_live(socket_path),
            "graceful_stop_line": _socket_stop_tokens(getattr(child, "log_path", None))}


def _build_runtime(root: Path | str, *, state_root: Path | str, budget: Any, latch: SendLatch,
                   env: Mapping[str, str], offline: bool = False,
                   plan: Optional[Sequence[str]] = None,
                   model_override: Optional[Mapping[str, Any]] = None,
                   trace: Optional[CandidateTrace] = None,
                   world_feed: bool = False) -> dict[str, Any]:
    """在真实 owner 上装出正式 runtime；只有最底层 transport 可能是明确替身。"""

    import autonomous_life_assembly as assembly
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    from agency_cognition_adapter import ProductionAgencyCognitionAdapter
    from agency_model_client import AgencyModelClient
    from life_artifact_port import LifeArtifactPort
    from life_request_budget import BudgetedCognitionAdapter, BudgetedModelClient
    from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter
    from model_continuation_writer import ModelContinuationWriter
    from profile_model_transport import OpenAICompatibleTransport

    supply = assembly.resolve_supply_configuration(env)
    if supply["missing"]:
        raise Refused(REASON_SUPPLY_UNAVAILABLE, ",".join(supply["missing"]))
    config = dict(model_override or {}) or _model_config_from_env(env)
    if not config:
        raise Refused(REASON_NO_MODEL_CONFIG, "profile resolves no usable provider")

    admission = assembly.pause_request_admission(state_root)
    if offline:
        decision_transport = _ScriptedDecisionTransport(list(plan or []))
        content_transport = _ScriptedContentTransport()
    else:
        decision_transport = OpenAICompatibleTransport(
            base_url=config["base_url"], api_key=config["api_key"], model=config["model"],
            provider=str(config.get("provider") or MODEL_PROVIDER), timeout_s=30.0,
            purpose="agency_cognition", request_json_object=True, pause_guard=admission,
            temperature=MODEL_TEMPERATURE)
        content_transport = OpenAICompatibleTransport(
            base_url=config["base_url"], api_key=config["api_key"], model=config["model"],
            provider=str(config.get("provider") or MODEL_PROVIDER), timeout_s=30.0,
            purpose="artifact_edit", request_json_object=True, pause_guard=admission,
            temperature=MODEL_TEMPERATURE)

    decision_client = AgencyModelClient(
        config=config, transport=decision_transport, purpose="agency_cognition",
        timeout_s=30.0, max_output_tokens=DECISION_OUTPUT_CAP,
        temperature=MODEL_TEMPERATURE, request_json_object=True)
    decision_adapter = ProductionAgencyCognitionAdapter(
        client=decision_client, timeout_s=30.0, max_output_tokens=DECISION_OUTPUT_CAP)
    cognition = G1CognitionGate(
        BudgetedCognitionAdapter(decision_adapter, budget, admission=admission),
        latch=latch, budget=budget, trace=trace)

    import history_choice_experiment as base

    memory = base.build_memory_ports(env)
    content_client = AgencyModelClient(
        config=config, transport=content_transport, purpose="artifact_edit",
        timeout_s=30.0, max_output_tokens=CONTENT_OUTPUT_CAP,
        temperature=MODEL_TEMPERATURE, request_json_object=True)
    writer = ModelContinuationWriter(
        G1ContentGate(BudgetedModelClient(content_client, budget, admission=admission),
                      latch=latch, budget=budget),
        max_new_chars=300, timeout_s=30.0, max_output_tokens=CONTENT_OUTPUT_CAP,
        experience_provider=memory["experience_provider"])

    port = LifeArtifactPort(supply["supply_socket"], supply["subject_id"])
    artifact_adapter = SupplyManagedArtifactActionAdapter(
        port, grant_id=supply["artifact_grant_id"],
        workspace_id=env.get("CHIYO_LIFE_SUPPLY_WORKSPACE_ID"))
    # d 的自然观测要 World 的两半同时就位：感知 provider 和动作 adapter。cycle 的
    # ``_world_step_enabled`` 要求二者都非 None，缺一半时 START 只建 Activity、不构造
    # 首步/Action。这里在构造 runtime **之前**用 resident 的标准解析口取一次真实 World
    # 状态：READY 就用同一个标准入口（``resolve_world_action_adapter``，与 resident 完全
    # 同一份实现）构造动作 adapter，并把同一个 provider 交给 assembly，避免同一 seam
    # 被解析两次。a/b/c（``world_feed=False``）不进入这里，仍是原来的 None/原行为。
    world_snapshot: Optional[dict[str, Any]] = None
    world_action_adapter = None
    if world_feed:
        world_snapshot = assembly.resolve_world_provider(env)
        if world_snapshot.get("status") == "READY":
            from autonomous_life_resident import resolve_world_action_adapter

            world_action_adapter = resolve_world_action_adapter(env, world_snapshot)
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
        proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        state_root, kill_switches=switches, cognition_adapter=cognition,
        install_root=state_root, artifact_action_adapter=artifact_adapter,
        world_action_adapter=world_action_adapter, experience_consumer=memory["consumer"])
    continuity = base.build_continuity_source(env, runtime)
    assembled = assembly.assemble_autonomous_life(
        runtime, environment=env, state_root=state_root,
        experience_provider=memory["experience_provider"], writer=writer,
        requests=budget, continuity_source=continuity, appraisal_source=None,
        world_provider=(world_snapshot.get("provider") if world_snapshot else None))
    if assembled.get("state") != assembly.READY:
        raise Refused(REASON_ASSEMBLY_NOT_READY,
                      "%s: %s" % (assembled.get("reason"), assembled.get("detail")))
    try:
        runtime.coordinator.recover_all_and_reconcile()
    except Exception:  # noqa: BLE001 - a failed recovery is reported by the round, not hidden
        pass
    try:
        assembled["runner"].reconcile_pending()
    except Exception:  # noqa: BLE001
        pass
    if trace is not None:
        # 只读引用 runtime 已经建好的 AG-0 materializer；不替换、不包装、不重算候选集。
        trace.attach(runtime)
    if world_feed:
        # 兜底：正常情况下上面的单次解析已经把同一个 READY provider 交给 assembly，cycle
        # 构造时就拿到了它；只有那次解析不是 READY、而 assembly 自己的解析又成功时，
        # cycle 才会是构造时的 ``None``。这里只把 assembled 里**真实读回 READY** 的同一个
        # 对象接回 cycle，不新建 provider、不改 components、不碰 a/b/c 的既有路径。
        world_record = dict(assembled.get("world") or {})
        provider = assembled.get("world_provider")
        cycle = assembled.get("cycle")
        if (world_record.get("status") == "READY" and provider is not None
                and cycle is not None
                and getattr(cycle, "world_opportunity_provider", None) is None):
            cycle.world_opportunity_provider = provider
    return {"runtime": runtime, "assembled": assembled, "cycle": assembled["cycle"],
            "port": port, "adapter": artifact_adapter, "env": dict(env),
            "state_root": str(state_root), "memory": memory, "writer": writer,
            "cognition": cognition, "decision_transport": decision_transport,
            "content_transport": content_transport, "trace": trace}


def _release_runtime(bundle: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if bundle is None:
        return {"closed": False}
    runtime = bundle.get("runtime")
    report = {"closed": False}
    try:
        runtime.close()
        report["closed"] = not bool(getattr(getattr(runtime, "lease", None), "held", True))
    except Exception as exc:  # noqa: BLE001 - printed by the phase, never hidden
        report["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    return report


# ---------------------------------------------------------------------------------------------
# 离线替身（只在 offline-check 路径里出现，并且显式标注）
# ---------------------------------------------------------------------------------------------


class _ScriptedTransportBase:
    provider = "offline-stub"
    model = "offline-stand-in-model"
    model_id = "stub:g1-real-adoption"
    model_version = "g1-real-adoption-offline-v1"
    #: 与真实 transport 同一约定：能分开 send phase 与等响应（这里立刻返回）。
    send_split_capable = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def _result(self, text: str, purpose: str):
        from agency_model_client import ModelCallResult

        return ModelCallResult(purpose=purpose, provider=self.provider, model=self.model,
                               text=text, latency_ms=1, status=200, usage_source="none")


class _ScriptedDecisionTransport(_ScriptedTransportBase):
    """离线决策替身：按计划从本轮真实候选里挑一个，绝不自己编 candidate id。"""

    def __init__(self, plan: Sequence[str]) -> None:
        super().__init__()
        self.plan = [str(step) for step in plan]

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or "")})
        try:
            payload = json.loads(user_prompt or "{}")
        except ValueError:
            payload = {}
        allowed = [str(code) for code in (payload.get("allowed_reason_codes") or [])]
        candidates = [row for row in (payload.get("candidates") or [])
                      if isinstance(row, Mapping)]
        step = self.plan.pop(0) if self.plan else "NO_ACTION"
        answer: dict[str, Any] = {"decision": "NO_ACTION", "selected_candidate_id": None,
                                  "reason_codes": allowed[:1] or ["NO_ACTION_CHOSEN"],
                                  "deferred_candidate_ids": []}
        if step == "START":
            pool = [row for row in candidates
                    if "START" in [str(x) for x in (row.get("legal_decisions") or [])]]
            pick = next((row for row in pool if str(row.get("source_ref") or "")), None) \
                or (pool[0] if pool else None)
            if pick is not None:
                answer.update({"decision": "START",
                               "selected_candidate_id": str(pick["candidate_id"])})
        elif step == "CONTINUE":
            wanted = str(payload.get("__g1_target__") or "")
            pool = [row for row in candidates
                    if str(row.get("candidate_kind") or "") == "CONTINUE_CURRENT"
                    and str(row.get("source_kind") or "") == "CURRENT_ACTIVITY"
                    and "CONTINUE" in [str(x) for x in (row.get("legal_decisions") or [])]]
            if wanted:
                narrowed = [row for row in pool
                            if any(_bare(ref) == wanted
                                   for ref in (row.get("target_refs") or []))]
                pool = narrowed or pool
            if pool:
                answer.update({"decision": "CONTINUE",
                               "selected_candidate_id": str(pool[0]["candidate_id"])})
        return self._result(json.dumps(answer, ensure_ascii=False), "offline-decision")


class _ScriptedContentTransport(_ScriptedTransportBase):
    """离线内容替身：回一段固定的中性追加文字。"""

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or "")})
        return self._result(json.dumps({"kind": "append_paragraph",
                                        "text": "末尾补了一句中性的练习说明。"},
                                       ensure_ascii=False), "offline-content")


@contextlib.contextmanager
def _http_forbidden() -> Any:
    """离线检查期间任何真实 HTTP connect 都直接失败。"""

    originals = {
        "http": getattr(http.client.HTTPConnection, "connect", None),
        "https": getattr(http.client.HTTPSConnection, "connect", None),
        "socket": getattr(socket.socket, "connect", None),
    }

    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("OFFLINE_HTTP_FORBIDDEN")

    original_socket_connect = originals["socket"]

    def _blocked_inet(sock: Any, *args: Any, **kwargs: Any) -> Any:
        # owner 之间走 AF_UNIX；只有真正的网络连接才在这里被硬拒。
        if getattr(sock, "family", None) == socket.AF_UNIX:
            if original_socket_connect is None:  # pragma: no cover - 异常环境
                raise AssertionError("OFFLINE_HTTP_FORBIDDEN")
            return original_socket_connect(sock, *args, **kwargs)
        raise AssertionError("OFFLINE_HTTP_FORBIDDEN")

    http.client.HTTPConnection.connect = _blocked
    http.client.HTTPSConnection.connect = _blocked
    socket.socket.connect = _blocked_inet
    try:
        yield
    finally:
        if originals["http"] is not None:
            http.client.HTTPConnection.connect = originals["http"]
        if originals["https"] is not None:
            http.client.HTTPSConnection.connect = originals["https"]
        if originals["socket"] is not None:
            socket.socket.connect = originals["socket"]


# ---------------------------------------------------------------------------------------------
# 安全视图（stdout / audit 只出现在这里定义的字段）
# ---------------------------------------------------------------------------------------------

_SAFE_WAKE_FIELDS = (
    "kind", "status", "outcome", "decision", "llm_calls", "action_id", "action_status",
    "activity_ref", "owner_version_created", "owner_version_id", "selected_candidate_ref",
    "decision_route", "reason_codes", "decision_id",
)


def _safe_wake(wake: Optional[Mapping[str, Any]], *, receipt: Optional[Mapping[str, Any]] = None,
               ) -> dict[str, Any]:
    row = dict(wake or {})
    extra = row.get("extra") if isinstance(row.get("extra"), Mapping) else {}
    view: dict[str, Any] = {}
    for name in _SAFE_WAKE_FIELDS:
        if name not in row:
            continue
        value = row.get(name)
        if name == "decision_id":
            value = _trace_id(value, frozenset({"dec"}))
            if not value:
                continue
        view[name] = value
    for name in ("owner_version_created", "owner_version_id", "selected_candidate_ref",
                 "decision_route", "reason_codes"):
        if name in extra and name not in view:
            view[name] = extra.get(name)
    view["llm_calls"] = int(view.get("llm_calls") or 0)
    if isinstance(view.get("reason_codes"), (list, tuple)):
        view["reason_codes"] = [str(code) for code in view["reason_codes"]]
    if receipt:
        view["step_artifact"] = str(receipt.get("artifact_id") or "") or None
        view["step_activity"] = str(receipt.get("activity_ref") or "") or None
        view["step_outcome"] = str(receipt.get("outcome") or "") or None
    return view


_SAFE_PHASE_FIELDS = (
    "phase", "pid", "root", "batch", "run_id", "offline", "stop_reason", "refusal_detail",
    "artifact_a", "activity_ref", "ledger", "window", "coverage", "injections", "rounds",
    "paused_round", "paused_round_zero_requests", "reconcile", "model",
    "pause", "state_at_pause", "pause_at_start", "state_at_start", "pause_after_resume",
    "head_after_write", "versions_after_write", "head_at_start", "versions_at_start",
    "head_at_end", "versions_at_end", "unknown_versions_after", "cas_owner", "first_receipt",
    "close_runtime", "close_supply", "close_world", "final_state", "states",
    "write_confirmed", "owner_version_created", "owner_version_id", "receipt_artifact",
    "receipt_activity", "p1_confirmed", "cas_evidence", "candidate_trace",
    # f 的精确锚判据只需要这些既有安全 id/布尔；旧批报告里没有这些键，形状不变。
    "first_action_id", "world_gate",
    # 只有 d 的自然 phase 才有；a/b/c 的报告里没有这些键，输出形状不变。
    "natural_phase", "operator_material_provisioned", "control_created",
    "injector_constructed", "round_count", "observation", "world", "isolation",
)


def _safe_phase_view(report: Mapping[str, Any]) -> dict[str, Any]:
    return {name: report.get(name) for name in _SAFE_PHASE_FIELDS if name in report}


def _dump_phase(report: Mapping[str, Any], root: Path | str) -> None:
    path = _experiment_dir(root) / (PHASE_FILENAME % report.get("phase"))
    _write_json(path, report)
    sys.stdout.write(PHASE_PREFIX + json.dumps(_safe_phase_view(report), ensure_ascii=False,
                                               default=str) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------------------------
# 候选观测（实验专用，默认关闭；只读、失败不影响业务）
# ---------------------------------------------------------------------------------------------
#
# 下一次获得独立批准的真实批次如果显式打开 ``--capture-candidates``，这里按**三个阶段**
# 记录当时能拿到的东西，每个阶段都标清 scope：
#
#   * ``CANDIDATE_SET_MATERIALIZED``：AG-0 ``build_candidate_set`` 当次真正构造的 CandidateSet
#     的 ``candidate_refs`` 与顺序。这是各源 materialize（每源上限 25）经去重、按 candidate_id
#     字典序、再受全局上限 100 之后的集合——**不是**原始 discover 全量。``truncated_sources``
#     只携带计数，用来标明哪些源在被观测之前已经截过。
#   * ``REQUEST_LEGAL_SET``：AG-1 硬约束与容量评估之后真正交给 adapter 的 ``request.candidates``。
#   * ``PROMPT_FINAL``：``build_prompt`` **同一次**返回的结构化 payload 里的 candidates
#     （已经过 ``MAX_CANDIDATES_IN_PROMPT=16`` 与结构性字符裁剪）。绝不重新构造 prompt，
#     也不解析 prompt 文本；拿不到结构化 payload 就写 ``PROMPT_BUILT_NOT_SENT`` 或
#     ``UNKNOWN``。
#
# 三段的差集只用来记 ``STAGE_DIFFERENCE``，不推断裁剪原因，也不把缺省当权限拒绝。
# 默认关闭时不建文件、不做任何额外状态读取。

def _trace_error(exc: BaseException) -> str:
    """只保留有界异常类型名；消息、参数与未知类名都不落盘。"""

    name = type(exc).__name__
    return name if name in _TRACE_ERROR_TYPES else "ERROR"


def _trace_int(value: Any) -> int:
    """只接受真正的整数（bool 不算）；其他一律 0，避免类型/解析异常。"""

    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _trace_enum(value: Any, allowed: frozenset[str]) -> Optional[str]:
    """固定枚举：只有表内值才算观测到，其他返回 None（由调用方计 UNKNOWN）。"""

    text = str(value or "").strip()
    return text if text in allowed else None


def _trace_token(value: Any, allowed: frozenset[str]) -> Optional[str]:
    """同 ``_trace_enum``，但用于失败分类/原因码这类已知 token。"""

    text = str(value or "").strip()
    return text if text in allowed else None


def _trace_id(value: Any, prefixes: Optional[frozenset[str]] = None) -> Optional[str]:
    """只接受已知前缀 + 已知载荷形状的结构化 id；未知形状不落盘。

    正式链里的 Activity id 是 ``actv_<hex>``（下划线），其余引用是 ``prefix:payload``。
    两种形式都按同一张前缀表校验，绝不把任意词当合法 id。
    """

    text = str(value or "").strip()
    if not text:
        return None
    if ":" in text:
        prefix, payload = text.split(":", 1)
    else:
        prefix, sep, payload = text.partition("_")
        if not sep or prefix not in _TRACE_UNDERSCORE_ID_PREFIXES:
            return None
    if prefixes is not None and prefix not in prefixes:
        return None
    pattern = _TRACE_ID_PAYLOADS.get(prefix)
    if pattern is None:
        return None
    return text if re.match(pattern, payload) else None


def _trace_candidate(row: Mapping[str, Any], *, prompt: bool = False) -> tuple[dict[str, Any], int]:
    """候选的观测副本：固定白名单字段；未知枚举/id 形状不落盘并计数。"""

    item: dict[str, Any] = {}
    unknown = 0
    candidate_id = _trace_id(row.get("candidate_id"), _TRACE_CANDIDATE_ID_PREFIXES)
    if candidate_id:
        item["candidate_id"] = candidate_id
    for key, allowed in (("source_kind", _TRACE_SOURCE_KINDS),
                         ("candidate_kind", _TRACE_CANDIDATE_KINDS)):
        raw = row.get(key)
        value = _trace_enum(raw, allowed)
        if value:
            item[key] = value
        elif raw not in (None, ""):
            item[key] = "UNKNOWN"
            unknown += 1
    legal = []
    for raw in list(row.get("legal_decisions") or [])[:8]:
        code = _trace_enum(raw, _TRACE_DECISIONS)
        if code:
            legal.append(code)
        else:
            unknown += 1
    if legal:
        item["legal_decisions"] = legal
    if isinstance(row.get("executable_now"), bool):
        item["executable_now"] = row["executable_now"]
    targets = row.get("target_refs")
    if isinstance(targets, (list, tuple)):
        item["target_ref_count"] = len(targets)
    if prompt:
        # 描述由 AG-1 产生，可能是它自己的一行话；这里只记「有/没有」，不落内容。
        item["has_description"] = bool(str(row.get("description") or "").strip())
    return item, unknown


def _trace_candidate_list(rows: Sequence[Any], *, prompt: bool = False) -> dict[str, Any]:
    source = list(rows or [])
    kept: list[dict[str, Any]] = []
    unknown = 0
    for row in source[:CANDIDATE_TRACE_MAX_CANDIDATES]:
        if not isinstance(row, Mapping):
            unknown += 1
            continue
        item, row_unknown = _trace_candidate(row, prompt=prompt)
        if "candidate_id" not in item:
            unknown += 1
        unknown += row_unknown
        kept.append(item)
    return {"candidates": kept, "count": len(kept), "total": len(source),
            "truncated": len(source) > CANDIDATE_TRACE_MAX_CANDIDATES, "unknown": unknown}


#: wake 的安全观测形状：每个字段都有自己的固定枚举或 id 形状，不直接复用 ``_safe_wake``。
_TRACE_WAKE_ENUM_FIELDS = (
    ("kind", _TRACE_WAKE_KINDS),
    ("status", _TRACE_WAKE_STATUSES),
    ("outcome", _TRACE_OUTCOMES),
    ("decision", _TRACE_DECISIONS),
    ("action_status", _TRACE_ACTION_STATUSES),
    ("decision_route", _TRACE_DECISION_ROUTES),
)
_TRACE_WAKE_ID_FIELDS = (
    ("activity_ref", _TRACE_ACTIVITY_ID_PREFIXES),
    ("action_id", frozenset({"actn"})),
    ("owner_version_id", frozenset({"artifactversion"})),
    ("selected_candidate_ref", _TRACE_CANDIDATE_ID_PREFIXES),
    ("decision_id", frozenset({"dec"})),
)


def _trace_wake(wake: Optional[Mapping[str, Any]]) -> tuple[dict[str, Any], int]:
    """一轮 wake 的白名单视图；未知枚举/id 记 ``UNKNOWN`` 并计数。"""

    row = dict(wake or {})
    extra = row.get("extra") if isinstance(row.get("extra"), Mapping) else {}
    merged = {**extra, **row}
    view: dict[str, Any] = {}
    unknown = 0
    for name, allowed in _TRACE_WAKE_ENUM_FIELDS:
        raw = merged.get(name)
        if raw in (None, ""):
            continue
        token = _trace_enum(raw, allowed)
        if token:
            view[name] = token
        else:
            view[name] = "UNKNOWN"
            unknown += 1
    for name, prefixes in _TRACE_WAKE_ID_FIELDS:
        raw = merged.get(name)
        if raw in (None, ""):
            continue
        token = _trace_id(raw, prefixes)
        if token:
            view[name] = token
        else:
            view[name] = "UNKNOWN"
            unknown += 1
    if isinstance(merged.get("llm_calls"), int) and not isinstance(merged.get("llm_calls"), bool):
        view["llm_calls"] = max(0, int(merged["llm_calls"]))
    else:
        view["llm_calls"] = 0
    codes = merged.get("reason_codes")
    if isinstance(codes, (list, tuple)):
        kept = []
        for code in codes:
            token = _trace_enum(code, _TRACE_REASON_CODES)
            if token:
                kept.append(token)
            else:
                unknown += 1
        if kept:
            view["reason_codes"] = kept
    # 只有「真实模型枚举且这一轮确实发生过请求」才叫模型选择；流程 NO_ACTION /
    # QUIET / NOT_DUE 一律不是，避免把零请求的安静读成模型决定。
    view["model_decision"] = bool(view.get("decision") and int(view.get("llm_calls") or 0) > 0)
    return view, unknown


def _trace_activity(summary: Any) -> dict[str, Any]:
    """同轮 ``current_activity_summary``（AG-1 自己给的快照），只取三个白名单字段。"""

    if not isinstance(summary, Mapping):
        return {"status": "UNKNOWN", "why": "no current_activity_summary on this request"}
    out: dict[str, Any] = {}
    unknown = 0
    activity_ref = _trace_id(summary.get("activity_ref"), _TRACE_ACTIVITY_ID_PREFIXES)
    if activity_ref:
        out["activity_ref"] = activity_ref
    elif summary.get("activity_ref") not in (None, ""):
        out["activity_ref"] = "UNKNOWN"
        unknown += 1
    for key, allowed in (("status", _TRACE_ACTIVITY_STATUSES),
                         ("interruptibility", _TRACE_INTERRUPTIBILITY)):
        raw = summary.get(key)
        if raw in (None, ""):
            continue
        value = _trace_enum(raw, allowed)
        if value:
            out[key] = value
        else:
            out[key] = "UNKNOWN"
            unknown += 1
    if unknown:
        out["unknown_fields"] = unknown
    return out


def _trace_pause_condition(context: Any) -> dict[str, Any]:
    """situation_context 里的 PAUSE 条件块：只取 id/枚举/布尔，绝不碰自由文本键。"""

    block = (context or {}).get("pause_condition") if isinstance(context, Mapping) else None
    if not isinstance(block, Mapping):
        return {"status": "UNKNOWN", "why": "no pause_condition block on this request"}
    out: dict[str, Any] = {}
    unknown = 0
    if isinstance(block.get("available"), bool):
        out["available"] = block["available"]
    for key, prefixes in (("condition_ref", frozenset({"waitbasis"})),
                          ("activity_ref", _TRACE_ACTIVITY_ID_PREFIXES),
                          ("waiting_on_ref", frozenset({"wait_dep"}))):
        raw = block.get(key)
        if raw in (None, ""):
            continue
        value = _trace_id(raw, prefixes)
        if value:
            out[key] = value
        else:
            out[key] = "UNKNOWN"
            unknown += 1
    for key, allowed in (("source", _TRACE_PAUSE_SOURCES), ("why_not", _TRACE_PAUSE_WHY_NOT)):
        raw = block.get(key)
        if raw in (None, ""):
            continue
        value = _trace_enum(raw, allowed)
        if value:
            out[key] = value
        else:
            out[key] = "UNKNOWN"
            unknown += 1
    if unknown:
        out["unknown_fields"] = unknown
    return out


def _pause_material_booleans(prompt_shape: Any) -> dict[str, Optional[bool]]:
    """d 专属：只看**同一次** ``build_prompt`` 返回结构里的 ``pause_condition.how``。

    只做两次精确子串判断，各自独立。最终 shape 缺失、没有 ``pause_condition``、``how``
    不是字符串，一律两个 ``None``（UNKNOWN）——未知不记 ``false``。取到字符串就各自
    ``true``/``false``：有 basis 时事实句与尾段都在，无 basis 时只有事实句。任何返回都
    只证明那两条文本在**交给模型的结构**里出现过，不证明发送、送达或模型理解。
    """

    how: Any = None
    if isinstance(prompt_shape, Mapping):
        context = prompt_shape.get("situation_context")
        if isinstance(context, Mapping):
            block = context.get("pause_condition")
            if isinstance(block, Mapping):
                how = block.get("how")
    if not isinstance(how, str):
        return {"fact_sentence_present": None, "tail_present": None}
    return {"fact_sentence_present": D_PAUSE_FACT_SENTENCE in how,
            "tail_present": D_PAUSE_TAIL in how}


class CandidateTrace:
    """实验观测：分阶段、脱敏、默认关闭；任何观测失败都不得改变业务。

    ``enabled=False`` 时所有方法立即返回：不读取额外状态、不建文件。``enabled=True`` 时
    每阶段一行 JSONL，字段只来自固定白名单；写失败只记 ``error``，绝不抛出。

    ``pause_material`` 只由 d 的自然 phase 传 ``True``：a/b/c 的 trace 一个字段都不多。
    """

    def __init__(self, root: Path | str, *, phase: str, enabled: bool,
                 pause_material: bool = False) -> None:
        self.root = Path(str(root))
        self.phase = str(phase)
        self.enabled = bool(enabled)
        self.pause_material = bool(pause_material)
        self.path = _experiment_dir(self.root) / (CANDIDATE_TRACE_FILENAME % self.phase)
        self.records = 0
        self.error = ""
        self.dropped_records = 0
        self._round = 0
        self._trigger = ""
        self._round_stages: dict[str, int] = {}
        self._write_failed = False
        self._materializer: Any = None
        self._runtime: Any = None
        self._last_candidate_set_id = ""

    # -- 装配 ------------------------------------------------------------------

    def attach(self, runtime: Any) -> None:
        """只读引用 runtime 已有的 AG-0 materializer 与公开纯读口；不替换、不包装它。"""

        if not self.enabled:
            return
        try:
            self._materializer = getattr(runtime, "candidate_materializer", None)
            self._runtime = runtime
        except Exception as exc:  # noqa: BLE001
            self.error = self.error or _trace_error(exc)

    # -- 轮次 ------------------------------------------------------------------

    def begin_round(self, index: int, *, trigger: str = "") -> None:
        if not self.enabled:
            return
        self._round = int(index)
        self._trigger = str(trigger or "")
        self._round_stages = {}

    def end_round(self, wakes: Sequence[Mapping[str, Any]],
                  *, receipt: Optional[Mapping[str, Any]] = None) -> None:
        if not self.enabled:
            return
        try:
            safe_wakes = []
            unknown = 0
            for wake in list(wakes or ())[:8]:
                view, wake_unknown = _trace_wake(wake)
                unknown += wake_unknown
                safe_wakes.append(view)
            self._append(STAGE_ROUND, {
                "trigger": _trace_enum(self._trigger, _TRACE_TRIGGERS) or "UNKNOWN",
                "llm_calls": sum(int((wake or {}).get("llm_calls") or 0)
                                 for wake in list(wakes or ())),
                "model_decision_present": any(view["model_decision"] for view in safe_wakes),
                "wakes": safe_wakes,
                "unknown_fields": unknown,
                "request_present": bool(self._round_stages.get(STAGE_REQUEST)
                                        or self._round_stages.get(STAGE_PROMPT)
                                        or self._round_stages.get(STAGE_PROMPT_UNSENT)),
                "stages": dict(sorted(self._round_stages.items())),
                "receipt": bool(receipt),
            })
        except Exception as exc:  # noqa: BLE001 - observability never breaks the round
            self.error = self.error or _trace_error(exc)

    def event(self, kind: str, **fields: Any) -> None:
        if not self.enabled:
            return
        payload: dict[str, Any] = {"event": _trace_enum(kind, _TRACE_EVENT_KINDS) or "UNKNOWN"}
        for name in ("paused", "revision", "state_class", "activity_ref", "reason_code"):
            value = fields.get(name)
            if isinstance(value, bool) or isinstance(value, int):
                payload[name] = value
            elif value is not None:
                if name.endswith("_ref"):
                    token = _trace_id(value, _TRACE_ACTIVITY_ID_PREFIXES)
                elif name == "state_class":
                    token = _trace_enum(value, _TRACE_STATE_CLASSES)
                else:
                    token = _trace_enum(value, _TRACE_REASON_CODES)
                payload[name] = token or "UNKNOWN"
        self._append(STAGE_EVENT, payload)

    # -- 三个阶段 --------------------------------------------------------------

    def record_request(self, request: Any) -> None:
        """adapter 真正收到的请求（AG-1 硬约束/容量之后）；附同轮的 activity/pause 快照。"""

        if not self.enabled:
            return
        try:
            materialized = self._candidate_set_view(request)
            if materialized is not None:
                self._append(STAGE_CANDIDATE_SET, materialized)
            context = getattr(request, "situation_context", None)
            legal = []
            unknown = 0
            for raw in list(getattr(request, "legal_decision_enums", ()) or ())[:16]:
                code = _trace_enum(raw, _TRACE_DECISIONS)
                if code:
                    legal.append(code)
                else:
                    unknown += 1
            raw_candidates = getattr(request, "candidates", None)
            if isinstance(raw_candidates, (str, bytes)) or not isinstance(
                    raw_candidates, (list, tuple)):
                payload = {"status": "UNKNOWN",
                           "why": "request candidates missing or not a sequence",
                           "candidates": [], "count": 0, "total": 0,
                           "truncated": False, "unknown": 0}
            else:
                payload = _trace_candidate_list(list(raw_candidates))
            payload.update({
                "candidate_set_id": _trace_id(getattr(request, "candidate_set_id", ""),
                                              frozenset({"cset"})),
                "legal_decision_enums": legal,
                "current_activity": _trace_activity(
                    getattr(request, "current_activity_summary", None)),
                "pause_condition": _trace_pause_condition(context),
                # 发送前的请求里没有资格 id/状态；原 tick 之后的只读旁证单独记在
                # POST_TICK_SNAPSHOT，不能把这里的 UNKNOWN 读成「没有资格」。
                "resume_eligibility": {"status": "UNKNOWN",
                                       "why": "no pre-send round snapshot carries eligibility ids"},
                "scope": ("AG-1 hard-constraint and capacity filtered candidates handed "
                          "to the adapter (order preserved)"),
            })
            if unknown:
                payload["unknown_fields"] = int(payload.get("unknown_fields", 0)) + unknown
            self._round_requested = True
            self._append(STAGE_REQUEST, payload)
        except Exception as exc:  # noqa: BLE001
            self.error = self.error or _trace_error(exc)

    def record_prompt(self, prompt_shape: Any, *, send_status: str, evidence: str = "",
                      error: str = "") -> None:
        """``build_prompt`` 同一次返回的结构化 payload；绝不二次构造或解析文本。

        ``send_status`` 与「有没有拿到结构化 payload」是两件事：``SENT`` 只给有直接证据的
        情况（本观测链没有这种证据），``NOT_SENT`` 只给发送前就被明确拒绝的情况，其余
        一律 ``UNKNOWN``。旧字段 ``sent`` 保留，未知时为 ``None``。
        """

        if not self.enabled:
            return
        try:
            structured = isinstance(prompt_shape, Mapping)
            raw_rows = prompt_shape.get("candidates") if structured else None
            has_rows = (isinstance(raw_rows, (list, tuple))
                        and not isinstance(raw_rows, (str, bytes)))
            if has_rows:
                payload = _trace_candidate_list(list(raw_rows), prompt=True)
            else:
                payload = {"candidates": [], "count": 0, "total": 0,
                           "truncated": False, "unknown": 0,
                           "status": "UNKNOWN",
                           "why": ("prompt payload has no candidate sequence" if structured
                                   else "no structured prompt payload on this call")}
            if send_status not in (SEND_STATUS_SENT, SEND_STATUS_NOT_SENT,
                                   SEND_STATUS_UNKNOWN):
                send_status = SEND_STATUS_UNKNOWN
            sent: Optional[bool] = (True if send_status == SEND_STATUS_SENT
                                    else False if send_status == SEND_STATUS_NOT_SENT else None)
            payload.update({
                "send_status": send_status,
                "sent": sent,
                "send_evidence": _trace_token(
                    evidence, _TRACE_SEND_EVIDENCE) if evidence else None,
                "scope": ("final structured prompt payload from the same build_prompt call "
                          "(MAX_CANDIDATES_IN_PROMPT and structural trim already applied)"),
                "limit_candidates": 16,
            })
            token = _trace_token(error, _TRACE_ERROR_TOKENS)
            if token:
                payload["error"] = token
            elif error:
                payload["error"] = "UNKNOWN_ERROR"
            elif not structured:
                payload["error"] = "NO_STRUCTURED_PROMPT"
            else:
                payload["error"] = None
            if self.pause_material:
                # d 专属：与 send_status 同一行，只有两个布尔/None，不存文本。
                payload.update(_pause_material_booleans(prompt_shape))
            self._append(STAGE_PROMPT if send_status != SEND_STATUS_NOT_SENT
                         else STAGE_PROMPT_UNSENT, payload)
        except Exception as exc:  # noqa: BLE001
            self.error = self.error or _trace_error(exc)

    # -- 原 tick 之后的只读旁证 ------------------------------------------------

    def observe_after_tick(self, wakes: Sequence[Mapping[str, Any]] = ()) -> None:
        """原 tick 之后的一次有界纯读，记 PAUSED/条件/资格与已提交 hint。

        明确标 ``POST_TICK_READ``：不是发送前的原子快照，也不在任何 owner 上做
        maintain/reconcile/资格计算/写。只读失败记 UNKNOWN/ERROR，绝不当成 EMPTY。
        """

        if not self.enabled:
            return
        try:
            decision_ids: list[str] = []
            for wake in list(wakes or ())[:8]:
                row = dict(wake or {})
                extra = row.get("extra") if isinstance(row.get("extra"), Mapping) else {}
                token = _trace_id({**extra, **row}.get("decision_id"), frozenset({"dec"}))
                if token and token not in decision_ids:
                    decision_ids.append(token)
            paused = self._read_paused_snapshot()
            payload = {
                "snapshot_time": "POST_TICK_READ",
                "not_pre_send_atomic": True,
                "decision_ids": decision_ids,
                "submitted_hint": self._read_submitted_hint(decision_ids),
                "paused_activities": paused,
            }
            self._append(STAGE_SNAPSHOT, payload)
        except Exception as exc:  # noqa: BLE001 - observability never breaks the round
            self.error = self.error or _trace_error(exc)

    def _read_submitted_hint(self, decision_ids: Sequence[str]) -> dict[str, Any]:
        """已提交 Decision 的 ``reevaluation_hint_ref``（若有）；只读 DecisionReadService。"""

        if not decision_ids:
            return {"status": "UNKNOWN", "why": "this round carries no known decision id"}
        reader = getattr(self._runtime, "decision_read", None) if self._runtime is not None else None
        if reader is None:
            return {"status": "UNKNOWN", "why": "decision read port is not available"}
        decision_id = decision_ids[-1]
        try:
            record = reader.get_decision(decision_id)
        except Exception as exc:  # noqa: BLE001
            return {"status": "ERROR", "decision_id": decision_id,
                    "error": _trace_error(exc)}
        if not isinstance(record, Mapping):
            return {"status": "UNKNOWN", "decision_id": decision_id,
                    "why": "decision record not found"}
        raw = record.get("reevaluation_hint_ref")
        if raw in (None, ""):
            return {"status": "NONE", "decision_id": decision_id}
        hint = _trace_id(raw, frozenset({"waitbasis"}))
        if not hint:
            return {"status": "UNKNOWN", "decision_id": decision_id,
                    "why": "hint ref is not a known wait basis id"}
        return {"status": "OBSERVED", "decision_id": decision_id, "hint_ref": hint}

    def _read_paused_snapshot(self) -> dict[str, Any]:
        """公开纯读口：open Activities → PAUSED 集合 → 其条件/资格；每块独立计状态。"""

        runtime = self._runtime
        if runtime is None:
            return {"status": "UNKNOWN", "why": "runtime is not attached to this trace"}
        activity_read = getattr(runtime, "activity_read", None)
        if activity_read is None:
            return {"status": "UNKNOWN", "why": "activity read port is not available"}
        try:
            rows = list(activity_read.list_open_activities() or [])
        except Exception as exc:  # noqa: BLE001
            return {"status": "ERROR", "error": _trace_error(exc)}
        if not isinstance(rows, list):
            return {"status": "ERROR", "error": "activity list is not a sequence"}
        paused: list[dict[str, Any]] = []
        unknown = 0
        for row in rows:
            if not isinstance(row, Mapping):
                unknown += 1
                continue
            if row.get("status") != "PAUSED":
                continue
            activity_id = _trace_id(row.get("activity_id") or row.get("activity_ref"),
                                    _TRACE_ACTIVITY_ID_PREFIXES)
            if not activity_id:
                unknown += 1
                continue
            item: dict[str, Any] = {"activity_id": activity_id, "status": "PAUSED"}
            revision = row.get("revision")
            if isinstance(revision, int) and not isinstance(revision, bool):
                item["revision"] = int(revision)
            item["conditions"] = self._read_conditions(activity_id)
            item["eligibilities"] = self._read_eligibilities(activity_id)
            paused.append(item)
            if len(paused) >= 8:
                break
        return {"status": "OBSERVED", "count": len(paused),
                "open_activity_total": len(rows), "unknown": unknown,
                "activities": paused}

    def _waiting_coordinator(self) -> Any:
        return getattr(self._runtime, "waiting_coordinator", None) \
            if self._runtime is not None else None

    def _read_conditions(self, activity_id: str) -> dict[str, Any]:
        coordinator = self._waiting_coordinator()
        if coordinator is None:
            return {"status": "UNKNOWN", "why": "waiting coordinator is not available"}
        try:
            rows = list(coordinator.list_conditions(activity_id=activity_id) or [])
        except Exception as exc:  # noqa: BLE001
            return {"status": "ERROR", "error": _trace_error(exc)}
        # 最新的绑定优先；只观测副本，不改变源集合/顺序语义。
        rows.sort(key=lambda row: (
            _trace_int(row.get("activity_revision_at_bind")) if isinstance(row, Mapping) else 0,
            str(row.get("condition_id") or "") if isinstance(row, Mapping) else ""),
            reverse=True)
        kept = []
        unknown = 0
        for row in rows[:8]:
            if not isinstance(row, Mapping):
                unknown += 1
                continue
            condition_id = _trace_id(row.get("condition_id"), frozenset({"rcond"}))
            if not condition_id:
                unknown += 1
                continue
            item: dict[str, Any] = {"condition_id": condition_id}
            status = _trace_enum(row.get("status"), _TRACE_CONDITION_STATUSES)
            item["status"] = status or "UNKNOWN"
            if not status:
                unknown += 1
            kind = _trace_enum(row.get("condition_kind"), _TRACE_CONDITION_KINDS)
            if kind:
                item["condition_kind"] = kind
            elif row.get("condition_kind") not in (None, ""):
                item["condition_kind"] = "UNKNOWN"
                unknown += 1
            waiting_on = _trace_id(row.get("waiting_on_ref"), frozenset({"wait_dep"}))
            if waiting_on:
                item["waiting_on_ref"] = waiting_on
            revision = row.get("activity_revision_at_bind")
            if isinstance(revision, int) and not isinstance(revision, bool):
                item["activity_revision_at_bind"] = int(revision)
            kept.append(item)
        return {"status": "OBSERVED", "count": len(kept), "total": len(rows),
                "unknown": unknown, "items": kept}

    def _read_eligibilities(self, activity_id: str) -> dict[str, Any]:
        coordinator = self._waiting_coordinator()
        if coordinator is None:
            return {"status": "UNKNOWN", "why": "waiting coordinator is not available"}
        try:
            rows = list(coordinator.list_eligibilities(activity_id=activity_id) or [])
        except Exception as exc:  # noqa: BLE001
            return {"status": "ERROR", "error": _trace_error(exc)}
        rows.sort(key=lambda row: (
            _trace_int(row.get("activity_revision")) if isinstance(row, Mapping) else 0,
            str(row.get("eligibility_id") or "") if isinstance(row, Mapping) else ""),
            reverse=True)
        kept = []
        unknown = 0
        for row in rows[:8]:
            if not isinstance(row, Mapping):
                unknown += 1
                continue
            eligibility_id = _trace_id(row.get("eligibility_id"), frozenset({"relig", "elig"}))
            if not eligibility_id:
                unknown += 1
                continue
            item: dict[str, Any] = {"eligibility_id": eligibility_id}
            status = _trace_enum(row.get("status"), _TRACE_ELIGIBILITY_STATUSES)
            item["status"] = status or "UNKNOWN"
            if not status:
                unknown += 1
            condition_ref = _trace_id(row.get("resume_condition_ref"), frozenset({"rcond"}))
            if condition_ref:
                item["resume_condition_ref"] = condition_ref
            revision = row.get("activity_revision")
            if isinstance(revision, int) and not isinstance(revision, bool):
                item["activity_revision"] = int(revision)
            kept.append(item)
        return {"status": "OBSERVED", "count": len(kept), "total": len(rows),
                "unknown": unknown, "items": kept}

    # -- 内部 ------------------------------------------------------------------

    def _candidate_set_view(self, request: Any) -> Optional[dict[str, Any]]:
        """只读 materializer 里**本请求那个** CandidateSet；拿不到就明确 UNKNOWN。"""

        candidate_set_id = str(getattr(request, "candidate_set_id", "") or "")
        if not candidate_set_id or candidate_set_id == self._last_candidate_set_id:
            return None
        self._last_candidate_set_id = candidate_set_id
        safe_id = _trace_id(candidate_set_id, frozenset({"cset"}))
        materializer = self._materializer
        if materializer is None:
            return {"candidate_set_id": safe_id, "status": "UNKNOWN",
                    "why": "materializer not attached to this trace"}
        table = getattr(materializer, "_candidate_sets_by_id", None)
        if not isinstance(table, Mapping):
            return {"candidate_set_id": safe_id, "status": "UNKNOWN",
                    "why": "materializer candidate sets are not readable"}
        entry = table.get(candidate_set_id)
        if entry is None:
            return {"candidate_set_id": safe_id, "status": "UNKNOWN",
                    "why": "candidate_set_id not found in this materializer"}
        try:
            data = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
        except Exception as exc:  # noqa: BLE001
            return {"candidate_set_id": safe_id, "status": "ERROR",
                    "why": "candidate set could not be read",
                    "error": _trace_error(exc)}
        raw_refs = data.get("candidate_refs")
        if raw_refs is None:
            return {"candidate_set_id": safe_id, "status": "UNKNOWN",
                    "why": "candidate_refs missing from this candidate set"}
        if isinstance(raw_refs, (str, bytes)) or not isinstance(raw_refs, (list, tuple)):
            return {"candidate_set_id": safe_id, "status": "UNKNOWN",
                    "why": "candidate_refs is not a sequence"}
        refs: list[str] = []
        unknown = 0
        for raw in raw_refs:
            ref = _trace_id(raw, _TRACE_CANDIDATE_ID_PREFIXES)
            if ref:
                refs.append(ref)
            else:
                unknown += 1
        truncated_sources: dict[str, dict[str, int]] = {}
        raw_sources = data.get("truncated_sources")
        for source, meta in dict(raw_sources or {}).items() if isinstance(
                raw_sources, Mapping) else ():
            source_kind = _trace_enum(source, _TRACE_SOURCE_KINDS)
            if not source_kind or not isinstance(meta, Mapping):
                continue
            truncated_sources[source_kind] = {
                key: int(value) for key, value in meta.items()
                if key in ("discovered", "emitted") and isinstance(value, int)}
        total = len(raw_refs)
        return {
            "status": "OBSERVED",
            "candidate_set_id": safe_id,
            "candidates": [{"candidate_id": ref} for ref in refs[:CANDIDATE_TRACE_MAX_CANDIDATES]],
            "count": min(len(refs), CANDIDATE_TRACE_MAX_CANDIDATES),
            "total": total,
            "truncated": total > CANDIDATE_TRACE_MAX_CANDIDATES,
            "unknown": unknown,
            "global_truncated": bool(data.get("global_truncated")),
            "truncated_sources": truncated_sources,
            "scope": ("per-source materialize capped at 25, deduplicated, ordered by "
                      "candidate_id, then global cap 100 -- not the raw discovery set"),
        }

    def _append(self, stage: str, payload: Mapping[str, Any]) -> None:
        if not self.enabled:
            return
        if self._write_failed:
            # 写失败后不再反复尝试同一条路径；业务继续，只是观测不完整。
            self.dropped_records += 1
            return
        if self.records >= CANDIDATE_TRACE_MAX_RECORDS:
            self.dropped_records += 1
            return
        row = {"schema": CANDIDATE_TRACE_SCHEMA, "phase": self.phase,
               "pid": os.getpid(), "round": self._round, "stage": stage,
               "observed_at": _iso()}
        row.update(dict(payload))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fresh = not self.path.exists()
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                        default=str) + "\n")
            if fresh:
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            self.records += 1
            self._round_stages[stage] = self._round_stages.get(stage, 0) + 1
        except Exception as exc:  # noqa: BLE001 - observation write failure never breaks the round
            self._write_failed = True
            self.error = _trace_error(exc)

    def view(self) -> dict[str, Any]:
        return {"enabled": self.enabled,
                "path": str(self.path) if self.enabled else None,
                "records": int(self.records),
                "dropped_records": int(self.dropped_records),
                "error": self.error or None}



# ---------------------------------------------------------------------------------------------
# 作品状态（只读 owner）
# ---------------------------------------------------------------------------------------------


def _works_snapshot(port: Any) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    try:
        rows = port.list_artifacts()
    except Exception:  # noqa: BLE001 - a failed read is reported by the round
        return snapshot
    for row in rows:
        artifact_id = str((row or {}).get("artifact_id") or "")
        if not artifact_id:
            continue
        try:
            view = port.read_artifact(artifact_id)
        except Exception:  # noqa: BLE001
            continue
        head = (view or {}).get("head") or {}
        snapshot[_bare(artifact_id)] = {"head": str(head.get("version_id") or ""),
                                        "versions": int(head.get("version_number") or 0),
                                        "lifecycle": str(((view or {}).get("artifact") or {})
                                                         .get("lifecycle") or "")}
    return snapshot


def _changed_works(port: Any, before: Mapping[str, Mapping[str, Any]],
                   *, excluded: Sequence[str] = ()) -> list[dict[str, Any]]:
    after = _works_snapshot(port)
    excluded_ids = {_bare(ref) for ref in excluded if _bare(ref)}
    changed: list[dict[str, Any]] = []
    for artifact_id, row in after.items():
        if artifact_id in excluded_ids:
            continue
        old = (before or {}).get(artifact_id) or {}
        if not old:
            continue  # 新出现的对象不在这里冒认；A 必须是真正收到新版本的既有/新作品
        if str(row.get("head") or "") != str(old.get("head") or "") \
                or int(row.get("versions") or 0) != int(old.get("versions") or 0):
            changed.append({"artifact_id": artifact_id, "head": row.get("head"),
                            "versions": row.get("versions")})
    return changed


def _excluded_artifact_ids(cycle: Any, data: Mapping[str, Any]) -> list[str]:
    ids = [str(data.get("private_items_artifact_id") or ""),
           str(data.get("reading_artifact_id") or "")]
    try:
        ids.extend(str(ref) for ref in (cycle._non_work_artifact_ids() or []))
    except Exception:  # noqa: BLE001 - the exclusion list is advisory, never fatal
        pass
    return [ref for ref in ids if ref]


def _resume(port: Any, artifact_id: Any) -> dict[str, Any]:
    """owner 自己回答的续作点：它读的是哪件作品、head 与版本号；读不到就如实空/0。

    ``artifact`` 是 owner 回执里自报的对象身份（``ArtifactResumePoint.artifact_id``），
    结果识别要靠它证明这次 head 读的就是 receipt 指向的那件作品；读取失败时留空，
    调用方因此只会失败关闭。
    """

    bare = _bare(artifact_id)
    if not bare:
        return {"artifact": "", "head": "", "versions": 0}
    try:
        point = port.resume_point("artifact:%s" % bare)
    except Exception:  # noqa: BLE001 - a failed read is reported as unknown
        return {"artifact": "", "head": "", "versions": 0}
    return {"artifact": _bare(getattr(point, "artifact_id", "") or ""),
            "head": str(getattr(point, "head_version_id", "") or ""),
            "versions": int(getattr(point, "version_number", 0) or 0)}


def _foreground_activity(bundle: Mapping[str, Any], activity_ref: str) -> dict[str, Any]:
    if not activity_ref:
        return {}
    try:
        found = bundle["runtime"].activity_read.get_activity(activity_ref)
    except Exception:  # noqa: BLE001
        return {}
    return dict(found) if found else {}


def _live_receipt(bundle: Mapping[str, Any], prior: Any) -> Optional[dict[str, Any]]:
    """这一轮新产生的正式 StepReceipt（没有新的就 None）。"""

    cycle = bundle["cycle"]
    current = getattr(cycle, "last_receipt", None)
    if current is None or current is prior:
        return None
    try:
        return dict(current.to_dict())
    except Exception:  # noqa: BLE001
        return None


def _action_owner_status(bundle: Mapping[str, Any], action_id: str) -> dict[str, Any]:
    if not action_id:
        return {}
    read = bundle["runtime"].action_read
    try:
        receipts = read.list_receipts(action_id) or []
    except Exception:  # noqa: BLE001
        receipts = []
    receipt = dict(receipts[-1] or {}) if receipts else {}
    details = dict(receipt.get("details") or {})
    try:
        attempts = read.list_attempts(action_id) or []
    except Exception:  # noqa: BLE001
        attempts = []
    attempt = dict(attempts[-1] or {}) if attempts else {}
    return {"receipt_claim": receipt.get("status_claim"),
            "owner_status": details.get("owner_status"),
            "transport_result": attempt.get("transport_result"),
            "error_class": attempt.get("error_class"),
            "receipt_count": len(receipts)}


# ---------------------------------------------------------------------------------------------
# 提交边界上的两次、各一次故障注入（既有 adapter 接缝，不改 Decision）
# ---------------------------------------------------------------------------------------------


class SubmissionInjector:
    """只包一层 ``adapter.submit``：目标必须是 A 的 commit 版本写入。

    * ``unknown``：在下一次 A 写入之前打开既有 ``fault_injection_drop_response_after_write``，
      效果真落 owner、回执丢失；不重发。
    * ``concurrent``：在 submission fence 落盘后、adapter 调 owner 之前，让**同一个 owner 的
      另一个真实 writer** 推一版；正式链于是带着过期 expected_head 被真 CAS 拒。

    两者各只触发一次；未被目标写入触发就保持 0。
    """

    def __init__(self, bundle: Mapping[str, Any], *, artifact_a: str, grant_id: str,
                 operation_prefix: str) -> None:
        import managed_artifact_action_adapter as maaa

        self.maaa = maaa
        self.bundle = bundle
        self.port = bundle["port"]
        self.adapter = bundle.get("adapter") or bundle["runtime"].artifact_action_adapter
        self.artifact_a = _bare(artifact_a)
        #: owner 自己的写法（带 namespace）；payload/报告里仍用不带前缀的身份比较。
        self.artifact_ref = "artifact:%s" % self.artifact_a if self.artifact_a else ""
        self.grant_id = str(grant_id or "")
        self.operation_prefix = str(operation_prefix or "g1")
        self._original = self.adapter.submit
        self.arm_unknown = False
        self.arm_concurrent = False
        self.unknown_fired = 0
        self.concurrent_fired = 0
        self.concurrent: dict[str, Any] = {}
        self.adapter.submit = self._submit

    def detach(self) -> None:
        try:
            self.adapter.submit = self._original
        except Exception:  # noqa: BLE001
            pass

    def _is_target(self, payload: Mapping[str, Any]) -> bool:
        return (str(payload.get("op") or "") == self.maaa.OP_COMMIT_VERSION
                and _bare(payload.get("artifact_id")) == self.artifact_a)

    def _submit(self, *args: Any, **kwargs: Any) -> Any:
        payload = dict(kwargs.get("payload") or {})
        if self._is_target(payload):
            if self.arm_unknown and not self.unknown_fired:
                self.unknown_fired += 1
                self.arm_unknown = False
                self.adapter.fault_injection_drop_response_after_write = True
            elif self.arm_concurrent and not self.concurrent_fired:
                self.concurrent_fired += 1
                self.arm_concurrent = False
                expected = str(payload.get("expected_head") or "")
                head_before = _works_snapshot(self.port).get(self.artifact_a, {}).get("head")
                self.concurrent = {"stage": "before_adapter_owner_call",
                                   "formal_expected_head": expected,
                                   "head_before": head_before}
                outcome = self.port.commit_version(
                    artifact_id=self.artifact_ref or self.artifact_a, expected_head=expected,
                    content="实验 operator 的并发内容（不属于她的经历，只用来撞一次 CAS）。",
                    grant_id=self.grant_id,
                    operation_key="%s:operator-concurrent:%d"
                                  % (self.operation_prefix, self.concurrent_fired))
                self.concurrent["applied"] = bool(getattr(outcome, "applied", False))
                self.concurrent["owner_status"] = str(getattr(outcome, "status", "") or "")
                self.concurrent["head_after"] = _works_snapshot(
                    self.port).get(self.artifact_a, {}).get("head")
        return self._original(*args, **kwargs)

    def view(self) -> dict[str, Any]:
        return {"unknown_fired": int(self.unknown_fired),
                "concurrent_fired": int(self.concurrent_fired),
                "concurrent": dict(self.concurrent)}


# ---------------------------------------------------------------------------------------------
# 驱动：离线用夹具时刻直接走正式 evaluate_once；真实 run 用 runner 自己的检查窗口
# ---------------------------------------------------------------------------------------------


class _FixtureClock:
    """离线夹具时钟：每一步推进 400 分钟（越过既有 6 小时 TTL），不是自然等待。"""

    STEP_MINUTES = 400

    def __init__(self) -> None:
        self.base = datetime.now().astimezone()
        self.index = 0

    def next(self) -> str:
        self.index += 1
        return (self.base + timedelta(minutes=self.STEP_MINUTES * self.index)).isoformat(
            timespec="seconds")


class ExplicitDriver:
    """离线：一次直接调用正式 ``evaluate_once``，用夹具时刻。"""

    def __init__(self, bundle: Mapping[str, Any], *, clock: _FixtureClock) -> None:
        self.bundle = bundle
        self.cycle = bundle["cycle"]
        self.clock = clock
        self.round = 0

    def next(self) -> list[dict[str, Any]]:
        self.round += 1
        prior = getattr(self.cycle, "last_receipt", None)
        out = self.cycle.evaluate_once(trigger_ref="g1:offline:%02d" % self.round,
                                       act=True, observed_at=self.clock.next()) or {}
        receipt = _live_receipt(self.bundle, prior) or dict(out.get("step_receipt") or {})
        return [_safe_wake(out, receipt=receipt)] if receipt else [_safe_wake(out)]

    def paused(self) -> dict[str, Any]:
        out = self.cycle.evaluate_once(trigger_ref="g1:offline:paused", act=True,
                                       observed_at=self.clock.next()) or {}
        return _safe_wake(out)


class RunnerDriver:
    """真实：生产 runner 的一次 tick（maintain + 原 due 门 + 原事件门）。"""

    def __init__(self, bundle: Mapping[str, Any]) -> None:
        self.bundle = bundle
        self.cycle = bundle["cycle"]
        self.assembled = bundle["assembled"]

    def next(self) -> list[dict[str, Any]]:
        import autonomous_life_assembly as assembly

        prior = getattr(self.cycle, "last_receipt", None)
        tick = assembly.runner_tick(self.assembled, act=True) or {}
        receipt = _live_receipt(self.bundle, prior)
        wakes: list[dict[str, Any]] = []
        due = tick.get("due")
        if isinstance(due, Mapping):
            wakes.append(_safe_wake(due, receipt=receipt))
        for event in tick.get("events") or []:
            if isinstance(event, Mapping):
                wakes.append(_safe_wake(event, receipt=receipt))
        return wakes


# ---------------------------------------------------------------------------------------------
# P2 的目标推进（只记录模型实际做了什么；不覆盖、不补齐）
# ---------------------------------------------------------------------------------------------

STATE_AWAIT_CONTINUE = "AWAIT_CONTINUE"
STATE_AWAIT_UNKNOWN = "AWAIT_UNKNOWN"
STATE_AWAIT_CAS = "AWAIT_CAS"
STATE_AWAIT_NO_ACTION = "AWAIT_NO_ACTION"
STATE_AWAIT_CONTINUE_AFTER_QUIET = "AWAIT_CONTINUE_AFTER_QUIET"
STATE_COVERED = "COVERED"


def _same_ref(left: Any, right: Any) -> bool:
    """两个引用是否指向同一件事；任一侧为空都算不同（不做「空等于空」的冒认）。"""

    return bool(_bare(left)) and _bare(left) == _bare(right)


def _reconcile_ok(reconcile: Optional[Mapping[str, Any]], injector: Any) -> bool:
    """原 operation 查询只有 owner 明确回「确证成功」且有可核验证据时才算已对账。"""

    row = dict(reconcile or {})
    fired = int(getattr(injector, "unknown_fired", 0) or 0)
    return bool(fired == 1
                and str(row.get("result") or "") in RECONCILE_SUCCESS_RESULTS
                and str(row.get("previous_status") or "") == "UNKNOWN"
                and row.get("evidence_verified") is True
                and int(row.get("evidence_count") or 0) >= 1)


def _cas_evidence(injector: Any, owner: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """CAS 必须是本次注入真的推进了 owner head，且原提交的权威原因是 expected_head 冲突。"""

    view = injector.view() if hasattr(injector, "view") else {}
    concurrent = dict(view.get("concurrent") or {})
    head_before = str(concurrent.get("head_before") or "")
    head_after = str(concurrent.get("head_after") or "")
    return {"injected": int(view.get("concurrent_fired") or 0) == 1,
            "applied": concurrent.get("applied") is True,
            "head_advanced": bool(head_after) and head_after != head_before,
            "owner_conflict": str((owner or {}).get("owner_status") or "")
            == CAS_OWNER_CONFLICT}


def _apply_round(state: str, wake: Mapping[str, Any], *, versions_before: int,
                 versions_after: int, injector: SubmissionInjector,
                 activity_matches: bool = False, same_work: bool = False,
                 round_has_receipt: bool = False, reconcile_ok: bool = False,
                 cas_evidence: Optional[Mapping[str, Any]] = None) -> str:
    """纯函数：只看这一轮真的发生了什么；证据不足一律不推进。

    * 续作必须同时满足：A 的真版本增加、决定是 CONTINUE、本轮 receipt 指向 A、Activity
      与 P1 的精确相同。只有 has_activity 非空不再算数。
    * UNKNOWN→CAS 要求本次注入确实触发（一次）且原 operation 已确证对账。
    * CAS→NO_ACTION 要求注入的并发写真的落 owner、head 真的推进，且原 Action 的权威原因
      是 ``CONFLICT``；任意 FAILED/GATE_OFF/先验拒绝都不算。
    * NO_ACTION 必须是模型自己的枚举结果，且本轮无写入、无 Action、无 receipt。
    """

    if int(wake.get("llm_calls") or 0) <= 0:
        return state
    decision = str(wake.get("decision") or "")
    action_status = str(wake.get("action_status") or "")
    outcome = str(wake.get("outcome") or "")
    wrote = versions_after > versions_before
    target_ok = bool(activity_matches and same_work and round_has_receipt)
    if state == STATE_AWAIT_CONTINUE:
        if wrote and decision == "CONTINUE" and target_ok:
            injector.arm_unknown = True
            return STATE_AWAIT_UNKNOWN
        return state
    if state == STATE_AWAIT_UNKNOWN:
        fired = int(getattr(injector, "unknown_fired", 0) or 0)
        if action_status == "UNKNOWN" and fired == 1 and reconcile_ok and target_ok:
            injector.arm_concurrent = True
            return STATE_AWAIT_CAS
        return state
    if state == STATE_AWAIT_CAS:
        evidence = dict(cas_evidence or {})
        if (action_status == "FAILED" and target_ok and evidence.get("injected") is True
                and evidence.get("applied") is True
                and evidence.get("head_advanced") is True
                and evidence.get("owner_conflict") is True):
            return STATE_AWAIT_NO_ACTION
        return state
    if state == STATE_AWAIT_NO_ACTION:
        if (decision == "NO_ACTION" and outcome == "NO_ACTION" and not wrote
                and not wake.get("action_id") and not round_has_receipt):
            return STATE_AWAIT_CONTINUE_AFTER_QUIET
        return state
    if state == STATE_AWAIT_CONTINUE_AFTER_QUIET:
        if wrote and decision == "CONTINUE" and target_ok:
            return STATE_COVERED
        return state
    return state


def _coverage_from_states(*, p1_write: bool, p1_activity: bool, pause_written: bool,
                          paused_zero: bool, states: Sequence[str], final_state: str,
                          unknown_reconciled: bool = False,
                          cas_refused: bool = False) -> dict[str, Any]:
    """六个场景各自要独立的证据，不按状态顺序倒推。

    ``unknown_reconciled`` / ``cas_refused`` 由调用方从本轮注入与 owner 权威返回算出；
    默认 False 是 fail-closed：没给证据就不算发生。
    """

    reached_unknown = (STATE_AWAIT_UNKNOWN in states
                       or final_state in (STATE_AWAIT_UNKNOWN, STATE_AWAIT_CAS,
                                          STATE_AWAIT_NO_ACTION, STATE_AWAIT_CONTINUE_AFTER_QUIET,
                                          STATE_COVERED))
    reached_cas = (STATE_AWAIT_CAS in states
                   or final_state in (STATE_AWAIT_CAS, STATE_AWAIT_NO_ACTION,
                                      STATE_AWAIT_CONTINUE_AFTER_QUIET, STATE_COVERED))
    reached_cas_refusal = (STATE_AWAIT_NO_ACTION in states
                           or final_state in (STATE_AWAIT_NO_ACTION,
                                              STATE_AWAIT_CONTINUE_AFTER_QUIET, STATE_COVERED))
    reached_no_action = (STATE_AWAIT_CONTINUE_AFTER_QUIET in states
                         or final_state in (STATE_AWAIT_CONTINUE_AFTER_QUIET, STATE_COVERED))
    flags = {
        "p1_real_write": bool(p1_write),
        "p1_formal_activity": bool(p1_activity),
        "operator_pause_set": bool(pause_written),
        "p2_paused_zero_requests": bool(paused_zero),
        "continue_same_activity": reached_unknown,
        "unknown_reconciled": bool(unknown_reconciled) and reached_cas,
        "cas_refused": bool(cas_refused) and reached_cas_refusal,
        "no_action_chosen_zero_write": reached_no_action,
        "continue_after_quiet": final_state == STATE_COVERED,
    }
    flags["g1_chain_complete"] = all(flags.values())
    flags["not_covered"] = [name for name, ok in flags.items()
                            if not ok and name != "g1_chain_complete"]
    return flags


# ---------------------------------------------------------------------------------------------
# 阶段 P1：真实第一版 + 正式 Activity + operator 中断 + 正规收尾
# ---------------------------------------------------------------------------------------------


def _phase_instance(root: Path | str) -> Path:
    """``demo.prepare_demo`` 的 Life 实例目录（与 ``components/phone/life.py`` 同名常量一致）。"""

    from components.phone.life import ONE_DAY_DIRNAME

    return Path(root) / ONE_DAY_DIRNAME


def _phase_p1(config: Mapping[str, Any]) -> dict[str, Any]:
    import one_day_life as odl

    root = Path(str(config["root"]))
    offline = bool(config.get("offline"))
    instance = _phase_instance(root)
    report: dict[str, Any] = {"phase": "p1", "pid": os.getpid(), "root": str(root),
                              "batch": _identity().key, "run_id": _identity().run_id,
                              "offline": offline,
                              "window": _window_view(root), "rounds": [],
                              "stop_reason": ""}
    data = odl.read_instance(instance)
    child = None
    bundle = None
    trace = CandidateTrace(root, phase="p1",
                           enabled=bool(config.get("capture_candidates")))
    try:
        child = _start_supply(root, tag="g1-p1")
        env = _runtime_env(root, offline=offline)
        if not offline:
            model = _verify_model_config(env)
            report["model"] = model
            if not model.get("ok"):
                raise Refused(str(model.get("reason_code")), "model parameters differ")
        state_root = Path(env["NYA02_RESIDENT_STATE"])
        budget = _budget(state_root)
        latch = SendLatch(lambda: _deadline_passed(root),
                          on_stop=lambda code: _record_batch_stop(root, code))
        report["ledger"] = _ledger_view(budget)
        bundle = _build_runtime(root, state_root=state_root, budget=budget, latch=latch,
                                env=env, offline=offline, plan=config.get("plan"),
                                trace=trace)
        driver = ExplicitDriver(bundle, clock=_FixtureClock()) if offline else RunnerDriver(bundle)
        excluded = _excluded_artifact_ids(bundle["cycle"], data)
        artifact_a = ""
        activity_ref = ""
        report["write_confirmed"] = False
        for index in range(MAX_TICKS):
            stop = _loop_stop(root, budget, latch)
            if stop:
                report["stop_reason"] = stop
                break
            trace.begin_round(index + 1, trigger="p1")
            prior = getattr(bundle["cycle"], "last_receipt", None)
            wakes = driver.next()
            receipt = _live_receipt(bundle, prior)
            trace.end_round(wakes, receipt=receipt)
            trace.observe_after_tick(wakes)
            for wake in wakes:
                report["rounds"].append({"index": index + 1, "wake": wake})
            if receipt is None:
                if offline:
                    break  # 离线计划已走完而这一轮没有正式 step
                _sleep_between_ticks(root)
                continue
            receipt_artifact = _bare(receipt.get("artifact_id"))
            receipt_activity = str(receipt.get("activity_ref") or "")
            owner_created, owner_version_id = _owner_version_from_wakes(wakes)
            point = (_resume(bundle["port"], receipt_artifact) if receipt_artifact
                     else {"artifact": "", "head": "", "versions": 0})
            # A 必须是「本轮 owner 明确新建的版本 + 本轮 receipt 指向这件作品与正式 Activity」；
            # 不拿不相关的 changed 或落库前就存在的版本冒本轮成果。
            confirmed = _p1_confirmed(
                owner_created=owner_created, owner_version_id=owner_version_id,
                receipt_artifact=receipt_artifact, receipt_activity=receipt_activity,
                excluded=excluded, point=point)
            report["owner_version_created"] = owner_created
            report["owner_version_id"] = owner_version_id or None
            report["receipt_artifact"] = receipt_artifact or None
            report["receipt_activity"] = receipt_activity or None
            if not confirmed:
                if offline:
                    report["stop_reason"] = "P1_NO_NEW_OWNER_VERSION"
                    break
                _sleep_between_ticks(root)
                continue
            artifact_a = receipt_artifact
            activity_ref = receipt_activity
            # f 的判据需要「首个真实 Action」本身，不能只凭版本增长。只取既有安全 id。
            first_action_id = ""
            for wake in wakes:
                if str(wake.get("action_id") or ""):
                    first_action_id = str(wake["action_id"])
                    break
            report.update({"artifact_a": artifact_a, "activity_ref": activity_ref,
                           "first_action_id": first_action_id or None,
                           "write_confirmed": True,
                           "first_receipt": {"step_id": receipt.get("step_id"),
                                             "outcome": receipt.get("outcome"),
                                             "artifact_id": receipt.get("artifact_id"),
                                             "activity_ref": receipt.get("activity_ref")},
                           "head_after_write": point["head"],
                           "versions_after_write": point["versions"]})
            break
        if artifact_a:
            pause = odl.set_pause(instance, True, reason="g1:operator-interrupt")
            report["pause"] = {"paused": bool((pause.get("pause") or {}).get("paused")),
                               "revision": (pause.get("pause") or {}).get("revision")}
            report["state_at_pause"] = odl.status(instance)["state"]["state_class"]
            trace.event("OPERATOR_PAUSE_SET",
                        paused=bool((pause.get("pause") or {}).get("paused")),
                        revision=(pause.get("pause") or {}).get("revision"),
                        state_class=report["state_at_pause"],
                        activity_ref=activity_ref)
        else:
            report.setdefault("stop_reason", "P1_NOT_COVERED")
    except GateClosed as exc:
        report["stop_reason"] = exc.reason_code
    except Refused as exc:
        report["stop_reason"] = exc.reason_code
        report["refusal_detail"] = exc.detail[:200]
    finally:
        report.setdefault("write_confirmed", False)
        report["close_runtime"] = _release_runtime(bundle)
        report["close_supply"] = _stop_supply(child)
        report["ledger"] = _ledger_view(budget) if "budget" in locals() else report.get("ledger")
        if _identity().key == F_PROFILE.key:
            # f：phase 子进程自己的实际 gate 与写面解析；只记路径/布尔，不读默认台账。
            report["world_gate"] = os.environ.get("WORLD_BODY_INTENT_MUTATION_GATE")
            report["isolation"] = _isolation_refresh(_isolation_view(root))
        report["candidate_trace"] = trace.view()
        _dump_phase(report, root)
    return report


# ---------------------------------------------------------------------------------------------
# 阶段 P2：同根暂停纯读 → 解除 → 真实续写 → UNKNOWN 对账 → CAS 拒 → NO_ACTION → 再续
# ---------------------------------------------------------------------------------------------


def _phase_p2(config: Mapping[str, Any]) -> dict[str, Any]:
    import one_day_life as odl

    root = Path(str(config["root"]))
    offline = bool(config.get("offline"))
    instance = _phase_instance(root)
    artifact_a = str(config.get("artifact_a") or "")
    report: dict[str, Any] = {"phase": "p2", "pid": os.getpid(), "root": str(root),
                              "batch": _identity().key, "run_id": _identity().run_id,
                              "offline": offline,
                              "window": _window_view(root), "rounds": [],
                              "artifact_a": artifact_a, "stop_reason": "",
                              "activity_ref": str(config.get("activity_ref") or ""),
                              "p1_confirmed": bool(config.get("p1_confirmed")),
                              "states": [], "injections": {}, "cas_evidence": None,
                              "cas_owner": None, "paused_round": None, "reconcile": None}
    data = odl.read_instance(instance)
    child = None
    bundle = None
    injector = None
    trace = CandidateTrace(root, phase="p2",
                           enabled=bool(config.get("capture_candidates")))

    def _coverage_kwargs() -> dict[str, Any]:
        """覆盖标志只由本轮真实证据算出；注入/对账/owner 返回缺一项就是 False。"""

        view = injector.view() if injector is not None else {}
        concurrent = dict(view.get("concurrent") or {})
        reconcile_ok = injector is not None and _reconcile_ok(report.get("reconcile"), injector)
        cas_ok = (injector is not None
                  and int(view.get("concurrent_fired") or 0) == 1
                  and concurrent.get("applied") is True
                  and bool(concurrent.get("head_after"))
                  and str(concurrent.get("head_after"))
                  != str(concurrent.get("head_before") or "")
                  and str((report.get("cas_owner") or {}).get("owner_status") or "")
                  == CAS_OWNER_CONFLICT)
        return dict(
            p1_write=bool(report.get("p1_confirmed") and report.get("artifact_a")),
            p1_activity=bool(report.get("p1_confirmed") and report.get("activity_ref")),
            pause_written=bool((report.get("pause_at_start") or {}).get("paused")),
            paused_zero=bool(report.get("paused_round_zero_requests")),
            states=list(report.get("states") or []),
            final_state=str(report.get("final_state") or STATE_AWAIT_CONTINUE),
            unknown_reconciled=reconcile_ok,
            cas_refused=cas_ok)

    try:
        child = _start_supply(root, tag="g1-p2")
        env = _runtime_env(root, offline=offline)
        if not offline:
            model = _verify_model_config(env)
            report["model"] = model
            if not model.get("ok"):
                raise Refused(str(model.get("reason_code")), "model parameters differ")
        state_root = Path(env["NYA02_RESIDENT_STATE"])
        budget = _budget(state_root)
        latch = SendLatch(lambda: _deadline_passed(root),
                          on_stop=lambda code: _record_batch_stop(root, code))
        report["ledger"] = _ledger_view(budget)
        report["pause_at_start"] = odl.status(instance)["pause"]
        report["state_at_start"] = odl.status(instance)["state"]["state_class"]
        bundle = _build_runtime(root, state_root=state_root, budget=budget, latch=latch,
                                env=env, offline=offline, plan=config.get("plan"),
                                trace=trace)
        driver = ExplicitDriver(bundle, clock=_FixtureClock()) if offline else RunnerDriver(bundle)
        injector = SubmissionInjector(bundle, artifact_a=artifact_a,
                                      grant_id=str(data.get("artifact_grant_id") or ""),
                                      operation_prefix="g1:%s" % report["phase"])
        a_before = _resume(bundle["port"], artifact_a)
        report.update({"head_at_start": a_before.get("head"),
                       "versions_at_start": a_before.get("versions")})
        report["activity_at_start"] = _foreground_activity(bundle, report["activity_ref"])

        # -- 1. 暂停轮：纯读零请求 ---------------------------------------------------
        paused_zero = False
        for index in range(MAX_TICKS):
            stop = _loop_stop(root, budget, latch)
            if stop:
                report["stop_reason"] = stop
                break
            trace.begin_round(index + 1, trigger="p2-paused")
            if offline:
                wake = driver.paused()
                wakes = [wake]
            else:
                wakes = driver.next()
                wake = wakes[0] if wakes else {}
            trace.end_round(wakes)
            trace.observe_after_tick(wakes)
            report["paused_round"] = wake
            if str(wake.get("outcome") or "") == "PAUSED_BY_OPERATOR" \
                    or str(wake.get("status") or "") == "PAUSED_BY_OPERATOR":
                paused_zero = int(wake.get("llm_calls") or 0) == 0
                break
            if offline:
                report["stop_reason"] = "P2_PAUSED_ROUND_NOT_OBSERVED"
                break
            _sleep_between_ticks(root)
        report["paused_round_zero_requests"] = paused_zero
        if not paused_zero:
            report.setdefault("stop_reason", "P2_PAUSED_ROUND_NOT_OBSERVED")
            return report  # coverage/收尾统一走 finally

        # -- 2. operator 解除暂停；之后完全由真实决定推进 ------------------------------
        odl.set_pause(instance, False, reason="g1:operator-resume")
        report["pause_after_resume"] = odl.status(instance)["pause"]
        trace.event("OPERATOR_PAUSE_RELEASED", paused=False,
                    activity_ref=report.get("activity_ref"))
        state = STATE_AWAIT_CONTINUE
        report["states"].append(state)
        last_versions = int(report.get("versions_at_start") or 0)
        expected_activity = report["activity_ref"]
        for index in range(MAX_TICKS):
            stop = _loop_stop(root, budget, latch)
            if stop:
                report["stop_reason"] = stop
                break
            trace.begin_round(index + 1, trigger="p2")
            prior = getattr(bundle["cycle"], "last_receipt", None)
            wakes = driver.next()
            receipt = _live_receipt(bundle, prior)
            trace.end_round(wakes, receipt=receipt)
            trace.observe_after_tick(wakes)
            versions_now = int(_resume(bundle["port"], artifact_a).get("versions") or 0)
            round_has_receipt = receipt is not None
            round_artifact = _bare(receipt.get("artifact_id")) if receipt else ""
            same_work = bool(round_artifact) and round_artifact == _bare(artifact_a)
            for wake in wakes:
                if int(wake.get("llm_calls") or 0) <= 0:
                    continue
                # 同作品、同 P1 Activity 精确匹配才算「续这一件」；别的新 Activity 原样记录。
                round_activity = (str(wake.get("activity_ref") or "")
                                  or str((receipt or {}).get("activity_ref") or ""))
                activity_matches = _same_ref(round_activity, expected_activity)
                report["rounds"].append({"index": index + 1, "state_before": state,
                                         "activity_matches": activity_matches,
                                         "same_work": same_work, "wake": wake})
                action_id = str(wake.get("action_id") or "")
                action_status = str(wake.get("action_status") or "")
                reconcile_ok = False
                cas_evidence = None
                if action_id and action_status == "UNKNOWN":
                    # 只对账同一个 operation：owner 自己的 resolve_operation，零裸重发。
                    resolved = bundle["runtime"].action_command.reconcile_action(
                        action_id=action_id)
                    reconciliation = dict((resolved or {}).get("reconciliation") or {})
                    evidences = []
                    try:
                        evidences = bundle["runtime"].action_read.list_result_evidences(
                            action_id) or []
                    except Exception:  # noqa: BLE001
                        evidences = []
                    report["reconcile"] = {
                        "action_id": action_id,
                        "result": reconciliation.get("result"),
                        "previous_status": reconciliation.get("previous_status"),
                        "evidence_count": len(evidences),
                        "evidence_verified": all(bool(row.get("verified"))
                                                 for row in evidences) if evidences else False,
                        "versions_after": versions_now,
                    }
                    report["unknown_versions_after"] = versions_now
                    reconcile_ok = _reconcile_ok(report["reconcile"], injector)
                elif action_id and action_status == "FAILED":
                    report["cas_owner"] = _action_owner_status(bundle, action_id)
                    cas_evidence = _cas_evidence(injector, report["cas_owner"])
                    report["cas_evidence"] = cas_evidence
                new_state = _apply_round(
                    state, wake, versions_before=last_versions, versions_after=versions_now,
                    injector=injector, activity_matches=activity_matches,
                    same_work=same_work, round_has_receipt=round_has_receipt,
                    reconcile_ok=reconcile_ok, cas_evidence=cas_evidence)
                if new_state != state:
                    state = new_state
                    report["states"].append(state)
                last_versions = versions_now
                if state == STATE_COVERED:
                    break
            report["injections"] = injector.view()
            report["versions_now"] = versions_now
            if state == STATE_COVERED:
                report["stop_reason"] = "G1_CHAIN_COVERED"
                break
            if offline and not wakes:
                break
            if not offline:
                _sleep_between_ticks(root)
        report["final_state"] = state
        a_end = _resume(bundle["port"], artifact_a)
        report["head_at_end"] = a_end.get("head")
        report["versions_at_end"] = a_end.get("versions")
        return report  # coverage/收尾统一走 finally
    except GateClosed as exc:
        report["stop_reason"] = exc.reason_code
    except Refused as exc:
        report["stop_reason"] = exc.reason_code
        report["refusal_detail"] = exc.detail[:200]
    finally:
        if injector is not None:
            injector.detach()
        report["close_runtime"] = _release_runtime(bundle)
        report["close_supply"] = _stop_supply(child)
        if "budget" in locals():
            report["ledger"] = _ledger_view(budget)
        if injector is not None:
            report.setdefault("injections", injector.view())
        report.setdefault("final_state", STATE_AWAIT_CONTINUE)
        report["coverage"] = _coverage_from_states(**_coverage_kwargs())
        if _identity().key == F_PROFILE.key:
            # f：p2 也用同一套实例 world_home 隔离；gate 必须是显式 off。
            report["world_gate"] = os.environ.get("WORLD_BODY_INTENT_MUTATION_GATE")
            report["isolation"] = _isolation_refresh(_isolation_view(root))
        report["candidate_trace"] = trace.view()
        _dump_phase(report, root)
    return report


# ---------------------------------------------------------------------------------------------
# d 的唯一自然 phase：没有任何注入；只观察同次最终结构里的两条固定文本
# ---------------------------------------------------------------------------------------------


def _phase_natural(config: Mapping[str, Any]) -> dict[str, Any]:
    """d：一条自然持续的单 phase，从第一轮走到 cap／窗口／发送异常。

    这里**不构造** ``SubmissionInjector``、不调用 ``_provision_operator_material``、
    不设 operator pause、不做两 PID 恢复、不要求先写出首版才继续，也不按 G1 的六个场景
    判结果。Decision/内容都走正式 adapter 与正式 ``evaluate_once``/runner。
    """

    root = Path(str(config["root"]))
    if _identity().key == E_PROFILE.key:
        # 直接调用（绕过 cmd_phase）时同样先自查：服务/模型启动前失败关闭。
        _verify_isolated_child_env(root)
    offline = bool(config.get("offline"))
    report: dict[str, Any] = {"phase": PHASE_NATURAL, "pid": os.getpid(), "root": str(root),
                              "batch": _identity().key, "run_id": _identity().run_id,
                              "offline": offline, "window": _window_view(root),
                              "rounds": [], "round_count": 0, "stop_reason": "",
                              "natural_phase": True,
                              "operator_material_provisioned": False,
                              "control_created": False,
                              "injector_constructed": False,
                              # 子进程自己的 gate/home/registry/lifecycle 解析；跑完再补
                              # 本根 ledger 的存在与记录（只读本实例根）。
                              "isolation": _isolation_view(root)}
    child = None
    world_child = None
    bundle = None
    gate = _world_intent_gate(_identity()) or "off"
    trace = CandidateTrace(root, phase=PHASE_NATURAL,
                           enabled=bool(config.get("capture_candidates")),
                           pause_material=(_identity().key == D_PROFILE.key))
    try:
        child = _start_supply(root, tag="g1-natural")
        # 标准 World：同一实例根、同一 world home/package；gate 用本批自己的值
        # （e=canonical，d=off）。world child 起来且 socket 就绪之前不装配 runtime——
        # 不做"先跑起来再补服务"。
        world_child = _start_world(root, tag="g1-natural")
        world_socket = str(getattr(world_child, "socket_path", "") or "")
        report["world"] = {"started": True, "pid": world_child.pid,
                           "socket": world_socket, "gate": gate, "ready": False,
                           # World child 自己的 env（demo 显式写入的那一份），只取两个名字，
                           # 用来核对三处映射一致；不输出整份环境。
                           "child_env_gate": world_child.environment.get(
                               "WORLD_BODY_INTENT_MUTATION_GATE"),
                           "child_env_world_home": world_child.environment.get(
                               "CHIYO_WORLD_HOME"),
                           "child_env_registry": world_child.environment.get(
                               "WORLD_ACTION_AUTH_REGISTRY_DIR"),
                           "child_env_lifecycle": world_child.environment.get(
                               "WORLD_ACTION_LIFECYCLE_PATH")}
        env = _runtime_env(root, offline=offline)
        # 三处 env 映射必须是同一个固定值：runtime env 与装配进来的标准 adapter 读的是
        # 这一份；World child 的 env 已在 ``_start_world`` 里显式写入同一个值。
        expected_gate = _world_intent_gate(_identity())
        if (expected_gate is not None
                and str(env.get("WORLD_BODY_INTENT_MUTATION_GATE") or "") != expected_gate):
            raise Refused(REASON_WORLD_GATE_MISMATCH,
                          "runtime env gate %r != %r"
                          % (env.get("WORLD_BODY_INTENT_MUTATION_GATE"), expected_gate))
        if str(env.get("WORLD_BODY_SOCKET") or "") != world_socket:
            raise Refused(REASON_WORLD_NOT_READY,
                          "instance world socket differs from the live world child")
        if not offline:
            model = _verify_model_config(env)
            report["model"] = model
            if not model.get("ok"):
                raise Refused(str(model.get("reason_code")), "model parameters differ")
        state_root = Path(env["NYA02_RESIDENT_STATE"])
        budget = _budget(state_root)
        latch = SendLatch(lambda: _deadline_passed(root),
                          on_stop=lambda code: _record_batch_stop(root, code))
        report["ledger"] = _ledger_view(budget)
        bundle = _build_runtime(root, state_root=state_root, budget=budget, latch=latch,
                                env=env, offline=offline, plan=config.get("plan"),
                                trace=trace, world_feed=True)
        # assembly 的 world record 来自 resolve_world_provider 对 World 的一次真实
        # ``get_status()`` 读回；READY 才等于「World 真答了」，不是外面塞的真值。
        world_record = dict(bundle["assembled"].get("world") or {})
        report["world"].update({"status": world_record.get("status"),
                                "reason": str(world_record.get("reason") or ""),
                                "ready": world_record.get("status") == "READY"})
        if world_record.get("status") != "READY":
            raise Refused(REASON_WORLD_NOT_READY,
                          str(world_record.get("reason") or "world is not ready"))
        driver = (ExplicitDriver(bundle, clock=_FixtureClock()) if offline
                  else RunnerDriver(bundle))
        for index in range(MAX_TICKS):
            stop = _loop_stop(root, budget, latch)
            if stop:
                report["stop_reason"] = stop
                break
            if offline and index >= OFFLINE_NATURAL_MAX_ROUNDS:
                # 离线只用夹具计划做有界接线证明；真实 run 没有这几条提前退出口。
                report["stop_reason"] = "OFFLINE_ROUND_LIMIT"
                break
            if offline and index > 0 and not getattr(bundle["decision_transport"], "plan", None):
                report["stop_reason"] = "OFFLINE_PLAN_EXHAUSTED"
                break
            trace.begin_round(index + 1, trigger="natural")
            prior = getattr(bundle["cycle"], "last_receipt", None)
            wakes = driver.next()
            receipt = _live_receipt(bundle, prior)
            trace.end_round(wakes, receipt=receipt)
            trace.observe_after_tick(wakes)
            report["round_count"] = index + 1
            for wake in wakes:
                report["rounds"].append({"index": index + 1,
                                         "wake": _safe_wake(wake, receipt=receipt)})
            if offline and not wakes:
                break
            if not offline:
                _sleep_between_ticks(root)
        else:
            report["stop_reason"] = "MAX_TICKS_REACHED"
    except GateClosed as exc:
        report["stop_reason"] = exc.reason_code
    except Refused as exc:
        report["stop_reason"] = exc.reason_code
        report["refusal_detail"] = exc.detail[:200]
    finally:
        report["close_runtime"] = _release_runtime(bundle)
        report["close_world"] = _stop_world(world_child)
        report["close_supply"] = _stop_supply(child)
        if "budget" in locals():
            report["ledger"] = _ledger_view(budget)
        report["candidate_trace"] = trace.view()
        # 本次 trace 已逐行落盘（每行 open/close）；只读摘要放在 phase 报告里，
        # 父级 audit/run 与离线验收看到的是同一份事实，不另建观测。
        report["observation"] = _d_trace_observation(root)
        # 跑完后只读本根 ledger 是否存在、里面有哪些固定枚举/ID；不碰仓库默认路径。
        if isinstance(report.get("isolation"), dict):
            report["isolation"] = _isolation_refresh(report["isolation"])
        _dump_phase(report, root)
    return report


def _d_trace_observation(root: Path | str) -> dict[str, Any]:
    """d 的事后只读摘要：只数本批自己的 trace 行，不新增记录、不推断因果。

    四项里前三项都来自既有 trace 字段（请求的合法集、pause_condition 块、ROUND 的
    ``model_decision``）；条件与作品的同作关系 trace 不携带，一律 ``UNKNOWN``，
    不逆哈希、不猜。第四项就是两个布尔逐次记录。另有一项
    ``world_candidates_per_request``：每次请求的候选里 ``source_kind=WORLD_OPPORTUNITY``
    的个数，取自同一行的固定枚举，零请求时为空列表——它证明真实 World 候选进了 AG-0，
    不是另造一份候选集。
    """

    path = _experiment_dir(root) / (CANDIDATE_TRACE_FILENAME % PHASE_NATURAL)
    view: dict[str, Any] = {
        "trace_path": str(path), "status": "UNKNOWN", "rows": 0,
        "pause_choice_opportunity": None, "abandon_in_legal_set": None,
        "pause_condition_offered": 0, "pause_chosen": 0, "abandon_chosen": 0,
        "pause_condition_same_work": "UNKNOWN",
        "pause_condition_same_work_why": "trace 不携带条件与作品的同作关系",
        "prompt_rows": [],
    }
    if not path.exists():
        view["why"] = "本次没有 trace 文件"
        return view
    rows: list[dict[str, Any]] = []
    bad = 0
    try:
        with open(path, encoding="utf-8") as handle:
            for line_no, line in enumerate(handle):
                if line_no >= CANDIDATE_TRACE_MAX_RECORDS:
                    view["truncated"] = True
                    break
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except ValueError:
                    bad += 1
                    continue
                if isinstance(row, Mapping):
                    rows.append(dict(row))
                else:
                    bad += 1
    except OSError as exc:  # noqa: BLE001 - 读不到只报状态，不当空
        view["why"] = "trace 不可读: %s" % type(exc).__name__
        return view

    requests = [row for row in rows if row.get("stage") == STAGE_REQUEST]
    rounds = [row for row in rows if row.get("stage") == STAGE_ROUND]
    #: 每次真实请求里 AG-0 交给 AG-1 的 WORLD_OPPORTUNITY 候选数。取自同一行
    #: ``candidates[].source_kind``（固定枚举），不重算候选集；零请求时为空列表。
    world_candidates = [
        sum(1 for candidate in (row.get("candidates") or [])
            if isinstance(candidate, Mapping)
            and candidate.get("source_kind") == "WORLD_OPPORTUNITY")
        for row in requests]
    prompts = [row for row in rows if row.get("stage") in (STAGE_PROMPT, STAGE_PROMPT_UNSENT)]
    pause_chosen = 0
    abandon_chosen = 0
    for row in rounds:
        for wake in row.get("wakes") or []:
            if not isinstance(wake, Mapping) or wake.get("model_decision") is not True:
                continue
            decision = str(wake.get("decision") or "")
            if decision == "PAUSE":
                pause_chosen += 1
            elif decision == "ABANDON":
                abandon_chosen += 1
    offered = sum(1 for row in requests
                  if isinstance(row.get("pause_condition"), Mapping)
                  and row["pause_condition"].get("available") is True)
    view.update({
        "status": "OBSERVED" if requests else "NO_MODEL_REQUEST",
        "rows": len(rows), "bad_lines": bad,
        "request_rows": len(requests), "round_rows": len(rounds),
        "pause_chosen": pause_chosen, "abandon_chosen": abandon_chosen,
        "pause_condition_offered": offered,
        "world_candidates_per_request": world_candidates,
        "prompt_rows": [{name: row.get(name)
                         for name in ("send_status", "fact_sentence_present",
                                      "tail_present", "error")}
                        for row in prompts],
    })
    if requests:
        # 有请求才有「合法集里有没有 PAUSE/ABANDON」这个事实；零请求是 UNKNOWN 不是 False。
        view["pause_choice_opportunity"] = any(
            "PAUSE" in list(row.get("legal_decision_enums") or ()) for row in requests)
        view["abandon_in_legal_set"] = any(
            "ABANDON" in list(row.get("legal_decision_enums") or ()) for row in requests)
    return view


def _loop_stop(root: Path | str, budget: Any, latch: SendLatch) -> str:
    if latch.closed:
        return latch.stop_reason
    recorded = _durable_stop(root)
    if recorded:
        # P1 落盘的停因对 P2 的新锁同样有效：失败不能因为换了进程就复活。
        latch.stop(recorded)
        return recorded
    if _deadline_passed(root):
        return REASON_DEADLINE_PASSED
    calls = _ledger_calls(budget)
    if calls is None:
        return REASON_LEDGER_UNREADABLE
    if calls >= REQUEST_CAP:
        return REASON_REQUEST_CAP
    return ""


def _sleep_between_ticks(root: Path | str) -> None:
    window = _read_json(_window_path(root))
    remaining = TICK_INTERVAL_SECONDS
    try:
        remaining = min(TICK_INTERVAL_SECONDS,
                        float(window["deadline_monotonic"]) - time.monotonic())
    except (KeyError, TypeError, ValueError):
        remaining = 0.0
    if remaining > 0:
        time.sleep(min(remaining, TICK_INTERVAL_SECONDS))


# ---------------------------------------------------------------------------------------------
# 实例准备（0 产品调用）
# ---------------------------------------------------------------------------------------------


def prepare(root: Path | str, *, expected_root: Optional[Path | str] = None,
            offline: bool = False) -> dict[str, Any]:
    profile = _identity()
    root_path = Path(str(root)).expanduser()
    expected = Path(str(expected_root)) if expected_root is not None else profile.root
    refusal = _check_root(root_path, expected=expected)
    if refusal is not None:
        return refusal
    clock_refusal = _offline_clock_refusal(root_path, offline=bool(offline))
    if clock_refusal is not None:
        return clock_refusal
    if not offline and not _explicit_model_base_url(os.environ):
        # 真实批缺地址：在建实例、写 prereg 之前就拒绝，不靠事后才发现。
        return {"ok": False, "reason_code": REASON_NO_MODEL_CONFIG,
                "detail": "NYA02_MODEL_BASE_URL is not set for a real batch"}
    if (_experiment_dir(root_path) / PREREG_FILENAME).exists():
        return {"ok": False, "reason_code": REASON_ALREADY_PREPARED, "root": str(root_path)}
    if root_path.exists() and any(root_path.iterdir()):
        return {"ok": False, "reason_code": REASON_ROOT_NOT_FRESH, "root": str(root_path)}
    # 新批的独立 Memory authority 也必须新鲜：已有内容就在写入前拒绝，
    # 既不默默复用旧批 authority，也不删除重建。
    authority_dir = root_path.parent / ("%s-memory-authority" % root_path.name)
    authority = authority_dir / "authority.db"
    try:
        authority_taken = bool(authority_dir.exists()
                               and (authority.exists() or any(authority_dir.iterdir())))
    except OSError as exc:  # noqa: BLE001 - 读不出来同样失败关闭
        return {"ok": False, "reason_code": REASON_AUTHORITY_NOT_FRESH,
                "authority": str(authority_dir),
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    if authority_taken:
        return {"ok": False, "reason_code": REASON_AUTHORITY_NOT_FRESH,
                "authority": str(authority_dir),
                "detail": "authority already exists; a new batch never reuses it"}

    from components.phone import demo, life as phone_life, service as phone_service

    try:
        prepared = demo.prepare_demo(
            root_path, with_world=True, seed_work_artifact=False,
            reading_title="", reading_content=None, run_id=profile.run_id,
            request_utc_day_max=UTC_DAY_CAP, request_run_window_max=REQUEST_CAP,
            max_cognition_calls_per_day=UTC_DAY_CAP)
    except Exception as exc:  # noqa: BLE001 - reported as a refusal, never hidden
        return {"ok": False, "reason_code": REASON_PREPARE_FAILED,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    if not isinstance(prepared, Mapping) or prepared.get("ok") is not True:
        return {"ok": False, "reason_code": REASON_PREPARE_FAILED,
                "detail": "demo.prepare_demo did not report ok"}

    instance = phone_service.load_instance(root_path)
    subject = str(instance.get("subject") or "")
    workspace_id = str(instance.get("workspace_id") or "")
    grant_id = str(instance.get("artifact_grant_id") or "")
    if not (subject and workspace_id and grant_id):
        return {"ok": False, "reason_code": REASON_PREPARE_FAILED,
                "detail": "instance lacks subject/workspace/grant"}

    # d/e 是自然观测批：准备阶段只留 ``demo.prepare_demo`` 自己建的实例结构。
    # 既不导入 operator 材料/对照作品，也不写 mark_unclear —— 连 Supply 都不在 prepare 里起。
    natural = _is_natural(profile)
    child = None
    material: Optional[dict[str, Any]] = {}
    control: Optional[dict[str, Any]] = {}
    if not natural:
        try:
            child = _start_supply(root_path, tag="g1-prepare")
            data = _read_instance_data(root_path)
            env = _runtime_env(root_path, offline=offline)
            material, control = _provision_operator_material(
                root_path, data=data, env=env)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason_code": REASON_MATERIAL_FAILED,
                    "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
        finally:
            _stop_supply(child)

    try:
        authority.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(authority.parent, 0o700)
        except OSError:
            pass
        memory = phone_life.enable_instance_memory(
            root_path, subject, "%s:memory" % profile.run_name, authority_path=authority)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason_code": REASON_MEMORY_BIND_FAILED,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    if memory.get("ok") is not True:
        return {"ok": False, "reason_code": REASON_MEMORY_BIND_FAILED,
                "detail": str(memory.get("status") or memory.get("reason") or "")}

    env = _runtime_env(root_path, offline=offline)
    state_root = Path(str(env.get("NYA02_RESIDENT_STATE") or ""))
    if not state_root or not str(state_root).startswith(str(root_path)):
        return {"ok": False, "reason_code": REASON_PREPARE_FAILED,
                "detail": "state root is not inside the experiment root"}
    if str(env.get("CHIYO_AUTONOMOUS_RUN_ID") or "") != profile.run_id:
        return {"ok": False, "reason_code": REASON_PREPARE_FAILED,
                "detail": "instance run identity differs from the plan"}

    budget = _budget(state_root)
    try:
        budget.ensure_window(allow_new=True)
        view = budget.view()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason_code": REASON_LEDGER_NOT_ZERO,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    if _ledger_calls(budget) != 0:
        return {"ok": False, "reason_code": REASON_LEDGER_NOT_ZERO,
                "ledger": _ledger_view(budget)}

    prereg = {
        "schema": "g1.real.prereg.v1", "batch": profile.key,
        "run_id": profile.run_id, "domain_id": profile.domain_id,
        "root": str(root_path), "state_root": str(state_root), "subject": subject,
        "workspace_id": workspace_id, "artifact_grant_id": grant_id,
        "memory_authority": str(authority),
        "caps": {"request_cap": REQUEST_CAP, "window_seconds": WINDOW_SECONDS,
                 "utc_day_cap": UTC_DAY_CAP, "expected_history": profile.expected_history,
                 "check_interval_minutes": CHECK_INTERVAL_MINUTES},
        "model_params": {"provider": MODEL_PROVIDER,
                         "base_url": _explicit_model_base_url(env).rstrip("/"),
                         "model": MODEL_NAME, "temperature": MODEL_TEMPERATURE,
                         "stream": False, "decision_output_cap": DECISION_OUTPUT_CAP,
                         "content_output_cap": CONTENT_OUTPUT_CAP,
                         "seed": None, "top_p": None},
        "deadline": profile.deadline.isoformat(),
        "operator_material": (None if natural else {
            "title": MATERIAL_TITLE, "chars": len(MATERIAL_TEXT),
            "sha256": _sha(MATERIAL_TEXT), "artifact_id": material.get("artifact_id"),
            "source_revision": material.get("source_revision"),
        }),
        "operator_control": (None if natural else {
            "title": CONTROL_TITLE, "chars": len(CONTROL_TEXT),
            "sha256": _sha(CONTROL_TEXT), "artifact_id": control.get("artifact_id"),
            "note": "operator 事先放下的对照文本；不是她的经历，也不参与自然成果判定。",
        }),
        "prepared_at": _iso(),
        "note": "参数、材料与上限在第一次产品请求之前写在这里；之后只读。",
        "window_view": view.get("state"),
    }
    if natural:
        # 路径事实：这些不是从别处读出来的结论，而是这条独立路径本身没有这些动作。
        # d 的观察项原样保留（旧断言钉着整块字典）；e 只写世界动作这一批自己的事实。
        if profile.key == D_PROFILE.key:
            prereg["observation"] = {
                "mode": "natural_phase", "natural_phase": True,
                "operator_material_provisioned": False, "control_created": False,
                "injector_constructed": False,
                "booleans": ["fact_sentence_present", "tail_present"],
            }
        else:
            prereg["observation"] = {
                "mode": "natural_phase", "natural_phase": True,
                "operator_material_provisioned": False, "control_created": False,
                "injector_constructed": False,
                "world_intent_gate": _world_intent_gate(profile),
            }
        # 自然批的 gate 是封闭身份的一部分，写进 prereg 供 audit/摘要只读核对。
        prereg["world_intent_gate"] = _world_intent_gate(profile)
    if profile.key == F_PROFILE.key:
        # f：作品续写批，不启动 World 服务；gate/写面隔离仍是封闭事实，写进 prereg。
        prereg["world_intent_gate"] = _world_intent_gate(profile)
        prereg["observation"] = {
            "mode": "g1_limited_adoption", "world_service_started": False,
            "world_intent_gate": _world_intent_gate(profile),
        }
    _write_json(_experiment_dir(root_path) / PREREG_FILENAME, prereg, private=True)
    _write_json(_experiment_dir(root_path) / BATCH_FILENAME, {
        "schema": "g1.batch.v1", "batch": profile.key,
        "run_id": profile.run_id, "domain_id": profile.domain_id,
        "state": "prepared", "prepared_at": _iso(), "root": str(root_path),
        "state_root": str(state_root), "subject": subject, "run": None, "stop": None,
        "coverage": None,
    }, private=True)
    return {"ok": True, "root": str(root_path), "state_root": str(state_root),
            "batch": profile.key, "run_id": profile.run_id, "domain_id": profile.domain_id,
            "material": prereg["operator_material"], "control": prereg["operator_control"],
            "model_called": False, "request_sent": False}


def _read_instance_data(root: Path | str) -> dict[str, Any]:
    import one_day_life as odl

    return odl.read_instance(_phase_instance(root))


def _provision_operator_material(root: Path | str, *, data: Mapping[str, Any],
                                 env: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """用真实 owner 导入一份中性 operator 材料，并留下一件明确的对照作品。0 模型。"""

    import private_daily_items as pdi
    import research_workspace_service as rws
    from life_artifact_port import LifeArtifactPort

    live_socket = (env.get("LIFE_SUPPLY_SOCKET")
                   or env.get("CHIYO_NATIVE_LIFE_SUPPLY_SOCKET")
                   or data.get("supply_socket"))
    port = LifeArtifactPort(str(live_socket), str(data.get("subject_id")))
    service = rws.research_from_env(port, dict(env))
    if service is None:
        raise Refused(REASON_MATERIAL_FAILED, "research workspace did not resolve")
    answer = service.import_material(
        title=MATERIAL_TITLE, text=MATERIAL_TEXT, source_kind="user_supplied",
        origin="操作者导入的文本（口述记录）", operation_key="g1:real:material:1")
    if not answer.get("ok"):
        raise Refused(REASON_MATERIAL_FAILED, "material import failed")
    material = dict((answer.get("data") or {}).get("material") or {})
    material_id = str(material.get("artifact_id") or "")
    revision = str(material.get("source_revision") or "")
    if not material_id:
        raise Refused(REASON_MATERIAL_FAILED, "material import returned no artifact")

    items_id = str(data.get("private_items_artifact_id") or "")
    if items_id:
        store = pdi.PrivateDailyItems(port, items_id,
                                      workspace_id=str(data.get("workspace_id") or ""),
                                      grant_id=str(data.get("artifact_grant_id") or ""))
        store.mark_unclear(source_id=material_id, source_revision=revision,
                           note=MATERIAL_NOTE, marker_id="unclear:g1-real:1",
                           operation_key="g1:real:unclear:1")

    control = port.create_artifact(
        workspace_id=str(data.get("workspace_id") or ""), title=CONTROL_TITLE,
        content=CONTROL_TEXT, grant_id=str(data.get("artifact_grant_id") or ""),
        operation_key="g1:real:operator-control:1")
    if not getattr(control, "applied", False):
        raise Refused(REASON_MATERIAL_FAILED, "operator control artifact was refused")
    return ({"artifact_id": material_id, "source_revision": revision},
            {"artifact_id": str(control.artifact_id),
             "version_id": str(getattr(control, "version_id", "") or "")})


# ---------------------------------------------------------------------------------------------
# 真实运行入口（一次；started 先持久写，失败/中断拒重启）
# ---------------------------------------------------------------------------------------------


def _phase_child_env(profile: Optional[BatchProfile] = None,
                     root: Optional[Path | str] = None, *,
                     offline: bool = False) -> dict[str, str]:
    """phase 子进程的环境映射：e 显式 canonical，d 显式 off，a/b/c 原样继承。

    这里只构造要交给 ``subprocess.run`` 的 dict，不修改本进程的 ``os.environ``。

    自然批还要把**本实例自己的** world home 写进这份子进程 env：Life 侧的 World
    bridge / intent executor 只从进程环境读 ``CHIYO_WORLD_HOME``（或
    ``WORLD_ACTION_AUTH_REGISTRY_DIR``）来决定一次性授权签到哪本 registry。缺了它，
    非 MOVE 的正式动作会在签发前以 ``UNAVAILABLE`` 被拒，看起来像"世界没接上"。
    只覆盖本实例的值，不让父 shell 里别的 root 漏进来。
    """

    profile = profile or _identity()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    # 父 shell 里残留的离线标记一律不带进子进程；只有在**本次实际离线调用路径**里、
    # 父进程确实处于离线夹具参考时刻时，才给这一次的子进程树补上布尔标记。
    env.pop(OFFLINE_FIXTURE_ENV, None)
    if offline and _OFFLINE_REFERENCE is not None:
        env[OFFLINE_FIXTURE_ENV] = "1"
    if offline:
        # 离线子进程不继承父 shell 的生产模型地址/密钥；离线值由
        # ``_runtime_env(offline=True)`` 的显式合成映射注入，不从生产 env 猜。
        for key in [name for name in env if name.startswith("NYA02_MODEL_")]:
            env.pop(key, None)
    gate = _world_intent_gate(profile)
    if gate is not None:
        env["WORLD_BODY_INTENT_MUTATION_GATE"] = gate
    if profile.key in (E_PROFILE.key, F_PROFILE.key) and root is not None:
        # e/f：缺/坏 world_home 直接拒绝，不把父 shell 的 home/registry/lifecycle 带进子进程。
        env.update(_world_isolation_env(_require_instance_world_home(root)))
    elif _is_natural(profile) and root is not None:
        # d 维持 b6 交付时的宽松投影；a/b/c 不进入这里。
        world_home = _instance_world_home(root)
        if world_home:
            # 四个名字一起给：home 供 bridge/owner 定位，registry/lifecycle 两个显式路径
            # 让只读进程环境的写面也不依赖 ``<home>/run`` 是否已存在，更不回退仓库默认。
            env.update(_world_isolation_env(world_home))
    return env


def _instance_world_home(root: Path | str) -> Optional[Path]:
    """只读：本实例自己的 world home；读不到返回 None（由子进程自己报错）。"""

    try:
        from components.phone import service as phone_service

        instance = dict(phone_service.load_instance(Path(str(root))))
    except Exception:  # noqa: BLE001 - 读不到不等于可以猜一个
        return None
    value = str(instance.get("world_home") or "").strip()
    return Path(value) if value else None


def _require_instance_world_home(root: Path | str) -> Path:
    """e 专用严格解析：实例 world_home 必须是根内一个已存在的绝对目录。

    合法值按实例已有绑定解析，只读不创建。缺失/不可读/相对路径/解析后落在根外
    （含符号链接逃逸）一律 ``Refused``：不允许退回父进程的 home/registry/lifecycle，
    也不猜一个路径自动创建。
    """

    world_home = _instance_world_home(root)
    if world_home is None:
        raise Refused(REASON_WORLD_HOME_UNAVAILABLE,
                      "instance world_home is missing or unreadable")
    if not world_home.is_absolute():
        raise Refused(REASON_WORLD_HOME_UNAVAILABLE,
                      "instance world_home is not an absolute path")
    try:
        resolved = world_home.resolve()
        root_resolved = Path(str(root)).resolve()
    except OSError as exc:  # noqa: BLE001 - 解析失败同样失败关闭
        raise Refused(REASON_WORLD_HOME_UNAVAILABLE,
                      "%s: %s" % (type(exc).__name__, str(exc)[:120]))
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise Refused(REASON_WORLD_HOME_OUTSIDE_ROOT,
                      "instance world_home resolves outside the experiment root")
    if not resolved.is_dir():
        raise Refused(REASON_WORLD_HOME_UNAVAILABLE,
                      "instance world_home directory does not exist")
    return resolved


def _verify_isolated_child_env(root: Path | str) -> None:
    """e 的 phase 子进程自查：实际 ``os.environ`` 必须与本实例固定映射逐项相等。

    这里只看子进程自己那份环境（由 ``_phase_child_env`` 显式映射进来），不读父进程、
    不改 ``os.environ``。少一项/多一项错值都拒绝，绝不带着错误的 home/registry/lifecycle
    继续起 World 或发请求。
    """

    expected = _world_isolation_env(_require_instance_world_home(root))
    mismatch = sorted(name for name, value in expected.items()
                      if str(os.environ.get(name) or "") != value)
    if mismatch:
        raise Refused(REASON_WORLD_ENV_MISMATCH, ",".join(mismatch))


_ISOLATION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_ISOLATION_STATES = frozenset({
    "CREATED", "AUTHORIZED", "SUBMITTING", "SUBMITTED", "APPLIED",
    "REJECTED", "STALE", "UNKNOWN", "ABORTED", "CANCELLED"})
_REPO_DEFAULT_LIFECYCLE = Path("./data/world/run/action_lifecycle.jsonl")


def _within_root(path_text: Optional[str], root: Path | str) -> bool:
    if not path_text:
        return False
    try:
        Path(path_text).resolve().relative_to(Path(str(root)).resolve())
        return True
    except (ValueError, OSError):
        return False


def _isolation_view(root: Path | str) -> dict[str, Any]:
    """phase 子进程**自己的** World 写面解析（只枚举，不输出全 env / 私文）。

    ``os.environ`` 在这里是子进程的环境——由 ``_phase_child_env`` 显式映射进来的那一份；
    不是父进程，也不是外部 shell。父进程的环境前后应始终原样。
    """

    root_path = Path(str(root))
    home = str(os.environ.get("CHIYO_WORLD_HOME")
               or os.environ.get("HERMES_HOME") or "").strip()
    registry = str(os.environ.get("WORLD_ACTION_AUTH_REGISTRY_DIR") or "").strip()
    lifecycle = str(os.environ.get("WORLD_ACTION_LIFECYCLE_PATH") or "").strip()
    if home and not registry:
        registry = str(Path(home) / "run")
    if home and not lifecycle:
        lifecycle = str(Path(home) / "run" / "action_lifecycle.jsonl")
    instance_home = _instance_world_home(root_path)
    view: dict[str, Any] = {
        "gate": os.environ.get("WORLD_BODY_INTENT_MUTATION_GATE"),
        "world_home": home or None,
        "instance_world_home": str(instance_home) if instance_home else None,
        "world_home_is_instance": bool(
            home and instance_home
            and Path(home).resolve() == instance_home.resolve()),
        "world_home_is_within_root": _within_root(home, root_path),
        "auth_registry_dir": registry or None,
        "registry_is_within_root": _within_root(registry, root_path),
        "lifecycle_path": lifecycle or None,
        "lifecycle_is_within_root": _within_root(lifecycle, root_path),
        # 只报路径本身，不读也不写仓库默认台账；零新增由父级前后快照比较。
        "repo_default_lifecycle_path": str(_REPO_DEFAULT_LIFECYCLE.resolve()),
    }
    return view


def _isolation_refresh(view: dict[str, Any]) -> dict[str, Any]:
    """跑完之后补本根 ledger 的存在与有界记录（只读本实例根）。"""

    path_text = str(view.get("lifecycle_path") or "")
    path = Path(path_text) if path_text else None
    view["lifecycle_exists"] = bool(path and path.exists())
    view["lifecycle_records"] = _lifecycle_safe_records(path) if path else []
    return view


def _lifecycle_safe_records(path: Path, limit: int = 40) -> list[dict[str, Any]]:
    """只取固定枚举/已知 ID 形状，不保留 detail/target 原文。"""

    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line_no, line in enumerate(handle):
                if line_no >= 200:
                    break
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(row, Mapping):
                    continue
                state = str(row.get("state") or "")
                execution_id = str(row.get("execution_id") or "")
                verb = str(row.get("verb") or "")
                rows.append({
                    "state": state if state in _ISOLATION_STATES else None,
                    "execution_id": (execution_id
                                     if _ISOLATION_TOKEN_RE.match(execution_id) else None),
                    "verb": verb if _ISOLATION_TOKEN_RE.match(verb) else None,
                })
    except OSError:
        return []
    return rows[-limit:]


def _run_phase_process(phase: str, root: Path | str, payload: Mapping[str, Any],
                       *, timeout: float) -> tuple[int, dict[str, Any], str]:
    config_path = _experiment_dir(root) / ("g1-config-%s.json" % phase)
    # 子进程用 __file__ 重新导入本模块：身份必须随 config 传下去，否则它会退回默认 a。
    # gate 也一样：config 里写本批固定值，子进程 env 映射也只放同一个值。
    profile = _identity()
    _write_json(config_path, dict(payload, root=str(root), batch=profile.key,
                                  run_id=profile.run_id, domain_id=profile.domain_id,
                                  deadline=profile.deadline.isoformat()))
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "_phase",
         "--phase", phase, "--config", str(config_path)],
        capture_output=True, text=True, timeout=timeout,
        env=_phase_child_env(profile, root, offline=bool(payload.get("offline"))))
    if completed.returncode != 0:
        tail = completed.stderr[-8000:]
        (_experiment_dir(root) / (STDERR_FILENAME % phase)).write_text(
            tail, encoding="utf-8", errors="replace")
        last = [line for line in completed.stderr.strip().splitlines() if line.strip()]
        sys.stdout.write("  %s exited %d; stderr tail: %s\n"
                         % (phase, completed.returncode,
                            (last[-1][:240] if last else "")))
    report = _read_json(_experiment_dir(root) / (PHASE_FILENAME % phase))
    return completed.returncode, report, completed.stderr[-8000:]


def start_run(root: Path | str, *, expected_root: Optional[Path | str] = None,
              execute_approved: bool = False, offline: bool = False,
              capture_candidates: bool = False) -> dict[str, Any]:
    profile = _identity()
    root_path = Path(str(root)).expanduser()
    expected = Path(str(expected_root)) if expected_root is not None else profile.root
    refusal = _check_root(root_path, expected=expected)
    if refusal is not None:
        return refusal
    clock_refusal = _offline_clock_refusal(root_path, offline=bool(offline))
    if clock_refusal is not None:
        return clock_refusal
    if not offline and not _explicit_model_base_url(os.environ):
        # 真实 run 缺地址：在写 started/窗口或发任何请求之前拒绝。
        return {"ok": False, "reason_code": REASON_NO_MODEL_CONFIG,
                "detail": "NYA02_MODEL_BASE_URL is not set for a real batch"}
    prereg = _read_json(_experiment_dir(root_path) / PREREG_FILENAME)
    if not prereg:
        return {"ok": False, "reason_code": REASON_NOT_PREPARED, "root": str(root_path)}
    # 开跑前只读核对 prereg/batch/窗口身份与所选批次一致；错批/缺身份直接拒绝。
    identity_refusal = _verify_instance_identity(root_path)
    if identity_refusal is not None:
        return identity_refusal
    if profile.key in (E_PROFILE.key, F_PROFILE.key):
        # 路径守卫必须在任何服务/请求/窗口之前：坏 home 不继承父环境，也不写 started。
        try:
            _require_instance_world_home(root_path)
        except Refused as exc:
            return {"ok": False, "reason_code": exc.reason_code,
                    "detail": exc.detail[:200]}
    if not execute_approved:
        return {"ok": False, "reason_code": REASON_EXECUTE_FLAG_MISSING,
                "detail": "run 只在显式 --execute-approved 下执行，而且只能用一次"}
    # 还没开跑时没有窗口文件；这里只核**所选批次**的绝对截止。窗口写下去之后一律按窗口判。
    if _wall_now() >= profile.deadline:
        return {"ok": False, "reason_code": REASON_DEADLINE_PASSED,
                "deadline": profile.deadline.isoformat()}
    if _window_path(root_path).exists() and _deadline_passed(root_path):
        return {"ok": False, "reason_code": REASON_DEADLINE_PASSED,
                "deadline": profile.deadline.isoformat()}
    batch = _read_json(_experiment_dir(root_path) / BATCH_FILENAME)
    if (batch.get("run") or {}).get("started_at"):
        return {"ok": False, "reason_code": REASON_ALREADY_STARTED,
                "started_at": (batch.get("run") or {}).get("started_at")}
    if str(batch.get("state") or "") != "prepared":
        return {"ok": False, "reason_code": REASON_NOT_PREPARED,
                "state": batch.get("state")}
    if _window_path(root_path).exists():
        # 窗口已经写过（可能在上次 started 落盘前崩了）：同样不许重开，避免未知是否发过请求。
        return {"ok": False, "reason_code": REASON_ALREADY_STARTED,
                "detail": "the run window already exists"}

    state_root = str(prereg.get("state_root") or "")
    budget = _budget(state_root)
    try:
        budget.ensure_window(allow_new=False)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason_code": REASON_LEDGER_UNREADABLE,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    if _ledger_calls(budget) != 0:
        return {"ok": False, "reason_code": REASON_LEDGER_NOT_ZERO,
                "ledger": _ledger_view(budget)}

    natural = _is_natural(profile)
    if not offline:
        env = _runtime_env(root_path, offline=False)
        model = _verify_model_config(env)
        if not model.get("ok"):
            return {"ok": False, **model}
    if natural:
        # 开跑前核对：本进程解析出的 gate 必须就是该批自己的固定值，不是父 shell 继承值。
        # 这道检查在写窗口之前，拒绝不会消耗唯一一次运行窗口。
        try:
            gate_env = _runtime_env(root_path, offline=bool(offline))
        except Refused as exc:
            return {"ok": False, "reason_code": exc.reason_code, "detail": exc.detail[:200]}
        expected_gate = _world_intent_gate(profile)
        actual_gate = str(gate_env.get("WORLD_BODY_INTENT_MUTATION_GATE") or "")
        if actual_gate != str(expected_gate or ""):
            return {"ok": False, "reason_code": REASON_WORLD_GATE_MISMATCH,
                    "expected": expected_gate, "actual": actual_gate or None}

    # started 必须在任何请求之前持久写；窗口也只写这一次，重开不延长。
    window = _write_window(root_path)
    batch["run"] = {"started_at": _iso(), "pid": os.getpid(), "status": "running",
                    "offline": bool(offline)}
    batch["state"] = "running"
    _write_json(_experiment_dir(root_path) / BATCH_FILENAME, batch, private=True)

    report: dict[str, Any] = {"ok": True, "root": str(root_path), "batch": profile.key,
                              "run_id": profile.run_id,
                              "offline": bool(offline), "started_at": batch["run"]["started_at"],
                              "window": window, "coverage": {}, "stop_reason": "",
                              "candidate_trace": {"enabled": bool(capture_candidates)},
                              "p1": None, "p2": None}
    if natural:
        report["natural"] = None
        report["observation"] = None
    try:
        if natural:
            # d/e 只有一个自然 phase：没有 P1 成果门、没有暂停/重启、没有第二次运行。
            remaining = max(60.0, float(window["deadline_monotonic"]) - time.monotonic() + 120.0)
            code_n, phase, _stderr = _run_phase_process(
                PHASE_NATURAL, root_path,
                {"offline": bool(offline),
                 "capture_candidates": bool(capture_candidates),
                 "world_gate": _world_intent_gate(profile),
                 "plan": (["START", "NO_ACTION"] if offline else [])},
                timeout=remaining)
            report["natural"] = _safe_phase_view(phase)
            report["observation"] = _d_trace_observation(root_path)
            report["stop_reason"] = str(phase.get("stop_reason")
                                        or (REASON_PHASE_FAILED if code_n else REASON_RUN_FINISHED))
        else:
            remaining = max(60.0, float(window["deadline_monotonic"]) - time.monotonic() + 120.0)
            code1, p1, _stderr = _run_phase_process(
                "p1", root_path,
                {"offline": bool(offline), "plan": ["START"],
                 "capture_candidates": bool(capture_candidates)},
                timeout=remaining)
            report["p1"] = _safe_phase_view(p1)
            if code1 != 0 or not p1.get("artifact_a") or not p1.get("write_confirmed"):
                report["stop_reason"] = str(p1.get("stop_reason") or REASON_PHASE_FAILED)
            else:
                remaining = max(60.0, float(window["deadline_monotonic"]) - time.monotonic() + 120.0)
                code2, p2, _stderr = _run_phase_process(
                    "p2", root_path,
                    {"offline": bool(offline), "artifact_a": p1.get("artifact_a"),
                     "activity_ref": p1.get("activity_ref"),
                     "p1_confirmed": True,
                     "capture_candidates": bool(capture_candidates),
                     "plan": ["CONTINUE", "CONTINUE", "CONTINUE", "NO_ACTION", "CONTINUE"]},
                    timeout=remaining)
                report["p2"] = _safe_phase_view(p2)
                report["coverage"] = dict(p2.get("coverage") or {})
                report["stop_reason"] = str(p2.get("stop_reason")
                                            or (REASON_PHASE_FAILED if code2 else REASON_RUN_FINISHED))
                report["injections"] = dict(p2.get("injections") or {})
        report["ledger"] = _ledger_view(budget)
    except subprocess.TimeoutExpired:
        report["stop_reason"] = REASON_WINDOW_ELAPSED
    except Exception as exc:  # noqa: BLE001 - the run's verdict is reported, not hidden
        report["stop_reason"] = REASON_UNEXPECTED
        report["detail"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    finally:
        if natural:
            # 不套 G1 的覆盖结论：只区分「真的出现过含 PAUSE 的模型选择机会」与
            # 「没到那个决策点」。两者都不判文案好坏。
            observed = (report.get("observation") or {}).get("pause_choice_opportunity")
            report["result"] = "OBSERVED" if observed is True else "NOT_COVERED"
        else:
            report["result"] = ("COVERED" if (report.get("coverage") or {}).get("g1_chain_complete")
                                else "NOT_COVERED")
        batch = _read_json(_experiment_dir(root_path) / BATCH_FILENAME)
        batch.setdefault("run", {})["status"] = "finished"
        batch["coverage"] = report.get("coverage") or {}
        batch["result"] = report["result"]
        batch["stop"] = {"at": _iso(), "reason_code": report.get("stop_reason")}
        batch["state"] = "finished"
        _write_json(_experiment_dir(root_path) / BATCH_FILENAME, batch, private=True)
        _write_json(_experiment_dir(root_path) / RUN_REPORT_FILENAME, report, private=True)
    return report


# ---------------------------------------------------------------------------------------------
# audit：只读安全字段
# ---------------------------------------------------------------------------------------------


def audit(root: Path | str, *, expected_root: Optional[Path | str] = None) -> dict[str, Any]:
    profile = _identity()
    root_path = Path(str(root)).expanduser()
    expected = Path(str(expected_root)) if expected_root is not None else profile.root
    refusal = _check_root(root_path, expected=expected)
    if refusal is not None:
        return refusal
    prereg = _read_json(_experiment_dir(root_path) / PREREG_FILENAME)
    if not prereg:
        return {"ok": False, "reason_code": REASON_NOT_PREPARED, "root": str(root_path)}
    identity_refusal = _verify_instance_identity(root_path)
    if identity_refusal is not None:
        return identity_refusal
    batch = _read_json(_experiment_dir(root_path) / BATCH_FILENAME)
    report = _read_json(_experiment_dir(root_path) / RUN_REPORT_FILENAME)
    window = _window_view(root_path)
    ledger = None
    try:
        ledger = _ledger_view(_budget(str(prereg.get("state_root") or "")))
    except Exception:  # noqa: BLE001
        ledger = {"state": "UNREADABLE"}
    phases = {}
    for phase in (("natural",) if _is_natural(profile) else ("p1", "p2")):
        row = _read_json(_experiment_dir(root_path) / (PHASE_FILENAME % phase))
        phases[phase] = _safe_phase_view(row) if row else None
    payload = {
        "ok": True, "root": str(root_path), "batch": profile.key,
        "run_id": profile.run_id, "domain_id": profile.domain_id,
        "deadline": profile.deadline.isoformat(),
        "state": batch.get("state"), "prepared_at": batch.get("prepared_at"),
        "started_at": (batch.get("run") or {}).get("started_at"),
        "result": report.get("result") or batch.get("result"),
        "stopped": batch.get("stop"), "ledger": ledger, "window": window,
        "caps": prereg.get("caps"), "model_params": prereg.get("model_params"),
        "operator_material": prereg.get("operator_material"),
        "operator_control": prereg.get("operator_control"),
        "coverage": (report.get("coverage") or batch.get("coverage") or {}),
        "stop_reason": report.get("stop_reason"),
        "phases": phases,
        "injections": report.get("injections"),
    }
    if _is_natural(profile):
        # 只有自然批多这两项；a/b/c 的 audit 字段与原来完全一致。
        payload["observation"] = report.get("observation")
        payload["world_intent_gate"] = prereg.get("world_intent_gate",
                                                  _world_intent_gate(profile))
    elif profile.key == F_PROFILE.key:
        # f：gate 固定 off，服务未启动；不复制自然批的 observation 语义。
        payload["world_intent_gate"] = prereg.get("world_intent_gate",
                                                  _world_intent_gate(profile))
    return payload


# ---------------------------------------------------------------------------------------------
# offline-check
# ---------------------------------------------------------------------------------------------

_OFFLINE_CHECKS: list[tuple[str, bool, Any]] = []


def _check(name: str, condition: Any, detail: Any = None) -> None:
    _OFFLINE_CHECKS.append((name, bool(condition), detail))
    print("CHECK %-72s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def _run_unit_tests() -> dict[str, Any]:
    try:
        suite = unittest.TestLoader().discover(
            start_dir=str(REPO_ROOT / "tests"), pattern="test_g1_real_adoption.py")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    return {"ok": bool(result.wasSuccessful()), "run": int(result.testsRun),
            "failures": len(result.failures), "errors": len(result.errors)}


def _cross_pid_cap(state_root: Path, *, batch: str = DEFAULT_BATCH) -> dict[str, Any]:
    """两个独立进程共用同一本持久账本：12 + 8 用满 20，第 21 次在 reserve 处被拒。"""

    script = str(Path(__file__).resolve())
    first = subprocess.run([sys.executable, "-B", script, "_reserve", "--batch", batch,
                            "--state-root", str(state_root), "--count", "12"],
                           capture_output=True, text=True)
    second = subprocess.run([sys.executable, "-B", script, "_reserve", "--batch", batch,
                             "--state-root", str(state_root), "--count", "8"],
                            capture_output=True, text=True)
    budget = _budget(state_root)
    third = "reserved"
    try:
        budget.reserve("decision")
        third = "reserved"
    except Exception as exc:  # noqa: BLE001 - the refusal reason is the evidence
        third = type(exc).__name__
    return {"first_rc": first.returncode, "second_rc": second.returncode,
            "first_out": _reserve_out(first.stdout), "second_out": _reserve_out(second.stdout),
            "third": third, "calls": _ledger_calls(budget),
            "run_id": _identity().run_id, "batch": batch}


def _reserve_out(text: str) -> Any:
    for line in reversed((text or "").strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return text[-200:]


def _offline_gate_checks(scratch: Path) -> dict[str, Any]:
    """每次发送前的截止门 + 供应商失败后的整批停发。"""

    deadline_root = scratch / "deadline-case"
    # 用本进程自己的"现在"（离线夹具里就是参考时刻）造一个已经过期的窗口，
    # 证明发送前的截止门真的会关；不是拿真实墙钟和夹具时刻混着比。
    _now = _wall_now()
    _write_json(_window_path(deadline_root), {
        "schema": "g1.window.v1", "run_id": _identity().run_id, "window_seconds": 1800,
        "started_monotonic": 0.0, "deadline_monotonic": 0.0,
        "started_wall": _iso(_now - timedelta(minutes=30)),
        "deadline_wall": _iso(_now - timedelta(seconds=5)),
    }, private=True)
    deadline_calls: list[str] = []
    failure_calls: list[str] = []

    class _Inner:
        def invoke(self, request, *, schema_retry_index: int = 0):
            deadline_calls.append("sent")
            return SimpleNamespace(failure_class=None)

    class _Failing:
        def invoke(self, request, *, schema_retry_index: int = 0):
            failure_calls.append("failed")
            return SimpleNamespace(failure_class="MODEL_TIMEOUT")

    latch = SendLatch(lambda: _deadline_passed(deadline_root))
    gate = G1CognitionGate(_Inner(), latch=latch, budget=_budget(scratch / "budget-deadline"))
    deadline_raised = ""
    try:
        gate.invoke(object())
    except GateClosed as exc:
        deadline_raised = exc.reason_code
    deadline = {"raised": deadline_raised, "sends": len(deadline_calls)}

    fail_root = scratch / "failure-case"
    _write_json(_window_path(fail_root), {
        "schema": "g1.window.v1", "run_id": _identity().run_id, "window_seconds": 1800,
        "started_monotonic": time.monotonic(),
        "deadline_monotonic": time.monotonic() + 1800,
        "started_wall": _iso(_wall_now()),
        "deadline_wall": _iso(_wall_now() + timedelta(seconds=1800)),
    }, private=True)
    fail_latch = SendLatch(lambda: _deadline_passed(fail_root))
    fail_gate = G1CognitionGate(_Failing(), latch=fail_latch,
                                budget=_budget(scratch / "budget-failure"))
    first = fail_gate.invoke(object())
    second = ""
    try:
        fail_gate.invoke(object())
    except GateClosed as exc:
        second = exc.reason_code
    failure = {"first_failure": getattr(first, "failure_class", None),
               "latch_closed": fail_latch.closed, "second": second,
               "inner_calls": len(failure_calls)}

    # -- 内容口抛异常：必须停整批，后续发送连预留都不发生 --------------------------
    content_root = scratch / "content-exception"
    _live_window(content_root)
    content_calls: list[str] = []

    class _RaisingContent:
        def complete(self, *args: Any, **kwargs: Any):
            content_calls.append("sent")
            raise RuntimeError("offline content boom")

    content_latch = SendLatch(lambda: _deadline_passed(content_root))
    content_gate = G1ContentGate(_RaisingContent(), latch=content_latch,
                                 budget=_budget(scratch / "budget-content"))
    content_raised = ""
    try:
        content_gate.complete()
    except RuntimeError:
        content_raised = "RuntimeError"
    content_second = ""
    try:
        content_gate.complete()
    except GateClosed as exc:
        content_second = exc.reason_code
    content_exception = {"raised": content_raised, "latch_closed": content_latch.closed,
                         "second": content_second, "inner_calls": len(content_calls)}

    # -- P1 落盘的失败对 P2 的新锁同样生效（不因换进程复活） ------------------------
    cross_root = scratch / "cross-phase-stop"
    _live_window(cross_root)
    _record_batch_stop(cross_root, REASON_PROVIDER_FAILURE)
    cross_latch = SendLatch(lambda: _deadline_passed(cross_root))
    cross_stop = _loop_stop(cross_root, _budget(scratch / "budget-cross"), cross_latch)
    durable_stop = {"recorded": _durable_stop(cross_root), "stop": cross_stop,
                    "latch_closed": cross_latch.closed}

    state = STATE_AWAIT_CONTINUE
    injector = SimpleNamespace(arm_unknown=False, arm_concurrent=False, unknown_fired=0,
                               concurrent_fired=0)
    non_target = _apply_round(state, {"llm_calls": 1, "decision": "WAIT",
                                      "reason_codes": ["WAIT_FOR_QUIET"]},
                              versions_before=1, versions_after=1, injector=injector)
    negatives = _offline_state_negatives()
    return {"deadline": deadline, "failure": failure,
            "content_exception": content_exception, "durable_stop": durable_stop,
            "non_target_state": non_target,
            "non_target_decision": "WAIT" if non_target == state else None,
            "negatives": negatives}


def _live_window(root: Path | str) -> None:
    """写一个还没到期的窗口（离线负控用，不据此声称真实等待）。"""

    _write_json(_window_path(root), {
        "schema": "g1.window.v1", "run_id": _identity().run_id, "window_seconds": 1800,
        "started_monotonic": time.monotonic(),
        "deadline_monotonic": time.monotonic() + 1800,
        "started_wall": _iso(_wall_now()),
        "deadline_wall": _iso(_wall_now() + timedelta(seconds=1800)),
    }, private=True)


def _offline_state_negatives() -> dict[str, str]:
    """状态机的反例：证据不对时一步都不许推进。"""

    def injector(**kwargs: Any) -> SimpleNamespace:
        base = {"arm_unknown": False, "arm_concurrent": False,
                "unknown_fired": 0, "concurrent_fired": 0}
        base.update(kwargs)
        return SimpleNamespace(**base)

    continue_wake = {"llm_calls": 1, "decision": "CONTINUE", "action_status": "SUCCEEDED",
                     "outcome": "ACTED"}
    different_activity = _apply_round(
        STATE_AWAIT_CONTINUE, continue_wake, versions_before=1, versions_after=2,
        injector=injector(), activity_matches=False, same_work=True, round_has_receipt=True)
    other_work = _apply_round(
        STATE_AWAIT_CONTINUE, continue_wake, versions_before=1, versions_after=2,
        injector=injector(), activity_matches=True, same_work=False, round_has_receipt=True)
    unreconciled = _apply_round(
        STATE_AWAIT_UNKNOWN, {"llm_calls": 1, "decision": "CONTINUE",
                              "action_status": "UNKNOWN"},
        versions_before=2, versions_after=2, injector=injector(unknown_fired=1),
        activity_matches=True, same_work=True, round_has_receipt=True, reconcile_ok=False)
    non_conflict = _apply_round(
        STATE_AWAIT_CAS, {"llm_calls": 1, "decision": "CONTINUE", "action_status": "FAILED"},
        versions_before=2, versions_after=2, injector=injector(concurrent_fired=1),
        activity_matches=True, same_work=True, round_has_receipt=True,
        cas_evidence={"injected": True, "applied": True, "head_advanced": True,
                      "owner_conflict": False})
    not_applied = _apply_round(
        STATE_AWAIT_CAS, {"llm_calls": 1, "decision": "CONTINUE", "action_status": "FAILED"},
        versions_before=2, versions_after=2, injector=injector(concurrent_fired=1),
        activity_matches=True, same_work=True, round_has_receipt=True,
        cas_evidence={"injected": True, "applied": False, "head_advanced": False,
                      "owner_conflict": True})
    return {"different_activity": different_activity, "other_work": other_work,
            "unreconciled": unreconciled, "non_conflict_failed": non_conflict,
            "concurrent_not_applied": not_applied}


def _batch_identity_checks(scratch: Path) -> dict[str, Any]:
    """批次身份的负控：错批/错 root/缺身份/未知批/旧 authority 全部失败关闭。"""

    profile = _identity()
    other_key = next(key for key in BATCH_PROFILES if key != profile.key)
    other = BATCH_PROFILES[other_key]
    rows: dict[str, Any] = {"active": profile.key, "run_id": profile.run_id,
                            "root": str(profile.root), "domain_id": profile.domain_id,
                            "expected_history": profile.expected_history}
    rows["a_mapping"] = {"root": str(BATCH_PROFILES["a"].root),
                         "run_id": BATCH_PROFILES["a"].run_id,
                         "domain_id": BATCH_PROFILES["a"].domain_id,
                         "expected_history": BATCH_PROFILES["a"].expected_history}
    try:
        _select_batch("z")
        rows["unknown_batch"] = "accepted"
    except Refused as exc:
        rows["unknown_batch"] = exc.reason_code
    # 另一批的真实根路径：纯字符串判定，不触碰任何 /root 目录。
    rows["wrong_root"] = (_check_root(other.root) or {}).get("reason_code")
    # 落盘身份与所选批次不一致的 prereg：audit 必须拒绝，不能只看 root 路径。
    wrong_root = scratch / ("wrong-batch-%s" % profile.key)
    _write_json(_experiment_dir(wrong_root) / PREREG_FILENAME, {
        "schema": "g1.real.prereg.v1", "batch": other.key, "run_id": other.run_id,
        "domain_id": other.domain_id, "root": str(wrong_root),
        "state_root": str(wrong_root / "one-day"),
    }, private=True)
    rows["wrong_prereg"] = audit(wrong_root, expected_root=wrong_root)
    # prereg 缺身份（没有 batch/run_id/域）：同样拒绝，不能默认成当前批。
    missing_root = scratch / ("missing-identity-%s" % profile.key)
    _write_json(_experiment_dir(missing_root) / PREREG_FILENAME, {
        "schema": "g1.real.prereg.v1", "root": str(missing_root),
        "state_root": str(missing_root / "one-day"),
    }, private=True)
    rows["missing_identity"] = audit(missing_root, expected_root=missing_root)
    # 预存 authority：prepare 必须在写入前拒绝，且不创建根、不删除重建。
    fresh_root = scratch / ("authority-taken-%s" % profile.key)
    taken_dir = fresh_root.parent / ("%s-memory-authority" % fresh_root.name)
    taken_dir.mkdir(parents=True, exist_ok=True)
    (taken_dir / "authority.db").write_text("pre-existing authority", encoding="utf-8")
    refused = prepare(fresh_root, expected_root=fresh_root, offline=True)
    rows["authority_taken"] = {"ok": refused.get("ok"),
                               "reason_code": refused.get("reason_code"),
                               "root_created": fresh_root.exists()}
    return rows


def offline_check() -> dict[str, Any]:
    """离线验收入口：显式冻结参考时刻；只在本次 mkdtemp scratch 根内生效。"""

    with offline_clock():
        return _offline_check_body()


def _offline_check_body() -> dict[str, Any]:
    tests = {"ok": True, "run": 0, "failures": 0, "errors": 0}
    checks: dict[str, Any] = {}
    scratch = Path(tempfile.mkdtemp(prefix=OFFLINE_PREFIX))
    root = scratch / "instance-root"  # 必须是新路径：prepare 只建一次，从不覆盖
    print("scratch root:", scratch)
    with _http_forbidden():
        # -- 1. 本入口自己的单元用例 ------------------------------------------------
        tests = _run_unit_tests()
        # -- 2. prepare / run 开关 --------------------------------------------------
        prepared = prepare(root, expected_root=root, offline=True)
        checks["prepare"] = {key: prepared.get(key) for key in
                             ("ok", "reason_code", "detail", "state_root", "model_called",
                              "request_sent", "batch", "run_id", "domain_id")}
        checks["prereg_identity"] = dict(_read_json(
            _experiment_dir(root) / PREREG_FILENAME).get("caps") or {})
        gate_off = start_run(root, expected_root=root, execute_approved=False,
                             offline=True)
        checks["run_requires_flag"] = {key: gate_off.get(key)
                                       for key in ("ok", "reason_code")}
        # -- 2b. 批次身份负控 -------------------------------------------------------
        checks["batch_identity"] = _batch_identity_checks(scratch)
        # -- 3. 共享跨 PID 额度 -----------------------------------------------------
        cap_root = scratch / "cap-state"
        cap_root.mkdir(parents=True, exist_ok=True)
        checks["cross_pid_cap"] = _cross_pid_cap(cap_root, batch=_identity().key)
        # -- 4. 每次发送前的门 ------------------------------------------------------
        checks["send_gates"] = _offline_gate_checks(scratch)
        # -- 5. 真实 owner/runtime 上的完整链 --------------------------------------
        chain = start_run(root, expected_root=root, execute_approved=True,
                          offline=True)
        checks["chain"] = {"ok": chain.get("ok"), "stop_reason": chain.get("stop_reason"),
                           "batch": chain.get("batch"), "run_id": chain.get("run_id"),
                           "coverage": chain.get("coverage"),
                           "injections": chain.get("injections"),
                           "p1": chain.get("p1"), "p2": chain.get("p2"),
                           "ledger": chain.get("ledger")}
        checks["child_identity"] = {
            phase: {key: _read_json(_experiment_dir(root) / ("g1-config-%s.json" % phase))
                    .get(key) for key in ("batch", "run_id", "domain_id")}
            for phase in ("p1", "p2")}
        # -- 6. 重开拒启动 ----------------------------------------------------------
        again = start_run(root, expected_root=root, execute_approved=True,
                          offline=True)
        checks["no_restart"] = {key: again.get(key) for key in ("ok", "reason_code")}
        # -- 7. audit 安全字段 ------------------------------------------------------
        audited = audit(root, expected_root=root)
        checks["audit"] = {"ok": audited.get("ok"), "stop_reason": audited.get("stop_reason")}
        # -- 8. 安全扫描（报告里不得出现正文/密钥/提示词） --------------------------
        blob = json.dumps(audited, ensure_ascii=False, default=str)
        forbidden = [MATERIAL_TEXT, CONTROL_TEXT, "末尾补了一句中性的练习说明。",
                     "offline-not-a-key", "sk-", "api_key", "prompt", "completion"]
        hits = [token for token in forbidden if token and token in blob]
        checks["audit_safe"] = {"hits": hits}

    p1 = checks["chain"].get("p1") or {}
    p2 = checks["chain"].get("p2") or {}
    coverage = checks["chain"].get("coverage") or {}
    injections = checks["chain"].get("injections") or {}
    close2 = (p2.get("close_supply") or {})
    gate = checks["send_gates"]

    _check("offline unit tests pass", tests.get("ok") is True, tests)
    _check("prepare ran with zero model calls / zero requests",
           checks["prepare"].get("ok") is True
           and checks["prepare"].get("model_called") is False
           and checks["prepare"].get("request_sent") is False, checks["prepare"])
    _check("run refuses without --execute-approved",
           checks["run_requires_flag"].get("reason_code") == REASON_EXECUTE_FLAG_MISSING,
           checks["run_requires_flag"])
    identity = checks["batch_identity"]
    _check("the selected batch is explicit and the default a mapping is unchanged",
           identity["active"] == _identity().key
           and identity["a_mapping"]["root"] == "/root/nyairo-g1-real-20261030"
           and identity["a_mapping"]["run_id"] == "run:g1-real-20261030"
           and identity["a_mapping"]["domain_id"] == "g1:real:20261030"
           and identity["a_mapping"]["expected_history"] == 98, identity)
    _check("unknown batches are refused",
           identity["unknown_batch"] == REASON_UNKNOWN_BATCH, identity["unknown_batch"])
    _check("the other batch's root is refused as a batch identity mismatch",
           identity["wrong_root"] == REASON_BATCH_IDENTITY_MISMATCH, identity["wrong_root"])
    _check("a prereg written for the other batch is refused",
           identity["wrong_prereg"].get("reason_code") == REASON_BATCH_IDENTITY_MISMATCH,
           identity["wrong_prereg"])
    _check("a prereg without batch identity is refused",
           identity["missing_identity"].get("reason_code") == REASON_BATCH_IDENTITY_MISMATCH,
           identity["missing_identity"])
    _check("prepare refuses a pre-existing authority before writing anything",
           identity["authority_taken"].get("ok") is False
           and identity["authority_taken"].get("reason_code") == REASON_AUTHORITY_NOT_FRESH
           and identity["authority_taken"].get("root_created") is False,
           identity["authority_taken"])
    _check("prepare records the selected batch identity and its history baseline",
           checks["prepare"].get("batch") == _identity().key
           and checks["prepare"].get("run_id") == _identity().run_id
           and checks["prepare"].get("domain_id") == _identity().domain_id
           and int(checks["prereg_identity"].get("expected_history") or 0)
           == _identity().expected_history, checks["prepare"])
    cap = checks["cross_pid_cap"]
    _check("two independent processes share one persistent 20-request cap",
           cap["first_rc"] == 0 and cap["second_rc"] == 0
           and (cap["first_out"] or {}).get("reserved") == 12
           and (cap["second_out"] or {}).get("reserved") == 8
           and cap["third"] != "reserved" and cap["calls"] == REQUEST_CAP
           and cap.get("run_id") == _identity().run_id, cap)
    _check("every send checks the shared deadline first (0 sends after it passes)",
           gate["deadline"].get("raised") == REASON_DEADLINE_PASSED
           and gate["deadline"].get("sends") == 0, gate["deadline"])
    _check("a provider failure closes the batch latch and the next send never happens",
           gate["failure"].get("latch_closed") is True
           and gate["failure"].get("second") == REASON_PROVIDER_FAILURE
           and gate["failure"].get("inner_calls") == 1, gate["failure"])
    _check("a content.complete exception stops the batch before any second send",
           gate["content_exception"].get("raised") == "RuntimeError"
           and gate["content_exception"].get("latch_closed") is True
           and gate["content_exception"].get("second") == REASON_PROVIDER_FAILURE
           and gate["content_exception"].get("inner_calls") == 1,
           gate["content_exception"])
    _check("a stop recorded by P1 also blocks P2's fresh latch",
           gate["durable_stop"].get("recorded") == REASON_PROVIDER_FAILURE
           and gate["durable_stop"].get("stop") == REASON_PROVIDER_FAILURE
           and gate["durable_stop"].get("latch_closed") is True, gate["durable_stop"])
    _check("a non-target model decision is recorded as-is, never rewritten",
           gate.get("non_target_state") == STATE_AWAIT_CONTINUE
           and gate.get("non_target_decision") == "WAIT", gate)
    negatives = gate.get("negatives") or {}
    _check("a write on another Activity never advances the continuation",
           negatives.get("different_activity") == STATE_AWAIT_CONTINUE, negatives)
    _check("a write on another work never advances the continuation",
           negatives.get("other_work") == STATE_AWAIT_CONTINUE, negatives)
    _check("UNKNOWN without a confirmed reconcile never counts as reconciled",
           negatives.get("unreconciled") == STATE_AWAIT_UNKNOWN, negatives)
    _check("a non-CONFLICT FAILED refusal never counts as the CAS rejection",
           negatives.get("non_conflict_failed") == STATE_AWAIT_CAS, negatives)
    _check("an injected concurrent write that did not land never counts as CAS",
           negatives.get("concurrent_not_applied") == STATE_AWAIT_CAS, negatives)
    _check("P1 confirmed this round's own owner version on the work and its formal Activity",
           p1.get("write_confirmed") is True
           and p1.get("owner_version_created") is True
           and bool(p1.get("owner_version_id"))
           and _bare(p1.get("receipt_artifact")) == _bare(p1.get("artifact_a"))
           and bool(p1.get("receipt_activity"))
           and bool(p1.get("activity_ref"))
           and int((p1.get("versions_after_write") or 0)) >= 1,
           {"a": p1.get("artifact_a"), "activity": p1.get("activity_ref"),
            "owner": p1.get("owner_version_id"), "confirmed": p1.get("write_confirmed")})
    _check("P1 set the existing operator pause and stopped its own owner orderly",
           bool((p1.get("pause") or {}).get("paused"))
           and (p1.get("close_supply") or {}).get("exit_code") == 0
           and (p1.get("close_supply") or {}).get("socket_live_after") is False,
           {"pause": p1.get("pause"), "close": p1.get("close_supply")})
    _check("P2 ran in a different PID on the same root",
           p1.get("pid") and p2.get("pid") and p1.get("pid") != p2.get("pid"),
           [p1.get("pid"), p2.get("pid")])
    child = checks["child_identity"]
    _check("parent config and both children carry the selected batch identity",
           all((row or {}).get("batch") == _identity().key for row in child.values())
           and all((row or {}).get("run_id") == _identity().run_id
                   for row in child.values())
           and p1.get("batch") == _identity().key and p2.get("batch") == _identity().key
           and p1.get("run_id") == _identity().run_id
           and p2.get("run_id") == _identity().run_id,
           {"configs": child, "p1": p1.get("run_id"), "p2": p2.get("run_id")})
    _check("both children reserved against the same batch ledger",
           (checks["chain"].get("ledger") or {}).get("run_id") == _identity().run_id
           and int((checks["chain"].get("ledger") or {}).get("run_window_max") or 0)
           == REQUEST_CAP,
           {"ledger": checks["chain"].get("ledger"), "cap": checks["cross_pid_cap"]})
    _check("the paused round was zero-request and wrote nothing",
           (p2.get("paused_round") or {}).get("outcome") == "PAUSED_BY_OPERATOR"
           and int((p2.get("paused_round") or {}).get("llm_calls") or 0) == 0
           and p2.get("paused_round_zero_requests") is True, p2.get("paused_round"))
    _check("the model's own Decision continued the same Activity after the restart",
           coverage.get("continue_same_activity") is True,
           {"states": p2.get("states"), "final": p2.get("final_state")})
    _check("the dropped-receipt write stayed UNKNOWN and was reconciled by operation only",
           (p2.get("reconcile") or {}).get("result") == "CONFIRMED_SUCCEEDED"
           and (p2.get("reconcile") or {}).get("previous_status") == "UNKNOWN"
           and (p2.get("reconcile") or {}).get("evidence_verified") is True
           and int((p2.get("reconcile") or {}).get("evidence_count") or 0) >= 1
           and injections.get("unknown_fired") == 1
           and (p2.get("reconcile") or {}).get("versions_after") == p2.get("unknown_versions_after"),
           {"reconcile": p2.get("reconcile"), "injections": injections})
    concurrent = dict(injections.get("concurrent") or {})
    _check("the stale-head submission was refused by the owner's CAS (single injection)",
           coverage.get("cas_refused") is True
           and injections.get("concurrent_fired") == 1
           and concurrent.get("applied") is True
           and bool(concurrent.get("head_after"))
           and concurrent.get("head_after") != concurrent.get("head_before")
           and (p2.get("cas_owner") or {}).get("owner_status") == "CONFLICT",
           {"cas_owner": p2.get("cas_owner"), "injections": injections})
    _check("NO_ACTION was the model's own enum with no attached write",
           coverage.get("no_action_chosen_zero_write") is True,
           {"states": p2.get("states")})
    _check("after the quiet round the same work was continued once more",
           coverage.get("continue_after_quiet") is True, coverage)
    _check("the six-scenario chain is complete in the offline fixture",
           coverage.get("g1_chain_complete") is True, coverage)
    _check("the isolated batch never exceeded its own 20-request cap",
           int((checks["chain"].get("ledger") or {}).get("run_window_calls") or 0) <= REQUEST_CAP,
           checks["chain"].get("ledger"))
    _check("a second run on the same root is refused (started is durable)",
           checks["no_restart"].get("reason_code") == REASON_ALREADY_STARTED,
           checks["no_restart"])
    _check("P2 also released its lease and left no live owner listener behind",
           (p2.get("close_runtime") or {}).get("closed") is True
           and close2.get("exit_code") == 0
           and close2.get("socket_live_after") is False, close2)
    _check("audit only carries safe fields (no body, key, prompt or completion)",
           checks["audit"].get("ok") is True and not checks["audit_safe"].get("hits"),
           checks["audit_safe"])

    failed = [name for name, ok, _detail in _OFFLINE_CHECKS if not ok]
    print()
    print("scratch kept:", scratch)
    for name in failed:
        print("FAILED:", name)
    print("RESULT: %s" % ("FAIL" if failed or not tests.get("ok") else "PASS"))
    return {"ok": not failed and tests.get("ok") is True,
            "checks": [{"name": name, "ok": ok} for name, ok, _detail in _OFFLINE_CHECKS],
            "unit_tests": tests, "scratch_root": str(scratch), "failed": failed}


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _emit(payload: Mapping[str, Any], *, code: int) -> int:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, indent=1,
                                sort_keys=True, default=str) + "\n")
    sys.stdout.flush()
    return int(code)


def cmd_offline_check(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_real_adoption offline-check")
    parser.add_argument("--batch", default=DEFAULT_BATCH, choices=CLI_BATCH_CHOICES)
    args = parser.parse_args(list(argv))
    if args.batch in NATURAL_PROFILES:
        # 这套 8 段检查整体按 G1 的 prepare/p1/p2 写；d/e 没有那两个 phase，
        # 给它跑只会得到一串与它无关的 FAIL。直接拒绝，别把错用读成红。
        sys.stderr.write("offline-check does not apply to the non-G1 %s batch\n"
                         % args.batch)
        return EXIT_REFUSED
    with _use_batch(args.batch):
        result = offline_check()
    return EXIT_OK if result.get("ok") else EXIT_REFUSED


def cmd_prepare(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_real_adoption prepare")
    parser.add_argument("--root", required=True)
    parser.add_argument("--batch", default=DEFAULT_BATCH, choices=CLI_BATCH_CHOICES)
    args = parser.parse_args(list(argv))
    with _use_batch(args.batch):
        result = prepare(Path(args.root))
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_real_adoption run")
    parser.add_argument("--root", required=True)
    parser.add_argument("--batch", default=DEFAULT_BATCH, choices=CLI_BATCH_CHOICES)
    parser.add_argument("--execute-approved", action="store_true")
    # 实验观测，默认关闭：只记录脱敏候选 id/枚举/顺序，不改变候选、排序或模型输入。
    parser.add_argument("--capture-candidates", action="store_true")
    args = parser.parse_args(list(argv))
    with _use_batch(args.batch):
        result = start_run(Path(args.root), execute_approved=bool(args.execute_approved),
                           capture_candidates=bool(args.capture_candidates))
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_audit(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_real_adoption audit")
    parser.add_argument("--root", required=True)
    parser.add_argument("--batch", default=DEFAULT_BATCH, choices=CLI_BATCH_CHOICES)
    args = parser.parse_args(list(argv))
    with _use_batch(args.batch):
        result = audit(Path(args.root))
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_phase(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_real_adoption _phase")
    parser.add_argument("--phase", required=True, choices=("p1", "p2", PHASE_NATURAL))
    parser.add_argument("--config", required=True)
    args = parser.parse_args(list(argv))
    config = _read_json(args.config)
    # 子进程从 config 读回批次身份；缺身份/错身份一律拒绝，不能退回默认 a。
    try:
        profile = _select_batch(config.get("batch"))
    except Refused as exc:
        sys.stderr.write("phase refused: %s\n" % exc.reason_code)
        return EXIT_REFUSED
    with _use_batch(profile.key):
        # config 的 run/域/截止必须与所选批次一致；父级写的是 profile 的截止，
        # 子进程不接受另一个批或另一段窗口时间。
        if (str(config.get("run_id") or "") != profile.run_id
                or str(config.get("domain_id") or "") != profile.domain_id
                or str(config.get("deadline") or "") != profile.deadline.isoformat()):
            sys.stderr.write("phase refused: %s\n" % REASON_BATCH_IDENTITY_MISMATCH)
            return EXIT_REFUSED
        if _is_natural(profile):
            # 自然批的 gate 是闭集的一部分：config 里必须是本批自己的固定值。
            # 缺字段、写成别的批的值、或想把父进程继承的 canonical 传进 d 都不认。
            expected_gate = _world_intent_gate(profile)
            if str(config.get("world_gate") or "") != str(expected_gate or ""):
                sys.stderr.write("phase refused: %s\n" % REASON_WORLD_GATE_MISMATCH)
                return EXIT_REFUSED
        if args.phase == PHASE_NATURAL and not _is_natural(profile):
            # 自然 phase 只属于 d/e；a/b/c 的 G1 链不能被它替换。
            sys.stderr.write("phase refused: %s\n" % REASON_UNKNOWN_BATCH)
            return EXIT_REFUSED
        if args.phase != PHASE_NATURAL and _is_natural(profile):
            # d/e 没有 p1/p2：它们是单自然 phase，不套 G1 链。
            sys.stderr.write("phase refused: %s\n" % REASON_UNKNOWN_BATCH)
            return EXIT_REFUSED
        refusal = _verify_instance_identity(str(config.get("root") or ""))
        if refusal is not None:
            sys.stderr.write("phase refused: %s\n" % refusal.get("reason_code"))
            return EXIT_REFUSED
        if ((profile.key == E_PROFILE.key and args.phase == PHASE_NATURAL)
                or profile.key == F_PROFILE.key):
            # e/f：进入正式 phase 之前先校验自己的实际 env 与实例固定映射一致。
            # f 的 p1/p2 也必须逐项相等，不能把父 shell 的 world 写面带进来。
            try:
                _verify_isolated_child_env(str(config.get("root") or ""))
            except Refused as exc:
                sys.stderr.write("phase refused: %s\n" % exc.reason_code)
                return EXIT_REFUSED
        runner = {"p1": _phase_p1, "p2": _phase_p2, PHASE_NATURAL: _phase_natural}[args.phase]
        if (bool(config.get("offline"))
                and str(os.environ.get(OFFLINE_FIXTURE_ENV) or "") == "1"):
            # 三道一起才算离线夹具：父级实际离线调用路径放进子进程树的标记 +
            # config 自己声明 offline + 根确实是 scratch。缺一项都退回真实墙钟/拒绝。
            if not _is_offline_scratch_root(str(config.get("root") or "")):
                sys.stderr.write("phase refused: %s\n" % REASON_OFFLINE_CLOCK_REFUSED)
                return EXIT_REFUSED
            with offline_clock(profile):
                runner(config)
            return EXIT_OK
        runner(config)
    return EXIT_OK


def cmd_reserve(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_real_adoption _reserve")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--count", type=int, required=True)
    # 内部子命令：f 也要能被父级/测试用来核共享账本；旧公开 CLI 元组不动。
    parser.add_argument("--batch", default=DEFAULT_BATCH,
                        choices=CLI_BATCH_CHOICES + (F_PROFILE.key,))
    args = parser.parse_args(list(argv))
    with _use_batch(args.batch):
        Path(args.state_root).mkdir(parents=True, exist_ok=True)
        budget = _budget(args.state_root)
        budget.ensure_window(allow_new=True)
        reserved = 0
        refused = ""
        for _index in range(max(0, int(args.count))):
            try:
                budget.reserve("decision")
                reserved += 1
            except Exception as exc:  # noqa: BLE001 - the reason is the evidence
                refused = "%s: %s" % (type(exc).__name__, str(exc)[:120])
                break
        print(json.dumps({"reserved": reserved, "refused": refused,
                          "run_id": _identity().run_id,
                          "run_window_calls": _ledger_calls(budget)}, ensure_ascii=False))
    return EXIT_OK if reserved == int(args.count) else EXIT_REFUSED


COMMANDS = {
    "offline-check": cmd_offline_check,
    "prepare": cmd_prepare,
    "run": cmd_run,
    "audit": cmd_audit,
    "_phase": cmd_phase,
    "_reserve": cmd_reserve,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        sys.stderr.write(__doc__ or "")
        return EXIT_OK if arguments else EXIT_REFUSED
    handler = COMMANDS.get(arguments[0])
    if handler is None:
        sys.stderr.write("unknown command: %s\n" % (arguments[0],))
        return EXIT_REFUSED
    return int(handler(arguments[1:]))


if __name__ == "__main__":
    raise SystemExit(main())

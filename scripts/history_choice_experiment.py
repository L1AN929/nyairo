#!/usr/bin/env python3
"""历史影响选择实验：离线 harness（预登记 2026-10-29，本人已确认）。

这个文件只做三件事：把已经批准过的实验流程写成一个可执行的 CLI、把每一个真实请求
都挡在阶段额度与预登记参数之后、把结果落成不含正文的安全记录。它不新增业务能力、
不改任何 owner/AG-1/AG-2/AR-0/Memory 规则、不开 World 写闸门，也不替模型做选择。

四个子命令（与预登记一致）

    python -B scripts/history_choice_experiment.py offline-check
    python -B scripts/history_choice_experiment.py prepare --root /root/nyairo-histchoice-20261029
    python -B scripts/history_choice_experiment.py run --root <root> --execute-approved
    python -B scripts/history_choice_experiment.py audit --root <root>

边界（预登记 §3/§7/§8）

* 采集最多 8 次请求（决策+内容合计），影子 20 对=40 次，真实承接最多 8 次，
  诊断预留 4 次**默认不使用**；一次运行的总请求硬限 60，写在同一本持久请求账本里，
  重启/换 run 都不刷新。
* 影子样本只走「正式 cognition adapter + AG-1 原输出校验」的决策前边界：请求进真实
  模型，输出照常被 AG-1 校验，但影子服务用的是实验自己的 shadow store，不提交
  Decision、不采纳、不跑 writer/Action、不写 M0。
* 两臂只改历史文本字段（``prior_experiences[*].text``），refs/候选/合法集/上下文/参数
  全部冻结；请求发出前用正式 adapter 自己渲染 payload 并逐字段比对，任何历史之外的
  差异都拒绝发送（记 ``SECOND_VARIABLE_DIFFERS``）。
* 输出的所有 JSON 只含 reason code / count / bool / 不透明 ref，不含 prompt、响应正文、
  推理过程、环境变量或密钥。

本模块不访问旧实例、不删除数据、不外发消息、不提交推送部署、不改安全设置。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import shutil
import sys
import time
import unittest
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

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2

# ---------------------------------------------------------------------------------
# 预登记里的固定值（唯一来源；改这里等于改方案，必须先经本人确认）
# ---------------------------------------------------------------------------------

PREREG_ROOT = Path("/root/nyairo-histchoice-20261029")
RUN_NAME = "histchoice-20261029"
RUN_ID = "run:" + RUN_NAME
DOMAIN_ID = "histchoice:20261029"
DEADLINE = datetime(2026, 10, 30, 1, 0, 0, tzinfo=timezone(timedelta(hours=8)))

TOTAL_REQUEST_CAP = 60
STAGE_CAPS = {"acquisition": 8, "shadow": 40, "continuation": 8, "diagnostic": 4}
#: 预登记「每臂最多一次新决定 + 一次内容」的硬上限：每次承接回合前把持久账本的
#: run window ceiling 压到「本回合起点 + 2」，第 3 次发送在 reserve 处就被拒绝。
ARM_REQUEST_CAP = 2
DIAGNOSTIC_DEFAULT = "NOT_USED_BY_DESIGN"

MODEL_TEMPERATURE = 0.0
MODEL_STREAM = False
DECISION_OUTPUT_CAP = 4096
CONTENT_OUTPUT_CAP = 2048

WORK_TITLES = ("练习甲", "练习乙")
WORK_SEED_TEXT = "这是由 operator 提供的练习用中性材料，放在这里只是为了让两件作品都能被合法地继续。"
WORK_SEED_TEXT_2 = "这是由 operator 提供的练习用中性材料，放在这里只是为了让两件作品都能被合法地继续。"

PAIR_ORDER = ("BA", "AB", "AB", "AB", "AB", "BA", "AB", "BA", "AB", "AB",
              "BA", "BA", "BA", "AB", "AB", "BA", "BA", "AB", "BA", "BA")
PAIR_COUNT = 20

NEUTRAL_SENTENCE = ("这是一段与本次选择无关的中性占位说明，只用来保持字符长度与换行结构一致，"
                    "它不代表任何经历、偏好或要求。")

PREREG_FILENAME = "histchoice.json"
BATCH_FILENAME = "histchoice_batch.json"
EXPERIMENT_DIRNAME = "experiment"
SHADOW_STATE_DIRNAME = "shadow-state"
CAPTURE_STATE_DIRNAME = "capture-state"
BRANCH_DIRNAME = "branch-shadow-b"

RESERVED_ROOTS = (
    "/root/nyairo-day-20261022",
    "/root/nyairo-usable-20261023",
    "/root/nyairo-fastday",
)

REASON_ROOT_NOT_ABSOLUTE = "ROOT_NOT_ABSOLUTE"
REASON_ROOT_NOT_PREREGISTERED = "ROOT_NOT_PREREGISTERED"
REASON_ROOT_RESERVED = "ROOT_RESERVED_OLD_INSTANCE"
REASON_ROOT_NOT_FRESH = "ROOT_NOT_FRESH"
REASON_ALREADY_PREPARED = "ROOT_ALREADY_PREPARED"
REASON_PREPARE_FAILED = "PREPARE_FAILED"
REASON_WORK_CREATE_FAILED = "WORK_ARTIFACT_CREATE_FAILED"
REASON_MEMORY_BIND_FAILED = "MEMORY_BIND_FAILED"
REASON_LEDGER_NOT_ZERO = "LEDGER_NOT_ZERO_AFTER_PREPARE"
REASON_NOT_PREPARED = "EXPERIMENT_NOT_PREPARED"
REASON_EXECUTE_FLAG_MISSING = "RUN_REQUIRES_EXECUTE_APPROVED"
REASON_DEADLINE_PASSED = "PREREG_DEADLINE_PASSED"
REASON_ALREADY_STARTED = "RUN_ALREADY_STARTED"
REASON_STAGE_CAP = "STAGE_REQUEST_CAP"
REASON_TOTAL_CAP = "TOTAL_REQUEST_CAP"
REASON_LEDGER_UNREADABLE = "REQUEST_LEDGER_UNREADABLE"
REASON_LEDGER_DRIFT = "REQUEST_LEDGER_DRIFT"
REASON_NO_MODEL_CONFIG = "NO_REAL_MODEL_CONFIG"
REASON_PARAM_MISMATCH = "PREREG_MODEL_PARAM_MISMATCH"
REASON_SECOND_VARIABLE = "SECOND_VARIABLE_DIFFERS"
REASON_SCENARIO_NOT_IDENTIFIABLE = "SCENARIO_NOT_IDENTIFIABLE"
REASON_NO_EFFECTIVE_HISTORY = "NO_EFFECTIVE_HISTORY"
REASON_HISTORY_NOT_ADMITTED = "HISTORY_NOT_ADMITTED_AT_FREEZE"
REASON_ACQUISITION_STALLED = "ACQUISITION_STALLED_NO_REQUEST"
REASON_PROVIDER_FAILURE = "PROVIDER_OR_TRANSPORT_FAILURE"
REASON_BRANCH_ISOLATION = "BRANCH_ISOLATION_UNPROVEN"
REASON_SUPPLY_UNAVAILABLE = "SUPPLY_OWNER_UNAVAILABLE"
REASON_ASSEMBLY_NOT_READY = "ASSEMBLY_NOT_READY"
REASON_UNEXPECTED = "UNEXPECTED_ERROR"
#: 单个 attempt 之前必须同时过「发送前批次门 + 持久预留」；schema/网络层面的自动重试一律不批。
REASON_ATTEMPT_RETRY_REFUSED = "MODEL_ATTEMPT_RETRY_REFUSED"
#: 结构门要求的第二个可执行候选必须指向另一件作品（同件的 START/closeout 不算）。
REASON_SECOND_WORK_MISSING = "SECOND_WORK_CANDIDATE_MISSING"
#: 每臂最多 1 决策 + 1 内容；实际超出就立刻停，不补样。
REASON_ARM_OVER_BUDGET = "ARM_REQUEST_OVER_BUDGET"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or _utc_now()).astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _deadline_passed(now: Optional[datetime] = None) -> bool:
    return (now or _utc_now()) >= DEADLINE


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def _write_json(path: Path, payload: Mapping[str, Any], *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, indent=1, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------
# 中性占位文本与请求身份
# ---------------------------------------------------------------------------------

def neutral_history_text(original: str) -> str:
    """与原文等 Unicode 字符数、等换行位置的中性文本（纯函数）。"""

    source = (NEUTRAL_SENTENCE * (2 + len(original) // max(1, len(NEUTRAL_SENTENCE))))
    out: list[str] = []
    index = 0
    for char in original:
        if char == "\n":
            out.append("\n")
            continue
        out.append(source[index % len(source)])
        index += 1
    return "".join(out)


def neutralise_history(experiences: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in experiences:
        row = dict(item)
        if isinstance(row.get("text"), str):
            row["text"] = neutral_history_text(row["text"])
        out.append(row)
    return out


def prompt_identity(payload: Mapping[str, Any]) -> str:
    """把最终渲染 payload 压成「历史文本之外」的指纹。"""

    data = json.loads(json.dumps(dict(payload), ensure_ascii=False))
    rows = data.get("prior_experiences")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and "text" in row:
                row["text"] = "<HISTORY>"
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha(blob)


def prompt_text_identity(text: str,
                         experiences: Sequence[Mapping[str, Any]] = ()) -> str:
    """最终**渲染文本**的指纹：把每段历史正文换成占位后比较，比只看 payload 更贴近真实发送。"""

    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, Mapping):
        return prompt_identity(parsed)
    if isinstance(parsed, list):
        return prompt_identity({"items": parsed})
    out = str(text or "")
    for row in experiences:
        body = str((row or {}).get("text") or "")
        if body:
            out = out.replace(body, "<HISTORY>")
    return _sha(out)


def history_shape(experiences: Sequence[Mapping[str, Any]]) -> list[tuple[str, str, int]]:
    """历史在最终请求里的形状（refs + 字符数）；任何一个变短都说明被裁剪了。"""

    shape: list[tuple[str, str, int]] = []
    for row in experiences:
        item = row if isinstance(row, Mapping) else {}
        shape.append((str(item.get("artifact_ref") or ""),
                      str(item.get("version_ref") or ""),
                      len(str(item.get("text") or ""))))
    return shape


def payload_history_shape(payload: Mapping[str, Any]) -> list[tuple[str, str, int]]:
    rows = (payload or {}).get("prior_experiences")
    return history_shape([row for row in (rows or []) if isinstance(row, Mapping)])


def identity_basis(payload: Mapping[str, Any]) -> dict[str, Any]:
    """指纹不一致时的安全差异说明：字段名/计数/refs，不含正文。"""

    data = json.loads(json.dumps(dict(payload), ensure_ascii=False))
    rows = data.pop("prior_experiences", None)
    return {
        "fields": sorted(str(key) for key in data.keys()),
        "candidate_ids": [str((row or {}).get("candidate_id") or "")[:80]
                          for row in (data.get("candidates") or [])],
        "prior_experience_count": len(rows or []),
        "prior_experience_refs": [
            {"artifact_ref": (row or {}).get("artifact_ref"),
             "version_ref": (row or {}).get("version_ref"),
             "text_chars": len(str((row or {}).get("text") or ""))}
            for row in (rows or []) if isinstance(row, Mapping)],
    }


# ---------------------------------------------------------------------------------
# 分类与统计
# ---------------------------------------------------------------------------------

CLASS_H = "H"
CLASS_OTHER = "OTHER_LEGAL"
CLASS_NO_ACTION = "NO_ACTION"
CLASS_INVALID = "INVALID_OUTPUT"
CLASS_FAILURE = "CALL_FAILURE"
CLASS_UNRUN = "UNRUN"

_TRANSPORT_FAILURE_MARKERS = (
    "MODEL_TIMEOUT", "MODEL_CONNECTION", "MODEL_RATE_LIMITED", "MODEL_PROVIDER_ERROR",
    "MODEL_AUTH_ERROR", "MODEL_UNAVAILABLE", "MODEL_REQUEST_REJECTED", "LATE_RESULT",
)


def classify_failure(failure_class: Optional[str]) -> str:
    text = str(failure_class or "")
    if not text:
        return CLASS_INVALID
    if any(marker in text for marker in _TRANSPORT_FAILURE_MARKERS):
        return CLASS_FAILURE
    return CLASS_INVALID


def classify_shadow_result(*, decision: Optional[str], selected_candidate_ref: Optional[str],
                           candidate_targets: Mapping[str, Sequence[str]],
                           h_refs: Sequence[str]) -> str:
    if not decision:
        return CLASS_INVALID
    if decision == "NO_ACTION":
        return CLASS_NO_ACTION
    if not selected_candidate_ref:
        return CLASS_OTHER
    targets = [_bare_artifact(ref) for ref in candidate_targets.get(selected_candidate_ref, ())]
    wanted = {_bare_artifact(ref) for ref in h_refs}
    return CLASS_H if any(ref in wanted for ref in targets) else CLASS_OTHER


def mcnemar_exact_two_sided(b: int, c: int) -> float:
    """双侧精确 McNemar（二项分布，p=0.5）。"""

    n = int(b) + int(c)
    if n <= 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(int(b), int(c)) + 1))
    return min(1.0, (2.0 * tail) / float(2 ** n))


def pair_delta(n_pairs: int, a_h: int, b_h: int) -> dict[str, Any]:
    return {"pairs_planned": int(n_pairs), "a_h": int(a_h), "b_h": int(b_h),
            "delta_fraction": (float(a_h - b_h) / float(n_pairs)) if n_pairs else None,
            "delta_count": int(a_h - b_h),
            "threshold_count": int(math.ceil(0.30 * n_pairs)) if n_pairs else None}


# ---------------------------------------------------------------------------------
# 批次账本（阶段起点 + 审计行；真正的预留仍在同一本持久请求账本里）
# ---------------------------------------------------------------------------------

class BatchRefused(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__("%s: %s" % (reason_code, detail) if detail else reason_code)
        self.reason_code = reason_code
        self.detail = detail


class BatchLedger:
    """实验批次清单。计数不另立一份：每次发送前读同一本持久请求账本。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.dir = self.root / EXPERIMENT_DIRNAME
        self.path = self.dir / BATCH_FILENAME
        self.journal = self.dir / "attempts.jsonl"

    def load(self) -> dict[str, Any]:
        data = _read_json(self.path)
        if not data:
            return {"schema": "histchoice.batch.v1", "run_id": RUN_ID, "state": "init",
                    "stages": {}, "rows": [], "rounds": [], "stop": None, "run": None,
                    "frozen": None}
        data.setdefault("stages", {})
        data.setdefault("rows", [])
        data.setdefault("rounds", [])
        return data

    def save(self, data: Mapping[str, Any]) -> None:
        _write_json(self.path, data, private=True)

    @staticmethod
    def spent(ledger_view: Mapping[str, Any]) -> int:
        calls = ledger_view.get("run_window_calls")
        if isinstance(calls, bool) or not isinstance(calls, int):
            raise BatchRefused(REASON_LEDGER_UNREADABLE,
                               "run window count is %r" % (calls,))
        return int(calls)

    def start_stage(self, stage: str, ledger_view: Mapping[str, Any]) -> dict[str, Any]:
        data = self.load()
        used = self.spent(ledger_view)
        data["stages"][stage] = {"started_at": _iso(), "start_calls": used,
                                 "cap": STAGE_CAPS[stage]}
        data["state"] = stage
        self.save(data)
        return data["stages"][stage]

    def assert_room(self, stage: str, ledger_view: Mapping[str, Any],
                    *, reserving: int = 1) -> dict[str, Any]:
        used = self.spent(ledger_view)
        data = self.load()
        entry = (data.get("stages") or {}).get(stage) or {}
        start = entry.get("start_calls")
        if isinstance(start, bool) or not isinstance(start, int):
            raise BatchRefused(REASON_STAGE_CAP, "stage %r has no recorded start" % (stage,))
        stage_used = max(0, used - start)
        if stage_used + reserving > STAGE_CAPS[stage]:
            raise BatchRefused(REASON_STAGE_CAP, "stage %s used %d of %d"
                               % (stage, stage_used, STAGE_CAPS[stage]))
        if used + reserving > TOTAL_REQUEST_CAP:
            raise BatchRefused(REASON_TOTAL_CAP, "batch used %d of %d"
                               % (used, TOTAL_REQUEST_CAP))
        return {"used": used, "stage_used": stage_used,
                "stage_remaining": STAGE_CAPS[stage] - stage_used,
                "total_remaining": TOTAL_REQUEST_CAP - used}

    def begin_attempt(self, *, stage: str, kind: str, slot: Optional[int] = None,
                      arm: Optional[str] = None) -> str:
        attempt_id = "hca:%s" % _sha("%s|%s|%s|%s|%s" % (_iso(), stage, kind, slot,
                                                         arm))[:16]
        row = {"attempt_id": attempt_id, "at": _iso(), "stage": stage, "kind": kind,
               "slot": slot, "arm": arm, "status": "reserved"}
        data = self.load()
        data["rows"] = list(data.get("rows") or []) + [row]
        self.save(data)
        _append_jsonl(self.journal, row)
        return attempt_id

    def finish_attempt(self, attempt_id: str, *, status: str, **extra: Any) -> None:
        data = self.load()
        rows = list(data.get("rows") or [])
        for row in rows:
            if row.get("attempt_id") == attempt_id:
                row["status"] = str(status)
                row.update({key: value for key, value in extra.items()
                            if isinstance(value, (str, int, float, bool)) or value is None})
        data["rows"] = rows
        self.save(data)

    def note_round(self, *, stage: str, index: int, outcome: str, llm_calls: int,
                   decision: Optional[str] = None,
                   selected_candidate_ref: Optional[str] = None,
                   owner_version_created: Optional[bool] = None,
                   h_ref: Optional[str] = None,
                   detail_code: Optional[str] = None) -> None:
        data = self.load()
        data["rounds"] = list(data.get("rounds") or []) + [{
            "at": _iso(), "stage": stage, "index": int(index), "outcome": str(outcome),
            "llm_calls": int(llm_calls), "decision": decision,
            "selected_candidate_ref": selected_candidate_ref,
            "owner_version_created": owner_version_created, "h_ref": h_ref,
            "detail_code": detail_code}]
        self.save(data)

    def mark_stopped(self, reason_code: str, detail: str = "") -> None:
        data = self.load()
        data["stop"] = {"at": _iso(), "reason_code": reason_code,
                        "detail": str(detail)[:200]}
        data["state"] = "stopped"
        self.save(data)

    def mark_finished(self, state: str) -> None:
        data = self.load()
        data["state"] = state
        self.save(data)


# ---------------------------------------------------------------------------------
# 实例准备（0 产品调用）
# ---------------------------------------------------------------------------------

def _check_root(root: Path, *, require_preregistered: bool) -> Optional[dict[str, Any]]:
    if not root.is_absolute():
        return {"ok": False, "reason_code": REASON_ROOT_NOT_ABSOLUTE, "root": str(root)}
    text = str(root)
    if text != str(PREREG_ROOT):
        for reserved in RESERVED_ROOTS:
            if text == reserved or text.startswith(reserved.rstrip("/") + "/"):
                return {"ok": False, "reason_code": REASON_ROOT_RESERVED, "reserved": reserved}
    if require_preregistered and text != str(PREREG_ROOT):
        return {"ok": False, "reason_code": REASON_ROOT_NOT_PREREGISTERED,
                "expected": str(PREREG_ROOT), "root": text}
    return None


def start_supply_owner(root: Path | str, *, tag: str = "run") -> Any:
    """起本根自己的 Supply owner 子进程（只起这一个，不碰任何别的进程）。"""

    from components.phone import demo, service as phone_service

    generation = "hc-%s-%d-%d" % (str(tag)[:16], int(time.time()), os.getpid())
    children = demo.owner_children(root, generation=generation,
                                   writer_instance="histchoice:%d" % os.getpid())
    supply = children["supply"]
    supply.start()
    try:
        if not supply.wait_ready(supply.socket_path, timeout=demo.CHILD_READY_TIMEOUT,
                                 probe="supply"):
            raise RuntimeError("SUPPLY_NOT_READY")
        # 与 serve/fast_day 一致：把本次 live socket 写进实例文件，绑定才指向活着的 owner；
        # 旧 generation 的 socket 文件原样保留，不删。
        instance = dict(phone_service.load_instance(root))
        instance["supply_socket"] = str(supply.socket_path)
        instance["supply_generation"] = generation
        phone_service.write_instance(root, instance)
    except Exception:
        try:
            supply.stop()
        except Exception:
            pass
        raise
    return supply


def _provision_works(*, subject: str, workspace_id: str, grant_id: str,
                     supply_socket: str) -> list[dict[str, Any]]:
    from life_artifact_port import LifeArtifactPort

    if len(WORK_SEED_TEXT) != len(WORK_SEED_TEXT_2):
        raise BatchRefused(REASON_PREPARE_FAILED, "the two seed texts must be equal length")
    port = LifeArtifactPort(supply_socket, subject)
    created: list[dict[str, Any]] = []
    for index, (title, text) in enumerate(zip(WORK_TITLES,
                                              (WORK_SEED_TEXT, WORK_SEED_TEXT_2))):
        outcome = port.create_artifact(
            workspace_id=workspace_id, title=title, content=text, grant_id=grant_id,
            operation_key="histchoice:%s:work:%d" % (RUN_NAME, index))
        if not outcome.applied or not outcome.artifact_id:
            raise BatchRefused(REASON_WORK_CREATE_FAILED,
                               "owner refused %s: %s" % (title, str(outcome.status)[:40]))
        created.append({"title": title, "artifact_id": str(outcome.artifact_id),
                        "version_id": str(outcome.version_id or ""),
                        "status": str(outcome.status)})
    return created


def memory_authority_path(root: Path | str) -> Path:
    """实例删除底账的位置：必须在本实例业务根**之外**（既有规则，不是本实验发明的）。

    它只是一个单调的删除下限账本，不含任何经历正文；本实验不执行任何 DELETE。
    """

    root_path = Path(str(root))
    return root_path.parent / ("%s-memory-authority" % root_path.name) / "authority.db"


def prepare(root: Path | str, *, require_preregistered: bool = True) -> dict[str, Any]:
    """建一次新实例：两件中性作品、真实 owner 绑定、独立 Memory、共享请求账本。0 产品调用。"""

    root_path = Path(str(root))
    refusal = _check_root(root_path, require_preregistered=require_preregistered)
    if refusal is not None:
        return refusal
    if (root_path / EXPERIMENT_DIRNAME / PREREG_FILENAME).exists():
        return {"ok": False, "reason_code": REASON_ALREADY_PREPARED, "root": str(root_path)}
    if root_path.exists() and any(root_path.iterdir()):
        return {"ok": False, "reason_code": REASON_ROOT_NOT_FRESH, "root": str(root_path)}

    from components.phone import demo, life as phone_life, service as phone_service

    try:
        prepared = demo.prepare_demo(
            root_path, with_world=True, seed_work_artifact=False,
            reading_title="", reading_content=None, run_id=RUN_ID,
            request_utc_day_max=300, request_run_window_max=TOTAL_REQUEST_CAP,
            max_cognition_calls_per_day=300)
    except Exception as exc:
        return {"ok": False, "reason_code": REASON_PREPARE_FAILED,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "partial": sorted(entry.name for entry in root_path.iterdir())
                if root_path.exists() else []}
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

    supply = None
    try:
        supply = start_supply_owner(root_path, tag="prepare")
        works = _provision_works(subject=subject, workspace_id=workspace_id,
                                 grant_id=grant_id, supply_socket=str(supply.socket_path))
    except BatchRefused as exc:
        return {"ok": False, "reason_code": exc.reason_code, "detail": exc.detail}
    except Exception as exc:
        return {"ok": False, "reason_code": REASON_SUPPLY_UNAVAILABLE,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    finally:
        if supply is not None:
            try:
                supply.stop()
            except Exception:
                pass

    authority = memory_authority_path(root_path)
    try:
        authority.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(authority.parent, 0o700)
        except OSError:
            pass
        memory = phone_life.enable_instance_memory(
            root_path, subject, "histchoice:%s:memory" % RUN_NAME,
            authority_path=authority)
    except Exception as exc:
        return {"ok": False, "reason_code": REASON_MEMORY_BIND_FAILED,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160]), "works": works}
    if memory.get("ok") is not True:
        return {"ok": False, "reason_code": REASON_MEMORY_BIND_FAILED,
                "detail": str(memory.get("reason") or memory.get("status") or ""),
                "works": works}

    phone = phone_life.load_phone_instance(root_path)
    nested = phone_life.read_nested_life(root_path)
    binding = phone_life.build_binding(phone, nested)
    env = binding["environment"]
    state_root = Path(str(env.get("NYA02_RESIDENT_STATE") or ""))
    if not state_root or not str(state_root).startswith(str(root_path)):
        return {"ok": False, "reason_code": REASON_PREPARE_FAILED,
                "detail": "state root is not inside the experiment root"}
    if str(env.get("CHIYO_AUTONOMOUS_RUN_ID") or "") != RUN_ID:
        return {"ok": False, "reason_code": REASON_PREPARE_FAILED,
                "detail": "instance run identity differs from the pre-registered run"}

    from life_request_budget import LifeRequestBudget

    budget = LifeRequestBudget(state_root, utc_day_max=300,
                               run_window_max=TOTAL_REQUEST_CAP, run_id=RUN_ID)
    try:
        budget.ensure_window(allow_new=True)
        view = budget.view()
    except Exception as exc:
        return {"ok": False, "reason_code": REASON_LEDGER_NOT_ZERO,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    if BatchLedger.spent(view) != 0:
        return {"ok": False, "reason_code": REASON_LEDGER_NOT_ZERO,
                "ledger_state": view.get("state")}

    payload = {
        "schema": "histchoice.prereg.v1", "run_id": RUN_ID, "domain_id": DOMAIN_ID,
        "root": str(root_path), "state_root": str(state_root), "subject": subject,
        "workspace_id": workspace_id, "artifact_grant_id": grant_id, "works": works,
        "memory_authority": str(authority),
        "materials": {"titles": list(WORK_TITLES), "seed_chars": len(WORK_SEED_TEXT),
                      "seed_equal_length": True},
        "caps": {"total": TOTAL_REQUEST_CAP, "stages": dict(STAGE_CAPS),
                 "diagnostic": DIAGNOSTIC_DEFAULT},
        "pairs": {"count": PAIR_COUNT, "order": list(PAIR_ORDER)},
        "model_params": {"temperature": MODEL_TEMPERATURE, "stream": MODEL_STREAM,
                         "decision_output_cap": DECISION_OUTPUT_CAP,
                         "content_output_cap": CONTENT_OUTPUT_CAP},
        "deadline": DEADLINE.isoformat(), "frozen_at": _iso(),
        "note": "材料、场景与参数在第一次产品请求之前写在这里；之后只读。",
    }
    _write_json(root_path / EXPERIMENT_DIRNAME / PREREG_FILENAME, payload, private=True)
    ledger = BatchLedger(root_path)
    data = ledger.load()
    data.update({"state": "prepared", "prepared_at": _iso(), "subject": subject,
                 "state_root": str(state_root), "run_id": RUN_ID, "works": works})
    ledger.save(data)
    return {"ok": True, "root": str(root_path), "state_root": str(state_root),
            "works": works, "memory": memory.get("status"),
            "ledger_state": view.get("state"), "ledger_calls": 0,
            "model_called": False, "request_sent": False}


# ---------------------------------------------------------------------------------
# 运行期装配（真 runtime + 正式 adapter；只有最底层 transport 可被替身注入）
# ---------------------------------------------------------------------------------

def runtime_env(root: Path | str, *, expected_run_id: Optional[str] = None) -> dict[str, str]:
    """实例环境；``expected_run_id`` 只给新 live 入口传自己的身份，默认仍是 v1 的 ``RUN_ID``。"""

    from components.phone import life as phone_life

    phone = phone_life.load_phone_instance(root)
    nested = phone_life.read_nested_life(root)
    if nested is None:
        raise BatchRefused(REASON_NOT_PREPARED, "no nested life instance")
    binding = phone_life.build_binding(phone, nested, base_env=dict(os.environ))
    env = dict(binding["environment"])
    wanted = str(expected_run_id or RUN_ID)
    if str(env.get("CHIYO_AUTONOMOUS_RUN_ID") or "") != wanted:
        raise BatchRefused(REASON_NOT_PREPARED, "instance run identity differs")
    return env


def _memory_authority(env: Mapping[str, str]) -> tuple[str, Optional[int]]:
    from autonomous_life_resident import MEMORY_AUTHORITY_ENV, MEMORY_AUTHORITY_MIN_SEQ_ENV, _first

    raw = _first(env, MEMORY_AUTHORITY_MIN_SEQ_ENV)
    try:
        minimum = int(str(raw)) if raw is not None else None
    except ValueError:
        minimum = None
    return (_first(env, MEMORY_AUTHORITY_ENV) or "", minimum)


def build_memory_ports(env: Mapping[str, str]) -> dict[str, Any]:
    from autonomous_life_resident import (MEMORY_CONTROLS_ENV, MEMORY_DB_ENV,
                                          MEMORY_SUBJECT_ENV, _first)

    database = _first(env, MEMORY_DB_ENV)
    if not database:
        return {"consumer": None, "experience_provider": None, "port": None,
                "reason": "MEMORY_NOT_BOUND"}
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
    from life_experience_port import LifeExperiencePort

    subject = _first(env, MEMORY_SUBJECT_ENV) or "chiyo"
    authority, minimum_seq = _memory_authority(env)
    port = LifeExperiencePort(database, controls_db=_first(env, MEMORY_CONTROLS_ENV),
                              subject=subject, authority_db=authority,
                              authority_min_seq=minimum_seq)
    return {"consumer": MemoryAdmissionExperienceConsumer(port=port, enabled=True),
            "experience_provider": (lambda: port.prompt_view()), "port": port,
            "subject": subject, "reason": ""}


def build_continuity_source(env: Mapping[str, str], runtime: Any) -> Any:
    from autonomous_life_resident import (MEMORY_CONTROLS_ENV, MEMORY_DB_ENV,
                                          MEMORY_SUBJECT_ENV, _first)

    memory = _first(env, MEMORY_DB_ENV)
    if not memory:
        return None
    from life_continuity_inputs import ContinuityBinding, LifeContinuityInputs
    from life_experience_port import LifeExperiencePort, life_conversation_id
    from memory_control import MemoryControlLedger

    owner = _first(env, MEMORY_SUBJECT_ENV) or "chiyo"
    controls = _first(env, MEMORY_CONTROLS_ENV) or memory
    home = str(env.get("CHIYO_WORLD_HOME") or "")
    authority, minimum_seq = _memory_authority(env)
    ledger = MemoryControlLedger(controls, memory, owner=owner,
                                 conversation=life_conversation_id(owner),
                                 authority=authority, authority_min_seq=minimum_seq)
    return LifeContinuityInputs(
        binding=ContinuityBinding(
            subject=owner, home=home, now=_iso(),
            owner_refs={"memory": "memory:%s" % owner, "activity": "activity:canonical",
                        "agenda": "agenda:lr4", "artifact": "artifact:supply"}),
        past_source=LifeExperiencePort(memory, controls_db=controls, subject=owner,
                                       authority_db=authority, authority_min_seq=minimum_seq),
        activity_source=getattr(runtime, "activity_read", None),
        agenda_source=getattr(runtime, "waiting_coordinator", None),
        governance_probe=lambda: {"healthy": bool(ledger.projection().healthy)})


class HistoryGateError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail


class SendLatch:
    """批次级「立刻停发」锁：截止、采集失败、影子里任何 stop 都把它关死。

    它挂在**每个真实发送口**前面（决策 adapter 与内容 client 都查），而不是只在 run 开头
    或外层循环查一次。一旦关闭，后面的请求连预留都不发生。
    """

    def __init__(self, deadline_check: Callable[[], bool] = _deadline_passed) -> None:
        self.deadline_check = deadline_check
        self.stop_reason = ""
        self.refused_sends = 0

    @property
    def closed(self) -> bool:
        return bool(self.stop_reason)

    def stop(self, reason_code: str) -> None:
        if not self.stop_reason:
            self.stop_reason = str(reason_code)

    def check(self) -> None:
        if self.deadline_check():
            self.stop(REASON_DEADLINE_PASSED)
        if self.stop_reason:
            self.refused_sends += 1
            raise HistoryGateError(self.stop_reason, "send latch closed")


class HistoryGateCognitionAdapter:
    """真实 adapter 之外的三道实验门，然后交给已绑定的持久请求账本。

    顺序固定：发送锁（截止/失败）→ 阶段额度门 → 构造边界记录器（可比性/裁剪）→
    持久预留（内层 BudgetedCognitionAdapter）。任何一道拒绝，这次 attempt 都不会发出去。
    同一个 attempt 的 schema 重试也不放行：一次尝试一次计费，没有「自动第二次」。
    """

    def __init__(self, inner: Any, *, stage: str, ledger: BatchLedger, budget: Any = None,
                 recorder: Optional[Callable[[Mapping[str, Any]], None]] = None,
                 latch: Optional[SendLatch] = None) -> None:
        self.inner = inner
        self.stage = stage
        self.ledger = ledger
        self.budget = budget
        self.recorder = recorder
        self.latch = latch

    @property
    def send_split_capable(self) -> bool:
        return bool(getattr(self.inner, "send_split_capable", False))

    def invoke(self, request: Any, *, schema_retry_index: int = 0) -> Any:
        if int(schema_retry_index or 0) > 0:
            raise HistoryGateError(REASON_ATTEMPT_RETRY_REFUSED,
                                   "schema retry would be a second attempt")
        if self.latch is not None:
            self.latch.check()
        if self.budget is not None:
            try:
                self.ledger.assert_room(self.stage, self.budget.view())
            except BatchRefused as exc:
                raise HistoryGateError(exc.reason_code, exc.detail) from exc
        if self.recorder is not None:
            text, payload = self.inner.build_prompt(request)
            self.recorder(payload, text)
        result = self.inner.invoke(request, schema_retry_index=schema_retry_index)
        failure = getattr(result, "failure_class", None)
        if failure is None and isinstance(result, Mapping):
            failure = result.get("failure_class")
        if self.latch is not None and classify_failure(failure) == CLASS_FAILURE:
            # 传输/供应商失败：整批停，同一回合里后面的内容请求也不许再发。
            self.latch.stop(REASON_PROVIDER_FAILURE)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class LatchModelClient:
    """内容请求的发送锁：在预算预留之前查一次，关了就一次都不发。"""

    def __init__(self, inner: Any, latch: SendLatch) -> None:
        self.inner = inner
        self.latch = latch

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.latch.check()
        result = self.inner.complete(*args, **kwargs)
        failure = getattr(result, "failure_class", None)
        if failure is None and isinstance(result, Mapping):
            failure = result.get("failure_class")
        if classify_failure(failure) == CLASS_FAILURE:
            self.latch.stop(REASON_PROVIDER_FAILURE)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def model_config(env: Mapping[str, str]) -> Optional[dict[str, Any]]:
    import autonomous_life_assembly as assembly

    return assembly._model_config(env)


def raw_cognition_adapter(env: Mapping[str, str], *, transport: Any = None,
                          admission: Any = None) -> Any:
    """正式 ProductionAgencyCognitionAdapter（未套预算/门），供离线测量与结构门使用。"""

    from agency_cognition_adapter import ProductionAgencyCognitionAdapter
    from agency_model_client import AgencyModelClient
    from profile_model_transport import OpenAICompatibleTransport

    config = model_config(env)
    if config is None:
        raise BatchRefused(REASON_NO_MODEL_CONFIG, "profile resolves no usable provider")
    if transport is None:
        transport = OpenAICompatibleTransport(
            base_url=config["base_url"], api_key=config["api_key"], model=config["model"],
            provider=config.get("provider") or "custom", timeout_s=30.0,
            purpose="agency_cognition", request_json_object=True, pause_guard=admission,
            temperature=MODEL_TEMPERATURE)
    client = AgencyModelClient(config=config, transport=transport,
                               purpose="agency_cognition", timeout_s=30.0,
                               max_output_tokens=512, temperature=MODEL_TEMPERATURE,
                               request_json_object=True)
    return ProductionAgencyCognitionAdapter(client=client, timeout_s=30.0,
                                            max_output_tokens=512)


def build_cognition_adapter(env: Mapping[str, str], *, budget: Any, admission: Any,
                            ledger: BatchLedger, stage: str,
                            recorder: Optional[Callable[[Mapping[str, Any], str], None]] = None,
                            transport: Any = None,
                            latch: Optional[SendLatch] = None) -> Any:
    from life_request_budget import BudgetedCognitionAdapter

    adapter = raw_cognition_adapter(env, transport=transport, admission=admission)
    if budget is None:
        return adapter
    budgeted = BudgetedCognitionAdapter(adapter, budget, admission=admission)
    return HistoryGateCognitionAdapter(budgeted, stage=stage, ledger=ledger, budget=budget,
                                       recorder=recorder, latch=latch)


def build_content_writer(env: Mapping[str, str], *, budget: Any, admission: Any,
                         transport: Any = None,
                         experience_provider: Any = None,
                         latch: Optional[SendLatch] = None) -> Any:
    """正式内容 writer，按预登记的 2048 输出 cap 构造（这是本实验的请求参数）。"""

    from agency_model_client import AgencyModelClient
    from life_request_budget import BudgetedModelClient
    from model_continuation_writer import ModelContinuationWriter
    from profile_model_transport import OpenAICompatibleTransport

    config = model_config(env)
    if config is None:
        raise BatchRefused(REASON_NO_MODEL_CONFIG, "profile resolves no usable provider")
    if transport is None:
        transport = OpenAICompatibleTransport(
            base_url=config["base_url"], api_key=config["api_key"], model=config["model"],
            provider=config.get("provider") or "custom", timeout_s=30.0,
            purpose="artifact_edit", request_json_object=True, pause_guard=admission,
            temperature=MODEL_TEMPERATURE)
    client = AgencyModelClient(config=config, transport=transport, purpose="artifact_edit",
                               timeout_s=30.0, max_output_tokens=CONTENT_OUTPUT_CAP,
                               temperature=MODEL_TEMPERATURE, request_json_object=True)
    if budget is not None:
        client = BudgetedModelClient(client, budget, admission=admission)
    if latch is not None:
        client = LatchModelClient(client, latch)
    return ModelContinuationWriter(client, max_new_chars=300, timeout_s=30.0,
                                   max_output_tokens=CONTENT_OUTPUT_CAP,
                                   experience_provider=experience_provider)


def build_experiment_runtime(env: Mapping[str, str], *, root: Path, state_root: Path,
                             budget: Any, ledger: BatchLedger, stage: str,
                             recorder: Optional[Callable[[Mapping[str, Any], str], None]] = None,
                             cognition_transport: Any = None,
                             content_transport: Any = None,
                             neutralise_experience_history: bool = False,
                             latch: Optional[SendLatch] = None) -> dict[str, Any]:
    import autonomous_life_assembly as assembly
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    from life_artifact_port import LifeArtifactPort
    from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter

    supply = assembly.resolve_supply_configuration(env)
    if supply["missing"]:
        raise BatchRefused(REASON_SUPPLY_UNAVAILABLE, ",".join(supply["missing"]))
    admission = assembly.pause_request_admission(state_root)
    cognition = build_cognition_adapter(env, budget=budget, admission=admission,
                                        ledger=ledger, stage=stage, recorder=recorder,
                                        transport=cognition_transport, latch=latch)
    memory = build_memory_ports(env)
    experience_provider = memory["experience_provider"]
    if neutralise_experience_history and experience_provider is not None:
        experience_provider = (lambda _inner=experience_provider:
                               neutralise_history(list(_inner() or [])))
    writer = build_content_writer(
        env, budget=budget, admission=admission, transport=content_transport,
        experience_provider=experience_provider, latch=latch)
    port = LifeArtifactPort(supply["supply_socket"], supply["subject_id"])
    artifact_adapter = SupplyManagedArtifactActionAdapter(
        port, grant_id=supply["artifact_grant_id"],
        workspace_id=env.get("CHIYO_LIFE_SUPPLY_WORKSPACE_ID"))
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
        proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        state_root, kill_switches=switches,
        cognition_adapter=cognition, install_root=state_root,
        artifact_action_adapter=artifact_adapter, world_action_adapter=None,
        experience_consumer=memory["consumer"])
    continuity = build_continuity_source(env, runtime)
    assembled = assembly.assemble_autonomous_life(
        runtime, environment=env, state_root=state_root,
        experience_provider=experience_provider, writer=writer,
        requests=budget, continuity_source=continuity, appraisal_source=None,
        world_provider=None)
    if assembled.get("state") != assembly.READY:
        raise BatchRefused(REASON_ASSEMBLY_NOT_READY,
                           "%s: %s" % (assembled.get("reason"), assembled.get("detail")))
    try:
        runtime.coordinator.recover_all_and_reconcile()
    except Exception:
        pass
    try:
        assembled["runner"].reconcile_pending()
    except Exception:
        pass
    return {"runtime": runtime, "assembled": assembled, "cycle": assembled["cycle"],
            "port": port, "admission": admission, "cognition": cognition,
            "writer": writer, "memory": memory, "root": str(root),
            "state_root": str(state_root)}


def release_runtime(bundle: Mapping[str, Any]) -> None:
    lease = getattr(bundle.get("runtime"), "lease", None)
    if lease is not None:
        try:
            lease.release()
        except Exception:
            pass


# ---------------------------------------------------------------------------------
# 阶段一：真实采集（最多 8 次请求）
# ---------------------------------------------------------------------------------

_SAFE_ROUND_KEYS = ("outcome", "decision", "selected_candidate_ref", "activity_ref",
                    "action_status", "owner_version_created", "owner_version_id",
                    "step_work_target", "llm_calls", "decision_route", "correlation_id",
                    "integration_id", "failure_class")


def _round_record(report: Mapping[str, Any]) -> dict[str, Any]:
    record = {key: report.get(key) for key in _SAFE_ROUND_KEYS}
    detail = str(report.get("detail") or "")
    record["detail_code"] = detail.split(":", 1)[0][:64]
    receipt = report.get("step_receipt") or {}
    if isinstance(receipt, Mapping):
        record["receipt_artifact_id"] = receipt.get("artifact_id")
        record["receipt_version_id"] = receipt.get("version_id")
        record["receipt_effect_state"] = receipt.get("effect_state")
    return record


def h_ref_from_round(record: Mapping[str, Any]) -> Optional[str]:
    if record.get("owner_version_created") is not True:
        return None
    target = str(record.get("step_work_target") or "")
    if target.startswith("artifact:"):
        return target
    artifact = str(record.get("receipt_artifact_id") or "")
    if artifact:
        return artifact if artifact.startswith("artifact:") else "artifact:%s" % artifact
    return None


def m0_has_artifact(port: Any, h_ref: str) -> dict[str, Any]:
    if port is None:
        return {"readable": False, "reason": "MEMORY_NOT_BOUND"}
    bare = h_ref[len("artifact:"):] if h_ref.startswith("artifact:") else h_ref
    try:
        records = port.recent(limit=50)
    except Exception as exc:
        return {"readable": False, "reason": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    for record in records:
        artifact = str(getattr(record, "artifact_ref", "") or "")
        if bare and bare in artifact:
            return {"readable": True, "experience_id": getattr(record, "experience_id", None),
                    "version_ref": getattr(record, "version_ref", None)}
    return {"readable": False, "reason": "NOT_FOUND_IN_RECENT"}


def _idle_trigger_kind() -> str:
    """cycle 自己的 IDLE_WINDOW 触发词（就是 AG-1 的 EXPLICIT_REEVALUATION）。"""

    import autonomous_life_cycle as cycle_module

    return cycle_module.TRIGGER_IDLE_WINDOW


def run_acquisition(bundle: Mapping[str, Any], *, ledger: BatchLedger, budget: Any,
                    max_rounds: int = 8,
                    try_freeze: Optional[Callable[[str], Optional[dict[str, Any]]]] = None,
                    deadline_check: Optional[Callable[[], bool]] = None,
                    latch: Optional[SendLatch] = None
                    ) -> dict[str, Any]:
    """真采集：每轮一次正式决定；一旦 H 成立**且**场景可冻结可识别就停。

    ``try_freeze(h_ref)`` 是父级传进来的「冻结 + 结构门」回调，0 请求；返回 None 表示这一轮
    还冻结不出可比场景（例如 H 还占着前台），那就继续采集到 8 次为止。
    """

    cycle = bundle["cycle"]
    port = (bundle.get("memory") or {}).get("port")
    rounds: list[dict[str, Any]] = []
    h_ref: Optional[str] = None
    h_evidence: dict[str, Any] = {}
    frozen: Optional[dict[str, Any]] = None
    gate: Optional[dict[str, Any]] = None
    stalled = 0
    stop_reason = ""
    deadline_check = deadline_check or _deadline_passed
    for index in range(1, max_rounds + 1):
        # 每一轮的真实发送口都要自己过截止与失败锁，而不是只信 run 开头那一次。
        if deadline_check():
            stop_reason = REASON_DEADLINE_PASSED
            break
        if latch is not None and latch.closed:
            stop_reason = latch.stop_reason
            break
        try:
            room = ledger.assert_room("acquisition", budget.view(), reserving=0)
        except BatchRefused as exc:
            stop_reason = exc.reason_code
            break
        if room["stage_remaining"] <= 0:
            stop_reason = REASON_STAGE_CAP
            break
        try:
            report = cycle.evaluate_once(trigger_ref="exp:acq:%02d" % index,
                                         trigger_kind=_idle_trigger_kind(), act=True)
        except HistoryGateError as exc:
            # 门/锁/额度在发送前拒绝：这一轮没有发出请求，也不再继续烧后面的轮次。
            stop_reason = exc.reason_code
            break
        record = _round_record(report)
        record["index"] = index
        rounds.append(record)
        ledger.note_round(stage="acquisition", index=index,
                          outcome=str(record.get("outcome") or ""),
                          llm_calls=int(record.get("llm_calls") or 0),
                          decision=record.get("decision"),
                          selected_candidate_ref=record.get("selected_candidate_ref"),
                          owner_version_created=record.get("owner_version_created"),
                          h_ref=h_ref_from_round(record),
                          detail_code=record.get("detail_code"))
        if classify_failure(record.get("failure_class")) == CLASS_FAILURE \
                or record.get("outcome") == "CALL_FAILURE":
            # 传输/供应商失败：整批停，不拿下一轮当重试。invalid output 才占槽继续。
            if latch is not None:
                latch.stop(REASON_PROVIDER_FAILURE)
            stop_reason = REASON_PROVIDER_FAILURE
            break
        candidate_h = h_ref_from_round(record)
        if candidate_h and h_ref is None:
            evidence = m0_has_artifact(port, candidate_h)
            if evidence.get("readable"):
                h_ref = candidate_h
                h_evidence = evidence
        if h_ref is not None and try_freeze is not None:
            attempt = try_freeze(h_ref)
            if attempt and attempt.get("frozen") is not None:
                frozen = attempt["frozen"]
                gate = attempt.get("gate")
                stop_reason = "H_ESTABLISHED"
                break
        if int(record.get("llm_calls") or 0) == 0:
            stalled += 1
            if stalled >= 2:
                stop_reason = REASON_ACQUISITION_STALLED
                break
        else:
            stalled = 0
    return {"rounds": rounds, "h_ref": h_ref, "h_evidence": h_evidence,
            "frozen": frozen, "gate": gate,
            "stop_reason": stop_reason or REASON_NO_EFFECTIVE_HISTORY,
            "requests_used": BatchLedger.spent(budget.view())}


# ---------------------------------------------------------------------------------
# 冻结：候选/情境/事实/历史（从一次真实状态算出，之后只读）
# ---------------------------------------------------------------------------------

def freeze_scenario(bundle: Mapping[str, Any], *, h_ref: str,
                    observed_at: Optional[str] = None) -> dict[str, Any]:
    cycle = bundle["cycle"]
    runtime = bundle["runtime"]
    obs = observed_at or _iso()
    cset = runtime.coordinator.candidate_materializer.build_candidate_set(observed_at=obs)
    refs = [str(ref) for ref in (getattr(cset, "candidate_refs", None) or [])]
    records = []
    for ref in refs:
        row = runtime.candidate_read.get_candidate(ref)
        if row:
            records.append(dict(row))
    situation = cycle.read_situation(obs)
    capacity = cycle.capacity_kwargs(situation)
    facts = dict(cycle.world_observation_facts())
    material_facts = dict(cycle.private_material_facts())
    try:
        from subject_private_materials import merge_decision_facts

        facts = merge_decision_facts(facts, material_facts)
    except Exception:
        facts.update(material_facts)
    try:
        readiness = cycle.step_readiness(act=True)
        ready_facts = dict(readiness.fact_keys() or {})
    except Exception:
        ready_facts = {}
    if ready_facts:
        # 就绪事实走 AG-1 自己的优先级合并；导入失败就明确不加，不另造语义。
        try:
            from life_step_readiness import merge_priority_facts
        except Exception:
            merge_priority_facts = None
        if merge_priority_facts is not None:
            facts = merge_priority_facts(facts, ready_facts)
        else:
            facts.update(ready_facts)
    context = cycle.decision_situation_context(now=obs, park=None, wait_basis=None)
    experiences = list(cycle.consideration_context() or [])
    import agency_decision_ag1 as ag1

    objects = [ag1.CandidateRecord.from_dict(row) for row in records]
    return {"observed_at": obs, "candidate_set": cset, "candidate_records": records,
            "candidate_objects": objects,
            "candidate_refs": refs, "situation_context": context,
            "observed_facts": dict(facts), "capacity_kwargs": dict(capacity),
            "allowed_method_refs": list(cycle.practice_method_refs() or []),
            "prior_experiences": experiences, "h_ref": h_ref}


def candidate_targets(records: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    return {str(row.get("candidate_id") or ""): [str(ref) for ref in (row.get("target_refs") or [])]
            for row in records}


def _make_shadow_service(runtime: Any, shadow_root: Path, adapter: Any) -> Any:
    import agency_decision_ag1 as ag1
    import activity_continuity as ac

    shadow_root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(shadow_root, 0o700)
    except OSError:
        pass
    if ac.is_production_path(shadow_root):
        raise BatchRefused(REASON_BRANCH_ISOLATION,
                           "shadow store must not be a production home")
    # 影子记录只能落在实验目录里：主业务的 AG-1 决定、Activity、M0 一行都不写。
    if EXPERIMENT_DIRNAME not in shadow_root.resolve().parts:
        raise BatchRefused(REASON_BRANCH_ISOLATION,
                           "shadow store must live under the experiment directory")
    store = ag1.AgencyDecisionStore(shadow_root, namespace="shadow")
    capability = ag1.AgencyDecisionAuthority.issue_capability(
        runtime.lease, caller_module="agency_decision_service", store_root=shadow_root,
        namespace="shadow")
    return ag1.AgencyDecisionService(
        store=store, lease=runtime.lease, capability=capability,
        candidate_read_service=runtime.candidate_read,
        activity_read_service=runtime.activity_read,
        resume_eligibility_store=getattr(runtime, "eligibility_store", None),
        cognition_adapter=adapter, max_schema_retries=0)


def new_opportunity(runtime: Any, frozen: Mapping[str, Any], *, trigger_ref: str) -> Any:
    import agency_decision_ag1 as ag1

    current_ref = None
    for activity in runtime.activity_read.list_open_activities():
        if activity.get("status") == "ACTIVE":
            current_ref = activity.get("activity_id")
            break
    return ag1.DecisionOpportunity.create(
        trigger_kind=ag1.TRIGGER_CANDIDATE_SET_CHANGED, trigger_ref=trigger_ref,
        candidate_set=frozen["candidate_set"], current_activity_ref=current_ref,
        observed_at=frozen["observed_at"],
        causal_parent_refs=[trigger_ref, frozen["candidate_set"].candidate_set_id])


def shadow_kwargs(frozen: Mapping[str, Any],
                  experiences: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    kwargs = {"candidate_set": frozen["candidate_set"],
              "candidates": list(frozen["candidate_objects"]),
              "prior_experiences": [dict(item) for item in experiences],
              "situation_context": dict(frozen["situation_context"]),
              "observed_facts": dict(frozen["observed_facts"]),
              "allowed_method_refs": list(frozen["allowed_method_refs"])}
    kwargs.update(frozen["capacity_kwargs"])
    return kwargs


def _offline_no_action_transport() -> Any:
    """结构门的默认 transport：本模块自己的离线替身，回一条合法 NO_ACTION，不碰网络。

    默认值就是它——调用方忘了传 transport 也仍然是 0 网络、0 预算，绝不落到真实 provider。
    """

    return CaptureTransport(
        text=json.dumps({"decision": "NO_ACTION", "reason_codes": ["NO_ACTION_CHOSEN"]}))


def _bare_artifact(ref: Any) -> str:
    """去掉重复的 ``artifact:`` 前缀，只留 id（正式目标里有 ``artifact:artifact:x`` 的形状）。"""

    text = str(ref or "")
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def work_artifact_refs(bundle: Mapping[str, Any]) -> set[str]:
    """本实例预登记过的两件作品（只读预登记文件；读不到就返回空集）。"""

    root = Path(str(bundle.get("root") or ""))
    prereg = _read_json(root / EXPERIMENT_DIRNAME / PREREG_FILENAME) if root else {}
    refs: set[str] = set()
    for work in prereg.get("works") or []:
        artifact = str((work or {}).get("artifact_id") or "")
        if artifact:
            refs.add(artifact if artifact.startswith("artifact:") else "artifact:%s" % artifact)
    return refs


def structural_gate(bundle: Mapping[str, Any], frozen: Mapping[str, Any],
                    *, transport: Any = None) -> dict[str, Any]:
    """最小离线结构门：正式规则是否真给出「H + 另一件作品 + NO_ACTION」三个可执行选择。

    用正式 adapter（底层 transport 默认是本模块的离线替身，回一条合法的 NO_ACTION）走一次
    完整的 AG-1 校验，只写实验自己的 shadow store；不发任何真实请求、不占任何预算。
    """

    recorded: dict[str, Any] = {}
    offline_transport = transport is None
    if offline_transport:
        transport = _offline_no_action_transport()

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
    adapter = _Recorder(raw_cognition_adapter(env, transport=transport,
                                              admission=bundle["admission"]))
    shadow_root = Path(bundle["root"]) / EXPERIMENT_DIRNAME / CAPTURE_STATE_DIRNAME
    service = _make_shadow_service(bundle["runtime"], shadow_root, adapter)
    # 每次结构门都是一次新的机会：同一个 trigger 会被 AG-1 当成幂等复用，不会真的再问一次。
    counter = int(bundle.get("capture_index", 0) or 0) if isinstance(bundle, dict) else 0
    if isinstance(bundle, dict):
        bundle["capture_index"] = counter + 1
    opportunity = new_opportunity(bundle["runtime"], frozen,
                                  trigger_ref="exp:capture:%02d" % counter)
    result: dict[str, Any] = {}
    try:
        result = service.evaluate_opportunity(
            opportunity=opportunity, **shadow_kwargs(frozen, frozen["prior_experiences"]))
    except Exception as exc:
        return {"ok": False, "reason_code": REASON_SCENARIO_NOT_IDENTIFIABLE,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    request = recorded.get("request")
    payload = recorded.get("payload")
    if request is None or payload is None:
        return {"ok": False, "reason_code": REASON_SCENARIO_NOT_IDENTIFIABLE,
                "detail": "no cognition request was built (rule-resolved path)",
                "result_status": (result or {}).get("status"),
                "failure_class": (result or {}).get("failure_class"),
                "decision_route": getattr(result.get("decision_record"), "decision_route", None),
                "candidate_refs": len(frozen.get("candidate_refs") or []),
                "candidate_records": len(frozen.get("candidate_records") or [])}
    candidates = list(getattr(request, "candidates", ()) or ())
    legal_enums = [str(x) for x in (getattr(request, "legal_decision_enums", ()) or ())]
    h_targets = {_bare_artifact(frozen["h_ref"])}
    h_candidates = [row for row in candidates
                    if any(_bare_artifact(ref) in h_targets
                           for ref in (row.get("target_refs") or ()))]
    executable = [row for row in candidates
                  if {"START", "CONTINUE", "RESUME"} & set(row.get("legal_decisions") or ())]
    h_executable = [row for row in h_candidates
                    if {"START", "CONTINUE", "RESUME"} & set(row.get("legal_decisions") or ())]
    # 第二个可执行候选必须指向**另一件作品**：同件的 START/closeout 两个候选不算独立选择。
    other_works = {_bare_artifact(ref) for ref in work_artifact_refs(bundle)}
    h_bare = _bare_artifact(frozen["h_ref"])
    other_works.discard(h_bare)
    other_work_executable = [
        row for row in executable
        if any(_bare_artifact(ref) in other_works and _bare_artifact(ref) != h_bare
               for ref in (row.get("target_refs") or ()))]
    other_executable = [row for row in executable if row not in h_candidates]
    ok = (bool(h_executable) and bool(other_work_executable)
          and "NO_ACTION" in legal_enums)
    reason = ""
    if not ok:
        reason = (REASON_SECOND_WORK_MISSING if h_executable and "NO_ACTION" in legal_enums
                  else REASON_SCENARIO_NOT_IDENTIFIABLE)
    return {
        "ok": ok, "reason_code": reason,
        "candidate_count": len(candidates), "executable_count": len(executable),
        "h_candidate_count": len(h_candidates), "h_executable": bool(h_executable),
        "other_executable": bool(other_executable),
        "other_work_executable": bool(other_work_executable),
        "other_work_count": len(other_works),
        "offline_transport": bool(offline_transport),
        "no_action_legal": "NO_ACTION" in legal_enums,
        "legal_decision_enums": sorted(set(legal_enums)),
        "candidate_ids": [str(row.get("candidate_id") or "") for row in candidates],
        "candidate_targets": [
            {"candidate_id": str(row.get("candidate_id") or ""),
             "targets": [str(ref)[:60] for ref in (row.get("target_refs") or ())],
             "legal": sorted(str(x) for x in (row.get("legal_decisions") or ()))}
            for row in candidates],
        "h_candidate_ids": [str(row.get("candidate_id") or "") for row in h_candidates],
        "baseline": {"fingerprint": prompt_identity(payload),
                     "text_fingerprint": prompt_text_identity(
                         str(recorded.get("text") or ""), frozen["prior_experiences"]),
                     "history_shape": payload_history_shape(payload),
                     "basis": identity_basis(payload)},
        "capture_decision": getattr(result.get("decision_record"), "decision", None),
    }


# ---------------------------------------------------------------------------------
# 阶段二：影子 20 对（每样本 1 次请求）
# ---------------------------------------------------------------------------------

def make_history_recorder(baseline: Mapping[str, Any],
                          experiences: Sequence[Mapping[str, Any]]) -> Callable[..., None]:
    """真实发送前的可比性/裁剪门：只忽略历史正文，其他任何差异都拒绝发送。"""

    expected_shape = history_shape(experiences)
    base_fingerprint = str(baseline.get("fingerprint") or "")
    base_text = str(baseline.get("text_fingerprint") or "")
    base_shape = [tuple(row) if isinstance(row, (list, tuple)) else row
                  for row in (baseline.get("history_shape") or [])]

    def recorder(payload: Mapping[str, Any], text: str) -> None:
        if expected_shape and payload_history_shape(payload) != expected_shape:
            raise HistoryGateError(REASON_SECOND_VARIABLE,
                                   "history rows were trimmed or reshaped")
        if base_shape and [tuple(row) for row in expected_shape] != base_shape:
            raise HistoryGateError(REASON_SECOND_VARIABLE,
                                   "arm history shape differs from the frozen baseline")
        if base_fingerprint and prompt_identity(payload) != base_fingerprint:
            raise HistoryGateError(REASON_SECOND_VARIABLE,
                                   "payload outside prior_experiences changed")
        if base_text and prompt_text_identity(text, experiences) != base_text:
            raise HistoryGateError(REASON_SECOND_VARIABLE,
                                   "rendered prompt outside history text changed")

    return recorder


def run_shadow(bundle: Mapping[str, Any], *, frozen: Mapping[str, Any],
               baseline: Mapping[str, Any], ledger: BatchLedger, budget: Any,
               env: Mapping[str, str], deadline_check: Callable[[], bool],
               cognition_factory: Optional[Callable[..., Any]] = None,
               latch: Optional[SendLatch] = None) -> dict[str, Any]:
    runtime = bundle["runtime"]
    root = Path(bundle["root"])
    admission = bundle["admission"]
    targets = candidate_targets(frozen["candidate_records"])
    arm_a = [dict(item) for item in frozen["prior_experiences"]]
    arm_b = neutralise_history(arm_a)
    samples: list[dict[str, Any]] = []
    stop_reason = ""

    def make_recorder(experiences: Sequence[Mapping[str, Any]]) -> Callable[..., None]:
        return make_history_recorder(baseline, experiences)

    for pair_index, order in enumerate(PAIR_ORDER, start=1):
        for arm in order:
            if latch is not None:
                if latch.closed:
                    stop_reason = latch.stop_reason
                    break
            if deadline_check():
                stop_reason = REASON_DEADLINE_PASSED
                break
            try:
                ledger.assert_room("shadow", budget.view())
            except BatchRefused as exc:
                stop_reason = exc.reason_code
                break
            experiences = arm_a if arm == "A" else arm_b
            adapter = build_cognition_adapter(
                env, budget=budget, admission=admission, ledger=ledger, stage="shadow",
                recorder=make_recorder(experiences), latch=latch,
                transport=(cognition_factory("shadow", arm) if cognition_factory else None))
            # 每样本一个独立 shadow store：两臂用同一个 trigger，AG-1 的幂等复用也不会把
            # B 样本读成 A 的回放（A/B 的唯一差别仍是历史正文）。
            sample_root = (root / EXPERIMENT_DIRNAME / SHADOW_STATE_DIRNAME
                           / ("p%02d-%s" % (pair_index, arm)))
            service = _make_shadow_service(runtime, sample_root, adapter)
            opportunity = new_opportunity(
                runtime, frozen, trigger_ref="exp:shadow:%02d" % pair_index)
            attempt_id = ledger.begin_attempt(stage="shadow", kind="decision",
                                              slot=pair_index, arm=arm)
            calls_before = BatchLedger.spent(budget.view())
            sample: dict[str, Any] = {"pair": pair_index, "arm": arm, "order": order}
            try:
                result = service.evaluate_opportunity(
                    opportunity=opportunity, **shadow_kwargs(frozen, experiences))
            except HistoryGateError as exc:
                ledger.finish_attempt(attempt_id, status="gate_refused",
                                      reason_code=exc.reason_code)
                samples.append({**sample, "classification": CLASS_UNRUN,
                                "reason_code": exc.reason_code})
                stop_reason = exc.reason_code
                break
            except Exception as exc:
                ledger.finish_attempt(attempt_id, status="error",
                                      error_kind=type(exc).__name__)
                samples.append({**sample, "classification": CLASS_FAILURE,
                                "reason_code": type(exc).__name__})
                stop_reason = REASON_PROVIDER_FAILURE
                break
            calls_after = BatchLedger.spent(budget.view())
            if calls_after - calls_before != 1:
                ledger.finish_attempt(attempt_id, status="ledger_drift",
                                      delta=int(calls_after - calls_before))
                samples.append({**sample, "classification": CLASS_UNRUN,
                                "reason_code": REASON_LEDGER_DRIFT})
                stop_reason = REASON_LEDGER_DRIFT
                break
            record = (result or {}).get("decision_record")
            failure = (result or {}).get("failure_class")
            decision = getattr(record, "decision", None)
            selected = getattr(record, "selected_candidate_ref", None)
            if record is None:
                classification = classify_failure(failure)
                if classification == CLASS_FAILURE:
                    stop_reason = REASON_PROVIDER_FAILURE
            else:
                classification = classify_shadow_result(
                    decision=decision, selected_candidate_ref=selected,
                    candidate_targets=targets, h_refs=[str(frozen["h_ref"])])
            ledger.finish_attempt(attempt_id, status=classification, decision=decision,
                                  selected_candidate_ref=selected,
                                  failure_class=str(failure or "")[:80] or None)
            samples.append({**sample, "classification": classification, "decision": decision,
                            "selected_candidate_ref": selected,
                            "failure_class": str(failure or "")[:80] or None,
                            "h": classification == CLASS_H})
            if stop_reason:
                break
        if stop_reason:
            break
    return {"samples": samples, "pairs": pair_summary(samples), "stop_reason": stop_reason,
            "baseline": dict(baseline), "requests_used": BatchLedger.spent(budget.view())}


def pair_summary(samples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_pair: dict[int, dict[str, Any]] = {}
    for sample in samples:
        pair = int(sample.get("pair") or 0)
        by_pair.setdefault(pair, {})[str(sample.get("arm"))] = sample.get("classification")
    return [{"pair": pair, "A": arms.get("A", CLASS_UNRUN), "B": arms.get("B", CLASS_UNRUN)}
            for pair, arms in sorted(by_pair.items())]


# ---------------------------------------------------------------------------------
# 阶段三：真实承接（第一对；每臂最多 1 决策 + 1 内容）
# ---------------------------------------------------------------------------------

def _rebase_json_paths(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        if value == old or value.startswith(old.rstrip("/") + "/"):
            return new + value[len(old):]
        return value
    if isinstance(value, list):
        return [_rebase_json_paths(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _rebase_json_paths(item, old, new) for key, item in value.items()}
    return value


def branch_path_audit(env: Mapping[str, str], branch: Path,
                      *, shared_authority: Optional[Path] = None) -> dict[str, Any]:
    """分支隔离证明。

    经历/控制库、Supply socket、World home、安装/状态根**必须**落在分支内；唯一的例外是
    删除底账（authority）：它是既有规则要求放在业务根之外的单调下限账本，本身不含经历
    正文，本实验也不执行任何 DELETE。例外必须**精确等于**主实例记录的那一个文件，否则
    同样判为未证明。
    """

    strict = ("NYA02_MEMORY_DB", "CHIYO_LIFE_MEMORY_DB", "NYA02_MEMORY_CONTROLS_DB",
              "CHIYO_LIFE_MEMORY_CONTROLS_DB", "CHIYO_WORLD_HOME", "WORLD_BODY_SOCKET",
              "LIFE_SUPPLY_SOCKET", "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET",
              "CHIYO_LIFE_INSTALL_ROOT", "NYA02_RESIDENT_STATE")
    authority_names = ("NYA02_MEMORY_AUTHORITY_DB", "CHIYO_LIFE_MEMORY_AUTHORITY_DB")
    branch_text = str(branch.resolve())
    authority_text = str(Path(shared_authority).resolve()) if shared_authority else ""
    outside: list[str] = []
    checked: list[str] = []
    shared_seen: list[str] = []
    for name in strict:
        value = str(env.get(name) or "").strip()
        if not value or not value.startswith("/"):
            continue
        checked.append(name)
        resolved = str(Path(value).resolve())
        if not (resolved == branch_text or resolved.startswith(branch_text + "/")):
            outside.append(name)
    for name in authority_names:
        value = str(env.get(name) or "").strip()
        if not value:
            continue
        checked.append(name)
        resolved = str(Path(value).resolve())
        if authority_text and resolved == authority_text:
            shared_seen.append(name)
            continue
        if not (resolved == branch_text or resolved.startswith(branch_text + "/")):
            outside.append(name)
    return {"ok": not outside, "outside": outside, "checked": checked,
            "shared_authority": sorted(set(shared_seen)), "branch": branch_text}


def _ignore_special_files(directory: str, names: list[str]) -> set[str]:
    """复制实例时跳过 unix socket / FIFO：它们是进程产物，分支会有自己的。"""

    import stat as stat_module

    skipped: set[str] = set()
    for name in names:
        try:
            mode = os.lstat(os.path.join(directory, name)).st_mode
        except OSError:
            skipped.add(name)
            continue
        if stat_module.S_ISSOCK(mode) or stat_module.S_ISFIFO(mode):
            skipped.add(name)
    return skipped


def _looks_like_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _snapshot_sqlite(source: str, target: str) -> None:
    """用 sqlite 自己的 backup API 取一致快照：活库有未提交事务也不会复制成半截。"""

    import sqlite3

    scratch = target + ".snapshot"
    source_conn = sqlite3.connect("file:%s?mode=ro" % (source,), uri=True)
    try:
        destination = sqlite3.connect(scratch)
        try:
            source_conn.backup(destination)
        finally:
            destination.close()
    finally:
        source_conn.close()
    os.replace(scratch, target)


def _copy_tree_consistent(main_root: Path, branch: Path) -> dict[str, int]:
    """复制实例：普通文件逐字节复制，活 sqlite 走一致性快照，socket/FIFO 跳过。"""

    copied = 0
    snapshotted = 0
    branch.mkdir(parents=True, exist_ok=False)
    try:
        os.chmod(branch, 0o700)
    except OSError:
        pass
    for directory, dirnames, filenames in os.walk(str(main_root)):
        relative = os.path.relpath(directory, str(main_root))
        target_dir = branch if relative == "." else branch / relative
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            # 目录权限也要跟原实例一致（正式 root 有 0o700 断言），不能留 umask 的 0755。
            shutil.copystat(directory, target_dir)
        except OSError:
            pass
        skipped = _ignore_special_files(directory, list(filenames))
        for name in filenames:
            if name in skipped:
                continue
            source_path = os.path.join(directory, name)
            target_path = str(target_dir / name)
            if os.path.islink(source_path):
                continue
            if _looks_like_sqlite(source_path):
                _snapshot_sqlite(source_path, target_path)
                snapshotted += 1
                continue
            shutil.copy2(source_path, target_path)
            copied += 1
        dirnames[:] = [name for name in dirnames if not os.path.islink(
            os.path.join(directory, name))]
    return {"files_copied": copied, "sqlite_snapshots": snapshotted}


def clone_branch(main_root: Path, branch: Path, *, expected_run_id: Optional[str] = None,
                 shared_authority: Optional[Path] = None) -> dict[str, Any]:
    """从 ``main_root`` 的一致快照复制出 ``branch``。

    两个可选参数只服务新 live 入口：``expected_run_id`` 让它按自己的 run 身份构造分支环境；
    ``shared_authority`` 指定精确共享的删除底账（默认沿用 v1 的同层 ``*-memory-authority``）。
    """

    if branch.exists():
        return {"ok": False, "reason_code": REASON_BRANCH_ISOLATION,
                "detail": "branch root already exists"}
    try:
        copy_stats = _copy_tree_consistent(Path(main_root), branch)
    except Exception as exc:
        return {"ok": False, "reason_code": REASON_BRANCH_ISOLATION,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    old_prefix = str(main_root.resolve())
    new_prefix = str(branch.resolve())
    rewritten: list[str] = []
    for relative in ("instance.json", "one-day/instance.json",
                     "experiment/histchoice.json"):
        path = branch / relative
        if not path.exists():
            continue
        data = _read_json(path)
        if not data:
            continue
        data = _rebase_json_paths(data, old_prefix, new_prefix)
        # socket 是进程产物：分支的绑定先指向分支内的路径，等分支 owner 起好再写成 live 的。
        if "supply_socket" in data:
            data["supply_socket"] = str(branch.resolve() / "run" / "supply-branch.sock")
        if data.get("world_body_socket"):
            data["world_body_socket"] = str(branch.resolve() / "run" / "gateway-branch.sock")
        _write_json(path, data, private=True)
        rewritten.append(relative)
    try:
        env = runtime_env(branch, expected_run_id=expected_run_id)
    except Exception as exc:
        return {"ok": False, "reason_code": REASON_BRANCH_ISOLATION,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
                "rewritten": rewritten}
    authority = (Path(shared_authority) if shared_authority is not None
                 else memory_authority_path(main_root))
    audit = branch_path_audit(env, branch, shared_authority=authority)
    audit["rewritten"] = rewritten
    audit["copy"] = copy_stats
    if not audit.get("ok"):
        audit["reason_code"] = REASON_BRANCH_ISOLATION
    return audit


def shadow_blocks_continuation(shadow: Mapping[str, Any],
                              latch: Optional[SendLatch] = None) -> str:
    """影子里任何 stop 都锁死后续发送；返回锁死原因（空串表示可以继续）。"""

    reason = str((shadow or {}).get("stop_reason") or "")
    if reason and latch is not None:
        latch.stop(reason)
    if latch is not None and latch.closed:
        return latch.stop_reason
    return reason


def skipped_continuation(reason_code: str, *, requests_used: int = 0) -> dict[str, Any]:
    """承接两臂都是 NOT_RUN —— 一次额外请求都不发。"""

    return {"A": {"status": "NOT_RUN", "reason_code": reason_code},
            "B": {"status": "NOT_RUN", "reason_code": reason_code},
            "stop_reason": reason_code, "requests_used": int(requests_used),
            "skipped": True}


def run_continuation(bundle: Mapping[str, Any], *, ledger: BatchLedger, budget: Any,
                     root: Path, deadline_check: Callable[[], bool],
                     cognition_factory: Optional[Callable[..., Any]] = None,
                     content_factory: Optional[Callable[..., Any]] = None,
                     env_overrides: Optional[Mapping[str, str]] = None,
                     latch: Optional[SendLatch] = None) -> dict[str, Any]:
    results: dict[str, Any] = {"A": {"status": "NOT_RUN"}, "B": {"status": "NOT_RUN"},
                               "requests_used": 0, "first_pair": list(PAIR_ORDER[:1])}
    # 分支必须**先于 A 的任何写入**从同一个冻结起点复制出来；复制不出来就两臂都不跑，
    # 绝不允许「先跑 A 再说 B 不行」——那样 B 拿到的已经不是同一起点。
    branch = Path(root).parent / ("%s-%s" % (Path(root).name, BRANCH_DIRNAME))
    clone = clone_branch(Path(root), branch)
    results["branch"] = {key: clone.get(key) for key in
                         ("ok", "reason_code", "detail", "outside", "checked", "copy")}
    if not clone.get("ok"):
        for arm in ("A", "B"):
            results[arm] = {"status": "NOT_RUN", "reason_code": REASON_BRANCH_ISOLATION,
                            "detail": clone.get("detail")}
        results["requests_used"] = BatchLedger.spent(budget.view())
        return results
    if deadline_check():
        results["A"] = {"status": "NOT_RUN", "reason_code": REASON_DEADLINE_PASSED}
    else:
        results["A"] = continue_round(bundle, ledger=ledger, budget=budget,
                                      trigger="exp:cont:A")
    arm_a_requests = int(results["A"].get("requests") or 0)
    if arm_a_requests > 2:
        # 硬门在 continue_round 里（第 3 次发送在 reserve 前就被拒）；这里只是事后断言，
        # 真走到这说明硬门被绕过，立刻停掉 B，不拿后验检查冒充发送前上限。
        results["A"]["over_budget"] = True
        results["B"] = {"status": "NOT_RUN", "reason_code": REASON_ARM_OVER_BUDGET}
    elif deadline_check():
        results["B"] = {"status": "NOT_RUN", "reason_code": REASON_DEADLINE_PASSED}
    else:
        results["B"] = continue_branch(branch, ledger=ledger, budget=budget,
                                        cognition_factory=cognition_factory,
                                        content_factory=content_factory,
                                        env_overrides=env_overrides, latch=latch)
    results["requests_used"] = BatchLedger.spent(budget.view())
    return results


def set_bundle_stage(bundle: Mapping[str, Any], stage: str) -> None:
    """把复用同一个 runtime 的阶段门切到当前阶段（否则会按旧阶段额度拒发）。"""

    gate = bundle.get("cognition")
    if gate is not None and hasattr(gate, "stage"):
        gate.stage = stage


def continue_round(bundle: Mapping[str, Any], *, ledger: BatchLedger, budget: Any,
                   trigger: str) -> dict[str, Any]:
    set_bundle_stage(bundle, "continuation")
    try:
        ledger.assert_room("continuation", budget.view(), reserving=0)
    except BatchRefused as exc:
        return {"status": "NOT_RUN", "reason_code": exc.reason_code}
    # 一轮最多 1 决策 + 1 内容；留不出两个位置就不开始这一轮。
    if ledger.assert_room("continuation", budget.view(),
                          reserving=2)["stage_remaining"] < 2:
        return {"status": "NOT_RUN", "reason_code": REASON_STAGE_CAP}
    # 每臂硬上限落在**共享的持久账本自身**上（决策与内容两个发送口用的是同一个对象）。
    # 第 3 次发送会在 reserve 处被拒，而不是发完再事后检查；回合结束/异常都恢复原上限。
    with arm_ceiling(budget) as arm_limits:
        before = arm_limits["start"]
        report = bundle["cycle"].evaluate_once(trigger_ref=trigger,
                                               trigger_kind=_idle_trigger_kind(), act=True)
        after = BatchLedger.spent(budget.view())
    record = _round_record(report)
    record.update({"status": "RAN", "requests": after - before,
                   "arm_request_cap": ARM_REQUEST_CAP,
                   "arm_ceiling": arm_limits["ceiling"],
                   "h_ref": h_ref_from_round(record)})
    ledger.note_round(stage="continuation", index=0, outcome=str(record.get("outcome") or ""),
                      llm_calls=int(record.get("llm_calls") or 0),
                      decision=record.get("decision"),
                      selected_candidate_ref=record.get("selected_candidate_ref"),
                      owner_version_created=record.get("owner_version_created"),
                      h_ref=record.get("h_ref"), detail_code=record.get("detail_code"))
    return record


def continue_branch(branch: Path, *, ledger: BatchLedger, budget: Any,
                    cognition_factory: Optional[Callable[..., Any]] = None,
                    content_factory: Optional[Callable[..., Any]] = None,
                    env_overrides: Optional[Mapping[str, str]] = None,
                    latch: Optional[SendLatch] = None) -> dict[str, Any]:
    supply = None
    bundle = None
    try:
        supply = start_supply_owner(branch, tag="branch-b")
        env = runtime_env(branch)
        if env_overrides:
            env.update({str(key): str(value) for key, value in env_overrides.items()})
        state_root = Path(str(env.get("NYA02_RESIDENT_STATE") or ""))
        bundle = build_experiment_runtime(
            env, root=branch, state_root=state_root, budget=budget, ledger=ledger,
            stage="continuation",
            cognition_transport=(cognition_factory("continuation", "B")
                                 if cognition_factory else None),
            content_transport=(content_factory("continuation", "B")
                               if content_factory else None),
            # 反事实分支：与影子 B 臂同一个干预（只换历史文本），其余完全照旧。
            neutralise_experience_history=True, latch=latch)
        record = continue_round(bundle, ledger=ledger, budget=budget, trigger="exp:cont:B")
        record["branch"] = str(branch)
        return record
    except BatchRefused as exc:
        return {"status": "NOT_RUN", "reason_code": exc.reason_code, "detail": exc.detail}
    except Exception as exc:
        return {"status": "NOT_RUN", "reason_code": REASON_UNEXPECTED,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    finally:
        if bundle is not None:
            release_runtime(bundle)
        if supply is not None:
            try:
                supply.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------------
# 审计（纯读，无正文）
# ---------------------------------------------------------------------------------

def audit(root: Path | str) -> dict[str, Any]:
    root_path = Path(str(root))
    prereg = _read_json(root_path / EXPERIMENT_DIRNAME / PREREG_FILENAME)
    if not prereg:
        return {"ok": False, "reason_code": REASON_NOT_PREPARED, "root": str(root_path)}
    batch = BatchLedger(root_path).load()
    state_root = Path(str(prereg.get("state_root") or ""))
    ledger_view: dict[str, Any] = {"state": "UNAVAILABLE"}
    if state_root:
        try:
            from life_request_budget import LifeRequestBudget

            ledger_view = LifeRequestBudget(state_root, utc_day_max=300,
                                            run_window_max=TOTAL_REQUEST_CAP,
                                            run_id=RUN_ID).view()
        except Exception as exc:
            ledger_view = {"state": "UNAVAILABLE", "reason": type(exc).__name__}
    rows = list(batch.get("rows") or [])
    shadow_rows = [row for row in rows if row.get("stage") == "shadow"]
    classes: dict[str, int] = {}
    for row in shadow_rows:
        status = str(row.get("status") or "")
        classes[status] = classes.get(status, 0) + 1
    return {
        "ok": True, "root": str(root_path), "state": batch.get("state"),
        "run_started_at": (batch.get("run") or {}).get("started_at"),
        "stop": batch.get("stop"), "deadline": prereg.get("deadline"),
        "requests": {"run_window_calls": ledger_view.get("run_window_calls"),
                     "utc_day_calls": ledger_view.get("utc_day_calls"),
                     "total_cap": TOTAL_REQUEST_CAP,
                     "ledger_state": ledger_view.get("state")},
        "stages": {name: {"start_calls": (entry or {}).get("start_calls"),
                          "cap": STAGE_CAPS.get(name),
                          "rows": sum(1 for row in rows if row.get("stage") == name)}
                   for name, entry in (batch.get("stages") or {}).items()},
        "rounds": [{"stage": row.get("stage"), "index": row.get("index"),
                    "outcome": row.get("outcome"), "llm_calls": row.get("llm_calls"),
                    "decision": row.get("decision"), "h_ref": row.get("h_ref")}
                   for row in (batch.get("rounds") or [])],
        "shadow": {"rows": len(shadow_rows), "classes": classes,
                   "pairs": pair_summary(shadow_rows)},
        "frozen": batch.get("frozen"), "diagnostic": DIAGNOSTIC_DEFAULT,
        "note": "只读摘要：不含 prompt、响应正文、推理、环境变量或密钥。",
    }


# ---------------------------------------------------------------------------------
# 离线检查（0 产品调用）
# ---------------------------------------------------------------------------------

class CaptureTransport:
    """最底层 client 的替身：记下 client 实际要求的参数，回一条不联网的结果。"""

    #: 与真实 transport 一样的「发送/等待可分离」形状，否则预算包装器会按设计拒发。
    send_split_capable = True
    backend = "offline_capture"
    provider = "offline"
    model = "offline-capture"

    def __init__(self, text: str = "{}") -> None:
        self.calls: list[dict[str, Any]] = []
        self._text = text

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "calls": len(self.calls)}

    def begin_send(self, *, system_prompt: str, user_prompt: str,
                   timeout_s: Optional[float] = None,
                   max_output_tokens: Optional[int] = None) -> dict[str, Any]:
        return {"system_prompt": system_prompt, "user_prompt": user_prompt,
                "max_output_tokens": max_output_tokens}

    def finish_send(self, pending: Mapping[str, Any]) -> Any:
        return self.complete(system_prompt=str(pending.get("system_prompt") or ""),
                             user_prompt=str(pending.get("user_prompt") or ""),
                             max_output_tokens=pending.get("max_output_tokens"))

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        from agency_model_client import ModelCallResult

        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or "")})
        return ModelCallResult(purpose="offline", provider=self.provider, model=self.model,
                               text=self._text, latency_ms=1, status=200,
                               usage_source="none")


def _scripted_decision_payload(user_prompt: str) -> dict[str, Any]:
    """dry-run 专用的确定性策略：只读**真实渲染出来的** payload，按公布的规则作答。"""

    payload = json.loads(user_prompt)
    history = [row for row in (payload.get("prior_experiences") or [])
               if isinstance(row, dict)]
    neutral = bool(history) and all(
        NEUTRAL_SENTENCE[:12] in str(row.get("text") or "") for row in history)
    candidates = [row for row in (payload.get("candidates") or []) if isinstance(row, dict)]
    if neutral:
        return {"decision": "NO_ACTION", "reason_codes": ["NO_ACTION_CHOSEN"],
                "short_rationale": "scripted: neutral-history arm"}
    def _bare(ref: Any) -> str:
        text = str(ref or "")
        while text.startswith("artifact:"):
            text = text[len("artifact:"):]
        return text

    startable = [row for row in candidates
                 if "START" in (row.get("legal_decisions") or [])
                 and any(str(ref).startswith("artifact:")
                         for ref in (row.get("target_refs") or []))]
    if startable:
        # 优先挑历史里点过名的那件（refs 两臂相同，所以这不区分 A/B）。
        named = {_bare(row.get("artifact_ref")) for row in history if row.get("artifact_ref")}
        preferred = [row for row in startable
                     if any(_bare(ref) in named for ref in (row.get("target_refs") or []))]
        chosen = (preferred or startable)[0]
        return {"decision": "START", "selected_candidate_id": chosen["candidate_id"],
                "reason_codes": ["OPEN_ENDED_CONTINUATION"],
                "short_rationale": "scripted: continue the offered work"}
    for row in candidates:
        if "ABANDON" in (row.get("legal_decisions") or []):
            return {"decision": "ABANDON", "reason_codes": ["ABANDON_NONTERMINAL_CHOSEN"],
                    "short_rationale": "scripted: end the running activity"}
    return {"decision": "NO_ACTION", "reason_codes": ["NO_ACTION_CHOSEN"],
            "short_rationale": "scripted: no legal start"}


class ScriptedDecisionTransport(CaptureTransport):
    """dry-run 的决策替身：不联网，按 payload 里的候选与合法集作答。"""

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        from agency_model_client import ModelCallResult

        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or "")})
        try:
            answer = _scripted_decision_payload(user_prompt)
        except Exception:
            answer = {"decision": "NO_ACTION", "reason_codes": ["NO_ACTION_CHOSEN"]}
        return ModelCallResult(purpose="offline", provider=self.provider, model=self.model,
                               text=json.dumps(answer, ensure_ascii=False), latency_ms=1,
                               status=200, usage_source="none")


class ScriptedContentTransport(CaptureTransport):
    """dry-run 的内容替身：回一段总是非空的、与本次选择无关的中性文字。"""

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        from agency_model_client import ModelCallResult

        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or "")})
        answer = {"kind": "append_paragraph",
                  "text": "在练习材料末尾补了一句中性的说明文字。"}
        return ModelCallResult(purpose="offline", provider=self.provider, model=self.model,
                               text=json.dumps(answer, ensure_ascii=False), latency_ms=1,
                               status=200, usage_source="none")


def dry_run(root: Path | str, *, require_preregistered: bool = False) -> dict[str, Any]:
    """整条流程的离线演练（0 产品调用）。**不是实验结果**，只证明三段能真实走通。"""

    root_path = Path(str(root))
    if str(root_path.resolve()) == str(PREREG_ROOT.resolve()):
        return {"ok": False, "reason_code": REASON_ROOT_RESERVED,
                "detail": "dry-run 只在临时隔离根里跑"}
    result = run(root_path, execute_approved=True,
                 require_preregistered=require_preregistered,
                 cognition_factory=lambda stage, arm=None: ScriptedDecisionTransport(),
                 content_factory=lambda stage, arm=None: ScriptedContentTransport(),
                 env_overrides=dummy_env())
    result["scripted"] = True
    if isinstance(result.get("statistics"), dict):
        result["statistics"]["conclusion"] = "DRY_RUN_SCRIPTED_NOT_A_RESULT"
    if result.get("ok"):
        result["result"] = "DRY_RUN_SCRIPTED_NOT_A_RESULT"
    return result


def offline_dry_run_check() -> dict[str, Any]:
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="histchoice-dry-"))
    root = base / "instance"
    prepared = prepare(root, require_preregistered=False)
    if not prepared.get("ok"):
        return {"ok": False, "reason_code": prepared.get("reason_code"),
                "detail": prepared.get("detail")}
    result = dry_run(root, require_preregistered=False)
    acquisition = result.get("acquisition") or {}
    shadow = result.get("shadow") or {}
    statistics = result.get("statistics") or {}
    continuation = result.get("continuation") or {}
    stages = {arm: (continuation.get(arm) or {}).get("status") for arm in ("A", "B")}
    ok = bool(result.get("ok")
              and acquisition.get("h_ref")
              and (shadow.get("pairs") or []) and len(shadow.get("pairs") or []) == PAIR_COUNT
              and stages.get("A") == "RAN" and stages.get("B") == "RAN"
              and (result.get("requests_used") or 0) <= TOTAL_REQUEST_CAP
              and result.get("result") == "DRY_RUN_SCRIPTED_NOT_A_RESULT")
    return {"ok": ok, "result": result.get("result"),
            "reason_code": result.get("reason_code"), "detail": result.get("detail"),
            "h_ref_present": bool(acquisition.get("h_ref")),
            "acquisition_stop": acquisition.get("stop_reason"),
            "pairs_complete": statistics.get("pairs_complete"),
            "a_h": statistics.get("a_h"), "b_h": statistics.get("b_h"),
            "continuation": stages,
            "continuation_reasons": {arm: (continuation.get(arm) or {}).get("reason_code")
                                     for arm in ("A", "B")},
            "continuation_details": {arm: (continuation.get(arm) or {}).get("detail")
                                     for arm in ("A", "B")},
            "branch": continuation.get("branch"),
            "requests_used": result.get("requests_used"),
            "stop": (result.get("stop") or None),
            "dry_run_root": str(root)}


def dummy_env() -> dict[str, str]:
    return {"NYA02_MODEL_PROVIDER": "custom",
            "NYA02_MODEL_BASE_URL": "https://offline.invalid/v1",
            "NYA02_MODEL_NAME": "offline-model",
            "NYA02_MODEL_API_KEY": "offline-not-a-key",
            "CHIYO_AUTONOMOUS_RUN_ID": RUN_ID}


def measure_request_parameters() -> dict[str, Any]:
    """离线实测：决策/内容两条路真实会发出的 max_tokens、temperature、stream。"""

    from agency_decision_ag1 import CognitiveDecisionRequest
    from life_artifact_port import ArtifactResumePoint
    from profile_model_transport import OpenAICompatibleTransport

    env = dummy_env()
    decision_transport = CaptureTransport(
        text=json.dumps({"decision": "NO_ACTION", "reason_codes": ["NO_ACTION_CHOSEN"]}))
    cognition = raw_cognition_adapter(env, transport=decision_transport)
    request = CognitiveDecisionRequest(
        opportunity_id="opp:offline", candidate_set_id="cset:offline",
        context_snapshot_id="ctx:offline", snapshot_fingerprint="fp:offline",
        candidates=(), current_activity_summary=None,
        capacity_summary={"overall_capacity_state": "AVAILABLE"},
        legal_decision_enums=("NO_ACTION", "DEFER"),
        allowed_reason_codes=("NO_ACTION_CHOSEN",),
        observed_fact_keys=(), policy_version="ag0-v1")
    decision_cap = int(cognition.decision_output_cap(request))
    response = cognition.invoke(request)
    content_transport = CaptureTransport(text=json.dumps({"kind": "no_change", "text": ""}))
    writer = build_content_writer(env, budget=None, admission=None,
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
    wire = json.loads(OpenAICompatibleTransport(
        base_url="https://offline.invalid/v1", api_key="x", model="offline-model",
        temperature=MODEL_TEMPERATURE)._build_payload("s", "u", content_cap))
    wire_decision = json.loads(OpenAICompatibleTransport(
        base_url="https://offline.invalid/v1", api_key="x", model="offline-model",
        temperature=MODEL_TEMPERATURE)._build_payload("s", "u", decision_cap))
    actual = {"decision_max_tokens": int(wire_decision.get("max_tokens") or -1),
              "decision_temperature": wire_decision.get("temperature"),
              "decision_stream": wire_decision.get("stream"),
              "content_max_tokens": int(wire.get("max_tokens") or -1),
              "content_temperature": wire.get("temperature"),
              "content_stream": wire.get("stream"),
              "decision_cap_reported": decision_cap,
              "content_cap_reported": int(getattr(writer, "max_output_tokens", -1)),
              "decision_response_present": bool(getattr(response, "raw_output", None))}
    expected = {"decision_max_tokens": DECISION_OUTPUT_CAP,
                "decision_temperature": MODEL_TEMPERATURE, "decision_stream": MODEL_STREAM,
                "content_max_tokens": CONTENT_OUTPUT_CAP,
                "content_temperature": MODEL_TEMPERATURE, "content_stream": MODEL_STREAM}
    mismatches = {key: {"expected": expected[key], "actual": actual.get(key)}
                  for key in expected if actual.get(key) != expected[key]}
    return {"ok": not mismatches, "expected": expected, "actual": actual,
            "mismatches": mismatches}


def _run_unit_tests() -> dict[str, Any]:
    try:
        suite = unittest.TestLoader().discover(
            start_dir=str(REPO_ROOT / "tests"), pattern="test_history_choice_experiment.py")
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    return {"ok": bool(result.wasSuccessful()), "run": int(result.testsRun),
            "failures": len(result.failures), "errors": len(result.errors)}


def offline_check() -> dict[str, Any]:
    """所有离线可行性门；整段把 HTTP connect 换成「一碰就炸」，证明没有真实外呼。"""

    import http.client

    restore = _hard_disconnect(http.client.HTTPConnection, "connect")
    try:
        return _offline_check_gates()
    finally:
        restore()


def _offline_check_gates() -> dict[str, Any]:
    gates: dict[str, Any] = {"unit_tests": _run_unit_tests()}
    try:
        gates["request_parameters"] = measure_request_parameters()
    except Exception as exc:
        gates["request_parameters"] = {"ok": False,
                                       "reason": "%s: %s" % (type(exc).__name__, exc)}
    try:
        gates["structural"] = offline_structural_check()
    except Exception as exc:
        gates["structural"] = {"ok": False,
                               "reason": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    try:
        gates["dry_run"] = offline_dry_run_check()
    except Exception as exc:
        gates["dry_run"] = {"ok": False,
                            "reason": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    blocking = [name for name in ("unit_tests", "request_parameters", "structural",
                                  "dry_run")
                if not (gates.get(name) or {}).get("ok")]
    return {"ok": not blocking, "blocking": blocking, "gates": gates,
            "model_called": 0, "request_sent": 0, "formal_root_created": False}


def _hard_disconnect(cls: type, method: str) -> Callable[[], None]:
    """把最底层的 HTTP connect 换成「一碰就炸」，用来证明离线段真的没连出去。"""

    original = getattr(cls, method, None)

    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("OFFLINE_HTTP_FORBIDDEN")

    setattr(cls, method, _blocked)

    def restore() -> None:
        if original is not None:
            setattr(cls, method, original)

    return restore


def offline_structural_check() -> dict[str, Any]:
    """真实 owner + 正式 runtime + 假 cognition client：0 调用走到「两个可执行选择」。"""

    import http.client
    import tempfile

    restore = _hard_disconnect(http.client.HTTPConnection, "connect")
    # demo.prepare_demo 要求根**尚不存在**，所以 mkdtemp 只当它的父目录（不清理，按规矩保留）。
    scratch = Path(tempfile.mkdtemp(prefix="histchoice-off-")) / "instance"
    prepared = prepare(scratch, require_preregistered=False)
    if not prepared.get("ok"):
        restore()
        return {"ok": False, "reason_code": prepared.get("reason_code"),
                "detail": prepared.get("detail")}
    supply = None
    bundle = None
    try:
        supply = start_supply_owner(scratch, tag="offline")
        env = runtime_env(scratch)
        env.update({key: value for key, value in dummy_env().items()
                    if key.startswith("NYA02_MODEL_")})
        batch = BatchLedger(scratch)
        from life_request_budget import LifeRequestBudget

        state_root = Path(str(env.get("NYA02_RESIDENT_STATE") or ""))
        budget = LifeRequestBudget(state_root, utc_day_max=300,
                                   run_window_max=TOTAL_REQUEST_CAP, run_id=RUN_ID)
        budget.ensure_window(allow_new=True)
        batch.start_stage("acquisition", budget.view())
        bundle = build_experiment_runtime(
            env, root=scratch, state_root=state_root, budget=budget, ledger=batch,
            stage="acquisition", cognition_transport=CaptureTransport(
                text=json.dumps({"decision": "NO_ACTION",
                                 "reason_codes": ["NO_ACTION_CHOSEN"]})),
            content_transport=CaptureTransport())
        bundle["assembled_env"] = env
        works = prepared.get("works") or []
        h_ref = ("artifact:%s" % works[0]["artifact_id"]) if works else "artifact:unknown"
        frozen = freeze_scenario(bundle, h_ref=h_ref)
        # 故意**不传** transport：结构门必须靠自己的离线默认值走完，而不是靠调用方记得传替身。
        gate = structural_gate(bundle, frozen)
        return {"ok": bool(gate.get("ok")), "reason_code": gate.get("reason_code"),
                "offline_transport": gate.get("offline_transport"),
                "other_work_executable": gate.get("other_work_executable"),
                "other_work_count": gate.get("other_work_count"),
                "detail": gate.get("detail"),
                "result_status": gate.get("result_status"),
                "failure_class": gate.get("failure_class"),
                "candidate_count": gate.get("candidate_count"),
                "candidate_refs": gate.get("candidate_refs"),
                "candidate_records": gate.get("candidate_records"),
                "legal_decision_enums": gate.get("legal_decision_enums"),
                "executable_count": gate.get("executable_count"),
                "h_executable": gate.get("h_executable"),
                "other_executable": gate.get("other_executable"),
                "no_action_legal": gate.get("no_action_legal"),
                "candidates": gate.get("candidate_ids"),
                "candidate_targets": gate.get("candidate_targets"),
                "works": [row.get("artifact_id") for row in works],
                "capture_decision": gate.get("capture_decision"),
                "requests_sent": BatchLedger.spent(budget.view()),
                "scratch_root": str(scratch)}
    finally:
        if bundle is not None:
            release_runtime(bundle)
        if supply is not None:
            try:
                supply.stop()
            except Exception:
                pass
        restore()


# ---------------------------------------------------------------------------------
# run：把三段串起来（唯一可能发真实请求的入口）
# ---------------------------------------------------------------------------------

@contextlib.contextmanager
def stage_ceiling(budget: Any, stage: str) -> Any:
    """把一个阶段的上限**落到持久账本自身**：本阶段最多再花 ``STAGE_CAPS[stage]`` 次。

    这是硬的：决策和内容共用同一本账本，所以一个回合的两次请求也不可能越过阶段上限；
    阶段结束后恢复原本的总额度（整批 60 仍在）。
    """

    try:
        start = BatchLedger.spent(budget.view())
    except BatchRefused:
        raise
    previous = budget.run_window_max
    base = TOTAL_REQUEST_CAP if previous is None else min(int(previous), TOTAL_REQUEST_CAP)
    budget.run_window_max = min(base, start + STAGE_CAPS[stage])
    try:
        yield {"start": start, "ceiling": budget.run_window_max}
    finally:
        budget.run_window_max = previous


@contextlib.contextmanager
def arm_ceiling(budget: Any, limit: int = ARM_REQUEST_CAP) -> Any:
    """把**单臂**上限落到同一本持久账本上：本回合最多再花 ``limit`` 次（默认 2）。

    与 :func:`stage_ceiling` 同一个做法，只是起点取「本回合开始时的已用数」，所以 A、B
    两臂各自独立（B 的起点是 A 结束后的计数）。决策与内容共用这一本账本，任何第 3 次
    发送都会在 ``LifeRequestBudget.reserve()`` 处被拒，而不是发完再查。只临时压低
    ceiling，不重置/不退款计数；正常返回或抛异常都在 ``finally`` 里恢复原值。
    """

    if budget is None or not hasattr(budget, "run_window_max"):
        yield {"start": None, "ceiling": None}
        return
    start = BatchLedger.spent(budget.view())
    previous = budget.run_window_max
    base = TOTAL_REQUEST_CAP if previous is None else min(int(previous), TOTAL_REQUEST_CAP)
    budget.run_window_max = min(base, start + int(limit))
    try:
        yield {"start": start, "ceiling": budget.run_window_max}
    finally:
        budget.run_window_max = previous


def run(root: Path | str, *, execute_approved: bool,
        require_preregistered: bool = True,
        cognition_factory: Optional[Callable[..., Any]] = None,
        content_factory: Optional[Callable[..., Any]] = None,
        env_overrides: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    root_path = Path(str(root))
    refusal = _check_root(root_path, require_preregistered=require_preregistered)
    if refusal is not None:
        return refusal
    prereg = _read_json(root_path / EXPERIMENT_DIRNAME / PREREG_FILENAME)
    if not prereg:
        return {"ok": False, "reason_code": REASON_NOT_PREPARED, "root": str(root_path)}
    if not execute_approved:
        return {"ok": False, "reason_code": REASON_EXECUTE_FLAG_MISSING,
                "detail": "run 只在显式 --execute-approved 下执行，且只能用一次"}
    if _deadline_passed():
        return {"ok": False, "reason_code": REASON_DEADLINE_PASSED,
                "deadline": DEADLINE.isoformat()}
    ledger = BatchLedger(root_path)
    batch = ledger.load()
    if (batch.get("run") or {}).get("started_at"):
        return {"ok": False, "reason_code": REASON_ALREADY_STARTED,
                "started_at": (batch.get("run") or {}).get("started_at")}
    if batch.get("state") != "prepared":
        return {"ok": False, "reason_code": REASON_NOT_PREPARED, "state": batch.get("state")}

    params = measure_request_parameters()
    if not params.get("ok"):
        ledger.mark_stopped(REASON_PARAM_MISMATCH)
        return {"ok": False, "reason_code": REASON_PARAM_MISMATCH,
                "mismatches": params.get("mismatches"), "actual": params.get("actual")}

    state_root = Path(str(prereg.get("state_root") or ""))
    from life_request_budget import LifeRequestBudget

    budget = LifeRequestBudget(state_root, utc_day_max=300,
                               run_window_max=TOTAL_REQUEST_CAP, run_id=RUN_ID)
    try:
        budget.ensure_window(allow_new=False)
        view = budget.view()
    except Exception as exc:
        return {"ok": False, "reason_code": REASON_LEDGER_UNREADABLE,
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    if BatchLedger.spent(view) != 0:
        return {"ok": False, "reason_code": REASON_LEDGER_NOT_ZERO,
                "run_window_calls": view.get("run_window_calls")}

    batch["run"] = {"started_at": _iso(), "status": "running"}
    batch["state"] = "running"
    ledger.save(batch)

    supply = None
    bundle = None
    try:
        # owner 先起、再把 live socket 写进实例文件，绑定才指向活着的进程。
        supply = start_supply_owner(root_path, tag="run")
        env = runtime_env(root_path)
        if env_overrides:
            env.update({str(key): str(value) for key, value in env_overrides.items()})
        if model_config(env) is None:
            raise BatchRefused(REASON_NO_MODEL_CONFIG, "profile resolves no usable provider")
        ledger.start_stage("acquisition", budget.view())
        latch = SendLatch(_deadline_passed)
        bundle = build_experiment_runtime(
            env, root=root_path, state_root=state_root, budget=budget, ledger=ledger,
            stage="acquisition", latch=latch,
            cognition_transport=(cognition_factory("acquisition", None)
                                 if cognition_factory else None),
            content_transport=(content_factory("acquisition", None)
                               if content_factory else None))
        bundle["assembled_env"] = env
        last_gate: dict[str, Any] = {}

        def try_freeze(candidate_h: str) -> Optional[dict[str, Any]]:
            """冻结一次 + 跑结构门；不可比就返回 None，让采集继续（预登记 §2）。"""

            scenario = freeze_scenario(bundle, h_ref=candidate_h)
            bare = candidate_h[len("artifact:"):] if candidate_h.startswith("artifact:") else candidate_h
            for _attempt in range(3):
                if any(bare in str(item.get("artifact_ref") or "")
                       for item in scenario["prior_experiences"]):
                    break
                time.sleep(0.2)
                scenario["prior_experiences"] = list(
                    bundle["cycle"].consideration_context() or [])
            if not any(bare in str(item.get("artifact_ref") or "")
                       for item in scenario["prior_experiences"]):
                last_gate["reason_code"] = REASON_HISTORY_NOT_ADMITTED
                return None
            attempt_gate = structural_gate(bundle, scenario)
            last_gate.clear()
            last_gate.update(attempt_gate)
            if not attempt_gate.get("ok"):
                return None
            return {"frozen": scenario, "gate": attempt_gate}

        with stage_ceiling(budget, "acquisition"):
            acquisition = run_acquisition(bundle, ledger=ledger, budget=budget,
                                          try_freeze=try_freeze,
                                          deadline_check=_deadline_passed, latch=latch)
        frozen = acquisition.get("frozen")
        gate = acquisition.get("gate")
        h_ref = acquisition.get("h_ref")
        if not h_ref:
            reason = acquisition.get("stop_reason") or REASON_NO_EFFECTIVE_HISTORY
            ledger.mark_stopped(reason)
            ledger.mark_finished("no_effective_history")
            return {"ok": True, "result": "NO_EFFECTIVE_HISTORY",
                    "acquisition": acquisition,
                    "requests_used": BatchLedger.spent(budget.view())}
        if frozen is None or gate is None:
            reason = str(last_gate.get("reason_code") or REASON_SCENARIO_NOT_IDENTIFIABLE)
            ledger.mark_stopped(reason)
            ledger.mark_finished("scenario_not_identifiable")
            return {"ok": True, "result": ("NO_EFFECTIVE_HISTORY"
                                           if reason == REASON_HISTORY_NOT_ADMITTED
                                           else "SCENARIO_NOT_IDENTIFIABLE"),
                    "acquisition": acquisition, "structural": last_gate,
                    "requests_used": BatchLedger.spent(budget.view())}

        data = ledger.load()
        data["frozen"] = {
            "at": _iso(), "observed_at": frozen["observed_at"], "h_ref": h_ref,
            "candidate_count": len(frozen["candidate_refs"]),
            "candidate_ids": list(frozen["candidate_refs"]),
            "prior_experience_count": len(frozen["prior_experiences"]),
            "prior_experience_refs": [
                {"artifact_ref": item.get("artifact_ref"),
                 "version_ref": item.get("version_ref"),
                 "text_chars": len(str(item.get("text") or ""))}
                for item in frozen["prior_experiences"]],
            "prompt_fingerprint": gate.get("baseline", {}).get("fingerprint"),
            "prompt_basis": gate.get("baseline", {}).get("basis"),
        }
        ledger.save(data)

        ledger.start_stage("shadow", budget.view())
        shadow = run_shadow(bundle, frozen=frozen, baseline=gate.get("baseline") or {},
                            ledger=ledger, budget=budget, env=env,
                            deadline_check=_deadline_passed, latch=latch,
                            cognition_factory=cognition_factory)
        # 影子里任何 stop（截止/额度/漂移/第二变量/供应商失败）都直接收成 partial：
        # 承接两臂 NOT_RUN，一次额外请求都不发。
        blocked = shadow_blocks_continuation(shadow, latch)
        if blocked:
            continuation = skipped_continuation(
                blocked, requests_used=BatchLedger.spent(budget.view()))
        else:
            ledger.start_stage("continuation", budget.view())
            with stage_ceiling(budget, "continuation"):
                continuation = run_continuation(bundle, ledger=ledger, budget=budget,
                                                root=root_path,
                                                deadline_check=_deadline_passed,
                                                cognition_factory=cognition_factory,
                                                content_factory=content_factory,
                                                env_overrides=env_overrides, latch=latch)

        stats = shadow_statistics(shadow, continuation)
        ledger.mark_finished("completed" if not shadow.get("stop_reason") else "partial")
        return {"ok": True, "result": stats.get("conclusion"), "acquisition": acquisition,
                "structural": {key: value for key, value in gate.items() if key != "baseline"},
                "shadow": {"pairs": shadow.get("pairs"),
                           "stop_reason": shadow.get("stop_reason"),
                           "requests_used": shadow.get("requests_used")},
                "continuation": continuation, "statistics": stats,
                "requests_used": BatchLedger.spent(budget.view()),
                "deadline": DEADLINE.isoformat()}
    except BatchRefused as exc:
        ledger.mark_stopped(exc.reason_code, exc.detail)
        return {"ok": False, "reason_code": exc.reason_code, "detail": exc.detail}
    except Exception as exc:
        ledger.mark_stopped(REASON_UNEXPECTED, "%s: %s" % (type(exc).__name__, exc))
        return {"ok": False, "reason_code": REASON_UNEXPECTED,
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    finally:
        if bundle is not None:
            release_runtime(bundle)
        if supply is not None:
            try:
                supply.stop()
            except Exception:
                pass


def shadow_statistics(shadow: Mapping[str, Any],
                      continuation: Mapping[str, Any]) -> dict[str, Any]:
    """按预登记 §6 事先写死的规则出结论，不补样、不筛样本。"""

    samples = list(shadow.get("samples") or [])
    by_pair: dict[int, dict[str, Any]] = {}
    for sample in samples:
        by_pair.setdefault(int(sample.get("pair") or 0), {})[str(sample.get("arm"))] = sample
    complete = [pair for pair, arms in by_pair.items() if "A" in arms and "B" in arms]
    a_h = sum(1 for arms in by_pair.values()
              if (arms.get("A") or {}).get("classification") == CLASS_H)
    b_h = sum(1 for arms in by_pair.values()
              if (arms.get("B") or {}).get("classification") == CLASS_H)
    discordant_a = sum(1 for arms in by_pair.values()
                       if (arms.get("A") or {}).get("classification") == CLASS_H
                       and (arms.get("B") or {}).get("classification") != CLASS_H)
    discordant_b = sum(1 for arms in by_pair.values()
                       if (arms.get("A") or {}).get("classification") != CLASS_H
                       and (arms.get("B") or {}).get("classification") == CLASS_H)
    delta = pair_delta(PAIR_COUNT, a_h, b_h)
    p_value = mcnemar_exact_two_sided(discordant_a, discordant_b) \
        if len(complete) == PAIR_COUNT else None
    failures = sum(1 for sample in samples
                   if sample.get("classification") in (CLASS_FAILURE, CLASS_INVALID))
    conclusion = "NO_FIXED_CONCLUSION"
    if not samples:
        conclusion = "SHADOW_NOT_RUN"
    elif len(complete) == PAIR_COUNT and not shadow.get("stop_reason"):
        if p_value is not None and p_value < 0.05 \
                and abs(delta["delta_count"]) >= (delta["threshold_count"] or 0):
            conclusion = "HISTORY_PACKAGE_CHANGED_H_CHOICE"
        else:
            conclusion = "NO_DETECTED_DIFFERENCE"
    return {"pairs_complete": len(complete), "a_h": a_h, "b_h": b_h,
            "discordant_a_only": discordant_a, "discordant_b_only": discordant_b,
            "mcnemar_two_sided_p": p_value, "delta": delta, "invalid_or_failed": failures,
            "shadow_stop_reason": shadow.get("stop_reason"),
            "continuation": {arm: {"status": (continuation.get(arm) or {}).get("status"),
                                   "reason_code": (continuation.get(arm) or {}).get("reason_code"),
                                   "requests": (continuation.get(arm) or {}).get("requests")}
                             for arm in ("A", "B")},
            "conclusion": conclusion}


# ---------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------

def _emit(payload: Mapping[str, Any], *, code: int) -> int:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, indent=1, sort_keys=True)
                     + "\n")
    sys.stdout.flush()
    return int(code)


def cmd_offline_check(argv: Sequence[str]) -> int:
    result = offline_check()
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_prepare(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="history_choice_experiment prepare")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(list(argv))
    result = prepare(Path(args.root), require_preregistered=True)
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="history_choice_experiment run")
    parser.add_argument("--root", required=True)
    parser.add_argument("--execute-approved", action="store_true")
    args = parser.parse_args(list(argv))
    result = run(Path(args.root), execute_approved=bool(args.execute_approved),
                 require_preregistered=True)
    if result.get("ok"):
        code = EXIT_OK
    elif result.get("reason_code"):
        code = EXIT_REFUSED
    else:
        code = EXIT_ERROR
    return _emit(result, code=code)


def cmd_audit(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="history_choice_experiment audit")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(list(argv))
    result = audit(Path(args.root))
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


COMMANDS = {"offline-check": cmd_offline_check, "prepare": cmd_prepare, "run": cmd_run,
            "audit": cmd_audit}


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

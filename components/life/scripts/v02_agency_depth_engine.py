#!/usr/bin/env python3
"""v0.2 品味演化、短期饱和、双向冲突搁置、三类后悔、二阶意愿小规矩与开放慢孵化引擎。

对标设计覆盖（F-125 ~ F-131, F-134）：
1. 真偏好 vs 被迫单选/迎合分离（F-127, F-128, F-134）：
   - 仅 ``choice_mode == "spontaneous"`` 且有效去重选项数 ``>= 2`` 才推进
     ``UNKNOWN -> TENTATIVE -> SUPPORTED``；
   - ``requested / constrained / forced_single`` 或单选（有效选项 ``< 2``）绝不升格 ``SUPPORTED``。
2. 长期喜欢 vs 短期看腻饱和分离（F-130）：
   - 连续重复接触推高 ``satiation_score >= 0.70`` 进入 ``saturated``，候选权重乘数降至 ``0.25``，
     但长期偏好 ``status="SUPPORTED"`` 保持不变；
   - 饱和度按连续时间指数衰减（绝不跨午夜清零），冷却后再次自发重拾进入 ``rediscovered``（权重 ``1.1``）。
3. 双向冲突搁置（F-126）：``park`` 保留双边动机并存，不强行二选一。
4. 三类后悔严格二分（F-129）：
   - ``outcome_disappointment`` 不推翻合理过程（``changes_method_policy=False``）；
   - ``process_mistake`` 记录方法调整（``changes_method_policy=True``）；
   - ``opportunity_loss`` 缺少可观测替代事实与反事实说明时硬拒绝 ``409 MISSING_OBSERVABLE_COUNTERFACTUAL``；
   - 伴侣视角强制 ``regret_reflections=[]`` 且 ``regret_reflections_withheld=True``。
5. 二阶意愿小规矩（F-131）：伴侣温和提议仅处于 ``proposed``，``auto_locked`` 恒为 ``False``，随时可修订或释放。
6. 开放问题慢孵化（F-125）：纯时间流逝无新证据绝不假顿悟，仅新证据或伴侣新线索推进 ``ready_to_revisit``。

红线保证：全程零外部大模型调用（``llm_calls == 0``），读视图与背景摘要 100% 零磁盘写入。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORLD_SCRIPTS_DIR = _REPO_ROOT / "components" / "world" / "scripts"
for _path_entry in (str(_REPO_ROOT), str(_WORLD_SCRIPTS_DIR)):
    if _path_entry not in sys.path:
        sys.path.insert(0, _path_entry)

try:
    from components.life.scripts import state_tx_guard as _tx_guard
except ImportError:  # pragma: no cover - flat life/scripts import path
    import state_tx_guard as _tx_guard  # type: ignore[no-redef]

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover - flat world/scripts import path
    import instance_paths as _instance_paths  # type: ignore[no-redef]

VIEW_SCHEMA = "nyairo.v02.agency_depth.v1"
STATE_REL_PATH = ("state", "v02_agency_depth_state.json")

CHOICE_MODES: frozenset[str] = frozenset({
    "spontaneous",
    "requested",
    "constrained",
    "forced_single",
})

PREFERENCE_STATUSES: frozenset[str] = frozenset({
    "UNKNOWN",
    "TENTATIVE",
    "SUPPORTED",
})

CONFLICT_OPS: frozenset[str] = frozenset({
    "park",
    "lean",
    "settle",
})

REGRET_KINDS: frozenset[str] = frozenset({
    "outcome_disappointment",
    "process_mistake",
    "opportunity_loss",
})

SECOND_ORDER_RULE_OPS: frozenset[str] = frozenset({
    "propose",
    "start_trial",
    "endorse",
    "revise",
    "release",
})

INCUBATION_OPS: frozenset[str] = frozenset({
    "open",
    "add_evidence",
    "check_elapsed",
    "settle",
})

# v0.8 Wave 7 / 15 章交付包 8：有界 Inquiry 生命周期（问题→证据→收敛/停止）。
INQUIRY_STATUS_OPEN = "OPEN"
INQUIRY_STATUS_INVESTIGATING = "INVESTIGATING"
INQUIRY_STATUS_CONCLUDED = "CONCLUDED"
INQUIRY_STATUS_STOPPED = "STOPPED"
INQUIRY_ACTIVE_STATUSES: frozenset[str] = frozenset({
    INQUIRY_STATUS_OPEN,
    INQUIRY_STATUS_INVESTIGATING,
})
INQUIRY_TERMINAL_STATUSES: frozenset[str] = frozenset({
    INQUIRY_STATUS_CONCLUDED,
    INQUIRY_STATUS_STOPPED,
})
INQUIRY_STATUSES: frozenset[str] = (
    INQUIRY_ACTIVE_STATUSES | INQUIRY_TERMINAL_STATUSES
)
MAX_OPEN_INQUIRIES = 5
MAX_EVIDENCE_NOTES_PER_INQUIRY = 8
MAX_CANDIDATE_EXPLANATIONS = 4
MAX_PARTNER_CLUES_PER_INQUIRY = 4
DEFAULT_INQUIRY_STEP_BUDGET = 6
MIN_INQUIRY_STEP_BUDGET = 1
MAX_INQUIRY_STEP_BUDGET = 12
INQUIRY_STOP_REASON_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
INQUIRY_STANCES: frozenset[str] = frozenset({
    "plausible",
    "supported",
    "ruled_out",
})

SATIATION_THRESHOLD = 0.70
SATIATION_HALF_LIFE_HOURS = 16.0
SATIATION_DECAY_LAMBDA = math.log(2.0) / SATIATION_HALF_LIFE_HOURS
SATURATED_WEIGHT_MULTIPLIER = 0.25
REDISCOVERED_WEIGHT_MULTIPLIER = 1.1

# Ch.15 §7 (D01/D08/D09/D10) & §10 (C/D/H) 数字原生与日常自主机制闭集常量。
INFO_CURATION_MODES: frozenset[str] = frozenset({
    "pin",
    "mute",
    "filter",
    "normal",
})
FUTURE_CAPSULE_DECISIONS: frozenset[str] = frozenset({
    "open",
    "decline_no_debt",
    "keep_closed",
})
MAX_OPTIONAL_GESTURE_LEN = 80
OPTIONAL_GESTURE_DECISIONS: frozenset[str] = frozenset({
    "adopt_step",
    "reframe_question",
    "archive_no_debt",
})
LEGACY_TOOL_ENDINGS: frozenset[str] = frozenset({
    "keep_repaired",
    "adapt_to_static_card",
    "bid_farewell",
})
RESPONSIBILITY_TARGET_KINDS: frozenset[str] = frozenset({
    "private_concern",
    "shared_partner_commitment",
})
RESPONSIBILITY_REVIEW_DECISIONS: frozenset[str] = frozenset({
    "one_small_step",
    "pause_auto_check",
    "retire_keep_history",
})
COMMITMENT_TRADEOFF_DECISIONS: frozenset[str] = frozenset({
    "keep_and_adapt",
    "rescale_with_partner",
    "hand_off_and_retire",
})
ACTIVE_SECOND_ORDER_RULE_STATUSES: frozenset[str] = frozenset({
    "trial",
    "trialing",
    "endorsed",
    "active",
})
MAX_RULE_ENFORCEMENTS = 40
LOCAL_TZ = timezone(timedelta(hours=8))
_WINDOW_HHMM_RANGE_RE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*[-~至到]\s*(\d{1,2})(?::(\d{2}))?"
)
_SAFE_S03_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SECOND_ORDER_SCOPE_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("重活", "高强度", "冲刺", "重认知", "加班", "大改", "sprint", "strenuous", "heavy", "intensive"),
        (
            "strenuous",
            "sprint",
            "creative_intensive",
            "heavy",
            "advance_workbench_draft",
            "workbench",
            "重活",
            "高强度",
            "冲刺",
            "大改",
            "长篇",
            "加班",
            "重认知",
        ),
    ),
    (
        ("外出", "远门", "出门", "远行", "逛街", "集市", "散步", "outing", "walk", "outside"),
        (
            "outing",
            "strenuous_outing",
            "strenuous_outing_walk",
            "requires_outing",
            "walk",
            "market",
            "visit",
            "外出",
            "远门",
            "出门",
            "散步",
            "集市",
            "逛街",
        ),
    ),
    (
        ("花钱", "消费", "购物", "买东西", "网购", "冲动消费", "spend", "purchase", "shopping", "buy"),
        (
            "purchase",
            "impulse_purchase",
            "non_essential_purchase",
            "shopping",
            "buy",
            "spend",
            "花钱",
            "消费",
            "购物",
            "购买",
            "买",
        ),
    ),
    (
        ("熬夜", "晚睡", "早点睡", "不熬夜", "收工", "late_night", "stay_up"),
        (
            "advance_workbench_draft",
            "creative_intensive",
            "strenuous_outing",
            "strenuous_outing_walk",
            "requires_outing",
            "non_essential_purchase",
            "workbench",
            "outing",
            "sprint",
            "heavy",
            "late_night",
            "熬夜",
            "重活",
            "冲刺",
            "大改",
            "外出",
        ),
    ),
)
_RULE_ASCII_STOPWORDS: frozenset[str] = frozenset({
    "the",
    "and",
    "for",
    "not",
    "with",
    "rule",
    "after",
    "before",
    "night",
    "late",
    "morning",
    "evening",
    "afternoon",
    "day",
    "all",
    "time",
    "anytime",
    "daily",
    "item",
    "task",
})
_RULE_CN_TIME_AND_FILLER: tuple[str, ...] = (
    "深夜",
    "晚间",
    "夜晚",
    "夜间",
    "晚上",
    "睡前",
    "清晨",
    "早晨",
    "早上",
    "上午",
    "午休",
    "中午",
    "下午",
    "午后",
    "全天",
    "随时",
    "十点后",
    "九点后",
    "十一点后",
    "十点",
    "九点",
    "十一点",
    "以后",
    "之后",
    "之前",
    "不要",
    "避免",
    "禁止",
    "停止",
    "暂停",
    "不新开",
    "不安排",
    "不碰",
    "不做",
    "少做",
    "温和",
    "建议",
    "提醒",
    "规矩",
    "自己",
    "休息",
    "保护",
    "精力",
    "注意",
)
_RULE_CN_LEADING_VERBS = frozenset({"喝", "吃", "买", "逛", "看", "做", "写", "开", "碰", "刷", "打", "回", "聊"})

FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "actor",
    "has_subject_capability",
    "capability_token",
    "subject_id",
    "grant_id",
    "private_override",
    "intimacy_score",
    "affection_score",
    "love_points",
    "obedience_score",
    "auto_locked",
    "__proto__",
    "constructor",
    "prototype",
})


class V02AgencyDepthError(Exception):
    """v0.2 品味与心事深度引擎结构化业务异常。"""

    def __init__(self, status_code: int, reason_code: str, message: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        super().__init__(f"{self.reason_code}: {self.message}")


class V02ValidationError(V02AgencyDepthError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(400, reason_code, message)


class V02PermissionError(V02AgencyDepthError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(403, reason_code, message)


class V02NotFoundError(V02AgencyDepthError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(404, reason_code, message)


class V02ConflictError(V02AgencyDepthError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(409, reason_code, message)


def _now_iso_str(now_iso: Optional[str] = None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        parsed = _parse_iso_utc(now_iso.strip())
        if parsed is None:
            raise V02ValidationError("INVALID_FIELDS", f"Malformed ISO timestamp: {now_iso!r}")
        return now_iso.strip()
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso_utc(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    txt = raw.strip()
    if txt.endswith("Z") or txt.endswith("z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours_between(earlier_iso: Any, later_iso: Any) -> float:
    dt_early = _parse_iso_utc(earlier_iso)
    dt_late = _parse_iso_utc(later_iso)
    if dt_early is None or dt_late is None:
        return 0.0
    diff_sec = (dt_late - dt_early).total_seconds()
    return max(0.0, diff_sec / 3600.0)


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _is_strict_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))


def _require_clean_str(value: Any, field_name: str, *, max_len: int = 240) -> str:
    if not isinstance(value, str):
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a string")
    if "\x00" in value:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} contains NUL byte")
    trimmed = value.strip()
    if not trimmed:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} must not be blank")
    if len(trimmed) > max_len:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} exceeds max length {max_len}")
    return trimmed


def _optional_clean_str(value: Any, field_name: str, *, max_len: int = 320) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a string")
    if "\x00" in value:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} contains NUL byte")
    trimmed = value.strip()
    if len(trimmed) > max_len:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} exceeds max length {max_len}")
    return trimmed


def _optional_bool(value: Any, field_name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a boolean")
    return value


def _decay_satiation_score(raw_score: float, elapsed_hours: float) -> float:
    base = max(0.0, min(1.0, float(raw_score)))
    if elapsed_hours <= 0.0 or base <= 0.0:
        return round(base, 4)
    decayed = base * math.exp(-SATIATION_DECAY_LAMBDA * float(elapsed_hours))
    if decayed < 1e-4:
        return 0.0
    return round(min(1.0, decayed), 4)


def _derive_satiation_metrics(
    score: float,
    *,
    rediscovered_active: bool,
) -> tuple[str, bool, float]:
    """根据当前饱和分与是否处于冷却后自发重拾态计算饱和状态与候选权重乘数。"""
    clamped = max(0.0, min(1.0, float(score)))
    if clamped >= SATIATION_THRESHOLD:
        return "saturated", True, SATURATED_WEIGHT_MULTIPLIER
    if rediscovered_active:
        return "rediscovered", False, REDISCOVERED_WEIGHT_MULTIPLIER
    if clamped >= 0.35:
        mult = round(max(0.55, 1.0 - 0.5 * clamped), 3)
        return "cooling", False, mult
    return "fresh", False, 1.0


def _evaluate_preference_at_time(pref: Mapping[str, Any], obs_iso: str) -> dict[str, Any]:
    """零写实时评估单条偏好在 ``obs_iso`` 时刻的连续衰减饱和度与候选权重。"""
    doc = copy.deepcopy(dict(pref))
    stored_score = float(doc.get("satiation_score") or 0.0)
    anchor_iso = doc.get("last_satiation_at") or doc.get("updated_at")
    dt_hours = _hours_between(anchor_iso, obs_iso)
    live_score = _decay_satiation_score(stored_score, dt_hours)

    rediscovered_flag = bool(doc.get("rediscovered", False)) and live_score < SATIATION_THRESHOLD
    sat_state, is_sat, weight_mult = _derive_satiation_metrics(
        live_score,
        rediscovered_active=rediscovered_flag,
    )

    doc["satiation_score"] = live_score
    doc["satiation_state"] = sat_state
    doc["saturated"] = is_sat
    doc["rediscovered"] = rediscovered_flag
    doc["candidate_weight_multiplier"] = weight_mult
    doc["midnight_reset_applied"] = False
    return doc


def _resolve_paths_zero_write(day_root: Path | str) -> Any:
    return _instance_paths.resolve_instance_paths(day_root)


def _state_file_path(day_root: Path | str) -> Path:
    return _resolve_paths_zero_write(day_root).agency_depth_state_json


def _state_lock_path(day_root: Path | str) -> Path:
    # 锁文件放在独立的 .locks 目录，避免在 state/ 下留下额外的 .lock 干扰原子目录检查。
    return _resolve_paths_zero_write(day_root).day_root / ".locks" / "v02_agency_depth_state.json"


def _empty_raw_state() -> dict[str, Any]:
    return {
        "schema": VIEW_SCHEMA,
        "revision": 0,
        "updated_at": None,
        "next_seq": 1,
        "preferences": [],
        "choice_episodes": [],
        "partner_options": [],
        "parked_conflicts": [],
        "open_incubations": [],
        "second_order_rules": [],
        "method_adjustments": [],
        "regret_reflections": [],
        "inquiries": [],
        "placed_memory_cues": [],
        "info_environment_curations": [],
        "future_self_capsules": [],
        "optional_next_gestures": [],
        "legacy_tool_endings": [],
        "gentle_responsibility_reviews": [],
        "belief_revisions": [],
        "rule_enforcements": [],
        "commitment_tradeoffs": [],
        "operation_ledger": {},
    }


def _load_state_readonly(day_root: Path | str) -> tuple[bool, dict[str, Any]]:
    """100% 零磁盘写入读取落盘状态（零建目录、零临时文件、零 mtime 触碰）。"""
    paths = _resolve_paths_zero_write(day_root)
    canonical_path = paths.agency_depth_state_json
    legacy_path = paths.legacy_nested_agency_depth_json
    if canonical_path.exists() or canonical_path.is_symlink():
        path = canonical_path
    elif legacy_path != canonical_path and (legacy_path.exists() or legacy_path.is_symlink()):
        path = legacy_path
    else:
        return False, _empty_raw_state()
    if path.is_symlink() or not path.is_file():
        raise V02AgencyDepthError(503, "CORRUPT_STATE_FILE", "State path is not a regular file")
    try:
        raw_text = path.read_text(encoding="utf-8")
        doc = json.loads(raw_text)
    except (OSError, UnicodeError, ValueError) as exc:
        raise V02AgencyDepthError(503, "CORRUPT_STATE_FILE", f"Corrupted agency depth state JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != VIEW_SCHEMA:
        raise V02AgencyDepthError(503, "CORRUPT_STATE_FILE", "Invalid schema in agency depth state file")
    merged = _empty_raw_state()
    merged.update(copy.deepcopy(doc))
    return True, merged


def _atomic_write_state(day_root: Path | str, state: Mapping[str, Any]) -> None:
    target = _state_file_path(day_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(dict(state), ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".v02_agency_depth_", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _allocate_id(state: dict[str, Any], prefix: str) -> str:
    seq = int(state.get("next_seq") or 1)
    state["next_seq"] = seq + 1
    return f"{prefix}_{seq:04d}"


def _compute_payload_fingerprint(actor_side: str, payload: Mapping[str, Any]) -> str:
    filtered = {
        k: v
        for k, v in sorted(payload.items())
        if k not in ("operation_key", "expected_revision")
    }
    blob = json.dumps({"actor_side": actor_side, "payload": filtered}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _filter_visible_items(
    items: Any,
    *,
    has_subject_capability: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw_item in items or []:
        if not isinstance(raw_item, Mapping):
            continue
        is_shared = bool(raw_item.get("shared", False))
        if not has_subject_capability and not is_shared:
            continue
        out.append(copy.deepcopy(dict(raw_item)))
    return out


def _is_placed_cue_active(cue: Mapping[str, Any]) -> bool:
    """仅当可见、未遮挡、未移除且仍在放置点未移走时激活摆放线索（D01）。"""
    return (
        cue.get("visible") is True
        and not bool(cue.get("occluded", False))
        and not bool(cue.get("removed", False))
        and bool(str(cue.get("spot_id") or "").strip())
        and not bool(cue.get("moved_away", False))
    )


def _project_placed_memory_cues(
    raw_cues: Any,
    *,
    has_subject_capability: bool,
) -> dict[str, Any]:
    """投影 D01 摆放记忆线索：伴侣剥离未共享项，``active_cues`` 严控可见触发。"""
    items = _filter_visible_items(raw_cues, has_subject_capability=has_subject_capability)
    active_cues: list[dict[str, Any]] = []
    for item in items:
        active = _is_placed_cue_active(item)
        item["active"] = active
        if active:
            active_cues.append(copy.deepcopy(item))
    return {
        "active_cues": active_cues,
        "items": items,
    }


def _project_info_environment_curations(
    raw_curations: Any,
    *,
    has_subject_capability: bool,
) -> dict[str, Any]:
    """投影 D08 信息环境塑形：输出过滤流（pin 置顶/normal 在后）与强制保留的未过滤对照入口。"""
    items = _filter_visible_items(raw_curations, has_subject_capability=has_subject_capability)
    pinned = [copy.deepcopy(entry) for entry in items if entry.get("mode") == "pin"]
    normal = [copy.deepcopy(entry) for entry in items if entry.get("mode") == "normal"]
    return {
        "curated_feed": pinned + normal,
        "unfiltered_control_entries": [copy.deepcopy(entry) for entry in items],
        "unfiltered_control_available": True,
    }


def _evaluate_future_capsule_at_time(cap: Mapping[str, Any], obs_iso: str) -> dict[str, Any]:
    """零写评估 D09 未来自己的时间胶囊：到时只变成 ``AVAILABLE``，绝不自动认领。"""
    doc = copy.deepcopy(dict(cap))
    decision = doc.get("decision")
    if decision == "open":
        doc["status"] = "OPENED"
    elif decision == "decline_no_debt":
        doc["status"] = "DECLINED"
    elif decision == "keep_closed":
        doc["status"] = "KEPT_CLOSED"
    else:
        obs_dt = _parse_iso_utc(obs_iso)
        unlock_dt = _parse_iso_utc(doc.get("unlock_after"))
        if obs_dt is not None and unlock_dt is not None and obs_dt >= unlock_dt:
            doc["status"] = "AVAILABLE"
        else:
            doc["status"] = "LOCKED"
    doc["auto_claimed"] = False
    doc["omission_debt"] = 0
    doc["must_fulfill_old_wish"] = False
    return doc


def _normalize_optional_gesture_for_read(raw: Mapping[str, Any]) -> dict[str, Any]:
    """规范 §10.C 可拒绝小把手读投影：绝不带完成百分比或红色截止日字段。"""
    doc = copy.deepcopy(dict(raw))
    for banned in ("progress_pct", "completion_pct", "deadline", "red_deadline", "overdue_alert"):
        doc.pop(banned, None)
    doc["claimed"] = bool(doc.get("claimed", False))
    doc["activity_resumed"] = bool(doc.get("activity_resumed", False))
    doc["peek_count"] = int(doc.get("peek_count") or 0)
    doc["omission_debt"] = 0
    doc["auto_refresh"] = False
    return doc


def _normalize_legacy_tool_for_read(raw: Mapping[str, Any]) -> dict[str, Any]:
    """规范 D10/§10.D 衰老工具三结尾读投影：所有旧作品引用始终可读且零失败污名。"""
    doc = copy.deepcopy(dict(raw))
    ending = str(doc.get("ending") or "")
    doc["executable"] = ending == "keep_repaired"
    doc["linked_works_readable"] = True
    doc["failure_penalty"] = 0
    return doc


def _evaluate_gentle_responsibility_at_time(raw: Mapping[str, Any], obs_iso: str) -> dict[str, Any]:
    """规范 §10.H 温和责任回访读投影：数周未碰仍零红色催促、零亏欠债。"""
    doc = copy.deepcopy(dict(raw))
    untouched_days = round(_hours_between(doc.get("last_touched_at"), obs_iso) / 24.0, 2)
    doc["untouched_days"] = untouched_days
    doc["overdue_alert"] = False
    doc["omission_debt"] = 0
    doc["history_preserved"] = True
    return doc


def project_v02_agency_depth(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
    now: Optional[str] = None,
) -> dict[str, Any]:
    """100% 零磁盘写入只读投影；伴侣视角严格剥离 ``shared=False`` 项与全部后悔反思。"""
    eff_now = now_iso if now_iso is not None else now
    obs_iso = _now_iso_str(eff_now)
    exists, raw_state = _load_state_readonly(day_root)

    visible_prefs: list[dict[str, Any]] = []
    for pref in raw_state.get("preferences") or []:
        if not isinstance(pref, Mapping):
            continue
        is_shared = bool(pref.get("shared", False))
        if not has_subject_capability and not is_shared:
            continue
        evaluated = _evaluate_preference_at_time(pref, obs_iso)
        visible_prefs.append(evaluated)

    visible_episodes = _filter_visible_items(
        raw_state.get("choice_episodes"),
        has_subject_capability=has_subject_capability,
    )
    visible_partner_opts = _filter_visible_items(
        raw_state.get("partner_options"),
        has_subject_capability=has_subject_capability,
    )
    visible_conflicts = _filter_visible_items(
        raw_state.get("parked_conflicts"),
        has_subject_capability=has_subject_capability,
    )
    visible_incubations = _filter_visible_items(
        raw_state.get("open_incubations"),
        has_subject_capability=has_subject_capability,
    )
    visible_rules = _filter_visible_items(
        raw_state.get("second_order_rules"),
        has_subject_capability=has_subject_capability,
    )
    for rl in visible_rules:
        rl["auto_locked"] = False

    visible_method_adj = _filter_visible_items(
        raw_state.get("method_adjustments"),
        has_subject_capability=has_subject_capability,
    )
    visible_inquiries = [
        _normalize_inquiry_for_read(item)
        for item in _filter_visible_items(
            raw_state.get("inquiries"),
            has_subject_capability=has_subject_capability,
        )
    ]

    placed_cues_view = _project_placed_memory_cues(
        raw_state.get("placed_memory_cues"),
        has_subject_capability=has_subject_capability,
    )
    info_curations_view = _project_info_environment_curations(
        raw_state.get("info_environment_curations"),
        has_subject_capability=has_subject_capability,
    )
    visible_capsules = [
        _evaluate_future_capsule_at_time(item, obs_iso)
        for item in _filter_visible_items(
            raw_state.get("future_self_capsules"),
            has_subject_capability=has_subject_capability,
        )
    ]
    visible_gestures = [
        _normalize_optional_gesture_for_read(item)
        for item in _filter_visible_items(
            raw_state.get("optional_next_gestures"),
            has_subject_capability=has_subject_capability,
        )
    ]
    visible_legacy_tools = [
        _normalize_legacy_tool_for_read(item)
        for item in _filter_visible_items(
            raw_state.get("legacy_tool_endings"),
            has_subject_capability=has_subject_capability,
        )
    ]
    visible_gentle_resps = [
        _evaluate_gentle_responsibility_at_time(item, obs_iso)
        for item in _filter_visible_items(
            raw_state.get("gentle_responsibility_reviews"),
            has_subject_capability=has_subject_capability,
        )
    ]
    visible_belief_revisions = _filter_visible_items(
        raw_state.get("belief_revisions"),
        has_subject_capability=has_subject_capability,
    )
    visible_commitment_tradeoffs = _filter_visible_items(
        raw_state.get("commitment_tradeoffs"),
        has_subject_capability=has_subject_capability,
    )
    rule_shared_map: dict[str, bool] = {}
    for rl in raw_state.get("second_order_rules") or []:
        if isinstance(rl, Mapping):
            rid = str(rl.get("rule_id") or "").strip()
            if rid:
                rule_shared_map[rid] = bool(rl.get("shared", False))
    visible_rule_enforcements: list[dict[str, Any]] = []
    for enf in raw_state.get("rule_enforcements") or []:
        if not isinstance(enf, Mapping):
            continue
        rid = str(enf.get("rule_id") or "").strip()
        is_shared = rule_shared_map.get(rid, bool(enf.get("shared", False)))
        if not has_subject_capability and not is_shared:
            continue
        visible_rule_enforcements.append(copy.deepcopy(dict(enf)))

    if has_subject_capability:
        visible_regrets = [
            copy.deepcopy(dict(r))
            for r in (raw_state.get("regret_reflections") or [])
            if isinstance(r, Mapping)
        ]
        regrets_withheld = False
    else:
        visible_regrets = []
        regrets_withheld = True

    return {
        "state_exists": bool(exists),
        "perspective": "subject" if has_subject_capability else "partner",
        "revision": int(raw_state.get("revision") or 0),
        "observed_at": obs_iso,
        "preferences": visible_prefs,
        "choice_episodes": visible_episodes,
        "partner_options": visible_partner_opts,
        "low_cost_options": copy.deepcopy(visible_partner_opts),
        "parked_conflicts": visible_conflicts,
        "open_incubations": visible_incubations,
        "second_order_rules": visible_rules,
        "method_adjustments": visible_method_adj,
        "regret_reflections": visible_regrets,
        "regret_reflections_withheld": regrets_withheld,
        "inquiries": visible_inquiries,
        "placed_memory_cues": placed_cues_view,
        "info_environment_curations": info_curations_view,
        "future_self_capsules": visible_capsules,
        "optional_next_gestures": visible_gestures,
        "legacy_tool_endings": visible_legacy_tools,
        "gentle_responsibility_reviews": visible_gentle_resps,
        "belief_revisions": visible_belief_revisions,
        "rule_enforcements": visible_rule_enforcements,
        "commitment_tradeoffs": visible_commitment_tradeoffs,
        "llm_calls": 0,
    }


def _find_preference(
    preferences: list[dict[str, Any]],
    domain: str,
    title: str,
) -> Optional[dict[str, Any]]:
    dom_key = domain.strip().lower()
    title_key = title.strip().lower()
    for pref in preferences:
        if not isinstance(pref, dict):
            continue
        if (
            str(pref.get("domain") or "").strip().lower() == dom_key
            and str(pref.get("title") or pref.get("chosen") or "").strip().lower() == title_key
        ):
            return pref
    return None


def _handle_record_choice_episode(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    domain = _require_clean_str(payload.get("domain"), "domain", max_len=80)
    chosen = _require_clean_str(payload.get("chosen"), "chosen", max_len=160)
    raw_mode = _require_clean_str(payload.get("choice_mode"), "choice_mode", max_len=40)
    if raw_mode not in CHOICE_MODES:
        raise V02ValidationError("INVALID_CHOICE_MODE", f"Unsupported choice_mode: {raw_mode!r}")

    raw_options = payload.get("options")
    if not isinstance(raw_options, list) or len(raw_options) == 0 or len(raw_options) > 32:
        raise V02ValidationError("INVALID_FIELDS", "Field 'options' must be a non-empty list of strings")

    unique_options: list[str] = []
    seen_lower: set[str] = set()
    for idx, item in enumerate(raw_options):
        cleaned_opt = _require_clean_str(item, f"options[{idx}]", max_len=160)
        low = cleaned_opt.lower()
        if low not in seen_lower:
            seen_lower.add(low)
            unique_options.append(cleaned_opt)

    if chosen.lower() not in seen_lower:
        raise V02ValidationError("INVALID_FIELDS", "Field 'chosen' must be present in 'options'")

    forgone_cost = _optional_clean_str(payload.get("forgone_cost"), "forgone_cost", max_len=200)
    note_cn = _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=240)
    shared = _optional_bool(payload.get("shared"), "shared", default=True)

    effective_opt_count = len(unique_options)
    qualifies_for_promotion = raw_mode == "spontaneous" and effective_opt_count >= 2

    prefs: list[dict[str, Any]] = list(state.get("preferences") or [])
    pref = _find_preference(prefs, domain, chosen)
    is_new = pref is None

    if pref is None:
        pref = {
            "pref_id": _allocate_id(state, "pref"),
            "domain": domain,
            "title": chosen,
            "chosen": chosen,
            "status": "UNKNOWN",
            "horizon": "long_term",
            "spontaneous_multi_count": 0,
            "constrained_or_single_count": 0,
            "satiation_score": 0.0,
            "satiation_state": "fresh",
            "saturated": False,
            "ever_saturated": False,
            "rediscovered": False,
            "candidate_weight_multiplier": 1.0,
            "forgone_cost": forgone_cost,
            "note_cn": note_cn,
            "shared": shared,
            "last_satiation_at": obs_iso,
            "created_at": obs_iso,
            "updated_at": obs_iso,
        }
        prefs.insert(0, pref)

    # 先按连续时间差推进已有饱和度衰减
    dt_hours = _hours_between(pref.get("last_satiation_at") or pref.get("updated_at"), obs_iso)
    live_score = _decay_satiation_score(float(pref.get("satiation_score") or 0.0), dt_hours)
    pref["satiation_score"] = live_score
    pref["last_satiation_at"] = obs_iso

    if qualifies_for_promotion:
        spont_cnt = int(pref.get("spontaneous_multi_count") or 0) + 1
        pref["spontaneous_multi_count"] = spont_cnt
        if spont_cnt >= 2:
            pref["status"] = "SUPPORTED"
        elif pref.get("status") == "UNKNOWN":
            pref["status"] = "TENTATIVE"

        if bool(pref.get("ever_saturated", False)) and live_score < SATIATION_THRESHOLD:
            pref["rediscovered"] = True
    else:
        pref["constrained_or_single_count"] = int(pref.get("constrained_or_single_count") or 0) + 1
        if is_new:
            pref["status"] = "UNKNOWN"
        # 铁律：非自发或单选绝不升格已有偏好状态

    sat_state, is_sat, weight_mult = _derive_satiation_metrics(
        live_score,
        rediscovered_active=bool(pref.get("rediscovered", False)) and live_score < SATIATION_THRESHOLD,
    )
    pref["satiation_state"] = sat_state
    pref["saturated"] = is_sat
    pref["candidate_weight_multiplier"] = weight_mult

    if forgone_cost:
        pref["forgone_cost"] = forgone_cost
    if payload.get("note_cn") is not None:
        pref["note_cn"] = note_cn
    if payload.get("shared") is not None or is_new:
        pref["shared"] = shared
    pref["updated_at"] = obs_iso
    state["preferences"] = prefs

    episode_doc: dict[str, Any] = {
        "episode_id": _allocate_id(state, "cep"),
        "domain": domain,
        "chosen": chosen,
        "options": unique_options,
        "effective_option_count": effective_opt_count,
        "choice_mode": raw_mode,
        "promoted_preference": qualifies_for_promotion,
        "resulting_status": pref["status"],
        "forgone_cost": forgone_cost,
        "note_cn": note_cn,
        "shared": shared,
        "recorded_at": obs_iso,
    }
    episodes: list[dict[str, Any]] = list(state.get("choice_episodes") or [])
    episodes.insert(0, episode_doc)
    state["choice_episodes"] = episodes[:120]


def _handle_record_exposure_satiation(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    domain = _require_clean_str(payload.get("domain"), "domain", max_len=80)
    raw_title = payload.get("title") if payload.get("title") is not None else payload.get("chosen")
    title = _require_clean_str(raw_title, "title", max_len=160)

    raw_intensity = payload.get("intensity")
    if raw_intensity is None:
        intensity = 0.45
    else:
        if not _is_strict_number(raw_intensity) or float(raw_intensity) < 0.0 or float(raw_intensity) > 1.0:
            raise V02ValidationError("INVALID_FIELDS", "Field 'intensity' must be a number in [0.0, 1.0]")
        intensity = float(raw_intensity)

    raw_elapsed = payload.get("elapsed_hours")
    if raw_elapsed is None:
        explicit_elapsed = 0.0
    else:
        if not _is_strict_number(raw_elapsed) or float(raw_elapsed) < 0.0 or float(raw_elapsed) > 8760.0:
            raise V02ValidationError("INVALID_FIELDS", "Field 'elapsed_hours' must be a number in [0.0, 8760.0]")
        explicit_elapsed = float(raw_elapsed)

    prefs: list[dict[str, Any]] = list(state.get("preferences") or [])
    pref = _find_preference(prefs, domain, title)
    is_new = pref is None
    default_shared = True if is_new else bool(pref.get("shared", True))
    shared = _optional_bool(payload.get("shared"), "shared", default=default_shared)
    note_cn = _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=240)

    if pref is None:
        pref = {
            "pref_id": _allocate_id(state, "pref"),
            "domain": domain,
            "title": title,
            "chosen": title,
            "status": "UNKNOWN",
            "horizon": "long_term",
            "spontaneous_multi_count": 0,
            "constrained_or_single_count": 0,
            "satiation_score": 0.0,
            "satiation_state": "fresh",
            "saturated": False,
            "ever_saturated": False,
            "rediscovered": False,
            "candidate_weight_multiplier": 1.0,
            "forgone_cost": "",
            "note_cn": note_cn,
            "shared": shared,
            "last_satiation_at": obs_iso,
            "created_at": obs_iso,
            "updated_at": obs_iso,
        }
        prefs.insert(0, pref)

    iso_dt_hours = _hours_between(pref.get("last_satiation_at") or pref.get("updated_at"), obs_iso)
    effective_elapsed = max(iso_dt_hours, explicit_elapsed)
    decayed = _decay_satiation_score(float(pref.get("satiation_score") or 0.0), effective_elapsed)
    new_score = round(min(1.0, decayed + intensity), 4)

    if new_score >= SATIATION_THRESHOLD:
        pref["ever_saturated"] = True
        pref["rediscovered"] = False

    sat_state, is_sat, weight_mult = _derive_satiation_metrics(
        new_score,
        rediscovered_active=bool(pref.get("rediscovered", False)) and new_score < SATIATION_THRESHOLD,
    )
    pref["satiation_score"] = new_score
    pref["satiation_state"] = sat_state
    pref["saturated"] = is_sat
    pref["candidate_weight_multiplier"] = weight_mult
    pref["last_satiation_at"] = obs_iso
    if payload.get("note_cn") is not None:
        pref["note_cn"] = note_cn
    if payload.get("shared") is not None or is_new:
        pref["shared"] = shared
    pref["updated_at"] = obs_iso
    # 铁律：短期看腻饱和绝不篡改或降级长期 status（SUPPORTED 保持 SUPPORTED）
    state["preferences"] = prefs


def _handle_offer_low_cost_option(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    domain = _require_clean_str(payload.get("domain"), "domain", max_len=80)
    option_title = _require_clean_str(payload.get("option_title"), "option_title", max_len=160)
    note = _optional_clean_str(payload.get("note"), "note", max_len=300)

    opt_doc: dict[str, Any] = {
        "option_id": _allocate_id(state, "popt"),
        "domain": domain,
        "option_title": option_title,
        "note": note,
        "status": "offered",
        "zero_obligation": True,
        "shared": True,
        "offered_at": obs_iso,
        "updated_at": obs_iso,
    }
    opts: list[dict[str, Any]] = list(state.get("partner_options") or [])
    opts.insert(0, opt_doc)
    state["partner_options"] = opts[:80]


def _handle_withdraw_partner_option(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    option_id = _require_clean_str(payload.get("option_id"), "option_id", max_len=96)
    opts: list[dict[str, Any]] = list(state.get("partner_options") or [])
    target: Optional[dict[str, Any]] = None
    for item in opts:
        if isinstance(item, dict) and item.get("option_id") == option_id:
            target = item
            break
    if target is None:
        raise V02NotFoundError("OPTION_NOT_FOUND", f"Partner option not found: {option_id!r}")
    target["status"] = "withdrawn"
    target["withdrawn_at"] = obs_iso
    target["updated_at"] = obs_iso
    state["partner_options"] = opts


def _handle_park_or_resolve_conflict(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    conflict_op = _require_clean_str(payload.get("conflict_op"), "conflict_op", max_len=32)
    if conflict_op not in CONFLICT_OPS:
        raise V02ValidationError("INVALID_CONFLICT_OP", f"Unsupported conflict_op: {conflict_op!r}")

    conflict_id_in = _optional_clean_str(payload.get("conflict_id"), "conflict_id", max_len=96)
    title_in = _optional_clean_str(payload.get("title"), "title", max_len=160)
    side_a_in = _optional_clean_str(payload.get("side_a"), "side_a", max_len=160)
    side_b_in = _optional_clean_str(payload.get("side_b"), "side_b", max_len=160)
    note_cn = _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=240)

    conflicts: list[dict[str, Any]] = list(state.get("parked_conflicts") or [])
    target: Optional[dict[str, Any]] = None
    if conflict_id_in:
        for cf in conflicts:
            if isinstance(cf, dict) and cf.get("conflict_id") == conflict_id_in:
                target = cf
                break
        if target is None:
            raise V02NotFoundError("CONFLICT_NOT_FOUND", f"Conflict not found: {conflict_id_in!r}")

    status_map = {
        "park": "parked",
        "lean": "leaning",
        "settle": "settled",
    }
    new_status = status_map[conflict_op]

    if target is not None:
        if title_in:
            target["title"] = title_in
        if side_a_in:
            target["side_a"] = side_a_in
        if side_b_in:
            target["side_b"] = side_b_in
        if payload.get("note_cn") is not None:
            target["note_cn"] = note_cn
        if payload.get("shared") is not None:
            target["shared"] = _optional_bool(payload.get("shared"), "shared", default=False)
        target["status"] = new_status
        target["both_motives_preserved"] = conflict_op == "park"
        target["forced_binary_choice"] = False
        target["updated_at"] = obs_iso
    else:
        if not title_in and not (side_a_in and side_b_in):
            raise V02ValidationError(
                "INVALID_FIELDS",
                "New conflict requires 'title' or both 'side_a' and 'side_b'",
            )
        if conflict_op == "park" and (not side_a_in or not side_b_in):
            raise V02ValidationError(
                "INVALID_FIELDS",
                "Parking a new conflict requires both 'side_a' and 'side_b'",
            )
        shared = _optional_bool(payload.get("shared"), "shared", default=False)
        eff_title = title_in or f"{side_a_in} vs {side_b_in}"
        conflicts.insert(0, {
            "conflict_id": _allocate_id(state, "cfl"),
            "title": eff_title,
            "side_a": side_a_in,
            "side_b": side_b_in,
            "status": new_status,
            "both_motives_preserved": conflict_op == "park",
            "forced_binary_choice": False,
            "note_cn": note_cn,
            "shared": shared,
            "created_at": obs_iso,
            "updated_at": obs_iso,
        })
    state["parked_conflicts"] = conflicts[:80]


def _handle_record_regret_reflection(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    regret_kind = _require_clean_str(payload.get("regret_kind"), "regret_kind", max_len=48)
    if regret_kind not in REGRET_KINDS:
        raise V02ValidationError("INVALID_REGRET_KIND", f"Unsupported regret_kind: {regret_kind!r}")

    summary = _require_clean_str(payload.get("summary"), "summary", max_len=300)
    counterfactual_note = _optional_clean_str(payload.get("counterfactual_note"), "counterfactual_note", max_len=300)
    method_adj_cn = _optional_clean_str(payload.get("method_adjustment_cn"), "method_adjustment_cn", max_len=240)
    shared = _optional_bool(payload.get("shared"), "shared", default=False)

    raw_has_alt = payload.get("has_observable_alternative")
    if raw_has_alt is not None and not isinstance(raw_has_alt, bool):
        raise V02ValidationError("INVALID_FIELDS", "Field 'has_observable_alternative' must be a boolean")
    has_alt = bool(raw_has_alt is True)

    if regret_kind == "opportunity_loss":
        if not has_alt or not counterfactual_note:
            raise V02ConflictError(
                "MISSING_OBSERVABLE_COUNTERFACTUAL",
                "opportunity_loss requires has_observable_alternative=True and non-empty counterfactual_note",
            )

    if regret_kind == "outcome_disappointment":
        changes_method_policy = False
    elif regret_kind == "process_mistake":
        changes_method_policy = True
    else:
        changes_method_policy = bool(method_adj_cn)

    regret_id = _allocate_id(state, "reg")
    regret_doc: dict[str, Any] = {
        "regret_id": regret_id,
        "regret_kind": regret_kind,
        "summary": summary,
        "counterfactual_note": counterfactual_note,
        "has_observable_alternative": has_alt,
        "changes_method_policy": changes_method_policy,
        "method_adjustment_cn": method_adj_cn if changes_method_policy else "",
        "shared": shared,
        "recorded_at": obs_iso,
    }
    regrets: list[dict[str, Any]] = list(state.get("regret_reflections") or [])
    regrets.insert(0, regret_doc)
    state["regret_reflections"] = regrets[:80]

    if regret_kind == "process_mistake":
        adj_text = method_adj_cn or summary
        adj_list: list[dict[str, Any]] = list(state.get("method_adjustments") or [])
        adj_list.insert(0, {
            "adjustment_id": _allocate_id(state, "madj"),
            "source_regret_id": regret_id,
            "adjustment_cn": adj_text,
            "shared": shared,
            "created_at": obs_iso,
        })
        state["method_adjustments"] = adj_list[:80]


def _handle_propose_shared_gentle_rule(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    title = _require_clean_str(payload.get("title"), "title", max_len=160)
    trigger_window = _require_clean_str(payload.get("trigger_window"), "trigger_window", max_len=120)
    reason_cn = _optional_clean_str(payload.get("reason_cn"), "reason_cn", max_len=300)

    rule_doc: dict[str, Any] = {
        "rule_id": _allocate_id(state, "srule"),
        "title": title,
        "trigger_window": trigger_window,
        "reason_cn": reason_cn,
        "kind": "shared_gentle",
        "author_side": "partner",
        "status": "proposed",
        "auto_locked": False,
        "shared": True,
        "created_at": obs_iso,
        "updated_at": obs_iso,
    }
    rules: list[dict[str, Any]] = list(state.get("second_order_rules") or [])
    rules.insert(0, rule_doc)
    state["second_order_rules"] = rules[:80]


def _handle_manage_second_order_rule(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    rule_op = _require_clean_str(payload.get("rule_op"), "rule_op", max_len=32)
    if rule_op not in SECOND_ORDER_RULE_OPS:
        raise V02ValidationError("INVALID_RULE_OP", f"Unsupported rule_op: {rule_op!r}")

    rule_id_in = _optional_clean_str(payload.get("rule_id"), "rule_id", max_len=96)
    title_in = _optional_clean_str(payload.get("title"), "title", max_len=160)
    trig_in = _optional_clean_str(payload.get("trigger_window"), "trigger_window", max_len=120)
    reason_cn = _optional_clean_str(payload.get("reason_cn"), "reason_cn", max_len=300)

    op_to_status = {
        "propose": "proposed",
        "start_trial": "trialing",
        "endorse": "endorsed",
        "revise": "endorsed",
        "release": "released",
    }

    rules: list[dict[str, Any]] = list(state.get("second_order_rules") or [])
    target: Optional[dict[str, Any]] = None
    if rule_id_in:
        for rl in rules:
            if isinstance(rl, dict) and rl.get("rule_id") == rule_id_in:
                target = rl
                break
        if target is None:
            raise V02NotFoundError("RULE_NOT_FOUND", f"Second-order rule not found: {rule_id_in!r}")

    if target is None:
        if rule_op in ("release", "revise") and not title_in:
            raise V02ValidationError("INVALID_FIELDS", f"rule_op={rule_op!r} requires 'rule_id' or 'title'")
        if not title_in:
            raise V02ValidationError("INVALID_FIELDS", "Creating a second-order rule requires 'title'")
        shared = _optional_bool(payload.get("shared"), "shared", default=False)
        new_rule: dict[str, Any] = {
            "rule_id": _allocate_id(state, "srule"),
            "title": title_in,
            "trigger_window": trig_in or "anytime",
            "reason_cn": reason_cn,
            "kind": "shared_gentle" if shared else "self_rule",
            "author_side": "subject",
            "status": op_to_status[rule_op],
            "auto_locked": False,
            "shared": shared,
            "created_at": obs_iso,
            "updated_at": obs_iso,
        }
        rules.insert(0, new_rule)
    else:
        if title_in:
            target["title"] = title_in
        if trig_in:
            target["trigger_window"] = trig_in
        if payload.get("reason_cn") is not None:
            target["reason_cn"] = reason_cn
        if payload.get("shared") is not None:
            target["shared"] = _optional_bool(payload.get("shared"), "shared", default=bool(target.get("shared", False)))
        if rule_op == "revise" and target.get("status") in ("proposed", "trialing", "endorsed"):
            target["status"] = target["status"] if target["status"] != "proposed" else "trialing"
            target["revision_count"] = int(target.get("revision_count") or 0) + 1
        else:
            target["status"] = op_to_status[rule_op]
        target["auto_locked"] = False
        target["updated_at"] = obs_iso

    state["second_order_rules"] = rules[:80]


def _evidence_fp(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _handle_manage_open_incubation(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    inc_op = _require_clean_str(payload.get("inc_op"), "inc_op", max_len=32)
    if inc_op not in INCUBATION_OPS:
        raise V02ValidationError("INVALID_INC_OP", f"Unsupported inc_op: {inc_op!r}")

    qid_in = _optional_clean_str(payload.get("question_id"), "question_id", max_len=96)
    topic_in = _optional_clean_str(payload.get("topic"), "topic", max_len=160)
    evidence_ref = _optional_clean_str(payload.get("evidence_ref"), "evidence_ref", max_len=240)
    clue_text = _optional_clean_str(payload.get("clue_text"), "clue_text", max_len=400)
    note_cn = _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=240)

    incubations: list[dict[str, Any]] = list(state.get("open_incubations") or [])
    target: Optional[dict[str, Any]] = None
    if qid_in:
        for inc in incubations:
            if isinstance(inc, dict) and inc.get("question_id") == qid_in:
                target = inc
                break
        if target is None:
            raise V02NotFoundError("INCUBATION_NOT_FOUND", f"Incubation question not found: {qid_in!r}")
    elif topic_in and inc_op != "open":
        for inc in incubations:
            if isinstance(inc, dict) and str(inc.get("topic") or "").strip().lower() == topic_in.lower():
                target = inc
                break

    if inc_op == "open":
        if target is None:
            if not topic_in:
                raise V02ValidationError("INVALID_FIELDS", "inc_op='open' requires 'topic'")
            shared = _optional_bool(payload.get("shared"), "shared", default=True)
            fps: list[str] = []
            clues: list[dict[str, Any]] = []
            if evidence_ref:
                fps.append(_evidence_fp(f"ev:{evidence_ref}"))
            if clue_text:
                fps.append(_evidence_fp(f"clue:{clue_text}"))
                clues.append({
                    "by": "subject",
                    "clue_text": clue_text,
                    "added_at": obs_iso,
                })
            new_inc: dict[str, Any] = {
                "question_id": _allocate_id(state, "inc"),
                "topic": topic_in,
                "status": "incubating",
                "clues": clues,
                "partner_clues": [],
                "latest_clue": clue_text or "",
                "evidence_refs": [evidence_ref] if evidence_ref else [],
                "evidence_fingerprints": fps,
                "fake_epiphany_blocked": True,
                "note_cn": note_cn,
                "shared": shared,
                "opened_at": obs_iso,
                "last_checked_at": obs_iso,
                "updated_at": obs_iso,
            }
            incubations.insert(0, new_inc)
        else:
            if topic_in:
                target["topic"] = topic_in
            if payload.get("note_cn") is not None:
                target["note_cn"] = note_cn
            if payload.get("shared") is not None:
                target["shared"] = _optional_bool(payload.get("shared"), "shared", default=True)
            target["status"] = "incubating"
            target["updated_at"] = obs_iso

    elif inc_op == "check_elapsed":
        if target is None:
            raise V02ValidationError("INVALID_FIELDS", "inc_op='check_elapsed' requires valid 'question_id' or existing 'topic'")
        elapsed_h = _hours_between(target.get("opened_at"), obs_iso)
        target["elapsed_hours"] = round(elapsed_h, 2)
        target["last_checked_at"] = obs_iso
        # 铁律：跨 48h/72h 纯时间流逝无新证据指纹时保持 incubating，绝不假顿悟
        if evidence_ref:
            fp = _evidence_fp(f"ev:{evidence_ref}")
            fps = list(target.get("evidence_fingerprints") or [])
            if fp not in fps:
                fps.append(fp)
                ev_list = list(target.get("evidence_refs") or [])
                ev_list.append(evidence_ref)
                target["evidence_refs"] = ev_list[-24:]
                target["evidence_fingerprints"] = fps[-48:]
                if target.get("status") == "incubating":
                    target["status"] = "ready_to_revisit"
        target["fake_epiphany_blocked"] = True
        if payload.get("note_cn") is not None:
            target["note_cn"] = note_cn
        target["updated_at"] = obs_iso

    elif inc_op == "add_evidence":
        if target is None:
            raise V02ValidationError("INVALID_FIELDS", "inc_op='add_evidence' requires valid 'question_id' or existing 'topic'")
        if not evidence_ref and not clue_text:
            raise V02ValidationError("INVALID_FIELDS", "inc_op='add_evidence' requires 'evidence_ref' or 'clue_text'")
        fps = list(target.get("evidence_fingerprints") or [])
        has_novel_evidence = False
        if evidence_ref:
            fp_ev = _evidence_fp(f"ev:{evidence_ref}")
            if fp_ev not in fps:
                fps.append(fp_ev)
                has_novel_evidence = True
                ev_list = list(target.get("evidence_refs") or [])
                ev_list.append(evidence_ref)
                target["evidence_refs"] = ev_list[-24:]
        if clue_text:
            fp_cl = _evidence_fp(f"clue:{clue_text}")
            if fp_cl not in fps:
                fps.append(fp_cl)
                has_novel_evidence = True
                clues = list(target.get("clues") or [])
                clues.append({
                    "by": "subject",
                    "clue_text": clue_text,
                    "added_at": obs_iso,
                })
                target["clues"] = clues[-24:]
                target["latest_clue"] = clue_text
        target["evidence_fingerprints"] = fps[-48:]
        if has_novel_evidence and target.get("status") != "settled":
            target["status"] = "ready_to_revisit"
        if payload.get("note_cn") is not None:
            target["note_cn"] = note_cn
        if payload.get("shared") is not None:
            target["shared"] = _optional_bool(payload.get("shared"), "shared", default=bool(target.get("shared", True)))
        target["updated_at"] = obs_iso

    elif inc_op == "settle":
        if target is None:
            raise V02ValidationError("INVALID_FIELDS", "inc_op='settle' requires valid 'question_id' or existing 'topic'")
        target["status"] = "settled"
        if payload.get("note_cn") is not None:
            target["note_cn"] = note_cn
        if payload.get("shared") is not None:
            target["shared"] = _optional_bool(payload.get("shared"), "shared", default=bool(target.get("shared", True)))
        target["settled_at"] = obs_iso
        target["updated_at"] = obs_iso

    state["open_incubations"] = incubations[:80]


def _handle_share_incubation_clue(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    clue_text = _require_clean_str(payload.get("clue_text"), "clue_text", max_len=400)
    qid_in = _optional_clean_str(payload.get("question_id"), "question_id", max_len=96)
    topic_in = _optional_clean_str(payload.get("topic"), "topic", max_len=160)

    incubations: list[dict[str, Any]] = list(state.get("open_incubations") or [])
    target: Optional[dict[str, Any]] = None

    if qid_in:
        for inc in incubations:
            if isinstance(inc, dict) and inc.get("question_id") == qid_in:
                target = inc
                break
        if target is None or not bool(target.get("shared", True)):
            raise V02NotFoundError("INCUBATION_NOT_FOUND", f"Shared incubation question not found: {qid_in!r}")
    elif topic_in:
        for inc in incubations:
            if (
                isinstance(inc, dict)
                and bool(inc.get("shared", True))
                and str(inc.get("topic") or "").strip().lower() == topic_in.lower()
            ):
                target = inc
                break

    clue_entry = {
        "by": "partner",
        "clue_text": clue_text,
        "added_at": obs_iso,
    }
    fp_cl = _evidence_fp(f"partner_clue:{clue_text}")

    if target is None:
        eff_topic = topic_in or (clue_text[:28] if len(clue_text) <= 28 else clue_text[:28] + "…")
        new_inc: dict[str, Any] = {
            "question_id": _allocate_id(state, "inc"),
            "topic": eff_topic,
            "status": "ready_to_revisit",
            "clues": [clue_entry],
            "partner_clues": [copy.deepcopy(clue_entry)],
            "latest_clue": clue_text,
            "evidence_refs": [],
            "evidence_fingerprints": [fp_cl],
            "fake_epiphany_blocked": True,
            "note_cn": "",
            "shared": True,
            "opened_at": obs_iso,
            "last_checked_at": obs_iso,
            "updated_at": obs_iso,
        }
        incubations.insert(0, new_inc)
    else:
        fps = list(target.get("evidence_fingerprints") or [])
        is_novel = fp_cl not in fps
        if is_novel:
            fps.append(fp_cl)
        clues = list(target.get("clues") or [])
        clues.append(clue_entry)
        p_clues = list(target.get("partner_clues") or [])
        p_clues.append(copy.deepcopy(clue_entry))
        target["clues"] = clues[-24:]
        target["partner_clues"] = p_clues[-24:]
        target["latest_clue"] = clue_text
        target["evidence_fingerprints"] = fps[-48:]
        if is_novel and target.get("status") != "settled":
            target["status"] = "ready_to_revisit"
        target["updated_at"] = obs_iso

    state["open_incubations"] = incubations[:80]


def _effective_inquiry_step_budget(value: Any) -> int:
    """读侧兜底：老数据缺省或越界时回落到默认步骤预算，绝不因历史格式报错。"""
    if _is_strict_int(value) and MIN_INQUIRY_STEP_BUDGET <= int(value) <= MAX_INQUIRY_STEP_BUDGET:
        return int(value)
    return DEFAULT_INQUIRY_STEP_BUDGET


def _clean_inquiry_step_budget(value: Any) -> int:
    """写侧校验：显式传入的步骤预算必须是 1–12 的严格整数。"""
    if not _is_strict_int(value) or not (
        MIN_INQUIRY_STEP_BUDGET <= int(value) <= MAX_INQUIRY_STEP_BUDGET
    ):
        raise V02ValidationError(
            "INVALID_FIELDS",
            f"Field 'step_budget' must be an integer in "
            f"[{MIN_INQUIRY_STEP_BUDGET}, {MAX_INQUIRY_STEP_BUDGET}]",
        )
    return int(value)


def _normalize_candidate_explanations(
    raw: Any,
    *,
    existing: Optional[list[Any]] = None,
) -> list[dict[str, Any]]:
    """规范候选解释列表（最多 4 项，立场仅限 plausible/supported/ruled_out）。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise V02ValidationError("INVALID_FIELDS", "Field 'candidate_explanations' must be a list")
    if len(raw) > MAX_CANDIDATE_EXPLANATIONS:
        raise V02ValidationError(
            "INVALID_FIELDS",
            f"At most {MAX_CANDIDATE_EXPLANATIONS} candidate explanations are allowed",
        )

    existing_by_summary: dict[str, str] = {}
    for item in existing or []:
        if isinstance(item, Mapping):
            summary_key = str(item.get("summary_cn") or "").strip()
            hyp_key = str(item.get("hypothesis_id") or "").strip()
            if summary_key and hyp_key:
                existing_by_summary[summary_key] = hyp_key

    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise V02ValidationError(
                "INVALID_FIELDS", f"candidate_explanations[{idx}] must be an object"
            )
        summary_cn = _require_clean_str(
            item.get("summary_cn"), f"candidate_explanations[{idx}].summary_cn", max_len=160
        )
        stance = str(item.get("stance") or "plausible").strip().lower()
        if stance not in INQUIRY_STANCES:
            raise V02ValidationError(
                "INVALID_FIELDS",
                f"candidate_explanations[{idx}].stance must be one of {sorted(INQUIRY_STANCES)}",
            )
        raw_hid = item.get("hypothesis_id")
        if isinstance(raw_hid, str) and raw_hid.strip():
            hypothesis_id = raw_hid.strip()[:64]
        else:
            hypothesis_id = existing_by_summary.get(summary_cn) or f"hyp_{idx + 1:02d}"
        out.append({
            "hypothesis_id": hypothesis_id,
            "summary_cn": summary_cn,
            "stance": stance,
        })
    return out


def _normalize_inquiry_for_read(raw: Mapping[str, Any]) -> dict[str, Any]:
    """读侧投影兜底：补齐候选解释/预算/线索等字段，老数据绝不因缺字段失真或报错。"""
    doc = copy.deepcopy(dict(raw))

    raw_candidates = doc.get("candidate_explanations")
    candidates: list[dict[str, Any]] = []
    if isinstance(raw_candidates, list):
        for idx, item in enumerate(raw_candidates[:MAX_CANDIDATE_EXPLANATIONS]):
            if not isinstance(item, Mapping):
                continue
            stance = str(item.get("stance") or "plausible").strip().lower()
            candidates.append({
                "hypothesis_id": str(item.get("hypothesis_id") or f"hyp_{idx + 1:02d}"),
                "summary_cn": str(item.get("summary_cn") or "")[:160],
                "stance": stance if stance in INQUIRY_STANCES else "plausible",
            })
    doc["candidate_explanations"] = candidates

    next_evidence = doc.get("next_evidence_needed")
    doc["next_evidence_needed"] = str(next_evidence)[:160] if isinstance(next_evidence, str) else ""

    doc["step_budget"] = _effective_inquiry_step_budget(doc.get("step_budget"))

    raw_spent = doc.get("steps_spent")
    if _is_strict_int(raw_spent) and int(raw_spent) >= 0:
        doc["steps_spent"] = int(raw_spent)
    else:
        doc["steps_spent"] = len(doc.get("evidence_notes") or [])

    doc["stop_reason"] = doc.get("stop_reason") or ""

    raw_clues = doc.get("partner_clues")
    clues: list[dict[str, Any]] = []
    if isinstance(raw_clues, list):
        for item in raw_clues[-MAX_PARTNER_CLUES_PER_INQUIRY:]:
            if not isinstance(item, Mapping):
                continue
            clues.append({
                "clue_id": str(item.get("clue_id") or ""),
                "clue_text": str(item.get("clue_text") or "")[:160],
                "at": str(item.get("at") or ""),
            })
    doc["partner_clues"] = clues
    return doc


def _find_inquiry(
    inquiries: list[dict[str, Any]],
    inquiry_id: str,
) -> Optional[dict[str, Any]]:
    for inquiry in inquiries:
        if isinstance(inquiry, dict) and inquiry.get("inquiry_id") == inquiry_id:
            return inquiry
    return None


def _handle_open_bounded_inquiry(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    question = _require_clean_str(payload.get("question"), "question", max_len=240)
    topic_tag = _optional_clean_str(payload.get("topic_tag"), "topic_tag", max_len=80)
    shared = _optional_bool(payload.get("shared"), "shared", default=False)

    raw_candidates = payload.get("candidate_explanations")
    candidate_explanations = _normalize_candidate_explanations(raw_candidates)
    next_evidence_needed = ""
    if payload.get("next_evidence_needed") is not None:
        next_evidence_needed = _optional_clean_str(
            payload.get("next_evidence_needed"), "next_evidence_needed", max_len=160
        )
    step_budget_explicit = payload.get("step_budget") is not None
    step_budget = (
        _clean_inquiry_step_budget(payload.get("step_budget"))
        if step_budget_explicit
        else DEFAULT_INQUIRY_STEP_BUDGET
    )

    inquiries: list[dict[str, Any]] = list(state.get("inquiries") or [])
    active_count = sum(
        1
        for inquiry in inquiries
        if isinstance(inquiry, Mapping)
        and str(inquiry.get("status") or "") in INQUIRY_ACTIVE_STATUSES
    )
    if active_count >= MAX_OPEN_INQUIRIES:
        raise V02ConflictError(
            "INQUIRY_CAPACITY_EXCEEDED",
            f"At most {MAX_OPEN_INQUIRIES} open inquiries are allowed at once",
        )

    inquiry_doc: dict[str, Any] = {
        "inquiry_id": _allocate_id(state, "inq"),
        "question": question,
        "topic_tag": topic_tag,
        "status": INQUIRY_STATUS_OPEN,
        "shared": shared,
        "evidence_notes": [],
        "conclusion": "",
        "stop_reason": "",
        "candidate_explanations": candidate_explanations,
        "next_evidence_needed": next_evidence_needed,
        "step_budget": step_budget,
        "step_budget_explicit": step_budget_explicit,
        "steps_spent": 0,
        "partner_clues": [],
        "opened_at": obs_iso,
        "updated_at": obs_iso,
        "settled_at": None,
    }
    inquiries.insert(0, inquiry_doc)
    state["inquiries"] = inquiries[:80]


def _handle_record_inquiry_evidence(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    inquiry_id = _require_clean_str(payload.get("inquiry_id"), "inquiry_id", max_len=96)
    note = _require_clean_str(payload.get("note"), "note", max_len=320)
    source_ref = _optional_clean_str(payload.get("source_ref"), "source_ref", max_len=240)

    inquiries: list[dict[str, Any]] = list(state.get("inquiries") or [])
    target = _find_inquiry(inquiries, inquiry_id)
    if target is None:
        raise V02NotFoundError("INQUIRY_NOT_FOUND", f"Inquiry not found: {inquiry_id!r}")
    if str(target.get("status") or "") in INQUIRY_TERMINAL_STATUSES:
        raise V02ConflictError(
            "INQUIRY_ALREADY_SETTLED",
            f"Inquiry {inquiry_id!r} is already settled and accepts no further evidence",
        )
    notes: list[dict[str, Any]] = list(target.get("evidence_notes") or [])
    if len(notes) >= MAX_EVIDENCE_NOTES_PER_INQUIRY:
        raise V02ConflictError(
            "INQUIRY_EVIDENCE_CAPACITY_EXCEEDED",
            f"Inquiry {inquiry_id!r} already holds {MAX_EVIDENCE_NOTES_PER_INQUIRY} evidence notes",
        )
    notes.append({
        "note": note,
        "source_ref": source_ref,
        "recorded_at": obs_iso,
    })
    target["evidence_notes"] = notes

    if payload.get("candidate_explanations") is not None:
        target["candidate_explanations"] = _normalize_candidate_explanations(
            payload.get("candidate_explanations"),
            existing=list(target.get("candidate_explanations") or []),
        )
    if payload.get("next_evidence_needed") is not None:
        target["next_evidence_needed"] = _optional_clean_str(
            payload.get("next_evidence_needed"), "next_evidence_needed", max_len=160
        )

    steps_spent = int(target.get("steps_spent") or 0) + 1
    step_budget = _effective_inquiry_step_budget(target.get("step_budget"))
    target["steps_spent"] = steps_spent
    target["step_budget"] = step_budget
    # 只有开问时显式给了 step_budget 的 inquiry 才启用预算自动收口；
    # 老调用方未传 step_budget 时保持原有"最多 8 条证据"的既有行为不被改写。
    budget_enforced = bool(target.get("step_budget_explicit", False))
    if budget_enforced and steps_spent >= step_budget:
        # 铁律：单条预算到额自动收口，绝不无限追查；同 operation_key 重放在入口即幂等返回。
        target["status"] = INQUIRY_STATUS_STOPPED
        target["stop_reason"] = INQUIRY_STOP_REASON_BUDGET_EXHAUSTED
        target["settled_at"] = obs_iso
    else:
        target["status"] = INQUIRY_STATUS_INVESTIGATING
    target["updated_at"] = obs_iso
    state["inquiries"] = inquiries[:80]


def _persist_inquiry_conclusion_to_s03(
    *,
    day_root: Path | str,
    inquiry_id: str,
    topic_tag: str,
    conclusion: str,
    evidence_refs: list[str],
    has_direct_evidence: bool,
    obs_iso: str,
) -> tuple[Optional[str], Optional[str]]:
    """Persist one concluded bounded inquiry as a real sourced S03 claim (write-path only)."""
    try:
        try:
            from components.life.scripts import s03_claims as _s03
        except ImportError:  # pragma: no cover - flat life/scripts import path
            import s03_claims as _s03  # type: ignore[no-redef]

        paths = _resolve_paths_zero_write(day_root)
        env_db = str(
            os.environ.get("CHIYO_S03_CLAIMS_DB")
            or os.environ.get("NYAIRO_S03_CLAIMS_DB")
            or ""
        ).strip()
        if env_db:
            db_path = Path(env_db)
        elif paths.s03_claims_db.is_file():
            db_path = paths.s03_claims_db
        elif (paths.day_root / "one-day").is_dir() or (paths.day_root / "one-day" / "s03").is_dir():
            db_path = paths.s03_claims_db
        elif (paths.day_root / "s03").is_dir():
            db_path = paths.day_root / "s03" / "claims.sqlite3"
        elif paths.is_managed_instance:
            db_path = paths.s03_claims_db
        else:
            db_path = paths.day_root / "life" / "state" / "s03_claims.sqlite3"
        topic_clean = str(topic_tag or "").strip()
        s03_subject_id = f"inquiry:{topic_clean or inquiry_id}"[:120]
        if topic_clean and _SAFE_S03_TOKEN_RE.match(topic_clean):
            scope_id = topic_clean[:120]
        elif topic_clean:
            digest = hashlib.sha256(topic_clean.encode("utf-8")).hexdigest()[:12]
            scope_id = f"topic_{digest}"
        else:
            safe_scope = re.sub(r"[^A-Za-z0-9._:-]+", "_", inquiry_id).strip("_") or "inq"
            scope_id = safe_scope[:120]

        safe_inq = re.sub(r"[^A-Za-z0-9._:-]+", "_", inquiry_id).strip("_") or "inq"
        source_ref = f"inquiry:{safe_inq}"[:220]
        source_kind = _s03.SOURCE_OBSERVED if has_direct_evidence else _s03.SOURCE_INFERRED
        ev_ref = "|".join(evidence_refs)[:200] if evidence_refs else source_ref
        conc_digest = hashlib.sha256(conclusion.encode("utf-8")).hexdigest()[:16]
        op_key = f"inquiry_conclusion:{safe_inq}:{conc_digest}"

        service = _s03.S03ClaimService(db_path, subject=s03_subject_id, create=True)
        res = service.assert_claim(
            predicate_id="inquiry_conclusion",
            scope_id=scope_id,
            value=conclusion[: _s03.MAX_VALUE_CHARS],
            source_kind=source_kind,
            source_ref=source_ref,
            evidence_ref=ev_ref,
            observed_at=obs_iso,
            now=obs_iso,
            operation_key=op_key,
        )
        claim_id = str(res.get("claim_id") or "").strip() or None
        slot_id = str(res.get("slot_id") or "").strip() or None
        return claim_id, slot_id
    except Exception:
        return None, None


def _infer_previous_belief_from_inquiry(target: Mapping[str, Any], conclusion: str) -> str:
    raw_candidates = target.get("candidate_explanations")
    if not isinstance(raw_candidates, list):
        return ""
    fallback = ""
    for item in raw_candidates:
        if not isinstance(item, Mapping):
            continue
        summary = str(item.get("summary_cn") or "").strip()
        if not summary:
            continue
        stance = str(item.get("stance") or "").strip().lower()
        if stance == "ruled_out":
            return summary[:320]
        if not fallback and summary != conclusion:
            fallback = summary[:320]
    if fallback:
        return fallback
    for item in raw_candidates:
        if isinstance(item, Mapping):
            summary = str(item.get("summary_cn") or "").strip()
            if summary:
                return summary[:320]
    return ""


def _handle_conclude_or_stop_inquiry(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
    *,
    day_root: Optional[Path | str] = None,
) -> None:
    inquiry_id = _require_clean_str(payload.get("inquiry_id"), "inquiry_id", max_len=96)
    outcome = _require_clean_str(payload.get("outcome"), "outcome", max_len=32).upper()
    conclusion = _optional_clean_str(payload.get("conclusion"), "conclusion", max_len=320)
    stop_reason = _optional_clean_str(payload.get("stop_reason"), "stop_reason", max_len=320)
    previous_belief_in = _optional_clean_str(
        payload.get("previous_belief_cn"), "previous_belief_cn", max_len=320
    )

    if outcome not in INQUIRY_TERMINAL_STATUSES:
        raise V02ValidationError(
            "INVALID_INQUIRY_OUTCOME",
            f"outcome must be one of {sorted(INQUIRY_TERMINAL_STATUSES)}",
        )
    if outcome == INQUIRY_STATUS_CONCLUDED and not conclusion:
        raise V02ValidationError(
            "INVALID_FIELDS",
            "Concluding an inquiry requires a non-empty 'conclusion'",
        )
    if outcome == INQUIRY_STATUS_STOPPED and not stop_reason:
        raise V02ValidationError(
            "INVALID_FIELDS",
            "Stopping an inquiry requires a non-empty 'stop_reason'",
        )

    inquiries: list[dict[str, Any]] = list(state.get("inquiries") or [])
    target = _find_inquiry(inquiries, inquiry_id)
    if target is None:
        raise V02NotFoundError("INQUIRY_NOT_FOUND", f"Inquiry not found: {inquiry_id!r}")
    if str(target.get("status") or "") in INQUIRY_TERMINAL_STATUSES:
        raise V02ConflictError(
            "INQUIRY_ALREADY_SETTLED",
            f"Inquiry {inquiry_id!r} is already in a terminal state",
        )

    target["status"] = outcome
    target["conclusion"] = conclusion if outcome == INQUIRY_STATUS_CONCLUDED else ""
    target["stop_reason"] = stop_reason if outcome == INQUIRY_STATUS_STOPPED else ""
    target["settled_at"] = obs_iso
    target["updated_at"] = obs_iso

    if outcome == INQUIRY_STATUS_CONCLUDED and conclusion:
        previous_belief_cn = previous_belief_in or _infer_previous_belief_from_inquiry(
            target, conclusion
        )
        evidence_refs: list[str] = []
        seen_ev: set[str] = set()
        raw_notes = target.get("evidence_notes") or []
        for idx, note_row in enumerate(raw_notes):
            if not isinstance(note_row, Mapping):
                continue
            ref_val = str(note_row.get("source_ref") or "").strip()
            if not ref_val and str(note_row.get("note") or "").strip():
                ref_val = f"inquiry_note:{inquiry_id}:{idx + 1}"
            if ref_val and ref_val not in seen_ev:
                seen_ev.add(ref_val)
                evidence_refs.append(ref_val)

        topic_tag = str(target.get("topic_tag") or "").strip()
        s03_claim_id: Optional[str] = None
        s03_slot_id: Optional[str] = None
        if day_root is not None:
            s03_claim_id, s03_slot_id = _persist_inquiry_conclusion_to_s03(
                day_root=day_root,
                inquiry_id=inquiry_id,
                topic_tag=topic_tag,
                conclusion=conclusion,
                evidence_refs=evidence_refs,
                has_direct_evidence=bool(raw_notes),
                obs_iso=obs_iso,
            )

        revision_doc: dict[str, Any] = {
            "revision_id": _allocate_id(state, "brev"),
            "inquiry_id": inquiry_id,
            "topic_tag": topic_tag,
            "previous_belief_cn": previous_belief_cn,
            "revised_belief_cn": conclusion,
            "evidence_refs": evidence_refs,
            "s03_claim_id": s03_claim_id,
            "s03_slot_id": s03_slot_id,
            "status": "REVISED",
            "shared": bool(target.get("shared", True)),
            "recorded_at": obs_iso,
        }
        revisions: list[dict[str, Any]] = list(state.get("belief_revisions") or [])
        revisions.insert(0, revision_doc)
        state["belief_revisions"] = revisions[:80]

    state["inquiries"] = inquiries[:80]


def _handle_share_inquiry_clue(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    """伴侣侧低压力递一条线索；私密/不存在一律 404，绝不泄漏问题存在性。"""
    inquiry_id = _require_clean_str(payload.get("inquiry_id"), "inquiry_id", max_len=96)
    clue_text = _require_clean_str(payload.get("clue_text"), "clue_text", max_len=160)

    inquiries: list[dict[str, Any]] = list(state.get("inquiries") or [])
    target = _find_inquiry(inquiries, inquiry_id)
    if target is None or not bool(target.get("shared", False)):
        raise V02NotFoundError(
            "INQUIRY_NOT_FOUND",
            f"Shared inquiry not found: {inquiry_id!r}",
        )
    if str(target.get("status") or "") in INQUIRY_TERMINAL_STATUSES:
        raise V02ConflictError(
            "INQUIRY_ALREADY_CLOSED",
            f"Inquiry {inquiry_id!r} is already closed",
        )

    clue_entry = {
        "clue_id": _allocate_id(state, "clue"),
        "clue_text": clue_text,
        "at": obs_iso,
    }
    clues: list[dict[str, Any]] = list(target.get("partner_clues") or [])
    clues.append(clue_entry)
    # 不强迫 TA 改变结论：只追加线索，不改 status / candidate_explanations。
    target["partner_clues"] = clues[-MAX_PARTNER_CLUES_PER_INQUIRY:]
    target["updated_at"] = obs_iso
    state["inquiries"] = inquiries[:80]


def open_bounded_inquiry(
    day_root: Path | str,
    *,
    question: str,
    topic_tag: str = "",
    shared: bool = False,
    candidate_explanations: Optional[list[dict[str, Any]]] = None,
    next_evidence_needed: str = "",
    step_budget: Optional[int] = None,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """Create one bounded ``OPEN`` inquiry through the controlled mutation path.

    Exposed as a named state-machine entry point; identical semantics to the
    ``open_bounded_inquiry`` action (same ``operation_key`` replay is idempotent).
    """

    payload: dict[str, Any] = {
        "action": "open_bounded_inquiry",
        "question": question,
        "topic_tag": topic_tag,
        "shared": shared,
    }
    if candidate_explanations is not None:
        payload["candidate_explanations"] = candidate_explanations
    if next_evidence_needed:
        payload["next_evidence_needed"] = next_evidence_needed
    if step_budget is not None:
        payload["step_budget"] = step_budget
    if operation_key is not None:
        payload["operation_key"] = operation_key
    return apply_agency_depth_mutation(
        day_root, payload, actor_side="subject", now_iso=now_iso
    )


def record_inquiry_evidence(
    day_root: Path | str,
    *,
    inquiry_id: str,
    note: str,
    source_ref: str = "",
    candidate_explanations: Optional[list[dict[str, Any]]] = None,
    next_evidence_needed: Optional[str] = None,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """Append one real observation note and advance ``OPEN -> INVESTIGATING``."""

    payload: dict[str, Any] = {
        "action": "record_inquiry_evidence",
        "inquiry_id": inquiry_id,
        "note": note,
        "source_ref": source_ref,
    }
    if candidate_explanations is not None:
        payload["candidate_explanations"] = candidate_explanations
    if next_evidence_needed is not None:
        payload["next_evidence_needed"] = next_evidence_needed
    if operation_key is not None:
        payload["operation_key"] = operation_key
    return apply_agency_depth_mutation(
        day_root, payload, actor_side="subject", now_iso=now_iso
    )


def conclude_or_stop_inquiry(
    day_root: Path | str,
    *,
    inquiry_id: str,
    outcome: str,
    conclusion: str = "",
    stop_reason: str = "",
    previous_belief_cn: str = "",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """Move an active inquiry to a terminal ``CONCLUDED`` or ``STOPPED`` state."""

    payload: dict[str, Any] = {
        "action": "conclude_or_stop_inquiry",
        "inquiry_id": inquiry_id,
        "outcome": outcome,
        "conclusion": conclusion,
        "stop_reason": stop_reason,
    }
    if previous_belief_cn:
        payload["previous_belief_cn"] = previous_belief_cn
    if operation_key is not None:
        payload["operation_key"] = operation_key
    return apply_agency_depth_mutation(
        day_root, payload, actor_side="subject", now_iso=now_iso
    )


def _find_by_key(
    items: list[dict[str, Any]],
    key_name: str,
    key_val: str,
) -> Optional[dict[str, Any]]:
    for item in items:
        if isinstance(item, dict) and item.get(key_name) == key_val:
            return item
    return None


def _handle_place_memory_cue(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    cue_id = _require_clean_str(payload.get("cue_id"), "cue_id", max_len=96)
    artifact_ref = _require_clean_str(payload.get("artifact_ref"), "artifact_ref", max_len=240)
    spot_id = _require_clean_str(payload.get("spot_id"), "spot_id", max_len=96)
    cue_text_cn = _require_clean_str(payload.get("cue_text_cn"), "cue_text_cn", max_len=240)
    visible = _optional_bool(payload.get("visible"), "visible", default=True)
    shared = _optional_bool(payload.get("shared"), "shared", default=False)

    cues: list[dict[str, Any]] = list(state.get("placed_memory_cues") or [])
    target = _find_by_key(cues, "cue_id", cue_id)
    if target is None:
        cues.insert(0, {
            "cue_id": cue_id,
            "artifact_ref": artifact_ref,
            "spot_id": spot_id,
            "placed_spot_id": spot_id,
            "cue_text_cn": cue_text_cn,
            "visible": visible,
            "occluded": False,
            "removed": False,
            "moved_away": False,
            "shared": shared,
            "placed_at": obs_iso,
            "updated_at": obs_iso,
        })
    else:
        target["artifact_ref"] = artifact_ref
        target["spot_id"] = spot_id
        target["placed_spot_id"] = spot_id
        target["cue_text_cn"] = cue_text_cn
        target["visible"] = visible
        target["occluded"] = False
        target["removed"] = False
        target["moved_away"] = False
        if payload.get("shared") is not None:
            target["shared"] = shared
        target["updated_at"] = obs_iso
    state["placed_memory_cues"] = cues[:120]


def _handle_update_placed_cue_visibility(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    cue_id = _require_clean_str(payload.get("cue_id"), "cue_id", max_len=96)
    cues: list[dict[str, Any]] = list(state.get("placed_memory_cues") or [])
    target = _find_by_key(cues, "cue_id", cue_id)
    if target is None:
        raise V02NotFoundError("CUE_NOT_FOUND", f"Placed memory cue not found: {cue_id!r}")

    if "spot_id" in payload:
        raw_spot = payload.get("spot_id")
        new_spot = "" if raw_spot is None else _optional_clean_str(raw_spot, "spot_id", max_len=96)
        target["spot_id"] = new_spot
        placed_spot = str(target.get("placed_spot_id") or "").strip()
        target["moved_away"] = (not new_spot) or (bool(placed_spot) and new_spot != placed_spot)

    if payload.get("visible") is not None:
        target["visible"] = _optional_bool(
            payload.get("visible"), "visible", default=bool(target.get("visible", True))
        )
    if payload.get("occluded") is not None:
        target["occluded"] = _optional_bool(
            payload.get("occluded"), "occluded", default=bool(target.get("occluded", False))
        )
    if payload.get("removed") is not None:
        target["removed"] = _optional_bool(
            payload.get("removed"), "removed", default=bool(target.get("removed", False))
        )
    target["updated_at"] = obs_iso
    state["placed_memory_cues"] = cues[:120]


def _handle_curate_info_source(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    source_id = _require_clean_str(payload.get("source_id"), "source_id", max_len=96)
    title_cn = _require_clean_str(payload.get("title_cn"), "title_cn", max_len=160)
    mode = _require_clean_str(payload.get("mode"), "mode", max_len=32).lower()
    if mode not in INFO_CURATION_MODES:
        raise V02ValidationError("INVALID_CURATION_MODE", f"Unsupported info curation mode: {mode!r}")
    reason_cn = _optional_clean_str(payload.get("reason_cn"), "reason_cn", max_len=240)

    curations: list[dict[str, Any]] = list(state.get("info_environment_curations") or [])
    target = _find_by_key(curations, "source_id", source_id)
    is_new = target is None
    default_shared = False if is_new else bool(target.get("shared", False))
    shared = _optional_bool(payload.get("shared"), "shared", default=default_shared)

    if target is None:
        curations.append({
            "source_id": source_id,
            "title_cn": title_cn,
            "mode": mode,
            "reason_cn": reason_cn,
            "shared": shared,
            "created_at": obs_iso,
            "updated_at": obs_iso,
        })
    else:
        target["title_cn"] = title_cn
        target["mode"] = mode
        if payload.get("reason_cn") is not None:
            target["reason_cn"] = reason_cn
        if payload.get("shared") is not None:
            target["shared"] = shared
        target["updated_at"] = obs_iso
    state["info_environment_curations"] = curations[:120]


def _handle_create_future_capsule(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    capsule_id = _require_clean_str(payload.get("capsule_id"), "capsule_id", max_len=96)
    unlock_after = _require_clean_str(payload.get("unlock_after"), "unlock_after", max_len=64)
    if _parse_iso_utc(unlock_after) is None:
        raise V02ValidationError("INVALID_FIELDS", f"Malformed ISO timestamp for 'unlock_after': {unlock_after!r}")
    title_cn = _require_clean_str(payload.get("title_cn"), "title_cn", max_len=160)
    note_cn = _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=320)
    artifact_ref = _optional_clean_str(payload.get("artifact_ref"), "artifact_ref", max_len=240)
    shared = _optional_bool(payload.get("shared"), "shared", default=False)

    capsules: list[dict[str, Any]] = list(state.get("future_self_capsules") or [])
    if _find_by_key(capsules, "capsule_id", capsule_id) is not None:
        raise V02ConflictError("CAPSULE_ALREADY_EXISTS", f"Future capsule already exists: {capsule_id!r}")

    capsules.insert(0, {
        "capsule_id": capsule_id,
        "unlock_after": unlock_after,
        "title_cn": title_cn,
        "note_cn": note_cn,
        "artifact_ref": artifact_ref,
        "shared": shared,
        "decision": None,
        "status": "LOCKED",
        "auto_claimed": False,
        "omission_debt": 0,
        "must_fulfill_old_wish": False,
        "created_at": obs_iso,
        "resolved_at": None,
        "updated_at": obs_iso,
    })
    state["future_self_capsules"] = capsules[:120]


def _handle_resolve_future_capsule(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    capsule_id = _require_clean_str(payload.get("capsule_id"), "capsule_id", max_len=96)
    decision = _require_clean_str(payload.get("decision"), "decision", max_len=40)
    if decision not in FUTURE_CAPSULE_DECISIONS:
        raise V02ValidationError("INVALID_CAPSULE_DECISION", f"Unsupported capsule decision: {decision!r}")

    capsules: list[dict[str, Any]] = list(state.get("future_self_capsules") or [])
    target = _find_by_key(capsules, "capsule_id", capsule_id)
    if target is None:
        raise V02NotFoundError("CAPSULE_NOT_FOUND", f"Future capsule not found: {capsule_id!r}")

    if target.get("decision") in ("open", "decline_no_debt"):
        raise V02ConflictError("CAPSULE_ALREADY_RESOLVED", f"Future capsule already resolved: {capsule_id!r}")

    obs_dt = _parse_iso_utc(obs_iso)
    unlock_dt = _parse_iso_utc(target.get("unlock_after"))
    if decision == "open" and obs_dt is not None and unlock_dt is not None and obs_dt < unlock_dt:
        raise V02ConflictError("CAPSULE_LOCKED", f"Future capsule {capsule_id!r} cannot be opened before unlock_after")

    status_map = {
        "open": "OPENED",
        "decline_no_debt": "DECLINED",
        "keep_closed": "KEPT_CLOSED",
    }
    target["decision"] = decision
    target["status"] = status_map[decision]
    target["auto_claimed"] = False
    target["omission_debt"] = 0
    target["must_fulfill_old_wish"] = False
    target["resolved_at"] = obs_iso
    target["updated_at"] = obs_iso
    state["future_self_capsules"] = capsules[:120]


def _handle_set_optional_next_gesture(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    gesture_id = _require_clean_str(payload.get("gesture_id"), "gesture_id", max_len=96)
    artifact_ref = _require_clean_str(payload.get("artifact_ref"), "artifact_ref", max_len=240)
    gesture_cn = _require_clean_str(payload.get("gesture_cn"), "gesture_cn", max_len=MAX_OPTIONAL_GESTURE_LEN)
    shared = _optional_bool(payload.get("shared"), "shared", default=False)

    gestures: list[dict[str, Any]] = list(state.get("optional_next_gestures") or [])
    target = _find_by_key(gestures, "gesture_id", gesture_id)
    if target is None:
        gestures.insert(0, {
            "gesture_id": gesture_id,
            "artifact_ref": artifact_ref,
            "gesture_cn": gesture_cn,
            "status": "OFFERED",
            "claimed": False,
            "activity_resumed": False,
            "peek_count": 0,
            "reframed_question_cn": "",
            "omission_debt": 0,
            "auto_refresh": False,
            "shared": shared,
            "created_at": obs_iso,
            "last_peeked_at": None,
            "acted_at": None,
            "updated_at": obs_iso,
        })
    else:
        target["artifact_ref"] = artifact_ref
        target["gesture_cn"] = gesture_cn
        target["status"] = "OFFERED"
        target["claimed"] = False
        target["activity_resumed"] = False
        target["omission_debt"] = 0
        target["auto_refresh"] = False
        if payload.get("shared") is not None:
            target["shared"] = shared
        target["updated_at"] = obs_iso
    state["optional_next_gestures"] = gestures[:120]


def _handle_peek_optional_next_gesture(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    gesture_id = _require_clean_str(payload.get("gesture_id"), "gesture_id", max_len=96)
    gestures: list[dict[str, Any]] = list(state.get("optional_next_gestures") or [])
    target = _find_by_key(gestures, "gesture_id", gesture_id)
    if target is None:
        raise V02NotFoundError("GESTURE_NOT_FOUND", f"Optional next gesture not found: {gesture_id!r}")
    if target.get("status") == "ADOPTED":
        raise V02ConflictError("GESTURE_ALREADY_ADOPTED", f"Optional next gesture {gesture_id!r} is already adopted")

    # 铁律：点开/偷看只累加 peek_count，绝不等于重新认领或恢复旧活动。
    target["peek_count"] = int(target.get("peek_count") or 0) + 1
    target["claimed"] = False
    target["activity_resumed"] = False
    target["omission_debt"] = 0
    target["last_peeked_at"] = obs_iso
    target["updated_at"] = obs_iso
    state["optional_next_gestures"] = gestures[:120]


def _handle_act_or_archive_gesture(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    gesture_id = _require_clean_str(payload.get("gesture_id"), "gesture_id", max_len=96)
    decision = _require_clean_str(payload.get("decision"), "decision", max_len=40)
    if decision not in OPTIONAL_GESTURE_DECISIONS:
        raise V02ValidationError("INVALID_GESTURE_DECISION", f"Unsupported gesture decision: {decision!r}")

    gestures: list[dict[str, Any]] = list(state.get("optional_next_gestures") or [])
    target = _find_by_key(gestures, "gesture_id", gesture_id)
    if target is None:
        raise V02NotFoundError("GESTURE_NOT_FOUND", f"Optional next gesture not found: {gesture_id!r}")

    if decision == "adopt_step":
        target["status"] = "ADOPTED"
        target["claimed"] = True
        target["activity_resumed"] = True
        target["acted_at"] = obs_iso
    elif decision == "reframe_question":
        new_q = _optional_clean_str(payload.get("new_question_cn"), "new_question_cn", max_len=160)
        if not new_q:
            raise V02ValidationError("INVALID_FIELDS", "decision='reframe_question' requires non-empty 'new_question_cn'")
        target["status"] = "REFRAMED"
        target["reframed_question_cn"] = new_q
        target["claimed"] = False
        target["activity_resumed"] = False
        target["acted_at"] = obs_iso
    else:
        target["status"] = "ARCHIVED"
        target["claimed"] = False
        target["activity_resumed"] = False
        target["omission_debt"] = 0
        target["acted_at"] = obs_iso

    target["updated_at"] = obs_iso
    state["optional_next_gestures"] = gestures[:120]


def _handle_settle_legacy_tool(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    tool_id = _require_clean_str(payload.get("tool_id"), "tool_id", max_len=96)
    title_cn = _require_clean_str(payload.get("title_cn"), "title_cn", max_len=160)
    dependency_error_cn = _require_clean_str(
        payload.get("dependency_error_cn"), "dependency_error_cn", max_len=240
    )
    raw_refs = payload.get("linked_work_refs")
    if not isinstance(raw_refs, list) or len(raw_refs) > 32:
        raise V02ValidationError("INVALID_FIELDS", "Field 'linked_work_refs' must be a list of strings")
    cleaned_refs: list[str] = []
    seen_refs: set[str] = set()
    for idx, item in enumerate(raw_refs):
        ref = _require_clean_str(item, f"linked_work_refs[{idx}]", max_len=240)
        if ref not in seen_refs:
            seen_refs.add(ref)
            cleaned_refs.append(ref)

    ending = _require_clean_str(payload.get("ending"), "ending", max_len=48)
    if ending not in LEGACY_TOOL_ENDINGS:
        raise V02ValidationError("INVALID_TOOL_ENDING", f"Unsupported legacy tool ending: {ending!r}")

    fallback_ref = _optional_clean_str(
        payload.get("fallback_artifact_ref"), "fallback_artifact_ref", max_len=240
    )

    tools: list[dict[str, Any]] = list(state.get("legacy_tool_endings") or [])
    target = _find_by_key(tools, "tool_id", tool_id)
    is_new = target is None
    default_shared = False if is_new else bool(target.get("shared", False))
    shared = _optional_bool(payload.get("shared"), "shared", default=default_shared)

    status_map = {
        "keep_repaired": "REPAIRED",
        "adapt_to_static_card": "ADAPTED_STATIC",
        "bid_farewell": "ARCHIVED_FAREWELL",
    }
    doc_fields = {
        "tool_id": tool_id,
        "title_cn": title_cn,
        "dependency_error_cn": dependency_error_cn,
        "linked_work_refs": cleaned_refs,
        "ending": ending,
        "status": status_map[ending],
        "executable": ending == "keep_repaired",
        "linked_works_readable": True,
        "failure_penalty": 0,
        "fallback_artifact_ref": fallback_ref,
        "archived": ending == "bid_farewell",
        "static_fallback_active": ending == "adapt_to_static_card",
        "shared": shared,
        "settled_at": obs_iso,
        "updated_at": obs_iso,
    }
    if target is None:
        tools.insert(0, doc_fields)
    else:
        target.update(doc_fields)
    state["legacy_tool_endings"] = tools[:120]


def _handle_register_gentle_responsibility(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    resp_id = _require_clean_str(payload.get("resp_id"), "resp_id", max_len=96)
    title_cn = _require_clean_str(payload.get("title_cn"), "title_cn", max_len=160)
    target_kind = _require_clean_str(payload.get("target_kind"), "target_kind", max_len=48)
    if target_kind not in RESPONSIBILITY_TARGET_KINDS:
        raise V02ValidationError(
            "INVALID_RESPONSIBILITY_KIND", f"Unsupported target_kind: {target_kind!r}"
        )
    last_touched_at = _require_clean_str(payload.get("last_touched_at"), "last_touched_at", max_len=64)
    if _parse_iso_utc(last_touched_at) is None:
        raise V02ValidationError(
            "INVALID_FIELDS", f"Malformed ISO timestamp for 'last_touched_at': {last_touched_at!r}"
        )
    shared = _optional_bool(payload.get("shared"), "shared", default=False)

    resps: list[dict[str, Any]] = list(state.get("gentle_responsibility_reviews") or [])
    target = _find_by_key(resps, "resp_id", resp_id)
    if target is None:
        resps.insert(0, {
            "resp_id": resp_id,
            "title_cn": title_cn,
            "target_kind": target_kind,
            "last_touched_at": last_touched_at,
            "status": "ACTIVE_GENTLE",
            "last_decision": None,
            "auto_check_paused": False,
            "history_preserved": True,
            "overdue_alert": False,
            "omission_debt": 0,
            "partner_agreed": False,
            "shared": shared,
            "created_at": obs_iso,
            "reviewed_at": None,
            "updated_at": obs_iso,
        })
    else:
        target["title_cn"] = title_cn
        target["target_kind"] = target_kind
        target["last_touched_at"] = last_touched_at
        if payload.get("shared") is not None:
            target["shared"] = shared
        target["overdue_alert"] = False
        target["omission_debt"] = 0
        target["history_preserved"] = True
        target["updated_at"] = obs_iso
    state["gentle_responsibility_reviews"] = resps[:120]


def _handle_review_gentle_responsibility(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    resp_id = _require_clean_str(payload.get("resp_id"), "resp_id", max_len=96)
    decision = _require_clean_str(payload.get("decision"), "decision", max_len=48)
    if decision not in RESPONSIBILITY_REVIEW_DECISIONS:
        raise V02ValidationError(
            "INVALID_REVIEW_DECISION", f"Unsupported responsibility review decision: {decision!r}"
        )
    partner_agreed = _optional_bool(payload.get("partner_agreed"), "partner_agreed", default=False)

    resps: list[dict[str, Any]] = list(state.get("gentle_responsibility_reviews") or [])
    target = _find_by_key(resps, "resp_id", resp_id)
    if target is None:
        raise V02NotFoundError("RESPONSIBILITY_NOT_FOUND", f"Gentle responsibility not found: {resp_id!r}")

    # 铁律：对他人共同承诺不可单方面宣布退役，必须完成伴侣协同确认。
    if (
        target.get("target_kind") == "shared_partner_commitment"
        and decision == "retire_keep_history"
        and not partner_agreed
    ):
        raise V02ConflictError(
            "EXTERNAL_COMMITMENT_REQUIRES_HANDOFF",
            f"Shared partner commitment {resp_id!r} cannot be retired without partner agreement",
        )

    if decision == "one_small_step":
        target["status"] = "ACTIVE_GENTLE"
        target["last_touched_at"] = obs_iso
        target["auto_check_paused"] = False
    elif decision == "pause_auto_check":
        target["status"] = "AUTO_CHECK_PAUSED"
        target["auto_check_paused"] = True
    else:
        target["status"] = "RETIRED_HISTORY_KEPT"
        target["auto_check_paused"] = True
        target["partner_agreed"] = bool(partner_agreed)

    target["last_decision"] = decision
    target["history_preserved"] = True
    target["overdue_alert"] = False
    target["omission_debt"] = 0
    target["reviewed_at"] = obs_iso
    target["updated_at"] = obs_iso
    state["gentle_responsibility_reviews"] = resps[:120]


def _trigger_window_active(trigger_window: Any, local_dt: datetime) -> bool:
    win = str(trigger_window or "").strip().lower()
    if not win or win in ("anytime", "always", "all", "all_day", "daily", "全天", "随时"):
        return True
    hour = local_dt.hour
    minute = local_dt.minute
    cur_mins = hour * 60 + minute

    match = _WINDOW_HHMM_RANGE_RE.search(win)
    if match:
        h1 = int(match.group(1))
        m1 = int(match.group(2) or 0)
        h2 = int(match.group(3))
        m2 = int(match.group(4) or 0)
        start_m = (h1 % 24) * 60 + min(59, m1)
        end_m = 24 * 60 if (h2 == 24 and m2 == 0) else ((h2 % 24) * 60 + min(59, m2))
        if start_m <= end_m:
            return start_m <= cur_mins <= end_m
        return cur_mins >= start_m or cur_mins <= end_m

    if any(
        tok in win
        for tok in (
            "late_night",
            "midnight",
            "bedtime",
            "night",
            "晚间",
            "夜晚",
            "深夜",
            "夜间",
            "睡前",
            "熬夜",
        )
    ):
        return hour >= 21 or hour < 6
    if any(tok in win for tok in ("evening", "傍晚", "晚上")):
        return 18 <= hour < 24
    if any(tok in win for tok in ("early_morning", "清晨", "早晨", "早上")):
        return 5 <= hour <= 9
    if any(tok in win for tok in ("morning", "上午")):
        return 5 <= hour < 12
    if any(tok in win for tok in ("noon", "midday", "午休", "中午")):
        return 12 <= hour <= 14
    if any(tok in win for tok in ("afternoon", "午后", "下午")):
        return 12 <= hour < 18
    return False


def _extract_rule_topic_tokens(rule_text: str) -> tuple[set[str], set[str]]:
    ascii_tokens = {
        tok
        for tok in re.findall(r"[a-z0-9_]{3,}", rule_text)
        if tok not in _RULE_ASCII_STOPWORDS
    }
    cleaned_cn = rule_text
    for phrase in _RULE_CN_TIME_AND_FILLER:
        cleaned_cn = cleaned_cn.replace(phrase, " ")
    cn_tokens: set[str] = set()
    for chunk in re.split(r"[\s,，。、.;:：!?！？/\\|()（）\[\]【】不别勿少后前的了和与或把给去再先]+", cleaned_cn):
        seg = chunk.strip()
        if not seg or not re.search(r"[\u4e00-\u9fff]", seg):
            continue
        if 2 <= len(seg) <= 8:
            cn_tokens.add(seg)
            if len(seg) >= 3 and seg[0] in _RULE_CN_LEADING_VERBS:
                cn_tokens.add(seg[1:])
    return ascii_tokens, cn_tokens


def _rule_matches_candidate_scope(
    title: str,
    reason_cn: str,
    *,
    candidate_kind: str,
    candidate_title: str,
    tags: Any,
) -> bool:
    rule_text = f"{title} {reason_cn}".strip().lower()
    if isinstance(tags, (str, bytes)):
        tag_items = [str(tags).strip()] if str(tags).strip() else []
    else:
        tag_items = [str(t).strip() for t in (tags or ()) if str(t).strip()]
    cand_text = " ".join(
        part
        for part in (
            str(candidate_kind or "").strip(),
            str(candidate_title or "").strip(),
            *tag_items,
        )
        if part
    ).lower()
    if not rule_text or not cand_text:
        return False

    for rule_kws, cand_kws in _SECOND_ORDER_SCOPE_GROUPS:
        if any(rk in rule_text for rk in rule_kws) and any(ck in cand_text for ck in cand_kws):
            return True

    ascii_tokens, cn_tokens = _extract_rule_topic_tokens(rule_text)
    if ascii_tokens:
        cand_ascii = set(re.findall(r"[a-z0-9_]{3,}", cand_text))
        if ascii_tokens & cand_ascii:
            return True
        if any(tok in cand_text for tok in ascii_tokens):
            return True
    for cn_tok in cn_tokens:
        if cn_tok in cand_text:
            return True
    return False


def evaluate_second_order_constraints(
    day_root: Path | str,
    *,
    candidate_kind: str = "",
    candidate_title: str = "",
    tags: Any = (),
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write check of active second-order rules against a scheduling candidate."""
    exists, raw_state = _load_state_readonly(day_root)
    if not exists:
        return {
            "constrained": False,
            "blocking_rules": [],
            "reason_cn": "",
        }
    obs_iso = _now_iso_str(now_iso)
    obs_utc = _parse_iso_utc(obs_iso) or datetime.now(timezone.utc)
    local_dt = obs_utc.astimezone(LOCAL_TZ)

    blocking_rules: list[dict[str, Any]] = []
    for rl in raw_state.get("second_order_rules") or []:
        if not isinstance(rl, Mapping):
            continue
        status = str(rl.get("status") or "").strip().lower()
        if status not in ACTIVE_SECOND_ORDER_RULE_STATUSES:
            continue
        trig_window = str(rl.get("trigger_window") or "")
        if not _trigger_window_active(trig_window, local_dt):
            continue
        title = str(rl.get("title") or "")
        reason_cn = str(rl.get("reason_cn") or "")
        if not _rule_matches_candidate_scope(
            title,
            reason_cn,
            candidate_kind=candidate_kind,
            candidate_title=candidate_title,
            tags=tags,
        ):
            continue
        blocking_rules.append({
            "rule_id": str(rl.get("rule_id") or ""),
            "title": title,
            "trigger_window": trig_window,
        })

    reason_text = (
        "受二阶小规矩约束：" + "；".join(r["title"] for r in blocking_rules[:2])
        if blocking_rules
        else ""
    )
    return {
        "constrained": bool(blocking_rules),
        "blocking_rules": blocking_rules,
        "reason_cn": reason_text,
    }


def record_second_order_enforcement(
    day_root: Path | str,
    *,
    rule_id: str,
    rule_title: str,
    candidate_ref: str,
    candidate_title: str,
    effect: str = "DEFERRED",
    reason_cn: str = "",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Persist one bounded second-order rule enforcement receipt under lock (max 40, newest last)."""
    clean_rule_id = _require_clean_str(rule_id, "rule_id", max_len=96)
    clean_rule_title = _require_clean_str(rule_title, "rule_title", max_len=160)
    clean_cand_ref = _require_clean_str(candidate_ref, "candidate_ref", max_len=200)
    clean_cand_title = _require_clean_str(candidate_title, "candidate_title", max_len=200)
    clean_effect = _optional_clean_str(effect, "effect", max_len=40).upper() or "DEFERRED"
    clean_reason = _optional_clean_str(reason_cn, "reason_cn", max_len=300)
    obs_iso = _now_iso_str(now_iso)

    with _tx_guard.state_file_lock(_state_lock_path(day_root)):
        _exists, state = _load_state_readonly(day_root)
        backing_shared = False
        for rl in state.get("second_order_rules") or []:
            if isinstance(rl, Mapping) and str(rl.get("rule_id") or "") == clean_rule_id:
                backing_shared = bool(rl.get("shared", False))
                break
        entry: dict[str, Any] = {
            "enforcement_id": _allocate_id(state, "renf"),
            "rule_id": clean_rule_id,
            "rule_title": clean_rule_title,
            "candidate_ref": clean_cand_ref,
            "candidate_title": clean_cand_title,
            "effect": clean_effect,
            "reason_cn": clean_reason,
            "shared": backing_shared,
            "recorded_at": obs_iso,
        }
        enforcements: list[dict[str, Any]] = list(state.get("rule_enforcements") or [])
        enforcements.append(entry)
        state["rule_enforcements"] = enforcements[-MAX_RULE_ENFORCEMENTS:]
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = obs_iso
        _atomic_write_state(day_root, state)
        return copy.deepcopy(entry)


def _find_responsibility_in_state(
    state: dict[str, Any],
    resp_id: str,
) -> tuple[Optional[dict[str, Any]], str]:
    for key in ("gentle_responsibility_reviews", "gentle_responsibilities"):
        rows = state.get(key)
        if not isinstance(rows, list):
            continue
        found = _find_by_key(rows, "resp_id", resp_id)
        if found is not None:
            return found, key
    return None, "gentle_responsibility_reviews"


def _handle_resolve_commitment_value_conflict(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
) -> None:
    resp_id = _require_clean_str(payload.get("resp_id"), "resp_id", max_len=96)
    new_value_cn = _require_clean_str(payload.get("new_value_cn"), "new_value_cn", max_len=240)
    conflict_note_cn = _optional_clean_str(
        payload.get("conflict_note_cn"), "conflict_note_cn", max_len=320
    )
    tradeoff_decision = _require_clean_str(
        payload.get("tradeoff_decision"), "tradeoff_decision", max_len=48
    )
    if tradeoff_decision not in COMMITMENT_TRADEOFF_DECISIONS:
        raise V02ValidationError(
            "INVALID_TRADEOFF_DECISION",
            f"Unsupported tradeoff_decision: {tradeoff_decision!r}",
        )
    partner_agreed = _optional_bool(payload.get("partner_agreed"), "partner_agreed", default=False)

    target, list_key = _find_responsibility_in_state(state, resp_id)
    if target is None:
        raise V02NotFoundError(
            "RESPONSIBILITY_NOT_FOUND",
            f"Gentle responsibility not found: {resp_id!r}",
        )

    # S08 F-095 invariant: retiring a shared partner commitment requires explicit partner handoff.
    if (
        target.get("target_kind") == "shared_partner_commitment"
        and tradeoff_decision == "hand_off_and_retire"
        and not partner_agreed
    ):
        raise V02ConflictError(
            "EXTERNAL_COMMITMENT_REQUIRES_HANDOFF",
            f"Shared partner commitment {resp_id!r} cannot be retired without partner agreement",
        )

    default_shared = bool(target.get("shared", False))
    shared = _optional_bool(payload.get("shared"), "shared", default=default_shared)

    status_map = {
        "keep_and_adapt": "adapted",
        "rescale_with_partner": "rescaled_pending_partner",
        "hand_off_and_retire": "retired_keep_history",
    }
    target["status"] = status_map[tradeoff_decision]
    target["last_decision"] = tradeoff_decision
    target["history_preserved"] = True
    target["overdue_alert"] = False
    target["omission_debt"] = 0
    if tradeoff_decision == "hand_off_and_retire":
        target["auto_check_paused"] = True
        target["partner_agreed"] = bool(partner_agreed)
    target["reviewed_at"] = obs_iso
    target["updated_at"] = obs_iso
    state[list_key] = list(state.get(list_key) or [])[:120]

    resp_title = str(target.get("title_cn") or target.get("title") or resp_id)
    tradeoff_doc: dict[str, Any] = {
        "tradeoff_id": _allocate_id(state, "ctrd"),
        "resp_id": resp_id,
        "resp_title": resp_title,
        "new_value_cn": new_value_cn,
        "conflict_note_cn": conflict_note_cn,
        "tradeoff_decision": tradeoff_decision,
        "status": "RESOLVED",
        "proposed_by": "subject",
        "partner_agreed": bool(partner_agreed),
        "shared": shared,
        "recorded_at": obs_iso,
    }
    tradeoffs: list[dict[str, Any]] = list(state.get("commitment_tradeoffs") or [])
    tradeoffs.insert(0, tradeoff_doc)
    state["commitment_tradeoffs"] = tradeoffs[:80]


def _handle_propose_commitment_tradeoff(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    obs_iso: str,
    *,
    actor_side: str = "partner",
) -> None:
    resp_id = _optional_clean_str(payload.get("resp_id"), "resp_id", max_len=96)
    resp_title_in = _optional_clean_str(payload.get("resp_title"), "resp_title", max_len=160)
    new_value_cn = _require_clean_str(payload.get("new_value_cn"), "new_value_cn", max_len=240)
    conflict_note_cn = _optional_clean_str(
        payload.get("conflict_note_cn"), "conflict_note_cn", max_len=320
    )
    suggested_decision = _require_clean_str(
        payload.get("suggested_decision"), "suggested_decision", max_len=48
    )
    if suggested_decision not in COMMITMENT_TRADEOFF_DECISIONS:
        raise V02ValidationError(
            "INVALID_TRADEOFF_DECISION",
            f"Unsupported suggested_decision: {suggested_decision!r}",
        )

    eff_title = resp_title_in
    if resp_id:
        found, _ = _find_responsibility_in_state(state, resp_id)
        if found is not None:
            if actor_side == "partner" and not bool(found.get("shared", False)):
                raise V02NotFoundError(
                    "RESPONSIBILITY_NOT_FOUND",
                    f"Shared responsibility not found: {resp_id!r}",
                )
            if not eff_title:
                eff_title = str(found.get("title_cn") or found.get("title") or resp_id)
    if not eff_title:
        eff_title = resp_id or "共同承诺调整建议"

    # Never mutate or retire TA's responsibility from a partner proposal.
    tradeoff_doc: dict[str, Any] = {
        "tradeoff_id": _allocate_id(state, "ctrd"),
        "resp_id": resp_id,
        "resp_title": eff_title,
        "new_value_cn": new_value_cn,
        "conflict_note_cn": conflict_note_cn,
        "tradeoff_decision": suggested_decision,
        "suggested_decision": suggested_decision,
        "status": "PROPOSED_BY_PARTNER",
        "proposed_by": "partner",
        "partner_agreed": True,
        "shared": True,
        "recorded_at": obs_iso,
    }
    tradeoffs: list[dict[str, Any]] = list(state.get("commitment_tradeoffs") or [])
    tradeoffs.insert(0, tradeoff_doc)
    state["commitment_tradeoffs"] = tradeoffs[:80]


_KNOWN_MUTATION_ACTIONS: frozenset[str] = frozenset({
    "record_choice_episode",
    "record_exposure_satiation",
    "offer_low_cost_option",
    "withdraw_partner_option",
    "park_or_resolve_conflict",
    "record_regret_reflection",
    "propose_shared_gentle_rule",
    "manage_second_order_rule",
    "manage_open_incubation",
    "share_incubation_clue",
    "open_bounded_inquiry",
    "record_inquiry_evidence",
    "share_inquiry_clue",
    "conclude_or_stop_inquiry",
    "place_memory_cue",
    "update_placed_cue_visibility",
    "curate_info_source",
    "create_future_capsule",
    "resolve_future_capsule",
    "set_optional_next_gesture",
    "peek_optional_next_gesture",
    "act_or_archive_gesture",
    "settle_legacy_tool",
    "register_gentle_responsibility",
    "review_gentle_responsibility",
    "resolve_commitment_value_conflict",
    "propose_commitment_tradeoff",
})


def apply_agency_depth_mutation(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """执行受控状态变更并原子落盘，支持 ``state_file_lock`` 与 ``operation_key`` 幂等重放。"""
    if actor_side not in ("partner", "subject"):
        raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {actor_side!r}")

    obs_iso = _now_iso_str(now_iso)
    has_cap = actor_side == "subject"

    expected_rev = payload.get("expected_revision")
    if expected_rev is not None:
        if not _is_strict_int(expected_rev) or expected_rev < 0:
            raise V02ValidationError("INVALID_FIELDS", "expected_revision must be a non-negative integer")

    op_key = _optional_clean_str(payload.get("operation_key"), "operation_key", max_len=128)
    action = _require_clean_str(payload.get("action"), "action", max_len=64)
    if action not in _KNOWN_MUTATION_ACTIONS:
        raise V02ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")

    fp = _compute_payload_fingerprint(actor_side, payload)

    with _tx_guard.state_file_lock(_state_lock_path(day_root)):
        _exists, state = _load_state_readonly(day_root)

        if expected_rev is not None and int(state.get("revision") or 0) != expected_rev:
            raise V02ConflictError("REVISION_MISMATCH", "State revision does not match expected_revision")

        ledger = state.setdefault("operation_ledger", {})
        if op_key:
            existing_entry = ledger.get(op_key)
            if isinstance(existing_entry, Mapping):
                if existing_entry.get("fingerprint") != fp:
                    raise V02ConflictError(
                        "OPERATION_KEY_CONFLICT",
                        f"operation_key {op_key!r} already used with a different payload",
                    )
                projected = project_v02_agency_depth(
                    day_root,
                    has_subject_capability=has_cap,
                    now_iso=obs_iso,
                )
                return projected, "DUPLICATE"

        if action == "record_choice_episode":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can record choice episodes")
            _handle_record_choice_episode(state, payload, obs_iso)
        elif action == "record_exposure_satiation":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can record exposure satiation")
            _handle_record_exposure_satiation(state, payload, obs_iso)
        elif action == "offer_low_cost_option":
            _handle_offer_low_cost_option(state, payload, obs_iso)
        elif action == "withdraw_partner_option":
            _handle_withdraw_partner_option(state, payload, obs_iso)
        elif action == "park_or_resolve_conflict":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can park or resolve conflicts")
            _handle_park_or_resolve_conflict(state, payload, obs_iso)
        elif action == "record_regret_reflection":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can record regret reflections")
            _handle_record_regret_reflection(state, payload, obs_iso)
        elif action == "propose_shared_gentle_rule":
            _handle_propose_shared_gentle_rule(state, payload, obs_iso)
        elif action == "manage_second_order_rule":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can manage second-order rules")
            _handle_manage_second_order_rule(state, payload, obs_iso)
        elif action == "manage_open_incubation":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can manage open incubations")
            _handle_manage_open_incubation(state, payload, obs_iso)
        elif action == "share_incubation_clue":
            _handle_share_incubation_clue(state, payload, obs_iso)
        elif action == "open_bounded_inquiry":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can open bounded inquiries")
            _handle_open_bounded_inquiry(state, payload, obs_iso)
        elif action == "record_inquiry_evidence":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can record inquiry evidence")
            _handle_record_inquiry_evidence(state, payload, obs_iso)
        elif action == "share_inquiry_clue":
            _handle_share_inquiry_clue(state, payload, obs_iso)
        elif action == "conclude_or_stop_inquiry":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can conclude or stop inquiries")
            _handle_conclude_or_stop_inquiry(state, payload, obs_iso, day_root=day_root)
        elif action == "place_memory_cue":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can place memory cues")
            _handle_place_memory_cue(state, payload, obs_iso)
        elif action == "update_placed_cue_visibility":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can update placed cue visibility")
            _handle_update_placed_cue_visibility(state, payload, obs_iso)
        elif action == "curate_info_source":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can curate info sources")
            _handle_curate_info_source(state, payload, obs_iso)
        elif action == "create_future_capsule":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can create future capsules")
            _handle_create_future_capsule(state, payload, obs_iso)
        elif action == "resolve_future_capsule":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can resolve future capsules")
            _handle_resolve_future_capsule(state, payload, obs_iso)
        elif action == "set_optional_next_gesture":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can set optional next gestures")
            _handle_set_optional_next_gesture(state, payload, obs_iso)
        elif action == "peek_optional_next_gesture":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can peek optional next gestures")
            _handle_peek_optional_next_gesture(state, payload, obs_iso)
        elif action == "act_or_archive_gesture":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can act on or archive gestures")
            _handle_act_or_archive_gesture(state, payload, obs_iso)
        elif action == "settle_legacy_tool":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can settle legacy tools")
            _handle_settle_legacy_tool(state, payload, obs_iso)
        elif action == "register_gentle_responsibility":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can register gentle responsibilities")
            _handle_register_gentle_responsibility(state, payload, obs_iso)
        elif action == "review_gentle_responsibility":
            if actor_side != "subject":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can review gentle responsibilities")
            _handle_review_gentle_responsibility(state, payload, obs_iso)
        elif action == "resolve_commitment_value_conflict":
            if actor_side != "subject":
                raise V02PermissionError(
                    "FORBIDDEN_ACTOR_SIDE",
                    "Only subject can resolve commitment value conflicts",
                )
            _handle_resolve_commitment_value_conflict(state, payload, obs_iso)
        elif action == "propose_commitment_tradeoff":
            _handle_propose_commitment_tradeoff(state, payload, obs_iso, actor_side=actor_side)

        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = obs_iso
        if op_key:
            ledger[op_key] = {
                "fingerprint": fp,
                "action": action,
                "actor_side": actor_side,
                "applied_at": obs_iso,
                "revision": state["revision"],
            }
            if len(ledger) > 256:
                oldest_keys = list(ledger.keys())[: len(ledger) - 256]
                for old_k in oldest_keys:
                    ledger.pop(old_k, None)

        _atomic_write_state(day_root, state)
        projected = project_v02_agency_depth(
            day_root,
            has_subject_capability=has_cap,
            now_iso=obs_iso,
        )
        return projected, "APPLIED"


def summarize_agency_depth_state_for_context(
    day_root: Path | str,
    *,
    include_private: bool = True,
    now_iso: Optional[str] = None,
    now: Optional[str] = None,
) -> dict[str, Any]:
    """100% 零磁盘写入的生活背景摘要（``include_private=False`` 时严禁含未共享或后悔内容）。"""
    eff_now = now_iso if now_iso is not None else now
    projected = project_v02_agency_depth(
        day_root,
        has_subject_capability=bool(include_private),
        now_iso=eff_now,
    )

    supported_prefs: list[dict[str, Any]] = []
    saturated_domains: list[str] = []
    satiation_multipliers: dict[str, float] = {}

    for pref in projected["preferences"]:
        dom = str(pref.get("domain") or "")
        title = str(pref.get("title") or "")
        key = f"{dom}:{title}" if dom else title
        mult = float(pref.get("candidate_weight_multiplier") or 1.0)
        satiation_multipliers[key] = mult
        if dom and dom not in satiation_multipliers:
            satiation_multipliers[dom] = mult
        else:
            if dom:
                satiation_multipliers[dom] = min(satiation_multipliers[dom], mult)

        if bool(pref.get("saturated", False)) and dom and dom not in saturated_domains:
            saturated_domains.append(dom)

        if pref.get("status") == "SUPPORTED":
            supported_prefs.append({
                "pref_id": pref.get("pref_id"),
                "domain": dom,
                "title": title,
                "saturated": bool(pref.get("saturated", False)),
                "satiation_state": pref.get("satiation_state"),
                "candidate_weight_multiplier": mult,
                "shared": bool(pref.get("shared", True)),
            })

    active_rules: list[dict[str, Any]] = []
    for rl in projected["second_order_rules"]:
        if rl.get("status") in ("released",):
            continue
        active_rules.append({
            "rule_id": rl.get("rule_id"),
            "title": rl.get("title"),
            "trigger_window": rl.get("trigger_window"),
            "status": rl.get("status"),
            "kind": rl.get("kind"),
            "auto_locked": False,
            "shared": bool(rl.get("shared", False)),
        })

    ready_incubations: list[dict[str, Any]] = []
    for inc in projected["open_incubations"]:
        if inc.get("status") == "ready_to_revisit":
            ready_incubations.append({
                "question_id": inc.get("question_id"),
                "topic": inc.get("topic"),
                "latest_clue": inc.get("latest_clue") or "",
                "shared": bool(inc.get("shared", True)),
            })

    parked_count = sum(
        1 for cf in projected["parked_conflicts"] if cf.get("status") == "parked"
    )

    active_inquiries: list[dict[str, Any]] = []
    for inquiry in projected.get("inquiries") or []:
        if str(inquiry.get("status") or "") not in INQUIRY_ACTIVE_STATUSES:
            continue
        active_inquiries.append({
            "inquiry_id": inquiry.get("inquiry_id"),
            "question": inquiry.get("question"),
            "topic_tag": inquiry.get("topic_tag") or "",
            "status": inquiry.get("status"),
            "evidence_count": len(inquiry.get("evidence_notes") or []),
            "shared": bool(inquiry.get("shared", False)),
        })

    active_cues: list[dict[str, Any]] = []
    for cue in (projected.get("placed_memory_cues") or {}).get("active_cues") or []:
        active_cues.append({
            "cue_id": cue.get("cue_id"),
            "spot_id": cue.get("spot_id"),
            "artifact_ref": cue.get("artifact_ref"),
            "cue_text_cn": str(cue.get("cue_text_cn") or "")[:120],
            "shared": bool(cue.get("shared", False)),
        })
        if len(active_cues) >= 8:
            break

    parts: list[str] = []
    if supported_prefs:
        names = "、".join(f"{p['title']}" for p in supported_prefs[:3])
        parts.append(f"稳定偏好：{names}")
    if saturated_domains:
        parts.append(f"短期看腻歇息领域：{'、'.join(saturated_domains[:3])}")
    if active_rules:
        parts.append(f"二阶小规矩 {len(active_rules)} 条")
    if ready_incubations:
        parts.append(f"有新线索待回看问题 {len(ready_incubations)} 个")
    if parked_count > 0:
        parts.append(f"暂时搁置拉扯 {parked_count} 处")
    if active_cues:
        parts.append(f"可见记忆线索 {len(active_cues)} 处")

    summary_line_cn = "；".join(parts)[:220] if parts else "当前暂无已落盘的品味饱和或慢孵化动态"

    return {
        "schema": VIEW_SCHEMA,
        "state_exists": projected["state_exists"],
        "supported_preferences": supported_prefs,
        "saturated_domains": saturated_domains,
        "satiation_multipliers": satiation_multipliers,
        "active_second_order_rules": active_rules,
        "ready_incubations": ready_incubations,
        "active_inquiries": active_inquiries,
        "active_cues": active_cues,
        "parked_conflict_count": parked_count,
        "summary_line_cn": summary_line_cn,
        "llm_calls": 0,
    }


def record_post_tick_execution_feedback(
    day_root: Path | str,
    *,
    tick_record: Mapping[str, Any],
    practice_service: Any = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """在 runner_tick 之后自动累加当轮真实动作的重复饱和度，并在执行受阻/复盘失误时联动 PersonalPracticeService 写入 TENTATIVE 方法草稿。

    红线保证：
    - 若当轮为空闲（IDLE_NO_CANDIDATE / NOT_DUE / PAUSED / HELD_BY_BUDGET）且无真实动作与失败复盘，保持 100% 零磁盘写入；
    - 零外部大模型调用（llm_calls == 0），操作基于确定性 operation_key 幂等防重。
    """
    if not isinstance(tick_record, Mapping):
        return {
            "ok": True,
            "satiation_recorded": False,
            "regret_recorded": False,
            "tentative_method_created": False,
            "llm_calls": 0,
        }

    obs_iso = _now_iso_str(now_iso or (str(tick_record.get("logical_at") or tick_record.get("at") or "") or None))
    due = tick_record.get("due") if isinstance(tick_record.get("due"), Mapping) else tick_record
    extra = due.get("extra") if isinstance(due.get("extra"), Mapping) else {}

    status = str(due.get("status") or tick_record.get("status") or "").strip().upper()
    tick_token = str(
        tick_record.get("tick")
        or due.get("round_id")
        or due.get("decision_id")
        or extra.get("version_id")
        or extra.get("action_id")
        or ""
    ).strip()

    real_acted = bool(
        status in {"ACTED", "APPLIED", "COMPLETED", "STEP_COMMITTED"}
        or due.get("acted") is True
        or due.get("action_committed") is True
        or extra.get("owner_version_created") is True
        or extra.get("world_step_committed") is True
        or extra.get("effect_state") == "APPLIED"
    )

    failure_review = (
        due.get("failure_review")
        if isinstance(due.get("failure_review"), Mapping)
        else (extra.get("failure_review") if isinstance(extra.get("failure_review"), Mapping) else None)
    )

    # v0.8 C2b: a soft landing is not a mistake.  When the daily-loop bridge honestly refuses
    # an act it cannot perform (unsupported capability, an object no longer at its believed
    # spot, an unmapped clue, an active cooldown) nothing was executed and nothing failed:
    # recording ``process_mistake`` would mint a regret reflection about an act that never
    # happened.  The reason may arrive on the tick record, on its ``extra`` (the runner copies
    # ``reason_codes`` there) or inside the bridge's own ``v02_v03_daily`` report.
    _soft_reasons = {
        "UNSUPPORTED_CAPABILITY",
        "OBJECT_NOT_AT_EXPECTED_SPOT",
        "UNMAPPED_BRIDGE_CLUE",
        "COOLDOWN_ACTIVE",
    }
    _soft_sources: list[Mapping[str, Any]] = [due, extra]
    for _daily in (due.get("v02_v03_daily"), extra.get("v02_v03_daily")):
        if isinstance(_daily, Mapping):
            _soft_sources.append(_daily)
    soft_landing_reason = ""
    for _source in _soft_sources:
        _code = str(_source.get("reason_code") or _source.get("soft_land_reason_code") or "").strip()
        if _code in _soft_reasons:
            soft_landing_reason = _code
            break
        if _source.get("soft_landed") is True:
            soft_landing_reason = _code or "SOFT_LANDED"
            break
    if not soft_landing_reason:
        for _code in (extra.get("reason_codes") or due.get("reason_codes") or ()):
            if str(_code).strip() in _soft_reasons:
                soft_landing_reason = str(_code).strip()
                break

    # A genuine failure keeps its old meaning even when a soft-landing field happens to be
    # present: a raised/refused execution, a failure review or an explicit process_mistake.
    hard_failure = bool(
        failure_review is not None
        or str(due.get("regret_kind") or extra.get("regret_kind") or "").strip() == "process_mistake"
        or extra.get("action_failed") is True
        or due.get("action_failed") is True
        or status in {"ACTION_FAILED", "STEP_FAILED", "EXECUTION_FAILED", "PROCESS_MISTAKE"}
    )
    is_failure_or_mistake = bool(
        hard_failure
        or (not soft_landing_reason and (
            extra.get("step_refused") is True
            or due.get("step_refused") is True
            or status in {"STEP_REFUSED"}
        ))
    )

    if not real_acted and not is_failure_or_mistake:
        return {
            "ok": True,
            "satiation_recorded": False,
            "regret_recorded": False,
            "tentative_method_created": False,
            "soft_landed": bool(soft_landing_reason),
            "soft_landing_reason": soft_landing_reason or None,
            "llm_calls": 0,
        }

    out: dict[str, Any] = {
        "ok": True,
        "satiation_recorded": False,
        "satiation_status": None,
        "satiation_domain": None,
        "satiation_title": None,
        "regret_recorded": False,
        "tentative_method_created": False,
        "tentative_method_artifact_id": None,
        "tentative_method_state": None,
        "soft_landed": bool(soft_landing_reason),
        "soft_landing_reason": soft_landing_reason or None,
        "llm_calls": 0,
    }

    if real_acted:
        raw_domain = (
            extra.get("domain")
            or due.get("domain")
            or extra.get("action_family")
            or due.get("action_family")
            or extra.get("activity_kind")
            or due.get("activity_kind")
            or extra.get("action_kind")
            or due.get("action_kind")
            or ("creative_work" if extra.get("artifact_id") else "")
            or ("world_outing" if (extra.get("place_id") or due.get("place_id")) else "")
            or "autonomous_activity"
        )
        domain = str(raw_domain).strip()[:80] or "autonomous_activity"
        raw_title = (
            extra.get("activity_title")
            or due.get("activity_title")
            or extra.get("title")
            or due.get("title")
            or extra.get("artifact_id")
            or extra.get("place_id")
            or due.get("place_id")
            or extra.get("target_ref")
            or due.get("target_ref")
            or due.get("selected_candidate_ref")
            or domain
        )
        title = str(raw_title).strip()[:160] or domain
        raw_int = extra.get("satiation_intensity", due.get("satiation_intensity"))
        intensity = 0.30
        if _is_strict_number(raw_int):
            intensity = max(0.05, min(1.0, float(raw_int)))

        sat_seed = f"{tick_token or obs_iso}|{domain}|{title}"
        sat_op_key = f"post_tick_sat_{hashlib.sha256(sat_seed.encode('utf-8')).hexdigest()[:20]}"
        proj_view, mut_status = apply_agency_depth_mutation(
            day_root,
            {
                "action": "record_exposure_satiation",
                "domain": domain,
                "title": title,
                "intensity": intensity,
                "operation_key": sat_op_key,
            },
            actor_side="subject",
            now_iso=obs_iso,
        )
        out["satiation_recorded"] = True
        out["satiation_status"] = mut_status
        out["satiation_domain"] = domain
        out["satiation_title"] = title
        for pref in proj_view.get("preferences") or ():
            if pref.get("domain") == domain and str(pref.get("title") or "").lower() == title.lower():
                out["satiation_score"] = pref.get("satiation_score")
                out["saturated"] = pref.get("saturated")
                out["candidate_weight_multiplier"] = pref.get("candidate_weight_multiplier")
                break

    if is_failure_or_mistake:
        rev_map = failure_review or {}
        summary = str(
            rev_map.get("summary")
            or extra.get("failure_summary")
            or due.get("failure_summary")
            or extra.get("reason")
            or due.get("reason")
            or f"执行环节受阻复盘：{status or 'step_failed'}"
        ).strip()[:280]
        adjustment_cn = str(
            rev_map.get("method_adjustment_cn")
            or rev_map.get("instruction")
            or extra.get("method_adjustment_cn")
            or due.get("method_adjustment_cn")
            or f"针对「{summary[:60]}」先核对前置条件，再按可回退小步试做"
        ).strip()[:220]
        scope_cn = str(
            rev_map.get("scope")
            or extra.get("domain")
            or due.get("domain")
            or extra.get("action_kind")
            or due.get("action_kind")
            or "日常自主执行与前置条件核对"
        ).strip()[:160]
        uncertainty_cn = str(
            rev_map.get("uncertainty")
            or "尚在初次复盘草拟阶段，还需后续真实场景试跑核验是否顺手"
        ).strip()[:160]
        method_title = str(
            rev_map.get("method_title")
            or extra.get("method_title")
            or f"复盘改进草稿：{adjustment_cn[:32]}"
        ).strip()[:80]

        reg_seed = f"{tick_token or obs_iso}|{summary}|{adjustment_cn}"
        reg_hash = hashlib.sha256(reg_seed.encode("utf-8")).hexdigest()[:20]
        apply_agency_depth_mutation(
            day_root,
            {
                "action": "record_regret_reflection",
                "regret_kind": "process_mistake",
                "summary": summary,
                "method_adjustment_cn": adjustment_cn,
                "shared": False,
                "operation_key": f"post_tick_reg_{reg_hash}",
            },
            actor_side="subject",
            now_iso=obs_iso,
        )
        out["regret_recorded"] = True

        create_fn = getattr(practice_service, "create_method", None) if practice_service is not None else None
        if callable(create_fn):
            try:
                method_ans = create_fn(
                    title=method_title,
                    instruction=adjustment_cn,
                    scope=scope_cn,
                    uncertainty=uncertainty_cn,
                    experience_ids=list(rev_map.get("experience_ids") or ()),
                    review_refs=list(rev_map.get("review_refs") or ()),
                    operation_key=f"post_tick_mth_{reg_hash}",
                )
                if isinstance(method_ans, Mapping) and method_ans.get("ok") is True:
                    mdata = method_ans.get("data") if isinstance(method_ans.get("data"), Mapping) else {}
                    proj = mdata.get("project") if isinstance(mdata.get("project"), Mapping) else {}
                    art_id = str(proj.get("artifact_id") or mdata.get("artifact_id") or "").strip() or None
                    mstate = str(mdata.get("method_state") or "TENTATIVE").strip()
                    out["tentative_method_created"] = True
                    out["tentative_method_artifact_id"] = art_id
                    out["tentative_method_state"] = mstate
                else:
                    out["practice_service_reason"] = (
                        method_ans.get("reason_code") if isinstance(method_ans, Mapping) else "REJECTED"
                    )
            except Exception as exc:
                out["practice_service_error"] = f"{type(exc).__name__}: {exc}"[:160]

    return out


__all__ = [
    "ACTIVE_SECOND_ORDER_RULE_STATUSES",
    "CHOICE_MODES",
    "COMMITMENT_TRADEOFF_DECISIONS",
    "CONFLICT_OPS",
    "DEFAULT_INQUIRY_STEP_BUDGET",
    "FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS",
    "FUTURE_CAPSULE_DECISIONS",
    "INCUBATION_OPS",
    "INFO_CURATION_MODES",
    "INQUIRY_ACTIVE_STATUSES",
    "INQUIRY_STANCES",
    "INQUIRY_STATUSES",
    "INQUIRY_STATUS_CONCLUDED",
    "INQUIRY_STATUS_INVESTIGATING",
    "INQUIRY_STATUS_OPEN",
    "INQUIRY_STATUS_STOPPED",
    "INQUIRY_STOP_REASON_BUDGET_EXHAUSTED",
    "INQUIRY_TERMINAL_STATUSES",
    "LEGACY_TOOL_ENDINGS",
    "MAX_CANDIDATE_EXPLANATIONS",
    "MAX_EVIDENCE_NOTES_PER_INQUIRY",
    "MAX_INQUIRY_STEP_BUDGET",
    "MAX_OPEN_INQUIRIES",
    "MAX_OPTIONAL_GESTURE_LEN",
    "MAX_PARTNER_CLUES_PER_INQUIRY",
    "MAX_RULE_ENFORCEMENTS",
    "MIN_INQUIRY_STEP_BUDGET",
    "OPTIONAL_GESTURE_DECISIONS",
    "PREFERENCE_STATUSES",
    "REDISCOVERED_WEIGHT_MULTIPLIER",
    "REGRET_KINDS",
    "RESPONSIBILITY_REVIEW_DECISIONS",
    "RESPONSIBILITY_TARGET_KINDS",
    "SATIATION_DECAY_LAMBDA",
    "SATIATION_HALF_LIFE_HOURS",
    "SATIATION_THRESHOLD",
    "SATURATED_WEIGHT_MULTIPLIER",
    "SECOND_ORDER_RULE_OPS",
    "STATE_REL_PATH",
    "V02AgencyDepthError",
    "V02ConflictError",
    "V02NotFoundError",
    "V02PermissionError",
    "V02ValidationError",
    "VIEW_SCHEMA",
    "apply_agency_depth_mutation",
    "conclude_or_stop_inquiry",
    "evaluate_second_order_constraints",
    "open_bounded_inquiry",
    "project_v02_agency_depth",
    "record_inquiry_evidence",
    "record_post_tick_execution_feedback",
    "record_second_order_enforcement",
    "summarize_agency_depth_state_for_context",
]

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
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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

SATIATION_THRESHOLD = 0.70
SATIATION_HALF_LIFE_HOURS = 16.0
SATIATION_DECAY_LAMBDA = math.log(2.0) / SATIATION_HALF_LIFE_HOURS
SATURATED_WEIGHT_MULTIPLIER = 0.25
REDISCOVERED_WEIGHT_MULTIPLIER = 1.1

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


def _state_file_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*STATE_REL_PATH)


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
        "operation_ledger": {},
    }


def _load_state_readonly(day_root: Path | str) -> tuple[bool, dict[str, Any]]:
    """100% 零磁盘写入读取落盘状态（零建目录、零临时文件、零 mtime 触碰）。"""
    path = _state_file_path(day_root)
    if not path.exists():
        return False, _empty_raw_state()
    if not path.is_file():
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


def project_v02_agency_depth(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% 零磁盘写入只读投影；伴侣视角严格剥离 ``shared=False`` 项与全部后悔反思。"""
    obs_iso = _now_iso_str(now_iso)
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


def apply_agency_depth_mutation(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """执行受控状态变更并原子落盘，支持 ``operation_key`` 幂等重放与冲突拦截。"""
    if actor_side not in ("partner", "subject"):
        raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {actor_side!r}")

    obs_iso = _now_iso_str(now_iso)
    _exists, state = _load_state_readonly(day_root)
    has_cap = actor_side == "subject"

    expected_rev = payload.get("expected_revision")
    if expected_rev is not None:
        if not _is_strict_int(expected_rev) or expected_rev < 0:
            raise V02ValidationError("INVALID_FIELDS", "expected_revision must be a non-negative integer")
        if int(state.get("revision") or 0) != expected_rev:
            raise V02ConflictError("REVISION_MISMATCH", "State revision does not match expected_revision")

    op_key = _optional_clean_str(payload.get("operation_key"), "operation_key", max_len=128)
    fp = _compute_payload_fingerprint(actor_side, payload)
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

    action = _require_clean_str(payload.get("action"), "action", max_len=64)

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
    else:
        raise V02ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")

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
) -> dict[str, Any]:
    """100% 零磁盘写入的生活背景摘要（``include_private=False`` 时严禁含未共享或后悔内容）。"""
    projected = project_v02_agency_depth(
        day_root,
        has_subject_capability=bool(include_private),
        now_iso=now_iso,
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

    summary_line_cn = "；".join(parts)[:220] if parts else "当前暂无已落盘的品味饱和或慢孵化动态"

    return {
        "schema": VIEW_SCHEMA,
        "state_exists": projected["state_exists"],
        "supported_preferences": supported_prefs,
        "saturated_domains": saturated_domains,
        "satiation_multipliers": satiation_multipliers,
        "active_second_order_rules": active_rules,
        "ready_incubations": ready_incubations,
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
    is_failure_or_mistake = bool(
        failure_review is not None
        or str(due.get("regret_kind") or extra.get("regret_kind") or "").strip() == "process_mistake"
        or extra.get("step_refused") is True
        or due.get("step_refused") is True
        or extra.get("action_failed") is True
        or due.get("action_failed") is True
        or status in {"ACTION_FAILED", "STEP_FAILED", "STEP_REFUSED", "EXECUTION_FAILED", "PROCESS_MISTAKE"}
    )

    if not real_acted and not is_failure_or_mistake:
        return {
            "ok": True,
            "satiation_recorded": False,
            "regret_recorded": False,
            "tentative_method_created": False,
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
    "CHOICE_MODES",
    "CONFLICT_OPS",
    "FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS",
    "INCUBATION_OPS",
    "PREFERENCE_STATUSES",
    "REDISCOVERED_WEIGHT_MULTIPLIER",
    "REGRET_KINDS",
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
    "project_v02_agency_depth",
    "record_post_tick_execution_feedback",
    "summarize_agency_depth_state_for_context",
]

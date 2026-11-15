"""0.2 第 10 章：四级回忆清晰度投影（RecallProjection）与主动淡忘 / 自然衰减引擎。

核心契约（对齐总体设计第 10 章与第 15 章 §1）：
1. 存储不等于当下脱口而出的回忆：禁止把日常记忆装成逐字逐句全知摄像机。
2. 四档回忆清晰度闭集：
   - ``EXACT``：近期发生或高显著度/反复重温锚定，保留具体细节与准确时间锚点；
   - ``RANGE``：中距记忆，记得核心经过但时间软化为合理区间（``fuzzy_date_range`` 必包含真实发生日）；
   - ``GIST``：久远日常琐事或已淡化片段，只剩大概印象（``is_verbatim_quote=False``，绝不伪造逐字原话）；
   - ``UNAVAILABLE``：治理墓碑/用户删除屏障，或深度主动淡忘/彻底遗忘的久远细碎小事。
3. 自然衰减（``NATURAL_DECAY``）vs 主动淡忘（``ACTIVE_FADE``）：
   - 自然衰减由时间距离（``elapsed_days``）、情绪显著度（``emotional_salience``）、重温次数与信物锚点共同决定；
   - 主动淡忘表示主体有意把某段已翻篇/不愿反复咀嚼的小事放下：立即阻断主动提及（``allow_proactive_mention=False``），
     并加速剥离逐字细节（轻度放下退为翻篇梗概 ``GIST``，深度放下或主动扫描直接返回 ``UNAVAILABLE``）。
4. 翻记录确认 != 一直记得：当 ``consulted_archive=True`` 且带 ``archive_ref`` 时，显式标记
   ``reconstructed_from_archive=True`` 与 ``attribution_mode="CHECKED_RECORD_NOT_SPONTANEOUS_MEMORY"``。
5. GET / 投影 / 认知摘要 100% 零写入（不建目录、不写临时文件、不改 mtime）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping
import uuid

SCHEMA_VERSION = "nya02.recall_projection.v1"
RECALL_STATE_REL_PATH = "memory/v02_recall_projection_state.json"

CLARITY_EXACT = "EXACT"
CLARITY_RANGE = "RANGE"
CLARITY_GIST = "GIST"
CLARITY_UNAVAILABLE = "UNAVAILABLE"
CLARITY_LEVELS: tuple[str, ...] = (
    CLARITY_EXACT,
    CLARITY_RANGE,
    CLARITY_GIST,
    CLARITY_UNAVAILABLE,
)

DECAY_NATURAL = "NATURAL_DECAY"
DECAY_ACTIVE_FADE = "ACTIVE_FADE"
DECAY_GOVERNANCE_TOMBSTONE = "GOVERNANCE_TOMBSTONE"

FADE_STRENGTH_SOFT = "soft_let_go"
FADE_STRENGTH_DEEP = "deep_let_go"
VALID_FADE_STRENGTHS = frozenset({FADE_STRENGTH_SOFT, FADE_STRENGTH_DEEP})

QUERY_MODE_DIRECT = "direct_recall"
QUERY_MODE_PROACTIVE = "proactive_surface"
VALID_QUERY_MODES = frozenset({QUERY_MODE_DIRECT, QUERY_MODE_PROACTIVE})

ATTRIBUTION_SPONTANEOUS = "SPONTANEOUS_RECALL"
ATTRIBUTION_ARCHIVE_CHECKED = "CHECKED_RECORD_NOT_SPONTANEOUS_MEMORY"
ATTRIBUTION_BLOCKED = "UNAVAILABLE_OR_WITHHELD"

DEFAULT_BUSINESS_TZ = timezone(timedelta(hours=8))


def _now_iso(now_iso: str | None = None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        return now_iso.strip()
    return datetime.now(DEFAULT_BUSINESS_TZ).isoformat(timespec="seconds")


def _parse_dt(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=DEFAULT_BUSINESS_TZ)
    if not isinstance(raw, str):
        return None
    txt = raw.strip()
    if not txt:
        return None
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(txt)
    except ValueError:
        try:
            day_only = date.fromisoformat(txt[:10])
            return datetime(
                day_only.year,
                day_only.month,
                day_only.day,
                12,
                0,
                0,
                tzinfo=DEFAULT_BUSINESS_TZ,
            )
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=DEFAULT_BUSINESS_TZ)
    return parsed


def _clamp_float(value: Any, default: float = 0.35, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = default
    if num < lo:
        return lo
    if num > hi:
        return hi
    return round(num, 4)


def _clamp_int(value: Any, default: int = 0, lo: int = 0, hi: int = 10000) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        num = default
    return max(lo, min(hi, num))


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:10]}.tmp")
    body = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _state_path(day_root: Path | str) -> Path:
    return Path(day_root) / RECALL_STATE_REL_PATH


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "memories": {},
        "tombstones": {},
        "updated_at": None,
    }


def _load_state_readonly(day_root: Path | str) -> dict[str, Any]:
    path = _state_path(day_root)
    if not path.is_file():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _default_state()
    if not isinstance(raw, dict):
        return _default_state()
    state = _default_state()
    if isinstance(raw.get("memories"), dict):
        state["memories"] = dict(raw["memories"])
    if isinstance(raw.get("tombstones"), dict):
        state["tombstones"] = dict(raw["tombstones"])
    state["updated_at"] = raw.get("updated_at")
    return state


def _compute_fuzzy_date_range(occurred_dt: datetime, elapsed_days: float) -> dict[str, Any]:
    local_day = occurred_dt.astimezone(DEFAULT_BUSINESS_TZ).date()
    if elapsed_days <= 14.0:
        half_span = 2
    elif elapsed_days <= 60.0:
        half_span = 5
    elif elapsed_days <= 180.0:
        half_span = 12
    else:
        half_span = 21
    start_day = local_day - timedelta(days=half_span)
    end_day = local_day + timedelta(days=half_span)
    part_of_month = (
        "上旬" if local_day.day <= 10 else ("中旬" if local_day.day <= 20 else "下旬")
    )
    return {
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "span_days": half_span * 2,
        "period_label": f"{local_day.year}年{local_day.month}月{part_of_month}前后",
    }


def _derive_gist_text(entry: Mapping[str, Any], *, active_fade: bool) -> str:
    explicit_gist = str(entry.get("gist_summary") or entry.get("gist") or "").strip()
    topic = str(entry.get("topic") or entry.get("title") or "").strip()
    category = str(entry.get("category") or "日常片段").strip()
    if active_fade:
        if topic:
            return f"关于「{topic}」那件小事已经翻篇放下了，具体枝节没再留着。"
        return "那段已经翻篇放下的小插曲，细节没有再反复记着。"
    if explicit_gist:
        return explicit_gist
    if topic:
        return f"大概记得和「{topic}」有关的一段{category}印象，逐字原话和细枝末节已经模糊了。"
    raw_detail = str(entry.get("exact_detail") or entry.get("content") or "").strip()
    if raw_detail:
        head = raw_detail[:18].rstrip("，。！？,.!? ")
        return f"只剩一点粗略印象（大致和「{head}…」那会儿的事有关），具体原话记不太清了。"
    return f"只留下一段模糊的{category}印象，具体细节记不太清了。"


def _compute_natural_clarity(
    *,
    elapsed_days: float,
    salience: float,
    rehearsal_count: int,
    is_milestone: bool,
    has_keepsake_anchor: bool,
) -> str:
    # 重温与外部信物锚点适度延缓时间衰减，但不把远期记忆永久锁死为逐字摄像机
    effective_salience = min(
        1.0,
        salience
        + min(0.18, rehearsal_count * 0.04)
        + (0.20 if is_milestone else 0.0)
        + (0.12 if has_keepsake_anchor else 0.0),
    )
    if effective_salience >= 0.78 or is_milestone or has_keepsake_anchor:
        if elapsed_days <= 45.0:
            return CLARITY_EXACT
        if elapsed_days <= 365.0:
            return CLARITY_RANGE
        return CLARITY_GIST

    if effective_salience >= 0.45:
        if elapsed_days <= 14.0:
            return CLARITY_EXACT
        if elapsed_days <= 90.0:
            return CLARITY_RANGE
        return CLARITY_GIST

    # 日常琐事（低显著度）：几天后失去精确时间，数周后只剩大概印象，极久远微末琐事彻底淡出
    if elapsed_days <= 3.0:
        return CLARITY_EXACT
    if elapsed_days <= 21.0:
        return CLARITY_RANGE
    if elapsed_days <= 180.0:
        return CLARITY_GIST
    if salience < 0.20 and rehearsal_count == 0:
        return CLARITY_UNAVAILABLE
    return CLARITY_GIST


def project_memory_item(
    entry: Mapping[str, Any],
    *,
    now_iso: str | None = None,
    query_mode: str = QUERY_MODE_DIRECT,
    consulted_archive: bool = False,
    archive_ref: str | None = None,
) -> dict[str, Any]:
    """按时间距离、情绪显著度与主动淡忘/治理墓碑投影单条记忆（纯函数、零写入）。"""
    mode = query_mode if query_mode in VALID_QUERY_MODES else QUERY_MODE_DIRECT
    now_dt = _parse_dt(_now_iso(now_iso)) or datetime.now(DEFAULT_BUSINESS_TZ)
    memory_id = str(entry.get("memory_id") or entry.get("id") or "mem:unnamed").strip()

    # 1. 治理/删除墓碑硬屏障：无论何种查询或档案回查都绝不吐出任何旧内容
    if bool(
        entry.get("deleted")
        or entry.get("tombstoned")
        or entry.get("governance_blocked")
    ):
        return {
            "memory_id": memory_id,
            "clarity": CLARITY_UNAVAILABLE,
            "spontaneous_clarity": CLARITY_UNAVAILABLE,
            "decay_mode": DECAY_GOVERNANCE_TOMBSTONE,
            "query_mode": mode,
            "detail_available": False,
            "is_verbatim_quote": False,
            "allow_proactive_mention": False,
            "proactive_surfaced": False,
            "reconstructed_from_archive": False,
            "attribution_mode": ATTRIBUTION_BLOCKED,
            "temporal_precision": "unavailable",
            "fuzzy_date_range": None,
            "projected_text": None,
            "exact_detail": None,
            "gist_summary": None,
            "uncertainty_note": "该记录已删除或受隐私治理保护，不可召回。",
        }

    occurred_dt = _parse_dt(entry.get("occurred_at") or entry.get("created_at")) or now_dt
    elapsed_seconds = max(0.0, (now_dt - occurred_dt).total_seconds())
    elapsed_days = round(elapsed_seconds / 86400.0, 3)

    salience = _clamp_float(entry.get("emotional_salience"), default=0.35)
    rehearsal_count = _clamp_int(entry.get("rehearsal_count"), default=0)
    is_milestone = bool(entry.get("is_relationship_milestone", False))
    has_keepsake_anchor = bool(
        entry.get("has_external_keepsake_anchor", False) or entry.get("keepsake_ref")
    )
    active_fade = bool(entry.get("active_fade", False))
    fade_strength = str(entry.get("fade_strength") or FADE_STRENGTH_SOFT).strip()
    if fade_strength not in VALID_FADE_STRENGTHS:
        fade_strength = FADE_STRENGTH_SOFT

    natural_clarity = _compute_natural_clarity(
        elapsed_days=elapsed_days,
        salience=salience,
        rehearsal_count=rehearsal_count,
        is_milestone=is_milestone,
        has_keepsake_anchor=has_keepsake_anchor,
    )

    # 2. 主动淡忘 vs 自然衰减分流
    if active_fade:
        decay_mode = DECAY_ACTIVE_FADE
        allow_proactive = False
        if mode == QUERY_MODE_PROACTIVE or fade_strength == FADE_STRENGTH_DEEP:
            spontaneous_clarity = CLARITY_UNAVAILABLE
        else:
            # 轻度主动放下：直接问起时不撒谎装失忆，但已退化为翻篇梗概，不再保留逐字细节
            spontaneous_clarity = (
                CLARITY_UNAVAILABLE
                if natural_clarity == CLARITY_UNAVAILABLE
                else CLARITY_GIST
            )
    else:
        decay_mode = DECAY_NATURAL
        spontaneous_clarity = natural_clarity
        allow_proactive = spontaneous_clarity in (CLARITY_EXACT, CLARITY_RANGE) or (
            spontaneous_clarity == CLARITY_GIST and (is_milestone or salience >= 0.7)
        )

    # 3. 档案核对诚实归因（翻记录 != 一直记得）
    clean_archive_ref = str(archive_ref or entry.get("archive_ref") or "").strip()
    used_archive = bool(consulted_archive and clean_archive_ref and not active_fade)
    effective_clarity = CLARITY_EXACT if used_archive else spontaneous_clarity

    exact_detail = str(entry.get("exact_detail") or entry.get("content") or "").strip()
    range_detail = str(entry.get("range_summary") or "").strip()
    occurred_day_iso = occurred_dt.astimezone(DEFAULT_BUSINESS_TZ).date().isoformat()

    if mode == QUERY_MODE_PROACTIVE and not allow_proactive:
        effective_clarity = CLARITY_UNAVAILABLE

    if effective_clarity == CLARITY_EXACT:
        attribution = (
            ATTRIBUTION_ARCHIVE_CHECKED if used_archive else ATTRIBUTION_SPONTANEOUS
        )
        projected_text = exact_detail or _derive_gist_text(entry, active_fade=False)
        uncertainty_note = (
            f"刚才翻看记录（{clean_archive_ref}）确认了当时的细节，并非一直逐字背着。"
            if used_archive
            else None
        )
        return {
            "memory_id": memory_id,
            "clarity": CLARITY_EXACT,
            "spontaneous_clarity": spontaneous_clarity,
            "decay_mode": decay_mode,
            "query_mode": mode,
            "elapsed_days": elapsed_days,
            "emotional_salience": salience,
            "detail_available": True,
            "is_verbatim_quote": bool(entry.get("has_saved_quote", False) or used_archive),
            "allow_proactive_mention": allow_proactive,
            "proactive_surfaced": bool(mode == QUERY_MODE_PROACTIVE and allow_proactive),
            "reconstructed_from_archive": used_archive,
            "archive_ref": clean_archive_ref if used_archive else None,
            "attribution_mode": attribution,
            "temporal_precision": "exact",
            "exact_date": occurred_day_iso,
            "fuzzy_date_range": None,
            "projected_text": projected_text,
            "exact_detail": projected_text,
            "gist_summary": _derive_gist_text(entry, active_fade=False),
            "uncertainty_note": uncertainty_note,
        }

    if effective_clarity == CLARITY_RANGE:
        fuzzy_range = _compute_fuzzy_date_range(occurred_dt, elapsed_days)
        summary_text = (
            range_detail
            or exact_detail
            or _derive_gist_text(entry, active_fade=False)
        )
        return {
            "memory_id": memory_id,
            "clarity": CLARITY_RANGE,
            "spontaneous_clarity": spontaneous_clarity,
            "decay_mode": decay_mode,
            "query_mode": mode,
            "elapsed_days": elapsed_days,
            "emotional_salience": salience,
            "detail_available": True,
            "is_verbatim_quote": False,
            "allow_proactive_mention": allow_proactive,
            "proactive_surfaced": bool(mode == QUERY_MODE_PROACTIVE and allow_proactive),
            "reconstructed_from_archive": False,
            "archive_ref": None,
            "attribution_mode": ATTRIBUTION_SPONTANEOUS,
            "temporal_precision": "range",
            "exact_date": None,
            "fuzzy_date_range": fuzzy_range,
            "projected_text": f"大约在{fuzzy_range['period_label']}：{summary_text}",
            "exact_detail": None,
            "gist_summary": summary_text,
            "uncertainty_note": "具体哪一天和逐字原话有点模糊了，只记得大致那段时间。",
        }

    if effective_clarity == CLARITY_GIST:
        gist_text = _derive_gist_text(entry, active_fade=active_fade)
        return {
            "memory_id": memory_id,
            "clarity": CLARITY_GIST,
            "spontaneous_clarity": spontaneous_clarity,
            "decay_mode": decay_mode,
            "query_mode": mode,
            "elapsed_days": elapsed_days,
            "emotional_salience": salience,
            "detail_available": False,
            "is_verbatim_quote": False,
            "allow_proactive_mention": allow_proactive,
            "proactive_surfaced": bool(mode == QUERY_MODE_PROACTIVE and allow_proactive),
            "reconstructed_from_archive": False,
            "archive_ref": None,
            "attribution_mode": ATTRIBUTION_SPONTANEOUS,
            "temporal_precision": "coarse_impression",
            "exact_date": None,
            "fuzzy_date_range": None,
            "projected_text": gist_text,
            "exact_detail": None,
            "gist_summary": gist_text,
            "let_go_stance": "intentionally_let_go" if active_fade else None,
            "uncertainty_note": (
                "这件事当时已经主动翻篇放下了，没再揪着细节记。"
                if active_fade
                else "隔得有些久了，只剩个大概印象，不敢编造当时的原话。"
            ),
        }

    return {
        "memory_id": memory_id,
        "clarity": CLARITY_UNAVAILABLE,
        "spontaneous_clarity": spontaneous_clarity,
        "decay_mode": decay_mode,
        "query_mode": mode,
        "elapsed_days": elapsed_days,
        "emotional_salience": salience,
        "detail_available": False,
        "is_verbatim_quote": False,
        "allow_proactive_mention": False,
        "proactive_surfaced": False,
        "reconstructed_from_archive": False,
        "archive_ref": None,
        "attribution_mode": ATTRIBUTION_BLOCKED,
        "temporal_precision": "unavailable",
        "exact_date": None,
        "fuzzy_date_range": None,
        "projected_text": None,
        "exact_detail": None,
        "gist_summary": None,
        "let_go_stance": "intentionally_let_go" if active_fade else None,
        "uncertainty_note": (
            "那段小事早已主动放下，不再往回翻了。"
            if active_fade
            else "太久之前的细碎小事，现在实在想不起来了。"
        ),
    }


def record_episodic_trace(
    day_root: Path | str,
    *,
    memory_id: str | None = None,
    topic: str = "",
    exact_detail: str = "",
    gist_summary: str = "",
    category: str = "日常经历",
    occurred_at: str | None = None,
    emotional_salience: float = 0.35,
    is_relationship_milestone: bool = False,
    keepsake_ref: str | None = None,
    has_saved_quote: bool = False,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """显式写入或更新一条经历痕迹（仅在 POST/结算路径调用）。"""
    stamp = _now_iso(now_iso)
    mid = str(memory_id or f"mem_{uuid.uuid4().hex[:10]}").strip()
    if not mid:
        return {"ok": False, "status_code": 400, "error_code": "MISSING_MEMORY_ID"}

    state = _load_state_readonly(day_root)
    if mid in state["tombstones"]:
        return {
            "ok": False,
            "status_code": 409,
            "error_code": "MEMORY_TOMBSTONED",
            "memory_id": mid,
        }

    existing = dict(state["memories"].get(mid) or {})
    record = {
        "memory_id": mid,
        "topic": str(topic or existing.get("topic") or "").strip(),
        "exact_detail": str(exact_detail or existing.get("exact_detail") or "").strip(),
        "gist_summary": str(gist_summary or existing.get("gist_summary") or "").strip(),
        "category": str(category or existing.get("category") or "日常经历").strip(),
        "occurred_at": str(occurred_at or existing.get("occurred_at") or stamp).strip(),
        "emotional_salience": _clamp_float(
            emotional_salience,
            default=_clamp_float(existing.get("emotional_salience"), 0.35),
        ),
        "rehearsal_count": _clamp_int(existing.get("rehearsal_count"), 0),
        "is_relationship_milestone": bool(
            is_relationship_milestone or existing.get("is_relationship_milestone", False)
        ),
        "keepsake_ref": (
            str(keepsake_ref).strip()
            if keepsake_ref is not None
            else existing.get("keepsake_ref")
        ),
        "has_external_keepsake_anchor": bool(
            keepsake_ref or existing.get("keepsake_ref") or existing.get("has_external_keepsake_anchor")
        ),
        "has_saved_quote": bool(has_saved_quote or existing.get("has_saved_quote", False)),
        "active_fade": bool(existing.get("active_fade", False)),
        "fade_strength": str(existing.get("fade_strength") or FADE_STRENGTH_SOFT),
        "updated_at": stamp,
    }
    state["memories"][mid] = record
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {
        "ok": True,
        "status_code": 200,
        "memory": record,
        "projection": project_memory_item(record, now_iso=stamp),
    }


def mark_active_fade(
    day_root: Path | str,
    *,
    memory_id: str,
    fade_strength: str = FADE_STRENGTH_SOFT,
    reason: str = "settled_and_let_go",
    now_iso: str | None = None,
) -> dict[str, Any]:
    """主体主动放下/淡忘某段小事（不同于用户删除墓碑，也不同于纯时间遗忘）。"""
    mid = str(memory_id or "").strip()
    if not mid:
        return {"ok": False, "status_code": 400, "error_code": "MISSING_MEMORY_ID"}
    strength = (
        fade_strength if fade_strength in VALID_FADE_STRENGTHS else FADE_STRENGTH_SOFT
    )
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    if mid in state["tombstones"]:
        return {"ok": False, "status_code": 409, "error_code": "MEMORY_TOMBSTONED"}
    target = dict(state["memories"].get(mid) or {})
    if not target:
        return {"ok": False, "status_code": 404, "error_code": "MEMORY_NOT_FOUND"}

    target["active_fade"] = True
    target["fade_strength"] = strength
    target["active_fade_reason"] = str(reason or "settled_and_let_go").strip()
    target["active_faded_at"] = stamp
    target["updated_at"] = stamp
    state["memories"][mid] = target
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {
        "ok": True,
        "status_code": 200,
        "memory_id": mid,
        "fade_strength": strength,
        "direct_projection": project_memory_item(
            target, now_iso=stamp, query_mode=QUERY_MODE_DIRECT
        ),
        "proactive_projection": project_memory_item(
            target, now_iso=stamp, query_mode=QUERY_MODE_PROACTIVE
        ),
    }


def record_rehearsal(
    day_root: Path | str,
    *,
    memory_id: str,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """自然重温某段未淡忘记忆，增加重温计数以减缓自然衰减。"""
    mid = str(memory_id or "").strip()
    if not mid:
        return {"ok": False, "status_code": 400, "error_code": "MISSING_MEMORY_ID"}
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    target = dict(state["memories"].get(mid) or {})
    if not target:
        return {"ok": False, "status_code": 404, "error_code": "MEMORY_NOT_FOUND"}
    target["rehearsal_count"] = _clamp_int(target.get("rehearsal_count"), 0) + 1
    target["last_rehearsed_at"] = stamp
    target["updated_at"] = stamp
    state["memories"][mid] = target
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {
        "ok": True,
        "status_code": 200,
        "memory_id": mid,
        "rehearsal_count": target["rehearsal_count"],
        "projection": project_memory_item(target, now_iso=stamp),
    }


def tombstone_memory(
    day_root: Path | str,
    *,
    memory_id: str,
    reason: str = "user_delete_governance",
    now_iso: str | None = None,
) -> dict[str, Any]:
    """治理/用户删除屏障：抹除原明细并立下不可召回墓碑。"""
    mid = str(memory_id or "").strip()
    if not mid:
        return {"ok": False, "status_code": 400, "error_code": "MISSING_MEMORY_ID"}
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    state["memories"].pop(mid, None)
    state["tombstones"][mid] = {
        "memory_id": mid,
        "tombstoned": True,
        "reason": str(reason or "user_delete_governance").strip(),
        "tombstoned_at": stamp,
    }
    state["updated_at"] = stamp
    _atomic_write_json(_state_path(day_root), state)
    return {
        "ok": True,
        "status_code": 200,
        "memory_id": mid,
        "projection": project_memory_item(state["tombstones"][mid], now_iso=stamp),
    }


def project_recall_set(
    day_root: Path | str,
    *,
    now_iso: str | None = None,
    query_mode: str = QUERY_MODE_DIRECT,
    include_unavailable: bool = False,
) -> dict[str, Any]:
    """只读投影实例下全部登记记忆（100% 零写入）。"""
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    items: list[dict[str, Any]] = []
    counts = {level: 0 for level in CLARITY_LEVELS}

    for mid in sorted(state["memories"].keys()):
        entry = state["memories"][mid]
        if not isinstance(entry, Mapping):
            continue
        proj = project_memory_item(entry, now_iso=stamp, query_mode=query_mode)
        counts[proj["clarity"]] = counts.get(proj["clarity"], 0) + 1
        if proj["clarity"] == CLARITY_UNAVAILABLE and not include_unavailable:
            continue
        items.append(proj)

    for mid in sorted(state["tombstones"].keys()):
        tomb = state["tombstones"][mid]
        if not isinstance(tomb, Mapping):
            continue
        proj = project_memory_item(tomb, now_iso=stamp, query_mode=query_mode)
        counts[CLARITY_UNAVAILABLE] = counts.get(CLARITY_UNAVAILABLE, 0) + 1
        if include_unavailable:
            items.append(proj)

    return {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": stamp,
        "query_mode": query_mode,
        "clarity_counts": counts,
        "items": items,
    }


def read_v02_recall_projection_view(
    day_root: Path | str,
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """GET 读侧视图（保证零磁盘写入）。"""
    direct = project_recall_set(
        day_root,
        now_iso=now_iso,
        query_mode=QUERY_MODE_DIRECT,
        include_unavailable=True,
    )
    proactive = project_recall_set(
        day_root,
        now_iso=now_iso,
        query_mode=QUERY_MODE_PROACTIVE,
        include_unavailable=False,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": direct["evaluated_at"],
        "clarity_counts": direct["clarity_counts"],
        "direct_recall_items": direct["items"],
        "proactive_surfaceable_ids": [
            row["memory_id"] for row in proactive["items"]
        ],
    }


def summarize_recall_for_cognition(
    day_root: Path | str,
    *,
    now_iso: str | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    """供认知回路消费的有界只读摘要（零写入、自动过滤 UNAVAILABLE 与主动淡忘）。"""
    proactive = project_recall_set(
        day_root,
        now_iso=now_iso,
        query_mode=QUERY_MODE_PROACTIVE,
        include_unavailable=False,
    )
    bounded = proactive["items"][: max(1, int(limit))]
    return {
        "evaluated_at": proactive["evaluated_at"],
        "surfaceable_count": len(proactive["items"]),
        "clarity_counts": proactive["clarity_counts"],
        "surfaceable_memories": [
            {
                "memory_id": item["memory_id"],
                "clarity": item["clarity"],
                "temporal_precision": item["temporal_precision"],
                "projected_text": item["projected_text"],
                "is_verbatim_quote": item["is_verbatim_quote"],
            }
            for item in bounded
        ],
    }


def mutate_v02_recall_projection(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """统一 POST 写入口。"""
    action = str(payload.get("action") or "").strip()
    if action == "record_episodic_trace":
        return record_episodic_trace(
            day_root,
            memory_id=payload.get("memory_id"),
            topic=str(payload.get("topic") or ""),
            exact_detail=str(payload.get("exact_detail") or ""),
            gist_summary=str(payload.get("gist_summary") or ""),
            category=str(payload.get("category") or "日常经历"),
            occurred_at=payload.get("occurred_at"),
            emotional_salience=_clamp_float(payload.get("emotional_salience"), 0.35),
            is_relationship_milestone=bool(payload.get("is_relationship_milestone", False)),
            keepsake_ref=payload.get("keepsake_ref"),
            has_saved_quote=bool(payload.get("has_saved_quote", False)),
            now_iso=now_iso,
        )
    if action == "mark_active_fade":
        return mark_active_fade(
            day_root,
            memory_id=str(payload.get("memory_id") or ""),
            fade_strength=str(payload.get("fade_strength") or FADE_STRENGTH_SOFT),
            reason=str(payload.get("reason") or "settled_and_let_go"),
            now_iso=now_iso,
        )
    if action == "record_rehearsal":
        return record_rehearsal(
            day_root,
            memory_id=str(payload.get("memory_id") or ""),
            now_iso=now_iso,
        )
    if action == "tombstone_memory":
        return tombstone_memory(
            day_root,
            memory_id=str(payload.get("memory_id") or ""),
            reason=str(payload.get("reason") or "user_delete_governance"),
            now_iso=now_iso,
        )
    return {
        "ok": False,
        "status_code": 400,
        "error_code": "UNKNOWN_RECALL_ACTION",
        "action": action,
    }


__all__ = [
    "ATTRIBUTION_ARCHIVE_CHECKED",
    "ATTRIBUTION_BLOCKED",
    "ATTRIBUTION_SPONTANEOUS",
    "CLARITY_EXACT",
    "CLARITY_GIST",
    "CLARITY_LEVELS",
    "CLARITY_RANGE",
    "CLARITY_UNAVAILABLE",
    "DECAY_ACTIVE_FADE",
    "DECAY_GOVERNANCE_TOMBSTONE",
    "DECAY_NATURAL",
    "FADE_STRENGTH_DEEP",
    "FADE_STRENGTH_SOFT",
    "QUERY_MODE_DIRECT",
    "QUERY_MODE_PROACTIVE",
    "RECALL_STATE_REL_PATH",
    "SCHEMA_VERSION",
    "mark_active_fade",
    "mutate_v02_recall_projection",
    "project_memory_item",
    "project_recall_set",
    "read_v02_recall_projection_view",
    "record_episodic_trace",
    "record_rehearsal",
    "summarize_recall_for_cognition",
    "tombstone_memory",
]

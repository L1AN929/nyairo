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

import copy
import dataclasses
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Optional, Sequence
import uuid

try:
    from components.life.scripts import state_tx_guard as _tx
except ImportError:
    _LIFE_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
    if _LIFE_SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _LIFE_SCRIPTS_DIR)
    import state_tx_guard as _tx  # type: ignore[no-redef]

SCHEMA_VERSION = "nya02.recall_projection.v1"
RECALL_STATE_REL_PATH = "memory/v02_recall_projection_state.json"
VC_AUTHORITY_SCHEMA = "nyairo.v02.vc_authority_watermark.v1"
VC_AUTHORITY_WATERMARK_REL_PATH = ("governance", "v02_vc_authority_watermark.json")
_VC_DIGEST_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VC_SAFE_SCOPE_RE = re.compile(r"[^A-Za-z0-9_.:-]")

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

CH131_SPONTANEOUS_EXACT_CEILING_DAYS = 14.0
UNCERTAINTY_RANGE_IMPRESSION = "RANGE_IMPRESSION"
UNCERTAINTY_GIST_IMPRESSION = "GIST_IMPRESSION"

_RE_ISO_DATETIME = re.compile(
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}[T\s]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_RE_ISO_DATE = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b")
_RE_ZH_DATE = re.compile(r"(?:\d{2,4}年)?\d{1,2}月\d{1,2}[日号]")
_RE_CLOCK_TIME = re.compile(
    r"(?:(?<![\d.])\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:AM|PM|am|pm))?(?!\d)"
    r"|(?:凌晨|早上|上午|中午|下午|傍晚|晚上|夜里)?\d{1,2}[点时](?:\d{1,2}分|半|整|一刻|三刻)?)"
)
_RE_ZH_CLOCK_WORDS = re.compile(
    r"[零一二两三四五六七八九十]{1,3}点(?:[零一二两三四五六七八九十]{1,3}分|半|整|一刻|三刻)"
)
_RE_VERBATIM_QUOTES = re.compile(
    r'(?:"[^"\n]{1,400}"|“[^”\n]{1,400}”|「[^」\n]{1,400}」|『[^』\n]{1,400}』|«[^»\n]{1,400}»)'
)
_RE_SINGLE_VERBATIM_QUOTE = re.compile(r"(?<!\w)'[^'\n]{4,200}'(?!\w)")
_RE_MULTI_SEP = re.compile(r"[\s,，;；:：、]+")

# P12 自传体叙事（15 章 P12 / 0.2 设计债收口）：只读聚合既有实证磁盘事实，
# 不新建叙事 store、不编造任何未发生的经历。伴侣视角严格过滤未共享与私密细节。
P12_SCHEMA_VERSION = "nya02.p12_autobiographical_narrative.v1"
P12_AGENCY_DEPTH_REL_PATH = "state/v02_agency_depth_state.json"
P12_COMMON_GROUND_REL_PATH = "memory/v02_common_ground_state.json"
P12_WORKBENCH_REL_PATHS = ("phone/workbench_tray.json", "phone/workbench_curio.json")
P12_MAX_CHAPTERS = 36
P12_MAX_TURNING_POINTS = 16
P12_MAX_MOTIFS = 8
P12_STATUS_NO_STATE = "NO_STATE_FILES"
P12_STATUS_NO_EVENTS = "STATE_PRESENT_NO_ATTESTED_EVENTS"
P12_STATUS_ATTESTED = "ATTESTED"

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


VC_CONTROL_ROOT_ENV_VAR = "NYAIRO_V02_CONTROL_ROOT"


def resolve_v02_control_root(
    day_root: Path | str,
    control_root: Path | str | None = None,
) -> Path:
    if control_root is not None and str(control_root).strip():
        return Path(control_root)
    env_val = str(os.environ.get(VC_CONTROL_ROOT_ENV_VAR) or "").strip()
    if env_val:
        return Path(env_val)
    return Path(day_root)


def _vc_watermark_path(
    day_root: Path | str,
    control_root: Path | str | None = None,
) -> Path:
    return resolve_v02_control_root(day_root, control_root).joinpath(*VC_AUTHORITY_WATERMARK_REL_PATH)


def _is_safe_nonsymlink(root: Path, rel_parts: tuple[str, ...]) -> bool:
    cur = root
    try:
        if cur.exists() and cur.is_symlink():
            return False
        for seg in rel_parts:
            cur = cur / seg
            if cur.is_symlink():
                return False
    except OSError:
        return False
    return True


def compute_vc_item_digest(item_id: str) -> str:
    """Compute irreversible V-C SHA-256 digest for ``vc:{item_id}`` (zero plaintext stored)."""
    clean_id = str(item_id or "").strip()
    if not clean_id:
        raise ValueError("item_id must be a non-empty string")
    return hashlib.sha256(f"vc:{clean_id}".encode("utf-8")).hexdigest()


def _default_vc_watermark() -> dict[str, Any]:
    return {
        "schema": VC_AUTHORITY_SCHEMA,
        "authority_generation": 0,
        "restore_epoch": 0,
        "plaintext_stored": False,
        "updated_at": None,
        "sealed_digests": {},
    }


def _sanitize_vc_scope(scope: Any) -> str:
    cleaned = _VC_SAFE_SCOPE_RE.sub("_", str(scope or "memory").strip())[:40]
    return cleaned or "memory"


def _read_single_vc_watermark(wm_root: Path) -> tuple[bool, dict[str, Any]]:
    """Read one ``governance/v02_vc_authority_watermark.json`` with 100% zero disk writes."""
    base = _default_vc_watermark()
    if not _is_safe_nonsymlink(wm_root, VC_AUTHORITY_WATERMARK_REL_PATH):
        return False, base
    path = wm_root.joinpath(*VC_AUTHORITY_WATERMARK_REL_PATH)
    if not path.is_file():
        return False, base
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return True, base
    if not isinstance(raw, Mapping):
        return True, base

    raw_gen = raw.get("authority_generation")
    if isinstance(raw_gen, int) and not isinstance(raw_gen, bool) and raw_gen >= 0:
        base["authority_generation"] = int(raw_gen)
    raw_epoch = raw.get("restore_epoch")
    if isinstance(raw_epoch, int) and not isinstance(raw_epoch, bool) and raw_epoch >= 0:
        base["restore_epoch"] = int(raw_epoch)
    upd = raw.get("updated_at")
    base["updated_at"] = str(upd).strip() if isinstance(upd, str) and upd.strip() else None

    sealed_out: dict[str, dict[str, Any]] = {}
    raw_sealed = raw.get("sealed_digests")
    if isinstance(raw_sealed, Mapping):
        for raw_d, raw_meta in raw_sealed.items():
            d_str = str(raw_d or "").strip().lower()
            if not _VC_DIGEST_HEX_RE.fullmatch(d_str):
                continue
            meta = raw_meta if isinstance(raw_meta, Mapping) else {}
            rev_at = meta.get("revoked_at")
            rev_str = str(rev_at).strip() if isinstance(rev_at, str) and rev_at.strip() else None
            m_gen = meta.get("authority_generation")
            entry_gen = (
                int(m_gen)
                if isinstance(m_gen, int) and not isinstance(m_gen, bool) and m_gen >= 0
                else base["authority_generation"]
            )
            sealed_out[d_str] = {
                "digest": d_str,
                "revoked_at": rev_str,
                "scope": _sanitize_vc_scope(meta.get("scope")),
                "authority_generation": entry_gen,
            }
    base["sealed_digests"] = sealed_out
    return True, base


def _read_vc_watermark_readonly(
    day_root: Path | str,
    control_root: Path | str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Read merged V-C watermark from external authority root and legacy root (0 writes)."""
    root = Path(day_root)
    ext_root = resolve_v02_control_root(root, control_root)
    roots: list[Path] = [ext_root]
    try:
        if ext_root.resolve() != root.resolve():
            roots.append(root)
    except OSError:
        if ext_root != root:
            roots.append(root)

    if len(roots) == 1:
        return _read_single_vc_watermark(roots[0])

    merged = _default_vc_watermark()
    any_exists = False
    sealed_merged: dict[str, dict[str, Any]] = {}
    latest_upd: str | None = None
    for cand_root in reversed(roots):
        ex, doc = _read_single_vc_watermark(cand_root)
        if not ex:
            continue
        any_exists = True
        merged["authority_generation"] = max(
            int(merged["authority_generation"]),
            int(doc.get("authority_generation") or 0),
        )
        merged["restore_epoch"] = max(
            int(merged["restore_epoch"]),
            int(doc.get("restore_epoch") or 0),
        )
        cand_upd = doc.get("updated_at")
        if isinstance(cand_upd, str) and cand_upd and (latest_upd is None or cand_upd > latest_upd):
            latest_upd = cand_upd
        for k, v in (doc.get("sealed_digests") or {}).items():
            sealed_merged[k] = dict(v)
    merged["updated_at"] = latest_upd
    merged["sealed_digests"] = sealed_merged
    return any_exists, merged


def read_vc_authority_watermark_readonly(day_root: Path | str) -> dict[str, Any]:
    """Public zero-write reader returning normalized V-C watermark state."""
    _, doc = _read_vc_watermark_readonly(day_root)
    return doc


def _vc_sealed_digest_set(day_root: Path | str) -> frozenset[str]:
    _, doc = _read_vc_watermark_readonly(day_root)
    return frozenset((doc.get("sealed_digests") or {}).keys())


def record_vc_revocation_seal(
    day_root: Path | str,
    item_id: str,
    *,
    revoked_at: str | None = None,
    scope: str = "memory",
    control_root: Path | str | None = None,
) -> dict[str, Any]:
    """Record an irreversible SHA-256 revocation seal in external/fallback V-C watermark."""
    root = Path(day_root)
    target_root = resolve_v02_control_root(root, control_root)
    digest = compute_vc_item_digest(item_id)
    stamp = _now_iso(revoked_at)
    clean_scope = _sanitize_vc_scope(scope)
    if not _is_safe_nonsymlink(target_root, VC_AUTHORITY_WATERMARK_REL_PATH):
        raise ValueError("SYMLINK_TARGET_FORBIDDEN")

    wm_path = target_root.joinpath(*VC_AUTHORITY_WATERMARK_REL_PATH)
    with _tx.state_file_lock(wm_path):
        _, wm = _read_vc_watermark_readonly(root, target_root)
        cur_gen = int(wm.get("authority_generation") or 0)
        sealed: dict[str, dict[str, Any]] = dict(wm.get("sealed_digests") or {})
        is_new = digest not in sealed
        next_gen = (cur_gen + 1) if is_new else max(1, cur_gen)
        sealed[digest] = {
            "digest": digest,
            "revoked_at": stamp,
            "scope": clean_scope,
            "authority_generation": next_gen,
        }
        updated_doc: dict[str, Any] = {
            "schema": VC_AUTHORITY_SCHEMA,
            "authority_generation": next_gen,
            "restore_epoch": int(wm.get("restore_epoch") or 0),
            "plaintext_stored": False,
            "updated_at": stamp,
            "sealed_digests": sealed,
        }
        _tx.atomic_write_json(wm_path, updated_doc, sort_keys=True)

    return {
        "ok": True,
        "digest": digest,
        "revoked_at": stamp,
        "scope": clean_scope,
        "authority_generation": next_gen,
        "restore_epoch": int(updated_doc["restore_epoch"]),
        "sealed_digest_count": len(sealed),
    }


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
        if elapsed_days <= CH131_SPONTANEOUS_EXACT_CEILING_DAYS:
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
    clean_archive_ref = str(
        archive_ref
        or entry.get("archive_ref")
        or entry.get("keepsake_ref")
        or (f"archive:{memory_id}" if (consulted_archive and archive_ref is None) else "")
    ).strip()
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


def _load_unified_barrier_safe(day_root: Path | str) -> dict[str, Any]:
    try:
        try:
            from components.life.scripts import v02_representation_lease_forget as _rlf
        except ImportError:
            import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]
        return _rlf.load_unified_forget_barrier_readonly(day_root)
    except Exception:
        return {}


def _is_barrier_blocked_safe(entity: Any, barrier: Mapping[str, Any]) -> bool:
    if not barrier:
        return False
    try:
        try:
            from components.life.scripts import v02_representation_lease_forget as _rlf
        except ImportError:
            import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]
        return bool(_rlf.is_entity_forget_blocked(entity, barrier))
    except Exception:
        return False


def _enforce_episodic_lease_safe(
    day_root: Path | str,
    *,
    now_iso: str,
) -> dict[str, Any]:
    try:
        try:
            from components.life.scripts import v02_representation_lease_forget as _rlf
        except ImportError:
            import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]
        return _rlf.enforce_active_representation_lease(
            day_root,
            scope="episodic_memory",
            now_iso=now_iso,
        )
    except Exception:
        return {
            "allowed": True,
            "reason": "NO_RESTRICTIVE_LEASE",
            "scope": "episodic_memory",
            "lease_id": None,
        }


def _strip_verbatim_quotes_and_timestamps(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = _RE_VERBATIM_QUOTES.sub(" ", cleaned)
    cleaned = _RE_SINGLE_VERBATIM_QUOTE.sub(" ", cleaned)
    cleaned = _RE_ISO_DATETIME.sub(" ", cleaned)
    cleaned = _RE_ISO_DATE.sub(" ", cleaned)
    cleaned = _RE_ZH_DATE.sub(" ", cleaned)
    cleaned = _RE_CLOCK_TIME.sub(" ", cleaned)
    cleaned = _RE_ZH_CLOCK_WORDS.sub(" ", cleaned)
    # Remove any stray unmatched quote marks left over.
    for qch in ('"', "“", "”", "「", "」", "『", "』", "«", "»"):
        cleaned = cleaned.replace(qch, " ")
    cleaned = _RE_MULTI_SEP.sub(" ", cleaned).strip(" \t\r\n,，.。;；:：、-—~")
    return cleaned


def _fuzzify_memory_object_text(
    raw_text: str,
    *,
    clarity: str,
    ts_dt: datetime | None,
    elapsed_days: float,
) -> str:
    body = _strip_verbatim_quotes_and_timestamps(raw_text)
    if clarity == CLARITY_RANGE:
        period_label = (
            _compute_fuzzy_date_range(ts_dt, elapsed_days)["period_label"]
            if ts_dt is not None
            else "前些日子"
        )
        if body:
            snippet = body[:80].rstrip(" ,，.。;；:：、")
            return f"大概记得在{period_label}左右，{snippet}（大概印象，具体时刻与逐字原话已模糊）"
        return f"大概记得在{period_label}左右有过这段经历（具体时刻与逐字原话已模糊）"
    if body:
        snippet = body[:48].rstrip(" ,，.。;；:：、")
        return f"隐约记得很久之前有过与{snippet}相关的大致印象（具体日期与原话已记不清）"
    return "隐约记得很久之前提过这件事，具体日期与逐字原话都已经记不清了"


def _obj_field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _extract_memory_object_barrier_probe(obj: Any) -> tuple[dict[str, Any], list[str]]:
    """Build an entity probe mapping and flat ID list for unified forget barrier checks."""
    scalar_keys = (
        "memory_id",
        "id",
        "object_id",
        "event_id",
        "turn_id",
        "episode_id",
        "source_id",
        "artifact_id",
        "derivation_id",
        "claim_id",
        "trace_id",
        "item_id",
        "entry_id",
        "episode_key",
        "source_ref",
        "ref",
    )
    entity: dict[str, Any] = {}
    probes: list[str] = []

    def _add_probe(val: Any) -> None:
        if isinstance(val, str):
            s = val.strip()
            if s and s not in probes:
                probes.append(s)
            if "#" in s:
                for part in s.split("#"):
                    ps = part.strip()
                    if ps and ps not in probes:
                        probes.append(ps)
            if ":" in s:
                tail = s.split(":", 1)[1].strip()
                if tail and tail not in probes:
                    probes.append(tail)

    for key in scalar_keys:
        val = _obj_field(obj, key)
        if isinstance(val, str) and val.strip():
            entity[key] = val.strip()
            _add_probe(val)

    for bool_key in ("hold_active", "erased", "deleted", "tombstoned", "governance_blocked"):
        if _obj_field(obj, bool_key):
            entity[bool_key] = True
    for st_key in ("status", "lifecycle"):
        sv = _obj_field(obj, st_key)
        if isinstance(sv, str) and sv.strip():
            entity[st_key] = sv.strip()

    ns = _obj_field(obj, "namespace")
    if ns is not None:
        ns_turn = ns.get("turn") if isinstance(ns, Mapping) else getattr(ns, "turn", None)
        if isinstance(ns_turn, str) and ns_turn.strip():
            entity.setdefault("turn_id", ns_turn.strip())
            _add_probe(ns_turn)

    seq_refs: list[str] = []
    for seq_key in ("source_event_ids", "source_refs", "provenance_refs", "root_set", "source_ids", "episode_ids"):
        raw_seq = _obj_field(obj, seq_key)
        if isinstance(raw_seq, Sequence) and not isinstance(raw_seq, (str, bytes)):
            for elem in raw_seq:
                if isinstance(elem, str) and elem.strip():
                    s = elem.strip()
                    seq_refs.append(s)
                    _add_probe(s)
                elif isinstance(elem, Sequence) and not isinstance(elem, (str, bytes)):
                    for sub in elem:
                        if isinstance(sub, str) and sub.strip():
                            seq_refs.append(sub.strip())
                            _add_probe(sub)
                    if len(elem) >= 2 and all(isinstance(x, str) and x.strip() for x in elem[:2]):
                        joined = f"{elem[0].strip()}:{elem[1].strip()}"
                        seq_refs.append(joined)
                        _add_probe(joined)
                elif isinstance(elem, Mapping):
                    for sub_k in ("ref", "source_ref", "event_id", "turn_id", "id", "memory_id", "source_id"):
                        sv = elem.get(sub_k)
                        if isinstance(sv, str) and sv.strip():
                            seq_refs.append(sv.strip())
                            _add_probe(sv)

    if seq_refs:
        entity["source_refs"] = list(dict.fromkeys(seq_refs))
    return entity, probes


def _memory_object_hits_barrier(obj: Any, barrier: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(barrier, Mapping) or not barrier:
        return False
    if barrier.get("fail_closed"):
        return True
    entity, probes = _extract_memory_object_barrier_probe(obj)
    if entity.get("deleted") or entity.get("tombstoned") or entity.get("governance_blocked"):
        return True
    blocked_raw = barrier.get("blocked_ids")
    blocked_set: set[str] = (
        {str(x).strip() for x in blocked_raw if str(x).strip()}
        if isinstance(blocked_raw, (set, frozenset, list, tuple))
        else set()
    )
    for extra_key in ("native_blocked_events", "native_blocked_turns", "native_blocked_source_refs"):
        extra = barrier.get(extra_key)
        if isinstance(extra, (set, frozenset, list, tuple)):
            blocked_set.update(str(x).strip() for x in extra if str(x).strip())

    for probe in probes:
        if probe in blocked_set:
            return True
    if _is_barrier_blocked_safe(entity, barrier):
        return True
    for probe in probes:
        if _is_barrier_blocked_safe(probe, barrier):
            return True
    return False


def _clone_memory_object(
    obj: Any,
    *,
    text: Optional[str] = None,
    uncertainty: Optional[str] = None,
    clarity: Optional[str] = None,
    attribution_mode: Optional[str] = None,
    reconstructed_from_archive: Optional[bool] = None,
    is_verbatim_quote: Optional[bool] = None,
) -> Any:
    """Return a non-mutating copy of a MemoryObject, dataclass, Mapping, or simple object."""
    if isinstance(obj, Mapping):
        cloned = copy.deepcopy(dict(obj))
        if text is not None:
            if "text" in cloned or "content" not in cloned:
                cloned["text"] = text
            if "content" in cloned:
                cloned["content"] = text
        if uncertainty is not None:
            cloned["uncertainty"] = uncertainty
        if clarity is not None:
            cloned["clarity"] = clarity
        if attribution_mode is not None:
            cloned["attribution_mode"] = attribution_mode
        if reconstructed_from_archive is not None:
            cloned["reconstructed_from_archive"] = reconstructed_from_archive
        if is_verbatim_quote is not None:
            cloned["is_verbatim_quote"] = is_verbatim_quote
        return cloned

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        field_names = {f.name for f in dataclasses.fields(obj)}
        updates: dict[str, Any] = {}
        if text is not None and "text" in field_names:
            updates["text"] = text
        if uncertainty is not None and "uncertainty" in field_names:
            updates["uncertainty"] = uncertainty
        if clarity is not None and "clarity" in field_names:
            updates["clarity"] = clarity
        if attribution_mode is not None and "attribution_mode" in field_names:
            updates["attribution_mode"] = attribution_mode
        if reconstructed_from_archive is not None and "reconstructed_from_archive" in field_names:
            updates["reconstructed_from_archive"] = reconstructed_from_archive
        if is_verbatim_quote is not None and "is_verbatim_quote" in field_names:
            updates["is_verbatim_quote"] = is_verbatim_quote
        return dataclasses.replace(obj, **updates)

    cloned_obj = copy.copy(obj)
    for attr_name, attr_val in (
        ("text", text),
        ("uncertainty", uncertainty),
        ("clarity", clarity),
        ("attribution_mode", attribution_mode),
        ("reconstructed_from_archive", reconstructed_from_archive),
        ("is_verbatim_quote", is_verbatim_quote),
    ):
        if attr_val is not None:
            try:
                setattr(cloned_obj, attr_name, attr_val)
            except Exception:
                pass
    return cloned_obj


def apply_ch131_two_stage_clarity_to_memory_objects(
    objects: Sequence[Any],
    *,
    now_iso: Optional[str] = None,
    consulted_archive: bool = False,
    barrier: Optional[Mapping[str, Any]] = None,
) -> list[Any]:
    """Ch. 131 downstream two-stage fuzzy recall & forget-barrier filter (pure, zero-write, non-mutating).

    1. Enforces ``barrier`` unconditionally (even when ``consulted_archive=True``); returns ``[]``
       immediately when ``barrier.get("fail_closed")`` is truthy.
    2. When ``consulted_archive=True``, preserves exact memory text and annotates
       ``ATTRIBUTION_ARCHIVE_CHECKED``.
    3. When ``not consulted_archive`` and a memory's ``valid_from``/``occurred_at``/``created_at``
       is older than 14.0 days:
       - ``14.0 < age <= 365.0`` -> bounded fuzzy ``RANGE`` (``uncertainty="RANGE_IMPRESSION"``)
       - ``age > 365.0`` -> bounded fuzzy ``GIST`` (``uncertainty="GIST_IMPRESSION"``)
       stripping verbatim clock timestamps and verbatim quotes into natural fuzzy impressions
       without mutating any caller-owned objects in place.
    """
    if isinstance(barrier, Mapping) and barrier.get("fail_closed"):
        return []
    if not objects:
        return []

    now_dt = _parse_dt(_now_iso(now_iso)) or datetime.now(DEFAULT_BUSINESS_TZ)
    result: list[Any] = []
    for obj in objects:
        if obj is None:
            continue
        if _memory_object_hits_barrier(obj, barrier):
            continue

        raw_text = str(_obj_field(obj, "text") or _obj_field(obj, "content") or "")
        cur_uncertainty = str(_obj_field(obj, "uncertainty") or "NONE").strip()

        if consulted_archive:
            result.append(
                _clone_memory_object(
                    obj,
                    uncertainty=ATTRIBUTION_ARCHIVE_CHECKED,
                    clarity=CLARITY_EXACT,
                    attribution_mode=ATTRIBUTION_ARCHIVE_CHECKED,
                    reconstructed_from_archive=True,
                )
            )
            continue

        raw_ts = (
            _obj_field(obj, "valid_from")
            or _obj_field(obj, "occurred_at")
            or _obj_field(obj, "created_at")
            or _obj_field(obj, "timestamp")
        )
        ts_dt = _parse_dt(raw_ts)
        elapsed_days = (
            max(0.0, (now_dt - ts_dt).total_seconds() / 86400.0)
            if ts_dt is not None
            else 0.0
        )

        if elapsed_days <= CH131_SPONTANEOUS_EXACT_CEILING_DAYS:
            result.append(
                _clone_memory_object(
                    obj,
                    clarity=CLARITY_EXACT,
                    attribution_mode=ATTRIBUTION_SPONTANEOUS,
                    reconstructed_from_archive=False,
                )
            )
            continue

        target_clarity = CLARITY_RANGE if elapsed_days <= 365.0 else CLARITY_GIST
        target_uncertainty = (
            UNCERTAINTY_RANGE_IMPRESSION
            if target_clarity == CLARITY_RANGE
            else UNCERTAINTY_GIST_IMPRESSION
        )
        # Idempotency: if already fuzzified to the target impression tier, avoid double-wrapping.
        if (
            cur_uncertainty == target_uncertainty
            and (raw_text.startswith("大概记得") or raw_text.startswith("隐约记得"))
        ):
            fuzzy_text = raw_text
        else:
            fuzzy_text = _fuzzify_memory_object_text(
                raw_text,
                clarity=target_clarity,
                ts_dt=ts_dt,
                elapsed_days=elapsed_days,
            )

        result.append(
            _clone_memory_object(
                obj,
                text=fuzzy_text,
                uncertainty=target_uncertainty,
                clarity=target_clarity,
                attribution_mode=ATTRIBUTION_SPONTANEOUS,
                reconstructed_from_archive=False,
                is_verbatim_quote=False,
            )
        )
    return result


def _memory_visible_to_actor(entry: Mapping[str, Any], *, has_subject_capability: bool) -> bool:
    if has_subject_capability:
        return True
    vis = str(entry.get("visibility") or "").strip().lower()
    if bool(entry.get("private")) or bool(entry.get("active_fade")) or vis in ("private", "subject_only", "unshared"):
        return False
    if entry.get("shared") is True or entry.get("shared_with_partner") is True or vis in ("shared", "partner", "public"):
        return True
    return False


def _normalize_trace_source_refs(raw: Any) -> list[Any]:
    """Normalize source_refs into a bounded, deduplicated, JSON-serializable list."""
    if not raw:
        return []
    items: list[Any]
    if isinstance(raw, Mapping):
        items = list(raw.items())
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    elif isinstance(raw, str):
        items = [raw]
    else:
        return []
    out: list[Any] = []
    seen: set[str] = set()
    for item in items:
        normalized: Any = None
        if isinstance(item, str):
            txt = item.strip()[:160]
            if txt:
                normalized = txt
        elif isinstance(item, (tuple, list)):
            parts = [str(p).strip()[:160] for p in item if p is not None and str(p).strip()]
            if len(parts) >= 2:
                normalized = [parts[0][:48], parts[1][:140]]
            elif len(parts) == 1:
                normalized = parts[0]
        elif isinstance(item, Mapping):
            kind = str(item.get("kind") or item.get("type") or "").strip()[:48]
            ident = str(item.get("id") or item.get("ref") or item.get("value") or "").strip()[:140]
            if kind and ident:
                normalized = [kind, ident]
            else:
                cleaned_map = {
                    str(k).strip()[:48]: str(v).strip()[:160]
                    for k, v in item.items()
                    if str(k).strip() and v is not None and str(v).strip()
                }
                if cleaned_map:
                    normalized = cleaned_map
        if normalized is None:
            continue
        dedup_key = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        if dedup_key not in seen:
            seen.add(dedup_key)
            out.append(normalized)
        if len(out) >= 32:
            break
    return out


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
    shared: bool | None = None,
    visibility: str | None = None,
    episode_key: str | None = None,
    source_refs: Sequence[Any] | None = None,
    now_iso: str | None = None,
    has_subject_capability: bool = True,
) -> dict[str, Any]:
    """显式写入或更新一条经历痕迹（仅在 POST/结算路径调用）。"""
    stamp = _now_iso(now_iso)
    mid = str(memory_id or f"mem_{uuid.uuid4().hex[:10]}").strip()
    if not mid:
        return {"ok": False, "status_code": 400, "error_code": "MISSING_MEMORY_ID"}

    cap = bool(has_subject_capability)
    if not cap:
        if shared is False or (visibility is not None and str(visibility).strip().lower() != "shared"):
            return {"ok": False, "status_code": 403, "error_code": "FORBIDDEN_ACTOR_SIDE"}

    lease_gate = _enforce_episodic_lease_safe(day_root, now_iso=stamp)
    if not lease_gate.get("allowed", True):
        return {
            "ok": False,
            "status_code": 403,
            "error_code": "REPRESENTATION_LEASE_BLOCKED",
            "reason": str(lease_gate.get("reason") or "REPRESENTATION_LEASE_BLOCKED"),
            "reason_cn": str(lease_gate.get("reason_cn") or "当前情景记忆表征租约不可用，已阻止写入"),
            "scope": str(lease_gate.get("scope") or "episodic_memory"),
            "lease_id": lease_gate.get("lease_id"),
            "memory_id": mid,
        }

    sealed_digests = _vc_sealed_digest_set(day_root)
    with _tx.state_file_lock(_state_path(day_root)):
        state = _load_state_readonly(day_root)
        if mid in state["tombstones"] or compute_vc_item_digest(mid) in sealed_digests:
            return {
                "ok": False,
                "status_code": 409,
                "error_code": "MEMORY_TOMBSTONED",
                "memory_id": mid,
            }

        existing = dict(state["memories"].get(mid) or {})
        if not cap:
            if existing and not _memory_visible_to_actor(existing, has_subject_capability=False):
                return {"ok": False, "status_code": 403, "error_code": "FORBIDDEN_PRIVATE_MEMORY"}
            eff_vis = "shared"
            eff_shared = True
        else:
            eff_vis = (
                str(visibility).strip().lower()
                if visibility is not None
                else str(existing.get("visibility") or ("shared" if shared is True else "private")).strip().lower()
            )
            eff_shared = (
                bool(shared)
                if shared is not None
                else (True if eff_vis == "shared" else bool(existing.get("shared", False)))
            )
            if eff_shared and visibility is None:
                eff_vis = "shared"
        eff_episode_key = (
            str(episode_key).strip()[:160]
            if episode_key is not None and str(episode_key).strip()
            else (
                str(existing.get("episode_key")).strip()[:160]
                if isinstance(existing.get("episode_key"), str) and str(existing.get("episode_key")).strip()
                else None
            )
        )
        existing_refs = _normalize_trace_source_refs(existing.get("source_refs"))
        incoming_refs = _normalize_trace_source_refs(source_refs) if source_refs is not None else []
        eff_source_refs = _normalize_trace_source_refs(existing_refs + incoming_refs)
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
            "shared": eff_shared,
            "visibility": eff_vis,
            "active_fade": bool(existing.get("active_fade", False)),
            "fade_strength": str(existing.get("fade_strength") or FADE_STRENGTH_SOFT),
            "updated_at": stamp,
        }
        if eff_episode_key is not None:
            record["episode_key"] = eff_episode_key
        if eff_source_refs:
            record["source_refs"] = eff_source_refs
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
    sealed_digests = _vc_sealed_digest_set(day_root)
    with _tx.state_file_lock(_state_path(day_root)):
        state = _load_state_readonly(day_root)
        if mid in state["tombstones"] or compute_vc_item_digest(mid) in sealed_digests:
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
    sealed_digests = _vc_sealed_digest_set(day_root)
    with _tx.state_file_lock(_state_path(day_root)):
        state = _load_state_readonly(day_root)
        if mid in state["tombstones"] or compute_vc_item_digest(mid) in sealed_digests:
            return {"ok": False, "status_code": 409, "error_code": "MEMORY_TOMBSTONED"}
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
    """治理/用户删除屏障：抹除原明细并立下不可召回墓碑，同时更新 V-C 跨备份不可逆印章。"""
    mid = str(memory_id or "").strip()
    if not mid:
        return {"ok": False, "status_code": 400, "error_code": "MISSING_MEMORY_ID"}
    stamp = _now_iso(now_iso)
    seal = record_vc_revocation_seal(
        day_root,
        mid,
        revoked_at=stamp,
        scope="memory",
    )
    with _tx.state_file_lock(_state_path(day_root)):
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
        "vc_authority_seal": seal,
        "projection": project_memory_item(state["tombstones"][mid], now_iso=stamp),
    }


def project_recall_set(
    day_root: Path | str,
    *,
    now_iso: str | None = None,
    query_mode: str = QUERY_MODE_DIRECT,
    include_unavailable: bool = False,
    has_subject_capability: bool = True,
) -> dict[str, Any]:
    """只读投影实例下全部登记记忆（100% 零写入）。"""
    stamp = _now_iso(now_iso)
    state = _load_state_readonly(day_root)
    sealed_digests = _vc_sealed_digest_set(day_root)
    barrier = _load_unified_barrier_safe(day_root)
    items: list[dict[str, Any]] = []
    counts = {level: 0 for level in CLARITY_LEVELS}

    for mid in sorted(state["memories"].keys()):
        entry = state["memories"][mid]
        if not isinstance(entry, Mapping):
            continue
        if not has_subject_capability and not _memory_visible_to_actor(entry, has_subject_capability=False):
            continue
        if compute_vc_item_digest(mid) in sealed_digests or _is_barrier_blocked_safe({"memory_id": mid, **dict(entry)}, barrier):
            if mid not in state["tombstones"] and has_subject_capability:
                proj = project_memory_item(
                    {"memory_id": mid, "tombstoned": True},
                    now_iso=stamp,
                    query_mode=query_mode,
                )
                counts[CLARITY_UNAVAILABLE] = counts.get(CLARITY_UNAVAILABLE, 0) + 1
                if include_unavailable:
                    items.append(proj)
            continue
        proj = project_memory_item(entry, now_iso=stamp, query_mode=query_mode)
        counts[proj["clarity"]] = counts.get(proj["clarity"], 0) + 1
        if proj["clarity"] == CLARITY_UNAVAILABLE and not include_unavailable:
            continue
        items.append(proj)

    if has_subject_capability:
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


def _read_json_object_readonly(path: Path) -> dict[str, Any] | None:
    """Read one JSON object without creating directories, files or touching mtimes."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return dict(raw) if isinstance(raw, Mapping) else None


def _p12_date_key(occurred_at: Any, fallback: str) -> str:
    parsed = _parse_dt(occurred_at)
    if parsed is not None:
        return parsed.astimezone(DEFAULT_BUSINESS_TZ).date().isoformat()
    text = str(occurred_at or "").strip()
    return text[:10] if len(text) >= 7 else fallback[:10]


def _p12_collect_events(
    state_root: Path | str,
    *,
    has_subject_capability: bool,
    stamp: str,
) -> tuple[bool, list[dict[str, Any]]]:
    """Collect only attested events from real persisted owner files (pure read)."""
    root = Path(state_root)
    any_state = False
    events: list[dict[str, Any]] = []
    sealed_digests = _vc_sealed_digest_set(root)
    barrier = _load_unified_barrier_safe(root)

    # 1. RecallProjection 情景记忆（既有四档清晰度 owner）
    recall_state = _load_state_readonly(root)
    recall_path = _state_path(root)
    if has_subject_capability and recall_path.is_file():
        any_state = True
    tombstones_map = recall_state.get("tombstones") or {}
    for mid in sorted(recall_state.get("memories", {}).keys()):
        if mid in tombstones_map or compute_vc_item_digest(mid) in sealed_digests:
            continue
        entry = recall_state["memories"][mid]
        if not isinstance(entry, Mapping):
            continue
        if _is_barrier_blocked_safe({"memory_id": mid, **dict(entry)}, barrier):
            continue
        shared = _memory_visible_to_actor(entry, has_subject_capability=False)
        if not has_subject_capability and not shared:
            continue
        any_state = True
        occurred = entry.get("occurred_at") or entry.get("created_at") or stamp
        topic = str(entry.get("topic") or entry.get("title") or "").strip()
        gist = str(entry.get("gist_summary") or "").strip()
        events.append({
            "occurred_at": str(occurred),
            "date_key": _p12_date_key(occurred, stamp),
            "kind": "memory",
            "summary": (gist or topic or "一段日常记忆")[:120],
            "source_ref": f"recall:{mid}",
            "shared": bool(shared),
            "milestone": bool(entry.get("is_relationship_milestone", False)),
            "motif": topic,
        })

    # 2. CommonGround 共同认知账本（只取已共享 / 待澄清的公开条目）
    cg_doc = _read_json_object_readonly(root / P12_COMMON_GROUND_REL_PATH)
    if has_subject_capability and cg_doc is not None:
        any_state = True
    cg_items = cg_doc.get("items") if isinstance(cg_doc, Mapping) else None
    if isinstance(cg_items, Mapping):
        for gid in sorted(cg_items.keys()):
            if compute_vc_item_digest(gid) in sealed_digests:
                continue
            item = cg_items[gid]
            if not isinstance(item, Mapping):
                continue
            if _is_barrier_blocked_safe({"item_id": gid, "ground_id": gid, **dict(item)}, barrier):
                continue
            status = str(item.get("ground_status") or "PRIVATE_UNSHARED")
            if not has_subject_capability and status != "SHARED_WITH_PARTNER":
                continue
            any_state = True
            occurred = item.get("created_at") or item.get("updated_at") or stamp
            topic = str(item.get("topic") or "").strip()
            summary = str(item.get("summary") or topic or "一条共同认知")[:120]
            events.append({
                "occurred_at": str(occurred),
                "date_key": _p12_date_key(occurred, stamp),
                "kind": "common_ground",
                "summary": summary,
                "source_ref": f"common_ground:{gid}",
                "shared": status == "SHARED_WITH_PARTNER",
                "milestone": status == "SHARED_WITH_PARTNER",
                "motif": topic,
            })

    # 3. AgencyDepth 选择片段 / 二阶小规矩 / 已收敛 Inquiry
    ad_doc = _read_json_object_readonly(root / P12_AGENCY_DEPTH_REL_PATH)
    if has_subject_capability and ad_doc is not None:
        any_state = True
    if isinstance(ad_doc, Mapping):
        episodes = ad_doc.get("choice_episodes")
        if isinstance(episodes, list):
            for ep in episodes:
                if not isinstance(ep, Mapping):
                    continue
                if _is_barrier_blocked_safe(ep, barrier):
                    continue
                if not has_subject_capability and not bool(ep.get("shared", False)):
                    continue
                any_state = True
                occurred = ep.get("recorded_at") or stamp
                domain = str(ep.get("domain") or "").strip()
                chosen = str(ep.get("chosen") or "").strip()
                events.append({
                    "occurred_at": str(occurred),
                    "date_key": _p12_date_key(occurred, stamp),
                    "kind": "choice_episode",
                    "summary": (f"{domain}：{chosen}" if domain else chosen or "一次自主选择")[:120],
                    "source_ref": f"agency_depth:{ep.get('episode_id') or 'choice'}",
                    "shared": bool(ep.get("shared", False)),
                    "milestone": bool(ep.get("promoted_preference", False)),
                    "motif": domain,
                })
        rules = ad_doc.get("second_order_rules")
        if isinstance(rules, list):
            for rl in rules:
                if not isinstance(rl, Mapping):
                    continue
                if _is_barrier_blocked_safe(rl, barrier):
                    continue
                if not has_subject_capability and not bool(rl.get("shared", False)):
                    continue
                any_state = True
                occurred = rl.get("created_at") or rl.get("updated_at") or stamp
                title = str(rl.get("title") or "一条二阶小规矩").strip()
                events.append({
                    "occurred_at": str(occurred),
                    "date_key": _p12_date_key(occurred, stamp),
                    "kind": "second_order_rule",
                    "summary": title[:120],
                    "source_ref": f"agency_depth:{rl.get('rule_id') or 'rule'}",
                    "shared": bool(rl.get("shared", False)),
                    "milestone": str(rl.get("status") or "") == "endorsed",
                    "motif": title,
                })
        inquiries = ad_doc.get("inquiries")
        if isinstance(inquiries, list):
            for iq in inquiries:
                if not isinstance(iq, Mapping):
                    continue
                if _is_barrier_blocked_safe(iq, barrier):
                    continue
                if not has_subject_capability and not bool(iq.get("shared", False)):
                    continue
                if str(iq.get("status") or "") not in ("CONCLUDED", "STOPPED"):
                    continue
                any_state = True
                occurred = iq.get("settled_at") or iq.get("updated_at") or stamp
                question = str(iq.get("question") or "一个有界探究").strip()
                conclusion = str(iq.get("conclusion") or iq.get("stop_reason") or "").strip()
                events.append({
                    "occurred_at": str(occurred),
                    "date_key": _p12_date_key(occurred, stamp),
                    "kind": "inquiry",
                    "summary": (conclusion or question)[:120],
                    "source_ref": f"agency_depth:{iq.get('inquiry_id') or 'inquiry'}",
                    "shared": bool(iq.get("shared", False)),
                    "milestone": str(iq.get("status") or "") == "CONCLUDED",
                    "motif": str(iq.get("topic_tag") or question),
                })

    # 4. Workbench 实证记录（托盘上真实存在的未完成作品）
    for wb_rel in P12_WORKBENCH_REL_PATHS:
        wb_doc = _read_json_object_readonly(root / wb_rel)
        if wb_doc is None:
            continue
        tray = wb_doc.get("tray") if isinstance(wb_doc, Mapping) else None
        work = tray.get("work") if isinstance(tray, Mapping) else None
        if isinstance(work, Mapping):
            aid = str(work.get("artifact_id") or "").strip()
            vis = str(work.get("visibility") or "").strip().lower()
            is_work_shared = (
                not bool(work.get("private"))
                and vis not in ("private", "subject_only", "unshared")
                and (
                    work.get("shared") is True
                    or work.get("shared_with_partner") is True
                    or vis == "shared"
                )
            )
            blocked_work = bool(aid) and (
                compute_vc_item_digest(aid) in sealed_digests
                or _is_barrier_blocked_safe({"artifact_id": aid, **dict(work)}, barrier)
            )
            if aid and not blocked_work and (has_subject_capability or is_work_shared):
                any_state = True
                occurred = wb_doc.get("updated_at") or stamp
                events.append({
                    "occurred_at": str(occurred),
                    "date_key": _p12_date_key(occurred, stamp),
                    "kind": "workbench",
                    "summary": f"工作台上仍在推进的作品 {aid}"[:120],
                    "source_ref": f"workbench:{aid}",
                    "shared": bool(is_work_shared),
                    "milestone": False,
                    "motif": "",
                })
        elif has_subject_capability:
            any_state = True
        break

    return any_state, events


def _p12_build_chapters(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_month: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        by_month.setdefault(ev["date_key"][:7], []).append(ev)
    chapters: list[dict[str, Any]] = []
    for month_key in sorted(by_month.keys()):
        rows = by_month[month_key]
        refs = sorted({ev["source_ref"] for ev in rows})
        dates = sorted(_p12_date_key(ev.get("occurred_at"), ev["date_key"]) for ev in rows)
        year, month = month_key[:4], month_key[5:7]
        chapters.append({
            "chapter_id": f"ch_{year}_{month}",
            "title": f"{int(year)}年{int(month)}月",
            "period_label": month_key,
            "start_date": dates[0] if dates else None,
            "end_date": dates[-1] if dates else None,
            "entry_count": len(rows),
            "source_refs": refs,
        })
    return chapters[-P12_MAX_CHAPTERS:]


def _p12_build_motifs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for ev in events:
        motif = str(ev.get("motif") or "").strip()
        if not motif:
            continue
        bucket = groups.setdefault(motif, {"motif": motif, "occurrences": 0, "source_refs": set()})
        bucket["occurrences"] += 1
        bucket["source_refs"].add(ev["source_ref"])
    recurring = [b for b in groups.values() if b["occurrences"] >= 2]
    recurring.sort(key=lambda b: (-b["occurrences"], b["motif"]))
    return [
        {"motif": b["motif"], "occurrences": b["occurrences"], "source_refs": sorted(b["source_refs"])}
        for b in recurring[:P12_MAX_MOTIFS]
    ]


def project_p12_autobiographical_narrative(
    state_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """P12 自传体叙事只读投影：聚合既有实证磁盘事实，零写盘、零编造、零大模型。

    严格隐私门控：``has_subject_capability=False``（伴侣视角）时过滤一切未共享条目、
    主体私密记忆与内部后悔细节，只保留允许公开/已共享的事实。实例为空时诚实返回
    空章节、空转折、空母题与明确 ``state_status``，绝不编造不存在的经历。
    """
    stamp = _now_iso(now_iso)
    any_state, events = _p12_collect_events(
        state_root,
        has_subject_capability=bool(has_subject_capability),
        stamp=stamp,
    )

    events.sort(key=lambda ev: (ev["date_key"], ev["source_ref"]))
    turning_points = [
        {
            "occurred_at": ev["occurred_at"],
            "kind": ev["kind"],
            "summary": ev["summary"],
            "source_ref": ev["source_ref"],
            "shared": ev["shared"],
        }
        for ev in events
        if ev.get("milestone")
    ][-P12_MAX_TURNING_POINTS:]

    attested_refs = sorted({ev["source_ref"] for ev in events})
    if not any_state:
        status = P12_STATUS_NO_STATE
        horizon = {"start_date": None, "end_date": None, "span_days": 0, "coverage": "NO_STATE_FILES"}
    elif not events:
        status = P12_STATUS_NO_EVENTS
        horizon = {"start_date": None, "end_date": None, "span_days": 0, "coverage": "NO_ATTESTED_EVENTS"}
    else:
        status = P12_STATUS_ATTESTED
        start_day = events[0]["date_key"]
        end_day = events[-1]["date_key"]
        try:
            span_days = max(0, (date.fromisoformat(end_day) - date.fromisoformat(start_day)).days)
        except ValueError:
            span_days = 0
        horizon = {
            "start_date": start_day,
            "end_date": end_day,
            "span_days": span_days,
            "coverage": "ATTESTED",
        }

    return {
        "schema_version": P12_SCHEMA_VERSION,
        "state_exists": bool(any_state),
        "perspective": "subject" if has_subject_capability else "partner",
        "observed_at": stamp,
        "state_status": status,
        "chapters": _p12_build_chapters(events),
        "turning_points": turning_points,
        "recurring_motifs": _p12_build_motifs(events),
        "narrative_horizon": horizon,
        "attested_source_refs": attested_refs,
        "attested_event_count": len(events),
        "llm_calls": 0,
        "zero_write": True,
    }


def read_v02_recall_projection_view(
    day_root: Path | str,
    *,
    now_iso: str | None = None,
    has_subject_capability: bool = False,
) -> dict[str, Any]:
    """GET 读侧视图（保证零磁盘写入）。"""
    cap = bool(has_subject_capability)
    direct = project_recall_set(
        day_root,
        now_iso=now_iso,
        query_mode=QUERY_MODE_DIRECT,
        include_unavailable=cap,
        has_subject_capability=cap,
    )
    proactive = project_recall_set(
        day_root,
        now_iso=now_iso,
        query_mode=QUERY_MODE_PROACTIVE,
        include_unavailable=False,
        has_subject_capability=cap,
    )
    narrative = project_p12_autobiographical_narrative(
        day_root,
        has_subject_capability=cap,
        now_iso=direct["evaluated_at"],
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": direct["evaluated_at"],
        "clarity_counts": direct["clarity_counts"],
        "direct_recall_items": direct["items"],
        "proactive_surfaceable_ids": [
            row["memory_id"] for row in proactive["items"]
        ],
        "p12_autobiographical_narrative": narrative,
    }


def summarize_recall_for_cognition(
    day_root: Path | str,
    *,
    now_iso: str | None = None,
    limit: int = 6,
    has_subject_capability: bool = True,
) -> dict[str, Any]:
    """供认知回路消费的有界只读摘要（零写入、自动过滤 UNAVAILABLE 与主动淡忘）。"""
    proactive = project_recall_set(
        day_root,
        now_iso=now_iso,
        query_mode=QUERY_MODE_PROACTIVE,
        include_unavailable=False,
        has_subject_capability=bool(has_subject_capability),
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
    has_subject_capability: bool = True,
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
            shared=payload.get("shared") if "shared" in payload else None,
            visibility=str(payload.get("visibility")) if payload.get("visibility") is not None else None,
            episode_key=payload.get("episode_key"),
            source_refs=payload.get("source_refs"),
            now_iso=now_iso,
            has_subject_capability=bool(has_subject_capability),
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
    "CH131_SPONTANEOUS_EXACT_CEILING_DAYS",
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
    "UNCERTAINTY_GIST_IMPRESSION",
    "UNCERTAINTY_RANGE_IMPRESSION",
    "VC_AUTHORITY_SCHEMA",
    "VC_AUTHORITY_WATERMARK_REL_PATH",
    "apply_ch131_two_stage_clarity_to_memory_objects",
    "compute_vc_item_digest",
    "mark_active_fade",
    "mutate_v02_recall_projection",
    "project_memory_item",
    "project_p12_autobiographical_narrative",
    "project_recall_set",
    "read_v02_recall_projection_view",
    "read_vc_authority_watermark_readonly",
    "record_episodic_trace",
    "record_rehearsal",
    "record_vc_revocation_seal",
    "summarize_recall_for_cognition",
    "tombstone_memory",
]

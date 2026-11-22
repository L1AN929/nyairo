#!/usr/bin/env python3
"""0.5 内在灵光纵切引擎：三层开放欲望抽屉、入睡与偶发梦境、书页延迟伏笔、雾窗哈气与零算力器物小机关。

对应设计章节：第 102 / 109 / 111 / 114 / 130 章。
不变量：
1. 100% 零大模型调用（``llm_calls == 0``），纯本地确定性逻辑；
2. 读路径（``project_v05_inner_spark_state``）100% 零写盘：绝不新建目录、文件、锁文件或修改已有文件；
3. 写路径使用 ``Path(day_root) / "life" / "v05_inner_spark.json"``，通过同目录临时文件 + ``os.replace`` 原子落盘（UTF-8 无 BOM、纯 LF）；
4. 严格反提线木偶边界：只有主体（TA）能写入/收束自己的开放欲望及结算/留存/分享梦境；伴侣越权一律返回 ``403 SUBJECT_ONLY_ACTION``；
5. 严格反剧透与隐私隔离：对方塞在未翻到后续页码里的伏笔在翻到前完全不可见；未分享的梦境与私密欲望对伴侣完全不可见；
6. 直接复用 ``components/world/scripts/v05_unified_world_fabric.py`` 中的 ``ForeshadowBook``、``TuckedPageNote``、
   ``WindowFrostCanvas`` 及 5 个零算力器物物理原语。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _entry in (str(REPO_ROOT), str(_WORLD_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:
    from components.world.scripts import v05_unified_world_fabric as world_fabric
except ImportError:  # pragma: no cover - fallback when scripts dir is loaded directly
    import v05_unified_world_fabric as world_fabric  # type: ignore[no-redef]

try:
    from components.world.scripts import v05_season_solar_pulse as season_pulse
except ImportError:  # pragma: no cover - fallback when scripts dir is loaded directly
    import v05_season_solar_pulse as season_pulse  # type: ignore[no-redef]

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover - fallback when scripts dir is loaded directly
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.life.scripts import everyday_desire_lifecycle as _everyday_desires
except ImportError:  # pragma: no cover - fallback when scripts dir is loaded directly
    try:
        import everyday_desire_lifecycle as _everyday_desires  # type: ignore[no-redef]
    except ImportError:
        _everyday_desires = None  # type: ignore[assignment]

try:
    from components.life.scripts import v02_representation_lease_forget as _rlf
except ImportError:  # pragma: no cover - fallback when scripts dir is loaded directly
    try:
        import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]
    except ImportError:
        _rlf = None  # type: ignore[assignment]


STATE_SCHEMA = "nyairo.v05.inner_spark.v1"
STATE_REL_PATH: tuple[str, ...] = ("life", "v05_inner_spark.json")
DEFAULT_TZ = timezone(timedelta(hours=8))

DESIRE_LAYERS: tuple[str, ...] = (
    "micro_craving",
    "mid_itch",
    "long_aspiration",
)
DESIRE_LAYER_LABELS_CN: dict[str, str] = {
    "micro_craving": "微念·当下小馋或临时起意",
    "mid_itch": "近念·这几天心里挂着的小事",
    "long_aspiration": "长念·慢慢放在心底的长线念想",
}
DESIRE_CATEGORIES: tuple[str, ...] = (
    "appetite_craving",
    "restlessness_outing",
    "curiosity_mystery",
    "craft_ambition",
    "mischief_rivalry",
    "social_longing",
    "cozy_comfort",
    "intimate_warmth",
)
DEFAULT_DESIRE_CATEGORY = "curiosity_mystery"
DESIRE_ORIGINS: tuple[str, ...] = (
    "self_emerged",
    "world_spark",
    "shared_hint",
    "manual",
)
DESIRE_STATUSES: tuple[str, ...] = (
    "active",
    "fulfilled",
    "faded",
    "conflicted",
)
RESOLVABLE_DESIRE_STATUSES: tuple[str, ...] = (
    "fulfilled",
    "faded",
    "conflicted",
)
ACTIVE_CAP_BY_LAYER: dict[str, int] = {
    "micro_craving": 2,
    "mid_itch": 2,
    "long_aspiration": 1,
}
DEFAULT_DECAY_PER_HOUR_BY_LAYER: dict[str, float] = {
    "micro_craving": 0.18,
    "mid_itch": 0.06,
    "long_aspiration": 0.015,
}
DEFAULT_DESIRE_INTENSITY = 0.65
DESIRE_FADE_INTENSITY_THRESHOLD = 0.08
ZERO_DEBT_FADE_NOTE_CN = "自然淡出，不留心理欠账"
MAX_WORLD_ANCHOR_LEN = 96
EMERGE_ANCHOR_COOLDOWN_SECONDS = 6 * 3600
MAX_ANCHOR_COOLDOWNS = 48
_RAINY_EMERGE_WEATHERS = frozenset({
    "drizzle",
    "rain",
    "shower",
    "heavy_rain",
    "humid_plum_rain",
    "storm",
    "thunder_blackout",
    "sea_gale",
})

SLEEP_PHASES: tuple[str, ...] = (
    "awake",
    "winding_down",
    "asleep",
    "just_woke",
)
BOOK_ITEM_KINDS: tuple[str, ...] = (
    "leaf",
    "note",
    "bookmark",
    "pressed_flower",
    "ticket",
)
OBJECT_CHEMISTRY_KINDS: tuple[str, ...] = (
    "same_bag_condensation",
    "hot_mug_ironing",
    "window_breeze_flip",
    "flyer_shoe_drying",
    "half_tangerine_offering",
)

FORBIDDEN_IDENTITY_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "actor",
    "actor_id",
    "actor_ref",
    "has_subject_capability",
    "capability_token",
    "subject_id",
    "subject_ref",
    "grant_id",
    "private_override",
    "verified_main_self",
    "main_self_verified",
    "authority",
    "authorities",
    "uid",
    "euid",
    "gid",
    "intimacy_score",
    "affection_score",
    "affection_delta",
    "favorability",
    "relationship_score",
    "love_points",
    "obedience_score",
    "trust_score",
    "mood_score",
    "mood_delta",
    "life_score",
    "auto_locked",
    "__proto__",
    "constructor",
    "prototype",
})

MAX_DESIRE_TEXT_LEN = 120
MAX_PARTNER_NOTE_LEN = 120
MAX_RESOLUTION_NOTE_LEN = 120
MAX_FORESHADOW_NOTE_LEN = 120
MAX_BOOK_TITLE_LEN = 80
MAX_WINDOW_FROST_TEXT_LEN = 40
MAX_ECHO_LEN = 36

MAX_DESIRES = 80
MAX_KEPT_DREAMS = 40
MAX_FORESHADOW_BOOKS = 32
MAX_REVEALED_NOTES_PER_BOOK = 60
MAX_CHEMISTRY_SPARKS = 16

DREAM_CHANCE_BPS = 2200  # ~22% on ordinary nights
FROST_MELTED_THRESHOLD = 0.05

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_OVERCAST_WEATHERS = frozenset({
    "rain",
    "shower",
    "heavy_rain",
    "humid_plum_rain",
    "fog",
    "coastal_mist",
    "snow",
    "thunder_blackout",
})
_MISTY_HUMIDITY_HINTS = frozenset({
    "high",
    "misty",
    "foggy",
    "steamy",
    "damp",
    "condensing",
    "wet",
})
_DRY_HUMIDITY_HINTS = frozenset({
    "dry",
    "low",
    "arid",
})
_MISTY_WEATHERS = frozenset({
    "rain",
    "shower",
    "heavy_rain",
    "humid_plum_rain",
    "snow",
    "coastal_mist",
    "fog",
    "breezy_cool",
    "cold_clear",
})

_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


class V05InnerSparkError(Exception):
    """Structured domain exception for the 0.5 Inner Spark engine."""

    def __init__(self, status_code: int, reason_code: str, message: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        super().__init__(f"{self.reason_code}: {self.message}")


# ---------------------------------------------------------------------------
# Basic validation, time helpers, and atomic zero-write-on-read persistence
# ---------------------------------------------------------------------------


def state_file_path(day_root: Path | str) -> Path:
    return _instance_paths.resolve_instance_paths(day_root).day_root.joinpath(*STATE_REL_PATH)


def _path_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(str(path)))
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _is_strict_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _normalize_actor_side(actor_side: Any) -> str:
    if not isinstance(actor_side, str) or not actor_side.strip():
        raise V05InnerSparkError(403, "SUBJECT_ONLY_ACTION", "Invalid actor_side")
    norm = actor_side.strip().lower()
    if norm not in ("partner", "subject"):
        raise V05InnerSparkError(403, "SUBJECT_ONLY_ACTION", f"Unsupported actor_side: {actor_side!r}")
    return norm


def _require_subject(actor_side: Any, action_name: str) -> str:
    norm = _normalize_actor_side(actor_side)
    if norm != "subject":
        raise V05InnerSparkError(
            403,
            "SUBJECT_ONLY_ACTION",
            f"Action {action_name!r} can only be performed by TA (subject)",
        )
    return norm


def _to_default_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=DEFAULT_TZ)
    return dt.astimezone(DEFAULT_TZ)


def _parse_iso_dt(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    txt = raw.strip()
    if txt.endswith(("Z", "z")):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    return _to_default_tz(dt)


def _resolve_now_dt(now_iso: Optional[str], *, strict: bool = True) -> tuple[datetime, str]:
    if now_iso is None:
        dt = datetime.now(tz=DEFAULT_TZ)
        return dt, dt.isoformat(timespec="seconds")
    dt = _parse_iso_dt(now_iso)
    if dt is None:
        if strict:
            raise V05InnerSparkError(400, "INVALID_FIELDS", f"Malformed ISO timestamp: {now_iso!r}")
        dt = datetime.now(tz=DEFAULT_TZ)
        return dt, dt.isoformat(timespec="seconds")
    return dt.astimezone(DEFAULT_TZ), str(now_iso).strip()


def _epoch_hours(dt: datetime) -> float:
    return dt.astimezone(timezone.utc).timestamp() / 3600.0


def _clean_text(
    raw: Any,
    *,
    field_name: str,
    max_len: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(raw, str):
        raise V05InnerSparkError(400, "INVALID_FIELDS", f"Field {field_name!r} must be a string")
    cleaned = _CONTROL_CHAR_RE.sub("", raw).strip()
    if not cleaned and not allow_empty:
        raise V05InnerSparkError(400, "INVALID_FIELDS", f"Field {field_name!r} cannot be empty")
    if len(cleaned) > max_len:
        raise V05InnerSparkError(
            400,
            "TEXT_TOO_LONG",
            f"Field {field_name!r} exceeds maximum length of {max_len} characters",
        )
    return cleaned


def _stable_id(prefix: str, *parts: Any) -> str:
    joined = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _normalize_desire_item(raw: Mapping[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(dict(raw))
    layer = str(item.get("layer") or "micro_craving")
    if layer not in DESIRE_LAYERS:
        layer = "micro_craving"
    item["layer"] = layer

    raw_cat = item.get("category")
    category = str(raw_cat).strip() if isinstance(raw_cat, str) and raw_cat.strip() in DESIRE_CATEGORIES else DEFAULT_DESIRE_CATEGORY
    item["category"] = category

    raw_origin = item.get("origin")
    origin = str(raw_origin).strip() if isinstance(raw_origin, str) and raw_origin.strip() in DESIRE_ORIGINS else "self_emerged"
    item["origin"] = origin

    raw_anchor = item.get("world_anchor")
    item["world_anchor"] = (
        _CONTROL_CHAR_RE.sub("", raw_anchor).strip()[:MAX_WORLD_ANCHOR_LEN] or None
        if isinstance(raw_anchor, str)
        else None
    )

    raw_intensity = item.get("intensity")
    if _is_strict_number(raw_intensity):
        intensity_val = round(max(0.0, min(1.0, float(raw_intensity))), 4)
    else:
        intensity_val = DEFAULT_DESIRE_INTENSITY
    item["intensity"] = intensity_val

    raw_decay = item.get("decay_per_hour")
    if _is_strict_number(raw_decay) and float(raw_decay) >= 0.0:
        decay_val = round(float(raw_decay), 4)
    else:
        decay_val = DEFAULT_DECAY_PER_HOUR_BY_LAYER[layer]
    item["decay_per_hour"] = decay_val

    raw_friction = item.get("friction_count")
    item["friction_count"] = max(0, int(raw_friction)) if _is_strict_int(raw_friction) else 0

    raw_cost = item.get("estimated_cost_jpy")
    item["estimated_cost_jpy"] = max(0, int(raw_cost)) if _is_strict_int(raw_cost) else 0

    item["requires_outing"] = bool(item.get("requires_outing", False))

    raw_status = item.get("status")
    item["status"] = str(raw_status) if isinstance(raw_status, str) and raw_status in DESIRE_STATUSES else "active"

    if category == "intimate_warmth":
        item["shared"] = False
    else:
        item["shared"] = bool(item.get("shared", True))

    item.setdefault("partner_note", None)
    item.setdefault("partner_note_at", None)
    item.setdefault("resolution_note", None)
    item["frustration_debt"] = 0
    return item


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "revision": 0,
        "updated_at": None,
        "last_desire_tick_at": None,
        "last_emerge_at": None,
        "emerge_anchor_cooldowns": {},
        "desires": [],
        "sleep_wake_state": {
            "phase": "awake",
            "last_sleep_at": None,
            "last_wake_at": None,
            "last_dream_roll_date": None,
            "morning_dream": None,
        },
        "dream_notebook": [],
        "foreshadow_books": {},
        "window_frost": None,
        "recent_chemistry_sparks": [],
    }


def _normalize_loaded_state(doc: Any) -> dict[str, Any]:
    state = _empty_state()
    if not isinstance(doc, Mapping):
        return state
    rev = doc.get("revision", 0)
    state["revision"] = int(rev) if _is_strict_int(rev) and rev >= 0 else 0
    state["updated_at"] = doc.get("updated_at") if isinstance(doc.get("updated_at"), str) else None
    state["last_desire_tick_at"] = (
        doc.get("last_desire_tick_at") if isinstance(doc.get("last_desire_tick_at"), str) else None
    )
    state["last_emerge_at"] = (
        doc.get("last_emerge_at") if isinstance(doc.get("last_emerge_at"), str) else None
    )
    raw_cd = doc.get("emerge_anchor_cooldowns")
    if isinstance(raw_cd, Mapping):
        clean_cd = {
            str(k)[:MAX_WORLD_ANCHOR_LEN]: str(v)
            for k, v in raw_cd.items()
            if isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
        }
        state["emerge_anchor_cooldowns"] = dict(list(clean_cd.items())[-MAX_ANCHOR_COOLDOWNS:])
    if isinstance(doc.get("desires"), list):
        state["desires"] = [_normalize_desire_item(x) for x in doc["desires"] if isinstance(x, Mapping)]
    sw = doc.get("sleep_wake_state")
    if isinstance(sw, Mapping):
        phase = str(sw.get("phase") or "awake")
        state["sleep_wake_state"]["phase"] = phase if phase in SLEEP_PHASES else "awake"
        state["sleep_wake_state"]["last_sleep_at"] = (
            sw.get("last_sleep_at") if isinstance(sw.get("last_sleep_at"), str) else None
        )
        state["sleep_wake_state"]["last_wake_at"] = (
            sw.get("last_wake_at") if isinstance(sw.get("last_wake_at"), str) else None
        )
        state["sleep_wake_state"]["last_dream_roll_date"] = (
            sw.get("last_dream_roll_date") if isinstance(sw.get("last_dream_roll_date"), str) else None
        )
        md = sw.get("morning_dream")
        state["sleep_wake_state"]["morning_dream"] = copy.deepcopy(dict(md)) if isinstance(md, Mapping) else None
    if isinstance(doc.get("dream_notebook"), list):
        state["dream_notebook"] = [copy.deepcopy(dict(x)) for x in doc["dream_notebook"] if isinstance(x, Mapping)]
    fb = doc.get("foreshadow_books")
    if isinstance(fb, Mapping):
        books_out: dict[str, dict[str, Any]] = {}
        for title, brec in fb.items():
            if isinstance(title, str) and isinstance(brec, Mapping):
                books_out[title] = copy.deepcopy(dict(brec))
        state["foreshadow_books"] = books_out
    wf = doc.get("window_frost")
    if isinstance(wf, Mapping):
        state["window_frost"] = copy.deepcopy(dict(wf))
    if isinstance(doc.get("recent_chemistry_sparks"), list):
        state["recent_chemistry_sparks"] = [
            copy.deepcopy(dict(x)) for x in doc["recent_chemistry_sparks"] if isinstance(x, Mapping)
        ][-MAX_CHEMISTRY_SPARKS:]
    return state


def _read_state_zero_write(day_root: Path | str) -> tuple[dict[str, Any], bool]:
    """100% zero-write read: never creates directories, files, or lock files."""
    path = state_file_path(day_root)
    if path.is_symlink() or not path.is_file():
        return _empty_state(), False
    try:
        raw = path.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return _empty_state(), False
    return _normalize_loaded_state(doc), True


def _read_explicit_season_zero_write(day_root: Path | str) -> Optional[str]:
    """Read optional explicit season override from existing world state with 0 disk writes."""
    paths = _instance_paths.resolve_instance_paths(day_root)
    root = paths.day_root
    explicit: Optional[str] = None
    nb_path = root / "world" / "v04_neighborhood_state.json"
    try:
        if not nb_path.is_symlink() and nb_path.is_file():
            nb_doc = json.loads(nb_path.read_text(encoding="utf-8"))
            if isinstance(nb_doc, Mapping):
                env = nb_doc.get("environment") if isinstance(nb_doc.get("environment"), Mapping) else {}
                norm = season_pulse.normalize_season(env.get("season"))
                if norm is not None:
                    season_explicit = env.get("season_explicit")
                    if season_explicit is True:
                        explicit = norm
                    elif season_explicit is False:
                        explicit = None
                    else:
                        rev = nb_doc.get("revision")
                        env_updated = env.get("updated_at")
                        is_unmodified_default_autumn = (
                            norm == "autumn"
                            and (rev is None or rev == 0)
                            and not (isinstance(env_updated, str) and env_updated.strip())
                        )
                        if not is_unmodified_default_autumn:
                            explicit = norm
    except (OSError, ValueError, TypeError):
        pass

    ws_candidates: list[Path] = [
        paths.world_state_json,
        root / "world" / "world_state.json",
        root / "data" / "world" / "world_state.json",
    ]
    seen_ws: set[str] = set()
    for ws_path in ws_candidates:
        ws_key = str(ws_path)
        if ws_key in seen_ws:
            continue
        seen_ws.add(ws_key)
        try:
            if ws_path.is_symlink() or not ws_path.is_file():
                continue
            ws_doc = json.loads(ws_path.read_text(encoding="utf-8"))
            if isinstance(ws_doc, Mapping):
                env_w = ws_doc.get("environment") if isinstance(ws_doc.get("environment"), Mapping) else {}
                for cand in (ws_doc.get("season"), env_w.get("season")):
                    norm = season_pulse.normalize_season(cand)
                    if norm is not None:
                        return norm
        except (OSError, ValueError, TypeError):
            continue
    return explicit


def _resolve_season_pulse_zero_write(day_root: Path | str, now_dt: datetime) -> dict[str, Any]:
    explicit_season = _read_explicit_season_zero_write(day_root)
    return season_pulse.project_season_solar_pulse(
        now_iso=now_dt,
        explicit_season=explicit_season,
    )


def _atomic_write_state(path: Path, state: Mapping[str, Any]) -> None:
    if path.is_symlink():
        raise V05InnerSparkError(400, "UNSAFE_STATE_PATH", "State file path cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".v05_inner_spark.",
            suffix=".tmp",
            dir=str(path.parent),
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(path))
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _commit_state(day_root: Path | str, state: dict[str, Any], now_str: str) -> None:
    state["schema_version"] = STATE_SCHEMA
    state["revision"] = int(state.get("revision", 0)) + 1
    state["updated_at"] = now_str
    _atomic_write_state(state_file_path(day_root), state)


def _emit_inner_spark_lived_episode(
    day_root: Path | str,
    *,
    episode_key: str,
    topic: str,
    exact_detail: str,
    gist_summary: str,
    occurred_at: str,
    emotional_salience: float = 0.56,
    affect_kind: Optional[str] = "affect_pulse",
    affect_intensity: int = 42,
    shared: bool = False,
    source_refs: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """Best-effort canonical memory sink hook for dreams & fulfilled desires (write-path only)."""
    try:
        try:
            from components.life.scripts import lived_memory_sink as _lms
        except ImportError:
            _life_dir = str(Path(__file__).resolve().parent)
            if _life_dir not in sys.path:
                sys.path.insert(0, _life_dir)
            import lived_memory_sink as _lms  # type: ignore[no-redef]
        ev = _lms.LivedEpisodeEvent(
            episode_key=episode_key,
            domain="v05_dream_desire",
            topic=topic,
            exact_detail=exact_detail,
            gist_summary=gist_summary,
            occurred_at=occurred_at,
            emotional_salience=emotional_salience,
            affect_kind=affect_kind,
            affect_intensity=affect_intensity,
            shared=shared,
            source_refs=source_refs,
        )
        return _lms.emit_lived_episode_safe(day_root, ev, now_iso=occurred_at)
    except Exception as exc:
        return {"ok": False, "recorded": False, "reason": f"SINK_IMPORT:{type(exc).__name__}", "llm_calls": 0}


# ---------------------------------------------------------------------------
# 1. Three-Layer Open Desire Drawer (Ch. 130, 2+2+1 Bounded Full-Spectrum)
# ---------------------------------------------------------------------------


def _active_counts_by_layer(desires: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {layer: 0 for layer in DESIRE_LAYERS}
    for d in desires:
        if str(d.get("status") or "active") != "active":
            continue
        layer_k = str(d.get("layer") or "micro_craving")
        if layer_k in counts:
            counts[layer_k] += 1
    return counts


def _enforce_active_slot_cap_in_place(
    desires: list[dict[str, Any]],
    *,
    now_str: str,
    layer: Optional[str] = None,
    protected_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Enforce Ch. 130 2+2+1 active cap, fading lowest-intensity older overflow with zero debt."""
    target_layers = (layer,) if layer is not None else DESIRE_LAYERS
    evicted: list[dict[str, Any]] = []
    for lyr in target_layers:
        cap = int(ACTIVE_CAP_BY_LAYER.get(lyr, 2))
        while True:
            active_entries = [
                (idx, item)
                for idx, item in enumerate(desires)
                if item.get("status") == "active" and item.get("layer") == lyr
            ]
            if len(active_entries) <= cap:
                break
            candidates = [
                pair for pair in active_entries if protected_id is None or pair[1].get("desire_id") != protected_id
            ]
            if not candidates:
                candidates = active_entries

            def _evict_sort_key(pair: tuple[int, dict[str, Any]]) -> tuple[float, float, int]:
                idx_val, rec = pair
                intensity_val = (
                    float(rec["intensity"])
                    if _is_strict_number(rec.get("intensity"))
                    else DEFAULT_DESIRE_INTENSITY
                )
                created_dt = _parse_iso_dt(rec.get("created_at"))
                created_ts = created_dt.timestamp() if created_dt is not None else 0.0
                return (intensity_val, created_ts, idx_val)

            _, victim = min(candidates, key=_evict_sort_key)
            victim["status"] = "faded"
            victim["resolution_note"] = ZERO_DEBT_FADE_NOTE_CN
            victim["updated_at"] = now_str
            victim["frustration_debt"] = 0
            evicted.append(copy.deepcopy(victim))
    return evicted


def _trim_desires_history_in_place(state: dict[str, Any]) -> None:
    desires: list[dict[str, Any]] = state["desires"]
    if len(desires) > MAX_DESIRES:
        active_items = [d for d in desires if d.get("status") == "active"]
        settled_items = [d for d in desires if d.get("status") != "active"]
        keep_settled = max(0, MAX_DESIRES - len(active_items))
        state["desires"] = (settled_items[-keep_settled:] if keep_settled > 0 else []) + active_items[-MAX_DESIRES:]


def _sync_everyday_projection_if_present(
    day_root: Path | str,
    desires: Sequence[Mapping[str, Any]],
    *,
    now_str: str,
) -> Optional[dict[str, Any]]:
    if _everyday_desires is None:
        return None
    sync_fn = getattr(_everyday_desires, "sync_v05_desires_projection", None)
    if not callable(sync_fn):
        return None
    try:
        return sync_fn(day_root, list(desires), now_iso=now_str, create_if_absent=False)
    except Exception:
        return None


def record_subject_desire(
    day_root: Path | str,
    *,
    layer: str,
    text: str,
    origin: str = "self_emerged",
    shared: bool = True,
    category: Optional[str] = None,
    world_anchor: Optional[str] = None,
    intensity: Optional[float] = None,
    decay_per_hour: Optional[float] = None,
    friction_count: int = 0,
    estimated_cost_jpy: int = 0,
    requires_outing: bool = False,
    expires_at: Optional[str] = None,
    now_iso: Optional[str] = None,
    actor_side: str = "subject",
) -> dict[str, Any]:
    """Record a free-form open desire for TA in one of the three layers (subject-only)."""
    _require_subject(actor_side, "record_subject_desire")
    if not isinstance(layer, str) or layer not in DESIRE_LAYERS:
        raise V05InnerSparkError(400, "INVALID_DESIRE_LAYER", f"layer must be one of {DESIRE_LAYERS}")
    if not isinstance(origin, str) or origin not in DESIRE_ORIGINS:
        raise V05InnerSparkError(400, "INVALID_DESIRE_ORIGIN", f"origin must be one of {DESIRE_ORIGINS}")
    if not isinstance(shared, bool):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'shared' must be a boolean")
    if category is None:
        eff_category = DEFAULT_DESIRE_CATEGORY
    else:
        if not isinstance(category, str) or category not in DESIRE_CATEGORIES:
            raise V05InnerSparkError(400, "INVALID_DESIRE_CATEGORY", f"category must be one of {DESIRE_CATEGORIES}")
        eff_category = category
    clean_anchor: Optional[str] = None
    if world_anchor is not None:
        clean_anchor = _clean_text(
            world_anchor,
            field_name="world_anchor",
            max_len=MAX_WORLD_ANCHOR_LEN,
            allow_empty=True,
        ) or None
    if intensity is None:
        eff_intensity = DEFAULT_DESIRE_INTENSITY
    else:
        if not _is_strict_number(intensity) or not (0.0 <= float(intensity) <= 1.0):
            raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'intensity' must be a number in [0.0, 1.0]")
        eff_intensity = round(float(intensity), 4)
    if decay_per_hour is None:
        eff_decay = DEFAULT_DECAY_PER_HOUR_BY_LAYER[layer]
    else:
        if not _is_strict_number(decay_per_hour) or float(decay_per_hour) < 0.0:
            raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'decay_per_hour' must be a non-negative number")
        eff_decay = round(float(decay_per_hour), 4)
    if not _is_strict_int(friction_count) or friction_count < 0:
        raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'friction_count' must be a non-negative integer")
    if not _is_strict_int(estimated_cost_jpy) or estimated_cost_jpy < 0:
        raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'estimated_cost_jpy' must be a non-negative integer")
    if not isinstance(requires_outing, bool):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'requires_outing' must be a boolean")
    clean_expires: Optional[str] = None
    if expires_at is not None:
        if _parse_iso_dt(expires_at) is None:
            raise V05InnerSparkError(400, "INVALID_FIELDS", f"Malformed expires_at: {expires_at!r}")
        clean_expires = str(expires_at).strip()

    clean_text = _clean_text(text, field_name="text", max_len=MAX_DESIRE_TEXT_LEN)
    _, now_str = _resolve_now_dt(now_iso, strict=True)
    eff_shared = False if eff_category == "intimate_warmth" else shared

    path = state_file_path(day_root)
    with _path_lock(path):
        state, _ = _read_state_zero_write(day_root)
        seq = int(state.get("revision", 0)) + 1
        desire_id = _stable_id("des", seq, layer, clean_text, now_str, len(state["desires"]))
        item: dict[str, Any] = {
            "desire_id": desire_id,
            "layer": layer,
            "category": eff_category,
            "text": clean_text,
            "origin": origin,
            "world_anchor": clean_anchor,
            "intensity": eff_intensity,
            "decay_per_hour": eff_decay,
            "friction_count": int(friction_count),
            "estimated_cost_jpy": int(estimated_cost_jpy),
            "requires_outing": requires_outing,
            "status": "active",
            "shared": eff_shared,
            "created_at": now_str,
            "updated_at": now_str,
            "partner_note": None,
            "partner_note_at": None,
            "resolution_note": None,
            "frustration_debt": 0,
        }
        if clean_expires is not None:
            item["expires_at"] = clean_expires
        desires: list[dict[str, Any]] = state["desires"]
        desires.append(item)
        evicted = _enforce_active_slot_cap_in_place(
            desires,
            now_str=now_str,
            layer=layer,
            protected_id=desire_id,
        )
        _trim_desires_history_in_place(state)
        _commit_state(day_root, state, now_str)
        _sync_everyday_projection_if_present(day_root, state["desires"], now_str=now_str)
        return {
            "action": "record_subject_desire",
            "desire": copy.deepcopy(item),
            "evicted_desires": evicted,
            "active_counts": _active_counts_by_layer(state["desires"]),
            "frustration_debt": 0,
            "revision": state["revision"],
            "llm_calls": 0,
        }


def resolve_subject_desire(
    day_root: Path | str,
    *,
    desire_id: str,
    status: str,
    resolution_note: Optional[str] = None,
    now_iso: Optional[str] = None,
    actor_side: str = "subject",
) -> dict[str, Any]:
    """Transition TA's desire status to fulfilled / faded / conflicted (subject-only)."""
    _require_subject(actor_side, "resolve_subject_desire")
    clean_id = _clean_text(desire_id, field_name="desire_id", max_len=64)
    if not isinstance(status, str) or status not in RESOLVABLE_DESIRE_STATUSES:
        raise V05InnerSparkError(
            400,
            "INVALID_DESIRE_STATUS",
            f"status must be one of {RESOLVABLE_DESIRE_STATUSES}",
        )
    clean_res_note: Optional[str] = None
    if resolution_note is not None:
        clean_res_note = _clean_text(
            resolution_note,
            field_name="resolution_note",
            max_len=MAX_RESOLUTION_NOTE_LEN,
            allow_empty=True,
        ) or None
    _, now_str = _resolve_now_dt(now_iso, strict=True)

    path = state_file_path(day_root)
    with _path_lock(path):
        state, _ = _read_state_zero_write(day_root)
        target: Optional[dict[str, Any]] = None
        for item in state["desires"]:
            if item.get("desire_id") == clean_id:
                target = item
                break
        if target is None:
            raise V05InnerSparkError(404, "DESIRE_NOT_FOUND", f"Desire not found: {clean_id}")
        target["status"] = status
        target["updated_at"] = now_str
        target["frustration_debt"] = 0
        if clean_res_note is not None:
            target["resolution_note"] = clean_res_note
        elif status == "faded" and not target.get("resolution_note"):
            target["resolution_note"] = ZERO_DEBT_FADE_NOTE_CN
        _commit_state(day_root, state, now_str)
        _sync_everyday_projection_if_present(day_root, state["desires"], now_str=now_str)
        desire_copy = copy.deepcopy(target)
        active_counts = _active_counts_by_layer(state["desires"])
        rev_after = state["revision"]

    sink_receipt: Optional[dict[str, Any]] = None
    if status == "fulfilled":
        desire_text = str(desire_copy.get("text") or "").strip()
        desire_cat = str(desire_copy.get("category") or "").strip().lower()
        is_private_desire = (not bool(desire_copy.get("shared", True))) or (desire_cat == "intimate_warmth")
        res_note_text = str(desire_copy.get("resolution_note") or "").strip()
        exact_d = f"实现了一个心愿：{desire_text}" + (f"（{res_note_text}）" if res_note_text else "")
        gist_d = f"如愿完成了一件小事：{desire_text[:80]}"
        sink_receipt = _emit_inner_spark_lived_episode(
            day_root,
            episode_key=f"v05_dream_desire:desire_fulfilled:{clean_id}",
            topic=f"心愿如愿：{desire_text[:48] or clean_id}",
            exact_detail=exact_d,
            gist_summary=gist_d,
            occurred_at=now_str,
            emotional_salience=0.62,
            affect_kind="mood_shift",
            affect_intensity=48,
            shared=(not is_private_desire),
            source_refs=(("desire_id", clean_id),),
        )

    out_resp: dict[str, Any] = {
        "action": "resolve_subject_desire",
        "desire": desire_copy,
        "active_counts": active_counts,
        "frustration_debt": 0,
        "revision": rev_after,
        "llm_calls": 0,
    }
    if sink_receipt is not None:
        out_resp["memory_sink"] = sink_receipt
    return out_resp


def record_desire_friction(
    day_root: Path | str,
    *,
    desire_id: str,
    friction_reason: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Increment ``friction_count`` on an active desire after real constraint arbitration (zero debt)."""
    clean_id = _clean_text(desire_id, field_name="desire_id", max_len=64)
    clean_reason: Optional[str] = None
    if friction_reason is not None:
        clean_reason = _clean_text(
            friction_reason,
            field_name="friction_reason",
            max_len=64,
            allow_empty=True,
        ) or None
    _, now_str = _resolve_now_dt(now_iso, strict=True)

    path = state_file_path(day_root)
    with _path_lock(path):
        state, state_exists = _read_state_zero_write(day_root)
        if not state_exists:
            return {
                "ok": False,
                "changed": False,
                "reason": "state_absent",
                "frustration_debt": 0,
                "llm_calls": 0,
            }
        target: Optional[dict[str, Any]] = None
        for item in state["desires"]:
            if item.get("desire_id") == clean_id:
                target = item
                break
        if target is None:
            return {
                "ok": False,
                "changed": False,
                "reason": "DESIRE_NOT_FOUND",
                "frustration_debt": 0,
                "llm_calls": 0,
            }
        target["friction_count"] = max(0, int(target.get("friction_count") or 0)) + 1
        if clean_reason is not None:
            target["last_friction_reason"] = clean_reason
        target["updated_at"] = now_str
        target["frustration_debt"] = 0
        _commit_state(day_root, state, now_str)
        _sync_everyday_projection_if_present(day_root, state["desires"], now_str=now_str)
        return {
            "ok": True,
            "changed": True,
            "desire": copy.deepcopy(target),
            "friction_count": target["friction_count"],
            "frustration_debt": 0,
            "revision": state["revision"],
            "llm_calls": 0,
        }


def respond_to_desire(
    day_root: Path | str,
    *,
    desire_id: str,
    partner_note: str,
    now_iso: Optional[str] = None,
    actor_side: str = "partner",
) -> dict[str, Any]:
    """Leave a gentle, non-coercive partner note on an existing visible desire without altering status."""
    norm_actor = _normalize_actor_side(actor_side)
    clean_id = _clean_text(desire_id, field_name="desire_id", max_len=64)
    clean_note = _clean_text(partner_note, field_name="partner_note", max_len=MAX_PARTNER_NOTE_LEN)
    _, now_str = _resolve_now_dt(now_iso, strict=True)

    path = state_file_path(day_root)
    with _path_lock(path):
        state, _ = _read_state_zero_write(day_root)
        target: Optional[dict[str, Any]] = None
        for item in state["desires"]:
            if item.get("desire_id") == clean_id:
                target = item
                break
        if target is None:
            raise V05InnerSparkError(404, "DESIRE_NOT_FOUND", f"Desire not found: {clean_id}")
        if norm_actor != "subject" and not bool(target.get("shared", True)):
            # Do not reveal existence of TA's private desires to partner
            raise V05InnerSparkError(404, "DESIRE_NOT_FOUND", f"Desire not found: {clean_id}")
        original_status = str(target.get("status", "active"))
        target["partner_note"] = clean_note
        target["partner_note_at"] = now_str
        target["updated_at"] = now_str
        # Invariant: status is never modified by partner response
        target["status"] = original_status
        _commit_state(day_root, state, now_str)
        return {
            "action": "respond_to_desire",
            "desire_id": clean_id,
            "partner_note": clean_note,
            "status_unchanged": original_status,
            "desire": copy.deepcopy(target),
            "revision": state["revision"],
            "llm_calls": 0,
        }


# ---------------------------------------------------------------------------
# 2. Sleep, Wake & Rare Morning Dreams (Ch. 102)
# ---------------------------------------------------------------------------


_SEASONAL_DREAM_FALLBACK_TEMPLATES: dict[str, tuple[str, ...]] = {
    "spring": (
        "快醒前像站在一条刚下过浅雨的春巷口，枝头落瓣轻晃，睁开眼就忘了去哪儿。",
        "清晨醒来前只留下一截浅绿柔光的窗影，像是春风吹动了纸页，再想细看已经模糊了。",
        "半睡半醒间好像听见门外有轻柔春风和远处的车铃，醒来后屋里还是安安静静的。",
    ),
    "summer": (
        "快醒前像在夏夜老树荫下听见远风和细碎虫声，睁开眼才发觉天边已经透亮。",
        "清晨醒来前只留下一片树叶影轻晃在墙面，像晚风吹过纱帘，再想细看已经淡了。",
        "半睡半醒间好像站在凉风吹过的石阶边听夏虫微鸣，醒来后屋里还是安安静静的。",
    ),
    "autumn": (
        "快醒前像走过一条落满干叶的秋巷，听见风拂过石阶的轻响，睁开眼就接不上了。",
        "清晨醒来前只留下一截低斜琥珀色的窗影，像是秋风翻动了纸页，再想细看已经模糊了。",
        "半睡半醒间好像听见巷口微凉的风声与细碎脚步，醒来后屋里还是安安静静的。",
    ),
    "winter": (
        "快醒前裹在暖被里看见霜窗外飘着一层薄雾，刚翻个身那点画面就自然淡散了。",
        "清晨醒来前只留下一圈暖灯映在薄霜玻璃上的浅影，睁开眼窗外天色才刚泛白。",
        "半睡半醒间好像听见寒晨木窗外低沉的风声，醒来后被窝里还留着安安静静的余温。",
    ),
}


def _compose_dream_fragment_cn(
    echoes: Sequence[str],
    digest: bytes,
    *,
    season: Optional[str] = None,
) -> str:
    idx = digest[4]
    if len(echoes) >= 2:
        e1, e2 = echoes[0], echoes[1]
        templates = (
            f"快醒前晃过一点零碎画面，好像先经过了{e1}，转角又莫名拿着{e2}，再睁眼就接不上了。",
            f"半梦半醒里听见像在{e1}附近的动静，桌边还放着{e2}，细想却想不起当时要去哪儿。",
            f"醒来前残留了半截念头，把{e1}和{e2}拼在了一张桌面上，刚翻个身就淡了。",
            f"清晨醒来时脑海里只剩一点和{e1}有关的影子，旁边好像搁着{e2}，前后都断开了。",
        )
        return templates[idx % len(templates)]
    if len(echoes) == 1:
        e1 = echoes[0]
        templates = (
            f"快醒时只记得一点和{e1}有关的影子，声音很轻，没等坐起来就散了大半。",
            f"天刚亮那会儿好像晃过{e1}的轮廓，前后都没头没尾，只剩一点安安静静的余温。",
            f"刚睁眼时脑子里还飘着关于{e1}的半句碎念，伸手摸杯子就记不清原话了。",
        )
        return templates[idx % len(templates)]
    norm_season = season_pulse.normalize_season(season)
    if norm_season is not None and norm_season in _SEASONAL_DREAM_FALLBACK_TEMPLATES:
        templates = _SEASONAL_DREAM_FALLBACK_TEMPLATES[norm_season]
        return templates[idx % len(templates)]
    templates = (
        "快醒前像站在一条刚下过雨的浅巷口，听见远处有人收伞，睁开眼就忘了去哪儿。",
        "清晨醒来前只留下一截很淡的窗影，像是风吹动了纸页，再想细看已经模糊了。",
        "半睡半醒间好像听见门外有轻微的脚步和车铃，醒来后屋里还是安安静静的。",
    )
    return templates[idx % len(templates)]


def settle_morning_dream_pulse(
    day_root: Path | str,
    *,
    recent_echoes: Sequence[str] = (),
    calm_quiet: bool = False,
    force_roll_seed: Optional[str] = None,
    now_iso: Optional[str] = None,
    phase: str = "just_woke",
    last_sleep_at: Optional[str] = None,
    fades_after_hour: int = 12,
    actor_side: str = "subject",
) -> dict[str, Any]:
    """Deterministic ~22% morning dream roll weaving 1-2 real recent echoes (subject/daily-loop only)."""
    _require_subject(actor_side, "settle_morning_dream_pulse")
    if not isinstance(calm_quiet, bool):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'calm_quiet' must be a boolean")
    if not isinstance(phase, str) or phase not in SLEEP_PHASES:
        raise V05InnerSparkError(400, "INVALID_FIELDS", f"phase must be one of {SLEEP_PHASES}")
    if not _is_strict_int(fades_after_hour) or not (1 <= fades_after_hour <= 23):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "fades_after_hour must be an integer between 1 and 23")
    if isinstance(recent_echoes, (str, bytes)) or not isinstance(recent_echoes, Sequence):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "recent_echoes must be a list of strings")
    if force_roll_seed is not None and not isinstance(force_roll_seed, str):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "force_roll_seed must be a string when provided")
    if last_sleep_at is not None and _parse_iso_dt(last_sleep_at) is None:
        raise V05InnerSparkError(400, "INVALID_FIELDS", f"Malformed last_sleep_at: {last_sleep_at!r}")

    cleaned_echoes: list[str] = []
    seen_echoes: set[str] = set()
    for raw_echo in recent_echoes:
        if not isinstance(raw_echo, str):
            raise V05InnerSparkError(400, "INVALID_FIELDS", "Each item in recent_echoes must be a string")
        ec = _CONTROL_CHAR_RE.sub("", raw_echo).strip()[:MAX_ECHO_LEN]
        if ec and ec not in seen_echoes:
            seen_echoes.add(ec)
            cleaned_echoes.append(ec)

    now_dt, now_str = _resolve_now_dt(now_iso, strict=True)
    date_key = now_dt.strftime("%Y-%m-%d")

    path = state_file_path(day_root)
    with _path_lock(path):
        state, _ = _read_state_zero_write(day_root)
        sw = state["sleep_wake_state"]
        sw["phase"] = phase
        if last_sleep_at is not None:
            sw["last_sleep_at"] = last_sleep_at.strip()
        elif phase in ("asleep", "winding_down"):
            sw["last_sleep_at"] = now_str
        if phase in ("just_woke", "awake"):
            sw["last_wake_at"] = now_str

        if sw.get("last_dream_roll_date") == date_key:
            _commit_state(day_root, state, now_str)
            return {
                "action": "settle_morning_dream_pulse",
                "date_key": date_key,
                "dream_occurred": False,
                "already_rolled_today": True,
                "suppressed": True,
                "suppression_reason": "already_rolled_today",
                "morning_dream": copy.deepcopy(sw.get("morning_dream")),
                "revision": state["revision"],
                "llm_calls": 0,
            }

        sw["last_dream_roll_date"] = date_key
        if calm_quiet:
            sw["morning_dream"] = None
            _commit_state(day_root, state, now_str)
            return {
                "action": "settle_morning_dream_pulse",
                "date_key": date_key,
                "dream_occurred": False,
                "already_rolled_today": False,
                "suppressed": True,
                "suppression_reason": "calm_quiet",
                "morning_dream": None,
                "revision": state["revision"],
                "llm_calls": 0,
            }

        seed_text = (
            force_roll_seed
            if force_roll_seed is not None
            else f"nyairo.v05.dream|{date_key}|{'|'.join(cleaned_echoes)}"
        )
        digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
        roll_bps = int.from_bytes(digest[:4], "big") % 10000

        if roll_bps < DREAM_CHANCE_BPS:
            if len(cleaned_echoes) <= 2:
                chosen_echoes = list(cleaned_echoes)
            else:
                i1 = digest[5] % len(cleaned_echoes)
                i2 = (i1 + 1 + (digest[6] % (len(cleaned_echoes) - 1))) % len(cleaned_echoes)
                chosen_echoes = [cleaned_echoes[i1], cleaned_echoes[i2]]

            pulse = _resolve_season_pulse_zero_write(day_root, now_dt)
            dream_slip: dict[str, Any] = {
                "dream_id": f"drm_{date_key.replace('-', '')}_{digest.hex()[:8]}",
                "date_key": date_key,
                "fragment_cn": _compose_dream_fragment_cn(
                    chosen_echoes,
                    digest,
                    season=str(pulse["season"]),
                ),
                "source_echoes": chosen_echoes,
                "fades_after_hour": int(fades_after_hour),
                "kept_in_notebook": False,
                "shared_with_partner": False,
                "created_at": now_str,
            }
            sw["morning_dream"] = dream_slip
            _commit_state(day_root, state, now_str)
            dream_copy = copy.deepcopy(dream_slip)
            dream_rev = state["revision"]
            dream_hit = True
        else:
            sw["morning_dream"] = None
            _commit_state(day_root, state, now_str)
            dream_copy = None
            dream_rev = state["revision"]
            dream_hit = False

    if dream_hit and isinstance(dream_copy, dict):
        did = str(dream_copy["dream_id"])
        frag = str(dream_copy.get("fragment_cn") or "").strip()
        sink_receipt = _emit_inner_spark_lived_episode(
            day_root,
            episode_key=f"v05_dream_desire:dream:{did}",
            topic=f"清晨残留梦境（{date_key}）",
            exact_detail=frag or f"{date_key}清晨残留的一小段梦影",
            gist_summary=f"{date_key}清晨做了一个轻浅的梦：{frag[:80]}",
            occurred_at=now_str,
            emotional_salience=0.56,
            affect_kind="affect_pulse",
            affect_intensity=42,
            shared=bool(dream_copy.get("shared_with_partner", False)),
            source_refs=(("dream_id", did),),
        )
        return {
            "action": "settle_morning_dream_pulse",
            "date_key": date_key,
            "dream_occurred": True,
            "already_rolled_today": False,
            "suppressed": False,
            "roll_bps": roll_bps,
            "threshold_bps": DREAM_CHANCE_BPS,
            "morning_dream": dream_copy,
            "memory_sink": sink_receipt,
            "revision": dream_rev,
            "llm_calls": 0,
        }

    return {
        "action": "settle_morning_dream_pulse",
        "date_key": date_key,
        "dream_occurred": False,
        "already_rolled_today": False,
        "suppressed": False,
        "suppression_reason": "quiet_night_no_dream",
        "roll_bps": roll_bps,
        "threshold_bps": DREAM_CHANCE_BPS,
        "morning_dream": None,
        "revision": dream_rev,
        "llm_calls": 0,
    }


def keep_or_share_dream(
    day_root: Path | str,
    *,
    kept_in_notebook: Optional[bool] = None,
    shared_with_partner: Optional[bool] = None,
    dream_id: Optional[str] = None,
    now_iso: Optional[str] = None,
    actor_side: str = "subject",
) -> dict[str, Any]:
    """Allow TA to keep a morning dream in the notebook and/or share it with partner (subject-only)."""
    _require_subject(actor_side, "keep_or_share_dream")
    if kept_in_notebook is None and shared_with_partner is None:
        raise V05InnerSparkError(
            400,
            "INVALID_FIELDS",
            "Provide at least one of 'kept_in_notebook' or 'shared_with_partner'",
        )
    if kept_in_notebook is not None and not isinstance(kept_in_notebook, bool):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'kept_in_notebook' must be a boolean")
    if shared_with_partner is not None and not isinstance(shared_with_partner, bool):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'shared_with_partner' must be a boolean")
    clean_dream_id: Optional[str] = None
    if dream_id is not None:
        clean_dream_id = _clean_text(dream_id, field_name="dream_id", max_len=64)
    _, now_str = _resolve_now_dt(now_iso, strict=True)

    path = state_file_path(day_root)
    with _path_lock(path):
        state, _ = _read_state_zero_write(day_root)
        morning = state["sleep_wake_state"].get("morning_dream")
        notebook: list[dict[str, Any]] = state["dream_notebook"]

        target: Optional[dict[str, Any]] = None
        if isinstance(morning, dict) and (clean_dream_id is None or morning.get("dream_id") == clean_dream_id):
            target = morning
        elif clean_dream_id is not None:
            for item in notebook:
                if item.get("dream_id") == clean_dream_id:
                    target = item
                    break

        if target is None:
            raise V05InnerSparkError(404, "NO_MORNING_DREAM", "No matching morning dream to keep or share")

        if kept_in_notebook is not None:
            target["kept_in_notebook"] = kept_in_notebook
        if shared_with_partner is not None:
            target["shared_with_partner"] = shared_with_partner
        target["updated_at"] = now_str

        # Keep morning_dream and dream_notebook synchronized
        tid = target.get("dream_id")
        if isinstance(morning, dict) and morning.get("dream_id") == tid:
            morning["kept_in_notebook"] = bool(target.get("kept_in_notebook"))
            morning["shared_with_partner"] = bool(target.get("shared_with_partner"))
            morning["updated_at"] = now_str

        if bool(target.get("kept_in_notebook")):
            replaced = False
            for idx, entry in enumerate(notebook):
                if entry.get("dream_id") == tid:
                    notebook[idx] = copy.deepcopy(target)
                    replaced = True
                    break
            if not replaced:
                notebook.append(copy.deepcopy(target))
            state["dream_notebook"] = notebook[-MAX_KEPT_DREAMS:]
        else:
            state["dream_notebook"] = [entry for entry in notebook if entry.get("dream_id") != tid]

        _commit_state(day_root, state, now_str)
        dream_copy = copy.deepcopy(target)
        rev_after = state["revision"]

    sink_receipt: Optional[dict[str, Any]] = None
    if shared_with_partner is True and bool(dream_copy.get("shared_with_partner")):
        did = str(dream_copy.get("dream_id") or "")
        frag = str(dream_copy.get("fragment_cn") or "").strip()
        if did:
            sink_receipt = _emit_inner_spark_lived_episode(
                day_root,
                episode_key=f"v05_dream_desire:dream_shared:{did}",
                topic="主动分享清晨梦境",
                exact_detail=f"把清晨记得的梦影分享了出去：{frag}",
                gist_summary=f"分享了一段清晨梦境：{frag[:80]}",
                occurred_at=now_str,
                emotional_salience=0.58,
                affect_kind="affect_pulse",
                affect_intensity=44,
                shared=True,
                source_refs=(("dream_id", did),),
            )

    out_resp: dict[str, Any] = {
        "action": "keep_or_share_dream",
        "dream": dream_copy,
        "revision": rev_after,
        "llm_calls": 0,
    }
    if sink_receipt is not None:
        out_resp["memory_sink"] = sink_receipt
    return out_resp


# ---------------------------------------------------------------------------
# 3. Time Folds: Delayed Page Foreshadowing (Ch. 109, ForeshadowBook)
# ---------------------------------------------------------------------------


def _build_fabric_book(book_rec: Mapping[str, Any]) -> world_fabric.ForeshadowBook:
    fb = world_fabric.ForeshadowBook(
        total_pages=int(book_rec.get("total_pages", 60)),
        current_page=int(book_rec.get("current_page", 1)),
    )
    for note in book_rec.get("tucked_notes", []):
        if isinstance(note, Mapping) and not bool(note.get("revealed", False)):
            fb.tucked.append(
                world_fabric.TuckedPageNote(
                    target_page=int(note["target_page"]),
                    author=str(note["author_side"]),
                    inserted_hour=float(note.get("inserted_hour", 0.0)),
                    item_kind=str(note.get("item_kind", "note")),
                    note_text_cn=str(note.get("note_text", "")),
                )
            )
    return fb


def _advance_book_in_memory(
    book_rec: dict[str, Any],
    *,
    new_page: int,
    now_epoch_hour: float,
    now_str: str,
    actor_side: str,
) -> tuple[int, list[dict[str, Any]]]:
    prev_page = int(book_rec.get("current_page", 1))
    unread_records = [
        dict(n) for n in book_rec.get("tucked_notes", []) if isinstance(n, Mapping) and not bool(n.get("revealed", False))
    ]
    fb = _build_fabric_book(book_rec)
    revealed_raw = fb.turn_to_page(new_page, now_epoch_hour)

    newly_revealed: list[dict[str, Any]] = []
    remaining_records: list[dict[str, Any]] = []
    raw_idx = 0
    for rec in unread_records:
        if fb.current_page >= int(rec["target_page"]) and raw_idx < len(revealed_raw):
            raw_item = revealed_raw[raw_idx]
            raw_idx += 1
            newly_revealed.append({
                "note_id": rec["note_id"],
                "target_page": int(raw_item["page"]),
                "author_side": str(raw_item["author"]),
                "item_kind": str(rec.get("item_kind", "note")),
                "note_text": str(raw_item.get("note_text_cn", "")),
                "state_cn": str(raw_item.get("state_cn", "夹页便签")),
                "elapsed_hours": round(float(raw_item.get("elapsed_hours", 0.0)), 2),
                "inserted_at": rec.get("inserted_at"),
                "revealed_at": now_str,
                "revealed_by_side": actor_side,
                "revealed": True,
                "status": "revealed",
            })
        else:
            remaining_records.append(rec)

    book_rec["current_page"] = fb.current_page
    book_rec["tucked_notes"] = remaining_records
    existing_revealed = list(book_rec.get("revealed_notes", []))
    book_rec["revealed_notes"] = (existing_revealed + newly_revealed)[-MAX_REVEALED_NOTES_PER_BOOK:]
    book_rec["updated_at"] = now_str
    return prev_page, newly_revealed


def tuck_page_note(
    day_root: Path | str,
    *,
    book_title: str,
    target_page: int,
    note_text: str = "",
    item_kind: str = "note",
    total_pages: Optional[int] = None,
    current_page: Optional[int] = None,
    now_iso: Optional[str] = None,
    actor_side: str = "partner",
) -> dict[str, Any]:
    """Tuck a delayed foreshadow note/leaf into a future unread page via ForeshadowBook."""
    norm_actor = _normalize_actor_side(actor_side)
    clean_title = _clean_text(book_title, field_name="book_title", max_len=MAX_BOOK_TITLE_LEN)
    if not _is_strict_int(target_page):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'target_page' must be an integer")
    if not isinstance(item_kind, str) or item_kind not in BOOK_ITEM_KINDS:
        raise V05InnerSparkError(400, "INVALID_FIELDS", f"item_kind must be one of {BOOK_ITEM_KINDS}")
    allow_empty_note = item_kind != "note"
    clean_note = _clean_text(
        note_text,
        field_name="note_text",
        max_len=MAX_FORESHADOW_NOTE_LEN,
        allow_empty=allow_empty_note,
    )
    if total_pages is not None and (not _is_strict_int(total_pages) or not (2 <= total_pages <= 2000)):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "total_pages must be an integer between 2 and 2000")
    if current_page is not None and (not _is_strict_int(current_page) or current_page < 1):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "current_page must be an integer >= 1")

    now_dt, now_str = _resolve_now_dt(now_iso, strict=True)
    now_epoch_h = _epoch_hours(now_dt)

    path = state_file_path(day_root)
    with _path_lock(path):
        state, _ = _read_state_zero_write(day_root)
        books: dict[str, dict[str, Any]] = state["foreshadow_books"]
        book_rec = books.get(clean_title)
        if book_rec is None:
            if len(books) >= MAX_FORESHADOW_BOOKS:
                raise V05InnerSparkError(409, "BOOKSHELF_FULL", "Maximum foreshadow books reached")
            init_total = int(total_pages) if total_pages is not None else 60
            init_cur = int(current_page) if current_page is not None else 1
            if init_cur > init_total:
                raise V05InnerSparkError(400, "INVALID_FIELDS", "current_page cannot exceed total_pages")
            book_rec = {
                "book_title": clean_title,
                "total_pages": init_total,
                "current_page": init_cur,
                "tucked_notes": [],
                "revealed_notes": [],
                "created_at": now_str,
                "updated_at": now_str,
            }
        else:
            book_rec = copy.deepcopy(book_rec)

        fb = _build_fabric_book(book_rec)
        try:
            fb.tuck_into_unread_page(
                target_page=target_page,
                author=norm_actor,
                now_hour=now_epoch_h,
                item_kind=item_kind,
                note_text_cn=clean_note,
            )
        except ValueError as exc:
            msg = str(exc)
            if "最多同时藏" in msg:
                raise V05InnerSparkError(409, "MAX_TUCKED_NOTES_REACHED", msg) from exc
            raise V05InnerSparkError(400, "INVALID_TARGET_PAGE", msg) from exc

        seq = int(state.get("revision", 0)) + 1
        note_id = _stable_id("fsh", seq, clean_title, target_page, norm_actor, now_str)
        note_record: dict[str, Any] = {
            "note_id": note_id,
            "target_page": target_page,
            "author_side": norm_actor,
            "inserted_at": now_str,
            "inserted_hour": now_epoch_h,
            "item_kind": item_kind,
            "note_text": clean_note,
            "revealed": False,
        }
        book_rec["tucked_notes"].append(note_record)
        book_rec["updated_at"] = now_str
        books[clean_title] = book_rec
        _commit_state(day_root, state, now_str)
        return {
            "action": "tuck_page_note",
            "book_title": clean_title,
            "note_id": note_id,
            "target_page": target_page,
            "item_kind": item_kind,
            "author_side": norm_actor,
            "status": "waiting_for_page",
            "current_page": book_rec["current_page"],
            "total_pages": book_rec["total_pages"],
            "revision": state["revision"],
            "llm_calls": 0,
        }


def advance_reading_page(
    day_root: Path | str,
    *,
    book_title: str,
    new_page: int,
    total_pages: Optional[int] = None,
    now_iso: Optional[str] = None,
    actor_side: str = "partner",
) -> dict[str, Any]:
    """Advance reading page of a book via ForeshadowBook.turn_to_page and reveal any reached notes."""
    norm_actor = _normalize_actor_side(actor_side)
    clean_title = _clean_text(book_title, field_name="book_title", max_len=MAX_BOOK_TITLE_LEN)
    if not _is_strict_int(new_page) or new_page < 1:
        raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'new_page' must be an integer >= 1")
    if total_pages is not None and (not _is_strict_int(total_pages) or not (2 <= total_pages <= 2000)):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "total_pages must be an integer between 2 and 2000")

    now_dt, now_str = _resolve_now_dt(now_iso, strict=True)
    now_epoch_h = _epoch_hours(now_dt)

    path = state_file_path(day_root)
    with _path_lock(path):
        state, _ = _read_state_zero_write(day_root)
        books: dict[str, dict[str, Any]] = state["foreshadow_books"]
        book_rec = books.get(clean_title)
        if book_rec is None:
            if len(books) >= MAX_FORESHADOW_BOOKS:
                raise V05InnerSparkError(409, "BOOKSHELF_FULL", "Maximum foreshadow books reached")
            init_total = int(total_pages) if total_pages is not None else max(60, new_page)
            book_rec = {
                "book_title": clean_title,
                "total_pages": init_total,
                "current_page": 1,
                "tucked_notes": [],
                "revealed_notes": [],
                "created_at": now_str,
                "updated_at": now_str,
            }
        else:
            book_rec = copy.deepcopy(book_rec)

        if new_page > int(book_rec["total_pages"]):
            raise V05InnerSparkError(
                400,
                "PAGE_OUT_OF_RANGE",
                f"new_page ({new_page}) exceeds total_pages ({book_rec['total_pages']})",
            )

        prev_page, newly_revealed = _advance_book_in_memory(
            book_rec,
            new_page=new_page,
            now_epoch_hour=now_epoch_h,
            now_str=now_str,
            actor_side=norm_actor,
        )
        books[clean_title] = book_rec
        _commit_state(day_root, state, now_str)
        return {
            "action": "advance_reading_page",
            "book_title": clean_title,
            "previous_page": prev_page,
            "current_page": book_rec["current_page"],
            "total_pages": book_rec["total_pages"],
            "newly_revealed_notes": copy.deepcopy(newly_revealed),
            "newly_revealed_count": len(newly_revealed),
            "revision": state["revision"],
            "llm_calls": 0,
        }


# ---------------------------------------------------------------------------
# 4A. Window Frost Sketch & Live Zero-Write Melt Projection (WindowFrostCanvas)
# ---------------------------------------------------------------------------


def _infer_window_misty(
    *,
    weather: str,
    temp_c: float,
    humidity_hint: str,
    season: Optional[str] = None,
) -> bool:
    w = weather.strip().lower()
    h = humidity_hint.strip().lower()
    if h in _DRY_HUMIDITY_HINTS and temp_c >= 18.0:
        return False
    if h in _MISTY_HUMIDITY_HINTS:
        return True
    if w in _MISTY_WEATHERS:
        return True
    norm_season = season_pulse.normalize_season(season)
    threshold = (
        float(season_pulse.SEASON_MICROCLIMATE_PROFILES[norm_season]["frost_temp_threshold_c"])
        if norm_season is not None
        else 14.0
    )
    return temp_c <= threshold


def _synthesize_strokes(text: str) -> list[tuple[float, float]]:
    count = min(30, max(2, len(text) * 2))
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    points: list[tuple[float, float]] = []
    for idx in range(count):
        x = round(0.1 + 0.8 * (idx / max(1, count - 1)), 3)
        y = round(0.25 + 0.5 * (digest[idx % len(digest)] / 255.0), 3)
        points.append((x, y))
    return points


def _compute_sun_warm_hours(
    start_dt: datetime,
    end_dt: datetime,
    *,
    weather: str,
    sunrise_min: Optional[int] = None,
    sunset_min: Optional[int] = None,
) -> float:
    if end_dt <= start_dt:
        return 0.0
    if weather.strip().lower() in _OVERCAST_WEATHERS:
        return 0.0
    if sunrise_min is not None and sunset_min is not None:
        warm_start_min = min(12 * 60, int(sunrise_min) + 120)
        warm_end_min = max(warm_start_min + 60, int(sunset_min) - 90)
    else:
        warm_start_min = 9 * 60
        warm_end_min = 16 * 60
    cur = _to_default_tz(start_dt)
    limit = min(_to_default_tz(end_dt), cur + timedelta(hours=48))
    sun_seconds = 0.0
    day_cursor = cur.replace(hour=0, minute=0, second=0, microsecond=0)
    while day_cursor <= limit:
        win_start = day_cursor + timedelta(minutes=warm_start_min)
        win_end = day_cursor + timedelta(minutes=warm_end_min)
        ov_start = max(cur, win_start)
        ov_end = min(limit, win_end)
        if ov_end > ov_start:
            sun_seconds += (ov_end - ov_start).total_seconds()
        day_cursor += timedelta(days=1)
    return sun_seconds / 3600.0


def project_window_frost(
    frost_rec: Optional[Mapping[str, Any]],
    now_dt: datetime,
    *,
    season: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Pure zero-write projection of window frost visibility & WindowFrostCanvas phase."""
    if not isinstance(frost_rec, Mapping):
        return None
    created_dt = _parse_iso_dt(frost_rec.get("created_at"))
    if created_dt is None:
        return None
    elapsed_hours = max(0.0, (now_dt.astimezone(timezone.utc) - created_dt.astimezone(timezone.utc)).total_seconds() / 3600.0)
    weather = str(frost_rec.get("weather") or "breezy_cool")
    temp_c = float(frost_rec.get("temp_c", 8.0))
    humidity_hint = str(frost_rec.get("humidity_hint") or "misty")

    eff_season = (
        season_pulse.normalize_season(season)
        or season_pulse.normalize_season(frost_rec.get("season"))
        or season_pulse.resolve_effective_season(now_dt)[0]
    )
    pulse = season_pulse.project_season_solar_pulse(
        now_iso=now_dt,
        explicit_season=eff_season,
        weather=weather,
    )
    micro = pulse["microclimate"]
    frost_melt_multiplier = float(micro["frost_melt_multiplier"])

    base_rate = 0.22
    if temp_c >= 16.0:
        base_rate += 0.18
    elif temp_c >= 10.0:
        base_rate += 0.08
    elif temp_c <= 4.0:
        base_rate -= 0.07

    hum_low = humidity_hint.strip().lower()
    if hum_low in _MISTY_HUMIDITY_HINTS:
        base_rate -= 0.04
    elif hum_low in _DRY_HUMIDITY_HINTS:
        base_rate += 0.14
    base_rate = max(0.08, base_rate)

    sun_warm_hours = _compute_sun_warm_hours(
        created_dt,
        now_dt,
        weather=weather,
        sunrise_min=int(micro["sunrise_min"]),
        sunset_min=int(micro["sunset_min"]),
    )
    melt_progress = min(1.0, (elapsed_hours * base_rate + sun_warm_hours * 0.25) * frost_melt_multiplier)
    visibility_ratio = round(max(0.0, 1.0 - melt_progress), 3)
    melted = visibility_ratio <= FROST_MELTED_THRESHOLD

    canvas = world_fabric.WindowFrostCanvas()
    raw_strokes = frost_rec.get("strokes", [])
    restored_strokes: list[tuple[float, float]] = []
    if isinstance(raw_strokes, list):
        for pt in raw_strokes[:30]:
            if isinstance(pt, (list, tuple)) and len(pt) == 2 and _is_strict_number(pt[0]) and _is_strict_number(pt[1]):
                restored_strokes.append((float(pt[0]), float(pt[1])))
    canvas.strokes = restored_strokes
    canvas.phase = str(frost_rec.get("canvas_phase") or "FRESH")
    canvas.latent_since_hour = float(frost_rec.get("latent_since_hour", 0.0))

    now_epoch_h = _epoch_hours(now_dt)
    latent_echo_opacity = 0.0
    if melted:
        canvas.on_warm_clear(now_epoch_h)
        # Check whether a future mist return could briefly echo the strokes (within 36h window)
        echo_probe = world_fabric.WindowFrostCanvas()
        echo_probe.strokes = list(canvas.strokes)
        echo_probe.phase = canvas.phase
        echo_probe.latent_since_hour = float(frost_rec.get("created_epoch_hour", now_epoch_h))
        echo_result = echo_probe.on_mist_return(now_epoch_h)
        if echo_result.get("visible"):
            latent_echo_opacity = float(echo_result.get("opacity", 0.0))
        else:
            canvas.strokes = []
            canvas.phase = "NONE"

    if melted:
        status_cn = "窗雾已化开"
    elif visibility_ratio >= 0.65:
        status_cn = "雾痕清晰"
    elif visibility_ratio >= 0.25:
        status_cn = "水汽渐散"
    else:
        status_cn = "只剩浅浅水痕"

    return {
        "frost_id": str(frost_rec.get("frost_id", "")),
        "created_at": str(frost_rec.get("created_at", "")),
        "author_side": str(frost_rec.get("author_side", "partner")),
        "text": "" if melted else str(frost_rec.get("text", "")),
        "humidity_hint": humidity_hint,
        "weather": weather,
        "temp_c": round(temp_c, 1),
        "season": eff_season,
        "frost_melt_multiplier": round(frost_melt_multiplier, 2),
        "solar_term_name_cn": str(pulse["solar_term"]["term_name_cn"]),
        "elapsed_hours": round(elapsed_hours, 2),
        "sun_warm_hours": round(sun_warm_hours, 2),
        "visibility_ratio": visibility_ratio,
        "melted": melted,
        "canvas_phase": canvas.phase,
        "latent_echo_opacity": round(latent_echo_opacity, 2),
        "point_count": len(canvas.strokes),
        "status_cn": status_cn,
    }


def sketch_window_frost(
    day_root: Path | str,
    *,
    text: str,
    weather: str = "breezy_cool",
    temp_c: float = 8.0,
    humidity_hint: str = "misty",
    strokes: Optional[Sequence[Sequence[float]]] = None,
    window_misty: Optional[bool] = None,
    now_iso: Optional[str] = None,
    actor_side: str = "partner",
) -> dict[str, Any]:
    """Write a short <=40-char note/doodle on the misty windowpane backed by WindowFrostCanvas."""
    norm_actor = _normalize_actor_side(actor_side)
    clean_text = _clean_text(text, field_name="text", max_len=MAX_WINDOW_FROST_TEXT_LEN)
    clean_weather = _clean_text(weather, field_name="weather", max_len=32)
    clean_humidity = _clean_text(humidity_hint, field_name="humidity_hint", max_len=32)
    if not _is_strict_number(temp_c) or not (-40.0 <= float(temp_c) <= 55.0):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "temp_c must be a number between -40 and 55")
    if window_misty is not None and not isinstance(window_misty, bool):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "window_misty must be a boolean")

    now_dt, now_str = _resolve_now_dt(now_iso, strict=True)
    pulse = _resolve_season_pulse_zero_write(day_root, now_dt)
    eff_season = str(pulse["season"])

    eff_misty = (
        window_misty
        if window_misty is not None
        else _infer_window_misty(
            weather=clean_weather,
            temp_c=float(temp_c),
            humidity_hint=clean_humidity,
            season=eff_season,
        )
    )

    points: list[tuple[float, float]] = []
    if strokes is not None:
        if isinstance(strokes, (str, bytes)) or not isinstance(strokes, Sequence):
            raise V05InnerSparkError(400, "INVALID_FIELDS", "strokes must be a list of [x, y] pairs")
        for pt in strokes[:30]:
            if (
                not isinstance(pt, (list, tuple))
                or len(pt) != 2
                or not _is_strict_number(pt[0])
                or not _is_strict_number(pt[1])
            ):
                raise V05InnerSparkError(400, "INVALID_FIELDS", "Each stroke point must be [x, y] numbers")
            points.append((round(float(pt[0]), 3), round(float(pt[1]), 3)))
    if not points:
        points = _synthesize_strokes(clean_text)

    canvas = world_fabric.WindowFrostCanvas()
    try:
        canvas.draw_on_mist(points, window_misty=eff_misty)
    except ValueError as exc:
        raise V05InnerSparkError(409, "WINDOW_NOT_MISTY", str(exc)) from exc

    now_epoch_h = _epoch_hours(now_dt)

    path = state_file_path(day_root)
    with _path_lock(path):
        state, _ = _read_state_zero_write(day_root)
        seq = int(state.get("revision", 0)) + 1
        frost_rec: dict[str, Any] = {
            "frost_id": _stable_id("frst", seq, norm_actor, clean_text, now_str),
            "created_at": now_str,
            "created_epoch_hour": now_epoch_h,
            "author_side": norm_actor,
            "text": clean_text,
            "weather": clean_weather,
            "temp_c": round(float(temp_c), 1),
            "humidity_hint": clean_humidity,
            "season": eff_season,
            "strokes": [[pt[0], pt[1]] for pt in canvas.strokes],
            "canvas_phase": canvas.phase,
            "latent_since_hour": canvas.latent_since_hour,
        }
        state["window_frost"] = frost_rec
        _commit_state(day_root, state, now_str)
        projected = project_window_frost(frost_rec, now_dt, season=eff_season)
        return {
            "action": "sketch_window_frost",
            "window_frost": projected,
            "revision": state["revision"],
            "llm_calls": 0,
        }


# ---------------------------------------------------------------------------
# 4B. Zero-LLM Object Chemistry & Half-Tangerine Peace Offering (Ch. 111, 114)
# ---------------------------------------------------------------------------


_CHEMISTRY_ALLOWED_PARAMS: dict[str, frozenset[str]] = {
    "same_bag_condensation": frozenset({"bag_items", "elapsed_minutes"}),
    "hot_mug_ironing": frozenset({
        "paper_moisture",
        "paper_posture",
        "mug_thermal",
        "mug_cleanliness",
        "stacked_minutes",
    }),
    "window_breeze_flip": frozenset({
        "book_open",
        "reading_page",
        "has_tucked_bookmark",
        "stacked_weight_items",
        "window_draft",
        "book_title",
    }),
    "flyer_shoe_drying": frozenset({
        "shoe_moisture",
        "stuffed_folded_dry_paper",
        "elapsed_minutes",
    }),
    "half_tangerine_offering": frozenset({
        "refusal_lock_active",
        "quiet_minutes_since_friction",
        "days_since_last_peace_gesture",
        "kitchen_has_fruit",
    }),
}


def trigger_object_chemistry(
    day_root: Path | str,
    *,
    kind: str,
    params: Optional[Mapping[str, Any]] = None,
    now_iso: Optional[str] = None,
    actor_side: str = "partner",
) -> dict[str, Any]:
    """Execute and persist one of the 5 real v05_unified_world_fabric zero-LLM physics outcomes."""
    norm_actor = _normalize_actor_side(actor_side)
    if not isinstance(kind, str) or kind not in OBJECT_CHEMISTRY_KINDS:
        raise V05InnerSparkError(
            400,
            "INVALID_CHEMISTRY_KIND",
            f"kind must be one of {OBJECT_CHEMISTRY_KINDS}",
        )
    if params is None:
        cfg: dict[str, Any] = {}
    elif isinstance(params, Mapping):
        cfg = dict(params)
    else:
        raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'params' must be an object")

    unknown_params = sorted(set(cfg.keys()) - _CHEMISTRY_ALLOWED_PARAMS[kind])
    if unknown_params:
        raise V05InnerSparkError(
            400,
            "INVALID_FIELDS",
            f"Unknown params for {kind!r}: {unknown_params}",
        )

    now_dt, now_str = _resolve_now_dt(now_iso, strict=True)
    now_epoch_h = _epoch_hours(now_dt)

    path = state_file_path(day_root)
    with _path_lock(path):
        state, _ = _read_state_zero_write(day_root)
        inputs_recorded: dict[str, Any]
        outcome: dict[str, Any]
        summary_cn: str

        if kind == "same_bag_condensation":
            raw_items = cfg.get("bag_items")
            if raw_items is None:
                bag_items: list[dict[str, Any]] = [
                    {"name": "热咖啡", "thermal": "hot"},
                    {"name": "冰牛奶", "thermal": "cold"},
                    {"name": "纸质收据", "paper_like": True, "wrapped_in_sleeve": False},
                ]
            else:
                if not isinstance(raw_items, list) or len(raw_items) == 0 or len(raw_items) > 12:
                    raise V05InnerSparkError(400, "INVALID_FIELDS", "bag_items must be a non-empty list (max 12)")
                bag_items = []
                for elem in raw_items:
                    if not isinstance(elem, Mapping):
                        raise V05InnerSparkError(400, "INVALID_FIELDS", "Each bag_item must be a JSON object")
                    bag_items.append(copy.deepcopy(dict(elem)))
            elapsed_minutes = cfg.get("elapsed_minutes", 20.0)
            if not _is_strict_number(elapsed_minutes) or float(elapsed_minutes) < 0.0:
                raise V05InnerSparkError(400, "INVALID_FIELDS", "elapsed_minutes must be a non-negative number")
            settled = world_fabric.settle_same_bag_thermal_condensation(bag_items, float(elapsed_minutes))
            smudged = [
                str(it.get("name") or it.get("id") or f"item_{idx}")
                for idx, it in enumerate(settled)
                if it.get("note_legibility") == "smudged"
            ]
            condensation_occurred = settled != bag_items
            inputs_recorded = {"bag_items": bag_items, "elapsed_minutes": float(elapsed_minutes)}
            outcome = {
                "bag_items": settled,
                "condensation_occurred": condensation_occurred,
                "smudged_items": smudged,
            }
            summary_cn = (
                "同袋冷热贴着放久了，外壁起了水汽，未套袋的纸角被洇潮了。"
                if smudged
                else ("同袋冷热互相走了温，袋壁挂起一层轻水珠。" if condensation_occurred else "时间还短，袋里各样物件都还保持原样。")
            )

        elif kind == "hot_mug_ironing":
            paper_moisture = str(cfg.get("paper_moisture", "dry"))
            paper_posture = str(cfg.get("paper_posture", "creased"))
            mug_thermal = str(cfg.get("mug_thermal", "hot"))
            mug_cleanliness = str(cfg.get("mug_cleanliness", "clean"))
            stacked_minutes = cfg.get("stacked_minutes", 15.0)
            if not _is_strict_number(stacked_minutes) or float(stacked_minutes) < 0.0:
                raise V05InnerSparkError(400, "INVALID_FIELDS", "stacked_minutes must be a non-negative number")
            settled = world_fabric.settle_hot_mug_ironing_paper(
                paper_moisture=paper_moisture,
                paper_posture=paper_posture,
                mug_thermal=mug_thermal,
                mug_cleanliness=mug_cleanliness,
                stacked_minutes=float(stacked_minutes),
            )
            ironed_flat = settled.get("posture") == "neat" and settled.get("legibility") == "clear"
            inputs_recorded = {
                "paper_moisture": paper_moisture,
                "paper_posture": paper_posture,
                "mug_thermal": mug_thermal,
                "mug_cleanliness": mug_cleanliness,
                "stacked_minutes": float(stacked_minutes),
            }
            outcome = dict(settled)
            outcome["ironed_flat"] = ironed_flat
            if settled.get("legibility") == "smudged":
                summary_cn = "湿纸还没干透就被热杯底压着，字迹化开了一小片。"
            elif ironed_flat and settled.get("cleanliness") == "soiled":
                summary_cn = "热杯底把折皱压平了，但杯底留下一圈浅浅茶渍。"
            elif ironed_flat:
                summary_cn = "热杯底借着余温把皱纸压平了，纸面清清爽爽。"
            else:
                summary_cn = "压的时间或热度还不够，纸面折痕基本没变。"

        elif kind == "window_breeze_flip":
            book_open = cfg.get("book_open", True)
            has_tucked_bookmark = cfg.get("has_tucked_bookmark", False)
            stacked_weight_items = cfg.get("stacked_weight_items", [])
            window_draft = str(cfg.get("window_draft", "breeze"))
            book_title_raw = cfg.get("book_title")
            if not isinstance(book_open, bool) or not isinstance(has_tucked_bookmark, bool):
                raise V05InnerSparkError(400, "INVALID_FIELDS", "book_open and has_tucked_bookmark must be booleans")
            if not isinstance(stacked_weight_items, list) or not all(isinstance(x, str) for x in stacked_weight_items):
                raise V05InnerSparkError(400, "INVALID_FIELDS", "stacked_weight_items must be a list of strings")

            clean_book_title: Optional[str] = None
            book_rec: Optional[dict[str, Any]] = None
            if book_title_raw is not None:
                clean_book_title = _clean_text(book_title_raw, field_name="book_title", max_len=MAX_BOOK_TITLE_LEN)
                existing_book = state["foreshadow_books"].get(clean_book_title)
                if isinstance(existing_book, dict):
                    book_rec = copy.deepcopy(existing_book)

            default_page = int(book_rec["current_page"]) if book_rec is not None else 10
            reading_page = cfg.get("reading_page", default_page)
            if not _is_strict_int(reading_page) or reading_page < 1:
                raise V05InnerSparkError(400, "INVALID_FIELDS", "reading_page must be an integer >= 1")

            after_page = world_fabric.settle_window_breeze_page_flip(
                book_open=book_open,
                reading_page=reading_page,
                has_tucked_bookmark=has_tucked_bookmark,
                stacked_weight_items=list(stacked_weight_items),
                window_draft=window_draft,
            )

            newly_revealed_notes: list[dict[str, Any]] = []
            if book_rec is not None and clean_book_title is not None:
                clamped_after = min(int(book_rec["total_pages"]), after_page)
                after_page = clamped_after
                if clamped_after > int(book_rec["current_page"]):
                    _, newly_revealed_notes = _advance_book_in_memory(
                        book_rec,
                        new_page=clamped_after,
                        now_epoch_hour=now_epoch_h,
                        now_str=now_str,
                        actor_side=norm_actor,
                    )
                    state["foreshadow_books"][clean_book_title] = book_rec

            pages_flipped = max(0, after_page - reading_page)
            inputs_recorded = {
                "book_open": book_open,
                "reading_page": reading_page,
                "has_tucked_bookmark": has_tucked_bookmark,
                "stacked_weight_items": list(stacked_weight_items),
                "window_draft": window_draft,
                "book_title": clean_book_title,
            }
            outcome = {
                "before_page": reading_page,
                "after_page": after_page,
                "pages_flipped": pages_flipped,
                "held_steady": pages_flipped == 0,
                "newly_revealed_notes": copy.deepcopy(newly_revealed_notes),
            }
            if pages_flipped > 0:
                summary_cn = f"穿堂风拂过摊开的书页，顺势往前翻了 {pages_flipped} 页。"
            else:
                summary_cn = "书页被稳稳压着或窗边无风，停在原来的那一页没动。"

        elif kind == "flyer_shoe_drying":
            shoe_moisture = str(cfg.get("shoe_moisture", "wet"))
            stuffed_folded_dry_paper = cfg.get("stuffed_folded_dry_paper", True)
            elapsed_minutes = cfg.get("elapsed_minutes", 120.0)
            if not isinstance(stuffed_folded_dry_paper, bool):
                raise V05InnerSparkError(400, "INVALID_FIELDS", "stuffed_folded_dry_paper must be a boolean")
            if not _is_strict_number(elapsed_minutes) or float(elapsed_minutes) < 0.0:
                raise V05InnerSparkError(400, "INVALID_FIELDS", "elapsed_minutes must be a non-negative number")
            settled = world_fabric.settle_crumpled_flyer_shoe_drying(
                shoe_moisture=shoe_moisture,
                stuffed_folded_dry_paper=stuffed_folded_dry_paper,
                elapsed_minutes=float(elapsed_minutes),
            )
            dried = settled.get("shoe_moisture") == "dry" and settled.get("paper_moisture") == "wet"
            inputs_recorded = {
                "shoe_moisture": shoe_moisture,
                "stuffed_folded_dry_paper": stuffed_folded_dry_paper,
                "elapsed_minutes": float(elapsed_minutes),
            }
            outcome = dict(settled)
            outcome["dried_successfully"] = dried
            summary_cn = (
                "塞进鞋里的揉皱干传单把潮气吸走了，鞋里干爽，纸团变得湿软。"
                if dried
                else "吸湿时间还不够或鞋内未塞干纸，湿气还没完全转移。"
            )

        else:  # half_tangerine_offering
            refusal_lock_active = cfg.get("refusal_lock_active", True)
            quiet_minutes_since_friction = cfg.get("quiet_minutes_since_friction", 45.0)
            days_since_last_peace_gesture = cfg.get("days_since_last_peace_gesture", 4.0)
            kitchen_has_fruit = cfg.get("kitchen_has_fruit", True)
            if not isinstance(refusal_lock_active, bool) or not isinstance(kitchen_has_fruit, bool):
                raise V05InnerSparkError(
                    400,
                    "INVALID_FIELDS",
                    "refusal_lock_active and kitchen_has_fruit must be booleans",
                )
            if (
                not _is_strict_number(quiet_minutes_since_friction)
                or float(quiet_minutes_since_friction) < 0.0
                or not _is_strict_number(days_since_last_peace_gesture)
                or float(days_since_last_peace_gesture) < 0.0
            ):
                raise V05InnerSparkError(400, "INVALID_FIELDS", "Time durations must be non-negative numbers")

            settled = world_fabric.settle_post_refusal_half_tangerine(
                refusal_lock_active=refusal_lock_active,
                quiet_minutes_since_friction=float(quiet_minutes_since_friction),
                days_since_last_peace_gesture=float(days_since_last_peace_gesture),
                kitchen_has_fruit=kitchen_has_fruit,
            )
            inputs_recorded = {
                "refusal_lock_active": refusal_lock_active,
                "quiet_minutes_since_friction": float(quiet_minutes_since_friction),
                "days_since_last_peace_gesture": float(days_since_last_peace_gesture),
                "kitchen_has_fruit": kitchen_has_fruit,
            }
            outcome = dict(settled)
            # Invariant: leaving half a tangerine never retracts or overrides a refusal boundary
            outcome["refusal_lock_active"] = refusal_lock_active
            outcome["apology_issued"] = False
            outcome["boundary_preserved"] = True
            peace_item = outcome.get("desk_peace_item")
            if peace_item:
                summary_cn = f"彼此安静缓过一阵后，桌角悄悄多放了{peace_item}，原先的拒绝边界照旧算数。"
            else:
                summary_cn = "刚起过摩擦还没缓够安静时间，桌上保持原样，不勉强凑近。"

        seq = int(state.get("revision", 0)) + 1
        spark_record: dict[str, Any] = {
            "spark_id": _stable_id("spk", seq, kind, norm_actor, now_str),
            "kind": kind,
            "actor_side": norm_actor,
            "created_at": now_str,
            "inputs": inputs_recorded,
            "outcome": outcome,
            "summary_cn": summary_cn,
            "llm_calls": 0,
        }
        sparks: list[dict[str, Any]] = state["recent_chemistry_sparks"]
        sparks.append(spark_record)
        state["recent_chemistry_sparks"] = sparks[-MAX_CHEMISTRY_SPARKS:]
        _commit_state(day_root, state, now_str)
        return {
            "action": "trigger_object_chemistry",
            "spark": copy.deepcopy(spark_record),
            "revision": state["revision"],
            "llm_calls": 0,
        }


# ---------------------------------------------------------------------------
# Zero-Write Read Projection (Privacy + Anti-Spoiler + Live Melt + Noon Fade)
# ---------------------------------------------------------------------------


def _project_dream_record(
    dream: Optional[Mapping[str, Any]],
    *,
    now_dt: datetime,
    has_subject_capability: bool,
) -> Optional[dict[str, Any]]:
    if not isinstance(dream, Mapping):
        return None
    shared = bool(dream.get("shared_with_partner", False))
    if not has_subject_capability and not shared:
        # Strict privacy isolation: partner never sees unshared dreams
        return None

    kept = bool(dream.get("kept_in_notebook", False))
    fades_after_hour = int(dream.get("fades_after_hour", 12))
    dream_date = str(dream.get("date_key") or "")
    local_now = _to_default_tz(now_dt)
    view_date = local_now.strftime("%Y-%m-%d")
    view_hour = local_now.hour
    faded = (not kept) and (
        (bool(dream_date) and view_date > dream_date)
        or (view_date == dream_date and view_hour >= fades_after_hour)
    )

    if faded:
        return {
            "dream_id": str(dream.get("dream_id", "")),
            "date_key": dream_date,
            "status": "faded_by_noon",
            "fragment_cn": "",
            "source_echoes": [],
            "fades_after_hour": fades_after_hour,
            "kept_in_notebook": False,
            "shared_with_partner": shared,
            "created_at": dream.get("created_at"),
            "fade_note_cn": "过了午时没来得及记下，清晨那点梦痕已经自然淡散了。",
        }

    return {
        "dream_id": str(dream.get("dream_id", "")),
        "date_key": dream_date,
        "status": "kept_in_notebook" if kept else "fresh_morning",
        "fragment_cn": str(dream.get("fragment_cn", "")),
        "source_echoes": list(dream.get("source_echoes", [])),
        "fades_after_hour": fades_after_hour,
        "kept_in_notebook": kept,
        "shared_with_partner": shared,
        "created_at": dream.get("created_at"),
    }


def _load_unified_forget_barrier_zero_write(day_root: Path | str) -> Mapping[str, Any]:
    if _rlf is None:
        return {}
    return _rlf.load_unified_forget_barrier_readonly(day_root)


def _is_blocked_by_barrier(entity: Any, barrier: Mapping[str, Any]) -> bool:
    if _rlf is None or not isinstance(barrier, Mapping):
        return False
    if not (barrier.get("barrier_active") or barrier.get("fail_closed")):
        return False
    return bool(_rlf.is_entity_forget_blocked(entity, barrier))


def _project_foreshadow_books(
    books: Mapping[str, Any],
    *,
    viewer_side: str,
    barrier: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    eff_barrier = barrier if isinstance(barrier, Mapping) else {}
    fail_closed = bool(eff_barrier.get("fail_closed"))
    barrier_active = bool(eff_barrier.get("barrier_active") or fail_closed)
    if fail_closed:
        return []
    projected_books: list[dict[str, Any]] = []
    for title in sorted(books.keys()):
        brec = books[title]
        if not isinstance(brec, Mapping):
            continue
        if barrier_active and (
            _is_blocked_by_barrier(title, eff_barrier)
            or _is_blocked_by_barrier(brec, eff_barrier)
        ):
            continue
        cur_page = int(brec.get("current_page", 1))
        total_pages = int(brec.get("total_pages", 60))
        raw_tucked = brec.get("tucked_notes", [])
        if barrier_active and _rlf is not None and isinstance(raw_tucked, list):
            filtered_tucked = _rlf.filter_collection_by_barrier(raw_tucked, eff_barrier)
        else:
            filtered_tucked = list(raw_tucked) if isinstance(raw_tucked, list) else []
        brec_for_fabric = dict(brec)
        brec_for_fabric["tucked_notes"] = filtered_tucked
        fb = _build_fabric_book(brec_for_fabric)

        # Strict Anti-Spoiler Rule:
        # Other side's unrevealed tucked notes with target_page > current_page are COMPLETELY OMITTED
        # so zero note_text, zero target_page, and zero spoiler hints leak.
        own_pending: list[dict[str, Any]] = []
        for note in filtered_tucked:
            if not isinstance(note, Mapping):
                continue
            if bool(note.get("revealed", False)):
                continue
            author = str(note.get("author_side", ""))
            t_page = int(note.get("target_page", 0))
            if author == viewer_side:
                own_pending.append({
                    "note_id": str(note.get("note_id", "")),
                    "target_page": t_page,
                    "author_side": author,
                    "item_kind": str(note.get("item_kind", "note")),
                    "note_text": str(note.get("note_text", "")),
                    "inserted_at": note.get("inserted_at"),
                    "revealed": False,
                    "status": "waiting_for_page",
                })

        raw_revealed = brec.get("revealed_notes", [])
        if barrier_active and _rlf is not None and isinstance(raw_revealed, list):
            filtered_revealed = _rlf.filter_collection_by_barrier(raw_revealed, eff_barrier)
        else:
            filtered_revealed = list(raw_revealed) if isinstance(raw_revealed, list) else []
        revealed_notes: list[dict[str, Any]] = []
        for rnote in filtered_revealed:
            if isinstance(rnote, Mapping):
                revealed_notes.append(copy.deepcopy(dict(rnote)))

        projected_books.append({
            "book_title": str(brec.get("book_title", title)),
            "total_pages": total_pages,
            "current_page": cur_page,
            "visible_context_before_flip": fb.visible_context_before_flip(),
            "my_pending_notes": own_pending,
            "tucked_notes": copy.deepcopy(own_pending),
            "revealed_notes": revealed_notes,
            "updated_at": brec.get("updated_at"),
        })
    return projected_books


def project_v05_inner_spark_state(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write projection of the 0.5 Inner Spark state for GET views."""
    now_dt, observed_at = _resolve_now_dt(now_iso, strict=False)
    state, state_exists = _read_state_zero_write(day_root)
    barrier = _load_unified_forget_barrier_zero_write(day_root)
    fail_closed = bool(barrier.get("fail_closed"))
    barrier_active = bool(barrier.get("barrier_active") or fail_closed)
    viewer_side = "subject" if has_subject_capability else "partner"
    perspective = "subject_private" if has_subject_capability else "partner_default"

    # 1. Desires (privacy filter for unshared subject desires + unified forget barrier + canonical fields)
    visible_desires: list[dict[str, Any]] = []
    if not fail_closed:
        for raw_item in state["desires"]:
            if barrier_active and _is_blocked_by_barrier(raw_item, barrier):
                continue
            clean_item = _normalize_desire_item(raw_item)
            if not has_subject_capability and not bool(clean_item.get("shared", True)):
                continue
            if barrier_active and _is_blocked_by_barrier(clean_item, barrier):
                continue
            visible_desires.append(clean_item)
        if barrier_active and _rlf is not None:
            visible_desires = _rlf.filter_collection_by_barrier(visible_desires, barrier)

    layers: dict[str, list[dict[str, Any]]] = {layer: [] for layer in DESIRE_LAYERS}
    for clean_item in visible_desires:
        layer_key = str(clean_item.get("layer", "micro_craving"))
        if layer_key in layers:
            layers[layer_key].append(clean_item)

    desires_block = {
        "layers": layers,
        "layer_labels_cn": dict(DESIRE_LAYER_LABELS_CN),
        "items": visible_desires,
        "active_cap_by_layer": dict(ACTIVE_CAP_BY_LAYER),
        "active_counts": _active_counts_by_layer(visible_desires),
        "counts": {
            "micro_craving": len(layers["micro_craving"]),
            "mid_itch": len(layers["mid_itch"]),
            "long_aspiration": len(layers["long_aspiration"]),
            "total_visible": len(visible_desires),
        },
    }

    # 2. Sleep-Wake & Rare Morning Dream (privacy + noon fade + unified forget barrier without disk write)
    sw_raw = state["sleep_wake_state"]
    raw_morning = sw_raw.get("morning_dream")
    if fail_closed or (barrier_active and raw_morning is not None and _is_blocked_by_barrier(raw_morning, barrier)):
        projected_morning_dream = None
    else:
        projected_morning_dream = _project_dream_record(
            raw_morning,
            now_dt=now_dt,
            has_subject_capability=has_subject_capability,
        )
        if projected_morning_dream is not None and barrier_active and _is_blocked_by_barrier(projected_morning_dream, barrier):
            projected_morning_dream = None
    sleep_wake_block = {
        "phase": sw_raw.get("phase", "awake"),
        "last_sleep_at": sw_raw.get("last_sleep_at"),
        "last_wake_at": sw_raw.get("last_wake_at"),
        "morning_dream": projected_morning_dream,
    }
    if has_subject_capability:
        sleep_wake_block["last_dream_roll_date"] = sw_raw.get("last_dream_roll_date")

    projected_notebook: list[dict[str, Any]] = []
    if not fail_closed:
        for entry in state["dream_notebook"]:
            if not has_subject_capability and not bool(entry.get("shared_with_partner", False)):
                continue
            if barrier_active and _is_blocked_by_barrier(entry, barrier):
                continue
            projected_entry = _project_dream_record(
                entry,
                now_dt=now_dt,
                has_subject_capability=has_subject_capability,
            )
            if projected_entry is not None and not (barrier_active and _is_blocked_by_barrier(projected_entry, barrier)):
                projected_notebook.append(projected_entry)
        if barrier_active and _rlf is not None:
            projected_notebook = _rlf.filter_collection_by_barrier(projected_notebook, barrier)

    # 3. Foreshadow Books (strict anti-spoiler + unified forget barrier)
    projected_books = _project_foreshadow_books(
        state["foreshadow_books"],
        viewer_side=viewer_side,
        barrier=barrier if barrier_active else None,
    )
    if barrier_active and _rlf is not None:
        projected_books = _rlf.filter_collection_by_barrier(projected_books, barrier)

    # 4. Window Frost (live melt projection + unified forget barrier without disk write)
    pulse = _resolve_season_pulse_zero_write(day_root, now_dt)
    frost_season_override = (
        str(pulse["season"]) if pulse.get("season_source") == "world_state_override" else None
    )
    raw_frost = state.get("window_frost")
    if fail_closed or (barrier_active and raw_frost is not None and _is_blocked_by_barrier(raw_frost, barrier)):
        projected_frost = None
    else:
        projected_frost = project_window_frost(
            raw_frost,
            now_dt,
            season=frost_season_override,
        )
        if projected_frost is not None and barrier_active and _is_blocked_by_barrier(projected_frost, barrier):
            projected_frost = None

    # 5. Recent Chemistry Sparks (bounded to 16 + unified forget barrier)
    if fail_closed:
        recent_sparks: list[dict[str, Any]] = []
    else:
        recent_sparks = copy.deepcopy(state["recent_chemistry_sparks"][-MAX_CHEMISTRY_SPARKS:])
        if barrier_active and _rlf is not None:
            recent_sparks = _rlf.filter_collection_by_barrier(recent_sparks, barrier)

    micro = pulse["microclimate"]
    season_solar_summary = {
        "schema_version": pulse["schema_version"],
        "season": pulse["season"],
        "season_label_cn": pulse["season_label_cn"],
        "season_source": pulse["season_source"],
        "solar_term": copy.deepcopy(pulse["solar_term"]),
        "sunlight_window": micro["sunlight_window"],
        "frost_ease": micro["frost_ease"],
        "frost_temp_threshold_c": micro["frost_temp_threshold_c"],
        "frost_melt_multiplier": micro["frost_melt_multiplier"],
        "seasonal_spark_note_cn": micro["seasonal_spark_note_cn"],
        "anti_chore_contract": copy.deepcopy(pulse["anti_chore_contract"]),
    }

    projected: dict[str, Any] = {
        "schema_version": STATE_SCHEMA,
        "state_exists": state_exists,
        "perspective": perspective,
        "viewer_side": viewer_side,
        "revision": int(state.get("revision", 0)),
        "observed_at": observed_at,
        "season_solar": season_solar_summary,
        "desires": desires_block,
        "sleep_wake_state": sleep_wake_block,
        "dream_notebook": projected_notebook,
        "foreshadow_books": projected_books,
        "window_frost": projected_frost,
        "recent_chemistry_sparks": recent_sparks,
        "llm_calls": 0,
        "zero_write": True,
    }
    if barrier_active and _rlf is not None:
        projected = _rlf.filter_document_by_barrier(
            projected,
            barrier,
            preserve_keys=(
                "schema_version",
                "state_exists",
                "perspective",
                "viewer_side",
                "revision",
                "observed_at",
                "season_solar",
                "layer_labels_cn",
                "active_cap_by_layer",
                "active_counts",
                "counts",
                "llm_calls",
                "zero_write",
            ),
            nullable_entity_keys=("morning_dream", "window_frost"),
        )
    return projected


def _read_body_sleep_status_zero_write(day_root: Path | str) -> dict[str, Any]:
    """Read optional body runtime sleep_like state with 0 disk writes."""
    paths = _instance_paths.resolve_instance_paths(day_root)
    rt_path = paths.body_runtime_json
    if rt_path.is_symlink() or not rt_path.is_file():
        return {"present": False, "sleeping": False, "completed_sleep": False, "started_at": None, "last_advanced_at": None}
    try:
        doc = json.loads(rt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"present": False, "sleeping": False, "completed_sleep": False, "started_at": None, "last_advanced_at": None}
    if not isinstance(doc, Mapping):
        return {"present": False, "sleeping": False, "completed_sleep": False, "started_at": None, "last_advanced_at": None}
    rec = doc.get("recovery") if isinstance(doc.get("recovery"), Mapping) else {}
    rec_state = str(rec.get("state") or "inactive").strip().lower()
    rec_mode = str(rec.get("mode") or "").strip().lower()
    sleeping = rec_state == "active" and rec_mode == "sleep_like"
    completed_sleep = rec_state in ("completed", "interrupted") and rec_mode == "sleep_like"
    return {
        "present": True,
        "sleeping": sleeping,
        "completed_sleep": completed_sleep,
        "started_at": rec.get("started_at") if isinstance(rec.get("started_at"), str) else None,
        "last_advanced_at": rec.get("last_advanced_at") if isinstance(rec.get("last_advanced_at"), str) else None,
    }


def _collect_recent_echoes_from_state(state: Mapping[str, Any]) -> list[str]:
    echoes: list[str] = []
    seen: set[str] = set()
    for item in reversed(state.get("desires") or []):
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "active") != "active":
            continue
        txt = _CONTROL_CHAR_RE.sub("", str(item.get("text") or "")).strip()[:MAX_ECHO_LEN]
        if txt and txt not in seen:
            seen.add(txt)
            echoes.append(txt)
            if len(echoes) >= 2:
                return echoes
    for spk in reversed(state.get("recent_chemistry_sparks") or []):
        if not isinstance(spk, Mapping):
            continue
        txt = _CONTROL_CHAR_RE.sub("", str(spk.get("summary_cn") or "")).strip()[:MAX_ECHO_LEN]
        if txt and txt not in seen:
            seen.add(txt)
            echoes.append(txt)
            if len(echoes) >= 2:
                return echoes
    return echoes


def advance_inner_spark_heartbeat(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
    woke_from_sleep: bool = False,
) -> dict[str, Any]:
    """Zero-LLM background heartbeat for ``v05_inner_spark.json``.

    - Melts window frost when daylight warmth advances or clears it;
    - Records real ``sleep_like`` phase entry and triggers ``settle_morning_dream_pulse``
      ONLY when ``woke_from_sleep=True`` or when transitioning out of a real sleep state;
    - Performs 0 disk writes when the state file is absent (and not waking) or nothing changed.
    """
    if not isinstance(woke_from_sleep, bool):
        raise V05InnerSparkError(400, "INVALID_FIELDS", "Field 'woke_from_sleep' must be a boolean")

    now_dt, now_str = _resolve_now_dt(now_iso, strict=True)
    date_key = now_dt.strftime("%Y-%m-%d")

    pre_state, state_exists = _read_state_zero_write(day_root)
    if not state_exists and not woke_from_sleep:
        return {
            "ok": True,
            "changed": False,
            "reason": "state_absent",
            "frost_updated": False,
            "frost_melted": False,
            "sleep_phase_updated": False,
            "morning_dream_settled": False,
            "morning_dream": None,
            "llm_calls": 0,
        }

    path = state_file_path(day_root)
    with _path_lock(path):
        state, state_exists_locked = _read_state_zero_write(day_root)
        if not state_exists_locked and not woke_from_sleep:
            return {
                "ok": True,
                "changed": False,
                "reason": "state_absent",
                "frost_updated": False,
                "frost_melted": False,
                "sleep_phase_updated": False,
                "morning_dream_settled": False,
                "morning_dream": None,
                "llm_calls": 0,
            }

        changed = False
        frost_updated = False
        frost_melted = False
        sleep_phase_updated = False
        morning_dream_settled = False
        dream_receipt: Optional[dict[str, Any]] = None

        # 1. Daylight warmth frost melting
        frost_rec = state.get("window_frost")
        if isinstance(frost_rec, dict):
            pulse = _resolve_season_pulse_zero_write(day_root, now_dt)
            frost_season_override = (
                str(pulse["season"]) if pulse.get("season_source") == "world_state_override" else None
            )
            projected = project_window_frost(frost_rec, now_dt, season=frost_season_override)
            if isinstance(projected, Mapping):
                prev_phase = str(frost_rec.get("canvas_phase") or "FRESH")
                next_phase = str(projected.get("canvas_phase") or prev_phase)
                prev_melted = bool(frost_rec.get("melted", False))
                next_melted = bool(projected.get("melted", False))
                prev_vis = frost_rec.get("visibility_ratio")
                next_vis = float(projected.get("visibility_ratio", 1.0))
                sun_warm = float(projected.get("sun_warm_hours", 0.0))

                significant_warm_melt = (
                    not next_melted
                    and sun_warm > 0.0
                    and (
                        not _is_strict_number(prev_vis)
                        or (float(prev_vis) - next_vis) >= 0.15
                    )
                )
                melted_transition = (next_melted and not prev_melted) or (next_phase != prev_phase)
                if melted_transition or significant_warm_melt:
                    frost_rec["canvas_phase"] = next_phase
                    frost_rec["visibility_ratio"] = next_vis
                    frost_rec["sun_warm_hours"] = round(sun_warm, 2)
                    frost_rec["melted"] = next_melted
                    if next_melted and not prev_melted:
                        frost_rec["melted_at"] = now_str
                        frost_melted = True
                    if next_phase == "LATENT" and float(frost_rec.get("latent_since_hour") or 0.0) <= 0.0:
                        frost_rec["latent_since_hour"] = _epoch_hours(now_dt)
                    elif next_phase == "NONE":
                        frost_rec["strokes"] = []
                    frost_updated = True
                    changed = True

        # 2. Real sleep tracking & wake-only morning dream pulse
        sw = state["sleep_wake_state"]
        prev_sleep_phase = str(sw.get("phase") or "awake")
        body_sleep = _read_body_sleep_status_zero_write(day_root)

        transitioned_out_of_sleep = False
        if woke_from_sleep:
            transitioned_out_of_sleep = True
        elif body_sleep["present"]:
            if body_sleep["sleeping"]:
                if prev_sleep_phase != "asleep":
                    sw["phase"] = "asleep"
                    sw["last_sleep_at"] = body_sleep["started_at"] or now_str
                    sleep_phase_updated = True
                    changed = True
            else:
                if prev_sleep_phase == "asleep":
                    transitioned_out_of_sleep = True
                elif body_sleep["completed_sleep"]:
                    last_adv = _parse_iso_dt(body_sleep.get("last_advanced_at"))
                    last_wake = _parse_iso_dt(sw.get("last_wake_at"))
                    if last_adv is not None and (last_wake is None or last_adv > last_wake):
                        transitioned_out_of_sleep = True

        should_settle_dream = (
            transitioned_out_of_sleep
            and (
                str(sw.get("last_dream_roll_date") or "") != date_key
                or prev_sleep_phase == "asleep"
            )
        )

        if changed and not should_settle_dream:
            _commit_state(day_root, state, now_str)
        elif changed and should_settle_dream:
            # Persist frost update first so settle_morning_dream_pulse reads it under re-entrant RLock.
            _commit_state(day_root, state, now_str)

        if should_settle_dream:
            echoes = _collect_recent_echoes_from_state(state)
            dream_receipt = settle_morning_dream_pulse(
                day_root,
                recent_echoes=echoes,
                now_iso=now_str,
                phase="just_woke",
                last_sleep_at=sw.get("last_sleep_at") or body_sleep.get("started_at"),
                actor_side="subject",
            )
            morning_dream_settled = True
            changed = True
            final_state, _ = _read_state_zero_write(day_root)
            final_rev = int(final_state.get("revision", 0))
            morning_dream_out = copy.deepcopy(final_state["sleep_wake_state"].get("morning_dream"))
        else:
            final_rev = int(state.get("revision", 0))
            morning_dream_out = copy.deepcopy(sw.get("morning_dream"))

        return {
            "ok": True,
            "changed": changed,
            "reason": "updated" if changed else "no_change",
            "frost_updated": frost_updated,
            "frost_melted": frost_melted,
            "sleep_phase_updated": sleep_phase_updated,
            "morning_dream_settled": morning_dream_settled,
            "morning_dream": morning_dream_out,
            "dream_receipt": dream_receipt,
            "revision": final_rev,
            "llm_calls": 0,
        }


# ---------------------------------------------------------------------------
# 5. Unified Full-Spectrum Desire Ticker & Autonomous Zero-LLM Emergence
# ---------------------------------------------------------------------------


def _read_json_file_zero_write(path: Path) -> Optional[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        return dict(doc) if isinstance(doc, Mapping) else None
    except (OSError, ValueError, TypeError):
        return None


def _read_live_desire_signals_zero_write(
    day_root: Path | str,
    now_dt: datetime,
    spark_state: Mapping[str, Any],
    *,
    barrier: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Read live body, weather, indoor duration, craft, town, social silence & S07 affect with 0 writes."""
    paths = _instance_paths.resolve_instance_paths(day_root)
    croot = paths.day_root
    eff_barrier = barrier if isinstance(barrier, Mapping) else _load_unified_forget_barrier_zero_write(day_root)
    fail_closed = bool(eff_barrier.get("fail_closed"))
    barrier_active = bool(eff_barrier.get("barrier_active") or fail_closed)

    # 1. Acute body state (runtime.json + somatic_comfort.json)
    body_doc = _read_json_file_zero_write(paths.body_runtime_json)
    if body_doc is None:
        for alt in (
            croot / "one-day" / "world-home" / "data" / "body" / "runtime.json",
            croot / "data" / "body" / "runtime.json",
            croot / "world" / "data" / "body" / "runtime.json",
        ):
            body_doc = _read_json_file_zero_write(alt)
            if body_doc is not None:
                break

    hunger = 0.0
    thirst = 0.0
    toilet = 0.0
    warmth = 0.50
    fatigue = 0.15
    cold_tendency = False
    sleeping_active = False

    if isinstance(body_doc, Mapping):
        somatic = body_doc.get("somatic") if isinstance(body_doc.get("somatic"), Mapping) else {}
        if _is_strict_number(somatic.get("warmth")):
            warmth = float(somatic["warmth"])
        elif _is_strict_number(body_doc.get("warmth")):
            warmth = float(body_doc["warmth"])
        if _is_strict_number(somatic.get("fatigue")):
            fatigue = float(somatic["fatigue"])
        elif _is_strict_number(body_doc.get("fatigue")):
            fatigue = float(body_doc["fatigue"])

        latch = body_doc.get("signal_latch") if isinstance(body_doc.get("signal_latch"), Mapping) else {}
        if latch.get("cold_tendency") is True or warmth <= 0.36:
            cold_tendency = True

        lived = body_doc.get("lived_needs") if isinstance(body_doc.get("lived_needs"), Mapping) else {}
        levels = lived.get("levels") if isinstance(lived.get("levels"), Mapping) else {}
        if _is_strict_number(levels.get("hunger")):
            hunger = float(levels["hunger"])
        if _is_strict_number(levels.get("thirst")):
            thirst = float(levels["thirst"])
        if _is_strict_number(levels.get("toilet")):
            toilet = float(levels["toilet"])

        rec = body_doc.get("recovery") if isinstance(body_doc.get("recovery"), Mapping) else {}
        if (
            str(rec.get("state") or "").strip().lower() == "active"
            and str(rec.get("mode") or "").strip().lower() == "sleep_like"
        ):
            sleeping_active = True

    somatic_comfort_doc = _read_json_file_zero_write(croot / "phone" / "somatic_comfort.json")
    damp_outerwear_worn = False
    if isinstance(somatic_comfort_doc, Mapping):
        garment = somatic_comfort_doc.get("garment") if isinstance(somatic_comfort_doc.get("garment"), Mapping) else {}
        if garment.get("state") == "wear" and garment.get("dryness") in ("damp", "wet"):
            damp_outerwear_worn = True

    # 2. Neighborhood weather & prolonged indoor time
    nb_doc = _read_json_file_zero_write(croot / "world" / "v04_neighborhood_state.json")
    world_doc = _read_json_file_zero_write(paths.world_state_json)
    canonical_loc = _instance_paths.read_canonical_world_location_zero_write(croot)

    weather = "clear"
    nb_exists = nb_doc is not None
    current_place = str(canonical_loc.get("v04_place_id") or "home") if canonical_loc.get("found") else "home"
    indoor_hours: float = 0.0

    if isinstance(nb_doc, Mapping):
        env = nb_doc.get("environment") if isinstance(nb_doc.get("environment"), Mapping) else {}
        raw_w = env.get("weather")
        if isinstance(raw_w, str) and raw_w.strip():
            weather = raw_w.strip().lower()
        journey = nb_doc.get("journey") if isinstance(nb_doc.get("journey"), Mapping) else {}
        journey_blocked = fail_closed or (
            barrier_active
            and (
                _is_blocked_by_barrier(nb_doc, eff_barrier)
                or _is_blocked_by_barrier(journey, eff_barrier)
            )
        )
        if not journey_blocked:
            j_place = journey.get("current_place_id")
            if isinstance(j_place, str) and j_place.strip():
                current_place = j_place.strip().lower()
            j_state = str(journey.get("state") or "at_place").strip().lower()
            explicit_indoor = nb_doc.get("indoor_hours") or nb_doc.get("hours_indoors") or journey.get("indoor_hours")
            if _is_strict_number(explicit_indoor):
                indoor_hours = max(0.0, float(explicit_indoor))
            elif current_place in ("home", "porch") and j_state == "at_place":
                anchor_ts = (
                    journey.get("arrived_at")
                    or journey.get("turned_back_at")
                    or journey.get("started_at")
                )
                anchor_dt = _parse_iso_dt(anchor_ts)
                if anchor_dt is not None and now_dt > anchor_dt:
                    indoor_hours = max(0.0, (now_dt - anchor_dt).total_seconds() / 3600.0)

    if indoor_hours <= 0.0 and not fail_closed and isinstance(world_doc, Mapping) and current_place in ("home", "porch"):
        ws_indoor = world_doc.get("indoor_hours") or world_doc.get("hours_indoors")
        if _is_strict_number(ws_indoor):
            indoor_hours = max(0.0, float(ws_indoor))
        else:
            ws_since = world_doc.get("location_since") or world_doc.get("arrived_at")
            ws_dt = _parse_iso_dt(ws_since)
            if ws_dt is not None and now_dt > ws_dt:
                indoor_hours = max(0.0, (now_dt - ws_dt).total_seconds() / 3600.0)

    # 3. Unfinished craft / booklet / v04 projects & playful games
    ep_doc = _read_json_file_zero_write(croot / "phone" / "v04_elastic_play.json")
    active_projects: list[dict[str, Any]] = []
    active_game: Optional[dict[str, Any]] = None
    if isinstance(ep_doc, Mapping) and not fail_closed:
        raw_projects = ep_doc.get("personal_projects") or []
        if barrier_active and _rlf is not None and isinstance(raw_projects, list):
            raw_projects = _rlf.filter_collection_by_barrier(raw_projects, eff_barrier)
        for proj in raw_projects:
            if not isinstance(proj, Mapping) or str(proj.get("status") or "").strip().lower() != "active":
                continue
            pid_c = str(proj.get("project_id") or proj.get("purpose_cn") or "project")[:48]
            if barrier_active and (
                _is_blocked_by_barrier(proj, eff_barrier)
                or _is_blocked_by_barrier(f"craft:project:{pid_c}", eff_barrier)
            ):
                continue
            active_projects.append(dict(proj))
        tgame = ep_doc.get("temporary_game")
        if isinstance(tgame, Mapping) and (
            tgame.get("active") is True or str(tgame.get("status") or "").strip().lower() == "active"
        ):
            gkind_c = str(tgame.get("game_kind") or "桌面小游戏").strip()[:24]
            if not (
                barrier_active
                and (
                    _is_blocked_by_barrier(tgame, eff_barrier)
                    or _is_blocked_by_barrier(f"play:game:{gkind_c}", eff_barrier)
                )
            ):
                active_game = dict(tgame)

    craft_doc = _read_json_file_zero_write(croot / "world" / "v05_home_craft.json")
    unpacked_carton_id: Optional[str] = None
    pending_arrangement_id: Optional[str] = None
    if isinstance(craft_doc, Mapping) and not fail_closed:
        mc = craft_doc.get("moving_carton") if isinstance(craft_doc.get("moving_carton"), Mapping) else {}
        raw_cartons = mc.get("cartons") or []
        if barrier_active and _rlf is not None and isinstance(raw_cartons, list):
            raw_cartons = _rlf.filter_collection_by_barrier(raw_cartons, eff_barrier)
        for carton in raw_cartons:
            if not isinstance(carton, Mapping):
                continue
            cid_c = str(carton.get("carton_id") or "entryway_carton")
            if barrier_active and (
                _is_blocked_by_barrier(carton, eff_barrier)
                or _is_blocked_by_barrier(f"craft:carton:{cid_c}", eff_barrier)
            ):
                continue
            items = carton.get("items")
            if barrier_active and _rlf is not None and isinstance(items, list):
                items = _rlf.filter_collection_by_barrier(items, eff_barrier)
            if isinstance(items, list) and any(
                isinstance(it, Mapping) and not bool(it.get("unpacked", False)) for it in items
            ):
                unpacked_carton_id = cid_c
                break
        ha = craft_doc.get("home_arrangement") if isinstance(craft_doc.get("home_arrangement"), Mapping) else {}
        raw_props = ha.get("proposals") or []
        if barrier_active and _rlf is not None and isinstance(raw_props, list):
            raw_props = _rlf.filter_collection_by_barrier(raw_props, eff_barrier)
        for prop in raw_props:
            if not isinstance(prop, Mapping) or str(prop.get("status") or "").strip().lower() != "pending":
                continue
            prop_id_c = str(prop.get("proposal_id") or "arrangement")
            if barrier_active and (
                _is_blocked_by_barrier(prop, eff_barrier)
                or _is_blocked_by_barrier(f"craft:arrangement:{prop_id_c}", eff_barrier)
            ):
                continue
            pending_arrangement_id = prop_id_c
            break

    unfinished_booklet: Optional[tuple[str, int, int]] = None
    if not fail_closed:
        for title, brec in sorted((spark_state.get("foreshadow_books") or {}).items()):
            if not isinstance(brec, Mapping):
                continue
            b_title_c = str(brec.get("book_title") or title)
            if barrier_active and (
                _is_blocked_by_barrier(title, eff_barrier)
                or _is_blocked_by_barrier(brec, eff_barrier)
                or _is_blocked_by_barrier(f"craft:booklet:{b_title_c[:48]}", eff_barrier)
            ):
                continue
            cur_p = int(brec.get("current_page") or 1)
            tot_p = int(brec.get("total_pages") or 60)
            if 1 < cur_p < tot_p:
                unfinished_booklet = (b_title_c, cur_p, tot_p)
                break

    # 4. v05_living_town regulars / bookbox / bulletin / kitten & social silence
    town_doc = _read_json_file_zero_write(croot / "world" / "v05_living_town.json")
    bookbox_spark: Optional[dict[str, str]] = None
    bulletin_spark: Optional[dict[str, str]] = None
    regular_spark: Optional[dict[str, str]] = None
    kitten_spark: Optional[dict[str, str]] = None

    if isinstance(town_doc, Mapping) and not fail_closed:
        raw_books = town_doc.get("drifting_bookbox") or []
        if barrier_active and _rlf is not None and isinstance(raw_books, list):
            raw_books = _rlf.filter_collection_by_barrier(raw_books, eff_barrier)
        for book in raw_books:
            if not isinstance(book, Mapping):
                continue
            if str(book.get("status") or "IN_BOX").upper() != "IN_BOX":
                continue
            bid = str(book.get("book_id") or "").strip()
            btitle = str(book.get("book_title") or "").strip()
            if barrier_active and (
                _is_blocked_by_barrier(book, eff_barrier)
                or _is_blocked_by_barrier(f"town:bookbox:{(bid or btitle)[:48]}", eff_barrier)
            ):
                continue
            margin_notes = book.get("margin_notes") if isinstance(book.get("margin_notes"), list) else []
            if barrier_active and _rlf is not None:
                margin_notes = _rlf.filter_collection_by_barrier(margin_notes, eff_barrier)
            left_by = str(book.get("left_by_side") or "town").strip().lower()
            is_untouched_starter = (
                bid == "book_starter_tide_notes"
                and left_by == "town"
                and len(margin_notes) <= 1
            )
            if btitle and not is_untouched_starter:
                bookbox_spark = {"book_id": bid or btitle, "book_title": btitle}
                break

        raw_notices = town_doc.get("bulletin_followups") or []
        if barrier_active and _rlf is not None and isinstance(raw_notices, list):
            raw_notices = _rlf.filter_collection_by_barrier(raw_notices, eff_barrier)
        for notice in raw_notices:
            if not isinstance(notice, Mapping):
                continue
            nid = str(notice.get("notice_id") or "").strip()
            ntitle = str(notice.get("title_cn") or "").strip()
            if barrier_active and (
                _is_blocked_by_barrier(notice, eff_barrier)
                or _is_blocked_by_barrier(f"town:bulletin:{(nid or ntitle)[:48]}", eff_barrier)
            ):
                continue
            followups = notice.get("followups") if isinstance(notice.get("followups"), list) else []
            if barrier_active and _rlf is not None:
                followups = _rlf.filter_collection_by_barrier(followups, eff_barrier)
            is_untouched_starter = nid == "notice_alley_cat" and len(followups) <= 1
            if ntitle and not is_untouched_starter:
                bulletin_spark = {"notice_id": nid or ntitle, "title_cn": ntitle}
                break

        regulars = town_doc.get("regulars") if isinstance(town_doc.get("regulars"), Mapping) else {}
        regular_labels = {
            "auntie_lin": "面包铺林姨",
            "uncle_zhou": "旧书铺周叔",
            "sister_su": "街角苏姐",
            "old_chen": "码头陈伯",
            "young_kaito": "送奶小海",
        }
        for rid in sorted(regulars.keys()):
            rrow = regulars[rid]
            if not isinstance(rrow, Mapping):
                continue
            if barrier_active and (
                _is_blocked_by_barrier(str(rid), eff_barrier)
                or _is_blocked_by_barrier(rrow, eff_barrier)
                or _is_blocked_by_barrier(f"town:regular:{str(rid)[:40]}", eff_barrier)
            ):
                continue
            if (
                int(rrow.get("favor_count") or 0) > 0
                or bool(rrow.get("unlocked_courtesy_cn"))
                or bool(rrow.get("last_encounter_at"))
            ):
                regular_spark = {
                    "regular_id": str(rid),
                    "name_cn": regular_labels.get(str(rid), str(rid)),
                }
                break

        kitten = town_doc.get("alley_kitten") if isinstance(town_doc.get("alley_kitten"), Mapping) else {}
        if (
            not (
                barrier_active
                and (
                    _is_blocked_by_barrier(kitten, eff_barrier)
                    or _is_blocked_by_barrier("town:alley_kitten", eff_barrier)
                )
            )
            and (int(kitten.get("feeding_count") or 0) > 0 or int(kitten.get("greeting_count") or 0) > 0)
        ):
            nick = str(kitten.get("nickname") or "巷口橘猫").strip()
            kitten_spark = {"nickname": nick}

    # Social silence detection (suppressed during refusal lock / emotional space)
    in_space_or_refused = False
    latest_social_dt: Optional[datetime] = None

    rhythm_doc = _read_json_file_zero_write(croot / "phone" / "autonomy_rhythm.json")
    if isinstance(rhythm_doc, Mapping):
        for lock in rhythm_doc.get("active_locks") or []:
            if isinstance(lock, Mapping) and str(lock.get("stance") or "") == "REFUSED_FIRM":
                exp_dt = _parse_iso_dt(lock.get("expires_at"))
                if exp_dt is None or exp_dt > now_dt:
                    in_space_or_refused = True
        rr = rhythm_doc.get("reply_rhythm") if isinstance(rhythm_doc.get("reply_rhythm"), Mapping) else {}
        for ts_cand in (
            rhythm_doc.get("last_partner_message_at"),
            rhythm_doc.get("last_inbound_at"),
            rr.get("last_user_turn_at"),
            rr.get("last_partner_message_at"),
        ):
            parsed = _parse_iso_dt(ts_cand)
            if parsed is not None and (latest_social_dt is None or parsed > latest_social_dt):
                latest_social_dt = parsed

    tension_doc = _read_json_file_zero_write(croot / "phone" / "emotional_tension.json")
    if isinstance(tension_doc, Mapping):
        tsum = tension_doc.get("tension_summary") if isinstance(tension_doc.get("tension_summary"), Mapping) else {}
        if tsum.get("in_space") is True or tension_doc.get("in_space") is True:
            in_space_or_refused = True
        for ts_cand in (
            tension_doc.get("last_interaction_at"),
            tension_doc.get("last_partner_contact_at"),
            tsum.get("last_interaction_at"),
        ):
            parsed = _parse_iso_dt(ts_cand)
            if parsed is not None and (latest_social_dt is None or parsed > latest_social_dt):
                latest_social_dt = parsed

    eco_doc = _read_json_file_zero_write(croot / "phone" / "v03_ecology.json")
    if isinstance(eco_doc, Mapping):
        rb = eco_doc.get("reality_bridge") if isinstance(eco_doc.get("reality_bridge"), Mapping) else {}
        parsed = _parse_iso_dt(rb.get("last_partner_inbound_at"))
        if parsed is not None and (latest_social_dt is None or parsed > latest_social_dt):
            latest_social_dt = parsed

    social_silence_hours = 0.0
    if latest_social_dt is not None and not in_space_or_refused and now_dt > latest_social_dt:
        social_silence_hours = (now_dt - latest_social_dt).total_seconds() / 3600.0

    # 5. S07 affect mild boredom / restlessness / curiosity
    affect_signal: Optional[dict[str, Any]] = None
    affect_candidates = (
        croot / "life" / "s07_affect_records.json",
        paths.life_state_root / "s07_affect_records.json",
        croot / "state" / "s07_affect_records.json",
    )
    seen_aff: set[str] = set()
    bored_tokens = ("bored", "boredom", "restless", "restlessness", "无聊", "发闷", "烦闷", "坐不住", "躁动")
    curious_tokens = ("curious", "curiosity", "mystery", "wonder", "好奇", "琢磨", "想看看", "新鲜")
    for aff_path in (() if fail_closed else affect_candidates):
        akey = str(aff_path)
        if akey in seen_aff:
            continue
        seen_aff.add(akey)
        aff_doc = _read_json_file_zero_write(aff_path)
        if aff_doc is None:
            continue
        raw_recs = aff_doc.get("records") if isinstance(aff_doc.get("records"), list) else []
        if barrier_active and _rlf is not None:
            raw_recs = _rlf.filter_collection_by_barrier(raw_recs, eff_barrier)
        for rec in raw_recs:
            if not isinstance(rec, Mapping):
                continue
            rec_id = str(rec.get("record_id") or "s07").strip()[:40]
            if barrier_active and (
                _is_blocked_by_barrier(rec, eff_barrier)
                or _is_blocked_by_barrier(f"affect:restless:{rec_id}", eff_barrier)
                or _is_blocked_by_barrier(f"affect:curious:{rec_id}", eff_barrier)
            ):
                continue
            exp_dt = _parse_iso_dt(rec.get("expires_at"))
            if exp_dt is not None and now_dt >= exp_dt:
                continue
            raw_int = rec.get("intensity", 50)
            eff_int = int(raw_int) if _is_strict_number(raw_int) else 50
            if eff_int <= 0:
                continue
            parts: list[str] = []
            for k in ("kind", "note", "declared_scope", "mood", "tag", "label"):
                v = rec.get(k)
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip().lower())
            srefs = rec.get("source_refs")
            if isinstance(srefs, list):
                parts.extend(str(r).strip().lower() for r in srefs if isinstance(r, str))
            haystack = " ".join(parts)
            if any(tok in haystack for tok in bored_tokens):
                affect_signal = {"mode": "restless", "record_id": rec_id, "intensity": eff_int}
                break
            if any(tok in haystack for tok in curious_tokens):
                affect_signal = {"mode": "curious", "record_id": rec_id, "intensity": eff_int}
                break
        if affect_signal is not None:
            break

    return {
        "sleeping_active": sleeping_active,
        "hunger": hunger,
        "thirst": thirst,
        "toilet": toilet,
        "warmth": warmth,
        "fatigue": fatigue,
        "cold_tendency": cold_tendency,
        "damp_outerwear_worn": damp_outerwear_worn,
        "nb_exists": nb_exists,
        "weather": weather,
        "is_rainy": weather in _RAINY_EMERGE_WEATHERS,
        "current_place": current_place,
        "indoor_hours": indoor_hours,
        "active_projects": active_projects,
        "active_game": active_game,
        "unpacked_carton_id": unpacked_carton_id,
        "pending_arrangement_id": pending_arrangement_id,
        "unfinished_booklet": unfinished_booklet,
        "bookbox_spark": bookbox_spark,
        "bulletin_spark": bulletin_spark,
        "regular_spark": regular_spark,
        "kitten_spark": kitten_spark,
        "social_silence_hours": social_silence_hours,
        "affect_signal": affect_signal,
    }


def _build_autonomous_desire_candidates(
    signals: Mapping[str, Any],
    now_dt: datetime,
) -> list[dict[str, Any]]:
    """Build deterministic full-spectrum non-sexual desire candidates from live signals."""
    if signals.get("sleeping_active"):
        return []

    local_now = _to_default_tz(now_dt)
    hour = int(local_now.hour)
    date_key = local_now.strftime("%Y-%m-%d")
    weather = str(signals.get("weather") or "clear")
    is_rainy = bool(signals.get("is_rainy"))
    hunger = float(signals.get("hunger") or 0.0)
    thirst = float(signals.get("thirst") or 0.0)
    warmth = float(signals.get("warmth") if _is_strict_number(signals.get("warmth")) else 0.50)
    cold_tendency = bool(signals.get("cold_tendency"))
    damp_worn = bool(signals.get("damp_outerwear_worn"))

    candidates: list[dict[str, Any]] = []

    # 1. Body hunger / thirst / cold -> micro_craving (appetite_craving / cozy_comfort)
    if hunger >= 0.45:
        if is_rainy or cold_tendency:
            candidates.append({
                "layer": "micro_craving",
                "category": "appetite_craving",
                "origin": "self_emerged",
                "world_anchor": f"body:hunger+weather:{weather}",
                "text": "雨意微凉又有点饿了，想煮一锅热腾腾的奶油蘑菇汤或热汤面暖暖胃",
                "intensity": round(min(0.92, 0.56 + hunger * 0.34), 4),
                "estimated_cost_jpy": 0,
                "requires_outing": False,
                "shared": True,
            })
        elif 7 <= hour <= 19:
            candidates.append({
                "layer": "micro_craving",
                "category": "appetite_craving",
                "origin": "self_emerged",
                "world_anchor": "body:hunger:bakery",
                "text": "肚子有点空了，想顺路去拐角面包铺挑一只刚出炉的盐卷配热茶",
                "intensity": round(min(0.90, 0.55 + hunger * 0.34), 4),
                "estimated_cost_jpy": 380,
                "requires_outing": True,
                "shared": True,
            })
        else:
            candidates.append({
                "layer": "micro_craving",
                "category": "appetite_craving",
                "origin": "self_emerged",
                "world_anchor": "body:hunger:home_snack",
                "text": "夜里肚子有点饿，想在厨房烤一片热吐司或冲一碗燕麦",
                "intensity": round(min(0.88, 0.54 + hunger * 0.32), 4),
                "estimated_cost_jpy": 0,
                "requires_outing": False,
                "shared": True,
            })

    if thirst >= 0.45:
        candidates.append({
            "layer": "micro_craving",
            "category": "appetite_craving",
            "origin": "self_emerged",
            "world_anchor": "body:thirst",
            "text": "嗓子有点发干，想泡一壶温热的焙茶或姜茶慢慢喝",
            "intensity": round(min(0.90, 0.52 + thirst * 0.34), 4),
            "estimated_cost_jpy": 0,
            "requires_outing": False,
            "shared": True,
        })

    if cold_tendency or warmth <= 0.36 or damp_worn:
        text_cn = (
            "外衣还带着湿凉气，想赶紧挂起来换上干爽家居服再捧杯热茶"
            if damp_worn
            else "手脚有点发凉，想披上亚麻薄毯靠近暖灯缓一缓"
        )
        candidates.append({
            "layer": "micro_craving",
            "category": "cozy_comfort",
            "origin": "self_emerged",
            "world_anchor": "body:cold_warmth:damp" if damp_worn else "body:cold_warmth",
            "text": text_cn,
            "intensity": round(min(0.90, 0.62 + max(0.0, 0.45 - warmth) * 0.6), 4),
            "estimated_cost_jpy": 0,
            "requires_outing": False,
            "shared": True,
        })

    # 2. v04_neighborhood rain/drizzle OR prolonged indoor time
    if signals.get("nb_exists") and is_rainy:
        candidates.append({
            "layer": "micro_craving",
            "category": "cozy_comfort",
            "origin": "world_spark",
            "world_anchor": f"weather:{weather}:cozy",
            "text": "窗外正落着雨，想拧亮暖灯靠在窗边听一会儿檐角雨声",
            "intensity": 0.68,
            "estimated_cost_jpy": 0,
            "requires_outing": False,
            "shared": True,
        })

    indoor_hours = float(signals.get("indoor_hours") or 0.0)
    if (
        indoor_hours >= 4.0
        and not is_rainy
        and 8 <= hour <= 20
        and str(signals.get("current_place") or "home") in ("home", "porch")
    ):
        candidates.append({
            "layer": "mid_itch",
            "category": "restlessness_outing",
            "origin": "self_emerged",
            "world_anchor": "indoor:prolonged_stay",
            "text": "在屋里待了大半天，想出去沿河堤或街角散十分钟步透透气",
            "intensity": round(min(0.84, 0.64 + min(4.0, indoor_hours - 4.0) * 0.04), 4),
            "estimated_cost_jpy": 0,
            "requires_outing": True,
            "shared": True,
        })

    # 3. Unfinished craft / booklet / v04 project -> craft_ambition (mid_itch / long_aspiration)
    active_projects = signals.get("active_projects") or []
    if active_projects:
        proj = active_projects[0]
        pid = str(proj.get("project_id") or proj.get("purpose_cn") or "project")[:48]
        purpose = str(proj.get("purpose_cn") or "手头小册子").strip()[:28]
        next_step = str(proj.get("next_step_cn") or "").strip()[:28]
        entries = proj.get("entries")
        is_long_arc = not next_step or (isinstance(entries, list) and len(entries) >= 2)
        proj_text = (
            f"心里还挂着「{purpose}」，想接着把「{next_step}」那一段慢慢补完"
            if next_step
            else f"心里还惦记着「{purpose}」，想挑段安静时间继续往下推进"
        )
        candidates.append({
            "layer": "long_aspiration" if is_long_arc else "mid_itch",
            "category": "craft_ambition",
            "origin": "self_emerged",
            "world_anchor": f"craft:project:{pid}",
            "text": proj_text[:MAX_DESIRE_TEXT_LEN],
            "intensity": 0.72,
            "estimated_cost_jpy": 0,
            "requires_outing": False,
            "shared": True,
        })

    carton_id = signals.get("unpacked_carton_id")
    if isinstance(carton_id, str) and carton_id:
        candidates.append({
            "layer": "long_aspiration",
            "category": "craft_ambition",
            "origin": "world_spark",
            "world_anchor": f"craft:carton:{carton_id}",
            "text": "玄关还有没拆完的搬家旧纸箱，想把里面的旧物件一件件安顿好",
            "intensity": 0.66,
            "estimated_cost_jpy": 0,
            "requires_outing": False,
            "shared": True,
        })

    arrangement_id = signals.get("pending_arrangement_id")
    if isinstance(arrangement_id, str) and arrangement_id:
        candidates.append({
            "layer": "mid_itch",
            "category": "craft_ambition",
            "origin": "world_spark",
            "world_anchor": f"craft:arrangement:{arrangement_id}",
            "text": "想着把屋里那件物件挪到更顺手的位置，顺便收拾下边角",
            "intensity": 0.64,
            "estimated_cost_jpy": 0,
            "requires_outing": False,
            "shared": True,
        })

    booklet = signals.get("unfinished_booklet")
    if isinstance(booklet, tuple) and len(booklet) == 3:
        b_title, cur_p, _tot_p = booklet
        candidates.append({
            "layer": "long_aspiration",
            "category": "craft_ambition",
            "origin": "self_emerged",
            "world_anchor": f"craft:booklet:{b_title[:48]}",
            "text": f"桌边那本《{b_title[:24]}》才翻到第 {cur_p} 页，想挑个安稳时候慢慢读完",
            "intensity": 0.63,
            "estimated_cost_jpy": 0,
            "requires_outing": False,
            "shared": True,
        })

    # 4. Living town regulars / bookbox / bulletin / mischief / social silence
    bookbox = signals.get("bookbox_spark")
    if isinstance(bookbox, Mapping):
        bid = str(bookbox["book_id"])[:48]
        btitle = str(bookbox["book_title"])[:26]
        candidates.append({
            "layer": "mid_itch",
            "category": "curiosity_mystery",
            "origin": "world_spark",
            "world_anchor": f"town:bookbox:{bid}",
            "text": f"旧书铺漂流书箱里新放了《{btitle}》，有点好奇扉页和页边留了什么字",
            "intensity": 0.68,
            "estimated_cost_jpy": 0,
            "requires_outing": True,
            "shared": True,
        })

    bulletin = signals.get("bulletin_spark")
    if isinstance(bulletin, Mapping):
        nid = str(bulletin["notice_id"])[:48]
        ntitle = str(bulletin["title_cn"])[:26]
        candidates.append({
            "layer": "mid_itch",
            "category": "curiosity_mystery",
            "origin": "world_spark",
            "world_anchor": f"town:bulletin:{nid}",
            "text": f"街角布告栏上关于「{ntitle}」有了新后续，想顺路去瞄一眼",
            "intensity": 0.66,
            "estimated_cost_jpy": 0,
            "requires_outing": True,
            "shared": True,
        })

    regular = signals.get("regular_spark")
    if isinstance(regular, Mapping):
        rid = str(regular["regular_id"])[:40]
        rname = str(regular["name_cn"])[:20]
        candidates.append({
            "layer": "mid_itch",
            "category": "social_longing",
            "origin": "world_spark",
            "world_anchor": f"town:regular:{rid}",
            "text": f"好一阵没去和{rname}打照面了，想顺路拐过去寒暄几句",
            "intensity": 0.64,
            "estimated_cost_jpy": 0,
            "requires_outing": True,
            "shared": True,
        })

    active_game = signals.get("active_game")
    if isinstance(active_game, Mapping):
        gkind = str(active_game.get("game_kind") or "桌面小游戏").strip()[:24]
        candidates.append({
            "layer": "micro_craving",
            "category": "mischief_rivalry",
            "origin": "shared_hint",
            "world_anchor": f"play:game:{gkind}",
            "text": f"刚才的「{gkind}」意犹未尽，想找机会悄悄扳回一局开个小玩笑",
            "intensity": 0.67,
            "estimated_cost_jpy": 0,
            "requires_outing": False,
            "shared": True,
        })
    elif isinstance(signals.get("kitten_spark"), Mapping):
        nick = str(signals["kitten_spark"]["nickname"])[:16]
        candidates.append({
            "layer": "micro_craving",
            "category": "mischief_rivalry",
            "origin": "world_spark",
            "world_anchor": "town:alley_kitten",
            "text": f"想去巷口碰碰运气，看{nick}今天会不会装作不认识又偷偷蹭过来",
            "intensity": 0.62,
            "estimated_cost_jpy": 0,
            "requires_outing": True,
            "shared": True,
        })

    silence_hours = float(signals.get("social_silence_hours") or 0.0)
    if silence_hours >= 18.0:
        candidates.append({
            "layer": "mid_itch",
            "category": "social_longing",
            "origin": "self_emerged",
            "world_anchor": f"social:silence:{date_key}",
            "text": "安静了挺长一阵子，有点惦记对方最近过得怎么样，想找个轻松时候分享件小事",
            "intensity": round(min(0.80, 0.62 + min(24.0, silence_hours - 18.0) * 0.006), 4),
            "estimated_cost_jpy": 0,
            "requires_outing": False,
            "shared": True,
        })

    # 5. S07 affect mild boredom / restlessness / curiosity
    affect = signals.get("affect_signal")
    if isinstance(affect, Mapping):
        mode = str(affect.get("mode") or "curious")
        rec_id = str(affect.get("record_id") or "s07")[:40]
        eff_int = int(affect.get("intensity") or 50)
        cand_intensity = round(min(0.85, 0.56 + eff_int / 260.0), 4)
        if mode == "restless" and not is_rainy:
            candidates.append({
                "layer": "mid_itch",
                "category": "restlessness_outing",
                "origin": "self_emerged",
                "world_anchor": f"affect:restless:{rec_id}",
                "text": "心里有点发闷坐不住，想去巷口或高处眺望台走一圈换换空气",
                "intensity": cand_intensity,
                "estimated_cost_jpy": 0,
                "requires_outing": True,
                "shared": True,
            })
        else:
            candidates.append({
                "layer": "mid_itch",
                "category": "curiosity_mystery",
                "origin": "self_emerged",
                "world_anchor": f"affect:curious:{rec_id}",
                "text": "有点轻度发闷想找点新鲜事琢磨，打算翻翻旧笔记或去书铺淘线索",
                "intensity": cand_intensity,
                "estimated_cost_jpy": 0,
                "requires_outing": False,
                "shared": True,
            })

    return candidates


def tick_desire_lifecycle(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Advance natural decay, TTL fade, and 2+2+1 active cap across BOTH desire stores (zero-LLM).

    - Decays active ``v05_inner_spark.json`` desires by ``decay_per_hour * elapsed_hours``,
      fading items at ``intensity <= 0.08`` or expired TTL with zero frustration debt.
    - Sweeps ``life/everyday_desires.json`` ``turn_only_buffer`` (``created_at + ttl_seconds``)
      and stale/source-lost ``retained_concerns``.
    - Strictly zero-write when neither state file exists or nothing changed.
    """
    now_dt, now_str = _resolve_now_dt(now_iso, strict=True)
    spark_path = state_file_path(day_root)
    spark_exists = (not spark_path.is_symlink()) and spark_path.is_file()

    everyday_path = (
        _everyday_desires.everyday_desire_state_path(day_root)
        if _everyday_desires is not None
        else _instance_paths.resolve_instance_paths(day_root).day_root / "life" / "everyday_desires.json"
    )
    everyday_exists = (not everyday_path.is_symlink()) and everyday_path.is_file()

    if not spark_exists and not everyday_exists:
        return {
            "ok": True,
            "changed": False,
            "reason": "state_absent",
            "spark_changed": False,
            "everyday_changed": False,
            "faded_count": 0,
            "decayed_count": 0,
            "evicted_count": 0,
            "spark_faded_ids": [],
            "spark_decayed_ids": [],
            "spark_evicted_ids": [],
            "everyday_desires_receipt": None,
            "active_counts": {layer: 0 for layer in DESIRE_LAYERS},
            "frustration_debt": 0,
            "llm_calls": 0,
        }

    spark_changed = False
    spark_faded_ids: list[str] = []
    spark_decayed_ids: list[str] = []
    spark_evicted_ids: list[str] = []
    active_counts: dict[str, int] = {layer: 0 for layer in DESIRE_LAYERS}
    latest_desires: list[dict[str, Any]] = []

    if spark_exists:
        with _path_lock(spark_path):
            state, exists_locked = _read_state_zero_write(day_root)
            if exists_locked:
                desires: list[dict[str, Any]] = state["desires"]
                fallback_ts = state.get("updated_at") if isinstance(state.get("updated_at"), str) else None
                for item in desires:
                    if item.get("status") != "active":
                        continue
                    desire_id = str(item.get("desire_id") or "")
                    exp_dt = _parse_iso_dt(item.get("expires_at") or item.get("ttl_until"))
                    if exp_dt is not None and now_dt >= exp_dt:
                        item["status"] = "faded"
                        item["resolution_note"] = item.get("resolution_note") or ZERO_DEBT_FADE_NOTE_CN
                        item["updated_at"] = now_str
                        item["frustration_debt"] = 0
                        spark_faded_ids.append(desire_id)
                        spark_changed = True
                        continue

                    created_dt = _parse_iso_dt(item.get("created_at"))
                    raw_ttl = item.get("ttl_seconds")
                    if (
                        created_dt is not None
                        and _is_strict_int(raw_ttl)
                        and raw_ttl > 0
                        and (now_dt - created_dt).total_seconds() >= raw_ttl
                    ):
                        item["status"] = "faded"
                        item["resolution_note"] = item.get("resolution_note") or ZERO_DEBT_FADE_NOTE_CN
                        item["updated_at"] = now_str
                        item["frustration_debt"] = 0
                        spark_faded_ids.append(desire_id)
                        spark_changed = True
                        continue

                    rate = float(item.get("decay_per_hour", DEFAULT_DECAY_PER_HOUR_BY_LAYER[item["layer"]]))
                    cur_int = float(item.get("intensity", DEFAULT_DESIRE_INTENSITY))
                    if cur_int <= DESIRE_FADE_INTENSITY_THRESHOLD:
                        item["status"] = "faded"
                        item["resolution_note"] = item.get("resolution_note") or ZERO_DEBT_FADE_NOTE_CN
                        item["updated_at"] = now_str
                        item["frustration_debt"] = 0
                        spark_faded_ids.append(desire_id)
                        spark_changed = True
                        continue

                    anchor_dt = _parse_iso_dt(item.get("updated_at") or item.get("created_at") or fallback_ts)
                    if rate > 0.0 and anchor_dt is not None and now_dt > anchor_dt:
                        elapsed_h = (now_dt - anchor_dt).total_seconds() / 3600.0
                        if elapsed_h > 0.0:
                            new_int = round(max(0.0, cur_int - rate * elapsed_h), 4)
                            if new_int <= DESIRE_FADE_INTENSITY_THRESHOLD:
                                item["intensity"] = new_int
                                item["status"] = "faded"
                                item["resolution_note"] = item.get("resolution_note") or ZERO_DEBT_FADE_NOTE_CN
                                item["updated_at"] = now_str
                                item["frustration_debt"] = 0
                                spark_faded_ids.append(desire_id)
                                spark_changed = True
                            elif new_int != round(cur_int, 4):
                                item["intensity"] = new_int
                                item["updated_at"] = now_str
                                spark_decayed_ids.append(desire_id)
                                spark_changed = True

                evicted = _enforce_active_slot_cap_in_place(desires, now_str=now_str)
                if evicted:
                    spark_evicted_ids = [str(ev.get("desire_id") or "") for ev in evicted]
                    spark_changed = True

                active_counts = _active_counts_by_layer(desires)
                latest_desires = copy.deepcopy(desires)
                if spark_changed:
                    state["last_desire_tick_at"] = now_str
                    _commit_state(day_root, state, now_str)

    everyday_rep: Optional[dict[str, Any]] = None
    everyday_changed = False
    sync_changed = False
    if everyday_exists and _everyday_desires is not None:
        sweep_fn = getattr(_everyday_desires, "sweep_everyday_desires_ttl_and_decay", None)
        if callable(sweep_fn):
            everyday_rep = sweep_fn(day_root, now_iso=now_str)
            everyday_changed = bool(isinstance(everyday_rep, Mapping) and everyday_rep.get("changed"))
        if spark_changed:
            sync_rep = _sync_everyday_projection_if_present(day_root, latest_desires, now_str=now_str)
            sync_changed = bool(isinstance(sync_rep, Mapping) and sync_rep.get("changed"))

    ed_faded = int((everyday_rep or {}).get("faded_count") or 0)
    ed_decayed = int((everyday_rep or {}).get("decayed_count") or 0)
    total_faded = len(spark_faded_ids) + len(spark_evicted_ids) + ed_faded
    total_decayed = len(spark_decayed_ids) + ed_decayed

    return {
        "ok": True,
        "changed": bool(spark_changed or everyday_changed or sync_changed),
        "reason": "ticked" if (spark_changed or everyday_changed or sync_changed) else "no_change",
        "spark_changed": spark_changed,
        "everyday_changed": bool(everyday_changed or sync_changed),
        "faded_count": total_faded,
        "decayed_count": total_decayed,
        "evicted_count": len(spark_evicted_ids),
        "spark_faded_ids": spark_faded_ids,
        "spark_decayed_ids": spark_decayed_ids,
        "spark_evicted_ids": spark_evicted_ids,
        "everyday_desires_receipt": everyday_rep,
        "active_counts": active_counts,
        "frustration_debt": 0,
        "llm_calls": 0,
    }


def emerge_autonomous_desires(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Deterministically emerge bounded full-spectrum non-sexual desires from live world/body state.

    Zero-LLM, idempotent & deduped by ``world_anchor`` + cooldown so repeated 60s calls never
    flood or churn disk. Strictly enforces Ch. 130 2+2+1 active caps and syncs a clean
    read-compatible projection into ``life/everyday_desires.json`` when that file exists.
    """
    now_dt, now_str = _resolve_now_dt(now_iso, strict=True)
    pre_state, _ = _read_state_zero_write(day_root)
    barrier = _load_unified_forget_barrier_zero_write(day_root)
    signals = _read_live_desire_signals_zero_write(day_root, now_dt, pre_state, barrier=barrier)
    raw_candidates = _build_autonomous_desire_candidates(signals, now_dt)
    if (barrier.get("barrier_active") or barrier.get("fail_closed")) and _rlf is not None:
        raw_candidates = _rlf.filter_collection_by_barrier(raw_candidates, barrier)

    if not raw_candidates:
        return {
            "ok": True,
            "changed": False,
            "reason": "sleeping_suppressed" if signals.get("sleeping_active") else "no_triggers",
            "emerged": [],
            "emerged_count": 0,
            "evicted_desires": [],
            "skipped_anchors": [],
            "active_counts": _active_counts_by_layer(pre_state["desires"]),
            "frustration_debt": 0,
            "llm_calls": 0,
        }

    # Sort deterministically per layer by descending intensity, then anchor
    sorted_candidates = sorted(
        raw_candidates,
        key=lambda c: (
            DESIRE_LAYERS.index(str(c["layer"])),
            -float(c["intensity"]),
            str(c["world_anchor"]),
        ),
    )

    path = state_file_path(day_root)
    with _path_lock(path):
        state, _ = _read_state_zero_write(day_root)
        desires: list[dict[str, Any]] = state["desires"]
        cooldowns: dict[str, str] = dict(state.get("emerge_anchor_cooldowns") or {})

        active_anchors: set[str] = set()
        recent_settled_anchors: set[str] = set()
        for existing in desires:
            anc = existing.get("world_anchor")
            if not isinstance(anc, str) or not anc.strip():
                continue
            anc_clean = anc.strip()
            if existing.get("status") == "active":
                active_anchors.add(anc_clean)
            else:
                upd_dt = _parse_iso_dt(existing.get("updated_at") or existing.get("created_at"))
                if upd_dt is not None and (now_dt - upd_dt).total_seconds() < EMERGE_ANCHOR_COOLDOWN_SECONDS:
                    recent_settled_anchors.add(anc_clean)

        # Also check explicit cooldown map
        for anc_k, ts_v in list(cooldowns.items()):
            cd_dt = _parse_iso_dt(ts_v)
            if cd_dt is None or (now_dt - cd_dt).total_seconds() >= EMERGE_ANCHOR_COOLDOWN_SECONDS:
                cooldowns.pop(anc_k, None)

        emerged: list[dict[str, Any]] = []
        evicted_all: list[dict[str, Any]] = []
        skipped_anchors: list[str] = []

        for cand in sorted_candidates:
            anchor = str(cand["world_anchor"]).strip()[:MAX_WORLD_ANCHOR_LEN]
            layer = str(cand["layer"])
            cand_intensity = round(max(0.0, min(1.0, float(cand["intensity"]))), 4)

            if anchor in active_anchors or anchor in recent_settled_anchors or anchor in cooldowns:
                skipped_anchors.append(anchor)
                continue

            # Avoid thrashing a full layer unless candidate is genuinely stronger (+0.10)
            cap = int(ACTIVE_CAP_BY_LAYER.get(layer, 2))
            layer_active = [d for d in desires if d.get("status") == "active" and d.get("layer") == layer]
            if len(layer_active) >= cap:
                min_active_int = min(
                    float(d.get("intensity", DEFAULT_DESIRE_INTENSITY)) for d in layer_active
                )
                if cand_intensity < min_active_int + 0.10:
                    skipped_anchors.append(anchor)
                    continue

            seq = int(state.get("revision", 0)) + 1 + len(emerged)
            desire_id = _stable_id("des", seq, layer, anchor, now_str, len(desires))
            item: dict[str, Any] = {
                "desire_id": desire_id,
                "layer": layer,
                "category": str(cand["category"]),
                "text": str(cand["text"])[:MAX_DESIRE_TEXT_LEN],
                "origin": str(cand.get("origin") or "self_emerged"),
                "world_anchor": anchor,
                "intensity": cand_intensity,
                "decay_per_hour": DEFAULT_DECAY_PER_HOUR_BY_LAYER[layer],
                "friction_count": 0,
                "estimated_cost_jpy": max(0, int(cand.get("estimated_cost_jpy") or 0)),
                "requires_outing": bool(cand.get("requires_outing", False)),
                "status": "active",
                "shared": False if str(cand["category"]) == "intimate_warmth" else bool(cand.get("shared", True)),
                "created_at": now_str,
                "updated_at": now_str,
                "partner_note": None,
                "partner_note_at": None,
                "resolution_note": None,
                "frustration_debt": 0,
            }
            desires.append(item)
            active_anchors.add(anchor)
            cooldowns[anchor] = now_str
            evicted = _enforce_active_slot_cap_in_place(
                desires,
                now_str=now_str,
                layer=layer,
                protected_id=desire_id,
            )
            evicted_all.extend(evicted)
            emerged.append(copy.deepcopy(item))

        if not emerged:
            return {
                "ok": True,
                "changed": False,
                "reason": "deduped_or_capped",
                "emerged": [],
                "emerged_count": 0,
                "evicted_desires": [],
                "skipped_anchors": skipped_anchors,
                "active_counts": _active_counts_by_layer(desires),
                "frustration_debt": 0,
                "llm_calls": 0,
            }

        state["emerge_anchor_cooldowns"] = dict(list(cooldowns.items())[-MAX_ANCHOR_COOLDOWNS:])
        state["last_emerge_at"] = now_str
        _trim_desires_history_in_place(state)
        _commit_state(day_root, state, now_str)
        sync_rep = _sync_everyday_projection_if_present(day_root, state["desires"], now_str=now_str)

        return {
            "ok": True,
            "changed": True,
            "reason": "emerged",
            "emerged": emerged,
            "emerged_count": len(emerged),
            "evicted_desires": evicted_all,
            "skipped_anchors": skipped_anchors,
            "active_counts": _active_counts_by_layer(state["desires"]),
            "everyday_sync": sync_rep,
            "revision": state["revision"],
            "frustration_debt": 0,
            "llm_calls": 0,
        }


__all__ = [
    "ACTIVE_CAP_BY_LAYER",
    "BOOK_ITEM_KINDS",
    "DEFAULT_DECAY_PER_HOUR_BY_LAYER",
    "DEFAULT_DESIRE_CATEGORY",
    "DEFAULT_DESIRE_INTENSITY",
    "DEFAULT_TZ",
    "DESIRE_CATEGORIES",
    "DESIRE_FADE_INTENSITY_THRESHOLD",
    "DESIRE_LAYERS",
    "DESIRE_LAYER_LABELS_CN",
    "DESIRE_ORIGINS",
    "DESIRE_STATUSES",
    "DREAM_CHANCE_BPS",
    "EMERGE_ANCHOR_COOLDOWN_SECONDS",
    "FORBIDDEN_IDENTITY_KEYS",
    "FROST_MELTED_THRESHOLD",
    "MAX_CHEMISTRY_SPARKS",
    "MAX_DESIRE_TEXT_LEN",
    "MAX_FORESHADOW_NOTE_LEN",
    "MAX_PARTNER_NOTE_LEN",
    "MAX_WINDOW_FROST_TEXT_LEN",
    "OBJECT_CHEMISTRY_KINDS",
    "RESOLVABLE_DESIRE_STATUSES",
    "SLEEP_PHASES",
    "STATE_REL_PATH",
    "STATE_SCHEMA",
    "V05InnerSparkError",
    "ZERO_DEBT_FADE_NOTE_CN",
    "advance_inner_spark_heartbeat",
    "advance_reading_page",
    "emerge_autonomous_desires",
    "keep_or_share_dream",
    "project_v05_inner_spark_state",
    "project_window_frost",
    "record_desire_friction",
    "record_subject_desire",
    "resolve_subject_desire",
    "respond_to_desire",
    "settle_morning_dream_pulse",
    "sketch_window_frost",
    "state_file_path",
    "tick_desire_lifecycle",
    "trigger_object_chemistry",
    "tuck_page_note",
]

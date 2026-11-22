#!/usr/bin/env python3
"""0.5 Home Craft, Patina, Negotiation, Moving Carton, Postcards & Zero-Text Draft Signal Engine.

Implements Ch. 67, 98, 103, 106, 110 (#2), and 112 in pure standard-library Python
(zero LLM calls, 100% zero-write on GET, atomic LF UTF-8 JSON on POST):

1. Ch. 67 Object Patina & Repair (`object_patina`): qualitative lived history
   for starter room objects & unpacked keepsakes (zero numeric wear/durability).
2. Ch. 98 + 103 Gentle Home Arrangement & Compromise (`home_arrangement`):
   propose moving an object across 5 room zones without forced teleporting,
   then settle via accept, middle compromise, or gentle keep-current.
3. Ch. 106 Entryway Moving Carton (`moving_carton`): pack keepsakes from a
   former town and unpack them one by one into chosen room zones, automatically
   promoting each unpacked item into `object_patina`.
4. Ch. 112 Parallel-World PNG Postcards (`parallel_postcards`): pure-Python
   160x104 seasonal sky+window+lamp PNG renderer wired directly to
   `v05_unified_world_fabric.embed_postcard_png_chunk` and
   `extract_postcard_png_chunk` with sanitized metadata only.
5. Ch. 110 #2 Zero-Text Crumpled Draft Signal (`crumpled_draft_signal`):
   partner-only privacy-preserving disposal counter (`wastebasket` or
   `winter_hearth`) storing strictly timestamp/kind/count/date and zero text.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import os
import re
import secrets
import struct
import sys
import unicodedata
import zlib
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_WORLD_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _WORLD_SCRIPTS_DIR.parents[2]
for _p in (str(_REPO_ROOT), str(_WORLD_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from components.world.scripts import v05_unified_world_fabric as fabric
except ImportError:  # pragma: no cover
    import v05_unified_world_fabric as fabric  # type: ignore[no-redef]

try:
    from components.world.scripts import v05_season_solar_pulse as season_pulse
except ImportError:  # pragma: no cover
    import v05_season_solar_pulse as season_pulse  # type: ignore[no-redef]

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.life.scripts import state_tx_guard as _tx
except ImportError:  # pragma: no cover
    _LIFE_SCRIPTS_DIR = _REPO_ROOT / "components" / "life" / "scripts"
    if str(_LIFE_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_LIFE_SCRIPTS_DIR))
    import state_tx_guard as _tx  # type: ignore[no-redef]


STATE_SCHEMA = "world.v05.home_craft.v1"
POSTCARD_SCHEMA = "nyairo.v05.postcard.v1"
STATE_REL_PATH: tuple[str, ...] = ("world", "v05_home_craft.json")
DEFAULT_TZ = timezone(timedelta(hours=8))

MAX_PATINA_OBJECTS = 32
MAX_PATINA_NOTES = 16
MAX_ARRANGEMENT_PROPOSALS = 24
MAX_MOVING_CARTONS = 8
MAX_CARTON_ITEMS = 12
MAX_POSTCARDS = 24
MAX_POSTCARD_PNG_BYTES = 256 * 1024
MAX_POSTCARD_B64_CHARS = 360 * 1024
MAX_CRUMPLED_OP_KEYS = 32

VALID_DISPOSAL_KINDS: tuple[str, ...] = (
    "wastebasket",
    "winter_hearth",
)
VALID_DISPOSAL_KIND_SET: frozenset[str] = frozenset(VALID_DISPOSAL_KINDS)

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_POSTCARD_META_KEYWORD = b"nyairo_postcard_meta\x00"
_STAMP_RE = re.compile(r"^(spr|sum|aut|win)-(\d{2})-(\d{4}-\d{2}-\d{2})$")

VALID_MICRO_ZONES: tuple[str, ...] = (
    "desk",
    "window_sill",
    "bedside",
    "hearth_table",
    "entryway_shelf",
)
VALID_MICRO_ZONE_SET: frozenset[str] = frozenset(VALID_MICRO_ZONES)

MICRO_ZONE_LABELS_CN: dict[str, str] = {
    "desk": "书桌边",
    "window_sill": "窗台沿",
    "bedside": "床头角",
    "hearth_table": "餐桌暖灯旁",
    "entryway_shelf": "玄关木架",
}

PATINA_EVENT_LABELS_CN: dict[str, str] = {
    "patina": "日用包浆",
    "mended": "细心修补",
    "gifted": "收作纪念",
    "retired_gently": "温柔退役",
}
VALID_PATINA_EVENT_KINDS: frozenset[str] = frozenset(PATINA_EVENT_LABELS_CN.keys())

VALID_COMPROMISE_DECISIONS: frozenset[str] = frozenset({
    "accept_as_proposed",
    "compromise_middle",
    "keep_current_gently",
})

_THEME_DEFAULT_TOWN_NAMES: dict[str, str] = {
    "coastal": "海坡小镇",
    "mountain": "山腰木檐镇",
    "residential": "安静巷尾镇",
    "rural": "田埂瓦檐镇",
    "urban": "老洋楼街区",
}

_SEASON_GIFT_SLOT: dict[str, str] = {
    "spring": "wild_mountain_tea",
    "summer": "sea_glass_shard",
    "autumn": "pressed_osmanthus",
    "winter": "wild_mountain_tea",
}

_SEASON_PREFIX_MAP: dict[str, str] = {
    "spr": "spring",
    "sum": "summer",
    "aut": "autumn",
    "win": "winter",
}

STARTER_OBJECT_IDS: frozenset[str] = frozenset({
    "ceramic_mug",
    "warm_desk_lamp",
    "linen_throw",
    "window_plant_pot",
    "wooden_chair",
    "fountain_pen",
})


class V05HomeCraftError(Exception):
    """Domain/validation error carrying HTTP status code and machine reason code."""

    def __init__(self, status_code: int, reason_code: str, detail: str = "") -> None:
        super().__init__(detail or reason_code)
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail or reason_code)
        self.message = self.detail


def _strip_emojis_and_ctrl_unbounded(text: str) -> str:
    cleaned_chars: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "So" or cat.startswith("C"):
            continue
        cleaned_chars.append(ch)
    return "".join(cleaned_chars).strip()


def _validate_clean_text(
    raw: Any,
    *,
    field_name: str,
    min_len: int,
    max_len: int,
) -> str:
    if raw is None:
        if min_len > 0:
            raise V05HomeCraftError(400, "INVALID_FIELDS", f"Field {field_name!r} is required")
        return ""
    if not isinstance(raw, str):
        raise V05HomeCraftError(400, "INVALID_FIELDS", f"Field {field_name!r} must be a string")
    cleaned = _strip_emojis_and_ctrl_unbounded(raw)
    if len(cleaned) < min_len:
        raise V05HomeCraftError(
            400,
            "INVALID_FIELDS",
            f"Field {field_name!r} must be at least {min_len} characters",
        )
    if len(cleaned) > max_len:
        raise V05HomeCraftError(
            400,
            "INVALID_FIELDS",
            f"Field {field_name!r} must be at most {max_len} characters",
        )
    return cleaned


def _validate_operation_key(operation_key: Any) -> Optional[str]:
    if operation_key is None:
        return None
    if not isinstance(operation_key, str):
        raise V05HomeCraftError(400, "INVALID_FIELDS", "Field 'operation_key' must be a string")
    if len(operation_key.strip()) > 128:
        raise V05HomeCraftError(400, "INVALID_FIELDS", "Field 'operation_key' is too long")
    cleaned = _strip_emojis_and_ctrl_unbounded(operation_key)
    return cleaned or None


def _normalize_author_side(actor_side: Any) -> str:
    norm = str(actor_side or "partner").strip().lower()
    if norm == "her":
        norm = "subject"
    if norm not in ("partner", "subject"):
        raise V05HomeCraftError(403, "FORBIDDEN_ACTOR_SIDE", f"Unsupported actor_side: {actor_side!r}")
    return norm


def _validate_zone(raw_zone: Any, *, field_name: str = "target_zone") -> str:
    if not isinstance(raw_zone, str):
        raise V05HomeCraftError(400, "INVALID_ZONE", f"Field {field_name!r} must be a valid room zone")
    z = raw_zone.strip().lower()
    if z not in VALID_MICRO_ZONE_SET:
        raise V05HomeCraftError(
            400,
            "INVALID_ZONE",
            f"Field {field_name!r} must be one of {VALID_MICRO_ZONES}, got {raw_zone!r}",
        )
    return z


def _parse_now(now_iso: Optional[str]) -> datetime:
    return season_pulse.parse_iso_to_default_tz(now_iso)


def _format_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _state_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*STATE_REL_PATH)


def _read_json_safe(path: Path) -> Optional[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _assert_no_symlink_chain(day_root: Path, target_file: Path) -> None:
    if day_root.exists() and day_root.is_symlink():
        raise V05HomeCraftError(400, "UNSAFE_SYMLINK", "day_root must not be a symlink")
    parent = target_file.parent
    if parent.exists() and parent.is_symlink():
        raise V05HomeCraftError(400, "UNSAFE_SYMLINK", "world directory must not be a symlink")
    if target_file.exists() and target_file.is_symlink():
        raise V05HomeCraftError(400, "UNSAFE_SYMLINK", "state file must not be a symlink")


def _atomic_write_json(day_root: Path, target_file: Path, payload: Mapping[str, Any]) -> None:
    _assert_no_symlink_chain(day_root, target_file)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    raw_bytes = serialized.replace("\r\n", "\n").encode("utf-8")
    tmp_name = f".{target_file.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    tmp_path = target_file.parent / tmp_name
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(raw_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target_file)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _normalize_theme(raw: Any) -> str:
    if isinstance(raw, str):
        low = raw.strip().lower()
        for t in ("coastal", "mountain", "residential", "rural", "urban"):
            if low == t or low.startswith(f"{t}_") or low.endswith(f"_{t}"):
                return t
        if "coast" in low or "sea" in low or "harbor" in low:
            return "coastal"
        if "mount" in low or "hill" in low or "valley" in low:
            return "mountain"
        if "rural" in low or "village" in low or "farm" in low:
            return "rural"
        if "urban" in low or "city" in low or "metro" in low:
            return "urban"
        if "resid" in low or "suburb" in low or "alley" in low:
            return "residential"
    return "coastal"


def _extract_explicit_v04_season(env: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not isinstance(env, Mapping):
        return None
    norm = season_pulse.normalize_season(env.get("season"))
    if norm is None:
        return None
    if env.get("season_explicit") is True:
        return norm
    if env.get("season_explicit") is False:
        return None
    if env.get("updated_at") is not None:
        return norm
    if "season_explicit" not in env:
        return norm
    return None


def _extract_world_context_zero_write(day_root: Path, now_dt: datetime) -> dict[str, Any]:
    paths = _instance_paths.resolve_instance_paths(day_root)
    canonical_root = paths.day_root
    world_doc = (
        _read_json_safe(paths.world_state_json)
        or _read_json_safe(canonical_root / "world" / "world_state.json")
        or _read_json_safe(canonical_root / "data" / "world" / "world_state.json")
        or _read_json_safe(canonical_root / "world_state.json")
    )
    nb_doc = _read_json_safe(canonical_root / "world" / "v04_neighborhood_state.json")
    canonical_loc = _instance_paths.read_canonical_world_location_zero_write(canonical_root)

    weather = "clear"
    explicit_season: Optional[str] = None
    theme = "coastal"
    theme_resolved = False
    town_name_cn = ""

    if isinstance(nb_doc, Mapping):
        env = nb_doc.get("environment") if isinstance(nb_doc.get("environment"), Mapping) else {}
        if env.get("weather"):
            weather = str(env["weather"]).strip().lower()
        nb_season = _extract_explicit_v04_season(env)
        if nb_season is not None:
            explicit_season = nb_season

    if isinstance(world_doc, Mapping):
        env_w = world_doc.get("environment") if isinstance(world_doc.get("environment"), Mapping) else {}
        for cand in (world_doc.get("weather"), env_w.get("weather")):
            if isinstance(cand, str) and cand.strip():
                weather = cand.strip().lower()
                break
        for cand in (world_doc.get("season"), env_w.get("season")):
            norm_s = season_pulse.normalize_season(cand)
            if norm_s is not None:
                explicit_season = norm_s
                break
        for cand in (
            world_doc.get("world_theme"),
            world_doc.get("theme"),
            world_doc.get("town_theme"),
            world_doc.get("setting_theme"),
            env_w.get("theme"),
        ):
            if isinstance(cand, str) and cand.strip():
                theme = _normalize_theme(cand)
                theme_resolved = True
                break
        for cand in (world_doc.get("town_name_cn"), env_w.get("town_name_cn")):
            if isinstance(cand, str) and cand.strip():
                town_name_cn = _strip_emojis_and_ctrl_unbounded(cand)[:30]
                break

    if (not theme_resolved or not town_name_cn) and paths.world_package_dir is not None:
        pkg_world = _read_json_safe(paths.world_package_dir / "world.json")
        if isinstance(pkg_world, Mapping):
            if not theme_resolved:
                raw_pkg_theme = pkg_world.get("theme") or pkg_world.get("world_theme")
                if isinstance(raw_pkg_theme, str) and raw_pkg_theme.strip():
                    theme = _normalize_theme(raw_pkg_theme)
            if not town_name_cn:
                raw_pkg_name = pkg_world.get("name") or pkg_world.get("town_name_cn")
                if isinstance(raw_pkg_name, str) and raw_pkg_name.strip():
                    town_name_cn = _strip_emojis_and_ctrl_unbounded(raw_pkg_name)[:30]

    if not town_name_cn:
        town_name_cn = _THEME_DEFAULT_TOWN_NAMES.get(theme, "海坡小镇")

    pulse = season_pulse.project_season_solar_pulse(
        now_iso=now_dt,
        explicit_season=explicit_season,
        weather=weather,
    )
    return {
        "world_state_found": world_doc is not None,
        "theme": theme,
        "town_name_cn": town_name_cn,
        "weather": weather,
        "explicit_season": explicit_season,
        "season": str(pulse["season"]),
        "season_solar": pulse,
        "canonical_location": canonical_loc,
        "hour": int(now_dt.hour),
        "date_str": now_dt.strftime("%Y-%m-%d"),
    }


def _starter_object_patina() -> list[dict[str, Any]]:
    rows = [
        ("ceramic_mug", "粗陶茶杯", "desk", "刚搬进小屋就一直在用的日常物件"),
        ("warm_desk_lamp", "书桌暖灯", "desk", "傍晚最早拧亮的一盏小灯"),
        ("linen_throw", "亚麻薄毯", "bedside", "叠在床尾或椅背上的旧亚麻毯"),
        ("window_plant_pot", "窗台素陶盆", "window_sill", "放在窗台晒斜阳的素烧花盆"),
        ("wooden_chair", "旧木椅", "hearth_table", "木纹磨得温润的餐桌靠背椅"),
        ("fountain_pen", "常用钢笔", "desk", "写便签和记账常顺手拿起的钢笔"),
    ]
    out: list[dict[str, Any]] = []
    for oid, name_cn, zone, origin_cn in rows:
        out.append({
            "object_id": oid,
            "name_cn": name_cn,
            "micro_zone": zone,
            "micro_zone_label_cn": MICRO_ZONE_LABELS_CN[zone],
            "retired": False,
            "origin_cn": origin_cn,
            "patina_notes": [],
            "updated_at": None,
        })
    return out


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "revision": 0,
        "updated_at": None,
        "object_patina": _starter_object_patina(),
        "home_arrangement": {
            "proposals": [],
        },
        "moving_carton": {
            "cartons": [],
        },
        "parallel_postcards": {
            "sent": [],
            "received": [],
        },
        "crumpled_draft_signal": None,
        "crumpled_draft_op_keys": [],
    }


def _load_state_zero_write(day_root: Path) -> tuple[dict[str, Any], bool]:
    raw = _read_json_safe(_state_path(day_root))
    base = _default_state()
    if not isinstance(raw, Mapping):
        return base, False

    merged = copy.deepcopy(base)
    merged["revision"] = int(raw.get("revision") or 0)
    merged["updated_at"] = raw.get("updated_at")

    if isinstance(raw.get("object_patina"), list):
        objs_out: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for row in raw["object_patina"]:
            if not isinstance(row, Mapping) or not row.get("object_id") or not row.get("name_cn"):
                continue
            oid = str(row["object_id"])
            if oid in seen_ids:
                continue
            seen_ids.add(oid)
            zone = str(row.get("micro_zone") or "desk")
            if zone not in VALID_MICRO_ZONE_SET:
                zone = "desk"
            notes_raw = row.get("patina_notes")
            notes = copy.deepcopy(notes_raw[-MAX_PATINA_NOTES:]) if isinstance(notes_raw, list) else []
            objs_out.append({
                "object_id": oid,
                "name_cn": str(row["name_cn"]),
                "micro_zone": zone,
                "micro_zone_label_cn": MICRO_ZONE_LABELS_CN[zone],
                "retired": bool(row.get("retired", False)),
                "origin_cn": str(row.get("origin_cn") or ""),
                "patina_notes": notes,
                "updated_at": row.get("updated_at"),
            })
        if objs_out:
            merged["object_patina"] = objs_out[:MAX_PATINA_OBJECTS]

    if isinstance(raw.get("home_arrangement"), Mapping):
        ha = raw["home_arrangement"]
        if isinstance(ha.get("proposals"), list):
            merged["home_arrangement"]["proposals"] = copy.deepcopy(
                ha["proposals"][-MAX_ARRANGEMENT_PROPOSALS:]
            )

    if isinstance(raw.get("moving_carton"), Mapping):
        mc = raw["moving_carton"]
        if isinstance(mc.get("cartons"), list):
            merged["moving_carton"]["cartons"] = copy.deepcopy(
                mc["cartons"][-MAX_MOVING_CARTONS:]
            )

    if isinstance(raw.get("parallel_postcards"), Mapping):
        pp = raw["parallel_postcards"]
        if isinstance(pp.get("sent"), list):
            merged["parallel_postcards"]["sent"] = copy.deepcopy(pp["sent"][-MAX_POSTCARDS:])
        if isinstance(pp.get("received"), list):
            merged["parallel_postcards"]["received"] = copy.deepcopy(pp["received"][-MAX_POSTCARDS:])

    raw_cds = raw.get("crumpled_draft_signal")
    if isinstance(raw_cds, Mapping):
        dk = str(raw_cds.get("disposal_kind") or "wastebasket").strip().lower()
        if dk not in VALID_DISPOSAL_KIND_SET:
            dk = "wastebasket"
        raw_cnt = raw_cds.get("crumpled_count_today")
        cnt = (
            int(raw_cnt)
            if isinstance(raw_cnt, int) and not isinstance(raw_cnt, bool) and raw_cnt >= 0
            else 0
        )
        raw_date = str(raw_cds.get("date") or "").strip()
        raw_ts = raw_cds.get("last_crumpled_at")
        ts_str = str(raw_ts).strip() if isinstance(raw_ts, str) and raw_ts.strip() else None
        if raw_date or ts_str is not None:
            merged["crumpled_draft_signal"] = {
                "last_crumpled_at": ts_str,
                "disposal_kind": dk,
                "crumpled_count_today": cnt,
                "date": raw_date,
            }

    raw_op_keys = raw.get("crumpled_draft_op_keys")
    if isinstance(raw_op_keys, list):
        merged["crumpled_draft_op_keys"] = [
            str(k).strip()
            for k in raw_op_keys[-MAX_CRUMPLED_OP_KEYS:]
            if isinstance(k, str) and k.strip()
        ]

    return merged, True


def _commit_state(day_root: Path, state: dict[str, Any], now_iso: str) -> None:
    target = _state_path(day_root)
    _assert_no_symlink_chain(day_root, target)
    with _tx.state_file_lock(target):
        state["revision"] = int(state.get("revision", 0)) + 1
        state["updated_at"] = now_iso
        _atomic_write_json(day_root, target, state)


def _emit_craft_lived_episode(
    day_root: Path,
    *,
    episode_key: str,
    topic: str,
    exact_detail: str,
    gist_summary: str,
    occurred_at: str,
    emotional_salience: float = 0.50,
    affect_kind: Optional[str] = None,
    affect_intensity: int = 40,
    shared: bool = True,
    source_refs: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """Best-effort canonical memory sink hook for home craft milestones (write-path only)."""
    try:
        try:
            from components.life.scripts import lived_memory_sink as _lms
        except ImportError:
            _life_dir = str(_REPO_ROOT / "components" / "life" / "scripts")
            if _life_dir not in sys.path:
                sys.path.insert(0, _life_dir)
            import lived_memory_sink as _lms  # type: ignore[no-redef]
        ev = _lms.LivedEpisodeEvent(
            episode_key=episode_key,
            domain="v05_craft",
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


def _find_object(state: Mapping[str, Any], object_id: str) -> Optional[dict[str, Any]]:
    for row in state.get("object_patina", []):
        if isinstance(row, dict) and row.get("object_id") == object_id:
            return row
    return None


def _pick_middle_zone(from_zone: str, target_zone: str) -> str:
    for cand in ("hearth_table", "window_sill", "desk", "entryway_shelf", "bedside"):
        if cand != from_zone and cand != target_zone:
            return cand
    return target_zone


# ---------------------------------------------------------------------------
# Pure-Python Minimal Valid PNG Generator & Chunk Utilities (Ch. 112)
# ---------------------------------------------------------------------------


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def render_seasonal_postcard_png(
    *,
    season: str = "autumn",
    weather: str = "clear",
    hour: int = 16,
    width: int = 160,
    height: int = 104,
) -> bytes:
    """Generate a valid minimal RGB PNG (160x104) with seasonal sky, window sash & warm lamp."""
    w = max(32, min(320, int(width)))
    h = max(32, min(240, int(height)))
    night = hour >= 19 or hour < 6
    rainy = str(weather or "").strip().lower() in (
        "rain",
        "heavy_rain",
        "shower",
        "drizzle",
        "humid_plum_rain",
        "sea_gale",
        "thunder_blackout",
    )

    # Seasonal sky top -> horizon RGB palettes
    palettes: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
        "spring": ((156, 204, 232), (242, 224, 214)),
        "summer": ((98, 172, 232), (218, 238, 248)),
        "autumn": ((124, 152, 198), (244, 194, 138)),
        "winter": ((126, 146, 174), (218, 224, 234)),
    }
    sky_top, sky_bot = palettes.get(season, palettes["autumn"])
    if night:
        sky_top = (28, 38, 62)
        sky_bot = (64, 78, 108)
    elif rainy:
        sky_top = tuple(max(40, int(c * 0.72)) for c in sky_top)  # type: ignore[assignment]
        sky_bot = tuple(max(60, int(c * 0.78)) for c in sky_bot)  # type: ignore[assignment]

    wall_rgb = (68, 56, 48) if night else (112, 92, 76)
    sash_rgb = (54, 42, 34)
    sill_rgb = (138, 110, 84)
    lamp_cx = int(w * 0.78)
    lamp_cy = int(h * 0.54)
    lamp_r2 = (int(h * 0.24)) ** 2

    wx0, wx1 = int(w * 0.12), int(w * 0.58)
    wy0, wy1 = int(h * 0.14), int(h * 0.74)
    mid_wx = (wx0 + wx1) // 2
    mid_wy = (wy0 + wy1) // 2

    rows: list[bytes] = []
    for y in range(h):
        row = bytearray(1 + w * 3)
        row[0] = 0  # PNG filter type 0 (None)
        t_sky = max(0.0, min(1.0, (y - wy0) / max(1, (wy1 - wy0))))
        sky_r = int(sky_top[0] + (sky_bot[0] - sky_top[0]) * t_sky)
        sky_g = int(sky_top[1] + (sky_bot[1] - sky_top[1]) * t_sky)
        sky_b = int(sky_top[2] + (sky_bot[2] - sky_top[2]) * t_sky)
        for x in range(w):
            in_win = wx0 <= x < wx1 and wy0 <= y < wy1
            on_frame = (
                (wx0 - 3 <= x < wx1 + 3 and wy0 - 3 <= y < wy1 + 3) and not in_win
            ) or (in_win and (abs(x - mid_wx) <= 1 or abs(y - mid_wy) <= 1))
            on_sill = (wx0 - 6 <= x < wx1 + 6) and (wy1 + 3 <= y < wy1 + 9)

            if on_sill:
                r, g, b = sill_rgb
            elif on_frame:
                r, g, b = sash_rgb
            elif in_win:
                r, g, b = sky_r, sky_g, sky_b
                if rainy and ((x + y) % 13 == 0) and (y % 4 != 0):
                    r = min(255, r + 26)
                    g = min(255, g + 26)
                    b = min(255, b + 32)
            else:
                r, g, b = wall_rgb
                dx = x - lamp_cx
                dy = y - lamp_cy
                d2 = dx * dx + dy * dy
                if d2 < lamp_r2:
                    glow = 1.0 - (d2 / max(1, lamp_r2))
                    r = min(255, int(r + 175 * glow))
                    g = min(255, int(g + 132 * glow))
                    b = min(255, int(b + 58 * glow))
                if abs(x - lamp_cx) <= 2 and lamp_cy + 4 <= y <= int(h * 0.84):
                    r, g, b = (82, 64, 50)
                elif abs(x - lamp_cx) <= 10 and int(h * 0.84) <= y <= int(h * 0.88):
                    r, g, b = (92, 74, 58)
            off = 1 + x * 3
            row[off] = r
            row[off + 1] = g
            row[off + 2] = b
        rows.append(bytes(row))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"".join(rows), level=6)
    return _PNG_SIG + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _insert_companion_meta_chunk(png_bytes: bytes, metadata: Mapping[str, Any]) -> bytes:
    iend_pos = png_bytes.rfind(b"\x00\x00\x00\x00IEND")
    if iend_pos < 0:
        raise V05HomeCraftError(500, "POSTCARD_ENCODE_ERROR", "Missing PNG IEND chunk")
    payload = json.dumps(dict(metadata), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    chunk = _png_chunk(b"tEXt", _POSTCARD_META_KEYWORD + payload)
    return png_bytes[:iend_pos] + chunk + png_bytes[iend_pos:]


def _verify_png_and_extract_companion_meta(raw_bytes: bytes) -> Optional[dict[str, Any]]:
    """Verify full PNG signature, chunk bounds, and CRC32 for every chunk; return optional meta dict."""
    if not raw_bytes.startswith(_PNG_SIG):
        raise V05HomeCraftError(400, "INVALID_POSTCARD_PNG", "Bytes do not start with PNG signature")
    if len(raw_bytes) > MAX_POSTCARD_PNG_BYTES:
        raise V05HomeCraftError(
            400,
            "INVALID_POSTCARD_PNG",
            f"PNG exceeds maximum allowed size ({MAX_POSTCARD_PNG_BYTES} bytes)",
        )
    pos = 8
    saw_ihdr = False
    saw_iend = False
    companion_meta: Optional[dict[str, Any]] = None

    while pos < len(raw_bytes):
        if pos + 12 > len(raw_bytes):
            raise V05HomeCraftError(400, "INVALID_POSTCARD_PNG", "Truncated PNG chunk header")
        length = struct.unpack(">I", raw_bytes[pos : pos + 4])[0]
        end_pos = pos + 12 + length
        if end_pos > len(raw_bytes):
            raise V05HomeCraftError(400, "INVALID_POSTCARD_PNG", "Truncated PNG chunk payload")
        ctype = raw_bytes[pos + 4 : pos + 8]
        data = raw_bytes[pos + 8 : pos + 8 + length]
        crc_expected = struct.unpack(">I", raw_bytes[pos + 8 + length : end_pos])[0]
        if (zlib.crc32(ctype + data) & 0xFFFFFFFF) != crc_expected:
            raise V05HomeCraftError(400, "INVALID_POSTCARD_PNG", "Corrupt PNG chunk CRC")
        if ctype == b"IHDR":
            saw_ihdr = True
        elif ctype == b"IEND":
            saw_iend = True
            pos = end_pos
            break
        elif ctype == b"tEXt" and data.startswith(_POSTCARD_META_KEYWORD):
            try:
                parsed = json.loads(data[len(_POSTCARD_META_KEYWORD) :].decode("utf-8"))
                if isinstance(parsed, dict):
                    companion_meta = parsed
            except (UnicodeDecodeError, ValueError):
                raise V05HomeCraftError(400, "INVALID_POSTCARD_PNG", "Corrupt postcard metadata JSON")
        pos = end_pos

    if not saw_ihdr or not saw_iend:
        raise V05HomeCraftError(400, "INVALID_POSTCARD_PNG", "PNG is missing required IHDR or IEND chunk")
    return companion_meta


# ---------------------------------------------------------------------------
# Zero-Write Snapshot Projection
# ---------------------------------------------------------------------------


def read_home_craft_snapshot(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write read projection of the 0.5 Home Craft state."""
    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    ctx = _extract_world_context_zero_write(root, now_dt)
    state, state_exists = _load_state_zero_write(root)

    proposals = copy.deepcopy(state["home_arrangement"]["proposals"])
    open_count = sum(1 for p in proposals if isinstance(p, Mapping) and p.get("status") == "OPEN")
    cartons = copy.deepcopy(state["moving_carton"]["cartons"])
    unpacked_remaining = 0
    for c in cartons:
        if isinstance(c, Mapping) and isinstance(c.get("items"), list):
            unpacked_remaining += sum(
                1 for it in c["items"] if isinstance(it, Mapping) and not bool(it.get("unpacked"))
            )

    return {
        "schema_version": STATE_SCHEMA,
        "state_exists": state_exists,
        "revision": int(state.get("revision", 0)),
        "observed_at": _format_iso(now_dt),
        "world_context": {
            "theme": ctx["theme"],
            "town_name_cn": ctx["town_name_cn"],
            "weather": ctx["weather"],
            "season": ctx["season"],
            "hour": ctx["hour"],
            "date": ctx["date_str"],
            "world_state_found": ctx["world_state_found"],
        },
        "season_solar": copy.deepcopy(ctx["season_solar"]),
        "object_patina": copy.deepcopy(state["object_patina"]),
        "home_arrangement": {
            "zones": [
                {"zone_id": zid, "label_cn": MICRO_ZONE_LABELS_CN[zid]}
                for zid in VALID_MICRO_ZONES
            ],
            "proposals": proposals,
            "open_count": open_count,
        },
        "moving_carton": {
            "cartons": cartons,
            "unpacked_remaining_total": unpacked_remaining,
        },
        "parallel_postcards": {
            "sent": copy.deepcopy(state["parallel_postcards"]["sent"]),
            "received": copy.deepcopy(state["parallel_postcards"]["received"]),
        },
        "crumpled_draft_signal": copy.deepcopy(state["crumpled_draft_signal"]),
        "llm_calls": 0,
        "zero_write": True,
    }


# ---------------------------------------------------------------------------
# Mutations (Ch. 67, 98, 103, 106, 112)
# ---------------------------------------------------------------------------


def log_object_patina_or_repair(
    day_root: Path | str,
    *,
    object_id: str,
    event_kind: str,
    note_cn: str,
    author_side: str = "partner",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.67: Append a real lived patina, repair, keepsake, or gentle retirement note."""
    side = _normalize_author_side(author_side)
    _validate_operation_key(operation_key)
    clean_oid = _validate_clean_text(object_id, field_name="object_id", min_len=1, max_len=64)
    if not isinstance(event_kind, str) or event_kind.strip() not in VALID_PATINA_EVENT_KINDS:
        raise V05HomeCraftError(
            400,
            "INVALID_FIELDS",
            f"Field 'event_kind' must be one of {sorted(VALID_PATINA_EVENT_KINDS)}",
        )
    norm_kind = event_kind.strip()
    clean_note = _validate_clean_text(note_cn, field_name="note_cn", min_len=1, max_len=160)

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    target = _state_path(root)
    _assert_no_symlink_chain(root, target)
    with _tx.state_file_lock(target):
        state, _ = _load_state_zero_write(root)

        obj = _find_object(state, clean_oid)
        if obj is None:
            raise V05HomeCraftError(404, "OBJECT_NOT_FOUND", f"Room object not found: {clean_oid!r}")

        rev_next = int(state.get("revision", 0)) + 1
        notes = obj.setdefault("patina_notes", [])
        note_entry = {
            "note_id": f"pn_{rev_next}_{len(notes) + 1}",
            "event_kind": norm_kind,
            "event_label_cn": PATINA_EVENT_LABELS_CN[norm_kind],
            "note_cn": clean_note,
            "author_side": side,
            "created_at": now_str,
        }
        notes.append(note_entry)
        if len(notes) > MAX_PATINA_NOTES:
            del notes[:-MAX_PATINA_NOTES]
        if norm_kind == "retired_gently":
            obj["retired"] = True
        obj["updated_at"] = now_str

        _commit_state(root, state, now_str)
        obj_snapshot = copy.deepcopy(obj)
        note_snapshot = copy.deepcopy(note_entry)
    note_id_final = str(note_snapshot["note_id"])
    sink_receipt = _emit_craft_lived_episode(
        root,
        episode_key=f"v05_craft:patina:{clean_oid}:{note_id_final}",
        topic=f"旧物痕迹：{obj_snapshot['name_cn']}（{PATINA_EVENT_LABELS_CN[norm_kind]}）",
        exact_detail=f"{obj_snapshot['name_cn']}记下{PATINA_EVENT_LABELS_CN[norm_kind]}：{clean_note}",
        gist_summary=f"为{obj_snapshot['name_cn']}记下了一笔{PATINA_EVENT_LABELS_CN[norm_kind]}",
        occurred_at=now_str,
        emotional_salience=0.52 if norm_kind in ("repaired", "retired_gently", "gifted") else 0.44,
        affect_kind="mood_shift" if norm_kind == "retired_gently" else "affect_pulse",
        affect_intensity=42 if norm_kind in ("repaired", "retired_gently", "gifted") else 36,
        shared=True,
        source_refs=(("project_id", clean_oid), ("object_id", clean_oid), ("note_id", note_id_final)),
    )
    return {
        "ok": True,
        "object": obj_snapshot,
        "entry": note_snapshot,
        "memory_sink": sink_receipt,
        "llm_calls": 0,
    }


def propose_arrangement_change(
    day_root: Path | str,
    *,
    object_id: str,
    target_zone: str,
    reason_cn: str,
    author_side: str = "partner",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.98+103: Propose rearranging a room object gently without forced teleport."""
    side = _normalize_author_side(author_side)
    _validate_operation_key(operation_key)
    clean_oid = _validate_clean_text(object_id, field_name="object_id", min_len=1, max_len=64)
    norm_zone = _validate_zone(target_zone, field_name="target_zone")
    clean_reason = _validate_clean_text(reason_cn, field_name="reason_cn", min_len=1, max_len=140)

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    target = _state_path(root)
    _assert_no_symlink_chain(root, target)
    with _tx.state_file_lock(target):
        state, _ = _load_state_zero_write(root)

        obj = _find_object(state, clean_oid)
        if obj is None:
            raise V05HomeCraftError(404, "OBJECT_NOT_FOUND", f"Room object not found: {clean_oid!r}")
        if bool(obj.get("retired", False)):
            raise V05HomeCraftError(
                409,
                "OBJECT_RETIRED",
                f"Object {obj['name_cn']} has been retired gently and stays where it rests",
            )

        from_zone = str(obj.get("micro_zone") or "desk")
        rev_next = int(state.get("revision", 0)) + 1
        proposal = {
            "proposal_id": f"arr_{rev_next}_{secrets.token_hex(3)}",
            "object_id": obj["object_id"],
            "object_name_cn": obj["name_cn"],
            "from_zone": from_zone,
            "from_zone_label_cn": MICRO_ZONE_LABELS_CN.get(from_zone, from_zone),
            "target_zone": norm_zone,
            "target_zone_label_cn": MICRO_ZONE_LABELS_CN[norm_zone],
            "final_zone": None,
            "proposed_by": side,
            "reason_cn": clean_reason,
            "status": "OPEN",
            "decision": None,
            "resolved_by": None,
            "resolution_note_cn": "",
            "created_at": now_str,
            "resolved_at": None,
        }
        proposals = state["home_arrangement"].setdefault("proposals", [])
        proposals.append(proposal)
        if len(proposals) > MAX_ARRANGEMENT_PROPOSALS:
            del proposals[:-MAX_ARRANGEMENT_PROPOSALS]

        _commit_state(root, state, now_str)
        proposal_snapshot = copy.deepcopy(proposal)
    return {
        "ok": True,
        "proposal": proposal_snapshot,
        "object_teleported": False,
        "llm_calls": 0,
    }


def resolve_arrangement_compromise(
    day_root: Path | str,
    *,
    proposal_id: str,
    decision: str,
    compromise_zone: Optional[str] = None,
    note_cn: Optional[str] = "",
    author_side: str = "partner",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.98+103: Settle an arrangement proposal via accept, middle compromise, or gentle keep."""
    side = _normalize_author_side(author_side)
    _validate_operation_key(operation_key)
    clean_pid = _validate_clean_text(proposal_id, field_name="proposal_id", min_len=1, max_len=64)
    if not isinstance(decision, str) or decision.strip() not in VALID_COMPROMISE_DECISIONS:
        raise V05HomeCraftError(
            400,
            "INVALID_FIELDS",
            f"Field 'decision' must be one of {sorted(VALID_COMPROMISE_DECISIONS)}",
        )
    norm_decision = decision.strip()
    clean_note = _validate_clean_text(note_cn, field_name="note_cn", min_len=0, max_len=140)

    validated_comp_zone: Optional[str] = None
    if compromise_zone is not None and str(compromise_zone).strip() != "":
        validated_comp_zone = _validate_zone(compromise_zone, field_name="compromise_zone")

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    target = _state_path(root)
    _assert_no_symlink_chain(root, target)
    with _tx.state_file_lock(target):
        state, _ = _load_state_zero_write(root)

        target_prop: Optional[dict[str, Any]] = None
        for row in state["home_arrangement"].get("proposals", []):
            if isinstance(row, dict) and row.get("proposal_id") == clean_pid:
                target_prop = row
                break
        if target_prop is None:
            raise V05HomeCraftError(404, "PROPOSAL_NOT_FOUND", f"Proposal not found: {clean_pid!r}")
        if target_prop.get("status") != "OPEN":
            raise V05HomeCraftError(409, "PROPOSAL_ALREADY_RESOLVED", f"Proposal already resolved: {clean_pid!r}")

        obj = _find_object(state, str(target_prop.get("object_id") or ""))
        if obj is None:
            raise V05HomeCraftError(404, "OBJECT_NOT_FOUND", "Referenced object no longer exists")

        from_zone = str(target_prop.get("from_zone") or obj["micro_zone"])
        proposed_zone = str(target_prop.get("target_zone") or from_zone)

        if norm_decision == "accept_as_proposed":
            final_zone = proposed_zone
            status = "ACCEPTED"
            obj["micro_zone"] = final_zone
            obj["micro_zone_label_cn"] = MICRO_ZONE_LABELS_CN[final_zone]
            obj["updated_at"] = now_str
            default_note = f"顺着商量把{obj['name_cn']}挪到了{MICRO_ZONE_LABELS_CN[final_zone]}。"
        elif norm_decision == "compromise_middle":
            final_zone = validated_comp_zone or _pick_middle_zone(from_zone, proposed_zone)
            status = "COMPROMISED"
            obj["micro_zone"] = final_zone
            obj["micro_zone_label_cn"] = MICRO_ZONE_LABELS_CN[final_zone]
            obj["updated_at"] = now_str
            default_note = f"各自让一步，把{obj['name_cn']}折中摆在了{MICRO_ZONE_LABELS_CN[final_zone]}。"
        else:
            final_zone = from_zone
            status = "KEPT_CURRENT"
            default_note = f"先让{obj['name_cn']}留在{MICRO_ZONE_LABELS_CN[final_zone]}，改天想换再商量。"

        target_prop["status"] = status
        target_prop["decision"] = norm_decision
        target_prop["final_zone"] = final_zone
        target_prop["resolved_by"] = side
        target_prop["resolution_note_cn"] = clean_note or default_note
        target_prop["resolved_at"] = now_str

        _commit_state(root, state, now_str)
        prop_snapshot = copy.deepcopy(target_prop)
        obj_snapshot = copy.deepcopy(obj)
    sink_receipt = _emit_craft_lived_episode(
        root,
        episode_key=f"v05_craft:arrangement:{clean_pid}",
        topic=f"屋里陈设商量：{obj_snapshot['name_cn']}",
        exact_detail=str(prop_snapshot["resolution_note_cn"]),
        gist_summary=f"敲定了{obj_snapshot['name_cn']}在屋里的摆放位置（{MICRO_ZONE_LABELS_CN[final_zone]}）",
        occurred_at=now_str,
        emotional_salience=0.48,
        affect_kind="affect_pulse",
        affect_intensity=38,
        shared=True,
        source_refs=(("project_id", clean_pid), ("proposal_id", clean_pid), ("object_id", str(obj_snapshot["object_id"]))),
    )
    return {
        "ok": True,
        "proposal": prop_snapshot,
        "object": obj_snapshot,
        "moved_to_zone": final_zone,
        "memory_sink": sink_receipt,
        "llm_calls": 0,
    }


def pack_moving_carton(
    day_root: Path | str,
    *,
    origin_town_cn: str,
    items: Any,
    author_side: str = "partner",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.106: Seal an entryway moving carton containing 1..12 keepsakes from an old town."""
    side = _normalize_author_side(author_side)
    _validate_operation_key(operation_key)
    clean_origin = _validate_clean_text(origin_town_cn, field_name="origin_town_cn", min_len=1, max_len=40)

    if not isinstance(items, list) or not (1 <= len(items) <= MAX_CARTON_ITEMS):
        raise V05HomeCraftError(
            400,
            "INVALID_FIELDS",
            f"Field 'items' must be a list of 1..{MAX_CARTON_ITEMS} keepsake objects",
        )

    allowed_item_keys = frozenset({"label_cn", "keepsake_note_cn"})
    cleaned_items: list[dict[str, Any]] = []
    for idx, raw_item in enumerate(items):
        if not isinstance(raw_item, Mapping):
            raise V05HomeCraftError(400, "INVALID_FIELDS", f"items[{idx}] must be an object")
        extra = sorted(set(raw_item.keys()) - allowed_item_keys)
        if extra:
            raise V05HomeCraftError(400, "INVALID_FIELDS", f"Unknown fields in items[{idx}]: {extra}")
        label = _validate_clean_text(
            raw_item.get("label_cn"),
            field_name=f"items[{idx}].label_cn",
            min_len=1,
            max_len=40,
        )
        note = _validate_clean_text(
            raw_item.get("keepsake_note_cn", ""),
            field_name=f"items[{idx}].keepsake_note_cn",
            min_len=0,
            max_len=140,
        )
        cleaned_items.append({
            "item_id": f"ci_{idx + 1}_{secrets.token_hex(3)}",
            "label_cn": label,
            "keepsake_note_cn": note,
            "unpacked": False,
            "placed_zone": None,
            "settle_note_cn": "",
            "unpacked_at": None,
            "linked_object_id": None,
        })

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    target = _state_path(root)
    _assert_no_symlink_chain(root, target)
    with _tx.state_file_lock(target):
        state, _ = _load_state_zero_write(root)

        rev_next = int(state.get("revision", 0)) + 1
        carton = {
            "carton_id": f"carton_{rev_next}_{secrets.token_hex(3)}",
            "origin_town_cn": clean_origin,
            "packed_by_side": side,
            "status": "UNPACKING",
            "items": cleaned_items,
            "packed_at": now_str,
            "settled_at": None,
        }
        cartons = state["moving_carton"].setdefault("cartons", [])
        cartons.append(carton)
        if len(cartons) > MAX_MOVING_CARTONS:
            del cartons[:-MAX_MOVING_CARTONS]

        _commit_state(root, state, now_str)
        carton_snapshot = copy.deepcopy(carton)
    return {
        "ok": True,
        "carton": carton_snapshot,
        "llm_calls": 0,
    }


def unpack_carton_item(
    day_root: Path | str,
    *,
    carton_id: str,
    item_id: str,
    target_zone: str,
    settle_note_cn: Optional[str] = "",
    author_side: str = "partner",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.106: Unpack one keepsake into target_zone and promote it into object_patina."""
    side = _normalize_author_side(author_side)
    _validate_operation_key(operation_key)
    clean_cid = _validate_clean_text(carton_id, field_name="carton_id", min_len=1, max_len=64)
    clean_iid = _validate_clean_text(item_id, field_name="item_id", min_len=1, max_len=64)
    norm_zone = _validate_zone(target_zone, field_name="target_zone")
    clean_settle = _validate_clean_text(settle_note_cn, field_name="settle_note_cn", min_len=0, max_len=140)

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    target = _state_path(root)
    _assert_no_symlink_chain(root, target)
    with _tx.state_file_lock(target):
        state, _ = _load_state_zero_write(root)

        target_carton: Optional[dict[str, Any]] = None
        for c in state["moving_carton"].get("cartons", []):
            if isinstance(c, dict) and c.get("carton_id") == clean_cid:
                target_carton = c
                break
        if target_carton is None:
            raise V05HomeCraftError(404, "CARTON_NOT_FOUND", f"Moving carton not found: {clean_cid!r}")

        target_item: Optional[dict[str, Any]] = None
        for it in target_carton.get("items", []):
            if isinstance(it, dict) and it.get("item_id") == clean_iid:
                target_item = it
                break
        if target_item is None:
            raise V05HomeCraftError(404, "CARTON_ITEM_NOT_FOUND", f"Carton item not found: {clean_iid!r}")
        if bool(target_item.get("unpacked", False)):
            raise V05HomeCraftError(409, "ITEM_ALREADY_UNPACKED", f"Item already unpacked: {clean_iid!r}")

        rev_next = int(state.get("revision", 0)) + 1
        origin_town = str(target_carton.get("origin_town_cn") or "旧小镇")
        settle_text = clean_settle or f"从{origin_town}带回，安放在{MICRO_ZONE_LABELS_CN[norm_zone]}。"

        new_obj_id = f"keepsake_{clean_iid}"
        target_item["unpacked"] = True
        target_item["placed_zone"] = norm_zone
        target_item["settle_note_cn"] = settle_text
        target_item["unpacked_at"] = now_str
        target_item["linked_object_id"] = new_obj_id

        keepsake_note = str(target_item.get("keepsake_note_cn") or "")
        combined_note = f"{keepsake_note}（{settle_text}）" if keepsake_note else settle_text
        new_obj = {
            "object_id": new_obj_id,
            "name_cn": str(target_item["label_cn"]),
            "micro_zone": norm_zone,
            "micro_zone_label_cn": MICRO_ZONE_LABELS_CN[norm_zone],
            "retired": False,
            "origin_cn": f"从{origin_town}纸箱里逐件拆出的旧物",
            "patina_notes": [
                {
                    "note_id": f"pn_{rev_next}_unpack",
                    "event_kind": "gifted",
                    "event_label_cn": "搬家入屋",
                    "note_cn": combined_note[:160],
                    "author_side": side,
                    "created_at": now_str,
                }
            ],
            "updated_at": now_str,
        }

        objs = state.setdefault("object_patina", [])
        objs.append(new_obj)
        if len(objs) > MAX_PATINA_OBJECTS:
            starters = [o for o in objs if o.get("object_id") in STARTER_OBJECT_IDS]
            extras = [o for o in objs if o.get("object_id") not in STARTER_OBJECT_IDS]
            keep_extra = max(0, MAX_PATINA_OBJECTS - len(starters))
            state["object_patina"] = starters + extras[-keep_extra:]

        all_done = all(bool(it.get("unpacked")) for it in target_carton.get("items", []))
        if all_done:
            target_carton["status"] = "ALL_SETTLED"
            target_carton["settled_at"] = now_str

        _commit_state(root, state, now_str)
        carton_status = target_carton["status"]
        item_snapshot = copy.deepcopy(target_item)
        obj_snapshot = copy.deepcopy(new_obj)
    sink_receipt = _emit_craft_lived_episode(
        root,
        episode_key=f"v05_craft:unpack:{clean_cid}:{clean_iid}",
        topic=f"拆箱安放旧物：{item_snapshot['label_cn']}",
        exact_detail=(
            f"从{origin_town}搬家纸箱拆出{item_snapshot['label_cn']}，安放在{MICRO_ZONE_LABELS_CN[norm_zone]}：{settle_text}"
            + ("（整箱旧物已全部安顿好）" if all_done else "")
        ),
        gist_summary=(
            f"从{origin_town}纸箱安放了{item_snapshot['label_cn']}"
            + ("，整箱已安顿完毕" if all_done else "")
        ),
        occurred_at=now_str,
        emotional_salience=0.62 if all_done else 0.50,
        affect_kind="mood_shift" if all_done else "affect_pulse",
        affect_intensity=48 if all_done else 40,
        shared=True,
        source_refs=(("project_id", clean_cid), ("object_id", new_obj_id), ("item_id", clean_iid)),
    )
    return {
        "ok": True,
        "carton_id": clean_cid,
        "carton_status": carton_status,
        "item": item_snapshot,
        "added_object": obj_snapshot,
        "memory_sink": sink_receipt,
        "llm_calls": 0,
    }


def mint_parallel_postcard(
    day_root: Path | str,
    *,
    greeting_cn: str,
    sender_label_cn: Optional[str] = None,
    town_name_cn: Optional[str] = None,
    author_side: str = "partner",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.112: Render a valid 160x104 PNG postcard & embed sanitized metadata via unified fabric."""
    side = _normalize_author_side(author_side)
    _validate_operation_key(operation_key)
    clean_greeting = _validate_clean_text(greeting_cn, field_name="greeting_cn", min_len=1, max_len=120)
    clean_sender = _validate_clean_text(sender_label_cn, field_name="sender_label_cn", min_len=0, max_len=30)
    clean_town = _validate_clean_text(town_name_cn, field_name="town_name_cn", min_len=0, max_len=30)

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    ctx = _extract_world_context_zero_write(root, now_dt)

    pulse = ctx["season_solar"]
    season = str(pulse["season"])
    solar_term = pulse["solar_term"]
    term_idx = int(solar_term["solar_term_idx"])
    term_name_cn = str(solar_term["term_name_cn"])
    sent_date = now_dt.strftime("%Y-%m-%d")

    eff_town = clean_town or str(ctx["town_name_cn"])
    eff_sender = clean_sender or ("小屋里的 TA" if side == "subject" else "小屋里的住客")

    metadata: dict[str, str] = {
        "schema": POSTCARD_SCHEMA,
        "town_name_cn": eff_town,
        "season": season,
        "solar_term_name_cn": term_name_cn,
        "greeting_cn": clean_greeting,
        "sender_label_cn": eff_sender,
        "sent_date": sent_date,
    }

    base_png = render_seasonal_postcard_png(
        season=season,
        weather=str(ctx["weather"]),
        hour=int(ctx["hour"]),
    )
    gift_slot = _SEASON_GIFT_SLOT.get(season, "none")
    stamp_code = f"{season[:3]}-{term_idx:02d}-{sent_date}"

    png_with_fabric = fabric.embed_postcard_png_chunk(
        base_png,
        origin_town=eff_town[:12],
        stamp_code=stamp_code,
        gift_slot=gift_slot,
        FrontNoteCn=clean_greeting[:60],
        hops=1,
    )
    final_png = _insert_companion_meta_chunk(png_with_fabric, metadata)
    sha256_hex = hashlib.sha256(final_png).hexdigest()
    png_b64 = base64.b64encode(final_png).decode("ascii")

    target = _state_path(root)
    _assert_no_symlink_chain(root, target)
    with _tx.state_file_lock(target):
        state, _ = _load_state_zero_write(root)
        rev_next = int(state.get("revision", 0)) + 1
        postcard_id = f"pc_{rev_next}_{sha256_hex[:8]}"

        sent_entry = {
            "postcard_id": postcard_id,
            "schema": POSTCARD_SCHEMA,
            "town_name_cn": eff_town,
            "season": season,
            "solar_term_name_cn": term_name_cn,
            "greeting_cn": clean_greeting,
            "sender_label_cn": eff_sender,
            "sent_date": sent_date,
            "gift_slot": gift_slot,
            "stamp_code": stamp_code,
            "sha256_hex": sha256_hex,
            "minted_by_side": side,
            "minted_at": now_str,
        }
        sent_list = state["parallel_postcards"].setdefault("sent", [])
        sent_list.append(sent_entry)
        if len(sent_list) > MAX_POSTCARDS:
            del sent_list[:-MAX_POSTCARDS]

        _commit_state(root, state, now_str)
        sent_snapshot = copy.deepcopy(sent_entry)
    sink_receipt = _emit_craft_lived_episode(
        root,
        episode_key=f"v05_craft:postcard_mint:{postcard_id}",
        topic=f"手绘明信片：{eff_town}（{term_name_cn}）",
        exact_detail=f"印制了一张{eff_town}的{term_name_cn}明信片，正面写着：「{clean_greeting}」",
        gist_summary=f"做好了一张{eff_town}的{term_name_cn}明信片",
        occurred_at=now_str,
        emotional_salience=0.56,
        affect_kind="affect_pulse",
        affect_intensity=44,
        shared=True,
        source_refs=(("project_id", postcard_id), ("postcard_id", postcard_id)),
    )
    return {
        "ok": True,
        "postcard_id": postcard_id,
        "metadata": copy.deepcopy(metadata),
        "sha256_hex": sha256_hex,
        "png_byte_length": len(final_png),
        "postcard_png_base64": png_b64,
        "sent_entry": sent_snapshot,
        "memory_sink": sink_receipt,
        "llm_calls": 0,
    }


def import_parallel_postcard(
    day_root: Path | str,
    *,
    png_base64: str,
    from_friend_note_cn: Optional[str] = "",
    author_side: str = "partner",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.112: Decode & verify an incoming PNG postcard via unified fabric and pin to received wall."""
    side = _normalize_author_side(author_side)
    _validate_operation_key(operation_key)
    clean_friend_note = _validate_clean_text(
        from_friend_note_cn,
        field_name="from_friend_note_cn",
        min_len=0,
        max_len=120,
    )

    if not isinstance(png_base64, str) or not png_base64.strip():
        raise V05HomeCraftError(400, "INVALID_POSTCARD_PNG", "Field 'png_base64' is required")
    raw_b64 = png_base64.strip()
    if raw_b64.startswith("data:image/png;base64,"):
        raw_b64 = raw_b64[len("data:image/png;base64,") :].strip()
    raw_b64 = re.sub(r"\s+", "", raw_b64)
    if len(raw_b64) > MAX_POSTCARD_B64_CHARS:
        raise V05HomeCraftError(400, "INVALID_POSTCARD_PNG", "Encoded PNG exceeds size limit")

    try:
        raw_bytes = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError):
        raise V05HomeCraftError(400, "INVALID_POSTCARD_PNG", "Invalid base64 encoding for PNG postcard")

    companion_meta = _verify_png_and_extract_companion_meta(raw_bytes)

    try:
        fabric_chunk = fabric.extract_postcard_png_chunk(raw_bytes)
    except (ValueError, struct.error, UnicodeDecodeError, TypeError):
        fabric_chunk = None
    if not isinstance(fabric_chunk, Mapping):
        raise V05HomeCraftError(
            400,
            "INVALID_POSTCARD_PNG",
            "PNG does not contain a valid nyairo_postcard steganographic chunk",
        )

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)

    # Reconstruct sanitized metadata (never trust arbitrary extra keys or paths)
    stamp_str = _strip_emojis_and_ctrl_unbounded(str(fabric_chunk.get("stamp") or ""))[:24]
    parsed_season = season_pulse.resolve_effective_season(now_dt)[0]
    parsed_term = ""
    parsed_date = now_dt.strftime("%Y-%m-%d")
    m = _STAMP_RE.match(stamp_str)
    if m:
        parsed_season = _SEASON_PREFIX_MAP.get(m.group(1), parsed_season)
        t_idx = int(m.group(2))
        if 0 <= t_idx < len(season_pulse.SOLAR_TERMS_24):
            parsed_term = str(season_pulse.SOLAR_TERMS_24[t_idx][2])
        parsed_date = m.group(3)

    town_name_cn = _strip_emojis_and_ctrl_unbounded(str(fabric_chunk.get("town") or ""))[:30] or "远方小镇"
    greeting_cn = _strip_emojis_and_ctrl_unbounded(str(fabric_chunk.get("note") or ""))[:120]
    sender_label_cn = "平行小镇的熟人"

    if isinstance(companion_meta, Mapping):
        if companion_meta.get("town_name_cn"):
            c_town = _strip_emojis_and_ctrl_unbounded(str(companion_meta["town_name_cn"]))[:30]
            if c_town:
                town_name_cn = c_town
        if companion_meta.get("season"):
            norm_s = season_pulse.normalize_season(companion_meta["season"])
            if norm_s is not None:
                parsed_season = norm_s
        if companion_meta.get("solar_term_name_cn"):
            c_term = _strip_emojis_and_ctrl_unbounded(str(companion_meta["solar_term_name_cn"]))[:12]
            if c_term:
                parsed_term = c_term
        if companion_meta.get("greeting_cn"):
            c_greet = _strip_emojis_and_ctrl_unbounded(str(companion_meta["greeting_cn"]))[:120]
            if c_greet:
                greeting_cn = c_greet
        if companion_meta.get("sender_label_cn"):
            c_sender = _strip_emojis_and_ctrl_unbounded(str(companion_meta["sender_label_cn"]))[:30]
            if c_sender:
                sender_label_cn = c_sender
        if companion_meta.get("sent_date"):
            c_date = _strip_emojis_and_ctrl_unbounded(str(companion_meta["sent_date"]))[:16]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c_date):
                parsed_date = c_date

    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
    target = _state_path(root)
    _assert_no_symlink_chain(root, target)
    with _tx.state_file_lock(target):
        state, _ = _load_state_zero_write(root)
        rev_next = int(state.get("revision", 0)) + 1
        received_entry = {
            "postcard_id": f"pc_in_{rev_next}_{sha256_hex[:8]}",
            "schema": POSTCARD_SCHEMA,
            "town_name_cn": town_name_cn,
            "season": parsed_season,
            "solar_term_name_cn": parsed_term,
            "greeting_cn": greeting_cn,
            "sender_label_cn": sender_label_cn,
            "sent_date": parsed_date,
            "gift_slot": str(fabric_chunk.get("gift") or "none"),
            "stamp_code": stamp_str,
            "safe_display_quote": str(
                fabric_chunk.get("safe_display_quote") or f"明信片正面手写字：「{greeting_cn[:60]}」"
            ),
            "from_friend_note_cn": clean_friend_note,
            "sha256_hex": sha256_hex,
            "imported_by_side": side,
            "received_at": now_str,
        }
        received_list = state["parallel_postcards"].setdefault("received", [])
        received_list.append(received_entry)
        if len(received_list) > MAX_POSTCARDS:
            del received_list[:-MAX_POSTCARDS]

        _commit_state(root, state, now_str)
        received_snapshot = copy.deepcopy(received_entry)
        received_count = len(received_list)
    in_pid = str(received_snapshot["postcard_id"])
    sink_receipt = _emit_craft_lived_episode(
        root,
        episode_key=f"v05_craft:postcard_in:{in_pid}",
        topic=f"收到远方明信片：{town_name_cn}",
        exact_detail=f"收到来自{town_name_cn}的明信片：{greeting_cn}",
        gist_summary=f"把来自{town_name_cn}的明信片钉上了墙",
        occurred_at=now_str,
        emotional_salience=0.55,
        affect_kind="affect_pulse",
        affect_intensity=44,
        shared=True,
        source_refs=(("project_id", in_pid), ("postcard_id", in_pid)),
    )
    return {
        "ok": True,
        "postcard": received_snapshot,
        "received_count": received_count,
        "memory_sink": sink_receipt,
        "llm_calls": 0,
    }


def signal_crumpled_draft(
    day_root: Path | str,
    *,
    disposal_kind: Optional[str] = "wastebasket",
    author_side: str = "partner",
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.110 #2: Record a zero-text crumpled draft disposal signal (partner-only, idempotent)."""
    side = _normalize_author_side(author_side)
    if side != "partner":
        raise V05HomeCraftError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            "Action 'signal_crumpled_draft' is restricted to partner side",
        )
    clean_op_key = _validate_operation_key(operation_key)

    if disposal_kind is None:
        norm_kind = "wastebasket"
    else:
        if not isinstance(disposal_kind, str):
            raise V05HomeCraftError(
                400,
                "INVALID_FIELDS",
                f"Field 'disposal_kind' must be one of {VALID_DISPOSAL_KINDS}",
            )
        norm_kind = disposal_kind.strip().lower()
        if norm_kind not in VALID_DISPOSAL_KIND_SET:
            raise V05HomeCraftError(
                400,
                "INVALID_FIELDS",
                f"Field 'disposal_kind' must be one of {VALID_DISPOSAL_KINDS}, got {disposal_kind!r}",
            )

    root = Path(day_root)
    now_dt = _parse_now(now_iso)
    now_str = _format_iso(now_dt)
    today_str = now_dt.strftime("%Y-%m-%d")
    target = _state_path(root)
    _assert_no_symlink_chain(root, target)
    with _tx.state_file_lock(target):
        state, _ = _load_state_zero_write(root)

        op_keys = list(state.get("crumpled_draft_op_keys") or [])
        prev_sig = state.get("crumpled_draft_signal")
        if clean_op_key and clean_op_key in op_keys and isinstance(prev_sig, Mapping):
            return {
                "ok": True,
                "idempotent_replay": True,
                "already_applied": True,
                "crumpled_draft_signal": copy.deepcopy(dict(prev_sig)),
                "zero_text_stored": True,
                "llm_calls": 0,
            }

        if isinstance(prev_sig, Mapping) and str(prev_sig.get("date") or "") == today_str:
            raw_prev_cnt = prev_sig.get("crumpled_count_today")
            base_cnt = (
                int(raw_prev_cnt)
                if isinstance(raw_prev_cnt, int) and not isinstance(raw_prev_cnt, bool) and raw_prev_cnt >= 0
                else 0
            )
            next_cnt = base_cnt + 1
        else:
            next_cnt = 1

        new_sig = {
            "last_crumpled_at": now_str,
            "disposal_kind": norm_kind,
            "crumpled_count_today": next_cnt,
            "date": today_str,
        }
        state["crumpled_draft_signal"] = new_sig
        if clean_op_key:
            op_keys.append(clean_op_key)
            if len(op_keys) > MAX_CRUMPLED_OP_KEYS:
                del op_keys[:-MAX_CRUMPLED_OP_KEYS]
            state["crumpled_draft_op_keys"] = op_keys

        _commit_state(root, state, now_str)
        sig_snapshot = copy.deepcopy(new_sig)
    return {
        "ok": True,
        "idempotent_replay": False,
        "already_applied": False,
        "crumpled_draft_signal": sig_snapshot,
        "zero_text_stored": True,
        "llm_calls": 0,
    }


__all__ = [
    "DEFAULT_TZ",
    "MAX_ARRANGEMENT_PROPOSALS",
    "MAX_CARTON_ITEMS",
    "MAX_CRUMPLED_OP_KEYS",
    "MAX_MOVING_CARTONS",
    "MAX_PATINA_NOTES",
    "MAX_PATINA_OBJECTS",
    "MAX_POSTCARDS",
    "MAX_POSTCARD_PNG_BYTES",
    "MICRO_ZONE_LABELS_CN",
    "PATINA_EVENT_LABELS_CN",
    "POSTCARD_SCHEMA",
    "STARTER_OBJECT_IDS",
    "STATE_REL_PATH",
    "STATE_SCHEMA",
    "VALID_COMPROMISE_DECISIONS",
    "VALID_DISPOSAL_KINDS",
    "VALID_DISPOSAL_KIND_SET",
    "VALID_MICRO_ZONES",
    "VALID_PATINA_EVENT_KINDS",
    "V05HomeCraftError",
    "import_parallel_postcard",
    "log_object_patina_or_repair",
    "mint_parallel_postcard",
    "pack_moving_carton",
    "propose_arrangement_change",
    "read_home_craft_snapshot",
    "render_seasonal_postcard_png",
    "resolve_arrangement_compromise",
    "signal_crumpled_draft",
    "unpack_carton_item",
]

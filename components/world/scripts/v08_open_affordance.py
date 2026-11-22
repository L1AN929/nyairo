#!/usr/bin/env python3
"""v0.8 general object affordances, living traces and self-built habit routines.

Three layers, all deterministic (``llm_calls == 0``) and zero-network:

1. **Universal object vocabulary** — :data:`UNIVERSAL_VERBS` names six verbs that
   work on any *real* reachable object: ``RELOCATE`` / ``INSERT_EXTRACT`` /
   ``INSCRIBE`` / ``TOGGLE_ADJUST`` / ``USE_CONSUME`` / ``COMBINE_SPLIT``.  Each
   verb requires a trait the object really has (``movable`` / ``insertable`` /
   ``inscribable`` / ``switchable`` / ``usable`` / ``thermal_or_damp`` /
   ``combinable`` / ``container``).  A verb never mints money, never invents an
   object, and never skips the illness gate.
2. **Living traces** — a short open gesture on a real object leaves a visible
   state sticker that either fades on its own (``timed``, 5–1440 minutes, settled
   by the 60-second zero-model clock) or fades when somebody really touches it
   (``until_touched``).  Partner traces are always ``shared``; private traces are
   stripped from the partner projection.
3. **Personal habit routines** — 2–4 step replays of universal verbs or living
   traces, at most eight, with no streak, no debt and no guilt copy.

WorldGen packages and world-editor saves are read straight off disk, so objects
authored outside this repository become reachable without new backend code.  The
``focused`` routine cue is claimed only while a real focus signal exists (an
unsettled workbench tray, an active reading workbench, or an explicitly active
focus state).

State file: ``<root>/phone/v08_living_traces.json``.  Every read helper is
100% zero-write (no directory, no file, no mtime change); every write is an
atomic replace of that one file.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:  # ``components/life/scripts`` is on sys.path in tests and the life service
    from state_tx_guard import (  # type: ignore[import-not-found]
        reconcile_evicted_or_active_op,
        stamp_receipt,
        state_file_lock,
        trim_receipts_by_op_seq,
    )
except ImportError:  # pragma: no cover - world service bootstrap
    _LIFE_SCRIPTS = Path(__file__).resolve().parents[2] / "life" / "scripts"
    if str(_LIFE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LIFE_SCRIPTS))
    from state_tx_guard import (  # type: ignore[import-not-found,no-redef]
        reconcile_evicted_or_active_op,
        stamp_receipt,
        state_file_lock,
        trim_receipts_by_op_seq,
    )

SCHEMA_VERSION = "world.v08.open_affordance.v1"
VIEW_SCHEMA = "nyairo.v08.living_traces.v1"
STATE_DIRNAME = "phone"
STATE_FILENAME = "v08_living_traces.json"

#: Real on-disk location snapshots, most specific first.  A missing or malformed
#: file is simply skipped; no directory or file is ever created here.
LOCATION_STATE_FILES: tuple[tuple[str, ...], ...] = (
    ("world", "world_runtime.json"),
    ("world", "runtime_state.json"),
    ("world", "state.json"),
    ("data", "world", "world_state.json"),
    ("phone", "desk_presence.json"),
)

#: WorldGen package catalogues.  Each row carries ``object_id/name/kind/note``
#: and ``location_id``; optional ``affordance_traits`` win over inference.
#: Desktop/daemon instances write the switched package under the HYPHEN path
#: ``<root>/world-package/``; the underscore variants stay for older exports.
WORLD_PACKAGE_OBJECT_FILES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("world-package", "objects.json"), "world_package"),
    (("world", "package", "objects.json"), "world_package"),
    (("world_package", "objects.json"), "world_package"),
    (("world", "objects.json"), "world_package"),
)

#: World-editor v3 saves (``nyairo.world-package.v3``): ``objects[]`` + ``rooms[]``.
EDITOR_WORLD_FILES: tuple[tuple[str, ...], ...] = (
    ("world", "nyairo-world.json"),
    ("world", "edited_world.json"),
    ("phone", "edited_world.json"),
    ("world", "scene.json"),
)

HOME_LOCATION_SCOPES: frozenset[str] = frozenset({
    "home", "bedroom", "living_room", "living-room", "livingroom",
    "study", "kitchen", "courtyard", "house", "apartment",
})
HOME_NAME_TOKENS: tuple[str, ...] = ("家", "卧室", "客厅", "书房", "厨房", "院子", "小屋", "居所")

_OUTDOOR_LOCATION_TOKENS: tuple[str, ...] = (
    "outdoor", "park", "street", "plaza", "garden", "yard", "field", "square",
)

_TRAIT_ORDER: tuple[str, ...] = (
    "movable",
    "insertable",
    "inscribable",
    "switchable",
    "usable",
    "thermal_or_damp",
    "combinable",
    "container",
)

_KIND_TRAITS: dict[str, tuple[str, ...]] = {
    "bed": ("usable", "thermal_or_damp", "movable"),
    "table": ("usable", "inscribable", "container"),
    "desk": ("usable", "inscribable", "container"),
    "chair": ("usable", "movable"),
    "cabinet": ("container", "insertable", "combinable"),
    "shelf": ("container", "insertable", "combinable"),
    "lamp": ("switchable",),
    "cup": ("usable", "movable", "container"),
    "dispenser": ("switchable", "usable", "container"),
    "book": ("inscribable", "usable", "movable", "insertable"),
    "plant": ("movable", "usable"),
    "door": ("switchable", "inscribable"),
    "window": ("switchable", "inscribable", "thermal_or_damp"),
    "rack": ("switchable", "thermal_or_damp", "container"),
    "basket": ("container", "insertable", "combinable", "movable"),
}

_NAME_TRAITS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("晾", ("switchable", "thermal_or_damp")),
    ("窗", ("switchable", "inscribable", "thermal_or_damp")),
    ("门", ("switchable", "inscribable")),
    ("牌", ("inscribable", "switchable")),
    ("桌", ("container", "inscribable", "usable")),
    ("架", ("container", "combinable")),
    ("柜", ("container", "insertable", "combinable")),
    ("盒", ("container", "insertable")),
    ("篮", ("container", "movable")),
    ("箱", ("container", "movable")),
    ("床", ("usable", "thermal_or_damp", "movable")),
    ("毯", ("thermal_or_damp", "movable")),
    ("枕", ("thermal_or_damp", "movable")),
    ("椅", ("usable", "movable")),
    ("凳", ("usable", "movable")),
    ("灯", ("switchable",)),
    ("杯", ("usable", "container", "movable")),
    ("壶", ("usable", "container")),
    ("书", ("inscribable", "usable", "movable", "insertable")),
    ("本", ("inscribable", "movable")),
    ("纸", ("inscribable", "movable")),
    ("笔", ("movable", "usable", "inscribable")),
    ("照片", ("inscribable", "movable")),
    ("相", ("inscribable", "movable")),
    ("绿植", ("movable", "usable")),
    ("植物", ("movable", "usable")),
    ("花", ("movable", "usable")),
)

_ACTION_TRAITS: dict[str, tuple[str, ...]] = {
    "sit": ("usable",),
    "rest": ("usable", "thermal_or_damp"),
    "pick_up": ("movable",),
    "place": ("movable",),
    "read": ("inscribable", "usable"),
    "drink": ("usable", "container"),
    "fill": ("usable", "container"),
    "store": ("container", "insertable"),
    "retrieve": ("container", "insertable"),
    "toggle": ("switchable",),
    "toggle_device": ("switchable",),
    "open_door": ("switchable",),
    "close_door": ("switchable",),
    "lock_door": ("switchable",),
    "unlock_door": ("switchable",),
}

#: Files that prove a real, unsettled focus signal.  All are read-only.
FOCUS_AGENCY_FILES: tuple[tuple[str, ...], ...] = (
    ("state", "v02_agency_depth_state.json"),
    ("phone", "v02_agency_depth.json"),
)
FOCUS_READING_FILES: tuple[tuple[str, ...], ...] = (
    ("phone", "reading_workbench.json"),
    ("life", "reading_workbench.json"),
)

LOCAL_TZ = timezone(timedelta(hours=8))

MAX_ACTIVE_TRACES = 24
MAX_PARTNER_TRACES = 8
MAX_TRACES_PER_OBJECT_AUTHOR = 2
MIN_TTL_MINUTES = 5
MAX_TTL_MINUTES = 1440
DEFAULT_TTL_MINUTES = 60
MAX_INSCRIPTIONS_PER_OBJECT = 5
MAX_INSCRIPTION_CHARS = 120
MAX_GESTURE_CHARS = 48
MAX_STICKER_CHARS = 80
MAX_ROUTINES = 8
MIN_ROUTINE_STEPS = 2
MAX_ROUTINE_STEPS = 4
MAX_USE_RECORDS = 24
MAX_OPERATION_RECEIPTS = 200

FADE_MODES: frozenset[str] = frozenset({"timed", "until_touched"})
VISIBILITIES: frozenset[str] = frozenset({"shared", "private"})
WHEN_CUES: frozenset[str] = frozenset({"rainy", "evening", "morning", "chilly", "focused", "any"})

TRAITS: frozenset[str] = frozenset({
    "movable",
    "insertable",
    "inscribable",
    "switchable",
    "usable",
    "thermal_or_damp",
    "combinable",
    "container",
})

#: The six general verbs and the Chinese labels the projection shows.  A label is a
#: display string; the wire value stays the stable upper-case verb name.
UNIVERSAL_VERBS: dict[str, dict[str, Any]] = {
    "RELOCATE": {
        "label_cn": "移动摆放",
        "requires_any": ["movable"],
        "detail_cn": "把物件放到房间或台面的另一个真实位置。",
    },
    "INSERT_EXTRACT": {
        "label_cn": "收起或取出",
        "requires_any": ["insertable", "container"],
        "detail_cn": "把小物夹进/放进另一个真实可达物件，或从中取出。",
    },
    "INSCRIBE": {
        "label_cn": "留字批注",
        "requires_any": ["inscribable"],
        "detail_cn": "在纸片、书页、布面或台牌上留一小段字。",
    },
    "TOGGLE_ADJUST": {
        "label_cn": "开关微调",
        "requires_any": ["switchable"],
        "detail_cn": "切换或微调开关、半掩、朝向。",
    },
    "USE_CONSUME": {
        "label_cn": "使用或喝掉",
        "requires_any": ["usable", "thermal_or_damp"],
        "detail_cn": "实际使用、喝完、整理或暖手。",
    },
    "COMBINE_SPLIT": {
        "label_cn": "搭在一起或拆开",
        "requires_any": ["combinable", "container"],
        "detail_cn": "把两件真实物件临时搭在一起，或重新拆开。",
    },
}

#: Fixed home anchors every real instance can reach.  They mirror the already
#: shipped room vocabulary (window / desk / bed / door sign / shelf / drying rack /
#: plant) instead of inventing new furniture.
_BASE_OBJECTS: tuple[dict[str, Any], ...] = (
    {
        "object_id": "home_window",
        "label_cn": "窗扇与玻璃",
        "location_hint": "屋里靠窗",
        "traits": ["switchable", "inscribable", "thermal_or_damp"],
        "is_outdoor": False,
    },
    {
        "object_id": "home_desk",
        "label_cn": "书桌台面",
        "location_hint": "屋里书桌",
        "traits": ["container", "inscribable", "usable"],
        "is_outdoor": False,
    },
    {
        "object_id": "home_bed",
        "label_cn": "床铺与毯子",
        "location_hint": "屋里床边",
        "traits": ["usable", "thermal_or_damp", "movable"],
        "is_outdoor": False,
    },
    {
        "object_id": "home_door_sign",
        "label_cn": "门口木牌",
        "location_hint": "门口",
        "traits": ["switchable", "inscribable"],
        "is_outdoor": False,
    },
    {
        "object_id": "home_shelf",
        "label_cn": "桌面置物架与笔搁",
        "location_hint": "屋里桌边",
        "traits": ["container", "combinable"],
        "is_outdoor": False,
    },
    {
        "object_id": "courtyard_rack",
        "label_cn": "廊下晾架",
        "location_hint": "院子廊下",
        "traits": ["switchable", "thermal_or_damp"],
        "is_outdoor": True,
    },
    {
        "object_id": "indoor_plant",
        "label_cn": "室内盆栽",
        "location_hint": "屋里窗台",
        "traits": ["movable", "usable"],
        "is_outdoor": False,
    },
)

_ERROR_STATUS: dict[str, int] = {
    "INVALID_FIELDS": 400,
    "FORBIDDEN_ACTOR_SIDE": 403,
    "CLIENT_IDENTITY_REFUSED": 409,
    "OBJECT_NOT_FOUND": 404,
    "TRAIT_MISMATCH": 409,
    "ILLNESS_GATE_BLOCKED": 409,
    "OPERATION_KEY_CONFLICT": 409,
    "TRACE_NOT_FOUND": 404,
    "AUTHOR_MISMATCH": 409,
    "ROUTINE_NOT_FOUND": 404,
    "ROUTINE_LIMIT_REACHED": 409,
    "STATE_INVALID": 409,
    "STATE_SYMLINK": 409,
}


class V08OpenAffordanceError(ValueError):
    """A named refusal with the HTTP status the phone surface maps it to."""

    def __init__(self, reason_code: str, message: str = "") -> None:
        self.reason_code = str(reason_code)
        self.message = str(message or self.reason_code)
        self.status_code = _ERROR_STATUS.get(self.reason_code, 400)
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=LOCAL_TZ)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(LOCAL_TZ).isoformat(timespec="seconds")


def _resolve_now(now_iso: Optional[str]) -> tuple[datetime, str]:
    dt = _parse_iso(now_iso) if now_iso is not None else None
    if dt is None:
        dt = _now()
    return dt, _format_iso(dt)


def _iso_cmp(value: Any, reference: datetime) -> Optional[int]:
    """-1 / 0 / 1 against ``reference``, or ``None`` when unparseable."""

    dt = _parse_iso(value)
    if dt is None:
        return None
    if dt < reference:
        return -1
    if dt > reference:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _text(value: Any, *, min_len: int, max_len: int, field: str) -> str:
    if not isinstance(value, str):
        raise V08OpenAffordanceError("INVALID_FIELDS", f"{field} must be a string")
    text = value.strip()
    if len(text) < min_len:
        raise V08OpenAffordanceError("INVALID_FIELDS", f"{field} is too short")
    if len(text) > max_len:
        raise V08OpenAffordanceError("INVALID_FIELDS", f"{field} exceeds {max_len} characters")
    return text


def _optional_text(value: Any, *, max_len: int, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise V08OpenAffordanceError("INVALID_FIELDS", f"{field} must be a string")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_len:
        raise V08OpenAffordanceError("INVALID_FIELDS", f"{field} exceeds {max_len} characters")
    return text


def _strict_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise V08OpenAffordanceError("INVALID_FIELDS", f"{field} must be a JSON boolean")


def _visibility(value: Any, *, default: str = "shared") -> str:
    if value is None:
        return default
    if not isinstance(value, str) or value.strip() not in VISIBILITIES:
        raise V08OpenAffordanceError("INVALID_FIELDS", "visibility must be shared or private")
    return value.strip()


def _fade_mode(value: Any) -> str:
    if value is None:
        return "timed"
    if not isinstance(value, str) or value.strip() not in FADE_MODES:
        raise V08OpenAffordanceError("INVALID_FIELDS", "fade_mode must be timed or until_touched")
    return value.strip()


def _ttl_minutes(value: Any) -> int:
    if value is None:
        return DEFAULT_TTL_MINUTES
    if isinstance(value, bool) or not isinstance(value, int):
        raise V08OpenAffordanceError("INVALID_FIELDS", "ttl_minutes must be an integer")
    if not (MIN_TTL_MINUTES <= value <= MAX_TTL_MINUTES):
        raise V08OpenAffordanceError(
            "INVALID_FIELDS",
            f"ttl_minutes must be {MIN_TTL_MINUTES}..{MAX_TTL_MINUTES}",
        )
    return int(value)


def _require_mapping(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or not payload:
        raise V08OpenAffordanceError("INVALID_FIELDS", "payload must be a non-empty object")
    for key in payload:
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise V08OpenAffordanceError("INVALID_FIELDS", "blank or padded key forbidden")
    return dict(payload)


_FORBIDDEN_IDENTITY_KEYS = frozenset(
    {"uid", "author_side", "by", "actor", "subject", "owner", "euid", "gid", "authority"}
)


def _assert_no_identity_keys(payload: Mapping[str, Any]) -> None:
    found = sorted(set(payload) & _FORBIDDEN_IDENTITY_KEYS)
    if found:
        raise V08OpenAffordanceError(
            "CLIENT_IDENTITY_REFUSED",
            "identity keys are never accepted from the client: " + ", ".join(found),
        )


# ---------------------------------------------------------------------------
# State persistence (reads are 100% zero-write)
# ---------------------------------------------------------------------------


def state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / STATE_FILENAME


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "created_at": None,
        "updated_at": None,
        "traces": [],
        "inscriptions_by_object": {},
        "object_placements": {},
        "object_settings": {},
        "object_relations": {},
        "combinations": [],
        "use_records": [],
        "routines": [],
        "operations": {},
    }


def _read_state_zero_write(root: Path | str) -> dict[str, Any]:
    """Parse the state file without creating anything.  A missing/corrupt file reads empty."""

    path = state_path(root)
    try:
        if not path.is_file() or path.is_symlink():
            return _empty_state()
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return _empty_state()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return _empty_state()
    if not isinstance(parsed, Mapping):
        return _empty_state()
    state = _empty_state()
    for key in state:
        if key in parsed:
            state[key] = copy.deepcopy(parsed[key])
    if not isinstance(state["traces"], list):
        state["traces"] = []
    for key in (
        "inscriptions_by_object",
        "object_placements",
        "object_settings",
        "object_relations",
    ):
        if not isinstance(state[key], Mapping):
            state[key] = {}
    if not isinstance(state["combinations"], list):
        state["combinations"] = []
    if not isinstance(state["use_records"], list):
        state["use_records"] = []
    if not isinstance(state["routines"], list):
        state["routines"] = []
    if not isinstance(state["operations"], Mapping):
        state["operations"] = {}
    return state


def _atomic_write_state(root: Path | str, state: Mapping[str, Any]) -> None:
    path = state_path(root)
    if path.exists() and path.is_symlink():
        raise V08OpenAffordanceError("STATE_SYMLINK", "state file is a symlink")
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise V08OpenAffordanceError("STATE_SYMLINK", "state directory is a symlink")
    parent.mkdir(parents=True, exist_ok=True)
    doc = copy.deepcopy(dict(state))
    encoded = (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".v08_living_", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, str(path))
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _bump(state: dict[str, Any], now_iso: str) -> None:
    state["schema_version"] = SCHEMA_VERSION
    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = now_iso
    if not state.get("created_at"):
        state["created_at"] = now_iso


# ---------------------------------------------------------------------------
# Reachable object discovery (pure read)
# ---------------------------------------------------------------------------


def _read_json_zero_write(path: Path) -> Optional[dict[str, Any]]:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _base_objects() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _BASE_OBJECTS:
        item = copy.deepcopy(row)
        item["source"] = "base_home"
        out.append(item)
    return out


def _containment_objects(root: Path) -> list[dict[str, Any]]:
    doc = _read_json_zero_write(Path(root) / "phone" / "stance_containment.json")
    if not doc:
        return []
    domain = doc.get("containment_domain")
    if not isinstance(domain, Mapping):
        return []
    out: list[dict[str, Any]] = []
    for container in domain.get("containers") or ():
        if not isinstance(container, Mapping):
            continue
        cid = str(container.get("container_id") or "").strip()
        if not cid:
            continue
        out.append({
            "object_id": f"containment_container:{cid}",
            "label_cn": str(container.get("label") or "手边容器").strip()[:40],
            "location_hint": str(container.get("place_id") or "书桌").strip()[:40],
            "traits": ["container", "insertable", "combinable"],
            "is_outdoor": False,
            "source": "stance_containment",
        })
    for item in domain.get("items") or ():
        if not isinstance(item, Mapping):
            continue
        iid = str(item.get("item_id") or "").strip()
        if not iid:
            continue
        item_class = str(item.get("item_class") or "").strip()
        traits = ["movable", "combinable"]
        if item_class in ("writing_tool", "stationery", "eyewear"):
            traits.append("insertable")
        if item_class in ("writing_tool", "stationery"):
            traits.append("usable")
        out.append({
            "object_id": f"containment_item:{iid}",
            "label_cn": str(item.get("label") or "手边小物").strip()[:40],
            "location_hint": str(item.get("place_id") or "书桌").strip()[:40],
            "traits": traits,
            "is_outdoor": False,
            "source": "stance_containment",
        })
    return out


def _workbench_objects(root: Path) -> list[dict[str, Any]]:
    doc = None
    for fname in ("workbench_tray.json", "workbench_curio.json"):
        doc = _read_json_zero_write(Path(root) / "phone" / fname)
        if doc is not None:
            break
    if not doc:
        return []
    tray = doc.get("tray") if isinstance(doc.get("tray"), Mapping) else doc
    if not isinstance(tray, Mapping):
        return []
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(tray.get("images") or ()):
        if not isinstance(row, Mapping):
            continue
        ref = str(row.get("album_artifact_id") or row.get("artifact_id") or f"image_{idx}").strip()
        out.append({
            "object_id": f"workbench_image:{_safe_token(ref)}",
            "label_cn": "工作台上的相片",
            "location_hint": "工作台托盘",
            "traits": ["movable", "combinable"],
            "is_outdoor": False,
            "source": "workbench_tray",
        })
    for idx, row in enumerate(tray.get("materials") or ()):
        if not isinstance(row, Mapping):
            continue
        ref = str(row.get("artifact_id") or f"material_{idx}").strip()
        out.append({
            "object_id": f"workbench_material:{_safe_token(ref)}",
            "label_cn": "工作台上的素材",
            "location_hint": "工作台托盘",
            "traits": ["movable", "combinable", "inscribable"],
            "is_outdoor": False,
            "source": "workbench_tray",
        })
    work = tray.get("work")
    if isinstance(work, Mapping) and str(work.get("artifact_id") or "").strip():
        ref = str(work.get("artifact_id")).strip()
        out.append({
            "object_id": f"workbench_work:{_safe_token(ref)}",
            "label_cn": "工作台上的未完稿",
            "location_hint": "工作台托盘",
            "traits": ["inscribable", "combinable"],
            "is_outdoor": False,
            "source": "workbench_tray",
        })
    return out


def _property_objects(
    root: Path,
    *,
    current_location: Optional[str] = None,
) -> list[dict[str, Any]]:
    doc = None
    for rel in (
        ("phone", "world_property_economy.json"),
        ("world", "state", "world_property_economy.json"),
        ("world", "world_property_economy.json"),
        ("state", "world_property_economy.json"),
    ):
        doc = _read_json_zero_write(Path(root).joinpath(*rel))
        if doc is not None:
            break
    if not doc or not isinstance(doc.get("items"), list):
        return []
    out: list[dict[str, Any]] = []
    for item in doc["items"]:
        if not isinstance(item, Mapping):
            continue
        iid = str(item.get("item_instance_id") or "").strip()
        if not iid:
            continue
        location = str(item.get("current_location") or "").strip()
        if current_location is not None:
            # Away from home only what the economy already moved along stays in
            # the hand's reach; in-transit rows are still travelling, not present.
            if location.startswith("in_transit/"):
                continue
            if not location or not _locations_match(location, current_location):
                continue
        catalog = str(item.get("catalog_item_id") or "").strip().lower()
        traits = ["movable", "usable"]
        if any(token in catalog for token in ("shelf", "desk", "table", "stand")):
            traits.append("container")
        if "lamp" in catalog:
            traits.append("switchable")
        if any(token in catalog for token in ("cushion", "blanket", "cloth", "towel")):
            traits.append("thermal_or_damp")
        out.append({
            "object_id": f"world_item:{_safe_token(iid)}",
            "label_cn": "已经入手的居家小物",
            "location_hint": "家中" if location != "delivery_pending" else "待送达",
            "traits": traits,
            "is_outdoor": False,
            "source": "world_property_economy",
        })
    return out


def _safe_token(value: Any, fallback: str = "item") -> str:
    text = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in str(value or "").strip())
    text = text.strip("._-")
    return (text or fallback)[:64]


# ---------------------------------------------------------------------------
# Location, WorldGen / world-editor object bridge, and focus cue (pure reads)
# ---------------------------------------------------------------------------


def _first_location_text(row: Mapping[str, Any]) -> Optional[str]:
    for key in ("place_id", "location_id", "current_location_id", "area_id", "id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_location_id(doc: Mapping[str, Any]) -> Optional[str]:
    """Pick the real current place out of the shapes already written on disk."""

    for key in ("current_location_id", "location_id", "current_place_id", "place_id"):
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    location = doc.get("location")
    if isinstance(location, Mapping):
        found = _first_location_text(location)
        if found:
            return found
    elif isinstance(location, str) and location.strip():
        return location.strip()
    current = doc.get("current_location")
    if isinstance(current, Mapping):
        found = _first_location_text(current)
        if found:
            return found
    elif isinstance(current, str) and current.strip():
        return current.strip()
    return None


def current_location_id(root: Path | str) -> str:
    """The real current place, read-only.  Missing state honestly means home."""

    base = Path(root)
    for rel in LOCATION_STATE_FILES:
        doc = _read_json_zero_write(base.joinpath(*rel))
        if not doc:
            continue
        found = _extract_location_id(doc)
        if not found:
            continue
        found = found.strip()
        if found.lower().startswith("in_transit/"):
            found = found.split("/", 1)[1]
        if found:
            return found
    return "home"


def _location_scope(value: Any) -> str:
    return str(value or "").strip().lower().replace("\\", "/").split("/")[0]


def _is_home_location(value: Any) -> bool:
    scope = _location_scope(value)
    return bool(scope) and scope in HOME_LOCATION_SCOPES


def _looks_like_home_name(value: Any) -> bool:
    text = str(value or "")
    return any(token in text for token in HOME_NAME_TOKENS)


def _is_outdoor_location(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return any(token in text for token in _OUTDOOR_LOCATION_TOKENS)


def _locations_match(candidate: Any, current: Any) -> bool:
    left = str(candidate or "").strip().lower().replace("\\", "/").rstrip("/")
    right = str(current or "").strip().lower().replace("\\", "/").rstrip("/")
    if not left or not right:
        return False
    if left == right:
        return True
    if left.startswith(right + "/") or right.startswith(left + "/"):
        return True
    return _is_home_location(left) and _is_home_location(right)


def _world_object_matches_location(object_location: Any, current: Any) -> bool:
    location = str(object_location or "").strip()
    if not location:
        # An unplaced catalogue row lives wherever the resident is at home.
        return _is_home_location(current)
    if _locations_match(location, current):
        return True
    return _is_home_location(current) and _is_home_location(location)


def _filter_trait_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    out: list[str] = []
    for raw in value:
        if isinstance(raw, str):
            trait = raw.strip()
            if trait in TRAITS and trait not in out:
                out.append(trait)
    return out


def _infer_traits_from_world_object(obj: Mapping[str, Any]) -> list[str]:
    """Deterministically infer legal traits from kind / name / portability / capacity."""

    traits: set[str] = set()
    kind = str(obj.get("kind") or obj.get("type") or "").strip().lower()
    traits.update(_KIND_TRAITS.get(kind, ()))
    name = str(obj.get("name") or obj.get("label") or "")
    for token, hinted in _NAME_TRAITS:
        if token in name:
            traits.update(hinted)
    if obj.get("portable") is True:
        traits.add("movable")
    capacity = obj.get("capacity")
    if isinstance(capacity, int) and not isinstance(capacity, bool) and capacity > 0:
        traits.update(("container", "insertable"))
    actions = obj.get("actions")
    if isinstance(actions, Sequence) and not isinstance(actions, (str, bytes, bytearray)):
        for action in actions:
            traits.update(_ACTION_TRAITS.get(str(action).strip().lower(), ()))
    if not traits:
        traits.add("usable")
    return [trait for trait in _TRAIT_ORDER if trait in traits]


def _object_affordance_traits(obj: Mapping[str, Any]) -> list[str]:
    explicit = _filter_trait_list(obj.get("affordance_traits"))
    if not explicit:
        explicit = _filter_trait_list(obj.get("traits"))
    return explicit or _infer_traits_from_world_object(obj)


def _rows_of(doc: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    rows = doc.get(key)
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _world_package_objects(root: Path, current_location: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rel, source in WORLD_PACKAGE_OBJECT_FILES:
        doc = _read_json_zero_write(root.joinpath(*rel))
        if not doc:
            continue
        for obj in _rows_of(doc, "objects"):
            object_id = str(obj.get("object_id") or obj.get("id") or "").strip()
            if not object_id:
                continue
            location = str(obj.get("location_id") or obj.get("room_id") or "").strip()
            if not _world_object_matches_location(location, current_location):
                continue
            label = str(obj.get("name") or obj.get("label") or object_id).strip()[:40]
            note = str(obj.get("note") or obj.get("description") or "").strip()[:40]
            out.append({
                "object_id": object_id,
                "label_cn": label or object_id[:40],
                "location_hint": note or location or ("家中" if _is_home_location(current_location) else current_location),
                "traits": _object_affordance_traits(obj),
                "is_outdoor": _is_outdoor_location(location),
                "source": source,
            })
    return out


def _looks_like_editor_world(doc: Mapping[str, Any]) -> bool:
    return str(doc.get("format") or "").strip() == "nyairo.world-package.v3" or (
        isinstance(doc.get("rooms"), (list, Mapping)) and isinstance(doc.get("objects"), (list, Mapping))
    )


def _editor_room_map(doc: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rooms: dict[str, Mapping[str, Any]] = {}
    for room in _rows_of(doc, "rooms"):
        room_id = str(room.get("id") or room.get("room_id") or "").strip()
        if room_id:
            rooms[room_id] = room
    return rooms


def _editor_room_matches(
    room_id: str,
    rooms: Mapping[str, Mapping[str, Any]],
    current_location: str,
) -> bool:
    if _locations_match(room_id, current_location):
        return True
    if not _is_home_location(current_location):
        return False
    home_rooms = [
        known_id
        for known_id, room in rooms.items()
        if _is_home_location(known_id) or _looks_like_home_name(room.get("name"))
    ]
    if home_rooms:
        return room_id in home_rooms
    # A single-space custom world is that resident's home, whatever the room id.
    return len(rooms) == 1


def _editor_world_objects(root: Path, current_location: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rel in EDITOR_WORLD_FILES:
        doc = _read_json_zero_write(root.joinpath(*rel))
        if not doc or not _looks_like_editor_world(doc):
            continue
        rooms = _editor_room_map(doc)
        for obj in _rows_of(doc, "objects"):
            object_id = str(obj.get("id") or obj.get("object_id") or "").strip()
            if not object_id:
                continue
            room_id = str(obj.get("room_id") or obj.get("location_id") or "").strip()
            if not _editor_room_matches(room_id, rooms, current_location):
                continue
            room = rooms.get(room_id) or {}
            label = str(obj.get("name") or obj.get("label") or object_id).strip()[:40]
            hint = str(room.get("name") or obj.get("description") or room_id).strip()[:40]
            is_outdoor = (
                str(room.get("kind") or "").strip().lower() == "outdoor"
                or _is_outdoor_location(room_id)
                or _is_outdoor_location(room.get("name"))
            )
            out.append({
                "object_id": object_id,
                "label_cn": label or object_id[:40],
                "location_hint": hint or ("家中" if _is_home_location(current_location) else current_location),
                "traits": _object_affordance_traits(obj),
                "is_outdoor": is_outdoor,
                "source": "world_editor",
            })
    return out


def _merge_discovered(
    seen: dict[str, dict[str, Any]],
    item: Mapping[str, Any],
) -> None:
    object_id = str(item.get("object_id") or "")
    if not object_id:
        return
    if object_id not in seen:
        seen[object_id] = dict(item)
        return
    existing = seen[object_id]
    if str(existing.get("source") or "") == str(item.get("source") or ""):
        return
    # A custom object keeps its own id when free; on a real collision the source
    # prefix keeps both identities stable instead of silently dropping one.
    prefixed = f"{item.get('source') or 'object'}:{_safe_token(object_id)}"
    if prefixed in seen:
        return
    replacement = dict(item)
    replacement["object_id"] = prefixed
    seen[prefixed] = replacement


def discover_reachable_objects(
    root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Every real object this instance can reach right now (100% zero-write).

    At home the seven fixed anchors are present exactly as before.  Away from
    home they give way to the WorldGen package / world-editor objects authored for
    the current place plus what is genuinely carried.  Already-written owner files
    (containment, workbench tray, property economy) add their real objects; a file
    that does not exist contributes nothing and never raises.
    """

    del now_iso  # the object set is structural, not time-dependent
    base = Path(root)
    current = current_location_id(base)
    at_home = _is_home_location(current)

    seen: dict[str, dict[str, Any]] = {}
    if at_home:
        for item in _base_objects():
            seen[item["object_id"]] = item

    supplements: list[dict[str, Any]] = []
    for loader in (_world_package_objects, _editor_world_objects):
        try:
            supplements.extend(loader(base, current))
        except Exception:  # noqa: BLE001 - a broken catalogue is simply absent
            continue
    try:
        containment = _containment_objects(base)
        if not at_home:
            # Containers live on the home desk; hand-held items may travel.
            containment = [
                row for row in containment
                if not str(row.get("object_id") or "").startswith("containment_container:")
            ]
        supplements.extend(containment)
    except Exception:  # noqa: BLE001
        pass
    if at_home:
        try:
            supplements.extend(_workbench_objects(base))
        except Exception:  # noqa: BLE001
            pass
    try:
        supplements.extend(
            _property_objects(base, None if at_home else current)
        )
    except Exception:  # noqa: BLE001
        pass

    for item in supplements:
        _merge_discovered(seen, item)
    return [seen[key] for key in sorted(seen)]


def _object_index(root: Path | str) -> dict[str, dict[str, Any]]:
    return {obj["object_id"]: obj for obj in discover_reachable_objects(root)}


def _require_object(index: Mapping[str, Mapping[str, Any]], object_id: Any) -> dict[str, Any]:
    oid = str(object_id or "").strip()
    if not oid:
        raise V08OpenAffordanceError("INVALID_FIELDS", "object_id is required")
    obj = index.get(oid)
    if obj is None:
        raise V08OpenAffordanceError("OBJECT_NOT_FOUND", f"unknown reachable object: {oid}")
    return dict(obj)


# ---------------------------------------------------------------------------
# Illness gate (pure read)
# ---------------------------------------------------------------------------


def _blocks_strenuous_outing(root: Path | str) -> bool:
    doc = _read_json_zero_write(Path(root) / "phone" / "v03_ecology.json")
    if not doc:
        return False
    for container_key in ("illness", "capability_envelope"):
        block = doc.get(container_key)
        if isinstance(block, Mapping) and isinstance(block.get("blocks_strenuous_outing"), bool):
            return bool(block["blocks_strenuous_outing"])
    if isinstance(doc.get("blocks_strenuous_outing"), bool):
        return bool(doc["blocks_strenuous_outing"])
    illness = doc.get("illness") if isinstance(doc.get("illness"), Mapping) else {}
    cap = illness.get("capability_envelope") if isinstance(illness.get("capability_envelope"), Mapping) else {}
    if isinstance(cap.get("blocks_strenuous_outing"), bool):
        return bool(cap["blocks_strenuous_outing"])
    phase = str(illness.get("phase") or doc.get("phase") or "").strip().lower()
    try:
        severity = float(illness.get("severity") or 0.0)
    except (TypeError, ValueError):
        severity = 0.0
    if phase in ("acute", "acute_illness"):
        return True
    if phase.startswith("convalescen"):
        return severity >= 0.22
    return False


# ---------------------------------------------------------------------------
# Operation-key idempotency
# ---------------------------------------------------------------------------


def _fingerprint(action: str, actor_side: str, core: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"action": action, "actor_side": actor_side, "core": core},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _core_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in ("action", "operation_key", "intent_fingerprint", "expected_revision")
    }


def _expected_revision(body: Any) -> Optional[int]:
    if not isinstance(body, Mapping):
        return None
    raw = body.get("expected_revision")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise V08OpenAffordanceError(
            "INVALID_FIELDS", "expected_revision must be a non-negative integer"
        )
    return int(raw)


def _check_replay(
    state: Mapping[str, Any],
    *,
    operation_key: Optional[str],
    fingerprint: str,
    root: Path | str | None = None,
    body: Any = None,
) -> Optional[dict[str, Any]]:
    if not operation_key:
        return None

    expected_revision = _expected_revision(body)
    operations = state.get("operations") if isinstance(state.get("operations"), Mapping) else {}
    existing = operations.get(operation_key)
    if isinstance(existing, Mapping):
        if str(existing.get("fingerprint") or "") != fingerprint:
            raise V08OpenAffordanceError(
                "OPERATION_KEY_CONFLICT",
                "the same operation_key was used for a different action",
            )
        receipt = existing.get("receipt")
        out = copy.deepcopy(dict(receipt)) if isinstance(receipt, Mapping) else {}
        out["already_applied"] = True
        return out

    # The active row is gone; a long-horizon retry may still be satisfied from the
    # bounded commit ring or the ``phone/operations.jsonl`` journal.  Anything
    # older than that window is an explicit RETRY_WINDOW_EXPIRED, never a fresh act.
    outcome, detail = reconcile_evicted_or_active_op(
        root,
        state,
        operation_key,
        dict_key="operations",
        expected_revision=expected_revision,
        payload_hash=fingerprint,
    )
    if outcome == "conflict":
        raise V08OpenAffordanceError(
            "OPERATION_KEY_CONFLICT",
            "the same operation_key was used for a different action",
        )
    if outcome == "expired":
        raise V08OpenAffordanceError(
            "RETRY_WINDOW_EXPIRED",
            "operation_key is older than the retained retry window",
        )
    if outcome in ("hit_full", "hit_evicted"):
        out = copy.deepcopy(detail) if isinstance(detail, Mapping) else {}
        out["already_applied"] = True
        return out

    if expected_revision is not None and int(state.get("revision") or 0) != expected_revision:
        raise V08OpenAffordanceError(
            "REVISION_CONFLICT",
            f"expected revision {expected_revision}, current revision is {state.get('revision')}",
        )
    return None


def _store_operation(
    state: dict[str, Any],
    *,
    operation_key: Optional[str],
    fingerprint: str,
    receipt: Mapping[str, Any],
) -> None:
    if not operation_key:
        return
    stamp_receipt(
        state,
        str(operation_key),
        {
            "fingerprint": fingerprint,
            "receipt": copy.deepcopy(dict(receipt)),
            "status": "applied",
            "ok": True,
        },
        revision=int(state.get("revision") or 0),
        dict_key="operations",
        payload_hash=fingerprint,
    )
    trim_receipts_by_op_seq(
        state,
        dict_key="operations",
        max_keep=MAX_OPERATION_RECEIPTS,
    )


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------


def _is_trace_active(trace: Mapping[str, Any], now_dt: datetime) -> bool:
    if str(trace.get("fade_mode") or "timed") == "until_touched":
        return True
    return _iso_cmp(trace.get("expires_at"), now_dt) == 1


def _trace_id(seed: str) -> str:
    return "trace_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _clear_until_touched(
    state: dict[str, Any],
    *,
    object_id: str,
    author_side: Optional[str],
    now_dt: datetime,
) -> list[str]:
    """Drop ``until_touched`` traces the act really touched.  Returns the cleared ids."""

    kept: list[dict[str, Any]] = []
    cleared: list[str] = []
    for trace in state.get("traces") or []:
        if not isinstance(trace, Mapping):
            continue
        same_object = str(trace.get("object_id") or "") == object_id
        author = str(trace.get("author_side") or "subject")
        matched_author = author_side is None or author == author_side
        if (
            same_object
            and matched_author
            and str(trace.get("fade_mode") or "timed") == "until_touched"
            and _is_trace_active(trace, now_dt)
        ):
            cleared.append(str(trace.get("trace_id") or ""))
            continue
        kept.append(dict(trace))
    state["traces"] = kept
    return [tid for tid in cleared if tid]


# ---------------------------------------------------------------------------
# Universal verbs
# ---------------------------------------------------------------------------


def _validate_verb_params(
    obj: Mapping[str, Any],
    verb: str,
    payload: Mapping[str, Any],
    index: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    traits = set(obj.get("traits") or ())
    spec = UNIVERSAL_VERBS.get(verb)
    if spec is None:
        raise V08OpenAffordanceError("INVALID_FIELDS", f"unknown verb: {verb}")
    if not any(trait in traits for trait in spec["requires_any"]):
        raise V08OpenAffordanceError(
            "TRAIT_MISMATCH",
            f"{obj.get('object_id')} lacks {spec['requires_any']} for {verb}",
        )

    if verb == "RELOCATE":
        target_spot = _text(payload.get("target_spot"), min_len=1, max_len=80, field="target_spot")
        return {"target_spot": target_spot}

    if verb == "INSERT_EXTRACT":
        mode = str(payload.get("mode") or "").strip()
        if mode not in ("insert", "extract"):
            raise V08OpenAffordanceError("INVALID_FIELDS", "mode must be insert or extract")
        container_id = _text(
            payload.get("container_object_id"),
            min_len=1,
            max_len=120,
            field="container_object_id",
        )
        if container_id == obj.get("object_id"):
            raise V08OpenAffordanceError("TRAIT_MISMATCH", "an object cannot contain itself")
        container = index.get(container_id)
        if container is None:
            raise V08OpenAffordanceError(
                "OBJECT_NOT_FOUND", f"unknown container object: {container_id}"
            )
        if "container" not in set(container.get("traits") or ()):
            raise V08OpenAffordanceError(
                "TRAIT_MISMATCH", f"{container_id} is not a container"
            )
        if "insertable" not in traits and "container" not in traits:
            raise V08OpenAffordanceError(
                "TRAIT_MISMATCH", f"{obj.get('object_id')} is not insertable"
            )
        return {"mode": mode, "container_object_id": container_id}

    if verb == "INSCRIBE":
        note_text = _text(
            payload.get("note_text"), min_len=1, max_len=MAX_INSCRIPTION_CHARS, field="note_text"
        )
        return {"note_text": note_text, "visibility": _visibility(payload.get("visibility"))}

    if verb == "TOGGLE_ADJUST":
        raw = payload.get("setting_value")
        if isinstance(raw, bool):
            setting: Any = raw
        elif isinstance(raw, int):
            setting = int(raw)
        elif isinstance(raw, str) and raw.strip():
            setting = raw.strip()[:40]
        else:
            raise V08OpenAffordanceError(
                "INVALID_FIELDS", "setting_value must be a non-empty string, integer or boolean"
            )
        return {"setting_value": setting}

    if verb == "USE_CONSUME":
        outcome_note = _text(
            payload.get("outcome_note"), min_len=1, max_len=80, field="outcome_note"
        )
        return {"outcome_note": outcome_note}

    # COMBINE_SPLIT
    mode = str(payload.get("mode") or payload.get("combine_mode") or "combine").strip()
    if mode not in ("combine", "split"):
        raise V08OpenAffordanceError("INVALID_FIELDS", "mode must be combine or split")
    partner_id = _text(
        payload.get("partner_object_id"),
        min_len=1,
        max_len=120,
        field="partner_object_id",
    )
    if partner_id == obj.get("object_id"):
        raise V08OpenAffordanceError("TRAIT_MISMATCH", "an object cannot combine with itself")
    partner = index.get(partner_id)
    if partner is None:
        raise V08OpenAffordanceError("OBJECT_NOT_FOUND", f"unknown object: {partner_id}")
    return {"mode": mode, "partner_object_id": partner_id}


def _execute_universal_verb_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Apply one general verb to a real reachable object (subject only)."""

    if actor_side != "subject":
        raise V08OpenAffordanceError(
            "FORBIDDEN_ACTOR_SIDE", "universal verbs are the subject's own actions"
        )
    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    verb = str(body.get("verb") or "").strip().upper()
    if verb not in UNIVERSAL_VERBS:
        raise V08OpenAffordanceError("INVALID_FIELDS", "verb must be one of the six universal verbs")
    object_id = str(body.get("object_id") or "").strip()
    if not object_id:
        raise V08OpenAffordanceError("INVALID_FIELDS", "object_id is required")

    now_dt, now_text = _resolve_now(now_iso)

    operation_key = _optional_text(body.get("operation_key"), max_len=120, field="operation_key")
    fingerprint = _fingerprint("execute_universal_verb", actor_side, _core_fields(body))

    state = _read_state_zero_write(root)
    replay = _check_replay(
        state, operation_key=operation_key, fingerprint=fingerprint, root=root, body=body
    )
    if replay is not None:
        return replay

    index = _object_index(root)
    obj = _require_object(index, object_id)

    if body.get("strenuous_outing") is not None:
        strenuous = _strict_bool(body.get("strenuous_outing"), field="strenuous_outing")
    else:
        strenuous = False
    if (strenuous or bool(obj.get("is_outdoor"))) and _blocks_strenuous_outing(root):
        raise V08OpenAffordanceError(
            "ILLNESS_GATE_BLOCKED",
            "the body is unwell; a heavy outing action is not allowed right now",
        )

    params = _validate_verb_params(obj, verb, body, index)
    if verb == "INSERT_EXTRACT" and params["mode"] == "extract":
        relation = state["object_relations"].get(object_id)
        if (
            not isinstance(relation, Mapping)
            or str(relation.get("container_object_id") or "") != params["container_object_id"]
        ):
            raise V08OpenAffordanceError(
                "TRAIT_MISMATCH",
                "the object is not inside that container, so it cannot be extracted",
            )

    receipt: dict[str, Any] = {
        "status": "applied",
        "action": "execute_universal_verb",
        "verb": verb,
        "object_id": object_id,
        "object_label_cn": obj.get("label_cn"),
        "at": now_text,
        "llm_calls": 0,
        "already_applied": False,
    }

    if verb == "RELOCATE":
        state["object_placements"][object_id] = {
            "target_spot": params["target_spot"],
            "at": now_text,
            "by": actor_side,
        }
        receipt["target_spot"] = params["target_spot"]
    elif verb == "INSERT_EXTRACT":
        if params["mode"] == "insert":
            state["object_relations"][object_id] = {
                "container_object_id": params["container_object_id"],
                "at": now_text,
                "by": actor_side,
            }
        else:
            state["object_relations"].pop(object_id, None)
        receipt.update(params)
    elif verb == "INSCRIBE":
        rows = list(state["inscriptions_by_object"].get(object_id) or [])
        row = {
            "inscription_id": "insc_" + hashlib.sha256(
                (object_id + params["note_text"] + now_text).encode("utf-8")
            ).hexdigest()[:16],
            "note_text": params["note_text"],
            "author_side": actor_side,
            "visibility": params["visibility"],
            "created_at": now_text,
        }
        rows.append(row)
        state["inscriptions_by_object"][object_id] = rows[-MAX_INSCRIPTIONS_PER_OBJECT:]
        receipt["inscription"] = copy.deepcopy(row)
    elif verb == "TOGGLE_ADJUST":
        state["object_settings"][object_id] = {
            "setting_value": params["setting_value"],
            "at": now_text,
            "by": actor_side,
        }
        receipt["setting_value"] = params["setting_value"]
    elif verb == "USE_CONSUME":
        state["use_records"] = (
            list(state["use_records"])
            + [{
                "object_id": object_id,
                "outcome_note": params["outcome_note"],
                "at": now_text,
                "by": actor_side,
            }]
        )[-MAX_USE_RECORDS:]
        receipt["outcome_note"] = params["outcome_note"]
    elif verb == "COMBINE_SPLIT":
        if params["mode"] == "combine":
            state["combinations"] = (
                list(state["combinations"])
                + [{
                    "object_id": object_id,
                    "partner_object_id": params["partner_object_id"],
                    "at": now_text,
                    "by": actor_side,
                }]
            )
        else:
            wanted = {object_id, params["partner_object_id"]}
            state["combinations"] = [
                row
                for row in state["combinations"]
                if not (
                    isinstance(row, Mapping)
                    and {str(row.get("object_id")), str(row.get("partner_object_id"))} == wanted
                )
            ]
        receipt.update(params)

    # A real touch fades every until_touched sticker on that object.
    touched = _clear_until_touched(state, object_id=object_id, author_side=None, now_dt=now_dt)
    if touched:
        receipt["touched_cleared_traces"] = touched

    _bump(state, now_text)
    _store_operation(
        state,
        operation_key=operation_key,
        fingerprint=fingerprint,
        receipt=receipt,
    )
    _atomic_write_state(root, state)
    return receipt


# ---------------------------------------------------------------------------
# Living traces
# ---------------------------------------------------------------------------


def _apply_living_trace_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Leave one visible state sticker on a real object."""

    if actor_side not in ("subject", "partner"):
        raise V08OpenAffordanceError("FORBIDDEN_ACTOR_SIDE", f"unknown actor_side: {actor_side!r}")
    body = _require_mapping(payload)
    _assert_no_identity_keys(body)

    object_id = str(body.get("object_id") or "").strip()
    if not object_id:
        raise V08OpenAffordanceError("INVALID_FIELDS", "object_id is required")
    gesture = _text(body.get("gesture_cn"), min_len=1, max_len=MAX_GESTURE_CHARS, field="gesture_cn")
    sticker = _text(
        body.get("state_sticker_cn"), min_len=1, max_len=MAX_STICKER_CHARS, field="state_sticker_cn"
    )
    fade_mode = _fade_mode(body.get("fade_mode"))
    ttl = _ttl_minutes(body.get("ttl_minutes")) if fade_mode == "timed" else None
    if actor_side == "partner":
        # Partner traces are always shared; a private partner trace is never written.
        if body.get("visibility") not in (None, "shared"):
            raise V08OpenAffordanceError("INVALID_FIELDS", "partner traces are always shared")
        visibility = "shared"
    else:
        visibility = _visibility(body.get("visibility"))

    now_dt, now_text = _resolve_now(now_iso)
    operation_key = _optional_text(body.get("operation_key"), max_len=120, field="operation_key")
    fingerprint = _fingerprint("apply_living_trace", actor_side, _core_fields(body))

    state = _read_state_zero_write(root)
    replay = _check_replay(
        state, operation_key=operation_key, fingerprint=fingerprint, root=root, body=body
    )
    if replay is not None:
        return replay

    obj = _require_object(_object_index(root), object_id)

    expires_at = (
        _format_iso(now_dt + timedelta(minutes=ttl)) if fade_mode == "timed" and ttl else None
    )
    trace = {
        "trace_id": _trace_id(
            "|".join((object_id, actor_side, gesture, sticker, fade_mode, now_text))
        ),
        "object_id": object_id,
        "object_label_cn": obj.get("label_cn"),
        "author_side": actor_side,
        "gesture_cn": gesture,
        "state_sticker_cn": sticker,
        "fade_mode": fade_mode,
        "ttl_minutes": ttl,
        "created_at": now_text,
        "expires_at": expires_at,
        "visibility": visibility,
    }

    # A new sticker by the subject is a real touch of the object; a partner's own
    # sticker only replaces that partner's own until_touched leftovers.
    touched = _clear_until_touched(
        state,
        object_id=object_id,
        author_side=None if actor_side == "subject" else "partner",
        now_dt=now_dt,
    )

    traces = [dict(row) for row in state.get("traces") or [] if isinstance(row, Mapping)]
    traces.append(trace)

    # Per (object, author) keep at most two, replacing the oldest peacefully.
    def _pair_key(row: Mapping[str, Any]) -> tuple[str, str]:
        return (str(row.get("object_id") or ""), str(row.get("author_side") or "subject"))

    pair_rows = [row for row in traces if _pair_key(row) == _pair_key(trace)]
    if len(pair_rows) > MAX_TRACES_PER_OBJECT_AUTHOR:
        for stale in pair_rows[: len(pair_rows) - MAX_TRACES_PER_OBJECT_AUTHOR]:
            traces.remove(stale)

    # Global and partner caps also replace the oldest rather than refusing a new act.
    def _trim(cap: int, author: Optional[str] = None) -> None:
        while True:
            active_rows = [row for row in traces if _is_trace_active(row, now_dt)]
            if len(active_rows) <= cap:
                return
            candidates = [
                row for row in active_rows
                if author is None or str(row.get("author_side") or "") == author
            ]
            if not candidates:
                return
            traces.remove(candidates[0])

    _trim(MAX_ACTIVE_TRACES)
    _trim(MAX_PARTNER_TRACES, author="partner")

    state["traces"] = traces

    receipt: dict[str, Any] = {
        "status": "applied",
        "action": "apply_living_trace",
        "trace_id": trace["trace_id"],
        "object_id": object_id,
        "object_label_cn": obj.get("label_cn"),
        "author_side": actor_side,
        "visibility": visibility,
        "fade_mode": fade_mode,
        "expires_at": expires_at,
        "at": now_text,
        "llm_calls": 0,
        "already_applied": False,
    }
    if touched:
        receipt["touched_cleared_traces"] = touched

    _bump(state, now_text)
    _store_operation(
        state,
        operation_key=operation_key,
        fingerprint=fingerprint,
        receipt=receipt,
    )
    _atomic_write_state(root, state)
    return receipt


def leave_partner_trace(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Partner entry point: always a shared trace, never a private one."""

    body = dict(payload or {})
    body.pop("visibility", None)
    return apply_living_trace(root, body, actor_side="partner", now_iso=now_iso)


def _find_trace(state: Mapping[str, Any], trace_id: str) -> Optional[dict[str, Any]]:
    for row in state.get("traces") or []:
        if isinstance(row, Mapping) and str(row.get("trace_id") or "") == trace_id:
            return dict(row)
    return None


def _remove_trace(state: dict[str, Any], trace_id: str) -> None:
    state["traces"] = [
        dict(row)
        for row in state.get("traces") or []
        if not (isinstance(row, Mapping) and str(row.get("trace_id") or "") == trace_id)
    ]


def _clear_living_trace_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Subject clears her own sticker or tidies away a partner's sticker."""

    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    trace_id = _text(body.get("trace_id"), min_len=1, max_len=120, field="trace_id")
    now_dt, now_text = _resolve_now(now_iso)
    operation_key = _optional_text(body.get("operation_key"), max_len=120, field="operation_key")
    fingerprint = _fingerprint("clear_living_trace", "subject", _core_fields(body))

    state = _read_state_zero_write(root)
    replay = _check_replay(
        state, operation_key=operation_key, fingerprint=fingerprint, root=root, body=body
    )
    if replay is not None:
        return replay
    trace = _find_trace(state, trace_id)
    if trace is None:
        raise V08OpenAffordanceError("TRACE_NOT_FOUND", f"unknown trace: {trace_id}")

    _remove_trace(state, trace_id)
    receipt = {
        "status": "applied",
        "action": "clear_living_trace",
        "cleared_trace_id": trace_id,
        "object_id": trace.get("object_id"),
        "previous_author_side": trace.get("author_side"),
        "at": now_text,
        "llm_calls": 0,
        "already_applied": False,
    }
    _bump(state, now_text)
    _store_operation(state, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt)
    _atomic_write_state(root, state)
    return receipt


def _clear_partner_trace_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Partner clears only their own sticker; touching a subject one is a named refusal."""

    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    trace_id = _text(body.get("trace_id"), min_len=1, max_len=120, field="trace_id")
    now_dt, now_text = _resolve_now(now_iso)
    operation_key = _optional_text(body.get("operation_key"), max_len=120, field="operation_key")
    fingerprint = _fingerprint("clear_partner_trace", "partner", _core_fields(body))

    state = _read_state_zero_write(root)
    replay = _check_replay(
        state, operation_key=operation_key, fingerprint=fingerprint, root=root, body=body
    )
    if replay is not None:
        return replay
    trace = _find_trace(state, trace_id)
    if trace is None:
        raise V08OpenAffordanceError("TRACE_NOT_FOUND", f"unknown trace: {trace_id}")
    if str(trace.get("author_side") or "") != "partner":
        raise V08OpenAffordanceError(
            "AUTHOR_MISMATCH", "a partner may only clear their own sticker"
        )

    _remove_trace(state, trace_id)
    receipt = {
        "status": "applied",
        "action": "clear_partner_trace",
        "cleared_trace_id": trace_id,
        "object_id": trace.get("object_id"),
        "at": now_text,
        "llm_calls": 0,
        "already_applied": False,
    }
    _bump(state, now_text)
    _store_operation(state, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt)
    _atomic_write_state(root, state)
    return receipt


def _evaporate_expired_living_traces_locked(root: Path | str, now_iso: Optional[str]) -> int:
    """Drop naturally expired ``timed`` traces.  Zero tombstones, zero errors, zero LLM."""

    path = state_path(root)
    try:
        if not path.is_file() or path.is_symlink():
            return 0
    except OSError:
        return 0
    now_dt, now_text = _resolve_now(now_iso)
    state = _read_state_zero_write(root)
    kept: list[dict[str, Any]] = []
    evaporated = 0
    for row in state.get("traces") or []:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("fade_mode") or "timed") == "timed":
            cmp = _iso_cmp(row.get("expires_at"), now_dt)
            if cmp is not None and cmp <= 0:
                evaporated += 1
                continue
        kept.append(dict(row))
    if evaporated == 0:
        return 0
    state["traces"] = kept
    _bump(state, now_text)
    _atomic_write_state(root, state)
    return evaporated


# ---------------------------------------------------------------------------
# Personal habit routines
# ---------------------------------------------------------------------------


def _normalize_step(step: Any, index: Mapping[str, Mapping[str, Any]], *, position: int) -> dict[str, Any]:
    if not isinstance(step, Mapping):
        raise V08OpenAffordanceError("INVALID_FIELDS", f"steps[{position}] must be an object")
    _assert_no_identity_keys(step)
    kind = str(step.get("kind") or "").strip()
    if kind not in ("verb", "trace"):
        raise V08OpenAffordanceError("INVALID_FIELDS", f"steps[{position}].kind must be verb or trace")
    object_id = str(step.get("object_id") or "").strip()
    obj = _require_object(index, object_id)
    out: dict[str, Any] = {"kind": kind, "object_id": object_id}

    if kind == "verb":
        verb = str(step.get("verb") or "").strip().upper()
        if verb not in UNIVERSAL_VERBS:
            raise V08OpenAffordanceError(
                "INVALID_FIELDS", f"steps[{position}].verb is not a universal verb"
            )
        _validate_verb_params(obj, verb, step, index)
        out["verb"] = verb
        for key in (
            "target_spot",
            "mode",
            "container_object_id",
            "note_text",
            "setting_value",
            "outcome_note",
            "partner_object_id",
            "visibility",
            "strenuous_outing",
        ):
            if key in step and step.get(key) is not None:
                out[key] = copy.deepcopy(step[key])
        if verb != "INSCRIBE":
            out.pop("visibility", None)
    else:
        gesture = _text(
            step.get("gesture_cn"), min_len=1, max_len=MAX_GESTURE_CHARS, field="gesture_cn"
        )
        sticker = _text(
            step.get("state_sticker_cn"),
            min_len=1,
            max_len=MAX_STICKER_CHARS,
            field="state_sticker_cn",
        )
        fade_mode = _fade_mode(step.get("fade_mode"))
        out.update({
            "gesture_cn": gesture,
            "state_sticker_cn": sticker,
            "fade_mode": fade_mode,
        })
        if fade_mode == "timed":
            out["ttl_minutes"] = _ttl_minutes(step.get("ttl_minutes"))
        if step.get("visibility") is not None:
            out["visibility"] = _visibility(step.get("visibility"))
    return out


def _save_personal_routine_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Save a 2–4 step routine the subject built herself (max eight, no streaks)."""

    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    title = _text(body.get("title_cn"), min_len=2, max_len=40, field="title_cn")
    cue = str(body.get("when_cue") or "any").strip()
    if cue not in WHEN_CUES:
        raise V08OpenAffordanceError("INVALID_FIELDS", f"when_cue must be one of {sorted(WHEN_CUES)}")
    visibility = _visibility(body.get("visibility"))
    raw_steps = body.get("steps")
    if (
        not isinstance(raw_steps, Sequence)
        or isinstance(raw_steps, (str, bytes, bytearray))
        or not (MIN_ROUTINE_STEPS <= len(raw_steps) <= MAX_ROUTINE_STEPS)
    ):
        raise V08OpenAffordanceError(
            "INVALID_FIELDS",
            f"steps must contain {MIN_ROUTINE_STEPS}..{MAX_ROUTINE_STEPS} objects",
        )

    now_dt, now_text = _resolve_now(now_iso)
    operation_key = _optional_text(body.get("operation_key"), max_len=120, field="operation_key")
    fingerprint = _fingerprint("save_personal_routine", "subject", _core_fields(body))

    state = _read_state_zero_write(root)
    replay = _check_replay(
        state, operation_key=operation_key, fingerprint=fingerprint, root=root, body=body
    )
    if replay is not None:
        return replay

    index = _object_index(root)
    steps = [
        _normalize_step(step, index, position=pos)
        for pos, step in enumerate(raw_steps)
    ]
    routines = [dict(row) for row in state.get("routines") or [] if isinstance(row, Mapping)]
    active = [row for row in routines if str(row.get("status") or "active") != "retired"]
    if len(active) >= MAX_ROUTINES:
        raise V08OpenAffordanceError(
            "ROUTINE_LIMIT_REACHED", f"at most {MAX_ROUTINES} routines can be kept"
        )

    routine = {
        "routine_id": "routine_" + hashlib.sha256(
            (title + cue + now_text).encode("utf-8")
        ).hexdigest()[:16],
        "title_cn": title,
        "when_cue": cue,
        "steps": steps,
        "visibility": visibility,
        "status": "active",
        "times_used": 0,
        "last_used_at": None,
        "created_at": now_text,
    }
    routines.append(routine)
    state["routines"] = routines

    receipt = {
        "status": "applied",
        "action": "save_personal_routine",
        "routine_id": routine["routine_id"],
        "title_cn": title,
        "when_cue": cue,
        "step_count": len(steps),
        "visibility": visibility,
        "at": now_text,
        "llm_calls": 0,
        "already_applied": False,
    }
    _bump(state, now_text)
    _store_operation(state, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt)
    _atomic_write_state(root, state)
    return receipt


def _find_routine(state: Mapping[str, Any], routine_id: str) -> Optional[dict[str, Any]]:
    for row in state.get("routines") or []:
        if (
            isinstance(row, Mapping)
            and str(row.get("routine_id") or "") == routine_id
            and str(row.get("status") or "active") != "retired"
        ):
            return dict(row)
    return None


def _run_personal_routine_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Replay one routine step by step on real state; stop honestly at the first block."""

    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    routine_id = _text(body.get("routine_id"), min_len=1, max_len=120, field="routine_id")
    now_dt, now_text = _resolve_now(now_iso)
    operation_key = _optional_text(body.get("operation_key"), max_len=120, field="operation_key")
    fingerprint = _fingerprint("run_personal_routine", "subject", _core_fields(body))

    state = _read_state_zero_write(root)
    replay = _check_replay(
        state, operation_key=operation_key, fingerprint=fingerprint, root=root, body=body
    )
    if replay is not None:
        return replay
    routine = _find_routine(state, routine_id)
    if routine is None:
        raise V08OpenAffordanceError("ROUTINE_NOT_FOUND", f"unknown routine: {routine_id}")

    executed: list[dict[str, Any]] = []
    blocked: Optional[dict[str, Any]] = None
    for pos, step in enumerate(routine.get("steps") or []):
        if not isinstance(step, Mapping):
            blocked = {"step_index": pos, "reason_code": "INVALID_FIELDS", "detail": "step is not an object"}
            break
        step_key = f"{operation_key or 'routine'}:{routine['routine_id']}:{pos}"
        try:
            if str(step.get("kind") or "") == "verb":
                step_payload = {
                    key: value
                    for key, value in step.items()
                    if key not in ("kind",)
                }
                step_payload["action"] = "execute_universal_verb"
                step_payload["operation_key"] = step_key
                result = execute_universal_verb(
                    root, step_payload, actor_side="subject", now_iso=now_text
                )
            else:
                step_payload = {
                    key: value
                    for key, value in step.items()
                    if key not in ("kind",)
                }
                step_payload["action"] = "apply_living_trace"
                step_payload["operation_key"] = step_key
                result = apply_living_trace(
                    root, step_payload, actor_side="subject", now_iso=now_text
                )
        except V08OpenAffordanceError as exc:
            blocked = {
                "step_index": pos,
                "reason_code": exc.reason_code,
                "detail": exc.message,
            }
            break
        executed.append({
            "step_index": pos,
            "kind": str(step.get("kind") or ""),
            "object_id": step.get("object_id"),
            "receipt": copy.deepcopy(result),
        })

    # Re-read after the per-step writes so the usage counter is not lost.
    latest = _read_state_zero_write(root)
    for row in latest.get("routines") or []:
        if isinstance(row, dict) and str(row.get("routine_id") or "") == routine_id:
            if executed:
                row["times_used"] = int(row.get("times_used") or 0) + 1
                row["last_used_at"] = now_text
            break

    receipt: dict[str, Any] = {
        "status": "partial" if blocked is not None else "applied",
        "action": "run_personal_routine",
        "routine_id": routine_id,
        "title_cn": routine.get("title_cn"),
        "executed_steps": executed,
        "executed_count": len(executed),
        "blocked_step": copy.deepcopy(blocked) if blocked is not None else None,
        "times_used": next(
            (
                int(row.get("times_used") or 0)
                for row in latest.get("routines") or []
                if isinstance(row, Mapping) and str(row.get("routine_id") or "") == routine_id
            ),
            0,
        ),
        "at": now_text,
        "llm_calls": 0,
        "already_applied": False,
    }
    _bump(latest, now_text)
    _store_operation(
        latest, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt
    )
    _atomic_write_state(root, latest)
    return receipt


def _retire_personal_routine_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Peacefully retire one routine.  No guilt copy, no penalty, no streak to break."""

    body = _require_mapping(payload)
    _assert_no_identity_keys(body)
    routine_id = _text(body.get("routine_id"), min_len=1, max_len=120, field="routine_id")
    now_dt, now_text = _resolve_now(now_iso)
    operation_key = _optional_text(body.get("operation_key"), max_len=120, field="operation_key")
    fingerprint = _fingerprint("retire_personal_routine", "subject", _core_fields(body))

    state = _read_state_zero_write(root)
    replay = _check_replay(
        state, operation_key=operation_key, fingerprint=fingerprint, root=root, body=body
    )
    if replay is not None:
        return replay
    routine = _find_routine(state, routine_id)
    if routine is None:
        raise V08OpenAffordanceError("ROUTINE_NOT_FOUND", f"unknown routine: {routine_id}")

    state["routines"] = [
        dict(row)
        for row in state.get("routines") or []
        if not (isinstance(row, Mapping) and str(row.get("routine_id") or "") == routine_id)
    ]
    receipt = {
        "status": "applied",
        "action": "retire_personal_routine",
        "retired_routine_id": routine_id,
        "title_cn": routine.get("title_cn"),
        "at": now_text,
        "llm_calls": 0,
        "already_applied": False,
    }
    _bump(state, now_text)
    _store_operation(state, operation_key=operation_key, fingerprint=fingerprint, receipt=receipt)
    _atomic_write_state(root, state)
    return receipt


# ---------------------------------------------------------------------------
# Serialized write entry points
# ---------------------------------------------------------------------------


def execute_universal_verb(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    with state_file_lock(state_path(root)):
        return _execute_universal_verb_locked(
            root, payload, actor_side=actor_side, now_iso=now_iso
        )


def apply_living_trace(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    with state_file_lock(state_path(root)):
        return _apply_living_trace_locked(
            root, payload, actor_side=actor_side, now_iso=now_iso
        )


def clear_living_trace(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    with state_file_lock(state_path(root)):
        return _clear_living_trace_locked(root, payload, now_iso=now_iso)


def clear_partner_trace(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    with state_file_lock(state_path(root)):
        return _clear_partner_trace_locked(root, payload, now_iso=now_iso)


def evaporate_expired_living_traces(root: Path | str, now_iso: Optional[str]) -> int:
    with state_file_lock(state_path(root)):
        return _evaporate_expired_living_traces_locked(root, now_iso)


def save_personal_routine(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    with state_file_lock(state_path(root)):
        return _save_personal_routine_locked(root, payload, now_iso=now_iso)


def run_personal_routine(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    with state_file_lock(state_path(root)):
        return _run_personal_routine_locked(root, payload, now_iso=now_iso)


def retire_personal_routine(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    with state_file_lock(state_path(root)):
        return _retire_personal_routine_locked(root, payload, now_iso=now_iso)


# ---------------------------------------------------------------------------
# Context cues (100% zero-write)
# ---------------------------------------------------------------------------

_ACTIVE_FOCUS_WORDS: frozenset[str] = frozenset({"active", "engaged", "focused", "working", "busy"})


def _workbench_focus_zero_write(root: Path) -> bool:
    for fname in ("workbench_tray.json", "workbench_curio.json"):
        doc = _read_json_zero_write(root / "phone" / fname)
        if not doc:
            continue
        tray = doc.get("tray") if isinstance(doc.get("tray"), Mapping) else doc
        if not isinstance(tray, Mapping):
            continue
        work = tray.get("work")
        if isinstance(work, Mapping) and str(work.get("artifact_id") or "").strip():
            return True
        for key in ("images", "materials"):
            rows = tray.get(key)
            if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)) and rows:
                return True
    return False


def _reading_focus_zero_write(root: Path) -> bool:
    for rel in FOCUS_READING_FILES:
        doc = _read_json_zero_write(root.joinpath(*rel))
        if not doc:
            continue
        for key in ("materials", "items", "books"):
            rows = doc.get(key)
            if isinstance(rows, Mapping) and rows:
                return True
            if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)) and rows:
                return True
        for key in ("scope_ref", "material_id", "book_id"):
            value = doc.get(key)
            if isinstance(value, str) and value.strip():
                return True
    return False


def _agency_focus_zero_write(root: Path) -> bool:
    for rel in FOCUS_AGENCY_FILES:
        doc = _read_json_zero_write(root.joinpath(*rel))
        if not doc:
            continue
        for key in ("focused", "is_focused"):
            if doc.get(key) is True:
                return True
        for key in ("active_focus", "focus", "foreground", "attention", "focus_state"):
            block = doc.get(key)
            if block is True:
                return True
            if isinstance(block, Mapping):
                if block.get("active") is True or block.get("focused") is True:
                    return True
                status = str(block.get("status") or "").strip().lower()
                if status in _ACTIVE_FOCUS_WORDS:
                    return True
    return False


def focused_state_zero_write(root: Path | str) -> bool:
    """True only while a real, unsettled focus signal exists on disk."""

    base = Path(root)
    try:
        return (
            _workbench_focus_zero_write(base)
            or _reading_focus_zero_write(base)
            or _agency_focus_zero_write(base)
        )
    except Exception:  # noqa: BLE001 - an unreadable signal is not a focus claim
        return False


def context_cues_zero_write(
    root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> set[str]:
    """The routine cues that honestly match right now (100% zero-write)."""

    now_dt, _ = _resolve_now(now_iso)
    cues: set[str] = {"any"}
    hour = int(now_dt.hour)
    if 5 <= hour < 11:
        cues.add("morning")
    if 17 <= hour < 24:
        cues.add("evening")
    if focused_state_zero_write(root):
        cues.add("focused")
    doc = _read_json_zero_write(Path(root) / "world" / "v04_neighborhood_state.json")
    if doc:
        env = doc.get("environment") if isinstance(doc.get("environment"), Mapping) else {}
        weather = str(env.get("weather") or doc.get("weather") or "").strip().lower()
        season = str(env.get("season") or "").strip().lower()
        if weather in ("rain", "drizzle", "snow"):
            cues.update(("rainy", "chilly"))
        if season in ("winter", "late_autumn"):
            cues.add("chilly")
    return cues


def match_routines_for_context(
    root: Path | str,
    routines: Optional[Sequence[Mapping[str, Any]]] = None,
    *,
    now_iso: Optional[str] = None,
    cues: Optional[Sequence[str]] = None,
) -> list[dict[str, Any]]:
    """Saved routines whose ``when_cue`` honestly matches right now.

    ``cues`` lets the daily-loop caller pass its already-resolved environmental
    cues; otherwise the real on-disk focus / clock / weather signals are read.
    A ``focused`` routine stays quiet unless an active signal really exists.
    """

    _, now_text = _resolve_now(now_iso)
    if cues is not None:
        available = {str(cue).strip() for cue in cues if str(cue).strip()}
    else:
        available = context_cues_zero_write(root, now_iso=now_text)
    if routines is None:
        rows = _read_state_zero_write(root).get("routines") or []
    else:
        rows = routines
    matched: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("status") or "active") == "retired":
            continue
        if str(row.get("when_cue") or "any").strip() in available:
            matched.append(copy.deepcopy(dict(row)))
    return matched


# ---------------------------------------------------------------------------
# Read view (100% zero-write)
# ---------------------------------------------------------------------------


def _remaining_minutes(expires_at: Any, now_dt: datetime) -> Optional[int]:
    dt = _parse_iso(expires_at)
    if dt is None:
        return None
    seconds = (dt - now_dt).total_seconds()
    if seconds <= 0:
        return 0
    return int((seconds + 59) // 60)


def read_living_traces_view(
    root: Path | str,
    *,
    viewer: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Project the traces, inscriptions, placements and routines without writing."""

    if viewer not in ("subject", "partner"):
        raise V08OpenAffordanceError("FORBIDDEN_ACTOR_SIDE", f"unknown viewer: {viewer!r}")
    now_dt, now_text = _resolve_now(now_iso)
    subject = viewer == "subject"
    state = _read_state_zero_write(root)
    objects = discover_reachable_objects(root, now_iso=now_text)

    active: list[dict[str, Any]] = []
    for row in state.get("traces") or []:
        if not isinstance(row, Mapping) or not _is_trace_active(row, now_dt):
            continue
        if not subject and str(row.get("visibility") or "shared") != "shared":
            continue
        out = copy.deepcopy(dict(row))
        out["remaining_minutes"] = _remaining_minutes(out.get("expires_at"), now_dt)
        if not subject:
            out.pop("hidden_count", None)
        active.append(out)
    active.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("trace_id") or "")))
    active = active[-MAX_ACTIVE_TRACES:]

    inscriptions: dict[str, list[dict[str, Any]]] = {}
    raw_inscriptions = state.get("inscriptions_by_object")
    if isinstance(raw_inscriptions, Mapping):
        for object_id, rows in raw_inscriptions.items():
            if not isinstance(rows, list):
                continue
            kept = [
                copy.deepcopy(dict(row))
                for row in rows
                if isinstance(row, Mapping)
                and (subject or str(row.get("visibility") or "shared") == "shared")
            ]
            if kept:
                inscriptions[str(object_id)] = kept

    routines: list[dict[str, Any]] = []
    for row in state.get("routines") or []:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("status") or "active") == "retired":
            continue
        if not subject and str(row.get("visibility") or "shared") != "shared":
            continue
        routines.append(copy.deepcopy(dict(row)))

    verbs = [
        {
            "verb": verb,
            "label_cn": spec["label_cn"],
            "detail_cn": spec["detail_cn"],
            "requires_any": list(spec["requires_any"]),
        }
        for verb, spec in UNIVERSAL_VERBS.items()
    ]

    view: dict[str, Any] = {
        "schema": VIEW_SCHEMA,
        "perspective": viewer,
        "read_only": True,
        "observed_at": now_text,
        "state_exists": state_path(root).is_file(),
        "revision": int(state.get("revision") or 0),
        "universal_verbs": verbs,
        "reachable_objects": objects,
        "active_traces": active,
        "inscriptions_by_object": inscriptions,
        "object_placements": copy.deepcopy(dict(state.get("object_placements") or {})),
        "object_settings": copy.deepcopy(dict(state.get("object_settings") or {})),
        "object_relations": copy.deepcopy(dict(state.get("object_relations") or {})),
        "combinations": copy.deepcopy(list(state.get("combinations") or [])),
        "routines": routines,
        "llm_calls": 0,
    }
    if not subject:
        # The partner projection never carries private counters or private rows.
        for row in view["reachable_objects"]:
            row.pop("hidden_count", None)
        view["routines"] = [
            row for row in routines if str(row.get("visibility") or "shared") == "shared"
        ]
    return view


__all__ = [
    "DEFAULT_TTL_MINUTES",
    "FADE_MODES",
    "MAX_ACTIVE_TRACES",
    "MAX_GESTURE_CHARS",
    "MAX_INSCRIPTION_CHARS",
    "MAX_PARTNER_TRACES",
    "MAX_ROUTINES",
    "MAX_STICKER_CHARS",
    "SCHEMA_VERSION",
    "STATE_FILENAME",
    "UNIVERSAL_VERBS",
    "V08OpenAffordanceError",
    "VIEW_SCHEMA",
    "apply_living_trace",
    "clear_living_trace",
    "clear_partner_trace",
    "context_cues_zero_write",
    "current_location_id",
    "discover_reachable_objects",
    "evaporate_expired_living_traces",
    "execute_universal_verb",
    "focused_state_zero_write",
    "leave_partner_trace",
    "match_routines_for_context",
    "read_living_traces_view",
    "retire_personal_routine",
    "run_personal_routine",
    "save_personal_routine",
    "state_path",
]

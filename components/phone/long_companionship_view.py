#!/usr/bin/env python3
"""长程陪伴六项日常切片：第一天房间对比 / 盆栽插画连结 / 可修订习惯册 / 草稿到成品礼物 / 回忆双层灯光 / 一键续接总览。

它是什么
--------
一个**纯投影 + 轻量显式状态**模块，把 `15-产品记忆与自主机制完整设计.md §2.8`（长程陪伴最后 6 项）
与 `16-生活生态与长程研究完整设计.md §10 G`（回忆空间「双层灯光」）落成两份 `0600` 原子 JSON 状态文件
（位于 `<root>/phone/`）与三条薄投影读面：

1. **第一天房间照与今日房间新旧对比**（`longterm_room_habits.json` `first_room`）：
   只允许一次性封存首日房间基线（`captured=True` 后任何再次 `capture_first_room` 一律返回
   `already_applied`，绝不覆盖第一天记忆）；读面实时与今日房间事实做差值对比（家具、物品、盆栽、
   书页、已送达分享），`no_absence_guilt=True`。`partner_default` 视角剥离底层 `album_artifact_id`
   与私密 `note`，仅暴露 `photo_status`（`available` / `withheld` / `none`）。
2. **薄荷/室内盆栽观察连到手账插画**（同上 `plant_sketch_links`，最多 8 条）：
   把家中真实盆栽 `plant_id` 连结到活跃 `project` 或 `PhoneAlbum` 图片（`target_kind` 为
   `"project"` 或 `"album"`）。`partner_default` 只返回 `shared=True` 的连结摘要，且当目标是
   `album` 或尚未本地投递的 `project` 时一律剥离底层 `target_artifact_id`。
3. **可修订共同习惯册**（同上 `shared_habits`，最多 12 条活跃 / 24 条总存）：
   双人分署名（`author_side` 为 `"her"` 或 `"partner"`），跨作者改写或停用一律 `409 AUTHOR_MISMATCH`。
   每次修订递增 `revision_count` 并保留最近 5 条修订轨迹（`version`、`previous_text`、`revised_reason`、
   `revised_at`）；停用转为 `RETIRED` 而不抹除历史，`no_hard_constraint=True`、`does_not_hijack_decision=True`。
4. **保留草稿到成品的共同礼物**（`longterm_gifts_lantern.json` `shared_gifts`，最多 12 件）：
   支持 `"draft"` -> `"wrapped"` -> `"gifted"` 单向演进（送出后不可回退，重送或倒退均 `409 GIFT_ALREADY_SENT`；
   跨作者 `409 AUTHOR_MISMATCH`）。**严格隐私边界**：`partner_default` 中任何 `author_side == "her"` 且
   `stage != "gifted"` 的礼物完全隐去，且绝不暴露她的隐藏草稿计数；一旦 `send_gift` 变为 `"gifted"`，
   同时公开 `draft_seed_note`（最初粗糙灵感）与 `finished_note` + `gifted_at`，保留从草稿到成品的心意轨迹。
5. **一年后重画/重写新旧谱系 & 回忆空间「双层灯光」**（同上 `memory_lantern`）：
   联合扫描 `PrivateProjectService` 真实 `derived` 作品来源谱系与最多 12 条 `later_reflections`。
   - `layer="then_only"`（默认）：`later_context_included=False`，每条谱系只照亮当年的 `then_snapshot`，
     `later_remakes=[]`、`later_reflection=None`，过去版本绝不“预知未来”。
   - `layer="with_later_context"`：`later_context_included=True`，照亮后续重画/重写 `later_remakes`
     与后来回望 `later_reflection`（`partner_default` 仅公开 `shared=True` 的回望）。
   - 披露安全：`partner_default` 对 `then_snapshot` 与每一条 `later_remake` 分别独立核对 `ARCHIVED`
     生命周期（`ARCHIVED_SOURCE`）及精确 `(artifact_id, version_id)` 的 `LOCAL_DELIVERED` 投递事实
     （`VERSION_NOT_DELIVERED`），未投递版本绝不泄露标题、正文或私密 ID。
6. **一键回到上次项目/书页/话题 + 已关闭归档项目无催促总览 + 备份恢复状态只读总览**（`resume-hub`）：
   纯零写聚合（无状态文件），汇总 `project_cue`、`reading_cue`、`topic_cue`、`closed_projects`
   （`archived_count`、`closed_cleanly=True`、`no_nagging=True`；`partner_default` 绝不暴露归档标题/ID）
   与 `recovery_overview`（现读 `runtime_recovery_review`，`read_only=True`）。

它不是什么
----------
* 纯 GET 绝不创建 `<root>/phone/` 或任何状态文件，绝不推进/修改任何底层 owner。
* 状态文件或父目录若是符号链接、损坏 JSON 或非法结构，读面具名失败关闭，写面拒绝覆盖。
* 零产品模型调用、零催促内疚话术、零私密草稿或未投递版本旁路泄露。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping, Optional

try:  # package import (tests / phone service import ``components.phone``)
    from ..life.scripts import state_tx_guard as _tx  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - flat import fallback (components/phone on sys.path)
    import sys as _sys

    _LIFE_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "life" / "scripts"
    if str(_LIFE_SCRIPTS_DIR) not in _sys.path:
        _sys.path.insert(0, str(_LIFE_SCRIPTS_DIR))
    import state_tx_guard as _tx  # type: ignore[no-redef]

FIRST_ROOM_HABITS_SCHEMA = "phone.longterm.first_room_habits.v1"
GIFTS_LANTERN_SCHEMA = "phone.longterm.gifts_lantern.v1"
RESUME_HUB_SCHEMA = "phone.longterm.resume_hub.v1"

STATE_DIRNAME = "phone"
ROOM_HABITS_STATE_FILENAME = "longterm_room_habits.json"
GIFTS_LANTERN_STATE_FILENAME = "longterm_gifts_lantern.json"
STATE_SCHEMA_VERSION = 1

MAX_PLANT_SKETCH_LINKS = 8
MAX_ACTIVE_HABITS = 12
MAX_STORED_HABITS = 24
MAX_HABIT_REVISIONS = 5
MAX_SHARED_GIFTS = 12
MAX_LATER_REFLECTIONS = 12
MAX_ARCHIVED_PREVIEW = 12
MAX_ROOM_NAMES = 64

MAX_TITLE = 60
MAX_HABIT_TEXT = 200
MAX_NOTE = 160
MAX_GIFT_NOTE = 200
MAX_REFLECTION_NOTE = 240
MAX_EXCERPT = 400
MAX_ID_LENGTH = 128

AUTHOR_SIDES = ("her", "partner")
HABIT_STATUSES = ("ACTIVE", "RETIRED")
SKETCH_TARGET_KINDS = ("project", "album")
GIFT_UPSERT_STAGES = ("draft", "wrapped")
GIFT_STAGES = ("draft", "wrapped", "gifted")
LANTERN_LAYERS = ("then_only", "with_later_context")

VERSION_NOT_DELIVERED = "VERSION_NOT_DELIVERED"
VERSION_NOT_IN_ARTIFACT = "VERSION_NOT_IN_ARTIFACT"
ARCHIVED_SOURCE = "ARCHIVED_SOURCE"
SOURCE_NOT_READABLE = "SOURCE_NOT_READABLE"
VERSION_UNREADABLE = "VERSION_UNREADABLE"


class LongtermStateError(ValueError):
    """Explicit pure-layer refusal with a stable reason code."""

    def __init__(self, reason_code: str, *, conflict: bool = False) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)
        self.conflict = bool(conflict)


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_body(value: Any, limit: int) -> str:
    return str(value or "")[:limit].strip()


def _iso_text(value: Any, reason: str = "TIMESTAMP_INVALID") -> str:
    if not isinstance(value, str) or not value.strip():
        raise LongtermStateError(reason)
    text = value.strip()
    if len(text) > 48 or any(ch in text for ch in ("\x00", "\r", "\n")):
        raise LongtermStateError(reason)
    return text


def _optional_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:48]


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _nonneg_int(value: Any, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LongtermStateError(reason)
    return int(value)


def _text_field(value: Any, limit: int, reason: str) -> str:
    if not isinstance(value, str):
        raise LongtermStateError(reason)
    if any(ch in value for ch in ("\x00", "\r")):
        raise LongtermStateError(reason)
    raw_stripped = value.strip()
    if not raw_stripped or len(raw_stripped) > limit:
        raise LongtermStateError(reason)
    cleaned = _one_line(raw_stripped, limit)
    if not cleaned:
        raise LongtermStateError(reason)
    return cleaned


def _optional_text_field(value: Any, limit: int, reason: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LongtermStateError(reason)
    if any(ch in value for ch in ("\x00", "\r")):
        raise LongtermStateError(reason)
    raw_stripped = value.strip()
    if not raw_stripped:
        return None
    if len(raw_stripped) > limit:
        raise LongtermStateError(reason)
    return _one_line(raw_stripped, limit) or None


def _local_id(value: Any, reason: str) -> str:
    if not isinstance(value, str):
        raise LongtermStateError(reason)
    text = value.strip()
    if not text or len(text) > 96 or not re.match(r"^[A-Za-z0-9._:\-]+$", text):
        raise LongtermStateError(reason)
    return text


def _artifact_ref(value: Any, reason: str) -> str:
    if not isinstance(value, str):
        raise LongtermStateError(reason)
    text = value.strip()
    if not text or len(text) > MAX_ID_LENGTH:
        raise LongtermStateError(reason)
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "\\", "?", "#", " ")):
        raise LongtermStateError(reason)
    return text


def _optional_artifact_ref(value: Any, reason: str) -> Optional[str]:
    if value is None:
        return None
    return _artifact_ref(value, reason)


def _strict_bool(value: Any, reason: str) -> bool:
    if not isinstance(value, bool):
        raise LongtermStateError(reason)
    return bool(value)


def _author_side(value: Any, reason: str = "AUTHOR_SIDE_INVALID") -> str:
    if not isinstance(value, str) or value.strip() not in AUTHOR_SIDES:
        raise LongtermStateError(reason)
    return value.strip()


def _stable_id(prefix: str, *parts: str) -> str:
    joined = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _clean_name_list(raw: Any, reason: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise LongtermStateError(reason)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise LongtermStateError(reason)
        cleaned = _one_line(item, 80)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
            if len(out) >= MAX_ROOM_NAMES:
                break
    return out


# ---------------------------------------------------------------------------------------------
# atomic 0600 JSON state files: GET never creates anything; corrupt/symlink fails closed
# ---------------------------------------------------------------------------------------------


def room_habits_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / ROOM_HABITS_STATE_FILENAME


def gifts_lantern_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / GIFTS_LANTERN_STATE_FILENAME


def _read_json(path: Path) -> tuple[Any, Optional[str]]:
    if path.parent.is_symlink() or path.is_symlink():
        return None, "STATE_SYMLINK"
    try:
        if not path.exists():
            return None, None
        if not path.is_file():
            return None, "STATE_NOT_FILE"
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError):
        return None, "STATE_CORRUPT"


def _write_json(path: Path, payload: Mapping[str, Any], *, prefix: str) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise LongtermStateError(f"{prefix}_STATE_SYMLINK", conflict=True)
    if path.exists():
        if not path.is_file():
            raise LongtermStateError(f"{prefix}_STATE_NOT_FILE", conflict=True)
        _, existing_reason = _read_json(path)
        if existing_reason is not None:
            raise LongtermStateError(f"{prefix}_{existing_reason}", conflict=True)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent),
                                             text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------
# File 1: longterm_room_habits.json (first_room + plant_sketch_links + shared_habits)
# ---------------------------------------------------------------------------------------------


def empty_room_habits_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "first_room": None,
        "plant_sketch_links": [],
        "shared_habits": [],
    }


def _validate_first_room_record(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    if raw.get("captured") is not True:
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    captured_at = _iso_text(raw.get("captured_at"), "ROOM_HABITS_STATE_INVALID")
    loc_raw = raw.get("location")
    if loc_raw is not None and (not isinstance(loc_raw, str) or len(loc_raw.strip()) > 96):
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    location = loc_raw.strip() if isinstance(loc_raw, str) and loc_raw.strip() else None
    furniture_count = _nonneg_int(raw.get("furniture_count"), "ROOM_HABITS_STATE_INVALID")
    item_count = _nonneg_int(raw.get("item_count"), "ROOM_HABITS_STATE_INVALID")
    item_names = _clean_name_list(raw.get("item_names"), "ROOM_HABITS_STATE_INVALID")
    plant_names = _clean_name_list(raw.get("plant_names"), "ROOM_HABITS_STATE_INVALID")
    reading_raw = raw.get("reading_page")
    reading_page: Optional[int] = None
    if reading_raw is not None:
        reading_page = _nonneg_int(reading_raw, "ROOM_HABITS_STATE_INVALID")
    delivered_count = _nonneg_int(raw.get("delivered_share_count"), "ROOM_HABITS_STATE_INVALID")
    album_id = _optional_artifact_ref(raw.get("album_artifact_id"), "ROOM_HABITS_STATE_INVALID")
    note = _optional_text_field(raw.get("note"), MAX_NOTE, "ROOM_HABITS_STATE_INVALID")
    return {
        "captured": True,
        "captured_at": captured_at,
        "location": location,
        "furniture_count": furniture_count,
        "item_count": item_count,
        "item_names": item_names,
        "plant_names": plant_names,
        "reading_page": reading_page,
        "delivered_share_count": delivered_count,
        "album_artifact_id": album_id,
        "note": note,
    }


def _validate_plant_sketch_link(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    link_id = _local_id(raw.get("link_id"), "ROOM_HABITS_STATE_INVALID")
    plant_id = _local_id(raw.get("plant_id"), "ROOM_HABITS_STATE_INVALID")
    plant_name = _optional_text_field(raw.get("plant_name"), MAX_TITLE, "ROOM_HABITS_STATE_INVALID")
    target_kind = raw.get("target_kind")
    if not isinstance(target_kind, str) or target_kind not in SKETCH_TARGET_KINDS:
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    target_artifact_id = _artifact_ref(raw.get("target_artifact_id"), "ROOM_HABITS_STATE_INVALID")
    observation_note = _text_field(raw.get("observation_note"), MAX_NOTE, "ROOM_HABITS_STATE_INVALID")
    shared = _strict_bool(raw.get("shared"), "ROOM_HABITS_STATE_INVALID")
    created_at = _iso_text(raw.get("created_at"), "ROOM_HABITS_STATE_INVALID")
    updated_at = _iso_text(raw.get("updated_at"), "ROOM_HABITS_STATE_INVALID")
    return {
        "link_id": link_id,
        "plant_id": plant_id,
        "plant_name": plant_name,
        "target_kind": target_kind,
        "target_artifact_id": target_artifact_id,
        "observation_note": observation_note,
        "shared": shared,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _validate_habit_revision(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    version = _nonneg_int(raw.get("version"), "ROOM_HABITS_STATE_INVALID")
    if version < 1:
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    previous_text = _text_field(raw.get("previous_text"), MAX_HABIT_TEXT, "ROOM_HABITS_STATE_INVALID")
    previous_title = _optional_text_field(raw.get("previous_title"), MAX_TITLE, "ROOM_HABITS_STATE_INVALID")
    revised_reason = _optional_text_field(raw.get("revised_reason"), MAX_NOTE, "ROOM_HABITS_STATE_INVALID")
    revised_at = _iso_text(raw.get("revised_at"), "ROOM_HABITS_STATE_INVALID")
    out: dict[str, Any] = {
        "version": version,
        "previous_text": previous_text,
        "revised_reason": revised_reason,
        "revised_at": revised_at,
    }
    if previous_title is not None:
        out["previous_title"] = previous_title
    return out


def _validate_shared_habit(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    habit_id = _local_id(raw.get("habit_id"), "ROOM_HABITS_STATE_INVALID")
    author_side = _author_side(raw.get("author_side"), "ROOM_HABITS_STATE_INVALID")
    title = _text_field(raw.get("title"), MAX_TITLE, "ROOM_HABITS_STATE_INVALID")
    habit_text = _text_field(raw.get("habit_text"), MAX_HABIT_TEXT, "ROOM_HABITS_STATE_INVALID")
    status = raw.get("status")
    if not isinstance(status, str) or status not in HABIT_STATUSES:
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    revision_count = _nonneg_int(raw.get("revision_count"), "ROOM_HABITS_STATE_INVALID")
    revisions_raw = raw.get("revisions")
    if not isinstance(revisions_raw, list) or len(revisions_raw) > MAX_HABIT_REVISIONS:
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    revisions = [_validate_habit_revision(item) for item in revisions_raw]
    retired_reason = _optional_text_field(raw.get("retired_reason"), MAX_NOTE, "ROOM_HABITS_STATE_INVALID")
    retired_at = _optional_iso(raw.get("retired_at"))
    created_at = _iso_text(raw.get("created_at"), "ROOM_HABITS_STATE_INVALID")
    updated_at = _iso_text(raw.get("updated_at"), "ROOM_HABITS_STATE_INVALID")
    return {
        "habit_id": habit_id,
        "author_side": author_side,
        "title": title,
        "habit_text": habit_text,
        "status": status,
        "revision_count": revision_count,
        "revisions": revisions,
        "retired_reason": retired_reason,
        "retired_at": retired_at,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _validate_room_habits_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    revision = _nonneg_int(raw.get("revision"), "ROOM_HABITS_STATE_INVALID")
    updated_at = _optional_iso(raw.get("updated_at"))
    first_room = _validate_first_room_record(raw.get("first_room"))

    links_raw = raw.get("plant_sketch_links")
    if not isinstance(links_raw, list) or len(links_raw) > MAX_PLANT_SKETCH_LINKS:
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    links = [_validate_plant_sketch_link(item) for item in links_raw]
    if len({item["link_id"] for item in links}) != len(links):
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")

    habits_raw = raw.get("shared_habits")
    if not isinstance(habits_raw, list) or len(habits_raw) > MAX_STORED_HABITS:
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    habits = [_validate_shared_habit(item) for item in habits_raw]
    if len({item["habit_id"] for item in habits}) != len(habits):
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")
    active_count = sum(1 for item in habits if item["status"] == "ACTIVE")
    if active_count > MAX_ACTIVE_HABITS:
        raise LongtermStateError("ROOM_HABITS_STATE_INVALID")

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": revision,
        "updated_at": updated_at,
        "first_room": first_room,
        "plant_sketch_links": links,
        "shared_habits": habits,
    }


def read_room_habits_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(room_habits_state_path(root))
    if reason is not None:
        return empty_room_habits_state(), "ROOM_HABITS_" + reason
    if raw is None:
        return empty_room_habits_state(), None
    try:
        return _validate_room_habits_state(raw), None
    except LongtermStateError:
        return empty_room_habits_state(), "ROOM_HABITS_STATE_INVALID"


def write_room_habits_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    path = room_habits_state_path(root)
    with _tx.state_file_lock(path):
        if path.exists() and not path.is_symlink() and path.is_file():
            existing_raw, existing_reason = _read_json(path)
            if existing_reason is not None:
                raise LongtermStateError("ROOM_HABITS_" + existing_reason, conflict=True)
            if existing_raw is not None:
                _validate_room_habits_state(existing_raw)
        normalized = _validate_room_habits_state(dict(state))
        _write_json(path, normalized, prefix="ROOM_HABITS")
        return normalized


def _bump_room_habits(state: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    out = _validate_room_habits_state(dict(state))
    out["revision"] = int(out["revision"]) + 1
    out["updated_at"] = _iso_text(now_iso)
    return out


def apply_capture_first_room(
    state: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    album_artifact_id: Optional[str] = None,
    note: Optional[str] = None,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Seal Day-1 room baseline once-only; subsequent calls return `(current, existing, False)`."""

    current = _validate_room_habits_state(dict(state))
    clean_now = _iso_text(now_iso)
    clean_album = _optional_artifact_ref(album_artifact_id, "FIRST_ROOM_ALBUM_ID_INVALID")
    clean_note = _optional_text_field(note, MAX_NOTE, "FIRST_ROOM_NOTE_INVALID")

    existing = current.get("first_room")
    if isinstance(existing, Mapping) and existing.get("captured") is True:
        return current, dict(existing), False

    if not isinstance(snapshot, Mapping):
        raise LongtermStateError("FIRST_ROOM_SNAPSHOT_INVALID")

    loc_raw = snapshot.get("location")
    location = loc_raw.strip()[:96] if isinstance(loc_raw, str) and loc_raw.strip() else None
    furniture_count = _nonneg_int(snapshot.get("furniture_count", 0), "FIRST_ROOM_SNAPSHOT_INVALID")
    item_count = _nonneg_int(snapshot.get("item_count", 0), "FIRST_ROOM_SNAPSHOT_INVALID")
    item_names = _clean_name_list(snapshot.get("item_names"), "FIRST_ROOM_SNAPSHOT_INVALID")
    plant_names = _clean_name_list(snapshot.get("plant_names"), "FIRST_ROOM_SNAPSHOT_INVALID")
    reading_raw = snapshot.get("reading_page")
    reading_page = (
        None if reading_raw is None
        else _nonneg_int(reading_raw, "FIRST_ROOM_SNAPSHOT_INVALID")
    )
    delivered_count = _nonneg_int(
        snapshot.get("delivered_share_count", 0), "FIRST_ROOM_SNAPSHOT_INVALID")

    record = {
        "captured": True,
        "captured_at": clean_now,
        "location": location,
        "furniture_count": furniture_count,
        "item_count": item_count,
        "item_names": item_names,
        "plant_names": plant_names,
        "reading_page": reading_page,
        "delivered_share_count": delivered_count,
        "album_artifact_id": clean_album,
        "note": clean_note,
    }
    current["first_room"] = record
    return _bump_room_habits(current, clean_now), dict(record), True


def apply_link_plant_sketch(
    state: Mapping[str, Any],
    *,
    plant_id: str,
    target_kind: str,
    target_artifact_id: str,
    observation_note: str,
    shared: bool = False,
    plant_name: Optional[str] = None,
    valid_plant_ids: Optional[Iterable[str]] = None,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Link an indoor potted plant observation to an active sketchbook project or album photo."""

    current = _validate_room_habits_state(dict(state))
    clean_now = _iso_text(now_iso)
    clean_plant_id = _local_id(plant_id, "PLANT_ID_INVALID")
    clean_plant_name = _optional_text_field(plant_name, MAX_TITLE, "PLANT_NAME_INVALID")
    if not isinstance(target_kind, str) or target_kind.strip() not in SKETCH_TARGET_KINDS:
        raise LongtermStateError("SKETCH_TARGET_KIND_INVALID")
    clean_kind = target_kind.strip()
    clean_target_id = _artifact_ref(target_artifact_id, "SKETCH_TARGET_ID_INVALID")
    clean_note = _text_field(observation_note, MAX_NOTE, "SKETCH_OBSERVATION_INVALID")
    clean_shared = _strict_bool(shared, "SKETCH_SHARED_INVALID")

    if valid_plant_ids is not None and clean_plant_id not in set(valid_plant_ids):
        raise LongtermStateError("PLANT_NOT_IN_HOME", conflict=True)

    existing = next(
        (
            row for row in current["plant_sketch_links"]
            if row["plant_id"] == clean_plant_id
            and row["target_kind"] == clean_kind
            and row["target_artifact_id"] == clean_target_id
        ),
        None,
    )
    if existing is not None:
        next_name = clean_plant_name if clean_plant_name is not None else existing["plant_name"]
        if (existing["observation_note"] == clean_note
                and existing["shared"] == clean_shared
                and existing["plant_name"] == next_name):
            return current, dict(existing), False
        existing["observation_note"] = clean_note
        existing["shared"] = clean_shared
        existing["plant_name"] = next_name
        existing["updated_at"] = clean_now
        return _bump_room_habits(current, clean_now), dict(existing), True

    if len(current["plant_sketch_links"]) >= MAX_PLANT_SKETCH_LINKS:
        raise LongtermStateError("PLANT_SKETCH_LINKS_FULL", conflict=True)

    link_id = _stable_id("psketch", clean_plant_id, clean_kind, clean_target_id)
    if any(row["link_id"] == link_id for row in current["plant_sketch_links"]):
        link_id = _stable_id("psketch", clean_plant_id, clean_kind, clean_target_id, clean_now)

    entry = {
        "link_id": link_id,
        "plant_id": clean_plant_id,
        "plant_name": clean_plant_name,
        "target_kind": clean_kind,
        "target_artifact_id": clean_target_id,
        "observation_note": clean_note,
        "shared": clean_shared,
        "created_at": clean_now,
        "updated_at": clean_now,
    }
    current["plant_sketch_links"].append(entry)
    return _bump_room_habits(current, clean_now), dict(entry), True


def apply_remove_plant_sketch(
    state: Mapping[str, Any],
    *,
    link_id: str,
    now_iso: str,
) -> tuple[dict[str, Any], bool]:
    current = _validate_room_habits_state(dict(state))
    clean_now = _iso_text(now_iso)
    clean_id = _local_id(link_id, "SKETCH_LINK_ID_INVALID")
    kept = [row for row in current["plant_sketch_links"] if row["link_id"] != clean_id]
    if len(kept) == len(current["plant_sketch_links"]):
        return current, False
    current["plant_sketch_links"] = kept
    return _bump_room_habits(current, clean_now), True


def apply_upsert_habit(
    state: Mapping[str, Any],
    *,
    habit_id: Optional[str] = None,
    title: str,
    habit_text: str,
    revised_reason: Optional[str] = None,
    author_side: str,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Create or revise a co-authored shared habit with bounded revision history (last 5)."""

    current = _validate_room_habits_state(dict(state))
    clean_now = _iso_text(now_iso)
    clean_author = _author_side(author_side)
    clean_title = _text_field(title, MAX_TITLE, "HABIT_TITLE_INVALID")
    clean_text = _text_field(habit_text, MAX_HABIT_TEXT, "HABIT_TEXT_INVALID")
    clean_reason = _optional_text_field(revised_reason, MAX_NOTE, "HABIT_REASON_INVALID")

    if habit_id is not None:
        clean_id = _local_id(habit_id, "HABIT_ID_INVALID")
        found = next((h for h in current["shared_habits"] if h["habit_id"] == clean_id), None)
        if found is None:
            raise LongtermStateError("HABIT_NOT_FOUND", conflict=True)
        if found["author_side"] != clean_author:
            raise LongtermStateError("AUTHOR_MISMATCH", conflict=True)
        if found["status"] == "RETIRED":
            raise LongtermStateError("HABIT_ALREADY_RETIRED", conflict=True)
        if found["title"] == clean_title and found["habit_text"] == clean_text:
            return current, dict(found), False
        next_version = int(found["revision_count"]) + 1
        rev_entry: dict[str, Any] = {
            "version": next_version,
            "previous_title": found["title"],
            "previous_text": found["habit_text"],
            "revised_reason": clean_reason,
            "revised_at": clean_now,
        }
        revisions = list(found["revisions"]) + [rev_entry]
        found["revisions"] = revisions[-MAX_HABIT_REVISIONS:]
        found["revision_count"] = next_version
        found["title"] = clean_title
        found["habit_text"] = clean_text
        found["updated_at"] = clean_now
        return _bump_room_habits(current, clean_now), dict(found), True

    active_count = sum(1 for h in current["shared_habits"] if h["status"] == "ACTIVE")
    if active_count >= MAX_ACTIVE_HABITS:
        raise LongtermStateError("ACTIVE_HABITS_FULL", conflict=True)
    if len(current["shared_habits"]) >= MAX_STORED_HABITS:
        raise LongtermStateError("STORED_HABITS_FULL", conflict=True)

    seq = len(current["shared_habits"]) + int(current["revision"]) + 1
    new_id = _stable_id("habit", clean_author, clean_title, clean_now, str(seq))
    while any(h["habit_id"] == new_id for h in current["shared_habits"]):
        seq += 1
        new_id = _stable_id("habit", clean_author, clean_title, clean_now, str(seq))

    entry = {
        "habit_id": new_id,
        "author_side": clean_author,
        "title": clean_title,
        "habit_text": clean_text,
        "status": "ACTIVE",
        "revision_count": 0,
        "revisions": [],
        "retired_reason": None,
        "retired_at": None,
        "created_at": clean_now,
        "updated_at": clean_now,
    }
    current["shared_habits"].append(entry)
    return _bump_room_habits(current, clean_now), dict(entry), True


def apply_retire_habit(
    state: Mapping[str, Any],
    *,
    habit_id: str,
    retired_reason: Optional[str] = None,
    author_side: str,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    current = _validate_room_habits_state(dict(state))
    clean_now = _iso_text(now_iso)
    clean_id = _local_id(habit_id, "HABIT_ID_INVALID")
    clean_author = _author_side(author_side)
    clean_reason = _optional_text_field(retired_reason, MAX_NOTE, "HABIT_REASON_INVALID")

    found = next((h for h in current["shared_habits"] if h["habit_id"] == clean_id), None)
    if found is None:
        raise LongtermStateError("HABIT_NOT_FOUND", conflict=True)
    if found["author_side"] != clean_author:
        raise LongtermStateError("AUTHOR_MISMATCH", conflict=True)
    if found["status"] == "RETIRED":
        return current, dict(found), False

    found["status"] = "RETIRED"
    found["retired_reason"] = clean_reason
    found["retired_at"] = clean_now
    found["updated_at"] = clean_now
    return _bump_room_habits(current, clean_now), dict(found), True


def first_room_habits_block(
    *,
    state: Mapping[str, Any],
    state_reason: Optional[str],
    live_room: Mapping[str, Any],
    home_plants_by_id: Mapping[str, Mapping[str, Any]],
    album_readability: Mapping[str, bool],
    project_targets: Mapping[str, Mapping[str, Any]],
    subject_view: bool,
) -> dict[str, Any]:
    """Project `first_room`, `plant_sketch_links`, and `shared_habits` for `subject` or `partner_default`."""

    current = _validate_room_habits_state(dict(state))
    private_included = False

    # 1) First room baseline & live delta comparison
    sealed = current.get("first_room")
    live_furniture = _int_or_none(live_room.get("furniture_count"))
    live_items = _int_or_none(live_room.get("item_count"))
    live_item_names = [
        str(x) for x in (live_room.get("item_names") or ()) if isinstance(x, str) and x
    ]
    live_plant_names = [
        str(x) for x in (live_room.get("plant_names") or ()) if isinstance(x, str) and x
    ]
    live_reading_page = _int_or_none(live_room.get("reading_page"))
    live_delivered_count = _int_or_none(live_room.get("delivered_share_count"))

    if not isinstance(sealed, Mapping) or sealed.get("captured") is not True:
        first_room_out: dict[str, Any] = {
            "captured": False,
            "captured_at": None,
            "photo_status": "none",
            "baseline": None,
            "comparison": None,
            "furniture_delta": None,
            "item_delta": None,
            "new_item_names": [],
            "new_plant_names": [],
            "reading_page_delta": None,
            "delivered_share_delta": None,
            "no_absence_guilt": True,
        }
        if subject_view:
            first_room_out["album_artifact_id"] = None
            first_room_out["note"] = None
    else:
        album_id = sealed.get("album_artifact_id")
        if album_id is None:
            photo_status = "none"
        else:
            photo_status = "available" if bool(album_readability.get(album_id)) else "withheld"

        base_items_set = set(sealed["item_names"])
        base_plants_set = set(sealed["plant_names"])
        furniture_delta = (
            live_furniture - sealed["furniture_count"]
            if live_furniture is not None else None
        )
        item_delta = (
            live_items - sealed["item_count"]
            if live_items is not None else None
        )
        new_item_names = [name for name in live_item_names if name not in base_items_set]
        new_plant_names = [name for name in live_plant_names if name not in base_plants_set]
        reading_page_delta = (
            live_reading_page - sealed["reading_page"]
            if live_reading_page is not None and sealed["reading_page"] is not None
            else None
        )
        delivered_share_delta = (
            live_delivered_count - sealed["delivered_share_count"]
            if live_delivered_count is not None else None
        )
        baseline_out: dict[str, Any] = {
            "captured_at": sealed["captured_at"],
            "location": sealed["location"],
            "furniture_count": sealed["furniture_count"],
            "item_count": sealed["item_count"],
            "item_names": list(sealed["item_names"]),
            "plant_names": list(sealed["plant_names"]),
            "reading_page": sealed["reading_page"],
            "delivered_share_count": sealed["delivered_share_count"],
        }
        comparison_out = {
            "furniture_delta": furniture_delta,
            "item_delta": item_delta,
            "new_item_names": new_item_names,
            "new_plant_names": new_plant_names,
            "reading_page_delta": reading_page_delta,
            "delivered_share_delta": delivered_share_delta,
        }
        first_room_out = {
            "captured": True,
            "captured_at": sealed["captured_at"],
            "photo_status": photo_status,
            "baseline": baseline_out,
            "comparison": comparison_out,
            "furniture_delta": furniture_delta,
            "item_delta": item_delta,
            "new_item_names": new_item_names,
            "new_plant_names": new_plant_names,
            "reading_page_delta": reading_page_delta,
            "delivered_share_delta": delivered_share_delta,
            "immutable_baseline": True,
            "no_absence_guilt": True,
        }
        if subject_view:
            if album_id is not None or sealed.get("note") is not None:
                private_included = True
            first_room_out["album_artifact_id"] = album_id
            first_room_out["note"] = sealed["note"]

    # 2) Potted plant -> sketchbook / album links
    links_out: list[dict[str, Any]] = []
    for link in current["plant_sketch_links"]:
        pid = link["plant_id"]
        plant_info = home_plants_by_id.get(pid)
        in_home = isinstance(plant_info, Mapping)
        resolved_plant_name = (
            str(plant_info.get("name") or "") if in_home else ""
        ) or link["plant_name"] or pid

        tkind = link["target_kind"]
        tid = link["target_artifact_id"]
        if tkind == "album":
            target_readable = bool(album_readability.get(tid))
            target_delivered = False
        else:
            pinfo = project_targets.get(tid)
            target_readable = bool(isinstance(pinfo, Mapping) and pinfo.get("active"))
            target_delivered = bool(isinstance(pinfo, Mapping) and pinfo.get("delivered"))

        if subject_view:
            if not link["shared"] or tkind == "album" or not target_delivered:
                private_included = True
            links_out.append({
                "link_id": link["link_id"],
                "plant_id": pid,
                "plant_name": resolved_plant_name,
                "in_home": in_home,
                "target_kind": tkind,
                "target_artifact_id": tid,
                "target_status": "available" if target_readable else "withheld",
                "observation_note": link["observation_note"],
                "shared": link["shared"],
                "created_at": link["created_at"],
                "updated_at": link["updated_at"],
            })
        elif link["shared"]:
            if tkind == "album":
                partner_status = "available" if target_readable else "withheld"
                partner_entry: dict[str, Any] = {
                    "link_id": link["link_id"],
                    "plant_id": pid,
                    "plant_name": resolved_plant_name,
                    "target_kind": tkind,
                    "target_status": partner_status,
                    "observation_note": link["observation_note"],
                    "updated_at": link["updated_at"],
                }
            else:
                if target_readable and target_delivered:
                    partner_entry = {
                        "link_id": link["link_id"],
                        "plant_id": pid,
                        "plant_name": resolved_plant_name,
                        "target_kind": tkind,
                        "target_artifact_id": tid,
                        "target_status": "available",
                        "observation_note": link["observation_note"],
                        "updated_at": link["updated_at"],
                    }
                else:
                    partner_entry = {
                        "link_id": link["link_id"],
                        "plant_id": pid,
                        "plant_name": resolved_plant_name,
                        "target_kind": tkind,
                        "target_status": "withheld",
                        "observation_note": link["observation_note"],
                        "updated_at": link["updated_at"],
                    }
            links_out.append(partner_entry)

    # 3) Co-authored revisable shared habits
    habits_out = [
        {
            "habit_id": h["habit_id"],
            "author_side": h["author_side"],
            "title": h["title"],
            "habit_text": h["habit_text"],
            "status": h["status"],
            "revision_count": h["revision_count"],
            "revisions": [dict(r) for r in h["revisions"]],
            "retired_reason": h["retired_reason"],
            "retired_at": h["retired_at"],
            "created_at": h["created_at"],
            "updated_at": h["updated_at"],
        }
        for h in current["shared_habits"]
    ]
    active_habits_count = sum(1 for h in habits_out if h["status"] == "ACTIVE")
    retired_habits_count = sum(1 for h in habits_out if h["status"] == "RETIRED")

    home_plants_catalog = [
        {
            "plant_id": str(info.get("plant_id") or pid),
            "name": str(info.get("name") or pid),
            "area_id": info.get("area_id"),
        }
        for pid, info in home_plants_by_id.items()
    ]

    return {
        "schema": FIRST_ROOM_HABITS_SCHEMA,
        "read_only": False,
        "view": "subject" if subject_view else "partner_default",
        "state": {
            "available": state_reason is None,
            "revision": _int_or_none(current.get("revision")),
            "updated_at": current.get("updated_at"),
            "reason_code": state_reason,
        },
        "first_room": first_room_out,
        "plant_sketch_links": {
            "home_plants": home_plants_catalog,
            "items": links_out,
            "visible_count": len(links_out),
        },
        "shared_habits": {
            "items": habits_out,
            "active_count": active_habits_count,
            "retired_count": retired_habits_count,
            "no_hard_constraint": True,
            "does_not_hijack_decision": True,
        },
        "private_text_included": bool(subject_view and private_included),
        "local_only": True,
        "note": "首日房间快照一旦封存即不可覆盖；盆栽手账连结剥离私密相册与未投递作品 ID；"
                "共同习惯册保留修订轨迹且绝不作硬约束。",
    }


# ---------------------------------------------------------------------------------------------
# File 2: longterm_gifts_lantern.json (shared_gifts + memory_lantern dual-layer lighting)
# ---------------------------------------------------------------------------------------------


def empty_gifts_lantern_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "shared_gifts": [],
        "later_reflections": [],
    }


def _validate_shared_gift(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")
    gift_id = _local_id(raw.get("gift_id"), "GIFTS_LANTERN_STATE_INVALID")
    author_side = _author_side(raw.get("author_side"), "GIFTS_LANTERN_STATE_INVALID")
    stage = raw.get("stage")
    if not isinstance(stage, str) or stage not in GIFT_STAGES:
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")
    title = _text_field(raw.get("title"), MAX_TITLE, "GIFTS_LANTERN_STATE_INVALID")
    draft_seed_note = _text_field(
        raw.get("draft_seed_note"), MAX_GIFT_NOTE, "GIFTS_LANTERN_STATE_INVALID")
    finished_note = _optional_text_field(
        raw.get("finished_note"), MAX_GIFT_NOTE, "GIFTS_LANTERN_STATE_INVALID")
    artifact_id = _optional_artifact_ref(raw.get("artifact_id"), "GIFTS_LANTERN_STATE_INVALID")
    version_id = _optional_artifact_ref(raw.get("version_id"), "GIFTS_LANTERN_STATE_INVALID")
    if (artifact_id is None) != (version_id is None):
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")
    gifted_at = _optional_iso(raw.get("gifted_at"))
    if stage == "gifted" and gifted_at is None:
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")
    created_at = _iso_text(raw.get("created_at"), "GIFTS_LANTERN_STATE_INVALID")
    updated_at = _iso_text(raw.get("updated_at"), "GIFTS_LANTERN_STATE_INVALID")
    return {
        "gift_id": gift_id,
        "author_side": author_side,
        "stage": stage,
        "title": title,
        "draft_seed_note": draft_seed_note,
        "finished_note": finished_note,
        "artifact_id": artifact_id,
        "version_id": version_id,
        "gifted_at": gifted_at,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _validate_later_reflection(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")
    reflection_id = _local_id(raw.get("reflection_id"), "GIFTS_LANTERN_STATE_INVALID")
    source_artifact_id = _artifact_ref(
        raw.get("source_artifact_id"), "GIFTS_LANTERN_STATE_INVALID")
    source_version_id = _artifact_ref(
        raw.get("source_version_id"), "GIFTS_LANTERN_STATE_INVALID")
    reflection_note = _text_field(
        raw.get("reflection_note"), MAX_REFLECTION_NOTE, "GIFTS_LANTERN_STATE_INVALID")
    shared = _strict_bool(raw.get("shared"), "GIFTS_LANTERN_STATE_INVALID")
    recorded_at = _iso_text(raw.get("recorded_at"), "GIFTS_LANTERN_STATE_INVALID")
    updated_at = _iso_text(raw.get("updated_at"), "GIFTS_LANTERN_STATE_INVALID")
    return {
        "reflection_id": reflection_id,
        "source_artifact_id": source_artifact_id,
        "source_version_id": source_version_id,
        "reflection_note": reflection_note,
        "shared": shared,
        "recorded_at": recorded_at,
        "updated_at": updated_at,
    }


def _validate_gifts_lantern_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")
    revision = _nonneg_int(raw.get("revision"), "GIFTS_LANTERN_STATE_INVALID")
    updated_at = _optional_iso(raw.get("updated_at"))

    gifts_raw = raw.get("shared_gifts")
    if not isinstance(gifts_raw, list) or len(gifts_raw) > MAX_SHARED_GIFTS:
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")
    gifts = [_validate_shared_gift(item) for item in gifts_raw]
    if len({g["gift_id"] for g in gifts}) != len(gifts):
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")

    refl_raw = raw.get("later_reflections")
    if not isinstance(refl_raw, list) or len(refl_raw) > MAX_LATER_REFLECTIONS:
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")
    reflections = [_validate_later_reflection(item) for item in refl_raw]
    if len({r["reflection_id"] for r in reflections}) != len(reflections):
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")
    if len({(r["source_artifact_id"], r["source_version_id"]) for r in reflections}) != len(reflections):
        raise LongtermStateError("GIFTS_LANTERN_STATE_INVALID")

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": revision,
        "updated_at": updated_at,
        "shared_gifts": gifts,
        "later_reflections": reflections,
    }


def read_gifts_lantern_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(gifts_lantern_state_path(root))
    if reason is not None:
        return empty_gifts_lantern_state(), "GIFTS_LANTERN_" + reason
    if raw is None:
        return empty_gifts_lantern_state(), None
    try:
        return _validate_gifts_lantern_state(raw), None
    except LongtermStateError:
        return empty_gifts_lantern_state(), "GIFTS_LANTERN_STATE_INVALID"


def write_gifts_lantern_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    path = gifts_lantern_state_path(root)
    with _tx.state_file_lock(path):
        if path.exists() and not path.is_symlink() and path.is_file():
            existing_raw, existing_reason = _read_json(path)
            if existing_reason is not None:
                raise LongtermStateError("GIFTS_LANTERN_" + existing_reason, conflict=True)
            if existing_raw is not None:
                _validate_gifts_lantern_state(existing_raw)
        normalized = _validate_gifts_lantern_state(dict(state))
        _write_json(path, normalized, prefix="GIFTS_LANTERN")
        return normalized


def _bump_gifts_lantern(state: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    out = _validate_gifts_lantern_state(dict(state))
    out["revision"] = int(out["revision"]) + 1
    out["updated_at"] = _iso_text(now_iso)
    return out


def apply_upsert_gift(
    state: Mapping[str, Any],
    *,
    gift_id: Optional[str] = None,
    author_side: str,
    stage: str,
    title: str,
    draft_seed_note: str,
    finished_note: Optional[str] = None,
    artifact_id: Optional[str] = None,
    version_id: Optional[str] = None,
    finished_note_supplied: bool = False,
    link_supplied: bool = False,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Create or update a gift in `draft` or `wrapped` stage before it is sent."""

    current = _validate_gifts_lantern_state(dict(state))
    clean_now = _iso_text(now_iso)
    clean_author = _author_side(author_side)
    if not isinstance(stage, str) or stage.strip() not in GIFT_UPSERT_STAGES:
        if isinstance(stage, str) and stage.strip() == "gifted":
            raise LongtermStateError("GIFT_STAGE_REQUIRES_SEND", conflict=True)
        raise LongtermStateError("GIFT_STAGE_INVALID")
    clean_stage = stage.strip()
    clean_title = _text_field(title, MAX_TITLE, "GIFT_TITLE_INVALID")
    clean_seed = _text_field(draft_seed_note, MAX_GIFT_NOTE, "GIFT_DRAFT_SEED_INVALID")
    clean_finished = _optional_text_field(finished_note, MAX_GIFT_NOTE, "GIFT_FINISHED_NOTE_INVALID")
    clean_art = _optional_artifact_ref(artifact_id, "GIFT_ARTIFACT_ID_INVALID")
    clean_ver = _optional_artifact_ref(version_id, "GIFT_VERSION_ID_INVALID")
    if (clean_art is None) != (clean_ver is None):
        raise LongtermStateError("GIFT_WORK_LINK_INCOMPLETE")
    if clean_author == "partner" and clean_art is not None:
        raise LongtermStateError("PARTNER_CANNOT_LINK_OWNER_WORK", conflict=True)

    if gift_id is not None:
        clean_id = _local_id(gift_id, "GIFT_ID_INVALID")
        found = next((g for g in current["shared_gifts"] if g["gift_id"] == clean_id), None)
        if found is None:
            raise LongtermStateError("GIFT_NOT_FOUND", conflict=True)
        if found["author_side"] != clean_author:
            raise LongtermStateError("AUTHOR_MISMATCH", conflict=True)
        if found["stage"] == "gifted":
            raise LongtermStateError("GIFT_ALREADY_SENT", conflict=True)

        next_finished = (
            clean_finished if (finished_note_supplied or clean_finished is not None)
            else found["finished_note"]
        )
        if link_supplied or clean_art is not None:
            next_art, next_ver = clean_art, clean_ver
        else:
            next_art, next_ver = found["artifact_id"], found["version_id"]

        if (found["stage"] == clean_stage
                and found["title"] == clean_title
                and found["draft_seed_note"] == clean_seed
                and found["finished_note"] == next_finished
                and found["artifact_id"] == next_art
                and found["version_id"] == next_ver):
            return current, dict(found), False

        found["stage"] = clean_stage
        found["title"] = clean_title
        found["draft_seed_note"] = clean_seed
        found["finished_note"] = next_finished
        found["artifact_id"] = next_art
        found["version_id"] = next_ver
        found["updated_at"] = clean_now
        return _bump_gifts_lantern(current, clean_now), dict(found), True

    if len(current["shared_gifts"]) >= MAX_SHARED_GIFTS:
        raise LongtermStateError("SHARED_GIFTS_FULL", conflict=True)

    seq = len(current["shared_gifts"]) + int(current["revision"]) + 1
    new_id = _stable_id("gift", clean_author, clean_title, clean_now, str(seq))
    while any(g["gift_id"] == new_id for g in current["shared_gifts"]):
        seq += 1
        new_id = _stable_id("gift", clean_author, clean_title, clean_now, str(seq))

    entry = {
        "gift_id": new_id,
        "author_side": clean_author,
        "stage": clean_stage,
        "title": clean_title,
        "draft_seed_note": clean_seed,
        "finished_note": clean_finished,
        "artifact_id": clean_art,
        "version_id": clean_ver,
        "gifted_at": None,
        "created_at": clean_now,
        "updated_at": clean_now,
    }
    current["shared_gifts"].append(entry)
    return _bump_gifts_lantern(current, clean_now), dict(entry), True


def apply_send_gift(
    state: Mapping[str, Any],
    *,
    gift_id: str,
    author_side: str,
    finished_note: Optional[str] = None,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Deliver a `draft` or `wrapped` gift to `gifted`, preserving `draft_seed_note`."""

    current = _validate_gifts_lantern_state(dict(state))
    clean_now = _iso_text(now_iso)
    clean_id = _local_id(gift_id, "GIFT_ID_INVALID")
    clean_author = _author_side(author_side)
    clean_finished = _optional_text_field(finished_note, MAX_GIFT_NOTE, "GIFT_FINISHED_NOTE_INVALID")

    found = next((g for g in current["shared_gifts"] if g["gift_id"] == clean_id), None)
    if found is None:
        raise LongtermStateError("GIFT_NOT_FOUND", conflict=True)
    if found["author_side"] != clean_author:
        raise LongtermStateError("AUTHOR_MISMATCH", conflict=True)
    if found["stage"] == "gifted":
        raise LongtermStateError("GIFT_ALREADY_SENT", conflict=True)

    found["stage"] = "gifted"
    if clean_finished is not None:
        found["finished_note"] = clean_finished
    found["gifted_at"] = clean_now
    found["updated_at"] = clean_now
    return _bump_gifts_lantern(current, clean_now), dict(found), True


def apply_record_later_reflection(
    state: Mapping[str, Any],
    *,
    source_artifact_id: str,
    source_version_id: str,
    reflection_note: str,
    shared: bool = False,
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Record or update a later-layer reflection anchored to `(source_artifact_id, source_version_id)`."""

    current = _validate_gifts_lantern_state(dict(state))
    clean_now = _iso_text(now_iso)
    clean_art = _artifact_ref(source_artifact_id, "REFLECTION_SOURCE_ARTIFACT_INVALID")
    clean_ver = _artifact_ref(source_version_id, "REFLECTION_SOURCE_VERSION_INVALID")
    clean_note = _text_field(reflection_note, MAX_REFLECTION_NOTE, "REFLECTION_NOTE_INVALID")
    clean_shared = _strict_bool(shared, "REFLECTION_SHARED_INVALID")

    existing = next(
        (
            r for r in current["later_reflections"]
            if r["source_artifact_id"] == clean_art and r["source_version_id"] == clean_ver
        ),
        None,
    )
    if existing is not None:
        if existing["reflection_note"] == clean_note and existing["shared"] == clean_shared:
            return current, dict(existing), False
        existing["reflection_note"] = clean_note
        existing["shared"] = clean_shared
        existing["updated_at"] = clean_now
        return _bump_gifts_lantern(current, clean_now), dict(existing), True

    if len(current["later_reflections"]) >= MAX_LATER_REFLECTIONS:
        raise LongtermStateError("LATER_REFLECTIONS_FULL", conflict=True)

    reflection_id = _stable_id("reflect", clean_art, clean_ver)
    entry = {
        "reflection_id": reflection_id,
        "source_artifact_id": clean_art,
        "source_version_id": clean_ver,
        "reflection_note": clean_note,
        "shared": clean_shared,
        "recorded_at": clean_now,
        "updated_at": clean_now,
    }
    current["later_reflections"].append(entry)
    return _bump_gifts_lantern(current, clean_now), dict(entry), True


def format_version_node_projection(
    *,
    artifact_id: str,
    version_id: str,
    inspection: Mapping[str, Any],
    subject_view: bool,
    extra_fields: Optional[Mapping[str, Any]] = None,
) -> tuple[dict[str, Any], bool]:
    """Project one pinned `(artifact_id, version_id)` node safely for `subject` or `partner_default`.

    Returns `(projected_dict, private_text_used)`.
    """

    lifecycle = str(inspection.get("lifecycle") or "")
    readable = bool(inspection.get("readable"))
    reason = str(inspection.get("reason_code") or "") or SOURCE_NOT_READABLE
    extra = dict(extra_fields or {})

    if lifecycle == "ARCHIVED" or reason == ARCHIVED_SOURCE:
        node: dict[str, Any] = {
            "status": "withheld",
            "reason_code": ARCHIVED_SOURCE,
            "title": None,
            "excerpt": None,
            "created_at": None,
        }
        if subject_view:
            node["artifact_id"] = artifact_id
            node["version_id"] = version_id
        for key in ("purpose", "taken_at"):
            if key in extra:
                node[key] = extra[key] if subject_view else None
        return node, False

    if not readable:
        node = {
            "status": "withheld",
            "reason_code": reason,
            "title": None,
            "excerpt": None,
            "created_at": None,
        }
        if subject_view:
            node["artifact_id"] = artifact_id
            node["version_id"] = version_id
        for key in ("purpose", "taken_at"):
            if key in extra:
                node[key] = extra[key] if subject_view else None
        return node, False

    if subject_view:
        owner_excerpt = _bounded_body(inspection.get("owner_excerpt"), MAX_EXCERPT)
        delivered = bool(inspection.get("delivered"))
        node = {
            "status": "available",
            "reason_code": None,
            "artifact_id": artifact_id,
            "version_id": version_id,
            "version_number": _int_or_none(inspection.get("version_number")),
            "title": inspection.get("title"),
            "excerpt": owner_excerpt,
            "created_at": inspection.get("version_created_at"),
            "delivered": delivered,
        }
        for key, val in extra.items():
            node[key] = val
        return node, (not delivered)

    # partner_default: must have exact-version LOCAL_DELIVERED share!
    if not bool(inspection.get("delivered")):
        node = {
            "status": "withheld",
            "reason_code": VERSION_NOT_DELIVERED,
            "title": None,
            "excerpt": None,
            "created_at": None,
        }
        for key in ("purpose", "taken_at"):
            if key in extra:
                node[key] = None
        return node, False

    share_excerpt = _bounded_body(inspection.get("delivered_excerpt"), MAX_EXCERPT)
    node = {
        "status": "available",
        "reason_code": None,
        "artifact_id": artifact_id,
        "version_id": version_id,
        "share_id": inspection.get("share_id"),
        "title": inspection.get("delivered_title") or inspection.get("title"),
        "excerpt": share_excerpt,
        "created_at": inspection.get("version_created_at"),
    }
    if "purpose" in extra:
        node["purpose"] = extra["purpose"]
    if "taken_at" in extra:
        node["taken_at"] = extra["taken_at"]
    return node, False


def gifts_lantern_block(
    *,
    state: Mapping[str, Any],
    state_reason: Optional[str],
    layer: str,
    version_inspections: Mapping[tuple[str, str], Mapping[str, Any]],
    derived_lineage: Iterable[Mapping[str, Any]],
    subject_view: bool,
) -> dict[str, Any]:
    """Project `shared_gifts` and `memory_lantern` (`then_only` vs `with_later_context`)."""

    if layer not in LANTERN_LAYERS:
        raise LongtermStateError("LANTERN_LAYER_INVALID")
    current = _validate_gifts_lantern_state(dict(state))
    private_included = False

    # 1) Shared gifts: her draft/wrapped gifts are completely invisible in partner_default!
    gifts_out: list[dict[str, Any]] = []
    for gift in current["shared_gifts"]:
        if not subject_view and gift["author_side"] == "her" and gift["stage"] != "gifted":
            continue
        if subject_view and gift["author_side"] == "her" and gift["stage"] != "gifted":
            private_included = True

        art_id = gift["artifact_id"]
        ver_id = gift["version_id"]
        linked_status = "none"
        linked_reason: Optional[str] = None
        linked_excerpt: Optional[str] = None
        expose_link_ids = False

        if art_id is not None and ver_id is not None:
            insp = version_inspections.get((art_id, ver_id)) or {"readable": False}
            proj_node, used_priv = format_version_node_projection(
                artifact_id=art_id,
                version_id=ver_id,
                inspection=insp,
                subject_view=subject_view,
            )
            if used_priv:
                private_included = True
            linked_status = proj_node["status"]
            linked_reason = proj_node.get("reason_code")
            linked_excerpt = proj_node.get("excerpt")
            expose_link_ids = subject_view or (linked_status == "available")

        gift_row: dict[str, Any] = {
            "gift_id": gift["gift_id"],
            "author_side": gift["author_side"],
            "stage": gift["stage"],
            "title": gift["title"],
            "draft_seed_note": gift["draft_seed_note"],
            "finished_note": gift["finished_note"],
            "gifted_at": gift["gifted_at"],
            "linked_work_status": linked_status,
            "linked_work_reason": linked_reason,
            "linked_work_excerpt": linked_excerpt,
            "created_at": gift["created_at"],
            "updated_at": gift["updated_at"],
        }
        if expose_link_ids and art_id is not None and ver_id is not None:
            gift_row["artifact_id"] = art_id
            gift_row["version_id"] = ver_id
        gifts_out.append(gift_row)

    gifted_count = sum(1 for g in gifts_out if g["stage"] == "gifted")

    # 2) Memory lantern: group real derived works + later_reflections by (source_artifact_id, source_version_id)
    roots_order: list[tuple[str, str]] = []
    remakes_by_root: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for remake in derived_lineage:
        src_art = str(remake.get("source_artifact_id") or "")
        src_ver = str(remake.get("source_version_id") or "")
        if not src_art or not src_ver:
            continue
        key = (src_art, src_ver)
        if key not in remakes_by_root:
            roots_order.append(key)
            remakes_by_root[key] = []
        remakes_by_root[key].append(remake)

    reflections_by_root: dict[tuple[str, str], dict[str, Any]] = {}
    for refl in current["later_reflections"]:
        key = (refl["source_artifact_id"], refl["source_version_id"])
        reflections_by_root[key] = refl
        if key not in remakes_by_root:
            # In partner_default, do not leak existence of a private-only reflection on an unrelated work
            if subject_view or refl["shared"]:
                roots_order.append(key)
                remakes_by_root[key] = []

    later_context_included = (layer == "with_later_context")
    lantern_entries: list[dict[str, Any]] = []

    for src_art, src_ver in roots_order:
        src_insp = version_inspections.get((src_art, src_ver)) or {"readable": False}
        then_snapshot, used_priv = format_version_node_projection(
            artifact_id=src_art,
            version_id=src_ver,
            inspection=src_insp,
            subject_view=subject_view,
        )
        if used_priv:
            private_included = True

        later_remakes_out: list[dict[str, Any]] = []
        later_reflection_out: Optional[dict[str, Any]] = None

        if later_context_included:
            for remake in remakes_by_root.get((src_art, src_ver), ()):
                d_art = str(remake.get("derived_artifact_id") or "")
                d_ver = str(remake.get("derived_version_id") or "")
                d_insp = version_inspections.get((d_art, d_ver)) or {"readable": False}
                extra: dict[str, Any] = {
                    "purpose": remake.get("purpose"),
                    "taken_at": remake.get("taken_at"),
                }
                if subject_view and remake.get("provenance_note") is not None:
                    extra["provenance_note"] = remake.get("provenance_note")
                remake_node, r_priv = format_version_node_projection(
                    artifact_id=d_art,
                    version_id=d_ver,
                    inspection=d_insp,
                    subject_view=subject_view,
                    extra_fields=extra,
                )
                if r_priv:
                    private_included = True
                later_remakes_out.append(remake_node)

            refl = reflections_by_root.get((src_art, src_ver))
            if refl is not None:
                if subject_view:
                    if not refl["shared"]:
                        private_included = True
                    later_reflection_out = {
                        "reflection_id": refl["reflection_id"],
                        "reflection_note": refl["reflection_note"],
                        "shared": refl["shared"],
                        "recorded_at": refl["recorded_at"],
                        "updated_at": refl["updated_at"],
                    }
                elif refl["shared"]:
                    later_reflection_out = {
                        "reflection_id": refl["reflection_id"],
                        "reflection_note": refl["reflection_note"],
                        "shared": True,
                        "recorded_at": refl["recorded_at"],
                        "updated_at": refl["updated_at"],
                    }

        entry_id = _stable_id("lantern", src_art, src_ver)
        lantern_entries.append({
            "entry_id": entry_id,
            "then_snapshot": then_snapshot,
            "later_remakes": later_remakes_out,
            "later_reflection": later_reflection_out,
        })

    return {
        "schema": GIFTS_LANTERN_SCHEMA,
        "read_only": False,
        "view": "subject" if subject_view else "partner_default",
        "state": {
            "available": state_reason is None,
            "revision": _int_or_none(current.get("revision")),
            "updated_at": current.get("updated_at"),
            "reason_code": state_reason,
        },
        "shared_gifts": {
            "gifts": gifts_out,
            "visible_count": len(gifts_out),
            "gifted_count": gifted_count,
            "preserves_draft_journey": True,
        },
        "memory_lantern": {
            "layer": layer,
            "later_context_included": later_context_included,
            "past_knows_future": False,
            "entries": lantern_entries,
            "entry_count": len(lantern_entries),
        },
        "private_text_included": bool(subject_view and private_included),
        "local_only": True,
        "note": "未送出的她的礼物草稿对伴侣默认完全隐形，送出后同时公开草稿初念与成品寄语；"
                "回忆双层灯光默认只亮当年快照，绝不让过去预知未来。",
    }


# ---------------------------------------------------------------------------------------------
# Pure Zero-Write Aggregation: GET /api/phone/longterm/resume-hub
# ---------------------------------------------------------------------------------------------


def resume_hub_block(
    *,
    active_projects: Iterable[Mapping[str, Any]],
    archived_projects: Iterable[Mapping[str, Any]],
    latest_delivered_project_share: Optional[Mapping[str, Any]],
    reading_bookmark: Mapping[str, Any],
    latest_topic_share: Optional[Mapping[str, Any]],
    recovery_summary: Mapping[str, Any],
    subject_view: bool,
) -> dict[str, Any]:
    """Pure zero-write aggregation for returning to recent project/book/topic + closed & recovery overview."""

    active_list = list(active_projects)
    archived_list = list(archived_projects)
    has_active = len(active_list) > 0
    latest_active = active_list[0] if has_active else None

    private_included = False

    # 1) Project cue
    delivered_proj = None
    if isinstance(latest_delivered_project_share, Mapping):
        delivered_proj = {
            "share_id": latest_delivered_project_share.get("share_id"),
            "title": latest_delivered_project_share.get("title"),
            "artifact_id": latest_delivered_project_share.get("artifact_id"),
            "version_id": latest_delivered_project_share.get("version_id"),
            "delivered_at": latest_delivered_project_share.get("delivered_at"),
        }

    if subject_view:
        if latest_active is not None:
            private_included = True
        project_cue: dict[str, Any] = {
            "has_active_project": has_active,
            "artifact_id": latest_active.get("artifact_id") if latest_active else None,
            "title": latest_active.get("title") if latest_active else None,
            "head_version_id": latest_active.get("head_version_id") if latest_active else None,
            "updated_at": latest_active.get("updated_at") if latest_active else None,
            "latest_delivered_share": delivered_proj,
        }
    else:
        project_cue = {
            "has_active_project": has_active,
            "latest_delivered_share": delivered_proj,
            "share_id": delivered_proj["share_id"] if delivered_proj else None,
            "title": delivered_proj["title"] if delivered_proj else None,
            "artifact_id": delivered_proj["artifact_id"] if delivered_proj else None,
            "version_id": delivered_proj["version_id"] if delivered_proj else None,
            "delivered_at": delivered_proj["delivered_at"] if delivered_proj else None,
        }

    # 2) Reading cue (title/excerpt private to subject_view, matching desk_presence disclosure)
    has_book = bool(reading_bookmark.get("has_book"))
    reading_cue: dict[str, Any] = {
        "has_book": has_book,
        "current_page": _int_or_none(reading_bookmark.get("current_page")) if has_book else None,
        "total_pages": _int_or_none(reading_bookmark.get("total_pages")) if has_book else None,
        "updated_at": reading_bookmark.get("updated_at") if has_book else None,
    }
    if subject_view:
        title_hint = reading_bookmark.get("title")
        if title_hint:
            private_included = True
        reading_cue["title"] = title_hint if has_book else None

    # 3) Topic cue (latest delivered share / safe co-presence topic)
    if isinstance(latest_topic_share, Mapping) and latest_topic_share.get("share_id"):
        topic_cue = {
            "has_recent_topic": True,
            "latest_share_id": latest_topic_share.get("share_id"),
            "latest_share_title": latest_topic_share.get("title"),
            "updated_at": latest_topic_share.get("updated_at"),
        }
    else:
        topic_cue = {
            "has_recent_topic": False,
            "latest_share_id": None,
            "latest_share_title": None,
            "updated_at": None,
        }

    # 4) Closed / archived projects without nagging
    closed_projects: dict[str, Any] = {
        "archived_count": len(archived_list),
        "closed_cleanly": True,
        "no_nagging": True,
    }
    if subject_view:
        if archived_list:
            private_included = True
        closed_projects["items"] = [
            {
                "artifact_id": row.get("artifact_id"),
                "title": row.get("title"),
                "updated_at": row.get("updated_at"),
            }
            for row in archived_list[:MAX_ARCHIVED_PREVIEW]
        ]

    # 5) Read-only backup / recovery overview
    recovery_overview = {
        "status": str(recovery_summary.get("status") or "UNBOUND"),
        "recovery_class": recovery_summary.get("recovery_class"),
        "backup_restore_status": str(recovery_summary.get("backup_restore_status") or "UNKNOWN"),
        "forget_barrier_status": str(recovery_summary.get("forget_barrier_status") or "UNKNOWN"),
        "reason_code": recovery_summary.get("reason_code"),
        "read_only": True,
    }

    return {
        "schema": RESUME_HUB_SCHEMA,
        "read_only": True,
        "view": "subject" if subject_view else "partner_default",
        "project_cue": project_cue,
        "reading_cue": reading_cue,
        "topic_cue": topic_cue,
        "closed_projects": closed_projects,
        "recovery_overview": recovery_overview,
        "no_absence_guilt": True,
        "no_nagging": True,
        "private_text_included": bool(subject_view and private_included),
        "local_only": True,
        "note": "一键续接总览为纯零写现读聚合：已归档项目安静收起不催促，伴侣默认视角绝不泄露未分享或已归档项目标题。",
    }

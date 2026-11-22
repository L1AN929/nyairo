#!/usr/bin/env python3
"""窗边景致、冰箱贴 / 自选锁屏、分日慢写的信：一个薄投影 + 一份显式选择状态。

它是什么
--------
一个**纯投影**模块，外加一个只记录「她自己显式选过什么」的轻量 JSON：

* 窗户：把 ``PhoneWorld.snapshot()`` 的当前房间家具（世界包里真实声明的 plant 对象）、
  灯光块、``world_home_space`` 已由她自己决定的窗向/窗景/院子座位，以及当天 home 的
  ambient 模板声景收成一个有限输出。没有气象权威时 weather 就是 ``null`` + 具名原因；
  模板声景不是天气，也不推植物生长。
* 锁屏：只保存一张 ``PhoneAlbum`` 图片的引用与选择时间；正文、字节、路径、摘要仍在
  原 owner，读取仍走原有受控相册路由。
* 冰箱贴：只保存已 ``LOCAL_DELIVERED`` 分享的 ``share_id`` 与贴上时间；正文仍从分享
  owner 现读，来源被治理/不可读时扣留并具名。
* 信：**没有信件 store**。它只是既有「分享 → 精确 source version」与
  ``PrivateProjectService`` 多版本时间线的投影：把跨 >=2 个「owner 时间戳本地日」的
  累积作品/分享列出来，并如实标明这不是独立信件类型。

它不是什么
----------
* 不复制正文/图片进新库，不新建 ACL/token/权限，不起后台线程或定时器。
* 不按标题里有没有「信」猜身份，不把 owner wall-clock 伪称压缩业务钟的历史业务日。
* 不把「没收到」说成「今天无雨」，不把模板 ambient 声景说成真实气象。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

try:  # package import (tests / phone service import ``components.phone``)
    from ..life.scripts import state_tx_guard as _tx  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - flat import fallback (components/phone on sys.path)
    import sys as _sys

    _LIFE_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "life" / "scripts"
    if str(_LIFE_SCRIPTS_DIR) not in _sys.path:
        _sys.path.insert(0, str(_LIFE_SCRIPTS_DIR))
    import state_tx_guard as _tx  # type: ignore[no-redef]

WINDOW_SCHEMA = "phone.home.window.v1"
KEEPSAKES_SCHEMA = "phone.home.keepsakes.v1"
LETTERS_SCHEMA = "phone.home.letters.v1"

STATE_DIRNAME = "phone"
STATE_FILENAME = "window_keepsakes.json"
STATE_SCHEMA_VERSION = 1

MAX_CAPTION = 200
MAX_PIN_TEXT = 240
MAX_LETTER_TEXT = 1000
MAX_LETTERS = 20
MAX_SHARE_SCAN = 100
MAX_HISTORY = 50
MAX_PINS = 50

#: 慢信强制冷静期（设计 CHIYO v3.0 第10章 §一.2 原文："一封信从动念起草到寄出，
#: 必须至少间隔 3 个日历日"）。寄出入口校验 ``now >= drafted_at + 72h``，不满足拒绝。
SLOW_LETTER_COOLING_PERIOD_HOURS = 72.0
SLOW_LETTER_COOLING_SECONDS = 72 * 3600
SLOW_LETTER_COOLING_PERIOD = "SLOW_LETTER_COOLING_PERIOD"
MAX_SLOW_LETTER_DRAFTS_PER_AUTHOR = 10
MAX_COMMITTED_OP_RING = 256
DEFAULT_POSTED_DELAY_HOURS = 12
MIN_POSTED_DELAY_HOURS = 1.0
MAX_POSTED_DELAY_HOURS = 168.0

SLOW_LETTER_DRAFT_ACTIONS = frozenset({
    "save_slow_letter_draft",
    "revise_slow_letter_draft",
    "discard_slow_letter_draft",
    "post_drafted_slow_letter",
})
ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "save_slow_letter_draft": frozenset({
        "action", "title", "body", "postmark_place", "operation_key",
    }),
    "revise_slow_letter_draft": frozenset({
        "action", "draft_id", "title", "body", "postmark_place", "operation_key",
    }),
    "discard_slow_letter_draft": frozenset({
        "action", "draft_id", "operation_key",
    }),
    "post_drafted_slow_letter": frozenset({
        "action", "draft_id", "delay_hours", "postmark_place", "operation_key",
    }),
}


class SlowLetterCoolingError(ValueError):
    """Raised when attempting to post a slow-letter draft before its 72h cooling window ends."""

    def __init__(self, *, cools_until: str, remaining_cooling_seconds: int) -> None:
        super().__init__(SLOW_LETTER_COOLING_PERIOD)
        self.reason_code = SLOW_LETTER_COOLING_PERIOD
        self.cools_until = str(cools_until)
        self.remaining_cooling_seconds = int(remaining_cooling_seconds)
        self.http_status = 409

LOCAL_DELIVERED = "LOCAL_DELIVERED"
#: 只有「还没发」与「已本地投递」会进入信的投影；DROPPED/EXPIRED 是终态，不是信。
LETTER_SHARE_STATES = ("DRAFT", "DEFERRED", LOCAL_DELIVERED)
LOCAL_AUDIENCE = "local:owner"
LOCAL_CHANNEL = "local_inbox"

NO_WEATHER_AUTHORITY = "NO_WEATHER_AUTHORITY"
NO_PLANT_GROWTH_AUTHORITY = "NO_PLANT_GROWTH_AUTHORITY"
NO_DECLARED_HOME_PLANT = "NO_DECLARED_HOME_PLANT"
NOT_AT_HOME = "NOT_AT_HOME"
V04_NEIGHBORHOOD_SOURCE = "v04_neighborhood_living"


def _plant_soil_wet_dry_state(hydration: float) -> str:
    if hydration < 25.0:
        return "dry"
    if hydration < 65.0:
        return "moist"
    return "wet"


def read_v04_neighborhood_window_context(
    day_root: Optional[Path | str] = None,
    *,
    now_iso: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """纯只读零写入联动 ``v04_neighborhood_living`` 天气与窗台/院落绿植干湿状态。

    当 ``day_root`` 未创建 ``world/v04_neighborhood_state.json`` 时立即返回 ``None``，
    绝不创建目录、绝不改写 mtime，也绝不编造默认晴雨或干湿值。
    """
    candidate_roots: list[Path] = []
    if day_root is not None:
        candidate_roots.append(Path(day_root))
    else:
        for env_key in ("CHIYO_DAY_ROOT", "CHIYO_INSTANCE_ROOT"):
            env_val = os.environ.get(env_key, "").strip()
            if env_val:
                candidate_roots.append(Path(env_val))
        try:
            import sys as _sys

            caller_frame = _sys._getframe(2)
            caller_self = caller_frame.f_locals.get("self") if caller_frame is not None else None
            caller_root = getattr(caller_self, "root", None)
            if isinstance(caller_root, (str, Path)):
                candidate_roots.append(Path(caller_root))
        except Exception:
            pass

    resolved_day_root: Optional[Path] = None
    for base in candidate_roots:
        for sub in (base, base / "world-home"):
            state_file = sub / "world" / "v04_neighborhood_state.json"
            try:
                if not state_file.is_symlink() and state_file.is_file():
                    resolved_day_root = sub
                    break
            except OSError:
                continue
        if resolved_day_root is not None:
            break

    if resolved_day_root is None:
        return None

    try:
        try:
            from components.world.scripts import v04_neighborhood_living as v04_nb
        except Exception:
            import sys as _sys

            world_scripts = Path(__file__).resolve().parents[1] / "world" / "scripts"
            if str(world_scripts) not in _sys.path:
                _sys.path.insert(0, str(world_scripts))
            import v04_neighborhood_living as v04_nb  # type: ignore[no-redef]

        proj = v04_nb.project_v04_neighborhood(
            resolved_day_root,
            has_subject_capability=False,
            now_iso=now_iso,
        )
    except Exception:
        return None

    data = proj.get("data") if isinstance(proj, Mapping) else None
    if not isinstance(data, Mapping):
        return None
    env = data.get("environment") if isinstance(data.get("environment"), Mapping) else {}
    plant = data.get("potted_plant") if isinstance(data.get("potted_plant"), Mapping) else {}
    weather_kind = str(env.get("weather") or "").strip()
    if not weather_kind:
        return None

    hydration_val = round(float(plant.get("hydration", 70.0)), 4)
    wet_dry = _plant_soil_wet_dry_state(hydration_val)
    stage_val = str(plant.get("stage") or "leafy")
    return {
        "weather": {
            "value": weather_kind,
            "condition": weather_kind,
            "airflow": str(env.get("airflow") or "medium"),
            "season": str(env.get("season") or "autumn"),
            "yard_bench_damp": bool(env.get("yard_bench_damp", False)),
            "ground_state": str((env.get("ground_by_place") or {}).get("yard") or "dry")
            if isinstance(env.get("ground_by_place"), Mapping)
            else "dry",
            "status": "available",
            "reason_code": None,
            "source": V04_NEIGHBORHOOD_SOURCE,
            "is_weather_authority": True,
            "updated_at": env.get("updated_at"),
        },
        "plant": {
            "available": bool(plant),
            "plant_id": str(plant.get("plant_id") or " windowsill_plant").strip(),
            "name": _one_line(plant.get("name"), 80) or None,
            "place_id": str(plant.get("place_id") or "home"),
            "hydration": hydration_val,
            "wet_dry_state": wet_dry,
            "soil_state": wet_dry,
            "needs_watering": bool(hydration_val < 25.0 or stage_val == "wilting_recoverable"),
            "stage": stage_val,
            "growth_points": round(float(plant.get("growth_points", 0.0)), 4),
            "last_watered_at": plant.get("last_watered_at"),
            "never_dies_offline": bool(plant.get("never_dies_offline", True)),
            "source": V04_NEIGHBORHOOD_SOURCE,
        },
    }


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_body(value: Any, limit: int) -> str:
    """Keep the author's own line breaks; never reflow a share body."""

    return str(value or "")[:limit].strip()


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


# ---------------------------------------------------------------------------------------------
# the explicit local selection state (the only thing this module owns)
# ---------------------------------------------------------------------------------------------


def state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / STATE_FILENAME


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "lockscreen": None,
        "pins": [],
        "slow_letters": [],
        "slow_letter_drafts": [],
        "committed_op_ring": [],
    }


def _validate_slow_letter_draft_entry(item: Any) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError("invalid slow letter draft")
    draft_id = item.get("draft_id")
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError("invalid slow letter draft id")
    author = str(item.get("author_side") or "").strip().lower()
    if author not in ("subject", "partner"):
        raise ValueError("invalid slow letter draft author_side")
    title = _one_line(item.get("title"), 120)
    body = _bounded_body(item.get("body"), MAX_LETTER_TEXT)
    if not title or not body:
        raise ValueError("invalid slow letter draft content")
    drafted_at = _iso_text(item.get("drafted_at"))
    cools_until = _iso_text(item.get("cools_until"))
    if not drafted_at or not cools_until:
        raise ValueError("invalid slow letter draft timestamps")
    updated_at = _iso_text(item.get("updated_at")) or drafted_at
    status = str(item.get("status") or "cooling_draft").strip()
    if status != "cooling_draft":
        raise ValueError("invalid slow letter draft status")
    place = _one_line(item.get("postmark_place"), 80) or "镇上邮筒"
    op_key = _one_line(item.get("operation_key"), 128) or None
    return {
        "draft_id": draft_id.strip()[:120],
        "author_side": author,
        "title": title,
        "body": body,
        "postmark_place": place,
        "drafted_at": drafted_at,
        "cools_until": cools_until,
        "updated_at": updated_at,
        "status": "cooling_draft",
        "operation_key": op_key,
    }


def _validate_committed_op_ring_entry(item: Any) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError("invalid committed_op_ring entry")
    op_key = _one_line(item.get("operation_key"), 128)
    action = _one_line(item.get("action"), 64)
    author = str(item.get("author_side") or "").strip().lower()
    fingerprint = _one_line(item.get("fingerprint"), 128)
    if not op_key or not action or author not in ("subject", "partner") or not fingerprint:
        raise ValueError("invalid committed_op_ring fields")
    revision = _int_or_none(item.get("revision"))
    if revision is None or revision < 0:
        raise ValueError("invalid committed_op_ring revision")
    snapshot = item.get("snapshot")
    if snapshot is not None and not isinstance(snapshot, Mapping):
        raise ValueError("invalid committed_op_ring snapshot")
    return {
        "operation_key": op_key,
        "action": action,
        "author_side": author,
        "fingerprint": fingerprint,
        "target_id": _one_line(item.get("target_id"), 120) or None,
        "recorded_at": _iso_text(item.get("recorded_at")),
        "revision": revision,
        "snapshot": dict(snapshot) if isinstance(snapshot, Mapping) else None,
    }


def _validate_slow_letter_entry(item: Any) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError("invalid slow letter")
    letter_id = item.get("letter_id")
    if not isinstance(letter_id, str) or not letter_id.strip():
        raise ValueError("invalid slow letter id")
    title = _one_line(item.get("title"), 120)
    body = _bounded_body(item.get("body"), MAX_LETTER_TEXT)
    if not title or not body:
        raise ValueError("invalid slow letter content")
    sent_at = _iso_text(item.get("sent_at"))
    deliver_after = _iso_text(item.get("deliver_after"))
    if not sent_at or not deliver_after:
        raise ValueError("invalid slow letter timestamps")
    author = str(item.get("author_side") or "subject").strip().lower()
    if author not in ("subject", "partner"):
        raise ValueError("invalid slow letter author_side")
    place = _one_line(item.get("postmark_place"), 80) or "镇上邮筒"
    raw_delay = item.get("delay_hours", 6)
    delay_val = round(float(raw_delay), 3) if isinstance(raw_delay, (int, float)) and not isinstance(raw_delay, bool) else 6.0
    op_key = _one_line(item.get("operation_key"), 128) or None
    return {
        "letter_id": letter_id.strip()[:120],
        "title": title,
        "body": body,
        "postmark_place": place,
        "author_side": author,
        "sent_at": sent_at,
        # 缺省视为创建（起草）时刻 = 寄出时刻（历史存量信件无冷静期债务）
        "drafted_at": _iso_text(item.get("drafted_at")) or sent_at,
        "deliver_after": deliver_after,
        "delay_hours": max(0.0, delay_val),
        "acked_at": _iso_text(item.get("acked_at")),
        "operation_key": op_key,
    }


def _validate_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("state is not an object")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported schema")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")
    lockscreen = raw.get("lockscreen")
    if lockscreen is not None:
        if not isinstance(lockscreen, Mapping):
            raise ValueError("invalid lockscreen")
        ref = lockscreen.get("album_artifact_id")
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("invalid lockscreen ref")
        lockscreen = {"album_artifact_id": ref.strip()[:200],
                      "selected_at": _iso_text(lockscreen.get("selected_at"))}
    pins_raw = raw.get("pins")
    if not isinstance(pins_raw, list) or len(pins_raw) > MAX_PINS:
        raise ValueError("invalid pins")
    pins: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in pins_raw:
        if not isinstance(item, Mapping):
            raise ValueError("invalid pin")
        share_id = item.get("share_id")
        if not isinstance(share_id, str) or not share_id.strip():
            raise ValueError("invalid pin ref")
        share_id = share_id.strip()[:200]
        if share_id in seen:
            continue
        seen.add(share_id)
        pins.append({"share_id": share_id, "pinned_at": _iso_text(item.get("pinned_at"))})
    slow_raw = raw.get("slow_letters")
    if slow_raw is None:
        slow_raw = []
    if not isinstance(slow_raw, list) or len(slow_raw) > MAX_PINS:
        raise ValueError("invalid slow_letters")
    slow_letters: list[dict[str, Any]] = []
    seen_letters: set[str] = set()
    for item in slow_raw:
        entry = _validate_slow_letter_entry(item)
        if entry["letter_id"] in seen_letters:
            continue
        seen_letters.add(entry["letter_id"])
        slow_letters.append(entry)

    drafts_raw = raw.get("slow_letter_drafts")
    if drafts_raw is None:
        drafts_raw = []
    if not isinstance(drafts_raw, list) or len(drafts_raw) > MAX_SLOW_LETTER_DRAFTS_PER_AUTHOR * 2:
        raise ValueError("invalid slow_letter_drafts")
    slow_letter_drafts: list[dict[str, Any]] = []
    seen_drafts: set[str] = set()
    per_author_counts: dict[str, int] = {"subject": 0, "partner": 0}
    for item in drafts_raw:
        d_entry = _validate_slow_letter_draft_entry(item)
        if d_entry["draft_id"] in seen_drafts:
            continue
        author_key = d_entry["author_side"]
        per_author_counts[author_key] = per_author_counts.get(author_key, 0) + 1
        if per_author_counts[author_key] > MAX_SLOW_LETTER_DRAFTS_PER_AUTHOR:
            raise ValueError("slow_letter_drafts author limit exceeded")
        seen_drafts.add(d_entry["draft_id"])
        slow_letter_drafts.append(d_entry)

    ring_raw = raw.get("committed_op_ring")
    if ring_raw is None:
        ring_raw = []
    if not isinstance(ring_raw, list):
        raise ValueError("invalid committed_op_ring")
    committed_op_ring = [_validate_committed_op_ring_entry(item) for item in ring_raw]
    if len(committed_op_ring) > MAX_COMMITTED_OP_RING:
        committed_op_ring = committed_op_ring[-MAX_COMMITTED_OP_RING:]

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": int(revision),
        "updated_at": _iso_text(raw.get("updated_at")),
        "lockscreen": lockscreen,
        "pins": pins,
        "slow_letters": slow_letters,
        "slow_letter_drafts": slow_letter_drafts,
        "committed_op_ring": committed_op_ring,
    }


def _iso_text(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:40]


def read_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    """Read the one selection file.  Missing = empty; corrupt = fail closed, never guessed."""

    path = state_path(root)
    if path.is_symlink():
        return empty_state(), "KEEPSAKE_STATE_SYMLINK"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state(), None
    except (OSError, ValueError):
        return empty_state(), "KEEPSAKE_STATE_UNREADABLE"
    try:
        return _validate_state(raw), None
    except ValueError:
        return empty_state(), "KEEPSAKE_STATE_INVALID"


def write_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically replace the selection file; the caller already decided something changed."""

    path = state_path(root)
    with _tx.state_file_lock(path):
        normalized = _validate_state(dict(state))
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")) + "\n"
        descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".",
                                                 dir=str(path.parent), text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
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
        return normalized


def _bump(state: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    out = _validate_state(dict(state))
    out["revision"] = int(out["revision"]) + 1
    out["updated_at"] = _iso_text(now_iso)
    return out


def select_lockscreen(state: Mapping[str, Any], album_artifact_id: str,
                      now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_state(dict(state))
    chosen = {"album_artifact_id": str(album_artifact_id), "selected_at": _iso_text(now_iso)}
    existing = current.get("lockscreen") or {}
    if existing.get("album_artifact_id") == chosen["album_artifact_id"]:
        return current, False
    current["lockscreen"] = chosen
    return _bump(current, now_iso), True


def clear_lockscreen(state: Mapping[str, Any],
                     now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_state(dict(state))
    if not current.get("lockscreen"):
        return current, False
    current["lockscreen"] = None
    return _bump(current, now_iso), True


def pin_share(state: Mapping[str, Any], share_id: str,
              now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_state(dict(state))
    if any(row.get("share_id") == share_id for row in current["pins"]):
        return current, False
    current["pins"].append({"share_id": str(share_id), "pinned_at": _iso_text(now_iso)})
    if len(current["pins"]) > MAX_PINS:
        current["pins"] = current["pins"][-MAX_PINS:]
    return _bump(current, now_iso), True


def unpin_share(state: Mapping[str, Any], share_id: str,
                now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_state(dict(state))
    kept = [row for row in current["pins"] if row.get("share_id") != share_id]
    if len(kept) == len(current["pins"]):
        return current, False
    current["pins"] = kept
    return _bump(current, now_iso), True


def _format_instant_iso(moment: datetime) -> str:
    if moment.utcoffset() == timedelta(0):
        return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return moment.replace(microsecond=0).isoformat()


def post_slow_letter(
    root: Path | str,
    *,
    title: str,
    body: str,
    delay_hours: float | int = 6,
    postmark_place: str = "镇上邮筒",
    author_side: str = "subject",
    drafted_at: Any = None,
    now: Any = None,
    operation_key: Optional[str] = None,
) -> dict[str, Any]:
    """显式投递一封带邮戳与在途延迟的慢信，写入 ``window_keepsakes.json`` 的 ``slow_letters``。

    冷静期（设计 v3.0 第10章）：调用方传入 ``drafted_at``（动念起草时刻）时，寄出时刻
    ``now`` 必须满足 ``now >= drafted_at + SLOW_LETTER_COOLING_PERIOD_HOURS``（72h，即
    3 个日历日），否则以 ``SLOW_LETTER_COOLING_PERIOD`` 拒绝——是"拒绝"而非"静默推迟"，
    与本模块其余契约违规（``SLOW_LETTER_STILL_IN_TRANSIT`` 等）同样 fail-closed。
    未传 ``drafted_at`` 视为起草时刻 = 寄出时刻（无独立草稿态时的最小诚实缺省）。
    """
    clean_title = _one_line(title, 120)
    clean_body = _bounded_body(body, MAX_LETTER_TEXT)
    if not clean_title or not clean_body:
        raise ValueError("SLOW_LETTER_CONTENT_REQUIRED")
    if isinstance(delay_hours, bool) or not isinstance(delay_hours, (int, float)) or delay_hours < 0 or delay_hours > 720:
        raise ValueError("INVALID_SLOW_LETTER_DELAY")
    norm_author = str(author_side or "subject").strip().lower()
    if norm_author == "her":
        norm_author = "subject"
    if norm_author not in ("subject", "partner"):
        raise ValueError("INVALID_SLOW_LETTER_AUTHOR")
    clean_place = _one_line(postmark_place, 80) or "镇上邮筒"
    clean_op = _one_line(operation_key, 128) or None

    now_dt = parse_instant(now) or datetime.now(tz=timezone(timedelta(hours=8)))
    sent_iso = _format_instant_iso(now_dt)
    deliver_dt = now_dt + timedelta(hours=float(delay_hours))
    deliver_iso = _format_instant_iso(deliver_dt)

    with _tx.state_file_lock(state_path(root)):
        current, reason = read_state(root)
        if reason is not None:
            raise ValueError(reason)

        if clean_op is not None:
            for existing in current["slow_letters"]:
                if existing.get("operation_key") == clean_op:
                    if (existing.get("title") != clean_title
                            or existing.get("body") != clean_body
                            or existing.get("author_side") != norm_author):
                        raise ValueError("OPERATION_FINGERPRINT_MISMATCH")
                    status_now = "in_transit" if now_dt < (parse_instant(existing["deliver_after"]) or deliver_dt) else "delivered"
                    return {**dict(existing), "status": status_now, "duplicate": True, "revision": current["revision"]}

        drafted_dt = parse_instant(drafted_at) if drafted_at is not None else None
        if drafted_dt is not None:
            earliest_send_dt = drafted_dt + timedelta(hours=SLOW_LETTER_COOLING_PERIOD_HOURS)
            if now_dt < earliest_send_dt:
                raise ValueError(SLOW_LETTER_COOLING_PERIOD)
        else:
            drafted_dt = now_dt
        drafted_iso = _format_instant_iso(drafted_dt)

        seed = f"{clean_title}\x00{sent_iso}\x00{norm_author}\x00{clean_op or current['revision']}"
        letter_id = "slow_letter_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        entry = {
            "letter_id": letter_id,
            "title": clean_title,
            "body": clean_body,
            "postmark_place": clean_place,
            "author_side": norm_author,
            "sent_at": sent_iso,
            "drafted_at": drafted_iso,
            "deliver_after": deliver_iso,
            "delay_hours": round(float(delay_hours), 3),
            "acked_at": None,
            "operation_key": clean_op,
        }
        current["slow_letters"] = [*(current.get("slow_letters") or []), entry][-MAX_PINS:]
        saved = write_state(root, _bump(current, sent_iso))
        status_now = "in_transit" if now_dt < deliver_dt else "delivered"
        return {**entry, "status": status_now, "duplicate": False, "revision": saved["revision"]}


def ack_slow_letter(
    root: Path | str,
    *,
    letter_id: str,
    now: Any = None,
) -> dict[str, Any]:
    """确认已读一封已到点寄达的慢信（在途慢信不可提前已读）。"""
    clean_id = _one_line(letter_id, 120)
    if not clean_id:
        raise ValueError("SLOW_LETTER_ID_REQUIRED")
    now_dt = parse_instant(now) or datetime.now(tz=timezone(timedelta(hours=8)))
    now_iso = _format_instant_iso(now_dt)

    with _tx.state_file_lock(state_path(root)):
        current, reason = read_state(root)
        if reason is not None:
            raise ValueError(reason)

        found: Optional[dict[str, Any]] = None
        for row in current["slow_letters"]:
            if row.get("letter_id") == clean_id:
                found = row
                break
        if found is None:
            raise KeyError("SLOW_LETTER_NOT_FOUND")

        deliver_dt = parse_instant(found.get("deliver_after"))
        if deliver_dt is not None and now_dt < deliver_dt:
            raise ValueError("SLOW_LETTER_STILL_IN_TRANSIT")

        if found.get("acked_at"):
            return {**dict(found), "status": "delivered", "acked": True, "duplicate": True, "revision": current["revision"]}

        found["acked_at"] = now_iso
        saved = write_state(root, _bump(current, now_iso))
        return {**dict(found), "status": "delivered", "acked": True, "duplicate": False, "revision": saved["revision"]}


def _normalize_actor_side(actor_side: Optional[str], author_side: Optional[str] = None) -> str:
    raw = actor_side if actor_side is not None else author_side
    norm = str(raw or "").strip().lower()
    if norm not in ("subject", "partner"):
        raise ValueError("INVALID_SLOW_LETTER_AUTHOR")
    return norm


def _clean_operation_key(operation_key: Any) -> Optional[str]:
    if operation_key is None:
        return None
    if not isinstance(operation_key, str):
        raise ValueError("INVALID_OPERATION_KEY")
    cleaned = " ".join(operation_key.split()).strip()
    if not cleaned or len(cleaned) > 128:
        raise ValueError("INVALID_OPERATION_KEY")
    return cleaned


def _clean_draft_title(title: Any, *, allow_default: bool) -> str:
    if title is None:
        if allow_default:
            return "慢信手稿"
        raise ValueError("SLOW_LETTER_TITLE_INVALID")
    if not isinstance(title, str):
        raise ValueError("SLOW_LETTER_TITLE_INVALID")
    collapsed = " ".join(title.split()).strip()
    if len(collapsed) > 120:
        raise ValueError("SLOW_LETTER_TITLE_TOO_LONG")
    if not collapsed:
        if allow_default:
            return "慢信手稿"
        raise ValueError("SLOW_LETTER_TITLE_INVALID")
    return collapsed


def _clean_draft_body(body: Any) -> str:
    if not isinstance(body, str):
        raise ValueError("SLOW_LETTER_CONTENT_REQUIRED")
    stripped = body.strip()
    if not stripped:
        raise ValueError("SLOW_LETTER_CONTENT_REQUIRED")
    if len(stripped) > MAX_LETTER_TEXT:
        raise ValueError("SLOW_LETTER_BODY_TOO_LONG")
    return stripped


def _clean_postmark_place(postmark_place: Any, *, default: str = "镇上邮筒") -> str:
    if postmark_place is None:
        return default
    if not isinstance(postmark_place, str):
        raise ValueError("SLOW_LETTER_PLACE_INVALID")
    collapsed = " ".join(postmark_place.split()).strip()
    if len(collapsed) > 80:
        raise ValueError("SLOW_LETTER_PLACE_TOO_LONG")
    return collapsed or default


def _clean_draft_id(draft_id: Any) -> str:
    if not isinstance(draft_id, str):
        raise ValueError("DRAFT_ID_REQUIRED")
    cleaned = " ".join(draft_id.split()).strip()
    if not cleaned or len(cleaned) > 120:
        raise ValueError("DRAFT_ID_REQUIRED")
    return cleaned


def _op_fingerprint(action: str, actor_side: str, *parts: Any) -> str:
    joined = "\x1f".join([action, actor_side, *[str(p if p is not None else "") for p in parts]])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def _lookup_op_ring(
    state: Mapping[str, Any],
    *,
    actor_side: str,
    operation_key: Optional[str],
) -> Optional[dict[str, Any]]:
    if not operation_key:
        return None
    for entry in reversed(state.get("committed_op_ring") or []):
        if (
            isinstance(entry, Mapping)
            and entry.get("author_side") == actor_side
            and entry.get("operation_key") == operation_key
        ):
            return dict(entry)
    return None


def _append_op_ring(
    state: dict[str, Any],
    *,
    operation_key: Optional[str],
    action: str,
    actor_side: str,
    fingerprint: str,
    target_id: Optional[str],
    recorded_at: str,
    revision: int,
    snapshot: Mapping[str, Any],
) -> None:
    if not operation_key:
        return
    ring = list(state.get("committed_op_ring") or [])
    ring.append({
        "operation_key": operation_key,
        "action": action,
        "author_side": actor_side,
        "fingerprint": fingerprint,
        "target_id": target_id,
        "recorded_at": recorded_at,
        "revision": int(revision),
        "snapshot": dict(snapshot),
    })
    if len(ring) > MAX_COMMITTED_OP_RING:
        ring = ring[-MAX_COMMITTED_OP_RING:]
    state["committed_op_ring"] = ring


def _project_slow_letter_draft_row(
    raw: Mapping[str, Any],
    *,
    now_dt: datetime,
) -> dict[str, Any]:
    drafted_at_str = _iso_text(raw.get("drafted_at"))
    cools_until_str = _iso_text(raw.get("cools_until"))
    cools_dt = parse_instant(cools_until_str)
    if cools_dt is None:
        base_dt = parse_instant(drafted_at_str) or now_dt
        cools_dt = base_dt + timedelta(hours=SLOW_LETTER_COOLING_PERIOD_HOURS)
        cools_until_str = _format_instant_iso(cools_dt)
    ready = bool(now_dt >= cools_dt)
    delta_secs = (cools_dt - now_dt).total_seconds()
    remaining = 0 if ready else max(1, int(math.ceil(delta_secs)))
    return {
        "draft_id": str(raw.get("draft_id") or ""),
        "author_side": str(raw.get("author_side") or ""),
        "title": _one_line(raw.get("title"), 120) or "慢信手稿",
        "body": _bounded_body(raw.get("body"), MAX_LETTER_TEXT),
        "postmark_place": _one_line(raw.get("postmark_place"), 80) or "镇上邮筒",
        "drafted_at": drafted_at_str,
        "cools_until": cools_until_str,
        "updated_at": _iso_text(raw.get("updated_at")) or drafted_at_str,
        "status": "cooling_draft",
        "ready_to_post": ready,
        "remaining_cooling_seconds": remaining,
    }


def save_slow_letter_draft(
    root: Path | str,
    *,
    actor_side: Optional[str] = None,
    author_side: Optional[str] = None,
    title: Any = None,
    body: Any = None,
    postmark_place: Any = "镇上邮筒",
    now: Any = None,
    operation_key: Optional[str] = None,
) -> dict[str, Any]:
    """Create a 72h cooling slow-letter draft in ``window_keepsakes.json``."""
    norm_actor = _normalize_actor_side(actor_side, author_side)
    clean_title = _clean_draft_title(title, allow_default=True)
    clean_body = _clean_draft_body(body)
    clean_place = _clean_postmark_place(postmark_place)
    clean_op = _clean_operation_key(operation_key)

    now_dt = parse_instant(now) or datetime.now(tz=timezone(timedelta(hours=8)))
    drafted_iso = _format_instant_iso(now_dt)
    cools_dt = now_dt + timedelta(hours=SLOW_LETTER_COOLING_PERIOD_HOURS)
    cools_iso = _format_instant_iso(cools_dt)
    fp = _op_fingerprint("save_slow_letter_draft", norm_actor, clean_title, clean_body, clean_place)

    with _tx.state_file_lock(state_path(root)):
        current, reason = read_state(root)
        if reason is not None:
            raise ValueError(reason)

        cached = _lookup_op_ring(current, actor_side=norm_actor, operation_key=clean_op)
        if cached is not None:
            if cached.get("action") != "save_slow_letter_draft" or cached.get("fingerprint") != fp:
                raise ValueError("OPERATION_FINGERPRINT_MISMATCH")
            snap_draft = cached.get("snapshot") or {}
            live_match = next(
                (d for d in current["slow_letter_drafts"] if d.get("draft_id") == cached.get("target_id")),
                snap_draft,
            )
            projected = _project_slow_letter_draft_row(live_match, now_dt=now_dt)
            return {
                **projected,
                "draft": projected,
                "duplicate": True,
                "revision": current["revision"],
            }

        own_active = [
            d for d in current["slow_letter_drafts"] if d.get("author_side") == norm_actor
        ]
        if len(own_active) >= MAX_SLOW_LETTER_DRAFTS_PER_AUTHOR:
            raise ValueError("SLOW_LETTER_DRAFT_LIMIT_REACHED")

        seed = f"{norm_actor}\x00{clean_title}\x00{drafted_iso}\x00{clean_op or (current['revision'] + 1)}"
        draft_id = "slow_draft_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        entry = {
            "draft_id": draft_id,
            "author_side": norm_actor,
            "title": clean_title,
            "body": clean_body,
            "postmark_place": clean_place,
            "drafted_at": drafted_iso,
            "cools_until": cools_iso,
            "updated_at": drafted_iso,
            "status": "cooling_draft",
            "operation_key": clean_op,
        }
        current["slow_letter_drafts"] = [*current["slow_letter_drafts"], entry]
        bumped = _bump(current, drafted_iso)
        _append_op_ring(
            bumped,
            operation_key=clean_op,
            action="save_slow_letter_draft",
            actor_side=norm_actor,
            fingerprint=fp,
            target_id=draft_id,
            recorded_at=drafted_iso,
            revision=bumped["revision"],
            snapshot=entry,
        )
        saved = write_state(root, bumped)
        projected = _project_slow_letter_draft_row(entry, now_dt=now_dt)
        return {
            **projected,
            "draft": projected,
            "duplicate": False,
            "revision": saved["revision"],
        }


def revise_slow_letter_draft(
    root: Path | str,
    *,
    actor_side: Optional[str] = None,
    author_side: Optional[str] = None,
    draft_id: Any = None,
    title: Any = None,
    body: Any = None,
    postmark_place: Any = None,
    now: Any = None,
    operation_key: Optional[str] = None,
) -> dict[str, Any]:
    """Revise own cooling slow-letter draft without resetting ``drafted_at`` or ``cools_until``."""
    norm_actor = _normalize_actor_side(actor_side, author_side)
    clean_id = _clean_draft_id(draft_id)
    if title is None and body is None and postmark_place is None:
        raise ValueError("SLOW_LETTER_REVISION_EMPTY")
    new_title = _clean_draft_title(title, allow_default=False) if title is not None else None
    new_body = _clean_draft_body(body) if body is not None else None
    new_place = _clean_postmark_place(postmark_place) if postmark_place is not None else None
    clean_op = _clean_operation_key(operation_key)

    now_dt = parse_instant(now) or datetime.now(tz=timezone(timedelta(hours=8)))
    now_iso = _format_instant_iso(now_dt)
    fp = _op_fingerprint(
        "revise_slow_letter_draft", norm_actor, clean_id, new_title, new_body, new_place
    )

    with _tx.state_file_lock(state_path(root)):
        current, reason = read_state(root)
        if reason is not None:
            raise ValueError(reason)

        cached = _lookup_op_ring(current, actor_side=norm_actor, operation_key=clean_op)
        if cached is not None:
            if cached.get("action") != "revise_slow_letter_draft" or cached.get("fingerprint") != fp:
                raise ValueError("OPERATION_FINGERPRINT_MISMATCH")
            snap_draft = cached.get("snapshot") or {}
            live_match = next(
                (
                    d
                    for d in current["slow_letter_drafts"]
                    if d.get("draft_id") == clean_id and d.get("author_side") == norm_actor
                ),
                snap_draft,
            )
            projected = _project_slow_letter_draft_row(live_match, now_dt=now_dt)
            return {
                **projected,
                "draft": projected,
                "duplicate": True,
                "revision": current["revision"],
            }

        target: Optional[dict[str, Any]] = None
        for row in current["slow_letter_drafts"]:
            if row.get("draft_id") == clean_id and row.get("author_side") == norm_actor:
                target = row
                break
        if target is None:
            raise KeyError("DRAFT_NOT_FOUND")

        if new_title is not None:
            target["title"] = new_title
        if new_body is not None:
            target["body"] = new_body
        if new_place is not None:
            target["postmark_place"] = new_place
        target["updated_at"] = now_iso
        # Strictly preserve original drafted_at and cools_until!
        bumped = _bump(current, now_iso)
        _append_op_ring(
            bumped,
            operation_key=clean_op,
            action="revise_slow_letter_draft",
            actor_side=norm_actor,
            fingerprint=fp,
            target_id=clean_id,
            recorded_at=now_iso,
            revision=bumped["revision"],
            snapshot=dict(target),
        )
        saved = write_state(root, bumped)
        projected = _project_slow_letter_draft_row(target, now_dt=now_dt)
        return {
            **projected,
            "draft": projected,
            "duplicate": False,
            "revision": saved["revision"],
        }


def discard_slow_letter_draft(
    root: Path | str,
    *,
    actor_side: Optional[str] = None,
    author_side: Optional[str] = None,
    draft_id: Any = None,
    now: Any = None,
    operation_key: Optional[str] = None,
) -> dict[str, Any]:
    """Discard own cooling slow-letter draft cleanly with zero omission debt."""
    norm_actor = _normalize_actor_side(actor_side, author_side)
    clean_id = _clean_draft_id(draft_id)
    clean_op = _clean_operation_key(operation_key)

    now_dt = parse_instant(now) or datetime.now(tz=timezone(timedelta(hours=8)))
    now_iso = _format_instant_iso(now_dt)
    fp = _op_fingerprint("discard_slow_letter_draft", norm_actor, clean_id)

    with _tx.state_file_lock(state_path(root)):
        current, reason = read_state(root)
        if reason is not None:
            raise ValueError(reason)

        cached = _lookup_op_ring(current, actor_side=norm_actor, operation_key=clean_op)
        if cached is not None:
            if cached.get("action") != "discard_slow_letter_draft" or cached.get("fingerprint") != fp:
                raise ValueError("OPERATION_FINGERPRINT_MISMATCH")
            return {
                "draft_id": clean_id,
                "discarded": True,
                "status": "discarded",
                "omission_debt": 0,
                "duplicate": True,
                "revision": current["revision"],
            }

        kept: list[dict[str, Any]] = []
        matched = False
        for row in current["slow_letter_drafts"]:
            if row.get("draft_id") == clean_id and row.get("author_side") == norm_actor:
                matched = True
            else:
                kept.append(row)
        if not matched:
            raise KeyError("DRAFT_NOT_FOUND")

        current["slow_letter_drafts"] = kept
        bumped = _bump(current, now_iso)
        _append_op_ring(
            bumped,
            operation_key=clean_op,
            action="discard_slow_letter_draft",
            actor_side=norm_actor,
            fingerprint=fp,
            target_id=clean_id,
            recorded_at=now_iso,
            revision=bumped["revision"],
            snapshot={"draft_id": clean_id, "discarded": True, "omission_debt": 0},
        )
        saved = write_state(root, bumped)
        return {
            "draft_id": clean_id,
            "discarded": True,
            "status": "discarded",
            "omission_debt": 0,
            "duplicate": False,
            "revision": saved["revision"],
        }


def post_drafted_slow_letter(
    root: Path | str,
    *,
    actor_side: Optional[str] = None,
    author_side: Optional[str] = None,
    draft_id: Any = None,
    delay_hours: Any = DEFAULT_POSTED_DELAY_HOURS,
    postmark_place: Any = None,
    now: Any = None,
    operation_key: Optional[str] = None,
) -> dict[str, Any]:
    """Post an own cooling draft once ``now >= cools_until`` (``drafted_at + 72h``)."""
    norm_actor = _normalize_actor_side(actor_side, author_side)
    clean_id = _clean_draft_id(draft_id)
    if delay_hours is None:
        raw_delay = float(DEFAULT_POSTED_DELAY_HOURS)
    elif (
        isinstance(delay_hours, bool)
        or not isinstance(delay_hours, (int, float))
        or not math.isfinite(float(delay_hours))
    ):
        raise ValueError("INVALID_SLOW_LETTER_DELAY")
    else:
        raw_delay = float(delay_hours)
    clamped_delay = round(
        max(MIN_POSTED_DELAY_HOURS, min(MAX_POSTED_DELAY_HOURS, raw_delay)), 3
    )
    override_place = (
        _clean_postmark_place(postmark_place) if postmark_place is not None else None
    )
    clean_op = _clean_operation_key(operation_key)

    now_dt = parse_instant(now) or datetime.now(tz=timezone(timedelta(hours=8)))
    sent_iso = _format_instant_iso(now_dt)
    fp = _op_fingerprint(
        "post_drafted_slow_letter", norm_actor, clean_id, clamped_delay, override_place
    )

    with _tx.state_file_lock(state_path(root)):
        current, reason = read_state(root)
        if reason is not None:
            raise ValueError(reason)

        cached = _lookup_op_ring(current, actor_side=norm_actor, operation_key=clean_op)
        if cached is not None:
            if cached.get("action") != "post_drafted_slow_letter" or cached.get("fingerprint") != fp:
                raise ValueError("OPERATION_FINGERPRINT_MISMATCH")
            snap_letter = cached.get("snapshot") or {}
            live_letter = next(
                (
                    item
                    for item in current["slow_letters"]
                    if item.get("letter_id") == cached.get("target_id")
                ),
                snap_letter,
            )
            deliv_dt = parse_instant(live_letter.get("deliver_after"))
            status_now = (
                "in_transit"
                if deliv_dt is not None and now_dt < deliv_dt
                else "delivered"
            )
            return {
                **dict(live_letter),
                "draft_id": clean_id,
                "status": status_now,
                "duplicate": True,
                "revision": current["revision"],
            }

        target_draft: Optional[dict[str, Any]] = None
        kept_drafts: list[dict[str, Any]] = []
        for row in current["slow_letter_drafts"]:
            if row.get("draft_id") == clean_id and row.get("author_side") == norm_actor:
                target_draft = row
            else:
                kept_drafts.append(row)
        if target_draft is None:
            raise KeyError("DRAFT_NOT_FOUND")

        cools_until_str = str(target_draft.get("cools_until") or "")
        cools_dt = parse_instant(cools_until_str)
        if cools_dt is None:
            base_dt = parse_instant(target_draft.get("drafted_at")) or now_dt
            cools_dt = base_dt + timedelta(hours=SLOW_LETTER_COOLING_PERIOD_HOURS)
            cools_until_str = _format_instant_iso(cools_dt)

        if now_dt < cools_dt:
            remaining = max(1, int(math.ceil((cools_dt - now_dt).total_seconds())))
            raise SlowLetterCoolingError(
                cools_until=cools_until_str,
                remaining_cooling_seconds=remaining,
            )

        deliver_dt = now_dt + timedelta(hours=clamped_delay)
        deliver_iso = _format_instant_iso(deliver_dt)
        eff_place = override_place or target_draft["postmark_place"]
        seed = f"{clean_id}\x00{sent_iso}\x00{norm_actor}\x00{clean_op or (current['revision'] + 1)}"
        letter_id = "slow_letter_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        letter_entry = {
            "letter_id": letter_id,
            "title": target_draft["title"],
            "body": target_draft["body"],
            "postmark_place": eff_place,
            "author_side": norm_actor,
            "sent_at": sent_iso,
            "drafted_at": target_draft["drafted_at"],
            "deliver_after": deliver_iso,
            "delay_hours": clamped_delay,
            "acked_at": None,
            "operation_key": clean_op,
        }
        current["slow_letter_drafts"] = kept_drafts
        current["slow_letters"] = [*(current.get("slow_letters") or []), letter_entry][-MAX_PINS:]
        bumped = _bump(current, sent_iso)
        _append_op_ring(
            bumped,
            operation_key=clean_op,
            action="post_drafted_slow_letter",
            actor_side=norm_actor,
            fingerprint=fp,
            target_id=letter_id,
            recorded_at=sent_iso,
            revision=bumped["revision"],
            snapshot=letter_entry,
        )
        saved = write_state(root, bumped)
        status_now = "in_transit" if now_dt < deliver_dt else "delivered"
        return {
            **letter_entry,
            "draft_id": clean_id,
            "status": status_now,
            "duplicate": False,
            "revision": saved["revision"],
        }


# ---------------------------------------------------------------------------------------------
# window: world plants / light / personal space / template soundscape / no fabricated weather
# ---------------------------------------------------------------------------------------------


def _plant_rows(
    world_result: Mapping[str, Any],
    *,
    neighborhood_context: Optional[Mapping[str, Any]] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
    environment = data.get("local_environment") \
        if isinstance(data.get("local_environment"), Mapping) else {}
    area = environment.get("area") if isinstance(environment.get("area"), Mapping) else {}
    location = data.get("location") if isinstance(data.get("location"), Mapping) else {}
    area_id = area.get("area_id") or location.get("area_id")
    if environment.get("status") != "available":
        return [], {"status": str(environment.get("status") or "unavailable"),
                    "reason_code": str(environment.get("reason_code")
                                       or "LOCAL_ENVIRONMENT_UNAVAILABLE"),
                    "source": "world_package_scene"}
    nb_plant = (
        neighborhood_context.get("plant")
        if isinstance(neighborhood_context, Mapping)
        and isinstance(neighborhood_context.get("plant"), Mapping)
        and neighborhood_context["plant"].get("available")
        else None
    )
    plants: list[dict[str, Any]] = []
    for raw in environment.get("furniture") or ():
        if not isinstance(raw, Mapping) or str(raw.get("kind") or "") != "plant":
            continue
        object_id = raw.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            continue
        if nb_plant is not None:
            growth_block: dict[str, Any] = {
                "value": nb_plant.get("stage"),
                "stage": nb_plant.get("stage"),
                "growth_points": nb_plant.get("growth_points"),
                "reason_code": None,
                "source": V04_NEIGHBORHOOD_SOURCE,
            }
            moisture_block: dict[str, Any] = {
                "hydration": nb_plant.get("hydration"),
                "wet_dry_state": nb_plant.get("wet_dry_state"),
                "soil_state": nb_plant.get("soil_state"),
                "needs_watering": bool(nb_plant.get("needs_watering", False)),
                "last_watered_at": nb_plant.get("last_watered_at"),
                "never_dies_offline": bool(nb_plant.get("never_dies_offline", True)),
                "source": V04_NEIGHBORHOOD_SOURCE,
            }
        else:
            # 分类/身份/摆位来自世界包；无 v04_neighborhood_living 状态时不推生长与干湿。
            growth_block = {"value": None, "reason_code": NO_PLANT_GROWTH_AUTHORITY}
            moisture_block = {
                "hydration": None,
                "wet_dry_state": None,
                "soil_state": None,
                "needs_watering": None,
                "reason_code": NO_PLANT_GROWTH_AUTHORITY,
                "source": "none",
            }
        plants.append({
            "object_id": object_id,
            "name": _one_line(raw.get("name"), 80) or None,
            "kind": "plant",
            "area_id": area_id,
            "placement_area_source": "world_package_scene",
            "growth": growth_block,
            "moisture": moisture_block,
            "source": "world_package_scene",
        })
    if not plants:
        return [], {"status": "none", "reason_code": NO_DECLARED_HOME_PLANT,
                    "source": "world_package_scene"}
    return plants, {
        "status": "available",
        "reason_code": None,
        "count": len(plants),
        "source": "world_package_scene",
        "moisture_authority": V04_NEIGHBORHOOD_SOURCE if nb_plant is not None else None,
    }


def _light_block(world_result: Mapping[str, Any]) -> dict[str, Any]:
    data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
    environment = data.get("local_environment") \
        if isinstance(data.get("local_environment"), Mapping) else {}
    light = environment.get("light") if isinstance(environment.get("light"), Mapping) else {}
    return {
        "status": str(light.get("status") or "unknown"),
        "reason_code": light.get("reason_code") or None,
        "power": light.get("power"),
        "band": light.get("band"),
        "declared_source": light.get("declared_source"),
        "fixture_ids": list(light.get("fixture_ids") or []),
        "source": "world_room_environment",
        "derived_from_clock": False,
    }


def _personal_space_block(observation: Mapping[str, Any]) -> dict[str, Any]:
    status = str(observation.get("status") or "unavailable")
    if status != "available":
        return {"available": False, "status": status,
                "reason_code": str(observation.get("reason_code") or "HOME_SPACE_UNAVAILABLE"),
                "source": "world_home_space.home_personal_space.json"}
    details = {row.get("field"): row for row in (observation.get("personal_details") or ())
               if isinstance(row, Mapping)}
    spaces = {row.get("space_id"): row for row in (observation.get("spaces") or ())
              if isinstance(row, Mapping)}
    bedroom = spaces.get("bedroom") if isinstance(spaces.get("bedroom"), Mapping) else {}
    facts = bedroom.get("facts") if isinstance(bedroom.get("facts"), Mapping) else {}

    def fact(name: str) -> dict[str, Any]:
        raw = facts.get(name) if isinstance(facts.get(name), Mapping) else {}
        if raw.get("status") == "SET_BY_SELF" and isinstance(raw.get("value"), str):
            return {"status": "SET_BY_SELF", "value": raw.get("value"),
                    "revision": _int_or_none(raw.get("revision"))}
        return {"status": str(raw.get("status") or "UNSET"), "value": None,
                "reason_code": "UNDECIDED_BY_SELF"}

    seating = details.get("courtyard.seating") if isinstance(details.get("courtyard.seating"),
                                                            Mapping) else None
    yard = ({"status": "SET_BY_SELF", "value": seating.get("value")}
            if seating is not None else
            {"status": "UNSET", "value": None, "reason_code": "UNDECIDED_BY_SELF"})
    window_orientation = fact("window_orientation")
    window_view = fact("window_view")
    undecided = [str(row.get("field")) for row in (observation.get("undecided_fields") or ())
                 if isinstance(row, Mapping)]
    return {
        "available": True,
        "status": "available",
        "reason_code": None,
        "window": {"orientation": window_orientation, "view": window_view},
        "yard_seating": yard,
        "undecided_fields": undecided[:10],
        "source": "world_home_space.home_personal_space.json",
    }


def weather_block(
    neighborhood_context: Optional[Mapping[str, Any]] = None,
    *,
    day_root: Optional[Path | str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """只读投影 ``v04_neighborhood_living`` 真实天气；无该权威状态时如实返回 ``null``。"""
    ctx = neighborhood_context
    if ctx is None and day_root is not None:
        ctx = read_v04_neighborhood_window_context(day_root, now_iso=now_iso)
    if isinstance(ctx, Mapping) and isinstance(ctx.get("weather"), Mapping):
        w = dict(ctx["weather"])
        if w.get("value"):
            return w
    return {"value": None, "status": "unavailable",
            "reason_code": NO_WEATHER_AUTHORITY, "source": "none",
            "is_weather_authority": False}


def soundscape_unavailable(reason_code: str, **extra: Any) -> dict[str, Any]:
    block = {"available": False, "events": [], "count": 0,
             "reason_code": str(reason_code), "source": "world_ambient_events",
             "template_projection": True, "is_weather": False}
    block.update(extra)
    return block


def soundscape_available(events: list[Mapping[str, Any]], *, local_date: Any = None,
                         local_time: Any = None, clock_source: Any = None
                         ) -> dict[str, Any]:
    rows = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        rows.append({
            "event_id": _one_line(event.get("event_id"), 80) or None,
            "event_type": _one_line(event.get("event_type"), 80) or None,
            "location_id": _one_line(event.get("location_id"), 40) or None,
            "summary": _one_line((event.get("payload") or {}).get("summary")
                                 if isinstance(event.get("payload"), Mapping)
                                 else event.get("summary"), 240) or None,
            "valid_from": _one_line(event.get("valid_from"), 40) or None,
            "valid_until": _one_line(event.get("valid_until"), 40) or None,
        })
    return {"available": True, "events": rows, "count": len(rows),
            "reason_code": None, "source": "world_ambient_events",
            "template_projection": True, "is_weather": False,
            "local_date": local_date, "local_time": local_time,
            "clock_source": clock_source or "world_snapshot"}


def window_block(
    *,
    world_result: Mapping[str, Any],
    home_observation: Mapping[str, Any],
    soundscape: Mapping[str, Any],
    day_root: Optional[Path | str] = None,
    neighborhood_context: Optional[Mapping[str, Any]] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
    location = data.get("location") if isinstance(data.get("location"), Mapping) else {}
    world_data = data.get("world") if isinstance(data.get("world"), Mapping) else {}
    eff_now_iso = now_iso or (
        str(world_data.get("local_time"))
        if isinstance(world_data.get("local_time"), str) and world_data.get("local_time")
        else None
    )
    nb_ctx = (
        dict(neighborhood_context)
        if isinstance(neighborhood_context, Mapping)
        else read_v04_neighborhood_window_context(day_root, now_iso=eff_now_iso)
    )
    world_ok = bool(world_result.get("ok"))
    at_home = bool(world_ok and location.get("status") == "available"
                   and str(location.get("place_id") or "") == "home")
    plants, plant_status = _plant_rows(world_result, neighborhood_context=nb_ctx)
    nb_plant = nb_ctx.get("plant") if isinstance(nb_ctx, Mapping) and isinstance(nb_ctx.get("plant"), Mapping) else None
    windowsill_plant = (
        dict(nb_plant)
        if nb_plant is not None and nb_plant.get("available")
        else {
            "available": False,
            "wet_dry_state": None,
            "soil_state": None,
            "hydration": None,
            "stage": None,
            "reason_code": NO_PLANT_GROWTH_AUTHORITY,
            "source": "none",
        }
    )
    return {
        "schema": WINDOW_SCHEMA,
        "read_only": True,
        "at_home": at_home,
        "location": {
            "available": bool(world_ok and location.get("status") == "available"),
            "place_id": location.get("place_id"),
            "area_id": location.get("area_id"),
            "home": location.get("home"),
            "reason_code": None if world_ok
            else str(world_result.get("reason_code") or "WORLD_UNAVAILABLE"),
        },
        "plants": plants,
        "plants_status": plant_status,
        "windowsill_plant": windowsill_plant,
        "light": _light_block(world_result),
        "window": _personal_space_block(home_observation),
        "soundscape": dict(soundscape),
        "weather": weather_block(nb_ctx),
        "private_text_included": False,
        "local_only": True,
        "note": "plant 按世界包声明对象/摆位投影，当存在 v04_neighborhood_living 权威状态时只读联动真实天气与窗台绿植干湿状态；"
                "soundscape 是当天 home ambient 模板声景，不是气象；没有气象权威时 weather 明确为 null。",
    }


# ---------------------------------------------------------------------------------------------
# keepsakes output: references only; the owner still owns every byte
# ---------------------------------------------------------------------------------------------


def keepsakes_block(*, state: Mapping[str, Any], state_reason: Optional[str],
                    lockscreen: Optional[Mapping[str, Any]],
                    pins: list[Mapping[str, Any]], subject_view: bool) -> dict[str, Any]:
    current = state.get("lockscreen") or {}
    selected = bool(current.get("album_artifact_id"))
    if lockscreen is None:
        lock_out: dict[str, Any] = {
            "status": "none", "has_lockscreen": False, "reason_code": None}
    else:
        lock_out = {
            "status": str(lockscreen.get("status") or "unavailable"),
            "has_lockscreen": selected,
            "reason_code": lockscreen.get("reason_code") or None,
        }
        if subject_view:
            lock_out["album_artifact_id"] = lockscreen.get("album_artifact_id")
            lock_out["caption"] = _one_line(lockscreen.get("caption"), MAX_CAPTION) or None
            lock_out["content_type"] = lockscreen.get("content_type")
            lock_out["image_surface"] = (
                "/api/phone/album/%s/image" % lockscreen.get("album_artifact_id")
                if lockscreen.get("album_artifact_id") else None)
    lock_out["updated_at"] = _iso_text(current.get("selected_at")) \
        or _iso_text(state.get("updated_at"))

    pin_rows: list[dict[str, Any]] = []
    for row in pins:
        entry: dict[str, Any] = {
            "share_id": row.get("share_id"),
            "status": str(row.get("status") or "withheld"),
            "reason_code": row.get("reason_code") or None,
            "pinned_at": _iso_text(row.get("pinned_at")),
            "delivered_at": row.get("delivered_at"),
            "state": row.get("state"),
            "text_withheld": bool(row.get("text_withheld", row.get("status") != "available")),
        }
        if not entry["text_withheld"] and row.get("text") is not None:
            entry["text"] = _bounded_body(row.get("text"), MAX_PIN_TEXT)
            entry["text_chars"] = _int_or_none(row.get("text_chars"))
        pin_rows.append(entry)
    return {
        "schema": KEEPSAKES_SCHEMA,
        "read_only": False,
        "view": "subject" if subject_view else "partner_default",
        "state": {"revision": _int_or_none(state.get("revision")),
                  "updated_at": state.get("updated_at"),
                  "reason_code": state_reason},
        "lockscreen": lock_out,
        "fridge_pins": pin_rows,
        "fridge_pin_count": len(pin_rows),
        "private_text_included": bool(
            any(not row["text_withheld"] for row in pin_rows)
            or (subject_view and lock_out.get("caption"))),
        "local_only": True,
        "note": "锁屏/冰箱贴只保存真实引用与时间；图片字节、相册路径、caption 默认不返回，"
                "正文仍由原 owner 现读。",
    }


# ---------------------------------------------------------------------------------------------
# letters: a cross-day projection of a share's pinned source-version timeline
# ---------------------------------------------------------------------------------------------


def clock_offset(clock: Mapping[str, Any]) -> str:
    """The business clock's own offset; unreadable clock falls back to +08:00 and says so."""

    value = clock.get("logical_at") if isinstance(clock, Mapping) else None
    moment = parse_instant(value)
    if moment is None:
        return "+08:00"
    return moment.isoformat(timespec="seconds")[-6:]


def parse_instant(value: Any) -> Optional[datetime]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _zone(offset: str):
    try:
        return datetime.fromisoformat("2000-01-01T00:00:00" + offset).tzinfo or timezone.utc
    except (TypeError, ValueError):
        return timezone.utc


def local_day(value: Any, offset: str) -> Optional[str]:
    moment = parse_instant(value)
    if moment is None:
        return None
    return moment.astimezone(_zone(offset)).date().isoformat()


def writing_days(entries: list[Mapping[str, Any]], *, cutoff_version_number: int,
                 offset: str) -> dict[str, Any]:
    """Distinct owner-timestamp local days among versions at or before the pinned version.

    ``cutoff_version_number`` is the bound share's own source version.  A newer committed
    version is never counted: the delivered draft cannot grow after it was delivered.
    """

    days: set[str] = set()
    considered = 0
    excluded_after = 0
    undated = 0
    unbounded = 0
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        number = _int_or_none(entry.get("version_number"))
        if number is None:
            unbounded += 1
            continue
        if number > cutoff_version_number:
            excluded_after += 1
            continue
        considered += 1
        day = local_day(entry.get("created_at"), offset)
        if day is None:
            undated += 1
            continue
        days.add(day)
    ordered = sorted(days)
    first = ordered[0] if ordered else None
    last = ordered[-1] if ordered else None
    elapsed = None
    if first and last:
        elapsed = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
    return {
        "first_written_day": first,
        "last_written_day": last,
        "writing_days": len(ordered),
        "elapsed_days_inclusive": elapsed,
        "versions_considered": considered,
        "versions_after_cutoff_excluded": excluded_after,
        "undated_versions": undated,
        "unbounded_versions": unbounded,
        "timezone_offset": offset,
        "date_basis": "owner_timestamp_localized",
        "historical_business_day_provable": False,
    }


def letter_row(*, share_row: Mapping[str, Any], timeline: Mapping[str, Any],
               history_complete: bool, history_reason: str,
               text: Optional[str], delivered_at: Any, status: str,
               reason_code: Optional[str]) -> dict[str, Any]:
    return {
        "share_id": share_row.get("share_id"),
        "state": share_row.get("state"),
        "delivered": bool(share_row.get("delivered")),
        "status": status,
        "reason_code": reason_code,
        "postmark": {
            "first_written_day": timeline.get("first_written_day"),
            "last_written_day": timeline.get("last_written_day"),
            "writing_days": timeline.get("writing_days"),
            "elapsed_days_inclusive": timeline.get("elapsed_days_inclusive"),
            "delivered_at": delivered_at,
            "timezone_offset": timeline.get("timezone_offset"),
            "date_basis": timeline.get("date_basis"),
            "historical_business_day_provable": False,
            "note": "owner 版本 created_at 按业务时钟偏移本地化；是否等于压缩业务日不可证明",
        },
        "versions_considered": timeline.get("versions_considered"),
        "versions_after_cutoff_excluded": timeline.get("versions_after_cutoff_excluded"),
        "bounded": {
            "history_complete": bool(history_complete),
            "history_reason_code": history_reason or None,
            "undated_versions": timeline.get("undated_versions"),
            "unbounded_versions": timeline.get("unbounded_versions"),
            "date_completeness": (
                "complete" if not timeline.get("undated_versions")
                and not timeline.get("unbounded_versions") else "partial"),
        },
        "text_withheld": text is None,
        "text": _bounded_body(text, MAX_LETTER_TEXT) if text is not None else None,
        "text_chars": len(text) if isinstance(text, str) else None,
        "source_work_body_included": False,
        "projection_only": True,
        "letter_type": None,
    }


def _read_slow_letters_and_drafts_zero_write(
    *,
    root: Optional[Path | str] = None,
    state: Optional[Mapping[str, Any]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if isinstance(state, Mapping):
        raw_letters = state.get("slow_letters")
        raw_drafts = state.get("slow_letter_drafts")
        letters = (
            [dict(x) for x in raw_letters if isinstance(x, Mapping)]
            if isinstance(raw_letters, list)
            else []
        )
        drafts = (
            [dict(x) for x in raw_drafts if isinstance(x, Mapping)]
            if isinstance(raw_drafts, list)
            else []
        )
        return letters, drafts
    candidates: list[Path] = []
    if root is not None:
        candidates.append(Path(root))
    else:
        for env_key in ("CHIYO_DAY_ROOT", "CHIYO_INSTANCE_ROOT"):
            env_val = os.environ.get(env_key, "").strip()
            if env_val:
                candidates.append(Path(env_val))
        try:
            import sys as _sys

            caller_frame = _sys._getframe(2)
            caller_self = caller_frame.f_locals.get("self") if caller_frame is not None else None
            caller_root = getattr(caller_self, "root", None)
            if isinstance(caller_root, (str, Path)):
                candidates.append(Path(caller_root))
        except Exception:
            pass
    for cand in candidates:
        sp = state_path(cand)
        try:
            if not sp.is_symlink() and sp.is_file():
                st_doc, reason = read_state(cand)
                if reason is None:
                    letters = [
                        dict(x)
                        for x in (st_doc.get("slow_letters") or [])
                        if isinstance(x, Mapping)
                    ]
                    drafts = [
                        dict(x)
                        for x in (st_doc.get("slow_letter_drafts") or [])
                        if isinstance(x, Mapping)
                    ]
                    return letters, drafts
                return [], []
        except OSError:
            continue
    return [], []


def _read_slow_letters_zero_write(
    *,
    root: Optional[Path | str] = None,
    state: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    letters, _drafts = _read_slow_letters_and_drafts_zero_write(root=root, state=state)
    return letters


def _project_slow_letter_row(
    raw: Mapping[str, Any],
    *,
    now_dt: datetime,
    offset: str,
    subject_view: bool,
) -> dict[str, Any]:
    deliver_after_str = _iso_text(raw.get("deliver_after"))
    sent_at_str = _iso_text(raw.get("sent_at"))
    drafted_at_str = _iso_text(raw.get("drafted_at")) or sent_at_str
    deliver_dt = parse_instant(deliver_after_str)
    is_in_transit = bool(deliver_dt is not None and now_dt < deliver_dt)
    status = "in_transit" if is_in_transit else "delivered"
    acked_at = _iso_text(raw.get("acked_at"))
    place = _one_line(raw.get("postmark_place"), 80) or "镇上邮筒"
    title = _one_line(raw.get("title"), 120) or "慢信"
    author_side = str(raw.get("author_side") or "subject")
    viewer_side = "subject" if subject_view else "partner"
    withhold_body = bool(is_in_transit and viewer_side != author_side)
    raw_body = _bounded_body(raw.get("body"), MAX_LETTER_TEXT) if not withhold_body else None
    return {
        "letter_id": str(raw.get("letter_id") or ""),
        "title": title,
        "author_side": author_side,
        "status": status,
        "in_transit": is_in_transit,
        "delivered": not is_in_transit,
        "acked": bool(acked_at) and not is_in_transit,
        "acked_at": acked_at if not is_in_transit else None,
        "drafted_at": drafted_at_str,
        "sent_at": sent_at_str,
        "deliver_after": deliver_after_str,
        "estimated_delivery_at": deliver_after_str,
        "delivered_at": None if is_in_transit else deliver_after_str,
        "postmark_place": place,
        "postmark": {
            "place": place,
            "drafted_at": drafted_at_str,
            "sent_at": sent_at_str,
            "sent_day": local_day(sent_at_str, offset),
            "deliver_after": deliver_after_str,
            "delivered_at": None if is_in_transit else deliver_after_str,
            "status": status,
            "timezone_offset": offset,
        },
        "text_withheld": withhold_body,
        "body": raw_body,
        "text": raw_body,
        "text_chars": len(raw_body) if isinstance(raw_body, str) else None,
        "letter_type": "slow_postmark_letter",
    }


def letters_block(*, rows: list[Mapping[str, Any]], offset: str,
                  clock: Mapping[str, Any], subject_view: bool, scanned: int,
                  truncated: bool, unprovable: int = 0,
                  root: Optional[Path | str] = None,
                  state: Optional[Mapping[str, Any]] = None,
                  now: Any = None) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (str((row.get("postmark") or {})
                                               .get("last_written_day") or ""),
                                            str(row.get("share_id") or "")), reverse=True)
    now_dt = (
        parse_instant(now)
        or parse_instant((clock or {}).get("logical_at") if isinstance(clock, Mapping) else None)
        or datetime.now(tz=timezone(timedelta(hours=8)))
    )
    viewer_side = "subject" if subject_view else "partner"
    slow_raw, drafts_raw = _read_slow_letters_and_drafts_zero_write(root=root, state=state)
    slow_projected = [
        _project_slow_letter_row(item, now_dt=now_dt, offset=offset, subject_view=subject_view)
        for item in slow_raw
    ]
    slow_projected.sort(key=lambda r: (str(r.get("sent_at") or ""), str(r.get("letter_id") or "")), reverse=True)
    in_transit_rows = [r for r in slow_projected if r["in_transit"]]
    delivered_slow_rows = [r for r in slow_projected if r["delivered"]]

    # Strict bilateral privacy: ONLY project the caller's own cooling drafts; never inspect or count the other side's.
    own_drafts = [
        _project_slow_letter_draft_row(item, now_dt=now_dt)
        for item in drafts_raw
        if str(item.get("author_side") or "") == viewer_side
    ]
    own_drafts.sort(
        key=lambda r: (str(r.get("drafted_at") or ""), str(r.get("draft_id") or "")),
        reverse=True,
    )

    bounded_ordered = ordered[:MAX_LETTERS]
    inbox_items: list[dict[str, Any]] = list(delivered_slow_rows)
    for row in bounded_ordered:
        if not row.get("text_withheld") and row.get("delivered"):
            inbox_items.append(dict(row))

    return {
        "schema": LETTERS_SCHEMA,
        "read_only": True,
        "view": "subject" if subject_view else "partner_default",
        "letters": bounded_ordered,
        "count": len(bounded_ordered),
        "items": inbox_items[:MAX_LETTERS],
        "delivered": delivered_slow_rows[:MAX_LETTERS],
        "delivered_count": len(delivered_slow_rows),
        "in_transit": in_transit_rows[:MAX_LETTERS],
        "in_transit_count": len(in_transit_rows),
        "slow_letters": slow_projected[:MAX_LETTERS],
        "own_slow_letter_drafts": own_drafts,
        "own_slow_letter_draft_count": len(own_drafts),
        "truncated": bool(truncated or len(ordered) > MAX_LETTERS),
        "scanned_shares": scanned,
        #: 时间戳缺失/版本不可界定的候选：不冒充成「不是信」，只如实说无法证明跨日。
        "unprovable_candidates": int(unprovable),
        "date_basis": "owner_timestamp_localized",
        "historical_business_day_provable": False,
        "timezone_offset": offset,
        "clock": {"source": clock.get("source"), "logical_at": clock.get("logical_at"),
                  "reason_code": clock.get("reason_code")},
        "letter_type_is_not_a_store": True,
        "private_text_included": (
            any(not row.get("text_withheld") for row in ordered)
            or any(not row.get("text_withheld") for row in slow_projected)
            or bool(own_drafts and subject_view)
        ),
        "local_only": True,
        "note": "这是跨 >=2 个 owner 时间戳本地日的作品/分享投影，以及带在途邮戳延迟的慢信纯读派生；"
                "只统计到分享绑定的 source version，不把投递后的新版本算进来。",
    }


__all__ = [
    "ACTION_ALLOWED_FIELDS",
    "DEFAULT_POSTED_DELAY_HOURS",
    "LETTERS_SCHEMA",
    "LETTER_SHARE_STATES",
    "LOCAL_CHANNEL",
    "LOCAL_DELIVERED",
    "LOCAL_AUDIENCE",
    "KEEPSAKES_SCHEMA",
    "MAX_COMMITTED_OP_RING",
    "MAX_HISTORY",
    "MAX_LETTERS",
    "MAX_LETTER_TEXT",
    "MAX_PIN_TEXT",
    "MAX_POSTED_DELAY_HOURS",
    "MAX_SHARE_SCAN",
    "MAX_SLOW_LETTER_DRAFTS_PER_AUTHOR",
    "MIN_POSTED_DELAY_HOURS",
    "NO_DECLARED_HOME_PLANT",
    "NO_PLANT_GROWTH_AUTHORITY",
    "NO_WEATHER_AUTHORITY",
    "NOT_AT_HOME",
    "SLOW_LETTER_COOLING_PERIOD",
    "SLOW_LETTER_COOLING_PERIOD_HOURS",
    "SLOW_LETTER_COOLING_SECONDS",
    "SLOW_LETTER_DRAFT_ACTIONS",
    "STATE_FILENAME",
    "STATE_SCHEMA_VERSION",
    "SlowLetterCoolingError",
    "WINDOW_SCHEMA",
    "ack_slow_letter",
    "clear_lockscreen",
    "clock_offset",
    "discard_slow_letter_draft",
    "empty_state",
    "keepsakes_block",
    "letter_row",
    "letters_block",
    "local_day",
    "parse_instant",
    "pin_share",
    "post_drafted_slow_letter",
    "post_slow_letter",
    "read_state",
    "read_v04_neighborhood_window_context",
    "revise_slow_letter_draft",
    "save_slow_letter_draft",
    "select_lockscreen",
    "soundscape_available",
    "soundscape_unavailable",
    "state_path",
    "unpin_share",
    "weather_block",
    "window_block",
    "write_state",
    "writing_days",
]

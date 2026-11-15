#!/usr/bin/env python3
"""具身冷暖干湿 / 窗扇与外衣 / 桌边温饮靠垫：一个纯投影 + 一份显式仿真状态。

它是什么
--------
四层分开（设计 33/34/37）：

1. **世界环境物件**：真实 ``PhoneWorld.snapshot()`` 的当前 place/area、灯光、
   当天 ambient 模板声景；
2. **Body 姿态负荷**：真实 Body 的 pose / recovery / needs 档位 / signals /
   rest_evidence（只投影档位，不出现裸浮点）；
3. **有界感觉投影**：本模块只给有限四类 ``thermal/moisture/posture/tactile``，
   每类带有限 quality/salience/region/source_refs；没有证据就是 unknown/无信号；
4. **认知解释选择**：不在本模块。这里不声称主观感受、不把报错/延迟/额度映射疼痛，
   也不写 Body。

窗扇开关、外衣穿挂、局部位置、桌边温饮/靠垫是**本实例的局部仿真条件**，
没有 World owner 字段可复用，统一标 ``phone_slice_simulation``。它们不是世界测量：
不复制 Body/世界位置，不另建动作或权限账本，只记本单实际条件、局部位置引用、
revision 与时间。

它不是什么
----------
* 不因卧室名字就说窗边；只有本人显式声明 ``window_side`` 且绑定同一 place/area，
  才可能有窗侧 cool 信号。
* 不把 ambient 雨声当淋湿证据；微潮只来自本人显式申报（``self_declared_simulation``）。
* 不把温饮说成已经喝下；拿起与真实饮用分开，饮用只走既有 owner 的 ``CONSUME``
  通道，重放不二次摄入。
* 照顾不涨关系、不消解争执、不授权亲密、不劫持 Activity/阅读断点。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

STATE_SCHEMA_VERSION = 1
STATE_DIRNAME = "phone"
STATE_FILENAME = "somatic_comfort.json"

SOMATIC_SCHEMA = "phone.body.somatic.v1"
GARMENT_WINDOW_SCHEMA = "phone.body.garment_window.v1"
CARE_SCHEMA = "phone.body.care_offering.v1"

SIMULATION_SOURCE = "phone_slice_simulation"
SELF_DECLARED_SOURCE = "self_declared_simulation"
WORLD_READ_SOURCE = "world_phone_world.snapshot"
BODY_SIGNAL_SOURCE = "world_phone_world.snapshot.body"

MODALITIES = ("thermal", "moisture", "posture", "tactile")
SALIENCE = ("none", "low", "medium", "high")
WINDOW_STATES = ("open", "ajar", "closed")
LOCAL_ZONES = ("window_side", "desk_side", "elsewhere", "unknown")
GARMENT_STATES = ("wear", "hang")
DRYNESS_STATES = ("dry", "damp", "unknown")
CARE_SLOTS = ("drink", "cushion")
DRINK_KINDS = ("warm_water", "hot_tea")
CUSHION_KINDS = ("floor_cushion",)
CARE_ACTORS = ("her", "partner")
CARE_POSITIONS = ("hand_side", "desk_side")
DRINK_STATES = ("available", "taken", "consumed", "stowed", "withdrawn")
CUSHION_STATES = ("available", "in_use", "stowed", "withdrawn")
TEMPERATURE_BANDS = ("warm", "tepid", "room_temp")

#: 固定有界速率：挂起来的微潮外衣 6 小时确定性干燥；温饮 30 分钟回室温。
#: 两条都只按 elapsed 派生，不落盘、不刷新、不因聊天结束瞬变。
DRYING_SECONDS = 6 * 3600
COOLING_SECONDS = 30 * 60

MAX_LABEL = 40
MAX_GARMENT_ID = 80
MAX_HANG_POSITION = 40
MAX_OFFER_ID = 160

NO_WEATHER_AUTHORITY = "NO_WEATHER_AUTHORITY"
AMBIENT_IS_TEMPLATE = "AMBIENT_IS_TEMPLATE_NOT_WETNESS_EVIDENCE"
NO_POSTURE_DURATION = "NO_POSTURE_DURATION_EVIDENCE"
LOCATION_UNREADABLE = "LOCAL_POSITION_UNREADABLE"
LOCATION_UNSTABLE = "LOCAL_POSITION_UNSTABLE"


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _optional_text(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def parse_instant(value: Any) -> Optional[datetime]:
    """Read one owner/local timestamp; an unreadable value stays ``None`` (never guessed)."""

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


def _iso(value: Any) -> Optional[str]:
    moment = parse_instant(value)
    return moment.isoformat(timespec="seconds") if moment is not None else None


def _elapsed_seconds(start: Any, now: Any) -> Optional[float]:
    started = parse_instant(start)
    moment = parse_instant(now)
    if started is None or moment is None:
        return None
    return (moment - started).total_seconds()


# ---------------------------------------------------------------------------------------------
# the one local simulation state
# ---------------------------------------------------------------------------------------------


def state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / STATE_FILENAME


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "window": {"state": None, "updated_at": None},
        "local_position": {"zone": None, "bound_place_id": None,
                           "bound_area_id": None, "declared_at": None},
        "garment": {"garment_id": None, "label": None, "state": None,
                    "dryness": None, "dampness_source": None,
                    "hang_position": None, "drying": None},
        "care": {"drink": None, "cushion": None},
    }


def _validate_drying(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("invalid drying")
    started_at = _iso(raw.get("started_at"))
    total = _int_or_none(raw.get("total_seconds"))
    if started_at is None or total is None or total <= 0:
        raise ValueError("invalid drying")
    return {"started_at": started_at, "total_seconds": int(total)}


def _validate_offer(raw: Any, slot: str) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("invalid offer")
    states = DRINK_STATES if slot == "drink" else CUSHION_STATES
    offer: dict[str, Any] = {
        "slot": slot,
        "offer_id": _optional_text(raw.get("offer_id"), MAX_OFFER_ID),
        "kind": _optional_text(raw.get("kind"), 40),
        "actor_side": _optional_text(raw.get("actor_side"), 20),
        "position": _optional_text(raw.get("position"), 20),
        "bound_place_id": _optional_text(raw.get("bound_place_id"), 80),
        "bound_area_id": _optional_text(raw.get("bound_area_id"), 80),
        "offered_at": _iso(raw.get("offered_at")),
        "state": str(raw.get("state") or ""),
        "taken_at": _iso(raw.get("taken_at")),
        "used_at": _iso(raw.get("used_at")),
        "closed_at": _iso(raw.get("closed_at")),
    }
    if offer["offer_id"] is None or offer["kind"] is None or offer["actor_side"] is None \
            or offer["position"] is None or offer["state"] not in states:
        raise ValueError("invalid offer")
    if offer["bound_place_id"] is None or offer["bound_area_id"] is None:
        raise ValueError("invalid offer binding")
    if slot == "drink":
        offered = parse_instant(offer["offered_at"])
        if offered is None:
            raise ValueError("invalid drink offer time")
        offer["temperature"] = {
            "placed_at": offered.isoformat(timespec="seconds"),
            "initial": "warm",
            "final": "room_temp",
            "cooling_seconds": COOLING_SECONDS,
        }
        offer["bound_item_instance_id"] = _optional_text(
            raw.get("bound_item_instance_id"), 160)
        offer["bound_item_label"] = _optional_text(raw.get("bound_item_label"), MAX_LABEL)
        offer["drink_operation_key"] = _optional_text(raw.get("drink_operation_key"), 160)
        offer["receipt_ref"] = _optional_text(raw.get("receipt_ref"), 160)
        offer["consumed_at"] = _iso(raw.get("consumed_at"))
        offer["pending_unknown"] = bool(raw.get("pending_unknown"))
    return offer


def _validate_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("state is not an object")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported schema")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")
    window_raw = raw.get("window") if isinstance(raw.get("window"), Mapping) else {}
    window_state = window_raw.get("state")
    if window_state is not None and window_state not in WINDOW_STATES:
        raise ValueError("invalid window state")
    position_raw = raw.get("local_position") \
        if isinstance(raw.get("local_position"), Mapping) else {}
    zone = position_raw.get("zone")
    if zone is not None and zone not in LOCAL_ZONES:
        raise ValueError("invalid local zone")
    garment_raw = raw.get("garment") \
        if isinstance(raw.get("garment"), Mapping) else {}
    garment_state = garment_raw.get("state")
    if garment_state is not None and garment_state not in GARMENT_STATES:
        raise ValueError("invalid garment state")
    dryness = garment_raw.get("dryness")
    if dryness is not None and dryness not in DRYNESS_STATES:
        raise ValueError("invalid garment dryness")
    care_raw = raw.get("care") if isinstance(raw.get("care"), Mapping) else {}
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": int(revision),
        "updated_at": _iso(raw.get("updated_at")),
        "window": {
            "state": window_state,
            "updated_at": _iso(window_raw.get("updated_at")),
        },
        "local_position": {
            "zone": zone,
            "bound_place_id": _optional_text(position_raw.get("bound_place_id"), 80),
            "bound_area_id": _optional_text(position_raw.get("bound_area_id"), 80),
            "declared_at": _iso(position_raw.get("declared_at")),
        },
        "garment": {
            "garment_id": _optional_text(garment_raw.get("garment_id"), MAX_GARMENT_ID),
            "label": _optional_text(garment_raw.get("label"), MAX_LABEL),
            "state": garment_state,
            "dryness": dryness,
            "dampness_source": _optional_text(garment_raw.get("dampness_source"), 40),
            "hang_position": _optional_text(garment_raw.get("hang_position"),
                                            MAX_HANG_POSITION),
            "drying": _validate_drying(garment_raw.get("drying")),
        },
        "care": {
            "drink": _validate_offer(care_raw.get("drink"), "drink"),
            "cushion": _validate_offer(care_raw.get("cushion"), "cushion"),
        },
    }


def read_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    """Read the one local file.  Missing = empty; corrupt = fail closed, never guessed."""

    path = state_path(root)
    if path.is_symlink():
        return empty_state(), "SOMATIC_STATE_SYMLINK"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state(), None
    except (OSError, ValueError):
        return empty_state(), "SOMATIC_STATE_UNREADABLE"
    try:
        return _validate_state(raw), None
    except ValueError:
        return empty_state(), "SOMATIC_STATE_INVALID"


def write_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _validate_state(dict(state))
    path = state_path(root)
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
    out["updated_at"] = _iso(now_iso)
    return out


# ---------------------------------------------------------------------------------------------
# window / local position / outerwear
# ---------------------------------------------------------------------------------------------


def set_window(state: Mapping[str, Any], value: Any,
               now_iso: str) -> tuple[dict[str, Any], bool, Optional[str]]:
    if not isinstance(value, str) or value not in WINDOW_STATES:
        return _validate_state(dict(state)), False, "WINDOW_STATE_INVALID"
    current = _validate_state(dict(state))
    if current["window"]["state"] == value:
        return current, False, None
    current["window"] = {"state": value, "updated_at": _iso(now_iso)}
    return _bump(current, now_iso), True, None


def set_local_position(state: Mapping[str, Any], *, zone: Any, place_id: Any, area_id: Any,
                       now_iso: str) -> tuple[dict[str, Any], bool, Optional[str]]:
    if not isinstance(zone, str) or zone not in LOCAL_ZONES or zone == "unknown":
        return _validate_state(dict(state)), False, "LOCAL_ZONE_INVALID"
    place = _optional_text(place_id, 80)
    area = _optional_text(area_id, 80)
    if place is None or area is None:
        return _validate_state(dict(state)), False, LOCATION_UNREADABLE
    current = _validate_state(dict(state))
    existing = current["local_position"]
    if (existing["zone"] == zone and existing["bound_place_id"] == place
            and existing["bound_area_id"] == area):
        return current, False, None
    current["local_position"] = {"zone": zone, "bound_place_id": place,
                                 "bound_area_id": area,
                                 "declared_at": _iso(now_iso)}
    return _bump(current, now_iso), True, None


def _normalized_dryness(value: Any, *, default: Optional[str]) -> Optional[str]:
    if value is None:
        return default
    if not isinstance(value, str) or value not in DRYNESS_STATES:
        return None
    return value


def wear_outerwear(state: Mapping[str, Any], *, garment_id: Any, label: Any,
                   dryness: Any, now_iso: str
                   ) -> tuple[dict[str, Any], bool, Optional[str]]:
    garment = _optional_text(garment_id, MAX_GARMENT_ID)
    if garment is None:
        return _validate_state(dict(state)), False, "GARMENT_ID_INVALID"
    clean_label = _optional_text(label, MAX_LABEL)
    current = _validate_state(dict(state))
    existing = current["garment"]
    chosen = _normalized_dryness(dryness, default="unknown")
    if chosen is None:
        return current, False, "GARMENT_DRYNESS_INVALID"
    if (existing["state"] == "wear" and existing["garment_id"] == garment
            and existing["label"] == clean_label and existing["dryness"] == chosen):
        return current, False, None
    current["garment"] = {
        "garment_id": garment,
        "label": clean_label,
        "state": "wear",
        "dryness": chosen,
        # There is no World garment owner: the only honest source is her explicit declaration.
        "dampness_source": SELF_DECLARED_SOURCE,
        "hang_position": None,
        # Wearing it ends any hanging/drying observation; it is never both.
        "drying": None,
    }
    return _bump(current, now_iso), True, None


def hang_outerwear(state: Mapping[str, Any], *, garment_id: Any, hang_position: Any,
                   dryness: Any, now_iso: str
                   ) -> tuple[dict[str, Any], bool, Optional[str]]:
    garment = _optional_text(garment_id, MAX_GARMENT_ID)
    position = _optional_text(hang_position, MAX_HANG_POSITION)
    if garment is None:
        return _validate_state(dict(state)), False, "GARMENT_ID_INVALID"
    if position is None:
        return _validate_state(dict(state)), False, "HANG_POSITION_INVALID"
    current = _validate_state(dict(state))
    existing = current["garment"]
    if existing["state"] == "hang" and existing["garment_id"] == garment \
            and existing["hang_position"] == position:
        # Same intent: never reset the drying start, never bump the revision.
        if dryness is None:
            return current, False, None
        chosen = _normalized_dryness(dryness, default=existing["dryness"])
        if chosen is None:
            return current, False, "GARMENT_DRYNESS_INVALID"
        if chosen == existing["dryness"]:
            return current, False, None
    chosen = _normalized_dryness(dryness, default="unknown")
    if chosen is None:
        return current, False, "GARMENT_DRYNESS_INVALID"
    drying = existing["drying"] if (existing["state"] == "hang"
                                    and existing["garment_id"] == garment
                                    and existing["dryness"] == "damp"
                                    and chosen == "damp"
                                    and existing["drying"] is not None) else None
    if chosen == "damp" and drying is None:
        drying = {"started_at": _iso(now_iso), "total_seconds": DRYING_SECONDS}
    current["garment"] = {
        "garment_id": garment,
        "label": existing["label"] if existing["garment_id"] == garment else None,
        "state": "hang",
        "dryness": chosen,
        "dampness_source": SELF_DECLARED_SOURCE,
        "hang_position": position,
        "drying": drying,
    }
    return _bump(current, now_iso), True, None


def derive_garment(state: Mapping[str, Any], now_iso: Any) -> dict[str, Any]:
    """The garment as it is now, including the elapsed-only drying derivation."""

    garment = state.get("garment") or {}
    if not garment.get("state"):
        return {"status": "unknown", "reason_code": "OUTERWEAR_NOT_DECLARED",
                "state": None, "dryness": "unknown", "garment_id": None,
                "label": None, "hang_position": None, "drying": None,
                "source": SIMULATION_SOURCE, "simulation": True,
                "rewrites_body": False}
    row: dict[str, Any] = {
        "status": "available",
        "reason_code": None,
        "state": garment.get("state"),
        "dryness": garment.get("dryness") or "unknown",
        "garment_id": garment.get("garment_id"),
        "label": garment.get("label"),
        "hang_position": garment.get("hang_position"),
        "dampness_source": garment.get("dampness_source"),
        "source": SIMULATION_SOURCE,
        "simulation": True,
        "rewrites_body": False,
        "drying": None,
    }
    drying = garment.get("drying")
    if garment.get("state") == "hang" and isinstance(drying, Mapping):
        elapsed = _elapsed_seconds(drying.get("started_at"), now_iso)
        total = _int_or_none(drying.get("total_seconds")) or DRYING_SECONDS
        if elapsed is None:
            row["dryness"] = "unknown"
            row["drying"] = {"status": "unknown", "reason_code": "CLOCK_UNAVAILABLE",
                             "started_at": drying.get("started_at"),
                             "total_seconds": total, "derived": True,
                             "source": SIMULATION_SOURCE}
        elif elapsed < 0:
            # A backwards clock never warms or dries anything, and never invents progress.
            row["dryness"] = "unknown"
            row["drying"] = {"status": "unknown", "reason_code": "CLOCK_ROLLBACK",
                             "started_at": drying.get("started_at"),
                             "total_seconds": total, "elapsed_seconds": elapsed,
                             "derived": True, "source": SIMULATION_SOURCE}
        else:
            derived = "dry" if elapsed >= total else "damp"
            row["dryness"] = derived
            row["drying"] = {"status": "derived", "reason_code": None,
                             "started_at": drying.get("started_at"),
                             "total_seconds": total,
                             "elapsed_seconds": round(elapsed, 3),
                             "remaining_seconds": max(0, int(total - elapsed)),
                             "derived": True, "source": SIMULATION_SOURCE}
    return row


# ---------------------------------------------------------------------------------------------
# care offerings: one drink slot + one cushion slot
# ---------------------------------------------------------------------------------------------


def _location_match(offer: Mapping[str, Any], place_id: Any, area_id: Any) -> Optional[bool]:
    if not offer:
        return None
    place = _optional_text(place_id, 80)
    area = _optional_text(area_id, 80)
    if place is None or area is None:
        return None
    return (offer.get("bound_place_id") == place
            and offer.get("bound_area_id") == area)


def _new_offer(*, slot: str, kind: str, actor_side: str, position: str,
               place_id: str, area_id: str, now_iso: str,
               bound_item: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    offered_at = _iso(now_iso) or now_iso
    offer: dict[str, Any] = {
        "slot": slot,
        "offer_id": "%s:%s:%s:%s:%s:%s" % (slot, kind, actor_side, place_id, area_id,
                                            offered_at)[:MAX_OFFER_ID],
        "kind": kind,
        "actor_side": actor_side,
        "position": position,
        "bound_place_id": place_id,
        "bound_area_id": area_id,
        "offered_at": offered_at,
        "state": "available",
        "taken_at": None,
        "used_at": None,
        "closed_at": None,
    }
    if slot == "drink":
        offer["temperature"] = {"placed_at": offered_at, "initial": "warm",
                                "final": "room_temp", "cooling_seconds": COOLING_SECONDS}
        offer["bound_item_instance_id"] = (bound_item or {}).get("item_instance_id")
        offer["bound_item_label"] = (bound_item or {}).get("label")
        offer["drink_operation_key"] = None
        offer["receipt_ref"] = None
        offer["consumed_at"] = None
        offer["pending_unknown"] = False
    return offer


def offer_care(state: Mapping[str, Any], *, slot: Any, kind: Any, actor_side: Any,
               position: Any, place_id: Any, area_id: Any, now_iso: str,
               bound_item: Optional[Mapping[str, Any]] = None
               ) -> tuple[dict[str, Any], bool, Optional[str], Optional[dict[str, Any]]]:
    """Place one bounded local offer.  Same content never resets placed/cooling time."""

    current = _validate_state(dict(state))
    if slot not in CARE_SLOTS:
        return current, False, "CARE_SLOT_INVALID", None
    if actor_side not in CARE_ACTORS:
        return current, False, "CARE_ACTOR_INVALID", None
    if position not in CARE_POSITIONS:
        return current, False, "CARE_POSITION_INVALID", None
    if slot == "drink":
        if kind not in DRINK_KINDS:
            return current, False, "DRINK_KIND_INVALID", None
    elif kind not in CUSHION_KINDS:
        return current, False, "CUSHION_KIND_INVALID", None
    place = _optional_text(place_id, 80)
    area = _optional_text(area_id, 80)
    if place is None or area is None:
        return current, False, LOCATION_UNREADABLE, None
    item = None
    if slot == "drink" and isinstance(bound_item, Mapping):
        item_id = _optional_text(bound_item.get("item_instance_id"), 160)
        if item_id is not None:
            item = {"item_instance_id": item_id,
                    "label": _optional_text(bound_item.get("label"), MAX_LABEL)}
    existing = current["care"].get(slot)
    if isinstance(existing, Mapping):
        if existing.get("state") in ("taken", "in_use", "consumed"):
            return current, False, "CARE_SLOT_LOCKED_BY_SUBJECT", existing
        if (existing.get("state") == "available"
                and existing.get("kind") == kind
                and existing.get("actor_side") == actor_side
                and existing.get("position") == position
                and existing.get("bound_place_id") == place
                and existing.get("bound_area_id") == area):
            return current, False, None, existing
    current["care"][slot] = _new_offer(slot=slot, kind=str(kind), actor_side=str(actor_side),
                                       position=str(position), place_id=place, area_id=area,
                                       now_iso=now_iso, bound_item=item)
    return _bump(current, now_iso), True, None, current["care"][slot]


def _care_row(state: Mapping[str, Any], slot: str) -> Optional[dict[str, Any]]:
    care = state.get("care") or {}
    row = care.get(slot)
    return dict(row) if isinstance(row, Mapping) else None


def take_drink(state: Mapping[str, Any], *, place_id: Any, area_id: Any, now_iso: str
               ) -> tuple[dict[str, Any], bool, Optional[str], Optional[dict[str, Any]]]:
    current = _validate_state(dict(state))
    row = _care_row(current, "drink")
    if row is None:
        return current, False, "NO_DRINK_OFFER", None
    if row.get("state") == "taken":
        return current, False, None, row
    if row.get("state") != "available":
        return current, False, "DRINK_NOT_AVAILABLE", row
    if _location_match(row, place_id, area_id) is not True:
        return current, False, "LOCATION_CHANGED", row
    row["state"] = "taken"
    row["taken_at"] = _iso(now_iso)
    current["care"]["drink"] = row
    return _bump(current, now_iso), True, None, row


def mark_drink_result(state: Mapping[str, Any], *, outcome: str, receipt_ref: Any,
                      operation_key: Any, now_iso: str
                      ) -> tuple[dict[str, Any], bool]:
    """Apply one owner outcome to the local slot; the owner already decided the intake."""

    current = _validate_state(dict(state))
    existing = _care_row(current, "drink")
    if existing is None:
        return current, False
    if existing.get("state") == "consumed" and outcome == "consumed":
        # The terminal local fact is recorded once; a replay never re-stamps the time.
        return current, False
    row = dict(existing)
    key = _optional_text(operation_key, 160)
    if outcome == "consumed":
        row["state"] = "consumed"
        row["consumed_at"] = _iso(now_iso) or row.get("consumed_at")
        row["receipt_ref"] = _optional_text(receipt_ref, 160) or row.get("receipt_ref")
        row["drink_operation_key"] = key
        row["pending_unknown"] = False
    elif outcome == "unknown":
        # Never mark consumed while the owner is unknown; reconcile with the same key.
        row["pending_unknown"] = True
        row["drink_operation_key"] = key
    elif outcome == "refused":
        row["pending_unknown"] = False
        row["drink_operation_key"] = key
    else:
        return current, False
    if row == existing:
        return current, False
    current["care"]["drink"] = row
    return _bump(current, now_iso), True


def use_cushion(state: Mapping[str, Any], *, pose: Any, place_id: Any, area_id: Any,
                now_iso: str
                ) -> tuple[dict[str, Any], bool, Optional[str], Optional[dict[str, Any]]]:
    current = _validate_state(dict(state))
    row = _care_row(current, "cushion")
    if row is None:
        return current, False, "NO_CUSHION_OFFER", None
    if row.get("state") == "in_use":
        if pose == "seated" and _location_match(row, place_id, area_id) is True:
            return current, False, None, row
        return current, False, ("POSTURE_NOT_SEATED" if pose != "seated"
                                else "LOCATION_CHANGED"), row
    if row.get("state") != "available":
        return current, False, "CUSHION_NOT_AVAILABLE", row
    if _location_match(row, place_id, area_id) is not True:
        return current, False, "LOCATION_CHANGED", row
    if pose != "seated":
        # A cushion on the floor is a support condition, not a remote support claim.
        return current, False, "POSTURE_NOT_SEATED", row
    row["state"] = "in_use"
    row["used_at"] = _iso(now_iso)
    current["care"]["cushion"] = row
    return _bump(current, now_iso), True, None, row


def stow_care(state: Mapping[str, Any], *, slot: Any, now_iso: str
              ) -> tuple[dict[str, Any], bool, Optional[str], Optional[dict[str, Any]]]:
    current = _validate_state(dict(state))
    if slot not in CARE_SLOTS:
        return current, False, "CARE_SLOT_INVALID", None
    row = _care_row(current, str(slot))
    if row is None:
        return current, False, "NO_CARE_OFFER", None
    if row.get("state") in ("stowed", "withdrawn", "consumed"):
        return current, False, "CARE_ALREADY_CLOSED", row
    row["state"] = "stowed"
    row["closed_at"] = _iso(now_iso)
    current["care"][str(slot)] = row
    return _bump(current, now_iso), True, None, row


def withdraw_care(state: Mapping[str, Any], *, slot: Any, now_iso: str
                  ) -> tuple[dict[str, Any], bool, Optional[str], Optional[dict[str, Any]]]:
    """A partner may only withdraw an offer the subject has not touched."""

    current = _validate_state(dict(state))
    if slot not in CARE_SLOTS:
        return current, False, "CARE_SLOT_INVALID", None
    row = _care_row(current, str(slot))
    if row is None:
        return current, False, "NO_CARE_OFFER", None
    if row.get("actor_side") != "partner":
        return current, False, "WITHDRAW_NOT_OWNER", row
    if row.get("state") == "withdrawn":
        return current, False, None, row
    if row.get("state") != "available":
        return current, False, "SUBJECT_HAS_TOUCHED", row
    row["state"] = "withdrawn"
    row["closed_at"] = _iso(now_iso)
    current["care"][str(slot)] = row
    return _bump(current, now_iso), True, None, row


def temperature_band(offer: Mapping[str, Any], now_iso: Any) -> dict[str, Any]:
    temperature = offer.get("temperature") if isinstance(offer.get("temperature"),
                                                          Mapping) else {}
    placed = temperature.get("placed_at") or offer.get("offered_at")
    total = _int_or_none(temperature.get("cooling_seconds")) or COOLING_SECONDS
    elapsed = _elapsed_seconds(placed, now_iso)
    base = {"placed_at": _iso(placed), "initial": "warm", "final": "room_temp",
            "cooling_seconds": total, "source": SIMULATION_SOURCE,
            "derived_from_elapsed": True, "never_rewritten": True}
    if elapsed is None:
        base.update({"band": "unknown", "reason_code": "CLOCK_UNAVAILABLE",
                     "elapsed_seconds": None})
        return base
    if elapsed < 0:
        # A backwards clock does not reheat; it makes the band unknown, conservatively.
        base.update({"band": "unknown", "reason_code": "CLOCK_ROLLBACK",
                     "elapsed_seconds": elapsed})
        return base
    if elapsed < total / 3:
        band = "warm"
    elif elapsed < (2 * total) / 3:
        band = "tepid"
    else:
        band = "room_temp"
    base.update({"band": band, "reason_code": None,
                 "elapsed_seconds": round(elapsed, 3)})
    return base


def derive_care(state: Mapping[str, Any], *, now_iso: Any, place_id: Any, area_id: Any,
                pose: Any) -> dict[str, Any]:
    drink = _care_row(state, "drink")
    cushion = _care_row(state, "cushion")
    drink_out: Optional[dict[str, Any]] = None
    if drink is not None:
        match = _location_match(drink, place_id, area_id)
        drink_out = {
            "slot": "drink", "kind": drink.get("kind"),
            "actor_side": drink.get("actor_side"), "position": drink.get("position"),
            "state": drink.get("state"),
            "bound": {"place_id": drink.get("bound_place_id"),
                      "area_id": drink.get("bound_area_id")},
            "location_match": match,
            "available_here": bool(drink.get("state") == "available" and match is True),
            "taken": drink.get("state") in ("taken", "consumed"),
            "temperature": temperature_band(drink, now_iso),
            "bound_item": ({"item_instance_id": drink.get("bound_item_instance_id"),
                            "label": drink.get("bound_item_label")}
                           if drink.get("bound_item_instance_id") else None),
            "drink_supported": bool(drink.get("bound_item_instance_id")),
            "receipt_ref": drink.get("receipt_ref"),
            "consumed_at": drink.get("consumed_at"),
            "pending_unknown": bool(drink.get("pending_unknown")),
            "source": SIMULATION_SOURCE, "simulation": True,
            "changes_needs": False,
        }
        if not drink_out["drink_supported"]:
            drink_out["drink_reason_code"] = "NO_THIRST_RELIEVING_CONSUMABLE"
    cushion_out: Optional[dict[str, Any]] = None
    if cushion is not None:
        match = _location_match(cushion, place_id, area_id)
        support_active = bool(cushion.get("state") == "in_use" and match is True
                              and pose == "seated")
        reason = None
        if cushion.get("state") == "in_use" and not support_active:
            if pose != "seated":
                reason = "POSTURE_CHANGED"
            elif match is not True:
                reason = "LOCATION_CHANGED"
        cushion_out = {
            "slot": "cushion", "kind": cushion.get("kind"),
            "actor_side": cushion.get("actor_side"), "position": cushion.get("position"),
            "state": cushion.get("state"),
            "bound": {"place_id": cushion.get("bound_place_id"),
                      "area_id": cushion.get("bound_area_id")},
            "location_match": match,
            "available_here": bool(cushion.get("state") == "available" and match is True),
            "support_active": support_active,
            "support_reason_code": reason,
            "support_rechecked": True,
            "source": SIMULATION_SOURCE, "simulation": True,
            "changes_needs": False,
        }
    return {"drink": drink_out, "cushion": cushion_out,
            "slots": {"drink": 1, "cushion": 1},
            "offer_source": SIMULATION_SOURCE,
            "changes_relationship": False, "changes_activity": False,
            "auto_follows_location": False}


# ---------------------------------------------------------------------------------------------
# the bounded four-modality projection
# ---------------------------------------------------------------------------------------------


def world_context(world_result: Mapping[str, Any]) -> dict[str, Any]:
    data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
    location = data.get("location") if isinstance(data.get("location"), Mapping) else {}
    world = data.get("world") if isinstance(data.get("world"), Mapping) else {}
    environment = data.get("local_environment") \
        if isinstance(data.get("local_environment"), Mapping) else {}
    light = environment.get("light") if isinstance(environment.get("light"),
                                                   Mapping) else {}
    travel = data.get("travel") if isinstance(data.get("travel"), Mapping) else {}
    available = bool(world_result.get("ok") and location.get("status") == "available")
    return {
        "status": "available" if available else "unavailable",
        "reason_code": None if available else str(
            location.get("reason_code") or world_result.get("reason_code")
            or "WORLD_UNAVAILABLE"),
        "place_id": location.get("place_id") if available else None,
        "area_id": location.get("area_id") if available else None,
        "home": bool(location.get("home")) if available else None,
        "local_time": world.get("local_time"),
        "local_date": world.get("local_date"),
        "light": {"status": str(light.get("status") or "unknown"),
                  "reason_code": light.get("reason_code") or None,
                  "power": light.get("power"), "band": light.get("band"),
                  "source": "world_room_environment"},
        "travelling": str(travel.get("status") or "") == "in_progress",
        "source": WORLD_READ_SOURCE,
    }


def body_context(body_view: Mapping[str, Any]) -> dict[str, Any]:
    raw_data = body_view.get("data")
    data = raw_data if isinstance(raw_data, Mapping) else {}
    needs = data.get("needs")
    return {
        "status": str(body_view.get("status") or "unavailable"),
        "reason_code": body_view.get("reason_code") or None,
        "pose": data.get("pose"),
        "orientation": data.get("orientation"),
        "recovery": data.get("recovery") if isinstance(data.get("recovery"),
                                                        Mapping) else None,
        "signals": [row for row in (data.get("signals") or []) if isinstance(row, Mapping)],
        "needs": dict(needs) if isinstance(needs, Mapping) else None,
        "needs_status": "available" if isinstance(needs, Mapping) else "unknown",
        "rest_evidence": data.get("rest_evidence"),
        "signals_status": data.get("signals_status"),
        "bands_only": True,
        "raw_values_exposed": False,
        "source": BODY_SIGNAL_SOURCE,
    }


def _region(world: Mapping[str, Any], local_position: Mapping[str, Any],
            bound_match: Optional[bool]) -> dict[str, Any]:
    zone = local_position.get("zone") or "unknown"
    return {
        "place_id": world.get("place_id"),
        "area_id": world.get("area_id"),
        "local_zone": zone,
        "local_zone_source": SIMULATION_SOURCE,
        "area_source": WORLD_READ_SOURCE,
        "bound": {"place_id": local_position.get("bound_place_id"),
                  "area_id": local_position.get("bound_area_id")},
        "at_declared_position": bound_match,
    }


def _modality(name: str, *, status: str, quality: Optional[str], salience: str,
              region: Mapping[str, Any], source_refs: list[str], simulation: bool,
              reason_code: Optional[str] = None, **extra: Any) -> dict[str, Any]:
    if salience not in SALIENCE:
        raise ValueError("invalid salience")
    row = {
        "modality": name,
        "status": status,
        "quality": quality,
        "salience": salience,
        "region": dict(region),
        "source_refs": [str(item) for item in source_refs],
        "simulation": bool(simulation),
        "reason_code": reason_code,
        "claims_subjective_experience": False,
        "diagnoses_pain": False,
    }
    row.update(extra)
    return row


def _local_position_match(state: Mapping[str, Any], world: Mapping[str, Any]
                          ) -> Optional[bool]:
    position = state.get("local_position") or {}
    if not position.get("zone") or position.get("zone") == "unknown":
        return None
    if world.get("status") != "available":
        return None
    return (position.get("bound_place_id") == world.get("place_id")
            and position.get("bound_area_id") == world.get("area_id"))


def somatic_block(*, world_result: Mapping[str, Any], body_view: Mapping[str, Any],
                  state: Mapping[str, Any], state_reason: Optional[str],
                  now_iso: Any, soundscape: Optional[Mapping[str, Any]] = None
                  ) -> dict[str, Any]:
    """The pure four-layer read: real owners + this instance's explicit simulation."""

    world = world_context(world_result)
    body = body_context(body_view)
    garment = derive_garment(state, now_iso)
    position = state.get("local_position") or {}
    match = _local_position_match(state, world)
    region = _region(world, position, match)
    window_state = (state.get("window") or {}).get("state")
    care = derive_care(state, now_iso=now_iso, place_id=world.get("place_id"),
                       area_id=world.get("area_id"), pose=body.get("pose"))
    pose = body.get("pose")

    # -- thermal: only a declared window-side position plus a known open window is cool.
    if world.get("status") != "available":
        thermal = _modality("thermal", status="unknown", quality=None, salience="none",
                            region=region, source_refs=["world:location"],
                            simulation=False, reason_code=world.get("reason_code"))
    elif window_state not in WINDOW_STATES:
        thermal = _modality("thermal", status="unknown", quality=None, salience="none",
                            region=region, source_refs=["local:window"],
                            simulation=True, reason_code="WINDOW_STATE_UNKNOWN")
    elif window_state == "closed":
        thermal = _modality("thermal", status="none", quality=None, salience="none",
                            region=region,
                            source_refs=["local:window:closed"],
                            simulation=True,
                            reason_code="WINDOW_CLOSED_NO_THERMAL_SOURCE")
    elif match is not True or position.get("zone") != "window_side":
        # Being in a bedroom is not evidence of standing by its window.
        thermal = _modality("thermal", status="unknown", quality=None, salience="none",
                            region=region,
                            source_refs=["local:window:%s" % window_state],
                            simulation=True,
                            reason_code="NO_WINDOW_PROXIMITY_EVIDENCE")
    else:
        thermal = _modality(
            "thermal", status="available", quality="cool",
            salience="mild" if window_state == "ajar" else "medium",
            region=region,
            source_refs=["local:window:%s" % window_state,
                         "local:position:window_side",
                         "world:area:%s" % (world.get("area_id") or "unknown")],
            simulation=True,
            limitation="cool is a bounded local projection, not a temperature measurement")

    # -- moisture: only a worn outerwear she explicitly declared.  Rain sound is not wetness.
    if garment.get("status") != "available":
        moisture = _modality("moisture", status="unknown", quality=None, salience="none",
                             region=region, source_refs=["local:garment"],
                             simulation=True, reason_code="OUTERWEAR_NOT_DECLARED")
    elif garment.get("state") != "wear":
        moisture = _modality("moisture", status="none", quality=None, salience="none",
                             region=region, source_refs=["local:garment:hang"],
                             simulation=True, reason_code="OUTERWEAR_NOT_WORN")
    else:
        dryness = garment.get("dryness")
        if dryness == "damp":
            moisture = _modality("moisture", status="available", quality="damp",
                                 salience="medium", region=region,
                                 source_refs=["local:garment:%s:damp"
                                              % garment.get("garment_id")],
                                 simulation=True,
                                 dampness_source=garment.get("dampness_source"))
        elif dryness == "dry":
            moisture = _modality("moisture", status="available", quality="dry",
                                 salience="low", region=region,
                                 source_refs=["local:garment:%s:dry"
                                              % garment.get("garment_id")],
                                 simulation=True,
                                 dampness_source=garment.get("dampness_source"))
        else:
            moisture = _modality("moisture", status="unknown", quality=None,
                                 salience="none", region=region,
                                 source_refs=["local:garment:%s"
                                              % garment.get("garment_id")],
                                 simulation=True, reason_code="GARMENT_DRYNESS_UNKNOWN")

    # -- posture: real pose + real rest surface; no duration, so no "sat too long".
    if body.get("status") == "unavailable" or pose is None:
        posture = _modality("posture", status="unknown", quality=None, salience="none",
                            region=region, source_refs=["body:pose"],
                            simulation=False,
                            reason_code=body.get("reason_code") or "BODY_VIEW_UNAVAILABLE")
    else:
        cushion_active = bool((care.get("cushion") or {}).get("support_active"))
        rest_evidence = body.get("rest_evidence")
        if rest_evidence:
            quality = "supported_world_surface"
            sources = ["body:pose:%s" % pose, "body:rest_evidence:%s" % rest_evidence]
        elif cushion_active:
            quality = "supported_local_cushion"
            sources = ["body:pose:%s" % pose, "local:cushion:support_active"]
        elif pose == "seated":
            quality = "seated_support_unverified"
            sources = ["body:pose:seated"]
        elif pose == "lying":
            quality = "lying"
            sources = ["body:pose:lying"]
        elif pose == "standing":
            quality = "standing"
            sources = ["body:pose:standing"]
        else:
            quality = "transitioning"
            sources = ["body:pose:%s" % pose]
        posture = _modality("posture", status="available", quality=quality,
                            salience="low", region=region, source_refs=sources,
                            simulation=bool(cushion_active and not rest_evidence),
                            sustained_duration_seconds=None,
                            duration_reason_code=NO_POSTURE_DURATION,
                            pain_claim=None,
                            pain_reason_code="NO_PAIN_AUTHORITY")

    # -- tactile: a warm cup is only "held" after she really took it.
    drink = care.get("drink")
    if not drink:
        tactile = _modality("tactile", status="none", quality=None, salience="none",
                            region=region, source_refs=["local:care:drink"],
                            simulation=True, reason_code="NO_DRINK_OFFER")
    else:
        band = (drink.get("temperature") or {}).get("band")
        state_name = drink.get("state")
        if state_name == "taken" and band in ("warm", "tepid"):
            tactile = _modality("tactile", status="available", quality="held_warm_contact",
                                salience="medium", region=region,
                                source_refs=["local:care:drink:taken",
                                             "local:care:temperature:%s" % band],
                                simulation=True)
        elif state_name == "available" and band in ("warm", "tepid") \
                and drink.get("location_match") is True:
            tactile = _modality("tactile", status="available",
                                quality="reachable_warm_object", salience="low",
                                region=region,
                                source_refs=["local:care:drink:available",
                                             "local:care:temperature:%s" % band],
                                simulation=True,
                                note="reachable only; she has not taken the cup")
        elif state_name == "consumed":
            tactile = _modality("tactile", status="none", quality=None, salience="none",
                                region=region, source_refs=["local:care:drink:consumed"],
                                simulation=True, reason_code="DRINK_ALREADY_CONSUMED")
        elif band == "room_temp" and state_name in ("available", "taken"):
            tactile = _modality("tactile", status="available",
                                quality="reachable_room_temp_object", salience="low",
                                region=region,
                                source_refs=["local:care:drink:%s" % state_name,
                                             "local:care:temperature:room_temp"],
                                simulation=True)
        else:
            tactile = _modality("tactile", status="none", quality=None, salience="none",
                                region=region, source_refs=["local:care:drink"],
                                simulation=True, reason_code="NO_TACTILE_SIGNAL")

    ambient = dict(soundscape) if isinstance(soundscape, Mapping) else {
        "available": False, "reason_code": "AMBIENT_UNAVAILABLE", "events": [],
        "template_projection": True, "is_weather": False}
    ambient["template_projection"] = True
    ambient["is_weather"] = False
    ambient["not_evidence_of_wetness"] = True
    ambient["note"] = "ambient 是当天模板声景；雨意/声景都不是淋湿或气象证据"

    modalities = {"thermal": thermal, "moisture": moisture,
                  "posture": posture, "tactile": tactile}
    local_state_ok = state_reason is None
    world_data = world_result.get("data")
    world_block = (world_data.get("world")
                   if isinstance(world_data, Mapping)
                   and isinstance(world_data.get("world"), Mapping) else {})
    return {
        "schema": SOMATIC_SCHEMA,
        "read_only": True,
        "observed_at": _iso(now_iso),
        "revision": _int_or_none(state.get("revision")),
        "local_state": {"status": "available" if local_state_ok else "unavailable",
                        "reason_code": state_reason,
                        "source": SIMULATION_SOURCE},
        "body": body,
        "world": {"place_id": world.get("place_id"), "area_id": world.get("area_id"),
                  "status": world.get("status"), "reason_code": world.get("reason_code"),
                  "local_date": world.get("local_date"),
                  "local_time": world.get("local_time"),
                  "light": world.get("light"),
                  "world_revision": world_block.get("revision"),
                  "source": WORLD_READ_SOURCE},
        "ambient": ambient,
        "weather": {"value": None, "status": "unavailable",
                    "reason_code": NO_WEATHER_AUTHORITY, "is_weather_authority": False},
        "modalities": modalities,
        "modality_count": len(MODALITIES),
        "available_modalities": [name for name in MODALITIES
                                 if modalities[name]["status"] == "available"],
        "unknown_modalities": [name for name in MODALITIES
                               if modalities[name]["status"] == "unknown"],
        "temporal_alignment": {
            "atomic_snapshot": False,
            "status": "partial" if local_state_ok else "partial",
            "reason_code": "INDEPENDENT_OWNER_READS",
            "note": "World 与 Body 是独立 owner 读；本地仿真条件也有自己的 revision，"
                    "不声称一次原子快照",
        },
        "layers": {
            "world_environment": {"in_scope": True, "source": WORLD_READ_SOURCE},
            "body_pose_load": {"in_scope": True, "source": BODY_SIGNAL_SOURCE},
            "bounded_projection": {"in_scope": True, "writes_body": False,
                                   "claims_subjective_experience": False,
                                   "maps_errors_to_pain": False},
            "cognition_choice": {"in_scope": False,
                                 "note": "本模块不替她解释或选择"},
        },
        "simulation_sources": [SIMULATION_SOURCE, SELF_DECLARED_SOURCE],
        "raw_body_values_exposed": False,
        "private_text_included": False,
        "local_only": True,
    }


# ---------------------------------------------------------------------------------------------
# the two read blocks for the explicit slices
# ---------------------------------------------------------------------------------------------


def garment_window_block(*, world_result: Mapping[str, Any], state: Mapping[str, Any],
                         state_reason: Optional[str], now_iso: Any) -> dict[str, Any]:
    world = world_context(world_result)
    position = state.get("local_position") or {}
    match = _local_position_match(state, world)
    garment = derive_garment(state, now_iso)
    return {
        "schema": GARMENT_WINDOW_SCHEMA,
        "read_only": True,
        "observed_at": _iso(now_iso),
        "revision": _int_or_none(state.get("revision")),
        "local_state": {"status": "available" if state_reason is None else "unavailable",
                        "reason_code": state_reason, "source": SIMULATION_SOURCE},
        "world": {"status": world.get("status"), "place_id": world.get("place_id"),
                  "area_id": world.get("area_id"), "travelling": world.get("travelling"),
                  "source": WORLD_READ_SOURCE},
        "window": {"state": (state.get("window") or {}).get("state"),
                   "updated_at": (state.get("window") or {}).get("updated_at"),
                   "source": SIMULATION_SOURCE, "simulation": True,
                   "states": list(WINDOW_STATES)},
        "local_position": {"zone": position.get("zone"),
                           "bound_place_id": position.get("bound_place_id"),
                           "bound_area_id": position.get("bound_area_id"),
                           "declared_at": position.get("declared_at"),
                           "at_declared_position": match,
                           "source": SIMULATION_SOURCE, "simulation": True},
        "garment": garment,
        "mutual_exclusion": {"wearing_and_hanging_impossible": True,
                             "states": list(GARMENT_STATES),
                             "dryness_states": list(DRYNESS_STATES)},
        "drying": {"supported": True, "only_declared_offline_drying": True,
                   "rate_seconds": DRYING_SECONDS,
                   "derived_from_elapsed": True, "writes_progress": False,
                   "source": SIMULATION_SOURCE},
        "weather": {"value": None, "status": "unavailable",
                    "reason_code": NO_WEATHER_AUTHORITY},
        "private_text_included": False,
        "local_only": True,
        "note": "窗与衣物都是本实例局部仿真；微潮只来自本人显式申报，"
                "不因 ambient 雨声自动生成淋湿。",
    }


def care_offering_block(*, world_result: Mapping[str, Any], body_view: Mapping[str, Any],
                        state: Mapping[str, Any], state_reason: Optional[str],
                        now_iso: Any) -> dict[str, Any]:
    world = world_context(world_result)
    body = body_context(body_view)
    care = derive_care(state, now_iso=now_iso, place_id=world.get("place_id"),
                       area_id=world.get("area_id"), pose=body.get("pose"))
    return {
        "schema": CARE_SCHEMA,
        "read_only": True,
        "observed_at": _iso(now_iso),
        "revision": _int_or_none(state.get("revision")),
        "local_state": {"status": "available" if state_reason is None else "unavailable",
                        "reason_code": state_reason, "source": SIMULATION_SOURCE},
        "world": {"status": world.get("status"), "place_id": world.get("place_id"),
                  "area_id": world.get("area_id"), "travelling": world.get("travelling"),
                  "source": WORLD_READ_SOURCE},
        "body": {"status": body.get("status"), "pose": body.get("pose"),
                 "source": BODY_SIGNAL_SOURCE},
        "drink": care["drink"],
        "cushion": care["cushion"],
        "slots": care["slots"],
        "offer_source": SIMULATION_SOURCE,
        "temperature_source": SIMULATION_SOURCE,
        "support_source": SIMULATION_SOURCE,
        "changes_needs": False,
        "changes_relationship": False,
        "changes_activity": False,
        "auto_follows_location": False,
        "drink_is_existing_owner_consume": True,
        "private_text_included": False,
        "local_only": True,
        "note": "放下只表示 available；拿起与饮用分开；靠垫只有真正使用且仍在同一"
                "place/area 的坐姿里才 support active。",
    }


# ---------------------------------------------------------------------------------------------
# v0.3 GAP-08 Integer ConsumeReceipt + Real body_lived_needs Thirst Relief & Action Validation
# ---------------------------------------------------------------------------------------------

DRINK_NUTRIENT_RECIPES: dict[str, dict[str, Any]] = {
    "warm_water": {
        "amount_mg": 200_000,
        "energy_mJ": 0,
        "thirst_relief": 0.35,
        "hunger_relief": 0.0,
        "nutrient_profile_version": "v03.drink.1",
    },
    "hot_tea": {
        "amount_mg": 200_000,
        "energy_mJ": 8_000,
        "thirst_relief": 0.32,
        "hunger_relief": 0.02,
        "nutrient_profile_version": "v03.drink.1",
    },
}

ALL_SOMATIC_ACTIONS = frozenset({
    "set_window",
    "set_local_position",
    "wear_outerwear",
    "hang_outerwear",
    "offer",
    "take",
    "drink",
    "use",
    "stow",
    "withdraw",
})


def _load_bln_module() -> Any:
    try:
        from components.world.scripts import body_lived_needs as bln

        return bln
    except ImportError:
        import body_lived_needs as bln  # type: ignore[no-redef]

        return bln


def _lived_needs_file_path(root: Path) -> Path:
    return root / "body" / "lived_needs.json"


def _lived_needs_acks_path(root: Path) -> Path:
    return root / "body" / "lived_needs_acks.json"


def _candidate_runtime_paths(root: Path) -> list[Path]:
    candidates = [
        root / "data" / "body" / "runtime.json",
        root / "world" / "data" / "body" / "runtime.json",
    ]
    inst_file = root / "phone_instance.json"
    if inst_file.exists() and not inst_file.is_symlink():
        try:
            inst = json.loads(inst_file.read_text(encoding="utf-8"))
            wh = str(inst.get("world_home") or "").strip()
            if wh:
                candidates.append(Path(wh) / "data" / "body" / "runtime.json")
        except Exception:
            pass
    return candidates


def _atomic_write_json_doc(path: Path, doc: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = ""
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def read_lived_needs_snapshot(root: Path | str) -> dict[str, Any]:
    """100% zero-write read of current ``body_lived_needs`` state & qualitative bands."""
    rpath = Path(root)
    bln = _load_bln_module()

    for rt_path in _candidate_runtime_paths(rpath):
        if rt_path.exists() and not rt_path.is_symlink():
            try:
                rt = json.loads(rt_path.read_text(encoding="utf-8"))
                if isinstance(rt, Mapping) and isinstance(rt.get("lived_needs"), Mapping):
                    validated = bln.validate_state(rt["lived_needs"])
                    return {
                        "status": "available",
                        "source_path": str(rt_path),
                        "levels": dict(validated["levels"]),
                        "bands": bln.bands({"lived_needs": validated}),
                    }
            except Exception:
                continue

    ln_path = _lived_needs_file_path(rpath)
    if ln_path.exists() and not ln_path.is_symlink():
        try:
            raw_ln = json.loads(ln_path.read_text(encoding="utf-8"))
            validated = bln.validate_state(raw_ln)
            return {
                "status": "available",
                "source_path": str(ln_path),
                "levels": dict(validated["levels"]),
                "bands": bln.bands({"lived_needs": validated}),
            }
        except Exception:
            return {"status": "corrupt", "source_path": str(ln_path), "levels": None, "bands": None}

    for prof_dir in (rpath / "data" / "body", rpath / "body", rpath / "world" / "data" / "body"):
        prof = bln.load_profile(prof_dir) if prof_dir.exists() else None
        if prof is not None:
            init_st = bln.initial_state(prof)
            return {
                "status": "profile_ready",
                "source_path": str(bln.profile_path(prof_dir)),
                "levels": dict(init_st["levels"]),
                "bands": bln.bands({"lived_needs": init_st}),
            }

    return {"status": "unconfigured", "source_path": None, "levels": None, "bands": None}


def apply_lived_needs_relief(
    root: Path | str,
    *,
    hunger_relief: float = 0.0,
    thirst_relief: float = 0.0,
    toilet_relief: float = 0.0,
    receipt_id: str,
    now_iso: str,
    auto_initialize: bool = True,
) -> dict[str, Any]:
    """Idempotently apply real hunger/thirst relief to ``body_lived_needs`` by ``receipt_id`` (GAP-08)."""
    rpath = Path(root)
    bln = _load_bln_module()

    acks_path = _lived_needs_acks_path(rpath)
    acks_doc: dict[str, Any] = {"revision": 0, "acks": {}}
    if acks_path.exists() and not acks_path.is_symlink():
        try:
            loaded_acks = json.loads(acks_path.read_text(encoding="utf-8"))
            if isinstance(loaded_acks, Mapping) and isinstance(loaded_acks.get("acks"), Mapping):
                acks_doc = {
                    "revision": int(loaded_acks.get("revision") or 0),
                    "acks": dict(loaded_acks["acks"]),
                }
        except Exception:
            pass

    if receipt_id in acks_doc["acks"]:
        prev_ack = dict(acks_doc["acks"][receipt_id])
        prev_ack["replayed"] = True
        return prev_ack

    relief_inputs: list[dict[str, Any]] = []
    if hunger_relief > 0.0:
        relief_inputs.append({"class": "LIVED_NEED_RELIEF", "need": "hunger", "amount": float(hunger_relief), "source_ref": receipt_id})
    if thirst_relief > 0.0:
        relief_inputs.append({"class": "LIVED_NEED_RELIEF", "need": "thirst", "amount": float(thirst_relief), "source_ref": receipt_id})
    if toilet_relief > 0.0:
        relief_inputs.append({"class": "LIVED_NEED_RELIEF", "need": "toilet", "amount": float(toilet_relief), "source_ref": receipt_id})

    # Locate existing runtime.json or lived_needs.json, or initialize from profile / baseline
    target_runtime_path: Optional[Path] = None
    runtime_doc: Optional[dict[str, Any]] = None
    ln_state: Optional[dict[str, Any]] = None

    for rt_path in _candidate_runtime_paths(rpath):
        if rt_path.exists() and not rt_path.is_symlink():
            try:
                parsed_rt = json.loads(rt_path.read_text(encoding="utf-8"))
                if isinstance(parsed_rt, Mapping) and isinstance(parsed_rt.get("lived_needs"), Mapping):
                    ln_state = bln.validate_state(parsed_rt["lived_needs"])
                    runtime_doc = dict(parsed_rt)
                    target_runtime_path = rt_path
                    break
            except Exception:
                continue

    ln_path = _lived_needs_file_path(rpath)
    if ln_state is None and ln_path.exists() and not ln_path.is_symlink():
        try:
            ln_state = bln.validate_state(json.loads(ln_path.read_text(encoding="utf-8")))
        except Exception:
            ln_state = None

    if ln_state is None:
        for prof_dir in (rpath / "data" / "body", rpath / "body", rpath / "world" / "data" / "body"):
            prof = bln.load_profile(prof_dir) if prof_dir.exists() else None
            if prof is not None:
                ln_state = bln.initial_state(prof)
                break

    if ln_state is None:
        if not auto_initialize:
            return {
                "applied": False,
                "receipt_id": receipt_id,
                "reason_code": "LIVED_NEEDS_NOT_ENABLED",
            }
        default_prof = bln.build_profile(hunger=0.62, thirst=0.75, toilet=0.18)
        ln_state = bln.initial_state(default_prof)

    before_levels = dict(ln_state["levels"])
    before_bands = bln.bands({"lived_needs": ln_state})
    updated_ln, deltas = bln.apply_advance(ln_state, hours=0.0, relief=relief_inputs)
    after_levels = dict(updated_ln["levels"])
    after_bands = bln.bands({"lived_needs": updated_ln})

    if target_runtime_path is not None and runtime_doc is not None:
        runtime_doc["lived_needs"] = updated_ln
        runtime_doc["version"] = int(runtime_doc.get("version") or 0) + 1
        _atomic_write_json_doc(target_runtime_path, runtime_doc)

    _atomic_write_json_doc(ln_path, updated_ln)

    body_rev = int(acks_doc["revision"]) + 1
    ack_record = {
        "applied": True,
        "replayed": False,
        "receipt_id": receipt_id,
        "body_revision": body_rev,
        "occurred_at": _iso(now_iso) or now_iso,
        "before_levels": {k: round(v, 4) for k, v in before_levels.items()},
        "after_levels": {k: round(v, 4) for k, v in after_levels.items()},
        "before_bands": before_bands,
        "after_bands": after_bands,
        "deltas": {k: round(v, 4) for k, v in deltas.items()},
    }
    acks_doc["revision"] = body_rev
    acks_doc["acks"][receipt_id] = ack_record
    _atomic_write_json_doc(acks_path, acks_doc)
    return ack_record


def is_cup_occupied_by_stationery(root: Path | str, container_id: str = "ceramic_mug") -> bool:
    """Zero-write check: True if ``container_id`` currently holds stationery/writing_tool/pen."""
    rpath = Path(root)
    stationery_classes = {"writing_tool", "stationery", "pen", "clip"}
    for candidate in (
        rpath / "life" / "attention_stance_containment.json",
        rpath / "phone" / "stance_containment.json",
    ):
        if not candidate.exists() or candidate.is_symlink():
            continue
        try:
            doc = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        cdom = doc.get("containment_domain")
        if not isinstance(cdom, Mapping):
            continue
        items = cdom.get("items")
        if isinstance(items, Mapping):
            item_iter = items.values()
        elif isinstance(items, list):
            item_iter = items
        else:
            continue
        for it in item_iter:
            if not isinstance(it, Mapping):
                continue
            parent_cid = str(it.get("parent_container_id") or it.get("container_id") or "")
            if it.get("placement_kind") == "container" and parent_cid == container_id:
                iclass = str(it.get("item_class") or "").lower()
                iid = str(it.get("item_id") or "").lower()
                if iclass in stationery_classes or "pen" in iid or "pencil" in iid:
                    return True
    return False


def drink_care(
    root: Path | str,
    *,
    actor_side: str = "her",
    place_id: str = "home",
    area_id: str = "bedroom",
    container_id: Optional[str] = None,
    operation_key: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Consume the taken drink in ``somatic_comfort.json`` and genuinely relieve ``body_lived_needs.thirst``."""
    rpath = Path(root)
    eff_now = _iso(now_iso) or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    if actor_side not in ("her", "subject"):
        return 403, {"ok": False, "reason_code": "CAPABILITY_REQUIRED", "consumed": False}

    target_cup = str(container_id or "").strip()
    if target_cup and is_cup_occupied_by_stationery(rpath, target_cup):
        return 409, {
            "ok": False,
            "reason_code": "CUP_OCCUPIED_BY_STATIONERY",
            "container_id": target_cup,
            "consumed": False,
        }

    current, state_reason = read_state(rpath)
    if state_reason is not None:
        return 503, {"ok": False, "reason_code": state_reason, "consumed": False}

    row = _care_row(current, "drink")
    if row is None:
        return 409, {"ok": False, "reason_code": "NO_DRINK_OFFER", "consumed": False}

    bound_id = str(row.get("bound_item_instance_id") or "")
    if bound_id == "ceramic_mug" or target_cup == "ceramic_mug":
        if is_cup_occupied_by_stationery(rpath, "ceramic_mug"):
            return 409, {
                "ok": False,
                "reason_code": "CUP_OCCUPIED_BY_STATIONERY",
                "container_id": "ceramic_mug",
                "consumed": False,
            }

    if row.get("state") == "consumed":
        existing_rcpt = str(row.get("receipt_ref") or f"rcpt-somatic-{row.get('offer_id')}")
        ln_snap = read_lived_needs_snapshot(rpath)
        return 200, {
            "ok": True,
            "status": "already_applied",
            "action": "drink",
            "slot": "drink",
            "changed": False,
            "replayed": True,
            "consumed": True,
            "receipt_ref": existing_rcpt,
            "lived_needs": ln_snap,
        }

    if row.get("state") != "taken":
        return 409, {
            "ok": False,
            "reason_code": "DRINK_REQUIRES_TAKE_FIRST",
            "state": row.get("state"),
            "consumed": False,
        }

    if _location_match(row, place_id, area_id) is False:
        return 409, {"ok": False, "reason_code": "LOCATION_CHANGED", "consumed": False}

    kind = str(row.get("kind") or "warm_water")
    recipe = DRINK_NUTRIENT_RECIPES.get(kind, DRINK_NUTRIENT_RECIPES["warm_water"])
    op_key = _optional_text(operation_key, 160) or f"somatic-drink:{row.get('offer_id')}"
    import hashlib as _hl

    receipt_id = "rcpt_drink_" + _hl.sha256(
        f"{row.get('offer_id')}|{op_key}|{kind}".encode("utf-8")
    ).hexdigest()[:16]

    ver_before = int(current.get("revision") or 0)
    next_state, changed = mark_drink_result(
        current,
        outcome="consumed",
        receipt_ref=receipt_id,
        operation_key=op_key,
        now_iso=eff_now,
    )
    written = write_state(rpath, next_state) if changed else current
    ver_after = int(written.get("revision") or ver_before)

    consume_receipt = {
        "receipt_id": receipt_id,
        "operation_key": op_key,
        "item_id": bound_id or f"care_drink:{kind}",
        "item_version_before": ver_before,
        "amount_mg": int(recipe["amount_mg"]),
        "energy_mJ": int(recipe["energy_mJ"]),
        "nutrient_profile_version": str(recipe["nutrient_profile_version"]),
        "occurred_at": eff_now,
        "world_version_after": ver_after,
    }
    ingest_ack = apply_lived_needs_relief(
        rpath,
        hunger_relief=float(recipe["hunger_relief"]),
        thirst_relief=float(recipe["thirst_relief"]),
        receipt_id=receipt_id,
        now_iso=eff_now,
        auto_initialize=True,
    )

    return 200, {
        "ok": True,
        "status": "applied",
        "action": "drink",
        "slot": "drink",
        "changed": bool(changed),
        "replayed": False,
        "consumed": True,
        "revision": ver_after,
        "consume_receipt": consume_receipt,
        "body_ingest_ack": ingest_ack,
        "lived_needs": read_lived_needs_snapshot(rpath),
    }


def mutate_somatic_comfort_view(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "her",
    place_id: str = "home",
    area_id: str = "bedroom",
    pose: str = "seated",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Unified validated action handler for somatic comfort (window/garment + care/drink with real thirst relief)."""
    if not isinstance(payload, Mapping):
        return 400, {"ok": False, "reason_code": "INVALID_JSON_OBJECT"}
    for k in payload:
        if not isinstance(k, str) or not k.strip():
            return 400, {"ok": False, "reason_code": "EMPTY_FIELD_NAME"}

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip():
        return 400, {"ok": False, "reason_code": "ACTION_REQUIRED"}
    action = raw_action.strip().lower()
    if action not in ALL_SOMATIC_ACTIONS:
        return 400, {"ok": False, "reason_code": "CARE_ACTION_UNSUPPORTED", "action": action}

    allowed_by_action: dict[str, frozenset[str]] = {
        "set_window": frozenset({"action", "state"}),
        "set_local_position": frozenset({"action", "zone"}),
        "wear_outerwear": frozenset({"action", "garment_id", "label", "dryness"}),
        "hang_outerwear": frozenset({"action", "garment_id", "hang_position", "dryness"}),
        "offer": frozenset({"action", "slot", "kind", "actor_side", "position", "bound_item"}),
        "take": frozenset({"action", "slot"}),
        "drink": frozenset({"action", "slot", "container_id", "operation_key"}),
        "use": frozenset({"action", "slot"}),
        "stow": frozenset({"action", "slot"}),
        "withdraw": frozenset({"action", "slot"}),
    }
    extra = sorted(set(payload.keys()) - allowed_by_action[action])
    if extra:
        return 400, {"ok": False, "reason_code": f"UNKNOWN_FIELD:{','.join(extra)}"}

    rpath = Path(root)
    eff_now = _iso(now_iso) or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    if action == "drink":
        slot = str(payload.get("slot") or "drink")
        if slot != "drink":
            return 409, {"ok": False, "reason_code": "CARE_SLOT_ACTION_MISMATCH"}
        return drink_care(
            rpath,
            actor_side=actor_side,
            place_id=place_id,
            area_id=area_id,
            container_id=_optional_text(payload.get("container_id"), 80),
            operation_key=_optional_text(payload.get("operation_key"), 160),
            now_iso=eff_now,
        )

    current, state_reason = read_state(rpath)
    if state_reason is not None:
        return 503, {"ok": False, "reason_code": state_reason}

    if action in ("set_window", "set_local_position", "wear_outerwear", "hang_outerwear", "take", "use", "stow"):
        if actor_side not in ("her", "subject"):
            return 403, {"ok": False, "reason_code": "CAPABILITY_REQUIRED"}

    if action == "set_window":
        new_state, changed, error = set_window(current, payload.get("state"), eff_now)
        offer = None
    elif action == "set_local_position":
        new_state, changed, error = set_local_position(
            current, zone=payload.get("zone"), place_id=place_id, area_id=area_id, now_iso=eff_now
        )
        offer = None
    elif action == "wear_outerwear":
        new_state, changed, error = wear_outerwear(
            current,
            garment_id=payload.get("garment_id"),
            label=payload.get("label"),
            dryness=payload.get("dryness"),
            now_iso=eff_now,
        )
        offer = None
    elif action == "hang_outerwear":
        new_state, changed, error = hang_outerwear(
            current,
            garment_id=payload.get("garment_id"),
            hang_position=payload.get("hang_position"),
            dryness=payload.get("dryness"),
            now_iso=eff_now,
        )
        offer = None
    elif action == "offer":
        offer_actor = str(payload.get("actor_side") or ("her" if actor_side in ("her", "subject") else "partner"))
        if offer_actor == "subject":
            offer_actor = "her"
        if offer_actor == "her" and actor_side not in ("her", "subject"):
            return 403, {"ok": False, "reason_code": "CAPABILITY_REQUIRED"}
        if payload.get("slot") == "drink" and is_cup_occupied_by_stationery(rpath, "ceramic_mug"):
            bound_map = payload.get("bound_item") if isinstance(payload.get("bound_item"), Mapping) else {}
            if str(bound_map.get("item_instance_id") or "") == "ceramic_mug":
                return 409, {"ok": False, "reason_code": "CUP_OCCUPIED_BY_STATIONERY"}
        new_state, changed, error, offer = offer_care(
            current,
            slot=payload.get("slot"),
            kind=payload.get("kind"),
            actor_side=offer_actor,
            position=payload.get("position"),
            place_id=place_id,
            area_id=area_id,
            now_iso=eff_now,
            bound_item=payload.get("bound_item") if isinstance(payload.get("bound_item"), Mapping) else None,
        )
    elif action == "take":
        if payload.get("slot") != "drink":
            return 409, {"ok": False, "reason_code": "CARE_SLOT_ACTION_MISMATCH"}
        new_state, changed, error, offer = take_drink(current, place_id=place_id, area_id=area_id, now_iso=eff_now)
    elif action == "use":
        if payload.get("slot") != "cushion":
            return 409, {"ok": False, "reason_code": "CARE_SLOT_ACTION_MISMATCH"}
        new_state, changed, error, offer = use_cushion(
            current, pose=pose, place_id=place_id, area_id=area_id, now_iso=eff_now
        )
    elif action == "stow":
        new_state, changed, error, offer = stow_care(current, slot=payload.get("slot"), now_iso=eff_now)
    elif action == "withdraw":
        new_state, changed, error, offer = withdraw_care(current, slot=payload.get("slot"), now_iso=eff_now)
    else:
        return 400, {"ok": False, "reason_code": "CARE_ACTION_UNSUPPORTED"}

    if error is not None:
        http_code = 400 if error.endswith("_INVALID") else 409
        return http_code, {"ok": False, "reason_code": error}

    if not changed:
        return 200, {
            "ok": True,
            "status": "already_applied",
            "action": action,
            "changed": False,
            "revision": current.get("revision"),
            "offer": offer,
        }

    written = write_state(rpath, new_state)
    return 200, {
        "ok": True,
        "status": "applied",
        "action": action,
        "changed": True,
        "revision": written.get("revision"),
        "offer": offer,
    }


__all__ = [
    "ALL_SOMATIC_ACTIONS",
    "AMBIENT_IS_TEMPLATE",
    "BODY_SIGNAL_SOURCE",
    "CARE_ACTORS",
    "CARE_POSITIONS",
    "CARE_SCHEMA",
    "CARE_SLOTS",
    "COOLING_SECONDS",
    "CUSHION_KINDS",
    "CUSHION_STATES",
    "DRINK_KINDS",
    "DRINK_NUTRIENT_RECIPES",
    "DRINK_STATES",
    "DRYNESS_STATES",
    "DRYING_SECONDS",
    "GARMENT_STATES",
    "GARMENT_WINDOW_SCHEMA",
    "LOCAL_ZONES",
    "LOCATION_UNREADABLE",
    "LOCATION_UNSTABLE",
    "MAX_LABEL",
    "MODALITIES",
    "NO_POSTURE_DURATION",
    "NO_WEATHER_AUTHORITY",
    "SALIENCE",
    "SELF_DECLARED_SOURCE",
    "SIMULATION_SOURCE",
    "SOMATIC_SCHEMA",
    "STATE_FILENAME",
    "STATE_SCHEMA_VERSION",
    "TEMPERATURE_BANDS",
    "WINDOW_STATES",
    "WORLD_READ_SOURCE",
    "apply_lived_needs_relief",
    "body_context",
    "care_offering_block",
    "derive_care",
    "derive_garment",
    "drink_care",
    "empty_state",
    "garment_window_block",
    "hang_outerwear",
    "is_cup_occupied_by_stationery",
    "mark_drink_result",
    "mutate_somatic_comfort_view",
    "offer_care",
    "parse_instant",
    "read_lived_needs_snapshot",
    "read_state",
    "set_local_position",
    "set_window",
    "somatic_block",
    "state_path",
    "stow_care",
    "take_drink",
    "temperature_band",
    "use_cushion",
    "wear_outerwear",
    "withdraw_care",
    "world_context",
    "write_state",
]

#!/usr/bin/env python3
"""衣柜梳妆台 / 歌单收藏想试小列表 / 作品旧版安全并排：薄投影 + 两份显式选择 JSON。

它是什么
--------
一个**纯投影**模块，外加两份只记录「她自己显式选过什么」的轻量 JSON：

* 衣柜：只记本实例的搭配选择（homewear / outdoor 风格、发带 / 发夹、搭配呼应备注、
  blanket / cushion 换季选择）。选择是**穿搭风格**，不是 World 产权或位置；若她明确点名
  一件已购实例，服务会向真实 world_property_economy 现读并核对 OWNED / 本人 / 类别适用。
  没有相关已购商品时如实给 ``related_owned_items=[]`` + ``NO_OWNED_RELEVANT_ITEMS``，
  绝不为了演示造商品，也不按名字编产权。
* 小列表：每类最多 ``MAX_LIST_ENTRIES`` 张个人卡片（playlist / favorites / want_to_try），
  稳定 ``entry_id``、显式 ``shared``（缺省 false）、有限状态；另有最多 3 张伴侣短建议。
  默认 GET 只给 ``shared=true`` 的个人卡片与伴侣建议，不给私人数量、ID、理由或未公开备注。
* 旧版并排：**没有新 store**。它只是 ``PrivateProjectService`` 两个真实历史版本的投影：
  本人视角给有界摘要；伴侣视角只在该精确 source version 有真实 ``LOCAL_DELIVERED`` 本地
  分享时，取**已投递分享正文**做摘要，绝不用来源全文或另一个版本的分享冒充。

它不是什么
----------
* 不做全身物理、不自动换装、不声称 World 穿戴状态，不改既有 somatic 外衣穿挂。
* 不建数据库 / 权限 / 偏好推断 / 后台 timer / 任务义务；不调用阅读、模型、记忆写入。
* 不把「有一封 v2 分享」当成「v1 正文也已公开」；对比只是安全摘要并排与有界结论。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

WARDROBE_SCHEMA = "phone.home.wardrobe.v1"
POCKET_SCHEMA = "phone.pocket.lists.v1"
WORK_REVISIT_SCHEMA = "phone.pocket.work_revisit.v1"

STATE_DIRNAME = "phone"
WARDROBE_STATE_FILENAME = "wardrobe.json"
POCKET_STATE_FILENAME = "pocket_lists.json"
STATE_SCHEMA_VERSION = 1

MAX_STYLE = 120
MAX_NOTE = 120
MAX_TITLE = 80
MAX_PARTNER_TITLE = 60
MAX_PARTNER_NOTE = 120
MAX_LIST_ENTRIES = 12
MAX_PARTNER_SUGGESTIONS = 3
MAX_OPERATION_KEY = 120
MAX_EXCERPT = 600
MAX_ID_LENGTH = 128
MAX_SHARE_SCAN = 100

SLOT_NAMES = ("homewear", "outdoor", "hairband", "hairclip")
SEASON_CHOICES = ("summer", "spring_autumn", "winter")
LIST_KINDS = ("playlist", "favorites", "want_to_try")
PLAYLIST_STATUSES = ("active", "resting")
FAVORITE_STATUSES = ("saved_unread", "reviewed")
WANT_STATUSES = ("active", "paused", "dropped", "done")
LIST_STATUSES = {
    "playlist": PLAYLIST_STATUSES,
    "favorites": FAVORITE_STATUSES,
    "want_to_try": WANT_STATUSES,
}
DEFAULT_STATUS = {"playlist": "active", "favorites": "saved_unread",
                  "want_to_try": "active"}
#: statuses that belong to *another* list are refused by name, never coerced.
ALL_STATUSES = frozenset(PLAYLIST_STATUSES + FAVORITE_STATUSES + WANT_STATUSES)

#: Explicitly declared standard wearable catalog_id mappings per wardrobe slot.
#: Non-wearable items (snacks such as ``virtual_snack_wafer`` or room furniture) are strictly
#: excluded so an owned snack or chair is never accepted as clothing or a hair accessory.
STANDARD_WEARABLE_CATALOG: dict[str, dict[str, Any]] = {
    "homewear_knit_set": {"slot": "homewear", "label_cn": "宽松针织家居套装", "wearable": True},
    "homewear_linen_pajamas": {"slot": "homewear", "label_cn": "棉麻长袖睡衣", "wearable": True},
    "homewear_soft_cardigan": {"slot": "homewear", "label_cn": "居家软绒开衫", "wearable": True},
    "cotton_loungewear": {"slot": "homewear", "label_cn": "纯棉居家便服", "wearable": True},
    "outdoor_coat_dark": {"slot": "outdoor", "label_cn": "深色外出长外套", "wearable": True},
    "outdoor_light_jacket": {"slot": "outdoor", "label_cn": "轻便防风短外套", "wearable": True},
    "outdoor_rain_cape": {"slot": "outdoor", "label_cn": "连帽防雨斗篷", "wearable": True},
    "linen_shirt_dress": {"slot": "outdoor", "label_cn": "亚麻外出衬衫裙", "wearable": True},
    "hairband_thin_ribbon": {"slot": "hairband", "label_cn": "素色细发带", "wearable": True},
    "hairband_linen_wrap": {"slot": "hairband", "label_cn": "棉麻宽发带", "wearable": True},
    "hairband_velvet": {"slot": "hairband", "label_cn": "丝绒发箍", "wearable": True},
    "ribbon_hairband": {"slot": "hairband", "label_cn": "缎带发带", "wearable": True},
    "hairclip_wooden_pin": {"slot": "hairclip", "label_cn": "原木一字发夹", "wearable": True},
    "hairclip_brass_barrette": {"slot": "hairclip", "label_cn": "黄铜边夹", "wearable": True},
    "hairclip_shell": {"slot": "hairclip", "label_cn": "贝母小抓夹", "wearable": True},
    "ribbon_clip": {"slot": "hairclip", "label_cn": "丝带发夹", "wearable": True},
}

WARDROBE_DEFAULT_SLOT_CATALOG_ID: dict[str, str] = {
    "homewear": "homewear_knit_set",
    "outdoor": "outdoor_coat_dark",
    "hairband": "hairband_thin_ribbon",
    "hairclip": "hairclip_wooden_pin",
}

_STYLE_KEYWORD_CATALOG_HINTS: dict[str, tuple[tuple[tuple[str, ...], str], ...]] = {
    "homewear": (
        (("棉麻", "睡衣", "linen", "pajama"), "homewear_linen_pajamas"),
        (("开衫", "绒", "cardigan"), "homewear_soft_cardigan"),
        (("纯棉", "便服", "cotton"), "cotton_loungewear"),
        (("针织", "knit"), "homewear_knit_set"),
    ),
    "outdoor": (
        (("雨", "斗篷", "rain"), "outdoor_rain_cape"),
        (("衬衫", "裙", "linen", "dress"), "linen_shirt_dress"),
        (("轻便", "短外套", "夹克", "jacket"), "outdoor_light_jacket"),
        (("深色", "外套", "大衣", "coat"), "outdoor_coat_dark"),
    ),
    "hairband": (
        (("棉麻", "宽", "linen"), "hairband_linen_wrap"),
        (("丝绒", "发箍", "velvet"), "hairband_velvet"),
        (("缎带", "丝带", "ribbon"), "ribbon_hairband"),
        (("细", "发带", "thin"), "hairband_thin_ribbon"),
    ),
    "hairclip": (
        (("黄铜", "金属", "brass"), "hairclip_brass_barrette"),
        (("贝母", "抓夹", "shell"), "hairclip_shell"),
        (("丝带", "蝴蝶结", "ribbon"), "ribbon_clip"),
        (("木", "wood"), "hairclip_wooden_pin"),
    ),
}

WARDROBE_ITEM_CATALOG_IDS: dict[str, frozenset[str]] = {
    slot: frozenset(
        cid for cid, meta in STANDARD_WEARABLE_CATALOG.items() if meta.get("slot") == slot
    )
    for slot in SLOT_NAMES
}


def resolve_wardrobe_style_catalog_id(
    slot: str,
    style: Optional[str],
    *,
    check: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Map an explicit wardrobe slot item/style selection to a standard wearable ``catalog_id``."""
    if slot not in SLOT_NAMES:
        return None
    if isinstance(check, Mapping) and check.get("status") == "verified":
        verified_cid = str(check.get("catalog_item_id") or check.get("catalog_id") or "").strip()
        if verified_cid:
            return verified_cid
    if not isinstance(style, str) or not style.strip():
        return None
    clean = style.strip()
    lowered = clean.lower()
    if clean in WARDROBE_ITEM_CATALOG_IDS.get(slot, frozenset()):
        return clean
    for keywords, target_cid in _STYLE_KEYWORD_CATALOG_HINTS.get(slot, ()):
        if any(kw in clean or kw in lowered for kw in keywords):
            return target_cid
    return WARDROBE_DEFAULT_SLOT_CATALOG_ID.get(slot)

#: Flat set fields -> (slot, attribute) or a direct selection key.
SLOT_FIELDS = {
    "homewear_style": ("homewear", "style"),
    "homewear_item_instance_id": ("homewear", "item_instance_id"),
    "outdoor_style": ("outdoor", "style"),
    "outdoor_item_instance_id": ("outdoor", "item_instance_id"),
    "hairband_style": ("hairband", "style"),
    "hairband_item_instance_id": ("hairband", "item_instance_id"),
    "hairclip_style": ("hairclip", "style"),
    "hairclip_item_instance_id": ("hairclip", "item_instance_id"),
}
DIRECT_FIELDS = ("blanket_season", "cushion_season", "pairing_note")
WARDROBE_SET_FIELDS = tuple(SLOT_FIELDS) + DIRECT_FIELDS
WARDROBE_CLEAR_FIELDS = frozenset(WARDROBE_SET_FIELDS)

NO_OWNED_RELEVANT_ITEMS = "NO_OWNED_RELEVANT_ITEMS"
GENESIS_NOT_INITIALIZED = "GENESIS_NOT_INITIALIZED"
ECONOMY_UNAVAILABLE = "ECONOMY_UNAVAILABLE"
ECONOMY_STATE_ERROR = "ECONOMY_STATE_ERROR"

VERSION_NOT_DELIVERED = "VERSION_NOT_DELIVERED"
VERSION_NOT_IN_ARTIFACT = "VERSION_NOT_IN_ARTIFACT"
ARCHIVED_SOURCE = "ARCHIVED_SOURCE"
SOURCE_NOT_READABLE = "SOURCE_NOT_READABLE"
NOT_A_PROJECT = "NOT_A_PROJECT"
SHARES_UNAVAILABLE = "SHARES_UNAVAILABLE"
VERSION_UNREADABLE = "VERSION_UNREADABLE"

OWNER_EXCERPT_BASIS = "owner_version_excerpt"
DELIVERED_SHARE_BASIS = "delivered_share_text"


class PocketStateError(ValueError):
    """One explicit state transition the pure layer refuses by name."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_body(value: Any, limit: int) -> str:
    return str(value or "")[:limit].strip()


def _iso_text(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:40]


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _strict_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise PocketStateError("POCKET_SHARED_INVALID")
    return value


def _text_field(value: Any, limit: int, reason: str) -> str:
    if not isinstance(value, str):
        raise PocketStateError(reason)
    text = value.strip()
    if not text or len(text) > limit:
        raise PocketStateError(reason)
    if any(ch in text for ch in ("\x00", "\r")):
        raise PocketStateError(reason)
    return text


def _item_id(value: Any) -> str:
    if not isinstance(value, str):
        raise PocketStateError("WARDROBE_ITEM_ID_INVALID")
    text = value.strip()
    if not text or len(text) > MAX_ID_LENGTH:
        raise PocketStateError("WARDROBE_ITEM_ID_INVALID")
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "\\", "?", "#")):
        raise PocketStateError("WARDROBE_ITEM_ID_INVALID")
    return text


def _choice(value: Any, allowed: Iterable[str], reason: str) -> str:
    if not isinstance(value, str) or value not in tuple(allowed):
        raise PocketStateError(reason)
    return value


# ---------------------------------------------------------------------------------------------
# atomic state files: GET never creates anything; corrupt state fails closed
# ---------------------------------------------------------------------------------------------


def wardrobe_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / WARDROBE_STATE_FILENAME


def pocket_state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / POCKET_STATE_FILENAME


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _read_json(path: Path) -> tuple[Any, Optional[str]]:
    if path.is_symlink():
        return None, "STATE_SYMLINK"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError):
        return None, "STATE_UNREADABLE"


# ---------------------------------------------------------------------------------------------
# wardrobe state
# ---------------------------------------------------------------------------------------------


def empty_wardrobe_state() -> dict[str, Any]:
    selection: dict[str, Any] = {
        "homewear": {"style": None, "item_instance_id": None},
        "outdoor": {"style": None, "item_instance_id": None},
        "hairband": {"style": None, "item_instance_id": None},
        "hairclip": {"style": None, "item_instance_id": None},
        "blanket_season": None,
        "cushion_season": None,
        "pairing_note": None,
    }
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": 0,
            "updated_at": None, "selection": selection}


def _validate_selection(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise PocketStateError("WARDROBE_STATE_INVALID")
    unknown = sorted(set(raw) - set(SLOT_NAMES) - set(DIRECT_FIELDS))
    if unknown:
        raise PocketStateError("WARDROBE_STATE_INVALID")
    selection: dict[str, Any] = {}
    for slot in SLOT_NAMES:
        if slot in raw and not isinstance(raw.get(slot), Mapping):
            raise PocketStateError("WARDROBE_STATE_INVALID")
        block = raw.get(slot) if isinstance(raw.get(slot), Mapping) else {}
        unknown_slot = sorted(set(block) - {"style", "item_instance_id"})
        if unknown_slot:
            raise PocketStateError("WARDROBE_STATE_INVALID")
        style = block.get("style")
        item_id = block.get("item_instance_id")
        if style is not None:
            if not isinstance(style, str):
                raise PocketStateError("WARDROBE_STATE_INVALID")
            style = _one_line(style, MAX_STYLE) or None
        if item_id is not None:
            item_id = _item_id(item_id)
        selection[slot] = {"style": style, "item_instance_id": item_id}
    for field in DIRECT_FIELDS:
        value = raw.get(field)
        if value is None:
            selection[field] = None
        elif field == "pairing_note":
            if not isinstance(value, str):
                raise PocketStateError("WARDROBE_STATE_INVALID")
            selection[field] = _one_line(value, MAX_NOTE) or None
        else:
            selection[field] = _choice(value, SEASON_CHOICES, "WARDROBE_STATE_INVALID")
    return selection


def _validate_wardrobe(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise PocketStateError("WARDROBE_STATE_INVALID")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise PocketStateError("WARDROBE_STATE_INVALID")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PocketStateError("WARDROBE_STATE_INVALID")
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": int(revision),
            "updated_at": _iso_text(raw.get("updated_at")),
            "selection": _validate_selection(raw.get("selection") or {})}


def read_wardrobe_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(wardrobe_state_path(root))
    if reason is not None:
        return empty_wardrobe_state(), "WARDROBE_" + reason
    if raw is None:
        return empty_wardrobe_state(), None
    try:
        return _validate_wardrobe(raw), None
    except PocketStateError:
        return empty_wardrobe_state(), "WARDROBE_STATE_INVALID"


def write_wardrobe_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _validate_wardrobe(dict(state))
    _write_json(wardrobe_state_path(root), normalized)
    return normalized


def _bump(state: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    out = _validate_wardrobe(dict(state))
    out["revision"] = int(out["revision"]) + 1
    out["updated_at"] = _iso_text(now_iso)
    return out


def apply_wardrobe_set(state: Mapping[str, Any], values: Mapping[str, Any],
                       now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_wardrobe(dict(state))
    changed = False
    for field, value in values.items():
        if field not in WARDROBE_SET_FIELDS:
            raise PocketStateError("WARDROBE_FIELD_UNSUPPORTED")
        if field in SLOT_FIELDS:
            slot, attribute = SLOT_FIELDS[field]
            if attribute == "style":
                clean: Any = _text_field(value, MAX_STYLE, "WARDROBE_STYLE_REQUIRED")
            else:
                clean = _item_id(value)
            if current["selection"][slot][attribute] != clean:
                current["selection"][slot][attribute] = clean
                changed = True
        elif field == "pairing_note":
            clean = _text_field(value, MAX_NOTE, "WARDROBE_NOTE_REQUIRED")
            if current["selection"][field] != clean:
                current["selection"][field] = clean
                changed = True
        else:
            clean = _choice(value, SEASON_CHOICES, "WARDROBE_SEASON_INVALID")
            if current["selection"][field] != clean:
                current["selection"][field] = clean
                changed = True
    if not changed:
        return current, False
    return _bump(current, now_iso), True


def apply_wardrobe_clear(state: Mapping[str, Any], fields: Iterable[Any],
                         now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_wardrobe(dict(state))
    names: list[str] = []
    for field in fields:
        if not isinstance(field, str) or field not in WARDROBE_CLEAR_FIELDS:
            raise PocketStateError("WARDROBE_CLEAR_FIELD_INVALID")
        if field in names:
            raise PocketStateError("WARDROBE_CLEAR_FIELD_DUPLICATED")
        names.append(field)
    if not names:
        raise PocketStateError("WARDROBE_CLEAR_FIELDS_REQUIRED")
    changed = False
    for field in names:
        if field in SLOT_FIELDS:
            slot, attribute = SLOT_FIELDS[field]
            if current["selection"][slot][attribute] is not None:
                current["selection"][slot][attribute] = None
                changed = True
        elif current["selection"][field] is not None:
            current["selection"][field] = None
            changed = True
    if not changed:
        return current, False
    return _bump(current, now_iso), True


def owned_item_check(economy_state: Optional[Mapping[str, Any]], *, subject: str, slot: str,
                     item_instance_id: str, catalog_ids: Optional[Mapping[str, Iterable[str]]]
                     = None) -> dict[str, Any]:
    """Verify one explicitly named owned instance against the real economy document.

    ``catalog_ids`` lets a pure test override the applicable wearable vocabulary; the
    production call passes ``None`` and uses ``WARDROBE_ITEM_CATALOG_IDS``.
    """

    applicable = (catalog_ids or WARDROBE_ITEM_CATALOG_IDS).get(slot) or ()
    base = {"status": "rejected", "reason_code": None, "slot": slot,
            "catalog_item_id": None, "catalog_id": None,
            "item_instance_id": item_instance_id}
    if economy_state is None:
        base["reason_code"] = GENESIS_NOT_INITIALIZED
        return base
    items = economy_state.get("items") if isinstance(economy_state.get("items"), list) else []
    found = next((item for item in items if isinstance(item, Mapping)
                  and str(item.get("item_instance_id") or "") == str(item_instance_id)), None)
    if found is None:
        base["reason_code"] = "ITEM_NOT_FOUND"
        return base
    if str(found.get("owner") or "") != str(subject):
        base["reason_code"] = "ITEM_NOT_OWNED_BY_SUBJECT"
        return base
    if str(found.get("disposition") or "") != "OWNED":
        base["reason_code"] = "ITEM_NOT_OWNED"
        return base
    catalog_item_id = str(found.get("catalog_item_id") or found.get("catalog_id") or "")
    base["catalog_item_id"] = catalog_item_id
    base["catalog_id"] = catalog_item_id
    if catalog_item_id not in set(applicable):
        base["reason_code"] = "ITEM_CATEGORY_NOT_APPLICABLE"
        return base
    base["status"] = "verified"
    return base


def related_owned_items(economy_state: Optional[Mapping[str, Any]], *, subject: str,
                        catalog_ids: Optional[Mapping[str, Iterable[str]]] = None
                        ) -> dict[str, Any]:
    """Owned instances that the declared wearable wardrobe vocabulary accepts."""

    if economy_state is None:
        return {"status": "unavailable", "reason_code": GENESIS_NOT_INITIALIZED,
                "items": [], "count": 0}
    applicable: dict[str, set[str]] = {}
    for slot, ids in (catalog_ids or WARDROBE_ITEM_CATALOG_IDS).items():
        applicable[slot] = set(ids)
    rows: list[dict[str, Any]] = []
    for item in economy_state.get("items") or ():
        if not isinstance(item, Mapping):
            continue
        if str(item.get("owner") or "") != str(subject):
            continue
        if str(item.get("disposition") or "") != "OWNED":
            continue
        catalog_item_id = str(item.get("catalog_item_id") or item.get("catalog_id") or "")
        slots = [slot for slot, ids in applicable.items() if catalog_item_id in ids]
        if not slots:
            continue
        rows.append({
            "item_instance_id": item.get("item_instance_id"),
            "catalog_item_id": catalog_item_id,
            "catalog_id": catalog_item_id,
            "applicable_slots": slots,
            "disposition": "OWNED",
        })
    if not rows:
        return {"status": "none", "reason_code": NO_OWNED_RELEVANT_ITEMS,
                "items": [], "count": 0}
    return {"status": "available", "reason_code": None, "items": rows, "count": len(rows)}


def wardrobe_block(*, state: Mapping[str, Any], state_reason: Optional[str],
                   checks: Mapping[str, Mapping[str, Any]],
                   related: Mapping[str, Any], subject_view: bool) -> dict[str, Any]:
    current = _validate_wardrobe(dict(state))
    selection = current["selection"]

    def slot_block(slot: str) -> dict[str, Any]:
        check = checks.get(slot) if isinstance(checks.get(slot), Mapping) else None
        style_val = selection[slot]["style"]
        resolved_cid = resolve_wardrobe_style_catalog_id(slot, style_val, check=check)
        block = {"style": style_val, "catalog_id": resolved_cid, "item": None}
        if check is None:
            if selection[slot]["item_instance_id"]:
                # A stored reference whose owner check could not run is never shown as trusted.
                block["item"] = {"status": "unavailable",
                                 "reason_code": str(check or "WARDROBE_ITEM_UNVERIFIED")}
            return block
        cid_val = check.get("catalog_item_id") or check.get("catalog_id")
        item = {"status": str(check.get("status") or "unavailable"),
                "reason_code": check.get("reason_code") or None,
                "catalog_item_id": cid_val,
                "catalog_id": cid_val}
        if subject_view:
            item["item_instance_id"] = check.get("item_instance_id")
        block["item"] = item
        return block

    return {
        "schema": WARDROBE_SCHEMA,
        "read_only": False,
        "view": "subject" if subject_view else "partner_default",
        "state": {"revision": _int_or_none(current.get("revision")),
                  "updated_at": current.get("updated_at"),
                  "reason_code": state_reason},
        "selection_only": True,
        "not_ownership_claim": True,
        "does_not_change_world_wear_state": True,
        "does_not_change_somatic_garment_state": True,
        "wearable_catalog_ids": {
            slot: sorted(WARDROBE_ITEM_CATALOG_IDS.get(slot, frozenset()))
            for slot in SLOT_NAMES
        },
        "outfit": {"homewear": slot_block("homewear"), "outdoor": slot_block("outdoor")},
        "accessories": {"hairband": slot_block("hairband"),
                        "hairclip": slot_block("hairclip")},
        "season_textiles": {
            "blanket": {"season": selection["blanket_season"]},
            "cushion": {"season": selection["cushion_season"]},
        },
        "pairing_note": selection["pairing_note"] if subject_view else None,
        "pairing_note_included": bool(subject_view and selection["pairing_note"]),
        # Default view shows the verification *result* (catalog category + slots), never the
        # private instance id; only her own capability gets the full reference.
        "related_owned_items": (
            [dict(row) for row in (related.get("items") or [])] if subject_view else
            [{key: value for key, value in row.items() if key != "item_instance_id"}
             for row in (related.get("items") or []) if isinstance(row, Mapping)]),
        "related_owned_items_status": {
            "status": related.get("status"), "reason_code": related.get("reason_code"),
            "count": related.get("count")},
        "no_ownership_inferred": True,
        "no_preference_inferred": True,
        "location_not_changed": True,
        "private_text_included": bool(subject_view and selection["pairing_note"]),
        "local_only": True,
        "note": "衣柜只存她显式的搭配风格与换季选择并映射标准可穿戴 catalog_id；不是 World 产权、位置或穿戴状态，"
                "不改既有 somatic 外衣穿挂；没有相关已购商品就不造商品。",
    }


# ---------------------------------------------------------------------------------------------
# pocket lists state
# ---------------------------------------------------------------------------------------------


def empty_pocket_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": 0, "updated_at": None,
            "lists": {kind: [] for kind in LIST_KINDS},
            "partner_suggestions": []}


def _entry_extra_fields(kind: str) -> tuple[str, ...]:
    return {
        "playlist": ("reason",),
        "favorites": ("expectation", "verdict"),
        "want_to_try": ("note",),
    }[kind]


def _validate_entry(kind: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise PocketStateError("POCKET_STATE_INVALID")
    allowed = {"entry_id", "title", "status", "shared", "created_at", "updated_at",
               "operation_key"} | set(_entry_extra_fields(kind))
    if set(raw) - allowed:
        raise PocketStateError("POCKET_STATE_INVALID")
    entry_id = raw.get("entry_id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise PocketStateError("POCKET_STATE_INVALID")
    title = _text_field(raw.get("title"), MAX_TITLE, "POCKET_STATE_INVALID")
    status = raw.get("status")
    if status not in LIST_STATUSES[kind]:
        raise PocketStateError("POCKET_STATE_INVALID")
    if not isinstance(raw.get("shared"), bool):
        raise PocketStateError("POCKET_STATE_INVALID")
    entry: dict[str, Any] = {
        "entry_id": entry_id.strip()[:MAX_ID_LENGTH],
        "title": title,
        "status": status,
        "shared": bool(raw.get("shared")),
        "created_at": _iso_text(raw.get("created_at")),
        "updated_at": _iso_text(raw.get("updated_at")),
        "operation_key": _iso_text(raw.get("operation_key")),
    }
    for field in _entry_extra_fields(kind):
        value = raw.get(field)
        if value is None:
            entry[field] = None
        elif not isinstance(value, str):
            raise PocketStateError("POCKET_STATE_INVALID")
        else:
            entry[field] = _one_line(value, MAX_NOTE) or None
    return entry


def _validate_suggestion(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise PocketStateError("POCKET_STATE_INVALID")
    if set(raw) - {"title", "note", "partner_contributed", "created_at"}:
        raise PocketStateError("POCKET_STATE_INVALID")
    title = _text_field(raw.get("title"), MAX_PARTNER_TITLE, "POCKET_STATE_INVALID")
    note = raw.get("note")
    if note is not None:
        note = _one_line(note, MAX_PARTNER_NOTE) or None
    return {"title": title, "note": note, "partner_contributed": True,
            "created_at": _iso_text(raw.get("created_at"))}


def _validate_pocket(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise PocketStateError("POCKET_STATE_INVALID")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise PocketStateError("POCKET_STATE_INVALID")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PocketStateError("POCKET_STATE_INVALID")
    lists_raw = raw.get("lists") if isinstance(raw.get("lists"), Mapping) else {}
    if set(lists_raw) - set(LIST_KINDS):
        raise PocketStateError("POCKET_STATE_INVALID")
    lists: dict[str, list[dict[str, Any]]] = {}
    for kind in LIST_KINDS:
        rows = lists_raw.get(kind) or []
        if not isinstance(rows, list) or len(rows) > MAX_LIST_ENTRIES:
            raise PocketStateError("POCKET_STATE_INVALID")
        lists[kind] = [_validate_entry(kind, row) for row in rows]
    suggestions_raw = raw.get("partner_suggestions") or []
    if not isinstance(suggestions_raw, list) \
            or len(suggestions_raw) > MAX_PARTNER_SUGGESTIONS:
        raise PocketStateError("POCKET_STATE_INVALID")
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": int(revision),
            "updated_at": _iso_text(raw.get("updated_at")),
            "lists": lists,
            "partner_suggestions": [_validate_suggestion(row) for row in suggestions_raw]}


def read_pocket_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(pocket_state_path(root))
    if reason is not None:
        return empty_pocket_state(), "POCKET_" + reason
    if raw is None:
        return empty_pocket_state(), None
    try:
        return _validate_pocket(raw), None
    except PocketStateError:
        return empty_pocket_state(), "POCKET_STATE_INVALID"


def write_pocket_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _validate_pocket(dict(state))
    _write_json(pocket_state_path(root), normalized)
    return normalized


def _pocket_bump(state: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    out = _validate_pocket(dict(state))
    out["revision"] = int(out["revision"]) + 1
    out["updated_at"] = _iso_text(now_iso)
    return out


def new_entry_id(kind: str) -> str:
    prefix = {"playlist": "pl", "favorites": "fa", "want_to_try": "wt"}[kind]
    return "%s_%s" % (prefix, os.urandom(8).hex())


def _find_entry(state: Mapping[str, Any], kind: str, entry_id: str
                ) -> Optional[dict[str, Any]]:
    return next((row for row in state["lists"][kind]
                 if row.get("entry_id") == entry_id), None)


def pocket_add(state: Mapping[str, Any], *, kind: str, entry_id: str, title: str,
               values: Mapping[str, Any], status: Optional[str], shared: Optional[bool],
               operation_key: str, now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_pocket(dict(state))
    if kind not in LIST_KINDS:
        raise PocketStateError("POCKET_LIST_INVALID")
    if isinstance(operation_key, str) and operation_key:
        for existing_kind in LIST_KINDS:
            for existing in current["lists"][existing_kind]:
                if existing.get("operation_key") != operation_key:
                    continue
                if existing_kind == kind:
                    return current, False
                raise PocketStateError("POCKET_OPERATION_KEY_REUSED")
    if len(current["lists"][kind]) >= MAX_LIST_ENTRIES:
        raise PocketStateError("POCKET_LIST_FULL")
    chosen_status = DEFAULT_STATUS[kind] if status is None else status
    if chosen_status not in LIST_STATUSES[kind]:
        raise PocketStateError("POCKET_STATUS_NOT_FOR_LIST")
    entry = _validate_entry(kind, {
        "entry_id": entry_id,
        "title": _text_field(title, MAX_TITLE, "POCKET_TITLE_REQUIRED"),
        "status": chosen_status,
        "shared": bool(shared) if shared is not None else False,
        "created_at": _iso_text(now_iso),
        "updated_at": _iso_text(now_iso),
        "operation_key": _text_field(operation_key, MAX_OPERATION_KEY,
                                     "POCKET_OPERATION_KEY_REQUIRED"),
        **dict(values),
    })
    current["lists"][kind].append(entry)
    return _pocket_bump(current, now_iso), True


def pocket_update(state: Mapping[str, Any], *, kind: str, entry_id: str,
                  values: Mapping[str, Any], status: Optional[str],
                  shared: Optional[bool], now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_pocket(dict(state))
    entry = _find_entry(current, kind, entry_id)
    if entry is None:
        raise PocketStateError("POCKET_ENTRY_NOT_FOUND")
    allowed = set(_entry_extra_fields(kind)) | {"title", "status", "shared"}
    if set(values) - allowed:
        raise PocketStateError("POCKET_FIELD_NOT_FOR_LIST")
    changed = False
    if "title" in values:
        title = _text_field(values["title"], MAX_TITLE, "POCKET_TITLE_REQUIRED")
        if entry["title"] != title:
            entry["title"] = title
            changed = True
    for field in _entry_extra_fields(kind):
        if field in values:
            value = _one_line(
                _text_field(values[field], MAX_NOTE,
                            "POCKET_%s_INVALID" % field.upper()), MAX_NOTE)
            if entry[field] != value:
                entry[field] = value
                changed = True
    if status is not None:
        if status not in LIST_STATUSES[kind]:
            raise PocketStateError("POCKET_STATUS_NOT_FOR_LIST")
        if entry["status"] != status:
            entry["status"] = status
            changed = True
    if shared is not None:
        if entry["shared"] != shared:
            entry["shared"] = bool(shared)
            changed = True
    if not changed:
        return current, False
    entry["updated_at"] = _iso_text(now_iso)
    return _pocket_bump(current, now_iso), True


def pocket_remove(state: Mapping[str, Any], *, kind: str, entry_id: str,
                  now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_pocket(dict(state))
    if kind not in LIST_KINDS:
        raise PocketStateError("POCKET_LIST_INVALID")
    kept = [row for row in current["lists"][kind] if row.get("entry_id") != entry_id]
    if len(kept) == len(current["lists"][kind]):
        return current, False
    current["lists"][kind] = kept
    return _pocket_bump(current, now_iso), True


def pocket_set_partner_suggestions(state: Mapping[str, Any], suggestions: Iterable[Any],
                                   now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_pocket(dict(state))
    rows = list(suggestions)
    if len(rows) > MAX_PARTNER_SUGGESTIONS:
        raise PocketStateError("POCKET_PARTNER_TOO_MANY")
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise PocketStateError("POCKET_PARTNER_SUGGESTION_INVALID")
        note = None
        if row.get("note") is not None:
            if not isinstance(row.get("note"), str):
                raise PocketStateError("POCKET_PARTNER_NOTE_INVALID")
            note = _one_line(row.get("note"), MAX_PARTNER_NOTE) or None
        normalized.append(_validate_suggestion({
            "title": _text_field(row.get("title"), MAX_PARTNER_TITLE,
                                 "POCKET_PARTNER_TITLE_REQUIRED"),
            "note": note,
            "partner_contributed": True,
            "created_at": now_iso,
        }))
    # Same content (titles + notes) is the same intent: created_at alone never forces a bump.
    if [(row["title"], row["note"]) for row in normalized] == \
            [(row["title"], row["note"]) for row in current["partner_suggestions"]]:
        return current, False
    current["partner_suggestions"] = normalized
    return _pocket_bump(current, now_iso), True


def pocket_block(*, state: Mapping[str, Any], state_reason: Optional[str],
                 subject_view: bool) -> dict[str, Any]:
    current = _validate_pocket(dict(state))
    lists: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    for kind in LIST_KINDS:
        rows = current["lists"][kind]
        visible = rows if subject_view else [row for row in rows if row["shared"]]
        if subject_view:
            lists[kind] = [dict(row) for row in visible]
        else:
            # Default view: a shared title and its finite status only.  No entry id, reason,
            # expectation, verdict, note, operation key or private count.
            lists[kind] = [{"title": row["title"], "status": row["status"], "shared": True}
                           for row in visible]
        counts[kind] = len(visible)
    suggestions = [{"title": row["title"], "note": row["note"],
                    "partner_contributed": True}
                   for row in current["partner_suggestions"]]
    private_fields = any(
        row.get("reason") or row.get("expectation") or row.get("verdict")
        or row.get("note") for kind in LIST_KINDS for row in current["lists"][kind])
    return {
        "schema": POCKET_SCHEMA,
        "read_only": False,
        "view": "subject" if subject_view else "partner_default",
        "state": {"revision": _int_or_none(current.get("revision")),
                  "updated_at": current.get("updated_at"),
                  "reason_code": state_reason},
        "lists": lists,
        "counts": counts,
        "partner_suggestions": suggestions,
        "partner_suggestion_count": len(suggestions),
        "statuses": {kind: list(LIST_STATUSES[kind]) for kind in LIST_KINDS},
        "shared_default": False,
        "shared_set_explicitly_by_her": True,
        "counts_are_visible_only": not subject_view,
        "no_obligation": True,
        "no_future_items_created": True,
        "reading_or_model_calls": False,
        "memory_writes": False,
        "private_text_included": bool(subject_view and private_fields),
        "local_only": True,
        "note": "默认 GET 只给 shared=true 的个人卡片与伴侣建议；shared=false 立即扣留。"
                "resting 保留喜欢理由，reviewed 只是本人显式自报回看，"
                "paused/dropped/done 不产生任何义务或 FutureItem。",
    }


# ---------------------------------------------------------------------------------------------
# work revisit: two real versions of one work, safe side-by-side summaries
# ---------------------------------------------------------------------------------------------


def text_sha256(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def version_excerpt(value: Any, limit: int = MAX_EXCERPT) -> dict[str, Any]:
    text = value if isinstance(value, str) else ""
    excerpt = text[:limit]
    return {"excerpt": excerpt, "excerpt_chars": len(excerpt),
            "total_chars": len(text), "excerpt_truncated": len(text) > len(excerpt)}


def owner_version_side(*, version: Mapping[str, Any], version_id: str,
                       lifecycle: str) -> dict[str, Any]:
    content = version.get("content") if isinstance(version.get("content"), str) else ""
    excerpt = version_excerpt(content)
    return {
        "version_id": version_id,
        "version_number": _int_or_none(version.get("version_number")),
        "status": "available",
        "reason_code": None,
        "excerpt_basis": OWNER_EXCERPT_BASIS,
        "excerpt": excerpt["excerpt"],
        "excerpt_chars": excerpt["excerpt_chars"],
        "excerpt_truncated": excerpt["excerpt_truncated"],
        "content_sha256": version.get("content_sha256"),
        "version_lifecycle": lifecycle,
        "source_work_body_included": False,
        "delivered_share_id": None,
        "delivered_at": None,
    }


def delivered_share_side(*, version_id: str, version_number: Optional[int],
                         share_id: str, text: str, delivered_at: Any) -> dict[str, Any]:
    excerpt = version_excerpt(text)
    return {
        "version_id": version_id,
        "version_number": _int_or_none(version_number),
        "status": "available",
        "reason_code": None,
        "excerpt_basis": DELIVERED_SHARE_BASIS,
        "excerpt": excerpt["excerpt"],
        "excerpt_chars": excerpt["excerpt_chars"],
        "excerpt_truncated": excerpt["excerpt_truncated"],
        "content_sha256": text_sha256(text),
        "version_lifecycle": None,
        "source_work_body_included": False,
        "delivered_share_id": share_id,
        "delivered_at": _iso_text(delivered_at),
    }


def withheld_side(*, version_id: str, version_number: Optional[int], reason_code: str,
                  excerpt_basis: str) -> dict[str, Any]:
    return {
        "version_id": version_id,
        "version_number": _int_or_none(version_number),
        "status": "withheld",
        "reason_code": str(reason_code),
        "excerpt_basis": excerpt_basis,
        "excerpt": None,
        "excerpt_chars": 0,
        "excerpt_truncated": False,
        "content_sha256": None,
        "version_lifecycle": None,
        "source_work_body_included": False,
        "delivered_share_id": None,
        "delivered_at": None,
    }


def comparison_block(*, left: Mapping[str, Any], right: Mapping[str, Any],
                     subject_view: bool) -> dict[str, Any]:
    """A bounded comparison: no diff fragments, only a named, scoped conclusion."""

    available = left.get("status") == "available" and right.get("status") == "available"
    left_hash = left.get("content_sha256") if available else None
    right_hash = right.get("content_sha256") if available else None
    if not available:
        return {"changed": None, "reason_code": "COMPARISON_SIDE_WITHHELD",
                "basis": None, "scope": None, "source_text_change_provable": False,
                "left_chars": left.get("excerpt_chars"), "right_chars": right.get("excerpt_chars")}
    if left_hash and right_hash:
        changed = left_hash != right_hash
        return {
            "changed": bool(changed),
            "reason_code": None,
            "basis": ("owner_content_sha256" if subject_view
                      else "delivered_share_text_sha256"),
            "scope": ("full_version_content" if subject_view
                      else "delivered_share_text_only"),
            "source_text_change_provable": bool(subject_view),
            "left_chars": left.get("excerpt_chars"),
            "right_chars": right.get("excerpt_chars"),
            "diff_fragments_included": False,
        }
    return {"changed": None, "reason_code": "COMPARISON_HASH_UNAVAILABLE",
            "basis": None, "scope": None, "source_text_change_provable": False,
            "left_chars": left.get("excerpt_chars"),
            "right_chars": right.get("excerpt_chars"),
            "diff_fragments_included": False}


def work_revisit_block(*, artifact_id: str, subject_view: bool, left: Mapping[str, Any],
                       right: Mapping[str, Any], artifact_status: str = "available",
                       artifact_reason: Optional[str] = None) -> dict[str, Any]:
    withheld = (artifact_status != "available" or left.get("status") != "available"
                or right.get("status") != "available")
    return {
        "schema": WORK_REVISIT_SCHEMA,
        "read_only": True,
        "view": "subject" if subject_view else "partner_default",
        "artifact": {"artifact_id": artifact_id, "status": artifact_status,
                     "reason_code": artifact_reason},
        "versions": {"left": dict(left), "right": dict(right)},
        "comparison": comparison_block(left=left, right=right, subject_view=subject_view),
        "withheld": bool(withheld),
        "excerpt_basis": (OWNER_EXCERPT_BASIS if subject_view else DELIVERED_SHARE_BASIS),
        "private_text_included": bool(
            not withheld and (subject_view or bool(left.get("delivered_share_id")
                                                   or right.get("delivered_share_id")))),
        "new_share_created": False,
        "permission_granted": False,
        "local_only": True,
        "note": "本人视角读两个真实版本的有界摘要；伴侣视角只读该精确版本已本地投递的分享"
                "正文摘要。有一封更新版本的分享，不代表更早版本的正文已经公开。",
    }


__all__ = [
    "ARCHIVED_SOURCE",
    "DELIVERED_SHARE_BASIS",
    "DIRECT_FIELDS",
    "ECONOMY_STATE_ERROR",
    "ECONOMY_UNAVAILABLE",
    "FAVORITE_STATUSES",
    "GENESIS_NOT_INITIALIZED",
    "LIST_KINDS",
    "LIST_STATUSES",
    "MAX_EXCERPT",
    "MAX_LIST_ENTRIES",
    "MAX_NOTE",
    "MAX_PARTNER_NOTE",
    "MAX_PARTNER_SUGGESTIONS",
    "MAX_PARTNER_TITLE",
    "MAX_SHARE_SCAN",
    "MAX_STYLE",
    "MAX_TITLE",
    "NO_OWNED_RELEVANT_ITEMS",
    "NOT_A_PROJECT",
    "OWNER_EXCERPT_BASIS",
    "PLAYLIST_STATUSES",
    "POCKET_SCHEMA",
    "POCKET_STATE_FILENAME",
    "PocketStateError",
    "SEASON_CHOICES",
    "SHARES_UNAVAILABLE",
    "SLOT_FIELDS",
    "SLOT_NAMES",
    "SOURCE_NOT_READABLE",
    "VERSION_NOT_DELIVERED",
    "VERSION_NOT_IN_ARTIFACT",
    "VERSION_UNREADABLE",
    "WARDROBE_CLEAR_FIELDS",
    "WARDROBE_ITEM_CATALOG_IDS",
    "WARDROBE_SCHEMA",
    "WARDROBE_SET_FIELDS",
    "WARDROBE_STATE_FILENAME",
    "WANT_STATUSES",
    "WORK_REVISIT_SCHEMA",
    "apply_wardrobe_clear",
    "apply_wardrobe_set",
    "comparison_block",
    "delivered_share_side",
    "empty_pocket_state",
    "empty_wardrobe_state",
    "new_entry_id",
    "owned_item_check",
    "owner_version_side",
    "pocket_add",
    "pocket_block",
    "pocket_remove",
    "pocket_set_partner_suggestions",
    "pocket_state_path",
    "pocket_update",
    "read_pocket_state",
    "read_wardrobe_state",
    "related_owned_items",
    "text_sha256",
    "version_excerpt",
    "wardrobe_block",
    "wardrobe_state_path",
    "withheld_side",
    "work_revisit_block",
    "write_pocket_state",
    "write_wardrobe_state",
]

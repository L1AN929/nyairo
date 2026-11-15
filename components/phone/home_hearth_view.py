#!/usr/bin/env python3
"""居家收官：重逢锚点、夜茶菜谱、待定小约定：一份薄投影 + 三份显式状态。

它是什么
--------
一个**只投影既有 owner 事实**的薄模块，外加三份记录「她/他们显式留下过什么」的轻量 JSON：

* 重逢锚点（``phone/reunion_anchor.json``）：只在一次显式 ``capture`` 时，把当时**可披露**
  的安全事实存成一张快照——本地时间与时钟来源、World 可信位置区域、读物稳定身份
  （artifact + source version）与停点页码、**已本地投递**分享的稳定 id 集合、已购物件实例
  的安全名称与摆放摘要。它不存任何正文、页注、未投递分享标题，也不是一份日志。
  GET 用真实 id 集合比对：新投递/新物件/真实摆放变化；读物换身份就说不可比，
  页码未知就不猜，位置未知就不猜搬动，购买不冒充已送达/已吃过。
* 夜茶菜谱（``phone/home_recipes.json``）：双方显式写的共同菜谱卡（最多 12 张），
  ``author_side`` 只选择写入口的验证策略，不授予她身份。GET 只附带 owner 真实购买/消费
  回执的有界参考区，并明确 ``not_evidence_of_cooking`` / ``consumption_claimed=false``：
  同名不等于吃过。
* 待定小约定（``phone/tentative_plans.json``）：最多 6 条显式暂定项，状态只有
  ``TENTATIVE`` / ``RESCHEDULED`` / ``DROPPED``；只能提/改期/放下，不造 ACCEPTED。
  每项 ``auto_hijack_activity=false`` / ``no_obligation=true``，绝不导入今日安排、
  canonical Activity、FutureItems 或 Memory。GET 把「正式今日安排的安全摘要」与
  「本地暂定项」分开列，暂定永远不是今日安排。

它不是什么
----------
* 不复制正文/图片/分享/作品到新库，不建 ACL/token/后台 timer，不产生义务。
* 不因购买就冒充送达，不因同名就冒充吃过，不把缺失源当成 0 再算「新增」。
* 不写 Memory / Belief / 经历 / 认知，不调用模型，不改 inbox 已读，不伪造到期执行。
* GET 一律纯读：不 tick、不 materialize、不 ensure 任何文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional

REUNION_SCHEMA = "phone.home.reunion_anchor.v1"
RECIPES_SCHEMA = "phone.home.recipes.v1"
PLANS_SCHEMA = "phone.home.tentative_plans.v1"

STATE_DIRNAME = "phone"
REUNION_FILENAME = "reunion_anchor.json"
RECIPES_FILENAME = "home_recipes.json"
PLANS_FILENAME = "tentative_plans.json"
STATE_SCHEMA_VERSION = 1

MAX_RECIPES = 12
MAX_RECIPE_TITLE = 40
MAX_RECIPE_MEMO = 240
MAX_RECIPE_REFERENCE = 6
MAX_PLANS = 6
MAX_PLAN_TITLE = 60
MAX_PLAN_WINDOW = 40
MAX_ANCHOR_SHARES = 40
MAX_ANCHOR_ITEMS = 40

AUTHOR_SIDES = ("her", "partner")
PLAN_PROPOSERS = AUTHOR_SIDES
PLAN_STATUSES = ("TENTATIVE", "RESCHEDULED", "DROPPED")
PLAN_OPEN_STATUSES = ("TENTATIVE", "RESCHEDULED")

NO_ABSENCE_DEBT = True
NOT_EVIDENCE_OF_COOKING = True
CONSUMPTION_CLAIMED = False

#: Reasons a comparison is not a delta.  A missing/unknown source is never 0.
READING_IDENTITY_CHANGED = "READING_IDENTITY_CHANGED"
READING_UNAVAILABLE = "READING_UNAVAILABLE"
READING_POSITION_UNKNOWN = "READING_POSITION_UNKNOWN"
READING_ANCHOR_POSITION_UNKNOWN = "READING_ANCHOR_POSITION_UNKNOWN"
DELIVERED_UNAVAILABLE = "DELIVERED_UNAVAILABLE"
DELIVERED_ANCHOR_UNAVAILABLE = "DELIVERED_ANCHOR_UNAVAILABLE"
ITEMS_UNAVAILABLE = "ITEMS_UNAVAILABLE"
ITEMS_ANCHOR_UNAVAILABLE = "ITEMS_ANCHOR_UNAVAILABLE"


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "")[:limit].strip()


def _iso_text(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:40]


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _clean_title(value: Any, maximum: int) -> Optional[str]:
    text = _one_line(value, maximum)
    return text or None


def _clean_memo(value: Any, maximum: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    if any(ch in value for ch in ("\x00",)):
        return None
    text = value[:maximum].strip()
    return text or None


def stable_id(prefix: str, *parts: Any) -> str:
    """A deterministic local id from declared intent; never a path or an owner id."""

    digest = hashlib.sha1("\x00".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return "%s-%s" % (prefix, digest[:16])


# ---------------------------------------------------------------------------------------------
# atomic json state (same small local primitive as the other home views)
# ---------------------------------------------------------------------------------------------


def _state_path(root: Path | str, filename: str) -> Path:
    return Path(root) / STATE_DIRNAME / filename


def reunion_path(root: Path | str) -> Path:
    return _state_path(root, REUNION_FILENAME)


def recipes_path(root: Path | str) -> Path:
    return _state_path(root, RECIPES_FILENAME)


def plans_path(root: Path | str) -> Path:
    return _state_path(root, PLANS_FILENAME)


def _read_json(path: Path, empty, invalid_reason: str) -> tuple[dict[str, Any], Optional[str]]:
    """Missing = empty; symlink/corrupt = fail closed, never guessed."""

    if path.is_symlink():
        return empty(), invalid_reason
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty(), None
    except (OSError, ValueError):
        return empty(), invalid_reason
    if not isinstance(raw, Mapping):
        return empty(), invalid_reason
    return dict(raw), None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent),
                                             text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
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
# reunion anchor state
# ---------------------------------------------------------------------------------------------


def empty_reunion_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": 0,
            "updated_at": None, "anchor": None}


def _validate_location(raw: Any) -> dict[str, Any]:
    row = raw if isinstance(raw, Mapping) else {}
    return {
        "status": str(row.get("status") or "unknown")[:40],
        "place_id": _one_line(row.get("place_id"), 80) or None,
        "area_id": _one_line(row.get("area_id"), 80) or None,
        "home": row.get("home") if isinstance(row.get("home"), bool) else None,
        "reason_code": _one_line(row.get("reason_code"), 120) or None,
    }


def _validate_reading(raw: Any) -> dict[str, Any]:
    row = raw if isinstance(raw, Mapping) else {}
    page = _int_or_none(row.get("page"))
    pages = _int_or_none(row.get("pages"))
    return {
        "status": str(row.get("status") or "unavailable")[:40],
        "artifact_id": _one_line(row.get("artifact_id"), 200) or None,
        "source_revision": _one_line(row.get("source_revision"), 200) or None,
        "version_number": _int_or_none(row.get("version_number")),
        "page": page if page is not None and page > 0 else None,
        "pages": pages if pages is not None and pages > 0 else None,
        "page_status": "known" if page is not None and page > 0 else "unknown",
        "reason_code": _one_line(row.get("reason_code"), 120) or None,
    }


def _validate_delivered(raw: Any) -> dict[str, Any]:
    row = raw if isinstance(raw, Mapping) else {}
    ids_raw = row.get("share_ids")
    ids: list[str] = []
    if isinstance(ids_raw, list):
        for item in ids_raw:
            text = _one_line(item, 200)
            if text and text not in ids:
                ids.append(text)
    return {
        "status": str(row.get("status") or "unavailable")[:40],
        "share_ids": ids[:MAX_ANCHOR_SHARES],
        "count": _int_or_none(row.get("count")),
        "truncated": bool(row.get("truncated")),
        "reason_code": _one_line(row.get("reason_code"), 120) or None,
    }


def _validate_placement(raw: Any) -> dict[str, Any]:
    row = raw if isinstance(raw, Mapping) else {}
    out = {"status": str(row.get("status") or "unknown")[:40]}
    for key in ("kind", "location_id", "area_id", "surface_id", "container_id", "slot_id",
                "from_location_id", "to_location_id", "reason_code"):
        value = row.get(key)
        if value is not None:
            out[key] = _one_line(value, 120) or None
    return out


def _validate_items(raw: Any) -> dict[str, Any]:
    row = raw if isinstance(raw, Mapping) else {}
    rows_raw = row.get("items")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(rows_raw, list):
        for entry in rows_raw:
            if not isinstance(entry, Mapping):
                continue
            item_id = _one_line(entry.get("item_instance_id"), 200)
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            items.append({"item_instance_id": item_id,
                          "name": _one_line(entry.get("name"), 80) or None,
                          "placement": _validate_placement(entry.get("placement"))})
    return {
        "status": str(row.get("status") or "unavailable")[:40],
        "items": items[:MAX_ANCHOR_ITEMS],
        "count": _int_or_none(row.get("count")),
        "truncated": bool(row.get("truncated")),
        "reason_code": _one_line(row.get("reason_code"), 120) or None,
    }


def _validate_anchor(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("invalid anchor")
    captured = _iso_text(raw.get("captured_at_local"))
    if captured is None:
        raise ValueError("invalid anchor time")
    return {
        "captured_at_local": captured,
        "clock_source": _one_line(raw.get("clock_source"), 60) or "unknown",
        "clock_offset": _one_line(raw.get("clock_offset"), 10) or None,
        "location": _validate_location(raw.get("location")),
        "reading": _validate_reading(raw.get("reading")),
        "delivered": _validate_delivered(raw.get("delivered")),
        "items": _validate_items(raw.get("items")),
        "complete": bool(raw.get("complete")),
        "no_absence_debt": NO_ABSENCE_DEBT,
    }


def _validate_reunion_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("state is not an object")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported schema")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": int(revision),
            "updated_at": _iso_text(raw.get("updated_at")),
            "anchor": _validate_anchor(raw.get("anchor"))}


def read_reunion_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(reunion_path(root), empty_reunion_state,
                             "REUNION_STATE_INVALID")
    if reason is not None:
        return raw, reason
    try:
        return _validate_reunion_state(raw), None
    except ValueError:
        return empty_reunion_state(), "REUNION_STATE_INVALID"


def write_reunion_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _validate_reunion_state(dict(state))
    _write_json(reunion_path(root), normalized)
    return normalized


def _anchor_from_snapshot(snapshot: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    clock = snapshot.get("clock") if isinstance(snapshot.get("clock"), Mapping) else {}
    anchor = {
        "captured_at_local": _iso_text(now_iso) or "1970-01-01T00:00:00+00:00",
        "clock_source": _one_line(clock.get("source"), 60) or "unknown",
        "clock_offset": _one_line(snapshot.get("clock_offset"), 10) or None,
        "location": _validate_location(snapshot.get("location")),
        "reading": _validate_reading(snapshot.get("reading")),
        "delivered": _validate_delivered(snapshot.get("delivered")),
        "items": _validate_items(snapshot.get("items")),
        "complete": bool(snapshot.get("complete")),
        "no_absence_debt": NO_ABSENCE_DEBT,
    }
    return _validate_anchor(anchor) or anchor


def _anchor_identity(anchor: Mapping[str, Any]) -> str:
    """Everything a capture names, except *when* it was taken.

    Comparing wall-clock capture times would bump the revision on every capture even when
    nothing she could see had moved.  The identity below is exactly the disclosed state.
    """

    comparable = {key: anchor.get(key) for key in
                  ("clock_source", "clock_offset", "location", "reading",
                   "delivered", "items", "complete")}
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def capture_anchor(state: Mapping[str, Any],
                   snapshot: Mapping[str, Any], now_iso: str) -> tuple[dict[str, Any], bool]:
    """Store one new safe snapshot only when the disclosed state actually changed."""

    current = _validate_reunion_state(dict(state))
    candidate = _anchor_from_snapshot(snapshot, now_iso)
    existing = current.get("anchor")
    if isinstance(existing, Mapping) \
            and _anchor_identity(existing) == _anchor_identity(candidate):
        return current, False
    current["anchor"] = candidate
    current["revision"] = int(current["revision"]) + 1
    current["updated_at"] = _iso_text(now_iso)
    return current, True


# -- reunion GET comparison ------------------------------------------------------------------


def compare_reading(anchor: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    a = _validate_reading(anchor)
    c = _validate_reading(current)
    out: dict[str, Any] = {"anchor": a, "current": c, "comparable": False,
                           "page_delta": None, "reason_code": None}
    if c["status"] != "available":
        out["reason_code"] = READING_UNAVAILABLE
        return out
    if a["status"] != "available":
        out["reason_code"] = READING_ANCHOR_POSITION_UNKNOWN
        return out
    if a["artifact_id"] != c["artifact_id"] or a["source_revision"] != c["source_revision"]:
        # A different material or a reset version is not a page delta.
        out["reason_code"] = READING_IDENTITY_CHANGED
        return out
    if a["page_status"] != "known" or c["page_status"] != "known" \
            or a["page"] is None or c["page"] is None:
        out["reason_code"] = READING_POSITION_UNKNOWN
        return out
    out["comparable"] = True
    out["page_delta"] = int(c["page"]) - int(a["page"])
    return out


def _placement_key(placement: Mapping[str, Any]) -> Optional[tuple]:
    row = _validate_placement(placement)
    if row.get("status") != "available":
        return None
    return tuple(sorted((key, row.get(key)) for key in
                        ("kind", "location_id", "area_id", "surface_id", "container_id",
                         "slot_id", "from_location_id", "to_location_id")
                        if row.get(key) is not None))


def compare_delivered(anchor: Mapping[str, Any],
                      current: Mapping[str, Any]) -> dict[str, Any]:
    a = _validate_delivered(anchor)
    c = _validate_delivered(current)
    out: dict[str, Any] = {"anchor_count": a["count"], "current_count": c["count"],
                           "new_share_ids": [], "new_count": None,
                           "comparable": False, "complete": False, "reason_code": None}
    if c["status"] != "available":
        out["reason_code"] = DELIVERED_UNAVAILABLE
        return out
    if a["status"] != "available":
        out["reason_code"] = DELIVERED_ANCHOR_UNAVAILABLE
        return out
    new_ids = [share_id for share_id in c["share_ids"] if share_id not in a["share_ids"]]
    out["new_share_ids"] = new_ids
    out["new_count"] = len(new_ids)
    out["comparable"] = True
    out["complete"] = not (a["truncated"] or c["truncated"])
    return out


def compare_items(anchor: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    a = _validate_items(anchor)
    c = _validate_items(current)
    out: dict[str, Any] = {
        "anchor_count": a["count"], "current_count": c["count"],
        "new_item_ids": [], "new_count": None,
        "moved": [], "moved_unknown": [],
        "comparable": False, "complete": False, "reason_code": None,
    }
    if c["status"] != "available":
        out["reason_code"] = ITEMS_UNAVAILABLE
        return out
    if a["status"] != "available":
        out["reason_code"] = ITEMS_ANCHOR_UNAVAILABLE
        return out
    anchor_rows = {row["item_instance_id"]: row for row in a["items"]}
    current_rows = {row["item_instance_id"]: row for row in c["items"]}
    new_ids = [item_id for item_id in current_rows if item_id not in anchor_rows]
    out["new_item_ids"] = new_ids
    out["new_count"] = len(new_ids)
    for item_id, row in current_rows.items():
        before = anchor_rows.get(item_id)
        if before is None:
            continue
        before_key = _placement_key(before.get("placement") or {})
        after_key = _placement_key(row.get("placement") or {})
        if before_key is None or after_key is None:
            out["moved_unknown"].append(item_id)
        elif before_key != after_key:
            out["moved"].append({"item_instance_id": item_id, "name": row.get("name"),
                                 "from": before.get("placement"),
                                 "to": row.get("placement")})
    out["comparable"] = True
    out["complete"] = not (a["truncated"] or c["truncated"])
    return out


def reunion_block(*, state: Mapping[str, Any], state_reason: Optional[str],
                  snapshot: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    """GET projection: the stored anchor versus the owners' current safe facts."""

    normalized = _validate_reunion_state(dict(state))
    anchor = normalized.get("anchor")
    clock = snapshot.get("clock") if isinstance(snapshot.get("clock"), Mapping) else {}
    location = _validate_location(snapshot.get("location"))
    block: dict[str, Any] = {
        "schema": REUNION_SCHEMA,
        "read_only": True,
        "state": {"revision": _int_or_none(normalized.get("revision")),
                  "updated_at": normalized.get("updated_at"),
                  "reason_code": state_reason},
        "has_anchor": anchor is not None,
        "at_home": bool(location.get("status") == "available"
                        and str(location.get("place_id") or "") == "home"),
        "location": location,
        "clock": {"source": clock.get("source"), "logical_at": clock.get("logical_at"),
                  "reason_code": clock.get("reason_code")},
        "clock_offset": _one_line(snapshot.get("clock_offset"), 10) or None,
        "no_absence_debt": NO_ABSENCE_DEBT,
        "absence_duration_claimed": False,
        "no_auto_nudge": True,
        "relationship_score": None,
        "model_called": False,
        "anchor_written": False,
        "inbox_read_state_changed": False,
        "local_only": True,
        "note": "只存安全快照：本地时间与时钟来源、可信位置区域、读物身份+停点、"
                "已投递分享 id 集合、已购物件安全名称与摆放摘要；不含正文/页注/未发标题。"
                "GET 用真实 id 集合比对，源读不到就是 unknown，不当成 0。",
    }
    if anchor is None:
        block.update({"anchor": None, "reading": None, "delivered": None, "items": None})
        return block
    block.update({
        "anchor": {"captured_at_local": anchor["captured_at_local"],
                   "clock_source": anchor["clock_source"],
                   "clock_offset": anchor["clock_offset"],
                   "location": anchor["location"],
                   "complete": anchor["complete"],
                   "no_absence_debt": NO_ABSENCE_DEBT},
        "reading": compare_reading(anchor["reading"], snapshot.get("reading") or {}),
        "delivered": compare_delivered(anchor["delivered"], snapshot.get("delivered") or {}),
        "items": compare_items(anchor["items"], snapshot.get("items") or {}),
        "now_local": _iso_text(now_iso),
    })
    return block


# ---------------------------------------------------------------------------------------------
# home recipes state
# ---------------------------------------------------------------------------------------------


def empty_recipes_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": 0,
            "updated_at": None, "recipes": []}


def recipe_id_for(author_side: str, title: str) -> str:
    return stable_id("recipe", author_side, _one_line(title, MAX_RECIPE_TITLE))


def _validate_recipe(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid recipe")
    recipe_id = _one_line(raw.get("recipe_id"), 80)
    if not recipe_id:
        raise ValueError("invalid recipe id")
    title = _clean_title(raw.get("title"), MAX_RECIPE_TITLE)
    memo = _clean_memo(raw.get("memo"), MAX_RECIPE_MEMO)
    if title is None or memo is None:
        raise ValueError("invalid recipe text")
    author_side = str(raw.get("author_side") or "")
    if author_side not in AUTHOR_SIDES:
        raise ValueError("invalid recipe author")
    revision = _int_or_none(raw.get("revision"))
    if revision is None or revision < 1:
        raise ValueError("invalid recipe revision")
    return {
        "recipe_id": recipe_id,
        "title": title,
        "memo": memo,
        "author_side": author_side,
        "partner_contributed": author_side == "partner",
        "revision": int(revision),
        "created_at": _iso_text(raw.get("created_at")),
        "updated_at": _iso_text(raw.get("updated_at")),
    }


def _validate_recipes_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("state is not an object")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported schema")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")
    rows_raw = raw.get("recipes")
    if not isinstance(rows_raw, list) or len(rows_raw) > MAX_RECIPES:
        raise ValueError("invalid recipes")
    seen: set[str] = set()
    recipes: list[dict[str, Any]] = []
    for entry in rows_raw:
        row = _validate_recipe(entry)
        if row["recipe_id"] in seen:
            raise ValueError("duplicate recipe id")
        seen.add(row["recipe_id"])
        recipes.append(row)
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": int(revision),
            "updated_at": _iso_text(raw.get("updated_at")), "recipes": recipes}


def read_recipes_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(recipes_path(root), empty_recipes_state,
                             "RECIPES_STATE_INVALID")
    if reason is not None:
        return raw, reason
    try:
        return _validate_recipes_state(raw), None
    except ValueError:
        return empty_recipes_state(), "RECIPES_STATE_INVALID"


def write_recipes_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _validate_recipes_state(dict(state))
    _write_json(recipes_path(root), normalized)
    return normalized


def upsert_recipe(state: Mapping[str, Any], *, recipe_id: Optional[str], title: Any,
                  memo: Any, author_side: str, now_iso: str
                  ) -> tuple[dict[str, Any], bool, Optional[dict[str, Any]], Optional[str]]:
    """Create or update one explicit shared recipe card; same intent never bumps anything."""

    current = _validate_recipes_state(dict(state))
    clean_title = _clean_title(title, MAX_RECIPE_TITLE)
    clean_memo = _clean_memo(memo, MAX_RECIPE_MEMO)
    if clean_title is None or clean_memo is None or author_side not in AUTHOR_SIDES:
        return current, False, None, "RECIPE_TEXT_INVALID"
    requested_id = _one_line(recipe_id, 80) if recipe_id is not None else ""
    target_id = requested_id or recipe_id_for(author_side, clean_title)
    existing = next((row for row in current["recipes"]
                     if row["recipe_id"] == target_id), None)
    if existing is None:
        if len(current["recipes"]) >= MAX_RECIPES:
            return current, False, None, "RECIPE_LIMIT_REACHED"
        card = {"recipe_id": target_id, "title": clean_title, "memo": clean_memo,
                "author_side": author_side,
                "partner_contributed": author_side == "partner", "revision": 1,
                "created_at": _iso_text(now_iso), "updated_at": _iso_text(now_iso)}
        current["recipes"].append(card)
    else:
        if existing["author_side"] != author_side:
            # The existing card's author is immutable: one side cannot take another's card.
            return current, False, None, "RECIPE_AUTHOR_MISMATCH"
        if existing["title"] == clean_title and existing["memo"] == clean_memo:
            return current, False, existing, None
        existing["title"] = clean_title
        existing["memo"] = clean_memo
        existing["revision"] = int(existing["revision"]) + 1
        existing["updated_at"] = _iso_text(now_iso)
        card = existing
    current["revision"] = int(current["revision"]) + 1
    current["updated_at"] = _iso_text(now_iso)
    normalized = _validate_recipes_state(current)
    updated = next(row for row in normalized["recipes"] if row["recipe_id"] == card["recipe_id"])
    return normalized, True, updated, None


def recipes_block(*, state: Mapping[str, Any], state_reason: Optional[str],
                  references: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = _validate_recipes_state(dict(state))
    cards = [dict(row, partner_contributed=row["author_side"] == "partner")
             for row in normalized["recipes"]]
    refs: dict[str, Any] = {
        "status": "unavailable", "reason_code": "RECIPE_REFERENCE_UNAVAILABLE",
        "items": [], "count": 0,
        "not_evidence_of_cooking": NOT_EVIDENCE_OF_COOKING,
        "consumption_claimed": CONSUMPTION_CLAIMED,
        "matched_by_title": False,
        "per_card_link": None,
        "source": "world_phone_shopping.inventory",
    }
    if isinstance(references, Mapping):
        refs.update(dict(references))
    refs["not_evidence_of_cooking"] = NOT_EVIDENCE_OF_COOKING
    refs["consumption_claimed"] = CONSUMPTION_CLAIMED
    refs["matched_by_title"] = False
    refs["per_card_link"] = None
    return {
        "schema": RECIPES_SCHEMA,
        "read_only": True,
        "state": {"revision": _int_or_none(normalized.get("revision")),
                  "updated_at": normalized.get("updated_at"),
                  "reason_code": state_reason},
        "recipes": cards,
        "count": len(cards),
        "max_recipes": MAX_RECIPES,
        "partner_contributed": any(row["partner_contributed"] for row in cards),
        "reference": refs,
        "does_not_enter_memory": True,
        "private_notes_imported": False,
        "no_obligation": True,
        "local_only": True,
        "note": "共同菜谱卡是双方显式写下的；作者侧只选择写入口的验证策略，不授予她身份，"
                "也不能改对方卡。参考区只读 owner 的真实购买/消费回执，购买不等于做过，"
                "同名不等于吃过。",
    }


# ---------------------------------------------------------------------------------------------
# tentative plans state
# ---------------------------------------------------------------------------------------------


def empty_plans_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": 0,
            "updated_at": None, "plans": []}


def plan_id_for(proposer: str, title: str) -> str:
    return stable_id("plan", proposer, _one_line(title, MAX_PLAN_TITLE))


def _validate_plan(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid plan")
    plan_id = _one_line(raw.get("plan_id"), 80)
    title = _clean_title(raw.get("title"), MAX_PLAN_TITLE)
    if not plan_id or title is None:
        raise ValueError("invalid plan identity")
    window_note = raw.get("window_note")
    if window_note is None:
        window_note = ""
    if not isinstance(window_note, str) or len(window_note) > MAX_PLAN_WINDOW \
            or "\x00" in window_note or "\r" in window_note:
        raise ValueError("invalid plan window")
    status = str(raw.get("status") or "")
    proposer = str(raw.get("proposer") or "")
    if status not in PLAN_STATUSES or proposer not in PLAN_PROPOSERS:
        raise ValueError("invalid plan vocabulary")
    revision = _int_or_none(raw.get("revision"))
    if revision is None or revision < 1:
        raise ValueError("invalid plan revision")
    return {
        "plan_id": plan_id,
        "title": title,
        "window_note": window_note.strip(),
        "status": status,
        "proposer": proposer,
        "revision": int(revision),
        "created_at": _iso_text(raw.get("created_at")),
        "updated_at": _iso_text(raw.get("updated_at")),
    }


def _validate_plans_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("state is not an object")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported schema")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")
    rows_raw = raw.get("plans")
    if not isinstance(rows_raw, list) or len(rows_raw) > MAX_PLANS:
        raise ValueError("invalid plans")
    seen: set[str] = set()
    plans: list[dict[str, Any]] = []
    for entry in rows_raw:
        row = _validate_plan(entry)
        if row["plan_id"] in seen:
            raise ValueError("duplicate plan id")
        seen.add(row["plan_id"])
        plans.append(row)
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": int(revision),
            "updated_at": _iso_text(raw.get("updated_at")), "plans": plans}


def read_plans_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(plans_path(root), empty_plans_state, "PLANS_STATE_INVALID")
    if reason is not None:
        return raw, reason
    try:
        return _validate_plans_state(raw), None
    except ValueError:
        return empty_plans_state(), "PLANS_STATE_INVALID"


def write_plans_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _validate_plans_state(dict(state))
    _write_json(plans_path(root), normalized)
    return normalized


def _bump_plans(state: dict[str, Any], now_iso: str) -> None:
    state["revision"] = int(state["revision"]) + 1
    state["updated_at"] = _iso_text(now_iso)


def propose_plan(state: Mapping[str, Any], *, plan_id: Optional[str], title: Any,
                 window_note: Any, proposer: str, now_iso: str
                 ) -> tuple[dict[str, Any], bool, Optional[dict[str, Any]], Optional[str]]:
    """One explicit tentative item.  A replay returns the same id and never fills the cap."""

    current = _validate_plans_state(dict(state))
    clean_title = _clean_title(title, MAX_PLAN_TITLE)
    if clean_title is None or proposer not in PLAN_PROPOSERS:
        return current, False, None, "PLAN_TEXT_INVALID"
    if window_note is None:
        clean_window = ""
    else:
        if not isinstance(window_note, str) or len(window_note) > MAX_PLAN_WINDOW \
                or "\x00" in window_note or "\r" in window_note:
            return current, False, None, "PLAN_WINDOW_INVALID"
        clean_window = window_note.strip()
    requested_id = _one_line(plan_id, 80) if plan_id is not None else ""
    target_id = requested_id or plan_id_for(proposer, clean_title)
    existing = next((row for row in current["plans"] if row["plan_id"] == target_id), None)
    if existing is not None:
        if existing["status"] == "DROPPED":
            # A dropped item stays dropped; a new idea is a new propose, never a resurrection.
            return current, False, None, "PLAN_DROPPED"
        if existing["proposer"] != proposer:
            return current, False, None, "PLAN_PROPOSER_MISMATCH"
        if existing["title"] == clean_title and existing["window_note"] == clean_window:
            return current, False, existing, None
        # The same id with different content is a reschedule-shaped intent, not a propose.
        return current, False, None, "PLAN_ALREADY_EXISTS"
    if len(current["plans"]) >= MAX_PLANS:
        return current, False, None, "PLAN_LIMIT_REACHED"
    card = {"plan_id": target_id, "title": clean_title, "window_note": clean_window,
            "status": "TENTATIVE", "proposer": proposer, "revision": 1,
            "created_at": _iso_text(now_iso), "updated_at": _iso_text(now_iso)}
    current["plans"].append(card)
    _bump_plans(current, now_iso)
    normalized = _validate_plans_state(current)
    return normalized, True, card, None


def find_plan(state: Mapping[str, Any], plan_id: str) -> Optional[dict[str, Any]]:
    target = _one_line(plan_id, 80)
    return next((row for row in state["plans"] if row["plan_id"] == target), None)


_find_plan = find_plan


def reschedule_plan(state: Mapping[str, Any], *, plan_id: str,
                    window_note: Any, now_iso: str
                    ) -> tuple[dict[str, Any], bool, Optional[dict[str, Any]], Optional[str]]:
    """Move a tentative/rescheduled item to a new explicit window note; proposer unchanged."""

    current = _validate_plans_state(dict(state))
    plan = _find_plan(current, plan_id)
    if plan is None:
        return current, False, None, "PLAN_NOT_FOUND"
    if plan["status"] == "DROPPED":
        return current, False, None, "PLAN_DROPPED"
    if not isinstance(window_note, str) or len(window_note) > MAX_PLAN_WINDOW \
            or "\x00" in window_note or "\r" in window_note or not window_note.strip():
        return current, False, None, "PLAN_WINDOW_INVALID"
    clean_window = window_note.strip()
    if plan["status"] == "RESCHEDULED" and plan["window_note"] == clean_window:
        return current, False, plan, None
    plan["window_note"] = clean_window
    plan["status"] = "RESCHEDULED"
    plan["revision"] = int(plan["revision"]) + 1
    plan["updated_at"] = _iso_text(now_iso)
    _bump_plans(current, now_iso)
    normalized = _validate_plans_state(current)
    return normalized, True, plan, None


def drop_plan(state: Mapping[str, Any], *, plan_id: str,
              now_iso: str) -> tuple[dict[str, Any], bool, Optional[dict[str, Any]],
                                      Optional[str]]:
    """Put an item down explicitly.  Dropping twice is already applied, not a new revision."""

    current = _validate_plans_state(dict(state))
    plan = _find_plan(current, plan_id)
    if plan is None:
        return current, False, None, "PLAN_NOT_FOUND"
    if plan["status"] == "DROPPED":
        return current, False, plan, None
    plan["status"] = "DROPPED"
    plan["revision"] = int(plan["revision"]) + 1
    plan["updated_at"] = _iso_text(now_iso)
    _bump_plans(current, now_iso)
    normalized = _validate_plans_state(current)
    return normalized, True, plan, None


def plan_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": row.get("plan_id"),
        "title": row.get("title"),
        "window_note": row.get("window_note") or "",
        "status": row.get("status"),
        "proposer": row.get("proposer"),
        "revision": _int_or_none(row.get("revision")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "auto_hijack_activity": False,
        "no_obligation": True,
    }


def plans_block(*, state: Mapping[str, Any], state_reason: Optional[str],
                arrangements: Optional[Mapping[str, Any]],
                subject_view: bool) -> dict[str, Any]:
    normalized = _validate_plans_state(dict(state))
    rows = [plan_row(row) for row in normalized["plans"]]
    summary: dict[str, Any] = {
        "status": "unknown", "reason_code": "ARRANGEMENTS_UNKNOWN",
        "day": None, "plan_count": None, "obligation_count": None,
        "execution_status": None, "truncated": None,
    }
    if isinstance(arrangements, Mapping):
        summary.update(dict(arrangements))
    if not subject_view:
        # The default view keeps only safe counts/status/day; no private title/source/memo.
        for key in ("plan_titles", "plans_detail", "source_refs", "resume_point", "memo",
                    "work_refs"):
            summary.pop(key, None)
    return {
        "schema": PLANS_SCHEMA,
        "read_only": True,
        "view": "subject" if subject_view else "partner_default",
        "state": {"revision": _int_or_none(normalized.get("revision")),
                  "updated_at": normalized.get("updated_at"),
                  "reason_code": state_reason},
        "arrangements": summary,
        "tentative_plans": rows,
        "tentative_count": len(rows),
        "max_tentative_plans": MAX_PLANS,
        "tentative_are_not_arrangements": True,
        "auto_hijack_activity": False,
        "no_obligation": True,
        "does_not_touch_core_arrangements": True,
        "expires_automatically": False,
        "local_only": True,
        "note": "arrangements 只报告正式今日安排的安全摘要（默认仅数量/状态/日期）；"
                "暂定项始终另列，不导入 canonical Activity / FutureItems / Memory，"
                "也不会到期自动执行。",
    }


__all__ = [
    "AUTHOR_SIDES",
    "CONSUMPTION_CLAIMED",
    "MAX_ANCHOR_ITEMS",
    "MAX_ANCHOR_SHARES",
    "MAX_PLANS",
    "MAX_RECIPE_MEMO",
    "MAX_RECIPE_REFERENCE",
    "MAX_RECIPE_TITLE",
    "MAX_RECIPES",
    "MAX_PLAN_TITLE",
    "MAX_PLAN_WINDOW",
    "NO_ABSENCE_DEBT",
    "NOT_EVIDENCE_OF_COOKING",
    "PLANS_SCHEMA",
    "PLAN_OPEN_STATUSES",
    "PLAN_PROPOSERS",
    "PLAN_STATUSES",
    "RECIPES_SCHEMA",
    "RECIPES_FILENAME",
    "REUNION_FILENAME",
    "REUNION_SCHEMA",
    "PLANS_FILENAME",
    "READING_ANCHOR_POSITION_UNKNOWN",
    "READING_IDENTITY_CHANGED",
    "READING_POSITION_UNKNOWN",
    "READING_UNAVAILABLE",
    "capture_anchor",
    "compare_delivered",
    "compare_items",
    "compare_reading",
    "drop_plan",
    "empty_plans_state",
    "empty_recipes_state",
    "empty_reunion_state",
    "find_plan",
    "plan_id_for",
    "plan_row",
    "plans_block",
    "plans_path",
    "propose_plan",
    "read_plans_state",
    "read_recipes_state",
    "read_reunion_state",
    "recipe_id_for",
    "recipes_block",
    "recipes_path",
    "reschedule_plan",
    "reunion_block",
    "reunion_path",
    "stable_id",
    "upsert_recipe",
    "write_plans_state",
    "write_recipes_state",
    "write_reunion_state",
]

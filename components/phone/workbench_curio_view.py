#!/usr/bin/env python3
"""创作托盘、小物履历、三样喜欢之物：一个薄投影 + 两份显式引用状态。

它是什么
--------
一个**只投影既有 owner 事实**的薄模块，外加两份只记录「她自己放进来/选过什么」的轻量 JSON：

* 创作托盘（``phone/workbench_tray.json``）：最多两张 ``PhoneAlbum`` 图片引用、两份
  ``ResearchWorkspaceService`` 正式资料引用、一件私人作品引用及其放入时的基准版本，外加
  一段可选私人备注。图片、资料正文、作品正文仍在原 owner；这里只保存引用与基准。
  GET 每次重新向真实 owner 核实；基准版本只用于说明「写入时看到的是哪一版」。
* 小物履历（无 store，纯读）：把 World 产权 economy 的已购实例与 Body substrate 的
  placement/手持/在途按 ``item_instance_id`` 对起来，给出名称、取得时间、真实交易引用、
  当前位置或携带状态。没有「剩余次数」权威就如实返回 null + 具名原因；不造磨损或依恋分。
* 三样喜欢之物（``phone/trio_exchange.json``）：``her_picks`` 只保存真实
  ``LOCAL_DELIVERED`` 分享的引用，正文仍从分享 owner 现读并受 source 治理约束；
  ``partner_picks`` 只保存伴侣写的短卡（标题/描述），绝不调用 Memory / Belief /
  经历 / 认知 API，也不表示她看过、相信或记住了。

它不是什么
----------
* 不复制图片/正文/相册/作品到新库，不建 ACL/token/后台 timer，不产生任何义务。
* 不按 sku 或同名把两件东西说成同一件，不把场景声明物说成已购私产。
* 不因购买成功就冒充「已送达」或「已摆好」：送达/摆放各有 owner 事实。
* GET 一律纯读，不 tick、不 materialize、不 ensure 任何文件。
"""
from __future__ import annotations

import json
import os
import tempfile
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

WORKBENCH_SCHEMA = "phone.home.workbench.v1"
CURIOS_SCHEMA = "phone.home.curios.v1"
TRIO_SCHEMA = "phone.home.trio_exchange.v1"

STATE_DIRNAME = "phone"
TRAY_FILENAME = "workbench_tray.json"
TRIO_FILENAME = "trio_exchange.json"
STATE_SCHEMA_VERSION = 1

MAX_TRAY_IMAGES = 2
MAX_TRAY_MATERIALS = 2
MAX_TRAY_WORKS = 1
MAX_TRAY_NOTE = 120
MAX_TRIO_HER = 3
MAX_TRIO_PARTNER = 3
MAX_PARTNER_TITLE = 40
MAX_PARTNER_DESCRIPTION = 120
#: Version-history page used to prove a head successor.  Bounded like every other read.
MAX_HISTORY = 50

NO_REMAINING_USES_AUTHORITY = "NO_REMAINING_USES_AUTHORITY"
NO_TRAY_NOTE = "NO_TRAY_NOTE"


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


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


# ---------------------------------------------------------------------------------------------
# state paths / atomic json
# ---------------------------------------------------------------------------------------------


def tray_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / TRAY_FILENAME


def trio_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIRNAME / TRIO_FILENAME


def empty_tray_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": 0, "updated_at": None,
            "tray": {"images": [], "materials": [], "work": None, "note": None}}


def empty_trio_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": 0, "updated_at": None,
            "her_picks": [], "partner_picks": []}


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
    try:
        return raw, None
    except Exception:  # pragma: no cover - raw is already parsed
        return empty(), invalid_reason


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
# tray state
# ---------------------------------------------------------------------------------------------


def _validate_image_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError("invalid image entry")
    ref = row.get("album_artifact_id")
    if not _is_text(ref):
        raise ValueError("invalid image ref")
    return {"album_artifact_id": ref.strip()[:200], "added_at": _iso_text(row.get("added_at"))}


def _validate_material_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError("invalid material entry")
    ref = row.get("artifact_id")
    if not _is_text(ref):
        raise ValueError("invalid material ref")
    return {"artifact_id": ref.strip()[:200],
            "baseline_source_revision": _one_line(row.get("baseline_source_revision"), 200),
            "added_at": _iso_text(row.get("added_at"))}


def _validate_work_row(row: Any) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise ValueError("invalid work entry")
    ref = row.get("artifact_id")
    if not _is_text(ref):
        raise ValueError("invalid work ref")
    return {"artifact_id": ref.strip()[:200],
            "baseline_version_id": _one_line(row.get("baseline_version_id"), 200),
            "baseline_version_number": _int_or_none(row.get("baseline_version_number")),
            "added_at": _iso_text(row.get("added_at"))}


def _validate_tray(tray: Any) -> dict[str, Any]:
    if not isinstance(tray, Mapping):
        raise ValueError("tray is not an object")
    images_raw = tray.get("images")
    materials_raw = tray.get("materials")
    if not isinstance(images_raw, list) or len(images_raw) > MAX_TRAY_IMAGES:
        raise ValueError("invalid tray images")
    if not isinstance(materials_raw, list) or len(materials_raw) > MAX_TRAY_MATERIALS:
        raise ValueError("invalid tray materials")
    images = [_validate_image_row(row) for row in images_raw]
    materials = [_validate_material_row(row) for row in materials_raw]
    work = _validate_work_row(tray.get("work"))
    note = tray.get("note")
    if note is not None:
        if not isinstance(note, str) or len(note) > MAX_TRAY_NOTE:
            raise ValueError("invalid tray note")
        note = note.strip() or None
    return {"images": images, "materials": materials, "work": work, "note": note}


def _validate_tray_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("state is not an object")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported schema")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")
    tray = _validate_tray(raw.get("tray"))
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": int(revision),
            "updated_at": _iso_text(raw.get("updated_at")), "tray": tray}


def read_tray_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(tray_path(root), empty_tray_state, "TRAY_STATE_INVALID")
    if reason is not None:
        return raw, reason
    try:
        return _validate_tray_state(raw), None
    except ValueError:
        return empty_tray_state(), "TRAY_STATE_INVALID"


def write_tray_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    with _tx.state_file_lock(tray_path(root)):
        normalized = _validate_tray_state(dict(state))
        _write_json(tray_path(root), normalized)
        return normalized


def tray_identity(tray: Mapping[str, Any]) -> tuple:
    """What a set_tray intent actually names: refs + baselines + note, never timestamps."""

    normalized = _validate_tray(tray)
    return (
        tuple(row["album_artifact_id"] for row in normalized["images"]),
        tuple((row["artifact_id"], row["baseline_source_revision"])
              for row in normalized["materials"]),
        (None if normalized["work"] is None else
         (normalized["work"]["artifact_id"], normalized["work"]["baseline_version_id"],
          normalized["work"]["baseline_version_number"])),
        normalized["note"],
    )


def _bump(state: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    out = _validate_tray_state(dict(state))
    out["revision"] = int(out["revision"]) + 1
    out["updated_at"] = _iso_text(now_iso)
    return out


def set_tray(state: Mapping[str, Any], tray: Mapping[str, Any],
             now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_tray_state(dict(state))
    candidate = _validate_tray(tray)
    if tray_identity(current["tray"]) == tray_identity(candidate):
        return current, False
    current["tray"] = candidate
    return _bump(current, now_iso), True


def clear_tray(state: Mapping[str, Any], now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_tray_state(dict(state))
    empty = _validate_tray(empty_tray_state()["tray"])
    if tray_identity(current["tray"]) == tray_identity(empty):
        return current, False
    current["tray"] = empty
    return _bump(current, now_iso), True


# ---------------------------------------------------------------------------------------------
# trio state
# ---------------------------------------------------------------------------------------------


def _validate_her_pick(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError("invalid her pick")
    ref = row.get("share_id")
    if not _is_text(ref):
        raise ValueError("invalid her pick ref")
    return {"share_id": ref.strip()[:200], "picked_at": _iso_text(row.get("picked_at"))}


def _validate_partner_pick(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError("invalid partner pick")
    title = row.get("title")
    description = row.get("description")
    if not _is_text(title) or len(title.strip()) > MAX_PARTNER_TITLE:
        raise ValueError("invalid partner title")
    if not isinstance(description, str) or len(description) > MAX_PARTNER_DESCRIPTION:
        raise ValueError("invalid partner description")
    return {"title": title.strip(), "description": description.strip() or "",
            "partner_contributed": True, "added_at": _iso_text(row.get("added_at"))}


def _validate_trio_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("state is not an object")
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported schema")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid revision")
    her_raw = raw.get("her_picks")
    partner_raw = raw.get("partner_picks")
    if not isinstance(her_raw, list) or len(her_raw) > MAX_TRIO_HER:
        raise ValueError("invalid her picks")
    if not isinstance(partner_raw, list) or len(partner_raw) > MAX_TRIO_PARTNER:
        raise ValueError("invalid partner picks")
    her: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in her_raw:
        entry = _validate_her_pick(row)
        if entry["share_id"] in seen:
            continue
        seen.add(entry["share_id"])
        her.append(entry)
    partner = [_validate_partner_pick(row) for row in partner_raw]
    return {"schema_version": STATE_SCHEMA_VERSION, "revision": int(revision),
            "updated_at": _iso_text(raw.get("updated_at")),
            "her_picks": her, "partner_picks": partner}


def read_trio_state(root: Path | str) -> tuple[dict[str, Any], Optional[str]]:
    raw, reason = _read_json(trio_path(root), empty_trio_state, "TRIO_STATE_INVALID")
    if reason is not None:
        return raw, reason
    try:
        return _validate_trio_state(raw), None
    except ValueError:
        return empty_trio_state(), "TRIO_STATE_INVALID"


def write_trio_state(root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    with _tx.state_file_lock(trio_path(root)):
        normalized = _validate_trio_state(dict(state))
        _write_json(trio_path(root), normalized)
        return normalized


def her_identity(state: Mapping[str, Any]) -> tuple:
    normalized = _validate_trio_state(dict(state))
    return tuple(row["share_id"] for row in normalized["her_picks"])


def partner_identity(state: Mapping[str, Any]) -> tuple:
    normalized = _validate_trio_state(dict(state))
    return tuple((row["title"], row["description"]) for row in normalized["partner_picks"])


def set_her_picks(state: Mapping[str, Any], picks: list[Mapping[str, Any]],
                  now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_trio_state(dict(state))
    candidate = [{"share_id": row["share_id"], "picked_at": _iso_text(row.get("picked_at"))}
                 for row in (_validate_her_pick(item) for item in picks)]
    if her_identity(current) == tuple(row["share_id"] for row in candidate):
        return current, False
    current["her_picks"] = candidate
    out = _validate_trio_state(current)
    out["revision"] = int(out["revision"]) + 1
    out["updated_at"] = _iso_text(now_iso)
    return out, True


def set_partner_picks(state: Mapping[str, Any], picks: list[Mapping[str, Any]],
                      now_iso: str) -> tuple[dict[str, Any], bool]:
    current = _validate_trio_state(dict(state))
    candidate = []
    for item in picks:
        validated = _validate_partner_pick(item)
        candidate.append({"title": validated["title"],
                          "description": validated["description"],
                          "partner_contributed": True,
                          "added_at": _iso_text(item.get("added_at"))})
    if partner_identity(current) == tuple((row["title"], row["description"])
                                          for row in candidate):
        return current, False
    current["partner_picks"] = candidate
    out = _validate_trio_state(current)
    out["revision"] = int(out["revision"]) + 1
    out["updated_at"] = _iso_text(now_iso)
    return out, True


# ---------------------------------------------------------------------------------------------
# workbench: head succession proof + projection
# ---------------------------------------------------------------------------------------------


def head_advanced(*, baseline_version_id: Any, baseline_version_number: Any,
                  head_version_id: Any, head_version_number: Any,
                  history_entries: list[Mapping[str, Any]],
                  history_complete: bool) -> Any:
    """``False`` same head, ``True`` proven successor, ``"unknown"`` otherwise.

    A string difference is never proof: the successor must be shown either by the version
    sequence (``head_version_number > baseline_version_number``) *and* the baseline version
    being a real entry of this work's history.  Missing/irrelevant history stays ``unknown``.
    """

    base_id = str(baseline_version_id or "")
    head_id = str(head_version_id or "")
    if not base_id or not head_id:
        return "unknown"
    if base_id == head_id:
        return False
    base_number = _int_or_none(baseline_version_number)
    head_number = _int_or_none(head_version_number)
    if base_number is None or head_number is None or head_number <= base_number:
        return "unknown"
    found = False
    for entry in history_entries or ():
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("version_id") or "") != base_id:
            continue
        number = _int_or_none(entry.get("version_number"))
        if number is None or number != base_number:
            return "unknown"
        found = True
        break
    if found:
        return True
    # The baseline is not in the (possibly bounded) history: a successor cannot be proven
    # even if the numbers look larger.  ``history_complete`` alone does not prove it either.
    return "unknown"


def workbench_block(*, state: Mapping[str, Any], state_reason: Optional[str],
                    subject_view: bool, images: list[Mapping[str, Any]],
                    materials: list[Mapping[str, Any]],
                    work: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    tray = state.get("tray") if isinstance(state.get("tray"), Mapping) else {}
    total = (len(tray.get("images") or []) + len(tray.get("materials") or [])
             + (1 if tray.get("work") else 0))
    block: dict[str, Any] = {
        "schema": WORKBENCH_SCHEMA,
        "read_only": True,
        "view": "subject" if subject_view else "partner_default",
        "state": {"revision": _int_or_none(state.get("revision")),
                  "updated_at": state.get("updated_at"),
                  "reason_code": state_reason},
        "has_materials": bool(total),
        "counts": {"images": len(tray.get("images") or []),
                   "materials": len(tray.get("materials") or []),
                   "works": 1 if tray.get("work") else 0},
        "status": "empty" if total == 0 else "available",
        "updated_at": state.get("updated_at"),
        "reason_code": state_reason,
        "no_obligation": True,
        "local_only": True,
    }
    if not subject_view:
        block.update({
            "private_refs_included": False,
            "note": "默认视角只报告托盘是否有内容与三类件数；引用、标题、备注与版本指纹不返回。",
        })
        return block
    block.update({
        "private_refs_included": bool(total),
        "note": tray.get("note"),
        "images": [dict(row) for row in images],
        "materials": [dict(row) for row in materials],
        "work": dict(work) if work is not None else None,
    })
    return block


# ---------------------------------------------------------------------------------------------
# curios: pure projection of economy items + substrate placements
# ---------------------------------------------------------------------------------------------


def position_block(placement: Optional[Mapping[str, Any]], *,
                   delivery_status: Optional[str],
                   economy_location: Optional[str],
                   substrate_unavailable: bool = False) -> dict[str, Any]:
    """One item's real position, or a named unknown.  Never falls back to the subject's area."""

    if placement is None:
        if substrate_unavailable:
            # An unreadable substrate is not "this item has no placement".
            return {"status": "unknown", "reason_code": "SUBSTRATE_UNAVAILABLE",
                    "economy_location": economy_location}
        if str(delivery_status or "").upper() == "PENDING":
            return {"status": "pending_delivery", "reason_code": "DELIVERY_PENDING",
                    "economy_location": economy_location}
        return {"status": "unknown", "reason_code": "NO_PLACEMENT",
                "economy_location": economy_location}
    kind = str(placement.get("kind") or "")
    if kind == "hand":
        return {"status": "available", "kind": "hand", "carried": True,
                "slot_id": placement.get("slot_id")}
    if kind == "in_transit":
        return {"status": "in_transit", "kind": "in_transit", "carried": False,
                "from_location_id": placement.get("from_location_id"),
                "to_location_id": placement.get("to_location_id")}
    out: dict[str, Any] = {"status": "available", "kind": kind, "carried": False,
                           "location_id": placement.get("location_id")}
    for key in ("area_id", "surface_id", "container_id"):
        if placement.get(key) is not None:
            out[key] = placement.get(key)
    return out


def curio_row(*, item: Mapping[str, Any], name: Optional[str],
              placement: Optional[Mapping[str, Any]],
              delivery: Optional[Mapping[str, Any]],
              substrate_unavailable: bool = False) -> dict[str, Any]:
    provenance = item.get("acquisition_provenance") \
        if isinstance(item.get("acquisition_provenance"), Mapping) else {}
    transaction_id = str(provenance.get("transaction_id") or "")
    intent_id = str(provenance.get("purchase_intent_id") or "")
    acquired_status = "available" if (transaction_id and intent_id) else "unknown"
    acquired: dict[str, Any] = {
        "status": acquired_status,
        "reason_code": None if acquired_status == "available" else "NO_PURCHASE_PROVENANCE",
        "acquired_at": item.get("acquired_at"),
        "acquisition_kind": item.get("acquisition_kind"),
        "price": item.get("acquisition_price"),
        "currency": item.get("currency"),
        "source": {
            "transaction_id": transaction_id or None,
            "purchase_intent_id": intent_id or None,
            "source_kind": provenance.get("source"),
            "delivery_kind": provenance.get("delivery"),
            "delivery_id": (delivery.get("delivery_id")
                            if isinstance(delivery, Mapping) else None),
        },
    }
    delivery_status = str(delivery.get("status") or "") if isinstance(delivery, Mapping) else None
    return {
        "item_instance_id": str(item.get("item_instance_id") or ""),
        "name": name,
        "catalog_item_id": item.get("catalog_item_id"),
        "ownership": item.get("disposition"),
        "acquired": acquired,
        "delivery": ({
            "status": delivery_status or "unknown",
            "expected_delivery_at": delivery.get("expected_delivery_at"),
            "delivered_at": delivery.get("delivered_at"),
        } if isinstance(delivery, Mapping) else
            {"status": "unknown", "reason_code": "NO_DELIVERY_RECORD"}),
        "position": position_block(placement, delivery_status=delivery_status,
                                   economy_location=item.get("current_location"),
                                   substrate_unavailable=substrate_unavailable),
        "remaining_uses": {"value": None, "reason_code": NO_REMAINING_USES_AUTHORITY},
        "no_fabricated_wear": True,
        "no_attachment_score": True,
    }


def curios_block(*, items: list[Mapping[str, Any]], reason_code: Optional[str] = None,
                 source_note: Optional[str] = None) -> dict[str, Any]:
    rows = [dict(row) for row in items]
    pending = sum(1 for row in rows
                  if (row.get("delivery") or {}).get("status", "").upper() == "PENDING")
    unknown_position = sum(1 for row in rows
                           if (row.get("position") or {}).get("status") == "unknown")
    block: dict[str, Any] = {
        "schema": CURIOS_SCHEMA,
        "read_only": True,
        "items": rows,
        "count": len(rows),
        "pending_delivery_count": pending,
        "unknown_position_count": unknown_position,
        "source": "world_property_economy+world_body_substrate",
        "no_fabricated_wear": True,
        "no_attachment_score": True,
        "local_only": True,
        "note": "只列 World 产权 economy 里真实购买的实例；送达与摆放各按 owner 事实报告，"
                "未知位置不回退到主体当前区域；没有剩余次数权威时为 null。",
    }
    if reason_code:
        block["reason_code"] = str(reason_code)
    if source_note:
        block["source_note"] = str(source_note)[:200]
    return block


# ---------------------------------------------------------------------------------------------
# trio exchange projection
# ---------------------------------------------------------------------------------------------


def trio_block(*, state: Mapping[str, Any], state_reason: Optional[str],
               her_rows: list[Mapping[str, Any]],
               partner_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    her = [dict(row) for row in her_rows]
    partner = [dict(row) for row in partner_rows]
    text_included = any(not row.get("text_withheld", True) for row in her)
    return {
        "schema": TRIO_SCHEMA,
        "read_only": True,
        "state": {"revision": _int_or_none(state.get("revision")),
                  "updated_at": state.get("updated_at"),
                  "reason_code": state_reason},
        "her": {"picks": her, "count": len(her)},
        "partner": {"picks": partner, "count": len(partner)},
        "partner_contributed": any(row.get("partner_contributed") for row in partner),
        "private_text_included": text_included,
        "no_obligation": True,
        "local_only": True,
        "note": "her_picks 只引用真实已本地投递的分享并现读正文；partner_picks 只是匣中短卡，"
                "不代表她看过、相信或记住，也不进入 Memory / Belief / 经历。",
    }


def her_pick_row(*, share_id: str, picked_at: Any, status: str,
                 reason_code: Optional[str], text: Optional[str] = None,
                 text_chars: Optional[int] = None, delivered_at: Any = None,
                 state: Optional[str] = None) -> dict[str, Any]:
    withheld = text is None
    return {
        "share_id": share_id,
        "picked_at": _iso_text(picked_at),
        "status": status,
        "reason_code": reason_code or None,
        "state": state,
        "delivered_at": delivered_at,
        "text_withheld": withheld,
        "text": None if withheld else str(text)[:1000],
        "text_chars": text_chars if not withheld else None,
        "source_text_included": False,
    }


def partner_pick_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "title": row.get("title"),
        "description": row.get("description"),
        "partner_contributed": True,
        "added_at": _iso_text(row.get("added_at")),
    }


__all__ = [
    "CURIOS_SCHEMA",
    "MAX_HISTORY",
    "MAX_PARTNER_DESCRIPTION",
    "MAX_PARTNER_TITLE",
    "MAX_TRAY_IMAGES",
    "MAX_TRAY_MATERIALS",
    "MAX_TRAY_NOTE",
    "MAX_TRAY_WORKS",
    "MAX_TRIO_HER",
    "MAX_TRIO_PARTNER",
    "NO_REMAINING_USES_AUTHORITY",
    "TRAY_FILENAME",
    "TRIO_FILENAME",
    "TRIO_SCHEMA",
    "WORKBENCH_SCHEMA",
    "clear_tray",
    "curio_row",
    "curios_block",
    "empty_tray_state",
    "empty_trio_state",
    "head_advanced",
    "her_identity",
    "her_pick_row",
    "partner_identity",
    "partner_pick_row",
    "position_block",
    "read_tray_state",
    "read_trio_state",
    "set_her_picks",
    "set_partner_picks",
    "set_tray",
    "tray_identity",
    "tray_path",
    "trio_block",
    "trio_path",
    "workbench_block",
    "write_tray_state",
    "write_trio_state",
]

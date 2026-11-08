"""Lightweight, zero-LLM-bloat companion extensions aligned with CHIYO v0.4 Ch. 47 & Ch. 50:

1. Shared Lexicon ("只有彼此懂的说法"):
   - Per-profile `<profile>/chiyo/shared_phrases.json` (mode 0600, atomic write).
   - Pure deterministic substring match: injects meaning explanation ONLY when an active
     phrase actually appears in the current incoming message (0 tokens otherwise).
2. Questions to Future Self ("给未来自己的问题"):
   - Per-profile `<profile>/chiyo/future_questions.json` (mode 0600, atomic write).
   - Pins optional questions & later revisit reflections to `(artifact_id, version_id)`
     and exposes them cleanly to both Desktop UI and `chiyo_phone(action="work_context")`.
3. Embodied Chat Action Receipts ("言出法随真实回执小标签"):
   - Per-profile `<profile>/chiyo/chat_action_receipts.json` (bounded ring buffer, 0600).
   - Records verified owner-confirmed state changes from `chiyo_phone` and `world_move`
     (never leaking private diary text) so Desktop chat bubbles can display clickable
     action receipt pills that jump straight to the affected Nagiura panel.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

MAX_SHARED_PHRASES = 100
MAX_FUTURE_QUESTIONS = 200
MAX_ACTION_RECEIPTS = 80

_NAGIURA_LOC_LABELS: dict[str, str] = {
    "home": "临海的住居",
    "home/entrance": "住居玄关",
    "home/living": "客厅",
    "home/living_room": "客厅",
    "home/bedroom": "卧室",
    "home/kitchen": "厨房",
    "home/study": "书房",
    "home/bathroom": "洗漱间",
    "home/balcony": "阳台",
    "street": "海滨街道",
    "street_coast": "海滨街道",
    "street_shop": "商店街",
    "residential_lane": "住宅坡道",
    "beach": "海滩",
    "cafe": "海雾咖啡馆",
    "bookstore": "潮声书店",
    "convenience": "海风便利店",
    "convenience_store": "海风便利店",
    "general_store": "浜道具店",
    "home_goods_store": "浜道具店",
    "fish_market": "鲜鱼朝市",
    "library": "町立图书角",
    "park": "高台公园",
    "pier": "海滨栈桥",
    "lighthouse": "海岬灯塔",
    "shrine": "山坡神社",
    "station": "凪浦车站",
    "clinic": "海滨诊疗所",
    "town_office": "町役场",
    "school": "海滨学园",
}

_POSE_LABELS: dict[str, str] = {
    "standing": "站立",
    "seated": "坐下",
    "lying": "躺下",
    "transitioning": "走动中",
}

_ORI_LABELS: dict[str, str] = {
    "north": "朝北",
    "south": "朝南",
    "east": "朝东",
    "west": "朝西",
    "up": "向上",
    "down": "向下",
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chiyo_dir(profile_home: Path | str) -> Path:
    base = Path(profile_home).expanduser().resolve() / "chiyo"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def _read_json_doc(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8-sig")
        doc = json.loads(raw)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp.write_text(data, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _clean_text(value: Any, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.replace("\x00", "").split())
    return cleaned[:max_chars].strip()


# ---------------------------------------------------------------------------
# 1. Shared Lexicon ("只有彼此懂的说法")
# ---------------------------------------------------------------------------

def shared_phrases_path(profile_home: Path | str) -> Path:
    return _chiyo_dir(profile_home) / "shared_phrases.json"


def list_shared_phrases(profile_home: Path | str) -> list[dict[str, Any]]:
    doc = _read_json_doc(shared_phrases_path(profile_home))
    items = doc.get("items")
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        phrase = _clean_text(row.get("phrase"), 48)
        meaning = _clean_text(row.get("meaning"), 280)
        if not phrase or not meaning:
            continue
        out.append({
            "id": str(row.get("id") or ""),
            "phrase": phrase,
            "meaning": meaning,
            "origin": _clean_text(row.get("origin"), 220),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        })
    return out


def upsert_shared_phrase(
    profile_home: Path | str,
    *,
    phrase: str,
    meaning: str,
    origin: str = "",
    phrase_id: str = "",
) -> dict[str, Any]:
    clean_phrase = _clean_text(phrase, 48)
    clean_meaning = _clean_text(meaning, 280)
    clean_origin = _clean_text(origin, 220)
    if not clean_phrase:
        raise ValueError("共同说法词条不能为空（最多 48 字）。")
    if not clean_meaning:
        raise ValueError("请写明这个说法在你们之间的具体含义（最多 280 字）。")

    path = shared_phrases_path(profile_home)
    items = list_shared_phrases(profile_home)
    now = _utc_iso()
    norm = clean_phrase.lower()
    target_id = str(phrase_id or "").strip()

    updated_entry: dict[str, Any] | None = None
    new_items: list[dict[str, Any]] = []
    for row in items:
        same_id = bool(target_id and row.get("id") == target_id)
        same_phrase = str(row.get("phrase") or "").lower() == norm
        if updated_entry is None and (same_id or same_phrase):
            updated_entry = {
                "id": row.get("id") or f"phr_{uuid.uuid4().hex[:10]}",
                "phrase": clean_phrase,
                "meaning": clean_meaning,
                "origin": clean_origin if origin is not None else row.get("origin", ""),
                "created_at": row.get("created_at") or now,
                "updated_at": now,
            }
            new_items.append(updated_entry)
        elif same_id or same_phrase:
            continue
        else:
            new_items.append(row)

    if updated_entry is None:
        updated_entry = {
            "id": f"phr_{uuid.uuid4().hex[:10]}",
            "phrase": clean_phrase,
            "meaning": clean_meaning,
            "origin": clean_origin,
            "created_at": now,
            "updated_at": now,
        }
        new_items.insert(0, updated_entry)

    new_items = new_items[:MAX_SHARED_PHRASES]
    _write_json_atomic(path, {
        "schema_version": "nyairo.shared_phrases.v1",
        "updated_at": now,
        "items": new_items,
    })
    return updated_entry


def delete_shared_phrase(profile_home: Path | str, target: str) -> bool:
    needle = str(target or "").strip()
    if not needle:
        return False
    needle_low = needle.lower()
    items = list_shared_phrases(profile_home)
    kept = [
        r for r in items
        if r.get("id") != needle and str(r.get("phrase") or "").lower() != needle_low
    ]
    if len(kept) == len(items):
        return False
    _write_json_atomic(shared_phrases_path(profile_home), {
        "schema_version": "nyairo.shared_phrases.v1",
        "updated_at": _utc_iso(),
        "items": kept,
    })
    return True


def match_shared_phrases(
    profile_home: Path | str,
    text: str,
    *,
    max_matches: int = 5,
) -> list[dict[str, Any]]:
    if not isinstance(text, str) or not text.strip():
        return []
    haystack = text.lower()
    items = list_shared_phrases(profile_home)
    if not items:
        return []
    # Match longer, more specific phrases first; deterministic substring check (0 LLM cost).
    ordered = sorted(items, key=lambda r: (-len(str(r.get("phrase") or "")), r.get("created_at") or ""))
    matched: list[dict[str, Any]] = []
    for row in ordered:
        phr = str(row.get("phrase") or "").strip()
        if phr and phr.lower() in haystack:
            matched.append(row)
            if len(matched) >= max_matches:
                break
    return matched


def render_matched_phrases_block(matches: list[dict[str, Any]]) -> str:
    if not matches:
        return ""
    lines = ["【双方约定的共同说法（仅用于理解当前话语的私人默契含义，不代表自动执行固定指令或义务）】"]
    for m in matches:
        line = f"- 「{m['phrase']}」：{m['meaning']}"
        if m.get("origin"):
            line += f"（来历备注：{m['origin']}）"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. Questions to Future Self ("给未来自己的问题")
# ---------------------------------------------------------------------------

def future_questions_path(profile_home: Path | str) -> Path:
    return _chiyo_dir(profile_home) / "future_questions.json"


def list_future_questions(
    profile_home: Path | str,
    *,
    artifact_id: str = "",
) -> list[dict[str, Any]]:
    doc = _read_json_doc(future_questions_path(profile_home))
    items = doc.get("items")
    if not isinstance(items, list):
        return []
    aid_filter = str(artifact_id or "").strip()
    out: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("artifact_id") or "").strip()
        q = _clean_text(row.get("question"), 320)
        if not aid or not q:
            continue
        if aid_filter and aid != aid_filter:
            continue
        out.append({
            "id": str(row.get("id") or ""),
            "artifact_id": aid,
            "artifact_title": _clean_text(row.get("artifact_title"), 120),
            "version_id": str(row.get("version_id") or ""),
            "version_number": int(row.get("version_number") or 1),
            "question": q,
            "reflection": _clean_text(row.get("reflection"), 400),
            "created_at": str(row.get("created_at") or ""),
            "answered_at": str(row.get("answered_at") or row.get("reflected_at") or ""),
            "reflected_at": str(row.get("reflected_at") or row.get("answered_at") or ""),
        })
    return out


def upsert_future_question(
    profile_home: Path | str,
    *,
    artifact_id: str,
    question: str,
    version_id: str = "",
    version_number: int = 1,
    artifact_title: str = "",
    reflection: str = "",
    question_id: str = "",
) -> dict[str, Any]:
    aid = str(artifact_id or "").strip()
    clean_q = _clean_text(question, 320)
    clean_ref = _clean_text(reflection, 400)
    clean_title = _clean_text(artifact_title, 120)
    if not aid:
        raise ValueError("缺少作品 artifact_id。")
    if not clean_q:
        raise ValueError("留给未来的问题不能为空。")

    all_items = list_future_questions(profile_home)
    now = _utc_iso()
    qid = str(question_id or "").strip()
    updated: dict[str, Any] | None = None
    next_items: list[dict[str, Any]] = []

    for row in all_items:
        if qid and row.get("id") == qid:
            ans_ts = now if clean_ref else ""
            updated = {
                **row,
                "question": clean_q,
                "artifact_title": clean_title or row.get("artifact_title", ""),
                "reflection": clean_ref,
                "answered_at": ans_ts,
                "reflected_at": ans_ts,
            }
            next_items.append(updated)
        else:
            next_items.append(row)

    if updated is None:
        ans_ts = now if clean_ref else ""
        updated = {
            "id": f"fq_{uuid.uuid4().hex[:10]}",
            "artifact_id": aid,
            "artifact_title": clean_title,
            "version_id": str(version_id or "").strip(),
            "version_number": max(1, int(version_number or 1)),
            "question": clean_q,
            "reflection": clean_ref,
            "created_at": now,
            "answered_at": ans_ts,
            "reflected_at": ans_ts,
        }
        next_items.insert(0, updated)

    next_items = next_items[:MAX_FUTURE_QUESTIONS]
    _write_json_atomic(future_questions_path(profile_home), {
        "schema_version": "nyairo.future_questions.v1",
        "updated_at": now,
        "items": next_items,
    })
    return updated


def answer_future_question(
    profile_home: Path | str,
    *,
    question_id: str,
    reflection: str,
) -> dict[str, Any] | None:
    qid = str(question_id or "").strip()
    if not qid:
        return None
    clean_ref = _clean_text(reflection, 400)
    all_items = list_future_questions(profile_home)
    now = _utc_iso()
    found: dict[str, Any] | None = None
    for row in all_items:
        if row.get("id") == qid:
            ans_ts = now if clean_ref else ""
            row["reflection"] = clean_ref
            row["answered_at"] = ans_ts
            row["reflected_at"] = ans_ts
            found = row
            break
    if found is None:
        return None
    _write_json_atomic(future_questions_path(profile_home), {
        "schema_version": "nyairo.future_questions.v1",
        "updated_at": now,
        "items": all_items,
    })
    return found


def delete_future_question(profile_home: Path | str, question_id: str) -> bool:
    qid = str(question_id or "").strip()
    if not qid:
        return False
    all_items = list_future_questions(profile_home)
    kept = [r for r in all_items if r.get("id") != qid]
    if len(kept) == len(all_items):
        return False
    _write_json_atomic(future_questions_path(profile_home), {
        "schema_version": "nyairo.future_questions.v1",
        "updated_at": _utc_iso(),
        "items": kept,
    })
    return True


# ---------------------------------------------------------------------------
# 3. Embodied Chat Action Receipts ("真实动作回执小标签")
# ---------------------------------------------------------------------------

def action_receipts_path(profile_home: Path | str) -> Path:
    return _chiyo_dir(profile_home) / "chat_action_receipts.json"


def list_action_receipts(
    profile_home: Path | str,
    *,
    limit: int = 40,
    since_ms: int = 0,
) -> list[dict[str, Any]]:
    doc = _read_json_doc(action_receipts_path(profile_home))
    items = doc.get("items")
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        ums = int(row.get("epoch_ms") or row.get("unix_ms") or 0)
        if since_ms > 0 and ums < since_ms:
            continue
        out.append(row)
        if len(out) >= max(1, limit):
            break
    return out


def record_action_receipt(
    profile_home: Path | str,
    *,
    tool: str,
    action: str,
    verb: str = "",
    label: str,
    detail: str = "",
    nav_target: str = "world:map",
    ok: bool = True,
    status: str = "applied",
    operation_key: str = "",
) -> dict[str, Any]:
    now_iso = _utc_iso()
    now_ms = int(time.time() * 1000)
    entry = {
        "id": f"rcpt_{uuid.uuid4().hex[:10]}",
        "at": now_iso,
        "recorded_at": now_iso,
        "unix_ms": now_ms,
        "epoch_ms": now_ms,
        "tool": str(tool or ""),
        "action": str(action or ""),
        "verb": str(verb or ""),
        "kind": str(verb or action or ""),
        "label": _clean_text(label, 64) or "已执行动作",
        "detail": _clean_text(detail, 120),
        "nav_target": str(nav_target or "world:map"),
        "ok": bool(ok),
        "status": str(status or "applied"),
        "operation_key": str(operation_key or "")[:80],
    }
    path = action_receipts_path(profile_home)
    existing = list_action_receipts(profile_home, limit=MAX_ACTION_RECEIPTS)
    # Deduplicate retry of the exact same operation_key
    if entry["operation_key"]:
        existing = [r for r in existing if r.get("operation_key") != entry["operation_key"]]
    merged = [entry] + existing
    _write_json_atomic(path, {
        "schema_version": "nyairo.chat_action_receipts.v1",
        "updated_at": now_iso,
        "items": merged[:MAX_ACTION_RECEIPTS],
    })
    return entry


def _friendly_loc(loc_id: str, doc: Mapping[str, Any] | None = None) -> str:
    raw = str(loc_id or "").strip()
    if isinstance(doc, Mapping):
        data = doc.get("data") if isinstance(doc.get("data"), Mapping) else doc
        if isinstance(data, Mapping):
            loc = data.get("location")
            if isinstance(loc, Mapping):
                if loc.get("area_name"):
                    return str(loc["area_name"])
                if "/" not in raw and loc.get("name"):
                    return str(loc["name"])
    if raw in _NAGIURA_LOC_LABELS:
        return _NAGIURA_LOC_LABELS[raw]
    if "/" in raw:
        tail = raw.split("/", 1)[-1]
        if f"home/{tail}" in _NAGIURA_LOC_LABELS:
            return _NAGIURA_LOC_LABELS[f"home/{tail}"]
    return raw or "目的地"


def record_phone_action_receipt(
    profile_home: Path | str,
    action: str,
    args: Mapping[str, Any],
    doc: Mapping[str, Any],
    operation_key: str = "",
) -> dict[str, Any] | None:
    status = str(doc.get("status") or "")
    ok = bool(doc.get("ok")) and status in ("applied", "already_applied")
    if not ok:
        return None

    verb = ""
    nav = "world:map"
    detail = ""

    if action == "world_action":
        verb = str(args.get("world_action") or "").strip().upper()
        params = args.get("params") if isinstance(args.get("params"), Mapping) else {}
        if verb == "MOVE":
            dest = str(params.get("destination_location_id") or "")
            data_map = doc.get("data") if isinstance(doc.get("data"), Mapping) else {}
            travel_map = data_map.get("travel") if isinstance(data_map, Mapping) and isinstance(data_map.get("travel"), Mapping) else {}
            in_prog = str(travel_map.get("status") or "").lower() == "in_progress"
            label = f"{'已动身前往' if in_prog else '已前往'} · {_friendly_loc(dest, doc)}"
        elif verb == "POSE":
            pose_cn = _POSE_LABELS.get(str(params.get("pose") or ""), str(params.get("pose") or "姿态"))
            ori_cn = _ORI_LABELS.get(str(params.get("orientation") or ""), "")
            label = f"切换姿态 · {pose_cn}" + (f"（{ori_cn}）" if ori_cn else "")
        elif verb == "REST":
            if params.get("state") == "stop":
                label = "已结束休息"
            elif params.get("mode") == "sleep_like":
                label = "躺下安睡休息"
            else:
                label = "开始小憩恢复"
        elif verb == "TOGGLE_FIXTURE":
            label = "已点亮房间灯具" if params.get("state") == "on" else "已关闭房间灯具"
        elif verb == "PICK_UP":
            label = "已拿起随身物品"
        elif verb == "PLACE":
            label = "已放下手中物品"
        elif verb == "ARRANGE":
            label = "已布置房间家具"
        elif verb == "TOILET":
            label = "已使用洗漱间"
        else:
            label = f"世界动作 · {verb}"
        nav = "world:map"
    elif action == "note_save":
        ttl = _clean_text(args.get("title"), 24)
        label = f"已记随笔 · {ttl}" if ttl else "已保存随笔备忘"
        nav = "world:notes"
    elif action == "diary_write":
        # Strictly never store private diary body in chat action receipts.
        label = "写下了一篇私密日记"
        nav = "world:notes"
    elif action == "camera":
        cap = _clean_text(args.get("caption"), 22)
        label = f"拍下留影 · {cap}" if cap else "已拍下场景留影"
        nav = "world:media"
    elif action == "reading_import":
        label = "已更新共读读物"
        nav = "world:media"
    elif action == "reading_position":
        pg = int(args.get("page") or 1)
        label = f"共读书签 · 第 {pg} 页"
        nav = "world:media"
    elif action == "reading_annotate":
        pg = int(args.get("page") or 1)
        is_note = str(args.get("kind") or "") == "note"
        label = f"{'共读笔记' if is_note else '共读疑问'} · 第 {pg} 页"
        nav = "world:media"
    elif action in ("purchase", "store_purchase"):
        label = "实体店购入物品" if action == "store_purchase" else "线上商店购入物品"
        nav = "world:shop"
    elif action == "use":
        label = "已使用随身物品"
        nav = "world:shop"
    elif action == "reference_save":
        lbl = _clean_text(args.get("label"), 22)
        label = f"已收藏 · {lbl}" if lbl else "已收藏参考条目"
        nav = "world:social"
    elif action == "claim_observe":
        label = "已实地感知当前环境"
        nav = "world:ev"
    else:
        return None

    return record_action_receipt(
        profile_home,
        tool="chiyo_phone",
        action=action,
        verb=verb,
        label=label,
        detail=detail,
        nav_target=nav,
        ok=True,
        status=status,
        operation_key=operation_key,
    )


def record_world_move_receipt(
    profile_home: Path | str,
    raw_location: str,
    result_doc: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not result_doc.get("ok"):
        return None
    status = str(result_doc.get("status") or "")
    dest = str(
        result_doc.get("location_id")
        or result_doc.get("destination_location_id")
        or result_doc.get("target_location_id")
        or raw_location
    )
    loc_label = _friendly_loc(dest)
    prefix = "已抵达" if status in ("arrived", "already_there") else "动身前往"
    return record_action_receipt(
        profile_home,
        tool="world_move",
        action="move",
        verb="MOVE",
        label=f"{prefix} · {loc_label}",
        detail=str(result_doc.get("message") or "")[:80],
        nav_target="world:map",
        ok=True,
        status=status,
    )

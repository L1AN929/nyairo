#!/usr/bin/env python3
"""统一因果生活故事线（零写入纯读聚合视图 ``GET /api/phone/today/story``）。

职责边界：
1. 100% 零磁盘写入：绝不创建目录、绝不落盘临时文件、绝不调用大模型（``llm_calls == 0``）。
2. 汇聚真实今日记录与跨切片生活回声：
   - ``day_timeline`` 今日时间线（优雅兼容 ``CANONICAL_STATE_ABSENT`` / ``LIFE_STATE_UNBOUND``）；
   - ``decision_review`` 决策摘要映射为温和自然的中文 ``because_tag``（严禁暴露内部状态码或未公开私人记忆）；
   - ``phone/v02_v03_consequence_ledger.json`` 今日已结算的生活回响账本；
   - 伴侣安全共享切片：``desk_tomorrow_note.json``、``workbench_curio.json``、
     ``outing_bench_bookstore.json``、``v02_crossmodal.json``（仅 ``share_with_room == True``）、
     ``somatic_comfort.json``。
3. 严守隐私红线：私人日记正文、未公开草稿、``share_with_room != True`` 的跨模态小样、
   ``shared == False`` 的私人便签/长椅足迹以及成年亲密域一律不进入故事线。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

VIEW_SCHEMA = "nyairo.phone.day_story.v1"
ENDPOINT_PATH = "/api/phone/today/story"
DEFAULT_TZ = timezone(timedelta(hours=8))

_LIFE_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "life" / "scripts"
if str(_LIFE_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_LIFE_SCRIPTS_DIR))

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INTERNAL_CODE_RE = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")

_PLACE_LABELS_ZH: dict[str, str] = {
    "home": "房间里",
    "bedroom": "卧室窗边",
    "desk": "书桌旁",
    "study": "书桌旁",
    "living": "客厅里",
    "living_room": "客厅里",
    "kitchen": "厨房操作台旁",
    "balcony": "阳台边",
    "window": "窗台边",
    "bookstore": "街角书店",
    "bench": "街边长椅",
    "park": "街心公园",
    "park_bench": "公园长椅",
    "riverbank": "河岸步道",
    "river_bench": "河岸长椅",
    "cafe": "街角咖啡馆",
    "flower_shop": "街区花铺",
    "home_goods_store": "生活杂货铺",
    "market": "市集步道",
    "gallery": "小画廊",
    "courtyard": "小院里",
    "street_1": "门前街巷",
    "street_2": "邻街步道",
}

_POSTURE_LABELS_ZH: dict[str, str] = {
    "seated": "安稳坐着",
    "sitting": "安稳坐着",
    "standing": "站着活动",
    "lying": "躺着歇息",
    "resting": "靠着休息",
    "walking": "慢步走动",
    "leaning": "倚在窗边",
}

_DRINK_LABELS_ZH: dict[str, str] = {
    "warm_water": "温水",
    "hot_tea": "热茶",
    "herbal_tea": "草本温茶",
    "barley_tea": "热麦茶",
    "warm_milk": "温牛奶",
    "yuzu_tea": "温柚子茶",
    "coffee": "温咖啡",
}

_CUSHION_LABELS_ZH: dict[str, str] = {
    "lumbar_pillow": "护腰软垫",
    "soft_cushion": "靠背软垫",
    "lap_blanket": "搭膝薄毯",
    "shawls": "披肩软毯",
}

_WINDOW_LABELS_ZH: dict[str, str] = {
    "closed": "关着窗挡风",
    "cracked": "留了一道窗缝透气",
    "open": "敞着窗吹风",
    "ajar": "微推开一扇窗透气",
}

_JUMP_TARGETS: dict[str, str] = {
    "desk": "/desk",
    "reading": "/reading",
    "workbench": "/workbench",
    "outing": "/outing",
    "crossmodal": "/crossmodal",
    "somatic": "/body",
    "home": "/home",
    "ecology": "/ecology",
    "activity": "/today",
    "world": "/world",
}

# Warm natural Chinese causal tags (zero internal codes)
BECAUSE_DESK_NOTE = "因为书桌便签上记着这件小事"
BECAUSE_HABIT = "因为顺着平时的生活小习惯"
BECAUSE_REST = "因为身体有点累想先歇一歇"
BECAUSE_READING = "因为还惦记着读到一半的那几页"
BECAUSE_OUTING = "因为想出门吹吹风散散步"
BECAUSE_DRAFT = "因为手头草稿还差一点收尾"
BECAUSE_CURIO = "因为被眼前的配色和小物触动了灵感"
BECAUSE_SHOPPING = "因为想添置一点屋里用得上的小物"
BECAUSE_SHARED_ROOM = "因为想把这份小日常留给屋里共享"


def _read_json_readonly(path: Path) -> Optional[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    txt = value.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt


def _local_day_and_hhmm(iso_str: Any) -> tuple[Optional[str], str, str]:
    dt = _parse_iso(iso_str)
    if dt is None:
        raw = str(iso_str or "").strip()
        if len(raw) >= 10 and _DAY_RE.match(raw[:10]):
            hhmm = raw[11:16] if len(raw) >= 16 and ":" in raw[11:16] else "--:--"
            return raw[:10], hhmm, raw[:40]
        return None, "--:--", ""
    local_dt = dt.astimezone(DEFAULT_TZ)
    return (
        local_dt.date().isoformat(),
        local_dt.strftime("%H:%M"),
        local_dt.isoformat(timespec="seconds"),
    )


def _clean_warm_text(value: Any, limit: int = 120) -> str:
    txt = " ".join(str(value or "").split()).strip()
    if not txt:
        return ""
    txt = _INTERNAL_CODE_RE.sub("", txt)
    txt = " ".join(txt.split()).strip(" ·-—:：;；,，")
    return txt[:limit]


def _human_place_name(place_id: Any = None, area_id: Any = None, fallback_label: Any = None) -> str:
    if isinstance(fallback_label, str) and fallback_label.strip():
        fb = fallback_label.strip()
        if fb not in _PLACE_LABELS_ZH and not fb.isascii():
            return fb[:32]
    area_key = str(area_id or "").strip().lower()
    place_key = str(place_id or "").strip().lower()
    if area_key and area_key in _PLACE_LABELS_ZH:
        return _PLACE_LABELS_ZH[area_key]
    if place_key and place_key in _PLACE_LABELS_ZH:
        return _PLACE_LABELS_ZH[place_key]
    if place_key:
        for known_k, zh_v in _PLACE_LABELS_ZH.items():
            if known_k in place_key:
                return zh_v
    return "房间里"


def _stable_story_id(prefix: str, *parts: Any) -> str:
    joined = "|".join(str(p or "") for p in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
    return f"story:{prefix}:{digest}"


def _resolve_story_day_readonly(root: Path, explicit_day: Optional[str]) -> str:
    if isinstance(explicit_day, str) and _DAY_RE.match(explicit_day.strip()):
        return explicit_day.strip()

    # 1. Try PhoneService business clock zero-write if nested life state exists
    try:
        from . import life as phone_life
        from . import service as phone_service

        p_root = phone_life.phone_root(root)
        nested = phone_life.read_nested_life(p_root)
        if nested is not None:
            s_root = phone_life.life_state_root(p_root, nested)
            if s_root is not None and Path(s_root).exists():
                clk = phone_service.today_business_day(s_root)
                cand = str(clk.get("day") or "").strip()
                if _DAY_RE.match(cand):
                    return cand
    except Exception:
        pass

    # 2. Check v03 clock or consequence ledger recent date
    for rel in (
        ("phone", "v03_clock_evaporation.json"),
        ("phone", "v03_clock.json"),
        ("state", "v03_clock_state.json"),
    ):
        doc = _read_json_readonly(root.joinpath(*rel))
        if isinstance(doc, Mapping):
            for k in ("business_day", "current_day", "logical_now", "updated_at"):
                d_str, _, _ = _local_day_and_hhmm(doc.get(k))
                if d_str and _DAY_RE.match(d_str):
                    return d_str

    return datetime.now(tz=DEFAULT_TZ).date().isoformat()


def _discover_life_state_roots_readonly(root: Path) -> list[Path]:
    found: list[Path] = []
    try:
        from . import life as phone_life

        p_root = phone_life.phone_root(root)
        nested = phone_life.read_nested_life(p_root)
        if nested is not None:
            sr = Path(phone_life.life_state_root(p_root, nested))
            if sr.is_dir() and not sr.is_symlink():
                found.append(sr)
    except Exception:
        pass

    for candidate in (
        root / "one-day" / "life",
        root / "life",
        root / "state",
        root,
    ):
        try:
            if candidate.is_dir() and not candidate.is_symlink() and candidate not in found:
                found.append(candidate)
        except OSError:
            continue
    return found


def map_decision_to_because_tag(record: Mapping[str, Any]) -> str:
    """Map structured decision fields to a natural warm Chinese ``because_tag`` (zero internal codes)."""
    decision = str(record.get("decision") or "").strip().upper()
    route = str(record.get("decision_route") or "").strip().lower()
    cand_ref = str(record.get("selected_candidate_ref") or "").strip().lower()
    raw_reasons = record.get("reason_codes")
    reasons_joined = " ".join(
        str(c or "").strip().lower()
        for c in (raw_reasons if isinstance(raw_reasons, (list, tuple)) else ())
    )
    haystack = f"{decision} {route} {cand_ref} {reasons_joined}"

    if decision in {"HOLD", "KEEP_IDLE", "DEFER", "DROP", "NONE"} and not any(
        tok in haystack for tok in ("rest", "fatigue", "sleep", "comfort", "tired", "illness")
    ):
        return ""

    if any(tok in haystack for tok in ("desk", "tomorrow", "note", "fitem:", "popt:", "memo", "obligation")):
        return BECAUSE_DESK_NOTE
    if any(
        tok in haystack
        for tok in ("rest", "sleep", "fatigue", "tired", "somatic", "comfort", "illness", "drink", "cushion", "pause", "recovery")
    ):
        return BECAUSE_REST
    if any(tok in haystack for tok in ("read", "book", "page", "material", "window_book", "annotation")):
        return BECAUSE_READING
    if any(
        tok in haystack
        for tok in ("move", "place:", "outing", "walk", "bench", "bookstore", "park", "street", "roam", "neighborhood", "courtyard", "flower_shop")
    ):
        return BECAUSE_OUTING
    if any(tok in haystack for tok in ("buy:", "purchase", "shop", "store", "market")):
        return BECAUSE_SHOPPING
    if any(tok in haystack for tok in ("palette", "crossmodal", "color", "swatch", "curio", "craft")):
        return BECAUSE_CURIO
    if any(
        tok in haystack
        for tok in ("habit", "routine", "rhythm", "dailyact:toggle", "dailyact:arrange", "window", "garment", "tidy")
    ):
        return BECAUSE_HABIT
    if any(tok in haystack for tok in ("share", "hearth", "recipe", "reunion", "contact")):
        return BECAUSE_SHARED_ROOM
    if any(
        tok in haystack
        for tok in ("artifact:", "project", "draft", "work", "continue", "resume", "practice", "research", "commit")
    ):
        return BECAUSE_DRAFT
    if decision in {"START", "CONTINUE", "RESUME"}:
        return BECAUSE_HABIT
    return ""


def _infer_decision_domain_and_title(record: Mapping[str, Any], because_tag: str) -> tuple[str, str, str]:
    cand_ref = str(record.get("selected_candidate_ref") or "").strip().lower()
    if because_tag == BECAUSE_DESK_NOTE:
        return "desk", "照着书桌便签跟进了一件小事", "顺手把记在便签上的待办往前推了一步。"
    if because_tag == BECAUSE_REST:
        return "somatic", "停下来歇了一会儿缓口气", "顺着身体的节奏放慢步子，给自己留一点安静余量。"
    if because_tag == BECAUSE_READING:
        return "reading", "翻开手边的书接着往下读", "顺着上次停下的书签又静静读了几页。"
    if because_tag == BECAUSE_OUTING:
        place_hint = "门外街巷"
        for k, zh in _PLACE_LABELS_ZH.items():
            if k in cand_ref:
                place_hint = zh
                break
        return "outing", f"起身去{place_hint}走走透气", "换个环境吹吹微风，看看街边的动静。"
    if because_tag == BECAUSE_SHOPPING:
        return "ecology", "去杂货铺挑了一件日常小物", "按手头预算添置了屋里用得上的东西。"
    if because_tag == BECAUSE_CURIO:
        return "crossmodal", "在桌边摆弄了一会儿配色与小物", "把顺眼的颜色和手边物件搭在一起试了试。"
    if because_tag == BECAUSE_DRAFT:
        return "workbench", "坐在工作台前续写手头草稿", "顺着上次留下的保存点做了一点细部修改。"
    if because_tag == BECAUSE_SHARED_ROOM:
        return "home", "整理了一点留给屋里的日常分享", "把今天觉得有意思的小片段留在了房间里。"
    return "home", "在屋里顺手收拾了一件日常小事", "按平时的起居节奏安静度过这一小段时光。"


def _read_decisions_zero_write(root: Path, resolved_day: str) -> list[dict[str, Any]]:
    """Read structured decisions for ``resolved_day`` with 100% zero disk writes."""
    import decision_review as dr_mod

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # 1. Support mocked or custom read_decision_review hook if present
    hook = getattr(dr_mod, "read_decision_review", None)
    if callable(hook):
        try:
            try:
                ans = hook(root, day=resolved_day)
            except TypeError:
                ans = hook(root)
            rows = []
            if isinstance(ans, Mapping):
                data = ans.get("data") if isinstance(ans.get("data"), Mapping) else ans
                rows = (
                    data.get("decisions")
                    or data.get("recent_decisions")
                    or data.get("entries")
                    or data.get("items")
                    or []
                )
            elif isinstance(ans, list):
                rows = ans
            for r in rows:
                if isinstance(r, Mapping):
                    did = str(r.get("decision_id") or r.get("id") or f"hook_{len(records)}")
                    if did not in seen_ids:
                        seen_ids.add(did)
                        records.append(dict(r))
        except Exception:
            pass

    # 2. Read from real AG-1 read store via decision_review.open_readers (zero writes)
    for sr in _discover_life_state_roots_readonly(root):
        has_agency_data = any(
            (sr / sub).exists()
            for sub in ("agency", "decisions", "canonical_life_state.json", "decision_records.json")
        )
        if not has_agency_data:
            continue
        try:
            readers = dr_mod.open_readers(sr)
            d_read = readers.get("decision_read")
            if d_read is None:
                continue
            recent = dr_mod.list_recent_decisions(d_read, limit=40)
            for skeleton in recent:
                if not isinstance(skeleton, Mapping):
                    continue
                did = str(skeleton.get("decision_id") or "").strip()
                if not did or did in seen_ids:
                    continue
                full_rec = None
                try:
                    full_rec = d_read.get_decision(did)
                except Exception:
                    full_rec = None
                merged_rec = dict(skeleton)
                if isinstance(full_rec, Mapping):
                    for k in ("decision", "decision_route", "selected_candidate_ref", "reason_codes", "created_at"):
                        if k in full_rec and full_rec[k] is not None:
                            merged_rec[k] = full_rec[k]
                seen_ids.add(did)
                records.append(merged_rec)
        except Exception:
            continue

    # 3. Optional JSON fixture fallback (phone/decision_review.json)
    json_fb = _read_json_readonly(root / "phone" / "decision_review.json")
    if isinstance(json_fb, Mapping):
        fb_rows = json_fb.get("decisions") or json_fb.get("entries") or json_fb.get("items") or []
        if isinstance(fb_rows, list):
            for idx, r in enumerate(fb_rows):
                if isinstance(r, Mapping):
                    did = str(r.get("decision_id") or r.get("id") or f"json_dec_{idx}")
                    if did not in seen_ids:
                        seen_ids.add(did)
                        records.append(dict(r))

    day_records: list[dict[str, Any]] = []
    for rec in records:
        d_str, hhmm, norm_iso = _local_day_and_hhmm(rec.get("created_at") or rec.get("at"))
        if d_str == resolved_day or d_str is None:
            rec_copy = dict(rec)
            rec_copy["_norm_at"] = norm_iso or f"{resolved_day}T09:00:00+08:00"
            rec_copy["_hhmm"] = hhmm if hhmm != "--:--" else "09:00"
            rec_copy["_because_tag"] = map_decision_to_because_tag(rec_copy)
            day_records.append(rec_copy)
    return day_records


def _read_timeline_entries_zero_write(
    root: Path,
    resolved_day: str,
    *,
    service: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """Read today's timeline entries gracefully (never failing on ``CANONICAL_STATE_ABSENT``)."""
    from . import day_timeline as dt_mod

    hook = getattr(dt_mod, "read_today_timeline", None)
    if callable(hook):
        try:
            try:
                ans = hook(root, day=resolved_day)
            except TypeError:
                ans = hook(root)
            if isinstance(ans, Mapping):
                data = ans.get("data") if isinstance(ans.get("data"), Mapping) else ans
                entries = data.get("entries")
                if isinstance(entries, list):
                    return [dict(e) for e in entries if isinstance(e, Mapping)]
            return []
        except Exception:
            return []

    try:
        from . import service as phone_service

        svc = service
        if svc is None:
            inst_doc = _read_json_readonly(root / "instance.json")
            if isinstance(inst_doc, Mapping):
                svc = phone_service.PhoneService(inst_doc)
            else:
                world_home = next(
                    (
                        cand
                        for cand in (root, root / "world", root / "world-home")
                        if (cand / "state" / "world_property_economy.json").is_file()
                    ),
                    None,
                )
                if world_home is not None:
                    svc = phone_service.PhoneService(
                        {
                            "root": str(root),
                            "subject": "chiyo",
                            "world_home": str(world_home),
                            "supply_socket": str(root / "run" / "supply.sock"),
                            "private_items_artifact_id": "",
                            "artifact_grant_id": "",
                            "workspace_id": "",
                            "features": {"CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1"},
                        }
                    )
        if svc is not None:
            ans = svc.day_timeline(subject_view=False)
            if isinstance(ans, Mapping) and ans.get("ok"):
                data = ans.get("data") if isinstance(ans.get("data"), Mapping) else ans
                ans_day = str(data.get("day") or "").strip()
                entries = data.get("entries")
                if isinstance(entries, list) and (not ans_day or ans_day == resolved_day):
                    return [dict(e) for e in entries if isinstance(e, Mapping)]
            explicit_ans = dt_mod.timeline_envelope(
                service=svc,
                state_root=svc._today_state_root(),
                subject_id=svc.subject,
                clock={
                    "source": "system_clock",
                    "logical_at": f"{resolved_day}T12:00:00+08:00",
                    "day": resolved_day,
                },
                subject_view=False,
                world_home=Path(svc.world_home),
                world_clock_injected=svc._world_clock is not None,
            )
            if isinstance(explicit_ans, Mapping) and explicit_ans.get("ok"):
                data = explicit_ans.get("data") if isinstance(explicit_ans.get("data"), Mapping) else explicit_ans
                entries = data.get("entries")
                if isinstance(entries, list):
                    return [dict(e) for e in entries if isinstance(e, Mapping)]
    except Exception:
        pass

    # Direct zero-write fallback on canonical_life_state.json when explicit day differs or service unbound
    fallback_entries: list[dict[str, Any]] = []
    for sr in _discover_life_state_roots_readonly(root):
        canon = _read_json_readonly(sr / "canonical_life_state.json")
        if not isinstance(canon, Mapping):
            continue
        acts = canon.get("activities")
        if not isinstance(acts, Mapping):
            continue
        for ref, act in acts.items():
            if not isinstance(act, Mapping):
                continue
            d_str, _, norm_iso = _local_day_and_hhmm(act.get("started_at"))
            if d_str != resolved_day:
                continue
            fallback_entries.append({
                "kind": "activity",
                "at": norm_iso,
                "title": str(act.get("title") or "日常活动"),
                "status": str(act.get("status") or ""),
                "activity_kind": str(act.get("activity_kind") or ""),
                "source_ref": str(ref),
                "decision_ref": act.get("decision_ref"),
            })
        break
    return fallback_entries


def _build_current_snapshot_zero_write(root: Path) -> dict[str, str]:
    place_name = "房间里"
    posture = "自然坐着"
    availability = "在屋里随时能搭话"
    holding = "手边空着"
    feeling = "安静平稳"

    # 1. World location & body state
    world_candidates = (
        root / "world_snapshot.json",
        root / "world_state.json",
        root / "data" / "world" / "world_state.json",
        root / "world" / "data" / "world" / "world_state.json",
    )
    for wp in world_candidates:
        wdoc = _read_json_readonly(wp)
        if not isinstance(wdoc, Mapping):
            continue
        loc = wdoc.get("location") if isinstance(wdoc.get("location"), Mapping) else {}
        if loc:
            place_name = _human_place_name(
                loc.get("place_id"),
                loc.get("area_id"),
                loc.get("name") or loc.get("place_name"),
            )
            if loc.get("home") is False and availability == "在屋里随时能搭话":
                availability = "正在外头散步透气，看到消息会回"
        body = wdoc.get("body") if isinstance(wdoc.get("body"), Mapping) else {}
        pose_raw = str(body.get("pose") or body.get("posture") or "").strip().lower()
        if pose_raw in _POSTURE_LABELS_ZH:
            posture = _POSTURE_LABELS_ZH[pose_raw]
        held = body.get("held_item") or body.get("holding")
        if isinstance(held, Mapping) and str(held.get("name") or held.get("label") or "").strip():
            holding = f"手里拿着{_clean_warm_text(held.get('name') or held.get('label'), 24)}"
        elif isinstance(held, str) and held.strip() and not held.strip().isascii():
            holding = f"手里拿着{_clean_warm_text(held, 24)}"
        break

    # 2. Somatic comfort (window, cushion, warm drink, local zone, or direct comfort fields)
    som_doc = _read_json_readonly(root / "phone" / "somatic_comfort.json")
    if isinstance(som_doc, Mapping):
        direct_place = _clean_warm_text(som_doc.get("place_name"), 24)
        loc_zone = str(som_doc.get("local_position") or som_doc.get("zone") or "").strip().lower()
        if direct_place and place_name == "房间里":
            place_name = direct_place
        elif loc_zone and place_name == "房间里":
            place_name = _human_place_name(None, loc_zone)

        direct_posture = _clean_warm_text(som_doc.get("posture"), 28)
        if direct_posture:
            posture = _POSTURE_LABELS_ZH.get(direct_posture.lower(), direct_posture)
        care = som_doc.get("care") if isinstance(som_doc.get("care"), Mapping) else {}
        cushion = care.get("cushion") if isinstance(care.get("cushion"), Mapping) else {}
        if str(cushion.get("state") or "").lower() in ("in_use", "used", "active"):
            c_kind = _CUSHION_LABELS_ZH.get(str(cushion.get("kind") or ""), "软垫")
            posture = f"靠着{c_kind}安稳坐着"

        direct_holding = _clean_warm_text(som_doc.get("holding"), 28)
        if direct_holding:
            holding = direct_holding
        drink = care.get("drink") if isinstance(care.get("drink"), Mapping) else {}
        d_state = str(drink.get("state") or "").lower()
        d_label = _DRINK_LABELS_ZH.get(str(drink.get("kind") or ""), "温饮")
        if d_state == "taken":
            holding = f"手边端着{d_label}"
            feeling = "捧着温热饮品，舒展放松"
        elif d_state == "offered":
            holding = f"桌边放着刚备好的{d_label}"

        direct_feeling = _clean_warm_text(som_doc.get("feeling"), 28)
        if direct_feeling and feeling == "安静平稳":
            feeling = direct_feeling
        win = som_doc.get("window")
        win_state = str(win.get("state") if isinstance(win, Mapping) else win or "").strip().lower()
        if win_state in ("open", "cracked", "ajar") and feeling == "安静平稳":
            feeling = "窗边透着微风，舒展放松"

    # 3. Workbench tray / shared work on desk if hands empty
    if holding == "手边空着":
        tray_doc = _read_json_readonly(root / "phone" / "workbench_tray.json")
        if isinstance(tray_doc, Mapping):
            tray = tray_doc.get("tray") if isinstance(tray_doc.get("tray"), Mapping) else tray_doc
            work = tray.get("work") if isinstance(tray, Mapping) else None
            if isinstance(work, Mapping) and work.get("shared") is not False:
                w_title = _clean_warm_text(work.get("title"), 24)
                if w_title:
                    holding = f"手边摊着《{w_title}》书稿"

    # 4. Autonomy rhythm & foreground activity for availability
    rhythm_doc = _read_json_readonly(root / "phone" / "autonomy_rhythm.json")
    if isinstance(rhythm_doc, Mapping):
        aside = rhythm_doc.get("phone_aside") if isinstance(rhythm_doc.get("phone_aside"), Mapping) else {}
        aside_mode = str(aside.get("mode") or "").strip().lower()
        if aside_mode and aside_mode not in ("none", "off", "available", "idle"):
            aside_note = _clean_warm_text(aside.get("note"), 28)
            availability = aside_note or "把手机搁在一边专心忙手头的事"

    for sr in _discover_life_state_roots_readonly(root):
        canon = _read_json_readonly(sr / "canonical_life_state.json")
        if isinstance(canon, Mapping):
            fg_ref = str(canon.get("foreground_activity_ref") or "").strip()
            acts = canon.get("activities") if isinstance(canon.get("activities"), Mapping) else {}
            fg_act = acts.get(fg_ref) if fg_ref and isinstance(acts, Mapping) else None
            if isinstance(fg_act, Mapping) and str(fg_act.get("status") or "").upper() == "ACTIVE":
                fg_title = _clean_warm_text(fg_act.get("title"), 24)
                if availability == "在屋里随时能搭话":
                    availability = f"正专心忙着「{fg_title}」，轻声招呼也能应" if fg_title else "正忙着眼前的小事，轻声招呼也能应"
            break

    # 5. Partner-safe illness / emotional tone for feeling
    eco_doc = _read_json_readonly(root / "phone" / "v03_ecology.json")
    if isinstance(eco_doc, Mapping):
        illness = eco_doc.get("illness") if isinstance(eco_doc.get("illness"), Mapping) else {}
        ill_stage = str(illness.get("stage") or illness.get("status") or "").strip().lower()
        if ill_stage and ill_stage not in ("healthy", "none", "recovered", "ok"):
            feeling = "身体微恙，正放慢节奏安静调养"

    emo_doc = _read_json_readonly(root / "phone" / "emotional_tension.json")
    if isinstance(emo_doc, Mapping) and feeling == "安静平稳":
        mood_summary = _clean_warm_text(
            emo_doc.get("partner_safe_summary") or emo_doc.get("public_mood_note") or "",
            28,
        )
        if mood_summary:
            feeling = mood_summary

    return {
        "place_name": place_name,
        "posture": posture,
        "availability": availability,
        "holding": holding,
        "feeling": feeling,
    }


def _match_decision_because_tag(
    *,
    decisions: list[dict[str, Any]],
    used_decision_ids: set[str],
    decision_ref: Optional[str] = None,
    candidate_ref: Optional[str] = None,
    domain: str = "",
    at_iso: str = "",
) -> str:
    if decision_ref:
        for d in decisions:
            did = str(d.get("decision_id") or "")
            if did and did == str(decision_ref) and d.get("_because_tag"):
                used_decision_ids.add(did)
                return str(d["_because_tag"])
    if candidate_ref:
        for d in decisions:
            did = str(d.get("decision_id") or "")
            if str(d.get("selected_candidate_ref") or "") == str(candidate_ref) and d.get("_because_tag"):
                if did:
                    used_decision_ids.add(did)
                return str(d["_because_tag"])

    item_dt = _parse_iso(at_iso)
    best_tag = ""
    best_delta = 1801.0
    for d in decisions:
        tag = str(d.get("_because_tag") or "")
        if not tag:
            continue
        did = str(d.get("decision_id") or "")
        d_dom, _, _ = _infer_decision_domain_and_title(d, tag)
        if domain and d_dom != domain and not (domain == "activity" and d_dom in ("workbench", "reading", "outing", "desk", "somatic")):
            continue
        d_dt = _parse_iso(d.get("_norm_at"))
        if item_dt is not None and d_dt is not None:
            delta = abs((item_dt - d_dt).total_seconds())
            if delta <= 1800 and delta < best_delta:
                best_delta = delta
                best_tag = tag
                if did:
                    used_decision_ids.add(did)
    return best_tag


def _default_because_for_timeline_entry(entry: Mapping[str, Any], domain: str) -> str:
    kind = str(entry.get("kind") or "").lower()
    act_kind = str(entry.get("activity_kind") or "").upper()
    title = str(entry.get("title") or "")
    if kind == "arrangement" or "便签" in title or "约定" in title:
        return BECAUSE_DESK_NOTE
    if domain == "reading" or "读" in title or "书" in title:
        return BECAUSE_READING
    if domain == "outing" or "MOVE" in act_kind or "散步" in title or "出门" in title:
        return BECAUSE_OUTING
    if domain == "somatic" or "休" in title or "歇" in title:
        return BECAUSE_REST
    if domain == "workbench" or "稿" in title or "写" in title or "作品" in title:
        return BECAUSE_DRAFT
    if kind in ("wallet_tx", "wallet_transaction") or "PURCHASE" in act_kind or "买" in title:
        return BECAUSE_SHOPPING
    if "DAILY_ACTION" in act_kind or "BODY_ACTION" in act_kind:
        return BECAUSE_HABIT
    return ""


def _timeline_entry_to_story_item(
    entry: Mapping[str, Any],
    *,
    resolved_day: str,
    default_place: str,
    decisions: list[dict[str, Any]],
    used_decision_ids: set[str],
) -> Optional[dict[str, Any]]:
    kind = str(entry.get("kind") or "activity").strip().lower()
    _, hhmm, norm_at = _local_day_and_hhmm(entry.get("at") or entry.get("occurred_at"))
    if not norm_at:
        norm_at = f"{resolved_day}T09:00:00+08:00"
        hhmm = "09:00"

    raw_title = _clean_warm_text(entry.get("title"), 60)
    act_kind = str(entry.get("activity_kind") or "").upper()

    domain = "activity"
    detail = "按着自己的节奏把这件事安稳做完了。"
    place_name = default_place

    if kind == "arrangement":
        domain = "desk"
        title = raw_title or "记挂着今天排好的小安排"
        detail = "顺着今天定下的安排留心推进。"
        place_name = "书桌旁"
    elif kind == "world_action":
        action_verb = str(entry.get("action") or "").upper()
        if action_verb == "MOVE":
            domain = "outing"
            place_name = _human_place_name(entry.get("target") or raw_title, None, raw_title)
            title = f"动身去了{place_name}"
            detail = "换个地方走动透气，看看周边的日常景致。"
        elif action_verb in ("ARRANGE", "TOGGLE_FIXTURE", "PLACE", "PICK_UP"):
            domain = "home"
            title = raw_title or "在屋里顺手拾掇了手边物件"
            detail = "把房间里的灯具与物件摆回顺手舒服的位置。"
        else:
            domain = "world"
            title = raw_title or "在屋里活动了一会儿"
            detail = "留下一段真实的日常行动足迹。"
    elif kind == "diary":
        # Never copy private diary body; keep warm skeleton only
        domain = "desk"
        title = "在书桌前安静写了一小段日记"
        detail = "把心里的一点想法收进了自己的本子里。"
        place_name = "书桌旁"
    elif kind == "album":
        domain = "crossmodal"
        title = "往相册里存下了一张眼前的画面"
        detail = "顺手把这一刻的光影留作纪念。"
    elif kind in ("wallet_tx", "wallet_transaction"):
        domain = "ecology"
        raw_item = str(entry.get("item_name") or raw_title or "").strip()
        if raw_item.startswith(("shop_slot:", "shop_item:")):
            raw_item = ""
        item_name = _clean_warm_text(raw_item, 36)
        if item_name:
            title = f"添置了「{item_name}」"
            detail = "按手头预算记下了一笔清清楚楚的日常开销。"
        elif str(entry.get("direction") or "").upper() == "CREDIT":
            title = "记下了一笔日常账目入账"
            detail = "账本里添上了一笔清楚的日常收支记录。"
        else:
            title = "添置了一件日常小物"
            detail = "按手头预算记下了一笔清清楚楚的日常开销。"
    else:
        if "READ" in act_kind or "读" in raw_title or "书" in raw_title:
            domain = "reading"
            place_name = "书桌旁"
        elif "MOVE" in act_kind:
            domain = "outing"
        elif "PURCHASE" in act_kind:
            domain = "ecology"
        elif "WORK" in act_kind or "PROJECT" in act_kind or "稿" in raw_title:
            domain = "workbench"
            place_name = "书桌旁"
        title = raw_title or "专心忙了一会儿手头的事"
        status_up = str(entry.get("status") or "").upper()
        if status_up == "ACTIVE":
            detail = "这件事眼下还在手头顺着往下做。"
        elif status_up in ("PAUSED", "WAITING"):
            detail = "做到一半先轻轻搁下，留着之后接着弄。"

    because_tag = _match_decision_because_tag(
        decisions=decisions,
        used_decision_ids=used_decision_ids,
        decision_ref=str(entry.get("decision_ref") or "") or None,
        candidate_ref=str(entry.get("source_ref") or "") or None,
        domain=domain,
        at_iso=norm_at,
    )
    if not because_tag:
        because_tag = _default_because_for_timeline_entry(entry, domain)

    src_ref = str(entry.get("source_ref") or entry.get("id") or f"{kind}:{norm_at}:{title}")
    return {
        "id": _stable_story_id("tl", kind, src_ref, norm_at),
        "at": norm_at,
        "time_hhmm": hhmm,
        "domain": domain,
        "title": title,
        "detail": detail,
        "place_name": place_name,
        "because_tag": because_tag,
        "jump_target": _JUMP_TARGETS.get(domain, "/today"),
    }


def _consequence_because_tag(receipt: Mapping[str, Any]) -> str:
    dom = str(receipt.get("source_domain") or "").lower()
    trig = str(receipt.get("trigger_kind") or "").lower()
    summary = str(receipt.get("summary_zh") or "")
    joined = f"{dom} {trig} {summary}"
    if any(k in joined for k in ("desk", "note", "便签", "约定")):
        return BECAUSE_DESK_NOTE
    if any(k in joined for k in ("somatic", "illness", "comfort", "drink", "cushion", "累", "歇", "休", "温水", "茶")):
        return BECAUSE_REST
    if any(k in joined for k in ("read", "book", "读", "书")):
        return BECAUSE_READING
    if any(k in joined for k in ("outing", "walk", "neighborhood", "bench", "散步", "风", "出门", "长椅")):
        return BECAUSE_OUTING
    if any(k in joined for k in ("crossmodal", "palette", "curio", "配色", "色", "小物")):
        return BECAUSE_CURIO
    if any(k in joined for k in ("work", "draft", "project", "稿", "作品")):
        return BECAUSE_DRAFT
    return BECAUSE_HABIT


def _collect_consequence_story_items(
    root: Path,
    *,
    resolved_day: str,
    default_place: str,
) -> list[dict[str, Any]]:
    doc = _read_json_readonly(root / "phone" / "v02_v03_consequence_ledger.json")
    if not isinstance(doc, Mapping):
        return []
    receipts = doc.get("receipts")
    if not isinstance(receipts, list):
        receipts = doc.get("entries") or doc.get("items")
    if not isinstance(receipts, list):
        return []

    items: list[dict[str, Any]] = []
    for idx, rec in enumerate(receipts):
        if not isinstance(rec, Mapping):
            continue
        status_up = str(rec.get("status") or "APPLIED").upper()
        if status_up not in ("APPLIED", "OK", "SETTLED"):
            continue
        src_dom = str(rec.get("source_domain") or rec.get("domain") or "home").strip().lower()
        if "intimacy" in src_dom or "private" in src_dom:
            continue
        rec_day = str(rec.get("business_date") or "").strip()
        settled_day, hhmm, norm_at = _local_day_and_hhmm(
            rec.get("settled_at") or rec.get("occurred_at") or rec.get("created_at") or rec.get("at")
        )
        eff_day = rec_day or settled_day
        if eff_day and eff_day != resolved_day:
            continue
        if not norm_at:
            norm_at = f"{resolved_day}T12:00:00+08:00"
            hhmm = "12:00"

        title_zh = _clean_warm_text(
            rec.get("title_zh") or rec.get("title") or rec.get("summary_zh") or rec.get("summary") or rec.get("detail_zh"),
            44,
        )
        summary_zh = _clean_warm_text(
            rec.get("summary_zh") or rec.get("summary") or rec.get("detail_zh") or rec.get("detail") or title_zh,
            80,
        )
        if not summary_zh:
            continue
        domain = (
            "somatic" if "somatic" in src_dom or "illness" in src_dom or "comfort" in src_dom
            else "outing" if "outing" in src_dom or "neighborhood" in src_dom
            else "crossmodal" if "crossmodal" in src_dom
            else "desk" if "desk" in src_dom
            else "ecology" if "ecology" in src_dom or "economy" in src_dom
            else "home"
        )
        rid = str(rec.get("receipt_id") or rec.get("entry_id") or rec.get("id") or f"csq_{idx}")
        items.append({
            "id": _stable_story_id("csq", rid),
            "at": norm_at,
            "time_hhmm": hhmm,
            "domain": domain,
            "title": title_zh or summary_zh[:44],
            "detail": summary_zh,
            "place_name": default_place,
            "because_tag": _consequence_because_tag(rec),
            "jump_target": _JUMP_TARGETS.get(domain, "/home"),
        })
    return items


def _is_partner_safe_shared(row: Mapping[str, Any], *, require_explicit_share: bool = False) -> bool:
    if row.get("private") is True or row.get("is_private") is True:
        return False
    if row.get("shared") is False or row.get("share_with_room") is False:
        return False
    vis = str(row.get("visibility") or "").strip().lower()
    if vis in ("private", "her_private", "subject_private", "private_unshared", "private_intimate"):
        return False
    if require_explicit_share:
        return bool(
            row.get("share_with_room") is True
            or row.get("shared") is True
            or vis in ("shared", "room", "partner", "public_or_shared")
        )
    return True


def _item_matches_story_day(row: Mapping[str, Any], resolved_day: str, *ts_keys: str) -> tuple[bool, str, str]:
    for tk in ts_keys:
        if row.get(tk):
            d_str, hhmm, norm_at = _local_day_and_hhmm(row.get(tk))
            if d_str is not None:
                return (d_str == resolved_day), hhmm, norm_at
    return True, "10:30", f"{resolved_day}T10:30:00+08:00"


def _collect_shared_slice_moments(
    root: Path,
    *,
    resolved_day: str,
    default_place: str,
) -> list[dict[str, Any]]:
    moments: list[dict[str, Any]] = []
    phone_dir = root / "phone"

    # 1. desk_tomorrow_note.json (partner-safe shared notes & tomorrow reminders)
    desk_doc = _read_json_readonly(phone_dir / "desk_tomorrow_note.json")
    if isinstance(desk_doc, Mapping):
        candidates: list[Mapping[str, Any]] = []
        for k in ("shared_notes", "retained_notes", "notes", "tomorrow_items", "items"):
            val = desk_doc.get(k)
            if isinstance(val, list):
                candidates.extend(x for x in val if isinstance(x, Mapping))
        if isinstance(desk_doc.get("partner_note"), Mapping):
            p_note = dict(desk_doc["partner_note"])
            p_note.setdefault("shared", True)
            candidates.append(p_note)
        if not candidates and (desk_doc.get("title") or desk_doc.get("text") or desk_doc.get("note")):
            candidates.append(desk_doc)
        for idx, row in enumerate(candidates):
            author = str(row.get("author_side") or row.get("by") or "").strip().lower()
            is_safe = (author == "partner") or _is_partner_safe_shared(row, require_explicit_share=True)
            if not is_safe:
                continue
            ok_day, hhmm, norm_at = _item_matches_story_day(
                row, resolved_day, "updated_at", "created_at", "recorded_at", "at"
            )
            if not ok_day:
                continue
            text = _clean_warm_text(row.get("title") or row.get("text") or row.get("note") or row.get("summary"), 72)
            if not text:
                continue
            nid = str(row.get("note_id") or row.get("item_id") or row.get("id") or f"desk_{idx}")
            moments.append({
                "id": _stable_story_id("desk", nid),
                "at": norm_at,
                "time_hhmm": hhmm,
                "domain": "desk",
                "title": f"书桌便签：{text[:30]}",
                "detail": text,
                "place_name": "书桌旁",
                "because_tag": BECAUSE_DESK_NOTE,
                "jump_target": "/desk",
            })

    # 2. workbench_curio.json (partner-safe shared curios / workbench items)
    curio_doc = _read_json_readonly(phone_dir / "workbench_curio.json")
    if isinstance(curio_doc, Mapping):
        curio_rows: list[Mapping[str, Any]] = []
        for k in ("curios", "items", "entries", "shared_items", "partner_picks"):
            val = curio_doc.get(k)
            if isinstance(val, list):
                curio_rows.extend(x for x in val if isinstance(x, Mapping))
        if isinstance(curio_doc.get("tray"), Mapping):
            tray_note = curio_doc["tray"].get("note")
            if isinstance(tray_note, Mapping) and _is_partner_safe_shared(tray_note, require_explicit_share=True):
                curio_rows.append(tray_note)
        for idx, row in enumerate(curio_rows):
            if not _is_partner_safe_shared(row, require_explicit_share=True):
                continue
            ok_day, hhmm, norm_at = _item_matches_story_day(
                row, resolved_day, "updated_at", "added_at", "created_at", "at"
            )
            if not ok_day:
                continue
            label = _clean_warm_text(row.get("title") or row.get("name") or row.get("note") or row.get("summary"), 72)
            if not label:
                continue
            cid = str(row.get("curio_id") or row.get("item_id") or row.get("id") or f"curio_{idx}")
            moments.append({
                "id": _stable_story_id("curio", cid),
                "at": norm_at,
                "time_hhmm": hhmm,
                "domain": "workbench",
                "title": f"工作台小物：{label[:30]}",
                "detail": label,
                "place_name": "书桌旁",
                "because_tag": BECAUSE_CURIO,
                "jump_target": "/workbench",
            })

    # 3. outing_bench_bookstore.json (shared bench visits, shared bookstore window bookmarks, partner note)
    outing_doc = _read_json_readonly(phone_dir / "outing_bench_bookstore.json")
    if isinstance(outing_doc, Mapping):
        outing_visits = list(outing_doc.get("bench_visits") or [])
        for extra_k in ("moments", "items"):
            if isinstance(outing_doc.get(extra_k), list):
                outing_visits.extend(outing_doc[extra_k])
        for idx, visit in enumerate(outing_visits):
            if not isinstance(visit, Mapping) or not _is_partner_safe_shared(visit, require_explicit_share=True):
                continue
            ok_day, hhmm, norm_at = _item_matches_story_day(
                visit, resolved_day, "visited_at", "updated_at", "created_at", "at"
            )
            if not ok_day:
                continue
            p_zh = _human_place_name(visit.get("place_id"), None, visit.get("place_name"))
            v_title = _clean_warm_text(visit.get("title"), 38)
            note = _clean_warm_text(visit.get("note") or visit.get("detail"), 80)
            vid = str(visit.get("visit_id") or visit.get("id") or f"bench_{idx}")
            moments.append({
                "id": _stable_story_id("bench", vid),
                "at": norm_at,
                "time_hhmm": hhmm,
                "domain": "outing",
                "title": v_title or f"在{p_zh}坐了一会儿",
                "detail": note or v_title or f"在{p_zh}吹吹风，留下一条共享的外出小记。",
                "place_name": p_zh,
                "because_tag": BECAUSE_OUTING,
                "jump_target": "/outing",
            })
        for idx, bm in enumerate(outing_doc.get("window_bookmarks") or []):
            if not isinstance(bm, Mapping) or bm.get("shared") is not True:
                continue
            ok_day, hhmm, norm_at = _item_matches_story_day(
                bm, resolved_day, "bookmarked_at", "updated_at", "created_at", "at"
            )
            if not ok_day:
                continue
            b_title = _clean_warm_text(bm.get("title") or bm.get("book_title") or bm.get("note") or "橱窗新书", 36)
            b_note = _clean_warm_text(bm.get("note"), 80)
            bid = str(bm.get("bookmark_id") or bm.get("book_code") or f"winbook_{idx}")
            moments.append({
                "id": _stable_story_id("bookstore", bid),
                "at": norm_at,
                "time_hhmm": hhmm,
                "domain": "reading",
                "title": f"在书店橱窗留意到《{b_title}》",
                "detail": b_note or f"路过街角书店橱窗时，把《{b_title}》记进了共享书签。",
                "place_name": "街角书店",
                "because_tag": BECAUSE_READING,
                "jump_target": "/outing",
            })
        p_win_note = outing_doc.get("partner_window_note")
        if isinstance(p_win_note, Mapping) and str(p_win_note.get("note") or p_win_note.get("text") or "").strip():
            ok_day, hhmm, norm_at = _item_matches_story_day(
                p_win_note, resolved_day, "updated_at", "created_at", "at"
            )
            if ok_day:
                n_txt = _clean_warm_text(p_win_note.get("note") or p_win_note.get("text"), 80)
                moments.append({
                    "id": _stable_story_id("bookstore_pnote", norm_at, n_txt),
                    "at": norm_at,
                    "time_hhmm": hhmm,
                    "domain": "outing",
                    "title": "书店橱窗边留了共同小字条",
                    "detail": n_txt,
                    "place_name": "街角书店",
                    "because_tag": BECAUSE_SHARED_ROOM,
                    "jump_target": "/outing",
                })

    # 4. v02_crossmodal.json (strictly share_with_room == True)
    cm_doc = _read_json_readonly(phone_dir / "v02_crossmodal.json")
    if isinstance(cm_doc, Mapping):
        for list_key, dom_tag, b_tag in (
            ("palette_studies", "crossmodal", BECAUSE_CURIO),
            ("work_palette_applications", "workbench", BECAUSE_DRAFT),
            ("reading_uncertainty_marks", "reading", BECAUSE_READING),
            ("collection_items", "crossmodal", BECAUSE_CURIO),
            ("items", "crossmodal", BECAUSE_CURIO),
        ):
            rows = cm_doc.get(list_key)
            if not isinstance(rows, list):
                continue
            for idx, row in enumerate(rows):
                if not isinstance(row, Mapping):
                    continue
                if row.get("share_with_room") is not True:
                    continue
                ok_day, hhmm, norm_at = _item_matches_story_day(
                    row, resolved_day, "updated_at", "created_at", "recorded_at", "applied_at", "at"
                )
                if not ok_day:
                    continue
                title_txt = _clean_warm_text(
                    row.get("title")
                    or row.get("mood_note_cn")
                    or row.get("impression_cn")
                    or row.get("question_cn")
                    or row.get("note")
                    or "留给房间的配色与灵感小记",
                    42,
                )
                detail_txt = _clean_warm_text(
                    row.get("impression_cn")
                    or row.get("mood_note_cn")
                    or row.get("question_cn")
                    or row.get("detail")
                    or row.get("note")
                    or title_txt,
                    90,
                )
                cid = str(
                    row.get("study_id")
                    or row.get("application_id")
                    or row.get("mark_id")
                    or row.get("item_id")
                    or row.get("id")
                    or f"{list_key}_{idx}"
                )
                moments.append({
                    "id": _stable_story_id("cm", list_key, cid),
                    "at": norm_at,
                    "time_hhmm": hhmm,
                    "domain": dom_tag,
                    "title": title_txt,
                    "detail": detail_txt,
                    "place_name": "书桌旁" if dom_tag in ("workbench", "reading") else default_place,
                    "because_tag": b_tag,
                    "jump_target": "/crossmodal",
                })

    # 5. somatic_comfort.json (partner-safe care offerings & window comfort on resolved_day)
    som_doc = _read_json_readonly(phone_dir / "somatic_comfort.json")
    if isinstance(som_doc, Mapping):
        care = som_doc.get("care") if isinstance(som_doc.get("care"), Mapping) else {}
        drink = care.get("drink") if isinstance(care.get("drink"), Mapping) else {}
        if drink and str(drink.get("state") or "").lower() in ("offered", "taken", "consumed"):
            ok_day, hhmm, norm_at = _item_matches_story_day(
                drink, resolved_day, "updated_at", "offered_at", "taken_at", "consumed_at", "at"
            )
            if ok_day:
                d_name = _DRINK_LABELS_ZH.get(str(drink.get("kind") or ""), "温饮")
                d_state = str(drink.get("state") or "").lower()
                d_title = (
                    f"喝下了桌边的{d_name}" if d_state == "consumed"
                    else f"端起了桌边的{d_name}" if d_state == "taken"
                    else f"桌边备好了一杯{d_name}"
                )
                moments.append({
                    "id": _stable_story_id("somatic_drink", drink.get("offer_id") or norm_at, d_state),
                    "at": norm_at,
                    "time_hhmm": hhmm,
                    "domain": "somatic",
                    "title": d_title,
                    "detail": f"用一杯温热的{d_name}照顾当下的身体状态。",
                    "place_name": _human_place_name(drink.get("place_id"), drink.get("area_id"), default_place),
                    "because_tag": BECAUSE_REST,
                    "jump_target": "/body",
                })
        cushion = care.get("cushion") if isinstance(care.get("cushion"), Mapping) else {}
        if cushion and str(cushion.get("state") or "").lower() in ("offered", "in_use", "used"):
            ok_day, hhmm, norm_at = _item_matches_story_day(
                cushion, resolved_day, "updated_at", "offered_at", "used_at", "at"
            )
            if ok_day:
                c_name = _CUSHION_LABELS_ZH.get(str(cushion.get("kind") or ""), "靠垫")
                moments.append({
                    "id": _stable_story_id("somatic_cushion", cushion.get("offer_id") or norm_at),
                    "at": norm_at,
                    "time_hhmm": hhmm,
                    "domain": "somatic",
                    "title": f"垫好了身后的{c_name}",
                    "detail": f"把{c_name}调整到舒服的位置，让久坐的身体轻松一点。",
                    "place_name": _human_place_name(cushion.get("place_id"), cushion.get("area_id"), default_place),
                    "because_tag": BECAUSE_REST,
                    "jump_target": "/body",
                })
        win = som_doc.get("window")
        win_state = str(win.get("state") if isinstance(win, Mapping) else win or "").strip().lower()
        emitted_som_summary = False
        if win_state in _WINDOW_LABELS_ZH and som_doc.get("updated_at"):
            ok_day, hhmm, norm_at = _item_matches_story_day(som_doc, resolved_day, "updated_at")
            if ok_day:
                emitted_som_summary = True
                moments.append({
                    "id": _stable_story_id("somatic_window", win_state, norm_at),
                    "at": norm_at,
                    "time_hhmm": hhmm,
                    "domain": "somatic",
                    "title": f"走去窗边{_WINDOW_LABELS_ZH[win_state]}",
                    "detail": "顺着屋里的冷暖体感调整了窗扇开合。",
                    "place_name": "窗台边",
                    "because_tag": BECAUSE_HABIT,
                    "jump_target": "/body",
                })
        som_summary = _clean_warm_text(som_doc.get("summary") or som_doc.get("comfort_note"), 80)
        if som_summary and not emitted_som_summary and som_doc.get("shared") is not False:
            ok_day, hhmm, norm_at = _item_matches_story_day(som_doc, resolved_day, "updated_at", "at")
            if ok_day:
                moments.append({
                    "id": _stable_story_id("somatic_summary", norm_at, som_summary[:24]),
                    "at": norm_at,
                    "time_hhmm": hhmm,
                    "domain": "somatic",
                    "title": _clean_warm_text(som_doc.get("title") or som_summary, 38),
                    "detail": som_summary,
                    "place_name": _clean_warm_text(som_doc.get("place_name"), 24) or default_place,
                    "because_tag": BECAUSE_REST,
                    "jump_target": "/body",
                })

    return moments


def _compose_headline(snapshot: Mapping[str, str], story_items: list[dict[str, Any]]) -> str:
    place = snapshot.get("place_name") or "房间里"
    feeling = snapshot.get("feeling") or "安静平稳"
    total = len(story_items)
    if total == 0:
        return f"今天在{place}安安静静地待着，状态{feeling}，还没有新的忙碌记录。"
    latest = story_items[-1]
    latest_title = str(latest.get("title") or "").strip()
    if total == 1:
        return f"今天记下了 1 段日常小片段：{latest_title}，眼下在{place}{feeling}。"
    return f"今天顺着日常心意留下了 {total} 段生活足迹，最近刚「{latest_title}」，此刻在{place}{feeling}。"


def read_today_story_zero_write(
    day_root: Path | str,
    *,
    day: Optional[str] = None,
    service: Optional[Any] = None,
) -> dict[str, Any]:
    """Read unified causal day story with 100% zero disk writes and zero LLM calls."""
    root = Path(day_root)
    resolved_day = _resolve_story_day_readonly(root, day)
    snapshot = _build_current_snapshot_zero_write(root)
    default_place = snapshot["place_name"]

    decisions = _read_decisions_zero_write(root, resolved_day)
    used_decision_ids: set[str] = set()

    story_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # 1. Timeline items + decision causal attribution
    timeline_entries = _read_timeline_entries_zero_write(
        root, resolved_day, service=service
    )
    for entry in timeline_entries:
        item = _timeline_entry_to_story_item(
            entry,
            resolved_day=resolved_day,
            default_place=default_place,
            decisions=decisions,
            used_decision_ids=used_decision_ids,
        )
        if item is not None and item["id"] not in seen_ids:
            seen_ids.add(item["id"])
            story_items.append(item)

    # 2. Standalone causal decisions not yet represented by a timeline row
    for dec in decisions:
        did = str(dec.get("decision_id") or "").strip()
        tag = str(dec.get("_because_tag") or "").strip()
        if not tag or (did and did in used_decision_ids):
            continue
        dec_verb = str(dec.get("decision") or "").strip().upper()
        if dec_verb in {"HOLD", "KEEP_IDLE", "DEFER", "DROP"}:
            continue
        dom, title, detail = _infer_decision_domain_and_title(dec, tag)
        sid = _stable_story_id("dec", did or dec.get("_norm_at"), tag)
        if sid not in seen_ids:
            seen_ids.add(sid)
            story_items.append({
                "id": sid,
                "at": str(dec.get("_norm_at") or f"{resolved_day}T09:00:00+08:00"),
                "time_hhmm": str(dec.get("_hhmm") or "09:00"),
                "domain": dom,
                "title": title,
                "detail": detail,
                "place_name": "书桌旁" if dom in ("desk", "workbench", "reading") else default_place,
                "because_tag": tag,
                "jump_target": _JUMP_TARGETS.get(dom, "/today"),
            })

    # 3. Consequence ledger items
    for csq_item in _collect_consequence_story_items(
        root, resolved_day=resolved_day, default_place=default_place
    ):
        if csq_item["id"] not in seen_ids:
            seen_ids.add(csq_item["id"])
            story_items.append(csq_item)

    # 4. Partner-safe shared moments from phone slices
    for slice_item in _collect_shared_slice_moments(
        root, resolved_day=resolved_day, default_place=default_place
    ):
        if slice_item["id"] not in seen_ids:
            seen_ids.add(slice_item["id"])
            story_items.append(slice_item)

    story_items.sort(key=lambda row: (str(row.get("at") or ""), str(row.get("id") or "")))
    causal_tagged = sum(1 for row in story_items if str(row.get("because_tag") or "").strip())
    headline = _compose_headline(snapshot, story_items)

    return {
        "ok": True,
        "status": "ok",
        "read_only": True,
        "llm_calls": 0,
        "data": {
            "schema": VIEW_SCHEMA,
            "day": resolved_day,
            "headline": headline,
            "current_snapshot": snapshot,
            "story_items": story_items,
            "counts": {
                "total": len(story_items),
                "causal_tagged": causal_tagged,
            },
        },
    }


__all__ = [
    "BECAUSE_CURIO",
    "BECAUSE_DESK_NOTE",
    "BECAUSE_DRAFT",
    "BECAUSE_HABIT",
    "BECAUSE_OUTING",
    "BECAUSE_READING",
    "BECAUSE_REST",
    "BECAUSE_SHARED_ROOM",
    "BECAUSE_SHOPPING",
    "ENDPOINT_PATH",
    "VIEW_SCHEMA",
    "map_decision_to_because_tag",
    "read_today_story_zero_write",
]

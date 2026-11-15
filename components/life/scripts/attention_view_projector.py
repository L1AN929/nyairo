#!/usr/bin/env python3
"""v0.3 有限视野注意投影器、未读页码校验与「局部误解 -> 新证据 -> 修订」谱系（第 23 / 30 章）。

设计对齐
--------
* 第 30 章（有限注意与看法修订实现方案）：
  - ``build_attention_view(snapshot_or_root, now, limits)``：纯函数只读投影（零网络、零全库透视、
    零模型调用、零写入），输出 ``proposed.attention_view.v1``；
  - ``concerns`` 默认上限 2 条（优先当前作品关联，其次当前材料关联），``visible_cues`` 默认上限 6 条，
    ``current_views`` 默认上限 3 条；明确区分 ``AVAILABLE`` / ``EMPTY`` / ``SOURCE_UNAVAILABLE`` / ``ALL_STALE``；
  - ``verify_page_obtained``：对具体页码发表/修订看法前校验该页是否真实已读/已取得，
    未取得页码拒绝并返回 ``VIEW_PAGE_NOT_OBTAINED``（``409``）。
* 第 23 章（有限视野、误解与重新认识）：
  - 关闭/上锁抽屉（``closed`` / ``locked``）及未打开父容器内的物件绝不透视进 ``visible_cues``；
  - 背着主体被挪动的物件在重新观察前不泄漏新落点，保留首次伸手摸空（``ITEM_NOT_AT_EXPECTED_SPOT``）的认知边界；
  - 构建「局部初印象/误解 -> 新证据（新读到页码/亲眼观察） -> 局部看法修订」链，不抹除旧印象，不泛化翻转整体评价。
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ATTENTION_VIEW_KIND = "proposed.attention_view.v1"

DEFAULT_LIMITS = {
    "max_concerns": 2,
    "max_visible_cues": 6,
    "max_current_views": 3,
}

NON_PAGE_ANCHORS = frozenset({
    "cover",
    "front_cover",
    "back_cover",
    "spine",
    "toc",
    "table_of_contents",
    "preface",
    "blurb",
    "colophon",
    "overall_surface",
})

_NUMERIC_PAGE_PATTERNS = (
    re.compile(r"^(\d+)$"),
    re.compile(r"^[pP]\.?\s*(\d+)$"),
    re.compile(r"^page[:_\-\s]*(\d+)$", re.IGNORECASE),
    re.compile(r"^第\s*(\d+)\s*页$"),
)


def parse_page_number(page_or_anchor: Any) -> int | None:
    """Extract 1-based integer page number if ``page_or_anchor`` names a specific page; else ``None``."""
    if isinstance(page_or_anchor, bool) or page_or_anchor is None:
        return None
    if isinstance(page_or_anchor, int):
        return page_or_anchor if page_or_anchor > 0 else None
    if not isinstance(page_or_anchor, str):
        return None
    cleaned = page_or_anchor.strip()
    if not cleaned:
        return None
    if cleaned.lower() in NON_PAGE_ANCHORS:
        return None
    for pat in _NUMERIC_PAGE_PATTERNS:
        m = pat.match(cleaned)
        if m:
            val = int(m.group(1))
            return val if val > 0 else None
    return None


def _extract_obtained_pages_from_entry(entry: Mapping[str, Any]) -> tuple[bool, set[int]]:
    """Return ``(tracks_pages, obtained_page_set)`` from a reading material / workbench entry."""
    tracks = False
    pages: set[int] = set()

    for list_key in ("obtained_pages", "read_pages", "pages_obtained", "unlocked_pages"):
        if list_key in entry and isinstance(entry[list_key], list):
            tracks = True
            for raw_p in entry[list_key]:
                p_num = parse_page_number(raw_p)
                if p_num is not None:
                    pages.add(p_num)

    for bound_key in ("max_obtained_page", "highest_read_page", "read_up_to_page"):
        if bound_key in entry:
            raw_b = entry[bound_key]
            if not isinstance(raw_b, bool) and isinstance(raw_b, int) and raw_b >= 0:
                tracks = True
                for p in range(1, raw_b + 1):
                    pages.add(p)

    if "total_pages" in entry and not tracks and entry.get("enforce_page_gate") is True:
        tracks = True

    return tracks, pages


def discover_obtained_pages_index(
    day_root: Path | str,
    *,
    extra_obtained_by_scope: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Zero-write discovery of obtained/read pages per ``scope_ref`` across Supply, Workbench & Stance state."""
    root = Path(day_root)
    index: dict[str, dict[str, Any]] = {}

    def _merge_entry(scope_ref: str, entry: Mapping[str, Any]) -> None:
        sref = scope_ref.strip()
        if not sref:
            return
        tracks, pages = _extract_obtained_pages_from_entry(entry)
        cur = index.setdefault(
            sref,
            {
                "scope_ref": sref,
                "tracks_pages": False,
                "obtained_pages": set(),
                "title": entry.get("title"),
                "total_pages": entry.get("total_pages"),
                "visible_toc": list(entry.get("visible_toc") or [])
                if isinstance(entry.get("visible_toc"), list)
                else [],
            },
        )
        if tracks:
            cur["tracks_pages"] = True
            cur["obtained_pages"].update(pages)
        if entry.get("title") and not cur.get("title"):
            cur["title"] = str(entry["title"])
        if isinstance(entry.get("total_pages"), int) and not isinstance(entry.get("total_pages"), bool):
            cur["total_pages"] = int(entry["total_pages"])

    candidates = (
        root / "supply" / "reading_materials.json",
        root / "phone" / "reading_workbench.json",
        root / "life" / "reading_workbench.json",
    )
    for cpath in candidates:
        if not cpath.exists() or cpath.is_symlink():
            continue
        try:
            doc = json.loads(cpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(doc, Mapping):
            items = doc.get("materials") or doc.get("items") or doc.get("books")
            if isinstance(items, list):
                for row in items:
                    if isinstance(row, Mapping):
                        sref = str(row.get("scope_ref") or row.get("material_id") or row.get("book_id") or "")
                        _merge_entry(sref, row)
            elif isinstance(items, Mapping):
                for k, row in items.items():
                    if isinstance(row, Mapping):
                        _merge_entry(str(row.get("scope_ref") or k), row)
            single_sref = doc.get("scope_ref") or doc.get("material_id")
            if isinstance(single_sref, str) and single_sref.strip():
                _merge_entry(single_sref, doc)

    if isinstance(extra_obtained_by_scope, Mapping):
        for sref, raw_val in extra_obtained_by_scope.items():
            if isinstance(raw_val, Mapping):
                _merge_entry(str(sref), raw_val)
            elif isinstance(raw_val, list):
                _merge_entry(str(sref), {"obtained_pages": raw_val})

    out: dict[str, dict[str, Any]] = {}
    for sref, info in index.items():
        out[sref] = {
            "scope_ref": sref,
            "tracks_pages": bool(info["tracks_pages"]),
            "obtained_pages": sorted(info["obtained_pages"]),
            "title": info.get("title"),
            "total_pages": info.get("total_pages"),
            "visible_toc": info.get("visible_toc", []),
        }
    return out


def verify_page_obtained(
    day_root: Path | str,
    *,
    scope_ref: str,
    page_or_anchor: Any,
    extra_obtained_by_scope: Mapping[str, Any] | None = None,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Verify whether ``page_or_anchor`` is legally obtained for ``scope_ref``.

    Returns ``(ok, reason_code, detail)``.
    - Non-page anchors (``cover``, ``spine``, ``toc``, section labels) are allowed without page lock.
    - If ``scope_ref`` tracks obtained pages (in ``reading_workbench.json``, ``supply/reading_materials.json``,
      or ``extra_obtained_by_scope``) and the target numeric page has NOT been obtained yet,
      returns ``(False, "VIEW_PAGE_NOT_OBTAINED", detail)``.
    """
    page_num = parse_page_number(page_or_anchor)
    if page_num is None:
        return True, None, {"page_number": None, "gate_checked": False}

    idx = discover_obtained_pages_index(day_root, extra_obtained_by_scope=extra_obtained_by_scope)
    info = idx.get(scope_ref.strip())
    if info is None or not info.get("tracks_pages"):
        return True, None, {"page_number": page_num, "gate_checked": False}

    obtained = set(info.get("obtained_pages") or [])
    total_pages = info.get("total_pages")
    if isinstance(total_pages, int) and page_num > total_pages:
        return (
            False,
            "VIEW_PAGE_NOT_OBTAINED",
            {
                "scope_ref": scope_ref,
                "requested_page": page_num,
                "obtained_pages": sorted(obtained),
                "total_pages": total_pages,
            },
        )
    if page_num not in obtained:
        return (
            False,
            "VIEW_PAGE_NOT_OBTAINED",
            {
                "scope_ref": scope_ref,
                "requested_page": page_num,
                "obtained_pages": sorted(obtained),
            },
        )
    return (
        True,
        None,
        {
            "scope_ref": scope_ref,
            "requested_page": page_num,
            "obtained_pages": sorted(obtained),
            "gate_checked": True,
        },
    )


def build_misunderstanding_revision_chain(
    stances: list[Mapping[str, Any]],
    *,
    include_unshared: bool = True,
) -> list[dict[str, Any]]:
    """Build deterministic Ch. 23 chains: ``initial_understanding -> new_evidence -> revised_understanding``."""
    by_id: dict[str, dict[str, Any]] = {}
    for row in stances:
        if not isinstance(row, Mapping):
            continue
        if row.get("status") == "SOURCE_WITHDRAWN":
            continue
        if not include_unshared and not row.get("shared_with_partner"):
            continue
        sid = str(row.get("stance_id") or "")
        if sid:
            by_id[sid] = copy.deepcopy(dict(row))

    chains: list[dict[str, Any]] = []
    for sid, cur in by_id.items():
        prev_id = cur.get("replaces_stance_id")
        if not prev_id or prev_id not in by_id:
            continue
        prev = by_id[prev_id]
        chains.append(
            {
                "chain_id": f"revchain_{prev_id}_to_{sid}",
                "scope_ref": cur.get("scope_ref"),
                "facet": cur.get("facet"),
                "initial_understanding": {
                    "stance_id": prev_id,
                    "page_or_anchor": prev.get("page_or_anchor"),
                    "text": prev.get("text"),
                    "status": prev.get("status"),
                },
                "new_evidence_anchor": cur.get("page_or_anchor"),
                "revised_understanding": {
                    "stance_id": sid,
                    "page_or_anchor": cur.get("page_or_anchor"),
                    "text": cur.get("text"),
                    "status": cur.get("status"),
                },
                "other_facets_preserved": True,
                "old_impression_retained_in_history": True,
            }
        )
    return chains


def _is_container_interior_visible(
    container_id: str,
    containers_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Closed or locked containers (or descendants of closed/locked ancestors) hide interior items."""
    cur_id: str | None = container_id
    visited: set[str] = set()
    while cur_id and cur_id not in visited:
        visited.add(cur_id)
        c_row = containers_by_id.get(cur_id)
        if not isinstance(c_row, Mapping):
            return False
        open_state = str(c_row.get("open_state") or "open_top")
        if open_state in ("closed", "locked"):
            return False
        parent = c_row.get("parent_container_id")
        cur_id = str(parent) if isinstance(parent, str) and parent else None
    return True


def _load_snapshot_from_root(day_root: Path) -> dict[str, Any]:
    """Zero-write helper to assemble bounded snapshot from ``day_root``."""
    from components.life.scripts import life_attention_stance_containment as owner_sc

    proj = owner_sc.read_attention_stance_containment(day_root, subject_view=True)
    obtained_idx = discover_obtained_pages_index(
        day_root,
        extra_obtained_by_scope=proj.get("stance_revisions", {}).get("obtained_pages_by_scope"),
    )
    return {
        "subject_ref": proj.get("character", {}).get("pack_id") or "subject",
        "activity_ref": (
            proj.get("shared_desk_table", {}).get("primary_activity", {}).get("activity_id")
            if isinstance(proj.get("shared_desk_table"), Mapping)
            else None
        ),
        "stance_revisions": proj.get("stance_revisions", {}),
        "object_containment": proj.get("object_containment", {}),
        "reading_materials": obtained_idx,
        "state_status": proj.get("state_status", "ok"),
    }


def build_attention_view(
    snapshot_or_root: Mapping[str, Any] | Path | str,
    now: str | datetime | None = None,
    limits: Mapping[str, int] | None = None,
    *,
    subject_ref: str | None = None,
    activity_ref: str | None = None,
    active_scope_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Pure Ch. 30 §3 ``build_attention_view`` implementation (zero-write, zero-omniscience)."""
    if isinstance(snapshot_or_root, (Path, str)):
        snapshot = _load_snapshot_from_root(Path(snapshot_or_root))
    elif isinstance(snapshot_or_root, Mapping):
        snapshot = dict(snapshot_or_root)
    else:
        raise TypeError("snapshot_or_root must be a Mapping or Path")

    if isinstance(now, datetime):
        observed_at = now.isoformat(timespec="seconds")
    elif isinstance(now, str) and now.strip():
        observed_at = now.strip()
    else:
        observed_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    lim = dict(DEFAULT_LIMITS)
    if isinstance(limits, Mapping):
        for k in DEFAULT_LIMITS:
            if k in limits and not isinstance(limits[k], bool) and isinstance(limits[k], int) and limits[k] >= 0:
                lim[k] = limits[k]

    resolved_subject = str(subject_ref or snapshot.get("subject_ref") or "subject")
    resolved_activity = activity_ref if activity_ref is not None else snapshot.get("activity_ref")

    if snapshot.get("state_status") in ("degraded", "unavailable", "SOURCE_UNAVAILABLE"):
        return {
            "kind": ATTENTION_VIEW_KIND,
            "observed_at": observed_at,
            "subject_ref": resolved_subject,
            "activity_ref": resolved_activity,
            "concerns": [],
            "visible_cues": [],
            "current_views": [],
            "revision_chains": [],
            "truncated": False,
            "availability": "SOURCE_UNAVAILABLE",
            "omniscience_blocked": True,
            "llm_calls": 0,
        }

    # 1. Concerns (max 2 by default; explicit private notes / unclear items first)
    raw_concerns = snapshot.get("concerns") if isinstance(snapshot.get("concerns"), list) else []
    normalized_concerns: list[dict[str, Any]] = []
    for idx, c in enumerate(raw_concerns):
        if not isinstance(c, Mapping):
            continue
        text = str(c.get("text") or "").strip()
        sref = str(c.get("scope_ref") or "").strip()
        if not text:
            continue
        normalized_concerns.append(
            {
                "ref": str(c.get("ref") or f"concern_{idx + 1}"),
                "scope_ref": sref or "general",
                "text": text[:240],
                "origin": str(c.get("origin") or "EXPLICIT_PRIVATE_NOTE"),
            }
        )

    truncated = False
    if len(normalized_concerns) > lim["max_concerns"]:
        truncated = True
        normalized_concerns = normalized_concerns[: lim["max_concerns"]]

    # 2. Visible cues (strictly bounded to current room + open containers + obtained pages!)
    visible_cues: list[dict[str, Any]] = []
    containment = snapshot.get("object_containment")
    if isinstance(containment, Mapping):
        subj_loc = containment.get("subject_location") if isinstance(containment.get("subject_location"), Mapping) else {}
        subj_room = subj_loc.get("room_id")
        subj_present = bool(subj_loc.get("present", True))
        containers_list = containment.get("containers") if isinstance(containment.get("containers"), list) else []
        containers_by_id = {
            str(c.get("container_id")): c for c in containers_list if isinstance(c, Mapping) and c.get("container_id")
        }
        items_list = containment.get("items") if isinstance(containment.get("items"), list) else []

        if subj_present:
            # Surface containers in current room (closed containers appear as closed exteriors, NOT interiors!)
            for c in containers_list:
                if not isinstance(c, Mapping):
                    continue
                if subj_room and c.get("room_id") != subj_room:
                    continue
                parent_cid = c.get("parent_container_id")
                if parent_cid and not _is_container_interior_visible(str(parent_cid), containers_by_id):
                    continue
                open_st = str(c.get("open_state") or "open_top")
                visible_cues.append(
                    {
                        "cue_kind": "container_exterior" if open_st in ("closed", "locked") else "open_container",
                        "ref": str(c.get("container_id")),
                        "label": str(c.get("label") or c.get("container_id")),
                        "place_id": c.get("effective_place_id") or c.get("place_id"),
                        "open_state": open_st,
                        "interior_visible": open_st not in ("closed", "locked"),
                    }
                )

            # Items visible in hand, on surface in current room, or inside OPEN containers in current room
            for it in items_list:
                if not isinstance(it, Mapping):
                    continue
                pkind = str(it.get("placement_kind") or "surface")
                if pkind == "hand":
                    visible_cues.append(
                        {
                            "cue_kind": "held_item",
                            "ref": str(it.get("item_id")),
                            "label": str(it.get("label") or it.get("item_id")),
                            "held_in_hand": it.get("held_in_hand"),
                        }
                    )
                    continue
                it_room = it.get("room_id")
                if it_room is not None and subj_room and it_room != subj_room:
                    continue
                if pkind == "container":
                    cid = str(it.get("parent_container_id") or it.get("container_id") or "")
                    if not _is_container_interior_visible(cid, containers_by_id):
                        # Closed/locked drawer: NEVER leak interior items into AttentionView!
                        continue
                # Also do not x-ray items whose impression is stale until subject observes that micro-zone!
                if it.get("impression_stale"):
                    continue
                visible_cues.append(
                    {
                        "cue_kind": "visible_item",
                        "ref": str(it.get("item_id")),
                        "label": str(it.get("label") or it.get("item_id")),
                        "place_id": it.get("effective_place_id") or it.get("place_id"),
                    }
                )

    # Reading materials: expose ONLY title/cover/TOC and explicitly obtained page numbers (never unread body!)
    materials = snapshot.get("reading_materials")
    if isinstance(materials, Mapping):
        for sref, minfo in sorted(materials.items()):
            if not isinstance(minfo, Mapping):
                continue
            if active_scope_refs is not None and sref not in active_scope_refs:
                continue
            visible_cues.append(
                {
                    "cue_kind": "reading_material_surface",
                    "ref": sref,
                    "title": minfo.get("title") or sref,
                    "obtained_pages": list(minfo.get("obtained_pages") or []),
                    "unread_pages_withheld": True,
                }
            )

    if len(visible_cues) > lim["max_visible_cues"]:
        truncated = True
        visible_cues = visible_cues[: lim["max_visible_cues"]]

    # 3. Current views (max 3; filter out stale/withdrawn; distinguish ALL_STALE vs EMPTY)
    stance_rev = snapshot.get("stance_revisions") if isinstance(snapshot.get("stance_revisions"), Mapping) else {}
    raw_current = stance_rev.get("current_stances") if isinstance(stance_rev.get("current_stances"), list) else []
    raw_stale = stance_rev.get("stale_stances") if isinstance(stance_rev.get("stale_stances"), list) else []
    raw_lineage = stance_rev.get("lineage_history") if isinstance(stance_rev.get("lineage_history"), list) else []

    filtered_views: list[dict[str, Any]] = []
    for st in raw_current:
        if not isinstance(st, Mapping):
            continue
        if st.get("status") != "ACTIVE":
            continue
        sref = str(st.get("scope_ref") or "")
        if active_scope_refs is not None and sref not in active_scope_refs:
            continue
        filtered_views.append(
            {
                "stance_id": st.get("stance_id"),
                "scope_ref": sref,
                "source_revision": st.get("source_revision"),
                "page_or_anchor": st.get("page_or_anchor"),
                "facet": st.get("facet"),
                "text": st.get("text"),
                "replaces_stance_id": st.get("replaces_stance_id"),
            }
        )

    if len(filtered_views) > lim["max_current_views"]:
        truncated = True
        filtered_views = filtered_views[: lim["max_current_views"]]

    if filtered_views or visible_cues or normalized_concerns:
        availability = "AVAILABLE"
    elif raw_stale and not raw_current:
        availability = "ALL_STALE"
    else:
        availability = "EMPTY"

    chains = build_misunderstanding_revision_chain(raw_lineage, include_unshared=True)

    return {
        "kind": ATTENTION_VIEW_KIND,
        "observed_at": observed_at,
        "subject_ref": resolved_subject,
        "activity_ref": resolved_activity,
        "concerns": normalized_concerns,
        "visible_cues": visible_cues,
        "current_views": filtered_views,
        "revision_chains": chains,
        "truncated": truncated,
        "availability": availability,
        "omniscience_blocked": True,
        "llm_calls": 0,
    }


__all__ = [
    "ATTENTION_VIEW_KIND",
    "DEFAULT_LIMITS",
    "NON_PAGE_ANCHORS",
    "build_attention_view",
    "build_misunderstanding_revision_chain",
    "discover_obtained_pages_index",
    "parse_page_number",
    "verify_page_obtained",
]

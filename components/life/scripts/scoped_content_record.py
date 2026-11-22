#!/usr/bin/env python3
"""跨组件共享的「有作用域内容记录」最小底座。

v08 ``emergent_echoes`` 的 future_self_questions / shared_idioms /
memory_reinterpretations / lingering_threads / day_directions，与 v03
``epistemic_encounters`` 的 encounters / shared_materials，四组横切规则长期各写一遍：

1. 作者、可见性（private / shared / shared_excerpt / unconfirmed_idiom）、
   封存与撤回、来源引用、生命周期（``consider_after`` 与只到点不续期的 ``expires_at``）；
2. 原始事实冻结：后续重释或欣赏只能追加，绝不覆写 ``original_fact_cn`` /
   ``prior_impressions``；
3. 三道门：撤回的全场隐身；封存 / ``RECEIVED_UNOPENED`` 只露存在元数据，
   正文在真正打开前扣留；可见性 + ``consider_after`` + 按钟表钉死的 ``expires_at``，
   读取永不续期；
4. 关切只改排序、从不授予阅读权（``rank_authorized_by_concern``：先过滤授权，
   再对匹配项排序）。

本模块只做这四件事：纯标准库、确定性、零大模型调用（``llm_calls == 0``）、
零写入、零变异。它不定义新的 HTTP/JSON 契约，只供两个上层模块共用。
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Optional

AUTHOR_SIDES: frozenset[str] = frozenset({"subject", "partner"})
VISIBILITIES: frozenset[str] = frozenset({
    "private",
    "shared",
    "shared_excerpt",
    "unconfirmed_idiom",
})

STATUS_WITHDRAWN = "WITHDRAWN"
STATUS_RECEIVED_UNOPENED = "RECEIVED_UNOPENED"

FROZEN_FACT_IMMUTABLE = "FROZEN_FACT_IMMUTABLE"

#: 默认取正文的字段；调用方可用 ``text_fn`` 覆盖。
DEFAULT_TEXT_KEYS: tuple[str, ...] = (
    "original_fact_cn",
    "current_view_cn",
    "new_view_cn",
    "prior_view_cn",
    "text_cn",
    "body_cn",
    "content_cn",
    "excerpt_cn",
    "reaction_cn",
    "title_cn",
    "topic_tag",
    "summary_cn",
    "question_cn",
    "future_question_cn",
    "idiom_cn",
    "phrase_cn",
    "direction_cn",
    "impression_cn",
    "current_impression_cn",
    "noticed_detail_cn",
    "target_title_cn",
    "active_question_cn",
    "horizon_note_cn",
    "bound_ref",
)

_NORMALIZE_RE = re.compile(r"[^0-9a-z㐀-䶿一-鿿]+")


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------


def _parse_iso_utc(raw: Any) -> Optional[datetime]:
    """Parse an ISO timestamp; naive values are treated as UTC. Never raises."""

    if not isinstance(raw, str) or not raw.strip():
        return None
    txt = raw.strip()
    if txt.endswith(("Z", "z")):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _first_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _validate_choice(value: Any, allowed: frozenset[str], code: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{code}: {value!r}")
    return value


def _envelope(row: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = row.get("content_envelope")
    return raw if isinstance(raw, Mapping) else {}


def _effective_visibility(row: Mapping[str, Any], *, visibility_field: str) -> str:
    """Resolve visibility from the explicit field, envelope, or legacy flags."""

    explicit = row.get(visibility_field)
    if isinstance(explicit, str) and explicit in VISIBILITIES:
        return explicit

    envelope_visibility = _envelope(row).get("visibility")
    if isinstance(envelope_visibility, str) and envelope_visibility in VISIBILITIES:
        return envelope_visibility

    if "shared_with_partner" in row:
        return "shared" if row.get("shared_with_partner") is True else "private"
    if "shared" in row:
        return "shared" if row.get("shared") is True else "private"
    return "private"


def _effective_author(row: Mapping[str, Any], *, author_field: str) -> str:
    envelope_author = _envelope(row).get("author_side")
    return _first_str(
        row.get(author_field),
        envelope_author,
        row.get("proposed_by"),
    )


def _effective_status(row: Mapping[str, Any], *, status_field: str) -> str:
    return _first_str(row.get(status_field), row.get("status"))


def _normalize(text: Any) -> str:
    return _NORMALIZE_RE.sub("", str(text).casefold())


def _bigrams(text: str) -> set[str]:
    if not text:
        return set()
    if len(text) == 1:
        return {text}
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _default_text(row: Mapping[str, Any]) -> str:
    parts = [row.get(key) for key in DEFAULT_TEXT_KEYS]
    envelope = _envelope(row)
    if envelope.get("frozen_fact"):
        parts.append(envelope.get("frozen_fact"))
    return " ".join(str(part) for part in parts if isinstance(part, str) and part.strip())


# ---------------------------------------------------------------------------------------------
# 1. envelope normalization
# ---------------------------------------------------------------------------------------------


def normalize_content_envelope(
    *,
    author_side: str,
    visibility: str = "private",
    created_at: Optional[str] = None,
    consider_after: Optional[str] = None,
    expires_at: Optional[str] = None,
    sealed: bool = False,
    withdrawn: bool = False,
    source_refs: Sequence[str] = (),
    frozen_fact: Optional[str] = None,
) -> dict[str, Any]:
    """Return a compact, canonical content envelope.

    * ``author_side`` is exactly ``subject`` or ``partner``;
    * ``visibility`` is one of the four scoped classes;
    * timestamps are copied verbatim -- never parsed, rounded, rewritten, or renewed;
    * ``source_refs`` is sorted, de-duplicated, and stripped of empty entries.
    """

    author = _validate_choice(author_side, AUTHOR_SIDES, "INVALID_AUTHOR_SIDE")
    scope = _validate_choice(visibility, VISIBILITIES, "INVALID_VISIBILITY")

    refs = tuple(
        sorted({
            str(ref).strip()
            for ref in source_refs
            if isinstance(ref, str) and str(ref).strip()
        })
    )

    return {
        "author_side": author,
        "visibility": scope,
        "created_at": created_at,
        "consider_after": consider_after,
        "expires_at": expires_at,
        "sealed": bool(sealed),
        "withdrawn": bool(withdrawn),
        "source_refs": refs,
        "frozen_fact": frozen_fact,
    }


# ---------------------------------------------------------------------------------------------
# 2. frozen original-fact immutability
# ---------------------------------------------------------------------------------------------


def enforce_frozen_fact_immutability(
    existing_row: Mapping[str, Any],
    candidate_row: Mapping[str, Any],
    *,
    fact_key: str = "original_fact_cn",
) -> None:
    """Refuse a re-submission that alters an already-recorded original fact.

    Only whitespace-trimmed equality counts as "the same fact". A later
    reinterpretation may add revisions, but it may never rewrite the frozen
    original. Empty / missing candidate values are allowed (append-only callers
    routinely omit the frozen field on updates).
    """

    existing = existing_row.get(fact_key)
    candidate = candidate_row.get(fact_key)
    if not isinstance(existing, str) or not existing.strip():
        return
    if not isinstance(candidate, str) or not candidate.strip():
        return
    if existing.strip() != candidate.strip():
        raise ValueError(FROZEN_FACT_IMMUTABLE)


# ---------------------------------------------------------------------------------------------
# 3. three-gate read & attention check
# ---------------------------------------------------------------------------------------------


def check_read_and_attention_gate(
    row: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
    author_field: str = "author_side",
    visibility_field: str = "visibility",
    status_field: str = "status",
) -> dict[str, Any]:
    """Evaluate one row against the three gates. Pure: never mutates ``row``.

    Gate order: withdrawn -> sealed / ``RECEIVED_UNOPENED`` -> visibility &
    ownership -> lifecycle. Lifecycle uses ``now_iso`` strictly; the row's
    ``expires_at`` / ``consider_after`` are read, never reset or renewed.
    """

    envelope = _envelope(row)
    status = _effective_status(row, status_field=status_field)
    author = _effective_author(row, author_field=author_field)
    visibility = _effective_visibility(row, visibility_field=visibility_field)

    withdrawn = bool(row.get("withdrawn") is True or envelope.get("withdrawn") is True
                     or status == STATUS_WITHDRAWN)
    sealed = bool(row.get("sealed") is True or envelope.get("sealed") is True
                  or status == STATUS_RECEIVED_UNOPENED)

    result: dict[str, Any] = {
        "allowed_read": False,
        "allowed_body": False,
        "allowed_attention": False,
        "dormant": False,
        "expired": False,
        "withdrawn": withdrawn,
        "sealed": sealed,
        "expose_raw_sources": False,
        "overdue": False,
        "debt_score": 0,
        "reason": "DENIED",
    }

    if withdrawn:
        result["reason"] = STATUS_WITHDRAWN
        return result

    if sealed:
        # Existence metadata is visible to the two parties; the body is not.
        result["allowed_read"] = True
        result["reason"] = "SEALED_UNOPENED"
        return result

    is_author = bool(author) and author == actor_side

    if visibility == "private":
        if is_author:
            result.update(
                allowed_read=True,
                allowed_body=True,
                allowed_attention=True,
                expose_raw_sources=True,
                reason="PRIVATE_AUTHOR",
            )
        else:
            result["reason"] = "PRIVATE_OTHER_SIDE"
    elif visibility == "unconfirmed_idiom":
        result.update(
            allowed_read=True,
            allowed_body=True,
            allowed_attention=True,
            expose_raw_sources=is_author,
            reason="UNCONFIRMED_IDIOM",
        )
    elif visibility == "shared_excerpt":
        result.update(
            allowed_read=True,
            allowed_body=True,
            allowed_attention=True,
            expose_raw_sources=is_author,
            reason="SHARED_EXCERPT",
        )
    else:  # "shared"
        result.update(
            allowed_read=True,
            allowed_body=True,
            allowed_attention=True,
            expose_raw_sources=is_author,
            reason="SHARED",
        )

    attention_base = result["allowed_attention"]

    now_dt = _parse_iso_utc(now_iso) if now_iso is not None else None
    if now_iso is None:
        now_dt = datetime.now(timezone.utc)

    if row.get("active_now") is False:
        result["expired"] = True
        result["allowed_attention"] = False
        if attention_base:
            result["reason"] = "INACTIVE"

    consider_raw = _first_str(
        row.get("consider_after"),
        row.get("surface_after"),
        envelope.get("consider_after"),
    )
    consider_dt = _parse_iso_utc(consider_raw)
    if consider_dt is not None and now_dt is not None and now_dt < consider_dt:
        result["dormant"] = True
        result["allowed_attention"] = False
        result["overdue"] = False
        result["debt_score"] = 0
        if attention_base:
            result["reason"] = "DORMANT"

    expires_raw = _first_str(row.get("expires_at"), envelope.get("expires_at"))
    expires_dt = _parse_iso_utc(expires_raw)
    if expires_dt is not None and now_dt is not None and now_dt >= expires_dt:
        result["expired"] = True
        result["allowed_attention"] = False
        if attention_base and not result["dormant"]:
            result["reason"] = "EXPIRED"

    return result


# ---------------------------------------------------------------------------------------------
# 4. concern-filtered ranking
# ---------------------------------------------------------------------------------------------


def rank_authorized_by_concern(
    rows: Sequence[Mapping[str, Any]],
    *,
    concern_text: str,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
    text_fn: Optional[Callable[[Mapping[str, Any]], str]] = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Filter by authorization first, then rank matching rows by overlap.

    Relevance never grants read or attention. Withdrawn, sealed /
    ``RECEIVED_UNOPENED`` and private records are dropped *before* any text is
    read, so an exact keyword hit can never promote them. Ties keep input order.
    """

    if limit <= 0 or not isinstance(concern_text, str) or not concern_text.strip():
        return []

    concern_norm = _normalize(concern_text)
    if not concern_norm:
        return []
    concern_bigrams = _bigrams(concern_norm)

    extract = text_fn or _default_text
    scored: list[tuple[int, int, dict[str, Any]]] = []

    for order, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        gate = check_read_and_attention_gate(
            row, actor_side=actor_side, now_iso=now_iso
        )
        if not gate["allowed_attention"]:
            # Withdrawn / sealed / dormant / expired never enter the ranking.
            continue
        if _effective_visibility(row, visibility_field="visibility") == "private":
            # Private records belong to private recall; concern never surfaces them,
            # not even to their own author.
            continue

        text = extract(row)
        text_norm = _normalize(text)
        if not text_norm:
            continue
        score = len(concern_bigrams & _bigrams(text_norm))
        if concern_norm in text_norm:
            score += len(concern_norm)
        if score <= 0:
            continue

        hit = copy.deepcopy(dict(row))
        hit["_concern_score"] = score
        scored.append((score, order, hit))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [hit for _score, _order, hit in scored[:limit]]

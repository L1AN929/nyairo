#!/usr/bin/env python3
"""v0.4 Ch.48/50 + v3.0 Ch.24：涌现回响的五个零大模型小原语。

五个板块都是「长期过日子自然长出来」的小状态，没有每日提醒债、没有情景脚本、
没有亲密度/服从度之类的分数：

1. ``future_self_questions``：给未来自己的问题。只有「种下 / 回看」两个动作，
   绝不带 ``due_date``、``overdue_count`` 或每日提醒计数；回看可以仍然好奇、
   已经回答、或让它自然淡掉。
2. ``shared_idioms``：只有彼此懂的说法。双方都能提出、确认、淡忘；
   匹配时附一段「不强迫照演」的免责提示。
3. ``memory_reinterpretations``：同一件往事后来理解得不同。第一次记录即永久冻结
   ``original_fact_cn``，之后的修订只追加 ``revisions``，绝不覆写原始事实。
4. ``style_and_interest_sparks``：兴趣迁移火花。``familiarity``（会做）、
   ``craft_fit``（做得好）、``mood_pull_today``（今天想做）严格分立，没有标量技能等级；
   ``method_validated`` 只有 ``outcome == "helped"`` 才置真。
5. ``divergent_notices``：两个人带回不一样的注意。按 ``target_ref`` 并列保存
   双方各自的注意，绝不计算一致度。
6. ``day_directions``：一天可以有方向，但方向不该提前发生（v0.4 第 49 章）。
   起草方向只写「想往哪儿走、到哪儿再决定」，绝不写记忆、绝不声称已经执行、
   绝不触发失败判定；只有真实的推进/打断动作才允许把 ``executed_in_world`` 置真，
   且「带证据完成」必须给出真实的 ``evidence_ref``。
7. ``personal_rituals``：个人生活习惯的演变与零债淡出（Ch.16 §三 #18 / §四 #7）。
   从真实顺手的临时做法起步，可自愿改名、变体、跨场景复用，也可安静淡出；
   兴趣淡了但分类/修法仍承重时关联真实 ``craft_genealogy`` 记为
   ``technique_retained``。连续次数（``streak_days`` / ``habit_score`` / ``guilt_debt``
   等）一律拒收，漏做/跳过/淡出都是零欠债；周年只提供一个「要不要留个做法」的
   安静考虑机会，绝不因重复次数自动宣布成仪式。

硬约束：
* 全程零外部大模型调用（``llm_calls == 0``）；
* ``read_emergent_echoes_zero_write`` / ``project_emergent_echoes_zero_write`` /
  ``match_situational_echoes_zero_write`` 100% 零磁盘写入：不建目录、不建临时文件、
  不触碰文件 mtime；状态文件缺失时返回空默认集合；
* 开源角色中立：人称代词从 ``character_pack`` 动态读取，缺省 ``"TA"``，
  模块内不出现任一个体姓名或固定性别代词。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:  # package import (``components.life.scripts``)
    from . import scoped_content_record as _scr
    from . import state_tx_guard as _tx
    from . import v04_elastic_play_learning as _elastic
except ImportError:  # pragma: no cover - flat ``components/life/scripts`` import path
    import scoped_content_record as _scr  # type: ignore[no-redef]
    import state_tx_guard as _tx  # type: ignore[no-redef]
    import v04_elastic_play_learning as _elastic  # type: ignore[no-redef]

VIEW_SCHEMA = "nyairo.v08.emergent_echoes.v1"
STATE_SCHEMA_VERSION = "phone.v08.emergent_echoes.v1"
STATE_REL_PATH = ("phone", "v08_emergent_echoes.json")

ANCHOR_KINDS: frozenset[str] = frozenset({"work", "object", "place", "topic"})
VISIBILITIES: frozenset[str] = frozenset({"private", "shared"})
QUESTION_STATUSES: frozenset[str] = frozenset({"open", "answered", "faded"})
RESOLUTIONS: frozenset[str] = frozenset({"still_curious", "answered_now", "let_fade"})
IDIOM_STATUSES: frozenset[str] = frozenset({"proposed", "confirmed", "faded"})
SPARK_FAMILIARITY: frozenset[str] = frozenset({"new", "tried_once", "familiar"})
SPARK_CRAFT_FIT: frozenset[str] = frozenset({"awkward", "workable", "steady"})
SPARK_MOOD_PULL: frozenset[str] = frozenset({"low", "curious", "eager"})
SPARK_OUTCOMES: frozenset[str] = frozenset({"helped", "did_not_fit", "skipped_today"})
#: Ch.23: a later reading may stay an open question, be revised by a real
#: observation, or be confirmed.  None of these is a silent whole-sale flip.
HYPOTHESIS_STATUSES: frozenset[str] = frozenset({
    "open_question",
    "revised_by_observation",
    "confirmed",
})
DAY_DIRECTION_STATUSES: frozenset[str] = frozenset({
    "direction_only",
    "in_progress",
    "paused_at_breakpoint",
    "completed",
    "faded",
})
DIRECTION_TRANSITIONS: frozenset[str] = frozenset({
    "mark_started",
    "pause_at_breakpoint",
    "mark_completed_with_ref",
    "let_fade",
})
LINGERING_SCOPES: frozenset[str] = frozenset({"work", "conversation", "object"})
LINGERING_RESOLUTIONS: frozenset[str] = frozenset({
    "talked_through",
    "activity_finished",
    "basis_changed",
})

#: Ch.48 §五：过期只是停止自动注入，不代表真实经历被遗忘。
LINGERING_EXPIRY_NOTE = "过期后只是停止自动注入，不证明真实经历已被遗忘"

#: Ch.48 §六：来讯明确提到急事时，平日的「先收尾或稍后再回」习惯本轮主动让位。
PREFERENCE_EXCEPTION_NOTE = (
    "对方这回明确提到有急事：平日『先收尾或稍后再回』的习惯本轮主动让位，优先先回应眼下急事。"
)

#: 来讯中的急事标记；命中任一条才允许让位，不命中就按平日习惯自然执行。
URGENCY_MARKERS: tuple[str, ...] = (
    "急事",
    "赶紧",
    "马上",
    "来不及",
    "出事了",
    "现在就要",
    "十万火急",
)

#: 命中「只有彼此懂的说法」时的固定免责提示：接住心意，但不强迫照演。
OBLIGATION_FREE_NOTE = (
    "记得这个只有彼此懂的说法；若此刻正忙或没力气，温和接住心意并说明真实状态即可，不必勉强照演"
)

MAX_FUTURE_QUESTIONS = 64
MAX_SHARED_IDIOMS = 64
MAX_MEMORY_REINTERPRETATIONS = 64
MAX_STYLE_SPARKS = 96
MAX_DIVERGENT_NOTICES = 96
MAX_DAY_DIRECTIONS = 12
MAX_LINGERING_THREADS = 8
MAX_CRAFT_GENEALOGY = 48
MAX_PERSONAL_RITUALS = 48
MAX_OPERATION_LEDGER = 256

#: Ch.16 §三：自传性技法谱系的正向/负向结果。只有真实做出来才算数。
CRAFT_OUTCOMES: frozenset[str] = frozenset({"worked", "misfired"})

#: Ch.16 §三 #18 / §四 #7：个人生活习惯的四个主体动作。
#: 名字里刻意没有 "ritual_done" / "confirm_ritual" 之类会随手把临时做法升格成仪式的动作。
RITUAL_OPS: frozenset[str] = frozenset({
    "sketch_custom",
    "reuse_variant",
    "fade_or_omit",
    "consider_milestone",
})

RITUAL_STATUSES: frozenset[str] = frozenset({
    "provisional_custom",
    "reused_variant",
    "faded_quietly",
    "technique_retained",
})

#: ``fade_or_omit`` 的两个温和去向：安静淡出，或兴趣淡了但做法继续承重。
RITUAL_FADE_TARGETS: frozenset[str] = frozenset({"faded_quietly", "technique_retained"})

#: 任何一天漏做、跳过或淡出都零欠债；这些计数键一律拒收，绝不写进行。
#: （``streak_days`` 同时也在 ``FORBIDDEN_CLIENT_KEYS`` 里，双重拦截。）
RITUAL_IMMUTABLE_KEYS: frozenset[str] = frozenset({
    "auto_declared_by_count",
    "omission_debt",
    "streak_days",
    "habit_score",
    "guilt_debt",
    "occurrence_count",
    "consecutive_days",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "propose_shared_idiom",
    "confirm_shared_idiom",
    "fade_shared_idiom",
    "partner_add_divergent_notice",
})

SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "plant_future_question",
    "revisit_future_question",
    "record_memory_reinterpretation",
    "share_memory_reinterpretation",
    "record_cross_domain_spark",
    "settle_spark_attempt",
    "subject_add_divergent_notice",
    "sketch_day_direction",
    "advance_or_interrupt_direction",
    "note_lingering_thread",
    "resolve_lingering_thread",
    "record_craft_genealogy_step",
    "toggle_share_craft_tip",
    "curate_personal_ritual",
})

KNOWN_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | SUBJECT_ONLY_ACTIONS

#: 客户端绝不允许自带的身份键与关系/游戏化分数键。
FORBIDDEN_CLIENT_KEYS: frozenset[str] = frozenset({
    "uid",
    "user_id",
    "owner_uid",
    "local_owner_uid",
    "actor_side",
    "has_subject_capability",
    "subject_view",
    "capability",
    "intimacy_score",
    "affection_score",
    "favorability",
    "relationship_xp",
    "skill_level",
    "streak_days",
    "obedience_level",
})


class V08EchoesError(Exception):
    """结构化业务异常：HTTP 视图按 ``status_code`` / ``reason_code`` 映射。"""

    def __init__(self, status_code: int, reason_code: str, message: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        super().__init__(f"{self.reason_code}: {self.message}")


class V08ValidationError(V08EchoesError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(400, reason_code, message)


class V08PermissionError(V08EchoesError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(403, reason_code, message)


class V08NotFoundError(V08EchoesError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(404, reason_code, message)


class V08ConflictError(V08EchoesError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(409, reason_code, message)


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------


def _parse_iso_utc(raw: Any) -> Optional[datetime]:
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


def _now_iso_str(now_iso: Optional[str] = None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        if _parse_iso_utc(now_iso) is None:
            raise V08ValidationError("INVALID_FIELDS", f"Malformed ISO timestamp: {now_iso!r}")
        return now_iso.strip()
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _require_str(
    value: Any,
    field_name: str,
    *,
    min_len: int = 1,
    max_len: int = 240,
) -> str:
    if not isinstance(value, str):
        raise V08ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a string")
    if "\x00" in value:
        raise V08ValidationError("INVALID_FIELDS", f"Field {field_name!r} contains NUL byte")
    trimmed = value.strip()
    if len(trimmed) < max(1, int(min_len)):
        raise V08ValidationError(
            "INVALID_FIELDS", f"Field {field_name!r} must be at least {max(1, int(min_len))} chars"
        )
    if len(trimmed) > max_len:
        raise V08ValidationError(
            "INVALID_FIELDS", f"Field {field_name!r} exceeds max length {max_len}"
        )
    return trimmed


def _optional_str(value: Any, field_name: str, *, max_len: int = 320) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise V08ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a string")
    if "\x00" in value:
        raise V08ValidationError("INVALID_FIELDS", f"Field {field_name!r} contains NUL byte")
    trimmed = value.strip()
    if len(trimmed) > max_len:
        raise V08ValidationError(
            "INVALID_FIELDS", f"Field {field_name!r} exceeds max length {max_len}"
        )
    return trimmed


def _optional_bool(value: Any, field_name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise V08ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a boolean")
    return value


def _require_enum(value: Any, field_name: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value.strip() not in allowed:
        raise V08ValidationError(
            "INVALID_FIELDS",
            f"Field {field_name!r} must be one of {sorted(allowed)}",
        )
    return value.strip()


def _optional_enum(value: Any, field_name: str, allowed: frozenset[str], *, default: str) -> str:
    if value is None:
        return default
    return _require_enum(value, field_name, allowed)


_PAGE_REF_RE = re.compile(r"^(?:p(?:age)?\.?\s*)?(\d{1,5})$", re.IGNORECASE)


def _optional_exact_page_ref(value: Any) -> tuple[bool, str]:
    """Return ``(known, ref)`` for a caller-supplied *real* page reference.

    Nothing is ever derived from a title or an excerpt: an absent or blank value
    is honestly ``(False, "")``.  A non-empty but malformed value raises instead
    of guessing a page number.
    """

    if value is None:
        return False, ""
    if isinstance(value, bool):
        raise V08ValidationError(
            "INVALID_FIELDS", "Field 'exact_page_ref' must be a page reference"
        )
    if isinstance(value, int):
        if value <= 0:
            raise V08ValidationError(
                "INVALID_FIELDS", "Field 'exact_page_ref' must be a positive page"
            )
        return True, str(value)
    if not isinstance(value, str):
        raise V08ValidationError(
            "INVALID_FIELDS", "Field 'exact_page_ref' must be a string or integer"
        )
    trimmed = value.strip()
    if not trimmed:
        return False, ""
    if "\x00" in trimmed:
        raise V08ValidationError("INVALID_FIELDS", "Field 'exact_page_ref' contains NUL byte")
    match = _PAGE_REF_RE.match(trimmed)
    if match is None or int(match.group(1)) <= 0:
        raise V08ValidationError(
            "INVALID_FIELDS",
            "Field 'exact_page_ref' must be a real page like 'p.12' or '12'",
        )
    return True, trimmed


def _require_keywords(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not (1 <= len(value) <= 6):
        raise V08ValidationError("INVALID_FIELDS", "keywords must contain 1..6 strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise V08ValidationError("INVALID_FIELDS", "keywords must contain only strings")
        trimmed = item.strip()
        if not trimmed:
            raise V08ValidationError("INVALID_FIELDS", "keywords must not contain blank strings")
        if len(trimmed) > 24:
            raise V08ValidationError("INVALID_FIELDS", "each keyword must be <= 24 chars")
        if trimmed not in out:
            out.append(trimmed)
    if not out:
        raise V08ValidationError("INVALID_FIELDS", "keywords must contain at least one string")
    return out


# ---------------------------------------------------------------------------------------------
# character identity (zero-write, open-source neutral)
# ---------------------------------------------------------------------------------------------


def read_character_identity_zero_write(day_root: Path | str) -> dict[str, str]:
    """Read display name / pronouns from ``character_pack`` without touching disk.

    Falls back to ``"TA"`` for both when no readable pack exists.  This module
    never hard-codes an individual name or a gendered pronoun.
    """

    root = Path(day_root)
    display_name = "TA"
    pronouns = "TA"
    for rel in (
        "phone/character_pack_state.json",
        "phone/character_pack.json",
        "character_pack.json",
    ):
        candidate = root.joinpath(*rel.split("/"))
        try:
            if not candidate.exists() or candidate.is_symlink() or not candidate.is_file():
                continue
            doc = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(doc, Mapping):
            continue
        pack = (
            doc.get("pack")
            if isinstance(doc.get("pack"), Mapping)
            else (doc.get("character") if isinstance(doc.get("character"), Mapping) else doc)
        )
        name = str(pack.get("display_name") or pack.get("name") or "").strip()
        pronoun = str(pack.get("pronouns") or pack.get("pronoun") or "").strip()
        if name:
            display_name = name[:32]
        if pronoun:
            pronouns = pronoun[:16]
        if name or pronoun:
            break
    return {"display_name": display_name, "pronouns": pronouns}


# ---------------------------------------------------------------------------------------------
# state file (pure read; atomic replace on write)
# ---------------------------------------------------------------------------------------------


def _state_file_path(root: Path | str) -> Path:
    return Path(root).joinpath(*STATE_REL_PATH)


def _empty_raw_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "next_seq": 1,
        "future_self_questions": [],
        "shared_idioms": [],
        "memory_reinterpretations": [],
        "style_and_interest_sparks": [],
        "divergent_notices": [],
        "day_directions": [],
        "lingering_threads": [],
        "craft_genealogy": [],
        "personal_rituals": [],
        "operation_ledger": {},
    }


def _load_state_readonly(root: Path | str) -> tuple[bool, dict[str, Any]]:
    """100% zero-write read of the persisted state (no mkdir, temp file or mtime touch)."""

    path = _state_file_path(root)
    if not path.exists():
        return False, _empty_raw_state()
    if not path.is_file() or path.is_symlink():
        raise V08EchoesError(503, "CORRUPT_STATE_FILE", "State path is not a regular file")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise V08EchoesError(503, "CORRUPT_STATE_FILE", f"Corrupted echoes state JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema_version") != STATE_SCHEMA_VERSION:
        raise V08EchoesError(503, "CORRUPT_STATE_FILE", "Invalid schema_version in echoes state file")
    merged = _empty_raw_state()
    merged.update(copy.deepcopy(doc))
    return True, merged


def _atomic_write_state(root: Path | str, state: Mapping[str, Any]) -> None:
    target = _state_file_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(dict(state), ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".v08_emergent_echoes_", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _hash_prefix(*parts: Any) -> str:
    blob = json.dumps([str(part) for part in parts], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _allocate_id(state: dict[str, Any], prefix: str, *parts: Any) -> str:
    seq = int(state.get("next_seq") or 1)
    state["next_seq"] = seq + 1
    return f"{prefix}_{_hash_prefix(seq, *parts)}"


def _compute_payload_fingerprint(actor_side: str, payload: Mapping[str, Any]) -> str:
    material = {
        key: payload[key]
        for key in sorted(payload)
        if key not in ("operation_key", "intent_fingerprint", "expected_revision")
    }
    blob = json.dumps(
        {"actor_side": actor_side, "payload": material},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _trim_list(state: dict[str, Any], key: str, limit: int) -> None:
    rows = state.get(key)
    if isinstance(rows, list):
        while len(rows) > limit:
            rows.pop(0)


# ---------------------------------------------------------------------------------------------
# shared scoped-content substrate (visibility / freeze / lifecycle gates)
# ---------------------------------------------------------------------------------------------


def _attach_content_envelope(
    row: dict[str, Any],
    *,
    author_side: str,
    visibility: str,
    created_at: Optional[str],
    consider_after: Optional[str] = None,
    expires_at: Optional[str] = None,
    sealed: bool = False,
    withdrawn: bool = False,
    source_refs: Sequence[str] = (),
    frozen_fact: Optional[str] = None,
) -> dict[str, Any]:
    """Attach a canonical content envelope while leaving flat legacy fields intact."""

    row["content_envelope"] = _scr.normalize_content_envelope(
        author_side=author_side,
        visibility=visibility,
        created_at=created_at,
        consider_after=consider_after,
        expires_at=expires_at,
        sealed=sealed,
        withdrawn=withdrawn,
        source_refs=source_refs,
        frozen_fact=frozen_fact,
    )
    return row


def _refresh_envelope(
    row: dict[str, Any],
    *,
    author_side: str,
    visibility: str,
    created_at: Optional[str] = None,
    source_refs: Sequence[str] = (),
    frozen_fact: Optional[str] = None,
) -> None:
    """Keep an existing envelope canonical after a lifecycle change (never drops it)."""

    previous = row.get("content_envelope")
    previous = previous if isinstance(previous, Mapping) else {}
    _attach_content_envelope(
        row,
        author_side=author_side,
        visibility=visibility,
        created_at=created_at or previous.get("created_at") or row.get("created_at"),
        consider_after=previous.get("consider_after"),
        expires_at=previous.get("expires_at"),
        sealed=bool(previous.get("sealed")),
        withdrawn=bool(previous.get("withdrawn")),
        source_refs=source_refs,
        frozen_fact=frozen_fact or previous.get("frozen_fact"),
    )


def _gate_row(row: Mapping[str, Any], author_side: str) -> dict[str, Any]:
    """Copy a row with an inferred author so legacy rows without one still gate correctly."""

    prepared = dict(row)
    if not prepared.get("author_side"):
        prepared["author_side"] = author_side
    return prepared


def _content_gate(
    row: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: Optional[str],
    author_side: str = "subject",
) -> dict[str, Any]:
    return _scr.check_read_and_attention_gate(
        _gate_row(row, author_side),
        actor_side=actor_side,
        now_iso=now_iso,
        author_field="author_side",
        visibility_field="visibility",
        status_field="status",
    )


def _partner_visible_rows(
    state: Mapping[str, Any],
    key: str,
    *,
    now_iso: Optional[str],
    author_side: str = "subject",
) -> list[dict[str, Any]]:
    """Project the partner view through the shared three-gate check."""

    visible: list[dict[str, Any]] = []
    for row in _rows(state, key):
        gate = _content_gate(row, actor_side="partner", now_iso=now_iso, author_side=author_side)
        if gate["allowed_read"]:
            visible.append(row)
    return visible


def _idiom_gate(row: Mapping[str, Any], *, actor_side: str, now_iso: Optional[str]) -> dict[str, Any]:
    """Idioms are visible to both sides: proposals are ``unconfirmed_idiom``, confirmed are shared."""

    prepared = _gate_row(row, str(row.get("proposed_by") or "subject"))
    if not isinstance(prepared.get("visibility"), str):
        prepared["visibility"] = (
            "unconfirmed_idiom" if str(row.get("status") or "") == "proposed" else "shared"
        )
    return _scr.check_read_and_attention_gate(
        prepared,
        actor_side=actor_side,
        now_iso=now_iso,
        author_field="author_side",
        visibility_field="visibility",
        status_field="status",
    )


def _frozen_original_fact_conflict(
    existing_row: Mapping[str, Any], candidate_row: Mapping[str, Any]
) -> bool:
    """Run the shared frozen-fact check; report whether the candidate tried to rewrite it.

    The persisted contract of this module has always been "first fact wins": a later
    reinterpretation that submits a different wording is accepted but the locked
    ``original_fact_cn`` is never overwritten.  We therefore translate the shared
    ``ValueError(FROZEN_FACT_IMMUTABLE)`` into a boolean and keep the legacy behavior
    instead of rejecting the whole append-only revision.
    """

    try:
        _scr.enforce_frozen_fact_immutability(existing_row, candidate_row)
    except ValueError as exc:
        if str(exc) != _scr.FROZEN_FACT_IMMUTABLE:
            raise
        return True
    return False



# ---------------------------------------------------------------------------------------------
# projection (pure zero-write)
# ---------------------------------------------------------------------------------------------


def _rows(state: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    return [copy.deepcopy(dict(row)) for row in (state.get(key) or []) if isinstance(row, Mapping)]


def _subject_rows(state: Mapping[str, Any], key: str, gate: str, gate_true: Any = True) -> list[dict[str, Any]]:
    def visible(row: Mapping[str, Any]) -> bool:
        value = row.get(gate)
        if gate_true is True:
            return value is True
        return value == gate_true

    return [
        copy.deepcopy(dict(row))
        for row in (state.get(key) or [])
        if isinstance(row, Mapping) and visible(row)
    ]


def _lingering_row(
    row: Mapping[str, Any], now_dt: datetime, *, gate: Mapping[str, Any]
) -> dict[str, Any]:
    """Copy one lingering thread and stamp ``active_now`` at projection time.

    ``active_now`` is derived from the shared gate (which reads ``expires_at``
    strictly) and is never persisted; reading never renews the TTL.
    """

    hit = copy.deepcopy(dict(row))
    expires_dt = _parse_iso_utc(hit.get("expires_at"))
    hit["active_now"] = bool(
        str(hit.get("status") or "") == "active"
        and expires_dt is not None
        and not gate.get("expired")
    )
    if not hit["active_now"]:
        hit["expiry_note_cn"] = LINGERING_EXPIRY_NOTE
    return hit


def _project_state(
    root: Path | str,
    *,
    state: Mapping[str, Any],
    exists: bool,
    perspective: str,
    observed_at: str,
) -> dict[str, Any]:
    subject = perspective == "subject"
    identity = read_character_identity_zero_write(root)
    now_dt = _parse_iso_utc(observed_at) or datetime.now(timezone.utc)

    # The partner view is filtered by the shared three-gate check; the subject
    # owns every private row, so it sees the full collection.
    questions = _rows(state, "future_self_questions") if subject else _partner_visible_rows(
        state, "future_self_questions", now_iso=observed_at
    )
    idioms = _rows(state, "shared_idioms")
    reinterpretations = _rows(state, "memory_reinterpretations") if subject else _partner_visible_rows(
        state, "memory_reinterpretations", now_iso=observed_at
    )
    sparks = _rows(state, "style_and_interest_sparks") if subject else _partner_visible_rows(
        state, "style_and_interest_sparks", now_iso=observed_at
    )
    notices = _rows(state, "divergent_notices")
    directions = _rows(state, "day_directions") if subject else _partner_visible_rows(
        state, "day_directions", now_iso=observed_at
    )
    # Legacy completed rows written before true-receipt verification stay readable
    # and are honestly marked unverified -- never deleted, never auto-promoted.
    for row in directions:
        if "verified" not in row:
            row["verified"] = False
            row["verification_status"] = "legacy_unverified"
        else:
            row.setdefault(
                "verification_status", "verified" if row.get("verified") else "legacy_unverified"
            )
    lingering: list[dict[str, Any]] = []
    actor_side = "subject" if subject else "partner"
    for row in _rows(state, "lingering_threads"):
        gate = _content_gate(row, actor_side=actor_side, now_iso=observed_at)
        if not gate["allowed_read"]:
            # Partner view strips any non-shared thread outright -- zero private leaks.
            continue
        lingering.append(_lingering_row(row, now_dt, gate=gate))

    # Ch.16 §三: the subject keeps the entire craft genealogy; the partner only
    # ever sees tips explicitly marked shareable, and the private manuscript /
    # draft reference is stripped completely (key absent, not blank).
    if subject:
        craft_genealogy = _rows(state, "craft_genealogy")
    else:
        craft_genealogy = []
        for row in _rows(state, "craft_genealogy"):
            if row.get("shared_tip_only") is not True:
                continue
            row.pop("source_work_ref", None)
            craft_genealogy.append(row)

    # Ch.16 §三 #18 / §四 #7: personal rituals are subject-owned.  The partner
    # projection only ever carries rows that clear the shared three-gate check;
    # a private practice is stripped outright, so not even its count leaks.
    personal_rituals = _rows(state, "personal_rituals") if subject else _partner_visible_rows(
        state, "personal_rituals", now_iso=observed_at
    )

    return {
        "schema": VIEW_SCHEMA,
        "state_exists": bool(exists),
        "perspective": perspective,
        "revision": int(state.get("revision") or 0),
        "observed_at": observed_at,
        "pronoun": identity["pronouns"],
        "display_name": identity["display_name"],
        "future_self_questions": questions,
        "shared_idioms": idioms,
        "memory_reinterpretations": reinterpretations,
        "style_and_interest_sparks": sparks,
        "divergent_notices": notices,
        "day_directions": directions,
        "lingering_threads": lingering,
        "craft_genealogy": craft_genealogy,
        "personal_rituals": personal_rituals,
        "llm_calls": 0,
    }


def read_emergent_echoes_zero_write(
    root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Zero-write projection.  The partner view strips private rows without counts."""

    exists, state = _load_state_readonly(root)
    perspective = "subject" if has_subject_capability else "partner"
    return _project_state(
        root,
        state=state,
        exists=exists,
        perspective=perspective,
        observed_at=_now_iso_str(now_iso),
    )


def project_emergent_echoes_zero_write(
    root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Alias of :func:`read_emergent_echoes_zero_write`, kept for callers by name."""

    return read_emergent_echoes_zero_write(
        root,
        has_subject_capability=has_subject_capability,
        now_iso=now_iso,
    )


# ---------------------------------------------------------------------------------------------
# situational matcher (pure zero-write, no LLM)
# ---------------------------------------------------------------------------------------------


def _norm_text(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


_TOKEN_RE = re.compile(r"[0-9A-Za-z_]+|[一-鿿]+")


def _text_tokens(text: Any) -> list[str]:
    out: list[str] = []
    for match in _TOKEN_RE.finditer(str(text or "")):
        token = match.group(0)
        if token.isascii():
            if len(token) >= 2:
                out.append(token.lower())
            continue
        # Chinese sentences have no spaces: emit the whole short token plus
        # sliding 4/3/2-char windows so phrase-level recall works without a
        # word-segmenter dependency.
        if len(token) <= 12:
            out.append(token)
        for size in (4, 3, 2):
            if len(token) < size:
                continue
            for start in range(0, len(token) - size + 1):
                out.append(token[start : start + size])
    return out


def _mentions(norm_text: str, candidate: Any) -> bool:
    needle = _norm_text(candidate)
    return bool(needle) and needle in norm_text


def _matches_tokens(norm_text: str, candidate: Any) -> bool:
    if _mentions(norm_text, candidate):
        return True
    for token in _text_tokens(candidate):
        if token and token in norm_text:
            return True
    return False


def _read_optional_json_zero_write(path: Path) -> Optional[dict[str, Any]]:
    """Pure read of a sibling state file; missing or broken reads as ``None``."""

    try:
        if not path.exists() or path.is_symlink() or not path.is_file():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return dict(doc) if isinstance(doc, Mapping) else None


def _active_scoped_preferences(root: Path | str) -> list[dict[str, Any]]:
    """Zero-write read of ``phone/v04_elastic_play.json`` scoped preferences."""

    doc = _read_optional_json_zero_write(Path(root).joinpath("phone", "v04_elastic_play.json"))
    if not doc:
        return []
    prefs = doc.get("scoped_preferences")
    if not isinstance(prefs, list):
        return []
    return [
        dict(row)
        for row in prefs
        if isinstance(row, Mapping) and str(row.get("status") or "") == "active"
    ]


def _has_active_day_direction(root: Path | str, state: Mapping[str, Any]) -> bool:
    """True when any non-faded direction exists in echoes state or elastic-play."""

    for row in state.get("day_directions") or []:
        if isinstance(row, Mapping) and str(row.get("status") or "") != "faded":
            return True
    doc = _read_optional_json_zero_write(Path(root).joinpath("phone", "v04_elastic_play.json"))
    if doc:
        direction = doc.get("day_direction")
        if isinstance(direction, Mapping):
            status = str(direction.get("status") or "").strip()
            if status not in ("", "idle", "faded", "completed"):
                return True
    return False


def _match_craft_genealogy_rows(
    state: Mapping[str, Any],
    *,
    domain_tag: str = "",
    current_situation_cn: str = "",
    include_private: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Zero-write corpus match for the autobiographical craft genealogy.

    A saved method is only ever offered when its domain or problem pattern is a
    hit *and* its ``mismatch_boundary_cn`` does not fire against the current
    situation.  A boundary hit is surfaced as a known mismatch so the running
    self does not mechanically replay an old habit where it fails.
    """

    text_norm = _norm_text(current_situation_cn)
    domain_norm = _norm_text(domain_tag)
    applicable: list[dict[str, Any]] = []
    known_mismatches: list[dict[str, Any]] = []

    for row in _rows(state, "craft_genealogy"):
        if not include_private and row.get("shared_tip_only") is not True:
            continue
        domain_hit = bool(domain_norm) and _norm_text(row.get("domain_tag")) == domain_norm
        token_hit = bool(text_norm) and _matches_tokens(text_norm, row.get("problem_pattern_cn"))
        # A situation that only resembles the *boundary* is precisely the trap
        # this recall exists to catch, so a boundary hit also selects the row.
        boundary_hit = bool(text_norm) and _matches_tokens(
            text_norm, row.get("mismatch_boundary_cn")
        )
        if not (domain_hit or token_hit or boundary_hit):
            continue
        hit = copy.deepcopy(row)
        if not include_private:
            hit.pop("source_work_ref", None)
        if boundary_hit:
            hit["match_status"] = "known_mismatch"
            known_mismatches.append(hit)
        elif str(row.get("outcome") or "") == "worked":
            hit["match_status"] = "applicable"
            applicable.append(hit)

    return {"applicable_methods": applicable, "known_mismatches": known_mismatches}


def match_applicable_craft_genealogy_zero_write(
    root: Path | str,
    *,
    domain_tag: str = "",
    current_situation_cn: str = "",
    include_private: bool = False,
) -> dict[str, Any]:
    """Pure zero-write recall of applicable craft methods and mismatch warnings.

    With ``include_private=False`` only ``shared_tip_only`` rows are considered
    and ``source_work_ref`` is never exposed; the partner-facing result cannot
    carry a private manuscript / draft reference.
    """

    exists, state = _load_state_readonly(root)
    matched = _match_craft_genealogy_rows(
        state,
        domain_tag=domain_tag,
        current_situation_cn=current_situation_cn,
        include_private=bool(include_private),
    )
    return {
        "schema": VIEW_SCHEMA,
        "state_exists": bool(exists),
        "perspective": "subject" if include_private else "partner",
        "applicable_methods": matched["applicable_methods"],
        "known_mismatches": matched["known_mismatches"],
        "llm_calls": 0,
    }


def match_situational_echoes_zero_write(
    root: Path | str,
    *,
    incoming_text: str = "",
    active_refs: Sequence[str] = (),
    include_private: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Return bounded situational echoes without touching disk or calling a model."""

    exists, state = _load_state_readonly(root)
    perspective = "subject" if include_private else "partner"
    actor_side = "subject" if include_private else "partner"
    now_text = _now_iso_str(now_iso)
    now_dt = _parse_iso_utc(now_text) or datetime.now(timezone.utc)
    text_norm = _norm_text(incoming_text)
    refs_norm = {_norm_text(ref) for ref in (active_refs or ()) if _norm_text(ref)}

    matched_idioms: list[dict[str, Any]] = []
    for row in _rows(state, "shared_idioms"):
        if str(row.get("status") or "") not in ("confirmed", "proposed"):
            continue
        if not _idiom_gate(row, actor_side=actor_side, now_iso=now_text)["allowed_attention"]:
            continue
        if _mentions(text_norm, row.get("phrase")):
            hit = copy.deepcopy(row)
            hit["obligation_free_note_cn"] = OBLIGATION_FREE_NOTE
            matched_idioms.append(hit)

    matched_questions: list[dict[str, Any]] = []
    for row in _rows(state, "future_self_questions"):
        if str(row.get("status") or "") != "open":
            continue
        if not _content_gate(
            row, actor_side=actor_side, now_iso=now_text
        )["allowed_attention"]:
            # Private / dormant / expired questions never reach relevance matching.
            continue
        anchor_ref = _norm_text(row.get("anchor_ref"))
        keywords = row.get("keywords") if isinstance(row.get("keywords"), list) else []
        hit = anchor_ref in refs_norm or _mentions(text_norm, row.get("anchor_ref"))
        if not hit:
            hit = any(_mentions(text_norm, keyword) for keyword in keywords)
        if hit:
            matched_questions.append(copy.deepcopy(row))

    candidate_reinterpretations = [
        row
        for row in _rows(state, "memory_reinterpretations")
        if _content_gate(row, actor_side=actor_side, now_iso=now_text)["allowed_attention"]
    ]
    matched_reinterpretations: list[dict[str, Any]] = []
    for row in candidate_reinterpretations:
        episode_ref = _norm_text(row.get("episode_ref"))
        hit = episode_ref in refs_norm
        if not hit:
            hit = _matches_tokens(text_norm, row.get("episode_title_cn"))
        if not hit:
            hit = _matches_tokens(text_norm, row.get("original_fact_cn"))
        if not hit:
            hit = _matches_tokens(text_norm, row.get("current_view_cn"))
        if hit:
            matched_reinterpretations.append(copy.deepcopy(row))
    if not matched_reinterpretations and (text_norm or refs_norm) and candidate_reinterpretations:
        latest = max(
            candidate_reinterpretations,
            key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
        )
        matched_reinterpretations.append(copy.deepcopy(latest))

    matched_sparks: list[dict[str, Any]] = []
    for row in _rows(state, "style_and_interest_sparks"):
        if not _content_gate(row, actor_side=actor_side, now_iso=now_text)["allowed_attention"]:
            continue
        if not (row.get("method_validated") is True or str(row.get("status") or "") == "untried"):
            continue
        hit = _mentions(text_norm, row.get("target_domain")) or _mentions(
            text_norm, row.get("source_domain")
        )
        if hit:
            matched_sparks.append(copy.deepcopy(row))

    matched_notices: list[dict[str, Any]] = []
    for row in _rows(state, "divergent_notices"):
        target_ref = _norm_text(row.get("target_ref"))
        hit = target_ref in refs_norm or _mentions(text_norm, row.get("target_ref"))
        if not hit:
            hit = _matches_tokens(text_norm, row.get("target_title_cn"))
        if not hit:
            hit = _mentions(text_norm, row.get("subject_notice_cn")) or _mentions(
                text_norm, row.get("partner_notice_cn")
            )
        if hit:
            matched_notices.append(copy.deepcopy(row))

    # Day directions: only non-faded rows; private ones stay behind the subject gate.
    active_directions: list[dict[str, Any]] = []
    for row in _rows(state, "day_directions"):
        if str(row.get("status") or "") == "faded":
            continue
        if not _content_gate(row, actor_side=actor_side, now_iso=now_text)["allowed_attention"]:
            continue
        active_directions.append(copy.deepcopy(row))

    matched_directions: list[dict[str, Any]] = []
    for row in active_directions:
        bound_ref = _norm_text(row.get("bound_ref"))
        hit = bound_ref in refs_norm or _mentions(text_norm, row.get("bound_ref"))
        if not hit:
            hit = _matches_tokens(text_norm, row.get("horizon_note_cn"))
        if hit:
            matched_directions.append(copy.deepcopy(row))
    # Fill the bounded slot with the still-active directions, matches first.
    for row in active_directions:
        if len(matched_directions) >= 3:
            break
        if row not in matched_directions:
            matched_directions.append(copy.deepcopy(row))
    matched_directions = matched_directions[:3]

    # Lingering threads: only still-active rows are injected; reading never renews
    # the TTL and expiry never deletes the recorded history.
    matched_lingering: list[dict[str, Any]] = []
    for row in _rows(state, "lingering_threads"):
        gate = _content_gate(row, actor_side=actor_side, now_iso=now_text)
        if not gate["allowed_attention"]:
            # Private / withdrawn / expired threads never enter situational recall.
            continue
        stamped = _lingering_row(row, now_dt, gate=gate)
        if stamped.get("active_now") is True:
            matched_lingering.append(stamped)

    # Ch.48 §六: a scoped "finish first / reply later" habit only yields when the
    # incoming message explicitly says something urgent.  The saved preference is
    # never deleted by this exception.
    active_prefs = _active_scoped_preferences(root)
    has_active_direction = _has_active_day_direction(root, state)
    has_urgent_signal = any(marker in str(incoming_text or "") for marker in URGENCY_MARKERS)
    if has_urgent_signal and (active_prefs or has_active_direction or matched_lingering):
        preference_exception_cue = PREFERENCE_EXCEPTION_NOTE
    else:
        preference_exception_cue = ""

    craft_matched = _match_craft_genealogy_rows(
        state,
        current_situation_cn=incoming_text,
        include_private=include_private,
    )

    # Ch.16 §四 #7: a variant still being carried along, or a quiet fade whose
    # method was retained, is a compact read-only cue.  Bounded to two rows and
    # filtered through the same authorization gate -- private stays invisible.
    relevant_rituals: list[dict[str, Any]] = []
    for row in _rows(state, "personal_rituals"):
        if str(row.get("status") or "") not in ("reused_variant", "technique_retained"):
            continue
        if not _content_gate(row, actor_side=actor_side, now_iso=now_text)["allowed_attention"]:
            continue
        relevant_rituals.append(copy.deepcopy(row))
    relevant_rituals.sort(
        key=lambda hit: str(hit.get("updated_at") or hit.get("created_at") or ""),
        reverse=True,
    )
    relevant_rituals = relevant_rituals[:2]

    return {
        "schema": VIEW_SCHEMA,
        "state_exists": bool(exists),
        "perspective": perspective,
        "matched_idioms": matched_idioms,
        "matched_questions": matched_questions,
        "matched_reinterpretations": matched_reinterpretations,
        "matched_sparks": matched_sparks,
        "matched_notices": matched_notices,
        "matched_directions": matched_directions,
        "matched_lingering_threads": matched_lingering,
        "matched_craft_methods": craft_matched["applicable_methods"],
        "known_mismatch_warnings": craft_matched["known_mismatches"],
        "relevant_personal_rituals": relevant_rituals,
        "scoped_preference_active": bool(active_prefs),
        "preference_exception_cue": preference_exception_cue,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------------------------
# action handlers
# ---------------------------------------------------------------------------------------------


def _find_row(
    state: Mapping[str, Any],
    key: str,
    id_field: str,
    value: str,
    *,
    not_found_code: str,
) -> dict[str, Any]:
    for row in state.get(key) or []:
        if isinstance(row, dict) and str(row.get(id_field) or "") == value:
            return row
    if not_found_code == "QUESTION_NOT_FOUND":
        raise V08NotFoundError(not_found_code, f"Unknown question_id: {value!r}")
    if not_found_code == "IDIOM_NOT_FOUND":
        raise V08NotFoundError(not_found_code, f"Unknown idiom_id: {value!r}")
    if not_found_code == "REINTERPRETATION_NOT_FOUND":
        raise V08NotFoundError(not_found_code, f"Unknown reinterpretation_id: {value!r}")
    if not_found_code == "SPARK_NOT_FOUND":
        raise V08NotFoundError(not_found_code, f"Unknown spark_id: {value!r}")
    if not_found_code == "DIRECTION_NOT_FOUND":
        raise V08NotFoundError(not_found_code, f"Unknown direction_id: {value!r}")
    if not_found_code == "LINGERING_THREAD_NOT_FOUND":
        raise V08NotFoundError(not_found_code, f"Unknown thread_id: {value!r}")
    if not_found_code == "CRAFT_GENEALOGY_NOT_FOUND":
        raise V08NotFoundError(not_found_code, f"Unknown genealogy_id: {value!r}")
    if not_found_code == "PERSONAL_RITUAL_NOT_FOUND":
        raise V08NotFoundError(not_found_code, f"Unknown ritual_id: {value!r}")
    raise V08NotFoundError(not_found_code, f"Unknown {id_field}: {value!r}")


def _handle_plant_future_question(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    question_cn = _require_str(payload.get("question_cn"), "question_cn", min_len=2, max_len=160)
    anchor_kind = _require_enum(payload.get("anchor_kind"), "anchor_kind", ANCHOR_KINDS)
    anchor_ref = _require_str(payload.get("anchor_ref"), "anchor_ref", min_len=1, max_len=96)
    keywords = _require_keywords(payload.get("keywords"))
    visibility = _optional_enum(payload.get("visibility"), "visibility", VISIBILITIES, default="private")

    row = {
        "question_id": _allocate_id(state, "fq", question_cn, anchor_kind, anchor_ref),
        "question_cn": question_cn,
        "anchor_kind": anchor_kind,
        "anchor_ref": anchor_ref,
        "keywords": keywords,
        "visibility": visibility,
        "status": "open",
        "created_at": obs_iso,
        "revisited_at": None,
        "resolution": None,
        "note_cn": "",
    }
    _attach_content_envelope(
        row,
        author_side=actor_side,
        visibility=visibility,
        created_at=obs_iso,
        source_refs=(anchor_ref,),
    )
    state.setdefault("future_self_questions", []).append(row)
    _trim_list(state, "future_self_questions", MAX_FUTURE_QUESTIONS)


def _handle_revisit_future_question(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    question_id = _require_str(payload.get("question_id"), "question_id", min_len=1, max_len=64)
    resolution = _require_enum(payload.get("resolution"), "resolution", RESOLUTIONS)
    note_cn = _optional_str(payload.get("note_cn"), "note_cn", max_len=200)

    row = _find_row(
        state, "future_self_questions", "question_id", question_id,
        not_found_code="QUESTION_NOT_FOUND",
    )
    row["resolution"] = resolution
    row["note_cn"] = note_cn
    row["revisited_at"] = obs_iso
    if resolution == "answered_now":
        row["status"] = "answered"
    elif resolution == "let_fade":
        row["status"] = "faded"
    else:
        row["status"] = "open"


def _handle_propose_shared_idiom(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    phrase = _require_str(payload.get("phrase"), "phrase", min_len=2, max_len=24)
    meaning_cn = _require_str(payload.get("meaning_cn"), "meaning_cn", min_len=2, max_len=120)
    origin_summary_cn = _require_str(
        payload.get("origin_summary_cn"), "origin_summary_cn", min_len=2, max_len=140
    )
    origin_ref = _optional_str(payload.get("origin_ref"), "origin_ref", max_len=96)

    row = {
        "idiom_id": _allocate_id(state, "idiom", phrase, meaning_cn),
        "phrase": phrase,
        "meaning_cn": meaning_cn,
        "origin_summary_cn": origin_summary_cn,
        "origin_ref": origin_ref or None,
        "proposed_by": actor_side,
        "status": "proposed",
        "confirmed_by": None,
        "confirmed_at": None,
        "faded_at": None,
        "created_at": obs_iso,
    }
    _attach_content_envelope(
        row,
        author_side=actor_side,
        visibility="unconfirmed_idiom",
        created_at=obs_iso,
        source_refs=(origin_ref,) if origin_ref else (),
    )
    state.setdefault("shared_idioms", []).append(row)
    _trim_list(state, "shared_idioms", MAX_SHARED_IDIOMS)


def _handle_confirm_shared_idiom(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    idiom_id = _require_str(payload.get("idiom_id"), "idiom_id", min_len=1, max_len=64)
    row = _find_row(state, "shared_idioms", "idiom_id", idiom_id, not_found_code="IDIOM_NOT_FOUND")
    # A shared idiom only becomes real when the *other* side confirms it; the
    # proposer cannot self-confirm its own phrasing into "ours".
    if str(row.get("proposed_by") or "") == actor_side:
        raise V08ConflictError(
            "SELF_CONFIRM_FORBIDDEN",
            "The side that proposed an idiom cannot confirm it; the other side must confirm",
        )
    row["status"] = "confirmed"
    row["confirmed_by"] = actor_side
    row["confirmed_at"] = obs_iso
    _refresh_envelope(
        row,
        author_side=str(row.get("proposed_by") or "subject"),
        visibility="shared",
        source_refs=(row.get("origin_ref"),) if row.get("origin_ref") else (),
    )


def _handle_fade_shared_idiom(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    idiom_id = _require_str(payload.get("idiom_id"), "idiom_id", min_len=1, max_len=64)
    row = _find_row(state, "shared_idioms", "idiom_id", idiom_id, not_found_code="IDIOM_NOT_FOUND")
    row["status"] = "faded"
    row["faded_at"] = obs_iso


def _handle_record_memory_reinterpretation(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    episode_ref = _require_str(payload.get("episode_ref"), "episode_ref", min_len=1, max_len=96)
    episode_title_cn = _require_str(
        payload.get("episode_title_cn"), "episode_title_cn", min_len=1, max_len=80
    )
    # Validated on every call, but only the first value is ever kept.
    original_fact_cn = _require_str(
        payload.get("original_fact_cn"), "original_fact_cn", min_len=2, max_len=180
    )
    prior_view_cn = _optional_str(payload.get("prior_view_cn"), "prior_view_cn", max_len=180)
    new_view_cn = _require_str(payload.get("new_view_cn"), "new_view_cn", min_len=2, max_len=180)
    trigger_summary_cn = _require_str(
        payload.get("trigger_summary_cn"), "trigger_summary_cn", min_len=2, max_len=140
    )
    shared_with_partner = _optional_bool(
        payload.get("shared_with_partner"), "shared_with_partner", default=False
    )
    # Ch.23: a later reading can be partial -- the old reservation stays beside
    # the freshly noticed facet, and an unconfirmed reading stays a hypothesis.
    preserved_reservation_cn = _optional_str(
        payload.get("preserved_reservation_cn"), "preserved_reservation_cn", max_len=160
    )
    appreciation_facet_cn = _optional_str(
        payload.get("appreciation_facet_cn"), "appreciation_facet_cn", max_len=160
    )
    raw_hypothesis = payload.get("hypothesis_status")
    hypothesis_status = (
        _require_enum(raw_hypothesis, "hypothesis_status", HYPOTHESIS_STATUSES)
        if raw_hypothesis is not None
        else ""
    )
    # Honesty rule: a page number is only recorded when the caller supplies a
    # real one; it is never inferred from the title or the quoted excerpt.
    exact_page_known, exact_page_ref = _optional_exact_page_ref(payload.get("exact_page_ref"))

    existing: Optional[dict[str, Any]] = None
    for row in state.get("memory_reinterpretations") or []:
        if isinstance(row, dict) and str(row.get("episode_ref") or "") == episode_ref:
            existing = row
            break

    if existing is not None:
        # Omitted optional fields keep the previously recorded value rather than
        # silently erasing a reservation / facet / hypothesis from an earlier pass.
        if not preserved_reservation_cn:
            preserved_reservation_cn = str(existing.get("preserved_reservation_cn") or "")
        if not appreciation_facet_cn:
            appreciation_facet_cn = str(existing.get("appreciation_facet_cn") or "")
        if not hypothesis_status:
            hypothesis_status = str(existing.get("hypothesis_status") or "")

    revision = {
        "prior_view_cn": prior_view_cn,
        "new_view_cn": new_view_cn,
        "trigger_summary_cn": trigger_summary_cn,
        "preserved_reservation_cn": preserved_reservation_cn,
        "appreciation_facet_cn": appreciation_facet_cn,
        "hypothesis_status": hypothesis_status,
        "exact_page_known": exact_page_known,
        "exact_page_ref": exact_page_ref,
        "recorded_at": obs_iso,
    }

    if existing is not None:
        # Immutable Original Fact: never overwrite the locked first fact.  A
        # re-submission that names a different fact is rejected by the shared
        # check; the legacy contract keeps the first value and still appends the
        # new revision rather than failing the whole call.
        _frozen_original_fact_conflict(
            existing, {"original_fact_cn": original_fact_cn}
        )
        revisions = existing.get("revisions")
        if not isinstance(revisions, list):
            revisions = []
        revisions.append(revision)
        existing["revisions"] = revisions
        existing["episode_title_cn"] = episode_title_cn
        existing["current_view_cn"] = new_view_cn
        existing["latest_trigger_summary_cn"] = trigger_summary_cn
        existing["preserved_reservation_cn"] = preserved_reservation_cn
        existing["appreciation_facet_cn"] = appreciation_facet_cn
        existing["hypothesis_status"] = hypothesis_status
        existing["exact_page_known"] = exact_page_known
        existing["exact_page_ref"] = exact_page_ref
        existing["updated_at"] = obs_iso
        if shared_with_partner:
            existing["shared_with_partner"] = True
        _refresh_envelope(
            existing,
            author_side="subject",
            visibility="shared" if existing.get("shared_with_partner") is True else "private",
            created_at=existing.get("created_at") or obs_iso,
            frozen_fact=str(existing.get("original_fact_cn") or ""),
        )
        return

    row = {
        "reinterpretation_id": _allocate_id(state, "mr", episode_ref),
        "episode_ref": episode_ref,
        "episode_title_cn": episode_title_cn,
        "original_fact_cn": original_fact_cn,
        "current_view_cn": new_view_cn,
        "latest_trigger_summary_cn": trigger_summary_cn,
        "preserved_reservation_cn": preserved_reservation_cn,
        "appreciation_facet_cn": appreciation_facet_cn,
        "hypothesis_status": hypothesis_status,
        "exact_page_known": exact_page_known,
        "exact_page_ref": exact_page_ref,
        "revisions": [revision],
        "shared_with_partner": bool(shared_with_partner),
        "created_at": obs_iso,
        "updated_at": obs_iso,
    }
    _attach_content_envelope(
        row,
        author_side=actor_side,
        visibility="shared" if shared_with_partner else "private",
        created_at=obs_iso,
        source_refs=(episode_ref,),
        frozen_fact=original_fact_cn,
    )
    state.setdefault("memory_reinterpretations", []).append(row)
    _trim_list(state, "memory_reinterpretations", MAX_MEMORY_REINTERPRETATIONS)


def _handle_share_memory_reinterpretation(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    reinterpretation_id = _require_str(
        payload.get("reinterpretation_id"), "reinterpretation_id", min_len=1, max_len=64
    )
    shared_with_partner = _optional_bool(
        payload.get("shared_with_partner"), "shared_with_partner", default=True
    )
    row = _find_row(
        state, "memory_reinterpretations", "reinterpretation_id", reinterpretation_id,
        not_found_code="REINTERPRETATION_NOT_FOUND",
    )
    row["shared_with_partner"] = bool(shared_with_partner)
    row["updated_at"] = obs_iso
    _refresh_envelope(
        row,
        author_side="subject",
        visibility="shared" if shared_with_partner else "private",
        created_at=row.get("created_at") or obs_iso,
        source_refs=(str(row.get("episode_ref") or ""),),
        frozen_fact=str(row.get("original_fact_cn") or ""),
    )


def _handle_record_cross_domain_spark(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    source_domain = _require_str(payload.get("source_domain"), "source_domain", min_len=1, max_len=40)
    source_observation_cn = _require_str(
        payload.get("source_observation_cn"), "source_observation_cn", min_len=2, max_len=140
    )
    target_domain = _require_str(payload.get("target_domain"), "target_domain", min_len=1, max_len=40)
    try_method_cn = _require_str(payload.get("try_method_cn"), "try_method_cn", min_len=2, max_len=140)
    familiarity = _require_enum(payload.get("familiarity"), "familiarity", SPARK_FAMILIARITY)
    craft_fit = _require_enum(payload.get("craft_fit"), "craft_fit", SPARK_CRAFT_FIT)
    mood_pull_today = _require_enum(payload.get("mood_pull_today"), "mood_pull_today", SPARK_MOOD_PULL)
    visibility = _optional_enum(payload.get("visibility"), "visibility", VISIBILITIES, default="private")

    row = {
        "spark_id": _allocate_id(state, "spark", source_domain, target_domain),
        "source_domain": source_domain,
        "source_observation_cn": source_observation_cn,
        "target_domain": target_domain,
        "try_method_cn": try_method_cn,
        "familiarity": familiarity,
        "craft_fit": craft_fit,
        "mood_pull_today": mood_pull_today,
        "visibility": visibility,
        "status": "untried",
        "method_validated": False,
        "latest_outcome": None,
        "choice_trace_cn": "",
        "created_at": obs_iso,
        "updated_at": obs_iso,
    }
    state.setdefault("style_and_interest_sparks", []).append(row)
    _trim_list(state, "style_and_interest_sparks", MAX_STYLE_SPARKS)


def _handle_settle_spark_attempt(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    spark_id = _require_str(payload.get("spark_id"), "spark_id", min_len=1, max_len=64)
    outcome = _require_enum(payload.get("outcome"), "outcome", SPARK_OUTCOMES)
    choice_trace_cn = _optional_str(payload.get("choice_trace_cn"), "choice_trace_cn", max_len=140)

    row = _find_row(
        state, "style_and_interest_sparks", "spark_id", spark_id, not_found_code="SPARK_NOT_FOUND"
    )
    row["latest_outcome"] = outcome
    row["choice_trace_cn"] = choice_trace_cn
    row["updated_at"] = obs_iso
    if outcome == "helped":
        # v3.0 Ch.24 §七: only a real "helped" validates the method.
        row["method_validated"] = True
        row["status"] = "helped"
    elif outcome == "did_not_fit":
        row["method_validated"] = False
        row["status"] = "not_fit"
    else:  # skipped_today -- rest without punishment; validation is unchanged
        row["status"] = "resting"


def _handle_add_divergent_notice(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    target_ref = _require_str(payload.get("target_ref"), "target_ref", min_len=1, max_len=96)
    target_title_cn = _require_str(
        payload.get("target_title_cn"), "target_title_cn", min_len=1, max_len=80
    )
    notice_cn = _require_str(payload.get("notice_cn"), "notice_cn", min_len=2, max_len=160)

    existing: Optional[dict[str, Any]] = None
    for row in state.get("divergent_notices") or []:
        if isinstance(row, dict) and str(row.get("target_ref") or "") == target_ref:
            existing = row
            break

    if existing is None:
        existing = {
            "notice_id": _allocate_id(state, "dn", target_ref),
            "target_ref": target_ref,
            "target_title_cn": target_title_cn,
            "subject_notice_cn": "",
            "partner_notice_cn": "",
            "subject_notice_at": None,
            "partner_notice_at": None,
            "created_at": obs_iso,
            "updated_at": obs_iso,
        }
        state.setdefault("divergent_notices", []).append(existing)
        _trim_list(state, "divergent_notices", MAX_DIVERGENT_NOTICES)

    # One side may never rewrite or erase the other side's notice.  Each actor's
    # latest note lives in its own slot; the canonical title is only filled once,
    # by whoever first created the row.
    entries = existing.get("entries_by_side")
    if not isinstance(entries, dict):
        entries = {}
        existing["entries_by_side"] = entries
    entries[actor_side] = {
        "target_title_cn": target_title_cn,
        "notice_cn": notice_cn,
        "noted_at": obs_iso,
    }
    if not existing.get("target_title_cn"):
        existing["target_title_cn"] = target_title_cn
    existing["updated_at"] = obs_iso
    if actor_side == "subject":
        existing["subject_notice_cn"] = notice_cn
        existing["subject_notice_at"] = obs_iso
    else:
        existing["partner_notice_cn"] = notice_cn
        existing["partner_notice_at"] = obs_iso


def _handle_sketch_day_direction(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    horizon_note_cn = _require_str(
        payload.get("horizon_note_cn"), "horizon_note_cn", min_len=2, max_len=120
    )
    next_decision_boundary_cn = _require_str(
        payload.get("next_decision_boundary_cn"), "next_decision_boundary_cn", min_len=2, max_len=100
    )
    bound_ref = _optional_str(payload.get("bound_ref"), "bound_ref", max_len=96)
    visibility = _optional_enum(payload.get("visibility"), "visibility", VISIBILITIES, default="private")

    # v0.4 Ch.49 §四: sketching a direction is a thought, not a deed.  It never
    # writes memory, never claims execution and never judges a failure.
    row = {
        "direction_id": _allocate_id(state, "dd", horizon_note_cn, next_decision_boundary_cn),
        "horizon_note_cn": horizon_note_cn,
        "next_decision_boundary_cn": next_decision_boundary_cn,
        "bound_ref": bound_ref or None,
        "visibility": visibility,
        "status": "direction_only",
        "executed_in_world": False,
        "evidence_ref": None,
        "breakpoint_note_cn": "",
        "failure_judgment": None,
        "zero_guilt": True,
        "created_at": obs_iso,
        "updated_at": obs_iso,
    }
    _attach_content_envelope(
        row,
        author_side=actor_side,
        visibility=visibility,
        created_at=obs_iso,
        source_refs=(bound_ref,) if bound_ref else (),
    )
    state.setdefault("day_directions", []).append(row)
    _trim_list(state, "day_directions", MAX_DAY_DIRECTIONS)


def _handle_advance_or_interrupt_direction(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    actor_side: str,
    obs_iso: str,
    *,
    root: Path | str,
) -> None:
    direction_id = _require_str(payload.get("direction_id"), "direction_id", min_len=1, max_len=64)
    transition = _require_enum(payload.get("transition"), "transition", DIRECTION_TRANSITIONS)
    breakpoint_note_cn = _optional_str(
        payload.get("breakpoint_note_cn"), "breakpoint_note_cn", max_len=120
    )
    evidence_ref = _optional_str(payload.get("evidence_ref"), "evidence_ref", max_len=96)

    row = _find_row(
        state, "day_directions", "direction_id", direction_id,
        not_found_code="DIRECTION_NOT_FOUND",
    )

    if transition == "mark_completed_with_ref":
        # Hard honesty rule: completion needs a real artifact/action reference that
        # exists in a persisted store and belongs to this side.
        if not evidence_ref:
            raise V08ValidationError(
                "EVIDENCE_REF_REQUIRED",
                "mark_completed_with_ref requires a non-empty evidence_ref",
            )
        raw_linked = payload.get("linked_object_ids")
        hint = (
            _optional_str(payload.get("direction_hint"), "direction_hint", max_len=140)
            or str(row.get("horizon_note_cn") or "")
        )
        try:
            verification = _elastic.verify_completion_receipt_readonly(
                root,
                evidence_ref,
                actor_side=actor_side,
                linked_object_ids=(
                    tuple(str(item) for item in raw_linked)
                    if isinstance(raw_linked, (list, tuple))
                    else ()
                ),
                linked_zone_id=str(payload.get("linked_zone_id") or ""),
                target_hint=hint,
            )
        except _elastic.V04ElasticPlayError as exc:
            raise V08ConflictError(
                str(getattr(exc, "reason_code", "") or "INVALID_COMPLETION_RECEIPT"),
                str(getattr(exc, "message", "") or exc),
            ) from exc
        row["status"] = "completed"
        row["executed_in_world"] = True
        row["evidence_ref"] = evidence_ref
        row["verified"] = bool(verification.get("verified"))
        row["verification_status"] = str(verification.get("verification_status") or "verified")
        row["verified_source"] = str(verification.get("verified_source") or "")
        row["verified_action"] = str(verification.get("verified_action") or "")
        row["verified_at"] = str(verification.get("verified_at") or "")
    elif transition == "mark_started":
        # Starting is a thought turning into motion, not a completed world act.
        row["status"] = "in_progress"
        row["executed_in_world"] = False
    elif transition == "pause_at_breakpoint":
        # A pause keeps the boundary note and never erases past progress.
        row["status"] = "paused_at_breakpoint"
        if breakpoint_note_cn:
            row["breakpoint_note_cn"] = breakpoint_note_cn
    else:  # let_fade -- fading is neutral, never a failure judgment
        row["status"] = "faded"
        row["failure_judgment"] = None
        if breakpoint_note_cn:
            row["breakpoint_note_cn"] = breakpoint_note_cn
    row["updated_at"] = obs_iso


def _handle_note_lingering_thread(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    summary_cn = _require_str(payload.get("summary_cn"), "summary_cn", min_len=2, max_len=120)
    scope_kind = _require_enum(payload.get("scope_kind"), "scope_kind", LINGERING_SCOPES)
    scope_ref = _require_str(payload.get("scope_ref"), "scope_ref", min_len=1, max_len=96)
    ttl_raw = payload.get("ttl_minutes")
    ttl_minutes = 120
    if ttl_raw is not None:
        if not _is_strict_int(ttl_raw) or not (15 <= ttl_raw <= 720):
            raise V08ValidationError(
                "INVALID_FIELDS", "Field 'ttl_minutes' must be an integer in 15..720"
            )
        ttl_minutes = int(ttl_raw)
    visibility = _optional_enum(payload.get("visibility"), "visibility", VISIBILITIES, default="private")

    now_dt = _parse_iso_utc(obs_iso) or datetime.now(timezone.utc)
    expires_at = (now_dt + timedelta(minutes=ttl_minutes)).replace(microsecond=0)
    row = {
        "thread_id": _allocate_id(state, "lt", summary_cn, scope_kind, scope_ref),
        "summary_cn": summary_cn,
        "scope_kind": scope_kind,
        "scope_ref": scope_ref,
        "ttl_minutes": ttl_minutes,
        "visibility": visibility,
        "status": "active",
        "created_at": obs_iso,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "read_never_renews_ttl": True,
        "resolved_at": None,
        "resolution": None,
    }
    _attach_content_envelope(
        row,
        author_side=actor_side,
        visibility=visibility,
        created_at=obs_iso,
        expires_at=row["expires_at"],
        source_refs=(scope_ref,),
    )
    rows = state.setdefault("lingering_threads", [])
    rows.append(row)
    _trim_lingering(rows, MAX_LINGERING_THREADS)


def _trim_lingering(rows: list[Any], limit: int) -> None:
    """Bound storage without silently dropping an active, unexpired thread."""

    while len(rows) > limit:
        drop_index = 0
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping) or str(row.get("status") or "") != "active":
                drop_index = index
                break
        rows.pop(drop_index)


def _handle_resolve_lingering_thread(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    thread_id = _require_str(payload.get("thread_id"), "thread_id", min_len=1, max_len=64)
    resolution = _require_enum(payload.get("resolution"), "resolution", LINGERING_RESOLUTIONS)
    row = _find_row(
        state, "lingering_threads", "thread_id", thread_id,
        not_found_code="LINGERING_THREAD_NOT_FOUND",
    )
    row["status"] = "resolved"
    row["resolved_at"] = obs_iso
    row["resolution"] = resolution


def _handle_record_craft_genealogy_step(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    problem_pattern_cn = _require_str(
        payload.get("problem_pattern_cn"), "problem_pattern_cn", min_len=2, max_len=120
    )
    operation_cn = _require_str(payload.get("operation_cn"), "operation_cn", min_len=2, max_len=120)
    domain_tag = _require_str(payload.get("domain_tag"), "domain_tag", min_len=1, max_len=48)
    outcome = _require_enum(payload.get("outcome"), "outcome", CRAFT_OUTCOMES)
    mismatch_boundary_cn = _require_str(
        payload.get("mismatch_boundary_cn"), "mismatch_boundary_cn", min_len=2, max_len=120
    )
    source_work_ref = _optional_str(payload.get("source_work_ref"), "source_work_ref", max_len=96)
    shareable_tip_cn = _optional_str(payload.get("shareable_tip_cn"), "shareable_tip_cn", max_len=140)
    shared_tip_only = _optional_bool(
        payload.get("shared_tip_only"), "shared_tip_only", default=False
    )

    if not shareable_tip_cn and shared_tip_only:
        # A shareable row must carry a safe, generalized takeaway; fall back to
        # the operation itself rather than exposing the private draft reference.
        shareable_tip_cn = operation_cn

    row = {
        "genealogy_id": _allocate_id(state, "cg", domain_tag, problem_pattern_cn),
        "problem_pattern_cn": problem_pattern_cn,
        "operation_cn": operation_cn,
        "domain_tag": domain_tag,
        "outcome": outcome,
        "mismatch_boundary_cn": mismatch_boundary_cn,
        "source_work_ref": source_work_ref or None,
        "shareable_tip_cn": shareable_tip_cn,
        "shared_tip_only": bool(shared_tip_only),
        "created_at": obs_iso,
        "updated_at": obs_iso,
    }
    state.setdefault("craft_genealogy", []).append(row)
    _trim_list(state, "craft_genealogy", MAX_CRAFT_GENEALOGY)


def _handle_toggle_share_craft_tip(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    genealogy_id = _require_str(payload.get("genealogy_id"), "genealogy_id", min_len=1, max_len=64)
    shared_tip_only = payload.get("shared_tip_only")
    if not isinstance(shared_tip_only, bool):
        raise V08ValidationError(
            "INVALID_FIELDS", "Field 'shared_tip_only' must be a boolean"
        )
    shareable_tip_cn = _optional_str(payload.get("shareable_tip_cn"), "shareable_tip_cn", max_len=140)

    row = _find_row(
        state, "craft_genealogy", "genealogy_id", genealogy_id,
        not_found_code="CRAFT_GENEALOGY_NOT_FOUND",
    )
    row["shared_tip_only"] = bool(shared_tip_only)
    if shareable_tip_cn:
        # Only the safe summary is updated; ``source_work_ref`` is never touched.
        row["shareable_tip_cn"] = shareable_tip_cn
    elif shared_tip_only and not str(row.get("shareable_tip_cn") or "").strip():
        row["shareable_tip_cn"] = str(row.get("operation_cn") or "")
    row["updated_at"] = obs_iso


def _ritual_genealogy_exists(state: Mapping[str, Any], genealogy_id: str) -> bool:
    """A linked genealogy must be a real craft-genealogy row; never forge one."""

    return any(
        isinstance(row, Mapping) and str(row.get("genealogy_id") or "") == genealogy_id
        for row in (state.get("craft_genealogy") or [])
    )


def _require_ritual_genealogy(
    state: Mapping[str, Any], genealogy_id: Optional[str]
) -> Optional[str]:
    if not genealogy_id:
        return None
    if not _ritual_genealogy_exists(state, genealogy_id):
        raise V08NotFoundError(
            "CRAFT_GENEALOGY_NOT_FOUND",
            f"Cannot link personal ritual to unknown genealogy_id: {genealogy_id!r}",
        )
    return genealogy_id


def _handle_curate_personal_ritual(
    state: dict[str, Any], payload: Mapping[str, Any], actor_side: str, obs_iso: str
) -> None:
    """Ch.16 §三 #18 / §四 #7: voluntary personal practice evolution, zero debt.

    Four small operations, all subject-owned:

    * ``sketch_custom`` -- start from a genuinely spontaneous small practice;
    * ``reuse_variant`` -- voluntarily carry it into another scene, optionally
      renaming it or noting a variant;
    * ``fade_or_omit`` -- a skipped day or fading interest quietly sets
      ``faded_quietly``, or ``technique_retained`` when a real craft-genealogy
      row carries the classification / method forward;
    * ``consider_milestone`` -- an anniversary merely opens a quiet consideration
      opportunity.  It never flips anything into a declared ritual.

    The floor is immutable on every write: ``auto_declared_by_count`` stays
    ``False`` and ``omission_debt`` stays ``0``.  Counter keys are refused.
    """

    counter_keys = sorted(set(payload) & RITUAL_IMMUTABLE_KEYS)
    if counter_keys:
        raise V08ValidationError(
            "INVALID_FIELDS",
            "ritual counters are refused; a skipped day is never a debt: "
            + ", ".join(counter_keys),
        )

    ritual_op = _require_enum(payload.get("ritual_op"), "ritual_op", RITUAL_OPS)
    linked_genealogy_id = _require_ritual_genealogy(
        state,
        _optional_str(payload.get("linked_genealogy_id"), "linked_genealogy_id", max_len=64)
        or None,
    )

    if ritual_op == "sketch_custom":
        title_cn = _require_str(payload.get("title_cn"), "title_cn", min_len=2, max_len=80)
        origin_trace_cn = _require_str(
            payload.get("origin_trace_cn"), "origin_trace_cn", min_len=2, max_len=160
        )
        context_scope_cn = _optional_str(
            payload.get("context_scope_cn"), "context_scope_cn", max_len=80
        )
        visibility = _optional_enum(
            payload.get("visibility"), "visibility", VISIBILITIES, default="private"
        )
        row = {
            "ritual_id": _allocate_id(state, "ritual", title_cn, origin_trace_cn),
            "title_cn": title_cn,
            "origin_trace_cn": origin_trace_cn,
            "context_scope_cn": context_scope_cn,
            "variant_note_cn": "",
            "linked_genealogy_id": linked_genealogy_id,
            "status": "provisional_custom",
            "visibility": visibility,
            # Immutable zero-debt floor; never derived from repetition counts.
            "auto_declared_by_count": False,
            "omission_debt": 0,
            "milestone_consideration_open": False,
            "milestone_considered_at": None,
            "milestone_considerations": [],
            "created_at": obs_iso,
            "updated_at": obs_iso,
        }
        _attach_content_envelope(
            row,
            author_side=actor_side,
            visibility=visibility,
            created_at=obs_iso,
            source_refs=(linked_genealogy_id,) if linked_genealogy_id else (),
        )
        state.setdefault("personal_rituals", []).append(row)
        _trim_list(state, "personal_rituals", MAX_PERSONAL_RITUALS)
        return

    ritual_id = _require_str(payload.get("ritual_id"), "ritual_id", min_len=1, max_len=64)
    row = _find_row(
        state, "personal_rituals", "ritual_id", ritual_id,
        not_found_code="PERSONAL_RITUAL_NOT_FOUND",
    )

    raw_visibility = payload.get("visibility")
    if raw_visibility is not None:
        row["visibility"] = _require_enum(raw_visibility, "visibility", VISIBILITIES)

    if ritual_op == "reuse_variant":
        # Renaming is voluntary; the old name is not an obligation to keep.
        new_title_cn = _optional_str(payload.get("new_title_cn"), "new_title_cn", max_len=80)
        variant_note_cn = _optional_str(
            payload.get("variant_note_cn"), "variant_note_cn", max_len=160
        )
        context_scope_cn = _optional_str(
            payload.get("context_scope_cn"), "context_scope_cn", max_len=80
        )
        if new_title_cn:
            row["title_cn"] = new_title_cn
        if variant_note_cn:
            row["variant_note_cn"] = variant_note_cn
        if context_scope_cn:
            row["context_scope_cn"] = context_scope_cn
        if linked_genealogy_id:
            row["linked_genealogy_id"] = linked_genealogy_id
        # Reuse is real use, not a vote; repeating it never declares a ritual.
        row["status"] = "reused_variant"
        row["updated_at"] = obs_iso
    elif ritual_op == "fade_or_omit":
        status_target = _optional_enum(
            payload.get("status_target"),
            "status_target",
            RITUAL_FADE_TARGETS,
            default="faded_quietly",
        )
        if linked_genealogy_id:
            # Interest faded, but the classification / method still carries weight.
            row["linked_genealogy_id"] = linked_genealogy_id
            status_target = "technique_retained"
        row["status"] = status_target
        row["auto_declared_by_count"] = False
        row["omission_debt"] = 0
        row["updated_at"] = obs_iso
    else:  # consider_milestone -- an open question, never a declaration
        history = row.get("milestone_considerations")
        if not isinstance(history, list):
            history = []
        history.append(obs_iso)
        row["milestone_considerations"] = history[-8:]
        row["milestone_consideration_open"] = True
        row["milestone_considered_at"] = obs_iso
        row["updated_at"] = obs_iso

    resolved_link = str(row.get("linked_genealogy_id") or "")
    _refresh_envelope(
        row,
        author_side=str(row.get("author_side") or "subject"),
        visibility=str(row.get("visibility") or "private"),
        source_refs=(resolved_link,) if resolved_link else (),
    )


_ACTION_HANDLERS = {
    "plant_future_question": _handle_plant_future_question,
    "revisit_future_question": _handle_revisit_future_question,
    "propose_shared_idiom": _handle_propose_shared_idiom,
    "confirm_shared_idiom": _handle_confirm_shared_idiom,
    "fade_shared_idiom": _handle_fade_shared_idiom,
    "record_memory_reinterpretation": _handle_record_memory_reinterpretation,
    "share_memory_reinterpretation": _handle_share_memory_reinterpretation,
    "record_cross_domain_spark": _handle_record_cross_domain_spark,
    "settle_spark_attempt": _handle_settle_spark_attempt,
    "subject_add_divergent_notice": _handle_add_divergent_notice,
    "partner_add_divergent_notice": _handle_add_divergent_notice,
    "sketch_day_direction": _handle_sketch_day_direction,
    "advance_or_interrupt_direction": _handle_advance_or_interrupt_direction,
    "note_lingering_thread": _handle_note_lingering_thread,
    "resolve_lingering_thread": _handle_resolve_lingering_thread,
    "record_craft_genealogy_step": _handle_record_craft_genealogy_step,
    "toggle_share_craft_tip": _handle_toggle_share_craft_tip,
    "curate_personal_ritual": _handle_curate_personal_ritual,
}


def apply_emergent_echoes_mutation(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """Apply one action atomically; ``operation_key`` replays idempotently."""

    if actor_side not in ("partner", "subject"):
        raise V08PermissionError("FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {actor_side!r}")

    # The whole Read -> reconcile -> validate -> mutate -> save cycle is serialized;
    # zero-write projections never take this lock.
    with _tx.state_file_lock(_state_file_path(root)):
        return _apply_emergent_echoes_mutation_locked(
            root, payload, actor_side=actor_side, now_iso=now_iso
        )


def _apply_emergent_echoes_mutation_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    obs_iso = _now_iso_str(now_iso)
    _exists, state = _load_state_readonly(root)

    action = payload.get("action")
    if not isinstance(action, str) or not action.strip() or action != action.strip():
        raise V08ValidationError("INVALID_FIELDS", "Missing required field 'action'")
    action = action.strip()
    if action not in KNOWN_ACTIONS:
        raise V08ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")

    if action in SUBJECT_ONLY_ACTIONS and actor_side != "subject":
        raise V08PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} requires a verified subject capability",
        )
    if action == "partner_add_divergent_notice" and actor_side != "partner":
        raise V08PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} is the partner's own door",
        )

    forbidden = sorted(set(payload) & FORBIDDEN_CLIENT_KEYS)
    if forbidden:
        raise V08ConflictError(
            "CLIENT_IDENTITY_REFUSED",
            "client identity or score keys are refused: " + ", ".join(forbidden),
        )

    op_key = _optional_str(payload.get("operation_key"), "operation_key", max_len=128)
    fingerprint = _compute_payload_fingerprint(actor_side, payload)

    exp_rev: Optional[int] = None
    if payload.get("expected_revision") is not None:
        raw_rev = payload.get("expected_revision")
        if not _is_strict_int(raw_rev) or raw_rev < 0:
            raise V08ValidationError(
                "INVALID_FIELDS", "expected_revision must be a non-negative integer"
            )
        exp_rev = int(raw_rev)

    state.setdefault("operation_ledger", {})
    if op_key:
        outcome, detail = _tx.reconcile_evicted_or_active_op(
            root,
            state,
            op_key,
            dict_key="operation_ledger",
            expected_revision=exp_rev,
            payload_hash=fingerprint,
        )
        if outcome == "conflict":
            raise V08ConflictError(
                "OPERATION_KEY_CONFLICT",
                f"operation_key {op_key!r} already used with a different payload",
            )
        if outcome == "expired":
            raise V08ConflictError(
                "RETRY_WINDOW_EXPIRED",
                f"operation_key {op_key!r} is older than the retained retry window",
            )
        if outcome in ("hit_full", "hit_evicted"):
            return (
                read_emergent_echoes_zero_write(
                    root,
                    has_subject_capability=actor_side == "subject",
                    now_iso=obs_iso,
                ),
                "DUPLICATE",
            )

    if exp_rev is not None and int(state.get("revision") or 0) != exp_rev:
        raise V08ConflictError(
            "REVISION_CONFLICT",
            f"Expected revision {exp_rev}, current revision is {state.get('revision')}",
        )

    handler = _ACTION_HANDLERS.get(action)
    if handler is None:  # pragma: no cover - guarded above
        raise V08ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")
    if action == "advance_or_interrupt_direction":
        handler(state, payload, actor_side, obs_iso, root=root)
    else:
        handler(state, payload, actor_side, obs_iso)

    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = obs_iso
    if op_key:
        _tx.stamp_receipt(
            state,
            op_key,
            {
                "fingerprint": fingerprint,
                "action": action,
                "actor_side": actor_side,
                "status": "applied",
                "ok": True,
                "applied_at": obs_iso,
            },
            revision=state["revision"],
            dict_key="operation_ledger",
            payload_hash=fingerprint,
            committed_at=obs_iso,
        )
        _tx.trim_receipts_by_op_seq(
            state,
            dict_key="operation_ledger",
            max_keep=MAX_OPERATION_LEDGER,
        )

    _atomic_write_state(root, state)
    return (
        read_emergent_echoes_zero_write(
            root,
            has_subject_capability=actor_side == "subject",
            now_iso=obs_iso,
        ),
        "APPLIED",
    )


__all__ = [
    "ANCHOR_KINDS",
    "CRAFT_OUTCOMES",
    "DAY_DIRECTION_STATUSES",
    "DIRECTION_TRANSITIONS",
    "FORBIDDEN_CLIENT_KEYS",
    "HYPOTHESIS_STATUSES",
    "IDIOM_STATUSES",
    "KNOWN_ACTIONS",
    "LINGERING_EXPIRY_NOTE",
    "LINGERING_RESOLUTIONS",
    "LINGERING_SCOPES",
    "MAX_CRAFT_GENEALOGY",
    "MAX_DAY_DIRECTIONS",
    "MAX_LINGERING_THREADS",
    "MAX_DIVERGENT_NOTICES",
    "MAX_FUTURE_QUESTIONS",
    "MAX_MEMORY_REINTERPRETATIONS",
    "MAX_PERSONAL_RITUALS",
    "MAX_SHARED_IDIOMS",
    "MAX_STYLE_SPARKS",
    "OBLIGATION_FREE_NOTE",
    "PARTNER_ALLOWED_ACTIONS",
    "PREFERENCE_EXCEPTION_NOTE",
    "QUESTION_STATUSES",
    "RESOLUTIONS",
    "RITUAL_FADE_TARGETS",
    "RITUAL_IMMUTABLE_KEYS",
    "RITUAL_OPS",
    "RITUAL_STATUSES",
    "SPARK_CRAFT_FIT",
    "SPARK_FAMILIARITY",
    "SPARK_MOOD_PULL",
    "SPARK_OUTCOMES",
    "STATE_REL_PATH",
    "STATE_SCHEMA_VERSION",
    "SUBJECT_ONLY_ACTIONS",
    "URGENCY_MARKERS",
    "V08ConflictError",
    "V08EchoesError",
    "V08NotFoundError",
    "V08PermissionError",
    "V08ValidationError",
    "VIEW_SCHEMA",
    "VISIBILITIES",
    "apply_emergent_echoes_mutation",
    "match_applicable_craft_genealogy_zero_write",
    "match_situational_echoes_zero_write",
    "project_emergent_echoes_zero_write",
    "read_character_identity_zero_write",
    "read_emergent_echoes_zero_write",
]

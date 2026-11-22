#!/usr/bin/env python3
"""v3.0 第 23/26/27/28 章体验切片：有限视野三次遇见、递一页材料、店主手头事与听说边界。

三个板块（全部有界容量，不膨胀）：

1. ``encounters``：同一份材料的三次改观。
   - ``FIRST_GLANCE``：只看过其中几页；``seen_pages`` 之外的页面严格扣留
     （``unseen_pages_withheld=True``），绝不冒充已经知道；
   - ``QUESTION_DRIVEN_REVISIT``：带着具体问题重看，注意到原先漏掉的细节；
   - ``NUANCED_RECOGNITION``：真实尝试后形成具体评价，并保留局部不同意
     （``partial_disagreement_kept``）。``tried_in_work_ref`` 只在确实有出处时记录，
     未提供就保持 ``None``，绝不编造「试过了」。
2. ``shared_materials``：伴侣递来的一段文字/主题材料，不是下派任务。
   - 初始 ``RECEIVED_UNOPENED``，零催促红点、零未读债务；
   - TA 可部分打开（``OPENED_PARTIAL``）或真实用到作品中（``USED_IN_WORK``）；
   - 伴侣一旦撤回，正文与反应即刻擦除并标记 ``WITHDRAWN``，之后任何上下文都不得再引用原文。
3. ``npc_errands_and_hearsay``：店主自己的手头事与二手信息来源分立。
   - ``hearsay`` 必须写明是谁听说的（``hearsay_source_cn``），默认 ``verified_in_person=False``；
   - 只有显式 ``verified_in_person=True`` 且给出实地说明才算亲眼所见；
   - 顺手帮忙（``suggested``）与真实委托（``adopted``）分开记。

红线保证：全程零外部大模型调用（``llm_calls == 0``）；读视图与背景摘要 100% 零磁盘写入。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:  # package import (``components.life.scripts``)
    from . import scoped_content_record as _scr
    from . import state_tx_guard as _tx
except ImportError:  # pragma: no cover - flat ``components/life/scripts`` import path
    import scoped_content_record as _scr  # type: ignore[no-redef]
    import state_tx_guard as _tx  # type: ignore[no-redef]

VIEW_SCHEMA = "nyairo.v03.encounters.v1"
STATE_SCHEMA_VERSION = "life.v03.encounters.v1"
STATE_REL_PATH = ("phone", "v03_encounters.json")

STAGES: frozenset[str] = frozenset({
    "FIRST_GLANCE",
    "QUESTION_DRIVEN_REVISIT",
    "NUANCED_RECOGNITION",
})

MATERIAL_RECEIVED = "RECEIVED_UNOPENED"
MATERIAL_OPENED_PARTIAL = "OPENED_PARTIAL"
MATERIAL_USED_IN_WORK = "USED_IN_WORK"
MATERIAL_WITHDRAWN = "WITHDRAWN"
MATERIAL_STATUSES: frozenset[str] = frozenset({
    MATERIAL_RECEIVED,
    MATERIAL_OPENED_PARTIAL,
    MATERIAL_USED_IN_WORK,
    MATERIAL_WITHDRAWN,
})

MATERIAL_MODES: frozenset[str] = frozenset({"open_partial", "use_in_work"})

NPC_KINDS: frozenset[str] = frozenset({"ongoing_errand", "hearsay"})
SUBJECT_HELP_STATUSES: frozenset[str] = frozenset({
    "none",
    "suggested",
    "adopted",
    "moved_aside",
})

MAX_ENCOUNTERS = 24
MAX_SHARED_MATERIALS = 16
MAX_NPC_ENTRIES = 20
MAX_PRIOR_IMPRESSIONS = 4
MAX_OPERATION_LEDGER = 256


class V03EncountersError(Exception):
    """结构化业务异常：HTTP 视图按 ``status_code`` / ``reason_code`` 映射。"""

    def __init__(self, status_code: int, reason_code: str, message: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        super().__init__(f"{self.reason_code}: {self.message}")


class V03ValidationError(V03EncountersError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(400, reason_code, message)


class V03PermissionError(V03EncountersError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(403, reason_code, message)


class V03NotFoundError(V03EncountersError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(404, reason_code, message)


class V03ConflictError(V03EncountersError):
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
            raise V03ValidationError("INVALID_FIELDS", f"Malformed ISO timestamp: {now_iso!r}")
        return now_iso.strip()
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _require_str(value: Any, field_name: str, *, max_len: int = 240) -> str:
    if not isinstance(value, str):
        raise V03ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a string")
    if "\x00" in value:
        raise V03ValidationError("INVALID_FIELDS", f"Field {field_name!r} contains NUL byte")
    trimmed = value.strip()
    if not trimmed:
        raise V03ValidationError("INVALID_FIELDS", f"Field {field_name!r} must not be blank")
    if len(trimmed) > max_len:
        raise V03ValidationError("INVALID_FIELDS", f"Field {field_name!r} exceeds max length {max_len}")
    return trimmed


def _optional_str(value: Any, field_name: str, *, max_len: int = 320) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise V03ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a string")
    if "\x00" in value:
        raise V03ValidationError("INVALID_FIELDS", f"Field {field_name!r} contains NUL byte")
    trimmed = value.strip()
    if len(trimmed) > max_len:
        raise V03ValidationError("INVALID_FIELDS", f"Field {field_name!r} exceeds max length {max_len}")
    return trimmed


def _optional_bool(value: Any, field_name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise V03ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a boolean")
    return value


def _pages_list(value: Any, field_name: str, *, total_pages: int, allow_empty: bool = False) -> list[int]:
    if not isinstance(value, (list, tuple)):
        raise V03ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a list of page numbers")
    if not value and not allow_empty:
        raise V03ValidationError("INVALID_FIELDS", f"Field {field_name!r} must not be empty")
    out: list[int] = []
    for item in value:
        if not _is_strict_int(item):
            raise V03ValidationError("INVALID_FIELDS", f"Field {field_name!r} must contain integers")
        if item < 1 or item > total_pages:
            raise V03ValidationError(
                "INVALID_FIELDS",
                f"Field {field_name!r} page {item} is outside 1..{total_pages}",
            )
        if item not in out:
            out.append(item)
    return sorted(out)


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
        "encounters": [],
        "shared_materials": [],
        "npc_errands_and_hearsay": [],
        "operation_ledger": {},
    }


def _load_state_readonly(root: Path | str) -> tuple[bool, dict[str, Any]]:
    """100% 零磁盘写入读取落盘状态（零建目录、零临时文件、零 mtime 触碰）。"""

    path = _state_file_path(root)
    if not path.exists():
        return False, _empty_raw_state()
    if not path.is_file():
        raise V03EncountersError(503, "CORRUPT_STATE_FILE", "State path is not a regular file")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise V03EncountersError(503, "CORRUPT_STATE_FILE", f"Corrupted encounters state JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema_version") != STATE_SCHEMA_VERSION:
        raise V03EncountersError(503, "CORRUPT_STATE_FILE", "Invalid schema_version in encounters state file")
    merged = _empty_raw_state()
    merged.update(copy.deepcopy(doc))
    return True, merged


def _atomic_write_state(root: Path | str, state: Mapping[str, Any]) -> None:
    target = _state_file_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(dict(state), ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".v03_encounters_", suffix=".tmp", dir=str(target.parent))
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


def _allocate_id(state: dict[str, Any], prefix: str) -> str:
    seq = int(state.get("next_seq") or 1)
    state["next_seq"] = seq + 1
    return f"{prefix}_{seq:04d}"


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


def _trim_list(state: dict[str, Any], key: str, limit: int, *, withdrawn_key: Optional[str] = None) -> None:
    rows = state.get(key)
    if not isinstance(rows, list):
        return
    while len(rows) > limit:
        drop_idx = 0
        if withdrawn_key:
            for idx, row in enumerate(rows):
                if isinstance(row, Mapping) and row.get(withdrawn_key) == MATERIAL_WITHDRAWN:
                    drop_idx = idx
                    break
        rows.pop(drop_idx)


# ---------------------------------------------------------------------------------------------
# shared scoped-content substrate (visibility / sealed / withdrawn gates)
# ---------------------------------------------------------------------------------------------


def _material_envelope(
    *,
    status: str,
    created_at: Optional[str],
) -> dict[str, Any]:
    """Canonical envelope for a shared material: partner-authored, never private."""

    return _scr.normalize_content_envelope(
        author_side="partner",
        visibility="shared",
        created_at=created_at,
        sealed=status == MATERIAL_RECEIVED,
        withdrawn=status == MATERIAL_WITHDRAWN,
    )


def _refresh_material_envelope(material: dict[str, Any], status: str) -> None:
    previous = material.get("content_envelope")
    previous = previous if isinstance(previous, Mapping) else {}
    material["content_envelope"] = _material_envelope(
        status=status,
        created_at=previous.get("created_at") or material.get("dropped_at") or material.get("updated_at"),
    )


def _encounter_envelope(*, shared: bool, created_at: str, title_cn: str) -> dict[str, Any]:
    return _scr.normalize_content_envelope(
        author_side="subject",
        visibility="shared" if shared else "private",
        created_at=created_at,
        frozen_fact=title_cn,
    )


def _material_gate(row: Mapping[str, Any]) -> dict[str, Any]:
    """Gate a material for the subject: sealed metadata may be read, the body may not."""

    prepared = dict(row)
    prepared.setdefault("author_side", "partner")
    prepared.setdefault("visibility", "shared")
    return _scr.check_read_and_attention_gate(
        prepared,
        actor_side="subject",
        now_iso=None,
        author_field="author_side",
        visibility_field="visibility",
        status_field="status",
    )


def _encounter_gate(row: Mapping[str, Any], *, actor_side: str, now_iso: Optional[str]) -> dict[str, Any]:
    prepared = dict(row)
    prepared.setdefault("author_side", "subject")
    return _scr.check_read_and_attention_gate(
        prepared,
        actor_side=actor_side,
        now_iso=now_iso,
        author_field="author_side",
        visibility_field="visibility",
        status_field="status",
    )


def _enforce_frozen_title(encounter: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    """An encounter's material title is the frozen original observation.

    Callers that try to rewrite it are refused instead of silently changing what
    was first written down.
    """

    if "target_title_cn" not in payload:
        return
    candidate = {"target_title_cn": _optional_str(payload.get("target_title_cn"), "target_title_cn", max_len=120)}
    try:
        _scr.enforce_frozen_fact_immutability(
            encounter, candidate, fact_key="target_title_cn"
        )
    except ValueError as exc:
        if str(exc) != _scr.FROZEN_FACT_IMMUTABLE:
            raise
        raise V03ConflictError(
            "FROZEN_FACT_IMMUTABLE",
            "The materially recorded original observation cannot be rewritten",
        ) from exc



# ---------------------------------------------------------------------------------------------
# project (pure zero-write read)
# ---------------------------------------------------------------------------------------------


def _public_material(row: Mapping[str, Any], *, has_subject_capability: bool) -> dict[str, Any]:
    doc = copy.deepcopy(dict(row))
    if _material_gate(doc)["withdrawn"]:
        # A withdrawn material leaves no old excerpt/reaction behind for any viewer.
        doc["excerpt_cn"] = ""
        doc["reaction_cn"] = ""
        doc["used_in_artifact_id"] = None
    elif not has_subject_capability:
        # The partner dropped it, so their own excerpt is theirs; still never claim usage.
        doc["used_in_artifact_id"] = doc.get("used_in_artifact_id")
    return doc


# ---------------------------------------------------------------------------------------------
# concern-driven attention lens (pure zero-write, v3.0 Ch.23 §一 + Ch.30)
# ---------------------------------------------------------------------------------------------


_CONCERN_TOKEN_RE = re.compile(r"[0-9A-Za-z_]+|[一-鿿]+")


def _concern_tokens(text: Any) -> list[str]:
    """Deterministic token windows; no word-segmenter dependency, no LLM."""

    tokens: list[str] = []
    for match in _CONCERN_TOKEN_RE.finditer(str(text or "")):
        token = match.group(0)
        if token.isascii():
            if len(token) >= 2:
                tokens.append(token.lower())
            continue
        if len(token) <= 12:
            tokens.append(token)
        for size in (4, 3, 2):
            if len(token) < size:
                continue
            for start in range(0, len(token) - size + 1):
                tokens.append(token[start : start + size])
    return tokens


def _norm_concern(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _concern_hit(concern_norm: str, field_norm: str) -> bool:
    if not concern_norm or not field_norm:
        return False
    if concern_norm in field_norm:
        return True
    return any(token and token in field_norm for token in _concern_tokens(concern_norm))


def _read_json_readonly(path: Path) -> Any:
    """Read a JSON document without creating, touching or rewriting anything."""

    try:
        if not path.exists() or path.is_symlink() or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None


def _gather_active_concerns(root: Path | str, explicit: Sequence[str] | None) -> list[str]:
    root_path = Path(root)
    out: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)

    for item in explicit or ():
        add(item)

    v08_doc = _read_json_readonly(root_path / "phone" / "v08_emergent_echoes.json")
    if isinstance(v08_doc, Mapping):
        for row in v08_doc.get("future_self_questions") or []:
            if not isinstance(row, Mapping) or row.get("status") != "open":
                continue
            keywords = row.get("keywords")
            if isinstance(keywords, list):
                for keyword in keywords:
                    add(keyword)
            add(row.get("question_cn"))
        for row in v08_doc.get("day_directions") or []:
            if not isinstance(row, Mapping) or row.get("status") == "faded":
                continue
            add(row.get("horizon_note_cn"))
            add(row.get("bound_ref"))

    try:
        _exists, state = _load_state_readonly(root_path)
    except V03EncountersError:
        state = _empty_raw_state()
    for row in state.get("encounters") or []:
        if not isinstance(row, Mapping):
            continue
        if row.get("stage") == "QUESTION_DRIVEN_REVISIT":
            add(row.get("active_question_cn"))

    return out


def project_concern_driven_attention_zero_write(
    root: Path | str,
    *,
    concerns: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """当下关切改变观察重点：同一面架子上，先注意到什么会随问题而变。

    严格只读：不建目录、不建临时文件、不触碰 mtime、零大模型调用。

    * 只扫描已经可见的字段：遇见页只取 ``target_title_cn`` /
      ``current_impression_cn`` / ``noticed_detail_cn``；
    * ``RECEIVED_UNOPENED`` 的材料只看 ``title_cn`` 与 ``topic_tag``，绝不提前
      翻看封在信封里的 ``excerpt_cn``（Ch.23/30 §3：整份文件在磁盘上不等于全文可先搜）；
    * ``WITHDRAWN`` 的材料整条跳过，绝不参与匹配；
    * 关切来源：显式传入的 ``concerns``、v08 ``open`` 问题与未淡掉的方向、
      以及 ``QUESTION_DRIVEN_REVISIT`` 阶段的 ``active_question_cn``。
    """

    try:
        exists, state = _load_state_readonly(root)
    except V03EncountersError:
        exists, state = False, _empty_raw_state()
    if not exists:
        return []

    active_concerns = _gather_active_concerns(root, concerns)
    if not active_concerns:
        return []
    norm_concerns = [(concern, _norm_concern(concern)) for concern in active_concerns]
    norm_concerns = [(orig, norm) for orig, norm in norm_concerns if norm]

    candidates: list[dict[str, Any]] = []
    #: Rows whose visible body may be read; scored through the shared concern ranking.
    ranking_rows: list[dict[str, Any]] = []

    for row in state.get("encounters") or []:
        if not isinstance(row, Mapping):
            continue
        gate = _encounter_gate(row, actor_side="subject", now_iso=None)
        if not gate["allowed_read"]:
            continue
        visible_fields = [
            row.get("target_title_cn"),
            row.get("current_impression_cn"),
            row.get("noticed_detail_cn"),
        ]
        key = len(candidates)
        candidates.append({
            "key": key,
            "ref": str(row.get("encounter_id") or row.get("target_ref") or ""),
            "title_cn": str(row.get("target_title_cn") or ""),
            "source_kind": "encounter",
            "fields": [str(field) for field in visible_fields if field],
            "sealed": False,
        })
        # Authorization already happened above: the lens belongs to the subject,
        # so the shared ranker is fed an already-authorized (shared) copy.
        ranking_rows.append({
            "_lens_key": key,
            "author_side": "subject",
            "visibility": "shared",
            "status": "",
            "_lens_text": " ".join(str(field) for field in visible_fields if field),
        })

    for row in state.get("shared_materials") or []:
        if not isinstance(row, Mapping):
            continue
        gate = _material_gate(row)
        if not gate["allowed_read"]:
            # A withdrawn material must never surface, not even as a cue.
            continue
        visible_fields = [row.get("title_cn"), row.get("topic_tag")]
        if gate["allowed_body"]:
            # Only after a real open / use may the body text inform attention.
            visible_fields.append(row.get("excerpt_cn"))
        key = len(candidates)
        candidates.append({
            "key": key,
            "ref": str(row.get("material_id") or ""),
            "title_cn": str(row.get("title_cn") or ""),
            "source_kind": "shared_material",
            "fields": [str(field) for field in visible_fields if field],
            "sealed": not gate["allowed_body"],
        })
        if gate["allowed_body"]:
            ranking_rows.append({
                "_lens_key": key,
                "author_side": "partner",
                "visibility": "shared",
                "status": str(row.get("status") or ""),
                "_lens_text": " ".join(str(field) for field in visible_fields if field),
            })
        # A sealed row is deliberately absent from ranking_rows: only its title /
        # topic metadata may be compared locally; the sealed body is never ranked.

    hit_pairs: set[tuple[int, int]] = set()
    for concern_index, (orig, norm) in enumerate(norm_concerns):
        ranked = _scr.rank_authorized_by_concern(
            ranking_rows,
            concern_text=orig,
            actor_side="subject",
            now_iso=None,
            text_fn=lambda item: str(item.get("_lens_text") or ""),
            limit=max(1, len(ranking_rows)),
        )
        hit_keys = {int(item.get("_lens_key")) for item in ranked}
        for candidate in candidates:
            if candidate["key"] in hit_keys:
                hit_pairs.add((candidate["key"], concern_index))
            elif candidate["sealed"] and any(
                _concern_hit(norm, _norm_concern(field)) for field in candidate["fields"]
            ):
                # Sealed existence metadata (title / topic) is readable; its body is not.
                hit_pairs.add((candidate["key"], concern_index))

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for order, candidate in enumerate(candidates):
        score = 0
        matched_concern_cn = ""
        for concern_index, (orig, _norm) in enumerate(norm_concerns):
            if (candidate["key"], concern_index) in hit_pairs:
                score += 1
                if not matched_concern_cn:
                    matched_concern_cn = orig
        if score <= 0:
            continue
        scored.append((
            -score,
            order,
            {
                "ref": candidate["ref"],
                "title_cn": candidate["title_cn"],
                "source_kind": candidate["source_kind"],
                "matched_concern_cn": matched_concern_cn,
                "overlap_score": score,
            },
        ))

    scored.sort(key=lambda item: (item[0], item[1]))
    return [cue for _neg_score, _order, cue in scored[:6]]


def project_v03_encounters(
    root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """读取三次遇见、递材料与店主听说投影（100% 零磁盘写入）。"""

    exists, state = _load_state_readonly(root)
    observed_at = _now_iso_str(now_iso)
    actor_side = "subject" if has_subject_capability else "partner"
    encounters: list[dict[str, Any]] = []
    for row in state.get("encounters") or []:
        if not isinstance(row, Mapping):
            continue
        if not _encounter_gate(row, actor_side=actor_side, now_iso=observed_at)["allowed_read"]:
            # Hidden encounters are removed whole: no name, no stage, no count.
            continue
        encounters.append(copy.deepcopy(dict(row)))

    materials = [
        _public_material(row, has_subject_capability=has_subject_capability)
        for row in state.get("shared_materials") or []
        if isinstance(row, Mapping)
    ]
    npc_rows = [
        copy.deepcopy(dict(row))
        for row in state.get("npc_errands_and_hearsay") or []
        if isinstance(row, Mapping)
    ]
    projected: dict[str, Any] = {
        "schema": VIEW_SCHEMA,
        "state_exists": bool(exists),
        "perspective": "subject" if has_subject_capability else "partner",
        "revision": int(state.get("revision") or 0),
        "observed_at": observed_at,
        "encounters": encounters,
        "shared_materials": materials,
        "npc_errands_and_hearsay": npc_rows,
        "llm_calls": 0,
    }
    if has_subject_capability:
        # The private attention queue belongs to the subject only; the partner
        # default view must not snoop what TA would notice first.
        projected["attention_cues"] = project_concern_driven_attention_zero_write(root)
    return projected


# ---------------------------------------------------------------------------------------------
# action handlers
# ---------------------------------------------------------------------------------------------


def _find_encounter(state: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    encounter_id = _optional_str(payload.get("encounter_id"), "encounter_id", max_len=64)
    target_ref = _optional_str(payload.get("target_ref"), "target_ref", max_len=160)
    rows = [row for row in state.get("encounters") or [] if isinstance(row, dict)]
    if encounter_id:
        for row in rows:
            if str(row.get("encounter_id") or "") == encounter_id:
                return row
        raise V03NotFoundError("ENCOUNTER_NOT_FOUND", f"Unknown encounter_id: {encounter_id!r}")
    if target_ref:
        matches = [row for row in rows if str(row.get("target_ref") or "") == target_ref]
        if matches:
            return matches[-1]
        raise V03NotFoundError("ENCOUNTER_NOT_FOUND", f"Unknown target_ref: {target_ref!r}")
    raise V03ValidationError("INVALID_FIELDS", "encounter_id or target_ref is required")


def _push_prior_impression(encounter: dict[str, Any], new_impression: str) -> None:
    previous = str(encounter.get("current_impression_cn") or "").strip()
    if previous and previous != new_impression:
        priors = encounter.get("prior_impressions")
        if not isinstance(priors, list):
            priors = []
        priors.append(previous)
        encounter["prior_impressions"] = priors[-MAX_PRIOR_IMPRESSIONS:]


def _handle_record_first_glance(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    target_ref = _require_str(payload.get("target_ref"), "target_ref", max_len=160)
    target_title_cn = _require_str(payload.get("target_title_cn"), "target_title_cn", max_len=120)
    raw_total = payload.get("total_pages")
    if not _is_strict_int(raw_total) or int(raw_total) < 1:
        raise V03ValidationError("INVALID_FIELDS", "total_pages must be an integer >= 1")
    total_pages = int(raw_total)
    seen_pages = _pages_list(payload.get("seen_pages"), "seen_pages", total_pages=total_pages)
    impression = _require_str(payload.get("current_impression_cn"), "current_impression_cn", max_len=400)
    shared = _optional_bool(payload.get("shared"), "shared", default=True)

    encounter = {
        "encounter_id": _allocate_id(state, "enc"),
        "target_ref": target_ref,
        "target_title_cn": target_title_cn,
        "stage": "FIRST_GLANCE",
        "seen_pages": seen_pages,
        "total_pages": total_pages,
        "unseen_pages_withheld": True,
        "current_impression_cn": impression,
        "prior_impressions": [],
        "active_question_cn": "",
        "noticed_detail_cn": "",
        "partial_disagreement_kept": "",
        "tried_in_work_ref": None,
        "shared": shared,
        "updated_at": obs_iso,
        "content_envelope": _encounter_envelope(
            shared=shared, created_at=obs_iso, title_cn=target_title_cn
        ),
    }
    state.setdefault("encounters", []).append(encounter)
    _trim_list(state, "encounters", MAX_ENCOUNTERS)


def _handle_revisit_with_question(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    encounter = _find_encounter(state, payload)
    _enforce_frozen_title(encounter, payload)
    total_pages = int(encounter.get("total_pages") or 1)
    newly_seen = _pages_list(
        payload.get("newly_seen_pages"), "newly_seen_pages", total_pages=total_pages
    )
    active_question_cn = _require_str(payload.get("active_question_cn"), "active_question_cn", max_len=400)
    noticed_detail_cn = _require_str(payload.get("noticed_detail_cn"), "noticed_detail_cn", max_len=400)
    new_impression = _optional_str(payload.get("current_impression_cn"), "current_impression_cn", max_len=400)

    merged = set(int(p) for p in (encounter.get("seen_pages") or []))
    merged.update(newly_seen)
    encounter["seen_pages"] = sorted(merged)
    if new_impression:
        _push_prior_impression(encounter, new_impression)
        encounter["current_impression_cn"] = new_impression
    encounter["active_question_cn"] = active_question_cn
    encounter["noticed_detail_cn"] = noticed_detail_cn
    encounter["stage"] = "QUESTION_DRIVEN_REVISIT"
    encounter["updated_at"] = obs_iso


def _handle_record_nuanced_recognition(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    encounter = _find_encounter(state, payload)
    _enforce_frozen_title(encounter, payload)
    impression = _require_str(payload.get("current_impression_cn"), "current_impression_cn", max_len=400)
    partial = _require_str(
        payload.get("partial_disagreement_kept"), "partial_disagreement_kept", max_len=400
    )
    tried_ref = _optional_str(payload.get("tried_in_work_ref"), "tried_in_work_ref", max_len=160)

    _push_prior_impression(encounter, impression)
    encounter["current_impression_cn"] = impression
    encounter["partial_disagreement_kept"] = partial
    # Never invent a trial: an absent reference stays None.
    encounter["tried_in_work_ref"] = tried_ref or None
    encounter["stage"] = "NUANCED_RECOGNITION"
    encounter["updated_at"] = obs_iso


def _find_material(state: dict[str, Any], material_id: str) -> dict[str, Any]:
    for row in state.get("shared_materials") or []:
        if isinstance(row, dict) and str(row.get("material_id") or "") == material_id:
            return row
    raise V03NotFoundError("MATERIAL_NOT_FOUND", f"Unknown material_id: {material_id!r}")


def _handle_drop_shared_material(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    title_cn = _require_str(payload.get("title_cn"), "title_cn", max_len=80)
    excerpt_cn = _require_str(payload.get("excerpt_cn"), "excerpt_cn", max_len=600)
    topic_tag = _optional_str(payload.get("topic_tag"), "topic_tag", max_len=40)
    raw_pages = payload.get("page_count")
    if raw_pages is None:
        page_count = 1
    elif not _is_strict_int(raw_pages) or int(raw_pages) < 1:
        raise V03ValidationError("INVALID_FIELDS", "page_count must be an integer >= 1")
    else:
        page_count = int(raw_pages)

    material = {
        "material_id": _allocate_id(state, "mat"),
        "title_cn": title_cn,
        "excerpt_cn": excerpt_cn,
        "topic_tag": topic_tag,
        "page_count": page_count,
        "status": MATERIAL_RECEIVED,
        "opened_pages": [],
        "reaction_cn": "",
        "used_in_artifact_id": None,
        "dropped_at": obs_iso,
        "updated_at": obs_iso,
        "content_envelope": _material_envelope(
            status=MATERIAL_RECEIVED, created_at=obs_iso
        ),
    }
    state.setdefault("shared_materials", []).append(material)
    _trim_list(state, "shared_materials", MAX_SHARED_MATERIALS, withdrawn_key="status")


def _handle_withdraw_shared_material(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    material_id = _require_str(payload.get("material_id"), "material_id", max_len=64)
    material = _find_material(state, material_id)
    # Erase the body and any reaction immediately; later context must never quote it again.
    material["excerpt_cn"] = ""
    material["reaction_cn"] = ""
    material["used_in_artifact_id"] = None
    material["status"] = MATERIAL_WITHDRAWN
    material["updated_at"] = obs_iso
    _refresh_material_envelope(material, MATERIAL_WITHDRAWN)


def _handle_open_or_use_shared_material(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    material_id = _require_str(payload.get("material_id"), "material_id", max_len=64)
    mode = _require_str(payload.get("mode"), "mode", max_len=32).lower()
    if mode not in MATERIAL_MODES:
        raise V03ValidationError("INVALID_FIELDS", "mode must be open_partial or use_in_work")
    material = _find_material(state, material_id)
    if str(material.get("status") or "") == MATERIAL_WITHDRAWN:
        raise V03ConflictError("MATERIAL_WITHDRAWN", "The material was withdrawn and cannot be opened")

    page_count = int(material.get("page_count") or 1)
    if mode == "open_partial":
        raw_pages = payload.get("opened_pages")
        opened_pages = (
            _pages_list(raw_pages, "opened_pages", total_pages=page_count, allow_empty=True)
            if raw_pages is not None
            else []
        )
        material["opened_pages"] = opened_pages
        material["reaction_cn"] = _optional_str(payload.get("reaction_cn"), "reaction_cn", max_len=400)
        if str(material.get("status") or "") != MATERIAL_USED_IN_WORK:
            material["status"] = MATERIAL_OPENED_PARTIAL
    else:
        used_in_artifact_id = _require_str(
            payload.get("used_in_artifact_id"), "used_in_artifact_id", max_len=160
        )
        material["used_in_artifact_id"] = used_in_artifact_id
        material["status"] = MATERIAL_USED_IN_WORK
    material["updated_at"] = obs_iso
    _refresh_material_envelope(material, str(material.get("status") or ""))


def _handle_record_npc_errand_or_hearsay(state: dict[str, Any], payload: Mapping[str, Any], obs_iso: str) -> None:
    npc_id = _require_str(payload.get("npc_id"), "npc_id", max_len=80)
    npc_label_cn = _require_str(payload.get("npc_label_cn"), "npc_label_cn", max_len=80)
    place_id = _require_str(payload.get("place_id"), "place_id", max_len=80)
    kind = _require_str(payload.get("kind"), "kind", max_len=32).lower()
    if kind not in NPC_KINDS:
        raise V03ValidationError("INVALID_FIELDS", "kind must be ongoing_errand or hearsay")
    summary_cn = _require_str(payload.get("summary_cn"), "summary_cn", max_len=400)

    hearsay_source_cn = _optional_str(payload.get("hearsay_source_cn"), "hearsay_source_cn", max_len=120)
    if kind == "hearsay" and not hearsay_source_cn:
        raise V03ValidationError("INVALID_FIELDS", "hearsay requires a non-empty hearsay_source_cn")

    verified_in_person = _optional_bool(payload.get("verified_in_person"), "verified_in_person", default=False)
    verified_place_note = _optional_str(
        payload.get("verified_place_note"), "verified_place_note", max_len=240
    )
    if verified_in_person and not verified_place_note:
        raise V03ValidationError(
            "INVALID_FIELDS", "verified_in_person requires a non-empty verified_place_note"
        )
    if kind == "hearsay" and payload.get("verified_in_person") is None:
        # Heard from someone is not seen in person unless explicitly marked and justified.
        verified_in_person = False

    help_status = _optional_str(
        payload.get("subject_help_status"), "subject_help_status", max_len=24
    ).lower() or "none"
    if help_status not in SUBJECT_HELP_STATUSES:
        raise V03ValidationError(
            "INVALID_FIELDS",
            "subject_help_status must be none / suggested / adopted / moved_aside",
        )

    entry = {
        "entry_id": _allocate_id(state, "npc"),
        "npc_id": npc_id,
        "npc_label_cn": npc_label_cn,
        "place_id": place_id,
        "kind": kind,
        "summary_cn": summary_cn,
        "hearsay_source_cn": hearsay_source_cn,
        "verified_in_person": verified_in_person,
        "verified_place_note": verified_place_note,
        "subject_help_status": help_status,
        "updated_at": obs_iso,
    }
    state.setdefault("npc_errands_and_hearsay", []).append(entry)
    _trim_list(state, "npc_errands_and_hearsay", MAX_NPC_ENTRIES)


_SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "record_first_glance",
    "revisit_with_question",
    "record_nuanced_recognition",
    "open_or_use_shared_material",
    "record_npc_errand_or_hearsay",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "drop_shared_material",
    "withdraw_shared_material",
})

KNOWN_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | _SUBJECT_ONLY_ACTIONS


def apply_encounters_mutation(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """执行受控状态变更并原子落盘；``operation_key`` 同一 key 幂等重放。"""

    if actor_side not in ("partner", "subject"):
        raise V03PermissionError("FORBIDDEN_ACTOR_SIDE", f"Forbidden actor_side: {actor_side!r}")

    # The whole Read -> reconcile -> validate -> mutate -> save cycle is serialized;
    # zero-write projections never take this lock and stay lock-free.
    with _tx.state_file_lock(_state_file_path(root)):
        return _apply_encounters_mutation_locked(
            root, payload, actor_side=actor_side, now_iso=now_iso
        )


def _apply_encounters_mutation_locked(
    root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: Optional[str],
) -> tuple[dict[str, Any], str]:
    obs_iso = _now_iso_str(now_iso)
    _exists, state = _load_state_readonly(root)
    has_cap = actor_side == "subject"

    action = _require_str(payload.get("action"), "action", max_len=64)
    if action not in KNOWN_ACTIONS:
        raise V03ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")
    if action in _SUBJECT_ONLY_ACTIONS and actor_side != "subject":
        raise V03PermissionError(
            "FORBIDDEN_ACTOR_SIDE", f"Action {action!r} requires a verified subject capability"
        )

    op_key = _optional_str(payload.get("operation_key"), "operation_key", max_len=128)
    fp = _compute_payload_fingerprint(actor_side, payload)

    exp_rev: Optional[int] = None
    raw_rev = payload.get("expected_revision")
    if raw_rev is not None:
        if not _is_strict_int(raw_rev) or raw_rev < 0:
            raise V03ValidationError(
                "INVALID_FIELDS", "expected_revision must be a non-negative integer"
            )
        exp_rev = int(raw_rev)

    ledger = state.setdefault("operation_ledger", {})
    if op_key:
        # Guard pre-migration receipts that only carry ``fingerprint``: keep their
        # old conflict semantics instead of silently replaying a changed payload.
        legacy = ledger.get(op_key)
        if isinstance(legacy, Mapping) and not legacy.get("payload_hash"):
            legacy_fp = legacy.get("fingerprint")
            if legacy_fp and legacy_fp != fp:
                raise V03ConflictError(
                    "OPERATION_KEY_CONFLICT",
                    f"operation_key {op_key!r} already used with a different payload",
                )
        outcome, _detail = _tx.reconcile_evicted_or_active_op(
            root,
            state,
            op_key,
            dict_key="operation_ledger",
            expected_revision=exp_rev,
            payload_hash=fp,
        )
        if outcome == "conflict":
            raise V03ConflictError(
                "OPERATION_KEY_CONFLICT",
                f"operation_key {op_key!r} already used with a different payload",
            )
        if outcome == "expired":
            raise V03ConflictError(
                "RETRY_WINDOW_EXPIRED",
                f"operation_key {op_key!r} is older than the retained retry window",
            )
        if outcome in ("hit_full", "hit_evicted"):
            return project_v03_encounters(
                root, has_subject_capability=has_cap, now_iso=obs_iso
            ), "DUPLICATE"

    if exp_rev is not None and int(state.get("revision") or 0) != exp_rev:
        raise V03ConflictError(
            "REVISION_CONFLICT",
            f"Expected revision {exp_rev}, current revision is {state.get('revision')}",
        )

    if action == "record_first_glance":
        _handle_record_first_glance(state, payload, obs_iso)
    elif action == "revisit_with_question":
        _handle_revisit_with_question(state, payload, obs_iso)
    elif action == "record_nuanced_recognition":
        _handle_record_nuanced_recognition(state, payload, obs_iso)
    elif action == "drop_shared_material":
        _handle_drop_shared_material(state, payload, obs_iso)
    elif action == "withdraw_shared_material":
        _handle_withdraw_shared_material(state, payload, obs_iso)
    elif action == "open_or_use_shared_material":
        _handle_open_or_use_shared_material(state, payload, obs_iso)
    elif action == "record_npc_errand_or_hearsay":
        _handle_record_npc_errand_or_hearsay(state, payload, obs_iso)
    else:  # pragma: no cover - guarded above
        raise V03ValidationError("INVALID_FIELDS", f"Unknown action: {action!r}")

    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = obs_iso
    if op_key:
        _tx.stamp_receipt(
            state,
            op_key,
            {
                "fingerprint": fp,
                "action": action,
                "actor_side": actor_side,
                "status": "applied",
                "ok": True,
                "applied_at": obs_iso,
            },
            revision=state["revision"],
            dict_key="operation_ledger",
            payload_hash=fp,
            committed_at=obs_iso,
        )
        # Eviction follows submission order (``op_seq``), never alphabetical order.
        _tx.trim_receipts_by_op_seq(
            state,
            dict_key="operation_ledger",
            max_keep=MAX_OPERATION_LEDGER,
        )

    _atomic_write_state(root, state)
    return project_v03_encounters(root, has_subject_capability=has_cap, now_iso=obs_iso), "APPLIED"


__all__ = [
    "KNOWN_ACTIONS",
    "MATERIAL_MODES",
    "MATERIAL_STATUSES",
    "MATERIAL_USED_IN_WORK",
    "MATERIAL_WITHDRAWN",
    "MAX_ENCOUNTERS",
    "MAX_SHARED_MATERIALS",
    "MAX_NPC_ENTRIES",
    "NPC_KINDS",
    "PARTNER_ALLOWED_ACTIONS",
    "STAGES",
    "STATE_REL_PATH",
    "STATE_SCHEMA_VERSION",
    "SUBJECT_HELP_STATUSES",
    "V03ConflictError",
    "V03EncountersError",
    "V03NotFoundError",
    "V03PermissionError",
    "V03ValidationError",
    "VIEW_SCHEMA",
    "apply_encounters_mutation",
    "project_concern_driven_attention_zero_write",
    "project_v03_encounters",
]

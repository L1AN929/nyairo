#!/usr/bin/env python3
"""v0.4 第 45–53 章：弹性日常方向、无截止共享小本子、局部共同试验、分域教学与十分钟玩心底座。

核心不变量：
1. 100% 零大模型调用（``llm_calls == 0``），纯 Python 标准库确定性状态机；
2. 100% 读路径零磁盘写入（``read_stored_elastic_play_zero_write`` / ``project_v04_elastic_play`` /
   ``build_v04_elastic_turn_hint`` 在目录或文件缺失时绝不创建目录、临时文件或改变 mtime）；
3. 开源角色中立：动态读取 ``character_pack`` 的 ``display_name`` / ``pronouns``，回退 ``"TA"``；
4. 严格防剧透与私密隔离：
   - ``prepared_waiting`` 阶段的小惊喜在伴侣视角（``has_subject_capability=False``）完全隐去；
   - ``offered`` 阶段仅暴露温和邀请提示 ``invite_hint``，直到伴侣点击 ``reveal`` 才展示 ``title`` / ``content``；
   - ``private_two_phrasings``（两种说法私下草稿）仅对主体能力视角可见；
   - ``teach_scoped_preference`` 严格限定在选定沟通域，``applies_to_private_writing`` 恒为 ``False``；
   - 共享小本子旧修订并发追加时自动并排保留双分支，绝不静默覆盖任何一方字句。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:  # package import (``components.life.scripts``)
    from . import state_tx_guard as _tx
except ImportError:  # pragma: no cover - flat ``components/life/scripts`` import path
    import state_tx_guard as _tx  # type: ignore[no-redef]

STATE_SCHEMA = "nyairo.v04.elastic_play_state.v1"
VIEW_SCHEMA = "phone.v04.elastic_play.v1"
STATE_FILENAME = "v04_elastic_play.json"
DEFAULT_TZ = timezone(timedelta(hours=8))

#: 活动回执上限；被淘汰的行压入 ``committed_op_ring``，仍可按原结果幂等重放。
MAX_OPERATION_RECEIPTS = 128

ALLOWED_PREFERENCE_SCOPES: frozenset[str] = frozenset({
    "partner_explanation",
    "work_revision",
    "world_claim_reported",
})

ALLOWED_GAME_KINDS: frozenset[str] = frozenset({
    "odd_naming",
    "word_chain",
    "object_logbook",
})

ALLOWED_SURPRISE_KINDS: frozenset[str] = frozenset({
    "bookmark_note",
    "pressed_leaf_card",
    "margin_doodle",
    "tiny_keepsake",
    "reading_clipping",
})

#: Ch.45 §二–§七：她自己发起的迷你项目媒介。全部是纸面/文字小物，
#: 没有任何真实录音或物理造物能力；``sound_card`` 只是文字声景来源标注，
#: ``sketch_note`` 只是表达练习，绝不进入世界事实。
ALLOWED_MEDIUM_KINDS: frozenset[str] = frozenset({
    "place_booklet",
    "sound_card",
    "map_margin_note",
    "sketch_note",
})

ALLOWED_PROJECT_OPS: frozenset[str] = frozenset({
    "start",
    "add_field_note",
    "pause_not_feeling_it",
    "resume",
    "share_page",
})

#: 严格室内地点：不需要世界走访证据即可记录亲历笔记。
INDOOR_NOTE_PLACES: frozenset[str] = frozenset({
    "home",
    "home_yard",
    "study_desk",
    "home_desk",
})

MAX_PERSONAL_PROJECTS = 8
MAX_GROUNDED_ALTERNATIVES = 16
MAX_ADMIN_WORLD_EDITS = 16

FORBIDDEN_FOREIGN_AND_SCORE_KEYS: frozenset[str] = frozenset({
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
    "win_score",
    "lose_score",
    "quiz_grade",
    "streak_days",
    "obedience_level",
})


class V04ElasticPlayError(Exception):
    def __init__(
        self,
        status_code: int,
        reason_code: str,
        message: str = "",
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message or reason_code)
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        self.details = dict(details or {})


class V04ValidationError(V04ElasticPlayError):
    def __init__(
        self,
        reason_code: str = "INVALID_FIELDS",
        message: str = "",
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(400, reason_code, message, details=details)


class V04PermissionError(V04ElasticPlayError):
    def __init__(
        self,
        reason_code: str = "FORBIDDEN_ACTOR_SIDE",
        message: str = "",
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(403, reason_code, message, details=details)


class V04NotFoundError(V04ElasticPlayError):
    def __init__(
        self,
        reason_code: str = "NOT_FOUND",
        message: str = "",
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(404, reason_code, message, details=details)


class V04ConflictError(V04ElasticPlayError):
    def __init__(
        self,
        reason_code: str = "CONFLICT",
        message: str = "",
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(409, reason_code, message, details=details)


class V04UnavailableError(V04ElasticPlayError):
    def __init__(
        self,
        reason_code: str = "STATE_CORRUPTED",
        message: str = "",
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(503, reason_code, message, details=details)


def elastic_play_state_path(day_root: Path | str) -> Path:
    return Path(day_root) / "phone" / STATE_FILENAME


def parse_iso_moment(raw: Optional[str]) -> datetime:
    if isinstance(raw, str) and raw.strip():
        txt = raw.strip()
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(txt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=DEFAULT_TZ)
            return dt
        except ValueError:
            pass
    return datetime.now(tz=DEFAULT_TZ)


def format_wall_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt.isoformat(timespec="seconds")


def _check_no_symlinks_zero_write(day_root: Path) -> None:
    for candidate in (day_root, day_root / "phone", day_root / "phone" / STATE_FILENAME):
        try:
            st = os.lstat(candidate)
        except FileNotFoundError:
            continue
        except OSError:
            continue
        if os.path.samestat(st, st) and (st.st_mode & 0o170000) == 0o120000:
            raise V04PermissionError("SYMLINK_PATH_FORBIDDEN", f"Symlink refused: {candidate}")


def read_character_identity_zero_write(day_root: Path | str) -> dict[str, str]:
    root = Path(day_root)
    display_name = "TA"
    pronouns = "TA"
    for rel in ("phone/character_pack_state.json", "phone/character_pack.json", "character_pack.json"):
        cand = root / rel
        try:
            if not cand.exists() or cand.is_symlink() or not cand.is_file():
                continue
            doc = json.loads(cand.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(doc, Mapping):
            continue
        pack = (
            doc.get("pack")
            if isinstance(doc.get("pack"), Mapping)
            else (doc.get("character") if isinstance(doc.get("character"), Mapping) else doc)
        )
        dn = str(pack.get("display_name") or pack.get("name") or "").strip()
        pr = str(pack.get("pronouns") or pack.get("pronoun") or "").strip()
        if dn:
            display_name = dn[:32]
        if pr:
            pronouns = pr[:16]
        if dn or pr:
            break
    return {"display_name": display_name, "pronouns": pronouns}


def default_elastic_play_state(*, now_iso: Optional[str] = None) -> dict[str, Any]:
    ts = format_wall_iso(parse_iso_moment(now_iso))
    return {
        "schema": STATE_SCHEMA,
        "revision": 0,
        "updated_at": ts,
        "day_direction": {
            "direction_id": "",
            "direction": "",
            "optional_directions": [],
            "commitments": [],
            "current_focus": "",
            "interrupt_note": "",
            "completed_items": [],
            "status": "idle",
            "pre_scheduled": False,
            # Ch.49 一天可以有方向，但不应提前发生：这些字段只描述"方向走到哪一步"，
            # 绝不把早上的可选方向补填写成已经发生过的事，也绝不记亏欠。
            "direction_stage": "idle",
            "active_step_ref": "",
            "preserved_work_notes": [],
            "offline_gap_notice": "",
            "backfilled_to_diary": False,
            "guilt_debt": 0,
            "updated_at": ts,
        },
        "shared_notebook": {
            "head_revision": 0,
            "has_concurrent_branch": False,
            "entries": [],
            "branches": [],
        },
        "reading_notices": [],
        # Ch.50 §五: two people bring back different things they noticed on the
        # same page.  Each row is one person's own annotation; no comprehension
        # or affinity score is ever attached.
        "distinct_annotations": [],
        "experiments": [],
        "private_two_phrasings": [],
        "scoped_preferences": [],
        "practice_sessions": {
            "status": "idle",
            "topic": "",
            "current_prompt": "",
            "last_answer": "",
            "gentle_feedback": "",
            "history": [],
        },
        "temporary_game": {
            "active": False,
            "status": "idle",
            "game_id": "",
            "game_kind": "",
            "prop_label": "",
            "rule_summary": "",
            "rule": "",
            "started_at": "",
            "expires_at": "",
            "turns": [],
            "keepsakes": [],
            # Ch.53 §二: a fictional frame is a shared invention only. It never
            # mutates a real world object and never fabricates a real journey.
            "fictional_prop_object_id": "",
            "fictional_role_cn": "",
            "fantasy_scope_isolated": True,
            "world_object_mutated": False,
            "world_travel_fabricated": False,
        },
        "prepared_surprises": [],
        # Ch.45 §二–§七: autonomous mini-projects, grounded counter-proposals and
        # admin/world edits kept explicitly separate from the subject's own consent.
        "personal_projects": [],
        "grounded_alternatives": [],
        "admin_world_edits": [],
        "operation_receipts": {},
    }


def read_stored_elastic_play_zero_write(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], bool]:
    root = Path(day_root)
    _check_no_symlinks_zero_write(root)
    path = elastic_play_state_path(root)
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError:
        return default_elastic_play_state(now_iso=now_iso), False
    except OSError as exc:
        raise V04UnavailableError("STATE_IO_ERROR", str(exc)) from exc

    try:
        loaded = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise V04UnavailableError("STATE_CORRUPTED", f"Invalid JSON in {STATE_FILENAME}") from exc

    if not isinstance(loaded, Mapping) or loaded.get("schema") != STATE_SCHEMA:
        raise V04UnavailableError("STATE_CORRUPTED", f"Unexpected schema in {STATE_FILENAME}")

    base = default_elastic_play_state(now_iso=now_iso)
    for k, v in loaded.items():
        base[k] = copy.deepcopy(v)
    return base, True


def _atomic_write_state(day_root: Path | str, state: Mapping[str, Any]) -> None:
    root = Path(day_root)
    _check_no_symlinks_zero_write(root)
    phone_dir = root / "phone"
    phone_dir.mkdir(parents=True, exist_ok=True)
    if phone_dir.is_symlink():
        raise V04PermissionError("SYMLINK_PATH_FORBIDDEN", "phone directory cannot be a symlink")
    target = phone_dir / STATE_FILENAME
    if target.exists() and target.is_symlink():
        raise V04PermissionError("SYMLINK_PATH_FORBIDDEN", "state file cannot be a symlink")

    encoded = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".v04_elastic_", suffix=".tmp", dir=str(phone_dir))
    try:
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _clean_line(val: Any, *, max_len: int = 240, field_name: str = "field") -> str:
    if not isinstance(val, str):
        raise V04ValidationError("INVALID_FIELDS", f"{field_name} must be a string")
    txt = " ".join(val.replace("\r", " ").replace("\n", " ").split()).strip()
    if not txt:
        raise V04ValidationError("INVALID_FIELDS", f"{field_name} must not be blank")
    return txt[:max_len]


def _clean_multiline(val: Any, *, max_len: int = 600, field_name: str = "field") -> str:
    if not isinstance(val, str):
        raise V04ValidationError("INVALID_FIELDS", f"{field_name} must be a string")
    txt = val.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not txt:
        raise V04ValidationError("INVALID_FIELDS", f"{field_name} must not be blank")
    return txt[:max_len]


def _short_hash(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10]


def _slug_token(value: Any, *, fallback: str = "project") -> str:
    raw = str(value or "").strip().lower()
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in raw)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return (cleaned[:48] or f"{fallback}-{_short_hash(raw)}")


def _effective_temp_game(tg_raw: Mapping[str, Any], now_dt: datetime) -> dict[str, Any]:
    tg = copy.deepcopy(dict(tg_raw))
    if tg.get("active") and tg.get("expires_at"):
        exp_dt = parse_iso_moment(str(tg.get("expires_at")))
        if now_dt >= exp_dt:
            tg["active"] = False
            tg["status"] = "expired_naturally"
    return tg


def _place_rows(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [row for row in value.values() if isinstance(row, Mapping)]
    if isinstance(value, (list, tuple)):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _collect_visited_place_ids_zero_write(root: Path) -> set[str]:
    """Every place id with persisted firsthand/world evidence, read-only.

    Sources: the neighborhood living state (``knowledge_status == visited``),
    the universal-matter space (objects/traces anchored to a place), the phone
    outing store and living traces.  A missing or corrupt file contributes
    nothing -- this helper never creates a byte.
    """

    visited: set[str] = set()

    def add(value: Any) -> None:
        pid = str(value or "").strip()
        if pid:
            visited.add(pid)

    for rel in (
        "world/v04_neighborhood_state.json",
        "world/v04_neighborhood_living.json",
    ):
        doc = _read_optional_store_zero_write(root / rel)
        if not isinstance(doc, Mapping):
            continue
        raw_visited = doc.get("visited_place_ids")
        if isinstance(raw_visited, (list, tuple)):
            for pid in raw_visited:
                add(pid)
        for row in _place_rows(doc.get("places")):
            if str(row.get("knowledge_status") or "").strip().lower() == "visited":
                add(row.get("place_id") or row.get("location_id") or row.get("id"))
            else:
                try:
                    visits = int(row.get("visit_count") or 0)
                except (TypeError, ValueError):
                    visits = 0
                if row.get("last_visited_at") or visits > 0:
                    add(row.get("place_id") or row.get("location_id") or row.get("id"))

    for rel in ("world/v08_matter_space.json", "world/v08_universal_matter.json"):
        doc = _read_optional_store_zero_write(root / rel)
        if not isinstance(doc, Mapping):
            continue
        raw_visited = doc.get("visited_place_ids")
        if isinstance(raw_visited, (list, tuple)):
            for pid in raw_visited:
                add(pid)
        for row in _place_rows(doc.get("matter_objects") or doc.get("objects")):
            for key in ("place_id", "zone_id", "familiar_spot_zone_id"):
                add(row.get(key))

    outing = _read_optional_store_zero_write(root / "phone" / "outing_places.json")
    if isinstance(outing, Mapping):
        raw_visited = outing.get("visited_place_ids")
        if isinstance(raw_visited, (list, tuple)):
            for pid in raw_visited:
                add(pid)
        for key in ("places", "visits", "visited_places", "bench_visits"):
            for row in _place_rows(outing.get(key)):
                marker = str(
                    row.get("status") or row.get("state") or row.get("knowledge_status") or ""
                ).strip().lower()
                if (
                    row.get("visited") is True
                    or marker == "visited"
                    or row.get("last_visited_at")
                ):
                    add(row.get("place_id") or row.get("location_id") or row.get("id"))
        for row in _place_rows(outing):
            if str(row.get("knowledge_status") or "").strip().lower() == "visited":
                add(row.get("place_id"))

    traces = _read_optional_store_zero_write(root / "phone" / "v08_living_traces.json")
    if isinstance(traces, Mapping):
        for key in ("traces", "recent_acts", "routines"):
            for row in _place_rows(traces.get(key)):
                add(row.get("place_id"))

    return visited


def _project_personal_projects_zero_write(
    state: Mapping[str, Any], *, has_subject_capability: bool
) -> list[dict[str, Any]]:
    """Subject sees everything; partner sees only explicitly shared pages."""

    out: list[dict[str, Any]] = []
    for raw in state.get("personal_projects") or []:
        if not isinstance(raw, Mapping):
            continue
        project = copy.deepcopy(dict(raw))
        if has_subject_capability:
            out.append(project)
            continue
        entries = [
            copy.deepcopy(dict(entry))
            for entry in (project.get("entries") or [])
            if isinstance(entry, Mapping) and entry.get("shared") is True
        ]
        if not entries:
            # Never reveal that an unshared project exists at all.
            continue
        for entry in entries:
            if "private_raw_note_cn" in entry:
                entry["private_raw_note_cn"] = ""
                entry["raw_note_withheld"] = True
        project["entries"] = entries
        project["unshared_entry_count_withheld"] = True
        out.append(project)
    return out


def _project_distinct_annotations_zero_write(
    state: Mapping[str, Any], *, has_subject_capability: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Ch.50 §五: two people bring back different attention, side by side.

    Each side's own annotation row is never handed over before it is explicitly
    shared (``shared_with_partner``).  The partner perspective drops unshared
    rows entirely -- not even a count leaks.  Pair rows only combine annotations
    both people chose to share, and never carry a comprehension/affinity score.
    """

    visible: list[dict[str, Any]] = []
    for raw in state.get("distinct_annotations") or []:
        if not isinstance(raw, Mapping):
            continue
        row = copy.deepcopy(dict(raw))
        if not has_subject_capability and row.get("shared_with_partner") is not True:
            # Never preview the other person's unshared annotation.
            continue
        row["forced_divergence"] = False
        row["comprehension_score_exists"] = False
        visible.append(row)

    def _norm(value: Any) -> str:
        return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())

    def _side(row: Mapping[str, Any]) -> str:
        return "subject" if row.get("shared_by") == "subject" else "partner"

    groups: dict[tuple[str, int], dict[str, list[dict[str, Any]]]] = {}
    for row in visible:
        if row.get("shared_with_partner") is not True:
            continue  # pairs are built only from what both sides chose to share
        page_val = row.get("page")
        if isinstance(page_val, bool) or not isinstance(page_val, int):
            continue
        key = (str(row.get("material_key") or ""), int(page_val))
        groups.setdefault(key, {"subject": [], "partner": []})[_side(row)].append(row)

    pairs: list[dict[str, Any]] = []
    for (material_key, page), sides in sorted(groups.items()):
        subject_rows = sides["subject"]
        partner_rows = sides["partner"]
        for subject_row, partner_row in zip(subject_rows, partner_rows):
            subject_detail = _norm(subject_row.get("noticed_detail_cn"))
            partner_detail = _norm(partner_row.get("noticed_detail_cn"))
            subject_quote = _norm(subject_row.get("quote_excerpt_cn"))
            partner_quote = _norm(partner_row.get("quote_excerpt_cn"))
            same_spot = subject_detail == partner_detail or (
                bool(subject_quote) and subject_quote == partner_quote
            )
            pairs.append({
                "material_key": material_key,
                "material_title": str(
                    subject_row.get("material_title")
                    or partner_row.get("material_title")
                    or ""
                ),
                "page": page,
                "subject_annotation_id": str(subject_row.get("annotation_id") or ""),
                "partner_annotation_id": str(partner_row.get("annotation_id") or ""),
                "subject_noticed_detail_cn": str(subject_row.get("noticed_detail_cn") or ""),
                "partner_noticed_detail_cn": str(partner_row.get("noticed_detail_cn") or ""),
                "subject_quote_excerpt_cn": str(subject_row.get("quote_excerpt_cn") or ""),
                "partner_quote_excerpt_cn": str(partner_row.get("quote_excerpt_cn") or ""),
                # Honest either way: sometimes both land on the same line.
                "same_spot_resonance": bool(same_spot),
                "forced_divergence": False,
                "comprehension_score_exists": False,
            })
    return visible, pairs


def project_v04_elastic_play(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    now_dt = parse_iso_moment(now_iso)
    eff_now = format_wall_iso(now_dt)
    state, exists = read_stored_elastic_play_zero_write(day_root, now_iso=eff_now)
    identity = read_character_identity_zero_write(day_root)
    pronoun = identity["pronouns"]

    tg_proj = _effective_temp_game(state.get("temporary_game") or {}, now_dt)

    # Ch.53 §二 Fantasy-Frame Isolation.  A scene where the mug "sails across a
    # desk nebula" is shared fiction only: the projection always reasserts the
    # isolation flags, even on legacy rows written before these fields existed.
    tg_proj["fantasy_scope_isolated"] = True
    tg_proj["world_object_mutated"] = False
    tg_proj["world_travel_fabricated"] = False
    for turn_row in tg_proj.get("turns") or []:
        if isinstance(turn_row, Mapping):
            turn_row["fantasy_scope_isolated"] = True
            turn_row["world_object_mutated"] = False
            turn_row["world_travel_fabricated"] = False
    for keepsake_row in tg_proj.get("keepsakes") or []:
        if isinstance(keepsake_row, Mapping):
            keepsake_row["fantasy_scope_isolated"] = True
            keepsake_row["world_object_mutated"] = False
            keepsake_row["world_travel_fabricated"] = False
    tg_proj["fantasy_boundary_note_cn"] = (
        "假想设定只在这次十分钟小游戏里有效，屋里的真物件保持原样"
        if str(tg_proj.get("fictional_role_cn") or "").strip()
        else ""
    )

    # Ch.52 §五: unresolved divergence memos stay bound to their work and never
    # nag on ordinary days (``nag_daily=False``) or cost affection.
    unresolved_bound_divergences: list[dict[str, Any]] = []
    for raw_exp in state.get("experiments") or []:
        if not isinstance(raw_exp, Mapping):
            continue
        if str(raw_exp.get("status") or "") != "kept_both_unreplaced":
            continue
        memo = str(raw_exp.get("divergence_memo_cn") or raw_exp.get("outcome") or "")
        unresolved_bound_divergences.append({
            "experiment_id": str(raw_exp.get("experiment_id") or ""),
            "target_work_ref": str(raw_exp.get("target_work_ref") or ""),
            "divergence_memo_cn": memo,
            "outcome": memo,
            "nag_daily": False,
            "affection_penalty": 0,
            "reopen_trigger_work_ref": str(raw_exp.get("reopen_trigger_work_ref") or ""),
        })

    visible_surprises: list[dict[str, Any]] = []
    hidden_waiting_count = 0
    for raw_sp in state.get("prepared_surprises") or []:
        if not isinstance(raw_sp, Mapping):
            continue
        sp_status = str(raw_sp.get("status") or "").lower()
        if sp_status == "prepared_waiting":
            if has_subject_capability:
                visible_surprises.append(copy.deepcopy(dict(raw_sp)))
            else:
                hidden_waiting_count += 1
        elif sp_status == "offered":
            if has_subject_capability:
                visible_surprises.append(copy.deepcopy(dict(raw_sp)))
            else:
                # Anti-spoiler: partner only sees invitation metadata until revealed!
                visible_surprises.append({
                    "surprise_id": str(raw_sp.get("surprise_id") or ""),
                    "kind": str(raw_sp.get("kind") or "tiny_keepsake"),
                    "status": "offered",
                    "invite_hint": str(
                        raw_sp.get("invite_hint")
                        or f"{pronoun}留了一件小东西想拿给你看，现在有空拆开吗？"
                    ),
                    "offered_at": raw_sp.get("offered_at") or eff_now,
                    "deferred_count": int(raw_sp.get("deferred_count") or 0),
                    "content_withheld_until_reveal": True,
                })
        elif sp_status == "revealed":
            visible_surprises.append(copy.deepcopy(dict(raw_sp)))

    dd_proj = copy.deepcopy(dict(state.get("day_direction") or {}))
    dd_proj["pre_scheduled"] = False
    # Legacy rows written before these fields existed stay readable.
    dd_proj.setdefault("direction_stage", "idle")
    dd_proj.setdefault("active_step_ref", "")
    dd_proj.setdefault("preserved_work_notes", [])
    dd_proj.setdefault("offline_gap_notice", "")
    # A morning intention is never projected as something that already happened.
    dd_proj["backfilled_to_diary"] = False
    dd_proj["guilt_debt"] = 0
    raw_preserved_notes = [
        copy.deepcopy(dict(row))
        for row in (dd_proj.get("preserved_work_notes") or [])
        if isinstance(row, Mapping)
    ]
    if has_subject_capability:
        dd_proj["preserved_work_notes"] = raw_preserved_notes
        dd_proj["preserved_work_notes_withheld"] = False
    else:
        shared_notes: list[dict[str, Any]] = []
        had_private_notes = False
        for note_row in raw_preserved_notes:
            note_author = str(
                note_row.get("author") or note_row.get("actor") or ""
            ).strip().lower()
            is_shared = (
                note_row.get("shared") is True
                or note_row.get("shared_with_partner") is True
                or note_author == "partner"
            )
            if is_shared:
                shared_notes.append(note_row)
            else:
                had_private_notes = True
        dd_proj["preserved_work_notes"] = shared_notes
        dd_proj["preserved_work_notes_withheld"] = bool(had_private_notes)
    # Legacy rows written before true-receipt verification stay readable and are
    # honestly marked unverified -- never deleted, never auto-promoted.
    completed_proj: list[dict[str, Any]] = []
    for raw_item in dd_proj.get("completed_items") or []:
        if not isinstance(raw_item, Mapping):
            continue
        item = copy.deepcopy(dict(raw_item))
        if "verified" not in item:
            item["verified"] = False
            item["verification_status"] = "legacy_unverified"
        else:
            item.setdefault(
                "verification_status", "verified" if item.get("verified") else "legacy_unverified"
            )
        completed_proj.append(item)
    dd_proj["completed_items"] = completed_proj

    distinct_annotations, distinct_annotation_pairs = _project_distinct_annotations_zero_write(
        state, has_subject_capability=has_subject_capability
    )

    out: dict[str, Any] = {
        "schema": VIEW_SCHEMA,
        "state_exists": bool(exists),
        "perspective": "subject" if has_subject_capability else "partner",
        "revision": int(state.get("revision") or 0),
        "observed_at": eff_now,
        "identity": identity,
        "day_direction": dd_proj,
        "shared_notebook": copy.deepcopy(dict(state.get("shared_notebook") or {})),
        "reading_notices": copy.deepcopy(list(state.get("reading_notices") or [])),
        "distinct_annotations": distinct_annotations,
        "distinct_annotation_pairs": distinct_annotation_pairs,
        "experiments": copy.deepcopy(list(state.get("experiments") or [])),
        "scoped_preferences": copy.deepcopy(list(state.get("scoped_preferences") or [])),
        "practice_sessions": copy.deepcopy(dict(state.get("practice_sessions") or {})),
        "temporary_game": tg_proj,
        "game_keepsakes": copy.deepcopy(list(tg_proj.get("keepsakes") or [])),
        "unresolved_bound_divergences": {
            "count": len(unresolved_bound_divergences),
            "rows": unresolved_bound_divergences,
            "nag_daily": False,
            "affection_penalty": 0,
        },
        "prepared_surprises": visible_surprises,
        "personal_projects": _project_personal_projects_zero_write(
            state, has_subject_capability=has_subject_capability
        ),
        "grounded_alternatives": copy.deepcopy(list(state.get("grounded_alternatives") or [])),
        "admin_world_edits": copy.deepcopy(list(state.get("admin_world_edits") or [])),
        "llm_calls": 0,
    }
    if has_subject_capability:
        out["private_two_phrasings"] = copy.deepcopy(list(state.get("private_two_phrasings") or []))
        out["private_two_phrasings_withheld"] = False
        out["hidden_waiting_surprise_count"] = hidden_waiting_count
    else:
        out["private_two_phrasings"] = []
        out["private_two_phrasings_withheld"] = True
    return out


def project_divergence_for_work_zero_write(
    day_root: Path | str,
    work_ref: str,
) -> list[dict[str, Any]]:
    """Unresolved divergence memos bound to one specific work, read-only.

    Ch.52 §五: "原版更含蓄，改版更清楚，先不替换" is bound to the work it came
    from.  It surfaces again only when that exact work is reopened; ordinary
    days and unrelated works get an empty list.  This helper never writes a byte.
    """

    ref = str(work_ref or "").strip()
    if not ref:
        return []
    state, _ = read_stored_elastic_play_zero_write(day_root)
    rows: list[dict[str, Any]] = []
    for raw_exp in state.get("experiments") or []:
        if not isinstance(raw_exp, Mapping):
            continue
        if str(raw_exp.get("target_work_ref") or "") != ref:
            continue
        if str(raw_exp.get("status") or "") != "kept_both_unreplaced":
            continue
        memo = str(raw_exp.get("divergence_memo_cn") or raw_exp.get("outcome") or "")
        rows.append({
            "experiment_id": str(raw_exp.get("experiment_id") or ""),
            "target_work_ref": ref,
            "status": "kept_both_unreplaced",
            "divergence_memo_cn": memo,
            "outcome": memo,
            "title": str(raw_exp.get("title") or ""),
            "question": str(raw_exp.get("question") or ""),
            "nag_daily": False,
            "affection_penalty": 0,
            "reopen_trigger_work_ref": str(raw_exp.get("reopen_trigger_work_ref") or ""),
            "paused_at": str(raw_exp.get("updated_at") or ""),
            "llm_calls": 0,
        })
    return rows


def project_surprise_readiness_zero_write(day_root: Path | str) -> dict[str, Any]:
    """Ch.53 §二: subject-side daily scan of surprises, strictly zero-write.

    Reports booleans and IDs only -- never a title, content or hint -- so the
    anti-spoiler rule holds even when this row feeds a daily report.  A missing
    day directory or state file yields all-false and creates no byte.
    """

    state, _ = read_stored_elastic_play_zero_write(day_root)
    prepared_waiting_ids: list[str] = []
    offered_unopened_ids: list[str] = []
    for raw_sp in state.get("prepared_surprises") or []:
        if not isinstance(raw_sp, Mapping):
            continue
        sid = str(raw_sp.get("surprise_id") or "")
        status = str(raw_sp.get("status") or "").lower()
        if status == "prepared_waiting":
            prepared_waiting_ids.append(sid)
        elif status == "offered":
            offered_unopened_ids.append(sid)
    return {
        "has_prepared_waiting": bool(prepared_waiting_ids),
        "prepared_waiting_ids": prepared_waiting_ids,
        "has_offered_unopened": bool(offered_unopened_ids),
        "offered_unopened_ids": offered_unopened_ids,
        "subject_side_only": True,
        "reveals_content": False,
        "llm_calls": 0,
    }


def build_v04_elastic_turn_hint(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> str:
    try:
        proj = project_v04_elastic_play(day_root, has_subject_capability=True, now_iso=now_iso)
    except Exception:
        return ""
    parts: list[str] = []
    dd = proj.get("day_direction") or {}
    if dd.get("direction") and dd.get("status") not in ("idle", "cleared", "dropped"):
        parts.append(f"今日弹性方向:{dd['direction']}")
    tg = proj.get("temporary_game") or {}
    if tg.get("active"):
        parts.append(f"十分钟小游戏:{tg.get('game_kind')}")
    return "；".join(parts)[:160]


# ---------------------------------------------------------------------------------------------
# True completion receipt verification (zero-write, read-only)
# ---------------------------------------------------------------------------------------------

_ID_KEY_HINTS = (
    "object_id",
    "object_ids",
    "source_object_ids",
    "target_object_ids",
    "target_object_id",
    "item_id",
    "card_id",
    "container_object_id",
    "partner_object_id",
    "trace_id",
    "act_id",
)
_ZONE_KEY_HINTS = ("zone_id", "zone_ids", "place_id", "target_zone", "target_zone_id")

_DRINK_FOOD_TOKENS: tuple[str, ...] = (
    "drink", "sip", "eat", "hydrate", "brew", "pour", "tea", "coffee", "soup",
    "cup", "bowl", "water", "喝", "饮", "吃", "茶", "咖啡",
    "汤", "杯", "碗", "水", "煮",
)
_READ_CRAFT_TOKENS: tuple[str, ...] = (
    "bookmark", "read", "book", "page", "organize", "write", "note", "card",
    "craft", "fold", "cut", "glue", "dry", "plant", "tidy", "sort",
    "书签", "读", "书", "页", "整理", "写", "笔记",
    "卡片", "折", "剪", "粘", "晾", "浇", "收纳",
    "摆", "手作",
)
_DOMAIN_TOKENS: dict[str, tuple[str, ...]] = {
    "drink_food": _DRINK_FOOD_TOKENS,
    "read_craft": _READ_CRAFT_TOKENS,
}
_FAILURE_MARKERS: tuple[str, ...] = (
    "fail", "refus", "reject", "denied", "blocked", "cancel", "error",
    "pending", "interrupt", "in_progress", "in-progress", "step_refused",
)
_SUCCESS_STATUSES: frozenset[str] = frozenset({
    "applied", "completed", "complete", "ok", "done", "success", "worked", "verified",
})


def _read_optional_store_zero_write(path: Path) -> Optional[dict[str, Any]]:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return dict(doc) if isinstance(doc, Mapping) else None


def _collect_ids(value: Any, keys: tuple[str, ...], *, depth: int = 0) -> set[str]:
    found: set[str] = set()
    if depth > 4:
        return found
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            if key in keys:
                if isinstance(item, str) and item.strip():
                    found.add(item.strip())
                elif isinstance(item, (list, tuple)):
                    for entry in item:
                        if isinstance(entry, str) and entry.strip():
                            found.add(entry.strip())
            found |= _collect_ids(item, keys, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= _collect_ids(item, keys, depth=depth + 1)
    return found


def _candidate(
    *,
    source: str,
    ref: str,
    row: Mapping[str, Any],
    action: str = "",
    at: str = "",
    key_ids: Sequence[str] = (),
) -> dict[str, Any]:
    status = str(row.get("status") or "")
    ok_value = row.get("ok")
    actor = str(
        row.get("actor_side")
        or row.get("author_side")
        or row.get("actor")
        or row.get("created_by")
        or ""
    )
    object_ids = _collect_ids(row, _ID_KEY_HINTS)
    zone_ids = _collect_ids(row, _ZONE_KEY_HINTS)
    labels = " ".join(
        str(row.get(field) or "")
        for field in ("action", "object_label_cn", "label_cn", "title_cn", "summary", "material_title")
    )
    ids = {str(ref)} | {str(item) for item in key_ids if str(item)}
    ids |= object_ids | zone_ids
    return {
        "source": source,
        "ref": str(ref),
        "row": dict(row),
        "status": status,
        "ok": ok_value if isinstance(ok_value, bool) else None,
        "actor": actor,
        "action": str(action or row.get("action") or ""),
        "at": str(at or row.get("applied_at") or row.get("committed_at") or row.get("at") or ""),
        "object_ids": object_ids,
        "zone_ids": zone_ids,
        "domain_text": " ".join(
            (str(action or row.get("action") or ""), labels, " ".join(sorted(object_ids)))
        ),
        "ids": ids,
    }


def _receipt_candidates_zero_write(root: Path | str) -> list[dict[str, Any]]:
    """Collect every persisted receipt-like row that may back a ``receipt_ref``."""

    base = Path(root)
    out: list[dict[str, Any]] = []

    matter = _read_optional_store_zero_write(base / "world" / "v08_matter_space.json")
    if matter:
        ops = matter.get("operations")
        if isinstance(ops, Mapping):
            for key, raw in ops.items():
                if not isinstance(raw, Mapping):
                    continue
                receipt = raw.get("receipt") if isinstance(raw.get("receipt"), Mapping) else {}
                merged = dict(receipt)
                merged.setdefault("actor_side", raw.get("actor_side"))
                out.append(_candidate(
                    source="world/v08_matter_space.json",
                    ref=str(key),
                    row=merged,
                    action=str(merged.get("action") or ""),
                    key_ids=(str(key),),
                ))
        for index, raw in enumerate(matter.get("recent_acts") or []):
            if not isinstance(raw, Mapping):
                continue
            ref = str(raw.get("act_id") or raw.get("operation_key") or f"act_{index}")
            out.append(_candidate(
                source="world/v08_matter_space.json#recent_acts",
                ref=ref,
                row=raw,
                key_ids=(ref,),
            ))
        objects = matter.get("matter_objects")
        if isinstance(objects, Mapping):
            for oid, raw in objects.items():
                if not isinstance(raw, Mapping):
                    continue
                row = dict(raw)
                row.setdefault("status", "applied")
                row.setdefault("action", "matter_object")
                out.append(_candidate(
                    source="world/v08_matter_space.json#objects",
                    ref=str(oid),
                    row=row,
                    key_ids=(str(oid),),
                ))

    neighborhood = _read_optional_store_zero_write(base / "world" / "v04_neighborhood_state.json")
    if neighborhood:
        receipts = neighborhood.get("operation_receipts")
        if isinstance(receipts, Mapping):
            for key, raw in receipts.items():
                if not isinstance(raw, Mapping):
                    continue
                result = raw.get("result") if isinstance(raw.get("result"), Mapping) else {}
                merged = dict(result)
                merged["action"] = raw.get("action")
                merged["actor_side"] = raw.get("actor_side")
                out.append(_candidate(
                    source="world/v04_neighborhood_state.json#operation_receipts",
                    ref=str(key),
                    row=merged,
                    action=str(raw.get("action") or ""),
                    at=str(raw.get("applied_at") or ""),
                    key_ids=(str(key),),
                ))
        craft = neighborhood.get("craft_bench")
        cards = craft.get("cards") if isinstance(craft, Mapping) else None
        if isinstance(cards, Mapping):
            for cid, raw in cards.items():
                if not isinstance(raw, Mapping):
                    continue
                row = dict(raw)
                row.setdefault("status", "applied")
                row.setdefault("action", "craft_card")
                out.append(_candidate(
                    source="world/v04_neighborhood_state.json#craft_bench.cards",
                    ref=str(cid),
                    row=row,
                    key_ids=(str(cid),),
                ))
        drying = neighborhood.get("drying_items")
        if isinstance(drying, Mapping):
            for iid, raw in drying.items():
                if not isinstance(raw, Mapping):
                    continue
                row = dict(raw)
                row.setdefault("status", "applied")
                row.setdefault("action", "hang_or_collect_cloth")
                out.append(_candidate(
                    source="world/v04_neighborhood_state.json#drying_items",
                    ref=str(iid),
                    row=row,
                    key_ids=(str(iid),),
                ))

    living = _read_optional_store_zero_write(base / "phone" / "v08_living_traces.json")
    if living:
        ops = living.get("operations")
        if isinstance(ops, Mapping):
            for key, raw in ops.items():
                if not isinstance(raw, Mapping):
                    continue
                receipt = raw.get("receipt") if isinstance(raw.get("receipt"), Mapping) else {}
                out.append(_candidate(
                    source="phone/v08_living_traces.json#operations",
                    ref=str(key),
                    row=dict(receipt),
                    action=str(receipt.get("action") or ""),
                    at=str(receipt.get("at") or ""),
                    key_ids=(str(key),),
                ))
        for index, raw in enumerate(living.get("traces") or []):
            if not isinstance(raw, Mapping):
                continue
            tid = str(raw.get("trace_id") or f"trace_{index}")
            out.append(_candidate(
                source="phone/v08_living_traces.json#traces",
                ref=tid,
                row=dict(raw),
                action="living_trace",
                key_ids=(tid,),
            ))

    micro = _read_optional_store_zero_write(base / "world" / "v08_microzone_state.json")
    if micro:
        ops = micro.get("operations")
        if isinstance(ops, Mapping):
            for key, raw in ops.items():
                if not isinstance(raw, Mapping):
                    continue
                receipt = raw.get("receipt") if isinstance(raw.get("receipt"), Mapping) else raw
                out.append(_candidate(
                    source="world/v08_microzone_state.json#operations",
                    ref=str(key),
                    row=dict(receipt),
                    action=str(receipt.get("action") or ""),
                    key_ids=(str(key),),
                ))

    journal = base / "phone" / "operations.jsonl"
    try:
        if journal.is_file() and not journal.is_symlink():
            with journal.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(raw, Mapping):
                        continue
                    key = str(raw.get("operation_key") or "")
                    if not key:
                        continue
                    out.append(_candidate(
                        source="phone/operations.jsonl",
                        ref=key,
                        row=dict(raw),
                        action=str(raw.get("action") or ""),
                        at=str(raw.get("at") or ""),
                        key_ids=(key,),
                    ))
    except OSError:
        pass
    return out


def _normalize_actor(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ("user", "subject", "owner", "her", "self"):
        return "subject"
    if text in ("partner", "other"):
        return "partner"
    return text


def _candidate_status(candidate: Mapping[str, Any]) -> str:
    status = str(candidate.get("status") or "").strip().lower()
    ok_value = candidate.get("ok")
    if ok_value is False:
        return "failed"
    if any(marker in status for marker in _FAILURE_MARKERS):
        return "failed"
    if status in _SUCCESS_STATUSES or ok_value is True:
        return "completed"
    if not status:
        return "completed"
    return "unknown"


def _domain_families(text: Any) -> set[str]:
    lowered = str(text or "").lower()
    families: set[str] = set()
    for family, tokens in _DOMAIN_TOKENS.items():
        if any(token in lowered for token in tokens):
            families.add(family)
    return families


def _candidate_is_shared(candidate: Mapping[str, Any]) -> bool:
    row = candidate.get("row") or {}
    return (
        str(row.get("visibility") or "") == "shared"
        or row.get("shared") is True
        or row.get("shared_with_partner") is True
        or row.get("joint") is True
    )


def verify_completion_receipt_readonly(
    root: Path | str,
    receipt_ref: str,
    *,
    actor_side: str,
    linked_object_ids: Sequence[str] = (),
    linked_zone_id: str = "",
    target_hint: str = "",
) -> dict[str, Any]:
    """Verify a completion receipt against real persisted stores, read-only.

    Five checks: exists, completed status, actor authority, object/action match and
    a bounded evidence projection.  Any unverifiable claim raises
    :class:`V04ConflictError` instead of letting a summary assert an act that never
    happened.  Nothing is written here.
    """

    ref = str(receipt_ref or "").strip()
    wanted_actor = _normalize_actor(actor_side)
    if not ref:
        raise V04ConflictError("INVALID_COMPLETION_RECEIPT", "receipt_ref must not be blank")

    matches = [row for row in _receipt_candidates_zero_write(root) if ref in row["ids"]]
    if not matches:
        raise V04ConflictError(
            "INVALID_COMPLETION_RECEIPT",
            f"receipt_ref {ref!r} does not match any persisted operation, act, card or item",
        )

    completed = [row for row in matches if _candidate_status(row) == "completed"]
    if not completed:
        raise V04ConflictError(
            "RECEIPT_NOT_COMPLETED",
            f"receipt_ref {ref!r} exists but is not in a completed/applied state",
        )

    authorized: list[dict[str, Any]] = []
    saw_actor_mismatch = False
    for candidate in completed:
        actor = _normalize_actor(candidate.get("actor"))
        if actor and actor != wanted_actor and not _candidate_is_shared(candidate):
            saw_actor_mismatch = True
            continue
        authorized.append(candidate)
    if not authorized:
        if saw_actor_mismatch:
            raise V04ConflictError(
                "RECEIPT_ACTOR_MISMATCH",
                f"receipt_ref {ref!r} belongs to the other side and is not a shared act",
            )
        raise V04ConflictError("INVALID_COMPLETION_RECEIPT", f"receipt_ref {ref!r} is unusable")

    linked_ids = {str(item).strip() for item in (linked_object_ids or ()) if str(item).strip()}
    zone_id = str(linked_zone_id or "").strip()
    target_families = _domain_families(
        " ".join([str(target_hint or ""), " ".join(sorted(linked_ids)), zone_id])
    )

    target_mismatch = False
    for candidate in authorized:
        object_ids = set(candidate.get("object_ids") or set())
        zone_ids = set(candidate.get("zone_ids") or set())
        if linked_ids and object_ids and not (object_ids & linked_ids):
            target_mismatch = True
            continue
        if zone_id and zone_ids and zone_id not in zone_ids:
            target_mismatch = True
            continue
        candidate_families = _domain_families(candidate.get("domain_text"))
        if target_families and candidate_families and not (target_families & candidate_families):
            target_mismatch = True
            continue
        return {
            "ok": True,
            "verified": True,
            "verification_status": "verified",
            "receipt_ref": ref,
            "verified_source": str(candidate.get("source") or ""),
            "verified_action": str(candidate.get("action") or ""),
            "verified_at": str(candidate.get("at") or ""),
            "actor_side": wanted_actor,
            "target_match": "ok",
            "llm_calls": 0,
        }

    if target_mismatch:
        raise V04ConflictError(
            "COMPLETION_TARGET_MISMATCH",
            f"receipt_ref {ref!r} targets a different object/action than the claimed completion",
        )
    raise V04ConflictError("INVALID_COMPLETION_RECEIPT", f"receipt_ref {ref!r} is unusable")


def apply_elastic_play_mutation(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """Serialized write entry point: the whole RMW cycle runs under the file lock."""

    if actor_side not in ("partner", "subject"):
        raise V04PermissionError("FORBIDDEN_ACTOR_SIDE", f"Invalid actor_side: {actor_side!r}")

    # The lock is only for write transactions; zero-write projections never enter here.
    with _tx.state_file_lock(elastic_play_state_path(day_root)):
        return _apply_elastic_play_mutation_locked(
            day_root, payload, actor_side=actor_side, now_iso=now_iso
        )


def _apply_elastic_play_mutation_locked(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    now_dt = parse_iso_moment(now_iso)
    eff_now = format_wall_iso(now_dt)
    state, _ = read_stored_elastic_play_zero_write(day_root, now_iso=eff_now)
    identity = read_character_identity_zero_write(day_root)
    pronoun = identity["pronouns"]

    action = str(payload.get("action") or "").strip()
    op_key = str(payload.get("operation_key") or "").strip()
    intent_body = {
        k: v
        for k, v in payload.items()
        if k not in ("operation_key", "intent_fingerprint", "expected_revision")
    }
    fp = hashlib.sha256(
        json.dumps({"actor_side": actor_side, "intent": intent_body}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:24]

    exp_rev: Optional[int] = None
    if "expected_revision" in payload and payload.get("expected_revision") is not None:
        raw_rev = payload.get("expected_revision")
        if isinstance(raw_rev, bool) or not isinstance(raw_rev, int) or raw_rev < 0:
            raise V04ValidationError("INVALID_FIELDS", "expected_revision must be non-negative int")
        exp_rev = int(raw_rev)

    if op_key:
        outcome, detail = _tx.reconcile_evicted_or_active_op(
            day_root,
            state,
            op_key,
            dict_key="operation_receipts",
            expected_revision=exp_rev,
            payload_hash=fp,
        )
        if outcome == "conflict":
            raise V04ConflictError(
                "OPERATION_KEY_CONFLICT",
                f"operation_key {op_key!r} reused with different payload",
            )
        if outcome == "expired":
            raise V04ConflictError(
                "RETRY_WINDOW_EXPIRED",
                f"operation_key {op_key!r} is older than the retained retry window",
                details=detail or {},
            )
        if outcome in ("hit_full", "hit_evicted"):
            return project_v04_elastic_play(
                day_root,
                has_subject_capability=(actor_side == "subject"),
                now_iso=eff_now,
            ), "DUPLICATE"

    if exp_rev is not None and int(state.get("revision") or 0) != exp_rev:
        raise V04ConflictError(
            "REVISION_CONFLICT",
            f"Expected revision {exp_rev}, current revision is {state.get('revision')}",
        )

    next_rev = int(state.get("revision") or 0) + 1

    # 1. Subject-only: Elastic Day Direction (Ch. 49)
    if action == "set_day_direction":
        if actor_side != "subject":
            raise V04PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can set elastic day direction")
        dd = state["day_direction"]
        # Legacy day_direction rows may predate these Ch.49 bookkeeping keys.
        dd.setdefault("direction_stage", "idle")
        dd.setdefault("active_step_ref", "")
        dd.setdefault("preserved_work_notes", [])
        dd.setdefault("offline_gap_notice", "")
        dd.setdefault("backfilled_to_diary", False)
        dd.setdefault("guilt_debt", 0)
        mode = str(payload.get("mode") or "").strip().lower()
        if not mode:
            if "completed_summary" in payload or "receipt_ref" in payload:
                mode = "record_completed"
            elif "interrupt_reason" in payload:
                mode = "interrupt"
            elif "clear" in payload and payload.get("clear") is True:
                mode = "clear"
            elif isinstance(payload.get("active_step_ref"), str) and payload["active_step_ref"].strip():
                mode = "start_step"
            elif "offline_gap_hours" in payload or (
                isinstance(payload.get("offline_gap_note"), str) and payload["offline_gap_note"].strip()
            ):
                mode = "offline_pause"
            else:
                mode = "set"

        if mode == "record_completed":
            summary = _clean_line(payload.get("completed_summary"), max_len=160, field_name="completed_summary")
            rec_ref_raw = payload.get("receipt_ref")
            if not isinstance(rec_ref_raw, str) or not rec_ref_raw.strip():
                raise V04ConflictError(
                    "MISSING_COMPLETION_RECEIPT",
                    "Recording a completed direction item requires a real receipt_ref",
                )
            rec_ref = rec_ref_raw.strip()[:96]
            hint = ""
            for key in ("direction_hint", "direction", "theme", "title", "direction_text"):
                if isinstance(payload.get(key), str) and str(payload.get(key)).strip():
                    hint = str(payload.get(key)).strip()
                    break
            if not hint:
                hint = str(dd.get("direction") or dd.get("current_focus") or "")
            raw_linked = payload.get("linked_object_ids")
            verification = verify_completion_receipt_readonly(
                day_root,
                rec_ref,
                actor_side=actor_side,
                linked_object_ids=(
                    tuple(str(item) for item in raw_linked)
                    if isinstance(raw_linked, (list, tuple))
                    else ()
                ),
                linked_zone_id=str(payload.get("linked_zone_id") or ""),
                target_hint=hint,
            )
            done_list = list(dd.get("completed_items") or [])
            done_list.append({
                "summary": summary,
                "receipt_ref": rec_ref,
                "completed_at": eff_now,
                "verified": bool(verification.get("verified")),
                "verification_status": str(verification.get("verification_status") or "verified"),
                "verified_source": str(verification.get("verified_source") or ""),
                "verified_action": str(verification.get("verified_action") or ""),
                "verified_at": str(verification.get("verified_at") or ""),
            })
            dd["completed_items"] = done_list[-16:]
            dd["status"] = "active"
        elif mode == "interrupt":
            reason = _clean_line(payload.get("interrupt_reason"), max_len=160, field_name="interrupt_reason")
            dd["interrupt_note"] = reason
            dd["status"] = "yielded_naturally"
            # 打断不是失败：草稿留着，可选方向也留着，不记亏欠，不补写成已发生。
            dd["direction_stage"] = "interrupted_preserved"
            dd["guilt_debt"] = 0
            dd["backfilled_to_diary"] = False
            raw_draft = payload.get("preserve_draft_note")
            if isinstance(raw_draft, str) and raw_draft.strip():
                draft = _clean_multiline(
                    raw_draft, max_len=600, field_name="preserve_draft_note"
                )
                notes = list(dd.get("preserved_work_notes") or [])
                note_entry: dict[str, Any] = {
                    "note": draft,
                    "saved_at": eff_now,
                    "stage": "interrupted",
                }
                if payload.get("shared") is True or payload.get("shared_with_partner") is True:
                    note_entry["shared"] = True
                notes.append(note_entry)
                dd["preserved_work_notes"] = notes[-8:]
        elif mode == "start_step":
            raw_step = payload.get("active_step_ref")
            step_ref = (
                _clean_line(raw_step, max_len=120, field_name="active_step_ref")
                if isinstance(raw_step, str) and raw_step.strip()
                else ""
            )
            dd["active_step_ref"] = step_ref
            if isinstance(payload.get("current_focus"), str) and payload["current_focus"].strip():
                focus = _clean_line(payload["current_focus"], max_len=120, field_name="current_focus")
            elif step_ref:
                focus = step_ref
            elif isinstance(payload.get("direction"), str) and payload["direction"].strip():
                focus = _clean_line(payload["direction"], max_len=140, field_name="direction")
            else:
                focus = ""
            if focus:
                dd["current_focus"] = focus
            # 只是进入当下一步：先不写 completed_items，也不消耗 optional_directions。
            dd["status"] = "active"
            dd["direction_stage"] = "in_progress"
            dd["backfilled_to_diary"] = False
            dd["guilt_debt"] = 0
        elif mode == "offline_pause":
            raw_hours = payload.get("offline_gap_hours")
            if raw_hours is not None:
                if (
                    isinstance(raw_hours, bool)
                    or not isinstance(raw_hours, (int, float))
                    or raw_hours < 0
                ):
                    raise V04ValidationError(
                        "INVALID_FIELDS", "offline_gap_hours must be a non-negative number"
                    )
                dd["offline_gap_hours"] = round(float(raw_hours), 2)
            raw_gap_note = payload.get("offline_gap_note")
            dd["offline_gap_notice"] = (
                _clean_line(raw_gap_note, max_len=240, field_name="offline_gap_note")
                if isinstance(raw_gap_note, str) and raw_gap_note.strip()
                else "那段时间没有连着发生，先停在这里，不用替它补写完。"
            )
            # 离线断点不是偷懒：原方向与可选方向都留着，绝不补写成已完成。
            dd["direction_stage"] = "offline_paused"
            dd["backfilled_to_diary"] = False
            dd["guilt_debt"] = 0
        elif mode == "clear":
            dd["direction"] = ""
            dd["optional_directions"] = []
            dd["current_focus"] = ""
            dd["status"] = "cleared"
        else:
            dir_txt = ""
            for k in ("direction", "theme", "title", "direction_text"):
                if isinstance(payload.get(k), str) and str(payload.get(k)).strip():
                    dir_txt = _clean_line(payload.get(k), max_len=140, field_name=k)
                    break
            opt_dirs: list[str] = list(dd.get("optional_directions") or [])
            if isinstance(payload.get("optional_directions"), list):
                opt_dirs = [
                    _clean_line(item, max_len=120, field_name="optional_directions")
                    for item in payload["optional_directions"][:6]
                ]
            elif dir_txt and dir_txt not in opt_dirs:
                opt_dirs = (opt_dirs + [dir_txt])[-6:]

            if isinstance(payload.get("commitments"), list):
                dd["commitments"] = [
                    _clean_line(c, max_len=120, field_name="commitments")
                    for c in payload["commitments"][:4]
                ]
            if isinstance(payload.get("current_focus"), str) and payload["current_focus"].strip():
                dd["current_focus"] = _clean_line(payload["current_focus"], max_len=120, field_name="current_focus")

            if not dir_txt and opt_dirs:
                dir_txt = opt_dirs[0]
            if not dir_txt and not dd.get("commitments") and not dd.get("current_focus"):
                raise V04ValidationError("INVALID_FIELDS", "set_day_direction requires direction or optional_directions")

            if dir_txt:
                dd["direction"] = dir_txt
                dd["direction_id"] = f"dir-{_short_hash(dir_txt + eff_now)}"
            dd["optional_directions"] = opt_dirs
            dd["status"] = "active"
            # 早上设的是"可选方向"，先记成打算，不当作已经发生。
            dd["direction_stage"] = "planned_or_optional"
            dd["backfilled_to_diary"] = False
            dd["guilt_debt"] = 0
        dd["pre_scheduled"] = False
        dd["updated_at"] = eff_now

    # 2. Both sides: Shared Notebook with non-destructive concurrent branch preservation (Ch. 46)
    elif action == "append_shared_notebook":
        raw_txt = payload.get("text") if "text" in payload else payload.get("body")
        text = _clean_multiline(raw_txt, max_len=500, field_name="text")
        sn = state["shared_notebook"]
        cur_head = int(sn.get("head_revision") or 0)
        exp_head = payload.get("expected_head_revision")
        branched = False
        branch_id = ""
        base_rev = cur_head

        if exp_head is not None:
            if isinstance(exp_head, str) and exp_head.strip().isdigit():
                exp_head_int = int(exp_head.strip())
            elif not isinstance(exp_head, bool) and isinstance(exp_head, int) and exp_head >= 0:
                exp_head_int = exp_head
            else:
                raise V04ValidationError("INVALID_FIELDS", "expected_head_revision must be non-negative revision")
            if exp_head_int != cur_head:
                branched = True
                base_rev = exp_head_int
                branch_id = f"{actor_side}-r{exp_head_int}-{next_rev}"

        new_head = cur_head + 1
        entry = {
            "entry_id": f"nb-{next_rev}",
            "author": actor_side,
            "actor": actor_side,
            "text": text,
            "base_revision": base_rev,
            "head_revision": new_head,
            "branch_id": branch_id,
            "created_at": eff_now,
        }
        entries = list(sn.get("entries") or [])
        entries.append(entry)
        sn["entries"] = entries[-64:]
        sn["head_revision"] = new_head
        if branched:
            sn["has_concurrent_branch"] = True
            branches = list(sn.get("branches") or [])
            branches.append({
                "branch_id": branch_id,
                "author": actor_side,
                "base_revision": base_rev,
                "merged_into_head": new_head,
                "text": text,
                "created_at": eff_now,
            })
            sn["branches"] = branches[-16:]

    # 3. Both sides: Reading Notice ("I paused at page X") (Ch. 46)
    elif action == "share_reading_notice":
        m_title = _clean_line(payload.get("material_title"), max_len=120, field_name="material_title")
        l_note = _clean_multiline(payload.get("line_note"), max_len=280, field_name="line_note")
        page = payload.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page > 100000:
            raise V04ValidationError("INVALID_FIELDS", "page must be an int in 1..100000")
        notices = list(state.get("reading_notices") or [])
        notices.append({
            "notice_id": f"rn-{next_rev}",
            "shared_by": actor_side,
            "material_title": m_title,
            "page": page,
            "line_note": l_note,
            "created_at": eff_now,
        })
        state["reading_notices"] = notices[-32:]

    # 3b. Both sides: Ch.50 §五 "两个人带回不一样的注意" -- each person shares one
    #     annotation they actually left on the same material page.  Divergence is
    #     never forced, sameness is never scored, and an unshared note is never
    #     previewed to the other side.
    elif action == "share_distinct_annotation":
        m_title = (
            _clean_line(payload["material_title"], max_len=120, field_name="material_title")
            if isinstance(payload.get("material_title"), str)
            and payload["material_title"].strip()
            else ""
        )
        src_ref = (
            _clean_line(payload["source_ref"], max_len=160, field_name="source_ref")
            if isinstance(payload.get("source_ref"), str) and payload["source_ref"].strip()
            else ""
        )
        if not m_title and not src_ref:
            raise V04ValidationError(
                "INVALID_FIELDS", "share_distinct_annotation requires material_title or source_ref"
            )
        page_val = payload.get("page")
        if (
            isinstance(page_val, bool)
            or not isinstance(page_val, int)
            or page_val < 1
            or page_val > 100000
        ):
            raise V04ValidationError("INVALID_FIELDS", "page must be an int in 1..100000")
        detail = _clean_line(
            payload.get("noticed_detail_cn"), max_len=240, field_name="noticed_detail_cn"
        )
        if len(detail) < 2:
            raise V04ValidationError(
                "INVALID_FIELDS", "noticed_detail_cn must be 2..240 characters"
            )
        quote = (
            _clean_line(payload["quote_excerpt_cn"], max_len=160, field_name="quote_excerpt_cn")
            if isinstance(payload.get("quote_excerpt_cn"), str)
            and payload["quote_excerpt_cn"].strip()
            else ""
        )
        ann_ref = (
            _clean_line(payload["annotation_ref"], max_len=96, field_name="annotation_ref")
            if isinstance(payload.get("annotation_ref"), str) and payload["annotation_ref"].strip()
            else ""
        )
        raw_shared = payload.get("shared_with_partner")
        if raw_shared is None:
            shared = True
        elif isinstance(raw_shared, bool):
            shared = raw_shared
        else:
            raise V04ValidationError("INVALID_FIELDS", "shared_with_partner must be a boolean")
        annotations = list(state.get("distinct_annotations") or [])
        annotations.append({
            "annotation_id": f"da-{next_rev}",
            "shared_by": actor_side,
            "material_title": m_title,
            "source_ref": src_ref,
            "material_key": _slug_token(src_ref or m_title, fallback="material"),
            "page": page_val,
            "noticed_detail_cn": detail,
            "quote_excerpt_cn": quote,
            "annotation_ref": ann_ref,
            "shared_with_partner": shared,
            # No fabricated "understanding" or "match" score, and no forced split.
            "forced_divergence": False,
            "comprehension_score_exists": False,
            "created_at": eff_now,
        })
        state["distinct_annotations"] = annotations[-64:]

    # 4. Both sides: Local Co-Experiment without win/loss scoreboards (Ch. 47)
    elif action == "propose_or_respond_experiment":
        exp_op = str(payload.get("exp_op") or payload.get("operation") or "propose").strip().lower()
        exps = list(state.get("experiments") or [])
        if exp_op == "propose":
            title = _clean_line(payload.get("title"), max_len=120, field_name="title")
            question = (
                _clean_line(payload["question"], max_len=240, field_name="question")
                if isinstance(payload.get("question"), str) and payload["question"].strip()
                else ""
            )
            orig = (
                _clean_multiline(payload["original_snippet"], max_len=600, field_name="original_snippet")
                if isinstance(payload.get("original_snippet"), str) and payload["original_snippet"].strip()
                else ""
            )
            cand = (
                _clean_multiline(payload["candidate_snippet"], max_len=600, field_name="candidate_snippet")
                if isinstance(payload.get("candidate_snippet"), str) and payload["candidate_snippet"].strip()
                else ""
            )
            eid = (
                _clean_line(payload["experiment_id"], max_len=64, field_name="experiment_id")
                if isinstance(payload.get("experiment_id"), str) and payload["experiment_id"].strip()
                else f"exp-{next_rev}"
            )
            target_work_ref = (
                _clean_line(payload["target_work_ref"], max_len=120, field_name="target_work_ref")
                if isinstance(payload.get("target_work_ref"), str) and payload["target_work_ref"].strip()
                else ""
            )
            exps.insert(0, {
                "experiment_id": eid,
                "proposed_by": actor_side,
                "title": title,
                "question": question,
                "original_snippet": orig,
                "candidate_snippet": cand,
                "target_work_ref": target_work_ref,
                "takes": [],
                "status": "active",
                "outcome": "",
                "scored": False,
                "created_at": eff_now,
                "updated_at": eff_now,
            })
            state["experiments"] = exps[:24]
        elif exp_op == "record_take":
            eid = _clean_line(payload.get("experiment_id"), max_len=64, field_name="experiment_id")
            take_txt = _clean_multiline(payload.get("take_text"), max_len=400, field_name="take_text")
            target = next((x for x in exps if x.get("experiment_id") == eid), None)
            if target is None:
                raise V04NotFoundError("EXPERIMENT_NOT_FOUND", f"Unknown experiment_id: {eid!r}")
            takes = list(target.get("takes") or [])
            takes.append({"actor": actor_side, "take_text": take_txt, "recorded_at": eff_now})
            target["takes"] = takes[-16:]
            target["updated_at"] = eff_now
        elif exp_op == "resolve":
            eid = _clean_line(payload.get("experiment_id"), max_len=64, field_name="experiment_id")
            target = next((x for x in exps if x.get("experiment_id") == eid), None)
            if target is None:
                raise V04NotFoundError("EXPERIMENT_NOT_FOUND", f"Unknown experiment_id: {eid!r}")
            outcome = (
                _clean_multiline(payload["outcome"], max_len=240, field_name="outcome")
                if isinstance(payload.get("outcome"), str) and payload["outcome"].strip()
                else "两边读感都试过，按最自然顺耳的一版收束"
            )
            target["status"] = "resolved"
            target["outcome"] = outcome
            target["resolved_by"] = actor_side
            target["updated_at"] = eff_now
        elif exp_op in ("pause_keep_both", "shelve_divergence"):
            # Ch.52 §五: disagreement can end in "keep both, replace neither".
            # The memo stays bound to the work, never nags on ordinary days and
            # never penalises affection.
            eid = (
                _clean_line(payload["experiment_id"], max_len=64, field_name="experiment_id")
                if isinstance(payload.get("experiment_id"), str) and payload["experiment_id"].strip()
                else ""
            )
            work_ref = (
                _clean_line(payload["target_work_ref"], max_len=120, field_name="target_work_ref")
                if isinstance(payload.get("target_work_ref"), str) and payload["target_work_ref"].strip()
                else ""
            )
            target = None
            if eid:
                target = next((x for x in exps if x.get("experiment_id") == eid), None)
            elif work_ref:
                target = next(
                    (x for x in exps if str(x.get("target_work_ref") or "") == work_ref),
                    None,
                )
            if target is None:
                wanted = eid or work_ref
                raise V04NotFoundError(
                    "EXPERIMENT_NOT_FOUND", f"Unknown experiment: {wanted!r}"
                )
            raw_memo = payload.get("divergence_memo_cn")
            if not (isinstance(raw_memo, str) and raw_memo.strip()):
                raw_memo = payload.get("outcome")
            memo = (
                _clean_multiline(raw_memo, max_len=240, field_name="divergence_memo_cn")
                if isinstance(raw_memo, str) and raw_memo.strip()
                else "原版更含蓄，改版更清楚，先不替换"
            )
            if work_ref and not target.get("target_work_ref"):
                target["target_work_ref"] = work_ref
            target["status"] = "kept_both_unreplaced"
            target["outcome"] = memo
            target["divergence_memo_cn"] = memo
            target["nag_daily"] = False
            target["affection_penalty"] = 0
            target["reopen_trigger_work_ref"] = str(target.get("target_work_ref") or "")
            target["paused_by"] = actor_side
            target["updated_at"] = eff_now
        else:
            raise V04ValidationError("INVALID_FIELDS", f"Unsupported exp_op: {exp_op!r}")

    # 5. Subject-only: Private Two Phrasings Draft (Ch. 47)
    elif action == "save_two_phrasings":
        if actor_side != "subject":
            raise V04PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can save private two phrasings")
        pa = _clean_multiline(payload.get("phrasing_a"), max_len=400, field_name="phrasing_a")
        pb = _clean_multiline(payload.get("phrasing_b"), max_len=400, field_name="phrasing_b")
        ctx_note = (
            _clean_line(payload["context_note"], max_len=160, field_name="context_note")
            if isinstance(payload.get("context_note"), str) and payload["context_note"].strip()
            else ""
        )
        chosen = str(payload.get("chosen") or "undecided").strip().lower()
        if chosen not in ("a", "b", "undecided"):
            raise V04ValidationError("INVALID_FIELDS", "chosen must be 'a', 'b', or 'undecided'")
        # Ch.50 §三: a guessed reply stays an imagined reply.  It never writes the
        # partner's stance and never enters real relationship memory.
        imagined = (
            _clean_multiline(
                payload["imagined_reply_cn"], max_len=240, field_name="imagined_reply_cn"
            )
            if isinstance(payload.get("imagined_reply_cn"), str)
            and payload["imagined_reply_cn"].strip()
            else ""
        )
        drafts = list(state.get("private_two_phrasings") or [])
        drafts.insert(0, {
            "draft_id": f"phr-{next_rev}",
            "context_note": ctx_note,
            "phrasing_a": pa,
            "phrasing_b": pb,
            "chosen": chosen,
            "imagined_reply_cn": imagined,
            "hypothetical_only": True,
            "writes_partner_stance": False,
            "enters_relationship_memory": False,
            "created_at": eff_now,
        })
        state["private_two_phrasings"] = drafts[:16]

    # 6. Both sides: Scoped Preference Teaching — never bleeds into private writing (Ch. 48)
    elif action == "teach_scoped_preference":
        scope = str(payload.get("scope") or "").strip().lower()
        if scope not in ALLOWED_PREFERENCE_SCOPES:
            raise V04ValidationError("INVALID_FIELDS", f"Invalid preference scope: {scope!r}")
        raw_inst = payload.get("instruction") if "instruction" in payload else payload.get("preference")
        inst = _clean_line(raw_inst, max_len=240, field_name="instruction")
        prefs = [
            p for p in (state.get("scoped_preferences") or [])
            if isinstance(p, Mapping) and p.get("scope") != scope
        ]
        prefs.insert(0, {
            "pref_id": f"pref-{scope}",
            "scope": scope,
            "instruction": inst,
            "preference": inst,
            "taught_by": actor_side,
            "status": "active",
            "applies_to_private_writing": False,
            "updated_at": eff_now,
        })
        state["scoped_preferences"] = prefs[:12]

    # 7. RBAC-split: Gentle Practice without numerical grades (Ch. 48)
    elif action == "request_or_answer_practice":
        prac_op = str(payload.get("practice_op") or "").strip().lower()
        ps = state["practice_sessions"]
        if prac_op == "request_prompt":
            if actor_side != "partner":
                raise V04PermissionError("FORBIDDEN_ACTOR_SIDE", "Only partner requests a practice prompt")
            topic = (
                _clean_line(payload["topic"], max_len=120, field_name="topic")
                if isinstance(payload.get("topic"), str) and payload["topic"].strip()
                else "日常表达小练习"
            )
            ps["status"] = "awaiting_prompt"
            ps["topic"] = topic
        elif prac_op == "offer_prompt":
            if actor_side != "subject":
                raise V04PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject offers a practice prompt")
            if payload.get("answer_text") is not None:
                raise V04ConflictError("PRACTICE_ROLE_CONFLICT", "offer_prompt cannot smuggle partner answer_text")
            p_txt = _clean_multiline(payload.get("prompt_text"), max_len=360, field_name="prompt_text")
            if isinstance(payload.get("topic"), str) and payload["topic"].strip():
                ps["topic"] = _clean_line(payload["topic"], max_len=120, field_name="topic")
            ps["current_prompt"] = p_txt
            ps["status"] = "prompt_ready"
        elif prac_op == "submit_answer":
            if actor_side != "partner":
                raise V04PermissionError("FORBIDDEN_ACTOR_SIDE", "Subject must never forge partner practice answers")
            ans = _clean_multiline(payload.get("answer_text"), max_len=600, field_name="answer_text")
            ps["last_answer"] = ans
            ps["status"] = "answer_submitted"
            hist = list(ps.get("history") or [])
            hist.append({"kind": "answer", "actor": "partner", "text": ans, "at": eff_now})
            ps["history"] = hist[-16:]
        elif prac_op == "give_feedback":
            if actor_side != "subject":
                raise V04PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject gives gentle practice observation")
            fb = _clean_multiline(payload.get("feedback_text"), max_len=360, field_name="feedback_text")
            ps["gentle_feedback"] = fb
            ps["status"] = "feedback_shared"
            hist = list(ps.get("history") or [])
            hist.append({"kind": "feedback", "actor": "subject", "text": fb, "graded": False, "at": eff_now})
            ps["history"] = hist[-16:]
        elif prac_op == "close_practice":
            ps["status"] = "idle"
            ps["current_prompt"] = ""
        else:
            raise V04ValidationError("INVALID_FIELDS", f"Unsupported practice_op: {prac_op!r}")

    # 8. Both sides: 10-Minute Temporary Rule Game with clean exit & optional keepsake (Ch. 50)
    elif action == "play_temporary_game":
        game_op = str(payload.get("game_op") or "").strip().lower()
        tg = state["temporary_game"]
        if game_op == "start":
            gk = str(payload.get("game_kind") or "").strip().lower()
            if gk not in ALLOWED_GAME_KINDS:
                raise V04ValidationError("INVALID_FIELDS", f"Invalid game_kind: {gk!r}")
            prop = (
                _clean_line(payload["prop_label"], max_len=80, field_name="prop_label")
                if isinstance(payload.get("prop_label"), str) and payload["prop_label"].strip()
                else "桌边小物"
            )
            rule_sum = (
                _clean_line(payload["rule_summary"], max_len=200, field_name="rule_summary")
                if isinstance(payload.get("rule_summary"), str) and payload["rule_summary"].strip()
                else "十分钟临时规则局，随时喊停回到普通聊天"
            )
            # Ch.53 §二: an optional shared-fiction frame ("this mug is a ship").
            # It is recorded on the game only -- no world object is touched.
            fict_prop = (
                _clean_line(
                    payload["fictional_prop_object_id"],
                    max_len=80,
                    field_name="fictional_prop_object_id",
                )
                if isinstance(payload.get("fictional_prop_object_id"), str)
                and payload["fictional_prop_object_id"].strip()
                else ""
            )
            fict_role = (
                _clean_line(
                    payload["fictional_role_cn"], max_len=120, field_name="fictional_role_cn"
                )
                if isinstance(payload.get("fictional_role_cn"), str)
                and payload["fictional_role_cn"].strip()
                else ""
            )
            tg["active"] = True
            tg["status"] = "active"
            tg["game_id"] = f"game-{next_rev}"
            tg["game_kind"] = gk
            tg["prop_label"] = prop
            tg["rule_summary"] = rule_sum
            tg["rule"] = rule_sum
            tg["started_at"] = eff_now
            tg["expires_at"] = format_wall_iso(now_dt + timedelta(minutes=15))
            tg["turns"] = []
            tg["fictional_prop_object_id"] = fict_prop
            tg["fictional_role_cn"] = fict_role
            tg["fantasy_scope_isolated"] = True
            tg["world_object_mutated"] = False
            tg["world_travel_fabricated"] = False
        elif game_op == "play_turn":
            eff_tg = _effective_temp_game(tg, now_dt)
            if not eff_tg.get("active"):
                raise V04ConflictError("NO_ACTIVE_GAME", "Start a temporary game before playing a turn")
            t_txt = _clean_multiline(payload.get("turn_text"), max_len=300, field_name="turn_text")
            turns = list(tg.get("turns") or [])
            turns.append({
                "actor": actor_side,
                "turn_text": t_txt,
                "at": eff_now,
                "fantasy_scope_isolated": True,
                "world_object_mutated": False,
                "world_travel_fabricated": False,
            })
            tg["turns"] = turns[-24:]
            tg["expires_at"] = format_wall_iso(now_dt + timedelta(minutes=15))
        elif game_op == "stop":
            tg["active"] = False
            tg["status"] = "idle"
            tg["rule_summary"] = ""
            tg["rule"] = ""
        elif game_op == "save_keepsake":
            k_title = (
                _clean_line(payload["keepsake_title"], max_len=120, field_name="keepsake_title")
                if isinstance(payload.get("keepsake_title"), str) and payload["keepsake_title"].strip()
                else "十分钟小游戏留念"
            )
            turns = list(tg.get("turns") or [])
            snippet = " / ".join(str(t.get("turn_text") or "")[:40] for t in turns[-2:])
            keepsakes = list(tg.get("keepsakes") or [])
            keepsakes.insert(0, {
                "keepsake_id": f"ks-{next_rev}",
                "keepsake_title": k_title,
                "title": k_title,
                "game_kind": str(tg.get("game_kind") or "odd_naming"),
                "snippet": snippet,
                "saved_by": actor_side,
                "saved_at": eff_now,
                # Ch.53 §二/§五: the kept work records the shared fiction as fiction;
                # the story's travels never become a real journey.
                "fictional_prop_object_id": str(tg.get("fictional_prop_object_id") or ""),
                "fictional_role_cn": str(tg.get("fictional_role_cn") or ""),
                "fantasy_scope_isolated": True,
                "world_object_mutated": False,
                "world_travel_fabricated": False,
            })
            tg["keepsakes"] = keepsakes[:16]
        else:
            raise V04ValidationError("INVALID_FIELDS", f"Unsupported game_op: {game_op!r}")

    # 9. Subject-only: Anti-spoiler Prepared Surprise (prepare_waiting -> offered) (Ch. 50)
    elif action == "prepare_or_offer_surprise":
        if actor_side != "subject":
            raise V04PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject prepares or offers a surprise")
        stage = str(payload.get("stage") or payload.get("surprise_op") or "prepare").strip().lower()
        surprises = list(state.get("prepared_surprises") or [])
        if stage == "prepare":
            kind = str(payload.get("kind") or "bookmark_note").strip().lower()
            if kind not in ALLOWED_SURPRISE_KINDS:
                kind = "tiny_keepsake"
            title = _clean_line(payload.get("title"), max_len=120, field_name="title")
            content = _clean_multiline(payload.get("content"), max_len=500, field_name="content")
            sid = (
                _clean_line(payload["surprise_id"], max_len=64, field_name="surprise_id")
                if isinstance(payload.get("surprise_id"), str) and payload["surprise_id"].strip()
                else f"surp-{next_rev}"
            )
            hint = (
                _clean_line(payload["invite_hint"], max_len=160, field_name="invite_hint")
                if isinstance(payload.get("invite_hint"), str) and payload["invite_hint"].strip()
                else f"{pronoun}悄悄留了一点小东西，等你有空时想拿给你看。"
            )
            surprises.insert(0, {
                "surprise_id": sid,
                "kind": kind,
                "title": title,
                "content": content,
                "invite_hint": hint,
                "status": "prepared_waiting",
                "prepared_at": eff_now,
                "offered_at": "",
                "revealed_at": "",
                "deferred_count": 0,
            })
            state["prepared_surprises"] = surprises[:16]
        elif stage == "offer":
            sid_opt = str(payload.get("surprise_id") or "").strip()
            target_sp = None
            for sp in surprises:
                if sid_opt and sp.get("surprise_id") == sid_opt:
                    target_sp = sp
                    break
                if not sid_opt and sp.get("status") == "prepared_waiting":
                    target_sp = sp
                    break
            if target_sp is None:
                raise V04NotFoundError("SURPRISE_NOT_FOUND", "No matching prepared surprise to offer")
            if isinstance(payload.get("invite_hint"), str) and payload["invite_hint"].strip():
                target_sp["invite_hint"] = _clean_line(payload["invite_hint"], max_len=160, field_name="invite_hint")
            target_sp["status"] = "offered"
            target_sp["offered_at"] = eff_now
        else:
            raise V04ValidationError("INVALID_FIELDS", f"Unsupported surprise stage: {stage!r}")

    # 10. Partner-only: Respond to Offered Surprise ("reveal" or "later") (Ch. 50)
    elif action == "respond_prepared_surprise":
        if actor_side != "partner":
            raise V04PermissionError("FORBIDDEN_ACTOR_SIDE", "Subject must never impersonate partner surprise response")
        sid = _clean_line(payload.get("surprise_id"), max_len=64, field_name="surprise_id")
        raw_dec = payload.get("decision") if "decision" in payload else payload.get("response")
        dec = str(raw_dec or "").strip().lower()
        if dec in ("accept", "open"):
            dec = "reveal"
        elif dec in ("defer", "hold"):
            dec = "later"
        if dec not in ("reveal", "later"):
            raise V04ValidationError("INVALID_FIELDS", "decision must be 'reveal' or 'later'")

        surprises = list(state.get("prepared_surprises") or [])
        target_sp = next((s for s in surprises if s.get("surprise_id") == sid), None)
        if target_sp is None:
            raise V04NotFoundError("SURPRISE_NOT_FOUND", f"Unknown surprise_id: {sid!r}")
        if target_sp.get("status") == "prepared_waiting":
            raise V04ConflictError("SURPRISE_NOT_YET_OFFERED", "Cannot reveal a surprise before subject offers it")
        if dec == "reveal":
            target_sp["status"] = "revealed"
            target_sp["revealed_at"] = eff_now
        else:
            target_sp["status"] = "offered"
            target_sp["deferred_count"] = int(target_sp.get("deferred_count") or 0) + 1
            target_sp["last_deferred_at"] = eff_now

    # 11. Subject-only: Ch.45 autonomous mini-projects linked to real places/works.
    #     No deadline, no failure score, guilt-free shelving and explicit page sharing.
    elif action == "curate_personal_project":
        if actor_side != "subject":
            raise V04PermissionError(
                "FORBIDDEN_ACTOR_SIDE", "Only subject curates her own personal projects"
            )
        curate_op = str(payload.get("op") or payload.get("project_op") or "").strip().lower()
        if curate_op not in ALLOWED_PROJECT_OPS:
            raise V04ValidationError("INVALID_FIELDS", f"Unsupported project op: {curate_op!r}")
        projects = [
            copy.deepcopy(dict(row))
            for row in (state.get("personal_projects") or [])
            if isinstance(row, Mapping)
        ]

        if curate_op == "start":
            purpose = _clean_line(payload.get("purpose_cn"), max_len=120, field_name="purpose_cn")
            medium = str(payload.get("medium_kind") or "").strip().lower()
            if medium not in ALLOWED_MEDIUM_KINDS:
                raise V04ValidationError("INVALID_FIELDS", f"Invalid medium_kind: {medium!r}")
            if payload.get("claimed_real_audio_recording") is True and medium == "sound_card":
                raise V04ConflictError(
                    "UNSUPPORTED_MEDIA_CLAIM",
                    "sound_card 是文字声景来源标注，没有任何真实录音或合成能力，不得声称录到了声音",
                )
            linked_work_ref = (
                _clean_line(payload["linked_work_ref"], max_len=160, field_name="linked_work_ref")
                if isinstance(payload.get("linked_work_ref"), str) and payload["linked_work_ref"].strip()
                else ""
            )
            next_step = (
                _clean_line(payload["next_step_cn"], max_len=100, field_name="next_step_cn")
                if isinstance(payload.get("next_step_cn"), str) and payload["next_step_cn"].strip()
                else ""
            )
            base_id = _slug_token(payload.get("project_id") or f"{medium}-{purpose}")
            existing_ids = {str(row.get("project_id") or "") for row in projects}
            project_id = base_id
            suffix = 2
            while project_id in existing_ids:
                project_id = f"{base_id}-{suffix}"
                suffix += 1
            project = {
                "project_id": project_id,
                "purpose_cn": purpose,
                "medium_kind": medium,
                "linked_work_ref": linked_work_ref,
                "next_step_cn": next_step,
                "status": "active",
                "confidence_penalty": 0,
                "forced_reflection": False,
                "reminder_suppressed": False,
                "artistic_expression_only": medium == "sketch_note",
                # A note or sketch never converts into world matter.
                "world_fact_authority": False,
                "entries": [],
                "created_at": eff_now,
                "updated_at": eff_now,
            }
            projects.insert(0, project)
        else:
            project_id = _clean_line(
                payload.get("project_id"), max_len=64, field_name="project_id"
            )
            project = next(
                (row for row in projects if row.get("project_id") == project_id), None
            )
            if project is None:
                raise V04NotFoundError(
                    "PROJECT_NOT_FOUND", f"Unknown project_id: {project_id!r}"
                )

            if curate_op == "add_field_note":
                if payload.get("claimed_real_audio_recording") is True:
                    raise V04ConflictError(
                        "UNSUPPORTED_MEDIA_CLAIM",
                        "没有真实录音/合成能力；sound_card 只能记文字声景来源",
                    )
                entry_status = str(payload.get("entry_status") or "visited_noted").strip().lower()
                if entry_status not in ("visited_noted", "blank_unvisited"):
                    raise V04ValidationError(
                        "INVALID_FIELDS",
                        "entry_status must be 'visited_noted' or 'blank_unvisited'",
                    )
                place_id = (
                    _clean_line(payload["place_id"], max_len=64, field_name="place_id")
                    if isinstance(payload.get("place_id"), str) and payload["place_id"].strip()
                    else ""
                )
                note_cn = _clean_multiline(
                    payload.get("note_cn"), max_len=400, field_name="note_cn"
                )
                private_raw_note = (
                    _clean_multiline(
                        payload["private_raw_note_cn"],
                        max_len=400,
                        field_name="private_raw_note_cn",
                    )
                    if isinstance(payload.get("private_raw_note_cn"), str)
                    and payload["private_raw_note_cn"].strip()
                    else ""
                )
                heard_sources: list[str] = []
                raw_sources = payload.get("heard_sources")
                if isinstance(raw_sources, (list, tuple)):
                    heard_sources = [
                        _clean_line(item, max_len=80, field_name="heard_sources")
                        for item in list(raw_sources)[:8]
                        if isinstance(item, str) and item.strip()
                    ]

                # Visited-Place Truth Gate (Ch.45 §二).  Indoor spots need no
                # journey evidence; an unvisited outdoor place may only be
                # recorded as ``blank_unvisited``.
                if (
                    entry_status == "visited_noted"
                    and place_id
                    and place_id not in INDOOR_NOTE_PLACES
                ):
                    verified = False
                    evidence_ref = payload.get("evidence_receipt_ref")
                    if isinstance(evidence_ref, str) and evidence_ref.strip():
                        try:
                            verify_completion_receipt_readonly(
                                day_root,
                                evidence_ref.strip(),
                                actor_side="subject",
                                linked_zone_id=place_id,
                            )
                            verified = True
                        except V04ElasticPlayError:
                            verified = False
                    if not verified:
                        verified = place_id in _collect_visited_place_ids_zero_write(
                            Path(day_root)
                        )
                    if not verified:
                        raise V04ConflictError(
                            "UNVISITED_PLACE_MUST_STAY_BLANK",
                            f"Place {place_id!r} has no persisted visit evidence; keep the page blank_unvisited",
                        )

                entries = list(project.get("entries") or [])
                entries.append({
                    "entry_id": f"page-{next_rev}-{len(entries) + 1}",
                    "place_id": place_id,
                    "entry_status": entry_status,
                    "note_cn": note_cn,
                    "private_raw_note_cn": private_raw_note,
                    "heard_sources": heard_sources,
                    "shared": False,
                    "created_at": eff_now,
                })
                project["entries"] = entries[-64:]
                project["updated_at"] = eff_now

            elif curate_op == "pause_not_feeling_it":
                project["status"] = "resting_not_feeling_it"
                project["confidence_penalty"] = 0
                project["forced_reflection"] = False
                project["reminder_suppressed"] = True
                project["paused_at"] = eff_now
                if isinstance(payload.get("note_cn"), str) and payload["note_cn"].strip():
                    project["pause_note_cn"] = _clean_line(
                        payload["note_cn"], max_len=200, field_name="note_cn"
                    )
                project["updated_at"] = eff_now

            elif curate_op == "resume":
                project["status"] = "active"
                project["reminder_suppressed"] = False
                project["resumed_at"] = eff_now
                project["updated_at"] = eff_now

            elif curate_op == "share_page":
                raw_index = payload.get("entry_index")
                if (
                    isinstance(raw_index, bool)
                    or not isinstance(raw_index, int)
                    or raw_index < 0
                ):
                    raise V04ValidationError(
                        "INVALID_FIELDS", "entry_index must be a non-negative int"
                    )
                entries = list(project.get("entries") or [])
                if raw_index >= len(entries):
                    raise V04NotFoundError(
                        "PAGE_NOT_FOUND", f"No entry at index {raw_index}"
                    )
                entries[raw_index]["shared"] = True
                entries[raw_index]["shared_at"] = eff_now
                project["entries"] = entries
                project["updated_at"] = eff_now

        state["personal_projects"] = projects[:MAX_PERSONAL_PROJECTS]

    # 12. Subject-only: grounded counter-proposal instead of servile obedience (Ch.45 §四)
    elif action == "propose_grounded_alternative":
        if actor_side != "subject":
            raise V04PermissionError(
                "FORBIDDEN_ACTOR_SIDE", "Only subject proposes her own grounded alternative"
            )
        target_suggestion = _clean_line(
            payload.get("target_suggestion_cn"),
            max_len=140,
            field_name="target_suggestion_cn",
        )
        alternative = _clean_line(
            payload.get("alternative_cn"), max_len=140, field_name="alternative_cn"
        )
        basis = _clean_line(
            payload.get("grounded_basis_cn"),
            max_len=140,
            field_name="grounded_basis_cn",
        )
        action_ref = (
            _clean_line(payload["action_ref"], max_len=160, field_name="action_ref")
            if isinstance(payload.get("action_ref"), str) and payload["action_ref"].strip()
            else ""
        )
        alternatives = list(state.get("grounded_alternatives") or [])
        alternatives.insert(0, {
            "alternative_id": f"alt-{next_rev}",
            "target_suggestion_cn": target_suggestion,
            "alternative_cn": alternative,
            "grounded_basis_cn": basis,
            "action_ref": action_ref,
            "created_at": eff_now,
        })
        state["grounded_alternatives"] = alternatives[:MAX_GROUNDED_ALTERNATIVES]

    # 13. Both sides: an admin/world edit is explicitly NOT subject consent (Ch.45 §五)
    elif action == "note_admin_world_change":
        summary = _clean_line(
            payload.get("change_summary_cn"),
            max_len=160,
            field_name="change_summary_cn",
        )
        raw_opinion = payload.get("subject_opinion_cn")
        if actor_side == "partner" and raw_opinion not in (None, ""):
            raise V04PermissionError(
                "FORBIDDEN_ACTOR_SIDE",
                "Partner may not forge the subject's opinion about an admin world edit",
            )
        opinion = (
            _clean_multiline(raw_opinion, max_len=240, field_name="subject_opinion_cn")
            if isinstance(raw_opinion, str) and raw_opinion.strip()
            else ""
        )
        edits = list(state.get("admin_world_edits") or [])
        edits.insert(0, {
            "edit_id": f"edit-{next_rev}",
            "change_summary_cn": summary,
            "recorded_by": actor_side,
            "recorded_as": "admin_world_edit",
            "subject_prior_consent_fabricated": False,
            "subject_opinion_cn": opinion,
            "recorded_at": eff_now,
        })
        state["admin_world_edits"] = edits[:MAX_ADMIN_WORLD_EDITS]

    else:
        raise V04ValidationError("INVALID_ACTION", f"Unknown action: {action!r}")

    state["revision"] = next_rev
    state["updated_at"] = eff_now
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
                "applied_at": eff_now,
            },
            revision=next_rev,
            dict_key="operation_receipts",
            payload_hash=fp,
            committed_at=eff_now,
        )
        _tx.trim_receipts_by_op_seq(
            state,
            dict_key="operation_receipts",
            max_keep=MAX_OPERATION_RECEIPTS,
        )

    _atomic_write_state(day_root, state)
    projected = project_v04_elastic_play(
        day_root,
        has_subject_capability=(actor_side == "subject"),
        now_iso=eff_now,
    )
    return projected, "APPLIED"

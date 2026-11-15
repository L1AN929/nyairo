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
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

STATE_SCHEMA = "nyairo.v04.elastic_play_state.v1"
VIEW_SCHEMA = "phone.v04.elastic_play.v1"
STATE_FILENAME = "v04_elastic_play.json"
DEFAULT_TZ = timezone(timedelta(hours=8))

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
            "updated_at": ts,
        },
        "shared_notebook": {
            "head_revision": 0,
            "has_concurrent_branch": False,
            "entries": [],
            "branches": [],
        },
        "reading_notices": [],
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
        },
        "prepared_surprises": [],
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


def _effective_temp_game(tg_raw: Mapping[str, Any], now_dt: datetime) -> dict[str, Any]:
    tg = copy.deepcopy(dict(tg_raw))
    if tg.get("active") and tg.get("expires_at"):
        exp_dt = parse_iso_moment(str(tg.get("expires_at")))
        if now_dt >= exp_dt:
            tg["active"] = False
            tg["status"] = "expired_naturally"
    return tg


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
        "experiments": copy.deepcopy(list(state.get("experiments") or [])),
        "scoped_preferences": copy.deepcopy(list(state.get("scoped_preferences") or [])),
        "practice_sessions": copy.deepcopy(dict(state.get("practice_sessions") or {})),
        "temporary_game": tg_proj,
        "game_keepsakes": copy.deepcopy(list(tg_proj.get("keepsakes") or [])),
        "prepared_surprises": visible_surprises,
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


def apply_elastic_play_mutation(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    if actor_side not in ("partner", "subject"):
        raise V04PermissionError("FORBIDDEN_ACTOR_SIDE", f"Invalid actor_side: {actor_side!r}")

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

    receipts = state.setdefault("operation_receipts", {})
    if op_key:
        prev = receipts.get(op_key)
        if isinstance(prev, Mapping):
            if prev.get("fingerprint") != fp:
                raise V04ConflictError(
                    "OPERATION_KEY_CONFLICT",
                    f"operation_key {op_key!r} reused with different payload",
                )
            return project_v04_elastic_play(
                day_root,
                has_subject_capability=(actor_side == "subject"),
                now_iso=eff_now,
            ), "DUPLICATE"

    if "expected_revision" in payload and payload.get("expected_revision") is not None:
        exp_rev = payload.get("expected_revision")
        if isinstance(exp_rev, bool) or not isinstance(exp_rev, int) or exp_rev < 0:
            raise V04ValidationError("INVALID_FIELDS", "expected_revision must be non-negative int")
        if int(state.get("revision") or 0) != exp_rev:
            raise V04ConflictError("REVISION_MISMATCH", "State revision mismatch")

    next_rev = int(state.get("revision") or 0) + 1

    # 1. Subject-only: Elastic Day Direction (Ch. 49)
    if action == "set_day_direction":
        if actor_side != "subject":
            raise V04PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can set elastic day direction")
        dd = state["day_direction"]
        mode = str(payload.get("mode") or "").strip().lower()
        if not mode:
            if "completed_summary" in payload or "receipt_ref" in payload:
                mode = "record_completed"
            elif "interrupt_reason" in payload:
                mode = "interrupt"
            elif "clear" in payload and payload.get("clear") is True:
                mode = "clear"
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
            done_list = list(dd.get("completed_items") or [])
            done_list.append({
                "summary": summary,
                "receipt_ref": rec_ref_raw.strip()[:96],
                "completed_at": eff_now,
            })
            dd["completed_items"] = done_list[-16:]
            dd["status"] = "active"
        elif mode == "interrupt":
            reason = _clean_line(payload.get("interrupt_reason"), max_len=160, field_name="interrupt_reason")
            dd["interrupt_note"] = reason
            dd["status"] = "yielded_naturally"
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
            exps.insert(0, {
                "experiment_id": eid,
                "proposed_by": actor_side,
                "title": title,
                "question": question,
                "original_snippet": orig,
                "candidate_snippet": cand,
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
        drafts = list(state.get("private_two_phrasings") or [])
        drafts.insert(0, {
            "draft_id": f"phr-{next_rev}",
            "context_note": ctx_note,
            "phrasing_a": pa,
            "phrasing_b": pb,
            "chosen": chosen,
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
        elif game_op == "play_turn":
            eff_tg = _effective_temp_game(tg, now_dt)
            if not eff_tg.get("active"):
                raise V04ConflictError("NO_ACTIVE_GAME", "Start a temporary game before playing a turn")
            t_txt = _clean_multiline(payload.get("turn_text"), max_len=300, field_name="turn_text")
            turns = list(tg.get("turns") or [])
            turns.append({"actor": actor_side, "turn_text": t_txt, "at": eff_now})
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

    else:
        raise V04ValidationError("INVALID_ACTION", f"Unknown action: {action!r}")

    state["revision"] = next_rev
    state["updated_at"] = eff_now
    if op_key:
        receipts[op_key] = {
            "fingerprint": fp,
            "action": action,
            "revision": next_rev,
            "applied_at": eff_now,
        }
        if len(receipts) > 128:
            oldest_k = next(iter(receipts.keys()))
            receipts.pop(oldest_k, None)

    _atomic_write_state(day_root, state)
    projected = project_v04_elastic_play(
        day_root,
        has_subject_capability=(actor_side == "subject"),
        now_iso=eff_now,
    )
    return projected, "APPLIED"

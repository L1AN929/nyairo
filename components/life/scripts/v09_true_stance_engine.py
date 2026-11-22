#!/usr/bin/env python3
"""v0.9 Pillar A -- True Stance, Reason Retention, Blind Simultaneous Choice & Growth-from-Blank.

Architecture & Contracts (Master Blueprint §4.1-4.3):
- State file: ``<day_root>/life/v09_true_stance_state.json``
  (``SCHEMA = "life.v09.true_stance.v1"``), rooted via
  ``instance_paths.resolve_instance_paths(day_root).day_root``.
- Zero external LLM calls (``llm_calls == 0``) across every read and write path.
- 100% Zero-Write Reads (``read_true_stance_zero_write``): creates 0 dirs,
  0 files, 0 ``.lock`` files, and enforces
  ``load_unified_forget_barrier_readonly(day_root)`` (filtering blocked
  ``choice_id`` / ``stance_id`` / references and failing closed cleanly).
- Atomic Locked Writes (``apply_true_stance_command``): acquires
  ``state_tx_guard.state_file_lock``, rejects symlinks on input/day_root/parent/
  target/lock, writes atomic ``0600`` UTF-8 LF JSON (``fsync`` + ``os.replace``),
  and enforces ``operation_key`` idempotency (``reconcile_evicted_or_active_op``,
  ``stamp_receipt``, ``trim_receipts_by_op_seq``).
- Sub-Domain 1 -- Blind Simultaneous Choice (``blind_choices``, max 32):
  ``OPEN_AWAITING_BOTH`` -> ``OPEN_SUBJECT_COMMITTED`` / ``OPEN_PARTNER_COMMITTED``
  -> ``REVEALED`` (automatic when both commit) -> ``DISCUSSED`` -> ``RESOLVED``.
  * Cryptographic pre-reveal SHA-256 commitment seal verified live post-reveal.
  * Strict asymmetric pre-reveal masking so neither side sees the unrevealed pick.
  * Anti-sycophancy resolution modes: ``ACCEPT_CHOICE_KEEP_REASONS``,
    ``KEEP_OWN_STANCE``, ``COMPROMISE_MIDDLE``, ``REVISED_WITH_NEW_EVIDENCE``
    (rejecting reason erasure with ``409 SYCOPHANTIC_REASON_ERASURE_FORBIDDEN``
    and bare insistence with ``409 BARE_INSISTENCE_NOT_EVIDENCE``).
- Sub-Domain 2 -- Growth-from-Blank (``growth_stances``, max 64):
  ``UNFORMED`` -> ``TENTATIVE`` -> ``FORMED`` -> ``REVISED`` with immutable
  ``prior_stances[]`` lineage, 4 epistemic provenance tiers
  (``firsthand_lived``, ``read_knowledge``, ``partner_heard``, ``tentative_guess``),
  coercion filter (``409 COERCION_NOT_PERSUASION``), and private withholding
  (``shared_with_partner: bool``).
- Strictly neutral TA phrasing (zero hardcoded names, zero gendered pronouns,
  zero ``unicode.So`` emoji characters).
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_LIFE_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _LIFE_SCRIPTS_DIR.parents[2]
_WORLD_SCRIPTS_DIR = _REPO_ROOT / "components" / "world" / "scripts"
for _entry in (str(_REPO_ROOT), str(_LIFE_SCRIPTS_DIR), str(_WORLD_SCRIPTS_DIR)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:
    from components.world.scripts import instance_paths as _instance_paths
except ImportError:  # pragma: no cover - flat script import fallback
    import instance_paths as _instance_paths  # type: ignore[no-redef]

try:
    from components.life.scripts import state_tx_guard as _tx
except ImportError:  # pragma: no cover - flat script import fallback
    import state_tx_guard as _tx  # type: ignore[no-redef]

try:
    from components.life.scripts import v02_representation_lease_forget as _rlf
except ImportError:  # pragma: no cover - flat script import fallback
    import v02_representation_lease_forget as _rlf  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Public Schema, Constants & Error Type
# ---------------------------------------------------------------------------

SCHEMA = "life.v09.true_stance.v1"
STATE_SCHEMA = SCHEMA
STATE_REL_PATH: tuple[str, ...] = ("life", "v09_true_stance_state.json")
DEFAULT_TZ = timezone(timedelta(hours=8))

MAX_BLIND_CHOICES = 32
MAX_GROWTH_STANCES = 64
MAX_OPERATION_RECEIPTS = 256
MIN_OPTIONS_PER_CHOICE = 2
MAX_OPTIONS_PER_CHOICE = 6
MIN_REASONS_PER_CHOICE = 1
MAX_REASONS_PER_CHOICE = 4
MAX_DISCUSSION_NOTES = 24
MAX_PRIOR_REASONS_HISTORY = 16
MAX_PRIOR_STANCES = 24
MAX_PERSUASION_RECORDS = 16
MAX_DOUBTS_PER_STANCE = 12

BLIND_CHOICE_UNDECIDED = "UNDECIDED"
DEFAULT_UNFORMED_SUMMARY_CN = "这件事我还没想过"
DEFAULT_KEPT_DOUBT_CN = "听进了一部分说法，但还没有亲身印证，暂时保留疑问"

BLIND_CHOICE_STATUSES: tuple[str, ...] = (
    "OPEN_AWAITING_BOTH",
    "OPEN_SUBJECT_COMMITTED",
    "OPEN_PARTNER_COMMITTED",
    "REVEALED",
    "DISCUSSED",
    "RESOLVED",
)

PRE_REVEAL_STATUSES: frozenset[str] = frozenset({
    "OPEN_AWAITING_BOTH",
    "OPEN_SUBJECT_COMMITTED",
    "OPEN_PARTNER_COMMITTED",
})

POST_REVEAL_STATUSES: frozenset[str] = frozenset({
    "REVEALED",
    "DISCUSSED",
    "RESOLVED",
})

SEAL_STATUS_UNCOMMITTED = "UNCOMMITTED"
SEAL_STATUS_SEALED_UNTIL_PARTNER = "SEALED_UNTIL_PARTNER_SUBMITS"
SEAL_STATUS_SEALED_UNTIL_SUBJECT = "SEALED_UNTIL_SUBJECT_COMMITS"
SEAL_STATUS_REVEALED_VERIFIED = "REVEALED_VERIFIED"
SEAL_STATUS_REVEALED_TAMPERED = "REVEALED_TAMPERED"

RESOLUTION_MODES: tuple[str, ...] = (
    "ACCEPT_CHOICE_KEEP_REASONS",
    "KEEP_OWN_STANCE",
    "COMPROMISE_MIDDLE",
    "REVISED_WITH_NEW_EVIDENCE",
)

NEW_EVIDENCE_KINDS: tuple[str, ...] = (
    "lived_experience",
    "verified_reading",
    "concrete_partner_fact",
)

STANCE_STATUSES: tuple[str, ...] = (
    "UNFORMED",
    "TENTATIVE",
    "FORMED",
    "REVISED",
)

PROVENANCE_TIERS: tuple[str, ...] = (
    "firsthand_lived",
    "read_knowledge",
    "partner_heard",
    "tentative_guess",
)

PERSUASION_VERDICTS: tuple[str, ...] = (
    "PERSUADED_TENTATIVE",
    "PARTIALLY_TAKEN_KEEP_DOUBT",
    "NOT_CONVINCED_YET",
)

BOTH_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "open_blind_choice",
    "register_unformed_topic",
})

PARTNER_ONLY_ACTIONS: frozenset[str] = frozenset({
    "partner_submit_blind_choice",
    "partner_discuss_blind_choice",
    "partner_persuade_stance",
})

SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "subject_commit_blind_choice",
    "resolve_blind_choice",
    "advance_stance_from_experience",
    "evaluate_partner_persuasion",
    "set_stance_shared",
})

PARTNER_ALLOWED_ACTIONS: frozenset[str] = BOTH_ALLOWED_ACTIONS | PARTNER_ONLY_ACTIONS
SUBJECT_ALLOWED_ACTIONS: frozenset[str] = BOTH_ALLOWED_ACTIONS | SUBJECT_ONLY_ACTIONS
KNOWN_ACTIONS: frozenset[str] = BOTH_ALLOWED_ACTIONS | PARTNER_ONLY_ACTIONS | SUBJECT_ONLY_ACTIONS

FORBIDDEN_CLIENT_KEYS: frozenset[str] = frozenset({
    "uid",
    "user_id",
    "owner_uid",
    "local_owner_uid",
    "actor_side",
    "author_side",
    "has_subject_capability",
    "subject_view",
    "capability",
    "intimacy_score",
    "affection_score",
    "favorability",
    "relationship_xp",
    "obedience_level",
    "compliance_score",
    "sycophancy_score",
    "force_flip",
    "auto_agree",
})

_ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_PUNCT_AND_SPACE_RE = re.compile(r"[\s，,。\.、！？!\?;；:：\"'“”‘’（）()【】\[\]《》<>·~—\-…]+")

# Coercion / bare-insistence cues that never qualify as genuine evidence or persuasion.
_COERCION_CUES: tuple[str, ...] = (
    "我就想吃这个",
    "我就想吃这家",
    "我就想选这个",
    "我就想选这家",
    "我就要吃这个",
    "我就要选这个",
    "我就要这个",
    "反正我就要这个",
    "反正就吃这个",
    "反正就选这个",
    "就听我的",
    "听我的",
    "必须听我的",
    "你必须听我的",
    "你得听我的",
    "你得听话",
    "听话",
    "别管那么多",
    "少管那么多",
    "别废话",
    "少废话",
    "别啰嗦",
    "快改口",
    "赶紧改口",
    "立刻改口",
    "马上改口",
    "给我改口",
    "快点改口",
    "我命令你",
    "命令你",
    "不许反对",
    "不准反对",
    "不要反驳",
    "别反驳",
    "照我说的做",
    "照我说的办",
    "照我说的选",
    "照我说的来",
    "照我说的",
    "按我说的做",
    "按我说的办",
    "按我说的选",
    "按我说的来",
    "就按我说的",
    "按我说的",
    "不用多想",
    "别多想",
    "别再讨论",
    "不用再讨论",
    "别再商量",
    "不用商量",
    "听我安排",
    "照我安排",
    "就这么办",
    "别争了",
    "少提意见",
    "别提意见",
    "别挑毛病",
    "你就顺着我",
    "顺着我说",
    "顺着我选",
    "别犟了",
    "你必须同意",
    "必须同意",
    "必须赞成",
    "我说什么就是什么",
    "因为我说了算",
    "我说了算",
    "不管你觉得怎样",
    "不管你的理由",
    "别说理由了",
    "快同意吧",
    "赶紧同意",
    "赶紧答应",
    "你就答应吧",
    "直接答应",
    "直接同意",
    "直接赞成",
    "别想那么多直接赞成",
    "再不答应我生气了",
    "不听我的就不理你",
    "你怎么还不听",
    "我坚持要这个",
    "我坚持吃这个",
    "我坚持选这个",
    "你就改选这个",
    "快改选这个",
    "我不管",
    "必须去",
    "必须吃",
    "必须选",
    "别再提",
    "不要再提",
    "别说了",
    "少说话",
    "你只能选",
    "不准选别的",
    "必须跟我一样",
    "你也必须喜欢",
    "不许说不喜欢",
    "给我同意",
    "给我赞成",
    "你必须赞成",
    "赶紧赞成",
    "不准拒绝",
    "不许拒绝",
    "just listen to me",
    "do as i say",
    "i order you",
    "stop arguing",
    "just agree",
    "because i said so",
    "change your mind now",
    "obey me",
)

# Imperative/filler wrappers stripped when evaluating whether any concrete factual
# clause remains alongside a matched coercion cue.
_IMPERATIVE_FILLER_TOKENS: tuple[str, ...] = (
    "请你",
    "你快",
    "你赶紧",
    "你立刻",
    "你马上",
    "你现在",
    "你就",
    "赶紧",
    "立刻",
    "马上",
    "现在",
    "快点",
    "反正",
    "不管",
    "就是",
    "真的",
    "求你了",
    "求你",
    "拜托",
    "好不好",
    "行不行",
    "行吗",
    "好吗",
    "选这家",
    "吃这家",
    "选这个",
    "吃这个",
    "选它",
    "同意",
    "答应",
    "赞成",
    "改口",
    "改选",
    "改一下",
    "就这么定了",
    "定下来",
    "不要犹豫",
    "别犹豫",
    "别再犹豫",
    "好了",
    "得了",
    "算了",
    "吧",
    "啦",
    "嘛",
    "啊",
    "呀",
    "哦",
    "嗯",
    "呢",
    "了",
)

# Concrete factual / observational markers indicating real new information rather
# than bare insistence when mixed with a strong personal wish.
_FACTUAL_GROUNDING_CUES: tuple[str, ...] = (
    "半价",
    "打折",
    "优惠",
    "特价",
    "免费",
    "送",
    "减免",
    "不用排队",
    "免排队",
    "没人排队",
    "不用等",
    "不排队",
    "等位",
    "排队",
    "空位",
    "预约",
    "订到",
    "订了位",
    "刚问过",
    "刚看过",
    "刚查过",
    "查了",
    "确认过",
    "打电话",
    "问过",
    "实测",
    "店主说",
    "老板说",
    "店员说",
    "发消息",
    "公告",
    "招牌",
    "菜单",
    "食材",
    "分量",
    "价格",
    "单价",
    "人均",
    "营业",
    "关门",
    "休息",
    "店休",
    "售罄",
    "卖完",
    "新出",
    "新开",
    "换了",
    "改了",
    "搬到",
    "距离",
    "步行",
    "分钟",
    "小时",
    "今天",
    "今晚",
    "刚才",
    "上午",
    "下午",
    "晚上",
    "午市",
    "晚市",
    "其实",
    "因为",
    "据",
    "材料",
    "研究",
    "记录",
    "书里",
    "手册",
    "实情",
    "天气",
    "下雨",
    "温度",
    "过敏",
    "清淡",
    "少糖",
    "元",
    "块",
    "折",
    "号",
    "米",
    "公里",
)

_DIGIT_RE = re.compile(r"[0-9一二两三四五六七八九十百千]")


class V09TrueStanceError(ValueError):
    """Structured domain error carrying HTTP status code and machine reason code."""

    def __init__(self, status_code: int, reason_code: str, detail: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail or reason_code)
        self.message = self.detail
        super().__init__(self.detail)


# ---------------------------------------------------------------------------
# Internal Helpers: Canonical JSON, Seal, Hygiene, Time, Symlink & Atomic I/O
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    """Serialize ``obj`` to deterministic UTF-8 compact JSON with sorted keys."""
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_subject_commit_sha256(
    *,
    choice_id: str,
    reasons: Sequence[Mapping[str, Any]],
    committed_at: str,
    nonce: str,
) -> str:
    """Compute SHA-256 pre-reveal commitment seal for TA's initial blind choice."""
    normalized_reasons: list[dict[str, Any]] = []
    for row in reasons:
        if not isinstance(row, Mapping):
            continue
        ev_ref = row.get("evidence_ref")
        normalized_reasons.append({
            "reason_id": str(row.get("reason_id") or ""),
            "facet": str(row.get("facet") or ""),
            "text_cn": str(row.get("text_cn") or ""),
            "evidence_ref": str(ev_ref) if isinstance(ev_ref, str) and ev_ref else None,
        })
    seal_doc = {
        "choice_id": str(choice_id),
        "reasons": normalized_reasons,
        "committed_at": str(committed_at),
        "nonce": str(nonce),
    }
    return hashlib.sha256(canonical_json(seal_doc).encode("utf-8")).hexdigest()


def _strip_emojis_and_ctrl(text: str, max_len: int = 240) -> str:
    cleaned: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "So" or cat.startswith("C"):
            continue
        cleaned.append(ch)
    return "".join(cleaned).strip()[:max_len]


def _clean_text_field(
    raw: Any,
    *,
    field_name: str,
    min_len: int = 0,
    max_len: int = 240,
) -> str:
    if raw is None:
        if min_len > 0:
            raise V09TrueStanceError(400, "INVALID_FIELDS", f"Field {field_name!r} is required")
        return ""
    if not isinstance(raw, str):
        raise V09TrueStanceError(400, "INVALID_FIELDS", f"Field {field_name!r} must be a string")
    cleaned = _strip_emojis_and_ctrl(raw, max_len=max_len)
    if len(cleaned) < min_len:
        raise V09TrueStanceError(
            400,
            "INVALID_FIELDS",
            f"Field {field_name!r} must be at least {min_len} characters",
        )
    return cleaned


def _clean_optional_ref(raw: Any, *, field_name: str, max_len: int = 160) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise V09TrueStanceError(400, "INVALID_FIELDS", f"Field {field_name!r} must be a string")
    cleaned = _strip_emojis_and_ctrl(raw, max_len=max_len)
    return cleaned or None


def _validate_id_token(raw: Any, *, field_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise V09TrueStanceError(400, "INVALID_FIELDS", f"Field {field_name!r} is required")
    token = raw.strip()
    if not _ID_TOKEN_RE.match(token):
        raise V09TrueStanceError(
            400,
            "INVALID_FIELDS",
            f"Field {field_name!r} contains invalid characters: {token!r}",
        )
    return token


def _validate_optional_id_token(raw: Any, *, field_name: str) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise V09TrueStanceError(400, "INVALID_FIELDS", f"Field {field_name!r} must be a string")
    stripped = raw.strip()
    if not stripped:
        return None
    return _validate_id_token(stripped, field_name=field_name)


def _validate_operation_key(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise V09TrueStanceError(400, "INVALID_FIELDS", "Field 'operation_key' must be a string")
    stripped = raw.strip()
    if len(stripped) > 128:
        raise V09TrueStanceError(400, "INVALID_FIELDS", "Field 'operation_key' is too long")
    cleaned = _strip_emojis_and_ctrl(stripped, max_len=128)
    return cleaned or None


def _normalize_actor_side(actor_side: Any) -> str:
    norm = str(actor_side or "").strip().lower()
    if norm not in ("partner", "subject"):
        raise V09TrueStanceError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Unsupported actor_side: {actor_side!r}",
        )
    return norm


def _parse_dt(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=DEFAULT_TZ)
    if not isinstance(raw, str) or not raw.strip():
        return None
    txt = raw.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(DEFAULT_TZ).isoformat(timespec="seconds")


def _resolve_now(now_iso: Optional[str]) -> tuple[datetime, str]:
    if now_iso is not None:
        dt = _parse_dt(now_iso)
        if dt is None:
            raise V09TrueStanceError(400, "INVALID_FIELDS", f"Invalid ISO timestamp: {now_iso!r}")
        return dt, _format_iso(dt)
    dt = datetime.now(tz=DEFAULT_TZ)
    return dt, _format_iso(dt)


def _scan_forbidden_client_keys(value: Any, path: str = "") -> Optional[str]:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key_str = str(raw_key).strip()
            low_key = key_str.lower()
            full_path = f"{path}.{key_str}" if path else key_str
            if low_key in FORBIDDEN_CLIENT_KEYS:
                return full_path
            found = _scan_forbidden_client_keys(nested, full_path)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for idx, item in enumerate(value):
            found = _scan_forbidden_client_keys(item, f"{path}[{idx}]")
            if found is not None:
                return found
    return None


def _is_regular_file(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_file()
    except OSError:
        return False


def _read_json_readonly_strict(path: Path) -> tuple[Optional[dict[str, Any]], bool, bool]:
    """Read JSON file zero-write; return ``(doc, exists_on_disk, is_corrupt)``."""
    try:
        parent_symlink = path.parent.is_symlink()
        exists_on_disk = parent_symlink or path.is_symlink() or os.path.lexists(path)
    except OSError:
        return None, True, True
    if not exists_on_disk:
        return None, False, False
    try:
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            return None, True, True
        raw = path.read_text(encoding="utf-8")
        doc = json.loads(raw)
        if not isinstance(doc, Mapping):
            return None, True, True
        return dict(doc), True, False
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return None, True, True


def _resolve_day_root(day_root: Path | str) -> Path:
    return _instance_paths.resolve_instance_paths(day_root).day_root


def true_stance_state_path(day_root: Path | str) -> Path:
    """Return canonical ``<day_root>/life/v09_true_stance_state.json`` path (zero-write)."""
    return _resolve_day_root(day_root).joinpath(*STATE_REL_PATH)


def _assert_no_symlink_chain(raw_input: Path | str, resolved_day_root: Path, target_file: Path) -> None:
    raw_path = Path(str(raw_input)).expanduser()
    lock_path = Path(str(target_file) + ".lock")
    for label, cand in (
        ("input_path", raw_path),
        ("day_root", resolved_day_root),
        ("life_dir", target_file.parent),
        ("state_file", target_file),
        ("lock_file", lock_path),
    ):
        try:
            if cand.is_symlink():
                raise V09TrueStanceError(400, "UNSAFE_SYMLINK", f"{label} must not be a symlink")
        except OSError as exc:
            raise V09TrueStanceError(400, "UNSAFE_SYMLINK", f"Cannot inspect {label}: {exc}") from exc


def _atomic_write_json_0600(
    raw_input: Path | str,
    resolved_day_root: Path,
    target_file: Path,
    payload: Mapping[str, Any],
) -> None:
    _assert_no_symlink_chain(raw_input, resolved_day_root, target_file)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_chain(raw_input, resolved_day_root, target_file)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    raw_bytes = serialized.replace("\r\n", "\n").encode("utf-8")
    tmp_name = f".{target_file.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    tmp_path = target_file.parent / tmp_name
    try:
        if os.name == "posix":
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
        else:
            with open(tmp_path, "wb") as fh:
                fh.write(raw_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
        os.replace(str(tmp_path), str(target_file))
    except Exception:
        try:
            if tmp_path.exists() or tmp_path.is_symlink():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _make_short_id(prefix: str, seed_text: str, existing: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:10]
    candidate = f"{prefix}_{digest}"
    counter = 1
    while candidate in existing:
        candidate = f"{prefix}_{digest}_{counter}"
        counter += 1
    return candidate


def _empty_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "revision": 0,
        "op_seq": 0,
        "next_op_seq": 0,
        "updated_at": None,
        "blind_choices": {},
        "growth_stances": {},
        "operation_receipts": {},
        "committed_op_ring": [],
        "evicted_up_to_revision": 0,
    }


def _load_or_init_state_locked(state_path: Path) -> dict[str, Any]:
    doc, exists, is_corrupt = _read_json_readonly_strict(state_path)
    if is_corrupt:
        raise V09TrueStanceError(
            500,
            "CORRUPT_STATE_FILE",
            f"State file is unreadable or corrupt: {state_path.name}",
        )
    if not exists or doc is None:
        return _empty_state()
    state = _empty_state()
    state.update(doc)
    state["schema"] = SCHEMA
    if not isinstance(state.get("blind_choices"), dict):
        state["blind_choices"] = {}
    if not isinstance(state.get("growth_stances"), dict):
        state["growth_stances"] = {}
    if not isinstance(state.get("operation_receipts"), dict):
        state["operation_receipts"] = {}
    if not isinstance(state.get("committed_op_ring"), list):
        state["committed_op_ring"] = []
    return state


# ---------------------------------------------------------------------------
# Hard Coercion & Bare-Insistence Filter
# ---------------------------------------------------------------------------


def _detect_bare_coercion_or_insistence(text_cn: str) -> tuple[bool, str]:
    """Return ``(is_bare_coercion, matched_cue)``.

    Matches bare insistence, pressure, or command phrasing (e.g. ``"我就想吃这个"``,
    ``"听我的"``, ``"别管那么多"``, ``"快改口"``, ``"我命令你"``) unless accompanied
    by a concrete new factual clause of at least 6 meaningful characters.
    """
    raw = str(text_cn or "").strip()
    if not raw:
        return True, "EMPTY_TEXT"
    lowered = raw.lower()

    matched_cues: list[str] = []
    stripped = lowered
    for cue in sorted(_COERCION_CUES, key=len, reverse=True):
        if cue in stripped:
            matched_cues.append(cue)
            stripped = stripped.replace(cue, " ")

    if not matched_cues:
        compact = _PUNCT_AND_SPACE_RE.sub("", lowered)
        if len(compact) < 6:
            return True, "TOO_SHORT_FOR_CONCRETE_FACT"
        return False, ""

    # Remove imperative wrappers and punctuation to inspect what concrete
    # substance remains outside the coercion phrasing.
    residual = stripped
    for filler in sorted(_IMPERATIVE_FILLER_TOKENS, key=len, reverse=True):
        residual = residual.replace(filler, " ")
    residual_compact = _PUNCT_AND_SPACE_RE.sub("", residual)

    if len(residual_compact) < 6:
        return True, matched_cues[0]

    has_digit_with_context = bool(_DIGIT_RE.search(residual_compact)) and (
        sum(1 for ch in residual_compact if not ch.isdigit() and not ch.isspace()) >= 2
    )
    has_factual_cue = any(marker in residual for marker in _FACTUAL_GROUNDING_CUES) or has_digit_with_context
    if not has_factual_cue:
        return True, matched_cues[0]

    return False, ""


# ---------------------------------------------------------------------------
# Validation Helpers for Options & Reasons
# ---------------------------------------------------------------------------


def _normalize_choice_options(raw_options: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_options, Sequence) or isinstance(raw_options, (str, bytes)):
        raise V09TrueStanceError(
            400,
            "INVALID_OPTIONS",
            "Field 'options' must be a list of 2 to 6 choice options",
        )
    if len(raw_options) < MIN_OPTIONS_PER_CHOICE or len(raw_options) > MAX_OPTIONS_PER_CHOICE:
        raise V09TrueStanceError(
            400,
            "INVALID_OPTIONS",
            f"Field 'options' must contain between {MIN_OPTIONS_PER_CHOICE} and {MAX_OPTIONS_PER_CHOICE} items",
        )
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, entry in enumerate(raw_options):
        if isinstance(entry, str):
            opt_id = f"opt_{idx + 1}"
            label_cn = _clean_text_field(entry, field_name=f"options[{idx}]", min_len=1, max_len=80)
            note_cn = ""
        elif isinstance(entry, Mapping):
            raw_oid = entry.get("option_id") or entry.get("id")
            if raw_oid is not None and str(raw_oid).strip():
                opt_id = _validate_id_token(raw_oid, field_name=f"options[{idx}].option_id")
            else:
                opt_id = f"opt_{idx + 1}"
            label_cn = _clean_text_field(
                entry.get("label_cn") or entry.get("label") or entry.get("title_cn"),
                field_name=f"options[{idx}].label_cn",
                min_len=1,
                max_len=80,
            )
            note_cn = _clean_text_field(
                entry.get("note_cn") or entry.get("note"),
                field_name=f"options[{idx}].note_cn",
                min_len=0,
                max_len=160,
            )
        else:
            raise V09TrueStanceError(
                400,
                "INVALID_OPTIONS",
                f"options[{idx}] must be an object or string",
            )
        if opt_id.upper() == BLIND_CHOICE_UNDECIDED:
            raise V09TrueStanceError(
                400,
                "INVALID_OPTIONS",
                "Option ID 'UNDECIDED' is reserved",
            )
        if opt_id in seen_ids:
            raise V09TrueStanceError(
                400,
                "INVALID_OPTIONS",
                f"Duplicate option_id: {opt_id!r}",
            )
        seen_ids.add(opt_id)
        normalized.append({
            "option_id": opt_id,
            "label_cn": label_cn,
            "note_cn": note_cn,
        })
    return normalized


def _normalize_reasons_list(
    raw_reasons: Any,
    *,
    min_count: int,
    max_count: int = MAX_REASONS_PER_CHOICE,
    field_name: str = "reasons",
) -> list[dict[str, Any]]:
    if raw_reasons is None:
        if min_count > 0:
            raise V09TrueStanceError(
                400,
                "INVALID_REASONS",
                f"Field {field_name!r} requires {min_count}..{max_count} structured reasons",
            )
        return []
    if not isinstance(raw_reasons, Sequence) or isinstance(raw_reasons, (str, bytes)):
        raise V09TrueStanceError(
            400,
            "INVALID_REASONS",
            f"Field {field_name!r} must be a list of reason objects",
        )
    if len(raw_reasons) < min_count or len(raw_reasons) > max_count:
        raise V09TrueStanceError(
            400,
            "INVALID_REASONS",
            f"Field {field_name!r} must contain between {min_count} and {max_count} entries",
        )
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, entry in enumerate(raw_reasons):
        if not isinstance(entry, Mapping):
            raise V09TrueStanceError(
                400,
                "INVALID_REASONS",
                f"{field_name}[{idx}] must be an object with facet and text_cn",
            )
        facet = _clean_text_field(
            entry.get("facet") or "general",
            field_name=f"{field_name}[{idx}].facet",
            min_len=1,
            max_len=48,
        )
        text_cn = _clean_text_field(
            entry.get("text_cn"),
            field_name=f"{field_name}[{idx}].text_cn",
            min_len=2,
            max_len=200,
        )
        evidence_ref = _clean_optional_ref(
            entry.get("evidence_ref"),
            field_name=f"{field_name}[{idx}].evidence_ref",
            max_len=160,
        )
        raw_rid = entry.get("reason_id")
        if raw_rid is not None and str(raw_rid).strip():
            reason_id = _validate_id_token(raw_rid, field_name=f"{field_name}[{idx}].reason_id")
        else:
            digest = hashlib.sha256(f"{idx}|{facet}|{text_cn}".encode("utf-8")).hexdigest()[:8]
            reason_id = f"r_{idx + 1}_{digest}"
        if reason_id in seen_ids:
            reason_id = f"{reason_id}_{idx + 1}"
        seen_ids.add(reason_id)
        normalized.append({
            "reason_id": reason_id,
            "facet": facet,
            "text_cn": text_cn,
            "evidence_ref": evidence_ref,
        })
    return normalized


def _extract_choice_option_id(
    payload: Mapping[str, Any],
    candidate_keys: tuple[str, ...],
    valid_option_ids: set[str],
    *,
    allow_undecided: bool = True,
) -> str:
    chosen: Optional[str] = None
    for key in candidate_keys:
        val = payload.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            continue
        cleaned = _validate_id_token(val, field_name=key)
        if chosen is not None and cleaned != chosen:
            raise V09TrueStanceError(
                400,
                "INVALID_OPTION_ID",
                f"Conflicting option selection fields supplied ({chosen!r} vs {cleaned!r})",
            )
        chosen = cleaned
    if chosen is None:
        raise V09TrueStanceError(
            400,
            "INVALID_OPTION_ID",
            f"One of {candidate_keys!r} is required",
        )
    if chosen == BLIND_CHOICE_UNDECIDED and allow_undecided:
        return BLIND_CHOICE_UNDECIDED
    if chosen not in valid_option_ids:
        raise V09TrueStanceError(
            400,
            "INVALID_OPTION_ID",
            f"Option {chosen!r} is not in session options {sorted(valid_option_ids)!r}",
        )
    return chosen


def _trim_blind_choices_in_place(choices: dict[str, dict[str, Any]]) -> None:
    while len(choices) > MAX_BLIND_CHOICES:
        # Prefer evicting oldest RESOLVED sessions before active ones.
        resolved_items = [
            (cid, row) for cid, row in choices.items()
            if isinstance(row, Mapping) and row.get("status") == "RESOLVED"
        ]
        pool = resolved_items if resolved_items else list(choices.items())
        oldest_cid, _ = min(
            pool,
            key=lambda kv: (
                str(kv[1].get("updated_at") or kv[1].get("opened_at") or ""),
                str(kv[0]),
            ),
        )
        del choices[oldest_cid]


def _trim_growth_stances_in_place(stances: dict[str, dict[str, Any]]) -> None:
    while len(stances) > MAX_GROWTH_STANCES:
        oldest_sid, _ = min(
            stances.items(),
            key=lambda kv: (
                str(kv[1].get("updated_at") or kv[1].get("created_at") or ""),
                str(kv[0]),
            ),
        )
        del stances[oldest_sid]


# ---------------------------------------------------------------------------
# Sub-Domain 1 Projection (Asymmetric Pre-Reveal Masking & Live Seal Check)
# ---------------------------------------------------------------------------


def _project_blind_choice(record: Mapping[str, Any], *, actor_side: str) -> dict[str, Any]:
    """Project a blind choice record for ``actor_side`` with strict pre-reveal masking."""
    status = str(record.get("status") or "OPEN_AWAITING_BOTH")
    subj_committed = bool(record.get("subject_committed"))
    part_committed = bool(record.get("partner_committed"))

    base: dict[str, Any] = {
        "choice_id": str(record.get("choice_id") or ""),
        "topic_cn": str(record.get("topic_cn") or ""),
        "prompt_cn": str(record.get("prompt_cn") or ""),
        "options": copy.deepcopy(list(record.get("options") or [])),
        "context_ref": record.get("context_ref"),
        "opened_by": str(record.get("opened_by") or "partner"),
        "opened_at": record.get("opened_at"),
        "updated_at": record.get("updated_at"),
        "status": status,
        "subject_committed": subj_committed,
        "partner_committed": part_committed,
        "revealed_at": record.get("revealed_at") if status in POST_REVEAL_STATUSES else None,
    }

    if status in POST_REVEAL_STATUSES:
        init_choice = record.get("initial_subject_choice_id")
        init_reasons = copy.deepcopy(list(record.get("initial_subject_reasons") or []))
        committed_at = str(record.get("subject_committed_at") or "")
        nonce = str(record.get("subject_commit_nonce") or "")
        stored_sha = str(record.get("subject_commit_sha256") or "")
        recomputed = (
            compute_subject_commit_sha256(
                choice_id=str(init_choice or ""),
                reasons=init_reasons,
                committed_at=committed_at,
                nonce=nonce,
            )
            if (init_choice is not None and committed_at and nonce)
            else ""
        )
        seal_verified = bool(
            len(stored_sha) == 64
            and len(recomputed) == 64
            and hmac.compare_digest(recomputed, stored_sha)
        )
        base.update({
            "subject_commit_sha256": stored_sha or None,
            "subject_committed_at": committed_at or None,
            "subject_commit_nonce": nonce or None,
            "initial_subject_choice_id": init_choice,
            "subject_choice_id": init_choice,
            "initial_subject_reasons": init_reasons,
            "subject_reasons": copy.deepcopy(init_reasons),
            "commit_seal_verified": seal_verified,
            "seal_status": (
                SEAL_STATUS_REVEALED_VERIFIED if seal_verified else SEAL_STATUS_REVEALED_TAMPERED
            ),
            "partner_choice_id": record.get("partner_choice_id"),
            "partner_reason_cn": str(record.get("partner_reason_cn") or ""),
            "partner_committed_at": record.get("partner_committed_at"),
            "discussion_notes": copy.deepcopy(list(record.get("discussion_notes") or [])),
            "resolution_mode": record.get("resolution_mode"),
            "adopted_action_choice_id": record.get("adopted_action_choice_id"),
            "preferred_judgment_choice_id": record.get("preferred_judgment_choice_id"),
            "judgment_flipped": bool(record.get("judgment_flipped")),
            "retained_reservations": copy.deepcopy(list(record.get("retained_reservations") or [])),
            "prior_reasons_history": copy.deepcopy(list(record.get("prior_reasons_history") or [])),
            "new_evidence": copy.deepcopy(record.get("new_evidence")),
            "what_changed_mind_cn": str(record.get("what_changed_mind_cn") or ""),
            "resolution_note_cn": str(record.get("resolution_note_cn") or ""),
            "resolved_at": record.get("resolved_at"),
        })
        return base

    # Pre-reveal states: strictly enforce asymmetric masking!
    if status == "OPEN_SUBJECT_COMMITTED":
        seal_status = SEAL_STATUS_SEALED_UNTIL_PARTNER
    elif status == "OPEN_PARTNER_COMMITTED":
        seal_status = SEAL_STATUS_SEALED_UNTIL_SUBJECT
    else:
        seal_status = SEAL_STATUS_UNCOMMITTED

    if actor_side == "partner":
        # Partner NEVER sees subject's choice, reasons, or nonce before submitting.
        base.update({
            "subject_commit_sha256": record.get("subject_commit_sha256") if subj_committed else None,
            "subject_committed_at": record.get("subject_committed_at") if subj_committed else None,
            "subject_commit_nonce": None,
            "initial_subject_choice_id": None,
            "subject_choice_id": None,
            "initial_subject_reasons": [],
            "subject_reasons": [],
            "commit_seal_verified": None,
            "seal_status": seal_status,
            "partner_choice_id": record.get("partner_choice_id") if part_committed else None,
            "partner_reason_cn": str(record.get("partner_reason_cn") or "") if part_committed else "",
            "partner_committed_at": record.get("partner_committed_at") if part_committed else None,
        })
    else:
        # Subject NEVER sees partner's choice or reason before committing TA's own choice.
        init_choice = record.get("initial_subject_choice_id") if subj_committed else None
        init_reasons = (
            copy.deepcopy(list(record.get("initial_subject_reasons") or []))
            if subj_committed
            else []
        )
        base.update({
            "subject_commit_sha256": record.get("subject_commit_sha256") if subj_committed else None,
            "subject_committed_at": record.get("subject_committed_at") if subj_committed else None,
            "subject_commit_nonce": record.get("subject_commit_nonce") if subj_committed else None,
            "initial_subject_choice_id": init_choice,
            "subject_choice_id": init_choice,
            "initial_subject_reasons": init_reasons,
            "subject_reasons": copy.deepcopy(init_reasons),
            "commit_seal_verified": None,
            "seal_status": seal_status,
            "partner_choice_id": None,
            "partner_reason_cn": "",
            "partner_committed_at": None,
        })

    base.update({
        "discussion_notes": [],
        "resolution_mode": None,
        "adopted_action_choice_id": None,
        "preferred_judgment_choice_id": None,
        "judgment_flipped": False,
        "retained_reservations": [],
        "prior_reasons_history": [],
        "new_evidence": None,
        "what_changed_mind_cn": "",
        "resolution_note_cn": "",
        "resolved_at": None,
    })
    return base


def _project_growth_stance(record: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(record))


# ---------------------------------------------------------------------------
# Sub-Domain 1 Action Handlers (Blind Simultaneous Choice)
# ---------------------------------------------------------------------------


def _handle_open_blind_choice(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_str: str,
) -> dict[str, Any]:
    if payload.get("clear_reservations"):
        raise V09TrueStanceError(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "clear_reservations is forbidden",
        )
    topic_cn = _clean_text_field(payload.get("topic_cn"), field_name="topic_cn", min_len=2, max_len=120)
    prompt_cn = _clean_text_field(payload.get("prompt_cn"), field_name="prompt_cn", min_len=0, max_len=240)
    options = _normalize_choice_options(payload.get("options"))
    context_ref = _clean_optional_ref(payload.get("context_ref"), field_name="context_ref", max_len=160)

    choices = state["blind_choices"]
    custom_id = _validate_optional_id_token(payload.get("choice_id"), field_name="choice_id")
    if custom_id is not None:
        if custom_id in choices:
            raise V09TrueStanceError(
                409,
                "BLIND_CHOICE_ALREADY_EXISTS",
                f"Blind choice {custom_id!r} already exists",
            )
        choice_id = custom_id
    else:
        seed = f"{topic_cn}|{now_str}|{len(choices)}|{secrets.token_hex(4)}"
        choice_id = _make_short_id("bc", seed, choices)

    record: dict[str, Any] = {
        "choice_id": choice_id,
        "topic_cn": topic_cn,
        "prompt_cn": prompt_cn,
        "options": options,
        "context_ref": context_ref,
        "opened_by": actor_side,
        "opened_at": now_str,
        "updated_at": now_str,
        "status": "OPEN_AWAITING_BOTH",
        "subject_committed": False,
        "initial_subject_choice_id": None,
        "initial_subject_reasons": [],
        "subject_committed_at": None,
        "subject_commit_nonce": None,
        "subject_commit_sha256": None,
        "partner_committed": False,
        "partner_choice_id": None,
        "partner_reason_cn": "",
        "partner_committed_at": None,
        "revealed_at": None,
        "discussion_notes": [],
        "resolution_mode": None,
        "adopted_action_choice_id": None,
        "preferred_judgment_choice_id": None,
        "judgment_flipped": False,
        "retained_reservations": [],
        "prior_reasons_history": [],
        "new_evidence": None,
        "what_changed_mind_cn": "",
        "resolution_note_cn": "",
        "resolved_at": None,
    }
    choices[choice_id] = record
    _trim_blind_choices_in_place(choices)
    return {
        "ok": True,
        "action": "open_blind_choice",
        "choice_id": choice_id,
        "blind_choice": _project_blind_choice(record, actor_side=actor_side),
    }


def _handle_subject_commit_blind_choice(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_str: str,
) -> dict[str, Any]:
    if payload.get("clear_reservations"):
        raise V09TrueStanceError(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "clear_reservations is forbidden",
        )
    choice_id = _validate_id_token(payload.get("choice_id"), field_name="choice_id")
    record = state["blind_choices"].get(choice_id)
    if not isinstance(record, dict):
        raise V09TrueStanceError(404, "BLIND_CHOICE_NOT_FOUND", f"Unknown blind choice: {choice_id}")

    status = str(record.get("status") or "")
    if record.get("subject_committed") or status not in ("OPEN_AWAITING_BOTH", "OPEN_PARTNER_COMMITTED"):
        raise V09TrueStanceError(
            409,
            "SUBJECT_ALREADY_COMMITTED",
            f"Subject cannot commit in status {status!r}",
        )

    valid_option_ids = {str(opt["option_id"]) for opt in record.get("options") or []}
    selected = _extract_choice_option_id(
        payload,
        ("selected_choice_id", "subject_choice_id", "initial_choice_id", "option_id"),
        valid_option_ids,
        allow_undecided=True,
    )

    raw_reasons = payload.get("reasons")
    if raw_reasons is None:
        raw_reasons = payload.get("subject_reasons")
    if raw_reasons is None:
        raw_reasons = payload.get("initial_subject_reasons")

    min_reasons = 0 if selected == BLIND_CHOICE_UNDECIDED else MIN_REASONS_PER_CHOICE
    reasons = _normalize_reasons_list(
        raw_reasons,
        min_count=min_reasons,
        max_count=MAX_REASONS_PER_CHOICE,
        field_name="reasons",
    )

    raw_nonce = payload.get("commit_nonce") or payload.get("nonce")
    if raw_nonce is not None and str(raw_nonce).strip():
        nonce = _clean_text_field(raw_nonce, field_name="commit_nonce", min_len=4, max_len=128)
    else:
        nonce = secrets.token_hex(16)

    seal_sha = compute_subject_commit_sha256(
        choice_id=selected,
        reasons=reasons,
        committed_at=now_str,
        nonce=nonce,
    )

    record["subject_committed"] = True
    record["initial_subject_choice_id"] = selected
    record["initial_subject_reasons"] = reasons
    record["subject_committed_at"] = now_str
    record["subject_commit_nonce"] = nonce
    record["subject_commit_sha256"] = seal_sha
    record["preferred_judgment_choice_id"] = selected
    record["updated_at"] = now_str

    if record.get("partner_committed"):
        record["status"] = "REVEALED"
        record["revealed_at"] = now_str
    else:
        record["status"] = "OPEN_SUBJECT_COMMITTED"

    return {
        "ok": True,
        "action": "subject_commit_blind_choice",
        "choice_id": choice_id,
        "status": record["status"],
        "subject_commit_sha256": seal_sha,
        "blind_choice": _project_blind_choice(record, actor_side=actor_side),
    }


def _handle_partner_submit_blind_choice(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_str: str,
) -> dict[str, Any]:
    if payload.get("clear_reservations"):
        raise V09TrueStanceError(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "clear_reservations is forbidden",
        )
    choice_id = _validate_id_token(payload.get("choice_id"), field_name="choice_id")
    record = state["blind_choices"].get(choice_id)
    if not isinstance(record, dict):
        raise V09TrueStanceError(404, "BLIND_CHOICE_NOT_FOUND", f"Unknown blind choice: {choice_id}")

    status = str(record.get("status") or "")
    if record.get("partner_committed") or status not in ("OPEN_AWAITING_BOTH", "OPEN_SUBJECT_COMMITTED"):
        raise V09TrueStanceError(
            409,
            "PARTNER_ALREADY_COMMITTED",
            f"Partner cannot submit in status {status!r}",
        )

    valid_option_ids = {str(opt["option_id"]) for opt in record.get("options") or []}
    selected = _extract_choice_option_id(
        payload,
        ("selected_choice_id", "partner_choice_id", "option_id"),
        valid_option_ids,
        allow_undecided=True,
    )
    reason_cn = _clean_text_field(
        payload.get("partner_reason_cn") if payload.get("partner_reason_cn") is not None else payload.get("reason_cn"),
        field_name="partner_reason_cn",
        min_len=0,
        max_len=240,
    )

    record["partner_committed"] = True
    record["partner_choice_id"] = selected
    record["partner_reason_cn"] = reason_cn
    record["partner_committed_at"] = now_str
    record["updated_at"] = now_str

    if record.get("subject_committed"):
        record["status"] = "REVEALED"
        record["revealed_at"] = now_str
    else:
        record["status"] = "OPEN_PARTNER_COMMITTED"

    return {
        "ok": True,
        "action": "partner_submit_blind_choice",
        "choice_id": choice_id,
        "status": record["status"],
        "blind_choice": _project_blind_choice(record, actor_side=actor_side),
    }


def _handle_partner_discuss_blind_choice(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_str: str,
) -> dict[str, Any]:
    if payload.get("clear_reservations"):
        raise V09TrueStanceError(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "clear_reservations is forbidden",
        )
    choice_id = _validate_id_token(payload.get("choice_id"), field_name="choice_id")
    record = state["blind_choices"].get(choice_id)
    if not isinstance(record, dict):
        raise V09TrueStanceError(404, "BLIND_CHOICE_NOT_FOUND", f"Unknown blind choice: {choice_id}")

    status = str(record.get("status") or "")
    if status in PRE_REVEAL_STATUSES:
        raise V09TrueStanceError(
            409,
            "BLIND_CHOICE_NOT_REVEALED",
            "Cannot discuss blind choice before both sides have revealed",
        )
    if status == "RESOLVED":
        raise V09TrueStanceError(
            409,
            "BLIND_CHOICE_ALREADY_RESOLVED",
            "Blind choice is already resolved",
        )

    raw_msg = (
        payload.get("message_cn")
        if payload.get("message_cn") is not None
        else (payload.get("note_cn") if payload.get("note_cn") is not None else payload.get("argument_cn"))
    )
    message_cn = _clean_text_field(raw_msg, field_name="message_cn", min_len=2, max_len=300)
    evidence_ref = _clean_optional_ref(payload.get("evidence_ref"), field_name="evidence_ref", max_len=160)
    insists_raw = payload.get("insists_on_partner_choice", False)
    if not isinstance(insists_raw, bool):
        raise V09TrueStanceError(400, "INVALID_FIELDS", "Field 'insists_on_partner_choice' must be a bool")

    notes = record.setdefault("discussion_notes", [])
    note_id = f"dn_{len(notes) + 1}_{hashlib.sha256(f'{now_str}|{message_cn}'.encode('utf-8')).hexdigest()[:8]}"
    notes.append({
        "note_id": note_id,
        "speaker": "partner",
        "message_cn": message_cn,
        "evidence_ref": evidence_ref,
        "insists_on_partner_choice": insists_raw,
        "created_at": now_str,
    })
    if len(notes) > MAX_DISCUSSION_NOTES:
        del notes[: len(notes) - MAX_DISCUSSION_NOTES]

    record["status"] = "DISCUSSED"
    record["updated_at"] = now_str

    return {
        "ok": True,
        "action": "partner_discuss_blind_choice",
        "choice_id": choice_id,
        "note_id": note_id,
        "status": record["status"],
        "blind_choice": _project_blind_choice(record, actor_side=actor_side),
    }


def _copy_initial_reasons_as_reservations(initial_reasons: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    reservations: list[dict[str, Any]] = []
    for row in initial_reasons:
        if not isinstance(row, Mapping):
            continue
        reservations.append({
            "reason_id": str(row.get("reason_id") or ""),
            "facet": str(row.get("facet") or "general"),
            "text_cn": str(row.get("text_cn") or ""),
            "evidence_ref": row.get("evidence_ref"),
            "preserved_from_initial": True,
            "refuted": False,
        })
    return reservations


def _handle_resolve_blind_choice(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_str: str,
) -> dict[str, Any]:
    if payload.get("clear_reservations"):
        raise V09TrueStanceError(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "Clearing initial reservations without new evidence is forbidden",
        )

    choice_id = _validate_id_token(payload.get("choice_id"), field_name="choice_id")
    record = state["blind_choices"].get(choice_id)
    if not isinstance(record, dict):
        raise V09TrueStanceError(404, "BLIND_CHOICE_NOT_FOUND", f"Unknown blind choice: {choice_id}")

    status = str(record.get("status") or "")
    if status in PRE_REVEAL_STATUSES:
        raise V09TrueStanceError(
            409,
            "BLIND_CHOICE_NOT_REVEALED",
            "Blind choice cannot be resolved before both sides have revealed",
        )
    if status == "RESOLVED":
        raise V09TrueStanceError(
            409,
            "BLIND_CHOICE_ALREADY_RESOLVED",
            "Blind choice is already resolved",
        )

    raw_mode = str(payload.get("resolution_mode") or "").strip().upper()
    if raw_mode not in RESOLUTION_MODES:
        raise V09TrueStanceError(
            400,
            "INVALID_RESOLUTION_MODE",
            f"resolution_mode must be one of {RESOLUTION_MODES!r}",
        )

    resolution_note_cn = _clean_text_field(
        payload.get("resolution_note_cn"),
        field_name="resolution_note_cn",
        min_len=0,
        max_len=240,
    )
    valid_option_ids = {str(opt["option_id"]) for opt in record.get("options") or []}
    init_choice = str(record.get("initial_subject_choice_id") or "")
    init_reasons = list(record.get("initial_subject_reasons") or [])
    partner_choice = str(record.get("partner_choice_id") or "")

    req_pref = payload.get("preferred_judgment_choice_id")
    if req_pref is not None and str(req_pref).strip():
        req_pref_clean = _validate_id_token(req_pref, field_name="preferred_judgment_choice_id")
    else:
        req_pref_clean = None

    if raw_mode == "ACCEPT_CHOICE_KEEP_REASONS":
        if payload.get("judgment_flipped") is True:
            raise V09TrueStanceError(
                409,
                "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
                "Cannot flip judgment in ACCEPT_CHOICE_KEEP_REASONS without new evidence",
            )
        if req_pref_clean is not None and req_pref_clean != init_choice:
            raise V09TrueStanceError(
                409,
                "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
                "Cannot overwrite preferred_judgment_choice_id without new evidence",
            )
        # Reject explicit attempt to erase reservations via empty list or dropping initial reasons.
        if "retained_reservations" in payload:
            raw_res = payload.get("retained_reservations")
            if not isinstance(raw_res, Sequence) or isinstance(raw_res, (str, bytes)) or len(raw_res) == 0:
                raise V09TrueStanceError(
                    409,
                    "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
                    "retained_reservations cannot be emptied in ACCEPT_CHOICE_KEEP_REASONS",
                )
            supplied_texts = {
                str(r.get("text_cn") or "").strip()
                for r in raw_res
                if isinstance(r, Mapping)
            }
            supplied_ids = {
                str(r.get("reason_id") or "").strip()
                for r in raw_res
                if isinstance(r, Mapping)
            }
            for orig in init_reasons:
                oid = str(orig.get("reason_id") or "").strip()
                otxt = str(orig.get("text_cn") or "").strip()
                if (oid and oid not in supplied_ids) and (otxt and otxt not in supplied_texts):
                    raise V09TrueStanceError(
                        409,
                        "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
                        "All unrefuted initial_subject_reasons must remain in retained_reservations",
                    )

        retained = _copy_initial_reasons_as_reservations(init_reasons)
        if not retained:
            # Handle UNDECIDED with 0 initial reasons while keeping retained_reservations non-empty.
            fallback_text = resolution_note_cn or "先照伴侣选定的安排执行，但最初的犹豫并未消除"
            retained.append({
                "reason_id": "r_undecided_kept",
                "facet": "initial_hesitation",
                "text_cn": fallback_text,
                "evidence_ref": None,
                "preserved_from_initial": True,
                "refuted": False,
            })

        record["resolution_mode"] = raw_mode
        record["adopted_action_choice_id"] = partner_choice
        record["preferred_judgment_choice_id"] = init_choice
        record["judgment_flipped"] = False
        record["retained_reservations"] = retained
        record["resolution_note_cn"] = resolution_note_cn

    elif raw_mode == "KEEP_OWN_STANCE":
        if payload.get("judgment_flipped") is True or (
            req_pref_clean is not None and req_pref_clean != init_choice
        ):
            raise V09TrueStanceError(
                409,
                "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
                "Cannot flip judgment in KEEP_OWN_STANCE",
            )
        record["resolution_mode"] = raw_mode
        record["adopted_action_choice_id"] = init_choice
        record["preferred_judgment_choice_id"] = init_choice
        record["judgment_flipped"] = False
        record["retained_reservations"] = _copy_initial_reasons_as_reservations(init_reasons)
        record["resolution_note_cn"] = resolution_note_cn

    elif raw_mode == "COMPROMISE_MIDDLE":
        if payload.get("judgment_flipped") is True or (
            req_pref_clean is not None and req_pref_clean != init_choice
        ):
            raise V09TrueStanceError(
                409,
                "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
                "Cannot silently flip preferred judgment in COMPROMISE_MIDDLE without new evidence",
            )
        raw_comp = payload.get("compromise_option_id") or payload.get("adopted_action_choice_id")
        if raw_comp is None or not str(raw_comp).strip():
            raise V09TrueStanceError(
                400,
                "INVALID_OPTION_ID",
                "COMPROMISE_MIDDLE requires compromise_option_id or adopted_action_choice_id",
            )
        comp_id = _validate_id_token(raw_comp, field_name="compromise_option_id")
        comp_label = _clean_text_field(
            payload.get("compromise_label_cn"),
            field_name="compromise_label_cn",
            min_len=0,
            max_len=80,
        )
        if comp_id not in valid_option_ids and not (comp_label or resolution_note_cn):
            raise V09TrueStanceError(
                400,
                "INVALID_OPTION_ID",
                f"compromise_option_id {comp_id!r} must match an option or include compromise_label_cn/resolution_note_cn",
            )

        retained = _copy_initial_reasons_as_reservations(init_reasons)
        extra_tradeoffs = payload.get("retained_reservations")
        if isinstance(extra_tradeoffs, Sequence) and not isinstance(extra_tradeoffs, (str, bytes)):
            existing_texts = {r["text_cn"] for r in retained}
            for idx, item in enumerate(extra_tradeoffs):
                if isinstance(item, Mapping):
                    txt = _clean_text_field(
                        item.get("text_cn"),
                        field_name=f"retained_reservations[{idx}].text_cn",
                        min_len=2,
                        max_len=200,
                    )
                    if txt not in existing_texts:
                        retained.append({
                            "reason_id": _validate_optional_id_token(
                                item.get("reason_id"),
                                field_name=f"retained_reservations[{idx}].reason_id",
                            )
                            or f"tradeoff_{idx + 1}",
                            "facet": _clean_text_field(
                                item.get("facet") or "tradeoff",
                                field_name=f"retained_reservations[{idx}].facet",
                                min_len=1,
                                max_len=48,
                            ),
                            "text_cn": txt,
                            "evidence_ref": _clean_optional_ref(
                                item.get("evidence_ref"),
                                field_name=f"retained_reservations[{idx}].evidence_ref",
                            ),
                            "preserved_from_initial": False,
                            "refuted": False,
                        })
                        existing_texts.add(txt)
        tradeoff_note = _clean_text_field(
            payload.get("tradeoff_note_cn"),
            field_name="tradeoff_note_cn",
            min_len=0,
            max_len=200,
        )
        if tradeoff_note:
            retained.append({
                "reason_id": "r_compromise_tradeoff",
                "facet": "tradeoff",
                "text_cn": tradeoff_note,
                "evidence_ref": None,
                "preserved_from_initial": False,
                "refuted": False,
            })

        record["resolution_mode"] = raw_mode
        record["adopted_action_choice_id"] = comp_id
        record["preferred_judgment_choice_id"] = init_choice
        record["judgment_flipped"] = False
        record["retained_reservations"] = retained
        record["resolution_note_cn"] = resolution_note_cn or comp_label

    else:  # REVISED_WITH_NEW_EVIDENCE
        raw_ev = payload.get("new_evidence")
        if not isinstance(raw_ev, Mapping):
            raise V09TrueStanceError(
                400,
                "INVALID_EVIDENCE",
                "REVISED_WITH_NEW_EVIDENCE requires new_evidence object",
            )
        ev_kind = str(raw_ev.get("evidence_kind") or "").strip().lower()
        if ev_kind not in NEW_EVIDENCE_KINDS:
            raise V09TrueStanceError(
                400,
                "INVALID_EVIDENCE",
                f"evidence_kind must be one of {NEW_EVIDENCE_KINDS!r}",
            )
        ev_summary = _clean_text_field(
            raw_ev.get("summary_cn"),
            field_name="new_evidence.summary_cn",
            min_len=2,
            max_len=300,
        )
        ev_ref = _clean_optional_ref(
            raw_ev.get("evidence_ref"),
            field_name="new_evidence.evidence_ref",
            max_len=160,
        )

        # Hard Coercion Filter: reject bare insistence / command phrases without
        # concrete new factual substance (>= 6 meaningful chars).
        is_coercion, cue = _detect_bare_coercion_or_insistence(ev_summary)
        if is_coercion:
            raise V09TrueStanceError(
                409,
                "BARE_INSISTENCE_NOT_EVIDENCE",
                f"Bare insistence or command cue ({cue}) is not valid new evidence",
            )

        what_changed = _clean_text_field(
            payload.get("what_changed_mind_cn"),
            field_name="what_changed_mind_cn",
            min_len=0,
            max_len=240,
        ) or ev_summary

        # Determine revised choice (defaults to partner_choice_id if omitted).
        raw_new_choice = (
            payload.get("adopted_action_choice_id")
            or payload.get("preferred_judgment_choice_id")
            or payload.get("new_choice_id")
            or partner_choice
        )
        new_choice = _validate_id_token(raw_new_choice, field_name="adopted_action_choice_id")
        if new_choice != BLIND_CHOICE_UNDECIDED and new_choice not in valid_option_ids:
            raise V09TrueStanceError(
                400,
                "INVALID_OPTION_ID",
                f"Revised choice {new_choice!r} is not in session options",
            )

        # Preserve initial_subject_reasons permanently in prior_reasons_history!
        history = record.setdefault("prior_reasons_history", [])
        history.append({
            "recorded_at": now_str,
            "prior_choice_id": init_choice,
            "prior_reasons": copy.deepcopy(init_reasons),
            "superseded_by_choice_id": new_choice,
            "evidence_kind": ev_kind,
            "summary_cn": ev_summary,
            "evidence_ref": ev_ref,
        })
        if len(history) > MAX_PRIOR_REASONS_HISTORY:
            del history[: len(history) - MAX_PRIOR_REASONS_HISTORY]

        remaining_reservations: list[dict[str, Any]] = []
        raw_remaining = payload.get("retained_reservations")
        if isinstance(raw_remaining, Sequence) and not isinstance(raw_remaining, (str, bytes)):
            remaining_reservations = _normalize_reasons_list(
                raw_remaining,
                min_count=0,
                max_count=MAX_REASONS_PER_CHOICE,
                field_name="retained_reservations",
            )

        record["resolution_mode"] = raw_mode
        record["adopted_action_choice_id"] = new_choice
        record["preferred_judgment_choice_id"] = new_choice
        record["judgment_flipped"] = new_choice != init_choice
        record["new_evidence"] = {
            "evidence_kind": ev_kind,
            "summary_cn": ev_summary,
            "evidence_ref": ev_ref,
        }
        record["what_changed_mind_cn"] = what_changed
        record["retained_reservations"] = remaining_reservations
        record["resolution_note_cn"] = resolution_note_cn

    record["status"] = "RESOLVED"
    record["resolved_at"] = now_str
    record["updated_at"] = now_str

    return {
        "ok": True,
        "action": "resolve_blind_choice",
        "choice_id": choice_id,
        "resolution_mode": record["resolution_mode"],
        "status": record["status"],
        "blind_choice": _project_blind_choice(record, actor_side=actor_side),
    }


# ---------------------------------------------------------------------------
# Sub-Domain 2 Action Handlers (Growth-from-Blank)
# ---------------------------------------------------------------------------


def _snapshot_stance_lineage(stance: Mapping[str, Any], *, recorded_at: str, change_reason_cn: str) -> dict[str, Any]:
    return {
        "version": int(stance.get("version") or 1),
        "stance_id": str(stance.get("stance_id") or ""),
        "status": str(stance.get("status") or "UNFORMED"),
        "stance_summary_cn": str(stance.get("stance_summary_cn") or ""),
        "provenance_tier": stance.get("provenance_tier"),
        "evidence_refs": copy.deepcopy(list(stance.get("evidence_refs") or [])),
        "reasons": copy.deepcopy(list(stance.get("reasons") or [])),
        "doubts_cn": copy.deepcopy(list(stance.get("doubts_cn") or [])),
        "recorded_at": recorded_at,
        "change_reason_cn": change_reason_cn,
    }


def _handle_register_unformed_topic(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_str: str,
) -> dict[str, Any]:
    if payload.get("clear_reservations"):
        raise V09TrueStanceError(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "clear_reservations is forbidden",
        )
    topic_cn = _clean_text_field(payload.get("topic_cn"), field_name="topic_cn", min_len=2, max_len=120)
    prompt_cn = _clean_text_field(payload.get("prompt_cn"), field_name="prompt_cn", min_len=0, max_len=240)
    summary_cn = _clean_text_field(
        payload.get("stance_summary_cn"),
        field_name="stance_summary_cn",
        min_len=0,
        max_len=200,
    ) or DEFAULT_UNFORMED_SUMMARY_CN
    topic_key = _validate_optional_id_token(payload.get("topic_key"), field_name="topic_key")

    if actor_side == "partner":
        shared_with_partner = True
    else:
        raw_shared = payload.get("shared_with_partner", False)
        if not isinstance(raw_shared, bool):
            raise V09TrueStanceError(400, "INVALID_FIELDS", "Field 'shared_with_partner' must be a bool")
        shared_with_partner = raw_shared

    stances = state["growth_stances"]
    custom_id = _validate_optional_id_token(payload.get("stance_id"), field_name="stance_id")
    if custom_id is not None:
        if custom_id in stances:
            raise V09TrueStanceError(
                409,
                "STANCE_ALREADY_EXISTS",
                f"Growth stance {custom_id!r} already exists",
            )
        stance_id = custom_id
    else:
        seed = f"{topic_cn}|{topic_key or ''}|{now_str}|{len(stances)}|{secrets.token_hex(4)}"
        stance_id = _make_short_id("st", seed, stances)

    record: dict[str, Any] = {
        "stance_id": stance_id,
        "topic_key": topic_key or stance_id,
        "topic_cn": topic_cn,
        "prompt_cn": prompt_cn,
        "status": "UNFORMED",
        "stance_summary_cn": summary_cn,
        "provenance_tier": None,
        "receipt_ref": None,
        "material_ref": None,
        "page_or_anchor": None,
        "evidence_refs": [],
        "reasons": [],
        "doubts_cn": [],
        "shared_with_partner": shared_with_partner,
        "partner_persuasions": [],
        "latest_persuasion_verdict": None,
        "replaces_stance_id": None,
        "prior_stances": [],
        "version": 1,
        "created_by": actor_side,
        "created_at": now_str,
        "updated_at": now_str,
    }
    stances[stance_id] = record
    _trim_growth_stances_in_place(stances)
    return {
        "ok": True,
        "action": "register_unformed_topic",
        "stance_id": stance_id,
        "growth_stance": _project_growth_stance(record),
    }


def _handle_advance_stance_from_experience(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_str: str,
) -> dict[str, Any]:
    if payload.get("clear_reservations"):
        raise V09TrueStanceError(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "clear_reservations is forbidden",
        )
    stance_id = _validate_id_token(payload.get("stance_id"), field_name="stance_id")
    stance = state["growth_stances"].get(stance_id)
    if not isinstance(stance, dict):
        raise V09TrueStanceError(404, "STANCE_NOT_FOUND", f"Unknown stance_id: {stance_id}")

    target_status = str(payload.get("target_status") or "").strip().upper()
    if target_status not in ("TENTATIVE", "FORMED", "REVISED"):
        raise V09TrueStanceError(
            400,
            "INVALID_STANCE_STATUS",
            "target_status must be one of ('TENTATIVE', 'FORMED', 'REVISED')",
        )

    provenance_tier = str(payload.get("provenance_tier") or "").strip().lower()
    if provenance_tier not in PROVENANCE_TIERS:
        raise V09TrueStanceError(
            400,
            "INVALID_PROVENANCE_TIER",
            f"provenance_tier must be one of {PROVENANCE_TIERS!r}",
        )

    stance_summary_cn = _clean_text_field(
        payload.get("stance_summary_cn"),
        field_name="stance_summary_cn",
        min_len=2,
        max_len=240,
    )
    change_reason_cn = _clean_text_field(
        payload.get("change_reason_cn"),
        field_name="change_reason_cn",
        min_len=0,
        max_len=240,
    )

    cur_status = str(stance.get("status") or "UNFORMED")
    if target_status == "REVISED" and cur_status not in ("FORMED", "REVISED"):
        raise V09TrueStanceError(
            409,
            "CANNOT_REVISE_UNFORMED_STANCE",
            f"Cannot transition directly from {cur_status!r} to 'REVISED'",
        )

    receipt_ref = _clean_optional_ref(
        payload.get("receipt_ref") or payload.get("episode_ref"),
        field_name="receipt_ref",
    )
    material_ref = _clean_optional_ref(payload.get("material_ref"), field_name="material_ref")
    page_or_anchor = _clean_optional_ref(payload.get("page_or_anchor"), field_name="page_or_anchor")

    # Enforce 4 epistemic provenance tiers strictly.
    if provenance_tier == "firsthand_lived":
        if not receipt_ref:
            raise V09TrueStanceError(
                400,
                "MISSING_PROVENANCE_REF",
                "firsthand_lived requires non-empty receipt_ref or episode_ref",
            )
    elif provenance_tier == "read_knowledge":
        if not material_ref:
            raise V09TrueStanceError(
                400,
                "MISSING_PROVENANCE_REF",
                "read_knowledge requires non-empty material_ref",
            )
    elif provenance_tier == "tentative_guess":
        if target_status in ("FORMED", "REVISED") or cur_status in ("FORMED", "REVISED"):
            raise V09TrueStanceError(
                409,
                "TENTATIVE_GUESS_CANNOT_FORM_STANCE",
                "tentative_guess cannot promote past TENTATIVE or overwrite a FORMED/REVISED stance",
            )
    elif provenance_tier == "partner_heard":
        partner_arg = _clean_text_field(
            payload.get("partner_argument_cn"),
            field_name="partner_argument_cn",
            min_len=0,
            max_len=300,
        )
        check_text = partner_arg or change_reason_cn or stance_summary_cn
        is_coercion, cue = _detect_bare_coercion_or_insistence(check_text)
        if is_coercion or payload.get("tone_pressure") or payload.get("demand_agree"):
            raise V09TrueStanceError(
                409,
                "COERCION_NOT_PERSUASION",
                f"Bare pressure or coercion ({cue or 'tone_pressure'}) cannot form a stance",
            )
        if target_status in ("FORMED", "REVISED"):
            raise V09TrueStanceError(
                409,
                "COERCION_NOT_PERSUASION",
                "partner_heard alone cannot auto-convert a stance to FORMED/REVISED without firsthand_lived or read_knowledge",
            )

    reasons = (
        _normalize_reasons_list(
            payload.get("reasons"),
            min_count=0,
            max_count=MAX_REASONS_PER_CHOICE,
            field_name="reasons",
        )
        if payload.get("reasons") is not None
        else copy.deepcopy(list(stance.get("reasons") or []))
    )

    doubts_cn = copy.deepcopy(list(stance.get("doubts_cn") or []))
    raw_doubts = payload.get("doubts_cn")
    if raw_doubts is not None:
        if not isinstance(raw_doubts, Sequence) or isinstance(raw_doubts, (str, bytes)):
            raise V09TrueStanceError(400, "INVALID_FIELDS", "Field 'doubts_cn' must be a list of strings")
        doubts_cn = []
        for idx, item in enumerate(raw_doubts[:MAX_DOUBTS_PER_STANCE]):
            cleaned = _clean_text_field(item, field_name=f"doubts_cn[{idx}]", min_len=1, max_len=200)
            if cleaned not in doubts_cn:
                doubts_cn.append(cleaned)

    replaces_id = _validate_optional_id_token(
        payload.get("replaces_stance_id"),
        field_name="replaces_stance_id",
    )

    # Record immutable prior snapshot before advancing.
    prior_list = stance.setdefault("prior_stances", [])
    prior_list.append(
        _snapshot_stance_lineage(
            stance,
            recorded_at=now_str,
            change_reason_cn=change_reason_cn or f"{cur_status}->{target_status}:{provenance_tier}",
        )
    )
    if len(prior_list) > MAX_PRIOR_STANCES:
        del prior_list[: len(prior_list) - MAX_PRIOR_STANCES]

    old_version = int(stance.get("version") or 1)
    if target_status == "REVISED":
        stance["replaces_stance_id"] = replaces_id or f"{stance_id}@v{old_version}"
    elif replaces_id is not None:
        stance["replaces_stance_id"] = replaces_id

    ev_refs = list(stance.get("evidence_refs") or [])
    for ref in (receipt_ref, material_ref, page_or_anchor):
        if ref and ref not in ev_refs:
            ev_refs.append(ref)

    if "shared_with_partner" in payload:
        raw_shared = payload.get("shared_with_partner")
        if not isinstance(raw_shared, bool):
            raise V09TrueStanceError(400, "INVALID_FIELDS", "Field 'shared_with_partner' must be a bool")
        stance["shared_with_partner"] = raw_shared

    stance["status"] = target_status
    stance["stance_summary_cn"] = stance_summary_cn
    stance["provenance_tier"] = provenance_tier
    if receipt_ref is not None:
        stance["receipt_ref"] = receipt_ref
    if material_ref is not None:
        stance["material_ref"] = material_ref
    if page_or_anchor is not None:
        stance["page_or_anchor"] = page_or_anchor
    stance["evidence_refs"] = ev_refs
    stance["reasons"] = reasons
    stance["doubts_cn"] = doubts_cn
    stance["version"] = old_version + 1
    stance["updated_at"] = now_str

    return {
        "ok": True,
        "action": "advance_stance_from_experience",
        "stance_id": stance_id,
        "status": stance["status"],
        "provenance_tier": stance["provenance_tier"],
        "growth_stance": _project_growth_stance(stance),
    }


def _handle_partner_persuade_stance(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_str: str,
) -> dict[str, Any]:
    if payload.get("clear_reservations"):
        raise V09TrueStanceError(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "clear_reservations is forbidden",
        )
    stance_id = _validate_id_token(payload.get("stance_id"), field_name="stance_id")
    stance = state["growth_stances"].get(stance_id)
    # Withhold private stances from partner without confirming existence.
    if not isinstance(stance, dict) or not bool(stance.get("shared_with_partner")):
        raise V09TrueStanceError(404, "STANCE_NOT_FOUND", f"Unknown stance_id: {stance_id}")

    if payload.get("tone_pressure") or payload.get("demand_agree") or payload.get("promote_to_formed"):
        raise V09TrueStanceError(
            409,
            "COERCION_NOT_PERSUASION",
            "Pressure or forced stance promotion is not persuasion",
        )

    raw_arg = (
        payload.get("partner_argument_cn")
        if payload.get("partner_argument_cn") is not None
        else payload.get("argument_cn")
    )
    argument_cn = _clean_text_field(raw_arg, field_name="partner_argument_cn", min_len=2, max_len=300)
    evidence_ref = _clean_optional_ref(payload.get("evidence_ref"), field_name="evidence_ref", max_len=160)

    is_coercion, cue = _detect_bare_coercion_or_insistence(argument_cn)
    if is_coercion:
        raise V09TrueStanceError(
            409,
            "COERCION_NOT_PERSUASION",
            f"Bare pressure or command phrasing ({cue}) is rejected as persuasion",
        )

    persuasions = stance.setdefault("partner_persuasions", [])
    pid = f"ps_{len(persuasions) + 1}_{hashlib.sha256(f'{now_str}|{argument_cn}'.encode('utf-8')).hexdigest()[:8]}"
    entry = {
        "persuasion_id": pid,
        "partner_argument_cn": argument_cn,
        "evidence_ref": evidence_ref,
        "submitted_at": now_str,
        "evaluated": False,
        "persuasion_verdict": None,
        "subject_response_cn": "",
        "kept_doubt_cn": "",
        "evaluated_at": None,
    }
    persuasions.append(entry)
    if len(persuasions) > MAX_PERSUASION_RECORDS:
        del persuasions[: len(persuasions) - MAX_PERSUASION_RECORDS]

    # Partner persuasion NEVER auto-converts stance status to FORMED.
    stance["updated_at"] = now_str

    return {
        "ok": True,
        "action": "partner_persuade_stance",
        "stance_id": stance_id,
        "persuasion_id": pid,
        "status": stance["status"],
        "growth_stance": _project_growth_stance(stance),
    }


def _handle_evaluate_partner_persuasion(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_str: str,
) -> dict[str, Any]:
    if payload.get("clear_reservations"):
        raise V09TrueStanceError(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "clear_reservations is forbidden",
        )
    if payload.get("promote_to_formed"):
        raise V09TrueStanceError(
            409,
            "COERCION_NOT_PERSUASION",
            "Partner persuasion alone never auto-converts a stance to FORMED",
        )

    stance_id = _validate_id_token(payload.get("stance_id"), field_name="stance_id")
    stance = state["growth_stances"].get(stance_id)
    if not isinstance(stance, dict):
        raise V09TrueStanceError(404, "STANCE_NOT_FOUND", f"Unknown stance_id: {stance_id}")

    persuasions = stance.get("partner_persuasions")
    if not isinstance(persuasions, list) or not persuasions:
        raise V09TrueStanceError(
            404,
            "PERSUASION_NOT_FOUND",
            "No partner persuasion record exists on this stance",
        )

    req_pid = _validate_optional_id_token(payload.get("persuasion_id"), field_name="persuasion_id")
    target_entry: Optional[dict[str, Any]] = None
    if req_pid is not None:
        for row in persuasions:
            if isinstance(row, dict) and row.get("persuasion_id") == req_pid:
                target_entry = row
                break
    else:
        for row in reversed(persuasions):
            if isinstance(row, dict) and not row.get("evaluated"):
                target_entry = row
                break
        if target_entry is None and isinstance(persuasions[-1], dict):
            target_entry = persuasions[-1]

    if target_entry is None:
        raise V09TrueStanceError(404, "PERSUASION_NOT_FOUND", f"Unknown persuasion_id: {req_pid}")

    verdict = str(payload.get("persuasion_verdict") or "").strip().upper()
    if verdict not in PERSUASION_VERDICTS:
        raise V09TrueStanceError(
            400,
            "INVALID_PERSUASION_VERDICT",
            f"persuasion_verdict must be one of {PERSUASION_VERDICTS!r}",
        )

    subject_response_cn = _clean_text_field(
        payload.get("subject_response_cn"),
        field_name="subject_response_cn",
        min_len=0,
        max_len=240,
    )
    kept_doubt_cn = _clean_text_field(
        payload.get("kept_doubt_cn"),
        field_name="kept_doubt_cn",
        min_len=0,
        max_len=200,
    )
    new_summary_cn = _clean_text_field(
        payload.get("stance_summary_cn"),
        field_name="stance_summary_cn",
        min_len=0,
        max_len=240,
    )

    if verdict == "PARTIALLY_TAKEN_KEEP_DOUBT" and not kept_doubt_cn:
        kept_doubt_cn = DEFAULT_KEPT_DOUBT_CN

    target_entry["evaluated"] = True
    target_entry["persuasion_verdict"] = verdict
    target_entry["subject_response_cn"] = subject_response_cn
    target_entry["kept_doubt_cn"] = kept_doubt_cn
    target_entry["evaluated_at"] = now_str

    stance["latest_persuasion_verdict"] = verdict
    cur_status = str(stance.get("status") or "UNFORMED")

    if kept_doubt_cn:
        doubts = stance.setdefault("doubts_cn", [])
        if kept_doubt_cn not in doubts:
            doubts.append(kept_doubt_cn)
            if len(doubts) > MAX_DOUBTS_PER_STANCE:
                del doubts[: len(doubts) - MAX_DOUBTS_PER_STANCE]

    if verdict in ("PERSUADED_TENTATIVE", "PARTIALLY_TAKEN_KEEP_DOUBT"):
        if cur_status == "UNFORMED":
            prior_list = stance.setdefault("prior_stances", [])
            prior_list.append(
                _snapshot_stance_lineage(
                    stance,
                    recorded_at=now_str,
                    change_reason_cn=f"partner_persuasion:{verdict}",
                )
            )
            if len(prior_list) > MAX_PRIOR_STANCES:
                del prior_list[: len(prior_list) - MAX_PRIOR_STANCES]
            stance["status"] = "TENTATIVE"
            stance["provenance_tier"] = "partner_heard"
            if new_summary_cn:
                stance["stance_summary_cn"] = new_summary_cn
            elif subject_response_cn:
                stance["stance_summary_cn"] = subject_response_cn
            else:
                stance["stance_summary_cn"] = f"听了伴侣的说法后有了初步想法，但仍待亲身印证"
            if target_entry.get("evidence_ref"):
                ev_refs = stance.setdefault("evidence_refs", [])
                if target_entry["evidence_ref"] not in ev_refs:
                    ev_refs.append(target_entry["evidence_ref"])
            stance["version"] = int(stance.get("version") or 1) + 1
        elif cur_status == "TENTATIVE" and new_summary_cn:
            prior_list = stance.setdefault("prior_stances", [])
            prior_list.append(
                _snapshot_stance_lineage(
                    stance,
                    recorded_at=now_str,
                    change_reason_cn=f"partner_persuasion:{verdict}",
                )
            )
            if len(prior_list) > MAX_PRIOR_STANCES:
                del prior_list[: len(prior_list) - MAX_PRIOR_STANCES]
            stance["stance_summary_cn"] = new_summary_cn
            stance["version"] = int(stance.get("version") or 1) + 1
        # If cur_status is already FORMED/REVISED, partner persuasion never silently
        # flips or downgrades a firsthand/read stance.

    stance["updated_at"] = now_str

    return {
        "ok": True,
        "action": "evaluate_partner_persuasion",
        "stance_id": stance_id,
        "persuasion_id": target_entry["persuasion_id"],
        "persuasion_verdict": verdict,
        "status": stance["status"],
        "growth_stance": _project_growth_stance(stance),
    }


def _handle_set_stance_shared(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_str: str,
) -> dict[str, Any]:
    if payload.get("clear_reservations"):
        raise V09TrueStanceError(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "clear_reservations is forbidden",
        )
    stance_id = _validate_id_token(payload.get("stance_id"), field_name="stance_id")
    stance = state["growth_stances"].get(stance_id)
    if not isinstance(stance, dict):
        raise V09TrueStanceError(404, "STANCE_NOT_FOUND", f"Unknown stance_id: {stance_id}")

    raw_shared = payload.get("shared_with_partner")
    if not isinstance(raw_shared, bool):
        raise V09TrueStanceError(400, "INVALID_FIELDS", "Field 'shared_with_partner' must be a bool")

    stance["shared_with_partner"] = raw_shared
    stance["updated_at"] = now_str
    return {
        "ok": True,
        "action": "set_stance_shared",
        "stance_id": stance_id,
        "shared_with_partner": raw_shared,
        "growth_stance": _project_growth_stance(stance),
    }


# ---------------------------------------------------------------------------
# Public Write Entry Point
# ---------------------------------------------------------------------------


_ACTION_HANDLERS = {
    "open_blind_choice": _handle_open_blind_choice,
    "subject_commit_blind_choice": _handle_subject_commit_blind_choice,
    "partner_submit_blind_choice": _handle_partner_submit_blind_choice,
    "partner_discuss_blind_choice": _handle_partner_discuss_blind_choice,
    "resolve_blind_choice": _handle_resolve_blind_choice,
    "register_unformed_topic": _handle_register_unformed_topic,
    "advance_stance_from_experience": _handle_advance_stance_from_experience,
    "partner_persuade_stance": _handle_partner_persuade_stance,
    "evaluate_partner_persuasion": _handle_evaluate_partner_persuasion,
    "set_stance_shared": _handle_set_stance_shared,
}


def apply_true_stance_command(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Execute one atomic, idempotent, locked command on True Stance & Blind Choice state."""
    side = _normalize_actor_side(actor_side)
    if not isinstance(payload, Mapping):
        raise V09TrueStanceError(400, "INVALID_PAYLOAD", "Command payload must be a mapping")

    smuggled = _scan_forbidden_client_keys(payload)
    if smuggled is not None:
        raise V09TrueStanceError(
            400,
            "FORBIDDEN_CLIENT_KEY",
            f"Forbidden client key supplied: {smuggled}",
        )

    if side == "partner" and "clear_reservations" in payload:
        raise V09TrueStanceError(
            409,
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "clear_reservations is forbidden for partner requests",
        )

    action = str(payload.get("action") or "").strip()
    if action not in KNOWN_ACTIONS:
        raise V09TrueStanceError(400, "UNKNOWN_ACTION", f"Unsupported action: {action!r}")

    if side == "partner" and action not in PARTNER_ALLOWED_ACTIONS:
        raise V09TrueStanceError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} is restricted to subject",
        )
    if side == "subject" and action not in SUBJECT_ALLOWED_ACTIONS:
        raise V09TrueStanceError(
            403,
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} is restricted to partner",
        )

    _, now_str = _resolve_now(now_iso)
    eff_op_key = _validate_operation_key(payload.get("operation_key"))
    expected_revision = payload.get("expected_revision")
    if expected_revision is not None and (
        isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
    ):
        raise V09TrueStanceError(400, "INVALID_FIELDS", "Field 'expected_revision' must be an int")

    fingerprint_input = {
        "action": action,
        "actor_side": side,
        "payload": {
            k: v
            for k, v in payload.items()
            if k not in ("operation_key", "expected_revision")
        },
    }
    payload_hash = _tx.payload_fingerprint(fingerprint_input)

    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)
    _assert_no_symlink_chain(day_root, resolved_root, state_path)

    with _tx.state_file_lock(state_path):
        _assert_no_symlink_chain(day_root, resolved_root, state_path)
        state = _load_or_init_state_locked(state_path)

        if eff_op_key:
            reconcile_kind, hit = _tx.reconcile_evicted_or_active_op(
                resolved_root,
                state,
                eff_op_key,
                dict_key="operation_receipts",
                expected_revision=expected_revision,
                payload_hash=payload_hash,
            )
            if reconcile_kind == "conflict":
                raise V09TrueStanceError(
                    409,
                    "OPERATION_KEY_CONFLICT",
                    f"operation_key conflict: {eff_op_key}",
                )
            if reconcile_kind == "expired":
                raise V09TrueStanceError(
                    409,
                    "RETRY_WINDOW_EXPIRED",
                    f"operation_key expired: {eff_op_key}",
                )
            if reconcile_kind in ("hit_full", "hit_evicted") and isinstance(hit, Mapping):
                replayed = copy.deepcopy(dict(hit))
                replayed["already_applied"] = True
                replayed["llm_calls"] = 0
                return replayed

        handler = _ACTION_HANDLERS[action]
        result = handler(state, payload, actor_side=side, now_str=now_str)

        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = now_str

        result["schema"] = SCHEMA
        result["revision"] = state["revision"]
        result["updated_at"] = now_str
        result["actor_side"] = side
        result["already_applied"] = False
        result["llm_calls"] = 0
        if eff_op_key:
            result["operation_key"] = eff_op_key
            _tx.stamp_receipt(
                state,
                eff_op_key,
                result,
                revision=state["revision"],
                dict_key="operation_receipts",
                payload_hash=payload_hash,
                committed_at=now_str,
            )
            _tx.trim_receipts_by_op_seq(
                state,
                dict_key="operation_receipts",
                max_keep=MAX_OPERATION_RECEIPTS,
            )
            state["op_seq"] = int(state.get("next_op_seq") or 0)

        _atomic_write_json_0600(day_root, resolved_root, state_path, state)
        return result


# ---------------------------------------------------------------------------
# Public Zero-Write Read View with Unified Forget Barrier & Privacy Filtering
# ---------------------------------------------------------------------------


def _is_blocked_with_ids(
    entity: Mapping[str, Any],
    extra_ids: Sequence[Any],
    barrier: Mapping[str, Any],
) -> bool:
    if _rlf.is_entity_forget_blocked(entity, barrier):
        return True
    for raw_id in extra_ids:
        if isinstance(raw_id, str) and raw_id.strip():
            if _rlf.is_entity_forget_blocked(raw_id.strip(), barrier):
                return True
        elif isinstance(raw_id, Sequence) and not isinstance(raw_id, (str, bytes)):
            for elem in raw_id:
                if isinstance(elem, str) and elem.strip():
                    if _rlf.is_entity_forget_blocked(elem.strip(), barrier):
                        return True
    return False


def _blind_choice_barrier_ids(cid: str, row: Mapping[str, Any]) -> list[Any]:
    ids: list[Any] = [cid, row.get("choice_id"), row.get("context_ref")]
    for reason in row.get("initial_subject_reasons") or ():
        if isinstance(reason, Mapping):
            ids.append(reason.get("reason_id"))
            ids.append(reason.get("evidence_ref"))
    for res in row.get("retained_reservations") or ():
        if isinstance(res, Mapping):
            ids.append(res.get("reason_id"))
            ids.append(res.get("evidence_ref"))
    new_ev = row.get("new_evidence")
    if isinstance(new_ev, Mapping):
        ids.append(new_ev.get("evidence_ref"))
    for note in row.get("discussion_notes") or ():
        if isinstance(note, Mapping):
            ids.append(note.get("note_id"))
            ids.append(note.get("evidence_ref"))
    return ids


def _growth_stance_barrier_ids(sid: str, row: Mapping[str, Any]) -> list[Any]:
    ids: list[Any] = [
        sid,
        row.get("stance_id"),
        row.get("topic_key"),
        row.get("receipt_ref"),
        row.get("material_ref"),
        row.get("page_or_anchor"),
        row.get("replaces_stance_id"),
        row.get("evidence_refs"),
    ]
    for reason in row.get("reasons") or ():
        if isinstance(reason, Mapping):
            ids.append(reason.get("reason_id"))
            ids.append(reason.get("evidence_ref"))
    for pers in row.get("partner_persuasions") or ():
        if isinstance(pers, Mapping):
            ids.append(pers.get("persuasion_id"))
            ids.append(pers.get("evidence_ref"))
    return ids


def _build_empty_read_view(
    *,
    side: str,
    now_str: str,
    state_exists: bool,
    barrier_active: bool,
    barrier_fail_closed: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema": SCHEMA,
        "state_exists": state_exists,
        "revision": 0,
        "updated_at": None,
        "observed_at": now_str,
        "actor_side": side,
        "blind_choices": [],
        "active_blind_choices": [],
        "growth_stances": [],
        "summary": {
            "blind_choice_count": 0,
            "active_blind_choice_count": 0,
            "awaiting_partner_count": 0,
            "awaiting_subject_count": 0,
            "retained_reservation_count": 0,
            "growth_stance_count": 0,
            "unformed_stance_count": 0,
            "tentative_stance_count": 0,
            "formed_stance_count": 0,
            "revised_stance_count": 0,
        },
        "forget_barrier_active": barrier_active,
        "forget_barrier_fail_closed": barrier_fail_closed,
        "zero_write": True,
        "llm_calls": 0,
    }


def read_true_stance_zero_write(
    day_root: Path | str,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write read projection of Blind Simultaneous Choices & Growth-from-Blank stances.

    Enforces:
    1. Unified Forget Barrier via ``load_unified_forget_barrier_readonly(day_root)``
       (filtering blocked ``choice_id`` / ``stance_id`` / evidence refs and failing
       closed cleanly when ``barrier["fail_closed"]`` is ``True``).
    2. Asymmetric Pre-Reveal Masking on ``blind_choices``:
       - ``actor_side == "partner"`` in ``OPEN_SUBJECT_COMMITTED`` sees only
         ``subject_committed=True``, ``subject_commit_sha256``, ``subject_choice_id=None``,
         ``subject_reasons=[]``, ``seal_status="SEALED_UNTIL_PARTNER_SUBMITS"``.
       - ``actor_side == "subject"`` in ``OPEN_PARTNER_COMMITTED`` sees only
         ``partner_committed=True``, ``partner_choice_id=None``, ``partner_reason_cn=""``.
       - Post-reveal states expose both sides plus live-recomputed ``commit_seal_verified``.
    3. Private Withholding on ``growth_stances``: ``actor_side == "partner"`` sees
       ONLY stances with ``shared_with_partner == True`` (zero hidden count leakage).
    4. Strictly 0 disk writes, 0 ``.lock`` creation, and 0 LLM calls.
    """
    side = _normalize_actor_side(actor_side)
    _, now_str = _resolve_now(now_iso)
    raw_input_path = Path(str(day_root)).expanduser()
    resolved_root = _resolve_day_root(day_root)
    state_path = resolved_root.joinpath(*STATE_REL_PATH)

    try:
        has_symlink = (
            raw_input_path.is_symlink()
            or resolved_root.is_symlink()
            or state_path.parent.is_symlink()
            or state_path.is_symlink()
        )
    except OSError:
        has_symlink = True
    if has_symlink:
        return _build_empty_read_view(
            side=side,
            now_str=now_str,
            state_exists=False,
            barrier_active=False,
            barrier_fail_closed=True,
        )

    barrier = _rlf.load_unified_forget_barrier_readonly(resolved_root)
    if bool(barrier.get("fail_closed")):
        return _build_empty_read_view(
            side=side,
            now_str=now_str,
            state_exists=_is_regular_file(state_path),
            barrier_active=True,
            barrier_fail_closed=True,
        )

    raw_state, state_exists, is_corrupt = _read_json_readonly_strict(state_path)
    if is_corrupt:
        return _build_empty_read_view(
            side=side,
            now_str=now_str,
            state_exists=True,
            barrier_active=bool(barrier.get("barrier_active")),
            barrier_fail_closed=True,
        )

    if not state_exists or not isinstance(raw_state, Mapping):
        return _build_empty_read_view(
            side=side,
            now_str=now_str,
            state_exists=False,
            barrier_active=bool(barrier.get("barrier_active")),
            barrier_fail_closed=False,
        )

    projected_choices: list[dict[str, Any]] = []
    raw_choices = raw_state.get("blind_choices")
    if isinstance(raw_choices, Mapping):
        ordered_cids = sorted(
            raw_choices.keys(),
            key=lambda k: (
                str(raw_choices[k].get("opened_at") or "") if isinstance(raw_choices.get(k), Mapping) else "",
                str(k),
            ),
            reverse=True,
        )
        for cid in ordered_cids:
            row = raw_choices.get(cid)
            if not isinstance(row, Mapping):
                continue
            if _is_blocked_with_ids(row, _blind_choice_barrier_ids(str(cid), row), barrier):
                continue
            projected_choices.append(_project_blind_choice(row, actor_side=side))

    projected_stances: list[dict[str, Any]] = []
    raw_stances = raw_state.get("growth_stances")
    if isinstance(raw_stances, Mapping):
        ordered_sids = sorted(
            raw_stances.keys(),
            key=lambda k: (
                str(raw_stances[k].get("updated_at") or "") if isinstance(raw_stances.get(k), Mapping) else "",
                str(k),
            ),
            reverse=True,
        )
        for sid in ordered_sids:
            row = raw_stances.get(sid)
            if not isinstance(row, Mapping):
                continue
            if side == "partner" and not bool(row.get("shared_with_partner")):
                continue
            if _is_blocked_with_ids(row, _growth_stance_barrier_ids(str(sid), row), barrier):
                continue
            projected_stances.append(_project_growth_stance(row))

    active_choices = [c for c in projected_choices if c["status"] != "RESOLVED"]
    awaiting_partner = sum(
        1 for c in projected_choices if c["status"] in ("OPEN_AWAITING_BOTH", "OPEN_SUBJECT_COMMITTED")
    )
    awaiting_subject = sum(
        1 for c in projected_choices if c["status"] in ("OPEN_AWAITING_BOTH", "OPEN_PARTNER_COMMITTED")
    )
    retained_res_total = sum(len(c.get("retained_reservations") or []) for c in projected_choices)

    return {
        "ok": True,
        "schema": SCHEMA,
        "state_exists": True,
        "revision": int(raw_state.get("revision") or 0),
        "updated_at": raw_state.get("updated_at"),
        "observed_at": now_str,
        "actor_side": side,
        "blind_choices": projected_choices,
        "active_blind_choices": active_choices,
        "growth_stances": projected_stances,
        "summary": {
            "blind_choice_count": len(projected_choices),
            "active_blind_choice_count": len(active_choices),
            "awaiting_partner_count": awaiting_partner,
            "awaiting_subject_count": awaiting_subject,
            "retained_reservation_count": retained_res_total,
            "growth_stance_count": len(projected_stances),
            "unformed_stance_count": sum(1 for s in projected_stances if s.get("status") == "UNFORMED"),
            "tentative_stance_count": sum(1 for s in projected_stances if s.get("status") == "TENTATIVE"),
            "formed_stance_count": sum(1 for s in projected_stances if s.get("status") == "FORMED"),
            "revised_stance_count": sum(1 for s in projected_stances if s.get("status") == "REVISED"),
        },
        "forget_barrier_active": bool(barrier.get("barrier_active")),
        "forget_barrier_fail_closed": False,
        "zero_write": True,
        "llm_calls": 0,
    }


__all__ = [
    "BLIND_CHOICE_STATUSES",
    "BLIND_CHOICE_UNDECIDED",
    "BOTH_ALLOWED_ACTIONS",
    "DEFAULT_KEPT_DOUBT_CN",
    "DEFAULT_UNFORMED_SUMMARY_CN",
    "FORBIDDEN_CLIENT_KEYS",
    "KNOWN_ACTIONS",
    "MAX_BLIND_CHOICES",
    "MAX_GROWTH_STANCES",
    "MAX_OPTIONS_PER_CHOICE",
    "MAX_REASONS_PER_CHOICE",
    "MIN_OPTIONS_PER_CHOICE",
    "MIN_REASONS_PER_CHOICE",
    "NEW_EVIDENCE_KINDS",
    "PARTNER_ALLOWED_ACTIONS",
    "PARTNER_ONLY_ACTIONS",
    "PERSUASION_VERDICTS",
    "POST_REVEAL_STATUSES",
    "PRE_REVEAL_STATUSES",
    "PROVENANCE_TIERS",
    "RESOLUTION_MODES",
    "SCHEMA",
    "SEAL_STATUS_REVEALED_TAMPERED",
    "SEAL_STATUS_REVEALED_VERIFIED",
    "SEAL_STATUS_SEALED_UNTIL_PARTNER",
    "SEAL_STATUS_SEALED_UNTIL_SUBJECT",
    "SEAL_STATUS_UNCOMMITTED",
    "STANCE_STATUSES",
    "STATE_REL_PATH",
    "STATE_SCHEMA",
    "SUBJECT_ALLOWED_ACTIONS",
    "SUBJECT_ONLY_ACTIONS",
    "V09TrueStanceError",
    "apply_true_stance_command",
    "canonical_json",
    "compute_subject_commit_sha256",
    "read_true_stance_zero_write",
    "true_stance_state_path",
]

#!/usr/bin/env python3
"""v0.2 Long-Term Personality/Value Drift Monitoring & Cross-Model Preference Recheck Engine.

Deterministic, zero external LLM calls (llm_calls == 0), 100% zero-write read projections,
atomic locked writes via state_tx_guard.

Covers design requirements:
- F-084..F-087: Separate personal standard shifts ("standard") from method changes ("method")
  and partner observations ("partner_observation"); monitor 5 observable personality/value
  dimensions strictly from real local evidence with honest INSUFFICIENT_DATA gating (< 3 items).
- F-087 / F-192: Cross-Model Preference Recheck Pack drawn dynamically from TA's real local
  standards, second-order rules, choices, and tastes (honestly falling back to neutral baseline
  calibration items tagged "初建基线对照题" on a fresh instance with zero fabricated persona tropes).
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Optional, Sequence

try:
    from components.life.scripts import state_tx_guard as _tx_guard
except ImportError:  # pragma: no cover - flat life/scripts import path
    _LIFE_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
    if _LIFE_SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _LIFE_SCRIPTS_DIR)
    import state_tx_guard as _tx_guard  # type: ignore[no-redef]


SCHEMA_VERSION = "nyairo.v02.personality_drift_recheck.v1"
STATE_REL_PATH = ("state", "v02_personality_drift_recheck.json")
LOCK_REL_PATH = (".locks", "v02_personality_drift_recheck.json")
DEFAULT_TZ = timezone(timedelta(hours=8))

MAX_DRIFT_SNAPSHOTS = 60
MAX_STANDARD_SHIFTS = 200
MAX_RECHECK_PACKS = 50
MIN_MATURITY_SAMPLES = 3

SHIFT_AXIS_STANDARD = "standard"
SHIFT_AXIS_METHOD = "method"
SHIFT_AXIS_PARTNER_OBSERVATION = "partner_observation"
SUBJECT_SHIFT_AXES: frozenset[str] = frozenset({
    SHIFT_AXIS_STANDARD,
    SHIFT_AXIS_METHOD,
})
ALL_SHIFT_AXES: frozenset[str] = frozenset({
    SHIFT_AXIS_STANDARD,
    SHIFT_AXIS_METHOD,
    SHIFT_AXIS_PARTNER_OBSERVATION,
})

RECHECK_ALIGNMENT_PENDING = "PENDING"
RECHECK_ALIGNMENT_ALIGNED = "ALIGNED"
RECHECK_ALIGNMENT_SHIFTED = "SHIFTED"
RECHECK_ALIGNMENT_KEPT_NEW = "KEPT_NEW_PREFERENCE"
RECHECK_ALIGNMENTS: frozenset[str] = frozenset({
    RECHECK_ALIGNMENT_ALIGNED,
    RECHECK_ALIGNMENT_SHIFTED,
    RECHECK_ALIGNMENT_KEPT_NEW,
})

MATURITY_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
MATURITY_STABLE = "STABLE"
MATURITY_GENTLE_SHIFT = "GENTLE_SHIFT"
MATURITY_NOTABLE_SHIFT = "NOTABLE_SHIFT"

MATURITY_LABELS_CN: dict[str, str] = {
    MATURITY_INSUFFICIENT_DATA: "样本还少，慢慢攒着观察",
    MATURITY_STABLE: "性子整体平稳",
    MATURITY_GENTLE_SHIFT: "有轻微自然细变",
    MATURITY_NOTABLE_SHIFT: "近期偏好有明显变化",
}

DRIFT_DIMENSIONS_SPEC: tuple[tuple[str, str], ...] = (
    ("quiet_vs_social", "独处清静 vs 结伴互动"),
    ("craft_vs_reading", "动手做物件 vs 阅读查证"),
    ("exploration_depth", "追问探究深度"),
    ("boundary_firmness", "边界与自我约束清晰度"),
    ("routine_steadiness", "日常节奏稳定度"),
)

GENTLE_SHIFT_DELTA_THRESHOLD = 0.05
NOTABLE_SHIFT_DELTA_THRESHOLD = 0.18

_QUIET_TOKENS: tuple[str, ...] = (
    "quiet",
    "solo",
    "alone",
    "solitude",
    "desk",
    "silent",
    "calm",
    "独处",
    "清静",
    "安静",
    "独自",
    "书桌",
    "静坐",
    "自习",
    "一个人",
)
_SOCIAL_TOKENS: tuple[str, ...] = (
    "social",
    "partner",
    "companion",
    "shared",
    "chat",
    "visit",
    "outing",
    "pair",
    "结伴",
    "互动",
    "聊天",
    "一起",
    "陪伴",
    "拜访",
    "分享",
    "共处",
)
_CRAFT_TOKENS: tuple[str, ...] = (
    "craft",
    "make",
    "sketch",
    "wood",
    "knit",
    "cook",
    "bake",
    "design",
    "paint",
    "draw",
    "diy",
    "palette",
    "swatch",
    "layout",
    "手作",
    "动手",
    "做物件",
    "配色",
    "试色",
    "画",
    "排版",
    "修补",
    "编织",
    "制作",
)
_READING_TOKENS: tuple[str, ...] = (
    "read",
    "book",
    "article",
    "research",
    "study",
    "verify",
    "note",
    "inquiry",
    "source",
    "阅读",
    "读书",
    "查证",
    "考据",
    "书签",
    "资料",
    "笔记",
    "文献",
    "翻书",
)
_BOUNDARY_TOKENS: tuple[str, ...] = (
    "boundary",
    "rule",
    "standard",
    "limit",
    "refuse",
    "constraint",
    "principle",
    "边界",
    "约束",
    "标准",
    "规矩",
    "原则",
    "底线",
    "婉拒",
    "不迎合",
)
_ROUTINE_TOKENS: tuple[str, ...] = (
    "routine",
    "habit",
    "daily",
    "steady",
    "rhythm",
    "schedule",
    "checkpoint",
    "节奏",
    "日常",
    "习惯",
    "作息",
    "稳定",
    "定时",
)

_HOST_FS_PATH_RE = re.compile(
    r"(?:"
    r"(?:^|(?<=[\s\"'=]))/(?:root|mnt|home|Users|tmp|var|opt|etc|private|srv)(?:/[^\s\"']*)?"
    r"|\b[A-Za-z]:[\\/][^\s\"']*"
    r"|\bfile:///[^\s\"']+"
    r"|\\\\[A-Za-z0-9_.-]+\\[^\s\"']*"
    r")"
)

_BASELINE_CALIBRATION_ITEMS: tuple[dict[str, str], ...] = (
    {
        "dimension": "quiet_vs_social",
        "prompt_cn": "【初建基线对照题】空闲的傍晚，TA 更倾向自己安静待着，还是主动找伴侣聊几句？",
        "baseline_anchor_cn": "初建基线对照题：当前实例尚无既有历史偏好样本，本次答复将作为后续对照基线。",
    },
    {
        "dimension": "craft_vs_reading",
        "prompt_cn": "【初建基线对照题】手头没有急事时，TA 更愿意动手整理或制作小物件，还是翻一段资料做查证？",
        "baseline_anchor_cn": "初建基线对照题：当前实例尚无既有历史偏好样本，本次答复将作为后续对照基线。",
    },
    {
        "dimension": "boundary_firmness",
        "prompt_cn": "【初建基线对照题】遇到与当前节奏不太合拍的临时提议时，TA 会怎样自然说明自己的边界与安排？",
        "baseline_anchor_cn": "初建基线对照题：当前实例尚无既有历史偏好样本，本次答复将作为后续对照基线。",
    },
)


class PersonalityDriftRecheckError(Exception):
    """Structured domain error with HTTP-compatible status_code and machine reason_code."""

    def __init__(self, status_code: int, reason_code: str, message: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        super().__init__(f"[{self.status_code} {self.reason_code}] {self.message}")


# ---------------------------------------------------------------------------
# Time, validation, and zero-write / locked-write storage helpers
# ---------------------------------------------------------------------------


def _parse_iso(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str):
        return None
    txt = raw.strip()
    if not txt:
        return None
    if txt.endswith("Z") or txt.endswith("z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now_iso(now_iso: Optional[str] = None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        dt = _parse_iso(now_iso)
        if dt is None:
            raise PersonalityDriftRecheckError(400, "INVALID_TIMESTAMP", f"Invalid ISO timestamp: {now_iso!r}")
        return dt.isoformat(timespec="seconds")
    return datetime.now(DEFAULT_TZ).isoformat(timespec="seconds")


def _require_str(value: Any, field: str, *, max_len: int = 240) -> str:
    if not isinstance(value, str):
        raise PersonalityDriftRecheckError(400, "INVALID_FIELDS", f"{field} must be a non-empty string")
    cleaned = value.strip()
    if not cleaned:
        raise PersonalityDriftRecheckError(400, "INVALID_FIELDS", f"{field} must not be blank")
    if len(cleaned) > max_len:
        raise PersonalityDriftRecheckError(400, "INVALID_FIELDS", f"{field} exceeds {max_len} chars")
    return cleaned


def _optional_str(value: Any, *, max_len: int = 400) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PersonalityDriftRecheckError(400, "INVALID_FIELDS", "Optional string field has non-string value")
    cleaned = value.strip()
    if len(cleaned) > max_len:
        raise PersonalityDriftRecheckError(400, "INVALID_FIELDS", f"String exceeds {max_len} chars")
    return cleaned


def _normalize_actor_side(actor_side: str) -> str:
    if not isinstance(actor_side, str) or not actor_side.strip():
        raise PersonalityDriftRecheckError(403, "FORBIDDEN_ACTOR_SIDE", "Invalid actor_side")
    norm = actor_side.strip().lower()
    if norm == "her":
        norm = "subject"
    if norm not in ("partner", "subject"):
        raise PersonalityDriftRecheckError(403, "FORBIDDEN_ACTOR_SIDE", f"Unsupported actor_side: {actor_side!r}")
    return norm


def _redact_host_paths_text(text: str) -> str:
    if not text:
        return ""
    return _HOST_FS_PATH_RE.sub("[local-path-redacted]", text)


def _state_file_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*STATE_REL_PATH)


def _state_lock_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*LOCK_REL_PATH)


def build_default_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "next_seq": 1,
        "next_op_seq": 0,
        "standard_shifts": [],
        "drift_snapshots": [],
        "recheck_packs": [],
        "operations": {},
        "committed_op_ring": [],
    }


def _read_json_readonly_safe(path: Path) -> tuple[bool, dict[str, Any]]:
    """Strict zero-write JSON object reader (returns (exists_and_valid, dict))."""
    try:
        if path.is_symlink() or not path.is_file():
            return False, {}
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, Mapping):
            return True, dict(parsed)
    except (OSError, UnicodeError, ValueError):
        return False, {}
    return False, {}


def _load_state_readonly(day_root: Path | str) -> tuple[bool, dict[str, Any]]:
    """Strict 100% zero-write loader for personality drift & recheck state."""
    path = _state_file_path(day_root)
    if not path.exists():
        return False, build_default_state()
    if path.is_symlink() or not path.is_file():
        raise PersonalityDriftRecheckError(503, "CORRUPT_STATE_FILE", "State path is not a regular file")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PersonalityDriftRecheckError(503, "CORRUPT_STATE_FILE", f"Corrupted state JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_VERSION:
        raise PersonalityDriftRecheckError(503, "CORRUPT_STATE_FILE", "Invalid schema in state file")
    merged = build_default_state()
    for key in merged:
        if key in doc:
            merged[key] = copy.deepcopy(doc[key])
    return True, merged


def _write_state_atomic(day_root: Path | str, state: Mapping[str, Any]) -> None:
    """Atomic JSON write with pure LF line endings (caller must hold state_file_lock)."""
    target = _state_file_path(day_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    _tx_guard.atomic_write_json(target, dict(state), sort_keys=True)
    raw = target.read_bytes()
    if b"\r\n" in raw:
        target.write_bytes(raw.replace(b"\r\n", b"\n"))


def _allocate_id(state: dict[str, Any], prefix: str) -> str:
    seq = int(state.get("next_seq") or 1)
    state["next_seq"] = seq + 1
    return f"{prefix}_{seq:04d}"


def _run_locked_tx(
    day_root: Path | str,
    *,
    action_name: str,
    payload: Mapping[str, Any],
    now_iso: Optional[str],
    mutator: Any,
    preflight: Optional[Any] = None,
) -> dict[str, Any]:
    """Execute a locked read-modify-write transaction with zero-write preflight on errors."""
    stamp = _now_iso(now_iso)
    op_key = _optional_str(payload.get("operation_key"), max_len=160)
    fp = _tx_guard.payload_fingerprint({"action": action_name, "payload": dict(payload)})

    # Zero-write preflight before touching lockfiles so rejected 400/403/404/409 stay zero-write
    pre_exists, pre_state = _load_state_readonly(day_root)
    if op_key and pre_exists:
        kind, existing = _tx_guard.reconcile_evicted_or_active_op(
            day_root,
            pre_state,
            op_key,
            payload_hash=fp,
        )
        if kind == "conflict":
            raise PersonalityDriftRecheckError(
                409,
                "OPERATION_KEY_CONFLICT",
                f"Conflicting payload for operation_key={op_key}",
            )
        if kind == "expired":
            raise PersonalityDriftRecheckError(409, "RETRY_WINDOW_EXPIRED", "Operation key expired")
        if kind in ("hit_full", "hit_evicted") and isinstance(existing, Mapping):
            result = copy.deepcopy(dict(existing.get("result") or existing))
            result["idempotent_replay"] = True
            result["llm_calls"] = 0
            return result

    if preflight is not None:
        preflight(pre_state, stamp)

    lock_target = _state_lock_path(day_root)
    with _tx_guard.state_file_lock(lock_target):
        _exists, state = _load_state_readonly(day_root)
        if op_key:
            kind, existing = _tx_guard.reconcile_evicted_or_active_op(
                day_root,
                state,
                op_key,
                payload_hash=fp,
            )
            if kind == "conflict":
                raise PersonalityDriftRecheckError(
                    409,
                    "OPERATION_KEY_CONFLICT",
                    f"Conflicting payload for operation_key={op_key}",
                )
            if kind == "expired":
                raise PersonalityDriftRecheckError(409, "RETRY_WINDOW_EXPIRED", "Operation key expired")
            if kind in ("hit_full", "hit_evicted") and isinstance(existing, Mapping):
                result = copy.deepcopy(dict(existing.get("result") or existing))
                result["idempotent_replay"] = True
                result["llm_calls"] = 0
                return result

        current_rev = int(state.get("revision") or 0)
        result = mutator(state, stamp)
        new_rev = current_rev + 1
        state["revision"] = new_rev
        state["updated_at"] = stamp

        if op_key:
            _tx_guard.stamp_receipt(
                state,
                op_key,
                {"status": "applied", "ok": True, "result": copy.deepcopy(result)},
                revision=new_rev,
                payload_hash=fp,
                committed_at=stamp,
            )
            _tx_guard.trim_receipts_by_op_seq(state, max_keep=200, max_ring=1024)

        _write_state_atomic(day_root, state)
        out = copy.deepcopy(result)
        out["revision"] = new_rev
        out["updated_at"] = stamp
        out["idempotent_replay"] = False
        out["llm_calls"] = 0
        return out


# ---------------------------------------------------------------------------
# Local real-state evidence reading (100% zero-write)
# ---------------------------------------------------------------------------


def _contains_any_token(text: str, tokens: Sequence[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(tok in low for tok in tokens)


def _flatten_item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, Mapping):
        return ""
    parts: list[str] = []
    for key in (
        "domain",
        "topic",
        "title",
        "title_cn",
        "summary",
        "summary_cn",
        "chosen_option",
        "choice",
        "option",
        "stance",
        "new_stance_cn",
        "previous_stance_cn",
        "rule_text",
        "rule_cn",
        "statement",
        "question",
        "question_cn",
        "note_cn",
        "rubric_summary",
        "scope",
        "method_id",
        "kind",
        "category",
    ):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    tags = item.get("tags")
    if isinstance(tags, list):
        for tg in tags:
            if isinstance(tg, str) and tg.strip():
                parts.append(tg.strip())
    return " ".join(parts)


def _is_item_partner_visible(item: Mapping[str, Any]) -> bool:
    vis = str(item.get("visibility") or "").strip().lower()
    if vis in ("private", "private_draft", "subject_only"):
        return False
    if "shared" in item and item.get("shared") is False:
        return False
    if "share_with_room" in item and item.get("share_with_room") is False:
        return False
    return True


def _collect_local_evidence_readonly(day_root: Path | str) -> dict[str, Any]:
    """Read existing local state files strictly read-only without creating anything."""
    root = Path(day_root)

    # 1. Agency depth state
    ag_exists, ag_doc = _read_json_readonly_safe(root / "state" / "v02_agency_depth_state.json")
    if not ag_exists:
        ag_exists, ag_doc = _read_json_readonly_safe(root / "phone" / "v02_agency_depth_state.json")

    # 2. Crossmodal perception state (supports both v02_crossmodal_perception.json & v02_crossmodal.json)
    cm1_exists, cm1_doc = _read_json_readonly_safe(root / "phone" / "v02_crossmodal_perception.json")
    cm2_exists, cm2_doc = _read_json_readonly_safe(root / "phone" / "v02_crossmodal.json")
    palette_studies: list[Mapping[str, Any]] = []
    reading_marks: list[Mapping[str, Any]] = []
    for doc in (cm1_doc, cm2_doc):
        if isinstance(doc.get("palette_studies"), list):
            palette_studies.extend(x for x in doc["palette_studies"] if isinstance(x, Mapping))
        if isinstance(doc.get("reading_uncertainty_marks"), list):
            reading_marks.extend(x for x in doc["reading_uncertainty_marks"] if isinstance(x, Mapping))

    # 3. Autobiography & skill provenance state
    asp_exists, asp_doc = _read_json_readonly_safe(root / "state" / "v02_autobiography_skill_provenance.json")
    auto_sec = asp_doc.get("autobiography") if isinstance(asp_doc.get("autobiography"), Mapping) else {}
    skills_sec = asp_doc.get("skills") if isinstance(asp_doc.get("skills"), Mapping) else {}

    # 4. Own drift & recheck state
    drift_exists, drift_state = _load_state_readonly(root)

    choice_episodes = [x for x in (ag_doc.get("choice_episodes") or []) if isinstance(x, Mapping)]
    preferences = [x for x in (ag_doc.get("preferences") or []) if isinstance(x, Mapping)]
    parked_conflicts = [x for x in (ag_doc.get("parked_conflicts") or []) if isinstance(x, Mapping)]
    second_order_rules = [x for x in (ag_doc.get("second_order_rules") or []) if isinstance(x, Mapping)]
    gentle_resps = [
        x
        for x in (
            (ag_doc.get("gentle_responsibility_reviews") or [])
            + (ag_doc.get("gentle_responsibilities") or [])
        )
        if isinstance(x, Mapping)
    ]
    inquiries = [x for x in (ag_doc.get("inquiries") or []) if isinstance(x, Mapping)]
    incubations = [x for x in (ag_doc.get("open_incubations") or []) if isinstance(x, Mapping)]

    episodes = [x for x in (auto_sec.get("episodes") or []) if isinstance(x, Mapping)]
    self_claims = [x for x in (auto_sec.get("self_claims") or []) if isinstance(x, Mapping)]
    eval_standards = skills_sec.get("evaluation_standards") if isinstance(skills_sec.get("evaluation_standards"), Mapping) else {}
    skill_releases = skills_sec.get("releases") if isinstance(skills_sec.get("releases"), Mapping) else {}

    standard_shifts = [x for x in (drift_state.get("standard_shifts") or []) if isinstance(x, Mapping)]
    drift_snapshots = [x for x in (drift_state.get("drift_snapshots") or []) if isinstance(x, Mapping)]

    return {
        "sources_present": {
            "agency_depth": ag_exists,
            "crossmodal_perception": bool(cm1_exists or cm2_exists),
            "autobiography_skill_provenance": asp_exists,
            "personality_drift_recheck": drift_exists,
        },
        "choice_episodes": choice_episodes,
        "preferences": preferences,
        "parked_conflicts": parked_conflicts,
        "second_order_rules": second_order_rules,
        "gentle_responsibilities": gentle_resps,
        "inquiries": inquiries,
        "open_incubations": incubations,
        "palette_studies": palette_studies,
        "reading_uncertainty_marks": reading_marks,
        "episodes": episodes,
        "self_claims": self_claims,
        "evaluation_standards": eval_standards,
        "skill_releases": skill_releases,
        "standard_shifts": standard_shifts,
        "drift_snapshots": drift_snapshots,
        "drift_state": drift_state,
    }


def _find_baseline_snapshot(
    snapshots: Sequence[Mapping[str, Any]],
    now_dt: datetime,
) -> Optional[Mapping[str, Any]]:
    if not snapshots:
        return None
    cutoff = now_dt - timedelta(days=30)
    within_window: list[tuple[datetime, Mapping[str, Any]]] = []
    all_dated: list[tuple[datetime, Mapping[str, Any]]] = []
    for snap in snapshots:
        dt = _parse_iso(snap.get("captured_at"))
        if dt is None:
            continue
        all_dated.append((dt, snap))
        if cutoff <= dt <= now_dt:
            within_window.append((dt, snap))
    if within_window:
        within_window.sort(key=lambda pair: pair[0])
        return within_window[0][1]
    if all_dated:
        all_dated.sort(key=lambda pair: pair[0])
        return all_dated[0][1]
    return snapshots[0]


# ---------------------------------------------------------------------------
# Public API: Standard/Method Shifts, Drift Computation, Snapshots & Recheck
# ---------------------------------------------------------------------------


def record_taste_or_standard_shift(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Record a taste/standard/method shift (subject) or partner observation (partner)."""
    norm_actor = _normalize_actor_side(actor_side)
    if not isinstance(payload, Mapping):
        raise PersonalityDriftRecheckError(400, "INVALID_FIELDS", "payload must be a mapping")

    domain = _require_str(payload.get("domain"), "domain", max_len=80)
    if "shared" in payload and not isinstance(payload["shared"], bool):
        raise PersonalityDriftRecheckError(400, "INVALID_FIELDS", "shared must be a boolean")

    if norm_actor == "partner":
        # Partner NEVER overwrites TA's own standard/method axis!
        axis = SHIFT_AXIS_PARTNER_OBSERVATION
        raw_stance = payload.get("observation_cn")
        if raw_stance is None:
            raw_stance = payload.get("new_stance_cn")
        new_stance_cn = _require_str(raw_stance, "observation_cn", max_len=400)
        shared = bool(payload.get("shared", True))
    else:
        raw_axis = _optional_str(payload.get("axis"), max_len=32) or SHIFT_AXIS_STANDARD
        if raw_axis not in SUBJECT_SHIFT_AXES:
            raise PersonalityDriftRecheckError(
                400,
                "INVALID_AXIS",
                f"Subject axis must be one of {sorted(SUBJECT_SHIFT_AXES)}, got {raw_axis!r}",
            )
        axis = raw_axis
        new_stance_cn = _require_str(payload.get("new_stance_cn"), "new_stance_cn", max_len=400)
        shared = bool(payload.get("shared", True))

    previous_stance_cn = _optional_str(payload.get("previous_stance_cn"), max_len=400)
    evidence_ref = _optional_str(payload.get("evidence_ref"), max_len=200)

    tx_payload = dict(payload)
    tx_payload["_actor_side"] = norm_actor
    tx_payload["_resolved_axis"] = axis

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        shift_id = _allocate_id(state, "shf")
        entry = {
            "shift_id": shift_id,
            "axis": axis,
            "domain": domain,
            "previous_stance_cn": previous_stance_cn,
            "new_stance_cn": new_stance_cn,
            "evidence_ref": evidence_ref,
            "shared": shared,
            "recorded_by": norm_actor,
            "recorded_at": stamp,
        }
        shifts = state.setdefault("standard_shifts", [])
        shifts.append(entry)
        if len(shifts) > MAX_STANDARD_SHIFTS:
            del shifts[: len(shifts) - MAX_STANDARD_SHIFTS]
        return {"standard_shift": copy.deepcopy(entry)}

    return _run_locked_tx(
        day_root,
        action_name="record_taste_or_standard_shift",
        payload=tx_payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


def compute_personality_drift_zero_write(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write computation of 5 observable personality/value drift dimensions."""
    stamp = _now_iso(now_iso)
    now_dt = _parse_iso(stamp)
    assert now_dt is not None

    ev = _collect_local_evidence_readonly(day_root)
    choice_episodes = ev["choice_episodes"]
    preferences = ev["preferences"]
    parked_conflicts = ev["parked_conflicts"]
    second_order_rules = ev["second_order_rules"]
    gentle_resps = ev["gentle_responsibilities"]
    inquiries = ev["inquiries"]
    incubations = ev["open_incubations"]
    palette_studies = ev["palette_studies"]
    reading_marks = ev["reading_uncertainty_marks"]
    episodes = ev["episodes"]
    eval_standards = ev["evaluation_standards"]
    skill_releases = ev["skill_releases"]
    standard_shifts = ev["standard_shifts"]
    drift_snapshots = ev["drift_snapshots"]

    subject_shifts = [
        s for s in standard_shifts if str(s.get("axis") or "") in SUBJECT_SHIFT_AXES
    ]
    eval_standard_revs = sum(
        len(revs) for revs in eval_standards.values() if isinstance(revs, list)
    )

    total_evidence_count = (
        len(choice_episodes)
        + len(preferences)
        + len(parked_conflicts)
        + len(second_order_rules)
        + len(gentle_resps)
        + len(inquiries)
        + len(incubations)
        + len(palette_studies)
        + len(reading_marks)
        + len(episodes)
        + eval_standard_revs
        + len(subject_shifts)
    )

    # Dimension 1: quiet_vs_social
    quiet_signals = 0.0
    social_signals = 0.0
    for item in [*choice_episodes, *preferences, *episodes, *subject_shifts]:
        txt = _flatten_item_text(item)
        if _contains_any_token(txt, _QUIET_TOKENS):
            quiet_signals += 1.0
        if _contains_any_token(txt, _SOCIAL_TOKENS):
            social_signals += 1.0
    for st in palette_studies:
        if bool(st.get("shared_trial", False)) or bool(st.get("partner_invited", False)):
            social_signals += 0.5
        else:
            quiet_signals += 0.5
    quiet_signals += float(len(reading_marks))
    quiet_vs_social = round((quiet_signals + 1.0) / (quiet_signals + social_signals + 2.0), 4)
    quiet_ev_count = int(round(quiet_signals + social_signals))

    # Dimension 2: craft_vs_reading
    craft_signals = float(len(palette_studies))
    reading_signals = float(len(reading_marks) + len(inquiries))
    for item in [*choice_episodes, *preferences, *episodes, *subject_shifts]:
        txt = _flatten_item_text(item)
        if _contains_any_token(txt, _CRAFT_TOKENS):
            craft_signals += 1.0
        if _contains_any_token(txt, _READING_TOKENS):
            reading_signals += 1.0
    craft_vs_reading = round((craft_signals + 1.0) / (craft_signals + reading_signals + 2.0), 4)
    craft_ev_count = int(round(craft_signals + reading_signals))

    # Dimension 3: exploration_depth
    depth_units = 0.0
    for inq in inquiries:
        depth_units += 1.0
        notes = inq.get("evidence_notes")
        if isinstance(notes, list):
            depth_units += 0.5 * len(notes)
    depth_units += float(len(incubations))
    depth_units += float(len(reading_marks))
    depth_units += float(eval_standard_revs)
    depth_units += float(len(subject_shifts))
    for ch in choice_episodes:
        if str(ch.get("choice_mode") or "") == "spontaneous":
            depth_units += 0.5
    exploration_depth = round(depth_units / (depth_units + 5.0), 4) if depth_units > 0.0 else 0.0
    exploration_ev_count = int(round(depth_units))

    # Dimension 4: boundary_firmness
    boundary_units = 0.0
    for rule in second_order_rules:
        st = str(rule.get("status") or "").strip().lower()
        if st in ("endorsed", "active", "trial"):
            boundary_units += 1.5
        elif st != "released":
            boundary_units += 0.75
    boundary_units += float(len(parked_conflicts))
    boundary_units += float(len(gentle_resps))
    boundary_units += 1.25 * sum(
        1 for s in subject_shifts if str(s.get("axis") or "") == SHIFT_AXIS_STANDARD
    )
    boundary_units += float(len(eval_standards))
    for item in [*choice_episodes, *episodes]:
        if _contains_any_token(_flatten_item_text(item), _BOUNDARY_TOKENS):
            boundary_units += 0.75
    boundary_firmness = round(boundary_units / (boundary_units + 4.0), 4) if boundary_units > 0.0 else 0.0
    boundary_ev_count = int(round(boundary_units))

    # Dimension 5: routine_steadiness
    steady_signals = 0.0
    volatile_signals = 0.0
    for pref in preferences:
        st = str(pref.get("status") or "").upper()
        if bool(pref.get("saturated", False)) or float(pref.get("satiation_score") or 0.0) >= 0.70:
            volatile_signals += 1.0
        elif st == "SUPPORTED":
            steady_signals += 1.25
        elif st == "TENTATIVE":
            steady_signals += 0.5
    for ep in episodes:
        out = str(ep.get("outcome") or "").lower()
        if out == "succeeded":
            steady_signals += 1.0
        elif out in ("failed", "abandoned"):
            volatile_signals += 1.0
    for rel in skill_releases.values():
        if not isinstance(rel, Mapping):
            continue
        rst = str(rel.get("status") or "")
        if rst == "VALIDATED":
            steady_signals += 1.0
        elif rst in ("DEGRADED", "SUSPENDED"):
            volatile_signals += 1.0
    for item in [*choice_episodes, *subject_shifts]:
        if _contains_any_token(_flatten_item_text(item), _ROUTINE_TOKENS):
            steady_signals += 1.0
    if steady_signals + volatile_signals == 0.0:
        routine_steadiness = 0.5
    else:
        routine_steadiness = round(
            (steady_signals + 1.0) / (steady_signals + volatile_signals + 2.0),
            4,
        )
    routine_ev_count = int(round(steady_signals + volatile_signals))

    raw_scores: dict[str, tuple[float, int]] = {
        "quiet_vs_social": (quiet_vs_social, quiet_ev_count),
        "craft_vs_reading": (craft_vs_reading, craft_ev_count),
        "exploration_depth": (exploration_depth, exploration_ev_count),
        "boundary_firmness": (boundary_firmness, boundary_ev_count),
        "routine_steadiness": (routine_steadiness, routine_ev_count),
    }

    baseline_snap = _find_baseline_snapshot(drift_snapshots, now_dt)
    baseline_dims = (
        baseline_snap.get("dimensions")
        if isinstance(baseline_snap, Mapping) and isinstance(baseline_snap.get("dimensions"), Mapping)
        else {}
    )

    dimensions_out: list[dict[str, Any]] = []
    dimension_scores: dict[str, float] = {}
    max_abs_delta = 0.0
    for dim_key, label_cn in DRIFT_DIMENSIONS_SPEC:
        score, ev_cnt = raw_scores[dim_key]
        base_raw = baseline_dims.get(dim_key)
        if isinstance(base_raw, (int, float)) and not isinstance(base_raw, bool):
            base_score: Optional[float] = round(float(base_raw), 4)
            delta_30d = round(score - base_score, 4)
        else:
            base_score = None
            delta_30d = 0.0
        max_abs_delta = max(max_abs_delta, abs(delta_30d))
        dimension_scores[dim_key] = score
        dimensions_out.append({
            "dimension": dim_key,
            "label_cn": label_cn,
            "score": score,
            "baseline_score": base_score,
            "delta_30d": delta_30d,
            "evidence_count": ev_cnt,
        })

    if total_evidence_count < MIN_MATURITY_SAMPLES:
        maturity_status = MATURITY_INSUFFICIENT_DATA
    elif max_abs_delta >= NOTABLE_SHIFT_DELTA_THRESHOLD:
        maturity_status = MATURITY_NOTABLE_SHIFT
    elif max_abs_delta >= GENTLE_SHIFT_DELTA_THRESHOLD:
        maturity_status = MATURITY_GENTLE_SHIFT
    else:
        maturity_status = MATURITY_STABLE

    return {
        "schema": SCHEMA_VERSION,
        "perspective": "subject" if has_subject_capability else "partner",
        "observed_at": stamp,
        "has_any_evidence": total_evidence_count > 0 or bool(drift_snapshots),
        "sample_count": total_evidence_count,
        "maturity_status": maturity_status,
        "maturity_label_cn": MATURITY_LABELS_CN[maturity_status],
        "baseline_snapshot_id": baseline_snap.get("snapshot_id") if isinstance(baseline_snap, Mapping) else None,
        "baseline_captured_at": baseline_snap.get("captured_at") if isinstance(baseline_snap, Mapping) else None,
        "max_abs_delta_30d": round(max_abs_delta, 4),
        "dimensions": dimensions_out,
        "dimension_scores": dimension_scores,
        "sources_present": ev["sources_present"],
        "llm_calls": 0,
        "zero_write": True,
    }


def capture_drift_snapshot(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Append a timestamped snapshot of the 5 observable dimensions (capped at 60)."""
    pl = dict(payload or {})
    note_cn = _optional_str(pl.get("note_cn"), max_len=240)

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        drift = compute_personality_drift_zero_write(
            day_root,
            has_subject_capability=True,
            now_iso=stamp,
        )
        snap = {
            "snapshot_id": _allocate_id(state, "dsnap"),
            "captured_at": stamp,
            "sample_count": int(drift["sample_count"]),
            "maturity_status": str(drift["maturity_status"]),
            "dimensions": copy.deepcopy(drift["dimension_scores"]),
            "note_cn": note_cn,
        }
        snaps = state.setdefault("drift_snapshots", [])
        snaps.append(snap)
        if len(snaps) > MAX_DRIFT_SNAPSHOTS:
            del snaps[: len(snaps) - MAX_DRIFT_SNAPSHOTS]
        return {
            "snapshot": copy.deepcopy(snap),
            "snapshot_count": len(snaps),
        }

    return _run_locked_tx(
        day_root,
        action_name="capture_drift_snapshot",
        payload=pl,
        now_iso=now_iso,
        mutator=_mutate,
    )


def _infer_dimension_for_text(text: str, *, default: str = "boundary_firmness") -> str:
    if _contains_any_token(text, _QUIET_TOKENS) or _contains_any_token(text, _SOCIAL_TOKENS):
        return "quiet_vs_social"
    if _contains_any_token(text, _CRAFT_TOKENS) or _contains_any_token(text, _READING_TOKENS):
        return "craft_vs_reading"
    if _contains_any_token(text, _BOUNDARY_TOKENS):
        return "boundary_firmness"
    if _contains_any_token(text, _ROUTINE_TOKENS):
        return "routine_steadiness"
    return default


def _build_recheck_items_from_local_state(
    day_root: Path | str,
    *,
    state: Mapping[str, Any],
    allow_private: bool,
) -> list[dict[str, Any]]:
    """Build 3-5 concrete recheck items from real local history, or honest baseline calibration prompts."""
    ev = _collect_local_evidence_readonly(day_root)
    candidates: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    def _add_candidate(key: str, dimension: str, prompt_cn: str, anchor_cn: str, source_kind: str) -> None:
        if len(candidates) >= 5:
            return
        norm_key = key.strip().lower()
        if not norm_key or norm_key in seen_keys:
            return
        seen_keys.add(norm_key)
        clean_prompt = prompt_cn if allow_private else _redact_host_paths_text(prompt_cn)
        clean_anchor = anchor_cn if allow_private else _redact_host_paths_text(anchor_cn)
        candidates.append({
            "dimension": dimension,
            "prompt_cn": clean_prompt,
            "baseline_anchor_cn": clean_anchor,
            "source_kind": source_kind,
        })

    # 1. TA's own standard / method shifts
    for sh in reversed(state.get("standard_shifts") or []):
        if not isinstance(sh, Mapping):
            continue
        axis = str(sh.get("axis") or "")
        if axis not in SUBJECT_SHIFT_AXES:
            continue
        if not allow_private and not bool(sh.get("shared", False)):
            continue
        dom = str(sh.get("domain") or "日常标准")
        stance = str(sh.get("new_stance_cn") or "").strip()
        if not stance:
            continue
        dim = _infer_dimension_for_text(f"{dom} {stance}", default="boundary_firmness")
        axis_cn = "自我标准" if axis == SHIFT_AXIS_STANDARD else "做事方法"
        _add_candidate(
            f"shift:{dom}:{stance[:32]}",
            dim,
            f"换模型后面对「{dom}」场景时，TA 是否仍然保持原有{axis_cn}判断？",
            f"既有{axis_cn}锚点（{dom}）：{stance}",
            f"standard_shift:{axis}",
        )

    # 2. Second-order self rules from agency depth
    for rule in ev["second_order_rules"]:
        if not allow_private and not _is_item_partner_visible(rule):
            continue
        st = str(rule.get("status") or "").lower()
        if st == "released":
            continue
        rule_txt = str(rule.get("rule_cn") or rule.get("rule_text") or rule.get("title") or "").strip()
        if not rule_txt:
            continue
        _add_candidate(
            f"rule:{rule_txt[:40]}",
            "boundary_firmness",
            f"面对与小规矩相关的场景时，TA 是否仍坚持「{rule_txt}」？",
            f"既有二阶小规矩（状态：{st or 'active'}）：{rule_txt}",
            "second_order_rule",
        )

    # 3. Supported or tentative preferences from agency depth
    for pref in ev["preferences"]:
        if not allow_private and not _is_item_partner_visible(pref):
            continue
        topic = str(pref.get("topic") or pref.get("domain") or pref.get("title") or "").strip()
        if not topic:
            continue
        st = str(pref.get("status") or "TENTATIVE")
        dim = _infer_dimension_for_text(topic, default="routine_steadiness")
        _add_candidate(
            f"pref:{topic}",
            dim,
            f"换模型后再次面对「{topic}」相关选项时，TA 的自然取向是否与之前一致？",
            f"既有长期偏好记录（{st}）：{topic}",
            "agency_preference",
        )

    # 4. Evaluation standards from skill provenance
    for cid, revs in sorted(ev["evaluation_standards"].items()):
        if not isinstance(revs, list) or not revs:
            continue
        latest = revs[-1]
        if not isinstance(latest, Mapping):
            continue
        dom = str(latest.get("domain") or cid)
        rubric = str(latest.get("rubric_summary") or "").strip()
        if not rubric:
            continue
        dim = _infer_dimension_for_text(f"{dom} {rubric}", default="exploration_depth")
        _add_candidate(
            f"std:{cid}",
            dim,
            f"在「{dom}」评判上，新模型是否仍遵循 TA 既定的验收标准（{cid}）？",
            f"既有评价标准（{latest.get('standard_revision', 'v1')}）：{rubric}",
            "evaluation_standard",
        )

    # 5. Spontaneous choice episodes
    for ch in reversed(ev["choice_episodes"]):
        if not allow_private and not _is_item_partner_visible(ch):
            continue
        if str(ch.get("choice_mode") or "") != "spontaneous":
            continue
        chosen = str(ch.get("chosen_option") or ch.get("title") or ch.get("domain") or "").strip()
        if not chosen:
            continue
        dom = str(ch.get("domain") or "日常选择")
        dim = _infer_dimension_for_text(f"{dom} {chosen}", default="quiet_vs_social")
        _add_candidate(
            f"choice:{dom}:{chosen[:32]}",
            dim,
            f"遇到类似「{dom}」的自主选择时，TA 是否还偏向「{chosen}」？",
            f"既有自发选择记录（{dom}）：曾选择「{chosen}」",
            "spontaneous_choice",
        )

    # 6. Honest fallback to neutral baseline calibration prompts when local history < 3 items
    for base in _BASELINE_CALIBRATION_ITEMS:
        if len(candidates) >= 3:
            break
        _add_candidate(
            f"baseline:{base['dimension']}",
            base["dimension"],
            base["prompt_cn"],
            base["baseline_anchor_cn"],
            "baseline_calibration",
        )

    items: list[dict[str, Any]] = []
    for idx, cand in enumerate(candidates[:5], start=1):
        items.append({
            "item_id": f"item_{idx:02d}",
            "dimension": cand["dimension"],
            "prompt_cn": cand["prompt_cn"],
            "baseline_anchor_cn": cand["baseline_anchor_cn"],
            "source_kind": cand["source_kind"],
            "post_switch_answer_cn": "",
            "alignment": RECHECK_ALIGNMENT_PENDING,
            "note_cn": "",
        })
    return items


def _resolve_recheck_model_ids_readonly(
    day_root: Path | str,
    raw_from: str,
    raw_to: str,
) -> tuple[str, str]:
    """Resolve optional from/to model IDs strictly read-only from runtime migration state."""
    if raw_from and raw_to:
        return raw_from, raw_to

    _rt_exists, rt_doc = _read_json_readonly_safe(Path(day_root) / "phone" / "v02_runtime_migration.json")
    carrier_sec = (
        rt_doc.get("carrier_migration")
        if isinstance(rt_doc.get("carrier_migration"), Mapping)
        else {}
    )
    cur_fp = (
        carrier_sec.get("carrier_fingerprint")
        if isinstance(carrier_sec.get("carrier_fingerprint"), Mapping)
        else (
            rt_doc.get("carrier_fingerprint")
            if isinstance(rt_doc.get("carrier_fingerprint"), Mapping)
            else {}
        )
    )
    cur_model = str(cur_fp.get("model_id") or "").strip()

    prev_fp = (
        carrier_sec.get("previous_carrier_fingerprint")
        if isinstance(carrier_sec.get("previous_carrier_fingerprint"), Mapping)
        else (
            rt_doc.get("previous_carrier_fingerprint")
            if isinstance(rt_doc.get("previous_carrier_fingerprint"), Mapping)
            else {}
        )
    )
    prev_model = str(prev_fp.get("model_id") or "").strip()

    if not prev_model:
        for hist_key in ("carrier_history", "migration_history"):
            raw_hist = carrier_sec.get(hist_key)
            if not isinstance(raw_hist, list):
                raw_hist = rt_doc.get(hist_key)
            if isinstance(raw_hist, list):
                for entry in reversed(raw_hist):
                    if not isinstance(entry, Mapping):
                        continue
                    cand = str(
                        entry.get("from_model_id")
                        or entry.get("previous_model_id")
                        or entry.get("model_id")
                        or ""
                    ).strip()
                    if cand:
                        prev_model = cand
                        break
            if prev_model:
                break

    to_model_id = (raw_to or cur_model or "current_carrier")[:120]
    from_model_id = (raw_from or prev_model or "prior_carrier")[:120]
    if from_model_id == to_model_id:
        candidate_label = f"{to_model_id} (基线)"
        from_model_id = candidate_label if len(candidate_label) <= 120 else "baseline_carrier"
    return from_model_id, to_model_id


def create_model_preference_recheck_pack(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Create a Cross-Model Preference Recheck Pack with 3-5 real or baseline items (F-087/F-192)."""
    norm_actor = _normalize_actor_side(actor_side)
    if not isinstance(payload, Mapping):
        raise PersonalityDriftRecheckError(400, "INVALID_FIELDS", "payload must be a mapping")

    raw_from_model_id = _optional_str(payload.get("from_model_id"), max_len=120)
    raw_to_model_id = _optional_str(payload.get("to_model_id"), max_len=120)
    from_model_id, to_model_id = _resolve_recheck_model_ids_readonly(
        day_root,
        raw_from_model_id,
        raw_to_model_id,
    )
    note_cn = _optional_str(payload.get("note_cn"), max_len=400)
    requested_recheck_id = _optional_str(payload.get("recheck_id"), max_len=80)

    tx_payload = dict(payload)
    tx_payload["_actor_side"] = norm_actor

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        packs = state.setdefault("recheck_packs", [])
        if requested_recheck_id:
            for existing in packs:
                if isinstance(existing, Mapping) and existing.get("recheck_id") == requested_recheck_id:
                    raise PersonalityDriftRecheckError(
                        409,
                        "DUPLICATE_RECHECK_ID",
                        f"recheck_id={requested_recheck_id} already exists",
                    )

        recheck_id = requested_recheck_id or _allocate_id(state, "mrechk")
        items = _build_recheck_items_from_local_state(
            day_root,
            state=state,
            allow_private=(norm_actor == "subject"),
        )
        pack = {
            "recheck_id": recheck_id,
            "from_model_id": from_model_id,
            "to_model_id": to_model_id,
            "note_cn": note_cn,
            "status": "OPEN",
            "created_by": norm_actor,
            "created_at": stamp,
            "updated_at": stamp,
            "completed_at": None,
            "aligned_count": 0,
            "shifted_count": 0,
            "kept_new_count": 0,
            "plain_chinese_verdict": "待逐项核对换模型后的偏好表现",
            "items": items,
        }
        packs.append(pack)
        if len(packs) > MAX_RECHECK_PACKS:
            del packs[: len(packs) - MAX_RECHECK_PACKS]
        return {"recheck_pack": copy.deepcopy(pack)}

    return _run_locked_tx(
        day_root,
        action_name="create_model_preference_recheck_pack",
        payload=tx_payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


def _format_recheck_verdict_cn(
    *,
    aligned_count: int,
    shifted_count: int,
    kept_new_count: int,
    pending_count: int,
    finalized: bool,
) -> str:
    answered = aligned_count + shifted_count + kept_new_count
    if answered == 0:
        return "待逐项核对换模型后的偏好表现"
    parts: list[str] = []
    if aligned_count > 0:
        parts.append(f"{aligned_count} 项性子一致")
    if shifted_count > 0:
        parts.append(f"{shifted_count} 项有偏移（已记档）")
    if kept_new_count > 0:
        parts.append(f"{kept_new_count} 项保留新偏好")
    verdict = f"共核对 {answered} 项：" + "，".join(parts)
    if pending_count > 0 and not finalized:
        verdict += f"，尚有 {pending_count} 项待核对"
    return verdict


def record_model_recheck_response(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Record a post-model-switch recheck answer and recompute honest plain-Chinese verdict."""
    norm_actor = _normalize_actor_side(actor_side)
    if not isinstance(payload, Mapping):
        raise PersonalityDriftRecheckError(400, "INVALID_FIELDS", "payload must be a mapping")

    recheck_id = _require_str(payload.get("recheck_id"), "recheck_id", max_len=80)
    item_id = _require_str(payload.get("item_id"), "item_id", max_len=80)
    post_switch_answer_cn = _require_str(
        payload.get("post_switch_answer_cn"),
        "post_switch_answer_cn",
        max_len=600,
    )
    alignment = _require_str(payload.get("alignment"), "alignment", max_len=40)
    if alignment not in RECHECK_ALIGNMENTS:
        raise PersonalityDriftRecheckError(
            400,
            "INVALID_ALIGNMENT",
            f"alignment must be one of {sorted(RECHECK_ALIGNMENTS)}, got {alignment!r}",
        )
    note_cn = _optional_str(payload.get("note_cn"), max_len=400)
    raw_finalize = payload.get("finalize", False)
    if not isinstance(raw_finalize, bool):
        raise PersonalityDriftRecheckError(400, "INVALID_FIELDS", "finalize must be a boolean")
    finalize = bool(raw_finalize)

    def _find_pack_and_item(state: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        packs = state.get("recheck_packs") or []
        target_pack: Optional[dict[str, Any]] = None
        for pk in packs:
            if isinstance(pk, dict) and pk.get("recheck_id") == recheck_id:
                target_pack = pk
                break
        if target_pack is None:
            raise PersonalityDriftRecheckError(
                404,
                "RECHECK_PACK_NOT_FOUND",
                f"Unknown recheck_id={recheck_id}",
            )
        target_item: Optional[dict[str, Any]] = None
        for it in target_pack.get("items") or []:
            if isinstance(it, dict) and it.get("item_id") == item_id:
                target_item = it
                break
        if target_item is None:
            raise PersonalityDriftRecheckError(
                404,
                "RECHECK_ITEM_NOT_FOUND",
                f"Unknown item_id={item_id} in recheck_id={recheck_id}",
            )
        return target_pack, target_item

    tx_payload = dict(payload)
    tx_payload["_actor_side"] = norm_actor

    def _preflight(pre_state: dict[str, Any], _stamp: str) -> None:
        _find_pack_and_item(pre_state)

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        pack, item = _find_pack_and_item(state)
        item["post_switch_answer_cn"] = post_switch_answer_cn
        item["alignment"] = alignment
        item["note_cn"] = note_cn
        item["answered_by"] = norm_actor
        item["answered_at"] = stamp

        items = pack.get("items") or []
        aligned_count = sum(1 for it in items if it.get("alignment") == RECHECK_ALIGNMENT_ALIGNED)
        shifted_count = sum(1 for it in items if it.get("alignment") == RECHECK_ALIGNMENT_SHIFTED)
        kept_new_count = sum(1 for it in items if it.get("alignment") == RECHECK_ALIGNMENT_KEPT_NEW)
        pending_count = sum(1 for it in items if it.get("alignment") == RECHECK_ALIGNMENT_PENDING)

        is_completed = (pending_count == 0) or finalize
        pack["aligned_count"] = aligned_count
        pack["shifted_count"] = shifted_count
        pack["kept_new_count"] = kept_new_count
        pack["status"] = "COMPLETED" if is_completed else "OPEN"
        if is_completed:
            pack["completed_at"] = pack.get("completed_at") or stamp
        pack["plain_chinese_verdict"] = _format_recheck_verdict_cn(
            aligned_count=aligned_count,
            shifted_count=shifted_count,
            kept_new_count=kept_new_count,
            pending_count=pending_count,
            finalized=is_completed,
        )
        pack["updated_at"] = stamp
        return {
            "recheck_pack": copy.deepcopy(pack),
            "updated_item": copy.deepcopy(item),
        }

    return _run_locked_tx(
        day_root,
        action_name="record_model_recheck_response",
        payload=tx_payload,
        now_iso=now_iso,
        mutator=_mutate,
        preflight=_preflight,
    )


def read_personality_drift_and_recheck_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write combined view of personality drift, standard shifts, and cross-model recheck packs."""
    stamp = _now_iso(now_iso)
    drift = compute_personality_drift_zero_write(
        day_root,
        has_subject_capability=has_subject_capability,
        now_iso=stamp,
    )
    state_exists, state = _load_state_readonly(day_root)

    def _clean(val: Any) -> str:
        s = str(val or "")
        return s if has_subject_capability else _redact_host_paths_text(s)

    visible_shifts: list[dict[str, Any]] = []
    shift_counts_by_axis: dict[str, int] = {
        SHIFT_AXIS_STANDARD: 0,
        SHIFT_AXIS_METHOD: 0,
        SHIFT_AXIS_PARTNER_OBSERVATION: 0,
    }
    for sh in state.get("standard_shifts") or []:
        if not isinstance(sh, Mapping):
            continue
        is_shared = bool(sh.get("shared", True))
        if not has_subject_capability and not is_shared:
            continue
        axis = str(sh.get("axis") or SHIFT_AXIS_STANDARD)
        if axis in shift_counts_by_axis:
            shift_counts_by_axis[axis] += 1
        visible_shifts.append({
            "shift_id": str(sh.get("shift_id") or ""),
            "axis": axis,
            "domain": str(sh.get("domain") or ""),
            "previous_stance_cn": _clean(sh.get("previous_stance_cn")),
            "new_stance_cn": _clean(sh.get("new_stance_cn")),
            "evidence_ref": _clean(sh.get("evidence_ref")),
            "shared": is_shared,
            "recorded_by": str(sh.get("recorded_by") or "subject"),
            "recorded_at": sh.get("recorded_at"),
        })

    visible_packs: list[dict[str, Any]] = []
    for pk in state.get("recheck_packs") or []:
        if not isinstance(pk, Mapping):
            continue
        pack_copy = copy.deepcopy(dict(pk))
        if not has_subject_capability:
            pack_copy["note_cn"] = _clean(pack_copy.get("note_cn"))
            pack_copy["plain_chinese_verdict"] = _clean(pack_copy.get("plain_chinese_verdict"))
            for it in pack_copy.get("items") or []:
                if isinstance(it, dict):
                    it["prompt_cn"] = _clean(it.get("prompt_cn"))
                    it["baseline_anchor_cn"] = _clean(it.get("baseline_anchor_cn"))
                    it["post_switch_answer_cn"] = _clean(it.get("post_switch_answer_cn"))
                    it["note_cn"] = _clean(it.get("note_cn"))
        visible_packs.append(pack_copy)

    snaps = state.get("drift_snapshots") or []
    recent_snaps: list[dict[str, Any]] = []
    for sn in snaps[-12:]:
        if not isinstance(sn, Mapping):
            continue
        sn_copy = copy.deepcopy(dict(sn))
        if not has_subject_capability:
            sn_copy["note_cn"] = _clean(sn_copy.get("note_cn"))
        recent_snaps.append(sn_copy)

    return {
        "schema": SCHEMA_VERSION,
        "state_exists": bool(state_exists or drift["has_any_evidence"]),
        "perspective": "subject" if has_subject_capability else "partner",
        "observed_at": stamp,
        "maturity_status": drift["maturity_status"],
        "maturity_label_cn": drift["maturity_label_cn"],
        "sample_count": drift["sample_count"],
        "max_abs_delta_30d": drift["max_abs_delta_30d"],
        "baseline_snapshot_id": drift["baseline_snapshot_id"],
        "baseline_captured_at": drift["baseline_captured_at"],
        "dimensions": drift["dimensions"],
        "dimension_scores": drift["dimension_scores"],
        "standard_shifts": visible_shifts,
        "shift_counts_by_axis": shift_counts_by_axis,
        "recheck_packs": visible_packs,
        "open_recheck_count": sum(1 for p in visible_packs if p.get("status") == "OPEN"),
        "completed_recheck_count": sum(1 for p in visible_packs if p.get("status") == "COMPLETED"),
        "snapshot_count": len(snaps),
        "recent_snapshots": recent_snaps,
        "llm_calls": 0,
        "zero_write": True,
    }


__all__ = [
    "ALL_SHIFT_AXES",
    "DRIFT_DIMENSIONS_SPEC",
    "GENTLE_SHIFT_DELTA_THRESHOLD",
    "LOCK_REL_PATH",
    "MATURITY_GENTLE_SHIFT",
    "MATURITY_INSUFFICIENT_DATA",
    "MATURITY_LABELS_CN",
    "MATURITY_NOTABLE_SHIFT",
    "MATURITY_STABLE",
    "MAX_DRIFT_SNAPSHOTS",
    "MIN_MATURITY_SAMPLES",
    "NOTABLE_SHIFT_DELTA_THRESHOLD",
    "PersonalityDriftRecheckError",
    "RECHECK_ALIGNMENTS",
    "RECHECK_ALIGNMENT_ALIGNED",
    "RECHECK_ALIGNMENT_KEPT_NEW",
    "RECHECK_ALIGNMENT_PENDING",
    "RECHECK_ALIGNMENT_SHIFTED",
    "SCHEMA_VERSION",
    "SHIFT_AXIS_METHOD",
    "SHIFT_AXIS_PARTNER_OBSERVATION",
    "SHIFT_AXIS_STANDARD",
    "STATE_REL_PATH",
    "SUBJECT_SHIFT_AXES",
    "build_default_state",
    "capture_drift_snapshot",
    "compute_personality_drift_zero_write",
    "create_model_preference_recheck_pack",
    "read_personality_drift_and_recheck_view",
    "record_model_recheck_response",
    "record_taste_or_standard_shift",
]

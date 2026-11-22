#!/usr/bin/env python3
"""v0.2 Versioned P12 Autobiographical Periods, 4-Way Skill Provenance,
Zero-Write Situation & Sampling Provenance, and 90% Budget-Drop / Cross-Midnight
UNKNOWN Fee Reservation engine (deterministic, llm_calls == 0).

Covers design requirements:
- F-084..F-087, F-188 & Ch.15 sec 5.2: Versioned P12 autobiographical periods
  (episode -> period_summary -> versioned_narrative_snapshot) with append-only
  snapshots, failed/abandoned arc preservation without historical backfill,
  explicit cross-month self-claim contradiction detection, separation of
  ``importance_to_self`` vs ``verified_capability`` and ``temporary_choice`` vs
  ``durable_claim``, and strict partner privacy filtering without hidden counts.
- F-134..F-139 & P29: 4-way skill attribution (``MODEL_NATIVE``,
  ``BORROWED_TOOL``, ``RETRIEVED_GUIDE``, ``PRACTICED_MASTERY``), separation of
  ``standard_revision`` from ``method_efficacy``, scoped consecutive degradation
  (``2x DEGRADED -> 3x SUSPENDED``) keyed by ``(method_id, tool_version, model_id,
  scope)`` while ignoring transient network/transport errors and deduplicating
  identical bug retries, and ``UNVALIDATED_AFTER_MIGRATION`` on model/tool bump
  until re-verified against bare model / old baseline.
- F-186, F-187, F-193: ``project_situation_provenance_zero_write`` reporting
  per-domain ``source_domain``, ``revision``, ``last_synced_at``,
  ``epistemic_status``, and workspace ``candidate_audit`` (sampled vs dropped
  reasons) with strict zero disk writes.
- F-191 & P30-P31: 90% budget-drop survival preserving activity checkpoints,
  pending commitments, deletion barriers, and unresolved reconciliations without
  fabricating fatigue/illness excuses; carrying in-flight ``UNKNOWN`` upper-bound
  fee reservations across UTC/local midnight rollover and clock jumps until
  explicitly reconciled.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
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


SCHEMA_VERSION = "nyairo.v02.autobiography_skill_provenance.v1"
STATE_REL_PATH = ("state", "v02_autobiography_skill_provenance.json")
LOCK_REL_PATH = (".locks", "v02_autobiography_skill_provenance.json")
DEFAULT_TZ = timezone(timedelta(hours=8))

# P12 / F-188 closed sets
EPISODE_OUTCOMES: frozenset[str] = frozenset({
    "succeeded",
    "failed",
    "abandoned",
    "paused",
    "partial",
    "ongoing",
})
FAILED_OR_ABANDONED_OUTCOMES: frozenset[str] = frozenset({"failed", "abandoned"})
VISIBILITY_SCOPES: frozenset[str] = frozenset({"private", "shared"})
CLAIM_LANES: frozenset[str] = frozenset({"temporary_choice", "durable_claim"})
CLAIM_POLARITIES: frozenset[str] = frozenset({"affirm", "negate", "uncertain", "contested"})
OPPOSING_POLARITIES: dict[str, frozenset[str]] = {
    "affirm": frozenset({"negate"}),
    "negate": frozenset({"affirm"}),
}

# 4-way Skill Attribution (F-136) & lifecycle (F-134..F-139, P29)
SKILL_ATTRIBUTION_MODEL_NATIVE = "MODEL_NATIVE"
SKILL_ATTRIBUTION_BORROWED_TOOL = "BORROWED_TOOL"
SKILL_ATTRIBUTION_RETRIEVED_GUIDE = "RETRIEVED_GUIDE"
SKILL_ATTRIBUTION_PRACTICED_MASTERY = "PRACTICED_MASTERY"
SKILL_ATTRIBUTIONS: frozenset[str] = frozenset({
    SKILL_ATTRIBUTION_MODEL_NATIVE,
    SKILL_ATTRIBUTION_BORROWED_TOOL,
    SKILL_ATTRIBUTION_RETRIEVED_GUIDE,
    SKILL_ATTRIBUTION_PRACTICED_MASTERY,
})

SKILL_STATUS_CANDIDATE = "CANDIDATE"
SKILL_STATUS_TRIAL = "TRIAL"
SKILL_STATUS_VALIDATED = "VALIDATED"
SKILL_STATUS_DEGRADED = "DEGRADED"
SKILL_STATUS_SUSPENDED = "SUSPENDED"
SKILL_STATUS_UNVALIDATED_AFTER_MIGRATION = "UNVALIDATED_AFTER_MIGRATION"
SKILL_STATUS_RETIRED = "RETIRED"
SKILL_STATUSES: frozenset[str] = frozenset({
    SKILL_STATUS_CANDIDATE,
    SKILL_STATUS_TRIAL,
    SKILL_STATUS_VALIDATED,
    SKILL_STATUS_DEGRADED,
    SKILL_STATUS_SUSPENDED,
    SKILL_STATUS_UNVALIDATED_AFTER_MIGRATION,
    SKILL_STATUS_RETIRED,
})

SKILL_ATTRIBUTION_LABELS_CN: dict[str, str] = {
    SKILL_ATTRIBUTION_MODEL_NATIVE: "模型自带本事",
    SKILL_ATTRIBUTION_BORROWED_TOOL: "借助本地工具",
    SKILL_ATTRIBUTION_RETRIEVED_GUIDE: "查资料照做",
    SKILL_ATTRIBUTION_PRACTICED_MASTERY: "自己练成的手艺",
}

SKILL_STATUS_LABELS_CN: dict[str, str] = {
    SKILL_STATUS_UNVALIDATED_AFTER_MIGRATION: "换模型待复验",
    SKILL_STATUS_VALIDATED: "已验证可用",
    SKILL_STATUS_TRIAL: "试行观察中",
    SKILL_STATUS_CANDIDATE: "初学候选",
    SKILL_STATUS_DEGRADED: "表现下降",
    SKILL_STATUS_SUSPENDED: "暂缓使用",
    SKILL_STATUS_RETIRED: "已体面退役",
}

METHOD_PATCH_RETEST_STATUSES: frozenset[str] = frozenset({
    "PENDING_RETEST",
    "VERIFIED",
})

_HOST_FS_PATH_RE = re.compile(
    r"(?:"
    r"(?:^|(?<=[\s\"'=]))/(?:root|mnt|home|Users|tmp|var|opt|etc|private|srv)(?:/[^\s\"']*)?"
    r"|\b[A-Za-z]:[\\/][^\s\"']*"
    r"|\bfile:///[^\s\"']+"
    r"|\\\\[A-Za-z0-9_.-]+\\[^\s\"']*"
    r")"
)

TRANSIENT_NETWORK_ERROR_KINDS: frozenset[str] = frozenset({
    "network_timeout",
    "transport_error",
    "rate_limit",
    "transient_network",
    "timeout",
    "connection_reset",
    "service_unavailable",
})

DEGRADATION_STREAK_THRESHOLD = 2
SUSPENSION_STREAK_THRESHOLD = 3

# Situation Provenance epistemic statuses (F-186, F-193)
EPISTEMIC_ATTESTED = "ATTESTED"
EPISTEMIC_PROVISIONAL = "PROVISIONAL"
EPISTEMIC_STALE = "STALE"
EPISTEMIC_UNMATERIALIZED = "UNMATERIALIZED"
EPISTEMIC_UNKNOWN = "UNKNOWN"

# Budget survival & reservation statuses (F-191, P30-P31)
BUDGET_MODE_NORMAL = "NORMAL"
BUDGET_MODE_CONSERVATION = "LOW_BUDGET_CONSERVATION"
RESERVATION_STATUS_UNKNOWN = "UNKNOWN_RESERVED"
RESERVATION_STATUS_SETTLED = "SETTLED"
RESERVATION_STATUS_NOT_CHARGED = "CONFIRMED_NOT_CHARGED"

FORBIDDEN_EXCUSE_TOKENS: tuple[str, ...] = (
    "fatigue",
    "tired",
    "illness",
    "sick",
    "exhausted",
    "mood_down",
)


class AutobiographySkillProvenanceError(Exception):
    """Structured domain error with HTTP-compatible status and machine reason_code."""

    def __init__(self, status_code: int, reason_code: str, message: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        super().__init__(f"[{self.status_code} {self.reason_code}] {self.message}")


# ---------------------------------------------------------------------------
# Time, validation, and zero-write / locked-write storage helpers
# ---------------------------------------------------------------------------


def _now_iso(now_iso: Optional[str] = None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        dt = _parse_iso(now_iso)
        if dt is None:
            raise AutobiographySkillProvenanceError(400, "INVALID_TIMESTAMP", f"Invalid ISO timestamp: {now_iso!r}")
        return dt.isoformat(timespec="seconds")
    return datetime.now(DEFAULT_TZ).isoformat(timespec="seconds")


def _parse_iso(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str):
        return None
    txt = raw.strip()
    if not txt:
        return None
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _require_str(value: Any, field: str, *, max_len: int = 240) -> str:
    if not isinstance(value, str):
        raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", f"{field} must be a non-empty string")
    cleaned = value.strip()
    if not cleaned:
        raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", f"{field} must not be blank")
    if len(cleaned) > max_len:
        raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", f"{field} exceeds {max_len} chars")
    return cleaned


def _optional_str(value: Any, *, max_len: int = 400) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", "Optional string field has non-string value")
    cleaned = value.strip()
    if len(cleaned) > max_len:
        raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", f"String exceeds {max_len} chars")
    return cleaned


def _coerce_float(value: Any, field: str, *, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AutobiographySkillProvenanceError(400, "INVALID_NUMBER", f"{field} must be numeric")
    num = float(value)
    if minimum is not None and num < minimum:
        raise AutobiographySkillProvenanceError(400, "INVALID_NUMBER", f"{field} must be >= {minimum}")
    if maximum is not None and num > maximum:
        raise AutobiographySkillProvenanceError(400, "INVALID_NUMBER", f"{field} must be <= {maximum}")
    return num


def _coerce_int(value: Any, field: str, *, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AutobiographySkillProvenanceError(400, "INVALID_INT", f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise AutobiographySkillProvenanceError(400, "INVALID_INT", f"{field} must be >= {minimum}")
    return value


def _clean_str_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", f"{field} must be a list of strings")
    out: list[str] = []
    for item in value:
        s = _require_str(item, f"{field}[]")
        if s not in out:
            out.append(s)
    return out


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
        "autobiography": {
            "episodes": [],
            "self_claims": [],
            "period_summaries": {},
            "versioned_narrative_snapshots": [],
            "domain_profiles": {},
            "temporary_choices": [],
        },
        "skills": {
            "evaluation_standards": {},
            "releases": {},
            "migration_history": [],
            "network_incident_log": [],
            "method_patches": [],
            "recheck_requests": [],
        },
        "situation_audits": {
            "workspace_candidate_audits": [],
        },
        "budget": {
            "normal_daily_cost_cap": 10.0,
            "normal_daily_token_cap": 50000,
            "normal_daily_wake_cap": 24,
            "budget_scale_pct": 100,
            "active_daily_cost_cap": 10.0,
            "active_daily_token_cap": 50000,
            "active_daily_wake_cap": 24,
            "mode": BUDGET_MODE_NORMAL,
            "status_reason": "NORMAL_OPERATION",
            "excuse_fabricated": False,
            "allow_exploratory_cognition": True,
            "allow_decorative_generation": True,
            "allow_essential_reconciliation": True,
            "allow_commitment_fulfillment": True,
            "allow_deletion_enforcement": True,
            "tz_offset_hours": 8,
            "current_utc_day": None,
            "current_local_day": None,
            "last_monotonic_seq": 0,
            "last_wall_clock_iso": None,
            "clock_jump_detected": False,
            "daily_settled_cost": 0.0,
            "daily_settled_tokens": 0,
            "daily_wakes_used": 0,
            "prior_day_settled_cost": 0.0,
            "reservations": {},
            "reconciled_ledger": [],
            "preserved_survival_state": {
                "activity_checkpoints": [],
                "pending_commitments": [],
                "deletion_barriers": [],
                "unresolved_reconciliations": [],
            },
            "metering_layers": {
                "llm_calls": 0,
                "background_jobs": 0,
                "admitted_products": 0,
                "unadmitted_samples": 0,
            },
        },
        "operations": {},
        "committed_op_ring": [],
    }


def _load_state_readonly(day_root: Path | str) -> tuple[bool, dict[str, Any]]:
    """Strict 100% zero-write read loader (no dir creation, no locks, no mtime touch)."""
    path = _state_file_path(day_root)
    if not path.exists():
        return False, build_default_state()
    if not path.is_file():
        raise AutobiographySkillProvenanceError(503, "CORRUPT_STATE_FILE", "State path is not a regular file")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise AutobiographySkillProvenanceError(503, "CORRUPT_STATE_FILE", f"Corrupted state JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_VERSION:
        raise AutobiographySkillProvenanceError(503, "CORRUPT_STATE_FILE", "Invalid schema in state file")
    merged = build_default_state()
    for key in merged:
        if key in doc:
            if isinstance(merged[key], dict) and isinstance(doc[key], dict):
                merged[key].update(copy.deepcopy(doc[key]))
            else:
                merged[key] = copy.deepcopy(doc[key])
    return True, merged


def _write_state_atomic(day_root: Path | str, state: Mapping[str, Any]) -> None:
    """Atomic JSON write with pure LF line endings (caller must hold state_file_lock)."""
    target = _state_file_path(day_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(dict(state), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _tx_guard.atomic_write_json(target, json.loads(serialized), sort_keys=True)
    # Normalize LF strictly in case host platform writes CRLF via NamedTemporaryFile
    raw = target.read_bytes()
    if b"\r\n" in raw:
        normalized = raw.replace(b"\r\n", b"\n")
        target.write_bytes(normalized)


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
) -> dict[str, Any]:
    """Execute a locked read-modify-write transaction via state_tx_guard.state_file_lock."""
    stamp = _now_iso(now_iso)
    op_key = _optional_str(payload.get("operation_key"))
    expected_rev = payload.get("expected_revision")
    if expected_rev is not None:
        expected_rev = _coerce_int(expected_rev, "expected_revision", minimum=0)
    fp = _tx_guard.payload_fingerprint({"action": action_name, "payload": dict(payload)})

    lock_target = _state_lock_path(day_root)
    with _tx_guard.state_file_lock(lock_target):
        _exists, state = _load_state_readonly(day_root)
        if op_key:
            kind, existing = _tx_guard.reconcile_evicted_or_active_op(
                day_root,
                state,
                op_key,
                expected_revision=expected_rev,
                payload_hash=fp,
            )
            if kind == "conflict":
                raise AutobiographySkillProvenanceError(
                    409,
                    "OPERATION_KEY_CONFLICT",
                    f"Conflicting payload for operation_key={op_key}",
                )
            if kind == "expired":
                raise AutobiographySkillProvenanceError(409, "RETRY_WINDOW_EXPIRED", "Operation key expired")
            if kind in ("hit_full", "hit_evicted") and isinstance(existing, Mapping):
                result = copy.deepcopy(dict(existing.get("result") or existing))
                result["idempotent_replay"] = True
                result["llm_calls"] = 0
                return result

        current_rev = int(state.get("revision") or 0)
        if expected_rev is not None and expected_rev != current_rev:
            raise AutobiographySkillProvenanceError(
                409,
                "REVISION_CONFLICT",
                f"expected_revision={expected_rev} does not match current revision={current_rev}",
            )

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
# Pillar 1: Versioned P12 Autobiographical Periods (F-188, sec 5.2)
# ---------------------------------------------------------------------------


def _ensure_domain_profile(auto_state: dict[str, Any], domain: str) -> dict[str, Any]:
    profiles = auto_state.setdefault("domain_profiles", {})
    if domain not in profiles:
        profiles[domain] = {
            "domain": domain,
            "importance_to_self": {
                "score": 0.0,
                "status": "UNSET",
                "supporting_choice_refs": [],
                "temporary_choice_refs": [],
                "last_updated_at": None,
                "visibility": "shared",
            },
            "verified_capability": {
                "score": None,
                "status": "UNVERIFIED",
                "assessment_refs": [],
                "last_verified_at": None,
            },
        }
    return profiles[domain]


def _detect_cross_month_contradictions(
    claims: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Detect explicit cross-month contradictions on self-claims without mutating history."""
    by_key: dict[str, list[Mapping[str, Any]]] = {}
    for claim in claims:
        key = str(claim.get("claim_key") or "")
        if not key:
            continue
        by_key.setdefault(key, []).append(claim)

    contradictions: list[dict[str, Any]] = []
    for key in sorted(by_key):
        rows = sorted(
            by_key[key],
            key=lambda c: (str(c.get("period_id") or ""), int(c.get("claim_seq") or 0), str(c.get("claim_id") or "")),
        )
        for i in range(len(rows)):
            earlier = rows[i]
            e_pol = str(earlier.get("polarity") or "")
            e_period = str(earlier.get("period_id") or "")
            e_stance = str(earlier.get("stance") or "")
            for j in range(i + 1, len(rows)):
                later = rows[j]
                l_pol = str(later.get("polarity") or "")
                l_period = str(later.get("period_id") or "")
                l_stance = str(later.get("stance") or "")
                if not e_period or not l_period or e_period == l_period:
                    continue
                pol_conflict = l_pol in OPPOSING_POLARITIES.get(e_pol, frozenset())
                stance_conflict = bool(e_stance and l_stance and e_stance != l_stance and e_pol == l_pol and e_pol == "affirm")
                if pol_conflict or stance_conflict:
                    contradictions.append({
                        "contradiction_id": f"ctr_{earlier.get('claim_id')}__{later.get('claim_id')}",
                        "claim_key": key,
                        "domain": str(later.get("domain") or earlier.get("domain") or ""),
                        "earlier_period": e_period,
                        "earlier_claim_id": str(earlier.get("claim_id") or ""),
                        "earlier_polarity": e_pol,
                        "earlier_stance": e_stance,
                        "earlier_statement": str(earlier.get("statement") or ""),
                        "later_period": l_period,
                        "later_claim_id": str(later.get("claim_id") or ""),
                        "later_polarity": l_pol,
                        "later_stance": l_stance,
                        "later_statement": str(later.get("statement") or ""),
                        "status": "CROSS_MONTH_CONTRADICTION",
                        "resolution": "UNRESOLVED_PRESERVED_BOTH",
                    })
    return contradictions


def _normalize_visibility(raw: Any, *, default: str = "private") -> str:
    vis = default if raw is None else _require_str(raw, "visibility")
    if vis not in VISIBILITY_SCOPES:
        raise AutobiographySkillProvenanceError(400, "INVALID_VISIBILITY", f"Unsupported visibility: {vis!r}")
    return vis


def _append_claim_record(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    stamp: str,
) -> dict[str, Any]:
    auto = state["autobiography"]
    period_id = _require_str(payload.get("period_id"), "period_id", max_len=32)
    claim_key = _require_str(payload.get("claim_key"), "claim_key", max_len=120)
    domain = _require_str(payload.get("domain"), "domain", max_len=80)
    lane = _optional_str(payload.get("lane")) or "durable_claim"
    if lane not in CLAIM_LANES:
        raise AutobiographySkillProvenanceError(400, "INVALID_CLAIM_LANE", f"Invalid claim lane: {lane!r}")
    polarity = _optional_str(payload.get("polarity")) or "affirm"
    if polarity not in CLAIM_POLARITIES:
        raise AutobiographySkillProvenanceError(400, "INVALID_POLARITY", f"Invalid polarity: {polarity!r}")
    stance = _optional_str(payload.get("stance"), max_len=80)
    statement = _require_str(payload.get("statement"), "statement", max_len=400)
    visibility = _normalize_visibility(payload.get("visibility"), default="private")
    evidence_refs = _clean_str_list(payload.get("evidence_refs"), "evidence_refs")
    claim_id = _optional_str(payload.get("claim_id"), max_len=80) or _allocate_id(state, "clm")

    claim_seq = len(auto["self_claims"]) + len(auto["temporary_choices"]) + 1
    record = {
        "claim_id": claim_id,
        "claim_seq": claim_seq,
        "period_id": period_id,
        "claim_key": claim_key,
        "domain": domain,
        "lane": lane,
        "polarity": polarity,
        "stance": stance,
        "statement": statement,
        "visibility": visibility,
        "shared": visibility == "shared",
        "evidence_refs": evidence_refs,
        "recorded_at": stamp,
    }

    profile = _ensure_domain_profile(auto, domain)
    if lane == "temporary_choice":
        # F-188: temporary choice stays separate from durable self-claims
        auto["temporary_choices"].append(record)
        if claim_id not in profile["importance_to_self"]["temporary_choice_refs"]:
            profile["importance_to_self"]["temporary_choice_refs"].append(claim_id)
    else:
        auto["self_claims"].append(record)
        imp_val = payload.get("importance_score")
        if imp_val is not None:
            score = _coerce_float(imp_val, "importance_score", minimum=0.0, maximum=1.0)
            imp = profile["importance_to_self"]
            imp["score"] = round(score, 4)
            imp["status"] = "HIGH_IMPORTANCE" if score >= 0.7 else ("PROVISIONAL" if score > 0.0 else "UNSET")
            imp["last_updated_at"] = stamp
            if claim_id not in imp["supporting_choice_refs"]:
                imp["supporting_choice_refs"].append(claim_id)
            if visibility == "private":
                imp["visibility"] = "private"

    return copy.deepcopy(record)


def record_autobiographical_episode(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Record a grounded autobiographical episode (preserving failed/abandoned arcs without backfilling old snapshots)."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        auto = state["autobiography"]
        period_id = _require_str(payload.get("period_id"), "period_id", max_len=32)
        domain = _require_str(payload.get("domain"), "domain", max_len=80)
        title = _require_str(payload.get("title"), "title", max_len=200)
        summary = _require_str(payload.get("summary"), "summary", max_len=600)
        outcome = _require_str(payload.get("outcome"), "outcome", max_len=32)
        if outcome not in EPISODE_OUTCOMES:
            raise AutobiographySkillProvenanceError(400, "INVALID_OUTCOME", f"Unsupported outcome: {outcome!r}")
        arc_id = _optional_str(payload.get("arc_id"), max_len=80)
        arc_status = _optional_str(payload.get("arc_status"), max_len=32) or outcome
        if arc_status not in EPISODE_OUTCOMES:
            raise AutobiographySkillProvenanceError(400, "INVALID_ARC_STATUS", f"Unsupported arc_status: {arc_status!r}")
        occurred_at = _now_iso(_optional_str(payload.get("occurred_at")) or stamp)
        visibility = _normalize_visibility(payload.get("visibility"), default="private")
        source_ref = _optional_str(payload.get("source_ref"), max_len=160)
        episode_id = _optional_str(payload.get("episode_id"), max_len=80) or _allocate_id(state, "ep")
        if not source_ref:
            source_ref = f"autobiography:episode:{episode_id}"

        cap_refs = _clean_str_list(payload.get("capability_evidence_refs"), "capability_evidence_refs")
        importance_signal = payload.get("importance_signal")
        imp_float: Optional[float] = None
        if importance_signal is not None:
            imp_float = _coerce_float(importance_signal, "importance_signal", minimum=0.0, maximum=1.0)

        episode = {
            "episode_id": episode_id,
            "period_id": period_id,
            "domain": domain,
            "title": title,
            "summary": summary,
            "outcome": outcome,
            "arc_id": arc_id or f"arc_{episode_id}",
            "arc_status": arc_status,
            "occurred_at": occurred_at,
            "visibility": visibility,
            "shared": visibility == "shared",
            "source_ref": source_ref,
            "importance_signal": imp_float,
            "capability_evidence_refs": cap_refs,
            "recorded_at": stamp,
        }
        auto["episodes"].append(episode)

        # Importance_to_self grows only from explicit autonomous choice / importance signals,
        # strictly separated from verified_capability.
        profile = _ensure_domain_profile(auto, domain)
        if imp_float is not None:
            imp = profile["importance_to_self"]
            imp["score"] = round(max(float(imp.get("score") or 0.0), imp_float), 4)
            imp["status"] = "HIGH_IMPORTANCE" if imp["score"] >= 0.7 else ("PROVISIONAL" if imp["score"] > 0.0 else "UNSET")
            if episode_id not in imp["supporting_choice_refs"]:
                imp["supporting_choice_refs"].append(episode_id)
            imp["last_updated_at"] = stamp
            if visibility == "private":
                imp["visibility"] = "private"

        claims_added: list[dict[str, Any]] = []
        raw_claims = payload.get("claim_updates")
        if raw_claims is not None:
            if not isinstance(raw_claims, list):
                raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", "claim_updates must be a list")
            for c_item in raw_claims:
                if not isinstance(c_item, Mapping):
                    raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", "claim_updates[] must be a mapping")
                merged_c = dict(c_item)
                merged_c.setdefault("period_id", period_id)
                merged_c.setdefault("domain", domain)
                merged_c.setdefault("visibility", visibility)
                claims_added.append(_append_claim_record(state, merged_c, stamp))

        return {
            "episode": copy.deepcopy(episode),
            "claims_added": claims_added,
        }

    return _run_locked_tx(day_root, action_name="record_autobiographical_episode", payload=payload, now_iso=now_iso, mutator=_mutate)


def record_self_claim(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Append a versioned self-claim or temporary choice without overwriting historical claims."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        claim = _append_claim_record(state, payload, stamp)
        contradictions = _detect_cross_month_contradictions(state["autobiography"]["self_claims"])
        return {
            "claim": claim,
            "cross_month_contradictions": contradictions,
        }

    return _run_locked_tx(day_root, action_name="record_self_claim", payload=payload, now_iso=now_iso, mutator=_mutate)


def compile_period_summary(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Compile a period_summary for ``period_id`` from attested episodes/claims without touching existing snapshots."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        auto = state["autobiography"]
        period_id = _require_str(payload.get("period_id"), "period_id", max_len=32)
        episodes = [ep for ep in auto["episodes"] if ep.get("period_id") == period_id]
        claims = [cl for cl in auto["self_claims"] if cl.get("period_id") == period_id]
        temp_choices = [tc for tc in auto["temporary_choices"] if tc.get("period_id") == period_id]

        existing = auto["period_summaries"].get(period_id)
        prev_rev = int(existing.get("summary_revision") or 0) if isinstance(existing, Mapping) else 0
        summary_rev = prev_rev + 1

        failed_arcs = [
            {
                "arc_id": ep["arc_id"],
                "episode_id": ep["episode_id"],
                "domain": ep["domain"],
                "title": ep["title"],
                "outcome": ep["outcome"],
                "arc_status": ep["arc_status"],
                "visibility": ep["visibility"],
            }
            for ep in episodes
            if ep.get("outcome") in FAILED_OR_ABANDONED_OUTCOMES or ep.get("arc_status") in FAILED_OR_ABANDONED_OUTCOMES
        ]
        succeeded_arcs = [
            {
                "arc_id": ep["arc_id"],
                "episode_id": ep["episode_id"],
                "domain": ep["domain"],
                "title": ep["title"],
                "outcome": ep["outcome"],
                "visibility": ep["visibility"],
            }
            for ep in episodes
            if ep.get("outcome") == "succeeded"
        ]
        period_visibility = "shared" if (episodes or claims) and all(
            item.get("visibility") == "shared" for item in [*episodes, *claims]
        ) else ("private" if (episodes or claims) else _normalize_visibility(payload.get("visibility"), default="shared"))

        summary_doc = {
            "period_id": period_id,
            "summary_revision": summary_rev,
            "compiled_at": stamp,
            "visibility": period_visibility,
            "episode_ids": [ep["episode_id"] for ep in episodes],
            "episode_count": len(episodes),
            "succeeded_arcs": succeeded_arcs,
            "failed_and_abandoned_arcs": failed_arcs,
            "durable_claim_ids": [cl["claim_id"] for cl in claims],
            "temporary_choice_ids": [tc["claim_id"] for tc in temp_choices],
        }
        auto["period_summaries"][period_id] = summary_doc
        return {"period_summary": copy.deepcopy(summary_doc)}

    return _run_locked_tx(day_root, action_name="compile_period_summary", payload=payload, now_iso=now_iso, mutator=_mutate)


def append_narrative_snapshot(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Append an immutable versioned_narrative_snapshot. Historical snapshots are NEVER rewritten or backfilled."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        auto = state["autobiography"]
        visibility = _normalize_visibility(payload.get("visibility"), default="private")
        note = _optional_str(payload.get("note"), max_len=400)
        requested_periods = payload.get("period_ids")
        filter_periods: Optional[set[str]] = None
        if requested_periods is not None:
            filter_periods = set(_clean_str_list(requested_periods, "period_ids"))

        def _vis_ok(item: Mapping[str, Any]) -> bool:
            if visibility == "shared":
                return item.get("visibility") == "shared"
            return True

        episodes = [
            copy.deepcopy(ep)
            for ep in auto["episodes"]
            if _vis_ok(ep) and (filter_periods is None or ep.get("period_id") in filter_periods)
        ]
        claims = [
            copy.deepcopy(cl)
            for cl in auto["self_claims"]
            if _vis_ok(cl) and (filter_periods is None or cl.get("period_id") in filter_periods)
        ]
        temp_choices = [
            copy.deepcopy(tc)
            for tc in auto["temporary_choices"]
            if _vis_ok(tc) and (filter_periods is None or tc.get("period_id") in filter_periods)
        ]
        period_summaries = {
            pid: copy.deepcopy(ps)
            for pid, ps in sorted(auto["period_summaries"].items())
            if _vis_ok(ps) and (filter_periods is None or pid in filter_periods)
        }
        failed_and_abandoned_arcs = [
            {
                "arc_id": ep["arc_id"],
                "episode_id": ep["episode_id"],
                "period_id": ep["period_id"],
                "domain": ep["domain"],
                "title": ep["title"],
                "outcome": ep["outcome"],
                "arc_status": ep["arc_status"],
            }
            for ep in episodes
            if ep.get("outcome") in FAILED_OR_ABANDONED_OUTCOMES or ep.get("arc_status") in FAILED_OR_ABANDONED_OUTCOMES
        ]
        contradictions = _detect_cross_month_contradictions(claims)

        domain_profiles: dict[str, Any] = {}
        for dom, prof in sorted(auto["domain_profiles"].items()):
            imp = prof["importance_to_self"]
            if visibility == "shared" and imp.get("visibility") == "private":
                continue
            domain_profiles[dom] = {
                "domain": dom,
                "importance_to_self": copy.deepcopy(imp),
                "verified_capability": copy.deepcopy(prof["verified_capability"]),
            }

        snap_version = len(auto["versioned_narrative_snapshots"]) + 1
        snap_body = {
            "snapshot_id": _allocate_id(state, "snap"),
            "snapshot_version": snap_version,
            "created_at": stamp,
            "visibility": visibility,
            "append_only": True,
            "immutable": True,
            "note": note,
            "covered_periods": sorted({ep["period_id"] for ep in episodes} | set(period_summaries.keys())),
            "period_summaries": period_summaries,
            "episode_refs": [ep["episode_id"] for ep in episodes],
            "preserved_failed_or_abandoned_arcs": failed_and_abandoned_arcs,
            "durable_claims": claims,
            "temporary_choice_ids": [tc["claim_id"] for tc in temp_choices],
            "cross_month_contradictions": contradictions,
            "domain_profiles": domain_profiles,
        }
        snap_hash = hashlib.sha256(
            json.dumps(snap_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        snap_body["snapshot_hash"] = snap_hash

        auto["versioned_narrative_snapshots"].append(snap_body)
        return {"snapshot": copy.deepcopy(snap_body)}

    return _run_locked_tx(day_root, action_name="append_narrative_snapshot", payload=payload, now_iso=now_iso, mutator=_mutate)


def project_p12_autobiographical_periods(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Zero-write P12 projection with strict partner privacy (excludes private arcs without hidden counts)."""
    stamp = _now_iso(now_iso)
    state_exists, state = _load_state_readonly(day_root)
    auto = state["autobiography"]

    def _allowed(item: Mapping[str, Any]) -> bool:
        if has_subject_capability:
            return True
        return item.get("visibility") == "shared"

    episodes = [copy.deepcopy(ep) for ep in auto["episodes"] if _allowed(ep)]
    claims = [copy.deepcopy(cl) for cl in auto["self_claims"] if _allowed(cl)]
    temp_choices = [copy.deepcopy(tc) for tc in auto["temporary_choices"] if _allowed(tc)]
    period_summaries = {
        pid: copy.deepcopy(ps)
        for pid, ps in sorted(auto["period_summaries"].items())
        if _allowed(ps)
    }
    snapshots = [copy.deepcopy(sn) for sn in auto["versioned_narrative_snapshots"] if _allowed(sn)]

    failed_arcs = [
        {
            "arc_id": ep["arc_id"],
            "episode_id": ep["episode_id"],
            "period_id": ep["period_id"],
            "domain": ep["domain"],
            "title": ep["title"],
            "outcome": ep["outcome"],
            "arc_status": ep["arc_status"],
        }
        for ep in episodes
        if ep.get("outcome") in FAILED_OR_ABANDONED_OUTCOMES or ep.get("arc_status") in FAILED_OR_ABANDONED_OUTCOMES
    ]
    contradictions = _detect_cross_month_contradictions(claims)

    domain_profiles: dict[str, Any] = {}
    for dom, prof in sorted(auto["domain_profiles"].items()):
        imp = prof["importance_to_self"]
        if not has_subject_capability and imp.get("visibility") == "private":
            continue
        domain_profiles[dom] = copy.deepcopy(prof)

    return {
        "schema": SCHEMA_VERSION,
        "state_exists": bool(state_exists),
        "perspective": "subject" if has_subject_capability else "partner",
        "observed_at": stamp,
        "episodes": episodes,
        "failed_and_abandoned_arcs": failed_arcs,
        "period_summaries": period_summaries,
        "versioned_narrative_snapshots": snapshots,
        "durable_claims": claims,
        "temporary_choices": temp_choices,
        "cross_month_contradictions": contradictions,
        "domain_profiles": domain_profiles,
        "llm_calls": 0,
        "zero_write": True,
    }


# ---------------------------------------------------------------------------
# Pillar 2: 4-Way Skill Attribution, Scoped Degradation, Migration (F-084..F-087, F-134..F-139, P29)
# ---------------------------------------------------------------------------


def _skill_scope_key(method_id: str, tool_version: str, model_id: str, scope: str) -> str:
    return f"{method_id}||{tool_version}||{model_id}||{scope}"


def _sync_domain_verified_capability(state: dict[str, Any], skill_rec: Mapping[str, Any], stamp: str) -> None:
    """Update domain verified_capability from skill status WITHOUT altering importance_to_self (F-084/sec 5.2)."""
    domain = str(skill_rec.get("domain") or "")
    if not domain:
        return
    profile = _ensure_domain_profile(state["autobiography"], domain)
    cap = profile["verified_capability"]
    efficacy = skill_rec.get("method_efficacy") or {}
    cap["status"] = str(skill_rec.get("status") or "UNVERIFIED")
    cap["score"] = efficacy.get("success_rate")
    cap["last_verified_at"] = stamp
    rel_ref = f"skill:{skill_rec['method_id']}:{skill_rec['tool_version']}:{skill_rec['model_id']}:{skill_rec['scope']}"
    if rel_ref not in cap["assessment_refs"]:
        cap["assessment_refs"].append(rel_ref)


def register_standard_revision(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Register a versioned evaluation criteria (``standard_revision``), separate from ``method_efficacy`` (F-084)."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        criteria_id = _require_str(payload.get("criteria_id"), "criteria_id", max_len=120)
        standard_revision = _require_str(payload.get("standard_revision"), "standard_revision", max_len=64)
        domain = _require_str(payload.get("domain"), "domain", max_len=80)
        pass_threshold = _coerce_float(payload.get("pass_threshold", 0.75), "pass_threshold", minimum=0.0, maximum=1.0)
        rubric_summary = _require_str(payload.get("rubric_summary"), "rubric_summary", max_len=400)
        change_reason = _optional_str(payload.get("change_reason"), max_len=240)
        overlap_note = _optional_str(payload.get("overlap_comparison_note"), max_len=240)

        standards = state["skills"]["evaluation_standards"]
        history = standards.setdefault(criteria_id, [])
        for existing in history:
            if existing.get("standard_revision") == standard_revision:
                raise AutobiographySkillProvenanceError(
                    409,
                    "DUPLICATE_STANDARD_REVISION",
                    f"standard_revision={standard_revision} already exists for {criteria_id}",
                )

        entry = {
            "criteria_id": criteria_id,
            "standard_revision": standard_revision,
            "domain": domain,
            "pass_threshold": round(pass_threshold, 4),
            "rubric_summary": rubric_summary,
            "change_reason": change_reason,
            "overlap_comparison_note": overlap_note,
            "prior_standard_revision": history[-1]["standard_revision"] if history else None,
            "registered_at": stamp,
        }
        history.append(entry)
        return {"standard_revision_entry": copy.deepcopy(entry)}

    return _run_locked_tx(day_root, action_name="register_standard_revision", payload=payload, now_iso=now_iso, mutator=_mutate)


def register_skill_release(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Register a 4-way attributed skill release scoped by (method_id, tool_version, model_id, scope)."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        method_id = _require_str(payload.get("method_id"), "method_id", max_len=120)
        tool_version = _require_str(payload.get("tool_version"), "tool_version", max_len=64)
        model_id = _require_str(payload.get("model_id"), "model_id", max_len=120)
        scope = _require_str(payload.get("scope"), "scope", max_len=120)
        domain = _optional_str(payload.get("domain"), max_len=80) or scope

        attribution = _require_str(payload.get("attribution"), "attribution", max_len=40)
        if attribution not in SKILL_ATTRIBUTIONS:
            raise AutobiographySkillProvenanceError(400, "INVALID_ATTRIBUTION", f"Invalid attribution: {attribution!r}")

        criteria_id = _require_str(payload.get("criteria_id"), "criteria_id", max_len=120)
        standard_revision = _require_str(payload.get("standard_revision"), "standard_revision", max_len=64)

        held_out = bool(payload.get("held_out", False))
        success_rate = payload.get("success_rate")
        sr_val = _coerce_float(success_rate, "success_rate", minimum=0.0, maximum=1.0) if success_rate is not None else None
        trials = _coerce_int(payload.get("trials", 0), "trials", minimum=0)
        successes = _coerce_int(payload.get("successes", 0), "successes", minimum=0)
        ablation_gain = _coerce_float(payload.get("ablation_gain_vs_bare_model", 0.0), "ablation_gain_vs_bare_model")

        requested_status = _optional_str(payload.get("status")) or SKILL_STATUS_CANDIDATE
        if requested_status not in SKILL_STATUSES:
            raise AutobiographySkillProvenanceError(400, "INVALID_SKILL_STATUS", f"Invalid status: {requested_status!r}")

        # F-135 / F-136 guard: RETRIEVED_GUIDE cannot claim VALIDATED mastery without independent held-out ablation
        if requested_status == SKILL_STATUS_VALIDATED and attribution == SKILL_ATTRIBUTION_RETRIEVED_GUIDE:
            raise AutobiographySkillProvenanceError(
                409,
                "RETRIEVED_GUIDE_CANNOT_CLAIM_MASTERY",
                "RETRIEVED_GUIDE must pass held-out ablation as PRACTICED_MASTERY before VALIDATED",
            )
        if requested_status == SKILL_STATUS_VALIDATED and attribution == SKILL_ATTRIBUTION_PRACTICED_MASTERY:
            if not held_out or ablation_gain <= 0.0:
                raise AutobiographySkillProvenanceError(
                    409,
                    "UNVERIFIED_MASTERY_REQUIRES_HELD_OUT_ABLATION",
                    "PRACTICED_MASTERY VALIDATED requires held_out=True and positive ablation_gain_vs_bare_model",
                )

        source_deps = _clean_str_list(payload.get("source_dependencies"), "source_dependencies")
        title_cn = _optional_str(payload.get("title_cn"), max_len=160)
        s_key = _skill_scope_key(method_id, tool_version, model_id, scope)
        record = {
            "scope_key": s_key,
            "method_id": method_id,
            "title_cn": title_cn,
            "tool_version": tool_version,
            "model_id": model_id,
            "scope": scope,
            "domain": domain,
            "attribution": attribution,
            "status": requested_status,
            "criteria_id": criteria_id,
            "standard_revision": standard_revision,
            "method_efficacy": {
                "success_rate": round(sr_val, 4) if sr_val is not None else None,
                "trials": trials,
                "successes": successes,
                "ablation_gain_vs_bare_model": round(ablation_gain, 4),
                "held_out": held_out,
            },
            "consecutive_failures": 0,
            "independent_failures_total": 0,
            "duplicate_retry_count": 0,
            "ignored_transient_network_errors": 0,
            "last_failure_bug_signature": None,
            "source_dependencies": source_deps,
            "inherited_from": None,
            "reverification_required": False,
            "reverified_against_baseline": requested_status == SKILL_STATUS_VALIDATED,
            "partner_recheck_requested": False,
            "registered_at": stamp,
            "updated_at": stamp,
        }
        state["skills"]["releases"][s_key] = record
        _sync_domain_verified_capability(state, record, stamp)
        return {"skill_release": copy.deepcopy(record)}

    return _run_locked_tx(day_root, action_name="register_skill_release", payload=payload, now_iso=now_iso, mutator=_mutate)


def record_skill_execution_outcome(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Record execution outcome for ``(method_id, tool_version, model_id, scope)``.

    Enforces P29:
    - Transient network/transport errors are logged separately and NEVER penalize skill streak/status.
    - Same-bug retries (matching ``bug_signature``) increment ``duplicate_retry_count`` without inflating streak.
    - Independent attributable failures apply ``2x -> DEGRADED``, ``3x -> SUSPENDED``, or 1x immediate
      ``SUSPENDED`` on ``safety_violation=True``.
    """

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        method_id = _require_str(payload.get("method_id"), "method_id", max_len=120)
        tool_version = _require_str(payload.get("tool_version"), "tool_version", max_len=64)
        model_id = _require_str(payload.get("model_id"), "model_id", max_len=120)
        scope = _require_str(payload.get("scope"), "scope", max_len=120)
        outcome = _require_str(payload.get("outcome"), "outcome", max_len=32)
        if outcome not in ("success", "failure"):
            raise AutobiographySkillProvenanceError(400, "INVALID_OUTCOME", "outcome must be 'success' or 'failure'")

        s_key = _skill_scope_key(method_id, tool_version, model_id, scope)
        releases = state["skills"]["releases"]
        rec = releases.get(s_key)
        if not isinstance(rec, dict):
            raise AutobiographySkillProvenanceError(404, "SKILL_SCOPE_NOT_FOUND", f"No skill registered for {s_key}")

        error_kind = _optional_str(payload.get("error_kind"), max_len=64)
        is_transient = bool(payload.get("is_transient_network", False)) or (error_kind in TRANSIENT_NETWORK_ERROR_KINDS)
        safety_violation = bool(payload.get("safety_violation", False))
        bug_signature = _optional_str(payload.get("bug_signature") or payload.get("retry_group_id"), max_len=120)

        if outcome == "failure" and is_transient:
            rec["ignored_transient_network_errors"] = int(rec.get("ignored_transient_network_errors") or 0) + 1
            rec["updated_at"] = stamp
            incident = {
                "scope_key": s_key,
                "error_kind": error_kind or "transient_network",
                "recorded_at": stamp,
                "penalized_skill": False,
            }
            state["skills"]["network_incident_log"].append(incident)
            return {
                "skill_release": copy.deepcopy(rec),
                "classification": "TRANSIENT_NETWORK_IGNORED",
                "penalized_skill": False,
            }

        eff = rec["method_efficacy"]
        if outcome == "failure":
            if safety_violation:
                rec["consecutive_failures"] = int(rec.get("consecutive_failures") or 0) + 1
                rec["independent_failures_total"] = int(rec.get("independent_failures_total") or 0) + 1
                rec["status"] = SKILL_STATUS_SUSPENDED
                rec["suspension_reason"] = "SAFETY_VIOLATION_IMMEDIATE"
                rec["updated_at"] = stamp
                _sync_domain_verified_capability(state, rec, stamp)
                return {
                    "skill_release": copy.deepcopy(rec),
                    "classification": "SAFETY_VIOLATION_SUSPENDED",
                    "penalized_skill": True,
                }

            if bug_signature and bug_signature == rec.get("last_failure_bug_signature"):
                rec["duplicate_retry_count"] = int(rec.get("duplicate_retry_count") or 0) + 1
                rec["updated_at"] = stamp
                return {
                    "skill_release": copy.deepcopy(rec),
                    "classification": "DUPLICATE_RETRY_DEDUPLICATED",
                    "penalized_skill": False,
                }

            rec["consecutive_failures"] = int(rec.get("consecutive_failures") or 0) + 1
            rec["independent_failures_total"] = int(rec.get("independent_failures_total") or 0) + 1
            rec["last_failure_bug_signature"] = bug_signature or None
            eff["trials"] = int(eff.get("trials") or 0) + 1
            eff["success_rate"] = round(eff["successes"] / eff["trials"], 4) if eff["trials"] > 0 else 0.0

            streak = rec["consecutive_failures"]
            if streak >= SUSPENSION_STREAK_THRESHOLD:
                rec["status"] = SKILL_STATUS_SUSPENDED
                rec["suspension_reason"] = "CONSECUTIVE_FAILURES_3X"
            elif streak >= DEGRADATION_STREAK_THRESHOLD and rec["status"] not in (SKILL_STATUS_SUSPENDED, SKILL_STATUS_RETIRED):
                rec["status"] = SKILL_STATUS_DEGRADED
            rec["updated_at"] = stamp
            _sync_domain_verified_capability(state, rec, stamp)
            return {
                "skill_release": copy.deepcopy(rec),
                "classification": "ATTRIBUTABLE_METHOD_FAILURE",
                "penalized_skill": True,
            }

        # outcome == "success"
        eff["trials"] = int(eff.get("trials") or 0) + 1
        eff["successes"] = int(eff.get("successes") or 0) + 1
        eff["success_rate"] = round(eff["successes"] / eff["trials"], 4)
        rec["consecutive_failures"] = 0
        rec["last_failure_bug_signature"] = None

        recovery_verified = bool(payload.get("recovery_verified", False))
        if rec["status"] in (SKILL_STATUS_DEGRADED, SKILL_STATUS_SUSPENDED) and recovery_verified:
            rec["status"] = SKILL_STATUS_VALIDATED if (
                rec["attribution"] == SKILL_ATTRIBUTION_PRACTICED_MASTERY
                and eff.get("held_out")
                and float(eff.get("ablation_gain_vs_bare_model") or 0.0) > 0.0
                and bool(payload.get("held_out_recovery_passed", False))
            ) else SKILL_STATUS_TRIAL
            rec.pop("suspension_reason", None)
        rec["updated_at"] = stamp
        _sync_domain_verified_capability(state, rec, stamp)
        return {
            "skill_release": copy.deepcopy(rec),
            "classification": "METHOD_SUCCESS",
            "penalized_skill": False,
        }

    return _run_locked_tx(day_root, action_name="record_skill_execution_outcome", payload=payload, now_iso=now_iso, mutator=_mutate)


def bump_carrier_or_tool_version(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Bump model_id or tool_version, preserving old verified records and transitioning inherited skills to UNVALIDATED_AFTER_MIGRATION."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        from_model = _require_str(payload.get("from_model_id"), "from_model_id", max_len=120)
        to_model = _require_str(payload.get("to_model_id"), "to_model_id", max_len=120)
        from_tool = _require_str(payload.get("from_tool_version"), "from_tool_version", max_len=64)
        to_tool = _require_str(payload.get("to_tool_version"), "to_tool_version", max_len=64)
        if from_model == to_model and from_tool == to_tool:
            raise AutobiographySkillProvenanceError(400, "NO_VERSION_CHANGE", "Either model_id or tool_version must change")

        method_filter = payload.get("method_ids")
        allowed_methods: Optional[set[str]] = set(_clean_str_list(method_filter, "method_ids")) if method_filter is not None else None

        releases = state["skills"]["releases"]
        migrated_keys: list[str] = []
        for s_key, rec in list(releases.items()):
            if rec.get("model_id") != from_model or rec.get("tool_version") != from_tool:
                continue
            if allowed_methods is not None and rec.get("method_id") not in allowed_methods:
                continue
            if rec.get("status") == SKILL_STATUS_RETIRED:
                continue

            new_key = _skill_scope_key(rec["method_id"], to_tool, to_model, rec["scope"])
            new_rec = copy.deepcopy(rec)
            new_rec["scope_key"] = new_key
            new_rec["model_id"] = to_model
            new_rec["tool_version"] = to_tool
            new_rec["status"] = SKILL_STATUS_UNVALIDATED_AFTER_MIGRATION
            new_rec["reverification_required"] = True
            new_rec["reverified_against_baseline"] = False
            new_rec["consecutive_failures"] = 0
            new_rec["last_failure_bug_signature"] = None
            new_rec["inherited_from"] = {
                "scope_key": s_key,
                "model_id": from_model,
                "tool_version": from_tool,
                "prior_status": rec["status"],
                "prior_efficacy": copy.deepcopy(rec["method_efficacy"]),
            }
            new_rec["updated_at"] = stamp
            releases[new_key] = new_rec
            migrated_keys.append(new_key)
            # Only updates verified_capability on the domain; importance_to_self stays untouched
            _sync_domain_verified_capability(state, new_rec, stamp)

        state["skills"]["migration_history"].append({
            "from_model_id": from_model,
            "to_model_id": to_model,
            "from_tool_version": from_tool,
            "to_tool_version": to_tool,
            "migrated_scope_keys": migrated_keys,
            "migrated_at": stamp,
        })
        return {
            "migrated_scope_keys": migrated_keys,
            "migrated_count": len(migrated_keys),
        }

    return _run_locked_tx(day_root, action_name="bump_carrier_or_tool_version", payload=payload, now_iso=now_iso, mutator=_mutate)


def reverify_migrated_skill(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Re-verify an inherited skill against bare new model and old baseline (F-139, Q36)."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        method_id = _require_str(payload.get("method_id"), "method_id", max_len=120)
        tool_version = _require_str(payload.get("tool_version"), "tool_version", max_len=64)
        model_id = _require_str(payload.get("model_id"), "model_id", max_len=120)
        scope = _require_str(payload.get("scope"), "scope", max_len=120)

        bare_score = _coerce_float(payload.get("bare_model_score"), "bare_model_score", minimum=0.0, maximum=1.0)
        method_score = _coerce_float(payload.get("method_with_new_model_score"), "method_with_new_model_score", minimum=0.0, maximum=1.0)
        old_baseline = _coerce_float(payload.get("old_baseline_score"), "old_baseline_score", minimum=0.0, maximum=1.0)
        trials = _coerce_int(payload.get("held_out_trials"), "held_out_trials", minimum=1)
        min_threshold = _coerce_float(payload.get("pass_threshold", 0.70), "pass_threshold", minimum=0.0, maximum=1.0)

        s_key = _skill_scope_key(method_id, tool_version, model_id, scope)
        releases = state["skills"]["releases"]
        rec = releases.get(s_key)
        if not isinstance(rec, dict):
            raise AutobiographySkillProvenanceError(404, "SKILL_SCOPE_NOT_FOUND", f"No skill registered for {s_key}")

        gain_over_bare = round(method_score - bare_score, 4)
        delta_vs_old = round(method_score - old_baseline, 4)
        rec["method_efficacy"] = {
            "success_rate": round(method_score, 4),
            "trials": trials,
            "successes": int(round(method_score * trials)),
            "ablation_gain_vs_bare_model": gain_over_bare,
            "delta_vs_old_baseline": delta_vs_old,
            "bare_model_score": round(bare_score, 4),
            "old_baseline_score": round(old_baseline, 4),
            "held_out": True,
        }
        rec["reverification_required"] = False
        rec["reverified_against_baseline"] = True
        rec["partner_recheck_requested"] = False
        rec["updated_at"] = stamp
        for rreq in state["skills"].setdefault("recheck_requests", []):
            if not isinstance(rreq, dict) or rreq.get("status") != "QUEUED":
                continue
            req_sk = rreq.get("scope_key")
            if (req_sk and req_sk == s_key) or (not req_sk and rreq.get("method_id") == method_id):
                rreq["status"] = "RESOLVED"
                rreq["resolved_at"] = stamp

        native_entry: Optional[dict[str, Any]] = None
        if gain_over_bare > 0.0 and method_score >= min_threshold:
            rec["status"] = SKILL_STATUS_VALIDATED
            verdict = "VALIDATED_METHOD_RETAINED"
        elif bare_score >= method_score and bare_score >= min_threshold:
            # Bare new model supersedes method -> retire old method, credit native capability to MODEL_NATIVE (never PRACTICED_MASTERY!)
            rec["status"] = SKILL_STATUS_RETIRED
            rec["retired_reason"] = "BARE_MODEL_SUPERSEDES_METHOD"
            verdict = "RETIRED_TO_MODEL_NATIVE"
            native_key = _skill_scope_key(f"{method_id}__bare_native", tool_version, model_id, scope)
            native_entry = {
                "scope_key": native_key,
                "method_id": f"{method_id}__bare_native",
                "tool_version": tool_version,
                "model_id": model_id,
                "scope": scope,
                "domain": rec.get("domain", scope),
                "attribution": SKILL_ATTRIBUTION_MODEL_NATIVE,
                "status": SKILL_STATUS_VALIDATED,
                "criteria_id": rec["criteria_id"],
                "standard_revision": rec["standard_revision"],
                "method_efficacy": {
                    "success_rate": round(bare_score, 4),
                    "trials": trials,
                    "successes": int(round(bare_score * trials)),
                    "ablation_gain_vs_bare_model": 0.0,
                    "held_out": True,
                },
                "consecutive_failures": 0,
                "independent_failures_total": 0,
                "duplicate_retry_count": 0,
                "ignored_transient_network_errors": 0,
                "last_failure_bug_signature": None,
                "source_dependencies": [],
                "inherited_from": None,
                "reverification_required": False,
                "reverified_against_baseline": True,
                "registered_at": stamp,
                "updated_at": stamp,
            }
            releases[native_key] = native_entry
        else:
            rec["status"] = SKILL_STATUS_DEGRADED
            verdict = "DEGRADED_BELOW_THRESHOLD"

        _sync_domain_verified_capability(state, native_entry or rec, stamp)
        return {
            "skill_release": copy.deepcopy(rec),
            "model_native_release": copy.deepcopy(native_entry) if native_entry is not None else None,
            "verdict": verdict,
        }

    return _run_locked_tx(day_root, action_name="reverify_migrated_skill", payload=payload, now_iso=now_iso, mutator=_mutate)


def revoke_skill_source_material(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Suspend skills whose training/derivation source material was deleted (F-137)."""
    if not isinstance(payload, Mapping):
        raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", "payload must be a mapping")
    raw_ref = _optional_str(payload.get("source_ref"), max_len=160)
    raw_id = _optional_str(payload.get("source_id"), max_len=160)
    if not raw_ref and not raw_id:
        raise AutobiographySkillProvenanceError(
            400,
            "INVALID_FIELD",
            "source_ref or source_id must be a non-empty string",
        )
    source_ref = raw_ref or raw_id
    reason_cn = _optional_str(payload.get("reason_cn"), max_len=400)
    match_targets = {tok for tok in (raw_ref, raw_id) if tok}

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        affected: list[str] = []
        for s_key, rec in state["skills"]["releases"].items():
            deps = rec.get("source_dependencies") or []
            if any(dep in match_targets for dep in deps):
                rec["status"] = SKILL_STATUS_SUSPENDED
                rec["suspension_reason"] = "SOURCE_MATERIAL_DELETED"
                rec["revoked_source_ref"] = source_ref
                if reason_cn:
                    rec["revoked_reason_cn"] = reason_cn
                rec["updated_at"] = stamp
                _sync_domain_verified_capability(state, rec, stamp)
                affected.append(s_key)
        out: dict[str, Any] = {
            "revoked_source_ref": source_ref,
            "suspended_scope_keys": affected,
        }
        if raw_id:
            out["revoked_source_id"] = raw_id
        if reason_cn:
            out["reason_cn"] = reason_cn
        return out

    return _run_locked_tx(day_root, action_name="revoke_skill_source_material", payload=payload, now_iso=now_iso, mutator=_mutate)


def project_skill_provenance(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Zero-write projection of 4-way attributed skills, separated standard revisions, and network incident counts."""
    stamp = _now_iso(now_iso)
    state_exists, state = _load_state_readonly(day_root)
    skills = state["skills"]
    return {
        "schema": SCHEMA_VERSION,
        "state_exists": bool(state_exists),
        "observed_at": stamp,
        "evaluation_standards": copy.deepcopy(skills["evaluation_standards"]),
        "releases": copy.deepcopy(skills["releases"]),
        "migration_history": copy.deepcopy(skills["migration_history"]),
        "network_incident_log": copy.deepcopy(skills["network_incident_log"]),
        "method_patches": copy.deepcopy(skills.get("method_patches") or []),
        "recheck_requests": copy.deepcopy(skills.get("recheck_requests") or []),
        "llm_calls": 0,
        "zero_write": True,
    }


def _redact_host_paths_text(text: str) -> str:
    if not text:
        return ""
    return _HOST_FS_PATH_RE.sub("[local-path-redacted]", text)


def _skill_status_label_cn(status: str) -> str:
    if status in SKILL_STATUS_LABELS_CN:
        return SKILL_STATUS_LABELS_CN[status]
    if status == "PENDING_REVERIFY":
        return SKILL_STATUS_LABELS_CN[SKILL_STATUS_UNVALIDATED_AFTER_MIGRATION]
    if status == "SUSPENDED_AFTER_TRIAL_LOSS":
        return SKILL_STATUS_LABELS_CN[SKILL_STATUS_SUSPENDED]
    return status


def _normalize_actor_side(actor_side: str) -> str:
    if not isinstance(actor_side, str) or not actor_side.strip():
        raise AutobiographySkillProvenanceError(403, "FORBIDDEN_ACTOR_SIDE", "Invalid actor_side")
    norm = actor_side.strip().lower()
    if norm == "her":
        norm = "subject"
    if norm not in ("partner", "subject"):
        raise AutobiographySkillProvenanceError(403, "FORBIDDEN_ACTOR_SIDE", f"Unsupported actor_side: {actor_side!r}")
    return norm


def _find_skill_release_for_retirement(
    releases: dict[str, dict[str, Any]],
    payload: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    raw_scope_key = _optional_str(payload.get("scope_key"), max_len=360)
    if raw_scope_key:
        rec = releases.get(raw_scope_key)
        if isinstance(rec, dict):
            return raw_scope_key, rec
        raise AutobiographySkillProvenanceError(
            404,
            "SKILL_SCOPE_NOT_FOUND",
            f"No skill registered for scope_key={raw_scope_key}",
        )

    method_id = _require_str(payload.get("method_id"), "method_id", max_len=120)
    raw_tool = payload.get("tool_version")
    raw_model = payload.get("model_id")
    raw_scope = payload.get("scope")
    if raw_tool is not None or raw_model is not None or raw_scope is not None:
        tool_version = _require_str(raw_tool, "tool_version", max_len=64)
        model_id = _require_str(raw_model, "model_id", max_len=120)
        scope = _require_str(raw_scope, "scope", max_len=120)
        s_key = _skill_scope_key(method_id, tool_version, model_id, scope)
        rec = releases.get(s_key)
        if isinstance(rec, dict):
            return s_key, rec
        raise AutobiographySkillProvenanceError(
            404,
            "SKILL_SCOPE_NOT_FOUND",
            f"No skill registered for {s_key}",
        )

    matches = [
        (k, r)
        for k, r in releases.items()
        if isinstance(r, dict) and r.get("method_id") == method_id
    ]
    if not matches:
        raise AutobiographySkillProvenanceError(
            404,
            "SKILL_SCOPE_NOT_FOUND",
            f"No skill registered for method_id={method_id}",
        )
    non_retired = [(k, r) for k, r in matches if r.get("status") != SKILL_STATUS_RETIRED]
    chosen_pool = non_retired or matches
    chosen_pool.sort(key=lambda pair: (str(pair[1].get("updated_at") or ""), pair[0]))
    return chosen_pool[-1]


def retire_skill_release(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Gracefully retire an existing skill release while preserving all historical efficacy records (F-138)."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        releases = state["skills"].setdefault("releases", {})
        s_key, rec = _find_skill_release_for_retirement(releases, payload)
        retired_reason = _optional_str(payload.get("retired_reason"), max_len=160) or "GRACEFUL_RETIREMENT"
        retirement_note_cn = _optional_str(payload.get("retirement_note_cn"), max_len=400)

        rec["status"] = SKILL_STATUS_RETIRED
        rec["retired_reason"] = retired_reason
        rec["retirement_note_cn"] = retirement_note_cn
        rec["retired_at"] = stamp
        rec["reverification_required"] = False
        rec["partner_recheck_requested"] = False
        rec["updated_at"] = stamp
        _sync_domain_verified_capability(state, rec, stamp)
        return {"skill_release": copy.deepcopy(rec), "scope_key": s_key}

    return _run_locked_tx(day_root, action_name="retire_skill_release", payload=payload, now_iso=now_iso, mutator=_mutate)


def record_targeted_method_patch(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Record or verify a structured failure->method intervention patch (F-190)."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        patches = state["skills"].setdefault("method_patches", [])
        raw_retest = _optional_str(payload.get("retest_status"), max_len=32) or "PENDING_RETEST"
        if raw_retest not in METHOD_PATCH_RETEST_STATUSES:
            raise AutobiographySkillProvenanceError(
                400,
                "INVALID_RETEST_STATUS",
                f"retest_status must be one of {sorted(METHOD_PATCH_RETEST_STATUSES)}",
            )
        if "shared" in payload and not isinstance(payload["shared"], bool):
            raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", "shared must be a boolean")

        provided_patch_id = _optional_str(payload.get("patch_id"), max_len=80)
        if provided_patch_id:
            for existing in patches:
                if isinstance(existing, dict) and existing.get("patch_id") == provided_patch_id:
                    if raw_retest == "VERIFIED":
                        if payload.get("method_id") is not None:
                            existing["method_id"] = _require_str(payload.get("method_id"), "method_id", max_len=120)
                        if payload.get("scope") is not None:
                            existing["scope"] = _require_str(payload.get("scope"), "scope", max_len=120)
                        if payload.get("failure_symptom_cn") is not None:
                            existing["failure_symptom_cn"] = _require_str(
                                payload.get("failure_symptom_cn"), "failure_symptom_cn", max_len=400
                            )
                        if payload.get("root_cause_hypothesis") is not None:
                            existing["root_cause_hypothesis"] = _require_str(
                                payload.get("root_cause_hypothesis"), "root_cause_hypothesis", max_len=400
                            )
                        if payload.get("method_change_cn") is not None:
                            existing["method_change_cn"] = _require_str(
                                payload.get("method_change_cn"), "method_change_cn", max_len=400
                            )
                        if "shared" in payload:
                            existing["shared"] = bool(payload["shared"])
                        existing["retest_status"] = "VERIFIED"
                        existing["verified_at"] = stamp
                        existing["updated_at"] = stamp
                        return {"method_patch": copy.deepcopy(existing), "updated_existing": True}
                    raise AutobiographySkillProvenanceError(
                        409,
                        "DUPLICATE_PATCH_ID",
                        f"patch_id={provided_patch_id} already exists",
                    )

        method_id = _require_str(payload.get("method_id"), "method_id", max_len=120)
        scope = _require_str(payload.get("scope"), "scope", max_len=120)
        failure_symptom_cn = _require_str(payload.get("failure_symptom_cn"), "failure_symptom_cn", max_len=400)
        root_cause_hypothesis = _require_str(
            payload.get("root_cause_hypothesis"), "root_cause_hypothesis", max_len=400
        )
        method_change_cn = _require_str(payload.get("method_change_cn"), "method_change_cn", max_len=400)
        shared = bool(payload.get("shared", True))
        patch_id = provided_patch_id or _allocate_id(state, "mpatch")

        entry = {
            "patch_id": patch_id,
            "method_id": method_id,
            "scope": scope,
            "failure_symptom_cn": failure_symptom_cn,
            "root_cause_hypothesis": root_cause_hypothesis,
            "method_change_cn": method_change_cn,
            "retest_status": raw_retest,
            "shared": shared,
            "recorded_at": stamp,
            "updated_at": stamp,
            "verified_at": stamp if raw_retest == "VERIFIED" else None,
        }
        patches.append(entry)
        return {"method_patch": copy.deepcopy(entry), "updated_existing": False}

    return _run_locked_tx(
        day_root,
        action_name="record_targeted_method_patch",
        payload=payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


def request_skill_recheck(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Queue a non-destructive recheck request for a skill without falsifying validation status."""
    norm_actor = _normalize_actor_side(actor_side)
    tx_payload = dict(payload)
    tx_payload["_actor_side"] = norm_actor

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        method_id = _require_str(payload.get("method_id"), "method_id", max_len=120)
        scope_key = _optional_str(payload.get("scope_key"), max_len=360)
        note_cn = _optional_str(payload.get("note_cn"), max_len=400)
        request_id = _optional_str(payload.get("request_id"), max_len=80) or _allocate_id(state, "rechk")

        req_entry = {
            "request_id": request_id,
            "method_id": method_id,
            "scope_key": scope_key or None,
            "note_cn": note_cn,
            "requested_by": norm_actor,
            "status": "QUEUED",
            "requested_at": stamp,
        }
        state["skills"].setdefault("recheck_requests", []).append(req_entry)

        matched_keys: list[str] = []
        releases = state["skills"].setdefault("releases", {})
        if scope_key:
            rec = releases.get(scope_key)
            if isinstance(rec, dict):
                rec["partner_recheck_requested"] = True
                rec["recheck_requested_at"] = stamp
                matched_keys.append(scope_key)
        else:
            for sk, rec in releases.items():
                if isinstance(rec, dict) and rec.get("method_id") == method_id:
                    rec["partner_recheck_requested"] = True
                    rec["recheck_requested_at"] = stamp
                    matched_keys.append(sk)

        return {
            "recheck_request": copy.deepcopy(req_entry),
            "matched_scope_keys": matched_keys,
        }

    return _run_locked_tx(
        day_root,
        action_name="request_skill_recheck",
        payload=tx_payload,
        now_iso=now_iso,
        mutator=_mutate,
    )


def _read_runtime_migration_json_readonly(day_root: Path | str) -> tuple[bool, dict[str, Any]]:
    path = Path(day_root) / "phone" / "v02_runtime_migration.json"
    try:
        if path.is_symlink() or not path.is_file():
            return False, {}
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, Mapping):
            return True, dict(parsed)
    except (OSError, UnicodeError, ValueError):
        return False, {}
    return False, {}


def _infer_runtime_skill_attribution(raw_sk: Mapping[str, Any]) -> str:
    explicit = str(raw_sk.get("attribution") or "").strip()
    if explicit in SKILL_ATTRIBUTIONS:
        return explicit
    category = str(raw_sk.get("category") or "").strip()
    status = str(raw_sk.get("status") or "").strip()
    if category in ("universal_tool", "governance_contract", "carrier_coupled_practice"):
        return SKILL_ATTRIBUTION_BORROWED_TOOL
    if bool(raw_sk.get("verified_practiced", False)) or (
        category == "personal_technique"
        and status == SKILL_STATUS_VALIDATED
        and bool(str(raw_sk.get("last_trial_note_cn") or "").strip())
    ):
        return SKILL_ATTRIBUTION_PRACTICED_MASTERY
    if bool(raw_sk.get("model_sensitive", False)):
        return SKILL_ATTRIBUTION_MODEL_NATIVE
    return SKILL_ATTRIBUTION_BORROWED_TOOL


def _normalize_runtime_skill_status(raw_status: str) -> str:
    st = str(raw_status or SKILL_STATUS_VALIDATED).strip()
    if st == "PENDING_REVERIFY":
        return SKILL_STATUS_UNVALIDATED_AFTER_MIGRATION
    if st == "SUSPENDED_AFTER_TRIAL_LOSS":
        return SKILL_STATUS_SUSPENDED
    if st in SKILL_STATUSES:
        return st
    return SKILL_STATUS_CANDIDATE


def project_personal_skill_book_zero_write(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write personal skill book merging provenance releases & runtime migration skills."""
    stamp = _now_iso(now_iso)
    state_exists, state = _load_state_readonly(day_root)
    rt_exists, rt_doc = _read_runtime_migration_json_readonly(day_root)

    skills = state["skills"]
    releases = skills.get("releases") or {}
    raw_patches = skills.get("method_patches") or []
    raw_rechecks = skills.get("recheck_requests") or []
    raw_standards = skills.get("evaluation_standards") or {}

    queued_recheck_methods: set[str] = set()
    for rreq in raw_rechecks:
        if isinstance(rreq, Mapping) and rreq.get("status") == "QUEUED":
            mid = str(rreq.get("method_id") or "").strip()
            if mid:
                queued_recheck_methods.add(mid)

    def _clean_text(val: Any) -> str:
        s = str(val or "")
        return s if has_subject_capability else _redact_host_paths_text(s)

    entries: list[dict[str, Any]] = []
    represented_method_ids: set[str] = set()

    for s_key in sorted(releases.keys()):
        rec = releases[s_key]
        if not isinstance(rec, Mapping):
            continue
        method_id = str(rec.get("method_id") or "")
        if method_id:
            represented_method_ids.add(method_id)
        attribution = str(rec.get("attribution") or SKILL_ATTRIBUTION_MODEL_NATIVE)
        status = str(rec.get("status") or SKILL_STATUS_CANDIDATE)
        eff = copy.deepcopy(dict(rec.get("method_efficacy") or {}))
        entry: dict[str, Any] = {
            "entry_id": str(s_key),
            "source": "skill_provenance",
            "scope_key": str(s_key),
            "method_id": method_id,
            "title_cn": _clean_text(rec.get("title_cn") or method_id),
            "domain": str(rec.get("domain") or rec.get("scope") or ""),
            "scope": str(rec.get("scope") or ""),
            "model_id": str(rec.get("model_id") or ""),
            "tool_version": str(rec.get("tool_version") or ""),
            "attribution": attribution,
            "attribution_label_cn": SKILL_ATTRIBUTION_LABELS_CN.get(attribution, attribution),
            "status": status,
            "status_label_cn": _skill_status_label_cn(status),
            "criteria_id": str(rec.get("criteria_id") or ""),
            "standard_revision": str(rec.get("standard_revision") or ""),
            "method_efficacy": eff,
            "consecutive_failures": int(rec.get("consecutive_failures") or 0),
            "reverification_required": bool(rec.get("reverification_required", False)),
            "reverified_against_baseline": bool(rec.get("reverified_against_baseline", False)),
            "partner_recheck_requested": bool(rec.get("partner_recheck_requested", False))
            or (method_id in queued_recheck_methods),
            "retired_reason": rec.get("retired_reason"),
            "retirement_note_cn": _clean_text(rec.get("retirement_note_cn")),
            "retired_at": rec.get("retired_at"),
            "updated_at": rec.get("updated_at"),
        }
        if has_subject_capability:
            entry["source_dependencies"] = copy.deepcopy(list(rec.get("source_dependencies") or []))
            entry["inherited_from"] = copy.deepcopy(rec.get("inherited_from"))
            entry["independent_failures_total"] = int(rec.get("independent_failures_total") or 0)
            entry["duplicate_retry_count"] = int(rec.get("duplicate_retry_count") or 0)
            entry["ignored_transient_network_errors"] = int(rec.get("ignored_transient_network_errors") or 0)
            if "suspension_reason" in rec:
                entry["suspension_reason"] = rec.get("suspension_reason")
        entries.append(entry)

    # Merge runtime migration today_capability_card / carrier_migration skills not yet represented by method_id
    runtime_skills_map: dict[str, Mapping[str, Any]] = {}
    if rt_exists:
        carrier_sec = rt_doc.get("carrier_migration")
        if isinstance(carrier_sec, Mapping) and isinstance(carrier_sec.get("skills"), Mapping):
            for sk_id, raw_sk in carrier_sec["skills"].items():
                if isinstance(raw_sk, Mapping):
                    runtime_skills_map[str(sk_id)] = raw_sk
        tcc = rt_doc.get("today_capability_card")
        if isinstance(tcc, Mapping) and isinstance(tcc.get("skills"), Mapping):
            for sk_id, raw_sk in tcc["skills"].items():
                if isinstance(raw_sk, Mapping) and str(sk_id) not in runtime_skills_map:
                    runtime_skills_map[str(sk_id)] = raw_sk

        fp = (
            carrier_sec.get("carrier_fingerprint")
            if isinstance(carrier_sec, Mapping) and isinstance(carrier_sec.get("carrier_fingerprint"), Mapping)
            else {}
        )
        cur_model = str(fp.get("model_id") or "")
        for sk_id in sorted(runtime_skills_map.keys()):
            if sk_id in represented_method_ids:
                continue
            raw_sk = runtime_skills_map[sk_id]
            attribution = _infer_runtime_skill_attribution(raw_sk)
            raw_status = str(raw_sk.get("status") or SKILL_STATUS_VALIDATED)
            status = _normalize_runtime_skill_status(raw_status)
            entries.append({
                "entry_id": f"runtime::{sk_id}",
                "source": "runtime_migration",
                "scope_key": f"runtime::{sk_id}",
                "method_id": sk_id,
                "title_cn": _clean_text(raw_sk.get("title_cn") or sk_id),
                "plain_desc_cn": _clean_text(raw_sk.get("plain_desc_cn") or ""),
                "domain": str(raw_sk.get("category") or "runtime_skill"),
                "scope": "runtime_carrier",
                "model_id": str(raw_sk.get("verified_model_id") or cur_model),
                "tool_version": "runtime_host",
                "attribution": attribution,
                "attribution_label_cn": SKILL_ATTRIBUTION_LABELS_CN.get(attribution, attribution),
                "status": status,
                "status_label_cn": _skill_status_label_cn(raw_status),
                "criteria_id": "",
                "standard_revision": "",
                "method_efficacy": {},
                "consecutive_failures": 0,
                "reverification_required": status == SKILL_STATUS_UNVALIDATED_AFTER_MIGRATION,
                "reverified_against_baseline": status == SKILL_STATUS_VALIDATED,
                "partner_recheck_requested": sk_id in queued_recheck_methods,
                "retired_reason": None,
                "retirement_note_cn": "",
                "retired_at": None,
                "updated_at": rt_doc.get("updated_at"),
            })

    counts_by_attribution: dict[str, int] = {
        SKILL_ATTRIBUTION_MODEL_NATIVE: 0,
        SKILL_ATTRIBUTION_BORROWED_TOOL: 0,
        SKILL_ATTRIBUTION_RETRIEVED_GUIDE: 0,
        SKILL_ATTRIBUTION_PRACTICED_MASTERY: 0,
    }
    unvalidated_count = 0
    retired_count = 0
    for ent in entries:
        attr = str(ent.get("attribution") or "")
        if attr in counts_by_attribution:
            counts_by_attribution[attr] += 1
        st = str(ent.get("status") or "")
        if st == SKILL_STATUS_UNVALIDATED_AFTER_MIGRATION:
            unvalidated_count += 1
        elif st == SKILL_STATUS_RETIRED:
            retired_count += 1

    visible_patches: list[dict[str, Any]] = []
    for p in raw_patches:
        if not isinstance(p, Mapping):
            continue
        is_shared = bool(p.get("shared", True))
        if not has_subject_capability and not is_shared:
            continue
        visible_patches.append({
            "patch_id": str(p.get("patch_id") or ""),
            "method_id": str(p.get("method_id") or ""),
            "scope": str(p.get("scope") or ""),
            "failure_symptom_cn": _clean_text(p.get("failure_symptom_cn")),
            "root_cause_hypothesis": _clean_text(p.get("root_cause_hypothesis")),
            "method_change_cn": _clean_text(p.get("method_change_cn")),
            "retest_status": str(p.get("retest_status") or "PENDING_RETEST"),
            "shared": is_shared,
            "recorded_at": p.get("recorded_at"),
            "updated_at": p.get("updated_at"),
            "verified_at": p.get("verified_at"),
        })

    visible_recheck_requests: list[dict[str, Any]] = []
    for r in raw_rechecks:
        if not isinstance(r, Mapping):
            continue
        visible_recheck_requests.append({
            "request_id": str(r.get("request_id") or ""),
            "method_id": str(r.get("method_id") or ""),
            "scope_key": r.get("scope_key"),
            "note_cn": _clean_text(r.get("note_cn")),
            "requested_by": str(r.get("requested_by") or "partner"),
            "status": str(r.get("status") or "QUEUED"),
            "requested_at": r.get("requested_at"),
            "resolved_at": r.get("resolved_at"),
        })

    visible_standards: dict[str, list[dict[str, Any]]] = {}
    for cid, revs in sorted(raw_standards.items()):
        if not isinstance(revs, list):
            continue
        clean_revs: list[dict[str, Any]] = []
        for rev in revs:
            if not isinstance(rev, Mapping):
                continue
            item = copy.deepcopy(dict(rev))
            if not has_subject_capability:
                item["rubric_summary"] = _clean_text(item.get("rubric_summary"))
                item["change_reason"] = _clean_text(item.get("change_reason"))
                item["overlap_comparison_note"] = _clean_text(item.get("overlap_comparison_note"))
            clean_revs.append(item)
        visible_standards[str(cid)] = clean_revs

    return {
        "schema": SCHEMA_VERSION,
        "state_exists": bool(state_exists or rt_exists),
        "perspective": "subject" if has_subject_capability else "partner",
        "observed_at": stamp,
        "entries": entries,
        "counts_by_attribution": counts_by_attribution,
        "unvalidated_count": unvalidated_count,
        "retired_count": retired_count,
        "method_patches": visible_patches,
        "recheck_requests": visible_recheck_requests,
        "evaluation_standards": visible_standards,
        "attribution_labels_cn": dict(SKILL_ATTRIBUTION_LABELS_CN),
        "status_labels_cn": dict(SKILL_STATUS_LABELS_CN),
        "llm_calls": 0,
        "zero_write": True,
    }


# ---------------------------------------------------------------------------
# Pillar 3: Zero-Write Situation & Sampling Provenance View (F-186, F-187, F-193)
# ---------------------------------------------------------------------------


def _evaluate_candidate_sampling(
    candidates: Sequence[Mapping[str, Any]],
    *,
    budget_token_cap: int,
    max_items: int,
    ranking_rule: str,
) -> dict[str, Any]:
    ordered = sorted(
        (dict(c) for c in candidates),
        key=lambda c: (-float(c.get("salience") or 0.0), str(c.get("candidate_id") or "")),
    )
    sampled: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}
    used_tokens = 0

    for cand in ordered:
        cid = str(cand.get("candidate_id") or "")
        vis = str(cand.get("visibility") or "private")
        if vis not in VISIBILITY_SCOPES:
            vis = "private"
        tokens = max(0, int(cand.get("estimated_tokens") or 0))
        entry = {
            "candidate_id": cid,
            "source_domain": str(cand.get("source_domain") or "unknown"),
            "title": str(cand.get("title") or ""),
            "visibility": vis,
            "salience": float(cand.get("salience") or 0.0),
            "estimated_tokens": tokens,
        }

        drop_reason: Optional[str] = None
        if cand.get("permission_ok") is False:
            drop_reason = "PERMISSION_DENIED"
        elif cand.get("stale") is True:
            drop_reason = "STALE_SOURCE"
        elif cand.get("duplicate_of"):
            drop_reason = "DUPLICATE_COALESCED"
        elif len(sampled) >= max_items:
            drop_reason = "MAX_ITEMS_TRIMMED"
        elif used_tokens + tokens > budget_token_cap:
            drop_reason = "BUDGET_CAP_TRIMMED"
            
        # print(f"DEBUG: cand={cid}, permission_ok={cand.get('permission_ok')}, drop_reason={drop_reason}, used_tokens={used_tokens}, tokens={tokens}")

        if drop_reason is not None:
            dropped_entry = dict(entry)
            dropped_entry["drop_reason"] = drop_reason
            dropped.append(dropped_entry)
            reason_counts[drop_reason] = reason_counts.get(drop_reason, 0) + 1
        else:
            used_tokens += tokens
            sampled.append(entry)

    return {
        "ranking_rule": ranking_rule,
        "active_budget_token_cap": budget_token_cap,
        "max_items": max_items,
        "used_tokens": used_tokens,
        "sampled_candidates": sampled,
        "dropped_candidates": dropped,
        "drop_reason_counts": reason_counts,
    }


def record_workspace_candidate_audit(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Persist a deterministic workspace candidate sampling vs drop/filter audit (F-187)."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        raw_cands = payload.get("candidates")
        if not isinstance(raw_cands, list):
            raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", "candidates must be a list")
        budget_token_cap = _coerce_int(payload.get("budget_token_cap", 2000), "budget_token_cap", minimum=0)
        max_items = _coerce_int(payload.get("max_items", 8), "max_items", minimum=0)
        ranking_rule = _optional_str(payload.get("ranking_rule"), max_len=64) or "salience_desc"

        evaluated = _evaluate_candidate_sampling(
            raw_cands,
            budget_token_cap=budget_token_cap,
            max_items=max_items,
            ranking_rule=ranking_rule,
        )
        audit_entry = {
            "audit_id": _allocate_id(state, "aud"),
            "recorded_at": stamp,
            **evaluated,
        }
        audits = state["situation_audits"]["workspace_candidate_audits"]
        audits.append(audit_entry)
        if len(audits) > 32:
            del audits[: len(audits) - 32]
        return {"candidate_audit": copy.deepcopy(audit_entry)}

    return _run_locked_tx(day_root, action_name="record_workspace_candidate_audit", payload=payload, now_iso=now_iso, mutator=_mutate)


def _filter_audit_for_perspective(audit: Mapping[str, Any], *, has_subject_capability: bool) -> dict[str, Any]:
    if has_subject_capability:
        return copy.deepcopy(dict(audit))
    # Strict partner privacy: omit private candidates completely without exposing hidden counts!
    sampled = [copy.deepcopy(c) for c in (audit.get("sampled_candidates") or []) if c.get("visibility") == "shared"]
    dropped = [copy.deepcopy(c) for c in (audit.get("dropped_candidates") or []) if c.get("visibility") == "shared"]
    reason_counts: dict[str, int] = {}
    for c in dropped:
        r = str(c.get("drop_reason") or "")
        if r:
            reason_counts[r] = reason_counts.get(r, 0) + 1
    out = copy.deepcopy(dict(audit))
    out["sampled_candidates"] = sampled
    out["dropped_candidates"] = dropped
    out["drop_reason_counts"] = reason_counts
    return out


def _compute_epistemic_status(
    *,
    source_exists: bool,
    synced_at: Optional[str],
    now_dt: datetime,
    staleness_threshold_seconds: int,
    has_unreconciled: bool = False,
) -> tuple[str, Optional[float]]:
    if not source_exists:
        return EPISTEMIC_UNMATERIALIZED, None
    if synced_at is None:
        return EPISTEMIC_UNKNOWN, None
    synced_dt = _parse_iso(synced_at)
    if synced_dt is None:
        return EPISTEMIC_UNKNOWN, None
    age_s = max(0.0, (now_dt - synced_dt).total_seconds())
    if age_s > float(staleness_threshold_seconds):
        return EPISTEMIC_STALE, round(age_s, 3)
    if has_unreconciled:
        return EPISTEMIC_PROVISIONAL, round(age_s, 3)
    return EPISTEMIC_ATTESTED, round(age_s, 3)


def project_situation_provenance_zero_write(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
    staleness_threshold_seconds: int = 3600,
    candidate_pool: Optional[Sequence[Mapping[str, Any]]] = None,
    budget_token_cap: Optional[int] = None,
    max_items: int = 8,
    external_domains: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Strict zero-write Situation & Sampling Provenance View (F-186, F-187, F-193)."""
    stamp = _now_iso(now_iso)
    now_dt = _parse_iso(stamp)
    assert now_dt is not None

    state_exists, state = _load_state_readonly(day_root)
    rel_state_path = "/".join(STATE_REL_PATH)
    main_rev = int(state.get("revision") or 0)
    main_synced = state.get("updated_at")

    budget = state["budget"]
    has_unknown_res = any(
        r.get("status") == RESERVATION_STATUS_UNKNOWN
        for r in (budget.get("reservations") or {}).values()
        if isinstance(r, Mapping)
    )

    domains: dict[str, dict[str, Any]] = {}
    core_specs = (
        ("autobiography", False),
        ("skills", False),
        ("budget", has_unknown_res),
    )
    for dom_name, provisional_flag in core_specs:
        ep_status, age_s = _compute_epistemic_status(
            source_exists=state_exists,
            synced_at=main_synced,
            now_dt=now_dt,
            staleness_threshold_seconds=staleness_threshold_seconds,
            has_unreconciled=provisional_flag,
        )
        domains[dom_name] = {
            "source_domain": dom_name,
            "revision": main_rev if state_exists else 0,
            "last_synced_at": main_synced if state_exists else None,
            "epistemic_status": ep_status,
            "source_path": rel_state_path,
            "source_exists": bool(state_exists),
            "age_seconds": age_s,
        }

    # Probe companion standard surface files readonly if present on disk
    companion_specs = (
        ("agency_depth", Path("state") / "v02_agency_depth_state.json"),
        ("runtime_migration", Path("phone") / "v02_runtime_migration.json"),
        ("recall_projection", Path("memory") / "v02_recall_projection_state.json"),
    )
    root_path = Path(day_root)
    for dom_name, rel_p in companion_specs:
        full_p = root_path / rel_p
        if full_p.exists() and full_p.is_file():
            try:
                comp_doc = json.loads(full_p.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                comp_doc = {}
            c_rev = int(comp_doc.get("revision") or 0) if isinstance(comp_doc, Mapping) else 0
            c_sync = None
            if isinstance(comp_doc, Mapping):
                for tk in ("updated_at", "observed_at", "evaluated_at"):
                    if isinstance(comp_doc.get(tk), str) and _parse_iso(comp_doc.get(tk)) is not None:
                        c_sync = comp_doc.get(tk)
                        break
            ep_status, age_s = _compute_epistemic_status(
                source_exists=True,
                synced_at=c_sync,
                now_dt=now_dt,
                staleness_threshold_seconds=staleness_threshold_seconds,
            )
            domains[dom_name] = {
                "source_domain": dom_name,
                "revision": c_rev,
                "last_synced_at": c_sync,
                "epistemic_status": ep_status,
                "source_path": rel_p.as_posix(),
                "source_exists": True,
                "age_seconds": age_s,
            }
        else:
            domains[dom_name] = {
                "source_domain": dom_name,
                "revision": 0,
                "last_synced_at": None,
                "epistemic_status": EPISTEMIC_UNMATERIALIZED,
                "source_path": rel_p.as_posix(),
                "source_exists": False,
                "age_seconds": None,
            }

    if external_domains:
        for dom_name, raw_meta in external_domains.items():
            c_sync = raw_meta.get("last_synced_at")
            c_exists = bool(raw_meta.get("source_exists", True))
            ep_status, age_s = _compute_epistemic_status(
                source_exists=c_exists,
                synced_at=c_sync if isinstance(c_sync, str) else None,
                now_dt=now_dt,
                staleness_threshold_seconds=staleness_threshold_seconds,
                has_unreconciled=bool(raw_meta.get("provisional", False)),
            )
            domains[str(dom_name)] = {
                "source_domain": str(dom_name),
                "revision": int(raw_meta.get("revision") or 0),
                "last_synced_at": c_sync,
                "epistemic_status": str(raw_meta.get("epistemic_status") or ep_status),
                "source_path": str(raw_meta.get("source_path") or dom_name),
                "source_exists": c_exists,
                "age_seconds": age_s,
            }

    # Workspace candidate_audit (live pool evaluation or latest persisted audit)
    if candidate_pool is not None:
        effective_tok_cap = budget_token_cap if budget_token_cap is not None else int(budget.get("active_daily_token_cap") or 2000)
        raw_audit = _evaluate_candidate_sampling(
            candidate_pool,
            budget_token_cap=effective_tok_cap,
            max_items=max_items,
            ranking_rule="salience_desc",
        )
        candidate_audit = _filter_audit_for_perspective(raw_audit, has_subject_capability=has_subject_capability)
    else:
        persisted_audits = state["situation_audits"]["workspace_candidate_audits"]
        if persisted_audits:
            candidate_audit = _filter_audit_for_perspective(persisted_audits[-1], has_subject_capability=has_subject_capability)
        else:
            candidate_audit = {
                "ranking_rule": "salience_desc",
                "active_budget_token_cap": int(budget.get("active_daily_token_cap") or 50000),
                "max_items": max_items,
                "used_tokens": 0,
                "sampled_candidates": [],
                "dropped_candidates": [],
                "drop_reason_counts": {},
            }

    return {
        "schema": SCHEMA_VERSION,
        "state_exists": bool(state_exists),
        "perspective": "subject" if has_subject_capability else "partner",
        "observed_at": stamp,
        "domains": domains,
        "candidate_audit": candidate_audit,
        "llm_calls": 0,
        "zero_write": True,
    }


# ---------------------------------------------------------------------------
# Pillar 4: F-191 90% Budget-Drop Survival & Cross-Midnight UNKNOWN Fee Reservation (P30-P31)
# ---------------------------------------------------------------------------


def _day_keys_for_dt(dt: datetime, tz_offset_hours: int) -> tuple[str, str]:
    utc_day = dt.astimezone(timezone.utc).date().isoformat()
    local_tz = timezone(timedelta(hours=int(tz_offset_hours)))
    local_day = dt.astimezone(local_tz).date().isoformat()
    return utc_day, local_day


def _rollover_clock_in_state(budget: dict[str, Any], stamp: str, *, tz_offset_override: Optional[int] = None) -> dict[str, Any]:
    """Update UTC/local calendar day and carry UNKNOWN reservations across midnight without freeing them."""
    if tz_offset_override is not None:
        budget["tz_offset_hours"] = int(tz_offset_override)
    tz_hours = int(budget.get("tz_offset_hours") or 8)
    now_dt = _parse_iso(stamp)
    assert now_dt is not None
    new_utc_day, new_local_day = _day_keys_for_dt(now_dt, tz_hours)

    prev_wall = _parse_iso(budget.get("last_wall_clock_iso"))
    prev_utc_day = budget.get("current_utc_day")
    prev_local_day = budget.get("current_local_day")

    rolled_over = False
    clock_jump = False
    if prev_wall is not None and now_dt < prev_wall:
        # Backward clock jump: NEVER reset settled counters and NEVER release UNKNOWN reservations!
        clock_jump = True
        budget["clock_jump_detected"] = True
    elif prev_utc_day is not None and prev_local_day is not None:
        if new_utc_day > prev_utc_day or new_local_day > prev_local_day:
            rolled_over = True
            budget["prior_day_settled_cost"] = round(
                float(budget.get("prior_day_settled_cost") or 0.0) + float(budget.get("daily_settled_cost") or 0.0),
                6,
            )
            budget["daily_settled_cost"] = 0.0
            budget["daily_settled_tokens"] = 0
            budget["daily_wakes_used"] = 0
            for res in (budget.get("reservations") or {}).values():
                if res.get("status") == RESERVATION_STATUS_UNKNOWN:
                    res["carried_across_midnight"] = True
                    res["carry_count"] = int(res.get("carry_count") or 0) + 1
                    res["last_carried_at"] = stamp
                    res["last_carried_utc_day"] = new_utc_day
                    res["last_carried_local_day"] = new_local_day

    if prev_wall is None or now_dt >= prev_wall:
        budget["last_wall_clock_iso"] = stamp
        budget["current_utc_day"] = new_utc_day
        budget["current_local_day"] = new_local_day

    budget["last_monotonic_seq"] = int(budget.get("last_monotonic_seq") or 0) + 1
    return {
        "rolled_over": rolled_over,
        "clock_jump_detected": clock_jump,
        "utc_day": budget["current_utc_day"],
        "local_day": budget["current_local_day"],
    }


def _active_unknown_reserved(budget: Mapping[str, Any]) -> tuple[float, int, list[str]]:
    cost = 0.0
    tokens = 0
    ids: list[str] = []
    for rid, res in sorted((budget.get("reservations") or {}).items()):
        if isinstance(res, Mapping) and res.get("status") == RESERVATION_STATUS_UNKNOWN:
            cost += float(res.get("upper_bound_cost") or 0.0)
            tokens += int(res.get("upper_bound_tokens") or 0)
            ids.append(str(rid))
    return round(cost, 6), tokens, ids


def _assert_no_fake_excuses(text: str) -> None:
    lowered = text.lower()
    for tok in FORBIDDEN_EXCUSE_TOKENS:
        if tok in lowered:
            raise AutobiographySkillProvenanceError(
                400,
                "FORBIDDEN_FAKE_EXCUSE",
                f"Budget conservation must not fabricate physical/emotional excuse ({tok})",
            )


def configure_budget_and_survival(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Configure baseline budget caps, timezone offset, and survival anchors (checkpoints, commitments, deletion barriers)."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        budget = state["budget"]
        tz_override = payload.get("tz_offset_hours")
        if tz_override is not None:
            tz_override = _coerce_int(tz_override, "tz_offset_hours")
        _rollover_clock_in_state(budget, stamp, tz_offset_override=tz_override)

        if "normal_daily_cost_cap" in payload:
            budget["normal_daily_cost_cap"] = round(
                _coerce_float(payload["normal_daily_cost_cap"], "normal_daily_cost_cap", minimum=0.0),
                6,
            )
        if "normal_daily_token_cap" in payload:
            budget["normal_daily_token_cap"] = _coerce_int(payload["normal_daily_token_cap"], "normal_daily_token_cap", minimum=0)
        if "normal_daily_wake_cap" in payload:
            budget["normal_daily_wake_cap"] = _coerce_int(payload["normal_daily_wake_cap"], "normal_daily_wake_cap", minimum=0)

        scale = int(budget.get("budget_scale_pct") or 100) / 100.0
        budget["active_daily_cost_cap"] = round(float(budget["normal_daily_cost_cap"]) * scale, 6)
        budget["active_daily_token_cap"] = int(round(int(budget["normal_daily_token_cap"]) * scale))
        budget["active_daily_wake_cap"] = max(1 if int(budget["normal_daily_wake_cap"]) > 0 else 0, int(round(int(budget["normal_daily_wake_cap"]) * scale)))

        surv = budget["preserved_survival_state"]
        for key in ("activity_checkpoints", "pending_commitments", "deletion_barriers"):
            if key in payload:
                items = payload[key]
                if not isinstance(items, list):
                    raise AutobiographySkillProvenanceError(400, "INVALID_FIELD", f"{key} must be a list")
                surv[key] = [copy.deepcopy(dict(x)) for x in items]

        return {"budget": copy.deepcopy(budget)}

    return _run_locked_tx(day_root, action_name="configure_budget_and_survival", payload=payload, now_iso=now_iso, mutator=_mutate)


def apply_budget_drop(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Apply a budget reduction (e.g. drop_pct=90 -> 10% scale) while preserving checkpoints, commitments, deletion barriers, and reconciliations."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        budget = state["budget"]
        _rollover_clock_in_state(budget, stamp)

        if "budget_scale_pct" in payload:
            scale_pct = _coerce_int(payload["budget_scale_pct"], "budget_scale_pct", minimum=1)
            if scale_pct > 100:
                raise AutobiographySkillProvenanceError(400, "INVALID_SCALE", "budget_scale_pct must be <= 100")
        else:
            drop_pct = _coerce_int(payload.get("drop_pct", 90), "drop_pct", minimum=0)
            if drop_pct >= 100:
                raise AutobiographySkillProvenanceError(400, "INVALID_DROP_PCT", "drop_pct must be < 100")
            scale_pct = 100 - drop_pct

        reason = _optional_str(payload.get("reason"), max_len=200) or (
            "BUDGET_DROP_CONSERVATION" if scale_pct <= 25 else "BUDGET_ADJUSTED"
        )
        _assert_no_fake_excuses(reason)

        scale = scale_pct / 100.0
        budget["budget_scale_pct"] = scale_pct
        budget["active_daily_cost_cap"] = round(float(budget["normal_daily_cost_cap"]) * scale, 6)
        budget["active_daily_token_cap"] = int(round(int(budget["normal_daily_token_cap"]) * scale))
        budget["active_daily_wake_cap"] = max(1 if int(budget["normal_daily_wake_cap"]) > 0 else 0, int(round(int(budget["normal_daily_wake_cap"]) * scale)))

        if scale_pct <= 25:
            budget["mode"] = BUDGET_MODE_CONSERVATION
            budget["allow_exploratory_cognition"] = False
            budget["allow_decorative_generation"] = False
        else:
            budget["mode"] = BUDGET_MODE_NORMAL
            budget["allow_exploratory_cognition"] = True
            budget["allow_decorative_generation"] = True

        # Survival essentials ALWAYS remain enabled and preserved
        budget["allow_essential_reconciliation"] = True
        budget["allow_commitment_fulfillment"] = True
        budget["allow_deletion_enforcement"] = True
        budget["status_reason"] = reason
        budget["excuse_fabricated"] = False

        return {"budget": copy.deepcopy(budget)}

    return _run_locked_tx(day_root, action_name="apply_budget_drop", payload=payload, now_iso=now_iso, mutator=_mutate)


def reserve_unknown_fee(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Reserve upper-bound fee for an in-flight UNKNOWN outcome operation; blocks if hard cap would be exceeded."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        budget = state["budget"]
        _rollover_clock_in_state(budget, stamp)

        upper_cost = _coerce_float(payload.get("upper_bound_cost"), "upper_bound_cost", minimum=0.0)
        upper_tokens = _coerce_int(payload.get("upper_bound_tokens", 0), "upper_bound_tokens", minimum=0)
        op_ref = _require_str(payload.get("action_ref") or payload.get("operation_key") or _allocate_id(state, "op"), "action_ref", max_len=160)
        res_id = _optional_str(payload.get("reservation_id"), max_len=120) or _allocate_id(state, "res")

        reserved_cost, reserved_tokens, _ = _active_unknown_reserved(budget)
        available_cost = round(float(budget["active_daily_cost_cap"]) - float(budget["daily_settled_cost"]) - reserved_cost, 6)
        available_tokens = int(budget["active_daily_token_cap"]) - int(budget["daily_settled_tokens"]) - reserved_tokens

        if upper_cost > available_cost + 1e-9 or upper_tokens > available_tokens:
            raise AutobiographySkillProvenanceError(
                429,
                "BUDGET_CAP_EXCEEDED_WITH_UNKNOWN_RESERVATION",
                f"Requested upper_bound_cost={upper_cost} exceeds available_cost={available_cost}",
            )

        res_entry = {
            "reservation_id": res_id,
            "action_ref": op_ref,
            "upper_bound_cost": round(upper_cost, 6),
            "upper_bound_tokens": upper_tokens,
            "status": RESERVATION_STATUS_UNKNOWN,
            "created_at": stamp,
            "created_utc_day": budget["current_utc_day"],
            "created_local_day": budget["current_local_day"],
            "carried_across_midnight": False,
            "carry_count": 0,
            "actual_cost": None,
            "actual_tokens": None,
            "reconciled_at": None,
        }
        budget["reservations"][res_id] = res_entry

        surv = budget["preserved_survival_state"]
        surv["unresolved_reconciliations"].append({
            "reconcile_id": _allocate_id(state, "rec"),
            "reservation_id": res_id,
            "action_ref": op_ref,
            "outcome": "UNKNOWN",
            "upper_bound_cost": round(upper_cost, 6),
            "created_at": stamp,
        })
        return {"reservation": copy.deepcopy(res_entry)}

    return _run_locked_tx(day_root, action_name="reserve_unknown_fee", payload=payload, now_iso=now_iso, mutator=_mutate)


def advance_clock_and_rollover_budget(
    day_root: Path | str,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Advance wall clock across UTC/local midnight (or test backward clock jump) and carry UNKNOWN reservations."""

    pl = dict(payload or {})

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        budget = state["budget"]
        tz_override = pl.get("tz_offset_hours")
        if tz_override is not None:
            tz_override = _coerce_int(tz_override, "tz_offset_hours")
        roll_info = _rollover_clock_in_state(budget, stamp, tz_offset_override=tz_override)
        reserved_cost, reserved_tokens, carried_ids = _active_unknown_reserved(budget)
        available_cost = max(0.0, round(float(budget["active_daily_cost_cap"]) - float(budget["daily_settled_cost"]) - reserved_cost, 6))
        return {
            **roll_info,
            "active_unknown_reserved_cost": reserved_cost,
            "active_unknown_reserved_tokens": reserved_tokens,
            "active_unknown_reservation_ids": carried_ids,
            "available_cost_budget": available_cost,
        }

    return _run_locked_tx(day_root, action_name="advance_clock_and_rollover_budget", payload=pl, now_iso=now_iso, mutator=_mutate)


def reconcile_unknown_reservation(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Reconcile an in-flight UNKNOWN reservation once confirmed SETTLED or CONFIRMED_NOT_CHARGED (UNKNOWN stays locked)."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        budget = state["budget"]
        _rollover_clock_in_state(budget, stamp)

        res_id = _require_str(payload.get("reservation_id"), "reservation_id", max_len=120)
        res = budget["reservations"].get(res_id)
        if not isinstance(res, dict):
            raise AutobiographySkillProvenanceError(404, "RESERVATION_NOT_FOUND", f"Unknown reservation_id={res_id}")

        outcome = _require_str(payload.get("reconciled_outcome"), "reconciled_outcome", max_len=40)
        if outcome not in (RESERVATION_STATUS_SETTLED, RESERVATION_STATUS_NOT_CHARGED, "UNKNOWN"):
            raise AutobiographySkillProvenanceError(400, "INVALID_RECONCILE_OUTCOME", f"Invalid outcome: {outcome!r}")

        if outcome == "UNKNOWN":
            # Never coerce UNKNOWN to FAILED or 0 cost!
            return {"reservation": copy.deepcopy(res), "reconciled": False}

        actual_cost = 0.0 if outcome == RESERVATION_STATUS_NOT_CHARGED else _coerce_float(
            payload.get("actual_cost", 0.0), "actual_cost", minimum=0.0
        )
        actual_tokens = 0 if outcome == RESERVATION_STATUS_NOT_CHARGED else _coerce_int(
            payload.get("actual_tokens", 0), "actual_tokens", minimum=0
        )

        res["status"] = outcome
        res["actual_cost"] = round(actual_cost, 6)
        res["actual_tokens"] = actual_tokens
        res["reconciled_at"] = stamp

        # If incurred on the current local/UTC day, add to daily_settled_cost; otherwise record in prior_day_settled_cost
        same_day = (
            res.get("created_utc_day") == budget.get("current_utc_day")
            and res.get("created_local_day") == budget.get("current_local_day")
        )
        if same_day:
            budget["daily_settled_cost"] = round(float(budget["daily_settled_cost"]) + actual_cost, 6)
            budget["daily_settled_tokens"] = int(budget["daily_settled_tokens"]) + actual_tokens
        else:
            budget["prior_day_settled_cost"] = round(float(budget["prior_day_settled_cost"]) + actual_cost, 6)

        budget["reconciled_ledger"].append({
            "reservation_id": res_id,
            "outcome": outcome,
            "upper_bound_cost": res["upper_bound_cost"],
            "actual_cost": round(actual_cost, 6),
            "reconciled_at": stamp,
        })

        surv = budget["preserved_survival_state"]
        surv["unresolved_reconciliations"] = [
            item for item in surv["unresolved_reconciliations"] if item.get("reservation_id") != res_id
        ]
        return {"reservation": copy.deepcopy(res), "reconciled": True}

    return _run_locked_tx(day_root, action_name="reconcile_unknown_reservation", payload=payload, now_iso=now_iso, mutator=_mutate)


def record_metering_event(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Record P30 3-layer metering (llm_call / background_job / admitted_product / unadmitted_sample)."""

    def _mutate(state: dict[str, Any], stamp: str) -> dict[str, Any]:
        layer = _require_str(payload.get("layer"), "layer", max_len=40)
        count = _coerce_int(payload.get("count", 1), "count", minimum=1)
        layers = state["budget"]["metering_layers"]
        key_map = {
            "llm_call": "llm_calls",
            "background_job": "background_jobs",
            "admitted_product": "admitted_products",
            "unadmitted_sample": "unadmitted_samples",
        }
        if layer not in key_map:
            raise AutobiographySkillProvenanceError(400, "INVALID_METERING_LAYER", f"Invalid layer: {layer!r}")
        target_key = key_map[layer]
        layers[target_key] = int(layers.get(target_key) or 0) + count
        return {"metering_layers": copy.deepcopy(layers)}

    return _run_locked_tx(day_root, action_name="record_metering_event", payload=payload, now_iso=now_iso, mutator=_mutate)


def project_budget_survival(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Zero-write projection of F-191 budget conservation and cross-midnight UNKNOWN fee reservations."""
    stamp = _now_iso(now_iso)
    state_exists, state = _load_state_readonly(day_root)
    budget = state["budget"]
    reserved_cost, reserved_tokens, reserved_ids = _active_unknown_reserved(budget)
    available_cost = max(0.0, round(float(budget["active_daily_cost_cap"]) - float(budget["daily_settled_cost"]) - reserved_cost, 6))
    available_tokens = max(0, int(budget["active_daily_token_cap"]) - int(budget["daily_settled_tokens"]) - reserved_tokens)
    carried_ids = [
        rid
        for rid, res in sorted((budget.get("reservations") or {}).items())
        if res.get("status") == RESERVATION_STATUS_UNKNOWN and res.get("carried_across_midnight") is True
    ]

    return {
        "schema": SCHEMA_VERSION,
        "state_exists": bool(state_exists),
        "observed_at": stamp,
        "mode": budget["mode"],
        "budget_scale_pct": budget["budget_scale_pct"],
        "status_reason": budget["status_reason"],
        "excuse_fabricated": bool(budget.get("excuse_fabricated", False)),
        "active_daily_cost_cap": budget["active_daily_cost_cap"],
        "active_daily_token_cap": budget["active_daily_token_cap"],
        "daily_settled_cost": budget["daily_settled_cost"],
        "active_unknown_reserved_cost": reserved_cost,
        "active_unknown_reserved_tokens": reserved_tokens,
        "active_unknown_reservation_ids": reserved_ids,
        "carried_across_midnight_ids": carried_ids,
        "available_cost_budget": available_cost,
        "available_token_budget": available_tokens,
        "clock_jump_detected": bool(budget.get("clock_jump_detected", False)),
        "allow_exploratory_cognition": bool(budget.get("allow_exploratory_cognition", True)),
        "allow_decorative_generation": bool(budget.get("allow_decorative_generation", True)),
        "allow_essential_reconciliation": bool(budget.get("allow_essential_reconciliation", True)),
        "allow_commitment_fulfillment": bool(budget.get("allow_commitment_fulfillment", True)),
        "allow_deletion_enforcement": bool(budget.get("allow_deletion_enforcement", True)),
        "preserved_survival_state": copy.deepcopy(budget["preserved_survival_state"]),
        "metering_layers": copy.deepcopy(budget["metering_layers"]),
        "llm_calls": 0,
        "zero_write": True,
    }


def project_autobiography_skill_provenance_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
    staleness_threshold_seconds: int = 3600,
) -> dict[str, Any]:
    """Combined zero-write read projection across all four pillars."""
    stamp = _now_iso(now_iso)
    return {
        "schema": SCHEMA_VERSION,
        "perspective": "subject" if has_subject_capability else "partner",
        "observed_at": stamp,
        "p12_autobiography": project_p12_autobiographical_periods(
            day_root,
            has_subject_capability=has_subject_capability,
            now_iso=stamp,
        ),
        "skill_provenance": project_skill_provenance(day_root, now_iso=stamp),
        "situation_provenance": project_situation_provenance_zero_write(
            day_root,
            has_subject_capability=has_subject_capability,
            now_iso=stamp,
            staleness_threshold_seconds=staleness_threshold_seconds,
        ),
        "budget_survival": project_budget_survival(day_root, now_iso=stamp),
        "llm_calls": 0,
        "zero_write": True,
    }


__all__ = [
    "AutobiographySkillProvenanceError",
    "BUDGET_MODE_CONSERVATION",
    "BUDGET_MODE_NORMAL",
    "CLAIM_LANES",
    "CLAIM_POLARITIES",
    "DEGRADATION_STREAK_THRESHOLD",
    "EPISTEMIC_ATTESTED",
    "EPISTEMIC_PROVISIONAL",
    "EPISTEMIC_STALE",
    "EPISTEMIC_UNKNOWN",
    "EPISTEMIC_UNMATERIALIZED",
    "EPISODE_OUTCOMES",
    "METHOD_PATCH_RETEST_STATUSES",
    "RESERVATION_STATUS_NOT_CHARGED",
    "RESERVATION_STATUS_SETTLED",
    "RESERVATION_STATUS_UNKNOWN",
    "SCHEMA_VERSION",
    "SKILL_ATTRIBUTIONS",
    "SKILL_ATTRIBUTION_BORROWED_TOOL",
    "SKILL_ATTRIBUTION_LABELS_CN",
    "SKILL_ATTRIBUTION_MODEL_NATIVE",
    "SKILL_ATTRIBUTION_PRACTICED_MASTERY",
    "SKILL_ATTRIBUTION_RETRIEVED_GUIDE",
    "SKILL_STATUSES",
    "SKILL_STATUS_CANDIDATE",
    "SKILL_STATUS_DEGRADED",
    "SKILL_STATUS_LABELS_CN",
    "SKILL_STATUS_RETIRED",
    "SKILL_STATUS_SUSPENDED",
    "SKILL_STATUS_TRIAL",
    "SKILL_STATUS_UNVALIDATED_AFTER_MIGRATION",
    "SKILL_STATUS_VALIDATED",
    "SUSPENSION_STREAK_THRESHOLD",
    "TRANSIENT_NETWORK_ERROR_KINDS",
    "advance_clock_and_rollover_budget",
    "append_narrative_snapshot",
    "apply_budget_drop",
    "build_default_state",
    "bump_carrier_or_tool_version",
    "compile_period_summary",
    "configure_budget_and_survival",
    "project_autobiography_skill_provenance_view",
    "project_budget_survival",
    "project_p12_autobiographical_periods",
    "project_personal_skill_book_zero_write",
    "project_situation_provenance_zero_write",
    "project_skill_provenance",
    "reconcile_unknown_reservation",
    "record_autobiographical_episode",
    "record_metering_event",
    "record_self_claim",
    "record_skill_execution_outcome",
    "record_targeted_method_patch",
    "record_workspace_candidate_audit",
    "register_skill_release",
    "register_standard_revision",
    "request_skill_recheck",
    "reserve_unknown_fee",
    "retire_skill_release",
    "reverify_migrated_skill",
    "revoke_skill_source_material",
]

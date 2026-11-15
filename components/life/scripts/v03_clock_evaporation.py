#!/usr/bin/env python3
"""v3.0 (Ch. 06-08, 20-22; ADR-037 / ADR-038 / ADR-042):
Three-Clock Deterministic World Engine, Four-Tier Accountability & Natural Evaporation,
Subject Meaningless-Idle Rights, and 0.2 Committed-World Slice Synchronizer.

Core invariants:
1. ``current_world = committed_events + deterministic_projection(rulepack, snapshot, now)``
   Offline/quiet gaps advance declared slow variables deterministically with ``llm_calls == 0``:
   - damp outerwear drying over 6h (``21600s``),
   - desk drink / tea cooling over 30m and table dishes over 20m/60m,
   - 72h missing-partner notes naturally fading with zero absence debt,
   - cross-day ambient soundscape & date switch,
   - ``EVAPORABLE`` fleeting thoughts expiring silently on TTL.
2. Iron Law 1: Discrete unresolved ``UNKNOWN`` actions are NEVER guessed into
   ``APPLIED`` or ``FAILED`` by elapsed time.
3. Iron Law 2: ``cognitive_backfill_forbidden == True`` — downtime never fabricates
   lived cognitive episodes ("read three books while server was off").
4. Four-Tier Accountability Partition (``EVAPORABLE``, ``LOCAL_PERSISTENT``,
   ``STRONG_ACCOUNTABILITY``, ``MUTUAL_CONTROL``):
   - ``EVAPORABLE`` expires with zero tombstones, zero audit backlog, zero forced reflection.
   - Accountable domains (wallet/money, object placement/containment, outbound delivery,
     mutual consent/permissions) are strictly rejected from ``EVAPORABLE``.
5. ``read_v03_clock_view`` is 100% pure read (zero disk write).
6. v3.0 Ch.02 Slow-Variable Incubation Kernel (``slow_ledger`` section, ADR-038):
   numeric ecological slow variables (``value_milli`` 0..1000 fixed quantum,
   per-hour deterministic drift, declared volatility bandwidth, closed six-phase
   lifecycle ``dormant/incubating/ripe/manifesting/settled/decayed``) incubate by
   elapsed time alone; crossing the threshold flips ``ripe`` and enqueues a
   one-time Threshold Manifestation Contract, dispatched under the steady-state
   guards (category cooldown families 120d/90d, monthly drama-entropy budget <= 2
   major events, forced 21d calm window) through the append-only causal edge
   registry into a one-time physical state write on the v04 neighborhood owner.
   Zero RNG (seed-derived initial values via sha256), zero LLM calls, "两周没路过
   照样发生" holds because only ``elapsed`` drives the pipeline.  The three legacy
   physical micro slow variables (damp_outerwear / desk_drink / missing_note) and
   the embodied-NPC templates keep running untouched next to this layer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_LIFE_SCRIPTS = Path(__file__).resolve().parent
_COMPONENTS_ROOT = _LIFE_SCRIPTS.parents[1]
for _entry in (str(_LIFE_SCRIPTS), str(_COMPONENTS_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import everyday_desire_lifecycle as desire_lc  # noqa: E402
import life_dwell  # noqa: E402

V03_CLOCK_VIEW_SCHEMA = "phone.v03.clock.v1"
V03_CLOCK_STATE_SCHEMA = "life.v03.clock_evaporation.v1"
V03_CLOCK_FILENAME = "v03_clock_state.json"
RULEPACK_ID = "v03.deterministic_slow_vars.v1"
WORLD_FORMULA = "current_world = committed_events + deterministic_projection(rulepack, snapshot, now)"

OUTERWEAR_DRYING_SECONDS = 6 * 3600
DRINK_TEPID_SECONDS = 15 * 60
DRINK_COOLING_SECONDS = 30 * 60
DISH_WARM_SECONDS = 20 * 60
DISH_COOLED_SECONDS = 60 * 60
MISSING_NOTE_FADE_SECONDS = 72 * 3600
DEFAULT_EVAPORABLE_TTL_SECONDS = 30 * 60
MIN_EVAPORABLE_TTL_SECONDS = 1
MAX_EVAPORABLE_TTL_SECONDS = 7 * 86400

MAX_EVAPORABLE_POOL = 64
MAX_SLOW_VARS = 64
MAX_UNKNOWN_ACTIONS = 64
MAX_GAP_RECEIPTS = 32
MAX_OP_LEDGER = 128
MAX_TEXT_LEN = 280
MAX_ID_LEN = 128

# ---------------------------------------------------------------------------
# v3.0 Ch.02 Slow-Variable Incubation Kernel (Variable Ledger / Causal Edge
# Registry / Threshold Manifestation Contract; ADR-038 zero-RNG discipline).
# Added 2026-11-14 as a *parallel* layer next to the three physical micro
# slow variables (damp_outerwear / desk_drink / missing_note), which stay as-is.
# ---------------------------------------------------------------------------
ECO_PHASES = (
    "dormant",
    "incubating",
    "ripe",
    "manifesting",
    "settled",
    "decayed",
)
ACTIVE_ECO_PHASES = frozenset({"dormant", "incubating", "ripe", "manifesting"})
MANIFESTATION_STATUSES = ("queued", "dispatched", "manifested", "dropped")
COOLDOWN_FAMILIES = ("commercial_vacancy", "interpersonal_move")
#: Ch.02 §四.2 category cooldown families: commercial change 120d, interpersonal 90d.
COOLDOWN_FAMILY_SECONDS = {
    "commercial_vacancy": 120 * 86400,
    "interpersonal_move": 90 * 86400,
}
CAUSAL_EDGE_GRADES = ("rule", "witnessed", "inferred")
PERCEPTION_TIERS = ("distant", "mid", "near", "interactive")
#: Ch.02 §四.2 drama-entropy budget: neighborhood-level major manifestations <= 2 / month.
ENTROPY_BUDGET_MONTHLY_MAJOR = 2
#: Ch.02 §四.2 forced calm window ("2–4 周低吸收窗") — midpoint 21 days, single value for determinism.
CALM_PERIOD_DAYS = 21
CALM_PERIOD_SECONDS = CALM_PERIOD_DAYS * 86400
#: Ch.02 §四.1 aftermath cold-folding: 90d after settling, the residue collapses to one line.
RESIDUE_FOLD_SECONDS = 90 * 86400
RIPE_THRESHOLD_MILLI_DEFAULT = 750
ECO_VALUE_MILLI_MAX = 1000
ECO_DRIFT_MILLI_PER_HOUR_LIMIT = 1000
ECO_SEED_LATENT_MILLI_SPAN = 400
DEFAULT_WORLD_SEED = 929711
MAX_ECO_VARIABLES = 64
MAX_ACTIVE_ECO_VARIABLES = 12
MAX_CAUSAL_EDGES = 256
MAX_MANIFESTATION_QUEUE = 16
MAX_MANIFESTATION_LEDGER = 128
MAX_EDGE_SOURCE_REFS = 8
MAX_ECO_CARRIERS = 4

# --- Ch.02 §1.2 causal-edge -> dynamics coupling (maturation wave 2026-11-14) ---
# Design semantics (02 章 §1.2 三级写权防线):
#   rule      -> structural cause: may set/narrow the target variable's drift band
#                and volatility band ("商圈地铁施工等外部工程通过 rule 边压低人流").
#   witnessed -> first-hand transmission: may inject ONE bounded one-shot drift
#                pulse (amplitude capped, audited in the edge effect ledger), and
#                accumulated witnessed contact narrows volatility one closed tier
#                (§1.1 "volatility ... 噪声带宽（千代接触时收窄）").
#   inferred  -> Chiyono's subjective inference: NEVER enters dynamics (§1.2
#                "只进 S03/S05，绝不允许回写 S01 世界账本"); projection only.
#: one-shot witnessed drift pulse cap (milli value units, |pulse| <= 100 = 0.1 scale)
WITNESSED_PULSE_MILLI_LIMIT = 100
#: witnessed edges per volatility tier narrowing ("接触越多、世界越可预测")
WITNESS_NARROW_THRESHOLD = 2
#: closed volatility tier ladder (milli units); narrowing steps strictly down this ladder
VOLATILITY_TIERS = (0, 8, 16, 32, 64, 128, 256, 512)
#: append-only audit trail of every dynamics effect a causal edge produced
MAX_EDGE_EFFECT_LEDGER = 256
#: manifestation hold reason when the bound POI is not open at dispatch time
MANIFESTATION_HOLD_POI_CLOSED = "POI_CLOSED"

ACCOUNTABILITY_TIERS = (
    "EVAPORABLE",
    "LOCAL_PERSISTENT",
    "STRONG_ACCOUNTABILITY",
    "MUTUAL_CONTROL",
)

EVAPORABLE_ALLOWED_DOMAINS = frozenset({
    "fleeting_thought",
    "passing_sensation",
    "ambient_glance",
    "idle_mood_ripple",
    "ordinary_linger",
})

FORBIDDEN_EVAPORABLE_DOMAINS = frozenset({
    "wallet",
    "money",
    "payment",
    "economy",
    "purchase",
    "object_placement",
    "containment",
    "inventory",
    "ownership",
    "world_object",
    "outbound_delivery",
    "external_effect",
    "share_delivery",
    "mutual_consent",
    "partner_permission",
    "intimacy_consent",
    "intimacy_boundary",
    "shared_grant",
    "copresence_consent",
})

FORBIDDEN_EVAPORABLE_PAYLOAD_KEYS = frozenset({
    "amount_jpy",
    "price_jpy",
    "wallet_balance",
    "wallet_delta",
    "payment_id",
    "object_id",
    "item_instance_id",
    "parent_object_id",
    "parent_container_id",
    "placement",
    "hand_slot",
    "consent_id",
    "consent_grant_id",
    "mutual_permission",
    "partner_consent",
    "adult_gate",
    "grant_id",
})

MEANINGLESS_IDLE_KINDS: dict[str, dict[str, str]] = {
    "daydream_window": {
        "label": "窗边发呆",
        "description": "靠在窗边看光线或云影发呆，不需要产出任何结论。",
    },
    "aimless_page_flip": {
        "label": "瞎翻书页不记笔记",
        "description": "随手翻几页书又合上，不推进考核也不强写读书心得。",
    },
    "halfway_abandon_tidy": {
        "label": "收拾一半歇着",
        "description": "东西理到一半不想弄了就停手歇着，不触发半途而废惩罚。",
    },
    "listen_rain_blankly": {
        "label": "听雨放空",
        "description": "安静听外面风声雨声放空，零成长打分也零反思任务。",
    },
}

AMBIENT_SOUNDSCAPES_BY_BAND: dict[str, tuple[str, ...]] = {
    "dawn": ("coastal_dawn_breeze", "early_sparrow_eaves"),
    "morning": ("morning_street_distant", "sunlit_window_dust"),
    "afternoon": ("quiet_afternoon_leaves", "harbor_distant_tide"),
    "evening": ("dusk_kettle_hum", "lamplight_evening_calm"),
    "late_night": ("night_clock_soft_silence", "midnight_window_draft"),
}

SLOW_VAR_KINDS = frozenset({"damp_outerwear", "desk_drink", "missing_note"})
ACTOR_SIDES = frozenset({"subject", "partner", "system", "operator"})

GLOBAL_FORBIDDEN_FIELDS = frozenset({
    "llm_prompt",
    "backfill_memories",
    "fabricated_books_read",
    "growth_score",
    "productivity_score",
    "affection_score",
    "absence_penalty",
})

ACTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "advance_projection": frozenset({
        "action",
        "sync_v02",
        "expected_revision",
        "operation_key",
    }),
    "record_evaporable": frozenset({
        "action",
        "thought_id",
        "domain_kind",
        "accountability_tier",
        "text",
        "ttl_seconds",
        "expected_revision",
        "operation_key",
    }),
    "start_meaningless_idle": frozenset({
        "action",
        "idle_kind",
        "quiet_note",
        "expected_revision",
        "operation_key",
    }),
    "end_meaningless_idle": frozenset({
        "action",
        "abandoned_halfway",
        "let_evaporate",
        "ttl_seconds",
        "expected_revision",
        "operation_key",
    }),
    "declare_slow_variable": frozenset({
        "action",
        "var_id",
        "var_kind",
        "label",
        "initial_state",
        "started_at",
        "expected_revision",
        "operation_key",
    }),
    # v3.0 Ch.02 slow-variable incubation kernel (numeric ecological variables)
    "declare_eco_variable": frozenset({
        "action",
        "var_id",
        "var_name",
        "var_class",
        "label",
        "carriers",
        "drift_milli_per_hour",
        "volatility_milli",
        "manifest_threshold_milli",
        "cooldown_family",
        "entropy_major",
        "perception_tier",
        "manifest_scene",
        "binds",
        "world_seed",
        "started_at",
        "expected_revision",
        "operation_key",
    }),
    "add_causal_edge": frozenset({
        "action",
        "edge_id",
        "from_var",
        "to_var",
        "grade",
        "lag_seconds",
        "lag_max_seconds",
        "coupling",
        "condition",
        "source_refs",
        # Ch.02 §1.2 dynamics coupling: rule edges may carry drift/volatility
        # bands; witnessed edges may carry one bounded drift pulse; inferred
        # edges must never carry any of these (inference never rewrites the world).
        "pulse_milli",
        "drift_band_min_milli_per_hour",
        "drift_band_max_milli_per_hour",
        "volatility_band_min_milli",
        "volatility_band_max_milli",
        "expected_revision",
        "operation_key",
    }),
    "drop_manifestation": frozenset({
        "action",
        "manifestation_id",
        "drop_reason",
        "expected_revision",
        "operation_key",
    }),
    "register_unknown_action": frozenset({
        "action",
        "action_id",
        "domain",
        "summary",
        "submitted_at",
        "expected_revision",
        "operation_key",
    }),
    "resolve_unknown_action": frozenset({
        "action",
        "action_id",
        "resolved_outcome",
        "receipt_ref",
        "expected_revision",
        "operation_key",
    }),
    "sync_v02_slices": frozenset({
        "action",
        "refresh_reunion_baseline",
        "expected_revision",
        "operation_key",
    }),
    # Ch. 03 / 08 / 21: Bounded dwell, >15m HOST_GAP (ERR-10), subjective time & everyday glitches
    "start_dwell": frozenset({
        "action",
        "declared_seconds",
        "place_node",
        "label",
        "replace_active",
        "expected_revision",
        "operation_key",
    }),
    "end_dwell": frozenset({
        "action",
        "reason",
        "expected_revision",
        "operation_key",
    }),
    "record_host_gap": frozenset({
        "action",
        "from_iso",
        "to_iso",
        "kind",
        "expected_revision",
        "operation_key",
    }),
    "update_subjective_time": frozenset({
        "action",
        "focused_work_minutes",
        "meaningful_event_count",
        "idle_or_waiting_minutes",
        "pillars",
        "future_hooks",
        "expected_revision",
        "operation_key",
    }),
    "trigger_everyday_glitch": frozenset({
        "action",
        "glitch_kind",
        "item_id",
        "expected_spot",
        "actual_spot",
        "resolved",
        "search_seconds",
        "surface_id",
        "wiped_clean",
        "garment_id",
        "garment_label",
        "target_ref",
        "intended_title",
        "actual_title",
        "object_id",
        "completed_units",
        "remaining_units",
        "expected_revision",
        "operation_key",
    }),
    # Ch. 35: Everyday desires (turn_only natural fade-out & 6-concern conflict arbitration)
    "emerge_concern": frozenset({
        "action",
        "concern_ref",
        "domain",
        "origin_kind",
        "focus_ref",
        "origin_refs",
        "expression",
        "urgency",
        "persistence",
        "cost_scale",
        "ttl_seconds",
        "estimated_cost_jpy",
        "expected_revision",
        "operation_key",
    }),
    "advance_desire_turn": frozenset({
        "action",
        "expected_revision",
        "operation_key",
    }),
    "leave_desire_scene": frozenset({
        "action",
        "new_scene_id",
        "expected_revision",
        "operation_key",
    }),
    "satisfy_concern": frozenset({
        "action",
        "concern_ref",
        "origin_ref",
        "expected_revision",
        "operation_key",
    }),
    "drop_concern": frozenset({
        "action",
        "concern_ref",
        "origin_ref",
        "expected_revision",
        "operation_key",
    }),
    "arbitrate_concerns": frozenset({
        "action",
        "foreground_activity",
        "body_context",
        "budget_context",
        "channel_visibility",
        "adult_gate_open",
        "expected_revision",
        "operation_key",
    }),
}

SUBJECT_ONLY_ACTIONS = frozenset({
    "record_evaporable",
    "start_meaningless_idle",
    "end_meaningless_idle",
    "start_dwell",
    "end_dwell",
    "update_subjective_time",
    "trigger_everyday_glitch",
    "emerge_concern",
    "satisfy_concern",
    "drop_concern",
    "arbitrate_concerns",
})

_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class V03ClockError(ValueError):
    status_code = 400
    status_label = "rejected"

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(detail or reason_code)
        self.reason_code = reason_code
        self.detail = detail or reason_code


class V03ClockValidationError(V03ClockError):
    status_code = 400
    status_label = "rejected"


class V03ClockPermissionError(V03ClockError):
    status_code = 403
    status_label = "rejected"


class V03ClockNotFoundError(V03ClockError):
    status_code = 404
    status_label = "not_found"


class V03ClockConflictError(V03ClockError):
    status_code = 409
    status_label = "rejected"


class V03ClockUnavailableError(V03ClockError):
    status_code = 503
    status_label = "unavailable"


def _raise_from_subengine_error(exc: Exception) -> None:
    status = int(getattr(exc, "http_status", 400))
    code = str(getattr(exc, "reason_code", "CLOCK_SUBENGINE_ERROR"))
    msg = str(getattr(exc, "message", str(exc)))
    if status == 403:
        raise V03ClockPermissionError(code, msg) from exc
    if status == 404:
        raise V03ClockNotFoundError(code, msg) from exc
    if status == 409:
        raise V03ClockConflictError(code, msg) from exc
    if status >= 500:
        raise V03ClockUnavailableError(code, msg) from exc
    raise V03ClockValidationError(code, msg) from exc


# ---------------------------------------------------------------------------
# Pure deterministic helpers
# ---------------------------------------------------------------------------


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _parse_iso(value: Any, *, reason_code: str = "TIMESTAMP_INVALID") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise V03ClockValidationError(reason_code)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise V03ClockValidationError(reason_code) from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise V03ClockValidationError(reason_code)
    return dt


def _resolve_now(now_iso: Optional[str]) -> tuple[datetime, str, str]:
    if now_iso is None:
        dt = datetime.now(timezone.utc).replace(microsecond=0)
        return dt, dt.isoformat(timespec="seconds"), "system_utc"
    dt = _parse_iso(now_iso, reason_code="NOW_ISO_INVALID")
    return dt, dt.isoformat(timespec="seconds"), "injected"


def _clean_text(
    value: Any,
    *,
    min_len: int = 1,
    max_len: int = MAX_TEXT_LEN,
    reason_code: str = "TEXT_INVALID",
) -> str:
    if not isinstance(value, str):
        raise V03ClockValidationError(reason_code)
    if "\x00" in value or "\r" in value:
        raise V03ClockValidationError(reason_code)
    cleaned = value.strip()
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise V03ClockValidationError(reason_code)
    return cleaned


def _optional_text(
    value: Any,
    *,
    max_len: int = MAX_TEXT_LEN,
    reason_code: str = "TEXT_INVALID",
) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _clean_text(value, min_len=1, max_len=max_len, reason_code=reason_code)


def _clean_id(value: Any, *, reason_code: str = "ID_INVALID") -> str:
    if not isinstance(value, str):
        raise V03ClockValidationError(reason_code)
    text = value.strip()
    if not text or len(text) > MAX_ID_LEN or not _ID_RE.match(text):
        raise V03ClockValidationError(reason_code)
    return text


def _strict_bool(value: Any, *, reason_code: str = "BOOLEAN_INVALID") -> bool:
    if not isinstance(value, bool):
        raise V03ClockValidationError(reason_code)
    return value


LOCAL_BUSINESS_TZ = timezone(timedelta(hours=8))


def _to_local_business(dt: datetime) -> datetime:
    return dt.astimezone(LOCAL_BUSINESS_TZ)


def _derive_ambient_soundscape(dt: datetime, previous_date: Optional[str]) -> dict[str, Any]:
    local_dt = _to_local_business(dt)
    local_date = local_dt.date().isoformat()
    hour = local_dt.hour
    if 5 <= hour < 8:
        band = "dawn"
    elif 8 <= hour < 12:
        band = "morning"
    elif 12 <= hour < 17:
        band = "afternoon"
    elif 17 <= hour < 22:
        band = "evening"
    else:
        band = "late_night"
    candidates = AMBIENT_SOUNDSCAPES_BY_BAND[band]
    idx = int(hashlib.sha256(f"{local_date}:{band}".encode("utf-8")).hexdigest()[:8], 16) % len(candidates)
    date_switched = bool(previous_date is not None and previous_date != local_date)
    return {
        "local_date": local_date,
        "previous_date": previous_date,
        "date_switched": date_switched,
        "time_band": band,
        "soundscape_code": candidates[idx],
        "window_rotation_date": local_date,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# Four-Tier Accountability Validator
# ---------------------------------------------------------------------------


def classify_and_validate_accountability(
    *,
    tier: str,
    domain_kind: str,
    payload: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Enforce the 4-tier accountability boundary (Ch. 20-22, ADR-038, ADR-042).

     Wallet/money, object placement/containment, outbound deliveries, and mutual
     consent/permissions MUST NEVER enter ``EVAPORABLE``.
    """
    clean_tier = str(tier or "").strip().upper()
    if clean_tier not in ACCOUNTABILITY_TIERS:
        raise V03ClockValidationError("ACCOUNTABILITY_TIER_INVALID")
    clean_domain = str(domain_kind or "").strip().lower()
    if not clean_domain:
        raise V03ClockValidationError("DOMAIN_KIND_REQUIRED")

    body = payload if isinstance(payload, Mapping) else {}
    forbidden_keys_present = sorted(set(body.keys()) & FORBIDDEN_EVAPORABLE_PAYLOAD_KEYS)

    if clean_tier == "EVAPORABLE":
        if clean_domain in FORBIDDEN_EVAPORABLE_DOMAINS or forbidden_keys_present:
            raise V03ClockConflictError("EVAPORABLE_FORBIDDEN_FOR_ACCOUNTABLE_STATE")
        for token in ("wallet", "money", "payment", "consent", "permission", "placement", "container"):
            if token in clean_domain:
                raise V03ClockConflictError("EVAPORABLE_FORBIDDEN_FOR_ACCOUNTABLE_STATE")
        if clean_domain not in EVAPORABLE_ALLOWED_DOMAINS:
            raise V03ClockConflictError("EVAPORABLE_DOMAIN_NOT_PERMITTED")
        return {
            "tier": "EVAPORABLE",
            "domain_kind": clean_domain,
            "requires_receipt": False,
            "requires_conservation": False,
            "requires_mutual_consent": False,
            "no_tombstone": True,
        }

    if clean_tier == "LOCAL_PERSISTENT":
        return {
            "tier": "LOCAL_PERSISTENT",
            "domain_kind": clean_domain,
            "requires_receipt": False,
            "requires_conservation": False,
            "requires_mutual_consent": False,
            "no_tombstone": False,
        }

    if clean_tier == "STRONG_ACCOUNTABILITY":
        return {
            "tier": "STRONG_ACCOUNTABILITY",
            "domain_kind": clean_domain,
            "requires_receipt": True,
            "requires_conservation": True,
            "requires_mutual_consent": False,
            "no_tombstone": False,
        }

    return {
        "tier": "MUTUAL_CONTROL",
        "domain_kind": clean_domain,
        "requires_receipt": True,
        "requires_conservation": False,
        "requires_mutual_consent": True,
        "no_tombstone": False,
    }


# ---------------------------------------------------------------------------
# State storage (strict zero-write on GET)
# ---------------------------------------------------------------------------


def v03_clock_state_path(day_root: Path | str) -> Path:
    return Path(day_root) / "phone" / V03_CLOCK_FILENAME


def default_slow_ledger() -> dict[str, Any]:
    """The Ch.02 three-layer slow ledger: values + slopes + evidence pointers, no history stream."""
    return {
        "world_seed": None,
        "eco_variables": [],
        "causal_edges": [],
        "edge_effect_ledger": [],
        "manifestation_queue": [],
        "manifestation_ledger": [],
        "manifestation_seq": 0,
    }


def default_v03_clock_state() -> dict[str, Any]:
    return {
        "schema_version": V03_CLOCK_STATE_SCHEMA,
        "revision": 0,
        "updated_at": None,
        "world_clock": {
            "last_committed_at": None,
            "ambient_date": None,
            "gap_receipts": [],
            "slow_variables": [],
            "pending_unknown_actions": [],
        },
        "cognitive_clock": {
            "causal_ticks": 0,
            "last_cognitive_at": None,
            "cognitive_backfill_forbidden": True,
            "active_meaningless_idle": None,
        },
        "evaporable_pool": [],
        "slow_ledger": default_slow_ledger(),
        "v02_sync": {
            "last_synced_at": None,
            "synced_visit_keys": [],
            "synced_route_ids": [],
            "reunion_baseline_seeded": False,
            "room_furnishing_digest": None,
            "delivered_share_count": 0,
            "album_count": 0,
        },
        "op_ledger": {},
    }


def _validate_clock_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise V03ClockUnavailableError("CLOCK_STATE_INVALID")
    if raw.get("schema_version") != V03_CLOCK_STATE_SCHEMA:
        raise V03ClockUnavailableError("CLOCK_STATE_SCHEMA_MISMATCH")
    rev = raw.get("revision")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise V03ClockUnavailableError("CLOCK_STATE_REVISION_INVALID")

    base = default_v03_clock_state()
    base["revision"] = int(rev)
    if raw.get("updated_at") is not None:
        _parse_iso(raw.get("updated_at"), reason_code="CLOCK_STATE_TIME_INVALID")
        base["updated_at"] = str(raw["updated_at"])

    wc = raw.get("world_clock")
    if isinstance(wc, Mapping):
        if wc.get("last_committed_at") is not None:
            _parse_iso(wc.get("last_committed_at"), reason_code="CLOCK_STATE_TIME_INVALID")
            base["world_clock"]["last_committed_at"] = str(wc["last_committed_at"])
        if wc.get("ambient_date") is not None:
            base["world_clock"]["ambient_date"] = str(wc["ambient_date"])[:16]
        if isinstance(wc.get("gap_receipts"), list):
            base["world_clock"]["gap_receipts"] = [
                dict(x) for x in wc["gap_receipts"][-MAX_GAP_RECEIPTS:] if isinstance(x, Mapping)
            ]
        if isinstance(wc.get("slow_variables"), list):
            base["world_clock"]["slow_variables"] = [
                dict(x) for x in wc["slow_variables"][:MAX_SLOW_VARS] if isinstance(x, Mapping)
            ]
        if isinstance(wc.get("pending_unknown_actions"), list):
            base["world_clock"]["pending_unknown_actions"] = [
                dict(x) for x in wc["pending_unknown_actions"][:MAX_UNKNOWN_ACTIONS]
                if isinstance(x, Mapping)
            ]

    cc = raw.get("cognitive_clock")
    if isinstance(cc, Mapping):
        ticks = cc.get("causal_ticks", 0)
        if isinstance(ticks, int) and not isinstance(ticks, bool) and ticks >= 0:
            base["cognitive_clock"]["causal_ticks"] = int(ticks)
        if cc.get("last_cognitive_at") is not None:
            _parse_iso(cc.get("last_cognitive_at"), reason_code="CLOCK_STATE_TIME_INVALID")
            base["cognitive_clock"]["last_cognitive_at"] = str(cc["last_cognitive_at"])
        base["cognitive_clock"]["cognitive_backfill_forbidden"] = True
        idle = cc.get("active_meaningless_idle")
        if isinstance(idle, Mapping):
            base["cognitive_clock"]["active_meaningless_idle"] = dict(idle)

    evap = raw.get("evaporable_pool")
    if isinstance(evap, list):
        cleaned_evap: list[dict[str, Any]] = []
        for entry in evap[:MAX_EVAPORABLE_POOL]:
            if not isinstance(entry, Mapping):
                continue
            cleaned_evap.append(dict(entry))
        base["evaporable_pool"] = cleaned_evap

    ledger_raw = raw.get("slow_ledger")
    if isinstance(ledger_raw, Mapping):
        ledger = base["slow_ledger"]
        seed_raw = ledger_raw.get("world_seed")
        if isinstance(seed_raw, int) and not isinstance(seed_raw, bool):
            ledger["world_seed"] = seed_raw
        for list_key, cap in (
            ("eco_variables", MAX_ECO_VARIABLES),
            ("causal_edges", MAX_CAUSAL_EDGES),
            ("edge_effect_ledger", MAX_EDGE_EFFECT_LEDGER),
            ("manifestation_queue", MAX_MANIFESTATION_QUEUE),
            ("manifestation_ledger", MAX_MANIFESTATION_LEDGER),
        ):
            items_raw = ledger_raw.get(list_key)
            if isinstance(items_raw, list):
                ledger[list_key] = [dict(x) for x in items_raw[:cap] if isinstance(x, Mapping)]
        seq_raw = ledger_raw.get("manifestation_seq")
        if isinstance(seq_raw, int) and not isinstance(seq_raw, bool) and seq_raw >= 0:
            ledger["manifestation_seq"] = int(seq_raw)

    v02 = raw.get("v02_sync")
    if isinstance(v02, Mapping):
        for k in (
            "last_synced_at",
            "reunion_baseline_seeded",
            "room_furnishing_digest",
            "delivered_share_count",
            "album_count",
        ):
            if k in v02:
                base["v02_sync"][k] = v02[k]
        if isinstance(v02.get("synced_visit_keys"), list):
            base["v02_sync"]["synced_visit_keys"] = [str(x) for x in v02["synced_visit_keys"][:128]]
        if isinstance(v02.get("synced_route_ids"), list):
            base["v02_sync"]["synced_route_ids"] = [str(x) for x in v02["synced_route_ids"][:64]]

    ledger = raw.get("op_ledger")
    if isinstance(ledger, Mapping):
        base["op_ledger"] = {
            str(k): dict(v)
            for k, v in list(ledger.items())[-MAX_OP_LEDGER:]
            if isinstance(v, Mapping)
        }
    return base


def read_v03_clock_state(day_root: Path | str) -> tuple[dict[str, Any], bool]:
    """Read clock state without touching disk. Returns ``(state, exists_on_disk)``."""
    path = v03_clock_state_path(day_root)
    if path.parent.exists() and path.parent.is_symlink():
        raise V03ClockUnavailableError("CLOCK_STATE_SYMLINK")
    if path.is_symlink():
        raise V03ClockUnavailableError("CLOCK_STATE_SYMLINK")
    if not path.exists():
        return default_v03_clock_state(), False
    if not path.is_file():
        raise V03ClockUnavailableError("CLOCK_STATE_NOT_FILE")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V03ClockUnavailableError("CLOCK_STATE_CORRUPT") from exc
    return _validate_clock_state(raw), True


def write_v03_clock_state(day_root: Path | str, state: Mapping[str, Any]) -> dict[str, Any]:
    path = v03_clock_state_path(day_root)
    if path.parent.exists() and path.parent.is_symlink():
        raise V03ClockUnavailableError("CLOCK_STATE_SYMLINK")
    if path.exists() and path.is_symlink():
        raise V03ClockUnavailableError("CLOCK_STATE_SYMLINK")
    normalized = _validate_clock_state(dict(state))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    body = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, path)
        tmp_name = ""
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return normalized


# ---------------------------------------------------------------------------
# Cross-slice slow variable inspection & deterministic projection
# ---------------------------------------------------------------------------


def _read_optional_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.exists() or not path.is_file():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except (OSError, ValueError):
        return None


def _project_outerwear_entry(
    *,
    var_id: str,
    label: str,
    started_at: str,
    initial_state: str,
    now_dt: datetime,
    source: str,
) -> dict[str, Any]:
    if initial_state == "dry":
        return {
            "var_id": var_id,
            "label": label,
            "started_at": started_at,
            "elapsed_seconds": 0,
            "remaining_seconds": 0,
            "projected_dryness": "dry",
            "settled_dry": True,
            "source": source,
        }
    started_dt = _parse_iso(started_at)
    elapsed = max(0, int((now_dt - started_dt).total_seconds()))
    remaining = max(0, OUTERWEAR_DRYING_SECONDS - elapsed)
    is_dry = elapsed >= OUTERWEAR_DRYING_SECONDS
    return {
        "var_id": var_id,
        "label": label,
        "started_at": started_at,
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining,
        "projected_dryness": "dry" if is_dry else "damp",
        "settled_dry": is_dry,
        "dried_at": (started_dt + timedelta(seconds=OUTERWEAR_DRYING_SECONDS)).isoformat(
            timespec="seconds"
        )
        if is_dry
        else None,
        "source": source,
    }


def _project_drink_entry(
    *,
    var_id: str,
    label: str,
    started_at: str,
    initial_state: str,
    now_dt: datetime,
    source: str,
    dish_curve: bool = False,
) -> dict[str, Any]:
    started_dt = _parse_iso(started_at)
    elapsed = max(0, int((now_dt - started_dt).total_seconds()))
    if dish_curve:
        if initial_state in ("cooled", "room_temp"):
            temp = initial_state
            rem = 0
        elif initial_state == "warm":
            extra = max(0, elapsed)
            temp = "cooled" if extra >= (DISH_COOLED_SECONDS - DISH_WARM_SECONDS) else "warm"
            rem = max(0, (DISH_COOLED_SECONDS - DISH_WARM_SECONDS) - extra)
        else:
            if elapsed < DISH_WARM_SECONDS:
                temp = "hot"
            elif elapsed < DISH_COOLED_SECONDS:
                temp = "warm"
            else:
                temp = "cooled"
            rem = max(0, DISH_COOLED_SECONDS - elapsed)
    else:
        if initial_state == "room_temp":
            temp = "room_temp"
            rem = 0
        elif elapsed < DRINK_TEPID_SECONDS:
            temp = "warm"
            rem = DRINK_COOLING_SECONDS - elapsed
        elif elapsed < DRINK_COOLING_SECONDS:
            temp = "tepid"
            rem = DRINK_COOLING_SECONDS - elapsed
        else:
            temp = "room_temp"
            rem = 0
    return {
        "var_id": var_id,
        "label": label,
        "started_at": started_at,
        "elapsed_seconds": elapsed,
        "remaining_seconds": rem,
        "projected_temperature": temp,
        "is_cooled": temp in ("room_temp", "cooled"),
        "source": source,
    }


def _project_missing_note_entry(
    *,
    note_id: str,
    created_at: str,
    summary: Optional[str],
    now_dt: datetime,
    has_subject_capability: bool,
    source: str,
) -> dict[str, Any]:
    created_dt = _parse_iso(created_at)
    elapsed = max(0, int((now_dt - created_dt).total_seconds()))
    faded = elapsed >= MISSING_NOTE_FADE_SECONDS
    row: dict[str, Any] = {
        "note_id": note_id,
        "created_at": created_at,
        "elapsed_seconds": elapsed,
        "remaining_seconds": max(0, MISSING_NOTE_FADE_SECONDS - elapsed),
        "naturally_faded": faded,
        "no_absence_debt": True,
        "source": source,
    }
    if has_subject_capability and summary is not None and not faded:
        row["summary"] = summary
    return row


def _collect_slow_variable_projections(
    day_root: Path,
    state: Mapping[str, Any],
    *,
    now_dt: datetime,
    has_subject_capability: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """Pure projection across ``v03_clock_state`` and sibling phone slices."""
    root = Path(day_root)
    outerwear_list: list[dict[str, Any]] = []
    drinks_list: list[dict[str, Any]] = []
    missing_list: list[dict[str, Any]] = []
    unknown_list: list[dict[str, Any]] = []
    private_included = False

    wc = state.get("world_clock") or {}
    for sv in wc.get("slow_variables") or []:
        kind = str(sv.get("var_kind") or "")
        vid = str(sv.get("var_id") or "")
        label = str(sv.get("label") or vid)
        started_at = str(sv.get("started_at") or "")
        init_state = str(sv.get("current_state") or sv.get("initial_state") or "")
        if not vid or not started_at:
            continue
        if kind == "damp_outerwear":
            outerwear_list.append(
                _project_outerwear_entry(
                    var_id=vid,
                    label=label,
                    started_at=started_at,
                    initial_state=init_state or "damp",
                    now_dt=now_dt,
                    source="v03_clock",
                )
            )
        elif kind == "desk_drink":
            drinks_list.append(
                _project_drink_entry(
                    var_id=vid,
                    label=label,
                    started_at=started_at,
                    initial_state=init_state or "warm",
                    now_dt=now_dt,
                    source="v03_clock",
                    dish_curve=False,
                )
            )
        elif kind == "missing_note":
            row = _project_missing_note_entry(
                note_id=vid,
                created_at=started_at,
                summary=str(sv.get("label") or ""),
                now_dt=now_dt,
                has_subject_capability=has_subject_capability,
                source="v03_clock",
            )
            if "summary" in row:
                private_included = True
            missing_list.append(row)

    for unk in wc.get("pending_unknown_actions") or []:
        if str(unk.get("effect_state") or "UNKNOWN") == "UNKNOWN":
            unknown_list.append({
                "action_id": str(unk.get("action_id") or ""),
                "domain": str(unk.get("domain") or "world"),
                "summary": str(unk.get("summary") or ""),
                "submitted_at": unk.get("submitted_at"),
                "effect_state": "UNKNOWN",
                "guessed_by_clock": False,
                "requires_explicit_reconcile": True,
            })

    # Inspect somatic_comfort.json (read-only here)
    somatic = _read_optional_json(root / "phone" / "somatic_comfort.json")
    if isinstance(somatic, Mapping):
        garment = somatic.get("garment")
        if isinstance(garment, Mapping) and garment.get("state") == "hang":
            gid = str(garment.get("garment_id") or "outerwear")
            glabel = str(garment.get("label") or gid)
            drying = garment.get("drying")
            if garment.get("dryness") == "damp" and isinstance(drying, Mapping) and drying.get("started_at"):
                outerwear_list.append(
                    _project_outerwear_entry(
                        var_id=gid,
                        label=glabel,
                        started_at=str(drying["started_at"]),
                        initial_state="damp",
                        now_dt=now_dt,
                        source="somatic_comfort",
                    )
                )
            elif garment.get("dryness") == "dry":
                outerwear_list.append(
                    _project_outerwear_entry(
                        var_id=gid,
                        label=glabel,
                        started_at=str(somatic.get("updated_at") or now_dt.isoformat(timespec="seconds")),
                        initial_state="dry",
                        now_dt=now_dt,
                        source="somatic_comfort",
                    )
                )
        care = somatic.get("care")
        if isinstance(care, Mapping):
            drink = care.get("drink")
            if isinstance(drink, Mapping) and drink.get("offered_at") and not drink.get("consumed_at"):
                drinks_list.append(
                    _project_drink_entry(
                        var_id=str(drink.get("bound_item_instance_id") or "care-drink"),
                        label=str(drink.get("label") or "桌边热饮"),
                        started_at=str(drink["offered_at"]),
                        initial_state="warm",
                        now_dt=now_dt,
                        source="somatic_comfort",
                        dish_curve=False,
                    )
                )
                if drink.get("pending_unknown"):
                    unknown_list.append({
                        "action_id": str(drink.get("drink_operation_key") or "somatic-drink-op"),
                        "domain": "somatic_comfort.drink",
                        "summary": "饮用回执未决",
                        "submitted_at": str(drink["offered_at"]),
                        "effect_state": "UNKNOWN",
                        "guessed_by_clock": False,
                        "requires_explicit_reconcile": True,
                    })

    # Inspect stance_containment.json table_dishes (read-only here)
    stance_doc = _read_optional_json(root / "phone" / "stance_containment.json")
    if isinstance(stance_doc, Mapping):
        desk = stance_doc.get("desk_table_domain")
        raw_dishes = desk.get("table_dishes") if isinstance(desk, Mapping) else None
        dish_iterable: list[Mapping[str, Any]] = []
        if isinstance(raw_dishes, list):
            dish_iterable = [d for d in raw_dishes if isinstance(d, Mapping)]
        elif isinstance(raw_dishes, Mapping):
            dish_iterable = [
                dict(d, dish_id=k) for k, d in raw_dishes.items() if isinstance(d, Mapping)
            ]
        for dish in dish_iterable:
            if dish.get("cleared_at") is not None:
                continue
            rem_portions = dish.get("portions_remaining", dish.get("remaining_portions", 0))
            if int(rem_portions or 0) <= 0:
                continue
            placed_at = dish.get("placed_at") or dish.get("prepared_at")
            if isinstance(placed_at, str) and placed_at.strip():
                dish_id = str(dish.get("dish_id") or "dish")
                drinks_list.append(
                    _project_drink_entry(
                        var_id=dish_id,
                        label=str(dish.get("title") or dish_id),
                        started_at=placed_at,
                        initial_state=str(dish.get("initial_temperature") or "hot"),
                        now_dt=now_dt,
                        source="stance_containment",
                        dish_curve=True,
                    )
                )

    # Inspect emotional_tension.json missing_partner notes (read-only here)
    emotion_doc = _read_optional_json(root / "phone" / "emotional_tension.json")
    if isinstance(emotion_doc, Mapping):
        mp = emotion_doc.get("missing_partner")
        notes_raw = mp.get("notes") if isinstance(mp, Mapping) else None
        if isinstance(notes_raw, list):
            for note in notes_raw:
                if not isinstance(note, Mapping):
                    continue
                if note.get("dropped_at") or note.get("invalidated_at"):
                    continue
                c_at = note.get("created_at")
                if isinstance(c_at, str) and c_at.strip():
                    row = _project_missing_note_entry(
                        note_id=str(note.get("note_id") or "note"),
                        created_at=c_at,
                        summary=str(note.get("summary") or "") if note.get("summary") else None,
                        now_dt=now_dt,
                        has_subject_capability=has_subject_capability,
                        source="emotional_tension",
                    )
                    if "summary" in row:
                        private_included = True
                    missing_list.append(row)

    active_missing = [m for m in missing_list if not m["naturally_faded"]]
    faded_missing = [m for m in missing_list if m["naturally_faded"]]

    slow_block = {
        "outerwear_drying": outerwear_list,
        "drinks_and_dishes": drinks_list,
        "missing_partner_notes": {
            "fade_window_seconds": MISSING_NOTE_FADE_SECONDS,
            "active_count": len(active_missing),
            "naturally_faded_count": len(faded_missing),
            "no_absence_debt": True,
            "items": active_missing + faded_missing,
        },
    }
    return slow_block, unknown_list, private_included


def _settle_sibling_slow_variables_on_advance(day_root: Path, now_dt: datetime, now_iso: str) -> dict[str, int]:
    """Deterministically settle sibling slow variables when ``advance_projection`` is explicitly invoked."""
    settled = {
        "somatic_outerwear_dried": 0,
        "emotional_missing_faded": 0,
    }
    root = Path(day_root)

    # 1. Settle somatic_comfort hanging damp outerwear that completed >= 6h drying
    somatic_path = root / "phone" / "somatic_comfort.json"
    if somatic_path.exists() and not somatic_path.is_symlink():
        try:
            from components.phone import somatic_comfort_view as sc_view

            s_state, s_reason = sc_view.read_state(root)
            if s_reason is None:
                garment = s_state.get("garment") or {}
                drying = garment.get("drying")
                if (
                    garment.get("state") == "hang"
                    and garment.get("dryness") == "damp"
                    and isinstance(drying, Mapping)
                    and drying.get("started_at")
                ):
                    started_dt = _parse_iso(str(drying["started_at"]))
                    if (now_dt - started_dt).total_seconds() >= OUTERWEAR_DRYING_SECONDS:
                        s_state["garment"]["dryness"] = "dry"
                        s_state["garment"]["drying"] = None
                        s_state["revision"] = int(s_state.get("revision") or 0) + 1
                        s_state["updated_at"] = now_iso
                        # Notice: care.drink.pending_unknown is NEVER touched by clock!
                        sc_view.write_state(root, s_state)
                        settled["somatic_outerwear_dried"] += 1
        except Exception:
            pass

    # 2. Prune naturally faded (>72h) missing_partner notes in emotional_tension.json if present
    emotion_path = root / "phone" / "emotional_tension.json"
    if emotion_path.exists() and not emotion_path.is_symlink():
        try:
            from components.phone import emotional_tension_view as et_view

            e_state = et_view.read_emotional_tension_state(root)
            mp = e_state.get("missing_partner")
            if isinstance(mp, dict) and isinstance(mp.get("notes"), list):
                kept = []
                faded_here = 0
                for note in mp["notes"]:
                    if not isinstance(note, Mapping):
                        continue
                    c_at = note.get("created_at")
                    if isinstance(c_at, str) and c_at.strip():
                        c_dt = _parse_iso(c_at)
                        if (now_dt - c_dt).total_seconds() >= MISSING_NOTE_FADE_SECONDS:
                            faded_here += 1
                            continue
                    kept.append(note)
                if faded_here > 0:
                    mp["notes"] = kept
                    e_state["revision"] = int(e_state.get("revision") or 0) + 1
                    e_state["updated_at"] = now_iso
                    et_view.write_emotional_tension_state(root, e_state)
                    settled["emotional_missing_faded"] += faded_here
        except Exception:
            pass

    return settled


# ---------------------------------------------------------------------------
# v3.0 Ch.02 Slow-Variable Incubation Kernel
# (Variable Ledger -> Causal Edge Registry -> Threshold Manifestation Contract)
# ---------------------------------------------------------------------------


def _slow_ledger_of(state: dict[str, Any]) -> dict[str, Any]:
    ledger = state.get("slow_ledger")
    if not isinstance(ledger, Mapping):
        ledger = default_slow_ledger()
        state["slow_ledger"] = ledger
    return ledger


def _business_month_key(dt: datetime) -> str:
    local = _to_local_business(dt)
    return f"{local.year:04d}-{local.month:02d}"


def _derive_eco_initial_milli(world_seed: Any, var_name: str) -> int:
    """ADR-038 spirit: the initial value is derived from ``sha256(world_seed|var_name)``.

    Zero RNG (no stochastic or pseudo-random source module is imported anywhere in
    this module); the derivation lives in the latent band ``[0, 0.4)`` — Ch.02
    event A "三子变量各自在 0.2 左右" made closed.
    """
    raw = f"{int(world_seed)}|{var_name}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return int(digest[:8], 16) % ECO_SEED_LATENT_MILLI_SPAN


# ---------------------------------------------------------------------------
# Ch.02 §1.2 causal-edge -> dynamics coupling helpers (all pure/deterministic)
# ---------------------------------------------------------------------------


def _rule_bands_on(ledger: Mapping[str, Any], var_id: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Collect the drift bands and volatility bands of all ``rule`` edges pointing at ``var_id``.

    Registry order (append-only) is the application order: each band narrows what
    the previous bands allowed.  Witnessed/inferred edges contribute nothing here
    (§1.2: witnessed transmits one-shot pulses, inferred never touches the world).
    """
    drift_bands: list[tuple[int, int]] = []
    volatility_bands: list[tuple[int, int]] = []
    for edge in ledger.get("causal_edges") or []:
        if not isinstance(edge, Mapping) or str(edge.get("grade") or "") != "rule":
            continue
        if str(edge.get("to_var") or "") != var_id:
            continue
        bmin, bmax = edge.get("drift_band_min_milli_per_hour"), edge.get("drift_band_max_milli_per_hour")
        if bmin is not None and bmax is not None:
            drift_bands.append((int(bmin), int(bmax)))
        vmin, vmax = edge.get("volatility_band_min_milli"), edge.get("volatility_band_max_milli")
        if vmin is not None and vmax is not None:
            volatility_bands.append((int(vmin), int(vmax)))
    return drift_bands, volatility_bands


def _clamp_into_bands(value: int, bands: list[tuple[int, int]]) -> int:
    """Sequentially clamp ``value`` into each band (deterministic, monotone narrowing)."""
    effective = int(value)
    for lo, hi in bands:
        effective = min(max(effective, lo), hi)
    return effective


def _narrow_volatility_one_tier(current_milli: int) -> int:
    """Ch.02 §1.1 "volatility: 噪声带宽（千代接触时收窄）" — one step down a closed tier ladder.

    Closed tiers keep the narrowing auditable and deterministic: the new value is
    always the largest tier strictly below the current one; 0 is the floor.
    """
    for tier in reversed(VOLATILITY_TIERS):
        if tier < current_milli:
            return tier
    return 0


def _v04_place_ids() -> frozenset[str]:
    """Closed set of place ids the v04 neighborhood owner knows (its blueprint)."""
    try:
        from components.world.scripts import v04_neighborhood_living as v04
    except Exception:  # pragma: no cover - import failure is environment, not state
        return frozenset({"bookstore"})
    return frozenset(str(pid) for pid in (v04.PLACES_BY_ID or {}).keys())


def _manifestation_place_id(entry: Mapping[str, Any], var: Mapping[str, Any]) -> str:
    """The POI a manifestation contract binds to: contract snapshot -> variable -> bookstore default.

    "binds" is validated against the v04 blueprint at declaration time, so an
    undeclared binds keeps the historical bookstore-compatible default.
    """
    binds = entry.get("binds")
    if not binds and isinstance(var, Mapping):
        binds = var.get("binds")
    return str(binds or "bookstore")


def _is_target_poi_open(place_id: str, now_dt: datetime) -> bool:
    """Whether the bound POI is open at ``now_dt`` (business-local wall-clock, +08:00).

    Unknown place ids and always-open POIs (no declared open window, e.g. outdoor
    places) never hold; a genuinely closed POI (bookstore 19:00-10:00) holds.
    """
    try:
        from components.world.scripts import v04_neighborhood_living as v04
    except Exception:  # pragma: no cover - import failure is environment, not state
        return True
    try:
        return bool(v04.is_poi_open(place_id, _to_local_business(now_dt)))
    except Exception:
        return True


def _next_poi_open_iso(place_id: str, now_dt: datetime) -> Optional[str]:
    """Next reopening moment of the POI in business-local time, or None when always open."""
    try:
        from components.world.scripts import v04_neighborhood_living as v04
        window = v04.poi_open_window_minutes(place_id)
    except Exception:  # pragma: no cover - import failure is environment, not state
        return None
    if window is None:
        return None
    start_min, _end_min = window
    local = _to_local_business(now_dt).replace(second=0, microsecond=0)
    open_dt = local
    if local.hour * 60 + local.minute >= start_min:
        open_dt = open_dt + timedelta(days=1)
    open_dt = open_dt.replace(hour=start_min // 60, minute=start_min % 60)
    return open_dt.isoformat(timespec="seconds")


def _record_edge_effects(
    ledger: dict[str, Any],
    *,
    edge_id: str,
    grade: str,
    var_id: str,
    effects: list[dict[str, Any]],
    now_iso: str,
) -> None:
    """Append the dynamics effects an edge just produced to the audit ledger (append-only)."""
    audit = ledger.setdefault("edge_effect_ledger", [])
    for effect in effects:
        effect_doc = {
            "effect_id": _stable_id(
                "cefx", edge_id, effect.get("kind"), now_iso, len(audit)
            ),
            "edge_id": edge_id,
            "grade": grade,
            "var_id": var_id,
            "at": now_iso,
        }
        effect_doc.update(effect)
        audit.append(effect_doc)
    if len(audit) > MAX_EDGE_EFFECT_LEDGER:
        del audit[0 : len(audit) - MAX_EDGE_EFFECT_LEDGER]


def _last_ledger_manifestation(ledger: Mapping[str, Any], *, family: Optional[str] = None, major_only: bool = False) -> Optional[dict[str, Any]]:
    """Most recent settled manifestation record, optionally scoped by family / major."""
    best: Optional[dict[str, Any]] = None
    best_dt: Optional[datetime] = None
    for rec in ledger.get("manifestation_ledger") or []:
        if not isinstance(rec, Mapping) or not rec.get("manifested_at"):
            continue
        if family is not None and str(rec.get("cooldown_family") or "") != family:
            continue
        if major_only and not rec.get("entropy_major"):
            continue
        try:
            rec_dt = _parse_iso(str(rec["manifested_at"]))
        except V03ClockError:
            continue
        if best_dt is None or rec_dt > best_dt:
            best_dt = rec_dt
            best = dict(rec)
    return best


def _dispatch_manifestation_to_world(
    root: Path,
    entry: Mapping[str, Any],
    var: Mapping[str, Any],
    *,
    now_iso: str,
) -> tuple[bool, dict[str, Any]]:
    """Write the one-time physical state into the v04 neighborhood owner and return its receipt.

    The write goes through v04's own owner submission mode
    (``apply_v04_neighborhood_command`` with an ``operation_key``), so replays are
    deduplicated by v04 itself: the manifestation can never be physically written twice.
    The physical write lands on the POI the contract binds to (``binds`` — Ch.02
    §1.3 "binds: POI前门墙面 / ..."); an undeclared binds keeps the historical
    bookstore-compatible default.
    """
    try:
        from components.world.scripts import v04_neighborhood_living as v04
    except Exception as exc:  # pragma: no cover - import failure is environment, not state
        return False, {"error": "V04_OWNER_UNAVAILABLE", "detail": str(exc)}
    manifestation_id = str(entry.get("manifestation_id") or "")
    scene = str(var.get("manifest_scene") or var.get("label") or "街区慢变量显形")
    place_id = _manifestation_place_id(entry, var)
    payload = {
        "action": "record_poi_physical_state",
        "place_id": place_id,
        "state_id": f"poi_state_{manifestation_id}",
        "label": scene,
        "source_ref": manifestation_id,
        "perception_tier": str(entry.get("perception_tier") or "mid"),
        "manifested_at": now_iso,
        "operation_key": f"v03-slow-var:{manifestation_id}",
    }
    try:
        _status, result, _projection = v04.apply_v04_neighborhood_command(
            root,
            payload,
            actor_side="system",
            now_iso=now_iso,
        )
    except Exception as exc:
        reason = str(getattr(exc, "reason_code", "V04_DISPATCH_FAILED"))
        return False, {"error": reason, "detail": str(exc)}
    return True, {
        "v04_status": _status,
        "place_id": result.get("place_id"),
        "state_id": result.get("state_id"),
        "knowledge_status_after": result.get("knowledge_status_after"),
        "one_time": True,
        "llm_calls": 0,
    }


def _settle_slow_ledger(
    root: Path,
    state: dict[str, Any],
    *,
    now_dt: datetime,
    now_iso: str,
) -> dict[str, int]:
    """Deterministic incubation->manifestation settlement, driven purely by elapsed time.

    "两周没路过照样发生" holds by construction: nothing here reads visits, only
    ``elapsed`` between ``last_settled_at`` and ``now``.  Zero RNG, zero LLM calls.
    """
    ledger = _slow_ledger_of(state)
    counters = {
        "drifted": 0,
        "ripened": 0,
        "enqueued": 0,
        "enqueue_held": 0,
        "manifested": 0,
        "held": 0,
        "poi_closed_held": 0,
        "rule_band_clamped": 0,
        "dropped": 0,
        "folded": 0,
    }
    variables: list[dict[str, Any]] = [v for v in ledger.get("eco_variables") or [] if isinstance(v, Mapping)]
    vars_by_id: dict[str, dict[str, Any]] = {str(v.get("var_id") or ""): v for v in variables}

    # 1. Per-hour deterministic drift (integer milli-value math, floor division) + phase flip.
    #    Ch.02 §1.2: rule edges structurally narrow the drift band ("商圈地铁施工
    #    等外部工程通过 rule 边压低人流") — the declared slope stays immutable and
    #    the effective slope is clamped into every rule band, in registry order.
    for var in variables:
        phase = str(var.get("phase") or "incubating")
        if phase not in ("dormant", "incubating"):
            continue
        last_raw = var.get("last_settled_at") or var.get("started_at")
        if not isinstance(last_raw, str) or not last_raw.strip():
            continue
        try:
            last_dt = _parse_iso(last_raw)
        except V03ClockError:
            continue
        elapsed = max(0, int((now_dt - last_dt).total_seconds()))
        if elapsed <= 0:
            continue
        if phase == "incubating":
            declared_drift = int(var.get("drift_milli_per_hour") or 0)
            drift_bands, _vol_bands = _rule_bands_on(ledger, str(var.get("var_id") or ""))
            drift = _clamp_into_bands(declared_drift, drift_bands)
            if drift != declared_drift:
                counters["rule_band_clamped"] += 1
            delta_milli = (drift * elapsed) // 3600
            before = int(var.get("value_milli") or 0)
            after = min(ECO_VALUE_MILLI_MAX, max(0, before + delta_milli))
            if after != before:
                var["value_milli"] = after
                counters["drifted"] += 1
        var["last_settled_at"] = now_iso
        threshold = int(var.get("manifest_threshold_milli") or RIPE_THRESHOLD_MILLI_DEFAULT)
        if phase == "incubating" and int(var.get("value_milli") or 0) >= threshold:
            var["phase"] = "ripe"
            var["rip_at"] = now_iso
            counters["ripened"] += 1

    # 2. Threshold Manifestation Contract: ripe variables enqueue a one-time event
    last_major = _last_ledger_manifestation(ledger, major_only=True)
    for var in variables:
        if str(var.get("phase") or "") != "ripe":
            continue
        if var.get("manifestation_id") or var.get("contract_dropped"):
            continue
        if bool(var.get("entropy_major")) and last_major is not None:
            try:
                major_dt = _parse_iso(str(last_major["manifested_at"]))
            except V03ClockError:
                major_dt = None
            if major_dt is not None and (now_dt - major_dt).total_seconds() < CALM_PERIOD_SECONDS:
                var["enqueue_hold_reason"] = "calm_period"
                var["enqueue_hold_until"] = (
                    major_dt + timedelta(seconds=CALM_PERIOD_SECONDS)
                ).isoformat(timespec="seconds")
                counters["enqueue_held"] += 1
                continue
        if len(ledger.get("manifestation_queue") or []) >= MAX_MANIFESTATION_QUEUE:
            var["enqueue_hold_reason"] = "queue_full"
            counters["enqueue_held"] += 1
            continue
        var["enqueue_hold_reason"] = None
        var["enqueue_hold_until"] = None
        manifestation_id = _stable_id("mfst", var.get("var_id"), var.get("rip_at") or now_iso)
        entry = {
            "manifestation_id": manifestation_id,
            "var_id": var.get("var_id"),
            "status": "queued",
            "created_at": now_iso,
            "cooldown_family": var.get("cooldown_family"),
            "entropy_major": bool(var.get("entropy_major")),
            "perception_tier": var.get("perception_tier"),
            "scene": str(var.get("manifest_scene") or var.get("label") or ""),
            "binds": var.get("binds"),
            "hold_reason": None,
            "hold_until": None,
            "last_dispatch_error": None,
        }
        ledger.setdefault("manifestation_queue", []).append(entry)
        var["manifestation_id"] = manifestation_id
        counters["enqueued"] += 1

    # 3. Dispatch queued contracts under the steady-state guards
    month_key = _business_month_key(now_dt)
    month_major_count = 0
    for rec in ledger.get("manifestation_ledger") or []:
        if not isinstance(rec, Mapping) or not rec.get("entropy_major") or not rec.get("manifested_at"):
            continue
        try:
            if _business_month_key(_parse_iso(str(rec["manifested_at"]))) == month_key:
                month_major_count += 1
        except V03ClockError:
            continue

    queue: list[dict[str, Any]] = ledger.setdefault("manifestation_queue", [])
    for entry in list(queue):
        if not isinstance(entry, Mapping) or entry.get("status") != "queued":
            continue
        var = vars_by_id.get(str(entry.get("var_id") or ""))
        if var is None:
            entry["status"] = "dropped"
            entry["drop_reason"] = "VARIABLE_GONE"
            counters["dropped"] += 1
            continue
        # Guard order: the neighborhood-level monthly entropy budget is reported
        # first (the topical block); the family cooldown still re-checks after a
        # budget release, so both holds always apply — only the reported reason
        # differs.  A held entry stays queued (never dropped).
        if entry.get("entropy_major") and month_major_count >= ENTROPY_BUDGET_MONTHLY_MAJOR:
            entry["hold_reason"] = "entropy_budget"
            counters["held"] += 1
            continue
        family = str(entry.get("cooldown_family") or "")
        cooldown_seconds = COOLDOWN_FAMILY_SECONDS.get(family)
        last_family = _last_ledger_manifestation(ledger, family=family)
        if cooldown_seconds is not None and last_family is not None:
            try:
                fam_dt = _parse_iso(str(last_family["manifested_at"]))
            except V03ClockError:
                fam_dt = None
            if fam_dt is not None:
                since = (now_dt - fam_dt).total_seconds()
                if since < cooldown_seconds:
                    entry["hold_reason"] = "family_cooldown"
                    entry["hold_until"] = (
                        fam_dt + timedelta(seconds=cooldown_seconds)
                    ).isoformat(timespec="seconds")
                    counters["held"] += 1
                    continue
        # Ch.02 §1.3 manifestation contract binds to a POI: dispatching into a
        # POI that is not currently open (bookstore closed 19:00-10:00) holds the
        # contract — queued for retry on a later advance — parallel to the
        # cooldown/entropy holds above.  Never dropped for being closed.
        place_id = _manifestation_place_id(entry, var)
        if not _is_target_poi_open(place_id, now_dt):
            entry["hold_reason"] = MANIFESTATION_HOLD_POI_CLOSED
            entry["hold_until"] = _next_poi_open_iso(place_id, now_dt)
            counters["poi_closed_held"] += 1
            continue

        var["phase"] = "manifesting"
        ok, dispatch_receipt = _dispatch_manifestation_to_world(root, entry, var, now_iso=now_iso)
        if not ok:
            var["phase"] = "ripe"
            entry["last_dispatch_error"] = str(dispatch_receipt.get("error") or "V04_DISPATCH_FAILED")
            continue
        entry["status"] = "manifested"
        entry["manifested_at"] = now_iso
        entry["receipt"] = dispatch_receipt
        var["phase"] = "settled"
        var["settled_at"] = now_iso
        var["manifested_at"] = now_iso
        ledger.setdefault("manifestation_ledger", []).append({
            "manifestation_id": entry.get("manifestation_id"),
            "var_id": entry.get("var_id"),
            "cooldown_family": entry.get("cooldown_family"),
            "entropy_major": bool(entry.get("entropy_major")),
            "perception_tier": entry.get("perception_tier"),
            "scene": entry.get("scene"),
            "manifested_at": now_iso,
            "receipt": dispatch_receipt,
        })
        if len(ledger["manifestation_ledger"]) > MAX_MANIFESTATION_LEDGER:
            del ledger["manifestation_ledger"][0]
        queue.remove(entry)
        counters["manifested"] += 1
        if entry.get("entropy_major"):
            month_major_count += 1

    # 4. Residue cold-folding: settled variables older than 90d collapse to one line
    for var in variables:
        if str(var.get("phase") or "") != "settled" or var.get("folded"):
            continue
        settled_raw = var.get("settled_at")
        if not isinstance(settled_raw, str) or not settled_raw.strip():
            continue
        try:
            settled_dt = _parse_iso(settled_raw)
        except V03ClockError:
            continue
        if (now_dt - settled_dt).total_seconds() >= RESIDUE_FOLD_SECONDS:
            var["folded"] = True
            var["phase"] = "decayed"
            counters["folded"] += 1

    return counters


def _project_slow_ledger_view(state: Mapping[str, Any], *, now_dt: datetime) -> dict[str, Any]:
    """Pure-read projection of the slow ledger (no disk write, no recomputation side effects)."""
    ledger = state.get("slow_ledger") if isinstance(state.get("slow_ledger"), Mapping) else default_slow_ledger()

    eco_out: list[dict[str, Any]] = []
    for var in ledger.get("eco_variables") or []:
        if not isinstance(var, Mapping):
            continue
        var_id = str(var.get("var_id") or "")
        declared_drift = int(var.get("drift_milli_per_hour") or 0)
        live_volatility = int(var.get("volatility_milli") or 0)
        drift_bands, volatility_bands = _rule_bands_on(ledger, var_id)
        eco_out.append({
            "var_id": var.get("var_id"),
            "var_name": var.get("var_name"),
            "var_class": var.get("var_class"),
            "label": var.get("label"),
            "carriers": list(var.get("carriers") or []),
            "phase": var.get("phase"),
            "value_milli": int(var.get("value_milli") or 0),
            "value": round(int(var.get("value_milli") or 0) / 1000.0, 3),
            "drift_milli_per_hour": declared_drift,
            "effective_drift_milli_per_hour": _clamp_into_bands(declared_drift, drift_bands),
            "volatility_milli": live_volatility,
            "declared_volatility_milli": int(
                var.get("declared_volatility_milli")
                if var.get("declared_volatility_milli") is not None
                else live_volatility
            ),
            "effective_volatility_milli": _clamp_into_bands(live_volatility, volatility_bands),
            "witnessed_contact_count": int(var.get("witnessed_contact_count") or 0),
            "manifest_threshold_milli": int(var.get("manifest_threshold_milli") or RIPE_THRESHOLD_MILLI_DEFAULT),
            "cooldown_family": var.get("cooldown_family"),
            "entropy_major": bool(var.get("entropy_major")),
            "perception_tier": var.get("perception_tier"),
            "manifest_scene": var.get("manifest_scene"),
            "binds": var.get("binds"),
            "started_at": var.get("started_at"),
            "last_settled_at": var.get("last_settled_at"),
            "rip_at": var.get("rip_at"),
            "settled_at": var.get("settled_at"),
            "manifested_at": var.get("manifested_at"),
            "manifestation_id": var.get("manifestation_id"),
            "enqueue_hold_reason": var.get("enqueue_hold_reason"),
            "folded": bool(var.get("folded")),
            "has_active_contract": bool(var.get("manifestation_id")),
        })

    edges_out: list[dict[str, Any]] = []
    for edge in ledger.get("causal_edges") or []:
        if isinstance(edge, Mapping):
            edges_out.append(dict(edge))

    effects_out: list[dict[str, Any]] = []
    for effect in (ledger.get("edge_effect_ledger") or [])[-32:]:
        if isinstance(effect, Mapping):
            effects_out.append(dict(effect))

    queue_out: list[dict[str, Any]] = []
    queue_status_counts: dict[str, int] = {}
    for entry in ledger.get("manifestation_queue") or []:
        if not isinstance(entry, Mapping):
            continue
        status = str(entry.get("status") or "queued")
        queue_status_counts[status] = queue_status_counts.get(status, 0) + 1
        queue_out.append({
            "manifestation_id": entry.get("manifestation_id"),
            "var_id": entry.get("var_id"),
            "status": status,
            "created_at": entry.get("created_at"),
            "cooldown_family": entry.get("cooldown_family"),
            "entropy_major": bool(entry.get("entropy_major")),
            "perception_tier": entry.get("perception_tier"),
            "scene": entry.get("scene"),
            "binds": entry.get("binds"),
            "hold_reason": entry.get("hold_reason"),
            "hold_until": entry.get("hold_until"),
            "last_dispatch_error": entry.get("last_dispatch_error"),
            "drop_reason": entry.get("drop_reason"),
        })

    ledger_out: list[dict[str, Any]] = []
    for rec in (ledger.get("manifestation_ledger") or [])[-16:]:
        if isinstance(rec, Mapping):
            ledger_out.append({
                "manifestation_id": rec.get("manifestation_id"),
                "var_id": rec.get("var_id"),
                "cooldown_family": rec.get("cooldown_family"),
                "entropy_major": bool(rec.get("entropy_major")),
                "perception_tier": rec.get("perception_tier"),
                "scene": rec.get("scene"),
                "manifested_at": rec.get("manifested_at"),
                "receipt": dict(rec.get("receipt") or {}),
            })

    month_key = _business_month_key(now_dt)
    cooldown_guards: dict[str, dict[str, Any]] = {}
    for family, seconds in COOLDOWN_FAMILY_SECONDS.items():
        last = _last_ledger_manifestation(ledger, family=family)
        remaining = 0
        last_at = None
        if last is not None:
            try:
                fam_dt = _parse_iso(str(last["manifested_at"]))
                last_at = last["manifested_at"]
                remaining = max(0, int(seconds - (now_dt - fam_dt).total_seconds()))
            except V03ClockError:
                pass
        cooldown_guards[family] = {
            "cooldown_seconds": seconds,
            "last_manifested_at": last_at,
            "remaining_seconds": remaining,
        }
    last_major = _last_ledger_manifestation(ledger, major_only=True)
    calm_until = None
    if last_major is not None:
        try:
            major_dt = _parse_iso(str(last_major["manifested_at"]))
            calm_until = (major_dt + timedelta(seconds=CALM_PERIOD_SECONDS)).isoformat(timespec="seconds")
        except V03ClockError:
            pass

    return {
        "world_seed": ledger.get("world_seed"),
        "eco_variables": eco_out,
        "active_variable_budget": {
            "cap": MAX_ACTIVE_ECO_VARIABLES,
            "active_count": sum(1 for v in eco_out if v["phase"] in ACTIVE_ECO_PHASES),
        },
        "causal_edges": edges_out,
        "edge_effect_ledger": effects_out,
        "manifestation_queue": queue_out,
        "queue_status_counts": queue_status_counts,
        "manifestation_ledger": ledger_out,
        "steady_state_guards": {
            "cooldown_families": cooldown_guards,
            "entropy_budget": {
                "monthly_major_cap": ENTROPY_BUDGET_MONTHLY_MAJOR,
                "used_this_month": sum(
                    1
                    for rec in ledger.get("manifestation_ledger") or []
                    if isinstance(rec, Mapping)
                    and rec.get("entropy_major")
                    and rec.get("manifested_at")
                    and _business_month_key(_parse_iso(str(rec["manifested_at"]))) == month_key
                ),
                "month_key": month_key,
                "business_timezone": "+08:00",
            },
            "calm_period": {
                "days": CALM_PERIOD_DAYS,
                "calm_until": calm_until,
            },
        },
        "llm_calls": 0,
        "model_called": False,
    }


# ---------------------------------------------------------------------------
# 0.2 Slice Synchronizer from Real Committed World & Life Artifacts
# ---------------------------------------------------------------------------


def _discover_world_roots(day_root: Path) -> list[Path]:
    candidates: list[Path] = []
    inst = _read_optional_json(day_root / "instance.json")
    if isinstance(inst, Mapping) and inst.get("world_home"):
        candidates.append(Path(str(inst["world_home"])))
    for sub in ("world", "world_home", "one-day/world"):
        p = day_root / sub
        if p not in candidates:
            candidates.append(p)
    if (day_root / "data" / "world").exists():
        candidates.append(day_root)
    return [c for c in candidates if c.exists() and not c.is_symlink()]


def _scan_committed_world_facts(day_root: Path, now_iso: str) -> dict[str, Any]:
    """Scan real committed World locations, non-home outings, room furnishings, and works/albums."""
    root = Path(day_root)
    valid_places: set[str] = set()
    non_home_visits: list[dict[str, str]] = []
    current_location: dict[str, Any] = {"status": "unknown", "place_id": None, "area_id": None, "home": None}
    room_items: list[dict[str, Any]] = []
    delivered_share_ids: list[str] = []
    album_artifact_ids: list[str] = []

    # Check instance world_package if declared
    inst = _read_optional_json(root / "instance.json")
    if isinstance(inst, Mapping):
        wp = inst.get("world_package")
        if isinstance(wp, str) and wp.strip():
            wp_path = Path(wp)
            world_pkg_doc = _read_optional_json(
                wp_path / "world.json" if wp_path.is_dir() else wp_path
            )
            if isinstance(world_pkg_doc, Mapping):
                locs = world_pkg_doc.get("locations") or world_pkg_doc.get("places")
                if isinstance(locs, list):
                    for loc in locs:
                        if isinstance(loc, Mapping) and loc.get("id"):
                            valid_places.add(str(loc["id"]))
                elif isinstance(locs, Mapping):
                    valid_places.update(str(k) for k in locs.keys())

    for wroot in _discover_world_roots(root):
        for ws_rel in (
            Path("data") / "world" / "world_state.json",
            Path("world_state.json"),
        ):
            ws = _read_optional_json(wroot / ws_rel)
            if not isinstance(ws, Mapping):
                continue
            places_raw = ws.get("places") or ws.get("locations")
            if isinstance(places_raw, Mapping):
                valid_places.update(str(k) for k in places_raw.keys())
            elif isinstance(places_raw, list):
                for entry in places_raw:
                    if isinstance(entry, Mapping):
                        pid = entry.get("place_id") or entry.get("id")
                        if pid:
                            valid_places.add(str(pid))

            loc = ws.get("location")
            if isinstance(loc, Mapping) and loc.get("place_id"):
                pid = str(loc["place_id"]).strip()
                aid = str(loc.get("area_id") or "").strip() or None
                valid_places.add(pid)
                current_location = {
                    "status": "available",
                    "place_id": pid,
                    "area_id": aid,
                    "home": pid == "home",
                }
                if pid != "home":
                    visited_at = str(
                        loc.get("arrived_at")
                        or loc.get("updated_at")
                        or ws.get("updated_at")
                        or now_iso
                    )
                    non_home_visits.append({
                        "place_id": pid,
                        "visited_at": visited_at,
                        "note": str(loc.get("note") or "") or None,
                    })

            # Explicit committed travel_history / visited_places in world_state
            for hist_key in ("visited_places", "travel_history", "committed_outings"):
                hist = ws.get(hist_key)
                if isinstance(hist, list):
                    for item in hist:
                        if isinstance(item, Mapping) and item.get("place_id"):
                            pid = str(item["place_id"]).strip()
                            if pid and pid != "home":
                                valid_places.add(pid)
                                v_at = str(
                                    item.get("visited_at")
                                    or item.get("arrived_at")
                                    or ws.get("updated_at")
                                    or now_iso
                                )
                                non_home_visits.append({
                                    "place_id": pid,
                                    "visited_at": v_at,
                                    "note": str(item.get("note") or "") or None,
                                    "album_artifact_id": str(item.get("album_artifact_id") or "") or None,
                                })

        # Inspect world_timeline.json for committed arrivals
        for tl_rel in (
            Path("data") / "world" / "world_timeline.json",
            Path("world_timeline.json"),
        ):
            tl = _read_optional_json(wroot / tl_rel)
            if isinstance(tl, Mapping) and isinstance(tl.get("events"), list):
                for ev in tl["events"]:
                    if not isinstance(ev, Mapping):
                        continue
                    dest = ev.get("destination_location_id") or ev.get("place_id")
                    status = str(ev.get("status") or ev.get("outcome") or "committed").lower()
                    if dest and str(dest) != "home" and status in ("committed", "applied", "arrived"):
                        pid = str(dest).strip()
                        valid_places.add(pid)
                        v_at = str(ev.get("at") or ev.get("arrived_at") or now_iso)
                        non_home_visits.append({
                            "place_id": pid,
                            "visited_at": v_at,
                            "note": None,
                        })

        # Inspect body_substrate.json for real physical object placements
        for bs_rel in (
            Path("data") / "world" / "body_substrate.json",
            Path("body_substrate.json"),
        ):
            bs = _read_optional_json(wroot / bs_rel)
            if isinstance(bs, Mapping) and isinstance(bs.get("placements"), Mapping):
                for obj_id, pl in sorted(bs["placements"].items()):
                    if not isinstance(pl, Mapping):
                        continue
                    room_items.append({
                        "item_instance_id": str(obj_id),
                        "name": str(pl.get("label") or pl.get("name") or obj_id)[:80],
                        "placement": {
                            "status": "available",
                            "kind": str(pl.get("kind") or "room"),
                            "location_id": str(pl.get("location_id") or "home"),
                            "area_id": str(pl.get("area_id") or "") or None,
                            "surface_id": str(pl.get("surface_id") or "") or None,
                            "container_id": str(pl.get("container_id") or "") or None,
                            "slot_id": str(pl.get("slot_id") or "") or None,
                        },
                    })

        # Inspect world_property_economy.json for owned room items not yet in placements
        for ec_rel in (
            Path("state") / "world_property_economy.json",
            Path("world_property_economy.json"),
        ):
            ec = _read_optional_json(wroot / ec_rel)
            if isinstance(ec, Mapping) and isinstance(ec.get("owned_items"), list):
                seen_ids = {r["item_instance_id"] for r in room_items}
                for item in ec["owned_items"]:
                    if not isinstance(item, Mapping):
                        continue
                    iid = str(item.get("item_instance_id") or item.get("object_id") or "").strip()
                    if iid and iid not in seen_ids:
                        seen_ids.add(iid)
                        room_items.append({
                            "item_instance_id": iid,
                            "name": str(item.get("name") or item.get("catalog_item_id") or iid)[:80],
                            "placement": {
                                "status": "available",
                                "kind": "room",
                                "location_id": str(item.get("location_id") or "home"),
                            },
                        })

    # Also include committed room arrangements in stance_containment.json if modified
    sc_doc = _read_optional_json(root / "phone" / "stance_containment.json")
    if isinstance(sc_doc, Mapping) and int(sc_doc.get("revision") or 0) > 0:
        cdom = sc_doc.get("containment_domain")
        raw_items = cdom.get("items") if isinstance(cdom, Mapping) else None
        item_rows: list[Mapping[str, Any]] = []
        if isinstance(raw_items, list):
            item_rows = [it for it in raw_items if isinstance(it, Mapping)]
        elif isinstance(raw_items, Mapping):
            item_rows = [
                dict(it, item_id=k) for k, it in sorted(raw_items.items()) if isinstance(it, Mapping)
            ]
        seen_ids = {r["item_instance_id"] for r in room_items}
        for item in item_rows:
            item_id = str(item.get("item_id") or "").strip()
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            parent_c = item.get("parent_container_id")
            hand_s = item.get("held_in_hand") or item.get("held_in_slot")
            loc_p = str(item.get("place_id") or item.get("physical_place") or "study_desk")
            pl_dict: dict[str, Any] = {
                "status": "available",
                "location_id": "home",
            }
            if hand_s:
                pl_dict["kind"] = "hand"
                pl_dict["slot_id"] = str(hand_s)
            elif parent_c:
                pl_dict["kind"] = "container"
                pl_dict["container_id"] = str(parent_c)
            else:
                pl_dict["kind"] = "surface"
                pl_dict["surface_id"] = loc_p
            room_items.append({
                "item_instance_id": item_id,
                "name": str(item.get("label") or item_id)[:80],
                "placement": pl_dict,
            })

    # Inspect committed shares / works / albums in phone/ or life/
    for share_candidate in (
        root / "phone" / "delivered_shares.json",
        root / "life" / "delivered_shares.json",
        root / "phone" / "committed_works.json",
    ):
        sdoc = _read_optional_json(share_candidate)
        if isinstance(sdoc, Mapping):
            for sid in sdoc.get("share_ids") or []:
                if isinstance(sid, str) and sid.strip() and sid.strip() not in delivered_share_ids:
                    delivered_share_ids.append(sid.strip())
            for aid in sdoc.get("album_artifact_ids") or []:
                if isinstance(aid, str) and aid.strip() and aid.strip() not in album_artifact_ids:
                    album_artifact_ids.append(aid.strip())
            if isinstance(sdoc.get("shares"), list):
                for srow in sdoc["shares"]:
                    if isinstance(srow, Mapping) and srow.get("share_id"):
                        sid = str(srow["share_id"]).strip()
                        if sid and sid not in delivered_share_ids:
                            delivered_share_ids.append(sid)

    for visit in non_home_visits:
        aid = visit.get("album_artifact_id")
        if isinstance(aid, str) and aid.strip() and aid not in album_artifact_ids:
            album_artifact_ids.append(aid)

    return {
        "valid_places": sorted(valid_places),
        "non_home_visits": non_home_visits,
        "current_location": current_location,
        "room_items": room_items,
        "delivered_share_ids": delivered_share_ids,
        "album_artifact_ids": album_artifact_ids,
    }


def sync_v02_slices_from_committed_world(
    day_root: Path | str,
    now_iso: Optional[str] = None,
    *,
    refresh_reunion_baseline: bool = False,
) -> dict[str, Any]:
    """Deterministically sync committed World outings, room furnishings, and works/albums into 0.2 slices.

    Zero LLM calls (``llm_calls == 0``), zero fabricated visits, idempotent on replay.
    """
    root = Path(day_root)
    now_dt, clean_now, _ = _resolve_now(now_iso)
    facts = _scan_committed_world_facts(root, clean_now)

    synced_slices: list[str] = []
    bench_logged = 0
    routes_logged = 0
    reunion_updated = False
    first_room_captured = False

    # 1. Sync non-home committed place visits into 0.2 outing_bench_bookstore.json
    if facts["non_home_visits"]:
        from components.phone import outing_places_view as opv

        bb_state, bb_reason = opv.read_bench_bookstore_state(root)
        if bb_reason is None:
            bb_changed = False
            valid_pids = set(facts["valid_places"]) | {
                v["place_id"] for v in facts["non_home_visits"] if v.get("place_id")
            }
            for visit in facts["non_home_visits"]:
                pid = str(visit.get("place_id") or "").strip()
                if not pid or pid == "home":
                    continue
                try:
                    v_dt = _parse_iso(visit.get("visited_at") or clean_now)
                    v_iso = v_dt.isoformat(timespec="seconds")
                except V03ClockError:
                    v_iso = clean_now
                op_key = f"v03-world-visit:{pid}:{v_iso}"[:120]
                try:
                    bb_state, _entry, changed = opv.apply_log_bench_visit(
                        bb_state,
                        place_id=pid,
                        valid_place_ids=valid_pids,
                        operation_key=op_key,
                        note=visit.get("note"),
                        shared=False,
                        now_iso=v_iso,
                    )
                    if changed:
                        bb_changed = True
                        bench_logged += 1
                except opv.OutingStateError:
                    continue
            if bb_changed:
                opv.write_bench_bookstore_state(root, bb_state)
                synced_slices.append("outing_bench_bookstore")

        # Sync non-home walk route + album output into 0.2 outing_routes_market.json when album present
        if facts["album_artifact_ids"]:
            rm_state, rm_reason = opv.read_routes_market_state(root)
            if rm_reason is None:
                unique_stops: list[str] = []
                for v in facts["non_home_visits"]:
                    pid = str(v.get("place_id") or "").strip()
                    if pid and pid != "home" and pid not in unique_stops:
                        unique_stops.append(pid)
                if unique_stops:
                    album_id = facts["album_artifact_ids"][0]
                    route_id = _stable_id("route-v03", ",".join(unique_stops), album_id)
                    try:
                        rm_state, _route, r_changed = opv.apply_save_walk_route(
                            rm_state,
                            route_id=route_id,
                            title=f"外出足迹：{unique_stops[0]}",
                            place_ids=unique_stops[:6],
                            valid_place_ids=set(facts["valid_places"]) | set(unique_stops),
                            album_artifact_id=album_id,
                            album_supplied=True,
                            caption=None,
                            caption_supplied=False,
                            shared=False,
                            now_iso=clean_now,
                        )
                        if r_changed:
                            opv.write_routes_market_state(root, rm_state)
                            routes_logged += 1
                            synced_slices.append("outing_routes_market")
                    except opv.OutingStateError:
                        pass

    # 2. Sync room furnishings & delivered shares into 0.2 reunion_anchor.json baseline
    has_real_home_facts = bool(
        facts["room_items"]
        or facts["delivered_share_ids"]
        or facts["current_location"].get("status") == "available"
    )
    if has_real_home_facts:
        from components.phone import home_hearth_view as hhv

        re_state, re_reason = hhv.read_reunion_state(root)
        if re_reason is None and (re_state.get("anchor") is None or refresh_reunion_baseline):
            snapshot = {
                "clock": {"source": "v03_world_clock", "logical_at": clean_now},
                "clock_offset": now_dt.strftime("%z")[:3] + ":" + now_dt.strftime("%z")[3:]
                if now_dt.strftime("%z")
                else "+00:00",
                "location": facts["current_location"],
                "reading": {"status": "unavailable", "reason_code": "NOT_SAMPLED_IN_SYNC"},
                "delivered": {
                    "status": "available",
                    "share_ids": facts["delivered_share_ids"],
                    "count": len(facts["delivered_share_ids"]),
                    "truncated": False,
                },
                "items": {
                    "status": "available",
                    "items": facts["room_items"],
                    "count": len(facts["room_items"]),
                    "truncated": False,
                },
                "complete": True,
            }
            re_state, changed = hhv.capture_anchor(re_state, snapshot, clean_now)
            if changed:
                hhv.write_reunion_state(root, re_state)
                reunion_updated = True
                synced_slices.append("reunion_anchor")

        # Seal Day-1 first_room baseline in longterm_room_habits.json if room items exist and uncaptured
        if facts["room_items"] or facts["album_artifact_ids"]:
            from components.phone import long_companionship_view as lcv

            rh_state, rh_reason = lcv.read_room_habits_state(root)
            if rh_reason is None and rh_state.get("first_room") is None:
                item_names = [
                    str(it.get("name") or it["item_instance_id"])
                    for it in facts["room_items"][:12]
                ]
                room_snap = {
                    "location": facts["current_location"].get("place_id") or "home",
                    "furniture_count": len(facts["room_items"]),
                    "item_count": len(facts["room_items"]),
                    "item_names": item_names,
                    "plant_names": [],
                    "reading_page": None,
                    "delivered_share_count": len(facts["delivered_share_ids"]),
                }
                try:
                    rh_state, _rec, rh_changed = lcv.apply_capture_first_room(
                        rh_state,
                        snapshot=room_snap,
                        album_artifact_id=facts["album_artifact_ids"][0]
                        if facts["album_artifact_ids"]
                        else None,
                        note=None,
                        now_iso=clean_now,
                    )
                    if rh_changed:
                        lcv.write_room_habits_state(root, rh_state)
                        first_room_captured = True
                        synced_slices.append("longterm_room_habits")
                except lcv.LongtermStateError:
                    pass

    digest_src = json.dumps(
        {
            "items": facts["room_items"],
            "shares": facts["delivered_share_ids"],
            "albums": facts["album_artifact_ids"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    furnishing_digest = hashlib.sha256(digest_src.encode("utf-8")).hexdigest()[:16]

    return {
        "ok": True,
        "synced_at": clean_now,
        "llm_calls": 0,
        "model_called": False,
        "changed": bool(synced_slices),
        "synced_slices": synced_slices,
        "bench_visits_logged": bench_logged,
        "walk_routes_logged": routes_logged,
        "reunion_baseline_updated": reunion_updated,
        "first_room_captured": first_room_captured,
        "room_furnishing_digest": furnishing_digest,
        "room_item_count": len(facts["room_items"]),
        "delivered_share_count": len(facts["delivered_share_ids"]),
        "album_count": len(facts["album_artifact_ids"]),
    }


# ---------------------------------------------------------------------------
# Pure View Projection & HTTP Entrypoints
# ---------------------------------------------------------------------------


def project_v03_clock_data(
    day_root: Path | str,
    state: Mapping[str, Any],
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    now_dt, clean_now, clock_source = _resolve_now(now_iso)
    wc = state.get("world_clock") or {}
    cc = state.get("cognitive_clock") or {}

    last_committed = wc.get("last_committed_at")
    if isinstance(last_committed, str) and last_committed.strip():
        last_dt = _parse_iso(last_committed)
        gap_seconds = max(0, int((now_dt - last_dt).total_seconds()))
    else:
        gap_seconds = 0

    ambient = _derive_ambient_soundscape(now_dt, wc.get("ambient_date"))
    slow_vars, unknown_actions, slow_private = _collect_slow_variable_projections(
        Path(day_root),
        state,
        now_dt=now_dt,
        has_subject_capability=has_subject_capability,
    )
    slow_ledger_view = _project_slow_ledger_view(state, now_dt=now_dt)

    # Filter active evaporable items on pure read (expired items vanish without tombstones)
    active_evaporable: list[dict[str, Any]] = []
    evap_private = False
    for item in state.get("evaporable_pool") or []:
        if not isinstance(item, Mapping):
            continue
        exp_raw = item.get("expires_at")
        if isinstance(exp_raw, str) and exp_raw.strip():
            exp_dt = _parse_iso(exp_raw)
            if now_dt >= exp_dt:
                continue
        entry_view: dict[str, Any] = {
            "thought_id": item.get("thought_id"),
            "domain_kind": item.get("domain_kind"),
            "accountability_tier": "EVAPORABLE",
            "created_at": item.get("created_at"),
            "expires_at": item.get("expires_at"),
            "ttl_seconds": item.get("ttl_seconds"),
            "no_tombstone": True,
            "reflection_required": False,
        }
        if has_subject_capability:
            entry_view["text"] = item.get("text")
            evap_private = True
        active_evaporable.append(entry_view)

    idle_raw = cc.get("active_meaningless_idle")
    idle_view: Optional[dict[str, Any]] = None
    idle_private = False
    if isinstance(idle_raw, Mapping):
        idle_view = {
            "idle_id": idle_raw.get("idle_id"),
            "idle_kind": idle_raw.get("idle_kind"),
            "label": MEANINGLESS_IDLE_KINDS.get(str(idle_raw.get("idle_kind") or ""), {}).get(
                "label", str(idle_raw.get("idle_kind") or "")
            ),
            "started_at": idle_raw.get("started_at"),
            "justification_required": False,
            "growth_score_delta": 0,
            "productivity_penalty": 0,
            "forced_summary_required": False,
        }
        if has_subject_capability and idle_raw.get("quiet_note"):
            idle_view["quiet_note"] = idle_raw.get("quiet_note")
            idle_private = True

    dwell_proj = life_dwell.project_life_dwell(
        day_root, now_iso=clean_now, subject_view=has_subject_capability
    )
    desire_proj = desire_lc.project_everyday_desires(
        day_root,
        now_iso=clean_now,
        channel_visibility="subject_private" if has_subject_capability else "public_or_shared",
    )

    return {
        "schema": V03_CLOCK_VIEW_SCHEMA,
        "state_schema": V03_CLOCK_STATE_SCHEMA,
        "revision": int(state.get("revision") or 0),
        "updated_at": state.get("updated_at"),
        "view": "subject" if has_subject_capability else "partner_default",
        "private_text_included": bool(
            has_subject_capability and (slow_private or evap_private or idle_private)
        ),
        "llm_calls": 0,
        "model_called": False,
        "formula": WORLD_FORMULA,
        "clocks": {
            "wall_clock": {
                "now_iso": clean_now,
                "local_date": _to_local_business(now_dt).date().isoformat(),
                "source": clock_source,
            },
            "world_clock": {
                "rulepack_id": RULEPACK_ID,
                "last_committed_at": last_committed,
                "projected_to": clean_now,
                "offline_gap_seconds": gap_seconds,
                "host_gap_threshold_seconds": life_dwell.HOST_GAP_MIN_SECONDS,
                "host_gaps": dwell_proj["host_gaps"],
                "ambient_date": ambient["local_date"],
                "ambient_date_switched": ambient["date_switched"],
                "ambient_soundscape": ambient,
                "gap_receipts": list(wc.get("gap_receipts") or []),
                "llm_calls": 0,
            },
            "cognitive_clock": {
                "causal_ticks": int(cc.get("causal_ticks") or 0),
                "last_cognitive_at": cc.get("last_cognitive_at"),
                "cognitive_backfill_forbidden": True,
                "fabricated_downtime_episodes": 0,
                "active_meaningless_idle": idle_view,
                "active_dwell": dwell_proj["active_dwell"],
                "subjective_time": dwell_proj["subjective_time"],
            },
        },
        "slow_variables": slow_vars,
        "slow_ledger": slow_ledger_view,
        "iron_laws": {
            "unknown_actions_never_guessed_by_clock": True,
            "cognitive_backfill_forbidden": True,
            "pending_unknown_actions": unknown_actions,
        },
        "accountability_partitions": {
            "tiers": list(ACCOUNTABILITY_TIERS),
            "evaporable_pool": {
                "active_count": len(active_evaporable),
                "items": active_evaporable,
            },
            "evaporation_guarantees": {
                "no_tombstone": True,
                "tombstone_count": 0,
                "audit_backlog_count": 0,
                "forced_summary_required": False,
            },
            "forbidden_in_evaporable": sorted(FORBIDDEN_EVAPORABLE_DOMAINS),
        },
        "meaningless_idle": {
            "allowed_kinds": sorted(MEANINGLESS_IDLE_KINDS.keys()),
            "active": idle_view,
            "rights": {
                "justification_required": False,
                "growth_score_delta": 0,
                "productivity_penalty": 0,
                "halfway_abandon_allowed": True,
                "forced_summary_forbidden": True,
            },
        },
        "life_dwell": dwell_proj,
        "subjective_time": dwell_proj["subjective_time"],
        "everyday_desires": desire_proj,
        "v02_sync": dict(state.get("v02_sync") or {}),
    }


def _envelope_ok(data: Mapping[str, Any], *, already_applied: bool = False) -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "status": "already_applied" if already_applied else "applied",
        "data": dict(data),
    }


def _envelope_err(exc: V03ClockError) -> tuple[int, dict[str, Any]]:
    return exc.status_code, {
        "ok": False,
        "status": exc.status_label,
        "reason_code": exc.reason_code,
        "data": {"llm_calls": 0, "model_called": False},
    }


def read_v03_clock_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Pure GET projection for ``/api/phone/v03/clock`` — 100% zero disk write."""
    try:
        state, _exists = read_v03_clock_state(day_root)
        data = project_v03_clock_data(
            day_root,
            state,
            has_subject_capability=bool(has_subject_capability),
            now_iso=now_iso,
        )
        return _envelope_ok(data)
    except V03ClockError as exc:
        return _envelope_err(exc)


def mutate_v03_clock_view(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """POST mutation entrypoint for ``/api/phone/v03/clock`` (zero LLM calls)."""
    try:
        if not isinstance(payload, Mapping):
            raise V03ClockValidationError("PAYLOAD_NOT_OBJECT")
        clean_actor = str(actor_side or "").strip().lower()
        if clean_actor not in ACTOR_SIDES:
            raise V03ClockPermissionError("ACTOR_SIDE_INVALID")
        has_subject_cap = clean_actor == "subject"

        forbidden_global = sorted(set(payload.keys()) & GLOBAL_FORBIDDEN_FIELDS)
        if forbidden_global:
            raise V03ClockConflictError(f"FORBIDDEN_FIELD:{forbidden_global[0]}")

        action = _clean_text(payload.get("action"), min_len=1, max_len=64, reason_code="ACTION_REQUIRED")
        allowed_fields = ACTION_ALLOWED_FIELDS.get(action)
        if allowed_fields is None:
            raise V03ClockValidationError(f"ACTION_INVALID:{action}")

        # Let accountability violations surface as 409 before generic unknown-field rejection on record_evaporable
        if action == "record_evaporable":
            forbidden_evap_keys = sorted(set(payload.keys()) & FORBIDDEN_EVAPORABLE_PAYLOAD_KEYS)
            if forbidden_evap_keys:
                raise V03ClockConflictError("EVAPORABLE_FORBIDDEN_FOR_ACCOUNTABLE_STATE")

        extra_fields = sorted(set(payload.keys()) - allowed_fields)
        if extra_fields:
            raise V03ClockValidationError(f"UNKNOWN_FIELD:{','.join(extra_fields)}")

        if action in SUBJECT_ONLY_ACTIONS and clean_actor != "subject":
            raise V03ClockPermissionError("SUBJECT_CAPABILITY_REQUIRED")

        now_dt, clean_now, _ = _resolve_now(now_iso)
        root = Path(day_root)
        state, _exists = read_v03_clock_state(root)

        op_key = payload.get("operation_key")
        clean_op_key: Optional[str] = None
        if op_key is not None:
            clean_op_key = _clean_id(op_key, reason_code="OPERATION_KEY_INVALID")
            intent_hash = hashlib.sha256(
                json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:24]
            existing_op = state["op_ledger"].get(clean_op_key)
            if isinstance(existing_op, Mapping):
                if existing_op.get("intent_hash") != intent_hash:
                    raise V03ClockConflictError("OPERATION_KEY_REUSED_WITH_DIFFERENT_INTENT")
                data = project_v03_clock_data(
                    root, state, has_subject_capability=has_subject_cap, now_iso=clean_now
                )
                data["action_receipt"] = dict(existing_op.get("receipt") or {})
                return _envelope_ok(data, already_applied=True)

        exp_rev = payload.get("expected_revision")
        if exp_rev is not None:
            if isinstance(exp_rev, bool) or not isinstance(exp_rev, int) or exp_rev < 0:
                raise V03ClockValidationError("EXPECTED_REVISION_INVALID")
            if int(exp_rev) != int(state["revision"]):
                raise V03ClockConflictError("REVISION_CONFLICT")

        wc = state["world_clock"]
        cc = state["cognitive_clock"]
        receipt: dict[str, Any] = {"action": action, "at": clean_now, "llm_calls": 0}

        if action == "advance_projection":
            sync_v02 = True if "sync_v02" not in payload else _strict_bool(payload["sync_v02"])
            prev_at = wc.get("last_committed_at")
            if isinstance(prev_at, str) and prev_at.strip():
                prev_dt = _parse_iso(prev_at)
                if now_dt < prev_dt:
                    raise V03ClockConflictError("WORLD_CLOCK_MONOTONIC_VIOLATION")
                elapsed = int((now_dt - prev_dt).total_seconds())
            else:
                elapsed = 0

            # 1. Silent TTL evaporation: zero tombstones, zero deleted_ids list, zero reflection queue
            before_evap = len(state["evaporable_pool"])
            surviving_evap: list[dict[str, Any]] = []
            for item in state["evaporable_pool"]:
                exp_dt = _parse_iso(str(item["expires_at"]))
                if now_dt < exp_dt:
                    surviving_evap.append(item)
            evaporated_count = before_evap - len(surviving_evap)
            state["evaporable_pool"] = surviving_evap

            # 2. Settle internal slow variables deterministically
            settled_count = 0
            remaining_slow_vars: list[dict[str, Any]] = []
            faded_missing_internal = 0
            for sv in wc["slow_variables"]:
                kind = str(sv.get("var_kind") or "")
                s_dt = _parse_iso(str(sv["started_at"]))
                age = max(0, int((now_dt - s_dt).total_seconds()))
                if kind == "damp_outerwear":
                    if sv.get("current_state") != "dry" and age >= OUTERWEAR_DRYING_SECONDS:
                        sv["current_state"] = "dry"
                        sv["settled_at"] = (s_dt + timedelta(seconds=OUTERWEAR_DRYING_SECONDS)).isoformat(
                            timespec="seconds"
                        )
                        settled_count += 1
                    remaining_slow_vars.append(sv)
                elif kind == "desk_drink":
                    new_temp = (
                        "warm"
                        if age < DRINK_TEPID_SECONDS
                        else ("tepid" if age < DRINK_COOLING_SECONDS else "room_temp")
                    )
                    if sv.get("current_state") != new_temp:
                        sv["current_state"] = new_temp
                        if new_temp == "room_temp":
                            settled_count += 1
                    remaining_slow_vars.append(sv)
                elif kind == "missing_note":
                    if age >= MISSING_NOTE_FADE_SECONDS:
                        faded_missing_internal += 1
                        settled_count += 1
                        # Naturally faded: drops cleanly with zero absence debt
                        continue
                    remaining_slow_vars.append(sv)
                else:
                    remaining_slow_vars.append(sv)
            wc["slow_variables"] = remaining_slow_vars

            # 3. Settle sibling slices (somatic outerwear >=6h, emotional missing notes >=72h)
            sibling_settled = _settle_sibling_slow_variables_on_advance(root, now_dt, clean_now)
            settled_count += (
                sibling_settled["somatic_outerwear_dried"]
                + sibling_settled["emotional_missing_faded"]
            )

            # 3.5 v3.0 Ch.02 slow-variable incubation kernel: drift -> ripe -> enqueue ->
            #     dispatch under cooldown/entropy/calm guards -> one-time v04 physical write
            slow_ledger_counters = _settle_slow_ledger(root, state, now_dt=now_dt, now_iso=clean_now)

            # 4. Iron Law 1: Discrete UNKNOWN actions stay strictly UNKNOWN (never guessed)
            for unk in wc["pending_unknown_actions"]:
                if unk.get("effect_state") == "UNKNOWN":
                    unk["guessed_by_clock"] = False

            # 5. Iron Law 2: CognitiveClock causal_ticks stays untouched (no fake backfill)
            cc["cognitive_backfill_forbidden"] = True

            # 6. Cross-day ambient date rotation (in +08:00 local business timezone)
            prev_date = wc.get("ambient_date")
            if prev_date is None and isinstance(prev_at, str) and prev_at.strip():
                prev_date = _to_local_business(_parse_iso(prev_at)).date().isoformat()
            new_date = _to_local_business(now_dt).date().isoformat()
            date_switched = bool(prev_date is not None and prev_date != new_date)
            wc["ambient_date"] = new_date
            wc["last_committed_at"] = clean_now

            host_gap_rec = (
                life_dwell.evaluate_host_gap(prev_at, clean_now)
                if isinstance(prev_at, str) and prev_at.strip()
                else None
            )
            if host_gap_rec is not None:
                try:
                    life_dwell.apply_life_dwell_action(
                        root,
                        {
                            "action": "record_host_gap",
                            "from_iso": prev_at,
                            "to_iso": clean_now,
                            "kind": "HOST_GAP",
                        },
                        now_iso=clean_now,
                        subject_view=has_subject_cap,
                    )
                except life_dwell.LifeDwellError as exc:
                    _raise_from_subengine_error(exc)

            gap_receipt = {
                "kind": life_dwell.HOST_GAP_KIND if host_gap_rec is not None else "SHORT_ADVANCE",
                "host_gap_recorded": host_gap_rec is not None,
                "honest_blank": True,
                "from_at": prev_at,
                "to_at": clean_now,
                "elapsed_seconds": elapsed,
                "settled_slow_vars": settled_count,
                "evaporated_count": evaporated_count,
                "faded_missing_notes": faded_missing_internal + sibling_settled["emotional_missing_faded"],
                "slow_ledger": dict(slow_ledger_counters),
                "ambient_date_switched": date_switched,
                "unknown_actions_guessed": 0,
                "cognitive_backfill_count": 0,
                "llm_calls": 0,
            }
            wc["gap_receipts"] = (list(wc.get("gap_receipts") or []) + [gap_receipt])[-MAX_GAP_RECEIPTS:]
            receipt.update(gap_receipt)

            if sync_v02:
                sync_report = sync_v02_slices_from_committed_world(root, clean_now)
                state["v02_sync"]["last_synced_at"] = clean_now
                state["v02_sync"]["room_furnishing_digest"] = sync_report["room_furnishing_digest"]
                state["v02_sync"]["delivered_share_count"] = sync_report["delivered_share_count"]
                state["v02_sync"]["album_count"] = sync_report["album_count"]
                if sync_report["reunion_baseline_updated"]:
                    state["v02_sync"]["reunion_baseline_seeded"] = True
                receipt["v02_sync"] = sync_report

        elif action in (
            "start_dwell",
            "end_dwell",
            "record_host_gap",
            "update_subjective_time",
            "trigger_everyday_glitch",
        ):
            sub_payload = {
                k: v for k, v in payload.items() if k not in ("expected_revision", "operation_key")
            }
            try:
                dwell_res = life_dwell.apply_life_dwell_action(
                    root,
                    sub_payload,
                    now_iso=clean_now,
                    subject_view=has_subject_cap,
                )
            except life_dwell.LifeDwellError as exc:
                _raise_from_subengine_error(exc)
            if action != "record_host_gap":
                cc["last_cognitive_at"] = clean_now
            receipt.update(dict(dwell_res.get("action_receipt") or {}))

        elif action in (
            "emerge_concern",
            "advance_desire_turn",
            "leave_desire_scene",
            "satisfy_concern",
            "drop_concern",
            "arbitrate_concerns",
        ):
            mapped_action = {
                "advance_desire_turn": "advance_turn",
                "leave_desire_scene": "leave_scene",
            }.get(action, action)
            sub_payload = {
                k: v for k, v in payload.items() if k not in ("expected_revision", "operation_key")
            }
            sub_payload["action"] = mapped_action
            try:
                desire_res = desire_lc.apply_everyday_desire_action(
                    root,
                    sub_payload,
                    now_iso=clean_now,
                    channel_visibility=str(
                        payload.get("channel_visibility")
                        or ("subject_private" if has_subject_cap else "public_or_shared")
                    ),
                    adult_gate_open=bool(payload.get("adult_gate_open", False)),
                )
            except desire_lc.EverydayDesireError as exc:
                _raise_from_subengine_error(exc)
            receipt.update(dict(desire_res.get("action_receipt") or {}))

        elif action == "record_evaporable":
            tier = str(payload.get("accountability_tier") or "EVAPORABLE").strip().upper()
            if tier != "EVAPORABLE":
                raise V03ClockConflictError("RECORD_EVAPORABLE_REQUIRES_EVAPORABLE_TIER")
            domain_kind = str(payload.get("domain_kind") or "fleeting_thought").strip().lower()
            classify_and_validate_accountability(
                tier="EVAPORABLE", domain_kind=domain_kind, payload=payload
            )
            text = _clean_text(payload.get("text"), min_len=1, max_len=MAX_TEXT_LEN, reason_code="EVAPORABLE_TEXT_INVALID")
            ttl = payload.get("ttl_seconds", DEFAULT_EVAPORABLE_TTL_SECONDS)
            if (
                isinstance(ttl, bool)
                or not isinstance(ttl, int)
                or not (MIN_EVAPORABLE_TTL_SECONDS <= ttl <= MAX_EVAPORABLE_TTL_SECONDS)
            ):
                raise V03ClockValidationError("TTL_SECONDS_INVALID")

            # Opportunistically drop already-expired items before checking pool capacity (no tombstones)
            state["evaporable_pool"] = [
                it for it in state["evaporable_pool"]
                if now_dt < _parse_iso(str(it["expires_at"]))
            ]
            if len(state["evaporable_pool"]) >= MAX_EVAPORABLE_POOL:
                raise V03ClockConflictError("EVAPORABLE_POOL_FULL")

            tid = (
                _clean_id(payload["thought_id"], reason_code="THOUGHT_ID_INVALID")
                if payload.get("thought_id") is not None
                else _stable_id("evap", domain_kind, text, clean_now, len(state["evaporable_pool"]))
            )
            expires_at = (now_dt + timedelta(seconds=int(ttl))).isoformat(timespec="seconds")
            entry = {
                "thought_id": tid,
                "domain_kind": domain_kind,
                "text": text,
                "created_at": clean_now,
                "ttl_seconds": int(ttl),
                "expires_at": expires_at,
                "no_tombstone": True,
                "reflection_required": False,
            }
            state["evaporable_pool"].append(entry)
            receipt.update({
                "thought_id": tid,
                "domain_kind": domain_kind,
                "expires_at": expires_at,
                "no_tombstone": True,
                "reflection_required": False,
            })

        elif action == "start_meaningless_idle":
            idle_kind = _clean_text(payload.get("idle_kind"), min_len=1, max_len=64, reason_code="IDLE_KIND_INVALID")
            if idle_kind not in MEANINGLESS_IDLE_KINDS:
                raise V03ClockValidationError("IDLE_KIND_INVALID")
            quiet_note = _optional_text(payload.get("quiet_note"), max_len=MAX_TEXT_LEN, reason_code="QUIET_NOTE_INVALID")
            idle_id = _stable_id("idle", idle_kind, clean_now, state["revision"] + 1)
            active_idle = {
                "idle_id": idle_id,
                "idle_kind": idle_kind,
                "started_at": clean_now,
                "quiet_note": quiet_note,
                "justification_required": False,
                "growth_score_delta": 0,
                "productivity_penalty": 0,
                "forced_summary_required": False,
            }
            cc["active_meaningless_idle"] = active_idle
            cc["causal_ticks"] = int(cc.get("causal_ticks") or 0) + 1
            cc["last_cognitive_at"] = clean_now
            receipt.update({
                "idle_id": idle_id,
                "idle_kind": idle_kind,
                "justification_required": False,
                "growth_score_delta": 0,
                "productivity_penalty": 0,
                "forced_summary_generated": False,
            })

        elif action == "end_meaningless_idle":
            active = cc.get("active_meaningless_idle")
            if not isinstance(active, Mapping):
                raise V03ClockConflictError("NO_ACTIVE_MEANINGLESS_IDLE")
            abandoned = (
                True
                if "abandoned_halfway" not in payload
                else _strict_bool(payload["abandoned_halfway"])
            )
            let_evap = (
                True
                if "let_evaporate" not in payload
                else _strict_bool(payload["let_evaporate"])
            )
            cc["active_meaningless_idle"] = None
            cc["causal_ticks"] = int(cc.get("causal_ticks") or 0) + 1
            cc["last_cognitive_at"] = clean_now

            evap_trace_id: Optional[str] = None
            if let_evap and payload.get("ttl_seconds") is not None:
                ttl = payload["ttl_seconds"]
                if (
                    isinstance(ttl, bool)
                    or not isinstance(ttl, int)
                    or not (MIN_EVAPORABLE_TTL_SECONDS <= ttl <= MAX_EVAPORABLE_TTL_SECONDS)
                ):
                    raise V03ClockValidationError("TTL_SECONDS_INVALID")
                if len(state["evaporable_pool"]) < MAX_EVAPORABLE_POOL:
                    evap_trace_id = _stable_id("evap-idle", active.get("idle_id"), clean_now)
                    state["evaporable_pool"].append({
                        "thought_id": evap_trace_id,
                        "domain_kind": "ordinary_linger",
                        "text": str(active.get("quiet_note") or MEANINGLESS_IDLE_KINDS[str(active["idle_kind"])]["label"]),
                        "created_at": clean_now,
                        "ttl_seconds": int(ttl),
                        "expires_at": (now_dt + timedelta(seconds=int(ttl))).isoformat(timespec="seconds"),
                        "no_tombstone": True,
                        "reflection_required": False,
                    })

            receipt.update({
                "ended_idle_id": active.get("idle_id"),
                "idle_kind": active.get("idle_kind"),
                "abandoned_halfway": abandoned,
                "justification_required": False,
                "growth_score_delta": 0,
                "productivity_penalty": 0,
                "forced_summary_generated": False,
                "reflection_queued": False,
                "evaporable_trace_id": evap_trace_id,
            })

        elif action == "declare_slow_variable":
            var_kind = _clean_text(payload.get("var_kind"), min_len=1, max_len=40, reason_code="SLOW_VAR_KIND_INVALID")
            if var_kind not in SLOW_VAR_KINDS:
                raise V03ClockValidationError("SLOW_VAR_KIND_INVALID")
            label = _clean_text(payload.get("label"), min_len=1, max_len=120, reason_code="SLOW_VAR_LABEL_INVALID")
            started_raw = payload.get("started_at")
            if started_raw is not None:
                s_dt = _parse_iso(started_raw, reason_code="STARTED_AT_INVALID")
                started_iso = s_dt.isoformat(timespec="seconds")
            else:
                started_iso = clean_now
            vid = (
                _clean_id(payload["var_id"], reason_code="VAR_ID_INVALID")
                if payload.get("var_id") is not None
                else _stable_id("svar", var_kind, label, started_iso)
            )
            default_init = {
                "damp_outerwear": "damp",
                "desk_drink": "warm",
                "missing_note": "active",
            }[var_kind]
            init_state = str(payload.get("initial_state") or default_init).strip()
            existing = next((x for x in wc["slow_variables"] if x.get("var_id") == vid), None)
            if existing is not None:
                existing.update({
                    "var_kind": var_kind,
                    "label": label,
                    "started_at": started_iso,
                    "initial_state": init_state,
                    "current_state": init_state,
                })
            else:
                if len(wc["slow_variables"]) >= MAX_SLOW_VARS:
                    raise V03ClockConflictError("SLOW_VARIABLES_FULL")
                wc["slow_variables"].append({
                    "var_id": vid,
                    "var_kind": var_kind,
                    "label": label,
                    "started_at": started_iso,
                    "initial_state": init_state,
                    "current_state": init_state,
                })
            if wc.get("last_committed_at") is None:
                wc["last_committed_at"] = clean_now
                wc["ambient_date"] = _to_local_business(now_dt).date().isoformat()
            receipt.update({"var_id": vid, "var_kind": var_kind, "started_at": started_iso})

        elif action == "declare_eco_variable":
            ledger = _slow_ledger_of(state)
            var_name = _clean_id(payload.get("var_name"), reason_code="SLOW_VAR_NAME_INVALID")
            label = _clean_text(payload.get("label") or var_name, min_len=1, max_len=120, reason_code="SLOW_VAR_LABEL_INVALID")
            drift_raw = payload.get("drift_milli_per_hour", 0)
            if isinstance(drift_raw, bool) or not isinstance(drift_raw, int) or abs(drift_raw) > ECO_DRIFT_MILLI_PER_HOUR_LIMIT:
                raise V03ClockValidationError("ECO_DRIFT_INVALID")
            volatility_raw = payload.get("volatility_milli", 0)
            if isinstance(volatility_raw, bool) or not isinstance(volatility_raw, int) or not (0 <= volatility_raw <= ECO_VALUE_MILLI_MAX):
                raise V03ClockValidationError("ECO_VOLATILITY_INVALID")
            threshold_raw = payload.get("manifest_threshold_milli", RIPE_THRESHOLD_MILLI_DEFAULT)
            if isinstance(threshold_raw, bool) or not isinstance(threshold_raw, int) or not (0 <= threshold_raw <= ECO_VALUE_MILLI_MAX):
                raise V03ClockValidationError("ECO_THRESHOLD_INVALID")
            cooldown_family = _clean_text(payload.get("cooldown_family"), min_len=1, max_len=40, reason_code="COOLDOWN_FAMILY_INVALID")
            if cooldown_family not in COOLDOWN_FAMILIES:
                raise V03ClockValidationError("COOLDOWN_FAMILY_INVALID")
            perception_tier = _clean_text(payload.get("perception_tier"), min_len=1, max_len=16, reason_code="PERCEPTION_TIER_INVALID")
            if perception_tier not in PERCEPTION_TIERS:
                raise V03ClockValidationError("PERCEPTION_TIER_INVALID")
            entropy_major = False if "entropy_major" not in payload else _strict_bool(payload["entropy_major"], reason_code="BOOLEAN_INVALID")
            manifest_scene = _optional_text(payload.get("manifest_scene"), max_len=120, reason_code="SLOW_VAR_SCENE_INVALID")
            # Ch.02 §1.3 manifestation contract binds to a POI: the kernel's binds
            # field is the target POI id, closed-validated against the v04
            # neighborhood blueprint (a place that exists in the v04 owner).
            # Undeclared binds keeps the bookstore-compatible default at dispatch.
            binds = _optional_text(payload.get("binds"), max_len=MAX_ID_LEN, reason_code="SLOW_VAR_BINDS_INVALID")
            if binds is not None and binds not in _v04_place_ids():
                raise V03ClockValidationError("SLOW_VAR_BINDS_INVALID")
            var_class = _optional_text(payload.get("var_class"), max_len=40, reason_code="SLOW_VAR_CLASS_INVALID")
            carriers_raw = payload.get("carriers") or []
            if not isinstance(carriers_raw, (list, tuple)) or len(carriers_raw) > MAX_ECO_CARRIERS:
                raise V03ClockValidationError("ECO_CARRIERS_INVALID")
            carriers = [
                _clean_text(c, min_len=1, max_len=MAX_ID_LEN, reason_code="ECO_CARRIERS_INVALID")
                for c in carriers_raw
            ]
            started_raw = payload.get("started_at")
            if started_raw is not None:
                s_dt = _parse_iso(started_raw, reason_code="STARTED_AT_INVALID")
                started_iso = s_dt.isoformat(timespec="seconds")
            else:
                started_iso = clean_now

            seed_raw = payload.get("world_seed")
            if seed_raw is not None:
                if isinstance(seed_raw, bool) or not isinstance(seed_raw, int):
                    raise V03ClockValidationError("WORLD_SEED_INVALID")
                if ledger.get("world_seed") is None:
                    ledger["world_seed"] = int(seed_raw)
                elif int(seed_raw) != int(ledger["world_seed"]):
                    raise V03ClockConflictError("SLOW_LEDGER_SEED_IMMUTABLE")
            world_seed = int(ledger.get("world_seed") or DEFAULT_WORLD_SEED)

            vid = (
                _clean_id(payload["var_id"], reason_code="VAR_ID_INVALID")
                if payload.get("var_id") is not None
                else _stable_id("eco", var_name, started_iso)
            )
            existing = next((x for x in ledger["eco_variables"] if x.get("var_id") == vid), None)
            if existing is not None:
                # Seed-derived value/phase are immutable; only the declared slope/metadata may be revised.
                existing.update({
                    "label": label,
                    "var_class": var_class,
                    "carriers": carriers,
                    "drift_milli_per_hour": int(drift_raw),
                    "volatility_milli": int(volatility_raw),
                    "declared_volatility_milli": int(volatility_raw),
                    "manifest_threshold_milli": int(threshold_raw),
                    "cooldown_family": cooldown_family,
                    "entropy_major": entropy_major,
                    "perception_tier": perception_tier,
                    "manifest_scene": manifest_scene,
                    "binds": binds,
                })
                receipt.update({"var_id": vid, "var_name": var_name, "phase": existing.get("phase"), "redeclared": True})
            else:
                if len(ledger["eco_variables"]) >= MAX_ECO_VARIABLES:
                    raise V03ClockConflictError("SLOW_LEDGER_FULL")
                active_count = sum(
                    1 for x in ledger["eco_variables"] if (x.get("phase") or "incubating") in ACTIVE_ECO_PHASES
                )
                if active_count >= MAX_ACTIVE_ECO_VARIABLES:
                    raise V03ClockConflictError("SLOW_LEDGER_ACTIVE_BUDGET_EXCEEDED")
                ledger["eco_variables"].append({
                    "var_id": vid,
                    "var_name": var_name,
                    "var_class": var_class,
                    "label": label,
                    "carriers": carriers,
                    "value_milli": _derive_eco_initial_milli(world_seed, var_name),
                    "drift_milli_per_hour": int(drift_raw),
                    "volatility_milli": int(volatility_raw),
                    "declared_volatility_milli": int(volatility_raw),
                    "witnessed_contact_count": 0,
                    "manifest_threshold_milli": int(threshold_raw),
                    "cooldown_family": cooldown_family,
                    "entropy_major": entropy_major,
                    "perception_tier": perception_tier,
                    "manifest_scene": manifest_scene,
                    "binds": binds,
                    "phase": "incubating",
                    "started_at": started_iso,
                    "last_settled_at": started_iso,
                    "rip_at": None,
                    "manifestation_id": None,
                    "enqueue_hold_reason": None,
                    "enqueue_hold_until": None,
                    "contract_dropped": False,
                    "settled_at": None,
                    "manifested_at": None,
                    "folded": False,
                })
                receipt.update({
                    "var_id": vid,
                    "var_name": var_name,
                    "phase": "incubating",
                    "world_seed": world_seed,
                    "seed_derived": True,
                })
            if wc.get("last_committed_at") is None:
                wc["last_committed_at"] = clean_now
                wc["ambient_date"] = _to_local_business(now_dt).date().isoformat()

        elif action == "add_causal_edge":
            ledger = _slow_ledger_of(state)
            from_var = _clean_text(payload.get("from_var"), min_len=1, max_len=MAX_ID_LEN, reason_code="CAUSAL_ENDPOINT_INVALID")
            to_var = _clean_text(payload.get("to_var"), min_len=1, max_len=MAX_ID_LEN, reason_code="CAUSAL_ENDPOINT_INVALID")
            grade = _clean_text(payload.get("grade"), min_len=1, max_len=16, reason_code="CAUSAL_GRADE_INVALID")
            if grade not in CAUSAL_EDGE_GRADES:
                raise V03ClockValidationError("CAUSAL_GRADE_INVALID")
            lag_raw = payload.get("lag_seconds")
            if lag_raw is None:
                lag_seconds = None
            elif isinstance(lag_raw, bool) or not isinstance(lag_raw, int) or lag_raw < 0:
                raise V03ClockValidationError("CAUSAL_LAG_INVALID")
            else:
                lag_seconds = int(lag_raw)
            lag_max_raw = payload.get("lag_max_seconds")
            if lag_max_raw is None:
                lag_max_seconds = None
            elif isinstance(lag_max_raw, bool) or not isinstance(lag_max_raw, int) or lag_max_raw < 0:
                raise V03ClockValidationError("CAUSAL_LAG_INVALID")
            else:
                lag_max_seconds = int(lag_max_raw)
            if lag_seconds is not None and lag_max_seconds is not None and lag_max_seconds < lag_seconds:
                raise V03ClockValidationError("CAUSAL_LAG_INVERTED")
            if lag_seconds is None and lag_max_seconds is not None:
                raise V03ClockValidationError("CAUSAL_LAG_INVALID")
            coupling = _optional_text(payload.get("coupling"), max_len=80, reason_code="CAUSAL_COUPLING_INVALID")
            condition = _optional_text(payload.get("condition"), max_len=160, reason_code="CAUSAL_CONDITION_INVALID")
            refs_raw = payload.get("source_refs") or []
            if not isinstance(refs_raw, (list, tuple)) or len(refs_raw) > MAX_EDGE_SOURCE_REFS:
                raise V03ClockValidationError("CAUSAL_SOURCE_REFS_INVALID")
            source_refs = [
                _clean_text(r, min_len=1, max_len=MAX_ID_LEN, reason_code="CAUSAL_SOURCE_REFS_INVALID")
                for r in refs_raw
            ]
            if grade == "inferred" and not source_refs:
                # Ch.02 §1.2: an inferred edge without its inference basis is fabrication — refuse.
                raise V03ClockConflictError("CAUSAL_INFERRED_REQUIRES_SOURCE_REFS")

            # --- Ch.02 §1.2 dynamics-coupling payload (grade-gated, bounded) ---
            pulse_raw = payload.get("pulse_milli")
            drift_lo_raw = payload.get("drift_band_min_milli_per_hour")
            drift_hi_raw = payload.get("drift_band_max_milli_per_hour")
            vol_lo_raw = payload.get("volatility_band_min_milli")
            vol_hi_raw = payload.get("volatility_band_max_milli")

            def _int_field(value: Any, reason_code: str) -> int:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise V03ClockValidationError(reason_code)
                return int(value)

            drift_band: Optional[tuple[int, int]] = None
            if drift_lo_raw is not None or drift_hi_raw is not None:
                if drift_lo_raw is None or drift_hi_raw is None:
                    raise V03ClockValidationError("CAUSAL_DRIFT_BAND_INVALID")
                lo, hi = _int_field(drift_lo_raw, "CAUSAL_DRIFT_BAND_INVALID"), _int_field(drift_hi_raw, "CAUSAL_DRIFT_BAND_INVALID")
                if not (-ECO_DRIFT_MILLI_PER_HOUR_LIMIT <= lo <= ECO_DRIFT_MILLI_PER_HOUR_LIMIT):
                    raise V03ClockValidationError("CAUSAL_DRIFT_BAND_INVALID")
                if not (-ECO_DRIFT_MILLI_PER_HOUR_LIMIT <= hi <= ECO_DRIFT_MILLI_PER_HOUR_LIMIT):
                    raise V03ClockValidationError("CAUSAL_DRIFT_BAND_INVALID")
                if lo > hi:
                    raise V03ClockValidationError("CAUSAL_DRIFT_BAND_INVERTED")
                drift_band = (lo, hi)
            volatility_band: Optional[tuple[int, int]] = None
            if vol_lo_raw is not None or vol_hi_raw is not None:
                if vol_lo_raw is None or vol_hi_raw is None:
                    raise V03ClockValidationError("CAUSAL_VOLATILITY_BAND_INVALID")
                lo, hi = _int_field(vol_lo_raw, "CAUSAL_VOLATILITY_BAND_INVALID"), _int_field(vol_hi_raw, "CAUSAL_VOLATILITY_BAND_INVALID")
                if not (0 <= lo <= ECO_VALUE_MILLI_MAX) or not (0 <= hi <= ECO_VALUE_MILLI_MAX):
                    raise V03ClockValidationError("CAUSAL_VOLATILITY_BAND_INVALID")
                if lo > hi:
                    raise V03ClockValidationError("CAUSAL_VOLATILITY_BAND_INVERTED")
                volatility_band = (lo, hi)
            pulse_milli: Optional[int] = None
            if pulse_raw is not None:
                pulse_milli = _int_field(pulse_raw, "WITNESSED_PULSE_INVALID")
                if abs(pulse_milli) > WITNESSED_PULSE_MILLI_LIMIT:
                    raise V03ClockValidationError("WITNESSED_PULSE_INVALID")
            if grade == "inferred" and (pulse_milli is not None or drift_band is not None or volatility_band is not None):
                # Ch.02 §1.2: "只进 S03/S05，绝不允许回写 S01 世界账本" — inference
                # may never carry world-side dynamics, not even a zero-width band.
                raise V03ClockConflictError("CAUSAL_INFERRED_NO_DYNAMICS")
            if pulse_milli is not None and grade != "witnessed":
                raise V03ClockConflictError("CAUSAL_PULSE_GRADE_MISMATCH")
            if (drift_band is not None or volatility_band is not None) and grade != "rule":
                raise V03ClockConflictError("CAUSAL_BAND_GRADE_MISMATCH")

            edge_id = (
                _clean_id(payload["edge_id"], reason_code="EDGE_ID_INVALID")
                if payload.get("edge_id") is not None
                else _stable_id("ce", from_var, to_var, grade, clean_now)
            )
            edge_doc = {
                "edge_id": edge_id,
                "from_var": from_var,
                "to_var": to_var,
                "grade": grade,
                "lag_seconds": lag_seconds,
                "lag_max_seconds": lag_max_seconds,
                "coupling": coupling,
                "condition": condition,
                "source_refs": source_refs,
                "drift_band_min_milli_per_hour": drift_band[0] if drift_band else None,
                "drift_band_max_milli_per_hour": drift_band[1] if drift_band else None,
                "volatility_band_min_milli": volatility_band[0] if volatility_band else None,
                "volatility_band_max_milli": volatility_band[1] if volatility_band else None,
                "pulse_milli": pulse_milli,
                "created_at": clean_now,
            }
            existing_edge = next((e for e in ledger["causal_edges"] if e.get("edge_id") == edge_id), None)
            if existing_edge is not None:
                # Append-only registry: identical replay is a no-op; any content change is a conflict.
                if {k: existing_edge.get(k) for k in edge_doc} != edge_doc:
                    raise V03ClockConflictError("CAUSAL_EDGE_IMMUTABLE")
                receipt.update({"edge_id": edge_id, "already_registered": True})
            else:
                if len(ledger["causal_edges"]) >= MAX_CAUSAL_EDGES:
                    raise V03ClockConflictError("CAUSAL_EDGE_REGISTRY_FULL")

                # Dynamics write-effects need a real, still-active ledger variable
                # (matched by var_id); the registry itself stays free-text.
                target_var = next(
                    (v for v in ledger.get("eco_variables") or [] if isinstance(v, Mapping) and str(v.get("var_id") or "") == to_var),
                    None,
                )
                if pulse_milli is not None:
                    if target_var is None:
                        raise V03ClockConflictError("CAUSAL_TARGET_VARIABLE_MISSING")
                    if str(target_var.get("phase") or "") not in ACTIVE_ECO_PHASES:
                        # settled variables are frozen into the static world layer
                        raise V03ClockConflictError("CAUSAL_TARGET_FROZEN")
                if (drift_band is not None or volatility_band is not None) and target_var is None:
                    raise V03ClockConflictError("CAUSAL_TARGET_VARIABLE_MISSING")

                ledger["causal_edges"].append(edge_doc)
                effects: list[dict[str, Any]] = []
                if grade == "witnessed" and target_var is not None and str(target_var.get("phase") or "") in ACTIVE_ECO_PHASES:
                    # First-hand contact: accumulate witnessed contact; each
                    # accumulated threshold narrows volatility one closed tier
                    # (§1.1 "volatility: 噪声带宽（千代接触时收窄）").
                    target_var["witnessed_contact_count"] = int(target_var.get("witnessed_contact_count") or 0) + 1
                    contact_count = int(target_var["witnessed_contact_count"])
                    if contact_count % WITNESS_NARROW_THRESHOLD == 0:
                        vol_before = int(target_var.get("volatility_milli") or 0)
                        vol_after = _narrow_volatility_one_tier(vol_before)
                        if vol_after != vol_before:
                            target_var["volatility_milli"] = vol_after
                            effects.append({
                                "kind": "volatility_narrow",
                                "from_milli": vol_before,
                                "to_milli": vol_after,
                                "witnessed_contact_count": contact_count,
                            })
                    if pulse_milli is not None:
                        value_before = int(target_var.get("value_milli") or 0)
                        value_after = min(ECO_VALUE_MILLI_MAX, max(0, value_before + pulse_milli))
                        target_var["value_milli"] = value_after
                        effects.append({
                            "kind": "drift_pulse",
                            "delta_milli": pulse_milli,
                            "value_before": value_before,
                            "value_after": value_after,
                        })
                if effects:
                    _record_edge_effects(
                        ledger, edge_id=edge_id, grade=grade, var_id=to_var,
                        effects=effects, now_iso=clean_now,
                    )
                receipt.update({
                    "edge_id": edge_id,
                    "grade": grade,
                    "already_registered": False,
                    "effects_applied": effects,
                })

        elif action == "drop_manifestation":
            ledger = _slow_ledger_of(state)
            manifestation_id = _clean_id(payload.get("manifestation_id"), reason_code="MANIFESTATION_ID_INVALID")
            drop_reason = _optional_text(payload.get("drop_reason"), max_len=120, reason_code="DROP_REASON_INVALID") or "dropped_by_explicit_request"
            queue: list[dict[str, Any]] = ledger.setdefault("manifestation_queue", [])
            entry = next((e for e in queue if e.get("manifestation_id") == manifestation_id), None)
            if entry is None:
                raise V03ClockNotFoundError("MANIFESTATION_NOT_FOUND")
            if entry.get("status") != "queued":
                raise V03ClockConflictError("MANIFESTATION_NOT_QUEUED")
            entry["status"] = "dropped"
            entry["drop_reason"] = drop_reason
            var = next(
                (v for v in ledger.get("eco_variables") or [] if isinstance(v, Mapping) and v.get("var_id") == entry.get("var_id")),
                None,
            )
            if var is not None:
                # One-time contract consumed: the variable stays ripe and never auto-re-enqueues.
                var["contract_dropped"] = True
            receipt.update({"manifestation_id": manifestation_id, "status": "dropped", "drop_reason": drop_reason})

        elif action == "register_unknown_action":
            aid = (
                _clean_id(payload["action_id"], reason_code="ACTION_ID_INVALID")
                if payload.get("action_id") is not None
                else _stable_id("unk", payload.get("domain"), clean_now, state["revision"] + 1)
            )
            domain = _clean_text(payload.get("domain") or "world", min_len=1, max_len=80, reason_code="DOMAIN_INVALID")
            summary = _clean_text(payload.get("summary") or "未决离散动作", min_len=1, max_len=160, reason_code="SUMMARY_INVALID")
            sub_at = (
                _parse_iso(payload["submitted_at"]).isoformat(timespec="seconds")
                if payload.get("submitted_at") is not None
                else clean_now
            )
            existing = next((u for u in wc["pending_unknown_actions"] if u.get("action_id") == aid), None)
            if existing is None:
                if len(wc["pending_unknown_actions"]) >= MAX_UNKNOWN_ACTIONS:
                    raise V03ClockConflictError("UNKNOWN_ACTIONS_FULL")
                wc["pending_unknown_actions"].append({
                    "action_id": aid,
                    "domain": domain,
                    "summary": summary,
                    "submitted_at": sub_at,
                    "effect_state": "UNKNOWN",
                    "guessed_by_clock": False,
                })
            receipt.update({"action_id": aid, "effect_state": "UNKNOWN", "guessed_by_clock": False})

        elif action == "resolve_unknown_action":
            aid = _clean_id(payload.get("action_id"), reason_code="ACTION_ID_INVALID")
            outcome = _clean_text(payload.get("resolved_outcome"), min_len=1, max_len=32, reason_code="OUTCOME_INVALID").upper()
            if outcome not in ("APPLIED", "FAILED", "REFUSED"):
                raise V03ClockValidationError("OUTCOME_INVALID")
            receipt_ref = _clean_text(payload.get("receipt_ref"), min_len=1, max_len=128, reason_code="RECEIPT_REF_REQUIRED")
            found = next((u for u in wc["pending_unknown_actions"] if u.get("action_id") == aid), None)
            if found is None:
                raise V03ClockNotFoundError("UNKNOWN_ACTION_NOT_FOUND")
            wc["pending_unknown_actions"] = [
                u for u in wc["pending_unknown_actions"] if u.get("action_id") != aid
            ]
            receipt.update({
                "action_id": aid,
                "resolved_outcome": outcome,
                "receipt_ref": receipt_ref,
                "resolved_by_explicit_receipt": True,
            })

        elif action == "sync_v02_slices":
            refresh_base = (
                False
                if "refresh_reunion_baseline" not in payload
                else _strict_bool(payload["refresh_reunion_baseline"])
            )
            sync_report = sync_v02_slices_from_committed_world(
                root, clean_now, refresh_reunion_baseline=refresh_base
            )
            state["v02_sync"]["last_synced_at"] = clean_now
            state["v02_sync"]["room_furnishing_digest"] = sync_report["room_furnishing_digest"]
            state["v02_sync"]["delivered_share_count"] = sync_report["delivered_share_count"]
            state["v02_sync"]["album_count"] = sync_report["album_count"]
            if sync_report["reunion_baseline_updated"]:
                state["v02_sync"]["reunion_baseline_seeded"] = True
            receipt["v02_sync"] = sync_report

        state["revision"] = int(state["revision"]) + 1
        state["updated_at"] = clean_now
        if clean_op_key is not None:
            intent_hash = hashlib.sha256(
                json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:24]
            state["op_ledger"][clean_op_key] = {
                "intent_hash": intent_hash,
                "receipt": dict(receipt),
                "at": clean_now,
            }
            if len(state["op_ledger"]) > MAX_OP_LEDGER:
                oldest = list(state["op_ledger"].keys())[0]
                state["op_ledger"].pop(oldest, None)

        saved = write_v03_clock_state(root, state)
        data = project_v03_clock_data(
            root, saved, has_subject_capability=has_subject_cap, now_iso=clean_now
        )
        data["action_receipt"] = receipt
        return _envelope_ok(data)
    except V03ClockError as exc:
        return _envelope_err(exc)


def apply_v03_clock_action(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper returning the JSON envelope dict from ``mutate_v03_clock_view``."""
    _status, body = mutate_v03_clock_view(
        day_root, payload, actor_side=actor_side, now_iso=now_iso
    )
    return body


__all__ = [
    "ACCOUNTABILITY_TIERS",
    "ACTION_ALLOWED_FIELDS",
    "CALM_PERIOD_DAYS",
    "CAUSAL_EDGE_GRADES",
    "COOLDOWN_FAMILIES",
    "COOLDOWN_FAMILY_SECONDS",
    "ECO_PHASES",
    "ENTROPY_BUDGET_MONTHLY_MAJOR",
    "FORBIDDEN_EVAPORABLE_DOMAINS",
    "MANIFESTATION_STATUSES",
    "MAX_ACTIVE_ECO_VARIABLES",
    "MEANINGLESS_IDLE_KINDS",
    "MISSING_NOTE_FADE_SECONDS",
    "OUTERWEAR_DRYING_SECONDS",
    "PERCEPTION_TIERS",
    "RESIDUE_FOLD_SECONDS",
    "RULEPACK_ID",
    "V03ClockConflictError",
    "V03ClockError",
    "V03ClockNotFoundError",
    "V03ClockPermissionError",
    "V03ClockUnavailableError",
    "V03ClockValidationError",
    "V03_CLOCK_FILENAME",
    "V03_CLOCK_STATE_SCHEMA",
    "V03_CLOCK_VIEW_SCHEMA",
    "WORLD_FORMULA",
    "apply_v03_clock_action",
    "classify_and_validate_accountability",
    "default_slow_ledger",
    "default_v03_clock_state",
    "mutate_v03_clock_view",
    "project_v03_clock_data",
    "read_v03_clock_state",
    "read_v03_clock_view",
    "sync_v02_slices_from_committed_world",
    "v03_clock_state_path",
    "write_v03_clock_state",
]

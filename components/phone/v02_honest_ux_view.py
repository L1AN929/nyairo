#!/usr/bin/env python3
"""HTTP view & action gate for ``/api/phone/v02/honest-ux`` (v0.2 Honest UX & Partner Boundary).

Contracts:
1. ``read_v02_honest_ux_view(day_root, has_subject_capability=..., now_iso=...)``:
   - 100% zero-write on fresh, non-existent, or populated ``day_root``.
   - Aggregates real Wave-1 states: provenance, chatless_traces (objects/works/bookmarks/waits),
     md_conflicts, forget_cases (zero original_text/matched_quote/secret echo),
     rescaled_copractices, periods, scraps, personal_tools, skill_attributions,
     budget_survival, and crash_reconciler_summary.
   - When ``has_subject_capability=False`` (partner view): strips private drafts and
     unshared periods/scraps/personal tools unless ``shared=True`` / ``shared_with_partner=True``;
     filters held/tombstoned items via the Forget barrier.
2. ``mutate_v02_honest_ux_view(day_root, payload, actor_side=..., now_iso=...)``:
   - Partner actions (enforcing ``actor_side="partner"`` when partner door, idempotent on
     ``operation_key``): ``mark_representation_divergence_seen``, ``request_forget_scope_hold``,
     ``clarify_forget_scope``, ``confirm_rescaled_copractice``.
   - Subject-only actions: ``explicit_import_representation``, ``approve_pending_forget_hold``,
     ``record_period_snapshot``, ``salvage_work_scrap``, ``simplify_personal_tool`` -> MUST
     return ``403 FORBIDDEN_PARTNER_ACTION`` when ``actor_side == "partner"``.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _entry in (str(REPO_ROOT), str(_LIFE_SCRIPTS), str(_WORLD_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import (  # noqa: E402
    state_tx_guard as _tx_guard,
    v02_autobiography_skill_provenance as _asp,
    v02_lived_slices_and_vd_protocols as _lived_slices,
    v02_representation_lease_forget as _rlf,
)
from components.world.scripts import (  # noqa: E402
    instance_paths as _instance_paths,
    v08_open_affordance as _v08_open,
    v08_universal_matter as _v08_matter,
)

VIEW_SCHEMA = "nyairo.v02.honest_ux.v1"
ENDPOINT_PATH = "/api/phone/v02/honest-ux"
STATE_REL_PATH = ("phone", "v02_honest_ux_state.json")
DEFAULT_TZ = timezone(timedelta(hours=8))

PARTNER_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "mark_representation_divergence_seen",
    "request_forget_scope_hold",
    "clarify_forget_scope",
    "confirm_rescaled_copractice",
    "propose_rescaled_copractice",
})

SUBJECT_ONLY_ACTIONS: frozenset[str] = frozenset({
    "explicit_import_representation",
    "approve_pending_forget_hold",
    "record_period_snapshot",
    "salvage_work_scrap",
    "simplify_personal_tool",
})

ALL_ALLOWED_ACTIONS: frozenset[str] = PARTNER_ALLOWED_ACTIONS | SUBJECT_ONLY_ACTIONS

ALLOWED_SCALE_MODES: frozenset[str] = frozenset({
    "light_relay",
    "co_presence",
    "async_checkin",
    "pause_without_debt",
    "paused_no_debt",
    "quiet_checkin",
})

_SCALE_MODE_ALIASES: dict[str, str] = {
    "paused_no_debt": "pause_without_debt",
}

ALLOWED_TARGET_DOMAINS: frozenset[str] = frozenset({
    "episode",
    "period",
    "draft",
    "conversation",
    "all_derived",
    "unsure",
})

ALLOWED_FORGET_SCOPE_CHOICES: frozenset[str] = frozenset({
    "last_turn",
    "single_turn",
    "current_topic",
    "whole_topic",
    "session_segment",
    "time_window",
    "all_related_derivations",
    "cancel_hold",
})

ALLOWED_REP_STATES: frozenset[str] = frozenset({
    "CURRENT",
    "MISSING",
    "DIVERGED",
    "UNBOUND",
})

_SECRET_FORGET_KEYS: frozenset[str] = frozenset({
    "original_text",
    "matched_quote",
    "raw_secret_text",
    "secret_text",
    "deleted_excerpt",
    "tombstone_plaintext",
    "command_text",
    "private_drafts",
    "internal_lease_token",
    "lease_token",
    "unshared_periods",
    "unshared_scraps",
    "private_periods",
    "private_scraps",
    "sensitive_content",
})

_SCALE_MODE_TO_CADENCE: dict[str, str] = {
    "light_relay": "light_relay",
    "co_presence": "co_presence",
    "async_checkin": "light_relay",
    "pause_without_debt": "paused_no_debt",
    "paused_no_debt": "paused_no_debt",
    "quiet_checkin": "co_presence",
}

_MATTER_SPACE_REL: tuple[str, ...] = ("world", "v08_matter_space.json")
_RESCALED_CADENCE_TOKENS: tuple[str, ...] = (
    "rescaled",
    "light_relay",
    "gentle",
    "co_presence",
    "async_checkin",
    "pause_without_debt",
    "paused_no_debt",
    "quiet_checkin",
)
_LEGACY_POWER_MODES: frozenset[str] = frozenset({"LOW_BUDGET", "DORMANT"})


def _normalize_scale_mode(raw: Any, *, default: str = "light_relay") -> str:
    val = str(raw or "").strip().lower()
    val = _SCALE_MODE_ALIASES.get(val, val)
    return val if val in ALLOWED_SCALE_MODES else default


def _now_iso(now_iso: Optional[str] = None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        return now_iso.strip()
    return datetime.now(tz=DEFAULT_TZ).isoformat(timespec="seconds")


def _state_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*STATE_REL_PATH)


def _lock_path(day_root: Path | str) -> Path:
    return Path(day_root) / "phone" / ".v02_honest_ux_state.lock"


def _read_json_checked_zero_write(
    path: Path,
    corrupt_domains: Optional[list[str]] = None,
    domain_name: str = "",
) -> Optional[dict[str, Any]]:
    """Read a JSON dict without writes; record ``domain_name`` if present on disk but corrupt."""
    try:
        exists_on_disk = path.is_symlink() or os.path.lexists(path)
    except OSError:
        if corrupt_domains is not None and domain_name:
            corrupt_domains.append(domain_name)
        return None
    if not exists_on_disk:
        return None
    try:
        if path.is_symlink() or not path.is_file():
            if corrupt_domains is not None and domain_name:
                corrupt_domains.append(domain_name)
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        if corrupt_domains is not None and domain_name:
            corrupt_domains.append(domain_name)
        return None
    if not isinstance(parsed, Mapping):
        if corrupt_domains is not None and domain_name:
            corrupt_domains.append(domain_name)
        return None
    return dict(parsed)


def _read_json_zero_write(path: Path) -> Optional[dict[str, Any]]:
    return _read_json_checked_zero_write(path)


def _empty_honest_state() -> dict[str, Any]:
    return {
        "schema": VIEW_SCHEMA,
        "revision": 0,
        "updated_at": None,
        "divergence_seen": {},
        "forget_case_meta": {},
        "proposed_rescaled_copractices": {},
        "operation_ledger": {},
    }


def _load_honest_state_readonly(
    day_root: Path | str,
    *,
    corrupt_domains: Optional[list[str]] = None,
) -> dict[str, Any]:
    doc = _read_json_checked_zero_write(
        _state_path(day_root),
        corrupt_domains=corrupt_domains,
        domain_name="honest_ux_state",
    )
    base = _empty_honest_state()
    if isinstance(doc, Mapping) and doc.get("schema") == VIEW_SCHEMA:
        base.update(copy.deepcopy(dict(doc)))
    for dkey in (
        "divergence_seen",
        "forget_case_meta",
        "proposed_rescaled_copractices",
        "operation_ledger",
        "pending_ops",
    ):
        if dkey in base and not isinstance(base.get(dkey), dict):
            base[dkey] = {}
            if corrupt_domains is not None:
                corrupt_domains.append("honest_ux_state")
    base.setdefault("divergence_seen", {})
    base.setdefault("forget_case_meta", {})
    base.setdefault("proposed_rescaled_copractices", {})
    base.setdefault("operation_ledger", {})
    return base


def _error_response(
    status_code: int,
    reason_code: str,
    message: str,
    *,
    extra: Optional[Mapping[str, Any]] = None,
) -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = {
        "ok": False,
        "status": "rejected",
        "reason": reason_code,
        "reason_code": reason_code,
        "error": reason_code,
        "error_code": reason_code,
        "message": message,
        "llm_calls": 0,
    }
    if extra:
        for k, v in extra.items():
            if k not in body:
                body[k] = v
    return int(status_code), body


def _clean_safe_id(raw: Any, *, max_len: int = 96) -> tuple[str, bool, bool]:
    if raw is None:
        return "", False, False
    if not isinstance(raw, str):
        return "", True, False
    cleaned = raw.strip()[:max_len]
    if not cleaned:
        return "", False, False
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned or "\x00" in cleaned:
        return "", True, False
    return cleaned, True, True


def _clean_required_str(raw: Any, *, min_len: int = 1, max_len: int = 128) -> tuple[Optional[str], bool]:
    if raw is None:
        return None, False
    if not isinstance(raw, str):
        return None, True
    cleaned = raw.strip()
    if len(cleaned) < min_len:
        return None, False
    return cleaned[:max_len], True


def _item_is_shared(item: Mapping[str, Any]) -> bool:
    if item.get("private") is True or item.get("unshared") is True:
        return False
    vis = str(item.get("visibility") or "").strip().lower()
    if vis == "private":
        return False
    if item.get("shared") is True or item.get("shared_with_partner") is True:
        return True
    if vis == "shared":
        return True
    return False


def _matter_object_shared(obj: Mapping[str, Any]) -> bool:
    """Partner visibility for a room/desk matter object.

    Ordinary desk and room objects that carry no explicit sharing marker are
    everyday shared matter. Any explicit private marker (or a private parent
    zone, normalised upstream) wins over the default.
    """
    if obj.get("private") is True:
        return False
    if str(obj.get("visibility") or "").strip().lower() == "private":
        return False
    if (
        "shared" not in obj
        and "shared_with_partner" not in obj
        and "visibility" not in obj
    ):
        return True
    return _item_is_shared(obj)


def _read_matter_space_objects_zero_write(
    day_root: Path | str,
    *,
    viewer: str = "partner",
    now_iso: Optional[str] = None,
    corrupt_domains: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Read ``world/v08_matter_space.json`` objects without any disk write."""
    root_path = Path(day_root)
    doc = _read_json_checked_zero_write(
        root_path.joinpath(*_MATTER_SPACE_REL),
        corrupt_domains=corrupt_domains,
        domain_name="room_objects",
    )
    if not isinstance(doc, Mapping):
        if root_path.is_dir() and (
            (root_path / "one-day").is_dir()
            or (root_path / "supply").is_dir()
            or (root_path / "world").is_dir()
        ):
            try:
                doc = _v08_matter.read_matter_space_view(
                    root_path,
                    actor_side="subject" if viewer == "subject" else "partner",
                    now_iso=now_iso,
                )
            except Exception:
                doc = None
    if not isinstance(doc, Mapping):
        return []
    raw = doc.get("matter_objects")
    if raw is None:
        raw = doc.get("objects")
    zones = doc.get("zones") if isinstance(doc.get("zones"), Mapping) else {}
    private_zones = {
        str(zid)
        for zid, zrow in zones.items()
        if isinstance(zrow, Mapping)
        and str(zrow.get("visibility") or "").strip().lower() == "private"
    }
    rows: list[dict[str, Any]] = []
    if isinstance(raw, Mapping):
        for oid, oval in sorted(raw.items()):
            if isinstance(oval, Mapping):
                row = dict(oval)
                row.setdefault("object_id", str(oid))
                rows.append(row)
    elif isinstance(raw, list):
        rows = [dict(oval) for oval in raw if isinstance(oval, Mapping)]
    row_by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        rid = str(row.get("object_id") or "").strip()
        if rid:
            row_by_id[rid] = row

    def _has_private_ancestor(start: Mapping[str, Any]) -> bool:
        visited: set[str] = set()
        cur: Optional[Mapping[str, Any]] = start
        for _hop in range(24):
            if cur is None:
                break
            cur_id = str(cur.get("object_id") or "").strip()
            if cur_id:
                if cur_id in visited:
                    break
                visited.add(cur_id)
            if (
                cur.get("private") is True
                or cur.get("unshared") is True
                or str(cur.get("visibility") or "").strip().lower() == "private"
                or cur.get("shared") is False
                or cur.get("shared_with_partner") is False
            ):
                return True
            parent_ref = str(
                cur.get("parent_id")
                or cur.get("container_id")
                or cur.get("inside_id")
                or cur.get("zone_id")
                or cur.get("location")
                or ""
            ).strip()
            if not parent_ref:
                break
            if parent_ref in private_zones:
                return True
            nxt = row_by_id.get(parent_ref)
            if nxt is None and parent_ref != str(cur.get("zone_id") or "").strip():
                zid = str(cur.get("zone_id") or "").strip()
                if zid in private_zones:
                    return True
            cur = nxt
        return False

    out: list[dict[str, Any]] = []
    for row in rows:
        if _has_private_ancestor(row):
            row = dict(row)
            row["visibility"] = "private"
        out.append(row)
    return out


def _common_ground_item_shared(item: Mapping[str, Any]) -> bool:
    status = str(item.get("ground_status") or "").strip().upper()
    if status == "PRIVATE_UNSHARED":
        return False
    if status == "SHARED_WITH_PARTNER":
        return True
    return _item_is_shared(item)


def _iter_common_ground_entries(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for key in ("items", "shared_plans", "copractices", "practices", "plans"):
        raw = doc.get(key)
        if isinstance(raw, Mapping):
            for eid, eval_ in sorted(raw.items()):
                if isinstance(eval_, Mapping):
                    row = dict(eval_)
                    row.setdefault("item_id" if key == "items" else "practice_id", str(eid))
                    entries.append(row)
        elif isinstance(raw, list):
            entries.extend(dict(row) for row in raw if isinstance(row, Mapping))
    return entries


def _collect_common_ground_copractices_zero_write(
    day_root: Path | str,
    *,
    has_subject_capability: bool,
    blocked_ids: set[str],
    seen_ids: set[str],
    barrier: Optional[Mapping[str, Any]] = None,
    corrupt_domains: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Merge light co-presence / agreement items from common-ground ledgers (zero-write)."""
    out: list[dict[str, Any]] = []
    for rel in (("phone", "v02_common_ground.json"), ("memory", "v02_common_ground_state.json")):
        doc = _read_json_checked_zero_write(
            Path(day_root).joinpath(*rel),
            corrupt_domains=corrupt_domains,
            domain_name="common_ground",
        )
        if not isinstance(doc, Mapping):
            continue
        for item in _iter_common_ground_entries(doc):
            cadence_blob = " ".join(
                str(item.get(key) or "")
                for key in ("scale_mode", "cadence_mode", "cadence", "pace", "rhythm")
            ).strip().lower()
            matched = next((tok for tok in _RESCALED_CADENCE_TOKENS if tok in cadence_blob), "")
            if cadence_blob and not matched:
                # Explicitly non-rescaled cadence (e.g. "daily" or "heavy_joint") must not be merged.
                continue
            item_kind = str(item.get("item_kind") or "").strip().lower()
            requires_agreement = bool(item.get("requires_agreement", False))
            if not matched and not (
                item_kind in ("copractice", "co_presence", "light_relay", "shared_habit", "agreement")
                or requires_agreement
            ):
                continue
            pid = str(
                item.get("practice_id")
                or item.get("item_id")
                or item.get("plan_id")
                or item.get("id")
                or ""
            ).strip()
            if not pid or pid in seen_ids:
                continue
            if _is_barrier_blocked(item, blocked_ids, barrier=barrier):
                continue
            g_status = str(item.get("ground_status") or "").strip().upper()
            if g_status == "PRIVATE_UNSHARED" or item.get("private") is True:
                is_sh = False
            elif g_status == "SHARED_WITH_PARTNER":
                is_sh = True
            elif g_status == "PENDING_CLARIFICATION" and requires_agreement:
                is_sh = True
            else:
                is_sh = _common_ground_item_shared(item)
            if not has_subject_capability and not is_sh:
                continue
            seen_ids.add(pid)
            raw_mode = (
                item.get("scale_mode")
                or item.get("cadence_mode")
                or item.get("cadence")
                or matched
                or "light_relay"
            )
            norm_mode = _normalize_scale_mode(raw_mode, default="light_relay")
            delivery_state = str(item.get("delivery_state") or "").strip().upper()
            partner_confirmed = bool(
                item.get("partner_confirmed")
                if "partner_confirmed" in item
                else (delivery_state == "AGREED" or (g_status == "SHARED_WITH_PARTNER" and not requires_agreement))
            )
            out.append({
                "practice_id": pid,
                "title_cn": str(
                    item.get("title_cn")
                    or item.get("title")
                    or item.get("topic")
                    or item.get("summary")
                    or "轻量共同安排"
                ),
                "scale_mode": norm_mode,
                "partner_confirmed": partner_confirmed,
                "shared": is_sh,
                "ground_status": g_status or ("SHARED_WITH_PARTNER" if is_sh else "PRIVATE_UNSHARED"),
                "requires_agreement": requires_agreement,
            })
    return out


def _merge_legacy_budget_mode_zero_write(
    day_root: Path | str,
    budget_survival: Mapping[str, Any],
    *,
    corrupt_domains: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Unify runtime migration power/dormant state and ASP budget survival into one truth."""
    out = copy.deepcopy(dict(budget_survival))
    doc = _read_json_checked_zero_write(
        Path(day_root) / "phone" / "v02_runtime_migration.json",
        corrupt_domains=corrupt_domains,
        domain_name="autonomy_rhythm",
    )
    mode_cfg = doc.get("mode_config") if isinstance(doc, Mapping) and isinstance(doc.get("mode_config"), Mapping) else {}
    legacy_mode = doc.get("runtime_mode") if isinstance(doc, Mapping) else None
    if isinstance(legacy_mode, Mapping):
        legacy_mode = legacy_mode.get("mode")
    if not isinstance(legacy_mode, str) or not legacy_mode.strip():
        legacy_mode = mode_cfg.get("mode")
    legacy_mode = str(legacy_mode or "").strip().upper()

    rm_scale = doc.get("budget_scale_pct") if isinstance(doc, Mapping) else None
    if rm_scale is None:
        rm_scale = mode_cfg.get("budget_scale_pct")
    rm_scale_int: Optional[int] = None
    if rm_scale is not None and not isinstance(rm_scale, bool):
        try:
            rm_scale_int = int(rm_scale)
        except (TypeError, ValueError):
            rm_scale_int = None

    base_pct_raw = out.get("budget_scale_pct")
    base_pct = int(base_pct_raw) if (isinstance(base_pct_raw, int) and not isinstance(base_pct_raw, bool)) else 100

    def _cap_resources(scale_pct: int) -> None:
        ratio = max(0.0, min(1.0, float(scale_pct) / 100.0))
        if isinstance(out.get("available_cost_budget"), (int, float)) and not isinstance(out.get("available_cost_budget"), bool):
            if scale_pct == 0:
                out["available_cost_budget"] = 0.0
            elif base_pct > scale_pct and base_pct > 0:
                out["available_cost_budget"] = round(float(out["available_cost_budget"]) * (scale_pct / float(base_pct)), 4)
        if isinstance(out.get("available_token_budget"), int) and not isinstance(out.get("available_token_budget"), bool):
            if scale_pct == 0:
                out["available_token_budget"] = 0
            elif base_pct > scale_pct and base_pct > 0:
                out["available_token_budget"] = max(0, int(round(out["available_token_budget"] * ratio)))
        if scale_pct < 100:
            out["allow_exploratory_cognition"] = False
            if "allow_decorative_generation" in out:
                out["allow_decorative_generation"] = False

    if legacy_mode == "DORMANT":
        out["mode"] = "DORMANT"
        out["legacy_runtime_mode"] = "DORMANT"
        out["runtime_mode"] = "DORMANT"
        out["budget_scale_pct"] = 0
        out["allow_exploratory_cognition"] = False
        out["allow_decorative_generation"] = False
        _cap_resources(0)
        return out

    eff_pct = base_pct
    if rm_scale_int is not None and rm_scale_int < eff_pct:
        eff_pct = max(0, rm_scale_int)

    asp_mode = str(out.get("mode") or "").strip().upper()
    if legacy_mode in _LEGACY_POWER_MODES:
        out["legacy_runtime_mode"] = legacy_mode
        out["runtime_mode"] = legacy_mode
        if asp_mode == str(_asp.BUDGET_MODE_NORMAL).strip().upper():
            out["mode"] = legacy_mode
        if rm_scale_int is None and legacy_mode == "LOW_BUDGET":
            eff_pct = min(eff_pct, 25)
    elif legacy_mode:
        out["runtime_mode"] = legacy_mode

    if eff_pct < 100 or asp_mode != str(_asp.BUDGET_MODE_NORMAL).strip().upper():
        out["budget_scale_pct"] = eff_pct
        _cap_resources(eff_pct)
        if str(out.get("mode") or "").strip().upper() == str(_asp.BUDGET_MODE_NORMAL).strip().upper():
            out["mode"] = "LOW_BUDGET"
            out["legacy_runtime_mode"] = legacy_mode or "LOW_BUDGET"

    return out


def _strip_secret_keys(obj: Any, *, depth: int = 0) -> Any:
    if obj is None or depth > 32:
        return obj
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).strip().lower()
            if lk in _SECRET_FORGET_KEYS or lk.endswith("_lease_token") or lk.endswith("_secret_text"):
                continue
            out[str(k)] = _strip_secret_keys(v, depth=depth + 1)
        return out
    if isinstance(obj, list):
        return [_strip_secret_keys(elem, depth=depth + 1) for elem in obj]
    return obj


def _collect_forget_barrier_zero_write(day_root: Path) -> dict[str, Any]:
    """Read-only inspection of Forget holds, tombstones, V-C seals, and external control barrier."""
    return _rlf.load_unified_forget_barrier_readonly(day_root)


def _is_barrier_blocked(
    item: Mapping[str, Any],
    blocked_ids: set[str],
    *,
    barrier: Optional[Mapping[str, Any]] = None,
) -> bool:
    if barrier is not None and _rlf.is_entity_forget_blocked(item, barrier):
        return True
    if item.get("hold_active") or item.get("erased"):
        return True
    if str(item.get("status") or "").upper() == "TOMBSTONED":
        return True
    if str(item.get("lifecycle") or "").upper() == "TOMBSTONED":
        return True
    if not blocked_ids:
        return False
    for key in (
        "artifact_id",
        "source_id",
        "derivation_id",
        "work_id",
        "source_work_id",
        "item_id",
        "trace_id",
        "object_id",
        "script_id",
        "scrap_id",
        "period_id",
        "practice_id",
        "routine_id",
        "entry_id",
        "episode_id",
        "claim_id",
        "case_id",
        "memory_id",
        "id",
    ):
        val = item.get(key)
        if isinstance(val, str) and val.strip() in blocked_ids:
            return True
    for list_key in ("root_set", "source_refs", "provenance_refs", "episode_ids", "durable_claim_ids"):
        seq = item.get(list_key)
        if isinstance(seq, Sequence) and not isinstance(seq, (str, bytes)):
            for elem in seq:
                s = str(elem).strip()
                if s in blocked_ids or any(s.endswith(f":{bid}") for bid in blocked_ids):
                    return True
    prov = item.get("provenance")
    if isinstance(prov, Mapping):
        for pv in prov.values():
            if isinstance(pv, str) and pv.strip() in blocked_ids:
                return True
    return False


def _read_crash_reconciler_summary_zero_write(
    day_root: Path,
    *,
    corrupt_domains: Optional[list[str]] = None,
) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "db_exists": False,
        "read_error": False,
        "total_actions": 0,
        "unknown_actions": 0,
        "quiesced_unresolved": 0,
        "pending_actions": 0,
        "succeeded_actions": 0,
        "failed_actions": 0,
        "partial_compound_actions": 0,
        "active_writer_epoch": 0,
    }
    paths = _instance_paths.resolve_instance_paths(day_root)
    croot = paths.day_root
    db_path = next(
        (
            cand
            for cand in (
                paths.canonical_ledger_db,
                croot / "v02" / "canonical_life.sqlite3",
                croot / "canonical_life.sqlite3",
                croot / "reconciler" / "canonical_life.sqlite3",
                paths.life_state_root / "reconciler" / "canonical_life.sqlite3",
                paths.life_state_root / "canonical_life.sqlite3",
            )
            if cand.is_file() and not cand.is_symlink()
        ),
        None,
    )
    if db_path is None:
        return empty

    conn: Optional[sqlite3.Connection] = None
    try:
        uri = db_path.resolve().as_uri()
        shm_path = db_path.with_name(db_path.name + "-shm")
        wal_path = db_path.with_name(db_path.name + "-wal")
        shm_exists = shm_path.is_file() and not shm_path.is_symlink()
        wal_nonempty = (
            wal_path.is_file()
            and not wal_path.is_symlink()
            and wal_path.stat().st_size > 0
        )
        if shm_exists or wal_nonempty:
            conn = sqlite3.connect(uri + "?mode=ro", uri=True, timeout=1.0)
        else:
            conn = sqlite3.connect(uri + "?mode=ro&immutable=1", uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT action_id, parent_action_id, is_compound, phase, outcome FROM actions"
        ).fetchall()
        epoch_row = conn.execute(
            "SELECT active_writer_epoch FROM runtime_control WHERE control_key='primary'"
        ).fetchone()
        total = len(rows)
        unk = sum(1 for r in rows if r["outcome"] == "UNKNOWN")
        quiesced = sum(1 for r in rows if r["phase"] == "QUIESCED_UNRESOLVED")
        pend = sum(1 for r in rows if r["outcome"] == "PENDING")
        succ = sum(1 for r in rows if r["outcome"] == "SUCCEEDED")
        fail = sum(1 for r in rows if r["outcome"] == "FAILED")

        children_by_parent: dict[str, set[str]] = {}
        for r in rows:
            pid = r["parent_action_id"]
            if pid:
                children_by_parent.setdefault(str(pid), set()).add(str(r["outcome"]))
        partial_cnt = sum(
            1
            for outcomes in children_by_parent.values()
            if "SUCCEEDED" in outcomes and bool(outcomes - {"SUCCEEDED"})
        )
        return {
            "db_exists": True,
            "read_error": False,
            "total_actions": total,
            "unknown_actions": unk,
            "quiesced_unresolved": quiesced,
            "pending_actions": pend,
            "succeeded_actions": succ,
            "failed_actions": fail,
            "partial_compound_actions": partial_cnt,
            "active_writer_epoch": int(epoch_row["active_writer_epoch"]) if epoch_row else 0,
        }
    except sqlite3.Error:
        if corrupt_domains is not None:
            corrupt_domains.append("crash_reconciler")
        return {**empty, "db_exists": True, "read_error": True}
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def _build_md_conflicts_zero_write(
    day_root: Path,
    *,
    raw_rlf: Mapping[str, Any],
    honest_state: Mapping[str, Any],
    blocked_ids: set[str],
    has_subject_capability: bool,
    barrier: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    artifacts = raw_rlf.get("artifacts") if isinstance(raw_rlf.get("artifacts"), Mapping) else {}
    representations = (
        raw_rlf.get("representations") if isinstance(raw_rlf.get("representations"), Mapping) else {}
    )
    seen_map = (
        honest_state.get("divergence_seen")
        if isinstance(honest_state.get("divergence_seen"), Mapping)
        else {}
    )

    rows: list[dict[str, Any]] = []
    for aid, art in sorted(artifacts.items()):
        if not isinstance(art, Mapping):
            continue
        if _is_barrier_blocked({"artifact_id": str(aid), **dict(art)}, blocked_ids, barrier=barrier):
            continue
        is_shared = art.get("visibility") == "shared" or art.get("shared") is True
        if not has_subject_capability and not is_shared:
            continue

        binding = representations.get(aid) if isinstance(representations.get(aid), Mapping) else None
        if binding is None:
            rep_state = _rlf.REP_UNBOUND
            obs_hash = ""
            doc_label = str(art.get("title") or aid)
        else:
            _rc, rep = _rlf.inspect_representation(
                day_root,
                artifact_id=aid,
                has_subject_capability=True,
            )
            rep_state = str(rep.get("representation_health") or binding.get("health") or _rlf.REP_DIVERGED)
            obs_hash = str(
                rep.get("observed_sha256")
                or binding.get("last_observed_sha256")
                or rep.get("known_projection_hash")
                or ""
            )
            raw_rel = str(binding.get("workspace_rel_path") or "")
            doc_label = str(art.get("title") or (Path(raw_rel).name if raw_rel else aid))

        seen_entry = seen_map.get(aid) if isinstance(seen_map.get(aid), Mapping) else None
        divergence_seen = False
        if seen_entry and bool(seen_entry.get("seen")):
            seen_hash = str(seen_entry.get("observed_head_hash") or "")
            if not seen_hash or not obs_hash or seen_hash == obs_hash:
                divergence_seen = True

        rows.append({
            "artifact_id": str(aid),
            "doc_label": doc_label,
            "state": rep_state,
            "observed_head_hash": obs_hash,
            "divergence_seen": divergence_seen,
            "shared": bool(is_shared),
        })
    return rows


def _is_forget_case_partner_visible(
    doc: Mapping[str, Any],
    meta: Mapping[str, Any],
) -> bool:
    if meta.get("shared_with_partner") is False or doc.get("shared_with_partner") is False:
        return False
    if meta.get("private") is True or doc.get("private") is True:
        return False
    vis = str(meta.get("visibility") or doc.get("visibility") or "").strip().lower()
    if vis == "shared":
        return True
    if vis == "private":
        return False
    if str(meta.get("actor_side") or "").strip().lower() == "partner":
        return True
    if str(doc.get("actor_side") or "").strip().lower() == "partner":
        return True
    if meta.get("shared_with_partner") is True or doc.get("shared_with_partner") is True:
        return True
    return False


def _build_forget_cases_zero_write(
    *,
    raw_rlf: Mapping[str, Any],
    honest_state: Mapping[str, Any],
    has_subject_capability: bool = True,
) -> list[dict[str, Any]]:
    meta_map = (
        honest_state.get("forget_case_meta")
        if isinstance(honest_state.get("forget_case_meta"), Mapping)
        else {}
    )
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    holds = raw_rlf.get("holds") if isinstance(raw_rlf.get("holds"), Mapping) else {}
    for hid, hdoc in sorted(holds.items()):
        if not isinstance(hdoc, Mapping):
            continue
        cid = str(hdoc.get("hold_id") or hid)
        seen_ids.add(cid)
        meta = meta_map.get(cid) if isinstance(meta_map.get(cid), Mapping) else {}
        if not has_subject_capability and not _is_forget_case_partner_visible(hdoc, meta):
            continue
        t_dom = str(meta.get("target_domain") or "episode")
        if t_dom not in ALLOWED_TARGET_DOMAINS:
            t_dom = "unsure"
        out.append({
            "case_id": cid,
            "status": str(hdoc.get("status") or _rlf.CASE_HOLD_PENDING),
            "target_domain": t_dom,
        })

    fcases = raw_rlf.get("forget_cases") if isinstance(raw_rlf.get("forget_cases"), Mapping) else {}
    for fcid, fdoc in sorted(fcases.items()):
        if not isinstance(fdoc, Mapping):
            continue
        cid = str(fdoc.get("case_id") or fcid)
        if cid in seen_ids:
            continue
        meta = meta_map.get(cid) if isinstance(meta_map.get(cid), Mapping) else {}
        if not has_subject_capability and not _is_forget_case_partner_visible(fdoc, meta):
            continue
        t_dom = str(meta.get("target_domain") or "all_derived")
        if t_dom not in ALLOWED_TARGET_DOMAINS:
            t_dom = "all_derived"
        out.append({
            "case_id": cid,
            "status": str(fdoc.get("status") or _rlf.CASE_COMPLETE),
            "target_domain": t_dom,
        })

    return out


def _build_chatless_traces_zero_write(
    day_root: Path,
    *,
    has_subject_capability: bool,
    now_iso: str,
    blocked_ids: set[str],
    lived_proj: Mapping[str, Any],
    md_conflicts: Sequence[Mapping[str, Any]],
    crash_summary: Mapping[str, Any],
    barrier: Optional[Mapping[str, Any]] = None,
    corrupt_domains: Optional[list[str]] = None,
) -> dict[str, list[dict[str, Any]]]:
    viewer = "subject" if has_subject_capability else "partner"
    objects: list[dict[str, Any]] = []
    works: list[dict[str, Any]] = []
    bookmarks: list[dict[str, Any]] = []
    waits: list[dict[str, Any]] = []

    # 1. Objects from v08_open_affordance (zero-write)
    try:
        v08_view = _v08_open.read_living_traces_view(day_root, viewer=viewer, now_iso=now_iso)
    except Exception:
        v08_view = {}
    seen_object_ids: set[str] = set()
    matter_rows_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(v08_view, Mapping):
        for tr in v08_view.get("active_traces") or ():
            if not isinstance(tr, Mapping) or _is_barrier_blocked(tr, blocked_ids, barrier=barrier):
                continue
            is_sh = _item_is_shared(tr)
            if not has_subject_capability and not is_sh:
                continue
            oid = str(tr.get("object_id") or "")
            if oid:
                seen_object_ids.add(oid)
            objects.append({
                "trace_id": str(tr.get("trace_id") or ""),
                "object_id": oid,
                "title_cn": str(tr.get("object_label_cn") or tr.get("state_sticker_cn") or tr.get("object_id") or "屋里物件"),
                "shared": is_sh,
            })

    # 1b. Room / desk matter objects (new matter space plus the legacy traces above).
    matter_objs: list[Mapping[str, Any]] = []
    if isinstance(v08_view, Mapping):
        matter_space = v08_view.get("matter_space")
        if isinstance(matter_space, Mapping) and isinstance(matter_space.get("objects"), list):
            matter_objs = [row for row in matter_space["objects"] if isinstance(row, Mapping)]
    if not matter_objs:
        matter_objs = _read_matter_space_objects_zero_write(
            day_root,
            viewer=viewer,
            now_iso=now_iso,
            corrupt_domains=corrupt_domains,
        )
    for obj in matter_objs:
        oid = str(obj.get("object_id") or "")
        if oid:
            matter_rows_by_id[oid] = dict(obj)
        if obj.get("active", True) is False:
            continue
        if _is_barrier_blocked(obj, blocked_ids, barrier=barrier):
            continue
        if not oid or oid in seen_object_ids:
            continue
        is_sh = _matter_object_shared(obj)
        if not has_subject_capability and not is_sh:
            continue
        seen_object_ids.add(oid)
        objects.append({
            "object_id": oid,
            "title_cn": str(
                obj.get("label_cn")
                or obj.get("title_cn")
                or obj.get("derived_role_cn")
                or oid
            ),
            "zone_id": str(obj.get("effective_zone_id") or obj.get("zone_id") or obj.get("parent_id") or ""),
            "shared": is_sh,
        })

    # 2. Works from lived_slices work_sufficiencies/versions & visible md_conflicts
    dual_blk = (
        lived_proj.get("dual_sufficiency_and_diff_material")
        if isinstance(lived_proj.get("dual_sufficiency_and_diff_material"), Mapping)
        else {}
    )
    for wk in dual_blk.get("work_sufficiencies") or ():
        if not isinstance(wk, Mapping) or _is_barrier_blocked(wk, blocked_ids, barrier=barrier):
            continue
        is_sh = _item_is_shared(wk)
        if not has_subject_capability and not is_sh:
            continue
        works.append({
            "work_id": str(wk.get("work_id") or ""),
            "title_cn": str(wk.get("title") or wk.get("work_id") or "手头作品"),
            "shared": is_sh,
        })
    for mc in md_conflicts:
        works.append({
            "artifact_id": str(mc.get("artifact_id") or ""),
            "title_cn": str(mc.get("doc_label") or mc.get("artifact_id") or "工作区文档"),
            "state": str(mc.get("state") or "UNBOUND"),
            "shared": bool(mc.get("shared", False)),
        })

    # 3. Bookmarks from outing_bench_bookstore.json & v02_crossmodal.json (zero-write)
    bs_doc = _read_json_checked_zero_write(
        day_root / "phone" / "outing_bench_bookstore.json",
        corrupt_domains=corrupt_domains,
        domain_name="reading_bookmarks",
    )
    if isinstance(bs_doc, Mapping):
        for bm in bs_doc.get("window_bookmarks") or ():
            if not isinstance(bm, Mapping) or _is_barrier_blocked(bm, blocked_ids, barrier=barrier):
                continue
            is_sh = _item_is_shared(bm)
            if not has_subject_capability and not is_sh:
                continue
            bookmarks.append({
                "book_code": str(bm.get("book_code") or ""),
                "title_cn": str(bm.get("title") or bm.get("book_code") or "书店书签"),
                "shared": is_sh,
            })
    cm_doc = _read_json_checked_zero_write(
        day_root / "phone" / "v02_crossmodal.json",
        corrupt_domains=corrupt_domains,
        domain_name="reading_bookmarks",
    )
    if isinstance(cm_doc, Mapping):
        for mk in cm_doc.get("reading_marks") or cm_doc.get("reading_uncertain_marks") or ():
            if not isinstance(mk, Mapping) or _is_barrier_blocked(mk, blocked_ids, barrier=barrier):
                continue
            is_sh = _item_is_shared(mk)
            if not has_subject_capability and not is_sh:
                continue
            bookmarks.append({
                "mark_id": str(mk.get("mark_id") or ""),
                "title_cn": str(mk.get("book_title") or "共读书签"),
                "shared": is_sh,
            })

    # 4. Waits from tentative plans & unresolved reconciler actions (zero-write)
    for tp_file in ("home_hearth_tentative_plans.json", "tentative_plans.json"):
        tp_doc = _read_json_checked_zero_write(
            day_root / "phone" / tp_file,
            corrupt_domains=corrupt_domains,
            domain_name="autonomy_rhythm",
        )
        if not isinstance(tp_doc, Mapping):
            continue
        for pl in tp_doc.get("tentative_plans") or tp_doc.get("plans") or ():
            if not isinstance(pl, Mapping) or _is_barrier_blocked(pl, blocked_ids, barrier=barrier):
                continue
            st = str(pl.get("status") or "").upper()
            if st not in ("PROPOSED", "TENTATIVE", "RESCHEDULED", "OPEN"):
                continue
            is_sh = True if "shared" not in pl and "visibility" not in pl else _item_is_shared(pl)
            if not has_subject_capability and not is_sh:
                continue
            waits.append({
                "plan_id": str(pl.get("plan_id") or ""),
                "title_cn": str(pl.get("title") or pl.get("summary") or "待定小约定"),
                "shared": is_sh,
            })
    if has_subject_capability and (
        int(crash_summary.get("unknown_actions") or 0) > 0
        or int(crash_summary.get("quiesced_unresolved") or 0) > 0
    ):
        waits.append({
            "kind": "reconciler_unresolved",
            "title_cn": "仍有待核实的在途动作结果",
            "shared": False,
        })

    # 5. Legacy chatless life review (zero-write; partner rows already filtered).
    try:
        from components.life.scripts import v02_runtime_governance_migration as _rtmig

        review = _rtmig.project_chatless_life_review(
            day_root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=now_iso,
        )
    except Exception:
        review = {}
    if not isinstance(review, Mapping):
        review = {}

    works_blk = review.get("works_and_versions")
    works_blk = works_blk if isinstance(works_blk, Mapping) else {}
    seen_work_ids = {str(w.get("work_id") or w.get("artifact_id") or "") for w in works}
    for item in works_blk.get("items") or ():
        if not isinstance(item, Mapping) or _is_barrier_blocked(item, blocked_ids, barrier=barrier):
            continue
        wid = str(item.get("work_id") or item.get("artifact_id") or "")
        if not wid or wid in seen_work_ids:
            continue
        is_sh = _item_is_shared(item)
        if not has_subject_capability and not is_sh:
            continue
        seen_work_ids.add(wid)
        works.append({
            "work_id": wid,
            "title_cn": str(item.get("title") or wid or "手头作品"),
            "shared": is_sh,
        })

    bookmarks_blk = review.get("reading_bookmarks")
    bookmarks_blk = bookmarks_blk if isinstance(bookmarks_blk, Mapping) else {}
    seen_bookmark_ids = {
        str(b.get("book_code") or b.get("mark_id") or "") for b in bookmarks
    }
    for item in bookmarks_blk.get("bookmarks") or ():
        if not isinstance(item, Mapping) or _is_barrier_blocked(item, blocked_ids, barrier=barrier):
            continue
        bid = str(item.get("bookmark_id") or item.get("book_code") or "")
        if not bid or bid in seen_bookmark_ids:
            continue
        is_sh = _item_is_shared(item)
        if not has_subject_capability and not is_sh:
            continue
        seen_bookmark_ids.add(bid)
        bookmarks.append({
            "book_code": bid,
            "title_cn": str(item.get("title") or bid or "书店书签"),
            "shared": is_sh,
        })
    for item in bookmarks_blk.get("crossmodal_marks") or ():
        if not isinstance(item, Mapping) or _is_barrier_blocked(item, blocked_ids, barrier=barrier):
            continue
        mid = str(item.get("mark_id") or "")
        if not mid or mid in seen_bookmark_ids:
            continue
        is_sh = _item_is_shared(item)
        if not has_subject_capability and not is_sh:
            continue
        seen_bookmark_ids.add(mid)
        bookmarks.append({
            "mark_id": mid,
            "title_cn": str(item.get("title") or "共读书签"),
            "shared": is_sh,
        })

    waiting_blk = review.get("closed_and_waiting_items")
    waiting_blk = waiting_blk if isinstance(waiting_blk, Mapping) else {}
    seen_wait_ids = {str(w.get("plan_id") or w.get("wait_id") or "") for w in waits}
    for item in waiting_blk.get("waiting_items") or ():
        if not isinstance(item, Mapping) or _is_barrier_blocked(item, blocked_ids, barrier=barrier):
            continue
        nid = str(item.get("note_id") or "")
        if not nid or nid in seen_wait_ids:
            continue
        is_sh = _item_is_shared(item)
        if not has_subject_capability and not is_sh:
            continue
        seen_wait_ids.add(nid)
        waits.append({
            "plan_id": nid,
            "title_cn": str(item.get("title") or "待定的明日便签"),
            "shared": is_sh,
        })

    room_blk = review.get("room_layout_traces")
    room_blk = room_blk if isinstance(room_blk, Mapping) else {}
    for item in room_blk.get("objects") or ():
        if not isinstance(item, Mapping) or _is_barrier_blocked(item, blocked_ids, barrier=barrier):
            continue
        oid = str(item.get("object_id") or "")
        if not oid or oid in seen_object_ids:
            continue
        src_row = matter_rows_by_id.get(oid)
        if src_row is not None and src_row.get("active", True) is False:
            continue
        is_sh = _matter_object_shared(src_row) if src_row is not None else _item_is_shared(item)
        if not has_subject_capability and not is_sh:
            continue
        seen_object_ids.add(oid)
        objects.append({
            "object_id": oid,
            "title_cn": str(item.get("label") or item.get("label_cn") or oid),
            "zone_id": str(item.get("zone") or item.get("zone_id") or ""),
            "shared": is_sh,
        })

    return {
        "objects": objects,
        "works": works,
        "bookmarks": bookmarks,
        "waits": waits,
    }


def _collect_skill_attributions_zero_write(
    day_root: Path,
    *,
    now_iso: str,
    corrupt_domains: Optional[list[str]] = None,
) -> dict[str, Any]:
    try:
        asp_skills = _asp.project_skill_provenance(day_root, now_iso=now_iso)
    except Exception:
        if corrupt_domains is not None:
            corrupt_domains.append("skill_attributions")
        asp_skills = {}
    releases_raw = asp_skills.get("releases") if isinstance(asp_skills.get("releases"), Mapping) else {}
    entries: list[dict[str, Any]] = []
    by_attr: dict[str, int] = {}
    unvalidated: list[str] = []

    for skey, srec in sorted(releases_raw.items()):
        if not isinstance(srec, Mapping):
            continue
        attr = str(srec.get("attribution") or "MODEL_NATIVE")
        status = str(srec.get("status") or "VALIDATED")
        by_attr[attr] = by_attr.get(attr, 0) + 1
        if status in ("UNVALIDATED_AFTER_MIGRATION", "SUSPENDED", "DEGRADED"):
            unvalidated.append(str(srec.get("method_id") or skey))
        entries.append({
            "skill_key": str(skey),
            "method_id": str(srec.get("method_id") or skey),
            "attribution": attr,
            "status": status,
            "tool_version": str(srec.get("tool_version") or ""),
            "model_id": str(srec.get("model_id") or ""),
        })

    # Also inspect runtime_migration skill statuses if present
    rm_doc = _read_json_checked_zero_write(
        day_root / "phone" / "v02_runtime_migration.json",
        corrupt_domains=corrupt_domains,
        domain_name="autonomy_rhythm",
    )
    if isinstance(rm_doc, Mapping) and isinstance(rm_doc.get("skills"), Mapping):
        for sk_id, sk_meta in sorted(rm_doc["skills"].items()):
            if not isinstance(sk_meta, Mapping):
                continue
            st = str(sk_meta.get("status") or "").upper()
            if st in ("UNVALIDATED_AFTER_MIGRATION", "SUSPENDED_AFTER_TRIAL_LOSS", "PENDING_REVERIFY"):
                if str(sk_id) not in unvalidated:
                    unvalidated.append(str(sk_id))

    return {
        "entries": entries,
        "by_attribution": by_attr,
        "unvalidated_after_migration": sorted(unvalidated),
        "unvalidated_count": len(unvalidated),
        "state_exists": bool(asp_skills.get("state_exists")),
    }


def read_v02_honest_ux_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """100% zero-write projection for ``GET /api/phone/v02/honest-ux``."""
    root = Path(day_root)
    stamp = _now_iso(now_iso)
    persp = "subject" if has_subject_capability else "partner"
    corrupt_domains: list[str] = []

    barrier = _collect_forget_barrier_zero_write(root)
    if barrier.get("fail_closed"):
        corrupt_domains.append("memory_boundary")
    raw_rlf = barrier["raw_rlf"]
    blocked_ids = barrier["blocked_ids"]
    honest_state = _load_honest_state_readonly(root, corrupt_domains=corrupt_domains)

    # Wave 1 projections (all zero-write; corrupt domain files degrade to unverified rather than 500 or false VERIFIED)
    try:
        lived_proj = _lived_slices.project_v02_lived_slices(
            root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=stamp,
        )
    except Exception:
        corrupt_domains.append("lived_slices")
        lived_proj = {}
    try:
        p12_proj = _asp.project_p12_autobiographical_periods(
            root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=stamp,
        )
    except Exception:
        corrupt_domains.append("autobiography")
        p12_proj = {}
    try:
        sit_prov = _asp.project_situation_provenance_zero_write(
            root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=stamp,
        )
    except Exception:
        corrupt_domains.append("situation_provenance")
        sit_prov = {}
    try:
        base_budget = _asp.project_budget_survival(root, now_iso=stamp)
    except Exception:
        corrupt_domains.append("autonomy_rhythm")
        base_budget = {}
    budget_survival = _merge_legacy_budget_mode_zero_write(
        root,
        base_budget,
        corrupt_domains=corrupt_domains,
    )
    skill_attributions = _collect_skill_attributions_zero_write(
        root, now_iso=stamp, corrupt_domains=corrupt_domains
    )
    crash_summary = _read_crash_reconciler_summary_zero_write(root, corrupt_domains=corrupt_domains)

    md_conflicts = _build_md_conflicts_zero_write(
        root,
        raw_rlf=raw_rlf,
        honest_state=honest_state,
        blocked_ids=blocked_ids,
        has_subject_capability=bool(has_subject_capability),
        barrier=barrier,
    )
    forget_cases = _build_forget_cases_zero_write(
        raw_rlf=raw_rlf,
        honest_state=honest_state,
        has_subject_capability=bool(has_subject_capability),
    )

    # Rescaled copractices (filter barrier + partner visibility already handled by project_v02_lived_slices)
    raw_routines = (
        (lived_proj.get("rescaled_copractice") or {}).get("routines")
        if isinstance(lived_proj.get("rescaled_copractice"), Mapping)
        else []
    )
    rescaled_copractices: list[dict[str, Any]] = []
    for r in raw_routines or ():
        if not isinstance(r, Mapping) or _is_barrier_blocked(r, blocked_ids, barrier=barrier):
            continue
        is_sh = _item_is_shared(r)
        if not has_subject_capability and not is_sh:
            continue
        rescaled_copractices.append({
            "practice_id": str(r.get("routine_id") or ""),
            "title_cn": str(r.get("title") or r.get("title_cn") or "轻量共同安排"),
            "scale_mode": _normalize_scale_mode(
                r.get("scale_mode") or r.get("cadence_mode") or r.get("cadence"),
                default="light_relay",
            ),
            "partner_confirmed": bool(r.get("partner_confirmed", False)),
            "shared": is_sh,
        })
    seen_practice_ids = {str(r.get("practice_id") or "") for r in rescaled_copractices}
    rescaled_copractices.extend(
        _collect_common_ground_copractices_zero_write(
            root,
            has_subject_capability=bool(has_subject_capability),
            blocked_ids=blocked_ids,
            seen_ids=seen_practice_ids,
            barrier=barrier,
            corrupt_domains=corrupt_domains,
        )
    )
    raw_proposed_copractices = (
        honest_state.get("proposed_rescaled_copractices")
        if isinstance(honest_state.get("proposed_rescaled_copractices"), Mapping)
        else {}
    )
    if raw_proposed_copractices:
        by_pid_index = {
            str(row.get("practice_id") or ""): idx
            for idx, row in enumerate(rescaled_copractices)
            if str(row.get("practice_id") or "")
        }
        for pid_key, prop in sorted(raw_proposed_copractices.items()):
            if not isinstance(prop, Mapping):
                continue
            if _is_barrier_blocked(prop, blocked_ids, barrier=barrier):
                continue
            pid = str(prop.get("practice_id") or pid_key or "").strip()
            if not pid:
                continue
            is_sh = _item_is_shared(prop)
            if not has_subject_capability and not is_sh:
                continue
            merged_row: dict[str, Any] = {
                "practice_id": pid,
                "title_cn": str(prop.get("title_cn") or prop.get("title") or "轻量共同安排"),
                "scale_mode": _normalize_scale_mode(prop.get("scale_mode"), default="light_relay"),
                "status": str(prop.get("status") or "PROPOSED_BY_PARTNER"),
                "proposed_by": str(prop.get("proposed_by") or "partner"),
                "partner_confirmed": bool(prop.get("partner_confirmed", True)),
                "subject_confirmed": bool(prop.get("subject_confirmed", False)),
                "shared": is_sh,
                "no_debt": bool(prop.get("no_debt", True)),
                "note_cn": str(prop.get("note_cn") or ""),
                "updated_at": prop.get("updated_at"),
            }
            if pid in by_pid_index:
                rescaled_copractices[by_pid_index[pid]].update(merged_row)
            else:
                by_pid_index[pid] = len(rescaled_copractices)
                seen_practice_ids.add(pid)
                rescaled_copractices.append(merged_row)

    # Periods (strip private/unshared unless shared:True / shared_with_partner:True; barrier-filtered)
    periods_out: list[dict[str, Any]] = []
    for pid, pdoc in sorted((p12_proj.get("period_summaries") or {}).items()):
        if not isinstance(pdoc, Mapping) or _is_barrier_blocked(pdoc, blocked_ids, barrier=barrier):
            continue
        is_sh = _item_is_shared(pdoc)
        if not has_subject_capability and not is_sh:
            continue
        row = copy.deepcopy(dict(pdoc))
        row.setdefault("period_id", str(pid))
        row["shared"] = is_sh
        periods_out.append(row)
    for snap in p12_proj.get("versioned_narrative_snapshots") or ():
        if not isinstance(snap, Mapping) or _is_barrier_blocked(snap, blocked_ids, barrier=barrier):
            continue
        is_sh = _item_is_shared(snap)
        if not has_subject_capability and not is_sh:
            continue
        row = copy.deepcopy(dict(snap))
        row["shared"] = is_sh
        periods_out.append(row)

    # Merge attested P12 autobiographical narrative chapters (zero-write).
    seen_period_ids = {
        str(row.get("period_id") or row.get("period_key") or row.get("snapshot_id") or "")
        for row in periods_out
    }
    try:
        from components.life.scripts import v02_recall_projection as _recall_proj

        narrative = _recall_proj.project_p12_autobiographical_narrative(
            root,
            has_subject_capability=bool(has_subject_capability),
            now_iso=stamp,
        )
    except Exception:
        narrative = {}
    if isinstance(narrative, Mapping):
        for chapter in narrative.get("chapters") or ():
            if not isinstance(chapter, Mapping):
                continue
            cid = str(
                chapter.get("chapter_id")
                or chapter.get("period_id")
                or chapter.get("period_label")
                or ""
            )
            if not cid or cid in seen_period_ids:
                continue
            barrier_row = dict(chapter)
            barrier_row["period_id"] = cid
            if _is_barrier_blocked(barrier_row, blocked_ids, barrier=barrier):
                continue
            is_sh = True if not has_subject_capability else _item_is_shared(chapter)
            if not has_subject_capability and not is_sh:
                continue
            row = copy.deepcopy(dict(chapter))
            row["period_id"] = cid
            row["title_cn"] = str(
                chapter.get("title_cn")
                or chapter.get("title")
                or chapter.get("period_label")
                or cid
            )
            row["shared"] = is_sh
            seen_period_ids.add(cid)
            periods_out.append(row)

    # Scraps
    raw_scraps = (
        (lived_proj.get("salvaged_scrap_folder") or {}).get("items")
        if isinstance(lived_proj.get("salvaged_scrap_folder"), Mapping)
        else []
    )
    scraps_out: list[dict[str, Any]] = []
    for sc in raw_scraps or ():
        if not isinstance(sc, Mapping) or _is_barrier_blocked(sc, blocked_ids, barrier=barrier):
            continue
        is_sh = _item_is_shared(sc)
        if not has_subject_capability and not is_sh:
            continue
        row = copy.deepcopy(dict(sc))
        row["shared"] = is_sh
        scraps_out.append(row)

    # Personal tools
    tool_blk = (
        lived_proj.get("tool_keymap_and_simplification")
        if isinstance(lived_proj.get("tool_keymap_and_simplification"), Mapping)
        else {}
    )
    personal_tools_out: list[dict[str, Any]] = []
    for pt in tool_blk.get("personal_scripts") or ():
        if not isinstance(pt, Mapping) or _is_barrier_blocked(pt, blocked_ids, barrier=barrier):
            continue
        is_sh = _item_is_shared(pt)
        if not has_subject_capability and not is_sh:
            continue
        row = copy.deepcopy(dict(pt))
        row["shared"] = is_sh
        personal_tools_out.append(row)

    chatless_traces = _build_chatless_traces_zero_write(
        root,
        has_subject_capability=bool(has_subject_capability),
        now_iso=stamp,
        blocked_ids=blocked_ids,
        lived_proj=lived_proj,
        md_conflicts=md_conflicts,
        crash_summary=crash_summary,
        barrier=barrier,
        corrupt_domains=corrupt_domains,
    )

    diverged_count = sum(1 for mc in md_conflicts if mc.get("state") in (_rlf.REP_DIVERGED, _rlf.REP_MISSING))
    domains_meta = sit_prov.get("domains") if isinstance(sit_prov.get("domains"), Mapping) else {}
    last_synced_at = None
    for dinfo in domains_meta.values():
        if isinstance(dinfo, Mapping) and isinstance(dinfo.get("last_synced_at"), str):
            ts_cand = dinfo["last_synced_at"]
            if last_synced_at is None or ts_cand > last_synced_at:
                last_synced_at = ts_cand
    if last_synced_at is None and isinstance(honest_state.get("updated_at"), str):
        last_synced_at = honest_state["updated_at"]

    degraded_domains = sorted(set(corrupt_domains))
    if has_subject_capability:
        unknown_budget_count = len(budget_survival.get("active_unknown_reservation_ids") or ())
        unknown_reconciler_count = int(crash_summary.get("unknown_actions") or 0) + int(
            crash_summary.get("quiesced_unresolved") or 0
        ) + (1 if crash_summary.get("read_error") else 0)
        unverified_skill_count = int(skill_attributions.get("unvalidated_count") or 0)
        has_unknown = (unknown_budget_count + unknown_reconciler_count) > 0 or bool(barrier.get("fail_closed"))
        unverified_count = (
            unknown_budget_count
            + unknown_reconciler_count
            + unverified_skill_count
            + diverged_count
            + len(degraded_domains)
        )
        provenance_domains: Any = domains_meta
        provenance_audit: Any = sit_prov.get("candidate_audit")
        out_skill_attr = skill_attributions
        out_crash_summary = crash_summary
        out_budget_survival = budget_survival
    else:
        pending_partner_holds = sum(
            1
            for fc in forget_cases
            if str(fc.get("status") or "").upper() == _rlf.CASE_HOLD_PENDING
        )
        has_unknown = bool(barrier.get("fail_closed"))
        unverified_count = diverged_count + pending_partner_holds + len(degraded_domains)
        provenance_domains = {}
        provenance_audit = None
        out_skill_attr = {
            "entries": [],
            "by_attribution": {},
            "unvalidated_after_migration": [],
            "unvalidated_count": 0,
            "state_exists": False,
            "perspective_redacted": True,
        }
        out_crash_summary = {
            "perspective_redacted": True,
            "db_exists": False,
            "total_actions": 0,
            "unknown_actions": 0,
            "quiesced_unresolved": 0,
            "pending_actions": 0,
            "succeeded_actions": 0,
            "failed_actions": 0,
            "partial_compound_actions": 0,
            "active_writer_epoch": 0,
        }
        out_budget_survival = copy.deepcopy(budget_survival)
        out_budget_survival.pop("active_unknown_reservation_ids", None)

    if has_unknown:
        epistemic_status = "UNKNOWN"
    elif unverified_count > 0 or degraded_domains:
        epistemic_status = "UNVERIFIED"
    else:
        epistemic_status = "VERIFIED"

    provenance = {
        "source_domain": "living_traces",
        "last_synced_at": last_synced_at,
        "epistemic_status": epistemic_status,
        "unverified_count": unverified_count,
        "has_unknown": has_unknown,
        "read_degraded": bool(degraded_domains),
        "degraded_domains": degraded_domains,
        "domains": provenance_domains,
        "candidate_audit": provenance_audit,
    }

    data_block: dict[str, Any] = {
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "perspective": persp,
        "observed_at": stamp,
        "read_only": True,
        "zero_write": True,
        "provenance": provenance,
        "chatless_traces": chatless_traces,
        "md_conflicts": md_conflicts,
        "forget_cases": _strip_secret_keys(forget_cases),
        "rescaled_copractices": rescaled_copractices,
        "periods": periods_out,
        "scraps": scraps_out,
        "personal_tools": personal_tools_out,
        "skill_attributions": out_skill_attr,
        "budget_survival": out_budget_survival,
        "crash_reconciler_summary": out_crash_summary,
        "llm_calls": 0,
    }
    if not has_subject_capability:
        data_block = _strip_secret_keys(data_block)

    return 200, {
        "ok": True,
        "status": "ok",
        "command_status": "READ",
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "perspective": persp,
        "llm_calls": 0,
        "zero_write": True,
        "data": data_block,
    }


def _payload_fingerprint(actor_side: str, payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"actor_side": actor_side, "payload": dict(payload)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _update_common_ground_copractice(
    day_root: Path,
    *,
    practice_id: str,
    scale_mode: str,
    target_cadence: str,
    confirmed: bool,
    frequency_per_week: float = 1.0,
    participation_scale: float = 0.5,
    effective_actor: str,
    stamp: str,
) -> tuple[bool, Optional[tuple[int, dict[str, Any]]]]:
    """Sync confirm_rescaled_copractice into v02_common_ground ledgers when present."""
    matched_any = False
    for rel in (("memory", "v02_common_ground_state.json"), ("phone", "v02_common_ground.json")):
        path = day_root.joinpath(*rel)
        if path.is_symlink() or not path.is_file():
            continue
        with _tx_guard.state_file_lock(path):
            doc = _read_json_zero_write(path)
            if not isinstance(doc, Mapping):
                continue
            updated_doc = copy.deepcopy(dict(doc))
            file_changed = False
            for key in ("items", "shared_plans", "copractices", "practices", "plans"):
                coll = updated_doc.get(key)
                if isinstance(coll, dict):
                    rows_iter = list(coll.items())
                elif isinstance(coll, list):
                    rows_iter = [(None, row) for row in coll]
                else:
                    continue
                for map_k, row in rows_iter:
                    if not isinstance(row, dict):
                        continue
                    cid = str(
                        row.get("practice_id")
                        or row.get("item_id")
                        or row.get("plan_id")
                        or row.get("id")
                        or (map_k or "")
                    ).strip()
                    if cid != practice_id:
                        continue
                    g_status = str(row.get("ground_status") or "").strip().upper()
                    req_agree = bool(row.get("requires_agreement", False))
                    if effective_actor == "partner" and (
                        row.get("private") is True
                        or (g_status == "PRIVATE_UNSHARED" and not req_agree)
                    ):
                        return False, _error_response(
                            403,
                            "FORBIDDEN_COPRACTICE_SCOPE",
                            "Cannot confirm private unshared copractice from partner view",
                        )
                    matched_any = True
                    file_changed = True
                    row["scale_mode"] = scale_mode
                    row["cadence"] = target_cadence
                    row["frequency_per_week"] = round(float(frequency_per_week), 4)
                    row["participation_scale"] = round(float(participation_scale), 4)
                    row["partner_confirmed"] = bool(confirmed)
                    row["updated_at"] = stamp
                    if key == "items" and confirmed:
                        row["delivery_state"] = "AGREED"
                        row["ground_status"] = "SHARED_WITH_PARTNER"
                        row["partner_knows"] = True
                        row["agreement_expired"] = False
                        if "version" in row:
                            row["version"] = int(row.get("version") or 0) + 1
            if file_changed:
                updated_doc["updated_at"] = stamp
                _tx_guard.atomic_write_json(path, updated_doc, sort_keys=False)
    return matched_any, None


def _persist_pending_op(
    day_root: Path,
    state: dict[str, Any],
    *,
    op_key: str,
    fp: str,
    action: str,
    stamp: str,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    if not op_key:
        return
    pending = state.setdefault("pending_ops", {})
    if not isinstance(pending, dict):
        pending = {}
        state["pending_ops"] = pending
    if op_key in pending and isinstance(pending[op_key], Mapping) and pending[op_key].get("fingerprint") == fp:
        return
    entry: dict[str, Any] = {
        "fingerprint": fp,
        "action": action,
        "started_at": stamp,
    }
    if extra:
        entry.update(dict(extra))
    pending[op_key] = entry
    if len(pending) > 64:
        for old_k in list(pending.keys())[: len(pending) - 64]:
            pending.pop(old_k, None)
    _tx_guard.atomic_write_json(_state_path(day_root), state, sort_keys=True)


def _commit_honest_state(
    day_root: Path,
    state: dict[str, Any],
    *,
    op_key: str,
    fp: str,
    result_data: Mapping[str, Any],
    stamp: str,
) -> None:
    new_rev = int(state.get("revision") or 0) + 1
    state["revision"] = new_rev
    state["updated_at"] = stamp
    if op_key:
        pending = state.get("pending_ops")
        if isinstance(pending, dict):
            pending.pop(op_key, None)
        _tx_guard.stamp_receipt(
            state,
            op_key,
            {
                "fingerprint": fp,
                "result_data": copy.deepcopy(dict(result_data)),
                "applied_at": stamp,
                "status": "applied",
                "ok": True,
            },
            revision=new_rev,
            dict_key="operation_ledger",
            payload_hash=fp,
            committed_at=stamp,
        )
        _tx_guard.trim_receipts_by_op_seq(
            state,
            dict_key="operation_ledger",
            max_keep=512,
        )
    _tx_guard.atomic_write_json(_state_path(day_root), state, sort_keys=True)


def mutate_v02_honest_ux_view(
    day_root: Path | str,
    payload: Any,
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Execute one Honest UX partner or subject action under lock and idempotency."""
    root = Path(day_root)
    stamp = _now_iso(now_iso)

    if not isinstance(payload, Mapping) or not payload:
        return _error_response(400, "BODY_REQUIRED", "Payload must be a non-empty object")

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip():
        return _error_response(400, "MISSING_ACTION", "Action is required")
    action = raw_action.strip().lower()

    # Enforce partner boundary: partner door cannot escalate via payload.actor_side;
    # subject door may explicitly act as partner if payload.actor_side == "partner".
    if actor_side != "subject":
        effective_actor = "partner"
    else:
        req_side = payload.get("actor_side")
        if req_side is not None:
            if not isinstance(req_side, str) or req_side.strip().lower() not in ("partner", "subject"):
                return _error_response(400, "INVALID_FIELDS", "actor_side must be partner or subject")
            effective_actor = req_side.strip().lower()
        else:
            effective_actor = "subject"

    if action in SUBJECT_ONLY_ACTIONS and effective_actor != "subject":
        return _error_response(
            403,
            "FORBIDDEN_PARTNER_ACTION",
            f"Action {action!r} is restricted to the subject capability",
        )

    if action not in ALL_ALLOWED_ACTIONS:
        if effective_actor == "partner":
            return _error_response(403, "FORBIDDEN_PARTNER_ACTION", f"Unsupported partner action: {action!r}")
        return _error_response(400, "UNKNOWN_ACTION", f"Unknown honest-ux action: {action!r}")

    op_key_raw = payload.get("operation_key")
    op_key = op_key_raw.strip()[:128] if isinstance(op_key_raw, str) and op_key_raw.strip() else ""
    fp = _payload_fingerprint(effective_actor, payload)

    with _tx_guard.state_file_lock(_lock_path(root)):
        state = _load_honest_state_readonly(root)
        if op_key:
            rec_kind, rec_row = _tx_guard.reconcile_evicted_or_active_op(
                root,
                state,
                op_key,
                dict_key="operation_ledger",
                payload_hash=fp,
            )
            if rec_kind == "conflict":
                return _error_response(
                    409,
                    "OPERATION_KEY_CONFLICT",
                    f"operation_key {op_key!r} already used with a different payload",
                )
            if rec_kind in ("hit_full", "hit_evicted") and isinstance(rec_row, Mapping):
                saved_data = copy.deepcopy(dict(rec_row.get("result_data") or rec_row))
                return 200, {
                    "ok": True,
                    "status": "already_applied",
                    "command_status": "DUPLICATE",
                    "idempotent_replay": True,
                    "schema": VIEW_SCHEMA,
                    "endpoint": ENDPOINT_PATH,
                    "llm_calls": 0,
                    "data": saved_data,
                }
        pending_map = state.get("pending_ops") if isinstance(state.get("pending_ops"), Mapping) else {}
        pending_ctx: Optional[Mapping[str, Any]] = None
        if op_key and op_key in pending_map and isinstance(pending_map[op_key], Mapping):
            pending_ctx = pending_map[op_key]
            if pending_ctx.get("fingerprint") != fp:
                return _error_response(
                    409,
                    "OPERATION_KEY_CONFLICT",
                    f"operation_key {op_key!r} already used with a different payload",
                )

        eff_op_id = op_key or f"hux_{uuid.uuid4().hex[:14]}"

        if action == "mark_representation_divergence_seen":
            art_id, present, valid = _clean_safe_id(payload.get("artifact_id"), max_len=96)
            if not present:
                return _error_response(400, "MISSING_REQUIRED_FIELDS", "artifact_id is required")
            if not valid or not art_id:
                return _error_response(400, "INVALID_ARTIFACT_ID", "Invalid artifact_id")

            obs_hash = ""
            if payload.get("observed_head_hash") is not None:
                h_val, h_ok = _clean_required_str(payload.get("observed_head_hash"), min_len=1, max_len=128)
                if not h_ok or h_val is None:
                    return _error_response(400, "INVALID_FIELDS", "Invalid observed_head_hash")
                obs_hash = h_val

            rep_state = "DIVERGED"
            if payload.get("representation_state") is not None:
                st_raw = payload.get("representation_state")
                if not isinstance(st_raw, str):
                    return _error_response(400, "INVALID_FIELDS", "Invalid representation_state")
                rep_state = st_raw.strip().upper()
                if rep_state not in ALLOWED_REP_STATES:
                    return _error_response(400, "INVALID_REPRESENTATION_STATE", "Invalid representation_state")

            state.setdefault("divergence_seen", {})[art_id] = {
                "artifact_id": art_id,
                "observed_head_hash": obs_hash,
                "representation_state": rep_state,
                "seen": True,
                "seen_at": stamp,
                "actor_side": effective_actor,
            }
            res_data = {
                "applied_action": action,
                "artifact_id": art_id,
                "observed_head_hash": obs_hash,
                "representation_state": rep_state,
                "divergence_seen": True,
                "actor_side": effective_actor,
            }
            _commit_honest_state(root, state, op_key=op_key, fp=fp, result_data=res_data, stamp=stamp)
            return 200, {
                "ok": True,
                "status": "applied",
                "command_status": "APPLIED",
                "schema": VIEW_SCHEMA,
                "endpoint": ENDPOINT_PATH,
                "llm_calls": 0,
                "data": res_data,
            }

        if action == "request_forget_scope_hold":
            scope_hint, hint_present = _clean_required_str(payload.get("scope_hint"), min_len=1, max_len=64)
            if not hint_present or scope_hint is None:
                return _error_response(
                    400,
                    "INVALID_FIELDS" if hint_present else "MISSING_REQUIRED_FIELDS",
                    "scope_hint is required",
                )
            target_domain = "conversation"
            if payload.get("target_domain") is not None:
                td_raw = payload.get("target_domain")
                if not isinstance(td_raw, str):
                    return _error_response(400, "INVALID_FIELDS", "Invalid target_domain")
                target_domain = td_raw.strip().lower()
                if target_domain not in ALLOWED_TARGET_DOMAINS:
                    return _error_response(400, "INVALID_TARGET_DOMAIN", "Invalid target_domain")

            conv_id = ""
            if payload.get("conversation_id") is not None:
                cid_val, cid_pres, cid_valid = _clean_safe_id(payload.get("conversation_id"), max_len=96)
                if cid_pres and not cid_valid:
                    return _error_response(400, "INVALID_FIELDS", "Invalid conversation_id")
                conv_id = cid_val

            recent_ids: list[str] = []
            if isinstance(payload.get("recent_source_ids"), Sequence) and not isinstance(
                payload.get("recent_source_ids"), (str, bytes)
            ):
                recent_ids = [str(x).strip() for x in payload["recent_source_ids"] if str(x).strip()]

            shared_with_partner = effective_actor == "partner" or bool(payload.get("shared_with_partner"))
            _persist_pending_op(root, state, op_key=op_key, fp=fp, action=action, stamp=stamp)
            st_code, hold_res = _rlf.submit_forget_command(
                root,
                command_text="忘掉刚才那段",
                operation_id=eff_op_id,
                recent_source_ids=recent_ids,
                actor_side=effective_actor,
                shared_with_partner=shared_with_partner,
                conversation_id=conv_id or None,
                target_domain=target_domain,
                require_candidates=True,
                now_iso=stamp,
            )
            if st_code >= 400 or not hold_res.get("ok"):
                return _error_response(
                    st_code,
                    str(hold_res.get("code") or "FORGET_HOLD_REJECTED"),
                    "Unable to create forget scope hold",
                )
            hold_id = str(hold_res.get("hold_id") or "")
            state.setdefault("forget_case_meta", {})[hold_id] = {
                "case_id": hold_id,
                "target_domain": target_domain,
                "scope_hint": scope_hint,
                **({"conversation_id": conv_id} if conv_id else {}),
                "actor_side": effective_actor,
                "shared_with_partner": shared_with_partner,
                "created_at": stamp,
            }
            res_data = {
                "applied_action": action,
                "case_id": hold_id,
                "hold_id": hold_id,
                "status": str(hold_res.get("status") or _rlf.CASE_HOLD_PENDING),
                "target_domain": target_domain,
                "read_blocked": True,
                "outbound_blocked": True,
                "no_echo": True,
                "actor_side": effective_actor,
            }
            _commit_honest_state(root, state, op_key=op_key, fp=fp, result_data=res_data, stamp=stamp)
            return 200, {
                "ok": True,
                "status": "applied",
                "command_status": "APPLIED",
                "idempotent_replay": bool(hold_res.get("idempotent_replay")),
                "schema": VIEW_SCHEMA,
                "endpoint": ENDPOINT_PATH,
                "llm_calls": 0,
                "data": res_data,
            }

        if action == "clarify_forget_scope":
            case_id, cid_present = _clean_required_str(payload.get("case_id"), min_len=1, max_len=96)
            scope_raw = payload.get("scope_choice")
            if not cid_present or case_id is None or scope_raw is None:
                return _error_response(
                    400,
                    "INVALID_FIELDS" if (cid_present and not isinstance(payload.get("case_id"), str)) else "MISSING_REQUIRED_FIELDS",
                    "case_id and scope_choice are required",
                )
            if not isinstance(scope_raw, str):
                return _error_response(400, "INVALID_FIELDS", "scope_choice must be a string")
            scope_choice = scope_raw.strip().lower()
            if not scope_choice:
                return _error_response(400, "MISSING_REQUIRED_FIELDS", "scope_choice is required")
            if scope_choice not in ALLOWED_FORGET_SCOPE_CHOICES:
                return _error_response(400, "INVALID_SCOPE_CHOICE", "Invalid scope_choice")

            win_mins: Optional[int] = None
            if payload.get("window_minutes") is not None:
                wm = payload.get("window_minutes")
                if isinstance(wm, bool) or not isinstance(wm, int) or wm < 1 or wm > 1440:
                    return _error_response(400, "INVALID_WINDOW_MINUTES", "window_minutes must be 1..1440")
                win_mins = wm

            rlf_ro, _ = _rlf._load_state_readonly(root)
            holds_ro = rlf_ro.get("holds") if isinstance(rlf_ro.get("holds"), Mapping) else {}
            hold_doc = holds_ro.get(case_id)
            meta = (
                state.get("forget_case_meta", {}).get(case_id)
                if isinstance(state.get("forget_case_meta"), Mapping)
                else {}
            )
            if effective_actor == "partner" and isinstance(hold_doc, Mapping):
                if not _is_forget_case_partner_visible(
                    hold_doc,
                    meta if isinstance(meta, Mapping) else {},
                ):
                    return _error_response(
                        403,
                        "FORBIDDEN_FORGET_CASE_SCOPE",
                        f"Hold case {case_id!r} is not shared with the partner perspective",
                    )

            resolution = "explicit_cancel_hold" if scope_choice == "cancel_hold" else "retract_only"
            _persist_pending_op(root, state, op_key=op_key, fp=fp, action=action, stamp=stamp)
            st_code, res_out = _rlf.resolve_hold_case(
                root,
                hold_id=case_id,
                resolution=resolution,
                operation_id=eff_op_id,
                scope_choice=scope_choice,
                window_minutes=win_mins,
                actor_side=effective_actor,
                now_iso=stamp,
            )
            if st_code >= 400 or not res_out.get("ok"):
                return _error_response(
                    st_code,
                    str(res_out.get("code") or "HOLD_NOT_FOUND"),
                    "Forget hold clarification rejected",
                )
            res_data = {
                "applied_action": action,
                "case_id": case_id,
                "scope_choice": scope_choice,
                "status": str(res_out.get("status") or _rlf.CASE_RETRACTED),
                "selected_source_ids": list(res_out.get("selected_source_ids") or ()),
                "released_source_ids": list(res_out.get("released_source_ids") or ()),
                "no_echo": True,
                "actor_side": effective_actor,
            }
            _commit_honest_state(root, state, op_key=op_key, fp=fp, result_data=res_data, stamp=stamp)
            return 200, {
                "ok": True,
                "status": "applied",
                "command_status": "APPLIED",
                "idempotent_replay": bool(res_out.get("idempotent_replay")),
                "schema": VIEW_SCHEMA,
                "endpoint": ENDPOINT_PATH,
                "llm_calls": 0,
                "data": res_data,
            }

        if action == "confirm_rescaled_copractice":
            raw_pid = payload.get("practice_id") if payload.get("practice_id") is not None else payload.get("copractice_id")
            practice_id, pid_present = _clean_required_str(raw_pid, min_len=1, max_len=96)
            if not pid_present or practice_id is None:
                return _error_response(
                    400,
                    "INVALID_FIELDS" if pid_present else "MISSING_REQUIRED_FIELDS",
                    "practice_id is required",
                )
            scale_mode = "light_relay"
            if payload.get("scale_mode") is not None:
                if not isinstance(payload.get("scale_mode"), str):
                    return _error_response(400, "INVALID_FIELDS", "scale_mode must be a string")
                raw_sm = payload["scale_mode"].strip().lower()
                if raw_sm not in ALLOWED_SCALE_MODES:
                    return _error_response(400, "INVALID_SCALE_MODE", "Invalid scale_mode")
                scale_mode = _normalize_scale_mode(raw_sm)
            confirmed = True
            if payload.get("confirmed") is not None:
                if not isinstance(payload.get("confirmed"), bool):
                    return _error_response(400, "INVALID_FIELDS", "confirmed must be boolean")
                confirmed = bool(payload["confirmed"])
                if not confirmed:
                    return _error_response(
                        400,
                        "INVALID_FIELDS",
                        "confirm_rescaled_copractice requires confirmed=true",
                    )

            target_cadence = _SCALE_MODE_TO_CADENCE[scale_mode]
            is_pause_mode = scale_mode in ("pause_without_debt", "paused_no_debt")
            freq_per_week = 0.0 if is_pause_mode else 1.0
            part_scale = 0.0 if is_pause_mode else 0.5
            _persist_pending_op(root, state, op_key=op_key, fp=fp, action=action, stamp=stamp)
            slice_missing_exc: Optional[_lived_slices.V02LivedSlicesError] = None
            try:
                _proj, _cmd = _lived_slices.mutate_v02_lived_slices(
                    root,
                    {
                        "action": "rescale_copractice_routine",
                        "routine_id": practice_id,
                        "target_cadence": target_cadence,
                        "new_frequency_per_week": freq_per_week,
                        "participation_scale": part_scale,
                        "partner_confirmed": confirmed,
                        "operation_key": eff_op_id,
                    },
                    actor_side=effective_actor,
                    now_iso=stamp,
                )
            except _lived_slices.V02LivedSlicesError as exc:
                if exc.status_code == 404 or exc.reason_code in ("ROUTINE_NOT_FOUND", "SLICE_NOT_FOUND", "NOT_FOUND"):
                    slice_missing_exc = exc
                else:
                    return _error_response(exc.status_code, exc.reason_code, exc.message)

            cg_updated, cg_err = _update_common_ground_copractice(
                root,
                practice_id=practice_id,
                scale_mode=scale_mode,
                target_cadence=target_cadence,
                confirmed=confirmed,
                frequency_per_week=freq_per_week,
                participation_scale=part_scale,
                effective_actor=effective_actor,
                stamp=stamp,
            )
            if cg_err is not None:
                return cg_err
            proposed_map = state.get("proposed_rescaled_copractices")
            prop_updated = False
            if isinstance(proposed_map, dict) and isinstance(proposed_map.get(practice_id), dict):
                prop_row = proposed_map[practice_id]
                prop_row["scale_mode"] = scale_mode
                prop_row["partner_confirmed"] = bool(confirmed)
                if effective_actor == "subject":
                    prop_row["subject_confirmed"] = True
                    prop_row["status"] = "CONFIRMED"
                prop_row["updated_at"] = stamp
                prop_updated = True
            if slice_missing_exc is not None and not cg_updated and not prop_updated:
                return _error_response(
                    slice_missing_exc.status_code,
                    slice_missing_exc.reason_code,
                    slice_missing_exc.message,
                )

            res_data = {
                "applied_action": action,
                "practice_id": practice_id,
                "scale_mode": scale_mode,
                "frequency_per_week": freq_per_week,
                "participation_scale": part_scale,
                "partner_confirmed": confirmed,
                "common_ground_synced": cg_updated,
                "actor_side": effective_actor,
            }
            _commit_honest_state(root, state, op_key=op_key, fp=fp, result_data=res_data, stamp=stamp)
            return 200, {
                "ok": True,
                "status": "applied",
                "command_status": "APPLIED",
                "schema": VIEW_SCHEMA,
                "endpoint": ENDPOINT_PATH,
                "llm_calls": 0,
                "data": res_data,
            }

        if action == "propose_rescaled_copractice":
            raw_pid = (
                payload.get("practice_id")
                if payload.get("practice_id") is not None
                else payload.get("copractice_id")
            )
            pid_val, pid_present, pid_valid = _clean_safe_id(raw_pid, max_len=96)
            if pid_present and not pid_valid:
                return _error_response(400, "INVALID_FIELDS", "Invalid practice_id")
            practice_id = pid_val or (
                f"cop_prop_{hashlib.sha256((op_key or uuid.uuid4().hex).encode('utf-8')).hexdigest()[:10]}"
            )

            scale_mode = "light_relay"
            if payload.get("scale_mode") is not None:
                if not isinstance(payload.get("scale_mode"), str):
                    return _error_response(400, "INVALID_FIELDS", "scale_mode must be a string")
                raw_sm = payload["scale_mode"].strip().lower()
                if raw_sm not in ALLOWED_SCALE_MODES:
                    return _error_response(400, "INVALID_SCALE_MODE", "Invalid scale_mode")
                scale_mode = _normalize_scale_mode(raw_sm)

            proposed_map = state.setdefault("proposed_rescaled_copractices", {})
            existing_prop = (
                proposed_map.get(practice_id)
                if isinstance(proposed_map.get(practice_id), Mapping)
                else None
            )

            # Prevent partner from probing or mutating private unshared routines in lived_slices
            lived_existing_title = ""
            if pid_present:
                try:
                    lived_ro = _lived_slices.project_v02_lived_slices(
                        root, has_subject_capability=True, now_iso=stamp
                    )
                    for row in (lived_ro.get("rescaled_copractice") or {}).get("routines") or ():
                        if isinstance(row, Mapping) and str(row.get("routine_id") or "") == practice_id:
                            if effective_actor == "partner" and not _item_is_shared(row):
                                return _error_response(
                                    403,
                                    "FORBIDDEN_COPRACTICE_SCOPE",
                                    "Cannot propose rescale for a private unshared routine",
                                )
                            lived_existing_title = str(row.get("title") or row.get("title_cn") or "").strip()
                            break
                except Exception:
                    pass

            raw_title = payload.get("title") if payload.get("title") is not None else payload.get("title_cn")
            title_val, title_present = _clean_required_str(raw_title, min_len=1, max_len=160)
            if title_present and title_val is None:
                return _error_response(400, "INVALID_FIELDS", "title must be a non-empty string")
            if title_val is None:
                fallback_title = str(
                    (existing_prop or {}).get("title_cn")
                    or (existing_prop or {}).get("title")
                    or lived_existing_title
                    or ""
                ).strip()
                if not fallback_title:
                    return _error_response(400, "MISSING_REQUIRED_FIELDS", "title is required")
                title_val = fallback_title[:160]

            note_cn = ""
            if payload.get("note_cn") is not None:
                if not isinstance(payload.get("note_cn"), str):
                    return _error_response(400, "INVALID_FIELDS", "note_cn must be a string")
                note_cn = payload["note_cn"].strip()[:240]

            proposal_entry: dict[str, Any] = {
                "practice_id": practice_id,
                "title_cn": title_val,
                "title": title_val,
                "scale_mode": scale_mode,
                "status": "PROPOSED_BY_PARTNER",
                "proposed_by": "partner",
                "partner_confirmed": True,
                "subject_confirmed": False,
                "shared": True,
                "no_debt": True,
                "note_cn": note_cn,
                "updated_at": stamp,
            }
            proposed_map[practice_id] = proposal_entry
            res_data = {
                "applied_action": action,
                **copy.deepcopy(proposal_entry),
                "actor_side": effective_actor,
            }
            _commit_honest_state(root, state, op_key=op_key, fp=fp, result_data=res_data, stamp=stamp)
            return 200, {
                "ok": True,
                "status": "applied",
                "command_status": "APPLIED",
                "schema": VIEW_SCHEMA,
                "endpoint": ENDPOINT_PATH,
                "llm_calls": 0,
                "data": res_data,
            }

        # ------------------------------------------------------------------
        # Subject-only actions (effective_actor == "subject" verified above)
        # ------------------------------------------------------------------
        if action == "explicit_import_representation":
            art_id, present, valid = _clean_safe_id(payload.get("artifact_id"), max_len=96)
            if not present:
                return _error_response(400, "MISSING_REQUIRED_FIELDS", "artifact_id is required")
            if not valid or not art_id:
                return _error_response(400, "INVALID_ARTIFACT_ID", "Invalid artifact_id")

            rlf_ro, _ = _rlf._load_state_readonly(root)
            binding = (rlf_ro.get("representations") or {}).get(art_id)
            if not isinstance(binding, Mapping):
                return _error_response(404, "REPRESENTATION_NOT_BOUND", "Representation not bound")

            bound_ws_raw = str(binding.get("workspace_root") or "").strip()
            if payload.get("workspace_root") is not None:
                req_ws_raw = str(payload.get("workspace_root") or "").strip()
                if not req_ws_raw:
                    return _error_response(400, "INVALID_FIELDS", "workspace_root cannot be empty")
                if bound_ws_raw:
                    try:
                        if Path(req_ws_raw).resolve() != Path(bound_ws_raw).resolve():
                            return _error_response(
                                403,
                                "WORKSPACE_ROOT_MISMATCH",
                                "workspace_root does not match the bound workspace",
                            )
                    except OSError:
                        return _error_response(403, "WORKSPACE_ROOT_MISMATCH", "Invalid workspace_root")

            ws_root = bound_ws_raw or str(
                (pending_ctx.get("resolved_workspace_root") if isinstance(pending_ctx, Mapping) else None)
                or ""
            )
            base_ver = str(
                payload.get("base_projection_version")
                or (pending_ctx.get("resolved_base_version") if isinstance(pending_ctx, Mapping) else None)
                or binding.get("generated_version")
                or ""
            )
            exp_obs_sha = str(
                payload.get("expected_observed_sha256")
                or payload.get("observed_head_hash")
                or (pending_ctx.get("resolved_expected_observed_sha256") if isinstance(pending_ctx, Mapping) else None)
                or ""
            ).strip()
            obs_ver = str(payload.get("observed_version") or "").strip()
            obs_list = rlf_ro.get("external_edit_observations") or []
            if obs_ver and not exp_obs_sha:
                matched_obs_sha = next(
                    (
                        str(obs.get("observed_sha256") or "").strip()
                        for obs in reversed(obs_list)
                        if isinstance(obs, Mapping)
                        and obs.get("artifact_id") == art_id
                        and str(obs.get("observation_id") or "").strip() == obs_ver
                    ),
                    "",
                )
                if matched_obs_sha:
                    exp_obs_sha = matched_obs_sha
                elif len(obs_ver) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in obs_ver):
                    exp_obs_sha = obs_ver.lower()
            if not exp_obs_sha:
                exp_obs_sha = next(
                    (
                        str(obs.get("observed_sha256") or "").strip()
                        for obs in reversed(obs_list)
                        if isinstance(obs, Mapping) and obs.get("artifact_id") == art_id
                    ),
                    "",
                ) or str(binding.get("last_observed_sha256") or "").strip()
            if not exp_obs_sha:
                return _error_response(
                    409,
                    "OBSERVATION_DIGEST_REQUIRED",
                    "Must observe and review external Markdown changes before importing",
                )

            actor_ref = str(payload.get("actor") or payload.get("actor_ref") or "subject")
            _persist_pending_op(
                root,
                state,
                op_key=op_key,
                fp=fp,
                action=action,
                stamp=stamp,
                extra={
                    "resolved_workspace_root": ws_root,
                    "resolved_base_version": base_ver,
                    "resolved_expected_observed_sha256": exp_obs_sha,
                },
            )

            st_code, imp_res = _rlf.explicit_import_representation(
                root,
                artifact_id=art_id,
                workspace_root=ws_root,
                base_projection_version=base_ver,
                actor_ref=actor_ref,
                operation_id=eff_op_id,
                trusted_actor_identity=True,
                explicit_import_permission=True,
                require_prior_observation=False,
                expected_observed_sha256=exp_obs_sha,
                now_iso=stamp,
            )
            if st_code >= 400 or not imp_res.get("ok"):
                return _error_response(
                    st_code,
                    str(imp_res.get("code") or "EXPLICIT_IMPORT_REJECTED"),
                    str(imp_res.get("reason") or "Explicit import rejected"),
                )
            res_data = {
                "applied_action": action,
                "artifact_id": art_id,
                "version_id": imp_res.get("version_id"),
                "current_head": imp_res.get("current_head"),
                "representation_health": imp_res.get("representation_health"),
                "actor_side": effective_actor,
            }
            _commit_honest_state(root, state, op_key=op_key, fp=fp, result_data=res_data, stamp=stamp)
            return 200, {
                "ok": True,
                "status": "applied",
                "command_status": "APPLIED",
                "idempotent_replay": bool(imp_res.get("idempotent_replay")),
                "schema": VIEW_SCHEMA,
                "endpoint": ENDPOINT_PATH,
                "llm_calls": 0,
                "data": res_data,
            }

        if action == "approve_pending_forget_hold":
            raw_cid = payload.get("case_id") if payload.get("case_id") is not None else payload.get("hold_id")
            case_id, cid_present = _clean_required_str(raw_cid, min_len=1, max_len=96)
            if not cid_present or case_id is None:
                return _error_response(400, "MISSING_REQUIRED_FIELDS", "case_id is required")
            resolution = str(payload.get("resolution") or "confirm_delete").strip()
            unreachable = (
                [str(x) for x in payload.get("unreachable_copies") or () if str(x).strip()]
                if isinstance(payload.get("unreachable_copies"), Sequence)
                and not isinstance(payload.get("unreachable_copies"), (str, bytes))
                else ()
            )
            st_code, res_out = _rlf.resolve_hold_case(
                root,
                hold_id=case_id,
                resolution=resolution,
                operation_id=eff_op_id,
                unreachable_copies=unreachable,
                now_iso=stamp,
            )
            if st_code >= 400 or not res_out.get("ok"):
                return _error_response(
                    st_code,
                    str(res_out.get("code") or "HOLD_APPROVAL_FAILED"),
                    "Unable to approve forget hold",
                )
            res_data = {
                "applied_action": action,
                "case_id": case_id,
                "status": str(res_out.get("status") or _rlf.CASE_COMPLETE),
                "no_echo": True,
                "actor_side": effective_actor,
            }
            _commit_honest_state(root, state, op_key=op_key, fp=fp, result_data=res_data, stamp=stamp)
            return 200, {
                "ok": True,
                "status": "applied",
                "command_status": "APPLIED",
                "schema": VIEW_SCHEMA,
                "endpoint": ENDPOINT_PATH,
                "llm_calls": 0,
                "data": res_data,
            }

        if action == "record_period_snapshot":
            period_id_raw = payload.get("period_id")
            period_id = period_id_raw.strip()[:32] if isinstance(period_id_raw, str) and period_id_raw.strip() else None
            visibility = str(payload.get("visibility") or ("shared" if payload.get("shared") is True else "private")).strip().lower()
            note = str(payload.get("note") or "")[:400]
            try:
                compiled: Optional[dict[str, Any]] = None
                if period_id:
                    compiled = _asp.compile_period_summary(
                        root,
                        {
                            "period_id": period_id,
                            "visibility": visibility,
                            "operation_key": f"{eff_op_id}:compile",
                        },
                        now_iso=stamp,
                    )
                snap_res = _asp.append_narrative_snapshot(
                    root,
                    {
                        "visibility": visibility,
                        "note": note,
                        **({"period_ids": [period_id]} if period_id else {}),
                        "operation_key": f"{eff_op_id}:snap",
                    },
                    now_iso=stamp,
                )
            except _asp.AutobiographySkillProvenanceError as exc:
                return _error_response(exc.status_code, exc.reason_code, str(exc))

            res_data = {
                "applied_action": action,
                "period_id": period_id,
                "visibility": visibility,
                "snapshot_id": (snap_res.get("snapshot") or {}).get("snapshot_id"),
                "summary_revision": (
                    ((compiled or {}).get("period_summary") or {}).get("summary_revision")
                    if compiled
                    else None
                ),
                "actor_side": effective_actor,
            }
            _commit_honest_state(root, state, op_key=op_key, fp=fp, result_data=res_data, stamp=stamp)
            return 200, {
                "ok": True,
                "status": "applied",
                "command_status": "APPLIED",
                "schema": VIEW_SCHEMA,
                "endpoint": ENDPOINT_PATH,
                "llm_calls": 0,
                "data": res_data,
            }

        if action == "salvage_work_scrap":
            scrap_payload = {
                k: v
                for k, v in payload.items()
                if k not in ("action", "actor_side", "actor", "source", "owner")
            }
            scrap_payload["action"] = "salvage_scrap_fragment"
            scrap_payload["operation_key"] = eff_op_id
            try:
                proj, _cmd = _lived_slices.mutate_v02_lived_slices(
                    root,
                    scrap_payload,
                    actor_side="subject",
                    now_iso=stamp,
                )
            except _lived_slices.V02LivedSlicesError as exc:
                return _error_response(exc.status_code, exc.reason_code, exc.message)

            res_data = {
                "applied_action": action,
                "scrap_id": payload.get("scrap_id"),
                "revision": proj.get("revision"),
                "actor_side": effective_actor,
            }
            _commit_honest_state(root, state, op_key=op_key, fp=fp, result_data=res_data, stamp=stamp)
            return 200, {
                "ok": True,
                "status": "applied",
                "command_status": "APPLIED",
                "schema": VIEW_SCHEMA,
                "endpoint": ENDPOINT_PATH,
                "llm_calls": 0,
                "data": res_data,
            }

        if action == "simplify_personal_tool":
            tool_payload = {
                k: v
                for k, v in payload.items()
                if k not in ("action", "actor_side", "actor", "source", "owner")
            }
            tool_payload["action"] = "simplify_personal_script"
            tool_payload["operation_key"] = eff_op_id
            try:
                proj, _cmd = _lived_slices.mutate_v02_lived_slices(
                    root,
                    tool_payload,
                    actor_side="subject",
                    now_iso=stamp,
                )
            except _lived_slices.V02LivedSlicesError as exc:
                return _error_response(exc.status_code, exc.reason_code, exc.message)

            res_data = {
                "applied_action": action,
                "script_id": payload.get("script_id"),
                "revision": proj.get("revision"),
                "actor_side": effective_actor,
            }
            _commit_honest_state(root, state, op_key=op_key, fp=fp, result_data=res_data, stamp=stamp)
            return 200, {
                "ok": True,
                "status": "applied",
                "command_status": "APPLIED",
                "schema": VIEW_SCHEMA,
                "endpoint": ENDPOINT_PATH,
                "llm_calls": 0,
                "data": res_data,
            }

    return _error_response(400, "UNKNOWN_ACTION", f"Unsupported action: {action!r}")


__all__ = [
    "ALL_ALLOWED_ACTIONS",
    "ALLOWED_FORGET_SCOPE_CHOICES",
    "ALLOWED_REP_STATES",
    "ALLOWED_SCALE_MODES",
    "ALLOWED_TARGET_DOMAINS",
    "ENDPOINT_PATH",
    "PARTNER_ALLOWED_ACTIONS",
    "SUBJECT_ONLY_ACTIONS",
    "VIEW_SCHEMA",
    "mutate_v02_honest_ux_view",
    "read_v02_honest_ux_view",
]
